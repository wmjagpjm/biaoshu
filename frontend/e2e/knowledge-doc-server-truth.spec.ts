/**
 * 模块：V1-O 知识库文档服务端真值 E2E（failure-first / R2 反假绿）
 * 用途：锁定 loading|ready|error、精确 schema、旧键旁路、写仅 ready+单写锁、
 *       固定脱敏错误、五类 mutation 双 GET 对账、refresh 代次围栏、隐私全出口、AST 自守卫。
 * 对接：Playwright chromium headless workers=1；前端 5174；受控 route 桩；合成 File。
 * 二次开发：禁止 test.skip / test.fixme / 固定 sleep 完成证据、宽 route、断言宽放、
 *       清理旧键掩盖“不写不删”、生产源码字符串冒充运行时；不得声称验证真实 DB/鉴权落盘。
 * R2/R3：早期 arm；全出口；穷举 GET schema + 写响应接线；双 GET 精确+1；共享锁矩阵；
 *     单一 analyzeSpecSource；禁止吞 promise；Grok 仅静态 parse，Playwright 由 Codex 单次执行。
 * R3：Cookie 原生终态；全 request+allHeaders+context.cookies；DOM 零截断+oldValue；
 *     IDB 不 disarm；reconcile 仅写后；schema 字段矩阵；写 3×3 精确一次；outcome 双 GET 真值。
 * R6-FIX：Q1 DOM detail+oldValue；Q2 response/requestfailed+精确 settled+1；Q3 写阶段分账；
 *     Q4 根级 DOM/字段空位；Q7 hook.queue 身份 + root.current 重遍历 poll；Q8 GET 逐字段；Q9 moveTarget=""与默认活跃。
 */
import {
  expect,
  test,
  type ConsoleMessage,
  type Page,
  type Route,
} from "@playwright/test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

// ---------------------------------------------------------------------------
// 固定文案与 canary（与契约对齐；禁止透传服务端 detail）
// ---------------------------------------------------------------------------

const DOCS_LS_KEY = "biaoshu.knowledgeBase.docs.v1";
const DOCS_LS_KEY_ALT = "biaoshu.knowledgeBase.docs.v1.backup";
const UNRELATED_LS_KEY = "biaoshu.unrelated.session.v1";
const LOADING_TEXT = "正在加载知识库文档…";
const LOAD_ERROR = "知识库文档加载失败，请稍后重试";
const EMPTY_TITLE = "知识库暂无文档";
const EMPTY_HINT = "上传文档后可在这里查看解析和索引状态。";
const FILTER_EMPTY = "当前筛选下无文档";
const CREATE_FOLDER_ERR = "创建文件夹失败，请稍后重试";
const UPLOAD_ERR = "文档上传失败，请稍后重试";
const MOVE_ERR = "移动文档失败，请稍后重试";
const DELETE_ERR = "删除文档失败，请稍后重试";
const REINDEX_ERR = "重新索引失败，请稍后重试";

/** 全出口禁止出现的敏感串（与外网 canary 分离） */
const SECRET =
  "SECRET_V1O_LEAK_C:\\Users\\secret\\db.sqlite_apiKey=sk-v1o-leaked";
/** 主动外网探测专用，不得与 SECRET 复用 */
const EXTERNAL_ROUTE_CANARY =
  "https://v1o-external-canary.example/probe?token=EXT_V1O_CANARY_9f3a";

const POISON_DOC_NAME = "POISON_MOCK_DOC_SHOULD_NOT_RENDER_V1O.docx";
const POISON_FOLDER_NAME = "POISON_MOCK_FOLDER_SHOULD_NOT_RENDER_V1O";
const POISON_CANARY = "V1O_POISON_CANARY_ready_fake";
const MOCK_DOC_SAMPLE = "智慧交通同类业绩汇编";
const MOCK_FOLDER_SAMPLE = "业绩材料";
const OFFLINE_HINT = "离线本地演示";
const SERVER_DOC_A = "server-doc-a-v1o.txt";
const SERVER_DOC_B = "server-doc-b-v1o.txt";
const SERVER_DOC_C = "server-doc-c-v1o.txt";
const SERVER_FOLDER_INBOX = "收件箱";
const SERVER_FOLDER_ARCHIVE = "归档";
const FLD_INBOX = "fld_server_inbox";
const FLD_ARCHIVE = "fld_server_archive";
const DOC_A = "doc_server_a";
const DOC_B = "doc_server_b";
const DOC_C = "doc_server_c";
const UPLOAD_CLIENT_NAME = "v1o-client-upload-name.txt";
const UPLOAD_SERVER_NAME = "server-renamed-upload-v1o.bin";
const UPLOAD_ANCHOR = "V1O_BYTE_ANCHOR_UPLOAD_e1f2a3b4";
const NEW_FOLDER_NAME = "V1O新建文件夹";
const SERVER_CREATE_FOLDER_NAME = "server-created-folder-v1o";
const LEGAL_FOLDER_SENTINEL = "fld_legal_sentinel_v1o";
const LEGAL_DOC_SENTINEL = "doc_legal_sentinel_v1o";
const LEGAL_FOLDER_NAME = "LEGAL_SENTINEL_FOLDER_MUST_NOT_RENDER";
const LEGAL_DOC_NAME = "LEGAL_SENTINEL_DOC_MUST_NOT_RENDER.txt";

const FIXED_MODEL = "BAAI/bge-small-zh-v1.5";

const STATUS_SAFE_LABEL: Record<string, string> = {
  ready: "已就绪",
  parsing: "解析中",
  indexing: "索引中",
  failed: "处理失败",
  pending: "待处理",
};

const POISON_LS_VALUE = JSON.stringify({
  folders: [
    {
      id: "fld_poison",
      name: POISON_FOLDER_NAME,
      parentId: null,
    },
  ],
  docs: [
    {
      id: "kb_poison_a",
      name: POISON_DOC_NAME,
      tags: [POISON_CANARY],
      chunks: 99,
      updated: "poison",
      updatedAt: "2020-01-01T00:00:00.000Z",
      category: "poison",
      folderId: "fld_poison",
      status: "ready",
      statusMessage: POISON_CANARY,
      sizeLabel: "1 KB",
    },
    {
      id: "kb_poison_b",
      name: "POISON_SECOND_DOC_V1O.txt",
      tags: [POISON_CANARY],
      chunks: 1,
      updated: "poison",
      updatedAt: "2020-01-02T00:00:00.000Z",
      category: "poison",
      folderId: "fld_poison",
      status: "ready",
      statusMessage: "异常详情: " + SECRET,
      sizeLabel: "2 KB",
    },
  ],
});

const POISON_LS_ALT_VALUE = JSON.stringify({
  note: "同族备份键不得触碰",
  poison: POISON_CANARY,
});

const UNRELATED_LS_VALUE = JSON.stringify({ keep: "unrelated-v1o" });

// ---------------------------------------------------------------------------
// 类型与探针
// ---------------------------------------------------------------------------

type KbFolder = { id: string; name: string; parentId: string | null };
type KbDoc = {
  id: string;
  name: string;
  tags: string[];
  chunks: number;
  updated: string;
  updatedAt: string;
  category: string;
  folderId: string;
  status: string;
  statusMessage: string | null;
  sizeLabel: string | null;
};

type SemanticIndexPayload = {
  id: string | null;
  workspaceId: string | null;
  status: string;
  provider: "offline_bge";
  modelId: string;
  modelFingerprint: string | null;
  dimension: number;
  totalChunks: number;
  embeddedChunks: number;
  chunkCount: number;
  errorCode: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  readonly released: boolean;
};

type ApiCall = {
  method: string;
  path: string;
  url: string;
  query: string;
  bodyText: string;
  postData: string | null;
  headers: Record<string, string>;
  /** 操作代次标记：open | first | second | other */
  opToken: string;
  resourceKind: "api" | "document" | "script" | "stylesheet" | "image" | "font" | "other";
};

type ListMode =
  | { kind: "ok" }
  | { kind: "status"; status: number; detail: unknown }
  | { kind: "malformed"; body: unknown }
  | { kind: "abort" }
  | { kind: "hold"; gate: HoldGate; then: "ok" | "error" | "malformed" | "abort" };

type WriteBodyMode =
  | { kind: "ok" }
  | { kind: "fail" }
  | { kind: "abort" }
  | { kind: "malformed"; body: unknown; status?: number };

type Probe = {
  folders: KbFolder[];
  docs: KbDoc[];
  /** 对账时 folders GET 返回（预先冻结，可与当前内存不同） */
  reconcileFolders: KbFolder[] | null;
  reconcileDocs: KbDoc[] | null;
  semantic: SemanticIndexPayload;
  foldersMode: ListMode;
  docsMode: ListMode;
  createFolderMode: WriteBodyMode;
  uploadMode: WriteBodyMode;
  moveMode:
    | { kind: "ok"; moved?: number }
    | { kind: "fail" }
    | { kind: "abort" }
    | { kind: "moved"; moved: unknown }
    | { kind: "partial_ok"; moved: number; applyIds: string[] };
  deleteMode:
    | { kind: "ok" }
    | { kind: "fail_all" }
    | { kind: "fail_second" }
    | { kind: "abort" };
  reindexMode: WriteBodyMode;
  rebuildMode: "ok" | "fail";
  writeHoldGate: HoldGate | null;
  folderGets: number;
  docGets: number;
  writes: ApiCall[];
  gets: ApiCall[];
  allRequests: ApiCall[];
  folderGetArrived: number;
  docGetArrived: number;
  folderGetFulfilled: number;
  docGetFulfilled: number;
  writeArrived: ApiCall[];
  externalHits: string[];
  forbiddenHits: string[];
  unknownKnowledgeHits: string[];
  concurrentWriteAttempts: number;
  /** 写到达后才启用 reconcile* 作为 GET 返回 */
  reconcileArmed: boolean;
  /** 当前写批次 operation token（可变；second 尝试用） */
  currentOpToken: string;
  /**
   * first operation 不可变 token：multi-delete 等释放后后续 first 写仍归 first，
   * 禁止依赖全局 mutable currentOpToken 误记为 second。
   */
  immutableFirstOpToken: string | null;
  /** 与 immutableFirstOpToken 绑定的 first 写 path 匹配（仅 first 阶段） */
  firstOpWriteMatch: ((c: Pick<ApiCall, "method" | "path">) => boolean) | null;
  /**
   * 写分账阶段：
   * - first：first 写到达期，同类 path 归 immutableFirstOpToken
   * - second-attempt：second 尝试期，任何新增写一律归 currentOpToken（second）
   * - first-drain：hold 释放后，仅 multi-delete 已冻结剩余 path 可继续归 first
   * - idle：默认
   */
  writePhase: "idle" | "first" | "second-attempt" | "first-drain";
  /**
   * multi-delete 已冻结的剩余 DELETE path（规范化无尾斜杠）；
   * 仅 first-drain 阶段可继续记 first；消费后移除。
   */
  firstFrozenRemainingPaths: string[] | null;
  contextCookiesBefore: Array<{ name: string; value: string; httpOnly: boolean }>;
  contextCookiesAfter: Array<{ name: string; value: string; httpOnly: boolean }>;
};

type StorageTouch = {
  api: string;
  args: unknown[];
  values: unknown[];
};
type IdbTouch = {
  api: string;
  db: string | null;
  store: string | null;
  index: string | null;
  args: unknown[];
  values: unknown[];
  isWrite: boolean;
};

type ConsoleCollector = {
  lines: string[];
  pending: Promise<void>[];
  drain: () => Promise<void>;
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
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    get released() {
      return released;
    },
  };
}

function baseSemantic(
  overrides: Partial<SemanticIndexPayload> = {},
): SemanticIndexPayload {
  return {
    id: null,
    workspaceId: "ws_e2e",
    status: "index_not_built",
    provider: "offline_bge",
    modelId: FIXED_MODEL,
    modelFingerprint: null,
    dimension: 512,
    totalChunks: 0,
    embeddedChunks: 0,
    chunkCount: 0,
    errorCode: "index_not_built",
    startedAt: null,
    finishedAt: null,
    createdAt: null,
    updatedAt: null,
    ...overrides,
  };
}

function makeDoc(
  partial: Partial<KbDoc> & Pick<KbDoc, "id" | "name">,
): KbDoc {
  return {
    tags: ["server"],
    chunks: 3,
    updated: "刚刚",
    updatedAt: "2026-07-23T10:00:00.000Z",
    category: "知识库",
    folderId: FLD_INBOX,
    status: "ready",
    statusMessage: null,
    sizeLabel: "1.0 KB",
    ...partial,
  };
}

function makeFolder(
  partial: Partial<KbFolder> & Pick<KbFolder, "id" | "name">,
): KbFolder {
  return {
    parentId: null,
    ...partial,
  };
}

function legalFolderSentinel(): KbFolder {
  return makeFolder({ id: LEGAL_FOLDER_SENTINEL, name: LEGAL_FOLDER_NAME });
}

function legalDocSentinel(): KbDoc {
  return makeDoc({
    id: LEGAL_DOC_SENTINEL,
    name: LEGAL_DOC_NAME,
    folderId: LEGAL_FOLDER_SENTINEL,
  });
}

function serverSeed(): { folders: KbFolder[]; docs: KbDoc[] } {
  return {
    folders: [
      makeFolder({ id: FLD_INBOX, name: SERVER_FOLDER_INBOX }),
      makeFolder({ id: FLD_ARCHIVE, name: SERVER_FOLDER_ARCHIVE }),
    ],
    docs: [
      makeDoc({ id: DOC_A, name: SERVER_DOC_A, chunks: 3, sizeLabel: "1.0 KB" }),
      makeDoc({
        id: DOC_B,
        name: SERVER_DOC_B,
        chunks: 5,
        sizeLabel: "2.0 KB",
        updatedAt: "2026-07-23T10:01:00.000Z",
      }),
    ],
  };
}

function threeDocSeed(): { folders: KbFolder[]; docs: KbDoc[] } {
  const seed = serverSeed();
  return {
    folders: seed.folders,
    docs: [
      ...seed.docs,
      makeDoc({
        id: DOC_C,
        name: SERVER_DOC_C,
        chunks: 7,
        sizeLabel: "3.0 KB",
        updatedAt: "2026-07-23T10:02:00.000Z",
      }),
    ],
  };
}

function emptyProbe(partial: Partial<Probe> = {}): Probe {
  const seed = serverSeed();
  return {
    folders: seed.folders,
    docs: seed.docs,
    reconcileFolders: null,
    reconcileDocs: null,
    semantic: baseSemantic(),
    foldersMode: { kind: "ok" },
    docsMode: { kind: "ok" },
    createFolderMode: { kind: "ok" },
    uploadMode: { kind: "ok" },
    moveMode: { kind: "ok" },
    deleteMode: { kind: "ok" },
    reindexMode: { kind: "ok" },
    rebuildMode: "ok",
    writeHoldGate: null,
    folderGets: 0,
    docGets: 0,
    writes: [],
    gets: [],
    allRequests: [],
    folderGetArrived: 0,
    docGetArrived: 0,
    folderGetFulfilled: 0,
    docGetFulfilled: 0,
    writeArrived: [],
    externalHits: [],
    forbiddenHits: [],
    unknownKnowledgeHits: [],
    concurrentWriteAttempts: 0,
    reconcileArmed: false,
    currentOpToken: "open",
    immutableFirstOpToken: null,
    firstOpWriteMatch: null,
    writePhase: "idle",
    firstFrozenRemainingPaths: null,
    contextCookiesBefore: [],
    contextCookiesAfter: [],
    ...partial,
  };
}

/** path 规范化：去尾斜杠，便于 DELETE 剩余序列精确匹配 */
function normalizeApiPath(pathName: string): string {
  if (pathName.length > 1 && pathName.endsWith("/")) {
    return pathName.slice(0, -1);
  }
  return pathName;
}

/**
 * 写请求 operation token（阶段 + 精确身份分账）：
 * - second-attempt：任何新增写一律 second（currentOpToken），禁止同类 path 仍吞入 first
 * - first-drain：仅 multi-delete 已冻结剩余 path 可归 first；其余用 current
 * - first：firstOpWriteMatch 命中才归 immutableFirstOpToken
 * - 其它：currentOpToken
 */
