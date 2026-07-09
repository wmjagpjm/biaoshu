import { useMemo, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import type { ChapterContent } from "../types";

/**
 * 模块：正文分章编辑器
 * 用途：左章节列表 + 右 Markdown 正文编辑；字数实时统计。
 * 对接：body 写入 useTechnicalPlanEditors；AI 反馈在父级按选中章 targetId 提交。
 */

export type ChapterEditorProps = {
  chapters: ChapterContent[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChangeBody: (id: string, body: string) => void;
  onChangeTitle: (id: string, title: string) => void;
};

function statusBadge(status: ChapterContent["status"]) {
  if (status === "done")
    return <span className="badge badge-primary">已完成</span>;
  if (status === "generating")
    return <span className="badge badge-primary">生成中</span>;
  if (status === "needs_review")
    return <span className="badge badge-free">待审</span>;
  return <span className="badge badge-muted">待生成</span>;
}

export function ChapterEditor({
  chapters,
  selectedId,
  onSelect,
  onChangeBody,
  onChangeTitle,
}: ChapterEditorProps) {
  const [showPreview, setShowPreview] = useState(false);

  const selected =
    chapters.find((c) => c.id === selectedId) ??
    chapters.find((c) => c.status === "done") ??
    chapters[0];

  const doneCount = useMemo(
    () => chapters.filter((c) => c.status === "done").length,
    [chapters],
  );

  if (!selected) {
    return (
      <div className="card empty-state">
        <strong>暂无章节</strong>
        请先在大纲步确认目录，再由生成任务创建章节（前端 mock 见 mockChapters）。
      </div>
    );
  }

  return (
    <div className="tp-content-layout">
      <aside className="tp-content-sidebar" aria-label="章节列表">
        <div className="tp-content-sidebar__head">
          <strong>章节</strong>
          <span className="mono" style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {doneCount}/{chapters.length} 完成
          </span>
        </div>
        <div className="tp-content-sidebar__list">
          {chapters.map((c) => {
            const active = c.id === selected.id;
            return (
              <button
                key={c.id}
                type="button"
                className={`tp-content-nav-item${active ? " is-active" : ""}`}
                onClick={() => onSelect(c.id)}
              >
                <div className="tp-content-nav-item__top">
                  <span className="tp-content-nav-item__title">{c.title}</span>
                  {statusBadge(c.status)}
                </div>
                <div className="tp-content-nav-item__meta mono">
                  {c.wordCount > 0 ? `${c.wordCount} 字` : "—"}
                </div>
                <div className="tp-content-nav-item__preview">{c.preview}</div>
              </button>
            );
          })}
        </div>
      </aside>

      <div className="tp-content-main card card-pad">
        <div className="tp-toolbar" style={{ marginBottom: 12 }}>
          <input
            className="tp-content-title-input"
            value={selected.title}
            onChange={(e) => onChangeTitle(selected.id, e.target.value)}
            aria-label="章节标题"
          />
          <div className="tp-toolbar__spacer" />
          <span className="mono" style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
            {selected.wordCount} 字
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setShowPreview((v) => !v)}
          >
            {showPreview ? (
              <>
                <EyeOff size={14} /> 编辑
              </>
            ) : (
              <>
                <Eye size={14} /> 预览
              </>
            )}
          </button>
        </div>

        {showPreview ? (
          <pre className="tp-content-preview mono">{selected.body || "（空）"}</pre>
        ) : (
          <textarea
            className="tp-content-body"
            value={selected.body}
            onChange={(e) => onChangeBody(selected.id, e.target.value)}
            placeholder="在此编辑本章 Markdown 正文。可用下方「按反馈调整」让 AI 定向修订……"
            aria-label={`正文：${selected.title}`}
          />
        )}
      </div>
    </div>
  );
}
