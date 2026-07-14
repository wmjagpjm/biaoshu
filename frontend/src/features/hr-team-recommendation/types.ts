/**
 * 模块：P10F 人力团队推荐快照类型
 * 用途：对齐 /api/hr/team-recommendations* 与 bid_writer 投影白名单字段。
 * 对接：hrTeamRecommendationApi；useHrTeamRecommendations；页面与只读面板。
 * 二次开发：禁止扩展 remark、操作者、workspace、完整项目或证件联系方式字段。
 */

import type { HrCredentialCategory } from "../hr/types";

/** 用途：HR 技术标项目选择器项（仅 id/name）。 */
export type HrTeamProjectSelectorItem = {
  id: string;
  name: string;
};

/** 用途：项目选择器列表包装。 */
export type HrTeamProjectSelectorList = {
  items: HrTeamProjectSelectorItem[];
};

/** 用途：推荐摘要（列表）。 */
export type HrTeamRecommendationSummary = {
  projectId: string;
  projectName: string;
  memberCount: number;
  updatedAt: string;
};

/** 用途：摘要列表包装。 */
export type HrTeamRecommendationSummaryList = {
  items: HrTeamRecommendationSummary[];
};

/** 用途：HR 编辑详情中的成员快照（含 sourceCardId，无 remark）。 */
export type HrTeamMemberSnapshot = {
  order: number;
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
  sourceCardId: string;
};

/** 用途：HR 项目团队推荐编辑详情。 */
export type HrTeamRecommendationDetail = {
  projectId: string;
  projectName: string;
  members: HrTeamMemberSnapshot[];
  updatedAt: string;
};

/** 用途：PUT 仅接受有序 memberCardIds。 */
export type HrTeamRecommendationPutBody = {
  memberCardIds: string[];
};

/** 用途：标书制作者投影成员（无 sourceCardId）。 */
export type BidWriterTeamMember = {
  order: number;
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level: string;
  validUntil: string | null;
};

/** 用途：标书制作者单项目只读投影。 */
export type BidWriterTeamRecommendation = {
  dataState: "empty" | "ready";
  members: BidWriterTeamMember[];
  updatedAt: string | null;
};
