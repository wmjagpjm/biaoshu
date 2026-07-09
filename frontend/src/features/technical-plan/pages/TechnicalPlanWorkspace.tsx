import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  FileText,
  Info,
  Pause,
  Play,
  RefreshCw,
  Upload,
} from "lucide-react";
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import type { Project } from "../../../shared/types/workspace";
import { ChapterEditor } from "../components/ChapterEditor";
import { FactsEditor } from "../components/FactsEditor";
import { OutlineStepWorkspace } from "../components/OutlineStepWorkspace";
import { ProjectGuidanceCard } from "../components/ProjectGuidanceCard";
import { StepStepper } from "../components/StepStepper";
import { useProjectGuidance } from "../hooks/useProjectGuidance";
import { useProjectPipeline } from "../hooks/useProjectPipeline";
import {
  factsToText,
  outlineToMarkdown,
  useTechnicalPlanEditors,
} from "../hooks/useTechnicalPlanEditors";
import {
  getPendingFileNames,
  getProjectAsync,
} from "../lib/projectStore";
import { mockAnalysis } from "../mock";
import type { TechnicalPlanStepId } from "../types";
import "./TechnicalPlan.css";

/** 用途：联调展示最近一次 revise 正文；文本步可一键替换。 */
function RevisePreviewPanel(props: {
  text: string | null;
  canApply: boolean;
  applyLabel?: string;
  onApply?: () => void;
  onClear: () => void;
}) {
  if (!props.text) return null;
  return (
    <div className="tp-revise-preview" role="region" aria-label="修订结果预览">
      <div className="tp-revise-preview__head">
        <strong>修订结果预览</strong>
        <span style={{ color: "var(--text-secondary)", flex: 1 }}>
          {props.canApply
            ? "可应用到当前编辑区"
            : "大纲等结构化内容仅预览，请人工对照修改"}
        </span>
        {props.canApply && props.onApply && (
          <button type="button" className="btn btn-primary btn-sm" onClick={props.onApply}>
            {props.applyLabel ?? "应用到编辑器"}
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={props.onClear}>
          关闭
        </button>
      </div>
      <pre className="tp-revise-preview__body mono">{props.text}</pre>
    </div>
  );
}

const STEP_IDS: TechnicalPlanStepId[] = [
  "document",
  "analysis",
  "outline",
  "facts",
  "content",
  "export",
];

/**
 * 模块：技术方案工作区
 * 用途：六步流水线 + 反馈修订；传 baseContent、展示 revisedContent。
 * 对接：getProjectAsync、editor-state、POST .../revise
 * 二次开发：上传解析后 document 步 baseContent 改为真实 Markdown。
 */
export function TechnicalPlanWorkspace() {
  const { projectId = "", step } = useParams<{ projectId: string; step?: string }>();
  const [project, setProject] = useState<Project | null | undefined>(undefined);
  const resolvedId = project?.id ?? projectId ?? "missing";
  const { guidance, history, updateGuidance, submitRevise } =
    useProjectGuidance(resolvedId);
  const editors = useTechnicalPlanEditors(resolvedId);
  const pipeline = useProjectPipeline(resolvedId);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [revisePreview, setRevisePreview] = useState<string | null>(null);
  const [revisePreviewStep, setRevisePreviewStep] = useState<TechnicalPlanStepId | null>(
    null,
  );
  const [taskTip, setTaskTip] = useState("");

  useEffect(() => {
    if (projectId) void pipeline.refreshFiles();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 解析正文：优先服务端 parsedMarkdown，否则演示占位
  const documentPreviewMd =
    editors.parsedMarkdown?.trim() ||
    `# 招标公告（尚未解析）

请上传 PDF/DOCX/TXT 后点击「轻量解析」。
项目：${project?.name ?? "未命名"}
`;

  useEffect(() => {
    let cancelled = false;
    setProject(undefined);
    void getProjectAsync(projectId).then((p) => {
      if (!cancelled) setProject(p ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (project === undefined) {
    return (
      <div className="page">
        <LoadingBlock label="加载项目…" />
      </div>
    );
  }

  if (!project) {
    return <Navigate to="/technical-plan" replace />;
  }

  const pendingFiles = getPendingFileNames(project.id);
  const displayFiles =
    pipeline.files.length > 0
      ? pipeline.files.map((f) => f.filename)
      : pendingFiles.length > 0
        ? pendingFiles
        : [];

  if (!step) {
    const defaultStep =
      STEP_IDS[Math.max(0, project.technicalPlanStep - 1)] ?? "document";
    return (
      <Navigate to={`/technical-plan/${project.id}/${defaultStep}`} replace />
    );
  }

  if (!STEP_IDS.includes(step as TechnicalPlanStepId)) {
    return <Navigate to={`/technical-plan/${project.id}/document`} replace />;
  }

  const active = step as TechnicalPlanStepId;
  const selectedChapter = editors.selectedChapter;

  async function runRevise(
    stepId: TechnicalPlanStepId,
    payload: {
      stage: Parameters<typeof submitRevise>[0]["stage"];
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
      baseContent?: string;
    },
  ) {
    const res = await submitRevise(payload);
    if (res.ok && res.revisedContent) {
      setRevisePreview(res.revisedContent);
      setRevisePreviewStep(stepId);
    } else if (res.ok && res.resultSummary) {
      setRevisePreview(res.resultSummary);
      setRevisePreviewStep(stepId);
    }
  }

  return (
    <div className="page tp-layout">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 编辑：
            {editors.persistSource === "api" ? "后端" : "本地"}
            {pipeline.busy
              ? " · 任务执行中…"
              : pipeline.lastTask
                ? ` · 最近任务 ${pipeline.lastTask.type}/${pipeline.lastTask.status}`
                : ""}
          </p>
        </div>
        <div className="page-actions">
          <Link to="/technical-plan" className="btn btn-ghost">
            项目列表
          </Link>
          <button
            type="button"
            className="btn btn-ghost"
            disabled
            title="个人版任务同步执行，暂无暂停"
          >
            <Pause size={16} /> 暂停任务
          </button>
        </div>
      </header>

      {(pipeline.error || taskTip) && (
        <div
          className={`tp-source-banner ${pipeline.error ? "is-local" : "is-api"}`}
          role="status"
        >
          {pipeline.error || taskTip}
        </div>
      )}

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
              上传后点「轻量解析」写入后端。扫描件请用
              <Link to="/local-parser" style={{ margin: "0 4px", textDecoration: "underline" }}>
                本地 MinerU
              </Link>
              。设置页需配置可用模型 Key（分析/生成步骤需要）。
            </span>
          </div>
          <div className="tp-panel two-col">
            <div>
              <div className="upload-zone">
                <div className="upload-zone__icon">
                  <Upload size={22} />
                </div>
                <h3>上传招标文件</h3>
                <p>支持 PDF / DOCX / TXT / MD（单文件默认 ≤ 50MB）。</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.txt,.md,.markdown,application/pdf"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    void (async () => {
                      try {
                        await pipeline.uploadFile(f);
                        setTaskTip(`已上传：${f.name}，可点击「轻量解析」`);
                      } catch {
                        /* error 已在 pipeline */
                      }
                      e.target.value = "";
                    })();
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={pipeline.busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  选择文件
                </button>
                <button
                  type="button"
                  className="btn btn-soft"
                  style={{ marginLeft: 8 }}
                  disabled={pipeline.busy || pipeline.files.length === 0}
                  onClick={() => {
                    void (async () => {
                      try {
                        const t = await pipeline.runTask("parse");
                        if (t.status === "success") {
                          await editors.reloadFromApi();
                          setTaskTip("解析完成，请查看右侧预览");
                        }
                      } catch {
                        /* */
                      }
                    })();
                  }}
                >
                  {pipeline.busy ? "处理中…" : "轻量解析"}
                </button>
              </div>
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {displayFiles.length === 0 ? (
                  <span className="file-chip">尚未上传文件</span>
                ) : (
                  displayFiles.map((name) => (
                    <span key={name} className="file-chip">
                      <FileText size={14} /> {name}
                    </span>
                  ))
                )}
                {editors.parsedMarkdown?.trim() ? (
                  <span className="badge badge-primary">已解析</span>
                ) : (
                  <span className="badge">未解析</span>
                )}
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
{documentPreviewMd}
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
              void runRevise("document", {
                stage: "document_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
                baseContent: [
                  `文件：${displayFiles.join("、")}`,
                  "",
                  documentPreviewMd,
                ].join("\n"),
              })
            }
            onRegenerate={() => {
              /* 后端：重新跑解析任务 */
            }}
          />
          <RevisePreviewPanel
            text={revisePreviewStep === "document" ? revisePreview : null}
            canApply={false}
            onClear={() => setRevisePreview(null)}
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
                  value={editors.analysisOverview}
                  onChange={(e) => editors.setAnalysisOverview(e.target.value)}
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
                <strong>评分点（演示列表）</strong>
                <div className="tp-toolbar__spacer" />
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={pipeline.busy}
                  onClick={() => {
                    void (async () => {
                      try {
                        const t = await pipeline.runTask("analyze");
                        if (t.status === "success") {
                          await editors.reloadFromApi();
                          setTaskTip("招标分析已写入概述");
                        }
                      } catch {
                        /* */
                      }
                    })();
                  }}
                >
                  <RefreshCw size={14} /> {pipeline.busy ? "分析中…" : "AI 招标分析"}
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
                void runRevise("analysis", {
                  stage: "bid_analysis",
                  message,
                  preserveStructure,
                  targetId,
                  targetLabel,
                  baseContent: editors.analysisOverview,
                })
              }
              onRegenerate={() => undefined}
            />
            <RevisePreviewPanel
              text={revisePreviewStep === "analysis" ? revisePreview : null}
              canApply
              applyLabel="替换项目概述"
              onApply={() => {
                if (revisePreview) editors.setAnalysisOverview(revisePreview);
                setRevisePreview(null);
              }}
              onClear={() => setRevisePreview(null)}
            />
          </div>
        </div>
      )}

      {active === "outline" && (
        <>
          <div className="tp-toolbar">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={pipeline.busy}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("outline");
                    if (t.status === "success") {
                      await editors.reloadFromApi();
                      setTaskTip("大纲与章节列表已生成");
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              {pipeline.busy ? "生成中…" : "AI 生成大纲"}
            </button>
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              将根据招标分析/解析文本调用模型，写入后端 editor-state
            </span>
          </div>
          <OutlineStepWorkspace
            projectId={project.id}
            outline={editors.outline}
            selectedId={editors.selectedOutlineId}
            moveFlags={editors.moveFlags}
            generating={pipeline.busy}
            progress={pipeline.lastTask?.type === "outline" ? pipeline.lastTask.progress : 100}
            onSelect={editors.setSelectedOutlineId}
            onPatch={editors.patchOutlineNode}
            onDelete={editors.deleteOutlineNode}
            onAddSibling={editors.addOutlineSibling}
            onAddChild={editors.addOutlineChild}
            onMove={editors.moveOutline}
          />
          <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
            <AiFeedbackPanel
              stage="outline"
              targetLabel="目录大纲"
              history={history}
              presets={[
                "一级目录对齐招标文件",
                "压缩重复小节",
                "突出评分高的章节",
              ]}
              onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
                void runRevise("outline", {
                  stage: "outline",
                  message,
                  preserveStructure,
                  targetId,
                  targetLabel,
                  baseContent: outlineToMarkdown(editors.outline),
                })
              }
            />
            <RevisePreviewPanel
              text={revisePreviewStep === "outline" ? revisePreview : null}
              canApply={false}
              onClear={() => setRevisePreview(null)}
            />
          </div>
        </>
      )}

      {active === "facts" && (
        <section className="card card-pad">
          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="summary" />
          <div className="hint-banner">
            <Info size={16} />
            <span>
              全局事实将用于后续各章编写约束。可手动增删改，也可在下方填写修改意见后修订。
            </span>
          </div>

          <FactsEditor
            facts={editors.facts}
            onAdd={editors.addFact}
            onUpdate={editors.updateFact}
            onRemove={editors.removeFact}
            onExtractDemo={editors.extractDemoFacts}
          />

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
              void runRevise("facts", {
                stage: "global_facts",
                message,
                preserveStructure,
                targetId,
                targetLabel,
                baseContent: factsToText(editors.facts),
              })
            }
          />
          <RevisePreviewPanel
            text={revisePreviewStep === "facts" ? revisePreview : null}
            canApply={false}
            onClear={() => setRevisePreview(null)}
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
        <div className="tp-layout">
          <ProjectGuidanceCard guidance={guidance} onChange={updateGuidance} mode="summary" />
          <div className="tp-toolbar">
            <span className="badge badge-primary">
              {pipeline.busy ? "生成中…" : "可生成章节"}
            </span>
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              左侧选章，右侧编辑；点「AI 生成本章」调用模型
            </span>
            <div className="tp-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={pipeline.busy || !selectedChapter}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("chapter", {
                      chapterId: selectedChapter?.id,
                    });
                    if (t.status === "success") {
                      await editors.reloadFromApi();
                      setTaskTip(`章节已生成：${selectedChapter?.title ?? ""}`);
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              <Play size={14} /> {pipeline.busy ? "生成中…" : "AI 生成本章"}
            </button>
          </div>

          <ChapterEditor
            chapters={editors.chapters}
            selectedId={editors.selectedChapterId}
            onSelect={editors.setSelectedChapterId}
            onChangeBody={editors.updateChapterBody}
            onChangeTitle={editors.updateChapterTitle}
          />

          {selectedChapter && (
            <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
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
                  void runRevise("content", {
                    stage: "chapter_content",
                    message,
                    preserveStructure,
                    targetId,
                    targetLabel,
                    baseContent: selectedChapter.body || selectedChapter.title,
                  })
                }
                onRegenerate={() => undefined}
              />
              <RevisePreviewPanel
                text={revisePreviewStep === "content" ? revisePreview : null}
                canApply={!!selectedChapter}
                applyLabel="替换当前章节正文"
                onApply={() => {
                  if (revisePreview && selectedChapter) {
                    editors.replaceChapterBody(selectedChapter.id, revisePreview);
                  }
                  setRevisePreview(null);
                }}
                onClear={() => setRevisePreview(null)}
              />
            </div>
          )}

          <div className="tp-toolbar" style={{ marginTop: 0, marginBottom: 0 }}>
            <div className="tp-toolbar__spacer" />
            <Link to={`/technical-plan/${project.id}/export`} className="btn btn-primary">
              下一步：导出
            </Link>
          </div>
        </div>
      )}

      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>准备导出 Word</strong>
              <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "var(--fs-sm)" }}>
                将合并大纲、正文与配图；导出样式可在模板设置中调整。
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
            <button
              type="button"
              className="btn btn-primary"
              disabled={pipeline.busy}
              onClick={() => {
                void (async () => {
                  try {
                    const t = await pipeline.runTask("export");
                    if (t.status === "success") {
                      setTaskTip("Word 已生成，正在下载…");
                      pipeline.downloadExport(t);
                    }
                  } catch {
                    /* */
                  }
                })();
              }}
            >
              <Download size={16} />{" "}
              {pipeline.busy ? "导出中…" : "生成并下载 Word"}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
