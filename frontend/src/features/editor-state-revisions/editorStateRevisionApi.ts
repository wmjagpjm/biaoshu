/**
 * 模块：P12C-C3 / P12D-B / P12E-A / P12E-C / P12F-C / P12F-D / P12F-E-B / P12F-F-B / P12F-G-B / P12F-H / P12F-J-B
 *       editor-state 修订历史、对比、正文差异、游标页、可见内容搜索、单条删除/命名/固定 API 封装
 * 用途：严格校验 list/page/detail/restore/comparison/body-diff/pair-body-diff/search 响应 shape；详情仅在 API 栈内解析并压缩为有界摘要；
 *       单条 DELETE 无 query/body，成功依赖 204 空体；单条 PATCH display-name/pin 精确一键 body/响应。
 * 对接：GET|POST|DELETE|PATCH /api/projects/{id}/editor-state-revisions*；page 游标分页（可选 sourceKind/createdFrom/createdBefore）；
 *       search POST body（query+可选来源/时间）；comparison/body-diff/pair 只读 GET；delete 无 body；display-name/pin PATCH；apiFetch。
 * 二次开发：
 *   - 禁止把原始 snapshot 返回给 React；禁止本地生成 revisionId/version/cursor
 *   - 禁止把响应原文、路径、后端 detail、字段值/键名/游标/时间/关键词/名称字面量拼进错误文案
 *   - 九类来源白名单；旧列表/页最多 10 条；search 最多 20 条且无 nextCursor
 *   - comparison 顶层精确四键 + 13 键有序子序列
 *   - body-diff 顶层精确六键（current/target）；pair 顶层精确六键（before/after）；item 五键；hunk 二键
 *   - page 顶层精确 items/nextCursor；游标仅 esrc1_/esrc2_/esrc3_ 外壳校验，禁止解码/本地生成
 *   - 时间 query 仅精确 24 字符 UTC 毫秒；顺序 sourceKind→createdFrom→createdBefore→cursor
 *   - search body 顺序 query→sourceKind→createdFrom→createdBefore；禁止 URL query/cursor
 *   - delete 仅 method DELETE；禁止 query/body/retry/读取响应 JSON
 *   - meta 精确七键含 displayName/isPinned；detail 精确八键；命名/固定 PATCH 禁止 query/retry/额外 header；响应精确一键
 *   - pin 响应 isPinned 必须等于请求目标；拒绝缺失/额外/非原生布尔/相反布尔
 */

import { apiFetch } from "../../shared/lib/api";

/** 服务端 stateVersion 精确格式 */
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

/** 服务端 revisionId 精确格式 */
const REVISION_ID_RE = /^esr_[0-9a-f]{32}$/;

/** 服务端 safetyCheckpointId 精确格式 */
const CHECKPOINT_ID_RE = /^escp_[0-9a-f]{32}$/;

