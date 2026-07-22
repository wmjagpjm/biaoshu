/**
 * 模块：V1-I 创建页招标文件摄入真值 E2E（failure-first）
 * 用途：锁定真实 File 选择/拖放、create→串行 multipart 上传顺序、失败零假绿、
 *       部分上传可恢复、同步单飞、无文件诚实创建、历史 pending 隔离与全路径泄漏探针。
 * 对接：Playwright chromium headless 单 worker；前端 5174；受控路由桩；合成 File 字节锚点。
 * 二次开发：禁止 skip/xfail、固定 sleep 作完成证据、源码扫描、宽泛 or/startsWith 路由、
 *       仅按 filename 冒充字节、吞路由异常；探针安装失败必须失败。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";

const FAKE_LS_KEY = "biaoshu.projects.v1";
const PENDING_SS_KEY = "biaoshu.pendingProjectFiles";
const FAKE_PROJECT_NAME = "LOCAL_FAKE_PROJECT_V1I_SHOULD_NOT_RENDER";
const FAKE_PROJECT_ID = "proj_local_fake_v1i";
const SECRET = "SECRET_V1I_LEAK_DETAIL_/api/projects";
const REAL_CREATE_ID = "proj_e2e_v1i_created";
const DEMO_FILENAME = "招标文件-正式稿.pdf";
const DEMO_SIZE = "12.4 MB";
const UPLOAD_ERROR = "文件上传失败，请重试";
const CREATE_ERROR = "项目创建失败，请稍后重试";
const EMPTY_FILES_UI = "尚未上传文件";

const ANCHOR_1 = "V1I_BYTE_ANCHOR_FILE1_a7f3c91e";
const ANCHOR_2 = "V1I_BYTE_ANCHOR_FILE2_b8e4d02f";
const ANCHOR_3 = "V1I_BYTE_ANCHOR_FILE3_c9f5e13a";
const FILE1_NAME = "v1i-intake-a.txt";
const FILE2_NAME = "v1i-intake-b.txt";
const FILE3_NAME = "v1i-intake-c.txt";
const DROP_NAME = "v1i-drop-real.txt";
const DROP_ANCHOR = "V1I_BYTE_ANCHOR_DROP_d0a6f24b";
const PENDING_FAKE_NAME = "HISTORICAL_PENDING_FAKE_NAME.pdf";

const PROJECT_META_KEY_RE = /^biaoshu\.projects(?:\.|$)/;

const FAKE_LS_VALUE = JSON.stringify([
  {
    id: FAKE_PROJECT_ID,
    workspaceId: "ws_local",
    name: FAKE_PROJECT_NAME,
    industry: "假行业",
    status: "draft",
    updatedAt: "2026-01-01T00:00:00.000Z",
    technicalPlanStep: 1,
    wordCount: 999,
    kind: "technical",
  },
]);

type ProjectStub = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  technicalPlanStep: number;
  wordCount: number;
  kind: "technical" | "business";
  linkedProjectId?: string | null;
};

type ProjectFileInfo = {
  id: string;
  filename: string;
  sizeBytes: number;
  createdAt: string;
};

/** multipart 全部有效 part（含普通字段与文件字段） */
type MultipartPart = {
  fieldName: string;
  /** 有 filename 属性则为文件 part，否则为普通字段 */
  filename: string | null;
  partBody: Buffer;
};

type FilePostRecord = {
  path: string;
  projectId: string;
  fieldName: string;
  filename: string;
  body: Buffer;
  contentType: string;
  /** 本请求解析出的全部有效 part（反假绿：恰好 1 个 file part） */
  parts: MultipartPart[];
};

type ProbeState = {
  projects: ProjectStub[];
  serverFiles: ProjectFileInfo[];
  createFail: boolean;
  /** 按 filename 计数：首次命中时若在 failOnceFilenames 则 500 */
  failOnceFilenames: Set<string>;
  fileAttemptCounts: Record<string, number>;
  createHold: Promise<void> | null;
  createPosts: Array<{ path: string; body: Record<string, unknown> }>;
  filePosts: FilePostRecord[];
  projectGets: string[];
  listGets: string[];
  forbiddenHits: string[];
  externalHits: string[];
  orderLog: string[];
  clipboard: { installed: boolean; read: number; write: number };
};

type StorageSnapshot = {
  lsKeys: string[];
  ls: Record<string, string>;
  ssKeys: string[];
  ss: Record<string, string>;
  cookies: string;
};

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

/**
 * 用途：method + 精确路径白名单；禁止宽放 /api/projects 前缀。
 * 只放行页面实际需要的端点；写方法仅允许 projects 创建与 files 上传。
 * settings/workspaces 嵌套与 tasks/images 写请求一律不放行（进 forbidden）。
 */
function isAllowedV1iApi(method: string, path: string): boolean {
  const pid = "proj_[a-z0-9_]+";
  const rules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    // 仅精确 workspace 单数 GET（页面壳可能读取）；禁止 /workspaces/* 前缀与写方法
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    // 仅精确 settings 根 GET；禁止 PUT 与 /settings/* 嵌套宽放
    { methods: ["GET"], path: /^\/api\/settings\/?$/ },
    { methods: ["GET", "POST"], path: /^\/api\/projects\/?$/ },
    { methods: ["GET", "PATCH"], path: new RegExp(`^/api/projects/${pid}/?$`) },
    {
      methods: ["GET", "PUT"],
      path: new RegExp(`^/api/projects/${pid}/editor-state/?$`),
    },
    // 写：仅 POST files；tasks/images 写不在白名单
    {
      methods: ["GET", "POST"],
      path: new RegExp(`^/api/projects/${pid}/files/?$`),
    },
    // 工作区可能拉任务列表：仅 GET
    {
      methods: ["GET"],
      path: new RegExp(`^/api/projects/${pid}/tasks/?$`),
    },
  ];
  return rules.some(
    (r) => r.methods.includes(method) && r.path.test(path),
  );
}

