/**
 * 模块：P8B / V1-M M3 工作空间解析策略接线 E2E
 * 用途：验收 light|managed|local|ask 在技术标与商务标入口的真实决策、
 *       失败收口、软切零污染、隐私红门与网络/存储边界。
 * 对接：Playwright chromium；后端 8010 / 前端 5174；GET /settings/parse-strategy；parse 任务。
 * 二次开发：禁止真实云 Key、固定 sleep 作完成证据、业务 route 桩冒充绿；
 *           策略故障仅对 parse-strategy 做可释放 HoldGate / 单次 500 注入；
 *           术语 exact 冻结为「轻量解析/本机自动 OCR/人工本地回传」，禁止兼容旧 MinerU 文案。
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
/** ask 对话框 / 设置页冻结三选一 exact 标签 */
const LABEL_LIGHT = "轻量解析";
const LABEL_MANAGED = "本机自动 OCR";
const LABEL_LOCAL = "人工本地回传";
/** 真实空 manifest 时后端 task.error 固定文案；UI 必须遮罩不可见 */
const REAL_MANIFEST_TASK_ERROR = "运行时清单无效";
/** U2 单点 POST /files 注入的固定 detail，须原样出现在 pipeline.error */
const NEW_UPLOAD_FAILURE = "NEW_UPLOAD_FAILURE";
/** managed 失败 result.diagnosticCode 固定值（契约冻结） */
const RUNTIME_MANIFEST_INVALID = "runtime_manifest_invalid";

type ParseStrategy = "light" | "managed" | "local" | "ask";

type CapturedRequest = {
  method: string;
  path: string;
  url: string;
  postData: string | null;
};

/** 用途：可释放 HoldGate，阻塞 route fulfill 直到 release。 */
type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  waiterCount: () => number;
};

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
 * 用途：技术入口精确「开始解析」（M3 冻结中性文案；禁止旧「轻量解析」按钮冒充）。
 */
function parseActionButton(page: Page) {
  return page.getByRole("button", { name: "开始解析", exact: true });
}

/**
 * 用途：SPA 软切（history.pushState + popstate），禁止 page.goto 全刷新冒充软切。
 * 对接：React Router BrowserRouter 监听 popstate。
 */
async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

/** 用途：Hold route 精确 held/fulfilled 计数（禁止固定时间完成门）。 */
type HoldRouteCounters = {
  held: number;
  fulfilled: number;
};

/**
 * 用途：安装可释放 HoldGate 拦截权威 parse-strategy GET（仅 GET；其它方法 continue）。
 * 对接：T3 在途软切；release 后精确 fulfilled + 业务 loading 解除。
 */
async function installParseStrategyGetHold(
  page: Page,
  gate: HoldGate,
  opts: {
    strategy?: ParseStrategy;
    status?: number;
    body?: unknown;
  } = {},
): Promise<HoldRouteCounters> {
  const counters: HoldRouteCounters = { held: 0, fulfilled: 0 };
  const status = opts.status ?? 200;
  const body =
    opts.body ??
    (status === 200
      ? { parseStrategy: opts.strategy ?? "light" }
      : {
          detail: {
            code: "internal_error",
            message: SENSITIVE_LEAK,
          },
        });
  await page.route("**/api/settings/parse-strategy", async (route) => {
    if (route.request().method().toUpperCase() !== "GET") {
      await route.continue();
      return;
    }
    counters.held += 1;
    await gate.wait();
    await route.fulfill({
      status,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify(body),
    });
    counters.fulfilled += 1;
  });
  return counters;
}

/**
 * 用途：唯一 DOM 隐私探针 — MutationObserver 检查 MutationRecord
 *       addedNodes/removedNodes/characterData（oldValue + 当前 target），并扫 current DOM。
 * 对接：T4/T5/Q7 全过程零泄漏；callbackCount 仅由 observer 回调增加，手工 scan 不计。
 */
async function installDomPrivacyProbe(
  page: Page,
  markers: string[],
): Promise<void> {
  await page.evaluate((ms) => {
    type Probe = {
      callbackCount: number;
      hitMarkers: string[];
      observer: MutationObserver | null;
    };
    const w = window as unknown as { __biaoshuPrivacyProbe?: Probe };
    if (w.__biaoshuPrivacyProbe?.observer) {
      w.__biaoshuPrivacyProbe.observer.disconnect();
    }
    const probe: Probe = {
      callbackCount: 0,
      hitMarkers: [],
      observer: null,
    };
    const noteHits = (text: string) => {
      for (const m of ms) {
        if (m && text.includes(m) && !probe.hitMarkers.includes(m)) {
          probe.hitMarkers.push(m);
        }
      }
    };
    const nodeText = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return node.textContent || "";
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        return (node as Element).textContent || "";
      }
      return "";
    };
    // 安装时扫当前 DOM，不增加 callbackCount
    noteHits(document.body?.innerText || "");
    const obs = new MutationObserver((records) => {
      // callbackCount 只由 Observer 回调增加
      probe.callbackCount += 1;
      for (const rec of records) {
        for (const n of Array.from(rec.addedNodes)) {
          noteHits(nodeText(n));
        }
        for (const n of Array.from(rec.removedNodes)) {
          noteHits(nodeText(n));
        }
        // Q1：characterData 必须同时检查 rec.oldValue 与当前 target
        if (rec.type === "characterData" && rec.target) {
          noteHits(rec.target.textContent || "");
          if (typeof rec.oldValue === "string") {
            noteHits(rec.oldValue);
          }
        }
      }
      // 同时扫 current DOM
      noteHits(document.body?.innerText || "");
    });
    obs.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      characterDataOldValue: true,
    });
    probe.observer = obs;
    w.__biaoshuPrivacyProbe = probe;
  }, markers);
}

/** 用途：读取隐私探针结果并 disconnect Observer。 */
async function readDomPrivacyProbe(page: Page): Promise<{
  callbackCount: number;
  hitMarkers: string[];
}> {
  return page.evaluate(() => {
    type Probe = {
      callbackCount: number;
      hitMarkers: string[];
      observer: MutationObserver | null;
    };
    const w = window as unknown as { __biaoshuPrivacyProbe?: Probe };
    const probe = w.__biaoshuPrivacyProbe;
    if (probe?.observer) {
      probe.observer.disconnect();
      probe.observer = null;
    }
    return {
      callbackCount: probe?.callbackCount ?? 0,
      hitMarkers: probe?.hitMarkers ?? [],
    };
  });
}

