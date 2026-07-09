import { Newspaper } from "lucide-react";

/**
 * 标讯页
 * 用途：对齐 C 端标讯入口；信息源对接二期实现。
 */
export function BidOpportunityPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>标讯</h1>
          <p>聚合招标信息线索，辅助立项。前端阶段提供信息架构占位。</p>
        </div>
      </header>
      <div className="card empty-state">
        <Newspaper size={32} color="var(--text-muted)" style={{ margin: "0 auto 12px" }} />
        <strong>标讯源尚未配置</strong>
        后续可对接公开招标 API 或 RSS，并一键创建技术方案项目。
      </div>
    </div>
  );
}
