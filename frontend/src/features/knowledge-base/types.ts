/**
 * 模块：知识库类型
 * 用途：文档知识库（文件夹 + 解析状态）+ 卡片化素材库 + 图片卡 + P9C 离线语义索引读模型。
 * 对接：GET|POST /api/knowledge/*；/api/knowledge/semantic-index*；/api/cards；useKnowledgeBase / useKnowledgeCards。
 * 二次开发：语义索引仅展示固定离线模型；禁止增加 URL/Token/缓存路径/供应商等可写配置字段。
 */

export type KbTab = "documents" | "cards" | "images";

/** P9C 服务端固定离线模型标识（前端只读展示，不可改） */
export const SEMANTIC_FIXED_MODEL_ID = "BAAI/bge-small-zh-v1.5";

/** P9C 固定向量维度（前端只读展示与归一化；禁止依赖后端任意返回值） */
export const SEMANTIC_FIXED_DIMENSION = 512;

/**
 * 用途：维度仅允许 512；非 512（含负数、小数、非数、字符串）一律收敛为 512。
 * 对接：normalizeSemanticIndex；页面展示不得直接使用 API 原始 dimension。
 */
export function normalizeSemanticDimension(raw: unknown): number {
  if (
    typeof raw === "number" &&
    Number.isFinite(raw) &&
    raw === SEMANTIC_FIXED_DIMENSION
  ) {
    return SEMANTIC_FIXED_DIMENSION;
  }
  return SEMANTIC_FIXED_DIMENSION;
}

/** 语义索引运行状态（与后端 SemanticIndexStatus 对齐） */
export type SemanticIndexStatus =
  | "queued"
  | "running"
  | "active"
  | "failed"
  | "superseded"
  | "index_not_built";

/** 语义索引固定错误码（与后端 SemanticIndexErrorCode 对齐） */
export type SemanticIndexErrorCode =
  | "model_unavailable"
  | "model_storage_insufficient"
  | "index_interrupted"
  | "index_failed"
  | "index_not_built"
  | "index_building";

const SEMANTIC_STATUS_VALUES: readonly SemanticIndexStatus[] = [
  "queued",
  "running",
  "active",
  "failed",
  "superseded",
  "index_not_built",
];

const SEMANTIC_ERROR_CODE_VALUES: readonly SemanticIndexErrorCode[] = [
  "model_unavailable",
  "model_storage_insufficient",
  "index_interrupted",
  "index_failed",
  "index_not_built",
  "index_building",
];

/** 用途：将 API 原始 status 收敛为联合类型；未知值安全降为未构建。 */
export function normalizeSemanticStatus(raw: unknown): SemanticIndexStatus {
  if (
    typeof raw === "string" &&
    (SEMANTIC_STATUS_VALUES as readonly string[]).includes(raw)
  ) {
    return raw as SemanticIndexStatus;
  }
  return "index_not_built";
}

/**
 * 用途：将 API 原始 errorCode 收敛为联合类型；未知非空值降为 index_failed，禁止透传原文。
 */
export function normalizeSemanticErrorCode(
  raw: unknown,
): SemanticIndexErrorCode | null {
  if (raw == null || raw === "") return null;
  if (
    typeof raw === "string" &&
    (SEMANTIC_ERROR_CODE_VALUES as readonly string[]).includes(raw)
  ) {
    return raw as SemanticIndexErrorCode;
  }
  return "index_failed";
}

/**
 * 用途：规范化语义索引读模型（status/errorCode 联合类型；modelId 仅兼容保留）。
 * 说明：展示层必须使用 SEMANTIC_FIXED_MODEL_ID 与 SEMANTIC_FIXED_DIMENSION，
 * 不得以 modelId/dimension 脏数据决定面板文案或行为。
 */
export function normalizeSemanticIndex(raw: unknown): SemanticIndex {
  const row =
    raw && typeof raw === "object"
      ? (raw as Record<string, unknown>)
      : ({} as Record<string, unknown>);
  const num = (v: unknown, fallback = 0) =>
    typeof v === "number" && Number.isFinite(v) ? v : fallback;
  const strOrNull = (v: unknown) =>
    typeof v === "string" && v.length > 0 ? v : null;
  return {
    id: strOrNull(row.id),
    workspaceId: strOrNull(row.workspaceId),
    status: normalizeSemanticStatus(row.status),
    provider: "offline_bge",
    // 仅内部兼容；页面始终展示 SEMANTIC_FIXED_MODEL_ID
    modelId:
      typeof row.modelId === "string" && row.modelId
        ? row.modelId
        : SEMANTIC_FIXED_MODEL_ID,
    modelFingerprint: strOrNull(row.modelFingerprint),
    // P9C 契约：维度固定 512；1536/负数/小数等一律收敛
    dimension: normalizeSemanticDimension(row.dimension),
    totalChunks: num(row.totalChunks),
    embeddedChunks: num(row.embeddedChunks),
    chunkCount: num(row.chunkCount),
    errorCode: normalizeSemanticErrorCode(row.errorCode),
    startedAt: strOrNull(row.startedAt),
    finishedAt: strOrNull(row.finishedAt),
    createdAt: strOrNull(row.createdAt),
    updatedAt: strOrNull(row.updatedAt),
  };
}

/**
 * 模块：P9C 语义索引读模型
 * 用途：知识库页状态面板展示；不含路径、密钥、正文。
 * 对接：GET/POST /api/knowledge/semantic-index*。
 * 二次开发：禁止加入 modelUrl、apiKey、cachePath 或 localStorage 伪就绪字段。
 */
