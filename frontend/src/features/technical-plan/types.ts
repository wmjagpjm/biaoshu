/**
 * 模块：技术方案域类型
 * 用途：对齐 C 端 technical-plan 工作流的数据结构。
 * 对接：大纲/正文字段将进入 revise 与生成任务 body；后端可原样复用。
 * 二次开发：响应矩阵持久化字段与任务建议字段分离，建议不得直接当作已保存状态。
 */

export type TechnicalPlanStepId =
  | "document"
  | "analysis"
  | "outline"
  | "facts"
  | "content"
  | "export";

export type TechnicalPlanStepMeta = {
  id: TechnicalPlanStepId;
  index: number;
  title: string;
  description: string;
};

/** 大纲扩展模式（对齐 C 端 FREE / ALIGNED 展示） */
export type OutlineExpansionMode = "ALIGNED" | "FREE";

export type OutlineNode = {
  id: string;
  title: string;
  level: 1 | 2 | 3;
  /** 目标字数（二级/三级常用） */
  targetWords?: number;
  /** 章节说明（可选，便于 AI 扩写） */
  description?: string;
  children?: OutlineNode[];
};

export type GlobalFact = {
  id: string;
  category: string;
  content: string;
  source: "tender" | "knowledge" | "manual";
};

export type ChapterContent = {
  id: string;
  title: string;
  wordCount: number;
  status: "pending" | "generating" | "done" | "needs_review";
  /** 列表摘要（可由 body 派生） */
  preview: string;
  /** 可编辑 Markdown 正文 */
  body: string;
};

/** 结构化招标分析（对接 analysis_json） */
export type ScoringPoint = {
  name: string;
  weight: string;
};

export type ResponseMatrixKind = "requirement" | "scoring";

export type ResponseMatrixStatus =
  | "uncovered"
  | "partial"
  | "covered"
  | "waived";

export type ResponseMatrixItem = {
  id: string;
  kind: ResponseMatrixKind;
  sourceKey: string;
  sourceIndex: number;
  sourceText: string;
  weight: string;
  chapterIds: string[];
  outlineNodeIds: string[];
  status: ResponseMatrixStatus;
  notes: string;
};

export type ResponseMatrixSuggestionStatus =
  | "uncovered"
  | "partial"
  | "covered";

export type ResponseMatrixSuggestion = {
  sourceKey: string;
  chapterIds: string[];
  outlineNodeIds: string[];
  status: ResponseMatrixSuggestionStatus;
  confidence: number;
  reason: string;
  base: Pick<
    ResponseMatrixItem,
    "chapterIds" | "outlineNodeIds" | "status"
  >;
};

export type BidAnalysis = {
  overview: string;
  techRequirements: string[];
  rejectionRisks: string[];
  scoringPoints: ScoringPoint[];
};

export function emptyBidAnalysis(): BidAnalysis {
  return {
    overview: "",
    techRequirements: [],
    rejectionRisks: [],
    scoringPoints: [],
  };
}

/** 用途：序列化分析供 revise baseContent */
export function serializeBidAnalysis(a: BidAnalysis): string {
  const lines = [
    "【项目概述】",
    a.overview || "（空）",
    "",
    "【技术要求】",
    ...(a.techRequirements.length
      ? a.techRequirements.map((t, i) => `${i + 1}. ${t}`)
      : ["（空）"]),
    "",
    "【废标风险】",
    ...(a.rejectionRisks.length
      ? a.rejectionRisks.map((t, i) => `${i + 1}. ${t}`)
      : ["（空）"]),
    "",
    "【评分点】",
    ...(a.scoringPoints.length
      ? a.scoringPoints.map((s) => `- ${s.name}　${s.weight}`)
      : ["（空）"]),
  ];
  return lines.join("\n");
}
