import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import {
  BookOpen,
  Briefcase,
  Calculator,
  FileSearch,
  FileWarning,
  FolderKanban,
  Home,
  Newspaper,
  Plug,
  Settings,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  authRoleLabel,
  useAuthSession,
} from "../../features/auth/hooks/useAuthSession";

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
  business?: boolean;
  ownerOnly?: boolean;
  /** 仅严格 finance 可见（与 AppShell 同源） */
  financeOnly?: boolean;
};

const primaryNav: NavItem[] = [
  { to: "/", label: "工作台", icon: <Home size={18} />, end: true, business: true },
  {
    to: "/technical-plan",
    label: "技术方案",
    icon: <Sparkles size={18} />,
    business: true,
  },
  {
    to: "/knowledge-base",
    label: "知识库",
    icon: <BookOpen size={18} />,
    business: true,
  },
];

const qualityNav: NavItem[] = [
  {
    to: "/duplicate-check",
    label: "标书查重",
    icon: <FileSearch size={18} />,
    business: true,
  },
  {
    to: "/rejection-check",
    label: "废标项检查",
    icon: <FileWarning size={18} />,
    business: true,
  },
  {
    to: "/export-format",
    label: "导出格式",
    icon: <FolderKanban size={18} />,
    business: true,
  },
];

const moreNav: NavItem[] = [
  {
    to: "/business-bid",
    label: "商务标",
    icon: <Briefcase size={18} />,
    business: true,
  },
  {
    to: "/bid-opportunity",
    label: "标讯",
    icon: <Newspaper size={18} />,
    business: true,
  },
  {
    to: "/local-parser",
    label: "本地解析插件",
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

/** P10B：财务只读入口，与 AppShell 可见性一致 */
const financeNav: NavItem[] = [
  {
    to: "/finance",
    label: "财务报价",
    icon: <Calculator size={18} />,
    financeOnly: true,
  },
];

function NavGroup({ title, items }: { title: string; items: NavItem[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <div className="sidebar__section">{title}</div>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => `nav-item${isActive ? " is-active" : ""}`}
        >
          {item.icon}
          <span>{item.label}</span>
        </NavLink>
      ))}
    </>
  );
}

function filterNav(
  items: NavItem[],
  canAccessBusiness: boolean,
  canAccessSettings: boolean,
  canAccessFinance: boolean,
): NavItem[] {
  return items.filter((item) => {
    if (item.business && !canAccessBusiness) return false;
    if (item.ownerOnly && !canAccessSettings) return false;
    if (item.financeOnly && !canAccessFinance) return false;
    return true;
  });
}

/**
 * 侧栏导航（遗留/可选）
 * 用途：曾规划左侧栏布局；当前壳层以 AppShell 顶栏为准。
 * 注意：勿与 AppShell 双开导致入口漂移；若启用侧栏，请与顶栏配置同源。
 * 二次开发：角色可见性与 AppShell 保持一致；不替代服务端鉴权。
 */
export function Sidebar() {
  const {
    phase,
    me,
    activeMembership,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
  } = useAuthSession();

  const primary = filterNav(
    primaryNav,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
  );
  const quality = filterNav(
    qualityNav,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
  );
  const more = filterNav(
    moreNav,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
  );
  const finance = filterNav(
    financeNav,
    canAccessBusiness,
    canAccessSettings,
    canAccessFinance,
  );

  const username =
    phase === "disabled" ? "本机用户" : (me?.user.username ?? "未登录");
  const roleLabel =
    phase === "disabled" ? "个人版" : authRoleLabel(activeMembership?.role);
  const workspaceLabel =
    phase === "disabled"
      ? "本机工作空间"
      : (activeMembership?.name ?? activeMembership?.id ?? "未选择空间");

  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="sidebar__brand">
        <div className="sidebar__mark" aria-hidden>
          标
        </div>
        <div className="sidebar__titles">
          <div className="sidebar__name">标书</div>
          <div className="sidebar__tag">投标工作台</div>
        </div>
      </div>

      <nav className="sidebar__nav">
        <NavGroup title="主流程" items={primary} />
        <NavGroup title="质检与交付" items={quality} />
        <NavGroup title="扩展" items={more} />
        <NavGroup title="财务" items={finance} />
      </nav>

      <div className="sidebar__foot">
        <div className="sidebar__foot-title">
          <ShieldCheck size={14} style={{ display: "inline", marginRight: 6 }} />
          {username} · {roleLabel}
        </div>
        <div className="sidebar__foot-desc">
          {workspaceLabel}
          {activeMembership?.isOwner ? "（所有者）" : ""}。复杂版式可使用本地解析插件。
        </div>
        {canAccessBusiness && (
          <NavLink
            to="/local-parser"
            className="btn btn-soft btn-sm"
            style={{ width: "100%" }}
          >
            配置本地解析
          </NavLink>
        )}
      </div>
    </aside>
  );
}
