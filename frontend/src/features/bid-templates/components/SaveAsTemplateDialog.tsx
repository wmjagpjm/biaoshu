/**
 * 模块：沉淀为模板对话框
 * 用途：在技术标工作区输入模板名称与可选标签，调用 from-project API。
 * 对接：TechnicalPlanWorkspace；useBidTemplates.saveFromProject。
 * 二次开发：保持 role/aria-label 稳定，供 E2E 定位。
 */

import { useState, type FormEvent } from "react";
import { LoaderCircle } from "lucide-react";
import { apiFetch } from "../../../shared/lib/api";
import type { BidTemplate, SaveAsTemplateDraft } from "../types";
import "../pages/BidTemplatesPage.css";

type Props = {
  projectId: string;
  defaultTitle: string;
  open: boolean;
  onClose: () => void;
  /** 用途：沉淀成功回调；响应含完整 snapshot，调用方勿直接塞入列表缓存。 */
  onSaved?: (template: BidTemplate) => void;
};

function parseTags(tagsText: string): string[] {
  return tagsText
    .split(/[，,\n]/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 20);
}

/**
 * 用途：模态表单沉淀当前项目大纲/章节为中标内容模板。
 */
export function SaveAsTemplateDialog({
  projectId,
  defaultTitle,
  open,
  onClose,
  onSaved,
}: Props) {
  const [draft, setDraft] = useState<SaveAsTemplateDraft>({
    title: defaultTitle,
    tagsText: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const item = await apiFetch<BidTemplate>("/templates/from-project", {
        method: "POST",
        body: JSON.stringify({
          projectId,
          title: draft.title.trim() || undefined,
          tags: parseTags(draft.tagsText),
        }),
      });
      onSaved?.(item);
      onClose();
    } catch (reason) {
      setError((reason as { message?: string }).message || "沉淀模板失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="bid-tpl-dialog-backdrop"
      role="presentation"
      onClick={() => {
        if (!saving) onClose();
      }}
    >
      <div
        className="bid-tpl-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="沉淀为中标内容模板"
        onClick={(event) => event.stopPropagation()}
      >
        <h2>沉淀为中标内容模板</h2>
        <p>将当前项目的大纲与章节深拷贝为工作空间内独立快照，不影响源项目。</p>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            模板名称
            <input
              value={draft.title}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, title: event.target.value }))
              }
              placeholder="例如：某市信息化中标模板"
              aria-label="模板名称"
              required
              autoFocus
            />
          </label>
          <label>
            标签（可选，逗号分隔）
            <input
              value={draft.tagsText}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, tagsText: event.target.value }))
              }
              placeholder="政务，安全"
              aria-label="模板标签"
            />
          </label>
          {error && (
            <p className="bid-tpl-inline-error" role="alert">
              {error}
            </p>
          )}
          <div className="bid-tpl-dialog__actions">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              aria-label="确认沉淀为模板"
              disabled={saving}
            >
              {saving ? <LoaderCircle size={14} /> : null} 确认沉淀
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
