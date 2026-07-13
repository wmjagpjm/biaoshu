/**
 * 模块：P10B 财务报价只读页
 * 用途：展示本工作空间商务标报价列表与分项明细；无编辑/导出/审批。
 * 对接：useFinanceQuotes（仅 /finance/business-bids*）；路由 /finance。
 * 二次开发：禁止调用通用项目/编辑器/设置接口；金额 null 显示「—」，不推算成本利润。
 */

import { Calculator, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useFinanceQuotes } from "../hooks/useFinanceQuotes";
import type { FinanceBusinessBidSummary, FinanceQuoteRow } from "../types";
import "./FinanceQuotePage.css";

/** 与后端 ALLOWED_STATUS 对齐的中文标签；未知码不回显英文内部状态。 */
const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  analyzing: "分析中",
  writing: "编写中",
  reviewing: "审核中",
  exported: "已导出",
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
 * 注意：不在浏览器推算成本、利润或税率。
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

/**
 * 用途：财务角色只读报价主页面。
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
            只读查看本工作空间商务标已落库报价分项与合计。不包含技术标、资格、目录、承诺、文件或设置；不支持编辑、导出与审批。
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
              <span>选择后将展示报价分项、金额合计与备注（只读）。</span>
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
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
