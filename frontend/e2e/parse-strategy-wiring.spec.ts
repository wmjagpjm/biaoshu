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
 * 用途：唯一 DOM 隐私探针 — 统一 scanNode/scanCurrent 覆盖文本、全部 attributes、
 *       input/textarea/select 当前 value；Observer 含 attributes + attributeOldValue；
 *       先包裹已有控件 own value descriptor（React 风格），再 patch 原型覆盖新控件；
 *       read 前 process takeRecords。
 * 对接：T4/T5/Q7 全过程零泄漏；callbackCount 仅由 observer 真回调增加，手工 scan 不计。
 */
async function installDomPrivacyProbe(
  page: Page,
  markers: string[],
): Promise<void> {
  await page.evaluate((ms) => {
    type ValueProto = {
      prototype: {
        value?: PropertyDescriptor | string;
      };
    };
    type FormValueEl =
      | HTMLInputElement
      | HTMLTextAreaElement
      | HTMLSelectElement;
    type Probe = {
      callbackCount: number;
      hitMarkers: string[];
      observer: MutationObserver | null;
      restoreValueSetters: Array<() => void>;
      processRecords: (records: MutationRecord[]) => void;
      scanCurrent: () => void;
    };
    type ProbeWin = Window & { __biaoshuPrivacyProbe?: Probe };

    const w = window as unknown as ProbeWin;
    // 重复安装须先清理旧探针（observer + value setter）
    const prev = w.__biaoshuPrivacyProbe;
    if (prev) {
      if (prev.observer) {
        prev.observer.disconnect();
        prev.observer = null;
      }
      for (const restore of prev.restoreValueSetters) {
        try {
          restore();
        } catch {
          /* 忽略恢复失败 */
        }
      }
      prev.restoreValueSetters = [];
      w.__biaoshuPrivacyProbe = undefined;
    }

    const probe: Probe = {
      callbackCount: 0,
      hitMarkers: [],
      observer: null,
      restoreValueSetters: [],
      processRecords: () => {},
      scanCurrent: () => {},
    };

    const noteHits = (text: string) => {
      if (!text) return;
      for (const m of ms) {
        if (m && text.includes(m) && !probe.hitMarkers.includes(m)) {
          probe.hitMarkers.push(m);
        }
      }
    };

    /** 统一节点扫描：文本 + 元素全部 attributes + form 当前 value */
    const scanNode = (root: Node | null) => {
      if (!root) return;
      if (root.nodeType === Node.TEXT_NODE) {
        noteHits(root.textContent || "");
        return;
      }
      if (root.nodeType !== Node.ELEMENT_NODE) return;
      const el = root as Element;
      noteHits(el.textContent || "");
      for (const attr of Array.from(el.attributes)) {
        noteHits(attr.name);
        noteHits(attr.value);
      }
      if (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement
      ) {
        noteHits(el.value);
      }
      for (const child of Array.from(el.childNodes)) {
        scanNode(child);
      }
    };

    const scanCurrent = () => {
      scanNode(document.documentElement);
      // 再扫一遍表单控件当前 value（含未挂在主树的边角）
      for (const el of Array.from(
        document.querySelectorAll("input, textarea, select"),
      )) {
        if (
          el instanceof HTMLInputElement ||
          el instanceof HTMLTextAreaElement ||
          el instanceof HTMLSelectElement
        ) {
          noteHits(el.value);
        }
      }
    };
    probe.scanCurrent = scanCurrent;

    const processRecords = (records: MutationRecord[]) => {
      for (const rec of records) {
        if (rec.type === "attributes" && rec.target) {
          if (typeof rec.oldValue === "string") {
            noteHits(rec.oldValue);
          }
          const el = rec.target as Element;
          if (rec.attributeName) {
            const cur = el.getAttribute?.(rec.attributeName);
            if (typeof cur === "string") noteHits(cur);
          }
          scanNode(el);
        }
        if (rec.type === "characterData" && rec.target) {
          noteHits(rec.target.textContent || "");
          if (typeof rec.oldValue === "string") {
            noteHits(rec.oldValue);
          }
        }
        for (const n of Array.from(rec.addedNodes)) {
          scanNode(n);
        }
        for (const n of Array.from(rec.removedNodes)) {
          scanNode(n);
        }
      }
      scanCurrent();
    };
    probe.processRecords = processRecords;

    const isFormValueEl = (el: Element): el is FormValueEl =>
      el instanceof HTMLInputElement ||
      el instanceof HTMLTextAreaElement ||
      el instanceof HTMLSelectElement;

    /**
     * D3：包裹已有控件 own value descriptor（React 在实例上 defineProperty，
     * 缓存安装前的原生/既有 setter；仅 patch 原型无法捕获此类 marker→safe 瞬态）。
     * 保留原 get/set 语义，restore 可完整还原 own descriptor。
     */
    const wrapExistingOwnValueDescriptors = () => {
      for (const node of Array.from(
        document.querySelectorAll("input, textarea, select"),
      )) {
        if (!isFormValueEl(node)) continue;
        const own = Object.getOwnPropertyDescriptor(node, "value");
        if (!own || typeof own.set !== "function") continue;
        const originalGet = own.get;
        const originalSet = own.set;
        Object.defineProperty(node, "value", {
          configurable: true,
          enumerable: own.enumerable,
          get: originalGet
            ? function (this: FormValueEl) {
                return originalGet.call(this);
              }
            : undefined,
          set: function (this: FormValueEl, next: string) {
            noteHits(String(next ?? ""));
            return originalSet.call(this, next);
          },
        });
        probe.restoreValueSetters.push(() => {
          Object.defineProperty(node, "value", own);
        });
      }
    };
    wrapExistingOwnValueDescriptors();

    // 原型路径：覆盖安装后新建控件；React 后续挂载时会缓存本 patched setter
    const patchValueSetter = (Ctor: ValueProto) => {
      const desc = Object.getOwnPropertyDescriptor(Ctor.prototype, "value");
      if (!desc || typeof desc.set !== "function" || typeof desc.get !== "function") {
        return;
      }
      const originalGet = desc.get;
      const originalSet = desc.set;
      Object.defineProperty(Ctor.prototype, "value", {
        configurable: true,
        enumerable: desc.enumerable,
        get: function (this: HTMLInputElement) {
          return originalGet.call(this);
        },
        set: function (this: HTMLInputElement, next: string) {
          noteHits(String(next ?? ""));
          return originalSet.call(this, next);
        },
      });
      probe.restoreValueSetters.push(() => {
        Object.defineProperty(Ctor.prototype, "value", desc);
      });
    };
    patchValueSetter(HTMLInputElement as unknown as ValueProto);
    patchValueSetter(HTMLTextAreaElement as unknown as ValueProto);
    patchValueSetter(HTMLSelectElement as unknown as ValueProto);

    // 安装时扫当前 DOM，不增加 callbackCount
    scanCurrent();

    const obs = new MutationObserver((records) => {
      // callbackCount 只由 Observer 真回调增加
      probe.callbackCount += 1;
      processRecords(records);
    });
    obs.observe(document.documentElement, {
      childList: true,
      subtree: true,
      characterData: true,
      characterDataOldValue: true,
      attributes: true,
      attributeOldValue: true,
    });
    probe.observer = obs;
    w.__biaoshuPrivacyProbe = probe;
  }, markers);
}