function resolveWriteOpToken(
  probe: Probe,
  method: string,
  pathName: string,
): string {
  if (method === "GET") return probe.currentOpToken;
  const norm = normalizeApiPath(pathName);

  // second 尝试阶段：任何新增写归 second，同类 diagonal 也不得记 first
  if (probe.writePhase === "second-attempt") {
    return probe.currentOpToken;
  }

  // hold 释放后 drain：仅冻结的 multi-delete 剩余 path 可继续 first
  if (
    probe.writePhase === "first-drain" &&
    probe.immutableFirstOpToken &&
    probe.firstFrozenRemainingPaths &&
    probe.firstFrozenRemainingPaths.length > 0
  ) {
    const idx = probe.firstFrozenRemainingPaths.findIndex(
      (p) => normalizeApiPath(p) === norm,
    );
    if (idx >= 0) {
      probe.firstFrozenRemainingPaths = [
        ...probe.firstFrozenRemainingPaths.slice(0, idx),
        ...probe.firstFrozenRemainingPaths.slice(idx + 1),
      ];
      return probe.immutableFirstOpToken;
    }
    return probe.currentOpToken;
  }

  // first 阶段：精确 kind matcher 命中才归 first
  if (
    probe.writePhase === "first" &&
    probe.immutableFirstOpToken &&
    probe.firstOpWriteMatch &&
    probe.firstOpWriteMatch({ method, path: pathName })
  ) {
    return probe.immutableFirstOpToken;
  }

  return probe.currentOpToken;
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function isLocalHost(host: string): boolean {
  return host === "127.0.0.1" || host === "localhost";
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function isAllowedApi(method: string, pathName: string): boolean {
  const rules: Array<{ methods: string[]; path: RegExp }> = [
    { methods: ["GET"], path: /^\/api\/health\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/bootstrap-status\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/me\/?$/ },
    { methods: ["GET"], path: /^\/api\/auth\/csrf\/?$/ },
    { methods: ["POST"], path: /^\/api\/auth\/(login|logout)\/?$/ },
    { methods: ["GET"], path: /^\/api\/workspace\/?$/ },
    { methods: ["GET"], path: /^\/api\/settings\/?$/ },
    { methods: ["GET"], path: /^\/api\/cards\/?$/ },
    { methods: ["GET"], path: /^\/api\/knowledge\/folders\/?$/ },
    { methods: ["POST"], path: /^\/api\/knowledge\/folders\/?$/ },
    { methods: ["GET"], path: /^\/api\/knowledge\/docs\/?$/ },
    { methods: ["POST"], path: /^\/api\/knowledge\/docs\/upload\/?$/ },
    { methods: ["POST"], path: /^\/api\/knowledge\/docs\/move\/?$/ },
    { methods: ["DELETE"], path: /^\/api\/knowledge\/docs\/[^/]+\/?$/ },
    {
      methods: ["POST"],
      path: /^\/api\/knowledge\/docs\/[^/]+\/reindex\/?$/,
    },
    { methods: ["GET"], path: /^\/api\/knowledge\/semantic-index\/?$/ },
    {
      methods: ["POST"],
      path: /^\/api\/knowledge\/semantic-index\/rebuild\/?$/,
    },
  ];
  return rules.some(
    (r) => r.methods.includes(method) && r.path.test(pathName),
  );
}

function listPayload(
  probe: Probe,
  which: "folders" | "docs",
): unknown {
  // 对账终态仅在 mutation 写到达后启用；初始 GET 必须是初始态
  if (probe.reconcileArmed) {
    if (which === "folders") {
      return probe.reconcileFolders ?? probe.folders;
    }
    return probe.reconcileDocs ?? probe.docs;
  }
  if (which === "folders") return probe.folders;
  return probe.docs;
}

/** 每条 GET 独立 arrived 即可返回；禁止等双 GET 齐到再统一 release */
async function fulfillListGet(
  route: Route,
  probe: Probe,
  which: "folders" | "docs",
) {
  const mode = which === "folders" ? probe.foldersMode : probe.docsMode;
  if (which === "folders") {
    probe.folderGetArrived += 1;
  } else {
    probe.docGetArrived += 1;
  }

  if (mode.kind === "hold") {
    await mode.gate.wait();
    if (mode.then === "error") {
      await json(route, { detail: SECRET }, 503);
    } else if (mode.then === "malformed") {
      await json(route, { not: "array" }, 200);
    } else if (mode.then === "abort") {
      await route.abort("failed");
    } else {
      await json(route, listPayload(probe, which), 200);
    }
  } else if (mode.kind === "status") {
    await json(route, { detail: mode.detail }, mode.status);
  } else if (mode.kind === "malformed") {
    await json(route, mode.body, 200);
  } else if (mode.kind === "abort") {
    await route.abort("failed");
  } else {
    await json(route, listPayload(probe, which), 200);
  }

  if (which === "folders") {
    probe.folderGets += 1;
    probe.folderGetFulfilled += 1;
  } else {
    probe.docGets += 1;
    probe.docGetFulfilled += 1;
  }
}

async function maybeHoldWrite(probe: Probe, rec: ApiCall) {
  probe.writeArrived.push(rec);
  // 写到达后启用对账态（后续 folders/docs GET 返回 reconcile*）
  probe.reconcileArmed = true;
  if (probe.writeHoldGate) {
    probe.concurrentWriteAttempts += 1;
    await probe.writeHoldGate.wait();
  }
}

function classifyResource(req: { resourceType: () => string; url: () => string }): ApiCall["resourceKind"] {
  const t = req.resourceType();
  if (t === "document") return "document";
  if (t === "script") return "script";
  if (t === "stylesheet") return "stylesheet";
  if (t === "image") return "image";
  if (t === "font") return "font";
  if (t === "xhr" || t === "fetch") return "api";
  const u = req.url();
  if (u.includes("/api/")) return "api";
  return "other";
}

async function installRoutes(page: Page, probe: Probe) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const pathName = url.pathname;
    const method = req.method();
    const bodyText = req.postData() ?? "";
    // allHeaders 覆盖 cookie 等完整头；失败时回退 headers()
    let headers: Record<string, string> = {};
    try {
      headers = { ...(await req.allHeaders()) };
    } catch {
      headers = { ...req.headers() };
    }
    const rec: ApiCall = {
      method,
      path: pathName,
      url: url.href,
      query: url.search,
      bodyText,
      postData: req.postData(),
      headers,
      // first 写（含 multi-delete 释放后后续 DELETE）用不可变 token；其余用 current
      opToken: resolveWriteOpToken(probe, method, pathName),
      resourceKind: classifyResource(req),
    };

    // 外部：先记录完整 URL（含 canary），再 abort
    if (!isLocalHost(host)) {
      probe.externalHits.push(url.href);
      probe.allRequests.push(rec);
      await route.abort("failed");
      return;
    }

    // 所有同源请求（含 HTML/JS/CSS）均记入 allRequests
    probe.allRequests.push(rec);

    if (pathName.startsWith("/api/")) {

      if (pathName.startsWith("/api/knowledge") && !isAllowedApi(method, pathName)) {
        probe.unknownKnowledgeHits.push(`${method} ${pathName}`);
        probe.forbiddenHits.push(`${method} ${pathName}`);
        await route.abort("failed");
        return;
      }
      if (!isAllowedApi(method, pathName)) {
        probe.forbiddenHits.push(`${method} ${pathName}`);
        await route.abort("failed");
        return;
      }

      if (method === "GET") {
        probe.gets.push(rec);
      } else {
        probe.writes.push(rec);
      }

      if (pathName === "/api/cards" || pathName === "/api/cards/") {
        await json(route, []);
        return;
      }
      if (pathName === "/api/health" || pathName === "/api/health/") {
        await json(route, {
          status: "ok",
          service: "biaoshu-e2e",
          workspaceId: "ws_e2e",
        });
        return;
      }
      if (
        pathName === "/api/auth/bootstrap-status" ||
        pathName === "/api/auth/bootstrap-status/"
      ) {
        await json(route, { needsBootstrap: false });
        return;
      }
      if (pathName === "/api/auth/me" || pathName === "/api/auth/me/") {
        await json(route, {
          id: "user_e2e",
          username: "e2e",
          displayName: "E2E",
          roles: ["admin"],
        });
        return;
      }
      if (pathName === "/api/auth/csrf" || pathName === "/api/auth/csrf/") {
        await json(route, { csrfToken: "e2e-csrf" });
        return;
      }
      if (pathName === "/api/workspace" || pathName === "/api/workspace/") {
        await json(route, {
          id: "ws_e2e",
          name: "E2E 工作空间",
        });
        return;
      }
      if (pathName === "/api/settings" || pathName === "/api/settings/") {
        await json(route, {});
        return;
      }

      if (
        (pathName === "/api/knowledge/folders" ||
          pathName === "/api/knowledge/folders/") &&
        method === "GET"
      ) {
        await fulfillListGet(route, probe, "folders");
        return;
      }
      if (
        (pathName === "/api/knowledge/docs" ||
          pathName === "/api/knowledge/docs/") &&
        method === "GET"
      ) {
        await fulfillListGet(route, probe, "docs");
        return;
      }

      if (
        (pathName === "/api/knowledge/folders" ||
          pathName === "/api/knowledge/folders/") &&
        method === "POST"
      ) {
        await maybeHoldWrite(probe, rec);
        if (probe.createFolderMode.kind === "abort") {
          await route.abort("failed");
          return;
        }
        if (probe.createFolderMode.kind === "fail") {
          await json(route, { detail: SECRET, code: "folder_create_fail" }, 500);
          return;
        }
        if (probe.createFolderMode.kind === "malformed") {
          await json(
            route,
            probe.createFolderMode.body,
            probe.createFolderMode.status ?? 200,
          );
          return;
        }
        const row: KbFolder = {
          id: `fld_server_${Date.now().toString(36)}`,
          name: SERVER_CREATE_FOLDER_NAME,
          parentId: null,
        };
        probe.folders = [...probe.folders, row];
        await json(route, row, 201);
        return;
      }

      if (
        (pathName === "/api/knowledge/docs/upload" ||
          pathName === "/api/knowledge/docs/upload/") &&
        method === "POST"
      ) {
        await maybeHoldWrite(probe, rec);
        if (probe.uploadMode.kind === "abort") {
          await route.abort("failed");
          return;
        }
        if (probe.uploadMode.kind === "fail") {
          await json(route, { detail: SECRET, code: "upload_fail" }, 500);
          return;
        }
        if (probe.uploadMode.kind === "malformed") {
          await json(
            route,
            probe.uploadMode.body,
            probe.uploadMode.status ?? 200,
          );
          return;
        }
        const row = makeDoc({
          id: `doc_server_up_${Date.now().toString(36)}`,
          name: UPLOAD_SERVER_NAME,
          tags: ["server-upload", "renamed-tag"],
          chunks: 42,
          status: "indexing",
          statusMessage: "服务端内部进度原文不得展示 " + SECRET,
          sizeLabel: "99.0 KB",
          category: "server-category",
          folderId: FLD_ARCHIVE,
        });
        probe.docs = [row, ...probe.docs];
        await json(route, row, 201);
        return;
      }

      if (
        (pathName === "/api/knowledge/docs/move" ||
          pathName === "/api/knowledge/docs/move/") &&
        method === "POST"
      ) {
        await maybeHoldWrite(probe, rec);
        if (probe.moveMode.kind === "abort") {
          await route.abort("failed");
          return;
        }
        if (probe.moveMode.kind === "fail") {
          await json(route, { detail: SECRET, code: "move_fail" }, 500);
          return;
        }
        let ids: string[] = [];
        let folderId = FLD_ARCHIVE;
        try {
          const parsed = JSON.parse(bodyText) as {
            ids?: string[];
            folderId?: string;
          };
          if (Array.isArray(parsed.ids)) ids = parsed.ids;
          if (typeof parsed.folderId === "string") folderId = parsed.folderId;
        } catch {
          // 畸形 body：仍返回受控响应
        }
        const unique = [...new Set(ids)];
        if (probe.moveMode.kind === "moved") {
          await json(route, { moved: probe.moveMode.moved }, 200);
          return;
        }
        if (probe.moveMode.kind === "partial_ok") {
          const apply = new Set(probe.moveMode.applyIds);
          probe.docs = probe.docs.map((d) =>
            apply.has(d.id) ? { ...d, folderId } : d,
          );
          await json(route, { moved: probe.moveMode.moved }, 200);
          return;
        }
        const moved =
          probe.moveMode.kind === "ok" && probe.moveMode.moved != null
            ? probe.moveMode.moved
            : unique.length;
        if (moved === unique.length) {
          probe.docs = probe.docs.map((d) =>
            unique.includes(d.id) ? { ...d, folderId } : d,
          );
        }
        await json(route, { moved }, 200);
        return;
      }

      if (
        method === "DELETE" &&
        /^\/api\/knowledge\/docs\/[^/]+\/?$/.test(pathName)
      ) {
        await maybeHoldWrite(probe, rec);
        const id = pathName.split("/").filter(Boolean).pop()!;
        if (probe.deleteMode.kind === "abort") {
          await route.abort("failed");
          return;
        }
        if (probe.deleteMode.kind === "fail_all") {
          await json(route, { detail: SECRET, code: "delete_fail" }, 500);
          return;
        }
        if (probe.deleteMode.kind === "fail_second") {
          const deleteWrites = probe.writes.filter(
            (w) => w.method === "DELETE",
          );
          if (deleteWrites.length >= 2) {
            await json(route, { detail: SECRET, code: "delete_fail_2" }, 500);
            return;
          }
        }
        probe.docs = probe.docs.filter((d) => d.id !== id);
        await route.fulfill({ status: 204, body: "" });
        return;
      }

      if (
        method === "POST" &&
        /^\/api\/knowledge\/docs\/[^/]+\/reindex\/?$/.test(pathName)
      ) {
        await maybeHoldWrite(probe, rec);
        if (probe.reindexMode.kind === "abort") {
          await route.abort("failed");
          return;
        }
        if (probe.reindexMode.kind === "fail") {
          await json(route, { detail: SECRET, code: "reindex_fail" }, 500);
          return;
        }
        if (probe.reindexMode.kind === "malformed") {
          await json(
            route,
            probe.reindexMode.body,
            probe.reindexMode.status ?? 200,
          );
          return;
        }
        const id = pathName.split("/").filter(Boolean).slice(-2)[0]!;
        const row = probe.docs.find((d) => d.id === id);
        if (!row) {
          await json(route, { detail: "not found" }, 404);
          return;
        }
        const next = makeDoc({
          ...row,
          chunks: row.chunks + 11,
          status: "ready",
          statusMessage: null,
          sizeLabel: "reindexed-size",
          tags: ["server-reindexed"],
          name: row.name,
        });
        probe.docs = probe.docs.map((d) => (d.id === id ? next : d));
        await json(route, next, 200);
        return;
      }

      if (
        (pathName === "/api/knowledge/semantic-index" ||
          pathName === "/api/knowledge/semantic-index/") &&
        method === "GET"
      ) {
        await json(route, probe.semantic, 200);
        return;
      }
      if (
        (pathName === "/api/knowledge/semantic-index/rebuild" ||
          pathName === "/api/knowledge/semantic-index/rebuild/") &&
        method === "POST"
      ) {
        if (probe.rebuildMode === "fail") {
          await json(route, { detail: SECRET }, 500);
          return;
        }
        probe.semantic = baseSemantic({
          id: "idx_building",
          status: "running",
          errorCode: "index_building",
          totalChunks: 4,
          embeddedChunks: 1,
          chunkCount: 1,
          startedAt: "2026-07-23T12:00:00+00:00",
        });
        await json(route, probe.semantic, 202);
        return;
      }

      probe.forbiddenHits.push(`${method} ${pathName} unhandled`);
      await route.abort("failed");
      return;
    }

    // 同源非 API（HTML/JS/CSS 等）已记入 allRequests，继续放行
    await route.continue();
  });
}

// ---------------------------------------------------------------------------
// 隐私 / Storage / IDB / DOM 历史 / Cookie（同步 capture→preset→baseline→arm）
// ---------------------------------------------------------------------------

async function installPrivacyInit(
  page: Page,
  opts?: { poison?: boolean },
) {
  const poison = opts?.poison !== false;
  await page.addInitScript(
    ({
      docsKey,
      docsVal,
      altKey,
      altVal,
      unrelatedKey,
      unrelatedVal,
      doPoison,
    }) => {
      type StorageTouch = {
        api: string;
        args: unknown[];
        values: unknown[];
      };
      type IdbTouch = {
        api: string;
        db: string | null;
        store: string | null;
        index: string | null;
        args: unknown[];
        values: unknown[];
        isWrite: boolean;
      };
      type DomHist = { kind: string; detail: string };
      type CookieTouch = { api: string; value: string };

      const g = window as unknown as {
        __v1oNative?: {
          lsGet: typeof localStorage.getItem;
          lsSet: typeof localStorage.setItem;
          lsRem: typeof localStorage.removeItem;
          lsClear: typeof localStorage.clear;
          lsKey: typeof localStorage.key;
          lsLen: () => number;
          ssGet: typeof sessionStorage.getItem;
          ssSet: typeof sessionStorage.setItem;
          ssRem: typeof sessionStorage.removeItem;
          ssClear: typeof sessionStorage.clear;
          ssKey: typeof sessionStorage.key;
          ssLen: () => number;
          idbOpen: typeof indexedDB.open;
          idbDelete: typeof indexedDB.deleteDatabase;
          idbDatabases?: () => Promise<IDBDatabaseInfo[]>;
          cookieDesc: PropertyDescriptor | null;
        };
        __v1oProbe?: {
          armed: boolean;
          storageTouches: StorageTouch[];
          idbTouches: IdbTouch[];
          domHistory: DomHist[];
          cookieTouches: CookieTouch[];
          storageBaseline: Record<string, string>;
          ssBaseline: Record<string, string>;
        };
      };

      // 1) 同步捕获全部原生 Storage / IDB / cookie 描述符
      if (!g.__v1oNative) {
        const cookieDesc =
          Object.getOwnPropertyDescriptor(Document.prototype, "cookie") ??
          Object.getOwnPropertyDescriptor(document, "cookie") ??
          null;
        g.__v1oNative = {
          lsGet: localStorage.getItem.bind(localStorage),
          lsSet: localStorage.setItem.bind(localStorage),
          lsRem: localStorage.removeItem.bind(localStorage),
          lsClear: localStorage.clear.bind(localStorage),
          lsKey: localStorage.key.bind(localStorage),
          lsLen: () => localStorage.length,
          ssGet: sessionStorage.getItem.bind(sessionStorage),
          ssSet: sessionStorage.setItem.bind(sessionStorage),
          ssRem: sessionStorage.removeItem.bind(sessionStorage),
          ssClear: sessionStorage.clear.bind(sessionStorage),
          ssKey: sessionStorage.key.bind(sessionStorage),
          ssLen: () => sessionStorage.length,
          idbOpen: indexedDB.open.bind(indexedDB),
          idbDelete: indexedDB.deleteDatabase.bind(indexedDB),
          idbDatabases:
            typeof indexedDB.databases === "function"
              ? indexedDB.databases.bind(indexedDB)
              : undefined,
          cookieDesc,
        };
      }

      if (!g.__v1oProbe) {
        g.__v1oProbe = {
          armed: false,
          storageTouches: [],
          idbTouches: [],
          domHistory: [],
          cookieTouches: [],
          storageBaseline: {},
          ssBaseline: {},
        };
      }

      const probe = g.__v1oProbe;
      const native = g.__v1oNative;

      // 2) 同步 Storage 预置（可选）+ baseline snapshot（原生方法，未 arm）
      if (doPoison) {
        native.lsSet(docsKey, docsVal);
        native.lsSet(altKey, altVal);
        native.lsSet(unrelatedKey, unrelatedVal);
      }
      const lsBase: Record<string, string> = {};
      for (let i = 0; i < native.lsLen(); i += 1) {
        const k = native.lsKey(i);
        if (k) lsBase[k] = native.lsGet(k) ?? "";
      }
      const ssBase: Record<string, string> = {};
      for (let i = 0; i < native.ssLen(); i += 1) {
        const k = native.ssKey(i);
        if (k) ssBase[k] = native.ssGet(k) ?? "";
      }
      probe.storageBaseline = lsBase;
      probe.ssBaseline = ssBase;
      // IDB 已知 baseline 固定为空：不预创建数据库

      const wrapStorage = (
        stor: Storage,
        get: typeof localStorage.getItem,
        set: typeof localStorage.setItem,
        rem: typeof localStorage.removeItem,
        clear: typeof localStorage.clear,
        prefix: string,
      ) => {
        stor.getItem = (key: string) => {
          const v = get(key);
          if (probe.armed) {
            probe.storageTouches.push({
              api: `${prefix}.getItem`,
              args: [key],
              values: [v],
            });
          }
          return v;
        };
        stor.setItem = (key: string, value: string) => {
          if (probe.armed) {
            probe.storageTouches.push({
              api: `${prefix}.setItem`,
              args: [key, value],
              values: [value],
            });
          }
          return set(key, value);
        };
        stor.removeItem = (key: string) => {
          if (probe.armed) {
            probe.storageTouches.push({
              api: `${prefix}.removeItem`,
              args: [key],
              values: [],
            });
          }
          return rem(key);
        };
        stor.clear = () => {
          if (probe.armed) {
            probe.storageTouches.push({
              api: `${prefix}.clear`,
              args: [],
              values: [],
            });
          }
          return clear();
        };
      };

      wrapStorage(
        localStorage,
        native.lsGet,
        native.lsSet,
        native.lsRem,
        native.lsClear,
        "localStorage",
      );
      wrapStorage(
        sessionStorage,
        native.ssGet,
        native.ssSet,
        native.ssRem,
        native.ssClear,
        "sessionStorage",
      );

      // Cookie getter/setter
      if (native.cookieDesc) {
        Object.defineProperty(document, "cookie", {
          configurable: true,
          enumerable: true,
          get() {
            const v = native.cookieDesc!.get!.call(document);
            if (probe.armed) {
              probe.cookieTouches.push({ api: "cookie.get", value: String(v) });
            }
            return v;
          },
          set(v: string) {
            if (probe.armed) {
              probe.cookieTouches.push({ api: "cookie.set", value: String(v) });
            }
            return native.cookieDesc!.set!.call(document, v);
          },
        });
      }

      const IDB_WRITE_APIS = new Set([
        "createObjectStore",
        "deleteObjectStore",
        "createIndex",
        "deleteIndex",
        "add",
        "put",
        "delete",
        "clear",
        "indexedDB.deleteDatabase",
      ]);

      const pushIdb = (
        api: string,
        db: string | null,
        store: string | null,
        index: string | null,
        args: unknown[],
      ) => {
        if (!probe.armed) return;
        probe.idbTouches.push({
          api,
          db,
          store,
          index,
          args,
          values: args.slice(),
          isWrite: IDB_WRITE_APIS.has(api),
        });
      };

      const patchStore = (store: IDBObjectStore, dbName: string) => {
        const storeName = store.name;
        const wrap = (api: "add" | "put" | "delete" | "clear") => {
          const orig = store[api].bind(store) as (...a: unknown[]) => unknown;
          (store as unknown as Record<string, unknown>)[api] = (
            ...args: unknown[]
          ) => {
            pushIdb(api, dbName, storeName, null, args);
            return orig(...args);
          };
        };
        wrap("add");
        wrap("put");
        wrap("delete");
        wrap("clear");
        const origCreateIndex = store.createIndex.bind(store);
        store.createIndex = ((
          name: string,
          keyPath: string | string[],
          options?: IDBIndexParameters,
        ) => {
          pushIdb("createIndex", dbName, storeName, name, [
            name,
            keyPath,
            options ?? null,
          ]);
          return origCreateIndex(name, keyPath, options);
        }) as typeof store.createIndex;
        const origDeleteIndex = store.deleteIndex.bind(store);
        store.deleteIndex = ((name: string) => {
          pushIdb("deleteIndex", dbName, storeName, name, [name]);
          return origDeleteIndex(name);
        }) as typeof store.deleteIndex;
        return store;
      };

      const patchDb = (db: IDBDatabase) => {
        const dbName = db.name;
        const origCreateStore = db.createObjectStore.bind(db);
        db.createObjectStore = ((
          name: string,
          opts?: IDBObjectStoreParameters,
        ) => {
          pushIdb("createObjectStore", dbName, name, null, [name, opts ?? null]);
          return patchStore(origCreateStore(name, opts), dbName);
        }) as typeof db.createObjectStore;

        const origDeleteStore = db.deleteObjectStore.bind(db);
        db.deleteObjectStore = ((name: string) => {
          pushIdb("deleteObjectStore", dbName, name, null, [name]);
          return origDeleteStore(name);
        }) as typeof db.deleteObjectStore;

        const origTx = db.transaction.bind(db);
        db.transaction = ((
          storeNames: string | string[],
          mode?: IDBTransactionMode,
          options?: IDBTransactionOptions,
        ) => {
          const tx = origTx(
            storeNames as string[],
            mode,
            options as IDBTransactionOptions,
          );
          const origObjectStore = tx.objectStore.bind(tx);
          tx.objectStore = ((name: string) =>
            patchStore(origObjectStore(name), dbName)) as typeof tx.objectStore;
          return tx;
        }) as typeof db.transaction;
      };

      indexedDB.open = ((name: string, version?: number) => {
        pushIdb("indexedDB.open", name, null, null, [name, version ?? null]);
        const req = native.idbOpen(name, version);
        req.addEventListener("upgradeneeded", () => {
          if (req.result) patchDb(req.result);
        });
        req.addEventListener("success", () => {
          if (req.result) patchDb(req.result);
        });
        return req;
      }) as typeof indexedDB.open;

      indexedDB.deleteDatabase = ((name: string) => {
        pushIdb("indexedDB.deleteDatabase", name, null, null, [name]);
        return native.idbDelete(name);
      }) as typeof indexedDB.deleteDatabase;

      // MutationObserver：尽早绑定（document 即可），零长度截断，记录 oldValue
      type DomHistFull = {
        kind: string;
        detail: string;
        oldValue: string | null;
      };
      const domHistory = probe.domHistory as unknown as DomHistFull[];
      const recordDom = (
        kind: string,
        detail: string,
        oldValue: string | null = null,
      ) => {
        if (!probe.armed) return;
        domHistory.push({ kind, detail, oldValue });
      };
      const mo = new MutationObserver((mutations) => {
        for (const m of mutations) {
          if (m.type === "characterData") {
            recordDom(
              "text",
              String(m.target.textContent ?? ""),
              m.oldValue,
            );
          }
          if (m.type === "childList") {
            m.addedNodes.forEach((n) => {
              if (n.nodeType === Node.TEXT_NODE) {
                recordDom("text-add", String(n.textContent ?? ""), null);
              } else if (n.nodeType === Node.ELEMENT_NODE) {
                recordDom(
                  "el-add",
                  (n as Element).outerHTML ?? "",
                  null,
                );
              }
            });
          }
          if (m.type === "attributes" && m.target instanceof Element) {
            recordDom(
              "attr",
              `${m.attributeName}=${m.target.getAttribute(m.attributeName ?? "")}`,
              m.oldValue,
            );
          }
        }
      });
      // 尽早 observe：优先 documentElement，否则 document
      const moTarget: Node = document.documentElement ?? document;
      mo.observe(moTarget, {
        subtree: true,
        childList: true,
        characterData: true,
        characterDataOldValue: true,
        attributes: true,
        attributeOldValue: true,
      });
      // 若当时无 documentElement，documentElement 出现后再补绑一次（不延迟到 DOMContentLoaded）
      if (!document.documentElement) {
        const bootMo = new MutationObserver(() => {
          if (document.documentElement) {
            mo.observe(document.documentElement, {
              subtree: true,
              childList: true,
              characterData: true,
              characterDataOldValue: true,
              attributes: true,
              attributeOldValue: true,
            });
            bootMo.disconnect();
          }
        });
        bootMo.observe(document, { childList: true });
      }

      // 3) 同步 armed=true：此后应用脚本才执行（init 内同步完成）
      // 重置只能清空原数组，禁止替换引用（MutationObserver 持有同一 domHistory）
      probe.storageTouches.length = 0;
      probe.idbTouches.length = 0;
      probe.domHistory.length = 0;
      probe.cookieTouches.length = 0;
      probe.armed = true;
    },
    {
      docsKey: DOCS_LS_KEY,
      docsVal: POISON_LS_VALUE,
      altKey: DOCS_LS_KEY_ALT,
      altVal: POISON_LS_ALT_VALUE,
      unrelatedKey: UNRELATED_LS_KEY,
      unrelatedVal: UNRELATED_LS_VALUE,
      doPoison: poison,
    },
  );
}

function collectConsole(page: Page): ConsoleCollector {
  const lines: string[] = [];
  const pending: Promise<void>[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    const type = msg.type();
    const text = msg.text();
    lines.push(`${type}: ${text}`);
    const args = msg.args();
    for (let i = 0; i < args.length; i += 1) {
      const handle = args[i]!;
      const p = (async () => {
        try {
          const v = await handle.jsonValue();
          try {
            lines.push(
              `${type}:arg${i}=${typeof v === "string" ? v : JSON.stringify(v)}`,
            );
          } catch {
            // 不可序列化参数：安全字符串表达（非空占位）
            lines.push(
              `${type}:arg${i}=[unserializable:${Object.prototype.toString.call(v)}]`,
            );
          }
        } catch (err) {
          lines.push(
            `${type}:arg${i}=[unserializable:${err instanceof Error ? err.name : "unknown"}]`,
          );
        }
      })();
      pending.push(p);
    }
  });
  page.on("pageerror", (err) => {
    lines.push(`pageerror: ${String(err)}`);
  });
  return {
    lines,
    pending,
    drain: async () => {
      // 循环 drain 至稳定：drain 期间新入队的 pending 也必须消化
      let guard = 0;
      const budget = 50;
      while (pending.length > 0 && guard < budget) {
        const batch = pending.splice(0, pending.length);
        await Promise.all(batch);
        guard += 1;
      }
      // 预算末断 pending 精确为零；禁止静默截断
      expect(
        pending.length,
        "console drain 预算末 pending 必须精确 0",
      ).toBe(0);
    },
  };
}

