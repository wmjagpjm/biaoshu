/**
 * 模块：P10A 前端会话与认证上下文（P13-E 活动空间切换）
 * 用途：
 *   1. 先读 bootstrap-status 区分 disabled / required
 *   2. required 下恢复 /auth/me 会话或展示登录页
 *   3. 登录仅把脱敏 me 与 CSRF 放在 React 内存
 *   4. 单飞 switchWorkspace：精确目标、PUT、坏响应对账
 * 对接：apiFetch CSRF 内存；router 业务壳门禁；AppShell 用户条/选择器
 * 二次开发：禁止 localStorage/sessionStorage 写口令/Cookie/CSRF/Token。
 *
 * CSRF 边界（P13-E 审查）：
 *   - 仅 POST /auth/login 响允许把 csrfToken 写入内存（login 显式 setCsrfToken）
 *   - GET /auth/me 与 PUT /auth/active-workspace 的 parser 必须丢弃 csrfToken，
 *     绝不能让这些响中的任意字符串经 applyMe 覆盖现有内存 CSRF
 *   - 硬刷新后 CSRF 仅由 GET /auth/csrf 续发
 */

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  apiFetch,
  clearCsrfToken,
  getCsrfToken,
  setCsrfToken,
  type ApiError,
} from "../../../shared/lib/api";
import type {
  AuthBootstrapStatus,
  AuthCsrfResume,
  AuthMe,
  AuthPhase,
  AuthRole,
  AuthWorkspace,
} from "../types";

/** 固定中文：切换失败（不回显 detail/URL/ID） */
const SWITCH_FAIL_MESSAGE = "工作空间切换失败，请重试";

const AUTH_ROLES: ReadonlySet<string> = new Set([
  "bid_writer",
  "finance",
  "hr",
  "bidder",
]);

type AuthContextValue = {
  phase: AuthPhase;
  bootstrapped: boolean;
  authRequired: boolean;
  me: AuthMe | null;
  /** 当前活动空间成员视图（角色/所有者） */
  activeMembership: AuthWorkspace | null;
  /** 是否可进入既有业务导航（bid_writer） */
  canAccessBusiness: boolean;
  /** 是否可看设置入口（当前空间所有者） */
  canAccessSettings: boolean;
  /**
   * 是否可进入财务报价只读入口。
   * 仅 phase=authenticated 且当前空间角色严格为 finance 时为 true；
   * disabled / 握手失败 / 未认证 / 其他角色一律 false。
   */
  canAccessFinance: boolean;
  /**
   * 是否可进入人力人员资质素材卡入口。
   * 仅 phase=authenticated 且当前空间角色严格为 hr 时为 true；
   * disabled / 握手失败 / 未认证 / 其他角色一律 false。
   */
  canAccessHr: boolean;
  /**
   * 是否可进入投标人匿名合规预览入口。
   * 仅 phase=authenticated 且当前空间角色严格为 bidder 时为 true；
   * disabled / 握手失败 / 未认证 / owner / bid_writer / finance / hr 一律 false。
   */
  canAccessBidder: boolean;
  errorMessage: string | null;
  /** 活动空间切换是否在途（单飞） */
  workspaceSwitching: boolean;
  /**
   * 刷新握手与会话。
   * 返回对账用的已严格校验脱敏 me（失败或未认证时为 null）。
   * 坏 /me 不得 applyMe 或进入 authenticated。
   */
  refresh: () => Promise<AuthMe | null>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /**
   * 切换当前会话活动工作空间。
   * 仅接受当前 me.workspaces 中精确 id 且非当前值的目标；单飞；
   * 成功后返回服务端已严格校验的目标 AuthWorkspace，调用方按 membership.role 整页导航。
   * @returns 目标 AuthWorkspace=已确认切换成功；null=未发请求/单飞/校验失败（调用方不得导航）
   */
  switchWorkspace: (workspaceId: string) => Promise<AuthWorkspace | null>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const ROLE_LABELS: Record<AuthRole, string> = {
  bid_writer: "标书制作者",
  finance: "财务",
  hr: "人力",
  bidder: "投标人",
};

/** 用途：角色中文标签（展示用）。 */
export function authRoleLabel(role: AuthRole | null | undefined): string {
  if (!role) return "未知角色";
  return ROLE_LABELS[role] ?? role;
}

/**
 * 用途：仅按 activeWorkspaceId 精确命中活动成员。
 * 边界：不得 fallback 第一空间——与服务端活动真值分叉会错赋角色权限。
 */
function pickActiveMembership(me: AuthMe | null): AuthWorkspace | null {
  if (!me) return null;
  const activeId = me.activeWorkspaceId;
  if (!activeId) return null;
  return me.workspaces.find((w) => w.id === activeId) ?? null;
}

function toErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === "object" && "message" in err) {
    const msg = (err as ApiError).message;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
}

