import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  FileWarning,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { mockRejectionItems } from "../mock";
import type { RejectionItem, RiskLevel } from "../types";
import { RISK_LEVEL_LABEL } from "../types";
import "./RejectionCheck.css";

/**
 * 模块：废标项检查页
 * 用途：风险列表 + 招标条款 / 现状对照 + 处理跳转。
 * 对接：规则引擎就绪后替换 mock 与「运行检查」。
 */

type FilterLevel = RiskLevel | "all";

function LevelIcon({ level }: { level: RiskLevel }) {
  if (level === "high") return <AlertTriangle color="var(--danger)" size={20} />;
  if (level === "medium") return <FileWarning color="var(--warning)" size={20} />;
  return <ShieldCheck color="var(--success)" size={20} />;
}

export function RejectionCheckPage() {
  const [items, setItems] = useState<RejectionItem[]>(mockRejectionItems);
  const [filter, setFilter] = useState<FilterLevel>("all");
  const [selectedId, setSelectedId] = useState(mockRejectionItems[0]?.id ?? "");
  const [running, setRunning] = useState(false);

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

  function runCheck() {
    setRunning(true);
    window.setTimeout(() => {
      setItems([...mockRejectionItems]);
      setSelectedId(mockRejectionItems[0]?.id ?? "");
      setRunning(false);
    }, 650);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>废标项检查</h1>
          <p>
            对照招标硬性条款、★ 号要求与当前大纲/正文/商务标状态，输出风险清单与修改建议。
          </p>
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={runCheck}
            disabled={running}
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
            <div style={{ color: "var(--text-tertiary)", padding: 24, textAlign: "center" }}>
              请选择左侧风险项
            </div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
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
                  <h4>当前现状</h4>
                  <p>{selected.currentStatus}</p>
                </div>
              </div>

              <div className="rej-suggest">
                <strong>修改建议</strong>
                <div style={{ marginTop: 6 }}>{selected.suggestion}</div>
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {selected.relatedTo && (
                  <Link to={selected.relatedTo} className="btn btn-primary btn-sm">
                    去处理{selected.relatedLabel ? `：${selected.relatedLabel}` : ""}
                  </Link>
                )}
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--text-tertiary)",
                    alignSelf: "center",
                  }}
                >
                  当前为示例结果，正式环境由后端规则检查
                </span>
              </div>
            </>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
