import { Link, Navigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  FileText,
  Info,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Upload,
} from "lucide-react";
import { mockProjects } from "../../../shared/mock/projects";
import { StepStepper } from "../components/StepStepper";
import {
  mockAnalysis,
  mockChapters,
  mockFacts,
  mockOutline,
} from "../mock";
import type { OutlineNode, TechnicalPlanStepId } from "../types";
import "./TechnicalPlan.css";

const STEP_IDS: TechnicalPlanStepId[] = [
  "document",
  "analysis",
  "outline",
  "facts",
  "content",
  "export",
];

function flattenOutline(nodes: OutlineNode[], acc: OutlineNode[] = []): OutlineNode[] {
  for (const n of nodes) {
    acc.push(n);
    if (n.children) flattenOutline(n.children, acc);
  }
  return acc;
}

function sourceLabel(s: "tender" | "knowledge" | "manual") {
  if (s === "tender") return "招标文件";
  if (s === "knowledge") return "知识库";
  return "手动";
}

function chapterStatusBadge(status: string) {
  if (status === "done") return <span className="badge badge-seal">已完成</span>;
  if (status === "generating") return <span className="badge badge-teal">生成中</span>;
  if (status === "needs_review") return <span className="badge badge-gold">待审</span>;
  return <span className="badge badge-muted">待生成</span>;
}

/**
 * 技术方案工作区
 * 用途：六步流水线统一容器；根据 :step 渲染对应面板。
 * 数据目前为 mock，后续替换为项目详情 API + 任务进度流。
 */