/** 用途：统计权威 parse-strategy GET 次数（精确增量门）。 */
function countStrategyGets(hits: CapturedRequest[]): number {
  return hits.filter(
    (h) =>
      h.method === "GET" &&
      (h.path === "/api/settings/parse-strategy" ||
        h.path === "/api/settings/parse-strategy/"),
  ).length;
}

/**
 * 用途：真实 API multipart 预置项目 source 文件（禁止 UI 上传自动 parse 作 seed）。
 */
async function seedProjectSourceFileViaApi(
  request: APIRequestContext,
  projectId: string,
  filename: string,
): Promise<void> {
  const res = await request.post(`${API}/projects/${projectId}/files`, {
    multipart: {
      file: {
        name: filename,
        mimeType: "text/plain",
        buffer: Buffer.from(
          `# E2E 招标文件\n\n一、项目概况\nAPI 预置源文件。\n二、资格条件\n具备相关资质。\n`,
          "utf8",
        ),
      },
    },
  });
  expect(res.status(), await res.text()).toBe(201);
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

/** Q9：浏览器 localStorage/sessionStorage 结构化快照（key+value 全量，含空 key）。 */
type BrowserStorageSnapshot = {
  localStorage: Record<string, string>;
  sessionStorage: Record<string, string>;
};

/** 设置页合法兜底 blob 键；仅此键允许既有 parseStrategy 字段（须与 before 字节相同）。 */
const SETTINGS_V1_KEY = "biaoshu.settings.v1";

/** 用途：key 是否为策略命名（parseStrategy / parse-strategy / parse_strategy）。 */
function isStrategyNamedKey(key: string): boolean {
  return /parse[-_]?strategy/i.test(key);
}

/** 用途：value 是否含策略字段/命名（含 "parseStrategy" JSON 字段）。 */
function valueHasStrategyContent(value: string): boolean {
  const vl = value.toLowerCase();
  return vl.includes('"parsestrategy"') || /parse[-_]?strategy/.test(vl);
}

/**
 * 用途：模拟 capture 的 key 规范化（null → ""），无条件写入 value。
 * 对接：Q9 纯 helper 自检；与 page.evaluate dump 规则一致。
 */
function dumpStorageEntriesLikeCapture(
  entries: Array<readonly [string | null, string | null]>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < entries.length; i += 1) {
    const k = entries[i][0] ?? "";
    out[k] = entries[i][1] ?? "";
  }
  return out;
}

/** 用途：捕获动作前/后 storage 结构化 snapshot（空 key 不得漏记）。 */
async function captureBrowserStorageSnapshot(
  page: Page,
): Promise<BrowserStorageSnapshot> {
  return page.evaluate(() => {
    const dump = (storage: Storage): Record<string, string> => {
      const out: Record<string, string> = {};
      for (let i = 0; i < storage.length; i += 1) {
        // 无条件记录：storage.key(i) 为 null 时归为 ""，禁止 if (k) 漏空字符串 key
        const k = storage.key(i) ?? "";
        out[k] = storage.getItem(k) ?? "";
      }
      return out;
    };
    return {
      localStorage: dump(window.localStorage),
      sessionStorage: dump(window.sessionStorage),
    };
  });
}

/**
 * 用途：key+value 全量策略泄漏检查；传入 before 锁定「解析动作不得新增/改写策略缓存」。
 * 对接：Q9；非 settings.v1 禁止策略命名 key 与 value 内策略字段；
 *       settings.v1 若含 parseStrategy，仅允许 before 同 key/value 字节完全相同；
 *       空 key 同样扫描；禁止盲 continue / 条件 return 冒充检查。
 */
function assertStorageNoStrategyLeak(
  snap: BrowserStorageSnapshot,
  before?: BrowserStorageSnapshot,
): void {
  const checkBag = (
    bag: Record<string, string>,
    label: "localStorage" | "sessionStorage",
    beforeBag?: Record<string, string>,
  ) => {
    for (const [key, value] of Object.entries(bag)) {
      // 所有 key/value 实际读取（含空字符串 key）
      expect(typeof key, `${label} key 类型`).toBe("string");
      expect(typeof value, `${label} value 类型@${JSON.stringify(key)}`).toBe(
        "string",
      );

      if (key === SETTINGS_V1_KEY) {
        // 设置页合法兜底：允许 blob 存在；若含策略字段则必须与 before 字节全同
        if (valueHasStrategyContent(value)) {
          expect(
            beforeBag,
            `${label} ${SETTINGS_V1_KEY} 含策略字段时必须提供动作前 snapshot`,
          ).toBeTruthy();
          expect(
            beforeBag &&
              Object.prototype.hasOwnProperty.call(beforeBag, SETTINGS_V1_KEY),
            `${label} ${SETTINGS_V1_KEY} 禁止由解析动作新增含策略的 settings blob`,
          ).toBe(true);
          const beforeVal = beforeBag![SETTINGS_V1_KEY];
          expect(
            value,
            `${label} ${SETTINGS_V1_KEY} 策略内容须与动作前 key/value 字节完全相同`,
          ).toBe(beforeVal);
        }
        // before 含策略而 after 改写：显式失败（不依赖 deep equal 单独兜底）
        if (
          beforeBag &&
          Object.prototype.hasOwnProperty.call(beforeBag, SETTINGS_V1_KEY)
        ) {
          const beforeVal = beforeBag[SETTINGS_V1_KEY];
          if (valueHasStrategyContent(beforeVal)) {
            expect(
              value,
              `${label} ${SETTINGS_V1_KEY} 禁止改写动作前既有策略兜底`,
            ).toBe(beforeVal);
          }
        }
      } else {
        // 非 settings.v1：策略命名 key 与 value 内策略字段均禁止（空 key 同样适用）
        expect(
          isStrategyNamedKey(key),
          `${label} 禁止策略命名 key=${JSON.stringify(key)}`,
        ).toBe(false);
        expect(
          valueHasStrategyContent(value),
          `${label} 禁止 value 含策略字段@${JSON.stringify(key)}`,
        ).toBe(false);
      }
    }

    // before 含策略的 settings.v1 被删除：after 循环见不到该 key，在此补检
    if (
      beforeBag &&
      Object.prototype.hasOwnProperty.call(beforeBag, SETTINGS_V1_KEY)
    ) {
      const beforeVal = beforeBag[SETTINGS_V1_KEY];
      if (valueHasStrategyContent(beforeVal)) {
        expect(
          Object.prototype.hasOwnProperty.call(bag, SETTINGS_V1_KEY),
          `${label} 禁止删除含策略的 ${SETTINGS_V1_KEY}`,
        ).toBe(true);
        if (Object.prototype.hasOwnProperty.call(bag, SETTINGS_V1_KEY)) {
          expect(
            bag[SETTINGS_V1_KEY],
            `${label} ${SETTINGS_V1_KEY} 禁止改写/替换既有策略兜底`,
          ).toBe(beforeVal);
        }
      }
    }
  };
  checkBag(snap.localStorage, "localStorage", before?.localStorage);
  checkBag(snap.sessionStorage, "sessionStorage", before?.sessionStorage);
}

