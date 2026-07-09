import { Link, Navigate, useParams } from "react-router-dom";
import {
  CheckCircle2,
  Download,
  Info,
  RefreshCw,
  Upload,
} from "lucide-react";
import { AiFeedbackPanel } from "../../../shared/components/AiFeedbackPanel/AiFeedbackPanel";
import {
  BusinessStepStepper,
  BUSINESS_STEPS,
} from "../components/BusinessStepStepper";
import { useBusinessBidWorkspace } from "../hooks/useBusinessBidWorkspace";
import { mockBusinessProjects } from "../mock";
import type { BusinessBidStepId, QualifyItemStatus } from "../types";
import "./BusinessBid.css";

/**
 * 模块：商务标分步工作区
 * 用途：可交互走完六步 mock；各步支持「人工反馈 → AI 定向调整」。
 * 对接：生成/解析任务后端接入后替换按钮 disabled 与 mock 写入逻辑。
 */

const STEP_IDS: BusinessBidStepId[] = BUSINESS_STEPS.map((s) => s.id);

function qualifyStatusLabel(s: QualifyItemStatus): string {
  if (s === "matched") return "已响应";
  if (s === "partial") return "待确认";
  if (s === "missing") return "缺材料";
  return "待处理";
}

function nextStepPath(projectId: string, active: BusinessBidStepId): string | null {
  const idx = STEP_IDS.indexOf(active);
  if (idx < 0 || idx >= STEP_IDS.length - 1) return null;
  return `/business-bid/${projectId}/${STEP_IDS[idx + 1]}`;
}

