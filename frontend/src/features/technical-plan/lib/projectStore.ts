/**
 * 模块：技术方案项目存储（前端数据门面）
 * 用途：list/get/create 优先后端；可配置是否合并演示 mock；失败回退本地。
 * 对接：GET|POST /api/projects；VITE_USE_API_PROJECTS、VITE_MERGE_MOCK_PROJECTS
 * 二次开发：联调时建议 MERGE_MOCK=false，避免与真实列表混淆。
 */

import { apiFetch } from "../../../shared/lib/api";
import {
  currentWorkspace,
  mockProjects,
} from "../../../shared/mock/projects";
import type { Project, ProjectStatus } from "../../../shared/types/workspace";

const STORAGE_KEY = "biaoshu.projects.v1";
const PENDING_FILES_KEY = "biaoshu.pendingProjectFiles";

export type CreateProjectInput = {
  name: string;
  industry?: string;
  featureId?: string;
  fileNames?: string[];
  technicalPlanStep?: number;
  status?: ProjectStatus;
};

/** 列表加载结果：带来源与离线提示，便于联调观测 */
export type ListProjectsResult = {
  projects: Project[];
  /** api=后端成功；local=回退 localStorage/mock */
  source: "api" | "local";
  offlineHint?: string;
};

function useApiProjects(): boolean {
  const flag = import.meta.env.VITE_USE_API_PROJECTS;
  if (flag === "false" || flag === "0") return false;
  return true;
}

/** 默认 true 保演示；联调可设 VITE_MERGE_MOCK_PROJECTS=false */
function mergeMockProjects(): boolean {
  const flag = import.meta.env.VITE_MERGE_MOCK_PROJECTS;
  if (flag === "false" || flag === "0") return false;
  return true;
}

function loadUserProjects(): Project[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw) as Project[];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function saveUserProjects(list: Project[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

function rememberPendingFiles(projectId: string, fileNames?: string[]) {
  if (!fileNames?.length) return;
  try {
    sessionStorage.setItem(
      PENDING_FILES_KEY,
      JSON.stringify({ projectId, fileNames }),
    );
  } catch {
    /* ignore */
  }
}

function mergeWithMock(userOrApi: Project[]): Project[] {
  if (!mergeMockProjects()) {
    return [...userOrApi].sort(
      (a, b) =>
        new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
  }
  const ids = new Set(userOrApi.map((p) => p.id));
  const base = mockProjects.filter((p) => !ids.has(p.id));
  return [...userOrApi, ...base].sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

export function listProjects(): Project[] {
  return mergeWithMock(loadUserProjects());
}

/**
 * 用途：异步列表；API 成功 source=api；失败带 offlineHint。
 */
export async function listProjectsAsync(): Promise<ListProjectsResult> {
  if (useApiProjects()) {
    try {
      const remote = await apiFetch<Project[]>("/projects");
      if (Array.isArray(remote)) {
        return { projects: mergeWithMock(remote), source: "api" };
      }
    } catch (err) {
      const message =
        (err as { message?: string })?.message || "后端不可用";
      return {
        projects: listProjects(),
        source: "local",
        offlineHint: `后端不可用，已显示本地/演示数据：${message}`,
      };
    }
  }
  return { projects: listProjects(), source: "local" };
}

export function getProject(id: string): Project | undefined {
  return listProjects().find((p) => p.id === id);
}

export async function getProjectAsync(
  id: string,
): Promise<Project | undefined> {
  if (useApiProjects()) {
    try {
      return await apiFetch<Project>(`/projects/${encodeURIComponent(id)}`);
    } catch {
      /* 本地兜底 */
    }
  }
  return getProject(id);
}

export function createProjectLocal(input: CreateProjectInput): Project {
  const id = `proj_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const project: Project = {
    id,
    workspaceId: currentWorkspace.id,
    name: input.name.trim() || "未命名技术标项目",
    industry: input.industry?.trim() || "通用",
    status: input.status ?? "draft",
    updatedAt: new Date().toISOString(),
    technicalPlanStep: input.technicalPlanStep ?? 1,
    wordCount: 0,
  };
  saveUserProjects([project, ...loadUserProjects()]);
  rememberPendingFiles(id, input.fileNames);
  return project;
}

/** @deprecated 请用 createProjectAsync */
export function createProject(input: CreateProjectInput): Project {
  return createProjectLocal(input);
}

export async function createProjectAsync(
  input: CreateProjectInput,
): Promise<Project> {
  if (useApiProjects()) {
    try {
      const project = await apiFetch<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: input.name.trim() || "未命名技术标项目",
          industry: input.industry?.trim() || "通用",
          status: input.status ?? "draft",
          technicalPlanStep: input.technicalPlanStep ?? 1,
        }),
      });
      rememberPendingFiles(project.id, input.fileNames);
      return project;
    } catch {
      /* 回退 */
    }
  }
  return createProjectLocal(input);
}

export async function updateProjectAsync(
  id: string,
  patch: Partial<
    Pick<
      Project,
      "name" | "industry" | "status" | "technicalPlanStep" | "wordCount"
    >
  >,
): Promise<Project | null> {
  if (!useApiProjects()) return null;
  try {
    return await apiFetch<Project>(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  } catch {
    return null;
  }
}

export function getPendingFileNames(projectId: string): string[] {
  try {
    const raw = sessionStorage.getItem(PENDING_FILES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as {
      projectId?: string;
      fileNames?: string[];
    };
    if (parsed.projectId !== projectId) return [];
    return parsed.fileNames ?? [];
  } catch {
    return [];
  }
}

export function industryFromFeature(featureId: string): string {
  if (featureId === "engineering") return "工程建设";
  if (featureId === "yibiaoxiebiao") return "以标写标";
  if (featureId === "full-bid") return "完整投标";
  return "智慧城市";
}