function isAuthRole(value: unknown): value is AuthRole {
  return typeof value === "string" && AUTH_ROLES.has(value);
}

/**
 * 用途：通用严格 AuthMe parser（GET /me、对账、login 形状校验）。
 * 校验：user id/username 非空字符串；workspaces 每项 id/name/四角色/isOwner；
 *       workspace id 全局唯一；活动真值不变量：
 *         - workspaces 非空 → activeWorkspaceId 必须为字符串且精确命中唯一空间
 *         - workspaces 为空 → activeWorkspaceId 只能为 null
 * 边界：始终丢弃 csrfToken（非 login 不得覆盖内存 CSRF）。
 * @returns 已校验 AuthMe（csrfToken 恒 undefined）；坏响应返回 null
 */
function parseAuthMeStrict(raw: unknown): AuthMe | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const userRaw = obj.user;
  if (!userRaw || typeof userRaw !== "object") return null;
  const user = userRaw as Record<string, unknown>;
  if (typeof user.id !== "string" || !user.id.trim()) return null;
  if (typeof user.username !== "string" || !user.username.trim()) return null;
  if (!Array.isArray(obj.workspaces)) return null;

  const workspaces: AuthWorkspace[] = [];
  const seenIds = new Set<string>();
  for (const item of obj.workspaces) {
    if (!item || typeof item !== "object") return null;
    const w = item as Record<string, unknown>;
    if (typeof w.id !== "string" || !w.id.trim()) return null;
    if (typeof w.name !== "string") return null;
    if (!isAuthRole(w.role)) return null;
    if (typeof w.isOwner !== "boolean") return null;
    // 重复 id：拒绝整响应，避免 UI 歧义命中
    if (seenIds.has(w.id)) return null;
    seenIds.add(w.id);
    workspaces.push({
      id: w.id,
      name: w.name,
      role: w.role,
      isOwner: w.isOwner,
    });
  }

  // 活动真值不变量：有空间必须精确 active；无空间只能 null；禁止回退首项
  const activeRaw = obj.activeWorkspaceId;
  let activeWorkspaceId: string | null;
  if (workspaces.length === 0) {
    if (activeRaw !== null) return null;
    activeWorkspaceId = null;
  } else if (typeof activeRaw === "string" && activeRaw) {
    if (!seenIds.has(activeRaw)) return null;
    activeWorkspaceId = activeRaw;
  } else {
    // 含 null / 非字符串 / 空串：有空间时一律拒绝
    return null;
  }

  // 非 login：丢弃 csrfToken，禁止任意字符串进入 applyMe
  return {
    user: { id: user.id, username: user.username },
    workspaces,
    activeWorkspaceId,
    csrfToken: undefined,
  };
}

/**
 * 用途：严格校验 PUT active-workspace 响应；在 parseAuthMeStrict 之上额外要求
 * 同一 user + 目标 activeWorkspaceId 精确等于请求目标且目标成员存在。
 * 同样丢弃 csrfToken。
 */
