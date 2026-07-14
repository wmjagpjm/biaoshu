/**
 * 模块：P10E 投标人匿名合规预览页
 * 用途：严格 bidder 下展示匿名汇总四计数与覆盖率；说明非评审结论/非投标结果。
 * 对接：useBidderCompliancePreview；formatCoverageBasisPoints；仅 /bidder/compliance-preview。
 * 二次开发：禁止展示项目名/ID/源文/备注/矩阵行；失败固定脱敏文案；禁止浏览器持久化。
 */

import { RefreshCw, ShieldCheck } from "lucide-react";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useBidderCompliancePreview } from "../hooks/useBidderCompliancePreview";
import { formatCoverageBasisPoints } from "../lib/bidderComplianceApi";
import "./BidderCompliancePreviewPage.css";

/** 用途：计数安全展示；非有限数显示「—」。 */
function formatCount(value: number | null | undefined): string {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return String(Math.trunc(value));
}

/**
 * 模块：BidderCompliancePreviewPage
 * 用途：P10E 匿名合规预览主页面。
 * 对接：RequireBidder 路由；AppShell 投标人导航。
 * 二次开发：不得在此页发起项目/财务/人力/设置请求。
 */
export function BidderCompliancePreviewPage() {
  const { data, loading, error, reload } = useBidderCompliancePreview();

  const summary = data?.summary;
  const isEmpty = data?.dataState === "empty";
  const isReady = data?.dataState === "ready";

  return (
    <div className="bc-layout" data-testid="bidder-compliance-page">
      <header className="page-header">
        <div>
          <h1>合规预览</h1>
          <p>
            当前工作空间响应矩阵的匿名合规汇总。仅显示覆盖准备度统计，不暴露项目、章节、源文或备注。
          </p>
        </div>
        <div>
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="bidder-reload"
            disabled={loading}
            onClick={() => reload()}
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
      </header>

      <p
        className="bc-disclaimer"
        data-testid="bidder-compliance-disclaimer"
      >
        <ShieldCheck size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
        本页仅为匿名合规准备度汇总，不是评审结论或投标结果，亦不构成法律意见或废标判定。
      </p>

      {error ? (
        <div
          className="bc-alert"
          role="alert"
          data-testid="bidder-compliance-error"
        >
          {error}
        </div>
      ) : null}

      <section className="bc-panel" aria-label="匿名合规汇总">
        <div className="bc-panel__head">
          <h2 className="bc-panel__title">匿名汇总</h2>
        </div>
        <p className="bc-panel__hint">
          数据来自当前空间技术标响应矩阵收敛结果；豁免项不计入覆盖率分母。
        </p>

        {loading ? (
          <LoadingBlock label="正在加载匿名合规预览…" />
        ) : error ? null : data ? (
          <>
            {isEmpty ? (
              <p className="bc-empty" data-testid="bidder-compliance-empty">
                当前暂无响应矩阵条目，覆盖率暂不可计算。
              </p>
            ) : null}
            {isReady ? (
              <span
                data-testid="bidder-compliance-ready"
                style={{
                  position: "absolute",
                  width: 1,
                  height: 1,
                  padding: 0,
                  margin: -1,
                  overflow: "hidden",
                  clip: "rect(0, 0, 0, 0)",
                  whiteSpace: "nowrap",
                  border: 0,
                }}
              >
                已加载匿名汇总
              </span>
            ) : null}
            <ul className="bc-stats">
              <li className="bc-stat">
                <span className="bc-stat__label">总条目</span>
                <span
                  className="bc-stat__value"
                  data-testid="bidder-total-items"
                >
                  {formatCount(summary?.totalItems)}
                </span>
              </li>
              <li className="bc-stat">
                <span className="bc-stat__label">已覆盖</span>
                <span
                  className="bc-stat__value"
                  data-testid="bidder-covered-items"
                >
                  {formatCount(summary?.coveredItems)}
                </span>
              </li>
              <li className="bc-stat">
                <span className="bc-stat__label">未覆盖</span>
                <span
                  className="bc-stat__value"
                  data-testid="bidder-uncovered-items"
                >
                  {formatCount(summary?.uncoveredItems)}
                </span>
              </li>
              <li className="bc-stat">
                <span className="bc-stat__label">已豁免</span>
                <span
                  className="bc-stat__value"
                  data-testid="bidder-waived-items"
                >
                  {formatCount(summary?.waivedItems)}
                </span>
              </li>
              <li className="bc-stat bc-stat--coverage">
                <span className="bc-stat__label">覆盖率</span>
                <span className="bc-stat__value" data-testid="bidder-coverage">
                  {formatCoverageBasisPoints(summary?.coverageBasisPoints)}
                </span>
              </li>
            </ul>
          </>
        ) : null}
      </section>
    </div>
  );
}
