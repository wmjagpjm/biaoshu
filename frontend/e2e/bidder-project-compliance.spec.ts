/**
 * 模块：P10G 投标人项目级合规统计前端 E2E
 * 用途：验收 bidder 入口、选择器初始白名单、按需详情、ready/empty、角色门禁、
 *       SPA 项目切换无旧数据/无预取、错误脱敏、不请求 P10E、浏览器存储零写入。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:bidder-project-compliance。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/bidder/project-compliance*；
 *   阻断 projects/editor-state/settings/files/finance/hr/compliance-preview 与外网。
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

type ProjectItem = { id: string; name: string };

type DetailPayload = {
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

const PROJECT_A: ProjectItem = {
  id: "proj_tech_a_p10g",
  name: "E2E 技术标甲",
};
const PROJECT_B: ProjectItem = {
  id: "proj_tech_b_p10g",
  name: "E2E 技术标乙",
};

const DETAIL_A_READY: DetailPayload = {
  dataState: "ready",
  summary: {
    totalItems: 12,
    coveredItems: 9,
    uncoveredItems: 2,
    waivedItems: 1,
    coverageBasisPoints: 8182,
  },
};

const DETAIL_B_EMPTY: DetailPayload = {
  dataState: "empty",
  summary: {
    totalItems: 0,
    coveredItems: 0,
    uncoveredItems: 0,
    waivedItems: 0,
    coverageBasisPoints: null,
  },
};

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

type P10GAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  forbiddenHits: string[];
  allowedHits: string[];
  p10eHits: string[];
  projects: ProjectItem[];
  details: Record<string, DetailPayload>;
  /** 指定 projectId 返回失败详情 */
  forceDetailErrorFor?: string;
  detailDelayMs?: number;
};

/**
 * 用途：安装 auth + P10G 专用接口桩；阻断通用业务 API、P10E 聚合与外网。
 */
async function installP10GRoutes(page: Page, state: P10GAuthState) {
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

    // P10E 匿名聚合：P10G 页面不得请求
    if (path === "/api/bidder/compliance-preview") {
      state.p10eHits.push(`${method} ${path}`);
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        {
          detail: {
            code: "role_forbidden",
            message: "P10G 不得回退 P10E 聚合",
          },
        },
        403,
      );
      return;
    }

    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      state.allowedHits.push(`${method} ${path}`);
      await json(route, {
        bootstrapped: state.bootstrapped,
        authRequired: state.authRequired,
      });
      return;
    }

    if (!state.authRequired) {
      if (path === "/api/health" && method === "GET") {
        state.allowedHits.push(`${method} ${path}`);
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          defaultWorkspaceId: "ws_e2e",
        });
        return;
      }
      if (path.startsWith("/api/bidder/")) {
        state.forbiddenHits.push(`${method} ${path}`);
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
      state.allowedHits.push(`${method} ${path}`);
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
      state.allowedHits.push(`${method} ${path}`);
      if (!state.session) {
        await json(
          route,
          { detail: { code: "auth_required", message: "需要登录" } },
          401,
        );
        return;
      }
      await json(route, {
        csrfToken: state.resumeCsrf ?? "e2e-p10g-csrf",
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.allowedHits.push(`${method} ${path}`);
      state.session = null;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (path === "/api/health" && method === "GET") {
      state.allowedHits.push(`${method} ${path}`);
      await json(route, {
        status: "ok",
        service: "biaoshu-e2e",
        defaultWorkspaceId: "ws_e2e",
      });
      return;
    }

    if (
      path === "/api/bidder/project-compliance/projects" &&
      method === "GET"
    ) {
      state.allowedHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "bidder") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      await json(route, { items: state.projects });
      return;
    }

    const detailMatch = path.match(
      /^\/api\/bidder\/project-compliance\/([^/]+)$/,
    );
    if (detailMatch && method === "GET") {
      const projectId = decodeURIComponent(detailMatch[1]);
      state.allowedHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "bidder") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (state.forceDetailErrorFor === projectId) {
        await json(
          route,
          {
            detail: {
              code: "server_error",
              message: `SECRET_P10G_LEAK path=C:\\\\secret\\\\${projectId} apiKey=leak`,
            },
          },
          500,
        );
        return;
      }
      const body = state.details[projectId];
      if (!body) {
        await json(
          route,
          {
            detail: {
              code: "bidder_project_compliance_not_found",
              message: "项目合规统计不存在",
            },
          },
          404,
        );
        return;
      }
      if (state.detailDelayMs && state.detailDelayMs > 0) {
        await new Promise((r) => setTimeout(r, state.detailDelayMs));
      }
      await json(route, body);
      return;
    }

    if (path.startsWith("/api/bidder/")) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "未授权投标人接口" } },
        403,
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
      expect
        .soft(
          /project-compliance|coverageBasisPoints|responseMatrix|sourceKey|proj_tech/i.test(
            key,
          ),
          `${scope} 含合规相关 key=${key}`,
        )
        .toBeFalsy();
      expect
        .soft(
          /project-compliance|coverageBasisPoints|responseMatrix|sourceKey|proj_tech/i.test(
            value,
          ),
          `${scope} 含合规相关 value of ${key}`,
        )
        .toBeFalsy();
    }
  }
}

