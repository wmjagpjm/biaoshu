/**
 * 模块：P12C-C3 / P12D-B / P12E-A / P12E-C / P12F-C / P12F-D 双工作区修订历史、对比、正文差异、游标分页与来源筛选前端 E2E
 * 用途：技术标/商务标证明默认折叠零请求、按需列表/摘要/对比/正文差异、双修订正文差异、
 *       二次确认 restore、游标页首屏与加载更多、来源筛选、执行时 expected、唯一 editor-state GET、
 *       失败阻断、迟到隔离与数据最小化。
 * 对接：Playwright chromium headless workers=1 retries=0；route 探针
 *       （含 comparison/body-diff/pair/page arrived/complete/cursor/sourceKind）。
 * 二次开发：禁止固定 sleep、.or(...)、>=1 冒充、宽泛状态码、route fallback 假成功。
 */
import {
  expect,
  test,
  type Page,
  type Route,
} from "@playwright/test";

const TECH_A = "proj_e2e_p12cc3_tech_a";
const TECH_B = "proj_e2e_p12cc3_tech_b";
const BIZ_A = "proj_e2e_p12cc3_biz_a";
const BIZ_B = "proj_e2e_p12cc3_biz_b";

const TECH_OVERVIEW = "P12C_C3_TECH_SERVER_OVERVIEW";
const TECH_OVERVIEW_B = "P12C_C3_TECH_SERVER_OVERVIEW_B";
const BIZ_MD = "P12C_C3_BIZ_SERVER_MARKDOWN";
const BIZ_MD_B = "P12C_C3_BIZ_SERVER_MARKDOWN_B";
const RESTORED_TECH = "P12C_C3_TECH_RESTORED_OVERVIEW";
const RESTORED_BIZ = "P12C_C3_BIZ_RESTORED_MARKDOWN";

const TECH_RESTORE_OUTLINE_TITLE = "P12C_C3_TECH_RESTORE_OUTLINE";
const TECH_RESTORE_CHAPTER_BODY = "P12C_C3_TECH_RESTORE_CHAPTER_BODY";
const TECH_RESTORE_FACT = "P12C_C3_TECH_RESTORE_FACT";
const TECH_RESTORE_GUIDANCE = "P12C_C3_TECH_RESTORE_GUIDANCE_FOCUS";
const TECH_RESTORE_MATRIX_TEXT = "P12C_C3_TECH_RESTORE_MATRIX_REQ";
const TECH_RESTORE_MATRIX_VERSION = "rmv_p12cc3_restored_matrix_v1";
const TECH_RESTORE_MODE = "FREE";

const BIZ_RESTORE_QUALIFY = "P12C_C3_BIZ_RESTORE_QUALIFY";
const BIZ_RESTORE_TOC = "P12C_C3_BIZ_RESTORE_TOC";
const BIZ_RESTORE_QUOTE = "P12C_C3_BIZ_RESTORE_QUOTE_ROW";
const BIZ_RESTORE_QUOTE_NOTES = "P12C_C3_BIZ_RESTORE_QUOTE_NOTES";
const BIZ_RESTORE_COMMIT = "P12C_C3_BIZ_RESTORE_COMMIT";

const SNAPSHOT_BODY_LEAK = "P12C_C3_SNAPSHOT_BODY_MUST_NOT_LEAK";

const MSG_LIST_FAIL = "修订历史加载失败，请稍后重试";
const MSG_DETAIL_FAIL = "修订摘要加载失败，请稍后重试";
const MSG_COMPARE_FAIL = "修订差异加载失败，请稍后重试";
const MSG_COMPARE_SAME = "与当前版本一致";
const MSG_COMPARE_DIFF = "与当前版本存在差异";
const MSG_BODY_DIFF_FAIL = "正文差异加载失败，请稍后重试";
const MSG_BODY_DIFF_SAME = "章节正文无变化";
const MSG_BODY_DIFF_TRUNCATED = "差异内容较长，仅显示有界片段";
const MSG_PAIR_BODY_DIFF_FAIL = "双修订差异加载失败，请稍后重试";
const MSG_PAIR_BODY_DIFF_SAME = "两条修订正文一致";
const MSG_PAIR_BODY_DIFF_TRUNCATED = "差异内容较长，仅显示有界片段";
/** P12F-C 加载更多固定失败文案 */
const MSG_LOAD_MORE_FAIL = "更多修订加载失败，请稍后重试";
/**
 * 探针侧第二页不透明游标（外壳合法：esrc1_ + base64url 无 =）。
 * 前端不得解码；仅原样回传。
 */
const PAGE_CURSOR_SECOND = "esrc1_cGFnZTJjdXJzb3Jmb3JyZXZoaXN0";
/**
 * P12F-D 筛选第二页不透明游标（外壳合法：esrc2_ + base64url 无 =）。
 * 前端不得解码；仅原样回传，且必须与当前 sourceKind 同时出现。
 */
const PAGE_CURSOR_FILTER_SECOND = "esrc2_ZmlsdGVycGFnZTJjdXJzb3Jmb3JyZXY";
/** 故意非法游标（缺前缀）——仅用于服务端/前端失败路径 */
const PAGE_CURSOR_BAD_SHAPE = "not_a_valid_cursor_value";
const MSG_RESTORE_OK = "已恢复到所选修订";
const MSG_RESTORE_BLOCKED =
  "当前无法恢复，请先处理版本冲突或重新载入";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
/** 检查点 create 被共享写令牌拒绝时的固定状态文案 */
const MSG_CHECKPOINT_CREATE_FAIL = "保存检查点失败，请确认后重试";
const RESTORE_CONFIRM =
  "服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。";

/** 权威 13 键固定顺序（与后端 CANONICAL_STATE_KEYS 对齐） */
const CANONICAL_FIELD_ORDER = [
  "outline",
  "chapters",
  "facts",
  "mode",
  "analysis",
  "responseMatrix",
  "guidance",
  "parsedMarkdown",
  "businessQualify",
  "businessToc",
  "businessQuote",
  "businessCommit",
  "analysisOverview",
] as const;

/** 13 键固定中文标签 */
const CANONICAL_FIELD_LABELS: Record<(typeof CANONICAL_FIELD_ORDER)[number], string> = {
  outline: "大纲",
  chapters: "章节",
  facts: "事实",
  mode: "编写模式",
  analysis: "分析",
  responseMatrix: "响应矩阵",
  guidance: "编写指导",
  parsedMarkdown: "解析正文",
  businessQualify: "商务资格",
  businessToc: "商务目录",
  businessQuote: "商务报价",
  businessCommit: "商务承诺",
  analysisOverview: "分析概览",
};

const FULL_STATE_CONFLICT_MSG =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

const SOURCE_LABELS: Record<string, string> = {
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

const NINE_SOURCES = Object.keys(SOURCE_LABELS);

const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

const TECH_DEBOUNCE_MS = 800;
const BIZ_DEBOUNCE_MS = 600;

type Mode = "tech" | "biz";

type RevisionMeta = {
  revisionId: string;
  stateVersion: string;
  snapshotBytes: number;
  sourceKind: string;
  createdAt: string;
};

type RevisionDetail = RevisionMeta & {
  snapshot: Record<string, unknown>;
};

type EditorState = {
  projectId: string;
  parsedMarkdown: string;
  businessQualify: unknown[];
  businessToc: unknown[];
  businessQuote: { rows: unknown[]; notes: string };
  businessCommit: unknown[];
  outline: Array<Record<string, unknown>>;
  chapters: Array<Record<string, unknown>>;
  mode: string;
  analysisOverview: string;
  analysis: { overview: string; techRequirements?: string[] };
  facts: unknown[];
  guidance: Record<string, unknown>;
  responseMatrix: unknown[];
  responseMatrixVersion: string | null;
  stateVersion: string;
};

type HoldGate = {
  wait: () => Promise<void>;
  release: () => void;
  readonly released: boolean;
  readonly enteredCount: number;
  waitUntilEntered: (min?: number) => Promise<void>;
};

type RestoreMode =
  | { kind: "ok" }
  | { kind: "abort" }
  | { kind: "http_error"; status: number }
  | { kind: "not_found" }
  | { kind: "missing_version" }
  | { kind: "invalid_version" }
  | { kind: "blank_version" }
  | { kind: "extra_field" }
  | { kind: "conflict" }
  | {
      kind: "gate";
      gate: HoldGate;
      then: Exclude<RestoreMode["kind"], "gate">;
    };

type PutMode =
  | { kind: "ok" }
  | { kind: "abort" }
  | { kind: "http_error" }
  | {
      kind: "hold";
      gate: HoldGate;
      then?: "ok" | "conflict";
    };

type ListMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
/** P12F-C 游标页模式：正常 / 挂起 / HTTP 错误 */
type PageMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate }
  | { kind: "http_error"; status: number };
type DetailMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type ComparisonMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type BodyDiffMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type PairBodyDiffMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };

/** page 探针到达记录：含 cursor/sourceKind 与查询键，供精确零旁路断言 */
type PageProbeHit = {
  projectId: string;
  cursor: string | null;
  /** 缺省 null 表示无 sourceKind query */
  sourceKind: string | null;
  method: string;
  path: string;
  postData: string | null;
  queryKeys: string[];
  search: string;
};

type ComparisonSummary = {
  outlineNodeCount: number;
  chapterCount: number;
  factCount: number;
  responseMatrixRowCount: number;
  businessEntryTotal: number;
  hasParsedMarkdown: boolean;
};

type ComparisonPayload = {
  sameState: boolean;
  changedFields: string[];
  currentSummary: ComparisonSummary;
  targetSummary: ComparisonSummary;
};

type BodyDiffHunk = { op: "equal" | "delete" | "insert"; text: string };
type BodyDiffItem = {
  ordinal: number;
  kind: "added" | "removed" | "changed";
  beforeTitle: string;
  afterTitle: string;
  hunks: BodyDiffHunk[];
};
type BodyDiffPayload = {
  sameBody: boolean;
  changedChapterCount: number;
  currentChapterCount: number;
  targetChapterCount: number;
  truncated: boolean;
  items: BodyDiffItem[];
};

/** P12E-B/C 双修订正文差异响应：before/after 章节计数 */
type PairBodyDiffPayload = {
  sameBody: boolean;
  changedChapterCount: number;
  beforeChapterCount: number;
  afterChapterCount: number;
  truncated: boolean;
  items: BodyDiffItem[];
};

type ProbeState = {
  mode: Mode;
  projects: Record<string, EditorState>;
  revisions: Record<string, RevisionMeta[]>;
  details: Record<string, RevisionDetail>;
  versionSeq: number;
  revisionSeq: number;
  checkpointSeq: number;
  putLog: Array<{
    projectId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  restoreLog: Array<{
    projectId: string;
    revisionId: string;
    body: Record<string, unknown>;
    responseVersion: string | null;
  }>;
  listLog: string[];
  /** list 响应已 fulfill（await json 返回后）——用于证明迟到响应真正完成 */
  listCompleteLog: string[];
  /** P12F-C/D page 到达（gate 前），含 cursor/sourceKind */
  pageLog: PageProbeHit[];
  /** P12F-C/D page 响应已 fulfill（await json 返回后） */
  pageCompleteLog: Array<{
    projectId: string;
    cursor: string | null;
    sourceKind: string | null;
  }>;
  /** 按 cursor 键固定第二页游标；筛选场景可用 esrc2 */
  pageSecondCursorBySource: Record<string, string>;
  detailLog: Array<{ projectId: string; revisionId: string }>;
  /** detail 响应已 fulfill（await json 返回后）——用于证明迟到响应真正完成 */
  detailCompleteLog: Array<{ projectId: string; revisionId: string }>;
  /** comparison 到达（gate 前） */
  comparisonLog: Array<{
    projectId: string;
    revisionId: string;
    method: string;
    path: string;
    postData: string | null;
    hasQuery: boolean;
  }>;
  /** comparison 响应已 fulfill（await json 返回后） */
  comparisonCompleteLog: Array<{ projectId: string; revisionId: string }>;
  /** body-diff 到达（gate 前） */
  bodyDiffLog: Array<{
    projectId: string;
    revisionId: string;
    method: string;
    path: string;
    postData: string | null;
    hasQuery: boolean;
  }>;
  /** body-diff 响应已 fulfill（await json 返回后） */
  bodyDiffCompleteLog: Array<{ projectId: string; revisionId: string }>;
  /** pair body-diff 到达（gate 前） */
  pairBodyDiffLog: Array<{
    projectId: string;
    beforeRevisionId: string;
    afterRevisionId: string;
    method: string;
    path: string;
    postData: string | null;
    hasQuery: boolean;
  }>;
  /** pair body-diff 响应已 fulfill（await json 返回后） */
  pairBodyDiffCompleteLog: Array<{
    projectId: string;
    beforeRevisionId: string;
    afterRevisionId: string;
  }>;
  editorGetLog: Array<{ projectId: string; path: string }>;
  putMode: PutMode;
  restoreMode: RestoreMode;
  listMode: ListMode;
  pageMode: PageMode;
  detailMode: DetailMode;
  comparisonMode: ComparisonMode;
  bodyDiffMode: BodyDiffMode;
  pairBodyDiffMode: PairBodyDiffMode;
  restoreModeByProject: Record<string, RestoreMode>;
  listModeByProject: Record<string, ListMode>;
  pageModeByProject: Record<string, PageMode>;
  /** 按 cursor 固定 page hold/错误（第二页） */
  pageModeByCursor: Record<string, PageMode>;
  detailModeByProject: Record<string, DetailMode>;
  detailModeByRevisionId: Record<string, DetailMode>;
  comparisonModeByProject: Record<string, ComparisonMode>;
  comparisonModeByRevisionId: Record<string, ComparisonMode>;
  bodyDiffModeByProject: Record<string, BodyDiffMode>;
  bodyDiffModeByRevisionId: Record<string, BodyDiffMode>;
  pairBodyDiffModeByProject: Record<string, PairBodyDiffMode>;
  /** 按 before::after 固定 pair hold */
  pairBodyDiffModeByPairKey: Record<string, PairBodyDiffMode>;
  listResponseOverride: unknown | null;
  pageResponseOverride: unknown | null;
  /** 按 cursor 键固定 page 响应（null cursor 用 ""） */
  pageResponseByCursor: Record<string, unknown>;
  /** 自定义第二页游标；默认 PAGE_CURSOR_SECOND */
  pageSecondCursor: string;
  detailResponseOverride: unknown | null;
  restoreResponseOverride: unknown | null;
  comparisonResponseOverride: unknown | null;
  /** 按 revisionId 固定 comparison 响应表 */
  comparisonResponseByRevisionId: Record<string, unknown>;
  bodyDiffResponseOverride: unknown | null;
  bodyDiffResponseByRevisionId: Record<string, unknown>;
  pairBodyDiffResponseOverride: unknown | null;
  /** 按 before::after 固定 pair 响应表 */
  pairBodyDiffResponseByPairKey: Record<string, unknown>;
  nextEditorGetFail: boolean;
  restoreArrivedWhilePutHeld: boolean;
  externalHits: string[];
  forbiddenHits: string[];
  checkpointListLog: string[];
  /** 检查点 create POST 探针（互斥/令牌验证） */
  checkpointCreateLog: string[];
};

function seedStateVersion(n: number): string {
  return `esv_${n.toString(16).padStart(32, "0")}`;
}

function seedRevisionId(n: number): string {
  return `esr_${n.toString(16).padStart(32, "0")}`;
}

function seedCheckpointId(n: number): string {
  return `escp_${n.toString(16).padStart(32, "0")}`;
}

function allocateVersion(state: ProbeState): string {
  state.versionSeq += 1;
  return seedStateVersion(state.versionSeq);
}

function allocateRevisionId(state: ProbeState): string {
  state.revisionSeq += 1;
  return seedRevisionId(state.revisionSeq);
}

function allocateCheckpointId(state: ProbeState): string {
  state.checkpointSeq += 1;
  return seedCheckpointId(state.checkpointSeq);
}

function createHoldGate(): HoldGate {
  let released = false;
  let enteredCount = 0;
  const waiters: Array<() => void> = [];
  const enteredWaiters: Array<() => void> = [];
  const notifyEntered = () => {
    while (enteredWaiters.length > 0) {
      enteredWaiters.shift()?.();
    }
  };
  return {
    wait: () => {
      enteredCount += 1;
      notifyEntered();
      return released
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            waiters.push(resolve);
          });
    },
    release: () => {
      released = true;
      while (waiters.length > 0) {
        waiters.shift()?.();
      }
    },
    get released() {
      return released;
    },
    get enteredCount() {
      return enteredCount;
    },
    waitUntilEntered: (min = 1) =>
      new Promise<void>((resolve) => {
        const tryResolve = () => {
          if (enteredCount >= min) resolve();
        };
        enteredWaiters.push(tryResolve);
        tryResolve();
      }),
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function baseEditor(
  projectId: string,
  mode: Mode,
  version: string,
  content: string,
): EditorState {
  return {
    projectId,
    parsedMarkdown: mode === "biz" ? content : "",
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    outline:
      mode === "tech"
        ? [{ id: "n1", title: "一级目录", children: [], targetWords: 100 }]
        : [],
    chapters:
      mode === "tech"
        ? [{ id: "n1", title: "一级目录", body: "章节正文" }]
        : [],
    mode: "ALIGNED",
    analysisOverview: mode === "tech" ? content : "",
    analysis: { overview: mode === "tech" ? content : "" },
    facts: [],
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
      kbEnabled: true,
      kbFolderIds: [],
    },
    responseMatrix: [],
    responseMatrixVersion: null,
    stateVersion: version,
  };
}

function canonicalSnapshot(editor: EditorState): Record<string, unknown> {
  return {
    outline: editor.outline,
    chapters: editor.chapters,
    facts: editor.facts,
    mode: editor.mode,
    analysis: editor.analysis,
    responseMatrix: editor.responseMatrix,
    guidance: editor.guidance,
    parsedMarkdown: editor.parsedMarkdown,
    businessQualify: editor.businessQualify,
    businessToc: editor.businessToc,
    businessQuote: editor.businessQuote,
    businessCommit: editor.businessCommit,
    analysisOverview: editor.analysisOverview,
  };
}

function createProbeState(mode: Mode): ProbeState {
  const versionSeq = 10;
  const aId = mode === "tech" ? TECH_A : BIZ_A;
  const bId = mode === "tech" ? TECH_B : BIZ_B;
  const aContent = mode === "tech" ? TECH_OVERVIEW : BIZ_MD;
  const bContent = mode === "tech" ? TECH_OVERVIEW_B : BIZ_MD_B;
  return {
    mode,
    projects: {
      [aId]: baseEditor(aId, mode, seedStateVersion(versionSeq), aContent),
      [bId]: baseEditor(bId, mode, seedStateVersion(versionSeq + 1), bContent),
    },
    revisions: { [aId]: [], [bId]: [] },
    details: {},
    versionSeq: versionSeq + 1,
    revisionSeq: 0,
    checkpointSeq: 0,
    putLog: [],
    restoreLog: [],
    listLog: [],
    listCompleteLog: [],
    pageLog: [],
    pageCompleteLog: [],
    detailLog: [],
    detailCompleteLog: [],
    comparisonLog: [],
    comparisonCompleteLog: [],
    bodyDiffLog: [],
    bodyDiffCompleteLog: [],
    pairBodyDiffLog: [],
    pairBodyDiffCompleteLog: [],
    editorGetLog: [],
    putMode: { kind: "ok" },
    restoreMode: { kind: "ok" },
    listMode: { kind: "ok" },
    pageMode: { kind: "ok" },
    detailMode: { kind: "ok" },
    comparisonMode: { kind: "ok" },
    bodyDiffMode: { kind: "ok" },
    pairBodyDiffMode: { kind: "ok" },
    restoreModeByProject: {},
    listModeByProject: {},
    pageModeByProject: {},
    pageModeByCursor: {},
    detailModeByProject: {},
    detailModeByRevisionId: {},
    comparisonModeByProject: {},
    comparisonModeByRevisionId: {},
    bodyDiffModeByProject: {},
    bodyDiffModeByRevisionId: {},
    pairBodyDiffModeByProject: {},
    pairBodyDiffModeByPairKey: {},
    listResponseOverride: null,
    pageResponseOverride: null,
    pageResponseByCursor: {},
    pageSecondCursor: PAGE_CURSOR_SECOND,
    pageSecondCursorBySource: {},
    detailResponseOverride: null,
    restoreResponseOverride: null,
    comparisonResponseOverride: null,
    comparisonResponseByRevisionId: {},
    bodyDiffResponseOverride: null,
    bodyDiffResponseByRevisionId: {},
    pairBodyDiffResponseOverride: null,
    pairBodyDiffResponseByPairKey: {},
    nextEditorGetFail: false,
    restoreArrivedWhilePutHeld: false,
    externalHits: [],
    forbiddenHits: [],
    checkpointListLog: [],
    checkpointCreateLog: [],
  };
}

