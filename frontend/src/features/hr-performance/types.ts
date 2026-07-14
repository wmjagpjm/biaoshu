/**
 * 模块：P10H 人员业绩素材卡类型
 * 用途：对齐 GET/POST/PATCH /api/hr/performance-cards* 白名单投影，仅含契约字段。
 * 对接：hrPerformanceApi；useHrPerformanceCards；HrPerformanceCardsPage。
 * 二次开发：禁止扩展证件号/手机/附件/URL/workspace/createdBy/合同金额等越界键。
 */

/** 用途：列表摘要（不含 performanceSummary、remark）。 */
export type HrPerformanceCardSummary = {
  id: string;
  personName: string;
  projectName: string;
  projectRole: string;
  completedYear: number | null;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
};

/** 用途：单卡详情（摘要 + 业绩概述与备注）。 */
export type HrPerformanceCardDetail = HrPerformanceCardSummary & {
  performanceSummary: string;
  remark: string;
};

/** 用途：列表接口响应包装。 */
export type HrPerformanceCardListResponse = {
  items: HrPerformanceCardSummary[];
};

/** 用途：新建人员业绩卡写入体。 */
export type HrPerformanceCardCreateBody = {
  personName: string;
  projectName: string;
  projectRole?: string;
  completedYear?: number | null;
  performanceSummary: string;
  remark?: string;
  isActive?: boolean;
};

/** 用途：更新/启停（至少一个可改字段）。 */
export type HrPerformanceCardUpdateBody = {
  personName?: string;
  projectName?: string;
  projectRole?: string;
  completedYear?: number | null;
  performanceSummary?: string;
  remark?: string;
  isActive?: boolean;
};
