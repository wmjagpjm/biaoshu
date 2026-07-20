/**
 * 模块：P13-F2 项目近期成员前端专项 E2E
 * 用途：required 已认证 strict bid_writer 下验证技术/商务 presence 挂载、
 *       初次 heartbeat/leave 精确 body 与 CSRF、StrictMode 稳定窗口、
 *       15 秒续租与慢请求串行、hidden/visible、pagehide、A→B 迟到隔离、
 *       disabled/非 bid_writer 零请求、坏响应与 clientId/secret 零出口。
 * 对接：Playwright chromium 单 worker；同源路由桩记录 presence 写链；
 *       固定 testid technical-project-presence / business-project-presence。
 * 二次开发：禁止源码字符串、恒真集合、未触发事件、初值已满足的伪稳定；
 *       请求计数只认真实 route 命中；不得 waitForTimeout 作完成证据；
 *       UI 不得出现在线/实时/正在编辑/正在输入/最后活跃。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p13f2_tech_a";
const TECH_B = "proj_e2e_p13f2_tech_b";
const BIZ_A = "proj_e2e_p13f2_biz_a";
const TECH_TESTID = "technical-project-presence";
const BIZ_TESTID = "business-project-presence";
const TITLE_TEXT = "近期在此项目";
const LOADING_TEXT = "近期成员加载中";
const UNAVAILABLE_TEXT = "近期成员暂不可用";
const TRUNCATED_TEXT = "另有更多近期成员";
const SELF_SUFFIX = "（我）";
const CSRF_TOKEN = "e2e-p13f2-csrf-token-memory";
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p13f2_sess_opaque";
const E2E_LOGIN_USER = "e2e_p13f2_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const SELF_USERNAME = "e2e_p13f2_user";
const OTHER_USERNAME = "同事甲";
const SECRET_MARKER = "SECRET_P13F2_LEAK_MARKER_xyz";
/** 规范 UUID（含 v1-v5）；成功路径仍要求 v4 形态 */
const CLIENT_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const CLIENT_ID_BACKEND_RE = /^[A-Za-z0-9_-]{22,64}$/;
const FORBIDDEN_UI_PHRASES = [
  "在线",
  "实时",
  "正在编辑",
  "正在输入",
  "最后活跃",
] as const;
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
/** StrictMode 稳定窗口：取消首轮探测后只应留下一次首跳 */
const STRICT_MODE_STABLE_MS = 250;
const REFRESH_MS = 15_000;
/** 非请求稳定窗口：证明零 presence 在完整窗口内保持 */
const ZERO_REQUEST_STABLE_MS = 400;

type Kind = "technical" | "business";
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
  kind: Kind;
  linkedProjectId?: string | null;
};

type EditorState = {
  projectId: string;
  outline: unknown[];
  chapters: unknown[];
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

type PresenceMember = { username: string; isSelf: boolean };

type PresenceHit = {
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

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  /** 当前卡在 wait 的调用数（route 已命中、finishedAt 仍为 0） */
  waiterCount: () => number;
  hitCount: () => number;
};

type FetchProbeEntry = {
  url: string;
  method: string;
  body: string;
  keepalive: boolean;
  at: number;
};

type IdbWriteProbeEntry = {
  db: string;
  store: string;
  key: string;
  value: string;
};

type HeartbeatMode =
  | { kind: "ok"; members?: PresenceMember[]; truncated?: boolean }
  | { kind: "fail"; status: number }
  | { kind: "bad"; body: unknown }
  | {
      kind: "gate";
      gate: HoldGate;
      then: "ok" | "fail" | "bad";
      status?: number;
      members?: PresenceMember[];
      truncated?: boolean;
      body?: unknown;
    };

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  authRequired: boolean;
  sessionAuthenticated: boolean;
  role: AuthRole;
  csrfToken: string;
  presenceSeq: number;
  presenceLog: PresenceHit[];
  /** 按项目覆盖下一次/持续 heartbeat 行为 */
  heartbeatMode: Record<string, HeartbeatMode>;
  /** 全局默认 heartbeat 行为 */
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
  ];
  return projectRules.some(
    (r) => r.methods.includes(method) && r.rest.test(rest),
  );
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-20T12:00:00",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: partial.kind,
  };
}