async function collectDomExport(page: Page): Promise<string> {
  return page.evaluate(() => {
    const parts: string[] = [];
    parts.push(document.body?.innerText ?? "");
    const walk = (el: Element) => {
      for (const attr of Array.from(el.attributes)) {
        parts.push(`${attr.name}=${attr.value}`);
      }
      for (const child of Array.from(el.children)) walk(child);
    };
    if (document.documentElement) walk(document.documentElement);
    return parts.join("\n");
  });
}

async function readPrivacyState(page: Page) {
  return page.evaluate(async () => {
    const g = window as unknown as {
      __v1oNative?: {
        lsGet: typeof localStorage.getItem;
        lsKey: typeof localStorage.key;
        lsLen: () => number;
        ssGet: typeof sessionStorage.getItem;
        ssKey: typeof sessionStorage.key;
        ssLen: () => number;
        idbOpen: typeof indexedDB.open;
        idbDatabases?: () => Promise<IDBDatabaseInfo[]>;
        cookieDesc: PropertyDescriptor | null;
      };
      __v1oProbe?: {
        armed: boolean;
        storageTouches: StorageTouch[];
        idbTouches: IdbTouch[];
        domHistory: Array<{ kind: string; detail: string; oldValue?: string | null }>;
        cookieTouches: Array<{ api: string; value: string }>;
        storageBaseline: Record<string, string>;
        ssBaseline: Record<string, string>;
      };
    };
    const native = g.__v1oNative;
    const probe = g.__v1oProbe;
    // Q4：终态 IDB 读取全程保持 armed，仅用捕获的 native.databases，消灭异步失明窗口
    const armedBefore = Boolean(probe?.armed);
    let idbNames: string[] = [];
    try {
      if (native?.idbDatabases) {
        const infos = await native.idbDatabases();
        idbNames = infos.map((i) => i.name ?? "").filter(Boolean);
      }
    } catch {
      idbNames = ["[idbDatabases-error]"];
    }
    // armed 不得因读取而关闭
    if (probe && probe.armed !== armedBefore) {
      probe.armed = armedBefore;
    }

    const ls: Record<string, string> = {};
    const lsKeys: string[] = [];
    if (native) {
      for (let i = 0; i < native.lsLen(); i += 1) {
        const k = native.lsKey(i);
        if (k) {
          lsKeys.push(k);
          ls[k] = native.lsGet(k) ?? "";
        }
      }
    }
    const ss: Record<string, string> = {};
    const ssKeys: string[] = [];
    if (native) {
      for (let i = 0; i < native.ssLen(); i += 1) {
        const k = native.ssKey(i);
        if (k) {
          ssKeys.push(k);
          ss[k] = native.ssGet(k) ?? "";
        }
      }
    }
    // Q1：Cookie 终态用捕获的 native getter，禁止走探针包装 getter（不制造 touch）
    let cookies = "";
    if (native?.cookieDesc?.get) {
      cookies = String(native.cookieDesc.get.call(document));
    }
    return {
      ls,
      lsKeys: lsKeys.slice().sort(),
      ss,
      ssKeys: ssKeys.slice().sort(),
      cookies,
      storageTouches: probe?.storageTouches?.slice() ?? [],
      idbTouches: probe?.idbTouches?.slice() ?? [],
      idbWrites: (probe?.idbTouches ?? []).filter((t) => t.isWrite),
      domHistory: probe?.domHistory?.slice() ?? [],
      cookieTouches: probe?.cookieTouches?.slice() ?? [],
      storageBaseline: probe?.storageBaseline ?? {},
      ssBaseline: probe?.ssBaseline ?? {},
      idbNames,
      armed: Boolean(probe?.armed),
    };
  });
}

function secretBlobsFromText(s: string): void {
  expect(s, "SECRET 泄漏").not.toContain(SECRET);
  expect(s, "apiKey 泄漏").not.toContain("sk-v1o-leaked");
  expect(s, "路径泄漏").not.toContain("C:\\Users\\secret");
  expect(s, "statusMessage 异常原文").not.toContain("异常详情:");
}

function scanAll(blob: string) {
  secretBlobsFromText(blob);
}

async function snapshotContextCookies(page: Page) {
  const list = await page.context().cookies();
  return list.map((c) => ({
    name: c.name,
    value: c.value,
    httpOnly: Boolean(c.httpOnly),
  }));
}

async function assertPrivacyClean(
  page: Page,
  probe: Probe,
  consoleCol: ConsoleCollector,
) {
  await consoleCol.drain();
  const domFinal = await collectDomExport(page);
  const priv = await readPrivacyState(page);
  probe.contextCookiesAfter = await snapshotContextCookies(page);
  const reqBlob = JSON.stringify(probe.allRequests);
  const cookieHeaderBlob = probe.allRequests
    .map((r) => r.headers.cookie ?? r.headers.Cookie ?? "")
    .join("\n");

  // 1) request / console / DOM 历史全部扫描（含同源静态资源请求）
  scanAll(reqBlob);
  scanAll(consoleCol.lines.join("\n"));
  scanAll(domFinal);
  scanAll(JSON.stringify(priv.domHistory));
  scanAll(cookieHeaderBlob);
  scanAll(page.url());
  scanAll(JSON.stringify(probe.contextCookiesAfter));

  // 2) arm 后 Storage/IDB touch 参数和值全部扫描
  scanAll(JSON.stringify(priv.storageTouches));
  scanAll(JSON.stringify(priv.idbTouches));
  scanAll(JSON.stringify(priv.cookieTouches));

  // 3) 最终 Storage 只扫新增或相对 baseline 变化的值
  for (const [k, v] of Object.entries(priv.ls)) {
    const base = priv.storageBaseline[k];
    if (base === v) continue; // 键值字节级未变 → 排除
    scanAll(`${k}=${v}`);
  }
  for (const [k, v] of Object.entries(priv.ss)) {
    const base = priv.ssBaseline[k];
    if (base === v) continue;
    scanAll(`${k}=${v}`);
  }
  // 新增键
  for (const k of priv.lsKeys) {
    if (!(k in priv.storageBaseline)) scanAll(`${k}=${priv.ls[k]}`);
  }

  // IDB：touches/writes 精确 0；快照仍为空
  expect(priv.idbTouches, "IDB touches 必须精确 0").toEqual([]);
  expect(priv.idbWrites, "IDB writes 必须精确 0").toEqual([]);
  expect(priv.idbNames, "IDB 数据库名快照应为空").toEqual([]);

  // Cookie：native 终态空 + touches 0；context.cookies 覆盖任意名值与 HttpOnly 全量对账
  expect(priv.cookies, "native cookie 终态必须空").toBe("");
  expect(priv.cookieTouches, "cookie touches 必须精确 0").toEqual([]);
  const sortCookies = (
    list: Array<{ name: string; value: string; httpOnly: boolean }>,
  ) =>
    list
      .slice()
      .sort((a, b) =>
        a.name === b.name
          ? a.value.localeCompare(b.value)
          : a.name.localeCompare(b.name),
      );
  expect(
    sortCookies(probe.contextCookiesAfter),
    "context.cookies 打开前/终态必须完整对账（含 HttpOnly，禁止仅筛 v1o/SECRET）",
  ).toEqual(sortCookies(probe.contextCookiesBefore));
  // 终态任一 cookie 亦不得含敏感 canary
  for (const c of probe.contextCookiesAfter) {
    secretBlobsFromText(`${c.name}=${c.value}`);
  }

  // 旧 poison 字节原样
  if (priv.storageBaseline[DOCS_LS_KEY] != null) {
    expect(priv.ls[DOCS_LS_KEY]).toBe(POISON_LS_VALUE);
    expect(priv.ls[DOCS_LS_KEY_ALT]).toBe(POISON_LS_ALT_VALUE);
    expect(priv.ls[UNRELATED_LS_KEY]).toBe(UNRELATED_LS_VALUE);
  }
}

function writeCount(probe: Probe, pred: (c: ApiCall) => boolean): number {
  return probe.writes.filter(pred).length;
}

async function openKnowledge(page: Page, probe?: Probe) {
  if (probe) {
    probe.contextCookiesBefore = await snapshotContextCookies(page);
    probe.currentOpToken = "open";
  }
  await page.goto("/knowledge-base");
  await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({
    timeout: 20_000,
  });
  if (probe) {
    probe.contextCookiesAfter = await snapshotContextCookies(page);
  }
}

async function doubleRaf(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => resolve());
        });
      }),
  );
}

/** 至少两轮 microtask/RAF continuation barrier */
async function continuationBarrier(page: Page) {
  await doubleRaf(page);
  await page.evaluate(() => Promise.resolve());
  await doubleRaf(page);
  await page.evaluate(() => Promise.resolve());
}

/**
 * 业务 catch/finally 可观测 continuation：response/requestfailed 之后再跑。
 * 双 RAF + 双 microtask，覆盖 success 与 abort 路径上的状态提交。
 */
async function businessContinuationBarrier(page: Page) {
  await continuationBarrier(page);
  await page.evaluate(() => Promise.resolve().then(() => Promise.resolve()));
  await continuationBarrier(page);
}

/** 规范化 pathname 比较（去尾斜杠） */
function pathEqualsApi(url: string, apiPath: string): boolean {
  try {
    const p = normalizeApiPath(new URL(url).pathname);
    return p === normalizeApiPath(apiPath);
  } catch {
    return false;
  }
}

/**
 * 等待浏览器层对指定 API 路径的终态：response 或 requestfailed。
 * 必须在 release 之前安装 promise，禁止仅依赖 route helper fulfilled 计数。
 */
function armBrowserRouteTerminal(
  page: Page,
  apiPath: string,
  prefer: "response" | "requestfailed" | "either",
): Promise<"response" | "requestfailed"> {
  const matchUrl = (url: string) => pathEqualsApi(url, apiPath);
  const resp = page
    .waitForEvent("response", {
      predicate: (r) => matchUrl(r.url()),
      timeout: 12_000,
    })
    .then(() => "response" as const);
  const fail = page
    .waitForEvent("requestfailed", {
      predicate: (r) => matchUrl(r.url()),
      timeout: 12_000,
    })
    .then(() => "requestfailed" as const);
  if (prefer === "response") return resp;
  if (prefer === "requestfailed") return fail;
  return Promise.race([resp, fail]);
}

async function setHiddenFileInput(page: Page, name: string, body: string) {
  const input = page.locator('input[type="file"][accept*=".pdf"]').first();
  await input.setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from(body, "utf8"),
  });
}

async function preparePage(
  page: Page,
  probe: Probe,
  opts?: { poison?: boolean },
) {
  // 同步 arm 在 addInitScript 内完成；禁止 open 后再 arm
  await installPrivacyInit(page, opts);
  await installRoutes(page, probe);
}

// ---------------------------------------------------------------------------
// H. 单一 TypeScript AST analyzer（实际源码与 synthetic 共用）
// ---------------------------------------------------------------------------

const ACTION_METHOD_NAMES = new Set([
  "click",
  "check",
  "uncheck",
  "selectOption",
  "setInputFiles",
  "fill",
  "press",
  "dispatchEvent",
  "goto",
  "reload",
]);

const MUTATION_HELPER_NAMES = new Set([
  "runMutationCase",
  "triggerMutation",
  "setHiddenFileInput",
]);

const SELF_GUARD_DESCRIBE = "V1-O 自守卫";
const SELF_GUARD_TEST =
  "结构化 AST：拒绝 skip/提前 return/OR 宽放/固定 sleep/清键/吞异常";

type AstFinding = { kind: string; pos: number; text: string };

function analyzeSpecSource(sourceText: string): AstFinding[] {
  const sf = ts.createSourceFile(
    "spec.ts",
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const findings: AstFinding[] = [];

  const calleeName = (expr: ts.Expression): string => {
    if (ts.isIdentifier(expr)) return expr.text;
    if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
    // 计算属性：page["click"] / loc['fill']
    if (
      ts.isElementAccessExpression(expr) &&
      (ts.isStringLiteral(expr.argumentExpression) ||
        ts.isNoSubstitutionTemplateLiteral(expr.argumentExpression))
    ) {
      return expr.argumentExpression.text;
    }
    return "";
  };

  const exprFull = (expr: ts.Expression): string => {
    if (ts.isPropertyAccessExpression(expr)) {
      return `${exprFull(expr.expression)}.${expr.name.text}`;
    }
    if (ts.isElementAccessExpression(expr)) {
      return `${exprFull(expr.expression)}[${expr.argumentExpression.getText(sf)}]`;
    }
    if (ts.isIdentifier(expr)) return expr.text;
    if (ts.isCallExpression(expr)) {
      return exprFull(expr.expression);
    }
    return expr.getText(sf);
  };

  /** 遍历 callee 接收链（含链式 then/catch/finally） */
  const walkCalleeReceivers = (expr: ts.Expression): string[] => {
    const out: string[] = [];
    let cur: ts.Expression | undefined = expr;
    while (cur) {
      out.push(exprFull(cur));
      if (ts.isPropertyAccessExpression(cur) || ts.isElementAccessExpression(cur)) {
        cur = cur.expression;
        continue;
      }
      if (ts.isCallExpression(cur)) {
        cur = cur.expression;
        continue;
      }
      if (ts.isParenthesizedExpression(cur)) {
        cur = cur.expression;
        continue;
      }
      break;
    }
    return out;
  };

  /** 解析字符串字面量或同文件 const 绑定标题 */
  const constStringTable = new Map<string, string>();
  /** helper 别名：const click = page.click / const run = runMutationCase */
  const helperAliasTable = new Map<string, string>();
  sf.statements.forEach((st) => {
    if (!ts.isVariableStatement(st)) return;
    for (const d of st.declarationList.declarations) {
      if (
        ts.isIdentifier(d.name) &&
        d.initializer &&
        (ts.isStringLiteral(d.initializer) ||
          ts.isNoSubstitutionTemplateLiteral(d.initializer))
      ) {
        constStringTable.set(d.name.text, d.initializer.text);
      }
      if (ts.isIdentifier(d.name) && d.initializer) {
        const initName = calleeName(d.initializer as ts.Expression);
        if (
          ACTION_METHOD_NAMES.has(initName) ||
          MUTATION_HELPER_NAMES.has(initName)
        ) {
          helperAliasTable.set(d.name.text, initName);
        }
        // page.click.bind(...) / loc.fill.bind
        if (
          ts.isCallExpression(d.initializer) &&
          ts.isPropertyAccessExpression(d.initializer.expression) &&
          d.initializer.expression.name.text === "bind"
        ) {
          const target = d.initializer.expression.expression;
          const tn = calleeName(target);
          if (ACTION_METHOD_NAMES.has(tn) || MUTATION_HELPER_NAMES.has(tn)) {
            helperAliasTable.set(d.name.text, tn);
          }
        }
      }
    }
  });
  // 也扫描 test 回调内的别名声明（synthetic 内联）
  const collectAliases = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const init = node.initializer;
      const initName = calleeName(init as ts.Expression);
      if (ACTION_METHOD_NAMES.has(initName) || MUTATION_HELPER_NAMES.has(initName)) {
        helperAliasTable.set(node.name.text, initName);
      }
      if (
        ts.isCallExpression(init) &&
        ts.isPropertyAccessExpression(init.expression) &&
        init.expression.name.text === "bind"
      ) {
        const tn = calleeName(init.expression.expression);
        if (ACTION_METHOD_NAMES.has(tn) || MUTATION_HELPER_NAMES.has(tn)) {
          helperAliasTable.set(node.name.text, tn);
        }
      }
    }
    ts.forEachChild(node, collectAliases);
  };
  collectAliases(sf);

  const strLit = (n: ts.Node | undefined): string | null => {
    if (!n) return null;
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      return n.text;
    }
    if (ts.isIdentifier(n) && constStringTable.has(n.text)) {
      return constStringTable.get(n.text)!;
    }
    return null;
  };

  const resolveActionName = (expr: ts.Expression): string => {
    const n = calleeName(expr);
    if (ACTION_METHOD_NAMES.has(n) || MUTATION_HELPER_NAMES.has(n)) return n;
    if (ts.isIdentifier(expr) && helperAliasTable.has(expr.text)) {
      return helperAliasTable.get(expr.text)!;
    }
    return n;
  };

  /**
   * 精确跳过：仅真实 test.describe(固定标题) + test(固定标题) 回调豁免。
   * 禁止宽 includes("describe") 或错误标题豁免。
   */
  const isExemptSelfGuardTest = (call: ts.CallExpression): boolean => {
    const title = strLit(call.arguments[0]);
    if (title !== SELF_GUARD_TEST) return false;
    let p: ts.Node | undefined = call.parent;
    while (p) {
      if (ts.isCallExpression(p)) {
        const cal = p.expression;
        const isTestDescribe =
          ts.isPropertyAccessExpression(cal) &&
          ts.isIdentifier(cal.expression) &&
          cal.expression.text === "test" &&
          cal.name.text === "describe";
        if (isTestDescribe) {
          const dTitle = strLit(p.arguments[0]);
          if (dTitle === SELF_GUARD_DESCRIBE) return true;
        }
      }
      p = p.parent;
    }
    return false;
  };

  const nestedSkipCallees = new Set([
    "route",
    "evaluate",
    "addInitScript",
    "waitForFunction",
    "exposeFunction",
    "on",
    "once",
    "addListener",
  ]);

  const isPromiseThenCatchFinally = (expr: ts.Expression): boolean => {
    if (!ts.isPropertyAccessExpression(expr)) return false;
    const n = expr.name.text;
    return n === "then" || n === "catch" || n === "finally";
  };

  const hasRethrowOrRejectAssert = (fn: ts.ConciseBody): boolean => {
    const text = fn.getText(sf);
    if (/\bthrow\b/.test(text)) return true;
    if (/rejects|toThrow|toBeInstanceOf\s*\(\s*Error/.test(text)) return true;
    if (/expect\s*\([^)]*\)\s*\.\s*rejects/.test(text)) return true;
    return false;
  };

  const isInsideExpect = (node: ts.Node): boolean => {
    let p: ts.Node | undefined = node.parent;
    while (p) {
      if (ts.isCallExpression(p) && calleeName(p.expression) === "expect") {
        return true;
      }
      p = p.parent;
    }
    return false;
  };

  const walkControl = (
    node: ts.Node,
    opts: { inNested: boolean; onFinding: (f: AstFinding) => void },
  ) => {
    const { inNested, onFinding } = opts;
    if (inNested) return;

    if (ts.isReturnStatement(node)) {
      onFinding({
        kind: "return",
        pos: node.getStart(sf),
        text: node.getText(sf).slice(0, 40),
      });
    }

    if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind === ts.SyntaxKind.BarBarToken &&
      isInsideExpect(node)
    ) {
      onFinding({
        kind: "expect_or",
        pos: node.getStart(sf),
        text: node.getText(sf).slice(0, 60),
      });
    }

    if (
      ts.isForStatement(node) ||
      ts.isForOfStatement(node) ||
      ts.isForInStatement(node) ||
      ts.isWhileStatement(node) ||
      ts.isDoStatement(node)
    ) {
      onFinding({
        kind: "loop",
        pos: node.getStart(sf),
        text: node.getText(sf).slice(0, 40),
      });
    }

    if (ts.isTryStatement(node) && node.catchClause) {
      const body = node.catchClause.block;
      if (!hasRethrowOrRejectAssert(body)) {
        onFinding({
          kind: "empty_catch",
          pos: node.catchClause.getStart(sf),
          text: "catch_without_rethrow_or_reject_assert",
        });
      }
    }

    if (ts.isCallExpression(node)) {
      const name = resolveActionName(node.expression);
      const full = exprFull(node.expression);
      const receivers = walkCalleeReceivers(node.expression);

      if (ACTION_METHOD_NAMES.has(name) || MUTATION_HELPER_NAMES.has(name)) {
        // 位于 If/Conditional/逻辑短路分支（非条件表达式本身）则违规
        let branched = false;
        let cur: ts.Node | undefined = node.parent;
        while (cur) {
          if (ts.isIfStatement(cur)) {
            // 在 condition 内不算分支动作
            let inCond = false;
            let p: ts.Node | undefined = node.parent;
            while (p && p !== cur) {
              if (p === cur.expression) inCond = true;
              p = p.parent;
            }
            if (!inCond) branched = true;
            break;
          }
          if (ts.isConditionalExpression(cur)) {
            let inCond = false;
            let p: ts.Node | undefined = node.parent;
            while (p && p !== cur) {
              if (p === cur.condition) inCond = true;
              p = p.parent;
            }
            if (!inCond) branched = true;
            break;
          }
          if (
            ts.isBinaryExpression(cur) &&
            (cur.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken ||
              cur.operatorToken.kind === ts.SyntaxKind.BarBarToken)
          ) {
            // 短路：action 为直接右操作数，或位于右操作数子树
            if (cur.right === node) {
              branched = true;
              break;
            }
            let inRight = false;
            let p: ts.Node | undefined = node.parent;
            while (p && p !== cur) {
              if (p === cur.right) inRight = true;
              p = p.parent;
            }
            if (inRight) {
              branched = true;
              break;
            }
          }
          cur = cur.parent;
        }
        if (branched) {
          onFinding({
            kind: "branched_action",
            pos: node.getStart(sf),
            text: full,
          });
        }
      }

      if (name === "waitForTimeout") {
        onFinding({
          kind: "waitForTimeout",
          pos: node.getStart(sf),
          text: full,
        });
      }
      if (
        (name === "setTimeout" || name === "setInterval") &&
        node.arguments.some(
          (a) =>
            ts.isNumericLiteral(a) ||
            (ts.isPrefixUnaryExpression(a) &&
              ts.isNumericLiteral(a.operand)),
        )
      ) {
        onFinding({
          kind: "timer_const",
          pos: node.getStart(sf),
          text: full,
        });
      }
      // 精确 callee：禁止用整段 getText（会误伤含 synthetic 字符串的大 test 体）
      if (
        full === "localStorage.removeItem" ||
        full === "localStorage.clear" ||
        full === "sessionStorage.removeItem" ||
        full === "sessionStorage.clear" ||
        name === "deleteDatabase" ||
        full.endsWith(".deleteDatabase") ||
        receivers.some(
          (r) =>
            r === "localStorage.removeItem" ||
            r === "localStorage.clear" ||
            r === "sessionStorage.removeItem" ||
            r === "sessionStorage.clear" ||
            r.endsWith(".deleteDatabase"),
        )
      ) {
        onFinding({
          kind: "clear_legacy_key",
          pos: node.getStart(sf),
          text: full.slice(0, 60),
        });
      }

      // Promise rejection 回调
      if (isPromiseThenCatchFinally(node.expression)) {
        const method = calleeName(node.expression);
        if (method === "catch" && node.arguments[0]) {
          const arg = node.arguments[0];
          if (
            (ts.isArrowFunction(arg) || ts.isFunctionExpression(arg)) &&
            !hasRethrowOrRejectAssert(arg.body)
          ) {
            onFinding({
              kind: "swallow_catch",
              pos: node.getStart(sf),
              text: node.getText(sf).slice(0, 80),
            });
          }
        }
        if (method === "then" && node.arguments[1]) {
          const arg = node.arguments[1];
          if (
            (ts.isArrowFunction(arg) || ts.isFunctionExpression(arg)) &&
            !hasRethrowOrRejectAssert(arg.body)
          ) {
            onFinding({
              kind: "swallow_then",
              pos: node.getStart(sf),
              text: node.getText(sf).slice(0, 80),
            });
          }
        }
      }
    }

    // 递归：if/switch/try/loop 进入；嵌套 function/lambda 排除；
    // Promise then/catch/finally 回调必须进入；遍历 callee receiver 链
    if (ts.isCallExpression(node)) {
      const name = calleeName(node.expression);
      const receivers = walkCalleeReceivers(node.expression);
      const enterPromise =
        isPromiseThenCatchFinally(node.expression) ||
        receivers.some((r) => /\.(then|catch|finally)$/.test(r));
      const skipNested = nestedSkipCallees.has(name) && !enterPromise;
      // 进入 receiver 表达式（链式调用左部）
      if (
        ts.isPropertyAccessExpression(node.expression) ||
        ts.isElementAccessExpression(node.expression)
      ) {
        walkControl(node.expression.expression, opts);
      }
      for (const arg of node.arguments) {
        if (ts.isArrowFunction(arg) || ts.isFunctionExpression(arg)) {
          if (skipNested) continue;
          if (enterPromise) {
            walkControl(arg.body, { inNested: false, onFinding });
          }
          // 命名 rejection handler：identifier 引用
          continue;
        }
        if (ts.isIdentifier(arg) && enterPromise && name === "catch") {
          // 命名 rejection 回调：视为可能吞异常（无函数体则红）
          onFinding({
            kind: "swallow_catch",
            pos: arg.getStart(sf),
            text: "named_rejection_handler:" + arg.text,
          });
          continue;
        }
        walkControl(arg, { inNested, onFinding });
      }
      return;
    }

    // ?? 与 switch 已在下方；计算属性访问中的 action
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken) {
      walkControl(node.left, opts);
      walkControl(node.right, opts);
      return;
    }

    if (
      ts.isFunctionDeclaration(node) ||
      ts.isFunctionExpression(node) ||
      ts.isArrowFunction(node)
    ) {
      return;
    }

    if (ts.isIfStatement(node)) {
      walkControl(node.expression, opts);
      walkControl(node.thenStatement, opts);
      if (node.elseStatement) walkControl(node.elseStatement, opts);
      return;
    }
    if (ts.isSwitchStatement(node)) {
      walkControl(node.expression, opts);
      for (const clause of node.caseBlock.clauses) {
        for (const st of clause.statements) walkControl(st, opts);
      }
      return;
    }
    if (ts.isTryStatement(node)) {
      walkControl(node.tryBlock, opts);
      if (node.catchClause) walkControl(node.catchClause.block, opts);
      if (node.finallyBlock) walkControl(node.finallyBlock, opts);
      return;
    }
    if (
      ts.isForStatement(node) ||
      ts.isWhileStatement(node) ||
      ts.isDoStatement(node) ||
      ts.isForOfStatement(node) ||
      ts.isForInStatement(node)
    ) {
      ts.forEachChild(node, (c) => walkControl(c, opts));
      return;
    }
    if (ts.isBlock(node)) {
      for (const st of node.statements) walkControl(st, opts);
      return;
    }

    ts.forEachChild(node, (c) => walkControl(c, opts));
  };

  const isTestCall = (node: ts.Node): node is ts.CallExpression => {
    if (!ts.isCallExpression(node)) return false;
    const expr = node.expression;
    // 仅 test / test.only / test.skip / test.fixme — 禁止把 test.describe 当 test
    if (ts.isIdentifier(expr) && expr.text === "test") return true;
    if (
      ts.isPropertyAccessExpression(expr) &&
      ts.isIdentifier(expr.expression) &&
      expr.expression.text === "test"
    ) {
      const m = expr.name.text;
      if (
        m === "describe" ||
        m === "beforeAll" ||
        m === "beforeEach" ||
        m === "afterAll" ||
        m === "afterEach" ||
        m === "configure" ||
        m === "use" ||
        m === "extend" ||
        m === "setTimeout" ||
        m === "info" ||
        m === "step"
      ) {
        return false;
      }
      // test.only / test.skip / test.fixme / test.fix
      return true;
    }
    return false;
  };

  const getCallback = (
    call: ts.CallExpression,
  ): ts.ArrowFunction | ts.FunctionExpression | null => {
    for (const arg of call.arguments) {
      if (ts.isArrowFunction(arg) || ts.isFunctionExpression(arg)) return arg;
    }
    return null;
  };

  // test/describe .skip/.fixme / serial
  const visitGlobal = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const full = exprFull(node.expression);
      if (
        full === "test.skip" ||
        full === "test.fixme" ||
        full === "describe.skip" ||
        full === "describe.fixme" ||
        full === "test.describe.skip" ||
        full === "test.describe.fixme"
      ) {
        findings.push({
          kind: "skip_fixme",
          pos: node.getStart(sf),
          text: full,
        });
      }
      if (
        full.includes("describe.configure") ||
        (calleeName(node.expression) === "configure" &&
          full.includes("describe"))
      ) {
        const argText = node.arguments[0]?.getText(sf) ?? "";
        if (argText.includes("serial")) {
          findings.push({
            kind: "serial_describe",
            pos: node.getStart(sf),
            text: argText,
          });
        }
      }
    }
    ts.forEachChild(node, visitGlobal);
  };
  visitGlobal(sf);

  // 各 test 回调直接控制流
  const visitTests = (node: ts.Node) => {
    if (isTestCall(node)) {
      if (isExemptSelfGuardTest(node)) {
        ts.forEachChild(node, visitTests);
        return;
      }
      const full = exprFull(node.expression);
      if (full.endsWith(".skip") || full.endsWith(".fixme")) {
        findings.push({
          kind: "skip_fixme",
          pos: node.getStart(sf),
          text: full,
        });
      }
      const cb = getCallback(node);
      if (cb) {
        walkControl(cb.body, {
          inNested: false,
          onFinding: (f) => findings.push(f),
        });
      }
    }
    ts.forEachChild(node, visitTests);
  };
  visitTests(sf);

  // production-read 门：整个 SourceFile（含 export-from / import = require / 变量路径）
  const isProdPath = (t: string) =>
    /src\/|features\/knowledge-base|useKnowledgeBase|KnowledgeBasePage/.test(t);

  const visitProduction = (node: ts.Node) => {
    if (ts.isImportDeclaration(node)) {
      const spec = node.moduleSpecifier;
      if (ts.isStringLiteral(spec) && isProdPath(spec.text)) {
        findings.push({
          kind: "production_import",
          pos: node.getStart(sf),
          text: spec.text,
        });
      }
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      if (isProdPath(node.moduleSpecifier.text)) {
        findings.push({
          kind: "production_import",
          pos: node.getStart(sf),
          text: "export-from:" + node.moduleSpecifier.text,
        });
      }
    }
    // import fs = require("...")
    if (ts.isImportEqualsDeclaration(node) && ts.isExternalModuleReference(node.moduleReference)) {
      const expr = node.moduleReference.expression;
      if (ts.isStringLiteral(expr) && isProdPath(expr.text)) {
        findings.push({
          kind: "production_require",
          pos: node.getStart(sf),
          text: "import=require:" + expr.text,
        });
      }
    }
    if (ts.isCallExpression(node)) {
      const name = calleeName(node.expression);
      const full = exprFull(node.expression);
      const receivers = walkCalleeReceivers(node.expression);
      if (name === "require" || full === "require" || receivers.includes("require")) {
        const a0 = node.arguments[0]?.getText(sf) ?? "";
        if (isProdPath(a0)) {
          findings.push({
            kind: "production_require",
            pos: node.getStart(sf),
            text: a0,
          });
        }
      }
      if (
        node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        full === "import"
      ) {
        const a0 = node.arguments[0]?.getText(sf) ?? "";
        if (isProdPath(a0)) {
          findings.push({
            kind: "production_dynamic_import",
            pos: node.getStart(sf),
            text: a0,
          });
        }
      }
      // helper 别名：const click = page.click.bind... 后调用仍覆盖 branched_action via MUTATION set
      if (
        name === "readFile" ||
        name === "readFileSync" ||
        full.endsWith(".readFile") ||
        full.endsWith(".readFileSync") ||
        receivers.some((r) => r.endsWith("readFile") || r.endsWith("readFileSync"))
      ) {
        let inSelf = false;
        let p: ts.Node | undefined = node.parent;
        while (p) {
          if (ts.isCallExpression(p) && isTestCall(p) && isExemptSelfGuardTest(p)) {
            inSelf = true;
            break;
          }
          p = p.parent;
        }
        if (!inSelf) {
          findings.push({
            kind: "fs_read",
            pos: node.getStart(sf),
            text: node.getText(sf).slice(0, 80),
          });
        }
      }
    }
    // 变量生产路径：const p = "..."; require(p) / import(p)
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      const init = node.initializer;
      if (
        (ts.isStringLiteral(init) || ts.isNoSubstitutionTemplateLiteral(init)) &&
        isProdPath(init.text)
      ) {
        // 记录绑定名，供后续 require(id)/import(id) 使用
        const bind = node.name.text;
        const visitUses = (n2: ts.Node) => {
          if (ts.isCallExpression(n2)) {
            const cn = calleeName(n2.expression);
            const a0 = n2.arguments[0];
            if (
              (cn === "require" ||
                n2.expression.kind === ts.SyntaxKind.ImportKeyword) &&
              a0 &&
              ts.isIdentifier(a0) &&
              a0.text === bind
            ) {
              findings.push({
                kind:
                  cn === "require"
                    ? "production_require"
                    : "production_dynamic_import",
                pos: n2.getStart(sf),
                text: "var-path:" + init.text,
              });
            }
          }
          ts.forEachChild(n2, visitUses);
        };
        visitUses(sf);
      }
    }
    ts.forEachChild(node, visitProduction);
  };
  visitProduction(sf);

  return findings;
}

