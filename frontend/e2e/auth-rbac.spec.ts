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
type AuthMemberPayload = {
  userId: string;
  username: string;
  role: AuthRole;
  isOwner: boolean;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
};

type SwitchPutRecord = {
  workspaceId: string | null;
  rawBody: string;
  csrf: string | null;
  xWorkspaceId: string | null;
  method: string;
  path: string;
};

type SwitchMode =
  | "ok"
  | "http_error"
  | "abort"
  | "hang"
  | "commit_then_abort"
  | "bad_active"
  | "bad_user"
  | "bad_role"
  | "bad_missing_ws"
  /** PUT 合法：目标原为 finance，服务端返回并提交 hr（角色与初始选项不同） */
  | "server_role_hr"
  /** PUT 合法但 body 夹带恶意 csrfToken（parser 必须丢弃） */
  | "ok_with_csrf_marker";

type MembersMode =
  | "ok"
  | "http_error"
  | "hang"
  | "bad_array"
  | "bad_member"
  /** 合法 7 字段 + password/token/marker 额外键：整批失败 */
  | "extra_sensitive";

/** GET /auth/me 响应模式 */
type MeMode =
  | "ok"
  /** 畸形 /me：坏 user/workspaces，且可夹带 csrf marker */
  | "malformed"
  /** 形状合法但夹带恶意 csrfToken（parser 必须丢弃） */
  | "ok_with_csrf_marker"
  /** workspaces 非空但 activeWorkspaceId=null：必须拒绝，不得回退首项 */
  | "active_null_with_spaces"
  /** 重复 workspace id：必须拒绝整响应 */
  | "duplicate_workspace_id";

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
  /** P13-E：活动空间切换 PUT 记录 */
  switchPuts?: SwitchPutRecord[];
  switchMode?: SwitchMode;
  /** hang 模式：等待此 Promise 再响应 */
  switchHangPromise?: Promise<void>;
  meGets?: number;
  /** GET /me 模式；默认 ok */
  meMode?: MeMode;
  /** 注入到 /me 或 PUT 的恶意 CSRF 标记（可观察，禁止源码假测） */
  csrfEvilMarker?: string;
  membersGets?: number;
  membersMode?: MembersMode;
  membersHangPromise?: Promise<void>;
  membersList?: AuthMemberPayload[];
  lastMembersCsrf?: string | null;
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
      // 默认合法会话；meMode 特殊形状覆盖 login body，真实到达 parser
      const baseSession = meFor("bid_writer", {
        username: "admin_local",
        isOwner: true,
        csrf,
      });
      state.session = baseSession;
      state.bootstrapped = true;
      const loginMode: MeMode = state.meMode ?? "ok";
      if (loginMode === "active_null_with_spaces") {
        await json(route, {
          ...baseSession,
          activeWorkspaceId: null,
          csrfToken: csrf,
        });
        return;
      }
      if (loginMode === "duplicate_workspace_id") {
        const ws = baseSession.workspaces[0];
        await json(route, {
          ...baseSession,
          workspaces: [ws, { ...ws, name: "重复同 id 空间" }],
          activeWorkspaceId: ws.id,
          csrfToken: csrf,
        });
        return;
      }
      await json(route, baseSession);
      return;
    }

    if (path === "/api/auth/me" && method === "GET") {
      state.meGets = (state.meGets ?? 0) + 1;
      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      const meMode: MeMode = state.meMode ?? "ok";
      if (meMode === "malformed") {
        // 畸形对账体：坏形状 + 可选恶意 CSRF；前端不得 applyMe / 进 authenticated
        await json(route, {
          user: "not-an-object",
          workspaces: "not-array",
          activeWorkspaceId: 12345,
          csrfToken: state.csrfEvilMarker ?? "EVIL_CSRF_FROM_MALFORMED_ME",
        });
        return;
      }
      if (meMode === "ok_with_csrf_marker") {
        // 形状合法但夹带 csrfToken：parser 必须丢弃，不得覆盖内存 CSRF
        await json(route, {
          ...state.session,
          csrfToken: state.csrfEvilMarker ?? "EVIL_CSRF_FROM_ME",
        });
        return;
      }
      if (meMode === "active_null_with_spaces") {
        // 有空间但 active=null：parser 必须拒绝，不得 fallback 首项赋权
        await json(route, {
          ...state.session,
          activeWorkspaceId: null,
          csrfToken: null,
        });
        return;
      }
      if (meMode === "duplicate_workspace_id") {
        const ws =
          state.session.workspaces[0] ??
          ({
            id: "ws_dup",
            name: "重复空间",
            role: "bid_writer" as AuthRole,
            isOwner: true,
          } as const);
        await json(route, {
          ...state.session,
          workspaces: [ws, { ...ws, name: `${ws.name}-副本` }],
          activeWorkspaceId: ws.id,
          csrfToken: null,
        });
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

    if (path === "/api/auth/active-workspace" && method === "PUT") {
      const headers = req.headers();
      const rawBody = req.postData() ?? "";
      let workspaceId: string | null = null;
      try {
        const parsed = JSON.parse(rawBody) as { workspaceId?: unknown };
        workspaceId =
          typeof parsed.workspaceId === "string" ? parsed.workspaceId : null;
      } catch {
        workspaceId = null;
      }
      state.switchPuts = state.switchPuts ?? [];
      state.switchPuts.push({
        workspaceId,
        rawBody,
        csrf: headers["x-csrf-token"] ?? null,
        xWorkspaceId: headers["x-workspace-id"] ?? null,
        method,
        path,
      });

      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }

      const mode: SwitchMode = state.switchMode ?? "ok";
      if (mode === "hang" && state.switchHangPromise) {
        await state.switchHangPromise;
      }

      const allowed = state.session.workspaces.some((w) => w.id === workspaceId);
      if (!workspaceId || !allowed) {
        await json(
          route,
          { detail: { code: "forbidden", message: "无权切换到该工作空间" } },
          403,
        );
        return;
      }

      if (mode === "http_error") {
        await json(
          route,
          { detail: { code: "switch_failed", message: "服务端切换失败原文" } },
          500,
        );
        return;
      }
      if (mode === "abort") {
        await route.abort("failed");
        return;
      }
      if (mode === "commit_then_abort") {
        state.session = {
          ...state.session,
          activeWorkspaceId: workspaceId,
          csrfToken: null,
        };
        await route.abort("failed");
        return;
      }

      const next: MePayload = {
        ...state.session,
        activeWorkspaceId: workspaceId,
        csrfToken: null,
      };
      if (mode === "bad_active") {
        next.activeWorkspaceId = "ws_forged_active";
      } else if (mode === "bad_user") {
        next.user = { id: "user_forged", username: "forged" };
      } else if (mode === "bad_role") {
        next.workspaces = next.workspaces.map((w) =>
          w.id === workspaceId
            ? { ...w, role: "super_admin" as AuthRole }
            : w,
        );
      } else if (mode === "bad_missing_ws") {
        next.workspaces = next.workspaces.filter((w) => w.id !== workspaceId);
      } else if (mode === "server_role_hr") {
        // 真实新角色：初始选项为 finance，PUT 合法返回并提交 hr
        next.workspaces = next.workspaces.map((w) =>
          w.id === workspaceId
            ? {
                ...w,
                role: "hr",
                name: "服务端人力空间",
                isOwner: false,
              }
            : w,
        );
        state.session = next;
        await json(route, {
          ...state.session,
          csrfToken: state.csrfEvilMarker ?? null,
        });
        return;
      } else if (mode === "ok_with_csrf_marker") {
        state.session = next;
        await json(route, {
          ...state.session,
          csrfToken: state.csrfEvilMarker ?? "EVIL_CSRF_FROM_PUT",
        });
        return;
      } else {
        state.session = next;
      }

      if (
        mode === "bad_active" ||
        mode === "bad_user" ||
        mode === "bad_role" ||
        mode === "bad_missing_ws"
      ) {
        // 坏响应不得写入服务端会话；仅返回畸形 body
        await json(route, next);
        return;
      }

      await json(route, state.session);
      return;
    }

    if (path === "/api/auth/members" && method === "GET") {
      state.membersGets = (state.membersGets ?? 0) + 1;
      state.lastMembersCsrf = req.headers()["x-csrf-token"] ?? null;
      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      const mmode: MembersMode = state.membersMode ?? "ok";
      if (mmode === "hang" && state.membersHangPromise) {
        await state.membersHangPromise;
      }
      if (mmode === "http_error") {
        await json(
          route,
          { detail: { code: "members_failed", message: "成员接口失败原文" } },
          500,
        );
        return;
      }
      if (mmode === "bad_array") {
        await json(route, { not: "an-array" });
        return;
      }
      if (mmode === "bad_member") {
        await json(route, [
          {
            userId: "u_bad",
            username: "bad_member",
            // 缺 role/isOwner/isActive/createdAt/updatedAt
          },
        ]);
        return;
      }
      if (mmode === "extra_sensitive") {
        // 7 合法字段 + 额外 password/token/marker：必须整批失败、零半列表
        await json(route, [
          {
            userId: "user_extra_secret_id",
            username: "extra_user_visible",
            role: "bid_writer",
            isOwner: true,
            isActive: true,
            createdAt: "2026-01-01T00:00:00.000Z",
            updatedAt: "2026-01-02T00:00:00.000Z",
            password: "P13E_MEM_PASSWORD",
            token: "P13E_MEM_TOKEN",
            marker: "P13E_EXTRA_SENSITIVE_MARKER",
          },
        ]);
        return;
      }
      const active = state.session.workspaces.find(
        (w) => w.id === state.session!.activeWorkspaceId,
      );
      if (!active?.isOwner) {
        await json(
          route,
          { detail: { code: "forbidden", message: "仅所有者可查看成员" } },
          403,
        );
        return;
      }
      const list =
        state.membersList ??
        ([
          {
            userId: "user_owner_secret",
            username: state.session.user.username,
            role: active.role,
            isOwner: true,
            isActive: true,
            createdAt: "2026-01-01T00:00:00.000Z",
            updatedAt: "2026-01-02T00:00:00.000Z",
          },
          {
            userId: "user_inactive_secret",
            username: "inactive_member",
            role: "finance",
            isOwner: false,
            isActive: false,
            createdAt: "2026-01-03T00:00:00.000Z",
            updatedAt: "2026-01-04T00:00:00.000Z",
          },
        ] satisfies AuthMemberPayload[]);
      await json(route, list);
      return;
    }

    if (path === "/api/settings" && method === "GET") {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "https://example.invalid/v1",
        apiKey: "",
        model: "e2e-model",
        parseStrategy: "light",
        embeddingModel: "",
      });
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


