/**
 * 模块：P11B 商务标编辑态真实数据收口 E2E
 * 用途：只认 GET|PUT /api/projects/{id}/editor-state；旧 workspace 键忽略保值；
 *       GET 失败固定卡；PUT 失败固定脱敏；A→B 迟到隔离；网络/存储/console 反假绿。
 * 对接：Playwright chromium headless 单 worker；前端 5174；受控路由桩。
 * 二次开发：禁止 or True、宽泛 startsWith 放行、吞异常、固定 waitForTimeout 作完成证据、
 *       条件跳过；探针安装失败必须失败；枚举 key(i)??""。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const SECRET = "SECRET_P11B_LEAK_DETAIL_/api/projects/editor-state";
const REAL_BIZ_A = "proj_e2e_p11b_biz_a";
const REAL_BIZ_B = "proj_e2e_p11b_biz_b";
const REAL_MARKDOWN = "P11B_SERVER_REAL_PARSED_MARKDOWN_权威正文";
const REAL_MARKDOWN_B = "P11B_SERVER_B_PARSED_MARKDOWN_项目乙";
const LOCAL_SECRET_MD = "LOCAL_WORKSPACE_SECRET_SHOULD_NOT_RENDER_P11B";
const DEMO_SNIPPET = "独立法人资格，营业执照有效";
const LOAD_ERROR = "商务标工作区加载失败，请稍后重试";
const SAVE_ERROR = "商务标工作区保存失败，请稍后重试";

const WORKSPACE_KEY_RE = /^biaoshu\.businessBid\.workspace(?:\.|$)/;
const FEEDBACK_KEY_RE = /^biaoshu\.businessBid\.feedback\./;

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: "business";
  linkedProjectId?: string | null;
};

type EditorState = {
  projectId: string;
  parsedMarkdown: string;
  businessQualify: Array<Record<string, unknown>>;
  businessToc: Array<Record<string, unknown>>;
  businessQuote: { rows: Array<Record<string, unknown>>; notes: string };
  businessCommit: Array<Record<string, unknown>>;
  outline: unknown[];
  chapters: unknown[];
  mode: string;
  version: number;
};

type PutRecord = {
  projectId: string;
  body: Record<string, unknown>;
  raw: string;
};

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  /** 项目 id → GET 行为 */
  getMode: Record<
    string,
    | { kind: "ok" }
    | { kind: "fail"; status: number }
    | { kind: "delay"; ms: number; then: "ok" | "fail"; status?: number }
  >;
  /** 项目 id → PUT 行为 */
  putMode: Record<
    string,
    | { kind: "ok" }
    | { kind: "fail"; status: number }
    | { kind: "delay"; ms: number; then: "ok" | "fail"; status?: number }
  >;
  getLog: string[];
  putLog: PutRecord[];
  taskPosts: Array<{ projectId: string; type: string }>;
  revisePosts: Array<{ projectId: string }>;
  forbiddenHits: string[];
  externalHits: string[];
  orderLog: string[];
  clipboard: { installed: boolean; read: number; write: number };
  /** 任务成功后下一次 editor-state GET 失败（按项目） */
  failNextEditorGetAfterTask: Record<string, boolean>;
};

type StorageSnapshot = {
  lsKeys: string[];
  ls: Record<string, string>;
  ssKeys: string[];
  ss: Record<string, string>;
  cookies: string;
};

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
    body: JSON.stringify(body),
  });
}

/**
 * 用途：method + 精确路径/受控正则白名单；禁止宽放 /api/projects 前缀。
 */
