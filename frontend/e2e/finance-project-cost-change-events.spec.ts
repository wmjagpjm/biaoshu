/**
 * 模块：P10K 财务项目成本变更记录前端 E2E
 * 用途：验收 /finance 显式面板零自动 GET、精确 1/2 次读取、路径 encodeURIComponent、
 *       字段/枚举/限制声明/空态/错误脱敏、项目切换迟到隔离、P10J/业务/外网阻断、
 *       存储/剪贴板/console 零泄漏、非 finance/owner/disabled 零请求、P10C 写后零自动。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:finance-project-cost-change-events。
 * 二次开发：仅桩 auth、health、P10B/P10C 与项目 cost-change-events；阻断 P10J、
 *           projects/editor-state/settings/files、HR/bidder、未知 API；字体本地 204；
 *           其他外网记 externalHits 后 abort。禁止 sleep。禁止 catch 后伪装成功。
 */
import { expect, test, type Page, type Route } from "@playwright/test";

type AuthRole = "bid_writer" | "finance" | "hr" | "bidder";

type MePayload = {
  user: { id: string; username: string };
  workspaces: Array<{
    id: string;
    name: string;
    role: AuthRole;
    isOwner: boolean;
  }>;
  activeWorkspaceId: string;
  csrfToken?: string | null;
};

type ProjectCostAction = "create" | "update" | "delete";
type ActorScope = "self" | "other";

type ProjectCostItem = {
  action: ProjectCostAction | string;
  entryId: string;
  actorScope: ActorScope | string;
  occurredAt: string;
};

type ProjectCostPayload = {
  items: ProjectCostItem[] | unknown;
};

/** P10C 成本条目桩（写后草案重读依赖） */
type CostEntryStub = {
  id: string;
  category: string;
  name: string;
  amountFen: number;
  remark: string;
  createdAt: string;
  updatedAt: string;
};

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

/** 含空格、斜杠、中文的项目 ID，防止普通 ID 假绿 */
const SPECIAL_PROJECT_ID = "proj 甲/测 试";
const SPECIAL_PROJECT_ENCODED = encodeURIComponent(SPECIAL_PROJECT_ID);

const DISCLAIMER =
  "仅记录 P10K 上线后的成功操作，不含金额、内容、成员身份、失败尝试或旧历史";

const FIXED_ERROR = "项目成本记录加载失败，请稍后重试";

const SECRET_SNIPPET =
  "SECRET-LEAK entryId=fce_p10k path=/api/finance/business-bids amount=9999";

const BACKEND_ERROR_CODE = "finance_project_cost_change_events_failed";

/**
 * 用途：识别既有 index.html 引入的 Google 字体 URL。
 * 对接：installP10kRoutes 本地阻断，不得 route.continue 外网。
 */
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
    headers: { "Cache-Control": "no-store" },
    body: JSON.stringify(body),
  });
}

function meFor(
  role: AuthRole,
  opts: { isOwner?: boolean; username?: string; csrf?: string | null } = {},
): MePayload {
  const isOwner = opts.isOwner ?? false;
  return {
    user: { id: `user_${role}`, username: opts.username ?? `user_${role}` },
    workspaces: [
      {
        id: "ws_e2e",
        name: "E2E 工作空间",
        role,
        isOwner,
      },
    ],
    activeWorkspaceId: "ws_e2e",
    csrfToken: opts.csrf === undefined ? null : opts.csrf,
  };
}

const SAMPLE_LIST = {
  items: [
    {
      projectId: "proj_biz_1",
      name: "示例商务标甲",
      industry: "能源",
      status: "writing",
      updatedAt: "2026-07-14T08:00:00+00:00",
      quoteRowCount: 1,
      quoteTotal: 10000,
    },
    {
      projectId: "proj_biz_2",
      name: "示例商务标乙",
      industry: "通用",
      status: "analyzing",
      updatedAt: "2026-07-13T12:30:00+00:00",
      quoteRowCount: 0,
      quoteTotal: 0,
    },
    {
      projectId: SPECIAL_PROJECT_ID,
      name: "特殊路径商务标",
      industry: "能源",
      status: "writing",
      updatedAt: "2026-07-14T09:00:00+00:00",
      quoteRowCount: 0,
      quoteTotal: 0,
    },
  ],
};

function detailFor(projectId: string) {
  const base = SAMPLE_LIST.items.find((i) => i.projectId === projectId);
  return {
    projectId,
    name: base?.name ?? projectId,
    industry: base?.industry ?? "通用",
    status: base?.status ?? "draft",
    updatedAt: base?.updatedAt ?? "2026-07-14T08:00:00+00:00",
    quoteRowCount: base?.quoteRowCount ?? 0,
    quoteTotal: base?.quoteTotal ?? 0,
    quoteRows:
      projectId === "proj_biz_1"
        ? [
            {
              id: "row_1",
              name: "设备",
              unit: "套",
              quantity: "1",
              unitPrice: "10000",
              amount: 10000,
              remark: "",
            },
          ]
        : [],
    quoteNotes: "",
  };
}