function emptyEditor(projectId: string, kind: Kind): EditorState {
  return {
    projectId,
    outline: [],
    chapters: [],
    facts: [],
    mode: kind === "technical" ? "analysis" : "business",
    analysisOverview: "P13F2 概述",
    analysis: {
      overview: "P13F2 概述",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: kind === "business" ? "P13F2 商务正文" : "",
    guidance: null,
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

function defaultMembers(): PresenceMember[] {
  return [
    { username: SELF_USERNAME, isSelf: true },
    { username: OTHER_USERNAME, isSelf: false },
  ];
}

function createProbeState(projects: ProjectStub[]): ProbeState {
  const editorById: Record<string, EditorState> = {};
  for (const p of projects) {
    editorById[p.id] = emptyEditor(p.id, p.kind);
  }
  return {
    projects,
    editorById,
    authRequired: true,
    sessionAuthenticated: false,
    role: "bid_writer",
    csrfToken: CSRF_TOKEN,
    presenceSeq: 0,
    presenceLog: [],
    heartbeatMode: {},
    defaultHeartbeat: { kind: "ok", members: defaultMembers(), truncated: false },
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

async function installRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p13f2Clip?: { installed: boolean; read: number; write: number };
      __p13f2FetchLog?: Array<{
        url: string;
        method: string;
        body: string;
        keepalive: boolean;
        at: number;
      }>;
      __p13f2IdbWrites?: Array<{
        db: string;
        store: string;
        key: string;
        value: string;
      }>;
    };
    g.__p13f2Clip = { installed: false, read: 0, write: 0 };
    g.__p13f2FetchLog = [];
    g.__p13f2IdbWrites = [];

    const clip = {
      readText: async () => {
        g.__p13f2Clip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__p13f2Clip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__p13f2Clip.installed = true;
    } catch {
      g.__p13f2Clip.installed = false;
    }

    // fetch 层：记录 URL/method/body/keepalive，供 leave keepalive 与零出口断言
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      let url = "";
      let method = (init?.method ?? "GET").toUpperCase();
      let body = "";
      let keepalive = init?.keepalive === true;
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
          if (init?.keepalive === undefined && "keepalive" in (input as Request)) {
            keepalive = Boolean((input as Request).keepalive);
          }
        }
        if (typeof init?.body === "string") {
          body = init.body;
        } else if (init?.body != null) {
          body = String(init.body);
        }
      } catch {
        url = String(input);
      }
      g.__p13f2FetchLog!.push({
        url,
        method,
        body,
        keepalive,
        at: Date.now(),
      });
      return originalFetch(input as RequestInfo, init);
    };

    // IndexedDB 写探针：记录 put/add 的 db/store/key/value
    const patchIdbStore = (proto: IDBObjectStore) => {
      const originalPut = proto.put;
      const originalAdd = proto.add;
      proto.put = function patchedPut(
        value: unknown,
        key?: IDBValidKey,
      ): IDBRequest {
        try {
          const dbName = this.transaction.db.name;
          g.__p13f2IdbWrites!.push({
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
          g.__p13f2IdbWrites!.push({
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
      // 先记录 URL/body，再 fulfill；不得绕过敏感值扫描
      const post = req.postData() || "";
      state.externalHits.push(`${method} ${rawUrl}${post ? ` body=${post}` : ""}`);
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
        { detail: { code: "p13f2_forbidden", message: SECRET_MARKER } },
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
        await json(route, { detail: { code: "p13f2_no_create" } }, 403);
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
        const body = state.editorById[id] ?? emptyEditor(id, "technical");
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
      const current = state.editorById[id] ?? emptyEditor(id, "technical");
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
      const startedAt = Date.now();
      state.presenceSeq += 1;
      const seq = state.presenceSeq;

      // 先入日志再 gate.wait，确保慢请求可被并发断言观察到
      const hit: PresenceHit = {
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
      state.presenceLog.push(hit);

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

      const mode = state.heartbeatMode[projectId] ?? state.defaultHeartbeat;
      let fulfillBody: string | null = null;
      let fulfillStatus = 200;

      const buildOk = (
        members: PresenceMember[] = defaultMembers(),
        truncated = false,
      ) => {
        const payload = {
          leaseExpiresAt: "2026-07-20T12:35:41",
          refreshAfterSeconds: 15,
          members,
          truncated,
        };
        return JSON.stringify(payload);
      };

      if (mode.kind === "gate") {
        await mode.gate.wait();
        if (mode.then === "fail") {
          fulfillStatus = mode.status ?? 500;
          fulfillBody = JSON.stringify({
            detail: {
              code: "presence_heartbeat_failed",
              message: SECRET_MARKER,
            },
          });
        } else if (mode.then === "bad") {
          fulfillStatus = 200;
          fulfillBody = JSON.stringify(
            mode.body ?? { bad: true, secret: SECRET_MARKER },
          );
        } else {
          fulfillStatus = 200;
          fulfillBody = buildOk(mode.members, mode.truncated ?? false);
        }
      } else if (mode.kind === "fail") {
        fulfillStatus = mode.status;
        fulfillBody = JSON.stringify({
          detail: {
            code: "presence_heartbeat_failed",
            message: SECRET_MARKER,
          },
        });
      } else if (mode.kind === "bad") {
        fulfillStatus = 200;
        fulfillBody = JSON.stringify(mode.body);
      } else {
        fulfillStatus = 200;
        fulfillBody = buildOk(mode.members, mode.truncated ?? false);
      }

      hit.finishedAt = Date.now();
      hit.status = fulfillStatus;
      hit.responseBody = fulfillBody ?? "";
      await route.fulfill({
        status: fulfillStatus,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: fulfillBody ?? "",
      });
      return;
    }

    // 其它已允许的项目子路径：返回空成功，避免阻断页面
    if (method === "GET") {
      await json(route, []);
      return;
    }
    if (method === "POST") {
      await json(route, { id: "task_e2e", status: "queued", type: "noop" });
      return;
    }
    await json(route, { detail: { code: "p13f2_unhandled" } }, 404);
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

/**
 * 用途：过滤已确认不含敏感值的浏览器资源失败噪声。
 * 规则：必须先对原始 console 行扫描 SECRET_MARKER 与本轮实际 clientIds；
 *       不得以字面量 `clientId` 代替真实 UUID 检查；含真实 UUID/secret 的行不可过滤。
 */
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

/**
 * 用途：对原始 console 行做敏感值零出口门（先于任何噪声过滤）。
 */
function assertConsoleNoSensitiveLeak(
  consoleLines: string[],
  clientIds: string[],
) {
  for (const line of consoleLines) {
    expect(line, "secret marker 经 console 泄漏").not.toContain(SECRET_MARKER);
    for (const id of clientIds) {
      if (!id) continue;
      expect(line, `clientId 经 console 泄漏: ${id}`).not.toContain(id);
    }
  }
}

/**
 * 用途：稳定计数门——首次满足 expected 后必须再观察完整 windowMs，窗口内始终精确等于 expected。
 * 禁止初值 expect.poll 立即成功；禁止纯 waitForTimeout 冒充完成。
 */
async function waitStableExactCount(
  getCount: () => number,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  const deadline = Date.now() + timeoutMs;
  // 先等到首次精确满足
  await expect
    .poll(() => getCount(), { timeout: timeoutMs })
    .toBe(expected);
  const stableStart = Date.now();
  // 再观察完整窗口：poll 仅在「窗口已满且仍精确」时成功
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

async function waitStablePresenceCount(
  state: ProbeState,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  await waitStableExactCount(
    () => state.presenceLog.length,
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
      __p13f2Clip?: { installed: boolean; read: number; write: number };
    };
    return g.__p13f2Clip ?? { installed: false, read: -1, write: -1 };
  });
}

async function readFetchProbe(page: Page): Promise<FetchProbeEntry[]> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13f2FetchLog?: FetchProbeEntry[];
    };
    return g.__p13f2FetchLog ?? [];
  });
}

async function readIdbWriteProbe(page: Page): Promise<IdbWriteProbeEntry[]> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13f2IdbWrites?: IdbWriteProbeEntry[];
    };
    return g.__p13f2IdbWrites ?? [];
  });
}

