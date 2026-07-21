/**
 * 模块：P13-I4 项目任务状态安全对账专项 E2E
 * 用途：验证 I3 task-event 触发后仅对当前 runTask 的 taskId 发起一次
 *       GET .../tasks/{taskId}/status；单飞、A→B/卸载迟到隔离；
 *       成功只改 status/progress 保留 message/result/error；
 *       失败/控制帧无后端原文；无详情/editor-state/轮询旁路；
 *       eventId 有界 FIFO 去重（容量 200）；无生产 lastTask 探针；
 *       Q-B1：同 task 迟到 status 不得把终态 lastTask 回退为 running。
 * 对接：Playwright chromium --workers=1 --retries=0；同源 route + 可挂起 SSE。
 * 二次开发：禁止源码字符串假绿、sleep 作完成证据；帧与 status 必须真实命中 route。
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
import { applySafeStatusProjection } from "../src/features/technical-plan/hooks/projectTaskStatus";
import type { PipelineTask } from "../src/features/technical-plan/hooks/useProjectPipeline";

const TECH_A = "proj_e2e_p13i4_tech_a";
const TECH_B = "proj_e2e_p13i4_tech_b";
const BIZ_A = "proj_e2e_p13i4_biz_a";

const TECH_TESTID = "technical-project-task-event-update";
const BIZ_TESTID = "business-project-task-event-update";

const UNAVAILABLE_TEXT = "项目任务提示暂不可用";
const CSRF_TOKEN = "e2e-p13i4-csrf-token-memory";
const SESSION_COOKIE_NAME = "biaoshu_e2e_sid";
const SESSION_COOKIE_VALUE = "p13i4_sess_opaque";
const E2E_LOGIN_USER = "e2e_p13i4_user";
const E2E_LOGIN_PASS = "E2e-Only-Fake-Pass!";
const SECRET_MARKER = "SECRET_P13I4_LEAK_MARKER_xyz";
const KEEP_MESSAGE = "LOCAL_KEEP_MSG_P13I4";
const KEEP_RESULT_FLAG = "keep_result_p13i4";
const KEEP_RESULT_DOC = "KEEP_RESULT_DOC_P13I4";
const KEEP_ERROR = "LOCAL_KEEP_ERR_P13I4";

const ZERO_STABLE_MS = 400;
/** 与服务端/生产 acceptedEventIds FIFO 容量一致 */
const ACCEPTED_EVENT_ID_CAPACITY = 200;

type Kind = "technical" | "business";
type AuthRole = "bid_writer" | "finance";

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
  projectId: string;
  path: string;
  method: string;
  search: string;
};

type StatusHit = {
  projectId: string;
  taskId: string;
  path: string;
  method: string;
  search: string;
  at: number;
  /** 本请求是否在 fulfill 前被客户端 Abort / 路由取消 */
  aborted: boolean;
  /** 进入 handler 时的在途数（含自身） */
  activeAtStart: number;
};

/** status GET 并发与取消可观测面（Q2） */
type StatusMetrics = {
  active: number;
  maxConcurrent: number;
  abortCount: number;
  abortLog: Array<{ projectId: string; taskId: string; at: number; path: string }>;
};

type StreamFramePlan =
  | { kind: "live"; initial?: string[]; onOpen?: (conn: LiveSseConn) => void }
  | { kind: "sse"; frames: string[]; holdOpen?: boolean };