/** P13-E：构造双空间会话 me */
function meDual(
  activeId: string,
  spaces: MePayload["workspaces"],
  opts: { username?: string; userId?: string; csrf?: string | null } = {},
): MePayload {
  return {
    user: {
      id: opts.userId ?? "user_p13e_owner",
      username: opts.username ?? "owner_p13e",
    },
    workspaces: spaces,
    activeWorkspaceId: activeId,
    csrfToken: opts.csrf === undefined ? null : opts.csrf,
  };
}

function createGate(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const ROLE_HOME: Record<AuthRole, string> = {
  bid_writer: "/create",
  finance: "/finance",
  hr: "/hr",
  bidder: "/bidder",
};

const ROLE_ZH: Record<AuthRole, string> = {
  bid_writer: "标书制作者",
  finance: "财务",
  hr: "人力",
  bidder: "投标人",
};

async function installP13eSession(
  page: Page,
  opts: {
    activeId?: string;
    spaces?: MePayload["workspaces"];
    isOwnerActive?: boolean;
    username?: string;
    switchMode?: SwitchMode;
    membersMode?: MembersMode;
    membersList?: AuthMemberPayload[];
    meMode?: MeMode;
    csrfEvilMarker?: string;
    resumeCsrf?: string;
  } = {},
) {
  const spaces =
    opts.spaces ??
    ([
      {
        id: "ws_alpha",
        name: "阿尔法空间",
        role: "bid_writer",
        isOwner: true,
      },
      {
        id: "ws_beta",
        name: "贝塔空间",
        role: "finance",
        isOwner: false,
      },
    ] satisfies MePayload["workspaces"]);
  const activeId = opts.activeId ?? spaces[0].id;
  const state: RequiredAuthState = {
    bootstrapped: true,
    session: meDual(activeId, spaces, {
      username: opts.username ?? "owner_p13e",
      csrf: null,
    }),
    resumeCsrf: opts.resumeCsrf ?? "e2e-csrf-p13e",
    switchPuts: [],
    switchMode: opts.switchMode ?? "ok",
    membersMode: opts.membersMode ?? "ok",
    membersList: opts.membersList,
    meMode: opts.meMode ?? "ok",
    csrfEvilMarker: opts.csrfEvilMarker,
    meGets: 0,
    membersGets: 0,
  };
  await installRequiredAuthRoutes(page, state);
  return state;
}

/**
 * 用途：等待浏览器事件队列/下一帧绘制（同步门，非 sleep）。
 * 用于 dispatch/change 后确认同步侧效已处理完毕再读计数。
 */
async function waitForNextFrame(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      }),
  );
}

