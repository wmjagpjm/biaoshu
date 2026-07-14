/**
 * 模块：废标项检查页
 * 用途：风险列表 + 招标条款 / 现状对照 + 处理跳转。
 * 对接：POST /api/projects/{id}/rejection-check；listProjectsAsync
 * 二次开发：规则表可后端配置；可接 LLM 复核。
 *       项目选择器失败时选项为空并固定中文提示，不读演示项目。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  FileWarning,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { apiFetch } from "../../../shared/lib/api";
import type { Project } from "../../../shared/types/workspace";
import { listProjectsAsync } from "../../technical-plan/lib/projectStore";
import type { RejectionItem, RiskLevel } from "../types";
import { RISK_LEVEL_LABEL } from "../types";
import "./RejectionCheck.css";

type FilterLevel = RiskLevel | "all";

type RejResult = {
  projectId: string;
  items: RejectionItem[];
  stats?: { total?: number };
};

function LevelIcon({ level }: { level: RiskLevel }) {
  if (level === "high") return <AlertTriangle color="var(--danger)" size={20} />;
  if (level === "medium") return <FileWarning color="var(--warning)" size={20} />;
  return <ShieldCheck color="var(--success)" size={20} />;
}

export function RejectionCheckPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [items, setItems] = useState<RejectionItem[]>([]);
  const [filter, setFilter] = useState<FilterLevel>("all");
  const [selectedId, setSelectedId] = useState("");
  const [running, setRunning] = useState(false);
  const [ranOnce, setRanOnce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadHint, setLoadHint] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await listProjectsAsync({ kind: "technical" });
        setProjects(res.projects);
        setLoadHint(null);
        if (res.projects[0]) setProjectId(res.projects[0].id);
        else setProjectId("");
      } catch {
        setProjects([]);
        setProjectId("");
        setLoadHint("项目列表加载失败，请稍后重试");
      }
    })();
  }, []);

  const counts = useMemo(() => {
    return {
      high: items.filter((i) => i.level === "high").length,
      medium: items.filter((i) => i.level === "medium").length,
      low: items.filter((i) => i.level === "low").length,
    };
  }, [items]);

  const filtered = useMemo(() => {
    if (filter === "all") return items;
    return items.filter((i) => i.level === filter);
  }, [items, filter]);

  const selected =
    filtered.find((i) => i.id === selectedId) ?? filtered[0] ?? null;

  const runCheck = useCallback(async () => {
    if (!projectId) {
      setError("请先选择项目");
      return;
    }
    setRunning(true);
    setError(null);
    setRanOnce(true);
    try {
      const data = await apiFetch<RejResult>(
        `/projects/${encodeURIComponent(projectId)}/rejection-check`,
        {
          method: "POST",
          body: JSON.stringify({ includeRules: true }),
        },
      );
      const list = Array.isArray(data.items) ? data.items : [];
      setItems(list);
      setSelectedId(list[0]?.id ?? "");
    } catch (err) {
      setItems([]);
      setError((err as { message?: string })?.message || "检查失败");
    } finally {
      setRunning(false);
    }
  }, [projectId]);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>废标项检查</h1>
          <p>
            对照招标硬性条款、★ 号要求与当前大纲/正文状态，输出风险清单与修改建议。
          </p>
        </div>
        <div className="page-actions">
          <div className="field" style={{ margin: 0, minWidth: 220 }}>
            <label htmlFor="rej-project" className="sr-only">
              项目
            </label>
            <select
              id="rej-project"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              style={{ minWidth: 200 }}
            >
              {projects.length === 0 ? (
                <option value="">暂无技术标项目</option>
              ) : (
                projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))
              )}
            </select>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void runCheck()}
            disabled={running || !projectId}
          >
            {running ? (
              <>
                <Loader2 size={16} /> 检查中…
              </>
            ) : (
              <>
                <FileWarning size={16} /> 运行检查
              </>
            )}
          </button>
        </div>
      </header>

      {error && (
        <div className="card card-pad" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}
      {loadHint && (
        <div
          className="card card-pad"
          role="alert"
          style={{ fontSize: 13, color: "var(--danger)" }}
        >
          {loadHint}
        </div>
      )}

      <div className="rej-stats">
        <div className="card rej-stat">
          <div className="rej-stat__num" style={{ color: "var(--danger)" }}>
            {counts.high}
          </div>
          <div className="rej-stat__label">高风险</div>
        </div>
        <div className="card rej-stat">
          <div className="rej-stat__num" style={{ color: "var(--warning)" }}>
            {counts.medium}
          </div>
          <div className="rej-stat__label">中风险</div>
        </div>
        <div className="card rej-stat">
          <div className="rej-stat__num" style={{ color: "var(--success)" }}>
            {counts.low}
          </div>
          <div className="rej-stat__label">低风险</div>
        </div>
      </div>

      <div className="rej-filters" role="group" aria-label="风险级别筛选">
        {(
          [
            ["all", "全部"],
            ["high", "高风险"],
            ["medium", "中风险"],
            ["low", "低风险"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`rej-chip${filter === key ? " is-active" : ""}`}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {running ? (
        <LoadingBlock label="正在对照招标条款与响应…" />
      ) : !ranOnce ? (
        <EmptyState
          icon={<FileWarning size={28} />}
          title="尚未运行检查"
          description="选择技术标项目后点击「运行检查」。建议先完成解析与招标分析。"
        />
      ) : (
        <div className="rej-layout">
          <div className="rej-list" aria-label="风险列表">
            {filtered.length === 0 ? (
              <EmptyState
                title="当前筛选下无风险项"
                description="切换级别筛选，或重新运行检查。"
              />
            ) : (
              filtered.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`rej-item${selected?.id === item.id ? " is-active" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <LevelIcon level={item.level} />
                  <div>
                    <div className="rej-item__title">{item.title}</div>
                    <div className="rej-item__hint">
                      {RISK_LEVEL_LABEL[item.level]} · {item.suggestion}
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>

          <div className="rej-panel" aria-label="条款对照">
            {!selected ? (
              <div
                style={{
                  color: "var(--text-tertiary)",
                  padding: 24,
                  textAlign: "center",
                }}
              >
                请选择左侧风险项
              </div>
            ) : (
              <>
                <div
                  style={{ display: "flex", gap: 10, alignItems: "flex-start" }}
                >
                  <LevelIcon level={selected.level} />
                  <div>
                    <strong style={{ fontSize: 16 }}>{selected.title}</strong>
                    <div style={{ marginTop: 4 }}>
                      <span
                        className="badge"
                        style={{
                          background:
                            selected.level === "high"
                              ? "rgba(239,68,68,0.12)"
                              : selected.level === "medium"
                                ? "rgba(245,158,11,0.14)"
                                : "rgba(16,185,129,0.12)",
                          color:
                            selected.level === "high"
                              ? "var(--danger)"
                              : selected.level === "medium"
                                ? "var(--warning)"
                                : "var(--success)",
                        }}
                      >
                        {RISK_LEVEL_LABEL[selected.level]}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="rej-panel__cols">
                  <div className="rej-col">
                    <h4>招标条款</h4>
                    <p>{selected.tenderClause}</p>
                  </div>
                  <div className="rej-col">
                    <h4>当前响应现状</h4>
                    <p>{selected.currentStatus}</p>
                  </div>
                </div>

                <div className="card card-pad" style={{ marginTop: 12 }}>
                  <strong>修改建议</strong>
                  <p style={{ margin: "8px 0 0" }}>{selected.suggestion}</p>
                  {selected.relatedTo && (
                    <div style={{ marginTop: 12 }}>
                      <Link to={selected.relatedTo} className="btn btn-soft btn-sm">
                        {selected.relatedLabel || "前往处理"}
                      </Link>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
