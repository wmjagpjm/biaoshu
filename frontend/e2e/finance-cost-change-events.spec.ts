/**
 * 模块：P10J 财务个人成本变更记录前端 E2E
 * 用途：验收 strict finance 入口、唯一 GET、三动作中文映射、entryId/时间、限制声明、
 *       Strict Mode 请求次数、空态、错误脱敏、角色门禁、导航精确激活、网络/存储白名单。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:finance-cost-change-events。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/finance/cost-change-events；阻断报价、
 *           cost-draft、projects、editor-state、settings、files、HR、bidder、未知 API；
 *           既有字体本地 204 阻断且不计 externalHits；其他非本机记 externalHits 后 abort。
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

type CostChangeAction = "create" | "update" | "delete";

type CostChangeItem = {
  action: CostChangeAction;
  entryId: string;
  occurredAt: string;
};

type CostChangePayload = {
  items: CostChangeItem[] | unknown;
};

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

const DISCLAIMER =
  "只记录当前账户在当前工作空间成功的成本条目新增、修改、删除；不是完整财务审计，不能还原项目、金额、内容、变更前后值或失败尝试";

const FIXED_ERROR = "成本变更记录加载失败，请稍后重试";

/**
 * 用途：识别既有 index.html 引入的 Google 字体 URL。
 * 对接：installP10jRoutes 本地阻断，不得 route.continue 外网。
 * 二次开发：字体请求可 abort/空响应，且不得计入 externalHits（非本任务新外链）。
 */
function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

/**
 * 用途：判断是否本机主机。
 * 对接：仅 127.0.0.1/localhost 可继续；其余一律记 externalHits 后 abort。
 */
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

function seedPayload(): CostChangePayload {
  return {
    items: [
      {
        action: "create",
        entryId: "fce_create_alpha",
        occurredAt: "2026-07-14T10:00:00.000Z",
      },
      {
        action: "update",
        entryId: "fce_update_beta",
        occurredAt: "2026-07-14T11:30:00.000Z",
      },
      {
        action: "delete",
        entryId: "fce_delete_gamma",
        occurredAt: "2026-07-14T12:45:00.000Z",
      },
    ],
  };
}

function emptyPayload(): CostChangePayload {
  return { items: [] };
}

function nonArrayPayload(): CostChangePayload {
  return { items: { not: "array" } as unknown as CostChangeItem[] };
}

type P10jAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  p10jHits: string[];
  /** 非本机外网请求记录（method + URL）；字体本地阻断不计入 */
  externalHits: string[];
  payload: CostChangePayload;
  forceError?: boolean;
  errorBody?: unknown;
};

/**
 * 用途：安装 auth + P10J 专用接口桩；阻断报价/cost-draft/业务/HR/bidder 与外网。
 * 对接：字体用本地空响应/abort，不计 externalHits；其他非本机先记 externalHits 再 abort。
 * 二次开发：禁止 route.continue 到外网；测试必须能断言 externalHits=[] 且可观测新外链。
 */