/** 九类固定内部来源 */
export const REVISION_SOURCE_KINDS = [
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

export type RevisionSourceKind = (typeof REVISION_SOURCE_KINDS)[number];

const SOURCE_KIND_SET = new Set<string>(REVISION_SOURCE_KINDS);

/** 固定中文来源标签（不展示内部原值） */
export const REVISION_SOURCE_LABELS: Record<RevisionSourceKind, string> = {
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

/** 列表项精确七键（P12F-H displayName + P12F-J-B isPinned） */
const META_KEYS = [
  "revisionId",
  "stateVersion",
  "snapshotBytes",
  "sourceKind",
  "createdAt",
  "displayName",
  "isPinned",
] as const;

/** P12M：搜索成功项精确八键（七键 + matchReasons） */
const SEARCH_ITEM_KEYS = [
  "revisionId",
  "stateVersion",
  "snapshotBytes",
  "sourceKind",
  "createdAt",
  "displayName",
  "isPinned",
  "matchReasons",
] as const;

/** P12M：命中原因固定枚举与顺序 */
const MATCH_REASON_ORDER = ["displayName", "visibleContent"] as const;
const MATCH_REASON_SET = new Set<string>(MATCH_REASON_ORDER);

/** 详情精确八键（七键元数据 + snapshot） */
const DETAIL_KEYS = [
  "revisionId",
  "stateVersion",
  "snapshotBytes",
  "sourceKind",
  "createdAt",
  "displayName",
  "isPinned",
  "snapshot",
] as const;

/** 命名成功响应精确一键 */
const DISPLAY_NAME_OUT_KEYS = ["displayName"] as const;

/** 固定成功响应精确一键 */
const PIN_OUT_KEYS = ["isPinned"] as const;

/** 展示名称 Unicode 码点上限（与后端契约对齐） */
const DISPLAY_NAME_MAX_CODEPOINTS = 40;

/** restore 成功体精确三键 */
const RESTORE_KEYS = [
  "safetyCheckpointId",
  "stateVersion",
  "restoredAt",
] as const;

/** list 顶层精确仅 items */
const LIST_TOP_KEYS = ["items"] as const;

/** page 顶层精确 items + nextCursor */
const PAGE_TOP_KEYS = ["items", "nextCursor"] as const;

/** V1/V2 游标完整长度上限（与后端合同对齐） */
const MAX_PAGE_CURSOR_LEN_V12 = 192;

/** V3 时间范围游标完整长度上限（P12F-E-A/B） */
const MAX_PAGE_CURSOR_LEN_V3 = 256;

/** 无筛选游标版本前缀 */
const PAGE_CURSOR_PREFIX_V1 = "esrc1_";

/** 有来源筛选游标版本前缀（P12F-D） */
const PAGE_CURSOR_PREFIX_V2 = "esrc2_";

/** 时间范围游标版本前缀（P12F-E-A/B） */
const PAGE_CURSOR_PREFIX_V3 = "esrc3_";

/** base64url 安全字符（无 =） */
const PAGE_CURSOR_BODY_RE = /^[A-Za-z0-9_-]+$/;

/**
 * 精确 24 字符 ASCII UTC 毫秒：YYYY-MM-DDTHH:MM:SS.sssZ
 * 只接受大写 T/Z 与三位毫秒。
 */
const UTC_MILLIS_RE =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z$/;

/** 合法 UTC 毫秒闭区间下界 */
const UTC_MILLIS_MIN = Date.parse("1970-01-01T00:00:00.000Z");

/** 合法 UTC 毫秒闭区间上界 */
const UTC_MILLIS_MAX = Date.parse("9999-12-31T23:59:59.999Z");

/** 权威 13 键（与后端 CANONICAL_STATE_KEYS 对齐） */
const CANONICAL_SNAPSHOT_KEYS = [
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

/** 权威字段键字面量类型 */
export type CanonicalStateFieldKey = (typeof CANONICAL_SNAPSHOT_KEYS)[number];

/** 13 键固定中文标签（可见层唯一业务字段名） */
export const CANONICAL_FIELD_LABELS: Record<CanonicalStateFieldKey, string> = {
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

const CANONICAL_FIELD_INDEX = new Map<string, number>(
  CANONICAL_SNAPSHOT_KEYS.map((k, i) => [k, i]),
);

/** comparison 顶层精确四键 */
const COMPARISON_TOP_KEYS = [
  "sameState",
  "changedFields",
  "currentSummary",
  "targetSummary",
] as const;

/** comparison 两侧摘要精确六键 */
const COMPARISON_SUMMARY_KEYS = [
  "outlineNodeCount",
  "chapterCount",
  "factCount",
  "responseMatrixRowCount",
  "businessEntryTotal",
  "hasParsedMarkdown",
] as const;

/** body-diff 顶层精确六键（单修订对当前） */
const BODY_DIFF_TOP_KEYS = [
  "sameBody",
  "changedChapterCount",
  "currentChapterCount",
  "targetChapterCount",
  "truncated",
  "items",
] as const;

/** pair body-diff 顶层精确六键（双历史修订） */
const PAIR_BODY_DIFF_TOP_KEYS = [
  "sameBody",
  "changedChapterCount",
  "beforeChapterCount",
  "afterChapterCount",
  "truncated",
  "items",
] as const;

/** body-diff item 精确五键 */
const BODY_DIFF_ITEM_KEYS = [
  "ordinal",
  "kind",
  "beforeTitle",
  "afterTitle",
  "hunks",
] as const;

/** body-diff hunk 精确二键 */
const BODY_DIFF_HUNK_KEYS = ["op", "text"] as const;

/** body-diff kind 枚举 */
const BODY_DIFF_KINDS = ["added", "removed", "changed"] as const;

/** body-diff hunk op 枚举 */
const BODY_DIFF_OPS = ["equal", "delete", "insert"] as const;

const BODY_DIFF_KIND_SET = new Set<string>(BODY_DIFF_KINDS);
const BODY_DIFF_OP_SET = new Set<string>(BODY_DIFF_OPS);

/** 与后端展示上限对齐：防止恶意超大响应拖垮页面 */
const MAX_BODY_DIFF_ITEMS = 100;
const MAX_BODY_DIFF_HUNKS = 80;
const MAX_BODY_DIFF_HUNK_TEXT = 2_000;
const MAX_BODY_DIFF_TITLE = 240;
const MAX_BODY_DIFF_TOTAL_TEXT = 120_000;

const MAX_LIST_ITEMS = 10;

/** P12F-F-B 搜索结果上限：与后端候选窗一致，独立于 list/page 的 10 条合同 */
const MAX_SEARCH_ITEMS = 20;

/** 搜索关键词 NFKC 后 Unicode 码点上限 */
const MAX_SEARCH_QUERY_CODEPOINTS = 64;

/** P12F-A 保留上限：前端累计最多 20 条 */
export const MAX_RETAINED_REVISIONS = 20;

/** 摘要计数遍历上限，防止恶意深树耗尽页面 */
const MAX_COUNT_NODES = 10_000;
const MAX_COUNT_DEPTH = 32;

/**
 * 用途：校验服务端 stateVersion；不得本地生成。
 */
export function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/**
 * 用途：校验服务端 revisionId；仅作请求参数/内存 key，禁止渲染到 DOM。
 */
export function isValidRevisionId(value: unknown): value is string {
  return typeof value === "string" && REVISION_ID_RE.test(value);
}

/**
 * 用途：校验服务端 safetyCheckpointId。
 */
export function isValidCheckpointId(value: unknown): value is string {
  return typeof value === "string" && CHECKPOINT_ID_RE.test(value);
}

/**
 * 用途：非负安全整数（计数/字节）。
 */
function isNonNegativeSafeInt(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
  );
}

/**
 * 用途：对象键集合精确等于期望（顺序无关）。
 */
function hasExactKeys(
  o: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const keys = Object.keys(o);
  if (keys.length !== expected.length) return false;
  const set = new Set(expected);
  return keys.every((k) => set.has(k));
}

/**
 * 模块：修订元数据（无 snapshot）
 */
export type EditorStateRevisionMeta = {
  revisionId: string;
  stateVersion: string;
  snapshotBytes: number;
  sourceKind: RevisionSourceKind;
  createdAt: string;
  /** 可选展示名称；null 表示未命名 */
  displayName: string | null;
  /** 是否固定；原生 boolean */
  isPinned: boolean;
};

/**
 * 模块：游标页响应（P12F-C）
 * 约束：顶层仅 items/nextCursor；items 最多 10；非空 nextCursor 时 items 恰好 10。
 */
export type EditorStateRevisionPage = {
  items: EditorStateRevisionMeta[];
  nextCursor: string | null;
};

/**
 * 模块：可见内容搜索请求（P12F-F-B）
 * 约束：query 必填原样；可选来源/时间仅非空时进入 body；无 cursor。
 */
export type EditorStateRevisionSearchQuery = {
  query: string;
  sourceKind?: RevisionSourceKind | null;
  createdFrom?: string | null;
  createdBefore?: string | null;
};

/**
 * 模块：P12M 搜索命中原因
 * 约束：仅 displayName / visibleContent；固定顺序。
 */
export type EditorStateRevisionMatchReason =
  (typeof MATCH_REASON_ORDER)[number];

/**
 * 模块：搜索成功项（P12M 八键）
 * 约束：七键元数据 + 非空 matchReasons；list/page 不得伪造该键。
 */
export type EditorStateRevisionSearchItem = EditorStateRevisionMeta & {
  matchReasons: EditorStateRevisionMatchReason[];
};

/**
 * 模块：可见内容搜索响应（P12F-F-B / P12M）
 * 约束：顶层精确 {items}；最多 20；revisionId 唯一；无 nextCursor；项为八键。
 */
export type EditorStateRevisionSearchResult = {
  items: EditorStateRevisionSearchItem[];
};

/**
 * 模块：详情有界摘要（不含正文）
 * 用途：API 栈内压缩后返回组件；组件不得再接触 snapshot。
 */
export type EditorStateRevisionSummary = {
  outlineNodeCount: number;
  chapterCount: number;
  factCount: number;
  responseMatrixRowCount: number;
  businessEntryTotal: number;
  hasParsedMarkdown: boolean;
};

/**
 * 模块：恢复成功响应
 */
export type EditorStateRevisionRestoreResult = {
  safetyCheckpointId: string;
  stateVersion: string;
  restoredAt: string;
};

/**
 * 模块：修订与当前状态差异摘要（P12D-B）
 * 约束：仅四键；changedFields 为 13 键有序无重复子序列；两侧摘要各六键。
 */
export type EditorStateRevisionComparison = {
  sameState: boolean;
  changedFields: CanonicalStateFieldKey[];
  currentSummary: EditorStateRevisionSummary;
  targetSummary: EditorStateRevisionSummary;
};

/** body-diff 章节变更类型 */
export type BodyDiffKind = (typeof BODY_DIFF_KINDS)[number];

/** body-diff 行操作类型 */
export type BodyDiffOp = (typeof BODY_DIFF_OPS)[number];

/**
 * 模块：正文差异 hunk（P12E-A）
 * 约束：仅 op/text；op 限定 equal|delete|insert。
 */
export type EditorStateRevisionBodyDiffHunk = {
  op: BodyDiffOp;
  text: string;
};

/**
 * 模块：正文差异单章项（P12E-A）
 * 约束：仅 ordinal/kind/beforeTitle/afterTitle/hunks。
 */
export type EditorStateRevisionBodyDiffItem = {
  ordinal: number;
  kind: BodyDiffKind;
  beforeTitle: string;
  afterTitle: string;
  hunks: EditorStateRevisionBodyDiffHunk[];
};

/**
 * 模块：修订与当前状态章节正文差异（P12E-A）
 * 约束：顶层精确六键；sameBody 当且仅当 items 为空且 changedChapterCount=0。
 */
export type EditorStateRevisionBodyDiff = {
  sameBody: boolean;
  changedChapterCount: number;
  currentChapterCount: number;
  targetChapterCount: number;
  truncated: boolean;
  items: EditorStateRevisionBodyDiffItem[];
};

/**
 * 模块：两条历史修订章节正文差异（P12E-B/C）
 * 约束：顶层精确六键 before/after；sameBody 当且仅当 items 为空。
 */
export type EditorStateRevisionPairBodyDiff = {
  sameBody: boolean;
  changedChapterCount: number;
  beforeChapterCount: number;
  afterChapterCount: number;
  truncated: boolean;
  items: EditorStateRevisionBodyDiffItem[];
};

/**
 * 用途：判断字符是否为命名可见安全规则禁止字符。
 */
function isForbiddenDisplayNameChar(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  if (code < 0x20 || code === 0x7f || (code >= 0x80 && code <= 0x9f)) {
    return true;
  }
  if (ch === "\u2028" || ch === "\u2029") return true;
  if (
    ch === "\u061c" ||
    ch === "\u200e" ||
    ch === "\u200f" ||
    ch === "\u202a" ||
    ch === "\u202b" ||
    ch === "\u202c" ||
    ch === "\u202d" ||
    ch === "\u202e" ||
    ch === "\u2066" ||
    ch === "\u2067" ||
    ch === "\u2068" ||
    ch === "\u2069"
  ) {
    return true;
  }
  return false;
}

/**
 * 用途：严格解析/校验 displayName 字段（响应与本地可判定保存值）。
 * 规则：null 合法；string 须 NFKC 后等于自身、首尾无空白、1..40 码点、无控制/双向字符。
 */
export function parseDisplayNameValue(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string") {
    throw new Error("revision_display_name_invalid");
  }
  if (value === "" || value.trim() !== value) {
    throw new Error("revision_display_name_invalid");
  }
  for (const ch of value) {
    if (isForbiddenDisplayNameChar(ch)) {
      throw new Error("revision_display_name_invalid");
    }
  }
  const normalized = value.normalize("NFKC");
  if (normalized !== value) {
    throw new Error("revision_display_name_invalid");
  }
  const n = [...value].length;
  if (n < 1 || n > DISPLAY_NAME_MAX_CODEPOINTS) {
    throw new Error("revision_display_name_invalid");
  }
  return value;
}

/**
 * 用途：前端保存前可判定合法非空名称；非法返回 null（调用方零请求）。
 * 规则：trim 后非空；NFKC；1..40；无控制/双向；规范化后仍首尾无空白。
 */
export function normalizeDisplayNameForSave(raw: string): string | null {
  if (typeof raw !== "string") return null;
  if (raw === "" || raw.trim() !== raw) return null;
  for (const ch of raw) {
    if (isForbiddenDisplayNameChar(ch)) return null;
  }
  const normalized = raw.normalize("NFKC");
  if (normalized === "" || normalized.trim() !== normalized) return null;
  for (const ch of normalized) {
    if (isForbiddenDisplayNameChar(ch)) return null;
  }
  const n = [...normalized].length;
  if (n < 1 || n > DISPLAY_NAME_MAX_CODEPOINTS) return null;
  return normalized;
}

/**
 * 用途：严格解析列表元数据；精确七键，任一字段非法抛固定错误。
 */
export function parseRevisionMeta(raw: unknown): EditorStateRevisionMeta {
  if (!raw || typeof raw !== "object") {
    throw new Error("revision_meta_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, META_KEYS)) {
    throw new Error("revision_meta_invalid");
  }
  if (!isValidRevisionId(o.revisionId)) {
    throw new Error("revision_meta_invalid");
  }
  if (!isValidStateVersion(o.stateVersion)) {
    throw new Error("revision_meta_invalid");
  }
  if (!isNonNegativeSafeInt(o.snapshotBytes)) {
    throw new Error("revision_meta_invalid");
  }
  if (
    typeof o.sourceKind !== "string" ||
    !SOURCE_KIND_SET.has(o.sourceKind)
  ) {
    throw new Error("revision_meta_invalid");
  }
  if (typeof o.createdAt !== "string" || !o.createdAt.trim()) {
    throw new Error("revision_meta_invalid");
  }
  let displayName: string | null;
  try {
    displayName = parseDisplayNameValue(o.displayName);
  } catch {
    throw new Error("revision_meta_invalid");
  }
  // 仅原生 boolean；拒绝 0/1/字符串/null
  if (typeof o.isPinned !== "boolean") {
    throw new Error("revision_meta_invalid");
  }
  return {
    revisionId: o.revisionId,
    stateVersion: o.stateVersion,
    snapshotBytes: o.snapshotBytes,
    sourceKind: o.sourceKind as RevisionSourceKind,
    createdAt: o.createdAt,
    displayName,
    isPinned: o.isPinned,
  };
}

/**
 * 用途：有界计数数组长度；非数组返回 0；超限抛错。
 */
function boundedArrayLength(value: unknown, budget: { n: number }): number {
  if (!Array.isArray(value)) return 0;
  budget.n += 1;
  if (budget.n > MAX_COUNT_NODES) {
    throw new Error("revision_summary_invalid");
  }
  return value.length;
}

/**
 * 用途：有界递归统计大纲树节点数。
 */
function countOutlineNodes(
  nodes: unknown,
  depth: number,
  budget: { n: number },
): number {
  if (depth > MAX_COUNT_DEPTH) {
    throw new Error("revision_summary_invalid");
  }
  if (!Array.isArray(nodes)) return 0;
  let total = 0;
  for (const node of nodes) {
    budget.n += 1;
    if (budget.n > MAX_COUNT_NODES) {
      throw new Error("revision_summary_invalid");
    }
    total += 1;
    if (node && typeof node === "object") {
      const children = (node as { children?: unknown }).children;
      total += countOutlineNodes(children, depth + 1, budget);
    }
  }
  return total;
}

/**
 * 用途：从已校验 13 键 snapshot 压缩有界摘要；结束后丢弃 snapshot 引用。
 * 约束：非法/过深结构固定失败；禁止递归耗尽页面。
 */
export function summarizeCanonicalSnapshot(
  snapshot: Record<string, unknown>,
): EditorStateRevisionSummary {
  const budget = { n: 0 };
  const outlineNodeCount = countOutlineNodes(snapshot.outline, 0, budget);
  const chapterCount = boundedArrayLength(snapshot.chapters, budget);
  const factCount = boundedArrayLength(snapshot.facts, budget);
  const responseMatrixRowCount = boundedArrayLength(
    snapshot.responseMatrix,
    budget,
  );
  const qualify = boundedArrayLength(snapshot.businessQualify, budget);
  const toc = boundedArrayLength(snapshot.businessToc, budget);
  const commit = boundedArrayLength(snapshot.businessCommit, budget);
  let quoteRows = 0;
  const bq = snapshot.businessQuote;
  if (bq && typeof bq === "object") {
    quoteRows = boundedArrayLength(
      (bq as { rows?: unknown }).rows,
      budget,
    );
  }
  const parsed = snapshot.parsedMarkdown;
  const hasParsedMarkdown =
    typeof parsed === "string" ? parsed.trim().length > 0 : false;
  return {
    outlineNodeCount,
    chapterCount,
    factCount,
    responseMatrixRowCount,
    businessEntryTotal: qualify + toc + quoteRows + commit,
    hasParsedMarkdown,
  };
}

/**
 * 用途：严格解析详情；元数据必须与列表项逐值一致；snapshot 精确 13 键后立即压缩摘要。
 * 返回：meta + summary；不返回原始 snapshot。
 */
export function parseRevisionDetail(
  raw: unknown,
  expectedMeta: EditorStateRevisionMeta,
): {
  meta: EditorStateRevisionMeta;
  summary: EditorStateRevisionSummary;
} {
  if (!raw || typeof raw !== "object") {
    throw new Error("revision_detail_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, DETAIL_KEYS)) {
    throw new Error("revision_detail_invalid");
  }
  const meta = parseRevisionMeta({
    revisionId: o.revisionId,
    stateVersion: o.stateVersion,
    snapshotBytes: o.snapshotBytes,
    sourceKind: o.sourceKind,
    createdAt: o.createdAt,
    displayName: o.displayName,
    isPinned: o.isPinned,
  });
  // 七项元数据必须与当前列表项逐值一致
  if (
    meta.revisionId !== expectedMeta.revisionId ||
    meta.stateVersion !== expectedMeta.stateVersion ||
    meta.snapshotBytes !== expectedMeta.snapshotBytes ||
    meta.sourceKind !== expectedMeta.sourceKind ||
    meta.createdAt !== expectedMeta.createdAt ||
    meta.displayName !== expectedMeta.displayName ||
    meta.isPinned !== expectedMeta.isPinned
  ) {
    throw new Error("revision_detail_meta_mismatch");
  }
  const snap = o.snapshot;
  if (!snap || typeof snap !== "object" || Array.isArray(snap)) {
    throw new Error("revision_detail_invalid");
  }
  const snapObj = snap as Record<string, unknown>;
  if (!hasExactKeys(snapObj, CANONICAL_SNAPSHOT_KEYS)) {
    throw new Error("revision_detail_invalid");
  }
  const summary = summarizeCanonicalSnapshot(snapObj);
  return { meta, summary };
}

/**
 * 用途：严格解析恢复响应；精确三键；stateVersion 必须合法 esv_。
 */
export function parseRestoreResult(
  raw: unknown,
): EditorStateRevisionRestoreResult {
  if (!raw || typeof raw !== "object") {
    throw new Error("revision_restore_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, RESTORE_KEYS)) {
    throw new Error("revision_restore_invalid");
  }
  if (!isValidCheckpointId(o.safetyCheckpointId)) {
    throw new Error("revision_restore_invalid");
  }
  if (!isValidStateVersion(o.stateVersion)) {
    throw new Error("revision_restore_invalid");
  }
  if (o.stateVersion !== o.stateVersion.trim()) {
    throw new Error("revision_restore_invalid");
  }
  if (typeof o.restoredAt !== "string" || !o.restoredAt.trim()) {
    throw new Error("revision_restore_invalid");
  }
  return {
    safetyCheckpointId: o.safetyCheckpointId,
    stateVersion: o.stateVersion,
    restoredAt: o.restoredAt,
  };
}

/**
 * 用途：严格解析 comparison 两侧摘要；精确六键；计数非负安全整数；hasParsedMarkdown 布尔。
 * 约束：不从 snapshot 重算；额外/缺失/类型错误一律固定失败。
 */
function parseComparisonSummary(raw: unknown): EditorStateRevisionSummary {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_comparison_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, COMPARISON_SUMMARY_KEYS)) {
    throw new Error("revision_comparison_invalid");
  }
  if (!isNonNegativeSafeInt(o.outlineNodeCount)) {
    throw new Error("revision_comparison_invalid");
  }
  if (!isNonNegativeSafeInt(o.chapterCount)) {
    throw new Error("revision_comparison_invalid");
  }
  if (!isNonNegativeSafeInt(o.factCount)) {
    throw new Error("revision_comparison_invalid");
  }
  if (!isNonNegativeSafeInt(o.responseMatrixRowCount)) {
    throw new Error("revision_comparison_invalid");
  }
  if (!isNonNegativeSafeInt(o.businessEntryTotal)) {
    throw new Error("revision_comparison_invalid");
  }
  if (typeof o.hasParsedMarkdown !== "boolean") {
    throw new Error("revision_comparison_invalid");
  }
  return {
    outlineNodeCount: o.outlineNodeCount,
    chapterCount: o.chapterCount,
    factCount: o.factCount,
    responseMatrixRowCount: o.responseMatrixRowCount,
    businessEntryTotal: o.businessEntryTotal,
    hasParsedMarkdown: o.hasParsedMarkdown,
  };
}

/**
 * 用途：严格解析 comparison 响应；顶层精确四键；changedFields 为 13 键有序无重复子序列。
 * 约束：sameState 当且仅当 changedFields 为空；失败不携带字段值。
 */
export function parseRevisionComparison(
  raw: unknown,
): EditorStateRevisionComparison {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_comparison_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, COMPARISON_TOP_KEYS)) {
    throw new Error("revision_comparison_invalid");
  }
  if (typeof o.sameState !== "boolean") {
    throw new Error("revision_comparison_invalid");
  }
  if (!Array.isArray(o.changedFields)) {
    throw new Error("revision_comparison_invalid");
  }
  const seen = new Set<string>();
  let lastIndex = -1;
  const changedFields: CanonicalStateFieldKey[] = [];
  for (const item of o.changedFields) {
    if (typeof item !== "string") {
      throw new Error("revision_comparison_invalid");
    }
    const idx = CANONICAL_FIELD_INDEX.get(item);
    if (idx === undefined) {
      throw new Error("revision_comparison_invalid");
    }
    if (seen.has(item)) {
      throw new Error("revision_comparison_invalid");
    }
    if (idx <= lastIndex) {
      // 乱序：必须严格沿权威 13 键递增
      throw new Error("revision_comparison_invalid");
    }
    seen.add(item);
    lastIndex = idx;
    changedFields.push(item as CanonicalStateFieldKey);
  }
  if (o.sameState !== (changedFields.length === 0)) {
    throw new Error("revision_comparison_invalid");
  }
  const currentSummary = parseComparisonSummary(o.currentSummary);
  const targetSummary = parseComparisonSummary(o.targetSummary);
  return {
    sameState: o.sameState,
    changedFields,
    currentSummary,
    targetSummary,
  };
}

/**
 * 用途：GET 最近 10 条元数据；不请求详情 snapshot。
 * 对接：GET /projects/{projectId}/editor-state-revisions
 * 说明：P12F-C 面板首屏改用 listEditorStateRevisionPage；本函数保留兼容。
 */
export async function listEditorStateRevisions(
  projectId: string,
): Promise<EditorStateRevisionMeta[]> {
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions`,
  );
  if (!raw || typeof raw !== "object") {
    throw new Error("revision_list_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, LIST_TOP_KEYS)) {
    throw new Error("revision_list_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("revision_list_invalid");
  }
  if (o.items.length > MAX_LIST_ITEMS) {
    throw new Error("revision_list_invalid");
  }
  return o.items.map((item) => parseRevisionMeta(item));
}

/**
 * 用途：校验不透明游标外壳；禁止解码或本地生成。
 * 规则：
 *   - esrc1_/esrc2_：完整长度 ≤192
 *   - esrc3_：完整长度 ≤256
 *   - 无首尾空白、其余仅 base64url 安全字符且无 =
 */
export function isValidPageCursor(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value.length === 0) return false;
  if (value.trim() !== value) return false;
  let prefix: string | null = null;
  let maxLen: number;
  if (value.startsWith(PAGE_CURSOR_PREFIX_V3)) {
    prefix = PAGE_CURSOR_PREFIX_V3;
    maxLen = MAX_PAGE_CURSOR_LEN_V3;
  } else if (value.startsWith(PAGE_CURSOR_PREFIX_V2)) {
    prefix = PAGE_CURSOR_PREFIX_V2;
    maxLen = MAX_PAGE_CURSOR_LEN_V12;
  } else if (value.startsWith(PAGE_CURSOR_PREFIX_V1)) {
    prefix = PAGE_CURSOR_PREFIX_V1;
    maxLen = MAX_PAGE_CURSOR_LEN_V12;
  } else {
    return false;
  }
  if (value.length > maxLen) return false;
  const body = value.slice(prefix.length);
  if (body.length === 0) return false;
  if (body.includes("=")) return false;
  return PAGE_CURSOR_BODY_RE.test(body);
}

/**
 * 用途：校验精确 24 字符 ASCII UTC 毫秒时间；合法日历往返且在 1970..9999。
 * 约束：失败不反射输入；拒绝空串/空白/偏移/非三位毫秒。
 */
export function isValidUtcMillisString(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value.length !== 24) return false;
  // 必须为纯 ASCII（正则已限数字与 T/Z/:/.）
  for (let i = 0; i < value.length; i++) {
    if (value.charCodeAt(i) > 127) return false;
  }
  const m = UTC_MILLIS_RE.exec(value);
  if (!m) return false;
  const year = Number(m[1]);
  if (year < 1970 || year > 9999) return false;
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return false;
  if (parsed < UTC_MILLIS_MIN || parsed > UTC_MILLIS_MAX) return false;
  // 日历规范往返：拒绝 02-30 等非法日期被 Date 归一化
  if (new Date(parsed).toISOString() !== value) return false;
  return true;
}

/**
 * 模块：游标页查询参数（P12F-D / P12F-E-B）
 * 组合：无参 / 仅 sourceKind / 仅 cursor / sourceKind+cursor /
 *       单边或双边时间 / 来源+时间 / 来源+时间+cursor。
 */
export type EditorStateRevisionPageQuery = {
  cursor?: string | null;
  sourceKind?: RevisionSourceKind | null;
  /** 包含下界；精确 24 字符 UTC 毫秒 */
  createdFrom?: string | null;
  /** 排除上界；精确 24 字符 UTC 毫秒 */
  createdBefore?: string | null;
};

/**
 * 用途：严格解析游标页；顶层精确 items/nextCursor；页内 ID 唯一；非空游标时恰好 10 条。
 * 约束：失败固定抛错，不携带响应原文/游标/ID。
 */
export function parseRevisionPage(raw: unknown): EditorStateRevisionPage {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_page_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, PAGE_TOP_KEYS)) {
    throw new Error("revision_page_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("revision_page_invalid");
  }
  if (o.items.length > MAX_LIST_ITEMS) {
    throw new Error("revision_page_invalid");
  }
  const items = o.items.map((item) => parseRevisionMeta(item));
  const seen = new Set<string>();
  for (const meta of items) {
    if (seen.has(meta.revisionId)) {
      throw new Error("revision_page_invalid");
    }
    seen.add(meta.revisionId);
  }
  let nextCursor: string | null;
  if (o.nextCursor === null) {
    nextCursor = null;
  } else if (isValidPageCursor(o.nextCursor)) {
    nextCursor = o.nextCursor;
  } else {
    throw new Error("revision_page_invalid");
  }
  // 非空 nextCursor 时本页必须恰好 10 条
  if (nextCursor !== null && items.length !== MAX_LIST_ITEMS) {
    throw new Error("revision_page_invalid");
  }
  return { items, nextCursor };
}

/**
 * 用途：校验搜索关键词；与后端/面板一致，不静默 trim，失败固定内部错误名。
 * 规则：原生字符串、首尾无空白、无 C0/C1/DEL，NFKC 后 1..64 个 Unicode 码点。
 */
export function assertValidSearchQuery(value: unknown): asserts value is string {
  if (typeof value !== "string") {
    throw new Error("revision_search_query_invalid");
  }
  if (value.length === 0) {
    throw new Error("revision_search_query_invalid");
  }
  // 首尾空白（含全空白）
  if (value.trim() !== value) {
    throw new Error("revision_search_query_invalid");
  }
  // C0 / DEL / C1 控制字符
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code <= 0x1f || code === 0x7f || (code >= 0x80 && code <= 0x9f)) {
      throw new Error("revision_search_query_invalid");
    }
  }
  const normalized = value.normalize("NFKC");
  // Unicode 码点（代理对算 1）
  let codepoints = 0;
  for (const _ch of normalized) {
    codepoints += 1;
    if (codepoints > MAX_SEARCH_QUERY_CODEPOINTS) {
      throw new Error("revision_search_query_invalid");
    }
  }
  if (codepoints < 1) {
    throw new Error("revision_search_query_invalid");
  }
}

/**
 * 用途：严格解析 matchReasons；非数组/空/重复/未知/乱序/非原生字符串均失败。
 */
export function parseMatchReasons(
  raw: unknown,
): EditorStateRevisionMatchReason[] {
  if (!Array.isArray(raw)) {
    throw new Error("revision_search_invalid");
  }
  if (raw.length < 1 || raw.length > 2) {
    throw new Error("revision_search_invalid");
  }
  const reasons: EditorStateRevisionMatchReason[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (typeof item !== "string") {
      throw new Error("revision_search_invalid");
    }
    if (!MATCH_REASON_SET.has(item)) {
      throw new Error("revision_search_invalid");
    }
    if (seen.has(item)) {
      throw new Error("revision_search_invalid");
    }
    seen.add(item);
    reasons.push(item as EditorStateRevisionMatchReason);
  }
  // 固定顺序：双命中必须 displayName 在前；单命中允许任一
  if (reasons.length === 2) {
    if (
      reasons[0] !== "displayName" ||
      reasons[1] !== "visibleContent"
    ) {
      throw new Error("revision_search_invalid");
    }
  }
  return reasons;
}

/**
 * 用途：严格解析搜索成功项；精确八键 + matchReasons 约束。
 */
export function parseRevisionSearchItem(
  raw: unknown,
): EditorStateRevisionSearchItem {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_search_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, SEARCH_ITEM_KEYS)) {
    throw new Error("revision_search_invalid");
  }
  // 复用七键元数据校验：先剥 matchReasons 再走 parseRevisionMeta
  const { matchReasons: rawReasons, ...metaRaw } = o;
  const meta = parseRevisionMeta(metaRaw);
  const matchReasons = parseMatchReasons(rawReasons);
  return { ...meta, matchReasons };
}

/**
 * 用途：严格解析搜索响应；顶层精确 items；最多 20；页内 ID 唯一；禁止 nextCursor 等额外键。
 */
export function parseRevisionSearchResult(
  raw: unknown,
): EditorStateRevisionSearchResult {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_search_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, LIST_TOP_KEYS)) {
    throw new Error("revision_search_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("revision_search_invalid");
  }
  if (o.items.length > MAX_SEARCH_ITEMS) {
    throw new Error("revision_search_invalid");
  }
  const items = o.items.map((item) => parseRevisionSearchItem(item));
  const seen = new Set<string>();
  for (const meta of items) {
    if (seen.has(meta.revisionId)) {
      throw new Error("revision_search_invalid");
    }
    seen.add(meta.revisionId);
  }
  return { items };
}

/**
 * 用途：POST 可见内容搜索；原样 query + 可选来源/时间；无 URL query。
 * 对接：POST /projects/{projectId}/editor-state-revisions/search
 * 约束：
 *   - body 键序 query → sourceKind → createdFrom → createdBefore
 *   - 仅非空可选键进入 body；禁止 cursor/limit/offset/page/search/q/snippet
 *   - 关键词不得编码进 URL；不重试、不日志
 */
export async function searchEditorStateRevisions(
  projectId: string,
  query: EditorStateRevisionSearchQuery,
): Promise<EditorStateRevisionSearchResult> {
  assertValidSearchQuery(query.query);

  const body: Record<string, unknown> = { query: query.query };

  if (query.sourceKind != null) {
    if (!SOURCE_KIND_SET.has(query.sourceKind)) {
      throw new Error("revision_search_source_invalid");
    }
    body.sourceKind = query.sourceKind;
  }

  let normalizedFrom: string | null = null;
  let normalizedBefore: string | null = null;
  if (query.createdFrom != null && query.createdFrom !== "") {
    if (!isValidUtcMillisString(query.createdFrom)) {
      throw new Error("revision_search_time_range_invalid");
    }
    normalizedFrom = query.createdFrom;
    body.createdFrom = query.createdFrom;
  } else if (query.createdFrom === "") {
    throw new Error("revision_search_time_range_invalid");
  }
  if (query.createdBefore != null && query.createdBefore !== "") {
    if (!isValidUtcMillisString(query.createdBefore)) {
      throw new Error("revision_search_time_range_invalid");
    }
    normalizedBefore = query.createdBefore;
    body.createdBefore = query.createdBefore;
  } else if (query.createdBefore === "") {
    throw new Error("revision_search_time_range_invalid");
  }
  if (
    normalizedFrom != null &&
    normalizedBefore != null &&
    !(normalizedFrom < normalizedBefore)
  ) {
    throw new Error("revision_search_time_range_invalid");
  }

  const path = `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/search`;
  const raw = await apiFetch<unknown>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return parseRevisionSearchResult(raw);
}

/**
 * 用途：GET 游标页；支持来源、UTC 时间范围与 cursor 的精确组合 GET。
 * 对接：GET /projects/{projectId}/editor-state-revisions/page[?sourceKind=&createdFrom=&createdBefore=&cursor=]
 * 约束：
 *   - query 顺序固定 sourceKind → createdFrom → createdBefore → cursor
 *   - 只发送存在的非空值；无 body；禁止解码/生成游标或从游标采用筛选
 *   - 时间必须为精确 24 字符 UTC 毫秒；双边严格 from < before
 *   - 禁止 limit/offset/page/total/hasMore/source/search/q/dateFrom 等别名
 * 兼容：第二参可为历史 cursor 字符串，或查询对象。
 */
export async function listEditorStateRevisionPage(
  projectId: string,
  cursorOrQuery?: string | null | EditorStateRevisionPageQuery,
): Promise<EditorStateRevisionPage> {
  let cursor: string | null | undefined;
  let sourceKind: RevisionSourceKind | null | undefined;
  let createdFrom: string | null | undefined;
  let createdBefore: string | null | undefined;
  if (
    cursorOrQuery !== null &&
    cursorOrQuery !== undefined &&
    typeof cursorOrQuery === "object"
  ) {
    const q = cursorOrQuery as EditorStateRevisionPageQuery;
    cursor = q.cursor;
    sourceKind = q.sourceKind ?? undefined;
    createdFrom = q.createdFrom ?? undefined;
    createdBefore = q.createdBefore ?? undefined;
  } else {
    cursor = cursorOrQuery as string | null | undefined;
    sourceKind = undefined;
    createdFrom = undefined;
    createdBefore = undefined;
  }

  let path = `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/page`;
  const parts: string[] = [];
  // 仅合法九类字面量可进入 query；非法不得静默发送
  if (sourceKind != null) {
    if (!SOURCE_KIND_SET.has(sourceKind)) {
      throw new Error("revision_page_source_invalid");
    }
    parts.push(`sourceKind=${encodeURIComponent(sourceKind)}`);
  }
  // 时间：空串/空白/非法格式/越界/倒序 → 固定内部脱敏错误，不带输入
  let normalizedFrom: string | null = null;
  let normalizedBefore: string | null = null;
  if (createdFrom != null && createdFrom !== "") {
    if (!isValidUtcMillisString(createdFrom)) {
      throw new Error("revision_page_time_range_invalid");
    }
    normalizedFrom = createdFrom;
    parts.push(`createdFrom=${encodeURIComponent(createdFrom)}`);
  } else if (createdFrom === "") {
    throw new Error("revision_page_time_range_invalid");
  }
  if (createdBefore != null && createdBefore !== "") {
    if (!isValidUtcMillisString(createdBefore)) {
      throw new Error("revision_page_time_range_invalid");
    }
    normalizedBefore = createdBefore;
    parts.push(`createdBefore=${encodeURIComponent(createdBefore)}`);
  } else if (createdBefore === "") {
    throw new Error("revision_page_time_range_invalid");
  }
  if (
    normalizedFrom != null &&
    normalizedBefore != null &&
    !(normalizedFrom < normalizedBefore)
  ) {
    throw new Error("revision_page_time_range_invalid");
  }
  // null/undefined → 不带 cursor；空字符串或其它非法游标 → 固定错误
  if (cursor != null) {
    if (!isValidPageCursor(cursor)) {
      throw new Error("revision_page_cursor_invalid");
    }
    parts.push(`cursor=${encodeURIComponent(cursor)}`);
  }
  if (parts.length > 0) {
    path += `?${parts.join("&")}`;
  }
  const raw = await apiFetch<unknown>(path);
  return parseRevisionPage(raw);
}

/**
 * 用途：按需 GET 详情；仅返回有界摘要。
 * 对接：GET /projects/{projectId}/editor-state-revisions/{revisionId}
 */
export async function getEditorStateRevisionSummary(
  projectId: string,
  expectedMeta: EditorStateRevisionMeta,
): Promise<EditorStateRevisionSummary> {
  if (!isValidRevisionId(expectedMeta.revisionId)) {
    throw new Error("revision_id_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(expectedMeta.revisionId)}`,
  );
  const parsed = parseRevisionDetail(raw, expectedMeta);
  return parsed.summary;
}

