import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import { TemplateForm } from "../components/TemplateForm";
import { TemplateNav } from "../components/TemplateNav";
import { TemplatePreview } from "../components/TemplatePreview";
import { useExportTemplates } from "../hooks/useExportTemplates";
import type { ExportFormatConfig } from "../model/exportFormat";
import { createDefaultExportFormat, withExportFormatDefaults } from "../model/cloneConfig";
import "./ExportFormat.css";

type Mode = "new" | "edit" | "view";

/**
 * 新建 / 编辑 / 查看 导出模板（C 端完整配置）
 */
export function TemplateEditorPage({ mode }: { mode: Mode }) {
  const { templateId = "" } = useParams();
  const navigate = useNavigate();
  const {
    getById,
    createTemplate,
    updateTemplate,
    applyLayoutToConfig,
    applyThemeToConfig,
  } = useExportTemplates();

  const existing = mode === "new" ? undefined : getById(templateId);
  const readOnly = mode === "view";

  const [config, setConfig] = useState<ExportFormatConfig>(() =>
    createDefaultExportFormat(),
  );
  const [setAsDefault, setSetAsDefault] = useState(false);
  const [tip, setTip] = useState("");

  useEffect(() => {
    if (existing) {
      setConfig(withExportFormatDefaults(existing.config));
    } else if (mode === "new") {
      setConfig(createDefaultExportFormat("未命名模板"));
    }
  }, [existing, mode, templateId]);

  const title = useMemo(() => {
    if (mode === "new") return "新建模板";
    if (mode === "view") return "查看模板";
    return "编辑模板";
  }, [mode]);

  if (mode !== "new" && !existing) {
    return (
      <div className="page">
        <TemplateNav />
        <div className="card ef-empty">
          <strong>未找到该模板</strong>
          <Link
            to="/export-format/my-templates"
            className="btn btn-primary"
            style={{ marginTop: 12 }}
          >
            返回我的模板
          </Link>
        </div>
      </div>
    );
  }

  function handleSave() {
    if (readOnly) return;
    if (!config.template_name.trim()) {
      window.alert("请填写模板名称");
      return;
    }
    if (mode === "new") {
      const id = createTemplate({
        name: config.template_name,
        config,
        setAsDefault,
      });
      setTip("已创建");
      navigate(`/export-format/${id}/edit`, { replace: true });
      return;
    }
    if (existing) {
      updateTemplate(existing.template_id, config);
      setTip("已保存");
      window.setTimeout(() => setTip(""), 2500);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>
            对齐易标 C 端：版面/主题预设、页面、六级标题、正文、表格与图片；右侧实时预览。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/my-templates" className="btn btn-ghost">
            <ArrowLeft size={16} /> 返回
          </Link>
          {mode === "view" && existing && (
            <Link
              to={`/export-format/${existing.template_id}/edit`}
              className="btn btn-primary"
            >
              去编辑
            </Link>
          )}
          {!readOnly && (
            <button type="button" className="btn btn-primary" onClick={handleSave}>
              <Save size={16} /> 保存模板
            </button>
          )}
        </div>
      </header>

      <TemplateNav />

      {tip ? (
        <div
          className="card card-pad"
          style={{ marginBottom: 12, color: "var(--success)", fontWeight: 600 }}
        >
          {tip}
        </div>
      ) : null}

      <div className="ef-editor-layout">
        <div className="card card-pad">
          <TemplateForm
            config={config}
            readOnly={readOnly}
            onChange={setConfig}
            onApplyLayout={(id) => {
              setConfig((c) => applyLayoutToConfig(c, id));
              setTip(`已应用版面预设，记得保存`);
            }}
            onApplyTheme={(id) => {
              setConfig((c) => applyThemeToConfig(c, id));
              setTip(`已应用主题，记得保存`);
            }}
          />
          {!readOnly && mode === "new" && (
            <label className="ef-check" style={{ marginTop: 16 }}>
              <input
                type="checkbox"
                checked={setAsDefault}
                onChange={(e) => setSetAsDefault(e.target.checked)}
              />
              保存后设为默认导出模板
            </label>
          )}
          {!readOnly && (
            <div
              style={{
                marginTop: 20,
                display: "flex",
                gap: 8,
                justifyContent: "flex-end",
              }}
            >
              <Link to="/export-format/my-templates" className="btn btn-ghost">
                取消
              </Link>
              <button type="button" className="btn btn-primary" onClick={handleSave}>
                <Save size={16} /> 保存模板
              </button>
            </div>
          )}
        </div>
        <aside className="ef-editor-preview">
          <div className="ef-editor-preview__title">实时预览</div>
          <TemplatePreview config={config} />
        </aside>
      </div>
    </div>
  );
}