/**
 * 用途：parse 决策动作不得改变任何 storage 条目（含空 key 与 biaoshu.settings.v1）。
 * 对接：Q9 动作前后 snapshot 深比较。
 */
function assertStorageUnchangedByParseAction(
  before: BrowserStorageSnapshot,
  after: BrowserStorageSnapshot,
): void {
  expect(after.localStorage).toEqual(before.localStorage);
  expect(after.sessionStorage).toEqual(before.sessionStorage);
}

/**
 * 用途：断言浏览器未将策略决策旁路持久化；before 传入时锁定 parse 动作零改写。
 * 对接：Q9；leak 检查与全量深比较均消费 before，禁止 settings.v1 盲 continue。
 */
async function assertNoStrategyPersistence(
  page: Page,
  before?: BrowserStorageSnapshot,
) {
  const after = await captureBrowserStorageSnapshot(page);
  assertStorageNoStrategyLeak(after, before);
  if (before) {
    assertStorageUnchangedByParseAction(before, after);
  }
}

/**
 * 用途：零时长任务队列排空（非稳定窗 / 非 sleep）。
 * 因果：仅在 response finished + body 已消费后调用；
 *       双 rAF 对齐帧回调，MessageChannel 作零时长宏任务，
 *       排空 refresh 已入队的 continuation；不得单独当「等一会就稳」。
 */
async function waitPageContinuationBarrier(page: Page): Promise<void> {
  await page.evaluate(() => {
    return new Promise<void>((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const ch = new MessageChannel();
          ch.port1.onmessage = () => resolve();
          ch.port2.postMessage(null);
        });
      });
    });
  });
}

/**
 * 用途：HoldGate release 后等待旧策略 GET 的浏览器 finished + body 消费 + continuation。
 * 因果：hold.fulfilled 只证明 route 侧写入完成，不能冒充页面完成；
 *       必须等 Response.finished、强制 text() 消费 body，再 barrier，最后才锁零 POST。
 * 对接：T3 Q2；禁止 sleep / waitForTimeout / Date.now 稳定窗 / networkidle。
 */
async function releaseAndAwaitHeldStrategyContinuation(
  page: Page,
  gate: HoldGate,
  hold: HoldRouteCounters,
  fulfilledBefore: number,
): Promise<void> {
  const responsePromise = page.waitForResponse(
    (r) => {
      try {
        const u = new URL(r.url());
        return (
          (u.pathname === "/api/settings/parse-strategy" ||
            u.pathname === "/api/settings/parse-strategy/") &&
          r.request().method().toUpperCase() === "GET"
        );
      } catch {
        return false;
      }
    },
    { timeout: 15_000 },
  );
  gate.release();
  const resp = await responsePromise;
  await resp.finished();
  // body 消费：强制读出，保证 fetch then 链路可推进
  await resp.text();
  await expect
    .poll(() => hold.fulfilled, { timeout: 15_000 })
    .toBe(fulfilledBefore + 1);
  await waitPageContinuationBarrier(page);
}

/** 用途：列出项目任务，供引擎/error/diagnosticCode 与是否创建断言。 */
async function listTasks(
  request: APIRequestContext,
  projectId: string,
): Promise<
  Array<{
    id: string;
    type: string;
    status: string;
    error?: string | null;
    result?: { engine?: string; diagnosticCode?: string } | null;
  }>
