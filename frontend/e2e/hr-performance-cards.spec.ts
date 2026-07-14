/**
 * 模块：P10H 人员业绩素材卡前端 E2E
 * 用途：验收 hr 入口、列表摘要/详情、创建编辑启停、年份预检、网络白名单、角色门禁与无敏感存储。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:hr-performance-cards。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/hr/performance-cards*；禁止快照整页泄露。
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

type CardRecord = {
  id: string;
  personName: string;
  projectName: string;
  projectRole: string;
  completedYear: number | null;
  performanceSummary: string;
  remark: string;
  isActive: boolean;
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

function toSummary(card: CardRecord) {
  return {
    id: card.id,
    personName: card.personName,
    projectName: card.projectName,
    projectRole: card.projectRole,
    completedYear: card.completedYear,
    isActive: card.isActive,
    createdAt: card.createdAt,
    updatedAt: card.updatedAt,
  };
}

function toDetail(card: CardRecord) {
  return {
    ...toSummary(card),
    performanceSummary: card.performanceSummary,
    remark: card.remark,
  };
}

type HpAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  currentCsrf?: string | null;
  forbiddenHits: string[];
  hpHits: string[];
  writeBodies: Array<{
    method: string;
    path: string;
    body: unknown;
    csrf: string | null;
  }>;
  cards: CardRecord[];
  seq: number;
  forceWriteError?: boolean;
  /** cardId -> 延迟毫秒；用于模拟迟到详情响应 */
  detailDelayMs?: Record<string, number>;
};

/**
 * 用途：安装 auth + P10H 专用接口桩；阻断通用业务/P10D/P10F/财务/投标人与外网。
 */
