/**
 * 模块：P13-G2 技术标章节编辑意图前端专项 E2E
 * 用途：required 已认证 strict bid_writer 在技术标 content 步验证
 *       章节租约 heartbeat/leave 精确 body 与 CSRF、与 presence 共享 clientId、
 *       200 自身 / 409 安全用户名且不阻断 editor-state PUT、章节切换、
 *       hidden/visible/pagehide、资格零请求、严格 parser 与隐私门。
 * 对接：Playwright chromium 单 worker；同源路由桩记录 chapter-edit-lease 与 presence；
 *       固定 testid technical-chapter-edit-intent。
 * 二次开发：禁止源码字符串、恒真集合、未触发事件、初值已满足的伪稳定；
 *       请求计数只认真实 route 命中；不得 waitForTimeout 作完成证据；
 *       UI 不得出现在线/实时/正在编辑/锁定/不可编辑/lease ID 等承诺。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p13g2_tech_a";
const TECH_B = "proj_e2e_p13g2_tech_b";
const CH_A1 = "ch_e2e_p13g2_a1";
const CH_A2 = "ch_e2e_p13g2_a2";
const CH_B1 = "ch_e2e_p13g2_b1";
const INTENT_TESTID = "technical-chapter-edit-intent";
const TITLE_TEXT = "本章处理意图";
const SELF_TEXT = "已记录你的近期处理意图";
const CONFLICT_PREFIX = "近期由 ";
const CONFLICT_SUFFIX = " 处理";
const UNAVAILABLE_TEXT = "章节处理意图暂不可用";
const CSRF_TOKEN = "e2e-p13g2-csrf-token-memory";
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p13g2_sess_opaque";
const E2E_LOGIN_USER = "e2e_p13g2_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const SELF_USERNAME = "e2e_p13g2_user";
const HOLDER_USERNAME = "同事乙";
/** 精确 100 个 Unicode 码点的安全用户名边界 */
const HOLDER_BOUNDARY_100 = `${"边".repeat(99)}界`;
const SECRET_MARKER = "SECRET_P13G2_LEAK_MARKER_xyz";
const CLIENT_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const CLIENT_ID_BACKEND_RE = /^[A-Za-z0-9_-]{22,64}$/;
const FORBIDDEN_UI_PHRASES = [
  "在线",
  "实时",
  "正在编辑",
  "正在输入",
  "独占",
  "锁定",
  "不可编辑",
  "最后活跃",
  "倒计时",
] as const;
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const STRICT_MODE_STABLE_MS = 250;
const REFRESH_MS = 15_000;
const ZERO_REQUEST_STABLE_MS = 400;
const AUTOSAVE_DEBOUNCE_MS = 800;
const CONFLICT_CODE = "chapter_edit_lease_conflict";
const CONFLICT_MESSAGE = "此章节近期已有处理意图";

type AuthRole = "bid_writer" | "finance" | "hr" | "bidder";

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

type ChapterStub = {
  id: string;
  title: string;
  body: string;
  preview: string;
  wordCount: number;
  status: "done" | "empty" | "generating" | "needs_review";
};

type EditorState = {
  projectId: string;
  outline: unknown[];
  chapters: ChapterStub[];
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
  businessQualify: unknown[];
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  stateVersion: string;
  updatedAt: string | null;
  currentRevisionSourceKind: string | null;
  currentRevisionActorUsername: string | null;
};

type LeaseHit = {
  seq: number;
  op: "heartbeat" | "leave";
  projectId: string;
  path: string;
  method: string;
  body: Record<string, unknown> | null;
  rawBody: string;
  csrf: string | null;
  headers: Record<string, string>;
  cookie: string;
  startedAt: number;
  finishedAt: number;
  status: number;
  responseBody: string;
};

type PresenceHit = {
  seq: number;
  op: "heartbeat" | "leave";
  projectId: string;
  path: string;
  body: Record<string, unknown> | null;
  rawBody: string;
  csrf: string | null;
};

type EditorPutHit = {
  seq: number;
  projectId: string;
  path: string;
  body: Record<string, unknown> | null;
  rawBody: string;
  csrf: string | null;
  at: number;
};

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  waiterCount: () => number;
  hitCount: () => number;
};

type FetchProbeEntry = {
  url: string;
  method: string;
  body: string;
  keepalive: boolean;
  credentials: string;
  contentType: string | null;
  at: number;
};

type IdbWriteProbeEntry = {
  db: string;
  store: string;
  key: string;
  value: string;
};

type HeartbeatMode =
  | { kind: "ok" }
  | { kind: "conflict"; holderUsername: string }
  | { kind: "fail"; status: number }
  | { kind: "bad"; status?: number; body: unknown }
  | { kind: "abort" }
  | {
      kind: "gate";
      gate: HoldGate;
      then: "ok" | "conflict" | "fail" | "bad" | "abort";
      status?: number;
      holderUsername?: string;
      body?: unknown;
    };

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  authRequired: boolean;
  sessionAuthenticated: boolean;
  role: AuthRole;
  csrfToken: string;
  leaseSeq: number;
  leaseLog: LeaseHit[];
  presenceSeq: number;
  presenceLog: PresenceHit[];
  editorPutSeq: number;
  editorPutLog: EditorPutHit[];
  heartbeatMode: Record<string, HeartbeatMode>;
  defaultHeartbeat: HeartbeatMode;
  leaveStatus: number;
  forbiddenHits: string[];
  externalHits: string[];
  versionSeq: number;
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function createHoldGate(): HoldGate {
  let released = false;
  let hits = 0;
  const waiters: Array<() => void> = [];
  return {
    wait: () => {
      hits += 1;
      if (released) return Promise.resolve();
      return new Promise<void>((resolve) => {
        waiters.push(resolve);
      });
    },
    release: () => {
      released = true;
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    isReleased: () => released,
    waiterCount: () => waiters.length,
    hitCount: () => hits,
  };
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
    { methods: ["GET", "POST"], rest: /^\/(files|tasks|images)\/?$/ },
    {
      methods: ["GET", "POST"],
      rest: /^\/tasks\/[^/]+(\/(events|cancel))?\/?$/,
    },
    { methods: ["POST"], rest: /^\/artifacts\/workspace\/revise\/?$/ },
    {
      methods: ["GET", "POST"],
      rest: /^\/editor-state-checkpoints(\/|$)/,
    },
    {
      methods: ["GET", "POST"],
      rest: /^\/editor-state-revisions(\/|$)/,
    },
    { methods: ["POST"], rest: /^\/presence\/(heartbeat|leave)\/?$/ },
    {
      methods: ["POST"],
      rest: /^\/chapter-edit-lease\/(heartbeat|leave)\/?$/,
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
    updatedAt: partial.updatedAt ?? "2026-07-20T12:00:00",
    technicalPlanStep: partial.technicalPlanStep ?? 5,
    wordCount: partial.wordCount ?? 100,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: "technical",
  };
}

function makeChapter(
  id: string,
  title: string,
  body = "初始章节正文。",
): ChapterStub {
  return {
    id,
    title,
    body,
    preview: body,
    wordCount: [...body].length,
    status: "done",
  };
}

function emptyEditor(
  projectId: string,
  chapters: ChapterStub[] = [makeChapter(CH_A1, "章节甲一")],
): EditorState {
  return {
    projectId,
    outline: chapters.map((c) => ({
      id: c.id,
      title: c.title,
      level: 1,
      targetWords: 800,
      description: "",
      children: [],
    })),
    chapters,
    facts: [],
    mode: "ALIGNED",
    analysisOverview: "P13G2 概述",
    analysis: {
      overview: "P13G2 概述",
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
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion: seedStateVersion(1),
    updatedAt: "2026-07-20T12:34:56",
    currentRevisionSourceKind: "browser_put",
    currentRevisionActorUsername: SELF_USERNAME,
  };
}

function createProbeState(
  projects: ProjectStub[],
  chaptersByProject?: Record<string, ChapterStub[]>,
): ProbeState {
  const editorById: Record<string, EditorState> = {};
  for (const p of projects) {
    const chapters =
      chaptersByProject?.[p.id] ??
      (p.id === TECH_B
        ? [makeChapter(CH_B1, "章节乙一")]
        : [
            makeChapter(CH_A1, "章节甲一"),
            makeChapter(CH_A2, "章节甲二", "甲二正文。"),
          ]);
    editorById[p.id] = emptyEditor(p.id, chapters);
  }
  return {
    projects,
    editorById,
    authRequired: true,
    sessionAuthenticated: false,
    role: "bid_writer",
    csrfToken: CSRF_TOKEN,
    leaseSeq: 0,
    leaseLog: [],
    presenceSeq: 0,
    presenceLog: [],
    editorPutSeq: 0,
    editorPutLog: [],
    heartbeatMode: {},
    defaultHeartbeat: { kind: "ok" },
    leaveStatus: 204,
    forbiddenHits: [],
    externalHits: [],
    versionSeq: 1,
  };
}

function parseJsonBody(raw: string): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as unknown;
    if (!v || typeof v !== "object" || Array.isArray(v)) return null;
    return v as Record<string, unknown>;
  } catch {
    return null;
  }
}

function workspaceForRole(role: AuthRole) {
  return {
    id: "ws_e2e",
    name: "E2E 工作空间",
    role,
    isOwner: role === "bid_writer",
  };
}

