/**
 * 模块：统一 HTTP 客户端
 * 用途：
 *   1. 拼接 API 根路径与业务 path，统一 JSON 请求头
 *   2. 同源 Cookie 会话（credentials: same-origin），不读取/拼装 Cookie
 *   3. 内存 CSRF：仅对非安全、非登录的同源 API 附加 X-CSRF-Token
 *   4. 解析 FastAPI detail 为可读错误；可选安全 code
 *   5. 健康探针（带短缓存）供顶栏/侧栏显示联通状态
 * 对接：
 *   - Base：import.meta.env.VITE_API_BASE_URL ?? "/api"
 *   - 开发：Vite proxy /api → http://127.0.0.1:8000
 *   - AuthProvider：setCsrfToken / clearCsrfToken
 * 二次开发：禁止把口令、Cookie、CSRF、Token 写入 localStorage/sessionStorage
 */

/** API 根路径（不含业务段）。默认 /api，与后端路由前缀一致。 */
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

/** 用途：调用失败时的结构化错误；code 仅取服务端固定字段，不含原始口令 detail。 */
export type ApiError = {
  status: number;
  message: string;
  /** 服务端固定错误码（如 auth_required），可选 */
  code?: string;
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

/** React 内存中的 CSRF 原始值；不落盘、不读 Cookie */
let memoryCsrfToken: string | null = null;

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

/**
 * 用途：登录成功后写入 CSRF 内存；登出/会话失效时清空。
 */
export function setCsrfToken(token: string | null | undefined): void {
  memoryCsrfToken =
    typeof token === "string" && token.trim() ? token.trim() : null;
}

/** 用途：读取当前内存 CSRF（测试或调试；业务勿持久化）。 */
export function getCsrfToken(): string | null {
  return memoryCsrfToken;
}

/** 用途：退出或会话失效时清空内存 CSRF。 */
export function clearCsrfToken(): void {
  memoryCsrfToken = null;
}

/**
 * 用途：把 FastAPI 的 detail（字符串 / 校验数组 / 对象）转成可读文案。
 * 注意：仅用于展示 message，不得把含口令的原始 body 持久化。
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
 * 用途：从错误 JSON 中安全提取固定 code 字段。
 */
function parseApiErrorCode(raw: string): string | undefined {
  if (!raw) return undefined;
  try {
    const data = JSON.parse(raw) as { detail?: unknown };
    const detail = data?.detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      const code = (detail as { code?: unknown }).code;
      if (typeof code === "string" && code.trim()) return code.trim();
    }
  } catch {
    /* ignore */
  }
  return undefined;
}

