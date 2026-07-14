/**
 * 模块：P8C 本地解析一次性回传票据前端 E2E
 * 用途：验收 required strict bid_writer 显式单次签发、内存票据与可执行 curl、
 *       错误脱敏、刷新丢失、disabled 旧表单兼容、非制作者零请求、网络/存储边界。
 * 对接：Playwright chromium；前端 5174；npm run test:e2e:local-parser-callback-ticket。
 * 二次开发：仅桩 bootstrap-status、auth me/csrf/logout、health、签发与旧/公共回调；
 *           Google 字体本地 204；未知本机 API 记 forbiddenHits 并 403；
 *           非本机外网记 externalHits 后 abort；仅固定假票据；禁止真实秘密、固定 sleep、可见窗口。
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

/** 单次拦截请求详情（禁止只记字符串导致假绿） */
type RequestHit = {
  method: string;
  path: string;
  postData: string | null;
  headers: Record<string, string>;
};

/** 固定假票据：不得使用真实秘密 */
const FAKE_TICKET = "e2e_fake_ticket_p8c_NOT_REAL";
const FAKE_EXPIRES = "2026-07-14T16:00:00.000Z";
const FIXED_CALLBACK_PATH = "/api/local-parser/callback";
const FIXED_TICKET_HEADER = "X-Local-Parse-Ticket";
const FIXED_ISSUE_ERROR = "生成一次性回传票据失败，请稍后重试";
const COMPAT_NOTICE = "无需一次性票据";
const EMPTY_PID_MSG = "请填写项目 ID";
/** 续发 CSRF 假值：与桩 /auth/csrf 及 apiFetch 附加头对齐 */
const RESUME_CSRF = "e2e-p8c-resume-csrf";
const SECRET_LEAK =
  "SECRET-LEAK detail=ticket_fail path=/api/projects/proj_x/parse-callback-ticket projectId=proj_secret ticket=REAL_TICKET_SHOULD_NOT_SHOW";
/**
 * 主路径编码用固定项目 ID：至少含空格与 `/`（可含中文）。
 * 若生产删掉 encodeURIComponent，pathname 将无 %20/%2F，本用例必须红。
 */
const ENCODE_NEED_PID = "proj e2e/路径 中文";
const ENCODE_NEED_PID_SEG = encodeURIComponent(ENCODE_NEED_PID);

type P8cState = {
  bootstrapped: boolean;
  authRequired: boolean;
  session: MePayload | null;
  resumeCsrf?: string;
  forbiddenHits: string[];
  issueHits: RequestHit[];
  oldCallbackHits: RequestHit[];
  publicCallbackHits: string[];
  externalHits: string[];
  forceIssueError?: boolean;
  issueErrorBody?: unknown;
  issueTicket?: string;
  issueExpiresAt?: string;
  /** 故意返回不可信 callbackPath，页面不得用它拼 URL */
  issueCallbackPath?: string;
};

/**
 * 用途：识别既有 index.html 引入的 Google 字体 URL。
 * 对接：本地 204 阻断且不计 externalHits。
 */
function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

/** 用途：判断是否本机主机。 */
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
  return {
    user: { id: `user_${role}`, username: opts.username ?? `user_${role}` },
    workspaces: [
      {
        id: "ws_e2e",
        name: "E2E 工作空间",
        role,
        isOwner: opts.isOwner ?? false,
      },
    ],
    activeWorkspaceId: "ws_e2e",
    csrfToken: opts.csrf === undefined ? null : opts.csrf,
  };
}

/** 用途：捕获请求 method/path/postData/headers（Playwright 头名为小写）。 */
function captureHit(req: {
  method: () => string;
  url: () => string;
  postData: () => string | null;
  headers: () => Record<string, string>;
}): RequestHit {
  let path = req.url();
  try {
    path = new URL(req.url()).pathname;
  } catch {
    /* 保留原串 */
  }
  return {
    method: req.method().toUpperCase(),
    path,
    postData: req.postData(),
    headers: req.headers(),
  };
}

/**
 * 用途：安装 auth + P8C 签发/旧回调/公共回调桩；阻断未知业务与外网。
 * 对接：字体本地 204；未知本机 API → forbiddenHits+403；外网 → externalHits+abort。
 * 二次开发：required/disabled 均不得宽泛放行未知 API。
 */
