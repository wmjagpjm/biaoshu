/**
 * 模块：P12B-D2 双工作区显式检查点入口 E2E
 * 用途：技术标/商务标逐模式证明 create 强制 PUT→POST{}、restore 串链与唯一 GET、
 *       不确定响应阻断、二次确认、A→B/折叠隔离、无详情/无 ID 泄漏。
 * 对接：Playwright chromium headless workers=1 retries=0；route 探针。
 * 二次开发：禁止 or True、固定 sleep 假同步、宽泛状态码、route fallback 伪成功。
 */
import {
  expect,
  test,
  type Page,
  type Route,
} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const TECH_A = "proj_e2e_p12bd2_tech_a";
const TECH_B = "proj_e2e_p12bd2_tech_b";
const BIZ_A = "proj_e2e_p12bd2_biz_a";
const BIZ_B = "proj_e2e_p12bd2_biz_b";

const TECH_OVERVIEW = "P12B_D2_TECH_SERVER_OVERVIEW";
const TECH_OVERVIEW_B = "P12B_D2_TECH_SERVER_OVERVIEW_B";
const BIZ_MD = "P12B_D2_BIZ_SERVER_MARKDOWN";
const BIZ_MD_B = "P12B_D2_BIZ_SERVER_MARKDOWN_B";
const RESTORED_TECH = "P12B_D2_TECH_RESTORED_OVERVIEW";
const RESTORED_BIZ = "P12B_D2_BIZ_RESTORED_MARKDOWN";

/** 技术标 restore 后服务端水合 sentinel（多字段） */
const TECH_RESTORE_OUTLINE_TITLE = "P12B_D2_TECH_RESTORE_OUTLINE";
const TECH_RESTORE_CHAPTER_BODY = "P12B_D2_TECH_RESTORE_CHAPTER_BODY";
const TECH_RESTORE_FACT = "P12B_D2_TECH_RESTORE_FACT";
const TECH_RESTORE_GUIDANCE = "P12B_D2_TECH_RESTORE_GUIDANCE_FOCUS";
const TECH_RESTORE_MATRIX_TEXT = "P12B_D2_TECH_RESTORE_MATRIX_REQ";
const TECH_RESTORE_MATRIX_VERSION = "rmv_p12bd2_restored_matrix_v1";
const TECH_RESTORE_MODE = "FREE";

/** 商务标 restore 后服务端水合 sentinel（多字段） */
const BIZ_RESTORE_QUALIFY = "P12B_D2_BIZ_RESTORE_QUALIFY";
const BIZ_RESTORE_TOC = "P12B_D2_BIZ_RESTORE_TOC";
const BIZ_RESTORE_QUOTE = "P12B_D2_BIZ_RESTORE_QUOTE_ROW";
const BIZ_RESTORE_QUOTE_NOTES = "P12B_D2_BIZ_RESTORE_QUOTE_NOTES";
const BIZ_RESTORE_COMMIT = "P12B_D2_BIZ_RESTORE_COMMIT";

const MSG_CREATE_OK = "已保存服务器当前版本为检查点";
const MSG_CREATE_FAIL = "保存检查点失败，请确认后重试";
const MSG_CREATE_BLOCKED = "当前无法保存检查点，请先处理版本冲突或重新载入";
const MSG_RESTORE_OK = "已恢复到所选检查点";
const MSG_LIST_FAIL = "检查点列表加载失败，请稍后重试";
const MSG_RESTORE_BLOCKED =
  "当前无法恢复，请先处理版本冲突或重新载入";

/** 与 useTechnicalPlanEditors / useBusinessBidWorkspace 固定文案一致 */
const FULL_STATE_CONFLICT_MSG =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";
const RESTORE_CONFIRM =
  "当前服务器内容会先自动保存为安全检查点，恢复会替换全部技术标和商务标编辑态";
const RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";

const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const CHECKPOINT_ID_RE = /^escp_[0-9a-f]{32}$/;

const TECH_DEBOUNCE_MS = 800;
const BIZ_DEBOUNCE_MS = 600;

type Mode = "tech" | "biz";

type CheckpointMeta = {
  checkpointId: string;
  stateVersion: string;
  snapshotBytes: number;
  outlineNodeCount: number;
  chapterCount: number;
  createdAt: string;
  /** P12G：可选展示名称；探针与 mock 统一七键 */
  displayName: string | null;
};

/** P12G 命名 mock 模式 */
type NameMode =
  | { kind: "ok" }
  | { kind: "http_error"; status: number }
  | {
      kind: "hold";
      gate: HoldGate;
      then?: "ok" | "http_error";
      status?: number;
    };

type EditorState = {
  projectId: string;
  parsedMarkdown: string;
  businessQualify: unknown[];
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  outline: Array<Record<string, unknown>>;
  chapters: Array<Record<string, unknown>>;
  mode: string;
  analysisOverview: string;
  analysis: { overview: string };
  facts: unknown[];
  guidance: Record<string, unknown>;
  responseMatrix: unknown[];
  responseMatrixVersion: string | null;
  stateVersion: string;
};

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  readonly released: boolean;
  /** 已有请求进入 wait 的次数（可 poll） */
  readonly enteredCount: number;
  /** 等到至少 min 个请求真正进入 gate.wait */
  waitUntilEntered: (min?: number) => Promise<void>;
};

type RestoreMode =
  | { kind: "ok" }
  | { kind: "abort" }
  | { kind: "missing_version" }
  | { kind: "invalid_version" }
  | { kind: "blank_version" }
  | { kind: "conflict" }
  | {
      kind: "gate";
      gate: HoldGate;
      then: Exclude<RestoreMode["kind"], "gate">;
    };

type PutMode =
  | { kind: "ok" }
  | { kind: "abort" }
  | { kind: "http_error" }
  | {
      kind: "hold";
      gate: HoldGate;
      then?: "ok" | "conflict";
    };

type ListMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate };

type CreateMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate }
  | { kind: "mismatch_version" }
  /** 非 2xx + 服务端 detail.code（可冒充专用错误码，用于反假绿） */
  | { kind: "http_error"; status: number; code: string };

