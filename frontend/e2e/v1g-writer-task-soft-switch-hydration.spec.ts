/**
 * 模块：V1-G 任务迟到 success 软切编辑态水合围栏 + V1-M M3 A9/A10/A13 红门 failure-first E2E
 * 用途：A 项目 writer 任务仍在 pending/running 时软切 B；B 初始 editor-state 就绪后
 *       再释放 A 的 success。未修生产时，旧闭包会触发额外 editor-state GET 或粘住 B loading。
 *       同项目对照证明合法 success 仍精确 +1 GET 水合。
 *       扩展 A9/A10/uploadImage：跨项目 files/tasks 失败挂起、迟到 upload success/failure、图片正文围栏。
 *       M3-T12/A13：商务 managed task POST 500 → 固定安全错误、零 unhandledrejection、marker 零泄漏。
 * 对接：契约 docs/v1g-writer-task-success-refresh-fence-contract.md §6；
 *       技术 parse/analyze/outline/chapters/chapter；商务 biz_qualify；
 *       Playwright chromium --workers=1 --retries=0；route 桩 + HoldGate。
 * 二次开发：禁止固定 waitForTimeout/setTimeout/sleep 作完成证据、skip/fixme/only、
 *       宽泛非零计数、源码扫描、真实外网/业务库/uploads/密钥。
 * 可控终态：POST 返回 running → per-task SSE 立即可用失败以回退轮询 →
 *           任务详情 GET 用 HoldGate 挂起；B 就绪后再把任务标为 success 并 release。
 * 加固：B11 因果门——release 前注册精确 waitForResponse，release 后 finished/body/
 *       route fulfilled 台账 +1 + 零时长 continuation barrier（双 rAF + MessageChannel）；
 *       删除固定 400ms/Date.now 稳定窗；T9 技术 analyze ABA 锁 generation；
 *       waitTaskRunningHeld 对当前 projectId+type 的 taskPosts 精确计数 1。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_v1g_tech_a";
const TECH_B = "proj_e2e_v1g_tech_b";
const BIZ_A = "proj_e2e_v1g_biz_a";
const BIZ_B = "proj_e2e_v1g_biz_b";

const OVERVIEW_A = "V1G_TECH_A_OVERVIEW_权威概述甲";
const OVERVIEW_B = "V1G_TECH_B_OVERVIEW_权威概述乙";
const MARKDOWN_A = "V1G_BIZ_A_PARSED_MARKDOWN_权威正文甲";
const MARKDOWN_B = "V1G_BIZ_B_PARSED_MARKDOWN_权威正文乙";
const QUALIFY_A = "V1G_BIZ_A_QUALIFY_资格要求甲";
const QUALIFY_B = "V1G_BIZ_B_QUALIFY_资格要求乙";
const CHAPTER_TITLE = "V1G服务端一级目录";
const CHAPTER_BODY_A = "V1G_CHAPTER_BODY_A";
const CHAPTER_BODY_B = "V1G_CHAPTER_BODY_B";
const PARSE_MD_A = "V1G_TECH_A_PARSED_MD";
const PARSE_MD_B = "V1G_TECH_B_PARSED_MD";

const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const SECRET = "SECRET_V1G_LEAK_SHOULD_NOT_RENDER";

/** A/B 专属文件名 / 任务 message / 上传 marker / 图片 ID（跨项目泄漏探针） */
const FILE_NAME_TECH_A = "V1G_TECH_A_ONLY_bid.pdf";
const FILE_NAME_TECH_B = "V1G_TECH_B_ONLY_bid.pdf";
const FILE_NAME_BIZ_A = "V1G_BIZ_A_ONLY_bid.pdf";
const FILE_NAME_BIZ_B = "V1G_BIZ_B_ONLY_bid.pdf";
const TASK_MSG_TECH_A = "V1G_TECH_A_TASK_MSG_unique";
const TASK_MSG_BIZ_A = "V1G_BIZ_A_TASK_MSG_unique";
const UPLOAD_NAME_TECH_A = "V1G_TECH_A_UPLOAD_marker.pdf";
const UPLOAD_NAME_TECH_B = "V1G_TECH_B_UPLOAD_marker.pdf";
const UPLOAD_NAME_BIZ_A = "V1G_BIZ_A_UPLOAD_marker.pdf";
const UPLOAD_NAME_BIZ_B = "V1G_BIZ_B_UPLOAD_marker.pdf";
const UPLOAD_FAIL_MARKER_BIZ_A = "V1G_BIZ_A_UPLOAD_FAIL_SENSITIVE_400";
const IMAGE_ID_A = "img_v1g_a_unique_only";
const IMAGE_FILE_A = "V1G_TECH_A_IMAGE_only.png";
/** A13：商务 managed task POST 500 响应体敏感 marker（UI/台账须零泄漏） */
const TASK_POST_500_MARKER_BIZ =
  "V1G_BIZ_MANAGED_TASK_POST_500_SENSITIVE_MARKER_unique";
/** 固定安全错误文案（pipeline.error / 错误区可见，禁止回显 marker） */
const TASK_POST_SAFE_ERROR = "任务请求失败";

type ApiMode = "ok" | "hold" | "fail";
type PathLedger = { method: string; pathname: string; projectId?: string };
type FileRow = {
  id: string;
  filename: string;
  sizeBytes: number;
  createdAt: string;
};

type Kind = "technical" | "business";

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  waiterCount: () => number;
};

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: Kind;
  linkedProjectId?: string | null;
};

type EditorState = {
  projectId: string;
  outline: Array<Record<string, unknown>>;
  chapters: Array<Record<string, unknown>>;
  facts: unknown[];
  mode: string;
  analysisOverview: string;
  analysis: {
    overview: string;
    techRequirements: string[];
    rejectionRisks: string[];
    scoringPoints: Array<{ name: string; weight: string }>;
  };
  responseMatrix: unknown[];
  responseMatrixVersion: string | null;
  parsedMarkdown: string;
  guidance: Record<string, unknown> | null;
  businessQualify: Array<Record<string, unknown>>;
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  stateVersion: string;
  updatedAt: string | null;
};

type TaskRecord = {
  id: string;
  projectId: string;
  type: string;
  status: string;
  progress: number;
  message: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  payload?: Record<string, unknown> | null;
};

type TaskPost = {
  projectId: string;
  type: string;
  payload?: Record<string, unknown> | null;
  taskId: string;
};

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  /** 兼容旧断言：editor-state GET 的 projectId 序列 */
  getLog: string[];
  putLog: string[];
  /** 精确 method+pathname 台账 */
  editorGetLog: PathLedger[];
  editorPutLog: PathLedger[];
  strategyGetLog: PathLedger[];
  filePostLog: PathLedger[];
  imagePostLog: PathLedger[];
  filePostFulfilledLog: string[];
  imagePostFulfilledLog: string[];
  taskDetailFulfilledLog: string[];
  /** B18：files GET / tasks 列表 GET 按 project+method+pathname 发起/waiter/fulfilled */
  filesGetInitLog: PathLedger[];
  filesGetWaiterLog: PathLedger[];
  filesGetFulfilledLog: PathLedger[];
  tasksListInitLog: PathLedger[];
  tasksListWaiterLog: PathLedger[];
  tasksListFulfilledLog: PathLedger[];
  patchLog: Array<{ projectId: string; body: Record<string, unknown> }>;
  taskPosts: TaskPost[];
  taskDetailLog: string[];
  filesLog: string[];
  forbiddenHits: string[];
  externalHits: string[];
  activeTasks: Record<string, TaskRecord>;
  /** 按项目隔离的文件列表（任务列表不得再返回跨项目 activeTasks） */
  filesByProject: Record<string, FileRow[]>;
  filesGetMode: Record<string, ApiMode>;
  tasksGetMode: Record<string, ApiMode>;
  uploadFileMode: Record<string, ApiMode>;
  uploadImageMode: Record<string, ApiMode>;
  /**
   * A13/M3-T12：任务创建 POST 模式（默认 ok）。
   * fail → HTTP 500 + 安全文案 + 敏感 marker 字段；仍记入 taskPosts 台账。
   * 不得影响 T6..T11 默认 ok 路径。
   */
  taskCreateMode: Record<string, ApiMode>;
  filesGetGate: Record<string, HoldGate>;
  tasksGetGate: Record<string, HoldGate>;
  uploadFileGate: Record<string, HoldGate>;
  uploadImageGate: Record<string, HoldGate>;
  /** hold 释放时使用的响应体（status + body） */
  uploadFilePending: Record<string, { status: number; body: unknown }>;
  uploadImagePending: Record<string, { status: number; body: unknown }>;
  /** 任务详情 GET 挂起：release 前阻塞，用于可控释放终态 */
  taskDetailGate: Record<string, HoldGate>;
  taskSeq: number;
  versionSeq: number;
  fileSeq: number;
  imageSeq: number;
  /**
   * M3：可注入权威 parseStrategy（light|managed|local|ask）。
   * 默认 light，保持既有用例语义。
   */
  parseStrategy: "light" | "managed" | "local" | "ask";
};

function createHoldGate(): HoldGate {
  let released = false;
  const waiters: Array<() => void> = [];
  return {
    wait: () =>
      released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          }),
    release: () => {
      released = true;
      while (waiters.length > 0) waiters.shift()?.();
    },
    isReleased: () => released,
    waiterCount: () => waiters.length,
  };
}

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedTaskId(n: number): string {
  return `task_v1g_${n.toString(16).padStart(12, "0")}`;
}

function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function isLocalHost(host: string): boolean {
  return host === "127.0.0.1" || host === "localhost";
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: { "Cache-Control": "no-store" },
    body: JSON.stringify(body),
  });
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-22T12:00:00.000Z",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: partial.kind,
  };
}