async function installP8cRoutes(page: Page, state: P8cState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

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

    if (!isLocalHost(host)) {
      state.externalHits.push(`${method} ${rawUrl}`);
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    // —— 精确白名单：bootstrap / health / auth / 签发 / 旧回调 / 公共回调 ——
    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, {
        bootstrapped: state.bootstrapped,
        authRequired: state.authRequired,
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

    if (!state.authRequired) {
      // disabled：允许旧 parse-callback；签发/公共回调仅观测后 403
      const oldCb = path.match(/^\/api\/projects\/([^/]+)\/parse-callback$/);
      if (oldCb && method === "POST") {
        state.oldCallbackHits.push(captureHit(req));
        await json(route, {
          ok: true,
          chars: 12,
          taskId: "task_e2e_old_cb",
        });
        return;
      }
      if (path.match(/^\/api\/projects\/[^/]+\/parse-callback-ticket$/)) {
        state.issueHits.push(captureHit(req));
        await json(
          route,
          { detail: { code: "role_forbidden", message: "不应签发" } },
          403,
        );
        return;
      }
      if (path === "/api/local-parser/callback") {
        state.publicCallbackHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "forbidden", message: "E2E 禁止公共回调" } },
          403,
        );
        return;
      }
      // 未知本机 API：禁用宽泛放行，记 forbiddenHits 并 403
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "role_forbidden", message: "未授权业务接口" } },
        403,
      );
      return;
    }

    // required 模式 auth
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
        csrfToken: state.resumeCsrf ?? RESUME_CSRF,
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.session = null;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    // 签发：精确 POST /api/projects/{encodeURIComponent(id)}/parse-callback-ticket
    // pathname 保留编码串（%20/%2F），禁止 decode 后再匹配（否则删掉 encodeURIComponent 会假绿）
    const issueMatch = path.match(
      /^\/api\/projects\/([^/]+)\/parse-callback-ticket$/,
    );
    if (issueMatch) {
      const hit = captureHit(req);
      state.issueHits.push(hit);
      const role = state.session?.workspaces[0]?.role;
      if (!state.session || role !== "bid_writer") {
        await json(
          route,
          { detail: { code: "role_forbidden", message: "角色不允许" } },
          403,
        );
        return;
      }
      if (method !== "POST") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(
          route,
          { detail: { code: "method_not_allowed", message: "仅 POST" } },
          405,
        );
        return;
      }
      // 不在此“宽松吞掉”非空 body；主路径 E2E 必须精确断言 postData 为 null/空串
      if (state.forceIssueError) {
        await json(
          route,
          state.issueErrorBody ?? {
            detail: {
              code: "local_parser_ticket_issue_failed",
              message: SECRET_LEAK,
              path: "/api/projects/proj_secret/parse-callback-ticket",
              projectId: "proj_secret",
              ticket: "REAL_TICKET_SHOULD_NOT_SHOW",
            },
          },
          500,
        );
        return;
      }
      await json(
        route,
        {
          ticket: state.issueTicket ?? FAKE_TICKET,
          expiresAt: state.issueExpiresAt ?? FAKE_EXPIRES,
          callbackPath:
            state.issueCallbackPath ??
            "https://evil.example/should-not-use",
        },
        201,
      );
      return;
    }

    // 旧项目回调
    const oldMatch = path.match(/^\/api\/projects\/([^/]+)\/parse-callback$/);
    if (oldMatch) {
      state.oldCallbackHits.push(captureHit(req));
      await json(route, {
        ok: true,
        chars: 8,
        taskId: "task_e2e_old",
      });
      return;
    }

    // 公共一次性回调
    if (path === "/api/local-parser/callback") {
      state.publicCallbackHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "forbidden", message: "E2E 禁止公共回调" } },
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

/**
 * 用途：新浏览器上下文存储必须精确为空（防票据写入普通名存储假绿）。
 * 对接：localStorage / sessionStorage / IndexedDB 数据库列表。
 * 二次开发：禁止 catch 枚举异常后伪装 []；禁止 filter(Boolean) 丢弃空名库；
 *           Playwright Chromium HTTP origin 必须强制 indexedDB.databases 为函数并直接枚举。
 */
