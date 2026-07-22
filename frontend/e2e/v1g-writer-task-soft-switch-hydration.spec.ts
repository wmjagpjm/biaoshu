/**
 * 模块：V1-G 任务迟到 success 软切编辑态水合围栏 failure-first E2E
 * 用途：A 项目 writer 任务仍在 pending/running 时软切 B；B 初始 editor-state 就绪后
 *       再释放 A 的 success。未修生产时，旧闭包会触发额外 editor-state GET 或粘住 B loading。
 *       同项目对照证明合法 success 仍精确 +1 GET 水合。
 * 对接：契约 docs/v1g-writer-task-success-refresh-fence-contract.md §6；
 *       技术 parse/analyze/outline/chapters/chapter；商务 biz_qualify；
 *       Playwright chromium --workers=1 --retries=0；route 桩 + HoldGate。
 * 二次开发：禁止固定 waitForTimeout/setTimeout/sleep 作完成证据、skip/fixme/only、
 *       宽泛非零计数、源码扫描、真实外网/业务库/uploads/密钥。
 * 可控终态：POST 返回 running → per-task SSE 立即可用失败以回退轮询 →
 *           任务详情 GET 用 HoldGate 挂起；B 就绪后再把任务标为 success 并 release。
 * 加固：T9 技术 analyze ABA（A→B→A）锁 generation；waitTaskRunningHeld 对
 *       当前 projectId+type 的 taskPosts 精确计数 1（禁止重复 POST 假绿）。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_v1g_tech_a";
const TECH_B = "proj_e2e_v1g_tech_b";
const BIZ_A = "proj_e2e_v1g_biz_a";
const BIZ_B = "proj_e2e_v1g_biz_b";

const OVERVIEW_A = "V1G_TECH_A_OVERVIEW_权威概述甲";
const OVERVIEW_B = "V1G_TECH_B_OVERVIEW_权威概述乙";
const MARKDOWN_A = "V1G_BIZ_A_PARSED_MARKDOWN_权威正文甲";
const MARKDOWN_B = "V1G_BIZ_B_PARSED_MARKDOWN_权威正文乙";
const QUALIFY_A = "V1G_BIZ_A_QUALIFY_资格要求甲";
const QUALIFY_B = "V1G_BIZ_B_QUALIFY_资格要求乙";
const CHAPTER_TITLE = "V1G服务端一级目录";
const CHAPTER_BODY_A = "V1G_CHAPTER_BODY_A";
const CHAPTER_BODY_B = "V1G_CHAPTER_BODY_B";
const PARSE_MD_A = "V1G_TECH_A_PARSED_MD";
const PARSE_MD_B = "V1G_TECH_B_PARSED_MD";

const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;
const ZERO_STABLE_MS = 400;
const SECRET = "SECRET_V1G_LEAK_SHOULD_NOT_RENDER";

type Kind = "technical" | "business";

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  isReleased: () => boolean;
  waiterCount: () => number;
};

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
  outline: Array<Record<string, unknown>>;
  chapters: Array<Record<string, unknown>>;
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
  businessQualify: Array<Record<string, unknown>>;
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  stateVersion: string;
  updatedAt: string | null;
};

type TaskRecord = {
  id: string;
  projectId: string;
  type: string;
  status: string;
  progress: number;
  message: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
  payload?: Record<string, unknown> | null;
};

type TaskPost = {
  projectId: string;
  type: string;
  payload?: Record<string, unknown> | null;
  taskId: string;
};

type ProbeState = {
  projects: ProjectStub[];
  editorById: Record<string, EditorState>;
  getLog: string[];
  putLog: string[];
  patchLog: Array<{ projectId: string; body: Record<string, unknown> }>;
  taskPosts: TaskPost[];
  taskDetailLog: string[];
  filesLog: string[];
  forbiddenHits: string[];
  externalHits: string[];
  activeTasks: Record<string, TaskRecord>;
  /** 任务详情 GET 挂起：release 前阻塞，用于可控释放终态 */
  taskDetailGate: Record<string, HoldGate>;
  taskSeq: number;
  versionSeq: number;
  /**
   * M3：可注入权威 parseStrategy（light|managed|local|ask）。
   * 默认 light，保持既有用例语义。
   */
  parseStrategy: "light" | "managed" | "local" | "ask";
};

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
      while (waiters.length > 0) waiters.shift()?.();
    },
    isReleased: () => released,
    waiterCount: () => waiters.length,
  };
}

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedTaskId(n: number): string {
  return `task_v1g_${n.toString(16).padStart(12, "0")}`;
}

function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
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
    headers: { "Cache-Control": "no-store" },
    body: JSON.stringify(body),
  });
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "政务",
    status: partial.status ?? "draft",
    updatedAt: partial.updatedAt ?? "2026-07-22T12:00:00.000Z",
    technicalPlanStep: partial.technicalPlanStep ?? 1,
    wordCount: partial.wordCount ?? 0,
    linkedProjectId: partial.linkedProjectId ?? null,
    id: partial.id,
    name: partial.name,
    kind: partial.kind,
  };
}

