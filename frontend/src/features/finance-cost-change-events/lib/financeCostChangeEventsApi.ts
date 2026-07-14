/**
 * 模块：P10J 财务个人成本变更记录 API 封装
 * 用途：仅调用 GET /finance/cost-change-events；无 body/query/写接口。
 * 对接：apiFetch；useFinanceCostChangeEvents；FinanceCostChangeEventsPage。
 * 二次开发：禁止回退 /projects、/finance/business-bids、cost-draft、auth 以外业务路径或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  FinanceCostChangeEventItem,
  FinanceCostChangeEventsResponse,
} from "../types";

/**
 * 模块：normalizeItems
 * 用途：将响应 items 安全收敛为数组；非数组或缺失时返回空数组。
 * 对接：fetchFinanceCostChangeEvents。
 * 二次开发：禁止在此反查项目/金额或补全缺失字段。
 */
function normalizeItems(raw: unknown): FinanceCostChangeEventItem[] {
  if (!Array.isArray(raw)) return [];
  return raw as FinanceCostChangeEventItem[];
}

/**
 * 模块：fetchFinanceCostChangeEvents
 * 用途：读取当前账户在当前工作空间的成功成本变更记录（服务端固定最近 50 条）。
 * 对接：GET /finance/cost-change-events。
 * 二次开发：不得附加 limit/cursor/user 查询参数；结果仅存 React 内存。
 */
export async function fetchFinanceCostChangeEvents(): Promise<FinanceCostChangeEventsResponse> {
  const data = await apiFetch<FinanceCostChangeEventsResponse>(
    "/finance/cost-change-events",
  );
  return {
    items: normalizeItems(data?.items),
  };
}