/**
 * 用途：合法当前 EditorStateApi 空态桩，供工作区 GET 水合通过 stateVersion 门。
 * 对接：useTechnicalPlanEditors / useBusinessBidWorkspace 要求 esv_ 版本；禁止 version:1。
 */
function emptyEditorState() {
  return {
    outline: [],
    chapters: [],
    mode: "ALIGNED",
    parsedMarkdown: "",
    facts: [],
    analysis: null,
    analysisOverview: "",
    responseMatrix: [],
    responseMatrixVersion: null,
    guidance: null,
    // 合法 P12B 空态版本；禁止 version:1 冒充
    stateVersion: "esv_00000000000000000000000000000001",
    updatedAt: null,
  };
}

function makeProject(
  partial: Partial<ProjectStub> & Pick<ProjectStub, "id" | "name" | "kind">,
): ProjectStub {
  return {
    workspaceId: "ws_e2e",
    industry: partial.industry ?? "智慧城市",
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

function createProbeState(seed: ProjectStub[] = []): ProbeState {
  return {
    projects: [...seed],
    serverFiles: [],
    createFail: false,
    failOnceFilenames: new Set(),
    fileAttemptCounts: {},
    createHold: null,
    createPosts: [],
    filePosts: [],
    projectGets: [],
    listGets: [],
    forbiddenHits: [],
    externalHits: [],
    orderLog: [],
    clipboard: { installed: false, read: 0, write: 0 },
  };
}

/**
 * 用途：从 multipart 请求体解析全部有效 part（按 boundary 精确切分）。
 * 反假绿：返回全部 name= part；禁止只取第一个 filename 而吞掉额外 part。
 */
function parseMultipartParts(
  body: Buffer,
  contentType: string,
): MultipartPart[] {
  const boundaryMatch = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
  if (!boundaryMatch) {
    throw new Error("multipart 缺少 boundary");
  }
  const boundary = boundaryMatch[1] || boundaryMatch[2];
  const delim = Buffer.from(`--${boundary}`);
  const rawParts: Buffer[] = [];
  let start = body.indexOf(delim);
  if (start < 0) {
    throw new Error("multipart 未找到首 boundary");
  }
  start += delim.length;
  // 跳过可选 CRLF
  if (body[start] === 0x0d && body[start + 1] === 0x0a) start += 2;
  while (start < body.length) {
    const next = body.indexOf(delim, start);
    if (next < 0) break;
    let part = body.subarray(start, next);
    // 去掉结尾 CRLF
    if (
      part.length >= 2 &&
      part[part.length - 2] === 0x0d &&
      part[part.length - 1] === 0x0a
    ) {
      part = part.subarray(0, part.length - 2);
    }
    // 结束 boundary 带 --
    if (part.length === 0 || (part.length === 2 && part.toString() === "--")) {
      break;
    }
    if (!(part.length === 2 && part[0] === 0x2d && part[1] === 0x2d)) {
      rawParts.push(part);
    }
    start = next + delim.length;
    if (body[start] === 0x0d && body[start + 1] === 0x0a) start += 2;
    if (body[start] === 0x2d && body[start + 1] === 0x2d) break;
  }

  const parsed: MultipartPart[] = [];
  for (const part of rawParts) {
    const headerEnd = part.indexOf(Buffer.from("\r\n\r\n"));
    if (headerEnd < 0) continue;
    const headerText = part.subarray(0, headerEnd).toString("utf8");
    const partBody = part.subarray(headerEnd + 4);
    const nameMatch = /name="([^"]+)"/i.exec(headerText);
    if (!nameMatch) continue;
    const fileMatch = /filename="([^"]*)"/i.exec(headerText);
    parsed.push({
      fieldName: nameMatch[1],
      filename: fileMatch ? fileMatch[1] : null,
      partBody,
    });
  }
  if (parsed.length === 0) {
    throw new Error("multipart 未解析到任何有效 part");
  }
  return parsed;
}

/**
 * 用途：要求恰好一个 part、且为 field=file 的文件字段；无额外普通/文件 part。
 */
function requireExactSingleFilePart(parts: MultipartPart[]): {
  fieldName: string;
  filename: string;
  partBody: Buffer;
} {
  if (parts.length !== 1) {
    throw new Error(`multipart 期望恰好 1 个 part，实际 ${parts.length}`);
  }
  const only = parts[0];
  if (only.filename === null) {
    throw new Error("multipart 唯一 part 缺少 filename（非文件字段）");
  }
  if (only.fieldName !== "file") {
    throw new Error(`multipart 字段名须为 file，实际 ${only.fieldName}`);
  }
  return {
    fieldName: only.fieldName,
    filename: only.filename,
    partBody: only.partBody,
  };
}

function bufferHasAnchor(buf: Buffer, anchor: string): boolean {
  return buf.includes(Buffer.from(anchor, "utf8"));
}

/** 用途：与知识库上传一致的真实大小展示（B / x.x KB / x.x MB）。 */
function expectedSizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function makeSyntheticFile(
  name: string,
  anchor: string,
  extra = "",
): { name: string; mimeType: string; buffer: Buffer } {
  const text = `# V1-I 合成招标片段\n${anchor}\n${extra}\n`;
  return {
    name,
    mimeType: "text/plain",
    buffer: Buffer.from(text, "utf8"),
  };
}

