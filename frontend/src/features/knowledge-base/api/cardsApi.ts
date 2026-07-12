/**
 * 模块：知识卡片 API 客户端
 * 用途：封装 /api/cards 与项目 insert-card，供知识库页与章节编辑器复用。
 * 对接：card_service；useKnowledgeCards；ChapterEditor 插入卡片。
 * 二次开发：禁止在客户端拼接 data URL 或卡片磁盘路径写入正文。
 */

import { apiFetch, getApiBase } from "../../../shared/lib/api";
import type {
  InsertCardResult,
  KnowledgeCard,
  KnowledgeCardStatus,
  KnowledgeCardSummary,
  KnowledgeCardType,
} from "../types";

/** 列表状态：active|archived|all；缺省由后端按 active 处理 */
export type ListCardsStatus = KnowledgeCardStatus | "all";

export type ListCardsParams = {
  q?: string;
  type?: KnowledgeCardType | "";
  status?: ListCardsStatus | "";
};

/** 用途：列表摘要（轻量）。status 缺省不传则后端仅返回 active。 */
export function listCards(params: ListCardsParams = {}) {
  const qs = new URLSearchParams();
  if (params.q?.trim()) qs.set("q", params.q.trim());
  if (params.type) qs.set("type", params.type);
  if (params.status) qs.set("status", params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<KnowledgeCardSummary[]>(`/cards${suffix}`);
}

/** 用途：卡片详情。 */
export function getCard(cardId: string) {
  return apiFetch<KnowledgeCard>(`/cards/${encodeURIComponent(cardId)}`);
}

/** 用途：手工创建文本类卡片。 */
export function createTextCard(body: {
  type: Exclude<KnowledgeCardType, "image">;
  title: string;
  bodyMarkdown: string;
  tags?: string[];
  summary?: string;
  sourceLabel?: string;
}) {
  return apiFetch<KnowledgeCard>("/cards", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 用途：上传图片沉淀为 image 卡。 */
export function uploadImageCard(file: File, meta?: { title?: string; tags?: string }) {
  const form = new FormData();
  form.append("file", file);
  if (meta?.title) form.append("title", meta.title);
  if (meta?.tags) form.append("tags", meta.tags);
  return apiFetch<KnowledgeCard>("/cards/upload-image", {
    method: "POST",
    body: form,
  });
}

/** 用途：从知识分块沉淀。 */
export function createCardFromChunk(body: {
  chunkId: string;
  title?: string;
  tags?: string[];
  type?: Exclude<KnowledgeCardType, "image">;
}) {
  return apiFetch<KnowledgeCard>("/cards/from-chunk", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 用途：更新卡片。 */
export function updateCard(
  cardId: string,
  body: Partial<{
    title: string;
    tags: string[];
    status: KnowledgeCardStatus;
    summary: string;
    bodyMarkdown: string;
    sourceLabel: string;
  }>,
) {
  return apiFetch<KnowledgeCard>(`/cards/${encodeURIComponent(cardId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** 用途：删除卡片（不影响已插入项目图片）。 */
export function deleteCard(cardId: string) {
  return apiFetch<void>(`/cards/${encodeURIComponent(cardId)}`, {
    method: "DELETE",
  });
}

/**
 * 用途：图片卡内容 URL（走同源 /api 代理，刷新后仍可用，不依赖 localStorage）。
 */
export function cardContentUrl(cardId: string): string {
  return `${getApiBase()}/cards/${encodeURIComponent(cardId)}/content`;
}

/**
 * 用途：向项目生成可插入 Markdown（图片会复制为项目 role=image）。
 * 对接：POST /api/projects/{id}/insert-card。
 */
export function insertCardIntoProject(projectId: string, cardId: string) {
  return apiFetch<InsertCardResult>(
    `/projects/${encodeURIComponent(projectId)}/insert-card`,
    {
      method: "POST",
      body: JSON.stringify({ cardId }),
    },
  );
}