/** 用途：读取隐私探针 — takeRecords → 扫 current → disconnect + 恢复 setter。 */
async function readDomPrivacyProbe(page: Page): Promise<{
  callbackCount: number;
  hitMarkers: string[];
}> {
  return page.evaluate(() => {
    type Probe = {
      callbackCount: number;
      hitMarkers: string[];
      observer: MutationObserver | null;
      restoreValueSetters: Array<() => void>;
      processRecords: (records: MutationRecord[]) => void;
      scanCurrent: () => void;
    };
    const w = window as unknown as { __biaoshuPrivacyProbe?: Probe };
    const probe = w.__biaoshuPrivacyProbe;
    if (probe?.observer) {
      // read 前先 process 未投递 records（不虚增 callbackCount，保留真触发门）
      const pending = probe.observer.takeRecords();
      if (pending.length > 0) {
        probe.processRecords(pending);
      }
      // 再扫描 current DOM/form values
      probe.scanCurrent();
      probe.observer.disconnect();
      probe.observer = null;
    } else if (probe) {
      probe.scanCurrent();
    }
    if (probe?.restoreValueSetters?.length) {
      for (const restore of probe.restoreValueSetters) {
        try {
          restore();
        } catch {
          /* 忽略 */
        }
      }
      probe.restoreValueSetters = [];
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

/** C2：同步 Storage 操作台账条目（setItem 写同值也记；clear 必记）。 */
type StorageOpRecord = {
  area: "localStorage" | "sessionStorage" | "other";
  method: "setItem" | "removeItem" | "clear";
  key: string | null;
  value: string | null;
};

/**
 * 用途：判断单条 Storage 操作是否策略违规。
 * 规则：clear 一律违规；SETTINGS_V1_KEY 一律违规；策略命名 key / 策略 value 违规。
 * 对接：C2 操作台账；合法无关 key 写不误报。
 */
function isViolatingStorageOp(op: StorageOpRecord): boolean {
  if (op.method === "clear") return true;
  if (op.key === SETTINGS_V1_KEY) return true;
  if (op.key != null && isStrategyNamedKey(op.key)) return true;
  if (op.value != null && valueHasStrategyContent(op.value)) return true;
  return false;
}

/** 用途：从 baseline 下标起切片操作台账。 */
function storageOpsSince(
  ops: StorageOpRecord[],
  baseline: number,
): StorageOpRecord[] {
  return ops.slice(baseline);
}

/**
 * 用途：断言操作台账增量内无策略相关 setItem/removeItem/clear。
 * 对接：C2 各 ask 分支即时断言。
 */
function assertNoStrategyStorageOps(
  ops: StorageOpRecord[],
  label: string,
): void {
  const bad = ops.filter(isViolatingStorageOp);
  expect(bad, `${label} 禁止策略相关 Storage 操作: ${JSON.stringify(bad)}`).toEqual(
    [],
  );
}

/** C2/D2：Storage 分支 baseline — 含不可伪造为 [] 的 document/ledger generation。 */
type StorageBranchBaseline = {
  opsBaseline: number;
  generation: number;
  snapshot: BrowserStorageSnapshot;
};

/** C2/D2：台账读取元数据；缺失时 present=false，禁止把 [] 当零操作。 */
type StorageOpsLedgerMeta = {
  present: boolean;
  generation: number | null;
  ops: StorageOpRecord[];
};

/**
 * 用途：安装同步 Storage 操作台账（patch Storage.prototype，SPA navigate 跨路由保留）。
 * 对接：C2/D2；重复安装清空数组并 bump generation；完整刷新后 document 丢失须重装。
 */
async function installStorageOpsLedger(page: Page): Promise<void> {
  await page.evaluate(() => {
    type OpsWin = Window & {
      __biaoshuStorageOps?: Array<{
        area: "localStorage" | "sessionStorage" | "other";
        method: "setItem" | "removeItem" | "clear";
        key: string | null;
        value: string | null;
      }>;
      __biaoshuStorageOpsPatched?: boolean;
      /** document 级 generation：reload 丢失；reinstall bump；不可用空数组冒充同台账 */
      __biaoshuStorageOpsGeneration?: number;
    };
    const w = window as unknown as OpsWin;
    if (!w.__biaoshuStorageOps) {
      w.__biaoshuStorageOps = [];
    } else {
      // 重复安装：清空台账，保留 patch
      w.__biaoshuStorageOps.length = 0;
    }
    // D2：每次 install 递增 generation（新 document 上从 1 起）
    w.__biaoshuStorageOpsGeneration =
      (typeof w.__biaoshuStorageOpsGeneration === "number"
        ? w.__biaoshuStorageOpsGeneration
        : 0) + 1;
    if (w.__biaoshuStorageOpsPatched) return;

    const resolveArea = (
      storage: Storage,
    ): "localStorage" | "sessionStorage" | "other" => {
      try {
        if (storage === window.localStorage) return "localStorage";
        if (storage === window.sessionStorage) return "sessionStorage";
      } catch {
        /* 跨上下文比较失败 */
      }
      return "other";
    };

    const proto = Storage.prototype;
    const origSetItem = proto.setItem;
    const origRemoveItem = proto.removeItem;
    const origClear = proto.clear;

    proto.setItem = function (this: Storage, key: string, value: string) {
      w.__biaoshuStorageOps!.push({
        area: resolveArea(this),
        method: "setItem",
        key: String(key),
        value: String(value),
      });
      return origSetItem.call(this, key, value);
    };
    proto.removeItem = function (this: Storage, key: string) {
      w.__biaoshuStorageOps!.push({
        area: resolveArea(this),
        method: "removeItem",
        key: String(key),
        value: null,
      });
      return origRemoveItem.call(this, key);
    };
    proto.clear = function (this: Storage) {
      w.__biaoshuStorageOps!.push({
        area: resolveArea(this),
        method: "clear",
        key: null,
        value: null,
      });
      return origClear.call(this);
    };
    w.__biaoshuStorageOpsPatched = true;
  });
}

/**
 * 用途：读取 Storage 操作台账 + generation 元数据。
 * 对接：D2；台账或 generation 缺失时 present=false，不得伪造成空增量。
 */
async function readStorageOpsLedgerMeta(
  page: Page,
): Promise<StorageOpsLedgerMeta> {
  return page.evaluate(() => {
    const w = window as unknown as {
      __biaoshuStorageOps?: StorageOpRecord[];
      __biaoshuStorageOpsGeneration?: number;
    };
    if (
      !Array.isArray(w.__biaoshuStorageOps) ||
      typeof w.__biaoshuStorageOpsGeneration !== "number"
    ) {
      return { present: false, generation: null, ops: [] };
    }
    return {
      present: true,
      generation: w.__biaoshuStorageOpsGeneration,
      ops: w.__biaoshuStorageOps.map((op) => ({ ...op })),
    };
  });
}

/** 用途：读取当前 Storage 操作台账全量副本（台账缺失时返回 []，调用方须配合 meta 门）。 */
async function readStorageOpsLedger(page: Page): Promise<StorageOpRecord[]> {
  const meta = await readStorageOpsLedgerMeta(page);
  return meta.ops;
}

/**
 * 用途：取操作台账基线下标 + generation + 全量 storage snapshot（第二层）。
 * 对接：C2/D2 每个 ask 分支动作前调用；台账缺失立即失败。
 */
async function takeStorageBranchBaseline(
  page: Page,
): Promise<StorageBranchBaseline> {
  const meta = await readStorageOpsLedgerMeta(page);
  if (!meta.present || meta.generation == null) {
    throw new Error(
      "Storage 台账缺失或 generation 不可用，无法取 baseline（禁止把 [] 当零操作）",
    );
  }
  const snapshot = await captureBrowserStorageSnapshot(page);
  return {
    opsBaseline: meta.ops.length,
    generation: meta.generation,
    snapshot,
  };
}

/**
 * 用途：分支动作后立即断言策略相关操作零增量且 snapshot 不变。
 * 对接：C2/D2；台账缺失 / generation 变化 / 长度截断均失败；禁止把 reload 后 [] 当零操作。
 */
async function assertStorageBranchClean(
  page: Page,
  baseline: StorageBranchBaseline,
  label: string,
): Promise<void> {
  const meta = await readStorageOpsLedgerMeta(page);
  if (!meta.present || meta.generation == null) {
    throw new Error(
      `${label}: Storage 台账缺失（document/ledger 不可用，禁止把 [] 当零操作）`,
    );
  }
  if (meta.generation !== baseline.generation) {
    throw new Error(
      `${label}: Storage 台账 generation 变化 baseline=${baseline.generation} now=${meta.generation}`,
    );
  }
  if (meta.ops.length < baseline.opsBaseline) {
    throw new Error(
      `${label}: Storage 台账 truncated baselineLen=${baseline.opsBaseline} now=${meta.ops.length}`,
    );
  }
  assertNoStrategyStorageOps(
    storageOpsSince(meta.ops, baseline.opsBaseline),
    label,
  );
  await assertNoStrategyPersistence(page, baseline.snapshot);
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

/** 用途：列出项目任务，供引擎/error/message/diagnosticCode 与是否创建断言。 */
async function listTasks(
  request: APIRequestContext,
  projectId: string,
): Promise<
  Array<{
    id: string;
    type: string;
    status: string;
    message?: string | null;
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
    message?: string | null;
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

/**
 * 用途：等待 managed failed 并锁定真实 lastTask.message（禁止仅用 error/diagnosticCode 代替）。
 * 对接：P2 old-lastTask 混显；返回 { id, message } 供 UI 可见基线与 API 仍存在证明。
 */
async function awaitManagedFailedWithMessage(
  request: APIRequestContext,
  projectId: string,
): Promise<{ id: string; message: string }> {
  let out: { id: string; message: string } | null = null;
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
      const msg =
        typeof failed.message === "string" ? failed.message.trim() : "";
      if (!msg) return "no-message";
      out = { id: failed.id, message: msg };
      return "failed-managed-message";
    })
    .toBe("failed-managed-message");
  expect(out, "managed failed 必须含非空 message").toBeTruthy();
  return out!;
}

/**
 * P2：读取/失败期 UI 零泄漏计数快照。
 * 用途：locator.count() 不抛，避免读取期 expect(toHaveCount(0)) 提前卡住，
 *       保证可先 release Hold 500、采失败期与 API 证据，finally 后再统一断言为零。
 */
type P2UiLeakSnapshot = {
  lastTaskMessage: number;
  recentTasksLabel: number;
  recentTasksList: number;
  managedFailMsg: number;
  managedFailLink: number;
  realManifestError: number;
  runtimeManifestInvalid: number;
  sensitiveLeak: number;
};

async function captureP2UiLeakSnapshot(
  page: Page,
  lastTaskMessage: string,
): Promise<P2UiLeakSnapshot> {
  return {
    lastTaskMessage: await page
      .getByText(lastTaskMessage, { exact: false })
      .count(),
    recentTasksLabel: await page.getByText("最近任务", { exact: false }).count(),
    recentTasksList: await page
      .getByText("最近任务列表", { exact: true })
      .count(),
    managedFailMsg: await page.getByText(MANAGED_FAIL_MSG).count(),
    managedFailLink: await page
      .getByRole("link", { name: MANAGED_FAIL_LINK })
      .count(),
    realManifestError: await page.getByText(REAL_MANIFEST_TASK_ERROR).count(),
    runtimeManifestInvalid: await page
      .getByText(RUNTIME_MANIFEST_INVALID)
      .count(),
    sensitiveLeak: await page.getByText(SENSITIVE_LEAK).count(),
  };
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
    // C2：页面就绪后安装 Storage 操作台账（SPA navigate 跨路由保留）
    await installStorageOpsLedger(page);
    await uploadTxtViaHiddenInput(page, "e2e-ask.txt");
    await expect(page.locator(".file-chip", { hasText: "e2e-ask.txt" })).toBeVisible({
      timeout: 15_000,
    });

    // 首次点击前锁 task/PUT/light/managed/GET 基线（打开/取消/再开不得吞入）
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBeforeOpen = countStrategyGets(net.apiHits);

    // 取消 — 冻结新术语 exact（T2：禁止旧「在线轻量解析/本地 MinerU 回传」）
    const cancelStorage = await takeStorageBranchBaseline(page);
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
    await assertStorageBranchClean(page, cancelStorage, "技术 ask cancel");

    // 再开前仍精确基线；选轻量（新一次打开再 +1 GET；对话框内选择不额外 GET）
    const lightStorage = await takeStorageBranchBaseline(page);
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
    await assertStorageBranchClean(page, lightStorage, "技术 ask light");

    // 选本地（冻结标签「人工本地回传」）；SPA navigate，台账跨路由保留
    await putParseStrategy(request, "ask");
    const localStorageBaseline = await takeStorageBranchBaseline(page);
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
    // SPA 后仍可读台账并断言本分支零策略写
    await assertStorageBranchClean(page, localStorageBaseline, "技术 ask local");
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
    await installStorageOpsLedger(page);
    const localStorageBranch = await takeStorageBranchBaseline(page);
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
    await assertStorageBranchClean(page, localStorageBranch, "商务 local 上传跳转");

    // ask：整段重解析弹框，取消不建任务
    await putParseStrategy(request, "ask");
    // 先保证已有文件：回到商务标页（文件仍在项目上）
    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标策略" }),
    ).toBeVisible({ timeout: 20_000 });
    await installStorageOpsLedger(page);
    await expect(page.locator(".file-chip", { hasText: "e2e-biz-local.txt" })).toBeVisible({
      timeout: 15_000,
    });
    const askCancelStorage = await takeStorageBranchBaseline(page);
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
    await assertStorageBranchClean(page, askCancelStorage, "商务 local/ask 取消");
  });

  test("商务标 ask：取消 + light/managed/local 独立验收，PUT 零增量", async ({
    page,
    request,
  }) => {
    /**
     * 模块：Q8 / C1 / C2 商务 ask 独立验收
     * 用途：取消 / light(+1 lightweight 真实 terminal) / managed(+1 managed 真实 failed 终态 + owner path)
     *       / local(零任务+SPA 项目化跳转) 精确；各分支独立 Storage 操作台账；
     *       全部 page PUT 零增量；同对话框选择不额外 GET。
     */
    await putParseStrategy(request, "ask");
    const projectId = await createProject(
      request,
      "business",
      "E2E P8B 商务标 ask 上传",
    );
    const net = await installNetworkGuard(page);
    const expectedTasksPath = `/api/projects/${projectId}/tasks`;

    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B 商务标 ask 上传" }),
    ).toBeVisible({ timeout: 20_000 });
    await installStorageOpsLedger(page);

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
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBefore = countStrategyGets(net.apiHits);

    // D1：cancel 分支 Storage baseline 必须在上传/首次 ask 打开之前，
    // 覆盖 上传→策略 GET→对话框→取消 完整窗口；light/managed/local 仍各自独立 baseline
    const cancelStorage = await takeStorageBranchBaseline(page);

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

    // 取消 — 零 task / 零 PUT / 零引擎增量 / 不额外 GET / C2 分支台账（含上传→取消整窗）
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await expect(dialog).toBeHidden();
    expect(net.taskPosts.length).toBe(baselineTasks);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(baselineLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterOpen);
    await assertStorageBranchClean(page, cancelStorage, "商务 ask cancel");

    // light：整段重解析再开 → 只 +1 lightweight，须等真实 terminal 后再进 managed
    const lightStorage = await takeStorageBranchBaseline(page);
    const getBeforeLight = countStrategyGets(net.apiHits);
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    expect(countStrategyGets(net.apiHits)).toBe(getBeforeLight + 1);
    const getAfterLightOpen = countStrategyGets(net.apiHits);
    await dialog.getByRole("button", { name: LABEL_LIGHT, exact: true }).click();
    await expect
      .poll(() => countLightweightParsePosts(net.taskPosts), { timeout: 30_000 })
      .toBe(baselineLight + 1);
    // C1：light 真实 terminal（避免同 type active 重叠假红）
    await expect
      .poll(async () => {
        const tasks = await listTasks(request, projectId);
        const light = tasks.find(
          (t) =>
            t.type === "parse" && t.result?.engine === "lightweight",
        );
        if (!light) return "pending";
        if (light.status === "success" || light.status === "failed") {
          return light.status;
        }
        return light.status;
      })
      .toMatch(/^(success|failed)$/);
    expect(countStrategyGets(net.apiHits)).toBe(getAfterLightOpen);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(baselineManaged);
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(net.taskPosts.length).toBe(baselineTasks + 1);
    await assertStorageBranchClean(page, lightStorage, "商务 ask light");

    // managed：C1 owner path + payload；真实 failed 终态后再锁计数；禁止 page.goto 冒充合格
    const managedStorage = await takeStorageBranchBaseline(page);
    const lightAfterLight = countLightweightParsePosts(net.taskPosts);
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const tasksBeforeManaged = net.taskPosts.length;
    const putsBeforeManaged = countSettingsPuts(net.apiHits);
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
    // C1：锁 captured request.path 与 payload（不得仅断言 payload）
    const managedPost = net.taskPosts
      .slice()
      .reverse()
      .find((h) => {
        const b = parseTaskPostBody(h.postData);
        return b.type === "parse" && b.payload?.engine === "managed";
      });
    expect(managedPost, "managed POST 必须入台账").toBeTruthy();
    expect(managedPost!.path).toBe(expectedTasksPath);
    expect(parseTaskPostBody(managedPost!.postData)).toEqual({
      type: "parse",
      payload: { engine: "managed" },
    });
    // C1：local 前等待当前项目 managed 真实 failed 终态 + 固定安全 UI
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    await awaitManagedFailedPrivacyMarkers(request, projectId);
    // D4：安全 UI + 真实 failed 后先执行明确 continuation barrier，再重读最终计数
    await waitPageContinuationBarrier(page);
    // 终态 + barrier 后重新读取请求台账（禁止复用早期数组）
    expect(countStrategyGets(net.apiHits)).toBe(getAfterManagedOpen);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightAfterLight);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(
      managedBefore + 1,
    );
    expect(net.taskPosts.length).toBe(tasksBeforeManaged + 1);
    expect(countSettingsPuts(net.apiHits)).toBe(putsBeforeManaged);
    // 终态后再次锁定 path/payload owner
    const managedPostFinal = net.taskPosts
      .slice()
      .reverse()
      .find((h) => {
        const b = parseTaskPostBody(h.postData);
        return b.type === "parse" && b.payload?.engine === "managed";
      });
    expect(managedPostFinal!.path).toBe(expectedTasksPath);
    expect(parseTaskPostBody(managedPostFinal!.postData)).toEqual({
      type: "parse",
      payload: { engine: "managed" },
    });
    await assertStorageBranchClean(page, managedStorage, "商务 ask managed");

    // local：同一页 SPA 跳转（不得用 page.goto 抑制 managed continuation 后声称合格）
    // D2：local baseline 记录 generation；跳转后须仍为同一 document/ledger
    const localStorageBranch = await takeStorageBranchBaseline(page);
    const localLedgerGen = localStorageBranch.generation;
    const tasksBeforeLocal = net.taskPosts.length;
    const lightBeforeLocal = countLightweightParsePosts(net.taskPosts);
    const managedBeforeLocal = countEngineParsePosts(net.taskPosts, "managed");
    const getBeforeLocalChoice = countStrategyGets(net.apiHits);
    await page.getByRole("button", { name: "整段重解析", exact: true }).click();
    await expect(dialog).toBeVisible({ timeout: 10_000 });
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
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBeforeLocal);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(
      managedBeforeLocal,
    );
    expect(countSettingsPuts(net.apiHits)).toBe(baselinePuts);
    expect(net.externalHits).toEqual([]);
    // D2：SPA 后同一 document/ledger generation 必须保持；assert 会拒 generation 变化/缺失/截断
    const localMetaAfter = await readStorageOpsLedgerMeta(page);
    expect(localMetaAfter.present, "商务 ask local SPA 后台账必须仍在").toBe(
      true,
    );
    expect(localMetaAfter.generation).toBe(localLedgerGen);
    await assertStorageBranchClean(page, localStorageBranch, "商务 ask local");
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
    const expectedTasksPath = `/api/projects/${projectId}/tasks`;

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E P8B M3 技术标 ask" }),
    ).toBeVisible({ timeout: 20_000 });
    await installStorageOpsLedger(page);
    await uploadTxtViaHiddenInput(page, "e2e-m3-ask.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-m3-ask.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    // 首次点击前锁 task/PUT/light/managed/GET 基线
    const baselineTasks = net.taskPosts.length;
    const baselinePuts = countSettingsPuts(net.apiHits);
    const baselineLight = countLightweightParsePosts(net.taskPosts);
    const baselineManaged = countEngineParsePosts(net.taskPosts, "managed");
    const getBeforeOpen = countStrategyGets(net.apiHits);

    const cancelStorage = await takeStorageBranchBaseline(page);
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
    await assertStorageBranchClean(page, cancelStorage, "M3 技术 ask cancel");

    // 再开前仍精确基线；选 managed 后只允许 managed +1，PUT/light 不变；同对话框选择不额外 GET
    const managedStorage = await takeStorageBranchBaseline(page);
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
    const managedPost = net.taskPosts
      .slice()
      .reverse()
      .find((h) => {
        const b = parseTaskPostBody(h.postData);
        return b.type === "parse" && b.payload?.engine === "managed";
      });
    expect(managedPost).toBeTruthy();
    expect(managedPost!.path).toBe(expectedTasksPath);
    expect(parseTaskPostBody(managedPost!.postData)).toEqual({
      type: "parse",
      payload: { engine: "managed" },
    });

    const settingsRes = await request.get(`${API}/settings`);
    expect(settingsRes.ok()).toBeTruthy();
    const settingsBody = (await settingsRes.json()) as {
      parseStrategy?: string;
    };
    expect(settingsBody.parseStrategy).toBe("ask");
    expect(net.externalHits).toEqual([]);
    await assertStorageBranchClean(page, managedStorage, "M3 技术 ask managed");
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

  test("T5+ P2 技术 old-lastTask 混显：Hold 读取期与 500 失败期零 message，API 任务仍在", async ({
    page,
    request,
  }) => {
    /**
     * 模块：P2 old-lastTask-mixed-with-new-strategy（技术）
     * 用途：真实 managed failed 取得 lastTask.message 并证明动作前 UI 可见；
     *       下一次策略 GET 可释放 HoldGate：读取期仅用 count 快照（不抛），
     *       再 release 500 采失败期快照 + API 旧任务仍在 + POST/隐私；
     *       finally 释放 Hold/断 probe 后，统一断言读取期与失败期均为零。
     * 探针：须在新读取开始后才 arm，禁止把动作前允许展示的旧任务误记为泄漏。
     * 清理：任一意外异常也须 finally 释放 Hold / 断开 probe。
     */
    const projectId = await createProject(
      request,
      "technical",
      "E2E M3 T5+ P2 技术混显",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T5+ P2 技术混显" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-t5p2-tech.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t5p2-tech.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    // 兼容本 worktree 旧「轻量解析」与中性「开始解析」（不改全局 parseActionButton）
    const techParseBtn = page.getByRole("button", {
      name: /^(轻量解析|开始解析)$/,
    });
    await techParseBtn.click();
    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    const failedTask = await awaitManagedFailedWithMessage(request, projectId);
    const lastTaskMessage = failedTask.message;
    expect(lastTaskMessage.length).toBeGreaterThan(0);
    // 动作前：真实 lastTask.message 必须可见（不得只用 error/diagnosticCode 代替）
    await expect(
      page.getByText(lastTaskMessage, { exact: false }).first(),
    ).toBeVisible({
      timeout: 15_000,
    });

    const postsBefore = net.taskPosts.length;
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const lightBefore = countLightweightParsePosts(net.taskPosts);
    const strategyGetBaseline = countStrategyGets(net.apiHits);

    await page.unroute("**/api/settings/parse-strategy");
    const gate = createHoldGate();
    const hold = await installParseStrategyGetHold(page, gate, {
      status: 500,
      body: {
        detail: { code: "internal_error", message: SENSITIVE_LEAK },
      },
    });
    const fulfilledBefore = hold.fulfilled;

    let readSnap: P2UiLeakSnapshot | null = null;
    let failSnap: P2UiLeakSnapshot | null = null;
    let stillThere:
      | {
          id: string;
          status: string;
          message?: string | null;
          result?: { engine?: string } | null;
        }
      | undefined;
    let probe: { callbackCount: number; hitMarkers: string[] } | null = null;
    let probeConsumed = false;

    try {
      await techParseBtn.click();
      // 新读取开始：仅允许「正在读取解析策略」门可见
      await expect(page.getByText("正在读取解析策略")).toBeVisible({
        timeout: 15_000,
      });
      await expect
        .poll(() => hold.held, { timeout: 15_000 })
        .toBeGreaterThanOrEqual(1);
      expect(countStrategyGets(net.apiHits)).toBe(strategyGetBaseline + 1);

      // 探针在新读取开始后才 arm（禁止把动作前旧任务误记为泄漏）
      await installDomPrivacyProbe(page, [
        lastTaskMessage,
        REAL_MANIFEST_TASK_ERROR,
        RUNTIME_MANIFEST_INVALID,
        SENSITIVE_LEAK,
      ]);

      // 读取期：不抛快照计数（禁止 toHaveCount(0) 提前卡住导致无法采 500/API）
      readSnap = await captureP2UiLeakSnapshot(page, lastTaskMessage);

      // 释放并消费策略 Hold 500 → 固定策略错误可见
      await releaseAndAwaitHeldStrategyContinuation(
        page,
        gate,
        hold,
        fulfilledBefore,
      );
      await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
        timeout: 15_000,
      });

      // 失败期快照：含 lastTask message、「最近任务」、「最近任务列表」
      failSnap = await captureP2UiLeakSnapshot(page, lastTaskMessage);

      // task API：旧任务仍存在（禁止删除/清 lastTask 真值冒充）
      const tasksAfter = await listTasks(request, projectId);
      stillThere = tasksAfter.find((t) => t.id === failedTask.id);

      probe = await readDomPrivacyProbe(page);
      probeConsumed = true;
    } finally {
      // 任一意外异常也须释放 Hold / 断 probe（禁止预期首红跳过清理）
      if (!gate.isReleased()) {
        gate.release();
      }
      if (!probeConsumed) {
        try {
          await readDomPrivacyProbe(page);
        } catch {
          /* 页面已关或探针未装时忽略 */
        }
      }
    }

    // 清理完成后统一断言读取期与失败期均为零（failure-first 红点在此）
    // P2R1：完整 8 字段对象精确 toEqual，禁止采集不消费 / 部分字段漏断言
    expect(readSnap, "读取期快照必须已采集").toBeTruthy();
    expect(failSnap, "失败期快照必须已采集").toBeTruthy();
    expect(readSnap!, "读取期 UI 泄漏快照 8 字段须全 0").toEqual({
      lastTaskMessage: 0,
      recentTasksLabel: 0,
      recentTasksList: 0,
      managedFailMsg: 0,
      managedFailLink: 0,
      realManifestError: 0,
      runtimeManifestInvalid: 0,
      sensitiveLeak: 0,
    });
    expect(failSnap!, "失败期 UI 泄漏快照 8 字段须全 0").toEqual({
      lastTaskMessage: 0,
      recentTasksLabel: 0,
      recentTasksList: 0,
      managedFailMsg: 0,
      managedFailLink: 0,
      realManifestError: 0,
      runtimeManifestInvalid: 0,
      sensitiveLeak: 0,
    });

    expect(stillThere, "旧 managed failed 任务必须仍存在").toBeTruthy();
    expect(stillThere!.status).toBe("failed");
    expect(stillThere!.message).toBe(lastTaskMessage);
    expect(stillThere!.result?.engine).toBe("managed");

    expect(probe, "隐私探针必须已消费").toBeTruthy();
    expect(probe!.callbackCount).toBeGreaterThan(0);
    expect(
      probe!.hitMarkers,
      `读取/失败期泄漏: ${probe!.hitMarkers.join(",")}`,
    ).toEqual([]);
    expect(net.taskPosts.length).toBe(postsBefore);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(managedBefore);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBefore);
    expect(net.externalHits).toEqual([]);
  });

  test("T5+ P2 商务 old-lastTask 混显：Hold 读取期与 500 失败期零 message，API 任务仍在", async ({
    page,
    request,
  }) => {
    /**
     * 模块：P2 old-lastTask-mixed-with-new-strategy（商务）
     * 用途：与技术页同风险；商务上传自动 managed failed 后整段重解析路径；
     *       读取期不抛快照 → release 500 → 失败期采 lastTask message/安全门；
     *       finally 释放 Hold/断 probe 后统一断言读取期与失败期为零；API 任务仍在。
     */
    const projectId = await createProject(
      request,
      "business",
      "E2E M3 T5+ P2 商务混显",
    );
    const net = await installNetworkGuard(page);
    await injectParseStrategyGet(page, "managed");

    await page.goto(`/business-bid/${projectId}/parse`);
    await expect(
      page.getByRole("heading", { name: "E2E M3 T5+ P2 商务混显" }),
    ).toBeVisible({ timeout: 20_000 });
    await uploadTxtViaHiddenInput(page, "e2e-t5p2-biz.txt");
    await expect(
      page.locator(".file-chip", { hasText: "e2e-t5p2-biz.txt" }),
    ).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText(MANAGED_FAIL_MSG)).toBeVisible({
      timeout: 45_000,
    });
    const failedTask = await awaitManagedFailedWithMessage(request, projectId);
    const lastTaskMessage = failedTask.message;
    expect(lastTaskMessage.length).toBeGreaterThan(0);
    await expect(
      page.getByText(lastTaskMessage, { exact: false }).first(),
    ).toBeVisible({
      timeout: 15_000,
    });

    const postsBefore = net.taskPosts.length;
    const managedBefore = countEngineParsePosts(net.taskPosts, "managed");
    const lightBefore = countLightweightParsePosts(net.taskPosts);
    const strategyGetBaseline = countStrategyGets(net.apiHits);

    await page.unroute("**/api/settings/parse-strategy");
    const gate = createHoldGate();
    const hold = await installParseStrategyGetHold(page, gate, {
      status: 500,
      body: {
        detail: { code: "internal_error", message: SENSITIVE_LEAK },
      },
    });
    const fulfilledBefore = hold.fulfilled;

    let readSnap: P2UiLeakSnapshot | null = null;
    let failSnap: P2UiLeakSnapshot | null = null;
    let stillThere:
      | {
          id: string;
          status: string;
          message?: string | null;
          result?: { engine?: string } | null;
        }
      | undefined;
    let probe: { callbackCount: number; hitMarkers: string[] } | null = null;
    let probeConsumed = false;

    try {
      await page
        .getByRole("button", { name: "整段重解析", exact: true })
        .click();
      await expect(page.getByText("正在读取解析策略")).toBeVisible({
        timeout: 15_000,
      });
      await expect
        .poll(() => hold.held, { timeout: 15_000 })
        .toBeGreaterThanOrEqual(1);
      expect(countStrategyGets(net.apiHits)).toBe(strategyGetBaseline + 1);

      // 探针在新读取开始后才 arm
      await installDomPrivacyProbe(page, [
        lastTaskMessage,
        REAL_MANIFEST_TASK_ERROR,
        RUNTIME_MANIFEST_INVALID,
        SENSITIVE_LEAK,
      ]);

      // 读取期：不抛快照（旧 lastTask message + 安全门文案）
      readSnap = await captureP2UiLeakSnapshot(page, lastTaskMessage);

      await releaseAndAwaitHeldStrategyContinuation(
        page,
        gate,
        hold,
        fulfilledBefore,
      );
      await expect(page.getByText(STRATEGY_FAIL_MSG)).toBeVisible({
        timeout: 15_000,
      });

      // 失败期：按商务实际 UI 采 lastTask message / 安全门
      failSnap = await captureP2UiLeakSnapshot(page, lastTaskMessage);

      const tasksAfter = await listTasks(request, projectId);
      stillThere = tasksAfter.find((t) => t.id === failedTask.id);

      probe = await readDomPrivacyProbe(page);
      probeConsumed = true;
    } finally {
      if (!gate.isReleased()) {
        gate.release();
      }
      if (!probeConsumed) {
        try {
          await readDomPrivacyProbe(page);
        } catch {
          /* 页面已关或探针未装时忽略 */
        }
      }
    }

    // 清理完成后统一断言（商务：旧 lastTask message + 安全门）
    // P2R1：完整 8 字段对象精确 toEqual，禁止采集不消费 / 部分字段漏断言
    expect(readSnap, "商务读取期快照必须已采集").toBeTruthy();
    expect(failSnap, "商务失败期快照必须已采集").toBeTruthy();
    expect(readSnap!, "商务读取期 UI 泄漏快照 8 字段须全 0").toEqual({
      lastTaskMessage: 0,
      recentTasksLabel: 0,
      recentTasksList: 0,
      managedFailMsg: 0,
      managedFailLink: 0,
      realManifestError: 0,
      runtimeManifestInvalid: 0,
      sensitiveLeak: 0,
    });
    expect(failSnap!, "商务失败期 UI 泄漏快照 8 字段须全 0").toEqual({
      lastTaskMessage: 0,
      recentTasksLabel: 0,
      recentTasksList: 0,
      managedFailMsg: 0,
      managedFailLink: 0,
      realManifestError: 0,
      runtimeManifestInvalid: 0,
      sensitiveLeak: 0,
    });

    expect(stillThere, "旧 managed failed 任务必须仍存在").toBeTruthy();
    expect(stillThere!.status).toBe("failed");
    expect(stillThere!.message).toBe(lastTaskMessage);
    expect(stillThere!.result?.engine).toBe("managed");

    expect(probe, "商务隐私探针必须已消费").toBeTruthy();
    expect(probe!.callbackCount).toBeGreaterThan(0);
    expect(
      probe!.hitMarkers,
      `商务读取/失败期泄漏: ${probe!.hitMarkers.join(",")}`,
    ).toEqual([]);
    expect(net.taskPosts.length).toBe(postsBefore);
    expect(countEngineParsePosts(net.taskPosts, "managed")).toBe(managedBefore);
    expect(countLightweightParsePosts(net.taskPosts)).toBe(lightBefore);
    expect(net.externalHits).toEqual([]);
  });
});