/**
 * 用途：POST restore，body 仅 expectedStateVersion。
 * 对接：POST /projects/{projectId}/editor-state-revisions/{revisionId}/restore
 */
export async function restoreEditorStateRevision(
  projectId: string,
  revisionId: string,
  expectedStateVersion: string,
): Promise<EditorStateRevisionRestoreResult> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  if (!isValidStateVersion(expectedStateVersion)) {
    throw new Error("expected_state_version_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}/restore`,
    {
      method: "POST",
      body: JSON.stringify({ expectedStateVersion }),
    },
  );
  return parseRestoreResult(raw);
}

/**
 * 用途：DELETE 单条自动修订；无 query/body；成功 204 空体。
 * 对接：DELETE /projects/{projectId}/editor-state-revisions/{revisionId}
 * 约束：
 *   - 非法 revisionId 在发请求前固定抛出内部错误
 *   - init 精确仅 { method: "DELETE" }
 *   - 不读响应 JSON、不重试、不导出响应体类型
 */
export async function deleteEditorStateRevision(
  projectId: string,
  revisionId: string,
): Promise<void> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  await apiFetch<void>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}`,
    { method: "DELETE" },
  );
}

/**
 * 用途：严格解析命名成功响应；精确一键 displayName；必须等于请求规范值。
 */
function parseDisplayNameResponse(
  raw: unknown,
  expected: string | null,
): string | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_display_name_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, DISPLAY_NAME_OUT_KEYS)) {
    throw new Error("revision_display_name_invalid");
  }
  const parsed = parseDisplayNameValue(o.displayName);
  if (parsed !== expected) {
    throw new Error("revision_display_name_invalid");
  }
  return parsed;
}

/**
 * 用途：PATCH 单条修订展示名称；精确 body {displayName}；成功回规范值。
 * 对接：PATCH /projects/{projectId}/editor-state-revisions/{revisionId}/display-name
 * 约束：
 *   - 非法 revisionId 或名称（非 null 且非合法字符串）在发请求前固定抛出
 *   - 禁止 query/retry/轮询/额外 header
 *   - 响应精确一键且等于请求规范值
 */
export async function setEditorStateRevisionDisplayName(
  projectId: string,
  revisionId: string,
  displayName: string | null,
): Promise<string | null> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  let normalized: string | null;
  if (displayName === null) {
    normalized = null;
  } else if (typeof displayName === "string") {
    // 请求侧已规范或可规范的非空名称；再次严格校验
    try {
      const via = normalizeDisplayNameForSave(displayName);
      if (via === null) {
        throw new Error("revision_display_name_invalid");
      }
      normalized = via;
    } catch {
      throw new Error("revision_display_name_invalid");
    }
  } else {
    throw new Error("revision_display_name_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}/display-name`,
    {
      method: "PATCH",
      body: JSON.stringify({ displayName: normalized }),
    },
  );
  return parseDisplayNameResponse(raw, normalized);
}

