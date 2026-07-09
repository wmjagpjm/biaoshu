/**
 * 模块：标讯类型
 * 用途：招标线索列表与筛选。
 * 对接：后续公开招标 API / RSS；当前 mock。
 */

export type BidOppStatus = "open" | "closing_soon" | "closed";

export type BidOpportunity = {
  id: string;
  title: string;
  buyer: string;
  region: string;
  budgetLabel: string;
  /** 展示用截止日期 */
  deadline: string;
  status: BidOppStatus;
  tags: string[];
  summary: string;
  sourceLabel: string;
};

export const BID_STATUS_LABEL: Record<BidOppStatus, string> = {
  open: "进行中",
  closing_soon: "即将截止",
  closed: "已截止",
};