> {
  const res = await request.get(`${API}/projects/${projectId}/tasks`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as Array<{
    id: string;
    type: string;
    status: string;
    error?: string | null;
    result?: { engine?: string; diagnosticCode?: string } | null;
  }>;
}

/**
 * 用途：等待 A 项目 managed 真实 failed，并锁定 error/result 毒化标记证据。
 * 返回已进入浏览器任务对象的真实 privacyMarkers（禁止 env/路径空证明）。
 */
async function awaitManagedFailedPrivacyMarkers(
  request: APIRequestContext,
  projectId: string,
): Promise<string[]> {
  let markers: string[] = [];
  await expect
    .poll(async () => {
      const tasks = await listTasks(request, projectId);
      const failed = tasks.find(
        (t) =>
          t.type === "parse" &&
          t.status === "failed" &&
          t.result?.engine === "managed",
      );
      if (!failed) return "pending";
      if (failed.error !== REAL_MANIFEST_TASK_ERROR) {
        return `error:${failed.error ?? "null"}`;
      }
      if (failed.result?.diagnosticCode !== RUNTIME_MANIFEST_INVALID) {
        return `code:${failed.result?.diagnosticCode ?? "null"}`;
      }
      markers = [REAL_MANIFEST_TASK_ERROR, RUNTIME_MANIFEST_INVALID];
      return "failed-managed";
    })
    .toBe("failed-managed");
  expect(markers).toEqual([REAL_MANIFEST_TASK_ERROR, RUNTIME_MANIFEST_INVALID]);
  return markers;
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

    // Q10：动作前锁 GET baseline；Q9：动作前 storage snapshot
    const storageBefore = await captureBrowserStorageSnapshot(page);
    const getBefore = countStrategyGets(net.apiHits);
    const lightBefore = countLightweightParsePosts(net.taskPosts);
    await parseActionButton(page).click();
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
    // Q10：精确 +1，禁止 >=1
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBefore + 1);
    const taskBodies = net.taskPosts.map((h) => h.postData || "");
    expect(taskBodies.some((b) => b.includes('"engine":"lightweight"'))).toBe(
      true,
    );
    await assertNoStrategyPersistence(page, storageBefore);
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

    const storageBefore = await captureBrowserStorageSnapshot(page);
    const beforeTasks = net.taskPosts.length;
    const getBefore = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();

    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    await expect(page.getByRole("heading", { name: "本地解析插件" })).toBeVisible();
    await expect(page.locator("#pid")).toHaveValue(projectId);
    expect(net.taskPosts.length).toBe(beforeTasks);
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBefore);
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

    // 首次点击前锁 task/PUT/light/managed/GET/storage 基线（打开/取消/再开不得吞入）
    const storageBefore = await captureBrowserStorageSnapshot(page);
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBeforeOpen = countStrategyGets(net.apiHits);

    // 取消 — 冻结新术语 exact（T2：禁止旧「在线轻量解析/本地 MinerU 回传」）
    await parseActionButton(page).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // 打开 dialog：策略 GET 精确 +1；同对话框内后续选择不得再 +GET（Q10）
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeOpen + 1);
    const getAfterOpen = countStrategyGets(net.apiHits);
    await expect(
      dialog.getByRole("button", { name: LABEL_LIGHT, exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: LABEL_MANAGED, exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: LABEL_LOCAL, exact: true }),
    ).toBeVisible();
    // 旧术语必须不可见（生产不得兼容旧文案）
    await expect(
      dialog.getByRole("button", { name: "在线轻量解析", exact: true }),
    ).toHaveCount(0);
    await expect(
      dialog.getByRole("button", { name: "本地 MinerU 回传", exact: true }),
    ).toHaveCount(0);
    // 打开后仍精确基线
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterOpen);

    // 再开前仍精确基线；选轻量（新一次打开再 +1 GET；对话框内选择不额外 GET）
    const getBeforeReopen = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeReopen + 1);
    const getAfterReopen = countStrategyGets(net.apiHits);
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    await dialog.getByRole("button", { name: LABEL_LIGHT, exact: true }).click();
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
    // 同对话框选择不额外 GET
    expect(countStrategyGets(net.apiHits)).toBe(getAfterReopen);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight + 1);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);

    // 选本地（冻结标签「人工本地回传」）
    await putParseStrategy(request, "ask");
    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 技术标 ask" }),
    ).toBeVisible({ timeout: 20_000 });
    const storageBeforeLocal = await captureBrowserStorageSnapshot(page);
    const beforeLocal = net.taskPosts.length;
    const putsBeforeLocal = countSettingsPuts(net.apiHits);
    const getBeforeLocal = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLocal + 1);
    const getAfterLocalOpen = countStrategyGets(net.apiHits);
    expect(net.taskPosts.length).toBe(beforeLocal);
    await dialog.getByRole("button", { name: LABEL_LOCAL, exact: true }).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(countStrategyGets(net.apiHits)).toBe(getAfterLocalOpen);
    expect(net.taskPosts.length).toBe(beforeLocal);
    expect(countSettingsPuts(net.apiHits)).toBe(putsBeforeLocal);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBeforeLocal);
    await assertNoStrategyPersistence(page, storageBefore);
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
    const storageBefore = await captureBrowserStorageSnapshot(page);
    const beforeUpload = net.taskPosts.length;
    const getBeforeLocal = countStrategyGets(net.apiHits);
    await uploadTxtViaHiddenInput(page, "e2e-biz-local.txt");
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(net.taskPosts.length).toBe(beforeUpload);
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLocal + 1);

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
    const getBeforeAsk = countStrategyGets(net.apiHits);
    await page.getByRole("button", { name: "整段重解析" }).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeAsk + 1);
    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(beforeReparse);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBefore);
  });

  test("商务标 ask：取消 + light/managed/local 独立验收，PUT 零增量", async ({
    page,
    request,
  }) => {
    /**
     * 模块：Q8 商务 ask 独立验收
     * 用途：首次动作前锁 task/PUT/engine/GET/storage 基线；
     *       取消 / light(+1 lightweight) / managed(+1 managed) / local(零任务+项目化跳转) 精确；
     *       全部 page PUT 零增量；同对话框选择不额外 GET。
     */
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

    // 商务人工入口冻结为「人工本地回传」（T2：禁止旧「本地 MinerU 插件」）
    await expect(
      page.getByRole("link", { name: LABEL_LOCAL, exact: true }),
    ).toHaveAttribute(
      "href",
      `/local-parser?projectId=${encodeURIComponent(projectId)}`,
    );
    await expect(
      page.getByRole("link", { name: "本地 MinerU 插件", exact: true }),
    ).toHaveCount(0);

    // 首次动作前锁基线
    const storageBefore = await captureBrowserStorageSnapshot(page);
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBefore = countStrategyGets(net.apiHits);

    await uploadTxtViaHiddenInput(page, "e2e-biz-ask-upload.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-biz-ask-upload.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // 上传后策略决策 GET 精确 +1
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);
    const getAfterOpen = countStrategyGets(net.apiHits);
    await expect(
      dialog.getByRole("button", { name: LABEL_LIGHT, exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: LABEL_MANAGED, exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: LABEL_LOCAL, exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "在线轻量解析", exact: true }),
    ).toHaveCount(0);
    await expect(
      dialog.getByRole("button", { name: "本地 MinerU 回传", exact: true }),
    ).toHaveCount(0);
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);

    // 取消 — 零 task / 零 PUT / 零引擎增量 / 不额外 GET
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterOpen);

    // light：整段重解析再开 → 只 +1 lightweight，PUT 零，对话框内选择不额外 GET
    const getBeforeLight = countStrategyGets(net.apiHits);
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLight + 1);
    const getAfterLightOpen = countStrategyGets(net.apiHits);
    await dialog.getByRole("button", { name: LABEL_LIGHT, exact: true }).click();
    await expect
      .poll(() => countLightweightParsePosts(net.taskPosts), { timeout: 30_000 })
      .toBe(baselineLight + 1);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterLightOpen);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(net.taskPosts.length).toBe(baselineTasks + 1);

    // managed：只 +1 managed，light 不再增，PUT 零
    const lightAfterLight = countLightweightParsePosts(net.taskPosts);
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const tasksBeforeManaged = net.taskPosts.length;
    const getBeforeManaged = countStrategyGets(net.apiHits);
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeManaged + 1);
    const getAfterManagedOpen = countStrategyGets(net.apiHits);
    await dialog.getByRole("button", { name: LABEL_MANAGED, exact: true }).click();
    await expect
      .poll(() => countEngineParsePosts(net.taskPosts, "managed"), {
        timeout: 20_000,
      })
      .toBe(managedBefore + 1);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterManagedOpen);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightAfterLight);
    expect(net.taskPosts.length).toBe(tasksBeforeManaged + 1);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(
      parseTaskPostBody(net.taskPosts[net.taskPosts.length - 1].postData),
    ).toEqual({ type: "parse", payload: { engine: "managed" } });

    // local：零任务并项目化跳转；PUT 零
    const tasksBeforeLocal = net.taskPosts.length;
    const getBeforeLocalChoice = countStrategyGets(net.apiHits);
    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标 ask 上传" }),
    ).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    // goto 后可能无额外策略 GET；以本轮打开精确 +1
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLocalChoice + 1);
    const getAfterLocalOpen = countStrategyGets(net.apiHits);
    await dialog.getByRole("button", { name: LABEL_LOCAL, exact: true }).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(projectId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(countStrategyGets(net.apiHits)).toBe(getAfterLocalOpen);
    expect(net.taskPosts.length).toBe(tasksBeforeLocal);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBefore);
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

    const storageBefore = await captureBrowserStorageSnapshot(page);
    const before = net.taskPosts.length;
    const getBefore = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(SENSITIVE_LEAK)).toHaveCount(0);
    expect(net.taskPosts.length).toBe(before);
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBefore);
  });
});

