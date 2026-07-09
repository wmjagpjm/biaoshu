import { Link } from "react-router-dom";
import { Eye, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { TemplateNav } from "../components/TemplateNav";
import { useExportTemplates } from "../hooks/useExportTemplates";
import "./ExportFormat.css";

/**
 * 我的模板页
 * 用途：对齐 C 端 MyTemplatesPage——查看、编辑、删除已保存的标书导出模板。
 */
export function MyTemplatesPage() {
  const { userTemplates, setDefault, deleteTemplate } = useExportTemplates();

  function handleDelete(id: string, name: string) {
    const ok = window.confirm(`确定删除模板「${name}」？删除后不可恢复。`);
    if (ok) deleteTemplate(id);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>我的模板</h1>
          <p>查看、编辑和删除已保存的标书导出模板。系统预设请在「模板设置」中管理。</p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/new" className="btn btn-primary">
            <Plus size={16} /> 新建模板
          </Link>
        </div>
      </header>

      <TemplateNav />

      {userTemplates.length === 0 ? (
        <div className="card ef-empty">
          <strong>还没有自定义模板</strong>
          可从系统预设「复制为自定义」，或直接新建模板。
          <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "center" }}>
            <Link to="/export-format" className="btn btn-ghost">
              查看系统预设
            </Link>
            <Link to="/export-format/new" className="btn btn-primary">
              新建模板
            </Link>
          </div>
        </div>
      ) : (
        <div className="card" style={{ overflow: "hidden" }}>
          <table className="project-table">
            <thead>
              <tr>
                <th>模板名称</th>
                <th>说明</th>
                <th>字体</th>
                <th>更新时间</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {userTemplates.map((t) => (
                <tr key={t.id}>
                  <td>
                    <strong>{t.name}</strong>
                  </td>
                  <td style={{ maxWidth: 220, color: "var(--text-secondary)" }}>
                    {t.description || "—"}
                  </td>
                  <td className="mono" style={{ fontSize: "var(--fs-xs)" }}>
                    {t.style.headingFont}/{t.style.bodyFont} · {t.style.bodySize}磅
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {new Date(t.updatedAt).toLocaleString("zh-CN")}
                  </td>
                  <td>
                    {t.isDefault ? (
                      <span className="badge badge-primary">默认</span>
                    ) : (
                      <span className="badge badge-muted">自定义</span>
                    )}
                  </td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      <Link
                        to={`/export-format/${t.id}`}
                        className="btn btn-ghost btn-sm"
                        title="查看"
                      >
                        <Eye size={14} /> 查看
                      </Link>
                      <Link
                        to={`/export-format/${t.id}/edit`}
                        className="btn btn-ghost btn-sm"
                        title="编辑"
                      >
                        <Pencil size={14} /> 编辑
                      </Link>
                      {!t.isDefault && (
                        <button
                          type="button"
                          className="btn btn-soft btn-sm"
                          onClick={() => setDefault(t.id)}
                        >
                          <Star size={14} /> 默认
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm ef-danger"
                        onClick={() => handleDelete(t.id, t.name)}
                      >
                        <Trash2 size={14} /> 删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
