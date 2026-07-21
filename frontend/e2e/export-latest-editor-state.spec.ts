/**
 * 模块：V1-E 导出前最新编辑态落盘 E2E（failure-first）
 * 用途：证明技术/商务导出点击必须先完成最新 editor-state PUT；失败/冲突/无改动/双击/切项目可判定。
 * 对接：Playwright chromium；本机后端 8010 / 前端 5174；真实页面编辑控件 + route 屏障。
 * 二次开发：禁止 waitForTimeout/setTimeout/sleep、skip/fixme、宽松 or、读生产源码、外网与真实业务数据。
 */
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
  type Request,
  type Route,
} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const API = "http://127.0.0.1:8010/api";

/** 集中 ASCII 锚点：唯一、可判定、禁止回显到安全 UI */
const ANCHOR = {
  techOld: "V1E_TECH_OLD_ANCHOR_7f3a",
  techNew: "V1E_TECH_NEW_ANCHOR_9c2b",
  techInflight: "V1E_TECH_INFLIGHT_ANCHOR_4d1e",
  techNoEdit: "V1E_TECH_NOEDIT_ANCHOR_1a8f",
  techConflict: "V1E_TECH_CONFLICT_ANCHOR_6b0c",
  techFail: "V1E_TECH_FAIL_ANCHOR_2e5d",
  techInvalid: "V1E_TECH_INVALID_ANCHOR_8f4a",
  techDouble: "V1E_TECH_DOUBLE_ANCHOR_3c7e",
  techLateA: "V1E_TECH_LATE_A_ANCHOR_5d9b",
  techLateB: "V1E_TECH_LATE_B_ANCHOR_0e2a",
  techA2Warn: "V1E_TECH_A2_LATE_WARN_7e1c",
  techA3S1: "V1E_TECH_A3_S1_ANCHOR_2b6d",
  techA3S2: "V1E_TECH_A3_S2_ANCHOR_8a4f",
  bizOld: "V1E_BIZ_OLD_ANCHOR_a1b2",
  bizNew: "V1E_BIZ_NEW_ANCHOR_c3d4",
  bizInflight: "V1E_BIZ_INFLIGHT_ANCHOR_e5f6",
  bizNoEdit: "V1E_BIZ_NOEDIT_ANCHOR_7788",
  bizConflict: "V1E_BIZ_CONFLICT_ANCHOR_9900",
  bizFail: "V1E_BIZ_FAIL_ANCHOR_1122",
  bizA2Warn: "V1E_BIZ_A2_LATE_WARN_3c9e",
  bizA2LateA: "V1E_BIZ_A2_LATE_A_ANCHOR_55aa",
  bizA2LateB: "V1E_BIZ_A2_LATE_B_ANCHOR_66bb",
  bizA3S1: "V1E_BIZ_A3_S1_ANCHOR_d4e5",
  bizA3S2: "V1E_BIZ_A3_S2_ANCHOR_f6a7",
  leakDetail: "SECRET_V1E_LEAK_DETAIL_DO_NOT_ECHO",
  leakVersion: "esv_deadbeefdeadbeefdeadbeefdeadbeef",
} as const;

const TECH_SAVE_ERROR = "技术标工作区保存失败，请稍后重试";
const TECH_FULL_CONFLICT =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";
const BIZ_SAVE_ERROR = "商务标工作区保存失败，请稍后重试";
const BIZ_FULL_CONFLICT =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
};

type PutRecord = {
  projectId: string;
  method: string;
  path: string;
  bodyText: string;
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

type RequestLedger = {
  puts: PutRecord[];
  exports: ExportRecord[];
  externalHits: string[];
  nextSeq: () => number;
};

/** 模块级 gate 注册表：失败路径由 afterEach 统一幂等释放，禁止泄漏挂起请求 */
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

function createLedger(): RequestLedger {
  let seq = 0;
  return {
    puts: [],
    exports: [],
    externalHits: [],
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

function isEditorStatePut(req: Request): boolean {
  if (req.method().toUpperCase() !== "PUT") return false;
  try {
    const u = new URL(req.url());
    return /\/api\/projects\/[^/]+\/editor-state\/?$/.test(u.pathname);
  } catch {
    return false;
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

/** 用途：安装 window.open 桩，仅记录本机 URL，禁止真实弹窗。 */
async function installOpenStub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as Window & { __v1eOpenCalls?: string[] };
    w.__v1eOpenCalls = [];
    window.open = (url?: string | URL) => {
      w.__v1eOpenCalls = w.__v1eOpenCalls || [];
      w.__v1eOpenCalls.push(String(url ?? ""));
      return null;
    };
  });
}

async function getOpenCalls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const w = window as Window & { __v1eOpenCalls?: string[] };
    return [...(w.__v1eOpenCalls || [])];
  });
}