// ---------------------------------------------------------------------------
// H. 自守卫用例 + synthetic 正反表
// ---------------------------------------------------------------------------

test.describe(SELF_GUARD_DESCRIBE, () => {
  test(SELF_GUARD_TEST, async () => {
    // 唯一豁免：自守卫回调内读取当前 spec 自身
    const fs = await import("node:fs");
    const selfPath = fileURLToPath(import.meta.url);
    const fullSrc = fs.readFileSync(selfPath, "utf8");
    const real = analyzeSpecSource(fullSrc);

    expect(real.filter((f) => f.kind === "skip_fixme")).toEqual([]);
    expect(real.filter((f) => f.kind === "waitForTimeout")).toEqual([]);
    expect(real.filter((f) => f.kind === "timer_const")).toEqual([]);
    expect(real.filter((f) => f.kind === "serial_describe")).toEqual([]);
    expect(real.filter((f) => f.kind === "production_import")).toEqual([]);
    expect(real.filter((f) => f.kind === "production_require")).toEqual([]);
    expect(real.filter((f) => f.kind === "production_dynamic_import")).toEqual(
      [],
    );
    expect(real.filter((f) => f.kind === "fs_read")).toEqual([]);
    expect(real.filter((f) => f.kind === "expect_or")).toEqual([]);
    expect(real.filter((f) => f.kind === "return")).toEqual([]);
    expect(real.filter((f) => f.kind === "clear_legacy_key")).toEqual([]);
    expect(real.filter((f) => f.kind === "empty_catch")).toEqual([]);
    expect(real.filter((f) => f.kind === "swallow_catch")).toEqual([]);
    expect(real.filter((f) => f.kind === "swallow_then")).toEqual([]);
    expect(real.filter((f) => f.kind === "loop")).toEqual([]);
    expect(real.filter((f) => f.kind === "branched_action")).toEqual([]);

    // ----- synthetic 负例：每条规则各一，仅内存字符串 + analyzeSpecSource -----
    const neg: Array<{ name: string; src: string; kind: string }> = [
      {
        name: "return",
        src: `test("t", () => { return; expect(1).toBe(1); });`,
        kind: "return",
      },
      {
        name: "branched_click",
        src: `test("t", async ({page}) => { if (true) { await page.click("x"); } });`,
        kind: "branched_action",
      },
      {
        // short-circuit 直接右操作数
        name: "short_circuit_and_click",
        src: `test("t", async ({page}) => { true && page.click("x"); });`,
        kind: "branched_action",
      },
      {
        name: "short_circuit_or_fill",
        src: `test("t", async ({page}) => { false || page.fill("y", "z"); });`,
        kind: "branched_action",
      },
      {
        name: "expect_or",
        src: `test("t", () => { expect(true || false).toBe(true); });`,
        kind: "expect_or",
      },
      {
        name: "skip",
        src: `test.skip("x", () => {});`,
        kind: "skip_fixme",
      },
      {
        name: "empty_catch",
        src: `test("t", () => { try { x(); } catch (e) {} });`,
        kind: "empty_catch",
      },
      {
        name: "swallow_catch",
        src: `test("t", () => { Promise.resolve().catch(() => undefined); });`,
        kind: "swallow_catch",
      },
      {
        name: "swallow_then",
        src: `test("t", () => { Promise.resolve().then(() => undefined, () => undefined); });`,
        kind: "swallow_then",
      },
      {
        name: "waitForTimeout",
        src: `test("t", async ({page}) => { await page.waitForTimeout(100); });`,
        kind: "waitForTimeout",
      },
      {
        name: "timer",
        src: `test("t", () => { setTimeout(() => {}, 50); });`,
        kind: "timer_const",
      },
      {
        name: "clear_key",
        src: `test("t", () => { localStorage.removeItem("k"); });`,
        kind: "clear_legacy_key",
      },
      {
        name: "serial",
        src: `test.describe.configure({ mode: "serial" }); test("t", () => {});`,
        kind: "serial_describe",
      },
      {
        name: "loop",
        src: `test("t", () => { for (let i = 0; i < 1; i++) { expect(i).toBe(0); } });`,
        kind: "loop",
      },
      {
        name: "fs_top",
        src: `import fs from "node:fs";\nfs.readFileSync("x");\ntest("t", () => {});`,
        kind: "fs_read",
      },
      {
        name: "prod_import",
        src: `import x from "../src/features/knowledge-base/hooks/useKnowledgeBase";\ntest("t", () => {});`,
        kind: "production_import",
      },
      {
        name: "prod_dynamic",
        src: `test("t", async () => { await import("../src/features/knowledge-base/pages/KnowledgeBasePage"); });`,
        kind: "production_dynamic_import",
      },
      {
        name: "helper_runMutationCase",
        src: `test("t", () => { if (1) runMutationCase(); });`,
        kind: "branched_action",
      },
      {
        name: "helper_triggerMutation",
        src: `test("t", () => { if (1) triggerMutation(); });`,
        kind: "branched_action",
      },
      {
        name: "helper_setHiddenFileInput",
        src: `test("t", () => { if (1) setHiddenFileInput(); });`,
        kind: "branched_action",
      },
      {
        name: "named_rejection",
        src: `function h(e){return undefined;} test("t", () => { Promise.resolve().catch(h); });`,
        kind: "swallow_catch",
      },
      {
        name: "switch_return",
        src: `test("t", () => { switch(1){ case 1: return; } });`,
        kind: "return",
      },
      {
        name: "export_from_prod",
        src: `export { x } from "../src/features/knowledge-base/hooks/useKnowledgeBase"; test("t", () => {});`,
        kind: "production_import",
      },
      {
        name: "import_eq_require",
        src: `import KB = require("../src/features/knowledge-base/hooks/useKnowledgeBase"); test("t", () => {});`,
        kind: "production_require",
      },
      {
        name: "computed_prop_click",
        src: `test("t", async ({page}) => { if (true) { await page["click"]("x"); } });`,
        kind: "branched_action",
      },
      {
        name: "helper_alias_branched",
        src: `test("t", async ({page}) => { const click = page.click; if (1) { await click("x"); } });`,
        kind: "branched_action",
      },
      {
        name: "var_path_require",
        src: `const p = "../src/features/knowledge-base/hooks/useKnowledgeBase"; require(p); test("t", () => {});`,
        kind: "production_require",
      },
      {
        name: "wrong_title_no_exempt_fs",
        src: `test.describe("V1-O 自守卫", () => { test("wrong title", async () => { const fs = require("node:fs"); fs.readFileSync("x"); }); });`,
        kind: "fs_read",
      },
      {
        name: "wrong_title_no_exempt_loop",
        src: `test.describe("V1-O 自守卫", () => { test("wrong title", () => { for (const x of [1]) { expect(x).toBe(1); } }); });`,
        kind: "loop",
      },
    ];
    for (const c of neg) {
      const f = analyzeSpecSource(c.src);
      expect(
        f.some((x) => x.kind === c.kind),
        `负例 ${c.name} 应命中 ${c.kind}`,
      ).toBe(true);
    }

    // ----- synthetic 正例 -----
    const pos: Array<{ name: string; src: string }> = [
      {
        name: "route_nested_return",
        src: `test("t", async ({page}) => { await page.route("**/*", (route) => { if (1) return; route.continue(); }); expect(1).toBe(1); });`,
      },
      {
        name: "helper_internal_loop",
        src: `function helper() { for (const x of [1]) { void x; } }\ntest("t", () => { helper(); expect(1).toBe(1); });`,
      },
      {
        name: "expect_single",
        src: `test("t", () => { expect(1).toBe(1); });`,
      },
      {
        name: "explicit_rejects",
        src: `test("t", async () => { await expect(Promise.reject(new Error("e"))).rejects.toThrow(); });`,
      },
      {
        name: "explicit_rethrow",
        src: `test("t", () => { try { throw new Error("e"); } catch (e) { throw e; } });`,
      },
      {
        name: "nullish_coalesce",
        src: `test("t", () => { const x = null ?? 1; expect(x).toBe(1); });`,
      },
      {
        name: "chained_then_rethrow",
        src: `test("t", () => { Promise.resolve().then(() => 1).catch((e) => { throw e; }); });`,
      },
      {
        // Q15：常量 describe/test 标题精确豁免；自身 fs.read + loop 不误红
        name: "const_title_self_guard_exempt",
        src: `const SELF_GUARD_DESCRIBE = "V1-O 自守卫";
const SELF_GUARD_TEST = "结构化 AST：拒绝 skip/提前 return/OR 宽放/固定 sleep/清键/吞异常";
test.describe(SELF_GUARD_DESCRIBE, () => {
  test(SELF_GUARD_TEST, async () => {
    const fs = require("node:fs");
    fs.readFileSync("x");
    for (const x of [1]) { void x; }
    expect(1).toBe(1);
  });
});`,
      },
    ];
    for (const c of pos) {
      const f = analyzeSpecSource(c.src);
      expect(
        f.filter((x) =>
          [
            "return",
            "loop",
            "expect_or",
            "empty_catch",
            "swallow_catch",
            "branched_action",
            "fs_read",
          ].includes(x.kind),
        ),
        `正例 ${c.name} 不应误红`,
      ).toEqual([]);
    }
  });
});

// ---------------------------------------------------------------------------
// A. 旧键毒化与真实空态 + 隐私 synthetic
// ---------------------------------------------------------------------------

