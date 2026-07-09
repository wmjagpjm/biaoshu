/**
 * API 客户端占位
 * 用途：统一后端请求入口。当前前端阶段不发起真实请求，
 * 业务数据走各 feature 的 mock。后端就绪后在此实现鉴权头与错误处理。
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export type ApiError = {
  status: number;
  message: string;
};

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const message = (await res.text()) || res.statusText;
    throw { status: res.status, message } satisfies ApiError;
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

export function getApiBase(): string {
  return API_BASE;
}