async function installHpRoutes(page: Page, state: HpAuthState) {
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
      path.startsWith("/api/hr/team-recommendations")
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
      if (path.startsWith("/api/hr/performance-cards")) {
        state.hpHits.push(`${method} ${path}`);
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
      const token = state.resumeCsrf ?? "e2e-hp-csrf";
      state.currentCsrf = token;
      await json(route, { csrfToken: token });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
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

    const isHp =
      path === "/api/hr/performance-cards" ||
      path.startsWith("/api/hr/performance-cards/");
    if (isHp) {
      state.hpHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "hr") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }

      if (path === "/api/hr/performance-cards" && method === "GET") {
        await json(route, {
          items: state.cards.map(toSummary),
        });
        return;
      }

      if (path === "/api/hr/performance-cards" && method === "POST") {
        const csrfHeader = req.headers()["x-csrf-token"] ?? null;
        let body: unknown = null;
        try {
          body = req.postDataJSON();
        } catch {
          body = req.postData();
        }
        state.writeBodies.push({
          method,
          path,
          body,
          csrf: csrfHeader,
        });
        if (state.forceWriteError) {
          await json(
            route,
            {
              detail: {
                code: "invalid_hr_performance",
                message: "人员业绩卡参数不合法",
                leak: "SECRET-LEAK-idCard=110101199001011234 phone=13800138000",
              },
            },
            422,
          );
          return;
        }
        if (!csrfHeader || csrfHeader !== state.currentCsrf) {
          await json(
            route,
            { detail: { code: "csrf_failed", message: "CSRF 校验失败" } },
            403,
          );
          return;
        }
        const b = (body ?? {}) as Record<string, unknown>;
        state.seq += 1;
        const now = "2026-07-14T10:00:00+00:00";
        const card: CardRecord = {
          id: `hpc_e2e_${state.seq}`,
          personName: String(b.personName ?? ""),
          projectName: String(b.projectName ?? ""),
          projectRole: String(b.projectRole ?? ""),
          completedYear:
            b.completedYear == null || b.completedYear === ""
              ? null
              : Number(b.completedYear),
          performanceSummary: String(b.performanceSummary ?? ""),
          remark: String(b.remark ?? ""),
          isActive: b.isActive === undefined ? true : Boolean(b.isActive),
          createdAt: now,
          updatedAt: now,
        };
        state.cards = [card, ...state.cards];
        await json(route, toDetail(card), 201);
        return;
      }

      const oneMatch = path.match(/^\/api\/hr\/performance-cards\/([^/]+)$/);
      if (oneMatch) {
        const cardId = decodeURIComponent(oneMatch[1]);
        const found = state.cards.find((c) => c.id === cardId);
        if (method === "GET") {
          if (!found) {
            await json(
              route,
              {
                detail: {
                  code: "hr_performance_not_found",
                  message: "人员业绩卡不存在",
                  pathEcho: cardId,
                },
              },
              404,
            );
            return;
          }
          const delay = state.detailDelayMs?.[cardId] ?? 0;
          if (delay > 0) {
            await new Promise((r) => setTimeout(r, delay));
          }
          await json(route, toDetail(found));
          return;
        }
        if (method === "PATCH") {
          const csrfHeader = req.headers()["x-csrf-token"] ?? null;
          let body: unknown = null;
          try {
            body = req.postDataJSON();
          } catch {
            body = req.postData();
          }
          state.writeBodies.push({
            method,
            path,
            body,
            csrf: csrfHeader,
          });
          if (state.forceWriteError) {
            await json(
              route,
              {
                detail: {
                  code: "invalid_hr_performance",
                  message: "人员业绩卡参数不合法",
                  leak: "secret-detail-must-not-show",
                },
              },
              422,
            );
            return;
          }
          if (!csrfHeader || csrfHeader !== state.currentCsrf) {
            await json(
              route,
              { detail: { code: "csrf_failed", message: "CSRF 校验失败" } },
              403,
            );
            return;
          }
          if (!found) {
            await json(
              route,
              {
                detail: {
                  code: "hr_performance_not_found",
                  message: "人员业绩卡不存在",
                },
              },
              404,
            );
            return;
          }
          const b = (body ?? {}) as Record<string, unknown>;
          if (b.personName !== undefined) {
            found.personName = String(b.personName);
          }
          if (b.projectName !== undefined) {
            found.projectName = String(b.projectName);
          }
          if (b.projectRole !== undefined) {
            found.projectRole = String(b.projectRole);
          }
          if ("completedYear" in b) {
            found.completedYear =
              b.completedYear == null || b.completedYear === ""
                ? null
                : Number(b.completedYear);
          }
          if (b.performanceSummary !== undefined) {
            found.performanceSummary = String(b.performanceSummary);
          }
          if (b.remark !== undefined) {
            found.remark = String(b.remark);
          }
          if (b.isActive !== undefined) {
            found.isActive = Boolean(b.isActive);
          }
          found.updatedAt = "2026-07-14T11:00:00+00:00";
          await json(route, toDetail(found));
          return;
        }
      }
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
      expect.soft(value.includes("负责施工组织设计")).toBeFalsy();
      expect.soft(value.includes("内部备注-仅详情可见")).toBeFalsy();
      expect.soft(value.includes("SECRET-LEAK")).toBeFalsy();
    }
  }
}

function seedCardA(): CardRecord {
  return {
    id: "hpc_seed_a",
    personName: "赵六",
    projectName: "市政道路改造工程",
    projectRole: "项目经理",
    completedYear: 2024,
    performanceSummary: "负责施工组织设计与进度管控",
    remark: "内部备注-仅详情可见",
    isActive: true,
    createdAt: "2026-07-13T08:00:00+00:00",
    updatedAt: "2026-07-13T09:00:00+00:00",
  };
}

function seedCardB(): CardRecord {
  return {
    id: "hpc_seed_b",
    personName: "钱七",
    projectName: "智慧园区运维项目",
    projectRole: "技术负责人",
    completedYear: 2023,
    performanceSummary: "主导运维体系建设",
    remark: "B卡备注不得被A覆盖",
    isActive: true,
    createdAt: "2026-07-13T08:30:00+00:00",
    updatedAt: "2026-07-13T09:30:00+00:00",
  };
}

