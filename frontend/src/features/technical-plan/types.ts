/**
 * 技术方案域类型
 * 用途：对齐 C 端 technical-plan 工作流的数据结构。
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

export type OutlineNode = {
  id: string;
  title: string;
  level: 1 | 2 | 3;
  targetWords?: number;
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
  preview: string;
};
