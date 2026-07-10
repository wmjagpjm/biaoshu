/**
 * 工作空间与项目领域类型
 * 用途：前后端共享概念模型的前端定义；一账号一 workspace（个人版）。
 */

export type Workspace = {
  id: string;
  name: string;
  /** 个人版与 userId 1:1 */
  ownerUserId: string;
};

export type ProjectStatus =
  | "draft"
  | "analyzing"
  | "writing"
  | "reviewing"
  | "exported";

/** technical=技术标；business=商务标 */
export type ProjectKind = "technical" | "business";

export type Project = {
  id: string;
  workspaceId: string;
  name: string;
  industry: string;
  status: ProjectStatus;
  updatedAt: string;
  /** 已完成的技术方案/商务标步骤 1-6 */
  technicalPlanStep: number;
  wordCount: number;
  /** 缺省 technical（旧数据兼容） */
  kind?: ProjectKind;
  /** 关联另一册项目（如商务关联技术标） */
  linkedProjectId?: string | null;
};

export type TaskStatus =
  | "pending"
  | "running"
  | "paused"
  | "success"
  | "failed";

export type BackgroundTask = {
  id: string;
  projectId: string;
  type: string;
  status: TaskStatus;
  progress: number;
  message: string;
};
