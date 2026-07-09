import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Newspaper, Search, FolderPlus } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { bidRegions, mockBidOpportunities } from "../mock";
import type { BidOppStatus, BidOpportunity } from "../types";
import { BID_STATUS_LABEL } from "../types";
import "./BidOpportunity.css";

/**
 * 模块：标讯页
 * 用途：招标线索列表、筛选，一键创建技术方案项目（跳转新建页）。
 * 对接：信息源 API/RSS 二期；当前 mock。
 */

type StatusFilter = BidOppStatus | "all";

export function BidOpportunityPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState("全部");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return mockBidOpportunities.filter((o) => {
      if (region !== "全部" && o.region !== region) return false;
      if (status !== "all" && o.status !== status) return false;
      if (!q) return true;
      return (
        o.title.toLowerCase().includes(q) ||
        o.buyer.toLowerCase().includes(q) ||
        o.tags.some((t) => t.toLowerCase().includes(q)) ||
        o.summary.toLowerCase().includes(q)
      );
    });
  }, [query, region, status]);

  function createProject(opp: BidOpportunity) {
    // 前端阶段：进入技术标新建页；后端可预填项目名
    navigate("/technical-plan/new", {
      state: {
        fromOpportunity: true,
        title: opp.title,
        oppId: opp.id,
      },
    });
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>标讯</h1>
          <p>
            招标信息线索列表，便于筛选与立项。
          </p>
        </div>
      </header>

      <div className="card card-pad opp-filters">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="opp-q">关键词</label>
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
              id="opp-q"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="标题、采购人、标签…"
              style={{ paddingLeft: 36 }}
            />
          </div>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="opp-region">地区</label>
          <select
            id="opp-region"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
          >
            {bidRegions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", paddingBottom: 8 }}>
          共 {list.length} 条
        </div>
      </div>

      <div className="opp-chips" role="group" aria-label="状态筛选">
        {(
          [
            ["all", "全部状态"],
            ["open", "进行中"],
            ["closing_soon", "即将截止"],
            ["closed", "已截止"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`opp-chip${status === key ? " is-active" : ""}`}
            onClick={() => setStatus(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {list.length === 0 ? (
        <EmptyState
          icon={<Newspaper size={32} />}
          title="没有匹配标讯"
          description="试试调整关键词、地区或状态筛选。"
        />
      ) : (
        <div className="opp-list">
          {list.map((opp) => {
            const open = expandedId === opp.id;
            return (
              <article key={opp.id} className="opp-card">
                <div className="opp-card__top">
                  <h2 className="opp-card__title">{opp.title}</h2>
                  <span className={`opp-status is-${opp.status}`}>
                    {BID_STATUS_LABEL[opp.status]}
                  </span>
                </div>
                <div className="opp-card__meta">
                  <span>
                    采购人 <strong>{opp.buyer}</strong>
                  </span>
                  <span>
                    地区 <strong>{opp.region}</strong>
                  </span>
                  <span>
                    预算 <strong>{opp.budgetLabel}</strong>
                  </span>
                  <span>
                    截止 <strong>{opp.deadline}</strong>
                  </span>
                </div>
                <div className="opp-card__tags">
                  {opp.tags.map((t) => (
                    <span key={t} className="badge badge-muted">
                      {t}
                    </span>
                  ))}
                </div>
                {open && <p className="opp-card__summary">{opp.summary}</p>}
                <div className="opp-card__actions">
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() =>
                      setExpandedId((id) => (id === opp.id ? null : opp.id))
                    }
                  >
                    {open ? "收起摘要" : "展开摘要"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={opp.status === "closed"}
                    title={
                      opp.status === "closed"
                        ? "已截止，仅可查看"
                        : "创建技术方案项目"
                    }
                    onClick={() => createProject(opp)}
                  >
                    <FolderPlus size={14} /> 创建技术方案项目
                  </button>
                  <span className="opp-card__source">{opp.sourceLabel}</span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