/** 用途：外网阻断 + API 旁路记录（业务请求默认 continue）。 */
async function installNetworkGuard(
  page: Page,
  ledger: RequestLedger,
): Promise<void> {
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
  bodyAnchor: string,
): Promise<string> {
  const projectId = await createProject(request, "technical", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      outline: [{ id: "node_v1e", title: "V1E章节", children: [] }],
      chapters: [
        {
          id: "chap_v1e",
          title: "V1E章节",
          body: `${bodyAnchor}\n`,
          preview: "v1e",
          wordCount: bodyAnchor.length,
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
  responseAnchor: string,
): Promise<string> {
  const projectId = await createProject(request, "business", name);
  const put = await request.put(`${API}/projects/${projectId}/editor-state`, {
    data: {
      parsedMarkdown: "V1E 商务条款摘要\n",
      businessQualify: [
        {
          id: "q_v1e",
          requirement: "独立法人资格",
          response: responseAnchor,
          evidence: "",
          status: "matched",
        },
      ],
      businessToc: [
        {
          id: "t_v1e",
          title: "营业执照",
          category: "资格证明",
          checked: true,
        },
      ],
      businessQuote: {
        rows: [
          {
            id: "qr_v1e",
            name: "实施服务",
            unit: "项",
            quantity: "1",
            unitPrice: "10000",
            amount: "10000",
            remark: "",
          },
        ],
        notes: "V1E_QUOTE_NOTES_SEED",
      },
      businessCommit: [
        {
          id: "c_v1e",
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

/**
 * 用途：对指定项目的 editor-state PUT 安装屏障/失败模式。
 * 同步点：请求到达时 mark，再 await gate；禁止固定 sleep。
 * 扩展：gateForIndex 按第 N 次 PUT 选择持有 gate（A3 双阶段 S1/S2）。
 */
async function installEditorStatePutBarrier(
  page: Page,
  ledger: RequestLedger,
  opts: {
    projectId: string;
    gate?: HoldGate;
    /** 按本项目第几次 PUT（0-based）选择 gate；优先于 gate */
    gateForIndex?: (index: number, rec: PutRecord) => HoldGate | undefined;
    onSeen?: (rec: PutRecord) => void;
    mode?:
      | { kind: "continue" }
      | { kind: "full_conflict" }
      | { kind: "http_fail"; status: number; detail: string }
      | { kind: "invalid_success_version" };
  },
): Promise<void> {
  const mode = opts.mode ?? { kind: "continue" as const };
  await page.route("**/api/projects/**/editor-state**", async (route: Route) => {
    const req = route.request();
    if (!isEditorStatePut(req)) {
      await route.continue();
      return;
    }
    const pid = projectIdFromApiUrl(req.url());
    if (pid !== opts.projectId) {
      await route.continue();
      return;
    }
    const bodyText = req.postData() || "";
    const rec: PutRecord = {
      projectId: pid,
      method: "PUT",
      path: new URL(req.url()).pathname,
      bodyText,
      seq: ledger.nextSeq(),
    };
    ledger.puts.push(rec);
    const putIndex = putsFor(ledger, pid).length - 1;
    opts.onSeen?.(rec);
    const indexGate = opts.gateForIndex?.(putIndex, rec);
    const hold = indexGate ?? opts.gate;
    if (hold) {
      await hold.wait();
    }

    if (mode.kind === "full_conflict") {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "editor_state_version_conflict",
            message: ANCHOR.leakDetail,
            currentStateVersion: ANCHOR.leakVersion,
          },
        }),
      });
      return;
    }
    if (mode.kind === "http_fail") {
      await route.fulfill({
        status: mode.status,
        contentType: "application/json",
        body: JSON.stringify({ detail: mode.detail }),
      });
      return;
    }
    if (mode.kind === "invalid_success_version") {
      const res = await route.fetch();
      const raw = await res.text();
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        parsed = {};
      }
      parsed.stateVersion = "not-a-valid-esv";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(parsed),
      });
      return;
    }
    await route.continue();
  });
}

/** 用途：记录/可选挂起/桩成功 export 任务 POST；按 project 归属计数。 */
async function installExportTaskBarrier(
  page: Page,
  ledger: RequestLedger,
  opts?: {
    projectId?: string;
    gate?: HoldGate;
    /** 仅对该 projectId 持有 gate；其它项目仍记录但不挂起（A2 跨项目） */
    holdOnlyProjectId?: string;
    onSeen?: (rec: ExportRecord) => void;
    stubSuccess?: boolean;
    /** stubSuccess 时 result.imageWarnings；用于 A2 迟到污染判定 */
    imageWarnings?: unknown[];
    /** stubSuccess 时 result.mode */
    resultMode?: "technical" | "business";
  },
): Promise<void> {
  await page.route("**/api/projects/**/tasks**", async (route: Route) => {
    const req = route.request();
    if (!isExportTaskPost(req)) {
      await route.continue();
      return;
    }
    const pid = projectIdFromApiUrl(req.url());
    if (!pid) {
      await route.continue();
      return;
    }
    if (opts?.projectId && pid !== opts.projectId) {
      await route.continue();
      return;
    }
    const bodyText = req.postData() || "";
    const rec: ExportRecord = {
      projectId: pid,
      method: "POST",
      path: new URL(req.url()).pathname,
      bodyText,
      type: "export",
      seq: ledger.nextSeq(),
    };
    ledger.exports.push(rec);
    opts?.onSeen?.(rec);
    const shouldHold =
      !!opts?.gate &&
      (!opts.holdOnlyProjectId || pid === opts.holdOnlyProjectId);
    if (shouldHold && opts?.gate) {
      await opts.gate.wait();
    }
    if (opts?.stubSuccess) {
      const storedName = `v1e-stub-${pid}.docx`;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          id: `task_v1e_${ledger.nextSeq()}`,
          type: "export",
          status: "success",
          progress: 100,
          message: "导出完成",
          result: {
            storedName,
            downloadPath: `/projects/${pid}/export/download/${storedName}`,
            size: 1024,
            mode: opts.resultMode ?? "technical",
            imageWarnings: opts.imageWarnings ?? [],
          },
        }),
      });
      return;
    }
    await route.continue();
  });
}

function putsFor(ledger: RequestLedger, projectId: string): PutRecord[] {
  return ledger.puts.filter((p) => p.projectId === projectId);
}

function exportsFor(ledger: RequestLedger, projectId: string): ExportRecord[] {
  return ledger.exports.filter((e) => e.projectId === projectId);
}

/** 用途：是否为本项目 export 任务 POST。 */
function isProjectExportRequest(req: Request, projectId: string): boolean {
  return (
    isExportTaskPost(req) && projectIdFromApiUrl(req.url()) === projectId
  );
}

/**
 * 用途：等待 PUT 先于 export；若 export 抢先则返回 export_first 供精确失败。
 * 对接：pending 编辑导出门；事件驱动，禁止 waitForTimeout/sleep。
 */
async function waitPutBeforeExport(
  page: Page,
  projectId: string,
  putSeen: Promise<void>,
  timeoutMs = 12_000,
): Promise<"put_first" | "export_first" | "timeout"> {
  const exportFirst = page
    .waitForRequest((req) => isProjectExportRequest(req, projectId), {
      timeout: timeoutMs,
    })
    .then(() => "export_first" as const)
    .catch(() => "timeout" as const);
  const putFirst = putSeen.then(() => "put_first" as const);
  return Promise.race([putFirst, exportFirst]);
}

/**
 * 用途：在 PUT 持有窗口内观察是否出现 export；超时仍无则 false。
 * 对接：在途 autosave / 持有 PUT 后点导出。
 */
