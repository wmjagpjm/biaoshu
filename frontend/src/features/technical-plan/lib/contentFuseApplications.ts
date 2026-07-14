/**
 * 模块：融合写入持久恢复批次 API（阶段3 M3-D）
 * 用途：技术标 content_fuse 建议的服务端原子确认、最近批次列表与一次性恢复。
 * 对接：ContentFuseDialog；后端 /projects/{id}/content-fuse-applications*；apiFetch。
 * 二次开发：请求体仅 taskId/suggestionIds；禁止缓存/轮询/存储；不请求历史正文或来源详情；
 *       错误不得回显服务端 detail/code/路径/ID。
 */

import { apiFetch } from "../../../shared/lib/api";

/** 用途：原子确认请求体；键集精确仅此两项。 */
export type ContentFuseApplicationCreateBody = {
  taskId: string;
  suggestionIds: string[];
};

/** 用途：原子确认成功响应（201）。 */
export type ContentFuseApplicationCreateResult = {
  batchId: string;
  appliedChapterCount: number;
  createdAt: string;
};

/** 用途：列表项最小投影；前端不得展示 batchId；state 仅 active|consumed。 */
export type ContentFuseApplicationListItem = {
  batchId: string;
  chapterCount: number;
  state: "active" | "consumed";
  createdAt: string;
  consumedAt: string | null;
};

/** 用途：列表响应顶层。 */
export type ContentFuseApplicationListResult = {
  items: ContentFuseApplicationListItem[];
};

/** 用途：一次消费恢复成功响应。 */
export type ContentFuseApplicationConsumeResult = {
  restoredChapterCount: number;
  skippedChapterCount: number;
  consumedAt: string;
};

/**
 * 用途：服务端原子确认所选融合建议。
 * 对接：POST /projects/{projectId}/content-fuse-applications
 */
export async function createContentFuseApplication(
  projectId: string,
  body: ContentFuseApplicationCreateBody,
): Promise<ContentFuseApplicationCreateResult> {
  return apiFetch<ContentFuseApplicationCreateResult>(
    `/projects/${encodeURIComponent(projectId)}/content-fuse-applications`,
    {
      method: "POST",
      body: JSON.stringify({
        taskId: body.taskId,
        suggestionIds: body.suggestionIds,
      }),
    },
  );
}

/**
 * 用途：读取当前项目最近恢复批次（服务端固定最多 20）。
 * 对接：GET /projects/{projectId}/content-fuse-applications
 */
export async function listContentFuseApplications(
  projectId: string,
): Promise<ContentFuseApplicationListResult> {
  return apiFetch<ContentFuseApplicationListResult>(
    `/projects/${encodeURIComponent(projectId)}/content-fuse-applications`,
  );
}

/**
 * 用途：对 active 批次执行一次漂移安全恢复并消费。
 * 对接：POST /projects/{projectId}/content-fuse-applications/{batchId}/consume
 */
export async function consumeContentFuseApplication(
  projectId: string,
  batchId: string,
): Promise<ContentFuseApplicationConsumeResult> {
  return apiFetch<ContentFuseApplicationConsumeResult>(
    `/projects/${encodeURIComponent(projectId)}/content-fuse-applications/${encodeURIComponent(batchId)}/consume`,
    { method: "POST" },
  );
}
