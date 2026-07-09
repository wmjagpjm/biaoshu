import { Link, useNavigate } from "react-router-dom";
import { Plus, Star } from "lucide-react";
import { TemplateNav } from "../components/TemplateNav";
import { TemplatePreview } from "../components/TemplatePreview";
import { useExportTemplates } from "../hooks/useExportTemplates";
import "./ExportFormat.css";

/**
 * 模板设置（总览）
 * 用途：默认模板、版面预设一键建模板、进入我的模板 / 新建。
 */
export function ExportFormatPage() {
  const navigate = useNavigate();
  const {
    templates,
    defaultTemplate,
    layoutPresets,
    setDefault,
    createFromLayoutPreset,
  } = useExportTemplates();

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>模板设置</h1>
          <p>
            对齐 C 端 export-format：系统版面预设、我的模板（查看/编辑/删除）、完整自定义配置与预览。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/my-templates" className="btn btn-ghost">
            我的模板（{templates.length}）
          </Link>
          <Link to="/export-format/new" className="btn btn-primary">
            <Plus size={16} /> 新建模板
          </Link>
        </div>
      </header>

      <TemplateNav />

      {defaultTemplate && (
        <div className="ef-default-banner card card-pad">
          <div>
            <strong>当前默认模板：{defaultTemplate.template_name}</strong>
            <p>
              纸张 {defaultTemplate.config.page.paper_size.toUpperCase()} · 正文{" "}
              {defaultTemplate.config.body_text.font}/
              {defaultTemplate.config.body_text.size} · 边距{" "}
              {defaultTemplate.config.page.margin_top_cm}cm
            </p>
          </div>
          <div className="ef-default-banner__preview">
            <TemplatePreview config={defaultTemplate.config} />
          </div>
        </div>
      )}

      <h2 className="ef-block-title">版面预设（一键生成模板）</h2>
      <p className="ef-block-desc">
        对应 C 端快捷「版面预设」。点击后会新建一条可编辑模板，再按需改主题与细节。
      </p>
      <div className="ef-grid">
        {layoutPresets.map((p) => (
          <article key={p.id} className="ef-card">
            <h3 className="ef-card__name">{p.label}</h3>
            <p className="ef-card__desc">{p.description}</p>
            <div className="ef-card__meta">
              <span>{p.page.paper_size.toUpperCase()}</span>
              <span>
                {p.page.orientation === "landscape" ? "横向" : "纵向"}
              </span>
              <span>边距 {p.page.margin_left_cm}cm</span>
            </div>
            <div className="ef-card__actions">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => {
                  const id = createFromLayoutPreset(p.id);
                  navigate(`/export-format/${id}/edit`);
                }}
              >
                使用并编辑
              </button>
            </div>
          </article>
        ))}
      </div>

      <h2 className="ef-block-title" style={{ marginTop: 28 }}>
        已保存模板
      </h2>
      <div className="ef-grid">
        {templates.map((t) => (
          <article
            key={t.template_id}
            className={`ef-card${t.template_id === defaultTemplate?.template_id ? " is-default" : ""}`}
          >
            <h3 className="ef-card__name">{t.template_name}</h3>
            <p className="ef-card__desc">
              {t.config.body_text.font} {t.config.body_text.size} · 更新于{" "}
              {new Date(t.updated_at).toLocaleString("zh-CN")}
            </p>
            <div className="ef-card__actions">
              {t.template_id !== defaultTemplate?.template_id && (
                <button
                  type="button"
                  className="btn btn-soft btn-sm"
                  onClick={() => setDefault(t.template_id)}
                >
                  <Star size={14} /> 设为默认
                </button>
              )}
              {t.template_id === defaultTemplate?.template_id && (
                <span className="badge badge-primary">当前默认</span>
              )}
              <Link
                to={`/export-format/${t.template_id}`}
                className="btn btn-ghost btn-sm"
              >
                查看
              </Link>
              <Link
                to={`/export-format/${t.template_id}/edit`}
                className="btn btn-ghost btn-sm"
              >
                编辑
              </Link>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