function resolveRestoreMode(state: ProbeState, projectId: string): RestoreMode {
  return state.restoreModeByProject[projectId] ?? state.restoreMode;
}

function resolveListMode(state: ProbeState, projectId: string): ListMode {
  return state.listModeByProject[projectId] ?? state.listMode;
}

/** 用途：解析 page 模式；带 cursor 时优先 pageModeByCursor */
function resolvePageMode(
  state: ProbeState,
  projectId: string,
  cursor: string | null,
): PageMode {
  if (cursor && state.pageModeByCursor[cursor]) {
    return state.pageModeByCursor[cursor];
  }
  return state.pageModeByProject[projectId] ?? state.pageMode;
}

/** 用途：统计某项目 page 到达次数 */
function pageHitCount(state: ProbeState, projectId: string): number {
  return state.pageLog.filter((h) => h.projectId === projectId).length;
}

/** 用途：统计某项目 page 完成次数 */
function pageCompleteCount(state: ProbeState, projectId: string): number {
  return state.pageCompleteLog.filter((h) => h.projectId === projectId).length;
}

/** 用途：统计带指定 cursor（null=首屏）的 page 到达次数 */
function pageHitCountForCursor(
  state: ProbeState,
  projectId: string,
  cursor: string | null,
): number {
  return state.pageLog.filter(
    (h) => h.projectId === projectId && h.cursor === cursor,
  ).length;
}

/** 用途：统计精确 sourceKind（null=无筛选 query）的 page 到达次数 */
function pageHitCountForSource(
  state: ProbeState,
  projectId: string,
  sourceKind: string | null,
): number {
  return state.pageLog.filter(
    (h) => h.projectId === projectId && h.sourceKind === sourceKind,
  ).length;
}

/** 用途：统计精确 sourceKind + cursor 组合的 page 到达次数 */
function pageHitCountForSourceCursor(
  state: ProbeState,
  projectId: string,
  sourceKind: string | null,
  cursor: string | null,
): number {
  return state.pageLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.sourceKind === sourceKind &&
      h.cursor === cursor,
  ).length;
}

/** 用途：统计 page 完成次数（可按 sourceKind/cursor 精确过滤） */
function pageCompleteCountForSourceCursor(
  state: ProbeState,
  projectId: string,
  sourceKind: string | null,
  cursor: string | null,
): number {
  return state.pageCompleteLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.sourceKind === sourceKind &&
      h.cursor === cursor,
  ).length;
}

/**
 * 用途：构建默认游标页响应；可选 sourceKind 服务端过滤；
 *   无筛选 nextCursor=esrc1 探针；有筛选 nextCursor=esrc2 探针。
 */
function buildDefaultPagePayload(
  state: ProbeState,
  projectId: string,
  cursor: string | null,
  sourceKind: string | null = null,
): { items: RevisionMeta[]; nextCursor: string | null } | { error: "cursor" } {
  const allRaw = state.revisions[projectId] || [];
  const all =
    sourceKind == null
      ? allRaw
      : allRaw.filter((it) => it.sourceKind === sourceKind);
  const secondCursor =
    sourceKind == null
      ? state.pageSecondCursor
      : (state.pageSecondCursorBySource[sourceKind] ??
        PAGE_CURSOR_FILTER_SECOND);
  if (cursor == null) {
    const items = all.slice(0, 10);
    const nextCursor = all.length > 10 ? secondCursor : null;
    return { items, nextCursor };
  }
  if (cursor === secondCursor) {
    const items = all.slice(10, 20);
    return { items, nextCursor: null };
  }
  // 兼容无筛选默认第二页游标
  if (sourceKind == null && cursor === state.pageSecondCursor) {
    const items = all.slice(10, 20);
    return { items, nextCursor: null };
  }
  return { error: "cursor" };
}

function resolveDetailMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): DetailMode {
  return (
    state.detailModeByRevisionId[revisionId] ??
    state.detailModeByProject[projectId] ??
    state.detailMode
  );
}

function resolveComparisonMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): ComparisonMode {
  return (
    state.comparisonModeByRevisionId[revisionId] ??
    state.comparisonModeByProject[projectId] ??
    state.comparisonMode
  );
}

function resolveBodyDiffMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): BodyDiffMode {
  return (
    state.bodyDiffModeByRevisionId[revisionId] ??
    state.bodyDiffModeByProject[projectId] ??
    state.bodyDiffMode
  );
}

/** 用途：pair 键 before::after，供 hold/响应表定位 */
function pairBodyDiffKey(beforeRevisionId: string, afterRevisionId: string): string {
  return `${beforeRevisionId}::${afterRevisionId}`;
}

function resolvePairBodyDiffMode(
  state: ProbeState,
  projectId: string,
  beforeRevisionId: string,
  afterRevisionId: string,
): PairBodyDiffMode {
  const key = pairBodyDiffKey(beforeRevisionId, afterRevisionId);
  return (
    state.pairBodyDiffModeByPairKey[key] ??
    state.pairBodyDiffModeByProject[projectId] ??
    state.pairBodyDiffMode
  );
}

/** 大纲节点有界计数（探针侧，与生产摘要语义一致） */
function probeCountOutlineNodes(nodes: unknown, depth = 0): number {
  if (depth > 32 || !Array.isArray(nodes)) return 0;
  let total = 0;
  for (const node of nodes) {
    total += 1;
    if (node && typeof node === "object") {
      total += probeCountOutlineNodes(
        (node as { children?: unknown }).children,
        depth + 1,
      );
    }
  }
  return total;
}

function probeSummarizeSnapshot(
  snap: Record<string, unknown>,
): ComparisonSummary {
  const qualify = Array.isArray(snap.businessQualify)
    ? snap.businessQualify.length
    : 0;
  const toc = Array.isArray(snap.businessToc) ? snap.businessToc.length : 0;
  const commit = Array.isArray(snap.businessCommit)
    ? snap.businessCommit.length
    : 0;
  let quoteRows = 0;
  const bq = snap.businessQuote;
  if (bq && typeof bq === "object") {
    const rows = (bq as { rows?: unknown }).rows;
    if (Array.isArray(rows)) quoteRows = rows.length;
  }
  const parsed = snap.parsedMarkdown;
  return {
    outlineNodeCount: probeCountOutlineNodes(snap.outline),
    chapterCount: Array.isArray(snap.chapters) ? snap.chapters.length : 0,
    factCount: Array.isArray(snap.facts) ? snap.facts.length : 0,
    responseMatrixRowCount: Array.isArray(snap.responseMatrix)
      ? snap.responseMatrix.length
      : 0,
    businessEntryTotal: qualify + toc + quoteRows + commit,
    hasParsedMarkdown:
      typeof parsed === "string" ? parsed.trim().length > 0 : false,
  };
}

/** 构造权威 comparison 响应：逐字段 JSON 比较 + 两侧六项摘要 */
function buildComparisonPayload(
  current: EditorState,
  targetSnap: Record<string, unknown>,
): ComparisonPayload {
  const currentSnap = canonicalSnapshot(current);
  const changedFields: string[] = [];
  for (const key of CANONICAL_FIELD_ORDER) {
    if (JSON.stringify(currentSnap[key]) !== JSON.stringify(targetSnap[key])) {
      changedFields.push(key);
    }
  }
  return {
    sameState: changedFields.length === 0,
    changedFields,
    currentSummary: probeSummarizeSnapshot(currentSnap),
    targetSummary: probeSummarizeSnapshot(targetSnap),
  };
}

type ProbeChapter = { id?: string; title?: string; body?: string };

/**
 * 用途：探针侧构造权威 body-diff 六键响应（简化 id 配对 + 行级 delete/insert）。
 * 二次开发：仅 E2E 探针；生产完整算法在后端服务。
 */
function buildBodyDiffPayload(
  current: EditorState,
  targetSnap: Record<string, unknown>,
): BodyDiffPayload {
  const currentChapters = Array.isArray(current.chapters)
    ? (current.chapters as ProbeChapter[])
    : [];
  const targetChapters = Array.isArray(targetSnap.chapters)
    ? (targetSnap.chapters as ProbeChapter[])
    : [];
  const curMap = new Map<string, ProbeChapter>();
  const tgtMap = new Map<string, ProbeChapter>();
  for (const ch of currentChapters) {
    if (typeof ch?.id === "string" && ch.id !== "") curMap.set(ch.id, ch);
  }
  for (const ch of targetChapters) {
    if (typeof ch?.id === "string" && ch.id !== "") tgtMap.set(ch.id, ch);
  }
  const items: BodyDiffItem[] = [];
  let ordinal = 1;
  for (const [id, cur] of curMap) {
    const tgt = tgtMap.get(id);
    const afterBody = typeof cur.body === "string" ? cur.body : "";
    const afterTitle = typeof cur.title === "string" ? cur.title : "";
    if (!tgt) {
      items.push({
        ordinal: ordinal++,
        kind: "added",
        beforeTitle: "",
        afterTitle,
        hunks: afterBody ? [{ op: "insert", text: afterBody }] : [],
      });
      continue;
    }
    const beforeBody = typeof tgt.body === "string" ? tgt.body : "";
    const beforeTitle = typeof tgt.title === "string" ? tgt.title : "";
    if (beforeBody === afterBody) continue;
    const hunks: BodyDiffHunk[] = [];
    if (beforeBody) hunks.push({ op: "delete", text: beforeBody });
    if (afterBody) hunks.push({ op: "insert", text: afterBody });
    items.push({
      ordinal: ordinal++,
      kind: "changed",
      beforeTitle,
      afterTitle,
      hunks,
    });
  }
  for (const [id, tgt] of tgtMap) {
    if (curMap.has(id)) continue;
    const beforeBody = typeof tgt.body === "string" ? tgt.body : "";
    const beforeTitle = typeof tgt.title === "string" ? tgt.title : "";
    items.push({
      ordinal: ordinal++,
      kind: "removed",
      beforeTitle,
      afterTitle: "",
      hunks: beforeBody ? [{ op: "delete", text: beforeBody }] : [],
    });
  }
  return {
    sameBody: items.length === 0,
    changedChapterCount: items.length,
    currentChapterCount: currentChapters.length,
    targetChapterCount: targetChapters.length,
    truncated: false,
    items,
  };
}

/**
 * 用途：探针侧构造 P12E-B 双修订 body-diff 六键响应（before/after 章节计数）。
 * 二次开发：仅 E2E 探针；生产完整算法在后端服务。
 */
function buildPairBodyDiffPayload(
  beforeSnap: Record<string, unknown>,
  afterSnap: Record<string, unknown>,
): PairBodyDiffPayload {
  // 复用单修订探针算法：current=after，target=before，再改写章节计数字段名
  const afterAsEditor: EditorState = {
    projectId: "probe_pair",
    parsedMarkdown: "",
    businessQualify: [],
    businessToc: [],
    businessQuote: { rows: [], notes: "" },
    businessCommit: [],
    outline: [],
    chapters: Array.isArray(afterSnap.chapters)
      ? (afterSnap.chapters as Array<Record<string, unknown>>)
      : [],
    mode: "FREE",
    analysisOverview: "",
    analysis: { overview: "" },
    facts: [],
    guidance: {},
    responseMatrix: [],
    responseMatrixVersion: null,
    stateVersion: seedStateVersion(1),
  };
  const single = buildBodyDiffPayload(afterAsEditor, beforeSnap);
  return {
    sameBody: single.sameBody,
    changedChapterCount: single.changedChapterCount,
    beforeChapterCount: Array.isArray(beforeSnap.chapters)
      ? beforeSnap.chapters.length
      : 0,
    afterChapterCount: Array.isArray(afterSnap.chapters)
      ? afterSnap.chapters.length
      : 0,
    truncated: single.truncated,
    items: single.items,
  };
}

/** 构造超过 MAX_COUNT_DEPTH 的大纲树，触发固定摘要失败 */
function makeOverDepthOutline(depth: number): unknown {
  let node: Record<string, unknown> = {
    id: "leaf",
    title: "deep-leaf",
    children: [],
  };
  for (let i = 0; i < depth; i++) {
    node = { id: `d${i}`, title: "deep", children: [node] };
  }
  return [node];
}

function techRestoredEditor(
  prev: EditorState,
  restoredVersion: string,
): EditorState {
  const matrixSourceKey = `requirement:${TECH_RESTORE_MATRIX_TEXT.trim().replace(/\s+/g, " ").toLocaleLowerCase()}`;
  return {
    ...prev,
    analysisOverview: RESTORED_TECH,
    analysis: {
      overview: RESTORED_TECH,
      techRequirements: [TECH_RESTORE_MATRIX_TEXT],
    },
    outline: [
      {
        id: "rest_n1",
        title: TECH_RESTORE_OUTLINE_TITLE,
        level: 1,
        children: [],
        targetWords: 200,
      },
    ],
    chapters: [
      {
        id: "rest_n1",
        title: TECH_RESTORE_OUTLINE_TITLE,
        body: TECH_RESTORE_CHAPTER_BODY,
        wordCount: 12,
        status: "done",
        preview: TECH_RESTORE_CHAPTER_BODY.slice(0, 40),
      },
    ],
    facts: [
      {
        id: "fact_rest_1",
        category: "tender",
        content: TECH_RESTORE_FACT,
        source: "tender",
      },
    ],
    guidance: {
      ...prev.guidance,
      chapterFocus: TECH_RESTORE_GUIDANCE,
      targetWordCount: 90000,
    },
    mode: TECH_RESTORE_MODE,
    responseMatrix: [
      {
        id: "rm_rest_1",
        kind: "requirement",
        sourceKey: matrixSourceKey,
        sourceIndex: 0,
        sourceText: TECH_RESTORE_MATRIX_TEXT,
        weight: "10",
        chapterIds: ["rest_n1"],
        outlineNodeIds: ["rest_n1"],
        status: "covered",
        notes: "restore-sentinel",
      },
    ],
    responseMatrixVersion: TECH_RESTORE_MATRIX_VERSION,
    stateVersion: restoredVersion,
  };
}

function bizRestoredEditor(
  prev: EditorState,
  restoredVersion: string,
): EditorState {
  return {
    ...prev,
    parsedMarkdown: RESTORED_BIZ,
    businessQualify: [
      {
        id: "q_rest_1",
        requirement: BIZ_RESTORE_QUALIFY,
        response: "restore-response",
        evidence: "restore-evidence",
        status: "matched",
      },
    ],
    businessToc: [
      {
        id: "toc_rest_1",
        title: BIZ_RESTORE_TOC,
        category: "附件",
        status: "required",
        checked: true,
      },
    ],
    businessQuote: {
      rows: [
        {
          id: "qr_rest_1",
          name: BIZ_RESTORE_QUOTE,
          unit: "项",
          quantity: "1",
          unitPrice: "100",
          amount: "100",
          remark: "restore",
        },
      ],
      notes: BIZ_RESTORE_QUOTE_NOTES,
    },
    businessCommit: [
      {
        id: "cb_rest_1",
        title: BIZ_RESTORE_COMMIT,
        body: "restore-commit-body",
        needsStamp: true,
      },
    ],
    stateVersion: restoredVersion,
  };
}

function isLegacyFontUrl(url: string): boolean {
  return (
    url.includes("fonts.googleapis.com") || url.includes("fonts.gstatic.com")
  );
}

function projectMeta(id: string, mode: Mode, name: string) {
  return {
    id,
    workspaceId: "ws_e2e",
    name,
    industry: "能源",
    status: "draft",
    updatedAt: "2026-07-16T00:00:00.000Z",
    technicalPlanStep: 1,
    wordCount: 0,
    kind: mode === "tech" ? "technical" : "business",
  };
}

async function installRuntimeErrorGuards(page: Page) {
  const pageErrors: string[] = [];
  const consoleLogs: string[] = [];
  page.on("pageerror", (err) => {
    pageErrors.push(String(err?.message || err));
  });
  page.on("console", (msg) => {
    consoleLogs.push(msg.text());
  });
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (ev) => {
      const reason = (ev as PromiseRejectionEvent).reason;
      const text =
        reason instanceof Error
          ? reason.message
          : typeof reason === "string"
            ? reason
            : String(reason);
      const g = window as unknown as { __p12cc3Unhandled?: string[] };
      g.__p12cc3Unhandled = g.__p12cc3Unhandled || [];
      g.__p12cc3Unhandled.push(text);
    });
  });
  return {
    pageErrors,
    consoleLogs,
    async readUnhandled(): Promise<string[]> {
      return page.evaluate(() => {
        return (
          (window as unknown as { __p12cc3Unhandled?: string[] })
            .__p12cc3Unhandled || []
        );
      });
    },
  };
}

function applyPutBodyToEditor(
  prev: EditorState,
  body: Record<string, unknown>,
  nextVersion: string,
): EditorState {
  const next: EditorState = {
    ...prev,
    stateVersion: nextVersion,
  };
  if (typeof body.parsedMarkdown === "string") {
    next.parsedMarkdown = body.parsedMarkdown;
  }
  if (typeof body.analysisOverview === "string") {
    next.analysisOverview = body.analysisOverview;
    next.analysis = { overview: body.analysisOverview };
  }
  if (body.analysis && typeof body.analysis === "object") {
    const overview = (body.analysis as { overview?: string }).overview;
    if (typeof overview === "string") {
      next.analysisOverview = overview;
      next.analysis = { overview };
    }
  }
  if (Array.isArray(body.outline)) {
    next.outline = body.outline as Array<Record<string, unknown>>;
  }
  if (Array.isArray(body.chapters)) {
    next.chapters = body.chapters as Array<Record<string, unknown>>;
  }
  return next;
}

