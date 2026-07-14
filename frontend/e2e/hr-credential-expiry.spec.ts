/**
 * 模块：P10I 人员资质到期提示前端 E2E
 * 用途：验收 hr 入口、唯一 GET、服务端计数/顺序/状态、免责声明、空态、错误脱敏、
 *       角色门禁、导航精确激活、无 P10D/P10F/P10H 回退、无敏感存储。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:hr-credential-expiry。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/hr/credential-expiry；阻断外网与其他业务 API。
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

type AttentionItem = {
  cardId: string;
  personName: string;
  category: string;
  credentialName: string;
  level: string;
  validUntil: string | null;
  state: "expired" | "expiring_soon" | "missing_expiry";
  daysRemaining: number | null;
};

type ExpiryPayload = {
  asOfDate: string;
  windowDays: number;
  activeTotalCount: number;
  expiredCount: number;
  expiringSoonCount: number;
  validCount: number;
  missingExpiryCount: number;
  inactiveExcludedCount: number;
  attentionItems: AttentionItem[];
};

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

const DISCLAIMER =
  "仅依据人工录入的有效期日期生成，不验证证书真实性、持证状态、适用范围或监管结论";

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

function seedPayload(): ExpiryPayload {
  return {
    asOfDate: "2026-07-14",
    windowDays: 90,
    activeTotalCount: 5,
    expiredCount: 1,
    expiringSoonCount: 1,
    validCount: 2,
    missingExpiryCount: 1,
    inactiveExcludedCount: 1,
    // 服务端固定顺序：expired → expiring_soon → missing_expiry
    attentionItems: [
      {
        cardId: "hcc_expired_secret",
        personName: "张三",
        category: "professional",
        credentialName: "一级建造师",
        level: "一级",
        validUntil: "2026-07-01",
        state: "expired",
        daysRemaining: -13,
      },
      {
        cardId: "hcc_soon_secret",
        personName: "李四",
        category: "safety",
        credentialName: "安全员证",
        level: "B",
        validUntil: "2026-08-01",
        state: "expiring_soon",
        daysRemaining: 18,
      },
      {
        cardId: "hcc_missing_secret",
        personName: "王五",
        category: "other",
        credentialName: "上岗证",
        level: "",
        validUntil: null,
        state: "missing_expiry",
        daysRemaining: null,
      },
    ],
  };
}

function emptyPayload(): ExpiryPayload {
  return {
    asOfDate: "2026-07-14",
    windowDays: 90,
    activeTotalCount: 0,
    expiredCount: 0,
    expiringSoonCount: 0,
    validCount: 0,
    missingExpiryCount: 0,
    inactiveExcludedCount: 0,
    attentionItems: [],
  };
}

/** 仅停用卡：无启用卡、关注空，但停用排除计数 > 0 */
function inactiveOnlyPayload(): ExpiryPayload {
  return {
    asOfDate: "2026-07-14",
    windowDays: 90,
    activeTotalCount: 0,
    expiredCount: 0,
    expiringSoonCount: 0,
    validCount: 0,
    missingExpiryCount: 0,
    inactiveExcludedCount: 1,
    attentionItems: [],
  };
}

type HceAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  hceHits: string[];
  payload: ExpiryPayload;
  forceError?: boolean;
  errorBody?: unknown;
};

/**
 * 用途：安装 auth + P10I 专用接口桩；阻断 P10D/P10F/P10H/业务与外网。
 */