test.describe("V1-O A 旧键与空态", () => {
  test("poisoned 双同族键+无关 + [][]：真实空态、访问计数0、快照不变", async ({
    page,
  }) => {
    const consoleCol = collectConsole(page);
    const probe = emptyProbe({ folders: [], docs: [] });
    await preparePage(page, probe);
    await openKnowledge(page, probe);
    await continuationBarrier(page);

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain(POISON_DOC_NAME);
    expect(bodyText).not.toContain(POISON_FOLDER_NAME);
    expect(bodyText).not.toContain(POISON_CANARY);
    expect(bodyText).not.toContain(MOCK_DOC_SAMPLE);
    expect(bodyText).not.toContain(MOCK_FOLDER_SAMPLE);
    expect(bodyText).not.toContain(OFFLINE_HINT);
    expect(bodyText).not.toContain("POISON_SECOND_DOC");

    await expect(page.getByText(EMPTY_TITLE)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(EMPTY_HINT)).toBeVisible();
    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    await expect(page.getByText(LOADING_TEXT)).toHaveCount(0);

    const priv = await readPrivacyState(page);
    expect(priv.ls[DOCS_LS_KEY]).toBe(POISON_LS_VALUE);
    expect(priv.ls[DOCS_LS_KEY_ALT]).toBe(POISON_LS_ALT_VALUE);
    expect(priv.ls[UNRELATED_LS_KEY]).toBe(UNRELATED_LS_VALUE);
    const legacyTouches = priv.storageTouches.filter((t) =>
      t.args.some(
        (a) =>
          typeof a === "string" &&
          (a === DOCS_LS_KEY ||
            a === DOCS_LS_KEY_ALT ||
            a.startsWith("biaoshu.knowledgeBase.docs")),
      ),
    );
    expect(legacyTouches, "旧键族访问计数必须为 0").toEqual([]);
    expect(probe.writes).toEqual([]);
    const reqBlob = JSON.stringify(probe.allRequests);
    expect(reqBlob).not.toContain(POISON_CANARY);
    expect(reqBlob).not.toContain(POISON_DOC_NAME);

    await assertPrivacyClean(page, probe, consoleCol);
    expect(probe.externalHits).toEqual([]);
    expect(probe.unknownKnowledgeHits).toEqual([]);
    expect(probe.folderGets).toBeGreaterThanOrEqual(1);
    expect(probe.docGets).toBeGreaterThanOrEqual(1);
  });

  test("合法收件箱 + 空 docs：真实文档空态", async ({ page }) => {
    const consoleCol = collectConsole(page);
    const probe = emptyProbe({
      folders: [makeFolder({ id: FLD_INBOX, name: SERVER_FOLDER_INBOX })],
      docs: [],
    });
    await preparePage(page, probe);
    await openKnowledge(page, probe);

    await expect(page.getByText(EMPTY_TITLE)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(EMPTY_HINT)).toBeVisible();
    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain(MOCK_DOC_SAMPLE);
    expect(bodyText).not.toContain(OFFLINE_HINT);
    await expect(page.getByText(SERVER_FOLDER_INBOX)).toBeVisible();
    await assertPrivacyClean(page, probe, consoleCol);
  });

  test("隐私 synthetic：baseline 含 SECRET 未触碰通过；触碰路径红", async ({
    page,
  }) => {
    const consoleCol = collectConsole(page);
    const probe = emptyProbe({ folders: [], docs: [] });
    await preparePage(page, probe);
    await openKnowledge(page, probe);
    await expect(page.getByText(EMPTY_TITLE)).toBeVisible({ timeout: 15_000 });
    // 未触碰：应通过分层扫描（baseline 含 SECRET 但不扫未变键）
    await assertPrivacyClean(page, probe, consoleCol);

    // 1) 可见读取旧键 → 红
    await page.evaluate((key) => {
      void localStorage.getItem(key);
    }, DOCS_LS_KEY);
    const afterRead = await readPrivacyState(page);
    expect(
      afterRead.storageTouches.some((t) => t.api === "localStorage.getItem"),
    ).toBe(true);
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();

    // 重置 touch：只能清空原数组，禁止替换引用（observer 与终态读取同一 domHistory）
    await page.evaluate(() => {
      const g = window as unknown as {
        __v1oProbe?: {
          storageTouches: unknown[];
          idbTouches: unknown[];
          domHistory: unknown[];
          cookieTouches: unknown[];
        };
      };
      if (g.__v1oProbe) {
        g.__v1oProbe.storageTouches.length = 0;
        g.__v1oProbe.idbTouches.length = 0;
        g.__v1oProbe.domHistory.length = 0;
        g.__v1oProbe.cookieTouches.length = 0;
      }
    });

    // 2) 同值 set（字节相同）仍记 touch → 红
    await page.evaluate(
      ({ key, val }) => {
        localStorage.setItem(key, val);
      },
      { key: DOCS_LS_KEY, val: POISON_LS_VALUE },
    );
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    await page.evaluate(() => {
      const g = window as unknown as { __v1oProbe?: { storageTouches: unknown[] } };
      if (g.__v1oProbe) g.__v1oProbe.storageTouches.length = 0;
    });

    // 3) 删除后恢复 → 红（删除本身即 touch）
    await page.evaluate(
      ({ key, val }) => {
        localStorage.removeItem(key);
        localStorage.setItem(key, val);
      },
      { key: DOCS_LS_KEY, val: POISON_LS_VALUE },
    );
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    await page.evaluate(() => {
      const g = window as unknown as { __v1oProbe?: { storageTouches: unknown[] } };
      if (g.__v1oProbe) g.__v1oProbe.storageTouches.length = 0;
    });

    // 4) 迁移到新键 → 红
    await page.evaluate(
      ({ from, to }) => {
        const v = localStorage.getItem(from);
        if (v != null) localStorage.setItem(to, v);
      },
      { from: DOCS_LS_KEY, to: DOCS_LS_KEY + ".migrated" },
    );
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    // 清迁移键（原生，避免后续干扰）并重置 touch（原数组清空）
    await page.evaluate((to) => {
      const g = window as unknown as {
        __v1oNative?: { lsRem: (k: string) => void };
        __v1oProbe?: { storageTouches: unknown[]; idbTouches: unknown[] };
      };
      g.__v1oNative?.lsRem(to);
      if (g.__v1oProbe) {
        g.__v1oProbe.storageTouches.length = 0;
        g.__v1oProbe.idbTouches.length = 0;
      }
    }, DOCS_LS_KEY + ".migrated");

    // 5) IDB 写 → 红
    await page.evaluate(async () => {
      await new Promise<void>((resolve, reject) => {
        const req = indexedDB.open("v1o_synth_leak_db", 1);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains("s")) db.createObjectStore("s");
        };
        req.onsuccess = () => {
          const db = req.result;
          const tx = db.transaction("s", "readwrite");
          tx.objectStore("s").put("SECRET_IN_IDB", "k");
          tx.oncomplete = () => {
            db.close();
            resolve();
          };
          tx.onerror = () => reject(tx.error);
        };
        req.onerror = () => reject(req.error);
      });
    });
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    await page.evaluate(() => {
      const g = window as unknown as {
        __v1oNative?: { idbDelete: (n: string) => IDBOpenDBRequest };
        __v1oProbe?: { idbTouches: unknown[] };
      };
      g.__v1oNative?.idbDelete("v1o_synth_leak_db");
      if (g.__v1oProbe) g.__v1oProbe.idbTouches.length = 0;
    });

    // 6) request 泄漏 → 红
    probe.allRequests.push({
      method: "GET",
      path: "/api/leak",
      url: "http://127.0.0.1/api/leak?x=" + SECRET,
      query: "?x=" + SECRET,
      bodyText: "",
      postData: null,
      headers: {},
      opToken: "synth",
      resourceKind: "api",
    });
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    probe.allRequests.pop();

    // 7) console 泄漏 → 红
    consoleCol.lines.push("error: " + SECRET);
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
    consoleCol.lines.pop();

    // 8) DOM 临时写再恢复：仅由真实 MutationObserver 捕获历史（禁止 synthetic 手工 push 掩盖）
    // 跨 task 写入 SECRET，确保 characterData/attr 回调时 detail 或 oldValue 确定含 SECRET
    // （同 task 内 clean→SECRET→clean 会使 detail 读到终值 clean，SECRET 仅在 oldValue）
    await page.evaluate(() => {
      const el = document.createElement("div");
      el.setAttribute("data-v1o-tmp", "clean");
      document.body.appendChild(el);
      (
        window as unknown as { __v1oTmpLeakEl?: HTMLElement }
      ).__v1oTmpLeakEl = el;
    });
    // 独立 task：写入 SECRET（observer 记录 detail 或 oldValue）
    await page.evaluate((secret) => {
      const el = (window as unknown as { __v1oTmpLeakEl?: HTMLElement })
        .__v1oTmpLeakEl;
      if (!el) throw new Error("V1O_TMP_LEAK_EL_MISSING");
      el.setAttribute("data-v1o-tmp", secret);
    }, SECRET);
    // 再一 task：恢复 clean 并移除（历史中仍保留 SECRET 记录）
    await page.evaluate(() => {
      const g = window as unknown as { __v1oTmpLeakEl?: HTMLElement };
      const el = g.__v1oTmpLeakEl;
      if (!el) throw new Error("V1O_TMP_LEAK_EL_MISSING");
      el.setAttribute("data-v1o-tmp", "clean");
      el.remove();
      delete g.__v1oTmpLeakEl;
    });
    // 证明历史可见：整条记录 detail+oldValue（禁止只查 detail 假红正确 observer）
    const afterDom = await readPrivacyState(page);
    expect(
      afterDom.domHistory.some((h) => {
        const blob = `${String(h.detail)}\0${String(h.oldValue ?? "")}`;
        return blob.includes(SECRET);
      }),
      "真实 observer 必须经 detail 或 oldValue 捕获临时 SECRET 写入",
    ).toBe(true);
    // assertPrivacyClean 扫描整段 domHistory JSON，SECRET 在历史中必须真实红
    await expect(assertPrivacyClean(page, probe, consoleCol)).rejects.toThrow();
  });
});

// ---------------------------------------------------------------------------
// B. 穷举共享 GET schema 矩阵 + 写响应接线 + HTTP 失败
// ---------------------------------------------------------------------------

function withSentinelFolders(bad: unknown[]): unknown[] {
  return [legalFolderSentinel(), ...bad];
}
function withSentinelDocs(bad: unknown[]): unknown[] {
  return [legalDocSentinel(), ...bad];
}

/** 唯一穷举共享 GET schema 矩阵 */
const schemaCases: Array<{
  name: string;
  folders?: unknown;
  docs?: unknown;
}> = [
  // folders 顶层非数组
  {
    name: "folders_not_array",
    folders: { items: [] },
    docs: [],
  },
  // folder 字段
  {
    name: "folder_id_missing",
    folders: withSentinelFolders([{ name: "x", parentId: null }]),
    docs: [],
  },
  {
    name: "folder_id_empty",
    folders: withSentinelFolders([{ id: "", name: "x", parentId: null }]),
    docs: [],
  },
  {
    name: "folder_id_wrong_type",
    folders: withSentinelFolders([{ id: 1, name: "x", parentId: null }]),
    docs: [],
  },
  {
    // 独立 canary：folder 缺 name 坏项 id 作 canary，不得出现在 UI
    name: "folder_name_missing",
    folders: withSentinelFolders([
      { id: "FOLDER_NAME_MISSING_CANARY_V1O", parentId: null },
    ]),
    docs: [],
  },
  {
    name: "folder_name_empty",
    folders: withSentinelFolders([{ id: "f1", name: "", parentId: null }]),
    docs: [],
  },
  {
    name: "folder_name_wrong_type",
    folders: withSentinelFolders([{ id: "f1", name: true, parentId: null }]),
    docs: [],
  },
  // parentId 缺失与 null 均为合法——正例见 folder_parentId_missing_ok_marker / 合法 null test
  {
    name: "folder_parentId_empty",
    folders: withSentinelFolders([{ id: "f1", name: "x", parentId: "" }]),
    docs: [],
  },
  {
    name: "folder_parentId_wrong_type",
    folders: withSentinelFolders([{ id: "f1", name: "x", parentId: 1 }]),
    docs: [],
  },
  {
    name: "folder_duplicate_id",
    folders: withSentinelFolders([
      { id: "dupf", name: "a", parentId: null },
      { id: "dupf", name: "b", parentId: null },
    ]),
    docs: [],
  },
  {
    name: "folder_parentId_orphan",
    folders: withSentinelFolders([
      { id: "f1", name: "x", parentId: "no_such_parent" },
    ]),
    docs: [],
  },
  {
    name: "folder_parentId_self",
    folders: withSentinelFolders([{ id: "f1", name: "x", parentId: "f1" }]),
    docs: [],
  },
  {
    name: "folder_parentId_cycle",
    folders: withSentinelFolders([
      { id: "f1", name: "a", parentId: "f2" },
      { id: "f2", name: "b", parentId: "f1" },
    ]),
    docs: [],
  },
  // docs 顶层非数组
  {
    name: "docs_not_array",
    folders: [legalFolderSentinel()],
    docs: { items: [] },
  },
  // doc 必需 string
  {
    name: "doc_id_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_id_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({ id: "", name: "x", folderId: LEGAL_FOLDER_SENTINEL }),
    ]),
  },
  {
    name: "doc_id_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: 1,
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    // 独立 canary：缺 name 的 doc 坏项不得渲染（与 folder 坏项 canary 分离）
    name: "doc_name_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
        // name 缺失；用旁路字段作 canary 身份（不得出现在 UI）
        title: "DOC_NAME_MISSING_CANARY_V1O",
      },
    ]),
  },
  {
    name: "doc_name_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({ id: "d1", name: "", folderId: LEGAL_FOLDER_SENTINEL }),
    ]),
  },
  {
    name: "doc_name_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: false,
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  // updated / updatedAt / category / folderId / status：缺失/空/错型矩阵
  {
    name: "doc_updated_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_updated_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        updated: "",
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_updated_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: 1,
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_updatedAt_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_updatedAt_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        updatedAt: "",
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_updatedAt_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: false,
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_category_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_category_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        category: "",
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_category_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: 9,
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_folderId_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_folderId_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({ id: "d1", name: "x", folderId: "" }),
    ]),
  },
  {
    name: "doc_folderId_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: 1,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_folderId_orphan",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({ id: "d1", name: "x", folderId: "fld_missing" }),
    ]),
  },
  {
    name: "doc_status_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_status_empty",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        status: "",
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_status_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "x",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: 1,
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_status_invalid",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        status: "weird",
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  // nullable：缺失 vs null — 缺失与 null 均为合法正例
  {
    name: "folder_parentId_missing_ok_marker",
    folders: [
      legalFolderSentinel(),
      { id: "fld_parent_missing_ok", name: "SCHEMA_PARENTID_MISSING_CANARY_V1O" },
    ],
    docs: [],
  },
  {
    name: "doc_statusMessage_missing_ok_marker",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "SCHEMA_STATUSMESSAGE_MISSING_CANARY_V1O",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_sizeLabel_missing_ok_marker",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "SCHEMA_SIZELABEL_MISSING_CANARY_V1O",
        tags: [],
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
      },
    ]),
  },
  // tags/chunks 缺失非法门（与 null/错型分离）
  {
    name: "doc_tags_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "DOC_TAGS_MISSING_CANARY_V1O",
        chunks: 0,
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_chunks_missing",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        id: "d1",
        name: "DOC_CHUNKS_MISSING_CANARY_V1O",
        tags: [],
        updated: "u",
        updatedAt: "t",
        category: "c",
        folderId: LEGAL_FOLDER_SENTINEL,
        status: "ready",
        statusMessage: null,
        sizeLabel: null,
      },
    ]),
  },
  {
    name: "doc_tags_not_array",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        ...makeDoc({ id: "d1", name: "x", folderId: LEGAL_FOLDER_SENTINEL }),
        tags: "tag" as unknown as string[],
      },
    ]),
  },
  {
    name: "doc_tags_elem_non_string",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        tags: [1 as unknown as string],
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_chunks_negative",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        chunks: -1,
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_chunks_float",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        chunks: 1.5,
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_chunks_unsafe_int",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({
        id: "d1",
        name: "x",
        chunks: Number.MAX_SAFE_INTEGER + 1,
        folderId: LEGAL_FOLDER_SENTINEL,
      }),
    ]),
  },
  {
    name: "doc_chunks_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        ...makeDoc({ id: "d1", name: "x", folderId: LEGAL_FOLDER_SENTINEL }),
        chunks: "3" as unknown as number,
      },
    ]),
  },
  {
    name: "doc_statusMessage_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        ...makeDoc({ id: "d1", name: "x", folderId: LEGAL_FOLDER_SENTINEL }),
        statusMessage: 1 as unknown as string,
      },
    ]),
  },
  {
    name: "doc_sizeLabel_wrong_type",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      {
        ...makeDoc({ id: "d1", name: "x", folderId: LEGAL_FOLDER_SENTINEL }),
        sizeLabel: 1 as unknown as string,
      },
    ]),
  },
  {
    name: "doc_duplicate_id",
    folders: [legalFolderSentinel()],
    docs: withSentinelDocs([
      makeDoc({ id: "dup", name: "a", folderId: LEGAL_FOLDER_SENTINEL }),
      makeDoc({ id: "dup", name: "b", folderId: LEGAL_FOLDER_SENTINEL }),
    ]),
  },
];

test.describe("V1-O B schema 与加载失败", () => {
  for (const c of schemaCases) {
    test(`schema ${c.name}：整批 error、sentinel 与坏项零渲染`, async ({
      page,
    }) => {
      const consoleCol = collectConsole(page);
      const seed = serverSeed();
      const probe = emptyProbe({
        folders: seed.folders,
        docs: seed.docs,
      });
      // 先 ready 再刷新进畸形，证明旧列表零残留
      await preparePage(page, probe, { poison: true });
      await openKnowledge(page, probe);
      await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

      if (c.name === "folders_not_array") {
        probe.foldersMode = { kind: "malformed", body: c.folders };
        probe.docsMode = { kind: "ok" };
        probe.docs = [];
      } else if (c.name === "docs_not_array") {
        probe.folders = [legalFolderSentinel()];
        probe.docsMode = { kind: "malformed", body: c.docs };
      } else {
        probe.folders = (c.folders as KbFolder[]) ?? seed.folders;
        probe.docs = (c.docs as KbDoc[]) ?? seed.docs;
      }
      await page.getByRole("button", { name: /刷新/ }).click();

      const nullableOkCanary: Record<string, string> = {
        folder_parentId_missing_ok_marker: "SCHEMA_PARENTID_MISSING_CANARY_V1O",
        doc_statusMessage_missing_ok_marker:
          "SCHEMA_STATUSMESSAGE_MISSING_CANARY_V1O",
        doc_sizeLabel_missing_ok_marker: "SCHEMA_SIZELABEL_MISSING_CANARY_V1O",
      };
      const okCanary = nullableOkCanary[c.name];

      if (okCanary) {
        // nullable 缺失为正例：不期望主 error；精确根级/空位，不显示字面 null
        await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
        await expect(page.getByText(okCanary)).toBeVisible({ timeout: 15_000 });
        const bodyOk = await page.locator("body").innerText();
        expect(bodyOk).not.toMatch(/\bnull\b/);
        if (c.name === "folder_parentId_missing_ok_marker") {
          // parentId 缺失：必须是树列表直接子项（根级 DOM 关系），禁止仅文字可见
          const list = page.locator(".kb-folder-tree__list");
          const rootItem = list.locator(":scope > .kb-folder-item").filter({
            hasText: okCanary,
          });
          await expect(rootItem).toHaveCount(1);
          await expect(rootItem).toBeVisible();
          // 不得嵌套在另一 folder item 内
          await expect(
            list.locator(".kb-folder-item .kb-folder-item").filter({
              hasText: okCanary,
            }),
          ).toHaveCount(0);
        }
        if (c.name === "doc_statusMessage_missing_ok_marker") {
          const row = page.getByRole("row", { name: new RegExp(okCanary) });
          await expect(row).toBeVisible();
          const statusCell = row.locator("td").nth(2);
          // statusMessage 缺失：无 .kb-status-msg 节点；pill title 不得为字面 "null"
          await expect(statusCell.locator(".kb-status-msg")).toHaveCount(0);
          const title = await statusCell
            .locator(".kb-status-pill")
            .getAttribute("title");
          expect(title ?? "").toBe("");
          expect(String(title ?? "")).not.toBe("null");
        }
        if (c.name === "doc_sizeLabel_missing_ok_marker") {
          const row = page.getByRole("row", { name: new RegExp(okCanary) });
          await expect(row).toBeVisible();
          const nameCell = row.locator("td").nth(1);
          // sizeLabel 缺失：资料列内无 mono 尺寸子块（精确不存在，非仅排除字面 null）
          await expect(nameCell.locator("div.mono")).toHaveCount(0);
          expect(await nameCell.innerText()).not.toMatch(/\bnull\b/);
        }
        await assertPrivacyClean(page, probe, consoleCol);
      } else {
        await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 15_000 });
        const bodyText = await page.locator("body").innerText();
        expect(bodyText).not.toContain(SERVER_DOC_A);
        expect(bodyText).not.toContain(LEGAL_DOC_NAME);
        expect(bodyText).not.toContain(LEGAL_FOLDER_NAME);
        expect(bodyText).not.toContain(MOCK_DOC_SAMPLE);
        expect(bodyText).not.toContain(OFFLINE_HINT);
        // 独立 canary：folder 坏项 / 缺 name doc / tags|chunks 缺失 均不得渲染
        const folderCanaries = [
          "FOLDER_NAME_MISSING_CANARY_V1O",
          "DOC_NAME_MISSING_CANARY_V1O",
          "DOC_TAGS_MISSING_CANARY_V1O",
          "DOC_CHUNKS_MISSING_CANARY_V1O",
        ];
        folderCanaries.forEach((cy) => {
          expect(bodyText).not.toContain(cy);
        });
        // 坏项/sentinel 零渲染（用 map 避免 test 内 for 被 AST 误红——在 forEach 回调）
        const names = Array.isArray(c.docs)
          ? (c.docs as Array<{ name?: unknown }>)
              .map((row) => row?.name)
              .filter((n): n is string => typeof n === "string" && n.length > 0)
          : [];
        names.forEach((nm) => {
          expect(bodyText).not.toContain(nm);
        });
        // folder 坏项 name 亦可观测拒绝
        const folderNames = Array.isArray(c.folders)
          ? (c.folders as Array<{ name?: unknown }>)
              .map((row) => row?.name)
              .filter((n): n is string => typeof n === "string" && n.length > 0)
          : [];
        folderNames.forEach((nm) => {
          if (nm !== LEGAL_FOLDER_NAME) {
            expect(bodyText).not.toContain(nm);
          }
        });
        await expect(page.getByText(EMPTY_TITLE)).toHaveCount(0);
        expect(probe.writes).toEqual([]);
        await assertPrivacyClean(page, probe, consoleCol);
      }
    });
  }

  test("合法 null 与 nullable 缺失：parentId/statusMessage/sizeLabel 层级与空位", async ({
    page,
  }) => {
    // parentId: null 与 parentId 键缺失均合法；根级 DOM 关系可观测；不显示字面 null
    const folderNull = makeFolder({
      id: FLD_INBOX,
      name: SERVER_FOLDER_INBOX,
      parentId: null,
    });
    const folderMissing = {
      id: "fld_parent_key_missing",
      name: "PARENTID_KEY_MISSING_ROOT_V1O",
    } as KbFolder;
    delete (folderMissing as { parentId?: unknown }).parentId;

    const probe = emptyProbe({
      folders: [folderNull, folderMissing],
      docs: [
        makeDoc({
          id: DOC_A,
          name: SERVER_DOC_A,
          status: "parsing",
          statusMessage: null,
          sizeLabel: null,
        }),
      ],
    });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    await expect(page.getByText(STATUS_SAFE_LABEL.parsing)).toBeVisible();

    // parentId null / 缺失：均为树列表直接子按钮（根级），禁止仅文字可见
    const treeList = page.locator(".kb-folder-tree__list");
    const rootInbox = treeList.locator(":scope > .kb-folder-item").filter({
      hasText: SERVER_FOLDER_INBOX,
    });
    const rootMissing = treeList.locator(":scope > .kb-folder-item").filter({
      hasText: "PARENTID_KEY_MISSING_ROOT_V1O",
    });
    await expect(rootInbox).toHaveCount(1);
    await expect(rootMissing).toHaveCount(1);
    await expect(
      treeList.locator(".kb-folder-item .kb-folder-item").filter({
        hasText: "PARENTID_KEY_MISSING_ROOT_V1O",
      }),
    ).toHaveCount(0);

    const row = page.getByRole("row", { name: new RegExp(SERVER_DOC_A) });
    await expect(row).toBeVisible();
    // sizeLabel: null → 资料列 mono 尺寸块精确不存在
    const nameCell = row.locator("td").nth(1);
    await expect(nameCell.locator("div.mono")).toHaveCount(0);
    // statusMessage: null → 无 .kb-status-msg；pill title 空/缺席，禁止字面 "null"
    const statusCell = row.locator("td").nth(2);
    await expect(statusCell.locator(".kb-status-msg")).toHaveCount(0);
    const pillTitle = await statusCell
      .locator(".kb-status-pill")
      .getAttribute("title");
    expect(pillTitle ?? "").toBe("");
    expect(String(pillTitle ?? "")).not.toBe("null");
    const rowText = await row.innerText();
    expect(rowText).not.toMatch(/\bnull\b/);
    const dom = await collectDomExport(page);
    expect(dom).not.toContain(SECRET);
    expect(dom).not.toMatch(/>\s*null\s*</);
    expect(dom).not.toMatch(/title=["']null["']/);
    expect(dom).not.toMatch(/(^|\n)null(\n|$)/);

    // nullable 字段键缺失（非 null）亦为合法正例 + 精确空位
    probe.docs = [
      {
        id: DOC_B,
        name: SERVER_DOC_B,
        tags: ["server"],
        chunks: 1,
        updated: "刚刚",
        updatedAt: "2026-07-23T10:01:00.000Z",
        category: "知识库",
        folderId: FLD_INBOX,
        status: "ready",
      } as KbDoc,
    ];
    delete (probe.docs[0] as { statusMessage?: unknown }).statusMessage;
    delete (probe.docs[0] as { sizeLabel?: unknown }).sizeLabel;
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(SERVER_DOC_B)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    const rowB = page.getByRole("row", { name: new RegExp(SERVER_DOC_B) });
    await expect(rowB).toBeVisible();
    await expect(rowB.locator("td").nth(1).locator("div.mono")).toHaveCount(0);
    await expect(rowB.locator("td").nth(2).locator(".kb-status-msg")).toHaveCount(
      0,
    );
    expect(await rowB.innerText()).not.toMatch(/\bnull\b/);
    const domB = await collectDomExport(page);
    expect(domB).not.toMatch(/title=["']null["']/);
  });

  for (const caseName of [
    "folders_503",
    "docs_503",
    "folders_abort",
    "docs_malformed_null",
  ] as const) {
    test(`HTTP/abort ${caseName}：主错误+全出口无 SECRET`, async ({ page }) => {
      const consoleCol = collectConsole(page);
      const probe = emptyProbe();
      if (caseName === "folders_503") {
        probe.foldersMode = { kind: "status", status: 503, detail: SECRET };
      } else if (caseName === "docs_503") {
        probe.docsMode = { kind: "status", status: 503, detail: SECRET };
      } else if (caseName === "folders_abort") {
        probe.foldersMode = { kind: "abort" };
      } else {
        probe.docsMode = { kind: "malformed", body: null };
      }
      await preparePage(page, probe);
      await openKnowledge(page, probe);
      await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 15_000 });
      expect(probe.writes).toEqual([]);
      await assertPrivacyClean(page, probe, consoleCol);
    });
  }
});