/**
 * M3：managed 策略接线 failure-first
 * 生产未改时须真实业务红；禁止 skip/xfail、宽泛 or、只查文案、light 冒充 managed。
 */
test.describe("P8B M3 managed 解析策略接线", () => {
  test("M3 设置页：精确四项 value/text 且只四项，managed 可保存", async ({
    page,
    request,
  }) => {
    /**
     * 模块：Q4 设置页 select 精确四项
     * 用途：value/text 锁 light=轻量解析、managed=本机自动 OCR、
     *       local=人工本地回传、ask=每次询问；旧术语/额外项为零。
     */
    // 基线先写 light，证明后续保存真写入 managed
    await putParseStrategy(request, "light");
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "解析策略" })).toBeVisible({
      timeout: 20_000,
    });

    const select = page.locator("#parse");
    await expect(select).toBeVisible();
    const options = select.locator("option");
    // 精确四项且只四项
    await expect(options).toHaveCount(4);

    const expected: Array<{ value: string; text: string }> = [
      { value: "light", text: LABEL_LIGHT },
      { value: "managed", text: LABEL_MANAGED },
      { value: "local", text: LABEL_LOCAL },
      { value: "ask", text: "每次询问" },
    ];
    for (const item of expected) {
      const opt = select.locator(`option[value="${item.value}"]`);
      await expect(opt).toHaveCount(1);
      await expect(opt).toHaveText(item.text);
    }
    // 旧术语 / 额外项为零
    await expect(select.locator('option[value="mineru"]')).toHaveCount(0);
    await expect(select.locator('option[value="docling"]')).toHaveCount(0);
    await expect(
      select.locator("option", { hasText: "在线轻量解析" }),
    ).toHaveCount(0);
    await expect(
      select.locator("option", { hasText: "优先本地 MinerU 插件" }),
    ).toHaveCount(0);
    await expect(
      select.locator("option", { hasText: "本地 MinerU" }),
    ).toHaveCount(0);

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

    const storageBefore = await captureBrowserStorageSnapshot(page);
    const beforeAll = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    const getBefore = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();

    await expect
      .poll(() => countEngineParsePosts(net.taskPosts, "managed"), {
        timeout: 20_000,
      })
      .toBe(1);
    expect(net.taskPosts.length).toBe(beforeAll + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);

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
    await assertNoStrategyPersistence(page, storageBefore);
  });

  test("M3 商务标 managed：真实 failed 终态后精确一次 managed POST，零 lightweight", async ({
    page,
    request,
  }) => {
    /**
     * 模块：Q7 商务 managed 真实失败 + 全过程隐私探针
     * 用途：上传前安装同一 privacy probe；终态由真实 task 证明 error/code 在数据链；
     *       再断言 hit=[]、callback>0。
     */
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

    const storageBefore = await captureBrowserStorageSnapshot(page);
    const beforeAll = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    const getBefore = countStrategyGets(net.apiHits);
    // Q7：上传前安装探针（标记为契约固定毒化串，终态由真实 task 证明进链）
    await installDomPrivacyProbe(page, [
      REAL_MANIFEST_TASK_ERROR,
      RUNTIME_MANIFEST_INVALID,
    ]);
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
    // 终态由真实 task 证明 error/code marker 确实在数据链
    const privacyMarkers = await awaitManagedFailedPrivacyMarkers(
      request,
      projectId,
    );
    expect(privacyMarkers).toEqual([
      REAL_MANIFEST_TASK_ERROR,
      RUNTIME_MANIFEST_INVALID,
    ]);
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);
    await expect(page.getByText(RUNTIME_MANIFEST_INVALID)).toHaveCount(0);
    await expect(page.getByText(/diagnosticCode/i)).toHaveCount(0);

    const probe = await readDomPrivacyProbe(page);
    expect(probe.callbackCount).toBeGreaterThan(0);
    expect(probe.hitMarkers).toEqual([]);

    // 终态后再锁计数（防异步补发 light 逃逸）
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(1);
    expect(net.taskPosts.length).toBe(beforeAll + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);
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
    await assertNoStrategyPersistence(page, storageBefore);
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

    // 首次点击前锁 task/PUT/light/managed/GET/storage 基线
    const storageBefore = await captureBrowserStorageSnapshot(page);
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBeforeOpen = countStrategyGets(net.apiHits);

    await parseActionButton(page).click();
    const dialog = page.getByRole("dialog", { name: "选择解析方式" });
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeOpen + 1);
    const getAfterOpen = countStrategyGets(net.apiHits);

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
    // 打开后仍精确基线
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);

    await cancelBtn.click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterOpen);

    // 再开前仍精确基线；选 managed 后只允许 managed +1，PUT/light 不变；同对话框选择不额外 GET
    const getBeforeReopen = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeReopen + 1);
    const getAfterReopen = countStrategyGets(net.apiHits);
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    await dialog
      .getByRole("button", { name: "本机自动 OCR", exact: true })
      .click();

    // U1：先等待 MANAGED_FAIL_MSG 与 listTasks 精确 failed-managed
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    await awaitManagedFailedPrivacyMarkers(request, projectId);

    // 终态后再锁：page PUT 不变、managed 精确 +1、light 不变、对话框内选择不额外 GET
    expect(countStrategyGets(net.apiHits)).toBe(getAfterReopen);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(
      baselineManaged + 1,
    );
    expect(net.taskPosts.length).toBe(baselineTasks + 1);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
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
    await assertNoStrategyPersistence(page, storageBefore);
  });

  test("M3 managed 空 manifest 真实失败：固定中文+项目化人工入口，零诊断泄漏", async ({
    page,
    request,
  }) => {
    /**
     * 模块：Q7 技术 managed 真实失败 + 全过程隐私探针
     * 用途：点击前安装同一 privacy probe；终态由真实 task 证明 marker 在数据链；
     *       hit=[] 且 callback>0。
     */
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

    const storageBefore = await captureBrowserStorageSnapshot(page);
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    const getBefore = countStrategyGets(net.apiHits);
    // Q7：点击前安装探针
    await installDomPrivacyProbe(page, [
      REAL_MANIFEST_TASK_ERROR,
      RUNTIME_MANIFEST_INVALID,
    ]);
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

    // 原 task.error 与 diagnosticCode 已进入任务对象，但 UI 不得可见
    const privacyMarkers = await awaitManagedFailedPrivacyMarkers(
      request,
      projectId,
    );
    expect(privacyMarkers).toEqual([
      REAL_MANIFEST_TASK_ERROR,
      RUNTIME_MANIFEST_INVALID,
    ]);
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);
    await expect(page.getByText(RUNTIME_MANIFEST_INVALID)).toHaveCount(0);
    await expect(page.getByText(/diagnosticCode/i)).toHaveCount(0);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    expect(countStrategyGets(net.apiHits)).toBe(getBefore + 1);

    const probe = await readDomPrivacyProbe(page);
    expect(probe.callbackCount).toBeGreaterThan(0);
    expect(probe.hitMarkers).toEqual([]);

    // 精确一次 managed POST
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(1);
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBefore);

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
    const storageBeforeLocal = await captureBrowserStorageSnapshot(page);
    const beforeLocal = net.taskPosts.length;
    const getBeforeLocal = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/local-parser\\?projectId=${encodeURIComponent(localId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`,
      ),
      { timeout: 15_000 },
    );
    expect(net.taskPosts.length).toBe(beforeLocal);
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLocal + 1);
    await assertNoStrategyPersistence(page, storageBeforeLocal);

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
    const storageBeforeLight = await captureBrowserStorageSnapshot(page);
    const lightBefore = countEngineParsePosts(net.taskPosts, "lightweight");
    const getBeforeLight = countStrategyGets(net.apiHits);
    await parseActionButton(page).click();
    await expect
      .poll(() => countEngineParsePosts(net.taskPosts, "lightweight"), {
        timeout: 30_000,
      })
      .toBe(lightBefore + 1);
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLight + 1);
    expect(
      parseTaskPostBody(net.taskPosts[net.taskPosts.length - 1].postData),
    ).toEqual({ type: "parse", payload: { engine: "lightweight" } });
    expect(net.externalHits).toEqual([]);
    await assertNoStrategyPersistence(page, storageBeforeLight);
  });

  test("T3 技术 A→B→A：策略 GET 在途 HoldGate 释放后 task POST 零增量、B 零污染", async ({
    page,
    request,
  }) => {
    /**
     * 模块：T3 策略 GET 在途软切（Q2）
     * 用途：A 点击开始解析时 HoldGate 挂起 parse-strategy GET；软切 A→B→A 后释放旧 GET；
     *       response finished + body 消费 + continuation barrier 后 task POST 零增量；
     *       禁止把 route.fulfilled 单独当页面完成；禁止固定时间完成门。
     */
    const idA = await createProject(
      request,
      "technical",
      "E2E M3 T3 技术甲",
    );
    const idB = await createProject(
      request,
      "technical",
      "E2E M3 T3 技术乙",
    );
    const net = await installNetworkGuard(page);
    const gate = createHoldGate();
    const hold = await installParseStrategyGetHold(page, gate, {
      strategy: "managed",
    });

    await page.goto(`/technical-plan/${idA}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 技术甲" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-t3-tech-a.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t3-tech-a.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const beforePosts = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    const beforeManaged = countEngineParsePosts(net.taskPosts, "managed");
    const heldBefore = hold.held;
    const fulfilledBefore = hold.fulfilled;

    // 触发策略读取；GET 进入 HoldGate（在途）
    await parseActionButton(page).click();
    await expect.poll(() => hold.held, { timeout: 15_000 }).toBe(heldBefore + 1);
    await expect.poll(() => gate.waiterCount(), { timeout: 10_000 }).toBe(1);
    // 在途：不得已创建 task；held 精确 +1，fulfilled 仍基线
    expect(net.taskPosts.length).toBe(beforePosts);
    expect(hold.fulfilled).toBe(fulfilledBefore);

    // 真实软切 A→B（禁止 page.goto）
    await softNavigate(page, `/technical-plan/${idB}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 技术乙" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("dialog", { name: "选择解析方式" })).toHaveCount(
      0,
    );
    await expect(page.getByText("正在读取解析策略")).toHaveCount(0);

    // 再软切回 A，再释放旧 GET（迟到响应）
    await softNavigate(page, `/technical-plan/${idA}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 技术甲" }),
    ).toBeVisible({ timeout: 20_000 });

    // Q2：release 后等 finished + body 消费 + continuation barrier，再锁零 POST
    await releaseAndAwaitHeldStrategyContinuation(
      page,
      gate,
      hold,
      fulfilledBefore,
    );
    await expect(page.getByText("正在读取解析策略")).toHaveCount(0);
    await expect(parseActionButton(page)).toBeEnabled({ timeout: 10_000 });

    expect(hold.held).toBe(heldBefore + 1);
    expect(net.taskPosts.length).toBe(beforePosts);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(beforeManaged);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    await expect(page.getByRole("dialog", { name: "选择解析方式" })).toHaveCount(
      0,
    );
    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 技术甲" }),
    ).toBeVisible();
    expect(net.externalHits).toEqual([]);
  });

  test("T3 商务 A→B：策略 GET 在途 HoldGate 释放后 task POST 零增量、B 零污染", async ({
    page,
    request,
  }) => {
    /**
     * 模块：T3 商务策略 GET 在途软切（Q2+Q3）
     * 用途：API multipart 预置 A source；整段重解析 Hold 策略 GET；软切无文件 B 后释放；
     *       response finished+body+continuation 后 POST 零增量；
     *       B 证明 A 文件名零入、尚未上传可见、整段重解析 disabled（禁止期待 enabled）。
     */
    const idA = await createProject(
      request,
      "business",
      "E2E M3 T3 商务甲",
    );
    const idB = await createProject(
      request,
      "business",
      "E2E M3 T3 商务乙",
    );
    // 真实 API multipart 预置 A 的 source（禁止 UI 上传自动 parse 作 seed）
    await seedProjectSourceFileViaApi(request, idA, "e2e-t3-biz-a.txt");

    const net = await installNetworkGuard(page);
    const gate = createHoldGate();
    const hold = await installParseStrategyGetHold(page, gate, {
      strategy: "managed",
    });

    await page.goto(`/business-bid/${idA}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 商务甲" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t3-biz-a.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    const beforePosts = net.taskPosts.length;
    const beforeLight = countLightweightParsePosts(net.taskPosts);
    const beforeManaged = countEngineParsePosts(net.taskPosts, "managed");
    const heldBefore = hold.held;
    const fulfilledBefore = hold.fulfilled;

    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect.poll(() => hold.held, { timeout: 15_000 }).toBe(heldBefore + 1);
    await expect.poll(() => gate.waiterCount(), { timeout: 10_000 }).toBe(1);
    expect(net.taskPosts.length).toBe(beforePosts);
    expect(hold.fulfilled).toBe(fulfilledBefore);

    await softNavigate(page, `/business-bid/${idB}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 商务乙" }),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("dialog", { name: "选择解析方式" })).toHaveCount(
      0,
    );
    await expect(page.getByText("正在读取解析策略")).toHaveCount(0);
    // Q3：B 无文件 — A 文件名零入、尚未上传可见、整段重解析 disabled
    await expect(page.getByText("尚未上传")).toBeVisible();
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t3-biz-a.txt" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "整段重解析", exact: true }),
    ).toBeDisabled();

    // Q2：不得把 route.fulfilled 当页面完成
    await releaseAndAwaitHeldStrategyContinuation(
      page,
      gate,
      hold,
      fulfilledBefore,
    );
    await expect(page.getByText("正在读取解析策略")).toHaveCount(0);
    // 仍在 B：无文件 → 整段重解析保持 disabled（禁止 toBeEnabled）
    await expect(
      page.getByRole("button", { name: "整段重解析", exact: true }),
    ).toBeDisabled();
    await expect(page.getByText("尚未上传")).toBeVisible();
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t3-biz-a.txt" }),
    ).toHaveCount(0);

    expect(hold.held).toBe(heldBefore + 1);
    expect(net.taskPosts.length).toBe(beforePosts);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(beforeManaged);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(beforeLight);
    await expect(page.getByRole("dialog", { name: "选择解析方式" })).toHaveCount(
      0,
    );
    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T3 商务乙" }),
    ).toBeVisible();
    expect(net.externalHits).toEqual([]);
  });

  test("T4 商务 A managed failed 软切 B：MutationObserver 全过程旧 error 零入 DOM", async ({
    page,
    request,
  }) => {
    /**
     * 模块：T4 商务软切首帧隐私门
     * 用途：A managed 真实 failed 后，唯一 privacy helper 全过程证明已进入
     *       任务对象的 error/diagnosticCode 标记从未进入 B DOM。
     */
    const idA = await createProject(
      request,
      "business",
      "E2E M3 T4 商务甲隐私",
    );
    const idB = await createProject(
      request,
      "business",
      "E2E M3 T4 商务乙干净",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/business-bid/${idA}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T4 商务甲隐私" }),
    ).toBeVisible({ timeout: 20_000 });

    await uploadTxtViaHiddenInput(page, "e2e-t4-biz-a.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t4-biz-a.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    // 仅使用已证明进入浏览器任务对象的真实标记
    const privacyMarkers = await awaitManagedFailedPrivacyMarkers(
      request,
      idA,
    );

    // 安装唯一 DOM 探针 → 软切 B（禁止内联第二份 observer）
    await installDomPrivacyProbe(page, privacyMarkers);
    await softNavigate(page, `/business-bid/${idB}/parse`);

    await expect(
      page.getByRole("heading", { name: "E2E M3 T4 商务乙干净" }),
    ).toBeVisible({ timeout: 20_000 });

    // 终态仍锁：B 无 managed 失败门/人工入口/原 error
    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toHaveCount(0);
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);
    await expect(page.getByText(RUNTIME_MANIFEST_INVALID)).toHaveCount(0);

    const probe = await readDomPrivacyProbe(page);
    // observer 必须真实触发；毒化标记全过程零命中
    expect(probe.callbackCount).toBeGreaterThan(0);
    expect(
      probe.hitMarkers,
      `B DOM 全过程命中隐私标记: ${probe.hitMarkers.join(",")}`,
    ).toEqual([]);
    expect(net.externalHits).toEqual([]);
  });

  test("T5 技术 managed failed 后策略 GET 500：STRATEGY_FAIL_MSG 唯一、旧门隐藏、POST 零增量", async ({
    page,
    request,
  }) => {
    /**
     * 模块：T5 技术失败后策略再读 500 覆盖
     * 用途：managed 真实 failed 后注入 parse-strategy 500；handler 精确 1 次；
     *       策略 GET baseline+1；SENSITIVE_LEAK 全过程零入 DOM；固定错误可见即完成。
     */
    const projectId = await createProject(
      request,
      "technical",
      "E2E M3 T5 技术覆盖",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T5 技术覆盖" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-t5-tech.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t5-tech.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await parseActionButton(page).click();
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toBeVisible();
    await awaitManagedFailedPrivacyMarkers(request, projectId);

    const postsBefore = net.taskPosts.length;
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const lightBefore = countLightweightParsePosts(net.taskPosts);
    const strategyGetBaseline = countStrategyGets(net.apiHits);

    // 移除 managed 注入，改为 500 handler；自身精确调用一次
    await page.unroute("**/api/settings/parse-strategy");
    let failHandlerCalls = 0;
    await page.route("**/api/settings/parse-strategy", async (route) => {
      if (route.request().method().toUpperCase() !== "GET") {
        await route.continue();
        return;
      }
      failHandlerCalls += 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          detail: { code: "internal_error", message: SENSITIVE_LEAK },
        }),
      });
    });

    // 点击前安装同一 record 探针，证明 SENSITIVE_LEAK 全过程零入 DOM
    await installDomPrivacyProbe(page, [SENSITIVE_LEAK]);
    await parseActionButton(page).click();
    // 固定错误可见即为 handler 完成信号
    await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
      timeout: 15_000,
    });
    expect(failHandlerCalls).toBe(1);
    expect(countStrategyGets(net.apiHits)).toBe(strategyGetBaseline + 1);

    // 旧 managed 门与人工入口必须隐藏
    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toHaveCount(0);
    await expect(page.getByText(SENSITIVE_LEAK)).toHaveCount(0);
    await expect(page.getByText(REAL_MANIFEST_TASK_ERROR)).toHaveCount(0);

    const probe = await readDomPrivacyProbe(page);
    expect(probe.callbackCount).toBeGreaterThan(0);
    expect(probe.hitMarkers).toEqual([]);
    expect(net.taskPosts.length).toBe(postsBefore);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(managedBefore);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBefore);
    expect(net.externalHits).toEqual([]);
  });

  test("T5 商务 managed failed 后策略 GET 500：STRATEGY_FAIL_MSG 唯一、旧门隐藏、POST 零增量", async ({
    page,
    request,
  }) => {
    /**
     * 模块：T5 商务失败后策略再读 500 覆盖
     * 用途：与技术页同风险；商务整段重解析路径；handler/GET 精确 +1 + 探针。
     */
    const projectId = await createProject(
      request,
      "business",
      "E2E M3 T5 商务覆盖",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T5 商务覆盖" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-t5-biz.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t5-biz.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toBeVisible();
    await awaitManagedFailedPrivacyMarkers(request, projectId);

    const postsBefore = net.taskPosts.length;
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const lightBefore = countLightweightParsePosts(net.taskPosts);
    const strategyGetBaseline = countStrategyGets(net.apiHits);

    await page.unroute("**/api/settings/parse-strategy");
    let failHandlerCalls = 0;
    await page.route("**/api/settings/parse-strategy", async (route) => {
      if (route.request().method().toUpperCase() !== "GET") {
        await route.continue();
        return;
      }
      failHandlerCalls += 1;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({
          detail: { code: "internal_error", message: SENSITIVE_LEAK },
        }),
      });
    });

    await installDomPrivacyProbe(page, [SENSITIVE_LEAK]);
    // 商务：整段重解析触发下一次策略 GET
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
      timeout: 15_000,
    });
    expect(failHandlerCalls).toBe(1);
    expect(countStrategyGets(net.apiHits)).toBe(strategyGetBaseline + 1);

    await expect(page.getByText(MANAGED_FAIL_MSG)).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: MANAGED_FAIL_LINK }),
    ).toHaveCount(0);
    await expect(page.getByText(SENSITIVE_LEAK)).toHaveCount(0);

    const probe = await readDomPrivacyProbe(page);
    expect(probe.callbackCount).toBeGreaterThan(0);
    expect(probe.hitMarkers).toEqual([]);
    expect(net.taskPosts.length).toBe(postsBefore);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(managedBefore);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBefore);
    expect(net.externalHits).toEqual([]);
  });
});