async function installRoutes(page: Page, state: ProbeState) {
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const host = url.hostname;
    const path = url.pathname;
    const method = req.method().toUpperCase();

    if (isLegacyFontUrl(url.href)) {
      await route.fulfill({ status: 200, contentType: "text/css", body: "" });
      return;
    }

    if (host !== "127.0.0.1" && host !== "localhost") {
      state.externalHits.push(`${method} ${url.href}`);
      await route.abort("failed");
      return;
    }

    if (!path.startsWith("/api")) {
      await route.continue();
      return;
    }

    const aId = state.mode === "tech" ? TECH_A : BIZ_A;
    const bId = state.mode === "tech" ? TECH_B : BIZ_B;
    const known = new Set([aId, bId]);

    if (path === "/api/health" && method === "GET") {
      await json(route, { status: "ok", service: "biaoshu" });
      return;
    }
    if (path === "/api/auth/bootstrap-status" && method === "GET") {
      await json(route, { mode: "disabled", bootstrapped: true });
      return;
    }
    if (path === "/api/auth/me" && method === "GET") {
      await json(route, { id: "user_e2e", name: "e2e" });
      return;
    }
    if (path === "/api/auth/csrf" && method === "GET") {
      await json(route, { csrfToken: "e2e-csrf" });
      return;
    }
    if (path === "/api/workspace" && method === "GET") {
      await json(route, { id: "ws_e2e", name: "E2E" });
      return;
    }
    if (path === "/api/settings" && method === "GET") {
      await json(route, {});
      return;
    }
    if (path === "/api/projects" && method === "GET") {
      await json(route, [
        projectMeta(aId, state.mode, "C3-A"),
        projectMeta(bId, state.mode, "C3-B"),
      ]);
      return;
    }

    const projectMatch = path.match(/^\/api\/projects\/([^/]+)\/?$/);
    if (projectMatch && method === "GET") {
      const pid = projectMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      await json(
        route,
        projectMeta(pid, state.mode, pid === aId ? "C3-A" : "C3-B"),
      );
      return;
    }

    const editorMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state\/?$/,
    );
    if (editorMatch) {
      const pid = editorMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method === "GET") {
        state.editorGetLog.push({ projectId: pid, path });
        if (state.nextEditorGetFail) {
          state.nextEditorGetFail = false;
          await json(route, { detail: "editor_get_fail" }, 500);
          return;
        }
        await json(route, state.projects[pid]);
        return;
      }
      if (method === "PUT") {
        const raw = req.postData() || "{}";
        const body = JSON.parse(raw) as Record<string, unknown>;
        if (state.putMode.kind === "abort") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await route.abort("failed");
          return;
        }
        if (state.putMode.kind === "http_error") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await json(route, { detail: "server_error" }, 500);
          return;
        }
        if (state.putMode.kind === "hold") {
          state.putLog.push({ projectId: pid, body, responseVersion: null });
          await state.putMode.gate.wait();
          if (state.putMode.then === "conflict") {
            await json(
              route,
              {
                detail: {
                  code: "editor_state_version_conflict",
                  message: "blocked",
                  currentStateVersion: state.projects[pid].stateVersion,
                },
              },
              409,
            );
            return;
          }
          const nextVersion = allocateVersion(state);
          const next = applyPutBodyToEditor(
            state.projects[pid],
            body,
            nextVersion,
          );
          state.projects[pid] = next;
          const last = state.putLog[state.putLog.length - 1];
          if (last) last.responseVersion = nextVersion;
          await json(route, next);
          return;
        }
        const nextVersion = allocateVersion(state);
        const next = applyPutBodyToEditor(
          state.projects[pid],
          body,
          nextVersion,
        );
        state.projects[pid] = next;
        state.putLog.push({
          projectId: pid,
          body,
          responseVersion: nextVersion,
        });
        await json(route, next);
        return;
      }
    }

    // 检查点面板仍存在：允许 list，记录但不实现 create/restore 业务
    const cpListMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-checkpoints\/?$/,
    );
    if (cpListMatch) {
      const pid = cpListMatch[1];
      if (method === "GET") {
        state.checkpointListLog.push(pid);
        await json(route, { items: [] });
        return;
      }
      if (method === "POST") {
        state.checkpointCreateLog.push(pid);
        await json(route, {
          checkpointId: allocateCheckpointId(state),
          stateVersion: state.projects[pid]?.stateVersion || seedStateVersion(1),
          snapshotBytes: 10,
          outlineNodeCount: 0,
          chapterCount: 0,
          createdAt: new Date().toISOString(),
        }, 201);
        return;
      }
    }

    // P12F-C/D 游标页：必须在旧 list 与 detail 之前匹配，避免 path 段 "page" 被当 revisionId
    const revPageMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/page\/?$/,
    );
    if (revPageMatch) {
      const pid = revPageMatch[1];
      const queryKeys = [...url.searchParams.keys()];
      const rawCursor = url.searchParams.get("cursor");
      const cursor =
        rawCursor === null || rawCursor === "" ? null : rawCursor;
      // sourceKind：缺省 null；空串也按字面记录（前端不得发送空串）
      const rawSource = url.searchParams.get("sourceKind");
      const sourceKind = rawSource === null ? null : rawSource;
      const postData = req.postData();
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method !== "GET") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "method_not_allowed" }, 405);
        return;
      }
      // arrived：gate 前记录，含 cursor/sourceKind 与查询键
      state.pageLog.push({
        projectId: pid,
        cursor,
        sourceKind,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
      });
      const pageMode = resolvePageMode(state, pid, cursor);
      if (pageMode.kind === "hold") {
        await pageMode.gate.wait();
      }
      if (pageMode.kind === "http_error") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_page_error",
              message: "page error",
            },
          },
          pageMode.status,
        );
        state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
        return;
      }
      if (state.pageResponseOverride != null) {
        await json(route, state.pageResponseOverride);
        state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
        return;
      }
      const cursorKey = cursor ?? "";
      if (state.pageResponseByCursor[cursorKey] != null) {
        await json(route, state.pageResponseByCursor[cursorKey]);
        state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
        return;
      }
      // 非法 sourceKind 字面量（探针侧）：仅用于失败路径；合法九类或 null 继续
      if (
        sourceKind !== null &&
        !NINE_SOURCES.includes(sourceKind)
      ) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_source_invalid",
              message: "修订来源筛选无效",
            },
          },
          400,
        );
        state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
        return;
      }
      const built = buildDefaultPagePayload(state, pid, cursor, sourceKind);
      if ("error" in built) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_cursor_invalid",
              message: "修订分页游标无效",
            },
          },
          400,
        );
        state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
        return;
      }
      await json(route, {
        items: built.items,
        nextCursor: built.nextCursor,
      });
      state.pageCompleteLog.push({ projectId: pid, cursor, sourceKind });
      return;
    }

    const revListMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/?$/,
    );
    if (revListMatch) {
      const pid = revListMatch[1];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method === "GET") {
        state.listLog.push(pid);
        const listMode = resolveListMode(state, pid);
        if (listMode.kind === "hold") {
          await listMode.gate.wait();
        }
        if (state.listResponseOverride != null) {
          await json(route, state.listResponseOverride);
          state.listCompleteLog.push(pid);
          return;
        }
        // 旧列表仍固定最近 10 条，不含游标（P12C 合同）
        await json(route, {
          items: (state.revisions[pid] || []).slice(0, 10),
        });
        state.listCompleteLog.push(pid);
        return;
      }
      state.forbiddenHits.push(`${method} ${path}`);
      await json(route, { detail: "method_not_allowed" }, 405);
      return;
    }

    // comparison 必须在通用 detail 之前匹配：GET .../revisions/{id}/comparison
    const revComparisonMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/comparison\/?$/,
    );
    if (revComparisonMatch) {
      const pid = revComparisonMatch[1];
      const revisionId = revComparisonMatch[2];
      const hasQuery = url.search.length > 1;
      const postData = req.postData();
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method !== "GET") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "method_not_allowed" }, 405);
        return;
      }
      // 拒绝 body / 查询参数改变结果：记录后仍按固定响应处理
      state.comparisonLog.push({
        projectId: pid,
        revisionId,
        method,
        path,
        postData,
        hasQuery,
      });
      const comparisonMode = resolveComparisonMode(state, pid, revisionId);
      if (comparisonMode.kind === "hold") {
        await comparisonMode.gate.wait();
      }
      if (state.comparisonResponseOverride != null) {
        await json(route, state.comparisonResponseOverride);
        state.comparisonCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      if (state.comparisonResponseByRevisionId[revisionId] != null) {
        await json(route, state.comparisonResponseByRevisionId[revisionId]);
        state.comparisonCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const detail = state.details[revisionId];
      if (!detail) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        state.comparisonCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const payload = buildComparisonPayload(
        state.projects[pid],
        detail.snapshot,
      );
      await json(route, payload);
      state.comparisonCompleteLog.push({ projectId: pid, revisionId });
      return;
    }

    // pair body-diff 必须在单修订 body-diff 之前匹配：
    // GET .../revisions/{before}/body-diff/{after}
    const revPairBodyDiffMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/body-diff\/([^/]+)\/?$/,
    );
    if (revPairBodyDiffMatch) {
      const pid = revPairBodyDiffMatch[1];
      const beforeRevisionId = revPairBodyDiffMatch[2];
      const afterRevisionId = revPairBodyDiffMatch[3];
      const hasQuery = url.search.length > 1;
      const postData = req.postData();
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method !== "GET") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "method_not_allowed" }, 405);
        return;
      }
      // arrived：gate 前记录，证明请求已到达
      state.pairBodyDiffLog.push({
        projectId: pid,
        beforeRevisionId,
        afterRevisionId,
        method,
        path,
        postData,
        hasQuery,
      });
      const pairMode = resolvePairBodyDiffMode(
        state,
        pid,
        beforeRevisionId,
        afterRevisionId,
      );
      if (pairMode.kind === "hold") {
        await pairMode.gate.wait();
      }
      const pairKey = pairBodyDiffKey(beforeRevisionId, afterRevisionId);
      if (state.pairBodyDiffResponseOverride != null) {
        await json(route, state.pairBodyDiffResponseOverride);
        state.pairBodyDiffCompleteLog.push({
          projectId: pid,
          beforeRevisionId,
          afterRevisionId,
        });
        return;
      }
      if (state.pairBodyDiffResponseByPairKey[pairKey] != null) {
        await json(route, state.pairBodyDiffResponseByPairKey[pairKey]);
        state.pairBodyDiffCompleteLog.push({
          projectId: pid,
          beforeRevisionId,
          afterRevisionId,
        });
        return;
      }
      const beforeDetail = state.details[beforeRevisionId];
      const afterDetail = state.details[afterRevisionId];
      if (!beforeDetail || !afterDetail) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        state.pairBodyDiffCompleteLog.push({
          projectId: pid,
          beforeRevisionId,
          afterRevisionId,
        });
        return;
      }
      const payload = buildPairBodyDiffPayload(
        beforeDetail.snapshot,
        afterDetail.snapshot,
      );
      await json(route, payload);
      // complete：await json 之后，证明响应已真正 fulfill
      state.pairBodyDiffCompleteLog.push({
        projectId: pid,
        beforeRevisionId,
        afterRevisionId,
      });
      return;
    }

    // body-diff 必须在通用 detail 之前匹配：GET .../revisions/{id}/body-diff
    const revBodyDiffMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/body-diff\/?$/,
    );
    if (revBodyDiffMatch) {
      const pid = revBodyDiffMatch[1];
      const revisionId = revBodyDiffMatch[2];
      const hasQuery = url.search.length > 1;
      const postData = req.postData();
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method !== "GET") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "method_not_allowed" }, 405);
        return;
      }
      // arrived：gate 前记录，证明请求已到达
      state.bodyDiffLog.push({
        projectId: pid,
        revisionId,
        method,
        path,
        postData,
        hasQuery,
      });
      const bodyDiffMode = resolveBodyDiffMode(state, pid, revisionId);
      if (bodyDiffMode.kind === "hold") {
        await bodyDiffMode.gate.wait();
      }
      if (state.bodyDiffResponseOverride != null) {
        await json(route, state.bodyDiffResponseOverride);
        state.bodyDiffCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      if (state.bodyDiffResponseByRevisionId[revisionId] != null) {
        await json(route, state.bodyDiffResponseByRevisionId[revisionId]);
        state.bodyDiffCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const detail = state.details[revisionId];
      if (!detail) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        state.bodyDiffCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const payload = buildBodyDiffPayload(
        state.projects[pid],
        detail.snapshot,
      );
      await json(route, payload);
      // complete：await json 之后，证明响应已真正 fulfill
      state.bodyDiffCompleteLog.push({ projectId: pid, revisionId });
      return;
    }

    const revRestoreMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/restore\/?$/,
    );
    if (revRestoreMatch && method === "POST") {
      const pid = revRestoreMatch[1];
      const revisionId = revRestoreMatch[2];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      const raw = req.postData() || "{}";
      const body = JSON.parse(raw) as Record<string, unknown>;
      if (state.putMode.kind === "hold" && !state.putMode.gate.released) {
        state.restoreArrivedWhilePutHeld = true;
      }

      let mode = resolveRestoreMode(state, pid);
      if (mode.kind === "gate") {
        await mode.gate.wait();
        mode = { kind: mode.then } as RestoreMode;
      }

      if (mode.kind === "abort") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await route.abort("failed");
        return;
      }
      if (mode.kind === "http_error") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(route, { detail: { code: "server_error", message: "x" } }, mode.status);
        return;
      }
      if (mode.kind === "not_found") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        return;
      }
      if (mode.kind === "conflict") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(
          route,
          {
            detail: {
              code: "editor_state_version_conflict",
              message: "conflict",
              currentStateVersion: state.projects[pid].stateVersion,
            },
          },
          409,
        );
        return;
      }

      const safetyId = allocateCheckpointId(state);
      if (mode.kind === "missing_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: null,
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "invalid_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: "not-a-version",
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: "not-a-version",
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "blank_version") {
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: "  ",
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: "  ",
          restoredAt: new Date().toISOString(),
        });
        return;
      }
      if (mode.kind === "extra_field") {
        const restoredVersion = allocateVersion(state);
        state.restoreLog.push({
          projectId: pid,
          revisionId,
          body,
          responseVersion: restoredVersion,
        });
        await json(route, {
          safetyCheckpointId: safetyId,
          stateVersion: restoredVersion,
          restoredAt: new Date().toISOString(),
          leak: "EXTRA_RESTORE_FIELD",
        });
        return;
      }

      const targetMeta = (state.revisions[pid] || []).find(
        (r) => r.revisionId === revisionId,
      );
      const restoredVersion =
        targetMeta?.stateVersion || allocateVersion(state);
      const prev = state.projects[pid];
      state.projects[pid] =
        state.mode === "tech"
          ? techRestoredEditor(prev, restoredVersion)
          : bizRestoredEditor(prev, restoredVersion);

      // 恢复成功后在列表头部追加 revision_restore 时间点；保留上限 20（P12F-A）
      const newMeta: RevisionMeta = {
        revisionId: allocateRevisionId(state),
        stateVersion: restoredVersion,
        snapshotBytes: 256,
        sourceKind: "revision_restore",
        createdAt: new Date().toISOString(),
      };
      state.revisions[pid] = [newMeta, ...(state.revisions[pid] || [])].slice(
        0,
        20,
      );

      state.restoreLog.push({
        projectId: pid,
        revisionId,
        body,
        responseVersion: restoredVersion,
      });
      if (state.restoreResponseOverride != null) {
        await json(route, state.restoreResponseOverride);
        return;
      }
      await json(route, {
        safetyCheckpointId: safetyId,
        stateVersion: restoredVersion,
        restoredAt: new Date().toISOString(),
      });
      return;
    }

    const revDetailMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/?$/,
    );
    if (revDetailMatch && method === "GET") {
      const pid = revDetailMatch[1];
      const revisionId = revDetailMatch[2];
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      state.detailLog.push({ projectId: pid, revisionId });
      const detailMode = resolveDetailMode(state, pid, revisionId);
      if (detailMode.kind === "hold") {
        await detailMode.gate.wait();
      }
      if (state.detailResponseOverride != null) {
        await json(route, state.detailResponseOverride);
        state.detailCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      const detail = state.details[revisionId];
      if (!detail) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_not_found",
              message: "修订不存在",
            },
          },
          404,
        );
        state.detailCompleteLog.push({ projectId: pid, revisionId });
        return;
      }
      await json(route, detail);
      state.detailCompleteLog.push({ projectId: pid, revisionId });
      return;
    }

    // 技术标壳层知识侧栏：仅精确 GET /api/knowledge/folders 良性；禁止 includes/写方法成功体
    if (method === "GET" && path === "/api/knowledge/folders") {
      await json(route, []);
      return;
    }

    if (
      path.includes("/tasks") ||
      path.includes("/files") ||
      path.includes("/templates") ||
      path.includes("/cards")
    ) {
      await json(route, method === "GET" ? [] : { ok: true });
      return;
    }

    state.forbiddenHits.push(`${method} ${path}`);
    await json(route, { detail: "forbidden" }, 404);
  });
}

async function openWorkspace(page: Page, mode: Mode, projectId: string) {
  const path =
    mode === "tech"
      ? `/technical-plan/${projectId}/analysis`
      : `/business-bid/${projectId}`;
  await page.goto(path);
  if (mode === "tech") {
    await expect(
      page.getByTestId("technical-editor-workspace"),
    ).toBeVisible();
    await expect(
      page.getByTestId("technical-analysis-overview"),
    ).toBeVisible();
  } else {
    await expect(
      page.getByTestId("business-editor-workspace"),
    ).toBeVisible();
    await expect(page.getByLabel("商务条款解析 Markdown")).toBeVisible();
  }
  await expect(page.getByTestId("editor-state-checkpoint-panel")).toBeVisible();
  await expect(page.getByTestId("editor-state-revision-panel")).toBeVisible();
}

async function editContent(page: Page, mode: Mode, text: string) {
  if (mode === "tech") {
    await page.getByTestId("technical-analysis-overview").fill(text);
  } else {
    await page.getByLabel("商务条款解析 Markdown").fill(text);
  }
}

async function readContent(page: Page, mode: Mode): Promise<string> {
  if (mode === "tech") {
    return page.getByTestId("technical-analysis-overview").inputValue();
  }
  return page.getByLabel("商务条款解析 Markdown").inputValue();
}

async function expandRevisionPanel(page: Page) {
  const toggle = page.getByTestId("editor-state-revision-toggle");
  const body = page.getByTestId("editor-state-revision-body");
  if (await body.count()) {
    return;
  }
  await toggle.click();
  await expect(body).toBeVisible();
}

function conflictTestId(mode: Mode) {
  return mode === "tech"
    ? "technical-editor-state-conflict"
    : "business-editor-state-conflict";
}

function reloadTestId(mode: Mode) {
  return mode === "tech"
    ? "technical-editor-state-reload"
    : "business-editor-state-reload";
}

