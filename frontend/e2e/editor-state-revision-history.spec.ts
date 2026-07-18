/**
 * 模块：P12C-C3 / P12D-B / P12E-A / P12E-C / P12F-C / P12F-D / P12F-E-B / P12F-F-B / P12F-G-B / P12F-H
 *       双工作区修订历史、对比、正文差异、游标分页、来源与时间范围筛选、可见内容搜索、单条删除、单条命名前端 E2E
 * 用途：技术标/商务标证明默认折叠零请求、按需列表/摘要/对比/正文差异、双修订正文差异、
 *       二次确认 restore、游标页首屏与加载更多、来源筛选、本地时间范围应用/清除、
 *       显式内容搜索 POST、单条删除确认/唯一 DELETE/成功重载/失败保留、
 *       单条命名保存/覆盖/清除/失败保值/互斥迟到/商务共用与数据最小化、
 *       执行时 expected、唯一 editor-state GET、失败阻断、迟到隔离与数据最小化。
 * 对接：Playwright chromium headless workers=1 retries=0；route 探针
 *       （含 comparison/body-diff/pair/page/search/delete/display-name arrived/complete/cursor/sourceKind/createdFrom/createdBefore/query body）。
 * 二次开发：禁止固定 sleep、.or(...)、>=1 冒充、宽泛状态码、route fallback 假成功。
 */
import {
  expect,
  test,
  type Page,
  type Route,
} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

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
/**
 * P12F-E-B 时间范围第二页不透明游标（外壳合法：esrc3_ + base64url 无 =）。
 * 前端不得解码；仅原样回传，且必须与当前时间/来源条件同时出现。
 */
const PAGE_CURSOR_TIME_SECOND = "esrc3_dGltZXBhZ2UyY3Vyc29yZm9ycmV2aGlzdA";
/** V3 外壳总长精确 256（前缀 6 + body 250），供上限验收 */
const PAGE_CURSOR_V3_LEN_256 =
  "esrc3_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".repeat(4).slice(0, 250);
/** V3 超长 257：前端 parser 必须拒绝 */
const PAGE_CURSOR_V3_LEN_257 = PAGE_CURSOR_V3_LEN_256 + "A";
/** 故意非法游标（缺前缀）——仅用于服务端/前端失败路径 */
const PAGE_CURSOR_BAD_SHAPE = "not_a_valid_cursor_value";
/** P12F-E-B 时间范围无效固定中文 */
const MSG_TIME_RANGE_INVALID = "时间范围无效，请检查开始和结束时间";
/** P12F-F-B 搜索关键词校验失败固定中文 */
const MSG_SEARCH_QUERY_INVALID =
  "搜索关键词需为 1 至 64 个字符，且不能含首尾空白或控制字符";
/** P12F-F-B 搜索空结果固定中文 */
const MSG_SEARCH_EMPTY = "未找到匹配修订";
/** P12F-F-B 搜索失败固定中文 */
const MSG_SEARCH_FAIL = "修订内容搜索失败，请稍后重试";
const MSG_RESTORE_OK = "已恢复到所选修订";
const MSG_RESTORE_BLOCKED =
  "当前无法恢复，请先处理版本冲突或重新载入";
const MSG_RESTORE_RELOAD_FAIL =
  "恢复已完成，但刷新失败，请重新载入远端内容";
/** 检查点 create 被共享写令牌拒绝时的固定状态文案 */
const MSG_CHECKPOINT_CREATE_FAIL = "保存检查点失败，请确认后重试";
const RESTORE_CONFIRM =
  "服务器当前内容会先保存为安全检查点，恢复替换技术标和商务标全部编辑态，尚未保存的本地修改不会写入。";
/** P12F-G-B 单条删除确认固定文案 */
const DELETE_CONFIRM =
  "删除后无法恢复。当前编辑内容和检查点不会改变，确定删除这条修订吗？";
/** P12F-G-B 删除成功固定文案 */
const MSG_DELETE_OK = "已删除所选修订";
/** P12F-G-B 删除失败固定文案 */
const MSG_DELETE_FAIL = "删除修订失败，当前列表已保留";
/** P12F-H 命名在途固定文案 */
const MSG_NAME_SAVING = "保存名称中…";
/** P12F-H 命名成功固定文案 */
const MSG_NAME_OK = "修订名称已保存";
/** P12F-H 清除名称成功固定文案 */
const MSG_NAME_CLEARED = "修订名称已清除";
/** P12F-H 命名失败固定文案 */
const MSG_NAME_FAIL = "保存修订名称失败，当前名称已保留";

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
  /** P12F-H 六键；缺省 null */
  displayName: string | null;
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
/** P12F-F-B 搜索模式：正常 / 挂起 / HTTP 错误 */
type SearchMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate }
  | { kind: "http_error"; status: number };
type DetailMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type ComparisonMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type BodyDiffMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
type PairBodyDiffMode = { kind: "ok" } | { kind: "hold"; gate: HoldGate };
/** P12F-G-B 单条删除模式：正常 / 挂起 / HTTP 错误 */
type DeleteMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate }
  | { kind: "http_error"; status: number };

/** P12F-H 单条命名模式：正常 / 挂起后成功 / 挂起后 HTTP 错误 / 即时 HTTP 错误 */
type NameMode =
  | { kind: "ok" }
  | { kind: "hold"; gate: HoldGate; then: "ok" }
  | { kind: "hold"; gate: HoldGate; then: "http_error"; status: number }
  | { kind: "http_error"; status: number };

/** P12F-G-B DELETE 探针到达记录：无 query/body */
type DeleteProbeHit = {
  projectId: string;
  revisionId: string;
  method: string;
  path: string;
  postData: string | null;
  queryKeys: string[];
  search: string;
};

/** P12F-H PATCH display-name 探针到达记录：精确 body 一键 + 实际 CSRF 头 */
type NameProbeHit = {
  projectId: string;
  revisionId: string;
  method: string;
  path: string;
  postData: string | null;
  queryKeys: string[];
  search: string;
  bodyKeys: string[];
  displayName: string | null | undefined;
  /** 小写请求头 x-csrf-token 的实际值；缺失为 null */
  csrfToken: string | null;
};

/** page 探针到达记录：含 cursor/sourceKind/时间与查询键，供精确零旁路断言 */
type PageProbeHit = {
  projectId: string;
  cursor: string | null;
  /** 缺省 null 表示无 sourceKind query */
  sourceKind: string | null;
  /** 缺省 null 表示无 createdFrom query */
  createdFrom: string | null;
  /** 缺省 null 表示无 createdBefore query */
  createdBefore: string | null;
  method: string;
  path: string;
  postData: string | null;
  queryKeys: string[];
  search: string;
};

/** P12F-F-B search 探针到达记录：无 URL query，body 精确键顺序 */
type SearchProbeHit = {
  projectId: string;
  query: string | null;
  sourceKind: string | null;
  createdFrom: string | null;
  createdBefore: string | null;
  method: string;
  path: string;
  postData: string | null;
  queryKeys: string[];
  search: string;
  bodyKeys: string[];
  body: Record<string, unknown> | null;
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
  /** P12F-C/D/E-B page 到达（gate 前），含 cursor/sourceKind/时间 */
  pageLog: PageProbeHit[];
  /** P12F-C/D/E-B page 响应已 fulfill（await json 返回后） */
  pageCompleteLog: Array<{
    projectId: string;
    cursor: string | null;
    sourceKind: string | null;
    createdFrom: string | null;
    createdBefore: string | null;
  }>;
  /** P12F-F-B search 到达（gate 前），含 body 解析字段 */
  searchLog: SearchProbeHit[];
  /** P12F-F-B search 响应已 fulfill（await json 返回后） */
  searchCompleteLog: Array<{
    projectId: string;
    query: string | null;
    sourceKind: string | null;
    createdFrom: string | null;
    createdBefore: string | null;
  }>;
  /** 按 cursor 键固定第二页游标；筛选场景可用 esrc2 */
  pageSecondCursorBySource: Record<string, string>;
  /** 时间范围激活时第二页游标（默认 esrc3） */
  pageSecondCursorForTime: string;
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
  /** P12F-G-B DELETE 到达（gate 前） */
  deleteLog: DeleteProbeHit[];
  /** P12F-G-B DELETE 响应已 fulfill（await fulfill 返回后） */
  deleteCompleteLog: Array<{
    projectId: string;
    revisionId: string;
    status: number;
  }>;
  /** P12F-H PATCH display-name 到达（gate 前） */
  nameLog: NameProbeHit[];
  /** P12F-H PATCH display-name 响应已 fulfill */
  nameCompleteLog: Array<{
    projectId: string;
    revisionId: string;
    status: number;
    displayName: string | null;
  }>;
  editorGetLog: Array<{ projectId: string; path: string }>;
  putMode: PutMode;
  restoreMode: RestoreMode;
  listMode: ListMode;
  pageMode: PageMode;
  searchMode: SearchMode;
  detailMode: DetailMode;
  comparisonMode: ComparisonMode;
  bodyDiffMode: BodyDiffMode;
  pairBodyDiffMode: PairBodyDiffMode;
  deleteMode: DeleteMode;
  nameMode: NameMode;
  restoreModeByProject: Record<string, RestoreMode>;
  listModeByProject: Record<string, ListMode>;
  pageModeByProject: Record<string, PageMode>;
  /** 按 cursor 固定 page hold/错误（第二页） */
  pageModeByCursor: Record<string, PageMode>;
  searchModeByProject: Record<string, SearchMode>;
  /** 按 query 字面量固定 search hold/错误 */
  searchModeByQuery: Record<string, SearchMode>;
  detailModeByProject: Record<string, DetailMode>;
  detailModeByRevisionId: Record<string, DetailMode>;
  comparisonModeByProject: Record<string, ComparisonMode>;
  comparisonModeByRevisionId: Record<string, ComparisonMode>;
  bodyDiffModeByProject: Record<string, BodyDiffMode>;
  bodyDiffModeByRevisionId: Record<string, BodyDiffMode>;
  pairBodyDiffModeByProject: Record<string, PairBodyDiffMode>;
  /** 按 before::after 固定 pair hold */
  pairBodyDiffModeByPairKey: Record<string, PairBodyDiffMode>;
  deleteModeByProject: Record<string, DeleteMode>;
  deleteModeByRevisionId: Record<string, DeleteMode>;
  nameModeByProject: Record<string, NameMode>;
  nameModeByRevisionId: Record<string, NameMode>;
  listResponseOverride: unknown | null;
  pageResponseOverride: unknown | null;
  /** 按 cursor 键固定 page 响应（null cursor 用 ""） */
  pageResponseByCursor: Record<string, unknown>;
  /** 自定义第二页游标；默认 PAGE_CURSOR_SECOND */
  pageSecondCursor: string;
  searchResponseOverride: unknown | null;
  /** 按 query 字面量固定 search 响应 */
  searchResponseByQuery: Record<string, unknown>;
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
  /**
   * P12F-H：为 true 时 bootstrap 走 authRequired，触发 /auth/csrf 续发 e2e-csrf，
   * 使命名 PATCH 携带精确 x-csrf-token。默认 false 保持其余用例 disabled 兼容。
   */
  authRequired: boolean;
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
    authRequired: false,
    versionSeq: versionSeq + 1,
    revisionSeq: 0,
    checkpointSeq: 0,
    putLog: [],
    restoreLog: [],
    listLog: [],
    listCompleteLog: [],
    pageLog: [],
    pageCompleteLog: [],
    searchLog: [],
    searchCompleteLog: [],
    detailLog: [],
    detailCompleteLog: [],
    comparisonLog: [],
    comparisonCompleteLog: [],
    bodyDiffLog: [],
    bodyDiffCompleteLog: [],
    pairBodyDiffLog: [],
    pairBodyDiffCompleteLog: [],
    deleteLog: [],
    deleteCompleteLog: [],
    nameLog: [],
    nameCompleteLog: [],
    editorGetLog: [],
    putMode: { kind: "ok" },
    restoreMode: { kind: "ok" },
    listMode: { kind: "ok" },
    pageMode: { kind: "ok" },
    searchMode: { kind: "ok" },
    detailMode: { kind: "ok" },
    comparisonMode: { kind: "ok" },
    bodyDiffMode: { kind: "ok" },
    pairBodyDiffMode: { kind: "ok" },
    deleteMode: { kind: "ok" },
    nameMode: { kind: "ok" },
    restoreModeByProject: {},
    listModeByProject: {},
    pageModeByProject: {},
    pageModeByCursor: {},
    searchModeByProject: {},
    searchModeByQuery: {},
    detailModeByProject: {},
    detailModeByRevisionId: {},
    comparisonModeByProject: {},
    comparisonModeByRevisionId: {},
    bodyDiffModeByProject: {},
    bodyDiffModeByRevisionId: {},
    pairBodyDiffModeByProject: {},
    pairBodyDiffModeByPairKey: {},
    deleteModeByProject: {},
    deleteModeByRevisionId: {},
    nameModeByProject: {},
    nameModeByRevisionId: {},
    listResponseOverride: null,
    pageResponseOverride: null,
    pageResponseByCursor: {},
    pageSecondCursor: PAGE_CURSOR_SECOND,
    pageSecondCursorBySource: {},
    pageSecondCursorForTime: PAGE_CURSOR_TIME_SECOND,
    searchResponseOverride: null,
    searchResponseByQuery: {},
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

/** 用途：解析 DELETE 模式；按 revisionId 优先，其次 project，最后全局 */
function resolveDeleteMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): DeleteMode {
  return (
    state.deleteModeByRevisionId[revisionId] ??
    state.deleteModeByProject[projectId] ??
    state.deleteMode
  );
}

/** 用途：解析命名 PATCH 模式；按 revisionId 优先，其次 project，最后全局 */
function resolveNameMode(
  state: ProbeState,
  projectId: string,
  revisionId: string,
): NameMode {
  return (
    state.nameModeByRevisionId[revisionId] ??
    state.nameModeByProject[projectId] ??
    state.nameMode
  );
}

/** 用途：DELETE arrived 计数（可按 project/revision 过滤） */
function deleteHitCount(
  state: ProbeState,
  projectId?: string,
  revisionId?: string,
): number {
  return state.deleteLog.filter(
    (h) =>
      (projectId == null || h.projectId === projectId) &&
      (revisionId == null || h.revisionId === revisionId),
  ).length;
}

/** 用途：命名 PATCH arrived 计数 */
function nameHitCount(
  state: ProbeState,
  projectId?: string,
  revisionId?: string,
): number {
  return state.nameLog.filter(
    (h) =>
      (projectId == null || h.projectId === projectId) &&
      (revisionId == null || h.revisionId === revisionId),
  ).length;
}

/** 用途：命名 PATCH complete 计数 */
function nameCompleteCount(
  state: ProbeState,
  projectId?: string,
  revisionId?: string,
): number {
  return state.nameCompleteLog.filter(
    (h) =>
      (projectId == null || h.projectId === projectId) &&
      (revisionId == null || h.revisionId === revisionId),
  ).length;
}

/** 用途：DELETE complete 计数（可按 project/revision 过滤） */
function deleteCompleteCount(
  state: ProbeState,
  projectId?: string,
  revisionId?: string,
): number {
  return state.deleteCompleteLog.filter(
    (h) =>
      (projectId == null || h.projectId === projectId) &&
      (revisionId == null || h.revisionId === revisionId),
  ).length;
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

/** 用途：解析 search 模式；按 query 字面量优先 */
function resolveSearchMode(
  state: ProbeState,
  projectId: string,
  query: string | null,
): SearchMode {
  if (query != null && state.searchModeByQuery[query]) {
    return state.searchModeByQuery[query];
  }
  return state.searchModeByProject[projectId] ?? state.searchMode;
}

/** 用途：统计某项目 search 到达次数 */
function searchHitCount(state: ProbeState, projectId: string): number {
  return state.searchLog.filter((h) => h.projectId === projectId).length;
}

/** 用途：统计某项目 search 完成次数 */
function searchCompleteCount(state: ProbeState, projectId: string): number {
  return state.searchCompleteLog.filter((h) => h.projectId === projectId)
    .length;
}

/** 用途：统计精确 query+source+time 组合的 search 到达次数 */
function searchHitCountForFilter(
  state: ProbeState,
  projectId: string,
  query: string | null,
  sourceKind: string | null,
  createdFrom: string | null,
  createdBefore: string | null,
): number {
  return state.searchLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.query === query &&
      h.sourceKind === sourceKind &&
      h.createdFrom === createdFrom &&
      h.createdBefore === createdBefore,
  ).length;
}

/** 用途：统计精确 query+source+time 组合的 search 完成次数 */
function searchCompleteCountForFilter(
  state: ProbeState,
  projectId: string,
  query: string | null,
  sourceKind: string | null,
  createdFrom: string | null,
  createdBefore: string | null,
): number {
  return state.searchCompleteLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.query === query &&
      h.sourceKind === sourceKind &&
      h.createdFrom === createdFrom &&
      h.createdBefore === createdBefore,
  ).length;
}

/**
 * 用途：探针侧 NFKC+casefold 连续字面匹配可见字符串叶；
 *   仅用于 E2E 默认真值，不冒充完整后端白名单算法。
 */
function probeSnapshotContainsQuery(
  snap: Record<string, unknown> | undefined,
  query: string,
): boolean {
  if (!snap || typeof query !== "string" || query.length === 0) return false;
  const needle = query.normalize("NFKC").toLocaleLowerCase();
  const stack: unknown[] = [snap];
  let visits = 0;
  while (stack.length > 0) {
    const cur = stack.pop();
    visits += 1;
    if (visits > 8192) return false;
    if (typeof cur === "string") {
      if (cur.normalize("NFKC").toLocaleLowerCase().includes(needle)) {
        return true;
      }
      continue;
    }
    if (Array.isArray(cur)) {
      for (const item of cur) stack.push(item);
      continue;
    }
    if (cur && typeof cur === "object") {
      for (const v of Object.values(cur as Record<string, unknown>)) {
        stack.push(v);
      }
    }
  }
  return false;
}

/**
 * 用途：构建默认 search 响应；来源/时间过滤后最多 20 条；
 *   按 query 字面匹配 snapshot 可见字符串；无 nextCursor。
 */
function buildDefaultSearchPayload(
  state: ProbeState,
  projectId: string,
  query: string,
  sourceKind: string | null,
  createdFrom: string | null,
  createdBefore: string | null,
): { items: RevisionMeta[] } {
  const allRaw = state.revisions[projectId] || [];
  const filtered = allRaw.filter((it) => {
    if (sourceKind != null && it.sourceKind !== sourceKind) return false;
    if (!matchesCreatedAtRange(it.createdAt, createdFrom, createdBefore)) {
      return false;
    }
    const detail = state.details[it.revisionId];
    return probeSnapshotContainsQuery(detail?.snapshot, query);
  });
  return { items: filtered.slice(0, 20) };
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

/** 用途：统计精确时间+来源+cursor 组合的 page 到达次数 */
function pageHitCountForTimeFilter(
  state: ProbeState,
  projectId: string,
  sourceKind: string | null,
  createdFrom: string | null,
  createdBefore: string | null,
  cursor: string | null,
): number {
  return state.pageLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.sourceKind === sourceKind &&
      h.createdFrom === createdFrom &&
      h.createdBefore === createdBefore &&
      h.cursor === cursor,
  ).length;
}

/** 用途：统计精确时间+来源+cursor 组合的 page 完成次数 */
function pageCompleteCountForTimeFilter(
  state: ProbeState,
  projectId: string,
  sourceKind: string | null,
  createdFrom: string | null,
  createdBefore: string | null,
  cursor: string | null,
): number {
  return state.pageCompleteLog.filter(
    (h) =>
      h.projectId === projectId &&
      h.sourceKind === sourceKind &&
      h.createdFrom === createdFrom &&
      h.createdBefore === createdBefore &&
      h.cursor === cursor,
  ).length;
}

/**
 * 用途：半开区间 [createdFrom, createdBefore) 服务端时间过滤；
 *   ISO UTC 毫秒字符串可字典序比较。
 */
function matchesCreatedAtRange(
  createdAt: string,
  createdFrom: string | null,
  createdBefore: string | null,
): boolean {
  if (createdFrom != null && createdAt < createdFrom) return false;
  if (createdBefore != null && !(createdAt < createdBefore)) return false;
  return true;
}