function leaseKey(projectId: string, chapterId: string): string {
  return `${projectId}::${chapterId}`;
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p13g2Clip?: { installed: boolean; read: number; write: number };
      __p13g2FetchLog?: Array<{
        url: string;
        method: string;
        body: string;
        keepalive: boolean;
        credentials: string;
        contentType: string | null;
        at: number;
      }>;
      __p13g2IdbWrites?: Array<{
        db: string;
        store: string;
        key: string;
        value: string;
      }>;
    };
    g.__p13g2Clip = { installed: false, read: 0, write: 0 };
    g.__p13g2FetchLog = [];
    g.__p13g2IdbWrites = [];

    const clip = {
      readText: async () => {
        g.__p13g2Clip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__p13g2Clip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__p13g2Clip.installed = true;
    } catch {
      g.__p13g2Clip.installed = false;
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      let url = "";
      let method = (init?.method ?? "GET").toUpperCase();
      let body = "";
      let keepalive = init?.keepalive === true;
      let credentials = "same-origin";
      let contentType: string | null = null;
      try {
        if (typeof input === "string") {
          url = input;
        } else if (input instanceof URL) {
          url = input.toString();
        } else if (input && typeof input === "object") {
          url = String((input as Request).url ?? "");
          method = String(
            init?.method ?? (input as Request).method ?? "GET",
          ).toUpperCase();
          if (
            init?.keepalive === undefined &&
            "keepalive" in (input as Request)
          ) {
            keepalive = Boolean((input as Request).keepalive);
          }
          if (init?.credentials === undefined && "credentials" in (input as Request)) {
            credentials = String((input as Request).credentials ?? "same-origin");
          }
        }
        if (init?.keepalive === true) {
          keepalive = true;
        } else if (init?.keepalive === false) {
          keepalive = false;
        }
        if (init?.credentials !== undefined) {
          credentials = String(init.credentials);
        }
        if (typeof init?.body === "string") {
          body = init.body;
        } else if (init?.body != null) {
          body = String(init.body);
        }
        const hdrs = init?.headers;
        if (hdrs && typeof hdrs === "object") {
          if (hdrs instanceof Headers) {
            contentType = hdrs.get("Content-Type") ?? hdrs.get("content-type");
          } else if (Array.isArray(hdrs)) {
            for (const [k, v] of hdrs) {
              if (String(k).toLowerCase() === "content-type") {
                contentType = String(v);
              }
            }
          } else {
            const rec = hdrs as Record<string, string>;
            for (const k of Object.keys(rec)) {
              if (k.toLowerCase() === "content-type") {
                contentType = String(rec[k]);
              }
            }
          }
        }
      } catch {
        url = String(input);
      }
      g.__p13g2FetchLog!.push({
        url,
        method,
        body,
        keepalive,
        credentials,
        contentType,
        at: Date.now(),
      });
      return originalFetch(input as RequestInfo, init);
    };

    const patchIdbStore = (proto: IDBObjectStore) => {
      const originalPut = proto.put;
      const originalAdd = proto.add;
      proto.put = function patchedPut(
        value: unknown,
        key?: IDBValidKey,
      ): IDBRequest {
        try {
          const dbName = this.transaction.db.name;
          g.__p13g2IdbWrites!.push({
            db: String(dbName ?? ""),
            store: String(this.name ?? ""),
            key: key === undefined ? "" : String(key),
            value: (() => {
              try {
                return JSON.stringify(value);
              } catch {
                return String(value);
              }
            })(),
          });
        } catch {
          /* 探针失败不阻断 */
        }
        return originalPut.call(this, value, key as IDBValidKey);
      };
      proto.add = function patchedAdd(
        value: unknown,
        key?: IDBValidKey,
      ): IDBRequest {
        try {
          const dbName = this.transaction.db.name;
          g.__p13g2IdbWrites!.push({
            db: String(dbName ?? ""),
            store: String(this.name ?? ""),
            key: key === undefined ? "" : String(key),
            value: (() => {
              try {
                return JSON.stringify(value);
              } catch {
                return String(value);
              }
            })(),
          });
        } catch {
          /* 探针失败不阻断 */
        }
        return originalAdd.call(this, value, key as IDBValidKey);
      };
    };
    try {
      patchIdbStore(IDBObjectStore.prototype);
    } catch {
      /* 环境无 IDB 时跳过 */
    }
  });

  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(rawUrl)) {
      const post = req.postData() || "";
      state.externalHits.push(
        `${method} ${rawUrl}${post ? ` body=${post}` : ""}`,
      );
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
    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p13g2_forbidden", message: SECRET_MARKER } },
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
        workspaces: [workspaceForRole(state.role)],
        activeWorkspaceId: "ws_e2e",
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
      state.sessionAuthenticated = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: {
          "Set-Cookie": `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}; Path=/; HttpOnly; SameSite=Lax`,
          "Cache-Control": "no-store",
        },
        body: JSON.stringify({
          user: { id: "user_e2e", username: E2E_LOGIN_USER },
          workspaces: [workspaceForRole(state.role)],
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

    if (
      path.startsWith("/api/settings") &&
      (method === "GET" || method === "PUT")
    ) {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "",
        apiKey: "",
        model: "",
        parseStrategy: "light",
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
      if (method === "POST") {
        await json(route, { detail: { code: "p13g2_no_create" } }, 403);
        return;
      }
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = decodeURIComponent(detailMatch[1]);
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(route, { detail: { code: "project_not_found" } }, 404);
        return;
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
        const body = state.editorById[id] ?? emptyEditor(id);
        await json(route, body);
        return;
      }
      const raw = req.postData() || "{}";
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = {};
      }
      const headers = req.headers();
      state.editorPutSeq += 1;
      state.editorPutLog.push({
        seq: state.editorPutSeq,
        projectId: id,
        path,
        body,
        rawBody: raw,
        csrf: headers["x-csrf-token"] ?? null,
        at: Date.now(),
      });
      const current = state.editorById[id] ?? emptyEditor(id);
      state.versionSeq += 1;
      const nextVersion = seedStateVersion(state.versionSeq);
      const next: EditorState = {
        ...current,
        ...body,
        projectId: id,
        stateVersion: isValidStateVersion(body.stateVersion)
          ? nextVersion
          : nextVersion,
        updatedAt: "2026-07-20T12:40:00",
      } as EditorState;
      state.editorById[id] = next;
      await json(route, next);
      return;
    }

    const presenceMatch = path.match(
      /^\/api\/projects\/([^/]+)\/presence\/(heartbeat|leave)\/?$/,
    );
    if (presenceMatch && method === "POST") {
      const projectId = decodeURIComponent(presenceMatch[1]);
      const op = presenceMatch[2] as "heartbeat" | "leave";
      const rawBody = req.postData() || "";
      const body = parseJsonBody(rawBody);
      const headers = req.headers();
      state.presenceSeq += 1;
      state.presenceLog.push({
        seq: state.presenceSeq,
        op,
        projectId,
        path,
        body,
        rawBody,
        csrf: headers["x-csrf-token"] ?? null,
      });
      if (op === "leave") {
        await route.fulfill({
          status: 204,
          headers: { "Cache-Control": "no-store" },
          body: "",
        });
        return;
      }
      await json(route, {
        leaseExpiresAt: "2026-07-20T12:35:41",
        refreshAfterSeconds: 15,
        members: [{ username: SELF_USERNAME, isSelf: true }],
        truncated: false,
      });
      return;
    }

    const leaseMatch = path.match(
      /^\/api\/projects\/([^/]+)\/chapter-edit-lease\/(heartbeat|leave)\/?$/,
    );
    if (leaseMatch && method === "POST") {
      const projectId = decodeURIComponent(leaseMatch[1]);
      const op = leaseMatch[2] as "heartbeat" | "leave";
      const rawBody = req.postData() || "";
      const body = parseJsonBody(rawBody);
      const headers = req.headers();
      const startedAt = Date.now();
      state.leaseSeq += 1;
      const seq = state.leaseSeq;
      const hit: LeaseHit = {
        seq,
        op,
        projectId,
        path,
        method,
        body,
        rawBody,
        csrf: headers["x-csrf-token"] ?? null,
        headers: { ...headers },
        cookie: headers["cookie"] || "",
        startedAt,
        finishedAt: 0,
        status: 0,
        responseBody: "",
      };
      state.leaseLog.push(hit);

      if (op === "leave") {
        hit.finishedAt = Date.now();
        hit.status = state.leaveStatus;
        await route.fulfill({
          status: state.leaveStatus,
          headers: { "Cache-Control": "no-store" },
          body: "",
        });
        return;
      }

      const chapterId =
        body && typeof body.chapterId === "string" ? body.chapterId : "";
      const modeKey = leaseKey(projectId, chapterId);
      const mode =
        state.heartbeatMode[modeKey] ??
        state.heartbeatMode[projectId] ??
        state.defaultHeartbeat;

      let fulfillBody = "";
      let fulfillStatus = 200;

      const buildOk = () =>
        JSON.stringify({
          leaseExpiresAt: "2026-07-20T12:35:41.000Z",
          refreshAfterSeconds: 15,
        });
      const buildConflict = (holder: string) =>
        JSON.stringify({
          detail: {
            code: CONFLICT_CODE,
            message: CONFLICT_MESSAGE,
            holderUsername: holder,
          },
        });

      if (mode.kind === "gate") {
        await mode.gate.wait();
        if (mode.then === "abort") {
          hit.finishedAt = Date.now();
          hit.status = 0;
          hit.responseBody = "";
          await route.abort("failed");
          return;
        }
        if (mode.then === "conflict") {
          fulfillStatus = 409;
          fulfillBody = buildConflict(mode.holderUsername ?? HOLDER_USERNAME);
        } else if (mode.then === "fail") {
          fulfillStatus = mode.status ?? 500;
          fulfillBody = JSON.stringify({
            detail: { code: "lease_failed", message: SECRET_MARKER },
          });
        } else if (mode.then === "bad") {
          fulfillStatus = mode.status ?? 200;
          fulfillBody = JSON.stringify(
            mode.body ?? { bad: true, secret: SECRET_MARKER },
          );
        } else {
          fulfillStatus = 200;
          fulfillBody = buildOk();
        }
      } else if (mode.kind === "abort") {
        hit.finishedAt = Date.now();
        hit.status = 0;
        hit.responseBody = "";
        await route.abort("failed");
        return;
      } else if (mode.kind === "conflict") {
        fulfillStatus = 409;
        fulfillBody = buildConflict(mode.holderUsername);
      } else if (mode.kind === "fail") {
        fulfillStatus = mode.status;
        fulfillBody = JSON.stringify({
          detail: { code: "lease_failed", message: SECRET_MARKER },
        });
      } else if (mode.kind === "bad") {
        fulfillStatus = mode.status ?? 200;
        // body 可为 array/scalar/null，JSON.stringify 精确复现
        fulfillBody = JSON.stringify(mode.body);
      } else {
        fulfillStatus = 200;
        fulfillBody = buildOk();
      }

      hit.finishedAt = Date.now();
      hit.status = fulfillStatus;
      hit.responseBody = fulfillBody;
      await route.fulfill({
        status: fulfillStatus,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: fulfillBody,
      });
      return;
    }

    if (method === "GET") {
      await json(route, []);
      return;
    }
    if (method === "POST") {
      await json(route, { id: "task_e2e", status: "queued", type: "noop" });
      return;
    }
    await json(route, { detail: { code: "p13g2_unhandled" } }, 404);
  });
}