/**
 * Q9：纯 helper 自检（无浏览器、无业务路由）。
 * 证明：(a) 空 key 策略写入被捕获；(b) settings.v1 动作后改写失败；
 *       (c) 动作前已存在且动作后字节相同的合法设置兜底不误报；
 *       (d) C2 操作台账：setItem→removeItem、同值 setItem、clear、session 策略 value 均违规，合法无关写不误报。
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

  test("C2 Storage 操作台账：setItem/removeItem/同值/clear/session 策略 value 违规，合法写不误报", () => {
    // setItem → removeItem 策略命名 key：两步均违规
    const setThenRemove: StorageOpRecord[] = [
      {
        area: "localStorage",
        method: "setItem",
        key: "parseStrategy",
        value: "light",
      },
      {
        area: "localStorage",
        method: "removeItem",
        key: "parseStrategy",
        value: null,
      },
    ];
    expect(isViolatingStorageOp(setThenRemove[0])).toBe(true);
    expect(isViolatingStorageOp(setThenRemove[1])).toBe(true);
    expect(() =>
      assertNoStrategyStorageOps(setThenRemove, "setItem→removeItem"),
    ).toThrow();

    // 同值 setItem 也必须记且违规（settings.v1）
    const sameValueSet: StorageOpRecord = {
      area: "localStorage",
      method: "setItem",
      key: SETTINGS_V1_KEY,
      value: JSON.stringify({ parseStrategy: "ask" }),
    };
    expect(isViolatingStorageOp(sameValueSet)).toBe(true);
    expect(() =>
      assertNoStrategyStorageOps([sameValueSet], "同值 setItem settings.v1"),
    ).toThrow();

    // clear 一律违规
    const clearOp: StorageOpRecord = {
      area: "sessionStorage",
      method: "clear",
      key: null,
      value: null,
    };
    expect(isViolatingStorageOp(clearOp)).toBe(true);
    expect(() => assertNoStrategyStorageOps([clearOp], "clear")).toThrow();

    // sessionStorage 策略 value 违规
    const sessionStrategyVal: StorageOpRecord = {
      area: "sessionStorage",
      method: "setItem",
      key: "tmp.cache",
      value: '{"parseStrategy":"managed"}',
    };
    expect(isViolatingStorageOp(sessionStrategyVal)).toBe(true);
    expect(() =>
      assertNoStrategyStorageOps([sessionStrategyVal], "session 策略 value"),
    ).toThrow();

    // 合法无关写不误报
    const legalOps: StorageOpRecord[] = [
      {
        area: "localStorage",
        method: "setItem",
        key: "biaoshu.ui.prefs",
        value: '{"sidebar":true}',
      },
      {
        area: "sessionStorage",
        method: "setItem",
        key: "tab",
        value: "doc",
      },
      {
        area: "localStorage",
        method: "removeItem",
        key: "biaoshu.ui.toast",
        value: null,
      },
    ];
    for (const op of legalOps) {
      expect(isViolatingStorageOp(op), JSON.stringify(op)).toBe(false);
    }
    assertNoStrategyStorageOps(legalOps, "合法无关写");

    // storageOpsSince 切片正确
    const ledger: StorageOpRecord[] = [
      ...legalOps,
      clearOp,
      sessionStrategyVal,
    ];
    const since = storageOpsSince(ledger, legalOps.length);
    expect(since).toHaveLength(2);
    expect(() => assertNoStrategyStorageOps(since, "since 切片")).toThrow();
    assertNoStrategyStorageOps(storageOpsSince(ledger, ledger.length), "空增量");
  });
});

/**
 * D2/D3：真实浏览器 helper 自检（必须执行浏览器原语，禁止仅查源码字符串）。
 * D2：台账缺失 / generation 变化 / truncated 均失败。
 * D3：React 风格 own value descriptor 上 marker→safe 瞬态命中；原型路径与 attributes/characterData 门保留。
 */
