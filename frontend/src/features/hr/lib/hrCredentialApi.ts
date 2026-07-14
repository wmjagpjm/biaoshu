/**
 * 模块：P10D 人员资质素材卡 API 封装
 * 用途：仅调用 /hr/credential-cards*；cardId 经 encodeURIComponent。
 * 对接：apiFetch（内存 CSRF）；useHrCredentialCards；HrCredentialCardsPage。
 * 二次开发：禁止回退 /projects、/editor-state、/settings、/files、/export 或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  HrCredentialCardCreateBody,
  HrCredentialCardDetail,
  HrCredentialCardListResponse,
  HrCredentialCardSummary,
  HrCredentialCardUpdateBody,
} from "../types";

/**
 * 用途：加载当前工作空间人员资质卡摘要列表。
 * 对接：GET /hr/credential-cards。
 */
export async function fetchHrCredentialCards(): Promise<
  HrCredentialCardSummary[]
> {
  const res = await apiFetch<HrCredentialCardListResponse>(
    "/hr/credential-cards",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 用途：加载单卡详情（含 remark）。
 * 对接：GET /hr/credential-cards/{cardId}。
 */
export async function fetchHrCredentialCard(
  cardId: string,
): Promise<HrCredentialCardDetail> {
  const id = encodeURIComponent(cardId);
  return apiFetch<HrCredentialCardDetail>(`/hr/credential-cards/${id}`);
}

/**
 * 用途：新建人员资质卡。
 * 对接：POST /hr/credential-cards（CSRF 由 apiFetch）。
 */
export async function createHrCredentialCard(
  body: HrCredentialCardCreateBody,
): Promise<HrCredentialCardDetail> {
  return apiFetch<HrCredentialCardDetail>("/hr/credential-cards", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * 用途：更新人员资质卡或启停。
 * 对接：PATCH /hr/credential-cards/{cardId}。
 */
export async function updateHrCredentialCard(
  cardId: string,
  body: HrCredentialCardUpdateBody,
): Promise<HrCredentialCardDetail> {
  const id = encodeURIComponent(cardId);
  return apiFetch<HrCredentialCardDetail>(`/hr/credential-cards/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
