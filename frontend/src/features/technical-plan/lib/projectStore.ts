/**
 * 模块：技术方案项目存储（前端数据门面）
 * 用途：list/get/create 只认服务端 /api/projects*；真实 200 [] 保持空；失败显式抛出或返回空，零 mock/localStorage 回退。
 * 对接：GET|POST /api/projects；GET|PATCH /api/projects/{id}；POST /projects/{id}/files 经 uploadProjectFileAsync 薄门面。
 * 二次开发：禁止恢复 biaoshu.projects.v1、mock 合并、VITE_USE_API_PROJECTS / VITE_MERGE_MOCK_PROJECTS 真值开关；
 *       不得读取/写入/删除/迁移旧项目键或 pending 文件名键；创建失败不得生成 proj_* 本地 ID 或导航假工作区；
 *       禁止在 CreatePage 散落 FormData/API 基址，上传一律走本门面。
 */

import { apiFetch, apiUploadFile } from "../../../shared/lib/api";
import type { Project, ProjectStatus } from "../../../shared/types/workspace";

export type CreateProjectInput = {
  name: string;
  industry?: string;
  featureId?: string;
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
 * 不接收 fileNames，不写 sessionStorage pending。
 */
export async function createProjectAsync(
  input: CreateProjectInput,
): Promise<Project> {
  const kind = input.kind ?? "technical";
  const defaultName =
    kind === "business" ? "未命名商务标项目" : "未命名技术标项目";
  return apiFetch<Project>("/projects", {
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
}

/**
 * 用途：项目文件 multipart 上传薄门面；内部复用 apiUploadFile，编码 projectId。
 * 对接：POST /projects/{id}/files，字段名固定 file。
 * 调用方不得自建 FormData 或拼接未编码路径。
 */
export async function uploadProjectFileAsync<T = unknown>(
  projectId: string,
  file: File,
): Promise<T> {
  return apiUploadFile<T>(
    `/projects/${encodeURIComponent(projectId)}/files`,
    file,
  );
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
 * 用途：按创建能力推断默认行业文案（纯映射，不读写存储）。
 */
export function industryFromFeature(featureId: string): string {
  if (featureId === "engineering") return "工程建设";
  if (featureId === "yibiaoxiebiao") return "以标写标";
  if (featureId === "full-bid") return "完整投标";
  return "智慧城市";
}
