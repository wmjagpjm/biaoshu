/**
 * 模块：废标项检查类型
 * 用途：风险条目与招标条款 / 现状对照。
 * 对接：POST /api/projects/{id}/rejection-check
 */

export type RiskLevel = "high" | "medium" | "low";

export type RejectionItem = {
  id: string;
  level: RiskLevel;
  title: string;
  /** 招标侧条款摘录 */
  tenderClause: string;
  /** 当前投标/大纲/正文现状 */
  currentStatus: string;
  /** 修改建议 */
  suggestion: string;
  /** 跳转提示文案 */
  relatedLabel?: string;
  /** 处理入口路由 */
  relatedTo?: string;
};

export const RISK_LEVEL_LABEL: Record<RiskLevel, string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
};