async function assertStorageExactlyEmpty(page: Page) {
  const storage = await page.evaluate(async () => {
    const keysOf = (store: Storage) => {
      const keys: string[] = [];
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        if (key) keys.push(key);
      }
      return keys;
    };
    // API 不可用或枚举失败必须抛出，让测试失败（禁止伪装空列表）
    if (typeof indexedDB === "undefined") {
      throw new Error("indexedDB 不可用");
    }
    if (typeof indexedDB.databases !== "function") {
      throw new Error("indexedDB.databases 必须为函数");
    }
    const dbs = await indexedDB.databases();
    // 保留所有 name ?? ""，不得 filter(Boolean) 丢弃空名称数据库
    const idbNames = dbs.map((d) => d.name ?? "");
    return {
      localKeys: keysOf(window.localStorage),
      sessionKeys: keysOf(window.sessionStorage),
      idbNames,
    };
  });

  expect(storage.localKeys, "localStorage 必须精确为空").toEqual([]);
  expect(storage.sessionKeys, "sessionStorage 必须精确为空").toEqual([]);
  expect(storage.idbNames, "IndexedDB 数据库列表必须精确为空").toEqual([]);
}

/**
 * 用途：断言签发请求为 POST、pathname 为 encodeURIComponent 形态、无 body、续发 CSRF。
 * 对接：path 必须保留 %20/%2F 等编码串，禁止先 decode 再比对（否则删掉 encodeURIComponent 会假绿）。
 */
function assertIssuePostHit(hit: RequestHit, projectId: string) {
  expect(hit.method).toBe("POST");
  const encodedSeg = encodeURIComponent(projectId);
  expect(hit.path).toBe(
    `/api/projects/${encodedSeg}/parse-callback-ticket`,
  );
  // 无 body：严格 null 或空串，不允许 "{}"
  expect(
    hit.postData === null || hit.postData === "",
    `postData 必须为 null/空串，实际=${JSON.stringify(hit.postData)}`,
  ).toBeTruthy();
  expect(hit.headers["x-csrf-token"]).toBe(RESUME_CSRF);
}

function baseState(
  overrides: Partial<P8cState> & {
    role?: AuthRole;
    isOwner?: boolean;
  } = {},
): P8cState {
  const role = overrides.role ?? "bid_writer";
  const { role: _r, isOwner, ...rest } = overrides;
  return {
    bootstrapped: true,
    authRequired: true,
    session: meFor(role, {
      isOwner: isOwner ?? false,
      csrf: null,
    }),
    resumeCsrf: RESUME_CSRF,
    forbiddenHits: [],
    issueHits: [],
    oldCallbackHits: [],
    publicCallbackHits: [],
    externalHits: [],
    ...rest,
  };
}

