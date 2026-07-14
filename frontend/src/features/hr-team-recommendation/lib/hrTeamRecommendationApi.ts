/**
 * 模块：P10F 团队推荐 API 封装
 * 用途：仅调用 HR 团队推荐与 bid_writer 投影端点；ID 均 encodeURIComponent。
 * 对接：apiFetch（内存 CSRF）；useHrTeamRecommendations；BidWriter 面板。
 * 二次开发：禁止回退完整 /projects*、/hr/credential-cards/{id}、editor-state、财务或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  BidWriterTeamRecommendation,
  HrTeamProjectSelectorItem,
  HrTeamProjectSelectorList,
  HrTeamRecommendationDetail,
  HrTeamRecommendationPutBody,
  HrTeamRecommendationSummary,
  HrTeamRecommendationSummaryList,
} from "../types";

/**
 * 用途：加载 HR 技术标项目选择器（仅 id/name）。
 * 对接：GET /hr/team-recommendations/projects。
 * 二次开发：不得改用 GET /projects。
 */
export async function fetchHrTeamProjects(): Promise<
  HrTeamProjectSelectorItem[]
> {
  const res = await apiFetch<HrTeamProjectSelectorList>(
    "/hr/team-recommendations/projects",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 用途：加载当前空间推荐摘要列表。
 * 对接：GET /hr/team-recommendations。
 * 二次开发：仅摘要；无成员明细。
 */
export async function fetchHrTeamRecommendationSummaries(): Promise<
  HrTeamRecommendationSummary[]
> {
  const res = await apiFetch<HrTeamRecommendationSummaryList>(
    "/hr/team-recommendations",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 用途：加载某技术标项目的推荐编辑详情。
 * 对接：GET /hr/team-recommendations/{projectId}。
 * 二次开发：404 由调用方区分 not_found / project_not_found。
 */
export async function fetchHrTeamRecommendationDetail(
  projectId: string,
): Promise<HrTeamRecommendationDetail> {
  const id = encodeURIComponent(projectId);
  return apiFetch<HrTeamRecommendationDetail>(
    `/hr/team-recommendations/${id}`,
  );
}

/**
 * 用途：保存有序成员快照（首建或替换）。
 * 对接：PUT /hr/team-recommendations/{projectId}；CSRF 由 apiFetch。
 * 二次开发：body 仅 memberCardIds；禁止乐观伪造成功。
 */
export async function putHrTeamRecommendation(
  projectId: string,
  body: HrTeamRecommendationPutBody,
): Promise<HrTeamRecommendationDetail> {
  const id = encodeURIComponent(projectId);
  return apiFetch<HrTeamRecommendationDetail>(
    `/hr/team-recommendations/${id}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/**
 * 用途：标书制作者按需读取单项目只读投影。
 * 对接：GET /projects/{projectId}/team-recommendation。
 * 二次开发：不得回退 /hr/*；empty 为 200 非 404。
 */
export async function fetchBidWriterTeamRecommendation(
  projectId: string,
): Promise<BidWriterTeamRecommendation> {
  const id = encodeURIComponent(projectId);
  return apiFetch<BidWriterTeamRecommendation>(
    `/projects/${id}/team-recommendation`,
  );
}
