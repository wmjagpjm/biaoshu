/**
 * 模块：P10C 财务成本草案前端 E2E
 * 用途：验收成本草案 CRUD、元转分、毛利快照、网络白名单、角色脱敏与无敏感存储。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:finance-cost-draft。
 * 二次开发：仅桩 P10B/P10C 财务端点；禁止快照整页；禁止回退 projects/editor-state。
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

type CostEntry = {
  id: string;
  category: "labor" | "material" | "service" | "other";
  name: string;
  amountFen: number;
  remark: string;
  createdAt: string;
  updatedAt: string;
};

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

/** 既有 index.html 引入的字体（非本任务引入，断言时排除） */
function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
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
      quoteRowCount: 2,
      quoteTotal: 128000.5,
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
      projectId: "proj_biz_3",
      name: "示例商务标丙",
      industry: "交通",
      status: "reviewing",
      updatedAt: "2026-07-12T09:00:00+00:00",
      quoteRowCount: 0,
      quoteTotal: 5000,
    },
  ],
};

const SAMPLE_DETAIL_1 = {
  projectId: "proj_biz_1",
  name: "示例商务标甲",
  industry: "能源",
  status: "writing",
  updatedAt: "2026-07-14T08:00:00+00:00",
  quoteRowCount: 2,
  quoteTotal: 128000.5,
  quoteRows: [
    {
      id: "row_1",
      name: "设备供货",
      unit: "套",
      quantity: "2",
      unitPrice: "50000",
      amount: 100000,
      remark: "含税",
    },
    {
      id: "row_2",
      name: "安装调试",
      unit: "项",
      quantity: "1",
      unitPrice: "28000.5",
      amount: 28000.5,
      remark: "",
    },
  ],
  quoteNotes: "本报价仅含设备与安装，不含运维。",
};

type CostState = {
  entries: CostEntry[];
  /** 报价合计（分），可按项目覆盖 */
  quoteFenByProject: Record<string, number>;
  seq: number;
};

function buildDraft(projectId: string, cost: CostState) {
  const quoteTotalFen = cost.quoteFenByProject[projectId] ?? 0;
  // 简化：可变条目仅归属 proj_biz_1；零报价/负毛利用例通过重置 entries 驱动
  const entries = projectId === "proj_biz_1" ? cost.entries : [];
  const costSum = entries.reduce((s, e) => s + e.amountFen, 0);
  const grossProfitFen = quoteTotalFen - costSum;
  let grossMarginBasisPoints: number | null = null;
  if (quoteTotalFen > 0) {
    // 桩侧整数基点，仅供展示断言；前端不得自行用报价/成本重算
    grossMarginBasisPoints = Math.round(
      (grossProfitFen * 10000) / quoteTotalFen,
    );
  }
  const name =
    SAMPLE_LIST.items.find((i) => i.projectId === projectId)?.name ?? projectId;
  return {
    projectId,
    projectName: name,
    quoteTotalFen,
    costTotalFen: costSum,
    grossProfitFen,
    grossMarginBasisPoints,
    costEntries: entries.map((e) => ({
      id: e.id,
      category: e.category,
      name: e.name,
      amountFen: e.amountFen,
      remark: e.remark,
      createdAt: e.createdAt,
      updatedAt: e.updatedAt,
    })),
  };
}

type FinanceAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  financeHits: string[];
  writeBodies: Array<{ method: string; path: string; body: unknown }>;
  cost: CostState;
  forceCostError?: boolean;
  /**
   * 用途：按项目延迟报价明细响应，便于断言切换瞬间不会挂载错配成本面板。
   * 二次开发：仅 E2E 使用；生产路径无此字段。
   */
  detailHoldByProject?: Record<string, Promise<void>>;
};

/**
 * 用途：安装 auth + 财务报价 + 成本草案桩；阻断通用业务 API。
 */
