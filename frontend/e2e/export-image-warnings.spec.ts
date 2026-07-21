/**
 * 模块：P9D 导出图片失效引用浏览器提示 E2E
 * 用途：验收技术标/商务标导出成功后展示 result.imageWarnings 且继续本机下载；收敛与清空边界。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；export 任务 result.imageWarnings。
 * 二次开发：主路径走真实本机 export；非法结构仅受控桩；禁止外网、固定 sleep、并行 worker。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

const API = "http://127.0.0.1:8010/api";
const INVALID_IMAGE_LINE = "![非法图](biaoshu-image://../outside)";
const EXPECTED_WARNING_SNIPPET = "图片引用无效";
const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key|imageWarnings/i;

/** 用途：判断是否为既有 index.html 字体（非本任务引入）。 */
function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

/** 用途：安装 window.open 桩，仅记录本机 URL，禁止真实弹窗。 */
async function installOpenStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as Window & { __p9dOpenCalls?: string[] };
    w.__p9dOpenCalls = [];
    window.open = (url?: string | URL) => {
      w.__p9dOpenCalls = w.__p9dOpenCalls || [];
      w.__p9dOpenCalls.push(String(url ?? ""));
      return null;
    };
  });
}

async function getOpenCalls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as Window & { __p9dOpenCalls?: string[] };
    return [...(w.__p9dOpenCalls || [])];
  });
}

/**
 * 用途：释放 export 成功响应后观察 window.open 是否新增；超时无新增返回 false。
 * 对接：V1-E 迟到 success 零下载；事件驱动 waitForFunction，禁止 sleep。
 */
async function openCallsIncreased(
  page: Page,
  baseline: number,
  timeoutMs = 5_000,
): Promise<boolean> {
  try {
    await page.waitForFunction(
      (n) => {
        const w = window as Window & { __p9dOpenCalls?: string[] };
        return (w.__p9dOpenCalls || []).length > n;
      },
      baseline,
      { timeout: timeoutMs },
    );
    return true;
  } catch {
    return false;
  }
}

/** 用途：网络捕获与外网阻断；业务请求默认继续真实后端。 */
async function installNetworkGuard(page: Page): Promise<{
  externalHits: string[];
  apiHits: Array<{ method: string; path: string; url: string }>;
}> {
  const externalHits: string[] = [];
  const apiHits: Array<{ method: string; path: string; url: string }> = [];

  page.on("request", (req: Request) => {
    const url = req.url();
    if (isLegacyFontUrl(url)) return;
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return;
    }
    if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "localhost") {
      externalHits.push(url);
      return;
    }
    if (!parsed.pathname.startsWith("/api")) return;
    apiHits.push({
      method: req.method().toUpperCase(),
      path: parsed.pathname,
      url,
    });
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

  return { externalHits, apiHits };
}

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

/** 用途：技术标写入含无效项目图片独占行的章节。 */
async function seedTechnicalInvalidImage(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const projectId = await createProject(request, "technical", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      outline: [{ id: "node_p9d", title: "配图章节", children: [] }],
      chapters: [
        {
          id: "chap_p9d",
          title: "配图章节",
          body: `正文前缀\n${INVALID_IMAGE_LINE}\n`,
          preview: "配图",
          wordCount: 2,
          status: "done",
        },
      ],
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();
  return projectId;
}

/** 用途：商务标写入含无效图片独占行的 parsedMarkdown。 */
async function seedBusinessInvalidImage(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const projectId = await createProject(request, "business", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      parsedMarkdown: `条款摘要\n${INVALID_IMAGE_LINE}\n`,
      businessQualify: [
        {
          id: "q1",
          requirement: "法人",
          response: "有",
          evidence: "",
          status: "matched",
        },
      ],
      businessCommit: [
        {
          id: "c1",
          title: "承诺",
          body: "正式承诺正文。",
          needsStamp: true,
        },
      ],
    },
  });
  expect(put.ok()).toBeTruthy();
  return projectId;
}

