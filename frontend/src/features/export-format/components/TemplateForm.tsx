import type { ExportStyleConfig } from "../types";
import { FONT_OPTIONS } from "../types";

type Props = {
  name: string;
  description: string;
  style: ExportStyleConfig;
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onStyleChange: (patch: Partial<ExportStyleConfig>) => void;
};

/**
 * 模板表单
 * 用途：新建 / 编辑自定义导出模板的字段编辑区（对齐 C 端样式配置项）。
 */
export function TemplateForm({
  name,
  description,
  style,
  onNameChange,
  onDescriptionChange,
  onStyleChange,
}: Props) {
  return (
    <div className="ef-form">
      <div className="field">
        <label htmlFor="tpl-name">模板名称</label>
        <input
          id="tpl-name"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="例如：我司政务标导出样式"
          required
        />
      </div>
      <div className="field">
        <label htmlFor="tpl-desc">说明</label>
        <textarea
          id="tpl-desc"
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="用途、适用行业或注意点"
        />
      </div>

      <h3 style={{ margin: "8px 0 0", fontSize: "var(--fs-md)" }}>字体与字号</h3>
      <div className="ef-form-grid">
        <div className="field">
          <label>正文字体</label>
          <select
            value={style.bodyFont}
            onChange={(e) => onStyleChange({ bodyFont: e.target.value })}
          >
            {FONT_OPTIONS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>标题字体</label>
          <select
            value={style.headingFont}
            onChange={(e) => onStyleChange({ headingFont: e.target.value })}
          >
            {FONT_OPTIONS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="ef-form-grid--3 ef-form-grid">
        <div className="field">
          <label>一级标题（磅）</label>
          <input
            type="number"
            min={10}
            max={28}
            value={style.h1Size}
            onChange={(e) => onStyleChange({ h1Size: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>二级标题（磅）</label>
          <input
            type="number"
            min={10}
            max={24}
            value={style.h2Size}
            onChange={(e) => onStyleChange({ h2Size: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>三级标题（磅）</label>
          <input
            type="number"
            min={10}
            max={20}
            value={style.h3Size}
            onChange={(e) => onStyleChange({ h3Size: Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="ef-form-grid">
        <div className="field">
          <label>正文（磅）</label>
          <input
            type="number"
            min={9}
            max={18}
            value={style.bodySize}
            onChange={(e) => onStyleChange({ bodySize: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>行距（倍）</label>
          <input
            type="number"
            min={1}
            max={3}
            step={0.05}
            value={style.lineHeight}
            onChange={(e) => onStyleChange({ lineHeight: Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="field">
        <label>首行缩进（字符）</label>
        <input
          type="number"
          min={0}
          max={4}
          value={style.firstLineIndent}
          onChange={(e) =>
            onStyleChange({ firstLineIndent: Number(e.target.value) })
          }
        />
      </div>

      <h3 style={{ margin: "8px 0 0", fontSize: "var(--fs-md)" }}>页边距（mm）</h3>
      <div className="ef-form-grid">
        {(
          [
            ["marginTop", "上"],
            ["marginBottom", "下"],
            ["marginLeft", "左"],
            ["marginRight", "右"],
          ] as const
        ).map(([key, label]) => (
          <div className="field" key={key}>
            <label>{label}</label>
            <input
              type="number"
              min={10}
              max={50}
              step={0.1}
              value={style[key]}
              onChange={(e) =>
                onStyleChange({ [key]: Number(e.target.value) } as Partial<ExportStyleConfig>)
              }
            />
          </div>
        ))}
      </div>

      <h3 style={{ margin: "8px 0 0", fontSize: "var(--fs-md)" }}>版式选项</h3>
      <div className="ef-form-grid">
        <div className="field">
          <label>封面标题</label>
          <input
            value={style.coverTitle}
            onChange={(e) => onStyleChange({ coverTitle: e.target.value })}
          />
        </div>
        <div className="field">
          <label>页眉文字（可空）</label>
          <input
            value={style.headerText}
            onChange={(e) => onStyleChange({ headerText: e.target.value })}
            placeholder="例如：某某项目技术标"
          />
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        <label className="ef-check">
          <input
            type="checkbox"
            checked={style.includeToc}
            onChange={(e) => onStyleChange({ includeToc: e.target.checked })}
          />
          生成目录
        </label>
        <label className="ef-check">
          <input
            type="checkbox"
            checked={style.showPageNumber}
            onChange={(e) => onStyleChange({ showPageNumber: e.target.checked })}
          />
          显示页码
        </label>
      </div>

      <div>
        <h3 style={{ margin: "0 0 10px", fontSize: "var(--fs-md)" }}>样式预览</h3>
        <div className="ef-preview">
          <div
            className="ef-preview__h1"
            style={{
              fontFamily: style.headingFont,
              fontSize: style.h1Size,
            }}
          >
            一、{style.coverTitle || "一级标题示例"}
          </div>
          <div
            className="ef-preview__h2"
            style={{
              fontFamily: style.headingFont,
              fontSize: style.h2Size,
            }}
          >
            1.1 二级标题示例
          </div>
          <div
            className="ef-preview__h3"
            style={{
              fontFamily: style.headingFont,
              fontSize: style.h3Size,
            }}
          >
            1.1.1 三级标题示例
          </div>
          <p
            className="ef-preview__p"
            style={{
              fontFamily: style.bodyFont,
              fontSize: style.bodySize,
              lineHeight: style.lineHeight,
              textIndent: `${style.firstLineIndent}em`,
            }}
          >
            这是正文预览。导出 Word 时将按以上字体、字号、行距与首行缩进渲染。
            页边距：上{style.marginTop} 下{style.marginBottom} 左{style.marginLeft}{" "}
            右{style.marginRight} mm。
          </p>
        </div>
      </div>
    </div>
  );
}
