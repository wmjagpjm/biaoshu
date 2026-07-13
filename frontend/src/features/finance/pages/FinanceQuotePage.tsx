/**
 * 模块：P10B/P10C 财务报价与成本草案页
 * 用途：只读报价列表/明细 + 选定商务标下的成本草案与毛利快照（非审批/非会计结论）。
 * 对接：useFinanceQuotes；useFinanceCostDraft；仅 /finance/business-bids* 与 cost 端点。
 * 二次开发：禁止调用通用项目/编辑器/设置；禁止浏览器持久化成本；金额分→元仅整数格式化。
 */

import { useEffect, useState } from "react";
import { Calculator, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import {
  useFinanceCostDraft,
  type CostFormInput,
} from "../hooks/useFinanceCostDraft";
import { useFinanceQuotes } from "../hooks/useFinanceQuotes";
import {
  formatFenAsYuan,
  formatMarginBasisPoints,
} from "../lib/financeApi";
import type {
  FinanceBusinessBidSummary,
  FinanceCostCategory,
  FinanceCostEntry,
  FinanceQuoteRow,
} from "../types";
import "./FinanceQuotePage.css";

/** 与后端 ALLOWED_STATUS 对齐的中文标签；未知码不回显英文内部状态。 */
const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  analyzing: "分析中",
  writing: "编写中",
  reviewing: "审核中",
  exported: "已导出",
};

const CATEGORY_LABELS: Record<FinanceCostCategory, string> = {
  labor: "人工",
  material: "材料",
  service: "服务",
  other: "其他",
};

const EMPTY_FORM: CostFormInput = {
  category: "material",
  name: "",
  amountYuanText: "",
  remark: "",
};

/**
 * 用途：项目状态码转中文，避免向用户泄露英文内部状态。
 * 注意：未知或空值降级为「—」，不原样输出 analyzing 等内部码。
 */
function statusLabel(status: string): string {
  if (!status) return "—";
  return STATUS_LABELS[status] ?? "—";
}

/**
 * 用途：金额本地化展示；null/非有限数值显示「—」。
 * 注意：不在浏览器推算成本、利润或税率（报价只读区沿用 P10B）。
 */
