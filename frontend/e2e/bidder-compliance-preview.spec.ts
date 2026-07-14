/**
 * 模块：P10E 投标人匿名合规预览前端 E2E
 * 用途：验收 bidder 入口、匿名汇总渲染、空态、受限角色不请求、错误脱敏、网络白名单与无浏览器持久化。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:bidder-compliance-preview。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/bidder/compliance-preview；禁止快照整页泄露；禁止业务回退。
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

type PreviewPayload = {
  dataState: "ready" | "empty";
  summary: {
    totalItems: number;
    coveredItems: number;
    uncoveredItems: number;
    waivedItems: number;
    coverageBasisPoints: number | null;
  };
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

const SAMPLE_READY: PreviewPayload = {
  dataState: "ready",
  summary: {
    totalItems: 12,
    coveredItems: 8,
    uncoveredItems: 3,
    waivedItems: 1,
    coverageBasisPoints: 7273,
  },
};

const SAMPLE_EMPTY: PreviewPayload = {
  dataState: "empty",
  summary: {
    totalItems: 0,
    coveredItems: 0,
    uncoveredItems: 0,
    waivedItems: 0,
    coverageBasisPoints: null,
  },
};

type BidderAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  bidderHits: string[];
  /** 可覆盖预览响应；默认 ready */
  preview?: PreviewPayload;
  /** 强制预览接口失败 */
  forcePreviewError?: boolean;
};

/**
 * 用途：安装 auth + bidder 专用接口桩；阻断通用业务 API 与外网。
 */
async function installBidderRoutes(page: Page, state: BidderAuthState) {
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
      path.startsWith("/api/finance/") ||
      path.startsWith("/api/hr/")
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
      if (path.startsWith("/api/bidder/")) {
        state.bidderHits.push(`${method} ${path}`);
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
        csrfToken: state.resumeCsrf ?? "e2e-bidder-csrf",
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

    if (path === "/api/bidder/compliance-preview" && method === "GET") {
      state.bidderHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "bidder") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (state.forcePreviewError) {
        await json(
          route,
          {
            detail: {
              code: "server_error",
              message: "C:\\\\secret\\\\path apiKey=leak sourceKey=SRC_1",
            },
          },
          500,
        );
        return;
      }
      await json(route, state.preview ?? SAMPLE_READY);
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
    }
  }

  // P10E：预览结果不得写入浏览器存储
  for (const [scope, map] of [
    ["localStorage", storage.local],
    ["sessionStorage", storage.session],
  ] as const) {
    for (const [key, value] of Object.entries(map)) {
      expect
        .soft(
          /compliance|bidder|coverage|responseMatrix|sourceKey/i.test(key),
          `${scope} 含预览相关 key=${key}`,
        )
        .toBeFalsy();
      expect
        .soft(
          /compliance-preview|coverageBasisPoints|sourceKey|responseMatrix/i.test(
            value,
          ),
          `${scope} 含预览相关 value of ${key}`,
        )
        .toBeFalsy();
    }
  }
}

