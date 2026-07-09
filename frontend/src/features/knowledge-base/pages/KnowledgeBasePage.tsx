import { useMemo, useState, type DragEvent } from "react";
import {
  BookOpen,
  FolderPlus,
  ImageIcon,
  Images,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { imageCategories, mockDocs, mockImages } from "../mock";
import type { KbTab, KnowledgeImage } from "../types";
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

/**
 * 知识库页
 * 用途：文档知识库 + 图片知识库（B 端补齐图文素材管理，供正文配图/架构图复用）。
 */
export function KnowledgeBasePage() {
  const [tab, setTab] = useState<KbTab>("documents");
  const [docQuery, setDocQuery] = useState("");
  const [imgQuery, setImgQuery] = useState("");
  const [imgCategory, setImgCategory] = useState("全部");
  const [userImages, setUserImages] = useState<KnowledgeImage[]>(() => loadUserImages());
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<KnowledgeImage | null>(null);

  const allImages = useMemo(
    () => [...userImages, ...mockImages],
    [userImages],
  );

  const filteredDocs = useMemo(() => {
    const q = docQuery.trim().toLowerCase();
    if (!q) return mockDocs;
    return mockDocs.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        d.tags.some((t) => t.includes(q)) ||
        d.category.includes(q),
    );
  }, [docQuery]);

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

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>知识库</h1>
          <p>
            文档知识库沉淀方案与规范；图片知识库管理架构图、部署图、效果图等配图素材，
            正文生成时可检索引用（后端 RAG / 配图链路待接）。
          </p>
        </div>
        <div className="page-actions">
          {tab === "documents" ? (
            <>
              <button type="button" className="btn btn-ghost">
                <FolderPlus size={16} /> 新建分类
              </button>
              <button type="button" className="btn btn-primary">
                <Upload size={16} /> 上传文档
              </button>
            </>
          ) : (
            <>
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
            </>
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
          <span className="badge badge-muted">{mockDocs.length}</span>
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
        <>
          <div className="card card-pad" style={{ marginBottom: 16 }}>
            <div className="field">
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
                  value={docQuery}
                  onChange={(e) => setDocQuery(e.target.value)}
                  placeholder="例如：等保、信创部署、视频接入规模…"
                  style={{ paddingLeft: 36 }}
                />
              </div>
            </div>
          </div>

          <div className="card" style={{ overflow: "hidden" }}>
            <table className="project-table">
              <thead>
                <tr>
                  <th>资料</th>
                  <th>分类</th>
                  <th>标签</th>
                  <th>分块</th>
                  <th>更新</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredDocs.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <BookOpen size={16} color="var(--primary)" />
                        <strong>{d.name}</strong>
                      </div>
                    </td>
                    <td>{d.category}</td>
                    <td>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {d.tags.map((t) => (
                          <span key={t} className="badge badge-muted">
                            {t}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="mono">{d.chunks}</td>
                    <td>{d.updated}</td>
                    <td>
                      <button type="button" className="btn btn-ghost btn-sm">
                        管理
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
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
              if (e.key === "Enter") document.getElementById("kb-image-input")?.click();
            }}
          >
            <ImageIcon size={28} color="var(--primary)" />
            <strong>拖拽图片到此处，或点击上传</strong>
            <p>支持 PNG / JPG / WEBP / GIF；建议单张 ≤ 10MB。上传后可供技术标配图引用。</p>
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
                      <ImageIcon size={36} style={{ position: "relative", zIndex: 1 }} />
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
                <div style={{ color: "#94a3b8", textAlign: "center", padding: 24 }}>
                  <ImageIcon size={48} />
                  <p>演示占位图（上传本地图片可预览真实内容）</p>
                </div>
              )}
            </div>
            <div className="kb-modal__side">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
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
                <div style={{ fontSize: "var(--fs-sm)", color: "var(--text-body)" }}>
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
                    window.alert("演示：已加入当前项目配图候选（后端待接）。")
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
