/**
 * 模块：P10F 人力团队推荐页
 * 用途：严格 hr 下为技术标项目组装有序成员快照；无导出/AI/审批。
 * 对接：useHrTeamRecommendations；初始仅 projects/资质摘要；详情与 PUT 后摘要重读。
 * 二次开发：禁止预取 remark/团队摘要；停用卡不可静默保存；错误固定中文脱敏。
 */

import { Users } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import type { HrCredentialCategory } from "../../hr/types";
import { useHrTeamRecommendations } from "../hooks/useHrTeamRecommendations";
import "./HrTeamRecommendationsPage.css";

/** 类别中文标签；不直接展示内部英文枚举。 */
const CATEGORY_LABELS: Record<HrCredentialCategory, string> = {
  professional: "专业资质",
  safety: "安全资质",
  performance: "业绩证明",
  other: "其他",
};

/** 用途：类别码转中文。 */
function categoryLabel(category: string): string {
  if (category in CATEGORY_LABELS) {
    return CATEGORY_LABELS[category as HrCredentialCategory];
  }
  return "—";
}

/** 用途：文本空值占位。 */
function textOrDash(value: string | null | undefined): string {
  if (value == null) return "—";
  const t = String(value).trim();
  return t ? t : "—";
}

/**
 * 用途：P10F 团队推荐主页面。
 * 对接：RequireHr 路由 /hr/team-recommendations。
 * 二次开发：保持仅 React 内存；保存后服务端重读。
 */
