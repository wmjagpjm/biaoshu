/**
 * 模块：P10A 前端会话与认证上下文
 * 用途：
 *   1. 先读 bootstrap-status 区分 disabled / required
 *   2. required 下恢复 /auth/me 会话或展示登录页
 *   3. 登录仅把脱敏 me 与 CSRF 放在 React 内存
 * 对接：apiFetch CSRF 内存；router 业务壳门禁；AppShell 用户条
 * 二次开发：禁止 localStorage/sessionStorage 写口令/Cookie/CSRF/Token。
 */

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
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
  errorMessage: string | null;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
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

function pickActiveMembership(me: AuthMe | null): AuthWorkspace | null {
  if (!me) return null;
  const activeId = me.activeWorkspaceId;
  if (activeId) {
    const hit = me.workspaces.find((w) => w.id === activeId);
    if (hit) return hit;
  }
  return me.workspaces[0] ?? null;
}

function toErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === "object" && "message" in err) {
    const msg = (err as ApiError).message;
    if (typeof msg === "string" && msg.trim()) return msg;
  }
  return fallback;
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

  const applyMe = useCallback((next: AuthMe | null) => {
    if (next?.csrfToken) {
      setCsrfToken(next.csrfToken);
    }
    // me 响应中 csrfToken 可能为 null；勿用 null 清掉已有内存 CSRF
    setMe(
      next
        ? {
            user: next.user,
            workspaces: next.workspaces,
            activeWorkspaceId: next.activeWorkspaceId,
            csrfToken: undefined,
          }
        : null,
    );
  }, []);

  const refresh = useCallback(async () => {
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
        setMe(null);
        setPhase("disabled");
        return;
      }

      try {
        const profile = await apiFetch<AuthMe>("/auth/me");
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
            setMe(null);
            setErrorMessage(
              toErrorMessage(csrfErr, "无法恢复写操作凭证，请重新登录"),
            );
            setPhase("unauthenticated");
            return;
          }
        }
        setPhase("authenticated");
      } catch (err) {
        clearCsrfToken();
        setMe(null);
        const status =
          err && typeof err === "object" && "status" in err
            ? (err as ApiError).status
            : 0;
        // 401/无会话 → 登录页；其他网络错误也落登录页并提示
        if (status !== 401 && status !== 0) {
          setErrorMessage(toErrorMessage(err, "无法恢复登录会话"));
        }
        setPhase("unauthenticated");
      }
    } catch (err) {
      // 握手失败必须保守：不得假设 disabled 误开业务壳；
      // 仅成功收到 authRequired=false 才可进入个人版业务壳。
      clearCsrfToken();
      setMe(null);
      setAuthRequired(false);
      setBootstrapped(false);
      setErrorMessage(toErrorMessage(err, "无法连接认证服务"));
      setPhase("handshake_error");
    }
  }, [applyMe]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (username: string, password: string) => {
      setErrorMessage(null);
      try {
        const profile = await apiFetch<AuthMe>("/auth/login", {
          method: "POST",
          body: JSON.stringify({ username, password }),
        });
        if (profile.csrfToken) {
          setCsrfToken(profile.csrfToken);
        }
        applyMe(profile);
        setPhase("authenticated");
      } catch (err) {
        clearCsrfToken();
        setMe(null);
        setPhase("unauthenticated");
        throw new Error(toErrorMessage(err, "登录失败"));
      }
    },
    [applyMe],
  );

  const logout = useCallback(async () => {
    setErrorMessage(null);
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch {
      // 即使服务端失败也清理本地内存会话
    } finally {
      clearCsrfToken();
      setMe(null);
      setPhase("unauthenticated");
    }
  }, []);

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
      errorMessage,
      refresh,
      login,
      logout,
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
      errorMessage,
      refresh,
      login,
      logout,
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