/**
 * 用途：构建默认游标页响应；可选 sourceKind + [from,before) 服务端过滤；
 *   无筛选 nextCursor=esrc1；仅来源 esrc2；时间范围 esrc3。
 */
function buildDefaultPagePayload(
  state: ProbeState,
  projectId: string,
  cursor: string | null,
  sourceKind: string | null = null,
  createdFrom: string | null = null,
  createdBefore: string | null = null,
): { items: RevisionMeta[]; nextCursor: string | null } | { error: "cursor" } {
  const allRaw = state.revisions[projectId] || [];
  const hasTime = createdFrom != null || createdBefore != null;
  const all = allRaw.filter((it) => {
    if (sourceKind != null && it.sourceKind !== sourceKind) return false;
    if (!matchesCreatedAtRange(it.createdAt, createdFrom, createdBefore)) {
      return false;
    }
    return true;
  });
  let secondCursor: string;
  if (hasTime) {
    secondCursor = state.pageSecondCursorForTime;
  } else if (sourceKind == null) {
    secondCursor = state.pageSecondCursor;
  } else {
    secondCursor =
      state.pageSecondCursorBySource[sourceKind] ?? PAGE_CURSOR_FILTER_SECOND;
  }
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
  if (
    !hasTime &&
    sourceKind == null &&
    cursor === state.pageSecondCursor
  ) {
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
      await json(route, {
        bootstrapped: true,
        authRequired: Boolean(state.authRequired),
      });
      return;
    }
    if (path === "/api/auth/me" && method === "GET") {
      if (state.authRequired) {
        // 强制走 /auth/csrf 续发，使内存 CSRF=e2e-csrf
        await json(route, {
          user: { id: "user_e2e", username: "e2e" },
          workspaces: [
            {
              id: "ws_e2e",
              name: "E2E",
              role: "bid_writer",
              isOwner: true,
            },
          ],
          activeWorkspaceId: "ws_e2e",
          csrfToken: null,
        });
      } else {
        await json(route, { id: "user_e2e", name: "e2e" });
      }
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

    // P12F-F-B 内容搜索：必须在 page/list/detail 之前匹配，避免 "search" 被当 revisionId
    const revSearchMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/search\/?$/,
    );
    if (revSearchMatch) {
      const pid = revSearchMatch[1];
      const queryKeys = [...url.searchParams.keys()];
      const postData = req.postData();
      let body: Record<string, unknown> | null = null;
      let bodyKeys: string[] = [];
      let query: string | null = null;
      let sourceKind: string | null = null;
      let createdFrom: string | null = null;
      let createdBefore: string | null = null;
      if (postData != null && postData !== "") {
        try {
          const parsed = JSON.parse(postData) as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            body = parsed as Record<string, unknown>;
            bodyKeys = Object.keys(body);
            if (typeof body.query === "string") query = body.query;
            if (typeof body.sourceKind === "string") sourceKind = body.sourceKind;
            else if (body.sourceKind === null) sourceKind = null;
            if (typeof body.createdFrom === "string") createdFrom = body.createdFrom;
            else if (body.createdFrom === null) createdFrom = null;
            if (typeof body.createdBefore === "string") {
              createdBefore = body.createdBefore;
            } else if (body.createdBefore === null) {
              createdBefore = null;
            }
          }
        } catch {
          body = null;
          bodyKeys = [];
        }
      }
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      if (method !== "POST") {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "method_not_allowed" }, 405);
        return;
      }
      // URL 不得带 search/q/cursor 等 query
      if (queryKeys.length > 0 || url.search.length > 1) {
        state.forbiddenHits.push(`${method} ${path}${url.search}`);
      }
      // body 只允许 query / sourceKind / createdFrom / createdBefore
      const allowedBodyKeys = new Set([
        "query",
        "sourceKind",
        "createdFrom",
        "createdBefore",
      ]);
      for (const k of bodyKeys) {
        if (!allowedBodyKeys.has(k)) {
          state.forbiddenHits.push(`search_extra_key:${k}`);
        }
      }
      if (body == null || typeof body.query !== "string") {
        state.forbiddenHits.push("search_body_invalid");
      }
      // arrived：gate 前记录
      state.searchLog.push({
        projectId: pid,
        query,
        sourceKind,
        createdFrom,
        createdBefore,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
        bodyKeys,
        body,
      });
      const completeHit = {
        projectId: pid,
        query,
        sourceKind,
        createdFrom,
        createdBefore,
      };
      const searchMode = resolveSearchMode(state, pid, query);
      if (searchMode.kind === "hold") {
        await searchMode.gate.wait();
      }
      if (searchMode.kind === "http_error") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_search_error",
              message: "search error",
            },
          },
          searchMode.status,
        );
        state.searchCompleteLog.push(completeHit);
        return;
      }
      if (state.searchResponseOverride != null) {
        await json(route, state.searchResponseOverride);
        state.searchCompleteLog.push(completeHit);
        return;
      }
      if (query != null && state.searchResponseByQuery[query] != null) {
        await json(route, state.searchResponseByQuery[query]);
        state.searchCompleteLog.push(completeHit);
        return;
      }
      if (query == null || typeof query !== "string") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_search_query_invalid",
              message: "修订搜索关键词无效",
            },
          },
          400,
        );
        state.searchCompleteLog.push(completeHit);
        return;
      }
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
        state.searchCompleteLog.push(completeHit);
        return;
      }
      const built = buildDefaultSearchPayload(
        state,
        pid,
        query,
        sourceKind,
        createdFrom,
        createdBefore,
      );
      await json(route, { items: built.items });
      state.searchCompleteLog.push(completeHit);
      return;
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
      // createdFrom/createdBefore：缺省 null；空串也按字面记录（前端不得发送空串）
      const rawFrom = url.searchParams.get("createdFrom");
      const createdFrom = rawFrom === null ? null : rawFrom;
      const rawBefore = url.searchParams.get("createdBefore");
      const createdBefore = rawBefore === null ? null : rawBefore;
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
      // arrived：gate 前记录，含 cursor/sourceKind/时间与查询键
      state.pageLog.push({
        projectId: pid,
        cursor,
        sourceKind,
        createdFrom,
        createdBefore,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
      });
      const completeHit = {
        projectId: pid,
        cursor,
        sourceKind,
        createdFrom,
        createdBefore,
      };
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
        state.pageCompleteLog.push(completeHit);
        return;
      }
      if (state.pageResponseOverride != null) {
        await json(route, state.pageResponseOverride);
        state.pageCompleteLog.push(completeHit);
        return;
      }
      const cursorKey = cursor ?? "";
      if (state.pageResponseByCursor[cursorKey] != null) {
        await json(route, state.pageResponseByCursor[cursorKey]);
        state.pageCompleteLog.push(completeHit);
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
        state.pageCompleteLog.push(completeHit);
        return;
      }
      const built = buildDefaultPagePayload(
        state,
        pid,
        cursor,
        sourceKind,
        createdFrom,
        createdBefore,
      );
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
        state.pageCompleteLog.push(completeHit);
        return;
      }
      await json(route, {
        items: built.items,
        nextCursor: built.nextCursor,
      });
      state.pageCompleteLog.push(completeHit);
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
      // P12F-H：恢复不复制名称，新行 displayName 固定 null
      const newMeta: RevisionMeta = {
        revisionId: allocateRevisionId(state),
        stateVersion: restoredVersion,
        snapshotBytes: 256,
        sourceKind: "revision_restore",
        createdAt: new Date().toISOString(),
        displayName: null,
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

    // P12F-H 单条命名：精确 PATCH .../display-name；body 仅 displayName；成功原位更新探针 meta
    const revNameMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/display-name\/?$/,
    );
    if (revNameMatch && method === "PATCH") {
      const pid = revNameMatch[1];
      const revisionId = revNameMatch[2];
      const queryKeys = [...url.searchParams.keys()];
      const postData = req.postData();
      const headers = req.headers();
      // Playwright 头名为小写；记录实际 x-csrf-token 值（缺失为 null）
      const csrfRaw = headers["x-csrf-token"];
      const csrfToken = typeof csrfRaw === "string" ? csrfRaw : null;
      let bodyKeys: string[] = [];
      let displayName: string | null | undefined = undefined;
      if (postData != null && postData !== "") {
        try {
          const parsed = JSON.parse(postData) as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            const body = parsed as Record<string, unknown>;
            bodyKeys = Object.keys(body);
            if (Object.prototype.hasOwnProperty.call(body, "displayName")) {
              const v = body.displayName;
              if (v === null) displayName = null;
              else if (typeof v === "string") displayName = v;
              else displayName = undefined;
            }
          }
        } catch {
          bodyKeys = [];
          displayName = undefined;
        }
      }
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      // arrived：gate 前记录
      state.nameLog.push({
        projectId: pid,
        revisionId,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
        bodyKeys,
        displayName,
        csrfToken,
      });
      if (queryKeys.length > 0 || url.search.length > 1) {
        state.forbiddenHits.push(`${method} ${path}${url.search}`);
      }
      const nameMode = resolveNameMode(state, pid, revisionId);
      if (nameMode.kind === "hold") {
        await nameMode.gate.wait();
        // hold 后可显式失败：类型收窄 then，禁止 any 变异
        if (nameMode.then === "http_error") {
          await json(
            route,
            {
              detail: {
                code: "editor_state_revision_display_name_error",
                message: "保存修订名称失败",
              },
            },
            nameMode.status,
          );
          state.nameCompleteLog.push({
            projectId: pid,
            revisionId,
            status: nameMode.status,
            displayName: null,
          });
          return;
        }
        // then === "ok"：继续成功路径
      }
      if (nameMode.kind === "http_error") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_display_name_error",
              message: "保存修订名称失败",
            },
          },
          nameMode.status,
        );
        state.nameCompleteLog.push({
          projectId: pid,
          revisionId,
          status: nameMode.status,
          displayName: null,
        });
        return;
      }
      const list = state.revisions[pid] || [];
      const idx = list.findIndex((r) => r.revisionId === revisionId);
      if (idx < 0) {
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
        state.nameCompleteLog.push({
          projectId: pid,
          revisionId,
          status: 404,
          displayName: null,
        });
        return;
      }
      // 仅当 body 精确一键且值为 string|null 时成功；否则 422
      if (
        bodyKeys.length !== 1 ||
        bodyKeys[0] !== "displayName" ||
        (displayName !== null && typeof displayName !== "string")
      ) {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_display_name_invalid",
              message: "修订名称无效",
            },
          },
          422,
        );
        state.nameCompleteLog.push({
          projectId: pid,
          revisionId,
          status: 422,
          displayName: null,
        });
        return;
      }
      // 探针侧原位更新六键 displayName；不重排、不重载 page/search
      const meta = list[idx];
      meta.displayName = displayName;
      list[idx] = meta;
      const detail = state.details[revisionId];
      if (detail) {
        detail.displayName = displayName;
        state.details[revisionId] = detail;
      }
      await json(route, { displayName });
      state.nameCompleteLog.push({
        projectId: pid,
        revisionId,
        status: 200,
        displayName,
      });
      return;
    }

    // P12F-G-B 单条物理删除：精确 DELETE，无 query/body；成功空 204 且突变探针 revisions/details
    const revDeleteMatch = path.match(
      /^\/api\/projects\/([^/]+)\/editor-state-revisions\/([^/]+)\/?$/,
    );
    if (revDeleteMatch && method === "DELETE") {
      const pid = revDeleteMatch[1];
      const revisionId = revDeleteMatch[2];
      const queryKeys = [...url.searchParams.keys()];
      const postData = req.postData();
      if (!known.has(pid)) {
        state.forbiddenHits.push(`${method} ${path}`);
        await json(route, { detail: "not_found" }, 404);
        return;
      }
      // arrived：gate 前记录
      state.deleteLog.push({
        projectId: pid,
        revisionId,
        method,
        path,
        postData,
        queryKeys,
        search: url.search,
      });
      // 无 query/body 约束：探测违规仍记录 arrived，并记 forbidden
      if (queryKeys.length > 0 || url.search.length > 1) {
        state.forbiddenHits.push(`${method} ${path}${url.search}`);
      }
      if (postData != null && postData !== "") {
        state.forbiddenHits.push(`delete_body:${path}`);
      }
      const deleteMode = resolveDeleteMode(state, pid, revisionId);
      if (deleteMode.kind === "hold") {
        await deleteMode.gate.wait();
      }
      if (deleteMode.kind === "http_error") {
        await json(
          route,
          {
            detail: {
              code: "editor_state_revision_delete_error",
              message: "delete error",
            },
          },
          deleteMode.status,
        );
        state.deleteCompleteLog.push({
          projectId: pid,
          revisionId,
          status: deleteMode.status,
        });
        return;
      }
      // 成功：仅在 ok（hold 释放后亦走此路径）时突变探针状态；失败不得修改
      const before = state.revisions[pid] || [];
      const found = before.some((r) => r.revisionId === revisionId);
      if (!found) {
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
        state.deleteCompleteLog.push({
          projectId: pid,
          revisionId,
          status: 404,
        });
        return;
      }
      state.revisions[pid] = before.filter((r) => r.revisionId !== revisionId);
      delete state.details[revisionId];
      // 204 必须以空 body fulfill；禁止 JSON 体
      await route.fulfill({ status: 204, body: "" });
      state.deleteCompleteLog.push({
        projectId: pid,
        revisionId,
        status: 204,
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
  expect(consoleBlob).not.toContain(PAGE_CURSOR_TIME_SECOND);
  expect(consoleBlob).not.toContain("esrc1_");
  expect(consoleBlob).not.toContain("esrc2_");
  expect(consoleBlob).not.toContain("esrc3_");
  expect(consoleBlob).not.toContain("createdFrom");
  expect(consoleBlob).not.toContain("createdBefore");
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
      displayName: null,
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

    // 无创建/删除；禁止错误命名「搜索修订」（P12F-F-B 合法入口为「内容搜索」+「搜索」）
    await expect(
      page.getByTestId("editor-state-revision-create"),
    ).toHaveCount(0);
    await expect(page.getByText("删除修订", { exact: true })).toHaveCount(0);
    await expect(page.getByText("搜索修订", { exact: true })).toHaveCount(0);
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

// ---------------------------------------------------------------------------
// P12F-E-B 修订历史时间范围筛选
// ---------------------------------------------------------------------------

/** 用途：同步 seed 后 detail 的 createdAt 为可控 UTC 毫秒序列 */
function syncRevisionCreatedAts(
  state: ProbeState,
  projectId: string,
  startUtcMs: string,
  stepMinutes = 1,
): RevisionMeta[] {
  const list = state.revisions[projectId] || [];
  const start = Date.parse(startUtcMs);
  for (let i = 0; i < list.length; i++) {
    const at = new Date(start + i * stepMinutes * 60_000).toISOString();
    list[i].createdAt = at;
    if (state.details[list[i].revisionId]) {
      state.details[list[i].revisionId].createdAt = at;
    }
  }
  return list;
}

test.describe("P12F-E-B 技术标修订历史时间范围筛选", () => {
  test.describe.configure({ mode: "serial" });
  test.use({ timezoneId: "Asia/Shanghai" });

  test("P12F-E-B 技术标：默认无时间；上海 08:00→UTC；单边/双边/来源/query顺序/同值零重发/倒序保值/清除；V3第二页；空态；失败", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    // 11 task + 3 revise，时间从 2026-07-16T00:00Z 起每分钟一条
    const taskSources = Array.from({ length: 11 }, () => "task");
    const reviseSources = Array.from({ length: 3 }, () => "revise");
    const seeded = seedRevisions(
      state,
      TECH_A,
      14,
      taskSources.concat(reviseSources),
    );
    syncRevisionCreatedAts(state, TECH_A, "2026-07-16T00:00:00.000Z", 1);
    state.pageSecondCursorForTime = PAGE_CURSOR_TIME_SECOND;

    const UTC_FROM_0800 = "2026-07-16T00:00:00.000Z"; // 上海本地 08:00
    const UTC_BEFORE_0810 = "2026-07-16T00:10:00.000Z"; // 上海本地 08:10
    const UTC_BEFORE_0805 = "2026-07-16T00:05:00.000Z"; // 上海本地 08:05

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.pageLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-created-from"),
    ).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    const firstHit = state.pageLog[0];
    expect(firstHit.method).toBe("GET");
    expect(firstHit.postData).toBeNull();
    expect(firstHit.cursor).toBeNull();
    expect(firstHit.sourceKind).toBeNull();
    expect(firstHit.createdFrom).toBeNull();
    expect(firstHit.createdBefore).toBeNull();
    expect(firstHit.queryKeys).toEqual([]);
    expect(firstHit.search).toBe("");

    const fromInput = page.getByTestId("editor-state-revision-created-from");
    const beforeInput = page.getByTestId(
      "editor-state-revision-created-before",
    );
    const applyBtn = page.getByTestId("editor-state-revision-time-apply");
    const clearBtn = page.getByTestId("editor-state-revision-time-clear");
    await expect(fromInput).toBeVisible();
    await expect(beforeInput).toBeVisible();
    await expect(applyBtn).toBeVisible();
    await expect(clearBtn).toBeVisible();
    await expect(applyBtn).toBeDisabled();

    // 上海本地 08:00 → UTC 00:00:00.000Z（单边 createdFrom）
    await fromInput.fill("2026-07-16T08:00");
    await expect(applyBtn).toBeEnabled();
    const pageBeforeFrom = state.pageLog.length;
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(state.pageLog.length).toBe(pageBeforeFrom + 1);
    const fromHit = state.pageLog[state.pageLog.length - 1];
    expect(fromHit.createdFrom).toBe(UTC_FROM_0800);
    expect(fromHit.createdBefore).toBeNull();
    expect(fromHit.sourceKind).toBeNull();
    expect(fromHit.cursor).toBeNull();
    expect(fromHit.postData).toBeNull();
    expect(fromHit.queryKeys).toEqual(["createdFrom"]);
    expect(fromHit.search).toBe(
      `?createdFrom=${encodeURIComponent(UTC_FROM_0800)}`,
    );
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 同值零重发
    const sameBefore = state.pageLog.length;
    await applyBtn.click();
    expect(state.pageLog.length).toBe(sameBefore);

    // 双边：本地 08:00–08:10 → [00:00, 00:10) UTC；恰 10 条
    await beforeInput.fill("2026-07-16T08:10");
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            UTC_BEFORE_0810,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const bothHit = state.pageLog[state.pageLog.length - 1];
    expect(bothHit.createdFrom).toBe(UTC_FROM_0800);
    expect(bothHit.createdBefore).toBe(UTC_BEFORE_0810);
    expect(bothHit.queryKeys).toEqual(["createdFrom", "createdBefore"]);
    expect(bothHit.search).toBe(
      `?createdFrom=${encodeURIComponent(UTC_FROM_0800)}&createdBefore=${encodeURIComponent(UTC_BEFORE_0810)}`,
    );
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
    ).toHaveCount(0);

    // 来源 + 时间：query 顺序 sourceKind → createdFrom → createdBefore
    const filter = page.getByTestId("editor-state-revision-source-filter");
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            UTC_BEFORE_0810,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const comboHit = state.pageLog[state.pageLog.length - 1];
    expect(comboHit.sourceKind).toBe("task");
    expect(comboHit.createdFrom).toBe(UTC_FROM_0800);
    expect(comboHit.createdBefore).toBe(UTC_BEFORE_0810);
    expect(comboHit.queryKeys).toEqual([
      "sourceKind",
      "createdFrom",
      "createdBefore",
    ]);
    expect(comboHit.search).toBe(
      `?sourceKind=task&createdFrom=${encodeURIComponent(UTC_FROM_0800)}&createdBefore=${encodeURIComponent(UTC_BEFORE_0810)}`,
    );
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText("任务写入");
    }

    // 倒序无效：零请求 + 固定错误 + 列表保值
    const beforeInvalid = state.pageLog.length;
    await fromInput.fill("2026-07-16T09:00");
    await beforeInput.fill("2026-07-16T08:00");
    await applyBtn.click();
    await expect(
      page.getByTestId("editor-state-revision-time-error"),
    ).toHaveText(MSG_TIME_RANGE_INVALID);
    expect(state.pageLog.length).toBe(beforeInvalid);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await fromInput.fill("2026-07-16T08:00");
    await expect(
      page.getByTestId("editor-state-revision-time-error"),
    ).toHaveCount(0);

    // 清除：保留来源，无时间
    const clearHitsBefore = pageHitCountForTimeFilter(
      state,
      TECH_A,
      "task",
      null,
      null,
      null,
    );
    await clearBtn.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(state, TECH_A, "task", null, null, null),
        { timeout: 10_000 },
      )
      .toBe(clearHitsBefore + 1);
    const afterClearHit = state.pageLog[state.pageLog.length - 1];
    expect(afterClearHit.sourceKind).toBe("task");
    expect(afterClearHit.createdFrom).toBeNull();
    expect(afterClearHit.createdBefore).toBeNull();
    expect(afterClearHit.queryKeys).toEqual(["sourceKind"]);
    await expect(fromInput).toHaveValue("");
    await expect(beforeInput).toHaveValue("");

    // 全空清除不重发
    const clearEmptyBefore = state.pageLog.length;
    await clearBtn.click();
    expect(state.pageLog.length).toBe(clearEmptyBefore);

    // 单边 createdBefore：本地 08:05 → UTC 00:05；task 且 < 00:05 → 5 条
    await beforeInput.fill("2026-07-16T08:05");
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            null,
            UTC_BEFORE_0805,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const beforeOnlyHit = state.pageLog[state.pageLog.length - 1];
    expect(beforeOnlyHit.createdFrom).toBeNull();
    expect(beforeOnlyHit.createdBefore).toBe(UTC_BEFORE_0805);
    expect(beforeOnlyHit.sourceKind).toBe("task");
    expect(beforeOnlyHit.queryKeys).toEqual(["sourceKind", "createdBefore"]);
    for (let i = 0; i < 5; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-5")).toHaveCount(
      0,
    );

    // 空态
    await fromInput.fill("2026-07-20T08:00");
    await beforeInput.fill("2026-07-20T09:00");
    await applyBtn.click();
    const emptyFrom = "2026-07-20T00:00:00.000Z";
    const emptyBefore = "2026-07-20T01:00:00.000Z";
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            emptyFrom,
            emptyBefore,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-empty")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );

    // 首屏失败不回退
    state.pageModeByProject[TECH_A] = { kind: "http_error", status: 500 };
    await fromInput.fill("2026-07-16T08:00");
    await beforeInput.fill("2026-07-16T08:10");
    const failCompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM_0800,
      UTC_BEFORE_0810,
      null,
    );
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            UTC_BEFORE_0810,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(failCompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_LIST_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    await expect(fromInput).toHaveValue("2026-07-16T08:00");
    await expect(beforeInput).toHaveValue("2026-07-16T08:10");

    // 刷新重试仍带完整条件
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    const failHitsBefore = pageHitCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM_0800,
      UTC_BEFORE_0810,
      null,
    );
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            UTC_BEFORE_0810,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(failHitsBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();

    // V3 第二页：仅 from + task → 11 条
    await fromInput.fill("2026-07-16T08:00");
    await beforeInput.fill("");
    const v3FirstCompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM_0800,
      null,
      null,
    );
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(v3FirstCompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 第二页失败保值 + 同 cursor 重试
    state.pageModeByCursor[PAGE_CURSOR_TIME_SECOND] = {
      kind: "http_error",
      status: 500,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            null,
            PAGE_CURSOR_TIME_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const failMoreHit = state.pageLog[state.pageLog.length - 1];
    expect(failMoreHit.cursor).toBe(PAGE_CURSOR_TIME_SECOND);
    expect(failMoreHit.sourceKind).toBe("task");
    expect(failMoreHit.createdFrom).toBe(UTC_FROM_0800);
    expect(failMoreHit.createdBefore).toBeNull();
    expect(failMoreHit.queryKeys).toEqual([
      "sourceKind",
      "createdFrom",
      "cursor",
    ]);
    expect(failMoreHit.search).toBe(
      `?sourceKind=task&createdFrom=${encodeURIComponent(UTC_FROM_0800)}&cursor=${encodeURIComponent(PAGE_CURSOR_TIME_SECOND)}`,
    );
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveText(MSG_LOAD_MORE_FAIL);

    state.pageModeByCursor[PAGE_CURSOR_TIME_SECOND] = { kind: "ok" };
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM_0800,
            null,
            PAGE_CURSOR_TIME_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);
    await expect(page.getByTestId("editor-state-revision-item-11")).toHaveCount(
      0,
    );

    // V3 256 外壳：override nextCursor 后第二页原样回传
    const allSrcCompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM_0800,
      null,
      null,
    );
    await filter.selectOption({ label: "全部来源" });
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(allSrcCompleteBefore + 1);
    const tenItems = (state.revisions[TECH_A] || []).slice(0, 10);
    state.pageResponseByCursor[""] = {
      items: tenItems,
      nextCursor: PAGE_CURSOR_V3_LEN_256,
    };
    state.pageSecondCursorForTime = PAGE_CURSOR_V3_LEN_256;
    state.pageModeByCursor[PAGE_CURSOR_V3_LEN_256] = { kind: "ok" };
    state.pageResponseByCursor[PAGE_CURSOR_V3_LEN_256] = {
      items: (state.revisions[TECH_A] || []).slice(10, 14),
      nextCursor: null,
    };
    const v3RefreshCompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM_0800,
      null,
      null,
    );
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(v3RefreshCompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    delete state.pageResponseByCursor[""];
    const v3Before = pageHitCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM_0800,
      null,
      PAGE_CURSOR_V3_LEN_256,
    );
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            PAGE_CURSOR_V3_LEN_256,
          ),
        { timeout: 10_000 },
      )
      .toBe(v3Before + 1);
    const v3Hit = state.pageLog[state.pageLog.length - 1];
    expect(v3Hit.cursor).toBe(PAGE_CURSOR_V3_LEN_256);
    expect(v3Hit.cursor!.length).toBe(256);
    expect(v3Hit.createdFrom).toBe(UTC_FROM_0800);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-14")).toHaveCount(
      0,
    );

    // V3 257 超长：首屏 nextCursor 恰 257 字符 → parseRevisionPage 整页失败
    const bad257CompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM_0800,
      null,
      null,
    );
    const bad257SecondHitsBefore = pageHitCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM_0800,
      null,
      PAGE_CURSOR_V3_LEN_257,
    );
    state.pageResponseByCursor[""] = {
      items: tenItems,
      nextCursor: PAGE_CURSOR_V3_LEN_257,
    };
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM_0800,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(bad257CompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_LIST_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);
    expect(
      pageHitCountForTimeFilter(
        state,
        TECH_A,
        null,
        UTC_FROM_0800,
        null,
        PAGE_CURSOR_V3_LEN_257,
      ),
    ).toBe(bad257SecondHitsBefore);
    // 清理 override，避免污染后续阶段
    delete state.pageResponseByCursor[""];

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);

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
        body: document.body?.innerText ?? "",
      };
    });
    for (const blob of [
      persist.ls,
      persist.ss,
      persist.href,
      persist.cookie ?? "",
    ]) {
      expect(blob).not.toContain("createdFrom");
      expect(blob).not.toContain("createdBefore");
      expect(blob).not.toContain("esrc3_");
      expect(blob).not.toContain(PAGE_CURSOR_TIME_SECOND);
      expect(blob).not.toContain(UTC_FROM_0800);
      expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    expect(persist.body).not.toContain("createdFrom=");
    expect(persist.body).not.toContain("esrc3_");
    expect(persist.body).not.toContain(PAGE_CURSOR_TIME_SECOND);
    expect(persist.body).not.toContain(UTC_FROM_0800);
    for (const item of seeded) {
      expect(persist.body).not.toContain(item.revisionId);
      expect(persist.body).not.toContain(item.stateVersion);
    }
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain("createdFrom");
    expect(consoleBlob).not.toContain("createdBefore");
    expect(consoleBlob).not.toContain("esrc3_");
    expect(consoleBlob).not.toContain(PAGE_CURSOR_TIME_SECOND);
    expect(seeded.length).toBe(14);
    expect(PAGE_CURSOR_V3_LEN_256.length).toBe(256);
    expect(PAGE_CURSOR_V3_LEN_257.length).toBe(257);
  });

  test("P12F-E-B 技术标：应用清意图；草稿不影响刷新/来源；折叠保留；项目切换重置；首屏与 load-more 迟到隔离", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    seedRevisions(
      state,
      TECH_A,
      12,
      Array.from({ length: 11 }, () => "task").concat(["revise"]),
    );
    syncRevisionCreatedAts(state, TECH_A, "2026-07-16T00:00:00.000Z", 1);
    seedRevisions(
      state,
      TECH_B,
      5,
      Array.from({ length: 5 }, () => "callback"),
    );
    syncRevisionCreatedAts(state, TECH_B, "2026-07-17T00:00:00.000Z", 1);
    state.pageSecondCursorForTime = PAGE_CURSOR_TIME_SECOND;

    const UTC_FROM = "2026-07-16T00:00:00.000Z";
    const firstTask = state.revisions[TECH_A][0];
    state.details[firstTask.revisionId].snapshot = {
      ...state.details[firstTask.revisionId].snapshot,
      chapters: [{ id: "ch", title: "时间筛选章节", body: "TIME_BODY_LEAK" }],
    };

    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);

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

    const fromInput = page.getByTestId("editor-state-revision-created-from");
    const applyBtn = page.getByTestId("editor-state-revision-time-apply");
    await fromInput.fill("2026-07-16T08:00");
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);

    // 草稿不影响刷新
    await fromInput.fill("2026-07-16T09:00");
    const beforeRefresh = pageHitCountForTimeFilter(
      state,
      TECH_A,
      null,
      UTC_FROM,
      null,
      null,
    );
    const draftUtc = "2026-07-16T01:00:00.000Z";
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            null,
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(beforeRefresh + 1);
    expect(
      pageHitCountForTimeFilter(state, TECH_A, null, draftUtc, null, null),
    ).toBe(0);
    expect(state.pageLog[state.pageLog.length - 1].createdFrom).toBe(UTC_FROM);

    // 草稿不影响来源切换
    const filter = page.getByTestId("editor-state-revision-source-filter");
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(
      pageHitCountForTimeFilter(state, TECH_A, "task", draftUtc, null, null),
    ).toBe(0);
    await fromInput.fill("2026-07-16T08:00");

    // 折叠再展开保留
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    const taskTimeBefore = pageHitCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM,
      null,
      null,
    );
    await expandRevisionPanel(page);
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(taskTimeBefore + 1);
    await expect(filter).toHaveValue("task");
    await expect(fromInput).toHaveValue("2026-07-16T08:00");
    const reopenHit = state.pageLog[state.pageLog.length - 1];
    expect(reopenHit.sourceKind).toBe("task");
    expect(reopenHit.createdFrom).toBe(UTC_FROM);
    expect(reopenHit.cursor).toBeNull();

    // 首屏 arrived+complete 迟到隔离
    const lateGate = createHoldGate();
    state.pageModeByProject[TECH_A] = { kind: "hold", gate: lateGate };
    await page.getByTestId("editor-state-revision-refresh").click();
    await lateGate.waitUntilEntered(1);
    const arrivedHeld = pageHitCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM,
      null,
      null,
    );
    const completeHeld = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM,
      null,
      null,
    );
    await expect(filter).toBeDisabled();
    await expect(fromInput).toBeDisabled();
    await expect(applyBtn).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-time-clear"),
    ).toBeDisabled();
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    lateGate.release();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(completeHeld + 1);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    expect(
      pageHitCountForTimeFilter(state, TECH_A, "task", UTC_FROM, null, null),
    ).toBe(arrivedHeld);

    // load-more 迟到隔离
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    await expandRevisionPanel(page);
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(completeHeld + 2);
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();
    const loadGate = createHoldGate();
    state.pageModeByCursor[PAGE_CURSOR_TIME_SECOND] = {
      kind: "hold",
      gate: loadGate,
    };
    await page.getByTestId("editor-state-revision-load-more").click();
    await loadGate.waitUntilEntered(1);
    await expect(filter).toBeDisabled();
    await expect(fromInput).toBeDisabled();
    await expect(applyBtn).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-time-clear"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    loadGate.release();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            PAGE_CURSOR_TIME_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);

    // 同一 TECH_A 重新展开：证明迟到第二页 success/catch/finally 未写；首屏精确 +1
    state.pageModeByCursor[PAGE_CURSOR_TIME_SECOND] = { kind: "ok" };
    const reopenFirstCompleteBefore = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM,
      null,
      null,
    );
    const lateSecondComplete = pageCompleteCountForTimeFilter(
      state,
      TECH_A,
      "task",
      UTC_FROM,
      null,
      PAGE_CURSOR_TIME_SECOND,
    );
    await expandRevisionPanel(page);
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(reopenFirstCompleteBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more-error"),
    ).toHaveCount(0);
    expect(
      pageCompleteCountForTimeFilter(
        state,
        TECH_A,
        "task",
        UTC_FROM,
        null,
        PAGE_CURSOR_TIME_SECOND,
      ),
    ).toBe(lateSecondComplete);

    // 项目切换重置
    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageHitCountForSource(state, TECH_B, null), {
        timeout: 10_000,
      })
      .toBe(1);
    const bFrom = page.getByTestId("editor-state-revision-created-from");
    const bFilter = page.getByTestId("editor-state-revision-source-filter");
    await expect(bFilter).toHaveValue("");
    await expect(bFrom).toHaveValue("");
    const bHit = state.pageLog[state.pageLog.length - 1];
    expect(bHit.projectId).toBe(TECH_B);
    expect(bHit.sourceKind).toBeNull();
    expect(bHit.createdFrom).toBeNull();
    expect(bHit.createdBefore).toBeNull();
    expect(bHit.queryKeys).toEqual([]);

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
    const console2 = guards.consoleLogs.join("\n");
    expect(console2).not.toContain("createdFrom");
    expect(console2).not.toContain("esrc3_");
    expect(console2).not.toContain(PAGE_CURSOR_TIME_SECOND);
    expect(console2).not.toContain("TIME_BODY_LEAK");
  });
});