export function TechnicalPlanWorkspace() {
  const { projectId = "", step } = useParams<{ projectId: string; step?: string }>();
  const project = mockProjects.find((p) => p.id === projectId) ?? mockProjects[0];

  if (!step) {
    const defaultStep = STEP_IDS[Math.max(0, project.technicalPlanStep - 1)] ?? "document";
    return <Navigate to={`/technical-plan/${project.id}/${defaultStep}`} replace />;
  }

  if (!STEP_IDS.includes(step as TechnicalPlanStepId)) {
    return <Navigate to={`/technical-plan/${project.id}/document`} replace />;
  }

  const active = step as TechnicalPlanStepId;
  const flatOutline = flattenOutline(mockOutline);

  return (
    <div className="page tp-layout">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 工作流步骤可随时回退修改 · 长任务将以后台任务形式运行
          </p>
        </div>
        <div className="page-actions">
          <Link to="/technical-plan" className="btn btn-ghost">
            项目列表
          </Link>
          <button type="button" className="btn btn-ink" disabled title="后端接入后可用">
            <Pause size={16} /> 暂停任务
          </button>
        </div>
      </header>

      <StepStepper
        projectId={project.id}
        active={active}
        doneUntil={project.technicalPlanStep}
      />

      {active === "document" && (
        <section className="card card-pad">
          <div className="hint-banner">
            <Info size={16} />
            <span>
              默认使用在线轻量解析；复杂版式/扫描件请安装
              <Link to="/local-parser" style={{ margin: "0 4px", textDecoration: "underline" }}>
                本地解析插件
              </Link>
              后回传结果。
            </span>
          </div>
          <div className="tp-panel two-col">
            <div>
              <div className="upload-zone">
                <div className="upload-zone__icon">
                  <Upload size={22} />
                </div>
                <h3>上传招标文件</h3>
                <p>支持 PDF / DOCX（单文件建议 ≤ 100MB）。前端阶段仅演示交互。</p>
                <button type="button" className="btn btn-primary">
                  选择文件
                </button>
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span className="file-chip">
                  <FileText size={14} /> 招标文件-正式稿.pdf
                </span>
                <span className="badge badge-teal">轻量解析完成</span>
              </div>
            </div>
            <div className="card card-pad" style={{ background: "var(--paper)" }}>
              <h3 style={{ marginTop: 0, fontSize: 14 }}>解析预览（Markdown）</h3>
              <pre
                className="mono"
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  lineHeight: 1.6,
                  maxHeight: 280,
                  overflow: "auto",
                }}
              >
{`# 招标公告

## 一、项目概况
项目名称：某市智慧交通综合管理平台
建设周期：180 日历天

## 二、技术要求
1. 视频接入不少于 2000 路…
2. 支持信创环境…

## 三、评分办法
…`}
              </pre>
              <div className="tp-toolbar" style={{ marginTop: 14, marginBottom: 0 }}>
                <div className="tp-toolbar__spacer" />
                <Link
                  to={`/technical-plan/${project.id}/analysis`}
                  className="btn btn-primary"
                >
                  下一步：招标分析
                </Link>
              </div>
            </div>
          </div>
        </section>
      )}

      {active === "analysis" && (
        <section className="tp-panel two-col">
          <div className="card card-pad analysis-grid">
            <div className="analysis-block">
              <h3>项目概述</h3>
              <p style={{ margin: 0, color: "var(--text-secondary)" }}>
                {mockAnalysis.overview}
              </p>
            </div>
            <div className="analysis-block">
              <h3>技术要求摘录</h3>
              <ul>
                {mockAnalysis.techRequirements.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
            <div className="analysis-block">
              <h3>潜在废标风险</h3>
              <ul>
                {mockAnalysis.rejectionRisks.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
          </div>
          <div className="card card-pad">
            <div className="tp-toolbar">
              <strong>评分点</strong>
              <div className="tp-toolbar__spacer" />
              <button type="button" className="btn btn-ghost btn-sm">
                <RefreshCw size={14} /> 重新分析
              </button>
            </div>
            <table className="score-table">
              <thead>
                <tr>
                  <th>评分项</th>
                  <th>权重</th>
                </tr>
              </thead>
              <tbody>
                {mockAnalysis.scoringPoints.map((s) => (
                  <tr key={s.name}>
                    <td>{s.name}</td>
                    <td className="mono">{s.weight}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="tp-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="tp-toolbar__spacer" />
              <Link to={`/technical-plan/${project.id}/outline`} className="btn btn-primary">
                下一步：生成大纲
              </Link>
            </div>
          </div>
        </section>
      )}

      {active === "outline" && (
        <section className="card card-pad">
          <div className="tp-toolbar">
            <span className="badge badge-gold">模式：ALIGNED（对齐技术要求）</span>
            <button type="button" className="btn btn-ghost btn-sm">
              切换 FREE
            </button>
            <div className="tp-toolbar__spacer" />
            <button type="button" className="btn btn-ghost btn-sm">
              <Plus size={14} /> 添加章节
            </button>
            <button type="button" className="btn btn-soft btn-sm">
              <RefreshCw size={14} /> AI 重生成
            </button>
          </div>
          <div className="outline-tree">
            {flatOutline.map((node) => (
              <div
                key={node.id}
                className={`outline-node is-l${node.level}`}
              >
                <div className="outline-node__row">
                  <span className="outline-node__title">{node.title}</span>
                  <span className="outline-node__meta">
                    L{node.level}
                    {node.targetWords ? ` · ${node.targetWords} 字` : ""}
                  </span>
                </div>
              </div>
            ))}
          </div>
          <div className="tp-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/facts`} className="btn btn-primary">
              下一步：全局事实
            </Link>
          </div>
        </section>
      )}

      {active === "facts" && (
        <section className="card card-pad">
          <div className="hint-banner">
            <Info size={16} />
            <span>
              全局事实将注入后续各章 Prompt，减少前后矛盾（C 端关键抗幻觉步骤，请勿跳过）。
            </span>
          </div>
          <div className="tp-toolbar">
            <strong>事实清单</strong>
            <div className="tp-toolbar__spacer" />
            <button type="button" className="btn btn-ghost btn-sm">
              <Plus size={14} /> 手动添加
            </button>
            <button type="button" className="btn btn-soft btn-sm">
              从招标/知识库抽取
            </button>
          </div>
          <div className="fact-list">
            {mockFacts.map((f) => (
              <div key={f.id} className="fact-item">
                <div className="fact-item__cat">{f.category}</div>
                <div className="fact-item__content">{f.content}</div>
                <span className="badge badge-muted">{sourceLabel(f.source)}</span>
              </div>
            ))}
          </div>
          <div className="tp-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/content`} className="btn btn-primary">
              下一步：正文生成
            </Link>
          </div>
        </section>
      )}

      {active === "content" && (
        <section className="card card-pad">
          <div className="tp-toolbar">
            <span className="badge badge-teal">任务进行中 · 62%</span>
            <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
              按章生成 · 支持扩写至目标字数
            </span>
            <div className="tp-toolbar__spacer" />
            <button type="button" className="btn btn-ghost btn-sm">
              <Pause size={14} /> 暂停
            </button>
            <button type="button" className="btn btn-primary btn-sm">
              <Play size={14} /> 继续生成
            </button>
          </div>
          <div className="chapter-list">
            {mockChapters.map((c) => (
              <div key={c.id} className="chapter-item">
                <div>
                  <div className="chapter-item__title">{c.title}</div>
                  <div className="chapter-item__preview">{c.preview}</div>
                </div>
                <div style={{ textAlign: "right", display: "grid", gap: 8, justifyItems: "end" }}>
                  {chapterStatusBadge(c.status)}
                  <span className="mono" style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    {c.wordCount > 0 ? `${c.wordCount} 字` : "—"}
                  </span>
                  <button type="button" className="btn btn-ghost btn-sm">
                    编辑
                  </button>
                </div>
              </div>
            ))}
          </div>
          <div className="tp-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/export`} className="btn btn-primary">
              下一步：导出
            </Link>
          </div>
        </section>
      )}

      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <CheckCircle2 size={28} color="var(--teal)" />
            <div>
              <strong style={{ fontSize: 16 }}>准备导出 Word</strong>
              <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: 13 }}>
                将合并大纲、正文与配图（Mermaid 栅格化后续支持），按导出格式模板生成 docx。
              </p>
            </div>
          </div>
          <div className="field" style={{ marginBottom: 14 }}>
            <label>导出模板</label>
            <select defaultValue="gov-standard">
              <option value="gov-standard">政务投标通用（宋体标题）</option>
              <option value="enterprise">企业方案风</option>
              <option value="custom">自定义（见导出格式页）</option>
            </select>
          </div>
          <div className="tp-toolbar" style={{ marginBottom: 0 }}>
            <Link to="/export-format" className="btn btn-ghost">
              管理模板
            </Link>
            <div className="tp-toolbar__spacer" />
            <button type="button" className="btn btn-primary">
              <Download size={16} /> 生成并下载 Word
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
