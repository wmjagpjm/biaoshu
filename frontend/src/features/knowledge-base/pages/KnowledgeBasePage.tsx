import { useMemo, useRef, useState, type DragEvent } from "react";
import {
  BookOpen,
  FolderInput,
  ImageIcon,
  Images,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { FolderTree } from "../components/FolderTree";
import { useKnowledgeBase } from "../hooks/useKnowledgeBase";
import { imageCategories, mockImages } from "../mock";
import type { DocParseStatus, KnowledgeImage } from "../types";
import { DOC_STATUS_LABEL } from "../types";
import "./KnowledgeBase.css";

const STORAGE_KEY = "biaoshu.knowledgeImages.v1";

function loadUserImages(): KnowledgeImage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as KnowledgeImage[];
  } catch {
    return [];
  }
}

function saveUserImages(list: KnowledgeImage[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

function statusClass(status: DocParseStatus): string {
  if (status === "ready") return "is-ready";
  if (status === "failed") return "is-failed";
  if (status === "pending") return "is-pending";
  return "is-busy";
}

/**
 * 模块：知识库页
 * 用途：文档（文件夹树 + 状态筛选 + 批量操作 + 上传索引）+ 图片素材库。
 * 对接：文档走 /api/knowledge；图片库仍 localStorage。
 */
export function KnowledgeBasePage() {
  const [tab, setTab] = useState<"documents" | "images">("documents");
  const kb = useKnowledgeBase();
  const docFileRef = useRef<HTMLInputElement>(null);

  const [imgQuery, setImgQuery] = useState("");
  const [imgCategory, setImgCategory] = useState("全部");
  const [userImages, setUserImages] = useState<KnowledgeImage[]>(() =>
    loadUserImages(),
  );
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<KnowledgeImage | null>(null);
  const [moveTarget, setMoveTarget] = useState("");

  const allImages = useMemo(
    () => [...userImages, ...mockImages],
    [userImages],
  );

  const filteredImages = useMemo(() => {
    const q = imgQuery.trim().toLowerCase();
    return allImages.filter((img) => {
      if (imgCategory !== "全部" && img.category !== imgCategory) return false;
      if (!q) return true;
      return (
        img.name.toLowerCase().includes(q) ||
        img.caption.toLowerCase().includes(q) ||
        img.tags.some((t) => t.toLowerCase().includes(q)) ||
        img.category.includes(q)
      );
    });
  }, [allImages, imgCategory, imgQuery]);

  const allFilteredSelected =
    kb.filteredDocs.length > 0 &&
    kb.filteredDocs.every((d) => kb.selectedIds.includes(d.id));

  function addImagesFromFiles(files: FileList | File[]) {
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (list.length === 0) {
      window.alert("请选择图片文件（png / jpg / webp / gif 等）");
      return;
    }

    const readers = list.map(
      (file) =>
        new Promise<KnowledgeImage>((resolve) => {
          const reader = new FileReader();
          reader.onload = () => {
            const url = String(reader.result || "");
            resolve({
              id: `user_img_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
              name: file.name,
              thumbUrl: url,
              url,
              tags: ["本地上传"],
              category: "未分类",
              sizeLabel: formatSize(file.size),
              caption: file.name.replace(/\.[^.]+$/, ""),
              updatedAt: new Date().toISOString(),
            });
          };
          reader.readAsDataURL(file);
        }),
    );

    void Promise.all(readers).then((items) => {
      setUserImages((prev) => {
        const next = [...items, ...prev];
        saveUserImages(next);
        return next;
      });
      setTab("images");
    });
  }

  function removeUserImage(id: string) {
    if (!id.startsWith("user_img_")) {
      window.alert("演示内置图片不可删除，可删除你上传的图片。");
      return;
    }
    if (!window.confirm("确定从图片知识库移除该图片？")) return;
    setUserImages((prev) => {
      const next = prev.filter((i) => i.id !== id);
      saveUserImages(next);
      return next;
    });
    if (preview?.id === id) setPreview(null);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) {
      addImagesFromFiles(e.dataTransfer.files);
    }
  }

  function handleBatchMove() {
    if (!moveTarget || !kb.selectedIds.length) return;
    kb.moveDocs(kb.selectedIds, moveTarget);
    setMoveTarget("");
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>知识库</h1>
          <p>
            文档按文件夹管理并展示解析/索引状态；图片库管理架构图等配图素材。
            大纲/正文生成时自动关键词检索知识库参考
            {kb.source === "api" ? "（已接后端）" : "（当前离线本地演示）"}。
          </p>
        </div>
        <div className="page-actions">
          {tab === "documents" ? (
            <>
              <input
                ref={docFileRef}
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown,application/pdf"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) {
                    void kb.uploadFiles(e.target.files);
                  }
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                className="btn btn-ghost"
                disabled={kb.busy}
                onClick={() => void kb.refresh()}
                title="刷新列表"
              >
                <RefreshCw size={16} /> 刷新
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={kb.busy}
                onClick={() => docFileRef.current?.click()}
                title="上传并同步解析分块"
              >
                <Upload size={16} /> {kb.busy ? "处理中…" : "上传文档"}
              </button>
            </>
          ) : (
            <label className="btn btn-primary" style={{ cursor: "pointer" }}>
              <Upload size={16} /> 上传图片
              <input
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) addImagesFromFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>
          )}
        </div>
      </header>

      <nav className="kb-tabs" aria-label="知识库类型">
        <button
          type="button"
          className={`kb-tab${tab === "documents" ? " is-active" : ""}`}
          onClick={() => setTab("documents")}
        >
          <BookOpen size={16} /> 文档知识库
          <span className="badge badge-muted">{kb.totalDocCount}</span>
        </button>
        <button
          type="button"
          className={`kb-tab${tab === "images" ? " is-active" : ""}`}
          onClick={() => setTab("images")}
        >
          <Images size={16} /> 图片知识库
          <span className="badge badge-muted">{allImages.length}</span>
        </button>
      </nav>

      {tab === "documents" && (
        <div className="kb-docs-layout">
          <FolderTree
            folders={kb.folders}
            counts={kb.folderCounts}
            totalCount={kb.totalDocCount}
            selectedId={kb.selectedFolderId}
            onSelect={kb.setSelectedFolderId}
            onCreate={(name) => {
              void kb.createFolder(name);
            }}
          />

          <div className="kb-docs-main">
            {kb.error && (
              <div className="hint-banner" role="status" style={{ marginBottom: 12 }}>
                {kb.error}
              </div>
            )}
            <div className="kb-docs-toolbar card card-pad">
              <div className="field" style={{ margin: 0, flex: 1, minWidth: 180 }}>
                <label htmlFor="kb-search">检索文档</label>
                <div style={{ position: "relative" }}>
                  <Search
                    size={16}
                    style={{
                      position: "absolute",
                      left: 12,
                      top: 14,
                      color: "var(--text-tertiary)",
                    }}
                  />
                  <input
                    id="kb-search"
                    value={kb.docQuery}
                    onChange={(e) => kb.setDocQuery(e.target.value)}
                    placeholder="名称、标签、状态说明…"
                    style={{ paddingLeft: 36 }}
                  />
                </div>
              </div>
              <div className="field" style={{ margin: 0, minWidth: 140 }}>
                <label htmlFor="kb-status">状态</label>
                <select
                  id="kb-status"
                  value={kb.statusFilter}
                  onChange={(e) =>
                    kb.setStatusFilter(
                      e.target.value as DocParseStatus | "all",
                    )
                  }
                >
                  <option value="all">全部状态</option>
                  <option value="ready">已就绪</option>
                  <option value="parsing">解析中</option>
                  <option value="indexing">索引中</option>
                  <option value="failed">失败</option>
                  <option value="pending">待处理</option>
                </select>
              </div>
            </div>

            {kb.selectedIds.length > 0 && (
              <div className="kb-batch-bar">
                <span>
                  已选 <strong>{kb.selectedIds.length}</strong> 项
                </span>
                <div className="kb-batch-bar__actions">
                  <select
                    value={moveTarget}
                    onChange={(e) => setMoveTarget(e.target.value)}
                    aria-label="移入文件夹"
                  >
                    <option value="">移入文件夹…</option>
                    {kb.folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn btn-soft btn-sm"
                    disabled={!moveTarget}
                    onClick={handleBatchMove}
                  >
                    <FolderInput size={14} /> 移动
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={kb.busy}
                    onClick={() => void kb.retryParse(kb.selectedIds)}
                  >
                    <RefreshCw size={14} /> 重试索引
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={kb.busy}
                    onClick={() => {
                      if (
                        window.confirm(
                          `确定删除选中的 ${kb.selectedIds.length} 个文档？不可恢复。`,
                        )
                      ) {
                        void kb.deleteDocs(kb.selectedIds);
                      }
                    }}
                  >
                    <Trash2 size={14} /> 删除
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={kb.clearSelection}
                  >
                    取消选择
                  </button>
                </div>
              </div>
            )}

            <div className="card" style={{ overflow: "hidden" }}>
              <table className="project-table kb-doc-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>
                      <input
                        type="checkbox"
                        checked={allFilteredSelected}
                        onChange={kb.toggleSelectAllFiltered}
                        aria-label="全选当前列表"
                      />
                    </th>
                    <th>资料</th>
                    <th>状态</th>
                    <th>文件夹</th>
                    <th>标签</th>
                    <th>分块</th>
                    <th>更新</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {kb.filteredDocs.length === 0 ? (
                    <tr>
                      <td colSpan={8} style={{ padding: 0 }}>
                        <EmptyState
                          title="当前筛选下无文档"
                          description="切换文件夹、状态或关键词后再试。"
                        />
                      </td>
                    </tr>
                  ) : (
                    kb.filteredDocs.map((d) => {
                      const folderName =
                        kb.folders.find((f) => f.id === d.folderId)?.name ??
                        d.category;
                      return (
                        <tr key={d.id}>
                          <td>
                            <input
                              type="checkbox"
                              checked={kb.selectedIds.includes(d.id)}
                              onChange={() => kb.toggleSelect(d.id)}
                              aria-label={`选择 ${d.name}`}
                            />
                          </td>
                          <td>
                            <div
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 8,
                              }}
                            >
                              <BookOpen size={16} color="var(--primary)" />
                              <div>
                                <strong>{d.name}</strong>
                                {d.sizeLabel && (
                                  <div
                                    className="mono"
                                    style={{
                                      fontSize: 11,
                                      color: "var(--text-tertiary)",
                                    }}
                                  >
                                    {d.sizeLabel}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                          <td>
                            <span
                              className={`kb-status-pill ${statusClass(d.status)}`}
                              title={d.statusMessage}
                            >
                              {DOC_STATUS_LABEL[d.status]}
                            </span>
                            {d.statusMessage && (
                              <div className="kb-status-msg">{d.statusMessage}</div>
                            )}
                          </td>
                          <td>{folderName}</td>
                          <td>
                            <div
                              style={{
                                display: "flex",
                                gap: 6,
                                flexWrap: "wrap",
                              }}
                            >
                              {d.tags.map((t) => (
                                <span key={t} className="badge badge-muted">
                                  {t}
                                </span>
                              ))}
                            </div>
                          </td>
                          <td className="mono">{d.chunks || "—"}</td>
                          <td>{d.updated}</td>
                          <td>
                            <div style={{ display: "flex", gap: 4 }}>
                              {(d.status === "failed" ||
                                d.status === "pending" ||
                                d.status === "ready") && (
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-sm"
                                  disabled={kb.busy}
                                  onClick={() => void kb.retryParse([d.id])}
                                  title="重新索引"
                                >
                                  <RefreshCw size={14} />
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={kb.busy}
                                title="删除"
                                onClick={() => {
                                  if (window.confirm(`确定删除「${d.name}」？`)) {
                                    void kb.deleteDocs([d.id]);
                                  }
                                }}
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === "images" && (
        <>
          <div
            className={`kb-upload-zone${dragOver ? " is-drag" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => document.getElementById("kb-image-input")?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter")
                document.getElementById("kb-image-input")?.click();
            }}
          >
            <ImageIcon size={28} color="var(--primary)" />
            <strong>拖拽图片到此处，或点击上传</strong>
            <p>
              支持 PNG / JPG / WEBP / GIF；建议单张 ≤ 10MB。上传后可供技术标配图引用。
            </p>
            <input
              id="kb-image-input"
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) addImagesFromFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          <div className="kb-toolbar">
            <div className="field">
              <label htmlFor="kb-img-search">检索图片</label>
              <div style={{ position: "relative" }}>
                <Search
                  size={16}
                  style={{
                    position: "absolute",
                    left: 12,
                    top: 14,
                    color: "var(--text-tertiary)",
                  }}
                />
                <input
                  id="kb-img-search"
                  value={imgQuery}
                  onChange={(e) => setImgQuery(e.target.value)}
                  placeholder="按文件名、说明、标签检索…"
                  style={{ paddingLeft: 36 }}
                />
              </div>
            </div>
          </div>

          <div className="kb-cats">
            {imageCategories.map((c) => (
              <button
                key={c}
                type="button"
                className={`kb-cat${imgCategory === c ? " is-active" : ""}`}
                onClick={() => setImgCategory(c)}
              >
                {c}
                {c !== "全部" ? "" : ` (${allImages.length})`}
              </button>
            ))}
          </div>

          {filteredImages.length === 0 ? (
            <div className="card empty-state">
              <strong>暂无匹配图片</strong>
              试试更换分类，或上传架构图 / 部署图 / 效果图素材。
            </div>
          ) : (
            <div className="kb-image-grid">
              {filteredImages.map((img) => (
                <article key={img.id} className="kb-image-card">
                  <div
                    className="kb-image-card__thumb"
                    onClick={() => setPreview(img)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") setPreview(img);
                    }}
                  >
                    <div className="kb-image-card__pattern" />
                    {img.url || img.thumbUrl ? (
                      <img src={img.url || img.thumbUrl} alt={img.name} />
                    ) : (
                      <ImageIcon
                        size={36}
                        style={{ position: "relative", zIndex: 1 }}
                      />
                    )}
                    <span className="kb-image-card__badge badge badge-primary">
                      {img.category}
                    </span>
                  </div>
                  <div className="kb-image-card__body">
                    <div className="kb-image-card__name">{img.name}</div>
                    <div className="kb-image-card__caption">{img.caption}</div>
                    <div className="kb-image-card__meta">
                      <span>{img.sizeLabel}</span>
                      {img.width && img.height ? (
                        <span>
                          {img.width}×{img.height}
                        </span>
                      ) : null}
                      {img.tags.slice(0, 2).map((t) => (
                        <span key={t} className="badge badge-muted">
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="kb-image-card__actions">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setPreview(img)}
                    >
                      查看
                    </button>
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      title="后端接入后可插入正文"
                      onClick={() =>
                        window.alert(
                          "演示：标记为「可引用配图」。后端将支持写入章节插图位。",
                        )
                      }
                    >
                      用于配图
                    </button>
                    {img.id.startsWith("user_img_") && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => removeUserImage(img.id)}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}

      {preview && (
        <div
          className="kb-modal-mask"
          onClick={() => setPreview(null)}
          role="presentation"
        >
          <div
            className="kb-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal
            aria-label={preview.name}
          >
            <div className="kb-modal__visual">
              {preview.url || preview.thumbUrl ? (
                <img src={preview.url || preview.thumbUrl} alt={preview.name} />
              ) : (
                <div
                  style={{ color: "#94a3b8", textAlign: "center", padding: 24 }}
                >
                  <ImageIcon size={48} />
                  <p>演示占位图（上传本地图片可预览真实内容）</p>
                </div>
              )}
            </div>
            <div className="kb-modal__side">
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 8,
                }}
              >
                <h3>{preview.name}</h3>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setPreview(null)}
                  aria-label="关闭"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="field">
                <label>分类</label>
                <div>{preview.category}</div>
              </div>
              <div className="field">
                <label>说明（检索/配图用）</label>
                <div
                  style={{
                    fontSize: "var(--fs-sm)",
                    color: "var(--text-body)",
                  }}
                >
                  {preview.caption}
                </div>
              </div>
              <div className="field">
                <label>标签</label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {preview.tags.map((t) => (
                    <span key={t} className="badge badge-muted">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
              <div className="kb-image-card__meta">
                <span>{preview.sizeLabel}</span>
                {preview.width && preview.height ? (
                  <span>
                    {preview.width}×{preview.height}
                  </span>
                ) : null}
                <span>
                  更新 {new Date(preview.updatedAt).toLocaleString("zh-CN")}
                </span>
              </div>
              <div className="kb-modal__actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() =>
                    window.alert(
                      "演示：已加入当前项目配图候选（后端待接）。",
                    )
                  }
                >
                  用于配图
                </button>
                {preview.id.startsWith("user_img_") && (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => removeUserImage(preview.id)}
                  >
                    <Trash2 size={16} /> 删除
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setPreview(null)}
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