export function HrTeamRecommendationsPage() {
  const {
    projects,
    activeCards,
    summaries,
    bootLoading,
    bootError,
    selectedProjectId,
    detail,
    detailEmpty,
    detailLoading,
    detailError,
    selectedCardIds,
    inactiveSnapshotIds,
    inactiveLabels,
    submitting,
    writeError,
    selectProject,
    toggleSelectCard,
    removeSelectedCard,
    clearInactiveSnapshot,
    clearWriteError,
    reloadBoot,
    saveRecommendation,
  } = useHrTeamRecommendations();

  const summaryMap = new Map(summaries.map((s) => [s.projectId, s]));

  return (
    <div className="htr-layout" data-testid="hr-team-page">
      <header className="page-header">
        <div>
          <h1>团队推荐</h1>
          <p>
            为当前工作空间的技术标项目人工组装人员资质推荐快照。仅使用已启用的资质卡摘要；不含备注、证件号或联系方式。这不是 AI 匹配或审批结论。
          </p>
        </div>
        <div className="htr-actions">
          <button
            type="button"
            className="btn btn-soft btn-sm"
            data-testid="hr-team-reload"
            disabled={bootLoading || submitting}
            onClick={() => {
              clearWriteError();
              reloadBoot();
            }}
          >
            刷新
          </button>
        </div>
      </header>

      {bootError ? (
        <div className="htr-alert" role="alert" data-testid="hr-team-boot-error">
          {bootError}
        </div>
      ) : null}

      {writeError ? (
        <div
          className="htr-alert"
          role="alert"
          data-testid="hr-team-write-error"
        >
          {writeError}
        </div>
      ) : null}

      {inactiveSnapshotIds.length > 0 ? (
        <div
          className="htr-alert htr-alert--warn"
          role="status"
          data-testid="hr-team-inactive-warning"
        >
          <div>
            当前快照中含已停用或不可用成员，不能静默保存其编号：
            {inactiveLabels.join("、") || "已停用成员"}。请先移除后再保存。
          </div>
          <div className="htr-actions">
            <button
              type="button"
              className="btn btn-soft btn-sm"
              data-testid="hr-team-remove-inactive"
              disabled={submitting}
              onClick={() => clearInactiveSnapshot()}
            >
              移除已停用成员提示
            </button>
          </div>
        </div>
      ) : null}

      {bootLoading ? (
        <LoadingBlock label="正在加载团队推荐…" />
      ) : (
        <div className="htr-grid">
          <section className="htr-panel" aria-label="技术标项目">
            <h2 className="htr-panel__title">技术标项目</h2>
            <p className="htr-panel__hint">
              仅显示 id/名称。选择项目后才加载该项目推荐详情。
            </p>
            {projects.length === 0 ? (
              <EmptyState
                icon={<Users size={28} />}
                title="暂无技术标项目"
                description="当前空间没有可供推荐的技术标项目。"
              />
            ) : (
              <ul className="htr-list" data-testid="hr-team-project-list">
                {projects.map((p) => {
                  const sum = summaryMap.get(p.id);
                  return (
                    <li key={p.id}>
                      <button
                        type="button"
                        className={`htr-list__item${
                          selectedProjectId === p.id ? " is-active" : ""
                        }`}
                        data-testid={`hr-team-project-${p.id}`}
                        onClick={() => selectProject(p.id)}
                      >
                        <span className="htr-list__name">{p.name}</span>
                        <span className="htr-list__meta">
                          {sum
                            ? `已推荐 ${sum.memberCount} 人`
                            : "尚未组装"}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="htr-panel" aria-label="可选人员资质">
            <h2 className="htr-panel__title">可选有效资质卡</h2>
            <p className="htr-panel__hint">
              仅 isActive=true 可选；按点击顺序加入推荐。不加载备注。
            </p>
            {activeCards.length === 0 ? (
              <div className="htr-placeholder" data-testid="hr-team-cards-empty">
                <strong>暂无可用资质卡</strong>
                <span>请先在「人员资质」中登记并启用素材卡。</span>
              </div>
            ) : (
              <ul className="htr-list" data-testid="hr-team-card-list">
                {activeCards.map((c) => {
                  const picked = selectedCardIds.includes(c.id);
                  const order = picked
                    ? selectedCardIds.indexOf(c.id) + 1
                    : 0;
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        className={`htr-list__item${picked ? " is-picked" : ""}`}
                        data-testid={`hr-team-card-${c.id}`}
                        disabled={!selectedProjectId || submitting}
                        onClick={() => toggleSelectCard(c.id)}
                      >
                        <span className="htr-list__name">
                          {textOrDash(c.personName)}
                          {picked ? (
                            <span className="htr-badge">顺序 {order}</span>
                          ) : null}
                        </span>
                        <span className="htr-list__meta">
                          <span>{categoryLabel(c.category)}</span>
                          <span>{textOrDash(c.credentialName)}</span>
                          <span>{textOrDash(c.level)}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="htr-panel" aria-label="推荐编排与详情">
            <h2 className="htr-panel__title">推荐编排</h2>
            {!selectedProjectId ? (
              <div
                className="htr-placeholder"
                data-testid="hr-team-project-unselected"
              >
                <strong>尚未选择项目</strong>
                <span>请先在左侧选择技术标项目。</span>
              </div>
            ) : detailLoading ? (
              <LoadingBlock label="正在加载推荐详情…" />
            ) : detailError ? (
              <div
                className="htr-alert"
                role="alert"
                data-testid="hr-team-detail-error"
              >
                {detailError}
              </div>
            ) : (
              <>
                {detailEmpty ? (
                  <div
                    className="htr-placeholder"
                    data-testid="hr-team-detail-empty"
                  >
                    <strong>尚未组装推荐</strong>
                    <span>
                      该项目还没有团队推荐快照。请从有效资质卡中按顺序挑选后保存。
                    </span>
                  </div>
                ) : detail ? (
                  <div data-testid="hr-team-detail">
                    <p className="htr-panel__hint">
                      项目「{detail.projectName}」· 上次更新{" "}
                      {textOrDash(detail.updatedAt)} · 服务端成员{" "}
                      {detail.members.length} 人（以下为当前编辑选择）
                    </p>
                  </div>
                ) : null}

                <h3 className="htr-panel__title">当前选择顺序</h3>
                {selectedCardIds.length === 0 ? (
                  <div
                    className="htr-placeholder"
                    data-testid="hr-team-selected-empty"
                  >
                    <strong>尚未选择成员</strong>
                    <span>点击中间列表中的有效卡按顺序加入。</span>
                  </div>
                ) : (
                  <ol
                    className="htr-order"
                    data-testid="hr-team-selected-order"
                  >
                    {selectedCardIds.map((id, idx) => {
                      const card = activeCards.find((c) => c.id === id);
                      return (
                        <li key={id} className="htr-order__item">
                          <div className="htr-order__left">
                            <span>
                              {idx + 1}.{" "}
                              {textOrDash(card?.personName ?? id)}
                            </span>
                            <span className="htr-list__meta">
                              {categoryLabel(card?.category ?? "other")} ·{" "}
                              {textOrDash(card?.credentialName)}
                            </span>
                          </div>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            data-testid={`hr-team-remove-${id}`}
                            disabled={submitting}
                            onClick={() => removeSelectedCard(id)}
                          >
                            移除
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                )}

                <div className="htr-actions">
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="hr-team-save"
                    disabled={submitting || !selectedProjectId}
                    onClick={() => void saveRecommendation()}
                  >
                    {submitting ? "保存中…" : "保存推荐"}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