/**
 * 用途：严格解析固定成功响应；精确一键 isPinned；必须等于请求目标。
 */
function parsePinResponse(raw: unknown, expected: boolean): boolean {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_pin_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, PIN_OUT_KEYS)) {
    throw new Error("revision_pin_invalid");
  }
  if (typeof o.isPinned !== "boolean") {
    throw new Error("revision_pin_invalid");
  }
  if (o.isPinned !== expected) {
    throw new Error("revision_pin_invalid");
  }
  return o.isPinned;
}

/**
 * 用途：PATCH 单条修订固定状态；精确 body {isPinned}；成功回目标布尔。
 * 对接：PATCH /projects/{projectId}/editor-state-revisions/{revisionId}/pin
 * 约束：
 *   - 非法 revisionId 或非原生 bool 在发请求前固定抛出
 *   - 禁止 query/retry/轮询/额外 header
 *   - 响应精确一键且等于请求目标
 */
export async function setEditorStateRevisionPin(
  projectId: string,
  revisionId: string,
  isPinned: boolean,
): Promise<boolean> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  if (typeof isPinned !== "boolean") {
    throw new Error("revision_pin_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}/pin`,
    {
      method: "PATCH",
      body: JSON.stringify({ isPinned }),
    },
  );
  return parsePinResponse(raw, isPinned);
}

