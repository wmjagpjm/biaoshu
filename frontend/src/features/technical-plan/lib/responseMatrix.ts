/**
 * 模块：响应矩阵合并工具
 * 用途：从结构化招标分析派生矩阵行，并按 sourceKey 保留用户章节映射与备注。
 * 对接：useTechnicalPlanEditors、ResponseMatrixPanel、editor-state responseMatrix 字段。
 * 二次开发：勿用 sourceIndex 作为主键；新增来源类型时同步后端 normalize_response_matrix。
 */

import type {
  BidAnalysis,
  ChapterContent,
  OutlineNode,
  ResponseMatrixItem,
  ResponseMatrixKind,
  ResponseMatrixSuggestion,
  ResponseMatrixSuggestionStatus,
  ResponseMatrixStatus,
} from "../types";

export type ResponseMatrixCoverage = {
  validChapterIds: string[];
  validOutlineNodeIds: string[];
  invalidCount: number;
};

type MatrixSource = {
  kind: ResponseMatrixKind;
  sourceIndex: number;
  sourceText: string;
  weight: string;
  sourceKey: string;
};

const STATUSES: ResponseMatrixStatus[] = [
  "uncovered",
  "partial",
  "covered",
  "waived",
];
const SUGGESTION_STATUSES: ResponseMatrixSuggestionStatus[] = [
  "uncovered",
  "partial",
  "covered",
];

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function stableId(sourceKey: string): string {
  let hash = 0;
  for (let i = 0; i < sourceKey.length; i += 1) {
    hash = (hash * 31 + sourceKey.charCodeAt(i)) >>> 0;
  }
  return `mx_${hash.toString(16).padStart(8, "0")}`;
}

export function makeResponseMatrixSourceKey(
  kind: ResponseMatrixKind,
  sourceText: string,
): string {
  /** 用途：生成矩阵来源稳定键，避免条目调序后错绑。 */
  return `${kind}:${normalizeText(sourceText)}`;
}

function uniqueStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

export function normalizeResponseMatrix(
  raw: unknown,
): ResponseMatrixItem[] {
  /** 用途：规范 API/localStorage 中的响应矩阵，坏行丢弃，状态降级为未覆盖。 */
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item, index) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Partial<ResponseMatrixItem>;
    if (row.kind !== "requirement" && row.kind !== "scoring") return [];
    const sourceText = String(row.sourceText || "").trim();
    if (!sourceText) return [];
    const sourceKey =
      String(row.sourceKey || "").trim() ||
      makeResponseMatrixSourceKey(row.kind, sourceText);
    const status = STATUSES.includes(row.status as ResponseMatrixStatus)
      ? (row.status as ResponseMatrixStatus)
      : "uncovered";
    return [
      {
        id: String(row.id || "").trim() || stableId(sourceKey),
        kind: row.kind,
        sourceKey,
        sourceIndex: Number.isFinite(Number(row.sourceIndex))
          ? Math.max(0, Number(row.sourceIndex))
          : index,
        sourceText,
        weight: String(row.weight || ""),
        chapterIds: uniqueStrings(row.chapterIds),
        outlineNodeIds: uniqueStrings(row.outlineNodeIds),
        status,
        notes: String(row.notes || ""),
      },
    ];
  });
}

export function normalizeResponseMatrixSuggestions(
  raw: unknown,
): ResponseMatrixSuggestion[] {
  /** 用途：规范任务结果中的待确认建议，不把异常数据带入人工应用流程。 */
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Partial<ResponseMatrixSuggestion>;
    const sourceKey = String(value.sourceKey || "").trim();
    if (!sourceKey || seen.has(sourceKey)) return [];
    const status = SUGGESTION_STATUSES.includes(
      value.status as ResponseMatrixSuggestionStatus,
    )
      ? (value.status as ResponseMatrixSuggestionStatus)
      : "uncovered";
    const base = value.base;
    if (!base || typeof base !== "object") return [];
    seen.add(sourceKey);
    return [
      {
        sourceKey,
        chapterIds: uniqueStrings(value.chapterIds),
        outlineNodeIds: uniqueStrings(value.outlineNodeIds),
        status,
        confidence: Math.max(0, Math.min(100, Number(value.confidence) || 0)),
        reason: String(value.reason || "").trim().slice(0, 500),
        base: {
          chapterIds: uniqueStrings(base.chapterIds),
          outlineNodeIds: uniqueStrings(base.outlineNodeIds),
          status: STATUSES.includes(base.status as ResponseMatrixStatus)
            ? (base.status as ResponseMatrixStatus)
            : "uncovered",
        },
      },
    ];
  });
}

