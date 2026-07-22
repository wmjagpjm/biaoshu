/**
 * 模块：P8B 工作空间解析策略接线 E2E
 * 用途：验收 light/local/ask 在技术标与商务标入口的真实决策、失败收口、网络与存储边界。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；GET /settings/parse-strategy；parse 任务。
 * 二次开发：禁止真实云 Key、固定 sleep、业务 route 桩；失败用例仅对 parse-strategy 做单次故障注入。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Request,
} from "@playwright/test";

const API = "http://127.0.0.1:8010/api";
const STRATEGY_FAIL_MSG = "暂时无法读取解析策略，请稍后重试";
const SENSITIVE_LEAK = "SECRET_KEY_xyz_/settings/parse-strategy";
/** M3 managed 失败固定中文（契约冻结，禁止拼接 diagnosticCode/task.error） */
const MANAGED_FAIL_MSG = "本机自动 OCR 暂不可用，可改用人工本地回传";
const MANAGED_FAIL_LINK = "前往人工本地回传";
/** 真实空 manifest 时后端 task.error 固定文案；UI 必须遮罩不可见 */
const REAL_MANIFEST_TASK_ERROR = "运行时清单无效";
/**
 * 真实 Windows 盘符路径正则：匹配 C:\Models 与 C:/Models（单反斜杠或正斜杠）。
 * 禁止再用只匹配双反斜杠的旧式 /C:\\\\|C:\//。
 */
const REAL_DRIVE_PATH_RE = /[A-Za-z]:[\\/]/
/** U2 单点 POST /files 注入的固定 detail，须原样出现在 pipeline.error */
const NEW_UPLOAD_FAILURE = "NEW_UPLOAD_FAILURE";

type ParseStrategy = "light" | "managed" | "local" | "ask";

type CapturedRequest = {
  method: string;
  path: string;
  url: string;
  postData: string | null;
};

/** 用途：判断是否为既有 index.html 字体（非本任务引入）。 */
function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

/** 用途：写入工作空间 parseStrategy（disabled 下 require_owner 兼容）。 */
async function putParseStrategy(
  request: APIRequestContext,
  strategy: ParseStrategy,
): Promise<void> {
  const res = await request.put(`${API}/settings`, {
    data: {
      provider: "openai-compatible",
      apiBaseUrl: "https://api.deepseek.com/v1",
      apiKey: "",
      model: "deepseek-chat",
      parseStrategy: strategy,
    },
  });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { parseStrategy?: string };
  expect(body.parseStrategy).toBe(strategy);
}

/**
 * 用途：M3 单点 route 注入权威策略 GET，使前端 decision 读到 managed，
 *       避免旧后端 PUT managed 400 短路 failure-first 红测。
 * 对接：仅 GET /api/settings/parse-strategy；业务 task/files 仍走真实后端。
 */
async function injectParseStrategyGet(page: Page, strategy: ParseStrategy) {
  await page.route("**/api/settings/parse-strategy", async (route) => {
    if (route.request().method().toUpperCase() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({ parseStrategy: strategy }),
    });
  });
}

/** 用途：解析 task POST 体，精确断言 type/engine。 */
function parseTaskPostBody(
  postData: string | null,
): { type?: string; payload?: { engine?: string } } {
  if (!postData) return {};
  return JSON.parse(postData) as {
    type?: string;
    payload?: { engine?: string };
  };
}

/** 用途：统计 taskPosts 中 engine=lightweight 的增量（M3 必须为零）。 */
function countLightweightParsePosts(posts: CapturedRequest[]): number {
  return posts.filter((h) => {
    const body = parseTaskPostBody(h.postData);
    return (
      body.type === "parse" && body.payload?.engine === "lightweight"
    );
  }).length;
}

/** 用途：统计精确 engine 的 parse 任务 POST。 */
function countEngineParsePosts(
  posts: CapturedRequest[],
  engine: string,
): number {
  return posts.filter((h) => {
    const body = parseTaskPostBody(h.postData);
    return body.type === "parse" && body.payload?.engine === engine;
  }).length;
}

/** 用途：统计 page 发出的 PUT /api/settings（不含 request fixture）。 */
function countSettingsPuts(hits: CapturedRequest[]): number {
  return hits.filter(
    (h) =>
      h.method === "PUT" &&
      (h.path === "/api/settings" || h.path === "/api/settings/"),
  ).length;
}

