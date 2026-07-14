/**
 * 模块：模板/卡片融合建议对话框（阶段3 M3-A/M3-B/M3-C）
 * 用途：多选模板与 active 文本卡片、目标章节，发起 content_fuse 只读建议；
 *      M3-B 双栏差异预览与确认写入；M3-C 最近成功批次一次性、漂移安全撤销。
 * 对接：useProjectPipeline.runTask("content_fuse")；editors.replaceChapterBody；
 *      /api/templates；/api/cards；TechnicalPlanWorkspace 编写步入口。
 * 二次开发：未确认/关闭/取消/base 漂移不得写章节；撤销快照仅 Dialog 实例内存且一次消费；
 *       禁止新增 API/存储/历史栈；禁止改矩阵/大纲。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Sparkles, X } from "lucide-react";
import { apiFetch } from "../../../shared/lib/api";
import { listCards } from "../../knowledge-base/api/cardsApi";
import type { KnowledgeCardSummary } from "../../knowledge-base/types";
import { CARD_TYPE_LABEL } from "../../knowledge-base/types";
import type { BidTemplateSummary } from "../../bid-templates/types";
import type { PipelineTask } from "../hooks/useProjectPipeline";
import type { ChapterContent } from "../types";
import {
  buildAppliedChapterBody,
  buildContentFusePayload,
  CONTENT_FUSE_LIMITS,
  formatFuseQuotaTip,
  formatFuseSourceRefLabel,
  matchFuseSuggestionBase,
  normalizeContentFuseResult,
  type ContentFuseResult,
  type ContentFuseSuggestion,
} from "../lib/contentFuse";

export type ContentFuseDialogProps = {
  open: boolean;
  projectId: string;
  chapters: ChapterContent[];
  busy: boolean;
  onClose: () => void;
  /** 用途：发起 content_fuse 任务；父级传入 runTask，便于代次/取消统一。 */
  onRun: (payload: Record<string, unknown>) => Promise<PipelineTask>;
  onCancelTask: () => Promise<PipelineTask | null>;
  /**
   * 用途：M3-B 确认写入 / M3-C 撤销恢复时调用既有 replaceChapterBody；无专用 PUT。
   * 对接：useTechnicalPlanEditors.replaceChapterBody。
   * 二次开发：第三参数仅撤销时传入原 status；写入路径保持两参数。
   */
  onReplaceChapterBody: (
    chapterId: string,
    body: string,
    originalStatus?: ChapterContent["status"],
  ) => void;
};

const TEXT_TYPES = new Set(["document", "qualification", "performance"]);

type ApplyOutcome = {
  suggestionId: string;
  status: "applied" | "skipped";
  reason?: string;
};

/**
 * 用途：最近一次成功确认写入批次的最小章节快照（仅 Dialog 实例内存）。
 * 对接：handleConfirmApply 建立；handleUndoBatch 校验后恢复。
 * 二次开发：禁止持久化；不得保存模板/卡片/模型原文或密钥类字段。
 */
type BatchUndoChapterSnapshot = {
  chapterId: string;
  title: string;
  beforeBody: string;
  beforeStatus: ChapterContent["status"];
  afterBody: string;
  afterStatus: ChapterContent["status"];
  suggestionIds: string[];
};

type BatchUndoSnapshot = {
  chapters: BatchUndoChapterSnapshot[];
};

function toggleId(
  list: string[],
  id: string,
  max: number,
): { next: string[]; error: string | null } {
  if (list.includes(id)) {
    return { next: list.filter((x) => x !== id), error: null };
  }
  if (list.length >= max) {
    return { next: list, error: `最多选择 ${max} 项` };
  }
  return { next: [...list, id], error: null };
}

function findChapter(
  chapters: ChapterContent[],
  id: string,
): ChapterContent | undefined {
  return chapters.find((c) => c.id === id);
}

/**
 * 用途：单条建议是否可勾选/写入；空正文永远不可。
 */
function canSelectSuggestion(
  suggestion: ContentFuseSuggestion,
  chapters: ChapterContent[],
  appliedIds: Set<string>,
): { selectable: boolean; reason: string | null } {
  if (appliedIds.has(suggestion.suggestionId)) {
    return { selectable: false, reason: "已写入" };
  }
  if (!(suggestion.proposedMarkdown || "").length) {
    return { selectable: false, reason: "建议正文为空，不可写入" };
  }
  const chapter = findChapter(chapters, suggestion.targetChapterId);
  const match = matchFuseSuggestionBase(chapter, suggestion.base);
  if (!match.ok) {
    return { selectable: false, reason: match.reason };
  }
  return { selectable: true, reason: null };
}

