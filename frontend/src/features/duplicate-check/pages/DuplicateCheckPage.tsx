import { FileSearch } from "lucide-react";

const hits = [
  {
    id: "d1",
    chapter: "2.1 逻辑架构",
    similarity: 0.86,
    snippet: "系统采用分层解耦设计，接入层、业务层与数据层通过标准接口交互…",
    vs: "知识库 · 微服务高可用部署白皮书",
  },
  {
    id: "d2",
    chapter: "4.2 风险与质量控制",
    similarity: 0.72,
    snippet: "建立周例会与里程碑评审机制，重大风险 24 小时内升级…",
    vs: "历史项目 · 园区能耗监测系统",
  },
  {
    id: "d3",
    chapter: "5.1 运维体系",
    similarity: 0.64,
    snippet: "提供 7×24 热线与远程支持，主城区 4 小时现场响应…",
    vs: "知识库 · 运维 SLA 与培训大纲",
  },
];

/**
 * 标书查重页
 * 用途：检测与知识库/历史稿的重复表达，辅助改写。
 */
export function DuplicateCheckPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>标书查重</h1>
          <p>对比当前技术标正文与知识库、历史项目，标注重复段落与相似度。</p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-primary">
            <FileSearch size={16} /> 开始查重
          </button>
        </div>
      </header>

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="field">
            <label>目标项目</label>
            <select defaultValue="proj_01">
              <option value="proj_01">某市智慧交通综合管理平台技术标</option>
              <option value="proj_03">医院信息集成平台改造</option>
            </select>
          </div>
          <div className="field">
            <label>对比范围</label>
            <select defaultValue="kb+history">
              <option value="kb+history">知识库 + 历史项目</option>
              <option value="kb">仅知识库</option>
              <option value="self">仅本文内部重复</option>
            </select>
          </div>
        </div>
      </div>

      <div className="chapter-list">
        {hits.map((h) => (
          <div key={h.id} className="card card-pad chapter-item" style={{ display: "grid" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
              <strong>{h.chapter}</strong>
              <span className="badge badge-primary">相似度 {(h.similarity * 100).toFixed(0)}%</span>
            </div>
            <p style={{ margin: "0 0 8px", color: "var(--text-secondary)", fontSize: 13 }}>
              {h.snippet}
            </p>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>对比来源：{h.vs}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
