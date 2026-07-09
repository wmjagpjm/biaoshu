import { FileType } from "lucide-react";

const presets = [
  {
    id: "gov",
    name: "政务投标通用",
    desc: "标题宋体/黑体层级，正文宋体小四，页边距适中。",
    active: true,
  },
  {
    id: "ent",
    name: "企业方案风",
    desc: "更紧凑行距，强调封面与目录样式。",
    active: false,
  },
  {
    id: "custom",
    name: "我的模板",
    desc: "上传 docx 样式底板（后端支持后启用）。",
    active: false,
  },
];

/**
 * 导出格式页
 * 用途：管理 Word 导出预设，对齐 C 端 export-format。
 */
export function ExportFormatPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>导出格式</h1>
          <p>配置技术标 Word 样式。生成阶段选择模板后由服务端 python-docx 渲染。</p>
        </div>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
        {presets.map((p) => (
          <div
            key={p.id}
            className="card card-pad"
            style={{
              borderColor: p.active ? "rgba(194,59,34,0.35)" : undefined,
              boxShadow: p.active ? "0 0 0 1px rgba(194,59,34,0.15)" : undefined,
            }}
          >
            <FileType size={20} color="var(--primary)" style={{ marginBottom: 10 }} />
            <strong>{p.name}</strong>
            <p style={{ margin: "8px 0 14px", fontSize: 13, color: "var(--text-secondary)" }}>
              {p.desc}
            </p>
            {p.active ? (
              <span className="badge badge-primary">当前默认</span>
            ) : (
              <button type="button" className="btn btn-ghost btn-sm">
                设为默认
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
