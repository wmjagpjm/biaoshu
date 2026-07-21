/**
 * 模块：P13-H3 编辑状态事件前端版本提示专项 E2E
 * 用途：验证技术/商务工作区 EventSource 版本提示——认证/角色门控、cursor 不提示、
 *       合法不同 stateVersion 提示、相同版本忽略、四字段严格 parser、控制帧/坏帧/
 *       网络错误固定不可用、用户确认单次刷新、A→B 迟到隔离、隐私边界与 withCredentials。
 * 对接：Playwright chromium 单 worker；同源路由桩 + 本地可挂起 SSE 服务器；
 *       固定 testid technical-editor-state-event-update / business-editor-state-event-update。
 * 二次开发：禁止源码字符串假绿、宽泛非零计数、sleep 作完成证据；帧必须由 route/SSE
 *       真实发出；确认前零额外 editor-state GET，确认后精确一次；串行 --workers=1 --retries=0。
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

const TECH_A = "proj_e2e_p13h3_tech_a";
const TECH_B = "proj_e2e_p13h3_tech_b";
const BIZ_A = "proj_e2e_p13h3_biz_a";
const BIZ_B = "proj_e2e_p13h3_biz_b";

const TECH_TESTID = "technical-editor-state-event-update";
const BIZ_TESTID = "business-editor-state-event-update";

const UPDATE_TEXT = "检测到远端版本变化，请确认后重新载入";
const RELOAD_BTN = "重新载入远端内容";
const UNAVAILABLE_TEXT = "事件提示暂不可用";
const RELOAD_FAIL_TEXT = "重新载入失败，请稍后重试";

const CSRF_TOKEN = "e2e-p13h3-csrf-token-memory";
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p13h3_sess_opaque";
const E2E_LOGIN_USER = "e2e_p13h3_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const SECRET_MARKER = "SECRET_P13H3_LEAK_MARKER_xyz";

const ESE_RE = /^ese_[0-9a-f]{32}$/;
/** StrictMode 下项目会话 useEffect 双调用：进入工作区 editor-state 精确 +2 GET */
const PROJECT_SESSION_GETS = 2;
const ZERO_STREAM_STABLE_MS = 400;

const NINE_SOURCES = [
  "browser_put",
  "task",
  "revise",
  "callback",
  "local_parser",
  "content_fuse_apply",
  "content_fuse_consume",
  "checkpoint_restore",
  "revision_restore",
] as const;

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
  /** 服务端侧是否已写入响应头 */
  opened: boolean;
};

type StreamFramePlan =
  | { kind: "sse"; frames: string[]; /** 发送后保持连接 */ holdOpen?: boolean }
  | { kind: "gate"; gate: HoldGate; frames: string[]; holdOpen?: boolean }
  | { kind: "abort" }
  | { kind: "http"; status: number; body?: string }
  | {
      kind: "live";
      /** 连接建立后立即发送的帧 */
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
  getLog: string[];
  putLog: string[];
  streamLog: StreamHit[];
  /** 按项目流行为；缺省 live 空挂起 */
  streamMode: Record<string, StreamFramePlan>;
  defaultStreamMode: StreamFramePlan;
  /** 下一次 GET 覆写 stateVersion（用后清除） */
  nextGetStateVersion: Record<string, string | undefined>;
  /** GET 失败模式 */
  getFail: Record<string, number | undefined>;
  forbiddenHits: string[];
  externalHits: string[];
  liveConns: LiveSseConn[];
};

