/**
 * 模块：知识库页
 * 用途：文档（文件夹树 + 状态筛选 + 上传索引）+ 素材卡片库 + 后端化图片卡 + P9C 离线语义索引状态面板。
 * 对接：/api/knowledge；/api/knowledge/semantic-index*；/api/cards；useKnowledgeBase / useKnowledgeCards。
 * 二次开发：图片禁止回退 localStorage/data URL；语义索引禁止模型 URL/Token/缓存路径输入；AI 注入与融合属阶段 3。
 */

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
} from "react";
import {
  BookOpen,
  Cpu,
  FolderInput,
  ImageIcon,
  Images,
  Layers,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { cardContentUrl, getCard } from "../api/cardsApi";
import { FolderTree } from "../components/FolderTree";
import { useKnowledgeBase } from "../hooks/useKnowledgeBase";
import { useKnowledgeCards } from "../hooks/useKnowledgeCards";
import type {
  DocParseStatus,
  KnowledgeCard,
  KnowledgeCardSummary,
  KnowledgeCardType,
  KbTab,
} from "../types";
import {
  CARD_TYPE_LABEL,
  DOC_STATUS_LABEL,
  SEMANTIC_FIXED_DIMENSION,
  SEMANTIC_FIXED_MODEL_ID,
  semanticActionLabel,
  semanticDegradeReason,
  semanticStatusLabel,
} from "../types";
import "./KnowledgeBase.css";

/**
 * 用途：格式化语义索引完成时间（本地可读）。
 * 仅接受可解析合法时间；invalid / 路径 / apiKey / NaN 一律精确「—」，
 * 禁止将原始脏串写入 text/title/aria/data/value/placeholder 或历史 DOM。
 */
function formatFinishedAt(iso: string | null | undefined): string {
  if (iso == null || typeof iso !== "string") return "—";
  const trimmed = iso.trim();
  if (!trimmed) return "—";
  const d = new Date(trimmed);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** 文档表最终列数（含服务端 category）；loading/error/empty 的 colSpan 必须一致 */
const DOC_TABLE_COL_COUNT = 9;

function statusClass(status: DocParseStatus): string {
  if (status === "ready") return "is-ready";
  if (status === "failed") return "is-failed";
  if (status === "pending") return "is-pending";
  return "is-busy";
}

function parseTags(raw: string): string[] {
  return raw
    .split(/[，,\n]/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 20);
}

export function KnowledgeBasePage() {
  const [tab, setTab] = useState<KbTab>("documents");
  const kb = useKnowledgeBase();
  const cards = useKnowledgeCards();
  const images = useKnowledgeCards({ fixedType: "image" });
  const docFileRef = useRef<HTMLInputElement>(null);

  const [dragOver, setDragOver] = useState(false);
  const [moveTarget, setMoveTarget] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createType, setCreateType] =
    useState<Exclude<KnowledgeCardType, "image">>("document");
  const [createTitle, setCreateTitle] = useState("");
  const [createBody, setCreateBody] = useState("");
  const [createTags, setCreateTags] = useState("");
  const [createSource, setCreateSource] = useState("手工录入");
  const [createError, setCreateError] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeCard | null>(null);

  const docsReady = kb.docStatus === "ready";
  const docsLocked = !docsReady || kb.busy;

  const allFilteredSelected =
    kb.filteredDocs.length > 0 &&
    kb.filteredDocs.every((d) => kb.selectedIds.includes(d.id));

  // 非 ready 时清空 moveTarget；ready 后若目标 folder 已不存在也置 ""，禁止自动改到另一 folder
  useEffect(() => {
    if (kb.docStatus !== "ready") {
      setMoveTarget("");
      return;
    }
    if (moveTarget && !kb.folders.some((f) => f.id === moveTarget)) {
      setMoveTarget("");
    }
  }, [kb.docStatus, kb.folders, moveTarget]);

  function handleBatchMove() {
    if (docsLocked || !moveTarget || !kb.selectedIds.length) return;
    kb.moveDocs(kb.selectedIds, moveTarget);
    setMoveTarget("");
  }

  async function openDetail(card: KnowledgeCardSummary) {
    try {
      const full = await getCard(card.id);
      setDetail(full);
    } catch {
      setDetail({
        ...card,
        bodyMarkdown: card.summary || "",
        payload: null,
        storedName: null,
      });
    }
  }

  async function handleCreateCard(e: FormEvent) {
    e.preventDefault();
    setCreateError(null);
    try {
      await cards.createText({
        type: createType,
        title: createTitle,
        bodyMarkdown: createBody,
        tags: parseTags(createTags),
        sourceLabel: createSource,
      });
      setCreateOpen(false);
      setCreateTitle("");
      setCreateBody("");
      setCreateTags("");
      setTab("cards");
    } catch (reason) {
      setCreateError(
        (reason as { message?: string }).message || "创建失败",
      );
    }
  }

  function onDropImages(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) {
      void images.uploadImages(e.dataTransfer.files).then(() => setTab("images"));
    }
  }

  const cardList = useMemo(() => cards.items, [cards.items]);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>知识库</h1>
          <p>
            文档按文件夹管理；素材卡片统一沉淀文档片段、资质、业绩与图片，供章节安全引用。
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
                  // 非 ready 时 Hook 亦零请求；此处仍避免无意义派发
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
                disabled={docsLocked}
                onClick={() => docFileRef.current?.click()}
                title="上传并同步解析分块"
              >
                <Upload size={16} /> {kb.busy ? "处理中…" : "上传文档"}
              </button>
            </>
          ) : null}
          {tab === "cards" ? (
            <>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={cards.loading}
                onClick={() => void cards.refresh()}
              >
                <RefreshCw size={16} /> 刷新
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setCreateOpen(true)}
                aria-label="新建文本卡片"
              >
                <Layers size={16} /> 新建卡片
              </button>
            </>
          ) : null}
          {tab === "images" ? (
            <label className="btn btn-primary" style={{ cursor: "pointer" }}>
              <Upload size={16} /> 上传图片卡
              <input
                type="file"
                accept="image/png,image/jpeg,image/gif"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files?.length) {
                    void images.uploadImages(e.target.files);
                  }
                  e.target.value = "";
                }}
              />
            </label>
          ) : null}
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
          className={`kb-tab${tab === "cards" ? " is-active" : ""}`}
          onClick={() => setTab("cards")}
        >
          <Layers size={16} /> 素材卡片
          <span className="badge badge-muted">{cardList.length}</span>
        </button>
        <button
          type="button"
          className={`kb-tab${tab === "images" ? " is-active" : ""}`}
          onClick={() => setTab("images")}
        >
          <Images size={16} /> 图片卡片
          <span className="badge badge-muted">{images.items.length}</span>
        </button>
      </nav>

      {tab === "documents" && (
        <div className="kb-docs-layout">
          {/* FolderTree 无 disabled prop：用原生 fieldset 禁用，覆盖 onCreate 不足的路径 */}
          <fieldset
            disabled={docsLocked}
            style={{
              border: "none",
              margin: 0,
              padding: 0,
              minInlineSize: 0,
            }}
          >
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
          </fieldset>

          <div className="kb-docs-main">
            {kb.error && (
              <div className="hint-banner" role="status" style={{ marginBottom: 12 }}>
                {kb.error}
              </div>
            )}

            {/* P9C：离线语义索引状态面板（固定模型，无配置入口） */}
            <section
              className="kb-semantic-panel card card-pad"
              data-testid="semantic-index-panel"
              aria-label="离线语义索引状态"
            >
              <div className="kb-semantic-panel__head">
                <div className="kb-semantic-panel__title">
                  <Cpu size={16} aria-hidden />
                  <strong>离线语义索引（本机）</strong>
                </div>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  data-testid="semantic-index-rebuild"
                  aria-label={semanticActionLabel(kb.semanticIndex)}
                  disabled={
                    !docsReady ||
                    kb.semanticBusy ||
                    kb.semanticBuilding ||
                    kb.busy
                  }
                  onClick={() => void kb.rebuildSemanticIndex()}
                  title="仅使用本机固定模型，不外发正文"
                >
                  {kb.semanticBusy || kb.semanticBuilding
                    ? "构建中…"
                    : semanticActionLabel(kb.semanticIndex)}
                </button>
              </div>
              <dl className="kb-semantic-panel__meta">
                <div>
                  <dt>模型</dt>
                  {/* 始终展示固定离线模型；禁止回显服务端脏数据 modelId */}
                  <dd data-testid="semantic-index-model">
                    {SEMANTIC_FIXED_MODEL_ID}
                  </dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd data-testid="semantic-index-status">
                    {semanticStatusLabel(kb.semanticIndex)}
                  </dd>
                </div>
                <div>
                  <dt>维度</dt>
                  {/* 始终展示固定 512 维；禁止回显服务端脏数据 dimension */}
                  <dd data-testid="semantic-index-dimension">
                    {SEMANTIC_FIXED_DIMENSION}
                  </dd>
                </div>
                <div>
                  <dt>进度</dt>
                  <dd data-testid="semantic-index-counts">
                    {kb.semanticIndex
                      ? `${kb.semanticIndex.embeddedChunks}/${kb.semanticIndex.totalChunks} 分块`
                      : "0/0 分块"}
                  </dd>
                </div>
                <div>
                  <dt>完成时间</dt>
                  <dd data-testid="semantic-index-finished">
                    {formatFinishedAt(kb.semanticIndex?.finishedAt)}
                  </dd>
                </div>
              </dl>
              {semanticDegradeReason(kb.semanticIndex) ? (
                <p
                  className="kb-semantic-panel__hint"
                  data-testid="semantic-index-degrade"
                  role="status"
                >
                  {semanticDegradeReason(kb.semanticIndex)}
                </p>
              ) : (
                <p
                  className="kb-semantic-panel__hint is-ok"
                  data-testid="semantic-index-degrade"
                  role="status"
                >
                  语义索引已就绪，检索将混合关键词与本机向量。
                </p>
              )}
              {kb.semanticError ? (
                <p
                  className="kb-semantic-panel__error"
                  role="alert"
                  data-testid="semantic-index-error"
                >
                  {kb.semanticError}
                </p>
              ) : null}
            </section>

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

            {docsReady && kb.selectedIds.length > 0 && (
              <div className="kb-batch-bar">
                <span>
                  已选 <strong>{kb.selectedIds.length}</strong> 项
                </span>
                <div className="kb-batch-bar__actions">
                  <select
                    value={moveTarget}
                    onChange={(e) => setMoveTarget(e.target.value)}
                    aria-label="移入文件夹"
                    disabled={docsLocked}
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
                    disabled={docsLocked || !moveTarget}
                    onClick={handleBatchMove}
                  >
                    <FolderInput size={14} /> 移动
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={docsLocked}
                    onClick={() => void kb.retryParse(kb.selectedIds)}
                  >
                    <RefreshCw size={14} /> 重试索引
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={docsLocked}
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
                        disabled={!docsReady}
                      />
                    </th>
                    <th>资料</th>
                    <th>状态</th>
                    <th>文件夹</th>
                    <th>标签</th>
                    <th>分块</th>
                    <th>分类</th>
                    <th>更新</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {kb.docStatus === "loading" ? (
                    <tr>
                      <td colSpan={DOC_TABLE_COL_COUNT} style={{ padding: 24 }}>
                        正在加载知识库文档…
                      </td>
                    </tr>
                  ) : kb.docStatus === "error" ? (
                    <tr>
                      <td colSpan={DOC_TABLE_COL_COUNT} style={{ padding: 0 }} />
                    </tr>
                  ) : kb.docs.length === 0 ? (
                    <tr>
                      <td colSpan={DOC_TABLE_COL_COUNT} style={{ padding: 0 }}>
                        <EmptyState
                          title="知识库暂无文档"
                          description="上传文档后可在这里查看解析和索引状态。"
                        />
                      </td>
                    </tr>
                  ) : kb.filteredDocs.length === 0 ? (
                    <tr>
                      <td colSpan={DOC_TABLE_COL_COUNT} style={{ padding: 0 }}>
                        <EmptyState
                          title="当前筛选下无文档"
                          description="切换文件夹、状态或关键词后再试。"
                        />
                      </td>
                    </tr>
                  ) : (
                    kb.filteredDocs.map((d) => {
                      // 文件夹列仅展示 folder 名；category 独立列展示服务端真值
                      const folderName =
                        kb.folders.find((f) => f.id === d.folderId)?.name ?? "";
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
                                {/* sizeLabel===null/undefined 不生成 div.mono */}
                                {d.sizeLabel != null ? (
                                  <div
                                    className="mono"
                                    style={{
                                      fontSize: 11,
                                      color: "var(--text-tertiary)",
                                    }}
                                  >
                                    {d.sizeLabel}
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </td>
                          <td>
                            {/* 禁止 statusMessage 原文进入 text/title/aria；仅固定 DOC_STATUS_LABEL */}
                            <span
                              className={`kb-status-pill ${statusClass(d.status)}`}
                            >
                              {DOC_STATUS_LABEL[d.status]}
                            </span>
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
                          {/* chunks=0 必须显示「0」，禁止用 — 吞掉；列序 chunks@nth(5) */}
                          <td className="mono">{d.chunks}</td>
                          <td>{d.category}</td>
                          <td>{d.updated}</td>
                          <td>
                            <div style={{ display: "flex", gap: 4 }}>
                              {(d.status === "failed" ||
                                d.status === "pending" ||
                                d.status === "ready") && (
                                <button
                                  type="button"
                                  className="btn btn-ghost btn-sm"
                                  disabled={docsLocked}
                                  onClick={() => void kb.retryParse([d.id])}
                                  title="重新索引"
                                >
                                  <RefreshCw size={14} />
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={docsLocked}
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

      {tab === "cards" && (
        <div className="kb-cards-panel">
          {cards.error && (
            <div className="hint-banner" role="status" style={{ marginBottom: 12 }}>
              {cards.error}
            </div>
          )}
          <div className="kb-docs-toolbar card card-pad">
            <div className="field" style={{ margin: 0, flex: 1, minWidth: 180 }}>
              <label htmlFor="kb-card-search">检索卡片</label>
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
                  id="kb-card-search"
                  value={cards.query}
                  onChange={(e) => cards.setQuery(e.target.value)}
                  placeholder="标题、标签、摘要、来源…"
                  style={{ paddingLeft: 36 }}
                  aria-label="检索卡片"
                />
              </div>
            </div>
            <div className="field" style={{ margin: 0, minWidth: 140 }}>
              <label htmlFor="kb-card-type">类型</label>
              <select
                id="kb-card-type"
                value={cards.typeFilter}
                onChange={(e) =>
                  cards.setTypeFilter(
                    (e.target.value || "") as KnowledgeCardType | "",
                  )
                }
                aria-label="卡片类型"
              >
                <option value="">全部类型</option>
                <option value="document">文档片段</option>
                <option value="qualification">资质</option>
                <option value="performance">业绩</option>
                <option value="image">图片</option>
              </select>
            </div>
            <div className="field" style={{ margin: 0, minWidth: 120 }}>
              <label htmlFor="kb-card-status">状态</label>
              <select
                id="kb-card-status"
                value={cards.statusFilter}
                onChange={(e) =>
                  cards.setStatusFilter(
                    e.target.value as "active" | "archived" | "all",
                  )
                }
              >
                <option value="active">有效</option>
                <option value="archived">已归档</option>
                <option value="all">全部</option>
              </select>
            </div>
          </div>

          {cards.loading ? (
            <p>加载卡片中…</p>
          ) : cardList.length === 0 ? (
            <EmptyState
              title="暂无素材卡片"
              description="点击「新建卡片」录入文档/资质/业绩，或在图片 Tab 上传 PNG/JPEG/GIF。"
            />
          ) : (
            <ul className="kb-card-grid" aria-label="素材卡片列表">
              {cardList.map((card) => (
                <li
                  key={card.id}
                  className="card card-pad kb-card-item"
                  aria-label={`卡片 ${card.title}`}
                >
                  <div className="kb-card-item__head">
                    <strong>{card.title}</strong>
                    <span className="badge badge-muted">
                      {CARD_TYPE_LABEL[card.type]}
                    </span>
                  </div>
                  <p className="kb-card-item__summary">
                    {card.summary || "无摘要"}
                  </p>
                  <div className="kb-card-item__meta mono">
                    {card.sourceLabel || "无来源"} · {card.status}
                  </div>
                  <div className="kb-card-item__tags">
                    {card.tags.map((t) => (
                      <span key={t} className="badge badge-muted">
                        {t}
                      </span>
                    ))}
                  </div>
                  {card.type === "image" && card.hasImage ? (
                    <img
                      src={cardContentUrl(card.id)}
                      alt={card.title}
                      className="kb-card-item__thumb"
                    />
                  ) : null}
                  <div className="kb-card-item__actions">
                    <button
                      type="button"
                      className="btn btn-soft btn-sm"
                      onClick={() => void openDetail(card)}
                    >
                      预览
                    </button>
                    {card.status === "active" ? (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        disabled={cards.busy}
                        onClick={() => void cards.archive(card.id)}
                      >
                        归档
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={cards.busy}
                      aria-label={`删除卡片 ${card.title}`}
                      onClick={() => {
                        if (window.confirm(`确定删除卡片「${card.title}」？`)) {
                          void cards.remove(card.id);
                        }
                      }}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {tab === "images" && (
        <>
          {images.error && (
            <div className="hint-banner" role="status" style={{ marginBottom: 12 }}>
              {images.error}
            </div>
          )}
          <div
            className={`kb-upload-zone${dragOver ? " is-drag" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDropImages}
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
              仅 PNG / JPEG / GIF；保存为后端图片卡，刷新后仍可预览，不依赖 localStorage。
            </p>
            <input
              id="kb-image-input"
              type="file"
              accept="image/png,image/jpeg,image/gif"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) {
                  void images.uploadImages(e.target.files);
                }
                e.target.value = "";
              }}
            />
          </div>

          <div className="kb-toolbar">
            <div className="field">
              <label htmlFor="kb-img-search">检索图片卡</label>
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
                  value={images.query}
                  onChange={(e) => images.setQuery(e.target.value)}
                  placeholder="名称、标签…"
                  style={{ paddingLeft: 36 }}
                />
              </div>
            </div>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={images.loading}
              onClick={() => void images.refresh()}
            >
              <RefreshCw size={16} /> 刷新
            </button>
          </div>

          {images.items.length === 0 ? (
            <EmptyState
              title="暂无图片卡片"
              description="上传 PNG/JPEG/GIF 后会出现在此，并可在章节中「插入卡片」。"
            />
          ) : (
            <div className="kb-image-grid" aria-label="图片卡片网格">
              {images.items.map((img) => (
                <article
                  key={img.id}
                  className="kb-image-card"
                  aria-label={`图片卡片 ${img.title}`}
                >
                  <img
                    src={cardContentUrl(img.id)}
                    alt={img.title}
                    loading="lazy"
                  />
                  <div className="kb-image-card__body">
                    <strong>{img.title}</strong>
                    <div className="mono" style={{ fontSize: 11 }}>
                      {img.sourceLabel || img.summary}
                    </div>
                    <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                      <button
                        type="button"
                        className="btn btn-soft btn-sm"
                        onClick={() => void openDetail(img)}
                      >
                        预览
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        aria-label={`删除图片卡片 ${img.title}`}
                        onClick={() => {
                          if (window.confirm(`删除图片卡「${img.title}」？`)) {
                            void images.remove(img.id);
                          }
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}

      {createOpen && (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setCreateOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 70,
            padding: 16,
          }}
        >
          <form
            className="card card-pad"
            role="dialog"
            aria-modal="true"
            aria-label="新建文本卡片"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => void handleCreateCard(e)}
            style={{ width: "min(560px, 100%)" }}
          >
            <h2 style={{ marginTop: 0, fontSize: 18 }}>新建文本卡片</h2>
            <div className="field">
              <label htmlFor="card-type">类型</label>
              <select
                id="card-type"
                value={createType}
                onChange={(e) =>
                  setCreateType(
                    e.target.value as Exclude<KnowledgeCardType, "image">,
                  )
                }
              >
                <option value="document">文档片段</option>
                <option value="qualification">资质</option>
                <option value="performance">业绩</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="card-title">标题</label>
              <input
                id="card-title"
                required
                value={createTitle}
                onChange={(e) => setCreateTitle(e.target.value)}
                aria-label="卡片标题"
              />
            </div>
            <div className="field">
              <label htmlFor="card-body">正文</label>
              <textarea
                id="card-body"
                required
                rows={6}
                value={createBody}
                onChange={(e) => setCreateBody(e.target.value)}
                aria-label="卡片正文"
              />
            </div>
            <div className="field">
              <label htmlFor="card-tags">标签（逗号分隔）</label>
              <input
                id="card-tags"
                value={createTags}
                onChange={(e) => setCreateTags(e.target.value)}
                aria-label="卡片标签"
              />
            </div>
            <div className="field">
              <label htmlFor="card-source">来源说明</label>
              <input
                id="card-source"
                value={createSource}
                onChange={(e) => setCreateSource(e.target.value)}
                aria-label="卡片来源"
              />
            </div>
            {createError && (
              <div className="hint-banner" role="alert">
                {createError}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setCreateOpen(false)}
              >
                取消
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={cards.busy}
              >
                {cards.busy ? "保存中…" : "创建卡片"}
              </button>
            </div>
          </form>
        </div>
      )}

      {detail && (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setDetail(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15, 23, 42, 0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 75,
            padding: 16,
          }}
        >
          <div
            className="card card-pad"
            role="dialog"
            aria-modal="true"
            aria-label={`预览卡片 ${detail.title}`}
            onClick={(e) => e.stopPropagation()}
            style={{ width: "min(640px, 100%)", maxHeight: "85vh", overflow: "auto" }}
          >
            <h2 style={{ marginTop: 0 }}>{detail.title}</h2>
            <div className="mono" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              {CARD_TYPE_LABEL[detail.type]} · 来源：{detail.sourceLabel || "—"}
            </div>
            {detail.type === "image" ? (
              <img
                src={cardContentUrl(detail.id)}
                alt={detail.title}
                style={{ marginTop: 12, maxWidth: "100%", maxHeight: 360 }}
              />
            ) : (
              <pre
                className="mono"
                style={{
                  marginTop: 12,
                  whiteSpace: "pre-wrap",
                  fontSize: 13,
                  background: "var(--hover-bg)",
                  padding: 12,
                  borderRadius: 8,
                }}
              >
                {detail.bodyMarkdown || "（无正文）"}
              </pre>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setDetail(null)}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