/**
 * 用途：按需 GET 修订与当前状态差异摘要；无 body/查询/重试。
 * 对接：GET /projects/{projectId}/editor-state-revisions/{revisionId}/comparison
 */
export async function getEditorStateRevisionComparison(
  projectId: string,
  revisionId: string,
): Promise<EditorStateRevisionComparison> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}/comparison`,
  );
  return parseRevisionComparison(raw);
}

/**
 * 用途：严格解析 body-diff hunk；精确二键；op 枚举；text 有界字符串。
 */
function parseBodyDiffHunk(
  raw: unknown,
  textBudget: { n: number },
): EditorStateRevisionBodyDiffHunk {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_body_diff_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, BODY_DIFF_HUNK_KEYS)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.op !== "string" || !BODY_DIFF_OP_SET.has(o.op)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.text !== "string") {
    throw new Error("revision_body_diff_invalid");
  }
  if ([...o.text].length > MAX_BODY_DIFF_HUNK_TEXT) {
    throw new Error("revision_body_diff_invalid");
  }
  textBudget.n += [...o.text].length;
  if (textBudget.n > MAX_BODY_DIFF_TOTAL_TEXT) {
    throw new Error("revision_body_diff_invalid");
  }
  return { op: o.op as BodyDiffOp, text: o.text };
}

/**
 * 用途：严格解析 body-diff item；精确五键；ordinal 从 1 递增；kind 枚举；标题有界。
 */
function parseBodyDiffItem(
  raw: unknown,
  expectedOrdinal: number,
  textBudget: { n: number },
): EditorStateRevisionBodyDiffItem {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_body_diff_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, BODY_DIFF_ITEM_KEYS)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (
    typeof o.ordinal !== "number" ||
    !Number.isSafeInteger(o.ordinal) ||
    o.ordinal !== expectedOrdinal
  ) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.kind !== "string" || !BODY_DIFF_KIND_SET.has(o.kind)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.beforeTitle !== "string") {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.afterTitle !== "string") {
    throw new Error("revision_body_diff_invalid");
  }
  if ([...o.beforeTitle].length > MAX_BODY_DIFF_TITLE) {
    throw new Error("revision_body_diff_invalid");
  }
  if ([...o.afterTitle].length > MAX_BODY_DIFF_TITLE) {
    throw new Error("revision_body_diff_invalid");
  }
  if (!Array.isArray(o.hunks)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (o.hunks.length > MAX_BODY_DIFF_HUNKS) {
    throw new Error("revision_body_diff_invalid");
  }
  const hunks = o.hunks.map((h) => parseBodyDiffHunk(h, textBudget));
  return {
    ordinal: o.ordinal,
    kind: o.kind as BodyDiffKind,
    beforeTitle: o.beforeTitle,
    afterTitle: o.afterTitle,
    hunks,
  };
}

/**
 * 用途：严格解析 body-diff 响应；顶层精确六键；计数/截断/hunk 一致性。
 * 约束：sameBody 当且仅当 items 为空；changedChapterCount === items.length；拒绝未知键。
 */
export function parseRevisionBodyDiff(
  raw: unknown,
): EditorStateRevisionBodyDiff {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_body_diff_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, BODY_DIFF_TOP_KEYS)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.sameBody !== "boolean") {
    throw new Error("revision_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.changedChapterCount)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.currentChapterCount)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.targetChapterCount)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (typeof o.truncated !== "boolean") {
    throw new Error("revision_body_diff_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (o.items.length > MAX_BODY_DIFF_ITEMS) {
    throw new Error("revision_body_diff_invalid");
  }
  if (o.changedChapterCount !== o.items.length) {
    throw new Error("revision_body_diff_invalid");
  }
  if (o.sameBody !== (o.items.length === 0)) {
    throw new Error("revision_body_diff_invalid");
  }
  if (o.sameBody && o.changedChapterCount !== 0) {
    throw new Error("revision_body_diff_invalid");
  }
  const textBudget = { n: 0 };
  const items = o.items.map((item, idx) =>
    parseBodyDiffItem(item, idx + 1, textBudget),
  );
  return {
    sameBody: o.sameBody,
    changedChapterCount: o.changedChapterCount,
    currentChapterCount: o.currentChapterCount,
    targetChapterCount: o.targetChapterCount,
    truncated: o.truncated,
    items,
  };
}

/**
 * 用途：按需 GET 修订与当前状态章节正文差异；无 body/查询/重试。
 * 对接：GET /projects/{projectId}/editor-state-revisions/{revisionId}/body-diff
 */
export async function getEditorStateRevisionBodyDiff(
  projectId: string,
  revisionId: string,
): Promise<EditorStateRevisionBodyDiff> {
  if (!isValidRevisionId(revisionId)) {
    throw new Error("revision_id_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(revisionId)}/body-diff`,
  );
  return parseRevisionBodyDiff(raw);
}

