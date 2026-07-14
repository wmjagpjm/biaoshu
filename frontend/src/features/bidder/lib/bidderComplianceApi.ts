/**
 * 模块：P10E 投标人匿名合规预览 API 封装
 * 用途：仅调用 GET /bidder/compliance-preview；不带项目参数、筛选或写操作。
 * 对接：apiFetch（内存 CSRF 会话）；useBidderCompliancePreview。
 * 二次开发：禁止回退 /projects、/editor-state、/settings、/files、/finance、/hr 或外网。
 */

import { apiFetch } from "../../../shared/lib/api";
import type { BidderCompliancePreview } from "../types";

/**
 * 模块：fetchBidderCompliancePreview
 * 用途：读取当前工作空间匿名合规汇总投影。
 * 对接：GET /bidder/compliance-preview。
 * 二次开发：禁止缓存到 localStorage/sessionStorage；仅 React 内存持有结果。
 */
export async function fetchBidderCompliancePreview(): Promise<BidderCompliancePreview> {
  return apiFetch<BidderCompliancePreview>("/bidder/compliance-preview");
}

/**
 * 模块：formatCoverageBasisPoints
 * 用途：将整数基点展示为百分比（例 7273→72.73%）；null 固定文案。
 * 对接：BidderCompliancePreviewPage 覆盖率单元格。
 * 二次开发：不得用四项计数在客户端重新推算不同口径。
 */
export function formatCoverageBasisPoints(
  bps: number | null | undefined,
): string {
  if (bps == null) {
    return "暂无可计算覆盖率";
  }
  if (typeof bps !== "number" || !Number.isFinite(bps) || !Number.isInteger(bps)) {
    return "暂无可计算覆盖率";
  }
  const negative = bps < 0;
  const abs = bps < 0 ? -bps : bps;
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;
  return `${negative ? "-" : ""}${whole}.${String(frac).padStart(2, "0")}%`;
}