test.describe("P10H 人员业绩素材卡前端", () => {
  test("hr 入口、初始仅摘要、按需详情、创建编辑启停与 CSRF、导航激活态", async ({
    page,
  }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", {
        username: "user_hr",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-hp-resume-csrf",
      currentCsrf: null,
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [seedCardA()],
      seq: 0,
    };
    await installHpRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("人力");
    await expect(nav).toContainText("人员资质");
    await expect(nav).toContainText("团队推荐");
    await expect(nav).toContainText("人员业绩");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("标书生成");

    await nav.getByText("人员业绩").click();
    await expect(page.getByTestId("hr-performance-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "人员业绩" })).toBeVisible();
    // 导航激活态：人员业绩 active，人员资质不 active
    await expect(nav.getByText("人员业绩")).toBeVisible();
    const perfLink = nav.locator('a[href="/hr/performance-cards"]');
    await expect(perfLink).toHaveClass(/is-active/);
    const credLink = nav.locator('a[href="/hr"]');
    await expect(credLink).not.toHaveClass(/is-active/);

    await expect(page.getByTestId("hp-list")).toBeVisible();
    await expect(page.getByTestId("hp-list-item")).toHaveCount(1);
    await expect(page.getByText("赵六")).toBeVisible();
    await expect(page.getByTestId("hp-list")).toContainText("市政道路改造工程");
    // 列表摘要不得含 performanceSummary / remark
    await expect(page.getByTestId("hp-list")).not.toContainText(
      "负责施工组织设计与进度管控",
    );
    await expect(page.getByTestId("hp-list")).not.toContainText(
      "内部备注-仅详情可见",
    );

    // 首次加载：只允许列表 GET，绝不预取详情
    const beforeSelectHits = [...state.hpHits];
    expect(beforeSelectHits).toContain("GET /api/hr/performance-cards");
    expect(
      beforeSelectHits.some((h) =>
        /^GET \/api\/hr\/performance-cards\/.+/.test(h),
      ),
    ).toBeFalsy();

    // 选中后才加载详情
    await page.getByTestId("hp-list-item").first().click();
    await expect(page.getByTestId("hp-detail")).toBeVisible();
    await expect(page.getByTestId("hp-detail-summary")).toContainText(
      "负责施工组织设计与进度管控",
    );
    await expect(page.getByTestId("hp-detail-remark")).toContainText(
      "内部备注-仅详情可见",
    );
    await expect(page.getByTestId("hp-detail-year")).toHaveText("2024");
    expect(
      state.hpHits.some((h) => h === "GET /api/hr/performance-cards/hpc_seed_a"),
    ).toBeTruthy();

    // 新建
    await page.getByTestId("hp-show-create").click();
    await expect(page.getByTestId("hp-create-form")).toBeVisible();
    await page.getByTestId("hp-create-person").fill("孙八");
    await page.getByTestId("hp-create-project").fill("数据中心机房工程");
    await page.getByTestId("hp-create-role").fill("施工员");
    await page.getByTestId("hp-create-year").fill("2025");
    await page.getByTestId("hp-create-summary").fill("参与机房综合布线");
    await page.getByTestId("hp-create-remark").fill("新建备注");
    await page.getByTestId("hp-create-submit").click();

    await expect(page.getByTestId("hp-list-item")).toHaveCount(2);
    await expect(page.getByTestId("hp-list")).toContainText("孙八");
    await expect(page.getByTestId("hp-detail-person")).toHaveText("孙八");
    await expect(page.getByTestId("hp-list")).not.toContainText("新建备注");
    await expect(page.getByTestId("hp-list")).not.toContainText(
      "参与机房综合布线",
    );

    const post = state.writeBodies.find((w) => w.method === "POST");
    expect(post).toBeTruthy();
    expect(post?.csrf).toBe("e2e-hp-resume-csrf");
    expect(post?.body).toMatchObject({
      personName: "孙八",
      projectName: "数据中心机房工程",
      projectRole: "施工员",
      completedYear: 2025,
      performanceSummary: "参与机房综合布线",
      remark: "新建备注",
      isActive: true,
    });
    const postIdx = state.hpHits.lastIndexOf("POST /api/hr/performance-cards");
    const afterPost = state.hpHits.slice(postIdx + 1);
    expect(afterPost.some((h) => h === "GET /api/hr/performance-cards")).toBeTruthy();
    expect(
      afterPost.some((h) => h.startsWith("GET /api/hr/performance-cards/hpc_e2e_")),
    ).toBeTruthy();

    // 编辑 seed A
    await page.locator('[data-card-id="hpc_seed_a"]').click();
    await expect(page.getByTestId("hp-detail-person")).toHaveText("赵六");
    await page.getByTestId("hp-edit-btn").click();
    await expect(page.getByTestId("hp-edit-form")).toBeVisible();
    await page.getByTestId("hp-edit-person").fill("赵六改");
    await page.getByTestId("hp-edit-project").fill("市政道路改造工程（二期）");
    await page.getByTestId("hp-edit-year").fill("2022");
    await page.getByTestId("hp-edit-submit").click();
    await expect(page.getByTestId("hp-detail-person")).toHaveText("赵六改");
    await expect(page.getByTestId("hp-detail-project")).toHaveText(
      "市政道路改造工程（二期）",
    );
    await expect(page.getByTestId("hp-detail-year")).toHaveText("2022");

    const patch = state.writeBodies.find(
      (w) =>
        w.method === "PATCH" &&
        w.path === "/api/hr/performance-cards/hpc_seed_a",
    );
    expect(patch?.csrf).toBe("e2e-hp-resume-csrf");
    expect(patch?.body).toMatchObject({
      personName: "赵六改",
      projectName: "市政道路改造工程（二期）",
      completedYear: 2022,
    });
    const editPatchIdx = state.hpHits.lastIndexOf(
      "PATCH /api/hr/performance-cards/hpc_seed_a",
    );
    const afterEdit = state.hpHits.slice(editPatchIdx + 1);
    expect(afterEdit).toContain("GET /api/hr/performance-cards");
    expect(afterEdit).toContain("GET /api/hr/performance-cards/hpc_seed_a");

    // 启停
    await page.getByTestId("hp-toggle-active").click();
    await expect(page.getByTestId("hp-detail-status")).toHaveText("停用");
    const toggle = state.writeBodies.filter(
      (w) =>
        w.method === "PATCH" &&
        w.path === "/api/hr/performance-cards/hpc_seed_a",
    );
    const lastToggle = toggle[toggle.length - 1];
    expect(lastToggle?.body).toMatchObject({ isActive: false });
    expect(lastToggle?.csrf).toBe("e2e-hp-resume-csrf");
    const togglePatchIdx = state.hpHits.lastIndexOf(
      "PATCH /api/hr/performance-cards/hpc_seed_a",
    );
    const afterToggle = state.hpHits.slice(togglePatchIdx + 1);
    expect(afterToggle).toContain("GET /api/hr/performance-cards");
    expect(afterToggle).toContain("GET /api/hr/performance-cards/hpc_seed_a");

    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("非法年份/空必填不发网络写请求", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hp-resume-csrf",
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHpRoutes(page, state);

    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("hr-performance-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("hp-show-create").click();
    // 空姓名
    await page.getByTestId("hp-create-submit").click();
    await expect(page.getByTestId("hp-write-error")).toContainText(
      "请输入人员姓名",
    );
    expect(state.writeBodies).toHaveLength(0);

    await page.getByTestId("hp-create-person").fill("测试员");
    await page.getByTestId("hp-create-project").fill("测试项目");
    await page.getByTestId("hp-create-summary").fill("摘要");
    await page.getByTestId("hp-create-year").fill("1899");
    await page.getByTestId("hp-create-submit").click();
    await expect(page.getByTestId("hp-write-error")).toContainText(
      "完成年份须为 1900–2100 的整数",
    );
    expect(state.writeBodies).toHaveLength(0);
    expect(state.hpHits.filter((h) => h.startsWith("POST ")).length).toBe(0);

    await page.getByTestId("hp-create-year").fill("2020.5");
    await page.getByTestId("hp-create-submit").click();
    await expect(page.getByTestId("hp-write-error")).toContainText(
      "完成年份须为 1900–2100 的整数",
    );
    expect(state.writeBodies).toHaveLength(0);
  });

  test("写入失败固定中文脱敏，不回显后端 detail", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hp-resume-csrf",
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
      forceWriteError: true,
    };
    await installHpRoutes(page, state);

    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("hr-performance-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("hp-show-create").click();
    await page.getByTestId("hp-create-person").fill("王五");
    await page.getByTestId("hp-create-project").fill("脱敏测试项目");
    await page.getByTestId("hp-create-summary").fill("脱敏摘要");
    await page.getByTestId("hp-create-submit").click();

    const err = page.getByTestId("hp-write-error");
    await expect(err).toBeVisible();
    await expect(err).toContainText("提交内容不符合要求");
    await expect(err).not.toContainText("invalid_hr_performance");
    await expect(err).not.toContainText("SECRET-LEAK");
    await expect(err).not.toContainText("idCard");
    await expect(err).not.toContainText("13800138000");
    await expect(err).not.toContainText("脱敏测试项目");
  });

  test("SPA 快速 A→B 丢弃迟到详情响应", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hp-resume-csrf",
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [seedCardA(), seedCardB()],
      seq: 0,
      detailDelayMs: { hpc_seed_a: 800 },
    };
    await installHpRoutes(page, state);

    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("hr-performance-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("hp-list-item")).toHaveCount(2);

    // 先点 A（慢），立刻点 B（快）
    await page.locator('[data-card-id="hpc_seed_a"]').click();
    await page.locator('[data-card-id="hpc_seed_b"]').click();

    await expect(page.getByTestId("hp-detail")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("hp-detail-person")).toHaveText("钱七");
    await expect(page.getByTestId("hp-detail-summary")).toContainText(
      "主导运维体系建设",
    );
    // 等待 A 的迟到响应窗口过去，仍不得被 A 覆盖
    await page.waitForTimeout(1000);
    await expect(page.getByTestId("hp-detail-person")).toHaveText("钱七");
    await expect(page.getByTestId("hp-detail-summary")).not.toContainText(
      "负责施工组织设计",
    );
    await expect(page.getByTestId("hp-detail-remark")).toContainText(
      "B卡备注不得被A覆盖",
    );

    expect(
      state.hpHits.filter(
        (h) => h === "GET /api/hr/performance-cards/hpc_seed_a",
      ).length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      state.hpHits.filter(
        (h) => h === "GET /api/hr/performance-cards/hpc_seed_b",
      ).length,
    ).toBeGreaterThanOrEqual(1);
  });

  for (const role of ["bid_writer", "finance", "bidder"] as AuthRole[]) {
    test(`${role} 无人员业绩入口且直达受限、零 P10H API`, async ({ page }) => {
      const state: HpAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        resumeCsrf: "e2e-other-csrf",
        forbiddenHits: [],
        hpHits: [],
        writeBodies: [],
        cards: [seedCardA()],
        seq: 0,
      };
      await installHpRoutes(page, state);

      await page.goto("/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("人员业绩");
      await expect(nav).not.toContainText("人力");

      await page.goto("/hr/performance-cards");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("hr-performance-page")).toHaveCount(0);
      expect(
        state.hpHits.filter((h) => h.startsWith("GET /api/hr/performance")),
      ).toEqual([]);
    });
  }

  test("owner（bid_writer 所有者）无人员业绩入口", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { isOwner: true, csrf: null }),
      resumeCsrf: "e2e-owner-csrf",
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHpRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("人员业绩");
    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hpHits).toEqual([]);
  });

  test("disabled 模式 /hr/performance-cards 受限且无入口", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHpRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("人员业绩");
    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hpHits).toEqual([]);
  });

  test("hr 无财务入口，人员资质激活态互不冲突", async ({ page }) => {
    const state: HpAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hp-csrf",
      forbiddenHits: [],
      hpHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHpRoutes(page, state);
    await page.goto("/hr/performance-cards");
    await expect(page.getByTestId("hr-performance-page")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).not.toContainText("财务报价");
    await page.goto("/finance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();

    // 人员资质页：精确激活，业绩不激活
    await page.goto("/hr");
    await expect(nav.locator('a[href="/hr"]')).toHaveClass(/is-active/);
    await expect(
      nav.locator('a[href="/hr/performance-cards"]'),
    ).not.toHaveClass(/is-active/);
  });
});
