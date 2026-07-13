/**
 * 模块：P10A 前端登录会话与受限导航 E2E
 * 用途：验收 disabled 业务壳、required 登录门禁、退出回登录、非 bid_writer 重定向；
 *       禁止 localStorage/sessionStorage 落敏感字段；业务 API 仅同源。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:auth-rbac。
 * 二次开发：required 场景用 route 桩模拟后端握手；允许既有 index.html 字体样式表。
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
    body: JSON.stringify(body),
  });
}

function meFor(
  role: AuthRole,
  opts: { isOwner?: boolean; username?: string; csrf?: string | null } = {},
): MePayload {
  const isOwner = opts.isOwner ?? role === "bid_writer";
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

/**
 * 用途：安装 required 模式 auth 路由桩（与 semantic-index 同样拦截全部请求）。
 * 会话态由内存对象驱动；非 /api 资源放行本地静态资源。
 */
type RequiredAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  loginCsrf?: string;
  /** 硬刷新后续发的 CSRF 原始值 */
  resumeCsrf?: string;
  /** 当前服务端有效 CSRF（登录或续发后） */
  currentCsrf?: string | null;
  /** 最近一次 logout 请求头中的 CSRF */
  lastLogoutCsrf?: string | null;
  csrfCalls?: number;
};

async function installRequiredAuthRoutes(page: Page, state: RequiredAuthState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const path = url.pathname;
    const method = req.method().toUpperCase();

    // 既有字体：放行或忽略，不记为失败
    if (isLegacyFontUrl(url.href)) {
      await route.continue();
      return;
    }

    // 非本机：阻断
    if (host !== "127.0.0.1" && host !== "localhost") {
      await route.abort("failed");
      return;
    }

    // 非 API：本地前端静态资源
    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, {
        bootstrapped: state.bootstrapped,
        authRequired: true,
      });
      return;
    }

    if (path === "/api/auth/login" && method === "POST") {
      const csrf = state.loginCsrf ?? "e2e-csrf-login-token";
      state.currentCsrf = csrf;
      state.session = meFor("bid_writer", {
        username: "admin_local",
        isOwner: true,
        csrf,
      });
      state.bootstrapped = true;
      await json(route, state.session);
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
      // 契约：/me 不重复下发 CSRF
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
      state.csrfCalls = (state.csrfCalls ?? 0) + 1;
      const rotated = state.resumeCsrf ?? "e2e-csrf-resume-token";
      state.currentCsrf = rotated;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "Cache-Control": "no-store" },
        body: JSON.stringify({ csrfToken: rotated }),
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.lastLogoutCsrf = req.headers()["x-csrf-token"] ?? null;
      state.session = null;
      state.currentCsrf = null;
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

    if (!state.session) {
      await json(
        route,
        { detail: { code: "auth_required", message: "需要登录" } },
        401,
      );
      return;
    }
    await json(route, []);
  });
}

