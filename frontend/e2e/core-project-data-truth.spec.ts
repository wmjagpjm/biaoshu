/**
 * 模块：P11A 核心项目真实数据收口 E2E
 * 用途：技术标/商务标列表与创建只认服务端 /api/projects*；真实空态、失败 fail-closed、
 *       忽略 biaoshu.projects.v1、演示 ID 不复活、网络/存储/console 边界反假绿。
 * 对接：Playwright chromium headless 单 worker；前端 5174；受控路由桩。
 * 二次开发：禁止 or True、宽泛 startsWith 放行、吞异常、固定 waitForTimeout 作完成证据、
 *       条件跳过；探针安装失败必须失败。
 *       存储断言必须覆盖完整键集合与项目元数据键族，禁止只比 v1 单键假绿。
 */
import { expect, test, type ConsoleMessage, type Page, type Route } from "@playwright/test";

const FAKE_LS_KEY = "biaoshu.projects.v1";
const PENDING_SS_KEY = "biaoshu.pendingProjectFiles";
const FAKE_PROJECT_NAME = "LOCAL_FAKE_PROJECT_P11A_SHOULD_NOT_RENDER";
const FAKE_PROJECT_ID = "proj_local_fake_p11a";
const SECRET = "SECRET_P11A_LEAK_DETAIL_/api/projects";
const REAL_TECH_ID = "proj_e2e_p11a_tech01";
const REAL_BIZ_ID = "proj_e2e_p11a_biz01";
const REAL_CREATE_ID = "proj_e2e_p11a_created";
const DEMO_TECH_ID = "proj_01";
const DEMO_BIZ_ID = "bb_01";

/** 项目元数据存储族：v1 及任何 v2/cache/别名均命中 */
const PROJECT_META_KEY_RE = /^biaoshu\.projects(?:\.|$)/;

const FAKE_LS_VALUE = JSON.stringify([
  {
    id: FAKE_PROJECT_ID,
    workspaceId: "ws_local",
    name: FAKE_PROJECT_NAME,
    industry: "假行业",
    status: "draft",
    updatedAt: "2026-01-01T00:00:00.000Z",
    technicalPlanStep: 1,
    wordCount: 999,
    kind: "technical",
  },
]);

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: "technical" | "business";
  linkedProjectId?: string | null;
};

type ProbeState = {
  projects: ProjectStub[];
  /** 列表 GET 失败开关 */
  listFail: boolean;
  /** 创建 POST 失败开关 */
  createFail: boolean;
  createPosts: Array<{ path: string; body: Record<string, unknown> }>;
  projectGets: string[];
  listGets: string[];
  forbiddenHits: string[];
  externalHits: string[];
  orderLog: string[];
  clipboard: { installed: boolean; read: number; write: number };
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
 * 未知 /api、/api/projects/unknown-m3d-probe 形态、外网均进入可观测阻断。
 */
function isAllowedP11aApi(method: string, path: string): boolean {
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
    // 列表：精确 /api/projects 或带 kind 查询（pathname 不含 query）
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
        `^/api/projects/${pid}/(duplicate-check|rejection-check)/?$`,
      ),
    },
  ];
  return rules.some(
    (r) => r.methods.includes(method) && r.path.test(path),
  );
}

function emptyEditorState() {
  return {
    outline: [],
    chapters: [],
    mode: "ALIGNED",
    parsedMarkdown: "",
    bidAnalysis: null,
    facts: [],
    version: 1,
  };
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-14T12:00:00.000Z",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: partial.kind,
  };
}

function createProbeState(seed: ProjectStub[] = []): ProbeState {
  return {
    projects: [...seed],
    listFail: false,
    createFail: false,
    createPosts: [],
    projectGets: [],
    listGets: [],
    forbiddenHits: [],
    externalHits: [],
    orderLog: [],
    clipboard: { installed: false, read: 0, write: 0 },
  };
}

/**
 * 用途：安装受控路由与剪贴板探针；业务默认拒绝未知 /api。
 */