test.describe("P12F-E-B 商务标修订历史时间范围筛选", () => {
  test.describe.configure({ mode: "serial" });
  test.use({ timezoneId: "Asia/Shanghai" });

  test("P12F-E-B 商务标：共享入口；刷新/恢复保留来源+时间；在途禁用；唯一 restore；零泄漏", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const seeded = seedRevisions(
      state,
      BIZ_A,
      11,
      Array.from({ length: 11 }, () => "callback"),
    );
    syncRevisionCreatedAts(state, BIZ_A, "2026-07-16T00:00:00.000Z", 1);
    state.pageSecondCursorForTime = PAGE_CURSOR_TIME_SECOND;
    const UTC_FROM = "2026-07-16T00:00:00.000Z";
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.pageLog.length).toBe(0);

    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, BIZ_A)).toBe(1);
    expect(state.pageLog[0].createdFrom).toBeNull();
    expect(state.pageLog[0].queryKeys).toEqual([]);

    const filter = page.getByTestId("editor-state-revision-source-filter");
    const fromInput = page.getByTestId("editor-state-revision-created-from");
    const applyBtn = page.getByTestId("editor-state-revision-time-apply");
    await expect(fromInput).toBeVisible();
    await filter.selectOption({ label: "解析回传" });
    await expect
      .poll(() => pageHitCountForSource(state, BIZ_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(1);
    await fromInput.fill("2026-07-16T08:00");
    await applyBtn.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const selHit = state.pageLog[state.pageLog.length - 1];
    expect(selHit.sourceKind).toBe("callback");
    expect(selHit.createdFrom).toBe(UTC_FROM);
    expect(selHit.queryKeys).toEqual(["sourceKind", "createdFrom"]);
    expect(selHit.search).toBe(
      `?sourceKind=callback&createdFrom=${encodeURIComponent(UTC_FROM)}`,
    );

    // 刷新保留来源+时间
    const beforeRefresh = pageHitCountForTimeFilter(
      state,
      BIZ_A,
      "callback",
      UTC_FROM,
      null,
      null,
    );
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(beforeRefresh + 1);
    expect(state.pageLog[state.pageLog.length - 1].createdFrom).toBe(UTC_FROM);
    expect(state.pageLog[state.pageLog.length - 1].sourceKind).toBe("callback");
    await expect(filter).toHaveValue("callback");
    await expect(fromInput).toHaveValue("2026-07-16T08:00");

    // 第二页 esrc3 全量重复
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageCompleteCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            null,
            PAGE_CURSOR_TIME_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const moreHit = state.pageLog[state.pageLog.length - 1];
    expect(moreHit.cursor).toBe(PAGE_CURSOR_TIME_SECOND);
    expect(moreHit.sourceKind).toBe("callback");
    expect(moreHit.createdFrom).toBe(UTC_FROM);
    expect(moreHit.queryKeys).toEqual([
      "sourceKind",
      "createdFrom",
      "cursor",
    ]);
    expect(moreHit.search).toBe(
      `?sourceKind=callback&createdFrom=${encodeURIComponent(UTC_FROM)}&cursor=${encodeURIComponent(PAGE_CURSOR_TIME_SECOND)}`,
    );
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();

    // 恢复在途禁用 + 唯一写链
    const restoreGate = createHoldGate();
    state.restoreMode = { kind: "gate", gate: restoreGate, then: "ok" };
    const pageBeforeRestore = pageHitCount(state, BIZ_A);
    const pageFirstBeforeRestore = pageHitCountForTimeFilter(
      state,
      BIZ_A,
      "callback",
      UTC_FROM,
      null,
      null,
    );
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const expectedAtConfirm = state.projects[BIZ_A].stateVersion;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGate.waitUntilEntered(1);
    expect(state.restoreLog.length).toBe(0);
    await expect(filter).toBeDisabled();
    await expect(fromInput).toBeDisabled();
    await expect(applyBtn).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-time-clear"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-confirm-restore-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-cancel-restore-0"),
    ).toBeDisabled();
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
    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(() => pageHitCount(state, BIZ_A) - pageBeforeRestore, {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            null,
            null,
          ) - pageFirstBeforeRestore,
        { timeout: 10_000 },
      )
      .toBe(1);
    const reloadHit = state.pageLog[state.pageLog.length - 1];
    expect(reloadHit.sourceKind).toBe("callback");
    expect(reloadHit.createdFrom).toBe(UTC_FROM);
    expect(reloadHit.cursor).toBeNull();
    expect(reloadHit.queryKeys).toEqual(["sourceKind", "createdFrom"]);
    await expect(filter).toHaveValue("callback");
    await expect(fromInput).toHaveValue("2026-07-16T08:00");
    await expect(filter).toBeEnabled();
    await expect(fromInput).toBeEnabled();
    await expect(page.getByTestId("editor-state-revision-item-10")).toHaveCount(
      0,
    );

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);

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
      body: document.body?.innerText ?? "",
    }));
    for (const blob of [
      persistBiz.href,
      persistBiz.ls,
      persistBiz.ss,
      persistBiz.cookie ?? "",
    ]) {
      expect(blob).not.toContain("createdFrom");
      expect(blob).not.toContain("createdBefore");
      expect(blob).not.toContain("esrc3_");
      expect(blob).not.toContain(PAGE_CURSOR_TIME_SECOND);
      expect(blob).not.toContain(UTC_FROM);
      expect(blob).not.toContain(SNAPSHOT_BODY_LEAK);
    }
    expect(persistBiz.body).not.toContain("esrc3_");
    expect(persistBiz.body).not.toContain(UTC_FROM);
    for (const item of seeded) {
      expect(persistBiz.body).not.toContain(item.revisionId);
      expect(persistBiz.body).not.toContain(item.stateVersion);
    }
    const consoleBiz = guards.consoleLogs.join("\n");
    expect(consoleBiz).not.toContain("createdFrom");
    expect(consoleBiz).not.toContain("esrc3_");
    expect(consoleBiz).not.toContain(PAGE_CURSOR_TIME_SECOND);
    expect(seeded.length).toBe(11);
  });
});

// ---------------------------------------------------------------------------
// P12F-F-B 修订可见内容搜索前端（三用例互不 serial 跳过）
// ---------------------------------------------------------------------------

/**
 * 用途：把指定修订的 snapshot 章节/商务字段写入可搜索标记，供探针默认匹配。
 */