async function exportAppearedDuringHold(
  page: Page,
  projectId: string,
  timeoutMs = 4_000,
): Promise<boolean> {
  try {
    await page.waitForRequest(
      (req) => isProjectExportRequest(req, projectId),
      { timeout: timeoutMs },
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * 用途：A3 释放 S1 后竞速 S2 PUT vs export；export 抢先必须可读首红。
 * 对接：route onSeen / waitForRequest；禁止固定 sleep。
 */
async function waitS2PutBeforeExport(
  page: Page,
  projectId: string,
  s2Seen: Promise<void>,
  timeoutMs = 12_000,
): Promise<"s2_first" | "export_first" | "timeout"> {
  const exportFirst = page
    .waitForRequest((req) => isProjectExportRequest(req, projectId), {
      timeout: timeoutMs,
    })
    .then(() => "export_first" as const)
    .catch(() => "timeout" as const);
  const s2First = s2Seen.then(() => "s2_first" as const);
  return Promise.race([s2First, exportFirst]);
}

/**
 * 用途：释放 export 成功响应后观察 window.open 是否新增；超时无新增返回 false。
 * 对接：A2 迟到下载隔离；事件驱动 waitForFunction，禁止 sleep。
 */
async function openCallsIncreased(
  page: Page,
  baseline: number,
  timeoutMs = 5_000,
): Promise<boolean> {
  try {
    await page.waitForFunction(
      (n) => {
        const w = window as Window & { __v1eOpenCalls?: string[] };
        return (w.__v1eOpenCalls || []).length > n;
      },
      baseline,
      { timeout: timeoutMs },
    );
    return true;
  } catch {
    return false;
  }
}

async function softNavigate(
  page: Page,
  url: string,
): Promise<void> {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function openTechnicalContent(page: Page, projectId: string, name: string) {
  await page.goto(`/technical-plan/${projectId}/content`);
  await expect(page.getByRole("heading", { name })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible();
  await expect(page.getByLabel("正文：V1E章节")).toBeVisible();
}

async function openBusinessQualify(page: Page, projectId: string, name: string) {
  await page.goto(`/business-bid/${projectId}/qualify`);
  await expect(page.getByRole("heading", { name })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible();
  await expect(page.getByText("独立法人资格")).toBeVisible();
}

async function goTechnicalExportViaLink(page: Page) {
  await page.getByRole("link", { name: "下一步：导出" }).click();
  await expect(page.getByText("准备导出 Word")).toBeVisible({ timeout: 15_000 });
}

async function goBusinessExportViaStepper(page: Page, projectId: string) {
  await page
    .locator(`a.bb-step[href="/business-bid/${projectId}/export"]`)
    .click();
  await expect(page.getByText("准备导出商务标 Word")).toBeVisible({
    timeout: 15_000,
  });
}

function assertSafeUiText(text: string) {
  expect(text).not.toContain(ANCHOR.leakDetail);
  expect(text).not.toContain(ANCHOR.leakVersion);
  expect(text).not.toContain("SECRET_V1E");
}

test.describe("V1-E 导出前最新编辑态落盘", () => {
  // 失败/超时路径统一幂等释放挂起 gate，避免跨用例泄漏；正常 release 已从 Set 移除
  test.afterEach(() => {
    for (const gate of [...activeHoldGates]) {
      gate.release();
    }
    activeHoldGates.clear();
  });

  test("技术标 pending 编辑：PUT 含 NEW 且释放前 export=0", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标 pending";
    const projectId = await seedTechnical(request, name, ANCHOR.techOld);
    const ledger = createLedger();
    const putGate = createHoldGate();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gate: putGate,
      onSeen: () => putSeenResolve(),
      mode: { kind: "continue" },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    const bodyBox = page.getByLabel("正文：V1E章节");
    await expect(bodyBox).toHaveValue(new RegExp(ANCHOR.techOld));
    await bodyBox.fill(`${ANCHOR.techNew}\n`);
    await expect(bodyBox).toHaveValue(new RegExp(ANCHOR.techNew));

    // 立即进入导出并点击：pending timer 尚未自然触发时，导出门必须先落盘
    await goTechnicalExportViaLink(page);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;

    // 释放合法 200 前 export 必须为 0；PUT 必须先到达
    expect(order, "请求顺序必须 PUT 先于 export").toBe("put_first");
    expect(
      exportsFor(ledger, projectId).length,
      "export POST 不得在 pending PUT 完成前出现",
    ).toBe(0);

    const putsHeld = putsFor(ledger, projectId);
    expect(putsHeld.length).toBeGreaterThanOrEqual(1);
    const firstPut = putsHeld[0];
    expect(firstPut.method).toBe("PUT");
    expect(firstPut.path).toContain(`/projects/${projectId}/editor-state`);
    expect(firstPut.bodyText).toContain(ANCHOR.techNew);
    expect(firstPut.bodyText).not.toContain(ANCHOR.techOld);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    putGate.release();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "pending 释放后最终精确 {puts:1,exports:1}",
    ).toEqual({ puts: 1, exports: 1 });
    expect(putsFinal[0].bodyText).toContain(ANCHOR.techNew);
    expect(putsFinal[0].bodyText).not.toContain(ANCHOR.techOld);
    expect(exportsFinal[0].type).toBe("export");
    expect(
      putsFinal[0].seq,
      "seq 证明 PUT 严格先于 export",
    ).toBeLessThan(exportsFinal[0].seq);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("商务标 pending 编辑：business PUT 精确含 NEW 且释放前 export=0", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 商务标 pending";
    const projectId = await seedBusiness(request, name, ANCHOR.bizOld);
    const ledger = createLedger();
    const putGate = createHoldGate();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gate: putGate,
      onSeen: () => putSeenResolve(),
      mode: { kind: "continue" },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openBusinessQualify(page, projectId, name);
    const responseBox = page.locator(".bb-qualify-item textarea").first();
    await expect(responseBox).toHaveValue(ANCHOR.bizOld);
    await responseBox.fill(ANCHOR.bizNew);
    await expect(responseBox).toHaveValue(ANCHOR.bizNew);

    await goBusinessExportViaStepper(page, projectId);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;

    expect(order, "商务请求顺序必须 PUT 先于 export").toBe("put_first");
    expect(
      exportsFor(ledger, projectId).length,
      "商务 export POST 不得在 pending PUT 完成前出现",
    ).toBe(0);

    const putsHeld = putsFor(ledger, projectId);
    expect(putsHeld.length).toBeGreaterThanOrEqual(1);
    const body = putsHeld[0].bodyText;
    expect(body).toContain(ANCHOR.bizNew);
    expect(body).not.toContain(ANCHOR.bizOld);
    expect(body).toContain("businessQualify");
    expect(exportsFor(ledger, projectId).length).toBe(0);

    putGate.release();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "商务 pending 释放后最终精确 {puts:1,exports:1}",
    ).toEqual({ puts: 1, exports: 1 });
    expect(putsFinal[0].bodyText).toContain(ANCHOR.bizNew);
    expect(putsFinal[0].bodyText).not.toContain(ANCHOR.bizOld);
    expect(putsFinal[0].bodyText).toContain("businessQualify");
    expect(
      putsFinal[0].seq,
      "商务 seq 证明 PUT 严格先于 export",
    ).toBeLessThan(exportsFinal[0].seq);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("技术标已在途 autosave：PUT 到达后点导出，释放前 export=0", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标 inflight";
    const projectId = await seedTechnical(request, name, ANCHOR.techNoEdit);
    const ledger = createLedger();
    const putGate = createHoldGate();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gate: putGate,
      onSeen: () => putSeenResolve(),
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techInflight}\n`);

    // 同步点：等待防抖 autosave PUT 真正到达（非 sleep）；记录唯一原 PUT seq
    await putSeen;
    const originalPuts = putsFor(ledger, projectId);
    expect(originalPuts.length).toBe(1);
    const originalPutSeq = originalPuts[0].seq;
    expect(originalPuts[0].bodyText).toContain(ANCHOR.techInflight);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    await goTechnicalExportViaLink(page);
    const holdWatch = exportAppearedDuringHold(page, projectId, 4_000);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const appeared = await holdWatch;
    expect(appeared, "在途 PUT 持有期间 export 必须为 0").toBe(false);
    expect(
      { puts: putsFor(ledger, projectId).length, exports: exportsFor(ledger, projectId).length },
      "持有期精确 puts=1/export=0，禁止额外即时 PUT",
    ).toEqual({ puts: 1, exports: 0 });
    expect(putsFor(ledger, projectId)[0].seq).toBe(originalPutSeq);

    putGate.release();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "inflight 释放后最终精确 {puts:1,exports:1}",
    ).toEqual({ puts: 1, exports: 1 });
    expect(putsFinal[0].seq, "原 PUT seq 不得被替换").toBe(originalPutSeq);
    expect(
      exportsFinal[0].seq,
      "export.seq 必须严格大于原 PUT seq",
    ).toBeGreaterThan(originalPutSeq);
    expect(ledger.externalHits).toEqual([]);
  });

  test("商务标已在途 autosave：PUT 到达后点导出，释放前 export=0", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 商务标 inflight";
    const projectId = await seedBusiness(request, name, ANCHOR.bizNoEdit);
    const ledger = createLedger();
    const putGate = createHoldGate();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gate: putGate,
      onSeen: () => putSeenResolve(),
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openBusinessQualify(page, projectId, name);
    await page
      .locator(".bb-qualify-item textarea")
      .first()
      .fill(ANCHOR.bizInflight);
    await putSeen;
    const originalPuts = putsFor(ledger, projectId);
    expect(originalPuts.length).toBe(1);
    const originalPutSeq = originalPuts[0].seq;
    expect(originalPuts[0].bodyText).toContain(ANCHOR.bizInflight);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    await goBusinessExportViaStepper(page, projectId);
    const holdWatch = exportAppearedDuringHold(page, projectId, 4_000);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const appeared = await holdWatch;
    expect(appeared, "商务在途 PUT 持有期间 export 必须为 0").toBe(false);
    expect(
      { puts: putsFor(ledger, projectId).length, exports: exportsFor(ledger, projectId).length },
      "商务持有期精确 puts=1/export=0，禁止额外即时 PUT",
    ).toEqual({ puts: 1, exports: 0 });
    expect(putsFor(ledger, projectId)[0].seq).toBe(originalPutSeq);

    putGate.release();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "商务 inflight 释放后最终精确 {puts:1,exports:1}",
    ).toEqual({ puts: 1, exports: 1 });
    expect(putsFinal[0].seq, "商务原 PUT seq 不得被替换").toBe(originalPutSeq);
    expect(
      exportsFinal[0].seq,
      "商务 export.seq 必须严格大于原 PUT seq",
    ).toBeGreaterThan(originalPutSeq);
    expect(ledger.externalHits).toEqual([]);
  });

  test("技术标无本地修改：editor-state PUT=0 且 export=1", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标无改动";
    const projectId = await seedTechnical(request, name, ANCHOR.techNoEdit);
    const ledger = createLedger();

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, { projectId });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await page.goto(`/technical-plan/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    expect(
      putsFor(ledger, projectId).length,
      "无本地修改不得额外 editor-state PUT",
    ).toBe(0);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("商务标无本地修改：editor-state PUT=0 且 export=1", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 商务标无改动";
    const projectId = await seedBusiness(request, name, ANCHOR.bizNoEdit);
    const ledger = createLedger();

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, { projectId });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await page.goto(`/business-bid/${projectId}/export`);
    await expect(page.getByRole("heading", { name })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible();
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();

    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    expect(putsFor(ledger, projectId).length).toBe(0);
    expect(ledger.externalHits).toEqual([]);
  });

  test("技术标 PUT 409 全状态冲突：export=0 且固定安全冲突 UI", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标409";
    const projectId = await seedTechnical(request, name, ANCHOR.techOld);
    const ledger = createLedger();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      onSeen: () => putSeenResolve(),
      mode: { kind: "full_conflict" },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techConflict}\n`);
    await goTechnicalExportViaLink(page);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "409 场景也必须先 PUT 再决定是否 export").toBe("put_first");
    expect(putsFor(ledger, projectId)[0].bodyText).toContain(
      ANCHOR.techConflict,
    );

    await expect(page.getByTestId("technical-editor-state-conflict")).toBeVisible(
      { timeout: 15_000 },
    );
    await expect(page.getByText(TECH_FULL_CONFLICT)).toBeVisible();
    const bodyText = await page.locator("body").innerText();
    assertSafeUiText(bodyText);
    expect(bodyText).not.toContain(ANCHOR.techConflict);
    expect(exportsFor(ledger, projectId).length).toBe(0);
    expect(await getOpenCalls(page)).toEqual([]);
    expect(ledger.externalHits).toEqual([]);
  });

  test("商务标 PUT 409 全状态冲突：export=0 且固定安全冲突 UI", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 商务标409";
    const projectId = await seedBusiness(request, name, ANCHOR.bizOld);
    const ledger = createLedger();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      onSeen: () => putSeenResolve(),
      mode: { kind: "full_conflict" },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openBusinessQualify(page, projectId, name);
    await page
      .locator(".bb-qualify-item textarea")
      .first()
      .fill(ANCHOR.bizConflict);
    await goBusinessExportViaStepper(page, projectId);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "商务 409 场景必须先 PUT").toBe("put_first");
    await expect(page.getByTestId("business-editor-state-conflict")).toBeVisible(
      { timeout: 15_000 },
    );
    await expect(page.getByText(BIZ_FULL_CONFLICT)).toBeVisible();
    const bodyText = await page.locator("body").innerText();
    assertSafeUiText(bodyText);
    expect(exportsFor(ledger, projectId).length).toBe(0);
    expect(await getOpenCalls(page)).toEqual([]);
  });

  test("技术标普通 PUT 失败：export=0 且固定 saveError 不回显 detail", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标PUT失败";
    const projectId = await seedTechnical(request, name, ANCHOR.techOld);
    const ledger = createLedger();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      onSeen: () => putSeenResolve(),
      mode: { kind: "http_fail", status: 500, detail: ANCHOR.leakDetail },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techFail}\n`);
    await goTechnicalExportViaLink(page);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "普通失败场景必须先 PUT").toBe("put_first");
    await expect(page.getByTestId("technical-editor-save-error")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(TECH_SAVE_ERROR)).toBeVisible();
    const bodyText = await page.locator("body").innerText();
    assertSafeUiText(bodyText);
    expect(exportsFor(ledger, projectId).length).toBe(0);
  });

  test("商务标普通 PUT 失败：export=0 且固定 saveError", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 商务标PUT失败";
    const projectId = await seedBusiness(request, name, ANCHOR.bizOld);
    const ledger = createLedger();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      onSeen: () => putSeenResolve(),
      mode: { kind: "http_fail", status: 503, detail: ANCHOR.leakDetail },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openBusinessQualify(page, projectId, name);
    await page.locator(".bb-qualify-item textarea").first().fill(ANCHOR.bizFail);
    await goBusinessExportViaStepper(page, projectId);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "商务普通失败场景必须先 PUT").toBe("put_first");
    await expect(page.getByTestId("business-editor-save-error")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(BIZ_SAVE_ERROR)).toBeVisible();
    const bodyText = await page.locator("body").innerText();
    assertSafeUiText(bodyText);
    expect(exportsFor(ledger, projectId).length).toBe(0);
  });

  test("技术标非法成功版本：export=0 且进入固定全状态冲突/阻断 UI", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标非法版本";
    const projectId = await seedTechnical(request, name, ANCHOR.techOld);
    const ledger = createLedger();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      onSeen: () => putSeenResolve(),
      mode: { kind: "invalid_success_version" },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techInvalid}\n`);
    await goTechnicalExportViaLink(page);
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "非法版本场景必须先 PUT").toBe("put_first");
    // 非法版本走 enterFullStateBlock → 固定冲突文案
    await expect(page.getByText(TECH_FULL_CONFLICT)).toBeVisible({
      timeout: 15_000,
    });
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("not-a-valid-esv");
    assertSafeUiText(bodyText);
    expect(exportsFor(ledger, projectId).length).toBe(0);
  });

  test("技术标快速双击：仅一次保存准备 PUT 与一次 export", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E 技术标双击";
    const projectId = await seedTechnical(request, name, ANCHOR.techOld);
    const ledger = createLedger();
    const putGate = createHoldGate();
    let putSeenResolve!: () => void;
    const putSeen = new Promise<void>((r) => {
      putSeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gate: putGate,
      onSeen: () => putSeenResolve(),
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techDouble}\n`);
    await goTechnicalExportViaLink(page);

    const btn = page.getByRole("button", { name: /生成并下载 Word/ });
    const orderPromise = waitPutBeforeExport(page, projectId, putSeen);
    // 快速双击：同一 HTMLButtonElement 在同一浏览器任务内连续 click 两次；禁止 force
    await btn.evaluate((el) => {
      const button = el as HTMLButtonElement;
      button.click();
      button.click();
    });
    const order = await orderPromise;
    expect(order, "双击仍须先进入一次保存准备 PUT").toBe("put_first");
    // 释放前精确计数：同步 token 单飞 → 仅 1 次 PUT、0 次 export
    expect(
      { puts: putsFor(ledger, projectId).length, exports: exportsFor(ledger, projectId).length },
      "释放前精确计数证明同步 token 单飞",
    ).toEqual({ puts: 1, exports: 0 });
    expect(putsFor(ledger, projectId)[0].bodyText).toContain(ANCHOR.techDouble);

    putGate.release();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);
    // 释放后精确计数：不得出现第二次保存准备或第二次 export
    expect(
      { puts: putsFor(ledger, projectId).length, exports: exportsFor(ledger, projectId).length },
      "释放后精确计数仍为单飞 {puts:1,exports:1}",
    ).toEqual({ puts: 1, exports: 1 });
  });

  test("项目A保存挂起后切B：迟到完成不得在B创建 export/下载", async ({
    page,
    request,
  }) => {
    const nameA = "E2E V1E 迟到隔离A";
    const nameB = "E2E V1E 迟到隔离B";
    const projectA = await seedTechnical(request, nameA, ANCHOR.techLateA);
    const projectB = await seedTechnical(request, nameB, ANCHOR.techLateB);
    const ledger = createLedger();
    const putGateA = createHoldGate();
    let putASeenResolve!: () => void;
    const putASeen = new Promise<void>((r) => {
      putASeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    // 仅挂起 A 的 PUT；B 的请求默认 continue（由 barrier 过滤 projectId）
    await installEditorStatePutBarrier(page, ledger, {
      projectId: projectA,
      gate: putGateA,
      onSeen: () => putASeenResolve(),
    });
    await installExportTaskBarrier(page, ledger, { stubSuccess: true });

    await openTechnicalContent(page, projectA, nameA);
    await page
      .getByLabel("正文：V1E章节")
      .fill(`${ANCHOR.techLateA}_EDIT\n`);
    await goTechnicalExportViaLink(page);
    const orderPromise = waitPutBeforeExport(page, projectA, putASeen);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const order = await orderPromise;
    expect(order, "切项目前 A 必须先进入 PUT 持有").toBe("put_first");
    expect(
      exportsFor(ledger, projectA).length,
      "切项目前 A 的 export 必须仍为 0（PUT 持有中）",
    ).toBe(0);

    // SPA 软切换到 B：同一文档保留 A 飞行中的闭包
    await softNavigate(page, `/technical-plan/${projectB}/export`);
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();

    const exportsBBefore = exportsFor(ledger, projectB).length;
    const opensBefore = (await getOpenCalls(page)).length;

    // 释放 A 的迟到 PUT：完成后不得在 B 创建 export/下载
    const lateExportA = page
      .waitForRequest((req) => isProjectExportRequest(req, projectA), {
        timeout: 5_000,
      })
      .then(() => true)
      .catch(() => false);
    const lateExportB = page
      .waitForRequest((req) => isProjectExportRequest(req, projectB), {
        timeout: 5_000,
      })
      .then(() => true)
      .catch(() => false);
    putGateA.release();
    expect(await lateExportA, "A 迟到完成不得再创建 export").toBe(false);
    expect(await lateExportB, "A 迟到完成不得在 B 创建 export").toBe(false);

    const opensAfter = await getOpenCalls(page);
    expect(opensAfter.length).toBe(opensBefore);
    expect(exportsFor(ledger, projectB).length).toBe(exportsBBefore);
    expect(exportsFor(ledger, projectA).length).toBe(0);
    expect(ledger.externalHits).toEqual([]);
  });

  test("A2 技术标：export 持有后软切 B，释放 A 成功响应零下载/零污染", async ({
    page,
    request,
  }) => {
    const nameA = "E2E V1E A2技术A";
    const nameB = "E2E V1E A2技术B";
    const projectA = await seedTechnical(request, nameA, ANCHOR.techNoEdit);
    const projectB = await seedTechnical(request, nameB, ANCHOR.techLateB);
    const ledger = createLedger();
    const exportGateA = createHoldGate();
    let exportASeenResolve!: () => void;
    const exportASeen = new Promise<void>((r) => {
      exportASeenResolve = r;
    });
    const downloads: string[] = [];
    page.on("download", (d) => {
      downloads.push(d.url());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    // A 无脏编辑：记录 A 的 PUT（期望 0）；仅挂起 A 的 export 成功响应并注入可判定告警
    await installEditorStatePutBarrier(page, ledger, { projectId: projectA });
    await installExportTaskBarrier(page, ledger, {
      gate: exportGateA,
      holdOnlyProjectId: projectA,
      stubSuccess: true,
      resultMode: "technical",
      imageWarnings: [ANCHOR.techA2Warn],
      onSeen: (rec) => {
        if (rec.projectId === projectA) exportASeenResolve();
      },
    });

    await page.goto(`/technical-plan/${projectA}/export`);
    await expect(page.getByRole("heading", { name: nameA })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 同步点：A 的 export POST 已到达并持有响应
    await exportASeen;
    expect(exportsFor(ledger, projectA).length).toBe(1);
    expect(putsFor(ledger, projectA).length).toBe(0);
    expect(exportsFor(ledger, projectB).length).toBe(0);

    // 软切 B 导出页且 B 不点
    await softNavigate(page, `/technical-plan/${projectB}/export`);
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出 Word")).toBeVisible();
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(page.getByText(ANCHOR.techA2Warn)).toHaveCount(0);

    const opensBefore = (await getOpenCalls(page)).length;
    const downloadsBefore = downloads.length;
    const exportsBBefore = exportsFor(ledger, projectB).length;

    exportGateA.release();
    const openIncreased = await openCallsIncreased(page, opensBefore, 5_000);
    expect(openIncreased, "释放 A 成功响应后 window.open 必须精确零新增").toBe(
      false,
    );
    expect(
      downloads.length,
      "释放 A 成功响应后 download 事件必须精确零新增",
    ).toBe(downloadsBefore);
    expect(await getOpenCalls(page)).toHaveLength(opensBefore);

    // B 告警/提示不被 A 污染
    await expect(page.getByText(ANCHOR.techA2Warn)).toHaveCount(0);
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible();
    expect(
      exportsFor(ledger, projectB).length,
      "B 未点击时 export 必须为 0",
    ).toBe(exportsBBefore);
    expect(
      exportsFor(ledger, projectA).length,
      "A export 保持 1（POST 已到达）",
    ).toBe(1);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("A2 商务标：export 持有后软切 B，direct window.open 零新增且零污染", async ({
    page,
    request,
  }) => {
    const nameA = "E2E V1E A2商务A";
    const nameB = "E2E V1E A2商务B";
    const projectA = await seedBusiness(request, nameA, ANCHOR.bizA2LateA);
    const projectB = await seedBusiness(request, nameB, ANCHOR.bizA2LateB);
    const ledger = createLedger();
    const exportGateA = createHoldGate();
    let exportASeenResolve!: () => void;
    const exportASeen = new Promise<void>((r) => {
      exportASeenResolve = r;
    });
    const downloads: string[] = [];
    page.on("download", (d) => {
      downloads.push(d.url());
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, { projectId: projectA });
    await installExportTaskBarrier(page, ledger, {
      gate: exportGateA,
      holdOnlyProjectId: projectA,
      stubSuccess: true,
      resultMode: "business",
      imageWarnings: [ANCHOR.bizA2Warn],
      onSeen: (rec) => {
        if (rec.projectId === projectA) exportASeenResolve();
      },
    });

    await page.goto(`/business-bid/${projectA}/export`);
    await expect(page.getByRole("heading", { name: nameA })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible();
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await exportASeen;
    expect(exportsFor(ledger, projectA).length).toBe(1);
    expect(putsFor(ledger, projectA).length).toBe(0);
    expect(exportsFor(ledger, projectB).length).toBe(0);

    await softNavigate(page, `/business-bid/${projectB}/export`);
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible();
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(page.getByText(ANCHOR.bizA2Warn)).toHaveCount(0);

    const opensBefore = (await getOpenCalls(page)).length;
    const downloadsBefore = downloads.length;
    const exportsBBefore = exportsFor(ledger, projectB).length;

    // 独立证明商务链 direct window.open 不发生，不得复用技术标结果
    exportGateA.release();
    const openIncreased = await openCallsIncreased(page, opensBefore, 5_000);
    expect(
      openIncreased,
      "商务 A2：释放 A 成功响应后 direct window.open 必须精确零新增",
    ).toBe(false);
    expect(
      downloads.length,
      "商务 A2：download 事件必须精确零新增",
    ).toBe(downloadsBefore);
    const opensAfter = await getOpenCalls(page);
    expect(opensAfter).toHaveLength(opensBefore);
    expect(
      opensAfter.some((u) => u.includes(`/projects/${projectA}/export/download/`)),
      "商务 A2：不得出现 A 的 downloadPath window.open",
    ).toBe(false);

    await expect(page.getByText(ANCHOR.bizA2Warn)).toHaveCount(0);
    await expect(page.getByRole("region", { name: "导出图片告警" })).toHaveCount(
      0,
    );
    await expect(page.getByRole("heading", { name: nameB })).toBeVisible();
    expect(exportsFor(ledger, projectB).length).toBe(exportsBBefore);
    expect(exportsFor(ledger, projectA).length).toBe(1);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("A3 技术标：S1 持有期间写 S2，释放后必须 S2 PUT 先于 export", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E A3技术";
    const projectId = await seedTechnical(request, name, ANCHOR.techNoEdit);
    const ledger = createLedger();
    const gateS1 = createHoldGate();
    const gateS2 = createHoldGate();
    let s1SeenResolve!: () => void;
    const s1Seen = new Promise<void>((r) => {
      s1SeenResolve = r;
    });
    let s2SeenResolve!: () => void;
    const s2Seen = new Promise<void>((r) => {
      s2SeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gateForIndex: (index) => {
        if (index === 0) return gateS1;
        if (index === 1) return gateS2;
        return undefined;
      },
      onSeen: (rec) => {
        const idx = putsFor(ledger, projectId).length - 1;
        if (idx === 0) s1SeenResolve();
        if (idx === 1) s2SeenResolve();
      },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
      resultMode: "technical",
    });

    await openTechnicalContent(page, projectId, name);
    await page.getByLabel("正文：V1E章节").fill(`${ANCHOR.techA3S1}\n`);
    // 同步点：S1 autosave PUT 已到达并持有
    await s1Seen;
    expect(putsFor(ledger, projectId).length).toBe(1);
    expect(putsFor(ledger, projectId)[0].bodyText).toContain(ANCHOR.techA3S1);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    // 在导出页点击使 flush F 排队
    await goTechnicalExportViaLink(page);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    // 持有 S1 期间 export 不得抢先
    const exportDuringS1 = await exportAppearedDuringHold(page, projectId, 3_000);
    expect(exportDuringS1, "S1 持有期间 export 必须为 0").toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    // 保持 S1 持有，软导航回同项目 content，控件可见后再装虚拟时钟
    await softNavigate(page, `/technical-plan/${projectId}/content`);
    await expect(page.getByTestId("technical-editor-workspace")).toBeVisible({
      timeout: 20_000,
    });
    const bodyBox = page.getByLabel("正文：V1E章节");
    await expect(bodyBox).toBeVisible();
    // page.clock 不可用则硬失败，禁止改用固定墙钟等待
    if (
      !page.clock ||
      typeof page.clock.install !== "function" ||
      typeof page.clock.fastForward !== "function"
    ) {
      throw new Error(
        "page.clock 不可用：A3 技术标禁止改用固定墙钟等待（waitForTimeout/sleep）",
      );
    }
    await page.clock.install();
    await bodyBox.fill(`${ANCHOR.techA3S2}\n`);
    await expect(bodyBox).toHaveValue(new RegExp(ANCHOR.techA3S2));
    // 技术标 800ms 防抖 +1ms；必须在 release S1 前完成虚拟时钟推进
    await page.clock.fastForward(801);

    // 释放 S1 后竞速：必须 S2 PUT 先到；当前生产 export_first 为真红可读首因
    const racePromise = waitS2PutBeforeExport(page, projectId, s2Seen);
    gateS1.release();
    const race = await racePromise;
    expect(
      race,
      "释放 S1 后请求顺序必须 s2_first（export_first/timeout 为可读首红）",
    ).toBe("s2_first");
    expect(
      exportsFor(ledger, projectId).length,
      "s2_first 时持有 S2：export 必须为 0",
    ).toBe(0);
    expect(putsFor(ledger, projectId).length).toBeGreaterThanOrEqual(2);
    expect(putsFor(ledger, projectId)[1].bodyText).toContain(ANCHOR.techA3S2);
    expect(putsFor(ledger, projectId)[1].bodyText).not.toContain(
      ANCHOR.techA3S1,
    );

    // s2_first 语义：持有 S2 期间 export=0
    const exportDuringS2 = await exportAppearedDuringHold(page, projectId, 3_000);
    expect(exportDuringS2, "S2 持有期间 export 必须为 0").toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    // 释放 S2 后仍 export=0：第一次点击因 generation 变化保守 blocked，不自动重试
    gateS2.release();
    const exportAfterS2Release = await exportAppearedDuringHold(
      page,
      projectId,
      3_000,
    );
    expect(
      exportAfterS2Release,
      "释放 S2 后第一次导出不得自动重试（export 仍为 0）",
    ).toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);
    expect(
      putsFor(ledger, projectId).length,
      "释放 S2 后不得产生额外 PUT",
    ).toBe(2);

    // 用户第二次显式导航到同项目 export 页并点击
    await softNavigate(page, `/technical-plan/${projectId}/export`);
    await expect(page.getByText("准备导出 Word")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);

    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "A3 技术最终精确 {puts:2,exports:1}",
    ).toEqual({ puts: 2, exports: 1 });
    expect(putsFinal[0].bodyText).toContain(ANCHOR.techA3S1);
    expect(putsFinal[1].bodyText).toContain(ANCHOR.techA3S2);
    expect(putsFinal[1].bodyText).not.toContain(ANCHOR.techA3S1);
    expect(
      putsFinal[0].seq,
      "精确全序 S1.seq < S2.seq",
    ).toBeLessThan(putsFinal[1].seq);
    expect(
      putsFinal[1].seq,
      "精确全序 S2.seq < export.seq",
    ).toBeLessThan(exportsFinal[0].seq);
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("A3 商务标：S1 持有期间软回 qualify 写 S2，精确 business payload 与全序", async ({
    page,
    request,
  }) => {
    const name = "E2E V1E A3商务";
    const projectId = await seedBusiness(request, name, ANCHOR.bizNoEdit);
    const ledger = createLedger();
    const gateS1 = createHoldGate();
    const gateS2 = createHoldGate();
    let s1SeenResolve!: () => void;
    const s1Seen = new Promise<void>((r) => {
      s1SeenResolve = r;
    });
    let s2SeenResolve!: () => void;
    const s2Seen = new Promise<void>((r) => {
      s2SeenResolve = r;
    });

    await installOpenStub(page);
    await installNetworkGuard(page, ledger);
    await installEditorStatePutBarrier(page, ledger, {
      projectId,
      gateForIndex: (index) => {
        if (index === 0) return gateS1;
        if (index === 1) return gateS2;
        return undefined;
      },
      onSeen: (rec) => {
        const idx = putsFor(ledger, projectId).length - 1;
        if (idx === 0) s1SeenResolve();
        if (idx === 1) s2SeenResolve();
      },
    });
    await installExportTaskBarrier(page, ledger, {
      projectId,
      stubSuccess: true,
      resultMode: "business",
    });

    await openBusinessQualify(page, projectId, name);
    await page
      .locator(".bb-qualify-item textarea")
      .first()
      .fill(ANCHOR.bizA3S1);
    await s1Seen;
    expect(putsFor(ledger, projectId).length).toBe(1);
    expect(putsFor(ledger, projectId)[0].bodyText).toContain(ANCHOR.bizA3S1);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    await goBusinessExportViaStepper(page, projectId);
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    const exportDuringS1 = await exportAppearedDuringHold(page, projectId, 3_000);
    expect(exportDuringS1, "商务 S1 持有期间 export 必须为 0").toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    // 软导航回 qualify，控件可见后再装虚拟时钟
    await softNavigate(page, `/business-bid/${projectId}/qualify`);
    await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
      timeout: 20_000,
    });
    const textarea = page.locator(".bb-qualify-item textarea").first();
    await expect(textarea).toBeVisible();
    // page.clock 不可用则硬失败，禁止改用固定墙钟等待
    if (
      !page.clock ||
      typeof page.clock.install !== "function" ||
      typeof page.clock.fastForward !== "function"
    ) {
      throw new Error(
        "page.clock 不可用：A3 商务标禁止改用固定墙钟等待（waitForTimeout/sleep）",
      );
    }
    await page.clock.install();
    await textarea.fill(ANCHOR.bizA3S2);
    await expect(textarea).toHaveValue(ANCHOR.bizA3S2);
    // 商务标 600ms 防抖 +1ms；必须在 release S1 前完成虚拟时钟推进
    await page.clock.fastForward(601);

    // 释放 S1 后竞速：必须 s2_first；当前生产 export_first 为真红可读首因
    const racePromise = waitS2PutBeforeExport(page, projectId, s2Seen);
    gateS1.release();
    const race = await racePromise;
    expect(
      race,
      "商务 A3：释放 S1 后必须 s2_first（export_first 为可读首红）",
    ).toBe("s2_first");
    expect(
      exportsFor(ledger, projectId).length,
      "s2_first 时持有 S2：export 必须为 0",
    ).toBe(0);
    expect(putsFor(ledger, projectId).length).toBeGreaterThanOrEqual(2);
    const s2Body = putsFor(ledger, projectId)[1].bodyText;
    expect(s2Body).toContain(ANCHOR.bizA3S2);
    expect(s2Body).not.toContain(ANCHOR.bizA3S1);
    // 精确 business payload：资格响应在 businessQualify 结构内
    expect(s2Body).toContain("businessQualify");
    expect(s2Body).toMatch(
      new RegExp(
        `"response"\\s*:\\s*"${ANCHOR.bizA3S2.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`,
      ),
    );

    // s2_first 语义：持有 S2 期间 export=0
    const exportDuringS2 = await exportAppearedDuringHold(page, projectId, 3_000);
    expect(exportDuringS2, "商务 S2 持有期间 export 必须为 0").toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);

    // 释放 S2 后仍 export=0：第一次点击因 generation 变化保守 blocked，不自动重试
    gateS2.release();
    const exportAfterS2Release = await exportAppearedDuringHold(
      page,
      projectId,
      3_000,
    );
    expect(
      exportAfterS2Release,
      "商务释放 S2 后第一次导出不得自动重试（export 仍为 0）",
    ).toBe(false);
    expect(exportsFor(ledger, projectId).length).toBe(0);
    expect(
      putsFor(ledger, projectId).length,
      "商务释放 S2 后不得产生额外 PUT",
    ).toBe(2);

    // 用户第二次显式导航到同项目 export 页并点击
    await softNavigate(page, `/business-bid/${projectId}/export`);
    await expect(page.getByText("准备导出商务标 Word")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /生成并下载 Word/ }).click();
    await expect
      .poll(() => exportsFor(ledger, projectId).length, { timeout: 20_000 })
      .toBe(1);

    const putsFinal = putsFor(ledger, projectId);
    const exportsFinal = exportsFor(ledger, projectId);
    expect(
      { puts: putsFinal.length, exports: exportsFinal.length },
      "A3 商务最终精确 {puts:2,exports:1}",
    ).toEqual({ puts: 2, exports: 1 });
    expect(putsFinal[0].bodyText).toContain(ANCHOR.bizA3S1);
    expect(putsFinal[1].bodyText).toContain(ANCHOR.bizA3S2);
    expect(putsFinal[1].bodyText).not.toContain(ANCHOR.bizA3S1);
    expect(putsFinal[0].seq).toBeLessThan(putsFinal[1].seq);
    expect(putsFinal[1].seq).toBeLessThan(exportsFinal[0].seq);
    expect(exportsFinal[0].type).toBe("export");
    expect(ledger.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("反假绿：本文件源码禁止 sleep/skip/宽松放行", async () => {
    const sourcePath = fileURLToPath(import.meta.url);
    const full = fs.readFileSync(sourcePath, "utf8");
    // 仅扫描本测试文件；禁止读取生产 hooks/pages 源码作绿证
    expect(full).not.toMatch(/waitForTimeout\s*\(/);
    expect(full).not.toMatch(/\bsetTimeout\s*\(/);
    expect(full).not.toMatch(/\bsleep\s*\(/i);
    expect(full).not.toMatch(/\btest\.(skip|fixme)\b/);
    expect(full).not.toMatch(/\bit\.(skip|fixme)\b/);
    expect(full).not.toMatch(/\btest\.only\b/);
    // 禁止宽松恒真放行（扫描代码语句，避开本注释字面量）
    expect(full).not.toMatch(/=\s*true\s*\|\|\s*true\b/i);
    expect(full).not.toMatch(/\bor\s+True\b/);
    // 禁止在本文件内读取生产源码路径
    expect(full).not.toMatch(/features\/technical-plan\/hooks/);
    expect(full).not.toMatch(/features\/business-bid\/hooks/);
    expect(full.length).toBeGreaterThan(1000);
  });
});
