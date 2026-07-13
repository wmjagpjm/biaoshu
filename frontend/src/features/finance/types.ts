/**
 * 模块：P10B/P10C 财务报价与成本草案类型
 * 用途：对齐 GET /api/finance/business-bids* 与 cost-draft 白名单投影，仅含契约字段。
 * 对接：financeApi；useFinanceQuotes；useFinanceCostDraft；FinanceQuotePage。
 * 二次开发：禁止扩展税率/审批/币种或透传 business_json、createdBy 等越界键。
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