function collectSources(analysis: BidAnalysis): MatrixSource[] {
  const sources: MatrixSource[] = [];
  analysis.techRequirements.forEach((text, index) => {
    const sourceText = String(text || "").trim();
    if (!sourceText) return;
    sources.push({
      kind: "requirement",
      sourceIndex: index,
      sourceText,
      weight: "",
      sourceKey: makeResponseMatrixSourceKey("requirement", sourceText),
    });
  });
  analysis.scoringPoints.forEach((point, index) => {
    const sourceText = String(point.name || "").trim();
    if (!sourceText) return;
    sources.push({
      kind: "scoring",
      sourceIndex: index,
      sourceText,
      weight: String(point.weight || ""),
      sourceKey: makeResponseMatrixSourceKey("scoring", sourceText),
    });
  });
  return sources;
}

export function mergeResponseMatrix(
  analysis: BidAnalysis,
  previous: ResponseMatrixItem[],
): ResponseMatrixItem[] {
  /**
   * 用途：按当前 analysis 生成矩阵；同 sourceKey 的旧行继承映射、状态和备注。
   * 对接：分析步保存、AI 分析后 reloadFromApi、手动刷新响应矩阵。
   */
  const bySource = new Map(
    normalizeResponseMatrix(previous).map((item) => [item.sourceKey, item]),
  );
  return collectSources(analysis).map((source) => {
    const old = bySource.get(source.sourceKey);
    return {
      id: old?.id || stableId(source.sourceKey),
      kind: source.kind,
      sourceKey: source.sourceKey,
      sourceIndex: source.sourceIndex,
      sourceText: source.sourceText,
      weight: source.weight,
      chapterIds: old?.chapterIds ?? [],
      outlineNodeIds: old?.outlineNodeIds ?? [],
      status: old?.status ?? "uncovered",
      notes: old?.notes ?? "",
    };
  });
}

export function collectOutlineOptions(nodes: OutlineNode[]) {
  /** 用途：把大纲树展开为可勾选选项，保留层级展示。 */
  const options: Array<{ id: string; title: string; level: number }> = [];
  const walk = (items: OutlineNode[]) => {
    for (const item of items) {
      options.push({ id: item.id, title: item.title, level: item.level });
      if (item.children?.length) walk(item.children);
    }
  };
  walk(nodes);
  return options;
}

export function reconcileResponseMatrixLinks(
  items: ResponseMatrixItem[],
  chapters: ChapterContent[],
  outline: OutlineNode[],
): ResponseMatrixItem[] {
  /**
   * 用途：移除已不存在的章节/大纲引用；无有效引用时降级覆盖状态。
   * 对接：大纲替换、章节回读、矩阵保存前的状态收敛。
   */
  const chapterSet = new Set(chapters.map((chapter) => chapter.id));
  const outlineSet = new Set(collectOutlineOptions(outline).map((node) => node.id));
  return normalizeResponseMatrix(items).map((item) => {
    const chapterIds = item.chapterIds.filter((id) => chapterSet.has(id));
    const outlineNodeIds = item.outlineNodeIds.filter((id) => outlineSet.has(id));
    const hasValidLink = chapterIds.length + outlineNodeIds.length > 0;
    return {
      ...item,
      chapterIds,
      outlineNodeIds,
      status:
        hasValidLink || item.status === "waived"
          ? item.status
          : "uncovered",
    };
  });
}

export function getResponseMatrixCoverage(
  item: ResponseMatrixItem,
  chapters: ChapterContent[],
  outline: OutlineNode[],
): ResponseMatrixCoverage {
  /** 用途：计算矩阵行的有效引用和失效引用数量，避免死 id 被算作已覆盖。 */
  const chapterSet = new Set(chapters.map((chapter) => chapter.id));
  const outlineSet = new Set(collectOutlineOptions(outline).map((node) => node.id));
  const validChapterIds = item.chapterIds.filter((id) => chapterSet.has(id));
  const validOutlineNodeIds = item.outlineNodeIds.filter((id) => outlineSet.has(id));
  return {
    validChapterIds,
    validOutlineNodeIds,
    invalidCount:
      item.chapterIds.length +
      item.outlineNodeIds.length -
      validChapterIds.length -
      validOutlineNodeIds.length,
  };
}