function formatAmount(value: number | null | undefined): string {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** 用途：时间本地化；无效值显示「—」。 */
function formatUpdatedAt(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 用途：文本单元格空值占位。 */
function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/** 用途：将整数分回填为「元.分」编辑文本（仅展示/编辑，不持久化）。 */
function fenToYuanText(fen: number): string {
  if (!Number.isInteger(fen) || !Number.isFinite(fen)) return "";
  const negative = fen < 0;
  const abs = fen < 0 ? -fen : fen;
  const cents = abs % 100;
  const yuan = (abs - cents) / 100;
  const text =
    cents === 0 ? String(yuan) : `${yuan}.${String(cents).padStart(2, "0")}`;
  return negative ? `-${text}` : text;
}

function ListItemButton({
  item,
  active,
  onSelect,
}: {
  item: FinanceBusinessBidSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`fq-list__item${active ? " is-active" : ""}`}
      data-testid="finance-list-item"
      data-project-id={item.projectId}
      onClick={onSelect}
    >
      <span className="fq-list__name">{item.name}</span>
      <span className="fq-list__meta">
        <span>
          行业 <strong>{textOrDash(item.industry)}</strong>
        </span>
        <span>
          状态 <strong>{statusLabel(item.status)}</strong>
        </span>
        <span>
          行数 <strong>{item.quoteRowCount}</strong>
        </span>
        <span>
          合计 <strong>{formatAmount(item.quoteTotal)}</strong>
        </span>
        <span>更新 {formatUpdatedAt(item.updatedAt)}</span>
      </span>
    </button>
  );
}

function QuoteRowsTable({ rows }: { rows: FinanceQuoteRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="fq-placeholder" data-testid="finance-rows-empty">
        <strong>暂无报价分项</strong>
        <span>该商务标尚未录入报价行，或行数据为空。</span>
      </div>
    );
  }

  return (
    <div className="fq-table-wrap">
      <table className="fq-table" data-testid="finance-rows-table">
        <thead>
          <tr>
            <th>编号</th>
            <th>名称</th>
            <th>单位</th>
            <th className="num">数量</th>
            <th className="num">单价</th>
            <th className="num">金额</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            // 编号列展示契约 id；React key 在空 id 时用序号占位，避免重复/空 key
            const rowKey = String(row.id ?? "").trim() || `row-fallback-${index}`;
            return (
              <tr key={rowKey} data-testid="finance-row">
                <td data-testid="finance-row-id">{textOrDash(row.id)}</td>
                <td>{textOrDash(row.name)}</td>
                <td>{textOrDash(row.unit)}</td>
                <td className="num">{textOrDash(row.quantity)}</td>
                <td className="num">{textOrDash(row.unitPrice)}</td>
                <td className="num">{formatAmount(row.amount)}</td>
                <td>{textOrDash(row.remark)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CostFormFields({
  form,
  disabled,
  onChange,
  idPrefix,
}: {
  form: CostFormInput;
  disabled: boolean;
  onChange: (next: CostFormInput) => void;
  idPrefix: string;
}) {
  return (
    <div className="fq-cost-form__fields">
      <label className="fq-cost-form__field" htmlFor={`${idPrefix}-category`}>
        <span>类别</span>
        <select
          id={`${idPrefix}-category`}
          data-testid={`${idPrefix}-category`}
          value={form.category}
          disabled={disabled}
          onChange={(e) =>
            onChange({
              ...form,
              category: e.target.value as FinanceCostCategory,
            })
          }
        >
          <option value="labor">人工</option>
          <option value="material">材料</option>
          <option value="service">服务</option>
          <option value="other">其他</option>
        </select>
      </label>
      <label className="fq-cost-form__field" htmlFor={`${idPrefix}-name`}>
        <span>名称</span>
        <input
          id={`${idPrefix}-name`}
          data-testid={`${idPrefix}-name`}
          type="text"
          maxLength={120}
          value={form.name}
          disabled={disabled}
          placeholder="成本项名称"
          onChange={(e) => onChange({ ...form, name: e.target.value })}
        />
      </label>
      <label className="fq-cost-form__field" htmlFor={`${idPrefix}-amount`}>
        <span>金额（元）</span>
        <input
          id={`${idPrefix}-amount`}
          data-testid={`${idPrefix}-amount`}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={form.amountYuanText}
          disabled={disabled}
          placeholder="例如 80000.50"
          onChange={(e) =>
            onChange({ ...form, amountYuanText: e.target.value })
          }
        />
      </label>
      <label className="fq-cost-form__field" htmlFor={`${idPrefix}-remark`}>
        <span>备注</span>
        <input
          id={`${idPrefix}-remark`}
          data-testid={`${idPrefix}-remark`}
          type="text"
          maxLength={500}
          value={form.remark}
          disabled={disabled}
          placeholder="可选"
          onChange={(e) => onChange({ ...form, remark: e.target.value })}
        />
      </label>
    </div>
  );
}

/**
 * 用途：成本草案 + 毛利快照面板（非已审批/最终利润/含税结论）。
 */
function CostDraftPanel({ projectId }: { projectId: string }) {
  const {
    draft,
    loading,
    error,
    submitting,
    writeError,
    clearWriteError,
    createEntry,
    updateEntry,
    removeEntry,
  } = useFinanceCostDraft(projectId);

  const [createForm, setCreateForm] = useState<CostFormInput>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<CostFormInput>(EMPTY_FORM);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // 切换项目时重置本地编辑态（不落存储）
  useEffect(() => {
    setCreateForm(EMPTY_FORM);
    setEditingId(null);
    setEditForm(EMPTY_FORM);
    setPendingDeleteId(null);
  }, [projectId]);

  const startEdit = (entry: FinanceCostEntry) => {
    clearWriteError();
    setPendingDeleteId(null);
    setEditingId(entry.id);
    setEditForm({
      category: entry.category,
      name: entry.name,
      amountYuanText: fenToYuanText(entry.amountFen),
      remark: entry.remark ?? "",
    });
  };

  const cancelEdit = () => {
    clearWriteError();
    setEditingId(null);
    setEditForm(EMPTY_FORM);
  };

  const onCreate = async () => {
    const ok = await createEntry(createForm);
    if (ok) {
      setCreateForm(EMPTY_FORM);
    }
  };

  const onSaveEdit = async () => {
    if (!editingId) return;
    const ok = await updateEntry(editingId, editForm);
    if (ok) {
      setEditingId(null);
      setEditForm(EMPTY_FORM);
    }
  };

  const onConfirmDelete = async () => {
    if (!pendingDeleteId) return;
    const targetId = pendingDeleteId;
    const ok = await removeEntry(targetId);
    if (ok) {
      setPendingDeleteId(null);
      if (editingId === targetId) {
        setEditingId(null);
        setEditForm(EMPTY_FORM);
      }
    }
  };

  const grossClass =
    draft && draft.grossProfitFen < 0
      ? " fq-cost-snapshot__value--neg"
      : "";

  return (
    <section
      className="fq-cost"
      aria-label="成本草案与毛利快照"
      data-testid="finance-cost-panel"
      data-project-id={projectId}
    >
      <div className="fq-cost__head">
        <h3 className="fq-cost__title">成本草案</h3>
        <p className="fq-cost__subtitle">
          人工维护的项目成本草案与基于当前报价的毛利快照。不是已审批结论、最终利润、含税或会计报表。
        </p>
      </div>

      {error ? (
        <div className="fq-alert" role="alert" data-testid="finance-cost-error">
          {error}
        </div>
      ) : null}

      {writeError ? (
        <div
          className="fq-alert"
          role="alert"
          data-testid="finance-cost-write-error"
        >
          {writeError}
        </div>
      ) : null}

      {loading ? (
        <LoadingBlock label="正在加载成本草案…" />
      ) : draft ? (
        <>
          <div
            className="fq-cost-snapshot"
            data-testid="finance-cost-snapshot"
          >
            <div className="fq-cost-snapshot__title">基于当前报价的毛利快照</div>
            <div className="fq-cost-snapshot__grid">
              <div className="fq-detail__field">
                <span className="fq-detail__label">报价合计</span>
                <span
                  className="fq-detail__value"
                  data-testid="finance-cost-quote-total"
                >
                  {formatFenAsYuan(draft.quoteTotalFen)}
                </span>
              </div>
              <div className="fq-detail__field">
                <span className="fq-detail__label">成本合计</span>
                <span
                  className="fq-detail__value"
                  data-testid="finance-cost-total"
                >
                  {formatFenAsYuan(draft.costTotalFen)}
                </span>
              </div>
              <div className="fq-detail__field">
                <span className="fq-detail__label">毛利金额</span>
                <span
                  className={`fq-detail__value${grossClass}`}
                  data-testid="finance-cost-gross-profit"
                >
                  {formatFenAsYuan(draft.grossProfitFen)}
                </span>
              </div>
              <div className="fq-detail__field">
                <span className="fq-detail__label">毛利率</span>
                <span
                  className="fq-detail__value"
                  data-testid="finance-cost-margin"
                >
                  {formatMarginBasisPoints(draft.grossMarginBasisPoints)}
                </span>
              </div>
            </div>
            {draft.grossProfitFen < 0 ? (
              <p
                className="fq-cost-snapshot__warn"
                data-testid="finance-cost-neg-profit-hint"
              >
                当前毛利为负：成本草案合计高于报价合计（仅为快照，非会计结论）。
              </p>
            ) : null}
          </div>

          <div className="fq-cost-entries">
            <div className="fq-cost-entries__head">
              <h4 className="fq-cost-entries__title">成本条目</h4>
            </div>
            {draft.costEntries.length === 0 ? (
              <div
                className="fq-placeholder"
                data-testid="finance-cost-empty"
              >
                <strong>暂无成本条目</strong>
                <span>可在下方新建人工、材料、服务或其他成本项。</span>
              </div>
            ) : (
              <div className="fq-table-wrap">
                <table
                  className="fq-table"
                  data-testid="finance-cost-entries-table"
                >
                  <thead>
                    <tr>
                      <th>类别</th>
                      <th>名称</th>
                      <th className="num">金额</th>
                      <th>备注</th>
                      <th>更新时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.costEntries.map((entry) => (
                      <tr
                        key={entry.id}
                        data-testid="finance-cost-entry"
                        data-entry-id={entry.id}
                      >
                        <td data-testid="finance-cost-entry-category">
                          {CATEGORY_LABELS[entry.category] ?? "—"}
                        </td>
                        <td data-testid="finance-cost-entry-name">
                          {textOrDash(entry.name)}
                        </td>
                        <td
                          className="num"
                          data-testid="finance-cost-entry-amount"
                        >
                          {formatFenAsYuan(entry.amountFen)}
                        </td>
                        <td data-testid="finance-cost-entry-remark">
                          {textOrDash(entry.remark)}
                        </td>
                        <td>{formatUpdatedAt(entry.updatedAt)}</td>
                        <td>
                          <div className="fq-cost-entry-actions">
                            <button
                              type="button"
                              className="btn btn-soft fq-cost-btn"
                              data-testid="finance-cost-edit-btn"
                              disabled={submitting}
                              onClick={() => startEdit(entry)}
                            >
                              编辑
                            </button>
                            <button
                              type="button"
                              className="btn btn-soft fq-cost-btn"
                              data-testid="finance-cost-delete-btn"
                              disabled={submitting}
                              onClick={() => {
                                clearWriteError();
                                setPendingDeleteId(entry.id);
                                setEditingId(null);
                              }}
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {pendingDeleteId ? (
            <div
              className="fq-cost-confirm"
              data-testid="finance-cost-delete-confirm"
            >
              <p>确认删除该成本条目？删除后将重新加载草案。</p>
              <div className="fq-cost-confirm__actions">
                <button
                  type="button"
                  className="btn btn-soft"
                  data-testid="finance-cost-delete-cancel"
                  disabled={submitting}
                  onClick={() => setPendingDeleteId(null)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="btn"
                  data-testid="finance-cost-delete-confirm-btn"
                  disabled={submitting}
                  onClick={() => void onConfirmDelete()}
                >
                  {submitting ? "删除中…" : "确认删除"}
                </button>
              </div>
            </div>
          ) : null}

          {editingId ? (
            <div
              className="fq-cost-form"
              data-testid="finance-cost-edit-form"
            >
              <div className="fq-cost-form__title">编辑成本条目</div>
              <CostFormFields
                form={editForm}
                disabled={submitting}
                onChange={(next) => {
                  clearWriteError();
                  setEditForm(next);
                }}
                idPrefix="finance-cost-edit"
              />
              <div className="fq-cost-form__actions">
                <button
                  type="button"
                  className="btn btn-soft"
                  data-testid="finance-cost-edit-cancel"
                  disabled={submitting}
                  onClick={cancelEdit}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="btn"
                  data-testid="finance-cost-edit-submit"
                  disabled={submitting}
                  onClick={() => void onSaveEdit()}
                >
                  {submitting ? "保存中…" : "保存修改"}
                </button>
              </div>
            </div>
          ) : (
            <div
              className="fq-cost-form"
              data-testid="finance-cost-create-form"
            >
              <div className="fq-cost-form__title">新建成本条目</div>
              <CostFormFields
                form={createForm}
                disabled={submitting}
                onChange={(next) => {
                  clearWriteError();
                  setCreateForm(next);
                }}
                idPrefix="finance-cost-create"
              />
              <div className="fq-cost-form__actions">
                <button
                  type="button"
                  className="btn"
                  data-testid="finance-cost-create-submit"
                  disabled={submitting}
                  onClick={() => void onCreate()}
                >
                  {submitting ? "提交中…" : "创建成本条目"}
                </button>
              </div>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}

/**
 * 用途：财务角色报价主页面（含成本草案）。
 */
export function FinanceQuotePage() {
  const {
    items,
    listLoading,
    listError,
    selectedId,
    detail,
    detailLoading,
    detailError,
    refreshList,
    selectProject,
  } = useFinanceQuotes();

  return (
    <div className="page fq-layout" data-testid="finance-quote-page">
      <header className="page-header">
        <div>
          <h1>财务报价</h1>
          <p>
            只读查看本工作空间商务标已落库报价分项与合计；选定项目后可维护成本草案并查看基于当前报价的毛利快照。不包含技术标、资格、目录、承诺、文件或设置；报价本身不支持编辑、导出与审批。
          </p>
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn btn-soft"
            data-testid="finance-refresh"
            onClick={() => void refreshList()}
            disabled={listLoading}
          >
            <RefreshCw size={16} />
            {listLoading ? "刷新中…" : "刷新列表"}
          </button>
        </div>
      </header>

      <div className="fq-grid">
        <section className="fq-panel" aria-label="报价列表">
          <div className="fq-panel__head">
            <h2 className="fq-panel__title">商务标列表</h2>
            <p className="fq-panel__hint">点击查看明细</p>
          </div>

          {listError ? (
            <div className="fq-alert" role="alert" data-testid="finance-list-error">
              {listError}
            </div>
          ) : null}

          {listLoading ? (
            <LoadingBlock label="正在加载财务报价列表…" />
          ) : items.length === 0 && !listError ? (
            <EmptyState
              icon={<Calculator size={28} />}
              title="暂无商务标报价"
              description="当前工作空间没有可查看的商务标项目，或项目尚未写入报价数据。"
            />
          ) : items.length === 0 ? null : (
            <ul className="fq-list" data-testid="finance-list">
              {items.map((item) => (
                <li key={item.projectId}>
                  <ListItemButton
                    item={item}
                    active={selectedId === item.projectId}
                    onSelect={() => selectProject(item.projectId)}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="fq-panel" aria-label="报价明细">
          <div className="fq-panel__head">
            <h2 className="fq-panel__title">报价明细</h2>
            {detail ? (
              <p className="fq-panel__hint" data-testid="finance-detail-name">
                {detail.name}
              </p>
            ) : null}
          </div>

          {!selectedId ? (
            <div className="fq-placeholder" data-testid="finance-detail-placeholder">
              <strong>请选择左侧项目</strong>
              <span>选择后将展示报价分项、金额合计与备注（只读），并可维护成本草案。</span>
            </div>
          ) : detailLoading ? (
            <LoadingBlock label="正在加载报价明细…" />
          ) : detailError ? (
            <div
              className="fq-alert"
              role="alert"
              data-testid="finance-detail-error"
            >
              {detailError}
            </div>
          ) : detail ? (
            <div data-testid="finance-detail">
              <div className="fq-detail__summary">
                <div className="fq-detail__field">
                  <span className="fq-detail__label">项目名称</span>
                  <span className="fq-detail__value">{detail.name}</span>
                </div>
                <div className="fq-detail__field">
                  <span className="fq-detail__label">行业</span>
                  <span className="fq-detail__value">
                    {textOrDash(detail.industry)}
                  </span>
                </div>
                <div className="fq-detail__field">
                  <span className="fq-detail__label">状态</span>
                  <span
                    className="fq-detail__value"
                    data-testid="finance-detail-status"
                  >
                    {statusLabel(detail.status)}
                  </span>
                </div>
                <div className="fq-detail__field">
                  <span className="fq-detail__label">更新时间</span>
                  <span className="fq-detail__value">
                    {formatUpdatedAt(detail.updatedAt)}
                  </span>
                </div>
                <div className="fq-detail__field">
                  <span className="fq-detail__label">报价行数</span>
                  <span className="fq-detail__value">{detail.quoteRowCount}</span>
                </div>
                <div className="fq-detail__field">
                  <span className="fq-detail__label">报价合计</span>
                  <span
                    className="fq-detail__value fq-detail__value--total"
                    data-testid="finance-quote-total"
                  >
                    {formatAmount(detail.quoteTotal)}
                  </span>
                </div>
              </div>

              <QuoteRowsTable rows={detail.quoteRows ?? []} />

              <div>
                <div className="fq-detail__label" style={{ marginBottom: 6 }}>
                  报价备注
                </div>
                <p
                  className={`fq-notes${
                    detail.quoteNotes?.trim() ? "" : " fq-notes--empty"
                  }`}
                  data-testid="finance-quote-notes"
                >
                  {detail.quoteNotes?.trim()
                    ? detail.quoteNotes
                    : "（无备注）"}
                </p>
              </div>

              {/* 仅当明细与当前选中项目一致时挂载，避免切换瞬间用旧明细发起新项目成本请求 */}
              {detail.projectId === selectedId ? (
                <CostDraftPanel projectId={detail.projectId} />
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