function baseEditor(projectId: string, kind: Kind, version: string): EditorState {
  const isTech = kind === "technical";
  return {
    projectId,
    outline: isTech
      ? [
          {
            id: "n1",
            title: CHAPTER_TITLE,
            level: 1,
            targetWords: 800,
            description: "",
            children: [],
          },
        ]
      : [],
    chapters: isTech
      ? [
          {
            id: "n1",
            title: CHAPTER_TITLE,
            body: projectId === TECH_A ? CHAPTER_BODY_A : CHAPTER_BODY_B,
            preview: projectId === TECH_A ? CHAPTER_BODY_A : CHAPTER_BODY_B,
            wordCount: 8,
            status: "done",
          },
        ]
      : [],
    facts: [],
    mode: isTech ? "ALIGNED" : "business",
    analysisOverview: projectId === TECH_A ? OVERVIEW_A : OVERVIEW_B,
    analysis: {
      overview: projectId === TECH_A ? OVERVIEW_A : OVERVIEW_B,
      techRequirements: ["V1G技术要求"],
      rejectionRisks: [],
      scoringPoints: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    parsedMarkdown: isTech
      ? projectId === TECH_A
        ? PARSE_MD_A
        : PARSE_MD_B
      : projectId === BIZ_A
        ? MARKDOWN_A
        : MARKDOWN_B,
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    businessQualify: isTech
      ? []
      : [
          {
            id: "q1",
            requirement:
              projectId === BIZ_A ? QUALIFY_A : QUALIFY_B,
            status: "missing",
            evidence: "",
            notes: "",
          },
        ],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    stateVersion: version,
    updatedAt: "2026-07-22T12:00:00.000Z",
  };
}

function createProbeState(
  projects: ProjectStub[],
  opts?: { parseStrategy?: ProbeState["parseStrategy"] },
): ProbeState {
  const editorById: Record<string, EditorState> = {};
  let versionSeq = 0;
  for (const p of projects) {
    versionSeq += 1;
    editorById[p.id] = baseEditor(p.id, p.kind, seedStateVersion(versionSeq));
  }
  return {
    projects,
    editorById,
    getLog: [],
    putLog: [],
    patchLog: [],
    taskPosts: [],
    taskDetailLog: [],
    filesLog: [],
    forbiddenHits: [],
    externalHits: [],
    activeTasks: {},
    taskDetailGate: {},
    taskSeq: 0,
    versionSeq,
    parseStrategy: opts?.parseStrategy ?? "light",
  };
}

function knownProjectIdSet(state: ProbeState): Set<string> {
  return new Set(state.projects.map((p) => p.id));
}

function isAllowedApi(
  method: string,
  path: string,
  known: ReadonlySet<string>,
): boolean {
  const staticRules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspaces(\/|$)/ },
    { methods: ["GET", "PUT"], path: /^\/api\/settings(\/|$)/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET"], path: /^\/api\/knowledge(\/|$)/ },
    { methods: ["GET"], path: /^\/api\/templates\/?$/ },
    { methods: ["GET"], path: /^\/api\/cards\/?$/ },
    { methods: ["GET"], path: /^\/api\/hr\/team-recommendations\/?$/ },
  ];
  if (staticRules.some((r) => r.methods.includes(method) && r.path.test(path))) {
    return true;
  }
  const projectMatch = path.match(/^\/api\/projects\/([^/]+)(\/.*)?$/);
  if (!projectMatch) return false;
  const projectId = decodeURIComponent(projectMatch[1]);
  if (!known.has(projectId)) return false;
  const rest = projectMatch[2] || "";
  const projectRules: Array<{ methods: string[]; rest: RegExp }> = [
    { methods: ["GET", "PATCH"], rest: /^\/?$/ },
    { methods: ["GET", "PUT"], rest: /^\/editor-state\/?$/ },
    { methods: ["GET"], rest: /^\/editor-state-events\/stream\/?$/ },
    { methods: ["GET"], rest: /^\/task-events\/stream\/?$/ },
    { methods: ["GET", "POST"], rest: /^\/(files|tasks|images)\/?$/ },
    {
      methods: ["GET", "POST"],
      rest: /^\/tasks\/[^/]+(\/(events|cancel|status))?\/?$/,
    },
    { methods: ["POST"], rest: /^\/presence\/(heartbeat|leave)\/?$/ },
    {
      methods: ["POST"],
      rest: /^\/chapter-edit-intents\/(heartbeat|leave)\/?$/,
    },
  ];
  return projectRules.some(
    (r) => r.methods.includes(method) && r.rest.test(rest),
  );
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

    const known = knownProjectIdSet(state);

    // 所有 SSE：立即不可用。per-task 失败迫使 runTask 回退详情 GET + HoldGate 可控终态。
    if (
      method === "GET" &&
      (/\/task-events\/stream\/?$/.test(path) ||
        /\/editor-state-events\/stream\/?$/.test(path) ||
        /\/tasks\/[^/]+\/events\/?$/.test(path))
    ) {
      await route.fulfill({
        status: 503,
        contentType: "text/plain",
        body: "v1g-sse-unavailable",
      });
      return;
    }

    if (!isAllowedApi(method, path, known)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "v1g_forbidden", message: SECRET } },
        403,
      );
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
        csrfToken: "e2e-v1g-csrf",
      });
      return;
    }
    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-v1g-csrf" });
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
    // M3：权威策略脱敏 GET 与完整 settings 分离
    if (path === "/api/settings/parse-strategy" && method === "GET") {
      await json(route, { parseStrategy: state.parseStrategy });
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
        parseStrategy: state.parseStrategy,
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
      await json(route, { detail: { code: "v1g_no_create" } }, 403);
      return;
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = decodeURIComponent(detailMatch[1]);
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(route, { detail: { code: "project_not_found" } }, 404);
        return;
      }
      if (method === "PATCH") {
        const body = (req.postDataJSON() as Record<string, unknown>) || {};
        state.patchLog.push({ projectId: id, body });
        if (typeof body.technicalPlanStep === "number") {
          found.technicalPlanStep = body.technicalPlanStep;
        }
        if (typeof body.name === "string") {
          found.name = body.name;
        }
      }
      await json(route, found);
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      const id = decodeURIComponent(editorMatch[1]);
      if (method === "GET") {
        state.getLog.push(id);
        const ed = state.editorById[id];
        if (!ed) {
          await json(route, { detail: { code: "not_found" } }, 404);
          return;
        }
        await json(route, ed);
        return;
      }
      state.putLog.push(id);
      const prev = state.editorById[id];
      state.versionSeq += 1;
      const nextVersion = seedStateVersion(state.versionSeq);
      if (prev && isValidStateVersion(prev.stateVersion)) {
        prev.stateVersion = nextVersion;
      }
      await json(route, state.editorById[id] ?? { projectId: id, stateVersion: nextVersion });
      return;
    }

    const filesMatch = path.match(/^\/api\/projects\/([^/]+)\/files\/?$/);
    if (filesMatch && method === "GET") {
      const pid = decodeURIComponent(filesMatch[1]);
      state.filesLog.push(pid);
      // 假文件：解除技术/商务「轻量解析」disabled
      await json(route, [
        {
          id: `file_${pid}`,
          filename: "sample-bid.pdf",
          sizeBytes: 2048,
          createdAt: "2026-07-22T12:00:00.000Z",
        },
      ]);
      return;
    }
    if (filesMatch && method === "POST") {
      await json(route, {
        id: "file_upload_stub",
        filename: "upload.pdf",
        sizeBytes: 100,
      });
      return;
    }

    // 任务创建 / 列表 / 详情 / status / cancel
    const taskMatch = path.match(
      /^\/api\/projects\/([^/]+)\/tasks(?:\/([^/]+))?(?:\/(events|cancel|status))?\/?$/,
    );
    if (taskMatch && (method === "GET" || method === "POST")) {
      const pid = decodeURIComponent(taskMatch[1]);
      const tid = taskMatch[2] ? decodeURIComponent(taskMatch[2]) : "";
      const sub = taskMatch[3] || "";

      if (method === "POST" && !tid) {
        const body = (req.postDataJSON() as {
          type?: string;
          payload?: Record<string, unknown>;
        }) || {};
        state.taskSeq += 1;
        const id = seedTaskId(state.taskSeq);
        const row: TaskRecord = {
          id,
          projectId: pid,
          type: body.type || "parse",
          status: "running",
          progress: 18,
          message: `V1G_${body.type || "parse"}_RUNNING`,
          result: null,
          error: null,
          payload: body.payload ?? null,
        };
        state.activeTasks[id] = row;
        state.taskPosts.push({
          projectId: pid,
          type: row.type,
          payload: row.payload,
          taskId: id,
        });
        // 每个任务默认挂起详情 GET，直到测试显式 release
        state.taskDetailGate[id] = createHoldGate();
        await json(route, row, 201);
        return;
      }

      if (method === "GET" && !tid) {
        await json(route, Object.values(state.activeTasks));
        return;
      }

      if (method === "GET" && tid && sub === "status") {
        const row = state.activeTasks[tid];
        await json(route, {
          taskId: tid,
          status: row?.status ?? "running",
          progress: row?.progress ?? 0,
        });
        return;
      }

      if (method === "POST" && sub === "cancel") {
        const row = state.activeTasks[tid];
        if (row) {
          row.status = "cancelled";
          row.progress = 0;
          row.message = "cancelled";
        }
        await json(route, row ?? { id: tid, status: "cancelled" });
        return;
      }

      if (method === "GET" && tid && !sub) {
        state.taskDetailLog.push(`${pid}/${tid}`);
        const gate = state.taskDetailGate[tid];
        if (gate && !gate.isReleased()) {
          await gate.wait();
        }
        const row = state.activeTasks[tid] ?? {
          id: tid,
          projectId: pid,
          type: "parse",
          status: "running",
          progress: 0,
          message: "missing",
        };
        await json(route, row);
        return;
      }

      await json(route, { detail: SECRET }, 404);
      return;
    }

    if (
      method === "POST" &&
      (/\/presence\/(heartbeat|leave)\/?$/.test(path) ||
        /\/chapter-edit-intents\/(heartbeat|leave)\/?$/.test(path))
    ) {
      if (path.includes("/leave")) {
        await route.fulfill({
          status: 204,
          headers: { "Cache-Control": "no-store" },
          body: "",
        });
        return;
      }
      if (path.includes("chapter-edit-intents")) {
        await json(route, {
          leaseExpiresAt: "2026-07-22T12:35:41.000Z",
          refreshAfterSeconds: 15,
        });
        return;
      }
      await json(route, {
        leaseExpiresAt: "2026-07-22T12:35:41.000Z",
        refreshAfterSeconds: 15,
        members: [{ username: "e2e", isSelf: true }],
        truncated: false,
      });
      return;
    }

    if (method === "GET") {
      await json(route, []);
      return;
    }
    state.forbiddenHits.push(`${method} ${path}`);
    await json(route, { detail: { code: "v1g_unhandled", message: SECRET } }, 404);
  });
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

