/**
 * 模块：技术方案项目存储（前端数据门面）
 * 用途：list/get/create 只认服务端 /api/projects*；真实 200 [] 保持空；失败显式抛出或返回空，零 mock/localStorage 回退。
 * 对接：GET|POST /api/projects；GET|PATCH /api/projects/{id}；sessionStorage 仅作待上传文件名交接。
 * 二次开发：禁止恢复 biaoshu.projects.v1、mock 合并、VITE_USE_API_PROJECTS / VITE_MERGE_MOCK_PROJECTS 真值开关；
 *       不得读取/写入/删除/迁移旧项目键；创建失败不得生成 proj_* 本地 ID 或导航假工作区。
 */

import { apiFetch } from "../../../shared/lib/api";
import type { Project, ProjectStatus } from "../../../shared/types/workspace";

/** 创建成功后待上传文件名交接键（仅 sessionStorage，projectId 必须为真实 POST 返回值） */
const PENDING_FILES_KEY = "biaoshu.pendingProjectFiles";

export type CreateProjectInput = {
  name: string;
  industry?: string;
  featureId?: string;
  fileNames?: string[];
  technicalPlanStep?: number;
  status?: ProjectStatus;
  kind?: "technical" | "business";
  linkedProjectId?: string | null;
};

/** 列表加载结果：仅服务端真值，source 固定为 api */
export type ListProjectsResult = {
  projects: Project[];
  source: "api";
};

/**
 * 用途：按更新时间倒序排序（纯函数，不改入参）。
 */
function sortByUpdatedAt(projects: Project[]): Project[] {
  return [...projects].sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

/**
 * 用途：创建成功后把待上传文件名写入 sessionStorage；仅绑定真实 projectId。
 * 失败路径不得调用本函数。
 */
function rememberPendingFiles(projectId: string, fileNames?: string[]) {
  if (!fileNames?.length) return;
  try {
    sessionStorage.setItem(
      PENDING_FILES_KEY,
      JSON.stringify({ projectId, fileNames }),
    );
  } catch {
    /* 配额等忽略；不得回退到 localStorage 项目键 */
  }
}

/**
 * 用途：异步列表，只请求真实 API；200 [] 返回空数组；失败抛出，不得返回旧数据或 mock。
 * @param kind 可选 technical|business，传给 GET /projects?kind=
 */
export async function listProjectsAsync(options?: {
  kind?: "technical" | "business";
}): Promise<ListProjectsResult> {
  const kind = options?.kind;
  const q = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  const remote = await apiFetch<Project[]>(`/projects${q}`);
  if (!Array.isArray(remote)) {
    throw new Error("项目列表响应无效");
  }
  // 显式 technical 时过滤非技术标，避免历史数据 kind 混杂；business 原样使用服务端结果
  const filtered =
    kind === "technical"
      ? remote.filter((p) => !p.kind || p.kind === "technical")
      : remote;
  return { projects: sortByUpdatedAt(filtered), source: "api" };
}

/**
 * 用途：只 GET 项目详情；失败或 404 返回 undefined，不得回退 mock/localStorage。
 * 技术标工作区对 undefined 会 Navigate 回真实列表。
 */
export async function getProjectAsync(
  id: string,
): Promise<Project | undefined> {
  try {
    return await apiFetch<Project>(`/projects/${encodeURIComponent(id)}`);
  } catch {
    return undefined;
  }
}

/**
 * 用途：每次显式提交只 POST 一次；成功返回服务端项目；失败直接抛出，不生成本地 ID。
 * 待上传文件名仅在 POST 成功后写入 sessionStorage。
 */
export async function createProjectAsync(
  input: CreateProjectInput,
): Promise<Project> {
  const kind = input.kind ?? "technical";
  const defaultName =
    kind === "business" ? "未命名商务标项目" : "未命名技术标项目";
  const project = await apiFetch<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({
      name: input.name.trim() || defaultName,
      industry: input.industry?.trim() || "通用",
      status: input.status ?? "draft",
      technicalPlanStep: input.technicalPlanStep ?? 1,
      kind,
      linkedProjectId: input.linkedProjectId ?? undefined,
    }),
  });
  rememberPendingFiles(project.id, input.fileNames);
  return project;
}

/**
 * 用途：PATCH 更新项目元数据；失败返回 null，不抛出业务细节。
 */
export async function updateProjectAsync(
  id: string,
  patch: Partial<
    Pick<
      Project,
      "name" | "industry" | "status" | "technicalPlanStep" | "wordCount"
    >
  >,
): Promise<Project | null> {
  try {
    return await apiFetch<Project>(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  } catch {
    return null;
  }
}

/**
 * 用途：读取创建成功后写入的待上传文件名；projectId 不匹配则返回空。
 */
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

/**
 * 用途：按创建能力推断默认行业文案（纯映射，不读写存储）。
 */
export function industryFromFeature(featureId: string): string {
  if (featureId === "engineering") return "工程建设";
  if (featureId === "yibiaoxiebiao") return "以标写标";
  if (featureId === "full-bid") return "完整投标";
  return "智慧城市";
}