function isLoginPath(path: string): boolean {
  return path === "/auth/login" || path.startsWith("/auth/login?");
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
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    ...(isForm ? {} : { "Content-Type": "application/json" }),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (isForm) {
    delete headers["Content-Type"];
  }

  // 非安全方法且非登录：附加内存 CSRF；从不读取 document.cookie
  if (
    !SAFE_METHODS.has(method) &&
    !isLoginPath(path) &&
    memoryCsrfToken &&
    !headers["X-CSRF-Token"]
  ) {
    headers["X-CSRF-Token"] = memoryCsrfToken;
  }

  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
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
    const code = parseApiErrorCode(raw);
    throw { status: res.status, message, code } satisfies ApiError;
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

/** DOCX 精确主 MIME（可带参数，如 charset） */
const DOCX_MIME_MAIN =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * 用途：严格校验可作浏览器保存名的 DOCX 文件名；非法则拒绝（不清洗）。
 * 对接：Content-Disposition 解析结果与 task.result.filename 共用同一规则。
 * 二次开发：禁止把 ../evil|name?.docx 等危险串洗成可用名。
 */
export function isSafeDocxDownloadFilename(name: string): boolean {
  if (typeof name !== "string") return false;
  if (!name || name.length > 260) return false;
  if (!/\.docx$/i.test(name)) return false;
  // 禁止重复扩展名
  if (/\.docx\.docx$/i.test(name)) return false;
  const base = name.slice(0, name.length - ".docx".length);
  if (!base) return false;
  // 码点长度（基础名）
  if ([...base].length > 100) return false;
  // 首尾空白/尾点不允许（后端已收敛；前端拒绝未收敛候选）
  if (base !== base.trim() || /[. ]$/.test(base)) return false;
  // 路径分隔/Windows 非法字符/C0+DEL+C1（U+007F–U+009F）；允许 A..B 等合法双点
  if (/[\u0000-\u001f\u007f-\u009f<>:"/\\|?*]/.test(base)) return false;
  // 整名保留设备名（大小写不敏感）必须已带尾 _
  const reserved = new Set([
    "CON",
    "PRN",
    "AUX",
    "NUL",
    ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
    ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
  ]);
  if (reserved.has(base.toUpperCase())) return false;
  return true;
}

/**
 * 用途：安全解析 Content-Disposition 的 filename* / filename；失败返回 null。
 * 规则：filename* 优先；仅接受可严格校验的结果，绝不回传 detail/path。
 */
export function parseContentDispositionFilename(
  header: string | null | undefined,
): string | null {
  if (!header || typeof header !== "string") return null;
  try {
    const star = header.match(/filename\*\s*=\s*([^;]+)/i);
    if (star) {
      let raw = star[1].trim().replace(/^"|"$/g, "");
      // 仅接受 charset'lang'value 形态（常见 UTF-8''...）
      const m = raw.match(/^([^']*)''(.+)$/);
      if (!m) return null;
      const decoded = decodeURIComponent(m[2]);
      return isSafeDocxDownloadFilename(decoded) ? decoded : null;
    }
    const quoted = header.match(/filename\s*=\s*"([^"]+)"/i);
    if (quoted) {
      const name = quoted[1];
      return isSafeDocxDownloadFilename(name) ? name : null;
    }
    const plain = header.match(/filename\s*=\s*([^;]+)/i);
    if (plain) {
      const name = plain[1].trim().replace(/^"|"$/g, "");
      return isSafeDocxDownloadFilename(name) ? name : null;
    }
  } catch {
    /* 解析失败：视为不可用 */
  }
  return null;
}

function isDocxContentType(contentType: string | null): boolean {
  if (!contentType) return false;
  const main = contentType.split(";")[0].trim().toLowerCase();
  return main === DOCX_MIME_MAIN;
}

export type DocxBinaryDownload = {
  blob: Blob;
  /** 响应头安全解析后的文件名；不可用则为 null */
  filename: string | null;
};

/**
 * 用途：同源二进制 GET 下载 DOCX（credentials=same-origin）。
 * 约束：GET 零 CSRF、零 Cookie 读取、零 query 敏感值；非 2xx 不读 body；
 *       仅接受精确 DOCX 主 MIME（可带参数）与非空 Blob。
 * 对接：useProjectPipeline.downloadExport。
 * 二次开发：失败不得向 UI 泄漏 detail/path/正文。
 */
export async function apiFetchDocxBlob(
  path: string,
  init?: { signal?: AbortSignal },
): Promise<DocxBinaryDownload> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "GET",
      credentials: "same-origin",
      signal: init?.signal,
    });
  } catch (err) {
    if (
      err instanceof DOMException &&
      (err.name === "AbortError" || err.name === "TimeoutError")
    ) {
      throw err;
    }
    const e = new Error("download_failed");
    e.name = "DocxDownloadError";
    throw e;
  }

  if (!res.ok) {
    // 非 2xx：禁止读 text/json，避免 detail 进入调用链
    const e = new Error("download_failed");
    e.name = "DocxDownloadError";
    throw e;
  }

  if (!isDocxContentType(res.headers.get("content-type"))) {
    const e = new Error("download_failed");
    e.name = "DocxDownloadError";
    throw e;
  }

  let blob: Blob;
  try {
    blob = await res.blob();
  } catch (err) {
    if (
      err instanceof DOMException &&
      (err.name === "AbortError" || err.name === "TimeoutError")
    ) {
      throw err;
    }
    const e = new Error("download_failed");
    e.name = "DocxDownloadError";
    throw e;
  }

  if (!blob || blob.size <= 0) {
    const e = new Error("download_failed");
    e.name = "DocxDownloadError";
    throw e;
  }

  const headerName = parseContentDispositionFilename(
    res.headers.get("content-disposition"),
  );
  return { blob, filename: headerName };
}
