/**
 * 导出配置克隆与默认值
 * 用途：深拷贝 DEFAULT_EXPORT_FORMAT，避免引用污染。
 */
import type { ExportFormatConfig } from "./exportFormat";
import { DEFAULT_EXPORT_FORMAT } from "./exportFormat";

export function createDefaultExportFormat(name = "默认模版"): ExportFormatConfig {
  return {
    template_name: name,
    page: { ...DEFAULT_EXPORT_FORMAT.page },
    heading_level1_page_break_before:
      DEFAULT_EXPORT_FORMAT.heading_level1_page_break_before,
    heading_border: {
      ...DEFAULT_EXPORT_FORMAT.heading_border,
      level_cell_colors: [...DEFAULT_EXPORT_FORMAT.heading_border.level_cell_colors],
    },
    headings: DEFAULT_EXPORT_FORMAT.headings.map((h) => ({ ...h })),
    body_text: { ...DEFAULT_EXPORT_FORMAT.body_text },
    table: {
      border_width: DEFAULT_EXPORT_FORMAT.table.border_width,
      border_color: DEFAULT_EXPORT_FORMAT.table.border_color,
      cell_padding_pt: DEFAULT_EXPORT_FORMAT.table.cell_padding_pt,
      full_width: DEFAULT_EXPORT_FORMAT.table.full_width,
      header_row: { ...DEFAULT_EXPORT_FORMAT.table.header_row },
      first_column: { ...DEFAULT_EXPORT_FORMAT.table.first_column },
      body_cell: { ...DEFAULT_EXPORT_FORMAT.table.body_cell },
    },
    image: { ...DEFAULT_EXPORT_FORMAT.image },
  };
}

export function withExportFormatDefaults(
  source: Partial<ExportFormatConfig> | null | undefined,
): ExportFormatConfig {
  const defaults = createDefaultExportFormat();
  if (!source) return defaults;
  return {
    ...defaults,
    ...source,
    template_name: source.template_name || defaults.template_name,
    page: { ...defaults.page, ...source.page },
    heading_border: {
      ...defaults.heading_border,
      ...source.heading_border,
      level_cell_colors: defaults.heading_border.level_cell_colors.map(
        (color, index) => source.heading_border?.level_cell_colors?.[index] || color,
      ),
    },
    headings: defaults.headings.map((heading, index) => ({
      ...heading,
      ...(source.headings?.[index] || {}),
    })),
    body_text: { ...defaults.body_text, ...source.body_text },
    table: {
      ...defaults.table,
      ...source.table,
      header_row: { ...defaults.table.header_row, ...source.table?.header_row },
      first_column: {
        ...defaults.table.first_column,
        ...source.table?.first_column,
      },
      body_cell: { ...defaults.table.body_cell, ...source.table?.body_cell },
    },
    image: { ...defaults.image, ...source.image },
  };
}