async function installCostRoutes(page: Page, state: FinanceAuthState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const path = url.pathname;
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(url.href)) {
      await route.continue();
      return;
    }

    if (host !== "127.0.0.1" && host !== "localhost") {
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    if (
      path.startsWith("/api/projects") ||
      path.includes("editor-state") ||
      path.startsWith("/api/settings") ||
      path.startsWith("/api/files") ||
      path.startsWith("/api/export")
    ) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "禁止业务回退" } },
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

    if (!state.authRequired) {
      if (path === "/api/health" && method === "GET") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          defaultWorkspaceId: "ws_e2e",
        });
        return;
      }
      if (path.startsWith("/api/finance/")) {
        state.financeHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      await json(route, []);
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
        csrfToken: state.resumeCsrf ?? "e2e-finance-csrf",
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

    if (path === "/api/finance/business-bids" && method === "GET") {
      state.financeHits.push(`${method} ${path}`);
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

    // 成本草案 GET
    const draftMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-draft$/,
    );
    if (draftMatch && method === "GET") {
      state.financeHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (state.forceCostError) {
        await json(
          route,
          {
            detail: {
              code: "server_error",
              message: "C:\\\\secret\\\\path amountFen=999 apiKey=leak",
            },
          },
          500,
        );
        return;
      }
      const projectId = decodeURIComponent(draftMatch[1]);
      if (
        projectId !== "proj_biz_1" &&
        projectId !== "proj_biz_2" &&
        projectId !== "proj_biz_3"
      ) {
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
      await json(route, buildDraft(projectId, state.cost));
      return;
    }

    // POST 创建条目
    const createMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-entries$/,
    );
    if (createMatch && method === "POST") {
      state.financeHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
      } catch {
        body = {};
      }
      state.writeBodies.push({ method, path, body });
      const now = "2026-07-14T10:00:00+00:00";
      state.cost.seq += 1;
      const entry: CostEntry = {
        id: `fce_${state.cost.seq}`,
        category: body.category as CostEntry["category"],
        name: String(body.name ?? ""),
        amountFen: Number(body.amountFen),
        remark: String(body.remark ?? ""),
        createdAt: now,
        updatedAt: now,
      };
      state.cost.entries = [entry, ...state.cost.entries];
      await json(route, entry, 201);
      return;
    }

    // PATCH / DELETE 条目
    const entryMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)\/cost-entries\/([^/]+)$/,
    );
    if (entryMatch && (method === "PATCH" || method === "DELETE")) {
      state.financeHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      const entryId = decodeURIComponent(entryMatch[2]);
      const idx = state.cost.entries.findIndex((e) => e.id === entryId);
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
        state.writeBodies.push({ method, path, body: null });
        state.cost.entries = state.cost.entries.filter((e) => e.id !== entryId);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(req.postData() || "{}") as Record<string, unknown>;
      } catch {
        body = {};
      }
      state.writeBodies.push({ method, path, body });
      const prev = state.cost.entries[idx];
      const next: CostEntry = {
        ...prev,
        category:
          body.category !== undefined
            ? (body.category as CostEntry["category"])
            : prev.category,
        name: body.name !== undefined ? String(body.name) : prev.name,
        amountFen:
          body.amountFen !== undefined
            ? Number(body.amountFen)
            : prev.amountFen,
        remark:
          body.remark !== undefined ? String(body.remark) : prev.remark,
        updatedAt: "2026-07-14T11:00:00+00:00",
      };
      state.cost.entries = [
        ...state.cost.entries.slice(0, idx),
        next,
        ...state.cost.entries.slice(idx + 1),
      ];
      await json(route, next);
      return;
    }

    // 报价明细 GET（须在 cost-entries 之后匹配，避免被误吞）
    const detailMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)$/,
    );
    if (detailMatch && method === "GET") {
      state.financeHits.push(`${method} ${path}`);
      if (!isFinance) {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      const projectId = decodeURIComponent(detailMatch[1]);
      // 切换一致性用例：在放行前挂起明细，期间不得出现成本请求/旧面板
      const hold = state.detailHoldByProject?.[projectId];
      if (hold) {
        await hold;
      }
      if (projectId === "proj_biz_1") {
        await json(route, SAMPLE_DETAIL_1);
        return;
      }
      if (projectId === "proj_biz_2" || projectId === "proj_biz_3") {
        const summary = SAMPLE_LIST.items.find(
          (i) => i.projectId === projectId,
        );
        await json(route, {
          ...summary,
          quoteRows: [],
          quoteNotes: "",
        });
        return;
      }
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

    if (!state.session) {
      await json(
        route,
        { detail: { code: "auth_required", message: "需要登录" } },
        401,
      );
      return;
    }

    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "role_forbidden", message: "未授权业务接口" } },
      403,
    );
  });
}

