/**
 * 模块：P10B 财务报价前端只读 E2E
 * 用途：验收 finance 入口/列表/明细、非财务与 disabled 受限、网络仅专用端点、存储无敏感字段。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:finance-role。
 * 二次开发：required 场景用 route 桩；禁止回退通用 projects/editor-state/settings。
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
      // 覆盖 ALLOWED_STATUS 中文映射（编写中）
      status: "writing",
      updatedAt: "2026-07-14T08:00:00+00:00",
      quoteRowCount: 2,
      quoteTotal: 128000.5,
    },
    {
      projectId: "proj_biz_2",
      name: "示例商务标乙",
      industry: "通用",
      // 覆盖 analyzing → 分析中
      status: "analyzing",
      updatedAt: "2026-07-13T12:30:00+00:00",
      quoteRowCount: 0,
      quoteTotal: 0,
    },
    {
      projectId: "proj_biz_3",
      name: "示例商务标丙",
      industry: "交通",
      // 覆盖 reviewing → 审核中
      status: "reviewing",
      updatedAt: "2026-07-12T09:00:00+00:00",
      quoteRowCount: 0,
      quoteTotal: 0,
    },
  ],
};

const SAMPLE_DETAIL = {
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

type FinanceAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  /** 记录业务侧禁止路径是否被请求 */
  forbiddenHits: string[];
  /** 记录财务端点命中 */
  financeHits: string[];
};

/**
 * 用途：安装 required/disabled 握手与 finance 专用接口桩；阻断通用业务 API。
 */
