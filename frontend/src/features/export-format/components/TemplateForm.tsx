import { useState } from "react";
import type { ExportFormatConfig, HeadingStyleConfig } from "../model/exportFormat";
import {
  ALIGNMENT_OPTIONS,
  FONT_OPTIONS,
  HEADING_LEVEL_LABELS,
  HEADING_NUMBERING_FORMAT_OPTIONS,
  LIST_STYLE_OPTIONS,
  ORDERED_LIST_STYLE_OPTIONS,
  PAPER_SIZES,
  SIZE_OPTIONS,
} from "../model/exportFormat";
import {
  EXPORT_LAYOUT_PRESETS,
  EXPORT_THEME_PRESETS,
} from "../model/exportFormatPresets";
import "../pages/ExportFormat.css";

type Tab = "quick" | "page" | "headings" | "body" | "table";

type Props = {
  config: ExportFormatConfig;
  onChange: (next: ExportFormatConfig) => void;
  onApplyLayout: (layoutId: string) => void;
  onApplyTheme: (themeId: string) => void;
  readOnly?: boolean;
};

/**
 * C 端对齐的模板配置表单
 * 分栏：快捷预设 / 页面 / 六级标题 / 正文 / 表格图片
 */
export function TemplateForm({
  config,
  onChange,
  onApplyLayout,
  onApplyTheme,
  readOnly,
}: Props) {
  const [tab, setTab] = useState<Tab>("quick");
  const [openHeading, setOpenHeading] = useState(0);

  function patchPage(patch: Partial<ExportFormatConfig["page"]>) {
    onChange({ ...config, page: { ...config.page, ...patch } });
  }

  function patchBody(patch: Partial<ExportFormatConfig["body_text"]>) {
    onChange({ ...config, body_text: { ...config.body_text, ...patch } });
  }

  function patchHeading(index: number, patch: Partial<HeadingStyleConfig>) {
    const headings = config.headings.map((h, i) =>
      i === index ? { ...h, ...patch } : h,
    );
    onChange({ ...config, headings });
  }

  function patchTable(patch: Partial<ExportFormatConfig["table"]>) {
    onChange({ ...config, table: { ...config.table, ...patch } });
  }

  function patchImage(patch: Partial<ExportFormatConfig["image"]>) {
    onChange({ ...config, image: { ...config.image, ...patch } });
  }

  const disabled = Boolean(readOnly);

  return (
    <div className={`ef-form${disabled ? " is-readonly" : ""}`}>
      <div className="ef-editor-tabs">
        {(
          [
            ["quick", "快捷预设"],
            ["page", "页面设置"],
            ["headings", "标题样式"],
            ["body", "正文样式"],
            ["table", "表格与图片"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={`ef-editor-tab${tab === id ? " is-active" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "quick" && (
        <div className="ef-section">
          <div className="field">
            <label>模板名称</label>
            <input
              value={config.template_name}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...config, template_name: e.target.value })
              }
              placeholder="例如：政务投标-装订版"
            />
          </div>

          <h3 className="ef-section-title">版面预设（对齐 C 端）</h3>
          <div className="ef-preset-grid">
            {EXPORT_LAYOUT_PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                className="ef-preset-card"
                disabled={disabled}
                onClick={() => onApplyLayout(p.id)}
              >
                <strong>{p.label}</strong>
                <span>{p.description}</span>
              </button>
            ))}
          </div>

          <h3 className="ef-section-title">主题色</h3>
          <div className="ef-theme-row">
            {EXPORT_THEME_PRESETS.map((t) => (
              <button
                key={t.id}
                type="button"
                className="ef-theme-chip"
                disabled={disabled}
                title={t.description}
                onClick={() => onApplyTheme(t.id)}
              >
                <span className="ef-theme-swatches">
                  {t.swatches.map((c) => (
                    <i key={c} style={{ background: c }} />
                  ))}
                </span>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {tab === "page" && (
        <div className="ef-section">
          <div className="ef-form-grid">
            <div className="field">
              <label>纸张</label>
              <select
                disabled={disabled}
                value={config.page.paper_size}
                onChange={(e) =>
                  patchPage({
                    paper_size: e.target.value as ExportFormatConfig["page"]["paper_size"],
                  })
                }
              >
                {PAPER_SIZES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label} · {p.detail}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>方向</label>
              <select
                disabled={disabled}
                value={config.page.orientation}
                onChange={(e) =>
                  patchPage({
                    orientation: e.target.value as "portrait" | "landscape",
                  })
                }
              >
                <option value="portrait">纵向</option>
                <option value="landscape">横向</option>
              </select>
            </div>
          </div>
          <div className="ef-form-grid">
            {(
              [
                ["margin_top_cm", "上边距 cm"],
                ["margin_bottom_cm", "下边距 cm"],
                ["margin_left_cm", "左边距 cm"],
                ["margin_right_cm", "右边距 cm"],
              ] as const
            ).map(([key, label]) => (
              <div className="field" key={key}>
                <label>{label}</label>
                <input
                  type="number"
                  min={1}
                  max={5}
                  step={0.1}
                  disabled={disabled}
                  value={config.page[key]}
                  onChange={(e) =>
                    patchPage({ [key]: Number(e.target.value) } as Partial<
                      ExportFormatConfig["page"]
                    >)
                  }
                />
              </div>
            ))}
          </div>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.heading_level1_page_break_before}
              onChange={(e) =>
                onChange({
                  ...config,
                  heading_level1_page_break_before: e.target.checked,
                })
              }
            />
            一级标题前分页
          </label>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.page.first_page_different}
              onChange={(e) => patchPage({ first_page_different: e.target.checked })}
            />
            首页不同（装订/封面场景）
          </label>

          <h3 className="ef-section-title">页眉页脚</h3>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.page.header_enabled}
              onChange={(e) => patchPage({ header_enabled: e.target.checked })}
            />
            启用页眉
          </label>
          <div className="field">
            <label>页眉文字</label>
            <input
              disabled={disabled || !config.page.header_enabled}
              value={config.page.header_text}
              onChange={(e) => patchPage({ header_text: e.target.value })}
            />
          </div>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.page.footer_enabled}
              onChange={(e) => patchPage({ footer_enabled: e.target.checked })}
            />
            启用页脚
          </label>
          <div className="field">
            <label>页脚文字</label>
            <input
              disabled={disabled || !config.page.footer_enabled}
              value={config.page.footer_text}
              onChange={(e) => patchPage({ footer_text: e.target.value })}
            />
          </div>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.page.page_number_enabled}
              onChange={(e) => patchPage({ page_number_enabled: e.target.checked })}
            />
            显示页码
          </label>
          <div className="ef-form-grid">
            <div className="field">
              <label>页码格式</label>
              <input
                disabled={disabled || !config.page.page_number_enabled}
                value={config.page.page_number_format}
                onChange={(e) => patchPage({ page_number_format: e.target.value })}
                placeholder="第{page}页"
              />
            </div>
            <div className="field">
              <label>起始页码</label>
              <input
                type="number"
                min={1}
                disabled={disabled || !config.page.page_number_enabled}
                value={config.page.page_number_start}
                onChange={(e) =>
                  patchPage({ page_number_start: Number(e.target.value) })
                }
              />
            </div>
          </div>
        </div>
      )}

      {tab === "headings" && (
        <div className="ef-section">
          <p className="ef-hint">
            六级标题分别配置字体、中文字号、对齐、段前段后、编号格式（与 C 端一致）。
          </p>
          {config.headings.map((heading, index) => (
            <div key={index} className="ef-heading-card">
              <button
                type="button"
                className="ef-heading-card__head"
                onClick={() =>
                  setOpenHeading((cur) => (cur === index ? -1 : index))
                }
              >
                <strong>
                  {HEADING_LEVEL_LABELS[index] || `第 ${index + 1} 级`}
                </strong>
                <span>
                  {heading.font} · {heading.size} · {heading.alignment}
                </span>
              </button>
              {openHeading === index && (
                <div className="ef-heading-card__body">
                  <div className="ef-form-grid">
                    <div className="field">
                      <label>字体</label>
                      <select
                        disabled={disabled}
                        value={heading.font}
                        onChange={(e) =>
                          patchHeading(index, { font: e.target.value })
                        }
                      >
                        {FONT_OPTIONS.map((f) => (
                          <option key={f} value={f}>
                            {f}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label>字号</label>
                      <select
                        disabled={disabled}
                        value={heading.size}
                        onChange={(e) =>
                          patchHeading(index, { size: e.target.value })
                        }
                      >
                        {SIZE_OPTIONS.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label>对齐</label>
                      <select
                        disabled={disabled}
                        value={heading.alignment}
                        onChange={(e) =>
                          patchHeading(index, { alignment: e.target.value })
                        }
                      >
                        {ALIGNMENT_OPTIONS.map((a) => (
                          <option key={a} value={a}>
                            {a}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label>文字颜色</label>
                      <input
                        type="color"
                        disabled={disabled}
                        value={heading.text_color || "#243048"}
                        onChange={(e) =>
                          patchHeading(index, { text_color: e.target.value })
                        }
                      />
                    </div>
                  </div>
                  <div className="ef-form-grid">
                    <div className="field">
                      <label>段前 (pt)</label>
                      <input
                        type="number"
                        disabled={disabled}
                        value={heading.spacing_before_pt}
                        onChange={(e) =>
                          patchHeading(index, {
                            spacing_before_pt: Number(e.target.value),
                          })
                        }
                      />
                    </div>
                    <div className="field">
                      <label>段后 (pt)</label>
                      <input
                        type="number"
                        disabled={disabled}
                        value={heading.spacing_after_pt}
                        onChange={(e) =>
                          patchHeading(index, {
                            spacing_after_pt: Number(e.target.value),
                          })
                        }
                      />
                    </div>
                    <div className="field">
                      <label>行距倍数</label>
                      <input
                        type="number"
                        step={0.1}
                        disabled={disabled}
                        value={heading.line_spacing}
                        onChange={(e) =>
                          patchHeading(index, {
                            line_spacing: Number(e.target.value),
                          })
                        }
                      />
                    </div>
                  </div>
                  <label className="ef-check">
                    <input
                      type="checkbox"
                      disabled={disabled}
                      checked={heading.bold}
                      onChange={(e) =>
                        patchHeading(index, { bold: e.target.checked })
                      }
                    />
                    加粗
                  </label>
                  <div className="ef-form-grid">
                    <div className="field">
                      <label>编号格式</label>
                      <select
                        disabled={disabled}
                        value={heading.numbering_format}
                        onChange={(e) =>
                          patchHeading(index, {
                            numbering_format: e.target
                              .value as HeadingStyleConfig["numbering_format"],
                          })
                        }
                      >
                        {HEADING_NUMBERING_FORMAT_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label>自定义编号模板</label>
                      <input
                        disabled={
                          disabled || heading.numbering_format !== "custom"
                        }
                        value={heading.numbering_template}
                        onChange={(e) =>
                          patchHeading(index, {
                            numbering_template: e.target.value,
                          })
                        }
                        placeholder="第{zh}章 / {tail}"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.heading_border.enabled}
              onChange={(e) =>
                onChange({
                  ...config,
                  heading_border: {
                    ...config.heading_border,
                    enabled: e.target.checked,
                  },
                })
              }
            />
            启用章节页框（标题边框装饰）
          </label>
        </div>
      )}

      {tab === "body" && (
        <div className="ef-section">
          <div className="ef-form-grid">
            <div className="field">
              <label>正文字体</label>
              <select
                disabled={disabled}
                value={config.body_text.font}
                onChange={(e) => patchBody({ font: e.target.value })}
              >
                {FONT_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>正文字号</label>
              <select
                disabled={disabled}
                value={config.body_text.size}
                onChange={(e) => patchBody({ size: e.target.value })}
              >
                {SIZE_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>对齐</label>
              <select
                disabled={disabled}
                value={config.body_text.alignment}
                onChange={(e) => patchBody({ alignment: e.target.value })}
              >
                {ALIGNMENT_OPTIONS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>行距倍数</label>
              <input
                type="number"
                step={0.05}
                disabled={disabled}
                value={config.body_text.line_spacing_multiple}
                onChange={(e) =>
                  patchBody({ line_spacing_multiple: Number(e.target.value) })
                }
              />
            </div>
            <div className="field">
              <label>首行缩进（字）</label>
              <input
                type="number"
                min={0}
                max={4}
                disabled={disabled}
                value={config.body_text.first_line_indent_chars}
                onChange={(e) =>
                  patchBody({ first_line_indent_chars: Number(e.target.value) })
                }
              />
            </div>
            <div className="field">
              <label>列表缩进（字）</label>
              <input
                type="number"
                min={0}
                max={6}
                disabled={disabled}
                value={config.body_text.list_indent_chars}
                onChange={(e) =>
                  patchBody({ list_indent_chars: Number(e.target.value) })
                }
              />
            </div>
            <div className="field">
              <label>无序列表</label>
              <select
                disabled={disabled}
                value={config.body_text.list_style}
                onChange={(e) =>
                  patchBody({
                    list_style: e.target
                      .value as ExportFormatConfig["body_text"]["list_style"],
                  })
                }
              >
                {LIST_STYLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>有序列表</label>
              <select
                disabled={disabled}
                value={config.body_text.ordered_list_style}
                onChange={(e) =>
                  patchBody({
                    ordered_list_style: e.target
                      .value as ExportFormatConfig["body_text"]["ordered_list_style"],
                  })
                }
              >
                {ORDERED_LIST_STYLE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      {tab === "table" && (
        <div className="ef-section">
          <h3 className="ef-section-title">表格</h3>
          <div className="ef-form-grid">
            <div className="field">
              <label>边框宽度</label>
              <input
                type="number"
                min={0}
                max={4}
                step={0.5}
                disabled={disabled}
                value={config.table.border_width}
                onChange={(e) =>
                  patchTable({ border_width: Number(e.target.value) })
                }
              />
            </div>
            <div className="field">
              <label>边框颜色</label>
              <input
                type="color"
                disabled={disabled}
                value={config.table.border_color}
                onChange={(e) => patchTable({ border_color: e.target.value })}
              />
            </div>
            <div className="field">
              <label>单元格内边距 pt</label>
              <input
                type="number"
                disabled={disabled}
                value={config.table.cell_padding_pt}
                onChange={(e) =>
                  patchTable({ cell_padding_pt: Number(e.target.value) })
                }
              />
            </div>
          </div>
          <label className="ef-check">
            <input
              type="checkbox"
              disabled={disabled}
              checked={config.table.full_width}
              onChange={(e) => patchTable({ full_width: e.target.checked })}
            />
            表格通栏
          </label>
          <div className="ef-form-grid">
            <div className="field">
              <label>表头字体</label>
              <select
                disabled={disabled}
                value={config.table.header_row.font}
                onChange={(e) =>
                  patchTable({
                    header_row: {
                      ...config.table.header_row,
                      font: e.target.value,
                    },
                  })
                }
              >
                {FONT_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>表头字号</label>
              <select
                disabled={disabled}
                value={config.table.header_row.size}
                onChange={(e) =>
                  patchTable({
                    header_row: {
                      ...config.table.header_row,
                      size: e.target.value,
                    },
                  })
                }
              >
                {SIZE_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <h3 className="ef-section-title">图片</h3>
          <div className="ef-form-grid">
            <div className="field">
              <label>最大宽度 %</label>
              <input
                type="number"
                min={20}
                max={100}
                disabled={disabled}
                value={config.image.max_width_percent}
                onChange={(e) =>
                  patchImage({ max_width_percent: Number(e.target.value) })
                }
              />
            </div>
            <div className="field">
              <label>图片对齐</label>
              <select
                disabled={disabled}
                value={config.image.alignment}
                onChange={(e) => patchImage({ alignment: e.target.value })}
              >
                {ALIGNMENT_OPTIONS.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>题注字体</label>
              <select
                disabled={disabled}
                value={config.image.caption_font}
                onChange={(e) => patchImage({ caption_font: e.target.value })}
              >
                {FONT_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>题注字号</label>
              <select
                disabled={disabled}
                value={config.image.caption_size}
                onChange={(e) => patchImage({ caption_size: e.target.value })}
              >
                {SIZE_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
