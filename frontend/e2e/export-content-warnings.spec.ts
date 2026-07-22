/**
 * 模块：V1-H2 技术标导出正文完整性提醒 E2E（failure-first）
 * 用途：证明 export success 的 contentWarnings 在技术标页独立展示且不阻断同源 Blob 下载；
 *       覆盖恶意收敛、干净重导出清空与 A→B 迟到隔离。
 * 对接：Playwright chromium；本机后端 8010 / 前端 5174；真实技术标导出页 + 受控 route/task/download。
 * 二次开发：禁止 waitForTimeout/setTimeout/sleep、skip/fixme/only、宽泛 or、私有 React 函数、
 *       源码扫描、外网与真实业务数据；生产未实现时必须真实失败。
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

/** 集中合成锚点：唯一、可判定、禁止回显到安全/敏感通道 */
const ANCHOR = {
  contentWarn: "V1H2_CONTENT_WARN_ANCHOR_7c3e",
  contentLateA: "V1H2_CONTENT_LATE_A_UNIQUE_9f2b",
  contentB: "V1H2_CONTENT_B_WARN_ANCHOR_4d1a",
  imageWarn: "V1H2_IMAGE_WARN_ANCHOR_1e8c",
  imageMix: "V1H2_IMAGE_MIX_ANCHOR_6b0d",
} as const;

const CONTENT_REGION = "正文完整性提醒";
const IMAGE_REGION = "导出图片告警";
const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key|contentWarnings|imageWarnings/i;

/** 合成非空 DOCX 字节，供受控下载 GET。 */
const DOCX_BYTES = Buffer.from(
  "PK\u0003\u0004" + "V1H2_SYNTH_DOCX_" + "w".repeat(180),
  "binary",
);

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
};

type DownloadGetRecord = {
  projectId: string;
  method: string;
  path: string;
  url: string;
  storedName: string;
  seq: number;
};

type ExportRecord = {
  projectId: string;
  method: string;
  path: string;
  bodyText: string;
  type: string;
  seq: number;
};

type Ledger = {
  downloads: DownloadGetRecord[];
  exports: ExportRecord[];
  externalHits: string[];
  otherDownloadPaths: string[];
  nextSeq: () => number;
};

const activeHoldGates = new Set<HoldGate>();

function createHoldGate(): HoldGate {
  let released = false;
  const waiters: Array<() => void> = [];
  const gate: HoldGate = {
    wait: () =>
      released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          }),
    release: () => {
      released = true;
      while (waiters.length > 0) {
        const w = waiters.shift();
        w?.();
      }
      activeHoldGates.delete(gate);
    },
    isReleased: () => released,
  };
  activeHoldGates.add(gate);
  return gate;
}

function createLedger(): Ledger {
  let seq = 0;
  return {
    downloads: [],
    exports: [],
    externalHits: [],
    otherDownloadPaths: [],
    nextSeq: () => {
      seq += 1;
      return seq;
    },
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

function projectIdFromApiUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const m = u.pathname.match(/\/api\/projects\/([^/]+)\//);
    return m?.[1] ?? null;
  } catch {
    return null;
  }
}

function isExportTaskPost(req: Request): boolean {
  if (req.method().toUpperCase() !== "POST") return false;
  try {
    const u = new URL(req.url());
    if (!/\/api\/projects\/[^/]+\/tasks\/?$/.test(u.pathname)) return false;
    if (u.pathname.includes("/events")) return false;
    const raw = req.postData() || "";
    if (!raw) return false;
    const body = JSON.parse(raw) as { type?: string };
    return body.type === "export";
  } catch {
    return false;
  }
}

function isDownloadGet(req: Request): boolean {
  if (req.method().toUpperCase() !== "GET") return false;
  try {
    const u = new URL(req.url());
    return /\/api\/projects\/[^/]+\/export\/download\/[^/]+\/?$/.test(
      u.pathname,
    );
  } catch {
    return false;
  }
}

function storedNameFromDownloadUrl(url: string): string {
  try {
    const u = new URL(url);
    const m = u.pathname.match(/\/export\/download\/([^/]+)\/?$/);
    return m?.[1] ? decodeURIComponent(m[1]) : "";
  } catch {
    return "";
  }
}

function downloadsFor(ledger: Ledger, projectId: string): DownloadGetRecord[] {
  return ledger.downloads.filter((d) => d.projectId === projectId);
}

function exportsFor(ledger: Ledger, projectId: string): ExportRecord[] {
  return ledger.exports.filter((e) => e.projectId === projectId);
}

/** 用途：安装 window.open 桩，仅记录本机 URL，禁止真实弹窗。 */
async function installOpenStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as Window & { __v1h2OpenCalls?: string[] };
    w.__v1h2OpenCalls = [];
    window.open = (url?: string | URL) => {
      w.__v1h2OpenCalls = w.__v1h2OpenCalls || [];
      w.__v1h2OpenCalls.push(String(url ?? ""));
      return null;
    };
  });
}