/**
 * Q9：纯 helper 自检（无浏览器、无业务路由）。
 * 证明：(a) 空 key 策略写入被捕获；(b) settings.v1 动作后改写失败；
 *       (c) 动作前已存在且动作后字节相同的合法设置兜底不误报。
 */
test.describe("Q9 存储探针纯 helper 自检", () => {
  test("空 key 捕获、settings.v1 改写失败、合法兜底不误报", () => {
    // (a) 空 key：capture 规则无条件记录；策略 value 必失败
    const emptyKeyBag = dumpStorageEntriesLikeCapture([
      ["", '{"parseStrategy":"light"}'],
    ]);
    expect(Object.prototype.hasOwnProperty.call(emptyKeyBag, "")).toBe(true);
    expect(emptyKeyBag[""]).toBe('{"parseStrategy":"light"}');
    // 旧 if (k) 会漏空 key — 对照证明 capture 规则差异
    const oldSkipEmpty: Record<string, string> = {};
    for (const [rawK, rawV] of [["", '{"parseStrategy":"light"}']] as const) {
      const k = rawK;
      if (k) oldSkipEmpty[k] = rawV;
    }
    expect(Object.prototype.hasOwnProperty.call(oldSkipEmpty, "")).toBe(false);

    const emptyKeySnap: BrowserStorageSnapshot = {
      localStorage: emptyKeyBag,
      sessionStorage: {},
    };
    const emptyBefore: BrowserStorageSnapshot = {
      localStorage: {},
      sessionStorage: {},
    };
    expect(() =>
      assertStorageNoStrategyLeak(emptyKeySnap, emptyBefore),
    ).toThrow();
    expect(() => assertStorageNoStrategyLeak(emptyKeySnap)).toThrow();

    // (b) settings.v1 含策略字段：动作后改写必须失败（leak 检查 + 深比较）
    const settingsBeforeVal = JSON.stringify({
      parseStrategy: "light",
      theme: "dark",
    });
    const settingsAfterVal = JSON.stringify({
      parseStrategy: "managed",
      theme: "dark",
    });
    const beforeSettings: BrowserStorageSnapshot = {
      localStorage: { [SETTINGS_V1_KEY]: settingsBeforeVal },
      sessionStorage: {},
    };
    const afterRewrite: BrowserStorageSnapshot = {
      localStorage: { [SETTINGS_V1_KEY]: settingsAfterVal },
      sessionStorage: {},
    };
    expect(() =>
      assertStorageNoStrategyLeak(afterRewrite, beforeSettings),
    ).toThrow();
    expect(() =>
      assertStorageUnchangedByParseAction(beforeSettings, afterRewrite),
    ).toThrow();
    // 新增含策略的 settings.v1 也失败
    expect(() =>
      assertStorageNoStrategyLeak(afterRewrite, {
        localStorage: {},
        sessionStorage: {},
      }),
    ).toThrow();
    // 删除含策略的 settings.v1 失败
    expect(() =>
      assertStorageNoStrategyLeak(
        { localStorage: {}, sessionStorage: {} },
        beforeSettings,
      ),
    ).toThrow();

    // (c) 动作前已存在且动作后完全相同的合法设置兜底：不误报
    const legitSame: BrowserStorageSnapshot = {
      localStorage: {
        [SETTINGS_V1_KEY]: settingsBeforeVal,
        "biaoshu.ui.prefs": '{"sidebar":true}',
        "": "non-strategy-empty-key",
      },
      sessionStorage: { tab: "doc" },
    };
    assertStorageNoStrategyLeak(legitSame, legitSame);
    assertStorageUnchangedByParseAction(legitSame, legitSame);
    // 非 settings.v1 策略命名 key 仍禁止
    expect(() =>
      assertStorageNoStrategyLeak({
        localStorage: { "parse-strategy": "light" },
        sessionStorage: {},
      }),
    ).toThrow();
  });
});
