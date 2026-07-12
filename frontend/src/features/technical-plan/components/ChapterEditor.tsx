/**
 * 模块：正文分章编辑器
 * 用途：左章节列表 + 右 Markdown 正文编辑；字数实时统计；支持项目图片与知识卡片插入。
 * 对接：body 写入 useTechnicalPlanEditors；项目图片 → biaoshu-image；卡片经 insert-card 追加。
 * 二次开发：新增图片来源时必须继续只写入项目 file_id；禁止外链、data URL 和卡片路径。
 */

import { useMemo, useRef, useState } from "react";
import { Eye, EyeOff, ImagePlus, Library } from "lucide-react";
import type { InsertCardResult } from "../../knowledge-base/types";
import type { ChapterContent } from "../types";
import { InsertCardDialog } from "./InsertCardDialog";

export type ChapterEditorProps = {
  chapters: ChapterContent[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onChangeBody: (id: string, body: string) => void;
  onChangeTitle: (id: string, title: string) => void;
  onUploadImage: (file: File) => Promise<{ id: string; filename: string }>;
  imageBusy?: boolean;
  /** 当前项目 id，用于 insert-card；缺省时隐藏「插入卡片」 */
  projectId?: string;
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
  onUploadImage,
  imageBusy = false,
  projectId,
}: ChapterEditorProps) {
  const [showPreview, setShowPreview] = useState(false);
  const [cardDialogOpen, setCardDialogOpen] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const bodyInputRef = useRef<HTMLTextAreaElement>(null);
  const chaptersRef = useRef(chapters);
  chaptersRef.current = chapters;

  const selected =
    chapters.find((c) => c.id === selectedId) ??
    chapters.find((c) => c.status === "done") ??
    chapters[0];

  const doneCount = useMemo(
    () => chapters.filter((c) => c.status === "done").length,
    [chapters],
  );

  const appendMarkdown = (targetId: string, fragment: string) => {
    const targetChapter = chaptersRef.current.find(
      (chapter) => chapter.id === targetId,
    );
    if (!targetChapter) return;
    const currentBody = targetChapter.body;
    const cursor =
      bodyInputRef.current && document.activeElement === bodyInputRef.current
        ? bodyInputRef.current.selectionStart
        : currentBody.length;
    const safeCursor = Math.min(Math.max(cursor, 0), currentBody.length);
    const before = currentBody.slice(0, safeCursor);
    const after = currentBody.slice(safeCursor);
    const prefix = before && !before.endsWith("\n") ? "\n\n" : "";
    const suffix = after && !after.startsWith("\n") ? "\n\n" : "";
    onChangeBody(targetId, `${before}${prefix}${fragment.trim()}${suffix}${after}`);
  };

  const insertProjectImage = async (file: File) => {
    const targetId = selected.id;
    const uploaded = await onUploadImage(file);
    const alt = uploaded.filename.replace(/[[\]\r\n]/g, "_") || "项目图片";
    const reference = `![${alt}](biaoshu-image://${uploaded.id})`;
    appendMarkdown(targetId, reference);
  };

  const handleCardInsert = (result: InsertCardResult) => {
    if (!selected) return;
    appendMarkdown(selected.id, result.markdown);
  };

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
          <input
            ref={imageInputRef}
            type="file"
            accept="image/png,image/jpeg,image/gif"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              void insertProjectImage(file).catch(() => {
                /* 上传错误由任务流水线统一展示。 */
              });
            }}
          />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            aria-label="插入项目图片"
            title="插入项目图片"
            disabled={imageBusy}
            onClick={() => imageInputRef.current?.click()}
          >
            <ImagePlus size={15} />
          </button>
          {projectId ? (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-label="插入知识卡片"
              title="插入知识卡片"
              disabled={imageBusy}
              onClick={() => setCardDialogOpen(true)}
            >
              <Library size={15} /> 插入卡片
            </button>
          ) : null}
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
            ref={bodyInputRef}
            onChange={(e) => onChangeBody(selected.id, e.target.value)}
            placeholder="在此编辑本章 Markdown 正文。可用下方「按反馈调整」让 AI 定向修订……"
            aria-label={`正文：${selected.title}`}
          />
        )}
      </div>

      {projectId ? (
        <InsertCardDialog
          open={cardDialogOpen}
          projectId={projectId}
          onClose={() => setCardDialogOpen(false)}
          onInsert={handleCardInsert}
        />
      ) : null}
    </div>
  );
}