function stampSearchableMarker(
  state: ProbeState,
  revisionId: string,
  marker: string,
  mode: Mode,
) {
  const detail = state.details[revisionId];
  if (!detail) return;
  const snap = { ...detail.snapshot };
  if (mode === "tech") {
    const chapters = Array.isArray(snap.chapters)
      ? [...(snap.chapters as Array<Record<string, unknown>>)]
      : [];
    if (chapters.length === 0) {
      chapters.push({ id: "ch_search", title: marker, body: marker });
    } else {
      const first = { ...(chapters[0] as Record<string, unknown>) };
      first.title = `${String(first.title ?? "")}${marker}`;
      first.body = `${String(first.body ?? "")}${marker}`;
      chapters[0] = first;
    }
    snap.chapters = chapters;
  } else {
    const commits = Array.isArray(snap.businessCommit)
      ? [...(snap.businessCommit as Array<Record<string, unknown>>)]
      : [];
    commits.push({ title: marker, body: marker });
    snap.businessCommit = commits;
  }
  state.details[revisionId] = { ...detail, snapshot: snap };
}

/**
 * 用途：构造严格合法五键 search item，供 parser 坏响应/21 条边界注入。
 * 约束：revisionId/stateVersion 使用探针同款 esr_/esv_ 外壳；不依赖列表种子。
 */
function makeValidSearchMeta(n: number): RevisionMeta {
  return {
    revisionId: seedRevisionId(n),
    stateVersion: seedStateVersion(n),
    snapshotBytes: 100 + (n % 50),
    sourceKind: "task",
    createdAt: "2026-07-16T10:00:00.000Z",
  };
}