async function installP11aRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __p11aClip?: { installed: boolean; read: number; write: number };
    };
    g.__p11aClip = { installed: false, read: 0, write: 0 };
    const clip = {
      readText: async () => {
        g.__p11aClip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__p11aClip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__p11aClip.installed = true;
    } catch {
      // 安装失败由断言检出，禁止伪装成功
      g.__p11aClip.installed = false;
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

    if (!isAllowedP11aApi(method, path)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "p11a_forbidden", message: SECRET } },
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
      await json(route, { csrfToken: "e2e-p11a-csrf" });
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

    if (path.startsWith("/api/settings") && (method === "GET" || method === "PUT")) {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "",
        apiKey: "",
        model: "",
        parseStrategy: "light",
      });
      return;
    }

    // GET/POST /api/projects
    if (path === "/api/projects" || path === "/api/projects/") {
      if (method === "GET") {
        state.listGets.push(`${path}${url.search || ""}`);
        state.orderLog.push(`list-get:${path}${url.search || ""}`);
        if (state.listFail) {
          await json(
            route,
            { detail: { code: "projects_list_failed", message: SECRET } },
            500,
          );
          return;
        }
        const kind = url.searchParams.get("kind");
        let items = state.projects;
        if (kind === "technical") {
          items = items.filter((p) => p.kind === "technical");
        } else if (kind === "business") {
          items = items.filter((p) => p.kind === "business");
        }
        await json(route, items);
        return;
      }
      if (method === "POST") {
        const raw = req.postData() || "{}";
        let body: Record<string, unknown> = {};
        try {
          body = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          body = { __parseError: true };
        }
        state.createPosts.push({ path, body });
        state.orderLog.push("create-post");
        if (state.createFail) {
          await json(
            route,
            { detail: { code: "projects_create_failed", message: SECRET } },
            500,
          );
          return;
        }
        const kind =
          body.kind === "business" ? "business" : ("technical" as const);
        const created = makeProject({
          id: REAL_CREATE_ID,
          name: String(body.name || "未命名"),
          industry: String(body.industry || "通用"),
          kind,
          technicalPlanStep: Number(body.technicalPlanStep || 1),
          status: String(body.status || "draft"),
        });
        state.projects = [created, ...state.projects];
        await json(route, created, 201);
        return;
      }
    }

    // GET/PATCH /api/projects/{id}
    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = detailMatch[1];
      state.projectGets.push(`${method} ${path}`);
      state.orderLog.push(`project-${method.toLowerCase()}:${id}`);
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

    // editor-state
    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      state.orderLog.push(`editor-${method.toLowerCase()}:${editorMatch[1]}`);
      await json(route, emptyEditorState());
      return;
    }

    // files / tasks 列表
    if (
      /\/api\/projects\/[^/]+\/(files|tasks)\/?$/.test(path) &&
      method === "GET"
    ) {
      await json(route, []);
      return;
    }

    if (
      /\/api\/projects\/[^/]+\/(files|tasks|images)\/?$/.test(path) &&
      method === "POST"
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

    // 白名单命中但未实现的端点：仍记 forbidden，避免宽放成功
    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "p11a_unhandled", message: SECRET } },
      403,
    );
  });
}

async function seedFakeLocalProjects(page: Page) {
  await page.addInitScript(
    ({ key, value }) => {
      localStorage.setItem(key, value);
    },
    { key: FAKE_LS_KEY, value: FAKE_LS_VALUE },
  );
}

async function readStorageSnapshot(page: Page): Promise<StorageSnapshot> {
  return page.evaluate(() => {
    const lsKeys: string[] = [];
    const ls: Record<string, string> = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      // 禁止 if (key) 隐藏空键
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
      __p11aClip?: { installed: boolean; read: number; write: number };
    };
    return g.__p11aClip ?? { installed: false, read: -1, write: -1 };
  });
}

/**
 * 用途：纯断言——项目元数据键族精确只有旧 biaoshu.projects.v1，且值=预置原文。
 * 反假绿：若实现另写 v2/cache/别名，本断言失败。
 */
