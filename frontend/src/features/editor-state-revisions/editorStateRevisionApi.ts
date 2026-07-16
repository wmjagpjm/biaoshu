/**
 * 模块：P12C-C3 editor-state 修订历史 API 封装
 * 用途：严格校验 list/detail/restore 响应 shape；详情仅在 API 栈内解析并压缩为有界摘要。
 * 对接：GET|POST /api/projects/{id}/editor-state-revisions*；apiFetch。
 * 二次开发：
 *   - 禁止把原始 snapshot 返回给 React；禁止本地生成 revisionId/version
 *   - 禁止把响应原文、路径、后端 detail 拼进错误文案
 *   - 九类来源白名单；列表最多 10 条
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

/** 列表项精确五键 */
const META_KEYS = [
  "revisionId",
  "stateVersion",
  "snapshotBytes",
  "sourceKind",
  "createdAt",
] as const;

/** 详情精确六键 */
const DETAIL_KEYS = [
  "revisionId",
  "stateVersion",
  "snapshotBytes",
  "sourceKind",
  "createdAt",
  "snapshot",
] as const;

/** restore 成功体精确三键 */
const RESTORE_KEYS = [
  "safetyCheckpointId",
  "stateVersion",
  "restoredAt",
] as const;

/** list 顶层精确仅 items */
const LIST_TOP_KEYS = ["items"] as const;

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

const MAX_LIST_ITEMS = 10;

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
 * 用途：严格解析列表元数据；精确五键，任一字段非法抛固定错误。
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
  return {
    revisionId: o.revisionId,
    stateVersion: o.stateVersion,
    snapshotBytes: o.snapshotBytes,
    sourceKind: o.sourceKind as RevisionSourceKind,
    createdAt: o.createdAt,
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
  });
  // 五项元数据必须与当前列表项逐值一致
  if (
    meta.revisionId !== expectedMeta.revisionId ||
    meta.stateVersion !== expectedMeta.stateVersion ||
    meta.snapshotBytes !== expectedMeta.snapshotBytes ||
    meta.sourceKind !== expectedMeta.sourceKind ||
    meta.createdAt !== expectedMeta.createdAt
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
 * 用途：GET 最近 10 条元数据；不请求详情 snapshot。
 * 对接：GET /projects/{projectId}/editor-state-revisions
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
