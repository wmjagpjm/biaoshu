/**
 * 模块：模板/卡片融合建议（M3-A）类型与规范化
 * 用途：解析 content_fuse 任务 result；仅只读展示，不写入 editor-state。
 * 对接：ContentFuseDialog；useProjectPipeline type=content_fuse。
 * 二次开发：M3-B 写入/差异预览另立类型；禁止在此触发章节替换。
 */

export type ContentFuseSourceRef = {
  kind: "template" | "card";
  id: string;
  /** 服务端从实际入 prompt 的模板/卡片补齐；展示优先用 title */
  title: string;
};

export type ContentFuseChapterBase = {
  bodyHash: string;
  bodyLength: number;
  title: string;
};

export type ContentFuseSuggestion = {
  suggestionId: string;
  targetChapterId: string;
  targetTitle: string;
  action: string;
  confidence: number;
  reason: string;
  sourceRefs: ContentFuseSourceRef[];
  base: ContentFuseChapterBase;
  currentPreview: string;
  proposedMarkdown: string;
  diffSummary: string;
};

export type ContentFuseSkippedSource = {
  kind: string;
  id: string;
  reason: string;
};

export type ContentFuseQuota = {
  templatesSelected: number;
  cardsSelected: number;
  targetsSelected: number;
  templatesUsed?: number;
  cardsUsed?: number;
  promptChars?: number;
  maxPromptChars?: number;
};

export type ContentFuseResult = {
  suggestions: ContentFuseSuggestion[];
  model: string;
  skippedSources: ContentFuseSkippedSource[];
  skippedInvalidCount: number;
  baseEditorUpdatedAt: string | null;
  quota: ContentFuseQuota;
  mode: string;
};

export type ContentFusePayload = {
  templateIds: string[];
  cardIds: string[];
  targetChapterIds: string[];
  mode: "merge_suggest";
};

/** 配额常量（与后端 fuse_context_service 对齐） */
export const CONTENT_FUSE_LIMITS = {
  maxTemplates: 3,
  maxCards: 8,
  maxSourcesTotal: 10,
  maxTargets: 5,
} as const;

function asString(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function asNumber(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * 用途：把任务 result 收敛为前端只读结构；非法字段丢弃。
 * 对接：ContentFuseDialog 成功态展示。
 */
export function normalizeContentFuseResult(
  raw: Record<string, unknown> | null | undefined,
): ContentFuseResult | null {
  if (!raw || typeof raw !== "object") return null;
  const suggestionsRaw = Array.isArray(raw.suggestions) ? raw.suggestions : [];
  const suggestions: ContentFuseSuggestion[] = [];
  for (const item of suggestionsRaw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const baseRaw =
      row.base && typeof row.base === "object"
        ? (row.base as Record<string, unknown>)
        : {};
    const refsRaw = Array.isArray(row.sourceRefs) ? row.sourceRefs : [];
    const sourceRefs: ContentFuseSourceRef[] = [];
    for (const ref of refsRaw) {
      if (!ref || typeof ref !== "object") continue;
      const r = ref as Record<string, unknown>;
      const kind = asString(r.kind);
      const id = asString(r.id).trim();
      if ((kind === "template" || kind === "card") && id) {
        // 保留服务端 title；缺失时退回空串，UI 再回退到 kind:id
        sourceRefs.push({
          kind,
          id,
          title: asString(r.title).trim(),
        });
      }
    }
    suggestions.push({
      suggestionId: asString(row.suggestionId) || `local_${suggestions.length}`,
      targetChapterId: asString(row.targetChapterId),
      targetTitle: asString(row.targetTitle),
      action: asString(row.action) || "merge_suggest",
      confidence: Math.max(0, Math.min(100, Math.round(asNumber(row.confidence)))),
      reason: asString(row.reason).slice(0, 60),
      sourceRefs,
      base: {
        bodyHash: asString(baseRaw.bodyHash),
        bodyLength: asNumber(baseRaw.bodyLength),
        title: asString(baseRaw.title),
      },
      currentPreview: asString(row.currentPreview).slice(0, 400),
      proposedMarkdown: asString(row.proposedMarkdown).slice(0, 12_000),
      diffSummary: asString(row.diffSummary).slice(0, 200),
    });
  }

  const skippedRaw = Array.isArray(raw.skippedSources) ? raw.skippedSources : [];
  const skippedSources: ContentFuseSkippedSource[] = skippedRaw
    .filter((s): s is Record<string, unknown> => !!s && typeof s === "object")
    .map((s) => ({
      kind: asString(s.kind),
      id: asString(s.id),
      reason: asString(s.reason),
    }));

  const quotaRaw =
    raw.quota && typeof raw.quota === "object"
      ? (raw.quota as Record<string, unknown>)
      : {};

  return {
    suggestions,
    model: asString(raw.model),
    skippedSources,
    skippedInvalidCount: Math.max(0, Math.round(asNumber(raw.skippedInvalidCount))),
    baseEditorUpdatedAt:
      raw.baseEditorUpdatedAt == null ? null : asString(raw.baseEditorUpdatedAt),
    quota: {
      templatesSelected: asNumber(quotaRaw.templatesSelected),
      cardsSelected: asNumber(quotaRaw.cardsSelected),
      targetsSelected: asNumber(quotaRaw.targetsSelected),
      templatesUsed: asNumber(quotaRaw.templatesUsed),
      cardsUsed: asNumber(quotaRaw.cardsUsed),
      promptChars: asNumber(quotaRaw.promptChars),
      maxPromptChars: asNumber(quotaRaw.maxPromptChars, 24_000),
    },
    mode: asString(raw.mode) || "merge_suggest",
  };
}

/**
 * 用途：构造 content_fuse 请求 payload；前端做软校验提示。
 */
export function buildContentFusePayload(input: {
  templateIds: string[];
  cardIds: string[];
  targetChapterIds: string[];
}): ContentFusePayload {
  return {
    templateIds: [...new Set(input.templateIds.filter(Boolean))],
    cardIds: [...new Set(input.cardIds.filter(Boolean))],
    targetChapterIds: [...new Set(input.targetChapterIds.filter(Boolean))],
    mode: "merge_suggest",
  };
}

/** 用途：配额文案。 */
export function formatFuseQuotaTip(payload: ContentFusePayload): string {
  const t = payload.templateIds.length;
  const c = payload.cardIds.length;
  const g = payload.targetChapterIds.length;
  return `模板 ${t}/${CONTENT_FUSE_LIMITS.maxTemplates} · 卡片 ${c}/${CONTENT_FUSE_LIMITS.maxCards} · 合计 ${t + c}/${CONTENT_FUSE_LIMITS.maxSourcesTotal} · 目标章 ${g}/${CONTENT_FUSE_LIMITS.maxTargets}`;
}

/**
 * 用途：来源芯片展示文案；优先 title，缺省回退 kind:短 id。
 * 对接：ContentFuseDialog 只读建议列表。
 */
export function formatFuseSourceRefLabel(ref: ContentFuseSourceRef): string {
  const title = (ref.title || "").trim();
  if (title) return title;
  return `${ref.kind}:${ref.id.slice(0, 12)}`;
}
