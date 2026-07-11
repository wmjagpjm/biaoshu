/**
 * 模块：资源中心类型
 * 用途：定义系统精选与工作空间自建资源的 API 读模型、色调枚举和编辑草稿。
 * 对接：/api/resources；useResources；ResourcesPage。
 * 二次开发：外部同步若引入来源状态，须由后端扩展读模型；禁止前端伪造 source 或 workspaceId。
 */

export type ResourceTone = "blue" | "violet" | "cyan" | "slate";
export type ResourceSource = "system" | "user";

export type ResourceItem = {
  id: string;
  workspaceId: string | null;
  source: ResourceSource;
  title: string;
  description: string;
  category: string;
  tags: string[];
  bodyMarkdown: string;
  tone: ResourceTone;
  viewCount: number;
  createdAt: string;
  updatedAt: string;
};

export type ResourceDraft = {
  title: string;
  description: string;
  category: string;
  tagsText: string;
  bodyMarkdown: string;
  tone: ResourceTone;
};