test.describe("P13-E 活动工作空间切换与成员只读可见性", () => {
  test("双空间显示选择器；同值与非法值零 PUT；合法切换精确一次 PUT/体/CSRF/零 X-Workspace-Id 并整页落点", async ({
    page,
  }) => {
    const state = await installP13eSession(page);
    const external: string[] = [];
    page.on("request", (req) => {
      try {
        const u = new URL(req.url());
        if (isLegacyFontUrl(u.href)) return;
        if (u.hostname === "127.0.0.1" || u.hostname === "localhost") return;
        external.push(req.url());
      } catch {
        /* ignore */
      }
    });

    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("shell-user")).toContainText("阿尔法空间");

    const switcher = page.getByTestId("workspace-switcher");
    await expect(switcher).toBeVisible();
    await expect(switcher).toHaveValue("ws_alpha");

    // 同值：select Promise 返回后同步门——精确断言零请求
    await switcher.selectOption("ws_alpha");
    expect((state.switchPuts ?? []).length).toBe(0);

    // 非法值：DOM 注入后 change，前端必须零请求
    await switcher.evaluate((el) => {
      const select = el as HTMLSelectElement;
      const opt = document.createElement("option");
      opt.value = "ws_injected_evil";
      opt.textContent = "注入空间";
      select.appendChild(opt);
      select.value = "ws_injected_evil";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await waitForNextFrame(page);
    expect((state.switchPuts ?? []).length).toBe(0);
    await expect(switcher).toHaveValue("ws_alpha");

    // 空白包裹合法 ID：必须原值精确匹配，零 PUT（不得 trim 别名）
    await switcher.evaluate((el) => {
      const select = el as HTMLSelectElement;
      const opt = document.createElement("option");
      opt.value = "  ws_beta  ";
      opt.textContent = "空白包裹贝塔";
      select.appendChild(opt);
      select.value = "  ws_beta  ";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await waitForNextFrame(page);
    expect((state.switchPuts ?? []).length).toBe(0);
    await expect(switcher).toHaveValue("ws_alpha");

    // 标记旧页内存
    await page.evaluate(() => {
      (window as unknown as { __P13E_OLD_PAGE?: boolean }).__P13E_OLD_PAGE = true;
    });

    await switcher.selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 15_000 })
      .toBe(1);

    const put = state.switchPuts![0];
    expect(JSON.parse(put.rawBody)).toEqual({ workspaceId: "ws_beta" });
    expect(put.csrf).toBe("e2e-csrf-p13e");
    expect(put.xWorkspaceId).toBeFalsy();
    expect(put.path).toBe("/api/auth/active-workspace");
    expect(put.method).toBe("PUT");

    // 整页重载 + finance 落点
    await expect(page).toHaveURL(/\/finance$/, { timeout: 20_000 });
    const oldMarker = await page.evaluate(
      () =>
        (window as unknown as { __P13E_OLD_PAGE?: boolean }).__P13E_OLD_PAGE ===
        true,
    );
    expect(oldMarker, "旧页面内存必须被整页重载清空").toBeFalsy();
    await expect(page.getByTestId("shell-user")).toContainText("贝塔空间");
    await expect(page.getByTestId("shell-user")).toContainText("财务");
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_beta");
    expect(state.switchPuts ?? []).toHaveLength(1);
    expect(external, `意外外网: ${external.join(",")}`).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  for (const role of ["bid_writer", "finance", "hr", "bidder"] as AuthRole[]) {
    test(`四角色落点：切换到 ${role} 整页导航 ${ROLE_HOME[role]}`, async ({
      page,
    }) => {
      const spaces: MePayload["workspaces"] = [
        {
          id: "ws_home_a",
          name: "起点空间",
          role: role === "bid_writer" ? "finance" : "bid_writer",
          isOwner: true,
        },
        {
          id: "ws_home_b",
          name: `目标-${role}`,
          role,
          isOwner: role === "bid_writer",
        },
      ];
      const state = await installP13eSession(page, {
        activeId: "ws_home_a",
        spaces,
      });
      await page.goto(ROLE_HOME[spaces[0].role]);
      await expect(page.getByTestId("workspace-switcher")).toBeVisible({
        timeout: 20_000,
      });
      await page.getByTestId("workspace-switcher").selectOption("ws_home_b");
      await expect
        .poll(() => (state.switchPuts ?? []).length, { timeout: 15_000 })
        .toBe(1);
      await expect(page).toHaveURL(new RegExp(`${ROLE_HOME[role]}$`), {
        timeout: 20_000,
      });
      await expect(page.getByTestId("shell-user")).toContainText(ROLE_ZH[role]);
      await expect(page.getByTestId("shell-user")).toContainText(`目标-${role}`);
    });
  }

  test("单空间显示真实名称且选择不产生 PUT", async ({ page }) => {
    const state = await installP13eSession(page, {
      spaces: [
        {
          id: "ws_only",
          name: "唯一空间",
          role: "bid_writer",
          isOwner: true,
        },
      ],
      activeId: "ws_only",
    });
    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("workspace-switcher")).toBeDisabled();
    await expect(page.getByTestId("shell-user")).toContainText("唯一空间");
    await page.getByTestId("workspace-switcher").evaluate((el) => {
      (el as HTMLSelectElement).dispatchEvent(
        new Event("change", { bubbles: true }),
      );
    });
    await waitForNextFrame(page);
    expect((state.switchPuts ?? []).length).toBe(0);
  });

  test("切换单飞：hang 期间重复选择不产生第二个 PUT", async ({ page }) => {
    const gate = createGate();
    const state = await installP13eSession(page, { switchMode: "hang" });
    state.switchHangPromise = gate.promise;
    await page.goto("/create");
    const switcher = page.getByTestId("workspace-switcher");
    await expect(switcher).toBeVisible({ timeout: 20_000 });
    await switcher.selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    await expect(switcher).toBeDisabled();
    await expect(page.getByTestId("workspace-switch-status")).toContainText(
      "正在切换工作空间",
    );
    // 快速重复：dispatch 返回 + rAF 同步门后精确断言仍为 1
    await switcher.evaluate((el) => {
      const s = el as HTMLSelectElement;
      s.disabled = false;
      s.value = "ws_beta";
      s.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await waitForNextFrame(page);
    expect((state.switchPuts ?? []).length).toBe(1);
    gate.resolve();
    await expect(page).toHaveURL(/\/finance$/, { timeout: 20_000 });
    expect(state.switchPuts ?? []).toHaveLength(1);
  });

  test("失败对账：commit_then_abort 后 /me 确认已切换则整页成功落点", async ({
    page,
  }) => {
    const state = await installP13eSession(page, {
      switchMode: "commit_then_abort",
    });
    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    const meBefore = state.meGets ?? 0;
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => (state.meGets ?? 0) > meBefore, { timeout: 10_000 })
      .toBeTruthy();
    await expect(page).toHaveURL(/\/finance$/, { timeout: 20_000 });
    await expect(page.getByTestId("shell-user")).toContainText("贝塔空间");
  });

  test("失败对账：真正失败仍停留原空间并显示固定中文错误", async ({ page }) => {
    const state = await installP13eSession(page, { switchMode: "http_error" });
    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("workspace-switch-status")).toContainText(
      "工作空间切换失败，请重试",
    );
    await expect(page.getByTestId("workspace-switch-status")).not.toContainText(
      "服务端切换失败原文",
    );
    await expect(page).toHaveURL(/\/create/);
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_alpha");
    await expect(page.getByTestId("shell-user")).toContainText("阿尔法空间");
    // 可重试：改 mode 后再次切换
    state.switchMode = "ok";
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(2);
    await expect(page).toHaveURL(/\/finance$/, { timeout: 20_000 });
  });

  test("坏响应不得写入业务态：bad_active 走对账后保持原空间", async ({
    page,
  }) => {
    const state = await installP13eSession(page, { switchMode: "bad_active" });
    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("workspace-switch-status")).toContainText(
      "工作空间切换失败，请重试",
    );
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_alpha");
    await expect(page.getByTestId("shell-user")).not.toContainText("ws_forged");
  });

  test("required 设置页展示真实活动空间名称/ID/角色/所有者；owner 显式一次成员 GET", async ({
    page,
  }) => {
    const state = await installP13eSession(page);
    const consoleLines: string[] = [];
    page.on("console", (msg) => consoleLines.push(msg.text()));
    const external: string[] = [];
    page.on("request", (req) => {
      try {
        const u = new URL(req.url());
        if (isLegacyFontUrl(u.href)) return;
        if (u.hostname === "127.0.0.1" || u.hostname === "localhost") return;
        external.push(req.url());
      } catch {
        /* ignore */
      }
    });

    await page.goto("/settings");
    await expect(page.getByTestId("settings-workspace-name")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("settings-workspace-name")).toHaveValue(
      "阿尔法空间",
    );
    await expect(page.getByTestId("settings-workspace-id")).toHaveValue(
      "ws_alpha",
    );
    await expect(page.getByTestId("settings-workspace-role")).toHaveValue(
      "标书制作者",
    );
    await expect(page.getByTestId("settings-workspace-owner")).toHaveValue(
      "是",
    );
    // 不得残留假值
    await expect(page.locator("body")).not.toContainText("我的工作空间（后端）");
    await expect(page.getByTestId("settings-workspace-id")).not.toHaveValue(
      "ws_local",
    );

    // 进入设置不自动请求 members
    expect(state.membersGets ?? 0).toBe(0);
    const loadBtn = page.getByTestId("load-members-button");
    await expect(loadBtn).toBeVisible();
    await loadBtn.click();
    await expect
      .poll(() => state.membersGets ?? 0, { timeout: 10_000 })
      .toBe(1);
    // 已加载后再次 click：Promise 返回后同步精确断言，不增请求
    await loadBtn.click();
    expect(state.membersGets ?? 0).toBe(1);

    const list = page.getByTestId("members-list");
    await expect(list).toBeVisible();
    await expect(list).toContainText("owner_p13e");
    await expect(list).toContainText("inactive_member");
    await expect(list).toContainText("财务");
    await expect(list).toContainText("停用");
    await expect(list).toContainText("启用");
    await expect(list).not.toContainText("在线");
    await expect(list).not.toContainText("user_owner_secret");
    await expect(list).not.toContainText("user_inactive_secret");
    await expect(list).not.toContainText("2026-01-01");

    // userId 不进 DOM 属性 / title / URL / storage / console / 外网
    const leak = await page.evaluate(() => {
      const html = document.documentElement.outerHTML;
      const href = location.href;
      const ls = JSON.stringify(localStorage);
      const ss = JSON.stringify(sessionStorage);
      return { html, href, ls, ss };
    });
    expect(leak.html).not.toContain("user_owner_secret");
    expect(leak.html).not.toContain("user_inactive_secret");
    expect(leak.href).not.toContain("user_owner_secret");
    expect(leak.ls).not.toContain("user_owner_secret");
    expect(leak.ss).not.toContain("user_inactive_secret");
    expect(consoleLines.join("\n")).not.toContain("user_owner_secret");
    expect(consoleLines.join("\n")).not.toContain("user_inactive_secret");
    expect(external).toEqual([]);
  });

  test("成员坏数组整批失败；显式重试成功；加载中单飞", async ({ page }) => {
    const gate = createGate();
    const state = await installP13eSession(page, { membersMode: "bad_array" });
    await page.goto("/settings");
    await expect(page.getByTestId("load-members-button")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("load-members-button").click();
    await expect(page.getByTestId("members-status")).toContainText(
      "成员列表加载失败，请重试",
    );
    await expect(page.getByTestId("members-list")).toHaveCount(0);
    expect(state.membersGets ?? 0).toBe(1);

    state.membersMode = "hang";
    state.membersHangPromise = gate.promise;
    await page.getByTestId("load-members-button").click();
    await expect
      .poll(() => state.membersGets ?? 0, { timeout: 10_000 })
      .toBe(2);
    await expect(page.getByTestId("load-members-button")).toBeDisabled();
    // 加载中单飞：disabled 下再次 click；rAF 同步门后精确断言不增
    await page.getByTestId("load-members-button").evaluate((el) => {
      (el as HTMLButtonElement).click();
    });
    await waitForNextFrame(page);
    expect(state.membersGets ?? 0).toBe(2);
    state.membersMode = "ok";
    gate.resolve();
    await expect(page.getByTestId("members-list")).toBeVisible({
      timeout: 15_000,
    });
    expect(state.membersGets ?? 0).toBe(2);
  });

  test("坏成员项整批失败不展示半真半假", async ({ page }) => {
    const state = await installP13eSession(page, { membersMode: "bad_member" });
    await page.goto("/settings");
    await expect(page.getByTestId("load-members-button")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("load-members-button").click();
    await expect
      .poll(() => state.membersGets ?? 0, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("members-status")).toContainText(
      "成员列表加载失败，请重试",
    );
    await expect(page.getByTestId("members-list")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("bad_member");
  });

  test("非 owner 不显示成员入口且零 members 请求", async ({ page }) => {
    const state = await installP13eSession(page, {
      spaces: [
        {
          id: "ws_member_only",
          name: "成员空间",
          role: "bid_writer",
          isOwner: false,
        },
      ],
      activeId: "ws_member_only",
    });
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({ timeout: 20_000 });
    // 非 owner 无设置导航；直链应受限或无加载按钮
    await page.goto("/settings");
    // 可能重定向到 restricted 或进入但无按钮
    await expect(page.getByTestId("load-members-button")).toHaveCount(0, {
      timeout: 10_000,
    });
    // 无入口同步门：页面稳定后直接精确断言零 members
    expect(state.membersGets ?? 0).toBe(0);
  });

  test("disabled 模式设置页明确个人版且零 members 请求；无切换选择器", async ({
    page,
  }) => {
    let membersHits = 0;
    await page.route("**/api/auth/members", async (route) => {
      membersHits += 1;
      await route.fulfill({ status: 500, body: "{}" });
    });
    await page.goto("/settings");
    await expect(page.getByTestId("settings-workspace-name")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("settings-workspace-name")).toHaveValue(
      /个人版/,
    );
    await expect(page.getByTestId("load-members-button")).toHaveCount(0);
    await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
    expect(membersHits).toBe(0);
  });

  test("未登录 required 不显示选择器且零切换请求", async ({ page }) => {
    const state: RequiredAuthState = {
      bootstrapped: true,
      session: null,
      switchPuts: [],
    };
    await installRequiredAuthRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
    expect(state.switchPuts ?? []).toHaveLength(0);
  });

  test("新角色真实落点：目标原 finance，PUT 合法返回并提交 hr，落 /hr 不落 /finance", async ({
    page,
  }) => {
    const state = await installP13eSession(page, {
      switchMode: "server_role_hr",
      spaces: [
        {
          id: "ws_alpha",
          name: "阿尔法空间",
          role: "bid_writer",
          isOwner: true,
        },
        {
          id: "ws_beta",
          name: "贝塔财务空间",
          role: "finance",
          isOwner: false,
        },
      ],
      activeId: "ws_alpha",
    });
    // 记录初始选项角色为 finance，与 PUT 提交角色不同
    const initialBeta = state.session!.workspaces.find((w) => w.id === "ws_beta");
    expect(initialBeta?.role).toBe("finance");

    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 15_000 })
      .toBe(1);
    // 服务端会话角色必须已变为 hr（桩真实提交）
    expect(
      state.session!.workspaces.find((w) => w.id === "ws_beta")?.role,
    ).toBe("hr");
    // 整页落人力首页，禁止旧 finance 路径
    await expect(page).toHaveURL(/\/hr$/, { timeout: 20_000 });
    await expect(page).not.toHaveURL(/\/finance/);
    await expect(page.getByTestId("shell-user")).toContainText("人力");
    await expect(page.getByTestId("shell-user")).toContainText(
      "服务端人力空间",
    );
    await expect(page.getByTestId("shell-user")).not.toContainText("财务");
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_beta");
  });

  test("失败对账后 /me 畸形：不得污染 authenticated 业务壳、不导航", async ({
    page,
  }) => {
    const state = await installP13eSession(page, {
      switchMode: "http_error",
      meMode: "ok",
    });
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_alpha");

    // 切换前切到畸形 /me：PUT 失败后 refresh 必须到达畸形体且拒绝 applyMe
    state.meMode = "malformed";
    state.csrfEvilMarker = "EVIL_CSRF_MALFORMED_RECONCILE";
    const meBefore = state.meGets ?? 0;
    const urlBefore = page.url();

    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => (state.meGets ?? 0) > meBefore, { timeout: 10_000 })
      .toBeTruthy();

    // 不得导航到目标角色首页
    await expect(page).not.toHaveURL(/\/finance/);
    expect(page.url()).toBe(urlBefore);

    // 不得继续显示可写业务壳/旧空间选择器（坏 me → unauthenticated）
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("阿尔法空间");
    await expect(page.locator("body")).not.toContainText(
      "EVIL_CSRF_MALFORMED_RECONCILE",
    );
  });

  test("CSRF 边界：PUT/me 夹带恶意 csrfToken 不得覆盖内存；退出仍用续发 CSRF", async ({
    page,
  }) => {
    const evil = "EVIL_CSRF_MARKER_P13E_SWITCH";
    const good = "e2e-csrf-p13e-good";
    const state = await installP13eSession(page, {
      switchMode: "http_error",
      meMode: "ok_with_csrf_marker",
      csrfEvilMarker: evil,
      resumeCsrf: good,
    });
    await page.goto("/create");
    await expect(page.getByTestId("workspace-switcher")).toBeVisible({
      timeout: 20_000,
    });
    // 首屏 /me 已带 evil marker 且续发 good；内存 CSRF 应为 good
    await expect
      .poll(() => state.csrfCalls ?? 0, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);

    const meBefore = state.meGets ?? 0;
    await page.getByTestId("workspace-switcher").selectOption("ws_beta");
    await expect
      .poll(() => (state.switchPuts ?? []).length, { timeout: 10_000 })
      .toBe(1);
    // PUT 失败后对账 /me 再次带 evil
    await expect
      .poll(() => (state.meGets ?? 0) > meBefore, { timeout: 10_000 })
      .toBeTruthy();
    await expect(page.getByTestId("workspace-switch-status")).toContainText(
      "工作空间切换失败，请重试",
    );
    // 仍停留原空间，未导航
    await expect(page).toHaveURL(/\/create/);
    await expect(page.getByTestId("workspace-switcher")).toHaveValue("ws_alpha");

    // 退出：请求头必须是续发 good，绝不能是 evil marker
    await page.getByTestId("logout-button").click();
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    expect(state.lastLogoutCsrf).toBe(good);
    expect(state.lastLogoutCsrf).not.toBe(evil);
    await expect(page.locator("body")).not.toContainText(evil);
  });

  test("成员 extra_sensitive 额外键整批失败：零半列表，marker/userId 不泄露", async ({
    page,
  }) => {
    const marker = "P13E_EXTRA_SENSITIVE_MARKER";
    const secretId = "user_extra_secret_id";
    const consoleLines: string[] = [];
    page.on("console", (msg) => consoleLines.push(msg.text()));
    const external: string[] = [];
    page.on("request", (req) => {
      try {
        const u = new URL(req.url());
        if (isLegacyFontUrl(u.href)) return;
        if (u.hostname === "127.0.0.1" || u.hostname === "localhost") return;
        external.push(req.url());
      } catch {
        /* ignore */
      }
    });

    const state = await installP13eSession(page, {
      membersMode: "extra_sensitive",
    });
    await page.goto("/settings");
    await expect(page.getByTestId("load-members-button")).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("load-members-button").click();
    // 确认额外字段桩已真实到达（members GET 计数）
    await expect
      .poll(() => state.membersGets ?? 0, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("members-status")).toContainText(
      "成员列表加载失败，请重试",
    );
    await expect(page.getByTestId("members-list")).toHaveCount(0);
    // 零半列表：合法 username 也不得展示
    await expect(page.locator("body")).not.toContainText("extra_user_visible");
    await expect(page.locator("body")).not.toContainText(marker);
    await expect(page.locator("body")).not.toContainText(secretId);
    await expect(page.locator("body")).not.toContainText("P13E_MEM_PASSWORD");
    await expect(page.locator("body")).not.toContainText("P13E_MEM_TOKEN");

    const leak = await page.evaluate(() => {
      const html = document.documentElement.outerHTML;
      const titles = Array.from(document.querySelectorAll("[title]")).map(
        (el) => el.getAttribute("title") ?? "",
      );
      return {
        html,
        href: location.href,
        ls: JSON.stringify(localStorage),
        ss: JSON.stringify(sessionStorage),
        titles: titles.join("|"),
      };
    });
    for (const needle of [
      marker,
      secretId,
      "P13E_MEM_PASSWORD",
      "P13E_MEM_TOKEN",
      "extra_user_visible",
    ]) {
      expect(leak.html, `html 不得含 ${needle}`).not.toContain(needle);
      expect(leak.href, `url 不得含 ${needle}`).not.toContain(needle);
      expect(leak.ls, `localStorage 不得含 ${needle}`).not.toContain(needle);
      expect(leak.ss, `sessionStorage 不得含 ${needle}`).not.toContain(needle);
      expect(leak.titles, `title 不得含 ${needle}`).not.toContain(needle);
      expect(consoleLines.join("\n"), `console 不得含 ${needle}`).not.toContain(
        needle,
      );
    }
    expect(external).toEqual([]);
  });

  test("required 设置页顶层说明不得自称个人版", async ({ page }) => {
    await installP13eSession(page);
    await page.goto("/settings");
    await expect(page.getByTestId("settings-workspace-name")).toBeVisible({
      timeout: 20_000,
    });
    const header = page.locator(".page-header");
    await expect(header).toBeVisible();
    await expect(header).toContainText("工作空间设置");
    await expect(header).not.toContainText("个人版：");
    // 工作空间区块 required 说明也非个人版默认空间
    await expect(page.getByTestId("settings-workspace-name")).toHaveValue(
      "阿尔法空间",
    );
  });

  test("GET /me：active_null_with_spaces 拒绝整响应，保守非业务态，不因首项获权", async ({
    page,
  }) => {
    // 首项为 owner + bid_writer：若 parser/UI 回退 workspaces[0] 会误开业务壳与选择器
    const state = await installP13eSession(page, {
      meMode: "active_null_with_spaces",
      spaces: [
        {
          id: "ws_first_owner",
          name: "首项所有者空间",
          role: "bid_writer",
          isOwner: true,
        },
        {
          id: "ws_second_finance",
          name: "次项财务空间",
          role: "finance",
          isOwner: false,
        },
      ],
      activeId: "ws_first_owner",
    });

    await page.goto("/create");
    // 坏 /me 必须真实到达 parser（计数>0）
    await expect
      .poll(() => state.meGets ?? 0, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);

    // 保守非业务态：登录页，无 AppShell/选择器/业务导航
    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(
      0,
    );
    // 不得因首项获得 role/owner 权限展示
    await expect(page.locator("body")).not.toContainText("首项所有者空间");
    await expect(page.locator("body")).not.toContainText("次项财务空间");
    await expect(page.locator("body")).not.toContainText("标书制作者");
    await expect(page.locator("body")).not.toContainText("财务");
    await expect(page.getByTestId("shell-user")).toHaveCount(0);
    // 零切换请求
    expect(state.switchPuts ?? []).toHaveLength(0);
  });

  test("GET /me：duplicate_workspace_id 拒绝整响应，保守非业务态", async ({
    page,
  }) => {
    const state = await installP13eSession(page, {
      meMode: "duplicate_workspace_id",
      spaces: [
        {
          id: "ws_dup_owner",
          name: "重复前所有者",
          role: "bid_writer",
          isOwner: true,
        },
      ],
      activeId: "ws_dup_owner",
    });

    await page.goto("/finance");
    await expect
      .poll(() => state.meGets ?? 0, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);

    await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("app-shell")).toHaveCount(0);
    await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(
      0,
    );
    await expect(page.locator("body")).not.toContainText("重复前所有者");
    await expect(page.locator("body")).not.toContainText("标书制作者");
    await expect(page.getByTestId("shell-user")).toHaveCount(0);
    expect(state.switchPuts ?? []).toHaveLength(0);
  });

  test("login：active_null_with_spaces / duplicate_workspace_id 不得进业务态", async ({
    page,
  }) => {
    // 覆盖 login 路径：坏 body 真实到达 parseLoginAuthMe
    for (const mode of [
      "active_null_with_spaces",
      "duplicate_workspace_id",
    ] as const) {
      const state: RequiredAuthState = {
        bootstrapped: true,
        session: null,
        loginCsrf: "e2e-csrf-login-bad-active",
        meMode: mode,
        switchPuts: [],
        meGets: 0,
      };
      await installRequiredAuthRoutes(page, state);

      await page.goto("/create");
      await expect(page.getByRole("heading", { name: "本机登录" })).toBeVisible(
        {
          timeout: 15_000,
        },
      );
      await page.locator('input[name="username"]').fill("admin_local");
      await page.locator('input[name="password"]').fill("E2e-Only-Not-Stored!");
      await page.getByRole("button", { name: "登录" }).click();

      // 仍停在登录页；坏响应不得开业务壳/选择器
      await expect(
        page.getByRole("heading", { name: "本机登录" }),
      ).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId("app-shell")).toHaveCount(0);
      await expect(page.getByTestId("workspace-switcher")).toHaveCount(0);
      await expect(
        page.getByRole("navigation", { name: "主导航" }),
      ).toHaveCount(0);
      // 不得因 meFor 默认首项 owner/bid_writer 获权
      await expect(page.locator("body")).not.toContainText("标书制作者");
      await expect(page.getByTestId("shell-user")).toHaveCount(0);
      expect(state.switchPuts ?? []).toHaveLength(0);
    }
  });

  test("合法 active 仍精确选中：双空间 active=ws_beta 选择器与壳展示命中次项", async ({
    page,
  }) => {
    // 对照：合法 active 必须精确命中，不得误选首项
    const state = await installP13eSession(page, {
      meMode: "ok",
      activeId: "ws_beta",
      spaces: [
        {
          id: "ws_alpha",
          name: "阿尔法空间",
          role: "bid_writer",
          isOwner: true,
        },
        {
          id: "ws_beta",
          name: "贝塔空间",
          role: "finance",
          isOwner: false,
        },
      ],
    });
    await page.goto("/finance");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 20_000,
    });
    await expect
      .poll(() => state.meGets ?? 0, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);
    const switcher = page.getByTestId("workspace-switcher");
    await expect(switcher).toBeVisible();
    // 选择值严格等于 activeWorkspaceId（次项），不得回退首项
    await expect(switcher).toHaveValue("ws_beta");
    const selectedLabel = await switcher.evaluate((el) => {
      const s = el as HTMLSelectElement;
      return s.options[s.selectedIndex]?.textContent ?? "";
    });
    expect(selectedLabel).toBe("贝塔空间");
    // title 只承载当前活动展示（用户·角色·空间），不含下拉全量选项
    const shellUser = page.getByTestId("shell-user");
    await expect(shellUser).toHaveAttribute(
      "title",
      "owner_p13e · 财务 · 贝塔空间",
    );
    await expect(shellUser).not.toHaveAttribute("title", /阿尔法空间/);
    await expect(shellUser).not.toHaveAttribute("title", /标书制作者/);
    // 顶栏同步展示活动真值（不经选择器 options 污染）
    await expect(page.getByTestId("topbar-user")).toContainText("贝塔空间");
    await expect(page.getByTestId("topbar-user")).toContainText("财务");
    await expect(page.getByTestId("topbar-user")).not.toContainText(
      "阿尔法空间",
    );
    await expect(page.getByTestId("topbar-user")).not.toContainText(
      "标书制作者",
    );
  });
});
