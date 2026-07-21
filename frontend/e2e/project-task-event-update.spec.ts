/**
 * 模块：P13-I3 项目任务事件前端提示专项 E2E
 * 用途：验证技术/商务工作区任务事件 EventSource——认证/角色门控、cursor 不提示、
 *       合法 task-event 六键与展示、重复键/坏帧/控制帧/网络错误固定不可用、
 *       A→B 迟到隔离、无任务详情请求、隐私边界与 withCredentials。
 * 对接：Playwright chromium 单 worker；同源路由桩 + 本地可挂起 SSE 服务器；
 *       固定 testid technical-project-task-event-update / business-project-task-event-update。
 * 二次开发：禁止源码字符串假绿、宽泛非零计数、sleep 作完成证据；帧必须由 route/SSE
 *       真实发出；串行 --workers=1 --retries=0。
 */
import http from "node:http";
import type { AddressInfo } from "node:net";
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p13i3_tech_a";
const TECH_B = "proj_e2e_p13i3_tech_b";
const BIZ_A = "proj_e2e_p13i3_biz_a";
const BIZ_B = "proj_e2e_p13i3_biz_b";

const TECH_TESTID = "technical-project-task-event-update";
const BIZ_TESTID = "business-project-task-event-update";

const UNAVAILABLE_TEXT = "项目任务提示暂不可用";
const OTHER_TASK_LABEL = "其他任务";

const CSRF_TOKEN = "e2e-p13i3-csrf-token-memory";
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p13i3_sess_opaque";
const E2E_LOGIN_USER = "e2e_p13i3_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const SECRET_MARKER = "SECRET_P13I3_LEAK_MARKER_xyz";

const ZERO_STREAM_STABLE_MS = 400;
/** StrictMode 下项目会话 useEffect 双调用：进入工作区 editor-state 精确 +2 GET */
const PROJECT_SESSION_GETS = 2;

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "进行中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
};

const TYPE_LABEL: Record<string, string> = {
  parse: "解析",
  analyze: "分析",
  outline: "大纲",
  chapter: "章节",
  chapters: "批量章节",
  export: "导出",
  response_match: "响应匹配",
  content_fuse: "内容融合",
  biz_qualify: "资格审查",
  biz_toc: "商务目录",
  biz_quote: "报价",
  biz_commit: "商务承诺",
};

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

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  waiterCount: () => number;
};

type StreamHit = {
  seq: number;
  projectId: string;
  path: string;
  method: string;
  search: string;
  cookie: string;
  headers: Record<string, string>;
  startedAt: number;
  opened: boolean;
};

type StreamFramePlan =
  | { kind: "sse"; frames: string[]; holdOpen?: boolean }
  | { kind: "gate"; gate: HoldGate; frames: string[]; holdOpen?: boolean }
  | { kind: "abort" }
  | { kind: "http"; status: number; body?: string }
  | {
      kind: "live";
      initial?: string[];
      onOpen?: (conn: LiveSseConn) => void;
    };

type LiveSseConn = {
  projectId: string;
  write: (chunk: string) => void;
  end: () => void;
  closed: () => boolean;
};

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  authRequired: boolean;
  sessionAuthenticated: boolean;
  role: AuthRole;
  csrfToken: string;
  versionSeq: number;
  eventSeq: number;
  taskSeq: number;
  getLog: string[];
  putLog: string[];
  taskDetailLog: string[];
  streamLog: StreamHit[];
  streamMode: Record<string, StreamFramePlan>;
  defaultStreamMode: StreamFramePlan;
  forbiddenHits: string[];
  externalHits: string[];
  liveConns: LiveSseConn[];
};

type EsCtorLog = {
  url: string;
  withCredentials: boolean | undefined;
  at: number;
};

function seedEventId(n: number): string {
  return `pte_${n.toString(16).padStart(32, "0")}`;
}

function seedTaskId(n: number): string {
  return `task_${n.toString(16).padStart(16, "0")}`;
}

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function allocateEventId(state: ProbeState): string {
  state.eventSeq += 1;
  return seedEventId(state.eventSeq);
}

function allocateTaskId(state: ProbeState): string {
  state.taskSeq += 1;
  return seedTaskId(state.taskSeq);
}

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
        waiters.shift()?.();
      }
    },
    isReleased: () => released,
    waiterCount: () => waiters.length,
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

