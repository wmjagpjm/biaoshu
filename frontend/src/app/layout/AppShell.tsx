import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BookOpen,
  Briefcase,
  FileSearch,
  FileStack,
  FileText,
  FileType,
  FileWarning,
  FolderKanban,
  Library,
  Menu,
  Newspaper,
  Plug,
  Settings,
  Sparkles,
  X,
} from "lucide-react";
import { useSiteBackground } from "../../shared/hooks/useSiteBackground";
import {
  checkApiHealth,
  getApiBase,
  type ApiHealthStatus,
} from "../../shared/lib/api";
import "./AppShell.css";

/**
 * 模块：应用壳（易标式左侧栏 + 主内容区）
 * 用途：固定侧栏导航，主区浅色渐变 + 大圆角内容；侧栏展示 API 联通状态。
 * 对接：checkApiHealth → GET /api/health
 */

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  matchPrefix?: string;
};

const mainNav: NavItem[] = [
  {
    to: "/create",
    label: "标书生成",
    icon: <Sparkles size={18} />,
    matchPrefix: "/create",
  },
  {
    to: "/projects",
    label: "我的项目",
    icon: <FolderKanban size={18} />,
    matchPrefix: "/technical-plan",
  },
  { to: "/knowledge-base", label: "知识库", icon: <BookOpen size={18} /> },
  { to: "/resources", label: "资源中心", icon: <Library size={18} /> },
  {
    to: "/bid-templates",
    label: "中标模板",
    icon: <FileStack size={18} />,
    matchPrefix: "/bid-templates",
  },
  { to: "/duplicate-check", label: "标书查重", icon: <FileSearch size={18} /> },
  {
    to: "/rejection-check",
    label: "废标检查",
    icon: <FileWarning size={18} />,
  },
  {
    to: "/business-bid",
    label: "商务标",
    icon: <Briefcase size={18} />,
    matchPrefix: "/business-bid",
  },
  { to: "/bid-opportunity", label: "标讯", icon: <Newspaper size={18} /> },
];

const systemNav: NavItem[] = [
  {
    to: "/export-format",
    label: "导出模板",
    icon: <FileType size={18} />,
    matchPrefix: "/export-format",
  },
  { to: "/local-parser", label: "本地解析", icon: <Plug size={18} /> },
  { to: "/settings", label: "设置", icon: <Settings size={18} /> },
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
  const isCreate = pathname === "/" || pathname.startsWith("/create");
  const isWorkspace =
    (pathname.startsWith("/technical-plan/") &&
      pathname.split("/").length >= 3) ||
    (pathname.startsWith("/business-bid/") && pathname.split("/").length >= 3);

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

  return (
    <div className="app-shell">
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

        <nav className="side-nav" aria-label="主导航">
          <div className="side-nav__section">主流程</div>
          {mainNav.map((item) => (
            <SideLink key={item.to} item={item} onNavigate={() => setMobileOpen(false)} />
          ))}
          <div className="side-nav__section">系统</div>
          {systemNav.map((item) => (
            <SideLink key={item.to} item={item} onNavigate={() => setMobileOpen(false)} />
          ))}
        </nav>

        <div className="app-sidebar__foot">
          <div className="app-sidebar__user">本机用户 · ws_local</div>
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
          <div className="app-topbar__spacer" />
        </header>

        <main
          className={`app-main${isCreate ? " app-main--create" : ""}${
            isWorkspace ? " app-main--wide" : ""
          }`}
        >
          {isCreate ? (
            <Outlet />
          ) : (
            <div className={`content-wrap${isWorkspace ? " content-wrap--wide" : ""}`}>
              <Outlet />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