test.describe("D2/D3 Storage 与隐私探针浏览器 helper 自检", () => {
  test("D2 Storage 台账 generation：missing/mismatch/truncated 均失败", async ({
    page,
  }) => {
    // 必须同源可访问 Storage 的页面（about:blank 会 SecurityError）
    await page.goto("http://127.0.0.1:5174/");
    await expect(page.locator("body")).toBeVisible({ timeout: 20_000 });

    // missing：未安装台账时 take/assert 必须失败
    await expect(takeStorageBranchBaseline(page)).rejects.toThrow(
      /台账缺失|generation/,
    );
    await expect(
      assertStorageBranchClean(
        page,
        {
          opsBaseline: 0,
          generation: 1,
          snapshot: { localStorage: {}, sessionStorage: {} },
        },
        "missing-ledger",
      ),
    ).rejects.toThrow(/台账缺失/);

    await installStorageOpsLedger(page);
    const baseline = await takeStorageBranchBaseline(page);
    expect(baseline.generation).toBeGreaterThan(0);
    // 台账副本可读且初始为空增量
    expect(await readStorageOpsLedger(page)).toEqual([]);
    // 合法空增量通过
    await assertStorageBranchClean(page, baseline, "clean-empty");

    // truncated：长度小于 baseline 必须失败（不可把截断当零操作）
    await page.evaluate(() => {
      const w = window as unknown as { __biaoshuStorageOps?: unknown[] };
      // 先写一条再截断到 0，模拟 document 半丢失/被清空
      w.__biaoshuStorageOps = w.__biaoshuStorageOps || [];
      w.__biaoshuStorageOps.push({
        area: "localStorage",
        method: "setItem",
        key: "x",
        value: "1",
      });
      const paddedBaseline = (w.__biaoshuStorageOps as unknown[]).length;
      (window as unknown as { __pad?: number }).__pad = paddedBaseline;
      w.__biaoshuStorageOps.length = 0;
    });
    const truncatedBaseline: StorageBranchBaseline = {
      opsBaseline: 1,
      generation: baseline.generation,
      snapshot: baseline.snapshot,
    };
    await expect(
      assertStorageBranchClean(page, truncatedBaseline, "truncated"),
    ).rejects.toThrow(/truncated/);

    // generation mismatch：reinstall bump 后旧 baseline 失败
    await installStorageOpsLedger(page);
    const afterReinstall = await readStorageOpsLedgerMeta(page);
    expect(afterReinstall.present).toBe(true);
    expect(afterReinstall.generation).not.toBe(baseline.generation);
    await expect(
      assertStorageBranchClean(page, baseline, "generation-mismatch"),
    ).rejects.toThrow(/generation 变化/);

    // 同 document 新 baseline 可通过
    const fresh = await takeStorageBranchBaseline(page);
    await assertStorageBranchClean(page, fresh, "fresh-ok");
  });

  test("D3 隐私探针：own/prototype/attr 互异 marker 命中且 restore 精确还原", async ({
    page,
  }) => {
    /**
     * E1：八条子路径各用互异 marker（existing-own 三控件 / prototype-new 三控件 /
     *     attribute / characterData），任一路径失效时其它路径无法顶替使其通过。
     * E2：同一正式 installDomPrivacyProbe 覆盖安装前 own 控件与安装后 plain 原型路径；
     *     安装前保存 own + 三原型 descriptor；restore 后精确核对 get/set 身份与 flags。
     * E3：existing/plain 两个 select 均含 SAFE option，终值精确等于 SAFE（禁止空串当成功）。
     */
    // 八个互异 marker：禁止共享类别 marker
    const MARKER_OWN_INPUT = "D3_EXISTING_OWN_INPUT_MARKER_SECRET";
    const MARKER_OWN_TEXTAREA = "D3_EXISTING_OWN_TEXTAREA_MARKER_SECRET";
    const MARKER_OWN_SELECT = "D3_EXISTING_OWN_SELECT_MARKER_SECRET";
    const MARKER_PROTO_INPUT = "D3_PROTOTYPE_NEW_INPUT_MARKER_SECRET";
    const MARKER_PROTO_TEXTAREA = "D3_PROTOTYPE_NEW_TEXTAREA_MARKER_SECRET";
    const MARKER_PROTO_SELECT = "D3_PROTOTYPE_NEW_SELECT_MARKER_SECRET";
    const MARKER_ATTR = "D3_ATTRIBUTE_MARKER_SECRET";
    const MARKER_CHAR = "D3_CHARACTER_DATA_MARKER_SECRET";
    const SAFE = "d3-safe-final-value";
    const ALL_MARKERS = [
      MARKER_OWN_INPUT,
      MARKER_OWN_TEXTAREA,
      MARKER_OWN_SELECT,
      MARKER_PROTO_INPUT,
      MARKER_PROTO_TEXTAREA,
      MARKER_PROTO_SELECT,
      MARKER_ATTR,
      MARKER_CHAR,
    ];
    // 断言八 marker 互异（去重后仍为 8）
    expect(new Set(ALL_MARKERS).size).toBe(8);

    await page.goto("about:blank");
    // 建立 React 风格 own descriptor：缓存原生 setter，实例 set 不经后续原型 patch 路径
    await page.evaluate(
      ({ safe }) => {
        type DescSnap = {
          get: PropertyDescriptor["get"];
          set: PropertyDescriptor["set"];
          enumerable: boolean | undefined;
          configurable: boolean | undefined;
        };
        type D3Snap = {
          inputOwn: DescSnap;
          textareaOwn: DescSnap;
          selectOwn: DescSnap;
          inputProto: DescSnap;
          textareaProto: DescSnap;
          selectProto: DescSnap;
        };
        const w = window as unknown as { __d3DescriptorSnap?: D3Snap };
        const host = document.createElement("div");
        host.id = "d3-probe-host";
        document.body.appendChild(host);

        const toSnap = (desc: PropertyDescriptor): DescSnap => ({
          get: desc.get,
          set: desc.set,
          enumerable: desc.enumerable,
          configurable: desc.configurable,
        });

        /** select 必须含 SAFE option，否则 marker→SAFE 后 value 变空串（禁止当成功） */
        const fillSelectOptions = (el: HTMLSelectElement, safe: string) => {
          const optEmpty = document.createElement("option");
          optEmpty.value = "";
          optEmpty.textContent = "empty";
          el.appendChild(optEmpty);
          const optX = document.createElement("option");
          optX.value = "x";
          optX.textContent = "x";
          el.appendChild(optX);
          const optSafe = document.createElement("option");
          optSafe.value = safe;
          optSafe.textContent = "safe";
          el.appendChild(optSafe);
        };

        const mkReactOwned = (tag: "input" | "textarea" | "select") => {
          const el = document.createElement(tag) as
            | HTMLInputElement
            | HTMLTextAreaElement
            | HTMLSelectElement;
          el.id = `d3-${tag}`;
          if (tag === "select") {
            fillSelectOptions(el as HTMLSelectElement, safe);
          }
          host.appendChild(el);
          const protoDesc = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(el),
            "value",
          );
          if (!protoDesc?.get || !protoDesc?.set) {
            throw new Error(`缺少 ${tag} 原型 value descriptor`);
          }
          // 缓存安装探针前的原生 setter（模拟 React trackValueOnNode）
          const nativeGet = protoDesc.get;
          const nativeSet = protoDesc.set;
          Object.defineProperty(el, "value", {
            configurable: true,
            enumerable: true,
            get: function (this: HTMLInputElement) {
              return nativeGet.call(this);
            },
            set: function (this: HTMLInputElement, next: string) {
              return nativeSet.call(this, next);
            },
          });
          return el;
        };
        const input = mkReactOwned("input");
        const textarea = mkReactOwned("textarea");
        const select = mkReactOwned("select");

        // attributes / characterData 门对照节点
        const span = document.createElement("span");
        span.id = "d3-attr-node";
        span.setAttribute("data-x", "init");
        span.textContent = "init-text";
        host.appendChild(span);

        // 安装前快照：existing own + 三原型 descriptor（身份/flags 供 restore 精确核对）
        const inputOwn = Object.getOwnPropertyDescriptor(input, "value");
        const textareaOwn = Object.getOwnPropertyDescriptor(textarea, "value");
        const selectOwn = Object.getOwnPropertyDescriptor(select, "value");
        const inputProto = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        );
        const textareaProto = Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype,
          "value",
        );
        const selectProto = Object.getOwnPropertyDescriptor(
          HTMLSelectElement.prototype,
          "value",
        );
        if (
          !inputOwn?.get ||
          !inputOwn?.set ||
          !textareaOwn?.get ||
          !textareaOwn?.set ||
          !selectOwn?.get ||
          !selectOwn?.set ||
          !inputProto?.get ||
          !inputProto?.set ||
          !textareaProto?.get ||
          !textareaProto?.set ||
          !selectProto?.get ||
          !selectProto?.set
        ) {
          throw new Error("D3 安装前 own/prototype value descriptor 不完整");
        }
        w.__d3DescriptorSnap = {
          inputOwn: toSnap(inputOwn),
          textareaOwn: toSnap(textareaOwn),
          selectOwn: toSnap(selectOwn),
          inputProto: toSnap(inputProto),
          textareaProto: toSnap(textareaProto),
          selectProto: toSnap(selectProto),
        };
      },
      { safe: SAFE },
    );

    // 证明 own descriptor 已存在
    const ownBefore = await page.evaluate(() => {
      const input = document.getElementById("d3-input") as HTMLInputElement;
      return !!Object.getOwnPropertyDescriptor(input, "value")?.set;
    });
    expect(ownBefore).toBe(true);

    // 同一正式 helper：同时监视八个互异 marker
    await installDomPrivacyProbe(page, ALL_MARKERS);

    // 八条互异路径：各只写自己的 marker，再写 SAFE
    await page.evaluate(
      ({
        markerOwnInput,
        markerOwnTextarea,
        markerOwnSelect,
        markerProtoInput,
        markerProtoTextarea,
        markerProtoSelect,
        markerAttr,
        markerChar,
        safe,
      }) => {
        const input = document.getElementById("d3-input") as HTMLInputElement;
        const textarea = document.getElementById(
          "d3-textarea",
        ) as HTMLTextAreaElement;
        const select = document.getElementById("d3-select") as HTMLSelectElement;
        // existing-own：安装前已有 own setter；三控件各写互异 marker
        input.value = markerOwnInput;
        input.value = safe;
        textarea.value = markerOwnTextarea;
        textarea.value = safe;
        select.value = markerOwnSelect;
        select.value = safe;

        // prototype-new：安装后新建 plain 控件（无 own descriptor）走原型 setter
        const host = document.getElementById("d3-probe-host") as HTMLElement;
        const fillSelectOptions = (el: HTMLSelectElement, safeVal: string) => {
          const optEmpty = document.createElement("option");
          optEmpty.value = "";
          optEmpty.textContent = "empty";
          el.appendChild(optEmpty);
          const optX = document.createElement("option");
          optX.value = "x";
          optX.textContent = "x";
          el.appendChild(optX);
          const optSafe = document.createElement("option");
          optSafe.value = safeVal;
          optSafe.textContent = "safe";
          el.appendChild(optSafe);
        };
        const mkPlain = (tag: "input" | "textarea" | "select") => {
          const el = document.createElement(tag) as
            | HTMLInputElement
            | HTMLTextAreaElement
            | HTMLSelectElement;
          el.id = `d3-plain-${tag}`;
          if (tag === "select") {
            fillSelectOptions(el as HTMLSelectElement, safe);
          }
          host.appendChild(el);
          if (Object.getOwnPropertyDescriptor(el, "value")) {
            throw new Error(`plain ${tag} 不应有 own value descriptor`);
          }
          return el;
        };
        const plainInput = mkPlain("input");
        const plainTextarea = mkPlain("textarea");
        const plainSelect = mkPlain("select");
        plainInput.value = markerProtoInput;
        plainInput.value = safe;
        plainTextarea.value = markerProtoTextarea;
        plainTextarea.value = safe;
        plainSelect.value = markerProtoSelect;
        plainSelect.value = safe;

        // attributes 与 characterData 各用独立 marker（不得共享）
        const span = document.getElementById("d3-attr-node") as HTMLElement;
        span.setAttribute("data-x", markerAttr);
        span.setAttribute("data-x", safe);
        const text = span.firstChild as Text;
        text.data = markerChar;
        text.data = safe;
      },
      {
        markerOwnInput: MARKER_OWN_INPUT,
        markerOwnTextarea: MARKER_OWN_TEXTAREA,
        markerOwnSelect: MARKER_OWN_SELECT,
        markerProtoInput: MARKER_PROTO_INPUT,
        markerProtoTextarea: MARKER_PROTO_TEXTAREA,
        markerProtoSelect: MARKER_PROTO_SELECT,
        markerAttr: MARKER_ATTR,
        markerChar: MARKER_CHAR,
        safe: SAFE,
      },
    );

    const probe = await readDomPrivacyProbe(page);
    // E1：八个 marker 逐项精确 toContain，禁止单路径/类别 marker 顶替
    expect(probe.hitMarkers, "existing-own input marker").toContain(
      MARKER_OWN_INPUT,
    );
    expect(probe.hitMarkers, "existing-own textarea marker").toContain(
      MARKER_OWN_TEXTAREA,
    );
    expect(probe.hitMarkers, "existing-own select marker").toContain(
      MARKER_OWN_SELECT,
    );
    expect(probe.hitMarkers, "prototype-new input marker").toContain(
      MARKER_PROTO_INPUT,
    );
    expect(probe.hitMarkers, "prototype-new textarea marker").toContain(
      MARKER_PROTO_TEXTAREA,
    );
    expect(probe.hitMarkers, "prototype-new select marker").toContain(
      MARKER_PROTO_SELECT,
    );
    expect(probe.hitMarkers, "attribute marker").toContain(MARKER_ATTR);
    expect(probe.hitMarkers, "characterData marker").toContain(MARKER_CHAR);

    // 完整终值门：六控件 value + attribute + Text.data 均精确等于 SAFE，且不含对应 marker
    // （read/restore 后读取；select 不得以无匹配 option 的空串冒充成功）
    const finals = await page.evaluate(() => {
      const input = document.getElementById("d3-input") as HTMLInputElement;
      const textarea = document.getElementById(
        "d3-textarea",
      ) as HTMLTextAreaElement;
      const select = document.getElementById("d3-select") as HTMLSelectElement;
      const plainInput = document.getElementById(
        "d3-plain-input",
      ) as HTMLInputElement;
      const plainTextarea = document.getElementById(
        "d3-plain-textarea",
      ) as HTMLTextAreaElement;
      const plainSelect = document.getElementById(
        "d3-plain-select",
      ) as HTMLSelectElement;
      const span = document.getElementById("d3-attr-node") as HTMLElement;
      const text = span.firstChild as Text;
      return {
        ownInput: input.value,
        ownTextarea: textarea.value,
        ownSelect: select.value,
        plainInput: plainInput.value,
        plainTextarea: plainTextarea.value,
        plainSelect: plainSelect.value,
        attr: span.getAttribute("data-x"),
        charData: text.data,
      };
    });
    expect(finals.ownInput, "existing input 终值").toBe(SAFE);
    expect(finals.ownTextarea, "existing textarea 终值").toBe(SAFE);
    expect(finals.ownSelect, "existing select 终值（须匹配 SAFE option）").toBe(
      SAFE,
    );
    expect(finals.plainInput, "plain input 终值").toBe(SAFE);
    expect(finals.plainTextarea, "plain textarea 终值").toBe(SAFE);
    expect(finals.plainSelect, "plain select 终值（须匹配 SAFE option）").toBe(
      SAFE,
    );
    expect(finals.attr, "attribute 终值").toBe(SAFE);
    expect(finals.charData, "characterData 终值").toBe(SAFE);

    expect(finals.ownInput).not.toContain(MARKER_OWN_INPUT);
    expect(finals.ownTextarea).not.toContain(MARKER_OWN_TEXTAREA);
    expect(finals.ownSelect).not.toContain(MARKER_OWN_SELECT);
    expect(finals.plainInput).not.toContain(MARKER_PROTO_INPUT);
    expect(finals.plainTextarea).not.toContain(MARKER_PROTO_TEXTAREA);
    expect(finals.plainSelect).not.toContain(MARKER_PROTO_SELECT);
    expect(finals.attr).not.toContain(MARKER_ATTR);
    expect(finals.charData).not.toContain(MARKER_CHAR);

    // E2：restore 后精确核对 get/set 身份与 enumerable/configurable（禁止仅「仍能赋值」）
    const restored = await page.evaluate(() => {
      type DescSnap = {
        get: PropertyDescriptor["get"];
        set: PropertyDescriptor["set"];
        enumerable: boolean | undefined;
        configurable: boolean | undefined;
      };
      type D3Snap = {
        inputOwn: DescSnap;
        textareaOwn: DescSnap;
        selectOwn: DescSnap;
        inputProto: DescSnap;
        textareaProto: DescSnap;
        selectProto: DescSnap;
      };
      const w = window as unknown as { __d3DescriptorSnap?: D3Snap };
      const snap = w.__d3DescriptorSnap;
      if (!snap) throw new Error("缺少安装前 descriptor 快照");

      const checkPair = (
        cur: PropertyDescriptor | undefined,
        expected: DescSnap,
        label: string,
      ) => {
        if (!cur) {
          return { ok: false, reason: `${label}: descriptor 缺失` };
        }
        if (cur.get !== expected.get) {
          return { ok: false, reason: `${label}: get 身份不一致` };
        }
        if (cur.set !== expected.set) {
          return { ok: false, reason: `${label}: set 身份不一致` };
        }
        if (cur.enumerable !== expected.enumerable) {
          return { ok: false, reason: `${label}: enumerable 不一致` };
        }
        if (cur.configurable !== expected.configurable) {
          return { ok: false, reason: `${label}: configurable 不一致` };
        }
        return { ok: true, reason: "" };
      };

      const input = document.getElementById("d3-input") as HTMLInputElement;
      const textarea = document.getElementById(
        "d3-textarea",
      ) as HTMLTextAreaElement;
      const select = document.getElementById("d3-select") as HTMLSelectElement;
      const checks = [
        checkPair(
          Object.getOwnPropertyDescriptor(input, "value"),
          snap.inputOwn,
          "input.own",
        ),
        checkPair(
          Object.getOwnPropertyDescriptor(textarea, "value"),
          snap.textareaOwn,
          "textarea.own",
        ),
        checkPair(
          Object.getOwnPropertyDescriptor(select, "value"),
          snap.selectOwn,
          "select.own",
        ),
        checkPair(
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value"),
          snap.inputProto,
          "HTMLInputElement.prototype",
        ),
        checkPair(
          Object.getOwnPropertyDescriptor(
            HTMLTextAreaElement.prototype,
            "value",
          ),
          snap.textareaProto,
          "HTMLTextAreaElement.prototype",
        ),
        checkPair(
          Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value"),
          snap.selectProto,
          "HTMLSelectElement.prototype",
        ),
      ];
      const failed = checks.filter((c) => !c.ok);
      // 语义仍可用（附加，非唯一证据）
      input.value = "after-restore";
      return {
        allOk: failed.length === 0,
        failed: failed.map((f) => f.reason),
        valueAfter: input.value,
      };
    });
    expect(
      restored.allOk,
      `restore 身份/flags 必须精确还原: ${restored.failed.join("; ")}`,
    ).toBe(true);
    expect(restored.valueAfter).toBe("after-restore");
  });
});