async function installFinanceRoutes(page: Page, state: FinanceAuthState) {
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

    // 记录禁止的通用业务回退
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
      // disabled：仅 health 等兼容；财务接口仍应 403（本页不会在 canAccessFinance=false 时调用）
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

    if (path === "/api/finance/business-bids" && method === "GET") {
      state.financeHits.push(`${method} ${path}`);
      if (!state.session || state.session.workspaces[0]?.role !== "finance") {
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

    const detailMatch = path.match(
      /^\/api\/finance\/business-bids\/([^/]+)$/,
    );
    if (detailMatch && method === "GET") {
      state.financeHits.push(`${method} ${path}`);
      if (!state.session || state.session.workspaces[0]?.role !== "finance") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      const projectId = decodeURIComponent(detailMatch[1]);
      if (projectId === "proj_biz_1") {
        await json(route, SAMPLE_DETAIL);
        return;
      }
      if (projectId === "proj_biz_2" || projectId === "proj_biz_3") {
        const summary = SAMPLE_LIST.items.find((i) => i.projectId === projectId);
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

    // 其它 API：默认 403，防止财务页静默依赖业务接口
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
    }
  }
}

test.describe("P10B 财务报价前端", () => {
  test("finance 可见入口并渲染列表与明细；仅专用端点", async ({ page }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", {
        username: "user_finance",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-finance-resume",
      forbiddenHits: [],
      financeHits: [],
    };
    await installFinanceRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("navigation", { name: "主导航" })).toContainText(
      "财务报价",
    );
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("标书生成");

    await page.getByRole("navigation", { name: "主导航" }).getByText("财务报价").click();
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "财务报价" })).toBeVisible();
    await expect(page.getByTestId("finance-list")).toBeVisible();
    await expect(page.getByTestId("finance-list-item")).toHaveCount(3);
    await expect(page.getByText("示例商务标甲")).toBeVisible();
    await expect(page.getByText("128,000.50").first()).toBeVisible();

    // 状态中文映射：不得泄露 writing/analyzing/reviewing 英文内部码
    await expect(page.getByTestId("finance-list")).toContainText("编写中");
    await expect(page.getByTestId("finance-list")).toContainText("分析中");
    await expect(page.getByTestId("finance-list")).toContainText("审核中");
    await expect(page.getByTestId("finance-list")).not.toContainText("writing");
    await expect(page.getByTestId("finance-list")).not.toContainText(
      "analyzing",
    );
    await expect(page.getByTestId("finance-list")).not.toContainText(
      "reviewing",
    );

    await page.getByTestId("finance-list-item").first().click();
    await expect(page.getByTestId("finance-detail")).toBeVisible();
    await expect(page.getByTestId("finance-detail-status")).toHaveText("编写中");
    await expect(page.getByTestId("finance-detail-status")).not.toHaveText(
      "writing",
    );
    await expect(page.getByTestId("finance-rows-table")).toBeVisible();
    await expect(page.getByTestId("finance-row")).toHaveCount(2);
    // 编号列必须展示后端契约 id，不能只做 React key
    await expect(page.getByTestId("finance-rows-table").getByText("编号")).toBeVisible();
    await expect(page.getByTestId("finance-row-id")).toHaveCount(2);
    await expect(page.getByTestId("finance-row-id").nth(0)).toHaveText("row_1");
    await expect(page.getByTestId("finance-row-id").nth(1)).toHaveText("row_2");
    await expect(page.getByTestId("finance-quote-total")).toHaveText(
      "128,000.50",
    );
    await expect(page.getByTestId("finance-quote-notes")).toContainText(
      "不含运维",
    );
    await expect(page.getByText("设备供货")).toBeVisible();
    // 白名单外字段不得出现
    await expect(page.getByText(/businessQualify|qualify|businessToc|commit/i)).toHaveCount(
      0,
    );

    expect(
      state.financeHits.some((h) => h.includes("/api/finance/business-bids")),
    ).toBeTruthy();
    expect(
      state.financeHits.some((h) =>
        h.includes("/api/finance/business-bids/proj_biz_1"),
      ),
    ).toBeTruthy();
    expect(state.forbiddenHits, `意外业务请求: ${state.forbiddenHits.join(", ")}`).toEqual(
      [],
    );
    await assertNoSensitiveStorage(page);
  });

  test("finance 空分项与列表失败可读", async ({ page }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { username: "user_finance", csrf: null }),
      forbiddenHits: [],
      financeHits: [],
    };
    await installFinanceRoutes(page, state);

    await page.goto("/finance");
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });
    // 第二项无分项
    await page.getByTestId("finance-list-item").nth(1).click();
    await expect(page.getByTestId("finance-rows-empty")).toBeVisible();
    await expect(page.getByTestId("finance-quote-notes")).toContainText("无备注");

    // 列表失败：改桩返回 500
    await page.unroute("**/*");
    await page.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      if (!url.pathname.startsWith("/api")) {
        await route.continue();
        return;
      }
      if (url.pathname === "/api/auth/bootstrap-status") {
        await json(route, { bootstrapped: true, authRequired: true });
        return;
      }
      if (url.pathname === "/api/auth/me") {
        await json(route, {
          ...meFor("finance", { username: "user_finance", csrf: null }),
          csrfToken: null,
        });
        return;
      }
      if (url.pathname === "/api/auth/csrf") {
        await json(route, { csrfToken: "e2e-finance-csrf" });
        return;
      }
      if (url.pathname === "/api/health") {
        await json(route, { status: "ok", service: "biaoshu-e2e" });
        return;
      }
      if (url.pathname.startsWith("/api/finance/business-bids")) {
        await json(
          route,
          {
            detail: {
              code: "server_error",
              message: "C:\\\\secret\\\\path apiKey=leak",
            },
          },
          500,
        );
        return;
      }
      await json(
        route,
        { detail: { code: "role_forbidden", message: "禁止" } },
        403,
      );
    });

    await page.getByTestId("finance-refresh").click();
    await expect(page.getByTestId("finance-list-error")).toBeVisible();
    await expect(page.getByTestId("finance-list-error")).toContainText(
      "加载失败",
    );
    await expect(page.getByTestId("finance-list-error")).not.toContainText(
      "secret",
    );
    await expect(page.getByTestId("finance-list-error")).not.toContainText(
      "apiKey",
    );
    await assertNoSensitiveStorage(page);
  });

  for (const role of ["bid_writer", "hr", "bidder"] as AuthRole[]) {
    test(`${role} 无财务入口且直达 /finance 受限`, async ({ page }) => {
      const state: FinanceAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          username: `user_${role}`,
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        forbiddenHits: [],
        financeHits: [],
      };
      await installFinanceRoutes(page, state);

      await page.goto(role === "bid_writer" ? "/create" : "/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).not.toContainText("财务报价");

      await page.goto("/finance");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText("当前账号无权访问该功能")).toBeVisible();
      await expect(page.getByTestId("finance-quote-page")).toHaveCount(0);
      // 非财务不应请求财务接口（页面被门禁截断）
      expect(state.financeHits).toEqual([]);
      await assertNoSensitiveStorage(page);
    });
  }

  test("所有者 bid_writer 无财务入口", async ({ page }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", {
        username: "owner_local",
        isOwner: true,
        csrf: null,
      }),
      forbiddenHits: [],
      financeHits: [],
    };
    await installFinanceRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("财务报价");
    await page.goto("/finance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.financeHits).toEqual([]);
  });

  test("disabled 模式 /finance 受限且无入口", async ({ page }) => {
    const state: FinanceAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      financeHits: [],
    };
    await installFinanceRoutes(page, state);

    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).toContainText("标书生成");
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("财务报价");

    await page.goto("/finance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("finance-quote-page")).toHaveCount(0);
    expect(state.financeHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
