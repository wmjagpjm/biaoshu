/**
 * 模块：技术标中标内容模板类型
 * 用途：对齐后端列表摘要 / 详情 BidTemplateOut / 沉淀与从模板立项请求体；与导出版式模板类型隔离。
 * 对接：/api/templates；useBidTemplates；BidTemplatesPage。
 * 二次开发：禁止与 export-format 的 ExportFormatConfig 混用字段语义；列表类型不得回填完整 snapshot。
 */

export type BidTemplateStatus = "active" | "archived";
export type BidTemplateKind = "technical";

/** 用途：模板快照中的大纲/章节最小形状（与 editor-state 兼容）。 */
export type BidTemplateSnapshot = {
  outline: unknown;
  chapters: unknown;
  mode?: string;
  facts?: unknown;
  guidance?: unknown;
};

/** 用途：模板公共元数据字段。 */
type BidTemplateMeta = {
  id: string;
  workspaceId: string;
  title: string;
  tags: string[];
  status: BidTemplateStatus;
  kind: BidTemplateKind;
  sourceProjectId: string | null;
  sourceProjectName: string;
  createdAt: string;
  updatedAt: string;
};

/**
 * 用途：列表摘要读模型（GET /api/templates）。
 * 规则：仅元数据 + chapterCount/outlineTitles，不含完整 snapshot。
 */
export type BidTemplateSummary = BidTemplateMeta & {
  chapterCount: number;
  outlineTitles: string[];
};

/**
 * 用途：详情/沉淀响应读模型（GET /api/templates/{id}、POST from-project）。
 * 规则：含完整 snapshot；列表不得缓存此对象。
 */
export type BidTemplate = BidTemplateMeta & {
  snapshot: BidTemplateSnapshot;
};

/** 用途：从项目沉淀模板的表单草稿。 */
export type SaveAsTemplateDraft = {
  title: string;
  tagsText: string;
};
