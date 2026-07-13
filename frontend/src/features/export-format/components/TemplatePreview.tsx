/**
 * 模块：导出模板实时预览
 * 用途：用 CSS 变量近似呈现纸张、标题段落边框、叶子左栏、正文、表格及页眉页脚样式。
 * 对接：ExportFormatConfig、buildExportFormatCssVars、TemplatePreview.css。
 * 二次开发：预览只修饰标题行；Word 最终效果以后端 python-docx 输出为准。
 */

import { useMemo, type CSSProperties } from "react";
import type { ExportFormatConfig } from "../model/exportFormat";
import { HEADING_LEVEL_LABELS } from "../model/exportFormat";
import { buildExportFormatCssVars } from "../model/exportFormatCss";
import "./TemplatePreview.css";

type Props = {
  config: ExportFormatConfig;
};

/**
 * 用途：渲染模板示例页，并实时响应标题边框和各类样式配置。
 * 对接：模板编辑页传入的 ExportFormatConfig。
 */
export function TemplatePreview({ config }: Props) {
  const style = useMemo(
    () => buildExportFormatCssVars(config) as CSSProperties,
    [config],
  );

  const h = config.headings;

  return (
    <div className="ef-preview-wrap" style={style}>
      <div className="ef-paper">
        {config.page.header_enabled && (
          <div className="ef-paper__header">{config.page.header_text || "页眉示例"}</div>
        )}
        <div className="ef-paper__body">
          {/* 示例树：一级/二级有下级，仅两处三级为叶子 */}
          <div className={headingClassName(config, "ef-h1", 1, false)}>
            {sampleTitle(h[0], "第一章 项目理解与建设目标")}
          </div>
          <div className={headingClassName(config, "ef-h2", 2, false)}>
            {sampleTitle(h[1], "第一节 招标需求解读")}
          </div>
          <div className={headingClassName(config, "ef-h3", 3, true)}>
            {sampleTitle(h[2], "1 业务痛点与建设范围")}
          </div>
          <p className="ef-body">
            这是正文预览段落。字体、字号、行距与首行缩进将按模板配置渲染。
            系统支持六级标题（
            {HEADING_LEVEL_LABELS.slice(0, 3).join("、")}…）与表格、图片样式。
          </p>
          <div className={headingClassName(config, "ef-h3", 3, true)}>
            {sampleTitle(h[2], "2 建设目标与成功标准")}
          </div>
          <p className="ef-body">
            建设周期、接入规模与等保要求等关键事实应与全局事实保持一致，避免前后矛盾。
          </p>
          <table className="ef-table">
            <thead>
              <tr>
                <th>评分项</th>
                <th>权重</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>总体架构</td>
                <td>20%</td>
              </tr>
              <tr>
                <td>功能完整性</td>
                <td>25%</td>
              </tr>
            </tbody>
          </table>
          <p className="ef-caption">表 1 评分权重示例</p>
        </div>
        {(config.page.footer_enabled || config.page.page_number_enabled) && (
          <div className="ef-paper__footer">
            {config.page.footer_enabled ? config.page.footer_text || "页脚示例" : ""}
            {config.page.page_number_enabled
              ? ` ${(config.page.page_number_format || "第{page}页").replace("{page}", "1")}`
              : ""}
          </div>
        )}
      </div>
    </div>
  );
}

function headingClassName(
  config: ExportFormatConfig,
  baseClassName: string,
  level: number,
  isLeaf: boolean,
) {
  if (!config.heading_border.enabled) return baseClassName;
  const classes = [
    baseClassName,
    "ef-heading-frame",
    `ef-heading-frame--level-${level}`,
  ];
  if (config.heading_border.min_heading_left_enabled && isLeaf) {
    classes.push("ef-heading-frame--min-left");
  }
  return classes.join(" ");
}

function sampleTitle(
  heading: ExportFormatConfig["headings"][number] | undefined,
  fallback: string,
) {
  if (!heading) return fallback;
  // 预览不完整解析编号模板，直接展示示例文案
  return fallback;
}