/**
 * 用途：M3 技术入口精确「开始解析」（冻结中性文案，exact 禁止旧 UI 冒充）。
 * 既有 P8B 历史用例仍用 /轻量解析|解析/，不得改动。
 */
function parseActionButton(page: Page) {
  return page.getByRole("button", { name: "开始解析", exact: true });
}

/** 用途：创建技术标或商务标项目。 */
async function createProject(
  request: APIRequestContext,
  kind: "technical" | "business",
  name: string,
): Promise<string> {
  const res = await request.post(`${API}/projects`, {
    data: { name, kind, industry: "政务" },
  });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  expect(body.id).toBeTruthy();
  return body.id;
}

/** 用途：安装网络捕获与外网阻断（业务请求仍走真实后端）。 */
async function installNetworkGuard(page: Page): Promise<{
  apiHits: CapturedRequest[];
  externalHits: string[];
  taskPosts: CapturedRequest[];
}> {
  const apiHits: CapturedRequest[] = [];
  const externalHits: string[] = [];
  const taskPosts: CapturedRequest[] = [];

  page.on("request", (req: Request) => {
    const url = req.url();
    if (isLegacyFontUrl(url)) return;
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return;
    }
    const host = parsed.hostname;
    if (host !== "127.0.0.1" && host !== "localhost") {
      externalHits.push(url);
      return;
    }
    if (!parsed.pathname.startsWith("/api")) return;
    const method = req.method().toUpperCase();
    const hit: CapturedRequest = {
      method,
      path: parsed.pathname,
      url,
      postData: req.postData(),
    };
    apiHits.push(hit);
    if (method === "POST" && /\/tasks\/?$/.test(parsed.pathname)) {
      taskPosts.push(hit);
    }
  });

  await page.route("**/*", async (route) => {
    const url = route.request().url();
    if (isLegacyFontUrl(url)) {
      await route.continue();
      return;
    }
    try {
      const host = new URL(url).hostname;
      if (host !== "127.0.0.1" && host !== "localhost") {
        await route.abort("failed");
        return;
      }
    } catch {
      await route.abort("failed");
      return;
    }
    await route.continue();
  });

  return { apiHits, externalHits, taskPosts };
}

/** 用途：向隐藏 file input 写入可轻量解析的文本标书。 */
async function uploadTxtViaHiddenInput(page: Page, filename: string) {
  const input = page.locator('input[type="file"]').first();
  await input.setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(
      `# E2E 招标文件\n\n一、项目概况\nE2E解析正文应写入预览。\n二、资格条件\n具备相关资质。\n`,
      "utf8",
    ),
  });
}

/** 用途：断言浏览器未持久化 parseStrategy 决策结果。 */
async function assertNoStrategyPersistence(page: Page) {
  const storage = await page.evaluate(() => {
    const ls: Record<string, string> = {};
    const ss: Record<string, string> = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (k) ls[k] = localStorage.getItem(k) || "";
    }
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i);
      if (k) ss[k] = sessionStorage.getItem(k) || "";
    }
    return { ls, ss };
  });
  for (const [key, value] of Object.entries(storage.ls)) {
    // 设置页历史键可能存在，但本包禁止用其决定策略；此处断言无独立策略缓存键
    expect(key.toLowerCase()).not.toMatch(/parse[-_]?strategy/);
    if (key === "biaoshu.settings.v1") {
      // 允许设置页缓存存在；不得仅因本流程写入策略决策旁路键
      continue;
    }
    expect(value.toLowerCase()).not.toContain('"parsestrategy"');
  }
  for (const key of Object.keys(storage.ss)) {
    expect(key.toLowerCase()).not.toMatch(/parse[-_]?strategy/);
  }
}

/** 用途：列出项目任务，供引擎与是否创建断言。 */
async function listTasks(
  request: APIRequestContext,
  projectId: string,
): Promise<
  Array<{
    id: string;
    type: string;
    status: string;
    result?: { engine?: string } | null;
  }>
