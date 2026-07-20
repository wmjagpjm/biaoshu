/**
 * 模块：P13-B/P13-C 已载入版本时间与修订来源可见性专项 E2E
 * 用途：验证技术标/商务标标题区展示当前已载入服务端版本 UTC 更新时间与修订来源；
 *       覆盖初始 GET、成功 PUT、409/失败保值、显式重载、A→B 迟到隔离与零额外请求。
 * 对接：Playwright chromium 单 worker；可变 editor-state route 桩；两工作区固定 testid。
 * 二次开发：禁止 sleep 作完成证据、宽泛 or、只读 route 自证；请求计数须精确；
 *       禁止声称「远端最新/实时/在线/最后由」；来源仅九类精确字符串。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p13b_tech_a";
const TECH_B = "proj_e2e_p13b_tech_b";
const BIZ_A = "proj_e2e_p13b_biz_a";
const BIZ_B = "proj_e2e_p13b_biz_b";
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const TECH_TESTID = "technical-editor-version-freshness";
const BIZ_TESTID = "business-editor-version-freshness";
const TECH_SOURCE_TESTID = "technical-editor-version-source";
const BIZ_SOURCE_TESTID = "business-editor-version-source";
const KNOWN_LABEL = "当前已载入版本：2026-07-20 12:34:56 UTC";
const UNKNOWN_LABEL = "当前已载入版本：更新时间未知";
const KNOWN_SOURCE_LABEL = "当前版本来源：浏览器保存";
const UNKNOWN_SOURCE_LABEL = "当前版本来源：来源未知";
const FORBIDDEN_PHRASES = ["在线", "最后由", "实时", "远端最新"];
/** 九类固定修订来源（与 editorStateRevisionApi 唯一集合对齐） */
const NINE_SOURCES = [
  "browser_put",
  "task",
  "revise",
  "callback",
  "local_parser",
  "content_fuse_apply",
  "content_fuse_consume",
  "checkpoint_restore",
  "revision_restore",
] as const;
const SOURCE_LABELS: Record<(typeof NINE_SOURCES)[number], string> = {
  browser_put: "浏览器保存",
  task: "任务写入",
  revise: "智能修订",
  callback: "解析回传",
  local_parser: "本地解析",
  content_fuse_apply: "内容融合应用",
  content_fuse_consume: "内容融合消费",
  checkpoint_restore: "检查点恢复",
  revision_restore: "修订恢复",
};
/**
 * React StrictMode 下项目会话 useEffect 双调用：
 * page.goto / 切项目会话进入 editor-state 精确 +2 GET；
 * 显式 reloadFromApi 按钮仍为精确 +1。
 */
const PROJECT_SESSION_GETS = 2;

type Kind = "technical" | "business";

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: Kind;
  linkedProjectId?: string | null;
};

type EditorState = {
  projectId: string;
  outline: unknown[];
  chapters: unknown[];
  facts: unknown[];
  mode: string;
  analysisOverview: string;
  analysis: {
    overview: string;
    techRequirements: string[];
    rejectionRisks: string[];
    scoringPoints: Array<{ name: string; weight: string }>;
  };
  responseMatrix: unknown[];
  responseMatrixVersion: string | null;
  parsedMarkdown: string;
  guidance: Record<string, unknown> | null;
  businessQualify: unknown[];
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  stateVersion: string;
  updatedAt: string | null;
  /** P13-C：当前已载入版本在修订账本中的来源；可 null */
  currentRevisionSourceKind: string | null;
};

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
};

type GetMode =
  | { kind: "ok" }
  | { kind: "fail"; status: number }
  | { kind: "gate"; gate: HoldGate; then: "ok" | "fail"; status?: number };

type PutMode =
  | {
      kind: "ok";
      updatedAt?: string | null;
      currentRevisionSourceKind?: string | null;
      stripStateVersion?: boolean;
      invalidStateVersion?: boolean;
    }
  | { kind: "fail"; status: number }
  | { kind: "full_conflict" }
  /** 真实网络层中断：route.abort，不得用 HTTP 500 冒充 */
  | { kind: "abort" }
  | {
      kind: "gate";
      gate: HoldGate;
      then: "ok" | "fail" | "full_conflict";
      status?: number;
      updatedAt?: string | null;
      currentRevisionSourceKind?: string | null;
    };

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  getMode: Record<string, GetMode>;
  putMode: Record<string, PutMode>;
  /** 可选：下一次 GET 覆写 updatedAt（用后清除） */
  nextGetUpdatedAt: Record<string, string | null | undefined>;
  /** 可选：下一次 GET 覆写 currentRevisionSourceKind（用后清除） */
  nextGetSourceKind: Record<string, string | null | undefined>;
  versionSeq: number;
  getLog: string[];
  putLog: Array<{ projectId: string; body: Record<string, unknown> }>;
  editorRequestLog: Array<"GET" | "PUT">;
  forbiddenHits: string[];
  externalHits: string[];
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function allocateStateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

function createHoldGate(): HoldGate {
  let released = false;
  const waiters: Array<() => void> = [];
  return {
    wait: () =>
      released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          }),
    release: () => {
      released = true;
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    isReleased: () => released,
  };
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function isLocalHost(host: string): boolean {
  return host === "127.0.0.1" || host === "localhost";
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function isAllowedApi(method: string, path: string): boolean {
  const pid = "proj_[a-z0-9_]+";
  const rules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces(\/|$)/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings(\/|$)/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET", "PATCH"], path: new RegExp(`^/api/projects/${pid}/?$`) },
    {
      methods: ["GET", "PUT"],
      path: new RegExp(`^/api/projects/${pid}/editor-state/?$`),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(`^/api/projects/${pid}/(files|tasks|images)/?$`),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/tasks/[^/]+(/(events|cancel))?/?$`,
      ),
    },
    {
      methods: ["POST"],
      path: new RegExp(
        `^/api/projects/${pid}/artifacts/workspace/revise/?$`,
      ),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/editor-state-checkpoints(/|$)?`,
      ),
    },
    {
      methods: ["GET", "POST"],
      path: new RegExp(
        `^/api/projects/${pid}/editor-state-revisions(/|$)?`,
      ),
    },
  ];
  return rules.some((r) => r.methods.includes(method) && r.path.test(path));
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-20T12:00:00",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    kind: partial.kind,
    id: partial.id,
    name: partial.name,
  };
}

