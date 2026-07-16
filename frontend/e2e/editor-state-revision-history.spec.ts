/**
 * 模块：P12C-C3 双工作区修订历史前端 E2E
 * 用途：技术标/商务标证明默认折叠零请求、按需列表/摘要、二次确认 restore、
 *       执行时 expected、唯一 editor-state GET、失败阻断、迟到隔离与数据最小化。
 * 对接：Playwright chromium headless workers=1 retries=0；route 探针。
 * 二次开发：禁止固定 sleep、.or(...)、>=1 冒充、宽泛状态码、route fallback 假成功。
 */
import {
  expect,
  test,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p12cc3_tech_a";
const TECH_B = "proj_e2e_p12cc3_tech_b";
const BIZ_A = "proj_e2e_p12cc3_biz_a";
const BIZ_B = "proj_e2e_p12cc3_biz_b";

const TECH_OVERVIEW = "P12C_C3_TECH_SERVER_OVERVIEW";
const TECH_OVERVIEW_B = "P12C_C3_TECH_SERVER_OVERVIEW_B";
const BIZ_MD = "P12C_C3_BIZ_SERVER_MARKDOWN";
const BIZ_MD_B = "P12C_C3_BIZ_SERVER_MARKDOWN_B";
const RESTORED_TECH = "P12C_C3_TECH_RESTORED_OVERVIEW";
const RESTORED_BIZ = "P12C_C3_BIZ_RESTORED_MARKDOWN";

const TECH_RESTORE_OUTLINE_TITLE = "P12C_C3_TECH_RESTORE_OUTLINE";
const TECH_RESTORE_CHAPTER_BODY = "P12C_C3_TECH_RESTORE_CHAPTER_BODY";
const TECH_RESTORE_FACT = "P12C_C3_TECH_RESTORE_FACT";
const TECH_RESTORE_GUIDANCE = "P12C_C3_TECH_RESTORE_GUIDANCE_FOCUS";
const TECH_RESTORE_MATRIX_TEXT = "P12C_C3_TECH_RESTORE_MATRIX_REQ";
const TECH_RESTORE_MATRIX_VERSION = "rmv_p12cc3_restored_matrix_v1";
const TECH_RESTORE_MODE = "FREE";

const BIZ_RESTORE_QUALIFY = "P12C_C3_BIZ_RESTORE_QUALIFY";
const BIZ_RESTORE_TOC = "P12C_C3_BIZ_RESTORE_TOC";
const BIZ_RESTORE_QUOTE = "P12C_C3_BIZ_RESTORE_QUOTE_ROW";
const BIZ_RESTORE_QUOTE_NOTES = "P12C_C3_BIZ_RESTORE_QUOTE_NOTES";
const BIZ_RESTORE_COMMIT = "P12C_C3_BIZ_RESTORE_COMMIT";

const SNAPSHOT_BODY_LEAK = "P12C_C3_SNAPSHOT_BODY_MUST_NOT_LEAK";

const MSG_LIST_FAIL = "修订历史加载失败，请稍后重试";
const MSG_DETAIL_FAIL = "修订摘要加载失败，请稍后重试";
const MSG_RESTORE_OK = "已恢复到所选修订";
const MSG_RESTORE_BLOCKED =
  "当前无法恢复，请先处理版本冲突或重新载入";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
/** 检查点 create 被共享写令牌拒绝时的固定状态文案 */
const MSG_CHECKPOINT_CREATE_FAIL = "保存检查点失败，请确认后重试";
const RESTORE_CONFIRM =
  "服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。";

const FULL_STATE_CONFLICT_MSG =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

const SOURCE_LABELS: Record<string, string> = {
  browser_put: "浏览器保存",
  task: "任务写入",
  revise: "智能修订",
  callback: "解析回传",
  local_parser: "本地解析",
  content_fuse_apply: "内容融合应用",
  content_fuse_consume: "内容融合消费",
  checkpoint_restore: "检查点恢复",
  revision_restore: "修订恢复",
};

const NINE_SOURCES = Object.keys(SOURCE_LABELS);

const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

const TECH_DEBOUNCE_MS = 800;
const BIZ_DEBOUNCE_MS = 600;

type Mode = "tech" | "biz";

type RevisionMeta = {
  revisionId: string;
  stateVersion: string;
  snapshotBytes: number;
  sourceKind: string;
  createdAt: string;
};

type RevisionDetail = RevisionMeta & {
  snapshot: Record<string, unknown>;
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
  analysis: { overview: string; techRequirements?: string[] };
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
  readonly enteredCount: number;
  waitUntilEntered: (min?: number) => Promise<void>;
};

type RestoreMode =
  | { kind: "ok" }
  | { kind: "abort" }
  | { kind: "http_error"; status: number }
  | { kind: "not_found" }
  | { kind: "missing_version" }
  | { kind: "invalid_version" }
  | { kind: "blank_version" }
  | { kind: "extra_field" }
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

type ListMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type DetailMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };

type ProbeState = {
  mode: Mode;
  projects: Record<string, EditorState>;
  revisions: Record<string, RevisionMeta[]>;
  details: Record<string, RevisionDetail>;
  versionSeq: number;
  revisionSeq: number;
  checkpointSeq: number;
  putLog: Array<{
    projectId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  restoreLog: Array<{
    projectId: string;
    revisionId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  listLog: string[];
  /** list 响应已 fulfill（await json 返回后）——用于证明迟到响应真正完成 */
  listCompleteLog: string[];
  detailLog: Array<{ projectId: string; revisionId: string }>;
  /** detail 响应已 fulfill（await json 返回后）——用于证明迟到响应真正完成 */
  detailCompleteLog: Array<{ projectId: string; revisionId: string }>;
  editorGetLog: Array<{ projectId: string; path: string }>;
  putMode: PutMode;
  restoreMode: RestoreMode;
  listMode: ListMode;
  detailMode: DetailMode;
  restoreModeByProject: Record<string, RestoreMode>;
  listModeByProject: Record<string, ListMode>;
  detailModeByProject: Record<string, DetailMode>;
  detailModeByRevisionId: Record<string, DetailMode>;
  listResponseOverride: unknown | null;
  detailResponseOverride: unknown | null;
  restoreResponseOverride: unknown | null;
  nextEditorGetFail: boolean;
  restoreArrivedWhilePutHeld: boolean;
  externalHits: string[];
  forbiddenHits: string[];
  checkpointListLog: string[];
  /** 检查点 create POST 探针（互斥/令牌验证） */
  checkpointCreateLog: string[];
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedRevisionId(n: number): string {
  return `esr_${n.toString(16).padStart(32, "0")}`;
}

function seedCheckpointId(n: number): string {
  return `escp_${n.toString(16).padStart(32, "0")}`;
}

function allocateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

function allocateRevisionId(state: ProbeState): string {
  state.revisionSeq += 1;
  return seedRevisionId(state.revisionSeq);
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

function canonicalSnapshot(editor: EditorState): Record<string, unknown> {
  return {
    outline: editor.outline,
    chapters: editor.chapters,
    facts: editor.facts,
    mode: editor.mode,
    analysis: editor.analysis,
    responseMatrix: editor.responseMatrix,
    guidance: editor.guidance,
    parsedMarkdown: editor.parsedMarkdown,
    businessQualify: editor.businessQualify,
    businessToc: editor.businessToc,
    businessQuote: editor.businessQuote,
    businessCommit: editor.businessCommit,
    analysisOverview: editor.analysisOverview,
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
    revisions: { [aId]: [], [bId]: [] },
    details: {},
    versionSeq: versionSeq + 1,
    revisionSeq: 0,
    checkpointSeq: 0,
    putLog: [],
    restoreLog: [],
    listLog: [],
    listCompleteLog: [],
    detailLog: [],
    detailCompleteLog: [],
    editorGetLog: [],
    putMode: { kind: "ok" },
    restoreMode: { kind: "ok" },
    listMode: { kind: "ok" },
    detailMode: { kind: "ok" },
    restoreModeByProject: {},
    listModeByProject: {},
    detailModeByProject: {},
    detailModeByRevisionId: {},
    listResponseOverride: null,
    detailResponseOverride: null,
    restoreResponseOverride: null,
    nextEditorGetFail: false,
    restoreArrivedWhilePutHeld: false,
    externalHits: [],
    forbiddenHits: [],
    checkpointListLog: [],
    checkpointCreateLog: [],
  };
}

function resolveRestoreMode(state: ProbeState, projectId: string): RestoreMode {
  return state.restoreModeByProject[projectId] ?? state.restoreMode;
}

function resolveListMode(state: ProbeState, projectId: string): ListMode {
  return state.listModeByProject[projectId] ?? state.listMode;
}

function resolveDetailMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): DetailMode {
  return (
    state.detailModeByRevisionId[revisionId] ??
    state.detailModeByProject[projectId] ??
    state.detailMode
  );
}

/** 构造超过 MAX_COUNT_DEPTH 的大纲树，触发固定摘要失败 */
function makeOverDepthOutline(depth: number): unknown {
  let node: Record<string, unknown> = {
    id: "leaf",
    title: "deep-leaf",
    children: [],
  };
  for (let i = 0; i < depth; i++) {
    node = { id: `d${i}`, title: "deep", children: [node] };
  }
  return [node];
}

function techRestoredEditor(
  prev: EditorState,
  restoredVersion: string,
): EditorState {
  const matrixSourceKey = `requirement:${TECH_RESTORE_MATRIX_TEXT.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
  return {
    ...prev,
    analysisOverview: RESTORED_TECH,
    analysis: {
      overview: RESTORED_TECH,
      techRequirements: [TECH_RESTORE_MATRIX_TEXT],
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

function bizRestoredEditor(
  prev: EditorState,
  restoredVersion: string,
): EditorState {
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
    updatedAt: "2026-07-16T00:00:00.000Z",
    technicalPlanStep: 1,
    wordCount: 0,
    kind: mode === "tech" ? "technical" : "business",
  };
}

async function installRuntimeErrorGuards(page: Page) {
  const pageErrors: string[] = [];
  const consoleLogs: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(String(err?.message || err));
  });
  page.on("console", (msg) => {
    consoleLogs.push(msg.text());
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
      const g = window as unknown as { __p12cc3Unhandled?: string[] };
      g.__p12cc3Unhandled = g.__p12cc3Unhandled || [];
      g.__p12cc3Unhandled.push(text);
    });
  });
  return {
    pageErrors,
    consoleLogs,
    async readUnhandled(): Promise<string[]> {
      return page.evaluate(() => {
        return (
          (window as unknown as { __p12cc3Unhandled?: string[] })
            .__p12cc3Unhandled || []
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
        projectMeta(aId, state.mode, "C3-A"),
        projectMeta(bId, state.mode, "C3-B"),
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
      await json(
        route,
        projectMeta(pid, state.mode, pid === aId ? "C3-A" : "C3-B"),
      );
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

    // 检查点面板仍存在：允许 list，记录但不实现 create/restore 业务
    const cpListMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/?$/,
    );
    if (cpListMatch) {
      const pid = cpListMatch[1];
      if (method === "GET") {
        state.checkpointListLog.push(pid);
        await json(route, { items: [] });
        return;
      }
      if (method === "POST") {
        state.checkpointCreateLog.push(pid);
        await json(route, {
          checkpointId: allocateCheckpointId(state),
          stateVersion: state.projects[pid]?.stateVersion || seedStateVersion(1),
          snapshotBytes: 10,
          outlineNodeCount: 0,
          chapterCount: 0,
          createdAt: new Date().toISOString(),
        }, 201);
        return;
      }
    }

    const revListMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/?$/,
    );
    if (revListMatch) {
      const pid = revListMatch[1];
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
          state.listCompleteLog.push(pid);
          return;
        }
        await json(route, { items: state.revisions[pid] || [] });
        state.listCompleteLog.push(pid);
        return;
      }
      state.forbiddenHits.push(`${method} ${path}`);
      await json(route, { detail: "method_not_allowed" }, 405);
      return;
    }

    const revRestoreMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/restore\/?$/,
    );
    if (revRestoreMatch && method === "POST") {
      const pid = revRestoreMatch[1];
      const revisionId = revRestoreMatch[2];
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
          revisionId,
          body,
          responseVersion: null,
        });
        await route.abort("failed");
        return;
      }
      if (mode.kind === "http_error") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(route, { detail: { code: "server_error", message: "x" } }, mode.status);
        return;
      }
      if (mode.kind === "not_found") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        return;
      }
      if (mode.kind === "conflict") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
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
      if (mode.kind === "missing_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "invalid_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: "not-a-version",
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: "not-a-version",
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "blank_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: "  ",
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: "  ",
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "extra_field") {
        const restoredVersion = allocateVersion(state);
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: restoredVersion,
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: restoredVersion,
          restoredAt: new Date().toISOString(),
          leak: "EXTRA_RESTORE_FIELD",
        });
        return;
      }

      const targetMeta = (state.revisions[pid] || []).find(
        (r) => r.revisionId === revisionId,
      );
      const restoredVersion =
        targetMeta?.stateVersion || allocateVersion(state);
      const prev = state.projects[pid];
      state.projects[pid] =
        state.mode === "tech"
          ? techRestoredEditor(prev, restoredVersion)
          : bizRestoredEditor(prev, restoredVersion);

      // 恢复成功后可在列表追加 revision_restore 时间点
      const newMeta: RevisionMeta = {
        revisionId: allocateRevisionId(state),
        stateVersion: restoredVersion,
        snapshotBytes: 256,
        sourceKind: "revision_restore",
        createdAt: new Date().toISOString(),
      };
      state.revisions[pid] = [newMeta, ...(state.revisions[pid] || [])].slice(
        0,
        10,
      );

      state.restoreLog.push({
        projectId: pid,
        revisionId,
        body,
        responseVersion: restoredVersion,
      });
      if (state.restoreResponseOverride != null) {
        await json(route, state.restoreResponseOverride);
        return;
      }
      await json(route, {
        safetyCheckpointId: safetyId,
        stateVersion: restoredVersion,
        restoredAt: new Date().toISOString(),
      });
      return;
    }

    const revDetailMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/?$/,
    );
    if (revDetailMatch && method === "GET") {
      const pid = revDetailMatch[1];
      const revisionId = revDetailMatch[2];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      state.detailLog.push({ projectId: pid, revisionId });
      const detailMode = resolveDetailMode(state, pid, revisionId);
      if (detailMode.kind === "hold") {
        await detailMode.gate.wait();
      }
      if (state.detailResponseOverride != null) {
        await json(route, state.detailResponseOverride);
        state.detailCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const detail = state.details[revisionId];
      if (!detail) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        state.detailCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      await json(route, detail);
      state.detailCompleteLog.push({ projectId: pid, revisionId });
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
  await expect(page.getByTestId("editor-state-revision-panel")).toBeVisible();
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

async function expandRevisionPanel(page: Page) {
  const toggle = page.getByTestId("editor-state-revision-toggle");
  const body = page.getByTestId("editor-state-revision-body");
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

async function assertNoIdLeak(
  page: Page,
  state: ProbeState,
  consoleLogs: string[] = [],
) {
  const html = await page.content();
  for (const list of Object.values(state.revisions)) {
    for (const item of list) {
      expect(html).not.toContain(item.revisionId);
      expect(html).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(html).not.toContain(editor.stateVersion);
  }
  expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
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
  expect(blob).not.toMatch(/esr_[0-9a-f]{32}/);
  expect(blob).not.toMatch(/esv_[0-9a-f]{32}/);
  expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);

  // console 不得出现 revisionId / stateVersion / snapshot 正文探针
  const consoleBlob = consoleLogs.join("\n");
  for (const list of Object.values(state.revisions)) {
    for (const item of list) {
      expect(consoleBlob).not.toContain(item.revisionId);
      expect(consoleBlob).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(consoleBlob).not.toContain(editor.stateVersion);
  }
  expect(consoleBlob).not.toMatch(/esr_[0-9a-f]{32}/);
  expect(consoleBlob).not.toMatch(/esv_[0-9a-f]{32}/);
  expect(consoleBlob).not.toContain(SNAPSHOT_BODY_LEAK);
  expect(consoleBlob).not.toMatch(/\bsnapshot\b/i);
}

function debounceMs(mode: Mode) {
  return mode === "tech" ? TECH_DEBOUNCE_MS : BIZ_DEBOUNCE_MS;
}

function seedRevisions(
  state: ProbeState,
  projectId: string,
  count = 1,
  sources?: string[],
): RevisionMeta[] {
  const list: RevisionMeta[] = [];
  for (let i = 0; i < count; i++) {
    const n = state.revisionSeq + 1 + i;
    const sourceKind =
      sources?.[i] || NINE_SOURCES[i % NINE_SOURCES.length] || "browser_put";
    const meta: RevisionMeta = {
      revisionId: seedRevisionId(n),
      stateVersion: seedStateVersion(20 + n),
      snapshotBytes: 100 + i,
      sourceKind,
      createdAt: new Date(Date.UTC(2026, 6, 16, 10, i, 0)).toISOString(),
    };
    const editor = state.projects[projectId];
    const snap = canonicalSnapshot(editor);
    // 故意塞入正文泄漏探针：前端不得渲染
    if (typeof snap.parsedMarkdown === "string") {
      snap.parsedMarkdown = `${snap.parsedMarkdown}\n${SNAPSHOT_BODY_LEAK}`;
    } else {
      snap.parsedMarkdown = SNAPSHOT_BODY_LEAK;
    }
    if (typeof snap.analysisOverview === "string") {
      snap.analysisOverview = `${snap.analysisOverview}\n${SNAPSHOT_BODY_LEAK}`;
    }
    state.details[meta.revisionId] = { ...meta, snapshot: snap };
    list.push(meta);
  }
  state.revisionSeq += count;
  state.revisions[projectId] = list;
  return list;
}

async function assertNoRestoreWhilePutHeld(
  page: Page,
  mode: Mode,
  state: ProbeState,
) {
  await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
  await page.clock.fastForward(debounceMs(mode) + 100);
  await page.clock.fastForward(debounceMs(mode) + 100);
  expect(state.restoreLog.length).toBe(0);
  expect(state.restoreArrivedWhilePutHeld).toBe(false);
}

// ---------------------------------------------------------------------------
// 技术标
// ---------------------------------------------------------------------------
test.describe("P12C-C3 技术标修订历史", () => {
  test.describe.configure({ mode: "serial" });

  test("默认折叠零 revision 请求；展开一次 list；刷新精确 +1；九来源标签；最多 10 条", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 10, NINE_SOURCES.concat(["browser_put"]));
    // 第 11 项不在服务端列表（探针只返回 10）
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    // 默认折叠：零 revision list/detail/restore
    expect(state.listLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);

    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
      const label = SOURCE_LABELS[state.revisions[TECH_A][i].sourceKind];
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText(label);
      // 不得展示内部 source 原值
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).not.toHaveText(state.revisions[TECH_A][i].sourceKind);
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBe(2);

    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("详情按需加载六项摘要；不展示正文；折叠/切项清空；严格 shape 固定失败", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listLog.length, { timeout: 10_000 })
      .toBe(1);

    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();
    const summaryText = await page
      .getByTestId("editor-state-revision-summary-body-0")
      .innerText();
    expect(summaryText).toMatch(/大纲/);
    expect(summaryText).toMatch(/章节/);
    expect(summaryText).toMatch(/事实/);
    expect(summaryText).toMatch(/矩阵/);
    expect(summaryText).toMatch(/商务/);
    expect(summaryText).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(summaryText).not.toContain(state.revisions[TECH_A][0].revisionId);

    // 同一时刻只展开一项：点第二项后第一项摘要消失
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    // 折叠清空
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    const baseMeta = state.revisions[TECH_A][0];
    const baseDetail = state.details[baseMeta.revisionId];

    // 元数据错配：详情 revisionId 与列表不一致（重展后无选中，直接加载）
    state.detailResponseOverride = {
      ...baseDetail,
      revisionId: seedRevisionId(999),
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    let html = await page.content();
    expect(html).not.toContain(seedRevisionId(999));
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    // 额外顶层键（再次点击清空 → 再点加载）
    state.detailResponseOverride = {
      ...baseDetail,
      leakExtra: "DETAIL_EXTRA_KEY",
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(4);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain("DETAIL_EXTRA_KEY");
    expect(html).not.toContain(baseMeta.revisionId);

    // 缺 13 键之一
    const missingSnap = { ...baseDetail.snapshot };
    delete missingSnap.outline;
    state.detailResponseOverride = {
      ...baseDetail,
      snapshot: missingSnap,
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(5);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain(baseMeta.revisionId);
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);

    // 非法快照 + 超过深度 → 固定脱敏失败
    state.detailResponseOverride = {
      ...baseDetail,
      snapshot: {
        ...baseDetail.snapshot,
        outline: makeOverDepthOutline(40),
      },
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(6);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain(baseMeta.revisionId);
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(html).not.toContain("deep-leaf");
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("确认前 POST=0；PUT 挂起时 restore=0；释放后 expected=PUT 响应版本", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 1);
    const targetRevisionId = seeded[0].revisionId;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => state.listLog.length, { timeout: 10_000 }).toBe(1);

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "tech", `${TECH_OVERVIEW}\n挂起后再恢复`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toContainText(RESTORE_CONFIRM);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await assertNoRestoreWhilePutHeld(page, "tech", state);

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    const putVersion = state.putLog[0].responseVersion;
    expect(putVersion).toMatch(STATE_VERSION_RE);
    // 必须与确认前内存目标一致；禁止与恢复后新追加的列表头比较
    expect(state.restoreLog[0].revisionId).toBe(targetRevisionId);
    expect(state.restoreLog[0].body).toEqual({
      expectedStateVersion: putVersion,
    });
    expect(Object.keys(state.restoreLog[0].body).sort()).toEqual([
      "expectedStateVersion",
    ]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("成功恢复：POST=1、editor-state GET=1、list 额外 1；完整水合；两窗口零 PUT；下一编辑 +1 PUT", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => state.listLog.length, { timeout: 10_000 }).toBe(1);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;
    const listBefore = state.listLog.filter((p) => p === TECH_A).length;

    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog[0].revisionId).toBe(seeded[0].revisionId);

    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === TECH_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);

    await expect
      .poll(
        () => state.listLog.filter((p) => p === TECH_A).length - listBefore,
        { timeout: 10_000 },
      )
      .toBe(1);

    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toContainText(MSG_RESTORE_OK);

    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    await editContent(page, "tech", `${RESTORED_TECH}\n用户下一编辑`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.analysisOverview).toBe(`${RESTORED_TECH}\n用户下一编辑`);
    expect(JSON.stringify(putBody.outline)).toContain(
      TECH_RESTORE_OUTLINE_TITLE,
    );
    expect(JSON.stringify(putBody.chapters)).toContain(
      TECH_RESTORE_CHAPTER_BODY,
    );
    expect(JSON.stringify(putBody.facts)).toContain(TECH_RESTORE_FACT);
    expect(JSON.stringify(putBody.guidance)).toContain(TECH_RESTORE_GUIDANCE);
    expect(putBody.mode).toBe(TECH_RESTORE_MODE);
    expect(JSON.stringify(putBody.responseMatrix)).toContain(
      TECH_RESTORE_MATRIX_TEXT,
    );
    expect(putBody.responseMatrixVersion).toBe(TECH_RESTORE_MATRIX_VERSION);
    await assertNoIdLeak(page, state, []);
  });

  test("双击确认 restore POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    const confirm = page.getByTestId(
      "editor-state-revision-confirm-restore-0",
    );
    await Promise.all([confirm.click(), confirm.click()]);
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
  });

  test("检查点与修订恢复互斥：同令牌只产生一个版本化写", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    const restoreGate = createHoldGate();
    state.restoreMode = { kind: "gate", gate: restoreGate, then: "ok" };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    // 展开修订并启动 restore（挂起）
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGate.waitUntilEntered(1);
    expect(state.restoreLog.length).toBe(0);

    const putsWhileHeld = state.putLog.length;
    const cpCreateBefore = state.checkpointCreateLog.length;

    // 真实点击检查点 create（无 force）；gate 仍挂起时必须先看到失败状态，再断言无 POST/PUT
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toBeVisible();
    await page.getByTestId("editor-state-checkpoint-create").click();
    // 无条件等待精确失败文案（证明前端已同步拒绝，而非异步排队后补发）
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveText(MSG_CHECKPOINT_CREATE_FAIL, { timeout: 10_000 });
    expect(state.checkpointCreateLog.length).toBe(cpCreateBefore);
    expect(state.putLog.length).toBe(putsWhileHeld);
    expect(state.restoreLog.length).toBe(0);

    restoreGate.release();
    state.restoreMode = { kind: "ok" };
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
    // 释放 revision gate 并完成后 create 仍不得补发
    expect(state.checkpointCreateLog.length).toBe(cpCreateBefore);
  });

  for (const kind of [
    "abort",
    "not_found",
    "http_error",
    "missing_version",
    "invalid_version",
    "blank_version",
    "extra_field",
    "conflict",
  ] as const) {
    test(`restore ${kind}：保留本地、POST=1、零自动重试、阻断`, async ({
      page,
    }) => {
      const state = createProbeState("tech");
      seedRevisions(state, TECH_A, 1);
      if (kind === "http_error") {
        state.restoreMode = { kind: "http_error", status: 500 };
      } else {
        state.restoreMode = { kind };
      }
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "tech", TECH_A);
      await expandRevisionPanel(page);

      const localText = `${TECH_OVERVIEW}\n保留-${kind}`;
      await editContent(page, "tech", localText);
      await page.clock.fastForward(debounceMs("tech") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBe(1);
      const putsAfterEdit = state.putLog.length;

      await page.getByTestId("editor-state-revision-restore-0").click();
      await page
        .getByTestId("editor-state-revision-confirm-restore-0")
        .click();

      await expect
        .poll(() => state.restoreLog.length, { timeout: 10_000 })
        .toBe(1);

      await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
      await expect(page.getByTestId(conflictTestId("tech"))).toContainText(
        FULL_STATE_CONFLICT_MSG,
      );
      expect(await readContent(page, "tech")).toBe(localText);

      // POST 失败路径：无条件固定 blocked 文案（不得 reload_failed）
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).toHaveText(MSG_RESTORE_BLOCKED);
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).not.toContainText(MSG_RESTORE_RELOAD_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).not.toContainText(state.revisions[TECH_A][0].revisionId);

      // 确认态消失；恢复按钮无条件 disabled；零自动重试
      await expect(
        page.getByTestId("editor-state-revision-confirm-0"),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("editor-state-revision-restore-0"),
      ).toBeDisabled();

      await page.clock.fastForward(debounceMs("tech") + 100);
      await page.clock.fastForward(debounceMs("tech") + 100);
      expect(state.restoreLog.length).toBe(1);
      expect(state.putLog.length).toBe(putsAfterEdit);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("POST 成功 + GET 失败：业务完成提示；revision POST=1；保持阻断", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);

    state.nextEditorGetFail = true;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);

    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toContainText(MSG_RESTORE_RELOAD_FAIL);
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

  test("迟到 list：折叠后释放不得污染", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    const listGate = createHoldGate();
    state.listMode = { kind: "hold", gate: listGate };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await page.getByTestId("editor-state-revision-toggle").click();
    await listGate.waitUntilEntered(1);
    expect(state.listLog.filter((p) => p === TECH_A).length).toBe(1);
    // 挂起期间尚未 fulfill
    expect(state.listCompleteLog.filter((p) => p === TECH_A).length).toBe(0);

    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    listGate.release();
    state.listMode = { kind: "ok" };
    // 必须等迟到 list 真正 fulfill 后再断言内容未污染（不能只 poll arrived listLog）
    await expect
      .poll(
        () => state.listCompleteLog.filter((p) => p === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listLog.filter((p) => p === TECH_A).length, {
        timeout: 10_000,
      })
      .toBe(2);
    await expect
      .poll(
        () => state.listCompleteLog.filter((p) => p === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW);
  });

  test("A→B 迟到 restore：双挂起不污染；旧 finally 不误清 B 令牌", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    seedRevisions(state, TECH_B, 1, ["task"]);
    const restoreGateA = createHoldGate();
    const restoreGateB = createHoldGate();
    state.restoreModeByProject[TECH_A] = {
      kind: "gate",
      gate: restoreGateA,
      then: "ok",
    };
    state.restoreModeByProject[TECH_B] = {
      kind: "gate",
      gate: restoreGateB,
      then: "ok",
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGateA.waitUntilEntered(1);

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGateB.waitUntilEntered(1);

    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_B,
    ).length;
    const listBBefore = state.listLog.filter((p) => p === TECH_B).length;
    const cpBefore = state.checkpointCreateLog.length;
    const putsBefore = state.putLog.length;

    // 释放 A：不得污染 B；B 令牌仍有效（真实 create 必须 POST=0）
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
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_B).length,
    ).toBe(0);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_B).length,
    ).toBe(getsBBefore);
    expect(state.listLog.filter((p) => p === TECH_B).length).toBe(listBBefore);

    // B restore 仍挂起：点击 create 必须先见固定失败文案，再断言无 POST/额外 PUT
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toBeVisible();
    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveText(MSG_CHECKPOINT_CREATE_FAIL, { timeout: 10_000 });
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.putLog.length).toBe(putsBefore);

    // 释放 B：A/B revision POST 各精确 1；完成后 create 仍不得队列后补发
    restoreGateB.release();
    delete state.restoreModeByProject[TECH_B];
    await expect
      .poll(
        () => state.restoreLog.filter((r) => r.projectId === TECH_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_A).length,
    ).toBe(1);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_B).length,
    ).toBe(1);
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.putLog.length).toBe(putsBefore);
  });

  test("迟到 detail：A 挂起点 B 成功后释放 A 不覆盖；项目切换隔离", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    seedRevisions(state, TECH_B, 1, ["revise"]);
    const gateA0 = createHoldGate();
    const gateProjA = createHoldGate();
    state.detailModeByRevisionId[state.revisions[TECH_A][0].revisionId] = {
      kind: "hold",
      gate: gateA0,
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => state.listLog.length, { timeout: 10_000 }).toBe(1);

    // 详情 A0 挂起 → 点 B(索引1) 摘要成功 → 释放 A0 不得覆盖
    const revA0 = state.revisions[TECH_A][0].revisionId;
    const revA1 = state.revisions[TECH_A][1].revisionId;
    await page.getByTestId("editor-state-revision-summary-0").click();
    await gateA0.waitUntilEntered(1);
    expect(
      state.detailCompleteLog.filter((d) => d.revisionId === revA0).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-summary-0"),
    ).toContainText("加载摘要");

    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);

    gateA0.release();
    delete state.detailModeByRevisionId[revA0];
    // 等待 A0 真正 fulfill（completion），再断言 B 摘要未被覆盖
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);

    // 项目 A 详情挂起 → 切 B → 释放不得污染 B
    state.detailModeByProject[TECH_A] = { kind: "hold", gate: gateProjA };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await gateProjA.waitUntilEntered(1);
    const completeABeforeProjectSwitch = state.detailCompleteLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listCompleteLog.filter((p) => p === TECH_B).length, {
        timeout: 10_000,
      })
      .toBe(1);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);

    const detailBBefore = state.detailLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const detailBCompleteBefore = state.detailCompleteLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const detailAArrivedBeforeRelease = state.detailLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;
    gateProjA.release();
    delete state.detailModeByProject[TECH_A];
    // 迟到 A 详情必须真正 fulfill；不得新增 B 请求/摘要，A 摘要不得写到 B 项目
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.projectId === TECH_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(completeABeforeProjectSwitch + 1);
    expect(
      state.detailLog.filter((d) => d.projectId === TECH_A).length,
    ).toBe(detailAArrivedBeforeRelease);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    expect(
      state.detailLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(detailBBefore);
    expect(
      state.detailCompleteLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(detailBCompleteBefore);
  });

  test("list shape 非法固定失败无泄漏；检查点面板仍存在", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    state.listResponseOverride = {
      items: state.revisions[TECH_A],
      total: 1,
      snapshot: "LEAK_LIST_TOP",
    };
    await openWorkspace(page, "tech", TECH_A);
    await expect(
      page.getByTestId("editor-state-checkpoint-panel"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect
      .poll(() => state.listLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    let html = await page.content();
    expect(html).not.toContain("LEAK_LIST_TOP");
    expect(html).not.toContain(state.revisions[TECH_A][0].revisionId);

    state.listResponseOverride = {
      items: [
        {
          ...state.revisions[TECH_A][0],
          snapshot: "LEAK_META",
        },
      ],
    };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => state.listLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    html = await page.content();
    expect(html).not.toContain("LEAK_META");

    // 超 10 条
    state.listResponseOverride = {
      items: Array.from({ length: 11 }, (_, i) => ({
        revisionId: seedRevisionId(100 + i),
        stateVersion: seedStateVersion(100 + i),
        snapshotBytes: 1,
        sourceKind: "browser_put",
        createdAt: "2026-07-16T00:00:00.000Z",
      })),
    };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => state.listLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);

    // 无创建/删除/diff/搜索
    await expect(
      page.getByTestId("editor-state-revision-create"),
    ).toHaveCount(0);
    await expect(page.getByText("删除修订")).toHaveCount(0);
    await expect(page.getByText("搜索修订")).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// 商务标
// ---------------------------------------------------------------------------
test.describe("P12C-C3 商务标修订历史", () => {
  test.describe.configure({ mode: "serial" });

  test("默认折叠零请求；成功恢复水合商务 13 键；两窗口零 PUT；下一编辑 +1", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 1, ["revise"]);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.listLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => state.listLog.filter((p) => p === BIZ_A).length, {
        timeout: 10_000,
      })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);

    const putsAfter = state.putLog.length;
    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfter);

    await editContent(page, "biz", `${RESTORED_BIZ}\n下一编辑`);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfter + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.parsedMarkdown).toBe(`${RESTORED_BIZ}\n下一编辑`);
    expect(JSON.stringify(putBody.businessQualify)).toContain(
      BIZ_RESTORE_QUALIFY,
    );
    expect(JSON.stringify(putBody.businessToc)).toContain(BIZ_RESTORE_TOC);
    expect(JSON.stringify(putBody.businessQuote)).toContain(BIZ_RESTORE_QUOTE);
    expect(JSON.stringify(putBody.businessQuote)).toContain(
      BIZ_RESTORE_QUOTE_NOTES,
    );
    expect(JSON.stringify(putBody.businessCommit)).toContain(
      BIZ_RESTORE_COMMIT,
    );

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("商务：确认前 POST=0；409 阻断零重试；A→B 迟到 list 不污染", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 1);
    seedRevisions(state, BIZ_B, 1, ["callback"]);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandRevisionPanel(page);

    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toContainText(RESTORE_CONFIRM);

    state.restoreMode = { kind: "conflict" };
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
    expect(state.restoreLog.length).toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveText(MSG_RESTORE_BLOCKED);
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();

    // A→B 迟到 list：必须在 A 真正发出 list 并 waitUntilEntered 后再切 B
    // listLog 在 gate.wait 前已写入，释放后只能靠 listCompleteLog 证明真正 fulfill
    const listGateA = createHoldGate();
    state.listModeByProject[BIZ_A] = { kind: "hold", gate: listGateA };
    // 项目仍在 A：真实点击刷新触发 A list 挂起；先记录 release 前 completion 基线
    const listCompleteABefore = state.listCompleteLog.filter(
      (p) => p === BIZ_A,
    ).length;
    await page.getByTestId("editor-state-revision-refresh").click();
    await listGateA.waitUntilEntered(1);
    expect(state.listLog.filter((p) => p === BIZ_A).length).toBe(2);
    // 挂起期间尚未 fulfill（arrived 已是 2，不能当作 completion 证据）
    expect(state.listCompleteLog.filter((p) => p === BIZ_A).length).toBe(
      listCompleteABefore,
    );

    await openWorkspace(page, "biz", BIZ_B);
    await expandRevisionPanel(page);
    // 等 B 的 list 真正完成后再拍快照
    await expect
      .poll(() => state.listCompleteLog.filter((p) => p === BIZ_B).length, {
        timeout: 10_000,
      })
      .toBe(1);
    const listBSnapshot = state.listLog.filter((p) => p === BIZ_B).length;
    const listCompleteBSnapshot = state.listCompleteLog.filter(
      (p) => p === BIZ_B,
    ).length;
    const sourceB = await page
      .getByTestId("editor-state-revision-source-0")
      .innerText();
    const bodyB = await readContent(page, "biz");
    expect(bodyB).toBe(BIZ_MD_B);
    expect(sourceB).toBe(SOURCE_LABELS.callback);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);

    listGateA.release();
    delete state.listModeByProject[BIZ_A];
    // 必须等迟到 list 真正 fulfill 后再断言 B 未污染（不能只 poll arrived listLog）
    await expect
      .poll(
        () => state.listCompleteLog.filter((p) => p === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(listCompleteABefore + 1);
    // B 列表/完成/来源/提示/正文不变
    expect(state.listLog.filter((p) => p === BIZ_B).length).toBe(listBSnapshot);
    expect(state.listCompleteLog.filter((p) => p === BIZ_B).length).toBe(
      listCompleteBSnapshot,
    );
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.callback);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
  });
});