function formatSseNamed(
  id: string,
  event: string,
  data: Record<string, unknown>,
): string {
  return `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** 手写 data 原文（用于重复键等 JSON.parse 会折叠的帧） */
function formatSseRawData(id: string, event: string, dataRaw: string): string {
  return `id: ${id}\nevent: ${event}\ndata: ${dataRaw}\n\n`;
}

function formatSseControl(
  event: "cursor-stale" | "unavailable",
  data: Record<string, unknown>,
): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function cursorFrame(eventId: string): string {
  return formatSseNamed(eventId, "cursor", { eventId });
}

function taskEventFrame(opts: {
  eventId: string;
  taskId: string;
  taskType?: string;
  status?: string;
  progress?: number;
  occurredAt?: string;
}): string {
  return formatSseNamed(opts.eventId, "task-event", {
    eventId: opts.eventId,
    taskId: opts.taskId,
    taskType: opts.taskType ?? "parse",
    status: opts.status ?? "running",
    progress: opts.progress ?? 50,
    occurredAt: opts.occurredAt ?? "2026-07-21T12:34:56.000Z",
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
    { methods: ["GET"], rest: /^\/editor-state-events\/stream\/?$/ },
    { methods: ["GET"], rest: /^\/task-events\/stream\/?$/ },
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
      rest: /^\/chapter-edit-intents\/(heartbeat|leave)\/?$/,
    },
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
    updatedAt: partial.updatedAt ?? "2026-07-21T12:00:00",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: partial.kind,
  };
}

function emptyEditor(projectId: string, kind: Kind, stateVersion: string): EditorState {
  return {
    projectId,
    outline: [],
    chapters: [],
    facts: [],
    mode: kind === "technical" ? "analysis" : "business",
    analysisOverview: "P13I3 概述",
    analysis: {
      overview: "P13I3 概述",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: kind === "business" ? "P13I3 商务正文" : "",
    guidance: null,
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion,
    updatedAt: "2026-07-21T12:34:56",
    currentRevisionSourceKind: "browser_put",
    currentRevisionActorUsername: E2E_LOGIN_USER,
  };
}

function createProbeState(projects: ProjectStub[]): ProbeState {
  const editorById: Record<string, EditorState> = {};
  let versionSeq = 0;
  for (const p of projects) {
    versionSeq += 1;
    editorById[p.id] = emptyEditor(p.id, p.kind, seedStateVersion(versionSeq));
  }
  return {
    projects,
    editorById,
    authRequired: true,
    sessionAuthenticated: false,
    role: "bid_writer",
    csrfToken: CSRF_TOKEN,
    versionSeq,
    eventSeq: 0,
    taskSeq: 0,
    getLog: [],
    putLog: [],
    taskDetailLog: [],
    streamLog: [],
    streamMode: {},
    defaultStreamMode: { kind: "live", initial: [] },
    forbiddenHits: [],
    externalHits: [],
    liveConns: [],
  };
}

function workspaceForRole(role: AuthRole) {
  return {
    id: "ws_e2e",
    name: "E2E 工作空间",
    role,
    isOwner: role === "bid_writer",
  };
}

type SseMockServer = {
  port: number;
  close: () => Promise<void>;
  writeToProject: (projectId: string, chunk: string) => number;
  endProject: (projectId: string) => void;
  activeCount: (projectId?: string) => number;
};

/**
 * 用途：可挂起真实 SSE HTTP 服务；同时承接 task-events 与 editor-state 流，
 *       避免 H3 面板因 404 干扰 I3 断言。
 */
async function startSseMockServer(state: ProbeState): Promise<SseMockServer> {
  type Conn = {
    projectId: string;
    kind: "task" | "editor";
    res: http.ServerResponse;
    closed: boolean;
  };
  const conns: Conn[] = [];

  const server = http.createServer(async (req, res) => {
    try {
      const host = req.headers.host ?? "127.0.0.1";
      const url = new URL(req.url ?? "/", `http://${host}`);
      const taskMatch = url.pathname.match(
        /^\/api\/projects\/([^/]+)\/task-events\/stream\/?$/,
      );
      const editorMatch = url.pathname.match(
        /^\/api\/projects\/([^/]+)\/editor-state-events\/stream\/?$/,
      );
      if ((!taskMatch && !editorMatch) || (req.method ?? "GET").toUpperCase() !== "GET") {
        res.statusCode = 404;
        res.end("not found");
        return;
      }
      const projectId = decodeURIComponent((taskMatch ?? editorMatch)![1]);
      const streamKind: "task" | "editor" = taskMatch ? "task" : "editor";

      // editor-state 流仅挂起，避免 H3 干扰；业务帧只走 task 流
      const plan =
        streamKind === "task"
          ? (state.streamMode[projectId] ?? state.defaultStreamMode)
          : ({ kind: "live", initial: [] } as StreamFramePlan);

      if (plan.kind === "abort") {
        req.socket.destroy();
        return;
      }
      if (plan.kind === "http") {
        res.statusCode = plan.status;
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        res.end(plan.body ?? JSON.stringify({ detail: { message: SECRET_MARKER } }));
        return;
      }

      if (plan.kind === "gate") {
        await plan.gate.wait();
      }

      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-store",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      });

      const conn: Conn = { projectId, kind: streamKind, res, closed: false };
      conns.push(conn);
      req.on("close", () => {
        conn.closed = true;
      });
      res.on("close", () => {
        conn.closed = true;
      });

      const writeFrames = (frames: string[]) => {
        for (const f of frames) {
          if (conn.closed) return;
          res.write(f);
        }
      };

      if (plan.kind === "sse" || plan.kind === "gate") {
        writeFrames(plan.frames);
        const hold = plan.holdOpen !== false;
        if (!hold) {
          res.end();
          conn.closed = true;
        }
        return;
      }

      if (plan.initial?.length) writeFrames(plan.initial);
      const live: LiveSseConn = {
        projectId,
        write: (chunk: string) => {
          if (conn.closed) return;
          res.write(chunk);
        },
        end: () => {
          if (conn.closed) return;
          res.end();
          conn.closed = true;
        },
        closed: () => conn.closed,
      };
      if (streamKind === "task") {
        state.liveConns.push(live);
        plan.onOpen?.(live);
      }
    } catch {
      try {
        res.statusCode = 500;
        res.end("");
      } catch {
        /* ignore */
      }
    }
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const port = (server.address() as AddressInfo).port;

  return {
    port,
    close: () =>
      new Promise<void>((resolve, reject) => {
        for (const c of conns) {
          try {
            if (!c.closed) c.res.end();
          } catch {
            /* ignore */
          }
        }
        server.close((err) => (err ? reject(err) : resolve()));
      }),
    writeToProject: (projectId, chunk) => {
      let n = 0;
      for (const c of conns) {
        if (c.projectId === projectId && c.kind === "task" && !c.closed) {
          c.res.write(chunk);
          n += 1;
        }
      }
      return n;
    },
    endProject: (projectId) => {
      for (const c of conns) {
        if (c.projectId === projectId && c.kind === "task" && !c.closed) {
          c.res.end();
          c.closed = true;
        }
      }
    },
    activeCount: (projectId) =>
      conns.filter(
        (c) =>
          !c.closed &&
          c.kind === "task" &&
          (projectId === undefined || c.projectId === projectId),
      ).length,
  };
}

