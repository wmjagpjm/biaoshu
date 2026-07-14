/**
 * 模块：P10G 投标人项目级合规统计 API 封装
 * 用途：仅调用选择器与单项目统计两条只读接口；ID 经 encodeURIComponent。
 * 对接：apiFetch（内存 CSRF 会话）；useBidderProjectCompliance。
 * 二次开发：禁止回退 /projects、/editor-state、/settings、/files、/finance、/hr、
 *   P10E /bidder/compliance-preview 或外网；禁止写入 localStorage/sessionStorage。
 */

import { apiFetch } from "../../../shared/lib/api";
import type {
  BidderProjectComplianceDetail,
  BidderProjectComplianceProjectList,
} from "../types";

/**
 * 模块：fetchBidderProjectComplianceProjects
 * 用途：读取当前空间技术标最小选择器（仅 id/name）。
 * 对接：GET /bidder/project-compliance/projects。
 * 二次开发：仅 React 内存持有；不预取单项目详情。
 */
export async function fetchBidderProjectComplianceProjects(): Promise<BidderProjectComplianceProjectList> {
  return apiFetch<BidderProjectComplianceProjectList>(
    "/bidder/project-compliance/projects",
  );
}

/**
 * 模块：fetchBidderProjectComplianceDetail
 * 用途：按用户选择读取单项目响应矩阵统计投影。
 * 对接：GET /bidder/project-compliance/{projectId}。
 * 二次开发：projectId 必须 encodeURIComponent；禁止 URL 查询参数或浏览器持久化。
 */
export async function fetchBidderProjectComplianceDetail(
  projectId: string,
): Promise<BidderProjectComplianceDetail> {
  const id = encodeURIComponent(projectId);
  return apiFetch<BidderProjectComplianceDetail>(
    `/bidder/project-compliance/${id}`,
  );
}

/**
 * 模块：formatCoverageBasisPoints
 * 用途：将整数基点展示为百分比（例 8182→81.82%）；null 固定文案。
 * 对接：BidderProjectCompliancePage 覆盖率单元格。
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
