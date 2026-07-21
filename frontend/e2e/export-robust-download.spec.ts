/**
 * 模块：V1-F 稳健 Word 下载 failure-first E2E
 * 用途：证明技术/商务导出在 window.open 被拦截时仍须触发真实 Playwright download；
 *       覆盖 HTTP/MIME/空体/非法 storedName/双击/下载 GET 挂起 A→B。
 * 对接：Playwright chromium；本机 8010/5174；真实导出页 + 受控 route 桩。
 * 二次开发：禁止 waitForTimeout/setTimeout/sleep、skip/fixme/only、宽松 or、读生产源码、外网。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Download,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";

const API = "http://127.0.0.1:8010/api";
const DOWNLOAD_FAIL_UI = "下载失败，请重试";
const LEAK_DETAIL = "SECRET_V1F_LEAK_DETAIL_DO_NOT_ECHO";
const LEAK_PATH = "C:\\Users\\Administrator\\secret\\exports\\leak.docx";
const MALICIOUS_DOWNLOAD_PATH =
  "/projects/other-project/export/download/export_deadbeef.docx";
const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

/** 最小非空“DOCX”字节（ZIP 魔数 + 填充），仅作 MIME/下载体。 */
const DOCX_BYTES = Buffer.from(
  "PK\u0003\u0004" + "V1F_SYNTH_DOCX_PAYLOAD_" + "x".repeat(200),
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

/** 用途：固定 window.open 为 blocked/null，并记录调用。 */
async function installOpenStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as Window & { __v1fOpenCalls?: string[] };
    w.__v1fOpenCalls = [];
    window.open = (url?: string | URL) => {
      w.__v1fOpenCalls = w.__v1fOpenCalls || [];
      w.__v1fOpenCalls.push(String(url ?? ""));
      return null;
    };
  });
}

async function getOpenCalls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as Window & { __v1fOpenCalls?: string[] };
    return [...(w.__v1fOpenCalls || [])];
  });
}

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

async function seedTechnical(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const projectId = await createProject(request, "technical", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      outline: [{ id: "node_v1f", title: "V1F章节", children: [] }],
      chapters: [
        {
          id: "chap_v1f",
          title: "V1F章节",
          body: "V1F_TECH_BODY\n",
          preview: "v1f",
          wordCount: 12,
          status: "done",
        },
      ],
      mode: "ALIGNED",
    },
  });
  expect(put.ok()).toBeTruthy();
  return projectId;
}

