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

type ParseStrategy = "light" | "local" | "ask";

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
