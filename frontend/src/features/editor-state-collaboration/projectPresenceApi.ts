/**
 * 模块：P13-F2 项目近期成员 presence API 与严格解析
 * 用途：文档级 clientId、heartbeat/leave 精确请求、严格响应解析与模块级串行写队列。
 * 对接：POST /api/projects/{projectId}/presence/heartbeat|leave；apiFetch 同源 Cookie/CSRF；
 *       ProjectPresencePanel 生命周期；后端 P13-F1 固定 15/45 与成员快照协议。
 * 二次开发：禁止 sendBeacon、弱随机、持久化 clientId、日志或错误原文回显；
 *       禁止改 api.ts；clientId 只进模块内存与精确 JSON body。
 */

import { apiFetch } from "../../shared/lib/api";

/** 成功 heartbeat 后的最小成员视图（仅安全用户名与自身标记） */
export type ProjectPresenceMember = {
  username: string;
  isSelf: boolean;
};

/** 成功 heartbeat 解析结果；不含 lease/client 内部字段 */
export type ProjectPresenceSnapshot = {
  members: ProjectPresenceMember[];
  truncated: boolean;
  refreshAfterSeconds: 15;
};

/** 文档级 clientId：undefined=尚未尝试；null=不可用；string=已生成 */
let documentClientId: string | null | undefined;

/** 模块级 presence 写链：单次最多一个在途 heartbeat/leave */
let presenceWriteChain: Promise<void> = Promise.resolve();

/** 后端 clientId 字符集与长度 */
const CLIENT_ID_BACKEND_RE = /^[A-Za-z0-9_-]{22,64}$/;
/** crypto.randomUUID 规范 v4 canonical UUID */
const CLIENT_ID_UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** 与 P13-D2/后端一致的用户名禁止字符（行分隔 + 双向控制） */
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
 * 用途：同时满足后端字符集长度与 canonical UUID v4；否则拒绝。
 */
function isValidPresenceClientId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    CLIENT_ID_BACKEND_RE.test(value) &&
    CLIENT_ID_UUID_V4_RE.test(value)
  );
}

/**
 * 用途：延迟生成文档级 crypto.randomUUID()；失败保守 null，禁止弱随机回退。
 * 对接：同一标签页 SPA 内复用；整页刷新后重新生成。
 * 规则：非字符串/缺失/抛错/非法格式一律缓存 null，禁止发请求。
 */
export function getOrCreatePresenceClientId(): string | null {
  if (documentClientId !== undefined) {
    return documentClientId;
  }
  try {
    if (
      typeof crypto !== "undefined" &&
      typeof crypto.randomUUID === "function"
    ) {
      const id = crypto.randomUUID();
      if (isValidPresenceClientId(id)) {
        documentClientId = id;
        return documentClientId;
      }
    }
  } catch {
    /* 保守禁用：不回显异常原文 */
  }
  documentClientId = null;
  return null;
}

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
 * 用途：与后端一致的 1..100 Unicode 码点安全用户名门。
 * 规则：原生字符串、无首尾空白；拒绝 C0/C1/DEL、U+2028/U+2029 与双向控制。
 */
function isSafePresenceUsername(value: unknown): value is string {
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
 * 用途：严格解析 heartbeat 200 整包；任何坏值返回 null，禁止部分展示。
 * 对接：顶层精确四键；成员精确两键；最多 50；唯一且必须含 self；refreshAfterSeconds=15。
 */
export function parsePresenceHeartbeatResponse(
  raw: unknown,
): ProjectPresenceSnapshot | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (
    !hasExactKeys(raw, [
      "leaseExpiresAt",
      "refreshAfterSeconds",
      "members",
      "truncated",
    ])
  ) {
    return null;
  }
  const o = raw as Record<string, unknown>;
  if (typeof o.leaseExpiresAt !== "string" || o.leaseExpiresAt.length < 1) {
    return null;
  }
  if (o.refreshAfterSeconds !== 15) return null;
  if (typeof o.truncated !== "boolean") return null;
  if (!Array.isArray(o.members) || o.members.length > 50) return null;

  const members: ProjectPresenceMember[] = [];
  let selfCount = 0;
  for (const item of o.members) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    if (!hasExactKeys(item, ["username", "isSelf"])) return null;
    const row = item as Record<string, unknown>;
    if (typeof row.isSelf !== "boolean") return null;
    if (!isSafePresenceUsername(row.username)) return null;
    if (row.isSelf) selfCount += 1;
    members.push({ username: row.username, isSelf: row.isSelf });
  }
  // 成功 heartbeat 必须含唯一自身
  if (selfCount !== 1) return null;

  return {
    members,
    truncated: o.truncated,
    refreshAfterSeconds: 15,
  };
}

/**
 * 用途：模块级 Promise 串行队列；单次失败不毒化后续链。
 */
export function enqueuePresenceWrite<T>(fn: () => Promise<T>): Promise<T> {
  const run = presenceWriteChain.then(
    () => fn(),
    () => fn(),
  );
  // 链本身吞掉拒绝，保证后续入队仍执行
  presenceWriteChain = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

/**
 * 用途：POST heartbeat；路径 encodeURIComponent(projectId)；精确 JSON body。
 * 返回：严格解析快照或 null（网络/HTTP/解析失败一律 null，不抛出原文）。
 */
export async function heartbeatProjectPresence(
  projectId: string,
): Promise<ProjectPresenceSnapshot | null> {
  const clientId = getOrCreatePresenceClientId();
  if (!clientId || !projectId) return null;
  const path = `/projects/${encodeURIComponent(projectId)}/presence/heartbeat`;
  try {
    const raw = await apiFetch<unknown>(path, {
      method: "POST",
      body: JSON.stringify({ clientId }),
    });
    return parsePresenceHeartbeatResponse(raw);
  } catch {
    return null;
  }
}

/**
 * 用途：POST leave；可 keepalive；失败静默。
 * 对接：hidden/切换/卸载/pagehide best-effort。
 */
export async function leaveProjectPresence(
  projectId: string,
  options?: { keepalive?: boolean },
): Promise<void> {
  const clientId = getOrCreatePresenceClientId();
  if (!clientId || !projectId) return;
  const path = `/projects/${encodeURIComponent(projectId)}/presence/leave`;
  try {
    await apiFetch<undefined>(path, {
      method: "POST",
      body: JSON.stringify({ clientId }),
      keepalive: options?.keepalive === true,
    });
  } catch {
    /* 静默；服务端 45s 过期兜底 */
  }
}
