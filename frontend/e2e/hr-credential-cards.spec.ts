/**
 * 模块：P10D 人员资质素材卡前端 E2E
 * 用途：验收 hr 入口、列表摘要/详情、创建编辑启停、CSRF、网络白名单、角色门禁与无敏感存储。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:hr-credential-cards。
 * 二次开发：仅桩 /api/auth/*、/api/health、/api/hr/credential-cards*；禁止快照整页泄露。
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

type CardCategory = "professional" | "safety" | "performance" | "other";

type CardRecord = {
  id: string;
  personName: string;
  category: CardCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
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
    category: card.category,
    credentialName: card.credentialName,
    level: card.level,
    validUntil: card.validUntil,
    isActive: card.isActive,
    createdAt: card.createdAt,
    updatedAt: card.updatedAt,
  };
}

function toDetail(card: CardRecord) {
  return {
    ...toSummary(card),
    remark: card.remark,
  };
}

type HrAuthState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  currentCsrf?: string | null;
  forbiddenHits: string[];
  hrHits: string[];
  writeBodies: Array<{
    method: string;
    path: string;
    body: unknown;
    csrf: string | null;
  }>;
  cards: CardRecord[];
  seq: number;
  forceWriteError?: boolean;
};

/**
 * 用途：安装 auth + HR 专用接口桩；阻断通用业务 API 与外网。
 */