function parseSwitchAuthMe(
  raw: unknown,
  expected: {
    userId: string;
    activeWorkspaceId: string;
  },
): AuthMe | null {
  const base = parseAuthMeStrict(raw);
  if (!base) return null;
  if (base.user.id !== expected.userId) return null;
  if (base.activeWorkspaceId !== expected.activeWorkspaceId) return null;
  const target = base.workspaces.find(
    (w) => w.id === expected.activeWorkspaceId,
  );
  if (!target) return null;
  return base;
}

/**
 * 用途：登录响应用的严格 parser（形状同 me），但不写入 CSRF——
 * CSRF 由 login 显式从 login 响应字段 setCsrfToken，与 GET/me 路径分离。
 */
function parseLoginAuthMe(raw: unknown): {
  me: AuthMe;
  loginCsrf: string | null;
} | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  // 先按严格 me 形状校验（active 可 null）
  const me = parseAuthMeStrict(raw);
  if (!me) return null;
  // 仅 login 允许读取 csrfToken 字段；不经 applyMe
  const loginCsrf =
    typeof obj.csrfToken === "string" && obj.csrfToken.trim()
      ? obj.csrfToken
      : null;
  return { me, loginCsrf };
}

/**
 * 用途：包裹路由树；完成握手与会话恢复。
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<AuthPhase>("loading");
  const [bootstrapped, setBootstrapped] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [me, setMe] = useState<AuthMe | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [workspaceSwitching, setWorkspaceSwitching] = useState(false);

  /** 切换操作代次：隔离迟到 success/catch/finally 与登出/刷新 */
  const switchGenRef = useRef(0);
  /** 在途切换目标；单飞门闩 */
  const switchInFlightRef = useRef<string | null>(null);
  /** 最新 me 快照，供 switchWorkspace 对账（避免闭包陈旧） */
  const meRef = useRef<AuthMe | null>(null);
  meRef.current = me;

  /**
   * 写入会话业务态。
   * 绝不从 me 写入 CSRF——login 与 /auth/csrf 才是 CSRF 入口。
   */
  const applyMe = useCallback((next: AuthMe | null) => {
    const cleaned = next
      ? {
          user: next.user,
          workspaces: next.workspaces,
          activeWorkspaceId: next.activeWorkspaceId,
          csrfToken: undefined,
        }
      : null;
    meRef.current = cleaned;
    setMe(cleaned);
  }, []);

  const refresh = useCallback(async (): Promise<AuthMe | null> => {
    setErrorMessage(null);
    try {
      const bootstrap = await apiFetch<AuthBootstrapStatus>(
        "/auth/bootstrap-status",
      );
      setBootstrapped(Boolean(bootstrap.bootstrapped));
      const required = Boolean(bootstrap.authRequired);
      setAuthRequired(required);

      if (!required) {
        // 个人版兼容：不读 /me，直接业务壳
        clearCsrfToken();
        applyMe(null);
        setPhase("disabled");
        return null;
      }

      try {
        // 先严格校验再 applyMe：坏 /me 不得污染 authenticated UI
        const raw = await apiFetch<unknown>("/auth/me");
        const profile = parseAuthMeStrict(raw);
        if (!profile) {
          clearCsrfToken();
          applyMe(null);
          setErrorMessage("无法恢复登录会话");
          setPhase("unauthenticated");
          return null;
        }
        applyMe(profile);
        // 硬刷新后 /me 不回传 CSRF：仅在本轮内存尚无登录 CSRF 时续发
        if (!getCsrfToken()) {
          try {
            const resumed = await apiFetch<AuthCsrfResume>("/auth/csrf");
            if (!resumed?.csrfToken?.trim()) {
              throw new Error("csrf empty");
            }
            setCsrfToken(resumed.csrfToken);
          } catch (csrfErr) {
            // 续发失败：不得渲染可写业务壳
            clearCsrfToken();
            applyMe(null);
            setErrorMessage(
              toErrorMessage(csrfErr, "无法恢复写操作凭证，请重新登录"),
            );
            setPhase("unauthenticated");
            return null;
          }
        }
        setPhase("authenticated");
        // 返回值必须是已校验值（无 csrf）
        return profile;
      } catch (err) {
        clearCsrfToken();
        applyMe(null);
        const status =
          err && typeof err === "object" && "status" in err
            ? (err as ApiError).status
            : 0;
        // 401/无会话 → 登录页；其他网络错误也落登录页并提示
        if (status !== 401 && status !== 0) {
          setErrorMessage(toErrorMessage(err, "无法恢复登录会话"));
        }
        setPhase("unauthenticated");
        return null;
      }
    } catch (err) {
      // 握手失败必须保守：不得假设 disabled 误开业务壳；
      // 仅成功收到 authRequired=false 才可进入个人版业务壳。
      clearCsrfToken();
      applyMe(null);
      setAuthRequired(false);
      setBootstrapped(false);
      setErrorMessage(toErrorMessage(err, "无法连接认证服务"));
      setPhase("handshake_error");
      return null;
    }
  }, [applyMe]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (username: string, password: string) => {
      setErrorMessage(null);
      try {
        const raw = await apiFetch<unknown>("/auth/login", {
          method: "POST",
          body: JSON.stringify({ username, password }),
        });
        const parsed = parseLoginAuthMe(raw);
        if (!parsed) {
          clearCsrfToken();
          applyMe(null);
          setPhase("unauthenticated");
          throw new Error("登录失败");
        }
        // 仅 login 路径：显式写入 CSRF（与 GET/me、PUT 解析完全分离）
        if (parsed.loginCsrf) {
          setCsrfToken(parsed.loginCsrf);
        }
        applyMe(parsed.me);
        setPhase("authenticated");
      } catch (err) {
        clearCsrfToken();
        applyMe(null);
        setPhase("unauthenticated");
        throw new Error(toErrorMessage(err, "登录失败"));
      }
    },
    [applyMe],
  );

  const logout = useCallback(async () => {
    setErrorMessage(null);
    // 作废在途切换：登出后迟到响应不得解锁/覆盖
    switchGenRef.current += 1;
    switchInFlightRef.current = null;
    setWorkspaceSwitching(false);
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch {
      // 即使服务端失败也清理本地内存会话
    } finally {
      clearCsrfToken();
      applyMe(null);
      setPhase("unauthenticated");
    }
  }, [applyMe]);

  const switchWorkspace = useCallback(
    async (workspaceId: string): Promise<AuthWorkspace | null> => {
      const current = meRef.current;
      // 仅 authenticated 且有 me 时允许
      if (!current) return null;
      // 目标精确值：不得 trim 后把带空白 DOM 值别名成合法 ID
      if (typeof workspaceId !== "string") return null;
      const targetId = workspaceId;
      if (!targetId) return null;
      // 同值 / 不在当前 workspaces（精确匹配）：零请求
      if (targetId === current.activeWorkspaceId) return null;
      const membership = current.workspaces.find((w) => w.id === targetId);
      if (!membership) return null;
      // 单飞：已有在途切换则忽略（调用方不得因此整页导航）
      if (switchInFlightRef.current) return null;

      const gen = ++switchGenRef.current;
      switchInFlightRef.current = targetId;
      setWorkspaceSwitching(true);
      setErrorMessage(null);

      const isStale = () => gen !== switchGenRef.current;

      const finishSuccess = (next: AuthMe): AuthWorkspace | null => {
        if (isStale()) return null;
        applyMe(next);
        setPhase("authenticated");
        switchInFlightRef.current = null;
        setWorkspaceSwitching(false);
        // 返回服务端已校验的目标成员（含可能并发变更后的新角色）
        const target = next.workspaces.find((w) => w.id === targetId);
        return target ?? null;
      };

      const finishFailKeepUi = () => {
        if (isStale()) return;
        switchInFlightRef.current = null;
        setWorkspaceSwitching(false);
      };

      try {
        let putOk: AuthMe | null = null;
        try {
          const raw = await apiFetch<unknown>("/auth/active-workspace", {
            method: "PUT",
            body: JSON.stringify({ workspaceId: targetId }),
          });
          // PUT parser：丢弃 csrfToken；要求同一 user + 目标 active
          putOk = parseSwitchAuthMe(raw, {
            userId: current.user.id,
            activeWorkspaceId: targetId,
          });
          if (!putOk) {
            throw new Error("bad_switch_response");
          }
        } catch {
          if (isStale()) return null;
          // 网络/HTTP/解析/坏响应：refresh 对账（refresh 内先严格校验再 apply）
          let reconciled: AuthMe | null = null;
          try {
            reconciled = await refresh();
          } catch {
            reconciled = null;
          }
          if (isStale()) return null;
          // 对账返回值已是校验值；再套 PUT 目标约束
          if (reconciled && reconciled.activeWorkspaceId === targetId) {
            const ok = parseSwitchAuthMe(reconciled, {
              userId: current.user.id,
              activeWorkspaceId: targetId,
            });
            if (ok) {
              return finishSuccess(ok);
            }
          }
          // 对账失败：refresh 已写入保守态；仍为原空间则固定中文错误
          if (
            reconciled &&
            reconciled.activeWorkspaceId === current.activeWorkspaceId
          ) {
            finishFailKeepUi();
            throw new Error(SWITCH_FAIL_MESSAGE);
          }
          // 会话丢失/握手失败/坏 me：不解锁业务；抛固定错误
          finishFailKeepUi();
          throw new Error(SWITCH_FAIL_MESSAGE);
        }

        if (isStale()) return null;
        return finishSuccess(putOk);
      } catch (err) {
        if (isStale()) return null;
        // 确保门闩清理（finishFail 已处理的路径会再次 no-op）
        if (switchInFlightRef.current === targetId) {
          switchInFlightRef.current = null;
          setWorkspaceSwitching(false);
        }
        if (err instanceof Error && err.message === SWITCH_FAIL_MESSAGE) {
          throw err;
        }
        throw new Error(SWITCH_FAIL_MESSAGE);
      }
    },
    [applyMe, refresh],
  );

  const activeMembership = useMemo(() => pickActiveMembership(me), [me]);
  // 仅 disabled / 已认证且角色允许时开放导航；handshake_error 一律拒绝
  const canAccessBusiness =
    phase === "disabled" ||
    (phase === "authenticated" && activeMembership?.role === "bid_writer");
  const canAccessSettings =
    phase === "disabled" ||
    (phase === "authenticated" && Boolean(activeMembership?.isOwner));
  // P10B：严格 finance；个人兼容（disabled）与所有者/制作者均不隐式放开
  const canAccessFinance =
    phase === "authenticated" && activeMembership?.role === "finance";
  // P10D：严格 hr；disabled 与 owner/bid_writer/finance/bidder 均不开放
  const canAccessHr =
    phase === "authenticated" && activeMembership?.role === "hr";
  // P10E：严格 bidder；disabled 与 owner/bid_writer/finance/hr 均不开放
  const canAccessBidder =
    phase === "authenticated" && activeMembership?.role === "bidder";

  const value = useMemo<AuthContextValue>(
    () => ({
      phase,
      bootstrapped,
      authRequired,
      me,
      activeMembership,
      canAccessBusiness,
      canAccessSettings,
      canAccessFinance,
      canAccessHr,
      canAccessBidder,
      errorMessage,
      workspaceSwitching,
      refresh,
      login,
      logout,
      switchWorkspace,
    }),
    [
      phase,
      bootstrapped,
      authRequired,
      me,
      activeMembership,
      canAccessBusiness,
      canAccessSettings,
      canAccessFinance,
      canAccessHr,
      canAccessBidder,
      errorMessage,
      workspaceSwitching,
      refresh,
      login,
      logout,
      switchWorkspace,
    ],
  );

  return createElement(AuthContext.Provider, { value }, children);
}

/**
 * 用途：读取认证上下文；必须在 AuthProvider 内。
 */
export function useAuthSession(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuthSession 必须在 AuthProvider 内使用");
  }
  return ctx;
}