function draftFor(projectId: string, entries: CostEntryStub[] = []) {
  const quoteTotalFen =
    projectId === "proj_biz_1" ? 1_000_000 : projectId === "proj_biz_2" ? 0 : 0;
  const costTotalFen = entries.reduce((sum, e) => sum + e.amountFen, 0);
  const grossProfitFen = quoteTotalFen - costTotalFen;
  return {
    projectId,
    projectName: detailFor(projectId).name,
    quoteTotalFen,
    costTotalFen,
    grossProfitFen,
    grossMarginBasisPoints:
      quoteTotalFen > 0
        ? Math.round((grossProfitFen / quoteTotalFen) * 10000)
        : null,
    costEntries: entries,
  };
}

function seedP10kPayload(): ProjectCostPayload {
  return {
    items: [
      {
        action: "create",
        entryId: "fce_create_alpha",
        actorScope: "self",
        occurredAt: "2026-07-14T10:00:00.000Z",
      },
      {
        action: "update",
        entryId: "fce_update_beta",
        actorScope: "other",
        occurredAt: "2026-07-14T11:30:00.000Z",
      },
      {
        action: "delete",
        entryId: "fce_delete_gamma",
        actorScope: "self",
        occurredAt: "2026-07-14T12:45:00.000Z",
      },
    ],
  };
}

type P10kAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  /** P10B 列表/明细 */
  p10bHits: string[];
  /** P10C cost-draft / cost-entries */
  p10cHits: string[];
  /** 精确 P10K 项目 cost-change-events（完整 pathname） */
  p10kHits: string[];
  /** 禁止的 P10J 个人 cost-change-events */
  p10jHits: string[];
  externalHits: string[];
  payloadByProject: Record<string, ProjectCostPayload>;
  forceErrorByProject?: Record<string, boolean>;
  /** 按项目延迟 P10K 响应，用于迟到隔离 */
  p10kHoldByProject?: Record<string, Promise<void>>;
  /** 按项目延迟明细响应 */
  detailHoldByProject?: Record<string, Promise<void>>;
  /** 可变成本条目，供 P10C 写后草案重读 */
  costEntriesByProject: Record<string, CostEntryStub[]>;
  costSeq: number;
};

/**
 * 用途：安装 auth + P10B/P10C/P10K 桩；阻断 P10J/业务/HR/bidder/未知 API 与外网。
 * 二次开发：AUTH_MODE=disabled 未知 /api 必须 forbiddenHits+403，禁止宽泛 json([])。
 */
