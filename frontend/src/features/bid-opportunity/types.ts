/**
 * 模块：标讯类型
 * 用途：工作空间本地标讯库的列表、编辑和立项读模型。
 * 对接：GET/POST/PATCH/DELETE /api/opportunities；POST /api/opportunities/{id}/projects。
 * 二次开发：外部信息源必须保留服务端 workspace 校验和动态截止状态，禁止前端伪造实时状态。
 */

export type BidOppStatus = "open" | "closing_soon" | "closed";

export type BidOpportunity = {
  id: string;
  workspaceId: string;
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
  createdAt: string;
  updatedAt: string;
};

export type BidOpportunityDraft = {
  title: string;
  buyer: string;
  region: string;
  budgetLabel: string;
  deadline: string;
  tagsText: string;
  summary: string;
  sourceLabel: string;
};

export type OpportunityImportResult = {
  inserted: number;
  skipped: number;
  total: number;
};

export const BID_STATUS_LABEL: Record<BidOppStatus, string> = {
  open: "进行中",
  closing_soon: "即将截止",
  closed: "已截止",
};