export function BusinessBidWorkspace() {
  const { projectId = "", step } = useParams<{
    projectId: string;
    step?: string;
  }>();

  const project =
    mockBusinessProjects.find((p) => p.id === projectId) ??
    mockBusinessProjects[0];

  const {
    workspace,
    history,
    setParseMarkdown,
    updateQualifyItem,
    toggleTocItem,
    updateQuoteRow,
    setQuoteNotes,
    updateCommitBlock,
    submitRevise,
  } = useBusinessBidWorkspace(project.id);

  if (!step) {
    const defaultStep =
      STEP_IDS[Math.max(0, project.currentStep - 1)] ?? "parse";
    return <Navigate to={`/business-bid/${project.id}/${defaultStep}`} replace />;
  }

  if (!STEP_IDS.includes(step as BusinessBidStepId)) {
    return <Navigate to={`/business-bid/${project.id}/parse`} replace />;
  }

  const active = step as BusinessBidStepId;
  const nextPath = nextStepPath(project.id, active);

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
          </p>
        </div>
        <div className="page-actions">
          <Link to="/business-bid" className="btn btn-ghost">
            项目列表
          </Link>
          {project.linkedTechnicalProjectId && (
            <Link
              to={`/technical-plan/${project.linkedTechnicalProjectId}`}
              className="btn btn-soft"
            >
              打开关联技术标
            </Link>
          )}
        </div>
      </header>

      <BusinessStepStepper
        projectId={project.id}
        active={active}
        doneUntil={project.currentStep}
      />

      {/* —— 1. 条款解析 —— */}
      {active === "parse" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              识别资格条件、付款/保证金、有效期等商务条款。复杂扫描件可走
              <Link to="/local-parser" style={{ margin: "0 4px", textDecoration: "underline" }}>
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
                <p>支持 PDF / DOCX。</p>
                <button type="button" className="btn btn-primary">
                  选择文件
                </button>
              </div>
              <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span className="file-chip">招标文件-正式稿.pdf</span>
                <span className="badge badge-primary">商务条款已抽取</span>
              </div>
            </div>
            <div>
              <div className="bb-toolbar">
                <strong>解析预览（可编辑）</strong>
                <div className="bb-toolbar__spacer" />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  title="后端：重新跑解析"
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
            placeholder="例如：社保人数要求识别成 10 人，应为 15 人；请按 PDF 第 8 页修正…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "business_parse",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

          {nextPath && (
            <div className="bb-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：资格响应
              </Link>
            </div>
          )}
        </section>
      )}

      {/* —— 2. 资格响应 —— */}
      {active === "qualify" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              对照资格要求逐条填写响应说明与证明材料索引。当前待确认/缺材料：
              <strong> {missingQualify} </strong>
              条。可手动修改，也可在下方填写修改意见后修订。
            </span>
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
              "证明材料命名对齐附件清单编号",
            ]}
            placeholder="例如：第 4 条社保人数按 15 人重写响应，并给出附件命名建议…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "business_qualify",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

          {nextPath && (
            <div className="bb-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：目录清单
              </Link>
            </div>
          )}
        </section>
      )}

      {/* —— 3. 目录清单 —— */}
      {active === "toc" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              勾选拟递交材料。已勾选 {checkedCount}/{workspace.tocItems.length}。
              「商务资料清单整理」只做勾选；本步可继续进入报价与正文生成。
            </span>
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
                    <div style={{ fontSize: 12, color: "var(--warning)", marginTop: 4 }}>
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
            presets={[
              "按招标目录顺序重排",
              "合并重复的资格证明项",
              "补充偏离表与报价表分项",
              "标出必须原件的材料",
            ]}
            placeholder="例如：增加「项目团队社保证明」；联合体协议标为不适用…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "business_toc",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

          {nextPath && (
            <div className="bb-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：报价说明
              </Link>
            </div>
          )}
        </section>
      )}

      {/* —— 4. 报价说明 —— */}
      {active === "quote" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              分项报价表。金额可手改；合价与税率以后端计算为准。
            </span>
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
            presets={[
              "合并实施与培训为一行",
              "备注写清是否含税",
              "补充「无负偏离」声明",
              "分项命名更贴近招标清单",
            ]}
            placeholder="例如：维保单独列出备品备件；总价备注增加税率说明…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "business_quote",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

          {nextPath && (
            <div className="bb-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：授权承诺
              </Link>
            </div>
          )}
        </section>
      )}

      {/* —— 5. 授权承诺 —— */}
      {active === "commit" && (
        <section className="card card-pad">
          <div className="bb-hint">
            <Info size={16} />
            <span>
              固定格式文本可手动替换单位名称与人员；标「需盖章」的块导出时会预留签章区。
            </span>
          </div>
          <div className="bb-commit-list">
            {workspace.commitBlocks.map((block) => (
              <div key={block.id} className="card card-pad" style={{ boxShadow: "none" }}>
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
            presets={[
              "替换为正式公文语气",
              "补全授权期限与权限范围",
              "诚信承诺增加串标禁止表述",
              "商务响应与付款条款对齐",
            ]}
            placeholder="例如：授权委托书补上身份证号占位；承诺书增加虚假材料责任条款…"
            onRevise={({ message, preserveStructure, targetId, targetLabel }) =>
              submitRevise({
                stage: "business_commit",
                message,
                preserveStructure,
                targetId,
                targetLabel,
              })
            }
            onRegenerate={() => undefined}
          />

          {nextPath && (
            <div className="bb-toolbar" style={{ marginTop: 16, marginBottom: 0 }}>
              <div className="bb-toolbar__spacer" />
              <Link to={nextPath} className="btn btn-primary">
                下一步：导出
              </Link>
            </div>
          )}
        </section>
      )}

      {/* —— 6. 导出 —— */}
      {active === "export" && (
        <section className="card card-pad" style={{ maxWidth: 720 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <CheckCircle2 size={28} color="var(--success)" />
            <div>
              <strong style={{ fontSize: "var(--fs-lg)" }}>准备导出商务标 Word</strong>
              <p
                style={{
                  margin: "4px 0 0",
                  color: "var(--text-secondary)",
                  fontSize: "var(--fs-sm)",
                }}
              >
                合并资格响应、目录清单、报价说明与授权承诺；版式使用导出模板（对齐 C 端
                ExportFormatConfig）。
              </p>
            </div>
          </div>
          <div className="field" style={{ marginBottom: 14 }}>
            <label>导出模板</label>
            <select defaultValue="gov-standard">
              <option value="gov-standard">政务投标通用（宋体标题）</option>
              <option value="enterprise">企业方案风</option>
              <option value="custom">自定义（见模板设置）</option>
            </select>
          </div>
          <div className="bb-toolbar" style={{ marginBottom: 0 }}>
            <Link to="/export-format" className="btn btn-ghost">
              管理模板
            </Link>
            <div className="bb-toolbar__spacer" />
            <button
              type="button"
              className="btn btn-primary"
              onClick={() =>
                window.alert("导出功能待后端接入后可用。")
              }
            >
              <Download size={16} /> 生成并下载 Word
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