async function assertNoIdLeak(
  page: Page,
  state: ProbeState,
  consoleLogs: string[] = [],
) {
  const html = await page.content();
  for (const list of Object.values(state.revisions)) {
    for (const item of list) {
      expect(html).not.toContain(item.revisionId);
      expect(html).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(html).not.toContain(editor.stateVersion);
  }
  expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
  // 游标不得进入 DOM
  expect(html).not.toContain(PAGE_CURSOR_SECOND);
  expect(html).not.toContain("esrc1_");
  const storage = await page.evaluate(() => {
    const ls: string[] = [];
    const ss: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k) ls.push(`${k}=${localStorage.getItem(k)}`);
    }
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k) ss.push(`${k}=${sessionStorage.getItem(k)}`);
    }
    // Cookie 纳入零泄漏证据（与 local/sessionStorage 同检）
    return { ls, ss, href: location.href, cookie: document.cookie };
  });
  const blob = JSON.stringify(storage);
  expect(blob).not.toMatch(/esr_[0-9a-f]{32}/);
  expect(blob).not.toMatch(/esv_[0-9a-f]{32}/);
  expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
  expect(blob).not.toContain(PAGE_CURSOR_SECOND);
  expect(blob).not.toContain("esrc1_");
  // Cookie 字符串同样禁止 revision ID / stateVersion / 游标 / 正文探针
  const cookieBlob = storage.cookie ?? "";
  for (const list of Object.values(state.revisions)) {
    for (const item of list) {
      expect(cookieBlob).not.toContain(item.revisionId);
      expect(cookieBlob).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(cookieBlob).not.toContain(editor.stateVersion);
  }
  expect(cookieBlob).not.toMatch(/esr_[0-9a-f]{32}/);
  expect(cookieBlob).not.toMatch(/esv_[0-9a-f]{32}/);
  expect(cookieBlob).not.toContain(SNAPSHOT_BODY_LEAK);
  expect(cookieBlob).not.toContain(PAGE_CURSOR_SECOND);
  expect(cookieBlob).not.toContain("esrc1_");

  // console 不得出现 revisionId / stateVersion / snapshot 正文探针 / 游标
  const consoleBlob = consoleLogs.join("\n");
  for (const list of Object.values(state.revisions)) {
    for (const item of list) {
      expect(consoleBlob).not.toContain(item.revisionId);
      expect(consoleBlob).not.toContain(item.stateVersion);
    }
  }
  for (const editor of Object.values(state.projects)) {
    expect(consoleBlob).not.toContain(editor.stateVersion);
  }
  expect(consoleBlob).not.toMatch(/esr_[0-9a-f]{32}/);
  expect(consoleBlob).not.toMatch(/esv_[0-9a-f]{32}/);
  expect(consoleBlob).not.toContain(SNAPSHOT_BODY_LEAK);
  expect(consoleBlob).not.toMatch(/\bsnapshot\b/i);
  expect(consoleBlob).not.toContain(PAGE_CURSOR_SECOND);
  expect(consoleBlob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
  expect(consoleBlob).not.toContain("esrc1_");
  expect(consoleBlob).not.toContain("esrc2_");
}

function debounceMs(mode: Mode) {
  return mode === "tech" ? TECH_DEBOUNCE_MS : BIZ_DEBOUNCE_MS;
}

function seedRevisions(
  state: ProbeState,
  projectId: string,
  count = 1,
  sources?: string[],
): RevisionMeta[] {
  const list: RevisionMeta[] = [];
  for (let i = 0; i < count; i++) {
    const n = state.revisionSeq + 1 + i;
    const sourceKind =
      sources?.[i] || NINE_SOURCES[i % NINE_SOURCES.length] || "browser_put";
    const meta: RevisionMeta = {
      revisionId: seedRevisionId(n),
      stateVersion: seedStateVersion(20 + n),
      snapshotBytes: 100 + i,
      sourceKind,
      createdAt: new Date(Date.UTC(2026, 6, 16, 10, i, 0)).toISOString(),
    };
    const editor = state.projects[projectId];
    const snap = canonicalSnapshot(editor);
    // 故意塞入正文泄漏探针：前端不得渲染
    if (typeof snap.parsedMarkdown === "string") {
      snap.parsedMarkdown = `${snap.parsedMarkdown}\n${SNAPSHOT_BODY_LEAK}`;
    } else {
      snap.parsedMarkdown = SNAPSHOT_BODY_LEAK;
    }
    if (typeof snap.analysisOverview === "string") {
      snap.analysisOverview = `${snap.analysisOverview}\n${SNAPSHOT_BODY_LEAK}`;
    }
    state.details[meta.revisionId] = { ...meta, snapshot: snap };
    list.push(meta);
  }
  state.revisionSeq += count;
  state.revisions[projectId] = list;
  return list;
}

async function assertNoRestoreWhilePutHeld(
  page: Page,
  mode: Mode,
  state: ProbeState,
) {
  await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
  await page.clock.fastForward(debounceMs(mode) + 100);
  await page.clock.fastForward(debounceMs(mode) + 100);
  expect(state.restoreLog.length).toBe(0);
  expect(state.restoreArrivedWhilePutHeld).toBe(false);
}

// ---------------------------------------------------------------------------
// 技术标
// ---------------------------------------------------------------------------
test.describe("P12C-C3 技术标修订历史", () => {
  test.describe.configure({ mode: "serial" });

  test("默认折叠零 revision 请求；展开一次 list；刷新精确 +1；九来源标签；最多 10 条", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 10, NINE_SOURCES.concat(["browser_put"]));
    // 第 11 项不在服务端列表（探针只返回 10）
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    // 默认折叠：零 revision list/detail/restore
    expect(state.listLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);

    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
      const label = SOURCE_LABELS[state.revisions[TECH_A][i].sourceKind];
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText(label);
      // 不得展示内部 source 原值
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).not.toHaveText(state.revisions[TECH_A][i].sourceKind);
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.listLog.length).toBe(0);

    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("详情按需加载六项摘要；不展示正文；折叠/切项清空；严格 shape 固定失败", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A) + pageHitCount(state, BIZ_A) + pageHitCount(state, TECH_B) + pageHitCount(state, BIZ_B), { timeout: 10_000 })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();
    const summaryText = await page
      .getByTestId("editor-state-revision-summary-body-0")
      .innerText();
    expect(summaryText).toMatch(/大纲/);
    expect(summaryText).toMatch(/章节/);
    expect(summaryText).toMatch(/事实/);
    expect(summaryText).toMatch(/矩阵/);
    expect(summaryText).toMatch(/商务/);
    expect(summaryText).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(summaryText).not.toContain(state.revisions[TECH_A][0].revisionId);

    // 同一时刻只展开一项：点第二项后第一项摘要消失
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    // 折叠清空
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A) + pageHitCount(state, BIZ_A) + pageHitCount(state, TECH_B) + pageHitCount(state, BIZ_B), { timeout: 10_000 })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    const baseMeta = state.revisions[TECH_A][0];
    const baseDetail = state.details[baseMeta.revisionId];

    // 元数据错配：详情 revisionId 与列表不一致（重展后无选中，直接加载）
    state.detailResponseOverride = {
      ...baseDetail,
      revisionId: seedRevisionId(999),
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    let html = await page.content();
    expect(html).not.toContain(seedRevisionId(999));
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    // 额外顶层键（再次点击清空 → 再点加载）
    state.detailResponseOverride = {
      ...baseDetail,
      leakExtra: "DETAIL_EXTRA_KEY",
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(4);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain("DETAIL_EXTRA_KEY");
    expect(html).not.toContain(baseMeta.revisionId);

    // 缺 13 键之一
    const missingSnap = { ...baseDetail.snapshot };
    delete missingSnap.outline;
    state.detailResponseOverride = {
      ...baseDetail,
      snapshot: missingSnap,
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(5);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain(baseMeta.revisionId);
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);

    // 非法快照 + 超过深度 → 固定脱敏失败
    state.detailResponseOverride = {
      ...baseDetail,
      snapshot: {
        ...baseDetail.snapshot,
        outline: makeOverDepthOutline(40),
      },
    };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(6);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toContainText(MSG_DETAIL_FAIL);
    html = await page.content();
    expect(html).not.toContain(baseMeta.revisionId);
    expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(html).not.toContain("deep-leaf");
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("确认前 POST=0；PUT 挂起时 restore=0；释放后 expected=PUT 响应版本", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 1);
    const targetRevisionId = seeded[0].revisionId;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageHitCount(state, TECH_A) + pageHitCount(state, BIZ_A), { timeout: 10_000 }).toBe(1);
    expect(state.listLog.length).toBe(0);

    const hold = createHoldGate();
    state.putMode = { kind: "hold", gate: hold, then: "ok" };

    await editContent(page, "tech", `${TECH_OVERVIEW}\n挂起后再恢复`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect.poll(() => state.putLog.length, { timeout: 10_000 }).toBe(1);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toContainText(RESTORE_CONFIRM);
    expect(state.restoreLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await assertNoRestoreWhilePutHeld(page, "tech", state);

    hold.release();
    state.putMode = { kind: "ok" };

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);

    const putVersion = state.putLog[0].responseVersion;
    expect(putVersion).toMatch(STATE_VERSION_RE);
    // 必须与确认前内存目标一致；禁止与恢复后新追加的列表头比较
    expect(state.restoreLog[0].revisionId).toBe(targetRevisionId);
    expect(state.restoreLog[0].body).toEqual({
      expectedStateVersion: putVersion,
    });
    expect(Object.keys(state.restoreLog[0].body).sort()).toEqual([
      "expectedStateVersion",
    ]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });

  test("成功恢复：POST=1、editor-state GET=1、list 额外 1；完整水合；两窗口零 PUT；下一编辑 +1 PUT", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageHitCount(state, TECH_A) + pageHitCount(state, BIZ_A), { timeout: 10_000 }).toBe(1);
    expect(state.listLog.length).toBe(0);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;
    const pageBefore = pageHitCount(state, TECH_A);

    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog[0].revisionId).toBe(seeded[0].revisionId);

    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === TECH_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);

    await expect
      .poll(() => pageHitCount(state, TECH_A) - pageBefore, {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toContainText(MSG_RESTORE_OK);

    const putsAfterRestore = state.putLog.length;
    await page.clock.fastForward(debounceMs("tech") + 100);
    await page.clock.fastForward(debounceMs("tech") + 100);
    expect(state.putLog.length).toBe(putsAfterRestore);

    await editContent(page, "tech", `${RESTORED_TECH}\n用户下一编辑`);
    await page.clock.fastForward(debounceMs("tech") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfterRestore + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.analysisOverview).toBe(`${RESTORED_TECH}\n用户下一编辑`);
    expect(JSON.stringify(putBody.outline)).toContain(
      TECH_RESTORE_OUTLINE_TITLE,
    );
    expect(JSON.stringify(putBody.chapters)).toContain(
      TECH_RESTORE_CHAPTER_BODY,
    );
    expect(JSON.stringify(putBody.facts)).toContain(TECH_RESTORE_FACT);
    expect(JSON.stringify(putBody.guidance)).toContain(TECH_RESTORE_GUIDANCE);
    expect(putBody.mode).toBe(TECH_RESTORE_MODE);
    expect(JSON.stringify(putBody.responseMatrix)).toContain(
      TECH_RESTORE_MATRIX_TEXT,
    );
    expect(putBody.responseMatrixVersion).toBe(TECH_RESTORE_MATRIX_VERSION);
    await assertNoIdLeak(page, state, []);
  });

  test("双击确认 restore POST 精确 1 次", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    const confirm = page.getByTestId(
      "editor-state-revision-confirm-restore-0",
    );
    await Promise.all([confirm.click(), confirm.click()]);
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
  });

  test("检查点与修订恢复互斥：同令牌只产生一个版本化写", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    const restoreGate = createHoldGate();
    state.restoreMode = { kind: "gate", gate: restoreGate, then: "ok" };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    // 展开修订并启动 restore（挂起）
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGate.waitUntilEntered(1);
    expect(state.restoreLog.length).toBe(0);

    const putsWhileHeld = state.putLog.length;
    const cpCreateBefore = state.checkpointCreateLog.length;

    // 真实点击检查点 create（无 force）；gate 仍挂起时必须先看到失败状态，再断言无 POST/PUT
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toBeVisible();
    await page.getByTestId("editor-state-checkpoint-create").click();
    // 无条件等待精确失败文案（证明前端已同步拒绝，而非异步排队后补发）
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveText(MSG_CHECKPOINT_CREATE_FAIL, { timeout: 10_000 });
    expect(state.checkpointCreateLog.length).toBe(cpCreateBefore);
    expect(state.putLog.length).toBe(putsWhileHeld);
    expect(state.restoreLog.length).toBe(0);

    restoreGate.release();
    state.restoreMode = { kind: "ok" };
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
    // 释放 revision gate 并完成后 create 仍不得补发
    expect(state.checkpointCreateLog.length).toBe(cpCreateBefore);
  });

  for (const kind of [
    "abort",
    "not_found",
    "http_error",
    "missing_version",
    "invalid_version",
    "blank_version",
    "extra_field",
    "conflict",
  ] as const) {
    test(`restore ${kind}：保留本地、POST=1、零自动重试、阻断`, async ({
      page,
    }) => {
      const state = createProbeState("tech");
      seedRevisions(state, TECH_A, 1);
      if (kind === "http_error") {
        state.restoreMode = { kind: "http_error", status: 500 };
      } else {
        state.restoreMode = { kind };
      }
      const guards = await installRuntimeErrorGuards(page);
      await installRoutes(page, state);
      await page.clock.install();

      await openWorkspace(page, "tech", TECH_A);
      await expandRevisionPanel(page);

      const localText = `${TECH_OVERVIEW}\n保留-${kind}`;
      await editContent(page, "tech", localText);
      await page.clock.fastForward(debounceMs("tech") + 100);
      await expect
        .poll(() => state.putLog.length, { timeout: 10_000 })
        .toBe(1);
      const putsAfterEdit = state.putLog.length;

      await page.getByTestId("editor-state-revision-restore-0").click();
      await page
        .getByTestId("editor-state-revision-confirm-restore-0")
        .click();

      await expect
        .poll(() => state.restoreLog.length, { timeout: 10_000 })
        .toBe(1);

      await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();
      await expect(page.getByTestId(conflictTestId("tech"))).toContainText(
        FULL_STATE_CONFLICT_MSG,
      );
      expect(await readContent(page, "tech")).toBe(localText);

      // POST 失败路径：无条件固定 blocked 文案（不得 reload_failed）
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).toHaveText(MSG_RESTORE_BLOCKED);
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).not.toContainText(MSG_RESTORE_RELOAD_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-status"),
      ).not.toContainText(state.revisions[TECH_A][0].revisionId);

      // 确认态消失；恢复按钮无条件 disabled；零自动重试
      await expect(
        page.getByTestId("editor-state-revision-confirm-0"),
      ).toHaveCount(0);
      await expect(
        page.getByTestId("editor-state-revision-restore-0"),
      ).toBeDisabled();

      await page.clock.fastForward(debounceMs("tech") + 100);
      await page.clock.fastForward(debounceMs("tech") + 100);
      expect(state.restoreLog.length).toBe(1);
      expect(state.putLog.length).toBe(putsAfterEdit);
      expect(guards.pageErrors).toEqual([]);
      expect(await guards.readUnhandled()).toEqual([]);
    });
  }

  test("POST 成功 + GET 失败：业务完成提示；revision POST=1；保持阻断", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);

    state.nextEditorGetFail = true;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);

    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toContainText(MSG_RESTORE_RELOAD_FAIL);
    await expect(page.getByTestId(conflictTestId("tech"))).toBeVisible();

    state.nextEditorGetFail = false;
    await page.getByTestId(reloadTestId("tech")).click();
    await expect(page.getByTestId(conflictTestId("tech"))).toBeHidden({
      timeout: 10_000,
    });
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
  });

  test("迟到 page：折叠后释放不得污染", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    const pageGate = createHoldGate();
    state.pageMode = { kind: "hold", gate: pageGate };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await page.getByTestId("editor-state-revision-toggle").click();
    await pageGate.waitUntilEntered(1);
    expect(pageHitCount(state, TECH_A)).toBe(1);
    // 挂起期间尚未 fulfill
    expect(pageCompleteCount(state, TECH_A)).toBe(0);
    expect(state.listLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    pageGate.release();
    state.pageMode = { kind: "ok" };
    // 必须等迟到 page 真正 fulfill 后再断言内容未污染
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW);
  });

  test("A→B 迟到 restore：双挂起不污染；旧 finally 不误清 B 令牌", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    seedRevisions(state, TECH_B, 1, ["task"]);
    const restoreGateA = createHoldGate();
    const restoreGateB = createHoldGate();
    state.restoreModeByProject[TECH_A] = {
      kind: "gate",
      gate: restoreGateA,
      then: "ok",
    };
    state.restoreModeByProject[TECH_B] = {
      kind: "gate",
      gate: restoreGateB,
      then: "ok",
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGateA.waitUntilEntered(1);

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGateB.waitUntilEntered(1);

    const getsBBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_B,
    ).length;
    const pageBBefore = pageHitCount(state, TECH_B);
    const cpBefore = state.checkpointCreateLog.length;
    const putsBefore = state.putLog.length;

    // 释放 A：不得污染 B；B 令牌仍有效（真实 create 必须 POST=0）
    restoreGateA.release();
    delete state.restoreModeByProject[TECH_A];
    await expect
      .poll(
        () => state.restoreLog.filter((r) => r.projectId === TECH_A).length,
        { timeout: 10_000 },
      )
      .toBe(1);

    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(page.getByTestId(conflictTestId("tech"))).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_B).length,
    ).toBe(0);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_B).length,
    ).toBe(getsBBefore);
    expect(pageHitCount(state, TECH_B)).toBe(pageBBefore);
    expect(state.listLog.length).toBe(0);

    // B restore 仍挂起：点击 create 必须先见固定失败文案，再断言无 POST/额外 PUT
    await page.getByTestId("editor-state-checkpoint-toggle").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-body"),
    ).toBeVisible();
    await page.getByTestId("editor-state-checkpoint-create").click();
    await expect(
      page.getByTestId("editor-state-checkpoint-status"),
    ).toHaveText(MSG_CHECKPOINT_CREATE_FAIL, { timeout: 10_000 });
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.putLog.length).toBe(putsBefore);

    // 释放 B：A/B revision POST 各精确 1；完成后 create 仍不得队列后补发
    restoreGateB.release();
    delete state.restoreModeByProject[TECH_B];
    await expect
      .poll(
        () => state.restoreLog.filter((r) => r.projectId === TECH_B).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_A).length,
    ).toBe(1);
    expect(
      state.restoreLog.filter((r) => r.projectId === TECH_B).length,
    ).toBe(1);
    await expect
      .poll(async () => readContent(page, "tech"), { timeout: 10_000 })
      .toBe(RESTORED_TECH);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.putLog.length).toBe(putsBefore);
  });

  test("迟到 detail：A 挂起点 B 成功后释放 A 不覆盖；项目切换隔离", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    seedRevisions(state, TECH_B, 1, ["revise"]);
    const gateA0 = createHoldGate();
    const gateProjA = createHoldGate();
    state.detailModeByRevisionId[state.revisions[TECH_A][0].revisionId] = {
      kind: "hold",
      gate: gateA0,
    };
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageHitCount(state, TECH_A) + pageHitCount(state, BIZ_A), { timeout: 10_000 }).toBe(1);
    expect(state.listLog.length).toBe(0);

    // 详情 A0 挂起 → 点 B(索引1) 摘要成功 → 释放 A0 不得覆盖
    const revA0 = state.revisions[TECH_A][0].revisionId;
    const revA1 = state.revisions[TECH_A][1].revisionId;
    await page.getByTestId("editor-state-revision-summary-0").click();
    await gateA0.waitUntilEntered(1);
    expect(
      state.detailCompleteLog.filter((d) => d.revisionId === revA0).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-summary-0"),
    ).toContainText("加载摘要");

    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);

    gateA0.release();
    delete state.detailModeByRevisionId[revA0];
    // 等待 A0 真正 fulfill（completion），再断言 B 摘要未被覆盖
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);

    // 项目 A 详情挂起 → 切 B → 释放不得污染 B
    state.detailModeByProject[TECH_A] = { kind: "hold", gate: gateProjA };
    await page.getByTestId("editor-state-revision-summary-0").click();
    await gateProjA.waitUntilEntered(1);
    const completeABeforeProjectSwitch = state.detailCompleteLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);

    const detailBBefore = state.detailLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const detailBCompleteBefore = state.detailCompleteLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const detailAArrivedBeforeRelease = state.detailLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;
    gateProjA.release();
    delete state.detailModeByProject[TECH_A];
    // 迟到 A 详情必须真正 fulfill；不得新增 B 请求/摘要，A 摘要不得写到 B 项目
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.projectId === TECH_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(completeABeforeProjectSwitch + 1);
    expect(
      state.detailLog.filter((d) => d.projectId === TECH_A).length,
    ).toBe(detailAArrivedBeforeRelease);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-detail-error"),
    ).toHaveCount(0);
    expect(
      state.detailLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(detailBBefore);
    expect(
      state.detailCompleteLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(detailBCompleteBefore);
  });

  test("page shape 非法固定失败无泄漏；检查点面板仍存在", async ({ page }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 1);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    state.pageResponseOverride = {
      items: state.revisions[TECH_A],
      nextCursor: null,
      total: 1,
      snapshot: "LEAK_LIST_TOP",
    };
    await openWorkspace(page, "tech", TECH_A);
    await expect(
      page.getByTestId("editor-state-checkpoint-panel"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    let html = await page.content();
    expect(html).not.toContain("LEAK_LIST_TOP");
    expect(html).not.toContain(state.revisions[TECH_A][0].revisionId);

    state.pageResponseOverride = {
      items: [
        {
          ...state.revisions[TECH_A][0],
          snapshot: "LEAK_META",
        },
      ],
      nextCursor: null,
    };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);
    html = await page.content();
    expect(html).not.toContain("LEAK_META");

    // 超 10 条
    state.pageResponseOverride = {
      items: Array.from({ length: 11 }, (_, i) => ({
        revisionId: seedRevisionId(100 + i),
        stateVersion: seedStateVersion(100 + i),
        snapshotBytes: 1,
        sourceKind: "browser_put",
        createdAt: "2026-07-16T00:00:00.000Z",
      })),
      nextCursor: null,
    };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(3);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toContainText(MSG_LIST_FAIL);

    // 无创建/删除/diff/搜索
    await expect(
      page.getByTestId("editor-state-revision-create"),
    ).toHaveCount(0);
    await expect(page.getByText("删除修订")).toHaveCount(0);
    await expect(page.getByText("搜索修订")).toHaveCount(0);
  });

  test("P12D-B 技术标：按需对比成功/一致、严格解析、中文标签、summary/compare/restore 互斥与零泄漏", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    const rev0 = state.revisions[TECH_A][0];
    const rev1 = state.revisions[TECH_A][1];
    // 差异修订：改 chapters + parsedMarkdown + analysisOverview（固定 13 键顺序）
    const diffSnap = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        {
          id: "diff_ch",
          title: "差异章节",
          body: "DIFF_CHAPTER_BODY",
        },
      ],
      parsedMarkdown: "DIFF_PARSED_MARKDOWN_BODY",
      analysisOverview: "DIFF_ANALYSIS_OVERVIEW",
      analysis: { overview: "DIFF_ANALYSIS_OVERVIEW" },
    };
    state.details[rev0.revisionId] = {
      ...state.details[rev0.revisionId],
      snapshot: diffSnap,
    };
    // seed 会在 snapshot 注入泄漏探针，故“一致”用例用固定响应表显式 sameState
    const expectedDiff = buildComparisonPayload(
      state.projects[TECH_A],
      diffSnap,
    );
    expect(expectedDiff.sameState).toBe(false);
    expect(expectedDiff.changedFields).toEqual([
      "chapters",
      "analysis",
      "parsedMarkdown",
      "analysisOverview",
    ]);
    const sameSummary = probeSummarizeSnapshot(
      canonicalSnapshot(state.projects[TECH_A]),
    );
    const samePayload: ComparisonPayload = {
      sameState: true,
      changedFields: [],
      currentSummary: sameSummary,
      targetSummary: { ...sameSummary },
    };
    state.comparisonResponseByRevisionId[rev1.revisionId] = samePayload;

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    // 默认折叠：comparison 精确 0
    expect(state.comparisonLog.length).toBe(0);
    expect(state.comparisonCompleteLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    // 展开后仍无自动 comparison
    expect(state.comparisonLog.length).toBe(0);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const externalBefore = state.externalHits.length;

    // 差异对比
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    const firstCmp = state.comparisonLog[0];
    expect(firstCmp.projectId).toBe(TECH_A);
    expect(firstCmp.revisionId).toBe(rev0.revisionId);
    expect(firstCmp.method).toBe("GET");
    expect(firstCmp.postData).toBeNull();
    expect(firstCmp.hasQuery).toBe(false);
    expect(firstCmp.path).toContain("/comparison");

    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-status-0"),
    ).toHaveText(MSG_COMPARE_DIFF);
    const fieldsText = await page
      .getByTestId("editor-state-revision-comparison-fields-0")
      .innerText();
    // 中文标签且顺序正确；不得出现内部字段键
    expect(fieldsText).toBe(
      [
        CANONICAL_FIELD_LABELS.chapters,
        CANONICAL_FIELD_LABELS.analysis,
        CANONICAL_FIELD_LABELS.parsedMarkdown,
        CANONICAL_FIELD_LABELS.analysisOverview,
      ].join("、"),
    );
    for (const key of expectedDiff.changedFields) {
      expect(fieldsText).not.toContain(key);
    }
    const currentText = await page
      .getByTestId("editor-state-revision-comparison-current-0")
      .innerText();
    const targetText = await page
      .getByTestId("editor-state-revision-comparison-target-0")
      .innerText();
    expect(currentText).toContain("当前版本");
    expect(currentText).toContain(
      `大纲节点 ${expectedDiff.currentSummary.outlineNodeCount}`,
    );
    expect(currentText).toContain(
      `章节 ${expectedDiff.currentSummary.chapterCount}`,
    );
    expect(currentText).toContain(
      `事实 ${expectedDiff.currentSummary.factCount}`,
    );
    expect(currentText).toContain(
      `矩阵行 ${expectedDiff.currentSummary.responseMatrixRowCount}`,
    );
    expect(currentText).toContain(
      `商务条目 ${expectedDiff.currentSummary.businessEntryTotal}`,
    );
    expect(targetText).toContain("所选修订");
    expect(targetText).toContain(
      `大纲节点 ${expectedDiff.targetSummary.outlineNodeCount}`,
    );
    expect(targetText).toContain(
      `章节 ${expectedDiff.targetSummary.chapterCount}`,
    );
    expect(targetText).toContain(
      expectedDiff.targetSummary.hasParsedMarkdown
        ? "含解析正文"
        : "无解析正文",
    );

    // 零旁路
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);

    // 同状态文案（第二条）
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-comparison-status-1"),
    ).toHaveText(MSG_COMPARE_SAME);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-fields-1"),
    ).toHaveCount(0);

    // summary / compare 互斥：点摘要作废比较
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toHaveCount(0);

    // compare 作废摘要
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toHaveCount(0);

    // restore 确认作废比较
    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-0").click();

    // 重新加载成功结果，再测严格 shape 失败会清除旧结果
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(4);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();

    const baseOk = buildComparisonPayload(
      state.projects[TECH_A],
      state.details[rev0.revisionId].snapshot,
    );
    const illegalCases: unknown[] = [
      { ...baseOk, leakExtra: "CMP_TOP_EXTRA" },
      {
        sameState: false,
        changedFields: ["chapters", "unknownField"],
        currentSummary: baseOk.currentSummary,
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: ["chapters", "chapters"],
        currentSummary: baseOk.currentSummary,
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: ["parsedMarkdown", "chapters"],
        currentSummary: baseOk.currentSummary,
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: true,
        changedFields: ["chapters"],
        currentSummary: baseOk.currentSummary,
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          ...baseOk.currentSummary,
          outlineNodeCount: -1,
        },
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          ...baseOk.currentSummary,
          chapterCount: 1.5,
        },
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          ...baseOk.currentSummary,
          factCount: "1",
        },
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          ...baseOk.currentSummary,
          hasParsedMarkdown: "yes",
        },
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          outlineNodeCount: 1,
          chapterCount: 1,
          factCount: 0,
          responseMatrixRowCount: 0,
          businessEntryTotal: 0,
          hasParsedMarkdown: false,
          extra: 1,
        },
        targetSummary: baseOk.targetSummary,
      },
      {
        sameState: false,
        changedFields: baseOk.changedFields,
        currentSummary: {
          outlineNodeCount: 1,
          chapterCount: 1,
          factCount: 0,
          responseMatrixRowCount: 0,
          businessEntryTotal: 0,
        },
        targetSummary: baseOk.targetSummary,
      },
    ];

    let completeBase = state.comparisonCompleteLog.length;
    for (const bad of illegalCases) {
      state.comparisonResponseOverride = bad;
      // 再次点击同一项：关闭 → 再点加载
      await page.getByTestId("editor-state-revision-compare-0").click();
      await expect(
        page.getByTestId("editor-state-revision-comparison-0"),
      ).toHaveCount(0);
      await page.getByTestId("editor-state-revision-compare-0").click();
      completeBase += 1;
      await expect
        .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
        .toBe(completeBase);
      await expect(
        page.getByTestId("editor-state-revision-comparison-error"),
      ).toHaveText(MSG_COMPARE_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-comparison-0"),
      ).toHaveCount(0);
      const html = await page.content();
      expect(html).not.toContain("CMP_TOP_EXTRA");
      expect(html).not.toContain(rev0.revisionId);
      expect(html).not.toContain(rev0.stateVersion);
      expect(html).not.toContain("unknownField");
      expect(html).not.toContain("DIFF_PARSED_MARKDOWN_BODY");
      expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    state.comparisonResponseOverride = null;

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    // 未使用变量：rev1 保证 seed 存在
    expect(rev1.sourceKind).toBe("task");
  });

  test("P12D-B 技术标：comparison arrived+complete 迟到隔离（A0→A1、项目切换、折叠/刷新/摘要/恢复）", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    seedRevisions(state, TECH_B, 1, ["revise"]);
    // A0 差异、A1 一致，便于观察是否被覆盖
    const revA0 = state.revisions[TECH_A][0].revisionId;
    const revA1 = state.revisions[TECH_A][1].revisionId;
    state.details[revA0].snapshot = {
      ...state.details[revA0].snapshot,
      chapters: [{ id: "a0", title: "A0_ONLY", body: "A0_BODY" }],
      analysisOverview: "A0_OVERVIEW_DIFF",
      analysis: { overview: "A0_OVERVIEW_DIFF" },
    };
    // A1 固定 sameState，避免 seed 泄漏探针导致“差异”假失败
    const sameSummaryA = probeSummarizeSnapshot(
      canonicalSnapshot(state.projects[TECH_A]),
    );
    state.comparisonResponseByRevisionId[revA1] = {
      sameState: true,
      changedFields: [],
      currentSummary: sameSummaryA,
      targetSummary: { ...sameSummaryA },
    };

    const gateA0 = createHoldGate();
    const gateProjA = createHoldGate();
    state.comparisonModeByRevisionId[revA0] = {
      kind: "hold",
      gate: gateA0,
    };

    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    // A0 挂起 → A1 成功 → 释放 A0 不得覆盖
    await page.getByTestId("editor-state-revision-compare-0").click();
    await gateA0.waitUntilEntered(1);
    expect(
      state.comparisonCompleteLog.filter((d) => d.revisionId === revA0)
        .length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-compare-0"),
    ).toContainText("正在对比");

    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-status-1"),
    ).toHaveText(MSG_COMPARE_SAME);
    await expect(
      page.getByTestId("editor-state-revision-comparison-error"),
    ).toHaveCount(0);

    gateA0.release();
    delete state.comparisonModeByRevisionId[revA0];
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-status-1"),
    ).toHaveText(MSG_COMPARE_SAME);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);

    // 折叠作废
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);

    // 刷新作废：先成功对比，再刷新
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(3);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toHaveCount(0);

    // 摘要作废比较
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(3);
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();

    // 恢复确认作废比较
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(4);
    await page.getByTestId("editor-state-revision-restore-1").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-1").click();

    // 项目 A 对比挂起 → 切 B → 释放不得污染
    state.comparisonModeByProject[TECH_A] = {
      kind: "hold",
      gate: gateProjA,
    };
    await page.getByTestId("editor-state-revision-compare-0").click();
    await gateProjA.waitUntilEntered(1);
    const completeABeforeSwitch = state.comparisonCompleteLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-error"),
    ).toHaveCount(0);

    const cmpBArrivedBefore = state.comparisonLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const cmpBCompleteBefore = state.comparisonCompleteLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    gateProjA.release();
    delete state.comparisonModeByProject[TECH_A];
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.projectId === TECH_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(completeABeforeSwitch + 1);
    expect(
      state.comparisonLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(cmpBArrivedBefore);
    expect(
      state.comparisonCompleteLog.filter((d) => d.projectId === TECH_B)
        .length,
    ).toBe(cmpBCompleteBefore);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);
  });
});

