/**
 * 模块：P10G 投标人项目级合规统计类型
 * 用途：对齐 GET /api/bidder/project-compliance/* 白名单投影，仅含契约字段。
 * 对接：bidderProjectComplianceApi；useBidderProjectCompliance；BidderProjectCompliancePage。
 * 二次开发：禁止扩展矩阵行、sourceKey、章节/大纲、备注、workspace、人员/财务字段。
 */

/** 用途：单项目统计数据态（ready=有条目；empty=总条目为零）。 */
export type BidderProjectComplianceDataState = "ready" | "empty";

/** 用途：与 P10E 一致的五项统计与覆盖率基点。 */
export type BidderProjectComplianceSummary = {
  totalItems: number;
  coveredItems: number;
  uncoveredItems: number;
  waivedItems: number;
  /** 整数基点；无条目或分母为零时为 null */
  coverageBasisPoints: number | null;
};

/** 用途：选择器项目项，仅 id/name。 */
export type BidderProjectComplianceProjectItem = {
  id: string;
  name: string;
};

/** 用途：GET /bidder/project-compliance/projects 成功响应体。 */
export type BidderProjectComplianceProjectList = {
  items: BidderProjectComplianceProjectItem[];
};

/** 用途：GET /bidder/project-compliance/{projectId} 成功响应体。 */
export type BidderProjectComplianceDetail = {
  dataState: BidderProjectComplianceDataState;
  summary: BidderProjectComplianceSummary;
};
