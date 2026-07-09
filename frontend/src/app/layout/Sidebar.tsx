import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import {
  BookOpen,
  Briefcase,
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

type NavItem = {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
};

const primaryNav: NavItem[] = [
  { to: "/", label: "工作台", icon: <Home size={18} />, end: true },
  { to: "/technical-plan", label: "技术方案", icon: <Sparkles size={18} /> },
  { to: "/knowledge-base", label: "知识库", icon: <BookOpen size={18} /> },
];

const qualityNav: NavItem[] = [
  { to: "/duplicate-check", label: "标书查重", icon: <FileSearch size={18} /> },
  { to: "/rejection-check", label: "废标项检查", icon: <FileWarning size={18} /> },
  { to: "/export-format", label: "导出格式", icon: <FolderKanban size={18} /> },
];

const moreNav: NavItem[] = [
  { to: "/business-bid", label: "商务标", icon: <Briefcase size={18} /> },
  { to: "/bid-opportunity", label: "标讯", icon: <Newspaper size={18} /> },
  { to: "/local-parser", label: "本地解析插件", icon: <Plug size={18} /> },
  { to: "/settings", label: "设置", icon: <Settings size={18} /> },
];

function NavGroup({ title, items }: { title: string; items: NavItem[] }) {
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

/**
 * 侧栏导航（遗留/可选）
 * 用途：曾规划左侧栏布局；当前壳层以 AppShell 顶栏为准。
 * 注意：勿与 AppShell 双开导致入口漂移；若启用侧栏，请与顶栏配置同源。
 */
export function Sidebar() {
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
        <NavGroup title="主流程" items={primaryNav} />
        <NavGroup title="质检与交付" items={qualityNav} />
        <NavGroup title="扩展" items={moreNav} />
      </nav>

      <div className="sidebar__foot">
        <div className="sidebar__foot-title">
          <ShieldCheck size={14} style={{ display: "inline", marginRight: 6 }} />
          本机工作空间
        </div>
        <div className="sidebar__foot-desc">
          一账号一工作空间。复杂版式可使用本地解析插件。
        </div>
        <NavLink to="/local-parser" className="btn btn-soft btn-sm" style={{ width: "100%" }}>
          配置本地解析
        </NavLink>
      </div>
    </aside>
  );
}