async function getOpenCalls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as Window & { __v1h2OpenCalls?: string[] };
    return [...(w.__v1h2OpenCalls || [])];
  });
}

/**
 * 用途：观察 browser download 是否新增；超时无新增返回 false。
 * 对接：迟到 success 零下载；事件驱动，禁止 sleep。
 */
async function downloadEventsIncreased(
  page: Page,
  getCount: () => number,
  baseline: number,
  timeoutMs = 5_000,
): Promise<boolean> {
  if (getCount() > baseline) return true;
  try {
    await page.waitForEvent("download", { timeout: timeoutMs });
  } catch {
    return getCount() > baseline;
  }
  return getCount() > baseline;
}

async function openCallsIncreased(
  page: Page,
  baseline: number,
  timeoutMs = 2_000,
): Promise<boolean> {
  try {
    await page.waitForFunction(
      (n) => {
        const w = window as Window & { __v1h2OpenCalls?: string[] };
        return (w.__v1h2OpenCalls || []).length > n;
      },
      baseline,
      { timeout: timeoutMs },
    );
    return true;
  } catch {
    return false;
  }
}

/** 用途：外网阻断 + 旁路记录；业务请求默认 continue。 */
async function installNetworkGuard(page: Page, ledger: Ledger): Promise<void> {
  page.on("request", (req: Request) => {
    const url = req.url();
    if (isLegacyFontUrl(url)) return;
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return;
    }
    if (!isLocalHost(parsed.hostname)) {
      ledger.externalHits.push(url);
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
      if (!isLocalHost(host)) {
        await route.abort("failed");
        return;
      }
    } catch {
      await route.abort("failed");
      return;
    }
    await route.continue();
  });
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

async function createProject(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const res = await request.post(`${API}/projects`, {
    data: { name, kind: "technical", industry: "政务" },
  });
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { id: string };
  expect(body.id).toBeTruthy();
  return body.id;
}

/** 用途：技术标写入干净章节，确保真实 editor-state 可用。 */
async function seedTechnical(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const projectId = await createProject(request, name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      outline: [{ id: "node_v1h2", title: "V1H2章节", children: [] }],
      chapters: [
        {
          id: "chap_v1h2",
          title: "V1H2章节",
          body: "V1H2_TECH_BODY_SEED\n",
          preview: "v1h2",
          wordCount: 16,
          status: "done",
        },
      ],
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();
  return projectId;
}

/**
 * 用途：在同一文档内软切换 technical-plan 路由参数。
 * 避免 page.goto 整页卸载中止飞行中的 export。
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

type ExportStubOpts = {
  projectId: string;
  storedName: string;
  contentWarnings?: unknown;
  imageWarnings?: unknown;
  /** 省略字段：不写入 result.contentWarnings（模拟生产缺口） */
  omitContentWarnings?: boolean;
  gate?: HoldGate;
  onSeen?: (rec: ExportRecord) => void;
  filename?: string;
};

/**
 * 用途：受控 export 任务 POST 成功结果；可注入 contentWarnings / imageWarnings 与挂起 gate。
 */