async function installRoutes(
  page: Page,
  state: ProbeState,
  sse: SseMockServer,
) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p13i3EsLog?: Array<{
        url: string;
        withCredentials: boolean | undefined;
        at: number;
      }>;
      __p13i3EsCloseCount?: number;
    };
    g.__p13i3EsLog = [];
    g.__p13i3EsCloseCount = 0;
    const Original = window.EventSource;
    const Wrapped = function (
      this: EventSource,
      url: string | URL,
      eventSourceInitDict?: EventSourceInit,
    ): EventSource {
      const href = typeof url === "string" ? url : url.toString();
      g.__p13i3EsLog!.push({
        url: href,
        withCredentials: eventSourceInitDict?.withCredentials,
        at: Date.now(),
      });
      const inst = new Original(url, eventSourceInitDict);
      const origClose = inst.close.bind(inst);
      inst.close = () => {
        g.__p13i3EsCloseCount = (g.__p13i3EsCloseCount ?? 0) + 1;
        origClose();
      };
      return inst;
    } as unknown as typeof EventSource;
    Wrapped.prototype = Original.prototype;
    Object.defineProperty(Wrapped, "CONNECTING", { value: Original.CONNECTING });
    Object.defineProperty(Wrapped, "OPEN", { value: Original.OPEN });
    Object.defineProperty(Wrapped, "CLOSED", { value: Original.CLOSED });
    (window as unknown as { EventSource: typeof EventSource }).EventSource =
      Wrapped;
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

    const known = knownProjectIdSet(state);

    // 任务事件 SSE
    const taskStreamMatch = path.match(
      /^\/api\/projects\/([^/]+)\/task-events\/stream\/?$/,
    );
    if (taskStreamMatch && method === "GET") {
      const projectId = decodeURIComponent(taskStreamMatch[1]);
      state.streamLog.push({
        seq: state.streamLog.length + 1,
        projectId,
        path,
        method,
        search: url.search,
        cookie: req.headers()["cookie"] || "",
        headers: { ...req.headers() },
        startedAt: Date.now(),
        opened: true,
      });
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/task-events/stream`;
      await route.continue({ url: target });
      return;
    }

    // 编辑态 SSE：代理到 mock，避免 H3 干扰
    const editorStreamMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-events\/stream\/?$/,
    );
    if (editorStreamMatch && method === "GET") {
      const projectId = decodeURIComponent(editorStreamMatch[1]);
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/editor-state-events/stream`;
      await route.continue({ url: target });
      return;
    }

    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p13i3_forbidden", message: SECRET_MARKER } },
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
        await json(route, { detail: { code: "p13i3_no_create" } }, 403);
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
        state.getLog.push(id);
        const body = {
          ...(state.editorById[id] ??
            emptyEditor(id, "technical", seedStateVersion(1))),
        };
        await json(route, body);
        return;
      }
      state.putLog.push(id);
      await json(route, state.editorById[id] ?? emptyEditor(id, "technical", seedStateVersion(1)));
      return;
    }

    // 任务详情/列表：记账，证明 I3 不得自动拉取
    const taskMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks(?:\/([^/]+))?(?:\/(events|cancel))?\/?$/,
    );
    if (taskMatch && (method === "GET" || method === "POST")) {
      const pid = decodeURIComponent(taskMatch[1]);
      const tid = taskMatch[2] ? decodeURIComponent(taskMatch[2]) : "";
      state.taskDetailLog.push(`${method} ${pid} ${tid || "_list"}`);
      if (method === "GET" && tid) {
        await json(route, {
          id: tid,
          status: "running",
          type: "parse",
          progress: 0,
          message: SECRET_MARKER,
          error: SECRET_MARKER,
          result: { leak: SECRET_MARKER },
        });
        return;
      }
      if (method === "GET") {
        await json(route, []);
        return;
      }
      await json(route, { id: "task_e2e", status: "queued", type: "noop" });
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
          leaseExpiresAt: "2026-07-21T12:35:41",
          refreshAfterSeconds: 15,
        });
        return;
      }
      await json(route, {
        leaseExpiresAt: "2026-07-21T12:35:41",
        refreshAfterSeconds: 15,
        members: [{ username: E2E_LOGIN_USER, isSelf: true }],
        truncated: false,
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
    await json(route, { detail: { code: "p13i3_unhandled" } }, 404);
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

function appConsoleLines(lines: string[]): string[] {
  return lines.filter((line) => {
    if (line.includes(SECRET_MARKER)) return true;
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

async function waitStableExactCount(
  getCount: () => number,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
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
}

async function readEsLog(page: Page): Promise<EsCtorLog[]> {
  return page.evaluate(() => {
    const g = globalThis as unknown as { __p13i3EsLog?: EsCtorLog[] };
    return g.__p13i3EsLog ?? [];
  });
}

async function readEsCloseCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const g = globalThis as unknown as { __p13i3EsCloseCount?: number };
    return g.__p13i3EsCloseCount ?? 0;
  });
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

function streamsFor(state: ProbeState, projectId?: string): StreamHit[] {
  return state.streamLog.filter(
    (h) => projectId === undefined || h.projectId === projectId,
  );
}

function getsFor(state: ProbeState, projectId?: string): string[] {
  return state.getLog.filter((id) => projectId === undefined || id === projectId);
}

async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
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
    return {
      html,
      text,
      href,
      local,
      session,
      cookie: document.cookie,
    };
  });
}