test.describe("P12F-F-B 技术标显式搜索", () => {
  // 独立 describe，禁止跨用例 serial 首失败 did-not-run
  test("P12F-F-B 技术标：输入零请求、非法零请求保值、有效 POST 无 URL query/精确 body、严格五键/唯一/最多20、空态/失败/刷新/清除、搜索态无加载更多", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const SEARCH_MARK = "P12FFB_TECH_HIT_MARK";
    const SEARCH_Q = "P12FFB_TECH_HIT";
    const EMPTY_Q = "NO_MATCH_TOKEN_XYZ";
    const seeded = seedRevisions(
      state,
      TECH_A,
      21,
      Array.from({ length: 21 }, (_, i) => (i % 2 === 0 ? "task" : "revise")),
    );
    // 最新 20 候选窗：给前 20 条打标记（列表倒序索引 0 为最新）
    for (let i = 0; i < 20; i++) {
      stampSearchableMarker(state, seeded[i].revisionId, SEARCH_MARK, "tech");
    }
    // 第 21 条不打标，证明候选窗边界由探针/后端控制
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.searchLog.length).toBe(0);
    expect(state.pageLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-search-input"),
    ).toHaveCount(0);

    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    expect(state.searchLog.length).toBe(0);
    expect(state.listLog.length).toBe(0);

    const searchInput = page.getByTestId("editor-state-revision-search-input");
    const searchApply = page.getByTestId("editor-state-revision-search-apply");
    const searchClear = page.getByTestId("editor-state-revision-search-clear");
    await expect(searchInput).toBeVisible();
    await expect(searchApply).toBeVisible();
    await expect(searchClear).toBeVisible();
    await expect(page.getByText("内容搜索", { exact: true })).toBeVisible();

    // 输入零请求
    const pageBeforeType = state.pageLog.length;
    const searchBeforeType = state.searchLog.length;
    await searchInput.fill(SEARCH_Q);
    expect(state.searchLog.length).toBe(searchBeforeType);
    expect(state.pageLog.length).toBe(pageBeforeType);

    // 非法：首尾空白 / 空串 / C0 / DEL / C1 / 65 ASCII — 零请求 + 固定错误 + 列表保值
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    const illegalCases = [
      "  ab",
      "ab  ",
      "   ",
      "\u0001ab", // C0
      "ab\u007F", // DEL U+007F
      "ab\u0080", // C1 U+0080
      "a".repeat(65),
    ];
    for (const bad of illegalCases) {
      await searchInput.fill(bad);
      const s0 = state.searchLog.length;
      const p0 = state.pageLog.length;
      await searchApply.click();
      await expect(
        page.getByTestId("editor-state-revision-search-error"),
      ).toHaveText(MSG_SEARCH_QUERY_INVALID);
      expect(state.searchLog.length).toBe(s0);
      expect(state.pageLog.length).toBe(p0);
      await expect(
        page.getByTestId("editor-state-revision-item-0"),
      ).toBeVisible();
      // 错误不得反射原值
      const errText = await page
        .getByTestId("editor-state-revision-search-error")
        .innerText();
      expect(errText).not.toContain(bad.trim() || "\u0001");
      expect(errText).not.toContain(bad);
    }

    // 64 个 astral Unicode 码点合法：UTF-16 .length=128 不得误拒；原样 POST +1
    const ASTRAL_CP = "\u{1F600}";
    const astral64 = ASTRAL_CP.repeat(64);
    const astral65 = ASTRAL_CP.repeat(65);
    expect([...astral64].length).toBe(64);
    expect(astral64.length).toBe(128);
    expect([...astral65].length).toBe(65);
    expect(astral65.length).toBe(130);
    const pageBeforeAstral = state.pageLog.length;
    const searchBeforeAstral = state.searchLog.length;
    await searchInput.fill(astral64);
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            astral64,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(state.searchLog.length).toBe(searchBeforeAstral + 1);
    expect(state.pageLog.length).toBe(pageBeforeAstral);
    const astralHit = state.searchLog[state.searchLog.length - 1];
    expect(astralHit.query).toBe(astral64);
    expect(astralHit.body).toEqual({ query: astral64 });
    expect(astralHit.postData).toBe(JSON.stringify({ query: astral64 }));
    expect(astralHit.queryKeys).toEqual([]);
    expect(astralHit.search).toBe("");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-empty")).toHaveText(
      MSG_SEARCH_EMPTY,
    );

    // 65 个 astral 码点非法：零 search/page；已应用 astral64 与空列表保留
    await searchInput.fill(astral65);
    const astral65SearchBefore = state.searchLog.length;
    const astral65PageBefore = state.pageLog.length;
    await searchApply.click();
    await expect(
      page.getByTestId("editor-state-revision-search-error"),
    ).toHaveText(MSG_SEARCH_QUERY_INVALID);
    expect(state.searchLog.length).toBe(astral65SearchBefore);
    expect(state.pageLog.length).toBe(astral65PageBefore);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-empty")).toHaveText(
      MSG_SEARCH_EMPTY,
    );
    const astral65Err = await page
      .getByTestId("editor-state-revision-search-error")
      .innerText();
    expect(astral65Err).not.toContain(astral65);
    expect(astral65Err).not.toContain(ASTRAL_CP);

    // 有效搜索：精确 POST +1，无 URL query，精确 body 键序
    await searchInput.fill(SEARCH_Q);
    const searchBefore = state.searchLog.length;
    const pageBefore = state.pageLog.length;
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(state.searchLog.length).toBe(searchBefore + 1);
    expect(state.pageLog.length).toBe(pageBefore);
    const hit = state.searchLog[state.searchLog.length - 1];
    expect(hit.method).toBe("POST");
    expect(hit.path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/search`,
    );
    expect(hit.queryKeys).toEqual([]);
    expect(hit.search).toBe("");
    expect(hit.query).toBe(SEARCH_Q);
    expect(hit.sourceKind).toBeNull();
    expect(hit.createdFrom).toBeNull();
    expect(hit.createdBefore).toBeNull();
    expect(hit.bodyKeys).toEqual(["query"]);
    expect(hit.body).toEqual({ query: SEARCH_Q });
    expect(hit.postData).toBe(JSON.stringify({ query: SEARCH_Q }));

    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    // 严格最多 20、唯一、无加载更多
    for (let i = 0; i < 20; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("editor-state-revision-item-20")).toHaveCount(
      0,
    );
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-search-error"),
    ).toHaveCount(0);

    // 同值再点搜索：零重发（不得用同值零重发冒充坏响应请求）
    const sameBefore = state.searchLog.length;
    await searchApply.click();
    expect(state.searchLog.length).toBe(sameBefore);

    // 严格 parser：真实坏响应 — 独立 query 各精确 POST +1；固定失败；不回退 page
    const validOne = makeValidSearchMeta(9101);
    const parserCases: Array<{ query: string; body: unknown }> = [
      {
        // 顶层额外键
        query: "PARSER_TOP_EXTRA",
        body: { items: [validOne], extra: true },
      },
      {
        // item 缺少五键之一（createdAt）
        query: "PARSER_ITEM_MISS",
        body: {
          items: [
            {
              revisionId: seedRevisionId(9102),
              stateVersion: seedStateVersion(9102),
              snapshotBytes: 12,
              sourceKind: "task",
            },
          ],
        },
      },
      {
        // item 多额外键
        query: "PARSER_ITEM_EXTRA",
        body: {
          items: [{ ...makeValidSearchMeta(9103), leaked: "x" }],
        },
      },
      {
        // 两个 item 同一 revisionId
        query: "PARSER_DUP_ID",
        body: {
          items: [
            makeValidSearchMeta(9104),
            {
              ...makeValidSearchMeta(9104),
              stateVersion: seedStateVersion(9199),
            },
          ],
        },
      },
      {
        // 21 个合法且 ID 唯一
        query: "PARSER_OVER_20",
        body: {
          items: Array.from({ length: 21 }, (_, i) =>
            makeValidSearchMeta(9200 + i),
          ),
        },
      },
    ];
    for (const c of parserCases) {
      state.searchResponseByQuery[c.query] = c.body;
      const pageBeforeBad = state.pageLog.length;
      const hitBeforeBad = searchHitCountForFilter(
        state,
        TECH_A,
        c.query,
        null,
        null,
        null,
      );
      const completeBeforeBad = searchCompleteCountForFilter(
        state,
        TECH_A,
        c.query,
        null,
        null,
        null,
      );
      await searchInput.fill(c.query);
      await searchApply.click();
      await expect
        .poll(
          () =>
            searchCompleteCountForFilter(
              state,
              TECH_A,
              c.query,
              null,
              null,
              null,
            ),
          { timeout: 10_000 },
        )
        .toBe(completeBeforeBad + 1);
      expect(
        searchHitCountForFilter(state, TECH_A, c.query, null, null, null),
      ).toBe(hitBeforeBad + 1);
      expect(state.pageLog.length).toBe(pageBeforeBad);
      const badHit = state.searchLog[state.searchLog.length - 1];
      expect(badHit.method).toBe("POST");
      expect(badHit.query).toBe(c.query);
      expect(badHit.queryKeys).toEqual([]);
      expect(badHit.search).toBe("");
      await expect(
        page.getByTestId("editor-state-revision-list-error"),
      ).toHaveText(MSG_SEARCH_FAIL);
      await expect(
        page.getByTestId("editor-state-revision-item-0"),
      ).toHaveCount(0);
      await expect(searchInput).toHaveValue(c.query);
      await expect(
        page.getByTestId("editor-state-revision-search-active"),
      ).toBeVisible();
      await expect(
        page.getByTestId("editor-state-revision-load-more"),
      ).toHaveCount(0);
      // 失败文案不得反射 query 或坏 shape 关键字
      const failText = await page
        .getByTestId("editor-state-revision-list-error")
        .innerText();
      expect(failText).not.toContain(c.query);
      expect(failText).not.toContain("extra");
      expect(failText).not.toContain("leaked");
    }

    // 恢复正常 byQuery 后：刷新重试独立 query 成功（空结果合法）
    delete state.searchResponseByQuery["PARSER_TOP_EXTRA"];
    delete state.searchResponseByQuery["PARSER_ITEM_MISS"];
    delete state.searchResponseByQuery["PARSER_ITEM_EXTRA"];
    delete state.searchResponseByQuery["PARSER_DUP_ID"];
    delete state.searchResponseByQuery["PARSER_OVER_20"];
    // 当前已应用为 PARSER_OVER_20；清除 override 后刷新应 POST +1 且空态成功
    const recoverQuery = "PARSER_OVER_20";
    const recoverHitBefore = searchHitCountForFilter(
      state,
      TECH_A,
      recoverQuery,
      null,
      null,
      null,
    );
    const recoverPageBefore = state.pageLog.length;
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            recoverQuery,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(recoverHitBefore + 1);
    expect(
      searchHitCountForFilter(state, TECH_A, recoverQuery, null, null, null),
    ).toBe(recoverHitBefore + 1);
    expect(state.pageLog.length).toBe(recoverPageBefore);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(page.getByTestId("editor-state-revision-empty")).toHaveText(
      MSG_SEARCH_EMPTY,
    );
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    // 空态
    await searchInput.fill(EMPTY_Q);
    const emptyBefore = searchHitCountForFilter(
      state,
      TECH_A,
      EMPTY_Q,
      null,
      null,
      null,
    );
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            EMPTY_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(emptyBefore + 1);
    expect(
      searchHitCountForFilter(state, TECH_A, EMPTY_Q, null, null, null),
    ).toBe(emptyBefore + 1);
    await expect(page.getByTestId("editor-state-revision-empty")).toHaveText(
      MSG_SEARCH_EMPTY,
    );
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    // 不得回退未筛选列表
    expect(state.pageLog.length).toBe(pageBefore);

    // 失败：固定中文、列表空、已应用关键词保留；刷新重试仍 search POST
    state.searchModeByProject[TECH_A] = { kind: "http_error", status: 500 };
    await searchInput.fill(SEARCH_Q);
    const failBefore = searchCompleteCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(failBefore + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_SEARCH_FAIL);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    await expect(searchInput).toHaveValue(SEARCH_Q);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    state.searchModeByProject[TECH_A] = { kind: "ok" };
    const refreshBefore = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    const pageBeforeRefresh = state.pageLog.length;
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          searchHitCountForFilter(state, TECH_A, SEARCH_Q, null, null, null),
        { timeout: 10_000 },
      )
      .toBe(refreshBefore + 1);
    expect(state.pageLog.length).toBe(pageBeforeRefresh);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);

    // 清除搜索：恢复 page 第一页；本来非空 → 精确 +1 page
    const clearPageBefore = state.pageLog.length;
    const clearSearchBefore = state.searchLog.length;
    const clearPageCompleteBefore = pageCompleteCount(state, TECH_A);
    await searchClear.click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(clearPageCompleteBefore + 1);
    expect(state.pageLog.length).toBe(clearPageBefore + 1);
    expect(state.searchLog.length).toBe(clearSearchBefore);
    const clearHit = state.pageLog[state.pageLog.length - 1];
    expect(clearHit.cursor).toBeNull();
    expect(clearHit.sourceKind).toBeNull();
    expect(clearHit.method).toBe("GET");
    await expect(searchInput).toHaveValue("");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-search-error"),
    ).toHaveCount(0);
    // 清除后可出现加载更多（21>10）
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 全空再清除：零请求
    const emptyClearPage = state.pageLog.length;
    const emptyClearSearch = state.searchLog.length;
    await searchClear.click();
    expect(state.pageLog.length).toBe(emptyClearPage);
    expect(state.searchLog.length).toBe(emptyClearSearch);

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);

    // 关键词不得进入 URL/存储/Cookie/console/页面固定文案
    const persist = await page.evaluate(() => ({
      href: location.href,
      cookie: document.cookie,
      ls: Object.keys(localStorage)
        .map((k) => `${k}=${localStorage.getItem(k)}`)
        .join("\n"),
      ss: Object.keys(sessionStorage)
        .map((k) => `${k}=${sessionStorage.getItem(k)}`)
        .join("\n"),
      body: document.body?.innerText ?? "",
    }));
    for (const blob of [
      persist.href,
      persist.cookie,
      persist.ls,
      persist.ss,
    ]) {
      expect(blob).not.toContain(SEARCH_Q);
      expect(blob).not.toContain(SEARCH_MARK);
      expect(blob).not.toContain(EMPTY_Q);
    }
    expect(persist.body).not.toContain(SEARCH_MARK);
    expect(persist.body).not.toContain(MSG_SEARCH_FAIL);
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain(SEARCH_Q);
    expect(consoleBlob).not.toContain(SEARCH_MARK);
    expect(seeded.length).toBe(21);
  });
});

test.describe("P12F-F-B 技术标组合与迟到隔离", () => {
  test.use({ timezoneId: "Asia/Shanghai" });

  test("P12F-F-B 技术标：来源+时间 body、筛选变化保持 query、折叠保留、项目切换重置、arrived/complete gate 与旧 success/catch/finally 零污染", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const SEARCH_MARK = "P12FFB_COMBO_MARK";
    const SEARCH_Q = "P12FFB_COMBO";
    const seededA = seedRevisions(
      state,
      TECH_A,
      12,
      Array.from({ length: 11 }, () => "task").concat(["revise"]),
    );
    syncRevisionCreatedAts(state, TECH_A, "2026-07-16T00:00:00.000Z", 1);
    for (const it of seededA) {
      if (it.sourceKind === "task") {
        stampSearchableMarker(state, it.revisionId, SEARCH_MARK, "tech");
      }
    }
    seedRevisions(
      state,
      TECH_B,
      5,
      Array.from({ length: 5 }, () => "callback"),
    );
    syncRevisionCreatedAts(state, TECH_B, "2026-07-17T00:00:00.000Z", 1);

    const UTC_FROM = "2026-07-16T00:00:00.000Z";
    const UTC_BEFORE = "2026-07-16T00:10:00.000Z";
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, TECH_A)).toBe(1);

    const filter = page.getByTestId("editor-state-revision-source-filter");
    const fromInput = page.getByTestId("editor-state-revision-created-from");
    const beforeInput = page.getByTestId(
      "editor-state-revision-created-before",
    );
    const timeApply = page.getByTestId("editor-state-revision-time-apply");
    const searchInput = page.getByTestId("editor-state-revision-search-input");
    const searchApply = page.getByTestId("editor-state-revision-search-apply");

    // 来源 + 双边时间 + 搜索：body 键序 query → sourceKind → createdFrom → createdBefore
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(() => pageHitCountForSource(state, TECH_A, "task"), {
        timeout: 10_000,
      })
      .toBe(1);
    await fromInput.fill("2026-07-16T08:00");
    await beforeInput.fill("2026-07-16T08:10");
    await timeApply.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            TECH_A,
            "task",
            UTC_FROM,
            UTC_BEFORE,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);

    await searchInput.fill(SEARCH_Q);
    const comboSearchBefore = state.searchLog.length;
    const pageBeforeCombo = state.pageLog.length;
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(state.searchLog.length).toBe(comboSearchBefore + 1);
    expect(state.pageLog.length).toBe(pageBeforeCombo);
    const comboHit = state.searchLog[state.searchLog.length - 1];
    expect(comboHit.method).toBe("POST");
    expect(comboHit.queryKeys).toEqual([]);
    expect(comboHit.search).toBe("");
    expect(comboHit.bodyKeys).toEqual([
      "query",
      "sourceKind",
      "createdFrom",
      "createdBefore",
    ]);
    expect(comboHit.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "task",
      createdFrom: UTC_FROM,
      createdBefore: UTC_BEFORE,
    });
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);

    // 来源变化：保持 query，重新 POST search（非 page）
    const srcBefore = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      "revise",
      UTC_FROM,
      UTC_BEFORE,
    );
    const pageBeforeSrc = state.pageLog.length;
    await filter.selectOption({ label: "智能修订" });
    await expect
      .poll(
        () =>
          searchHitCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "revise",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(srcBefore + 1);
    expect(state.pageLog.length).toBe(pageBeforeSrc);
    expect(state.searchLog[state.searchLog.length - 1].query).toBe(SEARCH_Q);
    await expect(searchInput).toHaveValue(SEARCH_Q);

    // 切回来源 task 继续
    await filter.selectOption({ label: "任务写入" });
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(2);

    // 时间变化保持 query
    await beforeInput.fill("2026-07-16T08:05");
    const UTC_BEFORE_0805 = "2026-07-16T00:05:00.000Z";
    const timeBefore = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      "task",
      UTC_FROM,
      UTC_BEFORE_0805,
    );
    await timeApply.click();
    await expect
      .poll(
        () =>
          searchHitCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE_0805,
          ),
        { timeout: 10_000 },
      )
      .toBe(timeBefore + 1);
    expect(state.searchLog[state.searchLog.length - 1].query).toBe(SEARCH_Q);
    expect(state.searchLog[state.searchLog.length - 1].createdBefore).toBe(
      UTC_BEFORE_0805,
    );

    // 恢复 08:10 便于后续
    await beforeInput.fill("2026-07-16T08:10");
    await timeApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(3);

    // 折叠再展开：保留草稿/已应用关键词/来源/时间，重新 POST
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    const reopenBefore = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      "task",
      UTC_FROM,
      UTC_BEFORE,
    );
    const pageBeforeReopen = state.pageLog.length;
    await expandRevisionPanel(page);
    await expect
      .poll(
        () =>
          searchHitCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(reopenBefore + 1);
    expect(state.pageLog.length).toBe(pageBeforeReopen);
    await expect(searchInput).toHaveValue(SEARCH_Q);
    await expect(filter).toHaveValue("task");
    await expect(fromInput).toHaveValue("2026-07-16T08:00");
    await expect(beforeInput).toHaveValue("2026-07-16T08:10");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    // arrived+complete 迟到：挂起 search → 折叠释放不得污染
    const lateGate = createHoldGate();
    state.searchModeByProject[TECH_A] = { kind: "hold", gate: lateGate };
    await page.getByTestId("editor-state-revision-refresh").click();
    await lateGate.waitUntilEntered(1);
    const arrivedHeld = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      "task",
      UTC_FROM,
      UTC_BEFORE,
    );
    const completeHeld = searchCompleteCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      "task",
      UTC_FROM,
      UTC_BEFORE,
    );
    await expect(searchInput).toBeDisabled();
    await expect(searchApply).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-clear"),
    ).toBeDisabled();
    await expect(filter).toBeDisabled();
    await page.getByTestId("editor-state-revision-toggle").click();
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    lateGate.release();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(completeHeld + 1);
    expect(
      searchHitCountForFilter(
        state,
        TECH_A,
        SEARCH_Q,
        "task",
        UTC_FROM,
        UTC_BEFORE,
      ),
    ).toBe(arrivedHeld);
    // 折叠态：无 body，旧结果不得写回
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );

    // 重开后正常；再测项目切换：旧 A catch 与 B page loading 真实重叠
    state.searchModeByProject[TECH_A] = { kind: "ok" };
    await expandRevisionPanel(page);
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            "task",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(completeHeld + 2);
    await expect(searchInput).toHaveValue(SEARCH_Q);

    // A search 进入 hold；切到 B 后首次展开并使 B page 独立 hold — 不得先等 A 完成
    const aCatchGate = createHoldGate();
    const bPageGate = createHoldGate();
    state.searchModeByProject[TECH_A] = { kind: "hold", gate: aCatchGate };
    await page.getByTestId("editor-state-revision-refresh").click();
    await aCatchGate.waitUntilEntered(1);
    const aArrivedAtHold = searchHitCount(state, TECH_A);
    const aCompleteAtHold = searchCompleteCount(state, TECH_A);
    const aSearchLogAtHold = state.searchLog.length;
    expect(aArrivedAtHold).toBe(aCompleteAtHold + 1);

    await openWorkspace(page, "tech", TECH_B);
    // 新项目默认折叠，搜索控件不在 DOM；A 仍挂起
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-search-input"),
    ).toHaveCount(0);
    expect(searchCompleteCount(state, TECH_A)).toBe(aCompleteAtHold);
    expect(pageHitCount(state, TECH_B)).toBe(0);

    // B 首次展开前挂起 page，制造与旧 A 的真实重叠窗口
    state.pageModeByProject[TECH_B] = { kind: "hold", gate: bPageGate };
    await expandRevisionPanel(page);
    await bPageGate.waitUntilEntered(1);
    expect(pageHitCount(state, TECH_B)).toBe(1);
    expect(pageCompleteCount(state, TECH_B)).toBe(0);
    // B loading 与搜索/来源/时间控件真实 disabled
    await expect(
      page.getByTestId("editor-state-revision-list-loading"),
    ).toBeVisible();
    const bSearchInput = page.getByTestId("editor-state-revision-search-input");
    const bSearchApply = page.getByTestId("editor-state-revision-search-apply");
    const bSearchClear = page.getByTestId("editor-state-revision-search-clear");
    const bFilter = page.getByTestId("editor-state-revision-source-filter");
    const bFrom = page.getByTestId("editor-state-revision-created-from");
    const bBefore = page.getByTestId("editor-state-revision-created-before");
    await expect(bSearchInput).toBeDisabled();
    await expect(bSearchApply).toBeDisabled();
    await expect(bSearchClear).toBeDisabled();
    await expect(bFilter).toBeDisabled();
    await expect(bFrom).toBeDisabled();
    await expect(bBefore).toBeDisabled();
    await expect(bSearchInput).toHaveValue("");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toHaveCount(0);
    await expect(bFilter).toHaveValue("");

    // A gate 挂起后注入非法 shape；释放后路由读取 override → parser catch
    state.searchResponseOverride = {
      items: [makeValidSearchMeta(9301)],
      extra: "late-catch",
    };
    aCatchGate.release();
    await expect
      .poll(() => searchCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(aCompleteAtHold + 1);
    // arrived 已在 gate 前入账；释放只 complete，不得再 +1 到达
    expect(searchHitCount(state, TECH_A)).toBe(aArrivedAtHold);
    expect(state.searchLog.length).toBe(aSearchLogAtHold);

    // B gate 尚未释放：旧 A catch 不得写入 B 的 MSG_SEARCH_FAIL/items；旧 finally 不得清 B loading/disabled
    expect(pageCompleteCount(state, TECH_B)).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-list-loading"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(page.getByTestId("editor-state-revision-item-0")).toHaveCount(
      0,
    );
    await expect(bSearchInput).toBeDisabled();
    await expect(bSearchApply).toBeDisabled();
    await expect(bSearchClear).toBeDisabled();
    await expect(bFilter).toBeDisabled();
    await expect(bFrom).toBeDisabled();
    await expect(bBefore).toBeDisabled();
    // 旧 A 完成不得触发 B 额外 page/search
    expect(pageHitCount(state, TECH_B)).toBe(1);
    expect(searchHitCount(state, TECH_B)).toBe(0);

    // 释放 B：精确 page complete +1；正常展示且搜索草稿/已应用/来源/时间为空
    state.searchResponseOverride = null;
    state.searchModeByProject[TECH_A] = { kind: "ok" };
    bPageGate.release();
    state.pageModeByProject[TECH_B] = { kind: "ok" };
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(1);
    expect(pageHitCount(state, TECH_B)).toBe(1);
    expect(searchHitCount(state, TECH_B)).toBe(0);
    const bFirst = state.pageLog[state.pageLog.length - 1];
    expect(bFirst.projectId).toBe(TECH_B);
    expect(bFirst.cursor).toBeNull();
    expect(bFirst.sourceKind).toBeNull();
    expect(bFirst.createdFrom).toBeNull();
    expect(bFirst.createdBefore).toBeNull();
    expect(bFirst.queryKeys).toEqual([]);
    await expect(
      page.getByTestId("editor-state-revision-list-loading"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(bSearchInput).toBeEnabled();
    await expect(bSearchInput).toHaveValue("");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toHaveCount(0);
    await expect(bFilter).toHaveValue("");
    await expect(bFrom).toHaveValue("");
    await expect(bBefore).toHaveValue("");

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

test.describe("P12F-F-B 商务标共享与恢复", () => {
  test.use({ timezoneId: "Asia/Shanghai" });

  test("P12F-F-B 商务标：共享入口、组合条件、搜索结果现有操作、恢复成功/重载失败后仍 search POST、项目重置与 URL/存储/Cookie/console/其它请求零关键词泄漏", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const SEARCH_MARK = "P12FFB_BIZ_HIT_MARK";
    const SEARCH_Q = "P12FFB_BIZ_HIT";
    const seeded = seedRevisions(
      state,
      BIZ_A,
      6,
      Array.from({ length: 6 }, () => "callback"),
    );
    syncRevisionCreatedAts(state, BIZ_A, "2026-07-16T00:00:00.000Z", 1);
    for (const it of seeded) {
      stampSearchableMarker(state, it.revisionId, SEARCH_MARK, "biz");
    }
    seedRevisions(
      state,
      BIZ_B,
      3,
      Array.from({ length: 3 }, () => "task"),
    );

    const UTC_FROM = "2026-07-16T00:00:00.000Z";
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);
    await page.clock.install();

    await openWorkspace(page, "biz", BIZ_A);
    expect(state.searchLog.length).toBe(0);
    await expandRevisionPanel(page);
    await expect.poll(() => pageCompleteCount(state, BIZ_A)).toBe(1);

    const filter = page.getByTestId("editor-state-revision-source-filter");
    const fromInput = page.getByTestId("editor-state-revision-created-from");
    const timeApply = page.getByTestId("editor-state-revision-time-apply");
    const searchInput = page.getByTestId("editor-state-revision-search-input");
    const searchApply = page.getByTestId("editor-state-revision-search-apply");
    const searchClear = page.getByTestId("editor-state-revision-search-clear");

    await expect(searchInput).toBeVisible();
    await expect(searchApply).toBeVisible();
    await expect(searchClear).toBeVisible();

    await filter.selectOption({ label: "解析回传" });
    await expect
      .poll(() => pageHitCountForSource(state, BIZ_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(1);
    await fromInput.fill("2026-07-16T08:00");
    await timeApply.click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);

    await searchInput.fill(SEARCH_Q);
    const pageBeforeSearch = state.pageLog.length;
    await searchApply.click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    expect(state.pageLog.length).toBe(pageBeforeSearch);
    const bizHit = state.searchLog[state.searchLog.length - 1];
    expect(bizHit.bodyKeys).toEqual([
      "query",
      "sourceKind",
      "createdFrom",
    ]);
    expect(bizHit.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "callback",
      createdFrom: UTC_FROM,
    });
    expect(bizHit.queryKeys).toEqual([]);
    expect(bizHit.search).toBe("");
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toHaveCount(0);

    // 搜索结果：摘要可用
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(() => state.detailCompleteLog.length, { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();

    // 恢复成功后仍 search POST（保留来源+时间+query）
    const restoreGate = createHoldGate();
    state.restoreMode = { kind: "gate", gate: restoreGate, then: "ok" };
    const searchBeforeRestore = searchHitCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      null,
    );
    const pageBeforeRestore = state.pageLog.length;
    const getsBefore = state.editorGetLog.filter(
      (g) => g.projectId === BIZ_A,
    ).length;
    const expectedAtConfirm = state.projects[BIZ_A].stateVersion;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await restoreGate.waitUntilEntered(1);
    await expect(searchInput).toBeDisabled();
    await expect(searchApply).toBeDisabled();
    await expect(searchClear).toBeDisabled();
    await expect(filter).toBeDisabled();
    expect(state.restoreLog.length).toBe(0);

    restoreGate.release();
    state.restoreMode = { kind: "ok" };
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(1);
    expect(state.restoreLog[0].body).toEqual({
      expectedStateVersion: expectedAtConfirm,
    });
    await expect
      .poll(
        () =>
          state.editorGetLog.filter((g) => g.projectId === BIZ_A).length -
          getsBefore,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect
      .poll(
        () =>
          searchHitCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchBeforeRestore + 1);
    expect(state.pageLog.length).toBe(pageBeforeRestore);
    await expect(searchInput).toHaveValue(SEARCH_Q);
    await expect(filter).toHaveValue("callback");
    await expect(fromInput).toHaveValue("2026-07-16T08:00");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    // reload_failed：恢复 POST=1 后 GET 失败，历史重载仍 search POST
    state.nextEditorGetFail = true;
    const searchBeforeReloadFail = searchHitCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      null,
    );
    const restoreBeforeFail = state.restoreLog.length;
    await page.getByTestId("editor-state-revision-restore-0").click();
    await page.getByTestId("editor-state-revision-confirm-restore-0").click();
    await expect
      .poll(() => state.restoreLog.length, { timeout: 10_000 })
      .toBe(restoreBeforeFail + 1);
    await expect(
      page.getByTestId("editor-state-revision-status"),
    ).toContainText(MSG_RESTORE_RELOAD_FAIL);
    await expect
      .poll(
        () =>
          searchHitCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchBeforeReloadFail + 1);
    expect(
      state.searchLog[state.searchLog.length - 1].query,
    ).toBe(SEARCH_Q);

    // 项目切换重置
    await openWorkspace(page, "biz", BIZ_B);
    await expect(page.getByTestId("editor-state-revision-body")).toHaveCount(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_B), { timeout: 10_000 })
      .toBe(1);
    expect(searchHitCount(state, BIZ_B)).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-search-input"),
    ).toHaveValue("");
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toHaveCount(0);

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);

    const persist = await page.evaluate(() => ({
      href: location.href,
      cookie: document.cookie,
      ls: Object.keys(localStorage)
        .map((k) => `${k}=${localStorage.getItem(k)}`)
        .join("\n"),
      ss: Object.keys(sessionStorage)
        .map((k) => `${k}=${sessionStorage.getItem(k)}`)
        .join("\n"),
      body: document.body?.innerText ?? "",
    }));
    for (const blob of [
      persist.href,
      persist.cookie,
      persist.ls,
      persist.ss,
    ]) {
      expect(blob).not.toContain(SEARCH_Q);
      expect(blob).not.toContain(SEARCH_MARK);
    }
    // 输入控件已空，页面正文也不得残留标记
    expect(persist.body).not.toContain(SEARCH_MARK);
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain(SEARCH_Q);
    expect(consoleBlob).not.toContain(SEARCH_MARK);
    // 其它请求路径不得携带关键词
    for (const h of state.pageLog) {
      expect(h.search).not.toContain(SEARCH_Q);
      expect(h.postData ?? "").not.toContain(SEARCH_Q);
    }
    for (const h of state.searchLog) {
      // search body 允许 query；URL 不允许
      expect(h.search).toBe("");
      expect(h.queryKeys).toEqual([]);
    }
    expect(seeded.length).toBe(6);
  });
});


// ---------------------------------------------------------------------------
// P12F-G-B 单条修订删除前端（三用例互不 serial 跳过）
// ---------------------------------------------------------------------------

test.describe("P12F-G-B 技术标确认成功失败与重载", () => {
  // 独立 describe，禁止跨用例 serial 首失败 did-not-run
  test("P12F-G-B 技术标：确认前/取消零 DELETE；确认精确一次无 query/body；成功普通页与搜索重载；404/500 固定失败保值；零写旁路", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const SEARCH_MARK = "P12FGB_TECH_DEL_MARK";
    const SEARCH_Q = "P12FGB_TECH_DEL";
    const seeded = seedRevisions(
      state,
      TECH_A,
      12,
      Array.from({ length: 12 }, (_, i) => (i % 2 === 0 ? "task" : "revise")),
    );
    for (let i = 0; i < 8; i++) {
      stampSearchableMarker(state, seeded[i].revisionId, SEARCH_MARK, "tech");
    }
    const target0 = seeded[0].revisionId;
    // 删除 index0 后探针剩余 11 条，顺序为 seed 原 1..11
    const orderAfterDel0 = seeded.slice(1).map((r) => r.revisionId);
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    expect(state.deleteLog.length).toBe(0);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeVisible();

    // 默认/展开后：DELETE=0
    expect(state.deleteLog.length).toBe(0);
    expect(state.deleteCompleteLog.length).toBe(0);

    // 点击删除：仅进入确认，DELETE 仍为 0
    await page.getByTestId("editor-state-revision-delete-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-delete-confirm-text-0"),
    ).toHaveText(DELETE_CONFIRM);
    expect(state.deleteLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-toggle"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-source-filter"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-input"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-apply"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-load-more"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-delete-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-summary-1"),
    ).toBeDisabled();

    // 取消：零 DELETE，确认关闭，列表不变
    await page.getByTestId("editor-state-revision-cancel-delete-0").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toHaveCount(0);
    expect(state.deleteLog.length).toBe(0);
    expect(state.deleteCompleteLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-toggle"),
    ).toBeEnabled();

    // 确认删除成功：精确一次 DELETE 无 query/body；普通页重载第一批
    const pageBeforeOk = pageHitCount(state, TECH_A);
    const pageCompleteBeforeOk = pageCompleteCount(state, TECH_A);
    const editorGetsBefore = state.editorGetLog.length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const cpBefore = state.checkpointCreateLog.length;
    const searchBefore = state.searchLog.length;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, target0), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(deleteHitCount(state, TECH_A, target0)).toBe(1);
    expect(state.deleteLog.length).toBe(1);
    const delHit = state.deleteLog[0];
    expect(delHit.method).toBe("DELETE");
    expect(delHit.path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/${target0}`,
    );
    expect(delHit.queryKeys).toEqual([]);
    expect(delHit.search).toBe("");
    expect(delHit.postData).toBeNull();
    expect(state.deleteCompleteLog[0].status).toBe(204);
    expect(
      state.revisions[TECH_A].some((r) => r.revisionId === target0),
    ).toBe(false);
    expect(state.details[target0]).toBeUndefined();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageCompleteBeforeOk + 1);
    expect(pageHitCount(state, TECH_A)).toBe(pageBeforeOk + 1);
    const reloadHit = state.pageLog[state.pageLog.length - 1];
    expect(reloadHit.projectId).toBe(TECH_A);
    expect(reloadHit.cursor).toBeNull();
    expect(reloadHit.method).toBe("GET");
    expect(reloadHit.queryKeys).toEqual([]);
    expect(state.searchLog.length).toBe(searchBefore);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    for (let i = 0; i < 10; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-item-${i}`),
      ).toBeVisible();
    }
    expect(
      state.revisions[TECH_A].map((r) => r.revisionId),
    ).toEqual(orderAfterDel0);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.externalHits).toEqual([]);

    // 加载更多后删除：成功只回第一批
    await page.getByTestId("editor-state-revision-load-more").click();
    await expect
      .poll(
        () =>
          pageCompleteCountForSourceCursor(
            state,
            TECH_A,
            null,
            PAGE_CURSOR_SECOND,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toBeVisible();
    const pageBeforeSecondDel = pageHitCount(state, TECH_A);
    expect(state.revisions[TECH_A].length).toBe(11);
    const delSecondId = state.revisions[TECH_A][0].revisionId;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, delSecondId), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageCompleteBeforeOk + 3);
    await expect(
      page.getByTestId("editor-state-revision-item-10"),
    ).toHaveCount(0);
    const afterSecondReload = state.pageLog[state.pageLog.length - 1];
    expect(afterSecondReload.cursor).toBeNull();
    expect(pageHitCount(state, TECH_A)).toBe(pageBeforeSecondDel + 1);

    // 搜索态成功：保留 query 精确一次 POST search，无 page
    for (const r of state.revisions[TECH_A]) {
      stampSearchableMarker(state, r.revisionId, SEARCH_MARK, "tech");
    }
    const searchInput = page.getByTestId("editor-state-revision-search-input");
    await searchInput.fill(SEARCH_Q);
    await page.getByTestId("editor-state-revision-search-apply").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    // 精确绑定：探针列表长度、第 0 项 ID、snapshot 含标记 → DOM index 0
    const probeAfterPageDeletes = state.revisions[TECH_A];
    expect(probeAfterPageDeletes.length).toBe(10);
    const searchTargetId = probeAfterPageDeletes[0].revisionId;
    expect(
      JSON.stringify(state.details[searchTargetId].snapshot),
    ).toContain(SEARCH_MARK);
    const pageBeforeSearchDel = pageHitCount(state, TECH_A);
    const searchBeforeDel = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    const searchCompleteBeforeDel = searchCompleteCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    const editorGetsBeforeSearchDel = state.editorGetLog.length;
    const putBeforeSearchDel = state.putLog.length;
    const restoreBeforeSearchDel = state.restoreLog.length;
    const cpBeforeSearchDel = state.checkpointCreateLog.length;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, searchTargetId), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(deleteHitCount(state, TECH_A, searchTargetId)).toBe(1);
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeDel + 1);
    expect(
      searchHitCountForFilter(state, TECH_A, SEARCH_Q, null, null, null),
    ).toBe(searchBeforeDel + 1);
    expect(pageHitCount(state, TECH_A)).toBe(pageBeforeSearchDel);
    const searchReload = state.searchLog[state.searchLog.length - 1];
    expect(searchReload.method).toBe("POST");
    expect(searchReload.query).toBe(SEARCH_Q);
    expect(searchReload.queryKeys).toEqual([]);
    expect(searchReload.bodyKeys).toEqual(["query"]);
    expect(searchReload.body).toEqual({ query: SEARCH_Q });
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    expect(
      state.revisions[TECH_A].some(
        (r) => r.revisionId === searchTargetId,
      ),
    ).toBe(false);
    expect(state.editorGetLog.length).toBe(editorGetsBeforeSearchDel);
    expect(state.putLog.length).toBe(putBeforeSearchDel);
    expect(state.restoreLog.length).toBe(restoreBeforeSearchDel);
    expect(state.checkpointCreateLog.length).toBe(cpBeforeSearchDel);

    // 搜索态 DELETE 204 成功 + search 重载固定 HTTP 失败：双文案并存，不回退 page
    for (const r of state.revisions[TECH_A]) {
      stampSearchableMarker(state, r.revisionId, SEARCH_MARK, "tech");
    }
    const probeBeforeSearchReloadFail = state.revisions[TECH_A];
    expect(probeBeforeSearchReloadFail.length).toBe(9);
    const searchFailTargetId = probeBeforeSearchReloadFail[0].revisionId;
    expect(
      JSON.stringify(state.details[searchFailTargetId].snapshot),
    ).toContain(SEARCH_MARK);
    state.searchModeByProject[TECH_A] = {
      kind: "http_error",
      status: 500,
    };
    const delCompleteBeforeSearchReloadFail = deleteCompleteCount(
      state,
      TECH_A,
      searchFailTargetId,
    );
    const delHitBeforeSearchReloadFail = deleteHitCount(
      state,
      TECH_A,
      searchFailTargetId,
    );
    const searchArrivedBeforeReloadFail = searchHitCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    const searchCompleteBeforeReloadFail = searchCompleteCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    const pageBeforeSearchReloadFail = pageHitCount(state, TECH_A);
    const pageCompleteBeforeSearchReloadFail = pageCompleteCount(
      state,
      TECH_A,
    );
    const editorGetsBeforeSearchReloadFail = state.editorGetLog.length;
    const putBeforeSearchReloadFail = state.putLog.length;
    const restoreBeforeSearchReloadFail = state.restoreLog.length;
    const cpBeforeSearchReloadFail = state.checkpointCreateLog.length;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(
        () => deleteCompleteCount(state, TECH_A, searchFailTargetId),
        { timeout: 10_000 },
      )
      .toBe(delCompleteBeforeSearchReloadFail + 1);
    expect(deleteHitCount(state, TECH_A, searchFailTargetId)).toBe(
      delHitBeforeSearchReloadFail + 1,
    );
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeReloadFail + 1);
    expect(
      searchHitCountForFilter(state, TECH_A, SEARCH_Q, null, null, null),
    ).toBe(searchArrivedBeforeReloadFail + 1);
    // DELETE 不重试；page 零新增
    expect(deleteHitCount(state, TECH_A, searchFailTargetId)).toBe(
      delHitBeforeSearchReloadFail + 1,
    );
    expect(pageHitCount(state, TECH_A)).toBe(pageBeforeSearchReloadFail);
    expect(pageCompleteCount(state, TECH_A)).toBe(
      pageCompleteBeforeSearchReloadFail,
    );
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_SEARCH_FAIL);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toHaveCount(0);
    expect(
      state.revisions[TECH_A].some(
        (r) => r.revisionId === searchFailTargetId,
      ),
    ).toBe(false);
    expect(state.editorGetLog.length).toBe(editorGetsBeforeSearchReloadFail);
    expect(state.putLog.length).toBe(putBeforeSearchReloadFail);
    expect(state.restoreLog.length).toBe(restoreBeforeSearchReloadFail);
    expect(state.checkpointCreateLog.length).toBe(cpBeforeSearchReloadFail);
    // 恢复 search 后显式刷新可重载
    state.searchModeByProject[TECH_A] = { kind: "ok" };
    const searchCompleteBeforeRecover = searchCompleteCountForFilter(
      state,
      TECH_A,
      SEARCH_Q,
      null,
      null,
      null,
    );
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            SEARCH_Q,
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeRecover + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    // 清除搜索回到 page 态
    const pageBeforeClearSearch = pageCompleteCount(state, TECH_A);
    await page.getByTestId("editor-state-revision-search-clear").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageBeforeClearSearch + 1);

    // 普通 page：DELETE 204 成功 + page 重载固定 HTTP 失败
    const probeBeforePageReloadFail = state.revisions[TECH_A];
    expect(probeBeforePageReloadFail.length).toBe(8);
    const pageReloadFailTargetId = probeBeforePageReloadFail[0].revisionId;
    state.pageModeByProject[TECH_A] = {
      kind: "http_error",
      status: 500,
    };
    const delCompleteBeforePageReloadFail = deleteCompleteCount(
      state,
      TECH_A,
      pageReloadFailTargetId,
    );
    const delHitBeforePageReloadFail = deleteHitCount(
      state,
      TECH_A,
      pageReloadFailTargetId,
    );
    const pageArrivedBeforeReloadFail = pageHitCount(state, TECH_A);
    const pageCompleteBeforeReloadFail = pageCompleteCount(state, TECH_A);
    const searchBeforePageReloadFail = state.searchLog.length;
    const editorGetsBeforePageReloadFail = state.editorGetLog.length;
    const putBeforePageReloadFail = state.putLog.length;
    const restoreBeforePageReloadFail = state.restoreLog.length;
    const cpBeforePageReloadFail = state.checkpointCreateLog.length;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(
        () => deleteCompleteCount(state, TECH_A, pageReloadFailTargetId),
        { timeout: 10_000 },
      )
      .toBe(delCompleteBeforePageReloadFail + 1);
    expect(deleteHitCount(state, TECH_A, pageReloadFailTargetId)).toBe(
      delHitBeforePageReloadFail + 1,
    );
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageCompleteBeforeReloadFail + 1);
    expect(pageHitCount(state, TECH_A)).toBe(pageArrivedBeforeReloadFail + 1);
    // DELETE 不重试
    expect(deleteHitCount(state, TECH_A, pageReloadFailTargetId)).toBe(
      delHitBeforePageReloadFail + 1,
    );
    expect(state.searchLog.length).toBe(searchBeforePageReloadFail);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_LIST_FAIL);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toHaveCount(0);
    expect(
      state.revisions[TECH_A].some(
        (r) => r.revisionId === pageReloadFailTargetId,
      ),
    ).toBe(false);
    expect(state.editorGetLog.length).toBe(editorGetsBeforePageReloadFail);
    expect(state.putLog.length).toBe(putBeforePageReloadFail);
    expect(state.restoreLog.length).toBe(restoreBeforePageReloadFail);
    expect(state.checkpointCreateLog.length).toBe(cpBeforePageReloadFail);
    // 恢复 page 后刷新可重载
    state.pageModeByProject[TECH_A] = { kind: "ok" };
    const pageCompleteBeforeRecover = pageCompleteCount(state, TECH_A);
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageCompleteBeforeRecover + 1);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();

    // 500/404 失败保值：可见 time/source 序列 + 探针 ID 顺序 + 计数不变 + 确认关闭 + 按钮启用
    const pageFailBase = pageCompleteCount(state, TECH_A);
    expect(state.revisions[TECH_A].length).toBe(7);
    const listIdsBeforeFail = state.revisions[TECH_A].map(
      (r) => r.revisionId,
    );
    expect(listIdsBeforeFail.length).toBe(7);
    const failTarget = state.revisions[TECH_A][0].revisionId;
    const visibleFailCount = 7;
    const timesBefore500: string[] = [];
    const sourcesBefore500: string[] = [];
    for (let i = 0; i < visibleFailCount; i++) {
      timesBefore500.push(
        await page.getByTestId(`editor-state-revision-time-${i}`).innerText(),
      );
      sourcesBefore500.push(
        await page
          .getByTestId(`editor-state-revision-source-${i}`)
          .innerText(),
      );
    }
    state.deleteModeByRevisionId[failTarget] = {
      kind: "http_error",
      status: 500,
    };
    const delBeforeFail = state.deleteLog.length;
    const pageBeforeFail = pageHitCount(state, TECH_A);
    const pageCompleteBeforeFail = pageCompleteCount(state, TECH_A);
    const searchBeforeFail = state.searchLog.length;
    const searchCompleteBeforeFail = searchCompleteCount(state, TECH_A);
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, failTarget), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(state.deleteLog.length).toBe(delBeforeFail + 1);
    expect(pageHitCount(state, TECH_A)).toBe(pageBeforeFail);
    expect(pageCompleteCount(state, TECH_A)).toBe(pageCompleteBeforeFail);
    expect(state.searchLog.length).toBe(searchBeforeFail);
    expect(searchCompleteCount(state, TECH_A)).toBe(searchCompleteBeforeFail);
    expect(pageCompleteCount(state, TECH_A)).toBe(pageFailBase);
    expect(state.revisions[TECH_A].map((r) => r.revisionId)).toEqual(
      listIdsBeforeFail,
    );
    for (let i = 0; i < visibleFailCount; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-time-${i}`),
      ).toHaveText(timesBefore500[i]);
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText(sourcesBefore500[i]);
    }
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_FAIL,
    );
    const failText = await page
      .getByTestId("editor-state-revision-status")
      .innerText();
    expect(failText).not.toContain(failTarget);
    expect(failText).not.toContain("delete error");
    expect(failText).not.toContain("/editor-state-revisions/");
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-delete-0"),
    ).toBeEnabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeEnabled();
    await expect(
      page.getByTestId("editor-state-revision-summary-0"),
    ).toBeEnabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeEnabled();

    delete state.deleteModeByRevisionId[failTarget];
    state.deleteModeByRevisionId[failTarget] = {
      kind: "http_error",
      status: 404,
    };
    const timesBefore404: string[] = [];
    const sourcesBefore404: string[] = [];
    for (let i = 0; i < visibleFailCount; i++) {
      timesBefore404.push(
        await page.getByTestId(`editor-state-revision-time-${i}`).innerText(),
      );
      sourcesBefore404.push(
        await page
          .getByTestId(`editor-state-revision-source-${i}`)
          .innerText(),
      );
    }
    const delBefore404 = state.deleteLog.length;
    const pageBefore404 = pageHitCount(state, TECH_A);
    const pageCompleteBefore404 = pageCompleteCount(state, TECH_A);
    const searchBefore404 = state.searchLog.length;
    const searchCompleteBefore404 = searchCompleteCount(state, TECH_A);
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, failTarget), {
        timeout: 10_000,
      })
      .toBe(2);
    expect(state.deleteLog.length).toBe(delBefore404 + 1);
    expect(pageHitCount(state, TECH_A)).toBe(pageBefore404);
    expect(pageCompleteCount(state, TECH_A)).toBe(pageCompleteBefore404);
    expect(state.searchLog.length).toBe(searchBefore404);
    expect(searchCompleteCount(state, TECH_A)).toBe(searchCompleteBefore404);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_FAIL,
    );
    expect(state.revisions[TECH_A].map((r) => r.revisionId)).toEqual(
      listIdsBeforeFail,
    );
    for (let i = 0; i < visibleFailCount; i++) {
      await expect(
        page.getByTestId(`editor-state-revision-time-${i}`),
      ).toHaveText(timesBefore404[i]);
      await expect(
        page.getByTestId(`editor-state-revision-source-${i}`),
      ).toHaveText(sourcesBefore404[i]);
    }
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-delete-0"),
    ).toBeEnabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-0"),
    ).toBeEnabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeEnabled();

    // 全部 deleteLog 终态：postData 精确 null
    for (const h of state.deleteLog) {
      expect(h.postData).toBeNull();
      expect(h.queryKeys).toEqual([]);
      expect(h.search).toBe("");
      expect(h.method).toBe("DELETE");
    }

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

