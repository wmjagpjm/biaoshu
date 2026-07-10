/**
 * 模块：知识库类型
 * 用途：文档知识库（文件夹 + 解析状态）+ 图片知识库。
 * 对接：GET|POST /api/knowledge/*；useKnowledgeBase 优先 API。
 */

export type KbTab = "documents" | "images";

/** 文档处理状态机 */
export type DocParseStatus =
  | "ready"
  | "parsing"
  | "indexing"
  | "failed"
  | "pending";

export const DOC_STATUS_LABEL: Record<DocParseStatus, string> = {
  ready: "已就绪",
  parsing: "解析中",
  indexing: "索引中",
  failed: "失败",
  pending: "待处理",
};

/** 知识库文件夹（一级；parentId 预留多级） */
export type KbFolder = {
  id: string;
  name: string;
  parentId: string | null;
};

export type KnowledgeDoc = {
  id: string;
  name: string;
  tags: string[];
  chunks: number;
  /** 展示用相对时间（兼容旧 UI） */
  updated: string;
  /** ISO 时间，排序与持久化用 */
  updatedAt: string;
  category: string;
  /** 所属文件夹 */
  folderId: string;
  status: DocParseStatus;
  statusMessage?: string;
  sizeLabel?: string;
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

/** 「全部文档」虚拟文件夹 id */
export const KB_FOLDER_ALL = "__all__";
