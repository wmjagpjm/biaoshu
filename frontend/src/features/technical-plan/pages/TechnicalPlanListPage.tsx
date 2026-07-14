/**
 * 模块：技术方案项目列表（我的项目）
 * 用途：只展示服务端 GET /api/projects?kind=technical 真值；真实空态保持空；失败固定中文且不混入 mock/localStorage。
 * 对接：listProjectsAsync → GET /api/projects?kind=technical
 * 二次开发：禁止回退 biaoshu.projects.v1 或演示项目；错误不得回显 detail/code/路径/ID。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FolderKanban, Plus, ArrowRight } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { ProjectStatusBadge } from "../../../shared/components/StatusBadge";
import { formatRelativeTime } from "../../../shared/mock/projects";
import type { Project } from "../../../shared/types/workspace";
import { listProjectsAsync } from "../lib/projectStore";
import "./TechnicalPlan.css";

const LIST_ERROR = "项目列表加载失败，请稍后重试";

export function TechnicalPlanListPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listProjectsAsync({ kind: "technical" });
      setProjects(res.projects);
    } catch {
      setError(LIST_ERROR);
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>我的项目</h1>
          <p>
            技术方案工作流：文档解析 → 招标分析 → 大纲 → 全局事实 → 正文 → 导出。
            也可从「创建方案」页上传招标文件后进入。
          </p>
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void reload()}
            title="刷新列表"
          >
            刷新
          </button>
          <Link to="/create" className="btn btn-ghost">
            去创建
          </Link>
          <Link to="/technical-plan/new" className="btn btn-primary">
            <Plus size={16} /> 新建项目
          </Link>
        </div>
      </header>

      {!loading && !error && (
        <div className="tp-source-banner is-api" role="status">
          数据来源：
          <strong>后端 API</strong>
          {" · 刷新后仍保留服务端项目"}
        </div>
      )}

      {loading ? (
        <LoadingBlock label="加载项目列表…" />
      ) : error ? (
        <EmptyState
          icon={<FolderKanban size={28} />}
          title="加载失败"
          description={error}
          action={
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void reload()}
            >
              重试
            </button>
          }
        />
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<FolderKanban size={28} />}
          title="暂无项目"
          description="从创建页或新建项目开始第一份技术标。"
          action={
            <Link to="/technical-plan/new" className="btn btn-primary btn-sm">
              新建项目
            </Link>
          }
        />
      ) : (
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
              {projects.map((p) => (
                <tr key={p.id}>
                  <td>
                    <span className="project-name">{p.name}</span>
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
                      <ArrowRight size={14} /> 进入
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