// ---------------------------------------------------------------------------
// 商务标
// ---------------------------------------------------------------------------
test.describe("P12C-C3 商务标修订历史", () => {
  test.describe.configure({ mode: "serial" });

  test("默认折叠零请求；成功恢复水合商务 13 键；两窗口零 PUT；下一编辑 +1", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 1, ["revise"]);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.listLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    expect(state.restoreLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, BIZ_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);

    const putsAfter = state.putLog.length;
    await page.clock.fastForward(debounceMs("biz") + 100);
    await page.clock.fastForward(debounceMs("biz") + 100);
    expect(state.putLog.length).toBe(putsAfter);

    await editContent(page, "biz", `${RESTORED_BIZ}\n下一编辑`);
    await page.clock.fastForward(debounceMs("biz") + 100);
    await expect
      .poll(() => state.putLog.length, { timeout: 10_000 })
      .toBe(putsAfter + 1);

    const putBody = state.putLog[state.putLog.length - 1].body;
    expect(putBody.parsedMarkdown).toBe(`${RESTORED_BIZ}\n下一编辑`);
    expect(JSON.stringify(putBody.businessQualify)).toContain(
      BIZ_RESTORE_QUALIFY,
    );
    expect(JSON.stringify(putBody.businessToc)).toContain(BIZ_RESTORE_TOC);
    expect(JSON.stringify(putBody.businessQuote)).toContain(BIZ_RESTORE_QUOTE);
    expect(JSON.stringify(putBody.businessQuote)).toContain(
      BIZ_RESTORE_QUOTE_NOTES,
    );
    expect(JSON.stringify(putBody.businessCommit)).toContain(
      BIZ_RESTORE_COMMIT,
    );

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("商务：确认前 POST=0；409 阻断零重试；A→B 迟到 list 不污染", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 1);
    seedRevisions(state, BIZ_B, 1, ["callback"]);
    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    await expandRevisionPanel(page);

    await page.getByTestId("editor-state-revision-restore-0").click();
    expect(state.restoreLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toContainText(RESTORE_CONFIRM);

    state.restoreMode = { kind: "conflict" };
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId(conflictTestId("biz"))).toBeVisible();
    expect(state.restoreLog.length).toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveText(MSG_RESTORE_BLOCKED);
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();

    // A→B 迟到 page：必须在 A 真正发出 page 并 waitUntilEntered 后再切 B
    // pageLog 在 gate.wait 前已写入，释放后只能靠 pageCompleteLog 证明真正 fulfill
    const pageGateA = createHoldGate();
    state.pageModeByProject[BIZ_A] = { kind: "hold", gate: pageGateA };
    // 项目仍在 A：真实点击刷新触发 A page 挂起；先记录 release 前 completion 基线
    const pageCompleteABefore = pageCompleteCount(state, BIZ_A);
    await page.getByTestId("editor-state-revision-refresh").click();
    await pageGateA.waitUntilEntered(1);
    expect(pageHitCount(state, BIZ_A)).toBe(2);
    // 挂起期间尚未 fulfill（arrived 已是 2，不能当作 completion 证据）
    expect(pageCompleteCount(state, BIZ_A)).toBe(pageCompleteABefore);
    expect(state.listLog.length).toBe(0);

    await openWorkspace(page, "biz", BIZ_B);
    await expandRevisionPanel(page);
    // 等 B 的 page 真正完成后再拍快照
    await expect
      .poll(() => pageCompleteCount(state, BIZ_B), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    const pageBSnapshot = pageHitCount(state, BIZ_B);
    const pageCompleteBSnapshot = pageCompleteCount(state, BIZ_B);
    const sourceB = await page
      .getByTestId("editor-state-revision-source-0")
      .innerText();
    const bodyB = await readContent(page, "biz");
    expect(bodyB).toBe(BIZ_MD_B);
    expect(sourceB).toBe(SOURCE_LABELS.callback);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);

    pageGateA.release();
    delete state.pageModeByProject[BIZ_A];
    // 必须等迟到 page 真正 fulfill 后再断言 B 未污染
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), { timeout: 10_000 })
      .toBe(pageCompleteABefore + 1);
    // B 列表/完成/来源/提示/正文不变
    expect(pageHitCount(state, BIZ_B)).toBe(pageBSnapshot);
    expect(pageCompleteCount(state, BIZ_B)).toBe(pageCompleteBSnapshot);
    expect(await readContent(page, "biz")).toBe(BIZ_MD_B);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.callback);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
  });

  test("P12D-B 商务标：共享对比入口成功、comparison 精确 1、正文不变、零旁路", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 1, ["callback"]);
    const rev0 = state.revisions[BIZ_A][0];
    // 商务差异：改 parsedMarkdown 与 businessQualify
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      parsedMarkdown: "BIZ_DIFF_PARSED_MD",
      businessQualify: [
        {
          id: "q_diff",
          requirement: "DIFF_QUALIFY",
          response: "r",
          evidence: "e",
          status: "matched",
        },
      ],
    };
    const expected = buildComparisonPayload(
      state.projects[BIZ_A],
      state.details[rev0.revisionId].snapshot,
    );
    expect(expected.sameState).toBe(false);

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.comparisonLog.length).toBe(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.comparisonLog.length).toBe(0);

    const bodyBefore = await readContent(page, "biz");
    expect(bodyBefore).toBe(BIZ_MD);
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const externalBefore = state.externalHits.length;

    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.comparisonLog[0].revisionId).toBe(rev0.revisionId);
    expect(state.comparisonLog[0].method).toBe("GET");
    expect(state.comparisonLog[0].postData).toBeNull();
    expect(state.comparisonLog[0].hasQuery).toBe(false);

    await expect(
      page.getByTestId("editor-state-revision-comparison-status-0"),
    ).toHaveText(MSG_COMPARE_DIFF);
    const fields = await page
      .getByTestId("editor-state-revision-comparison-fields-0")
      .innerText();
    expect(fields).toContain(CANONICAL_FIELD_LABELS.parsedMarkdown);
    expect(fields).toContain(CANONICAL_FIELD_LABELS.businessQualify);
    expect(fields).not.toContain("parsedMarkdown");
    expect(fields).not.toContain("businessQualify");

    // 正文不变、零旁路
    expect(await readContent(page, "biz")).toBe(BIZ_MD);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);
    expect(state.comparisonLog.length).toBe(1);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});


// ---------------------------------------------------------------------------
// P12E-A 正文差异：独立 describe，避免 serial 首败导致 did not run
// ---------------------------------------------------------------------------

test.describe("P12E-A 技术标正文差异-成功与严格解析", () => {
  test("P12E-A 技术标：按需正文差异成功/一致、严格 parser、summary/compare/restore 互斥与零泄漏", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    const rev0 = state.revisions[TECH_A][0];
    const rev1 = state.revisions[TECH_A][1];

    // 差异修订：仅改 chapters 正文，便于 body-diff 探针生成 changed
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        {
          id: "n1",
          title: "一级目录",
          body: "P12E_TARGET_CHAPTER_BODY",
        },
      ],
    };
    const expectedDiff = buildBodyDiffPayload(
      state.projects[TECH_A],
      state.details[rev0.revisionId].snapshot,
    );
    expect(expectedDiff.sameBody).toBe(false);
    expect(expectedDiff.changedChapterCount).toBeGreaterThan(0);

    // 一致：显式 sameBody，避免 seed 泄漏探针干扰
    const samePayload: BodyDiffPayload = {
      sameBody: true,
      changedChapterCount: 0,
      currentChapterCount: Array.isArray(state.projects[TECH_A].chapters)
        ? state.projects[TECH_A].chapters.length
        : 0,
      targetChapterCount: Array.isArray(
        state.details[rev1.revisionId].snapshot.chapters,
      )
        ? (state.details[rev1.revisionId].snapshot.chapters as unknown[])
            .length
        : 0,
      truncated: false,
      items: [],
    };
    state.bodyDiffResponseByRevisionId[rev1.revisionId] = samePayload;

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.bodyDiffLog.length).toBe(0);
    expect(state.bodyDiffCompleteLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.bodyDiffLog.length).toBe(0);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const comparisonBefore = state.comparisonLog.length;
    const externalBefore = state.externalHits.length;
    const bodyBefore = await readContent(page, "tech");

    // 差异正文
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    const first = state.bodyDiffLog[0];
    expect(first.projectId).toBe(TECH_A);
    expect(first.revisionId).toBe(rev0.revisionId);
    expect(first.method).toBe("GET");
    expect(first.postData).toBeNull();
    expect(first.hasQuery).toBe(false);
    expect(first.path).toContain("/body-diff");

    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-status-0"),
    ).toHaveText(`共 ${expectedDiff.changedChapterCount} 章正文有变化`);
    // 中文标签：保留/删除/新增；不得暴露 op 原值
    const resultText = await page
      .getByTestId("editor-state-revision-body-diff-result-0")
      .innerText();
    expect(resultText).toMatch(/保留|删除|新增/);
    expect(resultText).not.toContain("equal");
    expect(resultText).not.toContain("delete");
    expect(resultText).not.toContain("insert");
    expect(resultText).not.toContain("sameBody");
    expect(resultText).not.toContain("changedChapterCount");
    expect(resultText).not.toContain(rev0.revisionId);
    expect(resultText).not.toContain(rev0.stateVersion);

    // 零旁路：不触发 comparison/detail/restore/PUT/额外 editor GET
    expect(state.comparisonLog.length).toBe(comparisonBefore);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);
    expect(await readContent(page, "tech")).toBe(bodyBefore);

    // 一致文案（第二条）
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-status-1"),
    ).toHaveText(MSG_BODY_DIFF_SAME);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);

    // summary 作废 body-diff
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toHaveCount(0);

    // body-diff 作废 summary
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toHaveCount(0);

    // compare 作废 body-diff
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);

    // body-diff 作废 compare
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(4);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);

    // restore 确认作废 body-diff
    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-0").click();

    // 严格 shape：非法响应固定失败文案，无泄漏
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(5);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();

    const baseOk = buildBodyDiffPayload(
      state.projects[TECH_A],
      state.details[rev0.revisionId].snapshot,
    );
    const illegalCases: unknown[] = [
      { ...baseOk, leakExtra: "BODY_DIFF_TOP_EXTRA" },
      {
        sameBody: true,
        changedChapterCount: 1,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: baseOk.items,
      },
      {
        sameBody: false,
        changedChapterCount: 99,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: baseOk.items,
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 2,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "mutated",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "replace", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        currentChapterCount: baseOk.currentChapterCount,
        targetChapterCount: baseOk.targetChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a", extra: 1 }],
          },
        ],
      },
    ];

    let completeBase = state.bodyDiffCompleteLog.length;
    for (const bad of illegalCases) {
      state.bodyDiffResponseOverride = bad;
      await page.getByTestId("editor-state-revision-body-diff-0").click();
      await expect(
        page.getByTestId("editor-state-revision-body-diff-result-0"),
      ).toHaveCount(0);
      await page.getByTestId("editor-state-revision-body-diff-0").click();
      completeBase += 1;
      await expect
        .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
        .toBe(completeBase);
      await expect(
        page.getByTestId("editor-state-revision-body-diff-error"),
      ).toHaveText(MSG_BODY_DIFF_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-body-diff-result-0"),
      ).toHaveCount(0);
      const html = await page.content();
      expect(html).not.toContain("BODY_DIFF_TOP_EXTRA");
      expect(html).not.toContain(rev0.revisionId);
      expect(html).not.toContain(rev0.stateVersion);
      expect(html).not.toContain("mutated");
      expect(html).not.toContain("replace");
      expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
      expect(html).not.toContain("P12E_TARGET_CHAPTER_BODY");
    }
    state.bodyDiffResponseOverride = null;

    // 截断提示文案使用常量（错误态后 revisionId 仍挂着，先点一次关闭再打开）
    state.bodyDiffResponseByRevisionId[rev0.revisionId] = {
      ...baseOk,
      truncated: true,
    };
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-error"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(completeBase + 1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-truncated-0"),
    ).toHaveText(MSG_BODY_DIFF_TRUNCATED);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    expect(rev1.sourceKind).toBe("task");
  });
});

