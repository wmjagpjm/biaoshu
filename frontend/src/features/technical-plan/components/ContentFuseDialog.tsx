/**
 * 模块：模板/卡片融合建议对话框（阶段3 M3-A）
 * 用途：多选模板与 active 文本卡片、目标章节，发起 content_fuse 只读建议展示。
 * 对接：useProjectPipeline.runTask("content_fuse")；/api/templates；/api/cards；
 *      TechnicalPlanWorkspace 编写步入口。
 * 二次开发：禁止「应用/保存/复制到章节」；不得改 useTechnicalPlanEditors 写入路径。
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
  normalizeContentFuseResult,
  type ContentFuseResult,
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
};

const TEXT_TYPES = new Set(["document", "qualification", "performance"]);

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

/**
 * 用途：M3-A 融合入口 UI；只读建议列表，无写入动作。
 */
export function ContentFuseDialog({
  open,
  projectId,
  chapters,
  busy,
  onClose,
  onRun,
  onCancelTask,
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
  const sessionRef = useRef(0);

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
          ? `已生成 ${normalized.suggestions.length} 条只读建议（未写入章节）`
          : "任务完成但无建议",
      );
    } catch (err) {
      if (session !== sessionRef.current) return;
      setLocalError((err as { message?: string }).message || "请求失败");
      setRunMessage(null);
    }
  }, [onRun, payload]);

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
              M3-A 只读：生成建议保存在任务结果中，不会写入章节正文。应用与差异确认属
              M3-B。
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
              aria-label="只读融合建议"
            >
              <h4>
                只读建议
                {result.model ? ` · 模型 ${result.model}` : ""}
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
                  {result.suggestions.map((s) => (
                    <li
                      key={s.suggestionId}
                      className="content-fuse-suggestion"
                      aria-label={`建议 ${s.targetTitle}`}
                    >
                      <div className="content-fuse-suggestion__head">
                        <strong>{s.targetTitle || s.targetChapterId}</strong>
                        <span className="badge badge-primary">
                          置信度 {s.confidence}
                        </span>
                        <span className="badge">{s.action}</span>
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
                      <pre className="content-fuse-suggestion__md mono">
                        {s.proposedMarkdown || "（空建议）"}
                      </pre>
                    </li>
                  ))}
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
