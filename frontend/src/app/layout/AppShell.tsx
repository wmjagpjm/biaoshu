import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BookOpen,
  Briefcase,
  Calculator,
  FileSearch,
  FileStack,
  FileText,
  FileType,
  FileWarning,
  FolderKanban,
  IdCard,
  Library,
  Menu,
  Newspaper,
  Plug,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { useSiteBackground } from "../../shared/hooks/useSiteBackground";
import {
  checkApiHealth,
  getApiBase,
  type ApiHealthStatus,
} from "../../shared/lib/api";
import {
  authRoleLabel,
  useAuthSession,
} from "../../features/auth/hooks/useAuthSession";
import "./AppShell.css";

/**
 * 模块：应用壳（易标式左侧栏 + 主内容区）
 * 用途：固定侧栏导航，主区浅色渐变 + 大圆角内容；展示用户/角色/空间与退出。
 * 对接：checkApiHealth → GET /api/health；useAuthSession
 * 二次开发：导航隐藏不替代后端鉴权；禁止展示 Cookie/CSRF/API Key；
 *           财务入口仅严格 finance 可见；人力入口（人员资质/团队推荐）仅严格 hr 可见；
 *           投标人入口仅严格 bidder 可见；不得改动遗留 Sidebar 组件路径。
 */

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  matchPrefix?: string;
  /** 业务导航（仅 bid_writer / disabled） */
  business?: boolean;
  /** 仅所有者可见 */
  ownerOnly?: boolean;
  /** 仅严格 finance 可见（P10B 财务报价） */
  financeOnly?: boolean;
  /** 仅严格 hr 可见（P10D 人员资质） */
  hrOnly?: boolean;
  /** 仅严格 bidder 可见（P10E 合规预览） */
  bidderOnly?: boolean;
};

const mainNav: NavItem[] = [
  {
    to: "/create",
    label: "标书生成",
    icon: <Sparkles size={18} />,
    matchPrefix: "/create",
    business: true,
  },
  {
    to: "/projects",
    label: "我的项目",
    icon: <FolderKanban size={18} />,
    matchPrefix: "/technical-plan",
    business: true,
  },
  {
    to: "/knowledge-base",
    label: "知识库",
    icon: <BookOpen size={18} />,
    business: true,
  },
  {
    to: "/resources",
    label: "资源中心",
    icon: <Library size={18} />,
    business: true,
  },
  {
    to: "/bid-templates",
    label: "中标模板",
    icon: <FileStack size={18} />,
    matchPrefix: "/bid-templates",
    business: true,
  },
  {
    to: "/duplicate-check",
    label: "标书查重",
    icon: <FileSearch size={18} />,
    business: true,
  },
  {
    to: "/rejection-check",
    label: "废标检查",
    icon: <FileWarning size={18} />,
    business: true,
  },
  {
    to: "/business-bid",
    label: "商务标",
    icon: <Briefcase size={18} />,
    matchPrefix: "/business-bid",
    business: true,
  },
  {
    to: "/bid-opportunity",
    label: "标讯",
    icon: <Newspaper size={18} />,
    business: true,
  },
];

const systemNav: NavItem[] = [
  {
    to: "/export-format",
    label: "导出模板",
    icon: <FileType size={18} />,
    matchPrefix: "/export-format",
    business: true,
  },
  {
    to: "/local-parser",
    label: "本地解析",
    icon: <Plug size={18} />,
    business: true,
  },
  {
    to: "/settings",
    label: "设置",
    icon: <Settings size={18} />,
    ownerOnly: true,
  },
];

/** P10B：财务只读入口，独立于业务/系统导航，避免与制作者权限耦合 */
const financeNav: NavItem[] = [
  {
    to: "/finance",
    label: "财务报价",
    icon: <Calculator size={18} />,
    matchPrefix: "/finance",
    financeOnly: true,
  },
];

/** P10D/P10F：人力入口，独立「人力」分组；仅严格 hr 可见 */
const hrNav: NavItem[] = [
  {
    to: "/hr",
    label: "人员资质",
    icon: <IdCard size={18} />,
    hrOnly: true,
  },
  {
    to: "/hr/team-recommendations",
    label: "团队推荐",
    icon: <Users size={18} />,
    matchPrefix: "/hr/team-recommendations",
    hrOnly: true,
  },
];