test.describe("P12E-A 技术标正文差异-迟到隔离", () => {
  test("P12E-A 技术标：body-diff arrived+complete 迟到隔离（A0→A1、项目切换、折叠/刷新/摘要/对比/恢复）", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 2, ["browser_put", "task"]);
    seedRevisions(state, TECH_B, 1, ["revise"]);
    const revA0 = state.revisions[TECH_A][0].revisionId;
    const revA1 = state.revisions[TECH_A][1].revisionId;

    state.details[revA0].snapshot = {
      ...state.details[revA0].snapshot,
      chapters: [{ id: "a0", title: "A0_ONLY", body: "A0_BODY_DIFF" }],
    };
    state.bodyDiffResponseByRevisionId[revA1] = {
      sameBody: true,
      changedChapterCount: 0,
      currentChapterCount: 1,
      targetChapterCount: 1,
      truncated: false,
      items: [],
    };

    const gateA0 = createHoldGate();
    const gateProjA = createHoldGate();
    state.bodyDiffModeByRevisionId[revA0] = {
      kind: "hold",
      gate: gateA0,
    };

    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    // A0 挂起 → A1 成功 → 释放 A0 不得覆盖
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await gateA0.waitUntilEntered(1);
    expect(
      state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA0).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-0"),
    ).toContainText("加载正文差异");

    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-status-1"),
    ).toHaveText(MSG_BODY_DIFF_SAME);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-error"),
    ).toHaveCount(0);

    gateA0.release();
    delete state.bodyDiffModeByRevisionId[revA0];
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-status-1"),
    ).toHaveText(MSG_BODY_DIFF_SAME);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);

    // 折叠作废
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);

    // 刷新作废
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(3);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toHaveCount(0);

    // 摘要作废 body-diff
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(3);
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA1).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-1"),
    ).toBeVisible();

    // 对比作废 body-diff
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(4);
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-1"),
    ).toBeVisible();

    // 恢复确认作废 body-diff
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(5);
    await page.getByTestId("editor-state-revision-restore-1").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-1"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-1").click();

    // 项目 A 挂起 → 切 B → 释放不得污染
    state.bodyDiffModeByProject[TECH_A] = {
      kind: "hold",
      gate: gateProjA,
    };
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await gateProjA.waitUntilEntered(1);
    const completeABeforeSwitch = state.bodyDiffCompleteLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-error"),
    ).toHaveCount(0);

    const bdBArrivedBefore = state.bodyDiffLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const bdBCompleteBefore = state.bodyDiffCompleteLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    gateProjA.release();
    delete state.bodyDiffModeByProject[TECH_A];
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.projectId === TECH_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(completeABeforeSwitch + 1);
    expect(
      state.bodyDiffLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(bdBArrivedBefore);
    expect(
      state.bodyDiffCompleteLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(bdBCompleteBefore);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.revise);
  });
});

test.describe("P12E-A 商务标正文差异-共享入口", () => {
  test("P12E-A 商务标：共享正文差异入口成功、精确 1 次 GET、无 query/body、正文不变、零旁路", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    // 商务当前侧也给 chapters，使 body-diff 有真实差异
    state.projects[BIZ_A].chapters = [
      { id: "biz_ch", title: "商务章节", body: "BIZ_CURRENT_CHAPTER_BODY" },
    ];
    seedRevisions(state, BIZ_A, 1, ["callback"]);
    const rev0 = state.revisions[BIZ_A][0];
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        {
          id: "biz_ch",
          title: "商务章节",
          body: "BIZ_TARGET_CHAPTER_BODY",
        },
      ],
    };
    const expected = buildBodyDiffPayload(
      state.projects[BIZ_A],
      state.details[rev0.revisionId].snapshot,
    );
    expect(expected.sameBody).toBe(false);

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.bodyDiffLog.length).toBe(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.bodyDiffLog.length).toBe(0);

    const bodyBefore = await readContent(page, "biz");
    expect(bodyBefore).toBe(BIZ_MD);
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const comparisonBefore = state.comparisonLog.length;
    const externalBefore = state.externalHits.length;

    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.bodyDiffLog[0].revisionId).toBe(rev0.revisionId);
    expect(state.bodyDiffLog[0].method).toBe("GET");
    expect(state.bodyDiffLog[0].postData).toBeNull();
    expect(state.bodyDiffLog[0].hasQuery).toBe(false);
    expect(state.bodyDiffLog[0].path).toContain("/body-diff");

    await expect(
      page.getByTestId("editor-state-revision-body-diff-status-0"),
    ).toHaveText(`共 ${expected.changedChapterCount} 章正文有变化`);
    const resultText = await page
      .getByTestId("editor-state-revision-body-diff-result-0")
      .innerText();
    expect(resultText).toMatch(/保留|删除|新增/);
    expect(resultText).not.toContain("equal");
    expect(resultText).not.toContain("delete");
    expect(resultText).not.toContain("insert");

    // 正文不变、零旁路
    expect(await readContent(page, "biz")).toBe(BIZ_MD);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.comparisonLog.length).toBe(comparisonBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);
    expect(state.bodyDiffLog.length).toBe(1);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

// ---------------------------------------------------------------------------
// P12E-C 双修订正文差异
// ---------------------------------------------------------------------------
test.describe("P12E-C 技术标双修订正文差异-成功与严格解析", () => {
  test("P12E-C 技术标：选择零请求、pair 成功/同正文、严格 parser、互斥与零泄漏", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 3, ["browser_put", "task", "revise"]);
    const rev0 = state.revisions[TECH_A][0];
    const rev1 = state.revisions[TECH_A][1];
    const rev2 = state.revisions[TECH_A][2];

    // before=rev0、after=rev1：正文有变化
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        { id: "p12ec1", title: "差异前章", body: "PAIR_BEFORE_BODY" },
      ],
    };
    state.details[rev1.revisionId].snapshot = {
      ...state.details[rev1.revisionId].snapshot,
      chapters: [
        { id: "p12ec1", title: "差异后章", body: "PAIR_AFTER_BODY" },
      ],
    };
    const expectedDiff = buildPairBodyDiffPayload(
      state.details[rev0.revisionId].snapshot,
      state.details[rev1.revisionId].snapshot,
    );
    expect(expectedDiff.sameBody).toBe(false);
    expect(expectedDiff.changedChapterCount).toBeGreaterThan(0);

    // before=rev1、after=rev2：同正文
    state.details[rev2.revisionId].snapshot = {
      ...state.details[rev1.revisionId].snapshot,
    };
    const samePayload: PairBodyDiffPayload = {
      sameBody: true,
      changedChapterCount: 0,
      beforeChapterCount: 1,
      afterChapterCount: 1,
      truncated: false,
      items: [],
    };
    state.pairBodyDiffResponseByPairKey[
      pairBodyDiffKey(rev1.revisionId, rev2.revisionId)
    ] = samePayload;

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.pairBodyDiffLog.length).toBe(0);
    expect(state.pairBodyDiffCompleteLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.pairBodyDiffLog.length).toBe(0);

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === TECH_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const comparisonBefore = state.comparisonLog.length;
    const bodyDiffBefore = state.bodyDiffLog.length;
    const externalBefore = state.externalHits.length;
    const bodyBefore = await readContent(page, "tech");

    // 选择动作不发任何请求
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await expect(
      page.getByTestId("editor-state-revision-pair-select-before-0"),
    ).toContainText("已选为差异前");
    await expect(
      page.getByTestId("editor-state-revision-pair-select-after-1"),
    ).toContainText("已选为差异后");
    expect(state.pairBodyDiffLog.length).toBe(0);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.comparisonLog.length).toBe(comparisonBefore);
    expect(state.bodyDiffLog.length).toBe(bodyDiffBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);

    // 空/单侧：比较禁用
    await page.getByTestId("editor-state-revision-pair-clear").click();
    expect(state.pairBodyDiffLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-compare"),
    ).toBeDisabled();
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await expect(
      page.getByTestId("editor-state-revision-pair-compare"),
    ).toBeDisabled();
    expect(state.pairBodyDiffLog.length).toBe(0);

    // 两侧齐后比较：精确 1 次 pair GET，无 query/body
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    const first = state.pairBodyDiffLog[0];
    expect(first.projectId).toBe(TECH_A);
    expect(first.beforeRevisionId).toBe(rev0.revisionId);
    expect(first.afterRevisionId).toBe(rev1.revisionId);
    expect(first.method).toBe("GET");
    expect(first.postData).toBeNull();
    expect(first.hasQuery).toBe(false);
    expect(first.path).toContain(
      `/editor-state-revisions/${rev0.revisionId}/body-diff/${rev1.revisionId}`,
    );
    // 不得误打单修订 body-diff
    expect(state.bodyDiffLog.length).toBe(bodyDiffBefore);

    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-status"),
    ).toHaveText(`共 ${expectedDiff.changedChapterCount} 章正文有变化`);
    await expect(
      page.getByTestId("editor-state-revision-pair-meta"),
    ).toContainText(`差异前章节 ${expectedDiff.beforeChapterCount}`);
    await expect(
      page.getByTestId("editor-state-revision-pair-meta"),
    ).toContainText(`差异后章节 ${expectedDiff.afterChapterCount}`);
    const pairResultText = await page
      .getByTestId("editor-state-revision-pair-result")
      .innerText();
    expect(pairResultText).toMatch(/差异前修订|差异后修订/);
    expect(pairResultText).toMatch(/保留|删除|新增/);
    expect(pairResultText).not.toContain("equal");
    expect(pairResultText).not.toContain("delete");
    expect(pairResultText).not.toContain("insert");
    expect(pairResultText).not.toContain("sameBody");
    expect(pairResultText).not.toContain("beforeChapterCount");
    expect(pairResultText).not.toContain(rev0.revisionId);
    expect(pairResultText).not.toContain(rev1.revisionId);
    expect(pairResultText).not.toContain(rev0.stateVersion);

    // 零旁路
    expect(state.comparisonLog.length).toBe(comparisonBefore);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === TECH_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);
    expect(await readContent(page, "tech")).toBe(bodyBefore);

    // 同正文结果
    await page.getByTestId("editor-state-revision-pair-clear").click();
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-pair-status"),
    ).toHaveText(MSG_PAIR_BODY_DIFF_SAME);

    // 同一项不得同时承担两侧：先选 after-0，再选 before-0 应切换前侧并清后侧
    await page.getByTestId("editor-state-revision-pair-clear").click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await expect(
      page.getByTestId("editor-state-revision-pair-select-before-0"),
    ).toContainText("已选为差异前");
    await expect(
      page.getByTestId("editor-state-revision-pair-compare"),
    ).toBeDisabled();
    expect(state.pairBodyDiffLog.length).toBe(2);

    // summary 作废 pair 结果
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(3);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // pair 作废 summary
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(4);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);

    // compare 作废 pair
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(() => state.comparisonCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // pair 作废 compare
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(5);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);

    // 单修订 body-diff 作废 pair
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(() => state.bodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // pair 作废 body-diff
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(6);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);

    // restore 确认作废 pair
    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-0").click();

    // 严格 shape：非法响应固定失败且清除旧结果
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(7);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();

    const baseOk = buildPairBodyDiffPayload(
      state.details[rev0.revisionId].snapshot,
      state.details[rev1.revisionId].snapshot,
    );
    const illegalCases: unknown[] = [
      { ...baseOk, leakExtra: "PAIR_BODY_DIFF_TOP_EXTRA" },
      {
        sameBody: true,
        changedChapterCount: 1,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: baseOk.items,
      },
      {
        sameBody: false,
        changedChapterCount: 99,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: baseOk.items,
      },
      // 使用单修订字段名必须拒绝
      {
        sameBody: false,
        changedChapterCount: 1,
        currentChapterCount: 1,
        targetChapterCount: 1,
        truncated: false,
        items: baseOk.items,
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 2,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "mutated",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "replace", text: "a" }],
          },
        ],
      },
      {
        sameBody: false,
        changedChapterCount: 1,
        beforeChapterCount: baseOk.beforeChapterCount,
        afterChapterCount: baseOk.afterChapterCount,
        truncated: false,
        items: [
          {
            ordinal: 1,
            kind: "changed",
            beforeTitle: "x",
            afterTitle: "y",
            hunks: [{ op: "delete", text: "a", extra: 1 }],
          },
        ],
      },
    ];

    let completeBase = state.pairBodyDiffCompleteLog.length;
    for (const bad of illegalCases) {
      state.pairBodyDiffResponseOverride = bad;
      // 先清再选，确保发起新请求
      await page.getByTestId("editor-state-revision-pair-clear").click();
      await page
        .getByTestId("editor-state-revision-pair-select-before-0")
        .click();
      await page
        .getByTestId("editor-state-revision-pair-select-after-1")
        .click();
      await page.getByTestId("editor-state-revision-pair-compare").click();
      completeBase += 1;
      await expect
        .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
        .toBe(completeBase);
      await expect(
        page.getByTestId("editor-state-revision-pair-error"),
      ).toHaveText(MSG_PAIR_BODY_DIFF_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-pair-result"),
      ).toHaveCount(0);
      const html = await page.content();
      expect(html).not.toContain("PAIR_BODY_DIFF_TOP_EXTRA");
      expect(html).not.toContain(rev0.revisionId);
      expect(html).not.toContain(rev1.revisionId);
      expect(html).not.toContain("mutated");
      expect(html).not.toContain("replace");
      expect(html).not.toContain(SNAPSHOT_BODY_LEAK);
      expect(html).not.toContain("PAIR_BEFORE_BODY");
      expect(html).not.toContain("PAIR_AFTER_BODY");
    }
    state.pairBodyDiffResponseOverride = null;

    // 截断提示
    state.pairBodyDiffResponseByPairKey[
      pairBodyDiffKey(rev0.revisionId, rev1.revisionId)
    ] = {
      ...baseOk,
      truncated: true,
    };
    await page.getByTestId("editor-state-revision-pair-clear").click();
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(completeBase + 1);
    await expect(
      page.getByTestId("editor-state-revision-pair-truncated"),
    ).toHaveText(MSG_PAIR_BODY_DIFF_TRUNCATED);
    await expect(
      page.getByTestId("editor-state-revision-pair-error"),
    ).toHaveCount(0);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    expect(rev2.sourceKind).toBe("revise");
  });
});

test.describe("P12E-C 技术标双修订正文差异-迟到隔离", () => {
  test("P12E-C 技术标：pair arrived+complete 迟到隔离（A0→A1、重选、折叠/刷新、摘要/对比/正文差异/恢复、项目切换）", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 3, ["browser_put", "task", "revise"]);
    seedRevisions(state, TECH_B, 2, ["callback", "local_parser"]);
    const revA0 = state.revisions[TECH_A][0].revisionId;
    const revA1 = state.revisions[TECH_A][1].revisionId;
    const revA2 = state.revisions[TECH_A][2].revisionId;

    state.details[revA0].snapshot = {
      ...state.details[revA0].snapshot,
      chapters: [{ id: "a0", title: "A0_ONLY", body: "A0_PAIR_BODY" }],
    };
    state.details[revA1].snapshot = {
      ...state.details[revA1].snapshot,
      chapters: [{ id: "a1", title: "A1_ONLY", body: "A1_PAIR_BODY" }],
    };
    state.details[revA2].snapshot = {
      ...state.details[revA2].snapshot,
      chapters: [{ id: "a2", title: "A2_ONLY", body: "A2_PAIR_BODY" }],
    };

    // A1 pair 固定同正文，便于断言不被 A0 覆盖
    state.pairBodyDiffResponseByPairKey[pairBodyDiffKey(revA1, revA2)] = {
      sameBody: true,
      changedChapterCount: 0,
      beforeChapterCount: 1,
      afterChapterCount: 1,
      truncated: false,
      items: [],
    };

    const gateA0 = createHoldGate();
    const gateProjA = createHoldGate();
    state.pairBodyDiffModeByPairKey[pairBodyDiffKey(revA0, revA1)] = {
      kind: "hold",
      gate: gateA0,
    };

    await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    // A0 挂起 → 重选 A1 成功 → 释放 A0 不得覆盖
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await gateA0.waitUntilEntered(1);
    expect(
      state.pairBodyDiffCompleteLog.filter(
        (d) =>
          d.beforeRevisionId === revA0 && d.afterRevisionId === revA1,
      ).length,
    ).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-compare"),
    ).toContainText("正在比较");

    // 重选两侧并发起 A1 pair
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-status"),
    ).toHaveText(MSG_PAIR_BODY_DIFF_SAME);
    await expect(
      page.getByTestId("editor-state-revision-pair-error"),
    ).toHaveCount(0);

    gateA0.release();
    delete state.pairBodyDiffModeByPairKey[pairBodyDiffKey(revA0, revA1)];
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA0 && d.afterRevisionId === revA1,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-status"),
    ).toHaveText(MSG_PAIR_BODY_DIFF_SAME);
    // 不得被迟到 A0 改成有变化文案
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    const lateText = await page
      .getByTestId("editor-state-revision-pair-result")
      .innerText();
    expect(lateText).not.toContain("A0_ONLY");
    expect(lateText).not.toContain("A0_PAIR_BODY");

    // 折叠作废
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-compare"),
    ).toBeDisabled();

    // 刷新作废
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), {
        timeout: 10_000,
      })
      .toBe(3);
    expect(state.listLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 摘要作废 pair
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(3);
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 对比作废 pair
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(4);
    await page.getByTestId("editor-state-revision-compare-1").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 单修订 body-diff 作废 pair
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(5);
    await page.getByTestId("editor-state-revision-body-diff-1").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA1)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 恢复确认作废 pair
    await page
      .getByTestId("editor-state-revision-pair-select-before-1")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-2")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter(
            (d) =>
              d.beforeRevisionId === revA1 && d.afterRevisionId === revA2,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(6);
    await page.getByTestId("editor-state-revision-restore-1").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-1"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await page.getByTestId("editor-state-revision-cancel-restore-1").click();

    // 项目 A 挂起 → 切 B → 释放不得污染
    state.pairBodyDiffModeByProject[TECH_A] = {
      kind: "hold",
      gate: gateProjA,
    };
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await gateProjA.waitUntilEntered(1);
    const completeABeforeSwitch = state.pairBodyDiffCompleteLog.filter(
      (d) => d.projectId === TECH_A,
    ).length;

    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-error"),
    ).toHaveCount(0);

    const pairBArrivedBefore = state.pairBodyDiffLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    const pairBCompleteBefore = state.pairBodyDiffCompleteLog.filter(
      (d) => d.projectId === TECH_B,
    ).length;
    gateProjA.release();
    delete state.pairBodyDiffModeByProject[TECH_A];
    await expect
      .poll(
        () =>
          state.pairBodyDiffCompleteLog.filter((d) => d.projectId === TECH_A)
            .length,
        { timeout: 10_000 },
      )
      .toBe(completeABeforeSwitch + 1);
    expect(
      state.pairBodyDiffLog.filter((d) => d.projectId === TECH_B).length,
    ).toBe(pairBArrivedBefore);
    expect(
      state.pairBodyDiffCompleteLog.filter((d) => d.projectId === TECH_B)
        .length,
    ).toBe(pairBCompleteBefore);
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText(SOURCE_LABELS.callback);
  });
});