async function installV1iRoutes(page: Page, state: ProbeState) {
  await page.addInitScript(() => {
    const g = globalThis as unknown as {
      __v1iClip?: { installed: boolean; read: number; write: number };
    };
    g.__v1iClip = { installed: false, read: 0, write: 0 };
    const clip = {
      readText: async () => {
        g.__v1iClip!.read += 1;
        return "";
      },
      writeText: async () => {
        g.__v1iClip!.write += 1;
      },
    };
    try {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: clip,
      });
      g.__v1iClip.installed = true;
    } catch {
      g.__v1iClip.installed = false;
    }
  });

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

    if (!isAllowedV1iApi(method, path)) {
      state.forbiddenHits.push(`${method} ${path}`);
      await json(
        route,
        { detail: { code: "v1i_forbidden", message: SECRET } },
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
        csrfToken: null,
      });
      return;
    }

    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-v1i-csrf" });
      return;
    }

    if (
      (path === "/api/auth/login" || path === "/api/auth/logout") &&
      method === "POST"
    ) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    // 精确 GET /api/workspace（与白名单一致；/api/workspaces 与写方法不处理）
    if (/^\/api\/workspace\/?$/.test(path) && method === "GET") {
      await json(route, {
        id: "ws_e2e",
        name: "E2E 工作空间",
        ownerUserId: "user_e2e",
      });
      return;
    }

    // 精确 GET /api/settings（禁止 startsWith 宽放与 PUT）
    if (/^\/api\/settings\/?$/.test(path) && method === "GET") {
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
        state.listGets.push(`${path}${url.search || ""}`);
        state.orderLog.push(`list-get:${path}${url.search || ""}`);
        const kind = url.searchParams.get("kind");
        let items = state.projects;
        if (kind === "technical") {
          items = items.filter((p) => p.kind === "technical");
        } else if (kind === "business") {
          items = items.filter((p) => p.kind === "business");
        }
        await json(route, items);
        return;
      }
      if (method === "POST") {
        if (state.createHold) {
          await state.createHold;
        }
        const raw = req.postData() || "{}";
        let body: Record<string, unknown> = {};
        try {
          body = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          body = { __parseError: true };
        }
        state.createPosts.push({ path, body });
        state.orderLog.push("create-post");
        if (state.createFail) {
          await json(
            route,
            { detail: { code: "projects_create_failed", message: SECRET } },
            500,
          );
          return;
        }
        const kind =
          body.kind === "business" ? "business" : ("technical" as const);
        const created = makeProject({
          id: REAL_CREATE_ID,
          name: String(body.name || "未命名"),
          industry: String(body.industry || "通用"),
          kind,
          technicalPlanStep: Number(body.technicalPlanStep || 1),
          status: String(body.status || "draft"),
        });
        state.projects = [created, ...state.projects];
        await json(route, created, 201);
        return;
      }
    }

    const detailMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (detailMatch && (method === "GET" || method === "PATCH")) {
      const id = decodeURIComponent(detailMatch[1]);
      state.projectGets.push(`${method} ${path}`);
      state.orderLog.push(`project-${method.toLowerCase()}:${id}`);
      const found = state.projects.find((p) => p.id === id);
      if (!found) {
        await json(
          route,
          { detail: { code: "project_not_found", message: SECRET } },
          404,
        );
        return;
      }
      if (method === "PATCH") {
        const raw = req.postData() || "{}";
        let patch: Record<string, unknown> = {};
        try {
          patch = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          patch = {};
        }
        Object.assign(found, patch, { updatedAt: new Date().toISOString() });
      }
      await json(route, found);
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch && (method === "GET" || method === "PUT")) {
      state.orderLog.push(`editor-${method.toLowerCase()}:${editorMatch[1]}`);
      await json(route, emptyEditorState());
      return;
    }

    const filesMatch = path.match(/^\/api\/projects\/([^/]+)\/files\/?$/);
    if (filesMatch && method === "GET") {
      const pid = decodeURIComponent(filesMatch[1]);
      state.orderLog.push(`files-get:${pid}`);
      // 单项目探针：返回当前 serverFiles（由 POST 累加或测试预置）
      await json(route, state.serverFiles);
      return;
    }

    if (filesMatch && method === "POST") {
      const pid = decodeURIComponent(filesMatch[1]);
      const contentType = req.headers()["content-type"] || "";
      const rawBody = req.postDataBuffer() ?? Buffer.alloc(0);
      let parts: MultipartPart[] = [];
      let fieldName = "";
      let filename = "";
      let partBody = Buffer.alloc(0);
      try {
        parts = parseMultipartParts(rawBody, contentType);
        const single = requireExactSingleFilePart(parts);
        fieldName = single.fieldName;
        filename = single.filename;
        partBody = single.partBody;
      } catch {
        state.forbiddenHits.push(`${method} ${path} multipart-parse-failed`);
        await json(
          route,
          { detail: { code: "v1i_multipart_invalid", message: SECRET } },
          400,
        );
        return;
      }

      const record: FilePostRecord = {
        path,
        projectId: pid,
        fieldName,
        filename,
        body: partBody,
        contentType,
        parts,
      };
      state.filePosts.push(record);
      state.orderLog.push(`file-post:${filename}`);
      state.fileAttemptCounts[filename] =
        (state.fileAttemptCounts[filename] || 0) + 1;

      if (
        state.failOnceFilenames.has(filename) &&
        state.fileAttemptCounts[filename] === 1
      ) {
        await json(
          route,
          { detail: { code: "file_upload_failed", message: SECRET } },
          500,
        );
        return;
      }

      const row: ProjectFileInfo = {
        id: `file_${pid}_${state.filePosts.length}`,
        filename,
        sizeBytes: partBody.length,
        createdAt: "2026-07-22T12:00:00.000Z",
      };
      state.serverFiles = [...state.serverFiles, row];
      await json(route, row, 201);
      return;
    }

    // 仅 GET tasks 列表（无 tasks/images POST success 假绿）
    if (
      /\/api\/projects\/[^/]+\/tasks\/?$/.test(path) &&
      method === "GET"
    ) {
      await json(route, []);
      return;
    }

    // 白名单命中但未实现、或未知写请求：一律 forbidden
    state.forbiddenHits.push(`${method} ${path}`);
    await json(
      route,
      { detail: { code: "v1i_unhandled", message: SECRET } },
      403,
    );
  });
}

async function seedFakeLocalProjects(page: Page) {
  await page.addInitScript(
    ({ key, value }) => {
      localStorage.setItem(key, value);
    },
    { key: FAKE_LS_KEY, value: FAKE_LS_VALUE },
  );
}

async function seedPendingSession(
  page: Page,
  projectId: string,
  fileNames: string[],
) {
  await page.addInitScript(
    ({ key, payload }) => {
      sessionStorage.setItem(key, payload);
    },
    {
      key: PENDING_SS_KEY,
      payload: JSON.stringify({ projectId, fileNames }),
    },
  );
}