async function assertNoSensitiveStorage(page: Page) {
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
  for (const [key, value] of Object.entries({ ...storage.ls, ...storage.ss })) {
    expect(key).not.toMatch(SENSITIVE_STORAGE_RE);
    expect(value).not.toMatch(SENSITIVE_STORAGE_RE);
  }
}

function isLocalApiUrl(url: string): boolean {
  try {
    const u = new URL(url, "http://127.0.0.1:5174");
    return (
      (u.hostname === "127.0.0.1" || u.hostname === "localhost") &&
      u.pathname.includes("/export/download/")
    );
  } catch {
    return false;
  }
}

/**
 * 用途：在同一文档内软切换 technical-plan 路由参数。
 * 避免 page.goto 整页卸载中止飞行中的 export，从而复现迟到污染竞态。
 */
async function softNavigateTechnicalPlan(
  page: Page,
  projectId: string,
  step = "export",
): Promise<void> {
  const url = `/technical-plan/${projectId}/${step}`;
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

/**
 * 用途：对 export 任务 POST 返回受控成功结果（仅边缘结构用例）；其它请求 continue。
 */
async function stubExportTaskSuccess(
  page: Page,
  projectId: string,
  imageWarnings: unknown,
  opts?: { storedName?: string },
): Promise<void> {
  const storedName = opts?.storedName ?? "p9d-stub-export.docx";
  await page.route("**/api/projects/**/tasks**", async (route: Route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = req.url();
    if (method !== "POST") {
      await route.continue();
      return;
    }
    if (url.includes("/events") || /\/tasks\/[^/?]+/.test(new URL(url).pathname)) {
      await route.continue();
      return;
    }
    let type = "";
    try {
      const body = req.postDataJSON() as { type?: string };
      type = body?.type || "";
    } catch {
      type = "";
    }
    if (type !== "export") {
      await route.continue();
      return;
    }
    if (!url.includes(projectId)) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({
        id: `task_p9d_stub_${Date.now()}`,
        type: "export",
        status: "success",
        progress: 100,
        message: "导出完成",
        result: {
          storedName,
          downloadPath: `/projects/${projectId}/export/download/${storedName}`,
          size: 2048,
          mode: "technical",
          imageWarnings,
        },
      }),
    });
  });
}

