import { Link } from "react-router-dom";
import { Plus, Sparkles } from "lucide-react";
import { ProjectStatusBadge } from "../../../shared/components/StatusBadge";
import { formatRelativeTime, mockProjects } from "../../../shared/mock/projects";
import "./TechnicalPlan.css";

/**
 * 技术方案项目列表
 * 用途：管理工作空间内的技术标项目，进入六步工作流。
 */
export function TechnicalPlanListPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>技术方案</h1>
          <p>
            对齐 C 端主流程：文档解析 → 招标分析 → 大纲 → 全局事实 → 正文 → 导出。
            每一步可回看、可编辑，长任务支持进度展示。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/technical-plan/new" className="btn btn-primary">
            <Plus size={16} /> 新建项目
          </Link>
        </div>
      </header>

      <div className="card" style={{ overflow: "hidden" }}>
        <table className="project-table">
          <thead>
            <tr>
              <th>项目名称</th>
              <th>行业</th>
              <th>状态</th>
              <th>进度</th>
              <th>字数</th>
              <th>更新</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {mockProjects.map((p) => (
              <tr key={p.id}>
                <td>
                  <strong>{p.name}</strong>
                </td>
                <td>{p.industry}</td>
                <td>
                  <ProjectStatusBadge status={p.status} />
                </td>
                <td className="mono">步骤 {p.technicalPlanStep}/6</td>
                <td className="mono">
                  {p.wordCount > 0 ? p.wordCount.toLocaleString() : "—"}
                </td>
                <td>{formatRelativeTime(p.updatedAt)}</td>
                <td>
                  <Link
                    to={`/technical-plan/${p.id}`}
                    className="btn btn-ghost btn-sm"
                  >
                    <Sparkles size={14} /> 进入
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
