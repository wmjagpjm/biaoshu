/**
 * 模块：资源中心类型
 * 用途：精选写作/合规/模板类资源卡片与详情。
 * 对接：可选 VITE_RESOURCES_URL；默认 mock。
 */

export type ResourceTone = "blue" | "violet" | "cyan" | "slate";

export type ResourceItem = {
  id: string;
  title: string;
  description: string;
  tags: string[];
  /** 详情正文（Markdown 纯文本，pre-wrap 展示） */
  modalContent: string;
  tone: ResourceTone;
  clickCount: number;
  category?: string;
};