async function installExportSuccessStub(
  page: Page,
  ledger: Ledger,
  opts: ExportStubOpts,
): Promise<void> {
  await page.route("**/api/projects/**/tasks**", async (route: Route) => {
    const req = route.request();
    if (!isExportTaskPost(req)) {
      await route.continue();
      return;
    }
    const pid = projectIdFromApiUrl(req.url());
    if (pid !== opts.projectId) {
      await route.continue();
      return;
    }
    const rec: ExportRecord = {
      projectId: pid,
      method: "POST",
      path: new URL(req.url()).pathname,
      bodyText: req.postData() || "",
      type: "export",
      seq: ledger.nextSeq(),
    };
    ledger.exports.push(rec);
    opts.onSeen?.(rec);
    if (opts.gate) {
      await opts.gate.wait();
    }
    const result: Record<string, unknown> = {
      storedName: opts.storedName,
      downloadPath: `/projects/${pid}/export/download/${opts.storedName}`,
      size: DOCX_BYTES.length,
      mode: "technical",
      filename: opts.filename ?? "v1h2-stub.docx",
      imageWarnings: opts.imageWarnings ?? [],
    };
    if (!opts.omitContentWarnings) {
      result.contentWarnings = opts.contentWarnings ?? [];
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({
        id: `task_v1h2_${ledger.nextSeq()}`,
        type: "export",
        status: "success",
        progress: 100,
        message: "导出完成",
        result,
      }),
    });
  });
}

/**
 * 用途：按 project 精确归属记录下载 GET，并返回合成 DOCX。
 * 可选 gate/onSeen：仅首例用于「GET 已到且未 fulfill」时证明正文提醒先于下载完成；
 * 其它用例不传 gate，下载语义保持立即 fulfill。
 */
async function installDownloadGetStub(
  page: Page,
  ledger: Ledger,
  opts: {
    byProject: Record<string, { storedName: string; filename?: string }>;
    gate?: HoldGate;
    onSeen?: (rec: DownloadGetRecord) => void;
  },
): Promise<void> {
  await page.route(
    "**/api/projects/**/export/download/**",
    async (route: Route) => {
      const req = route.request();
      if (!isDownloadGet(req)) {
        await route.continue();
        return;
      }
      const url = req.url();
      const pid = projectIdFromApiUrl(url);
      const stored = storedNameFromDownloadUrl(url);
      const path = new URL(url).pathname;
      if (!pid || !opts.byProject[pid]) {
        ledger.otherDownloadPaths.push(path);
        await route.abort("failed");
        return;
      }
      const allowed = opts.byProject[pid].storedName;
      if (stored !== allowed) {
        ledger.otherDownloadPaths.push(path);
        await route.abort("failed");
        return;
      }
      const rec: DownloadGetRecord = {
        projectId: pid,
        method: "GET",
        path,
        url,
        storedName: stored,
        seq: ledger.nextSeq(),
      };
      ledger.downloads.push(rec);
      opts.onSeen?.(rec);
      // 可选挂起：观测 GET 已记账后，在 release 前完成 UI 断言
      if (opts.gate) {
        await opts.gate.wait();
      }
      const filename = opts.byProject[pid].filename ?? "v1h2-stub.docx";
      await route.fulfill({
        status: 200,
        contentType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers: {
          "Content-Disposition": `attachment; filename="${filename}"`,
          "Cache-Control": "no-store",
        },
        body: DOCX_BYTES,
      });
    },
  );
}