test.describe("P9D 导出图片告警", () => {
  test("技术标真实 export：显示后端告警且继续本机下载", async ({
    page,
    request,
  }) => {
    const projectId = await seedTechnicalInvalidImage(
      request,
      "E2E P9D 技术标图片告警",
    );
    await installOpenStub(page);
    const net = await installNetworkGuard(page);

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(
      page.getByRole("heading", { name: "E2E P9D 技术标图片告警" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("准备导出 Word")).toBeVisible();

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    const warningRegion = page.getByRole("region", { name: "导出图片告警" });
    await expect(warningRegion).toBeVisible({ timeout: 45_000 });
    await expect(warningRegion.getByText(EXPECTED_WARNING_SNIPPET)).toBeVisible();
    await expect(
      warningRegion.getByText("Word 已生成并继续下载，请在文档中检查降级位置"),
    ).toBeVisible();

    await expect
      .poll(async () => (await getOpenCalls(page)).length, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);
    const opens = await getOpenCalls(page);
    expect(opens.every(isLocalApiUrl)).toBe(true);
    expect(opens.some((u) => u.includes(`/projects/${projectId}/export/download/`))).toBe(
      true,
    );
    expect(net.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("商务标真实 export：显示后端告警且继续本机下载", async ({
    page,
    request,
  }) => {
    const projectId = await seedBusinessInvalidImage(
      request,
      "E2E P9D 商务标图片告警",
    );
    await installOpenStub(page);
    const net = await installNetworkGuard(page);

    await page.goto(`/business-bid/${projectId}/export`);
    await expect(
      page.getByRole("heading", { name: "E2E P9D 商务标图片告警" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible();

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    const warningRegion = page.getByRole("region", { name: "导出图片告警" });
    await expect(warningRegion).toBeVisible({ timeout: 45_000 });
    await expect(warningRegion.getByText(EXPECTED_WARNING_SNIPPET)).toBeVisible();

    await expect
      .poll(async () => (await getOpenCalls(page)).length, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);
    const opens = await getOpenCalls(page);
    expect(opens.every(isLocalApiUrl)).toBe(true);
    expect(opens.some((u) => u.includes(`/projects/${projectId}/export/download/`))).toBe(
      true,
    );
    expect(net.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("受控桩：非法/超量/超长收敛、HTML 文本不解释、后续无告警清空", async ({
    page,
    request,
  }) => {
    const projectId = await createProject(
      request,
      "technical",
      "E2E P9D 结构收敛",
    );
    const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
      data: {
        chapters: [
          {
            id: "chap_clean",
            title: "干净章节",
            body: "无图片引用。\n",
            status: "done",
          },
        ],
      },
    });
    expect(put.ok()).toBeTruthy();

    await installOpenStub(page);
    const net = await installNetworkGuard(page);

    // 单码点串重复，确保 Array.from 码点数远超 240
    const longWarning = `${"长".repeat(300)}告警中文与emoji🚀尾`;
    const htmlPayload =
      '<img src=x onerror=alert(1)><a href="https://evil.example">点我</a>';
    const many = Array.from({ length: 25 }, (_, i) => `告警条目${i + 1}`);
    // 合法项优先放入：HTML、超长，再填充超量；非法项应被丢弃
    const mixed: unknown[] = [
      null,
      12,
      {},
      "",
      "   ",
      htmlPayload,
      longWarning,
      ...many,
    ];

    await stubExportTaskSuccess(page, projectId, mixed);

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(
      page.getByRole("heading", { name: "E2E P9D 结构收敛" }),
    ).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    const warningRegion = page.getByRole("region", { name: "导出图片告警" });
    await expect(warningRegion).toBeVisible({ timeout: 20_000 });
    await expect(warningRegion.getByText(/共\s*20\s*条/)).toBeVisible();
    await expect(warningRegion.getByText(htmlPayload, { exact: true })).toBeVisible();
    // HTML 不得被解释为真实节点
    await expect(warningRegion.locator("img")).toHaveCount(0);
    await expect(warningRegion.locator("a")).toHaveCount(0);
    // 非字符串与空串丢弃后，列表最多 20 条（html + 超长 + 18 条序号）
    await expect(warningRegion.getByRole("listitem")).toHaveCount(20);
    await expect(warningRegion.getByText("告警条目19")).toHaveCount(0);
    // 超长按 Unicode 码点截断至 240；不得整段原样出现
    const longItem = warningRegion.getByRole("listitem").nth(1);
    const longText = (await longItem.innerText()).trim();
    expect(Array.from(longText).length).toBe(240);
    expect(longText.includes("🚀尾")).toBe(false);
    expect(longText.startsWith("长")).toBe(true);

    await expect
      .poll(async () => (await getOpenCalls(page)).length)
      .toBeGreaterThanOrEqual(1);

    // 下一成功导出无告警 → 清空
    await page.unroute("**/api/projects/**/tasks**");
    await stubExportTaskSuccess(page, projectId, undefined, {
      storedName: "p9d-stub-clean.docx",
    });
    // 再装一层 open stub 计数延续（init 已生效）
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
      { timeout: 20_000 },
    );
    await expect
      .poll(async () => (await getOpenCalls(page)).length)
      .toBeGreaterThanOrEqual(2);

    expect(net.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("项目切换：挂起 A export success 后切到 B，迟到响应不污染 B 告警且不触发下载", async ({
    page,
    request,
  }) => {
    // V1-E 后续语义：A 的 export success 迟到响应不得污染 B 告警，且不得触发 window.open 下载
    const projectA = await seedTechnicalInvalidImage(
      request,
      "E2E P9D 迟到隔离A",
    );
    const projectB = await createProject(
      request,
      "technical",
      "E2E P9D 迟到隔离B",
    );
    const putB = await request.put(`${API}/projects/${projectB}/editor-state`, {
      data: {
        chapters: [
          {
            id: "chap_b_clean",
            title: "B干净章节",
            body: "项目B无无效图片。\n",
            status: "done",
          },
        ],
      },
    });
    expect(putB.ok()).toBeTruthy();

    await installOpenStub(page);
    const net = await installNetworkGuard(page);

    const LATE_WARNING = "项目A迟到告警标记_UNIQUE_P9D_LATE";
    const storedNameA = "p9d-late-a.docx";
    const aDownloadMarker = `/projects/${projectA}/export/download/${storedNameA}`;
    let releaseExportA!: () => void;
    const exportAHeld = new Promise<void>((resolve) => {
      releaseExportA = resolve;
    });
    let markExportASeen!: () => void;
    const exportASeen = new Promise<void>((resolve) => {
      markExportASeen = resolve;
    });

    // 仅挂起项目 A 的 export 成功响应；用请求 Promise 同步，禁止 fixed sleep
    await page.route("**/api/projects/**/tasks**", async (route: Route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = req.url();
      if (method !== "POST") {
        await route.continue();
        return;
      }
      if (url.includes("/events") || /\/tasks\/[^/?]+/.test(new URL(url).pathname)) {
        await route.continue();
        return;
      }
      let type = "";
      try {
        type = (req.postDataJSON() as { type?: string })?.type || "";
      } catch {
        type = "";
      }
      if (type !== "export" || !url.includes(projectA)) {
        await route.continue();
        return;
      }
      markExportASeen();
      await exportAHeld;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          id: `task_p9d_late_${Date.now()}`,
          type: "export",
          status: "success",
          progress: 100,
          message: "导出完成",
          result: {
            storedName: storedNameA,
            downloadPath: aDownloadMarker,
            size: 2048,
            mode: "technical",
            imageWarnings: [LATE_WARNING],
          },
        }),
      });
    });

    await page.goto(`/technical-plan/${projectA}/export`);
    await expect(
      page.getByRole("heading", { name: "E2E P9D 迟到隔离A" }),
    ).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 同步点：A 的 export 请求已到达且仍被挂起
    await exportASeen;

    // SPA 软导航到 B：同一文档内切换，保留 A 飞行中的闭包
    await softNavigateTechnicalPlan(page, projectB, "export");
    await expect(
      page.getByRole("heading", { name: "E2E P9D 迟到隔离B" }),
    ).toBeVisible({ timeout: 20_000 });

    // 首帧起：B 不得出现 A 的告警区/专属文案
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(page.getByText(LATE_WARNING)).toHaveCount(0);

    // release 前记录 opensBefore 与 A downloadPath marker；建立响应同步
    const opensBefore = (await getOpenCalls(page)).length;
    const lateSuccessResponse = page.waitForResponse(
      async (res) => {
        if (res.status() !== 201) return false;
        const req = res.request();
        if (req.method().toUpperCase() !== "POST") return false;
        const url = req.url();
        let pathname = "";
        try {
          pathname = new URL(url).pathname;
        } catch {
          return false;
        }
        if (!pathname.includes(`/projects/${projectA}/tasks`)) return false;
        if (url.includes("/events") || /\/tasks\/[^/?]+$/.test(pathname)) {
          return false;
        }
        try {
          const body = req.postDataJSON() as { type?: string };
          if (body?.type !== "export") return false;
        } catch {
          return false;
        }
        return true;
      },
      { timeout: 15_000 },
    );

    // 释放 A 的迟到 success 并证明响应已交付；其后必须零 window.open
    releaseExportA();
    await lateSuccessResponse;

    const openIncreased = await openCallsIncreased(page, opensBefore, 5_000);
    expect(
      openIncreased,
      "V1-E：A export success 迟到交付后 window.open 必须精确零新增",
    ).toBe(false);
    const calls = await getOpenCalls(page);
    expect(calls.length).toBe(opensBefore);
    expect(
      calls.some((u) => u.includes(aDownloadMarker)),
      "V1-E：A downloadPath marker 不得出现在 window.open 记录中",
    ).toBe(false);

    // 迟到 success 已交付后，B 仍零告警且仍在 B 页
    await expect(page.getByText(LATE_WARNING)).toHaveCount(0);
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(
      page.getByRole("heading", { name: "E2E P9D 迟到隔离B" }),
    ).toBeVisible();

    expect(net.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
