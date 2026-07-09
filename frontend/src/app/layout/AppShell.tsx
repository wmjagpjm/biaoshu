import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import "./AppShell.css";

/**
 * 应用壳
 * 用途：统一侧栏导航 + 顶栏工作空间信息 + 内容出口。
 * 所有业务页面通过 React Router 的 <Outlet /> 渲染。
 */
export function AppShell() {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-area">
        <TopBar />
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
