import { BookOpen, FolderPlus, Search, Upload } from "lucide-react";

const docs = [
  { id: "kb1", name: "智慧交通同类业绩汇编.docx", tags: ["业绩", "交通"], chunks: 42, updated: "3 天前" },
  { id: "kb2", name: "等保三级建设方案模板.md", tags: ["安全", "模板"], chunks: 28, updated: "1 周前" },
  { id: "kb3", name: "微服务高可用部署白皮书.pdf", tags: ["架构"], chunks: 65, updated: "2 周前" },
  { id: "kb4", name: "运维 SLA 与培训大纲.docx", tags: ["运维", "培训"], chunks: 19, updated: "1 月前" },
];

/**
 * 知识库页
 * 用途：企业/个人素材入库与检索；生成大纲/正文时可引用（后端 RAG）。
 */
export function KnowledgeBasePage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>知识库</h1>
          <p>
            沉淀历史方案、业绩与规范。生成阶段按项目召回，避免串库（按工作空间隔离）。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-ghost">
            <FolderPlus size={16} /> 新建分类
          </button>
          <button type="button" className="btn btn-primary">
            <Upload size={16} /> 上传资料
          </button>
        </div>
      </header>

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="field">
          <label htmlFor="kb-search">检索知识库</label>
          <div style={{ position: "relative" }}>
            <Search
              size={16}
              style={{ position: "absolute", left: 12, top: 12, color: "var(--text-muted)" }}
            />
            <input
              id="kb-search"
              placeholder="例如：等保、信创部署、视频接入规模…"
              style={{ paddingLeft: 36 }}
            />
          </div>
        </div>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <table className="project-table">
          <thead>
            <tr>
              <th>资料</th>
              <th>标签</th>
              <th>分块</th>
              <th>更新</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <BookOpen size={16} color="var(--seal)" />
                    <strong>{d.name}</strong>
                  </div>
                </td>
                <td>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {d.tags.map((t) => (
                      <span key={t} className="badge badge-muted">
                        {t}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="mono">{d.chunks}</td>
                <td>{d.updated}</td>
                <td>
                  <button type="button" className="btn btn-ghost btn-sm">
                    管理
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