async function installP10kRoutes(page: Page, state: P10kAuthState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(rawUrl)) {
      await route.fulfill({
        status: 204,
        contentType: "text/plain",
        body: "",
      });
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

    const host = url.hostname;
    const path = url.pathname;

    if (!isLocalHost(host)) {
      state.externalHits.push(`${method} ${rawUrl}`);
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    // 阻断通用业务与其他角色
    if (
      path.startsWith("/api/projects") ||
      path.includes("editor-state") ||
      path.startsWith("/api/settings") ||
      path.startsWith("/api/files") ||
      path.startsWith("/api/export") ||
      path.startsWith("/api/hr") ||
      path.startsWith("/api/bidder")
    ) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "禁止业务回退" } },
        403,
      );
      return;
    }

    // P10J 全局接口：必须可观测阻断（auth 模式无关）
    if (path === "/api/finance/cost-change-events") {
      state.p10jHits.push(`${method} ${path}${url.search || ""}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "禁止调用 P10J" } },
        403,
      );
      return;
    }

    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, {
        bootstrapped: state.bootstrapped,
        authRequired: state.authRequired,
      });
      return;
    }

    // AUTH_MODE=disabled：只显式桩 health；未知 /api 必须 forbiddenHits+403
    if (!state.authRequired) {
      if (path === "/api/health" && method === "GET") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          defaultWorkspaceId: "ws_e2e",
        });
        return;
      }
      // 精确区分 P10K / 其他 finance / 未知 API
      const p10kDisabled = path.match(
        /^\/api\/finance\/business-bids\/(.+)\/cost-change-events$/,
      );
      if (p10kDisabled && method === "GET") {
        state.p10kHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (path.startsWith("/api/finance/")) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      // 未知本机 API：禁止宽泛 json([]) 放行
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "未授权业务接口" } },
        403,
      );
      return;
    }

    if (path === "/api/auth/me" && method === "GET") {
      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(route, { ...state.session, csrfToken: null });
      return;
    }

    if (path === "/api/auth/csrf" && method === "GET") {
      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(route, {
        csrfToken: state.resumeCsrf ?? "e2e-p10k-csrf",
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.session = null;
      await route.fulfill({ status: 204, body: "" });
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

    const isFinance = state.session?.workspaces[0]?.role === "finance";

    // P10K：/api/finance/business-bids/{id}/cost-change-events
    const p10kMatch = path.match(
      /^\/api\/finance\/business-bids\/(.+)\/cost-change-events$/,
    );
    if (p10kMatch && method === "GET") {
      state.p10kHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      // 路径段保持 encode 形态；解码后取项目 payload
      let projectId = p10kMatch[1];
      try {
        projectId = decodeURIComponent(p10kMatch[1]);
      } catch {
        projectId = p10kMatch[1];
      }
      if (state.p10kHoldByProject?.[projectId]) {
        await state.p10kHoldByProject[projectId];
      }
      if (state.forceErrorByProject?.[projectId]) {
        await json(
          route,
          {
            detail: {
              code: BACKEND_ERROR_CODE,
              message: "内部错误",
              leak: SECRET_SNIPPET,
              projectId,
              path,
              entryId: "fce_error_leak",
            },
          },
          500,
        );
        return;
      }
      const payload =
        state.payloadByProject[projectId] ?? ({ items: [] } as ProjectCostPayload);
      await json(route, payload);
      return;
    }

    if (path === "/api/finance/business-bids" && method === "GET") {
      state.p10bHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      await json(route, SAMPLE_LIST);
      return;
    }

    const draftMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-draft$/,
    );
    if (draftMatch && method === "GET") {
      state.p10cHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      let projectId = draftMatch[1];
      try {
        projectId = decodeURIComponent(draftMatch[1]);
      } catch {
        projectId = draftMatch[1];
      }
      const entries = state.costEntriesByProject[projectId] ?? [];
      await json(route, draftFor(projectId, entries));
      return;
    }

    // P10C POST 创建
    const createMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-entries$/,
    );
    if (createMatch && method === "POST") {
      state.p10cHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      let projectId = createMatch[1];
      try {
        projectId = decodeURIComponent(createMatch[1]);
      } catch {
        projectId = createMatch[1];
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
      } catch {
        body = {};
      }
      state.costSeq += 1;
      const now = "2026-07-14T13:00:00+00:00";
      const entry: CostEntryStub = {
        id: `fce_write_${state.costSeq}`,
        category: String(body.category ?? "other"),
        name: String(body.name ?? ""),
        amountFen: Number(body.amountFen ?? 0),
        remark: String(body.remark ?? ""),
        createdAt: now,
        updatedAt: now,
      };
      const prev = state.costEntriesByProject[projectId] ?? [];
      state.costEntriesByProject[projectId] = [entry, ...prev];
      await json(route, entry, 201);
      return;
    }

    // P10C PATCH / DELETE
    const entryMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-entries\/([^/]+)$/,
    );
    if (entryMatch && (method === "PATCH" || method === "DELETE")) {
      state.p10cHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      let projectId = entryMatch[1];
      try {
        projectId = decodeURIComponent(entryMatch[1]);
      } catch {
        projectId = entryMatch[1];
      }
      let entryId = entryMatch[2];
      try {
        entryId = decodeURIComponent(entryMatch[2]);
      } catch {
        entryId = entryMatch[2];
      }
      const list = state.costEntriesByProject[projectId] ?? [];
      const idx = list.findIndex((e) => e.id === entryId);
      if (idx < 0) {
        await json(
          route,
          {
            detail: {
              code: "project_not_found",
              message: "项目不存在或不可访问",
            },
          },
          404,
        );
        return;
      }
      if (method === "DELETE") {
        state.costEntriesByProject[projectId] = list.filter(
          (e) => e.id !== entryId,
        );
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
      } catch {
        body = {};
      }
      const prev = list[idx];
      const next: CostEntryStub = {
        ...prev,
        category:
          body.category !== undefined
            ? String(body.category)
            : prev.category,
        name: body.name !== undefined ? String(body.name) : prev.name,
        amountFen:
          body.amountFen !== undefined
            ? Number(body.amountFen)
            : prev.amountFen,
        remark:
          body.remark !== undefined ? String(body.remark) : prev.remark,
        updatedAt: "2026-07-14T14:00:00+00:00",
      };
      state.costEntriesByProject[projectId] = [
        ...list.slice(0, idx),
        next,
        ...list.slice(idx + 1),
      ];
      await json(route, next);
      return;
    }

    // 明细（可能含 encode 段）
    const detailMatch = path.match(
      /^\/api\/finance\/business-bids\/(.+)$/,
    );
    if (
      detailMatch &&
      method === "GET" &&
      !detailMatch[1].includes("/cost-") &&
      !detailMatch[1].includes("/cost-entries")
    ) {
      state.p10bHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      let projectId = detailMatch[1];
      try {
        projectId = decodeURIComponent(detailMatch[1]);
      } catch {
        projectId = detailMatch[1];
      }
      if (state.detailHoldByProject?.[projectId]) {
        await state.detailHoldByProject[projectId];
      }
      const known = SAMPLE_LIST.items.some((i) => i.projectId === projectId);
      if (!known) {
        await json(
          route,
          {
            detail: {
              code: "project_not_found",
              message: "项目不存在或不可访问",
            },
          },
          404,
        );
        return;
      }
      await json(route, detailFor(projectId));
      return;
    }

    if (!state.session) {
      await json(
        route,
        { detail: { code: "auth_required", message: "需要登录" } },
        401,
      );
      return;
    }

    // 未知本机 API：可观测阻断
    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "role_forbidden", message: "未授权业务接口" } },
      403,
    );
  });
}

/**
 * 用途：精确断言存储空；IndexedDB 不可用/枚举失败必须让测试失败。
 * 二次开发：禁止 catch 后伪装 []；禁止 filter 空名称；返回全部 name??""。
 */
async function assertNoSensitiveStorage(page: Page) {
  const storage = await page.evaluate(async () => {
    const dump = (store: Storage) => {
      const out: Record<string, string> = {};
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        if (key) out[key] = store.getItem(key) ?? "";
      }
      return out;
    };

    // API 不可用必须抛出，不得伪装空列表
    if (typeof indexedDB === "undefined") {
      throw new Error("indexedDB 不可用");
    }
    if (typeof indexedDB.databases !== "function") {
      throw new Error("indexedDB.databases 必须为函数");
    }
    const dbs = await indexedDB.databases();
    // 保留全部 name??""，禁止 filter(Boolean) 丢弃空名
    const idbNames = dbs.map((d) => d.name ?? "");

    return {
      local: dump(window.localStorage),
      session: dump(window.sessionStorage),
      cookies: document.cookie,
      idbNames,
    };
  });

  expect(Object.keys(storage.local)).toEqual([]);
  expect(Object.keys(storage.session)).toEqual([]);
  expect(storage.cookies.trim()).toBe("");
  expect(storage.idbNames).toEqual([]);

  for (const [scope, map] of [
    ["localStorage", storage.local],
    ["sessionStorage", storage.session],
  ] as const) {
    for (const [key, value] of Object.entries(map)) {
      expect
        .soft(SENSITIVE_STORAGE_RE.test(key), `${scope} key=${key}`)
        .toBeFalsy();
      expect
        .soft(SENSITIVE_STORAGE_RE.test(value), `${scope} value of ${key}`)
        .toBeFalsy();
      expect.soft(value.includes("fce_create_alpha")).toBeFalsy();
      expect.soft(value.includes("SECRET-LEAK")).toBeFalsy();
      expect.soft(value.includes("cost-change-events")).toBeFalsy();
      expect.soft(value.includes(SPECIAL_PROJECT_ID)).toBeFalsy();
    }
  }
}

/**
 * 用途：安装剪贴板探针；无论原方法是否存在都必须可观测替换。
 * 二次开发：安装失败必须让测试失败，禁止 clipboard 缺失时仍 0/0 过关。
 */
async function installClipboardProbe(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as {
      __p10kClipRead?: number;
      __p10kClipWrite?: number;
      __p10kClipInstalled?: boolean;
    };
    w.__p10kClipRead = 0;
    w.__p10kClipWrite = 0;
    w.__p10kClipInstalled = false;

    // 无论原 clipboard 是否存在，都强制挂载可观测方法
    const clip =
      navigator.clipboard ??
      ({} as Clipboard);
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
    } catch {
      // 若已存在不可配置对象，直接在其上覆写方法
    }

    const target = navigator.clipboard;
    if (!target) {
      w.__p10kClipInstalled = false;
      return;
    }

    target.readText = async () => {
      w.__p10kClipRead = (w.__p10kClipRead ?? 0) + 1;
      return "";
    };
    target.writeText = async (_text: string) => {
      w.__p10kClipWrite = (w.__p10kClipWrite ?? 0) + 1;
    };
    w.__p10kClipInstalled = true;
  });
}

/**
 * 用途：强制断言剪贴板探针已安装，且读写次数为 0；console 无敏感片段。
 */
async function assertNoClipboardAndCleanConsole(
  page: Page,
  consoleLines: string[],
) {
  const clipOps = await page.evaluate(() => {
    const w = window as unknown as {
      __p10kClipRead?: number;
      __p10kClipWrite?: number;
      __p10kClipInstalled?: boolean;
    };
    return {
      installed: w.__p10kClipInstalled === true,
      read: w.__p10kClipRead ?? -1,
      write: w.__p10kClipWrite ?? -1,
    };
  });
  expect(clipOps.installed, "剪贴板探针必须安装成功").toBe(true);
  expect(clipOps.read).toBe(0);
  expect(clipOps.write).toBe(0);

  const joined = consoleLines.join("\n");
  expect(joined).not.toContain("SECRET-LEAK");
  expect(joined).not.toContain(SPECIAL_PROJECT_ID);
  expect(joined).not.toContain("/api/finance/business-bids/");
  expect(joined).not.toContain("fce_create_alpha");
}

function baseState(
  overrides: Partial<P10kAuthState> = {},
): P10kAuthState {
  return {
    bootstrapped: true,
    authRequired: true,
    session: meFor("finance", {
      username: "user_finance",
      isOwner: false,
      csrf: null,
    }),
    resumeCsrf: "e2e-p10k-csrf",
    forbiddenHits: [],
    p10bHits: [],
    p10cHits: [],
    p10kHits: [],
    p10jHits: [],
    externalHits: [],
    payloadByProject: {
      proj_biz_1: seedP10kPayload(),
      proj_biz_2: { items: [] },
      [SPECIAL_PROJECT_ID]: seedP10kPayload(),
    },
    costEntriesByProject: {
      proj_biz_1: [],
      proj_biz_2: [],
      [SPECIAL_PROJECT_ID]: [],
    },
    costSeq: 0,
    ...overrides,
  };
}

async function selectFinanceProject(page: Page, projectId: string) {
  await page.getByTestId("finance-list-item").evaluateAll((nodes, id) => {
    const el = nodes.find(
      (n) => n.getAttribute("data-project-id") === id,
    ) as HTMLElement | undefined;
    if (!el) {
      throw new Error(`列表中找不到项目: ${id}`);
    }
    el.click();
  }, projectId);
}

async function openFinanceAndSelect(page: Page, projectId: string) {
  await page.goto("/finance");
  await expect(page.getByTestId("finance-quote-page")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("finance-list-item").first()).toBeVisible({
    timeout: 15_000,
  });
  await selectFinanceProject(page, projectId);
  await expect(page.getByTestId("finance-detail")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("finance-p10k-panel")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("finance-p10k-panel")).toHaveAttribute(
    "data-project-id",
    projectId,
  );
}

/**
 * 用途：页面与 console 同时断言敏感片段不出现。
 */
function assertNoLeakInPageAndConsole(
  pageText: string,
  consoleLines: string[],
  extras: string[] = [],
) {
  const joinedConsole = consoleLines.join("\n");
  const needles = [
    SECRET_SNIPPET,
    "SECRET-LEAK",
    BACKEND_ERROR_CODE,
    "/api/finance/business-bids",
    "proj_biz_1",
    "fce_error_leak",
    "fce_p10k",
    ...extras,
  ];
  for (const n of needles) {
    expect(pageText, `页面不得含 ${n}`).not.toContain(n);
    expect(joinedConsole, `console 不得含 ${n}`).not.toContain(n);
  }
}

test.describe("P10K 财务项目成本变更记录前端", () => {
  test("首屏既有 P10B/P10C 且 P10K hits=[]；显式点击 1 次；P10C 写后仍 1；刷新 2 次；字段映射与限制声明", async ({
    page,
  }) => {
    const state = baseState();
    const consoleLines: string[] = [];
    page.on("console", (msg) => {
      consoleLines.push(msg.text());
    });
    await installClipboardProbe(page);
    await installP10kRoutes(page, state);

    await openFinanceAndSelect(page, "proj_biz_1");

    // 首屏：列表 + 明细 + cost-draft 存在；P10K 严格 0
    expect(state.p10bHits.some((h) => h === "GET /api/finance/business-bids")).toBeTruthy();
    expect(
      state.p10bHits.some((h) =>
        h.includes("/api/finance/business-bids/proj_biz_1"),
      ),
    ).toBeTruthy();
    expect(
      state.p10cHits.some((h) =>
        h.includes("/api/finance/business-bids/proj_biz_1/cost-draft"),
      ),
    ).toBeTruthy();
    expect(state.p10kHits).toEqual([]);
    expect(state.p10jHits).toEqual([]);

    await expect(page.getByTestId("finance-p10k-disclaimer")).toContainText(
      DISCLAIMER,
    );
    await expect(page.getByTestId("finance-p10k-open")).toBeVisible();
    await expect(page.getByTestId("finance-p10k-item")).toHaveCount(0);

    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-item").first()).toBeVisible({
      timeout: 15_000,
    });
    expect(state.p10kHits).toEqual([
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
    ]);

    const items = page.getByTestId("finance-p10k-item");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0).getByTestId("finance-p10k-item-action")).toHaveText(
      "新增成本条目",
    );
    await expect(
      items.nth(0).getByTestId("finance-p10k-item-entry-id"),
    ).toHaveText("fce_create_alpha");
    await expect(items.nth(0).getByTestId("finance-p10k-item-actor")).toHaveText(
      "本人",
    );
    await expect(items.nth(1).getByTestId("finance-p10k-item-action")).toHaveText(
      "修改成本条目",
    );
    await expect(items.nth(1).getByTestId("finance-p10k-item-actor")).toHaveText(
      "其他财务成员",
    );
    await expect(items.nth(2).getByTestId("finance-p10k-item-action")).toHaveText(
      "删除成本条目",
    );

    // 有效 occurredAt 不得退化为「时间未知」
    for (let i = 0; i < 3; i += 1) {
      const t = (
        await items.nth(i).getByTestId("finance-p10k-item-time").innerText()
      ).trim();
      expect(t.length).toBeGreaterThan(0);
      expect(t).not.toBe("时间未知");
    }

    const panelText = await page.getByTestId("finance-p10k-panel").innerText();
    expect(panelText).not.toContain("SECRET-LEAK");
    expect(panelText).not.toContain("amount");
    expect(panelText).not.toContain("user_finance");

    // P10C 真实新增成功后不得自动再请求 P10K
    const p10cBeforeWrite = state.p10cHits.length;
    await expect(page.getByTestId("finance-cost-panel")).toBeVisible();
    await page.getByTestId("finance-cost-create-category").selectOption("material");
    await page.getByTestId("finance-cost-create-name").fill("P10K写后零自动条目");
    await page.getByTestId("finance-cost-create-amount").fill("100.50");
    await page.getByTestId("finance-cost-create-remark").fill("写后断言");
    await page.getByTestId("finance-cost-create-submit").click();

    // 等待 P10C 写请求 + 草案重读完成
    await expect
      .poll(
        () =>
          state.p10cHits.some(
            (h) =>
              h.startsWith("POST ") &&
              h.includes("/api/finance/business-bids/proj_biz_1/cost-entries"),
          ),
        { timeout: 15_000 },
      )
      .toBe(true);
    await expect(page.getByTestId("finance-cost-entry")).toHaveCount(1, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-cost-entry-name")).toHaveText(
      "P10K写后零自动条目",
    );
    // 草案至少再 GET 一次（写后重读）
    await expect
      .poll(
        () =>
          state.p10cHits.filter((h) =>
            h.includes("/api/finance/business-bids/proj_biz_1/cost-draft"),
          ).length,
        { timeout: 15_000 },
      )
      .toBeGreaterThanOrEqual(2);
    expect(state.p10cHits.length).toBeGreaterThan(p10cBeforeWrite);

    // 关键：写后 p10kHits 仍精确为 1
    expect(state.p10kHits).toEqual([
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
    ]);

    await page.getByTestId("finance-p10k-reload").click();
    await expect
      .poll(() => state.p10kHits.length, { timeout: 15_000 })
      .toBe(2);
    expect(state.p10kHits).toEqual([
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
    ]);

    expect(state.p10jHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
    await assertNoClipboardAndCleanConsole(page, consoleLines);
  });

  test("特殊项目 ID 路径完整保留 encodeURIComponent（含 %20/%2F）", async ({
    page,
  }) => {
    const state = baseState();
    await installP10kRoutes(page, state);

    await openFinanceAndSelect(page, SPECIAL_PROJECT_ID);
    expect(state.p10kHits).toEqual([]);

    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-item").first()).toBeVisible({
      timeout: 15_000,
    });

    const expectedPath = `/api/finance/business-bids/${SPECIAL_PROJECT_ENCODED}/cost-change-events`;
    expect(SPECIAL_PROJECT_ENCODED).toContain("%20");
    expect(SPECIAL_PROJECT_ENCODED).toContain("%2F");
    expect(state.p10kHits).toEqual([`GET ${expectedPath}`]);
    // 完整 pathname 含编码结果，不能只匹配普通 ID
    expect(state.p10kHits[0]).toContain("%20");
    expect(state.p10kHits[0]).toContain("%2F");
    expect(state.p10jHits).toEqual([]);
  });

  test("空态与未知枚举脱敏；后端泄密错误固定中文且 console 无泄漏", async ({
    page,
  }) => {
    const state = baseState({
      payloadByProject: {
        proj_biz_1: {
          items: [
            {
              action: "mystery",
              entryId: "fce_unknown",
              actorScope: "admin",
              occurredAt: "not-a-timestamp",
            },
          ],
        },
        proj_biz_2: { items: [] },
        [SPECIAL_PROJECT_ID]: { items: [] },
      },
    });
    await installP10kRoutes(page, state);

    await openFinanceAndSelect(page, "proj_biz_1");
    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-item")).toHaveCount(1, {
      timeout: 15_000,
    });
    await expect(
      page.getByTestId("finance-p10k-item-action"),
    ).toHaveText("—");
    await expect(
      page.getByTestId("finance-p10k-item-actor"),
    ).toHaveText("—");
    await expect(page.getByTestId("finance-p10k-item-time")).toHaveText(
      "时间未知",
    );
    const text = await page.getByTestId("finance-p10k-panel").innerText();
    expect(text).not.toContain("mystery");
    expect(text).not.toContain("admin");

    // 空态
    await page.unrouteAll({ behavior: "ignoreErrors" });
    const emptyState = baseState({
      payloadByProject: {
        proj_biz_1: { items: [] },
        proj_biz_2: { items: [] },
        [SPECIAL_PROJECT_ID]: { items: [] },
      },
    });
    await installP10kRoutes(page, emptyState);
    await openFinanceAndSelect(page, "proj_biz_1");
    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-empty")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-p10k-item")).toHaveCount(0);

    // 错误脱敏：页面 + console 同时断言
    await page.unrouteAll({ behavior: "ignoreErrors" });
    const errConsole: string[] = [];
    page.on("console", (msg) => {
      errConsole.push(msg.text());
    });
    const errState = baseState({
      forceErrorByProject: { proj_biz_1: true },
    });
    await installP10kRoutes(page, errState);
    await openFinanceAndSelect(page, "proj_biz_1");
    await page.getByTestId("finance-p10k-open").click();
    const err = page.getByTestId("finance-p10k-error");
    await expect(err).toBeVisible({ timeout: 15_000 });
    await expect(err).toContainText(FIXED_ERROR);
    await expect(err).not.toContainText("SECRET-LEAK");
    await expect(err).not.toContainText("fce_");
    await expect(err).not.toContainText("proj_biz_1");
    await expect(err).not.toContainText("/api/finance");
    await expect(err).not.toContainText(BACKEND_ERROR_CODE);

    const pageText = await page.getByTestId("finance-p10k-panel").innerText();
    assertNoLeakInPageAndConsole(pageText, errConsole, [
      "fce_unknown",
      "entryId=fce_p10k",
    ]);
  });

  test("切项目立即清空收起；迟到旧响应不得写入；新项目零自动 P10K", async ({
    page,
  }) => {
    let releaseHold: (() => void) | null = null;
    const hold = new Promise<void>((resolve) => {
      releaseHold = resolve;
    });

    const state = baseState({
      p10kHoldByProject: {
        proj_biz_1: hold,
      },
      payloadByProject: {
        proj_biz_1: {
          items: [
            {
              action: "create",
              entryId: "fce_late_only",
              actorScope: "self",
              occurredAt: "2026-07-14T10:00:00.000Z",
            },
          ],
        },
        proj_biz_2: {
          items: [
            {
              action: "update",
              entryId: "fce_proj2_only",
              actorScope: "other",
              occurredAt: "2026-07-14T11:00:00.000Z",
            },
          ],
        },
        [SPECIAL_PROJECT_ID]: { items: [] },
      },
    });
    await installP10kRoutes(page, state);

    await openFinanceAndSelect(page, "proj_biz_1");
    await page.getByTestId("finance-p10k-open").click();
    // 请求已发出但仍在 hold
    await expect
      .poll(() => state.p10kHits.length, { timeout: 15_000 })
      .toBe(1);
    expect(state.p10kHits[0]).toContain("proj_biz_1");

    // 切到项目乙：立即收起清空
    await selectFinanceProject(page, "proj_biz_2");
    await expect(page.getByTestId("finance-detail")).toBeVisible();
    await expect(page.getByTestId("finance-p10k-panel")).toBeVisible();
    await expect(page.getByTestId("finance-p10k-open")).toBeVisible();
    await expect(page.getByTestId("finance-p10k-item")).toHaveCount(0);
    await expect(page.getByTestId("finance-p10k-list")).toHaveCount(0);

    // 释放旧响应：不得出现 fce_late_only
    releaseHold?.();
    // 给微任务一点时间写入（用 expect.poll 而非 sleep）
    await expect
      .poll(async () => {
        const body = await page.getByTestId("finance-p10k-panel").innerText();
        return body.includes("fce_late_only");
      }, { timeout: 3_000 })
      .toBe(false);

    // 新项目仍零自动 P10K（仅旧项目 1 次）
    expect(state.p10kHits).toEqual([
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
    ]);
    await expect(page.getByTestId("finance-p10k-open")).toBeVisible();

    // 新项目显式打开应只读 proj_biz_2
    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-item-entry-id")).toHaveText(
      "fce_proj2_only",
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("finance-p10k-panel")).not.toContainText(
      "fce_late_only",
    );
    expect(state.p10kHits).toEqual([
      "GET /api/finance/business-bids/proj_biz_1/cost-change-events",
      "GET /api/finance/business-bids/proj_biz_2/cost-change-events",
    ]);
    expect(state.p10jHits).toEqual([]);
  });

  test("P10J/未知 API/外网阻断可观测；字体不计 externalHits", async ({
    page,
  }) => {
    const state = baseState();
    await installP10kRoutes(page, state);

    await openFinanceAndSelect(page, "proj_biz_1");
    await page.getByTestId("finance-p10k-open").click();
    await expect(page.getByTestId("finance-p10k-item").first()).toBeVisible({
      timeout: 15_000,
    });
    expect(state.p10jHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);

    // 探针：分别请求 P10J、/api/projects、/api/unknown-local-probe、外网
    await page.evaluate(async () => {
      const hits: string[] = [];
      try {
        await fetch("/api/finance/cost-change-events");
        hits.push("p10j");
      } catch {
        hits.push("p10j-err");
      }
      try {
        await fetch("/api/projects");
        hits.push("projects");
      } catch {
        hits.push("projects-err");
      }
      try {
        await fetch("/api/unknown-local-probe");
        hits.push("unknown");
      } catch {
        hits.push("unknown-err");
      }
      try {
        await fetch("https://example.invalid/p10k-probe", {
          method: "GET",
          mode: "no-cors",
        });
        hits.push("ext");
      } catch {
        hits.push("ext-err");
      }
      (window as unknown as { __p10kProbeHits?: string[] }).__p10kProbeHits =
        hits;
    });

    // 分别精确证明三者各自计数/阻断，禁止 projects OR unknown 弱断言
    await expect
      .poll(() => state.p10jHits.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    expect(
      state.p10jHits.some((h) => h.includes("/api/finance/cost-change-events")),
    ).toBeTruthy();
    expect(
      state.p10jHits.every((h) => h.includes("/api/finance/cost-change-events")),
    ).toBeTruthy();

    await expect
      .poll(
        () =>
          state.forbiddenHits.filter((h) => h.includes("/api/projects")).length,
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0);
    expect(
      state.forbiddenHits.some((h) => h.includes("/api/projects")),
    ).toBeTruthy();

    await expect
      .poll(
        () =>
          state.forbiddenHits.filter((h) =>
            h.includes("/api/unknown-local-probe"),
          ).length,
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0);
    expect(
      state.forbiddenHits.some((h) => h.includes("/api/unknown-local-probe")),
    ).toBeTruthy();

    await expect
      .poll(() => state.externalHits.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    expect(
      state.externalHits.some(
        (h) => h.includes("example.invalid") && h.includes("p10k-probe"),
      ),
    ).toBeTruthy();
    expect(
      state.externalHits.every(
        (h) =>
          !h.includes("fonts.googleapis.com") &&
          !h.includes("fonts.gstatic.com"),
      ),
    ).toBeTruthy();
  });

  for (const role of ["bid_writer", "hr", "bidder"] as AuthRole[]) {
    test(`required 非 finance（${role}）直达 /finance 零 P10K`, async ({
      page,
    }) => {
      const state = baseState({
        session: meFor(role, {
          isOwner: role === "bid_writer",
          csrf: null,
        }),
      });
      await installP10kRoutes(page, state);
      await page.goto("/finance");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      // 受限页或无财务面板
      await expect(page.getByTestId("finance-p10k-panel")).toHaveCount(0);
      expect(state.p10kHits).toEqual([]);
      expect(state.p10jHits).toEqual([]);
    });
  }

  test("仅 owner 与 disabled 直达 /finance 零 P10K", async ({ page }) => {
    // 仅 owner：bid_writer + isOwner（非 finance 角色）
    const ownerState = baseState({
      session: meFor("bid_writer", { isOwner: true, csrf: null }),
    });
    await installP10kRoutes(page, ownerState);
    await page.goto("/finance");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-p10k-panel")).toHaveCount(0);
    expect(ownerState.p10kHits).toEqual([]);

    await page.unrouteAll({ behavior: "ignoreErrors" });
    // disabled：authRequired=false；未知 API 应记 forbiddenHits 而非宽泛放行
    const disabledState = baseState({
      authRequired: false,
      session: null,
      bootstrapped: true,
    });
    await installP10kRoutes(page, disabledState);
    await page.goto("/finance");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-p10k-panel")).toHaveCount(0);
    expect(disabledState.p10kHits).toEqual([]);

    // 显式证明 disabled 下未知 API 被 403 计数（禁止 json([]) 假绿）
    await page.evaluate(async () => {
      try {
        await fetch("/api/unknown-local-probe");
      } catch {
        // 网络层失败也可接受，路由层应已记 forbiddenHits
      }
    });
    await expect
      .poll(
        () =>
          disabledState.forbiddenHits.filter((h) =>
            h.includes("/api/unknown-local-probe"),
          ).length,
        { timeout: 10_000 },
      )
      .toBeGreaterThan(0);
  });
});
