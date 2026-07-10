/**
 * 人工反馈 — AI 调整：领域类型
 * 用途：跨步骤统一「定向调整」契约，避免只有「手动改 / 整段重生成」。
 * 后端对接建议：
 * - POST /api/projects/{id}/artifacts/{artifactId}/revise
 * - body: { feedback, preserveStructure, mode: 'revise' | 'regenerate' }
 */

/** 流水线中可反馈的产物阶段（技术标 + 商务标） */
export type FeedbackStage =
  | "document_parse"
  | "bid_analysis"
  | "outline"
  | "global_facts"
  | "chapter_content"
  | "export_format"
  | "project_guidance"
  /** 商务标：解析 / 资格 / 目录 / 报价 / 承诺正文 */
  | "business_parse"
  | "business_qualify"
  | "business_toc"
  | "business_quote"
  | "business_commit";

/** 单次反馈记录（将进入下一轮 Prompt 上下文） */
export type AiFeedbackRecord = {
  id: string;
  stage: FeedbackStage;
  /** 用户自然语言修改意见 */
  message: string;
  /** 可选：作用目标（如章节 id、大纲节点 id） */
  targetId?: string;
  targetLabel?: string;
  createdAt: string;
  /** 前端演示状态；后端为 task 状态 */
  status: "queued" | "applying" | "applied" | "failed";
  /** 调整后摘要（演示） */
  resultSummary?: string;
};

/**
 * 项目级生成约束（贯穿后续 AI 任务）
 * 在招标分析后编辑/补充，大纲与正文生成时注入。
 */
export type ProjectGenerationGuidance = {
  /** 目标总字数，如 80000 */
  targetWordCount?: number;
  /** 章节侧重点、必须展开的业务点 */
  chapterFocus?: string;
  /** 特殊格式 / 目录强制要求 */
  formatRequirements?: string;
  /** 其它自由补充要求 */
  extraRequirements?: string;
  /** 用户确认已锁定，可进入下一阶段 */
  lockedForNextStage?: boolean;
  /**
   * 是否启用知识库检索注入（大纲/正文）。
   * 缺省 true；false 时生成不查知识库。
   */
  kbEnabled?: boolean;
  /**
   * 限定检索的知识库文件夹 id 列表。
   * 空/缺省 = 全库；非空 = 仅这些文件夹。
   */
  kbFolderIds?: string[];
  updatedAt?: string;
};

export type ProjectFeedbackState = {
  projectId: string;
  guidance: ProjectGenerationGuidance;
  history: AiFeedbackRecord[];
};

export const FEEDBACK_STAGE_LABEL: Record<FeedbackStage, string> = {
  document_parse: "文档解析",
  bid_analysis: "招标分析",
  outline: "目录大纲",
  global_facts: "全局事实",
  chapter_content: "正文内容",
  export_format: "导出格式",
  project_guidance: "项目生成要求",
  business_parse: "商务标·条款解析",
  business_qualify: "商务标·资格响应",
  business_toc: "商务标·目录清单",
  business_quote: "商务标·报价说明",
  business_commit: "商务标·授权承诺",
};