test.describe("P12F-G-B 技术标互斥与迟到隔离", () => {
  test("P12F-G-B 技术标：确认前清意图；确认/在途控件真实 disabled；A 挂起切 B 后发 B；释放 A 不污染 B；旧删除后迟到 page/search 不污染", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    const seededA = seedRevisions(state, TECH_A, 3, [
      "browser_put",
      "task",
      "revise",
    ]);
    const seededB = seedRevisions(state, TECH_B, 2, ["callback", "task"]);
    const revA0 = seededA[0].revisionId;
    const revB0 = seededB[0].revisionId;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);

    // 先建立摘要/比较/body-diff/pair/restore 意图
    await page.getByTestId("editor-state-revision-summary-0").click();
    await expect
      .poll(
        () =>
          state.detailCompleteLog.filter((d) => d.revisionId === revA0).length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-compare-0").click();
    await expect
      .poll(
        () =>
          state.comparisonCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-body-diff-0").click();
    await expect
      .poll(
        () =>
          state.bodyDiffCompleteLog.filter((d) => d.revisionId === revA0)
            .length,
        { timeout: 10_000 },
      )
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toBeVisible();
    await page.getByTestId("editor-state-revision-pair-select-before-0").click();
    await page.getByTestId("editor-state-revision-pair-select-after-1").click();
    await page.getByTestId("editor-state-revision-restore-1").click();
    await expect(
      page.getByTestId("editor-state-revision-confirm-restore-1"),
    ).toBeVisible();

    // 点击删除：确认前清除上述意图；DELETE=0
    await page.getByTestId("editor-state-revision-delete-0").click();
    expect(state.deleteLog.length).toBe(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-summary-body-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-comparison-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-body-diff-result-0"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-confirm-restore-1"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-pair-result"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-toggle"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-refresh"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-source-filter"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-apply"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-summary-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-delete-1"),
    ).toBeDisabled();

    // A 删除挂起
    const gateA = createHoldGate();
    state.deleteModeByProject[TECH_A] = { kind: "hold", gate: gateA };
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await gateA.waitUntilEntered(1);
    expect(deleteHitCount(state, TECH_A, revA0)).toBe(1);
    expect(deleteCompleteCount(state, TECH_A, revA0)).toBe(0);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      "删除中…",
    );
    await expect(
      page.getByTestId("editor-state-revision-toggle"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-confirm-delete-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-cancel-delete-0"),
    ).toBeDisabled();
    expect(state.deleteLog.length).toBe(1);

    // 切 B：再发 B 删除
    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(1);
    expect(deleteCompleteCount(state, TECH_A, revA0)).toBe(0);
    const gateB = createHoldGate();
    state.deleteModeByProject[TECH_B] = { kind: "hold", gate: gateB };
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await gateB.waitUntilEntered(1);
    expect(deleteHitCount(state, TECH_B, revB0)).toBe(1);
    expect(deleteCompleteCount(state, TECH_B, revB0)).toBe(0);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      "删除中…",
    );
    const pageBBeforeARelease = pageHitCount(state, TECH_B);
    const searchBBefore = state.searchLog.filter(
      (h) => h.projectId === TECH_B,
    ).length;
    const delBArrived = deleteHitCount(state, TECH_B, revB0);

    // 释放 A：不得污染 B
    gateA.release();
    delete state.deleteModeByProject[TECH_A];
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, revA0), {
        timeout: 10_000,
      })
      .toBe(1);
    expect(deleteHitCount(state, TECH_B, revB0)).toBe(delBArrived);
    expect(deleteCompleteCount(state, TECH_B, revB0)).toBe(0);
    expect(pageHitCount(state, TECH_B)).toBe(pageBBeforeARelease);
    expect(
      state.searchLog.filter((h) => h.projectId === TECH_B).length,
    ).toBe(searchBBefore);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      "删除中…",
    );
    await expect(
      page.getByTestId("editor-state-revision-toggle"),
    ).toBeDisabled();
    expect(await readContent(page, "tech")).toBe(TECH_OVERVIEW_B);

    // 释放 B
    gateB.release();
    delete state.deleteModeByProject[TECH_B];
    await expect
      .poll(() => deleteCompleteCount(state, TECH_B, revB0), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(2);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    expect(
      state.revisions[TECH_B].some((r) => r.revisionId === revB0),
    ).toBe(false);
    expect(deleteHitCount(state, TECH_A, revA0)).toBe(1);
    expect(deleteHitCount(state, TECH_B, revB0)).toBe(1);

    // 切回 A：首次展开精确 page complete +1（A 删除后可能已有迟到 page，但项目切换后折叠再展开必发）
    const pageABeforeReopen = pageCompleteCount(state, TECH_A);
    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageABeforeReopen + 1);
    expect(
      state.revisions[TECH_A].some((r) => r.revisionId === revA0),
    ).toBe(false);

    // 迟到 search 隔离：A 删除后 search 挂起，切 B 再释放
    expect(state.revisions[TECH_A].length).toBe(2);
    const lateStampId = state.revisions[TECH_A][0].revisionId;
    stampSearchableMarker(state, lateStampId, "P12FGB_LATE", "tech");
    await page
      .getByTestId("editor-state-revision-search-input")
      .fill("P12FGB_LATE");
    await page.getByTestId("editor-state-revision-search-apply").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            "P12FGB_LATE",
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const lateDelGate = createHoldGate();
    const lateSearchGate = createHoldGate();
    expect(state.revisions[TECH_A].length).toBe(2);
    const lateTarget = state.revisions[TECH_A][0].revisionId;
    state.deleteModeByRevisionId[lateTarget] = {
      kind: "hold",
      gate: lateDelGate,
    };
    state.searchModeByProject[TECH_A] = { kind: "hold", gate: lateSearchGate };
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await lateDelGate.waitUntilEntered(1);
    lateDelGate.release();
    delete state.deleteModeByRevisionId[lateTarget];
    await expect
      .poll(() => deleteCompleteCount(state, TECH_A, lateTarget), {
        timeout: 10_000,
      })
      .toBe(1);
    await lateSearchGate.waitUntilEntered(1);
    const pageBBeforeLateSearch = pageCompleteCount(state, TECH_B);
    await openWorkspace(page, "tech", TECH_B);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(pageBBeforeLateSearch + 1);
    lateSearchGate.release();
    state.searchModeByProject[TECH_A] = { kind: "ok" };
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            TECH_A,
            "P12FGB_LATE",
            null,
            null,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(2);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    expect(searchHitCount(state, TECH_B)).toBe(0);

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);
  });
});

