/**
 * 模块：P10F 标书制作者团队推荐只读面板
 * 用途：严格已认证 bid_writer 按需查看当前技术标项目最小投影。
 * 对接：fetchBidWriterTeamRecommendation；TechnicalPlanWorkspace。
 * 二次开发：仅用户点击后请求；data/error 与 requested projectId 绑定渲染；
 *   禁止仅依赖 useEffect 后置清空；禁止 /hr/*、完整项目、editor-state 回退；disabled 不挂载。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import { useAuthSession } from "../../auth/hooks/useAuthSession";
import type { HrCredentialCategory } from "../../hr/types";
import { fetchBidWriterTeamRecommendation } from "../lib/hrTeamRecommendationApi";
import type { BidWriterTeamRecommendation } from "../types";
import "./BidWriterTeamRecommendationPanel.css";

const ERR_FIXED = "暂时无法读取团队推荐";

const CATEGORY_LABELS: Record<HrCredentialCategory, string> = {
  professional: "专业资质",
  safety: "安全资质",
  performance: "业绩证明",
  other: "其他",
};

function categoryLabel(category: string): string {
  if (category in CATEGORY_LABELS) {
    return CATEGORY_LABELS[category as HrCredentialCategory];
  }
  return "—";
}

function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/** 与加载/失败时 projectId 绑定的投影结果，渲染时须比对当前 prop */
type BoundResult = {
  projectId: string;
  data: BidWriterTeamRecommendation | null;
  error: string | null;
};

/**
 * 用途：按需加载并展示团队推荐投影。
 * 对接：TechnicalPlanWorkspace（projectId）。
 * 二次开发：phase 须 authenticated 且 role 精确 bid_writer；
 *   仅展示 bound.projectId === 当前 projectId 的 data/error，防止切换帧暴露旧快照。
 */
export function BidWriterTeamRecommendationPanel({
  projectId,
}: {
  projectId: string;
}) {
  const { phase, activeMembership } = useAuthSession();
  const canView =
    phase === "authenticated" && activeMembership?.role === "bid_writer";

  const [loadingFor, setLoadingFor] = useState<string | null>(null);
  const [bound, setBound] = useState<BoundResult | null>(null);
  /** 项目切换或新请求时递增，用于丢弃过期响应 */
  const requestSeqRef = useRef(0);

  // 项目切换：作废 in-flight；不依赖本 effect 的 setState 来隐藏旧内容（渲染层已按 projectId 过滤）
  useEffect(() => {
    requestSeqRef.current += 1;
    setLoadingFor(null);
  }, [projectId]);

  const load = useCallback(async () => {
    if (!projectId || loadingFor === projectId) return;
    const requestedId = projectId;
    const seq = ++requestSeqRef.current;
    setLoadingFor(requestedId);
    // 仅清除当前请求项目的可见结果；绑定 projectId，避免跨项目写入
    setBound((prev) =>
      prev?.projectId === requestedId
        ? { projectId: requestedId, data: null, error: null }
        : prev,
    );
    try {
      const next = await fetchBidWriterTeamRecommendation(requestedId);
      if (seq !== requestSeqRef.current) return;
      setBound({ projectId: requestedId, data: next, error: null });
    } catch (err) {
      if (seq !== requestSeqRef.current) return;
      const status =
        err && typeof err === "object" && "status" in err
          ? (err as ApiError).status
          : 0;
      const message =
        status === 0
          ? "无法连接后端，团队推荐暂时不可用"
          : ERR_FIXED;
      setBound({ projectId: requestedId, data: null, error: message });
    } finally {
      if (seq === requestSeqRef.current) {
        setLoadingFor((cur) => (cur === requestedId ? null : cur));
      }
    }
  }, [projectId, loadingFor]);

  if (!canView) return null;

  // 关键守卫：只展示属于当前 projectId 的 data/error（不依赖 effect 后置清空）
  const visible =
    bound?.projectId === projectId
      ? bound
      : { projectId, data: null, error: null };
  const loading = loadingFor === projectId;
  const error = visible.error;
  const data = visible.data;

  return (
    <section
      className="bw-team"
      data-testid="bw-team-recommendation-panel"
      aria-label="团队推荐"
    >
      <div className="bw-team__head">
        <div>
          <h2 className="bw-team__title">团队推荐</h2>
          <p className="bw-team__hint">
            由人力人工维护的静态快照；点击后按需加载，非实时人员状态。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-soft btn-sm"
          data-testid="bw-team-recommendation-open"
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? "加载中…" : "查看团队推荐"}
        </button>
      </div>

      <div className="bw-team__body">
        {error ? (
          <div
            className="bw-team__alert"
            role="alert"
            data-testid="bw-team-recommendation-error"
          >
            {error}
          </div>
        ) : null}

        {!error && data?.dataState === "empty" ? (
          <div
            className="bw-team__empty"
            data-testid="bw-team-recommendation-empty"
          >
            人力尚未推荐本项目团队成员。
          </div>
        ) : null}

        {!error && data?.dataState === "ready" ? (
          <ul
            className="bw-team__list"
            data-testid="bw-team-recommendation-ready"
          >
            {data.members.map((m) => (
              <li key={`${m.order}-${m.personName}`} className="bw-team__item">
                <div>
                  {m.order}. {textOrDash(m.personName)}
                </div>
                <div className="bw-team__meta">
                  <span>{categoryLabel(m.category)}</span>
                  <span>{textOrDash(m.credentialName)}</span>
                  <span>级别 {textOrDash(m.level)}</span>
                  <span>有效期 {textOrDash(m.validUntil)}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
