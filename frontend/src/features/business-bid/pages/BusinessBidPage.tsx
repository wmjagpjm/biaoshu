import { Link } from "react-router-dom";
import {
  Briefcase,
  CheckSquare,
  FileText,
  BadgeDollarSign,
  ScrollText,
  Stamp,
} from "lucide-react";

const modules = [
  {
    title: "资格条件响应",
    desc: "对照招标资格要求，逐条生成响应说明与证明材料索引。",
    icon: <CheckSquare size={18} />,
  },
  {
    title: "商务目录与附件清单",
    desc: "自动梳理需递交的营业执照、资质、业绩、社保证明等。",
    icon: <FileText size={18} />,
  },
  {
    title: "报价说明",
    desc: "报价表结构、取费说明与偏离表（按行业模板，后端接入）。",
    icon: <BadgeDollarSign size={18} />,
  },
  {
    title: "授权与承诺",
    desc: "法定代表人授权、诚信承诺、保密与联合体协议等固定格式文本。",
    icon: <Stamp size={18} />,
  },
  {
    title: "商务响应正文",
    desc: "付款、交付、服务承诺等商务条款响应段落生成。",
    icon: <ScrollText size={18} />,
  },
];

/**
 * 商务标工作区
 * 用途：独立「商务标生成」能力；与技术标分册，可与「完整投标文件」组合使用。
 * 说明：当前为前端可交互骨架，生成逻辑待后端接入。
 */
export function BusinessBidPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>商务标生成</h1>
          <p>
            专注资格、报价与商务响应，不替代技术标正文。需要整套文件时请用「完整投标文件」；
            若只要交件清单，用「商务资料清单整理」。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/create" className="btn btn-ghost">
            返回创建
          </Link>
          <button type="button" className="btn btn-primary" disabled title="后端接入后可用">
            <Briefcase size={16} /> 开始解析招标文件
          </button>
        </div>
      </header>

      <div
        className="card card-pad"
        style={{
          marginBottom: 16,
          background: "var(--primary-soft)",
          borderColor: "rgba(100,56,255,0.15)",
        }}
      >
        <strong style={{ color: "var(--primary-deep)" }}>和另外两个入口怎么选？</strong>
        <ul style={{ margin: "10px 0 0", paddingLeft: 18, color: "var(--text-body)", fontSize: 13, lineHeight: 1.7 }}>
          <li>
            <strong>技术标生成</strong>：写实施方案、架构、进度、运维等技术内容。
          </li>
          <li>
            <strong>商务标生成（本页）</strong>：写资格、报价、授权承诺等商务册。
          </li>
          <li>
            <strong>完整投标文件</strong>：商务 + 技术一次规划，再分册深化。
          </li>
        </ul>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
          gap: 12,
        }}
      >
        {modules.map((m) => (
          <div key={m.title} className="card card-pad">
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "var(--primary-soft)",
                color: "var(--primary)",
                display: "grid",
                placeItems: "center",
                marginBottom: 10,
              }}
            >
              {m.icon}
            </div>
            <strong style={{ display: "block", marginBottom: 6 }}>{m.title}</strong>
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.55 }}>
              {m.desc}
            </p>
          </div>
        ))}
      </div>

      <div className="card empty-state" style={{ marginTop: 16 }}>
        <Briefcase size={28} color="var(--text-tertiary)" style={{ margin: "0 auto 10px" }} />
        <strong>生成流水线待后端接入</strong>
        前端已预留模块划分：解析资格条款 → 生成商务目录 → 填充响应与报价 → 导出 Word。
      </div>
    </div>
  );
}