test.describe("P10E 投标人匿名合规预览前端", () => {
  test("bidder 可见入口并渲染匿名汇总；仅认证/健康/预览端点", async ({
    page,
  }) => {
    const state: BidderAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bidder", {
        username: "user_bidder",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-bidder-resume",
      forbiddenHits: [],
      bidderHits: [],
      preview: SAMPLE_READY,
    };
    await installBidderRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("合规预览");
    await expect(nav).toContainText("投标人");
    await expect(nav).not.toContainText("标书生成");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("人员资质");

    await nav.getByText("合规预览").click();
    await expect(page.getByTestId("bidder-compliance-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("heading", { name: "合规预览" }),
    ).toBeVisible();
    await expect(page.getByTestId("bidder-compliance-ready")).toBeVisible();
    await expect(page.getByTestId("bidder-total-items")).toHaveText("12");
    await expect(page.getByTestId("bidder-covered-items")).toHaveText("8");
    await expect(page.getByTestId("bidder-uncovered-items")).toHaveText("3");
    await expect(page.getByTestId("bidder-waived-items")).toHaveText("1");
    await expect(page.getByTestId("bidder-coverage")).toContainText("72.73%");

    // 固定说明：不是评审结论或投标结果
    await expect(page.getByTestId("bidder-compliance-disclaimer")).toContainText(
      "不是评审结论",
    );
    await expect(page.getByTestId("bidder-compliance-disclaimer")).toContainText(
      "投标结果",
    );

    // 匿名：不得出现项目/源文/备注/内部键
    const pageText = await page.locator("main").innerText();
    expect(pageText).not.toMatch(/proj_|sourceKey|editor-state|responseMatrix/i);
    expect(pageText).not.toMatch(/招标原文|章节标题|大纲标题/);

    expect(
      state.bidderHits.some((h) =>
        h.includes("GET /api/bidder/compliance-preview"),
      ),
    ).toBeTruthy();
    expect(
      state.forbiddenHits,
      `意外业务请求: ${state.forbiddenHits.join(", ")}`,
    ).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("empty 态全零与暂无可计算覆盖率", async ({ page }) => {
    const state: BidderAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bidder", { username: "user_bidder", csrf: null }),
      forbiddenHits: [],
      bidderHits: [],
      preview: SAMPLE_EMPTY,
    };
    await installBidderRoutes(page, state);

    await page.goto("/bidder");
    await expect(page.getByTestId("bidder-compliance-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bidder-compliance-empty")).toBeVisible();
    await expect(page.getByTestId("bidder-total-items")).toHaveText("0");
    await expect(page.getByTestId("bidder-covered-items")).toHaveText("0");
    await expect(page.getByTestId("bidder-uncovered-items")).toHaveText("0");
    await expect(page.getByTestId("bidder-waived-items")).toHaveText("0");
    await expect(page.getByTestId("bidder-coverage")).toHaveText(
      "暂无可计算覆盖率",
    );
    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("预览失败固定中文脱敏", async ({ page }) => {
    const state: BidderAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bidder", { username: "user_bidder", csrf: null }),
      forbiddenHits: [],
      bidderHits: [],
      forcePreviewError: true,
    };
    await installBidderRoutes(page, state);

    await page.goto("/bidder");
    await expect(page.getByTestId("bidder-compliance-error")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bidder-compliance-error")).toHaveText(
      "暂时无法读取匿名合规预览",
    );
    await expect(page.getByTestId("bidder-compliance-error")).not.toContainText(
      "secret",
    );
    await expect(page.getByTestId("bidder-compliance-error")).not.toContainText(
      "apiKey",
    );
    await expect(page.getByTestId("bidder-compliance-error")).not.toContainText(
      "server_error",
    );
    await expect(page.getByTestId("bidder-compliance-error")).not.toContainText(
      "/api/bidder",
    );
    await assertNoSensitiveStorage(page);
  });

  for (const role of ["bid_writer", "finance", "hr"] as AuthRole[]) {
    test(`${role} 无投标人入口且直达 /bidder 受限且不请求预览`, async ({
      page,
    }) => {
      const state: BidderAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          username: `user_${role}`,
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        forbiddenHits: [],
        bidderHits: [],
      };
      await installBidderRoutes(page, state);

      await page.goto(role === "bid_writer" ? "/create" : "/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).not.toContainText("合规预览");
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).not.toContainText("投标人");

      await page.goto("/bidder");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText("当前账号无权访问该功能")).toBeVisible();
      await expect(page.getByTestId("bidder-compliance-page")).toHaveCount(0);
      expect(state.bidderHits).toEqual([]);
      await assertNoSensitiveStorage(page);
    });
  }

  test("所有者 bid_writer 无投标人入口", async ({ page }) => {
    const state: BidderAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", {
        username: "owner_local",
        isOwner: true,
        csrf: null,
      }),
      forbiddenHits: [],
      bidderHits: [],
    };
    await installBidderRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("合规预览");
    await page.goto("/bidder");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.bidderHits).toEqual([]);
  });

  test("disabled 模式 /bidder 受限且无入口", async ({ page }) => {
    const state: BidderAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      bidderHits: [],
    };
    await installBidderRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("合规预览");
    await page.goto("/bidder");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bidder-compliance-page")).toHaveCount(0);
    expect(state.bidderHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
