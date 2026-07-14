/**
 * 模块：P10E 投标人匿名合规预览类型
 * 用途：对齐 GET /api/bidder/compliance-preview 白名单投影，仅含契约字段。
 * 对接：bidderComplianceApi；useBidderCompliancePreview；BidderCompliancePreviewPage。
 * 二次开发：禁止扩展项目 ID/名称、矩阵行、源文、备注、workspace 或任意内部键。
 */

/** 用途：预览数据态（ready=有条目；empty=总条目为零）。 */
export type BidderComplianceDataState = "ready" | "empty";

/** 用途：匿名汇总计数与覆盖率基点。 */
export type BidderComplianceSummary = {
  totalItems: number;
  coveredItems: number;
  uncoveredItems: number;
  waivedItems: number;
  /** 整数基点；总条目为零或分母为零时为 null */
  coverageBasisPoints: number | null;
};

/** 用途：GET /bidder/compliance-preview 成功响应体。 */
export type BidderCompliancePreview = {
  dataState: BidderComplianceDataState;
  summary: BidderComplianceSummary;
};