/** P10E：投标人匿名合规预览入口，独立「投标人」分组；仅严格 bidder 可见 */
const bidderNav: NavItem[] = [
  {
    to: "/bidder",
    label: "合规预览",
    icon: <ShieldCheck size={18} />,
    matchPrefix: "/bidder",
    bidderOnly: true,
  },
];

function isNavActive(pathname: string, item: NavItem): boolean {
  if (item.to === "/create") {
    return pathname === "/" || pathname.startsWith("/create");
  }
  if (item.to === "/projects") {
    return (
      pathname.startsWith("/projects") || pathname.startsWith("/technical-plan")
    );
  }
  // 人员资质仅精确匹配 /hr，避免与 /hr/team-recommendations 激活态冲突
  if (item.to === "/hr") {
    return pathname === "/hr";
  }
  if (item.matchPrefix) {
    return pathname === item.to || pathname.startsWith(item.matchPrefix);
  }
  return pathname === item.to || pathname.startsWith(item.to + "/");
}

function SideLink({
  item,
  onNavigate,
}: {
  item: NavItem;
  onNavigate?: () => void;
}) {
  const { pathname } = useLocation();
  const active = isNavActive(pathname, item);
  return (
    <NavLink
      to={item.to}
      className={`side-nav__item${active ? " is-active" : ""}`}
      onClick={onNavigate}
    >
      <span className="side-nav__icon">{item.icon}</span>
      <span>{item.label}</span>
    </NavLink>
  );
}