> {
  const res = await request.get(`${API}/projects/${projectId}/tasks`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as Array<{
    id: string;
    type: string;
    status: string;
    result?: { engine?: string } | null;
  }>;
}

test.describe("P8B 解析策略接线", () => {
  test("技术标 light：创建 lightweight 解析任务并写入预览", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "light");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B 技术标 light",
    );
    const net = await installNetworkGuard(page);

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 技术标 light" }),
    ).toBeVisible({ timeout: 20_000 });

    await uploadTxtViaHiddenInput(page, "e2e-light.txt");
    await expect(page.locator(".file-chip", { hasText: "e2e-light.txt" })).toBeVisible({
      timeout: 15_000,
    });

    await page.getByRole("button", { name: /轻量解析|解析/ }).click();
    await expect(page.getByText("解析完成，请查看右侧预览")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("E2E解析正文应写入预览")).toBeVisible({
      timeout: 15_000,
    });

    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        const parseOk = tasks.find(
          (t) => t.type === "parse" && t.status === "success",
        );
        return parseOk?.result?.engine ?? null;
      })
      .toBe("lightweight");

    expect(net.externalHits).toEqual([]);
    const strategyGets = net.apiHits.filter(
      (h) => h.method === "GET" && h.path === "/api/settings/parse-strategy",
    );
    expect(strategyGets.length).toBeGreaterThanOrEqual(1);
    const taskBodies = net.taskPosts.map((h) => h.postData || "");
    expect(taskBodies.some((b) => b.includes('"engine":"lightweight"'))).toBe(
      true,
    );
    await assertNoStrategyPersistence(page);
  });

  test("技术标 local：仅跳转本地回传页且无 tasks POST", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "local");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B 技术标 local",
    );
    const net = await installNetworkGuard(page);

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 技术标 local" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-local.txt");
    await expect(page.locator(".file-chip", { hasText: "e2e-local.txt" })).toBeVisible({
      timeout: 15_000,
    });

    const beforeTasks = net.taskPosts.length;
    await page.getByRole("button", { name: /轻量解析|解析/ }).click();

    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    await expect(page.getByRole("heading", { name: "本地解析插件" })).toBeVisible();
    await expect(page.locator("#pid")).toHaveValue(projectId);
    expect(net.taskPosts.length).toBe(beforeTasks);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("技术标 ask：取消不建任务；选轻量建任务；选本地跳转", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "ask");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B 技术标 ask",
    );
    const net = await installNetworkGuard(page);

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 技术标 ask" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-ask.txt");
    await expect(page.locator(".file-chip", { hasText: "e2e-ask.txt" })).toBeVisible({
      timeout: 15_000,
    });

    // 取消
    await page.getByRole("button", { name: /轻量解析|解析/ }).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await expect(
      dialog.getByText("本地回传，不在服务器启动 MinerU"),
    ).toBeVisible();
    const tasksAfterOpen = net.taskPosts.length;
    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(tasksAfterOpen);

    // 选轻量
    await page.getByRole("button", { name: /轻量解析|解析/ }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await dialog.getByRole("button", { name: "在线轻量解析" }).click();
    await expect(page.getByText("解析完成，请查看右侧预览")).toBeVisible({
      timeout: 30_000,
    });
    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        return tasks.some(
          (t) =>
            t.type === "parse" &&
            t.status === "success" &&
            t.result?.engine === "lightweight",
        );
      })
      .toBe(true);

    // 选本地
    await putParseStrategy(request, "ask");
    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 技术标 ask" }),
    ).toBeVisible({ timeout: 20_000 });
    const beforeLocal = net.taskPosts.length;
    await page.getByRole("button", { name: /轻量解析|解析/ }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await dialog.getByRole("button", { name: "本地 MinerU 回传" }).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(net.taskPosts.length).toBe(beforeLocal);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("商务标 local/ask：上传与整段重解析不再无条件自动轻量", async ({
    page,
    request,
  }) => {
    const projectId = await createProject(
      request,
      "business",
      "E2E P8B 商务标策略",
    );
    const net = await installNetworkGuard(page);

    // local：上传后跳转，无 tasks
    await putParseStrategy(request, "local");
    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标策略" }),
    ).toBeVisible({ timeout: 20_000 });
    const beforeUpload = net.taskPosts.length;
    await uploadTxtViaHiddenInput(page, "e2e-biz-local.txt");
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(net.taskPosts.length).toBe(beforeUpload);

    // ask：整段重解析弹框，取消不建任务
    await putParseStrategy(request, "ask");
    // 先保证已有文件：回到商务标页（文件仍在项目上）
    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标策略" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(".file-chip", { hasText: "e2e-biz-local.txt" })).toBeVisible({
      timeout: 15_000,
    });
    const beforeReparse = net.taskPosts.length;
    await page.getByRole("button", { name: "整段重解析" }).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(beforeReparse);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("商务标 ask：上传完成后出现选择框，取消不创建任务", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "ask");
    const projectId = await createProject(
      request,
      "business",
      "E2E P8B 商务标 ask 上传",
    );
    const net = await installNetworkGuard(page);

    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标 ask 上传" }),
    ).toBeVisible({ timeout: 20_000 });

    // 提示区「本地 MinerU 插件」须预填当前项目 ID
    await expect(
      page.getByRole("link", { name: "本地 MinerU 插件" }),
    ).toHaveAttribute(
      "href",
      `/local-parser?projectId=${encodeURIComponent(projectId)}`,
    );

    const beforeUpload = net.taskPosts.length;
    await uploadTxtViaHiddenInput(page, "e2e-biz-ask-upload.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-biz-ask-upload.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await expect(
      dialog.getByText("本地回传，不在服务器启动 MinerU"),
    ).toBeVisible();

    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(beforeUpload);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("策略读取失败：固定中文、不建任务、不泄漏详情", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "light");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B 策略失败",
    );
    const net = await installNetworkGuard(page);

    // 仅对策略读取做故障注入（非业务数据桩）
    await page.route("**/api/settings/parse-strategy", async (route) => {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "internal_error",
            message: SENSITIVE_LEAK,
          },
        }),
      });
    });

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 策略失败" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-fail.txt");
    await expect(page.locator(".file-chip", { hasText: "e2e-fail.txt" })).toBeVisible({
      timeout: 15_000,
    });

    const before = net.taskPosts.length;
    await page.getByRole("button", { name: /轻量解析|解析/ }).click();
    await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(SENSITIVE_LEAK)).toHaveCount(0);
    expect(net.taskPosts.length).toBe(before);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });
});