/**
 * 用途：M3-A/M3-B/M3-C 融合入口 UI；建议预览、确认写入与最近批次撤销。
 */
export function ContentFuseDialog({
  open,
  projectId,
  chapters,
  busy,
  onClose,
  onRun,
  onCancelTask,
  onReplaceChapterBody,
}: ContentFuseDialogProps) {
  const [templates, setTemplates] = useState<BidTemplateSummary[]>([]);
  const [cards, setCards] = useState<KnowledgeCardSummary[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [templateIds, setTemplateIds] = useState<string[]>([]);
  const [cardIds, setCardIds] = useState<string[]>([]);
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [result, setResult] = useState<ContentFuseResult | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [appliedIds, setAppliedIds] = useState<string[]>([]);
  const [applyOutcomes, setApplyOutcomes] = useState<ApplyOutcome[]>([]);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [undoMessage, setUndoMessage] = useState<string | null>(null);
  const [undoSnapshot, setUndoSnapshot] = useState<BatchUndoSnapshot | null>(
    null,
  );
  const sessionRef = useRef(0);

  const appliedSet = useMemo(() => new Set(appliedIds), [appliedIds]);

  useEffect(() => {
    if (!open) return;
    sessionRef.current += 1;
    setTemplateIds([]);
    setCardIds([]);
    setTargetIds([]);
    setLocalError(null);
    setRunMessage(null);
    setResult(null);
    setSourceError(null);
    setSelectedIds([]);
    setAppliedIds([]);
    setApplyOutcomes([]);
    setApplyMessage(null);
    setUndoMessage(null);
    setUndoSnapshot(null);
    setLoadingSources(true);
    let cancelled = false;
    void Promise.all([
      apiFetch<BidTemplateSummary[]>("/templates?status=active"),
      listCards({ status: "active" }),
    ])
      .then(([tpl, cardList]) => {
        if (cancelled) return;
        setTemplates(Array.isArray(tpl) ? tpl : []);
        setCards(
          (Array.isArray(cardList) ? cardList : []).filter((c) =>
            TEXT_TYPES.has(c.type),
          ),
        );
      })
      .catch((err) => {
        if (!cancelled) {
          setSourceError(
            (err as { message?: string }).message || "加载模板/卡片失败",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSources(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  // 实时基线变化时，清掉已不可选的勾选
  useEffect(() => {
    if (!result) return;
    setSelectedIds((prev) =>
      prev.filter((id) => {
        const s = result.suggestions.find((x) => x.suggestionId === id);
        if (!s) return false;
        return canSelectSuggestion(s, chapters, appliedSet).selectable;
      }),
    );
  }, [chapters, result, appliedSet]);

  const payload = useMemo(
    () =>
      buildContentFusePayload({
        templateIds,
        cardIds,
        targetChapterIds: targetIds,
      }),
    [templateIds, cardIds, targetIds],
  );

  const quotaTip = formatFuseQuotaTip(payload);
  const canSubmit =
    !busy &&
    !loadingSources &&
    payload.templateIds.length + payload.cardIds.length >= 1 &&
    payload.templateIds.length + payload.cardIds.length <=
      CONTENT_FUSE_LIMITS.maxSourcesTotal &&
    payload.targetChapterIds.length >= 1 &&
    payload.targetChapterIds.length <= CONTENT_FUSE_LIMITS.maxTargets;

  const handleRun = useCallback(async () => {
    const session = sessionRef.current;
    setLocalError(null);
    setRunMessage("正在生成只读融合建议…");
    setResult(null);
    setSelectedIds([]);
    setAppliedIds([]);
    setApplyOutcomes([]);
    setApplyMessage(null);
    setUndoMessage(null);
    setUndoSnapshot(null);
    try {
      const task = await onRun(payload);
      if (session !== sessionRef.current) return;
      if (task.status === "cancelled") {
        setRunMessage("已取消");
        return;
      }
      if (task.status === "failed") {
        setLocalError(task.error || task.message || "融合任务失败");
        setRunMessage(null);
        return;
      }
      const normalized = normalizeContentFuseResult(
        (task.result as Record<string, unknown> | null) ?? null,
      );
      setResult(normalized);
      setRunMessage(
        normalized
          ? `已生成 ${normalized.suggestions.length} 条只读建议（默认不写入，需勾选确认）`
          : "任务完成但无建议",
      );
    } catch (err) {
      if (session !== sessionRef.current) return;
      setLocalError((err as { message?: string }).message || "请求失败");
      setRunMessage(null);
    }
  }, [onRun, payload]);

  const toggleSuggestion = useCallback(
    (suggestion: ContentFuseSuggestion, checked: boolean) => {
      const gate = canSelectSuggestion(suggestion, chapters, appliedSet);
      if (checked && !gate.selectable) return;
      setSelectedIds((prev) => {
        if (checked) {
          if (prev.includes(suggestion.suggestionId)) return prev;
          return [...prev, suggestion.suggestionId];
        }
        return prev.filter((id) => id !== suggestion.suggestionId);
      });
      setApplyMessage(null);
    },
    [chapters, appliedSet],
  );

  /**
   * 用途：确认写入所选；点击瞬间再校验 base；部分成功允许；至少一条成功则建撤销快照。
   * 对接：onReplaceChapterBody → debounce PUT editor-state（既有路径）。
   * 二次开发：多建议同章保留最早 before 与最终 after；无成功写入不得建快照。
   */
  const handleConfirmApply = useCallback(() => {
    if (!result) return;
    const outcomes: ApplyOutcome[] = [];
    const newlyApplied: string[] = [];
    const batchMap = new Map<string, BatchUndoChapterSnapshot>();
    let appliedCount = 0;
    let skippedCount = 0;

    for (const suggestionId of selectedIds) {
      const suggestion = result.suggestions.find(
        (s) => s.suggestionId === suggestionId,
      );
      if (!suggestion) {
        outcomes.push({
          suggestionId,
          status: "skipped",
          reason: "建议不存在",
        });
        skippedCount += 1;
        continue;
      }
      if (appliedSet.has(suggestion.suggestionId)) {
        outcomes.push({
          suggestionId,
          status: "skipped",
          reason: "已写入，跳过重复",
        });
        skippedCount += 1;
        continue;
      }
      if (!(suggestion.proposedMarkdown || "").length) {
        outcomes.push({
          suggestionId,
          status: "skipped",
          reason: "建议正文为空，不可写入",
        });
        skippedCount += 1;
        continue;
      }
      const chapter = findChapter(chapters, suggestion.targetChapterId);
      const match = matchFuseSuggestionBase(chapter, suggestion.base);
      if (!match.ok) {
        outcomes.push({
          suggestionId,
          status: "skipped",
          reason: match.reason,
        });
        skippedCount += 1;
        continue;
      }
      const beforeBody = chapter?.body || "";
      const nextBody = buildAppliedChapterBody(
        suggestion.action,
        beforeBody,
        suggestion.proposedMarkdown,
      );
      if (nextBody == null) {
        outcomes.push({
          suggestionId,
          status: "skipped",
          reason: "建议正文为空，不可写入",
        });
        skippedCount += 1;
        continue;
      }
      const beforeStatus: ChapterContent["status"] =
        chapter?.status ?? "pending";
      const afterStatus: ChapterContent["status"] = nextBody.trim()
        ? "needs_review"
        : beforeStatus;
      onReplaceChapterBody(suggestion.targetChapterId, nextBody);
      const existing = batchMap.get(suggestion.targetChapterId);
      if (existing) {
        existing.afterBody = nextBody;
        existing.afterStatus = afterStatus;
        existing.suggestionIds.push(suggestion.suggestionId);
      } else {
        batchMap.set(suggestion.targetChapterId, {
          chapterId: suggestion.targetChapterId,
          title: chapter?.title ?? "",
          beforeBody,
          beforeStatus,
          afterBody: nextBody,
          afterStatus,
          suggestionIds: [suggestion.suggestionId],
        });
      }
      outcomes.push({ suggestionId, status: "applied" });
      newlyApplied.push(suggestion.suggestionId);
      appliedCount += 1;
    }

    if (newlyApplied.length) {
      setAppliedIds((prev) => [...new Set([...prev, ...newlyApplied])]);
      setSelectedIds((prev) => prev.filter((id) => !newlyApplied.includes(id)));
      setUndoSnapshot({ chapters: [...batchMap.values()] });
      setUndoMessage(null);
    }
    setApplyOutcomes(outcomes);
    setApplyMessage(
      `已写入 ${appliedCount} 条，跳过 ${skippedCount} 条` +
        (appliedCount > 0 ? "（已沿用编辑器自动保存）" : ""),
    );
  }, [
    result,
    selectedIds,
    appliedSet,
    chapters,
    onReplaceChapterBody,
  ]);

  /**
   * 用途：撤销最近成功批次；仅标题/正文/状态均等于 after 的章恢复 before。
   * 对接：onReplaceChapterBody(id, beforeBody, beforeStatus)。
   * 二次开发：点击后无条件消费快照；仅移除成功恢复章的已写入建议 ID；漂移章保留已写入标记。
   */
  const handleUndoBatch = useCallback(() => {
    if (!undoSnapshot) return;
    let restored = 0;
    let skipped = 0;
    const restoredSuggestionIds: string[] = [];

    for (const snap of undoSnapshot.chapters) {
      const current = findChapter(chapters, snap.chapterId);
      const bodyMatch = (current?.body || "") === snap.afterBody;
      const titleMatch = (current?.title ?? "") === snap.title;
      const statusMatch = (current?.status ?? "pending") === snap.afterStatus;
      if (current && titleMatch && bodyMatch && statusMatch) {
        onReplaceChapterBody(
          snap.chapterId,
          snap.beforeBody,
          snap.beforeStatus,
        );
        restored += 1;
        restoredSuggestionIds.push(...snap.suggestionIds);
      } else {
        skipped += 1;
      }
    }

    setUndoSnapshot(null);
    if (restoredSuggestionIds.length) {
      const drop = new Set(restoredSuggestionIds);
      setAppliedIds((prev) => prev.filter((id) => !drop.has(id)));
    }
    setUndoMessage(`已撤销 ${restored} 章，跳过 ${skipped} 章`);
    setApplyMessage(null);
  }, [undoSnapshot, chapters, onReplaceChapterBody]);

  const selectedSelectableCount = useMemo(() => {
    if (!result) return 0;
    return selectedIds.filter((id) => {
      const s = result.suggestions.find((x) => x.suggestionId === id);
      if (!s) return false;
      return canSelectSuggestion(s, chapters, appliedSet).selectable;
    }).length;
  }, [result, selectedIds, chapters, appliedSet]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop content-fuse-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="content-fuse-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="模板卡片融合建议"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="content-fuse-dialog__head">
          <div>
            <strong>
              <Sparkles size={16} style={{ verticalAlign: "-2px" }} />{" "}
              模板/卡片融合建议
            </strong>
            <p className="content-fuse-dialog__sub">
              M3-B/M3-C：生成建议后可双栏预览并勾选确认写入；未确认关闭不会改章节。最近一次成功写入可在本对话框内一次性撤销（漂移章跳过）。
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-label="关闭融合对话框"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>

        <div className="content-fuse-dialog__body">
          <p className="content-fuse-dialog__quota" aria-live="polite">
            {quotaTip}
          </p>
          {sourceError && (
            <p className="content-fuse-dialog__error" role="alert">
              {sourceError}
            </p>
          )}
          {localError && (
            <p className="content-fuse-dialog__error" role="alert">
              {localError}
            </p>
          )}
          {runMessage && (
            <p className="content-fuse-dialog__msg" aria-live="polite">
              {busy && <LoaderCircle size={14} className="spin" />} {runMessage}
            </p>
          )}
          {applyMessage && (
            <p
              className="content-fuse-dialog__msg"
              role="status"
              aria-live="polite"
              data-testid="content-fuse-apply-summary"
            >
              {applyMessage}
            </p>
          )}
          {undoMessage && (
            <p
              className="content-fuse-dialog__msg"
              role="status"
              aria-live="polite"
              data-testid="content-fuse-undo-summary"
            >
              {undoMessage}
            </p>
          )}

          {loadingSources ? (
            <LoadingLine label="加载模板与卡片…" />
          ) : (
            <div className="content-fuse-grid">
              <section
                className="content-fuse-panel"
                aria-label="中标内容模板"
              >
                <h4>中标模板（0~{CONTENT_FUSE_LIMITS.maxTemplates}）</h4>
                {templates.length === 0 ? (
                  <p className="content-fuse-empty">暂无 active 模板</p>
                ) : (
                  <ul className="content-fuse-list">
                    {templates.map((t) => {
                      const checked = templateIds.includes(t.id);
                      return (
                        <li key={t.id}>
                          <label className="content-fuse-check">
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={busy}
                              aria-label={`模板 ${t.title}`}
                              onChange={() => {
                                const { next, error } = toggleId(
                                  templateIds,
                                  t.id,
                                  CONTENT_FUSE_LIMITS.maxTemplates,
                                );
                                setTemplateIds(next);
                                setLocalError(error);
                              }}
                            />
                            <span>
                              <strong>{t.title}</strong>
                              <small>
                                {t.chapterCount} 章
                                {t.outlineTitles?.length
                                  ? ` · ${t.outlineTitles.slice(0, 2).join("、")}`
                                  : ""}
                              </small>
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              <section className="content-fuse-panel" aria-label="知识卡片">
                <h4>
                  知识卡片（0~{CONTENT_FUSE_LIMITS.maxCards}，文本类）
                </h4>
                {cards.length === 0 ? (
                  <p className="content-fuse-empty">暂无可用文本卡片</p>
                ) : (
                  <ul className="content-fuse-list">
                    {cards.map((c) => {
                      const checked = cardIds.includes(c.id);
                      return (
                        <li key={c.id}>
                          <label className="content-fuse-check">
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={busy}
                              aria-label={`卡片 ${c.title}`}
                              onChange={() => {
                                const { next, error } = toggleId(
                                  cardIds,
                                  c.id,
                                  CONTENT_FUSE_LIMITS.maxCards,
                                );
                                setCardIds(next);
                                setLocalError(error);
                              }}
                            />
                            <span>
                              <strong>{c.title}</strong>
                              <small>
                                {CARD_TYPE_LABEL[c.type] || c.type}
                                {c.summary ? ` · ${c.summary.slice(0, 40)}` : ""}
                              </small>
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              <section
                className="content-fuse-panel content-fuse-panel--targets"
                aria-label="目标章节"
              >
                <h4>
                  目标章节（1~{CONTENT_FUSE_LIMITS.maxTargets}，必选）
                </h4>
                {chapters.length === 0 ? (
                  <p className="content-fuse-empty">当前项目无章节</p>
                ) : (
                  <ul className="content-fuse-list">
                    {chapters.map((ch) => {
                      const checked = targetIds.includes(ch.id);
                      return (
                        <li key={ch.id}>
                          <label className="content-fuse-check">
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={busy}
                              aria-label={`目标章节 ${ch.title}`}
                              onChange={() => {
                                const { next, error } = toggleId(
                                  targetIds,
                                  ch.id,
                                  CONTENT_FUSE_LIMITS.maxTargets,
                                );
                                setTargetIds(next);
                                setLocalError(error);
                              }}
                            />
                            <span>
                              <strong>{ch.title || "未命名章节"}</strong>
                              <small>
                                {(ch.body || "").trim()
                                  ? `${(ch.body || "").trim().length} 字`
                                  : "空正文"}
                              </small>
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </div>
          )}

          {result && (
            <section
              className="content-fuse-results"
              aria-label="融合建议与确认写入"
            >
              <h4>
                融合建议
                {result.model ? ` · 模型 ${result.model}` : ""}
                <span className="content-fuse-results__hint">
                  {" "}
                  · 默认不勾选；仅基线匹配项可确认写入
                </span>
              </h4>
              {result.skippedSources.length > 0 && (
                <p className="content-fuse-dialog__sub">
                  已跳过来源 {result.skippedSources.length} 项（不可用/归档/图片等）
                </p>
              )}
              {result.suggestions.length === 0 ? (
                <p className="content-fuse-empty">无有效建议</p>
              ) : (
                <ul className="content-fuse-suggestions">
                  {result.suggestions.map((s) => {
                    const chapter = findChapter(chapters, s.targetChapterId);
                    const gate = canSelectSuggestion(s, chapters, appliedSet);
                    const checked = selectedIds.includes(s.suggestionId);
                    const liveBody = chapter?.body || "";
                    const outcome = applyOutcomes.find(
                      (o) => o.suggestionId === s.suggestionId,
                    );
                    return (
                      <li
                        key={s.suggestionId}
                        className={
                          "content-fuse-suggestion" +
                          (appliedSet.has(s.suggestionId)
                            ? " content-fuse-suggestion--applied"
                            : "")
                        }
                        aria-label={`建议 ${s.targetTitle}`}
                        data-suggestion-id={s.suggestionId}
                        data-base-ok={gate.selectable ? "1" : "0"}
                      >
                        <div className="content-fuse-suggestion__head">
                          <label className="content-fuse-check content-fuse-check--apply">
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={!gate.selectable || busy}
                              aria-label={`勾选写入建议 ${s.targetTitle || s.targetChapterId}`}
                              onChange={(e) => {
                                toggleSuggestion(s, e.target.checked);
                              }}
                            />
                            <span>
                              <strong>{s.targetTitle || s.targetChapterId}</strong>
                            </span>
                          </label>
                          <span className="badge badge-primary">
                            置信度 {s.confidence}
                          </span>
                          <span className="badge">{s.action}</span>
                          {appliedSet.has(s.suggestionId) && (
                            <span className="badge badge-primary">已写入</span>
                          )}
                          {!gate.selectable && gate.reason && (
                            <span
                              className="content-fuse-suggestion__block-reason"
                              data-testid={`fuse-block-${s.suggestionId}`}
                            >
                              {gate.reason}
                            </span>
                          )}
                          {outcome?.status === "skipped" && outcome.reason && (
                            <span className="content-fuse-suggestion__skip">
                              跳过：{outcome.reason}
                            </span>
                          )}
                        </div>
                        {s.reason && (
                          <p className="content-fuse-suggestion__reason">
                            {s.reason}
                          </p>
                        )}
                        {s.sourceRefs.length > 0 && (
                          <p className="content-fuse-suggestion__refs">
                            来源：
                            {s.sourceRefs
                              .map((r) => formatFuseSourceRefLabel(r))
                              .join("、")}
                          </p>
                        )}
                        {s.diffSummary && (
                          <p className="content-fuse-suggestion__diff">
                            摘要：{s.diffSummary}
                          </p>
                        )}
                        <div
                          className="content-fuse-diff"
                          aria-label={`差异预览 ${s.targetTitle || s.targetChapterId}`}
                        >
                          <div className="content-fuse-diff__col">
                            <div className="content-fuse-diff__label">
                              当前正文
                            </div>
                            <pre className="content-fuse-suggestion__md mono">
                              {liveBody || "（空）"}
                            </pre>
                          </div>
                          <div className="content-fuse-diff__col">
                            <div className="content-fuse-diff__label">
                              建议正文
                              {s.action === "expand" ? "（追加）" : "（替换）"}
                            </div>
                            <pre className="content-fuse-suggestion__md mono">
                              {s.proposedMarkdown || "（空建议）"}
                            </pre>
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          )}
        </div>

        <footer className="content-fuse-dialog__foot">
          {busy ? (
            <button
              type="button"
              className="btn btn-soft btn-sm"
              onClick={() => {
                void onCancelTask();
              }}
            >
              取消任务
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={onClose}
            >
              关闭
            </button>
          )}
          {result && result.suggestions.length > 0 && (
            <button
              type="button"
              className="btn btn-soft btn-sm"
              disabled={busy || selectedSelectableCount === 0}
              aria-label="确认写入所选"
              onClick={handleConfirmApply}
            >
              确认写入所选
              {selectedSelectableCount > 0
                ? `（${selectedSelectableCount}）`
                : ""}
            </button>
          )}
          {undoSnapshot && (
            <button
              type="button"
              className="btn btn-soft btn-sm"
              disabled={busy}
              aria-label="撤销本次写入"
              data-testid="content-fuse-undo-batch"
              onClick={handleUndoBatch}
            >
              撤销本次写入
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!canSubmit}
            aria-label="生成只读融合建议"
            onClick={() => {
              void handleRun();
            }}
          >
            {busy ? "生成中…" : "生成只读建议"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function LoadingLine({ label }: { label: string }) {
  return (
    <p className="content-fuse-empty">
      <LoaderCircle size={14} className="spin" /> {label}
    </p>
  );
}