function baseEditor(projectId: string, kind: Kind, version: string): EditorState {
  const isTech = kind === "technical";
  return {
    projectId,
    outline: isTech
      ? [
          {
            id: "n1",
            title: CHAPTER_TITLE,
            level: 1,
            targetWords: 800,
            description: "",
            children: [],
          },
        ]
      : [],
    chapters: isTech
      ? [
          {
            id: "n1",
            title: CHAPTER_TITLE,
            body: projectId === TECH_A ? CHAPTER_BODY_A : CHAPTER_BODY_B,
            preview: projectId === TECH_A ? CHAPTER_BODY_A : CHAPTER_BODY_B,
            wordCount: 8,
            status: "done",
          },
        ]
      : [],
    facts: [],
    mode: isTech ? "ALIGNED" : "business",
    analysisOverview: projectId === TECH_A ? OVERVIEW_A : OVERVIEW_B,
    analysis: {
      overview: projectId === TECH_A ? OVERVIEW_A : OVERVIEW_B,
      techRequirements: ["V1G技术要求"],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: isTech
      ? projectId === TECH_A
        ? PARSE_MD_A
        : PARSE_MD_B
      : projectId === BIZ_A
        ? MARKDOWN_A
        : MARKDOWN_B,
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    businessQualify: isTech
      ? []
      : [
          {
            id: "q1",
            requirement:
              projectId === BIZ_A ? QUALIFY_A : QUALIFY_B,
            status: "missing",
            evidence: "",
            notes: "",
          },
        ],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion: version,
    updatedAt: "2026-07-22T12:00:00.000Z",
  };
}

function defaultFileName(projectId: string, kind: Kind): string {
  if (kind === "technical") {
    return projectId === TECH_A ? FILE_NAME_TECH_A : FILE_NAME_TECH_B;
  }
  return projectId === BIZ_A ? FILE_NAME_BIZ_A : FILE_NAME_BIZ_B;
}

function ensureProjectGate(
  bag: Record<string, HoldGate>,
  projectId: string,
): HoldGate {
  if (!bag[projectId]) bag[projectId] = createHoldGate();
  return bag[projectId];
}

function createProbeState(
  projects: ProjectStub[],
  opts?: { parseStrategy?: ProbeState["parseStrategy"] },
): ProbeState {
  const editorById: Record<string, EditorState> = {};
  const filesByProject: Record<string, FileRow[]> = {};
  let versionSeq = 0;
  for (const p of projects) {
    versionSeq += 1;
    editorById[p.id] = baseEditor(p.id, p.kind, seedStateVersion(versionSeq));
    // 默认每项目唯一文件：解除解析按钮 disabled，且 A/B 文件名互不共享
    filesByProject[p.id] = [
      {
        id: `file_${p.id}_seed`,
        filename: defaultFileName(p.id, p.kind),
        sizeBytes: 2048,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    ];
  }
  return {
    projects,
    editorById,
    getLog: [],
    putLog: [],
    editorGetLog: [],
    editorPutLog: [],
    strategyGetLog: [],
    filePostLog: [],
    imagePostLog: [],
    filePostFulfilledLog: [],
    imagePostFulfilledLog: [],
    taskDetailFulfilledLog: [],
    filesGetInitLog: [],
    filesGetWaiterLog: [],
    filesGetFulfilledLog: [],
    tasksListInitLog: [],
    tasksListWaiterLog: [],
    tasksListFulfilledLog: [],
    patchLog: [],
    taskPosts: [],
    taskDetailLog: [],
    filesLog: [],
    forbiddenHits: [],
    externalHits: [],
    activeTasks: {},
    filesByProject,
    filesGetMode: {},
    tasksGetMode: {},
    uploadFileMode: {},
    uploadImageMode: {},
    taskCreateMode: {},
    filesGetGate: {},
    tasksGetGate: {},
    uploadFileGate: {},
    uploadImageGate: {},
    uploadFilePending: {},
    uploadImagePending: {},
    taskDetailGate: {},
    taskSeq: 0,
    versionSeq,
    fileSeq: 0,
    imageSeq: 0,
    parseStrategy: opts?.parseStrategy ?? "light",
  };
}

/** 按 projectId 统计 PathLedger 条数（method+pathname 可选精确）。 */
function countPathLedger(
  log: PathLedger[],
  projectId: string,
  method?: string,
  pathname?: string,
): number {
  return log.filter((e) => {
    if (e.projectId !== projectId) return false;
    if (method && e.method.toUpperCase() !== method.toUpperCase()) return false;
    if (pathname && e.pathname !== pathname) return false;
    return true;
  }).length;
}

function pathLedgerEntry(
  method: string,
  pathname: string,
  projectId: string,
): PathLedger {
  return { method, pathname, projectId };
}

function knownProjectIdSet(state: ProbeState): Set<string> {
  return new Set(state.projects.map((p) => p.id));
}

function isAllowedApi(
  method: string,
  path: string,
  known: ReadonlySet<string>,
): boolean {
  const staticRules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces(\/|$)/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings(\/|$)/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET"], path: /^\/api\/knowledge(\/|$)/ },
    { methods: ["GET"], path: /^\/api\/templates\/?$/ },
    { methods: ["GET"], path: /^\/api\/cards\/?$/ },
    { methods: ["GET"], path: /^\/api\/hr\/team-recommendations\/?$/ },
  ];
  if (staticRules.some((r) => r.methods.includes(method) && r.path.test(path))) {
    return true;
  }
  const projectMatch = path.match(/^\/api\/projects\/([^/]+)(\/.*)?$/);
  if (!projectMatch) return false;
  const projectId = decodeURIComponent(projectMatch[1]);
  if (!known.has(projectId)) return false;
  const rest = projectMatch[2] || "";
  const projectRules: Array<{ methods: string[]; rest: RegExp }> = [
    { methods: ["GET", "PATCH"], rest: /^\/?$/ },
    { methods: ["GET", "PUT"], rest: /^\/editor-state\/?$/ },
    { methods: ["GET"], rest: /^\/editor-state-events\/stream\/?$/ },
    { methods: ["GET"], rest: /^\/task-events\/stream\/?$/ },
    { methods: ["GET", "POST"], rest: /^\/(files|tasks|images)\/?$/ },
    {
      methods: ["GET", "POST"],
      rest: /^\/tasks\/[^/]+(\/(events|cancel|status))?\/?$/,
    },
    { methods: ["POST"], rest: /^\/presence\/(heartbeat|leave)\/?$/ },
    {
      methods: ["POST"],
      rest: /^\/chapter-edit-intents\/(heartbeat|leave)\/?$/,
    },
  ];
  return projectRules.some(
    (r) => r.methods.includes(method) && r.rest.test(rest),
  );
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(rawUrl)) {
      await route.fulfill({ status: 204, contentType: "text/plain", body: "" });
      return;
    }

    let url: URL;
    try {
      url = new URL(rawUrl);
    } catch {
      state.externalHits.push(`${method} ${rawUrl}`);
      await route.abort("failed");
      return;
    }

    if (!isLocalHost(url.hostname)) {
      state.externalHits.push(`${method} ${rawUrl}`);
      await route.abort("failed");
      return;
    }

    const path = url.pathname;
    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    const known = knownProjectIdSet(state);

    // 所有 SSE：立即不可用。per-task 失败迫使 runTask 回退详情 GET + HoldGate 可控终态。
    if (
      method === "GET" &&
      (/\/task-events\/stream\/?$/.test(path) ||
        /\/editor-state-events\/stream\/?$/.test(path) ||
        /\/tasks\/[^/]+\/events\/?$/.test(path))
    ) {
      await route.fulfill({
        status: 503,
        contentType: "text/plain",
        body: "v1g-sse-unavailable",
      });
      return;
    }

    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "v1g_forbidden", message: SECRET } },
        403,
      );
      return;
    }

    if (path === "/api/health" && method === "GET") {
      await json(route, {
        status: "ok",
        service: "biaoshu-e2e",
        defaultWorkspaceId: "ws_e2e",
      });
      return;
    }
    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, { bootstrapped: true, authRequired: false });
      return;
    }
    if (path === "/api/auth/me" && method === "GET") {
      await json(route, {
        user: { id: "user_e2e", username: "e2e" },
        workspaces: [
          {
            id: "ws_e2e",
            name: "E2E 工作空间",
            role: "bid_writer",
            isOwner: true,
          },
        ],
        activeWorkspaceId: "ws_e2e",
        csrfToken: "e2e-v1g-csrf",
      });
      return;
    }
    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-v1g-csrf" });
      return;
    }
    if (
      (path === "/api/workspace" || path === "/api/workspaces") &&
      method === "GET"
    ) {
      await json(route, {
        id: "ws_e2e",
        name: "E2E 工作空间",
        ownerUserId: "user_e2e",
      });
      return;
    }
    // M3：权威策略脱敏 GET 与完整 settings 分离
    if (path === "/api/settings/parse-strategy" && method === "GET") {
      state.strategyGetLog.push({
        method: "GET",
        pathname: path,
      });
      await json(route, { parseStrategy: state.parseStrategy });
      return;
    }
    if (
      path.startsWith("/api/settings") &&
      (method === "GET" || method === "PUT")
    ) {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "",
        apiKey: "",
        model: "",
        parseStrategy: state.parseStrategy,
      });
      return;
    }
    if (path === "/api/projects" || path === "/api/projects/") {
      if (method === "GET") {
        const kind = url.searchParams.get("kind");
        let items = state.projects;
        if (kind === "technical" || kind === "business") {
          items = items.filter((p) => p.kind === kind);
        }
        await json(route, items);
        return;
      }
      await json(route, { detail: { code: "v1g_no_create" } }, 403);
      return;
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = decodeURIComponent(detailMatch[1]);
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(route, { detail: { code: "project_not_found" } }, 404);
        return;
      }
      if (method === "PATCH") {
        const body = (req.postDataJSON() as Record<string, unknown>) || {};
        state.patchLog.push({ projectId: id, body });
        if (typeof body.technicalPlanStep === "number") {
          found.technicalPlanStep = body.technicalPlanStep;
        }
        if (typeof body.name === "string") {
          found.name = body.name;
        }
      }
      await json(route, found);
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      const id = decodeURIComponent(editorMatch[1]);
      if (method === "GET") {
        state.getLog.push(id);
        state.editorGetLog.push({
          method: "GET",
          pathname: path,
          projectId: id,
        });
        const ed = state.editorById[id];
        if (!ed) {
          await json(route, { detail: { code: "not_found" } }, 404);
          return;
        }
        await json(route, ed);
        return;
      }
      state.putLog.push(id);
      state.editorPutLog.push({
        method: "PUT",
        pathname: path,
        projectId: id,
      });
      const prev = state.editorById[id];
      state.versionSeq += 1;
      const nextVersion = seedStateVersion(state.versionSeq);
      if (prev && isValidStateVersion(prev.stateVersion)) {
        prev.stateVersion = nextVersion;
      }
      await json(route, state.editorById[id] ?? { projectId: id, stateVersion: nextVersion });
      return;
    }

    const filesMatch = path.match(/^\/api\/projects\/([^/]+)\/files\/?$/);
    if (filesMatch && method === "GET") {
      const pid = decodeURIComponent(filesMatch[1]);
      state.filesLog.push(pid);
      const entry = pathLedgerEntry("GET", path, pid);
      state.filesGetInitLog.push(entry);
      const mode = state.filesGetMode[pid] ?? "ok";
      if (mode === "hold") {
        state.filesGetWaiterLog.push(entry);
        await ensureProjectGate(state.filesGetGate, pid).wait();
      }
      if (mode === "fail") {
        state.filesGetFulfilledLog.push(entry);
        await json(
          route,
          { detail: { code: "files_get_fail", message: SECRET } },
          500,
        );
        return;
      }
      state.filesGetFulfilledLog.push(entry);
      await json(route, state.filesByProject[pid] ?? []);
      return;
    }
    if (filesMatch && method === "POST") {
      const pid = decodeURIComponent(filesMatch[1]);
      state.filePostLog.push({
        method: "POST",
        pathname: path,
        projectId: pid,
      });
      const mode = state.uploadFileMode[pid] ?? "ok";
      if (mode === "hold") {
        await ensureProjectGate(state.uploadFileGate, pid).wait();
      }
      const pending = state.uploadFilePending[pid];
      const status = pending?.status ?? (mode === "fail" ? 400 : 201);
      const body =
        pending?.body ??
        (status >= 400
          ? { detail: { code: "upload_fail", message: SECRET } }
          : {
              id: `file_${pid}_up_${++state.fileSeq}`,
              filename: `upload_${pid}.pdf`,
              sizeBytes: 100,
              createdAt: "2026-07-22T12:00:00.000Z",
            });
      if (status < 400 && body && typeof body === "object") {
        const row = body as FileRow;
        const list = state.filesByProject[pid] ?? [];
        list.push({
          id: row.id,
          filename: row.filename,
          sizeBytes: row.sizeBytes ?? 100,
          createdAt: row.createdAt ?? "2026-07-22T12:00:00.000Z",
        });
        state.filesByProject[pid] = list;
      }
      state.filePostFulfilledLog.push(pid);
      await json(route, body, status);
      return;
    }

    const imagesMatch = path.match(/^\/api\/projects\/([^/]+)\/images\/?$/);
    if (imagesMatch && method === "POST") {
      const pid = decodeURIComponent(imagesMatch[1]);
      state.imagePostLog.push({
        method: "POST",
        pathname: path,
        projectId: pid,
      });
      const mode = state.uploadImageMode[pid] ?? "ok";
      if (mode === "hold") {
        await ensureProjectGate(state.uploadImageGate, pid).wait();
      }
      const pending = state.uploadImagePending[pid];
      const status = pending?.status ?? (mode === "fail" ? 400 : 201);
      const body =
        pending?.body ??
        (status >= 400
          ? { detail: { code: "image_upload_fail", message: SECRET } }
          : {
              id: `img_${pid}_${++state.imageSeq}`,
              filename: `image_${pid}.png`,
              sizeBytes: 64,
            });
      state.imagePostFulfilledLog.push(pid);
      await json(route, body, status);
      return;
    }

    // 任务创建 / 列表 / 详情 / status / cancel
    const taskMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks(?:\/([^/]+))?(?:\/(events|cancel|status))?\/?$/,
    );
    if (taskMatch && (method === "GET" || method === "POST")) {
      const pid = decodeURIComponent(taskMatch[1]);
      const tid = taskMatch[2] ? decodeURIComponent(taskMatch[2]) : "";
      const sub = taskMatch[3] || "";

      if (method === "POST" && !tid) {
        const body = (req.postDataJSON() as {
          type?: string;
          payload?: Record<string, unknown>;
        }) || {};
        const createMode = state.taskCreateMode[pid] ?? "ok";
        // A13/M3-T12：fail 路径仍精确记入 taskPosts（payload/engine 可断言），零 activeTasks
        if (createMode === "fail") {
          state.taskPosts.push({
            projectId: pid,
            type: body.type || "parse",
            payload: body.payload ?? null,
            taskId: "",
          });
          await json(
            route,
            {
              detail: {
                code: "task_create_fail",
                message: TASK_POST_SAFE_ERROR,
                // 敏感字段：若 UI 回显整包 detail/stack 会泄漏；安全路径不得渲染
                diagnostic: TASK_POST_500_MARKER_BIZ,
              },
            },
            500,
          );
          return;
        }
        state.taskSeq += 1;
        const id = seedTaskId(state.taskSeq);
        const row: TaskRecord = {
          id,
          projectId: pid,
          type: body.type || "parse",
          status: "running",
          progress: 18,
          message: `V1G_${body.type || "parse"}_RUNNING`,
          result: null,
          error: null,
          payload: body.payload ?? null,
        };
        state.activeTasks[id] = row;
        state.taskPosts.push({
          projectId: pid,
          type: row.type,
          payload: row.payload,
          taskId: id,
        });
        // 每个任务默认挂起详情 GET，直到测试显式 release
        state.taskDetailGate[id] = createHoldGate();
        await json(route, row, 201);
        return;
      }

      if (method === "GET" && !tid) {
        const entry = pathLedgerEntry("GET", path, pid);
        state.tasksListInitLog.push(entry);
        const mode = state.tasksGetMode[pid] ?? "ok";
        if (mode === "hold") {
          state.tasksListWaiterLog.push(entry);
          await ensureProjectGate(state.tasksGetGate, pid).wait();
        }
        if (mode === "fail") {
          state.tasksListFulfilledLog.push(entry);
          await json(
            route,
            { detail: { code: "tasks_get_fail", message: SECRET } },
            500,
          );
          return;
        }
        // 严格按项目隔离，禁止把其它项目 activeTasks 混入列表
        const list = Object.values(state.activeTasks).filter(
          (t) => t.projectId === pid,
        );
        state.tasksListFulfilledLog.push(entry);
        await json(route, list);
        return;
      }

      if (method === "GET" && tid && sub === "status") {
        const row = state.activeTasks[tid];
        await json(route, {
          taskId: tid,
          status: row?.status ?? "running",
          progress: row?.progress ?? 0,
        });
        return;
      }

      if (method === "POST" && sub === "cancel") {
        const row = state.activeTasks[tid];
        if (row) {
          row.status = "cancelled";
          row.progress = 0;
          row.message = "cancelled";
        }
        await json(route, row ?? { id: tid, status: "cancelled" });
        return;
      }

      if (method === "GET" && tid && !sub) {
        state.taskDetailLog.push(`${pid}/${tid}`);
        const gate = state.taskDetailGate[tid];
        if (gate && !gate.isReleased()) {
          await gate.wait();
        }
        const row = state.activeTasks[tid] ?? {
          id: tid,
          projectId: pid,
          type: "parse",
          status: "running",
          progress: 0,
          message: "missing",
        };
        state.taskDetailFulfilledLog.push(`${pid}/${tid}`);
        await json(route, row);
        return;
      }

      await json(route, { detail: SECRET }, 404);
      return;
    }

    if (
      method === "POST" &&
      (/\/presence\/(heartbeat|leave)\/?$/.test(path) ||
        /\/chapter-edit-intents\/(heartbeat|leave)\/?$/.test(path))
    ) {
      if (path.includes("/leave")) {
        await route.fulfill({
          status: 204,
          headers: { "Cache-Control": "no-store" },
          body: "",
        });
        return;
      }
      if (path.includes("chapter-edit-intents")) {
        await json(route, {
          leaseExpiresAt: "2026-07-22T12:35:41.000Z",
          refreshAfterSeconds: 15,
        });
        return;
      }
      await json(route, {
        leaseExpiresAt: "2026-07-22T12:35:41.000Z",
        refreshAfterSeconds: 15,
        members: [{ username: "e2e", isSelf: true }],
        truncated: false,
      });
      return;
    }

    if (method === "GET") {
      await json(route, []);
      return;
    }
    state.forbiddenHits.push(`${method} ${path}`);
    await json(route, { detail: { code: "v1g_unhandled", message: SECRET } }, 404);
  });
}