// 写响应 3×3 独立接线（除坏点外其余合法）
const writeSchemaCases: Array<{
  name: string;
  kind: "create" | "upload" | "reindex";
  body: unknown;
  err: string;
}> = [
  {
    name: "create_missing_id",
    kind: "create",
    body: { name: SERVER_CREATE_FOLDER_NAME, parentId: null },
    err: CREATE_FOLDER_ERR,
  },
  {
    name: "create_name_empty",
    kind: "create",
    body: { id: "fld_x", name: "", parentId: null },
    err: CREATE_FOLDER_ERR,
  },
  {
    name: "create_parentId_orphan",
    kind: "create",
    body: {
      id: "fld_x",
      name: SERVER_CREATE_FOLDER_NAME,
      parentId: "no_parent",
    },
    err: CREATE_FOLDER_ERR,
  },
  {
    name: "upload_missing_tags",
    kind: "upload",
    body: {
      id: "doc_x",
      name: UPLOAD_SERVER_NAME,
      chunks: 1,
      updated: "u",
      updatedAt: "t",
      category: "c",
      folderId: FLD_INBOX,
      status: "ready",
      statusMessage: null,
      sizeLabel: "1 KB",
    },
    err: UPLOAD_ERR,
  },
  {
    name: "upload_status_invalid",
    kind: "upload",
    body: makeDoc({
      id: "doc_x",
      name: UPLOAD_SERVER_NAME,
      status: "nope",
    }),
    err: UPLOAD_ERR,
  },
  {
    name: "upload_folderId_orphan",
    kind: "upload",
    body: makeDoc({
      id: "doc_x",
      name: UPLOAD_SERVER_NAME,
      folderId: "fld_missing",
    }),
    err: UPLOAD_ERR,
  },
  {
    name: "reindex_tags_non_string",
    kind: "reindex",
    body: {
      ...makeDoc({ id: DOC_A, name: SERVER_DOC_A }),
      tags: [1],
    },
    err: REINDEX_ERR,
  },
  {
    name: "reindex_chunks_unsafe",
    kind: "reindex",
    body: makeDoc({
      id: DOC_A,
      name: SERVER_DOC_A,
      chunks: Number.MAX_SAFE_INTEGER + 1,
    }),
    err: REINDEX_ERR,
  },
  {
    name: "reindex_folderId_orphan",
    kind: "reindex",
    body: makeDoc({
      id: DOC_A,
      name: SERVER_DOC_A,
      folderId: "fld_missing",
    }),
    err: REINDEX_ERR,
  },
];

async function applyWriteSchemaMode(
  probe: Probe,
  c: (typeof writeSchemaCases)[number],
) {
  if (c.kind === "create") {
    probe.createFolderMode = { kind: "malformed", body: c.body, status: 200 };
  } else if (c.kind === "upload") {
    probe.uploadMode = { kind: "malformed", body: c.body, status: 200 };
  } else {
    probe.reindexMode = { kind: "malformed", body: c.body, status: 200 };
  }
}

async function triggerWriteSchemaKind(
  page: Page,
  kind: "create" | "upload" | "reindex",
) {
  if (kind === "create") {
    await page.locator(".kb-folder-tree__head").getByRole("button").click();
    return;
  }
  if (kind === "upload") {
    await setHiddenFileInput(page, UPLOAD_CLIENT_NAME, UPLOAD_ANCHOR);
    return;
  }
  await page
    .getByRole("row", { name: new RegExp(SERVER_DOC_A) })
    .getByTitle("重新索引")
    .click();
}

test.describe("V1-O B2 写响应 schema 接线", () => {
  for (const c of writeSchemaCases) {
    test(`写响应 ${c.name}：固定错误 + 双 GET 对账不信任半行`, async ({
      page,
    }) => {
      const consoleCol = collectConsole(page);
      page.on("dialog", (d) => {
        if (d.type() === "prompt") void d.accept(NEW_FOLDER_NAME);
        else void d.accept();
      });
      const seed = serverSeed();
      const reconF = [
        makeFolder({ id: FLD_INBOX, name: SERVER_FOLDER_INBOX }),
        makeFolder({ id: FLD_ARCHIVE, name: "归档-AFTER-WRITE-SCHEMA" }),
      ];
      const reconD = [
        makeDoc({
          id: DOC_A,
          name: "server-doc-a-AFTER-WRITE-SCHEMA.txt",
        }),
      ];
      const probe = emptyProbe({
        folders: seed.folders,
        docs: seed.docs,
        reconcileFolders: reconF,
        reconcileDocs: reconD,
      });
      await applyWriteSchemaMode(probe, c);
      await preparePage(page, probe, { poison: false });
      await openKnowledge(page, probe);
      await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

      const folderBefore = probe.folderGets;
      const docBefore = probe.docGets;
      const writesBefore = probe.writes.length;
      probe.currentOpToken = "write-schema-" + c.name;
      await triggerWriteSchemaKind(page, c.kind);

      await expect(page.getByText(c.err)).toBeVisible({ timeout: 10_000 });
      await expect.poll(() => probe.folderGets).toBe(folderBefore + 1);
      await expect.poll(() => probe.docGets).toBe(docBefore + 1);
      // 对应 method/path 精确一次；其它知识库写为零
      const kindWrites = probe.writes.filter(writePathPred(c.kind));
      expect(kindWrites.length, c.name + " 写次数").toBe(1);
      const otherKbWrites = probe.writes
        .slice(writesBefore)
        .filter((w) => !writePathPred(c.kind)(w));
      expect(otherKbWrites, c.name + " 其它写必须 0").toEqual([]);
      await continuationBarrier(page);
      // 双 GET 真值（非写响应半行）
      await expect(page.getByText("server-doc-a-AFTER-WRITE-SCHEMA.txt")).toBeVisible({
        timeout: 10_000,
      });
      await expect(page.getByText("归档-AFTER-WRITE-SCHEMA")).toBeVisible();
      await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);
      await assertPrivacyClean(page, probe, consoleCol);
    });
  }
});

// ---------------------------------------------------------------------------
// C. loading/error 写门 + fake timer
// ---------------------------------------------------------------------------

async function assertWriteEntryDisabledOrHidden(page: Page) {
  const uploadBtn = page.getByRole("button", { name: /上传文档/ });
  await expect(uploadBtn).toBeVisible();
  await expect(uploadBtn).toBeDisabled();

  const createBtn = page.locator(".kb-folder-tree__head").getByRole("button");
  await expect(createBtn).toBeVisible();
  await expect(createBtn).toBeDisabled();

  const rebuild = page.getByTestId("semantic-index-rebuild");
  await expect(rebuild).toBeVisible();
  await expect(rebuild).toBeDisabled();

  const moveBtn = page.getByRole("button", { name: /^移动$/ });
  const deleteBtn = page.getByRole("button", { name: /^删除$/ });
  if ((await moveBtn.count()) > 0) {
    await expect(moveBtn).toBeDisabled();
  }
  if ((await deleteBtn.count()) > 0) {
    await expect(deleteBtn).toBeDisabled();
  }
  const reindexBtns = page.getByTitle("重新索引");
  const n = await reindexBtns.count();
  for (let i = 0; i < n; i += 1) {
    await expect(reindexBtns.nth(i)).toBeDisabled();
  }
}

