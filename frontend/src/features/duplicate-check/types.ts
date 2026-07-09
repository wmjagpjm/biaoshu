/**
 * 模块：标书查重类型
 * 用途：重复段落命中与对照展示数据结构。
 * 对接：后续 POST /api/duplicate-check/run；当前 mock。
 */

export type DupCompareScope = "kb+history" | "kb" | "self";

export type DupHit = {
  id: string;
  chapter: string;
  chapterId?: string;
  /** 0~1 */
  similarity: number;
  /** 本文段落 */
  currentText: string;
  /** 对比来源段落 */
  sourceText: string;
  /** 知识库 · xxx / 历史项目 · xxx */
  sourceLabel: string;
  /** 改写建议 */
  suggestion?: string;
};
