/**
 * 模块：工作空间解析策略 API 封装
 * 用途：仅调用 GET /settings/parse-strategy，返回 light|managed|local|ask。
 * 对接：apiFetch；useWorkspaceParseStrategy；P8B/V1-M M3 契约。
 * 二次开发：禁止回退完整 /settings；禁止缓存到 localStorage/sessionStorage。
 */

import { apiFetch } from "../../../shared/lib/api";

/** 合法策略枚举（与后端 ALLOWED_PARSE 对齐；M3 含 managed）。 */
export type WorkspaceParseStrategy = "light" | "managed" | "local" | "ask";

const ALLOWED = new Set<WorkspaceParseStrategy>([
  "light",
  "managed",
  "local",
  "ask",
]);

/**
 * 模块：fetchWorkspaceParseStrategy
 * 用途：读取当前工作空间脱敏 parseStrategy。
 * 对接：GET /settings/parse-strategy。
 * 二次开发：非法值视为失败，不得静默降级为 light。
 */
export async function fetchWorkspaceParseStrategy(): Promise<WorkspaceParseStrategy> {
  const data = await apiFetch<{ parseStrategy?: unknown }>("/settings/parse-strategy");
  const raw = data?.parseStrategy;
  if (typeof raw !== "string" || !ALLOWED.has(raw as WorkspaceParseStrategy)) {
    throw new Error("invalid_parse_strategy");
  }
  return raw as WorkspaceParseStrategy;
}
