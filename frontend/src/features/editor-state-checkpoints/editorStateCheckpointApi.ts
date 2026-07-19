/**
 * 模块：P12B-D2 / P12G editor-state 检查点 API 封装
 * 用途：仅封装元数据 list、空对象 create、带 expected 的 restore、单条 display-name PATCH；严格校验响应 shape。
 * 对接：GET|POST|PATCH /api/projects/{id}/editor-state-checkpoints*；apiFetch。
 * 二次开发：禁止请求详情 snapshot；禁止本地生成版本/ID；禁止持久化 checkpoint 正文；
 *   名称不得进入 URL/存储/Cookie/console/外网。
 */

import { apiFetch } from "../../shared/lib/api";

/** 服务端 stateVersion 精确格式 */
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

/** 服务端 checkpointId 精确格式 */
const CHECKPOINT_ID_RE = /^escp_[0-9a-f]{32}$/;

/** metadata 精确七键（P12G 含 displayName） */
const META_KEYS = [
  "checkpointId",
  "stateVersion",
  "snapshotBytes",
  "outlineNodeCount",
  "chapterCount",
  "createdAt",
  "displayName",
] as const;

/** restore 成功响应精确四键 */
const RESTORE_KEYS = [
  "restoredCheckpointId",
  "safetyCheckpointId",
  "stateVersion",
  "restoredAt",
] as const;

/** list 顶层精确仅 items */
const LIST_TOP_KEYS = ["items"] as const;

/** 命名成功响应精确一键 */
const DISPLAY_NAME_OUT_KEYS = ["displayName"] as const;

/** 列表契约上限 */
const MAX_LIST_ITEMS = 20;

/** 展示名称 Unicode 码点上限 */
const DISPLAY_NAME_MAX_CODEPOINTS = 40;

/**
 * 固定内部错误码：create POST 成功体 metadata 非对象，或 stateVersion 缺失/空白/非法。
 * 用途：供 Hook 可判别进入全量阻断；不携带响应原文/ID/版本/snapshot。
 */
export const CHECKPOINT_CREATE_STATE_VERSION_ERROR_CODE =
  "checkpoint_create_state_version_invalid" as const;

/**
 * 用途：create 路径 stateVersion 语义失败的可判别内部错误（非网络/HTTP/额外字段）。
 * 二次开发：禁止把响应原文塞进 message；禁止对 list 外泄到 DOM。
 */
export class CheckpointCreateStateVersionError extends Error {
  readonly code = CHECKPOINT_CREATE_STATE_VERSION_ERROR_CODE;

  constructor() {
    super(CHECKPOINT_CREATE_STATE_VERSION_ERROR_CODE);
    this.name = "CheckpointCreateStateVersionError";
  }
}

/**
 * 用途：判别 parseCheckpointMeta / create 抛出的 stateVersion 专用错误。
 * 约束：仅接受本模块真正 new 出的类实例（instanceof）。
 * 禁止信任任意结构化 { code }（ApiError 会从 HTTP 非 2xx detail.code 构造同形对象，
 * 若按 code 字符串匹配会把网络/HTTP 失败误判为成功体版本语义失败并错误全量阻断）。
 */
export function isCheckpointCreateStateVersionError(
  err: unknown,
): err is CheckpointCreateStateVersionError {
  return err instanceof CheckpointCreateStateVersionError;
}

/** 用途：校验服务端 stateVersion；不得本地生成。 */
export function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/** 用途：校验服务端 checkpointId；仅作请求参数/内存 key，禁止渲染到 DOM。 */
export function isValidCheckpointId(value: unknown): value is string {
  return typeof value === "string" && CHECKPOINT_ID_RE.test(value);
}

/**
 * 用途：非负安全整数（计数/字节）；拒绝 NaN、浮点、负数、非 number。
 */
function isNonNegativeSafeInt(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
  );
}

/**
 * 用途：对象键集合精确等于期望（顺序无关）；拒绝任何额外字段（含 snapshot）。
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
 * 用途：拒绝 C0/C1、换行/制表/NUL、U+2028/U+2029 与双向控制字符。
 */