async function seedBusiness(
  request: APIRequestContext,
  name: string,
): Promise<string> {
  const projectId = await createProject(request, "business", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      parsedMarkdown: "V1F 商务条款\n",
      businessQualify: [
        {
          id: "q_v1f",
          requirement: "独立法人资格",
          response: "具备",
          evidence: "",
          status: "matched",
        },
      ],
      businessCommit: [
        {
          id: "c_v1f",
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

async function softNavigate(page: Page, url: string): Promise<void> {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

function downloadsFor(ledger: Ledger, projectId: string): DownloadGetRecord[] {
  return ledger.downloads.filter((d) => d.projectId === projectId);
}

function exportsFor(ledger: Ledger, projectId: string): ExportRecord[] {
  return ledger.exports.filter((e) => e.projectId === projectId);
}

/**
 * 用途：受控 export 成功结果；可注入非法/缺失 storedName、filename 与恶意 downloadPath。
 * filename 语义：omitFilename=true 不写字段；filename=null 写 null；未传默认「合成标书.docx」。
 */
async function installExportSuccessStub(
  page: Page,
  ledger: Ledger,
  opts: {
    projectId: string;
    storedName?: string | null;
    omitStoredName?: boolean;
    filename?: string | null;
    omitFilename?: boolean;
    downloadPath?: string;
    imageWarnings?: unknown[];
    mode?: "technical" | "business";
    gate?: HoldGate;
    onSeen?: (rec: ExportRecord) => void;
  },
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
      seq: ledger.nextSeq(),
    };
    ledger.exports.push(rec);
    opts.onSeen?.(rec);
    if (opts.gate) {
      await opts.gate.wait();
    }
    const stored =
      opts.omitStoredName
        ? undefined
        : opts.storedName === null
          ? null
          : (opts.storedName ?? `export_${"a".repeat(8)}.docx`);
    const result: Record<string, unknown> = {
      size: DOCX_BYTES.length,
      mode: opts.mode ?? "technical",
      imageWarnings: opts.imageWarnings ?? [],
    };
    if (!opts.omitFilename) {
      result.filename =
        opts.filename === undefined ? "合成标书.docx" : opts.filename;
    }
    if (stored !== undefined) {
      result.storedName = stored;
    }
    if (opts.downloadPath !== undefined) {
      result.downloadPath = opts.downloadPath;
    } else if (typeof stored === "string") {
      result.downloadPath = `/projects/${pid}/export/download/${stored}`;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({
        id: `task_v1f_${ledger.nextSeq()}`,
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
 * dispositionMode：
 * - ok（默认）：标准 attachment + filename/filename*
 * - omit：无 Content-Disposition（业务回退 task filename）
 * - unparseable：不可安全解析的畸形头
 */
type DownloadFulfill =
  | {
      kind: "docx";
      filename?: string;
      dispositionMode?: "ok" | "omit" | "unparseable";
    }
  | { kind: "status"; status: number; detail?: string }
  | { kind: "mime"; contentType: string; body?: Buffer }
  | { kind: "empty" }
  | { kind: "abort" }
  | { kind: "hold"; gate: HoldGate; then: Exclude<DownloadFulfill, { kind: "hold" }> };

/**
 * 用途：按 project/method/path 精确归属记录下载 GET，并可挂起/失败/返回合成 DOCX。
 */
async function installDownloadGetStub(
  page: Page,
  ledger: Ledger,
  opts: {
    /** projectId → 履行策略；未列出的 download GET 记入 otherDownloadPaths 并 abort */
    byProject: Record<string, DownloadFulfill>;
    allowedStoredNames?: Record<string, string>;
    onSeen?: (rec: DownloadGetRecord) => void;
  },
): Promise<void> {
  await page.route("**/api/projects/**/export/download/**", async (route: Route) => {
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
    const allowed = opts.allowedStoredNames?.[pid];
    if (allowed && stored !== allowed) {
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

    let fulfill = opts.byProject[pid];
    if (fulfill.kind === "hold") {
      await fulfill.gate.wait();
      fulfill = fulfill.then;
    }

    if (fulfill.kind === "docx") {
      const filename = fulfill.filename ?? "合成标书.docx";
      const encoded = encodeURIComponent(filename);
      const mode = fulfill.dispositionMode ?? "ok";
      const headers: Record<string, string> = {
        "Cache-Control": "no-store",
      };
      if (mode === "ok") {
        headers["Content-Disposition"] =
          `attachment; filename="synth.docx"; filename*=UTF-8''${encoded}`;
      } else if (mode === "unparseable") {
        // 故意畸形：不可安全解析，前端应回退 task filename / 标书.docx
        headers["Content-Disposition"] =
          'attachment; filename="???broken;;; filename*=BAD';
      }
      // mode === "omit"：不写 Content-Disposition
      await route.fulfill({
        status: 200,
        contentType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers,
        body: DOCX_BYTES,
      });
      return;
    }
    if (fulfill.kind === "status") {
      await route.fulfill({
        status: fulfill.status,
        contentType: "application/json",
        body: JSON.stringify({
          detail: fulfill.detail ?? LEAK_DETAIL,
          path: LEAK_PATH,
        }),
      });
      return;
    }
    if (fulfill.kind === "mime") {
      await route.fulfill({
        status: 200,
        contentType: fulfill.contentType,
        body: fulfill.body ?? Buffer.from("not-a-docx"),
      });
      return;
    }
    if (fulfill.kind === "empty") {
      await route.fulfill({
        status: 200,
        contentType:
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers: {
          "Content-Disposition": 'attachment; filename="empty.docx"',
        },
        body: Buffer.alloc(0),
      });
      return;
    }
    if (fulfill.kind === "abort") {
      await route.abort("failed");
      return;
    }
    await route.abort("failed");
  });
}

/**
 * 用途：等待 download 事件；超时返回 null（事件驱动，禁止 sleep）。
 */
async function waitDownloadOrNull(
  page: Page,
  timeoutMs: number,
): Promise<Download | null> {
  try {
    return await page.waitForEvent("download", { timeout: timeoutMs });
  } catch {
    return null;
  }
}

/**
 * 用途：观察 browser download 事件是否新增；超时无新增返回 false。
 * 对接：仅依赖 page.waitForEvent("download")，禁止固定 sleep。
 */
async function downloadCountIncreased(
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

async function assertNoLeakInDom(page: Page) {
  const text = await page.locator("body").innerText();
  expect(text).not.toContain(LEAK_DETAIL);
  expect(text).not.toContain(LEAK_PATH);
  expect(text).not.toContain("SECRET_V1F");
  expect(text).not.toMatch(/export_[0-9a-f]{8}\.docx/i);
}

test.describe("V1-F 稳健 Word 下载", () => {
  test.afterEach(() => {
    for (const gate of [...activeHoldGates]) {
      gate.release();
    }
    activeHoldGates.clear();
  });

  test("技术标：window.open 被拦截时仍须一次真实 download 且 GET 精确一次", async ({
    page,
    request,
  }) => {
    // 响应头人读名 vs 任务 filename 刻意不同，证明优先取 Content-Disposition
    const headerProjectName = "E2E V1F 技术标稳健下载";
    const headerFilename = `${headerProjectName}.docx`;
    const taskFilename = "TASK_ONLY_NOT_HEADER_FILENAME.docx";
    const projectId = await seedTechnical(request, headerProjectName);
    const storedName = "export_aabbccdd.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
      mode: "technical",
      // 与响应头刻意不同；不得被选为 suggestedFilename
      filename: taskFilename,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: {
          kind: "docx",
          filename: headerFilename,
          dispositionMode: "ok",
        },
      },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(
      page.getByRole("heading", { name: headerProjectName }),
    ).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const download = await downloadWait;

    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].method).toBe("GET");
    expect(downloadsFor(ledger, projectId)[0].storedName).toBe(storedName);
    expect(downloadsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/export/download/${storedName}`,
    );
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(browserDownloads.length).toBe(1);
    // 精确：优先响应头项目名；不得回退 task filename / 标书.docx
    expect(download.suggestedFilename()).toBe(headerFilename);
    expect(download.suggestedFilename()).not.toBe(taskFilename);
    expect(download.suggestedFilename()).not.toBe("标书.docx");
    expect(browserDownloads[0]).toBe(headerFilename);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("商务标：统一 downloadExport，不消费恶意 downloadPath", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 商务标稳健下载";
    const safeTaskFilename = `${name}.docx`;
    const projectId = await seedBusiness(request, name);
    const storedName = "export_11223344.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
      mode: "business",
      // 响应头不可解析时必须精确回退该安全 task filename
      filename: safeTaskFilename,
      // 恶意路径：生产若仍 direct window.open(downloadPath) 会点到它
      downloadPath: MALICIOUS_DOWNLOAD_PATH,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: {
          kind: "docx",
          // 业务成功：头缺失或不可安全解析 → 回退 task filename
          dispositionMode: "omit",
        },
      },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/business-bid/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible();

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const download = await downloadWait;

    // 必须按当前 project + storedName 构造 GET，不得访问恶意 path
    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].storedName).toBe(storedName);
    expect(downloadsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/export/download/${storedName}`,
    );
    expect(ledger.otherDownloadPaths).toEqual([]);
    const opens = await getOpenCalls(page);
    expect(opens).toEqual([]);
    expect(
      opens.some((u) => u.includes("other-project") || u.includes("deadbeef")),
    ).toBe(false);
    expect(browserDownloads.length).toBe(1);
    // 精确：头缺失 → 安全 task filename；不得为 标书.docx / storedName
    expect(download.suggestedFilename()).toBe(safeTaskFilename);
    expect(download.suggestedFilename()).not.toBe("标书.docx");
    expect(download.suggestedFilename()).not.toBe(storedName);
    expect(browserDownloads[0]).toBe(safeTaskFilename);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("技术标：响应头与 task filename 均缺失/非法时精确回退 标书.docx", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 双缺失回退标书";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_deadf00d.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
      mode: "technical",
      // 非法/危险 task filename，不可用作保存名
      filename: "../evil|name?.docx",
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: {
          kind: "docx",
          dispositionMode: "unparseable",
        },
      },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const download = await downloadWait;

    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId)[0].method).toBe("GET");
    expect(downloadsFor(ledger, projectId)[0].storedName).toBe(storedName);
    expect(downloadsFor(ledger, projectId)[0].path).toContain(
      `/projects/${projectId}/export/download/${storedName}`,
    );
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(browserDownloads.length).toBe(1);
    // 精确固定回退
    expect(download.suggestedFilename()).toBe("标书.docx");
    expect(browserDownloads[0]).toBe("标书.docx");
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  for (const status of [401, 403, 404, 500] as const) {
    test(`技术标：下载 HTTP ${status} → 零 download 事件与固定脱敏错误`, async ({
      page,
      request,
    }) => {
      const name = `E2E V1F 下载HTTP${status}`;
      const projectId = await seedTechnical(request, name);
      const storedName = "export_ff01ff01.docx";
      const ledger = createLedger();
      const browserDownloads: Download[] = [];
      page.on("download", (d) => {
        browserDownloads.push(d);
      });

      await installOpenStub(page);
      await installNetworkGuard(page, ledger);
      await installExportSuccessStub(page, ledger, {
        projectId,
        storedName,
        mode: "technical",
      });
      await installDownloadGetStub(page, ledger, {
        byProject: {
          [projectId]: {
            kind: "status",
            status,
            detail: LEAK_DETAIL,
          },
        },
        allowedStoredNames: { [projectId]: storedName },
      });

      await page.goto(`/technical-plan/${projectId}/export`);
      await expect(page.getByRole("heading", { name })).toBeVisible({
        timeout: 20_000,
      });
      await page.getByRole("button", { name: /生成并下载 Word/ }).click();

      await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
        timeout: 20_000,
      });
      const gotDownload = await waitDownloadOrNull(page, 2_000);
      expect(gotDownload, "HTTP 失败不得触发浏览器 download 事件").toBeNull();
      expect(browserDownloads.length).toBe(0);
      expect(downloadsFor(ledger, projectId).length).toBe(1);
      expect(await getOpenCalls(page)).toEqual([]);
      await assertNoLeakInDom(page);
      await assertNoSensitiveStorage(page);
      expect(ledger.externalHits).toEqual([]);
    });
  }

  test("技术标：错误 MIME → 零 download 与固定错误", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 错误MIME";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_ee02ee02.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: {
          kind: "mime",
          contentType: "application/json",
          body: Buffer.from(JSON.stringify({ detail: LEAK_DETAIL })),
        },
      },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
      timeout: 20_000,
    });
    expect(browserDownloads.length).toBe(0);
    expect(await waitDownloadOrNull(page, 1_500)).toBeNull();
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(await getOpenCalls(page)).toEqual([]);
    await assertNoLeakInDom(page);
  });

  test("技术标：空 Blob → 零 download 与固定错误", async ({ page, request }) => {
    const name = "E2E V1F 空体下载";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_cc03cc03.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { kind: "empty" } },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
      timeout: 20_000,
    });
    expect(browserDownloads.length).toBe(0);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(await getOpenCalls(page)).toEqual([]);
  });

  test("技术标：网络失败 abort → 零 download 与固定错误", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 网络失败";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_dd04dd04.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: { [projectId]: { kind: "abort" } },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
      timeout: 20_000,
    });
    expect(browserDownloads.length).toBe(0);
    expect(await getOpenCalls(page)).toEqual([]);
  });

  test("技术标：缺失 storedName → 零下载 GET、零 anchor、固定错误", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 缺失storedName";
    const projectId = await seedTechnical(request, name);
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      omitStoredName: true,
      downloadPath: MALICIOUS_DOWNLOAD_PATH,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: { kind: "docx" },
      },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
      timeout: 20_000,
    });
    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(0);
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(browserDownloads.length).toBe(0);
    expect(await getOpenCalls(page)).toEqual([]);
    await assertNoLeakInDom(page);
  });

  test("技术标：非法 storedName → 零下载 GET", async ({ page, request }) => {
    const name = "E2E V1F 非法storedName";
    const projectId = await seedTechnical(request, name);
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName: "../secrets/not-export.docx",
      downloadPath: "/projects/x/export/download/../secrets/not-export.docx",
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: { kind: "docx" },
      },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect(page.getByText(DOWNLOAD_FAIL_UI)).toBeVisible({
      timeout: 20_000,
    });
    expect(downloadsFor(ledger, projectId).length).toBe(0);
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(browserDownloads.length).toBe(0);
    expect(await getOpenCalls(page)).toEqual([]);
  });

  test("技术标：快速双击同一导出仅一次下载 GET 与一次 download 事件", async ({
    page,
    request,
  }) => {
    const name = "E2E V1F 快速双击";
    const projectId = await seedTechnical(request, name);
    const storedName = "export_bb05bb05.docx";
    const ledger = createLedger();
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.suggestedFilename());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installExportSuccessStub(page, ledger, {
      projectId,
      storedName,
    });
    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectId]: { kind: "docx", filename: `${name}.docx` },
      },
      allowedStoredNames: { [projectId]: storedName },
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });

    const downloadWait = page.waitForEvent("download", { timeout: 20_000 });
    const btn = page.getByRole("button", { name: /生成并下载 Word/ });
    await btn.evaluate((el) => {
      const button = el as HTMLButtonElement;
      button.click();
      button.click();
    });
    await downloadWait;

    expect(exportsFor(ledger, projectId).length).toBe(1);
    expect(downloadsFor(ledger, projectId).length).toBe(1);
    expect(browserDownloads.length).toBe(1);
    expect(await getOpenCalls(page)).toEqual([]);
  });

  test("下载 GET 挂起时 A→B：释放 A 后 B 零下载；B 后续可成功", async ({
    page,
    request,
  }) => {
    const nameA = "E2E V1F 下载挂起A";
    const nameB = "E2E V1F 下载挂起B";
    const projectA = await seedTechnical(request, nameA);
    const projectB = await seedTechnical(request, nameB);
    const storedA = "export_aa06aa06.docx";
    const storedB = "export_bb06bb06.docx";
    const ledger = createLedger();
    const gateA = createHoldGate();
    let downloadASeenResolve!: () => void;
    const downloadASeen = new Promise<void>((r) => {
      downloadASeenResolve = r;
    });
    const browserDownloads: string[] = [];
    page.on("download", (d) => {
      browserDownloads.push(d.url());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);

    // 两个项目各自 export stub（按 project 精确归属）
    await page.route("**/api/projects/**/tasks**", async (route: Route) => {
      const req = route.request();
      if (!isExportTaskPost(req)) {
        await route.continue();
        return;
      }
      const pid = projectIdFromApiUrl(req.url());
      if (pid !== projectA && pid !== projectB) {
        await route.continue();
        return;
      }
      const stored = pid === projectA ? storedA : storedB;
      const rec: ExportRecord = {
        projectId: pid!,
        method: "POST",
        path: new URL(req.url()).pathname,
        bodyText: req.postData() || "",
        seq: ledger.nextSeq(),
      };
      ledger.exports.push(rec);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          id: `task_v1f_ab_${ledger.nextSeq()}`,
          type: "export",
          status: "success",
          progress: 100,
          message: "导出完成",
          result: {
            storedName: stored,
            downloadPath: `/projects/${pid}/export/download/${stored}`,
            size: DOCX_BYTES.length,
            mode: "technical",
            filename: pid === projectA ? `${nameA}.docx` : `${nameB}.docx`,
            imageWarnings: [],
          },
        }),
      });
    });

    await installDownloadGetStub(page, ledger, {
      byProject: {
        [projectA]: {
          kind: "hold",
          gate: gateA,
          then: { kind: "docx", filename: `${nameA}.docx` },
        },
        [projectB]: { kind: "docx", filename: `${nameB}.docx` },
      },
      allowedStoredNames: {
        [projectA]: storedA,
        [projectB]: storedB,
      },
      onSeen: (rec) => {
        if (rec.projectId === projectA) downloadASeenResolve();
      },
    });

    await page.goto(`/technical-plan/${projectA}/export`);
    await expect(page.getByRole("heading", { name: nameA })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 同步点：export 成功后必须发起同源下载 GET；当前 window.open 生产路径会在此真红
    await expect
      .poll(() => exportsFor(ledger, projectA).length, { timeout: 20_000 })
      .toBe(1);
    const downloadGetArrived = await Promise.race([
      downloadASeen.then(() => true),
      page
        .waitForEvent("download", { timeout: 8_000 })
        .then(() => false)
        .catch(() => false),
    ]);
    expect(
      downloadGetArrived,
      "A 导出成功后必须出现一次可挂起的下载 GET（Blob 协议）；window.open 不算",
    ).toBe(true);
    expect(downloadsFor(ledger, projectA).length).toBe(1);
    expect(browserDownloads.length).toBe(0);

    // 软切 B
    await softNavigate(page, `/technical-plan/${projectB}/export`);
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();

    const downloadsBeforeRelease = browserDownloads.length;
    const opensBefore = (await getOpenCalls(page)).length;
    const bGetsBefore = downloadsFor(ledger, projectB).length;

    // 证明 A 的 200 已交付后仍零 download
    const aFulfilled = page.waitForResponse(
      (res) => {
        if (res.status() !== 200) return false;
        const req = res.request();
        if (!isDownloadGet(req)) return false;
        return projectIdFromApiUrl(req.url()) === projectA;
      },
      { timeout: 15_000 },
    );
    gateA.release();
    await aFulfilled;

    const increased = await downloadCountIncreased(
      page,
      () => browserDownloads.length,
      downloadsBeforeRelease,
      5_000,
    );
    expect(
      increased,
      "A 下载 GET 200 交付后，已切到 B 必须零 browser download 事件",
    ).toBe(false);
    expect(browserDownloads.length).toBe(downloadsBeforeRelease);
    expect(await getOpenCalls(page)).toHaveLength(opensBefore);
    expect(downloadsFor(ledger, projectB).length).toBe(bGetsBefore);
    // B 页不得出现 A 成功/失败提示污染
    await expect(page.getByText("Word 已生成，正在下载")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible();

    // B 后续导出可成功（反假绿）
    const bDownloadWait = page.waitForEvent("download", { timeout: 20_000 });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await bDownloadWait;
    expect(exportsFor(ledger, projectB).length).toBe(1);
    expect(downloadsFor(ledger, projectB).length).toBe(1);
    expect(downloadsFor(ledger, projectB)[0].storedName).toBe(storedB);
    expect(browserDownloads.length).toBe(downloadsBeforeRelease + 1);
    expect(ledger.otherDownloadPaths).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
