/**
 * 模块：统一 HTTP 客户端
 * 用途：
 *   1. 拼接 API 根路径与业务 path，统一 JSON 请求头
 *   2. 解析 FastAPI detail 为可读错误信息
 *   3. 健康探针（带短缓存）供顶栏/侧栏显示联通状态
 * 对接：
 *   - Base：import.meta.env.VITE_API_BASE_URL ?? "/api"
 *   - 开发：Vite proxy /api → http://127.0.0.1:8000
 * 二次开发：鉴权头、超时、重试可在此扩展
 */

/** API 根路径（不含业务段）。默认 /api，与后端路由前缀一致。 */
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

/** 用途：调用失败时的结构化错误。 */
export type ApiError = {
  status: number;
  message: string;
};

export type ApiHealthStatus = "online" | "offline" | "unknown";

type HealthCache = {
  status: ApiHealthStatus;
  checkedAt: number;
  service?: string;
  workspaceId?: string;
};

let healthCache: HealthCache = {
  status: "unknown",
  checkedAt: 0,
};

const HEALTH_TTL_MS = 10_000;

/**
 * 用途：把 FastAPI 的 detail（字符串 / 校验数组 / 对象）转成可读文案。
 */
export function parseApiErrorMessage(raw: string, fallback: string): string {
  if (!raw) return fallback;
  try {
    const data = JSON.parse(raw) as { detail?: unknown };
    const detail = data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) {
            const loc = Array.isArray((item as { loc?: unknown }).loc)
              ? (item as { loc: unknown[] }).loc.join(".")
              : "";
            const msg = String((item as { msg: unknown }).msg);
            return loc ? `${loc}: ${msg}` : msg;
          }
          return JSON.stringify(item);
        })
        .join("；");
    }
    if (detail && typeof detail === "object") {
      const message =
        "message" in detail && typeof detail.message === "string"
          ? detail.message
          : fallback;
      const errors =
        "errors" in detail && Array.isArray(detail.errors)
          ? detail.errors
              .map((item) => {
                if (!item || typeof item !== "object") return JSON.stringify(item);
                const row = typeof item.row === "number" ? `第 ${item.row} 行` : "";
                const field = typeof item.field === "string" ? item.field : "";
                const itemMessage =
                  typeof item.message === "string" ? item.message : JSON.stringify(item);
                return [row, field, itemMessage].filter(Boolean).join("：");
              })
              .filter(Boolean)
          : [];
      return errors.length ? `${message}：${errors.join("；")}` : message;
    }
  } catch {
    /* 非 JSON，原文返回 */
  }
  return raw.length > 400 ? `${raw.slice(0, 400)}…` : raw;
}

/**
 * 用途：发起一次 JSON API 请求并解析响应。
 * @param path 以 / 开头，如 "/projects"
 * @throws ApiError 当 !res.ok
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  let res: Response;
  // FormData 时不要强设 JSON Content-Type，否则浏览器无法带 boundary
  const isForm =
    typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers: Record<string, string> = {
    ...(isForm ? {} : { "Content-Type": "application/json" }),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (isForm) {
    delete headers["Content-Type"];
  }
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
    });
  } catch {
    healthCache = { status: "offline", checkedAt: Date.now() };
    throw {
      status: 0,
      message: "无法连接后端，请确认已启动 uvicorn（默认 8000 端口）",
    } satisfies ApiError;
  }

  if (!res.ok) {
    const raw = (await res.text()) || res.statusText;
    const message = parseApiErrorMessage(raw, res.statusText);
    throw { status: res.status, message } satisfies ApiError;
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

/**
 * 用途：multipart 上传文件。
 * 对接：POST /projects/{id}/files
 */
export async function apiUploadFile<T>(
  path: string,
  file: File,
  fieldName = "file",
): Promise<T> {
  const form = new FormData();
  form.append(fieldName, file);
  return apiFetch<T>(path, { method: "POST", body: form });
}

/** 用途：调试或状态条展示当前 API 根路径。 */
export function getApiBase(): string {
  return API_BASE;
}

/**
 * 用途：探测 GET /health；结果缓存约 10s。
 * 对接：侧栏 API 状态点
 */
export async function checkApiHealth(force = false): Promise<HealthCache> {
  const now = Date.now();
  if (!force && now - healthCache.checkedAt < HEALTH_TTL_MS) {
    return healthCache;
  }
  try {
    const data = await apiFetch<{
      status: string;
      service?: string;
      defaultWorkspaceId?: string;
      dbOk?: boolean;
    }>("/health");
    healthCache = {
      status: data.status === "ok" ? "online" : "offline",
      checkedAt: Date.now(),
      service: data.service,
      workspaceId: data.defaultWorkspaceId,
    };
  } catch {
    healthCache = { status: "offline", checkedAt: Date.now() };
  }
  return healthCache;
}

/** 用途：同步读取最近一次健康检查结果（可能为 unknown）。 */
export function getCachedApiHealth(): HealthCache {
  return healthCache;
}