async function readStorageSnapshot(page: Page): Promise<StorageSnapshot> {
  return page.evaluate(() => {
    const lsKeys: string[] = [];
    const ls: Record<string, string> = {};
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i) ?? "";
      lsKeys.push(k);
      ls[k] = localStorage.getItem(k) ?? "";
    }
    const ssKeys: string[] = [];
    const ss: Record<string, string> = {};
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i) ?? "";
      ssKeys.push(k);
      ss[k] = sessionStorage.getItem(k) ?? "";
    }
    return {
      lsKeys: lsKeys.slice().sort(),
      ls,
      ssKeys: ssKeys.slice().sort(),
      ss,
      cookies: document.cookie,
    };
  });
}

async function readIdbNames(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    if (typeof indexedDB === "undefined") {
      throw new Error("indexedDB 不可用");
    }
    if (typeof indexedDB.databases !== "function") {
      throw new Error("indexedDB.databases 不可用");
    }
    const dbs = await indexedDB.databases();
    return dbs.map((d) => d.name ?? "");
  });
}

async function readClipboardProbe(page: Page) {
  return page.evaluate(() => {
    const g = globalThis as unknown as {
      __v1iClip?: { installed: boolean; read: number; write: number };
    };
    return g.__v1iClip ?? { installed: false, read: -1, write: -1 };
  });
}

function assertProjectMetaKeysOnlyLegacyV1(
  snap: StorageSnapshot,
  expectedValue: string = FAKE_LS_VALUE,
) {
  const projectKeys = snap.lsKeys
    .filter((k) => PROJECT_META_KEY_RE.test(k))
    .slice()
    .sort();
  expect(projectKeys, "项目元数据键族必须精确只有 v1").toEqual([FAKE_LS_KEY]);
  expect(snap.ls[FAKE_LS_KEY]).toBe(expectedValue);
}

function assertStorageSnapshotEqual(
  actual: StorageSnapshot,
  baseline: StorageSnapshot,
  label: string,
) {
  expect(actual.lsKeys, `${label} localStorage 键集`).toEqual(baseline.lsKeys);
  expect(actual.ls, `${label} localStorage 值`).toEqual(baseline.ls);
  expect(actual.ssKeys, `${label} sessionStorage 键集`).toEqual(
    baseline.ssKeys,
  );
  expect(actual.ss, `${label} sessionStorage 值`).toEqual(baseline.ss);
  expect(actual.cookies, `${label} cookie`).toBe(baseline.cookies);
}

function assertTechWorkspaceLocalKeys(
  snap: StorageSnapshot,
  projectId: string,
) {
  assertProjectMetaKeysOnlyLegacyV1(snap);
  const allowed = new Set([
    FAKE_LS_KEY,
    `biaoshu.technicalPlan.editors.${projectId}`,
    `biaoshu.projectFeedback.${projectId}`,
  ]);
  for (const k of snap.lsKeys) {
    expect(allowed.has(k), `技术标工作区未允许的 localStorage 键: ${k}`).toBe(
      true,
    );
  }
}

