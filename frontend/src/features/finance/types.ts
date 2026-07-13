/**
 * 模块：P10B 财务报价只读类型
 * 用途：对齐 GET /api/finance/business-bids* 白名单投影，仅含契约字段。
 * 对接：financeApi；useFinanceQuotes；FinanceQuotePage。
 * 二次开发：禁止扩展成本/利润/税率或透传 business_json 额外键。
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
