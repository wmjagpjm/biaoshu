/**
 * 模块：P10J 财务个人成本变更记录页
 * 用途：严格 finance 下展示本人成功成本条目新增/修改/删除记录；只读无写。
 * 对接：useFinanceCostChangeEvents；仅 GET /finance/cost-change-events；错误固定中文脱敏。
 * 二次开发：禁止回退报价/cost-draft/projects；禁止筛选分页导出详情跳转；禁止浏览器存储。
 */

import { AlertTriangle, History, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useFinanceCostChangeEvents } from "../hooks/useFinanceCostChangeEvents";
import type {
  FinanceCostChangeAction,
  FinanceCostChangeEventItem,
} from "../types";
import "./FinanceCostChangeEventsPage.css";

/** 限制声明全文（契约冻结文案）。 */
export const FINANCE_COST_CHANGE_EVENTS_DISCLAIMER =
  "只记录当前账户在当前工作空间成功的成本条目新增、修改、删除；不是完整财务审计，不能还原项目、金额、内容、变更前后值或失败尝试";

const ACTION_LABELS: Record<FinanceCostChangeAction, string> = {
  create: "新增成本条目",
  update: "修改成本条目",
  delete: "删除成本条目",
};

/**
 * 模块：actionLabel
 * 用途：动作码转固定中文；未知值显示「—」，不回显内部码。
 * 对接：列表展示。
 * 二次开发：禁止原样输出未登记枚举或内部 finance_cost_*。
 */
function actionLabel(action: string): string {
  if (action in ACTION_LABELS) {
    return ACTION_LABELS[action as FinanceCostChangeAction];
  }
  return "—";
}

/**
 * 模块：formatOccurredAt
 * 用途：服务端 occurredAt 仅做安全中文时间显示；非法时间固定「时间未知」。
 * 对接：列表时间列。
 * 二次开发：不得用本地时间重排或推导业务；不得因非法时间影响列表顺序。
 */
function formatOccurredAt(value: string | null | undefined): string {
  if (value == null) return "时间未知";
  const raw = String(value).trim();
  if (!raw) return "时间未知";
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return "时间未知";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(ms));
  } catch {
    return "时间未知";
  }
}

/**
 * 模块：EventItemRow
 * 用途：渲染单条变更记录；顺序由父级 map 保持服务端倒序。
 * 对接：items。
 * 二次开发：禁止详情/编辑/跳转按钮；禁止展示金额/项目/备注。
 */
function EventItemRow({ item }: { item: FinanceCostChangeEventItem }) {
  return (
    <li className="fcc-item" data-testid="fcc-item" data-action={item.action}>
      <div className="fcc-item__head">
        <span className="fcc-item__action" data-testid="fcc-item-action">
          {actionLabel(item.action)}
        </span>
      </div>
      <div className="fcc-item__meta">
        <span>
          条目编号{" "}
          <strong data-testid="fcc-item-entry-id">{item.entryId}</strong>
        </span>
        <span>
          发生时间{" "}
          <strong data-testid="fcc-item-time">
            {formatOccurredAt(item.occurredAt)}
          </strong>
        </span>
      </div>
    </li>
  );
}

/**
 * 模块：FinanceCostChangeEventsPage
 * 用途：P10J 财务个人成本变更记录主页面。
 * 对接：useFinanceCostChangeEvents；RequireFinance 路由门禁。
 * 二次开发：不得挂载 P10B/P10C 接口；错误不得回显后端 detail。
 */
export function FinanceCostChangeEventsPage() {
  const { data, loading, error, reload } = useFinanceCostChangeEvents();

  const items = Array.isArray(data?.items) ? data.items : [];

  return (
    <div className="fcc-layout" data-testid="finance-cost-change-events-page">
      <header className="page-header">
        <div>
          <h1>我的成本记录</h1>
          <p>
            展示当前账户在当前工作空间成功的成本条目新增、修改、删除记录。仅服务端固定投影，不是完整财务审计。
          </p>
        </div>
        <div className="fcc-header-actions">
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="fcc-reload"
            disabled={loading}
            onClick={() => reload()}
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
      </header>

      <p className="fcc-disclaimer" data-testid="fcc-disclaimer">
        <AlertTriangle
          size={14}
          style={{ verticalAlign: "-2px", marginRight: 6 }}
        />
        {FINANCE_COST_CHANGE_EVENTS_DISCLAIMER}
      </p>

      {error ? (
        <div className="fcc-alert" role="alert" data-testid="fcc-error">
          {error}
        </div>
      ) : null}

      {loading ? (
        <LoadingBlock label="正在加载我的成本记录…" />
      ) : data ? (
        <section className="fcc-panel" aria-label="成本变更列表">
          <div className="fcc-panel__head">
            <h2 className="fcc-panel__title">最近变更</h2>
          </div>
          <p className="fcc-panel__hint">
            顺序以服务端为准（时间倒序）。仅显示动作、条目编号与发生时间；无筛选、搜索、分页或导出。
          </p>
          {items.length === 0 ? (
            <div data-testid="fcc-empty">
              <EmptyState
                icon={<History size={28} />}
                title="暂无成本变更记录"
                description="当前账户在本工作空间尚无成功的成本条目新增、修改或删除记录。没有记录不等于没有发生，也不代表完整审计结论。"
              />
            </div>
          ) : (
            <ul className="fcc-list" data-testid="fcc-list">
              {items.map((item, index) => (
                <EventItemRow
                  key={`${item.entryId}-${item.occurredAt}-${index}`}
                  item={item}
                />
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}
