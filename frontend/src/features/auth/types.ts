/**
 * 模块：P10A 认证前端类型
 * 用途：与后端 /api/auth/* 脱敏响应对齐的只读结构。
 * 对接：useAuthSession、LoginPage、apiFetch CSRF 内存会话。
 * 二次开发：禁止在此类型中扩展口令、Cookie、Token 持久化字段。
 */

/** 固定业务角色；与后端 AuthRole 一致 */
export type AuthRole = "bid_writer" | "finance" | "hr" | "bidder";

/** 公开握手：是否已引导 + 是否强制认证 */
export type AuthBootstrapStatus = {
  bootstrapped: boolean;
  authRequired: boolean;
};

/** 脱敏用户 */
export type AuthUser = {
  id: string;
  username: string;
};

/** 当前用户可访问工作空间成员视图 */
export type AuthWorkspace = {
  id: string;
  name: string;
  role: AuthRole;
  isOwner: boolean;
};

/**
 * 登录 / me 脱敏响应。
 * csrfToken 仅登录响应可能非空；GET /me 通常为 null。
 * 硬刷新后由 GET /auth/csrf 单独续发，勿依赖 me 回传。
 */
export type AuthMe = {
  user: AuthUser;
  workspaces: AuthWorkspace[];
  activeWorkspaceId: string | null;
  csrfToken?: string | null;
};

/** GET /auth/csrf 续发响应；仅一次下发原始值 */
export type AuthCsrfResume = {
  csrfToken: string;
};

/** 登录请求体（仅内存使用，不落盘） */
export type AuthLoginRequest = {
  username: string;
  password: string;
};

/**
 * 前端会话阶段。
 * handshake_error：bootstrap-status 握手失败时的保守非业务态，
 * 不得假设为 disabled 以免误开业务壳。
 */
export type AuthPhase =
  | "loading"
  | "disabled"
  | "unauthenticated"
  | "authenticated"
  | "handshake_error";
