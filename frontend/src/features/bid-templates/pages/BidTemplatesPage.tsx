/**
 * 模块：中标内容模板库页
 * 用途：检索/查看 workspace 内技术标内容模板，并从模板创建新项目草稿。
 * 对接：useBidTemplates；/api/templates；技术标工作区大纲步。
 * 二次开发：勿与「导出模板」页合并；多模板融合属阶段 3。
 */

import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  FileStack,
  LoaderCircle,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { useBidTemplates } from "../hooks/useBidTemplates";
import type { BidTemplateSummary } from "../types";
import "./BidTemplatesPage.css";

/**
 * 用途：展示中标内容模板库并支持从模板新建技术标项目。
 * 规则：列表仅消费摘要字段（chapterCount/outlineTitles），不请求完整 snapshot。
 */
export function BidTemplatesPage() {
  const navigate = useNavigate();
  const { items, loading, saving, error, refresh, createProject, remove } =
    useBidTemplates();
  const [query, setQuery] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return items;
    return items.filter((item) =>
      [item.title, item.sourceProjectName, item.tags.join(" ")]
        .join("\n")
        .toLocaleLowerCase("zh-CN")
        .includes(needle),
    );
  }, [items, query]);

  const handleSearch = (event: FormEvent) => {
    event.preventDefault();
    void refresh(query);
  };

  const handleCreate = async (template: BidTemplateSummary) => {
    setActionError(null);
    try {
      const project = await createProject(template.id, {
        name: `${template.title} · 新建`,
      });
      navigate(`/technical-plan/${project.id}/outline`);
    } catch (reason) {
      setActionError(
        (reason as { message?: string }).message || "从模板创建项目失败",
      );
    }
  };

  const handleRemove = async (template: BidTemplateSummary) => {
    if (!window.confirm(`确定删除模板「${template.title}」？不影响任何项目。`)) {
      return;
    }
    setActionError(null);
    try {
      await remove(template.id);
      if (expandedId === template.id) setExpandedId(null);
    } catch (reason) {
      setActionError(
        (reason as { message?: string }).message || "删除模板失败",
      );
    }
  };

  return (
    <div className="page bid-tpl-page">
      <header className="page-header">
        <div>
          <h1>中标内容模板</h1>
          <p>
            沉淀技术标大纲与章节快照，供同工作空间检索与「从模板新建」草稿。
            与「导出模板」（Word 版式）相互独立。
          </p>
        </div>
      </header>

      <div className="bid-tpl-banner" role="note">
        模板是独立快照：删除源项目后仍可复用；从模板新建只复制到新项目，不覆盖已有正文。
      </div>

      <div className="bid-tpl-toolbar">
        <form onSubmit={handleSearch} role="search" aria-label="搜索中标内容模板">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="按标题、标签、来源项目搜索"
            aria-label="模板关键词"
          />
          <button type="submit" className="btn btn-ghost btn-sm">
            <Search size={16} /> 搜索
          </button>
        </form>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => void refresh(query)}
        >
          刷新
        </button>
      </div>

      {(error || actionError) && (
        <p className="bid-tpl-inline-error" role="alert">
          {actionError || error}
        </p>
      )}

      {loading ? (
        <LoadingBlock label="加载中标内容模板…" />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<FileStack size={28} />}
          title="暂无中标内容模板"
          description="在技术标工作区点击「沉淀为模板」，将大纲与章节保存为可复用快照。"
        />
      ) : (
        <div className="bid-tpl-grid" role="list" aria-label="中标内容模板列表">
          {list.map((item) => {
            const titles = item.outlineTitles || [];
            const chapters = item.chapterCount ?? 0;
            const expanded = expandedId === item.id;
            return (
              <article
                key={item.id}
                className="bid-tpl-card"
                role="listitem"
                aria-label={`模板 ${item.title}`}
              >
                <h2 className="bid-tpl-card__title">{item.title}</h2>
                <div className="bid-tpl-card__meta">
                  <span>章节 {chapters} 个</span>
                  <span>
                    来源：
                    {item.sourceProjectName || "（源项目已删除）"}
                  </span>
                  <span className="mono">{item.status}</span>
                </div>
                {item.tags.length > 0 && (
                  <div className="bid-tpl-tags" aria-label="标签">
                    {item.tags.map((tag) => (
                      <span key={tag} className="bid-tpl-tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="bid-tpl-card__actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    aria-label={`从模板新建 ${item.title}`}
                    disabled={saving}
                    onClick={() => void handleCreate(item)}
                  >
                    {saving ? (
                      <LoaderCircle size={14} className="spin" />
                    ) : (
                      <Sparkles size={14} />
                    )}{" "}
                    从模板新建
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    aria-expanded={expanded}
                    aria-label={`查看模板 ${item.title} 大纲`}
                    onClick={() =>
                      setExpandedId(expanded ? null : item.id)
                    }
                  >
                    {expanded ? "收起" : "查看大纲"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    aria-label={`删除模板 ${item.title}`}
                    disabled={saving}
                    onClick={() => void handleRemove(item)}
                  >
                    <Trash2 size={14} /> 删除
                  </button>
                </div>
                {expanded && (
                  <div className="bid-tpl-detail" role="region" aria-label="模板大纲预览">
                    <strong>大纲预览</strong>
                    {titles.length === 0 ? (
                      <p>无大纲标题</p>
                    ) : (
                      <ul>
                        {titles.map((title) => (
                          <li key={title}>{title}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