test.describe("P12E-C 商务标双修订正文差异-共享入口", () => {
  test("P12E-C 商务标：共享 pair 入口精确 1 次 GET、无 query/body、正文不变、零旁路", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    seedRevisions(state, BIZ_A, 2, ["callback", "task"]);
    const rev0 = state.revisions[BIZ_A][0];
    const rev1 = state.revisions[BIZ_A][1];
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        {
          id: "biz_pair",
          title: "商务差异前",
          body: "BIZ_PAIR_BEFORE_BODY",
        },
      ],
    };
    state.details[rev1.revisionId].snapshot = {
      ...state.details[rev1.revisionId].snapshot,
      chapters: [
        {
          id: "biz_pair",
          title: "商务差异后",
          body: "BIZ_PAIR_AFTER_BODY",
        },
      ],
    };
    const expected = buildPairBodyDiffPayload(
      state.details[rev0.revisionId].snapshot,
      state.details[rev1.revisionId].snapshot,
    );
    expect(expected.sameBody).toBe(false);

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.pairBodyDiffLog.length).toBe(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.listLog.length).toBe(0);
    expect(state.pairBodyDiffLog.length).toBe(0);

    const bodyBefore = await readContent(page, "biz");
    expect(bodyBefore).toBe(BIZ_MD);
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const detailBefore = state.detailLog.length;
    const comparisonBefore = state.comparisonLog.length;
    const bodyDiffBefore = state.bodyDiffLog.length;
    const externalBefore = state.externalHits.length;

    // 选择零请求
    await page
      .getByTestId("editor-state-revision-pair-select-before-0")
      .click();
    await page
      .getByTestId("editor-state-revision-pair-select-after-1")
      .click();
    expect(state.pairBodyDiffLog.length).toBe(0);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.bodyDiffLog.length).toBe(bodyDiffBefore);

    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.pairBodyDiffLog[0].beforeRevisionId).toBe(rev0.revisionId);
    expect(state.pairBodyDiffLog[0].afterRevisionId).toBe(rev1.revisionId);
    expect(state.pairBodyDiffLog[0].method).toBe("GET");
    expect(state.pairBodyDiffLog[0].postData).toBeNull();
    expect(state.pairBodyDiffLog[0].hasQuery).toBe(false);
    expect(state.pairBodyDiffLog[0].path).toContain("/body-diff/");

    await expect(
      page.getByTestId("editor-state-revision-pair-status"),
    ).toHaveText(`共 ${expected.changedChapterCount} 章正文有变化`);
    const pairResultText = await page
      .getByTestId("editor-state-revision-pair-result")
      .innerText();
    expect(pairResultText).toMatch(/保留|删除|新增/);
    expect(pairResultText).not.toContain("equal");
    expect(pairResultText).not.toContain("delete");
    expect(pairResultText).not.toContain("insert");

    // 正文不变、零旁路（无 detail/current comparison/单 body-diff/restore/PUT/额外 editor GET/外网）
    expect(await readContent(page, "biz")).toBe(BIZ_MD);
    expect(state.detailLog.length).toBe(detailBefore);
    expect(state.comparisonLog.length).toBe(comparisonBefore);
    expect(state.bodyDiffLog.length).toBe(bodyDiffBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_A).length,
    ).toBe(getsBefore);
    expect(state.externalHits.length).toBe(externalBefore);
    expect(state.pairBodyDiffLog.length).toBe(1);

    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

// ---------------------------------------------------------------------------
// P12F-C 修订历史游标页加载更多
// ---------------------------------------------------------------------------
test.describe("P12F-C 技术标修订历史加载更多", () => {
  test.describe.configure({ mode: "serial" });

  test("P12F-C 技术标：默认折叠零请求；展开精确一次无 cursor 页 GET；旧列表 0；20 条加载后顺序/无重复/按钮消失", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 20);
    expect(seeded).toHaveLength(20);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    // 默认折叠：新旧列表均为 0
    expect(state.listLog.length).toBe(0);
    expect(state.pageLog.length).toBe(0);
    expect(state.detailLog.length).toBe(0);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    // 旧列表精确 0
    expect(state.listLog.length).toBe(0);
    expect(pageHitCountForCursor(state, TECH_A, null)).toBe(1);
    const firstHit = state.pageLog[0];
    expect(firstHit.method).toBe("GET");
    expect(firstHit.postData).toBeNull();
    expect(firstHit.cursor).toBeNull();
    expect(firstHit.queryKeys).toEqual([]);
    expect(firstHit.search).toBe("");
    expect(firstHit.path).toMatch(/\/editor-state-revisions\/page\/?$/);

    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveText("加载更多");

    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    expect(pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND)).toBe(1);
    const secondHit = state.pageLog[1];
    expect(secondHit.method).toBe("GET");
    expect(secondHit.postData).toBeNull();
    expect(secondHit.cursor).toBe(PAGE_CURSOR_SECOND);
    expect(secondHit.queryKeys).toEqual(["cursor"]);
    expect(secondHit.search).toBe(
      `?cursor=${encodeURIComponent(PAGE_CURSOR_SECOND)}`,
    );
    // 禁止客户端分页/搜索旁路参数
    for (const banned of [
      "limit",
      "offset",
      "page",
      "total",
      "hasMore",
      "source",
      "search",
      "q",
    ]) {
      expect(secondHit.queryKeys).not.toContain(banned);
    }
    // 旧列表仍为 0
    expect(state.listLog.length).toBe(0);

    for (let i = 0; i < 20; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-20")).toHaveCount(
      0,
    );
    // 按钮消失
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);
    // 顺序与来源标签不因追加改变前 10 条
    for (let i = 0; i < 20; i++) {
      const label = SOURCE_LABELS[seeded[i].sourceKind];
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText(label);
    }

    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("P12F-C 技术标：第二页摘要与跨页 pair；HTTP/shape/额外键/超10/坏cursor/页内重复/跨页重复/超20 保值重试；双击单飞；load-more 期间刷新禁用", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seeded = seedRevisions(state, TECH_A, 20);
    // 第二页项注入可区分章节，供摘要/pair
    const rev10 = seeded[10];
    const rev0 = seeded[0];
    state.details[rev10.revisionId].snapshot = {
      ...state.details[rev10.revisionId].snapshot,
      chapters: [
        {
          id: "p12fc_ch",
          title: "第二页章节",
          body: "P12FC_PAGE2_BODY",
        },
      ],
      outline: [{ id: "o1", title: "第二页大纲", children: [] }],
      facts: [{ id: "f1", text: "第二页事实" }],
    };
    state.details[rev0.revisionId].snapshot = {
      ...state.details[rev0.revisionId].snapshot,
      chapters: [
        {
          id: "p12fc_ch0",
          title: "第一页章节",
          body: "P12FC_PAGE1_BODY",
        },
      ],
    };

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    // 加载第二页
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();

    // 第二页按需摘要
    await page.getByTestId("editor-state-revision-summary-10").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-10"),
    ).toContainText("章节 1");
    await expect(
      page.getByTestId("editor-state-revision-summary-body-10"),
    ).toContainText("大纲节点 1");
    // 正文不泄漏
    const summaryText = await page
      .getByTestId("editor-state-revision-summary-body-10")
      .innerText();
    expect(summaryText).not.toContain("P12FC_PAGE2_BODY");
    expect(summaryText).not.toContain(rev10.revisionId);

    // 跨页 pair：第 0 条与第 10 条
    await page.getByTestId("editor-state-revision-pair-select-before-0").click();
    await page.getByTestId("editor-state-revision-pair-select-after-10").click();
    await page.getByTestId("editor-state-revision-pair-compare").click();
    await expect
      .poll(() => state.pairBodyDiffCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toBeVisible();
    expect(state.pairBodyDiffLog[0].beforeRevisionId).toBe(rev0.revisionId);
    expect(state.pairBodyDiffLog[0].afterRevisionId).toBe(rev10.revisionId);

    // ---- 失败保值：HTTP 500 ----
    // 重新展开以拿到 nextCursor（当前 20 条后无按钮）；用 11 条场景重测失败路径
    // 折叠后用 override 构造 10 条 + cursor，再失败
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);

    // 重置为 11 条以便有 load-more
    seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // HTTP 失败
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "http_error",
      status: 500,
    };
    const pageBeforeHttp = pageHitCount(state, TECH_A);
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageHitCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageBeforeHttp + 1);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageBeforeHttp + 1);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    // 原 10 条保留
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    // 同 cursor 可重试
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = { kind: "ok" };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND), {
        timeout: 10_000,
      })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);

    // shape 非法：折叠重开，用 override
    await page.getByTestId("editor-state-revision-toggle").click();
    seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: state.revisions[TECH_A].slice(10, 11),
      // 缺 nextCursor
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 额外键
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: state.revisions[TECH_A].slice(10, 11),
      nextCursor: null,
      total: 11,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND), {
        timeout: 10_000,
      })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 超 10
    const eleven = seedRevisions(state, TECH_A, 12).slice(0, 11);
    // re-seed 会重置列表；重新展开
    await page.getByTestId("editor-state-revision-toggle").click();
    seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    delete state.pageResponseByCursor[PAGE_CURSOR_SECOND];
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: eleven,
      nextCursor: null,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 坏 cursor 外壳：首屏返回非法 nextCursor → 前端 parser 应整页失败
    await page.getByTestId("editor-state-revision-toggle").click();
    seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    delete state.pageResponseByCursor[PAGE_CURSOR_SECOND];
    state.pageResponseOverride = {
      items: state.revisions[TECH_A].slice(0, 10),
      nextCursor: PAGE_CURSOR_BAD_SHAPE,
    };
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    // 非法 nextCursor → 首屏固定失败
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_LIST_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    state.pageResponseOverride = null;

    // 页内重复 ID
    await page.getByTestId("editor-state-revision-toggle").click();
    const seeded11 = seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    const dupMeta = { ...seeded11[10] };
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: [dupMeta, { ...dupMeta }],
      nextCursor: null,
    };
    // 非空 nextCursor 要求恰好 10 条——此处 nextCursor null 允许 <10，但页内 ID 重复应失败
    // 实际上 2 条重复：parser 应拒
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 跨页重复：第二页返回与第一页相同的 ID
    delete state.pageResponseByCursor[PAGE_CURSOR_SECOND];
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: [seeded11[0]],
      nextCursor: null,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND), {
        timeout: 10_000,
      })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 超 20：先成功加载到 20，再伪造第三页 cursor
    await page.getByTestId("editor-state-revision-toggle").click();
    seedRevisions(state, TECH_A, 20);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    delete state.pageResponseByCursor[PAGE_CURSOR_SECOND];
    // 第二页返回 10 条且仍带 nextCursor → 前端应视作超限/第三页游标失败
    state.pageResponseByCursor[PAGE_CURSOR_SECOND] = {
      items: state.revisions[TECH_A].slice(10, 20),
      nextCursor: "esrc1_dGhpcmRwYWdlY3Vyc29yYmFk",
    };
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    // 20 + 非空 nextCursor 固定失败，保留原 10
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 双击单飞：同一浏览器 JS 任务内连续 DOM click() 两次，证明同步 ref 门拦截
    await page.getByTestId("editor-state-revision-toggle").click();
    seedRevisions(state, TECH_A, 11);
    state.pageLog.length = 0;
    state.pageCompleteLog.length = 0;
    delete state.pageResponseByCursor[PAGE_CURSOR_SECOND];
    const loadGate = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "hold",
      gate: loadGate,
    };
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);
    const loadBtn = page.getByTestId("editor-state-revision-load-more");
    await expect(loadBtn).toBeEnabled();
    await expect(loadBtn).toHaveText("加载更多");
    // 禁止 force:true；必须真实触发两次 DOM click，且在同一 JS 任务内
    await loadBtn.evaluate((el: HTMLElement) => {
      el.click();
      el.click();
    });
    await loadGate.waitUntilEntered(1);
    // gate 到达后该 cursor 请求精确为 1（第二击被同步 ref 拦截）
    expect(pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND)).toBe(1);
    // 在途文案
    await expect(loadBtn).toHaveText("加载更多…");
    await expect(loadBtn).toBeDisabled();
    // 刷新在途禁用
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    // 恢复禁用
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();
    loadGate.release();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = { kind: "ok" };
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(2);
    // 释放后仍不得追加第二请求
    expect(pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND)).toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });

  test("P12F-C 技术标：load-more arrived+complete 迟到隔离（折叠/刷新/项目切换）；旧 finally 不污染", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(state, TECH_A, 11);
    seedRevisions(state, TECH_B, 11, ["task"]);
    const loadGate = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "hold",
      gate: loadGate,
    };
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);

    // 发起 load-more 并挂起
    await page.getByTestId("editor-state-revision-load-more").click();
    await loadGate.waitUntilEntered(1);
    expect(pageHitCountForCursor(state, TECH_A, PAGE_CURSOR_SECOND)).toBe(1);
    expect(
      state.pageCompleteLog.filter(
        (h) => h.projectId === TECH_A && h.cursor === PAGE_CURSOR_SECOND,
      ).length,
    ).toBe(0);
    // 在途：刷新/恢复真实 disabled（不得 force:true）
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveText("加载更多…");

    // 折叠作废
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    loadGate.release();
    // 必须等迟到 complete
    await expect
      .poll(
        () =>
          state.pageCompleteLog.filter(
            (h) => h.projectId === TECH_A && h.cursor === PAGE_CURSOR_SECOND,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);

    // 刷新作废：先正常展开，再挂起 load-more，断言刷新 disabled；
    // 然后折叠作废；重新展开后点刷新，确认只发首屏且无第二页污染。
    const loadGate2 = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "hold",
      gate: loadGate2,
    };
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, null), {
        timeout: 10_000,
      })
      .toBe(2);
    await page.getByTestId("editor-state-revision-load-more").click();
    await loadGate2.waitUntilEntered(1);
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();
    // 折叠作废在途 load-more
    await page.getByTestId("editor-state-revision-toggle").click();
    loadGate2.release();
    await expect
      .poll(
        () =>
          state.pageCompleteLog.filter(
            (h) => h.projectId === TECH_A && h.cursor === PAGE_CURSOR_SECOND,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(2);
    // 重新展开后点刷新：精确一次首屏，旧列表 0
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = { kind: "ok" };
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, null), {
        timeout: 10_000,
      })
      .toBe(3);
    const firstBeforeRefresh = pageHitCountForCursor(state, TECH_A, null);
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_A, null), {
        timeout: 10_000,
      })
      .toBe(firstBeforeRefresh + 1);
    const refreshHit = state.pageLog[state.pageLog.length - 1];
    expect(refreshHit.cursor).toBeNull();
    expect(refreshHit.queryKeys).toEqual([]);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);

    // 项目切换迟到
    const loadGate3 = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "hold",
      gate: loadGate3,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await loadGate3.waitUntilEntered(1);
    // 切到 B
    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForCursor(state, TECH_B, null), {
        timeout: 10_000,
      })
      .toBe(1);
    loadGate3.release();
    await expect
      .poll(
        () =>
          state.pageCompleteLog.filter(
            (h) => h.projectId === TECH_A && h.cursor === PAGE_CURSOR_SECOND,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(3);
    // B 仅 10 条，不被 A 的第二页污染
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);
    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

test.describe("P12F-C 商务标修订历史加载更多", () => {
  test.describe.configure({ mode: "serial" });

  test("P12F-C 商务标：第二页恢复执行时 expected、唯一 editor-state GET、成功只重载第一页；恢复后迟到 load-more 隔离", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const seeded = seedRevisions(state, BIZ_A, 11, [
      "callback",
      "task",
      "revise",
      "browser_put",
      "local_parser",
      "content_fuse_apply",
      "content_fuse_consume",
      "checkpoint_restore",
      "revision_restore",
      "browser_put",
      "task",
    ]);
    const page2Meta = seeded[10];
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.listLog.length).toBe(0);
    expect(state.pageLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), { timeout: 10_000 })
      .toBe(1);
    expect(state.listLog.length).toBe(0);

    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), { timeout: 10_000 })
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();

    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const pageBeforeRestore = pageHitCount(state, BIZ_A);
    // 执行时 expected：确认前捕获当前 editor 版本（restore 成功后服务端版本会变）
    const expectedAtConfirm = state.projects[BIZ_A].stateVersion;

    await page.getByTestId("editor-state-revision-restore-10").click();
    expect(state.restoreLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-10"),
    ).toContainText(RESTORE_CONFIRM);
    await page.getByTestId("editor-state-revision-confirm-restore-10").click();

    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog[0].revisionId).toBe(page2Meta.revisionId);
    expect(state.restoreLog[0].body).toEqual({
      expectedStateVersion: expectedAtConfirm,
    });
    expect(Object.keys(state.restoreLog[0].body).sort()).toEqual([
      "expectedStateVersion",
    ]);
    // 不得误用修订自身 stateVersion 作为 expected
    expect(expectedAtConfirm).not.toBe(page2Meta.stateVersion);

    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);

    // 成功后只重载第一页
    await expect
      .poll(() => pageHitCount(state, BIZ_A) - pageBeforeRestore, {
        timeout: 10_000,
      })
      .toBe(1);
    const reloadHit = state.pageLog[state.pageLog.length - 1];
    expect(reloadHit.cursor).toBeNull();
    expect(reloadHit.queryKeys).toEqual([]);
    // 旧列表仍 0
    expect(state.listLog.length).toBe(0);
    // UI 回到第一页（最多 10 条）；有 nextCursor 时按钮仍在
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect
      .poll(async () => readContent(page, "biz"), { timeout: 10_000 })
      .toBe(RESTORED_BIZ);

    // 恢复后仍可加载更多（探针保留上限 20，首屏有 nextCursor）
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 迟到隔离：load-more 挂起期间恢复/刷新真实 disabled；折叠作废后再释放
    const lateGate = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_SECOND] = {
      kind: "hold",
      gate: lateGate,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await lateGate.waitUntilEntered(1);
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();
    const completeSecondBefore = state.pageCompleteLog.filter(
      (h) => h.projectId === BIZ_A && h.cursor === PAGE_CURSOR_SECOND,
    ).length;

    // 折叠作废在途 load-more；再展开并成功恢复第 0 条，只重载第一页
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    lateGate.release();
    await expect
      .poll(
        () =>
          state.pageCompleteLog.filter(
            (h) => h.projectId === BIZ_A && h.cursor === PAGE_CURSOR_SECOND,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(completeSecondBefore + 1);

    state.pageModeByCursor[PAGE_CURSOR_SECOND] = { kind: "ok" };
    // 重开前捕获首屏 arrived/complete 基线，重开后精确 +1（禁止宽泛 >=）
    const firstPageHitBeforeReopen = pageHitCountForCursor(state, BIZ_A, null);
    const firstPageCompleteBeforeReopen = state.pageCompleteLog.filter(
      (h) => h.projectId === BIZ_A && h.cursor === null,
    ).length;
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForCursor(state, BIZ_A, null), {
        timeout: 10_000,
      })
      .toBe(firstPageHitBeforeReopen + 1);
    await expect
      .poll(
        () =>
          state.pageCompleteLog.filter(
            (h) => h.projectId === BIZ_A && h.cursor === null,
          ).length,
        { timeout: 10_000 },
      )
      .toBe(firstPageCompleteBeforeReopen + 1);
    // 折叠后迟到结果不得把第二页渲染出来
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);

    // 再次恢复：成功后仍只重载第一页
    const pageBeforeSecondRestore = pageHitCount(state, BIZ_A);
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(2);
    await expect
      .poll(() => pageHitCount(state, BIZ_A) - pageBeforeSecondRestore, {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.pageLog[state.pageLog.length - 1].cursor).toBeNull();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

// ---------------------------------------------------------------------------
// P12F-D 修订历史来源筛选
// ---------------------------------------------------------------------------

/** 九类固定中文标签顺序（与 REVISION_SOURCE_LABELS 一致） */
const NINE_SOURCE_LABELS_ORDERED = NINE_SOURCES.map((k) => SOURCE_LABELS[k]);

test.describe("P12F-D 技术标修订历史来源筛选", () => {
  test.describe.configure({ mode: "serial" });

  test("P12F-D 技术标：筛选器九选项；默认无 query；选来源仅 sourceKind；同值不重发；第二页 esrc2；空态；失败不回退；在途禁用", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    // 混排：11 条 task + 3 条 revise，保证筛选分页与空态
    const taskSources = Array.from({ length: 11 }, () => "task");
    const reviseSources = Array.from({ length: 3 }, () => "revise");
    const seeded = seedRevisions(
      state,
      TECH_A,
      14,
      taskSources.concat(reviseSources),
    );
    state.pageSecondCursorBySource["task"] = PAGE_CURSOR_FILTER_SECOND;

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.pageLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-source-filter"),
    ).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    // 默认首次展开精确无 query
    expect(pageHitCountForSource(state, TECH_A, null)).toBe(1);
    const firstHit = state.pageLog[0];
    expect(firstHit.method).toBe("GET");
    expect(firstHit.postData).toBeNull();
    expect(firstHit.cursor).toBeNull();
    expect(firstHit.sourceKind).toBeNull();
    expect(firstHit.queryKeys).toEqual([]);
    expect(firstHit.search).toBe("");

    // 筛选器可见：默认“全部来源”+ 九中文选项
    const filter = page.getByTestId("editor-state-revision-source-filter");
    await expect(filter).toBeVisible();
    await expect(filter).toBeEnabled();
    const optionTexts = await filter.locator("option").allTextContents();
    expect(optionTexts[0]).toBe("全部来源");
    expect(optionTexts.slice(1)).toEqual(NINE_SOURCE_LABELS_ORDERED);
    // 内部值不得作为可见文案泄漏到 option 文本（仅中文标签）
    for (const sk of NINE_SOURCES) {
      expect(optionTexts.join("\n")).not.toContain(sk);
    }
    await expect(filter).toHaveValue("");

    // 选择「任务写入」= task：精确一次仅 sourceKind
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "task"), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(1);
    const filterHit = state.pageLog[state.pageLog.length - 1];
    expect(filterHit.method).toBe("GET");
    expect(filterHit.postData).toBeNull();
    expect(filterHit.cursor).toBeNull();
    expect(filterHit.sourceKind).toBe("task");
    expect(filterHit.queryKeys).toEqual(["sourceKind"]);
    expect(filterHit.search).toBe("?sourceKind=task");
    for (const banned of [
      "limit",
      "offset",
      "page",
      "total",
      "hasMore",
      "source",
      "search",
      "q",
      "cursor",
    ]) {
      expect(filterHit.queryKeys).not.toContain(banned);
    }
    // 仅 task 来源可见（11 条 → 首屏 10 + load-more）
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText("任务写入");
    }
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 同值不重发：再次选同一选项不得 +1
    const beforeSame = pageHitCountForSource(state, TECH_A, "task");
    await filter.selectOption({ label: "任务写入" });
    // 同值不触发请求：直接断言计数不变（禁止固定 sleep 冒充）
    expect(pageHitCountForSource(state, TECH_A, "task")).toBe(beforeSame);

    // 第二页成功前：PAGE_CURSOR_FILTER_SECOND 精确 HTTP 500
    state.pageModeByCursor[PAGE_CURSOR_FILTER_SECOND] = {
      kind: "http_error",
      status: 500,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageHitCountForSourceCursor(
            state,
            TECH_A,
            "task",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(
            state,
            TECH_A,
            "task",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const failHit = state.pageLog[state.pageLog.length - 1];
    expect(failHit.sourceKind).toBe("task");
    expect(failHit.cursor).toBe(PAGE_CURSOR_FILTER_SECOND);
    expect(failHit.queryKeys.sort()).toEqual(
      ["cursor", "sourceKind"].sort(),
    );
    expect(failHit.search).toContain("sourceKind=task");
    expect(failHit.search).toContain(
      `cursor=${encodeURIComponent(PAGE_CURSOR_FILTER_SECOND)}`,
    );
    // 筛选前 10 条、原中文来源与 load-more 按钮仍保留；第 11 条不存在
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText("任务写入");
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    await expect(filter).toHaveValue("task");
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);

    // 同 cursor/sourceKind 可点击重试：失败与成功各精确一次（同 cursor 总计 2）
    state.pageModeByCursor[PAGE_CURSOR_FILTER_SECOND] = { kind: "ok" };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageHitCountForSourceCursor(
            state,
            TECH_A,
            "task",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(
            state,
            TECH_A,
            "task",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(2);
    const secondHit = state.pageLog[state.pageLog.length - 1];
    expect(secondHit.sourceKind).toBe("task");
    expect(secondHit.cursor).toBe(PAGE_CURSOR_FILTER_SECOND);
    expect(secondHit.queryKeys.sort()).toEqual(
      ["cursor", "sourceKind"].sort(),
    );
    expect(secondHit.search).toContain("sourceKind=task");
    expect(secondHit.search).toContain(
      `cursor=${encodeURIComponent(PAGE_CURSOR_FILTER_SECOND)}`,
    );
    // 成功后第 11 条出现且错误消失；不得自动换 cursor/清空筛选
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-source-10"),
    ).toHaveText("任务写入");
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);
    await expect(filter).toHaveValue("task");
    // 同 cursor 总计精确 2（失败 1 + 成功 1），无自动换 cursor
    expect(
      pageHitCountForSourceCursor(
        state,
        TECH_A,
        "task",
        PAGE_CURSOR_FILTER_SECOND,
      ),
    ).toBe(2);
    expect(
      state.pageLog.filter(
        (h) =>
          h.projectId === TECH_A &&
          h.sourceKind === "task" &&
          h.cursor !== null &&
          h.cursor !== PAGE_CURSOR_FILTER_SECOND,
      ).length,
    ).toBe(0);

    // 空态：选 callback（无数据）
    await filter.selectOption({ label: "解析回传" });
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-empty")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);

    // 失败：HTTP 500，显示列表失败 + 空态，不回退旧 task 列表；保留筛选供刷新
    state.pageModeByProject[TECH_A] = { kind: "http_error", status: 500 };
    await filter.selectOption({ label: "智能修订" });
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "revise"), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "revise", null),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_LIST_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    // 保留所选来源
    await expect(filter).toHaveValue("revise");
    // 刷新重试仍带 revise
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "revise"), {
        timeout: 10_000,
      })
      .toBe(2);
    const refreshHit = state.pageLog[state.pageLog.length - 1];
    expect(refreshHit.sourceKind).toBe("revise");
    expect(refreshHit.cursor).toBeNull();
    expect(refreshHit.queryKeys).toEqual(["sourceKind"]);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    for (let i = 0; i < 3; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText("智能修订");
    }

    // 在途禁用 selector：挂起列表请求
    const holdGate = createHoldGate();
    state.pageModeByProject[TECH_A] = { kind: "hold", gate: holdGate };
    await filter.selectOption({ label: "任务写入" });
    await holdGate.waitUntilEntered(1);
    await expect(filter).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    holdGate.release();
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(filter).toBeEnabled();

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    // 零持久化：URL / localStorage / sessionStorage / document.cookie / console
    const persist = await page.evaluate(() => {
      const ls: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k) ls.push(`${k}=${localStorage.getItem(k)}`);
      }
      const ss: string[] = [];
      for (let i = 0; i < sessionStorage.length; i++) {
        const k = sessionStorage.key(i);
        if (k) ss.push(`${k}=${sessionStorage.getItem(k)}`);
      }
      return {
        ls: ls.join("\n"),
        ss: ss.join("\n"),
        href: location.href,
        cookie: document.cookie,
      };
    });
    const storageBlob = `${persist.ls}\n${persist.ss}`;
    const cookieBlob = persist.cookie ?? "";
    // URL / localStorage / sessionStorage：筛选键与游标/正文不得落盘
    for (const blob of [storageBlob, persist.href]) {
      expect(blob).not.toContain("sourceKind");
      expect(blob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
      expect(blob).not.toContain("esrc2_");
      expect(blob).not.toMatch(/esr_[0-9a-f]{32}/);
      expect(blob).not.toMatch(/esv_[0-9a-f]{32}/);
      expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    // document.cookie：精确不含 sourceKind、九来源字面量、esrc2_、revision/version/正文
    expect(cookieBlob).not.toContain("sourceKind");
    expect(cookieBlob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
    expect(cookieBlob).not.toContain("esrc2_");
    expect(cookieBlob).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(cookieBlob).not.toMatch(/esr_[0-9a-f]{32}/);
    expect(cookieBlob).not.toMatch(/esv_[0-9a-f]{32}/);
    for (const sk of NINE_SOURCES) {
      expect(cookieBlob).not.toContain(sk);
    }
    for (const item of seeded) {
      expect(cookieBlob).not.toContain(item.revisionId);
      expect(cookieBlob).not.toContain(item.stateVersion);
      expect(storageBlob).not.toContain(item.revisionId);
      expect(storageBlob).not.toContain(item.stateVersion);
    }
    // URL 不带筛选
    expect(page.url()).not.toContain("sourceKind");
    expect(page.url()).not.toContain("esrc2_");
    // console 保留游标/正文零泄漏（assertNoIdLeak 已检 revisionId/version）
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain("sourceKind");
    expect(consoleBlob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
    expect(consoleBlob).not.toContain("esrc2_");
    expect(consoleBlob).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(seeded.length).toBe(14);
  });

  test("P12F-D 技术标：切换清意图；折叠保留；项目切换重置；迟到 arrived+complete 隔离；零旁路", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(
      state,
      TECH_A,
      12,
      Array.from({ length: 11 }, () => "task").concat(["revise"]),
    );
    seedRevisions(
      state,
      TECH_B,
      5,
      Array.from({ length: 5 }, () => "callback"),
    );
    state.pageSecondCursorBySource["task"] = PAGE_CURSOR_FILTER_SECOND;

    const firstTask = state.revisions[TECH_A][0];
    state.details[firstTask.revisionId].snapshot = {
      ...state.details[firstTask.revisionId].snapshot,
      chapters: [{ id: "ch", title: "筛选章节", body: "FILTER_BODY_LEAK" }],
    };

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);

    // 展开摘要 + 选择 pair + 恢复确认，切换筛选应清空
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-pair-select-before-0").click();
    await page.getByTestId("editor-state-revision-pair-select-after-1").click();
    await page.getByTestId("editor-state-revision-restore-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toBeVisible();

    const filter = page.getByTestId("editor-state-revision-source-filter");
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(1);
    // 旧意图清空
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 折叠再展开：保留筛选（内存），再次请求带 sourceKind
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    const taskHitsBefore = pageHitCountForSource(state, TECH_A, "task");
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "task"), {
        timeout: 10_000,
      })
      .toBe(taskHitsBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-source-filter"),
    ).toHaveValue("task");
    const reopenHit = state.pageLog[state.pageLog.length - 1];
    expect(reopenHit.sourceKind).toBe("task");
    expect(reopenHit.cursor).toBeNull();
    expect(reopenHit.queryKeys).toEqual(["sourceKind"]);

    // 迟到隔离（首屏）：挂起 task 刷新；在途 selector 禁用；切 revise 前须先完成/作废
    // 路径：挂起 → 折叠作废 → 释放 complete → 再展开选 revise，证明旧 success 不污染
    const lateGate = createHoldGate();
    state.pageModeByProject[TECH_A] = { kind: "hold", gate: lateGate };
    await page.getByTestId("editor-state-revision-refresh").click();
    await lateGate.waitUntilEntered(1);
    const taskArrivedWhileHeld = pageHitCountForSourceCursor(
      state,
      TECH_A,
      "task",
      null,
    );
    const taskCompleteWhileHeld = pageCompleteCountForSourceCursor(
      state,
      TECH_A,
      "task",
      null,
    );
    // 在途：筛选器真实 disabled
    await expect(filter).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    // 折叠作废在途首屏
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    lateGate.release();
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(taskCompleteWhileHeld + 1);
    // 折叠态不得被迟到 success 打开
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    expect(taskArrivedWhileHeld).toBe(
      pageHitCountForSourceCursor(state, TECH_A, "task", null),
    );

    // 再展开（仍保留 task 筛选）后切换到 revise：精确新 sourceKind，清空旧列表意图
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    const taskFirstBeforeReopen2 = pageHitCountForSourceCursor(
      state,
      TECH_A,
      "task",
      null,
    );
    await expandRevisionPanel(page);
    await expect
      .poll(
        () => pageHitCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(taskFirstBeforeReopen2 + 1);
    await expect(filter).toHaveValue("task");
    await expect(filter).toBeEnabled();
    const reviseCompleteBefore = pageCompleteCountForSourceCursor(
      state,
      TECH_A,
      "revise",
      null,
    );
    await filter.selectOption({ label: "智能修订" });
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "revise", null),
        { timeout: 10_000 },
      )
      .toBe(reviseCompleteBefore + 1);
    await expect(filter).toHaveValue("revise");
    await expect(
      page.getByTestId("editor-state-revision-source-0"),
    ).toHaveText("智能修订");
    await expect(page.getByTestId("editor-state-revision-item-1")).toHaveCount(
      0,
    );

    // load-more 迟到：选回 task 有第二页；在途 selector 真实 disabled；折叠作废后释放不污染
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    const taskCompleteBefore = pageCompleteCountForSourceCursor(
      state,
      TECH_A,
      "task",
      null,
    );
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(
        () => pageCompleteCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(taskCompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    const loadGate = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_FILTER_SECOND] = {
      kind: "hold",
      gate: loadGate,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await loadGate.waitUntilEntered(1);
    // load-more 在途：筛选器/刷新/恢复真实 disabled（不得 force:true）
    await expect(filter).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeDisabled();
    // 折叠作废在途 load-more
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    loadGate.release();
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(
            state,
            TECH_A,
            "task",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    // 折叠后不得渲染第二页或错误
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);
    // 再展开：保留 task 筛选，精确一次首屏，无第二页污染
    state.pageModeByCursor[PAGE_CURSOR_FILTER_SECOND] = { kind: "ok" };
    const taskFirstBeforeReopen = pageHitCountForSourceCursor(
      state,
      TECH_A,
      "task",
      null,
    );
    await expandRevisionPanel(page);
    await expect
      .poll(
        () => pageHitCountForSourceCursor(state, TECH_A, "task", null),
        { timeout: 10_000 },
      )
      .toBe(taskFirstBeforeReopen + 1);
    await expect(filter).toHaveValue("task");
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    // 项目切换：重置全部来源
    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForSource(state, TECH_B, null), {
        timeout: 10_000,
      })
      .toBe(1);
    const bFilter = page.getByTestId("editor-state-revision-source-filter");
    await expect(bFilter).toHaveValue("");
    const bHit = state.pageLog[state.pageLog.length - 1];
    expect(bHit.projectId).toBe(TECH_B);
    expect(bHit.sourceKind).toBeNull();
    expect(bHit.queryKeys).toEqual([]);

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    // 零持久化：document.cookie + URL/storage/console
    const persist2 = await page.evaluate(() => ({
      href: location.href,
      cookie: document.cookie,
      ls: (() => {
        const out: string[] = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k) out.push(`${k}=${localStorage.getItem(k)}`);
        }
        return out.join("\n");
      })(),
      ss: (() => {
        const out: string[] = [];
        for (let i = 0; i < sessionStorage.length; i++) {
          const k = sessionStorage.key(i);
          if (k) out.push(`${k}=${sessionStorage.getItem(k)}`);
        }
        return out.join("\n");
      })(),
    }));
    const cookie2 = persist2.cookie ?? "";
    for (const blob of [persist2.href, persist2.ls, persist2.ss]) {
      expect(blob).not.toContain("sourceKind");
      expect(blob).not.toContain("esrc2_");
      expect(blob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
      expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    expect(cookie2).not.toContain("sourceKind");
    expect(cookie2).not.toContain("esrc2_");
    expect(cookie2).not.toContain(PAGE_CURSOR_FILTER_SECOND);
    expect(cookie2).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(cookie2).not.toMatch(/esr_[0-9a-f]{32}/);
    expect(cookie2).not.toMatch(/esv_[0-9a-f]{32}/);
    for (const sk of NINE_SOURCES) {
      expect(cookie2).not.toContain(sk);
    }
    const console2 = guards.consoleLogs.join("\n");
    expect(console2).not.toContain("sourceKind");
    expect(console2).not.toContain("esrc2_");
    expect(console2).not.toContain(PAGE_CURSOR_FILTER_SECOND);
  });
});

