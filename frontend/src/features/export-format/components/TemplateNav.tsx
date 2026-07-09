import { NavLink } from "react-router-dom";

/**
 * 导出模板子导航
 * 用途：模板设置 / 我的模板 / 新建模板（对齐 C 端 export-format 信息架构）
 */
export function TemplateNav() {
  return (
    <nav className="ef-tabs" aria-label="模板设置导航">
      <NavLink
        to="/export-format"
        end
        className={({ isActive }) => `ef-tab${isActive ? " is-active" : ""}`}
      >
        模板设置
      </NavLink>
      <NavLink
        to="/export-format/my-templates"
        className={({ isActive }) => `ef-tab${isActive ? " is-active" : ""}`}
      >
        我的模板
      </NavLink>
      <NavLink
        to="/export-format/new"
        className={({ isActive }) => `ef-tab${isActive ? " is-active" : ""}`}
      >
        新建模板
      </NavLink>
    </nav>
  );
}
