/**
 * 模块：商务标领域类型
 * 用途：对齐 C 端 business-bid 信息架构，落地 B 端「分步工作区」数据结构。
 * 对接：后续 POST /api/business-bid/projects、/revise 等；当前前端 mock。
 */

/** 商务标六步流水线（与技术方案六步对位，但内容不同） */
export type BusinessBidStepId =
  | "parse"
  | "qualify"
  | "toc"
  | "quote"
  | "commit"
  | "export";

export type BusinessBidStepMeta = {
  id: BusinessBidStepId;
  index: number;
  title: string;
  description: string;
};

/** 资格条件逐条响应 */
export type QualifyItemStatus = "pending" | "matched" | "partial" | "missing";

export type QualifyItem = {
  id: string;
  /** 招标原文摘要 */
  requirement: string;
  /** 响应说明草稿 */
  response: string;
  /** 证明材料索引（知识库/附件） */
  evidence: string;
  status: QualifyItemStatus;
};

/** 商务目录 / 附件清单项 */
export type TocItemStatus = "required" | "optional" | "done";

export type TocItem = {
  id: string;
  title: string;
  category: string;
  status: TocItemStatus;
  checked: boolean;
  note?: string;
};

/** 报价 / 偏离表骨架行 */
export type QuoteRow = {
  id: string;
  name: string;
  unit: string;
  quantity: string;
  unitPrice: string;
  amount: string;
  remark: string;
};

/** 授权与承诺正文块 */
export type CommitBlock = {
  id: string;
  title: string;
  body: string;
  /** 是否需盖章/签字 */
  needsStamp: boolean;
};

/** 商务标项目（可与技术标同属一投标项目，前端先独立 mock） */
export type BusinessBidProject = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  /** 已完成到第几步（1-6） */
  currentStep: number;
  updatedAt: string;
  /** 关联技术标项目 id（可选） */
  linkedTechnicalProjectId?: string;
};

export type BusinessBidWorkspaceState = {
  projectId: string;
  parseMarkdown: string;
  qualifyItems: QualifyItem[];
  tocItems: TocItem[];
  quoteRows: QuoteRow[];
  quoteNotes: string;
  commitBlocks: CommitBlock[];
};