function presenceFetchEntries(
  log: FetchProbeEntry[],
  op: "heartbeat" | "leave",
  projectId?: string,
): FetchProbeEntry[] {
  const needle =
    projectId !== undefined
      ? `/api/projects/${projectId}/presence/${op}`
      : `/presence/${op}`;
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

async function openTech(page: Page, projectId: string, step = "analysis") {
  await page.goto(`/technical-plan/${projectId}/${step}`);
}

async function openBiz(page: Page, projectId: string, step = "parse") {
  await page.goto(`/business-bid/${projectId}/${step}`);
}

async function softNavigate(
  page: Page,
  url: string,
) {
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

async function expectBizReady(page: Page, name: string) {
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
}

async function waitPresenceCount(
  state: ProbeState,
  count: number,
  timeoutMs = 10_000,
) {
  await expect
    .poll(() => state.presenceLog.length, { timeout: timeoutMs })
    .toBe(count);
}

async function waitPresenceAtLeast(
  state: ProbeState,
  count: number,
  timeoutMs = 10_000,
) {
  await expect
    .poll(() => state.presenceLog.length, { timeout: timeoutMs })
    .toBeGreaterThanOrEqual(count);
}

function heartbeats(state: ProbeState, projectId?: string): PresenceHit[] {
  return state.presenceLog.filter(
    (h) =>
      h.op === "heartbeat" &&
      (projectId === undefined || h.projectId === projectId),
  );
}

function leaves(state: ProbeState, projectId?: string): PresenceHit[] {
  return state.presenceLog.filter(
    (h) =>
      h.op === "leave" &&
      (projectId === undefined || h.projectId === projectId),
  );
}

function assertExactClientBody(hit: PresenceHit) {
  expect(hit.body, "presence body 必须是对象").not.toBeNull();
  const keys = Object.keys(hit.body ?? {}).sort();
  expect(keys).toEqual(["clientId"]);
  const clientId = hit.body!.clientId;
  expect(typeof clientId).toBe("string");
  expect(clientId as string).toMatch(CLIENT_ID_RE);
  expect(hit.rawBody).toBe(JSON.stringify({ clientId }));
}

function assertRequiredWriteAuth(hit: PresenceHit) {
  expect(hit.csrf).toBe(CSRF_TOKEN);
  expect(hit.cookie, "必须携带登录会话 Cookie").toContain(
    `${SESSION_COOKIE_NAME}=${SESSION_COOKIE_VALUE}`,
  );
  expect(hit.cookie).not.toContain(CSRF_TOKEN);
  expect(hit.cookie).not.toContain(SECRET_MARKER);
  expect(hit.path).toMatch(
    /\/api\/projects\/[^/]+\/presence\/(heartbeat|leave)\/?$/,
  );
  expect(hit.method).toBe("POST");
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
      __p13f2IdbWrites?: Array<{
        db: string;
        store: string;
        key: string;
        value: string;
      }>;
      __p13f2FetchLog?: Array<{
        url: string;
        method: string;
        body: string;
        keepalive: boolean;
        at: number;
      }>;
    };
    const idbWrites = (g.__p13f2IdbWrites ?? []).map(
      (w) => `${w.db}|${w.store}|${w.key}|${w.value}`,
    );
    const fetchBodies = (g.__p13f2FetchLog ?? []).map(
      (f) => `${f.method} ${f.url} keepalive=${f.keepalive} body=${f.body}`,
    );
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
    };
  });
}

function assertNoSensitiveLeak(
  surfaces: Awaited<ReturnType<typeof readLeakSurfaces>>,
  clientIds: string[],
  consoleLines: string[],
  externalHits: string[],
  presencePanelText: string,
  options?: {
    /** presence 请求 body 中允许出现的 clientId（仅精确 JSON body 通道） */
    allowClientIdsInPresenceBody?: boolean;
  },
) {
  // 1) 原始 console 必须先逐行扫描 secret 与本轮实际 clientIds（不可先过滤）
  assertConsoleNoSensitiveLeak(consoleLines, clientIds);

  // 2) 仅过滤已确认不含敏感值的资源失败噪声，供后续 residual 扫描
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
  ];
  // fetch body：clientId 仅允许出现在 presence heartbeat/leave 的精确 JSON 中
  for (const row of surfaces.fetchBodies) {
    expect(row, "secret marker 经 fetch 泄漏").not.toContain(SECRET_MARKER);
    const isPresenceWrite =
      row.includes("/presence/heartbeat") || row.includes("/presence/leave");
    for (const id of clientIds) {
      if (!id) continue;
      if (isPresenceWrite && options?.allowClientIdsInPresenceBody) {
        // 仍禁止出现在 URL 段
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
  // 禁用承诺仅约束 presence 面板，避免误伤壳层「API 在线」等既有文案
  for (const phrase of FORBIDDEN_UI_PHRASES) {
    expect(presencePanelText, `presence 禁用文案: ${phrase}`).not.toContain(
      phrase,
    );
  }
}

async function assertPrivacyClosed(
  page: Page,
  clientIds: string[],
  consoleLines: string[],
  externalHits: string[],
  presencePanelText: string,
) {
  const clip = await readClipboardProbe(page);
  expect(clip.installed, "clipboard override 必须安装成功").toBe(true);
  expect(clip.read, "clipboard.read 必须为 0").toBe(0);
  expect(clip.write, "clipboard.write 必须为 0").toBe(0);

  const surfaces = await readLeakSurfaces(page);
  // 传入原始 consoleLines：隐私门内部先 raw 扫描，再 residual 过滤
  assertNoSensitiveLeak(
    surfaces,
    clientIds,
    consoleLines,
    externalHits,
    presencePanelText,
    { allowClientIdsInPresenceBody: true },
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

/** 读取页面内 randomUUID 调用探针（不改弱随机、不伪造生产结果） */
async function readUuidProbe(page: Page): Promise<{
  calls: number;
  ids: string[];
}> {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __p13f2UuidProbe?: { calls: number; ids: string[] };
    };
    return g.__p13f2UuidProbe ?? { calls: -1, ids: [] as string[] };
  });
}

/**
 * 用途：在应用加载前包装原生 crypto.randomUUID，记录调用次数与返回 ID。
 * 规则：保留原始 this 与返回值；禁止弱随机或伪造生产结果。
 */
async function installUuidCallProbe(page: Page) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p13f2UuidProbe?: { calls: number; ids: string[] };
    };
    g.__p13f2UuidProbe = { calls: 0, ids: [] };
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
          g.__p13f2UuidProbe!.calls += 1;
          g.__p13f2UuidProbe!.ids.push(id);
          return id;
        },
      });
    } catch {
      /* 探针安装失败由测试断言 calls===-1 暴露 */
    }
  });
}

async function openAuthedTech(
  page: Page,
  state: ProbeState,
  projectId: string,
  projectName: string,
) {
  await installRoutes(page, state);
  await openTech(page, projectId);
  await loginViaUi(page);
  await expectTechReady(page, projectName);
}