/**
 * M3：managed 策略接线 failure-first
 * 生产未改时须真实业务红；禁止 skip/xfail、宽泛 or、只查文案、light 冒充 managed。
 */
test.describe("P8B M3 managed 解析策略接线", () => {
  test("M3 设置页：精确 value=managed「本机自动 OCR」可选并保存", async ({
    page,
    request,
  }) => {
    // 基线先写 light，证明后续保存真写入 managed
    await putParseStrategy(request, "light");
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "解析策略" })).toBeVisible({
      timeout: 20_000,
    });

    const select = page.locator("#parse");
    await expect(select).toBeVisible();
    const managedOption = select.locator('option[value="managed"]');
    await expect(managedOption).toHaveCount(1);
    await expect(managedOption).toHaveText(/本机自动 OCR/);

    await select.selectOption("managed");
    await expect(select).toHaveValue("managed");

    // 设置页保存（真实 PUT，生产未扩四值时业务红）
    const saveBtn = page.getByRole("button", { name: /保存/ });
    await expect(saveBtn).toBeVisible();
    await saveBtn.click();

    await expect
      .poll(async () => {
        const res = await request.get(`${API}/settings`);
        if (!res.ok()) return null;
        const body = (await res.json()) as { parseStrategy?: string };
        return body.parseStrategy ?? null;
      })
      .toBe("managed");
  });

  test("M3 技术标 managed：精确一次 POST engine=managed，零 lightweight", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "light");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B M3 技术标 managed",
    );
    const net = await installNetworkGuard(page);
    // 单点注入策略 GET，绕过旧后端 PUT 400 短路
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 技术标 managed" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-m3-tech-managed.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-tech-managed.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const beforeAll = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    await parseActionButton(page).click();

    await expect
      .poll(() => countEngineParsePosts(net.taskPosts, "managed"), {
        timeout: 20_000,
      })
      .toBe(1);
    expect(net.taskPosts.length).toBe(beforeAll + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);

    const managedPost = net.taskPosts.find((h) => {
      const b = parseTaskPostBody(h.postData);
      return b.type === "parse" && b.payload?.engine === "managed";
    });
    expect(managedPost).toBeTruthy();
    expect(parseTaskPostBody(managedPost!.postData)).toEqual({
      type: "parse",
      payload: { engine: "managed" },
    });
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("M3 商务标 managed：真实 failed 终态后精确一次 managed POST，零 lightweight", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "light");
    const projectId = await createProject(
      request,
      "business",
      "E2E P8B M3 商务标 managed",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 商务标 managed" }),
    ).toBeVisible({ timeout: 20_000 });

    const beforeAll = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    // 商务标上传后按策略自动解析；须先等到真实 fixed error UI + 后端 failed
    await uploadTxtViaHiddenInput(page, "e2e-m3-biz-managed.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-biz-managed.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    const link = page.getByRole("link", { name: MANAGED_FAIL_LINK });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute(
      "href",
      `/local-parser?projectId=${encodeURIComponent(projectId)}`,
    );
    // 原 task.error 不可见；真实盘符路径不得泄漏（U3 商务处）
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);
    await expect(page.getByText(/diagnosticCode/i)).toHaveCount(0);
    await expect(page.getByText(/manifest/i)).toHaveCount(0);
    await expect(page.getByText(/BIAOSHU_MANAGED/i)).toHaveCount(0);
    await expect(page.getByText(REAL_DRIVE_PATH_RE)).toHaveCount(0);

    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        const failed = tasks.find(
          (t) =>
            t.type === "parse" &&
            t.status === "failed" &&
            t.result?.engine === "managed",
        );
        return failed ? "failed-managed" : "pending";
      })
      .toBe("failed-managed");

    // 终态后再锁计数（防异步补发 light 逃逸）
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(1);
    expect(net.taskPosts.length).toBe(beforeAll + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    const managedPost = net.taskPosts.find((h) => {
      const b = parseTaskPostBody(h.postData);
      return b.type === "parse" && b.payload?.engine === "managed";
    });
    expect(managedPost).toBeTruthy();
    expect(parseTaskPostBody(managedPost!.postData)).toEqual({
      type: "parse",
      payload: { engine: "managed" },
    });
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("M3 技术标 ask：按钮集合 light/managed/local/取消；取消零副作用；managed 精确一次且服务端策略仍 ask", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "ask");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B M3 技术标 ask",
    );
    const net = await installNetworkGuard(page);

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 技术标 ask" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-m3-ask.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-ask.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await parseActionButton(page).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });

    // 精确四按钮 exact（禁止兼容旧标签）
    const lightBtn = dialog.getByRole("button", {
      name: "轻量解析",
      exact: true,
    });
    const managedBtn = dialog.getByRole("button", {
      name: "本机自动 OCR",
      exact: true,
    });
    const localBtn = dialog.getByRole("button", {
      name: "人工本地回传",
      exact: true,
    });
    const cancelBtn = dialog.getByRole("button", {
      name: "取消",
      exact: true,
    });
    await expect(lightBtn).toBeVisible();
    await expect(managedBtn).toBeVisible();
    await expect(localBtn).toBeVisible();
    await expect(cancelBtn).toBeVisible();
    // 对话框内按钮集合精确 4（禁止额外引擎按钮）
    await expect(dialog.getByRole("button")).toHaveCount(4);

    const beforeCancel = net.taskPosts.length;
    await cancelBtn.click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(beforeCancel);

    // 再开并选 managed：须先等到真实失败终态，再断言 PUT/ask/计数（U1：禁止 POST 出现时提前检查）
    await parseActionButton(page).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    const putsBeforeManaged = countSettingsPuts(net.apiHits);
    const beforeManaged = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    await dialog
      .getByRole("button", { name: "本机自动 OCR", exact: true })
      .click();

    // U1：先等待 MANAGED_FAIL_MSG 与 listTasks 精确 failed-managed
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        const failed = tasks.find(
          (t) =>
            t.type === "parse" &&
            t.status === "failed" &&
            t.result?.engine === "managed",
        );
        return failed ? "failed-managed" : "pending";
      })
      .toBe("failed-managed");

    // 终态后再锁：page PUT /api/settings 增量=0、服务端仍 ask、managed=1/light 零增量
    expect(countSettingsPuts(net.apiHits)).toBe(putsBeforeManaged);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(1);
    expect(net.taskPosts.length).toBe(beforeManaged + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    expect(
      parseTaskPostBody(net.taskPosts[net.taskPosts.length - 1].postData),
    ).toEqual({ type: "parse", payload: { engine: "managed" } });

    const settingsRes = await request.get(`${API}/settings`);
    expect(settingsRes.ok()).toBeTruthy();
    const settingsBody = (await settingsRes.json()) as {
      parseStrategy?: string;
    };
    expect(settingsBody.parseStrategy).toBe("ask");
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);
  });

  test("M3 managed 空 manifest 真实失败：固定中文+项目化人工入口，零诊断泄漏", async ({
    page,
    request,
  }) => {
    await putParseStrategy(request, "light");
    const projectId = await createProject(
      request,
      "technical",
      "E2E P8B M3 managed 失败",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 managed 失败" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-m3-fail.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-fail.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const beforeLight = countLightweightParsePosts(net.taskPosts);
    await parseActionButton(page).click();

    // 真实后端 + 空白 MANIFEST：任务 failed；界面固定中文
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    const link = page.getByRole("link", { name: MANAGED_FAIL_LINK });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute(
      "href",
      `/local-parser?projectId=${encodeURIComponent(projectId)}`,
    );

    // 原 task.error「运行时清单无效」必须不可见；零 diagnosticCode/路径；不得二次 lightweight
    // U3 技术处：真实盘符路径正则（与商务处同源 REAL_DRIVE_PATH_RE）
    expect(REAL_DRIVE_PATH_RE.test("C:\\Models")).toBe(true);
    expect(REAL_DRIVE_PATH_RE.test("D:/Models")).toBe(true);
    expect(REAL_DRIVE_PATH_RE.test("relative/Models")).toBe(false);
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);
    await expect(page.getByText(/diagnosticCode/i)).toHaveCount(0);
    await expect(page.getByText(/manifest/i)).toHaveCount(0);
    await expect(page.getByText(/BIAOSHU_MANAGED/i)).toHaveCount(0);
    await expect(page.getByText(REAL_DRIVE_PATH_RE)).toHaveCount(0);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);

    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        const failed = tasks.find(
          (t) =>
            t.type === "parse" &&
            t.status === "failed" &&
            t.result?.engine === "managed",
        );
        return failed ? "failed-managed" : "pending";
      })
      .toBe("failed-managed");

    // 精确一次 managed POST
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(1);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page);

    // U2：已确认 managed fixed error 后，单点 route 当前项目下一次 POST /files
    // 固定 detail NEW_UPLOAD_FAILURE；GET/其它项目/其它方法继续真实后端
    const taskPostsBeforeInject = net.taskPosts.length;
    const managedBeforeInject = countEngineParsePosts(net.taskPosts, "managed");
    const lightBeforeInject = countLightweightParsePosts(net.taskPosts);
    let filesPostInjected = false;
    await page.route("**/api/projects/*/files**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      let pathname = "";
      try {
        pathname = new URL(req.url()).pathname;
      } catch {
        await route.continue();
        return;
      }
      const expectedPath = `/api/projects/${projectId}/files`;
      if (
        method === "POST" &&
        !filesPostInjected &&
        (pathname === expectedPath || pathname === `${expectedPath}/`)
      ) {
        filesPostInjected = true;
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({ detail: NEW_UPLOAD_FAILURE }),
        });
        return;
      }
      await route.continue();
    });

    // 同一隐藏 input 再上传；等待新 pipeline.error 精确可见
    await uploadTxtViaHiddenInput(page, "e2e-m3-fail-reupload.txt");
    await expect(page.getByText(NEW_UPLOAD_FAILURE, { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    // managed 固定中文与人工入口须隐藏（不得与当前 pipeline.error 混显）
    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toHaveCount(0);
    // taskPosts 总数 / managed / light 与注入前完全相等
    expect(net.taskPosts.length).toBe(taskPostsBeforeInject);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(
      managedBeforeInject,
    );
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBeforeInject);
    expect(net.externalHits).toEqual([]);
  });

  test("M3 技术标 local 保持零任务；light 保持 lightweight", async ({
    page,
    request,
  }) => {
    // local
    await putParseStrategy(request, "local");
    const localId = await createProject(
      request,
      "technical",
      "E2E P8B M3 local 保持",
    );
    const net = await installNetworkGuard(page);
    await page.goto(`/technical-plan/${localId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 local 保持" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-m3-local.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-local.txt" }),
    ).toBeVisible({ timeout: 15_000 });
    const beforeLocal = net.taskPosts.length;
    await parseActionButton(page).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(localId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(net.taskPosts.length).toBe(beforeLocal);

    // light
    await putParseStrategy(request, "light");
    const lightId = await createProject(
      request,
      "technical",
      "E2E P8B M3 light 保持",
    );
    await page.goto(`/technical-plan/${lightId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 light 保持" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-m3-light.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-light.txt" }),
    ).toBeVisible({ timeout: 15_000 });
    const lightBefore = countEngineParsePosts(net.taskPosts, "lightweight");
    await parseActionButton(page).click();
    await expect
      .poll(() => countEngineParsePosts(net.taskPosts, "lightweight"), {
        timeout: 30_000,
      })
      .toBe(lightBefore + 1);
    expect(
      parseTaskPostBody(net.taskPosts[net.taskPosts.length - 1].postData),
    ).toEqual({ type: "parse", payload: { engine: "lightweight" } });
    expect(net.externalHits).toEqual([]);
  });
});