test.describe("V1-H2 技术标导出正文完整性提醒", () => {
  test.afterEach(() => {
    for (const gate of [...activeHoldGates]) {
      gate.release();
    }
    activeHoldGates.clear();
  });

  test("成功 export：contentWarnings 与 imageWarnings 独立展示且先于一次下载", async ({
    page,
    request,
  }) => {
    const name = "E2E V1H2 双告警并存";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_a1b2c311.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    // F1：挂起下载 GET fulfill，证明 region 先于下载完成
    const downloadGate = createHoldGate();
    let downloadGetSeenResolve!: () => void;
    const downloadGetSeen = new Promise<void>((r) => {
      downloadGetSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
      contentWarnings: [ANCHOR.contentWarn],
      imageWarnings: [ANCHOR.imageMix],
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { storedName } },
      gate: downloadGate,
      onSeen: () => downloadGetSeenResolve(),
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    // 同步点：下载 GET 已到且 gate 未释放（禁止固定 sleep）
    await downloadGetSeen;
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].method).toBe("GET");
    expect(downloadsFor(ledger, projectId)[0].storedName).toBe(storedName);
    expect(downloadGate.isReleased()).toBe(false);
    // 契约：GET 已观测且 fulfill 前，正文/图片 region 必须已展示且语义分离
    const contentRegion = page.getByRole("region", { name: CONTENT_REGION });
    await expect(contentRegion).toBeVisible({ timeout: 20_000 });
    await expect(
      contentRegion.getByText(ANCHOR.contentWarn, { exact: true }),
    ).toBeVisible();
    const imageRegion = page.getByRole("region", { name: IMAGE_REGION });
    await expect(imageRegion).toBeVisible();
    await expect(
      imageRegion.getByText(ANCHOR.imageMix, { exact: true }),
    ).toBeVisible();
    await expect(contentRegion.getByText(ANCHOR.imageMix)).toHaveCount(0);
    await expect(imageRegion.getByText(ANCHOR.contentWarn)).toHaveCount(0);
    await expect(contentRegion.getByText(IMAGE_REGION)).toHaveCount(0);
    await expect(imageRegion.getByText(CONTENT_REGION)).toHaveCount(0);
    // fulfill 前 browser download 仍为零
    expect(browserDownloads.length).toBe(0);

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    downloadGate.release();
    const download = await downloadWait;

    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(exportsFor(ledger, projectId)[0].type).toBe("export");
    expect(exportsFor(ledger, projectId)[0].method).toBe("POST");
    expect(exportsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/tasks`,
    );
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/export/download/${storedName}`,
    );
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(browserDownloads.length).toBe(1);
    expect(download.suggestedFilename().toLowerCase()).toContain("docx");
    expect(await getOpenCalls(page)).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("恶意 contentWarnings：丢弃非法项、截断 20/240、HTML 仅文本零注入", async ({
    page,
    request,
  }) => {
    const name = "E2E V1H2 恶意收敛";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_a1b2c322.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    const longWarning = `${"长".repeat(300)}正文提醒中文与emoji🚀尾`;
    const htmlPayload =
      '<img src=x onerror=alert(1)><a href="https://evil.example">点我</a>';
    const many = Array.from({ length: 25 }, (_, i) => `正文提醒条目${i + 1}`);
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

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
      contentWarnings: mixed,
      imageWarnings: [],
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { storedName } },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    const contentRegion = page.getByRole("region", { name: CONTENT_REGION });
    await expect(contentRegion).toBeVisible({ timeout: 20_000 });
    await expect(contentRegion.getByText(/共\s*20\s*条/)).toBeVisible();
    await expect(
      contentRegion.getByText(htmlPayload, { exact: true }),
    ).toBeVisible();
    // HTML 不得被解释为真实节点；区域内零 img / 零链接
    await expect(contentRegion.locator("img")).toHaveCount(0);
    await expect(contentRegion.locator("a")).toHaveCount(0);
    // 非字符串与空白丢弃后最多 20 条
    await expect(contentRegion.getByRole("listitem")).toHaveCount(20);
    await expect(contentRegion.getByText("正文提醒条目19")).toHaveCount(0);
    // 超长按 Unicode 码点截断至 240
    const longItem = contentRegion.getByRole("listitem").nth(1);
    const longText = (await longItem.innerText()).trim();
    expect(Array.from(longText).length).toBe(240);
    expect(longText.includes("🚀尾")).toBe(false);
    expect(longText.startsWith("长")).toBe(true);

    await downloadWait;
    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(exportsFor(ledger, projectId)[0].type).toBe("export");
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/export/download/${storedName}`,
    );
    expect(browserDownloads.length).toBe(1);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("同项目干净重导出：task POST 启动后终态前清空旧正文提醒", async ({
    page,
    request,
  }) => {
    const name = "E2E V1H2 干净重导出清空";
    const projectId = await seedTechnical(request, name);
    const storedFirst = "export_a1b2c333.docx";
    const storedSecond = "export_a1b2c344.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    // —— 第一次：有正文提醒 ——
    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName: storedFirst,
      contentWarnings: [ANCHOR.contentWarn],
      imageWarnings: [ANCHOR.imageWarn],
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { storedName: storedFirst } },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });

    const download1 = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const contentRegion = page.getByRole("region", { name: CONTENT_REGION });
    await expect(contentRegion).toBeVisible({ timeout: 20_000 });
    await expect(
      contentRegion.getByText(ANCHOR.contentWarn, { exact: true }),
    ).toBeVisible();
    await download1;
    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(browserDownloads.length).toBe(1);

    // —— 第二次：挂起 task 终态；POST 启动后旧提醒须已清空 ——
    await page.unroute("**/api/projects/**/tasks**");
    await page.unroute("**/api/projects/**/export/download/**");

    const secondGate = createHoldGate();
    let secondSeenResolve!: () => void;
    const secondSeen = new Promise<void>((r) => {
      secondSeenResolve = r;
    });

    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName: storedSecond,
      contentWarnings: [],
      imageWarnings: [],
      gate: secondGate,
      onSeen: () => secondSeenResolve(),
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { storedName: storedSecond } },
    });

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 同步点：第二次 export POST 已到达且仍被挂起（禁止固定 sleep）
    await secondSeen;
    expect(exportsFor(ledger, projectId).length).toBe(2);
    expect(exportsFor(ledger, projectId)[1].type).toBe("export");
    expect(secondGate.isReleased()).toBe(false);

    // 终态释放前：旧正文提醒必须已清空
    await expect(page.getByRole("region", { name: CONTENT_REGION })).toHaveCount(
      0,
    );
    await expect(page.getByText(ANCHOR.contentWarn)).toHaveCount(0);
    // 图片旧告警亦应在新导出启动时清空（既有 P9D 语义，作对照不替代正文断言）
    await expect(page.getByRole("region", { name: IMAGE_REGION })).toHaveCount(0);
    expect(downloadsFor(ledger, projectId).length).toBe(1);

    const download2 = page.waitForEvent("download", { timeout: 20_000 });
    secondGate.release();
    await download2;

    // 终态仍空；下载精确各一次（共两次）
    await expect(page.getByRole("region", { name: CONTENT_REGION })).toHaveCount(
      0,
    );
    await expect(page.getByText(ANCHOR.contentWarn)).toHaveCount(0);
    expect(exportsFor(ledger, projectId).length).toBe(2);
    expect(downloadsFor(ledger, projectId).length).toBe(2);
    expect(downloadsFor(ledger, projectId)[0].storedName).toBe(storedFirst);
    expect(downloadsFor(ledger, projectId)[1].storedName).toBe(storedSecond);
    expect(downloadsFor(ledger, projectId)[1].path).toContain(
      `/projects/${projectId}/export/download/${storedSecond}`,
    );
    expect(browserDownloads.length).toBe(2);
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("A→B 迟到 export success：B 零旧正文提醒、零 A 下载；B 后续可成功", async ({
    page,
    request,
  }) => {
    const nameA = "E2E V1H2 迟到隔离A";
    const nameB = "E2E V1H2 迟到隔离B";
    const projectA = await seedTechnical(request, nameA);
    const projectB = await seedTechnical(request, nameB);
    const storedA = "export_a1b2c3aa.docx";
    const storedB = "export_a1b2c3bb.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.url());
    });

    const gateA = createHoldGate();
    let exportASeenResolve!: () => void;
    const exportASeen = new Promise<void>((r) => {
      exportASeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);

    // 仅挂起项目 A 的 export 成功；B 后续独立安装 stub
    await page.route("**/api/projects/**/tasks**", async (route: Route) => {
      const req = route.request();
      if (!isExportTaskPost(req)) {
        await route.continue();
        return;
      }
      const pid = projectIdFromApiUrl(req.url());
      if (pid !== projectA) {
        await route.continue();
        return;
      }
      const rec: ExportRecord = {
        projectId: pid,
        method: "POST",
        path: new URL(req.url()).pathname,
        bodyText: req.postData() || "",
        type: "export",
        seq: ledger.nextSeq(),
      };
      ledger.exports.push(rec);
      exportASeenResolve();
      await gateA.wait();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          id: `task_v1h2_late_a_${ledger.nextSeq()}`,
          type: "export",
          status: "success",
          progress: 100,
          message: "导出完成",
          result: {
            storedName: storedA,
            downloadPath: `/projects/${projectA}/export/download/${storedA}`,
            size: DOCX_BYTES.length,
            mode: "technical",
            filename: `${nameA}.docx`,
            imageWarnings: [],
            contentWarnings: [ANCHOR.contentLateA],
          },
        }),
      });
    });

    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectA]: { storedName: storedA, filename: `${nameA}.docx` },
        [projectB]: { storedName: storedB, filename: `${nameB}.docx` },
      },
    });

    await page.goto(`/technical-plan/${projectA}/export`);
    await expect(page.getByRole("heading", { name: nameA })).toBeVisible({
      timeout: 20_000,
    });

    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 同步点：A export POST 已到达且仍被挂起
    await exportASeen;
    expect(exportsFor(ledger, projectA).length).toBe(1);
    expect(exportsFor(ledger, projectA)[0].type).toBe("export");
    expect(gateA.isReleased()).toBe(false);

    // F2：软导航 B 前注册精确 GET editor-state waitForResponse（禁止固定 sleep）
    const editorStateBWait = page.waitForResponse(
      (res) => {
        if (res.status() !== 200) return false;
        const req = res.request();
        if (req.method().toUpperCase() !== "GET") return false;
        try {
          const pathname = new URL(req.url()).pathname.replace(/\/$/, "");
          return pathname === `/api/projects/${projectB}/editor-state`;
        } catch {
          return false;
        }
      },
      { timeout: 20_000 },
    );

    // SPA 软导航到 B；await B editor-state 后再断言归属并释放 A
    await softNavigateTechnicalPlan(page, projectB, "export");
    await editorStateBWait;

    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${projectB}/export`),
    );
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByRole("heading", { name: nameA })).toHaveCount(0);
    await expect(page.getByText("准备导出 Word")).toBeVisible({
      timeout: 20_000,
    });
    // B 归属已确认且 A 仍挂起：零 A 正文提醒
    expect(gateA.isReleased()).toBe(false);
    await expect(page.getByRole("region", { name: CONTENT_REGION })).toHaveCount(
      0,
    );
    await expect(page.getByText(ANCHOR.contentLateA)).toHaveCount(0);

    const opensBefore = (await getOpenCalls(page)).length;
    const downloadsBefore = browserDownloads.length;
    const downloadsGetBefore = downloadsFor(ledger, projectA).length;

    const lateSuccessResponse = page.waitForResponse(
      async (res) => {
        if (res.status() !== 201) return false;
        const req = res.request();
        if (req.method().toUpperCase() !== "POST") return false;
        let pathname = "";
        try {
          pathname = new URL(req.url()).pathname;
        } catch {
          return false;
        }
        if (!pathname.includes(`/projects/${projectA}/tasks`)) return false;
        if (req.url().includes("/events") || /\/tasks\/[^/?]+$/.test(pathname)) {
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

    // 仅在 B 就绪且 gateA 仍未释放的证据齐备后，才释放迟到 success
    gateA.release();
    await lateSuccessResponse;

    const downloadIncreased = await downloadEventsIncreased(
      page,
      () => browserDownloads.length,
      downloadsBefore,
      5_000,
    );
    expect(
      downloadIncreased,
      "A export success 迟到交付后 browser download 必须精确零新增",
    ).toBe(false);
    expect(browserDownloads.length).toBe(downloadsBefore);
    expect(downloadsFor(ledger, projectA).length).toBe(downloadsGetBefore);

    const openIncreased = await openCallsIncreased(page, opensBefore, 2_000);
    expect(openIncreased, "迟到路径禁止 window.open 回流").toBe(false);
    expect(await getOpenCalls(page)).toEqual([]);

    // 迟到 success 已交付后，B 仍零正文提醒且仍在 B 页
    await expect(page.getByText(ANCHOR.contentLateA)).toHaveCount(0);
    await expect(page.getByRole("region", { name: CONTENT_REGION })).toHaveCount(
      0,
    );
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible();

    // B 后续导出：可显示 B 专属正文提醒并精确一次下载
    await page.unroute("**/api/projects/**/tasks**");
    await installExportSuccessStub(page, ledger, {
      projectId: projectB,
      storedName: storedB,
      contentWarnings: [ANCHOR.contentB],
      imageWarnings: [],
    });

    const downloadB = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    const contentRegionB = page.getByRole("region", { name: CONTENT_REGION });
    await expect(contentRegionB).toBeVisible({ timeout: 20_000 });
    await expect(
      contentRegionB.getByText(ANCHOR.contentB, { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(ANCHOR.contentLateA)).toHaveCount(0);

    await downloadB;
    expect(exportsFor(ledger, projectB).length).toBe(1);
    expect(exportsFor(ledger, projectB)[0].type).toBe("export");
    expect(exportsFor(ledger, projectB)[0].path).toContain(
      `/projects/${projectB}/tasks`,
    );
    expect(downloadsFor(ledger, projectB).length).toBe(1);
    expect(downloadsFor(ledger, projectB)[0].storedName).toBe(storedB);
    expect(downloadsFor(ledger, projectB)[0].path).toContain(
      `/projects/${projectB}/export/download/${storedB}`,
    );
    expect(downloadsFor(ledger, projectA).length).toBe(0);
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