async function openAuthedBiz(
  page: Page,
  state: ProbeState,
  projectId: string,
  projectName: string,
) {
  await installRoutes(page, state);
  await openBiz(page, projectId);
  await loginViaUi(page);
  await expectBizReady(page, projectName);
}

test.describe("P13-F2 项目近期成员前端", () => {
  test("技术标：固定 testid、初次 heartbeat、精确 body/CSRF、安全成员展示", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    const consoleLines = collectConsole(page);
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");

    const panel = page.getByTestId(TECH_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();

    // StrictMode 稳定门：首次满足后再观察完整窗口
    await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);

    const hb = heartbeats(state, TECH_A)[0];
    expect(hb.path).toBe(`/api/projects/${TECH_A}/presence/heartbeat`);
    assertExactClientBody(hb);
    assertRequiredWriteAuth(hb);
    expect(String(hb.body?.clientId)).toMatch(CLIENT_ID_BACKEND_RE);

    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible();
    await expect(panel.getByText(OTHER_USERNAME, { exact: true })).toBeVisible();
    await expect(panel.getByText(TRUNCATED_TEXT)).toHaveCount(0);
    await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [String(hb.body?.clientId ?? "")],
      consoleLines,
      state.externalHits,
      panelText,
    );
    expect(state.forbiddenHits).toEqual([]);
  });

  test("商务标：固定 testid、初次 heartbeat、精确 body/CSRF、安全成员展示", async ({
    page,
  }) => {
    const project = makeProject({
      id: BIZ_A,
      name: "P13F2商务甲",
      kind: "business",
    });
    const state = createProbeState([project]);
    const consoleLines = collectConsole(page);
    await openAuthedBiz(page, state, BIZ_A, "P13F2商务甲");

    const panel = page.getByTestId(BIZ_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();

    await waitStableHeartbeatCount(state, BIZ_A, 1, STRICT_MODE_STABLE_MS);

    const hb = heartbeats(state, BIZ_A)[0];
    expect(hb.path).toBe(`/api/projects/${BIZ_A}/presence/heartbeat`);
    assertExactClientBody(hb);
    assertRequiredWriteAuth(hb);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible();

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [String(hb.body?.clientId ?? "")],
      consoleLines,
      state.externalHits,
      panelText,
    );
  });

  test("成功后 15 秒续租；慢 heartbeat 不并发", async ({ page }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    await page.clock.install();
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");

    // 推进零延迟首跳（StrictMode 可取消调度）
    await page.clock.fastForward(1);
    await waitPresenceAtLeast(state, 1, 10_000);
    await expect
      .poll(() => heartbeats(state, TECH_A).length, {
        timeout: STRICT_MODE_STABLE_MS + 2_000,
      })
      .toBe(1);

    // 成功完成后 14.9s 不应续租
    await page.clock.fastForward(REFRESH_MS - 100);
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);

    // 下一次成功完成后的 15s 续租
    await page.clock.fastForward(200);
    await waitPresenceCount(state, 2, 10_000);
    expect(heartbeats(state, TECH_A)).toHaveLength(2);
    expect(heartbeats(state, TECH_A)[0].body?.clientId).toBe(
      heartbeats(state, TECH_A)[1].body?.clientId,
    );

    // 慢请求：第三次挂起时推进时钟不得并发第四次
    const gate = createHoldGate();
    state.defaultHeartbeat = {
      kind: "gate",
      gate,
      then: "ok",
      members: defaultMembers(),
    };
    await page.clock.fastForward(REFRESH_MS);
    await waitPresenceAtLeast(state, 3, 10_000);
    const inFlight = heartbeats(state, TECH_A).length;
    expect(inFlight).toBe(3);
    await page.clock.fastForward(REFRESH_MS);
    // 仍被 gate 卡住，不能并发出第四次
    expect(heartbeats(state, TECH_A)).toHaveLength(3);
    gate.release();
    await expect
      .poll(() => heartbeats(state, TECH_A).every((h) => h.finishedAt > 0))
      .toBe(true);
    // 释放后完成当前，再等 15s 才可能第 4 次
    await page.clock.fastForward(REFRESH_MS);
    await waitPresenceAtLeast(state, 4, 10_000);
    expect(heartbeats(state, TECH_A).length).toBeGreaterThanOrEqual(4);
    // 模块级串行：后一次 startedAt 不得早于前一次 finishedAt
    const hb = heartbeats(state, TECH_A);
    for (let i = 1; i < hb.length; i += 1) {
      expect(hb[i].startedAt).toBeGreaterThanOrEqual(hb[i - 1].finishedAt);
    }
  });

  test("hidden 清空 UI 并 leave；visible 立即 heartbeat", async ({ page }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");
    const panel = page.getByTestId(TECH_TESTID);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible({
      timeout: 10_000,
    });
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);

    await setVisibility(page, "hidden");
    await waitPresenceAtLeast(state, 2, 10_000);
    const leaveHit = leaves(state, TECH_A).at(-1);
    expect(leaveHit).toBeTruthy();
    assertExactClientBody(leaveHit!);
    assertRequiredWriteAuth(leaveHit!);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toHaveCount(
      0,
    );

    const before = state.presenceLog.length;
    await setVisibility(page, "visible");
    await waitPresenceAtLeast(state, before + 1, 10_000);
    const last = state.presenceLog[state.presenceLog.length - 1];
    expect(last.op).toBe("heartbeat");
    expect(last.projectId).toBe(TECH_A);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible({
      timeout: 10_000,
    });
  });

  test("pagehide 触发 best-effort leave（keepalive=true）；hidden leave 非 pagehide", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);

    // 普通 hidden leave：生产意图 keepalive=false
    await setVisibility(page, "hidden");
    await expect
      .poll(() => leaves(state, TECH_A).length, { timeout: 10_000 })
      .toBe(1);
    const hiddenLeave = leaves(state, TECH_A)[0]!;
    assertExactClientBody(hiddenLeave);
    assertRequiredWriteAuth(hiddenLeave);
    {
      const fetchLog = await readFetchProbe(page);
      const leaveFetches = presenceFetchEntries(fetchLog, "leave", TECH_A);
      expect(leaveFetches.length).toBeGreaterThanOrEqual(1);
      const lastHidden = leaveFetches[leaveFetches.length - 1]!;
      expect(lastHidden.keepalive, "hidden leave 不得冒充 pagehide keepalive").toBe(
        false,
      );
      expect(lastHidden.body).toBe(hiddenLeave.rawBody);
    }

    // 回到 visible 再 pagehide
    await setVisibility(page, "visible");
    await expect
      .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(2);

    const beforeLeaves = leaves(state, TECH_A).length;
    const fetchBefore = (await readFetchProbe(page)).length;
    await dispatchPageHide(page);
    await expect
      .poll(() => leaves(state, TECH_A).length, { timeout: 10_000 })
      .toBe(beforeLeaves + 1);
    const leaveHit = leaves(state, TECH_A).at(-1)!;
    assertExactClientBody(leaveHit);
    assertRequiredWriteAuth(leaveHit);
    expect(leaveHit.path).toBe(`/api/projects/${TECH_A}/presence/leave`);

    const fetchLog = await readFetchProbe(page);
    const leaveFetchesAfter = presenceFetchEntries(fetchLog, "leave", TECH_A);
    expect(leaveFetchesAfter.length).toBeGreaterThan(fetchBefore > 0 ? 1 : 0);
    const pagehideLeaveFetch = leaveFetchesAfter.find(
      (e) => e.body === leaveHit.rawBody && e.keepalive === true,
    );
    expect(
      pagehideLeaveFetch,
      "pagehide leave 必须从 fetch init 证明 keepalive===true，并命中真实 body/path",
    ).toBeTruthy();
    expect(pagehideLeaveFetch!.url).toContain(
      `/api/projects/${TECH_A}/presence/leave`,
    );
    // 同时保留 route 命中证据
    expect(leaveHit.csrf).toBe(CSRF_TOKEN);
    expect(leaveHit.path).toMatch(/\/presence\/leave\/?$/);
  });

  test("A→B：真实在途 A heartbeat 迟到隔离与串行顺序", async ({ page }) => {
    const a = makeProject({ id: TECH_A, name: "P13F2技术甲", kind: "technical" });
    const b = makeProject({ id: TECH_B, name: "P13F2技术乙", kind: "technical" });
    const state = createProbeState([a, b]);
    const gateA = createHoldGate();
    state.heartbeatMode[TECH_A] = {
      kind: "ok",
      members: [
        { username: SELF_USERNAME, isSelf: true },
        { username: "仅属于甲", isSelf: false },
      ],
    };
    state.heartbeatMode[TECH_B] = {
      kind: "ok",
      members: [
        { username: SELF_USERNAME, isSelf: true },
        { username: "仅属于乙", isSelf: false },
      ],
    };

    await page.clock.install();
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");
    const panel = page.getByTestId(TECH_TESTID);
    await page.clock.fastForward(1);
    await expect(panel.getByText("仅属于甲")).toBeVisible({ timeout: 10_000 });
    await expect.poll(() => heartbeats(state, TECH_A).length).toBe(1);

    // 下一次 A 心跳挂起：先切 gate，再推进 15s 真实启动第二个 A heartbeat
    state.heartbeatMode[TECH_A] = {
      kind: "gate",
      gate: gateA,
      then: "ok",
      members: [
        { username: SELF_USERNAME, isSelf: true },
        { username: "迟到甲成员", isSelf: false },
      ],
    };
    await page.clock.fastForward(REFRESH_MS);
    await expect
      .poll(() => heartbeats(state, TECH_A).length, { timeout: 10_000 })
      .toBe(2);
    const inflightA = heartbeats(state, TECH_A)[1]!;
    expect(inflightA.finishedAt, "第二个 A heartbeat 必须仍在 gate 内").toBe(0);
    await expect
      .poll(() => gateA.waiterCount() + (gateA.hitCount() > 0 && !gateA.isReleased() ? 1 : 0))
      .toBeGreaterThanOrEqual(1);
    // hitCount 证明 route 已进入 gate.wait
    expect(gateA.hitCount()).toBeGreaterThanOrEqual(1);
    expect(gateA.isReleased()).toBe(false);

    // 切 B：切换时同步不显示旧/迟到 A
    await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
    await expectTechReady(page, "P13F2技术乙");
    await expect(panel.getByText("仅属于甲")).toHaveCount(0);
    await expect(panel.getByText("迟到甲成员")).toHaveCount(0);

    // 释放后：观察 A 完成 → leave A → heartbeat B 的真实串行
    const seqBeforeRelease = state.presenceLog.map((h) => ({
      seq: h.seq,
      op: h.op,
      projectId: h.projectId,
      finishedAt: h.finishedAt,
    }));
    void seqBeforeRelease;
    gateA.release();

    await expect
      .poll(() => {
        const log = state.presenceLog;
        const secondA = log.find(
          (h) =>
            h.op === "heartbeat" &&
            h.projectId === TECH_A &&
            h.seq === inflightA.seq,
        );
        const leaveA = log.find(
          (h) => h.op === "leave" && h.projectId === TECH_A,
        );
        const hbB = log.find(
          (h) => h.op === "heartbeat" && h.projectId === TECH_B,
        );
        if (!secondA || !leaveA || !hbB) return false;
        if (secondA.finishedAt <= 0) return false;
        // 串行：A 完成 → leave A → B heartbeat（按 seq 或 startedAt）
        return (
          secondA.seq < leaveA.seq &&
          leaveA.seq < hbB.seq &&
          secondA.finishedAt <= leaveA.startedAt &&
          leaveA.finishedAt <= hbB.startedAt
        );
      }, { timeout: 15_000 })
      .toBe(true);

    await expect(panel.getByText("仅属于乙")).toBeVisible({ timeout: 10_000 });
    await expect(panel.getByText("仅属于甲")).toHaveCount(0);
    await expect(panel.getByText("迟到甲成员")).toHaveCount(0);

    // 完整 15 秒窗口：迟到 A 不渲染、不重启 A timer（A 心跳总数保持 2）
    await page.clock.fastForward(REFRESH_MS);
    await waitStableExactCount(
      () => heartbeats(state, TECH_A).length,
      2,
      STRICT_MODE_STABLE_MS,
      10_000,
    );
    await expect(panel.getByText("迟到甲成员")).toHaveCount(0);
    await expect(panel.getByText("仅属于乙")).toBeVisible();
  });

  test("disabled 与组件级非 bid_writer harness 零 presence 请求", async ({
    page,
  }) => {
    // disabled：authRequired=false → phase=disabled，不可 eligible
    {
      const project = makeProject({
        id: TECH_A,
        name: "P13F2技术甲",
        kind: "technical",
      });
      const state = createProbeState([project]);
      state.authRequired = false;
      state.sessionAuthenticated = true;
      await installRoutes(page, state);
      await openTech(page, TECH_A);
      await expectTechReady(page, "P13F2技术甲");
      await expect(page.getByTestId(TECH_TESTID)).toHaveCount(0);
      await waitStablePresenceCount(state, 0, ZERO_REQUEST_STABLE_MS);
    }

    // 非 bid_writer：真实 AuthProvider + ProjectPresencePanel harness，panel 实际参与渲染
    {
      const project = makeProject({
        id: TECH_B,
        name: "P13F2技术乙",
        kind: "technical",
      });
      const state = createProbeState([project]);
      state.role = "finance";
      state.sessionAuthenticated = false;
      await installRoutes(page, state);
      // finance 进业务页被父路由拒绝，但 AuthProvider 仍在；再挂真实 Panel harness
      await openTech(page, TECH_B);
      await loginViaUi(page);
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("technical-editor-workspace")).toHaveCount(0);

      // 隔离 React harness：真实 AuthProvider + Panel（finance 角色 /me）
      // 优先复用页面已加载的 Vite 预构建 deps URL，避免 bare import/路径漂移
      const harnessOk = await page.evaluate(async ({ projectId, testId }) => {
        const resourceUrls = performance
          .getEntriesByType("resource")
          .map((e) => e.name);
        const pick = (pred: (u: string) => boolean): string[] => {
          const hit = resourceUrls.filter(pred);
          return hit.length > 0 ? hit : [];
        };
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

        async function loadFirst(urls: string[]): Promise<Record<string, unknown>> {
          let lastErr: unknown;
          for (const u of urls) {
            try {
              const mod = (await import(/* @vite-ignore */ u)) as Record<
                string,
                unknown
              >;
              return mod;
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
          if (typeof def === "function" && name === "default") {
            return def as (...args: never[]) => unknown;
          }
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
          "/src/features/editor-state-collaboration/ProjectPresencePanel.tsx",
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
        const ProjectPresencePanel = (panelMod.ProjectPresencePanel ??
          (panelMod.default as Record<string, unknown> | undefined)
            ?.ProjectPresencePanel) as
          | import("react").ComponentType<{
              projectId: string;
              testId: string;
            }>
          | undefined;

        if (!createElement) {
          throw new Error(`react createElement missing keys=${Object.keys(React)}`);
        }
        if (!createRoot) {
          throw new Error(
            `createRoot missing keys=${Object.keys(clientMod).join(",")}`,
          );
        }
        if (!AuthProvider || !ProjectPresencePanel) {
          throw new Error("AuthProvider/ProjectPresencePanel missing");
        }

        let host = document.getElementById("p13f2-presence-harness");
        if (!host) {
          host = document.createElement("div");
          host.id = "p13f2-presence-harness";
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
            )(ProjectPresencePanel, {
              projectId,
              testId,
            }),
          ),
        );
        return true;
      }, { projectId: TECH_B, testId: TECH_TESTID });
      expect(harnessOk, "finance presence harness 必须成功挂载").toBe(true);

      // panel 可能因 finance 返回 null（eligible 假），testid 必须隐藏
      await expect(page.getByTestId(TECH_TESTID)).toHaveCount(0, {
        timeout: 10_000,
      });
      await waitStablePresenceCount(state, 0, ZERO_REQUEST_STABLE_MS);
    }
  });

  test("parser/用户名矩阵：独立坏包固定不可用；合法边界可展示", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    const consoleLines = collectConsole(page);

    const okBase = {
      leaseExpiresAt: "2026-07-20T12:35:41",
      refreshAfterSeconds: 15 as const,
      members: defaultMembers(),
      truncated: false,
    };

    type Case = { name: string; body: unknown; expectOk?: boolean; expectText?: string };
    const astral100 = `${"😀".repeat(99)}A`; // 100 码点
    const astral101 = "😀".repeat(101); // 101 码点
    const cases: Case[] = [
      {
        name: "顶层 extra",
        body: { ...okBase, secret: SECRET_MARKER },
      },
      {
        name: "缺 leaseExpiresAt",
        body: {
          refreshAfterSeconds: 15,
          members: defaultMembers(),
          truncated: false,
        },
      },
      {
        name: "lease 空串",
        body: { ...okBase, leaseExpiresAt: "" },
      },
      {
        name: "refresh 非整数",
        body: { ...okBase, refreshAfterSeconds: 15.5 },
      },
      {
        name: "refresh 非 15",
        body: { ...okBase, refreshAfterSeconds: 30 },
      },
      {
        name: "truncated 非 boolean",
        body: { ...okBase, truncated: "false" },
      },
      {
        name: "members 非数组",
        body: { ...okBase, members: { username: SELF_USERNAME, isSelf: true } },
      },
      {
        name: "members >50",
        body: {
          ...okBase,
          members: [
            { username: SELF_USERNAME, isSelf: true },
            ...Array.from({ length: 50 }, (_, i) => ({
              username: `peer_${i}`,
              isSelf: false,
            })),
          ],
        },
      },
      {
        name: "成员 extra",
        body: {
          ...okBase,
          members: [
            { username: SELF_USERNAME, isSelf: true, userId: "u_leak" },
            { username: OTHER_USERNAME, isSelf: false },
          ],
        },
      },
      {
        name: "成员缺 isSelf",
        body: {
          ...okBase,
          members: [{ username: SELF_USERNAME }],
        },
      },
      {
        name: "成员 isSelf 坏类型",
        body: {
          ...okBase,
          members: [{ username: SELF_USERNAME, isSelf: "true" }],
        },
      },
      {
        name: "0 self",
        body: {
          ...okBase,
          members: [{ username: OTHER_USERNAME, isSelf: false }],
        },
      },
      {
        name: "2 self",
        body: {
          ...okBase,
          members: [
            { username: SELF_USERNAME, isSelf: true },
            { username: "另一自我", isSelf: true },
          ],
        },
      },
      { name: "username 空", body: { ...okBase, members: [{ username: "", isSelf: true }] } },
      {
        name: "username 101 码点",
        body: {
          ...okBase,
          members: [{ username: "a".repeat(101), isSelf: true }],
        },
      },
      {
        name: "username 首尾空白",
        body: {
          ...okBase,
          members: [{ username: "  spaced  ", isSelf: true }],
        },
      },
      {
        name: "username C0",
        body: {
          ...okBase,
          members: [{ username: "bad\u0001name", isSelf: true }],
        },
      },
      {
        name: "username C1",
        body: {
          ...okBase,
          members: [{ username: "bad\u0081name", isSelf: true }],
        },
      },
      {
        name: "username DEL",
        body: {
          ...okBase,
          members: [{ username: "bad\u007fname", isSelf: true }],
        },
      },
      {
        name: "username U+2028",
        body: {
          ...okBase,
          members: [{ username: "bad\u2028name", isSelf: true }],
        },
      },
      {
        name: "username U+2029",
        body: {
          ...okBase,
          members: [{ username: "bad\u2029name", isSelf: true }],
        },
      },
      {
        name: "username 双向 LRE",
        body: {
          ...okBase,
          members: [{ username: "bad\u202aname", isSelf: true }],
        },
      },
      {
        name: "username 双向 RLO",
        body: {
          ...okBase,
          members: [{ username: "bad\u202ename", isSelf: true }],
        },
      },
      {
        name: "username 双向 RLI",
        body: {
          ...okBase,
          members: [{ username: "bad\u2067name", isSelf: true }],
        },
      },
      {
        name: "astral 101 非法",
        body: {
          ...okBase,
          members: [{ username: astral101, isSelf: true }],
        },
      },
      // 合法边界
      {
        name: "astral 100 合法",
        body: {
          ...okBase,
          members: [{ username: astral100, isSelf: true }],
        },
        expectOk: true,
        expectText: astral100,
      },
    ];

    // 首包用坏响应打开，建立 clientId 与 panel
    state.defaultHeartbeat = { kind: "bad", body: cases[0]!.body };
    await openAuthedTech(page, state, TECH_A, "P13F2技术甲");
    const panel = page.getByTestId(TECH_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await waitPresenceAtLeast(state, 1, 10_000);
    const clientId = String(heartbeats(state, TECH_A)[0]?.body?.clientId ?? "");
    expect(clientId).toMatch(CLIENT_ID_RE);

    for (let i = 0; i < cases.length; i += 1) {
      const c = cases[i]!;
      state.defaultHeartbeat = { kind: "bad", body: c.body };
      // 每个坏包经独立 heartbeat 响应证明（hidden→visible 触发）
      await setVisibility(page, "hidden");
      await expect
        .poll(() => leaves(state, TECH_A).length)
        .toBeGreaterThanOrEqual(i + 1);
      const before = state.presenceLog.length;
      await setVisibility(page, "visible");
      await waitPresenceAtLeast(state, before + 1, 10_000);

      if (c.expectOk) {
        await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);
        // 自身成员文本节点为 username + （我），不能对裸 username 做 exact
        await expect(
          panel.getByText(`${c.expectText ?? ""}${SELF_SUFFIX}`),
        ).toBeVisible({ timeout: 10_000 });
        await expect(panel.locator("li")).toHaveCount(1);
      } else {
        await expect(panel.getByText(UNAVAILABLE_TEXT)).toBeVisible({
          timeout: 10_000,
        });
        // 零部分成员
        await expect(panel.locator("li")).toHaveCount(0);
        await expect(panel.getByText(OTHER_USERNAME)).toHaveCount(0);
      }
    }

    // truncated 正常路径
    state.defaultHeartbeat = {
      kind: "ok",
      members: defaultMembers(),
      truncated: true,
    };
    await setVisibility(page, "hidden");
    await setVisibility(page, "visible");
    await expect(panel.getByText(TRUNCATED_TEXT)).toBeVisible({
      timeout: 10_000,
    });
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible();

    const panelText = (await panel.innerText()).trim();
    await assertPrivacyClosed(
      page,
      [clientId],
      consoleLines,
      state.externalHits,
      panelText,
    );
    expect(panelText).not.toContain("presence_heartbeat_failed");
    expect(panelText).not.toContain(TECH_A);
    expect(panelText).not.toContain("/api/projects");
  });

  test("clientId 异常：缺失/抛错/非法格式 → 固定不可用且零写", async ({
    browser,
  }) => {
    // 非法格式必须在旧实现上真实失败（仅长度门会放行）
    const modes: Array<{
      name: string;
      install: (page: Page) => Promise<void>;
    }> = [
      {
        name: "missing",
        install: async (p) => {
          await p.addInitScript(() => {
            try {
              Object.defineProperty(crypto, "randomUUID", {
                configurable: true,
                value: undefined,
              });
            } catch {
              /* ignore */
            }
          });
        },
      },
      {
        name: "throw",
        install: async (p) => {
          await p.addInitScript((marker) => {
            Object.defineProperty(crypto, "randomUUID", {
              configurable: true,
              value() {
                throw new Error(`uuid_boom_${marker}`);
              },
            });
          }, SECRET_MARKER);
        },
      },
      {
        name: "illegal-format",
        // 36 位但含非法 '!'，旧实现只查长度会放行
        install: async (p) => {
          await p.addInitScript(() => {
            Object.defineProperty(crypto, "randomUUID", {
              configurable: true,
              value() {
                return "!!!!!!!!-!!!!-!!!!-!!!!-!!!!!!!!!!!!";
              },
            });
          });
        },
      },
    ];

    for (const mode of modes) {
      await test.step(mode.name, async () => {
        const context = await browser.newContext();
        const page = await context.newPage();
        try {
          const project = makeProject({
            id: `${TECH_A}_${mode.name}`,
            name: `P13F2技术-${mode.name}`,
            kind: "technical",
          });
          const state = createProbeState([project]);
          const consoleLines = collectConsole(page);
          await mode.install(page);
          await installRoutes(page, state);
          await openTech(page, project.id);
          await loginViaUi(page);
          await expectTechReady(page, project.name);

          const panel = page.getByTestId(TECH_TESTID);
          await expect(panel).toBeVisible({ timeout: 10_000 });
          await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();
          await expect(panel.getByText(UNAVAILABLE_TEXT)).toBeVisible({
            timeout: 10_000,
          });
          await expect(panel.getByText(LOADING_TEXT)).toHaveCount(0);
          await expect(panel.locator("li")).toHaveCount(0);

          await waitStablePresenceCount(state, 0, ZERO_REQUEST_STABLE_MS);

          const badId = "!!!!!!!!-!!!!-!!!!-!!!!-!!!!!!!!!!!!";
          const boom = `uuid_boom_${SECRET_MARKER}`;
          const surfaces = await readLeakSurfaces(page);
          const panelText = (await panel.innerText()).trim();
          // 原始 console 先扫实际敏感值；隐私 blob 纳入 fetchBodies
          assertConsoleNoSensitiveLeak(consoleLines, [badId, boom]);
          for (const blob of [
            surfaces.html,
            surfaces.text,
            surfaces.href,
            surfaces.local.join("\n"),
            surfaces.session.join("\n"),
            surfaces.idbKeys.join("\n"),
            surfaces.idbWrites.join("\n"),
            surfaces.cookie,
            surfaces.fetchBodies.join("\n"),
            panelText,
            consoleLines.join("\n"),
            state.externalHits.join("\n"),
          ]) {
            expect(blob).not.toContain(badId);
            expect(blob).not.toContain(boom);
            expect(blob).not.toContain(SECRET_MARKER);
          }
          expect(state.forbiddenHits, "异常 clientId 不得触发未授权 API").toEqual(
            [],
          );
          expect(state.presenceLog, "异常 clientId 零 presence 写").toHaveLength(
            0,
          );
          const clip = await readClipboardProbe(page);
          expect(clip.installed).toBe(true);
          expect(clip.read).toBe(0);
          expect(clip.write).toBe(0);
        } finally {
          await context.close();
        }
      });
    }
  });

  test("console 隐私门自校：含真实 UUID 的资源失败行必须拒绝", () => {
    // 构造：含真实 UUID、不含字面量 clientId/SECRET_MARKER 的资源失败行
    const realUuid = "a1b2c3d4-e5f6-4789-a012-3456789abcde";
    const noisyLine =
      `error: Failed to load resource: net::ERR_FAILED ` +
      `https://cdn.example.invalid/asset/${realUuid}.js`;
    expect(noisyLine.includes(SECRET_MARKER)).toBe(false);
    expect(/clientId/i.test(noisyLine)).toBe(false);
    expect(noisyLine).toContain(realUuid);

    // 旧过滤器若只靠字面量 clientId 会错误丢弃该行
    const wronglyDropped = noisyLine.includes(SECRET_MARKER)
      ? [noisyLine]
      : /clientId/i.test(noisyLine)
        ? [noisyLine]
        : /^(error|warning): Failed to load resource:/.test(noisyLine)
          ? []
          : [noisyLine];
    expect(wronglyDropped, "字面量 clientId 门会误丢资源失败行").toEqual([]);

    // 正确门：原始行扫描真实 UUID 必须拒绝
    expect(() => {
      assertConsoleNoSensitiveLeak([noisyLine], [realUuid]);
    }).toThrow(/clientId 经 console 泄漏/);

    // 新 appConsoleLines 在已知 clientIds 时不得吞掉该行
    expect(appConsoleLines([noisyLine], [realUuid])).toEqual([noisyLine]);

    // assertNoSensitiveLeak 全路径也必须拒绝
    const emptySurfaces = {
      html: "",
      text: "",
      href: "http://127.0.0.1/",
      local: [] as string[],
      session: [] as string[],
      idbKeys: [] as string[],
      idbWrites: [] as string[],
      fetchBodies: [] as string[],
      cookie: "",
    };
    expect(() => {
      assertNoSensitiveLeak(
        emptySurfaces,
        [realUuid],
        [noisyLine],
        [],
        TITLE_TEXT,
      );
    }).toThrow(/clientId 经 console 泄漏/);
  });

  test("初始 hidden：ready 后仅标题/空成员区；visible 后唯一 heartbeat 与延迟 clientId", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13F2技术甲",
      kind: "technical",
    });
    const state = createProbeState([project]);
    // 应用加载前：固定 hidden + 包装原生 randomUUID（保留 this/返回值）
    await page.addInitScript(() => {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        get: () =>
          (globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis ??
          "hidden",
      });
      Object.defineProperty(document, "hidden", {
        configurable: true,
        get: () =>
          ((globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis ??
            "hidden") === "hidden",
      });
      (globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis = "hidden";
    });
    await installUuidCallProbe(page);

    await installRoutes(page, state);
    await openTech(page, TECH_A);
    await loginViaUi(page);
    await expectTechReady(page, "P13F2技术甲");

    const panel = page.getByTestId(TECH_TESTID);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel.getByText(TITLE_TEXT, { exact: true })).toBeVisible();
    // 不得显示 loading / unavailable / 成员
    await expect(panel.getByText(LOADING_TEXT)).toHaveCount(0);
    await expect(panel.getByText(UNAVAILABLE_TEXT)).toHaveCount(0);
    await expect(panel.locator("li")).toHaveCount(0);
    await waitStablePresenceCount(state, 0, ZERO_REQUEST_STABLE_MS);

    // hidden ready + 完整稳定窗口：randomUUID 精确 0、presence 0、UI cleared
    const probeHidden = await readUuidProbe(page);
    expect(probeHidden.calls, "初始 hidden 不得生成 clientId").toBe(0);
    expect(probeHidden.ids).toEqual([]);
    expect(state.presenceLog).toHaveLength(0);

    // 触发 visible：立即唯一 heartbeat 并展示成员
    await page.evaluate(() => {
      (globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitStableHeartbeatCount(state, TECH_A, 1, STRICT_MODE_STABLE_MS);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible({
      timeout: 10_000,
    });
    expect(leaves(state, TECH_A)).toHaveLength(0);

    const probeVisible = await readUuidProbe(page);
    expect(probeVisible.calls, "首 visible 仅生成一次 clientId").toBe(1);
    expect(probeVisible.ids).toHaveLength(1);
    const generatedId = probeVisible.ids[0];
    expect(generatedId).toMatch(CLIENT_ID_RE);
    const hb1 = heartbeats(state, TECH_A)[0];
    assertExactClientBody(hb1);
    expect(hb1.body?.clientId).toBe(generatedId);

    // 再完成一次 hidden→visible：randomUUID 仍精确 1，body 复用同一 ID
    await page.evaluate(() => {
      (globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect
      .poll(() => leaves(state, TECH_A).length, { timeout: 10_000 })
      .toBe(1);
    await expect(panel.locator("li")).toHaveCount(0);

    await page.evaluate(() => {
      (globalThis as unknown as { __p13f2Vis?: string }).__p13f2Vis = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitStableHeartbeatCount(state, TECH_A, 2, STRICT_MODE_STABLE_MS);
    await expect(panel.getByText(`${SELF_USERNAME}${SELF_SUFFIX}`)).toBeVisible({
      timeout: 10_000,
    });

    const probeReuse = await readUuidProbe(page);
    expect(probeReuse.calls, "文档级缓存不得再次 randomUUID").toBe(1);
    expect(probeReuse.ids).toEqual([generatedId]);
    const hb2 = heartbeats(state, TECH_A)[1];
    assertExactClientBody(hb2);
    expect(hb2.body?.clientId).toBe(generatedId);
  });
});
