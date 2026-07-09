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

/**
 * 模块：技术方案项目列表（我的项目）
 * 用途：异步加载项目；展示数据来源（API / 本地）与离线提示，便于联调。
 * 对接：listProjectsAsync → GET /api/projects
 */
export function TechnicalPlanListPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<"api" | "local">("local");
  const [offlineHint, setOfflineHint] = useState<string | undefined>();

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listProjectsAsync();
      setProjects(res.projects);
      setSource(res.source);
      setOfflineHint(res.offlineHint);
    } catch {
      setError("加载项目列表失败");
      setProjects([]);
      setSource("local");
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

      {!loading && (
        <div
          className={`tp-source-banner${source === "api" ? " is-api" : " is-local"}`}
          role="status"
        >
          数据来源：
          <strong>{source === "api" ? "后端 API" : "本地/演示兜底"}</strong>
          {source === "api"
            ? " · 刷新后仍保留服务端项目"
            : " · 请启动后端并检查 /api/health"}
          {offlineHint ? ` · ${offlineHint}` : null}
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
