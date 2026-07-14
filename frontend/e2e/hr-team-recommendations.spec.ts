/**
 * 模块：P10F 人力团队推荐快照前端 E2E
 * 用途：验收 HR 初始网络边界、有序保存后重读、停用快照提示、角色门禁、
 *       bid_writer 按需投影 ready/empty/错误，以及网络白名单与浏览器存储零写入。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:hr-team-recommendations。
 * 二次开发：仅桩 auth/health/HR 团队推荐与资质摘要、bid_writer 投影；禁止整页快照泄露。
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

type CardSummary = {
  id: string;
  personName: string;
  category: CardCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
};

type TeamMember = {
  order: number;
  personName: string;
  category: CardCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
  sourceCardId: string;
};

type TeamDetail = {
  projectId: string;
  projectName: string;
  members: TeamMember[];
  updatedAt: string;
};

type TeamSummary = {
  projectId: string;
  projectName: string;
  memberCount: number;
  updatedAt: string;
};

type BwProjection = {
  dataState: "empty" | "ready";
  members: Array<{
    order: number;
    personName: string;
    category: CardCategory;
    credentialName: string;
    level: string;
    validUntil: string | null;
  }>;
  updatedAt: string | null;
};

const SENSITIVE_STORAGE_RE =
  /password|cookie|csrf|token|auth|session|api[_-]?key/i;

const FIXED_ERROR_SAVE_422 = "提交内容不符合要求，请检查后重试";
const FIXED_ERROR_BW = "暂时无法读取团队推荐";

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

type ProjectStub = {
  id: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: "technical";
  workspaceId: string;
};

type P10fState = {
  bootstrapped: boolean;
  session: MePayload | null;
  authRequired: boolean;
  resumeCsrf?: string;
  currentCsrf?: string | null;
  forbiddenHits: string[];
  allowedHits: string[];
  writeBodies: Array<{
    method: string;
    path: string;
    body: unknown;
    csrf: string | null;
  }>;
  projects: Array<{ id: string; name: string }>;
  cards: CardSummary[];
  /** projectId -> detail；无键表示尚未组装 */
  details: Record<string, TeamDetail | undefined>;
  forcePutError?: boolean;
  forceDetailError?: boolean;
  forceBwError?: boolean;
  /** projectId -> 投影；未设置时回退 bwProjection 或 empty */
  bwProjections?: Record<string, BwProjection>;
  bwProjection?: BwProjection;
  projectStub?: ProjectStub;
  /** 多项目桩，供 bid_writer 路由切换隔离用例 */
  projectStubs?: ProjectStub[];
};

function buildSummaries(state: P10fState): TeamSummary[] {
  return Object.values(state.details)
    .filter((d): d is TeamDetail => Boolean(d))
    .map((d) => ({
      projectId: d.projectId,
      projectName: d.projectName,
      memberCount: d.members.length,
      updatedAt: d.updatedAt,
    }));
}

/**
 * 用途：安装 auth + P10F 专用接口桩；阻断通用业务与外网。
 */