async function installRoleSession(page: Page, role: AuthRole) {
  const state: RequiredAuthState = {
    bootstrapped: true,
    session: meFor(role, {
      username: `user_${role}`,
      isOwner: false,
      csrf: null,
    }),
    loginCsrf: "e2e-csrf-role",
    resumeCsrf: `e2e-csrf-resume-${role}`,
  };
  await installRequiredAuthRoutes(page, state);
  return state;
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

test.describe("P10A 认证前端", () => {
  test("disabled 模式直接显示业务壳", async ({ page }) => {
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
    await expect(page.getByText("标书生成").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "本机登录" })).toHaveCount(0);
    await assertNoSensitiveStorage(page);
  });

  test("required 未登录只显示登录页，无业务壳", async ({ page }) => {
    await installRequiredAuthRoutes(page, {
      bootstrapped: true,
      session: null,
    });
    await page.goto("/create");
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.getByText("标书生成")).toHaveCount(0);
    await assertNoSensitiveStorage(page);
  });

  test("握手失败保持非业务态，不进入 disabled 业务壳", async ({ page }) => {
    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (isLegacyFontUrl(url.href)) {
        await route.continue();
        return;
      }
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      if (!url.pathname.startsWith("/api")) {
        await route.continue();
        return;
      }
      if (url.pathname === "/api/auth/bootstrap-status") {
        await json(
          route,
          { detail: { code: "handshake_unavailable", message: "握手失败桩" } },
          503,
        );
        return;
      }
      // 其它 API 亦失败，防止绕过
      await json(
        route,
        { detail: { code: "auth_required", message: "需要登录" } },
        401,
      );
    });

    await page.goto("/create");
    await expect(page.getByTestId("auth-handshake-error")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "无法确认认证模式" })).toBeVisible();
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "本机登录" })).toHaveCount(0);
    await expect(page.getByText("标书生成")).toHaveCount(0);
    await assertNoSensitiveStorage(page);
  });

  test("required 未初始化时登录页有引导说明", async ({ page }) => {
    await installRequiredAuthRoutes(page, {
      bootstrapped: false,
      session: null,
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/尚未完成管理员引导/)).toBeVisible();
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
  });

  test("登录恢复业务壳，退出后回到登录且清空内存会话", async ({ page }) => {
    const state: RequiredAuthState = {
      bootstrapped: true,
      session: null,
      loginCsrf: "e2e-csrf-memory-only",
    };
    await installRequiredAuthRoutes(page, state);

    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });

    await page.locator('input[name="username"]').fill("admin_local");
    await page.locator('input[name="password"]').fill("E2e-Only-Not-Stored!");
    await page.getByRole("button", { name: "登录" }).click();

    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("shell-user")).toContainText("admin_local");
    await expect(page.getByTestId("shell-user")).toContainText("标书制作者");
    await expect(page.getByRole("navigation", { name: "主导航" })).toContainText(
      "标书生成",
    );
    // 登录响应已带 CSRF，不应再请求续发
    expect(state.csrfCalls ?? 0).toBe(0);

    await assertNoSensitiveStorage(page);
    const csrfInStorage = await page.evaluate(() => {
      const blob = `${JSON.stringify(localStorage)}|${JSON.stringify(sessionStorage)}`;
      return blob.includes("e2e-csrf-memory-only");
    });
    expect(csrfInStorage).toBeFalsy();

    await page.getByTestId("logout-button").click();
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    expect(state.lastLogoutCsrf).toBe("e2e-csrf-memory-only");
    await assertNoSensitiveStorage(page);
  });

  test("硬刷新后 /me 无 CSRF 时调用续发，退出携带新 CSRF 且不落盘", async ({
    page,
  }) => {
    const resumeToken = "e2e-csrf-after-hard-refresh";
    const state: RequiredAuthState = {
      bootstrapped: true,
      // 模拟浏览器硬刷新：Cookie 会话仍在，但 React 内存 CSRF 已空
      session: meFor("bid_writer", {
        username: "admin_local",
        isOwner: true,
        csrf: null,
      }),
      resumeCsrf: resumeToken,
    };
    await installRequiredAuthRoutes(page, state);

    await page.goto("/projects");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("shell-user")).toContainText("admin_local");
    // 必须走续发
    expect(state.csrfCalls ?? 0).toBeGreaterThanOrEqual(1);
    expect(state.currentCsrf).toBe(resumeToken);

    await assertNoSensitiveStorage(page);
    const csrfInStorage = await page.evaluate((token) => {
      const blob = `${JSON.stringify(localStorage)}|${JSON.stringify(sessionStorage)}`;
      return blob.includes(token);
    }, resumeToken);
    expect(csrfInStorage).toBeFalsy();

    await page.getByTestId("logout-button").click();
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    expect(state.lastLogoutCsrf).toBe(resumeToken);
    await assertNoSensitiveStorage(page);
  });

  test("CSRF 续发失败时不渲染可写业务壳", async ({ page }) => {
    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (isLegacyFontUrl(url.href)) {
        await route.continue();
        return;
      }
      if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
        await route.abort("failed");
        return;
      }
      if (!url.pathname.startsWith("/api")) {
        await route.continue();
        return;
      }
      if (url.pathname === "/api/auth/bootstrap-status") {
        await json(route, { bootstrapped: true, authRequired: true });
        return;
      }
      if (url.pathname === "/api/auth/me") {
        await json(
          route,
          meFor("bid_writer", {
            username: "admin_local",
            isOwner: true,
            csrf: null,
          }),
        );
        return;
      }
      if (url.pathname === "/api/auth/csrf") {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(
        route,
        { detail: { code: "auth_required", message: "需要登录" } },
        401,
      );
    });

    await page.goto("/create");
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.getByText("标书生成")).toHaveCount(0);
    await assertNoSensitiveStorage(page);
  });

  for (const role of ["finance", "hr", "bidder"] as AuthRole[]) {
    test(`${role} 无业务导航且业务直链重定向`, async ({ page }) => {
      await installRoleSession(page, role);
      await page.goto("/create");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).not.toContainText("标书生成");
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).not.toContainText("知识库");
      await page.goto("/knowledge-base");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText("当前账号无权访问该功能")).toBeVisible();
      await assertNoSensitiveStorage(page);
    });
  }

  test("业务 API 仅走同源 /api，无外部业务主机", async ({ page }) => {
    const externalApi: string[] = [];
    page.on("request", (req) => {
      try {
        const u = new URL(req.url());
        if (isLegacyFontUrl(u.href)) return;
        if (["127.0.0.1", "localhost"].includes(u.hostname)) return;
        // 仅关心 API/XHR/fetch 类业务请求
        const rt = req.resourceType();
        if (rt === "xhr" || rt === "fetch" || u.pathname.startsWith("/api")) {
          externalApi.push(req.url());
        }
      } catch {
        /* ignore */
      }
    });

    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("shell-user")).toBeVisible();
    expect(externalApi, `意外外网 API: ${externalApi.join(", ")}`).toEqual([]);
  });
});
