import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import { TemplateForm } from "../components/TemplateForm";
import { TemplateNav } from "../components/TemplateNav";
import { useExportTemplates } from "../hooks/useExportTemplates";
import type { ExportStyleConfig } from "../types";
import { createDefaultStyle } from "../types";
import "./ExportFormat.css";

type Mode = "new" | "edit" | "view";

/**
 * 新建 / 编辑 / 查看 导出模板
 * 用途：对齐 C 端自定义模板能力；系统模板仅允许查看，用户模板可保存。
 */
export function TemplateEditorPage({ mode }: { mode: Mode }) {
  const { templateId = "" } = useParams();
  const navigate = useNavigate();
  const { getById, createTemplate, updateTemplate } = useExportTemplates();

  const existing = mode === "new" ? undefined : getById(templateId);
  const isSystem = existing?.source === "system";
  const readOnly = mode === "view" || (mode === "edit" && isSystem);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [style, setStyle] = useState<ExportStyleConfig>(createDefaultStyle());
  const [setAsDefault, setAsDefaultFlag] = useState(false);
  const [savedTip, setSavedTip] = useState("");

  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setDescription(existing.description);
      setStyle({ ...existing.style });
    } else if (mode === "new") {
      setName("");
      setDescription("");
      setStyle(createDefaultStyle());
    }
  }, [existing, mode, templateId]);

  const title = useMemo(() => {
    if (mode === "new") return "新建模板";
    if (mode === "view") return "查看模板";
    return isSystem ? "查看系统模板" : "编辑模板";
  }, [mode, isSystem]);

  if (mode !== "new" && !existing) {
    return (
      <div className="page">
        <TemplateNav />
        <div className="card ef-empty">
          <strong>未找到该模板</strong>
          <Link to="/export-format/my-templates" className="btn btn-primary" style={{ marginTop: 12 }}>
            返回我的模板
          </Link>
        </div>
      </div>
    );
  }

  function handleSave() {
    if (readOnly) return;
    if (!name.trim()) {
      window.alert("请填写模板名称");
      return;
    }
    if (mode === "new") {
      const id = createTemplate({
        name,
        description,
        style,
        setAsDefault,
      });
      setSavedTip("已创建");
      navigate(`/export-format/${id}/edit`, { replace: true });
      return;
    }
    if (existing && existing.source === "user") {
      updateTemplate(existing.id, { name, description, style });
      setSavedTip("已保存");
      window.setTimeout(() => setSavedTip(""), 2500);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{title}</h1>
          <p>
            {readOnly
              ? "系统预设不可直接改写，可「复制为自定义」后再改。"
              : "自定义字体、标题层级、页边距与版式选项，导出 Word 时套用。"}
          </p>
        </div>
        <div className="page-actions">
          <Link to="/export-format/my-templates" className="btn btn-ghost">
            <ArrowLeft size={16} /> 返回
          </Link>
          {!readOnly && (
            <button type="button" className="btn btn-primary" onClick={handleSave}>
              <Save size={16} /> 保存模板
            </button>
          )}
          {mode === "view" && existing?.source === "system" && (
            <Link
              to="/export-format"
              className="btn btn-soft"
              onClick={(e) => {
                e.preventDefault();
                // 引导回设置页用复制
                navigate("/export-format");
              }}
            >
              去模板设置复制
            </Link>
          )}
          {mode === "view" && existing?.source === "user" && (
            <Link to={`/export-format/${existing.id}/edit`} className="btn btn-primary">
              去编辑
            </Link>
          )}
        </div>
      </header>

      <TemplateNav />

      {savedTip ? (
        <div
          className="card card-pad"
          style={{
            marginBottom: 12,
            color: "var(--success)",
            fontWeight: 600,
            borderColor: "rgba(110,202,88,0.35)",
          }}
        >
          {savedTip}
        </div>
      ) : null}

      {isSystem && mode === "edit" ? (
        <div className="hint-banner" style={{ marginBottom: 12 }}>
          系统模板只读。请返回「模板设置」使用「复制为自定义」。
        </div>
      ) : null}

      <div className="card card-pad">
        {readOnly ? (
          <div style={{ pointerEvents: "none", opacity: 0.92 }}>
            <TemplateForm
              name={name}
              description={description}
              style={style}
              onNameChange={setName}
              onDescriptionChange={setDescription}
              onStyleChange={(patch) => setStyle((s) => ({ ...s, ...patch }))}
            />
          </div>
        ) : (
          <>
            <TemplateForm
              name={name}
              description={description}
              style={style}
              onNameChange={setName}
              onDescriptionChange={setDescription}
              onStyleChange={(patch) => setStyle((s) => ({ ...s, ...patch }))}
            />
            {mode === "new" && (
              <label className="ef-check" style={{ marginTop: 16 }}>
                <input
                  type="checkbox"
                  checked={setAsDefault}
                  onChange={(e) => setAsDefaultFlag(e.target.checked)}
                />
                保存后设为默认导出模板
              </label>
            )}
            <div style={{ marginTop: 20, display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <Link to="/export-format/my-templates" className="btn btn-ghost">
                取消
              </Link>
              <button type="button" className="btn btn-primary" onClick={handleSave}>
                <Save size={16} /> 保存模板
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