function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
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

function appConsoleLines(lines: string[], clientIds: string[] = []): string[] {
  return lines.filter((line) => {
    if (line.includes(SECRET_MARKER)) return true;
    for (const id of clientIds) {
      if (id && line.includes(id)) return true;
    }
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

function assertConsoleNoSensitiveLeak(
  consoleLines: string[],
  clientIds: string[],
  extras: string[] = [],
) {
  for (const line of consoleLines) {
    expect(line, "secret marker 经 console 泄漏").not.toContain(SECRET_MARKER);
    for (const id of clientIds) {
      if (!id) continue;
      expect(line, `clientId 经 console 泄漏: ${id}`).not.toContain(id);
    }
    for (const extra of extras) {
      if (!extra) continue;
      expect(line, `敏感值经 console 泄漏: ${extra}`).not.toContain(extra);
    }
  }
}

async function waitStableExactCount(
  getCount: () => number,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  const deadline = Date.now() + timeoutMs;
  await expect.poll(() => getCount(), { timeout: timeoutMs }).toBe(expected);
  const stableStart = Date.now();
  await expect
    .poll(
      () => {
        const n = getCount();
        if (n !== expected) return -1;
        return Date.now() - stableStart >= windowMs ? 1 : 0;
      },
      { timeout: Math.max(timeoutMs, windowMs + 5_000) },
    )
    .toBe(1);
  expect(getCount()).toBe(expected);
  expect(Date.now()).toBeGreaterThanOrEqual(stableStart + windowMs);
  expect(Date.now()).toBeLessThanOrEqual(deadline + windowMs + 5_000);
}

async function waitStableLeaseCount(
  state: ProbeState,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  await waitStableExactCount(
    () => state.leaseLog.length,
    expected,
    windowMs,
    timeoutMs,
  );
}

async function waitStableHeartbeatCount(
  state: ProbeState,
  projectId: string,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  await waitStableExactCount(
    () => heartbeats(state, projectId).length,
    expected,
    windowMs,
    timeoutMs,
  );
}

async function readClipboardProbe(page: Page) {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13g2Clip?: { installed: boolean; read: number; write: number };
    };
    return g.__p13g2Clip ?? { installed: false, read: -1, write: -1 };
  });
}

async function readFetchProbe(page: Page): Promise<FetchProbeEntry[]> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13g2FetchLog?: FetchProbeEntry[];
    };
    return g.__p13g2FetchLog ?? [];
  });
}

async function readIdbWriteProbe(page: Page): Promise<IdbWriteProbeEntry[]> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13g2IdbWrites?: IdbWriteProbeEntry[];
    };
    return g.__p13g2IdbWrites ?? [];
  });
}

function leaseFetchEntries(
  log: FetchProbeEntry[],
  op: "heartbeat" | "leave",
  projectId?: string,
): FetchProbeEntry[] {
  const needle =
    projectId !== undefined
      ? `/api/projects/${projectId}/chapter-edit-lease/${op}`
      : `/chapter-edit-lease/${op}`;
  return log.filter(
    (e) =>
      e.method === "POST" &&
      e.url.includes(needle) &&
      !e.url.includes("fonts."),
  );
}

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

async function openTech(page: Page, projectId: string, step = "content") {
  await page.goto(`/technical-plan/${projectId}/${step}`);
}

async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function expectTechReady(page: Page, name: string) {
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
}

function heartbeats(state: ProbeState, projectId?: string): LeaseHit[] {
  return state.leaseLog.filter(
    (h) =>
      h.op === "heartbeat" &&
      (projectId === undefined || h.projectId === projectId),
  );
}

function leaves(state: ProbeState, projectId?: string): LeaseHit[] {
  return state.leaseLog.filter(
    (h) =>
      h.op === "leave" &&
      (projectId === undefined || h.projectId === projectId),
  );
}

function presenceHeartbeats(state: ProbeState, projectId?: string): PresenceHit[] {
  return state.presenceLog.filter(
    (h) =>
      h.op === "heartbeat" &&
      (projectId === undefined || h.projectId === projectId),
  );
}

function assertExactLeaseBody(hit: LeaseHit, chapterId: string) {
  expect(hit.body, "lease body 必须是对象").not.toBeNull();
  const keys = Object.keys(hit.body ?? {}).sort();
  expect(keys).toEqual(["chapterId", "clientId"]);
  const clientId = hit.body!.clientId;
  const ch = hit.body!.chapterId;
  expect(typeof clientId).toBe("string");
  expect(clientId as string).toMatch(CLIENT_ID_RE);
  expect(clientId as string).toMatch(CLIENT_ID_BACKEND_RE);
  expect(ch).toBe(chapterId);
  expect(hit.rawBody).toBe(JSON.stringify({ clientId, chapterId }));
}

function assertRequiredWriteAuth(hit: LeaseHit) {
  expect(hit.csrf).toBe(CSRF_TOKEN);
  expect(hit.cookie, "必须携带登录会话 Cookie").toContain(
    `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}`,
  );
  expect(hit.cookie).not.toContain(CSRF_TOKEN);
  expect(hit.cookie).not.toContain(SECRET_MARKER);
  expect(hit.path).toMatch(
    /\/api\/projects\/[^/]+\/chapter-edit-lease\/(heartbeat|leave)\/?$/,
  );
  expect(hit.method).toBe("POST");
  // Playwright 请求头键名为小写
  expect(hit.headers["content-type"]).toBe("application/json");
  expect(
    Object.prototype.hasOwnProperty.call(hit.headers, "x-workspace-id"),
    "chapter-lease 写不得带 X-Workspace-Id",
  ).toBe(false);
  expect(hit.headers["x-workspace-id"]).toBeUndefined();
}

async function assertLeaseFetchProtocol(
  page: Page,
  op: "heartbeat" | "leave",
  projectId: string,
  rawBody: string,
  expectedKeepalive: boolean,
) {
  const fetchLog = await readFetchProbe(page);
  // 同 body 可能对应多次 leave（hidden false / pagehide true），必须连 keepalive 一起精确过滤
  const matched = leaseFetchEntries(fetchLog, op, projectId).filter(
    (e) => e.body === rawBody && e.keepalive === expectedKeepalive,
  );
  expect(
    matched.length,
    `${op} fetch 探针必须精确命中 body+keepalive=${expectedKeepalive}`,
  ).toBeGreaterThanOrEqual(1);
  for (const e of matched) {
    expect(e.credentials, `${op} credentials 必须 same-origin`).toBe(
      "same-origin",
    );
    expect(e.contentType, `${op} Content-Type 必须 application/json`).toBe(
      "application/json",
    );
    expect(e.keepalive, `${op} keepalive 必须精确 ${expectedKeepalive}`).toBe(
      expectedKeepalive,
    );
  }
}

async function setVisibility(page: Page, state: "visible" | "hidden") {
  await page.evaluate((next) => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => next,
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => next === "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
}

async function dispatchPageHide(page: Page) {
  await page.evaluate(() => {
    window.dispatchEvent(new Event("pagehide"));
  });
}

async function readLeakSurfaces(page: Page) {
  return page.evaluate(async () => {
    const html = document.documentElement.outerHTML;
    const text = document.body?.innerText ?? "";
    const href = location.href;
    const local: string[] = [];
    const session: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i) ?? "";
      local.push(`${k}=${localStorage.getItem(k) ?? ""}`);
    }
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i) ?? "";
      session.push(`${k}=${sessionStorage.getItem(k) ?? ""}`);
    }
    let idbKeys: string[] = [];
    try {
      if (typeof indexedDB?.databases === "function") {
        const dbs = await indexedDB.databases();
        idbKeys = dbs.map((d) => `${d.name ?? ""}:${d.version ?? ""}`);
      }
    } catch {
      idbKeys = ["idb_probe_failed"];
    }
    const cookie = document.cookie;
    const g = globalThis as unknown as {
      __p13g2IdbWrites?: Array<{
        db: string;
        store: string;
        key: string;
        value: string;
      }>;
      __p13g2FetchLog?: Array<{
        url: string;
        method: string;
        body: string;
        keepalive: boolean;
        credentials: string;
        contentType: string | null;
        at: number;
      }>;
    };
    const idbWrites = (g.__p13g2IdbWrites ?? []).map(
      (w) => `${w.db}|${w.store}|${w.key}|${w.value}`,
    );
    const fetchBodies = (g.__p13g2FetchLog ?? []).map(
      (f) =>
        `${f.method} ${f.url} keepalive=${f.keepalive} credentials=${f.credentials} contentType=${f.contentType ?? ""} body=${f.body}`,
    );
    const attrBlob = Array.from(document.querySelectorAll("*"))
      .flatMap((el) =>
        Array.from(el.attributes).map((a) => `${a.name}=${a.value}`),
      )
      .join("\n");
    return {
      html,
      text,
      href,
      local,
      session,
      idbKeys,
      idbWrites,
      fetchBodies,
      cookie,
      attrBlob,
    };
  });
}

