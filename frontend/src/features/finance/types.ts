/**
 * 模块：P10B/P10C/P10K 财务报价、成本草案与项目成本变更记录类型
 * 用途：对齐 GET /api/finance/business-bids*、cost-draft 与项目 cost-change-events 白名单投影。
 * 对接：financeApi；useFinanceQuotes；useFinanceCostDraft；FinanceQuotePage。
 * 二次开发：禁止扩展税率/审批/币种；禁止透传 business_json、actor 身份、金额或审计原文。
 */

/** 用途：财务报价列表项（项目摘要 + 行数/合计）。 */
export type FinanceBusinessBidSummary = {
  projectId: string;
  name: string;
  industry: string;
  status: string;
  updatedAt: string;
  quoteRowCount: number;
  quoteTotal: number;
};

/** 用途：明细中的单行报价分项。 */
export type FinanceQuoteRow = {
  id: string;
  name: string;
  unit: string;
  quantity: string;
  unitPrice: string;
  amount: number | null;
  remark: string;
};

/** 用途：财务报价明细（列表字段 + 分项 + 备注）。 */
export type FinanceBusinessBidDetail = FinanceBusinessBidSummary & {
  quoteRows: FinanceQuoteRow[];
  quoteNotes: string;
};

/** 用途：列表接口响应包装。 */
export type FinanceBusinessBidListResponse = {
  items: FinanceBusinessBidSummary[];
};

/** 用途：成本条目固定类别枚举（与后端一致）。 */
export type FinanceCostCategory = "labor" | "material" | "service" | "other";

/** 用途：成本草案单条响应字段白名单（无创建人/工作空间）。 */
export type FinanceCostEntry = {
  id: string;
  category: FinanceCostCategory;
  name: string;
  amountFen: number;
  remark: string;
  createdAt: string;
  updatedAt: string;
};

/** 用途：成本草案汇总与毛利快照（GET cost-draft）。 */
export type FinanceCostDraft = {
  projectId: string;
  projectName: string;
  quoteTotalFen: number;
  costTotalFen: number;
  grossProfitFen: number;
  grossMarginBasisPoints: number | null;
  costEntries: FinanceCostEntry[];
};

/** 用途：新建成本条目写入体（仅四字段）。 */
export type FinanceCostEntryCreateBody = {
  category: FinanceCostCategory;
  name: string;
  amountFen: number;
  remark: string;
};

/** 用途：更新成本条目（至少一个可改字段）。 */
export type FinanceCostEntryUpdateBody = {
  category?: FinanceCostCategory;
  name?: string;
  amountFen?: number;
  remark?: string;
};

/** 用途：P10K 项目成本变更动作枚举；仅 create/update/delete。 */
export type FinanceProjectCostChangeAction = "create" | "update" | "delete";

/** 用途：服务端映射的操作者范围；仅 self/other，无成员身份。 */
export type FinanceProjectCostActorScope = "self" | "other";

/**
 * 用途：P10K 单条项目成本变更记录白名单字段。
 * 注意：不得扩展金额/名称/备注/成员 ID/事件 ID/projectId。
 */
export type FinanceProjectCostChangeEventItem = {
  action: FinanceProjectCostChangeAction;
  entryId: string;
  actorScope: FinanceProjectCostActorScope;
  occurredAt: string;
};

/** 用途：GET .../cost-change-events 成功响应体。 */
export type FinanceProjectCostChangeEventsResponse = {
  items: FinanceProjectCostChangeEventItem[];
};
