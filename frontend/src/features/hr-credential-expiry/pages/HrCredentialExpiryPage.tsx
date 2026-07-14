/**
 * 模块：P10I 人员资质到期提示页
 * 用途：严格 hr 下展示服务端日期分类、固定计数与关注列表；只读无写。
 * 对接：useHrCredentialExpiry；仅 GET /hr/credential-expiry；错误固定中文脱敏。
 * 二次开发：禁止浏览器重算 state/daysRemaining；禁止展示 cardId；禁止落盘/外网/写操作。
 */

import { AlertTriangle, RefreshCw } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useHrCredentialExpiry } from "../hooks/useHrCredentialExpiry";
import type {
  HrCredentialCategory,
  HrCredentialExpiryAttentionItem,
  HrCredentialExpiryAttentionState,
} from "../types";
import "./HrCredentialExpiryPage.css";

/** 免责声明全文（契约冻结文案）。 */
export const HR_CREDENTIAL_EXPIRY_DISCLAIMER =
  "仅依据人工录入的有效期日期生成，不验证证书真实性、持证状态、适用范围或监管结论";

const CATEGORY_LABELS: Record<HrCredentialCategory, string> = {
  professional: "专业资质",
  safety: "安全类",
  performance: "业绩类",
  other: "其他",
};

const STATE_LABELS: Record<HrCredentialExpiryAttentionState, string> = {
  expired: "已过期",
  expiring_soon: "即将到期",
  missing_expiry: "缺有效期",
};

/**
 * 模块：categoryLabel
 * 用途：类别码转中文；未知值显示「—」，不回显内部码。
 * 对接：关注列表展示。
 * 二次开发：禁止原样输出未登记枚举。
 */
function categoryLabel(category: string): string {
  if (category in CATEGORY_LABELS) {
    return CATEGORY_LABELS[category as HrCredentialCategory];
  }
  return "—";
}

/**
 * 模块：stateLabel
 * 用途：服务端 state 转固定中文标签；不在客户端重算。
 * 对接：关注列表状态徽章。
 * 二次开发：禁止按本地日期推断 expired/expiring_soon。
 */
function stateLabel(state: string): string {
  if (state in STATE_LABELS) {
    return STATE_LABELS[state as HrCredentialExpiryAttentionState];
  }
  return "—";
}

function stateBadgeClass(state: string): string {
  if (state === "expired") return "hce-badge hce-badge--expired";
  if (state === "expiring_soon") return "hce-badge hce-badge--expiring";
  if (state === "missing_expiry") return "hce-badge hce-badge--missing";
  return "hce-badge";
}

/** 用途：计数安全展示；非有限数显示「—」。 */
function formatCount(value: number | null | undefined): string {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return String(Math.trunc(value));
}

/** 用途：服务端 daysRemaining 原样展示；null 为「—」。 */
function formatDaysRemaining(value: number | null | undefined): string {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return String(Math.trunc(value));
}

/** 用途：文本单元格空值占位。 */
function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/**
 * 模块：AttentionItemRow
 * 用途：渲染单条关注项；不展示 cardId；顺序由父级 map 保持。
 * 对接：attentionItems。
 * 二次开发：禁止详情/编辑/跳转按钮；禁止本地 Date 重算。
 */
function AttentionItemRow({ item }: { item: HrCredentialExpiryAttentionItem }) {
  return (
    <li className="hce-item" data-testid="hce-attention-item" data-state={item.state}>
      <div className="hce-item__head">
        <span className="hce-item__name" data-testid="hce-item-person">
          {textOrDash(item.personName)}
        </span>
        <span
          className={stateBadgeClass(item.state)}
          data-testid="hce-item-state"
        >
          {stateLabel(item.state)}
        </span>
      </div>
      <div className="hce-item__meta">
        <span>
          类别 <strong data-testid="hce-item-category">{categoryLabel(item.category)}</strong>
        </span>
        <span>
          资质{" "}
          <strong data-testid="hce-item-credential">
            {textOrDash(item.credentialName)}
          </strong>
        </span>
        <span>
          等级 <strong data-testid="hce-item-level">{textOrDash(item.level)}</strong>
        </span>
        <span>
          有效期{" "}
          <strong data-testid="hce-item-valid-until">
            {textOrDash(item.validUntil)}
          </strong>
        </span>
        <span>
          剩余天数{" "}
          <strong data-testid="hce-item-days">
            {formatDaysRemaining(item.daysRemaining)}
          </strong>
        </span>
      </div>
    </li>
  );
}

