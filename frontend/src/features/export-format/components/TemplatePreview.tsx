import { useMemo, type CSSProperties } from "react";
import type { ExportFormatConfig } from "../model/exportFormat";
import { HEADING_LEVEL_LABELS } from "../model/exportFormat";
import { buildExportFormatCssVars } from "../model/exportFormatCss";
import "./TemplatePreview.css";

type Props = {
  config: ExportFormatConfig;
};

/**
 * 模板实时预览
 * 用途：对齐 C 端 TemplatePreview，用 CSS 变量渲染纸张/标题/正文效果。
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
          <div className="ef-h1">
            {sampleTitle(h[0], "第一章 项目理解与建设目标")}
          </div>
          <div className="ef-h2">
            {sampleTitle(h[1], "第一节 招标需求解读")}
          </div>
          <div className="ef-h3">{sampleTitle(h[2], "1 业务痛点与建设范围")}</div>
          <p className="ef-body">
            这是正文预览段落。字体、字号、行距与首行缩进将按模板配置渲染。
            系统支持六级标题（
            {HEADING_LEVEL_LABELS.slice(0, 3).join("、")}…）与表格、图片样式。
          </p>
          <div className="ef-h3">{sampleTitle(h[2], "2 建设目标与成功标准")}</div>
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

function sampleTitle(
  heading: ExportFormatConfig["headings"][number] | undefined,
  fallback: string,
) {
  if (!heading) return fallback;
  // 预览不完整解析编号模板，直接展示示例文案
  return fallback;
}
