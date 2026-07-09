/**
 * 知识库类型
 * 用途：文档知识库 + 图片知识库（B 端补齐 C 端图文素材能力）。
 */

export type KbTab = "documents" | "images";

export type KnowledgeDoc = {
  id: string;
  name: string;
  tags: string[];
  chunks: number;
  updated: string;
  category: string;
};

export type KnowledgeImage = {
  id: string;
  name: string;
  /** 展示用缩略图：远程 URL 或 data URL */
  thumbUrl: string;
  /** 原始预览 */
  url: string;
  tags: string[];
  category: string;
  width?: number;
  height?: number;
  sizeLabel: string;
  /** 用途说明，生成配图时作检索文案 */
  caption: string;
  updatedAt: string;
};

export type ImageCategory = {
  id: string;
  name: string;
  count: number;
};
