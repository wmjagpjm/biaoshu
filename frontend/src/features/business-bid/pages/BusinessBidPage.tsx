/**
 * 模块：商务标入口页
 * 用途：项目列表 + 概念区分 + 进入分步工作区；列表走 API kind=business。
 * 对接：listProjectsAsync / createProjectAsync；路由 /business-bid/:id/:step
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Briefcase,
  FileStack,
  Plus,
  Sparkles,
} from "lucide-react";
import { formatRelativeTime } from "../../../shared/mock/projects";
import type { Project } from "../../../shared/types/workspace";
import {
  createProjectAsync,
  listProjectsAsync,
} from "../../technical-plan/lib/projectStore";
import { BUSINESS_STEPS } from "../components/BusinessStepStepper";
import { mockBusinessProjects } from "../mock";
import "./BusinessBid.css";

function stepIdFromIndex(stepIndex: number): string {
  return (
    BUSINESS_STEPS[Math.max(0, Math.min(stepIndex, BUSINESS_STEPS.length) - 1)]
      ?.id ?? "parse"
  );
}

export function BusinessBidPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [source, setSource] = useState<"api" | "local" | "mock">("mock");
  const [offlineHint, setOfflineHint] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    try {
      const res = await listProjectsAsync({ kind: "business" });
      if (res.projects.length) {
        setProjects(res.projects);
        setSource(res.source);
        setOfflineHint(res.offlineHint ?? null);
        return;
      }
      // API 空列表时仍展示演示项目，便于首次体验
      if (res.source === "api") {
        setProjects([]);
        setSource("api");
        setOfflineHint(null);
        return;
      }
    } catch {
      /* fallthrough */
    }
    setProjects(
      mockBusinessProjects.map((p) => ({
        id: p.id,
        workspaceId: p.workspaceId,
        name: p.name,
        industry: p.industry,
        status: "draft" as const,
        updatedAt: p.updatedAt,
        technicalPlanStep: p.currentStep,
        wordCount: 0,
        kind: "business" as const,
        linkedProjectId: p.linkedTechnicalProjectId,
      })),
    );
    setSource("mock");
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  function openProject(id: string, stepIndex: number) {
    navigate(`/business-bid/${id}/${stepIdFromIndex(stepIndex)}`);
  }

  async function createProject() {
    setCreating(true);
    try {
      const p = await createProjectAsync({
        name: `商务标 ${new Date().toLocaleDateString("zh-CN")}`,
        industry: "通用",
        kind: "business",
      });
      navigate(`/business-bid/${p.id}/parse`);
    } catch {
      // 回退演示
      openProject(mockBusinessProjects[0].id, 1);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="page bb-layout">
      <header className="page-header">
        <div>
          <h1>商务标生成</h1>
          <p>
            专注资格、报价与商务响应，不替代技术标正文。流水线：解析条款 → 资格响应 →
            目录清单 → 报价 → 授权承诺 → 导出。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/create" className="btn btn-ghost">
            返回创建
          </Link>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void createProject()}
            disabled={creating}
          >
            <Plus size={16} /> {creating ? "创建中…" : "从招标文件开始"}
          </button>
        </div>
      </header>

      <div className="bb-hint">
        <Briefcase size={16} />
        <div>
          <strong style={{ color: "var(--primary-deep)" }}>和另外两个入口怎么选？</strong>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
            <li>
              <strong>技术标生成</strong>：写实施方案、架构、进度、运维等技术内容。
            </li>
            <li>
              <strong>商务标生成（本页）</strong>：写资格、报价、授权承诺等商务册。
            </li>
            <li>
              <strong>完整投标文件</strong>：商务 + 技术一次规划，再分册深化。
            </li>
          </ul>
        </div>
      </div>

      <section>
        <div className="bb-toolbar">
          <strong>商务标项目</strong>
          <div className="bb-toolbar__spacer" />
          <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
            {source === "api"
              ? "已接后端 · kind=business"
              : source === "local"
                ? "本地回退"
                : "演示 mock"}
            {offlineHint ? ` · ${offlineHint}` : ""}
          </span>
        </div>
        {projects.length === 0 ? (
          <div className="card card-pad" style={{ textAlign: "center" }}>
            <FileStack size={28} style={{ opacity: 0.5, marginBottom: 8 }} />
            <p>暂无商务标项目，点击「从招标文件开始」创建。</p>
          </div>
        ) : (
          <div className="bb-project-grid">
            {projects.map((p) => {
              const step = p.technicalPlanStep || 1;
              const stepMeta =
                BUSINESS_STEPS[step - 1] ?? BUSINESS_STEPS[0];
              return (
                <div key={p.id} className="card card-pad bb-project-card">
                  <div>
                    <strong
                      style={{
                        display: "block",
                        marginBottom: 6,
                        lineHeight: 1.45,
                      }}
                    >
                      {p.name}
                    </strong>
                    <div className="bb-project-card__meta">
                      {p.industry} · 更新 {formatRelativeTime(p.updatedAt)}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <span className="badge badge-primary">
                      进度 STEP {step}/{BUSINESS_STEPS.length}
                    </span>
                    <span className="badge badge-muted">{stepMeta.title}</span>
                    {p.linkedProjectId && (
                      <span className="badge badge-free">已关联技术标</span>
                    )}
                  </div>
                  <div className="bb-project-card__actions">
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => openProject(p.id, step)}
                    >
                      进入工作区
                    </button>
                    {p.linkedProjectId && (
                      <Link
                        to={`/technical-plan/${p.linkedProjectId}`}
                        className="btn btn-ghost btn-sm"
                      >
                        <Sparkles size={14} /> 技术标
                      </Link>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="card card-pad" style={{ marginTop: 4 }}>
        <div className="bb-toolbar" style={{ marginBottom: 8 }}>
          <FileStack size={18} color="var(--primary)" />
          <strong>六步流水线预览</strong>
        </div>
        <div
          className="bb-stepper"
          style={{ padding: 0, border: "none", background: "transparent" }}
        >
          {BUSINESS_STEPS.map((s) => (
            <div
              key={s.id}
              className="bb-step is-done"
              style={{ pointerEvents: "none" }}
            >
              <span className="bb-step__idx">STEP {s.index}</span>
              <span className="bb-step__title">{s.title}</span>
              <span className="bb-step__desc">{s.description}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