function collectConsole(page: Page): string[] {
  const lines: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      lines.push(`${msg.type()}: ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    lines.push(`pageerror: ${String(err)}`);
  });
  return lines;
}

/**
 * 零时长 continuation barrier：双 rAF + MessageChannel（任务队列门）。
 * 禁止 waitForTimeout/networkidle/固定时间窗冒充完成。
 */
async function waitContinuationBarrier(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            const ch = new MessageChannel();
            ch.port1.onmessage = () => resolve();
            ch.port2.postMessage(null);
          });
        });
      }),
  );
}

function pathnameOf(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return "";
  }
}

/** 精确 method + 完整 pathname 匹配 response。 */
function matchApiResponse(
  resp: { url: () => string; request: () => { method: () => string } },
  method: string,
  pathname: string,
): boolean {
  if (resp.request().method().toUpperCase() !== method.toUpperCase()) {
    return false;
  }
  return pathnameOf(resp.url()) === pathname;
}

async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

/**
 * B15/B20/B21/D6 route-aware DOM 探针：页面内同步 hits 为唯一真源。
 * 安装后默认 disarmed；保存 observer/processRecords。
 * arm 时：先 takeRecords 丢弃 pre-arm 队列 → 清 hits → 启用 → 扫 current（关 B20 假红）。
 * armed 后 process：characterData/attributes 的 old+current、added+removed、current DOM（关 B21 假绿）。
 * observe 启用 characterDataOldValue/attributeOldValue。read hits 前处理 pending records。
 * 必须在 page.goto 之前调用（addInitScript）。
 */
async function installRouteAwareDomProbe(
  page: Page,
  targetUrlPart: string,
  markers: string[],
): Promise<void> {
  await page.addInitScript(
    ({ targetUrlPart: part, markers: marks }) => {
      type ProbeWin = {
        __v1gDomLeakHits?: string[];
        __v1gDomProbeArmed?: boolean;
        __v1gDomProbeArm?: () => void;
        __v1gDomProbeClear?: () => void;
        __v1gDomProbeObserver?: MutationObserver | null;
        __v1gDomProbeProcessRecords?: (records: MutationRecord[]) => void;
        __v1gDomProbeFlushPending?: () => void;
      };
      const w = window as unknown as ProbeWin;
      w.__v1gDomLeakHits = [];
      w.__v1gDomProbeArmed = false;
      w.__v1gDomProbeObserver = null;
      const report = (kind: string, marker: string) => {
        if (!w.__v1gDomProbeArmed) return;
        w.__v1gDomLeakHits = w.__v1gDomLeakHits || [];
        w.__v1gDomLeakHits.push(`${kind}|${marker}`);
      };
      const urlOk = () => location.href.includes(part);
      const active = () => !!w.__v1gDomProbeArmed && urlOk();
      const scanText = (text: string, kind: string) => {
        if (!active() || !text) return;
        for (const m of marks) {
          if (m && text.includes(m)) report(kind, m);
        }
      };
      const scanNode = (root: Node | null, kind: string) => {
        if (!root || !active()) return;
        const walk = (n: Node) => {
          if (n.nodeType === Node.TEXT_NODE) {
            scanText(n.textContent || "", kind);
            return;
          }
          if (n.nodeType === Node.ELEMENT_NODE) {
            const el = n as Element;
            scanText(el.textContent || "", kind);
            for (const attr of Array.from(el.attributes || [])) {
              scanText(attr.value, `${kind}:attr`);
            }
          }
          for (const c of Array.from(n.childNodes)) walk(c);
        };
        walk(root);
      };
      const scanFull = (kind: string) => {
        if (!active() || !document.body) return;
        scanNode(document.body, kind);
      };
      const processRecords = (muts: MutationRecord[]) => {
        if (!active()) return;
        for (const mut of muts) {
          // characterData：old + current（关 post-arm marker→safe 假绿）
          if (mut.type === "characterData" && mut.target) {
            scanText(mut.target.textContent || "", "char");
            if (typeof mut.oldValue === "string") {
              scanText(mut.oldValue, "char-old");
            }
          }
          // attributes：old + current
          if (
            mut.type === "attributes" &&
            mut.target &&
            mut.target.nodeType === Node.ELEMENT_NODE
          ) {
            const el = mut.target as Element;
            if (typeof mut.oldValue === "string") {
              scanText(mut.oldValue, "attr-old");
            }
            if (mut.attributeName) {
              scanText(
                el.getAttribute(mut.attributeName) || "",
                "attr-changed",
              );
            }
            for (const attr of Array.from(el.attributes || [])) {
              scanText(attr.value, "attr-current");
            }
          }
          for (const n of Array.from(mut.addedNodes)) {
            scanNode(n, "added");
          }
          // removedNodes：插入后清空/删除瞬态
          for (const n of Array.from(mut.removedNodes)) {
            scanNode(n, "removed");
          }
        }
        scanFull("mut-dom");
      };
      w.__v1gDomProbeProcessRecords = processRecords;
      w.__v1gDomProbeFlushPending = () => {
        const obs = w.__v1gDomProbeObserver;
        if (!obs || !w.__v1gDomProbeProcessRecords) return;
        const pending = obs.takeRecords();
        if (pending.length > 0) {
          w.__v1gDomProbeProcessRecords(pending);
        }
      };
      const boot = () => {
        if (!document.body) return;
        const obs = new MutationObserver((muts) => {
          // 未 arm 时仍接收回调但不入 hits；arm 前 takeRecords 丢弃 pre-arm 队列
          if (!active()) return;
          processRecords(muts);
        });
        obs.observe(document.body, {
          childList: true,
          subtree: true,
          characterData: true,
          characterDataOldValue: true,
          attributes: true,
          attributeOldValue: true,
        });
        w.__v1gDomProbeObserver = obs;
        // 默认 disarmed：不在 install 时全量扫描
      };
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
      } else {
        boot();
      }
      // D6 arm：先 drain pre-arm 队列（不入 hits）→ 清 hits → 启用 → 扫 current
      w.__v1gDomProbeArm = () => {
        const obs = w.__v1gDomProbeObserver;
        if (obs) {
          // 丢弃 pre-arm pending，关闭 B20 假红
          obs.takeRecords();
        }
        w.__v1gDomLeakHits = [];
        w.__v1gDomProbeArmed = true;
        if (urlOk() && document.body) {
          scanFull("arm-dom");
        }
      };
      // clear：清空 hits 并 disarm
      w.__v1gDomProbeClear = () => {
        w.__v1gDomLeakHits = [];
        w.__v1gDomProbeArmed = false;
      };
    },
    { targetUrlPart, markers },
  );
}

/**
 * Node helper：精确读回页面内同步 hits（唯一真源）。
 * D6：read 前先 process pending takeRecords，避免队列残留假绿。
 */
async function readDomProbeHits(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as unknown as {
      __v1gDomLeakHits?: string[];
      __v1gDomProbeFlushPending?: () => void;
    };
    if (typeof w.__v1gDomProbeFlushPending === "function") {
      w.__v1gDomProbeFlushPending();
    }
    return (w.__v1gDomLeakHits || []).slice();
  });
}

/**
 * 目标页 ready 后显式 arm：清空 hits → 启用 → 同步扫描当前 DOM。
 * 禁止在 softNavigate 后、heading/ready 前调用。
 */
async function armDomProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __v1gDomProbeArm?: () => void };
    if (typeof w.__v1gDomProbeArm !== "function") {
      throw new Error("route-aware DOM probe 未安装，无法 arm");
    }
    w.__v1gDomProbeArm();
  });
}

/** 清空 hits 并 disarm（释放敏感观测窗口）。 */
async function clearDomProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __v1gDomProbeClear?: () => void };
    if (typeof w.__v1gDomProbeClear !== "function") {
      throw new Error("route-aware DOM probe 未安装，无法 clear");
    }
    w.__v1gDomProbeClear();
  });
}

/**
 * armed 后补扫当前 DOM（SPA 不重跑 initScript）。未 arm 时 no-op，避免 B ready 前假红。
 */
async function rescanDomProbeForUrl(
  page: Page,
  targetUrlPart: string,
  markers: string[],
) {
  await page.evaluate(
    ({ targetUrlPart: part, markers: marks }) => {
      const w = window as unknown as {
        __v1gDomLeakHits?: string[];
        __v1gDomProbeArmed?: boolean;
      };
      if (!w.__v1gDomProbeArmed) return;
      if (!location.href.includes(part) || !document.body) return;
      w.__v1gDomLeakHits = w.__v1gDomLeakHits || [];
      const text = document.body.innerText || "";
      const html = document.body.innerHTML || "";
      for (const m of marks) {
        if (m && (text.includes(m) || html.includes(m))) {
          w.__v1gDomLeakHits.push(`softnav-dom|${m}`);
        }
      }
    },
    { targetUrlPart, markers },
  );
  await waitContinuationBarrier(page);
  await page.evaluate(
    ({ targetUrlPart: part, markers: marks }) => {
      const w = window as unknown as {
        __v1gDomLeakHits?: string[];
        __v1gDomProbeArmed?: boolean;
      };
      if (!w.__v1gDomProbeArmed) return;
      if (!location.href.includes(part) || !document.body) return;
      w.__v1gDomLeakHits = w.__v1gDomLeakHits || [];
      const text = document.body.innerText || "";
      for (const m of marks) {
        if (m && text.includes(m)) {
          w.__v1gDomLeakHits.push(`softnav-raf|${m}`);
        }
      }
    },
    { targetUrlPart, markers },
  );
}

/**
 * B17：pageerror 台账 + 页面 unhandledrejection 台账双证据。
 * 必须 await addInitScript，禁止仅用 /unhandled/ 文本过滤冒充。
 */
async function installPageErrorLedgers(page: Page): Promise<{
  pageErrors: string[];
  readUnhandled: () => Promise<string[]>;
}> {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(String(err?.message || err));
  });
  await page.addInitScript(() => {
    const w = window as unknown as { __v1gUnhandledRejections?: string[] };
    w.__v1gUnhandledRejections = [];
    window.addEventListener("unhandledrejection", (ev) => {
      const reason = (ev as PromiseRejectionEvent).reason;
      const text =
        reason instanceof Error
          ? reason.message
          : typeof reason === "string"
            ? reason
            : String(reason);
      w.__v1gUnhandledRejections = w.__v1gUnhandledRejections || [];
      w.__v1gUnhandledRejections.push(text);
    });
  });
  return {
    pageErrors,
    readUnhandled: () =>
      page.evaluate(() => {
        const w = window as unknown as { __v1gUnhandledRejections?: string[] };
        return (w.__v1gUnhandledRejections || []).slice();
      }),
  };
}

async function expectTechReady(page: Page, name: string) {
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
}

async function expectBizReady(page: Page, name: string) {
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
}

function countGets(state: ProbeState, projectId: string): number {
  return state.getLog.filter((id) => id === projectId).length;
}

/**
 * 用途：启动 writer 任务后等到详情 GET 进入 HoldGate，证明仍在 pending/running。
 */
async function waitTaskRunningHeld(
  state: ProbeState,
  projectId: string,
  type: string,
): Promise<TaskPost> {
  // 精确 1：禁止重复 POST 仍因 >=1 假绿；waiterCount 仍允许 >=1（轮询重试）
  await expect
    .poll(
      () =>
        state.taskPosts.filter(
          (t) => t.projectId === projectId && t.type === type,
        ).length,
      { timeout: 15_000 },
    )
    .toBe(1);
  const post = [...state.taskPosts]
    .reverse()
    .find((t) => t.projectId === projectId && t.type === type);
  expect(post, `须创建 ${type} 任务`).toBeTruthy();
  const taskId = post!.taskId;
  const row = state.activeTasks[taskId];
  expect(row.status === "pending" || row.status === "running").toBe(true);
  await expect
    .poll(() => state.taskDetailGate[taskId]?.waiterCount() ?? 0, {
      timeout: 15_000,
    })
    .toBeGreaterThanOrEqual(1);
  return post!;
}

/**
 * 用途：B 就绪后释放 A 任务 success；返回释放前 A/B getLog 基线。
 * B11 固定顺序：先注册精确 waitForResponse → gate.release → response.finished
 * → 消费 body → route fulfilled 台账精确 +1 → continuation barrier。
 * 同项目 +1 GET：opts.expectEditorGetProjectId 在 release 前注册 editor-state GET。
 * M3：可选 result 精确注入（managed 成功须 engine/fileCount/chars 三键）。
 */
async function releaseTaskSuccess(
  page: Page,
  state: ProbeState,
  taskId: string,
  message: string,
  result?: Record<string, unknown> | null,
  opts?: { expectEditorGetProjectId?: string },
): Promise<{ getsA: number; getsB: number; getsAll: number }> {
  const row = state.activeTasks[taskId];
  expect(row, "任务必须存在").toBeTruthy();
  const getsA = countGets(state, row.projectId);
  const otherIds = state.projects
    .map((p) => p.id)
    .filter((id) => id !== row.projectId);
  const getsB = otherIds.length ? countGets(state, otherIds[0]) : 0;
  const getsAll = state.getLog.length;

  const detailPath = `/api/projects/${row.projectId}/tasks/${taskId}`;
  const fulfilledKey = `${row.projectId}/${taskId}`;
  const fulfilledBefore = state.taskDetailFulfilledLog.filter(
    (k) => k === fulfilledKey,
  ).length;

  const detailRespP = page.waitForResponse(
    (resp) => matchApiResponse(resp, "GET", detailPath),
    { timeout: 20_000 },
  );

  let editorRespP: Promise<import("@playwright/test").Response> | null = null;
  const editorPid = opts?.expectEditorGetProjectId;
  if (editorPid) {
    const editorPath = `/api/projects/${editorPid}/editor-state`;
    editorRespP = page.waitForResponse(
      (resp) => matchApiResponse(resp, "GET", editorPath),
      { timeout: 20_000 },
    );
  }

  row.status = "success";
  row.progress = 100;
  row.message = message;
  row.result =
    result !== undefined
      ? result
      : {
          generated: 1,
          note: "v1g-success",
        };
  const gate = state.taskDetailGate[taskId];
  expect(gate, "详情 HoldGate 必须存在").toBeTruthy();
  gate.release();

  const detailResp = await detailRespP;
  await detailResp.finished();
  try {
    await detailResp.json();
  } catch {
    await detailResp.text();
  }
  await expect
    .poll(
      () =>
        state.taskDetailFulfilledLog.filter((k) => k === fulfilledKey).length,
      { timeout: 10_000 },
    )
    .toBe(fulfilledBefore + 1);

  if (editorRespP) {
    const editorResp = await editorRespP;
    await editorResp.finished();
    try {
      await editorResp.json();
    } catch {
      await editorResp.text();
    }
  }

  await waitContinuationBarrier(page);
  return { getsA, getsB, getsAll };
}

/**
 * B14：释放挂起的文件/图片 POST。
 * 成功路径（file + status<400）：release 前注册精确 A files GET response，
 * 依次 POST finished/body/fulfilled+1 → files GET finished/body/fulfilled+1 → barrier。
 * 400 失败路径不得等待不存在的 files GET。image 无 refreshFiles，不注册 files GET。
 */
async function releaseHeldUpload(
  page: Page,
  state: ProbeState,
  kind: "file" | "image",
  projectId: string,
  pending: { status: number; body: unknown },
  opts?: { expectFilesGetRefresh?: boolean },
): Promise<void> {
  const pathname =
    kind === "file"
      ? `/api/projects/${projectId}/files`
      : `/api/projects/${projectId}/images`;
  const fulfilledLog =
    kind === "file" ? state.filePostFulfilledLog : state.imagePostFulfilledLog;
  const before = fulfilledLog.filter((id) => id === projectId).length;
  if (kind === "file") {
    state.uploadFilePending[projectId] = pending;
  } else {
    state.uploadImagePending[projectId] = pending;
  }

  // 生产 uploadFile 成功后 await refreshFiles；失败/图片路径无 files GET
  const expectFilesGet =
    opts?.expectFilesGetRefresh ??
    (kind === "file" && pending.status < 400);
  const filesPath = `/api/projects/${projectId}/files`;
  const filesFulfilledBefore = countPathLedger(
    state.filesGetFulfilledLog,
    projectId,
    "GET",
    filesPath,
  );
  let filesRespP: Promise<import("@playwright/test").Response> | null = null;
  if (expectFilesGet) {
    filesRespP = page.waitForResponse(
      (resp) => matchApiResponse(resp, "GET", filesPath),
      { timeout: 20_000 },
    );
  }

  const respP = page.waitForResponse(
    (resp) => matchApiResponse(resp, "POST", pathname),
    { timeout: 20_000 },
  );
  const gate =
    kind === "file"
      ? state.uploadFileGate[projectId]
      : state.uploadImageGate[projectId];
  expect(gate, `${kind} HoldGate 必须存在`).toBeTruthy();
  gate.release();
  const resp = await respP;
  await resp.finished();
  try {
    await resp.json();
  } catch {
    await resp.text();
  }
  await expect
    .poll(
      () => fulfilledLog.filter((id) => id === projectId).length,
      { timeout: 10_000 },
    )
    .toBe(before + 1);

  if (filesRespP) {
    const filesResp = await filesRespP;
    await filesResp.finished();
    try {
      await filesResp.json();
    } catch {
      await filesResp.text();
    }
    await expect
      .poll(
        () =>
          countPathLedger(
            state.filesGetFulfilledLog,
            projectId,
            "GET",
            filesPath,
          ),
        { timeout: 10_000 },
      )
      .toBe(filesFulfilledBefore + 1);
  }

  await waitContinuationBarrier(page);
}

/**
 * B18：上传 POST 精确 1 请求 + 精确 1 waiter（禁止 >=1）。
 * postsBefore 为 setInputFiles 前该 project 的 POST 台账基线。
 */
async function waitUploadExactHeld(
  state: ProbeState,
  kind: "file" | "image",
  projectId: string,
  postsBefore: number,
) {
  const postLog =
    kind === "file" ? state.filePostLog : state.imagePostLog;
  await expect
    .poll(
      () => postLog.filter((x) => x.projectId === projectId).length,
      { timeout: 15_000 },
    )
    .toBe(postsBefore + 1);
  await expect
    .poll(
      () => {
        const gate =
          kind === "file"
            ? state.uploadFileGate[projectId]
            : state.uploadImageGate[projectId];
        return gate?.waiterCount() ?? 0;
      },
      { timeout: 15_000 },
    )
    .toBe(1);
}

/**
 * B19：显式释放并消费挂起的 files GET（不得仅靠 afterEach unrouteAll）。
 */
async function releaseHeldFilesGet(
  page: Page,
  state: ProbeState,
  projectId: string,
): Promise<void> {
  const pathname = `/api/projects/${projectId}/files`;
  const gate = state.filesGetGate[projectId];
  if (!gate || gate.isReleased()) {
    gate?.release();
    return;
  }
  if (gate.waiterCount() === 0) {
    gate.release();
    return;
  }
  const before = countPathLedger(
    state.filesGetFulfilledLog,
    projectId,
    "GET",
    pathname,
  );
  const respP = page.waitForResponse(
    (resp) => matchApiResponse(resp, "GET", pathname),
    { timeout: 20_000 },
  );
  gate.release();
  const resp = await respP;
  await resp.finished();
  try {
    await resp.json();
  } catch {
    await resp.text();
  }
  await expect
    .poll(
      () =>
        countPathLedger(state.filesGetFulfilledLog, projectId, "GET", pathname),
      { timeout: 10_000 },
    )
    .toBe(before + 1);
  await waitContinuationBarrier(page);
}

/**
 * B19：显式释放并消费挂起的 tasks 列表 GET。
 */
async function releaseHeldTasksList(
  page: Page,
  state: ProbeState,
  projectId: string,
): Promise<void> {
  const pathname = `/api/projects/${projectId}/tasks`;
  const gate = state.tasksGetGate[projectId];
  if (!gate || gate.isReleased()) {
    gate?.release();
    return;
  }
  if (gate.waiterCount() === 0) {
    gate.release();
    return;
  }
  const before = countPathLedger(
    state.tasksListFulfilledLog,
    projectId,
    "GET",
    pathname,
  );
  const respP = page.waitForResponse(
    (resp) => matchApiResponse(resp, "GET", pathname),
    { timeout: 20_000 },
  );
  gate.release();
  const resp = await respP;
  await resp.finished();
  try {
    await resp.json();
  } catch {
    await resp.text();
  }
  await expect
    .poll(
      () =>
        countPathLedger(
          state.tasksListFulfilledLog,
          projectId,
          "GET",
          pathname,
        ),
      { timeout: 10_000 },
    )
    .toBe(before + 1);
  await waitContinuationBarrier(page);
}

/** M3 managed 成功 result 精确三键。 */
const MANAGED_SUCCESS_RESULT = {
  engine: "managed",
  fileCount: 1,
  chars: 128,
} as const;

/**
 * 用途：软切红测核心断言——释放后 A/B editor-state GET 增量均为 0，B 无 sticky loading。
 * 当前生产预期失败：旧 success 触发额外 GET 或 B loading 粘住。
 */
async function assertSoftSwitchNoLateHydration(opts: {
  page: Page;
  state: ProbeState;
  projectA: string;
  projectB: string;
  baselineA: number;
  baselineB: number;
  bName: string;
  kind: Kind;
  forbidTexts: string[];
}) {
  const {
    page,
    state,
    projectA,
    projectB,
    baselineA,
    baselineB,
    bName,
    kind,
    forbidTexts,
  } = opts;

  // releaseTaskSuccess 已完成：详情 response + fulfilled +1 + continuation barrier
  // 此后锁 A/B GET 零增量（禁止固定时间窗；禁止仅立即 count 冒充完成——barrier 已排空任务队列）
  expect(countGets(state, projectA)).toBe(baselineA);
  expect(countGets(state, projectB)).toBe(baselineB);
  await waitContinuationBarrier(page);
  expect(countGets(state, projectA)).toBe(baselineA);
  expect(countGets(state, projectB)).toBe(baselineB);

  if (kind === "technical") {
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, bName);
  } else {
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, bName);
  }

  for (const t of forbidTexts) {
    await expect(page.getByText(t)).toHaveCount(0);
  }

  expect(state.externalHits, "禁止外网请求").toEqual([]);
}

// 禁止 serial：首红不得 skip 后续用例；加固后参考 7 failed / 2 passed（实际为准）

test.describe("V1-G writer 任务迟到 success 软切水合", () => {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => undefined);
  });

  test("T1 技术 parse：A running 软切 B 后释放 success → 零额外 GET/零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB]);
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G技术甲");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    const getsAInit = countGets(state, TECH_A);
    await page.getByRole("button", { name: "轻量解析" }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    expect(post.payload).toEqual({ engine: "lightweight" });

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    await expectTechReady(page, "V1G技术乙");
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(getsAInit);
    expect(baselineB).toBeGreaterThanOrEqual(1);

    await releaseTaskSuccess(page, state, post.taskId, "解析完成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙",
      kind: "technical",
      forbidTexts: [PARSE_MD_A, "解析完成，请查看右侧预览"],
    });
  });

  test("T2 技术 analyze：真实点击 AI 招标分析；type 精确 analyze", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲分析",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙分析",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲分析");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");
    expect(post.type).toBe("analyze");

    await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
    await expectTechReady(page, "V1G技术乙分析");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(page, state, post.taskId, "招标分析已写入结构化结果");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙分析",
      kind: "technical",
      forbidTexts: [OVERVIEW_A, "招标分析已写入结构化结果"],
    });
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );
  });

  test("T3 技术 outline：真实点击 AI 生成大纲；type 精确 outline", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲大纲",
      kind: "technical",
      technicalPlanStep: 3,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙大纲",
      kind: "technical",
      technicalPlanStep: 3,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/outline`);
    await expectTechReady(page, "V1G技术甲大纲");

    await page.getByRole("button", { name: /AI 生成大纲/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "outline");
    expect(post.type).toBe("outline");

    await softNavigate(page, `/technical-plan/${TECH_B}/outline`);
    await expectTechReady(page, "V1G技术乙大纲");

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(page, state, post.taskId, "大纲与章节列表已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙大纲",
      kind: "technical",
      forbidTexts: ["大纲与章节列表已生成"],
    });
  });

  test("T4 技术 chapters：真实点击生成全部空章节；onlyEmpty + type 精确", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲全书",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙全书",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "V1G技术甲全书");

    await page.getByRole("button", { name: /生成全部空章节/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "chapters");
    expect(post.type).toBe("chapters");
    expect(post.payload).toEqual({ onlyEmpty: true });

    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "V1G技术乙全书");
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(page, state, post.taskId, "全书空章生成完成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙全书",
      kind: "technical",
      forbidTexts: [CHAPTER_BODY_A, "全书空章生成完成"],
    });
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);
  });

  test("T5 技术 chapter：真实选章并 AI 生成本章；chapterId 精确", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲单章",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙单章",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "V1G技术甲单章");
    // 选中章节（侧栏标题）
    await page.getByText(CHAPTER_TITLE, { exact: true }).first().click();
    const genBtn = page.getByRole("button", { name: /AI 生成本章/ });
    await expect(genBtn).toBeEnabled({ timeout: 10_000 });
    await genBtn.click();

    const post = await waitTaskRunningHeld(state, TECH_A, "chapter");
    expect(post.type).toBe("chapter");
    expect(post.payload).toEqual({ chapterId: "n1" });

    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "V1G技术乙单章");
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(page, state, post.taskId, "章节已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙单章",
      kind: "technical",
      forbidTexts: [CHAPTER_BODY_A, "章节已生成"],
    });
  });

  test("T6 商务 biz_qualify：软切后零额外 GET、零 step/project 污染、零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G商务甲",
      kind: "business",
      technicalPlanStep: 1,
    });
    const projectB = makeProject({
      id: BIZ_B,
      name: "V1G商务乙",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/qualify`);
    await expectBizReady(page, "V1G商务甲");
    await expect(page.getByText(QUALIFY_A)).toBeVisible();

    await page.getByRole("button", { name: "生成资格草稿" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "biz_qualify");
    expect(post.type).toBe("biz_qualify");

    const patchBefore = state.patchLog.length;
    await softNavigate(page, `/business-bid/${BIZ_B}/qualify`);
    await expectBizReady(page, "V1G商务乙");
    await expect(page.getByText(QUALIFY_B)).toBeVisible();

    const baselineA = countGets(state, BIZ_A);
    const baselineB = countGets(state, BIZ_B);
    const stepBBefore = projectB.technicalPlanStep;

    await releaseTaskSuccess(page, state, post.taskId, "资格草稿已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: BIZ_A,
      projectB: BIZ_B,
      baselineA,
      baselineB,
      bName: "V1G商务乙",
      kind: "business",
      forbidTexts: [QUALIFY_A, MARKDOWN_A],
    });
    await expect(page.getByText(QUALIFY_B)).toBeVisible();

    // 不得用 A 的 step PATCH 污染 B 项目对象
    const patchesAfter = state.patchLog.slice(patchBefore);
    for (const p of patchesAfter) {
      expect(p.projectId, "迟到 success 不得 PATCH 到任意项目步进").not.toBe(
        BIZ_A,
      );
      expect(p.projectId).not.toBe(BIZ_B);
    }
    expect(projectB.technicalPlanStep).toBe(stepBBefore);
    await expect(page.getByRole("heading", { name: "V1G商务乙" })).toBeVisible();
  });

  test("T7 同项目技术 analyze success：editor-state GET 精确 +1 并水合", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G同项目技术",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G同项目技术");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");

    // 任务仍 running：GET 不得提前增加（相对进入 gate 后的稳定基线）
    const baseline = countGets(state, TECH_A);

    // 释放前更新服务端权威正文，成功水合应看到新概述
    const HYDRATED = "V1G_SAME_PROJECT_HYDRATED_OVERVIEW";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      analysisOverview: HYDRATED,
      analysis: {
        ...state.editorById[TECH_A].analysis,
        overview: HYDRATED,
      },
    };

    await releaseTaskSuccess(
      page,
      state,
      post.taskId,
      "招标分析已写入结构化结果",
      undefined,
      { expectEditorGetProjectId: TECH_A },
    );

    await expect
      .poll(() => countGets(state, TECH_A), { timeout: 15_000 })
      .toBe(baseline + 1);

    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      HYDRATED,
    );
    await expect(
      page.getByRole("status").filter({ hasText: "招标分析已写入结构化结果" }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, "V1G同项目技术");
  });

  test("T8 同项目商务 biz_qualify success：editor-state GET 精确 +1 并保持步进", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G同项目商务",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA]);
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/qualify`);
    await expectBizReady(page, "V1G同项目商务");
    await expect(page.getByText(QUALIFY_A)).toBeVisible();

    await page.getByRole("button", { name: "生成资格草稿" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "biz_qualify");

    const baseline = countGets(state, BIZ_A);

    const HYDRATED_Q = "V1G_SAME_BIZ_HYDRATED_QUALIFY";
    state.editorById[BIZ_A] = {
      ...state.editorById[BIZ_A],
      businessQualify: [
        {
          id: "q1",
          requirement: HYDRATED_Q,
          status: "matched",
          evidence: "e2e",
          notes: "",
        },
      ],
    };

    const patchBefore = state.patchLog.length;
    await releaseTaskSuccess(
      page,
      state,
      post.taskId,
      "资格草稿已生成",
      undefined,
      { expectEditorGetProjectId: BIZ_A },
    );

    await expect
      .poll(() => countGets(state, BIZ_A), { timeout: 15_000 })
      .toBe(baseline + 1);

    await expect(page.getByText(HYDRATED_Q)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, "V1G同项目商务");

    // 同项目 success 允许既有 STEP_BY_TASK 步进（biz_qualify → 2）
    await expect
      .poll(
        () =>
          state.patchLog
            .slice(patchBefore)
            .filter((p) => p.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(1);
    const stepPatch = state.patchLog
      .slice(patchBefore)
      .find(
        (p) =>
          p.projectId === BIZ_A &&
          typeof p.body.technicalPlanStep === "number",
      );
    expect(stepPatch?.body.technicalPlanStep).toBe(2);
    expect(projectA.technicalPlanStep).toBe(2);
  });

  test("T9 技术 analyze ABA：A running→B 就绪→回 A 新会话就绪后释放旧 success → GET+0/零 tip/零 sticky", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲ABA",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙ABA",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    // 1) A：真实点击 analyze，任务进入 HoldGate（精确单 POST）
    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");
    expect(post.type).toBe("analyze");
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);

    // 2) 软切 B：完整就绪且可编辑
    await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
    await expectTechReady(page, "V1G技术乙ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );

    // 3) 软切回 A：新会话初始 GET 完成且可编辑（旧 task 仍 held）
    await softNavigate(page, `/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    // 若旧 success 仍触发 reload，会拉到毒化正文 → 证明覆盖新会话
    const POISON = "V1G_ABA_POISON_OLD_TASK_OVERVIEW";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      analysisOverview: POISON,
      analysis: {
        ...state.editorById[TECH_A].analysis,
        overview: POISON,
      },
    };

    // 4) 记录 A/B 基线后释放「旧」task success
    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(2);
    expect(baselineB).toBeGreaterThanOrEqual(1);
    // 旧任务仍挂起，未新增 analyze POST
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);
    expect(state.activeTasks[post.taskId]?.status).toMatch(/pending|running/);

    await releaseTaskSuccess(page, state, post.taskId, "招标分析已写入结构化结果");

    // 5) A/B GET 增量精确 0；零 tip/零 sticky；新会话正文不被旧刷新覆盖
    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术甲ABA",
      kind: "technical",
      forbidTexts: [
        POISON,
        OVERVIEW_B,
        "招标分析已写入结构化结果",
      ],
    });
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    // 全程仅一次 analyze POST（精确单 POST 锁）
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);
  });

  // ---------- M3：managed 成功水合与软切（复用既有 HoldGate，禁止复制桩） ----------

  test("M3 同项目技术 managed success：payload 精确、result 三键、editor-state GET +1 水合正文", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G M3 同项目技术 managed",
      kind: "technical",
    });
    const state = createProbeState([projectA], { parseStrategy: "managed" });
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G M3 同项目技术 managed");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    // M3 技术入口 exact「开始解析」（禁止宽正则旧 UI 冒充）
    await page.getByRole("button", { name: "开始解析", exact: true }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    // 精确 payload；禁止 lightweight 冒充
    expect(post.payload).toEqual({ engine: "managed" });
    expect(post.type).toBe("parse");
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "parse",
      ).length,
    ).toBe(1);

    const baseline = countGets(state, TECH_A);

    const HYDRATED_MD = "V1G_M3_MANAGED_SAME_PROJECT_HYDRATED_MD";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      parsedMarkdown: HYDRATED_MD,
    };

    await releaseTaskSuccess(
      page,
      state,
      post.taskId,
      "解析完成，请查看右侧预览",
      { ...MANAGED_SUCCESS_RESULT },
      { expectEditorGetProjectId: TECH_A },
    );

    // result 精确三键（释放后任务详情可读）
    const done = state.activeTasks[post.taskId];
    expect(done.result).toEqual({
      engine: "managed",
      fileCount: 1,
      chars: 128,
    });
    expect(Object.keys(done.result || {}).sort()).toEqual(
      ["chars", "engine", "fileCount"].sort(),
    );

    await expect
      .poll(() => countGets(state, TECH_A), { timeout: 15_000 })
      .toBe(baseline + 1);

    await expect(page.getByText(HYDRATED_MD)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, "V1G M3 同项目技术 managed");
    // 全程不得出现 lightweight parse POST
    expect(
      state.taskPosts.filter(
        (t) =>
          t.projectId === TECH_A &&
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      ).length,
    ).toBe(0);
    expect(state.externalHits).toEqual([]);
  });

  test("M3 技术 A managed running→切 B→释放 A success：A/B GET 零增量、B 零污染、零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G M3 技术甲 managed",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G M3 技术乙 managed",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB], {
      parseStrategy: "managed",
    });
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G M3 技术甲 managed");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    // M3 技术入口 exact「开始解析」
    await page.getByRole("button", { name: "开始解析", exact: true }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    expect(post.payload).toEqual({ engine: "managed" });

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    await expectTechReady(page, "V1G M3 技术乙 managed");
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(1);
    expect(baselineB).toBeGreaterThanOrEqual(1);

    // 毒化 A 正文：若迟到 success 错误水合到 B 或粘住 loading 可观测
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      parsedMarkdown: "V1G_M3_LATE_A_SHOULD_NOT_LEAK_TO_B",
    };

    await releaseTaskSuccess(
      page,
      state,
      post.taskId,
      "解析完成，请查看右侧预览",
      { ...MANAGED_SUCCESS_RESULT },
    );

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G M3 技术乙 managed",
      kind: "technical",
      forbidTexts: [
        PARSE_MD_A,
        "V1G_M3_LATE_A_SHOULD_NOT_LEAK_TO_B",
        "解析完成，请查看右侧预览",
      ],
    });
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "parse",
      ).length,
    ).toBe(1);
    expect(state.externalHits).toEqual([]);
  });

  test("M3 同项目商务 managed success：payload 精确、GET +1 水合、步进精确 1", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G M3 同项目商务 managed",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA], { parseStrategy: "managed" });
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/parse`);
    await expectBizReady(page, "V1G M3 同项目商务 managed");
    // 初始：aria-label 精确 textbox value（禁止仅 getByText 冒充水合）
    await expect(
      page.getByRole("textbox", { name: "商务条款解析 Markdown" }),
    ).toHaveValue(MARKDOWN_A);

    // 商务标「整段重解析」走 managed
    await page.getByRole("button", { name: "整段重解析" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "parse");
    expect(post.payload).toEqual({ engine: "managed" });
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === BIZ_A && t.type === "parse",
      ).length,
    ).toBe(1);

    const baseline = countGets(state, BIZ_A);

    const HYDRATED_BIZ = "V1G_M3_BIZ_MANAGED_HYDRATED_MD";
    state.editorById[BIZ_A] = {
      ...state.editorById[BIZ_A],
      parsedMarkdown: HYDRATED_BIZ,
    };

    const patchBefore = state.patchLog.length;
    await releaseTaskSuccess(
      page,
      state,
      post.taskId,
      "解析完成",
      { ...MANAGED_SUCCESS_RESULT },
      { expectEditorGetProjectId: BIZ_A },
    );

    const done = state.activeTasks[post.taskId];
    expect(done.result).toEqual({
      engine: "managed",
      fileCount: 1,
      chars: 128,
    });

    await expect
      .poll(() => countGets(state, BIZ_A), { timeout: 15_000 })
      .toBe(baseline + 1);

    // 水合：textbox value 精确等于新正文
    await expect(
      page.getByRole("textbox", { name: "商务条款解析 Markdown" }),
    ).toHaveValue(HYDRATED_BIZ);
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, "V1G M3 同项目商务 managed");

    // 本轮 step PATCH 精确 1 条：projectId=BIZ_A，technicalPlanStep=1（禁止空循环假绿）
    await expect
      .poll(
        () =>
          state.patchLog.slice(patchBefore).filter((p) => p.projectId === BIZ_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    const onlyPatch = state.patchLog
      .slice(patchBefore)
      .find((p) => p.projectId === BIZ_A);
    expect(onlyPatch, "须存在 BIZ_A step PATCH").toBeTruthy();
    expect(onlyPatch!.projectId).toBe(BIZ_A);
    expect(onlyPatch!.body.technicalPlanStep).toBe(1);
    const bodyKeys = Object.keys(onlyPatch!.body).sort();
    if (bodyKeys.length === 1) {
      expect(onlyPatch!.body).toEqual({ technicalPlanStep: 1 });
    } else {
      // 多键时：精确 technicalPlanStep，并锁允许键集合
      const allowed = new Set(["technicalPlanStep"]);
      for (const k of bodyKeys) {
        expect(allowed.has(k), `PATCH body 含未允许键: ${k}`).toBe(true);
      }
    }
    // 本轮不得 PATCH 到其他项目
    expect(state.patchLog.slice(patchBefore).length).toBe(1);
    expect(projectA.technicalPlanStep).toBe(1);
    expect(
      state.taskPosts.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      ).length,
    ).toBe(0);
    expect(state.externalHits).toEqual([]);
  });


  // ---------- M3-T6..M3-T11：A9/A10/uploadImage 跨项目上传红门（标题唯一，failure-first） ----------

  test("M3-T6 技术 A9：真实 A 任务链 marker 可见后软切 B；files Hold+tasks 500+B 上传 hold → 零 A 泄漏、解析 disabled", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G A9 技术甲",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G A9 技术乙",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB]);
    state.filesByProject[TECH_A] = [
      {
        id: "file_tech_a_only",
        filename: FILE_NAME_TECH_A,
        sizeBytes: 2048,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    ];
    state.filesByProject[TECH_B] = [];
    state.filesGetMode[TECH_B] = "hold";
    state.tasksGetMode[TECH_B] = "fail";
    state.uploadFileMode[TECH_B] = "hold";
    ensureProjectGate(state.filesGetGate, TECH_B);
    ensureProjectGate(state.uploadFileGate, TECH_B);

    const consoleLines = collectConsole(page);
    await installRoutes(page, state);
    await installRouteAwareDomProbe(page, TECH_B, [
      FILE_NAME_TECH_A,
      TASK_MSG_TECH_A,
    ]);

    // B16：真实 A 任务链证明 marker 进入客户端可见消费链（禁止 seed/条件跳过）
    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G A9 技术甲");
    await expect(page.getByText(FILE_NAME_TECH_A)).toBeVisible();
    await page.getByRole("button", { name: "轻量解析" }).click();
    const postA = await waitTaskRunningHeld(state, TECH_A, "parse");
    await releaseTaskSuccess(page, state, postA.taskId, TASK_MSG_TECH_A);
    // lastTask 摘要 + recentTasks 列表可同时含 marker；禁止条件跳过，至少一处可见
    await expect
      .poll(async () => page.getByText(TASK_MSG_TECH_A).count(), {
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(1);
    await expect(page.getByText(TASK_MSG_TECH_A).first()).toBeVisible();
    await expect(page.getByText(FILE_NAME_TECH_A).first()).toBeVisible();

    const filesPathB = `/api/projects/${TECH_B}/files`;
    const tasksPathB = `/api/projects/${TECH_B}/tasks`;
    const filesInitBefore = countPathLedger(
      state.filesGetInitLog,
      TECH_B,
      "GET",
      filesPathB,
    );
    const filesWaiterBefore = countPathLedger(
      state.filesGetWaiterLog,
      TECH_B,
      "GET",
      filesPathB,
    );
    const filesFulfilledBefore = countPathLedger(
      state.filesGetFulfilledLog,
      TECH_B,
      "GET",
      filesPathB,
    );
    const tasksInitBefore = countPathLedger(
      state.tasksListInitLog,
      TECH_B,
      "GET",
      tasksPathB,
    );
    const tasksFulfilledBefore = countPathLedger(
      state.tasksListFulfilledLog,
      TECH_B,
      "GET",
      tasksPathB,
    );
    const postsBBefore = state.filePostLog.filter(
      (x) => x.projectId === TECH_B,
    ).length;

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    // B20：禁止 softNavigate 后、B ready 前主动 rescan（URL 已切 B、旧 A DOM 仍在会假红）
    await expect(page.getByRole("heading", { name: "V1G A9 技术乙" })).toBeVisible({
      timeout: 20_000,
    });
    // B ready 后 arm：清空 hits → 启用 → 同步扫描当前 B DOM；observer 持续观测
    await armDomProbe(page);

    // B18：B files hold 发起+waiter 精确 +1；tasks fail 发起+fulfilled 精确 +1；files 尚未 fulfilled
    await expect
      .poll(
        () =>
          countPathLedger(state.filesGetInitLog, TECH_B, "GET", filesPathB),
        { timeout: 15_000 },
      )
      .toBe(filesInitBefore + 1);
    await expect
      .poll(
        () =>
          countPathLedger(state.filesGetWaiterLog, TECH_B, "GET", filesPathB),
        { timeout: 15_000 },
      )
      .toBe(filesWaiterBefore + 1);
    expect(
      countPathLedger(state.filesGetFulfilledLog, TECH_B, "GET", filesPathB),
    ).toBe(filesFulfilledBefore);
    await expect
      .poll(
        () =>
          countPathLedger(state.tasksListInitLog, TECH_B, "GET", tasksPathB),
        { timeout: 15_000 },
      )
      .toBe(tasksInitBefore + 1);
    await expect
      .poll(
        () =>
          countPathLedger(
            state.tasksListFulfilledLog,
            TECH_B,
            "GET",
            tasksPathB,
          ),
        { timeout: 15_000 },
      )
      .toBe(tasksFulfilledBefore + 1);

    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_TECH_B,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 v1g-b-upload"),
    });
    await waitUploadExactHeld(state, "file", TECH_B, postsBBefore);
    await expect(page.getByText("任务进行中")).toBeVisible({ timeout: 10_000 });
    // B20：已 arm，observer 同步检查 characterData/attributes/addedNodes/current DOM

    await expect(page.getByText(FILE_NAME_TECH_A)).toHaveCount(0);
    await expect(page.getByText(TASK_MSG_TECH_A)).toHaveCount(0);
    await expect(page.getByText("尚未上传文件")).toBeVisible();
    const parseBtn = page.getByRole("button", {
      name: /^(轻量解析|开始解析|处理中…|正在读取解析策略)$/,
    });
    await expect(parseBtn).toBeDisabled();
    const leakHits = await readDomProbeHits(page);
    expect(leakHits.filter((h) => h.includes(FILE_NAME_TECH_A))).toEqual([]);
    expect(leakHits.filter((h) => h.includes(TASK_MSG_TECH_A))).toEqual([]);
    expect(consoleLines.join("\n")).not.toContain(FILE_NAME_TECH_A);
    expect(consoleLines.join("\n")).not.toContain(TASK_MSG_TECH_A);
    expect(state.externalHits).toEqual([]);

    // B19：关键断言后显式 release/消费 held，不得仅靠 unrouteAll
    await releaseHeldFilesGet(page, state, TECH_B);
    await releaseHeldUpload(page, state, "file", TECH_B, {
      status: 201,
      body: {
        id: "file_tech_b_cleanup",
        filename: UPLOAD_NAME_TECH_B,
        sizeBytes: 80,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });
  });

  test("M3-T7 商务 A9：真实 A 任务链 lastTask marker 可见后软切 B；files 500+tasks Hold → 零 A 泄漏", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G A9 商务甲",
      kind: "business",
      technicalPlanStep: 1,
    });
    const projectB = makeProject({
      id: BIZ_B,
      name: "V1G A9 商务乙",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA, projectB]);
    state.filesByProject[BIZ_A] = [
      {
        id: "file_biz_a_only",
        filename: FILE_NAME_BIZ_A,
        sizeBytes: 2048,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    ];
    state.filesByProject[BIZ_B] = [];
    state.filesGetMode[BIZ_B] = "fail";
    state.tasksGetMode[BIZ_B] = "hold";
    ensureProjectGate(state.tasksGetGate, BIZ_B);

    const consoleLines = collectConsole(page);
    await installRoutes(page, state);
    await installRouteAwareDomProbe(page, BIZ_B, [
      FILE_NAME_BIZ_A,
      TASK_MSG_BIZ_A,
    ]);

    // B16：商务仅渲染 lastTask；必须经真实 parse 链证明 marker 可见
    await page.goto(`/business-bid/${BIZ_A}/parse`);
    await expectBizReady(page, "V1G A9 商务甲");
    await expect(page.getByText(FILE_NAME_BIZ_A)).toBeVisible();
    await page.getByRole("button", { name: "整段重解析" }).click();
    const postA = await waitTaskRunningHeld(state, BIZ_A, "parse");
    await releaseTaskSuccess(page, state, postA.taskId, TASK_MSG_BIZ_A);
    await expect
      .poll(async () => page.getByText(TASK_MSG_BIZ_A).count(), {
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(1);
    await expect(page.getByText(TASK_MSG_BIZ_A).first()).toBeVisible();

    const filesPathB = `/api/projects/${BIZ_B}/files`;
    const tasksPathB = `/api/projects/${BIZ_B}/tasks`;
    const filesInitBefore = countPathLedger(
      state.filesGetInitLog,
      BIZ_B,
      "GET",
      filesPathB,
    );
    const filesFulfilledBefore = countPathLedger(
      state.filesGetFulfilledLog,
      BIZ_B,
      "GET",
      filesPathB,
    );
    const tasksInitBefore = countPathLedger(
      state.tasksListInitLog,
      BIZ_B,
      "GET",
      tasksPathB,
    );
    const tasksWaiterBefore = countPathLedger(
      state.tasksListWaiterLog,
      BIZ_B,
      "GET",
      tasksPathB,
    );
    const tasksFulfilledBefore = countPathLedger(
      state.tasksListFulfilledLog,
      BIZ_B,
      "GET",
      tasksPathB,
    );

    await softNavigate(page, `/business-bid/${BIZ_B}/parse`);
    // B20：禁止 softNavigate 后、B ready 前主动 rescan
    await expect(page.getByRole("heading", { name: "V1G A9 商务乙" })).toBeVisible({
      timeout: 20_000,
    });
    await armDomProbe(page);

    // B18：B files fail 发起+fulfilled +1；tasks hold 发起+waiter +1、fulfilled 未增
    await expect
      .poll(
        () =>
          countPathLedger(state.filesGetInitLog, BIZ_B, "GET", filesPathB),
        { timeout: 15_000 },
      )
      .toBe(filesInitBefore + 1);
    await expect
      .poll(
        () =>
          countPathLedger(
            state.filesGetFulfilledLog,
            BIZ_B,
            "GET",
            filesPathB,
          ),
        { timeout: 15_000 },
      )
      .toBe(filesFulfilledBefore + 1);
    await expect
      .poll(
        () =>
          countPathLedger(state.tasksListInitLog, BIZ_B, "GET", tasksPathB),
        { timeout: 15_000 },
      )
      .toBe(tasksInitBefore + 1);
    await expect
      .poll(
        () =>
          countPathLedger(state.tasksListWaiterLog, BIZ_B, "GET", tasksPathB),
        { timeout: 15_000 },
      )
      .toBe(tasksWaiterBefore + 1);
    expect(
      countPathLedger(state.tasksListFulfilledLog, BIZ_B, "GET", tasksPathB),
    ).toBe(tasksFulfilledBefore);

    await expect(page.getByText(FILE_NAME_BIZ_A)).toHaveCount(0);
    await expect(page.getByText(TASK_MSG_BIZ_A)).toHaveCount(0);
    await expect(page.getByText("尚未上传")).toBeVisible();
    await expect(page.getByRole("button", { name: "整段重解析" })).toBeDisabled();
    const leakHits = await readDomProbeHits(page);
    expect(leakHits.filter((h) => h.includes(FILE_NAME_BIZ_A))).toEqual([]);
    expect(leakHits.filter((h) => h.includes(TASK_MSG_BIZ_A))).toEqual([]);
    expect(consoleLines.join("\n")).not.toContain(FILE_NAME_BIZ_A);
    expect(state.externalHits).toEqual([]);

    // B19：显式释放 B tasks hold
    await releaseHeldTasksList(page, state, BIZ_B);
  });

  test("M3-T8 技术 A10-success：A/B POST files Hold；释放 A 201 含 files GET 因果门；B busy 保持、策略/task +0", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G A10 技术甲",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G A10 技术乙",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB]);
    state.uploadFileMode[TECH_A] = "hold";
    state.uploadFileMode[TECH_B] = "hold";
    ensureProjectGate(state.uploadFileGate, TECH_A);
    ensureProjectGate(state.uploadFileGate, TECH_B);

    const consoleLines = collectConsole(page);
    await installRoutes(page, state);
    // B21：page.goto 前安装 route-aware 探针；默认 disarmed；监控唯一 A 上传文件名
    await installRouteAwareDomProbe(page, TECH_B, [UPLOAD_NAME_TECH_A]);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G A10 技术甲");

    const postsABefore = state.filePostLog.filter(
      (x) => x.projectId === TECH_A,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_TECH_A,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 v1g-a-upload"),
    });
    await waitUploadExactHeld(state, "file", TECH_A, postsABefore);

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    await expectTechReady(page, "V1G A10 技术乙");
    // B21：B ready 后、释放 A POST 前 arm（清空 hits → 启用 → 扫当前 B DOM）
    await armDomProbe(page);

    const postsBBefore = state.filePostLog.filter(
      (x) => x.projectId === TECH_B,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_TECH_B,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 v1g-b-upload"),
    });
    await waitUploadExactHeld(state, "file", TECH_B, postsBBefore);
    await expect(page.getByRole("button", { name: "选择文件" })).toBeDisabled();

    const strategyBefore = state.strategyGetLog.length;
    const taskPostsBefore = state.taskPosts.length;
    const filePostsA = state.filePostLog.filter(
      (x) => x.projectId === TECH_A,
    ).length;
    const filePostsB = state.filePostLog.filter(
      (x) => x.projectId === TECH_B,
    ).length;
    // 精确 1 请求
    expect(filePostsA).toBe(postsABefore + 1);
    expect(filePostsB).toBe(postsBBefore + 1);

    // B14：release 内注册 A files GET 并 finished/body/fulfilled+1 后再断言
    await releaseHeldUpload(page, state, "file", TECH_A, {
      status: 201,
      body: {
        id: "file_tech_a_late",
        filename: UPLOAD_NAME_TECH_A,
        sizeBytes: 120,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });

    // B21：释放 A 后同步 hits 精确为空（唯一 UPLOAD_NAME_TECH_A，含 tip 若回显文件名）
    const leakHits = await readDomProbeHits(page);
    expect(leakHits).toEqual([]);
    await expect(page.getByRole("button", { name: "选择文件" })).toBeDisabled();
    await expect(
      page.getByText(new RegExp(`已上传：${UPLOAD_NAME_TECH_A}`)),
    ).toHaveCount(0);
    await expect(page.getByText(UPLOAD_NAME_TECH_A)).toHaveCount(0);
    expect(state.strategyGetLog.length).toBe(strategyBefore);
    expect(state.taskPosts.length).toBe(taskPostsBefore);
    expect(
      state.filePostLog.filter((x) => x.projectId === TECH_A).length,
    ).toBe(filePostsA);
    expect(
      state.filePostLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(filePostsB);
    expect(consoleLines.join("\n")).not.toContain(UPLOAD_NAME_TECH_A);
    expect(state.externalHits).toEqual([]);

    // B19：显式消费 B 上传 hold
    await releaseHeldUpload(page, state, "file", TECH_B, {
      status: 201,
      body: {
        id: "file_tech_b_cleanup",
        filename: UPLOAD_NAME_TECH_B,
        sizeBytes: 80,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });
    await clearDomProbe(page);
  });

  test("M3-T9 商务 A10-success：managed；释放 A 201 含 files GET 因果门；策略 GET/task +0、B busy 保持", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G A10 商务甲",
      kind: "business",
      technicalPlanStep: 1,
    });
    const projectB = makeProject({
      id: BIZ_B,
      name: "V1G A10 商务乙",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA, projectB], {
      parseStrategy: "managed",
    });
    state.uploadFileMode[BIZ_A] = "hold";
    state.uploadFileMode[BIZ_B] = "hold";
    ensureProjectGate(state.uploadFileGate, BIZ_A);
    ensureProjectGate(state.uploadFileGate, BIZ_B);

    const consoleLines = collectConsole(page);
    await installRoutes(page, state);
    // B21：goto 前安装探针；唯一 marker = UPLOAD_NAME_BIZ_A（禁止公共中文文案冒充）
    await installRouteAwareDomProbe(page, BIZ_B, [UPLOAD_NAME_BIZ_A]);

    await page.goto(`/business-bid/${BIZ_A}/parse`);
    await expectBizReady(page, "V1G A10 商务甲");

    const postsABefore = state.filePostLog.filter(
      (x) => x.projectId === BIZ_A,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_BIZ_A,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 v1g-biz-a"),
    });
    await waitUploadExactHeld(state, "file", BIZ_A, postsABefore);

    await softNavigate(page, `/business-bid/${BIZ_B}/parse`);
    await expectBizReady(page, "V1G A10 商务乙");
    // B21：B ready 后、释放 A POST 前 arm
    await armDomProbe(page);

    const postsBBefore = state.filePostLog.filter(
      (x) => x.projectId === BIZ_B,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_BIZ_B,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 v1g-biz-b"),
    });
    await waitUploadExactHeld(state, "file", BIZ_B, postsBBefore);
    await expect(page.getByRole("button", { name: /选择文件|处理中/ })).toBeDisabled();

    const strategyBefore = state.strategyGetLog.length;
    const taskPostsA = state.taskPosts.filter((t) => t.projectId === BIZ_A).length;
    const taskPostsB = state.taskPosts.filter((t) => t.projectId === BIZ_B).length;

    await releaseHeldUpload(page, state, "file", BIZ_A, {
      status: 201,
      body: {
        id: "file_biz_a_late",
        filename: UPLOAD_NAME_BIZ_A,
        sizeBytes: 120,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });

    // B21：释放 A 后同步 hits 精确为空
    const leakHits = await readDomProbeHits(page);
    expect(leakHits).toEqual([]);
    await expect(page.getByRole("button", { name: /选择文件|处理中/ })).toBeDisabled();
    await expect(page.getByText(UPLOAD_NAME_BIZ_A)).toHaveCount(0);
    await expect(page.getByText(/已上传/)).toHaveCount(0);
    expect(state.strategyGetLog.length).toBe(strategyBefore);
    expect(state.taskPosts.filter((t) => t.projectId === BIZ_A).length).toBe(
      taskPostsA,
    );
    expect(state.taskPosts.filter((t) => t.projectId === BIZ_B).length).toBe(
      taskPostsB,
    );
    expect(consoleLines.join("\n")).not.toContain(UPLOAD_NAME_BIZ_A);
    expect(state.externalHits).toEqual([]);

    await releaseHeldUpload(page, state, "file", BIZ_B, {
      status: 201,
      body: {
        id: "file_biz_b_cleanup",
        filename: UPLOAD_NAME_BIZ_B,
        sizeBytes: 80,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });
    await clearDomProbe(page);
  });

  test("M3-T10 商务 A10-failure：释放 A 400 不登记 files GET；pageerror/unhandledrejection 台账皆空；marker 零泄漏", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G A10f 商务甲",
      kind: "business",
      technicalPlanStep: 1,
    });
    const projectB = makeProject({
      id: BIZ_B,
      name: "V1G A10f 商务乙",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA, projectB], {
      parseStrategy: "managed",
    });
    state.uploadFileMode[BIZ_A] = "hold";
    state.uploadFileMode[BIZ_B] = "hold";
    ensureProjectGate(state.uploadFileGate, BIZ_A);
    ensureProjectGate(state.uploadFileGate, BIZ_B);

    const consoleLines = collectConsole(page);
    const errorLedgers = await installPageErrorLedgers(page);
    await installRoutes(page, state);
    await installRouteAwareDomProbe(page, BIZ_B, [UPLOAD_FAIL_MARKER_BIZ_A]);

    await page.goto(`/business-bid/${BIZ_A}/parse`);
    await expectBizReady(page, "V1G A10f 商务甲");
    const postsABefore = state.filePostLog.filter(
      (x) => x.projectId === BIZ_A,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: "biz-a-fail.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 fail-a"),
    });
    await waitUploadExactHeld(state, "file", BIZ_A, postsABefore);

    await softNavigate(page, `/business-bid/${BIZ_B}/parse`);
    // B20：禁止 B ready 前 rescan；ready 后、释放前显式 arm
    await expectBizReady(page, "V1G A10f 商务乙");
    await armDomProbe(page);
    const postsBBefore = state.filePostLog.filter(
      (x) => x.projectId === BIZ_B,
    ).length;
    await page.locator('input[type="file"]').first().setInputFiles({
      name: UPLOAD_NAME_BIZ_B,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 biz-b"),
    });
    await waitUploadExactHeld(state, "file", BIZ_B, postsBBefore);

    const strategyBefore = state.strategyGetLog.length;
    const taskBefore = state.taskPosts.length;
    const filesPathA = `/api/projects/${BIZ_A}/files`;
    const filesGetABefore = countPathLedger(
      state.filesGetFulfilledLog,
      BIZ_A,
      "GET",
      filesPathA,
    );

    // B14：400 失败路径不得等待不存在的 files GET
    await releaseHeldUpload(
      page,
      state,
      "file",
      BIZ_A,
      {
        status: 400,
        body: {
          detail: {
            code: "upload_fail",
            message: UPLOAD_FAIL_MARKER_BIZ_A,
          },
        },
      },
      { expectFilesGetRefresh: false },
    );
    // 已 arm：observer 持续观测；补扫确认当前 B DOM 无敏感 marker
    await rescanDomProbeForUrl(page, BIZ_B, [UPLOAD_FAIL_MARKER_BIZ_A]);
    await waitContinuationBarrier(page);

    // 失败路径 files GET 不得 +1
    expect(
      countPathLedger(state.filesGetFulfilledLog, BIZ_A, "GET", filesPathA),
    ).toBe(filesGetABefore);

    await expect(page.getByText(UPLOAD_FAIL_MARKER_BIZ_A)).toHaveCount(0);
    const leakHits = await readDomProbeHits(page);
    expect(leakHits).toEqual([]);
    expect(consoleLines.join("\n")).not.toContain(UPLOAD_FAIL_MARKER_BIZ_A);
    // B17：专用 pageerror + unhandledrejection 台账精确空（禁止 /unhandled/ 过滤冒充）
    expect(errorLedgers.pageErrors).toEqual([]);
    expect(await errorLedgers.readUnhandled()).toEqual([]);
    expect(errorLedgers.pageErrors.join("\n")).not.toContain(
      UPLOAD_FAIL_MARKER_BIZ_A,
    );
    expect((await errorLedgers.readUnhandled()).join("\n")).not.toContain(
      UPLOAD_FAIL_MARKER_BIZ_A,
    );
    await expect(page.getByRole("button", { name: /选择文件|处理中/ })).toBeDisabled();
    expect(state.strategyGetLog.length).toBe(strategyBefore);
    expect(state.taskPosts.length).toBe(taskBefore);
    expect(state.externalHits).toEqual([]);

    await releaseHeldUpload(page, state, "file", BIZ_B, {
      status: 201,
      body: {
        id: "file_biz_b_cleanup",
        filename: UPLOAD_NAME_BIZ_B,
        sizeBytes: 80,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    });
  });

  test("M3-T11 uploadImage：A POST images Hold；软切 B content；释放 A 201 后 B 正文不变、零 A 图片引用、B PUT +0", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G T11 技术甲",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G T11 技术乙",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const state = createProbeState([projectA, projectB]);
    expect(state.editorById[TECH_A].chapters[0]?.id).toBe("n1");
    expect(state.editorById[TECH_B].chapters[0]?.id).toBe("n1");
    state.uploadImageMode[TECH_A] = "hold";
    ensureProjectGate(state.uploadImageGate, TECH_A);

    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "V1G T11 技术甲");
    await page.getByText(CHAPTER_TITLE, { exact: true }).first().click();

    const imagePostsBefore = state.imagePostLog.filter(
      (x) => x.projectId === TECH_A,
    ).length;
    const imageInput = page.locator('input[type="file"][accept*="image"]').first();
    await imageInput.setInputFiles({
      name: IMAGE_FILE_A,
      mimeType: "image/png",
      buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    });
    await waitUploadExactHeld(state, "image", TECH_A, imagePostsBefore);

    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "V1G T11 技术乙");
    await page.getByText(CHAPTER_TITLE, { exact: true }).first().click();

    const bodyBox = page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` });
    await expect(bodyBox).toHaveValue(CHAPTER_BODY_B);
    const bodyBefore = await bodyBox.inputValue();
    const putBBefore = state.editorPutLog.filter((x) => x.projectId === TECH_B)
      .length;
    const putAllBefore = state.putLog.filter((id) => id === TECH_B).length;

    // image 无 refreshFiles；releaseHeldUpload 不得登记 files GET
    await releaseHeldUpload(page, state, "image", TECH_A, {
      status: 201,
      body: {
        id: IMAGE_ID_A,
        filename: IMAGE_FILE_A,
        sizeBytes: 8,
      },
    });

    await expect(bodyBox).toHaveValue(bodyBefore);
    await expect(bodyBox).toHaveValue(CHAPTER_BODY_B);
    const imageRef = `biaoshu-image://${IMAGE_ID_A}`;
    await expect(page.getByText(imageRef)).toHaveCount(0);
    expect(await bodyBox.inputValue()).not.toContain(imageRef);
    expect(await bodyBox.inputValue()).not.toContain(IMAGE_ID_A);
    expect(
      state.editorPutLog.filter((x) => x.projectId === TECH_B).length,
    ).toBe(putBBefore);
    expect(state.putLog.filter((id) => id === TECH_B).length).toBe(putAllBefore);
    expect(state.externalHits).toEqual([]);
  });

  // ---------- M3-T12：A13 商务 managed task POST 500 未处理拒绝（failure-first） ----------

  test("M3-T12 商务 managed task POST 500：固定安全错误可见；pageerror/unhandledrejection 皆空；managed POST 精确一次、lightweight 零次、响应 marker 零泄漏", async ({
    page,
  }) => {
    /**
     * 用途：商务 managed 策略下真实点击解析入口，tasks POST 精确 500；
     *       断言固定安全错误可见、pageerror/unhandledrejection 精确空、
     *       managed POST=1 / lightweight=0、响应 marker 零泄漏。
     * 红因边界：若 managed 尚未接线导致更早失败，如实报告，禁止冒充 A13。
     * 清理：无 hold route；error ledger / console 监听随 page 关闭。
     */
    const project = makeProject({
      id: BIZ_A,
      name: "V1G T12 商务 managed POST500",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([project], { parseStrategy: "managed" });
    state.taskCreateMode[BIZ_A] = "fail";

    const consoleLines = collectConsole(page);
    const errorLedgers = await installPageErrorLedgers(page);
    await installRoutes(page, state);
    await installRouteAwareDomProbe(page, BIZ_A, [TASK_POST_500_MARKER_BIZ]);

    try {
      await page.goto(`/business-bid/${BIZ_A}/parse`);
      await expectBizReady(page, "V1G T12 商务 managed POST500");
      // B20/B22：目标页 ready 后、点击前显式 arm
      await armDomProbe(page);

      const postsBefore = state.taskPosts.filter((t) => t.projectId === BIZ_A)
        .length;
      expect(postsBefore).toBe(0);

      // 真实解析入口（禁止直接调内部函数 / 伪造 DOM）
      await page.getByRole("button", { name: "整段重解析" }).click();

      // 精确台账：相关 POST 必须恰好 1（禁止 >=1）——早期快速门
      await expect
        .poll(
          () => state.taskPosts.filter((t) => t.projectId === BIZ_A).length,
          { timeout: 15_000 },
        )
        .toBe(1);

      const relatedEarly = state.taskPosts.filter((t) => t.projectId === BIZ_A);
      expect(relatedEarly.length).toBe(1);
      expect(relatedEarly[0].type).toBe("parse");
      expect(relatedEarly[0].payload).toEqual({ engine: "managed" });

      const managedEarly = relatedEarly.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "managed",
      );
      const lightweightEarly = state.taskPosts.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      );
      // B22 早期快速门：total=1、managed=1、lightweight=0（仅快速门，非终态）
      expect(relatedEarly.length).toBe(1);
      expect(managedEarly.length).toBe(1);
      expect(lightweightEarly.length).toBe(0);
      expect(
        state.taskPosts.filter(
          (t) =>
            t.payload &&
            (t.payload as { engine?: string }).engine === "lightweight",
        ).length,
      ).toBe(0);

      // 固定安全错误必须在当前商务页面可见（终态 barrier 不得位于此之前）
      await expect(page.getByText(TASK_POST_SAFE_ERROR)).toBeVisible({
        timeout: 10_000,
      });

      // B22：safe-error 可见之后、最终 relatedFinal 快照之前执行双 continuation barrier
      await waitContinuationBarrier(page);
      await waitContinuationBarrier(page);

      // B22：barrier 后重新取当前快照；禁止复用早期 related/managed/lightweight 数组
      const relatedFinal = state.taskPosts.filter((t) => t.projectId === BIZ_A);
      const managedFinal = relatedFinal.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "managed",
      );
      const lightweightFinal = state.taskPosts.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      );
      expect(relatedFinal.length).toBe(1);
      expect(managedFinal.length).toBe(1);
      expect(lightweightFinal.length).toBe(0);
      // 全文件 lightweight 仍须精确 0
      expect(
        state.taskPosts.filter(
          (t) =>
            t.payload &&
            (t.payload as { engine?: string }).engine === "lightweight",
        ).length,
      ).toBe(0);
      expect(relatedFinal[0].type).toBe("parse");
      expect(relatedFinal[0].payload).toEqual({ engine: "managed" });

      // 敏感 marker：先完成全部 rescan/barrier（rescan 内含 continuation barrier）
      await rescanDomProbeForUrl(page, BIZ_A, [TASK_POST_500_MARKER_BIZ]);
      await expect(page.getByText(TASK_POST_500_MARKER_BIZ)).toHaveCount(0);
      const bodyText = await page.locator("body").innerText();
      expect(bodyText).not.toContain(TASK_POST_500_MARKER_BIZ);
      const bodyHtml = await page.locator("body").innerHTML();
      expect(bodyHtml).not.toContain(TASK_POST_500_MARKER_BIZ);
      const leakHits = await readDomProbeHits(page);
      expect(
        leakHits.filter((h) => h.includes(TASK_POST_500_MARKER_BIZ)),
      ).toEqual([]);
      expect(consoleLines.join("\n")).not.toContain(TASK_POST_500_MARKER_BIZ);

      // E4/D5：最终台账严格先 await readUnhandled()，再立即同步复制 Node pageErrors；
      // 此后禁止 page.evaluate / barrier / 网络/定时等待或可触发 continuation 的 await，
      // 只允许同步断言，确保 pageerror 快照确为最后异步动作之后。
      const finalUnhandled = await errorLedgers.readUnhandled();
      const finalPageErrors = errorLedgers.pageErrors.slice();
      expect(finalUnhandled).toEqual([]);
      expect(finalPageErrors).toEqual([]);
      expect(finalUnhandled.join("\n")).not.toContain(TASK_POST_500_MARKER_BIZ);
      expect(finalPageErrors.join("\n")).not.toContain(TASK_POST_500_MARKER_BIZ);
      expect(state.externalHits).toEqual([]);
    } finally {
      // 无 hold gate/route；显式清空本测 fail 模式，避免串测污染
      state.taskCreateMode[BIZ_A] = "ok";
    }
  });

});