function assertNoSecretOrIds(
  surfaces: Awaited<ReturnType<typeof readLeakSurfaces>>,
  panelText: string,
  extraForbidden: string[],
) {
  const blobs = [
    surfaces.html,
    surfaces.text,
    surfaces.href,
    surfaces.local.join("\n"),
    surfaces.session.join("\n"),
    surfaces.cookie,
    panelText,
  ];
  for (const blob of blobs) {
    expect(blob, "secret marker 泄漏").not.toContain(SECRET_MARKER);
    for (const f of extraForbidden) {
      if (!f) continue;
      expect(blob, `禁止出口: ${f}`).not.toContain(f);
    }
  }
}

async function setupPage(
  page: Page,
  state: ProbeState,
): Promise<{ sse: SseMockServer; consoleLines: string[] }> {
  const sse = await startSseMockServer(state);
  const consoleLines = collectConsole(page);
  await installRoutes(page, state, sse);
  return { sse, consoleLines };
}

test.describe.configure({ mode: "serial" });

test.describe("P13-I3 项目任务事件前端提示", () => {
  test("门控：disabled / 未认证 / 非 bid_writer 零 task-events 流", async ({
    page,
  }) => {
    // 1) disabled
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.authRequired = false;
      state.sessionAuthenticated = true;
      state.role = "bid_writer";
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await expectTechReady(page, "技术甲");
        await waitStableExactCount(
          () => streamsFor(state, TECH_A).length,
          0,
          ZERO_STREAM_STABLE_MS,
        );
        const taskEs = (await readEsLog(page)).filter((e) =>
          e.url.includes("/task-events/stream"),
        );
        expect(taskEs).toEqual([]);
        expect(await page.getByTestId(TECH_TESTID).count()).toBe(0);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 2) required 未登录
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.authRequired = true;
      state.sessionAuthenticated = false;
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
          timeout: 20_000,
        });
        await waitStableExactCount(
          () => streamsFor(state).length,
          0,
          ZERO_STREAM_STABLE_MS,
        );
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 3) finance 角色零流
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.role = "finance";
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await waitStableExactCount(
          () => streamsFor(state).length,
          0,
          ZERO_STREAM_STABLE_MS,
        );
        expect(await page.getByTestId(TECH_TESTID).count()).toBe(0);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }
  });

  test("技术标：首次 cursor 不提示；合法 task-event 展示类型/状态/进度；重复事件无副作用", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
    ]);
    const cursorId = allocateEventId(state);
    const eventId = allocateEventId(state);
    const taskId = allocateTaskId(state);

    let live: LiveSseConn | null = null;
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(cursorId)],
      onOpen: (c) => {
        live = c;
      },
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");

      await expect
        .poll(() => streamsFor(state, TECH_A).length)
        .toBeGreaterThanOrEqual(1);
      const hit = streamsFor(state, TECH_A)[0];
      expect(hit.search).toBe("");
      expect(hit.path).toBe(`/api/projects/${TECH_A}/task-events/stream`);
      expect(hit.method).toBe("GET");
      expect(hit.headers["x-workspace-id"]).toBeUndefined();

      await expect
        .poll(async () => (await readEsLog(page)).length)
        .toBeGreaterThanOrEqual(1);
      const es = (await readEsLog(page)).find((e) =>
        e.url.includes(`/projects/${TECH_A}/task-events/stream`),
      );
      expect(es, "必须构造 task-events EventSource").toBeTruthy();
      expect(es!.withCredentials).toBe(true);
      expect(es!.url).toMatch(
        new RegExp(`/api/projects/${TECH_A}/task-events/stream$`),
      );
      expect(es!.url).not.toContain("?");

      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toBeVisible({ timeout: 15_000 });
      await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      await expect(panel).not.toContainText(TYPE_LABEL.parse);
      await expect(panel).not.toContainText(STATUS_LABEL.running);

      await expect.poll(() => live !== null).toBeTruthy();
      const frame = taskEventFrame({
        eventId,
        taskId,
        taskType: "parse",
        status: "running",
        progress: 42,
      });
      live!.write(frame);

      await expect(panel).toContainText(TYPE_LABEL.parse, { timeout: 10_000 });
      await expect(panel).toContainText(STATUS_LABEL.running);
      await expect(panel).toContainText("42%");

      // 重复同一帧：展示保持，无任务详情、无额外 PUT
      const taskLogBefore = state.taskDetailLog.length;
      const getsBefore = getsFor(state, TECH_A).length;
      live!.write(frame);
      await expect(panel).toContainText(TYPE_LABEL.parse);
      await expect(panel).toContainText("42%");
      await waitStableExactCount(
        () => state.taskDetailLog.length,
        taskLogBefore,
        ZERO_STREAM_STABLE_MS,
      );
      expect(getsFor(state, TECH_A).length).toBe(getsBefore);
      expect(state.putLog.filter((id) => id === TECH_A)).toEqual([]);

      const panelText = await panel.innerText();
      expect(panelText).not.toContain(eventId);
      expect(panelText).not.toContain(taskId);
      expect(panelText).not.toContain(SECRET_MARKER);
      expect(panelText).not.toContain("2026-07-21");
      expect(panelText).not.toContain(TECH_A);

      expect(appConsoleLines(consoleLines).join("\n")).not.toContain(
        SECRET_MARKER,
      );
    } finally {
      await sse.close();
    }
  });

  test("技术标：未知 taskType 显示其他任务；坏帧/控制帧/默认 message/网络错误固定不可用", async ({
    page,
  }) => {
    // 未知类型
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eventId = allocateEventId(state);
      const taskId = allocateTaskId(state);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [
          cursorFrame(allocateEventId(state)),
          taskEventFrame({
            eventId,
            taskId,
            taskType: "totally_unknown_type_xyz",
            status: "pending",
            progress: 0,
          }),
        ],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(OTHER_TASK_LABEL, { timeout: 10_000 });
        await expect(panel).toContainText(STATUS_LABEL.pending);
        await expect(panel).not.toContainText("totally_unknown_type_xyz");
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    const badCases: Array<{ name: string; frames?: string[]; mode?: StreamFramePlan }> = [
      {
        name: "非法 JSON",
        frames: [
          cursorFrame(seedEventId(1)),
          `id: ${seedEventId(2)}\nevent: task-event\ndata: {not-json\n\n`,
        ],
      },
      {
        name: "缺字段",
        frames: [
          formatSseNamed(seedEventId(3), "task-event", {
            eventId: seedEventId(3),
            taskId: seedTaskId(3),
            taskType: "parse",
          }),
        ],
      },
      {
        name: "额外键",
        frames: [
          formatSseNamed(seedEventId(4), "task-event", {
            eventId: seedEventId(4),
            taskId: seedTaskId(4),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "eventId 与 lastEventId 不一致",
        frames: [
          formatSseNamed(seedEventId(5), "task-event", {
            eventId: seedEventId(6),
            taskId: seedTaskId(5),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 progress",
        frames: [
          formatSseNamed(seedEventId(7), "task-event", {
            eventId: seedEventId(7),
            taskId: seedTaskId(7),
            taskType: "parse",
            status: "running",
            progress: 101,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 status",
        frames: [
          formatSseNamed(seedEventId(8), "task-event", {
            eventId: seedEventId(8),
            taskId: seedTaskId(8),
            taskType: "parse",
            status: "done",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 pte_ 前缀",
        frames: [
          formatSseNamed("xxx_" + "0".repeat(32), "task-event", {
            eventId: "xxx_" + "0".repeat(32),
            taskId: seedTaskId(81),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 pte_ 大写",
        frames: [
          formatSseNamed("pte_" + "A".repeat(32), "task-event", {
            eventId: "pte_" + "A".repeat(32),
            taskId: seedTaskId(82),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 pte_ 长度",
        frames: [
          formatSseNamed("pte_" + "0".repeat(31), "task-event", {
            eventId: "pte_" + "0".repeat(31),
            taskId: seedTaskId(83),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "非法 task_ 长度",
        frames: [
          formatSseNamed(seedEventId(84), "task-event", {
            eventId: seedEventId(84),
            taskId: "task_" + "0".repeat(15),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "taskType 空串",
        frames: [
          formatSseNamed(seedEventId(85), "task-event", {
            eventId: seedEventId(85),
            taskId: seedTaskId(85),
            taskType: "",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "taskType 控制字符",
        frames: [
          formatSseNamed(seedEventId(86), "task-event", {
            eventId: seedEventId(86),
            taskId: seedTaskId(86),
            taskType: "pa\nrse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "taskType 超长",
        frames: [
          formatSseNamed(seedEventId(87), "task-event", {
            eventId: seedEventId(87),
            taskId: seedTaskId(87),
            taskType: "x".repeat(65),
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000Z",
          }),
        ],
      },
      {
        name: "occurredAt 非法日历 02-30",
        frames: [
          formatSseNamed(seedEventId(88), "task-event", {
            eventId: seedEventId(88),
            taskId: seedTaskId(88),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-02-30T12:00:00.000Z",
          }),
        ],
      },
      {
        name: "occurredAt 非法月 99",
        frames: [
          formatSseNamed(seedEventId(89), "task-event", {
            eventId: seedEventId(89),
            taskId: seedTaskId(89),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-99-99T99:99:99.999Z",
          }),
        ],
      },
      {
        name: "occurredAt 非 Z 偏移",
        frames: [
          formatSseNamed(seedEventId(90), "task-event", {
            eventId: seedEventId(90),
            taskId: seedTaskId(90),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.000+08:00",
          }),
        ],
      },
      {
        name: "occurredAt 非三位毫秒",
        frames: [
          formatSseNamed(seedEventId(91), "task-event", {
            eventId: seedEventId(91),
            taskId: seedTaskId(91),
            taskType: "parse",
            status: "running",
            progress: 10,
            occurredAt: "2026-07-21T12:34:56.00Z",
          }),
        ],
      },
      {
        name: "cursor 非法 JSON",
        frames: [`id: ${seedEventId(92)}\nevent: cursor\ndata: {not-json\n\n`],
      },
      {
        name: "cursor 额外键",
        frames: [
          formatSseNamed(seedEventId(93), "cursor", {
            eventId: seedEventId(93),
            extra: 1,
          }),
        ],
      },
      {
        name: "cursor eventId 不一致",
        frames: [
          formatSseNamed(seedEventId(94), "cursor", {
            eventId: seedEventId(95),
          }),
        ],
      },
      {
        name: "cursor 顶层重复键",
        frames: [
          formatSseRawData(
            seedEventId(96),
            "cursor",
            `{"eventId":"${seedEventId(96)}","eventId":"${seedEventId(96)}"}`,
          ),
        ],
      },
      {
        name: "cursor-stale 缺键",
        frames: [
          formatSseControl("cursor-stale", {
            code: "project_task_event_cursor_stale",
          }),
        ],
      },
      {
        name: "cursor-stale 额外键",
        frames: [
          formatSseControl("cursor-stale", {
            code: "project_task_event_cursor_stale",
            message: SECRET_MARKER,
            detail: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "cursor-stale 重复键",
        frames: [
          `event: cursor-stale\ndata: {"code":"project_task_event_cursor_stale","message":"x","code":"project_task_event_cursor_stale"}\n\n`,
        ],
      },
      {
        name: "cursor-stale 错误 code",
        frames: [
          formatSseControl("cursor-stale", {
            code: "wrong_code",
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "unavailable 缺键",
        frames: [
          formatSseControl("unavailable", {
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "unavailable 额外键",
        frames: [
          formatSseControl("unavailable", {
            code: "project_task_event_unavailable",
            message: SECRET_MARKER,
            stack: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "unavailable 错误 code",
        frames: [
          formatSseControl("unavailable", {
            code: "other_unavailable",
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "顶层重复键（合法折叠值对照）",
        frames: [
          formatSseRawData(
            seedEventId(9),
            "task-event",
            `{"eventId":"${seedEventId(9)}","taskId":"${seedTaskId(9)}","taskType":"parse","status":"running","progress":10,"occurredAt":"2026-07-21T12:34:56.000Z","status":"success"}`,
          ),
        ],
      },
      {
        name: "默认 message 帧",
        frames: [
          `id: ${seedEventId(10)}\ndata: ${JSON.stringify({
            eventId: seedEventId(10),
            taskId: seedTaskId(10),
            taskType: "parse",
            status: "running",
            progress: 1,
            occurredAt: "2026-07-21T12:34:56.000Z",
          })}\n\n`,
        ],
      },
      {
        name: "cursor-stale",
        frames: [
          formatSseControl("cursor-stale", {
            code: "project_task_event_cursor_stale",
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "unavailable",
        frames: [
          formatSseControl("unavailable", {
            code: "project_task_event_unavailable",
            message: SECRET_MARKER,
          }),
        ],
      },
      {
        name: "网络 abort",
        mode: { kind: "abort" },
      },
    ];

    for (const c of badCases) {
      // 每 case 清 cookie/导航，避免串 case 残留会话导致跳过登录页
      await page.context().clearCookies();
      await page.goto("about:blank");
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.streamMode[TECH_A] = c.mode ?? {
        kind: "sse",
        frames: c.frames ?? [],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
        const text = await panel.innerText();
        expect(text, `case=${c.name}`).not.toContain(SECRET_MARKER);
        expect(text).not.toContain("project_task_event");
        expect(text).not.toMatch(/^pte_/m);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }
  });

  test("技术标：A→B 关闭旧流；迟到 A 帧不得污染 B；无任务详情请求", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      makeProject({ id: TECH_B, name: "技术乙", kind: "technical" }),
    ]);
    const gateA = createHoldGate();
    const lateEvent = allocateEventId(state);
    const lateTask = allocateTaskId(state);
    state.streamMode[TECH_A] = {
      kind: "gate",
      gate: gateA,
      frames: [
        cursorFrame(allocateEventId(state)),
        taskEventFrame({
          eventId: lateEvent,
          taskId: lateTask,
          taskType: "analyze",
          status: "running",
          progress: 77,
        }),
      ],
      holdOpen: true,
    };
    state.streamMode[TECH_B] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");
      await expect
        .poll(() => streamsFor(state, TECH_A).length)
        .toBeGreaterThanOrEqual(1);
      await expect.poll(() => gateA.waiterCount()).toBeGreaterThanOrEqual(1);

      await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
      await expectTechReady(page, "技术乙");
      await expect
        .poll(() => streamsFor(state, TECH_B).length)
        .toBeGreaterThanOrEqual(1);

      // 与 H3 一致：A 卸载后 close 至少发生过（StrictMode/双面板可能更多）
      await expect
        .poll(async () => readEsCloseCount(page))
        .toBeGreaterThanOrEqual(1);

      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toBeVisible();
      await expect(panel).not.toContainText(TYPE_LABEL.analyze);
      await expect(panel).not.toContainText("77%");

      // 释放 A 迟到帧：不得污染 B 面板
      gateA.release();

      const stableStart = Date.now();
      await expect
        .poll(
          async () => {
            const text = await panel.innerText();
            if (text.includes(TYPE_LABEL.analyze) || text.includes("77%")) {
              return -1;
            }
            return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
          },
          { timeout: 10_000 },
        )
        .toBe(1);
      await expect(panel).not.toContainText(TYPE_LABEL.analyze);
      await expect(panel).not.toContainText(lateEvent);
      await expect(panel).not.toContainText(lateTask);
      // 契约禁止任务详情 GET（带 taskId）；项目级 tasks 列表由既有 pipeline 触发，不记为 I3 违规
      expect(
        state.taskDetailLog.filter((line) => !line.endsWith(" _list")),
      ).toEqual([]);
    } finally {
      await sse.close();
    }
  });

  test("商务标：合法 task-event 展示；无 storage/URL/console 敏感字段；无 editor PUT", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: BIZ_A, name: "商务甲", kind: "business" }),
      makeProject({ id: BIZ_B, name: "商务乙", kind: "business" }),
    ]);
    const eventId = allocateEventId(state);
    const taskId = allocateTaskId(state);
    state.streamMode[BIZ_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        taskEventFrame({
          eventId,
          taskId,
          taskType: "biz_quote",
          status: "success",
          progress: 100,
        }),
      ],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openBiz(page, BIZ_A);
      await loginViaUi(page);
      await expectBizReady(page, "商务甲");

      const panel = page.getByTestId(BIZ_TESTID);
      await expect(panel).toContainText(TYPE_LABEL.biz_quote, {
        timeout: 10_000,
      });
      await expect(panel).toContainText(STATUS_LABEL.success);
      await expect(panel).toContainText("100%");

      const es = (await readEsLog(page)).find((e) =>
        e.url.includes(`/projects/${BIZ_A}/task-events/stream`),
      );
      expect(es?.withCredentials).toBe(true);
      expect(es?.url).toMatch(
        new RegExp(`/api/projects/${BIZ_A}/task-events/stream$`),
      );

      // 会话 GET 存在；不得因 task-event 再拉任务详情（_list 由既有 pipeline 触发）
      expect(getsFor(state, BIZ_A).length).toBe(PROJECT_SESSION_GETS);
      expect(
        state.taskDetailLog.filter((line) => !line.endsWith(" _list")),
      ).toEqual([]);
      expect(state.putLog.filter((id) => id === BIZ_A)).toEqual([]);

      const surfaces = await readLeakSurfaces(page);
      const panelText = await panel.innerText();
      // 路由 URL 可含 projectId；面板与 storage/console 不得泄漏 eventId/taskId/secret
      assertNoSecretOrIds(surfaces, panelText, [
        eventId,
        taskId,
        SECRET_MARKER,
      ]);
      expect(panelText).not.toContain(BIZ_A);
      expect(panelText).not.toContain(eventId);
      expect(panelText).not.toContain(taskId);
      expect(surfaces.href).not.toContain(eventId);
      expect(surfaces.href).not.toContain(taskId);
      expect(surfaces.local.join("\n")).not.toContain(eventId);
      expect(surfaces.session.join("\n")).not.toContain(eventId);
      expect(appConsoleLines(consoleLines).join("\n")).not.toContain(
        SECRET_MARKER,
      );
    } finally {
      await sse.close();
    }
  });
});