test.describe("P8C 本地解析一次性回传票据前端", () => {
  test("required bid_writer：挂载/改项目 ID 零签发；点击严格 1 次；展示假票据与绝对 curl.exe", async ({
    page,
  }) => {
    const state = baseState({
      issueTicket: FAKE_TICKET,
      issueExpiresAt: FAKE_EXPIRES,
      issueCallbackPath: "https://evil.example/callback-must-ignore",
    });
    await installP8cRoutes(page, state);

    const consoleLines: string[] = [];
    page.on("console", (msg) => {
      consoleLines.push(msg.text());
    });

    await page.goto("/local-parser");
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByRole("button", { name: "生成一次性回传票据" }),
    ).toBeVisible();
    // required 不展示旧 Token/Markdown 回传
    await expect(
      page.getByLabel("X-Local-Token（可选，后端配置了才需要）"),
    ).toHaveCount(0);
    await expect(page.getByLabel("解析 Markdown")).toHaveCount(0);
    await expect(page.getByText(COMPAT_NOTICE)).toHaveCount(0);

    expect(state.issueHits).toEqual([]);
    expect(state.oldCallbackHits).toEqual([]);
    expect(state.publicCallbackHits).toEqual([]);

    // 填写/改变项目 ID 不得自动签发
    await page.getByLabel("项目 ID").fill("proj_e2e_alpha");
    await page.getByLabel("项目 ID").fill("proj_e2e_beta");
    await expect(page.getByLabel("项目 ID")).toHaveValue("proj_e2e_beta");
    expect(state.issueHits).toEqual([]);

    // 空项目 ID：固定中文，零请求
    await page.getByLabel("项目 ID").fill("   ");
    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect(page.getByTestId("lp-ticket-error")).toContainText(
      EMPTY_PID_MSG,
    );
    expect(state.issueHits).toEqual([]);

    // 显式点击：严格 1 次 POST，无 body + CSRF；项目 ID 必须触发 encodeURIComponent
    await page.getByLabel("项目 ID").fill(ENCODE_NEED_PID);
    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect
      .poll(() => state.issueHits.length, { timeout: 15_000 })
      .toBe(1);
    assertIssuePostHit(state.issueHits[0]!, ENCODE_NEED_PID);
    // 显式断言编码形态：%20（空格）与 %2F（/），防假绿
    expect(state.issueHits[0]!.path).toContain("%20");
    expect(state.issueHits[0]!.path).toContain("%2F");
    expect(state.issueHits[0]!.path).toBe(
      `/api/projects/${ENCODE_NEED_PID_SEG}/parse-callback-ticket`,
    );
    // 禁止未编码的原始空格/斜杠落在 pathname
    expect(state.issueHits[0]!.path).not.toContain("proj e2e/");
    expect(state.issueHits[0]!.path).not.toContain("/路径");
    expect(state.oldCallbackHits).toEqual([]);
    expect(state.publicCallbackHits).toEqual([]);

    await expect(page.getByTestId("lp-ticket-value")).toHaveText(FAKE_TICKET);
    await expect(page.getByTestId("lp-ticket-callback-path")).toHaveText(
      FIXED_CALLBACK_PATH,
    );
    await expect(page.getByTestId("lp-ticket-header-name")).toHaveText(
      FIXED_TICKET_HEADER,
    );

    const origin = new URL(page.url()).origin;
    const expectedAbsolute = `${origin}${FIXED_CALLBACK_PATH}`;
    const curlText = await page.getByTestId("lp-ticket-curl").innerText();
    expect(curlText).toContain("curl.exe");
    expect(curlText).toContain(expectedAbsolute);
    expect(curlText).toContain(FIXED_CALLBACK_PATH);
    expect(curlText).toContain(FIXED_TICKET_HEADER);
    expect(curlText).toContain(FAKE_TICKET);
    expect(curlText).not.toContain("evil.example");
    expect(curlText).not.toContain("callback-must-ignore");
    // 无自动复制按钮
    await expect(page.getByRole("button", { name: /复制/ })).toHaveCount(0);

    // URL / 控制台不得含票据
    expect(page.url()).not.toContain(FAKE_TICKET);
    expect(consoleLines.some((l) => l.includes(FAKE_TICKET))).toBeFalsy();

    await assertStorageExactlyEmpty(page);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
  });

  test("再次显式点击：先清空旧票据；累计只 +1；失败固定中文且 console 不泄密", async ({
    page,
  }) => {
    const state = baseState({
      issueTicket: FAKE_TICKET,
      issueExpiresAt: FAKE_EXPIRES,
    });
    await installP8cRoutes(page, state);

    const consoleLines: string[] = [];
    page.on("console", (msg) => {
      consoleLines.push(msg.text());
    });

    await page.goto("/local-parser");
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByLabel("项目 ID").fill("proj_e2e_reissue");
    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect(page.getByTestId("lp-ticket-value")).toHaveText(FAKE_TICKET, {
      timeout: 15_000,
    });
    expect(state.issueHits).toHaveLength(1);
    assertIssuePostHit(state.issueHits[0]!, "proj_e2e_reissue");

    // 切换为失败体
    state.forceIssueError = true;
    state.issueErrorBody = {
      detail: {
        code: "local_parser_ticket_issue_failed",
        message: SECRET_LEAK,
        path: "/api/projects/proj_secret/parse-callback-ticket",
        projectId: "proj_secret",
        ticket: "REAL_TICKET_SHOULD_NOT_SHOW",
      },
    };

    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect
      .poll(() => state.issueHits.length, { timeout: 15_000 })
      .toBe(2);
    assertIssuePostHit(state.issueHits[1]!, "proj_e2e_reissue");

    const err = page.getByTestId("lp-ticket-error");
    await expect(err).toBeVisible();
    await expect(err).toHaveText(FIXED_ISSUE_ERROR);
    await expect(err).not.toContainText("SECRET");
    await expect(err).not.toContainText("proj_secret");
    await expect(err).not.toContainText("REAL_TICKET");
    await expect(err).not.toContainText("parse-callback-ticket");
    await expect(err).not.toContainText("local_parser_ticket_issue_failed");
    // 旧票据不残留
    await expect(page.getByTestId("lp-ticket-value")).toHaveCount(0);
    const pageText = await page.getByTestId("local-parser-page").innerText();
    expect(pageText).not.toContain(FAKE_TICKET);
    expect(pageText).not.toContain("REAL_TICKET_SHOULD_NOT_SHOW");

    // console 不得出现假票据/敏感泄漏标记
    const consoleJoined = consoleLines.join("\n");
    expect(consoleJoined).not.toContain(FAKE_TICKET);
    expect(consoleJoined).not.toContain("SECRET_LEAK");
    expect(consoleJoined).not.toContain(SECRET_LEAK);
    expect(consoleJoined).not.toContain("REAL_TICKET");

    expect(state.publicCallbackHits).toEqual([]);
    expect(state.oldCallbackHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    await assertStorageExactlyEmpty(page);
  });

  test("刷新后假票据消失且不会自动再签发；剪贴板插桩 installed 且读写为 0", async ({
    page,
  }) => {
    const state = baseState({ issueTicket: FAKE_TICKET });
    await installP8cRoutes(page, state);

    await page.addInitScript(() => {
      const w = window as unknown as {
        __p8cClipboard?: {
          read: number;
          write: number;
          installed: boolean;
        };
      };
      w.__p8cClipboard = { read: 0, write: 0, installed: false };
      const fake: Pick<Clipboard, "readText" | "writeText"> = {
        readText: async () => {
          w.__p8cClipboard!.read += 1;
          return "";
        },
        writeText: async () => {
          w.__p8cClipboard!.write += 1;
        },
      };
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        get: () => fake,
      });
      w.__p8cClipboard.installed = true;
    });

    await page.goto("/local-parser");
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByLabel("项目 ID").fill("proj_e2e_refresh");
    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect(page.getByTestId("lp-ticket-value")).toHaveText(FAKE_TICKET, {
      timeout: 15_000,
    });
    expect(state.issueHits).toHaveLength(1);
    assertIssuePostHit(state.issueHits[0]!, "proj_e2e_refresh");

    await page.reload();
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("lp-ticket-value")).toHaveCount(0);
    // 刷新后不得自动再签发
    expect(state.issueHits).toHaveLength(1);
    expect(page.url()).not.toContain(FAKE_TICKET);

    const clip = await page.evaluate(() => {
      const w = window as unknown as {
        __p8cClipboard?: {
          read: number;
          write: number;
          installed: boolean;
        };
      };
      return w.__p8cClipboard ?? null;
    });
    // 必须安装成功，禁止 catch 失败后用默认 0 假绿
    expect(clip, "剪贴板插桩对象必须存在").not.toBeNull();
    expect(clip!.installed, "剪贴板插桩必须 installed=true").toBe(true);
    expect(clip!.read).toBe(0);
    expect(clip!.write).toBe(0);

    await assertStorageExactlyEmpty(page);
    expect(state.publicCallbackHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
  });

  test("disabled：无需一次性票据；旧表单精确 POST 一次；签发与公共回调 0", async ({
    page,
  }) => {
    const state: P8cState = {
      bootstrapped: true,
      authRequired: false,
      session: null,
      forbiddenHits: [],
      issueHits: [],
      oldCallbackHits: [],
      publicCallbackHits: [],
      externalHits: [],
    };
    await installP8cRoutes(page, state);

    await page.goto("/local-parser");
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("lp-compat-notice")).toContainText(
      COMPAT_NOTICE,
    );
    await expect(
      page.getByRole("button", { name: "生成一次性回传票据" }),
    ).toHaveCount(0);
    expect(state.issueHits).toEqual([]);
    expect(state.publicCallbackHits).toEqual([]);

    await page.getByLabel("项目 ID").fill("proj_disabled_old");
    await page
      .getByLabel("X-Local-Token（可选，后端配置了才需要）")
      .fill("e2e-local-token-fake");
    await page.getByLabel("解析 Markdown").fill("# E2E\n正文");
    await page.getByRole("button", { name: "回传到项目" }).click();
    await expect
      .poll(() => state.oldCallbackHits.length, { timeout: 15_000 })
      .toBe(1);

    const hit = state.oldCallbackHits[0]!;
    expect(hit.method).toBe("POST");
    expect(hit.path).toBe(
      "/api/projects/proj_disabled_old/parse-callback",
    );
    expect(hit.headers["x-local-token"]).toBe("e2e-local-token-fake");
    expect(hit.postData).toBeTruthy();
    const body = JSON.parse(hit.postData!) as {
      markdown: string;
      source: string;
      filename: string;
    };
    expect(body).toEqual({
      markdown: "# E2E\n正文",
      source: "mineru",
      filename: "local-mineru.md",
    });

    expect(state.issueHits).toEqual([]);
    expect(state.publicCallbackHits).toEqual([]);
    await expect(page.getByText(/回传成功/)).toBeVisible();
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    await assertStorageExactlyEmpty(page);
  });

  for (const role of ["finance", "hr", "bidder"] as AuthRole[]) {
    test(`${role} 进 /local-parser 受限；签发/旧回传/公共回调均为 0`, async ({
      page,
    }) => {
      const state = baseState({
        role,
        isOwner: role === "finance",
      });
      await installP8cRoutes(page, state);

      await page.goto("/local-parser");
      await expect(page.getByTestId("auth-restricted")).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByTestId("local-parser-page")).toHaveCount(0);
      expect(state.issueHits).toEqual([]);
      expect(state.oldCallbackHits).toEqual([]);
      expect(state.publicCallbackHits).toEqual([]);
      expect(state.forbiddenHits).toEqual([]);
      expect(state.externalHits).toEqual([]);
      await assertStorageExactlyEmpty(page);
    });
  }

  test("非 bid_writer 仅 owner 语义：bidder+isOwner 受限且零请求", async ({
    page,
  }) => {
    const state = baseState({ role: "bidder", isOwner: true });
    await installP8cRoutes(page, state);
    await page.goto("/local-parser");
    await expect(page.getByTestId("auth-restricted")).toBeVisible({
      timeout: 15_000,
    });
    expect(state.issueHits).toEqual([]);
    expect(state.oldCallbackHits).toEqual([]);
    expect(state.publicCallbackHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    await assertStorageExactlyEmpty(page);
  });

  test("主路径 externalHits=[]、forbiddenHits=[]；外网探针可观测阻断且字体不计外网", async ({
    page,
  }) => {
    const state = baseState({ issueTicket: FAKE_TICKET });
    await installP8cRoutes(page, state);

    await page.goto("/local-parser");
    await expect(page.getByTestId("local-parser-page")).toBeVisible({
      timeout: 15_000,
    });
    await page.getByLabel("项目 ID").fill("proj_e2e_net");
    await page.getByRole("button", { name: "生成一次性回传票据" }).click();
    await expect(page.getByTestId("lp-ticket-value")).toHaveText(FAKE_TICKET, {
      timeout: 15_000,
    });
    // 探针前必须为空
    expect(state.externalHits).toEqual([]);
    expect(state.forbiddenHits).toEqual([]);
    assertIssuePostHit(state.issueHits[0]!, "proj_e2e_net");

    await page.evaluate(async () => {
      try {
        await fetch("https://example.invalid/p8c-probe", {
          method: "GET",
          mode: "no-cors",
        });
      } catch {
        /* abort 可能抛错 */
      }
    });

    await expect
      .poll(() => state.externalHits.length, { timeout: 10_000 })
      .toBeGreaterThan(0);
    // 探针后只允许预期 example.invalid
    expect(
      state.externalHits.every(
        (h) =>
          h.includes("example.invalid") &&
          h.includes("p8c-probe") &&
          !h.includes("fonts.googleapis.com") &&
          !h.includes("fonts.gstatic.com"),
      ),
    ).toBeTruthy();
    expect(state.forbiddenHits).toEqual([]);
    await assertStorageExactlyEmpty(page);
  });
});
