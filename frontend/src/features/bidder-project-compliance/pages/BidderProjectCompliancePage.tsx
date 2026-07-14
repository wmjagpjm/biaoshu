/**
 * 模块：P10G 投标人项目级合规统计页
 * 用途：严格 bidder 下先选技术标，再展示单项目五项统计；说明非评审结论。
 * 对接：useBidderProjectCompliance；formatCoverageBasisPoints；仅 project-compliance API。
 * 二次开发：禁止展示矩阵原文/源文/备注；失败固定脱敏；禁止浏览器持久化与 URL 查询参数。
 */

import { RefreshCw, ShieldCheck } from "lucide-react";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useBidderProjectCompliance } from "../hooks/useBidderProjectCompliance";
import { formatCoverageBasisPoints } from "../lib/bidderProjectComplianceApi";
import "./BidderProjectCompliancePage.css";

/** 用途：计数安全展示；非有限数显示「—」。 */
function formatCount(value: number | null | undefined): string {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return String(Math.trunc(value));
}

/**
 * 模块：BidderProjectCompliancePage
 * 用途：P10G 项目合规统计主页面。
 * 对接：RequireBidder 路由；AppShell 投标人「项目合规」导航。
 * 二次开发：不得在此页发起 P10E 聚合、项目/财务/人力/设置请求。
 */
export function BidderProjectCompliancePage() {
  const {
    projects,
    listLoading,
    listError,
    selectedId,
    detail,
    detailError,
    detailLoading,
    selectProject,
    clearSelection,
    reloadProjects,
  } = useBidderProjectCompliance();

  const summary = detail?.summary;
  const isEmpty = detail?.dataState === "empty";
  const isReady = detail?.dataState === "ready";
  const hasSelection = selectedId != null && selectedId !== "";

  return (
    <div className="bpc-layout" data-testid="bidder-project-compliance-page">
      <header className="page-header">
        <div>
          <h1>项目合规</h1>
          <p>
            按技术标项目查看响应矩阵合规准备度统计。仅显示计数与覆盖率，不暴露矩阵原文、章节、源文或备注。
          </p>
        </div>
        <div>
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="bpc-reload-projects"
            disabled={listLoading}
            onClick={() => void reloadProjects()}
          >
            <RefreshCw size={14} />
            刷新列表
          </button>
        </div>
      </header>

      <p className="bpc-disclaimer" data-testid="bpc-disclaimer">
        <ShieldCheck size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
        本页仅为单项目合规准备度统计，不是评审结论、投标结果或废标判定，亦不构成法律意见。
      </p>

      {listError ? (
        <div className="bpc-alert" role="alert" data-testid="bpc-list-error">
          {listError}
        </div>
      ) : null}

      <section className="bpc-panel" aria-label="技术标项目选择">
        <div className="bpc-panel__head">
          <h2 className="bpc-panel__title">选择技术标项目</h2>
        </div>
        <p className="bpc-panel__hint">
          仅列出当前工作空间技术标名称；选择后才会加载该项目统计，不会请求工作空间匿名汇总。
        </p>

        {listLoading ? (
          <LoadingBlock label="正在加载技术标项目列表…" />
        ) : listError ? null : projects.length === 0 ? (
          <p className="bpc-empty" data-testid="bpc-projects-empty">
            当前空间暂无技术标项目。
          </p>
        ) : (
          <div className="bpc-select-row">
            <label className="bpc-sr-only" htmlFor="bpc-project-select">
              技术标项目
            </label>
            <select
              id="bpc-project-select"
              className="bpc-select"
              data-testid="bpc-project-select"
              value={selectedId ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                if (!v) {
                  clearSelection();
                  return;
                }
                selectProject(v);
              }}
            >
              <option value="">请选择项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </section>

      <section className="bpc-panel" aria-label="单项目合规统计">
        <div className="bpc-panel__head">
          <h2 className="bpc-panel__title">合规统计</h2>
        </div>
        <p className="bpc-panel__hint">
          口径与匿名合规预览一致：豁免项不计入覆盖率分母；未知状态按未覆盖计入。
        </p>

        {!hasSelection ? (
          <p className="bpc-idle" data-testid="bpc-detail-idle">
            请先选择技术标项目以查看合规统计。
          </p>
        ) : detailLoading ? (
          <LoadingBlock label="正在加载项目合规统计…" />
        ) : detailError ? (
          <div className="bpc-alert" role="alert" data-testid="bpc-detail-error">
            {detailError}
          </div>
        ) : detail ? (
          <>
            {isEmpty ? (
              <p className="bpc-empty" data-testid="bpc-detail-empty">
                该项目暂无响应矩阵条目，覆盖率暂不可计算。
              </p>
            ) : null}
            {isReady ? (
              <span className="bpc-sr-only" data-testid="bpc-detail-ready">
                已加载项目合规统计
              </span>
            ) : null}
            <ul className="bpc-stats" data-testid="bpc-detail-stats">
              <li className="bpc-stat">
                <span className="bpc-stat__label">总条目</span>
                <span className="bpc-stat__value" data-testid="bpc-total-items">
                  {formatCount(summary?.totalItems)}
                </span>
              </li>
              <li className="bpc-stat">
                <span className="bpc-stat__label">已覆盖</span>
                <span
                  className="bpc-stat__value"
                  data-testid="bpc-covered-items"
                >
                  {formatCount(summary?.coveredItems)}
                </span>
              </li>
              <li className="bpc-stat">
                <span className="bpc-stat__label">未覆盖</span>
                <span
                  className="bpc-stat__value"
                  data-testid="bpc-uncovered-items"
                >
                  {formatCount(summary?.uncoveredItems)}
                </span>
              </li>
              <li className="bpc-stat">
                <span className="bpc-stat__label">已豁免</span>
                <span className="bpc-stat__value" data-testid="bpc-waived-items">
                  {formatCount(summary?.waivedItems)}
                </span>
              </li>
              <li className="bpc-stat bpc-stat--coverage">
                <span className="bpc-stat__label">覆盖率</span>
                <span className="bpc-stat__value" data-testid="bpc-coverage">
                  {formatCoverageBasisPoints(summary?.coverageBasisPoints)}
                </span>
              </li>
            </ul>
          </>
        ) : (
          <p className="bpc-idle" data-testid="bpc-detail-idle">
            请先选择技术标项目以查看合规统计。
          </p>
        )}
      </section>
    </div>
  );
}