test.describe("P12F-G-B 商务标共用入口与数据最小化", () => {
  test.use({ timezoneId: "Asia/Shanghai" });

  test("P12F-G-B 商务标：同一删除入口/确认/失败/成功；搜索+来源+时间成功只重发原 search；零 editor-state/restore/checkpoint/外网与 ID/关键词/快照/CSRF 泄漏", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    const SEARCH_MARK = "P12FGB_BIZ_DEL_MARK";
    const SEARCH_Q = "P12FGB_BIZ_DEL";
    const seeded = seedRevisions(
      state,
      BIZ_A,
      6,
      Array.from({ length: 6 }, () => "callback"),
    );
    syncRevisionCreatedAts(state, BIZ_A, "2026-07-16T00:00:00.000Z", 1);
    for (const it of seeded) {
      stampSearchableMarker(state, it.revisionId, SEARCH_MARK, "biz");
    }
    seedRevisions(state, BIZ_B, 2, ["task", "revise"]);
    const UTC_FROM = "2026-07-16T00:00:00.000Z";
    const UTC_BEFORE = "2026-07-16T00:10:00.000Z";
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), { timeout: 10_000 })
      .toBe(1);

    await expect(
      page.getByTestId("editor-state-revision-delete-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-delete-0"),
    ).toHaveText("删除");

    await page
      .getByTestId("editor-state-revision-source-filter")
      .selectOption({ label: "解析回传" });
    await expect
      .poll(() => pageHitCountForSource(state, BIZ_A, "callback"), {
        timeout: 10_000,
      })
      .toBe(1);
    await page
      .getByTestId("editor-state-revision-created-from")
      .fill("2026-07-16T08:00");
    await page
      .getByTestId("editor-state-revision-created-before")
      .fill("2026-07-16T08:10");
    await page.getByTestId("editor-state-revision-time-apply").click();
    await expect
      .poll(
        () =>
          pageHitCountForTimeFilter(
            state,
            BIZ_A,
            "callback",
            UTC_FROM,
            UTC_BEFORE,
            null,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    await page
      .getByTestId("editor-state-revision-search-input")
      .fill(SEARCH_Q);
    await page.getByTestId("editor-state-revision-search-apply").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(1);
    const comboBody = state.searchLog[state.searchLog.length - 1];
    expect(comboBody.bodyKeys).toEqual([
      "query",
      "sourceKind",
      "createdFrom",
      "createdBefore",
    ]);
    expect(comboBody.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "callback",
      createdFrom: UTC_FROM,
      createdBefore: UTC_BEFORE,
    });

    expect(state.revisions[BIZ_A].length).toBe(6);
    const failId = state.revisions[BIZ_A][0].revisionId;
    state.deleteModeByRevisionId[failId] = {
      kind: "http_error",
      status: 500,
    };
    const pageBeforeFail = pageHitCount(state, BIZ_A);
    const searchBeforeFail = searchHitCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    await page.getByTestId("editor-state-revision-delete-0").click();
    await expect(
      page.getByTestId("editor-state-revision-delete-confirm-text-0"),
    ).toHaveText(DELETE_CONFIRM);
    expect(state.deleteLog.length).toBe(0);
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, BIZ_A, failId), {
        timeout: 10_000,
      })
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_FAIL,
    );
    expect(pageHitCount(state, BIZ_A)).toBe(pageBeforeFail);
    expect(
      searchHitCountForFilter(
        state,
        BIZ_A,
        SEARCH_Q,
        "callback",
        UTC_FROM,
        UTC_BEFORE,
      ),
    ).toBe(searchBeforeFail);
    expect(
      state.revisions[BIZ_A].some((r) => r.revisionId === failId),
    ).toBe(true);

    delete state.deleteModeByRevisionId[failId];
    const okId = failId;
    const editorGetsBefore = state.editorGetLog.length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const cpBefore = state.checkpointCreateLog.length;
    const pageBeforeOk = pageHitCount(state, BIZ_A);
    const delCompleteBeforeOk = deleteCompleteCount(state, BIZ_A, okId);
    const searchBeforeOk = searchHitCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    const searchCompleteBeforeOk = searchCompleteCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(() => deleteCompleteCount(state, BIZ_A, okId), {
        timeout: 10_000,
      })
      .toBe(delCompleteBeforeOk + 1);
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeOk + 1);
    expect(
      searchHitCountForFilter(
        state,
        BIZ_A,
        SEARCH_Q,
        "callback",
        UTC_FROM,
        UTC_BEFORE,
      ),
    ).toBe(searchBeforeOk + 1);
    expect(pageHitCount(state, BIZ_A)).toBe(pageBeforeOk);
    const reloadSearch = state.searchLog[state.searchLog.length - 1];
    expect(reloadSearch.method).toBe("POST");
    expect(reloadSearch.queryKeys).toEqual([]);
    expect(reloadSearch.search).toBe("");
    expect(reloadSearch.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "callback",
      createdFrom: UTC_FROM,
      createdBefore: UTC_BEFORE,
    });
    const delHit = state.deleteLog[state.deleteLog.length - 1];
    expect(delHit.method).toBe("DELETE");
    expect(delHit.queryKeys).toEqual([]);
    expect(delHit.postData).toBeNull();
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    expect(
      state.revisions[BIZ_A].some((r) => r.revisionId === okId),
    ).toBe(false);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.externalHits).toEqual([]);

    await expect(
      page.getByTestId("editor-state-revision-delete-0"),
    ).toBeVisible();

    // 组合四条件 search 态：DELETE 204 成功 + search 重载固定 HTTP 失败
    // 必须保留已应用 query/sourceKind/createdFrom/createdBefore，不得退回仅 query
    expect(state.revisions[BIZ_A].length).toBe(5);
    const comboFailTargetId = state.revisions[BIZ_A][0].revisionId;
    expect(
      JSON.stringify(state.details[comboFailTargetId].snapshot),
    ).toContain(SEARCH_MARK);
    state.searchModeByProject[BIZ_A] = {
      kind: "http_error",
      status: 500,
    };
    const delCompleteBeforeComboFail = deleteCompleteCount(
      state,
      BIZ_A,
      comboFailTargetId,
    );
    const delHitBeforeComboFail = deleteHitCount(
      state,
      BIZ_A,
      comboFailTargetId,
    );
    const searchArrivedBeforeComboFail = searchHitCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    const searchCompleteBeforeComboFail = searchCompleteCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    const pageBeforeComboFail = pageHitCount(state, BIZ_A);
    const pageCompleteBeforeComboFail = pageCompleteCount(state, BIZ_A);
    const editorGetsBeforeComboFail = state.editorGetLog.length;
    const putBeforeComboFail = state.putLog.length;
    const restoreBeforeComboFail = state.restoreLog.length;
    const cpBeforeComboFail = state.checkpointCreateLog.length;
    await page.getByTestId("editor-state-revision-delete-0").click();
    await page.getByTestId("editor-state-revision-confirm-delete-0").click();
    await expect
      .poll(
        () => deleteCompleteCount(state, BIZ_A, comboFailTargetId),
        { timeout: 10_000 },
      )
      .toBe(delCompleteBeforeComboFail + 1);
    expect(deleteHitCount(state, BIZ_A, comboFailTargetId)).toBe(
      delHitBeforeComboFail + 1,
    );
    const comboDelHit = state.deleteLog[state.deleteLog.length - 1];
    expect(comboDelHit.method).toBe("DELETE");
    expect(comboDelHit.queryKeys).toEqual([]);
    expect(comboDelHit.search).toBe("");
    expect(comboDelHit.postData).toBeNull();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeComboFail + 1);
    expect(
      searchHitCountForFilter(
        state,
        BIZ_A,
        SEARCH_Q,
        "callback",
        UTC_FROM,
        UTC_BEFORE,
      ),
    ).toBe(searchArrivedBeforeComboFail + 1);
    // DELETE 不重试；page 零新增
    expect(deleteHitCount(state, BIZ_A, comboFailTargetId)).toBe(
      delHitBeforeComboFail + 1,
    );
    expect(pageHitCount(state, BIZ_A)).toBe(pageBeforeComboFail);
    expect(pageCompleteCount(state, BIZ_A)).toBe(pageCompleteBeforeComboFail);
    const comboFailSearch = state.searchLog[state.searchLog.length - 1];
    expect(comboFailSearch.method).toBe("POST");
    expect(comboFailSearch.queryKeys).toEqual([]);
    expect(comboFailSearch.search).toBe("");
    expect(comboFailSearch.bodyKeys).toEqual([
      "query",
      "sourceKind",
      "createdFrom",
      "createdBefore",
    ]);
    expect(comboFailSearch.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "callback",
      createdFrom: UTC_FROM,
      createdBefore: UTC_BEFORE,
    });
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_DELETE_OK,
    );
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveText(MSG_SEARCH_FAIL);
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toHaveCount(0);
    expect(
      state.revisions[BIZ_A].some((r) => r.revisionId === comboFailTargetId),
    ).toBe(false);
    expect(state.editorGetLog.length).toBe(editorGetsBeforeComboFail);
    expect(state.putLog.length).toBe(putBeforeComboFail);
    expect(state.restoreLog.length).toBe(restoreBeforeComboFail);
    expect(state.checkpointCreateLog.length).toBe(cpBeforeComboFail);

    // 恢复 searchMode 后显式刷新：仍以同一四条件 search 成功重载且零 page
    state.searchModeByProject[BIZ_A] = { kind: "ok" };
    const searchCompleteBeforeComboRecover = searchCompleteCountForFilter(
      state,
      BIZ_A,
      SEARCH_Q,
      "callback",
      UTC_FROM,
      UTC_BEFORE,
    );
    const pageBeforeComboRecover = pageHitCount(state, BIZ_A);
    const pageCompleteBeforeComboRecover = pageCompleteCount(state, BIZ_A);
    await page.getByTestId("editor-state-revision-refresh").click();
    await expect
      .poll(
        () =>
          searchCompleteCountForFilter(
            state,
            BIZ_A,
            SEARCH_Q,
            "callback",
            UTC_FROM,
            UTC_BEFORE,
          ),
        { timeout: 10_000 },
      )
      .toBe(searchCompleteBeforeComboRecover + 1);
    expect(pageHitCount(state, BIZ_A)).toBe(pageBeforeComboRecover);
    expect(pageCompleteCount(state, BIZ_A)).toBe(
      pageCompleteBeforeComboRecover,
    );
    const comboRecoverSearch = state.searchLog[state.searchLog.length - 1];
    expect(comboRecoverSearch.bodyKeys).toEqual([
      "query",
      "sourceKind",
      "createdFrom",
      "createdBefore",
    ]);
    expect(comboRecoverSearch.body).toEqual({
      query: SEARCH_Q,
      sourceKind: "callback",
      createdFrom: UTC_FROM,
      createdBefore: UTC_BEFORE,
    });
    await expect(
      page.getByTestId("editor-state-revision-list-error"),
    ).toHaveCount(0);
    await expect(
      page.getByTestId("editor-state-revision-item-0"),
    ).toBeVisible();
    await expect(
      page.getByTestId("editor-state-revision-search-active"),
    ).toBeVisible();

    expect(state.listLog.length).toBe(0);
    expect(state.forbiddenHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
    await assertNoIdLeak(page, state, guards.consoleLogs);

    const persist = await page.evaluate(() => ({
      href: location.href,
      cookie: document.cookie,
      ls: Object.keys(localStorage)
        .map((k) => `${k}=${localStorage.getItem(k)}`)
        .join("\n"),
      ss: Object.keys(sessionStorage)
        .map((k) => `${k}=${sessionStorage.getItem(k)}`)
        .join("\n"),
      body: document.body?.innerText ?? "",
    }));
    for (const blob of [
      persist.href,
      persist.cookie,
      persist.ls,
      persist.ss,
    ]) {
      expect(blob).not.toContain(SEARCH_Q);
      expect(blob).not.toContain(SEARCH_MARK);
      expect(blob).not.toMatch(/esr_[0-9a-f]{32}/);
      expect(blob).not.toMatch(/esv_[0-9a-f]{32}/);
      expect(blob).not.toMatch(/csrf/i);
    }
    expect(persist.body).not.toContain(SEARCH_MARK);
    expect(persist.body).not.toContain(SNAPSHOT_BODY_LEAK);
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain(SEARCH_Q);
    expect(consoleBlob).not.toContain(SEARCH_MARK);
    expect(consoleBlob).not.toMatch(/esr_[0-9a-f]{32}/);
    expect(consoleBlob).not.toMatch(/csrf/i);
    // 全部 deleteLog 终态：postData 精确 null；query 空；method DELETE
    for (const h of state.deleteLog) {
      expect(h.postData).toBeNull();
      expect(h.queryKeys).toEqual([]);
      expect(h.search).toBe("");
      expect(h.method).toBe("DELETE");
    }
  });
});