/**
 * 模块：HrCredentialExpiryPage
 * 用途：P10I 人员资质到期提示主页面。
 * 对接：useHrCredentialExpiry；RequireHr 路由门禁。
 * 二次开发：不得挂载 P10D/P10F/P10H 接口；错误不得回显后端 detail。
 */
export function HrCredentialExpiryPage() {
  const { data, loading, error, reload } = useHrCredentialExpiry();

  const items = Array.isArray(data?.attentionItems) ? data.attentionItems : [];
  /** 无启用卡（含仅停用卡）与「均在有效窗口外」必须区分，避免语义误导。 */
  const noActiveCards = data != null && data.activeTotalCount === 0;

  return (
    <div className="hce-layout" data-testid="hr-credential-expiry-page">
      <header className="page-header">
        <div>
          <h1>到期提示</h1>
          <p>
            基于当前工作空间已启用人员资质卡的有效期日期，由服务端按固定 90
            天窗口生成只读提示。不验证证件真伪、持证状态或监管结论。
          </p>
        </div>
        <div className="hce-header-actions">
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="hce-reload"
            disabled={loading}
            onClick={() => reload()}
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
      </header>

      <p className="hce-disclaimer" data-testid="hce-disclaimer">
        <AlertTriangle
          size={14}
          style={{ verticalAlign: "-2px", marginRight: 6 }}
        />
        {HR_CREDENTIAL_EXPIRY_DISCLAIMER}
      </p>

      {error ? (
        <div className="hce-alert" role="alert" data-testid="hce-error">
          {error}
        </div>
      ) : null}

      {loading ? (
        <LoadingBlock label="正在加载人员资质到期提示…" />
      ) : data ? (
        <>
          <p className="hce-meta" data-testid="hce-meta">
            <span>
              服务端基准日期 <strong data-testid="hce-as-of">{data.asOfDate}</strong>
            </span>
            <span>
              提示窗口{" "}
              <strong data-testid="hce-window-days">{data.windowDays}</strong> 天
            </span>
          </p>

          <section className="hce-panel" aria-label="到期计数">
            <div className="hce-panel__head">
              <h2 className="hce-panel__title">统计概览</h2>
            </div>
            <p className="hce-panel__hint">
              计数由服务端固定投影；「有效」仅计数不进关注列表；停用卡只计入排除数。
            </p>
            <ul className="hce-stats" data-testid="hce-stats">
              <li className="hce-stat">
                <span className="hce-stat__label">启用总数</span>
                <span className="hce-stat__value" data-testid="hce-active-total">
                  {formatCount(data.activeTotalCount)}
                </span>
              </li>
              <li className="hce-stat">
                <span className="hce-stat__label">已过期</span>
                <span className="hce-stat__value" data-testid="hce-expired-count">
                  {formatCount(data.expiredCount)}
                </span>
              </li>
              <li className="hce-stat">
                <span className="hce-stat__label">即将到期</span>
                <span
                  className="hce-stat__value"
                  data-testid="hce-expiring-count"
                >
                  {formatCount(data.expiringSoonCount)}
                </span>
              </li>
              <li className="hce-stat">
                <span className="hce-stat__label">有效</span>
                <span className="hce-stat__value" data-testid="hce-valid-count">
                  {formatCount(data.validCount)}
                </span>
              </li>
              <li className="hce-stat">
                <span className="hce-stat__label">缺有效期</span>
                <span
                  className="hce-stat__value"
                  data-testid="hce-missing-count"
                >
                  {formatCount(data.missingExpiryCount)}
                </span>
              </li>
              <li className="hce-stat">
                <span className="hce-stat__label">停用排除</span>
                <span
                  className="hce-stat__value"
                  data-testid="hce-inactive-count"
                >
                  {formatCount(data.inactiveExcludedCount)}
                </span>
              </li>
            </ul>
          </section>

          <section className="hce-panel" aria-label="关注列表">
            <div className="hce-panel__head">
              <h2 className="hce-panel__title">关注列表</h2>
            </div>
            <p className="hce-panel__hint">
              顺序与状态均以服务端为准：已过期 → 即将到期 → 缺有效期。不展示卡片
              ID，不提供编辑或自动修复。
            </p>
            {items.length === 0 ? (
              <div data-testid="hce-attention-empty">
                <EmptyState
                  icon={<AlertTriangle size={28} />}
                  title="暂无需要关注的资质到期项"
                  description={
                    noActiveCards
                      ? "当前无启用卡；停用卡已排除"
                      : "启用卡均在有效窗口外，关注列表为空。"
                  }
                />
              </div>
            ) : (
              <ul className="hce-list" data-testid="hce-attention-list">
                {items.map((item, index) => (
                  <AttentionItemRow
                    key={`${item.cardId}-${index}`}
                    item={item}
                  />
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