function bidderState(
  overrides: Partial<P10GAuthState> = {},
): P10GAuthState {
  return {
    bootstrapped: true,
    authRequired: true,
    session: meFor("bidder", {
      username: "user_bidder",
      isOwner: false,
      csrf: null,
    }),
    resumeCsrf: "e2e-p10g-resume",
    forbiddenHits: [],
    allowedHits: [],
    p10eHits: [],
    projects: [PROJECT_A, PROJECT_B],
    details: {
      [PROJECT_A.id]: DETAIL_A_READY,
      [PROJECT_B.id]: DETAIL_B_EMPTY,
    },
    ...overrides,
  };
}

test.describe("P10G 投标人项目级合规统计前端", () => {
  test("bidder 入口可见；初始仅选择器；按需详情；ready 统计；无 P10E/业务回退", async ({
    page,
  }) => {
    const state = bidderState();
    await installP10GRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("投标人");
    await expect(nav).toContainText("合规预览");
    await expect(nav).toContainText("项目合规");
    await expect(nav).not.toContainText("标书生成");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("人员资质");

    // 导航激活态：进入项目合规后，「合规预览」不应激活
    await nav.getByText("项目合规", { exact: true }).click();
    await expect(page.getByTestId("bidder-project-compliance-page")).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    await expect(page.getByRole("heading", { name: "项目合规" })).toBeVisible();

    const projectNav = nav.getByRole("link", { name: "项目合规" });
    const previewNav = nav.getByRole("link", { name: "合规预览" });
    await expect(projectNav).toHaveClass(/is-active/);
    await expect(previewNav).not.toHaveClass(/is-active/);

    // 初始：仅 projects（StrictMode 可能重复挂载导致重复 GET），不得详情、不得 P10E
    const initialAllowed = state.allowedHits.filter(
      (h) =>
        h.includes("/api/bidder/project-compliance") ||
        h.includes("/api/bidder/compliance-preview"),
    );
    expect(initialAllowed.length).toBeGreaterThan(0);
    expect(
      initialAllowed.every(
        (h) => h === "GET /api/bidder/project-compliance/projects",
      ),
      `初始非选择器请求: ${initialAllowed.join(", ")}`,
    ).toBeTruthy();
    expect(
      initialAllowed.some(
        (h) =>
          h.startsWith("GET /api/bidder/project-compliance/") &&
          !h.endsWith("/projects"),
      ),
    ).toBeFalsy();
    expect(state.p10eHits).toEqual([]);
    expect(
      state.forbiddenHits,
      `意外业务请求: ${state.forbiddenHits.join(", ")}`,
    ).toEqual([]);

    await expect(page.getByTestId("bpc-detail-idle")).toBeVisible();
    await expect(page.getByTestId("bpc-detail-stats")).toHaveCount(0);

    // 选择 A：按需详情
    await page.getByTestId("bpc-project-select").selectOption(PROJECT_A.id);
    await expect(page.getByTestId("bpc-detail-ready")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bpc-total-items")).toHaveText("12");
    await expect(page.getByTestId("bpc-covered-items")).toHaveText("9");
    await expect(page.getByTestId("bpc-uncovered-items")).toHaveText("2");
    await expect(page.getByTestId("bpc-waived-items")).toHaveText("1");
    await expect(page.getByTestId("bpc-coverage")).toContainText("81.82%");

    expect(
      state.allowedHits.some(
        (h) =>
          h ===
          `GET /api/bidder/project-compliance/${PROJECT_A.id}`,
      ),
    ).toBeTruthy();

    // 页面不得出现矩阵原文/内部键/SECRET
    const pageText = await page.locator("main").innerText();
    expect(pageText).not.toMatch(
      /sourceKey|editor-state|responseMatrix|SECRET_P10G/i,
    );
    expect(pageText).not.toMatch(/招标原文|章节标题|大纲标题/);
    // 下拉 option 可含名称，但不得出现矩阵或路径
    expect(pageText).not.toContain("apiKey");

    await expect(page.getByTestId("bpc-disclaimer")).toContainText(
      "不是评审结论",
    );

    expect(state.p10eHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("empty 态全零与暂无可计算覆盖率", async ({ page }) => {
    const state = bidderState();
    await installP10GRoutes(page, state);

    await page.goto("/bidder/project-compliance");
    await expect(page.getByTestId("bidder-project-compliance-page")).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    await page.getByTestId("bpc-project-select").selectOption(PROJECT_B.id);
    await expect(page.getByTestId("bpc-detail-empty")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bpc-total-items")).toHaveText("0");
    await expect(page.getByTestId("bpc-covered-items")).toHaveText("0");
    await expect(page.getByTestId("bpc-uncovered-items")).toHaveText("0");
    await expect(page.getByTestId("bpc-waived-items")).toHaveText("0");
    await expect(page.getByTestId("bpc-coverage")).toHaveText(
      "暂无可计算覆盖率",
    );
    expect(state.p10eHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("详情失败固定中文脱敏且不回显 SECRET/路径/ID", async ({ page }) => {
    const state = bidderState({
      forceDetailErrorFor: PROJECT_A.id,
    });
    await installP10GRoutes(page, state);

    await page.goto("/bidder/project-compliance");
    await expect(page.getByTestId("bidder-project-compliance-page")).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    await page.getByTestId("bpc-project-select").selectOption(PROJECT_A.id);
    const err = page.getByTestId("bpc-detail-error");
    await expect(err).toBeVisible({ timeout: 15_000 });
    await expect(err).toHaveText("暂时无法读取项目合规统计");
    await expect(err).not.toContainText("SECRET");
    await expect(err).not.toContainText("apiKey");
    await expect(err).not.toContainText("server_error");
    await expect(err).not.toContainText(PROJECT_A.id);
    await expect(err).not.toContainText("/api/bidder");
    await assertNoSensitiveStorage(page);
  });

  test("SPA 切换 A→B：旧统计立即消失且未动作前不请求 B 详情", async ({
    page,
  }) => {
    const state = bidderState({ detailDelayMs: 80 });
    await installP10GRoutes(page, state);

    await page.goto("/bidder/project-compliance");
    await expect(page.getByTestId("bidder-project-compliance-page")).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    await page.getByTestId("bpc-project-select").selectOption(PROJECT_A.id);
    await expect(page.getByTestId("bpc-detail-ready")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bpc-total-items")).toHaveText("12");

    const hitsBeforeSwitch = state.allowedHits.filter((h) =>
      h.includes("/api/bidder/project-compliance/"),
    );
    // 切换前不得已有 B 详情
    expect(
      hitsBeforeSwitch.some((h) => h.includes(PROJECT_B.id)),
    ).toBeFalsy();

    // 选择 B：旧 A 统计不得残留；最终 empty 为 B
    await page.getByTestId("bpc-project-select").selectOption(PROJECT_B.id);

    // 切换瞬间或加载中：不得继续展示 A 的 12/9/2
    await expect
      .poll(async () => {
        const stats = page.getByTestId("bpc-detail-stats");
        if ((await stats.count()) === 0) return "cleared_or_loading";
        const total = await page.getByTestId("bpc-total-items").innerText();
        if (total === "12") return "stale_a";
        return total;
      })
      .not.toBe("stale_a");

    await expect(page.getByTestId("bpc-detail-empty")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bpc-total-items")).toHaveText("0");
    await expect(page.getByTestId("bpc-coverage")).toHaveText(
      "暂无可计算覆盖率",
    );

    expect(
      state.allowedHits.some(
        (h) =>
          h ===
          `GET /api/bidder/project-compliance/${PROJECT_B.id}`,
      ),
    ).toBeTruthy();
    expect(state.p10eHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  for (const role of ["bid_writer", "finance", "hr"] as AuthRole[]) {
    test(`${role} 无项目合规入口且直达受限且零 P10G API`, async ({ page }) => {
      const state = bidderState({
        session: meFor(role, {
          username: `user_${role}`,
          isOwner: role === "bid_writer",
          csrf: null,
        }),
      });
      await installP10GRoutes(page, state);

      await page.goto(role === "bid_writer" ? "/create" : "/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("项目合规");
      await expect(nav).not.toContainText("投标人");

      await page.goto("/bidder/project-compliance");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText("当前账号无权访问该功能")).toBeVisible();
      await expect(
        page.getByTestId("bidder-project-compliance-page"),
      ).toHaveCount(0);

      const p10gHits = state.allowedHits.filter((h) =>
        h.includes("/api/bidder/project-compliance"),
      );
      expect(p10gHits).toEqual([]);
      expect(state.p10eHits).toEqual([]);
      await assertNoSensitiveStorage(page);
    });
  }

  test("所有者 bid_writer 无项目合规入口", async ({ page }) => {
    const state = bidderState({
      session: meFor("bid_writer", {
        username: "owner_local",
        isOwner: true,
        csrf: null,
      }),
    });
    await installP10GRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("项目合规");
    await page.goto("/bidder/project-compliance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    const p10gHits = state.allowedHits.filter((h) =>
      h.includes("/api/bidder/project-compliance"),
    );
    expect(p10gHits).toEqual([]);
  });

  test("disabled 模式 /bidder/project-compliance 受限且无入口", async ({
    page,
  }) => {
    const state = bidderState({
      authRequired: false,
      session: null,
    });
    await installP10GRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("项目合规");
    await page.goto("/bidder/project-compliance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByTestId("bidder-project-compliance-page"),
    ).toHaveCount(0);
    const p10gHits = state.allowedHits.filter((h) =>
      h.includes("/api/bidder/project-compliance"),
    );
    expect(p10gHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("URL 查询参数不得用于携带项目选择（页面不读 search）", async ({
    page,
  }) => {
    const state = bidderState();
    await installP10GRoutes(page, state);
    await page.goto(
      `/bidder/project-compliance?projectId=${encodeURIComponent(PROJECT_A.id)}`,
    );
    await expect(page.getByTestId("bidder-project-compliance-page")).toBeVisible(
      {
        timeout: 15_000,
      },
    );
    // 不得因 query 预取详情
    await expect(page.getByTestId("bpc-detail-idle")).toBeVisible();
    await expect(page.getByTestId("bpc-detail-stats")).toHaveCount(0);
    const detailHits = state.allowedHits.filter(
      (h) =>
        h.startsWith("GET /api/bidder/project-compliance/") &&
        !h.endsWith("/projects"),
    );
    expect(detailHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });
});