async function assertNoSensitiveStorage(page: Page) {
  const storage = await page.evaluate(() => {
    const dump = (store: Storage) => {
      const out: Record<string, string> = {};
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        if (key) out[key] = store.getItem(key) ?? "";
      }
      return out;
    };
    return {
      local: dump(window.localStorage),
      session: dump(window.sessionStorage),
    };
  });

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
      // 不得把成本/毛利/备注写入浏览器存储
      expect
        .soft(
          /costTotal|grossProfit|amountFen|costEntries|毛利|成本备注/i.test(
            key + value,
          ),
          `${scope} 不得含成本敏感字段 key=${key}`,
        )
        .toBeFalsy();
    }
  }
}

function defaultCostState(): CostState {
  return {
    entries: [],
    quoteFenByProject: {
      // 128000.50 元 → 12800050 分
      proj_biz_1: 12_800_050,
      proj_biz_2: 0,
      // 5000 元 → 500000 分；后续可加高成本做负毛利
      proj_biz_3: 500_000,
    },
    seq: 0,
  };
}

test.describe("P10C 财务成本草案前端", () => {
  test("选择项目后加载草案；元转分创建；编辑删除刷新；网络白名单", async ({
    page,
  }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", {
        username: "user_finance",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-finance-cost-csrf",
      forbiddenHits: [],
      financeHits: [],
      writeBodies: [],
      cost: defaultCostState(),
    };
    await installCostRoutes(page, state);

    await page.goto("/finance");
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("finance-list-item").first().click();
    await expect(page.getByTestId("finance-detail")).toBeVisible();
    await expect(page.getByTestId("finance-cost-panel")).toBeVisible();
    await expect(page.getByTestId("finance-cost-snapshot")).toBeVisible();
    await expect(page.getByTestId("finance-cost-empty")).toBeVisible();
    await expect(page.getByTestId("finance-cost-quote-total")).toHaveText(
      "¥128,000.50",
    );
    await expect(page.getByTestId("finance-cost-total")).toHaveText("¥0.00");
    await expect(page.getByTestId("finance-cost-margin")).toHaveText(
      "100.00%",
    );

    // GET cost-draft 已发生
    expect(
      state.financeHits.some((h) =>
        h.includes("/api/finance/business-bids/proj_biz_1/cost-draft"),
      ),
    ).toBeTruthy();

    // 非法金额：不发写网络
    const writesBefore = state.writeBodies.length;
    await page.getByTestId("finance-cost-create-name").fill("坏金额");
    await page.getByTestId("finance-cost-create-amount").fill("12.345");
    await page.getByTestId("finance-cost-create-submit").click();
    await expect(page.getByTestId("finance-cost-write-error")).toContainText(
      "最多两位小数",
    );
    expect(state.writeBodies.length).toBe(writesBefore);

    await page.getByTestId("finance-cost-create-amount").fill("abc");
    await page.getByTestId("finance-cost-create-submit").click();
    await expect(page.getByTestId("finance-cost-write-error")).toBeVisible();
    expect(state.writeBodies.length).toBe(writesBefore);

    // 合法：80000.50 → 8000050
    await page.getByTestId("finance-cost-create-category").selectOption("material");
    await page.getByTestId("finance-cost-create-name").fill("设备采购");
    await page.getByTestId("finance-cost-create-amount").fill("80000.50");
    await page.getByTestId("finance-cost-create-remark").fill("草案备注甲");
    await page.getByTestId("finance-cost-create-submit").click();

    await expect(page.getByTestId("finance-cost-entries-table")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("finance-cost-entry")).toHaveCount(1);
    await expect(page.getByTestId("finance-cost-entry-name")).toHaveText(
      "设备采购",
    );
    await expect(page.getByTestId("finance-cost-entry-amount")).toHaveText(
      "¥80,000.50",
    );
    await expect(page.getByTestId("finance-cost-entry-category")).toHaveText(
      "材料",
    );

    const createWrite = state.writeBodies.find(
      (w) => w.method === "POST" && String(w.path).includes("cost-entries"),
    );
    expect(createWrite).toBeTruthy();
    expect(createWrite?.body).toMatchObject({
      category: "material",
      name: "设备采购",
      amountFen: 8_000_050,
      remark: "草案备注甲",
    });
    // 创建后应再次 GET draft
    const draftGets = state.financeHits.filter(
      (h) =>
        h.startsWith("GET ") &&
        h.includes("/cost-draft"),
    );
    expect(draftGets.length).toBeGreaterThanOrEqual(2);

    // 编辑 PATCH
    await page.getByTestId("finance-cost-edit-btn").click();
    await expect(page.getByTestId("finance-cost-edit-form")).toBeVisible();
    await page.getByTestId("finance-cost-edit-name").fill("设备采购(更新)");
    await page.getByTestId("finance-cost-edit-amount").fill("90000");
    await page.getByTestId("finance-cost-edit-submit").click();
    await expect(page.getByTestId("finance-cost-entry-name")).toHaveText(
      "设备采购(更新)",
    );
    await expect(page.getByTestId("finance-cost-entry-amount")).toHaveText(
      "¥90,000.00",
    );
    const patchWrite = state.writeBodies.find((w) => w.method === "PATCH");
    expect(patchWrite?.body).toMatchObject({
      name: "设备采购(更新)",
      amountFen: 9_000_000,
    });

    // 删除 DELETE
    await page.getByTestId("finance-cost-delete-btn").click();
    await expect(page.getByTestId("finance-cost-delete-confirm")).toBeVisible();
    await page.getByTestId("finance-cost-delete-confirm-btn").click();
    await expect(page.getByTestId("finance-cost-empty")).toBeVisible();
    expect(state.writeBodies.some((w) => w.method === "DELETE")).toBeTruthy();

    // 网络白名单：无禁止路径
    expect(
      state.forbiddenHits,
      `意外业务请求: ${state.forbiddenHits.join(", ")}`,
    ).toEqual([]);
    // 仅允许 finance business-bids 与 cost 路径
    for (const hit of state.financeHits) {
      expect(hit).toMatch(
        /\/api\/finance\/business-bids(\/|$)/,
      );
    }

    await assertNoSensitiveStorage(page);
  });

  test("无成本、报价<=0 毛利率占位、负毛利可读；错误脱敏", async ({
    page,
  }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { username: "user_finance", csrf: null }),
      resumeCsrf: "e2e-finance-cost-csrf",
      forbiddenHits: [],
      financeHits: [],
      writeBodies: [],
      cost: defaultCostState(),
    };
    await installCostRoutes(page, state);

    await page.goto("/finance");
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });

    // 报价=0 项目
    await page.getByTestId("finance-list-item").nth(1).click();
    await expect(page.getByTestId("finance-cost-panel")).toBeVisible();
    await expect(page.getByTestId("finance-cost-empty")).toBeVisible();
    await expect(page.getByTestId("finance-cost-margin")).toHaveText(
      "—（报价合计不大于零）",
    );
    await expect(page.getByTestId("finance-cost-quote-total")).toHaveText(
      "¥0.00",
    );

    // 负毛利：在 proj_biz_3 先切回甲创建高成本后... 改用甲项目直接注入高成本
    // 切回甲，预置超高成本条目
    state.cost.entries = [
      {
        id: "fce_high",
        category: "service",
        name: "外包服务",
        amountFen: 200_000_00,
        remark: "",
        createdAt: "2026-07-14T09:00:00+00:00",
        updatedAt: "2026-07-14T09:00:00+00:00",
      },
    ];
    await page.getByTestId("finance-list-item").first().click();
    await expect(page.getByTestId("finance-cost-gross-profit")).toBeVisible();
    await expect(page.getByTestId("finance-cost-neg-profit-hint")).toBeVisible();
    await expect(page.getByTestId("finance-cost-gross-profit")).toContainText(
      "-",
    );
    // 必须明示「不是已审批/最终利润」类结论（允许免责声明中出现这些词作否定）
    await expect(page.getByTestId("finance-cost-panel")).toContainText(
      "不是已审批结论",
    );
    await expect(page.getByTestId("finance-cost-panel")).toContainText(
      "最终利润",
    );
    await expect(page.getByTestId("finance-cost-panel")).toContainText(
      "毛利快照",
    );

    // 错误脱敏：强制 500
    state.forceCostError = true;
    await page.getByTestId("finance-list-item").nth(1).click();
    await expect(page.getByTestId("finance-cost-error")).toBeVisible();
    await expect(page.getByTestId("finance-cost-error")).toContainText(
      "加载失败",
    );
    await expect(page.getByTestId("finance-cost-error")).not.toContainText(
      "secret",
    );
    await expect(page.getByTestId("finance-cost-error")).not.toContainText(
      "apiKey",
    );
    await expect(page.getByTestId("finance-cost-error")).not.toContainText(
      "amountFen",
    );

    await assertNoSensitiveStorage(page);
  });

  test("非 finance 无成本面板与财务写请求", async ({ page }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", {
        username: "user_bid_writer",
        isOwner: true,
        csrf: null,
      }),
      forbiddenHits: [],
      financeHits: [],
      writeBodies: [],
      cost: defaultCostState(),
    };
    await installCostRoutes(page, state);

    await page.goto("/finance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-quote-page")).toHaveCount(0);
    await expect(page.getByTestId("finance-cost-panel")).toHaveCount(0);
    expect(state.financeHits).toEqual([]);
    expect(state.writeBodies).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("从项目A切到B：明细未就绪前不挂载旧成本面板、不发起错配草案请求", async ({
    page,
  }) => {
    let releaseBDetail!: () => void;
    const holdBDetail = new Promise<void>((resolve) => {
      releaseBDetail = resolve;
    });

    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", {
        username: "user_finance",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-finance-cost-csrf",
      forbiddenHits: [],
      financeHits: [],
      writeBodies: [],
      cost: {
        ...defaultCostState(),
        // A 有成本，B 空成本，便于最终断言只显示 B 草案
        entries: [
          {
            id: "fce_a_only",
            category: "labor",
            name: "仅甲项目人工",
            amountFen: 1_000_00,
            remark: "不得出现在乙",
            createdAt: "2026-07-14T09:00:00+00:00",
            updatedAt: "2026-07-14T09:00:00+00:00",
          },
        ],
      },
      detailHoldByProject: {
        proj_biz_2: holdBDetail,
      },
    };
    await installCostRoutes(page, state);

    await page.goto("/finance");
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });

    // 选中甲：成本面板就绪
    await page.getByTestId("finance-list-item").first().click();
    await expect(page.getByTestId("finance-cost-panel")).toBeVisible();
    await expect(page.getByTestId("finance-cost-panel")).toHaveAttribute(
      "data-project-id",
      "proj_biz_1",
    );
    await expect(page.getByTestId("finance-cost-entry-name")).toHaveText(
      "仅甲项目人工",
    );

    const countDraft = (projectId: string) =>
      state.financeHits.filter(
        (h) =>
          h.startsWith("GET ") &&
          h.includes(`/api/finance/business-bids/${projectId}/cost-draft`),
      ).length;
    const aDraftBefore = countDraft("proj_biz_1");
    const bDraftBefore = countDraft("proj_biz_2");
    expect(aDraftBefore).toBeGreaterThanOrEqual(1);
    expect(bDraftBefore).toBe(0);

    // 切到乙：明细挂起期间不得保留甲成本面板，也不得抢先请求乙草案
    await page.getByTestId("finance-list-item").nth(1).click();
    await expect(page.getByTestId("finance-cost-panel")).toHaveCount(0);
    await expect(page.getByText("仅甲项目人工")).toHaveCount(0);

    // 等待乙明细请求已发出但仍被挂起
    await expect
      .poll(() =>
        state.financeHits.some(
          (h) => h === "GET /api/finance/business-bids/proj_biz_2",
        ),
      )
      .toBeTruthy();

    // 挂起期间：甲草案次数不变，乙草案仍为 0
    expect(countDraft("proj_biz_1")).toBe(aDraftBefore);
    expect(countDraft("proj_biz_2")).toBe(0);
    await expect(page.getByTestId("finance-cost-panel")).toHaveCount(0);

    // 放行乙明细后，只显示乙草案（空成本、零报价占位）
    releaseBDetail();
    await expect(page.getByTestId("finance-cost-panel")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("finance-cost-panel")).toHaveAttribute(
      "data-project-id",
      "proj_biz_2",
    );
    await expect(page.getByTestId("finance-cost-empty")).toBeVisible();
    await expect(page.getByText("仅甲项目人工")).toHaveCount(0);
    await expect(page.getByTestId("finance-cost-margin")).toHaveText(
      "—（报价合计不大于零）",
    );

    // 最终：仅新增乙草案 GET；甲草案次数在切换后不再增加
    expect(countDraft("proj_biz_2")).toBeGreaterThanOrEqual(1);
    expect(countDraft("proj_biz_1")).toBe(aDraftBefore);
    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