async function installP10fRoutes(page: Page, state: P10fState) {
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

    // 白名单外业务回退：记录并 403
    const isTeamApi =
      path === "/api/hr/team-recommendations" ||
      path.startsWith("/api/hr/team-recommendations/");
    const isCardList =
      path === "/api/hr/credential-cards" && method === "GET";
    const isBwProjection =
      /^\/api\/projects\/[^/]+\/team-recommendation$/.test(path) &&
      method === "GET";
    const allProjectStubs: ProjectStub[] = [
      ...(state.projectStubs ?? []),
      ...(state.projectStub ? [state.projectStub] : []),
    ];
    const projectGetMatch = path.match(/^\/api\/projects\/([^/]+)$/);
    const projectGetId =
      method === "GET" && projectGetMatch
        ? decodeURIComponent(projectGetMatch[1])
        : null;
    const matchedProjectStub =
      projectGetId != null
        ? allProjectStubs.find((p) => p.id === projectGetId)
        : undefined;
    const isProjectGet = Boolean(matchedProjectStub);
    const isEditorOrFiles =
      path.includes("editor-state") ||
      path.includes("/files") ||
      path.includes("/tasks") ||
      path.includes("parse-strategy");
    const hasAnyProjectStub = allProjectStubs.length > 0;

    if (
      path.startsWith("/api/settings") ||
      path.startsWith("/api/export") ||
      path.startsWith("/api/finance") ||
      path.startsWith("/api/bidder") ||
      (path.startsWith("/api/projects") &&
        !isBwProjection &&
        !isProjectGet) ||
      (path.startsWith("/api/hr/credential-cards/") && method === "GET") ||
      (path.startsWith("/api/hr/") && !isTeamApi && !isCardList)
    ) {
      // 详情预取资质卡必须阻断
      if (path.startsWith("/api/hr/credential-cards/")) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "role_forbidden", message: "禁止预取详情" } },
          403,
        );
        return;
      }
      if (!isEditorOrFiles || !hasAnyProjectStub) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "role_forbidden", message: "禁止业务回退" } },
          403,
        );
        return;
      }
    }

    // 技术标工作区附属接口：返回空，避免白屏
    if (hasAnyProjectStub && isEditorOrFiles) {
      state.allowedHits.push(`${method} ${path}`);
      if (path.includes("editor-state")) {
        const editorProjectId =
          matchedProjectStub?.id ??
          allProjectStubs[0]?.id ??
          "proj_unknown";
        await json(route, {
          projectId: editorProjectId,
          parsedMarkdown: "",
          bidAnalysis: null,
          outline: null,
          facts: null,
          chapters: [],
          responseMatrix: [],
          version: 1,
        });
        return;
      }
      if (path.includes("/files")) {
        await json(route, []);
        return;
      }
      if (path.includes("/tasks")) {
        await json(route, []);
        return;
      }
      if (path.includes("parse-strategy")) {
        await json(route, { parseStrategy: "light" });
        return;
      }
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
      if (path.startsWith("/api/hr/") || isBwProjection) {
        state.allowedHits.push(`${method} ${path}`);
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
      const token = state.resumeCsrf ?? "e2e-p10f-csrf";
      state.currentCsrf = token;
      await json(route, { csrfToken: token });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.allowedHits.push(`${method} ${path}`);
      state.session = null;
      state.currentCsrf = null;
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

    if (isProjectGet && matchedProjectStub) {
      state.allowedHits.push(`${method} ${path}`);
      await json(route, matchedProjectStub);
      return;
    }

    if (isBwProjection) {
      state.allowedHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "bid_writer") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (state.forceBwError) {
        await json(
          route,
          {
            detail: {
              code: "internal_error",
              message: "SECRET_BW_LEAK_/projects/team-recommendation",
            },
          },
          500,
        );
        return;
      }
      const bwMatch = path.match(
        /^\/api\/projects\/([^/]+)\/team-recommendation$/,
      );
      const bwProjectId = bwMatch
        ? decodeURIComponent(bwMatch[1])
        : "";
      const projection =
        (bwProjectId && state.bwProjections?.[bwProjectId]) ||
        state.bwProjection || {
          dataState: "empty" as const,
          members: [],
          updatedAt: null,
        };
      await json(route, projection);
      return;
    }

    if (isCardList) {
      state.allowedHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "hr") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      await json(route, { items: state.cards });
      return;
    }

    if (isTeamApi) {
      state.allowedHits.push(`${method} ${path}`);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "hr") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }

      if (path === "/api/hr/team-recommendations/projects" && method === "GET") {
        await json(route, { items: state.projects });
        return;
      }

      if (path === "/api/hr/team-recommendations" && method === "GET") {
        await json(route, { items: buildSummaries(state) });
        return;
      }

      const oneMatch = path.match(
        /^\/api\/hr\/team-recommendations\/([^/]+)$/,
      );
      if (oneMatch) {
        const projectId = decodeURIComponent(oneMatch[1]);
        if (method === "GET") {
          if (state.forceDetailError) {
            await json(
              route,
              {
                detail: {
                  code: "internal_error",
                  message: "SECRET_DETAIL_LEAK_path=C:\\\\secret",
                },
              },
              500,
            );
            return;
          }
          const proj = state.projects.find((p) => p.id === projectId);
          if (!proj) {
            await json(
              route,
              {
                detail: {
                  code: "hr_team_project_not_found",
                  message: "项目不存在",
                },
              },
              404,
            );
            return;
          }
          const detail = state.details[projectId];
          if (!detail) {
            await json(
              route,
              {
                detail: {
                  code: "hr_team_recommendation_not_found",
                  message: "尚未组装推荐",
                },
              },
              404,
            );
            return;
          }
          await json(route, detail);
          return;
        }
        if (method === "PUT") {
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
          if (state.forcePutError) {
            await json(
              route,
              {
                detail: {
                  code: "invalid_hr_team_recommendation",
                  message: "SECRET_PUT_LEAK_card=hcc_xxx",
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
          const proj = state.projects.find((p) => p.id === projectId);
          if (!proj) {
            await json(
              route,
              {
                detail: {
                  code: "hr_team_project_not_found",
                  message: "项目不存在",
                },
              },
              404,
            );
            return;
          }
          const b = (body ?? {}) as { memberCardIds?: unknown };
          const ids = Array.isArray(b.memberCardIds)
            ? (b.memberCardIds as string[])
            : [];
          const members: TeamMember[] = ids.map((cardId, idx) => {
            const card = state.cards.find((c) => c.id === cardId);
            return {
              order: idx + 1,
              personName: card?.personName ?? "未知",
              category: card?.category ?? "other",
              credentialName: card?.credentialName ?? "",
              level: card?.level ?? "",
              validUntil: card?.validUntil ?? null,
              sourceCardId: cardId,
            };
          });
          const now = "2026-07-14T12:00:00+00:00";
          const detail: TeamDetail = {
            projectId,
            projectName: proj.name,
            members,
            updatedAt: now,
          };
          state.details[projectId] = detail;
          await json(route, detail, state.details[projectId] ? 200 : 201);
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
      expect.soft(value.includes("一级建造师")).toBeFalsy();
      expect.soft(value.includes("SECRET_")).toBeFalsy();
    }
  }
}

function seedCards(): CardSummary[] {
  return [
    {
      id: "hcc_active_1",
      personName: "协作甲",
      category: "professional",
      credentialName: "一级建造师",
      level: "一级",
      validUntil: "2027-12-31",
      isActive: true,
      createdAt: "2026-07-13T08:00:00+00:00",
      updatedAt: "2026-07-13T09:00:00+00:00",
    },
    {
      id: "hcc_active_2",
      personName: "协作乙",
      category: "safety",
      credentialName: "安全员证",
      level: "B",
      validUntil: "2028-01-01",
      isActive: true,
      createdAt: "2026-07-13T08:00:00+00:00",
      updatedAt: "2026-07-13T09:00:00+00:00",
    },
    {
      id: "hcc_inactive_1",
      personName: "已停用丙",
      category: "other",
      credentialName: "旧证",
      level: "",
      validUntil: null,
      isActive: false,
      createdAt: "2026-07-13T08:00:00+00:00",
      updatedAt: "2026-07-13T09:00:00+00:00",
    },
  ];
}

test.describe("P10F 人力团队推荐快照前端", () => {
  test("HR 入口、初始网络边界、有序保存重读与停用提示", async ({ page }) => {
    const state: P10fState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("hr", { username: "user_hr", csrf: null }),
      resumeCsrf: "e2e-p10f-resume-csrf",
      currentCsrf: null,
      forbiddenHits: [],
      allowedHits: [],
      writeBodies: [],
      projects: [
        { id: "proj_tech_a", name: "技术标项目甲" },
        { id: "proj_tech_b", name: "技术标项目乙" },
      ],
      cards: seedCards(),
      details: {},
    };
    await installP10fRoutes(page, state);

    await page.goto("/restricted");
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toContainText("人力");
    await expect(nav).toContainText("人员资质");
    await expect(nav).toContainText("团队推荐");
    await expect(nav).not.toContainText("财务报价");
    await expect(nav).not.toContainText("标书生成");

    await nav.getByText("团队推荐", { exact: true }).click();
    await expect(page.getByTestId("hr-team-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("heading", { name: "团队推荐" }),
    ).toBeVisible();

    // 初始 HR 网络最小化：端点集合精确仅为 projects + credential-cards
    // （开发态 Strict Mode 可能重复同端点，故按去重集合断言；禁止摘要/详情/卡详情）
    await expect
      .poll(() =>
        [
          ...new Set(
            state.allowedHits.filter((h) => h.startsWith("GET /api/hr/")),
          ),
        ].sort(),
      )
      .toEqual(
        [
          "GET /api/hr/credential-cards",
          "GET /api/hr/team-recommendations/projects",
        ].sort(),
      );
    const initialHr = state.allowedHits.filter((h) =>
      h.startsWith("GET /api/hr/"),
    );
    expect(
      initialHr.every(
        (h) =>
          h === "GET /api/hr/credential-cards" ||
          h === "GET /api/hr/team-recommendations/projects",
      ),
    ).toBeTruthy();
    expect(initialHr).not.toContain("GET /api/hr/team-recommendations");
    expect(
      initialHr.some((h) =>
        /^GET \/api\/hr\/team-recommendations\/(?!projects$)[^/]+$/.test(h),
      ),
    ).toBeFalsy();
    expect(
      initialHr.some((h) => /^GET \/api\/hr\/credential-cards\//.test(h)),
    ).toBeFalsy();

    // 可选有效卡可见；停用卡不可选
    await expect(page.getByTestId("hr-team-card-hcc_active_1")).toBeVisible();
    await expect(page.getByTestId("hr-team-card-hcc_active_2")).toBeVisible();
    await expect(page.getByTestId("hr-team-card-hcc_inactive_1")).toHaveCount(
      0,
    );

    // 选择项目后才 GET 详情
    await page.getByTestId("hr-team-project-proj_tech_a").click();
    await expect(page.getByTestId("hr-team-detail-empty")).toBeVisible();
    expect(state.allowedHits).toContain(
      "GET /api/hr/team-recommendations/proj_tech_a",
    );

    // 按点击顺序选择成员
    await page.getByTestId("hr-team-card-hcc_active_2").click();
    await page.getByTestId("hr-team-card-hcc_active_1").click();
    await expect(page.getByTestId("hr-team-selected-order")).toContainText(
      "协作乙",
    );
    await expect(page.getByTestId("hr-team-selected-order")).toContainText(
      "协作甲",
    );

    await page.getByTestId("hr-team-save").click();
    await expect(page.getByTestId("hr-team-detail")).toBeVisible();
    const put = state.writeBodies.find((w) => w.method === "PUT");
    expect(put).toBeTruthy();
    expect(put?.csrf).toBe("e2e-p10f-resume-csrf");
    expect(put?.body).toEqual({
      memberCardIds: ["hcc_active_2", "hcc_active_1"],
    });
    // 保存后重读摘要与详情
    const putIdx = state.allowedHits.lastIndexOf(
      "PUT /api/hr/team-recommendations/proj_tech_a",
    );
    const afterPut = state.allowedHits.slice(putIdx + 1);
    expect(afterPut).toContain("GET /api/hr/team-recommendations");
    expect(afterPut).toContain(
      "GET /api/hr/team-recommendations/proj_tech_a",
    );

    // 模拟快照含已停用卡：重新装载详情
    state.details.proj_tech_a = {
      projectId: "proj_tech_a",
      projectName: "技术标项目甲",
      members: [
        {
          order: 1,
          personName: "已停用丙",
          category: "other",
          credentialName: "旧证",
          level: "",
          validUntil: null,
          sourceCardId: "hcc_inactive_1",
        },
        {
          order: 2,
          personName: "协作甲",
          category: "professional",
          credentialName: "一级建造师",
          level: "一级",
          validUntil: "2027-12-31",
          sourceCardId: "hcc_active_1",
        },
      ],
      updatedAt: "2026-07-14T13:00:00+00:00",
    };
    await page.getByTestId("hr-team-project-proj_tech_b").click();
    await page.getByTestId("hr-team-project-proj_tech_a").click();
    await expect(page.getByTestId("hr-team-inactive-warning")).toBeVisible();
    await expect(page.getByTestId("hr-team-inactive-warning")).toContainText(
      "已停用",
    );
    // 不可静默保存停用 ID：点保存应失败且 body 不含 hcc_inactive_1
    const writesBefore = state.writeBodies.length;
    await page.getByTestId("hr-team-save").click();
    await expect(page.getByTestId("hr-team-write-error")).toBeVisible();
    expect(state.writeBodies.length).toBe(writesBefore);

    // 错误脱敏：强制 PUT 失败不回显 secret
    await page.getByTestId("hr-team-remove-inactive").click();
    state.forcePutError = true;
    await page.getByTestId("hr-team-save").click();
    await expect(page.getByTestId("hr-team-write-error")).toContainText(
      FIXED_ERROR_SAVE_422,
    );
    await expect(page.getByTestId("hr-team-write-error")).not.toContainText(
      "SECRET_PUT",
    );
    await expect(page.getByTestId("hr-team-write-error")).not.toContainText(
      "hcc_xxx",
    );

    await assertNoSensitiveStorage(page);
    expect(state.forbiddenHits.length).toBe(0);
  });

  test("受限角色与 disabled 无入口且不发 HR API", async ({ page }) => {
    for (const role of ["bid_writer", "finance", "bidder", "owner"] as const) {
      const session =
        role === "owner"
          ? meFor("bid_writer", { isOwner: true, username: "owner_user" })
          : meFor(role, { username: `user_${role}` });
      // owner 用 bid_writer 角色但仍是业务角色；按契约 owner/非 hr 均不可见人力
      // 用 finance 代表非 hr；owner 测试用 isOwner 但 role 仍 bid_writer
      const state: P10fState = {
        bootstrapped: true,
        authRequired: true,
        session:
          role === "owner"
            ? meFor("bid_writer", {
                isOwner: true,
                username: "owner_user",
              })
            : session,
        resumeCsrf: "csrf",
        currentCsrf: null,
        forbiddenHits: [],
        allowedHits: [],
        writeBodies: [],
        projects: [],
        cards: [],
        details: {},
      };
      await installP10fRoutes(page, state);
      await page.goto("/hr/team-recommendations");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("hr-team-page")).toHaveCount(0);
      const nav = page.getByRole("navigation", { name: "主导航" });
      await expect(nav).not.toContainText("团队推荐");
      expect(
        state.allowedHits.some((h) => h.includes("/api/hr/team-recommendations")),
      ).toBeFalsy();
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }

    // disabled：无人力入口
    const disabled: P10fState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      allowedHits: [],
      writeBodies: [],
      projects: [],
      cards: [],
      details: {},
    };
    await installP10fRoutes(page, disabled);
    await page.goto("/hr/team-recommendations");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("hr-team-page")).toHaveCount(0);
    expect(
      disabled.allowedHits.some((h) =>
        h.includes("/api/hr/team-recommendations"),
      ),
    ).toBeFalsy();
  });

  test("bid_writer 按需投影 ready/empty/错误且不发 HR API", async ({
    page,
  }) => {
    const projectId = "proj_bw_1";
    const state: P10fState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { username: "user_bw", csrf: null }),
      resumeCsrf: "e2e-bw-csrf",
      currentCsrf: null,
      forbiddenHits: [],
      allowedHits: [],
      writeBodies: [],
      projects: [],
      cards: [],
      details: {},
      projectStub: {
        id: projectId,
        name: "技术标工作区项目",
        industry: "政务",
        status: "draft",
        updatedAt: "2026-07-14T00:00:00+00:00",
        technicalPlanStep: 1,
        wordCount: 0,
        kind: "technical",
        workspaceId: "ws_e2e",
      },
      bwProjection: {
        dataState: "ready",
        members: [
          {
            order: 1,
            personName: "协作甲",
            category: "professional",
            credentialName: "一级建造师",
            level: "一级",
            validUntil: "2027-12-31",
          },
        ],
        updatedAt: "2026-07-14T12:00:00+00:00",
      },
    };
    await installP10fRoutes(page, state);

    await page.goto(`/technical-plan/${projectId}/document`);
    await expect(page.getByTestId("app-shell")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByTestId("bw-team-recommendation-panel"),
    ).toBeVisible({ timeout: 15_000 });

    // 未点击前不得请求投影
    expect(
      state.allowedHits.some((h) => h.includes("/team-recommendation")),
    ).toBeFalsy();
    expect(
      state.allowedHits.some((h) => h.includes("/api/hr/")),
    ).toBeFalsy();

    await page.getByTestId("bw-team-recommendation-open").click();
    await expect(page.getByTestId("bw-team-recommendation-ready")).toBeVisible();
    await expect(page.getByTestId("bw-team-recommendation-ready")).toContainText(
      "协作甲",
    );
    await expect(page.getByTestId("bw-team-recommendation-ready")).toContainText(
      "一级建造师",
    );
    expect(state.allowedHits).toContain(
      `GET /api/projects/${projectId}/team-recommendation`,
    );
    // 无 sourceCardId / htr
    await expect(
      page.getByTestId("bw-team-recommendation-ready"),
    ).not.toContainText("hcc_");
    await expect(
      page.getByTestId("bw-team-recommendation-ready"),
    ).not.toContainText("htr_");
    expect(state.allowedHits.some((h) => h.includes("/api/hr/"))).toBeFalsy();

    // empty
    state.bwProjection = {
      dataState: "empty",
      members: [],
      updatedAt: null,
    };
    await page.getByTestId("bw-team-recommendation-open").click();
    await expect(page.getByTestId("bw-team-recommendation-empty")).toBeVisible();
    await expect(page.getByTestId("bw-team-recommendation-empty")).toContainText(
      "尚未推荐",
    );

    // 错误脱敏
    state.forceBwError = true;
    await page.getByTestId("bw-team-recommendation-open").click();
    await expect(page.getByTestId("bw-team-recommendation-error")).toBeVisible();
    await expect(page.getByTestId("bw-team-recommendation-error")).toContainText(
      FIXED_ERROR_BW,
    );
    await expect(
      page.getByTestId("bw-team-recommendation-error"),
    ).not.toContainText("SECRET_BW");

    await assertNoSensitiveStorage(page);
  });

  test("bid_writer 切换项目时清空旧投影且未点击不请求", async ({ page }) => {
    const projectA = "proj_bw_a";
    const projectB = "proj_bw_b";
    const mkStub = (id: string, name: string): ProjectStub => ({
      id,
      name,
      industry: "政务",
      status: "draft",
      updatedAt: "2026-07-14T00:00:00+00:00",
      technicalPlanStep: 1,
      wordCount: 0,
      kind: "technical",
      workspaceId: "ws_e2e",
    });
    const state: P10fState = {
      bootstrapped: true,
      authRequired: true,
      session: meFor("bid_writer", { username: "user_bw_switch", csrf: null }),
      resumeCsrf: "e2e-bw-switch-csrf",
      currentCsrf: null,
      forbiddenHits: [],
      allowedHits: [],
      writeBodies: [],
      projects: [],
      cards: [],
      details: {},
      projectStubs: [mkStub(projectA, "技术标项目A"), mkStub(projectB, "技术标项目B")],
      bwProjections: {
        [projectA]: {
          dataState: "ready",
          members: [
            {
              order: 1,
              personName: "成员甲A",
              category: "professional",
              credentialName: "一级建造师",
              level: "一级",
              validUntil: "2027-12-31",
            },
          ],
          updatedAt: "2026-07-14T12:00:00+00:00",
        },
        [projectB]: {
          dataState: "empty",
          members: [],
          updatedAt: null,
        },
      },
    };
    await installP10fRoutes(page, state);

    await page.goto(`/technical-plan/${projectA}/document`);
    await expect(page.getByTestId("bw-team-recommendation-panel")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByTestId("bw-team-recommendation-open").click();
    await expect(page.getByTestId("bw-team-recommendation-ready")).toBeVisible();
    await expect(page.getByTestId("bw-team-recommendation-ready")).toContainText(
      "成员甲A",
    );
    expect(state.allowedHits).toContain(
      `GET /api/projects/${projectA}/team-recommendation`,
    );

    // SPA 软导航 A→B：同一 React 实例内切换，禁止 page.goto 整页重载
    const hitsBeforeSwitch = state.allowedHits.length;
    await page.evaluate(
      (nextUrl) => {
        window.history.pushState({}, "", nextUrl);
        window.dispatchEvent(new PopStateEvent("popstate"));
      },
      `/technical-plan/${projectB}/document`,
    );

    // 切换后旧 ready/empty/error 不得再出现（含切换过程中的帧）
    await expect
      .poll(async () => {
        const ready = await page.getByTestId("bw-team-recommendation-ready").count();
        const empty = await page.getByTestId("bw-team-recommendation-empty").count();
        const err = await page.getByTestId("bw-team-recommendation-error").count();
        const leak = await page.getByText("成员甲A").count();
        return ready + empty + err + leak;
      })
      .toBe(0);

    await expect(page.getByTestId("bw-team-recommendation-panel")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("bw-team-recommendation-ready")).toHaveCount(0);
    await expect(page.getByTestId("bw-team-recommendation-empty")).toHaveCount(0);
    await expect(page.getByTestId("bw-team-recommendation-error")).toHaveCount(0);
    await expect(page.getByTestId("bw-team-recommendation-panel")).not.toContainText(
      "成员甲A",
    );
    // 新项目点击前不请求 B
    expect(
      state.allowedHits
        .slice(hitsBeforeSwitch)
        .some((h) => h.includes(`/projects/${projectB}/team-recommendation`)),
    ).toBeFalsy();

    // 仅用户再次点击后才请求 B
    await page.getByTestId("bw-team-recommendation-open").click();
    await expect(page.getByTestId("bw-team-recommendation-empty")).toBeVisible();
    expect(state.allowedHits).toContain(
      `GET /api/projects/${projectB}/team-recommendation`,
    );
    expect(state.allowedHits.some((h) => h.includes("/api/hr/"))).toBeFalsy();
    await assertNoSensitiveStorage(page);
  });
});
