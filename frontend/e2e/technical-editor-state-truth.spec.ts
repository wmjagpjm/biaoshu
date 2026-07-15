/**
 * 模块：P11C 技术标编辑态真实数据收口 E2E
 * 用途：只认 GET|PUT /api/projects/{id}/editor-state；旧 editors 键忽略保值；
 *       GET 失败固定卡；PUT 失败固定脱敏；required CSRF（普通+合并 PUT）；
 *       409 三方合并/二次不循环；M3-D 对话框兼容；A→B 迟到隔离；
 *       网络/存储/console 反假绿。
 * 对接：Playwright chromium headless 单 worker；前端 5174；受控路由桩。
 * 二次开发：禁止 or True、宽泛 startsWith 放行、吞异常、固定 waitForTimeout 作完成证据、
 *       条件跳过；探针安装失败必须失败；枚举 key(i)??""；项目 ID 仅允许 state.projects 已知集合。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const SECRET = "SECRET_P11C_LEAK_DETAIL_/api/projects/editor-state";
const REAL_TECH_A = "proj_e2e_p11c_tech_a";
const REAL_TECH_B = "proj_e2e_p11c_tech_b";
const REAL_OVERVIEW = "P11C_SERVER_REAL_OVERVIEW_权威概述";
const REAL_OVERVIEW_B = "P11C_SERVER_B_OVERVIEW_项目乙";
const LOCAL_SECRET = "LOCAL_EDITORS_SECRET_SHOULD_NOT_RENDER_P11C";
const MOCK_SNIPPET = "智慧交通综合管理平台";
const LOAD_ERROR = "技术标工作区加载失败，请稍后重试";
const SAVE_ERROR = "技术标工作区保存失败，请稍后重试";
const FULL_STATE_CONFLICT_MSG =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const MATRIX_CONFLICT_MSG = "响应矩阵已被其他终端更新，请重新载入后再保存";
const CSRF_TOKEN = "e2e-p11c-csrf-token-memory";
/** 不含敏感信息的 E2E 会话 Cookie（HttpOnly；document.cookie 不可见） */
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p11c_sess_opaque";
const E2E_LOGIN_USER = "e2e_p11c_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const FUSE_SUGGESTION_ID = "sug_e2e_p11c_1";
const TECH_REQ = "服务端技术要求甲";
const MATRIX_SOURCE_KEY = `requirement:${TECH_REQ.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
const FUSE_CHAPTER_BODY = "服务端章节正文";
/** 与 computeChapterBase / Python sha1 前 20 hex 对齐 */
const FUSE_BODY_HASH = "bh_925ac0c6562662802896";
const FUSE_BODY_LEN = 7;
const MSG_APPLY_RELOAD_FAIL = "融合已写入，但刷新失败，请关闭后重新打开";
const TPL_ID = "tpl_e2e_p11c_fuse";
const TPL_TITLE = "P11C融合模板甲";
const CARD_ID = "card_e2e_p11c_fuse";
const CARD_TITLE = "P11C融合卡片甲";

const EDITORS_KEY_RE = /^biaoshu\.technicalPlan\.editors(?:\.|$)/;

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: "technical";
  linkedProjectId?: string | null;
};

type MatrixRow = {
  id: string;
  kind: "requirement" | "scoring";
  sourceKey: string;
  sourceIndex: number;
  sourceText: string;
  weight: string;
  chapterIds: string[];
  outlineNodeIds: string[];
  status: string;
  notes: string;
};

type EditorState = {
  projectId: string;
  outline: Array<Record<string, unknown>>;
  chapters: Array<Record<string, unknown>>;
  facts: Array<Record<string, unknown>>;
  mode: string;
  analysisOverview: string;
  analysis: {
    overview: string;
    techRequirements: string[];
    rejectionRisks: string[];
    scoringPoints: Array<{ name: string; weight: string }>;
  };
  responseMatrix: MatrixRow[];
  responseMatrixVersion: string | null;
  parsedMarkdown: string;
  guidance?: Record<string, unknown> | null;
  stateVersion: string;
  updatedAt: string | null;
};

type PutRecord = {
  projectId: string;
  body: Record<string, unknown>;
  raw: string;
  headers: Record<string, string>;
  /** 服务端成功 200 响应中的 stateVersion；缺/非法时为 null */
  responseVersion?: string | null;
};

/** 用途：受控挂起，便于 A→B 在响应释放前切换项目。 */
type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
};

type PutMode =
  | { kind: "ok"; stripStateVersion?: boolean; invalidStateVersion?: boolean }
  | { kind: "fail"; status: number }
  | { kind: "full_conflict" }
  /** 普通 409：无 editor_state_version_conflict code、无矩阵明细 */
  | { kind: "plain_409" }
  | {
      kind: "conflict";
      remoteNotes?: string;
      remoteVersion?: string;
      /** 连续冲突次数（含首次）；用尽后放行 200 */
      times?: number;
    }
  | {
      kind: "delay";
      ms: number;
      then: "ok" | "fail" | "conflict";
      status?: number;
      remoteNotes?: string;
      remoteVersion?: string;
    }
  | {
      kind: "gate";
      gate: HoldGate;
      then: "ok" | "fail" | "conflict";
      status?: number;
      remoteNotes?: string;
      remoteVersion?: string;
    };

type GetMode =
  | { kind: "ok" }
  | { kind: "fail"; status: number }
  | { kind: "delay"; ms: number; then: "ok" | "fail"; status?: number }
  | { kind: "gate"; gate: HoldGate; then: "ok" | "fail"; status?: number };

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  getMode: Record<string, GetMode>;
  putMode: Record<string, PutMode>;
  getLog: string[];
  putLog: PutRecord[];
  taskPosts: Array<{ projectId: string; type: string }>;
  forbiddenHits: string[];
  externalHits: string[];
  orderLog: string[];
  clipboard: { installed: boolean; read: number; write: number };
  failNextEditorGetAfterTask: Record<string, boolean>;
  /** 下一次 editor-state GET 失败（用于 M3-D reload） */
  failNextEditorGet: Record<string, boolean>;
  authRequired: boolean;
  /** required 下是否已通过 POST /auth/login 建立会话 */
  sessionAuthenticated: boolean;
  /** 精确记录登录次数（须为 1） */
  loginPosts: number;
  csrfToken: string | null;
  fuseCreatePosts: Array<{ projectId: string; body: Record<string, unknown> }>;
  fuseListGets: string[];
  fuseConsumePosts: string[];
  /** 服务端桩版本序号；成功 PUT 生成下一 esv_，禁止回显客户端 expected */
  versionSeq: number;
  /** 成功 200 且响应含合法 stateVersion 时按序记录，供串链反假绿 */
  successVersionLog: string[];
};

type StorageSnapshot = {
  lsKeys: string[];
  ls: Record<string, string>;
  ssKeys: string[];
  ss: Record<string, string>;
  cookies: string;
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
      while (waiters.length > 0) {
        const w = waiters.shift();
        w?.();
      }
    },
    isReleased: () => released,
  };
}


function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/** 用途：服务端桩分配下一合法 stateVersion，不得回显客户端 expected。 */
function allocateStateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return `esv_${state.versionSeq.toString(16).padStart(32, "0")}`;
}

/** 用途：固定种子版本（GET 初始态）。 */
function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function isLocalHost(host: string): boolean {
  return host === "127.0.0.1" || host === "localhost";
}

async function json(
  route: Route,
  body: unknown,
  status = 200,
  extraHeaders?: Record<string, string>,
) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: extraHeaders,
    body: JSON.stringify(body),
  });
}

/**
 * 用途：method + 精确路径/精确已知项目 ID 白名单。
 * 禁止宽放 workspaces/settings/templates/cards/hr 任意后缀与伪项目 ID。
 */
function isAllowedP11cApi(
  method: string,
  path: string,
  knownProjectIds: ReadonlySet<string>,
): boolean {
  const staticRules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces\/?$/ },
    { methods: ["GET"], path: /^\/api\/settings\/parse-strategy\/?$/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings\/?$/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET"], path: /^\/api\/templates\/?$/ },
    { methods: ["GET"], path: /^\/api\/cards\/?$/ },
    { methods: ["GET"], path: /^\/api\/hr\/team-recommendations\/?$/ },
  ];
  if (staticRules.some((r) => r.methods.includes(method) && r.path.test(path))) {
    return true;
  }

  const projectMatch = path.match(/^\/api\/projects\/([^/]+)(\/.*)?$/);
  if (!projectMatch) return false;
  const projectId = projectMatch[1];
  if (!knownProjectIds.has(projectId)) return false;
  const rest = projectMatch[2] || "";

  const projectRules: Array<{ methods: string[]; rest: RegExp }> = [
    { methods: ["GET", "PATCH"], rest: /^\/?$/ },
    { methods: ["GET", "PUT"], rest: /^\/editor-state\/?$/ },
    { methods: ["GET", "POST"], rest: /^\/files\/?$/ },
    { methods: ["POST"], rest: /^\/images\/?$/ },
    { methods: ["GET", "POST"], rest: /^\/tasks\/?$/ },
    {
      methods: ["GET"],
      rest: /^\/tasks\/[^/]+\/?$/,
    },
    {
      methods: ["GET"],
      rest: /^\/tasks\/[^/]+\/events\/?$/,
    },
    {
      methods: ["POST"],
      rest: /^\/tasks\/[^/]+\/cancel\/?$/,
    },
    {
      methods: ["GET", "POST"],
      rest: /^\/content-fuse-applications\/?$/,
    },
    {
      methods: ["POST"],
      rest: /^\/content-fuse-applications\/[^/]+\/consume\/?$/,
    },
    {
      methods: ["POST"],
      rest: /^\/artifacts\/workspace\/revise\/?$/,
    },
    {
      methods: ["POST"],
      rest: /^\/artifacts\/[^/]+\/revise\/?$/,
    },
  ];
  return projectRules.some(
    (r) => r.methods.includes(method) && r.rest.test(rest),
  );
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-15T12:00:00.000Z",
    technicalPlanStep: partial.technicalPlanStep ?? 2,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    kind: "technical",
    id: partial.id,
    name: partial.name,
  };
}

function matrixRow(partial?: Partial<MatrixRow>): MatrixRow {
  return {
    id: partial?.id ?? "rm1",
    kind: partial?.kind ?? "requirement",
    sourceKey: partial?.sourceKey ?? MATRIX_SOURCE_KEY,
    sourceIndex: partial?.sourceIndex ?? 0,
    sourceText: partial?.sourceText ?? TECH_REQ,
    weight: partial?.weight ?? "",
    chapterIds: partial?.chapterIds ?? ["n1"],
    outlineNodeIds: partial?.outlineNodeIds ?? ["n1"],
    status: partial?.status ?? "covered",
    notes: partial?.notes ?? "",
  };
}

function emptyTechnicalEditor(projectId: string): EditorState {
  return {
    projectId,
    outline: [],
    chapters: [],
    facts: [],
    mode: "ALIGNED",
    analysisOverview: "",
    analysis: {
      overview: "",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: "",
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    stateVersion: seedStateVersion(1),
    updatedAt: null,
  };
}

function realTechnicalEditor(
  projectId: string,
  overview: string,
): EditorState {
  return {
    projectId,
    outline: [
      {
        id: "n1",
        title: "服务端一级目录",
        level: 1,
        targetWords: 800,
        description: "",
        children: [],
      },
    ],
    chapters: [
      {
        id: "n1",
        title: "服务端一级目录",
        body: FUSE_CHAPTER_BODY,
        preview: FUSE_CHAPTER_BODY,
        wordCount: FUSE_BODY_LEN,
        status: "done",
      },
    ],
    facts: [
      {
        id: "f1",
        category: "招标",
        content: "服务端事实甲",
        source: "tender",
      },
    ],
    mode: "ALIGNED",
    analysisOverview: overview,
    analysis: {
      overview,
      techRequirements: [TECH_REQ],
      rejectionRisks: ["服务端废标风险甲"],
      // 仅一条技术要求，保证矩阵单行，便于 409 字段冲突与备注选择器精确定位
      scoringPoints: [],
    },
    responseMatrix: [matrixRow()],
    responseMatrixVersion: "ver_srv_1",
    parsedMarkdown: "服务端解析正文",
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "服务端章节侧重点",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    stateVersion: seedStateVersion(10),
    updatedAt: "2026-07-15T12:00:00.000Z",
  };
}

function editorsStorageKey(projectId: string) {
  return `biaoshu.technicalPlan.editors.${projectId}`;
}

function fakeEditorsValue(_projectId: string) {
  return JSON.stringify({
    outline: [
      {
        id: "local_n",
        title: MOCK_SNIPPET,
        level: 1,
        children: [],
      },
    ],
    chapters: [
      {
        id: "local_n",
        title: MOCK_SNIPPET,
        body: LOCAL_SECRET,
        preview: LOCAL_SECRET,
        wordCount: 1,
        status: "done",
      },
    ],
    facts: [
      {
        id: "local_f",
        category: "本地",
        content: LOCAL_SECRET,
        source: "manual",
      },
    ],
    mode: "ALIGNED",
    analysisOverview: LOCAL_SECRET,
    analysis: {
      overview: LOCAL_SECRET,
      techRequirements: [MOCK_SNIPPET],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    parsedMarkdown: LOCAL_SECRET,
  });
}

function createProbeState(seed: ProjectStub[] = []): ProbeState {
  const editorById: Record<string, EditorState> = {};
  for (const p of seed) {
    editorById[p.id] = emptyTechnicalEditor(p.id);
  }
  return {
    projects: [...seed],
    editorById,
    getMode: {},
    putMode: {},
    getLog: [],
    putLog: [],
    taskPosts: [],
    forbiddenHits: [],
    externalHits: [],
    orderLog: [],
    clipboard: { installed: false, read: 0, write: 0 },
    failNextEditorGetAfterTask: {},
    failNextEditorGet: {},
    authRequired: false,
    sessionAuthenticated: false,
    loginPosts: 0,
    csrfToken: CSRF_TOKEN,
    fuseCreatePosts: [],
    fuseListGets: [],
    fuseConsumePosts: [],
    versionSeq: 100,
    successVersionLog: [],
  };
}

function knownProjectIdSet(state: ProbeState): Set<string> {
  return new Set(state.projects.map((p) => p.id));
}

function conflictRemoteMatrix(
  prev: EditorState,
  remoteNotes: string,
): MatrixRow[] {
  const base =
    prev.responseMatrix[0] ??
    matrixRow({ notes: "", chapterIds: [], outlineNodeIds: [] });
  return [
    {
      ...base,
      notes: remoteNotes,
      status: base.status || "covered",
    },
  ];
}

async function fulfillEditorPut(
  route: Route,
  state: ProbeState,
  id: string,
  body: Record<string, unknown>,
  mode: PutMode,
) {
  if (mode.kind === "fail") {
    await json(
      route,
      {
        detail: { code: "editor_state_put_failed", message: SECRET },
      },
      mode.status,
    );
    return;
  }

  if (mode.kind === "full_conflict") {
    const prev = state.editorById[id] ?? emptyTechnicalEditor(id);
    await json(
      route,
      {
        detail: {
          code: "editor_state_version_conflict",
          message: "编辑内容已被其他操作更新，请重新载入后再保存",
          currentStateVersion: prev.stateVersion,
        },
      },
      409,
    );
    return;
  }

  if (mode.kind === "plain_409") {
    await json(
      route,
      {
        detail: {
          code: "generic_conflict",
          message: SECRET,
        },
      },
      409,
    );
    return;
  }

  if (mode.kind === "conflict") {
    const times = mode.times ?? 1;
    const nextTimes = times - 1;
    if (nextTimes > 0) {
      state.putMode[id] = { ...mode, times: nextTimes };
    } else {
      state.putMode[id] = { kind: "ok" };
    }
    const prev = state.editorById[id] ?? emptyTechnicalEditor(id);
    const remoteNotes = mode.remoteNotes ?? "远端矩阵备注冲突值";
    const remoteVersion = mode.remoteVersion ?? "ver_remote_2";
    const remoteMatrix = conflictRemoteMatrix(prev, remoteNotes);
    prev.responseMatrixVersion = remoteVersion;
    prev.responseMatrix = remoteMatrix.map((r) => ({ ...r }));
    state.editorById[id] = prev;
    await json(
      route,
      {
        detail: {
          code: "response_matrix_version_conflict",
          message: SECRET,
          responseMatrix: remoteMatrix,
          currentResponseMatrixVersion: remoteVersion,
        },
      },
      409,
    );
    return;
  }

  // ok：可选 CAS；成功生成下一版本，禁止回显客户端 expected
  const prev = state.editorById[id] ?? emptyTechnicalEditor(id);
  if (body.expectedStateVersion != null) {
    const expected = body.expectedStateVersion;
    if (!isValidStateVersion(expected) || expected !== prev.stateVersion) {
      await json(
        route,
        {
          detail: {
            code: "editor_state_version_conflict",
            message: "编辑内容已被其他操作更新，请重新载入后再保存",
            currentStateVersion: prev.stateVersion,
          },
        },
        409,
      );
      return;
    }
  }

  const nextVersion = allocateStateVersion(state);
  const nextGuidance =
    body.guidance && typeof body.guidance === "object"
      ? (body.guidance as Record<string, unknown>)
      : prev.guidance;
  const nextVersionMatrix =
    typeof body.responseMatrixVersion === "string" && body.responseMatrixVersion
      ? `ver_after_${body.responseMatrixVersion}`
      : prev.responseMatrixVersion || "ver_srv_saved";
  state.editorById[id] = {
    ...prev,
    projectId: id,
    outline: Array.isArray(body.outline)
      ? (body.outline as Array<Record<string, unknown>>)
      : prev.outline,
    chapters: Array.isArray(body.chapters)
      ? (body.chapters as Array<Record<string, unknown>>)
      : prev.chapters,
    facts: Array.isArray(body.facts)
      ? (body.facts as Array<Record<string, unknown>>)
      : prev.facts,
    mode: typeof body.mode === "string" ? body.mode : prev.mode,
    analysisOverview:
      body.analysisOverview != null
        ? String(body.analysisOverview)
        : prev.analysisOverview,
    analysis:
      body.analysis && typeof body.analysis === "object"
        ? (body.analysis as EditorState["analysis"])
        : prev.analysis,
    responseMatrix: Array.isArray(body.responseMatrix)
      ? (body.responseMatrix as MatrixRow[])
      : prev.responseMatrix,
    responseMatrixVersion: Array.isArray(body.responseMatrix)
      ? nextVersionMatrix
      : prev.responseMatrixVersion,
    parsedMarkdown:
      body.parsedMarkdown != null
        ? String(body.parsedMarkdown)
        : prev.parsedMarkdown,
    guidance: nextGuidance ?? prev.guidance,
    stateVersion: nextVersion,
    updatedAt: new Date().toISOString(),
  };
  const responseBody: Record<string, unknown> = {
    ...state.editorById[id],
  };
  let responseVersion: string | null = nextVersion;
  if (mode.kind === "ok" && mode.stripStateVersion) {
    delete responseBody.stateVersion;
    responseVersion = null;
  } else if (mode.kind === "ok" && mode.invalidStateVersion) {
    responseBody.stateVersion = "not-a-valid-esv";
    responseVersion = null;
  }
  // 挂到最近一条 putLog，供串链断言读取服务端真实响应版本
  const lastPut = state.putLog[state.putLog.length - 1];
  if (lastPut && lastPut.projectId === id) {
    lastPut.responseVersion = responseVersion;
  }
  if (responseVersion && isValidStateVersion(responseVersion)) {
    state.successVersionLog.push(responseVersion);
  }
  await json(route, responseBody);
}

async function installP11cRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p11cClip?: { installed: boolean; read: number; write: number };
    };
    g.__p11cClip = { installed: false, read: 0, write: 0 };
    const clip = {
      readText: async () => {
        g.__p11cClip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__p11cClip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__p11cClip.installed = true;
    } catch {
      g.__p11cClip.installed = false;
    }
  });

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

    const knownIds = knownProjectIdSet(state);
    if (!isAllowedP11cApi(method, path, knownIds)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p11c_forbidden", message: SECRET } },
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
      await json(route, {
        bootstrapped: true,
        authRequired: state.authRequired,
      });
      return;
    }

    if (path === "/api/auth/me" && method === "GET") {
      // required 且未登录：必须 401，迫使走登录 UI，禁止直接假扮已认证
      if (state.authRequired && !state.sessionAuthenticated) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(route, {
        user: {
          id: "user_e2e",
          username: state.authRequired ? E2E_LOGIN_USER : "e2e",
        },
        workspaces: [
          {
            id: "ws_e2e",
            name: "E2E 工作空间",
            role: "bid_writer",
            isOwner: true,
          },
        ],
        activeWorkspaceId: "ws_e2e",
        // 登录响应已下发 CSRF；/me 不再重复下发（与 P10A 契约一致）
        csrfToken: state.authRequired ? null : state.csrfToken,
      });
      return;
    }

    if (path === "/api/auth/csrf" && method === "GET") {
      if (state.authRequired && !state.sessionAuthenticated) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(route, { csrfToken: state.csrfToken });
      return;
    }

    if (path === "/api/auth/login" && method === "POST") {
      state.loginPosts += 1;
      state.sessionAuthenticated = true;
      // 登录响应：内存 CSRF + 同源 HttpOnly 会话 Cookie（无敏感明文）
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: {
          "Set-Cookie": `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}; Path=/; HttpOnly; SameSite=Lax`,
          "Cache-Control": "no-store",
        },
        body: JSON.stringify({
          user: { id: "user_e2e", username: E2E_LOGIN_USER },
          workspaces: [
            {
              id: "ws_e2e",
              name: "E2E 工作空间",
              role: "bid_writer",
              isOwner: true,
            },
          ],
          activeWorkspaceId: "ws_e2e",
          csrfToken: state.csrfToken,
        }),
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.sessionAuthenticated = false;
      await route.fulfill({
        status: 204,
        headers: {
          "Set-Cookie": `${SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax`,
        },
        body: "",
      });
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

    if (path === "/api/settings/parse-strategy" && method === "GET") {
      await json(route, { parseStrategy: "light" });
      return;
    }

    if (path === "/api/settings" && (method === "GET" || method === "PUT")) {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "",
        apiKey: "",
        model: "",
        parseStrategy: "light",
      });
      return;
    }

    if (path === "/api/templates" && method === "GET") {
      await json(route, [
        {
          id: TPL_ID,
          workspaceId: "ws_e2e",
          title: TPL_TITLE,
          tags: ["E2E"],
          status: "active",
          kind: "technical",
          sourceProjectId: null,
          sourceProjectName: "",
          createdAt: "2026-07-15T12:00:00.000Z",
          updatedAt: "2026-07-15T12:00:00.000Z",
          chapterCount: 1,
          outlineTitles: ["服务端一级目录"],
        },
      ]);
      return;
    }

    if (path === "/api/cards" && method === "GET") {
      await json(route, [
        {
          id: CARD_ID,
          workspaceId: "ws_e2e",
          type: "document",
          title: CARD_TITLE,
          tags: ["E2E"],
          status: "active",
          summary: "P11C 融合卡片摘要",
          sourceType: "manual",
          sourceId: null,
          sourceLabel: "E2E",
          hasBody: true,
          hasImage: false,
          sizeBytes: 32,
          createdAt: "2026-07-15T12:00:00.000Z",
          updatedAt: "2026-07-15T12:00:00.000Z",
        },
      ]);
      return;
    }

    if (path === "/api/hr/team-recommendations" && method === "GET") {
      await json(route, []);
      return;
    }

    if (path === "/api/projects" || path === "/api/projects/") {
      if (method === "GET") {
        const kind = url.searchParams.get("kind");
        let items = state.projects;
        if (kind === "technical") {
          items = items.filter((p) => p.kind === "technical");
        }
        await json(route, items);
        return;
      }
      if (method === "POST") {
        await json(
          route,
          { detail: { code: "p11c_no_create", message: SECRET } },
          403,
        );
        return;
      }
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = detailMatch[1];
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(
          route,
          { detail: { code: "project_not_found", message: SECRET } },
          404,
        );
        return;
      }
      if (method === "PATCH") {
        const raw = req.postData() || "{}";
        let patch: Record<string, unknown> = {};
        try {
          patch = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          patch = {};
        }
        Object.assign(found, patch, { updatedAt: new Date().toISOString() });
      }
      await json(route, found);
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      const id = editorMatch[1];
      if (method === "GET") {
        state.getLog.push(id);
        state.orderLog.push(`editor-get:${id}`);

        if (state.failNextEditorGet[id]) {
          state.failNextEditorGet[id] = false;
          await json(
            route,
            {
              detail: {
                code: "editor_state_get_failed_once",
                message: SECRET,
              },
            },
            500,
          );
          return;
        }

        if (state.failNextEditorGetAfterTask[id]) {
          state.failNextEditorGetAfterTask[id] = false;
          await json(
            route,
            {
              detail: {
                code: "editor_state_get_failed_after_task",
                message: SECRET,
              },
            },
            500,
          );
          return;
        }

        const mode = state.getMode[id] ?? { kind: "ok" as const };
        if (mode.kind === "delay") {
          await new Promise((r) => setTimeout(r, mode.ms));
          if (mode.then === "fail") {
            await json(
              route,
              {
                detail: {
                  code: "editor_state_delayed_fail",
                  message: SECRET,
                },
              },
              mode.status ?? 500,
            );
            return;
          }
        } else if (mode.kind === "gate") {
          await mode.gate.wait();
          if (mode.then === "fail") {
            await json(
              route,
              {
                detail: {
                  code: "editor_state_gated_fail",
                  message: SECRET,
                },
              },
              mode.status ?? 500,
            );
            return;
          }
        } else if (mode.kind === "fail") {
          await json(
            route,
            {
              detail: { code: "editor_state_get_failed", message: SECRET },
            },
            mode.status,
          );
          return;
        }

        const body = state.editorById[id] ?? emptyTechnicalEditor(id);
        await json(route, body);
        return;
      }

      // PUT
      const raw = req.postData() || "{}";
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = { __parseError: true };
      }
      const headers: Record<string, string> = {};
      for (const [k, v] of Object.entries(req.headers())) {
        headers[k.toLowerCase()] = String(v ?? "");
      }
      state.putLog.push({ projectId: id, body, raw, headers });
      state.orderLog.push(`editor-put:${id}`);

      if (state.authRequired) {
        const csrf = headers["x-csrf-token"] || "";
        if (!csrf || csrf !== state.csrfToken) {
          await json(
            route,
            {
              detail: {
                code: "csrf_failed",
                message: SECRET,
              },
            },
            403,
          );
          return;
        }
        // 必须携带登录响应 Set-Cookie 建立的会话 Cookie
        const cookie = headers["cookie"] || "";
        const expectedCookie = `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}`;
        if (!cookie.includes(expectedCookie)) {
          await json(
            route,
            {
              detail: {
                code: "auth_required",
                message: SECRET,
              },
            },
            401,
          );
          return;
        }
      }

      const mode = state.putMode[id] ?? { kind: "ok" as const };
      if (mode.kind === "delay") {
        await new Promise((r) => setTimeout(r, mode.ms));
        if (mode.then === "fail") {
          await fulfillEditorPut(
            route,
            state,
            id,
            body,
            { kind: "fail", status: mode.status ?? 500 },
          );
          return;
        }
        if (mode.then === "conflict") {
          await fulfillEditorPut(route, state, id, body, {
            kind: "conflict",
            remoteNotes: mode.remoteNotes,
            remoteVersion: mode.remoteVersion,
          });
          return;
        }
        await fulfillEditorPut(route, state, id, body, { kind: "ok" });
        return;
      }
      if (mode.kind === "gate") {
        await mode.gate.wait();
        if (mode.then === "fail") {
          await fulfillEditorPut(
            route,
            state,
            id,
            body,
            { kind: "fail", status: mode.status ?? 500 },
          );
          return;
        }
        if (mode.then === "conflict") {
          await fulfillEditorPut(route, state, id, body, {
            kind: "conflict",
            remoteNotes: mode.remoteNotes,
            remoteVersion: mode.remoteVersion,
          });
          return;
        }
        await fulfillEditorPut(route, state, id, body, { kind: "ok" });
        return;
      }

      await fulfillEditorPut(route, state, id, body, mode);
      return;
    }

    if (
      /^\/api\/projects\/[^/]+\/(files|tasks)\/?$/.test(path) &&
      method === "GET"
    ) {
      await json(route, []);
      return;
    }

    const taskPostMatch = path.match(/^\/api\/projects\/([^/]+)\/tasks\/?$/);
    if (taskPostMatch && method === "POST") {
      const pid = taskPostMatch[1];
      let type = "";
      let payload: Record<string, unknown> = {};
      try {
        const b = JSON.parse(req.postData() || "{}") as {
          type?: string;
          payload?: Record<string, unknown>;
        };
        type = b.type || "";
        payload = b.payload && typeof b.payload === "object" ? b.payload : {};
      } catch {
        type = "";
      }
      state.taskPosts.push({ projectId: pid, type });
      state.orderLog.push(`task-post:${pid}:${type}`);

      if (type === "content_fuse") {
        const targetIds = Array.isArray(payload.targetChapterIds)
          ? (payload.targetChapterIds as string[])
          : [];
        const targetId = targetIds[0] || "n1";
        const editor = state.editorById[pid] ?? emptyTechnicalEditor(pid);
        const chapter = (editor.chapters || []).find(
          (c) => String(c.id) === targetId,
        );
        const title = String(chapter?.title || "服务端一级目录");
        const bodyText = String(chapter?.body || FUSE_CHAPTER_BODY);
        await json(route, {
          id: `task_${state.taskPosts.length}`,
          type,
          status: "success",
          progress: 100,
          message: "ok",
          result: {
            suggestions: [
              {
                suggestionId: FUSE_SUGGESTION_ID,
                targetChapterId: targetId,
                targetTitle: title,
                action: "merge_suggest",
                confidence: 90,
                reason: "P11C 受控融合建议",
                sourceRefs: [
                  { kind: "template", id: TPL_ID, title: TPL_TITLE },
                  { kind: "card", id: CARD_ID, title: CARD_TITLE },
                ],
                base: {
                  bodyHash: FUSE_BODY_HASH,
                  bodyLength:
                    bodyText === FUSE_CHAPTER_BODY
                      ? FUSE_BODY_LEN
                      : Array.from(bodyText).length,
                  title,
                },
                currentPreview: bodyText.slice(0, 40),
                proposedMarkdown: "P11C_FUSE_PROPOSED_服务端权威建议正文",
                diffSummary: "受控差异",
              },
            ],
            model: "e2e-p11c",
            skippedSources: [],
            skippedInvalidCount: 0,
            baseEditorUpdatedAt: editor.updatedAt,
            quota: {
              templatesSelected: 1,
              cardsSelected: 1,
              targetsSelected: 1,
            },
            mode: "merge_suggest",
          },
        });
        return;
      }

      await json(route, {
        id: `task_${state.taskPosts.length}`,
        type,
        status: "success",
        progress: 100,
        message: "ok",
        result: {},
      });
      return;
    }

    const fuseRootMatch = path.match(
      /^\/api\/projects\/([^/]+)\/content-fuse-applications\/?$/,
    );
    if (fuseRootMatch) {
      const pid = fuseRootMatch[1];
      if (method === "POST") {
        let body: Record<string, unknown> = {};
        try {
          body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
        } catch {
          body = {};
        }
        state.fuseCreatePosts.push({ projectId: pid, body });
        state.orderLog.push(`fuse-create:${pid}`);
        await json(
          route,
          {
            batchId: `cfa_${state.fuseCreatePosts.length}`,
            appliedChapterCount: Array.isArray(body.suggestionIds)
              ? body.suggestionIds.length
              : 1,
            createdAt: "2026-07-15T12:30:00.000Z",
          },
          201,
        );
        return;
      }
      if (method === "GET") {
        state.fuseListGets.push(pid);
        state.orderLog.push(`fuse-list:${pid}`);
        await json(route, { items: [] });
        return;
      }
    }

    const fuseConsumeMatch = path.match(
      /^\/api\/projects\/([^/]+)\/content-fuse-applications\/([^/]+)\/consume\/?$/,
    );
    if (fuseConsumeMatch && method === "POST") {
      const pid = fuseConsumeMatch[1];
      state.fuseConsumePosts.push(pid);
      state.orderLog.push(`fuse-consume:${pid}`);
      await json(route, {
        restoredChapterCount: 1,
        skippedChapterCount: 0,
        consumedAt: "2026-07-15T12:31:00.000Z",
      });
      return;
    }

    if (
      /^\/api\/projects\/[^/]+\/(files|images)\/?$/.test(path) &&
      method === "POST"
    ) {
      await json(route, { id: "file_stub", filename: "stub.pdf" });
      return;
    }

    if (/^\/api\/projects\/[^/]+\/tasks\/[^/]+/.test(path)) {
      await json(route, { id: "task_stub", status: "success", progress: 100 });
      return;
    }

    if (
      /^\/api\/projects\/[^/]+\/artifacts\/workspace\/revise\/?$/.test(path) &&
      method === "POST"
    ) {
      await json(route, {
        status: "success",
        resultSummary: "修订完成",
        revisedContent: "修订后正文片段",
      });
      return;
    }

    if (
      /^\/api\/projects\/[^/]+\/artifacts\/[^/]+\/revise\/?$/.test(path) &&
      method === "POST"
    ) {
      await json(route, {
        status: "success",
        resultSummary: "修订完成",
        revisedContent: "修订后正文片段",
      });
      return;
    }

    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "p11c_unhandled", message: SECRET } },
      403,
    );
  });
}

async function seedOldEditors(page: Page, projectId: string, value?: string) {
  const key = editorsStorageKey(projectId);
  const v = value ?? fakeEditorsValue(projectId);
  await page.addInitScript(
    ({ k, val }) => {
      localStorage.setItem(k, val);
    },
    { k: key, val: v },
  );
  return { key, value: v };
}

async function readStorageSnapshot(page: Page): Promise<StorageSnapshot> {
  return page.evaluate(() => {
    const lsKeys: string[] = [];
    const ls: Record<string, string> = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i) ?? "";
      lsKeys.push(k);
      ls[k] = localStorage.getItem(k) ?? "";
    }
    const ssKeys: string[] = [];
    const ss: Record<string, string> = {};
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i) ?? "";
      ssKeys.push(k);
      ss[k] = sessionStorage.getItem(k) ?? "";
    }
    return {
      lsKeys: lsKeys.slice().sort(),
      ls,
      ssKeys: ssKeys.slice().sort(),
      ss,
      cookies: document.cookie,
    };
  });
}

async function readIdbNames(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    if (typeof indexedDB === "undefined") {
      throw new Error("indexedDB 不可用");
    }
    if (typeof indexedDB.databases !== "function") {
      throw new Error("indexedDB.databases 不可用");
    }
    const dbs = await indexedDB.databases();
    return dbs.map((d) => d.name ?? "");
  });
}

async function readClipboardProbe(page: Page) {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p11cClip?: { installed: boolean; read: number; write: number };
    };
    return g.__p11cClip ?? { installed: false, read: -1, write: -1 };
  });
}

function assertEditorsKeyFamilyExact(
  snap: StorageSnapshot,
  expected: Record<string, string>,
) {
  const family = snap.lsKeys
    .filter((k) => EDITORS_KEY_RE.test(k))
    .slice()
    .sort();
  const expectedKeys = Object.keys(expected).slice().sort();
  expect(family, "editors 键族必须精确等于预置旧键集合").toEqual(expectedKeys);
  for (const k of expectedKeys) {
    expect(snap.ls[k], `editors 键 ${k} 原值必须不变`).toBe(expected[k]);
  }
}

/**
 * 用途：关键场景强制断言 IndexedDB/Cookie/clipboard 与敏感片段不落存储。
 * 探针 installed=false 必须失败。
 * 二次开发：required/M3-D 场景应传入 forbidFragments（正文/task/suggestion 标识）；
 *       HttpOnly 会话 Cookie 值不得出现在 document.cookie。
 *       不得断言「任意 projectId 字符串绝对不存在」（guidance 非目标键可能含之）。
 */
async function assertStorageBoundary(
  page: Page,
  options?: {
    editorsExpected?: Record<string, string>;
    sessionKeysExact?: string[];
    /** 额外禁止落盘的正文/业务标识（如 FUSE 正文、suggestionId） */
    forbidFragments?: string[];
    /** required 登录后：HttpOnly 会话 Cookie 值不得出现在 document.cookie */
    httpOnlySessionCookieValue?: string;
  },
) {
  const snap = await readStorageSnapshot(page);
  if (options?.editorsExpected) {
    assertEditorsKeyFamilyExact(snap, options.editorsExpected);
  }
  if (options?.sessionKeysExact) {
    expect(snap.ssKeys).toEqual(options.sessionKeysExact.slice().sort());
  }
  expect(snap.cookies, "Cookie 必须为空或无 CSRF/SECRET").not.toContain(
    CSRF_TOKEN,
  );
  expect(snap.cookies).not.toContain(SECRET);
  if (options?.httpOnlySessionCookieValue) {
    expect(
      snap.cookies,
      "HttpOnly 会话 Cookie 不得对 document.cookie 可见",
    ).not.toContain(options.httpOnlySessionCookieValue);
    expect(snap.cookies).not.toContain(SESSION_COOKIE_NAME);
  }
  expect(await readIdbNames(page)).toEqual([]);
  const clip = await readClipboardProbe(page);
  expect(clip.installed, "clipboard 探针必须安装成功").toBe(true);
  expect(clip.read).toBe(0);
  expect(clip.write).toBe(0);
  const extraFrags = options?.forbidFragments ?? [];
  for (const v of Object.values(snap.ls)) {
    expect(v).not.toContain(SECRET);
    expect(v).not.toContain(CSRF_TOKEN);
    expect(v).not.toContain(SESSION_COOKIE_VALUE);
    for (const frag of extraFrags) {
      expect(v, `localStorage 不得含片段 ${frag}`).not.toContain(frag);
    }
  }
  for (const v of Object.values(snap.ss)) {
    expect(v).not.toContain(SECRET);
    expect(v).not.toContain(CSRF_TOKEN);
    expect(v).not.toContain(SESSION_COOKIE_VALUE);
    for (const frag of extraFrags) {
      expect(v, `sessionStorage 不得含片段 ${frag}`).not.toContain(frag);
    }
  }
  return snap;
}

/** 用途：required 场景经真实登录 UI 建立会话（假值仅 E2E 使用）。 */
async function loginViaUi(page: Page) {
  await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
    timeout: 20_000,
  });
  await page.locator('input[name="username"]').fill(E2E_LOGIN_USER);
  await page.locator('input[name="password"]').fill(E2E_LOGIN_PASS);
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByRole("heading", { name: "本机登录" })).toHaveCount(0, {
    timeout: 15_000,
  });
}

/** 用途：断言 PUT 同时携带精确 CSRF 与登录会话 Cookie。 */
function assertRequiredPutAuth(put: PutRecord) {
  expect(put.headers["x-csrf-token"]).toBe(CSRF_TOKEN);
  const cookie = put.headers["cookie"] || "";
  expect(cookie, "PUT 必须携带登录会话 Cookie").toContain(
    `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}`,
  );
  expect(cookie).not.toContain(CSRF_TOKEN);
  expect(cookie).not.toContain(SECRET);
}

function collectConsole(page: Page) {
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

function appConsoleLines(lines: string[]): string[] {
  return lines.filter((line) => {
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

function assertCleanConsole(lines: string[], extra: string[] = []) {
  expect(appConsoleLines(lines)).toEqual([]);
  const joined = lines.join("\n");
  for (const b of [
    SECRET,
    REAL_TECH_A,
    REAL_TECH_B,
    LOCAL_SECRET,
    CSRF_TOKEN,
    "/api/projects",
    "editor_state_get_failed",
    "editor_state_put_failed",
    "p11c_forbidden",
    "response_matrix_version_conflict",
    ...extra,
  ]) {
    expect(joined, `console 敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

