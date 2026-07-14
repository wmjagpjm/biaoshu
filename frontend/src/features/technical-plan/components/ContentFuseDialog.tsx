/**
 * 模块：模板/卡片融合建议对话框（阶段3 M3-A/M3-B/M3-D）
 * 用途：多选模板与 active 文本卡片、目标章节，发起 content_fuse 只读建议；
 *      M3-B 双栏差异预览；M3-D 服务端原子确认写入与持久恢复批次一次消费。
 * 对接：useProjectPipeline.runTask("content_fuse")；editors.reloadFromApi；
 *      contentFuseApplications 三接口；/api/templates；/api/cards；TechnicalPlanWorkspace。
 * 二次开发：确认前零本地写章节；POST/consume 成功后只调用一次 onReloadFromApi；
 *       据其 boolean 判定刷新成败，禁止再直连 apiFetch(editor-state)；
 *       taskId/batchId 仅 Dialog 实例内存，禁止 URL/存储/console/剪贴板；
 *       关闭或切项目立即使在途请求失效；错误固定中文，不回显服务端原文。
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
  buildContentFusePayload,
  CONTENT_FUSE_LIMITS,
  formatFuseQuotaTip,
  formatFuseSourceRefLabel,
  matchFuseSuggestionBase,
  normalizeContentFuseResult,
  type ContentFuseResult,
  type ContentFuseSuggestion,
} from "../lib/contentFuse";
import {
  consumeContentFuseApplication,
  createContentFuseApplication,
  listContentFuseApplications,
  type ContentFuseApplicationListItem,
} from "../lib/contentFuseApplications";

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
   * 用途：原子确认/恢复成功后唯一一次 GET editor-state 并写父级状态。
   * 对接：useTechnicalPlanEditors.reloadFromApi（返回 Promise boolean）。
   * true=重载成功可刷批次；false=保持本地并显示“已完成但刷新失败”。
   */
  onReloadFromApi: () => Promise<boolean>;
};

const TEXT_TYPES = new Set(["document", "qualification", "performance"]);

const MSG_SAME_CHAPTER = "同一目标章节只能选择一条建议";
const MSG_APPLY_FAIL = "融合确认失败，请刷新后重试";
/** 用途：POST 已成功但 editor-state 重读失败；禁止谎报业务失败。 */
const MSG_APPLY_RELOAD_FAIL = "融合已写入，但刷新失败，请关闭后重新打开";
const MSG_RESTORE_FAIL = "恢复失败，请刷新后重试";
/** 用途：consume 已成功但 editor-state 重读失败；禁止谎报业务失败。 */
const MSG_RESTORE_RELOAD_FAIL = "恢复已完成，但刷新失败，请关闭后重新打开";
const MSG_BATCH_WINDOW = "最多保留最近 20 批，不是完整版本历史";

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
 * 用途：单条建议是否可勾选；空正文永远不可。
 * 二次开发：同章唯一在 toggle 层处理，此处不隐藏勾选框。
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

/** 用途：列表时间展示；不输出 batchId/taskId。 */
function formatBatchTime(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", { hour12: false });
}

/**
 * 用途：M3-A/M3-B/M3-D 融合入口 UI；建议预览、原子确认与持久恢复。
 */