function assertProjectMetaKeysOnlyLegacyV1(
  snap: StorageSnapshot,
  expectedValue: string = FAKE_LS_VALUE,
) {
  const projectKeys = snap.lsKeys
    .filter((k) => PROJECT_META_KEY_RE.test(k))
    .slice()
    .sort();
  expect(projectKeys, "项目元数据键族必须精确只有 v1").toEqual([FAKE_LS_KEY]);
  expect(snap.ls[FAKE_LS_KEY]).toBe(expectedValue);
  for (const k of Object.keys(snap.ls)) {
    if (PROJECT_META_KEY_RE.test(k)) {
      expect(k).toBe(FAKE_LS_KEY);
      expect(snap.ls[k]).toBe(expectedValue);
    }
  }
}

/**
 * 用途：完整 local/session/cookie 快照全等（键集排序后 + 值字典 + cookie 串）。
 */
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

/**
 * 用途：列表页（无 editor hooks）结束时的完整浏览器存储边界。
 * localStorage 完整键集合必须精确等于仅旧 v1 键。
 */
async function assertListPageBrowserStorageBoundary(page: Page) {
  const snap = await readStorageSnapshot(page);
  assertProjectMetaKeysOnlyLegacyV1(snap);
  expect(snap.lsKeys, "列表页 localStorage 只能有旧 v1").toEqual([
    FAKE_LS_KEY,
  ]);
  expect(snap.ssKeys, "列表页 sessionStorage 必须空").toEqual([]);
  expect(snap.cookies, "列表页 Cookie 必须空").toBe("");
  expect(await readIdbNames(page), "IndexedDB names 必须空").toEqual([]);
  const clip = await readClipboardProbe(page);
  expect(clip.installed).toBe(true);
  expect(clip.read).toBe(0);
  expect(clip.write).toBe(0);
  return snap;
}

/**
 * 用途：技术标工作区导航后允许的 localStorage 键（精确格式，禁止任意放开）。
 * 契约排除：editor 本地备份 + guidance 反馈键。
 */
function assertTechWorkspaceLocalKeys(
  snap: StorageSnapshot,
  projectId: string,
) {
  assertProjectMetaKeysOnlyLegacyV1(snap);
  const allowed = new Set([
    FAKE_LS_KEY,
    `biaoshu.technicalPlan.editors.${projectId}`,
    `biaoshu.projectFeedback.${projectId}`,
  ]);
  for (const k of snap.lsKeys) {
    expect(allowed.has(k), `技术标工作区未允许的 localStorage 键: ${k}`).toBe(
      true,
    );
  }
}

/**
 * 用途：商务标工作区导航后允许的 localStorage 键（精确格式）。
 */
function assertBizWorkspaceLocalKeys(snap: StorageSnapshot, projectId: string) {
  assertProjectMetaKeysOnlyLegacyV1(snap);
  const allowed = new Set([
    FAKE_LS_KEY,
    `biaoshu.businessBid.workspace.${projectId}`,
    `biaoshu.businessBid.feedback.${projectId}`,
  ]);
  for (const k of snap.lsKeys) {
    expect(allowed.has(k), `商务标工作区未允许的 localStorage 键: ${k}`).toBe(
      true,
    );
  }
}

/**
 * 用途：收集 console error/warning 与 pageerror；格式 type: text，便于过滤浏览器网络噪声。
 */
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
 * 用途：去掉 Chromium 对 4xx/5xx/外网失败的 "Failed to load resource" 网络层日志；
 * 应用层 console.error/warn 与 pageerror 仍须精确 []。
 */