async function installHceRoutes(page: Page, state: HceAuthState) {
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
      path.startsWith("/api/export") ||
      path.startsWith("/api/finance") ||
      path.startsWith("/api/bidder") ||
      path.startsWith("/api/hr/credential-cards") ||
      path.startsWith("/api/hr/team-recommendations") ||
      path.startsWith("/api/hr/performance-cards")
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
      if (path === "/api/hr/credential-expiry") {
        state.hceHits.push(`${method} ${path}`);
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
        csrfToken: state.resumeCsrf ?? "e2e-hce-csrf",
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

    if (path === "/api/hr/credential-expiry") {
      state.hceHits.push(`${method} ${path}${url.search || ""}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "hr") {
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
              code: "hr_credential_expiry_failed",
              message: "内部错误",
              leak:
                "SECRET-LEAK cardId=hcc_expired_secret person=张三 path=/api/hr/credential-expiry",
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
      expect.soft(value.includes("hcc_expired_secret")).toBeFalsy();
      expect.soft(value.includes("SECRET-LEAK")).toBeFalsy();
      expect.soft(value.includes("一级建造师")).toBeFalsy();
    }
  }
}

test.describe("P10I 人员资质到期提示前端", () => {
  test("hr 入口、唯一 GET、固定计数、服务端顺序状态、免责声明、无 cardId/无回退/无敏感存储", async ({
    page,
  }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { username: "user_hr", isOwner: false, csrf: null }),
      resumeCsrf: "e2e-hce-resume-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload: seedPayload(),
    };
    await installHceRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("人力");
    await expect(nav).toContainText("人员资质");
    await expect(nav).toContainText("团队推荐");
    await expect(nav).toContainText("人员业绩");
    await expect(nav).toContainText("到期提示");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("标书生成");

    await nav.getByText("到期提示").click();
    await expect(page.getByTestId("hr-credential-expiry-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "到期提示" })).toBeVisible();

    const expiryLink = nav.locator('a[href="/hr/credential-expiry"]');
    await expect(expiryLink).toHaveClass(/is-active/);
    await expect(nav.locator('a[href="/hr"]')).not.toHaveClass(/is-active/);
    await expect(
      nav.locator('a[href="/hr/team-recommendations"]'),
    ).not.toHaveClass(/is-active/);
    await expect(
      nav.locator('a[href="/hr/performance-cards"]'),
    ).not.toHaveClass(/is-active/);

    // 首屏恰好 1 次无 query 的 GET（实例内 Promise 去重，不因 Strict Mode 双挂载放宽）
    await expect(page.getByTestId("hce-as-of")).toHaveText("2026-07-14");
    expect(state.hceHits).toEqual(["GET /api/hr/credential-expiry"]);

    await expect(page.getByTestId("hce-disclaimer")).toContainText(DISCLAIMER);
    await expect(page.getByTestId("hce-window-days")).toHaveText("90");

    await expect(page.getByTestId("hce-active-total")).toHaveText("5");
    await expect(page.getByTestId("hce-expired-count")).toHaveText("1");
    await expect(page.getByTestId("hce-expiring-count")).toHaveText("1");
    await expect(page.getByTestId("hce-valid-count")).toHaveText("2");
    await expect(page.getByTestId("hce-missing-count")).toHaveText("1");
    await expect(page.getByTestId("hce-inactive-count")).toHaveText("1");

    const items = page.getByTestId("hce-attention-item");
    await expect(items).toHaveCount(3);
    // 服务端顺序：expired → expiring_soon → missing_expiry
    await expect(items.nth(0).getByTestId("hce-item-state")).toHaveText(
      "已过期",
    );
    await expect(items.nth(0).getByTestId("hce-item-person")).toHaveText("张三");
    await expect(items.nth(0).getByTestId("hce-item-days")).toHaveText("-13");
    await expect(items.nth(1).getByTestId("hce-item-state")).toHaveText(
      "即将到期",
    );
    await expect(items.nth(1).getByTestId("hce-item-person")).toHaveText("李四");
    await expect(items.nth(1).getByTestId("hce-item-days")).toHaveText("18");
    await expect(items.nth(2).getByTestId("hce-item-state")).toHaveText(
      "缺有效期",
    );
    await expect(items.nth(2).getByTestId("hce-item-person")).toHaveText("王五");
    await expect(items.nth(2).getByTestId("hce-item-days")).toHaveText("—");

    const pageText = await page.getByTestId("hr-credential-expiry-page").innerText();
    expect(pageText).not.toContain("hcc_expired_secret");
    expect(pageText).not.toContain("hcc_soon_secret");
    expect(pageText).not.toContain("hcc_missing_secret");
    expect(pageText).not.toContain("cardId");

    // 不得以浏览器本地日期重算：服务端 daysRemaining=-13 原样展示
    await expect(items.nth(0)).toHaveAttribute("data-state", "expired");
    await expect(items.nth(1)).toHaveAttribute("data-state", "expiring_soon");
    await expect(items.nth(2)).toHaveAttribute("data-state", "missing_expiry");

    // 手动刷新恰好再 +1，总数 2，且均无 query
    await page.getByTestId("hce-reload").click();
    await expect
      .poll(() => state.hceHits.length, { timeout: 15_000 })
      .toBe(2);
    await expect(page.getByTestId("hce-as-of")).toHaveText("2026-07-14");
    expect(state.hceHits).toEqual([
      "GET /api/hr/credential-expiry",
      "GET /api/hr/credential-expiry",
    ]);

    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("空态：完整计数为零、关注列表空、仍展示基准日期与窗口", async ({
    page,
  }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hce-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload: emptyPayload(),
    };
    await installHceRoutes(page, state);

    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("hr-credential-expiry-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("hce-attention-empty")).toBeVisible();
    await expect(page.getByTestId("hce-attention-empty")).toContainText(
      "当前无启用卡；停用卡已排除",
    );
    await expect(page.getByTestId("hce-active-total")).toHaveText("0");
    await expect(page.getByTestId("hce-expired-count")).toHaveText("0");
    await expect(page.getByTestId("hce-window-days")).toHaveText("90");
    await expect(page.getByTestId("hce-attention-item")).toHaveCount(0);
    expect(state.hceHits).toEqual(["GET /api/hr/credential-expiry"]);
    expect(state.forbiddenHits).toEqual([]);
  });

  test("仅停用卡：activeTotal=0 且 inactiveExcluded>0 显示无启用卡文案", async ({
    page,
  }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hce-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload: inactiveOnlyPayload(),
    };
    await installHceRoutes(page, state);

    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("hr-credential-expiry-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("hce-attention-empty")).toBeVisible();
    await expect(page.getByTestId("hce-attention-empty")).toContainText(
      "当前无启用卡；停用卡已排除",
    );
    await expect(page.getByTestId("hce-attention-empty")).not.toContainText(
      "启用卡均在有效窗口外",
    );
    await expect(page.getByTestId("hce-active-total")).toHaveText("0");
    await expect(page.getByTestId("hce-inactive-count")).toHaveText("1");
    await expect(page.getByTestId("hce-attention-item")).toHaveCount(0);
    expect(state.hceHits).toEqual(["GET /api/hr/credential-expiry"]);
    expect(state.forbiddenHits).toEqual([]);
  });

  test("后端错误含 SECRET/cardId/姓名时固定中文脱敏", async ({ page }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hce-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload: seedPayload(),
      forceError: true,
    };
    await installHceRoutes(page, state);

    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("hr-credential-expiry-page")).toBeVisible({
      timeout: 15_000,
    });
    const err = page.getByTestId("hce-error");
    await expect(err).toBeVisible();
    await expect(err).toContainText("人员资质到期提示加载失败");
    await expect(err).not.toContainText("SECRET-LEAK");
    await expect(err).not.toContainText("hcc_expired_secret");
    await expect(err).not.toContainText("hr_credential_expiry_failed");
    await expect(err).not.toContainText("张三");
    await expect(err).not.toContainText("/api/hr/credential-expiry");
    await expect(page.getByTestId("hce-attention-list")).toHaveCount(0);
    expect(state.hceHits).toEqual(["GET /api/hr/credential-expiry"]);
  });

  for (const role of ["bid_writer", "finance", "bidder"] as AuthRole[]) {
    test(`${role} 无到期提示入口且直达受限、零 P10I API`, async ({ page }) => {
      const state: HceAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        resumeCsrf: "e2e-other-csrf",
        forbiddenHits: [],
        hceHits: [],
        payload: seedPayload(),
      };
      await installHceRoutes(page, state);

      await page.goto("/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("到期提示");
      await expect(nav).not.toContainText("人力");

      await page.goto("/hr/credential-expiry");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("hr-credential-expiry-page")).toHaveCount(0);
      expect(state.hceHits).toEqual([]);
    });
  }

  test("仅所有者（bid_writer isOwner）直达受限、零 P10I API", async ({
    page,
  }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { isOwner: true, csrf: null }),
      resumeCsrf: "e2e-owner-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload: seedPayload(),
    };
    await installHceRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("到期提示");
    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hceHits).toEqual([]);
  });

  test("disabled 模式 /hr/credential-expiry 受限且无入口、零 P10I API", async ({
    page,
  }) => {
    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      hceHits: [],
      payload: seedPayload(),
    };
    await installHceRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("到期提示");
    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hceHits).toEqual([]);
  });

  test("到期提示 active、人员资质非 active；服务端 state/daysRemaining 不重算", async ({
    page,
  }) => {
    // 故意给出与“今天”可能不一致的服务端值，确认前端不按 Date.now 重算
    const payload = seedPayload();
    payload.asOfDate = "2099-01-01";
    payload.attentionItems = [
      {
        cardId: "hcc_future_state",
        personName: "赵六",
        category: "professional",
        credentialName: "假到期测试证",
        level: "甲",
        validUntil: "2099-12-31",
        state: "expired",
        daysRemaining: -999,
      },
    ];
    payload.activeTotalCount = 1;
    payload.expiredCount = 1;
    payload.expiringSoonCount = 0;
    payload.validCount = 0;
    payload.missingExpiryCount = 0;
    payload.inactiveExcludedCount = 0;

    const state: HceAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hce-csrf",
      forbiddenHits: [],
      hceHits: [],
      payload,
    };
    await installHceRoutes(page, state);

    await page.goto("/hr/credential-expiry");
    await expect(page.getByTestId("hr-credential-expiry-page")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav.locator('a[href="/hr/credential-expiry"]')).toHaveClass(
      /is-active/,
    );
    await expect(nav.locator('a[href="/hr"]')).not.toHaveClass(/is-active/);

    // 即使 validUntil 看起来还在未来，仍展示服务端 state=expired 与 daysRemaining=-999
    await expect(page.getByTestId("hce-as-of")).toHaveText("2099-01-01");
    await expect(page.getByTestId("hce-item-state")).toHaveText("已过期");
    await expect(page.getByTestId("hce-item-days")).toHaveText("-999");
    await expect(page.getByTestId("hce-item-valid-until")).toHaveText(
      "2099-12-31",
    );

    await expect(nav).not.toContainText("财务报价");
    expect(state.hceHits).toEqual(["GET /api/hr/credential-expiry"]);
    expect(state.forbiddenHits).toEqual([]);
  });
});
