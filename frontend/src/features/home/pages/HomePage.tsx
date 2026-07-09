import { Link } from "react-router-dom";
import { ArrowRight, Plus, Radio } from "lucide-react";
import { ProjectStatusBadge } from "../../../shared/components/StatusBadge";
import {
  formatRelativeTime,
  mockProjects,
  mockTasks,
} from "../../../shared/mock/projects";
import "./HomePage.css";

const flowSteps = ["导入解析", "招标分析", "大纲", "全局事实", "正文生成", "导出"];

/**
 * 工作台首页
 * 用途：项目总览、进行中任务、进入技术方案主流程的入口。
 */
export function HomePage() {
  const activeTask = mockTasks[0];
  const writingCount = mockProjects.filter((p) => p.status === "writing").length;
  const totalWords = mockProjects.reduce((s, p) => s + p.wordCount, 0);

  return (
    <div className="page">
      <section className="home-hero">
        <div className="home-hero__eyebrow">
          <Radio size={14} /> Web 自托管 · C 端工作流重构
        </div>
        <h1>把标书写作，收成一条可恢复的流水线</h1>
        <p>
          对齐易标桌面端能力：解析招标文件、生成大纲与正文、知识库复用、查重与废标检查。
          当前为前端原型，数据为本地 mock，后端接口位已预留。
        </p>
        <div className="home-hero__actions">
          <Link to="/technical-plan" className="btn btn-primary btn-lg">
            进入技术方案 <ArrowRight size={16} />
          </Link>
          <Link to="/technical-plan/new" className="btn btn-ghost btn-lg" style={{ color: "#f3ebe0", borderColor: "rgba(243,235,224,0.25)" }}>
            <Plus size={16} /> 新建项目
          </Link>
        </div>
        <div className="flow-strip">
          {flowSteps.map((t, i) => (
            <div className="flow-step" key={t}>
              <div className="flow-step__n">0{i + 1}</div>
              <div className="flow-step__t">{t}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="home-grid stagger">
        <section className="card card-pad">
          <div className="home-section-title">
            <h2>最近项目</h2>
            <Link to="/technical-plan" className="btn btn-ghost btn-sm">
              全部
            </Link>
          </div>
          <div className="project-list">
            {mockProjects.map((p) => (
              <Link key={p.id} to={`/technical-plan/${p.id}`} className="project-item">
                <div>
                  <div className="project-item__name">{p.name}</div>
                  <div className="project-item__meta">
                    <span>{p.industry}</span>
                    <span>步骤 {p.technicalPlanStep}/6</span>
                    <span>{formatRelativeTime(p.updatedAt)}</span>
                  </div>
                </div>
                <div className="project-item__side">
                  <ProjectStatusBadge status={p.status} />
                  <span className="mono" style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {p.wordCount > 0 ? `${(p.wordCount / 10000).toFixed(1)} 万字` : "—"}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </section>

        <aside>
          <div className="stat-cards">
            <div className="stat-card">
              <div className="stat-card__value">{mockProjects.length}</div>
              <div className="stat-card__label">工作空间项目</div>
            </div>
            <div className="stat-card">
              <div className="stat-card__value">{writingCount}</div>
              <div className="stat-card__label">撰写中</div>
            </div>
            <div className="stat-card">
              <div className="stat-card__value">{(totalWords / 10000).toFixed(1)}</div>
              <div className="stat-card__label">累计生成（万字）</div>
            </div>
            <div className="stat-card">
              <div className="stat-card__value">1</div>
              <div className="stat-card__label">后台任务</div>
            </div>
          </div>

          <div className="card task-panel">
            <div className="home-section-title">
              <h2>进行中的任务</h2>
            </div>
            {activeTask ? (
              <div className="task-row">
                <div className="task-row__head">
                  <span>{activeTask.message}</span>
                  <strong className="mono">{activeTask.progress}%</strong>
                </div>
                <div className="progress" aria-hidden>
                  <span style={{ width: `${activeTask.progress}%` }} />
                </div>
                <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
                  对接后端后将通过 SSE/WebSocket 推送真实进度；支持暂停与断点续跑。
                </p>
              </div>
            ) : (
              <div className="empty-state">暂无运行中任务</div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