function assertNoSensitiveLeak(
  surfaces: Awaited<ReturnType<typeof readLeakSurfaces>>,
  clientIds: string[],
  consoleLines: string[],
  externalHits: string[],
  panelText: string,
  options?: {
    allowClientIdsInLeaseAndPresenceBody?: boolean;
    chapterIds?: string[];
    holderUsernames?: string[];
  },
) {
  const chapterIds = options?.chapterIds ?? [];
  const holders = options?.holderUsernames ?? [];
  assertConsoleNoSensitiveLeak(consoleLines, clientIds, [
    ...chapterIds,
    SECRET_MARKER,
  ]);

  const residualConsole = appConsoleLines(consoleLines, clientIds).join("\n");
  const blobs = [
    surfaces.html,
    surfaces.text,
    surfaces.href,
    surfaces.local.join("\n"),
    surfaces.session.join("\n"),
    surfaces.idbKeys.join("\n"),
    surfaces.idbWrites.join("\n"),
    surfaces.cookie,
    residualConsole,
    externalHits.join("\n"),
    surfaces.attrBlob,
  ];

  for (const row of surfaces.fetchBodies) {
    expect(row, "secret marker 经 fetch 泄漏").not.toContain(SECRET_MARKER);
    const isLeaseWrite =
      row.includes("/chapter-edit-lease/heartbeat") ||
      row.includes("/chapter-edit-lease/leave");
    const isPresenceWrite =
      row.includes("/presence/heartbeat") || row.includes("/presence/leave");
    for (const id of clientIds) {
      if (!id) continue;
      if (
        (isLeaseWrite || isPresenceWrite) &&
        options?.allowClientIdsInLeaseAndPresenceBody
      ) {
        const urlPart = row.split(" body=")[0] ?? row;
        expect(urlPart, `clientId 泄漏到 fetch URL: ${id}`).not.toContain(id);
        continue;
      }
      expect(row, `clientId 经 fetch 泄漏: ${id}`).not.toContain(id);
    }
  }

  for (const blob of blobs) {
    expect(blob, "secret marker 泄漏").not.toContain(SECRET_MARKER);
    for (const id of clientIds) {
      if (!id) continue;
      expect(blob, `clientId 泄漏: ${id}`).not.toContain(id);
    }
  }

  // G2 不得把 chapterId 新增到 DOM/属性/URL/存储/console/外网
  // （既有 editor-state JSON body 通道由业务 PUT 负责，不在本门扫描）
  for (const ch of chapterIds) {
    if (!ch) continue;
    expect(surfaces.html, `chapterId DOM 泄漏: ${ch}`).not.toContain(ch);
    expect(surfaces.href, `chapterId URL 泄漏: ${ch}`).not.toContain(ch);
    expect(surfaces.attrBlob, `chapterId 属性泄漏: ${ch}`).not.toContain(ch);
    expect(surfaces.local.join("\n"), `chapterId localStorage: ${ch}`).not.toContain(
      ch,
    );
    expect(
      surfaces.session.join("\n"),
      `chapterId sessionStorage: ${ch}`,
    ).not.toContain(ch);
    expect(surfaces.cookie, `chapterId cookie: ${ch}`).not.toContain(ch);
    for (const line of consoleLines) {
      expect(line, `chapterId console: ${ch}`).not.toContain(ch);
    }
    for (const ext of externalHits) {
      expect(ext, `chapterId 外网: ${ch}`).not.toContain(ch);
    }
  }

  // holder 只允许 React 文本，不得进属性/title/URL/存储/日志
  for (const holder of holders) {
    if (!holder) continue;
    expect(surfaces.attrBlob, `holder 属性泄漏: ${holder}`).not.toContain(
      holder,
    );
    expect(surfaces.href, `holder URL 泄漏: ${holder}`).not.toContain(holder);
    expect(surfaces.local.join("\n")).not.toContain(holder);
    expect(surfaces.session.join("\n")).not.toContain(holder);
    expect(surfaces.cookie).not.toContain(holder);
    for (const line of consoleLines) {
      expect(line).not.toContain(holder);
    }
  }

  for (const phrase of FORBIDDEN_UI_PHRASES) {
    expect(panelText, `意图面板禁用文案: ${phrase}`).not.toContain(phrase);
  }
}

async function assertPrivacyClosed(
  page: Page,
  clientIds: string[],
  consoleLines: string[],
  externalHits: string[],
  panelText: string,
  options?: {
    chapterIds?: string[];
    holderUsernames?: string[];
  },
) {
  const clip = await readClipboardProbe(page);
  expect(clip.installed, "clipboard override 必须安装成功").toBe(true);
  expect(clip.read, "clipboard.read 必须为 0").toBe(0);
  expect(clip.write, "clipboard.write 必须为 0").toBe(0);

  const surfaces = await readLeakSurfaces(page);
  assertNoSensitiveLeak(
    surfaces,
    clientIds,
    consoleLines,
    externalHits,
    panelText,
    {
      allowClientIdsInLeaseAndPresenceBody: true,
      chapterIds: options?.chapterIds,
      holderUsernames: options?.holderUsernames,
    },
  );

  const idbWrites = await readIdbWriteProbe(page);
  for (const w of idbWrites) {
    const blob = `${w.db}|${w.store}|${w.key}|${w.value}`;
    expect(blob).not.toContain(SECRET_MARKER);
    for (const id of clientIds) {
      if (!id) continue;
      expect(blob, `clientId 写入 IDB: ${id}`).not.toContain(id);
    }
  }
}

async function installUuidCallProbe(page: Page) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p13g2UuidProbe?: { calls: number; ids: string[] };
    };
    g.__p13g2UuidProbe = { calls: 0, ids: [] };
    try {
      if (
        typeof crypto === "undefined" ||
        typeof crypto.randomUUID !== "function"
      ) {
        return;
      }
      const original = crypto.randomUUID;
      Object.defineProperty(crypto, "randomUUID", {
        configurable: true,
        value: function randomUUID(this: Crypto) {
          const id = original.call(this);
          g.__p13g2UuidProbe!.calls += 1;
          g.__p13g2UuidProbe!.ids.push(id);
          return id;
        },
      });
    } catch {
      /* 探针安装失败由测试断言暴露 */
    }
  });
}

async function readUuidProbe(page: Page): Promise<{
  calls: number;
  ids: string[];
}> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13g2UuidProbe?: { calls: number; ids: string[] };
    };
    return g.__p13g2UuidProbe ?? { calls: -1, ids: [] as string[] };
  });
}

async function openAuthedContent(
  page: Page,
  state: ProbeState,
  projectId: string,
  projectName: string,
) {
  await installRoutes(page, state);
  await openTech(page, projectId, "content");
  await loginViaUi(page);
  await expectTechReady(page, projectName);
}

async function selectChapterByTitle(page: Page, title: string) {
  // 章节列表项以标题文本出现；点击切换当前有效章节
  await page.getByText(title, { exact: true }).click();
}

