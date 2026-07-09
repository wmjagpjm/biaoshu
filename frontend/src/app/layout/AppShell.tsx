import { NavLink, Outlet, useLocation } from "react-router-dom";
import { FolderKanban, Sparkles } from "lucide-react";
import "./AppShell.css";

/**
 * 应用壳
 * 用途：喜鹊 /create 风格顶栏 + 全高内容出口。
 * 创建页占满主区；其它业务页使用可滚动内容容器。
 */
export function AppShell() {
  const { pathname } = useLocation();
  const isCreate = pathname === "/" || pathname.startsWith("/create");

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__left">
          <NavLink to="/create" className="brand">
            <span className="brand__logo">标</span>
            <span>
              <div className="brand__text">标书工坊</div>
              <div className="brand__sub">AI 投标方案助手</div>
            </span>
          </NavLink>
          <nav className="header-nav" aria-label="主导航">
            <NavLink
              to="/create"
              className={({ isActive }) => (isActive || isCreate ? "is-active" : "")}
            >
              <Sparkles size={14} style={{ marginRight: 6 }} />
              创建方案
            </NavLink>
            <NavLink
              to="/projects"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              <FolderKanban size={14} style={{ marginRight: 6 }} />
              我的项目
            </NavLink>
            <NavLink
              to="/knowledge-base"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              知识库
            </NavLink>
            <NavLink
              to="/settings"
              className={({ isActive }) => (isActive ? "is-active" : "")}
            >
              设置
            </NavLink>
          </nav>
        </div>
        <div className="app-header__right">
          <div className="user-pill">
            演示用户
            <span className="user-pill__avatar">演</span>
          </div>
        </div>
      </header>

      <main className={`app-main${isCreate ? "" : " app-main--scroll"}`}>
        {isCreate ? (
          <Outlet />
        ) : (
          <div className="content-wrap">
            <Outlet />
          </div>
        )}
      </main>
    </div>
  );
}
