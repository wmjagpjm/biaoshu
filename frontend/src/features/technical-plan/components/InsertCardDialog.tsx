/**
 * 模块：章节「插入卡片」对话框
 * 用途：检索 workspace 卡片，调用 insert-card 取得 Markdown 后由父级追加正文。
 * 对接：/api/cards；POST /api/projects/{id}/insert-card；ChapterEditor。
 * 二次开发：不得自动覆盖章节正文；图片结果必须是 biaoshu-image://file_*。
 */

import { useEffect, useState } from "react";
import { LoaderCircle, Search } from "lucide-react";
import {
  getCard,
  insertCardIntoProject,
  listCards,
  cardContentUrl,
} from "../../knowledge-base/api/cardsApi";
import type {
  InsertCardResult,
  KnowledgeCardSummary,
  KnowledgeCardType,
} from "../../knowledge-base/types";
import { CARD_TYPE_LABEL } from "../../knowledge-base/types";

export type InsertCardDialogProps = {
  open: boolean;
  projectId: string;
  onClose: () => void;
  onInsert: (result: InsertCardResult) => void;
};

export function InsertCardDialog({
  open,
  projectId,
  onClose,
  onInsert,
}: InsertCardDialogProps) {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<KnowledgeCardType | "">("");
  const [items, setItems] = useState<KnowledgeCardSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<KnowledgeCardSummary | null>(null);
  const [previewBody, setPreviewBody] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listCards({ q: query, type: typeFilter, status: "active" })
      .then((data) => {
        if (!cancelled) setItems(data);
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(
            (reason as { message?: string }).message || "加载卡片失败",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, query, typeFilter]);

  useEffect(() => {
    if (!preview) {
      setPreviewBody("");
      return;
    }
    if (preview.type === "image") {
      setPreviewBody("");
      return;
    }
    let cancelled = false;
    void getCard(preview.id)
      .then((detail) => {
        if (!cancelled) setPreviewBody(detail.bodyMarkdown || "");
      })
      .catch(() => {
        if (!cancelled) setPreviewBody("");
      });
    return () => {
      cancelled = true;
    };
  }, [preview]);

  if (!open) return null;

  const handleInsert = async (card: KnowledgeCardSummary) => {
    setBusyId(card.id);
    setError(null);
    try {
      const result = await insertCardIntoProject(projectId, card.id);
      onInsert(result);
      onClose();
    } catch (reason) {
      setError((reason as { message?: string }).message || "插入卡片失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15, 23, 42, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 80,
        padding: 16,
      }}
    >
      <div
        className="card card-pad"
        role="dialog"
        aria-modal="true"
        aria-label="插入知识卡片"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(720px, 100%)", maxHeight: "85vh", overflow: "auto" }}
      >
        <header style={{ marginBottom: 12 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>插入知识卡片</h2>
          <p style={{ margin: "6px 0 0", color: "var(--text-secondary)", fontSize: 13 }}>
            选择卡片后追加到当前章节光标处；图片会复制为项目内受控引用。
          </p>
        </header>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 140px",
            gap: 10,
            marginBottom: 12,
          }}
        >
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="insert-card-q">检索</label>
            <div style={{ position: "relative" }}>
              <Search
                size={15}
                style={{
                  position: "absolute",
                  left: 10,
                  top: 12,
                  color: "var(--text-tertiary)",
                }}
              />
              <input
                id="insert-card-q"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="标题、标签、来源…"
                style={{ paddingLeft: 32 }}
                aria-label="检索卡片"
              />
            </div>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="insert-card-type">类型</label>
            <select
              id="insert-card-type"
              value={typeFilter}
              onChange={(e) =>
                setTypeFilter((e.target.value || "") as KnowledgeCardType | "")
              }
              aria-label="卡片类型筛选"
            >
              <option value="">全部</option>
              <option value="document">文档片段</option>
              <option value="image">图片</option>
              <option value="qualification">资质</option>
              <option value="performance">业绩</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="hint-banner" role="alert" style={{ marginBottom: 10 }}>
            {error}
          </div>
        )}

        {loading ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <LoaderCircle size={16} className="spin" /> 加载中…
          </div>
        ) : items.length === 0 ? (
          <p style={{ color: "var(--text-secondary)" }}>
            暂无可用卡片。请先在「知识库」创建或上传。
          </p>
        ) : (
          <ul
            style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}
            aria-label="可插入卡片列表"
          >
            {items.map((card) => (
              <li
                key={card.id}
                className="card"
                style={{ padding: 12 }}
                aria-label={`卡片 ${card.title}`}
              >
                <div
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <strong>{card.title}</strong>
                      <span className="badge badge-muted">
                        {CARD_TYPE_LABEL[card.type]}
                      </span>
                    </div>
                    <div
                      className="mono"
                      style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}
                    >
                      {card.sourceLabel || "无来源标签"} · {card.summary || "无摘要"}
                    </div>
                    {card.type === "image" && (
                      <img
                        src={cardContentUrl(card.id)}
                        alt={card.title}
                        style={{
                          marginTop: 8,
                          maxWidth: 160,
                          maxHeight: 96,
                          objectFit: "contain",
                          borderRadius: 6,
                          border: "1px solid var(--border)",
                        }}
                      />
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setPreview(card)}
                    >
                      预览
                    </button>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={busyId === card.id}
                      aria-label={`插入卡片 ${card.title}`}
                      onClick={() => void handleInsert(card)}
                    >
                      {busyId === card.id ? "插入中…" : "插入"}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        {preview && (
          <div
            className="card card-pad"
            style={{ marginTop: 12, background: "var(--hover-bg)" }}
            aria-label="卡片预览"
          >
            <strong>{preview.title}</strong>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
              来源：{preview.sourceLabel || "—"}
            </div>
            {preview.type === "image" ? (
              <img
                src={cardContentUrl(preview.id)}
                alt={preview.title}
                style={{ marginTop: 8, maxWidth: "100%", maxHeight: 220 }}
              />
            ) : (
              <pre
                className="mono"
                style={{
                  marginTop: 8,
                  whiteSpace: "pre-wrap",
                  fontSize: 12,
                  maxHeight: 180,
                  overflow: "auto",
                }}
              >
                {previewBody || preview.summary || "（无正文）"}
              </pre>
            )}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ marginTop: 8 }}
              onClick={() => setPreview(null)}
            >
              关闭预览
            </button>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