function emptyEditor(projectId: string, updatedAt: string | null): EditorState {
  return {
    projectId,
    outline: [],
    chapters: [],
    facts: [],
    mode: "ALIGNED",
    analysisOverview: "",
    analysis: {
      overview: "",
      techRequirements: [],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: "rmv_e2e_empty",
    parsedMarkdown: "",
    guidance: null,
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion: seedStateVersion(1),
    updatedAt,
    currentRevisionSourceKind: null,
  };
}

function createProbeState(seed: ProjectStub[] = []): ProbeState {
  const editorById: Record<string, EditorState> = {};
  for (const p of seed) {
    editorById[p.id] = emptyEditor(p.id, "2026-07-20T12:34:56");
  }
  return {
    projects: [...seed],
    editorById,
    getMode: {},
    putMode: {},
    nextGetUpdatedAt: {},
    nextGetSourceKind: {},
    versionSeq: 100,
    getLog: [],
    putLog: [],
    editorRequestLog: [],
    forbiddenHits: [],
    externalHits: [],
  };
}

function collectConsole(page: Page): string[] {
  const lines: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      lines.push(`${msg.type()}: ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    lines.push(`pageerror: ${String(err)}`);
  });
  return lines;
}

/** 过滤浏览器网络层噪声；保留应用 pageerror 与业务 console。 */
function appConsoleLines(lines: string[]): string[] {
  return lines.filter((line) => {
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

function assertCleanConsole(lines: string[]) {
  expect(
    appConsoleLines(lines),
    `不得出现应用 pageerror/console.error：\n${appConsoleLines(lines).join("\n")}`,
  ).toEqual([]);
}

/** 只检查版本时间组件文案，避免壳层「API 在线」误伤。 */
async function assertNoForbiddenFreshnessCopy(page: Page, testId: string) {
  const text = await page.getByTestId(testId).innerText();
  for (const phrase of FORBIDDEN_PHRASES) {
    expect(text, `版本时间文案不得出现「${phrase}」`).not.toContain(phrase);
  }
  // 明确未做承诺不得出现在标题区 freshness 邻域（同 header 左栏）
  const header = page.locator("header.page-header").first();
  const headerText = await header.innerText();
  for (const phrase of ["远端最新", "最后由", "实时同步", "在线成员"] as const) {
    expect(headerText, `标题区不得出现「${phrase}」`).not.toContain(phrase);
  }
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const rawUrl = req.url();
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(rawUrl)) {
      await route.fulfill({ status: 204, contentType: "text/plain", body: "" });
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

    if (!isLocalHost(url.hostname)) {
      state.externalHits.push(`${method} ${rawUrl}`);
      await route.abort("failed");
      return;
    }

    const path = url.pathname;
    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    if (!isAllowedApi(method, path)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(route, { detail: { code: "p13b_forbidden" } }, 403);
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

    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, { bootstrapped: true, authRequired: false });
      return;
    }

    if (path === "/api/auth/me" && method === "GET") {
      await json(route, {
        user: { id: "user_e2e", username: "e2e" },
        workspaces: [
          {
            id: "ws_e2e",
            name: "E2E 工作空间",
            role: "bid_writer",
            isOwner: true,
          },
        ],
        activeWorkspaceId: "ws_e2e",
        csrfToken: null,
      });
      return;
    }

    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-p13b-csrf" });
      return;
    }

    if (
      (path === "/api/auth/login" || path === "/api/auth/logout") &&
      method === "POST"
    ) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (
      (path === "/api/workspace" || path === "/api/workspaces") &&
      method === "GET"
    ) {
      await json(route, {
        id: "ws_e2e",
        name: "E2E 工作空间",
        ownerUserId: "user_e2e",
      });
      return;
    }

    if (
      path.startsWith("/api/settings") &&
      (method === "GET" || method === "PUT")
    ) {
      await json(route, {
        provider: "openai-compatible",
        apiBaseUrl: "",
        apiKey: "",
        model: "",
        parseStrategy: "light",
      });
      return;
    }

    if (path === "/api/projects" || path === "/api/projects/") {
      if (method === "GET") {
        const kind = url.searchParams.get("kind");
        let items = state.projects;
        if (kind === "technical" || kind === "business") {
          items = items.filter((p) => p.kind === kind);
        }
        await json(route, items);
        return;
      }
      if (method === "POST") {
        await json(route, { detail: { code: "p13b_no_create" } }, 403);
        return;
      }
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = detailMatch[1];
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(route, { detail: { code: "project_not_found" } }, 404);
        return;
      }
      await json(route, found);
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      const id = editorMatch[1];
      if (method === "GET") {
        state.getLog.push(id);
        state.editorRequestLog.push("GET");
        const mode = state.getMode[id] ?? { kind: "ok" as const };
        if (mode.kind === "gate") {
          await mode.gate.wait();
          if (mode.then === "fail") {
            await json(
              route,
              { detail: { code: "editor_state_get_failed" } },
              mode.status ?? 500,
            );
            return;
          }
        } else if (mode.kind === "fail") {
          await json(
            route,
            { detail: { code: "editor_state_get_failed" } },
            mode.status,
          );
          return;
        }

        const body = {
          ...(state.editorById[id] ?? emptyEditor(id, "2026-07-20T12:34:56")),
        };
        if (Object.prototype.hasOwnProperty.call(state.nextGetUpdatedAt, id)) {
          body.updatedAt = state.nextGetUpdatedAt[id] as string | null;
          delete state.nextGetUpdatedAt[id];
        }
        if (Object.prototype.hasOwnProperty.call(state.nextGetSourceKind, id)) {
          body.currentRevisionSourceKind = state.nextGetSourceKind[
            id
          ] as string | null;
          delete state.nextGetSourceKind[id];
        }
        await json(route, body);
        return;
      }

      // PUT
      const raw = req.postData() || "{}";
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = {};
      }
      state.putLog.push({ projectId: id, body });
      state.editorRequestLog.push("PUT");

      const mode = state.putMode[id] ?? { kind: "ok" as const };
      if (mode.kind === "abort") {
        // 真实网络失败：不返回 HTTP 状态体
        await route.abort("failed");
        return;
      }
      if (mode.kind === "gate") {
        await mode.gate.wait();
        if (mode.then === "fail") {
          await json(
            route,
            { detail: { code: "editor_state_put_failed" } },
            mode.status ?? 500,
          );
          return;
        }
        if (mode.then === "full_conflict") {
          const cur = state.editorById[id] ?? emptyEditor(id, null);
          await json(
            route,
            {
              detail: {
                code: "editor_state_version_conflict",
                message: "编辑内容已被其他操作更新，请重新载入后再保存",
                currentStateVersion: cur.stateVersion,
              },
            },
            409,
          );
          return;
        }
        // gate then ok → fall through
      } else if (mode.kind === "fail") {
        await json(
          route,
          { detail: { code: "editor_state_put_failed" } },
          mode.status,
        );
        return;
      } else if (mode.kind === "full_conflict") {
        const cur = state.editorById[id] ?? emptyEditor(id, null);
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "编辑内容已被其他操作更新，请重新载入后再保存",
              currentStateVersion: cur.stateVersion,
            },
          },
          409,
        );
        return;
      }

      const prev = state.editorById[id] ?? emptyEditor(id, null);
      const expected = body.expectedStateVersion;
      if (
        isValidStateVersion(expected) &&
        expected !== prev.stateVersion
      ) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "编辑内容已被其他操作更新，请重新载入后再保存",
              currentStateVersion: prev.stateVersion,
            },
          },
          409,
        );
        return;
      }

      const nextVersion =
        mode.kind === "ok" && mode.invalidStateVersion
          ? "not-a-version"
          : mode.kind === "ok" && mode.stripStateVersion
            ? undefined
            : allocateStateVersion(state);

      const nextUpdatedAt =
        mode.kind === "ok" && Object.prototype.hasOwnProperty.call(mode, "updatedAt")
          ? mode.updatedAt!
          : mode.kind === "gate" &&
              mode.then === "ok" &&
              Object.prototype.hasOwnProperty.call(mode, "updatedAt")
            ? mode.updatedAt!
            : "2026-07-20T15:00:00";

      const nextSourceKind =
        mode.kind === "ok" &&
        Object.prototype.hasOwnProperty.call(mode, "currentRevisionSourceKind")
          ? mode.currentRevisionSourceKind!
          : mode.kind === "gate" &&
              mode.then === "ok" &&
              Object.prototype.hasOwnProperty.call(
                mode,
                "currentRevisionSourceKind",
              )
            ? mode.currentRevisionSourceKind!
            : "browser_put";

      const next: EditorState = {
        ...prev,
        projectId: id,
        outline: Array.isArray(body.outline) ? body.outline : prev.outline,
        chapters: Array.isArray(body.chapters) ? body.chapters : prev.chapters,
        facts: Array.isArray(body.facts) ? body.facts : prev.facts,
        mode: typeof body.mode === "string" ? body.mode : prev.mode,
        analysisOverview:
          typeof body.analysisOverview === "string"
            ? body.analysisOverview
            : prev.analysisOverview,
        analysis:
          body.analysis && typeof body.analysis === "object"
            ? (body.analysis as EditorState["analysis"])
            : prev.analysis,
        responseMatrix: Array.isArray(body.responseMatrix)
          ? body.responseMatrix
          : prev.responseMatrix,
        responseMatrixVersion:
          typeof body.responseMatrixVersion === "string"
            ? body.responseMatrixVersion
            : prev.responseMatrixVersion,
        parsedMarkdown:
          body.parsedMarkdown != null
            ? String(body.parsedMarkdown)
            : prev.parsedMarkdown,
        guidance:
          body.guidance && typeof body.guidance === "object"
            ? (body.guidance as Record<string, unknown>)
            : prev.guidance,
        businessQualify: Array.isArray(body.businessQualify)
          ? body.businessQualify
          : prev.businessQualify,
        businessToc: Array.isArray(body.businessToc)
          ? body.businessToc
          : prev.businessToc,
        businessQuote:
          body.businessQuote && typeof body.businessQuote === "object"
            ? (body.businessQuote as EditorState["businessQuote"])
            : prev.businessQuote,
        businessCommit: Array.isArray(body.businessCommit)
          ? body.businessCommit
          : prev.businessCommit,
        stateVersion:
          typeof nextVersion === "string" ? nextVersion : prev.stateVersion,
        updatedAt: nextUpdatedAt,
        currentRevisionSourceKind: nextSourceKind,
      };
      state.editorById[id] = next;

      const responseBody: Record<string, unknown> = { ...next };
      if (mode.kind === "ok" && mode.stripStateVersion) {
        delete responseBody.stateVersion;
      } else if (mode.kind === "ok" && mode.invalidStateVersion) {
        responseBody.stateVersion = "not-a-version";
      }
      await json(route, responseBody);
      return;
    }

    // 其它白名单路径给最小空响应，避免工作区附属请求拖垮测试
    if (method === "GET") {
      await json(route, []);
      return;
    }
    if (method === "POST") {
      await json(route, { ok: true });
      return;
    }
    await json(route, { detail: { code: "p13b_unhandled" } }, 404);
  });
}

async function openTechnical(page: Page, projectId: string) {
  // analysis 步含可编辑概述，便于触发防抖 PUT
  await page.goto(`/technical-plan/${projectId}/analysis`);
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible();
}

async function openBusiness(page: Page, projectId: string) {
  await page.goto(`/business-bid/${projectId}/parse`);
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible();
}

async function editTechnicalOverview(page: Page, text: string) {
  const overview = page.getByTestId("technical-analysis-overview");
  await expect(overview).toBeVisible();
  await overview.fill(text);
}

async function editBusinessMarkdown(page: Page, text: string) {
  const area = page.getByLabel("商务条款解析 Markdown");
  await expect(area).toBeVisible();
  await area.fill(text);
}

test.describe("P13-B 已载入编辑版本更新时间可见性", () => {
  test("failure-first：旧页面缺少两个固定 testid", async ({ page }) => {
    const tech = makeProject({
      id: TECH_A,
      name: "P13B技术甲",
      kind: "technical",
    });
    const biz = makeProject({
      id: BIZ_A,
      name: "P13B商务甲",
      kind: "business",
    });
    const state = createProbeState([tech, biz]);
    state.editorById[TECH_A] = emptyEditor(TECH_A, "2026-07-20T12:34:56");
    state.editorById[BIZ_A] = emptyEditor(BIZ_A, "2026-07-20T12:34:56");
    await installRoutes(page, state);

    await openTechnical(page, TECH_A);
    await expect(page.getByTestId("technical-editor-workspace")).toBeVisible();
    // 首个业务断言：固定技术标 testid 必须存在
    await expect(page.getByTestId(TECH_TESTID)).toBeVisible();

    await openBusiness(page, BIZ_A);
    await expect(page.getByTestId("business-editor-workspace")).toBeVisible();
    await expect(page.getByTestId(BIZ_TESTID)).toBeVisible();
  });

  test("技术标：合法 UTC 显示；非法值未知；成功 PUT 无额外 GET", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13B技术合法",
      kind: "technical",
    });
    const state = createProbeState([project]);
    state.editorById[TECH_A] = {
      ...emptyEditor(TECH_A, "2026-07-20T12:34:56.123456"),
      stateVersion: seedStateVersion(10),
      analysisOverview: "初始概述",
    };
    state.putMode[TECH_A] = {
      kind: "ok",
      updatedAt: "2026-07-20T15:00:00.5",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    await openTechnical(page, TECH_A);
    const freshness = page.getByTestId(TECH_TESTID);
    await expect(freshness).toHaveText(KNOWN_LABEL);
    await assertNoForbiddenFreshnessCopy(page, TECH_TESTID);

    const getsBefore = state.getLog.length;
    const putsBefore = state.putLog.length;
    const editorBefore = state.editorRequestLog.length;

    await editTechnicalOverview(page, "概述已编辑触发PUT");
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsBefore + 1);
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 15:00:00 UTC");

    // 成功 PUT 后展示时间更新，且不得因展示功能额外多 GET
    expect(state.getLog.length).toBe(getsBefore);
    expect(state.editorRequestLog.length).toBe(editorBefore + 1);
    expect(state.editorRequestLog[state.editorRequestLog.length - 1]).toBe(
      "PUT",
    );
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });

  test("技术标：缺失/null/空白/时区后缀/越界均显示未知且无 error", async ({
    page,
  }) => {
    const cases: Array<{
      id: string;
      label: string;
      updatedAt: string | null;
      omitField?: boolean;
    }> = [
      {
        id: "proj_e2e_p13b_tech_missing",
        label: "缺失字段",
        updatedAt: null,
        omitField: true,
      },
      { id: "proj_e2e_p13b_tech_null", label: "null", updatedAt: null },
      { id: "proj_e2e_p13b_tech_blank", label: "空白", updatedAt: "   " },
      {
        id: "proj_e2e_p13b_tech_z",
        label: "Z后缀",
        updatedAt: "2026-07-20T12:34:56Z",
      },
      {
        id: "proj_e2e_p13b_tech_offset",
        label: "偏移",
        updatedAt: "2026-07-20T12:34:56+08:00",
      },
      {
        id: "proj_e2e_p13b_tech_oob",
        label: "越界月日",
        updatedAt: "2026-13-40T12:34:56",
      },
      {
        id: "proj_e2e_p13b_tech_garbage",
        label: "任意字符串",
        updatedAt: "not-a-date",
      },
    ];

    for (const c of cases) {
      const project = makeProject({
        id: c.id,
        name: `P13B非法-${c.label}`,
        kind: "technical",
      });
      const state = createProbeState([project]);
      const editor = emptyEditor(c.id, c.updatedAt);
      if (c.omitField) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        delete (editor as any).updatedAt;
      }
      state.editorById[c.id] = editor;
      const consoleLines = collectConsole(page);
      await installRoutes(page, state);
      await openTechnical(page, c.id);
      await expect(page.getByTestId(TECH_TESTID)).toHaveText(UNKNOWN_LABEL);
      assertCleanConsole(consoleLines);
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("技术标：PUT 409/网络失败保留时间；显式重载成功更新、失败保值", async ({
    page,
  }) => {
    const project = makeProject({
      id: TECH_A,
      name: "P13B技术冲突",
      kind: "technical",
    });
    const state = createProbeState([project]);
    state.editorById[TECH_A] = {
      ...emptyEditor(TECH_A, "2026-07-20T12:34:56"),
      stateVersion: seedStateVersion(20),
    };
    state.putMode[TECH_A] = { kind: "full_conflict" };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    await openTechnical(page, TECH_A);
    const freshness = page.getByTestId(TECH_TESTID);
    await expect(freshness).toHaveText(KNOWN_LABEL);

    const putsAtStart = state.putLog.length;
    await editTechnicalOverview(page, "触发409");
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAtStart + 1);
    await expect(page.getByTestId("technical-editor-state-conflict")).toBeVisible();
    await expect(freshness).toHaveText(KNOWN_LABEL);
    // 409 进入全状态阻断：稳定窗口内无自动重试
    {
      const expectedPuts = putsAtStart + 1;
      const stableSince = Date.now();
      await expect
        .poll(() => {
          if (state.putLog.length !== expectedPuts) return "count-drift";
          return Date.now() - stableSince >= 1_500 ? "stable" : "waiting";
        }, { timeout: 3_000 })
        .toBe("stable");
    }

    // 显式重载成功：时间更新，冲突解除
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      updatedAt: "2026-07-20T18:22:11",
      stateVersion: seedStateVersion(21),
    };
    state.getMode[TECH_A] = { kind: "ok" };
    const getsBeforeReloadOk = state.getLog.filter((id) => id === TECH_A).length;
    await page.getByTestId("technical-editor-state-reload").click();
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsBeforeReloadOk + 1);
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 18:22:11 UTC");
    await expect(page.getByTestId("technical-editor-state-conflict")).toHaveCount(
      0,
    );

    // 真实 PUT 网络 abort：保留已载入时间、进入保存失败阻断、零自动重试
    state.putMode[TECH_A] = { kind: "abort" };
    const putsBeforeAbort = state.putLog.length;
    const getsBeforeAbort = state.getLog.filter((id) => id === TECH_A).length;
    await editTechnicalOverview(page, "触发PUT网络abort");
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsBeforeAbort + 1);
    await expect(page.getByTestId("technical-editor-save-error")).toBeVisible();
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 18:22:11 UTC");
    // 空闲稳定窗口：不得自动重试 PUT，也不得因 abort 额外 GET
    {
      const expectedPuts = putsBeforeAbort + 1;
      const stableSince = Date.now();
      await expect
        .poll(() => {
          if (state.putLog.length !== expectedPuts) return "count-drift";
          if (state.getLog.filter((id) => id === TECH_A).length !== getsBeforeAbort) {
            return "get-drift";
          }
          return Date.now() - stableSince >= 1_500 ? "stable" : "waiting";
        }, { timeout: 3_000 })
        .toBe("stable");
    }

    // 显式 GET 成功恢复（整页会话重载，非伪造 HTTP 500 PUT）
    state.putMode[TECH_A] = { kind: "ok" };
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      updatedAt: "2026-07-20T19:30:00",
      stateVersion: seedStateVersion(22),
    };
    state.getMode[TECH_A] = { kind: "ok" };
    const getsBeforeRecover = state.getLog.filter((id) => id === TECH_A).length;
    await openTechnical(page, TECH_A);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsBeforeRecover + PROJECT_SESSION_GETS);
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 19:30:00 UTC");
    await expect(page.getByTestId("technical-editor-save-error")).toHaveCount(0);
    await expect(page.getByTestId("technical-editor-state-conflict")).toHaveCount(
      0,
    );

    // 再制造 409，然后重载失败保值
    state.putMode[TECH_A] = { kind: "full_conflict" };
    const putsBefore409b = state.putLog.length;
    await editTechnicalOverview(page, "再次409");
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsBefore409b + 1);
    await expect(page.getByTestId("technical-editor-state-conflict")).toBeVisible();
    const kept = await freshness.innerText();
    expect(kept).toBe("当前已载入版本：2026-07-20 19:30:00 UTC");

    state.getMode[TECH_A] = { kind: "fail", status: 500 };
    const getsBeforeReloadFail = state.getLog.filter((id) => id === TECH_A)
      .length;
    await page.getByTestId("technical-editor-state-reload").click();
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsBeforeReloadFail + 1);
    await expect(page.getByTestId("technical-editor-state-conflict")).toBeVisible();
    await expect(freshness).toHaveText(kept);
    assertCleanConsole(consoleLines);
  });

  test("技术标：A→B 立即清空；迟到 A GET/PUT 不污染 B", async ({ page }) => {
    const a = makeProject({ id: TECH_A, name: "P13B技术甲", kind: "technical" });
    const b = makeProject({ id: TECH_B, name: "P13B技术乙", kind: "technical" });
    const state = createProbeState([a, b]);
    // 迟到 A GET success 若污染，会写成 23:59:59；B 权威时间为 09:00:00
    state.editorById[TECH_A] = {
      ...emptyEditor(TECH_A, "2026-07-20T23:59:59"),
      stateVersion: seedStateVersion(30),
      analysisOverview: "甲正文",
    };
    state.editorById[TECH_B] = {
      ...emptyEditor(TECH_B, "2026-07-20T09:00:00"),
      stateVersion: seedStateVersion(31),
      analysisOverview: "乙正文",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    // —— 真实在途 A GET success：挂起 → 切入 B → 释放 A ——
    const getGateOk = createHoldGate();
    state.getMode[TECH_A] = { kind: "gate", gate: getGateOk, then: "ok" };
    const getsABefore = state.getLog.filter((id) => id === TECH_A).length;
    // 不 await 工作区就绪：A GET 在途时 freshness 可能尚未挂载
    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsABefore + PROJECT_SESSION_GETS);
    expect(getGateOk.isReleased()).toBe(false);

    const getsBBefore = state.getLog.filter((id) => id === TECH_B).length;
    await openTechnical(page, TECH_B);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_B).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBefore + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 09:00:00 UTC",
    );
    const getsBAfterReady = state.getLog.filter((id) => id === TECH_B).length;
    expect(getsBAfterReady).toBe(getsBBefore + PROJECT_SESSION_GETS);
    const putsBeforeLateGetOk = state.putLog.length;

    getGateOk.release();
    // 迟到 A GET success/finally：B 时间稳定，且 B 无额外 GET、无额外 PUT
    await expect
      .poll(async () => page.getByTestId(TECH_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前已载入版本：2026-07-20 09:00:00 UTC");
    expect(state.getLog.filter((id) => id === TECH_B).length).toBe(
      getsBAfterReady,
    );
    expect(state.putLog.length).toBe(putsBeforeLateGetOk);
    await expect(page.getByTestId("technical-editor-load-error")).toHaveCount(0);

    // —— 真实在途 A GET catch：挂起 fail → 切入 B → 释放 A ——
    const getGateFail = createHoldGate();
    state.getMode[TECH_A] = {
      kind: "gate",
      gate: getGateFail,
      then: "fail",
      status: 500,
    };
    const getsABeforeFail = state.getLog.filter((id) => id === TECH_A).length;
    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_A).length,
        { timeout: 5_000 },
      )
      .toBe(getsABeforeFail + PROJECT_SESSION_GETS);
    expect(getGateFail.isReleased()).toBe(false);

    const getsBBeforeFail = state.getLog.filter((id) => id === TECH_B).length;
    await openTechnical(page, TECH_B);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_B).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBeforeFail + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 09:00:00 UTC",
    );
    const getsBAfterFailReady = state.getLog.filter((id) => id === TECH_B)
      .length;
    expect(getsBAfterFailReady).toBe(getsBBeforeFail + PROJECT_SESSION_GETS);
    const putsBeforeLateGetFail = state.putLog.length;

    getGateFail.release();
    await expect
      .poll(async () => page.getByTestId(TECH_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前已载入版本：2026-07-20 09:00:00 UTC");
    expect(state.getLog.filter((id) => id === TECH_B).length).toBe(
      getsBAfterFailReady,
    );
    expect(state.putLog.length).toBe(putsBeforeLateGetFail);
    await expect(page.getByTestId("technical-editor-load-error")).toHaveCount(0);

    // —— 迟到 A PUT success：先稳定打开 A，挂起 PUT，再切 B 后释放 ——
    state.getMode[TECH_A] = { kind: "ok" };
    state.getMode[TECH_B] = { kind: "ok" };
    // 恢复 A 为合法基线时间，便于对照
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      updatedAt: "2026-07-20T12:34:56",
      stateVersion: seedStateVersion(32),
    };
    await openTechnical(page, TECH_A);
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(KNOWN_LABEL);

    const putGate = createHoldGate();
    state.putMode[TECH_A] = {
      kind: "gate",
      gate: putGate,
      then: "ok",
      updatedAt: "2026-07-20T23:59:59",
    };
    const putsBeforeLatePut = state.putLog.filter((p) => p.projectId === TECH_A)
      .length;
    const getsABeforeLatePut = state.getLog.filter((id) => id === TECH_A).length;
    await editTechnicalOverview(page, "甲迟到PUT");
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(putsBeforeLatePut + 1);
    // 在途期间不得重复 PUT / 额外 GET
    expect(state.putLog.filter((p) => p.projectId === TECH_A).length).toBe(
      putsBeforeLatePut + 1,
    );
    expect(state.getLog.filter((id) => id === TECH_A).length).toBe(
      getsABeforeLatePut,
    );

    const getsBBeforeLatePut = state.getLog.filter((id) => id === TECH_B).length;
    await openTechnical(page, TECH_B);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === TECH_B).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBeforeLatePut + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 09:00:00 UTC",
    );
    const getsBAfterLatePutReady = state.getLog.filter((id) => id === TECH_B)
      .length;
    expect(getsBAfterLatePutReady).toBe(
      getsBBeforeLatePut + PROJECT_SESSION_GETS,
    );

    putGate.release();
    await expect
      .poll(async () => page.getByTestId(TECH_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前已载入版本：2026-07-20 09:00:00 UTC");
    // 迟到 A PUT success 不得触发 B 额外 GET/PUT，也不得写成 23:59:59
    expect(state.getLog.filter((id) => id === TECH_B).length).toBe(
      getsBAfterLatePutReady,
    );
    expect(state.putLog.filter((p) => p.projectId === TECH_B).length).toBe(0);
    expect(state.putLog.filter((p) => p.projectId === TECH_A).length).toBe(
      putsBeforeLatePut + 1,
    );
    assertCleanConsole(consoleLines);
  });

  test("商务标：合法 UTC、成功 PUT、409 保值、A→B 隔离、零额外请求", async ({
    page,
  }) => {
    const a = makeProject({ id: BIZ_A, name: "P13B商务甲", kind: "business" });
    const b = makeProject({ id: BIZ_B, name: "P13B商务乙", kind: "business" });
    const state = createProbeState([a, b]);
    state.editorById[BIZ_A] = {
      ...emptyEditor(BIZ_A, "2026-07-20T12:34:56"),
      stateVersion: seedStateVersion(40),
      parsedMarkdown: "商务正文甲",
    };
    state.editorById[BIZ_B] = {
      ...emptyEditor(BIZ_B, "2026-07-20T08:08:08"),
      stateVersion: seedStateVersion(41),
      parsedMarkdown: "商务正文乙",
    };
    state.putMode[BIZ_A] = {
      kind: "ok",
      updatedAt: "2026-07-20T16:16:16",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    await openBusiness(page, BIZ_A);
    const freshness = page.getByTestId(BIZ_TESTID);
    await expect(freshness).toHaveText(KNOWN_LABEL);
    await assertNoForbiddenFreshnessCopy(page, BIZ_TESTID);

    const getsBefore = state.getLog.filter((id) => id === BIZ_A).length;
    const putsBefore = state.putLog.filter((p) => p.projectId === BIZ_A).length;
    await editBusinessMarkdown(page, "商务正文甲-已编辑");
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(putsBefore + 1);
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 16:16:16 UTC");
    expect(state.getLog.filter((id) => id === BIZ_A).length).toBe(getsBefore);

    // 409 保值
    state.putMode[BIZ_A] = { kind: "full_conflict" };
    await editBusinessMarkdown(page, "商务正文甲-触发409");
    await expect(page.getByTestId("business-editor-state-conflict")).toBeVisible();
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 16:16:16 UTC");

    // A→B
    const putGate = createHoldGate();
    // 先解除冲突以便再编辑？冲突阻断后不能自动 PUT；改用重载成功后再测迟到
    state.editorById[BIZ_A] = {
      ...state.editorById[BIZ_A],
      updatedAt: "2026-07-20T16:16:16",
      stateVersion: seedStateVersion(50),
    };
    state.getMode[BIZ_A] = { kind: "ok" };
    await page.getByTestId("business-editor-state-reload").click();
    await expect(page.getByTestId("business-editor-state-conflict")).toHaveCount(
      0,
    );
    await expect(freshness).toHaveText("当前已载入版本：2026-07-20 16:16:16 UTC");

    state.putMode[BIZ_A] = {
      kind: "gate",
      gate: putGate,
      then: "ok",
      updatedAt: "2026-07-20T23:00:00",
    };
    const putsBeforeLate = state.putLog.filter((p) => p.projectId === BIZ_A)
      .length;
    const getsABeforeLate = state.getLog.filter((id) => id === BIZ_A).length;
    await editBusinessMarkdown(page, "商务甲迟到PUT");
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBe(putsBeforeLate + 1);
    // 在途期间精确 +1，无重复 PUT / 额外 GET
    expect(state.putLog.filter((p) => p.projectId === BIZ_A).length).toBe(
      putsBeforeLate + 1,
    );
    expect(state.getLog.filter((id) => id === BIZ_A).length).toBe(
      getsABeforeLate,
    );

    const getsBBefore = state.getLog.filter((id) => id === BIZ_B).length;
    const putsBBefore = state.putLog.filter((p) => p.projectId === BIZ_B).length;
    await openBusiness(page, BIZ_B);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === BIZ_B).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBefore + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(BIZ_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 08:08:08 UTC",
    );
    const getsBAfterReady = state.getLog.filter((id) => id === BIZ_B).length;
    expect(getsBAfterReady).toBe(getsBBefore + PROJECT_SESSION_GETS);

    putGate.release();
    await expect
      .poll(async () => page.getByTestId(BIZ_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前已载入版本：2026-07-20 08:08:08 UTC");
    // 迟到 A PUT 不污染 B：B 无额外 GET/PUT，A 无重复 PUT
    expect(state.getLog.filter((id) => id === BIZ_B).length).toBe(
      getsBAfterReady,
    );
    expect(state.putLog.filter((p) => p.projectId === BIZ_B).length).toBe(
      putsBBefore,
    );
    expect(state.putLog.filter((p) => p.projectId === BIZ_A).length).toBe(
      putsBeforeLate + 1,
    );
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });
});

test.describe("P13-C 当前已载入版本修订来源可见性", () => {
  test("failure-first：双页面缺少来源 testid 与固定文案", async ({ page }) => {
    const tech = makeProject({
      id: "proj_e2e_p13c_tech_ff",
      name: "P13C技术红测",
      kind: "technical",
    });
    const biz = makeProject({
      id: "proj_e2e_p13c_biz_ff",
      name: "P13C商务红测",
      kind: "business",
    });
    const state = createProbeState([tech, biz]);
    state.editorById[tech.id] = {
      ...emptyEditor(tech.id, "2026-07-20T12:34:56"),
      currentRevisionSourceKind: "browser_put",
    };
    state.editorById[biz.id] = {
      ...emptyEditor(biz.id, "2026-07-20T12:34:56"),
      currentRevisionSourceKind: "browser_put",
    };
    await installRoutes(page, state);

    await openTechnical(page, tech.id);
    await expect(page.getByTestId("technical-editor-workspace")).toBeVisible();
    // 首个业务断言：固定来源 testid 必须存在
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toBeVisible();
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );

    await openBusiness(page, biz.id);
    await expect(page.getByTestId("business-editor-workspace")).toBeVisible();
    await expect(page.getByTestId(BIZ_SOURCE_TESTID)).toBeVisible();
    await expect(page.getByTestId(BIZ_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );
  });

  test("技术标：九类来源中文标签与坏值未知", async ({ page }) => {
    for (const kind of NINE_SOURCES) {
      const id = `proj_e2e_p13c_tech_${kind}`;
      const project = makeProject({
        id,
        name: `P13C-${kind}`,
        kind: "technical",
      });
      const state = createProbeState([project]);
      state.editorById[id] = {
        ...emptyEditor(id, "2026-07-20T12:34:56"),
        currentRevisionSourceKind: kind,
      };
      await installRoutes(page, state);
      await openTechnical(page, id);
      await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
        `当前版本来源：${SOURCE_LABELS[kind]}`,
      );
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }

    const badCases: Array<{
      id: string;
      source: string | null;
      omit?: boolean;
    }> = [
      { id: "proj_e2e_p13c_src_null", source: null },
      { id: "proj_e2e_p13c_src_missing", source: null, omit: true },
      { id: "proj_e2e_p13c_src_blank", source: "  " },
      { id: "proj_e2e_p13c_src_case", source: "Browser_Put" },
      { id: "proj_e2e_p13c_src_unknown", source: "not_a_source" },
    ];
    for (const c of badCases) {
      const project = makeProject({
        id: c.id,
        name: `P13C坏值-${c.id}`,
        kind: "technical",
      });
      const state = createProbeState([project]);
      const editor = {
        ...emptyEditor(c.id, "2026-07-20T12:34:56"),
        currentRevisionSourceKind: c.source,
      };
      if (c.omit) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        delete (editor as any).currentRevisionSourceKind;
      }
      state.editorById[c.id] = editor;
      const consoleLines = collectConsole(page);
      await installRoutes(page, state);
      await openTechnical(page, c.id);
      await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
        UNKNOWN_SOURCE_LABEL,
      );
      assertCleanConsole(consoleLines);
      await page.unrouteAll({ behavior: "ignoreErrors" });
    }
  });

  test("技术标：GET/PUT 合法接受来源；409 保值；零额外请求", async ({
    page,
  }) => {
    const project = makeProject({
      id: "proj_e2e_p13c_tech_put",
      name: "P13C技术PUT",
      kind: "technical",
    });
    const state = createProbeState([project]);
    state.editorById[project.id] = {
      ...emptyEditor(project.id, "2026-07-20T12:34:56"),
      stateVersion: seedStateVersion(70),
      currentRevisionSourceKind: "task",
      analysisOverview: "初始",
    };
    state.putMode[project.id] = {
      kind: "ok",
      updatedAt: "2026-07-20T15:00:00",
      currentRevisionSourceKind: "browser_put",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    await openTechnical(page, project.id);
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(KNOWN_LABEL);
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      "当前版本来源：任务写入",
    );

    const getsBefore = state.getLog.length;
    const putsBefore = state.putLog.length;
    const editorBefore = state.editorRequestLog.length;
    await editTechnicalOverview(page, "触发PUT更新来源");
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsBefore + 1);
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 15:00:00 UTC",
    );
    expect(state.getLog.length).toBe(getsBefore);
    expect(state.editorRequestLog.length).toBe(editorBefore + 1);

    // 409 保留来源与时间
    state.putMode[project.id] = { kind: "full_conflict" };
    await editTechnicalOverview(page, "触发409来源保值");
    await expect(page.getByTestId("technical-editor-state-conflict")).toBeVisible();
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );
    await expect(page.getByTestId(TECH_TESTID)).toHaveText(
      "当前已载入版本：2026-07-20 15:00:00 UTC",
    );
    assertCleanConsole(consoleLines);
  });

  test("技术标：A→B 立即清空来源；迟到 A GET/PUT 不污染 B", async ({
    page,
  }) => {
    const aId = "proj_e2e_p13c_tech_a";
    const bId = "proj_e2e_p13c_tech_b";
    const a = makeProject({ id: aId, name: "P13C技术甲", kind: "technical" });
    const b = makeProject({ id: bId, name: "P13C技术乙", kind: "technical" });
    const state = createProbeState([a, b]);
    state.editorById[aId] = {
      ...emptyEditor(aId, "2026-07-20T23:59:59"),
      stateVersion: seedStateVersion(80),
      currentRevisionSourceKind: "revise",
      analysisOverview: "甲",
    };
    state.editorById[bId] = {
      ...emptyEditor(bId, "2026-07-20T09:00:00"),
      stateVersion: seedStateVersion(81),
      currentRevisionSourceKind: "callback",
      analysisOverview: "乙",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    const getGateOk = createHoldGate();
    state.getMode[aId] = { kind: "gate", gate: getGateOk, then: "ok" };
    const getsABefore = state.getLog.filter((id) => id === aId).length;
    await page.goto(`/technical-plan/${aId}/analysis`);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === aId).length,
        { timeout: 5_000 },
      )
      .toBe(getsABefore + PROJECT_SESSION_GETS);

    const getsBBefore = state.getLog.filter((id) => id === bId).length;
    await openTechnical(page, bId);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === bId).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBefore + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      "当前版本来源：解析回传",
    );
    const getsBAfterReady = state.getLog.filter((id) => id === bId).length;

    getGateOk.release();
    await expect
      .poll(async () => page.getByTestId(TECH_SOURCE_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前版本来源：解析回传");
    expect(state.getLog.filter((id) => id === bId).length).toBe(
      getsBAfterReady,
    );

    // 稳定打开 A 后挂起 PUT，切 B 再释放
    state.getMode[aId] = { kind: "ok" };
    state.editorById[aId] = {
      ...state.editorById[aId],
      updatedAt: "2026-07-20T12:34:56",
      stateVersion: seedStateVersion(82),
      currentRevisionSourceKind: "browser_put",
    };
    await openTechnical(page, aId);
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );

    const putGate = createHoldGate();
    state.putMode[aId] = {
      kind: "gate",
      gate: putGate,
      then: "ok",
      updatedAt: "2026-07-20T23:59:59",
      currentRevisionSourceKind: "revise",
    };
    const putsBefore = state.putLog.filter((p) => p.projectId === aId).length;
    await editTechnicalOverview(page, "甲迟到PUT来源");
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === aId).length,
        { timeout: 10_000 },
      )
      .toBe(putsBefore + 1);

    const getsBBeforeLate = state.getLog.filter((id) => id === bId).length;
    await openTechnical(page, bId);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === bId).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBeforeLate + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(TECH_SOURCE_TESTID)).toHaveText(
      "当前版本来源：解析回传",
    );
    const getsBAfterLate = state.getLog.filter((id) => id === bId).length;

    putGate.release();
    await expect
      .poll(async () => page.getByTestId(TECH_SOURCE_TESTID).innerText(), {
        timeout: 3_000,
      })
      .toBe("当前版本来源：解析回传");
    expect(state.getLog.filter((id) => id === bId).length).toBe(getsBAfterLate);
    expect(state.putLog.filter((p) => p.projectId === bId).length).toBe(0);
    assertCleanConsole(consoleLines);
  });

  test("商务标：来源展示、PUT 更新、A→B 隔离、零额外请求", async ({ page }) => {
    const aId = "proj_e2e_p13c_biz_a";
    const bId = "proj_e2e_p13c_biz_b";
    const a = makeProject({ id: aId, name: "P13C商务甲", kind: "business" });
    const b = makeProject({ id: bId, name: "P13C商务乙", kind: "business" });
    const state = createProbeState([a, b]);
    state.editorById[aId] = {
      ...emptyEditor(aId, "2026-07-20T12:34:56"),
      stateVersion: seedStateVersion(90),
      currentRevisionSourceKind: "local_parser",
      parsedMarkdown: "商务甲",
    };
    state.editorById[bId] = {
      ...emptyEditor(bId, "2026-07-20T08:08:08"),
      stateVersion: seedStateVersion(91),
      currentRevisionSourceKind: "content_fuse_apply",
      parsedMarkdown: "商务乙",
    };
    state.putMode[aId] = {
      kind: "ok",
      updatedAt: "2026-07-20T16:16:16",
      currentRevisionSourceKind: "browser_put",
    };
    const consoleLines = collectConsole(page);
    await installRoutes(page, state);

    await openBusiness(page, aId);
    await expect(page.getByTestId(BIZ_SOURCE_TESTID)).toHaveText(
      "当前版本来源：本地解析",
    );
    const getsBefore = state.getLog.filter((id) => id === aId).length;
    const putsBefore = state.putLog.filter((p) => p.projectId === aId).length;
    await editBusinessMarkdown(page, "商务甲-已编辑来源");
    await expect
      .poll(
        () => state.putLog.filter((p) => p.projectId === aId).length,
        { timeout: 10_000 },
      )
      .toBe(putsBefore + 1);
    await expect(page.getByTestId(BIZ_SOURCE_TESTID)).toHaveText(
      KNOWN_SOURCE_LABEL,
    );
    expect(state.getLog.filter((id) => id === aId).length).toBe(getsBefore);

    const getsBBefore = state.getLog.filter((id) => id === bId).length;
    await openBusiness(page, bId);
    await expect
      .poll(
        () => state.getLog.filter((id) => id === bId).length,
        { timeout: 5_000 },
      )
      .toBe(getsBBefore + PROJECT_SESSION_GETS);
    await expect(page.getByTestId(BIZ_SOURCE_TESTID)).toHaveText(
      "当前版本来源：内容融合应用",
    );
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });
});