/**
 * D6：route-aware probe 真实浏览器 helper 自检。
 * 证明：arm 后同一任务 marker→safe 与 插入→清空→删除 均命中；
 *       pre-arm A marker 被 drain 且不命中（关 B20 假红与 B21 假绿两端）。
 * E3：同一 page.evaluate 内先造 pre-arm mutation，再立即调用真实 __v1gDomProbeArm，
 *     使 pending 必须由 arm 的 takeRecords 真正 drain；禁止跨 evaluate 冒充 pending。
 * 必须调用生产 helper（install/arm/read），禁止无关复制实现。
 */
test.describe("D6 route-aware probe 浏览器 helper 自检", () => {
  test("pre-arm drain 不命中；post-arm marker→safe 与 removed 命中", async ({
    page,
  }) => {
    const PRE = "D6_PRE_ARM_MARKER_A";
    const POST_CHAR = "D6_POST_ARM_CHAR_MARKER";
    const POST_INS = "D6_POST_ARM_INSERT_MARKER";

    await installRouteAwareDomProbe(page, "127.0.0.1", [
      PRE,
      POST_CHAR,
      POST_INS,
    ]);
    await page.goto("http://127.0.0.1:5174/");
    await expect(page.locator("body")).toBeVisible({ timeout: 20_000 });

    // E3：同一浏览器任务内 — 先造 pre-arm mutation，再立即调用已安装的真实 arm；
    // 同步 turn 内 takeRecords 必须真正 drain pending；跨 evaluate 会让 MO 回调先吞掉队列。
    await page.evaluate((pre) => {
      const w = window as unknown as {
        __v1gDomProbeArm?: () => void;
        __v1gDomProbeObserver?: MutationObserver | null;
        __v1gDomProbeArmed?: boolean;
      };
      if (typeof w.__v1gDomProbeArm !== "function") {
        throw new Error("route-aware DOM probe 未安装，无法 arm");
      }
      if (!w.__v1gDomProbeObserver) {
        throw new Error("route-aware DOM probe observer 未就绪");
      }
      // pre-arm：写入 A marker 并改为 safe，产生 observer 队列记录（尚未 arm）
      const wrap = document.createElement("div");
      wrap.id = "d6-pre-arm";
      const t = document.createTextNode(pre);
      wrap.appendChild(t);
      document.body.appendChild(wrap);
      // characterData 变更：old=PRE current=safe（应被 arm 时 takeRecords drain，不得入 hits）
      t.textContent = "pre-arm-safe";
      // 立即 arm：同步 drain pending → 清 hits → 启用 → 扫 current（current 已无 PRE）
      w.__v1gDomProbeArm();
      if (!w.__v1gDomProbeArmed) {
        throw new Error("arm 后 __v1gDomProbeArmed 应为 true");
      }
    }, PRE);

    // post-arm：同一任务 marker→safe（依赖 characterDataOldValue）
    await page.evaluate((post) => {
      const t = document.createTextNode(post);
      const wrap = document.createElement("div");
      wrap.id = "d6-post-char";
      wrap.appendChild(t);
      document.body.appendChild(wrap);
      t.textContent = "post-char-safe";
    }, POST_CHAR);

    // post-arm：插入 → 清空 → 删除（added + removed）
    await page.evaluate((post) => {
      const el = document.createElement("div");
      el.id = "d6-post-ins";
      el.textContent = post;
      document.body.appendChild(el);
      el.textContent = "";
      el.remove();
    }, POST_INS);

    const hits = await readDomProbeHits(page);
    const hitText = hits.join("\n");
    // pre-arm A 不得命中（依赖 arm takeRecords 真正 drain；删 takeRecords 则同步 arm 后回调必红）
    expect(
      hits.filter((h) => h.includes(PRE)),
      `pre-arm 不得入 hits: ${hitText}`,
    ).toEqual([]);
    // post-arm characterData old marker 必须命中
    expect(
      hits.some((h) => h.includes(POST_CHAR)),
      `post-arm char marker 必须命中: ${hitText}`,
    ).toBe(true);
    // post-arm insert/remove marker 必须命中
    expect(
      hits.some((h) => h.includes(POST_INS)),
      `post-arm insert/remove marker 必须命中: ${hitText}`,
    ).toBe(true);
  });
});