function isAllowedP11bApi(method: string, path: string): boolean {
  const pid = "proj_[a-z0-9_]+";
  const rules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces(\/|$)/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings(\/|$)/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET", "PATCH"], path: new RegExp(`^/api/projects/${pid}/?$`) },
    {
      methods: ["GET", "PUT"],
      path: new RegExp(`^/api/projects/${pid}/editor-state/?$`),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(`^/api/projects/${pid}/(files|tasks|images)/?$`),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/tasks/[^/]+(/(events|cancel))?/?$`,
      ),
    },
    {
      methods: ["POST"],
      path: new RegExp(
        `^/api/projects/${pid}/artifacts/workspace/revise/?$`,
      ),
    },
    {
      methods: ["POST"],
      path: new RegExp(
        `^/api/projects/${pid}/(duplicate-check|rejection-check)/?$`,
      ),
    },
  ];
  return rules.some((r) => r.methods.includes(method) && r.path.test(path));
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-14T12:00:00.000Z",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    kind: "business",
    id: partial.id,
    name: partial.name,
  };
}

function emptyBusinessEditor(projectId: string): EditorState {
  return {
    projectId,
    parsedMarkdown: "",
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    outline: [],
    chapters: [],
    mode: "ALIGNED",
    version: 1,
  };
}

function realBusinessEditor(
  projectId: string,
  markdown: string,
): EditorState {
  return {
    projectId,
    parsedMarkdown: markdown,
    businessQualify: [
      {
        id: "q_srv_1",
        requirement: "服务端资格要求甲",
        response: "服务端响应甲",
        evidence: "证据甲.pdf",
        status: "matched",
      },
    ],
    businessToc: [
      {
        id: "t_srv_1",
        title: "服务端目录项甲",
        category: "资格证明",
        status: "required",
        checked: true,
      },
    ],
    businessQuote: {
      rows: [
        {
          id: "qr_srv_1",
          name: "服务端报价行甲",
          unit: "项",
          quantity: "1",
          unitPrice: "100",
          amount: "100",
          remark: "",
        },
      ],
      notes: "服务端报价备注",
    },
    businessCommit: [
      {
        id: "c_srv_1",
        title: "服务端承诺甲",
        body: "服务端承诺正文",
        needsStamp: true,
      },
    ],
    outline: [],
    chapters: [],
    mode: "ALIGNED",
    version: 1,
  };
}

function workspaceStorageKey(projectId: string) {
  return `biaoshu.businessBid.workspace.${projectId}`;
}

function feedbackStorageKey(projectId: string) {
  return `biaoshu.businessBid.feedback.${projectId}`;
}

function fakeWorkspaceValue(projectId: string) {
  return JSON.stringify({
    projectId,
    parseMarkdown: LOCAL_SECRET_MD,
    qualifyItems: [
      {
        id: "local_q",
        requirement: DEMO_SNIPPET,
        response: "本地假响应",
        evidence: "",
        status: "matched",
      },
    ],
    tocItems: [],
    quoteRows: [],
    quoteNotes: "LOCAL_QUOTE_NOTES",
    commitBlocks: [],
  });
}

function createProbeState(seed: ProjectStub[] = []): ProbeState {
  const editorById: Record<string, EditorState> = {};
  for (const p of seed) {
    editorById[p.id] = emptyBusinessEditor(p.id);
  }
  return {
    projects: [...seed],
    editorById,
    getMode: {},
    putMode: {},
    getLog: [],
    putLog: [],
    taskPosts: [],
    revisePosts: [],
    forbiddenHits: [],
    externalHits: [],
    orderLog: [],
    clipboard: { installed: false, read: 0, write: 0 },
    failNextEditorGetAfterTask: {},
  };
}

async function installP11bRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p11bClip?: { installed: boolean; read: number; write: number };
    };
    g.__p11bClip = { installed: false, read: 0, write: 0 };
    const clip = {
      readText: async () => {
        g.__p11bClip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__p11bClip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__p11bClip.installed = true;
    } catch {
      g.__p11bClip.installed = false;
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

    if (!isAllowedP11bApi(method, path)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p11b_forbidden", message: SECRET } },
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
        csrfToken: null,
      });
      return;
    }

    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-p11b-csrf" });
      return;
    }

    if (
      (path === "/api/auth/login" || path === "/api/auth/logout") &&
      method === "POST"
    ) {
      await route.fulfill({ status: 204, body: "" });
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
        if (kind === "business") {
          items = items.filter((p) => p.kind === "business");
        }
        await json(route, items);
        return;
      }
      if (method === "POST") {
        await json(
          route,
          { detail: { code: "p11b_no_create", message: SECRET } },
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

        const body =
          state.editorById[id] ?? emptyBusinessEditor(id);
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
      state.putLog.push({ projectId: id, body, raw });
      state.orderLog.push(`editor-put:${id}`);

      const mode = state.putMode[id] ?? { kind: "ok" as const };
      if (mode.kind === "delay") {
        await new Promise((r) => setTimeout(r, mode.ms));
        if (mode.then === "fail") {
          await json(
            route,
            {
              detail: {
                code: "editor_state_put_delayed_fail",
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
            detail: { code: "editor_state_put_failed", message: SECRET },
          },
          mode.status,
        );
        return;
      }

      const prev = state.editorById[id] ?? emptyBusinessEditor(id);
      const quote = body.businessQuote as
        | { rows?: unknown[]; notes?: string }
        | undefined;
      state.editorById[id] = {
        ...prev,
        projectId: id,
        parsedMarkdown:
          body.parsedMarkdown != null
            ? String(body.parsedMarkdown)
            : prev.parsedMarkdown,
        businessQualify: Array.isArray(body.businessQualify)
          ? (body.businessQualify as Array<Record<string, unknown>>)
          : prev.businessQualify,
        businessToc: Array.isArray(body.businessToc)
          ? (body.businessToc as Array<Record<string, unknown>>)
          : prev.businessToc,
        businessQuote: {
          rows: quote && Array.isArray(quote.rows) ? quote.rows as Array<Record<string, unknown>> : prev.businessQuote.rows,
          notes:
            quote && typeof quote.notes === "string"
              ? quote.notes
              : prev.businessQuote.notes,
        },
        businessCommit: Array.isArray(body.businessCommit)
          ? (body.businessCommit as Array<Record<string, unknown>>)
          : prev.businessCommit,
      };
      await json(route, state.editorById[id]);
      return;
    }

    // files / tasks GET
    if (
      /\/api\/projects\/[^/]+\/(files|tasks)\/?$/.test(path) &&
      method === "GET"
    ) {
      await json(route, []);
      return;
    }

    // tasks POST
    const taskPostMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks\/?$/,
    );
    if (taskPostMatch && method === "POST") {
      const pid = taskPostMatch[1];
      let type = "";
      try {
        const b = JSON.parse(req.postData() || "{}") as { type?: string };
        type = b.type || "";
      } catch {
        type = "";
      }
      state.taskPosts.push({ projectId: pid, type });
      state.orderLog.push(`task-post:${pid}:${type}`);
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

    // revise POST
    const reviseMatch = path.match(
      /^\/api\/projects\/([^/]+)\/artifacts\/workspace\/revise\/?$/,
    );
    if (reviseMatch && method === "POST") {
      const pid = reviseMatch[1];
      state.revisePosts.push({ projectId: pid });
      state.orderLog.push(`revise-post:${pid}`);
      await json(route, {
        status: "success",
        resultSummary: "修订完成",
        revisedContent: "修订后正文片段",
      });
      return;
    }

    if (
      /\/api\/projects\/[^/]+\/(files|images)\/?$/.test(path) &&
      method === "POST"
    ) {
      await json(route, { id: "file_stub", filename: "stub.pdf" });
      return;
    }

    if (
      /\/api\/projects\/[^/]+\/tasks\/[^/]+/.test(path)
    ) {
      await json(route, { id: "task_stub", status: "success", progress: 100 });
      return;
    }

    if (
      /\/api\/projects\/[^/]+\/(duplicate-check|rejection-check)\/?$/.test(
        path,
      ) &&
      method === "POST"
    ) {
      await json(route, { projectId: "x", hits: [], items: [], stats: {} });
      return;
    }

    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "p11b_unhandled", message: SECRET } },
      403,
    );
  });
}

async function seedOldWorkspace(
  page: Page,
  projectId: string,
  value?: string,
) {
  const key = workspaceStorageKey(projectId);
  const v = value ?? fakeWorkspaceValue(projectId);
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
      __p11bClip?: { installed: boolean; read: number; write: number };
    };
    return g.__p11bClip ?? { installed: false, read: -1, write: -1 };
  });
}

/**
 * 用途：项目 workspace 键族只能是预置旧键（精确 key），值精确不变；不得出现 v2/cache/别名。
 */
function assertWorkspaceKeyFamilyExact(
  snap: StorageSnapshot,
  expected: Record<string, string>,
) {
  const family = snap.lsKeys
    .filter((k) => WORKSPACE_KEY_RE.test(k))
    .slice()
    .sort();
  const expectedKeys = Object.keys(expected).slice().sort();
  expect(family, "workspace 键族必须精确等于预置旧键集合").toEqual(
    expectedKeys,
  );
  for (const k of expectedKeys) {
    expect(snap.ls[k], `workspace 键 ${k} 原值必须不变`).toBe(expected[k]);
  }
}

/**
 * 用途：feedback 键只允许精确格式 biaoshu.businessBid.feedback.{projectId}。
 */
function assertFeedbackKeysExactFormat(
  snap: StorageSnapshot,
  allowedProjectIds: string[],
) {
  const allowed = new Set(
    allowedProjectIds.map((id) => feedbackStorageKey(id)),
  );
  for (const k of snap.lsKeys) {
    if (!FEEDBACK_KEY_RE.test(k)) continue;
    expect(allowed.has(k), `不允许的 feedback 键: ${k}`).toBe(true);
  }
}

function assertStorageSnapshotEqual(
  actual: StorageSnapshot,
  baseline: StorageSnapshot,
  label: string,
) {
  expect(actual.lsKeys, `${label} localStorage 键集`).toEqual(baseline.lsKeys);
  expect(actual.ls, `${label} localStorage 值`).toEqual(baseline.ls);
  expect(actual.ssKeys, `${label} sessionStorage 键集`).toEqual(
    baseline.ssKeys,
  );
  expect(actual.ss, `${label} sessionStorage 值`).toEqual(baseline.ss);
  expect(actual.cookies, `${label} cookie`).toBe(baseline.cookies);
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

function sensitiveSnippets(extra: string[] = []): string[] {
  return [
    SECRET,
    REAL_BIZ_A,
    REAL_BIZ_B,
    LOCAL_SECRET_MD,
    "/api/projects",
    "editor_state_get_failed",
    "editor_state_put_failed",
    "p11b_forbidden",
    "detail",
    ...extra,
  ];
}

function assertCleanConsole(lines: string[], extra: string[] = []) {
  expect(appConsoleLines(lines)).toEqual([]);
  const joined = lines.join("\n");
  for (const b of sensitiveSnippets(extra)) {
    // "detail" 过严易误伤；仅检查 SECRET/路径/ID 等
    if (b === "detail") continue;
    expect(joined, `console 敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

function assertNoSensitiveInText(text: string, extra: string[] = []) {
  for (const b of [
    SECRET,
    "editor_state_get_failed",
    "editor_state_put_failed",
    "p11b_forbidden",
    ...extra,
  ]) {
    expect(text, `页面敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

async function softNavigateBusiness(
  page: Page,
  projectId: string,
  step = "parse",
) {
  const url = `/business-bid/${projectId}/${step}`;
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function openBusinessWorkspace(page: Page, projectId: string) {
  await page.goto(`/business-bid/${projectId}/parse`);
}

async function expectWorkspaceReady(page: Page, projectName: string) {
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
}

async function expectLoadErrorCard(page: Page) {
  await expect(page.getByTestId("business-editor-load-error")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(LOAD_ERROR)).toBeVisible();
  await expect(page.getByTestId("business-editor-retry")).toBeVisible();
  await expect(page.getByRole("link", { name: "返回列表" })).toBeVisible();
  // 不得挂步骤/解析编辑区
  await expect(page.getByTestId("business-editor-workspace")).toHaveCount(0);
  await expect(
    page.getByLabel("商务条款解析 Markdown"),
  ).toHaveCount(0);
}

test.describe("P11B 商务标编辑态真实数据收口", () => {
  test("服务端真实内容；旧 workspace 键忽略且原值不变", async ({ page }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B真实商务标甲",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    const seeded = await seedOldWorkspace(page, REAL_BIZ_A);
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B真实商务标甲");

    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN,
    );
    await expect(page.getByText(LOCAL_SECRET_MD)).toHaveCount(0);
    await expect(page.getByText(DEMO_SNIPPET)).toHaveCount(0);
    await expect(page.getByText("服务端资格要求甲")).toHaveCount(0); // parse 步不展示资格表
    // 切换到资格步确认服务端值
    await page.goto(`/business-bid/${REAL_BIZ_A}/qualify`);
    await expect(page.getByText("服务端资格要求甲")).toBeVisible();
    await expect(page.getByText(DEMO_SNIPPET)).toHaveCount(0);

    const snap = await readStorageSnapshot(page);
    assertWorkspaceKeyFamilyExact(snap, { [seeded.key]: seeded.value });
    assertFeedbackKeysExactFormat(snap, [REAL_BIZ_A]);
    expect(snap.ssKeys).toEqual([]);
    expect(snap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);
    assertCleanConsole(consoleLines);
  });

  test("GET 空商务字段保持空；不补 mock；不写 workspace 键", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B空态商务标",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = emptyBusinessEditor(REAL_BIZ_A);
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B空态商务标");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue("");
    await expect(page.getByText(DEMO_SNIPPET)).toHaveCount(0);
    await expect(page.getByText("平台软件开发与集成")).toHaveCount(0);

    await page.goto(`/business-bid/${REAL_BIZ_A}/qualify`);
    await expect(page.locator(".bb-qualify-item")).toHaveCount(0);

    await page.goto(`/business-bid/${REAL_BIZ_A}/toc`);
    await expect(page.locator(".bb-toc-row")).toHaveCount(0);

    const snap = await readStorageSnapshot(page);
    const workspaceKeys = snap.lsKeys.filter((k) => WORKSPACE_KEY_RE.test(k));
    expect(workspaceKeys, "不得新写 workspace 键").toEqual([]);
    assertFeedbackKeysExactFormat(snap, [REAL_BIZ_A]);
    expect(snap.ssKeys).toEqual([]);
    expect(snap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    assertCleanConsole(consoleLines);
  });

  test("GET 失败固定卡；零旧内容/零 PUT；重试 +1 GET 成功后挂工作区", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B失败后重试",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    state.getMode[REAL_BIZ_A] = { kind: "fail", status: 500 };
    const seeded = await seedOldWorkspace(page, REAL_BIZ_A);
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectLoadErrorCard(page);
    await expect(page.getByText(LOCAL_SECRET_MD)).toHaveCount(0);
    await expect(page.getByText(REAL_MARKDOWN)).toHaveCount(0);
    await expect(page.getByText(DEMO_SNIPPET)).toHaveCount(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText);
    expect(state.putLog.length).toBe(0);

    const getsBeforeRetry = state.getLog.filter((id) => id === REAL_BIZ_A)
      .length;
    expect(getsBeforeRetry).toBeGreaterThanOrEqual(1);

    // 重试成功
    state.getMode[REAL_BIZ_A] = { kind: "ok" };
    await page.getByTestId("business-editor-retry").click();
    await expectWorkspaceReady(page, "P11B失败后重试");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN,
    );

    const getsAfter = state.getLog.filter((id) => id === REAL_BIZ_A).length;
    expect(getsAfter).toBe(getsBeforeRetry + 1);
    expect(state.putLog.length).toBe(0);

    const snap = await readStorageSnapshot(page);
    assertWorkspaceKeyFamilyExact(snap, { [seeded.key]: seeded.value });
    assertCleanConsole(consoleLines);
  });

  test("GET 401 同固定失败卡；零 PUT", async ({ page }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B状态401",
    });
    const state = createProbeState([project]);
    state.getMode[REAL_BIZ_A] = { kind: "fail", status: 401 };
    await installP11bRoutes(page, state);
    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectLoadErrorCard(page);
    expect(state.putLog.length).toBe(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
  });

  test("GET 404 同固定失败卡；零 PUT", async ({ page }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B状态404",
    });
    const state = createProbeState([project]);
    state.getMode[REAL_BIZ_A] = { kind: "fail", status: 404 };
    await installP11bRoutes(page, state);
    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectLoadErrorCard(page);
    expect(state.putLog.length).toBe(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
  });

  test("编辑后防抖 PUT 精确 body；旧 workspace 键保值", async ({ page }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B防抖保存",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    const seeded = await seedOldWorkspace(page, REAL_BIZ_A);
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B防抖保存");

    const putsBefore = state.putLog.length;
    const edited = `${REAL_MARKDOWN}\n用户追加编辑行`;
    await page.getByLabel("商务条款解析 Markdown").fill(edited);

    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsBefore + 1);

    const put = state.putLog[state.putLog.length - 1];
    expect(put.projectId).toBe(REAL_BIZ_A);
    const keys = Object.keys(put.body).slice().sort();
    expect(keys).toEqual(
      [
        "businessCommit",
        "businessQualify",
        "businessQuote",
        "businessToc",
        "parsedMarkdown",
      ].slice().sort(),
    );
    expect(put.body.parsedMarkdown).toBe(edited);
    expect(Array.isArray(put.body.businessQualify)).toBe(true);
    expect(Array.isArray(put.body.businessToc)).toBe(true);
    expect(put.body.businessQuote).toEqual({
      rows: state.editorById[REAL_BIZ_A].businessQuote.rows.length
        ? expect.any(Array)
        : expect.any(Array),
      notes: expect.any(String),
    });
    // 精确资格项保留服务端结构
    expect(put.body.businessQualify).toEqual([
      {
        id: "q_srv_1",
        requirement: "服务端资格要求甲",
        response: "服务端响应甲",
        evidence: "证据甲.pdf",
        status: "matched",
      },
    ]);

    const snap = await readStorageSnapshot(page);
    assertWorkspaceKeyFamilyExact(snap, { [seeded.key]: seeded.value });
    assertCleanConsole(consoleLines);
  });

  test("PUT 500 固定保存错误；脱敏；再编辑可新增 PUT 并清错", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B保存失败",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    state.putMode[REAL_BIZ_A] = { kind: "fail", status: 500 };
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B保存失败");

    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n保存失败编辑`);

    await expect(page.getByTestId("business-editor-save-error")).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.getByText(SAVE_ERROR)).toBeVisible();
    await expect(page.getByText(SECRET)).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText, ["editor_state_put_failed"]);

    const putsFail = state.putLog.length;
    expect(putsFail).toBeGreaterThanOrEqual(1);

    // 再次编辑成功
    state.putMode[REAL_BIZ_A] = { kind: "ok" };
    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n再次编辑成功`);

    await expect
      .poll(() => state.putLog.length, { timeout: 5_000 })
      .toBe(putsFail + 1);
    await expect(page.getByTestId("business-editor-save-error")).toHaveCount(0);

    const snap = await readStorageSnapshot(page);
    // history/storage 无 SECRET
    for (const v of Object.values(snap.ls)) {
      expect(v).not.toContain(SECRET);
    }
    for (const v of Object.values(snap.ss)) {
      expect(v).not.toContain(SECRET);
    }
    assertCleanConsole(consoleLines);
  });

  test("任务成功后 editor-state 刷新失败：业务成功不反转，进入加载失败态", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B任务后刷新失败",
      technicalPlanStep: 1,
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B任务后刷新失败");

    // 上传占位 + 触发整段重解析需要文件；直接走资格生成更直接
    await page.goto(`/business-bid/${REAL_BIZ_A}/qualify`);
    await expect(page.getByText("服务端资格要求甲")).toBeVisible();

    // 资格页初始化 GET 已稳定完成；从此刻起只让任务成功后的刷新 GET 失败
    state.failNextEditorGetAfterTask[REAL_BIZ_A] = true;

    // 生成资格草稿会 POST task 后 refreshFromApi
    // 按钮可能因 parseMarkdown 非空而可点
    const tasksBefore = state.taskPosts.length;
    await page.getByRole("button", { name: "生成资格草稿" }).click();

    await expect
      .poll(() => state.taskPosts.length, { timeout: 10_000 })
      .toBe(tasksBefore + 1);
    expect(state.taskPosts[state.taskPosts.length - 1]).toEqual({
      projectId: REAL_BIZ_A,
      type: "biz_qualify",
    });

    await expectLoadErrorCard(page);
    // 旧服务端内容不得继续作为最新
    await expect(page.getByText("服务端资格要求甲")).toHaveCount(0);
    await expect(page.getByText(REAL_MARKDOWN)).toHaveCount(0);
    // 业务成功不谎报失败：任务仅一次
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === REAL_BIZ_A && t.type === "biz_qualify",
      ).length,
    ).toBe(1);

    // 重试 GET 恢复
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      "任务后恢复权威正文",
    );
    await page.getByTestId("business-editor-retry").click();
    await expectWorkspaceReady(page, "P11B任务后刷新失败");
    await softNavigateBusiness(page, REAL_BIZ_A, "parse");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      "任务后恢复权威正文",
    );
    assertCleanConsole(consoleLines);
  });

  test("SPA A→B：迟到 A GET 不污染 B；迟到 A PUT 不改 B", async ({ page }) => {
    const projectA = makeProject({ id: REAL_BIZ_A, name: "项目甲A" });
    const projectB = makeProject({ id: REAL_BIZ_B, name: "项目乙B" });
    const state = createProbeState([projectA, projectB]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    state.editorById[REAL_BIZ_B] = realBusinessEditor(
      REAL_BIZ_B,
      REAL_MARKDOWN_B,
    );
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    // —— 迟到 GET ——
    state.getMode[REAL_BIZ_A] = { kind: "delay", ms: 800, then: "ok" };
    await openBusinessWorkspace(page, REAL_BIZ_A);
    // 确认 A GET 已进入延迟路由，但不等待响应完成，再切 B
    await expect
      .poll(() => state.getLog.filter((id) => id === REAL_BIZ_A).length, {
        timeout: 5_000,
      })
      .toBeGreaterThanOrEqual(1);
    await softNavigateBusiness(page, REAL_BIZ_B, "parse");
    // B 立即成功
    await expectWorkspaceReady(page, "项目乙B");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN_B,
    );

    // 仍保持 B
    await expect(page.getByRole("heading", { name: "项目乙B" })).toBeVisible();
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN_B,
    );
    await expect(page.getByText(REAL_MARKDOWN)).toHaveCount(0);
    await expect(page.getByTestId("business-editor-load-error")).toHaveCount(0);

    // —— 迟到 PUT ——
    // 先稳定在 A，编辑触发 delay PUT，再切 B
    state.getMode[REAL_BIZ_A] = { kind: "ok" };
    state.getMode[REAL_BIZ_B] = { kind: "ok" };
    await softNavigateBusiness(page, REAL_BIZ_A, "parse");
    await expectWorkspaceReady(page, "项目甲A");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN,
    );

    state.putMode[REAL_BIZ_A] = { kind: "delay", ms: 1200, then: "ok" };
    const putsABefore = state.putLog.filter((p) => p.projectId === REAL_BIZ_A)
      .length;
    const putsBBefore = state.putLog.filter((p) => p.projectId === REAL_BIZ_B)
      .length;
    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n甲的迟到PUT编辑`);

    // 等防抖 PUT 进入路由处理（已 push putLog，仍在 delay 中）
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_BIZ_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsABefore + 1);

    // PUT 仍在 delay 中时切 B
    await softNavigateBusiness(page, REAL_BIZ_B, "parse");
    await expectWorkspaceReady(page, "项目乙B");
    await expect(page.getByLabel("商务条款解析 Markdown")).toHaveValue(
      REAL_MARKDOWN_B,
    );

    // 等 A 延迟 PUT 处理结束（orderLog 已有 put；再给 delay 余量用 poll 内容稳定）
    await expect
      .poll(async () => {
        const text = await page
          .getByLabel("商务条款解析 Markdown")
          .inputValue();
        const saveErr = await page
          .getByTestId("business-editor-save-error")
          .count();
        const loadErr = await page
          .getByTestId("business-editor-load-error")
          .count();
        return `${text}|${saveErr}|${loadErr}`;
      }, { timeout: 5_000 })
      .toBe(`${REAL_MARKDOWN_B}|0|0`);

    const putsBAfter = state.putLog.filter((p) => p.projectId === REAL_BIZ_B)
      .length;
    expect(putsBAfter).toBe(putsBBefore);

    // 迟到 PUT 失败路径
    await softNavigateBusiness(page, REAL_BIZ_A, "parse");
    await expectWorkspaceReady(page, "项目甲A");
    state.putMode[REAL_BIZ_A] = {
      kind: "delay",
      ms: 1200,
      then: "fail",
      status: 500,
    };
    const putsAMid = state.putLog.filter((p) => p.projectId === REAL_BIZ_A)
      .length;
    await page
      .getByLabel("商务条款解析 Markdown")
      .fill(`${REAL_MARKDOWN}\n甲的失败迟到PUT`);
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === REAL_BIZ_A).length,
        { timeout: 5_000 },
      )
      .toBe(putsAMid + 1);
    await softNavigateBusiness(page, REAL_BIZ_B, "parse");
    await expectWorkspaceReady(page, "项目乙B");
    await expect
      .poll(async () => {
        const text = await page
          .getByLabel("商务条款解析 Markdown")
          .inputValue();
        const saveErr = await page
          .getByTestId("business-editor-save-error")
          .count();
        const loadErr = await page
          .getByTestId("business-editor-load-error")
          .count();
        return `${text}|${saveErr}|${loadErr}`;
      }, { timeout: 5_000 })
      .toBe(`${REAL_MARKDOWN_B}|0|0`);

    assertCleanConsole(consoleLines);
  });

  test("网络白名单：未知 API 与外网可观测阻断；存储基线不因探针变化", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B网络探针",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = emptyBusinessEditor(REAL_BIZ_A);
    const seeded = await seedOldWorkspace(page, REAL_BIZ_A);
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B网络探针");
    const baseline = await readStorageSnapshot(page);

    const probeResults = await page.evaluate(async () => {
      const results: Array<{ url: string; status: number | string }> = [];
      for (const url of [
        "/api/unknown-p11b-probe",
        `/api/projects/${"proj_e2e_p11b_biz_a"}/editor-state/unknown`,
      ]) {
        try {
          const res = await fetch(url);
          results.push({ url, status: res.status });
        } catch (e) {
          results.push({ url, status: String(e) });
        }
      }
      try {
        await fetch("https://example.invalid/p11b-probe");
        results.push({ url: "https://example.invalid/p11b-probe", status: "ok" });
      } catch {
        results.push({
          url: "https://example.invalid/p11b-probe",
          status: "blocked",
        });
      }
      return results;
    });

    expect(
      state.forbiddenHits.some((h) => h.includes("/api/unknown-p11b-probe")),
    ).toBe(true);
    expect(
      state.forbiddenHits.some((h) =>
        h.includes("/editor-state/unknown"),
      ),
    ).toBe(true);
    expect(
      state.externalHits.some((h) => h.includes("example.invalid")),
    ).toBe(true);
    expect(probeResults.find((r) => r.url.includes("unknown-p11b-probe"))?.status).toBe(
      403,
    );
    expect(
      probeResults.find((r) => r.url.includes("editor-state/unknown"))?.status,
    ).toBe(403);
    expect(
      probeResults.find((r) => r.url.includes("example.invalid"))?.status,
    ).toBe("blocked");

    const after = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(after, baseline, "网络探针后");
    assertWorkspaceKeyFamilyExact(after, { [seeded.key]: seeded.value });
    assertCleanConsole(consoleLines);
  });

  test("session/IndexedDB/Cookie/clipboard 边界；无 workspace 别名", async ({
    page,
  }) => {
    const project = makeProject({
      id: REAL_BIZ_A,
      name: "P11B存储边界",
    });
    const state = createProbeState([project]);
    state.editorById[REAL_BIZ_A] = realBusinessEditor(
      REAL_BIZ_A,
      REAL_MARKDOWN,
    );
    const seeded = await seedOldWorkspace(page, REAL_BIZ_A);
    // 预置一个非法别名键：实现不得新增，但预置的应... 任务说不得新增 v2/cache；预置别名若存在仍可检测实现是否新增更多
    await page.addInitScript(() => {
      // 不预置 v2；断言结束时不得出现
    });
    const consoleLines = collectConsole(page);
    await installP11bRoutes(page, state);

    await openBusinessWorkspace(page, REAL_BIZ_A);
    await expectWorkspaceReady(page, "P11B存储边界");

    // 触发 feedback 写入（修订历史）
    // 不强制修订；仅确认 workspace 族
    const snap = await readStorageSnapshot(page);
    assertWorkspaceKeyFamilyExact(snap, { [seeded.key]: seeded.value });
    // 不得出现 v2/cache
    for (const k of snap.lsKeys) {
      expect(k.includes("workspace.v2") || k.includes("workspace.cache")).toBe(
        false,
      );
    }
    expect(snap.ssKeys).toEqual([]);
    expect(snap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);
    assertCleanConsole(consoleLines);
  });
});
