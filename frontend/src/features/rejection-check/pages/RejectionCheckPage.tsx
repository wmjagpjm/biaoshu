import { AlertTriangle, FileWarning, ShieldCheck } from "lucide-react";

const items = [
  {
    id: "r1",
    level: "high" as const,
    title: "目录未完全对齐招标文件一级章节",
    detail: "招标要求「实施保障」为一级目录，当前大纲合并进第四章，存在形式评审风险。",
  },
  {
    id: "r2",
    level: "medium" as const,
    title: "★号条款响应不完整",
    detail: "技术要求中「信创适配证明」未见独立小节或附件索引。",
  },
  {
    id: "r3",
    level: "low" as const,
    title: "售后响应时间表述不一致",
    detail: "全局事实为 4 小时，第五章正文出现「工作日 8 小时内」，建议统一。",
  },
];

/**
 * 废标项检查页
 * 用途：对照招标硬性条款与响应完整性，输出风险清单。
 */
export function RejectionCheckPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>废标项检查</h1>
          <p>聚焦形式评审与★号条款响应，降低废标风险。可与技术方案项目联动。</p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-primary">
            <FileWarning size={16} /> 运行检查
          </button>
        </div>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
        <div className="card card-pad" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 700, color: "var(--danger)" }}>1</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>高风险</div>
        </div>
        <div className="card card-pad" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 700, color: "var(--warning)" }}>1</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>中风险</div>
        </div>
        <div className="card card-pad" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 32, fontWeight: 700, color: "var(--success)" }}>1</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>低风险</div>
        </div>
      </div>

      <div className="chapter-list">
        {items.map((item) => (
          <div key={item.id} className="card card-pad">
            <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              {item.level === "high" ? (
                <AlertTriangle color="var(--danger)" size={20} />
              ) : item.level === "medium" ? (
                <FileWarning color="var(--warning)" size={20} />
              ) : (
                <ShieldCheck color="var(--success)" size={20} />
              )}
              <div>
                <strong>{item.title}</strong>
                <p style={{ margin: "6px 0 0", color: "var(--text-secondary)", fontSize: 13 }}>
                  {item.detail}
                </p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
