/**
 * 模块：标讯类型
 * 用途：工作空间本地标讯库的列表、编辑和立项读模型；以及国能计划追踪仪表盘读模型。
 * 对接：GET/POST/PATCH/DELETE /api/opportunities；/api/opportunity-watch/*。
 * 二次开发：外部信息源必须保留服务端 workspace 校验和动态截止状态；前端不得伪造 announcementUrl 或直连国能站点。
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

export type OpportunityWatchRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed";

export type OpportunityWatchExtractionStatus = "resolved" | "needs_review";

export type OpportunityWatchErrorCode =
  | "source_unavailable"
  | "rate_limited"
  | "malformed_response"
  | "interrupted";

export type OpportunityWatchSyncRun = {
  id: string;
  workspaceId: string;
  sourceName: "chnenergy";
  status: OpportunityWatchRunStatus;
  startedAt: string;
  finishedAt: string | null;
  planCount: number;
  candidateCount: number;
  detailPageCount: number;
  resolvedCount: number;
  needsReviewCount: number;
  skippedCount: number;
  errorCode: OpportunityWatchErrorCode | null;
  createdAt: string;
  updatedAt: string;
};

export type OpportunityWatchHit = {
  id: string;
  workspaceId: string;
  watchPlanId: string;
  syncRunId: string;
  sourceName: "chnenergy";
  sourceInfoId: string;
  categoryNum: string;
  sourcePublishText: string;
  title: string;
  deadlineAtLocal: string | null;
  openingAtLocal: string | null;
  sourceTimezone: "Asia/Shanghai";
  extractionStatus: OpportunityWatchExtractionStatus;
  acceptedOpportunityId: string | null;
  /** 仅后端动态生成；前端只读展示，不得拼接或提交 */
  announcementUrl: string | null;
  createdAt: string;
  updatedAt: string;
};

export type OpportunityWatchDashboard = {
  planCount: number;
  latestRun: OpportunityWatchSyncRun | null;
  hits: OpportunityWatchHit[];
};

export type OpportunityWatchPlanImportResult = {
  inserted: number;
  skipped: number;
  total: number;
};

export type OpportunityWatchAcceptResult = {
  opportunityId: string;
  created: boolean;
};

export const BID_STATUS_LABEL: Record<BidOppStatus, string> = {
  open: "进行中",
  closing_soon: "即将截止",
  closed: "已截止",
};

export const WATCH_RUN_STATUS_LABEL: Record<OpportunityWatchRunStatus, string> = {
  queued: "排队中",
  running: "正在同步",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "失败",
};

export const WATCH_EXTRACTION_LABEL: Record<
  OpportunityWatchExtractionStatus,
  string
> = {
  resolved: "待人工确认",
  needs_review: "待复核",
};
