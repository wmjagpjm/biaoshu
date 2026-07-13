/**
 * 模块：标讯页
 * 用途：浏览、筛选和维护本地标讯库；展示国能 e 招受控追踪面板并支持人工加入本地标讯。
 * 对接：useOpportunities；/api/opportunities；/api/opportunity-watch/*；技术标工作区。
 * 二次开发：前端只访问本机 /api；外链仅用后端 announcementUrl；不得恢复 mock 或直连国能站点。
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FolderPlus,
  LoaderCircle,
  Newspaper,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import {
  opportunityToDraft,
  useOpportunities,
} from "../hooks/useOpportunities";
import type {
  BidOppStatus,
  BidOpportunity,
  BidOpportunityDraft,
  OpportunityImportResult,
} from "../types";
import {
  BID_STATUS_LABEL,
  WATCH_EXTRACTION_LABEL,
  WATCH_RUN_STATUS_LABEL,
} from "../types";
import "./BidOpportunity.css";

type StatusFilter = BidOppStatus | "all";
type EditingState = {
  opportunity?: BidOpportunity;
  draft: BidOpportunityDraft;
};

function updateDraft(
  draft: BidOpportunityDraft,
  key: keyof BidOpportunityDraft,
  value: string,
) {
  return { ...draft, [key]: value };
}

export function BidOpportunityPage() {
  const navigate = useNavigate();
  const {
    items,
    loading,
    saving,
    error,
    refresh,
    save,
    remove,
    importOpportunities,
    createProject,
    watchDashboard,
    watchLoading,
    watchError,
    watchBusy,
    watchSyncing,
    activeWatchRun,
    watchImportResult,
    refreshWatchDashboard,
    importWatchPlans,
    startWatchSync,
    acceptWatchHit,
  } = useOpportunities();
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState("全部");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [selectedImportFile, setSelectedImportFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<OpportunityImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [watchPlanFile, setWatchPlanFile] = useState<File | null>(null);

  const regions = useMemo(
    () => ["全部", ...Array.from(new Set(items.map((item) => item.region))).sort()],
    [items],
  );
  const list = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((opportunity) => {
      if (region !== "全部" && opportunity.region !== region) return false;
      if (status !== "all" && opportunity.status !== status) return false;
      if (!normalizedQuery) return true;
      return [
        opportunity.title,
        opportunity.buyer,
        opportunity.summary,
        opportunity.tags.join(" "),
      ]
        .join("\n")
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [items, query, region, status]);

  const runStatus =
    activeWatchRun?.status ?? watchDashboard?.latestRun?.status ?? null;
  const syncDisabled = watchBusy || watchSyncing
    || runStatus === "queued"
    || runStatus === "running";

  const closeEditor = () => {
    if (!saving) setEditing(null);
  };

  const closeImport = () => {
    if (!saving) setIsImporting(false);
  };

  const submitEditor = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    try {
      await save(editing.draft, editing.opportunity?.id);
      setEditing(null);
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  const deleteOpportunity = async (opportunity: BidOpportunity) => {
    if (!window.confirm(`删除标讯「${opportunity.title}」？已创建的项目会保留。`)) {
      return;
    }
    try {
      await remove(opportunity.id);
      if (expandedId === opportunity.id) setExpandedId(null);
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  const startProject = async (opportunity: BidOpportunity) => {
    try {
      const project = await createProject(opportunity.id);
      navigate(`/technical-plan/${project.id}/document`);
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  const submitImport = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedImportFile) return;
    setImportResult(null);
    setImportError(null);
    try {
      const result = await importOpportunities(selectedImportFile);
      setImportResult(result);
    } catch (reason) {
      setImportError((reason as { message?: string }).message || "导入标讯失败");
    }
  };

  const submitWatchImport = async () => {
    if (!watchPlanFile) return;
    try {
      await importWatchPlans(watchPlanFile);
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  const submitWatchSync = async () => {
    try {
      await startWatchSync();
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  const submitAcceptHit = async (hitId: string) => {
    try {
      await acceptWatchHit(hitId);
    } catch {
      /* 错误已由 Hook 回显。 */
    }
  };

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>标讯</h1>
          <p>工作空间内的本地标讯线索。</p>
        </div>
        <div className="opp-header-actions">
          <button
            type="button"
            className="btn btn-soft"
            disabled={saving}
            onClick={() => {
              setSelectedImportFile(null);
              setImportResult(null);
              setImportError(null);
              setIsImporting(true);
            }}
          >
            <Upload size={16} /> 导入标讯
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={saving}
            onClick={() =>
              setEditing({ draft: opportunityToDraft(), opportunity: undefined })
            }
          >
            <Plus size={16} /> 新增标讯
          </button>
        </div>
      </header>

      <section
        className="card card-pad opp-watch"
        aria-labelledby="opp-watch-title"
        data-testid="opportunity-watch-panel"
      >
        <div className="opp-watch__head">
          <div>
            <h2 id="opp-watch-title">国能 e 招计划追踪</h2>
            <p className="opp-watch__disclaimer">
              国能 e 招候选公告，需人工确认；不会自动创建项目
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={watchBusy || watchLoading}
            onClick={() => {
              void refreshWatchDashboard().catch(() => undefined);
            }}
          >
            <RefreshCw size={14} /> 刷新面板
          </button>
        </div>

        <div className="opp-watch__controls">
          <label className="field opp-watch__file">
            <span>招标计划表（.xlsx）</span>
            <input
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              disabled={watchBusy}
              data-testid="watch-plan-file"
              onChange={(event) => {
                setWatchPlanFile(event.currentTarget.files?.[0] ?? null);
              }}
            />
            {watchPlanFile && <small>{watchPlanFile.name}</small>}
          </label>
          <div className="opp-watch__actions">
            <button
              type="button"
              className="btn btn-soft"
              disabled={watchBusy || !watchPlanFile}
              data-testid="watch-plan-import"
              onClick={() => void submitWatchImport()}
            >
              {watchBusy && !watchSyncing ? (
                <LoaderCircle className="opp-spin" size={16} />
              ) : (
                <Upload size={16} />
              )}
              上传计划表
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={syncDisabled}
              data-testid="watch-sync"
              onClick={() => void submitWatchSync()}
            >
              {watchSyncing || runStatus === "queued" || runStatus === "running" ? (
                <LoaderCircle className="opp-spin" size={16} />
              ) : (
                <RefreshCw size={16} />
              )}
              同步国能 e 招
            </button>
          </div>
        </div>

        {watchImportResult && (
          <div className="opp-watch__import-result" role="status" data-testid="watch-import-result">
            导入 {watchImportResult.inserted} 条，跳过 {watchImportResult.skipped} 条，共{" "}
            {watchImportResult.total} 条
          </div>
        )}

        <div className="opp-watch__stats" aria-live="polite">
          <span data-testid="watch-plan-count">
            计划数 <strong>{watchLoading ? "…" : watchDashboard?.planCount ?? 0}</strong>
          </span>
          <span data-testid="watch-run-status">
            最近运行{" "}
            <strong>
              {watchSyncing || runStatus === "queued" || runStatus === "running"
                ? "正在同步"
                : runStatus
                  ? WATCH_RUN_STATUS_LABEL[runStatus]
                  : "尚无运行"}
            </strong>
          </span>
          {watchDashboard?.latestRun && (
            <span data-testid="watch-run-resolved">
              已解析 <strong>{watchDashboard.latestRun.resolvedCount}</strong>
              {" / 待复核 "}
              <strong>{watchDashboard.latestRun.needsReviewCount}</strong>
            </span>
          )}
        </div>

        {watchError && (
          <div className="opp-error" role="alert" data-testid="watch-error">
            <span>{watchError}</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => void refreshWatchDashboard().catch(() => undefined)}
            >
              重试
            </button>
          </div>
        )}

        <div className="opp-watch__hits" data-testid="watch-hit-list">
          {(watchDashboard?.hits ?? []).length === 0 ? (
            <p className="opp-watch__empty">
              {watchLoading ? "载入追踪结果…" : "暂无命中公告。上传计划表并同步后显示。"}
            </p>
          ) : (
            (watchDashboard?.hits ?? []).map((hit) => {
              const canAccept =
                hit.extractionStatus === "resolved" && !hit.acceptedOpportunityId;
              return (
                <article
                  key={hit.id}
                  className="opp-watch-hit"
                  data-testid={`watch-hit-${hit.id}`}
                  data-extraction-status={hit.extractionStatus}
                >
                  <div className="opp-watch-hit__top">
                    <h3 className="opp-watch-hit__title">{hit.title}</h3>
                    <span
                      className={`opp-watch-hit__badge is-${hit.extractionStatus}`}
                      data-testid={`watch-hit-status-${hit.id}`}
                    >
                      {WATCH_EXTRACTION_LABEL[hit.extractionStatus]}
                    </span>
                  </div>
                  <div className="opp-watch-hit__meta">
                    <span>
                      投标截止{" "}
                      <strong data-testid={`watch-hit-deadline-${hit.id}`}>
                        {hit.deadlineAtLocal
                          ? `${hit.deadlineAtLocal}（北京时间）`
                          : "未解析"}
                      </strong>
                    </span>
                    <span>
                      开标时间{" "}
                      <strong>
                        {hit.openingAtLocal
                          ? `${hit.openingAtLocal}（北京时间）`
                          : "未解析"}
                      </strong>
                    </span>
                  </div>
                  <div className="opp-watch-hit__actions">
                    {hit.announcementUrl ? (
                      <a
                        className="btn btn-ghost btn-sm"
                        href={hit.announcementUrl}
                        target="_blank"
                        rel="noreferrer"
                        data-testid={`watch-hit-link-${hit.id}`}
                      >
                        打开公告
                      </a>
                    ) : (
                      <span className="opp-watch-hit__no-link">无可用公告链接</span>
                    )}
                    {canAccept && (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        disabled={watchBusy}
                        data-testid={`watch-hit-accept-${hit.id}`}
                        onClick={() => void submitAcceptHit(hit.id)}
                      >
                        加入本地标讯
                      </button>
                    )}
                    {hit.acceptedOpportunityId && (
                      <span
                        className="opp-watch-hit__accepted"
                        data-testid={`watch-hit-accepted-${hit.id}`}
                      >
                        已加入本地标讯
                      </span>
                    )}
                  </div>
                </article>
              );
            })
          )}
        </div>
      </section>

      <div className="card card-pad opp-filters">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="opp-q">关键词</label>
          <div style={{ position: "relative" }}>
            <Search
              size={16}
              style={{
                position: "absolute",
                left: 12,
                top: 14,
                color: "var(--text-tertiary)",
              }}
            />
            <input
              id="opp-q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="标题、采购人、标签…"
              style={{ paddingLeft: 36 }}
            />
          </div>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="opp-region">地区</label>
          <select
            id="opp-region"
            value={region}
            onChange={(event) => setRegion(event.target.value)}
          >
            {regions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>
        <div className="opp-count" aria-live="polite">
          {loading ? "载入中" : `共 ${list.length} 条`}
        </div>
      </div>

      <div className="opp-chips" role="group" aria-label="状态筛选">
        {(
          [
            ["all", "全部状态"],
            ["open", "进行中"],
            ["closing_soon", "即将截止"],
            ["closed", "已截止"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`opp-chip${status === key ? " is-active" : ""}`}
            onClick={() => setStatus(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="opp-error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void refresh()}>
            重试
          </button>
        </div>
      )}

      {!loading && list.length === 0 ? (
        <EmptyState
          icon={<Newspaper size={32} />}
          title="没有匹配标讯"
          description="调整筛选条件，或录入一条新的本地标讯。"
        />
      ) : (
        <div className="opp-list" aria-busy={loading} data-testid="local-opportunity-list">
          {list.map((opportunity) => {
            const isExpanded = expandedId === opportunity.id;
            return (
              <article
                key={opportunity.id}
                className="opp-card"
                data-testid={`local-opp-${opportunity.id}`}
              >
                <div className="opp-card__top">
                  <h2 className="opp-card__title">{opportunity.title}</h2>
                  <span className={`opp-status is-${opportunity.status}`}>
                    {BID_STATUS_LABEL[opportunity.status]}
                  </span>
                </div>
                <div className="opp-card__meta">
                  <span>采购人 <strong>{opportunity.buyer || "未填写"}</strong></span>
                  <span>地区 <strong>{opportunity.region}</strong></span>
                  <span>预算 <strong>{opportunity.budgetLabel || "未填写"}</strong></span>
                  <span>截止 <strong>{opportunity.deadline}</strong></span>
                </div>
                <div className="opp-card__tags">
                  {opportunity.tags.map((tag) => (
                    <span key={tag} className="badge badge-muted">{tag}</span>
                  ))}
                </div>
                {isExpanded && <p className="opp-card__summary">{opportunity.summary || "暂无摘要"}</p>}
                <div className="opp-card__actions">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() =>
                      setExpandedId((current) =>
                        current === opportunity.id ? null : opportunity.id,
                      )
                    }
                  >
                    {isExpanded ? "收起摘要" : "展开摘要"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={opportunity.status === "closed" || saving}
                    title={opportunity.status === "closed" ? "已截止，仅可查看" : "创建技术方案项目"}
                    onClick={() => void startProject(opportunity)}
                  >
                    <FolderPlus size={14} /> 创建技术方案项目
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm opp-icon-button"
                    aria-label={`编辑：${opportunity.title}`}
                    title="编辑标讯"
                    disabled={saving}
                    onClick={() => setEditing({ opportunity, draft: opportunityToDraft(opportunity) })}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm opp-icon-button"
                    aria-label={`删除：${opportunity.title}`}
                    title="删除标讯"
                    disabled={saving}
                    onClick={() => void deleteOpportunity(opportunity)}
                  >
                    <Trash2 size={15} />
                  </button>
                  <span className="opp-card__source">{opportunity.sourceLabel}</span>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {editing && (
        <div className="opp-modal-mask" onMouseDown={closeEditor}>
          <form
            className="opp-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="opp-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submitEditor}
          >
            <div className="opp-modal__head">
              <div>
                <h2 id="opp-modal-title">{editing.opportunity ? "编辑标讯" : "新增标讯"}</h2>
                <p>截止状态会按服务端日期自动计算。</p>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm opp-icon-button"
                aria-label="关闭"
                title="关闭"
                disabled={saving}
                onClick={closeEditor}
              >
                <X size={16} />
              </button>
            </div>
            <div className="opp-modal__fields">
              <label className="field">
                <span>标讯标题</span>
                <input required value={editing.draft.title} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "title", event.target.value) })} />
              </label>
              <label className="field">
                <span>采购人</span>
                <input value={editing.draft.buyer} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "buyer", event.target.value) })} />
              </label>
              <label className="field">
                <span>地区</span>
                <input value={editing.draft.region} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "region", event.target.value) })} />
              </label>
              <label className="field">
                <span>预算文案</span>
                <input value={editing.draft.budgetLabel} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "budgetLabel", event.target.value) })} />
              </label>
              <label className="field">
                <span>截止日期</span>
                <input required type="date" value={editing.draft.deadline} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "deadline", event.target.value) })} />
              </label>
              <label className="field">
                <span>标签</span>
                <input value={editing.draft.tagsText} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "tagsText", event.target.value) })} placeholder="以逗号分隔" />
              </label>
              <label className="field opp-modal__full">
                <span>摘要</span>
                <textarea value={editing.draft.summary} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "summary", event.target.value) })} />
              </label>
              <label className="field opp-modal__full">
                <span>来源说明</span>
                <input value={editing.draft.sourceLabel} onChange={(event) => setEditing((current) => current && { ...current, draft: updateDraft(current.draft, "sourceLabel", event.target.value) })} />
              </label>
            </div>
            <div className="opp-modal__actions">
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={closeEditor}>取消</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? <LoaderCircle className="opp-spin" size={16} /> : null}
                保存标讯
              </button>
            </div>
          </form>
        </div>
      )}

      {isImporting && (
        <div className="opp-modal-mask" onMouseDown={closeImport}>
          <form
            className="opp-modal opp-import"
            role="dialog"
            aria-modal="true"
            aria-labelledby="opp-import-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submitImport}
          >
            <div className="opp-modal__head">
              <div>
                <h2 id="opp-import-title">导入标讯</h2>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm opp-icon-button"
                aria-label="关闭"
                title="关闭"
                disabled={saving}
                onClick={closeImport}
              >
                <X size={16} />
              </button>
            </div>
            <label className="field opp-import__file">
              <span>本机文件</span>
              <input
                type="file"
                accept=".csv,.json,text/csv,application/json"
                disabled={saving}
                data-testid="local-import-file"
                onChange={(event) => {
                  setSelectedImportFile(event.currentTarget.files?.[0] ?? null);
                  setImportResult(null);
                  setImportError(null);
                }}
              />
              {selectedImportFile && <small>{selectedImportFile.name}</small>}
            </label>
            {importError && <div className="opp-import__error" role="alert">{importError}</div>}
            {importResult && (
              <div className="opp-import__result" role="status">
                导入 {importResult.inserted} 条，跳过 {importResult.skipped} 条，共 {importResult.total} 条
              </div>
            )}
            <div className="opp-modal__actions">
              <button type="button" className="btn btn-ghost" disabled={saving} onClick={closeImport}>
                取消
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving || !selectedImportFile}>
                {saving ? <LoaderCircle className="opp-spin" size={16} /> : <Upload size={16} />}
                导入
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