async function waitStableExactCount(
  getCount: () => number,
  expected: number,
  windowMs: number,
  timeoutMs = 15_000,
) {
  await expect.poll(() => getCount(), { timeout: timeoutMs }).toBe(expected);
  const stableStart = Date.now();
  await expect
    .poll(
      () => {
        const n = getCount();
        if (n !== expected) return -1;
        return Date.now() - stableStart >= windowMs ? 1 : 0;
      },
      { timeout: Math.max(timeoutMs, windowMs + 5_000) },
    )
    .toBe(1);
  expect(getCount()).toBe(expected);
}

async function softNavigate(page: Page, url: string) {
  await page.evaluate((nextUrl) => {
    window.history.pushState({}, "", nextUrl);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, url);
}

async function expectTechReady(page: Page, name: string) {
  await expect(page.getByTestId("technical-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
}

async function expectBizReady(page: Page, name: string) {
  await expect(page.getByTestId("business-editor-workspace")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
}

function countGets(state: ProbeState, projectId: string): number {
  return state.getLog.filter((id) => id === projectId).length;
}

/**
 * 用途：启动 writer 任务后等到详情 GET 进入 HoldGate，证明仍在 pending/running。
 */
async function waitTaskRunningHeld(
  state: ProbeState,
  projectId: string,
  type: string,
): Promise<TaskPost> {
  // 精确 1：禁止重复 POST 仍因 >=1 假绿；waiterCount 仍允许 >=1（轮询重试）
  await expect
    .poll(
      () =>
        state.taskPosts.filter(
          (t) => t.projectId === projectId && t.type === type,
        ).length,
      { timeout: 15_000 },
    )
    .toBe(1);
  const post = [...state.taskPosts]
    .reverse()
    .find((t) => t.projectId === projectId && t.type === type);
  expect(post, `须创建 ${type} 任务`).toBeTruthy();
  const taskId = post!.taskId;
  const row = state.activeTasks[taskId];
  expect(row.status === "pending" || row.status === "running").toBe(true);
  await expect
    .poll(() => state.taskDetailGate[taskId]?.waiterCount() ?? 0, {
      timeout: 15_000,
    })
    .toBeGreaterThanOrEqual(1);
  return post!;
}

/**
 * 用途：B 就绪后释放 A 任务 success；返回释放前 A/B getLog 基线。
 * M3：可选 result 精确注入（managed 成功须 engine/fileCount/chars 三键）。
 */
async function releaseTaskSuccess(
  state: ProbeState,
  taskId: string,
  message: string,
  result?: Record<string, unknown> | null,
): Promise<{ getsA: number; getsB: number; getsAll: number }> {
  const row = state.activeTasks[taskId];
  expect(row, "任务必须存在").toBeTruthy();
  const getsA = countGets(state, row.projectId);
  // 若任务属 A，同时记录可能的 B；调用方应在软切后调用
  const otherIds = state.projects.map((p) => p.id).filter((id) => id !== row.projectId);
  const getsB = otherIds.length ? countGets(state, otherIds[0]) : 0;
  const getsAll = state.getLog.length;

  row.status = "success";
  row.progress = 100;
  row.message = message;
  row.result =
    result !== undefined
      ? result
      : {
          generated: 1,
          note: "v1g-success",
        };
  const gate = state.taskDetailGate[taskId];
  expect(gate, "详情 HoldGate 必须存在").toBeTruthy();
  gate.release();
  return { getsA, getsB, getsAll };
}

/** M3 managed 成功 result 精确三键。 */
const MANAGED_SUCCESS_RESULT = {
  engine: "managed",
  fileCount: 1,
  chars: 128,
} as const;

/**
 * 用途：软切红测核心断言——释放后 A/B editor-state GET 增量均为 0，B 无 sticky loading。
 * 当前生产预期失败：旧 success 触发额外 GET 或 B loading 粘住。
 */
async function assertSoftSwitchNoLateHydration(opts: {
  page: Page;
  state: ProbeState;
  projectA: string;
  projectB: string;
  baselineA: number;
  baselineB: number;
  bName: string;
  kind: Kind;
  forbidTexts: string[];
}) {
  const {
    page,
    state,
    projectA,
    projectB,
    baselineA,
    baselineB,
    bName,
    kind,
    forbidTexts,
  } = opts;

  // 先给迟到 success 副作用一个可观测窗，再精确断言（禁止 sleep 作完成证据）
  await expect
    .poll(
      () => ({
        a: countGets(state, projectA),
        b: countGets(state, projectB),
      }),
      { timeout: 8_000 },
    )
    .toEqual({ a: baselineA, b: baselineB });

  // 稳定窗：网络层不得再增 A/B GET（failure-first 首红优先落 Layer-1）
  await waitStableExactCount(
    () => countGets(state, projectA),
    baselineA,
    ZERO_STABLE_MS,
    8_000,
  );
  await waitStableExactCount(
    () => countGets(state, projectB),
    baselineB,
    ZERO_STABLE_MS,
    8_000,
  );

  if (kind === "technical") {
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, bName);
  } else {
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, bName);
  }

  for (const t of forbidTexts) {
    await expect(page.getByText(t)).toHaveCount(0);
  }

  expect(state.externalHits, "禁止外网请求").toEqual([]);
}

// 禁止 serial：首红不得 skip 后续用例；加固后参考 7 failed / 2 passed（实际为准）

test.describe("V1-G writer 任务迟到 success 软切水合", () => {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => undefined);
  });

  test("T1 技术 parse：A running 软切 B 后释放 success → 零额外 GET/零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB]);
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G技术甲");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    const getsAInit = countGets(state, TECH_A);
    await page.getByRole("button", { name: "轻量解析" }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    expect(post.payload).toEqual({ engine: "lightweight" });

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    await expectTechReady(page, "V1G技术乙");
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();
    await waitStableExactCount(
      () => countGets(state, TECH_B),
      countGets(state, TECH_B),
      ZERO_STABLE_MS,
    );

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(getsAInit);
    expect(baselineB).toBeGreaterThanOrEqual(1);

    await releaseTaskSuccess(state, post.taskId, "解析完成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙",
      kind: "technical",
      forbidTexts: [PARSE_MD_A, "解析完成，请查看右侧预览"],
    });
  });

  test("T2 技术 analyze：真实点击 AI 招标分析；type 精确 analyze", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲分析",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙分析",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲分析");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");
    expect(post.type).toBe("analyze");

    await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
    await expectTechReady(page, "V1G技术乙分析");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(state, post.taskId, "招标分析已写入结构化结果");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙分析",
      kind: "technical",
      forbidTexts: [OVERVIEW_A, "招标分析已写入结构化结果"],
    });
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );
  });

  test("T3 技术 outline：真实点击 AI 生成大纲；type 精确 outline", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲大纲",
      kind: "technical",
      technicalPlanStep: 3,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙大纲",
      kind: "technical",
      technicalPlanStep: 3,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/outline`);
    await expectTechReady(page, "V1G技术甲大纲");

    await page.getByRole("button", { name: /AI 生成大纲/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "outline");
    expect(post.type).toBe("outline");

    await softNavigate(page, `/technical-plan/${TECH_B}/outline`);
    await expectTechReady(page, "V1G技术乙大纲");

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(state, post.taskId, "大纲与章节列表已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙大纲",
      kind: "technical",
      forbidTexts: ["大纲与章节列表已生成"],
    });
  });

  test("T4 技术 chapters：真实点击生成全部空章节；onlyEmpty + type 精确", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲全书",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙全书",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "V1G技术甲全书");

    await page.getByRole("button", { name: /生成全部空章节/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "chapters");
    expect(post.type).toBe("chapters");
    expect(post.payload).toEqual({ onlyEmpty: true });

    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "V1G技术乙全书");
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(state, post.taskId, "全书空章生成完成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙全书",
      kind: "technical",
      forbidTexts: [CHAPTER_BODY_A, "全书空章生成完成"],
    });
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);
  });

  test("T5 技术 chapter：真实选章并 AI 生成本章；chapterId 精确", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲单章",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙单章",
      kind: "technical",
      technicalPlanStep: 5,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/content`);
    await expectTechReady(page, "V1G技术甲单章");
    // 选中章节（侧栏标题）
    await page.getByText(CHAPTER_TITLE, { exact: true }).first().click();
    const genBtn = page.getByRole("button", { name: /AI 生成本章/ });
    await expect(genBtn).toBeEnabled({ timeout: 10_000 });
    await genBtn.click();

    const post = await waitTaskRunningHeld(state, TECH_A, "chapter");
    expect(post.type).toBe("chapter");
    expect(post.payload).toEqual({ chapterId: "n1" });

    await softNavigate(page, `/technical-plan/${TECH_B}/content`);
    await expectTechReady(page, "V1G技术乙单章");
    await expect(
      page.getByRole("textbox", { name: `正文：${CHAPTER_TITLE}` }),
    ).toHaveValue(CHAPTER_BODY_B);

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    await releaseTaskSuccess(state, post.taskId, "章节已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术乙单章",
      kind: "technical",
      forbidTexts: [CHAPTER_BODY_A, "章节已生成"],
    });
  });

  test("T6 商务 biz_qualify：软切后零额外 GET、零 step/project 污染、零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G商务甲",
      kind: "business",
      technicalPlanStep: 1,
    });
    const projectB = makeProject({
      id: BIZ_B,
      name: "V1G商务乙",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/qualify`);
    await expectBizReady(page, "V1G商务甲");
    await expect(page.getByText(QUALIFY_A)).toBeVisible();

    await page.getByRole("button", { name: "生成资格草稿" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "biz_qualify");
    expect(post.type).toBe("biz_qualify");

    const patchBefore = state.patchLog.length;
    await softNavigate(page, `/business-bid/${BIZ_B}/qualify`);
    await expectBizReady(page, "V1G商务乙");
    await expect(page.getByText(QUALIFY_B)).toBeVisible();

    const baselineA = countGets(state, BIZ_A);
    const baselineB = countGets(state, BIZ_B);
    const stepBBefore = projectB.technicalPlanStep;

    await releaseTaskSuccess(state, post.taskId, "资格草稿已生成");

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: BIZ_A,
      projectB: BIZ_B,
      baselineA,
      baselineB,
      bName: "V1G商务乙",
      kind: "business",
      forbidTexts: [QUALIFY_A, MARKDOWN_A],
    });
    await expect(page.getByText(QUALIFY_B)).toBeVisible();

    // 不得用 A 的 step PATCH 污染 B 项目对象
    const patchesAfter = state.patchLog.slice(patchBefore);
    for (const p of patchesAfter) {
      expect(p.projectId, "迟到 success 不得 PATCH 到任意项目步进").not.toBe(
        BIZ_A,
      );
      expect(p.projectId).not.toBe(BIZ_B);
    }
    expect(projectB.technicalPlanStep).toBe(stepBBefore);
    await expect(page.getByRole("heading", { name: "V1G商务乙" })).toBeVisible();
  });

  test("T7 同项目技术 analyze success：editor-state GET 精确 +1 并水合", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G同项目技术",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA]);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G同项目技术");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");

    // 任务仍 running：GET 不得提前增加（相对进入 gate 后的稳定基线）
    const baseline = countGets(state, TECH_A);
    await waitStableExactCount(
      () => countGets(state, TECH_A),
      baseline,
      ZERO_STABLE_MS,
    );

    // 释放前更新服务端权威正文，成功水合应看到新概述
    const HYDRATED = "V1G_SAME_PROJECT_HYDRATED_OVERVIEW";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      analysisOverview: HYDRATED,
      analysis: {
        ...state.editorById[TECH_A].analysis,
        overview: HYDRATED,
      },
    };

    await releaseTaskSuccess(state, post.taskId, "招标分析已写入结构化结果");

    await expect
      .poll(() => countGets(state, TECH_A), { timeout: 15_000 })
      .toBe(baseline + 1);
    await waitStableExactCount(
      () => countGets(state, TECH_A),
      baseline + 1,
      ZERO_STABLE_MS,
    );

    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      HYDRATED,
    );
    await expect(
      page.getByRole("status").filter({ hasText: "招标分析已写入结构化结果" }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, "V1G同项目技术");
  });

  test("T8 同项目商务 biz_qualify success：editor-state GET 精确 +1 并保持步进", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G同项目商务",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA]);
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/qualify`);
    await expectBizReady(page, "V1G同项目商务");
    await expect(page.getByText(QUALIFY_A)).toBeVisible();

    await page.getByRole("button", { name: "生成资格草稿" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "biz_qualify");

    const baseline = countGets(state, BIZ_A);
    await waitStableExactCount(
      () => countGets(state, BIZ_A),
      baseline,
      ZERO_STABLE_MS,
    );

    const HYDRATED_Q = "V1G_SAME_BIZ_HYDRATED_QUALIFY";
    state.editorById[BIZ_A] = {
      ...state.editorById[BIZ_A],
      businessQualify: [
        {
          id: "q1",
          requirement: HYDRATED_Q,
          status: "matched",
          evidence: "e2e",
          notes: "",
        },
      ],
    };

    const patchBefore = state.patchLog.length;
    await releaseTaskSuccess(state, post.taskId, "资格草稿已生成");

    await expect
      .poll(() => countGets(state, BIZ_A), { timeout: 15_000 })
      .toBe(baseline + 1);
    await waitStableExactCount(
      () => countGets(state, BIZ_A),
      baseline + 1,
      ZERO_STABLE_MS,
    );

    await expect(page.getByText(HYDRATED_Q)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, "V1G同项目商务");

    // 同项目 success 允许既有 STEP_BY_TASK 步进（biz_qualify → 2）
    await expect
      .poll(
        () =>
          state.patchLog
            .slice(patchBefore)
            .filter((p) => p.projectId === BIZ_A).length,
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(1);
    const stepPatch = state.patchLog
      .slice(patchBefore)
      .find(
        (p) =>
          p.projectId === BIZ_A &&
          typeof p.body.technicalPlanStep === "number",
      );
    expect(stepPatch?.body.technicalPlanStep).toBe(2);
    expect(projectA.technicalPlanStep).toBe(2);
  });

  test("T9 技术 analyze ABA：A running→B 就绪→回 A 新会话就绪后释放旧 success → GET+0/零 tip/零 sticky", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G技术甲ABA",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G技术乙ABA",
      kind: "technical",
      technicalPlanStep: 2,
    });
    const state = createProbeState([projectA, projectB]);
    await installRoutes(page, state);

    // 1) A：真实点击 analyze，任务进入 HoldGate（精确单 POST）
    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );

    await page.getByRole("button", { name: /AI 招标分析/ }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "analyze");
    expect(post.type).toBe("analyze");
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);

    // 2) 软切 B：完整就绪且可编辑
    await softNavigate(page, `/technical-plan/${TECH_B}/analysis`);
    await expectTechReady(page, "V1G技术乙ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_B,
    );
    await waitStableExactCount(
      () => countGets(state, TECH_B),
      countGets(state, TECH_B),
      ZERO_STABLE_MS,
    );

    // 3) 软切回 A：新会话初始 GET 完成且可编辑（旧 task 仍 held）
    await softNavigate(page, `/technical-plan/${TECH_A}/analysis`);
    await expectTechReady(page, "V1G技术甲ABA");
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );
    await waitStableExactCount(
      () => countGets(state, TECH_A),
      countGets(state, TECH_A),
      ZERO_STABLE_MS,
    );

    // 若旧 success 仍触发 reload，会拉到毒化正文 → 证明覆盖新会话
    const POISON = "V1G_ABA_POISON_OLD_TASK_OVERVIEW";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      analysisOverview: POISON,
      analysis: {
        ...state.editorById[TECH_A].analysis,
        overview: POISON,
      },
    };

    // 4) 记录 A/B 基线后释放「旧」task success
    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(2);
    expect(baselineB).toBeGreaterThanOrEqual(1);
    // 旧任务仍挂起，未新增 analyze POST
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);
    expect(state.activeTasks[post.taskId]?.status).toMatch(/pending|running/);

    await releaseTaskSuccess(state, post.taskId, "招标分析已写入结构化结果");

    // 5) A/B GET 增量精确 0；零 tip/零 sticky；新会话正文不被旧刷新覆盖
    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G技术甲ABA",
      kind: "technical",
      forbidTexts: [
        POISON,
        OVERVIEW_B,
        "招标分析已写入结构化结果",
      ],
    });
    await expect(page.getByTestId("technical-analysis-overview")).toHaveValue(
      OVERVIEW_A,
    );
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    // 全程仅一次 analyze POST（精确单 POST 锁）
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "analyze",
      ).length,
    ).toBe(1);
  });

  // ---------- M3：managed 成功水合与软切（复用既有 HoldGate，禁止复制桩） ----------

  test("M3 同项目技术 managed success：payload 精确、result 三键、editor-state GET +1 水合正文", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G M3 同项目技术 managed",
      kind: "technical",
    });
    const state = createProbeState([projectA], { parseStrategy: "managed" });
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G M3 同项目技术 managed");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    // M3 技术入口 exact「开始解析」（禁止宽正则旧 UI 冒充）
    await page.getByRole("button", { name: "开始解析", exact: true }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    // 精确 payload；禁止 lightweight 冒充
    expect(post.payload).toEqual({ engine: "managed" });
    expect(post.type).toBe("parse");
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "parse",
      ).length,
    ).toBe(1);

    const baseline = countGets(state, TECH_A);
    await waitStableExactCount(
      () => countGets(state, TECH_A),
      baseline,
      ZERO_STABLE_MS,
    );

    const HYDRATED_MD = "V1G_M3_MANAGED_SAME_PROJECT_HYDRATED_MD";
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      parsedMarkdown: HYDRATED_MD,
    };

    await releaseTaskSuccess(
      state,
      post.taskId,
      "解析完成，请查看右侧预览",
      { ...MANAGED_SUCCESS_RESULT },
    );

    // result 精确三键（释放后任务详情可读）
    const done = state.activeTasks[post.taskId];
    expect(done.result).toEqual({
      engine: "managed",
      fileCount: 1,
      chars: 128,
    });
    expect(Object.keys(done.result || {}).sort()).toEqual(
      ["chars", "engine", "fileCount"].sort(),
    );

    await expect
      .poll(() => countGets(state, TECH_A), { timeout: 15_000 })
      .toBe(baseline + 1);
    await waitStableExactCount(
      () => countGets(state, TECH_A),
      baseline + 1,
      ZERO_STABLE_MS,
    );

    await expect(page.getByText(HYDRATED_MD)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("technical-editor-loading")).toHaveCount(0);
    await expectTechReady(page, "V1G M3 同项目技术 managed");
    // 全程不得出现 lightweight parse POST
    expect(
      state.taskPosts.filter(
        (t) =>
          t.projectId === TECH_A &&
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      ).length,
    ).toBe(0);
    expect(state.externalHits).toEqual([]);
  });

  test("M3 技术 A managed running→切 B→释放 A success：A/B GET 零增量、B 零污染、零 sticky loading", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: TECH_A,
      name: "V1G M3 技术甲 managed",
      kind: "technical",
    });
    const projectB = makeProject({
      id: TECH_B,
      name: "V1G M3 技术乙 managed",
      kind: "technical",
    });
    const state = createProbeState([projectA, projectB], {
      parseStrategy: "managed",
    });
    collectConsole(page);
    await installRoutes(page, state);

    await page.goto(`/technical-plan/${TECH_A}/document`);
    await expectTechReady(page, "V1G M3 技术甲 managed");
    await expect(page.getByText(PARSE_MD_A)).toBeVisible();

    // M3 技术入口 exact「开始解析」
    await page.getByRole("button", { name: "开始解析", exact: true }).click();
    const post = await waitTaskRunningHeld(state, TECH_A, "parse");
    expect(post.payload).toEqual({ engine: "managed" });

    await softNavigate(page, `/technical-plan/${TECH_B}/document`);
    await expectTechReady(page, "V1G M3 技术乙 managed");
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();
    await waitStableExactCount(
      () => countGets(state, TECH_B),
      countGets(state, TECH_B),
      ZERO_STABLE_MS,
    );

    const baselineA = countGets(state, TECH_A);
    const baselineB = countGets(state, TECH_B);
    expect(baselineA).toBeGreaterThanOrEqual(1);
    expect(baselineB).toBeGreaterThanOrEqual(1);

    // 毒化 A 正文：若迟到 success 错误水合到 B 或粘住 loading 可观测
    state.editorById[TECH_A] = {
      ...state.editorById[TECH_A],
      parsedMarkdown: "V1G_M3_LATE_A_SHOULD_NOT_LEAK_TO_B",
    };

    await releaseTaskSuccess(
      state,
      post.taskId,
      "解析完成，请查看右侧预览",
      { ...MANAGED_SUCCESS_RESULT },
    );

    await assertSoftSwitchNoLateHydration({
      page,
      state,
      projectA: TECH_A,
      projectB: TECH_B,
      baselineA,
      baselineB,
      bName: "V1G M3 技术乙 managed",
      kind: "technical",
      forbidTexts: [
        PARSE_MD_A,
        "V1G_M3_LATE_A_SHOULD_NOT_LEAK_TO_B",
        "解析完成，请查看右侧预览",
      ],
    });
    await expect(page.getByText(PARSE_MD_B)).toBeVisible();
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === TECH_A && t.type === "parse",
      ).length,
    ).toBe(1);
    expect(state.externalHits).toEqual([]);
  });

  test("M3 同项目商务 managed success：payload 精确、GET +1 水合、步进精确 1", async ({
    page,
  }) => {
    const projectA = makeProject({
      id: BIZ_A,
      name: "V1G M3 同项目商务 managed",
      kind: "business",
      technicalPlanStep: 1,
    });
    const state = createProbeState([projectA], { parseStrategy: "managed" });
    await installRoutes(page, state);

    await page.goto(`/business-bid/${BIZ_A}/parse`);
    await expectBizReady(page, "V1G M3 同项目商务 managed");
    // 初始：aria-label 精确 textbox value（禁止仅 getByText 冒充水合）
    await expect(
      page.getByRole("textbox", { name: "商务条款解析 Markdown" }),
    ).toHaveValue(MARKDOWN_A);

    // 商务标「整段重解析」走 managed
    await page.getByRole("button", { name: "整段重解析" }).click();
    const post = await waitTaskRunningHeld(state, BIZ_A, "parse");
    expect(post.payload).toEqual({ engine: "managed" });
    expect(
      state.taskPosts.filter(
        (t) => t.projectId === BIZ_A && t.type === "parse",
      ).length,
    ).toBe(1);

    const baseline = countGets(state, BIZ_A);
    await waitStableExactCount(
      () => countGets(state, BIZ_A),
      baseline,
      ZERO_STABLE_MS,
    );

    const HYDRATED_BIZ = "V1G_M3_BIZ_MANAGED_HYDRATED_MD";
    state.editorById[BIZ_A] = {
      ...state.editorById[BIZ_A],
      parsedMarkdown: HYDRATED_BIZ,
    };

    const patchBefore = state.patchLog.length;
    await releaseTaskSuccess(
      state,
      post.taskId,
      "解析完成",
      { ...MANAGED_SUCCESS_RESULT },
    );

    const done = state.activeTasks[post.taskId];
    expect(done.result).toEqual({
      engine: "managed",
      fileCount: 1,
      chars: 128,
    });

    await expect
      .poll(() => countGets(state, BIZ_A), { timeout: 15_000 })
      .toBe(baseline + 1);
    await waitStableExactCount(
      () => countGets(state, BIZ_A),
      baseline + 1,
      ZERO_STABLE_MS,
    );

    // 水合：textbox value 精确等于新正文
    await expect(
      page.getByRole("textbox", { name: "商务条款解析 Markdown" }),
    ).toHaveValue(HYDRATED_BIZ);
    await expect(page.getByText("加载商务标工作区…")).toHaveCount(0);
    await expectBizReady(page, "V1G M3 同项目商务 managed");

    // 本轮 step PATCH 精确 1 条：projectId=BIZ_A，technicalPlanStep=1（禁止空循环假绿）
    await expect
      .poll(
        () =>
          state.patchLog.slice(patchBefore).filter((p) => p.projectId === BIZ_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    const onlyPatch = state.patchLog
      .slice(patchBefore)
      .find((p) => p.projectId === BIZ_A);
    expect(onlyPatch, "须存在 BIZ_A step PATCH").toBeTruthy();
    expect(onlyPatch!.projectId).toBe(BIZ_A);
    expect(onlyPatch!.body.technicalPlanStep).toBe(1);
    const bodyKeys = Object.keys(onlyPatch!.body).sort();
    if (bodyKeys.length === 1) {
      expect(onlyPatch!.body).toEqual({ technicalPlanStep: 1 });
    } else {
      // 多键时：精确 technicalPlanStep，并锁允许键集合
      const allowed = new Set(["technicalPlanStep"]);
      for (const k of bodyKeys) {
        expect(allowed.has(k), `PATCH body 含未允许键: ${k}`).toBe(true);
      }
    }
    // 本轮不得 PATCH 到其他项目
    expect(state.patchLog.slice(patchBefore).length).toBe(1);
    expect(projectA.technicalPlanStep).toBe(1);
    expect(
      state.taskPosts.filter(
        (t) =>
          t.type === "parse" &&
          t.payload &&
          (t.payload as { engine?: string }).engine === "lightweight",
      ).length,
    ).toBe(0);
    expect(state.externalHits).toEqual([]);
  });
});