function appConsoleLines(lines: string[]): string[] {
  return lines.filter((line) => {
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

function sensitiveSnippets(extra: string[] = []): string[] {
  return [
    SECRET,
    FAKE_PROJECT_ID,
    REAL_TECH_ID,
    REAL_BIZ_ID,
    REAL_CREATE_ID,
    DEMO_TECH_ID,
    DEMO_BIZ_ID,
    "/api/projects",
    "projects_list_failed",
    "projects_create_failed",
    "project_not_found",
    "p11a_forbidden",
    ...extra,
  ];
}

/** 用途：应用层 console 精确空，且全部日志（含网络噪声）不含敏感片段。 */
function assertCleanConsole(lines: string[], extra: string[] = []) {
  expect(appConsoleLines(lines)).toEqual([]);
  const joined = lines.join("\n");
  for (const b of sensitiveSnippets(extra)) {
    expect(joined, `console 敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

function assertNoSensitiveInText(text: string, extra: string[] = []) {
  for (const b of sensitiveSnippets(extra)) {
    expect(text, `页面敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

test.describe("P11A 核心项目真实数据收口", () => {
  test("技术标真实列表与空数组；假 localStorage 不渲染且原值不变", async ({
    page,
  }) => {
    const state = createProbeState([
      makeProject({
        id: REAL_TECH_ID,
        name: "E2E真实技术标甲",
        kind: "technical",
      }),
    ]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/technical-plan");
    await expect(page.getByRole("heading", { name: "我的项目" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("E2E真实技术标甲")).toBeVisible();
    await expect(page.getByText(FAKE_PROJECT_NAME)).toHaveCount(0);
    await expect(page.getByText("本地/演示兜底")).toHaveCount(0);

    // 真实空数组
    state.projects = [];
    await page.getByRole("button", { name: "刷新" }).click();
    await expect(page.getByText("暂无项目")).toBeVisible();
    await expect(page.getByText(FAKE_PROJECT_NAME)).toHaveCount(0);
    await expect(page.getByText("E2E真实技术标甲")).toHaveCount(0);

    await assertListPageBrowserStorageBoundary(page);
    assertCleanConsole(consoleLines);
  });

  test("技术标列表 API 失败不显示 local/mock，固定中文", async ({ page }) => {
    const state = createProbeState([
      makeProject({
        id: REAL_TECH_ID,
        name: "不应出现的真实项",
        kind: "technical",
      }),
    ]);
    state.listFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/technical-plan");
    await expect(page.getByText("项目列表加载失败，请稍后重试")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText(FAKE_PROJECT_NAME)).toHaveCount(0);
    await expect(page.getByText("不应出现的真实项")).toHaveCount(0);
    await expect(page.getByText("本地/演示兜底")).toHaveCount(0);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    await expect(page.getByText("projects_list_failed")).toHaveCount(0);
    await expect(page.getByText("/api/projects")).toHaveCount(0);

    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText);
    assertCleanConsole(consoleLines);

    await assertListPageBrowserStorageBoundary(page);
  });

  test("商务标真实列表/空态/失败；不补演示卡", async ({ page }) => {
    const state = createProbeState([
      makeProject({
        id: REAL_BIZ_ID,
        name: "E2E真实商务标乙",
        kind: "business",
      }),
    ]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/business-bid");
    await expect(page.getByRole("heading", { name: "商务标生成" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("E2E真实商务标乙")).toBeVisible();
    await expect(page.getByText("某市智慧交通综合管理平台 · 商务标")).toHaveCount(
      0,
    );
    await expect(page.getByText(FAKE_PROJECT_NAME)).toHaveCount(0);
    await expect(page.getByText("演示 mock")).toHaveCount(0);

    state.projects = [];
    await page.getByRole("button", { name: "刷新" }).click();
    await expect(
      page.getByText("暂无商务标项目，点击「从招标文件开始」创建。"),
    ).toBeVisible();

    state.listFail = true;
    await page.getByRole("button", { name: "刷新" }).click();
    await expect(
      page.getByText("商务标项目加载失败，请稍后重试"),
    ).toBeVisible();
    await expect(page.getByText("某市智慧交通综合管理平台 · 商务标")).toHaveCount(
      0,
    );
    await expect(page.getByText(SECRET)).toHaveCount(0);

    assertCleanConsole(consoleLines);
    await assertListPageBrowserStorageBoundary(page);
  });

  test("技术标新建页创建失败不假成功；重试为新的单次 POST", async ({
    page,
  }) => {
    const state = createProbeState([]);
    state.createFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/technical-plan/new");
    await expect(page.getByRole("heading", { name: "新建项目" })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByLabel("项目名称").fill("失败创建项目甲");

    const baseline = await readStorageSnapshot(page);
    assertProjectMetaKeysOnlyLegacyV1(baseline);

    await page.getByRole("button", { name: "创建并开始解析" }).click();
    await expect(page.getByText("项目创建失败，请稍后重试")).toBeVisible();
    await expect(page).toHaveURL(/\/technical-plan\/new/);
    expect(state.createPosts.length).toBe(1);
    expect(Object.keys(state.createPosts[0].body).sort()).toEqual(
      [
        "industry",
        "kind",
        "name",
        "status",
        "technicalPlanStep",
      ].sort(),
    );
    expect(state.createPosts[0].body.name).toBe("失败创建项目甲");
    expect(state.createPosts[0].body.kind).toBe("technical");

    const afterFirst = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(afterFirst, baseline, "新建失败第1次后");
    assertProjectMetaKeysOnlyLegacyV1(afterFirst);

    // 重试：又一次显式单次 POST
    await page.getByRole("button", { name: "创建并开始解析" }).click();
    await expect(page.getByText("项目创建失败，请稍后重试")).toBeVisible();
    expect(state.createPosts.length).toBe(2);
    await expect(page).toHaveURL(/\/technical-plan\/new/);
    await expect(page.getByText(SECRET)).toHaveCount(0);
    // 不得出现本地假 ID 形态文案
    await expect(page.getByText(/proj_[a-z0-9]+_[a-z0-9]+/)).toHaveCount(0);

    const afterSecond = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(afterSecond, baseline, "新建失败第2次后");
    assertProjectMetaKeysOnlyLegacyV1(afterSecond);
    assertCleanConsole(consoleLines);
  });

  test("创建方案页创建失败不导航；成功 POST 精确并绑定 pending", async ({
    page,
  }) => {
    const state = createProbeState([]);
    state.createFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/create");
    await expect(
      page.getByRole("heading", { name: "技术标生成" }),
    ).toBeVisible({
      timeout: 20_000,
    });

    // 提交前页面必须有非空文件名数组，成功后 pending.fileNames 精确相等
    await page.locator(".upload-card").click();
    await expect(page.getByText("招标文件-正式稿.pdf")).toBeVisible();
    const pageFileNames = ["招标文件-正式稿.pdf"];

    const baseline = await readStorageSnapshot(page);
    assertProjectMetaKeysOnlyLegacyV1(baseline);

    const cta = page.getByRole("button", { name: "开始生成技术标" });
    await cta.click();
    await expect(page.getByText("项目创建失败，请稍后重试")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page).toHaveURL(/\/create/);
    expect(state.createPosts.length).toBe(1);
    const failSnap = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(failSnap, baseline, "创建方案失败后");
    assertProjectMetaKeysOnlyLegacyV1(failSnap);

    // 成功路径
    state.createFail = false;
    await cta.click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 15_000 },
    );
    expect(state.createPosts.length).toBe(2);
    const okBody = state.createPosts[1].body;
    expect(Object.keys(okBody).sort()).toEqual(
      [
        "industry",
        "kind",
        "name",
        "status",
        "technicalPlanStep",
      ].sort(),
    );
    expect(okBody.kind).toBe("technical");
    expect(typeof okBody.name).toBe("string");
    expect(String(okBody.name).length).toBeGreaterThan(0);

    await expect
      .poll(async () => {
        const s = await readStorageSnapshot(page);
        return s.ss[PENDING_SS_KEY] || "";
      })
      .toContain(REAL_CREATE_ID);
    const okSnap = await readStorageSnapshot(page);
    // 项目元数据键族仍只 v1；允许技术标 editor/guidance 既有本地键
    assertTechWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    // sessionStorage 键集合精确只有 pending
    expect(okSnap.ssKeys).toEqual([PENDING_SS_KEY]);
    const pending = JSON.parse(okSnap.ss[PENDING_SS_KEY]) as {
      projectId: string;
      fileNames: string[];
    };
    expect(Object.keys(pending).sort()).toEqual(
      ["fileNames", "projectId"].sort(),
    );
    expect(pending.projectId).toBe(REAL_CREATE_ID);
    expect(pending.fileNames).toEqual(pageFileNames);
    expect(pending.fileNames.length).toBeGreaterThan(0);

    expect(await readIdbNames(page)).toEqual([]);
    expect(okSnap.cookies).toBe("");
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);

    assertCleanConsole(consoleLines);
  });

  test("商务标创建失败停留本页；成功导航真实 ID", async ({ page }) => {
    const state = createProbeState([]);
    state.createFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/business-bid");
    await expect(page.getByRole("heading", { name: "商务标生成" })).toBeVisible({
      timeout: 20_000,
    });

    const baseline = await readStorageSnapshot(page);
    assertProjectMetaKeysOnlyLegacyV1(baseline);

    await page.getByRole("button", { name: "从招标文件开始" }).click();
    await expect(page.getByText("项目创建失败，请稍后重试")).toBeVisible();
    await expect(page).toHaveURL(/\/business-bid$/);
    expect(state.createPosts.length).toBe(1);
    expect(state.createPosts[0].body.kind).toBe("business");
    await expect(page.getByText("某市智慧交通综合管理平台 · 商务标")).toHaveCount(
      0,
    );

    const failSnap = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(failSnap, baseline, "商务标创建失败后");
    assertProjectMetaKeysOnlyLegacyV1(failSnap);

    state.createFail = false;
    await page.getByRole("button", { name: "从招标文件开始" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/business-bid/${REAL_CREATE_ID}/parse`),
      { timeout: 15_000 },
    );
    expect(state.createPosts.length).toBe(2);
    expect(state.createPosts[1].body.kind).toBe("business");

    // 成功导航后允许商务标 editor-state 既有本地键；项目元数据仍只 v1；不得新增 session
    const okSnap = await readStorageSnapshot(page);
    assertBizWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    expect(okSnap.ssKeys).toEqual([]);
    expect(okSnap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);

    assertCleanConsole(consoleLines);
  });

  test("演示 ID 直达不能构造技术/商务工作区", async ({ page }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await installP11aRoutes(page, state);

    await page.goto(`/technical-plan/${DEMO_TECH_ID}/document`);
    await expect(page.getByRole("heading", { name: "我的项目" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page).toHaveURL(/\/technical-plan\/?$/);
    await expect(page.getByText("某市智慧交通综合管理平台技术标")).toHaveCount(0);

    await page.goto(`/business-bid/${DEMO_BIZ_ID}/parse`);
    await expect(page.getByText("未找到项目。")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("某市智慧交通综合管理平台 · 商务标")).toHaveCount(
      0,
    );

    assertCleanConsole(consoleLines);
  });

  test("查重/废标项目列表失败选项为空且无未处理拒绝", async ({ page }) => {
    const state = createProbeState([
      makeProject({
        id: REAL_TECH_ID,
        name: "选择器不应出现",
        kind: "technical",
      }),
    ]);
    state.listFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/duplicate-check");
    await expect(page.getByRole("heading", { name: "标书查重" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("项目列表加载失败，请稍后重试")).toBeVisible();
    const dupSelect = page.locator("#dup-project");
    await expect(dupSelect.locator("option")).toHaveCount(1);
    await expect(dupSelect.locator("option").first()).toHaveText("暂无技术标项目");
    await expect(page.getByText("选择器不应出现")).toHaveCount(0);
    await expect(page.getByText(FAKE_PROJECT_NAME)).toHaveCount(0);

    await page.goto("/rejection-check");
    await expect(page.getByRole("heading", { name: "废标项检查" })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("项目列表加载失败，请稍后重试")).toBeVisible();
    const rejSelect = page.locator("#rej-project");
    await expect(rejSelect.locator("option")).toHaveCount(1);
    await expect(rejSelect.locator("option").first()).toHaveText("暂无技术标项目");
    await expect(page.getByText("选择器不应出现")).toHaveCount(0);

    // pageerror 计入 appConsoleLines；未处理拒绝会使断言失败
    assertCleanConsole(consoleLines);
  });

  test("网络白名单：未知/api、projects前缀未知端点与外网阻断", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/technical-plan");
    await expect(page.getByRole("heading", { name: "我的项目" })).toBeVisible({
      timeout: 20_000,
    });

    // 进入页面后记录存储基线；三主动探测后必须全等
    const baseline = await readStorageSnapshot(page);
    assertProjectMetaKeysOnlyLegacyV1(baseline);

    // 主动探测未知 API 与 projects 前缀下未知端点
    await page.evaluate(async () => {
      await fetch("/api/unknown-p11a-probe").catch(() => undefined);
      await fetch("/api/projects/unknown-p11a-probe").catch(() => undefined);
      await fetch("https://example.invalid/p11a-probe").catch(() => undefined);
    });

    await expect
      .poll(() =>
        state.forbiddenHits.filter((h) => h.includes("/api/unknown-p11a-probe"))
          .length,
      )
      .toBeGreaterThanOrEqual(1);
    await expect
      .poll(() =>
        state.forbiddenHits.filter((h) =>
          h.includes("/api/projects/unknown-p11a-probe"),
        ).length,
      )
      .toBeGreaterThanOrEqual(1);
    await expect
      .poll(() =>
        state.externalHits.filter((h) => h.includes("example.invalid")).length,
      )
      .toBeGreaterThanOrEqual(1);

    // 精确：projects 前缀未知不得被宽放
    expect(
      state.forbiddenHits.some((h) =>
        h.includes("/api/projects/unknown-p11a-probe"),
      ),
    ).toBe(true);

    const afterProbe = await readStorageSnapshot(page);
    assertStorageSnapshotEqual(afterProbe, baseline, "三主动探测后");
    assertProjectMetaKeysOnlyLegacyV1(afterProbe);

    assertCleanConsole(consoleLines);
  });

  test("技术标新建成功：POST body/次数精确、导航真实 ID、存储边界", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installP11aRoutes(page, state);

    await page.goto("/technical-plan/new");
    await expect(page.getByRole("heading", { name: "新建项目" })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByLabel("项目名称").fill("P11A成功新建项目");
    await page.getByRole("button", { name: "创建并开始解析" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 15_000 },
    );
    expect(state.createPosts.length).toBe(1);
    const body = state.createPosts[0].body;
    expect(Object.keys(body).sort()).toEqual(
      [
        "industry",
        "kind",
        "name",
        "status",
        "technicalPlanStep",
      ].sort(),
    );
    expect(body.name).toBe("P11A成功新建项目");
    expect(body.kind).toBe("technical");
    expect(body.technicalPlanStep).toBe(1);
    expect(body.status).toBe("draft");

    // 允许技术标 editor/guidance 既有本地键；项目元数据仍只 v1
    const snap = await readStorageSnapshot(page);
    assertTechWorkspaceLocalKeys(snap, REAL_CREATE_ID);
    // 新建页无 fileNames → sessionStorage 完整键集合精确为空
    expect(snap.ssKeys).toEqual([]);
    expect(snap.ss[PENDING_SS_KEY] ?? null).toBe(null);
    expect(snap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);

    assertCleanConsole(consoleLines);
    const bodyText = await page.locator("body").innerText();
    // 页面可显示项目名，但不得显示 SECRET/code/路径
    expect(bodyText).not.toContain(SECRET);
    expect(bodyText).not.toContain("projects_create_failed");
    expect(bodyText).not.toContain("/api/projects");
  });
});