type ProbeState = {
  mode: Mode;
  projects: Record<string, EditorState>;
  checkpoints: Record<string, CheckpointMeta[]>;
  versionSeq: number;
  checkpointSeq: number;
  putLog: Array<{
    projectId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  createLog: Array<{
    projectId: string;
    body: string;
    responseVersion: string | null;
  }>;
  restoreLog: Array<{
    projectId: string;
    checkpointId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  listLog: string[];
  detailLog: string[];
  editorGetLog: Array<{ projectId: string; path: string }>;
  putMode: PutMode;
  /** 全局默认；可被 *ModeByProject 覆盖，便于 A/B 并行挂起 */
  restoreMode: RestoreMode;
  listMode: ListMode;
  createMode: CreateMode;
  createModeByProject: Record<string, CreateMode>;
  restoreModeByProject: Record<string, RestoreMode>;
  listModeByProject: Record<string, ListMode>;
  /** shape 反假绿：覆盖 list/create/restore 成功响应体 */
  listResponseOverride: unknown | null;
  createResponseOverride: unknown | null;
  restoreResponseOverride: unknown | null;
  nextEditorGetFail: boolean;
  createArrivedWhilePutHeld: boolean;
  restoreArrivedWhilePutHeld: boolean;
  externalHits: string[];
  forbiddenHits: string[];
  /** P12G 命名 */
  nameMode: NameMode;
  nameModeByProject: Record<string, NameMode>;
  nameModeByCheckpoint: Record<string, NameMode>;
  nameLog: Array<{
    projectId: string;
    checkpointId: string;
    method: string;
    path: string;
    postData: string | null;
    queryKeys: string[];
    search: string;
    bodyKeys: string[];
    displayName: string | null | undefined;
  }>;
  nameCompleteLog: Array<{
    projectId: string;
    checkpointId: string;
    status: number;
    displayName: string | null;
  }>;
  nameResponseOverride: unknown | null;
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedCheckpointId(n: number): string {
  return `escp_${n.toString(16).padStart(32, "0")}`;
}

function allocateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

function allocateCheckpointId(state: ProbeState): string {
  state.checkpointSeq += 1;
  return seedCheckpointId(state.checkpointSeq);
}

function createHoldGate(): HoldGate {
  let released = false;
  let enteredCount = 0;
  const waiters: Array<() => void> = [];
  const enteredWaiters: Array<() => void> = [];
  const notifyEntered = () => {
    while (enteredWaiters.length > 0) {
      enteredWaiters.shift()?.();
    }
  };
  return {
    wait: () => {
      enteredCount += 1;
      notifyEntered();
      return released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          });
    },
    release: () => {
      released = true;
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    get released() {
      return released;
    },
    get enteredCount() {
      return enteredCount;
    },
    waitUntilEntered: (min = 1) =>
      new Promise<void>((resolve) => {
        // 先注册再检查，避免 count 在中间态时漏掉 notify
        const tryResolve = () => {
          if (enteredCount >= min) resolve();
        };
        enteredWaiters.push(tryResolve);
        tryResolve();
      }),
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function baseEditor(
  projectId: string,
  mode: Mode,
  version: string,
  content: string,
): EditorState {
  return {
    projectId,
    parsedMarkdown: mode === "biz" ? content : "",
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    outline:
      mode === "tech"
        ? [{ id: "n1", title: "一级目录", children: [], targetWords: 100 }]
        : [],
    chapters:
      mode === "tech"
        ? [{ id: "n1", title: "一级目录", body: "章节正文" }]
        : [],
    mode: "ALIGNED",
    analysisOverview: mode === "tech" ? content : "",
    analysis: { overview: mode === "tech" ? content : "" },
    facts: [],
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    stateVersion: version,
  };
}

function createProbeState(mode: Mode): ProbeState {
  const versionSeq = 10;
  const aId = mode === "tech" ? TECH_A : BIZ_A;
  const bId = mode === "tech" ? TECH_B : BIZ_B;
  const aContent = mode === "tech" ? TECH_OVERVIEW : BIZ_MD;
  const bContent = mode === "tech" ? TECH_OVERVIEW_B : BIZ_MD_B;
  return {
    mode,
    projects: {
      [aId]: baseEditor(aId, mode, seedStateVersion(versionSeq), aContent),
      [bId]: baseEditor(bId, mode, seedStateVersion(versionSeq + 1), bContent),
    },
    checkpoints: { [aId]: [], [bId]: [] },
    versionSeq: versionSeq + 1,
    checkpointSeq: 0,
    putLog: [],
    createLog: [],
    restoreLog: [],
    listLog: [],
    detailLog: [],
    editorGetLog: [],
    putMode: { kind: "ok" },
    restoreMode: { kind: "ok" },
    listMode: { kind: "ok" },
    createMode: { kind: "ok" },
    createModeByProject: {},
    restoreModeByProject: {},
    listModeByProject: {},
    listResponseOverride: null,
    createResponseOverride: null,
    restoreResponseOverride: null,
    nextEditorGetFail: false,
    createArrivedWhilePutHeld: false,
    restoreArrivedWhilePutHeld: false,
    externalHits: [],
    forbiddenHits: [],
    nameMode: { kind: "ok" },
    nameModeByProject: {},
    nameModeByCheckpoint: {},
    nameLog: [],
    nameCompleteLog: [],
    nameResponseOverride: null,
  };
}

function resolveNameMode(
  state: ProbeState,
  projectId: string,
  checkpointId: string,
): NameMode {
  return (
    state.nameModeByCheckpoint[checkpointId] ??
    state.nameModeByProject[projectId] ??
    state.nameMode
  );
}

function resolveCreateMode(state: ProbeState, projectId: string): CreateMode {
  return state.createModeByProject[projectId] ?? state.createMode;
}

function resolveRestoreMode(state: ProbeState, projectId: string): RestoreMode {
  return state.restoreModeByProject[projectId] ?? state.restoreMode;
}

function resolveListMode(state: ProbeState, projectId: string): ListMode {
  return state.listModeByProject[projectId] ?? state.listMode;
}

function techRestoredEditor(prev: EditorState, restoredVersion: string): EditorState {
  // analysis.techRequirements 必须含矩阵 sourceText：fromApi 的 mergeResponseMatrix
  // 只按 analysis 源生成行，远程矩阵按 sourceKey 继承，否则会变成 []
  // 与 makeResponseMatrixSourceKey 一致：trim + 折叠空白 + toLocaleLowerCase
  const matrixSourceKey = `requirement:${TECH_RESTORE_MATRIX_TEXT.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
  return {
    ...prev,
    analysisOverview: RESTORED_TECH,
    analysis: {
      overview: RESTORED_TECH,
      techRequirements: [TECH_RESTORE_MATRIX_TEXT],
      rejectionRisks: [],
      scoringPoints: [],
    },
    outline: [
      {
        id: "rest_n1",
        title: TECH_RESTORE_OUTLINE_TITLE,
        level: 1,
        children: [],
        targetWords: 200,
      },
    ],
    chapters: [
      {
        id: "rest_n1",
        title: TECH_RESTORE_OUTLINE_TITLE,
        body: TECH_RESTORE_CHAPTER_BODY,
        wordCount: 12,
        status: "done",
        preview: TECH_RESTORE_CHAPTER_BODY.slice(0, 40),
      },
    ],
    facts: [
      {
        id: "fact_rest_1",
        category: "tender",
        content: TECH_RESTORE_FACT,
        source: "tender",
      },
    ],
    guidance: {
      ...prev.guidance,
      chapterFocus: TECH_RESTORE_GUIDANCE,
      targetWordCount: 90000,
    },
    mode: TECH_RESTORE_MODE,
    responseMatrix: [
      {
        id: "rm_rest_1",
        kind: "requirement",
        sourceKey: matrixSourceKey,
        sourceIndex: 0,
        sourceText: TECH_RESTORE_MATRIX_TEXT,
        weight: "10",
        chapterIds: ["rest_n1"],
        outlineNodeIds: ["rest_n1"],
        status: "covered",
        notes: "restore-sentinel",
      },
    ],
    responseMatrixVersion: TECH_RESTORE_MATRIX_VERSION,
    stateVersion: restoredVersion,
  };
}

function bizRestoredEditor(prev: EditorState, restoredVersion: string): EditorState {
  return {
    ...prev,
    parsedMarkdown: RESTORED_BIZ,
    businessQualify: [
      {
        id: "q_rest_1",
        requirement: BIZ_RESTORE_QUALIFY,
        response: "restore-response",
        evidence: "restore-evidence",
        status: "matched",
      },
    ],
    businessToc: [
      {
        id: "toc_rest_1",
        title: BIZ_RESTORE_TOC,
        category: "附件",
        status: "required",
        checked: true,
      },
    ],
    businessQuote: {
      rows: [
        {
          id: "qr_rest_1",
          name: BIZ_RESTORE_QUOTE,
          unit: "项",
          quantity: "1",
          unitPrice: "100",
          amount: "100",
          remark: "restore",
        },
      ],
      notes: BIZ_RESTORE_QUOTE_NOTES,
    },
    businessCommit: [
      {
        id: "cb_rest_1",
        title: BIZ_RESTORE_COMMIT,
        body: "restore-commit-body",
        needsStamp: true,
      },
    ],
    stateVersion: restoredVersion,
  };
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function projectMeta(id: string, mode: Mode, name: string) {
  return {
    id,
    workspaceId: "ws_e2e",
    name,
    industry: "能源",
    status: "draft",
    updatedAt: "2026-07-15T00:00:00.000Z",
    technicalPlanStep: 1,
    wordCount: 0,
    kind: mode === "tech" ? "technical" : "business",
  };
}

async function installRuntimeErrorGuards(page: Page) {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(String(err?.message || err));
  });
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (ev) => {
      const reason = (ev as PromiseRejectionEvent).reason;
      const text =
        reason instanceof Error
          ? reason.message
          : typeof reason === "string"
            ? reason
            : String(reason);
      const g = window as unknown as { __p12bd2Unhandled?: string[] };
      g.__p12bd2Unhandled = g.__p12bd2Unhandled || [];
      g.__p12bd2Unhandled.push(text);
    });
  });
  return {
    pageErrors,
    async readUnhandled(): Promise<string[]> {
      return page.evaluate(() => {
        return (
          (window as unknown as { __p12bd2Unhandled?: string[] })
            .__p12bd2Unhandled || []
        );
      });
    },
  };
}

function applyPutBodyToEditor(
  prev: EditorState,
  body: Record<string, unknown>,
  nextVersion: string,
): EditorState {
  const next: EditorState = {
    ...prev,
    stateVersion: nextVersion,
  };
  if (typeof body.parsedMarkdown === "string") {
    next.parsedMarkdown = body.parsedMarkdown;
  }
  if (typeof body.analysisOverview === "string") {
    next.analysisOverview = body.analysisOverview;
    next.analysis = { overview: body.analysisOverview };
  }
  if (body.analysis && typeof body.analysis === "object") {
    const overview = (body.analysis as { overview?: string }).overview;
    if (typeof overview === "string") {
      next.analysisOverview = overview;
      next.analysis = { overview };
    }
  }
  if (Array.isArray(body.outline)) {
    next.outline = body.outline as Array<Record<string, unknown>>;
  }
  if (Array.isArray(body.chapters)) {
    next.chapters = body.chapters as Array<Record<string, unknown>>;
  }
  return next;
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const path = url.pathname;
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(url.href)) {
      await route.fulfill({ status: 200, contentType: "text/css", body: "" });
      return;
    }

    if (host !== "127.0.0.1" && host !== "localhost") {
      state.externalHits.push(`${method} ${url.href}`);
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    const aId = state.mode === "tech" ? TECH_A : BIZ_A;
    const bId = state.mode === "tech" ? TECH_B : BIZ_B;
    const known = new Set([aId, bId]);

    if (path === "/api/health" && method === "GET") {
      await json(route, { status: "ok", service: "biaoshu" });
      return;
    }
    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, { mode: "disabled", bootstrapped: true });
      return;
    }
    if (path === "/api/auth/me" && method === "GET") {
      await json(route, { id: "user_e2e", name: "e2e" });
      return;
    }
    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-csrf" });
      return;
    }
    if (path === "/api/workspace" && method === "GET") {
      await json(route, { id: "ws_e2e", name: "E2E" });
      return;
    }
    if (path === "/api/settings" && method === "GET") {
      await json(route, {});
      return;
    }
    if (path === "/api/projects" && method === "GET") {
      await json(route, [
        projectMeta(aId, state.mode, "D2-A"),
        projectMeta(bId, state.mode, "D2-B"),
      ]);
      return;
    }

    const projectMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (projectMatch && method === "GET") {
      const pid = projectMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      await json(route, projectMeta(pid, state.mode, pid === aId ? "D2-A" : "D2-B"));
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch) {
      const pid = editorMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method === "GET") {
        state.editorGetLog.push({ projectId: pid, path });
        if (state.nextEditorGetFail) {
          state.nextEditorGetFail = false;
          await json(route, { detail: "editor_get_fail" }, 500);
          return;
        }
        await json(route, state.projects[pid]);
        return;
      }
      if (method === "PUT") {
        const raw = req.postData() || "{}";
        const body = JSON.parse(raw) as Record<string, unknown>;

        if (state.putMode.kind === "abort") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await route.abort("failed");
          return;
        }
        if (state.putMode.kind === "http_error") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await json(route, { detail: "server_error" }, 500);
          return;
        }
        if (state.putMode.kind === "hold") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await state.putMode.gate.wait();
          if (state.putMode.then === "conflict") {
            await json(
              route,
              {
                detail: {
                  code: "editor_state_version_conflict",
                  message: "blocked",
                  currentStateVersion: state.projects[pid].stateVersion,
                },
              },
              409,
            );
            return;
          }
          const nextVersion = allocateVersion(state);
          const next = applyPutBodyToEditor(
            state.projects[pid],
            body,
            nextVersion,
          );
          state.projects[pid] = next;
          const last = state.putLog[state.putLog.length - 1];
          if (last) last.responseVersion = nextVersion;
          await json(route, next);
          return;
        }
        const nextVersion = allocateVersion(state);
        const next = applyPutBodyToEditor(
          state.projects[pid],
          body,
          nextVersion,
        );
        state.projects[pid] = next;
        state.putLog.push({
          projectId: pid,
          body,
          responseVersion: nextVersion,
        });
        await json(route, next);
        return;
      }
    }

    const listMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/?$/,
    );
    if (listMatch) {
      const pid = listMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method === "GET") {
        state.listLog.push(pid);
        const listMode = resolveListMode(state, pid);
        if (listMode.kind === "hold") {
          await listMode.gate.wait();
        }
        if (state.listResponseOverride != null) {
          await json(route, state.listResponseOverride);
          return;
        }
        await json(route, { items: state.checkpoints[pid] || [] });
        return;
      }
      if (method === "POST") {
        const raw = req.postData() || "";
        if (state.putMode.kind === "hold" && !state.putMode.gate.released) {
          state.createArrivedWhilePutHeld = true;
        }
        const createMode = resolveCreateMode(state, pid);
        if (createMode.kind === "hold") {
          await createMode.gate.wait();
        }
        const bodyText = raw.trim() || "{}";
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(bodyText) as Record<string, unknown>;
        } catch {
          await json(route, { detail: "bad_json" }, 422);
          return;
        }
        if (Object.keys(parsed).length !== 0) {
          await json(route, { detail: "extra_fields" }, 422);
          return;
        }
        // HTTP 非 2xx：ApiError 会带 detail.code；不得被专用错误判别器冒充为成功体版本失败
        if (createMode.kind === "http_error") {
          state.createLog.push({
            projectId: pid,
            body: bodyText,
            responseVersion: null,
          });
          await json(
            route,
            {
              detail: {
                code: createMode.code,
                message: "create_http_error",
              },
            },
            createMode.status,
          );
          return;
        }
        const editor = state.projects[pid];
        const metaVersion =
          createMode.kind === "mismatch_version"
            ? allocateVersion(state)
            : editor.stateVersion;
        const meta: CheckpointMeta = {
          checkpointId: allocateCheckpointId(state),
          stateVersion: metaVersion,
          snapshotBytes: 128 + (state.checkpoints[pid]?.length || 0),
          outlineNodeCount: Array.isArray(editor.outline)
            ? editor.outline.length
            : 0,
          chapterCount: Array.isArray(editor.chapters)
            ? editor.chapters.length
            : 0,
          createdAt: new Date(
            Date.UTC(2026, 6, 15, 12, state.checkpointSeq, 0),
          ).toISOString(),
          displayName: null,
        };
        state.checkpoints[pid] = [meta, ...(state.checkpoints[pid] || [])].slice(
          0,
          20,
        );
        state.createLog.push({
          projectId: pid,
          body: bodyText,
          responseVersion: meta.stateVersion,
        });
        if (state.createResponseOverride != null) {
          await json(route, state.createResponseOverride, 201);
          return;
        }
        await json(route, meta, 201);
        return;
      }
    }

    const restoreMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/([^/]+)\/restore\/?$/,
    );
    if (restoreMatch && method === "POST") {
      const pid = restoreMatch[1];
      const checkpointId = restoreMatch[2];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      const raw = req.postData() || "{}";
      const body = JSON.parse(raw) as Record<string, unknown>;
      if (state.putMode.kind === "hold" && !state.putMode.gate.released) {
        state.restoreArrivedWhilePutHeld = true;
      }

      let mode = resolveRestoreMode(state, pid);
      if (mode.kind === "gate") {
        await mode.gate.wait();
        mode = { kind: mode.then } as RestoreMode;
      }

      if (mode.kind === "abort") {
        state.restoreLog.push({
          projectId: pid,
          checkpointId,
          body,
          responseVersion: null,
        });
        await route.abort("failed");
        return;
      }
      if (mode.kind === "conflict") {
        state.restoreLog.push({
          projectId: pid,
          checkpointId,
          body,
          responseVersion: null,
        });
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "conflict",
              currentStateVersion: state.projects[pid].stateVersion,
            },
          },
          409,
        );
        return;
      }

      const safetyId = allocateCheckpointId(state);
      const safety: CheckpointMeta = {
        checkpointId: safetyId,
        stateVersion: state.projects[pid].stateVersion,
        snapshotBytes: 200,
        outlineNodeCount: state.projects[pid].outline.length,
        chapterCount: state.projects[pid].chapters.length,
        createdAt: new Date().toISOString(),
        displayName: null,
      };
      state.checkpoints[pid] = [safety, ...(state.checkpoints[pid] || [])].slice(
        0,
        20,
      );

      if (mode.kind === "missing_version") {
        state.restoreLog.push({
          projectId: pid,
          checkpointId,
          body,
          responseVersion: null,
        });
        await json(route, {
          restoredCheckpointId: checkpointId,
          safetyCheckpointId: safetyId,
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "invalid_version") {
        state.restoreLog.push({
          projectId: pid,
          checkpointId,
          body,
          responseVersion: "not-a-version",
        });
        await json(route, {
          restoredCheckpointId: checkpointId,
          safetyCheckpointId: safetyId,
          stateVersion: "not-a-version",
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "blank_version") {
        state.restoreLog.push({
          projectId: pid,
          checkpointId,
          body,
          responseVersion: "  ",
        });
        await json(route, {
          restoredCheckpointId: checkpointId,
          safetyCheckpointId: safetyId,
          stateVersion: "  ",
          restoredAt: new Date().toISOString(),
        });
        return;
      }

      const targetMeta = (state.checkpoints[pid] || []).find(
        (c) => c.checkpointId === checkpointId,
      );
      const restoredVersion =
        targetMeta?.stateVersion || allocateVersion(state);
      const prev = state.projects[pid];
      // restore 成功后的服务端 editor-state 必须含多字段 sentinel（供完整水合证据）
      state.projects[pid] =
        state.mode === "tech"
          ? techRestoredEditor(prev, restoredVersion)
          : bizRestoredEditor(prev, restoredVersion);
      state.restoreLog.push({
        projectId: pid,
        checkpointId,
        body,
        responseVersion: restoredVersion,
      });
      if (state.restoreResponseOverride != null) {
        await json(route, state.restoreResponseOverride);
        return;
      }
      await json(route, {
        restoredCheckpointId: checkpointId,
        safetyCheckpointId: safetyId,
        stateVersion: restoredVersion,
        restoredAt: new Date().toISOString(),
      });
      return;
    }

    // P12G 单条命名：精确 PATCH .../display-name；body 仅 displayName；成功原位更新探针 meta
    const cpNameMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/([^/]+)\/display-name\/?$/,
    );
    if (cpNameMatch && method === "PATCH") {
      const pid = cpNameMatch[1];
      const checkpointId = cpNameMatch[2];
      const queryKeys = [...url.searchParams.keys()];
      const postData = req.postData();
      let bodyKeys: string[] = [];
      let displayName: string | null | undefined = undefined;
      if (postData != null && postData !== "") {
        try {
          const parsed = JSON.parse(postData) as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            const body = parsed as Record<string, unknown>;
            bodyKeys = Object.keys(body);
            if (Object.prototype.hasOwnProperty.call(body, "displayName")) {
              const v = body.displayName;
              if (v === null) displayName = null;
              else if (typeof v === "string") displayName = v;
              else displayName = undefined;
            }
          }
        } catch {
          bodyKeys = [];
          displayName = undefined;
        }
      }
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      state.nameLog.push({
        projectId: pid,
        checkpointId,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
        bodyKeys,
        displayName,
      });
      if (queryKeys.length > 0 || url.search.length > 1) {
        state.forbiddenHits.push(`${method} ${path}${url.search}`);
      }
      const nameMode = resolveNameMode(state, pid, checkpointId);
      if (nameMode.kind === "hold") {
        await nameMode.gate.wait();
        if (nameMode.then === "http_error") {
          await json(
            route,
            {
              detail: {
                code: "editor_state_checkpoint_display_name_error",
                message: "保存检查点名称失败",
              },
            },
            nameMode.status ?? 500,
          );
          state.nameCompleteLog.push({
            projectId: pid,
            checkpointId,
            status: nameMode.status ?? 500,
            displayName: null,
          });
          return;
        }
      }
      if (nameMode.kind === "http_error") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_checkpoint_display_name_error",
              message: "保存检查点名称失败",
            },
          },
          nameMode.status,
        );
        state.nameCompleteLog.push({
          projectId: pid,
          checkpointId,
          status: nameMode.status,
          displayName: null,
        });
        return;
      }
      const list = state.checkpoints[pid] || [];
      const idx = list.findIndex((c) => c.checkpointId === checkpointId);
      if (idx < 0) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_checkpoint_not_found",
              message: "检查点不存在",
            },
          },
          404,
        );
        state.nameCompleteLog.push({
          projectId: pid,
          checkpointId,
          status: 404,
          displayName: null,
        });
        return;
      }
      if (
        bodyKeys.length !== 1 ||
        bodyKeys[0] !== "displayName" ||
        (displayName !== null && typeof displayName !== "string")
      ) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_checkpoint_display_name_invalid",
              message: "检查点名称无效",
            },
          },
          422,
        );
        state.nameCompleteLog.push({
          projectId: pid,
          checkpointId,
          status: 422,
          displayName: null,
        });
        return;
      }
      if (state.nameResponseOverride != null) {
        await json(route, state.nameResponseOverride);
        state.nameCompleteLog.push({
          projectId: pid,
          checkpointId,
          status: 200,
          displayName: null,
        });
        return;
      }
      const meta = list[idx];
      meta.displayName = displayName;
      list[idx] = meta;
      await json(route, { displayName });
      state.nameCompleteLog.push({
        projectId: pid,
        checkpointId,
        status: 200,
        displayName,
      });
      return;
    }

    const detailMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/([^/]+)\/?$/,
    );
    if (detailMatch && method === "GET") {
      state.detailLog.push(path);
      state.forbiddenHits.push(`${method} ${path}`);
      await json(route, { detail: "detail_not_allowed_in_d2_ui" }, 404);
      return;
    }

    if (
      path.includes("/tasks") ||
      path.includes("/files") ||
      path.includes("/templates") ||
      path.includes("/cards")
    ) {
      await json(route, method === "GET" ? [] : { ok: true });
      return;
    }

    state.forbiddenHits.push(`${method} ${path}`);
    await json(route, { detail: "forbidden" }, 404);
  });
}

async function openWorkspace(page: Page, mode: Mode, projectId: string) {
  const path =
    mode === "tech"
      ? `/technical-plan/${projectId}/analysis`
      : `/business-bid/${projectId}`;
  await page.goto(path);
  if (mode === "tech") {
    await expect(
      page.getByTestId("technical-editor-workspace"),
    ).toBeVisible();
    await expect(
      page.getByTestId("technical-analysis-overview"),
    ).toBeVisible();
  } else {
    await expect(
      page.getByTestId("business-editor-workspace"),
    ).toBeVisible();
    await expect(page.getByLabel("商务条款解析 Markdown")).toBeVisible();
  }
  await expect(page.getByTestId("editor-state-checkpoint-panel")).toBeVisible();
}

async function editContent(page: Page, mode: Mode, text: string) {
  if (mode === "tech") {
    await page.getByTestId("technical-analysis-overview").fill(text);
  } else {
    await page.getByLabel("商务条款解析 Markdown").fill(text);
  }
}

async function readContent(page: Page, mode: Mode): Promise<string> {
  if (mode === "tech") {
    return page.getByTestId("technical-analysis-overview").inputValue();
  }
  return page.getByLabel("商务条款解析 Markdown").inputValue();
}

async function expandPanel(page: Page) {
  const toggle = page.getByTestId("editor-state-checkpoint-toggle");
  const body = page.getByTestId("editor-state-checkpoint-body");
  if (await body.count()) {
    return;
  }
  await toggle.click();
  await expect(body).toBeVisible();
}

function conflictTestId(mode: Mode) {
  return mode === "tech"
    ? "technical-editor-state-conflict"
    : "business-editor-state-conflict";
}

function reloadTestId(mode: Mode) {
  return mode === "tech"
    ? "technical-editor-state-reload"
    : "business-editor-state-reload";
}

async function assertNoIdLeak(page: Page, state: ProbeState) {
  const html = await page.content();
  for (const list of Object.values(state.checkpoints)) {
    for (const item of list) {
      expect(html).not.toContain(item.checkpointId);
      expect(html).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(html).not.toContain(editor.stateVersion);
  }
  const storage = await page.evaluate(() => {
    const ls: string[] = [];
    const ss: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k) ls.push(`${k}=${localStorage.getItem(k)}`);
    }
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k) ss.push(`${k}=${sessionStorage.getItem(k)}`);
    }
    return { ls, ss, href: location.href };
  });
  const blob = JSON.stringify(storage);
  expect(blob).not.toMatch(/escp_[0-9a-f]{32}/);
  expect(blob).not.toMatch(/esv_[0-9a-f]{32}/);
}

function debounceMs(mode: Mode) {
  return mode === "tech" ? TECH_DEBOUNCE_MS : BIZ_DEBOUNCE_MS;
}

function projectIds(mode: Mode) {
  return mode === "tech"
    ? { a: TECH_A, b: TECH_B, contentA: TECH_OVERVIEW, contentB: TECH_OVERVIEW_B }
    : { a: BIZ_A, b: BIZ_B, contentA: BIZ_MD, contentB: BIZ_MD_B };
}

function seedCheckpoint(
  state: ProbeState,
  projectId: string,
  n = 1,
): CheckpointMeta {
  const meta: CheckpointMeta = {
    checkpointId: seedCheckpointId(n),
    stateVersion: seedStateVersion(5),
    snapshotBytes: 100,
    outlineNodeCount: state.mode === "tech" ? 1 : 0,
    chapterCount: state.mode === "tech" ? 1 : 0,
    createdAt: "2026-07-15T10:00:00.000Z",
    displayName: null,
  };
  state.checkpoints[projectId] = [meta];
  state.checkpointSeq = Math.max(state.checkpointSeq, n);
  return meta;
}

/**
 * 用途：在 PUT 仍挂起时用 debounce 窗口推进证明业务 POST 仍为 0。
 * 禁止固定 sleep；用 clock.fastForward + 精确计数。
 */
async function assertNoBusinessPostWhilePutHeld(
  page: Page,
  mode: Mode,
  state: ProbeState,
  kind: "create" | "restore",
) {
  await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
  await page.clock.fastForward(debounceMs(mode) + 100);
  await page.clock.fastForward(debounceMs(mode) + 100);
  if (kind === "create") {
    expect(state.createLog.length).toBe(0);
    expect(state.createArrivedWhilePutHeld).toBe(false);
  } else {
    expect(state.restoreLog.length).toBe(0);
    expect(state.restoreArrivedWhilePutHeld).toBe(false);
  }
}

// ---------------------------------------------------------------------------
// 技术标模式
// ---------------------------------------------------------------------------
test.describe("P12B-D2 技术标检查点入口", () => {
  test.describe.configure({ mode: "serial" });

  test("立即编辑后创建：PUT 先完成，POST {} 后发，版本相等；PUT 挂起时 POST=0", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);
    const listBefore = state.listLog.length;

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "tech", `${TECH_OVERVIEW}\n本地创建编辑`);
    await page.getByTestId("editor-state-checkpoint-create").click();

    await assertNoBusinessPostWhilePutHeld(page, "tech", state, "create");

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);

    const put = state.putLog[0];
    const create = state.createLog[0];
    expect(create.body.replace(/\s/g, "")).toBe("{}");
    expect(create.responseVersion).toBe(put.responseVersion);
    expect(put.body.expectedStateVersion).toMatch(STATE_VERSION_RE);
    expect(state.listLog.length).toBeGreaterThan(listBefore);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state);
  });

  test("双击创建：业务 create POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("tech");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const createBtn = page.getByTestId("editor-state-checkpoint-create");
    await Promise.all([createBtn.click(), createBtn.click()]);

    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.createLog.length).toBe(1);
  });

  test("forced-create PUT abort：create POST=0、保留本地、全状态阻断、两窗口零 PUT", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const localText = `${TECH_OVERVIEW}\nforced-abort`;
    await editContent(page, "tech", localText);
    // 先让编辑防抖 PUT 完成，避免与 forced PUT 混淆
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    const putsAfterEdit = state.putLog.length;

    state.putMode = { kind: "abort" };
    await page.getByTestId("editor-state-checkpoint-create").click();

    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterEdit + 1);
    expect(state.createLog.length).toBe(0);

    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
    await expect(page.getByTestId(conflictTestId("tech"))).toContainText(
      FULL_STATE_CONFLICT_MSG,
    );
    expect(await readContent(page, "tech")).toBe(localText);

    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterEdit + 1);
    expect(state.createLog.length).toBe(0);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("forced-create PUT 500：create POST=0、保留本地、全状态阻断", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const localText = `${TECH_OVERVIEW}\nforced-500`;
    await editContent(page, "tech", localText);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    const putsAfterEdit = state.putLog.length;

    state.putMode = { kind: "http_error" };
    await page.getByTestId("editor-state-checkpoint-create").click();

    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterEdit + 1);
    expect(state.createLog.length).toBe(0);
    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
    expect(await readContent(page, "tech")).toBe(localText);

    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterEdit + 1);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("create POST 版本不匹配：全状态阻断 outcome blocked", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    state.createMode = { kind: "mismatch_version" };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText("当前无法保存检查点");
  });

  test("普通 PUT 挂起时 restore POST=0；释放后 expected=PUT 响应版本", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "tech", `${TECH_OVERVIEW}\n挂起后再恢复`);
    await page.clock.fastForward(debounceMs("tech") + 100);

    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-confirm-0"),
    ).toContainText(RESTORE_CONFIRM);
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await assertNoBusinessPostWhilePutHeld(page, "tech", state, "restore");

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    const putVersion = state.putLog[0].responseVersion;
    expect(putVersion).toMatch(STATE_VERSION_RE);
    expect(state.restoreLog[0].body.expectedStateVersion).toBe(putVersion);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("二次确认前 restore=0；成功唯一 editor-state GET 并水合；列表现安全检查点", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    const getsAfterOpen = state.editorGetLog.length;
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    const getsBeforeConfirm = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;

    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === TECH_A).length -
          getsBeforeConfirm,
        { timeout: 10_000 },
      )
      .toBe(1);

    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);

    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText("已恢复到所选检查点");

    await expect
      .poll(() => state.checkpoints[TECH_A].length)
      .toBeGreaterThanOrEqual(2);
    await expect(page.getByTestId("editor-state-checkpoint-item-0")).toBeVisible();
    expect(state.detailLog.length).toBe(0);
    expect(getsAfterOpen).toBeGreaterThan(0);
    await assertNoIdLeak(page, state);
  });

  test("成功 restore 后两窗口零 PUT；真实编辑精确 +1 PUT", async ({ page }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);

    // 唯一 editor-state GET 后：推进两个 debounce 窗口，PUT 增量仍为 0
    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    // 真实用户编辑必须精确新增 1 次 PUT
    await editContent(page, "tech", `${RESTORED_TECH}\n用户下一编辑`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("双击确认恢复：业务 restore POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    const confirm = page.getByTestId(
      "editor-state-checkpoint-confirm-restore-0",
    );
    await Promise.all([confirm.click(), confirm.click()]);

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
  });

  for (const kind of [
    "abort",
    "missing_version",
    "invalid_version",
    "blank_version",
  ] as const) {
    test(`restore ${kind}：保留本地、POST=1、再点仍=1、两窗口 PUT=0`, async ({
      page,
    }) => {
      const state = createProbeState("tech");
      seedCheckpoint(state, TECH_A);
      state.restoreMode = { kind };
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "tech", TECH_A);
      await expandPanel(page);

      const localText = `${TECH_OVERVIEW}\n保留-${kind}`;
      await editContent(page, "tech", localText);
      await page.clock.fastForward(debounceMs("tech") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBeGreaterThan(0);
      const putsAfterEdit = state.putLog.length;

      await page.getByTestId("editor-state-checkpoint-restore-0").click();
      await page
        .getByTestId("editor-state-checkpoint-confirm-restore-0")
        .click();

      await expect
        .poll(() => state.restoreLog.length, { timeout: 10_000 })
        .toBe(1);

      await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
      await expect(page.getByTestId(conflictTestId("tech"))).toContainText(
        FULL_STATE_CONFLICT_MSG,
      );
      expect(await readContent(page, "tech")).toBe(localText);

      const restoreBtn = page.getByTestId("editor-state-checkpoint-restore-0");
      if (await restoreBtn.isEnabled().catch(() => false)) {
        await restoreBtn.click();
        const confirm = page.getByTestId(
          "editor-state-checkpoint-confirm-restore-0",
        );
        if (await confirm.count()) {
          await confirm.click();
        }
      }
      expect(state.restoreLog.length).toBe(1);

      await page.clock.fastForward(debounceMs("tech") + 100);
      await page.clock.fastForward(debounceMs("tech") + 100);
      expect(state.putLog.length).toBe(putsAfterEdit);

      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("POST 成功 + GET 失败：业务成功提示并保持阻断；显式重载才恢复", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    state.nextEditorGetFail = true;

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(RESTORE_RELOAD_FAIL);
    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();

    state.nextEditorGetFail = false;
    await page.getByTestId(reloadTestId("tech")).click();
    await expect(page.getByTestId(conflictTestId("tech"))).toBeHidden({
      timeout: 10_000,
    });
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
  });

  test("迟到 list：折叠后释放不得污染；无详情", async ({ page }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A, 1);
    const listGate = createHoldGate();
    state.listMode = { kind: "hold", gate: listGate };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    // 展开触发 list（挂起）
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    // 必须等到 route 真正进入 gate，而非仅 listLog
    await listGate.waitUntilEntered(1);
    expect(listGate.enteredCount).toBe(1);
    expect(state.listLog.filter((p) => p === TECH_A).length).toBe(1);

    // 折叠：递增会话
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toHaveCount(0);

    // 释放迟到 list：不得回写折叠后 UI
    listGate.release();
    state.listMode = { kind: "ok" };

    // 再展开应重新 GET list
    await expandPanel(page);
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBeGreaterThanOrEqual(2);
    await expect(page.getByTestId("editor-state-checkpoint-item-0")).toBeVisible();
    expect(state.detailLog.length).toBe(0);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
  });

  test("A→B 迟到 create：旧项目 create 不得污染新项目正文/提示/阻断", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const createGateA = createHoldGate();
    state.createModeByProject[TECH_A] = { kind: "hold", gate: createGateA };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-create").click();
    // 必须等 A create route 真正进入 gate
    await createGateA.waitUntilEntered(1);
    expect(createGateA.enteredCount).toBe(1);
    expect(createGateA.released).toBe(false);

    // create 仍挂起时切到 B
    await openWorkspace(page, "tech", TECH_B);
    await expandPanel(page);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_B,
    ).length;
    const putsOnBBefore = state.putLog.filter((p) => p.projectId === TECH_B)
      .length;
    const createsOnBBefore = state.createLog.filter(
      (c) => c.projectId === TECH_B,
    ).length;
    const listBBefore = state.listLog.filter((p) => p === TECH_B).length;

    createGateA.release();
    delete state.createModeByProject[TECH_A];

    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);

    // B 正文/阻断/提示精确不受 A 迟到 create 影响（禁止 conditional skip）
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
    expect(
      state.createLog.filter((c) => c.projectId === TECH_B).length,
    ).toBe(createsOnBBefore);
    expect(
      state.putLog.filter((p) => p.projectId === TECH_B).length,
    ).toBe(putsOnBBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_B).length,
    ).toBe(getsBBefore);
    expect(state.listLog.filter((p) => p === TECH_B).length).toBe(listBBefore);
    expect(state.detailLog.length).toBe(0);
  });

  test("A→B 迟到 restore：旧项目 restore 不得污染新项目正文/提示/阻断", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    const restoreGateA = createHoldGate();
    state.restoreModeByProject[TECH_A] = {
      kind: "gate",
      gate: restoreGateA,
      then: "ok",
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    // 必须等 A restore route 真正进入 gate（禁止 !released 恒真证据）
    await restoreGateA.waitUntilEntered(1);
    expect(restoreGateA.enteredCount).toBe(1);
    expect(restoreGateA.released).toBe(false);

    await openWorkspace(page, "tech", TECH_B);
    await expandPanel(page);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_B,
    ).length;
    const listBBefore = state.listLog.filter((p) => p === TECH_B).length;

    restoreGateA.release();
    delete state.restoreModeByProject[TECH_A];

    await expect
      .poll(
        () => state.restoreLog.filter((r) => r.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);

    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_B).length,
    ).toBe(0);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_B).length,
    ).toBe(getsBBefore);
    expect(state.listLog.filter((p) => p === TECH_B).length).toBe(listBBefore);
    expect(state.detailLog.length).toBe(0);
  });

  test("跨项目操作 token：A create 挂起时 B 可独立 create；A finally 不误清 B", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const createGateA = createHoldGate();
    const createGateB = createHoldGate();
    state.createModeByProject[TECH_A] = { kind: "hold", gate: createGateA };
    state.createModeByProject[TECH_B] = { kind: "hold", gate: createGateB };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-create").click();
    await createGateA.waitUntilEntered(1);
    expect(createGateA.released).toBe(false);

    // A 仍挂起时导航 B：B 合法 create 必须能进入自己的 gate
    await openWorkspace(page, "tech", TECH_B);
    await expandPanel(page);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await page.getByTestId("editor-state-checkpoint-create").click();
    await createGateB.waitUntilEntered(1);
    expect(createGateB.enteredCount).toBe(1);
    expect(createGateB.released).toBe(false);

    // 保持 B 挂起，释放 A：A finally 不得清掉 B 的 token
    createGateA.release();
    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    // A 完成后 B 仍挂起且尚未落 createLog
    expect(createGateB.enteredCount).toBe(1);
    expect(createGateB.released).toBe(false);
    expect(
      state.createLog.filter((c) => c.projectId === TECH_B).length,
    ).toBe(0);

    // 折叠再展开：重置面板本地 createBusy/session；Hook 的 B token 仍应在
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toHaveCount(0);
    await expandPanel(page);
    await expect(
      page.getByTestId("editor-state-checkpoint-create"),
    ).toBeEnabled();

    // 正常点击（禁止 force）：token 仍属 B → 立即固定失败；不得再入 B gate
    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    expect(createGateB.enteredCount).toBe(1);
    expect(
      state.createLog.filter((c) => c.projectId === TECH_B).length,
    ).toBe(0);

    createGateB.release();
    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === TECH_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    // 服务端仅 1 次 create；折叠作废旧会话，不得显示成功
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
  });

  test("完整水合：restore 后下一 PUT 精确携带 analysis/outline/chapters/facts/guidance/mode/matrix", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);

    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    await editContent(page, "tech", `${RESTORED_TECH}\n水合后下一编辑`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.analysisOverview).toBe(`${RESTORED_TECH}\n水合后下一编辑`);
    // 实际 analysis 对象必须含水合后的 sentinel overview（非仅顶层 analysisOverview）
    const analysis = putBody.analysis as { overview?: string } | undefined;
    expect(analysis && typeof analysis === "object").toBe(true);
    expect(analysis?.overview).toBe(`${RESTORED_TECH}\n水合后下一编辑`);
    expect(JSON.stringify(analysis)).toContain(RESTORED_TECH);
    expect(JSON.stringify(putBody.outline)).toContain(TECH_RESTORE_OUTLINE_TITLE);
    expect(JSON.stringify(putBody.chapters)).toContain(TECH_RESTORE_CHAPTER_BODY);
    expect(JSON.stringify(putBody.facts)).toContain(TECH_RESTORE_FACT);
    expect(JSON.stringify(putBody.guidance)).toContain(TECH_RESTORE_GUIDANCE);
    expect(putBody.mode).toBe(TECH_RESTORE_MODE);
    expect(JSON.stringify(putBody.responseMatrix)).toContain(
      TECH_RESTORE_MATRIX_TEXT,
    );
    expect(putBody.responseMatrixVersion).toBe(TECH_RESTORE_MATRIX_VERSION);
  });

  test("API shape：list 顶层额外键/metadata 异常/超20条固定失败无泄漏", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    // 1) list 顶层额外键
    state.listResponseOverride = {
      items: state.checkpoints[TECH_A],
      total: 1,
      snapshot: "LEAK_LIST_TOP_SNAPSHOT",
    };
    await openWorkspace(page, "tech", TECH_A);
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBeGreaterThanOrEqual(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-item-0"),
    ).toHaveCount(0);
    let html = await page.content();
    expect(html).not.toContain("LEAK_LIST_TOP_SNAPSHOT");
    expect(html).not.toContain(state.checkpoints[TECH_A][0].checkpointId);
    expect(html).not.toContain(state.checkpoints[TECH_A][0].stateVersion);

    // 2) metadata 含 snapshot 额外键
    state.listResponseOverride = {
      items: [
        {
          ...state.checkpoints[TECH_A][0],
          snapshot: "LEAK_META_SNAPSHOT",
        },
      ],
    };
    await page.getByTestId("editor-state-checkpoint-refresh").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBeGreaterThanOrEqual(2);
    await expect(
      page.getByTestId("editor-state-checkpoint-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    html = await page.content();
    expect(html).not.toContain("LEAK_META_SNAPSHOT");

    // 3) 负数 snapshotBytes
    state.listResponseOverride = {
      items: [{ ...state.checkpoints[TECH_A][0], snapshotBytes: -1 }],
    };
    await page.getByTestId("editor-state-checkpoint-refresh").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length)
      .toBeGreaterThanOrEqual(3);
    await expect(
      page.getByTestId("editor-state-checkpoint-list-error"),
    ).toContainText(MSG_LIST_FAIL);

    // 4) 浮点 chapterCount
    state.listResponseOverride = {
      items: [{ ...state.checkpoints[TECH_A][0], chapterCount: 1.5 }],
    };
    await page.getByTestId("editor-state-checkpoint-refresh").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length)
      .toBeGreaterThanOrEqual(4);
    await expect(
      page.getByTestId("editor-state-checkpoint-list-error"),
    ).toContainText(MSG_LIST_FAIL);

    // 5) 超过 20 条
    const over = Array.from({ length: 21 }, (_, i) => ({
      ...state.checkpoints[TECH_A][0],
      checkpointId: seedCheckpointId(100 + i),
      stateVersion: seedStateVersion(100 + i),
    }));
    state.listResponseOverride = { items: over };
    await page.getByTestId("editor-state-checkpoint-refresh").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length)
      .toBeGreaterThanOrEqual(5);
    await expect(
      page.getByTestId("editor-state-checkpoint-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-item-0"),
    ).toHaveCount(0);
    html = await page.content();
    expect(html).not.toContain(over[0].checkpointId);
    expect(html).not.toContain(over[0].stateVersion);
    expect(state.detailLog.length).toBe(0);
  });

  test("API shape：restore 成功体额外键→阻断保留本地零重试两窗口零 PUT", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedCheckpoint(state, TECH_A);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const localText = `${TECH_OVERVIEW}\nshape-restore-extra`;
    await editContent(page, "tech", localText);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    const putsAfterEdit = state.putLog.length;

    state.restoreResponseOverride = {
      restoredCheckpointId: state.checkpoints[TECH_A][0].checkpointId,
      safetyCheckpointId: seedCheckpointId(50),
      stateVersion: seedStateVersion(50),
      restoredAt: "2026-07-15T12:00:00.000Z",
      snapshot: "LEAK_RESTORE_SNAPSHOT",
      extra: true,
    };

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
    expect(await readContent(page, "tech")).toBe(localText);
    // 额外键视为不确定 → blocked（非普通 post_failed）
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_RESTORE_BLOCKED);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_RESTORE_OK);

    // 零重试 + 两窗口零 PUT（全状态阻断后恢复按钮禁用，不得再发）
    const restoreBtn = page.getByTestId("editor-state-checkpoint-restore-0");
    await expect(restoreBtn).toBeDisabled();
    expect(state.restoreLog.length).toBe(1);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterEdit);

    const html = await page.content();
    expect(html).not.toContain("LEAK_RESTORE_SNAPSHOT");
    expect(html).not.toContain(state.checkpoints[TECH_A][0].checkpointId);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("API shape：create 元数据额外键固定失败且不显示成功", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);
    state.createResponseOverride = {
      checkpointId: seedCheckpointId(60),
      stateVersion: state.projects[TECH_A].stateVersion,
      snapshotBytes: 10,
      outlineNodeCount: 1,
      chapterCount: 1,
      createdAt: "2026-07-15T12:00:00.000Z",
      snapshot: "LEAK_CREATE_SNAPSHOT",
    };
    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    // 额外字段不得扩大为全量阻断
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    const html = await page.content();
    expect(html).not.toContain("LEAK_CREATE_SNAPSHOT");
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  for (const kind of ["missing", "blank", "invalid"] as const) {
    test(`create POST 响应 stateVersion ${kind}：forced PUT 成功、全量阻断、POST=1、两窗口零 PUT`, async ({
      page,
    }) => {
      const state = createProbeState("tech");
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "tech", TECH_A);
      await expandPanel(page);

      const localText = `${TECH_OVERVIEW}\ncreate-sv-${kind}`;
      await editContent(page, "tech", localText);
      await page.clock.fastForward(debounceMs("tech") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBeGreaterThan(0);
      const putsBeforeCreate = state.putLog.length;

      const baseMeta = {
        checkpointId: seedCheckpointId(70 + (kind === "missing" ? 1 : kind === "blank" ? 2 : 3)),
        snapshotBytes: 10,
        outlineNodeCount: 1,
        chapterCount: 1,
        createdAt: "2026-07-15T12:00:00.000Z",
      };
      if (kind === "missing") {
        state.createResponseOverride = { ...baseMeta };
      } else if (kind === "blank") {
        state.createResponseOverride = { ...baseMeta, stateVersion: "  " };
      } else {
        state.createResponseOverride = {
          ...baseMeta,
          stateVersion: "not-a-version",
        };
      }

      await page.getByTestId("editor-state-checkpoint-create").click();
      await expect
        .poll(() => state.createLog.length, { timeout: 10_000 })
        .toBe(1);
      // forced PUT 成功后再 POST create
      expect(state.putLog.length).toBeGreaterThanOrEqual(putsBeforeCreate + 1);
      expect(state.createLog.length).toBe(1);

      await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
      await expect(page.getByTestId(conflictTestId("tech"))).toContainText(
        FULL_STATE_CONFLICT_MSG,
      );
      await expect(
        page.getByTestId("editor-state-checkpoint-status"),
      ).toContainText(MSG_CREATE_BLOCKED);
      expect(await readContent(page, "tech")).toBe(localText);

      const putsAfterBlock = state.putLog.length;
      await page.clock.fastForward(debounceMs("tech") + 100);
      await page.clock.fastForward(debounceMs("tech") + 100);
      expect(state.putLog.length).toBe(putsAfterBlock);
      expect(state.createLog.length).toBe(1);

      // 无 ID/版本/非法原文泄漏
      const html = await page.content();
      expect(html).not.toContain("not-a-version");
      expect(html).not.toContain(baseMeta.checkpointId);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("create POST HTTP 500 + 冒充专用 code：固定失败且不全量阻断（技术）", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    // ApiError 会从 detail.code 生成同形 { code }；若判别器信任任意 code 会错误 enterFullStateBlock
    state.createMode = {
      kind: "http_error",
      status: 500,
      code: "checkpoint_create_state_version_invalid",
    };
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const localText = `${TECH_OVERVIEW}\ncreate-http-code-spoof`;
    await editContent(page, "tech", localText);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    const putsBeforeCreate = state.putLog.length;

    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    // forced PUT 成功后再 POST create 精确 1
    expect(state.putLog.length).toBeGreaterThanOrEqual(putsBeforeCreate + 1);
    expect(state.createLog.length).toBe(1);
    expect(state.createLog[0].responseVersion).toBeNull();

    // HTTP 失败 → 普通 MSG_CREATE_FAIL；不得显示成功、不得全状态冲突/阻断
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_BLOCKED);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    expect(await readContent(page, "tech")).toBe(localText);

    const putsAfterFail = state.putLog.length;
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterFail);
    expect(state.createLog.length).toBe(1);

    const html = await page.content();
    expect(html).not.toContain("checkpoint_create_state_version_invalid");
    expect(html).not.toContain("create_http_error");
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 商务标模式
// ---------------------------------------------------------------------------
test.describe("P12B-D2 商务标检查点入口", () => {
  test.describe.configure({ mode: "serial" });

  test("立即编辑后创建：PUT 先完成，POST {} 后发，版本相等；PUT 挂起时 POST=0", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "biz", `${BIZ_MD}\n本地创建编辑`);
    await page.getByTestId("editor-state-checkpoint-create").click();

    await assertNoBusinessPostWhilePutHeld(page, "biz", state, "create");

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);

    expect(state.createLog[0].body.replace(/\s/g, "")).toBe("{}");
    expect(state.createLog[0].responseVersion).toBe(
      state.putLog[0].responseVersion,
    );
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("双击创建：业务 create POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("biz");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const createBtn = page.getByTestId("editor-state-checkpoint-create");
    await Promise.all([createBtn.click(), createBtn.click()]);

    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
  });

  test("forced-create PUT abort：create POST=0、保留本地、全状态阻断、两窗口零 PUT", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const localText = `${BIZ_MD}\nforced-abort`;
    await editContent(page, "biz", localText);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    const putsAfterEdit = state.putLog.length;

    state.putMode = { kind: "abort" };
    await page.getByTestId("editor-state-checkpoint-create").click();

    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterEdit + 1);
    expect(state.createLog.length).toBe(0);
    await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
    expect(await readContent(page, "biz")).toBe(localText);

    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfterEdit + 1);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("forced-create PUT 500：create POST=0、保留本地、全状态阻断", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const localText = `${BIZ_MD}\nforced-500`;
    await editContent(page, "biz", localText);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    const putsAfterEdit = state.putLog.length;

    state.putMode = { kind: "http_error" };
    await page.getByTestId("editor-state-checkpoint-create").click();

    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterEdit + 1);
    expect(state.createLog.length).toBe(0);
    await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
    expect(await readContent(page, "biz")).toBe(localText);

    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfterEdit + 1);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("普通 PUT 挂起时 restore POST=0；释放后 expected=PUT 响应版本", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "biz", `${BIZ_MD}\n挂起后再恢复`);
    await page.clock.fastForward(debounceMs("biz") + 100);

    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await assertNoBusinessPostWhilePutHeld(page, "biz", state, "restore");

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog[0].body.expectedStateVersion).toBe(
      state.putLog[0].responseVersion,
    );
  });

  test("二次确认前 restore=0；成功唯一 GET 并水合；列表现安全检查点", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    const getsAfterOpen = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsAfterOpen,
        { timeout: 10_000 },
      )
      .toBe(1);

    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);
    expect(state.detailLog.length).toBe(0);
    await assertNoIdLeak(page, state);
  });

  test("成功 restore 后两窗口零 PUT；真实编辑精确 +1 PUT（无吞下一编辑）", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);

    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    await editContent(page, "biz", `${RESTORED_BIZ}\n用户下一编辑`);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  for (const kind of [
    "abort",
    "missing_version",
    "invalid_version",
    "blank_version",
  ] as const) {
    test(`restore ${kind}：保留本地、POST=1、两窗口 PUT=0`, async ({ page }) => {
      const state = createProbeState("biz");
      seedCheckpoint(state, BIZ_A);
      state.restoreMode = { kind };
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "biz", BIZ_A);
      await expandPanel(page);

      const localText = `${BIZ_MD}\n保留-${kind}`;
      await editContent(page, "biz", localText);
      await page.clock.fastForward(debounceMs("biz") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBeGreaterThan(0);
      const putsAfterEdit = state.putLog.length;

      await page.getByTestId("editor-state-checkpoint-restore-0").click();
      await page
        .getByTestId("editor-state-checkpoint-confirm-restore-0")
        .click();

      await expect
        .poll(() => state.restoreLog.length, { timeout: 10_000 })
        .toBe(1);
      await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
      expect(await readContent(page, "biz")).toBe(localText);

      await page.clock.fastForward(debounceMs("biz") + 100);
      await page.clock.fastForward(debounceMs("biz") + 100);
      expect(state.putLog.length).toBe(putsAfterEdit);
      expect(state.restoreLog.length).toBe(1);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("POST 成功 + GET 失败：业务成功提示并保持阻断", async ({ page }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    state.nextEditorGetFail = true;

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(RESTORE_RELOAD_FAIL);
    await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();

    state.nextEditorGetFail = false;
    await page.getByTestId(reloadTestId("biz")).click();
    await expect(page.getByTestId(conflictTestId("biz"))).toBeHidden({
      timeout: 10_000,
    });
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);
  });

  test("迟到 list：折叠后释放不得污染；无详情", async ({ page }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A, 1);
    const listGate = createHoldGate();
    state.listMode = { kind: "hold", gate: listGate };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await listGate.waitUntilEntered(1);
    expect(listGate.enteredCount).toBe(1);

    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toHaveCount(0);

    listGate.release();
    state.listMode = { kind: "ok" };

    await expandPanel(page);
    await expect
      .poll(() => state.listLog.filter((p) => p === BIZ_A).length, {
        timeout: 10_000,
      })
      .toBeGreaterThanOrEqual(2);
    await expect(page.getByTestId("editor-state-checkpoint-item-0")).toBeVisible();
    expect(state.detailLog.length).toBe(0);
    expect(await readContent(page, "biz")).toBe(BIZ_MD);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
  });

  test("A→B 迟到 create：旧项目 create 不得污染新项目正文/提示/阻断", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const createGateA = createHoldGate();
    state.createModeByProject[BIZ_A] = { kind: "hold", gate: createGateA };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-create").click();
    await createGateA.waitUntilEntered(1);
    expect(createGateA.released).toBe(false);

    await openWorkspace(page, "biz", BIZ_B);
    await expandPanel(page);
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_B,
    ).length;
    const putsOnBBefore = state.putLog.filter((p) => p.projectId === BIZ_B)
      .length;
    const createsOnBBefore = state.createLog.filter(
      (c) => c.projectId === BIZ_B,
    ).length;

    createGateA.release();
    delete state.createModeByProject[BIZ_A];
    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);

    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
    expect(
      state.createLog.filter((c) => c.projectId === BIZ_B).length,
    ).toBe(createsOnBBefore);
    expect(
      state.putLog.filter((p) => p.projectId === BIZ_B).length,
    ).toBe(putsOnBBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_B).length,
    ).toBe(getsBBefore);
    expect(state.detailLog.length).toBe(0);
  });

  test("A→B 迟到 restore：旧项目 restore 不得污染新项目正文/提示/阻断", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    const restoreGateA = createHoldGate();
    state.restoreModeByProject[BIZ_A] = {
      kind: "gate",
      gate: restoreGateA,
      then: "ok",
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();
    await restoreGateA.waitUntilEntered(1);
    expect(restoreGateA.enteredCount).toBe(1);
    expect(restoreGateA.released).toBe(false);

    await openWorkspace(page, "biz", BIZ_B);
    await expandPanel(page);
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_B,
    ).length;

    restoreGateA.release();
    delete state.restoreModeByProject[BIZ_A];

    await expect
      .poll(
        () => state.restoreLog.filter((r) => r.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);

    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveCount(0);
    expect(
      state.restoreLog.filter((r) => r.projectId === BIZ_B).length,
    ).toBe(0);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_B).length,
    ).toBe(getsBBefore);
    expect(state.detailLog.length).toBe(0);
  });

  test("双击确认恢复：业务 restore POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    const confirm = page.getByTestId(
      "editor-state-checkpoint-confirm-restore-0",
    );
    await Promise.all([confirm.click(), confirm.click()]);
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
  });

  test("跨项目操作 token：A create 挂起时 B 可独立 create；A finally 不误清 B", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const createGateA = createHoldGate();
    const createGateB = createHoldGate();
    state.createModeByProject[BIZ_A] = { kind: "hold", gate: createGateA };
    state.createModeByProject[BIZ_B] = { kind: "hold", gate: createGateB };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-create").click();
    await createGateA.waitUntilEntered(1);

    await openWorkspace(page, "biz", BIZ_B);
    await expandPanel(page);
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await page.getByTestId("editor-state-checkpoint-create").click();
    await createGateB.waitUntilEntered(1);
    expect(createGateB.enteredCount).toBe(1);

    createGateA.release();
    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(createGateB.enteredCount).toBe(1);
    expect(createGateB.released).toBe(false);
    expect(
      state.createLog.filter((c) => c.projectId === BIZ_B).length,
    ).toBe(0);

    // 折叠再展开：重置面板本地 busy/session；Hook B token 仍在
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toHaveCount(0);
    await expandPanel(page);
    await expect(
      page.getByTestId("editor-state-checkpoint-create"),
    ).toBeEnabled();

    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    expect(createGateB.enteredCount).toBe(1);
    expect(
      state.createLog.filter((c) => c.projectId === BIZ_B).length,
    ).toBe(0);

    createGateB.release();
    await expect
      .poll(
        () => state.createLog.filter((c) => c.projectId === BIZ_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
  });

  test("完整水合：restore 后下一 PUT 精确携带 qualify/toc/quote/commit", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedCheckpoint(state, BIZ_A);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-restore-0").click();
    await page.getByTestId("editor-state-checkpoint-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);

    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    await editContent(page, "biz", `${RESTORED_BIZ}\n水合后下一编辑`);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.parsedMarkdown).toBe(`${RESTORED_BIZ}\n水合后下一编辑`);
    expect(JSON.stringify(putBody.businessQualify)).toContain(BIZ_RESTORE_QUALIFY);
    expect(JSON.stringify(putBody.businessToc)).toContain(BIZ_RESTORE_TOC);
    expect(JSON.stringify(putBody.businessQuote)).toContain(BIZ_RESTORE_QUOTE);
    expect(JSON.stringify(putBody.businessQuote)).toContain(
      BIZ_RESTORE_QUOTE_NOTES,
    );
    expect(JSON.stringify(putBody.businessCommit)).toContain(BIZ_RESTORE_COMMIT);
  });

  test("API shape：create 元数据额外键固定失败且不显示成功", async ({ page }) => {
    const state = createProbeState("biz");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);
    state.createResponseOverride = {
      checkpointId: seedCheckpointId(70),
      stateVersion: state.projects[BIZ_A].stateVersion,
      snapshotBytes: 10,
      outlineNodeCount: 0,
      chapterCount: 0,
      createdAt: "2026-07-15T12:00:00.000Z",
      snapshot: "LEAK_BIZ_CREATE_SNAPSHOT",
    };
    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    const html = await page.content();
    expect(html).not.toContain("LEAK_BIZ_CREATE_SNAPSHOT");
  });

  for (const kind of ["missing", "blank", "invalid"] as const) {
    test(`create POST 响应 stateVersion ${kind}：forced PUT 成功、全量阻断、POST=1、两窗口零 PUT`, async ({
      page,
    }) => {
      const state = createProbeState("biz");
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "biz", BIZ_A);
      await expandPanel(page);

      const localText = `${BIZ_MD}\ncreate-sv-${kind}`;
      await editContent(page, "biz", localText);
      await page.clock.fastForward(debounceMs("biz") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBeGreaterThan(0);
      const putsBeforeCreate = state.putLog.length;

      const baseMeta = {
        checkpointId: seedCheckpointId(
          80 + (kind === "missing" ? 1 : kind === "blank" ? 2 : 3),
        ),
        snapshotBytes: 10,
        outlineNodeCount: 0,
        chapterCount: 0,
        createdAt: "2026-07-15T12:00:00.000Z",
      };
      if (kind === "missing") {
        state.createResponseOverride = { ...baseMeta };
      } else if (kind === "blank") {
        state.createResponseOverride = { ...baseMeta, stateVersion: "  " };
      } else {
        state.createResponseOverride = {
          ...baseMeta,
          stateVersion: "not-a-version",
        };
      }

      await page.getByTestId("editor-state-checkpoint-create").click();
      await expect
        .poll(() => state.createLog.length, { timeout: 10_000 })
        .toBe(1);
      expect(state.putLog.length).toBeGreaterThanOrEqual(putsBeforeCreate + 1);
      expect(state.createLog.length).toBe(1);

      await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
      await expect(page.getByTestId(conflictTestId("biz"))).toContainText(
        FULL_STATE_CONFLICT_MSG,
      );
      await expect(
        page.getByTestId("editor-state-checkpoint-status"),
      ).toContainText(MSG_CREATE_BLOCKED);
      expect(await readContent(page, "biz")).toBe(localText);

      const putsAfterBlock = state.putLog.length;
      await page.clock.fastForward(debounceMs("biz") + 100);
      await page.clock.fastForward(debounceMs("biz") + 100);
      expect(state.putLog.length).toBe(putsAfterBlock);
      expect(state.createLog.length).toBe(1);

      const html = await page.content();
      expect(html).not.toContain("not-a-version");
      expect(html).not.toContain(baseMeta.checkpointId);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("create POST HTTP 500 + 冒充专用 code：固定失败且不全量阻断（商务）", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    state.createMode = {
      kind: "http_error",
      status: 500,
      code: "checkpoint_create_state_version_invalid",
    };
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const localText = `${BIZ_MD}\ncreate-http-code-spoof`;
    await editContent(page, "biz", localText);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    const putsBeforeCreate = state.putLog.length;

    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect
      .poll(() => state.createLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.putLog.length).toBeGreaterThanOrEqual(putsBeforeCreate + 1);
    expect(state.createLog.length).toBe(1);
    expect(state.createLog[0].responseVersion).toBeNull();

    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_CREATE_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_OK);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_CREATE_BLOCKED);
    await expect(page.getByTestId(conflictTestId("biz"))).toHaveCount(0);
    expect(await readContent(page, "biz")).toBe(localText);

    const putsAfterFail = state.putLog.length;
    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfterFail);
    expect(state.createLog.length).toBe(1);

    const html = await page.content();
    expect(html).not.toContain("checkpoint_create_state_version_invalid");
    expect(html).not.toContain("create_http_error");
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("未知外网记录后中止；不请求详情", async ({ page }) => {
    const state = createProbeState("biz");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await openWorkspace(page, "biz", BIZ_A);
    await expandPanel(page);

    const before = state.externalHits.length;
    await page.evaluate(async () => {
      try {
        await fetch("https://example.invalid/p12bd2-probe");
      } catch {
        /* 预期失败 */
      }
    });
    await expect
      .poll(() => state.externalHits.length)
      .toBeGreaterThanOrEqual(before + 1);
    expect(state.detailLog.length).toBe(0);
    expect(state.externalHits.some((h) => h.includes("example.invalid"))).toBe(
      true,
    );
  });
});

// ---------------------------------------------------------------------------
// P12G 检查点展示名称
// ---------------------------------------------------------------------------
const MSG_NAME_OK = "已保存检查点名称";
const MSG_NAME_CLEARED = "已清除检查点名称";
const MSG_NAME_FAIL = "保存检查点名称失败，请稍后重试";
const MSG_NAME_SAVING = "保存名称中…";

test.describe("P12G 检查点展示名称", () => {
  test.describe.configure({ mode: "serial" });

  for (const mode of ["tech", "biz"] as const) {
    const ids = projectIds(mode);
    const projectId = ids.a;

    test(`${mode}：保存/覆盖/清除/取消；成功原位更新；零 list/restore/editor-state 旁路`, async ({
      page,
    }) => {
      const state = createProbeState(mode);
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      seedCheckpoint(state, projectId, 1);
      await openWorkspace(page, mode, projectId);
      await expandPanel(page);

      const listBefore = state.listLog.length;
      const editorGetBefore = state.editorGetLog.length;
      const restoreBefore = state.restoreLog.length;

      // 命名入口
      await page.getByTestId("editor-state-checkpoint-name-0").click();
      await expect(
        page.getByTestId("editor-state-checkpoint-name-input-0"),
      ).toBeVisible();
      // 仅输入：零请求
      await page
        .getByTestId("editor-state-checkpoint-name-input-0")
        .fill("投标前确认版");
      expect(state.nameLog.length).toBe(0);

      // 取消：零请求
      await page.getByTestId("editor-state-checkpoint-name-cancel-0").click();
      expect(state.nameLog.length).toBe(0);
      await expect(
        page.getByTestId("editor-state-checkpoint-name-input-0"),
      ).toHaveCount(0);

      // 保存
      await page.getByTestId("editor-state-checkpoint-name-0").click();
      await page
        .getByTestId("editor-state-checkpoint-name-input-0")
        .fill("投标前确认版");
      await page.getByTestId("editor-state-checkpoint-name-save-0").click();
      await expect
        .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
        .toBe(1);
      expect(state.nameLog[0].bodyKeys).toEqual(["displayName"]);
      expect(state.nameLog[0].displayName).toBe("投标前确认版");
      expect(state.nameLog[0].queryKeys).toEqual([]);
      await expect(
        page.getByTestId("editor-state-checkpoint-display-name-0"),
      ).toHaveText("投标前确认版");
      await expect(
        page.getByTestId("editor-state-checkpoint-status"),
      ).toContainText(MSG_NAME_OK);
      // 成功原位：无额外 list/restore/editor-state GET
      expect(state.listLog.length).toBe(listBefore);
      expect(state.restoreLog.length).toBe(restoreBefore);
      expect(state.editorGetLog.length).toBe(editorGetBefore);

      // 覆盖
      await page.getByTestId("editor-state-checkpoint-name-0").click();
      await page
        .getByTestId("editor-state-checkpoint-name-input-0")
        .fill("报价复核前");
      await page.getByTestId("editor-state-checkpoint-name-save-0").click();
      await expect
        .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
        .toBe(2);
      await expect(
        page.getByTestId("editor-state-checkpoint-display-name-0"),
      ).toHaveText("报价复核前");
      expect(state.listLog.length).toBe(listBefore);

      // 清除
      await page.getByTestId("editor-state-checkpoint-name-0").click();
      await page.getByTestId("editor-state-checkpoint-name-clear-0").click();
      await expect
        .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
        .toBe(3);
      expect(state.nameLog[2].displayName).toBeNull();
      await expect(
        page.getByTestId("editor-state-checkpoint-display-name-0"),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("editor-state-checkpoint-status"),
      ).toContainText(MSG_NAME_CLEARED);

      const html = await page.content();
      expect(html).not.toContain(state.checkpoints[projectId][0].checkpointId);
      expect(html).not.toContain(state.checkpoints[projectId][0].stateVersion);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("坏响应/失败保值：名称与草稿不丢；零重试", async ({ page }) => {
    const state = createProbeState("tech");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    seedCheckpoint(state, TECH_A, 1);
    state.checkpoints[TECH_A][0].displayName = "原名称";
    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("原名称");

    // 响应额外键 → 失败保值
    state.nameResponseOverride = { displayName: "新名", extra: 1 };
    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("新名");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await expect
      .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("原名称");
    // 草稿仍在输入框
    await expect(
      page.getByTestId("editor-state-checkpoint-name-input-0"),
    ).toHaveValue("新名");
    expect(state.nameLog.length).toBe(1);

    // HTTP 失败
    state.nameResponseOverride = null;
    state.nameMode = { kind: "http_error", status: 500 };
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("再试");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await expect
      .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("原名称");
    expect(state.nameLog.length).toBe(2);
  });

  test("双击单飞：仅一次 PATCH；在途互斥 disabled", async ({ page }) => {
    const state = createProbeState("tech");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    seedCheckpoint(state, TECH_A, 1);
    const hold = createHoldGate();
    state.nameMode = { kind: "hold", gate: hold, then: "ok" };
    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("单飞名");
    // 同一浏览器任务内连续两次 DOM click，禁止已 disabled 后 force 再点
    await page.evaluate(() => {
      const btn = document.querySelector(
        '[data-testid="editor-state-checkpoint-name-save-0"]',
      ) as HTMLButtonElement | null;
      if (!btn) throw new Error("save button missing");
      btn.click();
      btn.click();
    });
    await hold.waitUntilEntered(1);
    // PATCH arrived/complete 精确 1
    expect(state.nameLog.length).toBe(1);
    expect(state.nameCompleteLog.length).toBe(0);
    // 在途：保存/创建/刷新真实 disabled；命名态不渲染恢复按钮（互斥）
    await expect(
      page.getByTestId("editor-state-checkpoint-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-create"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-restore-0"),
    ).toHaveCount(0);
    hold.release();
    await expect
      .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.nameLog.length).toBe(1);
    expect(state.nameCompleteLog.length).toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("单飞名");
  });

  test("A→B 迟到 success 不污染 B；旧 finally 不解锁 B", async ({ page }) => {
    const state = createProbeState("tech");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    seedCheckpoint(state, TECH_A, 1);
    seedCheckpoint(state, TECH_B, 2);
    const holdA = createHoldGate();
    const holdB = createHoldGate();
    state.nameModeByProject[TECH_A] = {
      kind: "hold",
      gate: holdA,
      then: "ok",
    };
    state.nameModeByProject[TECH_B] = {
      kind: "hold",
      gate: holdB,
      then: "ok",
    };
    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("A名称");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await holdA.waitUntilEntered(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_A).length,
    ).toBe(1);

    // 切到 B：B 也 hold 且 arrived 精确 1 后再释放 A
    await openWorkspace(page, "tech", TECH_B);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("B名称");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await holdB.waitUntilEntered(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    expect(
      state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-name-input-0"),
    ).toHaveValue("B名称");
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_SAVING);

    // 释放 A：B 仍 disabled、草稿/消息不被 A 污染、B gate 未释放、B arrived 仍 1
    holdA.release();
    await expect
      .poll(
        () =>
          state.nameCompleteLog.filter((x) => x.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(holdB.released).toBe(false);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    expect(
      state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-name-input-0"),
    ).toHaveValue("B名称");
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_SAVING);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_NAME_OK);

    // 释放 B 并精确完成
    holdB.release();
    await expect
      .poll(
        () =>
          state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_A).length,
    ).toBe(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("B名称");
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_OK);
  });

  test("A→B 迟到 failure 不污染 B 消息/busy", async ({ page }) => {
    const state = createProbeState("tech");
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    seedCheckpoint(state, TECH_A, 1);
    seedCheckpoint(state, TECH_B, 2);
    const holdA = createHoldGate();
    const holdB = createHoldGate();
    state.nameModeByProject[TECH_A] = {
      kind: "hold",
      gate: holdA,
      then: "http_error",
      status: 500,
    };
    state.nameModeByProject[TECH_B] = {
      kind: "hold",
      gate: holdB,
      then: "ok",
    };
    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("A失败名");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await holdA.waitUntilEntered(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_A).length,
    ).toBe(1);

    await openWorkspace(page, "tech", TECH_B);
    await expandPanel(page);
    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill("B成功名");
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await holdB.waitUntilEntered(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    expect(
      state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_SAVING);

    // 释放 A failure：B 仍 disabled，不被 catch/finally 解锁或污染
    holdA.release();
    await expect
      .poll(
        () =>
          state.nameCompleteLog.filter((x) => x.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(holdB.released).toBe(false);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    expect(
      state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-checkpoint-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-checkpoint-name-input-0"),
    ).toHaveValue("B成功名");
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_SAVING);
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).not.toContainText(MSG_NAME_FAIL);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveCount(0);

    holdB.release();
    await expect
      .poll(
        () =>
          state.nameCompleteLog.filter((x) => x.projectId === TECH_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_A).length,
    ).toBe(1);
    expect(
      state.nameLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(1);
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText("B成功名");
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toContainText(MSG_NAME_OK);
  });

  test("名称不进 URL/storage/Cookie/console；仅同源 body 与 React 文本", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    seedCheckpoint(state, TECH_A, 1);
    await openWorkspace(page, "tech", TECH_A);
    await expandPanel(page);

    const secretName = "SECRET_P12G_NAME_LEAK";
    await page.getByTestId("editor-state-checkpoint-name-0").click();
    await page
      .getByTestId("editor-state-checkpoint-name-input-0")
      .fill(secretName);
    await page.getByTestId("editor-state-checkpoint-name-save-0").click();
    await expect
      .poll(() => state.nameCompleteLog.length, { timeout: 10_000 })
      .toBe(1);

    expect(page.url()).not.toContain(secretName);
    expect(page.url()).not.toContain(
      state.checkpoints[TECH_A][0].checkpointId,
    );
    const storage = await page.evaluate(() => ({
      ls: JSON.stringify(localStorage),
      ss: JSON.stringify(sessionStorage),
      cookie: document.cookie,
    }));
    expect(storage.ls).not.toContain(secretName);
    expect(storage.ss).not.toContain(secretName);
    expect(storage.cookie).not.toContain(secretName);
    // React 文本可见
    await expect(
      page.getByTestId("editor-state-checkpoint-display-name-0"),
    ).toHaveText(secretName);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
  });

  test("P12G 静态守卫：禁止 force 与宽泛计数", () => {
    const src = fs.readFileSync(
      path.join(process.cwd(), "e2e/editor-state-checkpoint-restore.spec.ts"),
      "utf8",
    );
    const marker = 'test.describe("P12G 检查点展示名称"';
    const start = src.indexOf(marker);
    expect(start).not.toBe(-1);
    const endMarker = 'test("契约常量格式"';
    const end = src.indexOf(endMarker, start);
    expect(end).not.toBe(-1);
    let block = src.slice(start, end);
    // 剔除本静态守卫用例，避免自引用
    block = block.replace(
      /test\("P12G 静态守卫：禁止 force 与宽泛计数"[\s\S]*?\n  \}\);\n/,
      "",
    );
    expect(block).not.toMatch(/force\s*:\s*true/);
    expect(block).not.toMatch(/toBeGreaterThanOrEqual/);
  });
});

test("契约常量格式", () => {
  expect(seedStateVersion(1)).toMatch(STATE_VERSION_RE);
  expect(seedCheckpointId(1)).toMatch(CHECKPOINT_ID_RE);
  // 辅助函数存在性（避免 tree-shake 误删）
  expect(projectIds("tech").a).toBe(TECH_A);
  expect(projectIds("biz").a).toBe(BIZ_A);
});
