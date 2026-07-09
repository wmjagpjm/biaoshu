import { useState } from "react";
import { Link } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { TemplateNav } from "../components/TemplateNav";
import { TemplatePreview } from "../components/TemplatePreview";
import { useExportTemplates } from "../hooks/useExportTemplates";
import { createDefaultExportFormat } from "../model/cloneConfig";
import "./ExportFormat.css";

/**
 * 我的模板
 * 用途：对齐 C 端 MyTemplatesPage——左侧列表查看/编辑/删除，右侧实时预览。
 */
export function MyTemplatesPage() {
  const { templates, deleteTemplate, defaultTemplate, setDefault } =
    useExportTemplates();
  const [selectedId, setSelectedId] = useState(
    () => defaultTemplate?.template_id || templates[0]?.template_id || "",
  );

  const selected =
    templates.find((t) => t.template_id === selectedId) || templates[0] || null;

  function handleDelete(id: string, name: string) {
    if (!window.confirm(`确定删除模板「${name}」？删除后无法恢复。`)) return;
    deleteTemplate(id);
    if (selectedId === id) {
      setSelectedId("");
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>我的模板</h1>
          <p>查看、编辑和删除已保存的标书导出模板。</p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/new" className="btn btn-primary">
            <Plus size={16} /> 新建模板
          </Link>
        </div>
      </header>

      <TemplateNav />

      {templates.length === 0 ? (
        <div className="card ef-empty">
          <strong>还没有保存模板</strong>
          进入新建模板页配置排版样式，或从模板设置使用版面预设。
          <div style={{ marginTop: 16, display: "flex", gap: 8, justifyContent: "center" }}>
            <Link to="/export-format" className="btn btn-ghost">
              模板设置
            </Link>
            <Link to="/export-format/new" className="btn btn-primary">
              新建第一个模板
            </Link>
          </div>
        </div>
      ) : (
        <div className="ef-library">
          <section className="ef-library-list card">
            {templates.map((t) => {
              const active =
                (selected?.template_id || "") === t.template_id;
              return (
                <article
                  key={t.template_id}
                  className={`ef-library-item${active ? " is-active" : ""}`}
                >
                  <button
                    type="button"
                    className="ef-library-item__main"
                    onClick={() => setSelectedId(t.template_id)}
                  >
                    <strong>{t.template_name}</strong>
                    <small>
                      更新于 {new Date(t.updated_at).toLocaleString("zh-CN")}
                    </small>
                  </button>
                  <div className="ef-library-item__actions">
                    <Link
                      to={`/export-format/${t.template_id}/edit`}
                      className="btn btn-ghost btn-sm"
                    >
                      <Pencil size={14} /> 编辑
                    </Link>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => setDefault(t.template_id)}
                    >
                      默认
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm ef-danger"
                      onClick={() =>
                        handleDelete(t.template_id, t.template_name)
                      }
                    >
                      <Trash2 size={14} /> 删除
                    </button>
                  </div>
                </article>
              );
            })}
          </section>

          <section className="ef-library-preview card card-pad">
            {selected ? (
              <>
                <div className="ef-library-preview__head">
                  <div>
                    <span className="badge badge-primary">实时预览</span>
                    <h3>{selected.template_name}</h3>
                  </div>
                  <Link
                    to={`/export-format/${selected.template_id}/edit`}
                    className="btn btn-primary btn-sm"
                  >
                    编辑模板
                  </Link>
                </div>
                <TemplatePreview config={selected.config} />
              </>
            ) : (
              <div className="ef-empty">
                <strong>暂无模板可预览</strong>
                <TemplatePreview config={createDefaultExportFormat()} />
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