function assertNoSensitiveInText(text: string, extra: string[] = []) {
  for (const b of [
    SECRET,
    "editor_state_get_failed",
    "editor_state_put_failed",
    "p11c_forbidden",
    CSRF_TOKEN,
    "response_matrix_version_conflict",
    ...extra,
  ]) {
    expect(text, `页面敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

async function softNavigateTech(
  page: Page,
  projectId: string,
  step = "analysis",
) {
  const url = `/technical-plan/${projectId}/${step}`;
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function openTechWorkspace(
  page: Page,
  projectId: string,
  step = "analysis",
) {
  await page.goto(`/technical-plan/${projectId}/${step}`);
}

async function expectWorkspaceReady(page: Page, projectName: string) {
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await expect(page.getByText("服务端编辑态")).toBeVisible();
}

async function expectLoadErrorCard(page: Page) {
  await expect(page.getByTestId("technical-editor-load-error")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(LOAD_ERROR)).toBeVisible();
  await expect(page.getByTestId("technical-editor-retry")).toBeVisible();
  await expect(page.getByRole("link", { name: "返回列表" })).toBeVisible();
  await expect(page.getByTestId("technical-editor-workspace")).toHaveCount(0);
  await expect(page.getByTestId("technical-analysis-overview")).toHaveCount(0);
}

/** 用途：按 sourceText 定位矩阵行备注，避免评分点行 strict 冲突。 */
function matrixNotesLocator(page: Page, sourceText = TECH_REQ) {
  return page
    .locator("article.response-matrix__item")
    .filter({ hasText: sourceText })
    .getByLabel("响应备注");
}

test.describe("P11C 技术标编辑态真实数据收口", () => {
  test("服务端真实内容；旧 editors 键忽略保值；演示入口消失", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C真实技术标甲",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C真实技术标甲");

    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW,
    );
    await expect(page.getByText(LOCAL_SECRET)).toHaveCount(0);
    await expect(page.getByText(MOCK_SNIPPET)).toHaveCount(0);
    await expect(page.getByText("填入演示数据")).toHaveCount(0);
    await expect(page.getByText("从招标/知识库抽取")).toHaveCount(0);
    await expect(page.getByText("编辑：本地")).toHaveCount(0);
    await expect(page.getByText("编辑：后端")).toHaveCount(0);

    await page.goto(`/technical-plan/${REAL_TECH_A}/outline`);
    await expect(page.getByText("服务端一级目录")).toBeVisible();
    await expect(page.getByText("恢复示例目录")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重置" })).toHaveCount(0);
    await expect(page.getByText("10:24:08")).toHaveCount(0);

    await page.goto(`/technical-plan/${REAL_TECH_A}/content`);
    await expect(
      page.getByRole("textbox", { name: /正文：服务端一级目录/ }),
    ).toHaveValue(FUSE_CHAPTER_BODY);
    await expect(page.getByText("mockChapters")).toHaveCount(0);

    await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
      sessionKeysExact: [],
    });
    assertCleanConsole(consoleLines);
  });

  test("canonical 空态：全空响应不补 mock、不写 editors 键", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C空态技术标",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = emptyTechnicalEditor(REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C空态技术标");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      "",
    );
    await expect(page.getByText(MOCK_SNIPPET)).toHaveCount(0);
    await expect(page.getByText("填入演示数据")).toHaveCount(0);

    await page.goto(`/technical-plan/${REAL_TECH_A}/outline`);
    await expect(page.getByText("尚无目录")).toBeVisible();

    await page.goto(`/technical-plan/${REAL_TECH_A}/facts`);
    await expect(page.getByText("暂无全局事实")).toBeVisible();
    await expect(page.getByText("从招标/知识库抽取")).toHaveCount(0);

    await page.goto(`/technical-plan/${REAL_TECH_A}/content`);
    await expect(page.getByText("暂无章节")).toBeVisible();
    await expect(page.getByText("mockChapters")).toHaveCount(0);

    const snap = await assertStorageBoundary(page, { sessionKeysExact: [] });
    const editorKeys = snap.lsKeys.filter((k) => EDITORS_KEY_RE.test(k));
    expect(editorKeys, "不得新写 editors 键").toEqual([]);
    assertCleanConsole(consoleLines);
  });

  test("GET 500 固定失败卡；零旧内容/零 PUT；重试 +1 GET 成功", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C失败后重试",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.getMode[REAL_TECH_A] = { kind: "fail", status: 500 };
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectLoadErrorCard(page);
    await expect(page.getByText(LOCAL_SECRET)).toHaveCount(0);
    await expect(page.getByText(REAL_OVERVIEW)).toHaveCount(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText);
    expect(state.putLog.length).toBe(0);

    const getsBefore = state.getLog.filter((id) => id === REAL_TECH_A).length;
    expect(getsBefore).toBeGreaterThanOrEqual(1);

    state.getMode[REAL_TECH_A] = { kind: "ok" };
    await page.getByTestId("technical-editor-retry").click();
    await expectWorkspaceReady(page, "P11C失败后重试");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW,
    );

    const getsAfter = state.getLog.filter((id) => id === REAL_TECH_A).length;
    expect(getsAfter).toBe(getsBefore + 1);
    expect(state.putLog.length).toBe(0);

    await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
    });
    assertCleanConsole(consoleLines);
  });

  test("GET 401 同固定失败卡；零 PUT", async ({ page }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C状态401",
    });
    const state = createProbeState([project]);
    state.getMode[REAL_TECH_A] = { kind: "fail", status: 401 };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectLoadErrorCard(page);
    expect(state.putLog.length).toBe(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
  });

  test("GET 404 同固定失败卡；零 PUT", async ({ page }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C状态404",
    });
    const state = createProbeState([project]);
    state.getMode[REAL_TECH_A] = { kind: "fail", status: 404 };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectLoadErrorCard(page);
    expect(state.putLog.length).toBe(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
  });

  test("加载延迟期间不渲染旧 mock 或编辑控件", async ({ page }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C加载延迟",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.getMode[REAL_TECH_A] = { kind: "delay", ms: 600, then: "ok" };
    await seedOldEditors(page, REAL_TECH_A);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expect(page.getByTestId("technical-editor-loading")).toBeVisible();
    await expect(page.getByTestId("technical-analysis-overview")).toHaveCount(0);
    await expect(page.getByText(LOCAL_SECRET)).toHaveCount(0);
    await expect(page.getByText(MOCK_SNIPPET)).toHaveCount(0);

    await expectWorkspaceReady(page, "P11C加载延迟");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW,
    );
  });

  test("编辑后 800ms 防抖 PUT 精确 body；required 真登录 Cookie+CSRF；旧键保值", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C防抖保存",
    });
    const state = createProbeState([project]);
    state.authRequired = true;
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    // 必须经登录 UI：/auth/me 401 → POST /auth/login（Set-Cookie + CSRF）
    await loginViaUi(page);
    await expectWorkspaceReady(page, "P11C防抖保存");
    expect(state.loginPosts).toBe(1);

    const putsBefore = state.putLog.length;
    const edited = `${REAL_OVERVIEW}\n用户追加概述`;
    await page.getByTestId("technical-analysis-overview").fill(edited);

    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBefore + 1);

    const put = state.putLog[state.putLog.length - 1];
    expect(put.projectId).toBe(REAL_TECH_A);
    assertRequiredPutAuth(put);
    const keys = Object.keys(put.body).slice().sort();
    expect(keys).toEqual(
      [
        "analysis",
        "analysisOverview",
        "chapters",
        "expectedStateVersion",
        "facts",
        "guidance",
        "mode",
        "outline",
        "responseMatrix",
        "responseMatrixVersion",
      ]
        .slice()
        .sort(),
    );
    expect(put.body.expectedStateVersion).toBe(seedStateVersion(10));
    expect(isValidStateVersion(put.body.expectedStateVersion)).toBe(true);
    expect(put.body.analysisOverview).toBe(edited);
    expect(
      (put.body.analysis as { overview?: string } | undefined)?.overview,
    ).toBe(edited);
    expect(put.body).not.toHaveProperty("parsedMarkdown");

    // HttpOnly 会话 Cookie 对 document.cookie 不可见；CSRF/正文不落存储
    await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
      httpOnlySessionCookieValue: SESSION_COOKIE_VALUE,
      forbidFragments: [
        FUSE_CHAPTER_BODY,
        FUSE_SUGGESTION_ID,
        REAL_OVERVIEW,
        "task_",
        E2E_LOGIN_PASS,
      ],
    });
    assertCleanConsole(consoleLines);
    // 登录仅一次
    expect(state.loginPosts).toBe(1);
  });

  for (const status of [401, 403] as const) {
    test(`PUT ${status} 固定保存错误；再编辑新增 PUT 并清错`, async ({
      page,
    }) => {
      const project = makeProject({
        id: REAL_TECH_A,
        name: `P11C保存失败${status}`,
      });
      const state = createProbeState([project]);
      state.editorById[REAL_TECH_A] = realTechnicalEditor(
        REAL_TECH_A,
        REAL_OVERVIEW,
      );
      state.putMode[REAL_TECH_A] = { kind: "fail", status };
      const seeded = await seedOldEditors(page, REAL_TECH_A);
      const consoleLines = collectConsole(page);
      await installP11cRoutes(page, state);

      await openTechWorkspace(page, REAL_TECH_A, "analysis");
      await expectWorkspaceReady(page, `P11C保存失败${status}`);

      await page
        .getByTestId("technical-analysis-overview")
        .fill(`${REAL_OVERVIEW}\n保存失败编辑${status}`);

      await expect(page.getByTestId("technical-editor-save-error")).toBeVisible({
        timeout: 5_000,
      });
      await expect(page.getByText(SAVE_ERROR)).toBeVisible();
      await expect(page.getByText(SECRET)).toHaveCount(0);
      const bodyText = await page.locator("body").innerText();
      assertNoSensitiveInText(bodyText, ["editor_state_put_failed"]);
      expect(bodyText).not.toContain(SECRET);
      expect(bodyText).not.toContain("/api/projects");
      expect(bodyText).not.toContain(REAL_TECH_A);

      const putsFail = state.putLog.length;
      expect(putsFail).toBeGreaterThanOrEqual(1);

      state.putMode[REAL_TECH_A] = { kind: "ok" };
      await page
        .getByTestId("technical-analysis-overview")
        .fill(`${REAL_OVERVIEW}\n再次编辑成功${status}`);

      await expect
        .poll(() => state.putLog.length, { timeout: 5_000 })
        .toBe(putsFail + 1);
      await expect(page.getByTestId("technical-editor-save-error")).toHaveCount(
        0,
      );

      await assertStorageBoundary(page, {
        editorsExpected: { [seeded.key]: seeded.value },
      });
      assertCleanConsole(consoleLines);
    });
  }

  test("PUT 500 固定保存错误；再编辑新增 PUT 并清错", async ({ page }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C保存失败",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = { kind: "fail", status: 500 };
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C保存失败");

    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n保存失败编辑`);

    await expect(page.getByTestId("technical-editor-save-error")).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByText(SAVE_ERROR)).toBeVisible();
    await expect(page.getByText(SECRET)).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText, ["editor_state_put_failed"]);

    const putsFail = state.putLog.length;
    expect(putsFail).toBeGreaterThanOrEqual(1);

    state.putMode[REAL_TECH_A] = { kind: "ok" };
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n再次编辑成功`);

    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsFail + 1);
    await expect(page.getByTestId("technical-editor-save-error")).toHaveCount(0);

    await assertStorageBoundary(page);
    assertCleanConsole(consoleLines);
  });

  test("409 固定中文矩阵冲突；不展示 SECRET/detail；无通用保存错误", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C矩阵冲突",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = {
      kind: "conflict",
      remoteNotes: "远端冲突备注",
      remoteVersion: "ver_remote_conflict",
    };
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C矩阵冲突");

    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n触发冲突`);

    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByTestId("technical-editor-save-error")).toHaveCount(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    await expect(page.getByText("response_matrix_version_conflict")).toHaveCount(
      0,
    );
    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText);
    assertCleanConsole(consoleLines);
  });

  test("required 合并 PUT：真登录 Cookie+CSRF；body 仅两键；二次 409 不循环", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C合并CSRF",
    });
    const state = createProbeState([project]);
    state.authRequired = true;
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await loginViaUi(page);
    await expectWorkspaceReady(page, "P11C合并CSRF");
    expect(state.loginPosts).toBe(1);
    const notesBox = matrixNotesLocator(page);
    await expect(notesBox).toBeVisible();

    // 先成功一次普通 PUT：建立 base，并证明 Cookie+CSRF 同时携带
    const putsBase = state.putLog.length;
    await notesBox.fill("基线备注");
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBase + 1);
    const normalPut = state.putLog[state.putLog.length - 1];
    assertRequiredPutAuth(normalPut);
    expect(Object.keys(normalPut.body).includes("responseMatrix")).toBe(true);

    // 触发 notes 字段冲突（本地 vs 远端同字段）
    state.putMode[REAL_TECH_A] = {
      kind: "conflict",
      remoteNotes: "远端备注-冲突侧",
      remoteVersion: "ver_remote_merge_1",
      times: 1,
    };
    const putsBeforeConflict = state.putLog.length;
    await notesBox.fill("本地备注-冲突侧");
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBeforeConflict + 1);

    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByTestId("response-matrix-merge-conflicts")).toBeVisible();
    await expect(page.getByTestId("merge-field-conflict-notes")).toBeVisible();
    await expect(page.getByTestId("merge-local-value-notes")).toContainText(
      "本地备注-冲突侧",
    );
    await expect(page.getByTestId("merge-remote-value-notes")).toContainText(
      "远端备注-冲突侧",
    );

    const applyBtn = page.getByTestId("response-matrix-apply-merge");
    await expect(applyBtn).toBeDisabled();
    await page.getByTestId("merge-choose-local-notes").click();
    await expect(applyBtn).toBeEnabled();

    // 应用前服务端仍为远端版本，证明应用前不写库
    expect(state.editorById[REAL_TECH_A].responseMatrix[0]?.notes).toBe(
      "远端备注-冲突侧",
    );
    const putsBeforeApply = state.putLog.length;

    // 首次应用合并：故意二次 409，禁止自动循环
    state.putMode[REAL_TECH_A] = {
      kind: "conflict",
      remoteNotes: "远端二次变更备注",
      remoteVersion: "ver_remote_merge_2",
      times: 1,
    };
    await applyBtn.click();
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBeforeApply + 1);
    const mergePut409 = state.putLog[state.putLog.length - 1];
    assertRequiredPutAuth(mergePut409);
    expect(Object.keys(mergePut409.body).slice().sort()).toEqual(
      ["expectedStateVersion", "responseMatrix", "responseMatrixVersion"]
        .slice()
        .sort(),
    );
    expect(isValidStateVersion(mergePut409.body.expectedStateVersion)).toBe(
      true,
    );
    expect(mergePut409.body.responseMatrixVersion).toBe("ver_remote_merge_1");
    await expect(
      page.getByTestId("response-matrix-merge-apply-error"),
    ).toContainText("未自动重试");
    await expect(page.getByTestId("response-matrix-apply-merge")).toHaveCount(0);
    // 仅一次合并 PUT，无自动重试第二发
    expect(state.putLog.length).toBe(putsBeforeApply + 1);

    // 重新载入远端后再次进入可合并，成功应用
    await page.getByRole("button", { name: "重新载入远端矩阵" }).click();
    await expect(matrixNotesLocator(page)).toHaveValue("远端二次变更备注");
    // 再制造冲突并成功合并
    state.putMode[REAL_TECH_A] = {
      kind: "conflict",
      remoteNotes: "远端最终备注",
      remoteVersion: "ver_remote_merge_3",
      times: 1,
    };
    const putsBeforeSecond = state.putLog.length;
    await matrixNotesLocator(page).fill("本地最终备注");
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBeforeSecond + 1);
    await expect(page.getByTestId("response-matrix-merge-conflicts")).toBeVisible({
      timeout: 5_000,
    });
    await page.getByTestId("merge-choose-remote-notes").click();
    const applyBtn2 = page.getByTestId("response-matrix-apply-merge");
    await expect(applyBtn2).toBeEnabled();
    state.putMode[REAL_TECH_A] = { kind: "ok" };
    const putsBeforeOk = state.putLog.length;
    await applyBtn2.click();
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBeforeOk + 1);
    const mergePutOk = state.putLog[state.putLog.length - 1];
    assertRequiredPutAuth(mergePutOk);
    expect(Object.keys(mergePutOk.body).slice().sort()).toEqual(
      ["expectedStateVersion", "responseMatrix", "responseMatrixVersion"]
        .slice()
        .sort(),
    );
    expect(isValidStateVersion(mergePutOk.body.expectedStateVersion)).toBe(
      true,
    );
    expect(mergePutOk.body.responseMatrixVersion).toBe("ver_remote_merge_3");
    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toHaveCount(0);
    await expect(matrixNotesLocator(page)).toHaveValue("远端最终备注");

    // 全程仅一次登录；会话 Cookie 对 document 不可见
    expect(state.loginPosts).toBe(1);
    await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
      httpOnlySessionCookieValue: SESSION_COOKIE_VALUE,
      forbidFragments: [
        FUSE_CHAPTER_BODY,
        FUSE_SUGGESTION_ID,
        "基线备注",
        "本地最终备注",
        "task_",
        E2E_LOGIN_PASS,
      ],
    });
    assertCleanConsole(consoleLines);
  });

  test("普通任务成功后 editor GET 失败：进入固定加载失败态", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C任务后刷新失败",
      technicalPlanStep: 2,
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C任务后刷新失败");

    state.failNextEditorGetAfterTask[REAL_TECH_A] = true;
    const tasksBefore = state.taskPosts.length;
    await page.getByRole("button", { name: /AI 招标分析/ }).click();

    await expect
      .poll(() => state.taskPosts.length, { timeout: 10_000 })
      .toBe(tasksBefore + 1);
    expect(state.taskPosts[state.taskPosts.length - 1]).toEqual({
      projectId: REAL_TECH_A,
      type: "analyze",
    });

    await expectLoadErrorCard(page);
    await expect(page.getByText(REAL_OVERVIEW)).toHaveCount(0);
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === REAL_TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);

    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      "任务后恢复权威概述",
    );
    await page.getByTestId("technical-editor-retry").click();
    await expectWorkspaceReady(page, "P11C任务后刷新失败");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      "任务后恢复权威概述",
    );
    assertCleanConsole(consoleLines);
  });

  test("M3-D 对话框：业务写入成功但 reload 失败；关闭后才显示 P11C 失败卡", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11CM3D兼容",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "content");
    await expectWorkspaceReady(page, "P11CM3D兼容");
    await expect(
      page.getByRole("textbox", { name: /正文：服务端一级目录/ }),
    ).toHaveValue(FUSE_CHAPTER_BODY);

    await page.getByRole("button", { name: "模板卡片融合建议" }).click();
    const dialog = page.getByRole("dialog", { name: "模板卡片融合建议" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel(`模板 ${TPL_TITLE}`).check();
    await dialog.getByLabel(`卡片 ${CARD_TITLE}`).check();
    await dialog.getByLabel("目标章节 服务端一级目录").check();

    const tasksBefore = state.taskPosts.length;
    await dialog.getByRole("button", { name: "生成只读融合建议" }).click();
    await expect(dialog.getByText(/已生成 \d+ 条只读建议/)).toBeVisible({
      timeout: 15_000,
    });
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === REAL_TECH_A && t.type === "content_fuse",
      ).length,
    ).toBe(1);
    expect(state.taskPosts.length).toBe(tasksBefore + 1);

    // 勾选建议并在确认前设置唯一 reload GET 失败
    await dialog.getByLabel("勾选写入建议 服务端一级目录").check();
    state.failNextEditorGet[REAL_TECH_A] = true;
    const getsBeforeApply = state.getLog.filter((id) => id === REAL_TECH_A)
      .length;
    const fuseBefore = state.fuseCreatePosts.length;

    await dialog.getByTestId("content-fuse-confirm-apply").click();
    await expect
      .poll(() => state.fuseCreatePosts.length, { timeout: 10_000 })
      .toBe(fuseBefore + 1);
    expect(state.fuseCreatePosts.length).toBe(1);
    expect(state.fuseConsumePosts.length).toBe(0);

    await expect(dialog.getByTestId("content-fuse-local-error")).toContainText(
      MSG_APPLY_RELOAD_FAIL,
    );
    // 对话框仍打开；P11C 失败卡不得提前卸载对话框
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("technical-editor-load-error")).toHaveCount(0);

    await expect
      .poll(
        () => state.getLog.filter((id) => id === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsBeforeApply + 1);

    // 关闭后出现 P11C 固定加载失败卡
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expectLoadErrorCard(page);

    // 显式重试精确 +1 GET 后恢复
    const getsBeforeRetry = state.getLog.filter((id) => id === REAL_TECH_A)
      .length;
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      "M3D后恢复概述",
    );
    await page.getByTestId("technical-editor-retry").click();
    await expectWorkspaceReady(page, "P11CM3D兼容");
    const getsAfterRetry = state.getLog.filter((id) => id === REAL_TECH_A)
      .length;
    expect(getsAfterRetry).toBe(getsBeforeRetry + 1);
    // 无二次 POST / 重复 consume
    expect(state.fuseCreatePosts.length).toBe(1);
    expect(state.fuseConsumePosts.length).toBe(0);
    expect(
      state.taskPosts.filter((t) => t.type === "content_fuse").length,
    ).toBe(1);

    await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
      forbidFragments: [
        FUSE_CHAPTER_BODY,
        FUSE_SUGGESTION_ID,
        "task_",
        "sug_e2e",
      ],
    });
    assertCleanConsole(consoleLines);
  });

  test("SPA A→B：A 挂起 PUT 不阻塞 B 独立保存", async ({ page }) => {
    const projectA = makeProject({ id: REAL_TECH_A, name: "技术项目甲挂起" });
    const projectB = makeProject({ id: REAL_TECH_B, name: "技术项目乙独立" });
    const state = createProbeState([projectA, projectB]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.editorById[REAL_TECH_B] = realTechnicalEditor(
      REAL_TECH_B,
      REAL_OVERVIEW_B,
    );
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "技术项目甲挂起");

    // A 普通 PUT 保持未释放（队头挂起）
    const holdA = createHoldGate();
    state.putMode[REAL_TECH_A] = { kind: "gate", gate: holdA, then: "ok" };
    const putsABefore = state.putLog.filter((p) => p.projectId === REAL_TECH_A)
      .length;
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n甲挂起中的PUT`);
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsABefore + 1);
    expect(holdA.isReleased()).toBe(false);

    // 切 B：在 A 仍挂起时，B 须 5 秒内独立发出且成功一次 PUT
    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙独立");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW_B,
    );

    const putsBBefore = state.putLog.filter((p) => p.projectId === REAL_TECH_B)
      .length;
    const editedB = `${REAL_OVERVIEW_B}\n乙独立保存`;
    await page.getByTestId("technical-analysis-overview").fill(editedB);

    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
        { timeout: 5_000 },
      )
      .toBe(putsBBefore + 1);

    // A 仍未释放时 B 已成功；body/project 均属 B，无 A 内容
    expect(holdA.isReleased()).toBe(false);
    const putB = state.putLog
      .filter((p) => p.projectId === REAL_TECH_B)
      .at(-1)!;
    expect(putB.projectId).toBe(REAL_TECH_B);
    expect(putB.body.analysisOverview).toBe(editedB);
    expect(String(putB.body.analysisOverview)).not.toContain("甲挂起");
    expect(String(putB.raw)).not.toContain(REAL_OVERVIEW);
    expect(String(putB.raw)).not.toContain("甲挂起中的PUT");
    // B 请求体版本属 B 初始服务端版本（非 A 内容）
    expect(putB.body.responseMatrixVersion).toBe("ver_srv_1");
    expect(
      (putB.body.analysis as { overview?: string } | undefined)?.overview,
    ).toBe(editedB);

    const putsBAfterSuccess = state.putLog.filter(
      (p) => p.projectId === REAL_TECH_B,
    ).length;

    // 释放 A：B 内容/错误/冲突/base 不变，且无额外 B PUT
    holdA.release();
    await expect
      .poll(async () => {
        const text = await page
          .getByTestId("technical-analysis-overview")
          .inputValue();
        const saveErr = await page
          .getByTestId("technical-editor-save-error")
          .count();
        const loadErr = await page
          .getByTestId("technical-editor-load-error")
          .count();
        const conflict = await page.getByText(MATRIX_CONFLICT_MSG).count();
        return `${text}|${saveErr}|${loadErr}|${conflict}`;
      }, { timeout: 5_000 })
      .toBe(`${editedB}|0|0|0`);

    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
        { timeout: 2_000 },
      )
      .toBe(putsBAfterSuccess);

    expect(
      state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
    ).toBe(putsBBefore + 1);
    assertCleanConsole(consoleLines);
  });

  test("SPA A→B：迟到 A GET/PUT 不污染 B", async ({ page }) => {
    const projectA = makeProject({ id: REAL_TECH_A, name: "技术项目甲A" });
    const projectB = makeProject({ id: REAL_TECH_B, name: "技术项目乙B" });
    const state = createProbeState([projectA, projectB]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.editorById[REAL_TECH_B] = realTechnicalEditor(
      REAL_TECH_B,
      REAL_OVERVIEW_B,
    );
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    state.getMode[REAL_TECH_A] = { kind: "delay", ms: 800, then: "ok" };
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expect
      .poll(() => state.getLog.filter((id) => id === REAL_TECH_A).length, {
        timeout: 5_000,
      })
      .toBeGreaterThanOrEqual(1);
    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙B");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW_B,
    );
    await expect(page.getByText(REAL_OVERVIEW)).toHaveCount(0);
    await expect(page.getByTestId("technical-editor-load-error")).toHaveCount(0);

    state.getMode[REAL_TECH_A] = { kind: "ok" };
    state.getMode[REAL_TECH_B] = { kind: "ok" };
    await softNavigateTech(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "技术项目甲A");

    state.putMode[REAL_TECH_A] = { kind: "delay", ms: 1200, then: "ok" };
    const putsABefore = state.putLog.filter((p) => p.projectId === REAL_TECH_A)
      .length;
    const putsBBefore = state.putLog.filter((p) => p.projectId === REAL_TECH_B)
      .length;
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n甲的迟到PUT`);

    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsABefore + 1);

    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙B");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW_B,
    );

    await expect
      .poll(
        async () => {
          const text = await page
            .getByTestId("technical-analysis-overview")
            .inputValue();
          const saveErr = await page
            .getByTestId("technical-editor-save-error")
            .count();
          const loadErr = await page
            .getByTestId("technical-editor-load-error")
            .count();
          return `${text}|${saveErr}|${loadErr}`;
        },
        { timeout: 5_000 },
      )
      .toBe(`${REAL_OVERVIEW_B}|0|0`);

    expect(
      state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
    ).toBe(putsBBefore);
    assertCleanConsole(consoleLines);
  });

  test("SPA A→B：任务 reload / PUT 失败 / PUT 409 迟到均不污染 B", async ({
    page,
  }) => {
    const projectA = makeProject({ id: REAL_TECH_A, name: "技术项目甲A2" });
    const projectB = makeProject({ id: REAL_TECH_B, name: "技术项目乙B2" });
    const state = createProbeState([projectA, projectB]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.editorById[REAL_TECH_B] = realTechnicalEditor(
      REAL_TECH_B,
      REAL_OVERVIEW_B,
    );
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    // --- 1) 任务成功后的 reload GET 迟到 ---
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "技术项目甲A2");
    const reloadGate = createHoldGate();
    state.getMode[REAL_TECH_A] = {
      kind: "gate",
      gate: reloadGate,
      then: "ok",
    };
    const getsABefore = state.getLog.filter((id) => id === REAL_TECH_A).length;
    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    await expect
      .poll(() => state.taskPosts.filter((t) => t.type === "analyze").length, {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBeGreaterThan(getsABefore);

    const putsB0 = state.putLog.filter((p) => p.projectId === REAL_TECH_B)
      .length;
    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙B2");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW_B,
    );
    // 释放 A 的迟到 reload
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      "甲迟到reload污染探测",
    );
    reloadGate.release();
    await expect
      .poll(async () => {
        const text = await page
          .getByTestId("technical-analysis-overview")
          .inputValue();
        const loadErr = await page
          .getByTestId("technical-editor-load-error")
          .count();
        return `${text}|${loadErr}`;
      }, { timeout: 5_000 })
      .toBe(`${REAL_OVERVIEW_B}|0`);
    await expect(page.getByText("甲迟到reload污染探测")).toHaveCount(0);
    expect(
      state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
    ).toBe(putsB0);

    // --- 2) 普通 PUT 失败迟到 ---
    state.getMode[REAL_TECH_A] = { kind: "ok" };
    state.getMode[REAL_TECH_B] = { kind: "ok" };
    await softNavigateTech(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "技术项目甲A2");
    const failGate = createHoldGate();
    state.putMode[REAL_TECH_A] = {
      kind: "gate",
      gate: failGate,
      then: "fail",
      status: 500,
    };
    const putsAFailBefore = state.putLog.filter(
      (p) => p.projectId === REAL_TECH_A,
    ).length;
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n甲失败PUT`);
    await expect
      .poll(
        () =>
          state.putLog.filter((p) => p.projectId === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsAFailBefore + 1);
    const putsB1 = state.putLog.filter((p) => p.projectId === REAL_TECH_B)
      .length;
    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙B2");
    failGate.release();
    await expect
      .poll(async () => {
        const text = await page
          .getByTestId("technical-analysis-overview")
          .inputValue();
        const saveErr = await page
          .getByTestId("technical-editor-save-error")
          .count();
        return `${text}|${saveErr}`;
      }, { timeout: 5_000 })
      .toBe(`${REAL_OVERVIEW_B}|0`);
    expect(
      state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
    ).toBe(putsB1);

    // --- 3) PUT 409（含 res.json）迟到 ---
    await softNavigateTech(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "技术项目甲A2");
    // 先成功 PUT 建立 base
    state.putMode[REAL_TECH_A] = { kind: "ok" };
    await matrixNotesLocator(page).fill("甲基线备注");
    await expect
      .poll(
        () =>
          state.putLog.filter((p) => p.projectId === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBeGreaterThan(putsAFailBefore + 1);

    const conflictGate = createHoldGate();
    state.putMode[REAL_TECH_A] = {
      kind: "gate",
      gate: conflictGate,
      then: "conflict",
      remoteNotes: "甲迟到远端备注",
      remoteVersion: "ver_a_late_conflict",
    };
    const putsA409Before = state.putLog.filter(
      (p) => p.projectId === REAL_TECH_A,
    ).length;
    await matrixNotesLocator(page).fill("甲本地迟到备注");
    await expect
      .poll(
        () =>
          state.putLog.filter((p) => p.projectId === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsA409Before + 1);
    const putsB2 = state.putLog.filter((p) => p.projectId === REAL_TECH_B)
      .length;
    await softNavigateTech(page, REAL_TECH_B, "analysis");
    await expectWorkspaceReady(page, "技术项目乙B2");
    conflictGate.release();
    await expect
      .poll(async () => {
        const text = await page
          .getByTestId("technical-analysis-overview")
          .inputValue();
        const notes = await matrixNotesLocator(page).inputValue();
        const conflict = await page.getByText(MATRIX_CONFLICT_MSG).count();
        const mergeUi = await page
          .getByTestId("response-matrix-merge-preview")
          .count();
        const saveErr = await page
          .getByTestId("technical-editor-save-error")
          .count();
        const loadErr = await page
          .getByTestId("technical-editor-load-error")
          .count();
        return `${text}|${notes}|${conflict}|${mergeUi}|${saveErr}|${loadErr}`;
      }, { timeout: 5_000 })
      .toBe(`${REAL_OVERVIEW_B}||0|0|0|0`);
    // B 备注仍为服务端初始（空串）
    await expect(matrixNotesLocator(page)).toHaveValue("");
    expect(
      state.putLog.filter((p) => p.projectId === REAL_TECH_B).length,
    ).toBe(putsB2);
    assertCleanConsole(consoleLines);
  });

  test("网络白名单：未知 API/宽前缀后缀/未知项目与外网可观测阻断", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_TECH_A,
      name: "P11C网络探针",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const seeded = await seedOldEditors(page, REAL_TECH_A);
    const consoleLines = collectConsole(page);
    await installP11cRoutes(page, state);

    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P11C网络探针");
    const baseline = await assertStorageBoundary(page, {
      editorsExpected: { [seeded.key]: seeded.value },
    });
    const idbBefore = await readIdbNames(page);
    const clipBefore = await readClipboardProbe(page);

    const probeResult = await page.evaluate(async (knownId) => {
      const out: Record<string, number> = {};
      const hit = async (key: string, url: string, init?: RequestInit) => {
        try {
          const r = await fetch(url, init);
          out[key] = r.status;
        } catch {
          out[key] = -1;
        }
      };
      await hit("unknown", "/api/unknown-p11c-probe");
      await hit("projectsUnknown", "/api/projects/unknown-p11c-probe");
      await hit(
        "editorUnknown",
        `/api/projects/${knownId}/editor-state/unknown`,
      );
      await hit("tasksUnknownAction", `/api/projects/${knownId}/tasks/foo/bar`);
      await hit("workspacesExtra", "/api/workspaces/extra-suffix");
      await hit("settingsExtra", "/api/settings/secret-extra");
      await hit("templatesExtra", "/api/templates/not-list");
      await hit("cardsExtra", "/api/cards/not-list");
      await hit("hrExtra", "/api/hr/team-recommendations/extra");
      try {
        await fetch("https://example.invalid/p11c-external");
        out.external = 1;
      } catch {
        out.external = 0;
      }
      return out;
    }, REAL_TECH_A);

    expect(probeResult.unknown).toBe(403);
    expect(probeResult.projectsUnknown).toBe(403);
    expect(probeResult.editorUnknown).toBe(403);
    expect(probeResult.tasksUnknownAction).toBe(403);
    expect(probeResult.workspacesExtra).toBe(403);
    expect(probeResult.settingsExtra).toBe(403);
    expect(probeResult.templatesExtra).toBe(403);
    expect(probeResult.cardsExtra).toBe(403);
    expect(probeResult.hrExtra).toBe(403);
    expect(probeResult.external).toBe(0);

    expect(
      state.forbiddenHits.some((h) => h.includes("/api/unknown-p11c-probe")),
    ).toBe(true);
    expect(
      state.forbiddenHits.some((h) =>
        h.includes("/api/projects/unknown-p11c-probe"),
      ),
    ).toBe(true);
    expect(
      state.forbiddenHits.some((h) => h.includes("/editor-state/unknown")),
    ).toBe(true);
    expect(
      state.forbiddenHits.some((h) => h.includes("/api/workspaces/extra-suffix")),
    ).toBe(true);
    expect(
      state.forbiddenHits.some((h) => h.includes("/api/settings/secret-extra")),
    ).toBe(true);
    expect(state.externalHits.length).toBeGreaterThanOrEqual(1);

    const after = await readStorageSnapshot(page);
    expect(after.lsKeys).toEqual(baseline.lsKeys);
    expect(after.ls).toEqual(baseline.ls);
    expect(after.ssKeys).toEqual(baseline.ssKeys);
    expect(after.ss).toEqual(baseline.ss);
    expect(after.cookies).toBe(baseline.cookies);
    expect(await readIdbNames(page)).toEqual(idbBefore);
    const clipAfter = await readClipboardProbe(page);
    expect(clipAfter.installed).toBe(true);
    expect(clipAfter.read).toBe(clipBefore.read);
    expect(clipAfter.write).toBe(clipBefore.write);
    assertEditorsKeyFamilyExact(after, { [seeded.key]: seeded.value });
    assertCleanConsole(consoleLines);
  });

  // ---------------------------------------------------------------------------
  // P12B：全状态 CAS / 串行队列 / guidance 收口 / 冲突与代次
  // ---------------------------------------------------------------------------

  test("P12B GET 缺失 stateVersion 固定加载失败且零 PUT", async ({ page }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B缺版本" });
    const state = createProbeState([project]);
    const editor = realTechnicalEditor(REAL_TECH_A, REAL_OVERVIEW);
    // @ts-expect-error 故意缺失版本以测加载失败
    delete editor.stateVersion;
    state.editorById[REAL_TECH_A] = editor;
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expect(page.getByTestId("technical-editor-load-error")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(LOAD_ERROR)).toBeVisible();
    expect(state.putLog.length).toBe(0);
  });

  test("P12B GET 非法 stateVersion 固定加载失败且零 PUT", async ({ page }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B非法版本" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = {
      ...realTechnicalEditor(REAL_TECH_A, REAL_OVERVIEW),
      stateVersion: "bad_version",
    };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expect(page.getByTestId("technical-editor-load-error")).toBeVisible({
      timeout: 10_000,
    });
    expect(state.putLog.length).toBe(0);
  });

  test("P12B 技术连续编辑真挂起串行：第二 PUT 在第一响应前为 0，expected 串链", async ({
    page,
  }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B串行" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    const gate = createHoldGate();
    state.putMode[REAL_TECH_A] = { kind: "gate", gate, then: "ok" };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B串行");

    const v0 = state.editorById[REAL_TECH_A].stateVersion;
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n第一波编辑`);
    await expect.poll(() => state.putLog.length, { timeout: 5_000 }).toBe(1);
    expect(state.putLog[0].body.expectedStateVersion).toBe(v0);

    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n第二波最新正文`);
    // 防抖 800ms 后仍不得发出第二 PUT（第一仍挂起）
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 50));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(1);

    gate.release();
    await expect.poll(() => state.putLog.length, { timeout: 8_000 }).toBe(2);
    const first = state.putLog[0];
    const second = state.putLog[1];
    // 反假绿：第二 expected 必须精确等于第一 PUT 响应的服务端 stateVersion
    expect(first.responseVersion).toBeTruthy();
    expect(isValidStateVersion(first.responseVersion)).toBe(true);
    expect(first.responseVersion).not.toBe(v0);
    expect(state.successVersionLog[0]).toBe(first.responseVersion);
    expect(second.body.expectedStateVersion).toBe(first.responseVersion);
    expect(second.body.expectedStateVersion).toBe(state.successVersionLog[0]);
    expect(
      (second.body.analysis as { overview?: string }).overview,
    ).toContain("第二波最新正文");
  });

  test("P12B guidance 编辑只走技术主队列；含 guidance+expected；无独立 guidance GET/PUT；旧 feedback guidance 不水合", async ({
    page,
  }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12Bguidance" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    await page.addInitScript(() => {
      localStorage.setItem(
        "biaoshu.projectFeedback.proj_e2e_p11c_tech_a",
        JSON.stringify({
          projectId: "proj_e2e_p11c_tech_a",
          guidance: {
            chapterFocus: "LOCAL_GUIDANCE_SHOULD_NOT_HYDRATE",
            extraRequirements: "local-extra",
          },
          history: [],
          keepMe: "preserve-unrelated",
        }),
      );
    });
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12Bguidance");
    await expect(page.locator("body")).not.toContainText(
      "LOCAL_GUIDANCE_SHOULD_NOT_HYDRATE",
    );

    const getsBefore = state.getLog.filter((id) => id === REAL_TECH_A).length;
    const putsBefore = state.putLog.length;

    // ProjectGuidanceCard：#gw-focus 章节侧重点
    await page.locator("#gw-focus").fill("P12B_GUIDANCE_EDIT_FOCUS");

    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBefore + 1);
    const put = state.putLog[state.putLog.length - 1];
    expect(put.body).toHaveProperty("guidance");
    expect(put.body).toHaveProperty("expectedStateVersion");
    expect(
      (put.body.guidance as { chapterFocus?: string }).chapterFocus,
    ).toContain("P12B_GUIDANCE_EDIT_FOCUS");
    const getsAfter = state.getLog.filter((id) => id === REAL_TECH_A).length;
    expect(getsAfter).toBe(getsBefore);

    const snap = await readStorageSnapshot(page);
    const fbKey = Object.keys(snap.ls).find((k) =>
      k.startsWith("biaoshu.projectFeedback."),
    );
    if (fbKey) {
      expect(snap.ls[fbKey]).toContain("preserve-unrelated");
      expect(snap.ls[fbKey]).not.toMatch(/esv_[0-9a-f]{32}/);
    }
  });

  test("P12B 技术全状态 409：本地保留、全量 PUT 阻断、固定 UI、无矩阵伪冲突；显式 reload 恢复", async ({
    page,
  }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B全状态冲突" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = { kind: "full_conflict" };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B全状态冲突");

    const localText = `${REAL_OVERVIEW}\n本地未保存冲突正文`;
    await page.getByTestId("technical-analysis-overview").fill(localText);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(FULL_STATE_CONFLICT_MSG)).toBeVisible();
    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toHaveCount(0);
    await expect(
      page.getByTestId("response-matrix-merge-conflicts"),
    ).toHaveCount(0);
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      localText,
    );

    const putsAtConflict = state.putLog.length;
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${localText}\n继续编辑不应自动重试`);
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 100));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(putsAtConflict);

    state.editorById[REAL_TECH_A] = {
      ...realTechnicalEditor(REAL_TECH_A, "远端重载后的概述"),
      stateVersion: seedStateVersion(999),
    };
    state.putMode[REAL_TECH_A] = { kind: "ok" };
    const getsBefore = state.getLog.filter((id) => id === REAL_TECH_A).length;
    await page.getByTestId("technical-editor-state-reload").click();
    await expect
      .poll(
        () => state.getLog.filter((id) => id === REAL_TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsBefore + 1);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toHaveCount(0);
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      "远端重载后的概述",
    );

    const putsBefore = state.putLog.length;
    await page
      .getByTestId("technical-analysis-overview")
      .fill("远端重载后的概述\n恢复后编辑");
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBefore + 1);
    expect(
      state.putLog[state.putLog.length - 1].body.expectedStateVersion,
    ).toBe(seedStateVersion(999));
  });

  test("P12B 技术 200 缺失 stateVersion 阻断；零后续 PUT", async ({ page }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B缺失200" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = { kind: "ok", stripStateVersion: true };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B缺失200");
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n触发缺失200`);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(FULL_STATE_CONFLICT_MSG)).toBeVisible();
    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toHaveCount(0);
    const putsBlocked = state.putLog.length;
    expect(state.putLog[putsBlocked - 1].responseVersion).toBeNull();
    expect(state.successVersionLog.length).toBe(0);
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n缺失后仍阻断`);
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 100));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(putsBlocked);
  });

  test("P12B 技术 200 非法新版本阻断；reload 失败继续阻断", async ({ page }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B非法200" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = { kind: "ok", invalidStateVersion: true };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B非法200");
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n触发非法200`);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(FULL_STATE_CONFLICT_MSG)).toBeVisible();
    const putsBlocked = state.putLog.length;
    expect(state.putLog[putsBlocked - 1].responseVersion).toBeNull();
    expect(state.successVersionLog.length).toBe(0);
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n阻断后再编辑`);
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 100));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(putsBlocked);

    state.getMode[REAL_TECH_A] = { kind: "fail", status: 500 };
    await page.getByTestId("technical-editor-state-reload").click();
    await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 5_000 });
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toBeVisible();
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n失败后仍阻断`);
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 100));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(putsBlocked);
  });

  test("P12B 普通 409 无矩阵明细：固定保存失败，不伪造空矩阵冲突/mergePreview", async ({
    page,
  }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B普通409" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.putMode[REAL_TECH_A] = { kind: "plain_409" };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B普通409");

    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n普通409无明细`);
    await expect(page.getByText(SAVE_ERROR)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toHaveCount(0);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("response-matrix-merge-preview"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("response-matrix-merge-conflicts"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("response-matrix-apply-merge"),
    ).toHaveCount(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    await expect(page.getByText("generic_conflict")).toHaveCount(0);

    // 全状态阻断期间：矩阵相关动作不得产生 PUT
    state.putMode[REAL_TECH_A] = { kind: "full_conflict" };
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n进入全状态阻断`);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toBeVisible({ timeout: 5_000 });
    const putsBlocked = state.putLog.length;
    await matrixNotesLocator(page).fill("阻断期矩阵备注不应发PUT");
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n阻断期继续编辑`);
    await expect
      .poll(async () => {
        await new Promise((r) => setTimeout(r, 100));
        return state.putLog.length;
      }, { timeout: 2_000 })
      .toBe(putsBlocked);
  });

  test("P12B 矩阵既有 409 与合并三键串行；全状态 code 不进矩阵 UX", async ({
    page,
  }) => {
    const project = makeProject({ id: REAL_TECH_A, name: "P12B矩阵兼容" });
    const state = createProbeState([project]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B矩阵兼容");
    const notesBox = matrixNotesLocator(page);
    await notesBox.fill("基线");
    await expect.poll(() => state.putLog.length, { timeout: 5_000 }).toBe(1);

    state.putMode[REAL_TECH_A] = {
      kind: "conflict",
      remoteNotes: "远端矩阵备注",
      remoteVersion: "ver_remote_p12b",
      times: 1,
    };
    await notesBox.fill("本地矩阵备注");
    await expect.poll(() => state.putLog.length, { timeout: 5_000 }).toBe(2);
    await expect(page.getByText(MATRIX_CONFLICT_MSG)).toBeVisible();
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("response-matrix-merge-conflicts"),
    ).toBeVisible();
    await page.getByTestId("merge-choose-local-notes").click();
    state.putMode[REAL_TECH_A] = { kind: "ok" };
    const before = state.putLog.length;
    await page.getByTestId("response-matrix-apply-merge").click();
    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(before + 1);
    const mergePut = state.putLog[state.putLog.length - 1];
    expect(Object.keys(mergePut.body).slice().sort()).toEqual(
      ["expectedStateVersion", "responseMatrix", "responseMatrixVersion"]
        .slice()
        .sort(),
    );
    expect(mergePut.body).not.toHaveProperty("analysis");
    expect(mergePut.body).not.toHaveProperty("outline");
    expect(mergePut.body).not.toHaveProperty("guidance");
  });

  test("P12B A→B 挂起/迟到不污染 B；版本不落存储", async ({ page }) => {
    const projectA = makeProject({ id: REAL_TECH_A, name: "P12B甲" });
    const projectB = makeProject({ id: REAL_TECH_B, name: "P12B乙" });
    const state = createProbeState([projectA, projectB]);
    state.editorById[REAL_TECH_A] = realTechnicalEditor(
      REAL_TECH_A,
      REAL_OVERVIEW,
    );
    state.editorById[REAL_TECH_B] = realTechnicalEditor(
      REAL_TECH_B,
      REAL_OVERVIEW_B,
    );
    const gate = createHoldGate();
    state.putMode[REAL_TECH_A] = { kind: "gate", gate, then: "ok" };
    await installP11cRoutes(page, state);
    await openTechWorkspace(page, REAL_TECH_A, "analysis");
    await expectWorkspaceReady(page, "P12B甲");
    await page
      .getByTestId("technical-analysis-overview")
      .fill(`${REAL_OVERVIEW}\n甲挂起编辑`);
    await expect.poll(() => state.putLog.length, { timeout: 5_000 }).toBe(1);

    await page.goto(`/technical-plan/${REAL_TECH_B}/analysis`);
    await expectWorkspaceReady(page, "P12B乙");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      REAL_OVERVIEW_B,
    );
    gate.release();
    await expect
      .poll(async () => {
        return page.getByTestId("technical-analysis-overview").inputValue();
      }, { timeout: 5_000 })
      .toBe(REAL_OVERVIEW_B);
    await expect(
      page.getByTestId("technical-editor-state-conflict"),
    ).toHaveCount(0);

    const snap = await readStorageSnapshot(page);
    for (const v of Object.values(snap.ls)) {
      expect(v).not.toMatch(/esv_[0-9a-f]{32}/);
    }
    for (const v of Object.values(snap.ss)) {
      expect(v).not.toMatch(/esv_[0-9a-f]{32}/);
    }
  });

});