async function installHrRoutes(page: Page, state: HrAuthState) {
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
      if (path.startsWith("/api/hr/")) {
        state.hrHits.push(`${method} ${path}`);
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
      const token = state.resumeCsrf ?? "e2e-hr-csrf";
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

    const isHr = path === "/api/hr/credential-cards" ||
      path.startsWith("/api/hr/credential-cards/");
    if (isHr) {
      state.hrHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "hr") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }

      if (path === "/api/hr/credential-cards" && method === "GET") {
        await json(route, {
          items: state.cards.map(toSummary),
        });
        return;
      }

      if (path === "/api/hr/credential-cards" && method === "POST") {
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
                code: "invalid_hr_credential",
                message: "人员资质卡参数不合法",
                // 故意塞敏感字段，前端不得展示
                leak: "idCard=110101199001011234 phone=13800138000",
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
          id: `hcc_e2e_${state.seq}`,
          personName: String(b.personName ?? ""),
          category: (b.category as CardCategory) ?? "other",
          credentialName: String(b.credentialName ?? ""),
          level: String(b.level ?? ""),
          validUntil:
            b.validUntil == null || b.validUntil === ""
              ? null
              : String(b.validUntil),
          remark: String(b.remark ?? ""),
          isActive: b.isActive === undefined ? true : Boolean(b.isActive),
          createdAt: now,
          updatedAt: now,
        };
        state.cards = [card, ...state.cards];
        await json(route, toDetail(card), 201);
        return;
      }

      const oneMatch = path.match(/^\/api\/hr\/credential-cards\/([^/]+)$/);
      if (oneMatch) {
        const cardId = decodeURIComponent(oneMatch[1]);
        const found = state.cards.find((c) => c.id === cardId);
        if (method === "GET") {
          if (!found) {
            await json(
              route,
              {
                detail: {
                  code: "hr_credential_not_found",
                  message: "人员资质卡不存在",
                },
              },
              404,
            );
            return;
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
                  code: "invalid_hr_credential",
                  message: "人员资质卡参数不合法",
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
                  code: "hr_credential_not_found",
                  message: "人员资质卡不存在",
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
          if (b.category !== undefined) {
            found.category = b.category as CardCategory;
          }
          if (b.credentialName !== undefined) {
            found.credentialName = String(b.credentialName);
          }
          if (b.level !== undefined) {
            found.level = String(b.level);
          }
          if ("validUntil" in b) {
            found.validUntil =
              b.validUntil == null || b.validUntil === ""
                ? null
                : String(b.validUntil);
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
      // 不得把资质卡正文/备注落入浏览器存储
      expect.soft(value.includes("一级建造师")).toBeFalsy();
      expect.soft(value.includes("内部备注")).toBeFalsy();
    }
  }
}

function seedCard(): CardRecord {
  return {
    id: "hcc_seed_1",
    personName: "张三",
    category: "professional",
    credentialName: "一级建造师",
    level: "一级",
    validUntil: "2027-12-31",
    remark: "内部备注-仅详情可见",
    isActive: true,
    createdAt: "2026-07-13T08:00:00+00:00",
    updatedAt: "2026-07-13T09:00:00+00:00",
  };
}

test.describe("P10D 人员资质素材卡前端", () => {
  test("hr 入口、列表摘要无 remark、详情含 remark、创建编辑启停与 CSRF", async ({
    page,
  }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", {
        username: "user_hr",
        isOwner: false,
        csrf: null,
      }),
      resumeCsrf: "e2e-hr-resume-csrf",
      currentCsrf: null,
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [seedCard()],
      seq: 0,
    };
    await installHrRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("人力");
    await expect(nav).toContainText("人员资质");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("标书生成");

    await nav.getByText("人员资质").click();
    await expect(page.getByTestId("hr-credential-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "人员资质" })).toBeVisible();
    await expect(page.getByTestId("hr-list")).toBeVisible();
    await expect(page.getByTestId("hr-list-item")).toHaveCount(1);
    await expect(page.getByText("张三")).toBeVisible();
    await expect(page.getByTestId("hr-list")).toContainText("专业资质");
    await expect(page.getByTestId("hr-list")).toContainText("一级建造师");
    // 列表摘要不得含 remark
    await expect(page.getByTestId("hr-list")).not.toContainText(
      "内部备注-仅详情可见",
    );
    // 不直接展示英文枚举
    await expect(page.getByTestId("hr-list")).not.toContainText("professional");

    // 首次加载列表且点击条目之前：只允许列表 GET，绝不预取详情
    const beforeSelectHits = [...state.hrHits];
    expect(beforeSelectHits).toContain("GET /api/hr/credential-cards");
    expect(
      beforeSelectHits.some((h) =>
        /^GET \/api\/hr\/credential-cards\/.+/.test(h),
      ),
    ).toBeFalsy();

    // 选中后才加载详情
    await page.getByTestId("hr-list-item").first().click();
    await expect(page.getByTestId("hr-detail")).toBeVisible();
    await expect(page.getByTestId("hr-detail-remark")).toContainText(
      "内部备注-仅详情可见",
    );
    await expect(page.getByTestId("hr-detail-category")).toHaveText("专业资质");
    expect(
      state.hrHits.some(
        (h) => h === "GET /api/hr/credential-cards/hcc_seed_1",
      ),
    ).toBeTruthy();

    // 新建
    await page.getByTestId("hr-show-create").click();
    await expect(page.getByTestId("hr-create-form")).toBeVisible();
    await page.getByTestId("hr-create-person").fill("李四");
    await page.getByTestId("hr-create-category").selectOption("safety");
    await page.getByTestId("hr-create-credential").fill("安全员证");
    await page.getByTestId("hr-create-level").fill("B 证");
    await page.getByTestId("hr-create-remark").fill("新建备注");
    await page.getByTestId("hr-create-submit").click();

    await expect(page.getByTestId("hr-list-item")).toHaveCount(2);
    await expect(page.getByTestId("hr-list")).toContainText("李四");
    await expect(page.getByTestId("hr-detail-person")).toHaveText("李四");
    await expect(page.getByTestId("hr-list")).toContainText("安全资质");
    // 列表仍无新建备注
    await expect(page.getByTestId("hr-list")).not.toContainText("新建备注");

    const post = state.writeBodies.find((w) => w.method === "POST");
    expect(post).toBeTruthy();
    expect(post?.csrf).toBe("e2e-hr-resume-csrf");
    expect(post?.body).toMatchObject({
      personName: "李四",
      category: "safety",
      credentialName: "安全员证",
      level: "B 证",
      remark: "新建备注",
      isActive: true,
    });
    // 创建后应重读列表 + 详情
    const postIdx = state.hrHits.lastIndexOf("POST /api/hr/credential-cards");
    const afterPost = state.hrHits.slice(postIdx + 1);
    expect(afterPost.some((h) => h === "GET /api/hr/credential-cards")).toBeTruthy();
    expect(
      afterPost.some((h) => h.startsWith("GET /api/hr/credential-cards/hcc_e2e_")),
    ).toBeTruthy();

    // 编辑 seed
    await page.locator('[data-card-id="hcc_seed_1"]').click();
    await expect(page.getByTestId("hr-detail-person")).toHaveText("张三");
    await page.getByTestId("hr-edit-btn").click();
    await expect(page.getByTestId("hr-edit-form")).toBeVisible();
    await page.getByTestId("hr-edit-person").fill("张三丰");
    await page.getByTestId("hr-edit-credential").fill("一级建造师（市政）");
    await page.getByTestId("hr-edit-submit").click();
    await expect(page.getByTestId("hr-detail-person")).toHaveText("张三丰");
    await expect(page.getByTestId("hr-detail-credential")).toHaveText(
      "一级建造师（市政）",
    );

    const patch = state.writeBodies.find(
      (w) =>
        w.method === "PATCH" &&
        w.path === "/api/hr/credential-cards/hcc_seed_1",
    );
    expect(patch?.csrf).toBe("e2e-hr-resume-csrf");
    expect(patch?.body).toMatchObject({
      personName: "张三丰",
      credentialName: "一级建造师（市政）",
    });
    // 编辑成功后强制重读列表与当前详情（无乐观更新）
    const editPatchIdx = state.hrHits.lastIndexOf(
      "PATCH /api/hr/credential-cards/hcc_seed_1",
    );
    const afterEdit = state.hrHits.slice(editPatchIdx + 1);
    expect(afterEdit).toContain("GET /api/hr/credential-cards");
    expect(afterEdit).toContain("GET /api/hr/credential-cards/hcc_seed_1");

    // 启停
    await page.getByTestId("hr-toggle-active").click();
    await expect(page.getByTestId("hr-detail-status")).toHaveText("停用");
    const toggle = state.writeBodies.filter(
      (w) =>
        w.method === "PATCH" &&
        w.path === "/api/hr/credential-cards/hcc_seed_1",
    );
    const lastToggle = toggle[toggle.length - 1];
    expect(lastToggle?.body).toMatchObject({ isActive: false });
    expect(lastToggle?.csrf).toBe("e2e-hr-resume-csrf");
    // 启停成功后强制重读列表与当前详情
    const togglePatchIdx = state.hrHits.lastIndexOf(
      "PATCH /api/hr/credential-cards/hcc_seed_1",
    );
    const afterToggle = state.hrHits.slice(togglePatchIdx + 1);
    expect(afterToggle).toContain("GET /api/hr/credential-cards");
    expect(afterToggle).toContain("GET /api/hr/credential-cards/hcc_seed_1");

    expect(state.forbiddenHits).toEqual([]);
    await assertNoSensitiveStorage(page);
  });

  test("写入失败固定中文脱敏，不回显后端 detail", async ({ page }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hr-resume-csrf",
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
      forceWriteError: true,
    };
    await installHrRoutes(page, state);

    await page.goto("/hr");
    await expect(page.getByTestId("hr-credential-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("hr-show-create").click();
    await page.getByTestId("hr-create-person").fill("王五");
    await page.getByTestId("hr-create-credential").fill("焊工证");
    await page.getByTestId("hr-create-submit").click();

    const err = page.getByTestId("hr-write-error");
    await expect(err).toBeVisible();
    await expect(err).toContainText("提交内容不符合要求");
    await expect(err).not.toContainText("invalid_hr_credential");
    await expect(err).not.toContainText("idCard");
    await expect(err).not.toContainText("13800138000");
    await expect(err).not.toContainText("leak");
  });

  test("非法表单不发网络写请求", async ({ page }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hr-resume-csrf",
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHrRoutes(page, state);

    await page.goto("/hr");
    await expect(page.getByTestId("hr-credential-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("hr-show-create").click();
    // 空姓名
    await page.getByTestId("hr-create-submit").click();
    await expect(page.getByTestId("hr-write-error")).toContainText(
      "请输入人员姓名",
    );
    expect(state.writeBodies).toHaveLength(0);
    expect(
      state.hrHits.filter((h) => h.startsWith("POST ")).length,
    ).toBe(0);
  });

  for (const role of ["bid_writer", "finance", "bidder"] as AuthRole[]) {
    test(`${role} 无人力入口且直达 /hr 受限、无 HR API`, async ({ page }) => {
      const state: HrAuthState = {
        bootstrapped: true,
        authRequired: true,
        session: meFor(role, {
          isOwner: role === "bid_writer",
          csrf: null,
        }),
        resumeCsrf: "e2e-other-csrf",
        forbiddenHits: [],
        hrHits: [],
        writeBodies: [],
        cards: [seedCard()],
        seq: 0,
      };
      await installHrRoutes(page, state);

      await page.goto("/restricted");
      await expect(page.getByTestId("app-shell")).toBeVisible({
        timeout: 15_000,
      });
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("人员资质");
      await expect(nav).not.toContainText("人力");

      await page.goto("/hr");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("hr-credential-page")).toHaveCount(0);
      expect(state.hrHits.filter((h) => h.startsWith("GET /api/hr"))).toEqual(
        [],
      );
    });
  }

  test("owner（bid_writer 所有者）无人力入口", async ({ page }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { isOwner: true, csrf: null }),
      resumeCsrf: "e2e-owner-csrf",
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHrRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("人员资质");
    await page.goto("/hr");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hrHits).toEqual([]);
  });

  test("hr 无财务入口", async ({ page }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { csrf: null }),
      resumeCsrf: "e2e-hr-csrf",
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHrRoutes(page, state);
    await page.goto("/hr");
    await expect(page.getByTestId("hr-credential-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("财务报价");
    await page.goto("/finance");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
  });

  test("disabled 模式 /hr 受限且无入口", async ({ page }) => {
    const state: HrAuthState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      hrHits: [],
      writeBodies: [],
      cards: [],
      seq: 0,
    };
    await installHrRoutes(page, state);
    await page.goto("/create");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("navigation", { name: "主导航" }),
    ).not.toContainText("人员资质");
    await page.goto("/hr");
    await expect(page.getByTestId("auth-restricted")).toBeVisible();
    expect(state.hrHits).toEqual([]);
  });
});