test.describe("P13-G2 技术标章节编辑意图前端", () => {
  test("content 首跳：固定 testid、精确 path/body/CSRF、200 自身、与 presence 同 clientId", async ({
    page,
  }) => {
    const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const state = createProbeState([project]);
    const consoleLines = collectConsole(page);
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");

    const panel = page.getByTestId(INTENT_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();

    await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);

    const hb = heartbeats(state, TECH_A)[0]!;
    expect(hb.path).toBe(
      `/api/projects/${TECH_A}/chapter-edit-lease/heartbeat`,
    );
    assertExactLeaseBody(hb, CH_A1);
    assertRequiredWriteAuth(hb);
    expect(hb.status).toBe(200);
    await assertLeaseFetchProtocol(page, "heartbeat", TECH_A, hb.rawBody, false);

    await expect(panel.getByText(SELF_TEXT, { exact: true })).toBeVisible();
    await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);

    // presence 与 chapter lease 共享同一真实 UUID
    await expect
      .poll(() => presenceHeartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);
    const presenceHb = presenceHeartbeats(state, TECH_A)[0]!;
    expect(presenceHb.body?.clientId).toBe(hb.body?.clientId);
    expect(Object.keys(presenceHb.body ?? {}).sort()).toEqual(["clientId"]);

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [String(hb.body?.clientId ?? "")],
      consoleLines,
      state.externalHits,
      panelText,
      { chapterIds: [CH_A1] },
    );
    expect(state.forbiddenHits).toEqual([]);
  });

  test("409 安全用户名展示；冲突下正文仍可编辑并触发既有 editor-state PUT", async ({
    page,
  }) => {
    const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const state = createProbeState([project]);
    state.defaultHeartbeat = {
      kind: "conflict",
      holderUsername: HOLDER_USERNAME,
    };
    const consoleLines = collectConsole(page);
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");

    const panel = page.getByTestId(INTENT_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);
    const hb = heartbeats(state, TECH_A)[0]!;
    assertExactLeaseBody(hb, CH_A1);
    assertRequiredWriteAuth(hb);
    expect(hb.status).toBe(409);
    await assertLeaseFetchProtocol(page, "heartbeat", TECH_A, hb.rawBody, false);

    await expect(
      panel.getByText(`${CONFLICT_PREFIX}${HOLDER_USERNAME}${CONFLICT_SUFFIX}`, {
        exact: true,
      }),
    ).toBeVisible();
    await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
    await expect(panel.getByText(SECRET_MARKER)).toHaveCount(0);
    await expect(panel.getByText(CONFLICT_CODE)).toHaveCount(0);

    // 编辑器/按钮不得因冲突禁用
    const body = page.locator("textarea.tp-content-body");
    await expect(body).toBeVisible();
    await expect(body).toBeEnabled();
    const putBefore = state.editorPutLog.length;
    const marker = `冲突后仍可保存-${Date.now()}`;
    await body.fill(marker);

    await expect
      .poll(() => state.editorPutLog.length, {
        timeout: AUTOSAVE_DEBOUNCE_MS + 8_000,
      })
      .toBeGreaterThan(putBefore);
    const put = state.editorPutLog[state.editorPutLog.length - 1]!;
    expect(put.path).toBe(`/api/projects/${TECH_A}/editor-state`);
    expect(put.csrf).toBe(CSRF_TOKEN);
    expect(put.rawBody).toContain(marker);

    // AI/工具栏按钮仍可交互（disabled 仅由 pipeline busy 控制，非 G2）
    await expect(
      page.getByRole("button", { name: /AI 生成本章/ }),
    ).toBeEnabled();

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [String(hb.body?.clientId ?? "")],
      consoleLines,
      state.externalHits,
      panelText,
      {
        chapterIds: [CH_A1],
        holderUsernames: [HOLDER_USERNAME],
      },
    );
  });

  test("unavailable advisory 非阻断：500/坏包/网络后仍可编辑并 PUT", async ({
    page,
  }) => {
    const cases: Array<{ name: string; mode: HeartbeatMode }> = [
      { name: "http-500", mode: { kind: "fail", status: 500 } },
      {
        name: "bad-200-extra",
        mode: {
          kind: "bad",
          body: {
            leaseExpiresAt: "2026-07-20T12:35:41.000Z",
            refreshAfterSeconds: 15,
            extra: SECRET_MARKER,
          },
        },
      },
      { name: "network-abort", mode: { kind: "abort" } },
    ];

    for (const c of cases) {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      state.defaultHeartbeat = c.mode;
      await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
      const panel = page.getByTestId(INTENT_TESTID);
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);
      await expect(
        panel.getByText(UNAVAILABLE_TEXT, { exact: true }),
      ).toBeVisible();
      await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
      await expect(panel.getByText(HOLDER_USERNAME)).toHaveCount(0);
      await expect(panel.getByText(SECRET_MARKER)).toHaveCount(0);

      const body = page.locator("textarea.tp-content-body");
      await expect(body).toBeEnabled();
      await expect(
        page.getByRole("button", { name: /AI 生成本章/ }),
      ).toBeEnabled();
      const putBefore = state.editorPutLog.length;
      const marker = `unavailable-nonblock-${c.name}-${Date.now()}`;
      await body.fill(marker);
      await expect
        .poll(() => state.editorPutLog.length, {
          timeout: AUTOSAVE_DEBOUNCE_MS + 8_000,
        })
        .toBeGreaterThan(putBefore);
      const put = state.editorPutLog[state.editorPutLog.length - 1]!;
      expect(put.path, `case=${c.name}`).toBe(
        `/api/projects/${TECH_A}/editor-state`,
      );
      expect(put.csrf, `case=${c.name}`).toBe(CSRF_TOKEN);
      expect(put.rawBody, `case=${c.name}`).toContain(marker);
      await page.goto("about:blank");
    }
  });

  test("章节 A→B 切换 leave/heartbeat；hidden/visible/pagehide keepalive", async ({
    page,
  }) => {
    const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const state = createProbeState([project]);
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
    const panel = page.getByTestId(INTENT_TESTID);
    await expect(panel.getByText(SELF_TEXT)).toBeVisible({ timeout: 10_000 });
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);
    assertExactLeaseBody(heartbeats(state, TECH_A)[0]!, CH_A1);
    assertRequiredWriteAuth(heartbeats(state, TECH_A)[0]!);

    // 切到甲二
    await selectChapterByTitle(page, "章节甲二");
    await expect
      .poll(() => {
        const log = state.leaseLog;
        const leaveA = log.find(
          (h) =>
            h.op === "leave" &&
            h.projectId === TECH_A &&
            h.body?.chapterId === CH_A1,
        );
        const hbB = log.find(
          (h) =>
            h.op === "heartbeat" &&
            h.projectId === TECH_A &&
            h.body?.chapterId === CH_A2,
        );
        if (!leaveA || !hbB) return false;
        return leaveA.seq < hbB.seq;
      }, { timeout: 15_000 })
      .toBe(true);
    await expect(panel.getByText(SELF_TEXT)).toBeVisible();

    // hidden 清空状态并 leave；普通 hidden leave keepalive===false
    const leaveBeforeHidden = leaves(state, TECH_A).filter(
      (h) => h.body?.chapterId === CH_A2,
    ).length;
    await setVisibility(page, "hidden");
    await waitStableExactCount(
      () =>
        leaves(state, TECH_A).filter((h) => h.body?.chapterId === CH_A2).length,
      leaveBeforeHidden + 1,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
    await expect(panel.getByText(TITLE_TEXT)).toBeVisible();
    const hiddenLeave = leaves(state, TECH_A)
      .filter((h) => h.body?.chapterId === CH_A2)
      .at(-1)!;
    assertExactLeaseBody(hiddenLeave, CH_A2);
    assertRequiredWriteAuth(hiddenLeave);
    await assertLeaseFetchProtocol(
      page,
      "leave",
      TECH_A,
      hiddenLeave.rawBody,
      false,
    );

    // visible 立即首跳
    const before = state.leaseLog.length;
    await setVisibility(page, "visible");
    await expect
      .poll(() => state.leaseLog.length, { timeout: 10_000 })
      .toBeGreaterThan(before);
    const last = state.leaseLog[state.leaseLog.length - 1]!;
    expect(last.op).toBe("heartbeat");
    expect(last.body?.chapterId).toBe(CH_A2);
    await expect(panel.getByText(SELF_TEXT)).toBeVisible({ timeout: 10_000 });
    await assertLeaseFetchProtocol(page, "heartbeat", TECH_A, last.rawBody, false);

    // pagehide keepalive leave 精确 true（不得与 hidden leave 混淆）
    const leaveBefore = leaves(state, TECH_A).length;
    await dispatchPageHide(page);
    await expect
      .poll(() => leaves(state, TECH_A).length, { timeout: 10_000 })
      .toBe(leaveBefore + 1);
    const leaveHit = leaves(state, TECH_A).at(-1)!;
    assertExactLeaseBody(leaveHit, CH_A2);
    assertRequiredWriteAuth(leaveHit);
    await assertLeaseFetchProtocol(page, "leave", TECH_A, leaveHit.rawBody, true);
    // 交叉证明：同一 rawBody 的 hidden leave 已是 false，pagehide 为 true
    const fetchLog = await readFetchProbe(page);
    const leaveFetches = leaseFetchEntries(fetchLog, "leave", TECH_A);
    const pagehideLeave = leaveFetches.find(
      (e) => e.body === leaveHit.rawBody && e.keepalive === true,
    );
    const plainHiddenLeave = leaveFetches.find(
      (e) => e.body === hiddenLeave.rawBody && e.keepalive === false,
    );
    expect(pagehideLeave, "pagehide leave keepalive===true").toBeTruthy();
    expect(plainHiddenLeave, "hidden leave keepalive===false").toBeTruthy();
  });

  test("请求完成后 15 秒续租：success/conflict/unavailable 精确边界", async ({
    page,
  }) => {
    const modes: Array<{ name: string; mode: HeartbeatMode }> = [
      { name: "success-ok", mode: { kind: "ok" } },
      {
        name: "conflict",
        mode: { kind: "conflict", holderUsername: HOLDER_USERNAME },
      },
      { name: "unavailable-500", mode: { kind: "fail", status: 500 } },
    ];

    await page.clock.install();
    for (const c of modes) {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      state.defaultHeartbeat = c.mode;
      await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
      await page.clock.fastForward(1);
      await expect
        .poll(() => heartbeats(state, TECH_A).length, {
          timeout: STRICT_MODE_STABLE_MS + 5_000,
        })
        .toBe(1);
      const first = heartbeats(state, TECH_A)[0]!;
      await expect
        .poll(() => first.finishedAt > 0, { timeout: 5_000 })
        .toBe(true);

      // 必须从“请求完成”后计 15s，而不是从发起/页面打开
      await page.clock.fastForward(14_999);
      expect(
        heartbeats(state, TECH_A).length,
        `case=${c.name} 完成后 14999ms 不得第二次`,
      ).toBe(1);

      await page.clock.fastForward(1);
      await expect
        .poll(() => heartbeats(state, TECH_A).length, {
          timeout: 10_000,
        })
        .toBe(2);
      expect(heartbeats(state, TECH_A)[1]!.body?.clientId).toBe(
        first.body?.clientId,
      );
      await page.goto("about:blank");
    }

    // 慢 heartbeat 单在途不并发（success 分支）
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
      await page.clock.fastForward(1);
      await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);
      const gate = createHoldGate();
      state.defaultHeartbeat = { kind: "gate", gate, then: "ok" };
      await page.clock.fastForward(REFRESH_MS);
      await expect
        .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
        .toBe(2);
      expect(gate.hitCount()).toBe(1);
      expect(gate.isReleased()).toBe(false);
      await page.clock.fastForward(REFRESH_MS);
      expect(heartbeats(state, TECH_A)).toHaveLength(2);
      gate.release();
      await expect
        .poll(() => heartbeats(state, TECH_A).every((h) => h.finishedAt > 0))
        .toBe(true);
      await page.clock.fastForward(REFRESH_MS);
      await waitStableHeartbeatCount(
        state,
        TECH_A,
        3,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      const hb = heartbeats(state, TECH_A);
      for (let i = 1; i < hb.length; i += 1) {
        expect(hb[i].startedAt).toBeGreaterThanOrEqual(hb[i - 1].finishedAt);
      }
    }
  });

  test("A heartbeat 在途切 B：全分支迟到隔离（ok/conflict/bad/abort）", async ({
    page,
  }) => {
    const branches: Array<{
      name: string;
      then: "ok" | "conflict" | "fail" | "bad" | "abort";
      status?: number;
      holderUsername?: string;
      body?: unknown;
    }> = [
      { name: "late-ok", then: "ok" },
      {
        name: "late-conflict",
        then: "conflict",
        holderUsername: HOLDER_USERNAME,
      },
      {
        name: "late-bad-200",
        then: "bad",
        status: 200,
        body: {
          leaseExpiresAt: "2026-07-20T12:35:41.000Z",
          refreshAfterSeconds: 15,
          extra: SECRET_MARKER,
        },
      },
      {
        name: "late-bad-409",
        then: "bad",
        status: 409,
        body: {
          detail: {
            code: "other_code",
            message: CONFLICT_MESSAGE,
            holderUsername: HOLDER_USERNAME,
          },
        },
      },
      { name: "late-http-500", then: "fail", status: 500 },
      { name: "late-abort", then: "abort" },
    ];

    await page.clock.install();
    for (const br of branches) {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      const gateA = createHoldGate();
      await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
      const panel = page.getByTestId(INTENT_TESTID);
      await page.clock.fastForward(1);
      await expect(panel.getByText(SELF_TEXT)).toBeVisible({ timeout: 10_000 });
      await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);

      state.heartbeatMode[leaseKey(TECH_A, CH_A1)] = {
        kind: "gate",
        gate: gateA,
        then: br.then,
        status: br.status,
        holderUsername: br.holderUsername,
        body: br.body,
      };
      await page.clock.fastForward(REFRESH_MS);
      await expect
        .poll(
          () =>
            heartbeats(state, TECH_A).filter((h) => h.body?.chapterId === CH_A1)
              .length,
        )
        .toBe(2);
      const inflightA = heartbeats(state, TECH_A).filter(
        (h) => h.body?.chapterId === CH_A1,
      )[1]!;
      expect(inflightA.finishedAt, `case=${br.name}`).toBe(0);
      expect(gateA.hitCount(), `case=${br.name}`).toBe(1);

      await selectChapterByTitle(page, "章节甲二");
      expect(
        heartbeats(state, TECH_A).filter((h) => h.body?.chapterId === CH_A2),
        `case=${br.name} 释放前不得 B heartbeat`,
      ).toHaveLength(0);

      gateA.release();
      await expect
        .poll(() => {
          const log = state.leaseLog;
          const secondA = log.find((h) => h.seq === inflightA.seq);
          const leaveA = log.find(
            (h) =>
              h.op === "leave" &&
              h.projectId === TECH_A &&
              h.body?.chapterId === CH_A1,
          );
          const hbB = log.find(
            (h) =>
              h.op === "heartbeat" &&
              h.projectId === TECH_A &&
              h.body?.chapterId === CH_A2,
          );
          if (!secondA || !leaveA || !hbB) return false;
          if (secondA.finishedAt <= 0) return false;
          return (
            secondA.seq < leaveA.seq &&
            leaveA.seq < hbB.seq &&
            secondA.finishedAt <= leaveA.startedAt &&
            leaveA.finishedAt <= hbB.startedAt
          );
        }, { timeout: 15_000 })
        .toBe(true);

      // B 不得被迟到 A 覆盖为 conflict/self 错误态；B 自身 200
      await expect(panel.getByText(SELF_TEXT)).toBeVisible({ timeout: 10_000 });
      await expect(panel.getByText(HOLDER_USERNAME)).toHaveCount(0);
      await expect(panel.getByText(SECRET_MARKER)).toHaveCount(0);
      await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);

      const aCountAtSwitch = heartbeats(state, TECH_A).filter(
        (h) => h.body?.chapterId === CH_A1,
      ).length;
      expect(aCountAtSwitch, `case=${br.name}`).toBe(2);

      // B 续租后 A 不重启 timer
      await page.clock.fastForward(REFRESH_MS);
      await waitStableExactCount(
        () =>
          heartbeats(state, TECH_A).filter((h) => h.body?.chapterId === CH_A1)
            .length,
        2,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      await waitStableExactCount(
        () =>
          heartbeats(state, TECH_A).filter((h) => h.body?.chapterId === CH_A2)
            .length,
        2,
        STRICT_MODE_STABLE_MS,
        10_000,
      );

      await page.goto("about:blank");
    }
  });

  test("直接项目 TECH_A/content→TECH_B/content：在途隔离与 clientId", async ({
    page,
  }) => {
    const a = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const b = makeProject({ id: TECH_B, name: "P13G2技术乙" });
    const state = createProbeState([a, b]);
    const gateA = createHoldGate();
    await page.clock.install();
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
    const panel = page.getByTestId(INTENT_TESTID);
    await page.clock.fastForward(1);
    await expect(panel.getByText(SELF_TEXT)).toBeVisible({ timeout: 10_000 });
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);
    const clientIdA = String(heartbeats(state, TECH_A)[0]!.body?.clientId);

    state.heartbeatMode[leaseKey(TECH_A, CH_A1)] = {
      kind: "gate",
      gate: gateA,
      then: "conflict",
      holderUsername: HOLDER_USERNAME,
    };
    await page.clock.fastForward(REFRESH_MS);
    await expect
      .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBe(2);
    const inflightA = heartbeats(state, TECH_A)[1]!;
    expect(inflightA.finishedAt).toBe(0);

    // 直接 A content → B content，不经 facts
    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "P13G2技术乙");
    // 切换同步不得展示旧项目 self/conflict/holder
    await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
    await expect(panel.getByText(HOLDER_USERNAME)).toHaveCount(0);
    await expect(
      page.getByText(`${CONFLICT_PREFIX}${HOLDER_USERNAME}${CONFLICT_SUFFIX}`),
    ).toHaveCount(0);

    // 释放前 B heartbeat 不得插队（队列串行）
    expect(heartbeats(state, TECH_B)).toHaveLength(0);

    gateA.release();
    await expect
      .poll(() => {
        const log = state.leaseLog;
        const secondA = log.find((h) => h.seq === inflightA.seq);
        const leaveA = log.find(
          (h) =>
            h.op === "leave" &&
            h.projectId === TECH_A &&
            h.body?.chapterId === CH_A1,
        );
        const hbB = log.find(
          (h) =>
            h.op === "heartbeat" &&
            h.projectId === TECH_B &&
            h.body?.chapterId === CH_B1,
        );
        if (!secondA || !leaveA || !hbB) return false;
        if (secondA.finishedAt <= 0) return false;
        return (
          secondA.seq < leaveA.seq &&
          leaveA.seq < hbB.seq &&
          secondA.finishedAt <= leaveA.startedAt &&
          leaveA.finishedAt <= hbB.startedAt
        );
      }, { timeout: 15_000 })
      .toBe(true);

    const hbB = heartbeats(state, TECH_B)[0]!;
    assertExactLeaseBody(hbB, CH_B1);
    assertRequiredWriteAuth(hbB);
    expect(hbB.body?.clientId).toBe(clientIdA);
    await expect(page.getByTestId(INTENT_TESTID).getByText(SELF_TEXT)).toBeVisible(
      { timeout: 10_000 },
    );
    await expect(page.getByText(HOLDER_USERNAME)).toHaveCount(0);

    // A 迟到不污染/不续租
    await page.clock.fastForward(REFRESH_MS);
    await waitStableExactCount(
      () => heartbeats(state, TECH_A).length,
      2,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    await waitStableHeartbeatCount(
      state,
      TECH_B,
      2,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
  });

  test("content→facts / 项目切换 leave；非 content 零写", async ({ page }) => {
    const a = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const b = makeProject({ id: TECH_B, name: "P13G2技术乙" });
    const state = createProbeState([a, b]);
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
    await expect
      .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBe(1);

    // content → facts：leave A，facts 步无 chapter lease（独立步骤门）
    const leaveBeforeFacts = leaves(state, TECH_A).length;
    await softNavigate(page, `/technical-plan/${TECH_A}/facts`);
    await expectTechReady(page, "P13G2技术甲");
    await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0);
    await waitStableExactCount(
      () => leaves(state, TECH_A).length,
      leaveBeforeFacts + 1,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    const afterFacts = state.leaseLog.length;
    await waitStableExactCount(
      () => state.leaseLog.length,
      afterFacts,
      ZERO_REQUEST_STABLE_MS,
      8_000,
    );

    // 项目切换到 B content
    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "P13G2技术乙");
    await waitStableHeartbeatCount(
      state,
      TECH_B,
      1,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    assertExactLeaseBody(heartbeats(state, TECH_B)[0]!, CH_B1);
  });

  test("资格门：disabled / 非 bid_writer harness / 初始 hidden 延迟 UUID", async ({
    page,
  }) => {
    // disabled：authRequired=false
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      state.authRequired = false;
      state.sessionAuthenticated = true;
      await installRoutes(page, state);
      await openTech(page, TECH_A, "content");
      await expectTechReady(page, "P13G2技术甲");
      await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0);
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
      await page.goto("about:blank");
    }

    // 非 bid_writer：finance 路由 restricted + 真实 AuthProvider + Panel harness
    {
      const project = makeProject({ id: TECH_B, name: "P13G2技术乙" });
      const state = createProbeState([project]);
      state.role = "finance";
      await installRoutes(page, state);
      await openTech(page, TECH_B, "content");
      await loginViaUi(page);
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0);

      const harnessOk = await page.evaluate(
        async ({ projectId, chapterId, testId }) => {
          const resourceUrls = performance
            .getEntriesByType("resource")
            .map((e) => e.name);
          const pick = (pred: (u: string) => boolean): string[] =>
            resourceUrls.filter(pred);
          const reactCandidates = [
            ...pick((u) => /\/deps\/react\.js(?:\?|$)/.test(u)),
            "/node_modules/.vite/deps/react.js",
            "/node_modules/react/index.js",
          ];
          const clientCandidates = [
            ...pick((u) => /react-dom_client\.js(?:\?|$)/.test(u)),
            "/node_modules/.vite/deps/react-dom_client.js",
            "/node_modules/react-dom/client.js",
          ];

          async function loadFirst(
            urls: string[],
          ): Promise<Record<string, unknown>> {
            let lastErr: unknown;
            for (const u of urls) {
              try {
                return (await import(/* @vite-ignore */ u)) as Record<
                  string,
                  unknown
                >;
              } catch (e) {
                lastErr = e;
              }
            }
            throw lastErr ?? new Error(`module load failed: ${urls.join(",")}`);
          }

          function pickFn(
            mod: Record<string, unknown>,
            name: string,
          ): ((...args: never[]) => unknown) | null {
            const direct = mod[name];
            if (typeof direct === "function") {
              return direct as (...args: never[]) => unknown;
            }
            const def = mod.default;
            if (def && typeof def === "object") {
              const nested = (def as Record<string, unknown>)[name];
              if (typeof nested === "function") {
                return nested as (...args: never[]) => unknown;
              }
            }
            return null;
          }

          const React = await loadFirst(reactCandidates);
          const clientMod = await loadFirst(clientCandidates);
          const authMod = await loadFirst([
            "/src/features/auth/hooks/useAuthSession.ts",
          ]);
          const panelMod = await loadFirst([
            "/src/features/editor-state-collaboration/ChapterEditIntentPanel.tsx",
          ]);

          const createElement = pickFn(React, "createElement");
          const createRoot = pickFn(clientMod, "createRoot");
          const AuthProvider = (authMod.AuthProvider ??
            (authMod.default as Record<string, unknown> | undefined)
              ?.AuthProvider) as
            | import("react").ComponentType<{
                children?: import("react").ReactNode;
              }>
            | undefined;
          const ChapterEditIntentPanel = (panelMod.ChapterEditIntentPanel ??
            panelMod.default) as
            | import("react").ComponentType<{
                projectId: string;
                chapterId: string | null;
              }>
            | undefined;

          if (!createElement || !createRoot || !AuthProvider || !ChapterEditIntentPanel) {
            throw new Error(
              `harness missing createElement=${!!createElement} createRoot=${!!createRoot} AuthProvider=${!!AuthProvider} Panel=${!!ChapterEditIntentPanel}`,
            );
          }

          let host = document.getElementById("p13g2-chapter-intent-harness");
          if (!host) {
            host = document.createElement("div");
            host.id = "p13g2-chapter-intent-harness";
            document.body.appendChild(host);
          }
          const root = (
            createRoot as unknown as (el: Element) => {
              render: (node: unknown) => void;
            }
          )(host);
          root.render(
            (
              createElement as unknown as (
                type: unknown,
                props: unknown,
                ...children: unknown[]
              ) => unknown
            )(
              AuthProvider,
              null,
              (
                createElement as unknown as (
                  type: unknown,
                  props: unknown,
                ) => unknown
              )(ChapterEditIntentPanel, {
                projectId,
                chapterId,
              }),
            ),
          );
          // 记录 harness 元数据供调试；testid 由生产常量固定
          host.setAttribute("data-harness-for", testId);
          return true;
        },
        { projectId: TECH_B, chapterId: CH_B1, testId: INTENT_TESTID },
      );
      expect(harnessOk, "finance chapter-intent harness 必须成功挂载").toBe(
        true,
      );

      // 面板私有角色门：finance 即使直接挂载也必须不渲染 testid
      await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0, {
        timeout: 10_000,
      });
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
      await page.goto("about:blank");
    }

    // 初始 hidden：延迟 UUID 精确 0，直到首次 visible 才 1 并复用
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      await installUuidCallProbe(page);
      await page.addInitScript(() => {
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          get: () => "hidden",
        });
        Object.defineProperty(document, "hidden", {
          configurable: true,
          get: () => true,
        });
      });
      await installRoutes(page, state);
      await openTech(page, TECH_A, "content");
      await loginViaUi(page);
      await expectTechReady(page, "P13G2技术甲");

      // hidden ready + 完整稳定窗口
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
      const uuidHidden = await readUuidProbe(page);
      expect(uuidHidden.calls, "初始 hidden UUID calls 精确 0").toBe(0);
      expect(uuidHidden.ids, "初始 hidden UUID ids 精确空").toEqual([]);
      expect(state.leaseLog).toHaveLength(0);
      expect(presenceHeartbeats(state, TECH_A)).toHaveLength(0);

      const panel = page.getByTestId(INTENT_TESTID);
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();
      await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
      await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);
      await expect(panel.getByText(HOLDER_USERNAME)).toHaveCount(0);

      // 首次 visible：calls 精确 1；presence 与 chapter lease 复用该 ID
      await setVisibility(page, "visible");
      await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);
      await expect
        .poll(() => presenceHeartbeats(state, TECH_A).length, {
          timeout: 10_000,
        })
        .toBeGreaterThanOrEqual(1);
      const uuidFirst = await readUuidProbe(page);
      expect(uuidFirst.calls, "首次 visible UUID calls 精确 1").toBe(1);
      expect(uuidFirst.ids).toHaveLength(1);
      const onlyId = uuidFirst.ids[0]!;
      expect(onlyId).toMatch(CLIENT_ID_RE);
      const leaseHb = heartbeats(state, TECH_A)[0]!;
      const presenceHb = presenceHeartbeats(state, TECH_A)[0]!;
      expect(leaseHb.body?.clientId).toBe(onlyId);
      expect(presenceHb.body?.clientId).toBe(onlyId);
      assertExactLeaseBody(leaseHb, CH_A1);

      // 再 hidden→visible：calls 仍精确 1，两类 body 仍复用
      const leaveBeforeUuid = leaves(state, TECH_A).length;
      await setVisibility(page, "hidden");
      await waitStableExactCount(
        () => leaves(state, TECH_A).length,
        leaveBeforeUuid + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      const hbBeforeUuid = heartbeats(state, TECH_A).length;
      await setVisibility(page, "visible");
      await waitStableExactCount(
        () => heartbeats(state, TECH_A).length,
        hbBeforeUuid + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      expect(heartbeats(state, TECH_A).length, "再 visible 后 G2 heartbeat 总数精确 2").toBe(2);
      const uuidAgain = await readUuidProbe(page);
      expect(uuidAgain.calls, "再 visible 后 UUID calls 仍精确 1").toBe(1);
      expect(uuidAgain.ids).toEqual([onlyId]);
      for (const h of heartbeats(state, TECH_A)) {
        expect(h.body?.clientId).toBe(onlyId);
      }
      for (const h of presenceHeartbeats(state, TECH_A)) {
        expect(h.body?.clientId).toBe(onlyId);
      }
    }
  });

  test("parser 矩阵：坏 200/409 固定 unavailable；合法边界 holder 可展示", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const astral100 = `${"😀".repeat(99)}A`;
    const astral101 = "😀".repeat(101);
    expect([...astral100].length).toBe(100);
    expect([...astral101].length).toBe(101);
    expect([...HOLDER_BOUNDARY_100].length).toBe(100);

    const ok200 = {
      leaseExpiresAt: "2026-07-20T12:35:41.000Z",
      refreshAfterSeconds: 15 as const,
    };
    const ok409Detail = {
      code: CONFLICT_CODE,
      message: CONFLICT_MESSAGE,
      holderUsername: HOLDER_USERNAME,
    };

    const forbiddenBidi: Array<{ name: string; ch: string }> = [
      { name: "bidi-ALM-U+061C", ch: "\u061c" },
      { name: "bidi-LRM-U+200E", ch: "\u200e" },
      { name: "bidi-RLM-U+200F", ch: "\u200f" },
      { name: "bidi-LRE-U+202A", ch: "\u202a" },
      { name: "bidi-RLE-U+202B", ch: "\u202b" },
      { name: "bidi-PDF-U+202C", ch: "\u202c" },
      { name: "bidi-LRO-U+202D", ch: "\u202d" },
      { name: "bidi-RLO-U+202E", ch: "\u202e" },
      { name: "bidi-LRI-U+2066", ch: "\u2066" },
      { name: "bidi-RLI-U+2067", ch: "\u2067" },
      { name: "bidi-FSI-U+2068", ch: "\u2068" },
      { name: "bidi-PDI-U+2069", ch: "\u2069" },
    ];

    type Case = {
      name: string;
      mode: HeartbeatMode;
      expectConflictHolder?: string;
    };

    const cases: Case[] = [
      // 200 root 非对象
      { name: "200-root-array", mode: { kind: "bad", body: [] } },
      { name: "200-root-scalar", mode: { kind: "bad", body: 15 } },
      { name: "200-root-null", mode: { kind: "bad", body: null } },
      // 200 顶层 extra / 缺键
      {
        name: "200-extra-top",
        mode: { kind: "bad", body: { ...ok200, extra: SECRET_MARKER } },
      },
      {
        name: "200-missing-leaseExpiresAt",
        mode: { kind: "bad", body: { refreshAfterSeconds: 15 } },
      },
      {
        name: "200-missing-refresh",
        mode: {
          kind: "bad",
          body: { leaseExpiresAt: "2026-07-20T12:35:41.000Z" },
        },
      },
      // leaseExpiresAt 空/坏类型/坏时间
      {
        name: "200-lease-empty",
        mode: {
          kind: "bad",
          body: { leaseExpiresAt: "", refreshAfterSeconds: 15 },
        },
      },
      {
        name: "200-lease-bad-type",
        mode: {
          kind: "bad",
          body: { leaseExpiresAt: 12345, refreshAfterSeconds: 15 },
        },
      },
      {
        name: "200-lease-bad-time",
        mode: {
          kind: "bad",
          body: { leaseExpiresAt: "not-a-date", refreshAfterSeconds: 15 },
        },
      },
      // refresh 坏类型/非15
      {
        name: "200-refresh-string",
        mode: {
          kind: "bad",
          body: {
            leaseExpiresAt: "2026-07-20T12:35:41.000Z",
            refreshAfterSeconds: "15",
          },
        },
      },
      {
        name: "200-refresh-not-15",
        mode: {
          kind: "bad",
          body: {
            leaseExpiresAt: "2026-07-20T12:35:41.000Z",
            refreshAfterSeconds: 16,
          },
        },
      },
      // 409 顶层
      {
        name: "409-extra-top",
        mode: {
          kind: "bad",
          status: 409,
          body: { detail: ok409Detail, extra: SECRET_MARKER },
        },
      },
      {
        name: "409-missing-detail",
        mode: {
          kind: "bad",
          status: 409,
          body: { code: CONFLICT_CODE },
        },
      },
      {
        name: "409-detail-array",
        mode: { kind: "bad", status: 409, body: { detail: [] } },
      },
      {
        name: "409-detail-scalar",
        mode: { kind: "bad", status: 409, body: { detail: "x" } },
      },
      {
        name: "409-detail-null",
        mode: { kind: "bad", status: 409, body: { detail: null } },
      },
      // detail extra / 缺键
      {
        name: "409-detail-extra",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: { ...ok409Detail, secret: SECRET_MARKER },
          },
        },
      },
      {
        name: "409-detail-missing-code",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              message: CONFLICT_MESSAGE,
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      {
        name: "409-detail-missing-message",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      {
        name: "409-detail-missing-holder",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
            },
          },
        },
      },
      // 三字段坏类型
      {
        name: "409-code-bad-type",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: 1,
              message: CONFLICT_MESSAGE,
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      {
        name: "409-message-bad-type",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: 1,
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      {
        name: "409-holder-bad-type",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: 1,
            },
          },
        },
      },
      // wrong code / message
      {
        name: "409-wrong-code",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: "other_code",
              message: CONFLICT_MESSAGE,
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      {
        name: "409-wrong-message",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: "其它冲突文案",
              holderUsername: HOLDER_USERNAME,
            },
          },
        },
      },
      // holder 边界非法
      {
        name: "holder-empty",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "",
            },
          },
        },
      },
      {
        name: "holder-101-codepoints",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: `${"边".repeat(100)}界`,
            },
          },
        },
      },
      {
        name: "holder-trim-space",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "  spaced  ",
            },
          },
        },
      },
      {
        name: "holder-C0",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "坏\u0001名",
            },
          },
        },
      },
      {
        name: "holder-C1",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "坏\u0081名",
            },
          },
        },
      },
      {
        name: "holder-DEL",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "坏\u007f名",
            },
          },
        },
      },
      {
        name: "holder-U+2028",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "坏\u2028名",
            },
          },
        },
      },
      {
        name: "holder-U+2029",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: "坏\u2029名",
            },
          },
        },
      },
      {
        name: "holder-astral-101",
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: astral101,
            },
          },
        },
      },
      // HTTP 500
      { name: "http-500", mode: { kind: "fail", status: 500 } },
    ];

    for (const b of forbiddenBidi) {
      cases.push({
        name: `holder-${b.name}`,
        mode: {
          kind: "bad",
          status: 409,
          body: {
            detail: {
              code: CONFLICT_CODE,
              message: CONFLICT_MESSAGE,
              holderUsername: `坏${b.ch}${SECRET_MARKER}`,
            },
          },
        },
      });
    }

    // 单会话：首包建立 clientId/panel，后续仅 hidden→visible 独立触发每案
    const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const state = createProbeState([project]);
    const consoleLines = collectConsole(page);
    state.defaultHeartbeat = cases[0]!.mode;
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
    const panel = page.getByTestId(INTENT_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await waitStableHeartbeatCount(
      state,
      TECH_A,
      1,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    const clientId = String(heartbeats(state, TECH_A)[0]!.body?.clientId ?? "");
    expect(clientId).toMatch(CLIENT_ID_RE);
    assertRequiredWriteAuth(heartbeats(state, TECH_A)[0]!);

    for (let i = 0; i < cases.length; i += 1) {
      const c = cases[i]!;
      state.defaultHeartbeat = c.mode;
      const leaveBefore = leaves(state, TECH_A).length;
      await setVisibility(page, "hidden");
      await waitStableExactCount(
        () => leaves(state, TECH_A).length,
        leaveBefore + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      const beforeHb = heartbeats(state, TECH_A).length;
      await setVisibility(page, "visible");
      await waitStableExactCount(
        () => heartbeats(state, TECH_A).length,
        beforeHb + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      const hb = heartbeats(state, TECH_A)[heartbeats(state, TECH_A).length - 1]!;
      expect(hb.body?.clientId, `case=${c.name}`).toBe(clientId);
      assertExactLeaseBody(hb, CH_A1);
      assertRequiredWriteAuth(hb);

      await expect(
        panel.getByText(UNAVAILABLE_TEXT, { exact: true }),
        `case=${c.name} 必须 unavailable`,
      ).toBeVisible({ timeout: 10_000 });
      await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
      await expect(panel.getByText(HOLDER_USERNAME)).toHaveCount(0);
      await expect(panel.getByText(SECRET_MARKER)).toHaveCount(0);
      await expect(panel.getByText(CONFLICT_CODE)).toHaveCount(0);
      await expect(panel.getByText(CONFLICT_MESSAGE)).toHaveCount(0);
      const panelText = (await panel.innerText()).trim();
      expect(panelText, `case=${c.name}`).toBe(
        `${TITLE_TEXT}\n${UNAVAILABLE_TEXT}`,
      );
      if (c.name.includes("bidi") || c.name.startsWith("holder-")) {
        expect(panelText, `case=${c.name} 零部分展示`).not.toContain("坏");
      }
    }

    // 合法边界：100 码点 BMP + 100 astral（独立 conflict 真包）
    const legalHolders = [
      { name: "holder-100-bmp", holder: HOLDER_BOUNDARY_100 },
      { name: "holder-100-astral", holder: astral100 },
    ];
    for (const c of legalHolders) {
      state.defaultHeartbeat = {
        kind: "conflict",
        holderUsername: c.holder,
      };
      const leaveBefore = leaves(state, TECH_A).length;
      await setVisibility(page, "hidden");
      await waitStableExactCount(
        () => leaves(state, TECH_A).length,
        leaveBefore + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      const beforeHb = heartbeats(state, TECH_A).length;
      await setVisibility(page, "visible");
      await waitStableExactCount(
        () => heartbeats(state, TECH_A).length,
        beforeHb + 1,
        STRICT_MODE_STABLE_MS,
        10_000,
      );
      await expect(
        panel.getByText(
          `${CONFLICT_PREFIX}${c.holder}${CONFLICT_SUFFIX}`,
          { exact: true },
        ),
        `case=${c.name}`,
      ).toBeVisible({ timeout: 10_000 });
      await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);
      await expect(panel.getByText(SELF_TEXT)).toHaveCount(0);
      const hb = heartbeats(state, TECH_A)[heartbeats(state, TECH_A).length - 1]!;
      expect(hb.body?.clientId).toBe(clientId);
      expect(hb.status).toBe(409);
    }

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [clientId],
      consoleLines,
      state.externalHits,
      panelText,
      {
        chapterIds: [CH_A1],
        holderUsernames: legalHolders.map((h) => h.holder),
      },
    );
  });

  test("console 隐私门自校：含真实 UUID 的资源失败行必须拒绝", () => {
    const fakeId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
    const noisyLine = `error: Failed to load resource: net::ERR ${fakeId}`;
    expect(() =>
      assertConsoleNoSensitiveLeak([noisyLine], [fakeId]),
    ).toThrow();
    // 不含真实 UUID 的资源失败可被 residual 过滤
    const clean = "error: Failed to load resource: net::ERR_FAILED";
    expect(appConsoleLines([clean], [fakeId])).toEqual([]);
    expect(appConsoleLines([noisyLine], [fakeId])).toEqual([noisyLine]);
  });

  test("无章节 / 坏 UUID / 缺 CSRF：完整稳定窗口零 chapter-lease 请求", async ({
    page,
  }) => {
    // 无章节：content 步挂载但 selectedChapter 空 → 面板零请求
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project], { [TECH_A]: [] });
      await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
      await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0);
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
      await page.goto("about:blank");
    }

    // 坏 UUID：crypto.randomUUID 返回非法格式 → unavailable 且零写
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      await page.addInitScript(() => {
        Object.defineProperty(crypto, "randomUUID", {
          configurable: true,
          value: () => "not-a-valid-uuid!!!",
        });
      });
      await installRoutes(page, state);
      await openTech(page, TECH_A, "content");
      await loginViaUi(page);
      await expectTechReady(page, "P13G2技术甲");
      const panel = page.getByTestId(INTENT_TESTID);
      await expect(panel).toBeVisible({ timeout: 10_000 });
      await expect(
        panel.getByText(UNAVAILABLE_TEXT, { exact: true }),
      ).toBeVisible({ timeout: 10_000 });
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
      await page.goto("about:blank");
    }

    // 缺 CSRF：登录后 csrf 接口返回空 → 零写
    {
      const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
      const state = createProbeState([project]);
      state.csrfToken = "";
      await installRoutes(page, state);
      // 覆盖 csrf 路由为空 token：login 响应也不带有效 token
      await page.route("**/api/auth/login", async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback();
          return;
        }
        state.sessionAuthenticated = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: {
            "Set-Cookie": `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}; Path=/; HttpOnly; SameSite=Lax`,
            "Cache-Control": "no-store",
          },
          body: JSON.stringify({
            user: { id: "user_e2e", username: E2E_LOGIN_USER },
            workspaces: [workspaceForRole("bid_writer")],
            activeWorkspaceId: "ws_e2e",
            csrfToken: "",
          }),
        });
      });
      await page.route("**/api/auth/csrf", async (route) => {
        if (route.request().method() !== "GET") {
          await route.fallback();
          return;
        }
        await json(route, { csrfToken: "" });
      });
      await openTech(page, TECH_A, "content");
      await loginViaUi(page);
      await expectTechReady(page, "P13G2技术甲");
      // 面板可能 loading/unavailable；关键：稳定窗口零 lease 写
      await waitStableLeaseCount(state, 0, ZERO_REQUEST_STABLE_MS);
    }
  });

  test("章节删除后有效选择变化：leave 旧章并对新有效章 heartbeat", async ({
    page,
  }) => {
    const project = makeProject({ id: TECH_A, name: "P13G2技术甲" });
    const state = createProbeState([project]);
    await openAuthedContent(page, state, TECH_A, "P13G2技术甲");
    await expect
      .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBe(1);
    assertExactLeaseBody(heartbeats(state, TECH_A)[0]!, CH_A1);
    const leaveBefore = leaves(state, TECH_A).length;

    // 远端仅剩甲二：先离开 content（卸载面板 leave A1），再改 GET 快照并回到 content
    await softNavigate(page, `/technical-plan/${TECH_A}/facts`);
    await expectTechReady(page, "P13G2技术甲");
    await expect(page.getByTestId(INTENT_TESTID)).toHaveCount(0);
    await expect
      .poll(() => leaves(state, TECH_A).length, { timeout: 10_000 })
      .toBeGreaterThan(leaveBefore);

    state.editorById[TECH_A] = emptyEditor(TECH_A, [
      makeChapter(CH_A2, "章节甲二", "甲二正文。"),
    ]);
    // 换项目再换回会强制 hook 重读 editor-state，模拟删除后有效选择变化
    const b = makeProject({ id: TECH_B, name: "P13G2技术乙" });
    state.projects.push(b);
    state.editorById[TECH_B] = emptyEditor(TECH_B, [
      makeChapter(CH_B1, "章节乙一"),
    ]);
    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "P13G2技术乙");
    await softNavigate(page, `/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "P13G2技术甲");

    await expect
      .poll(
        () =>
          heartbeats(state, TECH_A).filter((h) => h.body?.chapterId === CH_A2)
            .length,
        { timeout: 15_000 },
      )
      .toBeGreaterThanOrEqual(1);
    const last = heartbeats(state, TECH_A).filter(
      (h) => h.body?.chapterId === CH_A2,
    )[0]!;
    assertExactLeaseBody(last, CH_A2);
    // 不得再对已删除的甲一 heartbeat
    const afterReselect = heartbeats(state, TECH_A).filter(
      (h) => h.body?.chapterId === CH_A1 && h.seq > last.seq,
    );
    expect(afterReselect).toHaveLength(0);
  });
});