function collectConsole(page: Page) {
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

function appConsoleLines(lines: string[]): string[] {
  return lines.filter((line) => {
    if (/^(error|warning): Failed to load resource:/.test(line)) return false;
    return true;
  });
}

function sensitiveSnippets(extra: string[] = []): string[] {
  return [
    SECRET,
    FAKE_PROJECT_ID,
    REAL_CREATE_ID,
    ANCHOR_1,
    ANCHOR_2,
    ANCHOR_3,
    DROP_ANCHOR,
    "/api/projects",
    "projects_create_failed",
    "file_upload_failed",
    "v1i_forbidden",
    PENDING_FAKE_NAME,
    ...extra,
  ];
}

function assertCleanConsole(lines: string[], extra: string[] = []) {
  expect(appConsoleLines(lines)).toEqual([]);
  const joined = lines.join("\n");
  for (const b of sensitiveSnippets(extra)) {
    expect(joined, `console 敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

function assertNoSensitiveInText(text: string, extra: string[] = []) {
  for (const b of sensitiveSnippets(extra)) {
    // 页面可显示用户选择的 basename，但不得显示字节锚点与 secret
    if (
      b === FILE1_NAME ||
      b === FILE2_NAME ||
      b === FILE3_NAME ||
      b === DROP_NAME
    ) {
      continue;
    }
    expect(text, `页面敏感片段泄漏: ${b}`).not.toContain(b);
  }
}

function assertCreateBodyFiveKeys(body: Record<string, unknown>) {
  expect(Object.keys(body).sort()).toEqual(
    ["industry", "kind", "name", "status", "technicalPlanStep"].sort(),
  );
  expect(body.kind).toBe("technical");
  expect(typeof body.name).toBe("string");
  expect(String(body.name).length).toBeGreaterThan(0);
}

function assertFilePostExact(
  rec: FilePostRecord,
  expected: { projectId: string; filename: string; anchor: string },
) {
  expect(rec.projectId).toBe(expected.projectId);
  // 恰好一个 part、一个 file 字段、无额外普通/文件 part
  expect(rec.parts.length, "multipart 恰好一个 part").toBe(1);
  expect(
    rec.parts.filter((p) => p.filename !== null).length,
    "恰好一个文件 part",
  ).toBe(1);
  expect(
    rec.parts.filter((p) => p.filename === null).length,
    "不得有额外普通 part",
  ).toBe(0);
  expect(rec.fieldName).toBe("file");
  expect(rec.parts[0].fieldName).toBe("file");
  expect(rec.parts[0].filename).toBe(expected.filename);
  expect(rec.filename).toBe(expected.filename);
  expect(bufferHasAnchor(rec.body, expected.anchor)).toBe(true);
  expect(bufferHasAnchor(rec.parts[0].partBody, expected.anchor)).toBe(true);
  // 独立锚点：本 part 不得夹带其它文件锚点（drop 用例单独断言）
}

function countFilePosts(
  state: ProbeState,
  filename: string,
): number {
  return state.filePosts.filter((p) => p.filename === filename).length;
}

function orderOfApi(state: ProbeState): string[] {
  return state.orderLog.filter(
    (x) => x === "create-post" || x.startsWith("file-post:"),
  );
}

async function gotoCreate(page: Page) {
  await page.goto("/create");
  await expect(
    page.getByRole("heading", { name: "技术标生成" }),
  ).toBeVisible({ timeout: 20_000 });
}

async function selectTwoFilesViaChooser(page: Page) {
  const f1 = makeSyntheticFile(FILE1_NAME, ANCHOR_1, "part-a");
  const f2 = makeSyntheticFile(FILE2_NAME, ANCHOR_2, "part-b");
  // 短超时：生产未接线真实 input 时快速首红，禁止固定 sleep 伪装等待
  const chooserPromise = page.waitForEvent("filechooser", { timeout: 5_000 });
  await page.locator(".upload-card").click();
  const chooser = await chooserPromise;
  await chooser.setFiles([f1, f2]);
  return { f1, f2 };
}

async function selectThreeFilesViaChooser(page: Page) {
  const f1 = makeSyntheticFile(FILE1_NAME, ANCHOR_1, "part-a");
  const f2 = makeSyntheticFile(FILE2_NAME, ANCHOR_2, "part-b");
  const f3 = makeSyntheticFile(FILE3_NAME, ANCHOR_3, "part-c");
  const chooserPromise = page.waitForEvent("filechooser", { timeout: 5_000 });
  await page.locator(".upload-card").click();
  const chooser = await chooserPromise;
  await chooser.setFiles([f1, f2, f3]);
  return { f1, f2, f3 };
}

/**
 * 用途：DataTransfer 拖入真实 File（浏览器内构造，保留 filename 与字节）。
 */
async function dropRealFile(
  page: Page,
  file: { name: string; mimeType: string; buffer: Buffer },
) {
  const b64 = file.buffer.toString("base64");
  await page.locator(".upload-card").evaluate(
    (el, payload) => {
      const binary = atob(payload.b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      const dt = new DataTransfer();
      dt.items.add(
        new File([bytes], payload.name, { type: payload.mimeType }),
      );
      el.dispatchEvent(
        new DragEvent("dragover", {
          bubbles: true,
          cancelable: true,
          dataTransfer: dt,
        }),
      );
      el.dispatchEvent(
        new DragEvent("drop", {
          bubbles: true,
          cancelable: true,
          dataTransfer: dt,
        }),
      );
    },
    { name: file.name, mimeType: file.mimeType, b64 },
  );
}

async function dropEmpty(page: Page) {
  await page.locator(".upload-card").evaluate((el) => {
    const dt = new DataTransfer();
    el.dispatchEvent(
      new DragEvent("dragover", {
        bubbles: true,
        cancelable: true,
        dataTransfer: dt,
      }),
    );
    el.dispatchEvent(
      new DragEvent("drop", {
        bubbles: true,
        cancelable: true,
        dataTransfer: dt,
      }),
    );
  });
}

async function assertCreatePageBoundary(
  page: Page,
  baseline: StorageSnapshot,
  label: string,
) {
  const snap = await readStorageSnapshot(page);
  assertStorageSnapshotEqual(snap, baseline, label);
  assertProjectMetaKeysOnlyLegacyV1(snap);
  expect(snap.ss[PENDING_SS_KEY] ?? null).toBe(null);
  expect(snap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
  expect(snap.cookies).toBe("");
  expect(await readIdbNames(page)).toEqual([]);
  const clip = await readClipboardProbe(page);
  expect(clip.installed).toBe(true);
  expect(clip.read).toBe(0);
  expect(clip.write).toBe(0);
}

function assertAnchorsOnlyInMatchingMultipart(state: ProbeState) {
  const allCreateBodies = state.createPosts
    .map((c) => JSON.stringify(c.body))
    .join("\n");
  for (const a of [ANCHOR_1, ANCHOR_2, ANCHOR_3, DROP_ANCHOR]) {
    expect(allCreateBodies, `create JSON 不得含锚点 ${a}`).not.toContain(a);
  }
  for (const rec of state.filePosts) {
    const anchors = [ANCHOR_1, ANCHOR_2, ANCHOR_3, DROP_ANCHOR].filter((a) =>
      bufferHasAnchor(rec.body, a),
    );
    // 每个 multipart part 至多携带与自身 filename 对应的一个测试锚点
    expect(
      anchors.length,
      `file ${rec.filename} 锚点数量异常: ${anchors.join(",")}`,
    ).toBeLessThanOrEqual(1);
  }
}

test.describe("V1-I 创建页招标文件摄入真值", () => {
  test("点击 upload-card 只触发真实 input；双文件 chip/顺序/multipart 字节锚点后导航", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    // 点击前：零演示 chip
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    await expect(page.getByText(DEMO_SIZE)).toHaveCount(0);

    const { f1, f2 } = await selectTwoFilesViaChooser(page);

    // 选择后：真实文件名与真实大小；按 .file-chip+filename 作用域（等长 64 B 不得全页唯一）
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    const chip1Ok = page.locator(".file-chip", { hasText: FILE1_NAME });
    const chip2Ok = page.locator(".file-chip", { hasText: FILE2_NAME });
    await expect(
      chip1Ok.getByText(expectedSizeLabel(f1.buffer.length)),
    ).toBeVisible();
    await expect(
      chip2Ok.getByText(expectedSizeLabel(f2.buffer.length)),
    ).toBeVisible();
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    await expect(page.getByText(DEMO_SIZE)).toHaveCount(0);

    const baseline = await readStorageSnapshot(page);
    assertProjectMetaKeysOnlyLegacyV1(baseline);

    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );

    expect(state.createPosts.length).toBe(1);
    assertCreateBodyFiveKeys(state.createPosts[0].body);
    expect(state.filePosts.length).toBe(2);
    expect(orderOfApi(state)).toEqual([
      "create-post",
      `file-post:${FILE1_NAME}`,
      `file-post:${FILE2_NAME}`,
    ]);
    assertFilePostExact(state.filePosts[0], {
      projectId: REAL_CREATE_ID,
      filename: FILE1_NAME,
      anchor: ANCHOR_1,
    });
    assertFilePostExact(state.filePosts[1], {
      projectId: REAL_CREATE_ID,
      filename: FILE2_NAME,
      anchor: ANCHOR_2,
    });
    expect(bufferHasAnchor(state.filePosts[0].body, ANCHOR_2)).toBe(false);
    expect(bufferHasAnchor(state.filePosts[1].body, ANCHOR_1)).toBe(false);
    assertAnchorsOnlyInMatchingMultipart(state);

    const okSnap = await readStorageSnapshot(page);
    assertTechWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    expect(okSnap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
    expect(okSnap.ss[PENDING_SS_KEY] ?? null).toBe(null);
    expect(okSnap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);

    const bodyText = await page.locator("body").innerText();
    assertNoSensitiveInText(bodyText, [DEMO_FILENAME]);
    expect(page.url()).not.toContain(ANCHOR_1);
    expect(page.url()).not.toContain(ANCHOR_2);
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });

  test("DataTransfer 拖入保留真实 File；空 drop 零演示文件", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    await dropEmpty(page);
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    await expect(page.locator(".file-chip")).toHaveCount(0);

    const dropFile = makeSyntheticFile(DROP_NAME, DROP_ANCHOR, "drop-body");
    await dropRealFile(page, dropFile);
    await expect(page.getByText(DROP_NAME)).toBeVisible();
    await expect(
      page.getByText(expectedSizeLabel(dropFile.buffer.length)),
    ).toBeVisible();
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);

    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );

    expect(state.createPosts.length).toBe(1);
    expect(state.filePosts.length).toBe(1);
    expect(orderOfApi(state)).toEqual([
      "create-post",
      `file-post:${DROP_NAME}`,
    ]);
    assertFilePostExact(state.filePosts[0], {
      projectId: REAL_CREATE_ID,
      filename: DROP_NAME,
      anchor: DROP_ANCHOR,
    });
    assertAnchorsOnlyInMatchingMultipart(state);
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });

  test("create 失败：一次 create、零 upload、留 /create、固定脱敏错误、选择可重试", async ({
    page,
  }) => {
    const state = createProbeState([]);
    state.createFail = true;
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    const { f1, f2 } = await selectTwoFilesViaChooser(page);
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();

    const baseline = await readStorageSnapshot(page);
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page.getByText(CREATE_ERROR)).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/\/create/);
    expect(state.createPosts.length).toBe(1);
    expect(state.filePosts.length).toBe(0);
    expect(orderOfApi(state)).toEqual(["create-post"]);

    await expect(page.getByText(SECRET)).toHaveCount(0);
    await expect(page.getByText("projects_create_failed")).toHaveCount(0);
    await expect(page.getByText("/api/projects")).toHaveCount(0);
    await expect(page.getByText(REAL_CREATE_ID)).toHaveCount(0);

    // 选择仍可重试：文件 chip 保留；大小按 chip+filename 作用域（等长 64 B 不得全页唯一）
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    const chip1Fail = page.locator(".file-chip", { hasText: FILE1_NAME });
    const chip2Fail = page.locator(".file-chip", { hasText: FILE2_NAME });
    await expect(
      chip1Fail.getByText(expectedSizeLabel(f1.buffer.length)),
    ).toBeVisible();
    await expect(
      chip2Fail.getByText(expectedSizeLabel(f2.buffer.length)),
    ).toBeVisible();

    await assertCreatePageBoundary(page, baseline, "create 失败后");
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).toContain(CREATE_ERROR);
    expect(bodyText).not.toContain(SECRET);
    expect(bodyText).not.toContain(ANCHOR_1);
    assertCleanConsole(consoleLines);

    // 重试：仍失败，仍只增加 create、零 upload
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page.getByText(CREATE_ERROR)).toBeVisible();
    expect(state.createPosts.length).toBe(2);
    expect(state.filePosts.length).toBe(0);
    await expect(page).toHaveURL(/\/create/);
  });

  test("三文件中第二个首次失败：create 一次；首文件不重传；第二重试；第三仅重试轮；全成才导航", async ({
    page,
  }) => {
    const state = createProbeState([]);
    state.failOnceFilenames.add(FILE2_NAME);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    await selectThreeFilesViaChooser(page);
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    await expect(page.getByText(FILE3_NAME)).toBeVisible();

    const baseline = await readStorageSnapshot(page);
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page.getByText(UPLOAD_ERROR)).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/create/);

    expect(state.createPosts.length).toBe(1);
    expect(countFilePosts(state, FILE1_NAME)).toBe(1);
    expect(countFilePosts(state, FILE2_NAME)).toBe(1);
    expect(countFilePosts(state, FILE3_NAME)).toBe(0);
    expect(orderOfApi(state)).toEqual([
      "create-post",
      `file-post:${FILE1_NAME}`,
      `file-post:${FILE2_NAME}`,
    ]);
    assertFilePostExact(state.filePosts[0], {
      projectId: REAL_CREATE_ID,
      filename: FILE1_NAME,
      anchor: ANCHOR_1,
    });
    // 失败请求仍须是真实 multipart（含锚点），不得假体
    assertFilePostExact(state.filePosts[1], {
      projectId: REAL_CREATE_ID,
      filename: FILE2_NAME,
      anchor: ANCHOR_2,
    });

    await expect(page.getByText(SECRET)).toHaveCount(0);
    await expect(page.getByText("file_upload_failed")).toHaveCount(0);
    // 失败停留：不得写 pending；项目已在服务端，但本页存储仍无 pending
    const failSnap = await readStorageSnapshot(page);
    expect(failSnap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
    assertProjectMetaKeysOnlyLegacyV1(failSnap);
    expect(failSnap.cookies).toBe(baseline.cookies);
    expect(await readIdbNames(page)).toEqual([]);

    // —— 契约 §2.8：部分上传失败后、重试前，项目语义锁定（UI 行为，禁止只读源码）——
    await expect(page).toHaveURL(/\/create/);
    await expect(
      page.getByRole("heading", { name: "技术标生成" }),
    ).toBeVisible();
    await expect(page.locator(".file-chip")).toHaveCount(3);
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    await expect(page.getByText(FILE3_NAME)).toBeVisible();

    const uploadCard = page.locator(".upload-card");
    // upload-card 须有明确禁用语义（a11y）
    await expect(uploadCard).toHaveAttribute("aria-disabled", "true");

    // 其它能力按钮不可切换创建能力
    const bizFeature = page.locator(".feature-item", {
      hasText: "商务标生成",
    });
    await bizFeature.click({ force: true });
    await expect(
      page.getByRole("heading", { name: "技术标生成" }),
    ).toBeVisible();
    await expect(page.locator(".file-chip")).toHaveCount(3);
    expect(state.createPosts.length).toBe(1);

    // 移除按钮不可改变三文件集合（.file-chip 内精确 aria-label，不依赖 upload-card 可访问名）
    const removeBtns = page.locator('.file-chip button[aria-label="移除"]');
    await expect(removeBtns).toHaveCount(3);
    await expect(removeBtns.nth(0)).toBeDisabled();
    await removeBtns.nth(0).click({ force: true });
    await expect(page.locator(".file-chip")).toHaveCount(3);
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    await expect(page.getByText(FILE3_NAME)).toBeVisible();

    // file input / 点击 upload-card 不得新增文件（不应弹出 filechooser）
    const chooserProbe = page
      .waitForEvent("filechooser", { timeout: 1_500 })
      .then(() => "opened" as const)
      .catch(() => "none" as const);
    await uploadCard.click({ force: true });
    expect(await chooserProbe).toBe("none");
    await expect(page.locator(".file-chip")).toHaveCount(3);

    // 新 drop 不改变三文件集合
    const extraDrop = makeSyntheticFile(
      "v1i-extra-should-not-add.txt",
      "V1I_BYTE_ANCHOR_EXTRA_should_not",
      "extra",
    );
    await dropRealFile(page, extraDrop);
    await expect(page.getByText("v1i-extra-should-not-add.txt")).toHaveCount(0);
    await expect(page.locator(".file-chip")).toHaveCount(3);
    await expect(page.getByText(FILE1_NAME)).toBeVisible();
    await expect(page.getByText(FILE2_NAME)).toBeVisible();
    await expect(page.getByText(FILE3_NAME)).toBeVisible();

    // 空 drop 不改变三文件集合与项目
    await dropEmpty(page);
    await expect(page.locator(".file-chip")).toHaveCount(3);
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    expect(state.createPosts.length).toBe(1);
    expect(countFilePosts(state, FILE1_NAME)).toBe(1);
    expect(countFilePosts(state, FILE2_NAME)).toBe(1);
    expect(countFilePosts(state, FILE3_NAME)).toBe(0);

    // 重试：不得再 create；首文件不重传
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );

    expect(state.createPosts.length).toBe(1);
    expect(countFilePosts(state, FILE1_NAME)).toBe(1);
    expect(countFilePosts(state, FILE2_NAME)).toBe(2);
    expect(countFilePosts(state, FILE3_NAME)).toBe(1);
    expect(orderOfApi(state)).toEqual([
      "create-post",
      `file-post:${FILE1_NAME}`,
      `file-post:${FILE2_NAME}`,
      `file-post:${FILE2_NAME}`,
      `file-post:${FILE3_NAME}`,
    ]);

    const secondOk = state.filePosts.filter((p) => p.filename === FILE2_NAME)[1];
    assertFilePostExact(secondOk, {
      projectId: REAL_CREATE_ID,
      filename: FILE2_NAME,
      anchor: ANCHOR_2,
    });
    const third = state.filePosts.find((p) => p.filename === FILE3_NAME)!;
    assertFilePostExact(third, {
      projectId: REAL_CREATE_ID,
      filename: FILE3_NAME,
      anchor: ANCHOR_3,
    });
    assertAnchorsOnlyInMatchingMultipart(state);

    const okSnap = await readStorageSnapshot(page);
    assertTechWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    expect(okSnap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
    assertCleanConsole(consoleLines);
    expect(state.externalHits).toEqual([]);
  });

  test("延迟 create 时同步双触发只产生一次 create", async ({ page }) => {
    const state = createProbeState([]);
    let release!: () => void;
    state.createHold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    await selectTwoFilesViaChooser(page);

    // 同步双触发：同一调用栈连续 click，不依赖 React 下一帧 disabled
    await page.getByRole("button", { name: "开始生成技术标" }).evaluate((el) => {
      (el as HTMLButtonElement).click();
      (el as HTMLButtonElement).click();
    });

    // 释放前不应完成导航
    await expect(page).toHaveURL(/\/create/);
    release();

    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );

    expect(state.createPosts.length).toBe(1);
    expect(state.filePosts.length).toBe(2);
    expect(orderOfApi(state)).toEqual([
      "create-post",
      `file-post:${FILE1_NAME}`,
      `file-post:${FILE2_NAME}`,
    ]);
    // 上传与导航也不得重复
    expect(countFilePosts(state, FILE1_NAME)).toBe(1);
    expect(countFilePosts(state, FILE2_NAME)).toBe(1);
    assertCleanConsole(consoleLines);
  });

  test("无文件：一次 create、零 upload、零 pending/演示名，工作区服务端空态", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    await expect(page.locator(".file-chip")).toHaveCount(0);

    const baseline = await readStorageSnapshot(page);
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );

    expect(state.createPosts.length).toBe(1);
    assertCreateBodyFiveKeys(state.createPosts[0].body);
    expect(state.filePosts.length).toBe(0);
    expect(orderOfApi(state)).toEqual(["create-post"]);

    await expect(page.getByText(EMPTY_FILES_UI)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);
    await expect(page.getByText(PENDING_FAKE_NAME)).toHaveCount(0);

    const okSnap = await readStorageSnapshot(page);
    assertTechWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    expect(okSnap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
    expect(okSnap.ss[PENDING_SS_KEY] ?? null).toBe(null);
    // 相对创建页基线：session 不得新增 pending
    expect(okSnap.ssKeys.filter((k) => k === PENDING_SS_KEY)).toEqual([]);
    expect(okSnap.cookies).toBe(baseline.cookies);
    expect(await readIdbNames(page)).toEqual([]);
    assertCleanConsole(consoleLines);
  });

  test("历史 pending 预置后工作区只认 GET /files", async ({ page }) => {
    const state = createProbeState([
      makeProject({
        id: REAL_CREATE_ID,
        name: "已有技术标项目",
        kind: "technical",
      }),
    ]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await seedPendingSession(page, REAL_CREATE_ID, [PENDING_FAKE_NAME]);
    await installV1iRoutes(page, state);

    // 服务端空：假名不可见
    state.serverFiles = [];
    await page.goto(`/technical-plan/${REAL_CREATE_ID}/document`);
    await expect(page.getByText(EMPTY_FILES_UI)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(PENDING_FAKE_NAME)).toHaveCount(0);
    await expect(page.getByText(DEMO_FILENAME)).toHaveCount(0);

    // 服务端返回真实文件：只显示服务端 filename
    const serverName = "server-authoritative-file.txt";
    state.serverFiles = [
      {
        id: `file_${REAL_CREATE_ID}_1`,
        filename: serverName,
        sizeBytes: 128,
        createdAt: "2026-07-22T12:00:00.000Z",
      },
    ];
    await page.reload();
    await expect(page.getByText(serverName)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(PENDING_FAKE_NAME)).toHaveCount(0);
    await expect(page.getByText(EMPTY_FILES_UI)).toHaveCount(0);

    // 再刷新仍只认服务端
    await page.reload();
    await expect(page.getByText(serverName)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(PENDING_FAKE_NAME)).toHaveCount(0);

    // 历史 pending：不读取、不迁移、不删除——键必须仍在且 JSON 严格等于预置
    const snap = await readStorageSnapshot(page);
    expect(snap.ssKeys.includes(PENDING_SS_KEY)).toBe(true);
    expect(JSON.parse(snap.ss[PENDING_SS_KEY])).toEqual({
      projectId: REAL_CREATE_ID,
      fileNames: [PENDING_FAKE_NAME],
    });
    assertCleanConsole(consoleLines, [serverName]);
  });

  test("成功/失败路径：storage、IDB、Cookie、clipboard、console、URL、未知 API、外网边界", async ({
    page,
  }) => {
    const state = createProbeState([]);
    const consoleLines = collectConsole(page);
    await seedFakeLocalProjects(page);
    await installV1iRoutes(page, state);
    await gotoCreate(page);

    const { f1 } = await selectTwoFilesViaChooser(page);
    void f1;
    const baseline = await readStorageSnapshot(page);

    // 主动探测：两未知 API + 一外网，各精确一次
    await page.evaluate(async () => {
      await fetch("/api/unknown-v1i-probe").catch(() => undefined);
      await fetch("/api/projects/unknown-v1i-probe").catch(() => undefined);
      await fetch("https://example.invalid/v1i-probe").catch(() => undefined);
    });
    await expect
      .poll(() => state.forbiddenHits.length)
      .toBe(2);
    await expect
      .poll(() => state.externalHits.length)
      .toBe(1);
    expect(state.forbiddenHits).toEqual([
      "GET /api/unknown-v1i-probe",
      "GET /api/projects/unknown-v1i-probe",
    ]);
    expect(state.externalHits).toEqual([
      "GET https://example.invalid/v1i-probe",
    ]);

    // create 失败路径边界
    state.createFail = true;
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page.getByText(CREATE_ERROR)).toBeVisible({ timeout: 10_000 });
    await assertCreatePageBoundary(page, baseline, "边界用例 create 失败");
    expect(page.url()).not.toContain(ANCHOR_1);
    expect(state.filePosts.length).toBe(0);
    // 失败路径不得新增 forbidden/external
    expect(state.forbiddenHits).toEqual([
      "GET /api/unknown-v1i-probe",
      "GET /api/projects/unknown-v1i-probe",
    ]);
    expect(state.externalHits).toEqual([
      "GET https://example.invalid/v1i-probe",
    ]);

    // 成功路径
    state.createFail = false;
    await page.getByRole("button", { name: "开始生成技术标" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/technical-plan/${REAL_CREATE_ID}/document`),
      { timeout: 20_000 },
    );
    expect(state.filePosts.length).toBe(2);
    assertAnchorsOnlyInMatchingMultipart(state);
    assertFilePostExact(state.filePosts[0], {
      projectId: REAL_CREATE_ID,
      filename: FILE1_NAME,
      anchor: ANCHOR_1,
    });
    assertFilePostExact(state.filePosts[1], {
      projectId: REAL_CREATE_ID,
      filename: FILE2_NAME,
      anchor: ANCHOR_2,
    });

    const okSnap = await readStorageSnapshot(page);
    assertTechWorkspaceLocalKeys(okSnap, REAL_CREATE_ID);
    expect(okSnap.ssKeys.includes(PENDING_SS_KEY)).toBe(false);
    expect(okSnap.cookies).toBe("");
    expect(await readIdbNames(page)).toEqual([]);
    const clip = await readClipboardProbe(page);
    expect(clip.installed).toBe(true);
    expect(clip.read).toBe(0);
    expect(clip.write).toBe(0);
    expect(page.url()).not.toContain(ANCHOR_1);
    expect(page.url()).not.toContain(ANCHOR_2);

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain(SECRET);
    expect(bodyText).not.toContain(ANCHOR_1);
    assertCleanConsole(consoleLines);

    // 最终集合精确等于预期探针，零其它成员
    expect(state.forbiddenHits).toEqual([
      "GET /api/unknown-v1i-probe",
      "GET /api/projects/unknown-v1i-probe",
    ]);
    expect(state.externalHits).toEqual([
      "GET https://example.invalid/v1i-probe",
    ]);
  });
});
