/**
 * 模块：响应矩阵面板
 * 用途：编辑技术要求/评分点到章节和大纲节点的可追溯映射；展示多端冲突并支持显式载入远端。
 * 对接：useTechnicalPlanEditors.responseMatrix / responseMatrixConflict；串行 response_match。
 * 二次开发：保持为受控组件；冲突时禁止静默覆盖；批次进度仅展示，不写 editor-state。
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  GitBranch,
  ListChecks,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import type {
  ChapterContent,
  OutlineNode,
  ResponseMatrixItem,
  ResponseMatrixSuggestion,
  ResponseMatrixStatus,
} from "../types";
import {
  collectOutlineOptions,
  getResponseMatrixCoverage,
} from "../lib/responseMatrix";

const STATUS_OPTIONS: Array<{ value: ResponseMatrixStatus; label: string }> = [
  { value: "uncovered", label: "未覆盖" },
  { value: "partial", label: "部分覆盖" },
  { value: "covered", label: "已覆盖" },
  { value: "waived", label: "不响应" },
];

const SUGGESTION_STATUS_LABELS: Record<
  ResponseMatrixSuggestion["status"],
  string
> = {
  uncovered: "未覆盖",
  partial: "部分覆盖",
  covered: "已覆盖",
};

function toggleId(values: string[], id: string, checked: boolean): string[] {
  const set = new Set(values);
  if (checked) {
    set.add(id);
  } else {
    set.delete(id);
  }
  return [...set];
}

export function ResponseMatrixPanel(props: {
  items: ResponseMatrixItem[];
  chapters: ChapterContent[];
  outline: OutlineNode[];
  onRefresh: () => void;
  onPatch: (id: string, patch: Partial<ResponseMatrixItem>) => void;
  suggestions: ResponseMatrixSuggestion[];
  suggestionBusy: boolean;
  /** 串行候选分批进度文案，如「候选批次 2/5 · 已累计 12 条待确认」 */
  suggestionProgressLabel?: string | null;
  onRequestSuggestions: () => void;
  onApplySuggestions: (sourceKeys: string[]) => void;
  onClearSuggestions: () => void;
  /** 多端矩阵冲突提示；有值时展示中文说明与「重新载入远端矩阵」 */
  conflictMessage?: string | null;
  onReloadRemote?: () => void;
}) {
  const outlineOptions = collectOutlineOptions(props.outline);
  const [selectedSuggestionKeys, setSelectedSuggestionKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const suggestionsBySource = useMemo(
    () => new Map(props.suggestions.map((item) => [item.sourceKey, item])),
    [props.suggestions],
  );
  const chapterTitles = useMemo(
    () => new Map(props.chapters.map((chapter) => [chapter.id, chapter.title])),
    [props.chapters],
  );
  const outlineTitles = useMemo(
    () => new Map(outlineOptions.map((node) => [node.id, node.title])),
    [outlineOptions],
  );

  useEffect(() => {
    setSelectedSuggestionKeys(new Set(props.suggestions.map((item) => item.sourceKey)));
  }, [props.suggestions]);

  const coveredCount = props.items.filter((item) => {
    const coverage = getResponseMatrixCoverage(item, props.chapters, props.outline);
    return coverage.validChapterIds.length + coverage.validOutlineNodeIds.length > 0;
  }).length;
  const invalidCount = props.items.reduce(
    (sum, item) =>
      sum + getResponseMatrixCoverage(item, props.chapters, props.outline).invalidCount,
    0,
  );

  const formatSuggestionLinks = (suggestion: ResponseMatrixSuggestion) => {
    const labels = [
      ...suggestion.chapterIds.flatMap((id) => {
        const title = chapterTitles.get(id);
        return title ? [`正文：${title}`] : [];
      }),
      ...suggestion.outlineNodeIds.flatMap((id) => {
        const title = outlineTitles.get(id);
        return title ? [`大纲：${title}`] : [];
      }),
    ];
    return labels.join("；") || "暂未找到可用关联";
  };

  const toggleSuggestion = (sourceKey: string, checked: boolean) => {
    setSelectedSuggestionKeys((current) => {
      const next = new Set(current);
      if (checked) next.add(sourceKey);
      else next.delete(sourceKey);
      return next;
    });
  };

  return (
    <section className="card card-pad response-matrix" aria-label="响应矩阵">
      <div className="tp-toolbar">
        <div>
          <strong>响应矩阵</strong>
          <div className="response-matrix__summary">
            {coveredCount}/{props.items.length} 已建立覆盖
            {invalidCount > 0 ? ` · ${invalidCount} 个失效引用` : ""}
            {props.suggestionProgressLabel
              ? ` · ${props.suggestionProgressLabel}`
              : ""}
          </div>
        </div>
        <div className="tp-toolbar__spacer" />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={props.suggestionBusy || props.items.length === 0}
          onClick={props.onRequestSuggestions}
          title={props.suggestionProgressLabel || "按候选章节/大纲分批串行生成建议"}
        >
          <Sparkles size={14} /> {props.suggestionBusy ? "匹配中…" : "智能建议"}
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={props.onRefresh}
        >
          <RefreshCw size={14} /> 刷新来源
        </button>
      </div>

      {props.conflictMessage ? (
        <div
          className="response-matrix__conflict"
          role="alert"
          style={{
            display: "flex",
            gap: 12,
            alignItems: "flex-start",
            margin: "12px 0",
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid #f0b429",
            background: "rgba(240, 180, 41, 0.12)",
          }}
        >
          <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>矩阵保存冲突</div>
            <div style={{ fontSize: "0.92em", opacity: 0.92 }}>
              {props.conflictMessage}
              （已保留本页本地编辑，未用远端结果静默覆盖。）
            </div>
          </div>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => props.onReloadRemote?.()}
            disabled={!props.onReloadRemote}
          >
            <GitBranch size={14} /> 重新载入远端矩阵
          </button>
        </div>
      ) : null}

      {props.suggestions.length > 0 || props.suggestionBusy ? (
        <div className="response-matrix__suggestion-actions">
          <span>
            {props.suggestionProgressLabel ||
              (props.suggestions.length > 0
                ? `已生成 ${props.suggestions.length} 条待确认建议`
                : "正在生成映射建议…")}
          </span>
          <div className="tp-toolbar__spacer" />
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={selectedSuggestionKeys.size === 0}
            onClick={() => props.onApplySuggestions([...selectedSuggestionKeys])}
          >
            应用已选建议（{selectedSuggestionKeys.size}）
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={props.onClearSuggestions}
            title="清除当前待确认建议"
          >
            <X size={14} />
          </button>
        </div>
      ) : null}

      {props.items.length === 0 ? (
        <div className="response-matrix__empty">暂无可追踪条目</div>
      ) : (
        <div className="response-matrix__list">
          {props.items.map((item) => {
            const coverage = getResponseMatrixCoverage(
              item,
              props.chapters,
              props.outline,
            );
            const suggestion = suggestionsBySource.get(item.sourceKey);
            return (
              <article className="response-matrix__item" key={item.id}>
                <div className="response-matrix__item-head">
                  <span
                    className={`response-matrix__kind response-matrix__kind--${item.kind}`}
                  >
                    {item.kind === "requirement" ? "技术要求" : "评分点"}
                  </span>
                  {item.weight ? <span className="mono">{item.weight}</span> : null}
                  <select
                    value={item.status}
                    onChange={(event) =>
                      props.onPatch(item.id, {
                        status: event.target.value as ResponseMatrixStatus,
                      })
                    }
                    aria-label="响应状态"
                  >
                    {STATUS_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>

                <p className="response-matrix__source">{item.sourceText}</p>

                {suggestion ? (
                  <div className="response-matrix__suggestion">
                    <label className="response-matrix__suggestion-head">
                      <input
                        type="checkbox"
                        checked={selectedSuggestionKeys.has(suggestion.sourceKey)}
                        onChange={(event) =>
                          toggleSuggestion(suggestion.sourceKey, event.target.checked)
                        }
                      />
                      <span>智能建议</span>
                      <span>{suggestion.confidence}%</span>
                      <span>{SUGGESTION_STATUS_LABELS[suggestion.status]}</span>
                    </label>
                    <div>{formatSuggestionLinks(suggestion)}</div>
                    {suggestion.reason ? <small>{suggestion.reason}</small> : null}
                  </div>
                ) : null}

                {coverage.invalidCount > 0 ? (
                  <div className="response-matrix__warning">
                    <AlertTriangle size={14} />
                    <span>{coverage.invalidCount} 个引用已不在当前章节或大纲中</span>
                  </div>
                ) : null}

                <div className="response-matrix__links">
                  <div>
                    <div className="response-matrix__link-title">
                      <ListChecks size={14} /> 章节
                    </div>
                    <div className="response-matrix__checks">
                      {props.chapters.length === 0 ? (
                        <span className="response-matrix__muted">无章节</span>
                      ) : (
                        props.chapters.map((chapter) => (
                          <label key={chapter.id} className="response-matrix__check">
                            <input
                              type="checkbox"
                              checked={item.chapterIds.includes(chapter.id)}
                              onChange={(event) =>
                                props.onPatch(item.id, {
                                  chapterIds: toggleId(
                                    item.chapterIds,
                                    chapter.id,
                                    event.target.checked,
                                  ),
                                })
                              }
                            />
                            <span>{chapter.title}</span>
                          </label>
                        ))
                      )}
                    </div>
                  </div>

                  <div>
                    <div className="response-matrix__link-title">
                      <GitBranch size={14} /> 大纲
                    </div>
                    <div className="response-matrix__checks">
                      {outlineOptions.length === 0 ? (
                        <span className="response-matrix__muted">无大纲</span>
                      ) : (
                        outlineOptions.map((node) => (
                          <label
                            key={node.id}
                            className="response-matrix__check"
                            style={{ paddingLeft: `${(node.level - 1) * 12}px` }}
                          >
                            <input
                              type="checkbox"
                              checked={item.outlineNodeIds.includes(node.id)}
                              onChange={(event) =>
                                props.onPatch(item.id, {
                                  outlineNodeIds: toggleId(
                                    item.outlineNodeIds,
                                    node.id,
                                    event.target.checked,
                                  ),
                                })
                              }
                            />
                            <span>{node.title}</span>
                          </label>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                <textarea
                  value={item.notes}
                  onChange={(event) =>
                    props.onPatch(item.id, { notes: event.target.value })
                  }
                  aria-label="响应备注"
                  placeholder="备注"
                />
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