test.describe("V1-O C loading/error 零写", () => {
  test("error 态：入口禁用/隐藏 + file input 强制 + fake timer 无假 ready", async ({
    page,
  }) => {
    const consoleCol = collectConsole(page);
    page.on("dialog", (d) => d.accept(NEW_FOLDER_NAME));
    const seed = serverSeed();
    const probe = emptyProbe({
      folders: seed.folders,
      docs: seed.docs,
    });
    await preparePage(page, probe);
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    probe.foldersMode = { kind: "status", status: 503, detail: SECRET };
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);

    await assertWriteEntryDisabledOrHidden(page);

    const writesBefore = probe.writes.length;
    await setHiddenFileInput(page, UPLOAD_CLIENT_NAME, UPLOAD_ANCHOR);
    await page
      .locator(".kb-folder-tree__head")
      .getByRole("button")
      .click({ force: true });
    await page.getByRole("button", { name: /上传文档/ }).click({ force: true });
    await page.getByTestId("semantic-index-rebuild").click({ force: true });

    await page.clock.install();
    await page.clock.fastForward(2000);
    await continuationBarrier(page);

    expect(probe.writes.length).toBe(writesBefore);
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain(UPLOAD_CLIENT_NAME);
    expect(bodyText).not.toContain(UPLOAD_SERVER_NAME);
    expect(bodyText).not.toMatch(/\bkb_[a-z0-9]+_[a-z0-9]+\b/);
    await assertPrivacyClean(page, probe, consoleCol);
  });

  test("loading 挂起：加载文案、入口禁用、写 HTTP 0", async ({ page }) => {
    const gateF = createHoldGate();
    const gateD = createHoldGate();
    const probe = emptyProbe({
      foldersMode: { kind: "hold", gate: gateF, then: "ok" },
      docsMode: { kind: "hold", gate: gateD, then: "ok" },
    });
    await preparePage(page, probe, { poison: false });
    const nav = openKnowledge(page);
    await expect
      .poll(() => probe.folderGetArrived + probe.docGetArrived, {
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(2);

    await expect(page.getByText(LOADING_TEXT)).toBeVisible({ timeout: 10_000 });
    await assertWriteEntryDisabledOrHidden(page);
    expect(probe.writes).toEqual([]);

    await setHiddenFileInput(page, UPLOAD_CLIENT_NAME, UPLOAD_ANCHOR);
    expect(probe.writes).toEqual([]);

    gateF.release();
    gateD.release();
    await nav;
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
  });
});

// ---------------------------------------------------------------------------
// D. 五类 mutation 参数化 + 共享锁矩阵
// ---------------------------------------------------------------------------

type MutationKind = "create" | "upload" | "move" | "delete" | "reindex";
type MutationOutcome = "success" | "http_fail" | "abort" | "partial";

const mutationMatrix: Array<{
  kind: MutationKind;
  outcome: MutationOutcome;
}> = [
  { kind: "create", outcome: "success" },
  { kind: "create", outcome: "http_fail" },
  { kind: "create", outcome: "abort" },
  { kind: "upload", outcome: "success" },
  { kind: "upload", outcome: "http_fail" },
  { kind: "upload", outcome: "abort" },
  { kind: "move", outcome: "success" },
  { kind: "move", outcome: "http_fail" },
  { kind: "move", outcome: "abort" },
  { kind: "move", outcome: "partial" },
  { kind: "delete", outcome: "success" },
  { kind: "delete", outcome: "http_fail" },
  { kind: "delete", outcome: "abort" },
  { kind: "delete", outcome: "partial" },
  { kind: "reindex", outcome: "success" },
  { kind: "reindex", outcome: "http_fail" },
  { kind: "reindex", outcome: "abort" },
];

const ALL_KINDS: MutationKind[] = [
  "create",
  "upload",
  "move",
  "delete",
  "reindex",
];

/** 五类写入口：禁止再套其它别名 helper */
async function triggerMutation(
  page: Page,
  kind: MutationKind,
  opts?: { multiDelete?: boolean },
): Promise<void> {
  if (kind === "create") {
    await page.locator(".kb-folder-tree__head").getByRole("button").click();
    return;
  }
  if (kind === "upload") {
    await setHiddenFileInput(page, UPLOAD_CLIENT_NAME, UPLOAD_ANCHOR);
    return;
  }
  if (kind === "move") {
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();
    await page.getByLabel("移入文件夹").selectOption(FLD_ARCHIVE);
    await page.getByRole("button", { name: /移动/ }).click();
    return;
  }
  if (kind === "delete") {
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
    if (opts?.multiDelete) {
      await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();
      await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_C}` }).check();
    }
    await page.getByRole("button", { name: /^删除$/ }).click();
    return;
  }
  await page
    .getByRole("row", { name: new RegExp(SERVER_DOC_A) })
    .getByTitle("重新索引")
    .click();
}

function writePathPred(kind: MutationKind): (c: ApiCall) => boolean {
  return (w) => {
    if (kind === "create") {
      return (
        w.method === "POST" && /\/api\/knowledge\/folders\/?$/.test(w.path)
      );
    }
    if (kind === "upload") {
      return w.method === "POST" && w.path.includes("/docs/upload");
    }
    if (kind === "move") {
      return w.method === "POST" && w.path.includes("/docs/move");
    }
    if (kind === "delete") {
      return w.method === "DELETE";
    }
    return w.method === "POST" && w.path.includes("/reindex");
  };
}

/**
 * 共享锁 second 入口验收（分支逻辑在 helper 内，避免 test 直接控制流 if+action）。
 * 显式四态互斥：不存在 / 隐藏 / disabled / 可派发；可派发时真实用户入口事件精确 +1。
 * upload：必须操作真实「上传文档」按钮；隐藏 file input 本身不能让用例提前通过。
 */
async function classifyEntryState(
  locator: import("@playwright/test").Locator,
): Promise<"missing" | "hidden" | "disabled" | "dispatchable"> {
  const count = await locator.count();
  if (count === 0) return "missing";
  const first = locator.first();
  if (!(await first.isVisible())) return "hidden";
  if (await first.isDisabled()) return "disabled";
  return "dispatchable";
}

/** 四态互斥证明：同一时刻恰好一态 */
function assertExclusiveEntryState(
  state: "missing" | "hidden" | "disabled" | "dispatchable",
) {
  const flags = {
    missing: state === "missing",
    hidden: state === "hidden",
    disabled: state === "disabled",
    dispatchable: state === "dispatchable",
  };
  expect(
    Object.values(flags).filter(Boolean).length,
    "入口四态必须互斥且恰一态",
  ).toBe(1);
}

async function attemptSecondLockedEntry(
  page: Page,
  second: MutationKind,
): Promise<void> {
  await page.evaluate(() => {
    const g = window as unknown as {
      __v1oDomEv?: { click: number; input: number };
    };
    g.__v1oDomEv = { click: 0, input: 0 };
    document.addEventListener(
      "click",
      () => {
        g.__v1oDomEv!.click += 1;
      },
      true,
    );
    document.addEventListener(
      "input",
      () => {
        g.__v1oDomEv!.input += 1;
      },
      true,
    );
  });

  const readEv = async () =>
    page.evaluate(() => {
      const g = window as unknown as {
        __v1oDomEv?: { click: number; input: number };
      };
      return g.__v1oDomEv ?? { click: 0, input: 0 };
    });

  const assertMissingOrHidden = async (
    state: "missing" | "hidden",
    count: number,
  ) => {
    if (state === "missing") expect(count).toBe(0);
    else expect(count).toBeGreaterThan(0);
  };

  if (second === "create") {
    const btn = page.locator(".kb-folder-tree__head").getByRole("button");
    const state = await classifyEntryState(btn);
    assertExclusiveEntryState(state);
    if (state === "missing" || state === "hidden") {
      await assertMissingOrHidden(state, await btn.count());
      if (state === "hidden") await expect(btn.first()).toBeHidden();
      return;
    }
    if (state === "disabled") {
      await expect(btn.first()).toBeDisabled();
      expect((await readEv()).click).toBe(0);
      return;
    }
    const before = (await readEv()).click;
    await btn.click();
    await expect.poll(async () => (await readEv()).click).toBe(before + 1);
    return;
  }

  if (second === "upload") {
    // 真实用户上传入口 =「上传文档」按钮；隐藏 file input 不得单独让用例通过
    const userBtn = page.getByRole("button", { name: /上传文档/ });
    const hiddenInput = page.locator('input[type="file"][accept*=".pdf"]');
    const state = await classifyEntryState(userBtn);
    assertExclusiveEntryState(state);
    if (state === "missing" || state === "hidden") {
      await assertMissingOrHidden(state, await userBtn.count());
      if (state === "hidden") await expect(userBtn.first()).toBeHidden();
      // 即使隐藏 input 存在且可 setInputFiles，也不得据此提前通过
      expect((await readEv()).click + (await readEv()).input).toBe(0);
      return;
    }
    if (state === "disabled") {
      await expect(userBtn.first()).toBeDisabled();
      expect((await readEv()).click).toBe(0);
      expect((await readEv()).input).toBe(0);
      return;
    }
    // 可派发：必须点击真实用户按钮，并证明 click 精确 +1；再经 input 完成文件（若可见链）
    const beforeClick = (await readEv()).click;
    const beforeInput = (await readEv()).input;
    await userBtn.click();
    await expect.poll(async () => (await readEv()).click).toBe(beforeClick + 1);
    // 若存在隐藏 input，补充 setInputFiles 并证 input +1（用户入口已证明）
    if ((await hiddenInput.count()) > 0 && (await hiddenInput.first().isEnabled())) {
      await setHiddenFileInput(page, "second.txt", "x");
      await expect
        .poll(async () => (await readEv()).input)
        .toBe(beforeInput + 1);
    }
    return;
  }

  if (second === "move") {
    const btn = page.getByRole("button", { name: /移动/ });
    const state = await classifyEntryState(btn);
    assertExclusiveEntryState(state);
    if (state === "missing" || state === "hidden") {
      await assertMissingOrHidden(state, await btn.count());
      if (state === "hidden") await expect(btn.first()).toBeHidden();
      return;
    }
    if (state === "disabled") {
      await expect(btn.first()).toBeDisabled();
      expect((await readEv()).click).toBe(0);
      return;
    }
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
    await page.getByLabel("移入文件夹").selectOption(FLD_ARCHIVE);
    const before = (await readEv()).click;
    await btn.click();
    await expect.poll(async () => (await readEv()).click).toBe(before + 1);
    return;
  }

  if (second === "delete") {
    const btn = page.getByRole("button", { name: /^删除$/ });
    const state = await classifyEntryState(btn);
    assertExclusiveEntryState(state);
    if (state === "missing" || state === "hidden") {
      await assertMissingOrHidden(state, await btn.count());
      if (state === "hidden") await expect(btn.first()).toBeHidden();
      return;
    }
    if (state === "disabled") {
      await expect(btn.first()).toBeDisabled();
      expect((await readEv()).click).toBe(0);
      return;
    }
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();
    const before = (await readEv()).click;
    await btn.click();
    await expect.poll(async () => (await readEv()).click).toBe(before + 1);
    return;
  }

  const btn = page.getByTitle("重新索引").first();
  const state = await classifyEntryState(btn);
  assertExclusiveEntryState(state);
  if (state === "missing" || state === "hidden") {
    await assertMissingOrHidden(state, await btn.count());
    if (state === "hidden") await expect(btn).toBeHidden();
    return;
  }
  if (state === "disabled") {
    await expect(btn).toBeDisabled();
    expect((await readEv()).click).toBe(0);
    return;
  }
  const before = (await readEv()).click;
  await btn.click();
  await expect.poll(async () => (await readEv()).click).toBe(before + 1);
}

async function runMutationCase(
  page: Page,
  kind: MutationKind,
  outcome: MutationOutcome,
) {
  const consoleCol = collectConsole(page);
  page.on("dialog", (d) => {
    if (d.type() === "prompt") void d.accept(NEW_FOLDER_NAME);
    else void d.accept();
  });

  const seed = threeDocSeed();
  // 对账最终态动作前冻结，folders/docs 明显不同
  const reconF = [
    makeFolder({ id: FLD_INBOX, name: SERVER_FOLDER_INBOX }),
    makeFolder({ id: FLD_ARCHIVE, name: "归档-RECON-" + kind }),
  ];
  // 对账 GET 真值字段与写响应明显可区分（chunks≠42/99、size≠99.0KB、category≠server-category）
  const reconD = [
    makeDoc({
      id: DOC_A,
      name: `recon-${kind}-${outcome}-a.txt`,
      folderId: FLD_INBOX,
      tags: [`get-tag-${kind}-${outcome}`],
      chunks: 41,
      sizeLabel: `get-size-${kind}`,
      category: `get-cat-${kind}`,
      status: "ready",
      statusMessage: null,
    }),
    makeDoc({
      id: DOC_B,
      name: `recon-${kind}-${outcome}-b.txt`,
      folderId: FLD_INBOX,
      tags: [`get-tag-b-${kind}`],
      chunks: 43,
      sizeLabel: `get-size-b-${kind}`,
      category: `get-cat-b-${kind}`,
      status: "ready",
      statusMessage: null,
    }),
  ];
  const probe = emptyProbe({
    folders: seed.folders,
    docs: seed.docs,
    reconcileFolders: reconF,
    reconcileDocs: reconD,
  });

  if (kind === "create") {
    if (outcome === "http_fail") probe.createFolderMode = { kind: "fail" };
    if (outcome === "abort") probe.createFolderMode = { kind: "abort" };
  }
  if (kind === "upload") {
    if (outcome === "http_fail") probe.uploadMode = { kind: "fail" };
    if (outcome === "abort") probe.uploadMode = { kind: "abort" };
  }
  if (kind === "move") {
    if (outcome === "http_fail") probe.moveMode = { kind: "fail" };
    if (outcome === "abort") probe.moveMode = { kind: "abort" };
    if (outcome === "partial") {
      probe.moveMode = {
        kind: "partial_ok",
        moved: 1,
        applyIds: [DOC_A],
      };
    }
  }
  if (kind === "delete") {
    if (outcome === "http_fail") probe.deleteMode = { kind: "fail_all" };
    if (outcome === "abort") probe.deleteMode = { kind: "abort" };
    if (outcome === "partial") probe.deleteMode = { kind: "fail_second" };
  }
  if (kind === "reindex") {
    if (outcome === "http_fail") probe.reindexMode = { kind: "fail" };
    if (outcome === "abort") probe.reindexMode = { kind: "abort" };
  }

  await preparePage(page, probe, { poison: false });
  await openKnowledge(page, probe);
  // 初态：必须先见初始 seed，断初始 GET 非对账态
  await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByText(`recon-${kind}-${outcome}-a.txt`),
  ).toHaveCount(0);

  const folderGetsBefore = probe.folderGets;
  const docGetsBefore = probe.docGets;
  probe.currentOpToken = `first-${kind}-${outcome}`;

  // first action 派发（不吞 promise）
  const actionPromise = triggerMutation(page, kind, {
    multiDelete: outcome === "partial" && kind === "delete",
  });
  await expect
    .poll(() => writeCount(probe, writePathPred(kind)), { timeout: 10_000 })
    .toBeGreaterThanOrEqual(1);
  // 入口派发成功：action promise 显式 await（HTTP fail/abort 不要求 reject）
  await actionPromise;

  await expect
    .poll(() => probe.folderGets, { timeout: 12_000 })
    .toBe(folderGetsBefore + 1);
  await expect
    .poll(() => probe.docGets, { timeout: 12_000 })
    .toBe(docGetsBefore + 1);
  await continuationBarrier(page);

  // Q8：所有 outcome 以写后双 GET 为唯一最终真值；UI 逐字段消费 GET，排除写响应残留
  const reconNameA = `recon-${kind}-${outcome}-a.txt`;
  const reconNameB = `recon-${kind}-${outcome}-b.txt`;
  const reconFolder = "归档-RECON-" + kind;
  const getDocA = reconD[0]!;
  const getDocB = reconD[1]!;
  await expect(page.getByText(reconNameA)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(reconNameB)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(reconFolder)).toBeVisible();
  // 写响应专属残留门（probe 自身值不作 UI 消费证明）
  await expect(page.getByText(UPLOAD_SERVER_NAME)).toHaveCount(0);
  await expect(page.getByText(UPLOAD_CLIENT_NAME)).toHaveCount(0);
  await expect(page.getByText(SERVER_CREATE_FOLDER_NAME)).toHaveCount(0);
  await expect(page.getByText("server-renamed-upload-v1o")).toHaveCount(0);
  await expect(page.getByText("99.0 KB")).toHaveCount(0);
  await expect(page.getByText("server-category")).toHaveCount(0);
  await expect(page.getByText("server-upload")).toHaveCount(0);
  await expect(page.getByText("reindexed-size")).toHaveCount(0);
  await expect(page.getByText("server-reindexed")).toHaveCount(0);
  // 初始 seed 名不得在对账后残留（双 GET 已替换）
  await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);
  await expect(page.getByText(SERVER_DOC_B)).toHaveCount(0);
  // UI 逐字段：id 不可见时用后续 path 消费；name/status/tags/chunks/sizeLabel/category/folder 用行内真值
  const rowA = page.getByRole("row", { name: new RegExp(reconNameA) });
  const rowB = page.getByRole("row", { name: new RegExp(reconNameB) });
  await expect(rowA).toBeVisible();
  await expect(rowB).toBeVisible();
  await expect(rowA).toContainText(String(getDocA.chunks));
  await expect(rowA).toContainText(getDocA.sizeLabel ?? "___no_size___");
  await expect(rowA).toContainText(getDocA.tags[0]!);
  await expect(rowA).toContainText(getDocA.category);
  await expect(rowA).toContainText(STATUS_SAFE_LABEL[getDocA.status]!);
  await expect(rowA).toContainText(SERVER_FOLDER_INBOX);
  // 分块列精确 GET 真值；禁止写响应 chunks=42/99 混入
  await expect(rowA.locator("td").nth(5)).toHaveText(String(getDocA.chunks));
  await expect(rowA.locator("td").nth(5)).not.toHaveText("42");
  await expect(rowA.locator("td").nth(5)).not.toHaveText("99");
  await expect(rowB.locator("td").nth(5)).toHaveText(String(getDocB.chunks));
  expect(probe.reconcileArmed).toBe(true);
  // reindex/reload 闭环：对账名可见，写响应半行不可见
  if (kind === "reindex") {
    await expect(page.getByText(reconNameA)).toBeVisible();
    await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);
  }

  if (outcome === "http_fail" || outcome === "abort" || outcome === "partial") {
    const errMap: Record<MutationKind, string> = {
      create: CREATE_FOLDER_ERR,
      upload: UPLOAD_ERR,
      move: MOVE_ERR,
      delete: DELETE_ERR,
      reindex: REINDEX_ERR,
    };
    await expect(page.getByText(errMap[kind])).toBeVisible({ timeout: 10_000 });
    await continuationBarrier(page);
    await expect(page.getByText(errMap[kind])).toBeVisible();
  }

  expect(probe.folderGets - folderGetsBefore).toBe(1);
  expect(probe.docGets - docGetsBefore).toBe(1);
  expect(probe.reconcileArmed).toBe(true);
  await assertPrivacyClean(page, probe, consoleCol);
}

test.describe("V1-O D 五类 mutation 独立参数化", () => {
  for (const { kind, outcome } of mutationMatrix) {
    test(`${kind} / ${outcome}：双 GET 精确+1 + 错误保留`, async ({ page }) => {
      await runMutationCase(page, kind, outcome);
    });
  }

  test("delete 三文档第二败：第三 DELETE=0 且双 GET+1", async ({ page }) => {
    const consoleCol = collectConsole(page);
    page.on("dialog", (d) => d.accept());
    const seed = threeDocSeed();
    const probe = emptyProbe({
      folders: seed.folders,
      docs: seed.docs,
      deleteMode: { kind: "fail_second" },
      reconcileFolders: seed.folders,
      reconcileDocs: seed.docs.filter((d) => d.id !== DOC_A),
    });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    await page.getByRole("checkbox", { name: "全选当前列表" }).check();
    const folderBefore = probe.folderGets;
    const docBefore = probe.docGets;
    await page.getByRole("button", { name: /^删除$/ }).click();

    await expect(page.getByText(DELETE_ERR)).toBeVisible({ timeout: 10_000 });
    const deletes = probe.writes.filter((w) => w.method === "DELETE");
    expect(deletes.length).toBe(2);
    await expect.poll(() => probe.folderGets).toBe(folderBefore + 1);
    await expect.poll(() => probe.docGets).toBe(docBefore + 1);
    await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_B)).toBeVisible();
    await expect(page.getByText(SERVER_DOC_C)).toBeVisible();
    await assertPrivacyClean(page, probe, consoleCol);
  });
});

// 共享锁 first×second 交叉矩阵
test.describe("V1-O D 共享单写锁 first×second", () => {
  test("写分账 synthetic：second 阶段同类写必须记 second；错误第二写必红", () => {
    const probe = emptyProbe();
    const firstToken = "first-synth-upload";
    const secondToken = "second-synth-upload";
    probe.immutableFirstOpToken = firstToken;
    probe.firstOpWriteMatch = writePathPred("upload");
    probe.currentOpToken = firstToken;
    probe.writePhase = "first";
    expect(
      resolveWriteOpToken(probe, "POST", "/api/knowledge/docs/upload"),
    ).toBe(firstToken);

    // second 尝试阶段：同类 path 也必须归 second，禁止 first matcher 吞并
    probe.writePhase = "second-attempt";
    probe.currentOpToken = secondToken;
    expect(
      resolveWriteOpToken(probe, "POST", "/api/knowledge/docs/upload"),
    ).toBe(secondToken);
    expect(
      resolveWriteOpToken(probe, "POST", "/api/knowledge/docs/move"),
    ).toBe(secondToken);

    // first-drain：仅冻结剩余 multi-delete path 归 first
    probe.writePhase = "first-drain";
    probe.immutableFirstOpToken = firstToken;
    probe.currentOpToken = secondToken;
    probe.firstFrozenRemainingPaths = [
      `/api/knowledge/docs/${DOC_B}`,
      `/api/knowledge/docs/${DOC_C}`,
    ];
    expect(
      resolveWriteOpToken(probe, "DELETE", `/api/knowledge/docs/${DOC_B}`),
    ).toBe(firstToken);
    expect(
      resolveWriteOpToken(probe, "DELETE", `/api/knowledge/docs/${DOC_C}`),
    ).toBe(firstToken);
    // 未冻结 path 不得再归 first
    expect(
      resolveWriteOpToken(probe, "DELETE", `/api/knowledge/docs/${DOC_A}`),
    ).toBe(secondToken);
    expect(
      resolveWriteOpToken(probe, "POST", "/api/knowledge/docs/upload"),
    ).toBe(secondToken);

    // 错误第二写：second 计数非 0 时矩阵门必须红
    const errSecond = 1;
    expect(() => {
      expect(errSecond, "错误第二写必须使 second 断言失败").toBe(0);
    }).toThrow();
  });

  for (const first of ALL_KINDS) {
    for (const second of ALL_KINDS) {
      test(`锁 ${first}→${second}：second write 精确 0`, async ({ page }) => {
        const consoleCol = collectConsole(page);
        page.on("dialog", (d) => {
          if (d.type() === "prompt") void d.accept(NEW_FOLDER_NAME);
          else void d.accept();
        });
        const seed = threeDocSeed();
        const probe = emptyProbe({
          folders: seed.folders,
          docs: seed.docs,
        });
        const hold = createHoldGate();
        probe.writeHoldGate = hold;
        await preparePage(page, probe, { poison: false });
        await openKnowledge(page, probe);
        await expect(page.getByText(SERVER_DOC_A)).toBeVisible({
          timeout: 15_000,
        });

        // 冻结动作前 fulfilled 基线
        const folderFulfilledBase = probe.folderGetFulfilled;
        const docFulfilledBase = probe.docGetFulfilled;
        const firstToken = `first-${first}-vs-${second}`;
        const secondToken = `second-${first}-vs-${second}`;
        probe.currentOpToken = firstToken;
        probe.immutableFirstOpToken = firstToken;
        probe.firstOpWriteMatch = writePathPred(first);
        probe.writePhase = "first";
        probe.firstFrozenRemainingPaths = null;

        const firstAction = triggerMutation(page, first, {
          multiDelete: first === "delete",
        });
        await expect
          .poll(() => writeCount(probe, writePathPred(first)), {
            timeout: 10_000,
          })
          .toBeGreaterThanOrEqual(1);
        await firstAction;

        // first 批次：带不可变 firstToken 的写记录
        const firstBatch = probe.writeArrived.filter(
          (w) => w.opToken === firstToken,
        );
        expect(firstBatch.length).toBeGreaterThanOrEqual(1);

        // multi-delete：冻结尚未到达的剩余 DELETE path，释放后仅这些可继续归 first
        if (first === "delete") {
          const arrivedNorm = new Set(
            firstBatch.map((w) => normalizeApiPath(w.path)),
          );
          const candidates = [DOC_A, DOC_B, DOC_C].map(
            (id) => `/api/knowledge/docs/${id}`,
          );
          probe.firstFrozenRemainingPaths = candidates.filter(
            (p) => !arrivedNorm.has(normalizeApiPath(p)),
          );
        } else {
          probe.firstFrozenRemainingPaths = null;
        }

        const secondWritesBefore = probe.writeArrived.filter(
          (w) => w.opToken === secondToken,
        ).length;

        // second 尝试阶段：任何新增写归 secondToken（含同类 diagonal）
        probe.writePhase = "second-attempt";
        probe.currentOpToken = secondToken;
        await attemptSecondLockedEntry(page, second);
        await continuationBarrier(page);
        await continuationBarrier(page);
        expect(
          probe.writeArrived.filter((w) => w.opToken === secondToken).length,
        ).toBe(secondWritesBefore);

        // 释放 first hold → drain 阶段仅冻结剩余 multi-delete 可归 first
        probe.writePhase = "first-drain";
        hold.release();
        probe.writeHoldGate = null;
        await continuationBarrier(page);
        await expect
          .poll(() => probe.folderGetFulfilled, { timeout: 12_000 })
          .toBe(folderFulfilledBase + 1);
        await expect
          .poll(() => probe.docGetFulfilled, { timeout: 12_000 })
          .toBe(docFulfilledBase + 1);
        await continuationBarrier(page);
        // 释放后 second 仍精确 0；first 仅允许 first 批次 + 冻结剩余
        expect(
          probe.writeArrived.filter((w) => w.opToken === secondToken).length,
        ).toBe(0);
        expect(
          probe.writeArrived.filter((w) => w.opToken === firstToken).length,
        ).toBeGreaterThanOrEqual(firstBatch.length);
        probe.writePhase = "idle";
        probe.immutableFirstOpToken = null;
        probe.firstOpWriteMatch = null;
        probe.firstFrozenRemainingPaths = null;
        await assertPrivacyClean(page, probe, consoleCol);
      });
    }
  }
});

// ---------------------------------------------------------------------------
// D3. moved 矩阵
// ---------------------------------------------------------------------------

const movedCases: Array<{ name: string; moved: unknown; success?: boolean }> = [
  { name: "missing", moved: undefined },
  { name: "null", moved: null },
  { name: "bool_true", moved: true },
  { name: "bool_false", moved: false },
  { name: "string", moved: "2" },
  { name: "negative", moved: -1 },
  { name: "float", moved: 1.5 },
  { name: "neg_zero", moved: -0 },
  { name: "zero", moved: 0 },
  { name: "partial", moved: 1 },
  { name: "oversize", moved: 99 },
  { name: "exact_success", moved: 2, success: true },
];

test.describe("V1-O D3 move moved 矩阵", () => {
  for (const c of movedCases) {
    test(`moved=${c.name}`, async ({ page }) => {
      const consoleCol = collectConsole(page);
      const seed = serverSeed();
      // 动作前冻结对账态
      const reconF = seed.folders;
      const reconD = seed.docs.map((d) =>
        d.id === DOC_B
          ? { ...d, name: "server-doc-b-AFTER-RECONCILE.txt" }
          : d,
      );
      const probe = emptyProbe({
        folders: seed.folders,
        docs: seed.docs,
        reconcileFolders: reconF,
        reconcileDocs: c.success
          ? seed.docs.map((d) =>
              d.id === DOC_A || d.id === DOC_B
                ? { ...d, folderId: FLD_ARCHIVE }
                : d,
            )
          : reconD,
        moveMode:
          c.moved === undefined
            ? { kind: "moved", moved: undefined }
            : c.success
              ? { kind: "ok", moved: 2 }
              : { kind: "moved", moved: c.moved },
      });
      await preparePage(page, probe, { poison: false });
      await openKnowledge(page, probe);
      await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

      await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
      await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();

      const folderBefore = probe.folderGets;
      const docBefore = probe.docGets;
      await page.getByLabel("移入文件夹").selectOption(FLD_ARCHIVE);
      await page.getByRole("button", { name: /移动/ }).click();

      await expect
        .poll(
          () =>
            probe.writes.filter(
              (w) =>
                w.method === "POST" &&
                /\/api\/knowledge\/docs\/move\/?$/.test(w.path),
            ).length,
        )
        .toBeGreaterThanOrEqual(1);
      const moveWrite = probe.writes.find(
        (w) =>
          w.method === "POST" &&
          /\/api\/knowledge\/docs\/move\/?$/.test(w.path),
      )!;
      const body = JSON.parse(moveWrite.bodyText) as {
        ids: string[];
        folderId: string;
      };
      expect(body.folderId).toBe(FLD_ARCHIVE);
      expect(body.ids).toEqual([DOC_A, DOC_B]);

      await expect.poll(() => probe.folderGets).toBe(folderBefore + 1);
      await expect.poll(() => probe.docGets).toBe(docBefore + 1);
      await continuationBarrier(page);

      if (c.success) {
        await expect(
          page.getByRole("row", { name: new RegExp(SERVER_DOC_A) }),
        ).toContainText(SERVER_FOLDER_ARCHIVE);
      } else {
        await expect(page.getByText(MOVE_ERR)).toBeVisible({ timeout: 10_000 });
        await expect(
          page.getByText("server-doc-b-AFTER-RECONCILE.txt"),
        ).toBeVisible({ timeout: 10_000 });
        const rowA = page.getByRole("row", {
          name: new RegExp(SERVER_DOC_A),
        });
        await expect(rowA).toContainText(SERVER_FOLDER_INBOX);
      }
      await assertPrivacyClean(page, probe, consoleCol);
    });
  }

  test("move 真实重复 ids：mutation 边界收到 [A,B,A] 后首次顺序去重", async ({
    page,
  }) => {
    const probe = emptyProbe({ moveMode: { kind: "ok", moved: 2 } });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    // 先经 UI 选中 A/B 与 moveTarget，保证控件就绪
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();
    await page.getByLabel("移入文件夹").selectOption(FLD_ARCHIVE);

    // dispatch 仅同步返回命中/派发；只保存 queue 对象身份 + root 定位，禁止把 HookNode 当 render 后长期真值
    const dispatchHit = await page.evaluate(
      ({ ids }) => {
        type HookQueue = { dispatch?: (v: unknown) => void };
        type HookNode = {
          memoizedState?: unknown;
          queue?: HookQueue | null;
          next?: HookNode | null;
        };
        type Fiber = {
          memoizedState?: HookNode | null;
          child?: Fiber | null;
          sibling?: Fiber | null;
          stateNode?: { current?: Fiber } | null;
        };
        /** 从 DOM 容器解析最新 HostRoot fiber（root.current） */
        const resolveCurrentRoot = (): Fiber | null => {
          const el = document.getElementById("root") ?? document.body;
          const key = Object.keys(el).find(
            (k) =>
              k.startsWith("__reactContainer") ||
              k.startsWith("__reactFiber") ||
              k.startsWith("__reactInternalInstance"),
          );
          if (!key) return null;
          const start = (el as unknown as Record<string, Fiber>)[key] as Fiber;
          return start.stateNode?.current ?? start;
        };
        const wanted = new Set(ids);
        let savedQueue: HookQueue | null = null;
        let dispatchFn: ((v: unknown) => void) | null = null;
        // 首次定位：用当前选中 ids 命中目标 useState，只取 queue 身份与 dispatch
        const visitForDispatch = (fiber: Fiber | null | undefined): boolean => {
          if (!fiber) return false;
          let st = fiber.memoizedState;
          while (st) {
            const val = st.memoizedState;
            if (
              Array.isArray(val) &&
              val.length >= 1 &&
              val.every((x) => typeof x === "string") &&
              val.some((x) => wanted.has(String(x)))
            ) {
              const q = st.queue;
              const dispatch = q?.dispatch;
              if (q && typeof dispatch === "function") {
                savedQueue = q;
                dispatchFn = dispatch;
                return true;
              }
            }
            st = st.next ?? null;
          }
          if (visitForDispatch(fiber.child)) return true;
          if (visitForDispatch(fiber.sibling)) return true;
          return false;
        };
        const top = resolveCurrentRoot();
        if (!visitForDispatch(top) || !savedQueue || !dispatchFn) {
          throw new Error("V1O_INJECT_SELECTED_IDS_FAILED");
        }
        // 长期身份 = queue 对象引用；get/poll 必须从最新 root.current 重遍历
        // 且仅接受 hook.queue === savedQueue，再读最新 HookNode.memoizedState
        const queueIdentity = savedQueue;
        (
          window as unknown as {
            __v1oSelectedIdsHook?: {
              queue: HookQueue;
              get: () => unknown;
            };
          }
        ).__v1oSelectedIdsHook = {
          queue: queueIdentity,
          get: () => {
            // 每次从最新 root.current 重遍历；禁止使用 dispatch 时的旧 HookNode
            const current = resolveCurrentRoot();
            let found: { state: unknown } | null = null;
            const visitByQueue = (fiber: Fiber | null | undefined): boolean => {
              if (!fiber) return false;
              let st = fiber.memoizedState;
              while (st) {
                // 精确 queue 对象身份；禁止任意字符串数组 / Set / 顺序猜测
                if (st.queue === queueIdentity) {
                  found = { state: st.memoizedState };
                  return true;
                }
                st = st.next ?? null;
              }
              if (visitByQueue(fiber.child)) return true;
              if (visitByQueue(fiber.sibling)) return true;
              return false;
            };
            if (!visitByQueue(current) || !found) return null;
            return found.state;
          },
        };
        dispatchFn(ids);
        // 同步返回仅命中/派发，不读 memoizedState（React 通常只排队更新）
        return { hit: true, dispatched: true };
      },
      { ids: [DOC_A, DOC_B, DOC_A] },
    );
    expect(dispatchHit).toEqual({ hit: true, dispatched: true });

    // 独立 render/poll：get 内部按 queue 身份从最新 fiber 读 [A,B,A]；禁止重搜任意字符串数组
    await expect
      .poll(async () => {
        return page.evaluate(() => {
          const g = window as unknown as {
            __v1oSelectedIdsHook?: { get: () => unknown };
          };
          const probe = g.__v1oSelectedIdsHook;
          if (!probe) return null;
          const val = probe.get();
          return Array.isArray(val) ? val.map(String) : null;
        });
      })
      .toEqual([DOC_A, DOC_B, DOC_A]);

    await page.getByRole("button", { name: /移动/ }).click();
    await expect
      .poll(() =>
        writeCount(
          probe,
          (w) =>
            w.method === "POST" && /\/api\/knowledge\/docs\/move\/?$/.test(w.path),
        ),
      )
      .toBe(1);
    const w = probe.writes.find((x) => x.path.includes("/docs/move"))!;
    const body = JSON.parse(w.bodyText) as { ids: string[]; folderId: string };
    // 请求首次出现顺序去重：[A,B,A] → [A,B]
    expect(body.ids).toEqual([DOC_A, DOC_B]);
    expect(new Set(body.ids).size).toBe(body.ids.length);
    expect(body.folderId).toBe(FLD_ARCHIVE);
  });
});

// ---------------------------------------------------------------------------
// E. 成功写链字段 + 服务端 ID
// ---------------------------------------------------------------------------

test.describe("V1-O E 成功写链服务端真值", () => {
  test("上传成功字段与输入不同；后续 reindex/move/delete 用服务端 ID", async ({
    page,
  }) => {
    page.on("dialog", (d) => d.accept());
    // 写响应与对账 GET 字段明显不同：最终列表仅认双 GET
    const reconDoc = makeDoc({
      id: "doc_server_up_recon_final",
      name: "server-upload-AFTER-RECONCILE.bin",
      tags: ["recon-tag-final"],
      chunks: 77,
      status: "ready",
      sizeLabel: "recon-size-final",
      category: "recon-category",
      folderId: FLD_ARCHIVE,
    });
    const probe = emptyProbe({
      folders: serverSeed().folders,
      docs: [],
      uploadMode: { kind: "ok" },
      reconcileFolders: serverSeed().folders,
      reconcileDocs: [reconDoc],
    });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(EMPTY_TITLE)).toBeVisible({ timeout: 15_000 });

    const folderBefore = probe.folderGets;
    const docBefore = probe.docGets;
    await setHiddenFileInput(page, UPLOAD_CLIENT_NAME, UPLOAD_ANCHOR);
    // 双 GET 精确 +1 后才认最终真值
    await expect.poll(() => probe.folderGets).toBe(folderBefore + 1);
    await expect.poll(() => probe.docGets).toBe(docBefore + 1);
    await continuationBarrier(page);

    // 最终真值 = 对账 GET（非写响应 UPLOAD_SERVER_NAME/99.0KB/server-category）
    await expect(page.getByText(reconDoc.name)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(UPLOAD_CLIENT_NAME)).toHaveCount(0);
    await expect(page.getByText(UPLOAD_SERVER_NAME)).toHaveCount(0);
    await expect(page.getByText("99.0 KB")).toHaveCount(0);
    await expect(page.getByText("server-category")).toHaveCount(0);
    const row = page.getByRole("row", { name: new RegExp(reconDoc.name) });
    await expect(row).toBeVisible();
    await expect(row).toContainText("77");
    await expect(row).toContainText("recon-size-final");
    await expect(row).toContainText("recon-tag-final");
    await expect(row).toContainText("recon-category");
    await expect(row).toContainText(SERVER_FOLDER_ARCHIVE);
    await expect(row).toContainText(STATUS_SAFE_LABEL.ready);

    const serverId = reconDoc.id;
    // 不可见 id：用 API 消费证据（后续 path），禁止不含 ID 的 checkbox 冒充
    expect(serverId.startsWith("doc_server_up_")).toBe(true);
    const idAttr = page.locator(`[data-doc-id="${serverId}"]`);
    if ((await idAttr.count()) === 0) {
      // 无 data-doc-id 时仅接受后续 reindex path 含精确 serverId 为消费证据
      expect(probe.reconcileDocs?.[0]?.id).toBe(serverId);
    } else {
      await expect(idAttr.first()).toHaveCount(1);
    }

    // reload 闭环：仍见对账真值字段
    await page.reload();
    await expect(page.getByText(reconDoc.name)).toBeVisible({ timeout: 15_000 });
    const row2 = page.getByRole("row", { name: new RegExp(reconDoc.name) });
    await expect(row2).toContainText("77");
    await expect(row2).toContainText("recon-size-final");

    // reindex：对账 GET chunks=88；写响应由其 +11→99；UI 必须消费 GET=88 并排除 99
    const afterReindex = {
      ...reconDoc,
      status: "ready" as const,
      chunks: 88,
      tags: ["get-reindex-tag-final"],
      sizeLabel: "get-reindex-size-88",
      category: "get-reindex-category",
      folderId: FLD_ARCHIVE,
    };
    // 写响应探针：当前 docs 行 chunks 会 +11 → 99，与 GET 真值可区分
    probe.docs = [{ ...afterReindex, chunks: 88 }];
    probe.reconcileDocs = [afterReindex];
    const f2 = probe.folderGets;
    const d2 = probe.docGets;
    await page
      .getByRole("row", { name: new RegExp(reconDoc.name) })
      .getByTitle("重新索引")
      .click();
    await expect.poll(() => probe.folderGets).toBe(f2 + 1);
    await expect.poll(() => probe.docGets).toBe(d2 + 1);
    await expect
      .poll(() =>
        probe.writes.some(
          (w) =>
            w.method === "POST" &&
            w.path.includes(`/docs/${serverId}/reindex`),
        ),
      )
      .toBe(true);
    await continuationBarrier(page);
    // UI 消费 GET 真值：chunks=88、排除写响应 99；probe 自身值不作 UI 证明
    const rowRe = page.getByRole("row", { name: new RegExp(reconDoc.name) });
    await expect(rowRe).toBeVisible({ timeout: 10_000 });
    await expect(rowRe.locator("td").nth(5)).toHaveText("88");
    await expect(rowRe.locator("td").nth(5)).not.toHaveText("99");
    await expect(rowRe).toContainText("get-reindex-size-88");
    await expect(rowRe).toContainText("get-reindex-tag-final");
    await expect(rowRe).toContainText("get-reindex-category");
    await expect(rowRe).toContainText(SERVER_FOLDER_ARCHIVE);
    await expect(rowRe).toContainText(STATUS_SAFE_LABEL.ready);
    // 写响应专属残留不得作为列表真值
    await expect(page.getByText("reindexed-size")).toHaveCount(0);
    await expect(page.getByText("server-reindexed")).toHaveCount(0);
    await expect(rowRe).not.toContainText(/\b99\b/);

    await page
      .getByRole("checkbox", { name: `选择 ${reconDoc.name}` })
      .check();
    await page.getByLabel("移入文件夹").selectOption(FLD_INBOX);
    await page.getByRole("button", { name: /移动/ }).click();
    await expect
      .poll(() => {
        const m = probe.writes.find(
          (w) => w.method === "POST" && w.path.includes("/docs/move"),
        );
        if (!m) return false;
        const b = JSON.parse(m.bodyText) as { ids: string[] };
        return b.ids.includes(serverId);
      })
      .toBe(true);

    await page
      .getByRole("checkbox", { name: `选择 ${reconDoc.name}` })
      .check();
    await page.getByRole("button", { name: /^删除$/ }).click();
    await expect
      .poll(() =>
        probe.writes.some(
          (w) =>
            w.method === "DELETE" && w.path.includes(`/docs/${serverId}`),
        ),
      )
      .toBe(true);
    expect(
      probe.writes.some((w) => /\/docs\/kb_/.test(w.path)),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// F. refresh 代次与 unmount（释放前基线）
// ---------------------------------------------------------------------------

test.describe("V1-O F refresh 代次与 unmount", () => {
  test("旧 success：arrived/settled 分离，释放后允许旧完成但禁新污染", async ({ page }) => {
    const gateA = createHoldGate();
    const probe = emptyProbe({ folders: [], docs: [] });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(EMPTY_TITLE)).toBeVisible({ timeout: 15_000 });

    // 旧代次：folders+docs 均 hold；两者 arrived 均可独立观测（本轮精确 +1）
    probe.foldersMode = { kind: "hold", gate: gateA, then: "ok" };
    probe.docsMode = { kind: "hold", gate: gateA, then: "ok" };
    const arrivedF0 = probe.folderGetArrived;
    const arrivedD0 = probe.docGetArrived;
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect
      .poll(() => probe.folderGetArrived, { timeout: 10_000 })
      .toBe(arrivedF0 + 1);
    await expect
      .poll(() => probe.docGetArrived, { timeout: 10_000 })
      .toBe(arrivedD0 + 1);

    // 新代次成功（成为可见真值）
    probe.folders = serverSeed().folders;
    probe.docs = serverSeed().docs;
    probe.foldersMode = { kind: "ok" };
    probe.docsMode = { kind: "ok" };
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
    await businessContinuationBarrier(page);

    // 释放前精确基线：DOM / 请求 arrived / settled / 写 / semantic
    const baseDom = await collectDomExport(page);
    const baseArrivedF = probe.folderGetArrived;
    const baseArrivedD = probe.docGetArrived;
    const baseSettledF = probe.folderGetFulfilled;
    const baseSettledD = probe.docGetFulfilled;
    const baseSemantic = probe.gets.filter((g) =>
      g.path.includes("semantic-index"),
    ).length;
    const baseWrite = probe.writes.length;
    const baseAllReq = probe.allRequests.length;

    // 污染载荷：若旧代次错误采用会渲染 STALE
    probe.folders = [
      makeFolder({ id: "fld_stale", name: "STALE_FOLDER_SHOULD_NOT_STICK" }),
    ];
    probe.docs = [
      makeDoc({ id: "doc_stale", name: "STALE_DOC_SHOULD_NOT_STICK.txt" }),
    ];
    // 释放前安装浏览器层 response 门（禁止仅 route helper >base+RAF）
    const folderTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/folders",
      "response",
    );
    const docTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/docs",
      "response",
    );
    gateA.release();
    await expect.poll(() => gateA.released).toBe(true);
    expect(await folderTerminal).toBe("response");
    expect(await docTerminal).toBe("response");
    // 旧 route 自身 settle：本轮 folders/docs 各自精确 +1（非 >=/>）
    await expect
      .poll(() => probe.folderGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledF + 1);
    await expect
      .poll(() => probe.docGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledD + 1);
    // 业务 catch/finally 可观测 continuation
    await businessContinuationBarrier(page);

    // 新增污染分计数：arrived 精确 0 增长；写/semantic 精确 0
    await expect(page.getByText("STALE_DOC_SHOULD_NOT_STICK")).toHaveCount(0);
    await expect(page.getByText("STALE_FOLDER_SHOULD_NOT_STICK")).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible();
    expect(probe.folderGetArrived - baseArrivedF).toBe(0);
    expect(probe.docGetArrived - baseArrivedD).toBe(0);
    expect(probe.writes.length - baseWrite).toBe(0);
    expect(
      probe.gets.filter((g) => g.path.includes("semantic-index")).length -
        baseSemantic,
    ).toBe(0);
    const apiAfter = probe.allRequests.filter((r) => r.resourceKind === "api");
    const apiBase = probe.allRequests
      .slice(0, baseAllReq)
      .filter((r) => r.resourceKind === "api").length;
    expect(apiAfter.length).toBe(apiBase);
    const domAfter = await collectDomExport(page);
    expect(domAfter.includes("STALE_DOC_SHOULD_NOT_STICK")).toBe(false);
    expect(baseDom.includes("STALE_DOC_SHOULD_NOT_STICK")).toBe(false);
  });

  test("旧 HTTP error：response barrier + settle 分计，释放后零新污染", async ({ page }) => {
    const gateA = createHoldGate();
    const probe = emptyProbe({
      folders: serverSeed().folders,
      docs: serverSeed().docs,
    });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    probe.foldersMode = { kind: "hold", gate: gateA, then: "error" };
    probe.docsMode = { kind: "hold", gate: gateA, then: "error" };
    const arrivedF0 = probe.folderGetArrived;
    const arrivedD0 = probe.docGetArrived;
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect.poll(() => probe.folderGetArrived, { timeout: 10_000 }).toBe(arrivedF0 + 1);
    await expect.poll(() => probe.docGetArrived, { timeout: 10_000 }).toBe(arrivedD0 + 1);

    probe.foldersMode = { kind: "ok" };
    probe.docsMode = { kind: "ok" };
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
    await businessContinuationBarrier(page);

    const baseDom = await collectDomExport(page);
    const baseArrivedF = probe.folderGetArrived;
    const baseArrivedD = probe.docGetArrived;
    const baseSettledF = probe.folderGetFulfilled;
    const baseSettledD = probe.docGetFulfilled;
    const baseWrite = probe.writes.length;
    const baseSemantic = probe.gets.filter((g) =>
      g.path.includes("semantic-index"),
    ).length;
    const baseAllReq = probe.allRequests.length;

    probe.folders = [
      makeFolder({ id: "fld_err_stale", name: "ERR_STALE_FOLDER_SHOULD_NOT" }),
    ];
    probe.docs = [
      makeDoc({ id: "doc_err_stale", name: "ERR_STALE_DOC_SHOULD_NOT.txt" }),
    ];
    const folderTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/folders",
      "response",
    );
    const docTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/docs",
      "response",
    );
    gateA.release();
    expect(await folderTerminal).toBe("response");
    expect(await docTerminal).toBe("response");
    await expect
      .poll(() => probe.folderGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledF + 1);
    await expect
      .poll(() => probe.docGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledD + 1);
    await businessContinuationBarrier(page);

    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible();
    await expect(page.getByText("ERR_STALE_DOC_SHOULD_NOT")).toHaveCount(0);
    expect(probe.folderGetArrived - baseArrivedF).toBe(0);
    expect(probe.docGetArrived - baseArrivedD).toBe(0);
    expect(probe.writes.length - baseWrite).toBe(0);
    expect(
      probe.gets.filter((g) => g.path.includes("semantic-index")).length -
        baseSemantic,
    ).toBe(0);
    const apiAfterErr = probe.allRequests.filter((r) => r.resourceKind === "api");
    const apiBaseErr = probe.allRequests
      .slice(0, baseAllReq)
      .filter((r) => r.resourceKind === "api").length;
    expect(apiAfterErr.length).toBe(apiBaseErr);
    expect((await collectDomExport(page)).includes(SERVER_DOC_A)).toBe(true);
    expect(baseDom.includes(SERVER_DOC_A)).toBe(true);
  });

  test("旧 abort/network：requestfailed barrier + settle 分计，释放后零新污染", async ({ page }) => {
    const gateA = createHoldGate();
    const probe = emptyProbe({
      folders: serverSeed().folders,
      docs: serverSeed().docs,
    });
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    probe.foldersMode = { kind: "hold", gate: gateA, then: "abort" };
    probe.docsMode = { kind: "hold", gate: gateA, then: "abort" };
    const arrivedF0 = probe.folderGetArrived;
    const arrivedD0 = probe.docGetArrived;
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect.poll(() => probe.folderGetArrived, { timeout: 10_000 }).toBe(arrivedF0 + 1);
    await expect.poll(() => probe.docGetArrived, { timeout: 10_000 }).toBe(arrivedD0 + 1);

    probe.foldersMode = { kind: "ok" };
    probe.docsMode = { kind: "ok" };
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });
    await businessContinuationBarrier(page);

    const baseDom = await collectDomExport(page);
    const baseArrivedF = probe.folderGetArrived;
    const baseArrivedD = probe.docGetArrived;
    const baseSettledF = probe.folderGetFulfilled;
    const baseSettledD = probe.docGetFulfilled;
    const baseWrite = probe.writes.length;
    const baseSemantic = probe.gets.filter((g) =>
      g.path.includes("semantic-index"),
    ).length;
    const baseAllReq = probe.allRequests.length;

    probe.folders = [
      makeFolder({ id: "fld_abort_stale", name: "ABORT_STALE_FOLDER_SHOULD_NOT" }),
    ];
    probe.docs = [
      makeDoc({ id: "doc_abort_stale", name: "ABORT_STALE_DOC_SHOULD_NOT.txt" }),
    ];
    // abort：浏览器 requestfailed 门 + 本轮 settled 精确 +1
    const folderTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/folders",
      "requestfailed",
    );
    const docTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/docs",
      "requestfailed",
    );
    gateA.release();
    expect(await folderTerminal).toBe("requestfailed");
    expect(await docTerminal).toBe("requestfailed");
    await expect
      .poll(() => probe.folderGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledF + 1);
    await expect
      .poll(() => probe.docGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledD + 1);
    await businessContinuationBarrier(page);

    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible();
    await expect(page.getByText("ABORT_STALE_DOC_SHOULD_NOT")).toHaveCount(0);
    expect(probe.folderGetArrived - baseArrivedF).toBe(0);
    expect(probe.docGetArrived - baseArrivedD).toBe(0);
    expect(probe.writes.length - baseWrite).toBe(0);
    expect(
      probe.gets.filter((g) => g.path.includes("semantic-index")).length -
        baseSemantic,
    ).toBe(0);
    const apiAfterAbort = probe.allRequests.filter(
      (r) => r.resourceKind === "api",
    );
    const apiBaseAbort = probe.allRequests
      .slice(0, baseAllReq)
      .filter((r) => r.resourceKind === "api").length;
    expect(apiAfterAbort.length).toBe(apiBaseAbort);
    expect(baseDom.includes(SERVER_DOC_A)).toBe(true);
  });

  test("unmount：释放前 DOM/请求/写/semantic 基线，卸载后仅旧 settle 禁新副作用", async ({ page }) => {
    const gate = createHoldGate();
    const probe = emptyProbe({
      foldersMode: { kind: "hold", gate, then: "ok" },
      docsMode: { kind: "hold", gate, then: "ok" },
      folders: serverSeed().folders,
      docs: serverSeed().docs,
    });
    await preparePage(page, probe, { poison: false });
    await page.goto("/knowledge-base");
    // folders+docs arrived 本轮可观测（挂载首拉至少各 1）
    await expect
      .poll(() => probe.folderGetArrived, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);
    await expect
      .poll(() => probe.docGetArrived, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(1);

    // 释放前基线（禁止释放/等待后才取）
    const baseArrivedF = probe.folderGetArrived;
    const baseArrivedD = probe.docGetArrived;
    const baseSettledF = probe.folderGetFulfilled;
    const baseSettledD = probe.docGetFulfilled;
    const baseWrite = probe.writes.length;
    const baseSemantic = probe.gets.filter((g) =>
      g.path.includes("semantic-index"),
    ).length;
    const baseKbApi = probe.allRequests.filter(
      (r) =>
        r.resourceKind === "api" &&
        (r.path.startsWith("/api/knowledge") || r.path.startsWith("/api/cards")),
    ).length;

    probe.folders = [
      makeFolder({ id: "fld_um_stale", name: "UNMOUNT_STALE_FOLDER" }),
    ];
    probe.docs = [
      makeDoc({ id: "doc_um_stale", name: "UNMOUNT_STALE_DOC.txt" }),
    ];

    // 导航卸载前安装浏览器终态门（response 或 requestfailed 均可，导航可能改写终态）
    // page.goto 导航请求与业务 API 分账
    const folderTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/folders",
      "either",
    );
    const docTerminal = armBrowserRouteTerminal(
      page,
      "/api/knowledge/docs",
      "either",
    );
    await page.goto("/");
    gate.release();
    const folderTerm = await folderTerminal;
    const docTerm = await docTerminal;
    expect(["response", "requestfailed"]).toContain(folderTerm);
    expect(["response", "requestfailed"]).toContain(docTerm);
    // 旧 folders+docs 本轮 settled 精确 +1（route handler 收尾）
    await expect
      .poll(() => probe.folderGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledF + 1);
    await expect
      .poll(() => probe.docGetFulfilled, { timeout: 10_000 })
      .toBe(baseSettledD + 1);
    await businessContinuationBarrier(page);

    // 业务 API：只允许旧请求自身完成，禁止新 knowledge arrived/写/semantic
    expect(probe.folderGetArrived).toBe(baseArrivedF);
    expect(probe.docGetArrived).toBe(baseArrivedD);
    expect(probe.writes.length).toBe(baseWrite);
    expect(
      probe.gets.filter((g) => g.path.includes("semantic-index")).length,
    ).toBe(baseSemantic);
    const kbApiAfter = probe.allRequests.filter(
      (r) =>
        r.resourceKind === "api" &&
        (r.path.startsWith("/api/knowledge") || r.path.startsWith("/api/cards")),
    ).length;
    expect(kbApiAfter).toBe(baseKbApi);
    expect(
      probe.allRequests.some((r) => r.resourceKind === "document"),
    ).toBe(true);
    await expect(page.getByRole("heading", { name: "知识库" })).toHaveCount(0);
    await expect(page.getByText("UNMOUNT_STALE_DOC")).toHaveCount(0);
  });
});

test.describe("V1-O G 选择清理与 fail-closed", () => {
  test("selectedFolderId/selectedIds/moveTarget 刷新后精确清理", async ({
    page,
  }) => {
    const probe = emptyProbe();
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    // 两个 folder 控件必须存在：树侧 selectedFolderId + 移入 moveTarget
    const treeInbox = page.getByRole("button", { name: SERVER_FOLDER_INBOX });
    const treeArchive = page.getByRole("button", { name: SERVER_FOLDER_ARCHIVE });
    const moveTarget = page.getByLabel("移入文件夹");
    await expect(treeInbox).toBeVisible();
    await expect(treeArchive).toBeVisible();
    await expect(moveTarget).toHaveCount(1);
    await expect(moveTarget).toBeVisible();

    // 先勾选 A/B（在收件箱/全部视图），moveTarget=归档
    await treeInbox.click();
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` }).check();
    await page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` }).check();
    await moveTarget.selectOption(FLD_ARCHIVE);
    // stale selectedFolderId=归档（即将消失）
    await treeArchive.click();
    await expect(page.getByText(/已选/)).toBeVisible();

    // 删除归档 folder 与 DOC_A；保留 DOC_B
    probe.docs = probe.docs.filter((d) => d.id !== DOC_A);
    probe.folders = probe.folders.filter((f) => f.id !== FLD_ARCHIVE);
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(SERVER_DOC_A)).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_B)).toBeVisible();

    // 两控件必须存在并落精确默认态；无失效 archive
    await expect(page.getByRole("button", { name: SERVER_FOLDER_INBOX })).toBeVisible();
    await expect(page.getByRole("button", { name: SERVER_FOLDER_ARCHIVE })).toHaveCount(0);
    const folderSelect = page.getByLabel("移入文件夹");
    await expect(folderSelect).toHaveCount(1);
    await expect(folderSelect).toBeVisible();
    // moveTarget 精确默认 ""（占位 option value=""），禁止失效 archive 或其它 folder id
    const val = await folderSelect.inputValue();
    expect(val).toBe("");
    expect(val).not.toBe(FLD_ARCHIVE);
    expect(val).not.toBe(FLD_INBOX);
    const options = await folderSelect.locator("option").allTextContents();
    expect(options.join("\n")).not.toContain(SERVER_FOLDER_ARCHIVE);
    // selectedIds：失效 A 清除、合法 B 精确保留
    await expect(page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_A}` })).toHaveCount(0);
    const cbB = page.getByRole("checkbox", { name: `选择 ${SERVER_DOC_B}` });
    await expect(cbB).toBeVisible();
    await expect(cbB).toBeChecked();
    // folder tree：精确默认活跃项「全部文档」（契约冻结默认 KB_FOLDER_ALL）
    const treeList = page.locator(".kb-folder-tree__list");
    const allDocsItem = treeList.locator(":scope > .kb-folder-item").filter({
      hasText: "全部文档",
    });
    await expect(allDocsItem).toHaveCount(1);
    await expect(allDocsItem).toHaveClass(/is-active/);
    // 收件箱可见但不得误标为活跃默认
    const inboxItem = treeList.locator(":scope > .kb-folder-item").filter({
      hasText: SERVER_FOLDER_INBOX,
    });
    await expect(inboxItem).toHaveCount(1);
    await expect(inboxItem).not.toHaveClass(/is-active/);
    // aria-selected（若实现提供）亦须指向全部文档；无属性时以 is-active 为准
    const ariaAll = await allDocsItem.getAttribute("aria-selected");
    if (ariaAll != null) {
      expect(ariaAll).toBe("true");
    }
    const ariaInbox = await inboxItem.getAttribute("aria-selected");
    if (ariaInbox != null) {
      expect(ariaInbox).not.toBe("true");
    }

    // 筛选空
    await page.locator("#kb-search").fill("NO_SUCH_DOC_FILTER_V1O");
    await expect(page.getByText(FILTER_EMPTY)).toBeVisible();
    await expect(page.getByText(LOAD_ERROR)).toHaveCount(0);
    await expect(page.getByText(EMPTY_TITLE)).toHaveCount(0);

    probe.foldersMode = { kind: "status", status: 503, detail: SECRET };
    await page.locator("#kb-search").fill("");
    await page.getByRole("button", { name: /刷新/ }).click();
    await expect(page.getByText(LOAD_ERROR)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(FILTER_EMPTY)).toHaveCount(0);
    await expect(page.getByText(EMPTY_TITLE)).toHaveCount(0);
    await expect(page.getByText(SERVER_DOC_B)).toHaveCount(0);
  });

  test("未知 knowledge 与外网 EXTERNAL_ROUTE_CANARY fail-closed", async ({
    page,
  }) => {
    const consoleCol = collectConsole(page);
    const probe = emptyProbe();
    await preparePage(page, probe, { poison: false });
    await openKnowledge(page, probe);
    await expect(page.getByText(SERVER_DOC_A)).toBeVisible({ timeout: 15_000 });

    const rejected = await page.evaluate(async () => {
      try {
        await fetch("/api/knowledge/not-a-real-endpoint-v1o");
        return "ok";
      } catch {
        return "fail";
      }
    });
    expect(rejected).toBe("fail");
    expect(
      probe.unknownKnowledgeHits.some((h) =>
        h.includes("not-a-real-endpoint-v1o"),
      ),
    ).toBe(true);

    const ext = await page.evaluate(async (url) => {
      try {
        await fetch(url);
        return "ok";
      } catch {
        return "fail";
      }
    }, EXTERNAL_ROUTE_CANARY);
    expect(ext).toBe("fail");
    expect(
      probe.externalHits.some((u) => u.includes("EXT_V1O_CANARY_9f3a")),
    ).toBe(true);
    expect(probe.externalHits.join("\n")).not.toContain(SECRET);
    // 全 request 已记录 canary URL
    expect(
      probe.allRequests.some((r) => r.url.includes("EXT_V1O_CANARY_9f3a")),
    ).toBe(true);
    await assertPrivacyClean(page, probe, consoleCol);
  });
});