// ---------------------------------------------------------------------------
// P12F-G-B 终态静态自检（扫描上列三用例源码；本 describe 不计入扫描范围）
// ---------------------------------------------------------------------------
test.describe("P12F-G-B 终态静态自检", () => {
  test("P12F-G-B marker 后禁止项精确零命中", () => {
    const selfPath = fileURLToPath(import.meta.url);
    const sourcePath = path.resolve(selfPath);
    expect(sourcePath.endsWith("editor-state-revision-history.spec.ts")).toBe(
      true,
    );
    const full = fs.readFileSync(sourcePath, "utf8");
    const beginMark = "// P12F-G-B 单条修订删除前端";
    const endMark = "// P12F-G-B 终态静态自检";
    const begin = full.indexOf(beginMark);
    const end = full.indexOf(endMark);
    expect(begin).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(begin);
    const block = full.slice(begin, end);

    const count = (re: RegExp): number => {
      const flags = re.flags.includes("g") ? re.flags : `${re.flags}g`;
      const matched = block.match(new RegExp(re.source, flags));
      if (matched === null) return 0;
      return matched.length;
    };

    // 权威 revisions 不得空列表兜底
    expect(count(/state\.revisions\[[^\]]+\]\s*\|\|/)).toBe(0);
    // 已断言长度后禁止 Math.min 收缩可见计数
    expect(count(/Math\.min\s*\(/)).toBe(0);
    // 弱存在/定义断言
    expect(count(/toBeTruthy\s*\(/)).toBe(0);
    expect(count(/toBeFalsy\s*\(/)).toBe(0);
    expect(count(/toBeDefined\s*\(/)).toBe(0);
    // postData 不得用 == null 逃逸
    expect(count(/postData\s*==\s*null/)).toBe(0);
    // Playwright 宽松定位 / 固定 sleep / 强制点击 / 条件跳过
    expect(count(/\.or\s*\(/)).toBe(0);
    expect(count(/waitForTimeout\s*\(/)).toBe(0);
    expect(count(/force\s*:\s*true/)).toBe(0);
    expect(count(/test\.skip\s*[.(]/)).toBe(0);
    expect(count(/test\.fixme\s*[.(]/)).toBe(0);
    // 可选链取第 0 项
    expect(count(/\)\[0\]\?/)).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// P12F-H 单条修订命名前端（三用例互不 serial 跳过）
// failure-first：必须先进入页面且列表已加载，再因命名入口/六键呈现缺失失败。
// ---------------------------------------------------------------------------

test.describe("P12F-H 技术标保存覆盖清除与失败保值", () => {
  test("P12F-H 技术标：列表已加载后命名入口可见；输入/取消零 PATCH；保存/覆盖/清除精确一次；失败保值；非法前端零请求", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    state.authRequired = true;
    const seeded = seedRevisions(state, TECH_A, 3, [
      "browser_put",
      "task",
      "revise",
    ]);
    const target0 = seeded[0].revisionId;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    // 既有列表已加载（failure-first 必须越过此步）
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-item-1")).toBeVisible();
    expect(state.nameLog.length).toBe(0);

    // 命名入口（生产 UI 未实现时在此业务失败）
    const nameBtn = page.getByTestId("editor-state-revision-name-0");
    await expect(nameBtn).toBeVisible();
    await nameBtn.click();
    const nameInput = page.getByTestId("editor-state-revision-name-input-0");
    await expect(nameInput).toBeVisible();

    // 输入零 PATCH
    await nameInput.fill("初版名称");
    expect(state.nameLog.length).toBe(0);

    // 取消零 PATCH，列表不变
    await page.getByTestId("editor-state-revision-name-cancel-0").click();
    await expect(nameInput).toHaveCount(0);
    expect(state.nameLog.length).toBe(0);
    expect(state.nameCompleteLog.length).toBe(0);

    // 非法输入（前端可判定）零请求：空串 / 纯空白 / 41 码点
    await page.getByTestId("editor-state-revision-name-0").click();
    await nameInput.fill("   ");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    expect(state.nameLog.length).toBe(0);
    await nameInput.fill("字".repeat(41));
    await page.getByTestId("editor-state-revision-name-save-0").click();
    expect(state.nameLog.length).toBe(0);

    // 合法保存：精确一次 PATCH
    const pageBefore = pageHitCount(state, TECH_A);
    const searchBefore = state.searchLog.length;
    const editorGetsBefore = state.editorGetLog.length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const cpBefore = state.checkpointCreateLog.length;
    const deleteBefore = state.deleteLog.length;
    await nameInput.fill("初版名称");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, target0), { timeout: 10_000 })
      .toBe(1);
    expect(nameHitCount(state, TECH_A, target0)).toBe(1);
    const hit0 = state.nameLog[0];
    expect(hit0.method).toBe("PATCH");
    expect(hit0.path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/${target0}/display-name`,
    );
    expect(hit0.queryKeys).toEqual([]);
    expect(hit0.search).toBe("");
    expect(hit0.bodyKeys).toEqual(["displayName"]);
    expect(hit0.displayName).toBe("初版名称");
    expect(typeof hit0.postData).toBe("string");
    expect(JSON.parse(hit0.postData as string)).toEqual({
      displayName: "初版名称",
    });
    expect(state.nameCompleteLog[0].status).toBe(200);
    expect(state.nameCompleteLog[0].displayName).toBe("初版名称");
    expect(hit0.csrfToken).toBe("e2e-csrf");
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_OK,
    );
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("初版名称");
    // 成功原位更新，零 page/search 重载
    expect(pageHitCount(state, TECH_A)).toBe(pageBefore);
    expect(state.searchLog.length).toBe(searchBefore);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.deleteLog.length).toBe(deleteBefore);
    expect(state.externalHits).toEqual([]);

    // 覆盖（失败保值前置：当前 DOM 名为「覆盖名」）
    await page.getByTestId("editor-state-revision-name-0").click();
    await page.getByTestId("editor-state-revision-name-input-0").fill("覆盖名");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, target0), { timeout: 10_000 })
      .toBe(2);
    expect(nameHitCount(state, TECH_A, target0)).toBe(2);
    expect(state.nameLog[1].method).toBe("PATCH");
    expect(state.nameLog[1].path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/${target0}/display-name`,
    );
    expect(state.nameLog[1].queryKeys).toEqual([]);
    expect(state.nameLog[1].search).toBe("");
    expect(state.nameLog[1].bodyKeys).toEqual(["displayName"]);
    expect(state.nameLog[1].displayName).toBe("覆盖名");
    expect(typeof state.nameLog[1].postData).toBe("string");
    expect(JSON.parse(state.nameLog[1].postData as string)).toEqual({
      displayName: "覆盖名",
    });
    expect(state.nameLog[1].csrfToken).toBe("e2e-csrf");
    expect(state.nameCompleteLog[1].status).toBe(200);
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("覆盖名");
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_OK,
    );

    // 失败保值：在非空原名「覆盖名」上 HTTP 500 尝试「失败名」
    state.nameModeByRevisionId[target0] = { kind: "http_error", status: 500 };
    await page.getByTestId("editor-state-revision-name-0").click();
    await page.getByTestId("editor-state-revision-name-input-0").fill("失败名");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, target0), { timeout: 10_000 })
      .toBe(3);
    expect(nameHitCount(state, TECH_A, target0)).toBe(3);
    const hitFail = state.nameLog[2];
    expect(hitFail.method).toBe("PATCH");
    expect(hitFail.path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/${target0}/display-name`,
    );
    expect(hitFail.queryKeys).toEqual([]);
    expect(hitFail.search).toBe("");
    expect(hitFail.bodyKeys).toEqual(["displayName"]);
    expect(hitFail.displayName).toBe("失败名");
    expect(typeof hitFail.postData).toBe("string");
    expect(JSON.parse(hitFail.postData as string)).toEqual({
      displayName: "失败名",
    });
    expect(hitFail.csrfToken).toBe("e2e-csrf");
    expect(state.nameCompleteLog[2].status).toBe(500);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_FAIL,
    );
    // DOM 仍保留非空原名；输入草稿仍为失败名
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("覆盖名");
    await expect(
      page.getByTestId("editor-state-revision-name-input-0"),
    ).toHaveValue("失败名");
    expect(pageHitCount(state, TECH_A)).toBe(pageBefore);
    expect(state.searchLog.length).toBe(searchBefore);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.deleteLog.length).toBe(deleteBefore);
    expect(state.externalHits).toEqual([]);

    // 恢复 name mode 后执行清除 null（第四次 PATCH）
    state.nameModeByRevisionId[target0] = { kind: "ok" };
    await page.getByTestId("editor-state-revision-name-clear-0").click();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, target0), { timeout: 10_000 })
      .toBe(4);
    expect(nameHitCount(state, TECH_A, target0)).toBe(4);
    expect(state.nameLog[3].method).toBe("PATCH");
    expect(state.nameLog[3].path).toBe(
      `/api/projects/${TECH_A}/editor-state-revisions/${target0}/display-name`,
    );
    expect(state.nameLog[3].queryKeys).toEqual([]);
    expect(state.nameLog[3].search).toBe("");
    expect(state.nameLog[3].bodyKeys).toEqual(["displayName"]);
    expect(state.nameLog[3].displayName).toBeNull();
    expect(typeof state.nameLog[3].postData).toBe("string");
    expect(JSON.parse(state.nameLog[3].postData as string)).toEqual({
      displayName: null,
    });
    expect(state.nameLog[3].csrfToken).toBe("e2e-csrf");
    expect(state.nameCompleteLog[3].status).toBe(200);
    expect(state.nameCompleteLog[3].displayName).toBeNull();
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_CLEARED,
    );
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveCount(0);
    // 终态：四次 PATCH 完整序列 CSRF 均为 e2e-csrf；日志长度精确 4
    expect(state.nameLog.length).toBe(4);
    expect(state.nameCompleteLog.length).toBe(4);
    expect(state.nameLog.map((h) => h.csrfToken)).toEqual([
      "e2e-csrf",
      "e2e-csrf",
      "e2e-csrf",
      "e2e-csrf",
    ]);
    expect(pageHitCount(state, TECH_A)).toBe(pageBefore);
    expect(state.searchLog.length).toBe(searchBefore);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.deleteLog.length).toBe(deleteBefore);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });
});

test.describe("P12F-H 技术标互斥与迟到隔离", () => {
  test("P12F-H 技术标：命名确认清其它意图；非命名控件真实 disabled；A/B 双 hold 迟到 success/failure 不解锁在途 B", async ({
    page,
  }) => {
    const state = createProbeState("tech");
    state.authRequired = true;
    expect(state.revisions[TECH_A].length).toBe(0);
    seedRevisions(state, TECH_A, 4, [
      "browser_put",
      "task",
      "revise",
      "callback",
    ]);
    seedRevisions(state, TECH_B, 3, ["browser_put", "task", "revise"]);
    expect(state.revisions[TECH_A].length).toBe(4);
    expect(state.revisions[TECH_B].length).toBe(3);
    const targetA = state.revisions[TECH_A][0].revisionId;
    const targetB = state.revisions[TECH_B][0].revisionId;
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "tech", TECH_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();

    // 先点摘要意图，再进入命名——命名确认应清除其它意图
    const detailBefore = state.detailLog.length;
    await page.getByTestId("editor-state-revision-summary-1").click();
    await expect
      .poll(() => state.detailLog.length, { timeout: 10_000 })
      .toBe(detailBefore + 1);
    await page.getByTestId("editor-state-revision-name-0").click();
    await expect(
      page.getByTestId("editor-state-revision-name-input-0"),
    ).toBeVisible();
    await expect(page.getByTestId("editor-state-revision-toggle")).toBeDisabled();
    await expect(page.getByTestId("editor-state-revision-refresh")).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-source-filter"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-input"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-search-apply"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-restore-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-delete-1"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-summary-1"),
    ).toBeDisabled();

    // ---------- 路径 1：A-success hold，切 B 后 B 也 hold；释放 A 时 B 仍在途 ----------
    const gateAOk = createHoldGate();
    state.nameModeByRevisionId[targetA] = {
      kind: "hold",
      gate: gateAOk,
      then: "ok",
    };
    const pageABeforeOk = pageHitCount(state, TECH_A);
    const searchABeforeOk = state.searchLog.filter(
      (h) => h.projectId === TECH_A,
    ).length;
    const putBeforeOk = state.putLog.length;
    const restoreBeforeOk = state.restoreLog.length;
    const cpBeforeOk = state.checkpointCreateLog.length;
    const deleteBeforeOk = state.deleteLog.length;

    await page.getByTestId("editor-state-revision-name-input-0").fill("项目A名");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await gateAOk.waitUntilEntered(1);
    await expect
      .poll(() => nameHitCount(state, TECH_A, targetA), { timeout: 10_000 })
      .toBe(1);
    expect(nameCompleteCount(state, TECH_A, targetA)).toBe(0);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_SAVING,
    );

    // 切 B，B 也 hold
    const gateBOk = createHoldGate();
    state.nameModeByRevisionId[targetB] = {
      kind: "hold",
      gate: gateBOk,
      then: "ok",
    };
    await page.goto(`/technical-plan/${TECH_B}/analysis`);
    await expect(
      page.getByTestId("technical-editor-workspace"),
    ).toBeVisible();
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();
    const pageBBeforeName = pageHitCount(state, TECH_B);
    const searchBBeforeName = state.searchLog.filter(
      (h) => h.projectId === TECH_B,
    ).length;
    // 切 B 后 editor-state GET 已完成；命名/释放不得再增加
    const editorGetAfterBOpen = state.editorGetLog.length;
    await page.getByTestId("editor-state-revision-name-0").click();
    await page.getByTestId("editor-state-revision-name-input-0").fill("项目B名");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await gateBOk.waitUntilEntered(1);
    await expect
      .poll(() => nameHitCount(state, TECH_B, targetB), { timeout: 10_000 })
      .toBe(1);
    expect(nameCompleteCount(state, TECH_B, targetB)).toBe(0);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_SAVING,
    );
    // B 在途：input/save/cancel 与非命名控件真实 disabled
    await expect(
      page.getByTestId("editor-state-revision-name-input-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-cancel-0"),
    ).toBeDisabled();
    await expect(page.getByTestId("editor-state-revision-refresh")).toBeDisabled();
    await expect(page.getByTestId("editor-state-revision-toggle")).toBeDisabled();

    // 释放 A success：B complete 仍 0；B 仍“保存名称中…”；控件仍 disabled
    gateAOk.release();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, targetA), { timeout: 10_000 })
      .toBe(1);
    expect(nameCompleteCount(state, TECH_B, targetB)).toBe(0);
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_SAVING,
    );
    await expect(
      page.getByTestId("editor-state-revision-name-input-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-cancel-0"),
    ).toBeDisabled();
    await expect(page.getByTestId("editor-state-revision-refresh")).toBeDisabled();
    expect(pageHitCount(state, TECH_B)).toBe(pageBBeforeName);
    expect(
      state.searchLog.filter((h) => h.projectId === TECH_B).length,
    ).toBe(searchBBeforeName);

    // 释放 B：成功原位
    gateBOk.release();
    await expect
      .poll(() => nameCompleteCount(state, TECH_B, targetB), { timeout: 10_000 })
      .toBe(1);
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("项目B名");
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_OK,
    );
    expect(nameHitCount(state, TECH_A, targetA)).toBe(1);
    expect(nameHitCount(state, TECH_B, targetB)).toBe(1);
    expect(state.nameLog.length).toBe(2);
    expect(state.nameCompleteLog.length).toBe(2);
    expect(state.nameLog.map((h) => h.csrfToken)).toEqual([
      "e2e-csrf",
      "e2e-csrf",
    ]);
    expect(pageHitCount(state, TECH_A)).toBe(pageABeforeOk);
    expect(
      state.searchLog.filter((h) => h.projectId === TECH_A).length,
    ).toBe(searchABeforeOk);
    // 命名/释放不触发 editor-state GET；切 B 的导航 GET 已计入 editorGetAfterBOpen
    expect(state.editorGetLog.length).toBe(editorGetAfterBOpen);
    expect(state.putLog.length).toBe(putBeforeOk);
    expect(state.restoreLog.length).toBe(restoreBeforeOk);
    expect(state.checkpointCreateLog.length).toBe(cpBeforeOk);
    expect(state.deleteLog.length).toBe(deleteBeforeOk);
    expect(state.externalHits).toEqual([]);

    // ---------- 路径 2：A-failure hold，切 B 新一轮 B hold；释放 A 500 不解锁 B ----------
    const gateAFail = createHoldGate();
    state.nameModeByRevisionId[targetA] = {
      kind: "hold",
      gate: gateAFail,
      then: "http_error",
      status: 500,
    };
    const aHitBeforeFail = nameHitCount(state, TECH_A, targetA);
    const aCompleteBeforeFail = nameCompleteCount(state, TECH_A, targetA);
    const pageABeforeFail = pageCompleteCount(state, TECH_A);
    await page.goto(`/technical-plan/${TECH_A}/analysis`);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_A), { timeout: 10_000 })
      .toBe(pageABeforeFail + 1);
    // A 路径1 success 后已有 displayName；仍进入命名
    await page.getByTestId("editor-state-revision-name-0").click();
    await page.getByTestId("editor-state-revision-name-input-0").fill("A失败迟到");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await gateAFail.waitUntilEntered(1);
    await expect
      .poll(() => nameHitCount(state, TECH_A, targetA), { timeout: 10_000 })
      .toBe(aHitBeforeFail + 1);
    expect(nameCompleteCount(state, TECH_A, targetA)).toBe(aCompleteBeforeFail);

    const gateBFail = createHoldGate();
    state.nameModeByRevisionId[targetB] = {
      kind: "hold",
      gate: gateBFail,
      then: "ok",
    };
    const bHitBeforeFailPath = nameHitCount(state, TECH_B, targetB);
    const bCompleteBeforeFailPath = nameCompleteCount(state, TECH_B, targetB);
    const pageBBeforeFailPath = pageCompleteCount(state, TECH_B);
    const putBeforeFail = state.putLog.length;
    const restoreBeforeFail = state.restoreLog.length;
    const cpBeforeFail = state.checkpointCreateLog.length;
    const deleteBeforeFail = state.deleteLog.length;
    await page.goto(`/technical-plan/${TECH_B}/analysis`);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, TECH_B), { timeout: 10_000 })
      .toBe(pageBBeforeFailPath + 1);
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("项目B名");
    // 切 B 后 editor-state GET 已完成；命名/释放不得再增加
    const editorGetAfterBOpenFail = state.editorGetLog.length;
    await page.getByTestId("editor-state-revision-name-0").click();
    await page
      .getByTestId("editor-state-revision-name-input-0")
      .fill("项目B第二轮");
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await gateBFail.waitUntilEntered(1);
    await expect
      .poll(() => nameHitCount(state, TECH_B, targetB), { timeout: 10_000 })
      .toBe(bHitBeforeFailPath + 1);
    expect(nameCompleteCount(state, TECH_B, targetB)).toBe(
      bCompleteBeforeFailPath,
    );
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_SAVING,
    );
    const pageBDuringBHold = pageHitCount(state, TECH_B);
    const searchBDuringBHold = state.searchLog.filter(
      (h) => h.projectId === TECH_B,
    ).length;

    // 释放 A 500：A catch/finally 不得改 B 名称/状态/解锁
    gateAFail.release();
    await expect
      .poll(() => nameCompleteCount(state, TECH_A, targetA), { timeout: 10_000 })
      .toBe(aCompleteBeforeFail + 1);
    expect(nameCompleteCount(state, TECH_B, targetB)).toBe(
      bCompleteBeforeFailPath,
    );
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_SAVING,
    );
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("项目B名");
    await expect(
      page.getByTestId("editor-state-revision-name-input-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-save-0"),
    ).toBeDisabled();
    await expect(
      page.getByTestId("editor-state-revision-name-cancel-0"),
    ).toBeDisabled();
    await expect(page.getByTestId("editor-state-revision-refresh")).toBeDisabled();
    expect(pageHitCount(state, TECH_B)).toBe(pageBDuringBHold);
    expect(
      state.searchLog.filter((h) => h.projectId === TECH_B).length,
    ).toBe(searchBDuringBHold);

    // 释放 B：第二轮成功
    gateBFail.release();
    await expect
      .poll(() => nameCompleteCount(state, TECH_B, targetB), { timeout: 10_000 })
      .toBe(bCompleteBeforeFailPath + 1);
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText("项目B第二轮");
    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_OK,
    );
    expect(nameHitCount(state, TECH_A, targetA)).toBe(aHitBeforeFail + 1);
    expect(nameHitCount(state, TECH_B, targetB)).toBe(bHitBeforeFailPath + 1);
    // 路径1 A/B + 路径2 A/B 共 4 次；完整 CSRF 序列
    expect(state.nameLog.length).toBe(4);
    expect(state.nameCompleteLog.length).toBe(4);
    expect(state.nameLog.map((h) => h.csrfToken)).toEqual([
      "e2e-csrf",
      "e2e-csrf",
      "e2e-csrf",
      "e2e-csrf",
    ]);
    expect(state.editorGetLog.length).toBe(editorGetAfterBOpenFail);
    expect(state.putLog.length).toBe(putBeforeFail);
    expect(state.restoreLog.length).toBe(restoreBeforeFail);
    expect(state.checkpointCreateLog.length).toBe(cpBeforeFail);
    expect(state.deleteLog.length).toBe(deleteBeforeFail);
    expect(state.externalHits).toEqual([]);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });
});

test.describe("P12F-H 商务标共用入口与数据最小化", () => {
  test("P12F-H 商务标：同一命名入口；HTML marker 作文本；URL/存储/Cookie/console 无泄漏；零 editor-state/restore/checkpoint/DELETE/page 重载/外网", async ({
    page,
  }) => {
    const state = createProbeState("biz");
    state.authRequired = true;
    const seeded = seedRevisions(state, BIZ_A, 2, ["browser_put", "task"]);
    const target0 = seeded[0].revisionId;
    const HTML_MARK = "<img src=x onerror=window.__p12fh_xss=1>";
    const guards = await installRuntimeErrorGuards(page);
    await installRoutes(page, state);

    await openWorkspace(page, "biz", BIZ_A);
    await expandRevisionPanel(page);
    await expect
      .poll(() => pageCompleteCount(state, BIZ_A), { timeout: 10_000 })
      .toBe(1);
    await expect(page.getByTestId("editor-state-revision-item-0")).toBeVisible();

    const pageBefore = pageHitCount(state, BIZ_A);
    const editorGetsBefore = state.editorGetLog.length;
    const putBefore = state.putLog.length;
    const restoreBefore = state.restoreLog.length;
    const cpBefore = state.checkpointCreateLog.length;
    const deleteBefore = state.deleteLog.length;
    const searchBefore = state.searchLog.length;

    await page.getByTestId("editor-state-revision-name-0").click();
    await page
      .getByTestId("editor-state-revision-name-input-0")
      .fill(HTML_MARK);
    await page.getByTestId("editor-state-revision-name-save-0").click();
    await expect
      .poll(() => nameCompleteCount(state, BIZ_A, target0), { timeout: 10_000 })
      .toBe(1);
    expect(state.nameLog.length).toBe(1);
    expect(state.nameCompleteLog.length).toBe(1);
    expect(state.nameLog[0].method).toBe("PATCH");
    expect(state.nameLog[0].path).toBe(
      `/api/projects/${BIZ_A}/editor-state-revisions/${target0}/display-name`,
    );
    expect(state.nameLog[0].queryKeys).toEqual([]);
    expect(state.nameLog[0].search).toBe("");
    expect(state.nameLog[0].bodyKeys).toEqual(["displayName"]);
    expect(state.nameLog[0].displayName).toBe(HTML_MARK);
    expect(typeof state.nameLog[0].postData).toBe("string");
    expect(JSON.parse(state.nameLog[0].postData as string)).toEqual({
      displayName: HTML_MARK,
    });
    expect(state.nameLog.map((h) => h.csrfToken)).toEqual(["e2e-csrf"]);
    expect(state.nameCompleteLog[0].status).toBe(200);
    await expect(
      page.getByTestId("editor-state-revision-display-name-0"),
    ).toHaveText(HTML_MARK);
    // 不得当作 HTML 执行
    const xss = await page.evaluate(() => (window as { __p12fh_xss?: number }).__p12fh_xss);
    expect(xss).toBe(undefined);
    // 文本节点而非 img
    await expect(
      page.getByTestId("editor-state-revision-display-name-0").locator("img"),
    ).toHaveCount(0);

    await expect(page.getByTestId("editor-state-revision-status")).toHaveText(
      MSG_NAME_OK,
    );
    expect(pageHitCount(state, BIZ_A)).toBe(pageBefore);
    expect(state.searchLog.length).toBe(searchBefore);
    expect(state.editorGetLog.length).toBe(editorGetsBefore);
    expect(state.putLog.length).toBe(putBefore);
    expect(state.restoreLog.length).toBe(restoreBefore);
    expect(state.checkpointCreateLog.length).toBe(cpBefore);
    expect(state.deleteLog.length).toBe(deleteBefore);
    expect(state.externalHits).toEqual([]);

    // URL / 存储 / Cookie / console 无名称与 revisionId 泄漏
    expect(page.url()).not.toContain(HTML_MARK);
    expect(page.url()).not.toContain(target0);
    const persist = await page.evaluate(() => ({
      ls: JSON.stringify(window.localStorage),
      ss: JSON.stringify(window.sessionStorage),
      cookie: document.cookie,
    }));
    expect(persist.ls).not.toContain(HTML_MARK);
    expect(persist.ss).not.toContain(HTML_MARK);
    expect(persist.cookie).not.toContain(HTML_MARK);
    expect(persist.ls).not.toContain(target0);
    expect(persist.ss).not.toContain(target0);
    expect(persist.cookie).not.toContain(target0);
    const consoleBlob = guards.consoleLogs.join("\n");
    expect(consoleBlob).not.toContain(HTML_MARK);
    expect(consoleBlob).not.toContain(target0);
    expect(consoleBlob).not.toMatch(/csrf/i);
    expect(guards.pageErrors).toEqual([]);
    expect(await guards.readUnhandled()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// P12F-H 终态静态自检（仅扫描上列三用例源码；本 describe 不计入扫描范围）
// ---------------------------------------------------------------------------
test.describe("P12F-H 终态静态自检", () => {
  test("P12F-H marker 后禁止项精确零命中", () => {
    const selfPath = fileURLToPath(import.meta.url);
    const sourcePath = path.resolve(selfPath);
    expect(sourcePath.endsWith("editor-state-revision-history.spec.ts")).toBe(
      true,
    );
    const full = fs.readFileSync(sourcePath, "utf8");
    const beginMark = "// P12F-H 单条修订命名前端";
    const endMark = "// P12F-H 终态静态自检";
    const begin = full.indexOf(beginMark);
    const end = full.indexOf(endMark);
    expect(begin).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(begin);
    const block = full.slice(begin, end);

    const count = (re: RegExp): number => {
      const flags = re.flags.includes("g") ? re.flags : `${re.flags}g`;
      const matched = block.match(new RegExp(re.source, flags));
      if (matched === null) return 0;
      return matched.length;
    };

    // 权威列表不得空数组兜底
    expect(count(/\|\|\s*\[\]/)).toBe(0);
    // postData 不得用 || "{}" 逃逸
    expect(count(/\|\|\s*"\{\}"/)).toBe(0);
    expect(count(/\|\|\s*'\{\}'/)).toBe(0);
    // 已断言长度后禁止 Math.min 收缩可见计数
    expect(count(/Math\.min\s*\(/)).toBe(0);
    // 弱存在/定义断言
    expect(count(/toBeTruthy\s*\(/)).toBe(0);
    expect(count(/toBeFalsy\s*\(/)).toBe(0);
    expect(count(/toBeDefined\s*\(/)).toBe(0);
    expect(count(/toBeUndefined\s*\(/)).toBe(0);
    // Playwright 宽松定位 / 固定 sleep / 强制点击 / 条件跳过
    expect(count(/\.or\s*\(/)).toBe(0);
    expect(count(/waitForTimeout\s*\(/)).toBe(0);
    expect(count(/force\s*:\s*true/)).toBe(0);
    expect(count(/test\.skip\s*[.(]/)).toBe(0);
    expect(count(/test\.fixme\s*[.(]/)).toBe(0);
    // 可选链取第 0 项
    expect(count(/\)\[0\]\?/)).toBe(0);
    // 宽计数 >=1（带空格）；旧字段 hasCsrf 零命中
    expect(count(/>=\s*1/)).toBe(0);
    expect(count(/hasCsrf/)).toBe(0);
  });

  test("P12F-H 生产面板 pendingNameIdRef.current === revisionId 可执行守卫", () => {
    // 精确限定到两个命名处理函数，禁止宽泛 includes 冒充
    const panelPath = path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../src/features/editor-state-revisions/EditorStateRevisionPanel.tsx",
    );
    expect(fs.existsSync(panelPath)).toBe(true);
    const src = fs.readFileSync(panelPath, "utf8");
    expect(src.includes("pendingNameIdRef")).toBe(true);
    expect(src.includes("const pendingNameIdRef = useRef")).toBe(true);

    const extractFn = (name: string): string => {
      const re = new RegExp(
        `const ${name} = useCallback\\([\\s\\S]*?\\n  \\}, \\[`,
      );
      const m = src.match(re);
      expect(m).not.toBeNull();
      return m![0];
    };
    const saveFn = extractFn("handleNameSave");
    const clearFn = extractFn("handleNameClear");
    const guardRe = /pendingNameIdRef\.current\s*===\s*revisionId/g;
    const saveHits = saveFn.match(guardRe);
    const clearHits = clearFn.match(guardRe);
    expect(saveHits === null ? 0 : saveHits.length).toBe(1);
    expect(clearHits === null ? 0 : clearHits.length).toBe(1);
    // 可执行代码中该守卫恰好两次（save + clear 各一）；剔除注释避免自命中
    const noBlockComments = src.replace(/\/\*[\s\S]*?\*\//g, "");
    const noLineComments = noBlockComments.replace(/^\s*\/\/.*$/gm, "");
    const allHits = noLineComments.match(guardRe);
    expect(allHits === null ? 0 : allHits.length).toBe(2);
  });
});