export function ContentFuseDialog({
  open,
  projectId,
  chapters,
  busy,
  onClose,
  onRun,
  onCancelTask,
  onReloadFromApi,
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
  /** 用途：当前 Dialog 实例内成功 content_fuse 任务 id；禁止持久化。 */
  const [applyTaskId, setApplyTaskId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [appliedIds, setAppliedIds] = useState<string[]>([]);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [applyBusy, setApplyBusy] = useState(false);
  const [batches, setBatches] = useState<ContentFuseApplicationListItem[]>([]);
  const [batchesLoading, setBatchesLoading] = useState(false);
  const [batchesError, setBatchesError] = useState<string | null>(null);
  const [restoreMessage, setRestoreMessage] = useState<string | null>(null);
  /** 用途：组件内二次确认中的 batchId；关闭即清空。 */
  const [pendingRestoreBatchId, setPendingRestoreBatchId] = useState<
    string | null
  >(null);
  const [restoreBusy, setRestoreBusy] = useState(false);
  /** 用途：打开/切项目/关闭时递增，使迟到响应失效。 */
  const sessionRef = useRef(0);

  const appliedSet = useMemo(() => new Set(appliedIds), [appliedIds]);

  const clearSessionState = useCallback(() => {
    setTemplateIds([]);
    setCardIds([]);
    setTargetIds([]);
    setLocalError(null);
    setRunMessage(null);
    setResult(null);
    setSourceError(null);
    setApplyTaskId(null);
    setSelectedIds([]);
    setAppliedIds([]);
    setApplyMessage(null);
    setApplyBusy(false);
    setBatches([]);
    setBatchesLoading(false);
    setBatchesError(null);
    setRestoreMessage(null);
    setPendingRestoreBatchId(null);
    setRestoreBusy(false);
  }, []);

  /**
   * 用途：按当前项目拉取最近批次；代次不匹配时丢弃。
   */
  const refreshBatches = useCallback(
    async (session: number, pid: string) => {
      setBatchesLoading(true);
      setBatchesError(null);
      try {
        const res = await listContentFuseApplications(pid);
        if (session !== sessionRef.current) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        setBatches(items);
      } catch {
        if (session !== sessionRef.current) return;
        setBatchesError("批次列表加载失败，请关闭后重试");
        setBatches([]);
      } finally {
        if (session === sessionRef.current) {
          setBatchesLoading(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    // 关闭或切换项目：使在途请求失效并清空实例态
    sessionRef.current += 1;
    clearSessionState();
    if (!open) {
      return;
    }
    const session = sessionRef.current;
    setLoadingSources(true);
    let cancelled = false;
    void Promise.all([
      apiFetch<BidTemplateSummary[]>("/templates?status=active"),
      listCards({ status: "active" }),
    ])
      .then(([tpl, cardList]) => {
        if (cancelled || session !== sessionRef.current) return;
        setTemplates(Array.isArray(tpl) ? tpl : []);
        setCards(
          (Array.isArray(cardList) ? cardList : []).filter((c) =>
            TEXT_TYPES.has(c.type),
          ),
        );
      })
      .catch((err) => {
        if (!cancelled && session === sessionRef.current) {
          setSourceError(
            (err as { message?: string }).message || "加载模板/卡片失败",
          );
        }
      })
      .finally(() => {
        if (!cancelled && session === sessionRef.current) {
          setLoadingSources(false);
        }
      });
    void refreshBatches(session, projectId);
    return () => {
      cancelled = true;
    };
  }, [open, projectId, clearSessionState, refreshBatches]);

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
    !applyBusy &&
    !restoreBusy &&
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
    setApplyTaskId(null);
    setSelectedIds([]);
    setAppliedIds([]);
    setApplyMessage(null);
    setRestoreMessage(null);
    setPendingRestoreBatchId(null);
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
      // 仅成功任务在当前实例内存保留 task.id，供原子确认
      if (task.status === "success" && typeof task.id === "string" && task.id) {
        setApplyTaskId(task.id);
      } else {
        setApplyTaskId(null);
      }
      setRunMessage(
        normalized
          ? `已生成 ${normalized.suggestions.length} 条只读建议（默认不写入，需勾选确认）`
          : "任务完成但无建议",
      );
    } catch (err) {
      if (session !== sessionRef.current) return;
      setLocalError((err as { message?: string }).message || "请求失败");
      setRunMessage(null);
      setApplyTaskId(null);
    }
  }, [onRun, payload]);

  const toggleSuggestion = useCallback(
    (suggestion: ContentFuseSuggestion, checked: boolean) => {
      const gate = canSelectSuggestion(suggestion, chapters, appliedSet);
      if (checked && !gate.selectable) return;

      // 先基于当前 selectedIds/result 纯计算，再分别 setState（禁止在 updater 内调其他 setState）
      if (!checked) {
        const next = selectedIds.filter((id) => id !== suggestion.suggestionId);
        setSelectedIds(next);
        // 取消勾选时清掉遗留的同章提示
        if (localError === MSG_SAME_CHAPTER) {
          setLocalError(null);
        }
        setApplyMessage(null);
        return;
      }

      if (selectedIds.includes(suggestion.suggestionId)) {
        setApplyMessage(null);
        return;
      }

      // 同一目标章最多一条：第二条保持未选并提示，不靠隐藏按钮
      if (result) {
        const conflict = selectedIds.some((id) => {
          const other = result.suggestions.find((x) => x.suggestionId === id);
          return (
            !!other &&
            other.targetChapterId === suggestion.targetChapterId &&
            other.suggestionId !== suggestion.suggestionId
          );
        });
        if (conflict) {
          setLocalError(MSG_SAME_CHAPTER);
          setApplyMessage(null);
          return;
        }
      }

      if (localError === MSG_SAME_CHAPTER) {
        setLocalError(null);
      }
      setSelectedIds([...selectedIds, suggestion.suggestionId]);
      setApplyMessage(null);
    },
    [chapters, appliedSet, result, selectedIds, localError],
  );

  /**
   * 用途：原子确认所选；点击前本地校验，真正写入仅一次 POST。
   * 二次开发：在途与成功前禁止 onReplaceChapterBody / editor-state PUT / 先改本地 chapters。
   */
  const handleConfirmApply = useCallback(async () => {
    if (!result || !applyTaskId || applyBusy || restoreBusy || busy) return;
    const session = sessionRef.current;
    const pid = projectId;

    // 本地可选性校验 + 同章唯一（服务端仍为最终校验）
    const validIds: string[] = [];
    const seenChapters = new Set<string>();
    for (const suggestionId of selectedIds) {
      const suggestion = result.suggestions.find(
        (s) => s.suggestionId === suggestionId,
      );
      if (!suggestion) continue;
      const gate = canSelectSuggestion(suggestion, chapters, appliedSet);
      if (!gate.selectable) continue;
      if (seenChapters.has(suggestion.targetChapterId)) {
        setLocalError(MSG_SAME_CHAPTER);
        return;
      }
      seenChapters.add(suggestion.targetChapterId);
      validIds.push(suggestion.suggestionId);
    }
    if (validIds.length === 0) {
      setLocalError("请先勾选可写入的建议");
      return;
    }
    if (validIds.length > 5) {
      setLocalError("最多选择 5 条建议");
      return;
    }

    setLocalError(null);
    setApplyMessage(null);
    setRestoreMessage(null);
    setApplyBusy(true);

    // 区分业务 POST 失败 与 POST 已成功后的刷新失败
    let created: Awaited<ReturnType<typeof createContentFuseApplication>>;
    try {
      created = await createContentFuseApplication(pid, {
        taskId: applyTaskId,
        suggestionIds: validIds,
      });
    } catch {
      if (session !== sessionRef.current) return;
      setLocalError(MSG_APPLY_FAIL);
      setApplyMessage(null);
      setApplyBusy(false);
      return;
    }
    if (session !== sessionRef.current) return;

    // POST 已成功：立即标记已应用并清空勾选，阻止同 Dialog 重复 POST
    setAppliedIds((prev) => [...new Set([...prev, ...validIds])]);
    setSelectedIds((prev) => prev.filter((id) => !validIds.includes(id)));

    try {
      // 唯一一次实际重载：父级 GET + setState；禁止再直连 apiFetch(editor-state)
      const reloaded = await onReloadFromApi();
      if (session !== sessionRef.current) return;
      if (!reloaded) {
        setLocalError(MSG_APPLY_RELOAD_FAIL);
        setApplyMessage(null);
        return;
      }
      await refreshBatches(session, pid);
      if (session !== sessionRef.current) return;
      setApplyMessage(`已写入 ${created.appliedChapterCount} 章`);
    } catch {
      // refreshBatches 等后续失败：业务已写入，仍按刷新失败处理
      if (session !== sessionRef.current) return;
      setLocalError(MSG_APPLY_RELOAD_FAIL);
      setApplyMessage(null);
    } finally {
      if (session === sessionRef.current) {
        setApplyBusy(false);
      }
    }
  }, [
    result,
    applyTaskId,
    applyBusy,
    restoreBusy,
    busy,
    projectId,
    selectedIds,
    chapters,
    appliedSet,
    onReloadFromApi,
    refreshBatches,
  ]);

  /**
   * 用途：二次确认后一次消费恢复；完整/部分/零恢复均刷新为已消费。
   */
  const handleConfirmRestore = useCallback(async () => {
    if (!pendingRestoreBatchId || restoreBusy || applyBusy || busy) return;
    const session = sessionRef.current;
    const pid = projectId;
    const batchId = pendingRestoreBatchId;
    setRestoreBusy(true);
    setLocalError(null);
    setRestoreMessage(null);
    setApplyMessage(null);

    // 区分业务 consume 失败 与 consume 已成功后的刷新失败
    let res: Awaited<ReturnType<typeof consumeContentFuseApplication>>;
    try {
      res = await consumeContentFuseApplication(pid, batchId);
    } catch {
      if (session !== sessionRef.current) return;
      setLocalError(MSG_RESTORE_FAIL);
      setPendingRestoreBatchId(null);
      setRestoreBusy(false);
      return;
    }
    if (session !== sessionRef.current) return;

    // consume 已成功：立即清空二次确认，并在内存把该批标为 consumed，阻止再次 consume
    setPendingRestoreBatchId(null);
    setBatches((prev) =>
      prev.map((b) =>
        b.batchId === batchId
          ? {
              ...b,
              state: "consumed" as const,
              consumedAt: res.consumedAt,
            }
          : b,
      ),
    );

    try {
      // 唯一一次实际重载；禁止再直连 apiFetch(editor-state)
      const reloaded = await onReloadFromApi();
      if (session !== sessionRef.current) return;
      if (!reloaded) {
        setLocalError(MSG_RESTORE_RELOAD_FAIL);
        setRestoreMessage(null);
        return;
      }
      await refreshBatches(session, pid);
      if (session !== sessionRef.current) return;
      setRestoreMessage(
        `已恢复 ${res.restoredChapterCount} 章，跳过 ${res.skippedChapterCount} 章`,
      );
    } catch {
      // 列表刷新等后续失败：consume 已成功，仍按刷新失败处理
      if (session !== sessionRef.current) return;
      setLocalError(MSG_RESTORE_RELOAD_FAIL);
      setRestoreMessage(null);
    } finally {
      if (session === sessionRef.current) {
        setRestoreBusy(false);
      }
    }
  }, [
    pendingRestoreBatchId,
    restoreBusy,
    applyBusy,
    busy,
    projectId,
    onReloadFromApi,
    refreshBatches,
  ]);

  const selectedSelectableCount = useMemo(() => {
    if (!result) return 0;
    return selectedIds.filter((id) => {
      const s = result.suggestions.find((x) => x.suggestionId === id);
      if (!s) return false;
      return canSelectSuggestion(s, chapters, appliedSet).selectable;
    }).length;
  }, [result, selectedIds, chapters, appliedSet]);

  const opBusy = busy || applyBusy || restoreBusy;

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
              M3-B/M3-D：生成建议后可双栏预览并勾选，由服务端原子确认写入；关闭未确认不改章节。
              最近批次可跨刷新恢复。
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
            <p
              className="content-fuse-dialog__error"
              role="alert"
              data-testid="content-fuse-local-error"
            >
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
          {restoreMessage && (
            <p
              className="content-fuse-dialog__msg"
              role="status"
              aria-live="polite"
              data-testid="content-fuse-restore-summary"
            >
              {restoreMessage}
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
                              disabled={opBusy}
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
                              disabled={opBusy}
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
                              disabled={opBusy}
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

          {/* 持久恢复批次：打开时读一次；不展示 ID/正文/来源 */}
          <section
            className="content-fuse-batches"
            aria-label="融合写入恢复批次"
            data-testid="content-fuse-batches"
          >
            <h4>最近恢复批次</h4>
            <p className="content-fuse-dialog__sub">{MSG_BATCH_WINDOW}</p>
            {batchesLoading ? (
              <LoadingLine label="加载恢复批次…" />
            ) : batchesError ? (
              <p className="content-fuse-dialog__error" role="alert">
                {batchesError}
              </p>
            ) : batches.length === 0 ? (
              <p className="content-fuse-empty">暂无恢复批次</p>
            ) : (
              <ul className="content-fuse-batch-list">
                {batches.map((b) => {
                  const isActive = b.state === "active";
                  const pending = pendingRestoreBatchId === b.batchId;
                  // 列表 key 用 batchId 保证 React 稳定，但不渲染到可见文案
                  return (
                    <li
                      key={b.batchId}
                      className="content-fuse-batch-item"
                      data-testid="content-fuse-batch-item"
                      data-batch-state={isActive ? "active" : "consumed"}
                    >
                      <div className="content-fuse-batch-item__meta">
                        <span>{formatBatchTime(b.createdAt)}</span>
                        <span>{b.chapterCount} 章</span>
                        <span className="badge">
                          {isActive ? "可恢复" : "已消费"}
                        </span>
                      </div>
                      {isActive && !pending && (
                        <button
                          type="button"
                          className="btn btn-soft btn-sm"
                          disabled={opBusy}
                          aria-label="恢复此批次"
                          data-testid="content-fuse-restore-start"
                          onClick={() => {
                            setPendingRestoreBatchId(b.batchId);
                            setLocalError(null);
                            setRestoreMessage(null);
                          }}
                        >
                          恢复
                        </button>
                      )}
                      {isActive && pending && (
                        <div
                          className="content-fuse-batch-item__confirm"
                          data-testid="content-fuse-restore-confirm"
                        >
                          <span>确认恢复该批次？</span>
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            disabled={opBusy}
                            aria-label="确认恢复批次"
                            data-testid="content-fuse-restore-yes"
                            onClick={() => {
                              void handleConfirmRestore();
                            }}
                          >
                            {restoreBusy ? "恢复中…" : "确认"}
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            disabled={opBusy}
                            aria-label="取消恢复批次"
                            data-testid="content-fuse-restore-no"
                            onClick={() => setPendingRestoreBatchId(null)}
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

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
                  · 默认不勾选；仅基线匹配项可确认写入；同章仅一条
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
                              disabled={!gate.selectable || opBusy}
                              aria-label={`勾选写入建议 ${s.targetTitle || s.targetChapterId}`}
                              onChange={(e) => {
                                toggleSuggestion(s, e.target.checked);
                              }}
                            />
                            <span>
                              <strong>
                                {s.targetTitle || s.targetChapterId}
                              </strong>
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
              disabled={
                opBusy || selectedSelectableCount === 0 || !applyTaskId
              }
              aria-label="确认写入所选"
              data-testid="content-fuse-confirm-apply"
              onClick={() => {
                void handleConfirmApply();
              }}
            >
              {applyBusy
                ? "确认中…"
                : `确认写入所选${
                    selectedSelectableCount > 0
                      ? `（${selectedSelectableCount}）`
                      : ""
                  }`}
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
