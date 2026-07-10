/**
 * 模块：商务标分步工作区
 * 用途：六步流水线；上传/解析/biz_* 生成/导出接 project/task/editor-state。
 * 对接：useProjectPipeline、useBusinessBidWorkspace、GET project
 * 二次开发：勿大改步骤信息架构；新任务类型扩在 pipeline TaskType。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  Info,
  Loader2,
  RefreshCw,
  Square,
  Upload,
} from "lucide-react";
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import { getApiBase } from "../../../shared/lib/api";
import type { Project } from "../../../shared/types/workspace";
import { useProjectPipeline } from "../../technical-plan/hooks/useProjectPipeline";
import { getProjectAsync } from "../../technical-plan/lib/projectStore";
import {
  BusinessStepStepper,
  BUSINESS_STEPS,
} from "../components/BusinessStepStepper";
import { useBusinessBidWorkspace } from "../hooks/useBusinessBidWorkspace";
import { mockBusinessProjects } from "../mock";
import type { BusinessBidStepId, QualifyItemStatus } from "../types";
import "./BusinessBid.css";

const STEP_IDS: BusinessBidStepId[] = BUSINESS_STEPS.map((s) => s.id);

function qualifyStatusLabel(s: QualifyItemStatus): string {
  if (s === "matched") return "已响应";
  if (s === "partial") return "待确认";
  if (s === "missing") return "缺材料";
  return "待处理";
}

function nextStepPath(
  projectId: string,
  active: BusinessBidStepId,
): string | null {
  const idx = STEP_IDS.indexOf(active);
  if (idx < 0 || idx >= STEP_IDS.length - 1) return null;
  return `/business-bid/${projectId}/${STEP_IDS[idx + 1]}`;
}

export function BusinessBidWorkspace() {
  const { projectId = "", step } = useParams<{
    projectId: string;
    step?: string;
  }>();

  const [project, setProject] = useState<Project | null>(null);
  const [projectLoading, setProjectLoading] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    workspace,
    history,
    loading: wsLoading,
    saveError,
    refreshFromApi,
    setParseMarkdown,
    updateQualifyItem,
    toggleTocItem,
    updateQuoteRow,
    setQuoteNotes,
    updateCommitBlock,
    submitRevise,
  } = useBusinessBidWorkspace(projectId);

  const pipeline = useProjectPipeline(projectId);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setProjectLoading(true);
      const remote = await getProjectAsync(projectId);
      if (cancelled) return;
      if (remote) {
        setProject(remote);
      } else {
        const mock = mockBusinessProjects.find((p) => p.id === projectId);
        if (mock) {
          setProject({
            id: mock.id,
            workspaceId: mock.workspaceId,
            name: mock.name,
            industry: mock.industry,
            status: "draft",
            updatedAt: mock.updatedAt,
            technicalPlanStep: mock.currentStep,
            wordCount: 0,
            kind: "business",
            linkedProjectId: mock.linkedTechnicalProjectId,
          });
        } else {
          setProject(null);
        }
      }
      setProjectLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    void pipeline.refreshFiles();
    void pipeline.refreshTasks();
    // 仅 projectId 变化时刷新
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const runBizTask = useCallback(
    async (
      type:
        | "parse"
        | "biz_qualify"
        | "biz_toc"
        | "biz_quote"
        | "biz_commit"
        | "export",
      payload?: Record<string, unknown>,
    ) => {
      const t = await pipeline.runTask(type, payload);
      if (t.status === "success") {
        await refreshFromApi();
        const remote = await getProjectAsync(projectId);
        if (remote) setProject(remote);
      }
      return t;
    },
    [pipeline, projectId, refreshFromApi],
  );

  const onPickFile = useCallback(
    async (file: File | null) => {
      if (!file) return;
      await pipeline.uploadFile(file);
      await runBizTask("parse");
    },
    [pipeline, runBizTask],
  );

  const onRevise = useCallback(
    (
      stage:
        | "business_parse"
        | "business_qualify"
        | "business_toc"
        | "business_quote"
        | "business_commit",
      message: string,
      preserveStructure: boolean,
      targetId?: string,
      targetLabel?: string,
    ) => {
      void submitRevise({
        stage,
        message,
        preserveStructure,
        targetId,
        targetLabel,
      });
    },
    [submitRevise],
  );

  if (projectLoading || wsLoading) {
    return (
      <div className="page bb-layout">
        <p style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Loader2 size={18} /> 加载商务标工作区…
        </p>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="page bb-layout">
        <p>未找到项目。</p>
        <Link to="/business-bid" className="btn btn-primary">
          返回列表
        </Link>
      </div>
    );
  }

  if (!step) {
    const defaultStep =
      STEP_IDS[Math.max(0, (project.technicalPlanStep || 1) - 1)] ?? "parse";
    return (
      <Navigate to={`/business-bid/${project.id}/${defaultStep}`} replace />
    );
  }

  if (!STEP_IDS.includes(step as BusinessBidStepId)) {
    return <Navigate to={`/business-bid/${project.id}/parse`} replace />;
  }

  const active = step as BusinessBidStepId;
  const nextPath = nextStepPath(project.id, active);
  const doneUntil = project.technicalPlanStep || 0;
  const busy = pipeline.busy;
  const lastTask = pipeline.lastTask;
  const checkedCount = workspace.tocItems.filter((t) => t.checked).length;
  const missingQualify = workspace.qualifyItems.filter(
    (q) => q.status === "missing" || q.status === "partial",
  ).length;

  return (
    <div className="page bb-layout">
      <header className="page-header">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.industry} · 可手动编辑，也可填写修改意见后修订 · 与技术标分册
            {saveError ? ` · 保存异常：${saveError}` : ""}
          </p>
        </div>
        <div className="page-actions">
          <Link to="/business-bid" className="btn btn-ghost">
            项目列表
          </Link>
          {project.linkedProjectId && (
            <Link
              to={`/technical-plan/${project.linkedProjectId}`}
              className="btn btn-soft"
            >
              打开关联技术标
            </Link>
          )}
        </div>
      </header>

      {(busy || lastTask || pipeline.error) && (
        <div className="bb-hint" style={{ marginBottom: 12 }}>
          <Info size={16} />
          <div style={{ flex: 1 }}>
            {pipeline.error && (
              <div style={{ color: "var(--danger)" }}>{pipeline.error}</div>
            )}
            {lastTask && (
              <div>
                任务 <strong>{lastTask.type}</strong> · {lastTask.status} ·{" "}
                {lastTask.progress}% · {lastTask.message}
              </div>
            )}
          </div>
          {lastTask &&
            (lastTask.status === "pending" ||
              lastTask.status === "running") && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => void pipeline.cancelTask()}
              >
                <Square size={14} /> 取消
              </button>
            )}
        </div>
      )}

      <BusinessStepStepper
        projectId={project.id}
        active={active}
        doneUntil={doneUntil}
      />

      {active === "parse" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              识别资格条件、付款/保证金、有效期等商务条款。复杂扫描件可走
              <Link
                to="/local-parser"
                style={{ margin: "0 4px", textDecoration: "underline" }}
              >
                本地 MinerU 插件
              </Link>
              。解析不准时用下方反馈定向修正。
            </span>
          </div>
          <div className="bb-two-col">
            <div>
              <div className="upload-zone">
                <div className="upload-zone__icon">
                  <Upload size={22} />
                </div>
                <h3>上传招标文件</h3>
                <p>支持 PDF / DOCX；上传后自动 parse。</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.doc,.docx,.txt,.md"
                  hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    e.target.value = "";
                    void onPickFile(f);
                  }}
                />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {busy ? "处理中…" : "选择文件"}
                </button>
              </div>
              <div
                style={{
                  marginTop: 12,
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                {pipeline.files.length === 0 ? (
                  <span className="badge badge-muted">尚未上传</span>
                ) : (
                  pipeline.files.map((f) => (
                    <span key={f.id} className="file-chip">
                      {f.filename}
                    </span>
                  ))
                )}
                {workspace.parseMarkdown.trim() ? (
                  <span className="badge badge-primary">已有解析文本</span>
                ) : null}
              </div>
            </div>
            <div>
              <div className="bb-toolbar">
                <strong>解析预览（可编辑）</strong>
                <div className="bb-toolbar__spacer" />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={busy || pipeline.files.length === 0}
                  onClick={() => void runBizTask("parse")}
                >
                  <RefreshCw size={14} /> 整段重解析
                </button>
              </div>
              <textarea
                className="bb-parse-edit"
                value={workspace.parseMarkdown}
                onChange={(e) => setParseMarkdown(e.target.value)}
                aria-label="商务条款解析 Markdown"
              />
            </div>
          </div>

          <AiFeedbackPanel
            stage="business_parse"
            targetLabel="商务条款解析"
            history={history}
            presets={[
              "补全遗漏的★号资格条款",
              "付款节点拆成条目列表",
              "标出履约保证金与有效期",
              "保留原文编号与强制性用语",
            ]}
            placeholder="例如：社保人数要求识别有误，请按 PDF 修正…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("parse")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：资格响应
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "qualify" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              对照资格要求逐条填写。待确认/缺材料：
              <strong> {missingQualify} </strong>
              条。
            </span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_qualify")}
            >
              <RefreshCw size={14} /> 生成资格草稿
            </button>
          </div>
          <div className="bb-qualify-list">
            {workspace.qualifyItems.map((item) => (
              <div key={item.id} className="bb-qualify-item">
                <div className="bb-qualify-item__head">
                  <div className="bb-qualify-item__req">{item.requirement}</div>
                  <select
                    className={`bb-status-pill is-${item.status}`}
                    value={item.status}
                    onChange={(e) =>
                      updateQualifyItem(item.id, {
                        status: e.target.value as QualifyItemStatus,
                      })
                    }
                    aria-label="响应状态"
                    style={{
                      border: "none",
                      cursor: "pointer",
                      appearance: "auto",
                    }}
                  >
                    <option value="matched">已响应</option>
                    <option value="partial">待确认</option>
                    <option value="missing">缺材料</option>
                    <option value="pending">待处理</option>
                  </select>
                </div>
                <div className="field">
                  <label>响应说明</label>
                  <textarea
                    rows={3}
                    value={item.response}
                    onChange={(e) =>
                      updateQualifyItem(item.id, { response: e.target.value })
                    }
                  />
                </div>
                <div className="field">
                  <label>证明材料索引</label>
                  <input
                    value={item.evidence}
                    onChange={(e) =>
                      updateQualifyItem(item.id, { evidence: e.target.value })
                    }
                    placeholder="附件名或知识库文档"
                  />
                </div>
                <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  状态：{qualifyStatusLabel(item.status)}
                </div>
              </div>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_qualify"
            targetLabel="资格响应表"
            history={history}
            presets={[
              "缺材料条目补写可落地的响应模板",
              "统一业绩描述口径与年份",
              "★ 号条款单独加粗提示",
            ]}
            placeholder="例如：第 4 条社保人数按 15 人重写响应…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_qualify",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_qualify")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：目录清单
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "toc" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              勾选拟递交材料。已勾选 {checkedCount}/{workspace.tocItems.length}。
            </span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_toc")}
            >
              <RefreshCw size={14} /> 生成材料清单
            </button>
          </div>
          <div className="bb-toc-list">
            {workspace.tocItems.map((item) => (
              <label key={item.id} className="bb-toc-row">
                <input
                  type="checkbox"
                  checked={item.checked}
                  onChange={() => toggleTocItem(item.id)}
                  aria-label={item.title}
                />
                <div>
                  <div className="bb-toc-row__title">{item.title}</div>
                  {item.note && (
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--warning)",
                        marginTop: 4,
                      }}
                    >
                      {item.note}
                    </div>
                  )}
                </div>
                <span className="bb-toc-row__cat">{item.category}</span>
                <span
                  className={`bb-status-pill ${
                    item.status === "optional" ? "is-pending" : "is-matched"
                  }`}
                >
                  {item.status === "optional" ? "可选" : "必需"}
                </span>
              </label>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_toc"
            targetLabel="商务目录清单"
            history={history}
            presets={["按招标目录顺序重排", "合并重复的资格证明项"]}
            placeholder="例如：增加「项目团队社保证明」…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_toc",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_toc")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：报价说明
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "quote" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>分项报价表。金额可手改。</span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_quote")}
            >
              <RefreshCw size={14} /> 生成报价骨架
            </button>
          </div>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table className="bb-quote-table">
              <thead>
                <tr>
                  <th>分项名称</th>
                  <th>单位</th>
                  <th>数量</th>
                  <th>单价（元）</th>
                  <th>合价（元）</th>
                  <th>备注</th>
                </tr>
              </thead>
              <tbody>
                {workspace.quoteRows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <input
                        value={row.name}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { name: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.unit}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { unit: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.quantity}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { quantity: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.unitPrice}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { unitPrice: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.amount}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { amount: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        value={row.remark}
                        onChange={(e) =>
                          updateQuoteRow(row.id, { remark: e.target.value })
                        }
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="field">
            <label>报价与偏离说明</label>
            <textarea
              rows={4}
              value={workspace.quoteNotes}
              onChange={(e) => setQuoteNotes(e.target.value)}
            />
          </div>

          <AiFeedbackPanel
            stage="business_quote"
            targetLabel="报价表与说明"
            history={history}
            presets={["备注写清是否含税", "补充「无负偏离」声明"]}
            placeholder="例如：维保单独列出备品备件…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_quote",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_quote")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：授权承诺
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "commit" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>固定格式文本可手动替换单位名称与人员。</span>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 12 }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              disabled={busy || !workspace.parseMarkdown.trim()}
              onClick={() => void runBizTask("biz_commit")}
            >
              <RefreshCw size={14} /> 生成授权承诺
            </button>
          </div>
          <div className="bb-commit-list">
            {workspace.commitBlocks.map((block) => (
              <div
                key={block.id}
                className="card card-pad"
                style={{ boxShadow: "none" }}
              >
                <div className="bb-toolbar" style={{ marginBottom: 8 }}>
                  <strong>{block.title}</strong>
                  <div className="bb-toolbar__spacer" />
                  {block.needsStamp ? (
                    <span className="badge badge-primary">需盖章/签字</span>
                  ) : (
                    <span className="badge badge-muted">正文响应</span>
                  )}
                </div>
                <textarea
                  value={block.body}
                  onChange={(e) =>
                    updateCommitBlock(block.id, { body: e.target.value })
                  }
                  aria-label={block.title}
                />
              </div>
            ))}
          </div>

          <AiFeedbackPanel
            stage="business_commit"
            targetLabel="授权与承诺正文"
            history={history}
            presets={["替换为正式公文语气", "补全授权期限与权限范围"]}
            placeholder="例如：授权委托书补上身份证号占位…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              onRevise(
                "business_commit",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              )
            }
            onRegenerate={() => void runBizTask("biz_commit")}
          />

          {nextPath && (
            <div
              className="bb-toolbar"
              style={{ marginTop: 16, marginBottom: 0 }}
            >
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：导出
              </Link>
            </div>
          )}
        </section>
      )}

      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginBottom: 16,
            }}
          >
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>
                准备导出商务标 Word
              </strong>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-secondary)",
                  fontSize: "var(--fs-sm)",
                }}
              >
                合并资格响应、目录清单、报价说明与授权承诺；使用工作区默认导出模板。
              </p>
            </div>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 0 }}>
            <Link to="/export-format" className="btn btn-ghost">
              管理模板
            </Link>
            <div className="bb-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => {
                void (async () => {
                  const t = await runBizTask("export", { mode: "business" });
                  const path = t.result?.downloadPath as string | undefined;
                  if (t.status === "success" && path) {
                    const base = getApiBase().replace(/\/$/, "");
                    window.open(`${base}${path}`, "_blank");
                  }
                })();
              }}
            >
              <Download size={16} /> {busy ? "导出中…" : "生成并下载 Word"}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