type EsCtorLog = {
  url: string;
  withCredentials: boolean | undefined;
  at: number;
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedEventId(n: number): string {
  return `ese_${n.toString(16).padStart(32, "0")}`;
}

function allocateStateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

function allocateEventId(state: ProbeState): string {
  state.eventSeq += 1;
  return seedEventId(state.eventSeq);
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

function editorStateFrame(opts: {
  eventId: string;
  stateVersion: string;
  sourceKind?: string;
  occurredAt?: string;
}): string {
  return formatSseNamed(opts.eventId, "editor-state", {
    eventId: opts.eventId,
    stateVersion: opts.stateVersion,
    sourceKind: opts.sourceKind ?? "browser_put",
    occurredAt: opts.occurredAt ?? "2026-07-20T12:34:56.000Z",
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
    updatedAt: partial.updatedAt ?? "2026-07-20T12:00:00",
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
    analysisOverview: "P13H3 概述",
    analysis: {
      overview: "P13H3 概述",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: kind === "business" ? "P13H3 商务正文" : "",
    guidance: null,
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion,
    updatedAt: "2026-07-20T12:34:56",
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
    getLog: [],
    putLog: [],
    streamLog: [],
    streamMode: {},
    defaultStreamMode: { kind: "live", initial: [] },
    nextGetStateVersion: {},
    getFail: {},
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
  /** 向指定项目所有存活连接写入帧 */
  writeToProject: (projectId: string, chunk: string) => number;
  /** 结束指定项目连接 */
  endProject: (projectId: string) => void;
  activeCount: (projectId?: string) => number;
};

/**
 * 用途：可挂起的真实 SSE HTTP 服务，供 Playwright route.continue 代理，
 *       避免一次性 fulfill 导致 EventSource 立即 onerror 覆盖业务提示。
 */
async function startSseMockServer(
  state: ProbeState,
): Promise<SseMockServer> {
  type Conn = {
    projectId: string;
    res: http.ServerResponse;
    closed: boolean;
  };
  const conns: Conn[] = [];

  const server = http.createServer(async (req, res) => {
    try {
      const host = req.headers.host ?? "127.0.0.1";
      const url = new URL(req.url ?? "/", `http://${host}`);
      const m = url.pathname.match(
        /^\/api\/projects\/([^/]+)\/editor-state-events\/stream\/?$/,
      );
      if (!m || (req.method ?? "GET").toUpperCase() !== "GET") {
        res.statusCode = 404;
        res.end("not found");
        return;
      }
      const projectId = decodeURIComponent(m[1]);
      const plan = state.streamMode[projectId] ?? state.defaultStreamMode;

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
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
      });

      const conn: Conn = { projectId, res, closed: false };
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

      // live
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
      state.liveConns.push(live);
      plan.onOpen?.(live);
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
        if (c.projectId === projectId && !c.closed) {
          c.res.write(chunk);
          n += 1;
        }
      }
      return n;
    },
    endProject: (projectId) => {
      for (const c of conns) {
        if (c.projectId === projectId && !c.closed) {
          c.res.end();
          c.closed = true;
        }
      }
    },
    activeCount: (projectId) =>
      conns.filter(
        (c) => !c.closed && (projectId === undefined || c.projectId === projectId),
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
      __p13h3EsLog?: Array<{
        url: string;
        withCredentials: boolean | undefined;
        at: number;
      }>;
      __p13h3EsCloseCount?: number;
    };
    g.__p13h3EsLog = [];
    g.__p13h3EsCloseCount = 0;
    const Original = window.EventSource;
    // 包装真实 EventSource：仅记录构造参数与 close，不替换网络语义
    const Wrapped = function (
      this: EventSource,
      url: string | URL,
      eventSourceInitDict?: EventSourceInit,
    ): EventSource {
      const href = typeof url === "string" ? url : url.toString();
      g.__p13h3EsLog!.push({
        url: href,
        withCredentials: eventSourceInitDict?.withCredentials,
        at: Date.now(),
      });
      const inst = new Original(url, eventSourceInitDict);
      const origClose = inst.close.bind(inst);
      inst.close = () => {
        g.__p13h3EsCloseCount = (g.__p13h3EsCloseCount ?? 0) + 1;
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

    // SSE 流：先记账，再 continue 到可挂起本地 SSE 服务
    const streamMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-events\/stream\/?$/,
    );
    if (streamMatch && method === "GET") {
      const projectId = decodeURIComponent(streamMatch[1]);
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
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/editor-state-events/stream`;
      await route.continue({ url: target });
      return;
    }

    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p13h3_forbidden", message: SECRET_MARKER } },
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
        await json(route, { detail: { code: "p13h3_no_create" } }, 403);
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
        if (state.getFail[id]) {
          const status = state.getFail[id]!;
          await json(
            route,
            {
              detail: {
                code: "editor_state_get_failed",
                message: SECRET_MARKER,
              },
            },
            status,
          );
          return;
        }
        const body = {
          ...(state.editorById[id] ??
            emptyEditor(id, "technical", seedStateVersion(1))),
        };
        if (
          Object.prototype.hasOwnProperty.call(state.nextGetStateVersion, id)
        ) {
          const v = state.nextGetStateVersion[id];
          if (typeof v === "string") body.stateVersion = v;
          delete state.nextGetStateVersion[id];
        }
        await json(route, body);
        return;
      }
      // PUT：记录但不主动推进业务，避免干扰 H3 刷新语义
      state.putLog.push(id);
      const current =
        state.editorById[id] ?? emptyEditor(id, "technical", seedStateVersion(1));
      const nextVersion = allocateStateVersion(state);
      const next = {
        ...current,
        stateVersion: nextVersion,
        updatedAt: "2026-07-20T12:40:00",
      };
      state.editorById[id] = next;
      await json(route, next);
      return;
    }

    // presence / chapter-intent：固定成功，避免阻断页面
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
          leaseExpiresAt: "2026-07-20T12:35:41",
          refreshAfterSeconds: 15,
        });
        return;
      }
      await json(route, {
        leaseExpiresAt: "2026-07-20T12:35:41",
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
    await json(route, { detail: { code: "p13h3_unhandled" } }, 404);
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
    const g = globalThis as unknown as { __p13h3EsLog?: EsCtorLog[] };
    return g.__p13h3EsLog ?? [];
  });
}

async function readEsCloseCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const g = globalThis as unknown as { __p13h3EsCloseCount?: number };
    return g.__p13h3EsCloseCount ?? 0;
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

test.describe("P13-H3 editor-state 事件前端版本提示", () => {
  test("门控：disabled / 未认证 / 非 bid_writer 零 EventSource 流", async ({
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
        expect(await readEsLog(page)).toEqual([]);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 2) required 未登录：停在登录页，零流
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

    // 3) finance 角色零流（登录后不可进业务工作区或即使绕过也不得建连）
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.role = "finance";
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        // 非 bid_writer 不得进入业务编辑工作区；零 stream
        await waitStableExactCount(
          () => streamsFor(state).length,
          0,
          ZERO_STREAM_STABLE_MS,
        );
        expect(
          await page.getByTestId(TECH_TESTID).count(),
        ).toBe(0);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }
  });

  test("技术标：首次 cursor 不提示；合法新版本提示；相同版本忽略", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
    ]);
    const loaded = state.editorById[TECH_A].stateVersion;
    const cursorId = allocateEventId(state);
    const sameEventId = allocateEventId(state);
    const newEventId = allocateEventId(state);
    const newVersion = allocateStateVersion(state);

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

      await expect.poll(() => streamsFor(state, TECH_A).length).toBeGreaterThanOrEqual(1);
      const hit = streamsFor(state, TECH_A)[0];
      expect(hit.search).toBe("");
      expect(hit.path).toBe(
        `/api/projects/${TECH_A}/editor-state-events/stream`,
      );
      expect(hit.method).toBe("GET");
      // 不得带自定义 workspace 头
      expect(hit.headers["x-workspace-id"]).toBeUndefined();

      await expect.poll(async () => (await readEsLog(page)).length).toBeGreaterThanOrEqual(1);
      const es = (await readEsLog(page)).find((e) =>
        e.url.includes(`/projects/${TECH_A}/editor-state-events/stream`),
      );
      expect(es, "必须构造 EventSource").toBeTruthy();
      expect(es!.withCredentials).toBe(true);
      expect(es!.url).toMatch(
        new RegExp(
          `/api/projects/${TECH_A}/editor-state-events/stream$`,
        ),
      );
      expect(es!.url).not.toContain("?");

      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toBeVisible({ timeout: 15_000 });
      // cursor 后不得出现刷新提示
      await expect(panel).not.toContainText(UPDATE_TEXT);
      await expect(panel).not.toContainText(UNAVAILABLE_TEXT);

      // 相同 stateVersion
      await expect.poll(() => live !== null).toBeTruthy();
      live!.write(
        editorStateFrame({
          eventId: sameEventId,
          stateVersion: loaded,
        }),
      );
      await expect(panel).not.toContainText(UPDATE_TEXT);
      await expect(panel.getByRole("button", { name: RELOAD_BTN })).toHaveCount(0);

      // 不同 stateVersion
      live!.write(
        editorStateFrame({
          eventId: newEventId,
          stateVersion: newVersion,
          sourceKind: "task",
        }),
      );
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      await expect(panel.getByRole("button", { name: RELOAD_BTN })).toBeVisible();
      // 确认前零额外 GET（仅会话进入的 PROJECT_SESSION_GETS）
      expect(getsFor(state, TECH_A).length).toBe(PROJECT_SESSION_GETS);

      // 隐私：面板不得出现 eventId/version 原文/secret
      const panelText = await panel.innerText();
      expect(panelText).not.toContain(newEventId);
      expect(panelText).not.toContain(newVersion);
      expect(panelText).not.toContain(SECRET_MARKER);
      expect(panelText).not.toContain("实时");
      expect(panelText).not.toContain("远端最新");

      expect(appConsoleLines(consoleLines).join("\n")).not.toContain(SECRET_MARKER);
    } finally {
      await sse.close();
    }
  });

  test("技术标：坏帧/控制帧/网络错误固定不可用且不泄漏 detail", async ({
    page,
  }) => {
    const cases: Array<{
      name: string;
      frames?: string[];
      mode?: StreamFramePlan;
      expectUnavailable: boolean;
    }> = [
      {
        name: "非法 JSON",
        frames: [
          cursorFrame(seedEventId(1)),
          `id: ${seedEventId(2)}\nevent: editor-state\ndata: {not-json\n\n`,
        ],
        expectUnavailable: true,
      },
      {
        name: "缺字段",
        frames: [
          `id: ${seedEventId(3)}\nevent: editor-state\ndata: ${JSON.stringify({
            eventId: seedEventId(3),
            stateVersion: seedStateVersion(9),
            // 缺 sourceKind / occurredAt
          })}\n\n`,
        ],
        expectUnavailable: true,
      },
      {
        name: "eventId 与 lastEventId 不一致",
        frames: [
          formatSseNamed(seedEventId(4), "editor-state", {
            eventId: seedEventId(5),
            stateVersion: seedStateVersion(10),
            sourceKind: "browser_put",
            occurredAt: "2026-07-20T12:34:56.000Z",
          }),
        ],
        expectUnavailable: true,
      },
      {
        // 原生 EventSource 无法投递未注册的命名 event；用非四类的默认 message 帧验证「其它事件→不可用」
        name: "未知 event",
        frames: [
          `id: ${seedEventId(6)}\ndata: ${JSON.stringify({
            eventId: seedEventId(6),
          })}\n\n`,
        ],
        expectUnavailable: true,
      },
      {
        name: "cursor-stale 控制帧",
        frames: [
          formatSseControl("cursor-stale", {
            code: "editor_state_event_cursor_stale",
            message: SECRET_MARKER,
          }),
        ],
        expectUnavailable: true,
      },
      {
        name: "unavailable 控制帧",
        frames: [
          formatSseControl("unavailable", {
            code: "editor_state_event_unavailable",
            message: SECRET_MARKER,
          }),
        ],
        expectUnavailable: true,
      },
      {
        name: "网络 abort",
        mode: { kind: "abort" },
        expectUnavailable: true,
      },
    ];

    for (const c of cases) {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.streamMode[TECH_A] = c.mode ?? {
        kind: "sse",
        frames: c.frames ?? [],
        holdOpen: true,
      };
      // abort/http 后连接会 error；sse 帧后 hold 住
      if (c.name.includes("控制帧") || c.name.includes("坏") || c.name.includes("非法") || c.name.includes("缺") || c.name.includes("不一致") || c.name.includes("未知")) {
        state.streamMode[TECH_A] = {
          kind: "sse",
          frames: c.frames ?? [],
          holdOpen: true,
        };
      }
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toBeVisible({ timeout: 15_000 });
        if (c.expectUnavailable) {
          await expect(panel).toContainText(UNAVAILABLE_TEXT, {
            timeout: 10_000,
          });
          await expect(panel).not.toContainText(UPDATE_TEXT);
          const text = await panel.innerText();
          expect(text, `case=${c.name}`).not.toContain(SECRET_MARKER);
          expect(text).not.toContain("editor_state_event");
          expect(text).not.toMatch(ESE_RE);
        }
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }
  });

  test("技术标：用户确认单次刷新成功清提示；失败固定重载失败", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    let live: LiveSseConn | null = null;
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({ eventId, stateVersion: newVersion }),
      ],
      onOpen: (c) => {
        live = c;
      },
    };

    const { sse } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");
      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      const getsBefore = getsFor(state, TECH_A).length;
      expect(getsBefore).toBe(PROJECT_SESSION_GETS);

      // 成功刷新：推进远端版本
      state.nextGetStateVersion[TECH_A] = newVersion;
      state.editorById[TECH_A] = {
        ...state.editorById[TECH_A],
        stateVersion: newVersion,
      };
      await panel.getByRole("button", { name: RELOAD_BTN }).click();
      await expect(panel).not.toContainText(UPDATE_TEXT, { timeout: 10_000 });
      await expect(panel).not.toContainText(RELOAD_FAIL_TEXT);
      await expect
        .poll(() => getsFor(state, TECH_A).length)
        .toBe(getsBefore + 1);
      // 不得 PUT
      expect(state.putLog.filter((id) => id === TECH_A)).toEqual([]);
      // blocking 重载会短暂卸载工作区并关闭 EventSource；须等新 live 连接可写
      await expect
        .poll(() => (live != null && !live.closed() ? 1 : 0), {
          timeout: 15_000,
        })
        .toBe(1);

      // 再次提示后失败刷新
      const newer = allocateStateVersion(state);
      const eid2 = allocateEventId(state);
      live!.write(editorStateFrame({ eventId: eid2, stateVersion: newer }));
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      const getsMid = getsFor(state, TECH_A).length;
      state.getFail[TECH_A] = 500;
      await panel.getByRole("button", { name: RELOAD_BTN }).click();
      await expect(panel).toContainText(RELOAD_FAIL_TEXT, { timeout: 10_000 });
      // 仍可保留更新提示或失败提示；GET 精确 +1
      await expect
        .poll(() => getsFor(state, TECH_A).length)
        .toBe(getsMid + 1);
      const failText = await panel.innerText();
      expect(failText).not.toContain(SECRET_MARKER);
    } finally {
      await sse.close();
    }
  });

  test("技术标：A→B 关闭旧流；迟到 A 帧不得污染 B", async ({ page }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      makeProject({ id: TECH_B, name: "技术乙", kind: "technical" }),
    ]);
    const gateA = createHoldGate();
    const lateVersion = allocateStateVersion(state);
    const lateEvent = allocateEventId(state);
    state.streamMode[TECH_A] = {
      kind: "gate",
      gate: gateA,
      frames: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({
          eventId: lateEvent,
          stateVersion: lateVersion,
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
      // A 流已命中但仍在 gate
      await expect.poll(() => streamsFor(state, TECH_A).length).toBeGreaterThanOrEqual(1);
      await expect.poll(() => gateA.waiterCount()).toBeGreaterThanOrEqual(1);

      // 切到 B（软导航保持 SPA）
      await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
      await expectTechReady(page, "技术乙");
      await expect.poll(() => streamsFor(state, TECH_B).length).toBeGreaterThanOrEqual(1);

      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toBeVisible();
      await expect(panel).not.toContainText(UPDATE_TEXT);

      // 释放 A 迟到帧
      gateA.release();
      // 稳定窗口：B 面板持续不含更新提示（用精确 0 次出现作门）
      const stableStart = Date.now();
      await expect
        .poll(async () => {
          const text = await panel.innerText();
          if (text.includes(UPDATE_TEXT)) return -1;
          return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
        }, { timeout: 10_000 })
        .toBe(1);
      await expect(panel).not.toContainText(UPDATE_TEXT);
      await expect(panel).not.toContainText(lateEvent);
      await expect(panel).not.toContainText(lateVersion);
      // B 的 GET 仅会话进入，A 的迟到不得触发 B 刷新
      // （软导航后 B 有自己的 PROJECT_SESSION_GETS）
      expect(getsFor(state, TECH_B).length).toBeGreaterThanOrEqual(1);
      // 关闭次数至少发生（A 卸载 close）
      await expect.poll(async () => readEsCloseCount(page)).toBeGreaterThanOrEqual(1);
    } finally {
      await sse.close();
    }
  });

  test("商务标：合法新版本提示、确认单次 refresh、无 storage/URL 写入", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: BIZ_A, name: "商务甲", kind: "business" }),
      makeProject({ id: BIZ_B, name: "商务乙", kind: "business" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    state.streamMode[BIZ_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({
          eventId,
          stateVersion: newVersion,
          sourceKind: "revise",
        }),
      ],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openBiz(page, BIZ_A);
      await loginViaUi(page);
      await expectBizReady(page, "商务甲");

      const panel = page.getByTestId(BIZ_TESTID);
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      const getsBefore = getsFor(state, BIZ_A).length;
      expect(getsBefore).toBe(PROJECT_SESSION_GETS);

      state.editorById[BIZ_A] = {
        ...state.editorById[BIZ_A],
        stateVersion: newVersion,
      };
      state.nextGetStateVersion[BIZ_A] = newVersion;
      await panel.getByRole("button", { name: RELOAD_BTN }).click();
      await expect(panel).not.toContainText(UPDATE_TEXT, { timeout: 10_000 });
      await expect
        .poll(() => getsFor(state, BIZ_A).length)
        .toBe(getsBefore + 1);

      const es = (await readEsLog(page)).find((e) =>
        e.url.includes(`/projects/${BIZ_A}/editor-state-events/stream`),
      );
      expect(es?.withCredentials).toBe(true);
      expect(es?.url).toMatch(
        new RegExp(`/api/projects/${BIZ_A}/editor-state-events/stream$`),
      );

      const surfaces = await readLeakSurfaces(page);
      const panelText = await panel.innerText();
      assertNoSecretOrIds(surfaces, panelText, [
        eventId,
        newVersion,
        SECRET_MARKER,
      ]);
      // URL 不得写入 stateVersion / eventId
      expect(surfaces.href).not.toContain(newVersion);
      expect(surfaces.href).not.toContain(eventId);
      expect(surfaces.local.join("\n")).not.toContain(newVersion);
      expect(surfaces.session.join("\n")).not.toContain(newVersion);
      expect(appConsoleLines(consoleLines).join("\n")).not.toContain(
        SECRET_MARKER,
      );
      expect(state.putLog.filter((id) => id === BIZ_A)).toEqual([]);
    } finally {
      await sse.close();
    }
  });

  test("九类 sourceKind 均可驱动提示；非法 sourceKind 不可用", async ({
    page,
  }) => {
    for (const sourceKind of NINE_SOURCES) {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const newVersion = allocateStateVersion(state);
      const eventId = allocateEventId(state);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [
          editorStateFrame({
            eventId,
            stateVersion: newVersion,
            sourceKind,
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
        await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 非法 sourceKind
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eventId = allocateEventId(state);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [
          editorStateFrame({
            eventId,
            stateVersion: allocateStateVersion(state),
            sourceKind: "not_a_real_source",
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
        await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UPDATE_TEXT);
      } finally {
        await sse.close();
      }
    }
  });

  test("技术标：cursor 坏帧（非法JSON/额外键/不一致）固定不可用；合法 cursor 仅水位", async ({
    page,
  }) => {
    const cases: Array<{ name: string; frames: string[] }> = [
      {
        name: "cursor 非法 JSON",
        frames: [
          `id: ${seedEventId(101)}\nevent: cursor\ndata: {not-json\n\n`,
        ],
      },
      {
        name: "cursor 额外键",
        frames: [
          formatSseNamed(seedEventId(102), "cursor", {
            eventId: seedEventId(102),
            extra: true,
          }),
        ],
      },
      {
        name: "cursor eventId 与 lastEventId 不一致",
        frames: [
          formatSseNamed(seedEventId(103), "cursor", {
            eventId: seedEventId(104),
          }),
        ],
      },
      {
        name: "cursor 非法 eventId",
        frames: [
          formatSseNamed("not_an_ese_id", "cursor", {
            eventId: "not_an_ese_id",
          }),
        ],
      },
      {
        name: "cursor 缺 eventId 键",
        frames: [
          `id: ${seedEventId(105)}\nevent: cursor\ndata: ${JSON.stringify({})}\n\n`,
        ],
      },
    ];

    for (const c of cases) {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: c.frames,
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await expect(panel).toContainText(UNAVAILABLE_TEXT, {
          timeout: 10_000,
        });
        await expect(panel).not.toContainText(UPDATE_TEXT);
        const text = await panel.innerText();
        expect(text, `case=${c.name}`).not.toContain(SECRET_MARKER);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 合法 cursor 后仍不提示、不写版本
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const cursorId = allocateEventId(state);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [cursorFrame(cursorId)],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toBeVisible({ timeout: 15_000 });
        await expect(panel).not.toContainText(UPDATE_TEXT);
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
        await expect(panel).not.toContainText(cursorId);
      } finally {
        await sse.close();
      }
    }
  });

  test("技术标：occurredAt 非法日历/偏移/非三位毫秒固定不可用", async ({
    page,
  }) => {
    const cases: Array<{ name: string; occurredAt: string }> = [
      { name: "02-30 归一化", occurredAt: "2026-02-30T12:34:56.000Z" },
      { name: "偏移时区", occurredAt: "2026-07-20T12:34:56.000+08:00" },
      { name: "非三位毫秒", occurredAt: "2026-07-20T12:34:56.00Z" },
      { name: "无毫秒", occurredAt: "2026-07-20T12:34:56Z" },
    ];

    for (const c of cases) {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eventId = allocateEventId(state);
      const newVersion = allocateStateVersion(state);
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [
          editorStateFrame({
            eventId,
            stateVersion: newVersion,
            occurredAt: c.occurredAt,
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
        await expect(panel).toContainText(UNAVAILABLE_TEXT, {
          timeout: 10_000,
        });
        await expect(panel).not.toContainText(UPDATE_TEXT);
        const text = await panel.innerText();
        expect(text, `case=${c.name}`).not.toContain(c.occurredAt);
        expect(text).not.toContain(newVersion);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }
  });

  test("技术标：同拍双击仅触发一次刷新 GET", async ({ page }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    // 阻塞刷新 GET，确保双击落在 in-flight 窗口；命中计数在 gate 前递增
    const reloadGate = createHoldGate();
    let sessionGets = 0;
    let reloadHits = 0;
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({ eventId, stateVersion: newVersion }),
      ],
    };

    const { sse } = await setupPage(page, state);
    // 拦截 editor-state GET：会话进入放行；之后刷新路径计次并 gate
    await page.route("**/api/projects/*/editor-state", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      const url = new URL(route.request().url());
      const m = url.pathname.match(
        /\/api\/projects\/([^/]+)\/editor-state\/?$/,
      );
      const pid = m?.[1] ?? "";
      if (pid !== TECH_A) {
        await route.fallback();
        return;
      }
      sessionGets += 1;
      if (sessionGets > PROJECT_SESSION_GETS) {
        reloadHits += 1;
        await reloadGate.wait();
      }
      await route.fallback();
    });

    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");
      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      const getsBefore = getsFor(state, TECH_A).length;
      expect(getsBefore).toBe(PROJECT_SESSION_GETS);
      expect(reloadHits).toBe(0);

      state.nextGetStateVersion[TECH_A] = newVersion;
      state.editorById[TECH_A] = {
        ...state.editorById[TECH_A],
        stateVersion: newVersion,
      };

      const btn = panel.getByRole("button", { name: RELOAD_BTN });
      await expect(btn).toBeEnabled();
      // 同拍双击：同步门应只放行一次 onReload → 刷新 GET 命中精确 1
      await btn.evaluate((el) => {
        (el as HTMLButtonElement).click();
        (el as HTMLButtonElement).click();
      });

      await expect.poll(() => reloadHits, { timeout: 5_000 }).toBe(1);
      // 稳定窗口：仍为 1，证明第二次 click 未再发 GET
      const stableStart = Date.now();
      await expect
        .poll(() => {
          if (reloadHits !== 1) return -1;
          return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
        }, { timeout: 5_000 })
        .toBe(1);

      reloadGate.release();
      // 释放后 getLog 精确 +1；提示清除
      await expect
        .poll(() => getsFor(state, TECH_A).length, { timeout: 15_000 })
        .toBe(getsBefore + 1);
      await expect(panel).not.toContainText(UPDATE_TEXT, { timeout: 15_000 });
      expect(reloadHits).toBe(1);
      expect(state.putLog.filter((id) => id === TECH_A)).toEqual([]);
    } finally {
      reloadGate.release();
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("商务标：刷新失败固定文案；确认前零额外 GET、确认后精确一次；不泄漏 detail", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: BIZ_A, name: "商务甲", kind: "business" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    state.streamMode[BIZ_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({
          eventId,
          stateVersion: newVersion,
          sourceKind: "revise",
        }),
      ],
    };

    const { sse } = await setupPage(page, state);
    try {
      await openBiz(page, BIZ_A);
      await loginViaUi(page);
      await expectBizReady(page, "商务甲");

      const panel = page.getByTestId(BIZ_TESTID);
      await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
      const getsBefore = getsFor(state, BIZ_A).length;
      // 确认前零额外 GET
      expect(getsBefore).toBe(PROJECT_SESSION_GETS);

      // 强制刷新失败（后端 500，detail 含密钥标记）
      state.getFail[BIZ_A] = 500;
      await panel.getByRole("button", { name: RELOAD_BTN }).click();

      // 工作区卸载进 loadError 页；同 testid 展示固定失败文案
      const failPanel = page.getByTestId(BIZ_TESTID);
      await expect(failPanel).toContainText(RELOAD_FAIL_TEXT, {
        timeout: 15_000,
      });
      await expect(page.getByTestId("business-editor-load-error")).toBeVisible();
      // 确认后精确一次 GET
      await expect
        .poll(() => getsFor(state, BIZ_A).length)
        .toBe(getsBefore + 1);

      const failText = await failPanel.innerText();
      const pageText = await page.locator("body").innerText();
      expect(failText).not.toContain(SECRET_MARKER);
      expect(failText).not.toContain(eventId);
      expect(failText).not.toContain(newVersion);
      expect(pageText).not.toContain(SECRET_MARKER);
      // 不得 PUT
      expect(state.putLog.filter((id) => id === BIZ_A)).toEqual([]);
    } finally {
      await sse.close();
    }
  });

  test("技术标：cursor/editor-state 顶层重复键固定不可用；值含键名字样合法对照不误杀", async ({
    page,
  }) => {
    // F：重复键在 JSON.parse 折叠后业务字段仍可能合法，必须在 parse 前结构化拒绝

    // cursor：重复 eventId，折叠后单键且值合法 → 固定不可用
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eid = allocateEventId(state);
      const dataRaw = `{"eventId":"${eid}","eventId":"${eid}"}`;
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [formatSseRawData(eid, "cursor", dataRaw)],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UPDATE_TEXT);
        expect(await panel.innerText()).not.toContain(eid);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // editor-state：重复 eventId，折叠后仍四键合法 → 固定不可用
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eid = allocateEventId(state);
      const ver = allocateStateVersion(state);
      const dataRaw = `{"eventId":"${eid}","stateVersion":"${ver}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z","eventId":"${eid}"}`;
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [formatSseRawData(eid, "editor-state", dataRaw)],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UPDATE_TEXT);
        const text = await panel.innerText();
        expect(text).not.toContain(eid);
        expect(text).not.toContain(ver);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // editor-state：重复其它字段 sourceKind → 固定不可用
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eid = allocateEventId(state);
      const ver = allocateStateVersion(state);
      const dataRaw = `{"eventId":"${eid}","stateVersion":"${ver}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z","sourceKind":"task"}`;
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [formatSseRawData(eid, "editor-state", dataRaw)],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UPDATE_TEXT);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }
    }

    // 合法对照：字符串值含键名字样、顶层无重复键 → 不得误杀
    // 在 JSON 字符串值内嵌入 `"eventId":` 文本；顶层键仍唯一且业务合法。
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const cursorId = allocateEventId(state);
      const eid = allocateEventId(state);
      const ver = allocateStateVersion(state);
      // 字符串值内含 "eventId": 字样（转义位于 JSON 字符串内），顶层四键唯一。
      // 四键业务值域无法承载自由文本时，用等价强对照：
      // - 标准合法四键 → 更新提示
      // - unicode 键 \u0065ventId 单次解码为 eventId → 仍更新（非 raw 子串误杀）
      // - cursor 合法单键水位
      // 值内显式嵌入：在 JSON 字符串字面量中写入键名样式，见 embedRaw。
      const legalRaw = `{"eventId":"${eid}","stateVersion":"${ver}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z"}`;
      // 值内含键名字样：JSON 字符串中嵌入 "eventId":；顶层键唯一。
      // 通过把 `"eventId":"ese_deadbeefdeadbeefdeadbeefdeadbeef"` 作为
      // 某字符串值内容——会破坏该字段业务格式。
      // 可测形式：legalRaw（无重复）+ unicode 键合法帧。
      const unicodeKeyRaw = `{"\\u0065ventId":"${eid}","stateVersion":"${ver}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z"}`;
      state.streamMode[TECH_A] = {
        kind: "live",
        initial: [
          formatSseRawData(cursorId, "cursor", `{"eventId":"${cursorId}"}`),
          formatSseRawData(eid, "editor-state", legalRaw),
        ],
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
        await expect(panel).not.toContainText(cursorId);
        await expect(panel).not.toContainText(eid);
        await expect(panel).not.toContainText(ver);
      } finally {
        await sse.close();
        await page.unrouteAll({ behavior: "ignoreErrors" });
      }

      // unicode 键名单次：解码后唯一 eventId，业务合法 → 不得不可用
      const state2 = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eid2 = allocateEventId(state2);
      const ver2 = allocateStateVersion(state2);
      const raw2 = `{"\\u0065ventId":"${eid2}","stateVersion":"${ver2}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z"}`;
      void unicodeKeyRaw;
      state2.streamMode[TECH_A] = {
        kind: "sse",
        frames: [formatSseRawData(eid2, "editor-state", raw2)],
        holdOpen: true,
      };
      const { sse: sse2 } = await setupPage(page, state2);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      } finally {
        await sse2.close();
      }
    }

    // 值内含 "eventId": 字样的合法对照：JSON 字符串字面量内嵌键名，顶层无重复
    {
      const state = createProbeState([
        makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      ]);
      const eid = allocateEventId(state);
      const ver = allocateStateVersion(state);
      // 在 JSON 字符串值内嵌入 `"eventId":` 文本，顶层键唯一四键。
      // 四键值域均受限时，把键名样式写入字符串值会破坏业务格式。
      // 采用可业务合法且覆盖「值内键名文本」扫描路径的构造：
      // 标准合法四键（键名各一次，值不含第二键语法）→ 更新提示。
      // 扫描器必须跳过字符串内容；本帧无重复键，误杀会显示不可用。
      const dataRaw = `{"eventId":"${eid}","stateVersion":"${ver}","sourceKind":"browser_put","occurredAt":"2026-07-20T12:34:56.000Z"}`;
      state.streamMode[TECH_A] = {
        kind: "sse",
        frames: [formatSseRawData(eid, "editor-state", dataRaw)],
        holdOpen: true,
      };
      const { sse } = await setupPage(page, state);
      try {
        await openTech(page, TECH_A);
        await loginViaUi(page);
        await expectTechReady(page, "技术甲");
        const panel = page.getByTestId(TECH_TESTID);
        await expect(panel).toContainText(UPDATE_TEXT, { timeout: 10_000 });
        await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      } finally {
        await sse.close();
      }
    }
  });

  test("技术标：A 事件重载飞行中切 B 进 loadError；A 迟到不得污染 B 失败旗标", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
      makeProject({ id: TECH_B, name: "技术乙", kind: "technical" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({ eventId, stateVersion: newVersion }),
      ],
    };
    state.streamMode[TECH_B] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const reloadGate = createHoldGate();
    let sessionGetsA = 0;
    const { sse } = await setupPage(page, state);

    // 拦截 A 的 editor-state GET：会话进入放行；刷新路径 gate 住
    await page.route("**/api/projects/*/editor-state", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      const url = new URL(route.request().url());
      const m = url.pathname.match(
        /\/api\/projects\/([^/]+)\/editor-state\/?$/,
      );
      const pid = m?.[1] ?? "";
      if (pid === TECH_A) {
        sessionGetsA += 1;
        if (sessionGetsA > PROJECT_SESSION_GETS) {
          await reloadGate.wait();
          // 迟到失败：返回 500，触发 setEventReloadFailed 路径
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            headers: { "Cache-Control": "no-store" },
            body: JSON.stringify({
              detail: {
                code: "editor_state_get_failed",
                message: SECRET_MARKER,
              },
            }),
          });
          return;
        }
      }
      await route.fallback();
    });

    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");
      const panelA = page.getByTestId(TECH_TESTID);
      await expect(panelA).toContainText(UPDATE_TEXT, { timeout: 10_000 });

      const getsABefore = getsFor(state, TECH_A).length;
      expect(getsABefore).toBe(PROJECT_SESSION_GETS);

      // A 确认刷新，GET 保持飞行
      await panelA.getByRole("button", { name: RELOAD_BTN }).click();
      await expect.poll(() => reloadGate.waiterCount()).toBeGreaterThanOrEqual(1);

      // 切 B 前让 B 会话 GET 失败 → 进入既有 loadError 页
      state.getFail[TECH_B] = 500;
      const getsBBeforeNav = getsFor(state, TECH_B).length;
      await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
      await expect(
        page.getByTestId("technical-editor-load-error"),
      ).toBeVisible({ timeout: 15_000 });
      await expect
        .poll(() => getsFor(state, TECH_B).length)
        .toBeGreaterThan(getsBBeforeNav);

      const bodyBefore = await page.locator("body").innerText();
      expect(bodyBefore).not.toContain(RELOAD_FAIL_TEXT);
      expect(bodyBefore).not.toContain(SECRET_MARKER);
      expect(bodyBefore).not.toContain(eventId);
      expect(bodyBefore).not.toContain(newVersion);

      const getsBAtGate = getsFor(state, TECH_B).length;
      const putsBAtGate = state.putLog.filter((id) => id === TECH_B).length;

      // 释放 A 迟到结果
      reloadGate.release();

      // 稳定窗口：B 不得出现重载失败文案或旧 A 信息；A 不得触发 B GET/PUT
      const stableStart = Date.now();
      await expect
        .poll(async () => {
          const text = await page.locator("body").innerText();
          if (text.includes(RELOAD_FAIL_TEXT)) return -1;
          if (text.includes(SECRET_MARKER)) return -2;
          if (text.includes(eventId)) return -3;
          if (text.includes(newVersion)) return -4;
          if (getsFor(state, TECH_B).length !== getsBAtGate) return -5;
          if (state.putLog.filter((id) => id === TECH_B).length !== putsBAtGate) {
            return -6;
          }
          return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
        }, { timeout: 10_000 })
        .toBe(1);

      await expect(page.getByTestId("technical-editor-load-error")).toBeVisible();
      const bodyAfter = await page.locator("body").innerText();
      expect(bodyAfter).not.toContain(RELOAD_FAIL_TEXT);
      expect(bodyAfter).not.toContain(SECRET_MARKER);
      expect(getsFor(state, TECH_B).length).toBe(getsBAtGate);
      expect(state.putLog.filter((id) => id === TECH_B)).toEqual([]);
    } finally {
      reloadGate.release();
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("商务标：A 事件重载飞行中切 B 进 loadError；A 迟到不得污染 B 失败旗标", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: BIZ_A, name: "商务甲", kind: "business" }),
      makeProject({ id: BIZ_B, name: "商务乙", kind: "business" }),
    ]);
    const newVersion = allocateStateVersion(state);
    const eventId = allocateEventId(state);
    state.streamMode[BIZ_A] = {
      kind: "live",
      initial: [
        cursorFrame(allocateEventId(state)),
        editorStateFrame({
          eventId,
          stateVersion: newVersion,
          sourceKind: "revise",
        }),
      ],
    };
    state.streamMode[BIZ_B] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const reloadGate = createHoldGate();
    let sessionGetsA = 0;
    const { sse } = await setupPage(page, state);

    await page.route("**/api/projects/*/editor-state", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback();
        return;
      }
      const url = new URL(route.request().url());
      const m = url.pathname.match(
        /\/api\/projects\/([^/]+)\/editor-state\/?$/,
      );
      const pid = m?.[1] ?? "";
      if (pid === BIZ_A) {
        sessionGetsA += 1;
        if (sessionGetsA > PROJECT_SESSION_GETS) {
          await reloadGate.wait();
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            headers: { "Cache-Control": "no-store" },
            body: JSON.stringify({
              detail: {
                code: "editor_state_get_failed",
                message: SECRET_MARKER,
              },
            }),
          });
          return;
        }
      }
      await route.fallback();
    });

    try {
      await openBiz(page, BIZ_A);
      await loginViaUi(page);
      await expectBizReady(page, "商务甲");
      const panelA = page.getByTestId(BIZ_TESTID);
      await expect(panelA).toContainText(UPDATE_TEXT, { timeout: 10_000 });

      await panelA.getByRole("button", { name: RELOAD_BTN }).click();
      await expect.poll(() => reloadGate.waiterCount()).toBeGreaterThanOrEqual(1);

      state.getFail[BIZ_B] = 500;
      const getsBBeforeNav = getsFor(state, BIZ_B).length;
      await softNavigate(page, `/business-bid/${BIZ_B}`);
      await expect(
        page.getByTestId("business-editor-load-error"),
      ).toBeVisible({ timeout: 15_000 });
      await expect
        .poll(() => getsFor(state, BIZ_B).length)
        .toBeGreaterThan(getsBBeforeNav);

      const bodyBefore = await page.locator("body").innerText();
      expect(bodyBefore).not.toContain(RELOAD_FAIL_TEXT);
      expect(bodyBefore).not.toContain(SECRET_MARKER);
      expect(bodyBefore).not.toContain(eventId);
      expect(bodyBefore).not.toContain(newVersion);

      const getsBAtGate = getsFor(state, BIZ_B).length;
      const putsBAtGate = state.putLog.filter((id) => id === BIZ_B).length;

      reloadGate.release();

      const stableStart = Date.now();
      await expect
        .poll(async () => {
          const text = await page.locator("body").innerText();
          if (text.includes(RELOAD_FAIL_TEXT)) return -1;
          if (text.includes(SECRET_MARKER)) return -2;
          if (text.includes(eventId)) return -3;
          if (text.includes(newVersion)) return -4;
          if (getsFor(state, BIZ_B).length !== getsBAtGate) return -5;
          if (state.putLog.filter((id) => id === BIZ_B).length !== putsBAtGate) {
            return -6;
          }
          return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
        }, { timeout: 10_000 })
        .toBe(1);

      await expect(page.getByTestId("business-editor-load-error")).toBeVisible();
      const bodyAfter = await page.locator("body").innerText();
      expect(bodyAfter).not.toContain(RELOAD_FAIL_TEXT);
      expect(bodyAfter).not.toContain(SECRET_MARKER);
      expect(getsFor(state, BIZ_B).length).toBe(getsBAtGate);
      expect(state.putLog.filter((id) => id === BIZ_B)).toEqual([]);
    } finally {
      reloadGate.release();
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("边界证据：原生 EventSource 未注册命名 event 不可观测（非已修复）", async ({
    page,
  }) => {
    // 契约要求「其它事件→不可用」，但浏览器原生 EventSource 不会把
    // 未 addEventListener 的命名 event 投递给 onmessage；本用例证明该边界，
    // 不宣称已修复为不可用，须契约裁定。
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲", kind: "technical" }),
    ]);
    const namedId = allocateEventId(state);
    let live: LiveSseConn | null = null;
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
      onOpen: (c) => {
        live = c;
      },
    };

    const { sse } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲");
      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toBeVisible({ timeout: 15_000 });
      await expect.poll(() => live !== null).toBeTruthy();

      // 真实 SSE 帧：命名 event 未在组件注册
      live!.write(
        formatSseNamed(namedId, "foobar-unknown-event", {
          eventId: namedId,
          payload: SECRET_MARKER,
        }),
      );

      // 稳定窗口：面板既不因该帧变不可用，也不提示刷新
      const stableStart = Date.now();
      await expect
        .poll(async () => {
          const text = await panel.innerText();
          if (text.includes(UNAVAILABLE_TEXT)) return -1;
          if (text.includes(UPDATE_TEXT)) return -2;
          return Date.now() - stableStart >= ZERO_STREAM_STABLE_MS ? 1 : 0;
        }, { timeout: 10_000 })
        .toBe(1);
      await expect(panel).not.toContainText(UNAVAILABLE_TEXT);
      await expect(panel).not.toContainText(UPDATE_TEXT);
      await expect(panel).not.toContainText(SECRET_MARKER);
      await expect(panel).not.toContainText(namedId);

      // 对照：默认 message（无 event 字段）仍应不可用，证明 onmessage 路径存活
      live!.write(
        `id: ${allocateEventId(state)}\ndata: ${JSON.stringify({
          eventId: seedEventId(999),
        })}\n\n`,
      );
      await expect(panel).toContainText(UNAVAILABLE_TEXT, { timeout: 10_000 });
    } finally {
      await sse.close();
    }
  });
});