type LiveSseConn = {
  projectId: string;
  write: (chunk: string) => void;
  end: () => void;
  closed: () => boolean;
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
  taskListLog: string[];
  filesLog: string[];
  statusLog: StatusHit[];
  statusMetrics: StatusMetrics;
  streamLog: StreamHit[];
  streamMode: Record<string, StreamFramePlan>;
  defaultStreamMode: StreamFramePlan;
  forbiddenHits: string[];
  externalHits: string[];
  liveConns: LiveSseConn[];
  /** 当前 runTask 创建的任务 */
  activeTasks: Record<string, TaskRecord>;
  /** status 响应控制：projectId/taskId → 计划 */
  statusPlan: Record<
    string,
    | { kind: "ok"; status: string; progress: number; gate?: HoldGate; delayMs?: number }
    | { kind: "http"; code: number; body: unknown; gate?: HoldGate }
  >;
  /** 挂起 per-task SSE，避免 runTask 回退轮询 */
  holdTaskEventStreams: boolean;
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
      while (waiters.length > 0) waiters.shift()?.();
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
function formatSseControl(
  event: "cursor-stale" | "unavailable",
  data: Record<string, unknown>,
): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
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

function emptyEditor(
  projectId: string,
  kind: Kind,
  stateVersion: string,
): EditorState {
  return {
    projectId,
    outline: [],
    chapters: [],
    facts: [],
    mode: kind === "technical" ? "analysis" : "business",
    analysisOverview: "P13I4 概述",
    analysis: {
      overview: "P13I4 概述",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: kind === "business" ? "P13I4 商务正文" : "",
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
    taskListLog: [],
    filesLog: [],
    statusLog: [],
    statusMetrics: {
      active: 0,
      maxConcurrent: 0,
      abortCount: 0,
      abortLog: [],
    },
    streamLog: [],
    streamMode: {},
    defaultStreamMode: { kind: "live", initial: [] },
    forbiddenHits: [],
    externalHits: [],
    liveConns: [],
    activeTasks: {},
    statusPlan: {},
    holdTaskEventStreams: true,
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
  /** 向 runTask 的 per-task SSE 写入真实 task/snapshot 帧 */
  writeToPerTask: (
    projectId: string,
    taskId: string,
    chunk: string,
  ) => number;
  activeCount: (projectId?: string) => number;
  perTaskActiveCount: (projectId?: string, taskId?: string) => number;
};

async function startSseMockServer(state: ProbeState): Promise<SseMockServer> {
  type Conn = {
    projectId: string;
    taskId?: string;
    kind: "task" | "editor" | "task-events";
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
      const perTaskMatch = url.pathname.match(
        /^\/api\/projects\/([^/]+)\/tasks\/([^/]+)\/events\/?$/,
      );
      if (
        (!taskMatch && !editorMatch && !perTaskMatch) ||
        (req.method ?? "GET").toUpperCase() !== "GET"
      ) {
        res.statusCode = 404;
        res.end("not found");
        return;
      }

      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-store",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      });

      if (perTaskMatch) {
        const projectId = decodeURIComponent(perTaskMatch[1]);
        const taskId = decodeURIComponent(perTaskMatch[2]);
        const conn: Conn = {
          projectId,
          taskId,
          kind: "task-events",
          res,
          closed: false,
        };
        conns.push(conn);
        req.on("close", () => {
          conn.closed = true;
        });
        // 保持挂起，防止 runTask 回退 GET 轮询；可再 writeToPerTask 推终态
        res.write(": hold\n\n");
        return;
      }

      const projectId = decodeURIComponent((taskMatch ?? editorMatch)![1]);
      const streamKind: "task" | "editor" = taskMatch ? "task" : "editor";
      const plan =
        streamKind === "task"
          ? (state.streamMode[projectId] ?? state.defaultStreamMode)
          : ({ kind: "live", initial: [] } as StreamFramePlan);

      const conn: Conn = { projectId, kind: streamKind, res, closed: false };
      conns.push(conn);
      req.on("close", () => {
        conn.closed = true;
      });

      if (plan.kind === "sse") {
        for (const f of plan.frames) {
          if (!conn.closed) res.write(f);
        }
        if (plan.holdOpen === false) {
          res.end();
          conn.closed = true;
        }
        return;
      }

      if (plan.initial?.length) {
        for (const f of plan.initial) {
          if (!conn.closed) res.write(f);
        }
      }
      if (streamKind === "task") {
        const live: LiveSseConn = {
          projectId,
          write: (chunk) => {
            if (!conn.closed) res.write(chunk);
          },
          end: () => {
            if (!conn.closed) {
              res.end();
              conn.closed = true;
            }
          },
          closed: () => conn.closed,
        };
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
    writeToPerTask: (projectId, taskId, chunk) => {
      let n = 0;
      for (const c of conns) {
        if (
          c.kind === "task-events" &&
          !c.closed &&
          c.projectId === projectId &&
          c.taskId === taskId
        ) {
          c.res.write(chunk);
          n += 1;
        }
      }
      return n;
    },
    activeCount: (projectId) =>
      conns.filter(
        (c) =>
          !c.closed &&
          c.kind === "task" &&
          (projectId === undefined || c.projectId === projectId),
      ).length,
    perTaskActiveCount: (projectId, taskId) =>
      conns.filter(
        (c) =>
          !c.closed &&
          c.kind === "task-events" &&
          (projectId === undefined || c.projectId === projectId) &&
          (taskId === undefined || c.taskId === taskId),
      ).length,
  };
}

/** runTask per-task SSE 的 task/snapshot 帧（与 waitForTaskEvents 一致） */
function perTaskPipelineFrame(task: TaskRecord): string {
  return formatSseNamed(task.id, "task", {
    id: task.id,
    projectId: task.projectId,
    type: task.type,
    status: task.status,
    progress: task.progress,
    message: task.message,
    result: task.result ?? null,
    error: task.error ?? null,
  });
}

async function installRoutes(
  page: Page,
  state: ProbeState,
  sse: SseMockServer,
) {
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

    const taskStreamMatch = path.match(
      /^\/api\/projects\/([^/]+)\/task-events\/stream\/?$/,
    );
    if (taskStreamMatch && method === "GET") {
      const projectId = decodeURIComponent(taskStreamMatch[1]);
      state.streamLog.push({
        projectId,
        path,
        method,
        search: url.search,
      });
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/task-events/stream`;
      await route.continue({ url: target });
      return;
    }

    const editorStreamMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-events\/stream\/?$/,
    );
    if (editorStreamMatch && method === "GET") {
      const projectId = decodeURIComponent(editorStreamMatch[1]);
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/editor-state-events/stream`;
      await route.continue({ url: target });
      return;
    }

    const perTaskEvents = path.match(
      /^\/api\/projects\/([^/]+)\/tasks\/([^/]+)\/events\/?$/,
    );
    if (perTaskEvents && method === "GET" && state.holdTaskEventStreams) {
      const projectId = decodeURIComponent(perTaskEvents[1]);
      const taskId = decodeURIComponent(perTaskEvents[2]);
      const target = `http://127.0.0.1:${sse.port}/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}/events`;
      await route.continue({ url: target });
      return;
    }

    // I4 安全状态（可观测 active / maxConcurrent / abort，供 Q2 断言）
    const statusMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks\/([^/]+)\/status\/?$/,
    );
    if (statusMatch && method === "GET") {
      const projectId = decodeURIComponent(statusMatch[1]);
      const taskId = decodeURIComponent(statusMatch[2]);
      const metrics = state.statusMetrics;
      metrics.active += 1;
      metrics.maxConcurrent = Math.max(metrics.maxConcurrent, metrics.active);
      const hit: StatusHit = {
        projectId,
        taskId,
        path,
        method,
        search: url.search,
        at: Date.now(),
        aborted: false,
        activeAtStart: metrics.active,
      };
      state.statusLog.push(hit);

      const key = `${projectId}/${taskId}`;
      const plan = state.statusPlan[key] ?? {
        kind: "ok" as const,
        status: "running",
        progress: 77,
      };

      /** 客户端 Abort 时 Playwright 会 requestfailed；与 gate 竞态以便释放在途 */
      let abortedByClient = false;
      const markAborted = () => {
        if (abortedByClient) return;
        abortedByClient = true;
        hit.aborted = true;
        metrics.abortCount += 1;
        metrics.abortLog.push({
          projectId,
          taskId,
          at: Date.now(),
          path,
        });
      };
      const onRequestFailed = (failed: import("@playwright/test").Request) => {
        if (failed.url() === rawUrl) markAborted();
      };
      page.on("requestfailed", onRequestFailed);

      try {
        if (plan.gate) {
          await Promise.race([
            plan.gate.wait(),
            new Promise<void>((resolve) => {
              const poll = () => {
                if (abortedByClient) {
                  resolve();
                  return;
                }
                setTimeout(poll, 20);
              };
              poll();
            }),
          ]);
        }
        if (abortedByClient) {
          try {
            await route.abort("failed");
          } catch {
            /* 可能已被取消 */
          }
          return;
        }
        if ("delayMs" in plan && plan.delayMs) {
          const delayMs = plan.delayMs;
          await Promise.race([
            new Promise<void>((r) => setTimeout(r, delayMs)),
            new Promise<void>((resolve) => {
              const poll = () => {
                if (abortedByClient) {
                  resolve();
                  return;
                }
                setTimeout(poll, 20);
              };
              poll();
            }),
          ]);
        }
        if (abortedByClient) {
          try {
            await route.abort("failed");
          } catch {
            /* ignore */
          }
          return;
        }
        if (plan.kind === "http") {
          await json(route, plan.body, plan.code);
          return;
        }
        await json(route, {
          taskId,
          status: plan.status,
          progress: plan.progress,
        });
      } catch {
        markAborted();
        try {
          await route.abort("failed");
        } catch {
          /* ignore */
        }
      } finally {
        page.off("requestfailed", onRequestFailed);
        metrics.active = Math.max(0, metrics.active - 1);
      }
      return;
    }

    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p13i4_forbidden", message: SECRET_MARKER } },
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
      await json(route, { detail: { code: "p13i4_no_create" } }, 403);
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
        await json(
          route,
          state.editorById[id] ??
            emptyEditor(id, "technical", seedStateVersion(1)),
        );
        return;
      }
      state.putLog.push(id);
      await json(
        route,
        state.editorById[id] ??
          emptyEditor(id, "technical", seedStateVersion(1)),
      );
      return;
    }

    const filesMatch = path.match(/^\/api\/projects\/([^/]+)\/files\/?$/);
    if (filesMatch && method === "GET") {
      state.filesLog.push(decodeURIComponent(filesMatch[1]));
      // 返回假文件，解除「轻量解析」disabled（files.length===0）
      await json(route, [
        {
          id: "file_e2e_p13i4",
          filename: "sample-bid.pdf",
          sizeBytes: 1024,
          createdAt: "2026-07-21T12:00:00",
        },
      ]);
      return;
    }

    // 任务列表 / 创建 / 详情
    const taskMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks(?:\/([^/]+))?(?:\/(events|cancel))?\/?$/,
    );
    if (taskMatch && (method === "GET" || method === "POST")) {
      const pid = decodeURIComponent(taskMatch[1]);
      const tid = taskMatch[2] ? decodeURIComponent(taskMatch[2]) : "";
      const sub = taskMatch[3] || "";
      if (method === "POST" && !tid) {
        const body = req.postDataJSON() as {
          type?: string;
          payload?: Record<string, unknown>;
        };
        const id = allocateTaskId(state);
        const row: TaskRecord = {
          id,
          projectId: pid,
          type: body?.type || "parse",
          status: "running",
          progress: 12,
          message: KEEP_MESSAGE,
          result: {
            [KEEP_RESULT_FLAG]: true,
            note: "preserve-me",
            // 既有 TaskProgress UI 会展示 kbCitations，供 Q3 浏览器可观察面
            kbCitations: [
              {
                docName: KEEP_RESULT_DOC,
                title: KEEP_RESULT_FLAG,
                excerpt: "preserve-excerpt",
              },
            ],
          },
          error: KEEP_ERROR,
        };
        state.activeTasks[id] = row;
        await json(route, row, 201);
        return;
      }
      if (method === "GET" && !tid) {
        state.taskListLog.push(pid);
        await json(route, Object.values(state.activeTasks));
        return;
      }
      if (method === "GET" && tid && !sub) {
        state.taskDetailLog.push(`${method} ${pid} ${tid}`);
        const row = state.activeTasks[tid] ?? {
          id: tid,
          projectId: pid,
          type: "parse",
          status: "running",
          progress: 0,
          message: SECRET_MARKER,
          error: SECRET_MARKER,
          result: { leak: SECRET_MARKER },
        };
        await json(route, row);
        return;
      }
      if (method === "POST" && sub === "cancel") {
        await json(route, {
          id: tid,
          status: "cancelled",
          type: "parse",
          progress: 0,
          message: "cancelled",
        });
        return;
      }
      state.taskDetailLog.push(`${method} ${pid} ${tid}/${sub}`);
      await json(route, { detail: SECRET_MARKER }, 404);
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
    await json(route, { detail: { code: "p13i4_unhandled" } }, 404);
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

async function openTech(page: Page, projectId: string) {
  // document 步含「轻量解析」；analysis 无该按钮
  await page.goto(`/technical-plan/${projectId}/document`);
}
async function openBiz(page: Page, projectId: string) {
  await page.goto(`/business-bid/${projectId}/parse`);
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

async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function setupPage(page: Page, state: ProbeState) {
  const sse = await startSseMockServer(state);
  const consoleLines = collectConsole(page);
  await installRoutes(page, state, sse);
  return { sse, consoleLines };
}

async function startLightweightParse(page: Page) {
  const btn = page.getByRole("button", { name: "轻量解析" });
  await expect(btn).toBeVisible({ timeout: 15_000 });
  await expect(btn).toBeEnabled({ timeout: 15_000 });
  await btn.click();
}

async function waitLastTaskMessage(page: Page, message: string) {
  await expect
    .poll(async () => page.locator("body").innerText(), { timeout: 20_000 })
    .toContain(message);
}

function liveFor(state: ProbeState, projectId: string): LiveSseConn | null {
  return (
    state.liveConns.filter((c) => c.projectId === projectId && !c.closed()).at(-1) ??
    null
  );
}

test.describe.configure({ mode: "serial" });

test.describe("P13-I4 项目任务状态安全对账", () => {
  test("Q1: 同 eventId 重放 status 精确 1 次；非匹配零请求；在途单飞", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4", kind: "technical" }),
    ]);
    const cursorId = allocateEventId(state);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(cursorId)],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4");
      await expect.poll(() => state.streamLog.length).toBeGreaterThanOrEqual(1);

      const editorGetsBefore = state.getLog.length;
      const detailBefore = state.taskDetailLog.length;
      const filesBefore = state.filesLog.length;
      const putBefore = state.putLog.length;

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);

      const created = Object.values(state.activeTasks);
      expect(created.length).toBe(1);
      const taskId = created[0].id;
      const otherTaskId = allocateTaskId(state);

      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 64,
      };

      const statusBefore = state.statusLog.length;
      const live = liveFor(state, TECH_A);
      expect(live, "项目 task-events 流必须已连接").toBeTruthy();

      // 其它 task：零 status
      live!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId: otherTaskId,
          progress: 33,
        }),
      );
      await page.getByTestId(TECH_TESTID).getByText("解析").waitFor({
        state: "visible",
        timeout: 10_000,
      });
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore,
        ZERO_STABLE_MS,
      );

      // 匹配 task：第一次合法 eventId → 精确 1 次 status
      const matchedEventId = allocateEventId(state);
      live!.write(
        taskEventFrame({
          eventId: matchedEventId,
          taskId,
          progress: 55,
          status: "running",
        }),
      );
      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(statusBefore + 1);
      const hit = state.statusLog[statusBefore];
      expect(hit.method).toBe("GET");
      expect(hit.projectId).toBe(TECH_A);
      expect(hit.taskId).toBe(taskId);
      expect(hit.search).toBe("");
      expect(hit.path).toBe(
        `/api/projects/${TECH_A}/tasks/${taskId}/status`,
      );
      expect(hit.aborted).toBe(false);

      // 第一次 status 完成后任务仍 running
      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/parse · running · 64%/);
      await expect
        .poll(async () => page.locator("body").innerText())
        .toContain(KEEP_MESSAGE);

      // Q1 核心：同一合法 eventId 重放，statusLog 必须仍精确 1 次（禁止两个新 eventId 冒充）
      live!.write(
        taskEventFrame({
          eventId: matchedEventId,
          taskId,
          progress: 55,
          status: "running",
        }),
      );
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 1,
        ZERO_STABLE_MS,
      );

      // 旁路：对账不得触发详情/editor put/文件刷新增量
      expect(state.taskDetailLog.length).toBe(detailBefore);
      expect(state.getLog.length).toBe(editorGetsBefore);
      expect(state.putLog.length).toBe(putBefore);
      expect(state.filesLog.length).toBe(filesBefore);
      expect(state.statusMetrics.maxConcurrent).toBeLessThanOrEqual(1);

      // 在途单飞：不同 eventId 但同 task，请求未完成时不得叠加
      const gate = createHoldGate();
      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 88,
        gate,
      };
      const mid = state.statusLog.length;
      const eidInflight = allocateEventId(state);
      live!.write(
        taskEventFrame({
          eventId: eidInflight,
          taskId,
          progress: 70,
        }),
      );
      await expect
        .poll(() => gate.waiterCount(), { timeout: 10_000 })
        .toBeGreaterThanOrEqual(1);
      live!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId,
          progress: 71,
        }),
      );
      await waitStableExactCount(
        () => state.statusLog.length,
        mid + 1,
        ZERO_STABLE_MS,
      );
      expect(state.statusMetrics.maxConcurrent).toBeLessThanOrEqual(1);
      gate.release();
      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/running · 88%/);

      const leaks = appConsoleLines(consoleLines).filter((l) =>
        l.includes(SECRET_MARKER),
      );
      expect(leaks).toEqual([]);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("Q2: A status 挂起切 B 后须 Abort；任一时刻最多一个 status", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4b", kind: "technical" }),
      makeProject({ id: TECH_B, name: "技术乙I4b", kind: "technical" }),
    ]);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };
    state.streamMode[TECH_B] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4b");

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      const taskA = Object.values(state.activeTasks)[0].id;

      const gateA = createHoldGate();
      state.statusPlan[`${TECH_A}/${taskA}`] = {
        kind: "ok",
        status: "running",
        progress: 91,
        gate: gateA,
      };

      const liveA = liveFor(state, TECH_A)!;
      liveA.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId: taskA,
          progress: 40,
        }),
      );
      await expect.poll(() => gateA.waiterCount()).toBeGreaterThanOrEqual(1);
      expect(state.statusMetrics.active).toBe(1);
      const statusAfterA = state.statusLog.length;

      // 切到 B：A 须被 Abort；不得等 gate 释放才清在途
      await softNavigate(page, `/technical-plan/${TECH_B}/document`);
      await expectTechReady(page, "技术乙I4b");

      await expect
        .poll(() => state.statusMetrics.abortCount, { timeout: 10_000 })
        .toBeGreaterThanOrEqual(1);
      await expect
        .poll(() => state.statusLog.some((h) => h.taskId === taskA && h.aborted))
        .toBe(true);
      await expect
        .poll(() => state.statusMetrics.active, { timeout: 10_000 })
        .toBe(0);

      // B 真实 runTask + 匹配事件；开始 B 对账前 A 已不在途
      state.activeTasks = {};
      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      const taskB = Object.values(state.activeTasks)[0].id;
      expect(taskB).not.toBe(taskA);

      // 证明：B 对账启动前 A 已 abort 且 active=0
      expect(state.statusMetrics.active).toBe(0);
      expect(
        state.statusLog.filter((h) => h.taskId === taskA && h.aborted).length,
      ).toBeGreaterThanOrEqual(1);

      state.statusPlan[`${TECH_B}/${taskB}`] = {
        kind: "ok",
        status: "running",
        progress: 33,
      };
      const beforeB = state.statusLog.length;
      const liveB = liveFor(state, TECH_B);
      expect(liveB, "B 项目 task-events 流必须已连接").toBeTruthy();
      liveB!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId: taskB,
          progress: 20,
        }),
      );
      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(beforeB + 1);
      const hitB = state.statusLog[beforeB];
      expect(hitB.projectId).toBe(TECH_B);
      expect(hitB.taskId).toBe(taskB);
      expect(hitB.aborted).toBe(false);
      // 任一时刻最多一个 status（含 A 挂起与 B 对账全过程）
      expect(state.statusMetrics.maxConcurrent).toBe(1);

      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/running · 33%/);
      await expect(page.locator("body")).not.toContainText("91%");

      // 迟到释放 A gate 不得再污染 / 不得新增 A status
      const statusBeforeRelease = state.statusLog.length;
      gateA.release();
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBeforeRelease,
        ZERO_STABLE_MS,
      );
      expect(state.statusLog.length).toBeGreaterThanOrEqual(statusAfterA);
      await expect(page.locator("body")).not.toContainText("91%");

      expect(
        appConsoleLines(consoleLines).filter((l) => l.includes(SECRET_MARKER)),
      ).toEqual([]);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("Q3-pure: applySafeStatusProjection 只改 status/progress 且不 mutation", () => {
    const resultObj: Record<string, unknown> = {
      [KEEP_RESULT_FLAG]: true,
      note: "preserve-me",
      kbCitations: [
        {
          docName: KEEP_RESULT_DOC,
          title: KEEP_RESULT_FLAG,
          excerpt: "preserve-excerpt",
        },
      ],
    };
    const current: PipelineTask = {
      id: "task_0123456789abcdef",
      projectId: TECH_A,
      type: "parse",
      status: "running",
      progress: 12,
      message: KEEP_MESSAGE,
      result: resultObj,
      error: KEEP_ERROR,
    };
    const resultRef = current.result;
    const errorRef = current.error;

    const next = applySafeStatusProjection(current, {
      status: "running",
      progress: 64,
    });

    expect(next).not.toBe(current);
    expect(next.status).toBe("running");
    expect(next.progress).toBe(64);
    expect(next.message).toBe(KEEP_MESSAGE);
    expect(next.error).toBe(KEEP_ERROR);
    expect(next.result).toBe(resultRef);
    expect(next.error).toBe(errorRef);
    expect(next.result).toEqual(resultObj);
    expect(next.id).toBe(current.id);
    expect(next.projectId).toBe(current.projectId);
    expect(next.type).toBe(current.type);

    // 原对象不被 mutation
    expect(current.status).toBe("running");
    expect(current.progress).toBe(12);
    expect(current.message).toBe(KEEP_MESSAGE);
    expect(current.result).toBe(resultObj);
    expect(current.error).toBe(KEEP_ERROR);
    expect(current.result).toEqual({
      [KEEP_RESULT_FLAG]: true,
      note: "preserve-me",
      kbCitations: [
        {
          docName: KEEP_RESULT_DOC,
          title: KEEP_RESULT_FLAG,
          excerpt: "preserve-excerpt",
        },
      ],
    });
  });

  test("Q3: 成功对账后浏览器面保留 message 与 result.kbCitations", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4c", kind: "technical" }),
    ]);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4c");

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      // 既有 UI：知识库引用来自 lastTask.result.kbCitations
      await expect(page.getByText(KEEP_RESULT_DOC)).toBeVisible({
        timeout: 10_000,
      });

      const taskId = Object.values(state.activeTasks)[0].id;
      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 64,
      };

      liveFor(state, TECH_A)!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId,
          progress: 55,
          status: "running",
        }),
      );

      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/parse · running · 64%/);

      // 浏览器真实可观察面：message + result.kbCitations；禁止 mock activeTasks / 探针
      const bodyText = await page.locator("body").innerText();
      expect(bodyText).toContain(KEEP_MESSAGE);
      expect(bodyText).toContain(KEEP_RESULT_DOC);
      expect(bodyText).toContain(KEEP_RESULT_FLAG);
      // 不得显示 error，也不得摊敏感标记
      expect(bodyText).not.toContain(KEEP_ERROR);
      expect(bodyText).not.toContain(SECRET_MARKER);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("Q6: eventId FIFO 容量淘汰后重放首 ID 再触发 1 次 status；窗内零增量", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4f", kind: "technical" }),
    ]);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4f");

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      const taskId = Object.values(state.activeTasks)[0].id;
      const otherTaskId = allocateTaskId(state);

      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 71,
      };

      const live = liveFor(state, TECH_A);
      expect(live, "项目 task-events 流必须已连接").toBeTruthy();
      const statusBefore = state.statusLog.length;

      // 1) 当前 task 首 eventId → 精确 1 次 status
      const firstEventId = allocateEventId(state);
      live!.write(
        taskEventFrame({
          eventId: firstEventId,
          taskId,
          progress: 40,
          status: "running",
        }),
      );
      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(statusBefore + 1);
      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/parse · running · 71%/);

      // 2) 再写入足够多不同合法 eventId，使首 ID 被 FIFO 淘汰；其它 taskId 避免额外 status
      const recentEventIds: string[] = [];
      let floodChunk = "";
      for (let i = 0; i < ACCEPTED_EVENT_ID_CAPACITY; i += 1) {
        const eid = allocateEventId(state);
        recentEventIds.push(eid);
        const progress =
          i === ACCEPTED_EVENT_ID_CAPACITY - 1 ? 99 : (i % 50) + 1;
        floodChunk += taskEventFrame({
          eventId: eid,
          taskId: otherTaskId,
          progress,
          status: "running",
        });
      }
      live!.write(floodChunk);
      // 末帧进入面板即可证明有序处理完（含 FIFO 淘汰）
      await expect
        .poll(async () => page.getByTestId(TECH_TESTID).innerText(), {
          timeout: 30_000,
        })
        .toContain("99%");
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 1,
        ZERO_STABLE_MS,
      );

      // 3) 重放已被淘汰的首 ID：视为窗口外新事件，再触发恰好 1 次 status
      live!.write(
        taskEventFrame({
          eventId: firstEventId,
          taskId,
          progress: 41,
          status: "running",
        }),
      );
      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(statusBefore + 2);
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 2,
        ZERO_STABLE_MS,
      );
      expect(state.statusLog[statusBefore + 1].taskId).toBe(taskId);
      expect(state.statusLog[statusBefore + 1].aborted).toBe(false);

      // 4) 窗口内最近 ID 重放必须零 status（行为证明，禁止只数 Set）
      const recentInWindow = recentEventIds[recentEventIds.length - 1];
      live!.write(
        taskEventFrame({
          eventId: recentInWindow,
          taskId: otherTaskId,
          progress: 99,
          status: "running",
        }),
      );
      // 首 ID 重入窗口后再次重放：匹配 task 也必须零增量
      live!.write(
        taskEventFrame({
          eventId: firstEventId,
          taskId,
          progress: 41,
          status: "running",
        }),
      );
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 2,
        ZERO_STABLE_MS,
      );

      expect(
        appConsoleLines(consoleLines).filter((l) => l.includes(SECRET_MARKER)),
      ).toEqual([]);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("Q4: status 500 后 I3 面板保留安全文案；旁路计数不变", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4d", kind: "technical" }),
    ]);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4d");

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      const taskId = Object.values(state.activeTasks)[0].id;

      const editorGetsBefore = state.getLog.length;
      const detailBefore = state.taskDetailLog.length;
      const filesBefore = state.filesLog.length;
      const putBefore = state.putLog.length;
      const statusBefore = state.statusLog.length;

      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "http",
        code: 500,
        body: { detail: { message: SECRET_MARKER, stack: SECRET_MARKER } },
      };

      const live = liveFor(state, TECH_A)!;
      live.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId,
          taskType: "parse",
          status: "running",
          progress: 22,
        }),
      );

      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(statusBefore + 1);

      // I3 面板仍保留该合法 task-event 的固定安全类型/状态/进度，无后端原文
      const panel = page.getByTestId(TECH_TESTID);
      await expect(panel).toContainText("解析");
      await expect(panel).toContainText("进行中");
      await expect(panel).toContainText("22%");
      const panelText = await panel.innerText();
      expect(panelText).not.toContain(SECRET_MARKER);
      expect(panelText).not.toContain(taskId);
      expect(panelText).not.toContain("500");

      const body = await page.locator("body").innerText();
      expect(body).not.toContain(SECRET_MARKER);
      expect(body).toContain(KEEP_MESSAGE);

      // task detail / editor-state / files / PUT / 额外 status 旁路计数不变
      expect(state.taskDetailLog.length).toBe(detailBefore);
      expect(state.getLog.length).toBe(editorGetsBefore);
      expect(state.putLog.length).toBe(putBefore);
      expect(state.filesLog.length).toBe(filesBefore);
      expect(state.statusLog.length).toBe(statusBefore + 1);

      // 控制帧 unavailable：固定文案，无 SECRET
      live.write(
        formatSseControl("unavailable", {
          code: "project_task_event_unavailable",
          message: SECRET_MARKER,
        }),
      );
      await expect(panel).toContainText(UNAVAILABLE_TEXT);
      expect(await panel.innerText()).not.toContain(SECRET_MARKER);

      expect(
        appConsoleLines(consoleLines).filter((l) => l.includes(SECRET_MARKER)),
      ).toEqual([]);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("商务标：匹配 task 触发 status；非匹配零请求", async ({ page }) => {
    const state = createProbeState([
      makeProject({ id: BIZ_A, name: "商务甲I4", kind: "business" }),
    ]);
    state.streamMode[BIZ_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };
    const { sse } = await setupPage(page, state);
    try {
      await openBiz(page, BIZ_A);
      await loginViaUi(page);
      await expectBizReady(page, "商务甲I4");

      const parseBtn = page.getByRole("button", { name: /整段重解析/ });
      await expect(parseBtn).toBeVisible({ timeout: 15_000 });
      await expect(parseBtn).toBeEnabled({ timeout: 15_000 });
      await parseBtn.click();
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      const taskId = Object.values(state.activeTasks)[0].id;
      state.statusPlan[`${BIZ_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 41,
      };

      const before = state.statusLog.length;
      liveFor(state, BIZ_A)!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId: allocateTaskId(state),
          taskType: "biz_qualify",
          progress: 10,
        }),
      );
      await page.getByTestId(BIZ_TESTID).getByText("资格审查").waitFor({
        timeout: 10_000,
      });
      await waitStableExactCount(
        () => state.statusLog.length,
        before,
        ZERO_STABLE_MS,
      );

      liveFor(state, BIZ_A)!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId,
          taskType: "parse",
          progress: 20,
        }),
      );
      await expect
        .poll(() => state.statusLog.length)
        .toBe(before + 1);
      expect(state.statusLog.at(-1)?.path).toBe(
        `/api/projects/${BIZ_A}/tasks/${taskId}/status`,
      );
      await expect
        .poll(async () => page.locator("body").innerText())
        .toMatch(/running · 41%/);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("Q-B1: 同 task 迟到 running status 不得回退终态 success/100", async ({
    page,
  }) => {
    /**
     * 真实时序红测（禁止纯函数冒充）：
     * 1) runTask 后 per-task SSE 保持挂起；
     * 2) 合法 project task-event 触发 status GET，用 gate 挂起；
     * 3) 经既有 per-task SSE 把 lastTask 推为 success/100（保留 message/result/error）；
     * 4) 释放旧 running status；最终仍须 success/100，绝不回到 running；
     * 5) 释放后无额外 status/detail/editor/files/PUT/console 泄漏。
     */
    const state = createProbeState([
      makeProject({ id: TECH_A, name: "技术甲I4-QB1", kind: "technical" }),
    ]);
    state.streamMode[TECH_A] = {
      kind: "live",
      initial: [cursorFrame(allocateEventId(state))],
    };

    const { sse, consoleLines } = await setupPage(page, state);
    try {
      await openTech(page, TECH_A);
      await loginViaUi(page);
      await expectTechReady(page, "技术甲I4-QB1");

      await startLightweightParse(page);
      await waitLastTaskMessage(page, KEEP_MESSAGE);
      await expect(page.getByText(KEEP_RESULT_DOC)).toBeVisible({
        timeout: 10_000,
      });

      const created = Object.values(state.activeTasks);
      expect(created.length).toBe(1);
      const taskRow = created[0];
      const taskId = taskRow.id;

      // per-task SSE 须已挂起（runTask 可观察路径）
      await expect
        .poll(() => sse.perTaskActiveCount(TECH_A, taskId), {
          timeout: 10_000,
        })
        .toBeGreaterThanOrEqual(1);

      const lateGate = createHoldGate();
      state.statusPlan[`${TECH_A}/${taskId}`] = {
        kind: "ok",
        status: "running",
        progress: 37,
        gate: lateGate,
      };

      const editorGetsBefore = state.getLog.length;
      const detailBefore = state.taskDetailLog.length;
      const filesBefore = state.filesLog.length;
      const putBefore = state.putLog.length;
      const statusBefore = state.statusLog.length;

      const live = liveFor(state, TECH_A);
      expect(live, "项目 task-events 流必须已连接").toBeTruthy();
      live!.write(
        taskEventFrame({
          eventId: allocateEventId(state),
          taskId,
          progress: 55,
          status: "running",
        }),
      );

      // status GET 进入 gate 挂起
      await expect
        .poll(() => lateGate.waiterCount(), { timeout: 10_000 })
        .toBeGreaterThanOrEqual(1);
      await expect
        .poll(() => state.statusLog.length, { timeout: 10_000 })
        .toBe(statusBefore + 1);
      expect(state.statusLog[statusBefore].taskId).toBe(taskId);
      expect(state.statusLog[statusBefore].aborted).toBe(false);
      expect(state.statusMetrics.active).toBe(1);

      // 经 per-task SSE 真实 pipeline 路径把 lastTask 推为 success（保留 message/result/error）
      const successTask: TaskRecord = {
        ...taskRow,
        status: "success",
        progress: 100,
        message: KEEP_MESSAGE,
        result: taskRow.result,
        error: KEEP_ERROR,
      };
      state.activeTasks[taskId] = successTask;
      const written = sse.writeToPerTask(
        TECH_A,
        taskId,
        perTaskPipelineFrame(successTask),
      );
      expect(written, "须写入至少一条 per-task SSE 连接").toBeGreaterThanOrEqual(
        1,
      );

      await expect
        .poll(async () => page.locator("body").innerText(), {
          timeout: 15_000,
        })
        .toMatch(/parse · success · 100%/);
      await expect
        .poll(async () => page.locator("body").innerText())
        .toContain(KEEP_MESSAGE);
      await expect(page.getByText(KEEP_RESULT_DOC)).toBeVisible();
      // runLightweightParse 成功后会 reloadFromApi；等提示落稳再取旁路基线
      await expect(page.getByText("解析完成，请查看右侧预览")).toBeVisible({
        timeout: 15_000,
      });
      // 终态后、释放迟到 status 前：UI 绝不能仍是 running
      await expect(page.locator("body")).not.toContainText("running · 37%");

      // 旁路基线：status 精确 1 且稳定；editor 允许 pipeline 成功后的一次重载
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 1,
        ZERO_STABLE_MS,
      );
      await expect
        .poll(() => state.getLog.length, { timeout: 10_000 })
        .toBeGreaterThanOrEqual(editorGetsBefore);
      const editorAfterSuccess = state.getLog.length;
      await waitStableExactCount(
        () => state.getLog.length,
        editorAfterSuccess,
        ZERO_STABLE_MS,
      );
      const detailAfterSuccess = state.taskDetailLog.length;
      const filesAfterSuccess = state.filesLog.length;
      const putAfterSuccess = state.putLog.length;
      expect(state.statusLog.length).toBe(statusBefore + 1);

      // 释放旧 running status：终态不得回退
      lateGate.release();
      await expect
        .poll(() => state.statusMetrics.active, { timeout: 10_000 })
        .toBe(0);
      // 给迟到合并一个稳定窗：仍须 success/100
      await expect
        .poll(async () => page.locator("body").innerText(), {
          timeout: 10_000,
        })
        .toMatch(/parse · success · 100%/);
      await waitStableExactCount(
        () => state.statusLog.length,
        statusBefore + 1,
        ZERO_STABLE_MS,
      );

      const bodyAfter = await page.locator("body").innerText();
      expect(bodyAfter).toMatch(/parse · success · 100%/);
      expect(bodyAfter).toContain(KEEP_MESSAGE);
      expect(bodyAfter).toContain(KEEP_RESULT_DOC);
      expect(bodyAfter).toContain(KEEP_RESULT_FLAG);
      // 绝不回到 running / 迟到 progress
      expect(bodyAfter).not.toMatch(/parse · running ·/);
      expect(bodyAfter).not.toContain("37%");
      expect(bodyAfter).not.toContain(KEEP_ERROR);
      expect(bodyAfter).not.toContain(SECRET_MARKER);

      // 释放迟到 status 后：对账侧不得再增 status/detail/editor/files/PUT
      // （pipeline 终态可能已合法重载 editor-state，故相对终态后基线断言）
      expect(state.statusLog.length).toBe(statusBefore + 1);
      expect(state.getLog.length).toBe(editorAfterSuccess);
      expect(state.taskDetailLog.length).toBe(detailAfterSuccess);
      expect(state.filesLog.length).toBe(filesAfterSuccess);
      expect(state.putLog.length).toBe(putAfterSuccess);
      // 对账本身：全程无 PUT、无 files 刷新、无 task 详情；status 精确 1
      expect(state.putLog.length).toBe(putBefore);
      expect(state.filesLog.length).toBe(filesBefore);
      expect(state.taskDetailLog.length).toBe(detailBefore);
      // editor GET 允许 pipeline 成功后的既有重载，但相对触发前不得由迟到 status 再增
      expect(state.getLog.length).toBeGreaterThanOrEqual(editorGetsBefore);
      expect(state.statusMetrics.maxConcurrent).toBeLessThanOrEqual(1);

      expect(
        appConsoleLines(consoleLines).filter((l) => l.includes(SECRET_MARKER)),
      ).toEqual([]);
    } finally {
      await sse.close();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });
});
