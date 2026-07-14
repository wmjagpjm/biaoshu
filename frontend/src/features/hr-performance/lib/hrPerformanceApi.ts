/**
 * 模块：P10H 人员业绩素材卡 API 封装
 * 用途：仅调用 /hr/performance-cards*；cardId 经 encodeURIComponent。
 * 对接：apiFetch（内存 CSRF）；useHrPerformanceCards；HrPerformanceCardsPage。
 * 二次开发：禁止回退 /projects、editor-state、credential-cards、team-recommendations、财务、投标人或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  HrPerformanceCardCreateBody,
  HrPerformanceCardDetail,
  HrPerformanceCardListResponse,
  HrPerformanceCardSummary,
  HrPerformanceCardUpdateBody,
} from "../types";

/**
 * 模块：fetchHrPerformanceCards
 * 用途：加载当前工作空间人员业绩卡摘要列表。
 * 对接：GET /hr/performance-cards。
 * 二次开发：列表不得假设含 performanceSummary/remark；禁止预取详情。
 */
export async function fetchHrPerformanceCards(): Promise<
  HrPerformanceCardSummary[]
> {
  const res = await apiFetch<HrPerformanceCardListResponse>(
    "/hr/performance-cards",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 模块：fetchHrPerformanceCard
 * 用途：加载单卡详情（含 performanceSummary 与 remark）。
 * 对接：GET /hr/performance-cards/{cardId}。
 * 二次开发：cardId 必须 encodeURIComponent；仅用户点选后调用。
 */
export async function fetchHrPerformanceCard(
  cardId: string,
): Promise<HrPerformanceCardDetail> {
  const id = encodeURIComponent(cardId);
  return apiFetch<HrPerformanceCardDetail>(`/hr/performance-cards/${id}`);
}

/**
 * 模块：createHrPerformanceCard
 * 用途：新建人员业绩卡。
 * 对接：POST /hr/performance-cards（CSRF 由 apiFetch）。
 * 二次开发：成功后由 Hook 强制重读列表与详情，禁止乐观伪造。
 */
export async function createHrPerformanceCard(
  body: HrPerformanceCardCreateBody,
): Promise<HrPerformanceCardDetail> {
  return apiFetch<HrPerformanceCardDetail>("/hr/performance-cards", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * 模块：updateHrPerformanceCard
 * 用途：更新人员业绩卡或启停。
 * 对接：PATCH /hr/performance-cards/{cardId}。
 * 二次开发：isActive 仅 JSON 布尔；成功后须服务端重读。
 */
export async function updateHrPerformanceCard(
  cardId: string,
  body: HrPerformanceCardUpdateBody,
): Promise<HrPerformanceCardDetail> {
  const id = encodeURIComponent(cardId);
  return apiFetch<HrPerformanceCardDetail>(`/hr/performance-cards/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
