/**
 * 标书导出模板类型
 * 用途：对齐 C 端 export-format / templateStore 的数据结构，
 * 用于 Word 导出样式（字体、标题层级、页边距、行距等）。
 */

export type TemplateSource = "system" | "user";

/** 单条导出模板（系统预设或用户自定义） */
export type ExportTemplate = {
  id: string;
  name: string;
  description: string;
  source: TemplateSource;
  /** 是否为当前默认导出模板 */
  isDefault: boolean;
  createdAt: string;
  updatedAt: string;
  /** 样式配置 */
  style: ExportStyleConfig;
};

export type ExportStyleConfig = {
  /** 正文字体 */
  bodyFont: string;
  /** 标题字体 */
  headingFont: string;
  /** 一级标题字号（磅） */
  h1Size: number;
  h2Size: number;
  h3Size: number;
  /** 正文字号（磅） */
  bodySize: number;
  /** 行距倍数 */
  lineHeight: number;
  /** 首行缩进（字符） */
  firstLineIndent: number;
  /** 页边距 mm */
  marginTop: number;
  marginRight: number;
  marginBottom: number;
  marginLeft: number;
  /** 是否生成 Word 目录域 */
  includeToc: boolean;
  /** 页眉文案，空则无 */
  headerText: string;
  /** 是否显示页码 */
  showPageNumber: boolean;
  /** 封面标题 */
  coverTitle: string;
};

export const FONT_OPTIONS = [
  "宋体",
  "黑体",
  "仿宋",
  "楷体",
  "微软雅黑",
  "方正小标宋简体",
] as const;

export function createDefaultStyle(): ExportStyleConfig {
  return {
    bodyFont: "宋体",
    headingFont: "黑体",
    h1Size: 16,
    h2Size: 14,
    h3Size: 12,
    bodySize: 12,
    lineHeight: 1.5,
    firstLineIndent: 2,
    marginTop: 25.4,
    marginRight: 25.4,
    marginBottom: 25.4,
    marginLeft: 25.4,
    includeToc: true,
    headerText: "",
    showPageNumber: true,
    coverTitle: "技术方案",
  };
}