test.describe("P12F-D 商务标修订历史来源筛选", () => {
  test.describe.configure({ mode: "serial" });

  test("P12F-D 商务标：共享筛选入口；刷新/恢复保留筛选；零额外 API/外网/泄漏", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const seeded = seedRevisions(
      state,
      BIZ_A,
      11,
      Array.from({ length: 11 }, () => "callback"),
    );
    state.pageSecondCursorBySource["callback"] = PAGE_CURSOR_FILTER_SECOND;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.pageLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, BIZ_A)).toBe(1);
    expect(state.pageLog[0].sourceKind).toBeNull();
    expect(state.pageLog[0].queryKeys).toEqual([]);

    const filter = page.getByTestId("editor-state-revision-source-filter");
    await expect(filter).toBeVisible();
    await filter.selectOption({ label: "解析回传" });
    await expect
      .poll(() => pageHitCountForSource(state, BIZ_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(1);
    const selHit = state.pageLog[state.pageLog.length - 1];
    expect(selHit.sourceKind).toBe("callback");
    expect(selHit.queryKeys).toEqual(["sourceKind"]);
    expect(selHit.search).toBe("?sourceKind=callback");

    // 刷新保留筛选
    const beforeRefresh = pageHitCountForSource(state, BIZ_A, "callback");
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageHitCountForSource(state, BIZ_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(beforeRefresh + 1);
    expect(state.pageLog[state.pageLog.length - 1].sourceKind).toBe("callback");
    await expect(filter).toHaveValue("callback");

    // 加载第二页后恢复：gate 在途禁用筛选/刷新/恢复；释放后唯一 restore + 唯一 editor-state GET + 只重载筛选第一页
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(
            state,
            BIZ_A,
            "callback",
            PAGE_CURSOR_FILTER_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();

    const restoreGate = createHoldGate();
    state.restoreMode = { kind: "gate", gate: restoreGate, then: "ok" };
    const pageBeforeRestore = pageHitCount(state, BIZ_A);
    const pageFirstBeforeRestore = pageHitCountForSourceCursor(
      state,
      BIZ_A,
      "callback",
      null,
    );
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const expectedAtConfirm = state.projects[BIZ_A].stateVersion;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    // POST 到达（gate entered）后：来源筛选器、刷新、恢复相关交互真实在途禁用（不得 force:true）
    await restoreGate.waitUntilEntered(1);
    expect(state.restoreLog.length).toBe(0);
    await expect(filter).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    // 确认态在途：确认/取消真实 disabled（restore 主按钮此时由 confirm 区替代，不得 force:true）
    await expect(
      page.getByTestId("editor-state-revision-confirm-restore-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-cancel-restore-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-confirm-restore-0"),
    ).toHaveText("恢复中…");
    // 在途不得提前重载 page / editor-state
    expect(pageHitCount(state, BIZ_A)).toBe(pageBeforeRestore);
    expect(
      state.editorGetLog.filter((g) => g.projectId === BIZ_A).length,
    ).toBe(getsBefore);

    restoreGate.release();
    state.restoreMode = { kind: "ok" };
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog.length).toBe(1);
    expect(state.restoreLog[0].body).toEqual({
      expectedStateVersion: expectedAtConfirm,
    });
    // 唯一 editor-state GET
    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);
    // 只重载筛选第一页（callback + null cursor 精确 +1；总 page +1）
    await expect
      .poll(() => pageHitCount(state, BIZ_A) - pageBeforeRestore, {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(
        () =>
          pageHitCountForSourceCursor(state, BIZ_A, "callback", null) -
          pageFirstBeforeRestore,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(state, BIZ_A, "callback", null),
        { timeout: 10_000 },
      )
      .toBe(pageFirstBeforeRestore + 1);
    const reloadHit = state.pageLog[state.pageLog.length - 1];
    expect(reloadHit.sourceKind).toBe("callback");
    expect(reloadHit.cursor).toBeNull();
    expect(reloadHit.queryKeys).toEqual(["sourceKind"]);
    expect(reloadHit.search).toBe("?sourceKind=callback");
    await expect(filter).toHaveValue("callback");
    await expect(filter).toBeEnabled();
    // 恢复后仍最多首屏
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    // 零持久化：document.cookie + URL/storage/console
    const persistBiz = await page.evaluate(() => ({
      href: location.href,
      cookie: document.cookie,
      ls: (() => {
        const out: string[] = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k) out.push(`${k}=${localStorage.getItem(k)}`);
        }
        return out.join("\n");
      })(),
      ss: (() => {
        const out: string[] = [];
        for (let i = 0; i < sessionStorage.length; i++) {
          const k = sessionStorage.key(i);
          if (k) out.push(`${k}=${sessionStorage.getItem(k)}`);
        }
        return out.join("\n");
      })(),
    }));
    const cookieBiz = persistBiz.cookie ?? "";
    for (const blob of [persistBiz.href, persistBiz.ls, persistBiz.ss]) {
      expect(blob).not.toContain("sourceKind");
      expect(blob).not.toContain("esrc2_");
      expect(blob).not.toContain(PAGE_CURSOR_FILTER_SECOND);
      expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    expect(cookieBiz).not.toContain("sourceKind");
    expect(cookieBiz).not.toContain("esrc2_");
    expect(cookieBiz).not.toContain(PAGE_CURSOR_FILTER_SECOND);
    expect(cookieBiz).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(cookieBiz).not.toMatch(/esr_[0-9a-f]{32}/);
    expect(cookieBiz).not.toMatch(/esv_[0-9a-f]{32}/);
    for (const sk of NINE_SOURCES) {
      expect(cookieBiz).not.toContain(sk);
    }
    for (const item of seeded) {
      expect(cookieBiz).not.toContain(item.revisionId);
      expect(cookieBiz).not.toContain(item.stateVersion);
    }
    const consoleBiz = guards.consoleLogs.join("\n");
    expect(consoleBiz).not.toContain("sourceKind");
    expect(consoleBiz).not.toContain("esrc2_");
    expect(consoleBiz).not.toContain(PAGE_CURSOR_FILTER_SECOND);
    expect(consoleBiz).not.toContain(SNAPSHOT_BODY_LEAK);
    expect(seeded.length).toBe(11);
  });
});