export type SemanticIndex = {
  id: string | null;
  workspaceId: string | null;
  status: SemanticIndexStatus;
  provider: "offline_bge";
  /** 后端字段兼容保留；UI 不得展示或依赖其决定行为 */
  modelId: string;
  modelFingerprint: string | null;
  /** 归一化后恒为 512；禁止直接展示 API 原始 dimension */
  dimension: number;
  totalChunks: number;
  embeddedChunks: number;
  chunkCount: number;
  errorCode: SemanticIndexErrorCode | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
};

/** 固定中文：语义状态拉取失败（禁止透传 err.message） */
export const SEMANTIC_STATUS_UNAVAILABLE_MSG = "语义索引状态不可用";

/** 固定中文：重建启动失败（禁止透传 err.message） */
export const SEMANTIC_REBUILD_FAILED_MSG = "启动语义索引构建失败";

/** 固定中文：本地演示模式不可构建 */
export const SEMANTIC_LOCAL_MODE_MSG =
  "当前为本地演示模式，无法构建离线语义索引，请连接后端后重试";

/** 用途：是否处于构建中（按钮禁用 + 轮询）。 */
export function isSemanticIndexBuilding(index: SemanticIndex | null): boolean {
  if (!index) return false;
  if (index.status === "queued" || index.status === "running") return true;
  return index.errorCode === "index_building";
}

/** 用途：面板主状态中文（固定码表，禁止透传远端原文）。 */
export function semanticStatusLabel(index: SemanticIndex | null): string {
  if (!index) return "未构建 · 关键词降级";
  if (isSemanticIndexBuilding(index)) return "构建中";
  if (index.status === "active") return "已就绪";
  if (index.status === "failed") {
    if (index.errorCode === "model_unavailable") return "模型不可用";
    if (index.errorCode === "model_storage_insufficient") return "磁盘空间不足";
    if (index.errorCode === "index_interrupted") return "构建中断";
    return "构建失败";
  }
  if (index.errorCode === "model_unavailable") return "模型不可用";
  if (index.errorCode === "model_storage_insufficient") return "磁盘空间不足";
  if (index.errorCode === "index_interrupted") return "构建中断";
  if (
    index.status === "index_not_built" ||
    index.errorCode === "index_not_built"
  ) {
    return "未构建 · 关键词降级";
  }
  if (index.status === "superseded") return "已替换";
  // 未知状态：固定中文，绝不回显原始 status 字符串
  return "状态未知 · 关键词降级";
}

/**
 * 用途：关键词降级/失败原因（固定中文，不含路径与 Token）。
 */
export function semanticDegradeReason(index: SemanticIndex | null): string | null {
  if (!index) return "尚未构建语义索引，当前仅关键词检索";
  if (isSemanticIndexBuilding(index)) {
    return "索引构建中，检索暂以关键词为主";
  }
  if (index.status === "active") return null;
  switch (index.errorCode) {
    case "model_unavailable":
      return "本机离线模型未就绪，已降级为关键词检索";
    case "model_storage_insufficient":
      return "本机可用磁盘不足，已降级为关键词检索";
    case "index_interrupted":
      return "上次构建中断，已降级为关键词检索";
    case "index_failed":
      return "语义索引构建失败，已降级为关键词检索";
    case "index_not_built":
    default:
      if (index.status === "failed") {
        return "语义索引不可用，已降级为关键词检索";
      }
      return "尚未构建语义索引，当前仅关键词检索";
  }
}

/** 用途：主操作按钮文案。 */
export function semanticActionLabel(index: SemanticIndex | null): string {
  if (!index) return "构建语义索引";
  if (
    index.status === "failed" ||
    index.errorCode === "model_unavailable" ||
    index.errorCode === "index_failed" ||
    index.errorCode === "index_interrupted" ||
    index.errorCode === "model_storage_insufficient"
  ) {
    return "重试构建";
  }
  if (index.status === "active") return "重建语义索引";
  return "构建语义索引";
}

/** 知识卡片类型（与后端 knowledge_cards.type 对齐） */
export type KnowledgeCardType =
  | "document"
  | "image"
  | "qualification"
  | "performance";

export type KnowledgeCardStatus = "active" | "archived";

/** 列表摘要：无正文全文、无 base64 */
export type KnowledgeCardSummary = {
  id: string;
  workspaceId: string;
  type: KnowledgeCardType;
  title: string;
  tags: string[];
  status: KnowledgeCardStatus;
  summary: string;
  sourceType: string;
  sourceId: string | null;
  sourceLabel: string;
  hasBody: boolean;
  hasImage: boolean;
  contentType?: string | null;
  sizeBytes: number;
  createdAt: string;
  updatedAt: string;
};

/** 详情：含正文快照与图片元数据 */
export type KnowledgeCard = KnowledgeCardSummary & {
  bodyMarkdown: string;
  payload?: Record<string, unknown> | null;
  storedName?: string | null;
};

export type InsertCardResult = {
  markdown: string;
  projectImageId: string | null;
  cardId: string;
  cardType: KnowledgeCardType;
  title: string;
  sourceLabel: string;
};

export const CARD_TYPE_LABEL: Record<KnowledgeCardType, string> = {
  document: "文档片段",
  image: "图片",
  qualification: "资质",
  performance: "业绩",
};

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
