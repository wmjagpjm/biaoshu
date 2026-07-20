/**
 * 模块：P13-G2 技术标章节编辑意图 API 与严格解析
 * 用途：复用 P13-F2 文档级 clientId；heartbeat/leave 精确两键 body；
 *       独立 Promise 写队列；严格 200/409 解析，其它固定 unavailable。
 * 对接：POST /api/projects/{projectId}/chapter-edit-lease/heartbeat|leave；
 *       getApiBase/getCsrfToken 同源 fetch；ChapterEditIntentPanel 生命周期。
 * 二次开发：禁止 sendBeacon、弱随机、持久化、console、Cookie/storage/IDB、
 *       错误原文回显；禁止改 api.ts 或 P13-F2 文件。
 */

import { getApiBase, getCsrfToken } from "../../shared/lib/api";
import { getOrCreatePresenceClientId } from "./projectPresenceApi";

/** heartbeat 成功：自身持有意图 */
export type ChapterEditIntentSelf = { kind: "self" };

/** heartbeat 冲突：仅安全用户名 */
export type ChapterEditIntentConflict = {
  kind: "conflict";
  holderUsername: string;
};

/** 网络/HTTP/解析失败统一不可用 */
export type ChapterEditIntentUnavailable = { kind: "unavailable" };

export type ChapterEditIntentResult =
  | ChapterEditIntentSelf
  | ChapterEditIntentConflict
  | ChapterEditIntentUnavailable;

/** 模块级章节租约写链：与 presence 队列独立；失败不毒化后续 */
let chapterEditWriteChain: Promise<void> = Promise.resolve();

const CONFLICT_CODE = "chapter_edit_lease_conflict";
const CONFLICT_MESSAGE = "此章节近期已有处理意图";

/** 与后端一致的用户名禁止字符（行分隔 + 双向控制） */
const USERNAME_FORBIDDEN_CHARS = new Set<string>([
  "\u061c",
  "\u200e",
  "\u200f",
  "\u2028",
  "\u2029",
  "\u202a",
  "\u202b",
  "\u202c",
  "\u202d",
  "\u202e",
  "\u2066",
  "\u2067",
  "\u2068",
  "\u2069",
]);

/**
 * 用途：校验对象是否精确拥有给定键集合（无额外、无缺失）。
 */
function hasExactKeys(value: object, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  if (actual.length !== expected.length) return false;
  const sorted = [...expected].sort();
  for (let i = 0; i < sorted.length; i += 1) {
    if (actual[i] !== sorted[i]) return false;
  }
  return true;
}

/**
 * 用途：1..100 Unicode 码点安全用户名门（与 P13-G1/F2 一致）。
 */
function isSafeHolderUsername(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const n = [...value].length;
  if (n < 1 || n > 100) return false;
  if (value.trim() !== value) return false;
  for (const ch of value) {
    const o = ch.codePointAt(0) ?? 0;
    if (o < 0x20 || o === 0x7f || (o >= 0x80 && o <= 0x9f)) return false;
    if (USERNAME_FORBIDDEN_CHARS.has(ch)) return false;
  }
  return true;
}

/**
 * 用途：leaseExpiresAt 必须为有限可解析时间字符串。
 */
function isFiniteParseableTime(value: unknown): value is string {
  if (typeof value !== "string" || value.length < 1) return false;
  const t = Date.parse(value);
  return Number.isFinite(t);
}

/**
 * 用途：严格解析 heartbeat 200；精确两键 + refreshAfterSeconds===15 + 可解析时间。
 */
export function parseChapterEditHeartbeatOk(
  raw: unknown,
): ChapterEditIntentSelf | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (!hasExactKeys(raw, ["leaseExpiresAt", "refreshAfterSeconds"])) {
    return null;
  }
  const o = raw as Record<string, unknown>;
  if (!isFiniteParseableTime(o.leaseExpiresAt)) return null;
  if (o.refreshAfterSeconds !== 15) return null;
  return { kind: "self" };
}

/**
 * 用途：严格解析 heartbeat 409；顶层仅 detail，detail 精确三键与固定文案。
 */
export function parseChapterEditHeartbeatConflict(
  raw: unknown,
): ChapterEditIntentConflict | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (!hasExactKeys(raw, ["detail"])) return null;
  const detail = (raw as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return null;
  }
  if (
    !hasExactKeys(detail, ["code", "message", "holderUsername"])
  ) {
    return null;
  }
  const d = detail as Record<string, unknown>;
  if (d.code !== CONFLICT_CODE) return null;
  if (d.message !== CONFLICT_MESSAGE) return null;
  if (!isSafeHolderUsername(d.holderUsername)) return null;
  return { kind: "conflict", holderUsername: d.holderUsername };
}

/**
 * 用途：模块级 Promise 串行队列；单次失败不毒化后续链。
 */
export function enqueueChapterEditWrite<T>(fn: () => Promise<T>): Promise<T> {
  const run = chapterEditWriteChain.then(
    () => fn(),
    () => fn(),
  );
  chapterEditWriteChain = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

/**
 * 用途：构造同源 POST 头；CSRF 缺失返回 null（调用方零写/unavailable）。
 */
function buildPostHeaders(): Record<string, string> | null {
  const csrf = getCsrfToken();
  if (!csrf) return null;
  return {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrf,
  };
}

/**
 * 用途：POST heartbeat；路径 encodeURIComponent(projectId)；精确两键 body。
 * 返回：self / conflict / unavailable（永不抛出原文）。
 */
export async function heartbeatChapterEditIntent(
  projectId: string,
  chapterId: string,
): Promise<ChapterEditIntentResult> {
  const clientId = getOrCreatePresenceClientId();
  if (!clientId || !projectId || !chapterId) {
    return { kind: "unavailable" };
  }
  const headers = buildPostHeaders();
  if (!headers) {
    return { kind: "unavailable" };
  }
  const path = `${getApiBase()}/projects/${encodeURIComponent(projectId)}/chapter-edit-lease/heartbeat`;
  try {
    const res = await fetch(path, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify({ clientId, chapterId }),
    });
    let raw: unknown = null;
    try {
      const text = await res.text();
      raw = text ? JSON.parse(text) : null;
    } catch {
      return { kind: "unavailable" };
    }
    if (res.status === 200) {
      return parseChapterEditHeartbeatOk(raw) ?? { kind: "unavailable" };
    }
    if (res.status === 409) {
      return (
        parseChapterEditHeartbeatConflict(raw) ?? { kind: "unavailable" }
      );
    }
    return { kind: "unavailable" };
  } catch {
    return { kind: "unavailable" };
  }
}

/**
 * 用途：POST leave；仅认 204；可 keepalive；其它静默失败。
 */
export async function leaveChapterEditIntent(
  projectId: string,
  chapterId: string,
  options?: { keepalive?: boolean },
): Promise<void> {
  const clientId = getOrCreatePresenceClientId();
  if (!clientId || !projectId || !chapterId) return;
  const headers = buildPostHeaders();
  if (!headers) return;
  const path = `${getApiBase()}/projects/${encodeURIComponent(projectId)}/chapter-edit-lease/leave`;
  try {
    const res = await fetch(path, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify({ clientId, chapterId }),
      keepalive: options?.keepalive === true,
    });
    // 仅 204 视为成功；其它静默，后端 TTL 兜底
    if (res.status !== 204) {
      return;
    }
  } catch {
    /* 静默 */
  }
}
