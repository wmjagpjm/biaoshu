/**
 * 模块：P10B 财务报价只读 API 封装
 * 用途：仅调用两个专用 GET 端点，返回白名单投影。
 * 对接：apiFetch；GET /finance/business-bids*；useFinanceQuotes。
 * 二次开发：禁止回退到 /projects、/editor-state、/settings、/files 或任何写接口。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  FinanceBusinessBidDetail,
  FinanceBusinessBidListResponse,
  FinanceBusinessBidSummary,
} from "../types";

/**
 * 用途：加载当前工作空间商务标报价列表。
 * 对接：GET /finance/business-bids。
 */
export async function fetchFinanceBusinessBids(): Promise<
  FinanceBusinessBidSummary[]
> {
  const res = await apiFetch<FinanceBusinessBidListResponse>(
    "/finance/business-bids",
  );
  return Array.isArray(res?.items) ? res.items : [];
}

/**
 * 用途：加载单项目商务标报价明细。
 * 对接：GET /finance/business-bids/{projectId}。
 */
export async function fetchFinanceBusinessBidDetail(
  projectId: string,
): Promise<FinanceBusinessBidDetail> {
  const id = encodeURIComponent(projectId);
  return apiFetch<FinanceBusinessBidDetail>(`/finance/business-bids/${id}`);
}
