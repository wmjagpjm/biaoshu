import { useState } from "react";
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
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import { mockProjects } from "../../../shared/mock/projects";
import { StepStepper } from "../components/StepStepper";
import { ProjectGuidanceCard } from "../components/ProjectGuidanceCard";
import { useProjectGuidance } from "../hooks/useProjectGuidance";
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
  if (status === "done") return <span className="badge badge-primary">已完成</span>;
  if (status === "generating") return <span className="badge badge-primary">生成中</span>;
  if (status === "needs_review") return <span className="badge badge-free">待审</span>;
  return <span className="badge badge-muted">待生成</span>;
}

/**
 * 技术方案工作区
 * 用途：六步流水线 + 核心交互「人工反馈 → AI 定向调整」。
 * 各阶段均可文字反馈；招标分析步可编辑项目级生成要求并注入后续任务。
 */
export function TechnicalPlanWorkspace() {
  const { projectId = "", step } = useParams<{ projectId: string; step?: string }>();
  const project = mockProjects.find((p) => p.id === projectId) ?? mockProjects[0];
  const { guidance, history, updateGuidance, submitRevise } = useProjectGuidance(project.id);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [analysisOverview, setAnalysisOverview] = useState(mockAnalysis.overview);

  if (!step) {
    const defaultStep = STEP_IDS[Math.max(0, project.technicalPlanStep - 1)] ?? "document";
    return <Navigate to={`/technical-plan/${project.id}/${defaultStep}`} replace />;
  }

  if (!STEP_IDS.includes(step as TechnicalPlanStepId)) {
    return <Navigate to={`/technical-plan/${project.id}/document`} replace />;
  }

  const active = step as TechnicalPlanStepId;
  const flatOutline = flattenOutline(mockOutline);
  const selectedChapter =
    mockChapters.find((c) => c.id === selectedChapterId) ??
    mockChapters.find((c) => c.status === "done") ??
    mockChapters[0];

  return (
    <div className="page tp-layout">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 支持「手动改 / 按反馈 AI 调整 / 整段重生成」三种干预方式 ·
            项目要求可贯穿大纲与正文
          </p>
        </div>
        <div className="page-actions">
          <Link to="/technical-plan" className="btn btn-ghost">
            项目列表
          </Link>
          <button type="button" className="btn btn-ghost" disabled title="后端接入后可用">
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
              后回传结果。解析不准时可用下方反馈让 AI 定向修正识别结果。
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
                <span className="badge badge-primary">轻量解析完成</span>
              </div>
            </div>
            <div className="card card-pad" style={{ background: "var(--surface-card)" }}>
              <h3 style={{ marginTop: 0, fontSize: "var(--fs-md)" }}>解析预览（Markdown）</h3>
              <pre
                className="mono"
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  fontSize: "var(--fs-sm)",
                  color: "var(--text-secondary)",
                  lineHeight: 1.65,
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

          <AiFeedbackPanel
            stage="document_parse"
            targetLabel="当前解析文本"
            history={history}
            presets={[
              "表格识别错位，请按评分表重排",
              "补全缺失的废标条款段落",
              "合并重复的项目概况",
              "保留原文编号与★号标记",
            ]}
            placeholder="例如：第三章评分表第 3 行权重识别错误；请按 PDF 第 12 页修正…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "document_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => {
              /* 后端：重新跑解析任务 */
            }}
          />
        </section>
      )}

      {active === "analysis" && (
        <div className="tp-layout">
          <section className="tp-panel two-col">
            <div className="card card-pad analysis-grid">
              <div className="analysis-block">
                <h3>项目概述（可编辑）</h3>
                <textarea
                  value={analysisOverview}
                  onChange={(e) => setAnalysisOverview(e.target.value)}
                  style={{
                    width: "100%",
                    minHeight: 120,
                    border: "1px solid var(--border-strong)",
                    borderRadius: 10,
                    padding: 12,
                    fontSize: "var(--fs-md)",
                    lineHeight: 1.65,
                  }}
                />
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
                  <RefreshCw size={14} /> 整段重分析
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

          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="edit" />

          <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
            <AiFeedbackPanel
              stage="bid_analysis"
              targetLabel="招标分析结果"
              history={history}
              presets={[
                "补充遗漏的★号条款",
                "评分权重与文件不一致，请核对",
                "概述写得太泛，紧扣项目名称与规模",
                "废标风险再列 2～3 条形式评审点",
              ]}
              placeholder="例如：评分表漏了「售后服务 10%」；技术要求应单独列出信创清单…"
              onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
                submitRevise({
                  stage: "bid_analysis",
                  message,
                  preserveStructure,
                  targetId,
                  targetLabel,
                })
              }
              onRegenerate={() => undefined}
            />
          </div>
        </div>
      )}

      {active === "outline" && (
        <section className="card card-pad">
          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="summary" />

          <div className="tp-toolbar">
            <span className="badge badge-primary">模式：ALIGNED（对齐技术要求）</span>
            <button type="button" className="btn btn-ghost btn-sm">
              切换 FREE
            </button>
            <div className="tp-toolbar__spacer" />
            <button type="button" className="btn btn-ghost btn-sm">
              <Plus size={14} /> 手动添加章节
            </button>
            <button type="button" className="btn btn-ghost btn-sm" title="不携带文字反馈的整段重试">
              <RefreshCw size={14} /> 整段重生成
            </button>
          </div>
          <div className="outline-tree">
            {flatOutline.map((node) => (
              <div key={node.id} className={`outline-node is-l${node.level}`}>
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

          <AiFeedbackPanel
            stage="outline"
            targetLabel="当前目录"
            history={history}
            presets={[
              "一级目录对齐招标文件规定",
              "突出技术评分高的章节",
              "合并过碎的三级目录",
              "增加「信创与安全」独立一级",
              "控制总章数，避免超长",
            ]}
            placeholder="例如：把运维提升为一级；实施方案下增加「里程碑与交付物」；删除与评分无关的通用简介章…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "outline",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

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
          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="summary" />
          <div className="hint-banner">
            <Info size={16} />
            <span>
              全局事实将注入后续各章 Prompt。可用反馈让 AI 增删改事实，保持与招标/你的要求一致。
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

          <AiFeedbackPanel
            stage="global_facts"
            targetLabel="全局事实"
            history={history}
            presets={[
              "统一售后响应时间为 4 小时",
              "补充信创软硬件清单事实",
              "删除与招标冲突的承诺",
              "增加建设周期与里程碑事实",
            ]}
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "global_facts",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
          />

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
          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="summary" />
          <div className="tp-toolbar">
            <span className="badge badge-primary">任务进行中 · 62%</span>
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              点选章节后可对该章「按反馈调整」
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
            {mockChapters.map((c) => {
              const selected = c.id === selectedChapter.id;
              return (
                <div
                  key={c.id}
                  className="chapter-item"
                  style={{
                    cursor: "pointer",
                    borderColor: selected ? "rgba(100,56,255,0.45)" : undefined,
                    boxShadow: selected ? "0 0 0 1px rgba(100,56,255,0.15)" : undefined,
                  }}
                  onClick={() => setSelectedChapterId(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setSelectedChapterId(c.id);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div>
                    <div className="chapter-item__title">{c.title}</div>
                    <div className="chapter-item__preview">{c.preview}</div>
                  </div>
                  <div style={{ textAlign: "right", display: "grid", gap: 8, justifyItems: "end" }}>
                    {chapterStatusBadge(c.status)}
                    <span
                      className="mono"
                      style={{ fontSize: "var(--fs-xs)", color: "var(--text-tertiary)" }}
                    >
                      {c.wordCount > 0 ? `${c.wordCount} 字` : "—"}
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedChapterId(c.id);
                      }}
                    >
                      选中反馈
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <AiFeedbackPanel
            stage="chapter_content"
            targetId={selectedChapter.id}
            targetLabel={`章节：${selectedChapter.title}`}
            history={history}
            presets={[
              "扩写到目标字数，少套话",
              "紧扣全局事实，删冲突表述",
              "增加可落地的步骤与指标",
              "语气更正式、偏政务标书",
              "补充图表占位说明",
            ]}
            placeholder={`针对「${selectedChapter.title}」提出修改意见，例如：补充双机房切换流程；压缩产品软文…`}
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "chapter_content",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

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
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>准备导出 Word</strong>
              <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "var(--fs-sm)" }}>
                将合并大纲、正文与配图；导出样式也可通过反馈让 AI 调整模板参数（后续）。
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
