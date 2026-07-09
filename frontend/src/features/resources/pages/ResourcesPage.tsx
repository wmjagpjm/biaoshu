import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Library, Search, X } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { mockResources } from "../mock";
import type { ResourceItem } from "../types";
import "./ResourcesPage.css";

/**
 * 模块：资源中心页
 * 用途：精选写作/合规/模板资源书架；搜索 + 详情弹层。
 * 对接：若配置 VITE_RESOURCES_URL 则尝试远程拉取，失败回落 mock。
 * 说明：对齐 C 端 resources 信息架构，不复制其 analytics 实现。
 */

const REMOTE_URL = import.meta.env.VITE_RESOURCES_URL as string | undefined;

async function tryLoadRemote(query: string): Promise<ResourceItem[] | null> {
  if (!REMOTE_URL) return null;
  try {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    const qs = params.toString();
    const url = qs ? `${REMOTE_URL}?${qs}` : REMOTE_URL;
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = (await res.json()) as {
      code?: number;
      resources?: ResourceItem[];
    };
    if (data.code !== undefined && data.code !== 0) return null;
    if (!data.resources?.length) return null;
    return data.resources;
  } catch {
    return null;
  }
}

export function ResourcesPage() {
  const [items, setItems] = useState<ResourceItem[]>(() =>
    mockResources.map((r) => ({ ...r })),
  );
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<ResourceItem | null>(null);
  const [sourceLabel, setSourceLabel] = useState("本地 mock");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!REMOTE_URL) return;
      setLoading(true);
      const remote = await tryLoadRemote("");
      if (!cancelled && remote) {
        setItems(remote);
        setSourceLabel("远程接口");
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q) ||
        r.tags.some((t) => t.toLowerCase().includes(q)) ||
        (r.category?.toLowerCase().includes(q) ?? false),
    );
  }, [items, query]);

  async function handleSearch(e: FormEvent) {
    e.preventDefault();
    if (!REMOTE_URL) return;
    setLoading(true);
    const remote = await tryLoadRemote(query);
    if (remote) {
      setItems(remote);
      setSourceLabel("远程接口");
    }
    setLoading(false);
  }

  function openResource(item: ResourceItem) {
    setItems((prev) =>
      prev.map((r) =>
        r.id === item.id ? { ...r, clickCount: r.clickCount + 1 } : r,
      ),
    );
    setSelected({ ...item, clickCount: item.clickCount + 1 });
  }

  return (
    <div className="page res-page">
      <header className="page-header">
        <div>
          <h1>资源中心</h1>
          <p>
            精选写作规范、废标避坑、导出模板与产品用法。数据源：
            {sourceLabel}
            {loading ? " · 加载中…" : ""}。
          </p>
        </div>
      </header>

      <form className="res-toolbar card card-pad" onSubmit={handleSearch}>
        <div className="field">
          <label htmlFor="res-q">搜索资源</label>
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
              id="res-q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="标题、标签、描述…"
              style={{ paddingLeft: 36 }}
              aria-label="搜索资源"
            />
          </div>
        </div>
        <button type="submit" className="btn btn-soft" disabled={loading}>
          {loading ? "搜索中" : "搜索"}
        </button>
      </form>

      {loading && items.length === 0 ? (
        <LoadingBlock label="加载资源…" />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Library size={28} />}
          title="没有匹配资源"
          description="试试其它关键词，或清空搜索。"
        />
      ) : (
        <div className="res-grid">
          {filtered.map((item) => (
            <button
              key={item.id}
              type="button"
              className="res-card"
              onClick={() => openResource(item)}
              aria-label={`查看资源：${item.title}`}
            >
              <div className={`res-card__cover is-${item.tone}`}>
                <span className="res-card__kicker">
                  {item.category ?? "资源"}
                </span>
              </div>
              <div className="res-card__body">
                <h3 className="res-card__title">{item.title}</h3>
                <p className="res-card__desc">{item.description}</p>
                <div className="res-card__tags">
                  {item.tags.map((t) => (
                    <span key={t} className="badge badge-muted">
                      {t}
                    </span>
                  ))}
                </div>
                <div className="res-card__meta mono">
                  浏览 {item.clickCount.toLocaleString("zh-CN")}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div
          className="res-modal-mask"
          onClick={() => setSelected(null)}
          role="presentation"
        >
          <div
            className="res-modal"
            role="dialog"
            aria-modal
            aria-labelledby="res-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="res-modal__head">
              <div>
                <h2 id="res-modal-title">{selected.title}</h2>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {selected.tags.map((t) => (
                    <span key={t} className="badge badge-primary">
                      {t}
                    </span>
                  ))}
                  <span className="badge badge-muted mono">
                    浏览 {selected.clickCount.toLocaleString("zh-CN")}
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSelected(null)}
                aria-label="关闭"
              >
                <X size={16} />
              </button>
            </div>
            <div className="res-modal__body">
              <p
                style={{
                  margin: "0 0 12px",
                  color: "var(--text-secondary)",
                  fontSize: 13,
                  lineHeight: 1.55,
                }}
              >
                {selected.description}
              </p>
              <pre className="res-modal__content">{selected.modalContent}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
