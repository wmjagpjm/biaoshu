/**
 * 模块：P10I 人员资质到期提示 API 封装
 * 用途：仅调用 GET /hr/credential-expiry；无 body/query/写接口。
 * 对接：apiFetch；useHrCredentialExpiry；HrCredentialExpiryPage。
 * 二次开发：禁止回退 credential-cards/team-recommendations/performance-cards、项目、财务或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type { HrCredentialExpirySummary } from "../types";

/**
 * 模块：fetchHrCredentialExpiry
 * 用途：读取当前工作空间人员资质到期摘要与关注列表。
 * 对接：GET /hr/credential-expiry。
 * 二次开发：不得附加 asOf/window 查询参数；结果仅存 React 内存。
 */
export async function fetchHrCredentialExpiry(): Promise<HrCredentialExpirySummary> {
  return apiFetch<HrCredentialExpirySummary>("/hr/credential-expiry");
}
