import { Link, useNavigate } from "react-router-dom";
import { Copy, FileType, Plus, Star } from "lucide-react";
import { TemplateNav } from "../components/TemplateNav";
import { useExportTemplates } from "../hooks/useExportTemplates";
import "./ExportFormat.css";

/**
 * 模板设置页
 * 用途：对齐 C 端 ExportFormatPage——展示系统预设、设默认、复制为自定义、入口到我的模板/新建。
 */
export function ExportFormatPage() {
  const navigate = useNavigate();
  const {
    systemTemplates,
    userTemplates,
    defaultTemplate,
    setDefault,
    cloneFrom,
  } = useExportTemplates();

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>模板设置</h1>
          <p>
            配置标书 Word 导出样式。系统预设可直接选用；需要自定义时请「复制为模板」或新建，
            在「我的模板」中查看、编辑与删除。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/my-templates" className="btn btn-ghost">
            我的模板（{userTemplates.length}）
          </Link>
          <Link to="/export-format/new" className="btn btn-primary">
            <Plus size={16} /> 新建模板
          </Link>
        </div>
      </header>

      <TemplateNav />

      <div
        className="card card-pad"
        style={{
          marginBottom: 16,
          background: "var(--primary-soft)",
          borderColor: "rgba(100,56,255,0.15)",
        }}
      >
        <strong style={{ color: "var(--primary-deep)" }}>当前默认模板</strong>
        <p style={{ margin: "6px 0 0", fontSize: "var(--fs-sm)", color: "var(--text-body)" }}>
          {defaultTemplate?.name ?? "—"} · {defaultTemplate?.description}
          <span className="badge badge-primary" style={{ marginLeft: 8 }}>
            导出时优先使用
          </span>
        </p>
      </div>

      <h2 style={{ fontSize: "var(--fs-md)", margin: "0 0 12px" }}>系统预设</h2>
      <div className="ef-grid">
        {systemTemplates.map((t) => (
          <article
            key={t.id}
            className={`ef-card${t.isDefault ? " is-default" : ""}`}
          >
            <div className="ef-card__icon">
              <FileType size={20} />
            </div>
            <h3 className="ef-card__name">{t.name}</h3>
            <p className="ef-card__desc">{t.description}</p>
            <div className="ef-card__meta">
              <span>
                {t.style.headingFont}/{t.style.bodyFont}
              </span>
              <span>正文 {t.style.bodySize} 磅</span>
              {t.isDefault ? (
                <span className="badge badge-primary">当前默认</span>
              ) : (
                <span className="badge badge-muted">系统</span>
              )}
            </div>
            <div className="ef-card__actions">
              {!t.isDefault && (
                <button
                  type="button"
                  className="btn btn-soft btn-sm"
                  onClick={() => setDefault(t.id)}
                >
                  <Star size={14} /> 设为默认
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  const id = cloneFrom(t.id);
                  if (id) navigate(`/export-format/${id}/edit`);
                }}
              >
                <Copy size={14} /> 复制为自定义
              </button>
              <Link to={`/export-format/${t.id}`} className="btn btn-ghost btn-sm">
                查看
              </Link>
            </div>
          </article>
        ))}
      </div>

      {userTemplates.length > 0 && (
        <>
          <h2 style={{ fontSize: "var(--fs-md)", margin: "24px 0 12px" }}>
            我的模板（快捷）
          </h2>
          <div className="ef-grid">
            {userTemplates.slice(0, 3).map((t) => (
              <article
                key={t.id}
                className={`ef-card${t.isDefault ? " is-default" : ""}`}
              >
                <h3 className="ef-card__name">{t.name}</h3>
                <p className="ef-card__desc">{t.description}</p>
                <div className="ef-card__actions">
                  <Link to={`/export-format/${t.id}/edit`} className="btn btn-primary btn-sm">
                    编辑
                  </Link>
                  <Link to="/export-format/my-templates" className="btn btn-ghost btn-sm">
                    全部
                  </Link>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