export function AppShell() {
  const { pathname } = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { hasImage } = useSiteBackground();
  const [apiStatus, setApiStatus] = useState<ApiHealthStatus>("unknown");
  const [apiTitle, setApiTitle] = useState(getApiBase());
  const [loggingOut, setLoggingOut] = useState(false);
  const {
    phase,
    me,
    activeMembership,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
    canAccessHr,
    canAccessBidder,
    logout,
  } = useAuthSession();

  const isCreate = pathname === "/" || pathname.startsWith("/create");
  const isWorkspace =
    (pathname.startsWith("/technical-plan/") &&
      pathname.split("/").length >= 3) ||
    (pathname.startsWith("/business-bid/") && pathname.split("/").length >= 3);

  const visibleMain = mainNav.filter((item) => {
    if (item.business && !canAccessBusiness) return false;
    if (item.ownerOnly && !canAccessSettings) return false;
    if (item.financeOnly && !canAccessFinance) return false;
    if (item.hrOnly && !canAccessHr) return false;
    if (item.bidderOnly && !canAccessBidder) return false;
    return true;
  });
  const visibleSystem = systemNav.filter((item) => {
    if (item.business && !canAccessBusiness) return false;
    if (item.ownerOnly && !canAccessSettings) return false;
    if (item.financeOnly && !canAccessFinance) return false;
    if (item.hrOnly && !canAccessHr) return false;
    if (item.bidderOnly && !canAccessBidder) return false;
    return true;
  });
  const visibleFinance = financeNav.filter(
    (item) => !item.financeOnly || canAccessFinance,
  );
  const visibleHr = hrNav.filter((item) => !item.hrOnly || canAccessHr);
  const visibleBidder = bidderNav.filter(
    (item) => !item.bidderOnly || canAccessBidder,
  );

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const h = await checkApiHealth(true);
      if (cancelled) return;
      setApiStatus(h.status);
      setApiTitle(
        h.status === "online"
          ? `API 在线 · ${h.service ?? "biaoshu"} · ${getApiBase()}${h.workspaceId ? ` · ${h.workspaceId}` : ""}`
          : `API 离线 · ${getApiBase()} · 请启动 uvicorn :8000`,
      );
    };
    void tick();
    const id = window.setInterval(() => void tick(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const statusLabel =
    apiStatus === "online"
      ? "API 在线"
      : apiStatus === "offline"
        ? "API 离线"
        : "API 检测中";

  const username =
    phase === "disabled" ? "本机用户" : (me?.user.username ?? "未登录");
  const roleLabel =
    phase === "disabled"
      ? "个人版"
      : authRoleLabel(activeMembership?.role);
  const workspaceLabel =
    phase === "disabled"
      ? "ws_local"
      : (activeMembership?.name ??
        activeMembership?.id ??
        me?.activeWorkspaceId ??
        "未选择空间");

  async function onLogout() {
    if (phase !== "authenticated" || loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className="app-shell" data-testid="app-shell">
      <aside className={`app-sidebar${mobileOpen ? " is-open" : ""}`}>
        <div className="app-sidebar__brand">
          <span className="app-sidebar__logo">
            <FileText size={18} />
          </span>
          <div>
            <div className="app-sidebar__name">易标工坊</div>
            <div className="app-sidebar__tag">投标工具箱</div>
          </div>
        </div>

        <nav className="side-nav" aria-label="主导航" data-testid="side-nav">
          {visibleMain.length > 0 && (
            <>
              <div className="side-nav__section">主流程</div>
              {visibleMain.map((item) => (
                <SideLink
                  key={item.to}
                  item={item}
                  onNavigate={() => setMobileOpen(false)}
                />
              ))}
            </>
          )}
          {visibleSystem.length > 0 && (
            <>
              <div className="side-nav__section">系统</div>
              {visibleSystem.map((item) => (
                <SideLink
                  key={item.to}
                  item={item}
                  onNavigate={() => setMobileOpen(false)}
                />
              ))}
            </>
          )}
          {visibleFinance.length > 0 && (
            <>
              <div className="side-nav__section">财务</div>
              {visibleFinance.map((item) => (
                <SideLink
                  key={item.to}
                  item={item}
                  onNavigate={() => setMobileOpen(false)}
                />
              ))}
            </>
          )}
          {visibleHr.length > 0 && (
            <>
              <div className="side-nav__section">人力</div>
              {visibleHr.map((item) => (
                <SideLink
                  key={item.to}
                  item={item}
                  onNavigate={() => setMobileOpen(false)}
                />
              ))}
            </>
          )}
          {visibleBidder.length > 0 && (
            <>
              <div className="side-nav__section">投标人</div>
              {visibleBidder.map((item) => (
                <SideLink
                  key={item.to}
                  item={item}
                  onNavigate={() => setMobileOpen(false)}
                />
              ))}
            </>
          )}
          {!canAccessBusiness &&
            !canAccessFinance &&
            !canAccessHr &&
            !canAccessBidder &&
            phase === "authenticated" && (
              <div className="side-nav__section">说明</div>
            )}
          {!canAccessBusiness &&
            !canAccessFinance &&
            !canAccessHr &&
            !canAccessBidder &&
            phase === "authenticated" && (
              <NavLink
                to="/restricted"
                className="side-nav__item"
                onClick={() => setMobileOpen(false)}
              >
                <span>权限说明</span>
              </NavLink>
            )}
        </nav>

        <div className="app-sidebar__foot">
          <div
            className="app-sidebar__user"
            data-testid="shell-user"
            title={`${username} · ${roleLabel} · ${workspaceLabel}`}
          >
            <div>{username}</div>
            <div className="auth-shell__meta">
              {roleLabel}
              {activeMembership?.isOwner ? " · 所有者" : ""}
            </div>
            <div className="auth-shell__meta">{workspaceLabel}</div>
          </div>
          {phase === "authenticated" && (
            <button
              type="button"
              className="btn btn-ghost btn-sm auth-shell__logout"
              data-testid="logout-button"
              onClick={() => void onLogout()}
              disabled={loggingOut}
            >
              {loggingOut ? "退出中…" : "退出登录"}
            </button>
          )}
          <div
            className={`api-status-chip is-${apiStatus}`}
            title={apiTitle}
            role="status"
          >
            <span className="api-status-chip__dot" aria-hidden />
            <span>{statusLabel}</span>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <button
          type="button"
          className="app-sidebar-mask"
          aria-label="关闭菜单"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <div className={`app-body${hasImage ? " has-custom-bg" : ""}`}>
        <header className="app-topbar">
          <button
            type="button"
            className="app-topbar__menu"
            onClick={() => setMobileOpen((v) => !v)}
            aria-label="菜单"
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="auth-shell__topbar-user" data-testid="topbar-user">
            <span>{username}</span>
            <span aria-hidden>·</span>
            <span>{roleLabel}</span>
            <span aria-hidden>·</span>
            <span>{workspaceLabel}</span>
          </div>
          <div className="app-topbar__spacer" />
          {phase === "authenticated" && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              data-testid="topbar-logout"
              onClick={() => void onLogout()}
              disabled={loggingOut}
            >
              {loggingOut ? "退出中…" : "退出"}
            </button>
          )}
        </header>

        <main
          className={`app-main${isCreate ? " app-main--create" : ""}${
            isWorkspace ? " app-main--wide" : ""
          }`}
        >
          {isCreate ? (
            <Outlet />
          ) : (
            <div
              className={`content-wrap${isWorkspace ? " content-wrap--wide" : ""}`}
            >
              <Outlet />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