/**
 * 用途：严格解析双修订 body-diff；顶层精确六键 before/after；复用 item/hunk 与预算校验。
 * 约束：sameBody 当且仅当 items 为空；changedChapterCount === items.length；拒绝未知键。
 */
export function parseRevisionPairBodyDiff(
  raw: unknown,
): EditorStateRevisionPairBodyDiff {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, PAIR_BODY_DIFF_TOP_KEYS)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (typeof o.sameBody !== "boolean") {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.changedChapterCount)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.beforeChapterCount)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (!isNonNegativeSafeInt(o.afterChapterCount)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (typeof o.truncated !== "boolean") {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (o.items.length > MAX_BODY_DIFF_ITEMS) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (o.changedChapterCount !== o.items.length) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (o.sameBody !== (o.items.length === 0)) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  if (o.sameBody && o.changedChapterCount !== 0) {
    throw new Error("revision_pair_body_diff_invalid");
  }
  const textBudget = { n: 0 };
  // 复用 item/hunk 严格解析；失败统一映射为 pair 固定错误标识，避免泄漏内部细节
  let items: EditorStateRevisionBodyDiffItem[];
  try {
    items = o.items.map((item, idx) =>
      parseBodyDiffItem(item, idx + 1, textBudget),
    );
  } catch {
    throw new Error("revision_pair_body_diff_invalid");
  }
  return {
    sameBody: o.sameBody,
    changedChapterCount: o.changedChapterCount,
    beforeChapterCount: o.beforeChapterCount,
    afterChapterCount: o.afterChapterCount,
    truncated: o.truncated,
    items,
  };
}

/**
 * 用途：按需 GET 两条历史修订正文差异；ID 非法或相同固定失败不发请求；无 body/查询/重试。
 * 对接：GET /projects/{projectId}/editor-state-revisions/{before}/body-diff/{after}
 */
export async function getEditorStateRevisionPairBodyDiff(
  projectId: string,
  beforeRevisionId: string,
  afterRevisionId: string,
): Promise<EditorStateRevisionPairBodyDiff> {
  if (!isValidRevisionId(beforeRevisionId) || !isValidRevisionId(afterRevisionId)) {
    throw new Error("revision_id_invalid");
  }
  if (beforeRevisionId === afterRevisionId) {
    throw new Error("revision_pair_same_id");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-revisions/${encodeURIComponent(beforeRevisionId)}/body-diff/${encodeURIComponent(afterRevisionId)}`,
  );
  return parseRevisionPairBodyDiff(raw);
}

/**
 * 用途：body-diff kind 转固定中文标签；不暴露枚举原值。
 * changed 返回空串（由标题 + hunk 的 保留/删除/新增 表达）。
 */
export function formatBodyDiffKindLabel(kind: BodyDiffKind): string {
  if (kind === "added") return "新增";
  if (kind === "removed") return "删除";
  return "";
}

/**
 * 用途：字段键转固定中文标签；未知回退固定文案（正常路径不会触发）。
 */
export function formatCanonicalFieldLabel(
  key: CanonicalStateFieldKey,
): string {
  return CANONICAL_FIELD_LABELS[key] || "未知字段";
}

/**
 * 用途：格式化修订时间（本地展示）；失败回退固定文案。
 */
export function formatRevisionTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "时间未知";
    return d.toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "时间未知";
  }
}

/**
 * 用途：格式化 snapshot 字节数为可读大小。
 */
export function formatRevisionBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return `${Math.floor(bytes)} B`;
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(2) : Math.round(mb)} MB`;
}

/**
 * 用途：来源固定中文标签；未知回退固定文案（正常路径不会触发）。
 */
export function formatRevisionSourceLabel(kind: RevisionSourceKind): string {
  return REVISION_SOURCE_LABELS[kind] || "未知来源";
}

/**
 * 用途：P13-C 严格校验 editor-state 响应中的 currentRevisionSourceKind。
 * 规则：仅接受九类精确字符串；缺失/null/非字符串/大小写变化/首尾空白/未知值一律 null。
 * 对接：useTechnicalPlanEditors / useBusinessBidWorkspace 与 updatedAt 同门接受。
 * 二次开发：禁止第二套白名单或本地猜值；不得 trim 后放宽匹配。
 */
export function parseRevisionSourceKind(
  value: unknown,
): RevisionSourceKind | null {
  if (typeof value !== "string") return null;
  if (!SOURCE_KIND_SET.has(value)) return null;
  return value as RevisionSourceKind;
}
