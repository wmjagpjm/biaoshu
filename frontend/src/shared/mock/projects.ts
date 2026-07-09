import type { BackgroundTask, Project, Workspace } from "../types/workspace";

/** 当前登录工作空间（个人版 mock） */
export const currentWorkspace: Workspace = {
  id: "ws_demo",
  name: "我的工作空间",
  ownerUserId: "user_demo",
};

export const mockProjects: Project[] = [
  {
    id: "proj_01",
    workspaceId: "ws_demo",
    name: "某市智慧交通综合管理平台技术标",
    industry: "智慧城市",
    status: "writing",
    updatedAt: "2026-07-09T09:20:00+08:00",
    technicalPlanStep: 5,
    wordCount: 42680,
  },
  {
    id: "proj_02",
    workspaceId: "ws_demo",
    name: "园区能耗监测系统采购项目",
    industry: "能源环保",
    status: "analyzing",
    updatedAt: "2026-07-08T16:40:00+08:00",
    technicalPlanStep: 2,
    wordCount: 0,
  },
  {
    id: "proj_03",
    workspaceId: "ws_demo",
    name: "医院信息集成平台改造",
    industry: "医疗信息化",
    status: "exported",
    updatedAt: "2026-07-05T11:10:00+08:00",
    technicalPlanStep: 6,
    wordCount: 98620,
  },
  {
    id: "proj_04",
    workspaceId: "ws_demo",
    name: "新建数据中心基础设施运维服务",
    industry: "IDC / 运维",
    status: "draft",
    updatedAt: "2026-07-04T14:00:00+08:00",
    technicalPlanStep: 1,
    wordCount: 0,
  },
];

export const mockTasks: BackgroundTask[] = [
  {
    id: "task_01",
    projectId: "proj_01",
    type: "content-generation",
    status: "running",
    progress: 62,
    message: "正在生成第 8/13 章：实施方案与进度计划",
  },
];

export function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return "刚刚";
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}
