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

export function Sidebar() {
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="sidebar__brand">
        <div className="sidebar__mark" aria-hidden>
          标
        </div>
        <div className="sidebar__titles">
          <div className="sidebar__name">标书工坊</div>
          <div className="sidebar__tag">Bid Studio</div>
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
          本地优先 · 自备 Key
        </div>
        <div className="sidebar__foot-desc">
          一账号一工作空间。重解析可走本地 MinerU 插件，服务器保持轻量。
        </div>
        <NavLink to="/local-parser" className="btn btn-soft btn-sm" style={{ width: "100%" }}>
          配置本地解析
        </NavLink>
      </div>
    </aside>
  );
}
