/**
 * 模块：资源中心页
 * 用途：展示系统精选与用户资源，支持搜索、详情、用户资源维护和服务端浏览量记录。
 * 对接：useResources；/api/resources；资源中心类型与样式。
 * 二次开发：受控外部来源应落入后端资源库后复用本页；不得恢复 mock、浏览器任意 URL 请求或前端浏览量加一。
 */

import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Library,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { resourceToDraft, useResources } from "../hooks/useResources";
import type { ResourceDraft, ResourceItem, ResourceTone } from "../types";
import "./ResourcesPage.css";

type EditingState = {
  resource?: ResourceItem;
  draft: ResourceDraft;
};

const TONES: ResourceTone[] = ["blue", "violet", "cyan", "slate"];

function updateDraft(
  draft: ResourceDraft,
  key: keyof ResourceDraft,
  value: string,
): ResourceDraft {
  return { ...draft, [key]: value };
}

export function ResourcesPage() {
  const {
    items,
    loading,
    saving,
    error,
    refresh,
    save,
    remove,
    recordView,
  } = useResources();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ResourceItem | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);

  useEffect(() => {
    if (!selected && !editing) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || saving) return;
      setSelected(null);
      setEditing(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [editing, saving, selected]);

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
    if (!normalizedQuery) return items;
    return items.filter((item) =>
      [item.title, item.description, item.category, item.tags.join(" ")]
        .join("\n")
        .toLocaleLowerCase("zh-CN")
        .includes(normalizedQuery),
    );
  }, [items, query]);

  const closeEditor = () => {
    if (!saving) setEditing(null);
  };

  const handleSearch = (event: FormEvent) => {
    event.preventDefault();
    void refresh();
  };

  const openResource = async (resource: ResourceItem) => {
    try {
      const updated = await recordView(resource.id);
      setSelected(updated);
    } catch {
      // 错误已由 Hook 回显。
    }
  };

  const submitEditor = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    try {
      await save(editing.draft, editing.resource?.id);
      setEditing(null);
    } catch {
      // 错误已由 Hook 回显。
    }
  };

  const deleteResource = async (resource: ResourceItem) => {
    if (!window.confirm(`删除资源“${resource.title}”？`)) return;
    try {
      await remove(resource.id);
      if (selected?.id === resource.id) setSelected(null);
    } catch {
      // 错误已由 Hook 回显。
    }
  };

  return (
    <div className="page res-page">
      <header className="page-header">
        <div>
          <h1>资源中心</h1>
          <p>系统精选与当前工作空间的资料。</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={saving}
          onClick={() => setEditing({ draft: resourceToDraft() })}
        >
          <Plus size={16} /> 新增资源
        </button>
      </header>

      <form className="res-toolbar card card-pad" onSubmit={handleSearch}>
        <div className="field">
          <label htmlFor="res-q">搜索资源</label>
          <div className="res-search-input">
            <Search size={16} aria-hidden="true" />
            <input
              id="res-q"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="标题、标签、描述…"
            />
          </div>
        </div>
        <button type="submit" className="btn btn-soft" disabled={loading}>
          {loading ? "加载中" : "刷新"}
        </button>
        <span className="res-count" aria-live="polite">
          {loading ? "" : `共 ${filtered.length} 条`}
        </span>
      </form>

      {error && (
        <div className="res-error" role="alert">
          <span>{error}</span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void refresh()}
          >
            重试
          </button>
        </div>
      )}

      {loading && items.length === 0 ? (
        <LoadingBlock label="加载资源…" />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Library size={28} />}
          title="没有匹配资源"
          description="调整关键词，或新增一条资源。"
        />
      ) : (
        <div className="res-grid" aria-busy={loading}>
          {filtered.map((item) => (
            <article key={item.id} className="res-card">
              <button
                type="button"
                className="res-card__main"
                onClick={() => void openResource(item)}
                aria-label={`查看资源：${item.title}`}
                disabled={saving}
              >
                <div className={`res-card__cover is-${item.tone}`}>
                  <span className="res-card__kicker">{item.category || "资源"}</span>
                </div>
                <div className="res-card__body">
                  <h2 className="res-card__title">{item.title}</h2>
                  <p className="res-card__desc">{item.description || "暂无摘要"}</p>
                  <div className="res-card__tags">
                    {item.tags.map((tag) => (
                      <span key={tag} className="badge badge-muted">
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="res-card__meta mono">
                    <span>{item.source === "system" ? "系统精选" : "我的资源"}</span>
                    <span>浏览 {item.viewCount.toLocaleString("zh-CN")}</span>
                  </div>
                </div>
              </button>
              {item.source === "user" && (
                <div className="res-card__actions">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm res-icon-button"
                    aria-label={`编辑：${item.title}`}
                    title="编辑资源"
                    disabled={saving}
                    onClick={() =>
                      setEditing({ resource: item, draft: resourceToDraft(item) })
                    }
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm res-icon-button"
                    aria-label={`删除：${item.title}`}
                    title="删除资源"
                    disabled={saving}
                    onClick={() => void deleteResource(item)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}

      {selected && (
        <div className="res-modal-mask" onMouseDown={() => setSelected(null)}>
          <section
            className="res-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="res-modal-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="res-modal__head">
              <div>
                <h2 id="res-modal-title">{selected.title}</h2>
                <div className="res-modal__badges">
                  {selected.tags.map((tag) => (
                    <span key={tag} className="badge badge-primary">
                      {tag}
                    </span>
                  ))}
                  <span className="badge badge-muted mono">
                    浏览 {selected.viewCount.toLocaleString("zh-CN")}
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm res-icon-button"
                aria-label="关闭"
                title="关闭"
                onClick={() => setSelected(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="res-modal__body">
              {selected.description && <p>{selected.description}</p>}
              <pre className="res-modal__content">{selected.bodyMarkdown}</pre>
            </div>
          </section>
        </div>
      )}

      {editing && (
        <div className="res-modal-mask" onMouseDown={closeEditor}>
          <form
            className="res-modal res-editor"
            role="dialog"
            aria-modal="true"
            aria-labelledby="res-editor-title"
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={submitEditor}
          >
            <div className="res-modal__head">
              <div>
                <h2 id="res-editor-title">
                  {editing.resource ? "编辑资源" : "新增资源"}
                </h2>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm res-icon-button"
                aria-label="关闭"
                title="关闭"
                disabled={saving}
                onClick={closeEditor}
              >
                <X size={16} />
              </button>
            </div>
            <div className="res-editor__fields">
              <label className="field">
                <span>标题</span>
                <input
                  required
                  value={editing.draft.title}
                  onChange={(event) =>
                    setEditing((current) =>
                      current && {
                        ...current,
                        draft: updateDraft(current.draft, "title", event.target.value),
                      },
                    )
                  }
                />
              </label>
              <label className="field">
                <span>分类</span>
                <input
                  value={editing.draft.category}
                  onChange={(event) =>
                    setEditing((current) =>
                      current && {
                        ...current,
                        draft: updateDraft(current.draft, "category", event.target.value),
                      },
                    )
                  }
                />
              </label>
              <label className="field res-editor__full">
                <span>摘要</span>
                <textarea
                  value={editing.draft.description}
                  onChange={(event) =>
                    setEditing((current) =>
                      current && {
                        ...current,
                        draft: updateDraft(
                          current.draft,
                          "description",
                          event.target.value,
                        ),
                      },
                    )
                  }
                />
              </label>
              <label className="field">
                <span>标签</span>
                <input
                  value={editing.draft.tagsText}
                  onChange={(event) =>
                    setEditing((current) =>
                      current && {
                        ...current,
                        draft: updateDraft(current.draft, "tagsText", event.target.value),
                      },
                    )
                  }
                  placeholder="以逗号分隔"
                />
              </label>
              <div className="field">
                <span>封面色调</span>
                <div className="res-tone-picker" role="radiogroup" aria-label="封面色调">
                  {TONES.map((tone) => (
                    <button
                      key={tone}
                      type="button"
                      className={`res-tone-swatch is-${tone}${
                        editing.draft.tone === tone ? " is-selected" : ""
                      }`}
                      role="radio"
                      aria-checked={editing.draft.tone === tone}
                      aria-label={`选择${tone}色调`}
                      title={`选择${tone}色调`}
                      onClick={() =>
                        setEditing((current) =>
                          current && {
                            ...current,
                            draft: { ...current.draft, tone },
                          },
                        )
                      }
                    />
                  ))}
                </div>
              </div>
              <label className="field res-editor__full">
                <span>正文 Markdown</span>
                <textarea
                  required
                  className="res-editor__body"
                  value={editing.draft.bodyMarkdown}
                  onChange={(event) =>
                    setEditing((current) =>
                      current && {
                        ...current,
                        draft: updateDraft(
                          current.draft,
                          "bodyMarkdown",
                          event.target.value,
                        ),
                      },
                    )
                  }
                />
              </label>
            </div>
            <div className="res-editor__actions">
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saving}
                onClick={closeEditor}
              >
                取消
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? <LoaderCircle className="res-spin" size={16} /> : null}
                保存资源
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