function isForbiddenDisplayNameChar(ch: string): boolean {
  const code = ch.codePointAt(0) ?? 0;
  if (code < 0x20 || code === 0x7f || (code >= 0x80 && code <= 0x9f)) {
    return true;
  }
  if (
    ch === "\u2028" ||
    ch === "\u2029" ||
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
    throw new Error("checkpoint_display_name_invalid");
  }
  if (value === "" || value.trim() !== value) {
    throw new Error("checkpoint_display_name_invalid");
  }
  for (const ch of value) {
    if (isForbiddenDisplayNameChar(ch)) {
      throw new Error("checkpoint_display_name_invalid");
    }
  }
  const normalized = value.normalize("NFKC");
  if (normalized !== value) {
    throw new Error("checkpoint_display_name_invalid");
  }
  const n = [...value].length;
  if (n < 1 || n > DISPLAY_NAME_MAX_CODEPOINTS) {
    throw new Error("checkpoint_display_name_invalid");
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
 * 模块：检查点元数据（无 snapshot）
 * 用途：列表与创建响应共用字段；精确七键含 displayName。
 */
export type EditorStateCheckpointMeta = {
  checkpointId: string;
  stateVersion: string;
  snapshotBytes: number;
  outlineNodeCount: number;
  chapterCount: number;
  createdAt: string;
  displayName: string | null;
};

/**
 * 模块：恢复成功响应
 * 用途：仅含 restored/safety id、结果版本与时间；不含正文。
 */
export type EditorStateCheckpointRestoreResult = {
  restoredCheckpointId: string;
  safetyCheckpointId: string;
  stateVersion: string;
  restoredAt: string;
};

/**
 * 用途：严格解析检查点元数据；精确七键，任一字段非法抛错。
 * 二次开发：
 *   - metadata 非对象，或 stateVersion 缺失/空白/非法 → 专用 CheckpointCreateStateVersionError
 *     （须先于 hasExactKeys，避免缺键被普通 shape 错误吞掉；供 create Hook 全量阻断）
 *   - 其余 shape（额外字段/非法 id/计数/名称等）→ 普通 Error，create 仅 failed 不阻断
 *   - 错误固定脱敏（不把响应原文外泄）
 */
export function parseCheckpointMeta(raw: unknown): EditorStateCheckpointMeta {
  if (!raw || typeof raw !== "object") {
    throw new CheckpointCreateStateVersionError();
  }
  const o = raw as Record<string, unknown>;
  // stateVersion 缺失/空白/非法：专用错误（先于精确键校验）
  const sv = o.stateVersion;
  if (
    sv === undefined ||
    typeof sv !== "string" ||
    !sv.trim() ||
    sv !== sv.trim() ||
    !isValidStateVersion(sv)
  ) {
    throw new CheckpointCreateStateVersionError();
  }
  if (!hasExactKeys(o, META_KEYS)) {
    throw new Error("checkpoint_meta_invalid");
  }
  if (!isValidCheckpointId(o.checkpointId)) {
    throw new Error("checkpoint_meta_invalid");
  }
  if (!isNonNegativeSafeInt(o.snapshotBytes)) {
    throw new Error("checkpoint_meta_invalid");
  }
  if (!isNonNegativeSafeInt(o.outlineNodeCount)) {
    throw new Error("checkpoint_meta_invalid");
  }
  if (!isNonNegativeSafeInt(o.chapterCount)) {
    throw new Error("checkpoint_meta_invalid");
  }
  if (typeof o.createdAt !== "string" || !o.createdAt.trim()) {
    throw new Error("checkpoint_meta_invalid");
  }
  let displayName: string | null;
  try {
    displayName = parseDisplayNameValue(o.displayName);
  } catch {
    throw new Error("checkpoint_meta_invalid");
  }
  return {
    checkpointId: o.checkpointId as string,
    stateVersion: sv,
    snapshotBytes: o.snapshotBytes,
    outlineNodeCount: o.outlineNodeCount,
    chapterCount: o.chapterCount,
    createdAt: o.createdAt,
    displayName,
  };
}

/**
 * 用途：严格解析恢复响应；精确四键；stateVersion 必须合法 esv_。
 */
export function parseRestoreResult(
  raw: unknown,
): EditorStateCheckpointRestoreResult {
  if (!raw || typeof raw !== "object") {
    throw new Error("checkpoint_restore_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, RESTORE_KEYS)) {
    throw new Error("checkpoint_restore_invalid");
  }
  if (!isValidCheckpointId(o.restoredCheckpointId)) {
    throw new Error("checkpoint_restore_invalid");
  }
  if (!isValidCheckpointId(o.safetyCheckpointId)) {
    throw new Error("checkpoint_restore_invalid");
  }
  if (!isValidStateVersion(o.stateVersion)) {
    throw new Error("checkpoint_restore_invalid");
  }
  if (typeof o.restoredAt !== "string" || !o.restoredAt.trim()) {
    throw new Error("checkpoint_restore_invalid");
  }
  // 带空白版本视为非法（正则已拒，额外防御 trim 后空白）
  if (o.stateVersion !== o.stateVersion.trim()) {
    throw new Error("checkpoint_restore_invalid");
  }
  return {
    restoredCheckpointId: o.restoredCheckpointId,
    safetyCheckpointId: o.safetyCheckpointId,
    stateVersion: o.stateVersion,
    restoredAt: o.restoredAt,
  };
}

/**
 * 用途：GET 最近 20 条元数据；不请求详情 snapshot。
 * 对接：GET /projects/{projectId}/editor-state-checkpoints
 * 约束：顶层精确 items；最多 20 条；禁止额外字段。
 */
export async function listEditorStateCheckpoints(
  projectId: string,
): Promise<EditorStateCheckpointMeta[]> {
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-checkpoints`,
  );
  if (!raw || typeof raw !== "object") {
    throw new Error("checkpoint_list_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, LIST_TOP_KEYS)) {
    throw new Error("checkpoint_list_invalid");
  }
  if (!Array.isArray(o.items)) {
    throw new Error("checkpoint_list_invalid");
  }
  if (o.items.length > MAX_LIST_ITEMS) {
    throw new Error("checkpoint_list_invalid");
  }
  return o.items.map((item) => parseCheckpointMeta(item));
}

/**
 * 用途：POST 精确空对象 {} 创建服务端当前版本检查点。
 * 对接：POST /projects/{projectId}/editor-state-checkpoints
 * 二次开发：禁止附带 snapshot/名称/版本/备注。
 */
export async function createEditorStateCheckpoint(
  projectId: string,
): Promise<EditorStateCheckpointMeta> {
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-checkpoints`,
    {
      method: "POST",
      body: JSON.stringify({}),
    },
  );
  return parseCheckpointMeta(raw);
}

/**
 * 用途：POST restore，body 仅 expectedStateVersion。
 * 对接：POST /projects/{projectId}/editor-state-checkpoints/{checkpointId}/restore
 * 二次开发：禁止附带 snapshot/force/dryRun。
 */
export async function restoreEditorStateCheckpoint(
  projectId: string,
  checkpointId: string,
  expectedStateVersion: string,
): Promise<EditorStateCheckpointRestoreResult> {
  if (!isValidCheckpointId(checkpointId)) {
    throw new Error("checkpoint_id_invalid");
  }
  if (!isValidStateVersion(expectedStateVersion)) {
    throw new Error("expected_state_version_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-checkpoints/${encodeURIComponent(checkpointId)}/restore`,
    {
      method: "POST",
      body: JSON.stringify({ expectedStateVersion }),
    },
  );
  return parseRestoreResult(raw);
}

/**
 * 用途：严格解析命名成功响应；精确一键 displayName；必须等于请求规范值。
 */
function parseDisplayNameResponse(
  raw: unknown,
  expected: string | null,
): string | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("checkpoint_display_name_invalid");
  }
  const o = raw as Record<string, unknown>;
  if (!hasExactKeys(o, DISPLAY_NAME_OUT_KEYS)) {
    throw new Error("checkpoint_display_name_invalid");
  }
  const parsed = parseDisplayNameValue(o.displayName);
  if (parsed !== expected) {
    throw new Error("checkpoint_display_name_invalid");
  }
  return parsed;
}

/**
 * 用途：PATCH 单条检查点展示名称；精确 body {displayName}；成功回规范值。
 * 对接：PATCH /projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name
 * 约束：
 *   - 非法 checkpointId 或名称（非 null 且非合法字符串）在发请求前固定抛出
 *   - 禁止 query/retry/轮询/额外 header
 *   - 响应精确一键且等于请求规范值
 */
export async function setEditorStateCheckpointDisplayName(
  projectId: string,
  checkpointId: string,
  displayName: string | null,
): Promise<string | null> {
  if (!isValidCheckpointId(checkpointId)) {
    throw new Error("checkpoint_id_invalid");
  }
  let normalized: string | null;
  if (displayName === null) {
    normalized = null;
  } else if (typeof displayName === "string") {
    try {
      const via = normalizeDisplayNameForSave(displayName);
      if (via === null) {
        throw new Error("checkpoint_display_name_invalid");
      }
      normalized = via;
    } catch {
      throw new Error("checkpoint_display_name_invalid");
    }
  } else {
    throw new Error("checkpoint_display_name_invalid");
  }
  const raw = await apiFetch<unknown>(
    `/projects/${encodeURIComponent(projectId)}/editor-state-checkpoints/${encodeURIComponent(checkpointId)}/display-name`,
    {
      method: "PATCH",
      body: JSON.stringify({ displayName: normalized }),
    },
  );
  return parseDisplayNameResponse(raw, normalized);
}

/**
 * 用途：格式化检查点创建时间（本地展示）；失败回退固定文案。
 * 二次开发：不输出 checkpointId/stateVersion。
 */
export function formatCheckpointTime(iso: string): string {
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
export function formatCheckpointBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return `${Math.floor(bytes)} B`;
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb < 10 ? mb.toFixed(2) : Math.round(mb)} MB`;
}