async function installP10jRoutes(page: Page, state: P10jAuthState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

    // 既有 Google 字体：本地阻断，真实零外网；不计为 P10J 新外链
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

    // 未知外网：可观测记录后 abort，确保白名单不会假绿
    if (!isLocalHost(host)) {
      state.externalHits.push(`${method} ${rawUrl}`);
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
      path.startsWith("/api/export") ||
      path.startsWith("/api/hr") ||
      path.startsWith("/api/bidder") ||
      path === "/api/finance/business-bids" ||
      path.startsWith("/api/finance/business-bids/") ||
      path.includes("cost-draft") ||
      path.includes("cost-entries")
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
      if (path === "/api/finance/cost-change-events") {
        state.p10jHits.push(`${method} ${path}`);
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
        csrfToken: state.resumeCsrf ?? "e2e-p10j-csrf",
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

    if (path === "/api/finance/cost-change-events") {
      state.p10jHits.push(`${method} ${path}${url.search || ""}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "finance") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (method !== "GET") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "method_not_allowed", message: "仅支持 GET" } },
          405,
        );
        return;
      }
      if (state.forceError) {
        await json(
          route,
          state.errorBody ?? {
            detail: {
              code: "finance_cost_change_events_failed",
              message: "内部错误",
              leak:
                "SECRET-LEAK entryId=fce_create_alpha path=/api/finance/cost-change-events amount=9999",
            },
          },
          500,
        );
        return;
      }
      await json(route, state.payload);
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
      expect.soft(value.includes("fce_create_alpha")).toBeFalsy();
      expect.soft(value.includes("SECRET-LEAK")).toBeFalsy();
      expect.soft(value.includes("cost-change-events")).toBeFalsy();
    }
  }
}

test.describe("P10J 财务个人成本变更记录前端", () => {
  test("strict finance 可见导航与页面；三动作映射/entryId/限制声明；首次 GET 严格 1 次", async ({
    page,
  }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", {
        username: "user_finance",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-p10j-resume-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("财务");
    await expect(nav).toContainText("财务报价");
    await expect(nav).toContainText("我的成本记录");
    await expect(nav).not.toContainText("标书生成");
    await expect(nav).not.toContainText("到期提示");
    await expect(nav).not.toContainText("人员资质");

    await nav.getByText("我的成本记录").click();
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole("heading", { name: "我的成本记录" }),
    ).toBeVisible();

    // 首屏恰好 1 次无 query 的 GET（实例内 Promise 去重，不因 Strict Mode 双挂载放宽）
    await expect(page.getByTestId("fcc-item").first()).toBeVisible({
      timeout: 15_000,
    });
    expect(state.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);

    await expect(page.getByTestId("fcc-disclaimer")).toContainText(DISCLAIMER);

    const items = page.getByTestId("fcc-item");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0).getByTestId("fcc-item-action")).toHaveText(
      "新增成本条目",
    );
    await expect(items.nth(0).getByTestId("fcc-item-entry-id")).toHaveText(
      "fce_create_alpha",
    );
    await expect(items.nth(1).getByTestId("fcc-item-action")).toHaveText(
      "修改成本条目",
    );
    await expect(items.nth(1).getByTestId("fcc-item-entry-id")).toHaveText(
      "fce_update_beta",
    );
    await expect(items.nth(2).getByTestId("fcc-item-action")).toHaveText(
      "删除成本条目",
    );
    await expect(items.nth(2).getByTestId("fcc-item-entry-id")).toHaveText(
      "fce_delete_gamma",
    );

    // 时间只做安全展示，不得空白
    for (let i = 0; i < 3; i += 1) {
      const t = (await items.nth(i).getByTestId("fcc-item-time").innerText())
        .trim();
      expect(t.length).toBeGreaterThan(0);
      expect(t).not.toBe("");
    }

    const pageText = await page
      .getByTestId("finance-cost-change-events-page")
      .innerText();
    expect(pageText).not.toContain("SECRET-LEAK");
    expect(pageText).not.toContain("amount");
    expect(pageText).not.toContain("毛利");

    expect(state.forbiddenHits).toEqual([]);
    // 严格主路径：无未知外网；字体已本地阻断且不计 externalHits
    expect(state.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("手动刷新后累计严格 2 次；不得因 Strict Mode 多读", async ({ page }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);

    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("fcc-item").first()).toBeVisible({
      timeout: 15_000,
    });
    expect(state.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);

    await page.getByTestId("fcc-reload").click();
    await expect
      .poll(() => state.p10jHits.length, { timeout: 15_000 })
      .toBe(2);
    expect(state.p10jHits).toEqual([
      "GET /api/finance/cost-change-events",
      "GET /api/finance/cost-change-events",
    ]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
  });

  test("空 items 空态；非数组安全空态", async ({ page }) => {
    const emptyState: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: emptyPayload(),
    };
    await installP10jRoutes(page, emptyState);

    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("fcc-empty")).toBeVisible();
    await expect(page.getByTestId("fcc-item")).toHaveCount(0);
    expect(emptyState.p10jHits).toEqual([
      "GET /api/finance/cost-change-events",
    ]);
    expect(emptyState.forbiddenHits).toEqual([]);
    expect(emptyState.externalHits).toEqual([]);

    // 非数组 items：安全收敛为空态
    await page.unrouteAll({ behavior: "ignoreErrors" });
    const badState: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: nonArrayPayload(),
    };
    await installP10jRoutes(page, badState);
    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("fcc-empty")).toBeVisible();
    await expect(page.getByTestId("fcc-item")).toHaveCount(0);
    expect(badState.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);
    expect(badState.forbiddenHits).toEqual([]);
    expect(badState.externalHits).toEqual([]);
  });

  test("500/detail/path/SECRET 只显示固定中文错误，不泄漏原文", async ({
    page,
  }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
      forceError: true,
    };
    await installP10jRoutes(page, state);

    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    const err = page.getByTestId("fcc-error");
    await expect(err).toBeVisible();
    await expect(err).toContainText(FIXED_ERROR);
    await expect(err).not.toContainText("SECRET-LEAK");
    await expect(err).not.toContainText("fce_create_alpha");
    await expect(err).not.toContainText("finance_cost_change_events_failed");
    await expect(err).not.toContainText("/api/finance/cost-change-events");
    await expect(err).not.toContainText("amount=9999");
    await expect(page.getByTestId("fcc-item")).toHaveCount(0);
    expect(state.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);
    expect(state.externalHits).toEqual([]);
  });

  for (const role of ["bid_writer", "hr", "bidder"] as AuthRole[]) {
    test(`${role} 无成本记录入口且直达受限、零 P10J API`, async ({ page }) => {
      const state: P10jAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        resumeCsrf: "e2e-other-csrf",
        forbiddenHits: [],
        p10jHits: [],
        externalHits: [],
        payload: seedPayload(),
      };
      await installP10jRoutes(page, state);

      await page.goto("/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("我的成本记录");
      await expect(nav).not.toContainText("财务报价");

      await page.goto("/finance/cost-changes");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(
        page.getByTestId("finance-cost-change-events-page"),
      ).toHaveCount(0);
      expect(state.p10jHits).toEqual([]);
      expect(state.externalHits).toEqual([]);
    });
  }

  test("仅所有者（bid_writer isOwner）直达受限、零 P10J API", async ({
    page,
  }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { isOwner: true, csrf: null }),
      resumeCsrf: "e2e-owner-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("我的成本记录");
    await page.goto("/finance/cost-changes");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.p10jHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
  });

  test("disabled 模式 /finance/cost-changes 受限且无入口、零 P10J API", async ({
    page,
  }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("我的成本记录");
    await page.goto("/finance/cost-changes");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.p10jHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
  });

  test("/finance 与 /finance/cost-changes 激活态互斥；生命周期无业务回退", async ({
    page,
  }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);

    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav.locator('a[href="/finance/cost-changes"]')).toHaveClass(
      /is-active/,
    );
    await expect(nav.locator('a[href="/finance"]')).not.toHaveClass(
      /is-active/,
    );

    // 切到财务报价页：报价激活、成本记录非激活
    await nav.getByText("财务报价").click();
    await expect(page.getByTestId("finance-quote-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(nav.locator('a[href="/finance"]')).toHaveClass(/is-active/);
    await expect(nav.locator('a[href="/finance/cost-changes"]')).not.toHaveClass(
      /is-active/,
    );

    // 再回成本记录
    await nav.getByText("我的成本记录").click();
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(nav.locator('a[href="/finance/cost-changes"]')).toHaveClass(
      /is-active/,
    );
    await expect(nav.locator('a[href="/finance"]')).not.toHaveClass(
      /is-active/,
    );

    // P10J 页面生命周期内不得回退报价/cost-draft 等（forbiddenHits 应空）
    // 注意：点击「财务报价」会触发 P10B 请求，那是合法业务页；此处只断言 cost-changes 页本身未回退
    // 因此在进入 cost-changes 后重置 forbidden 统计再观察一次加载
    state.forbiddenHits.length = 0;
    state.p10jHits.length = 0;
    state.externalHits.length = 0;
    await page.getByTestId("fcc-reload").click();
    await expect
      .poll(() => state.p10jHits.length, { timeout: 15_000 })
      .toBe(1);
    expect(state.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);
    expect(state.forbiddenHits).toEqual([]);
    // 生命周期：未知外网可观测且必须为空
    expect(state.externalHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("非法时间显示「时间未知」；合法时间可见中文展示", async ({ page }) => {
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: {
        items: [
          {
            action: "create",
            entryId: "fce_valid_time",
            occurredAt: "2026-07-14T08:15:00.000Z",
          },
          {
            action: "update",
            entryId: "fce_bad_time",
            occurredAt: "not-a-timestamp",
          },
        ],
      },
    };
    await installP10jRoutes(page, state);

    await page.goto("/finance/cost-changes");
    await expect(page.getByTestId("fcc-item")).toHaveCount(2, {
      timeout: 15_000,
    });
    await expect(
      page.getByTestId("fcc-item").nth(1).getByTestId("fcc-item-time"),
    ).toHaveText("时间未知");
    const validTime = (
      await page
        .getByTestId("fcc-item")
        .nth(0)
        .getByTestId("fcc-item-time")
        .innerText()
    ).trim();
    expect(validTime).not.toBe("时间未知");
    expect(validTime.length).toBeGreaterThan(0);
    expect(state.p10jHits).toEqual(["GET /api/finance/cost-change-events"]);
    expect(state.externalHits).toEqual([]);
  });

  test("未知非字体外链可观测写入 externalHits 且不真实出网", async ({
    page,
  }) => {
    // 不改生产代码：在页面内主动 fetch 非字体外链，验证桩可观测且 abort
    const state: P10jAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("finance", { csrf: null }),
      resumeCsrf: "e2e-p10j-csrf",
      forbiddenHits: [],
      p10jHits: [],
      externalHits: [],
      payload: seedPayload(),
    };
    await installP10jRoutes(page, state);

    await page.goto("/finance/cost-changes");
    await expect(
      page.getByTestId("finance-cost-change-events-page"),
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("fcc-item").first()).toBeVisible({
      timeout: 15_000,
    });
    expect(state.externalHits).toEqual([]);

    // 模拟非字体外链：只走 Playwright route，真实网络被 abort
    await page.evaluate(async () => {
      try {
        await fetch("https://example.invalid/p10j-probe", {
          method: "GET",
          mode: "no-cors",
        });
      } catch {
        // abort 可能抛错，忽略
      }
    });

    await expect
      .poll(() => state.externalHits.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    expect(
      state.externalHits.some(
        (h) =>
          h.includes("example.invalid") && h.includes("p10j-probe"),
      ),
    ).toBeTruthy();
    // 字体本地阻断不进 externalHits
    expect(
      state.externalHits.every(
        (h) =>
          !h.includes("fonts.googleapis.com") &&
          !h.includes("fonts.gstatic.com"),
      ),
    ).toBeTruthy();
  });
});
