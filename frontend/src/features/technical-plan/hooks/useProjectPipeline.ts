/**
 * 模块：技术标本机日用流水线（上传 / 异步任务轮询 / 取消）
 * 用途：POST 创建任务后轮询进度，直到 success/failed/cancelled；可主动取消。
 * 对接：
 *   - POST /projects/{id}/tasks（默认异步）
 *   - GET  /projects/{id}/tasks/{taskId}
 *   - POST /projects/{id}/tasks/{taskId}/cancel
 *   - POST /projects/{id}/files
 * 二次开发：可改为 SSE；超时与间隔可配置。
 */

import { useCallback, useRef, useState } from "react";
import { apiFetch, apiUploadFile, getApiBase } from "../../../shared/lib/api";

export type PipelineTask = {
  id: string;
  projectId: string;
  type: string;
  status: "pending" | "running" | "success" | "failed" | "cancelled" | string;
  progress: number;
  message: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

export type ProjectFileInfo = {
  id: string;
  filename: string;
  sizeBytes: number;
  createdAt?: string;
};

export type TaskType =
  | "parse"
  | "analyze"
  | "outline"
  | "chapter"
  | "chapters"
  | "export"
  | "biz_qualify"
  | "biz_toc"
  | "biz_quote"
  | "biz_commit";

const POLL_MS = 1000;
const POLL_MAX_MS = 10 * 60 * 1000;

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export function useProjectPipeline(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [lastTask, setLastTask] = useState<PipelineTask | null>(null);
  const [recentTasks, setRecentTasks] = useState<PipelineTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<ProjectFileInfo[]>([]);
  const abortRef = useRef(false);

  const refreshFiles = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await apiFetch<ProjectFileInfo[]>(
        `/projects/${encodeURIComponent(projectId)}/files`,
      );
      setFiles(Array.isArray(list) ? list : []);
    } catch {
      /* ignore */
    }
  }, [projectId]);

  const refreshTasks = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await apiFetch<PipelineTask[]>(
        `/projects/${encodeURIComponent(projectId)}/tasks`,
      );
      setRecentTasks(Array.isArray(list) ? list.slice(0, 8) : []);
    } catch {
      /* ignore */
    }
  }, [projectId]);

  const uploadFile = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const row = await apiUploadFile<ProjectFileInfo>(
          `/projects/${encodeURIComponent(projectId)}/files`,
          file,
        );
        await refreshFiles();
        return row;
      } catch (err) {
        const msg = (err as { message?: string })?.message || "上传失败";
        setError(msg);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [projectId, refreshFiles],
  );

  /**
   * 用途：创建异步任务并轮询直到结束。
   */
  const runTask = useCallback(
    async (type: TaskType, payload?: Record<string, unknown>) => {
      setBusy(true);
      setError(null);
      abortRef.current = false;
      try {
        let task = await apiFetch<PipelineTask>(
          `/projects/${encodeURIComponent(projectId)}/tasks`,
          {
            method: "POST",
            body: JSON.stringify({ type, payload }),
          },
        );
        setLastTask(task);

        const started = Date.now();
        while (
          task.status === "pending" ||
          task.status === "running"
        ) {
          if (abortRef.current) {
            // 本地已请求取消，再拉一次最终状态
            try {
              task = await apiFetch<PipelineTask>(
                `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(task.id)}`,
              );
              setLastTask({ ...task });
            } catch {
              /* ignore */
            }
            break;
          }
          if (Date.now() - started > POLL_MAX_MS) {
            throw new Error("任务超时（超过 10 分钟），请查看后端日志后重试");
          }
          await sleep(POLL_MS);
          task = await apiFetch<PipelineTask>(
            `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(task.id)}`,
          );
          setLastTask({ ...task });
        }

        if (task.status === "failed") {
          const msg = task.error || task.message || "任务失败";
          setError(msg);
        } else if (task.status === "cancelled") {
          setError(null);
        }
        await refreshTasks();
        return task;
      } catch (err) {
        const msg = (err as { message?: string })?.message || "任务请求失败";
        setError(msg);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [projectId, refreshTasks],
  );

  /**
   * 用途：取消当前进行中任务（协作式，章间/步骤间生效）。
   * 对接：POST .../tasks/{id}/cancel
   */
  const cancelTask = useCallback(async () => {
    const tid = lastTask?.id;
    if (!tid || !projectId) return null;
    if (
      lastTask &&
      lastTask.status !== "pending" &&
      lastTask.status !== "running"
    ) {
      return lastTask;
    }
    abortRef.current = true;
    try {
      const task = await apiFetch<PipelineTask>(
        `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(tid)}/cancel`,
        { method: "POST" },
      );
      setLastTask(task);
      setError(null);
      await refreshTasks();
      return task;
    } catch (err) {
      const msg = (err as { message?: string })?.message || "取消失败";
      setError(msg);
      throw err;
    }
  }, [lastTask, projectId, refreshTasks]);

  const downloadExport = useCallback(
    (task: PipelineTask) => {
      const stored = task.result?.storedName as string | undefined;
      if (!stored) {
        setError("导出结果中无文件名");
        return;
      }
      const path = `/projects/${encodeURIComponent(projectId)}/export/download/${encodeURIComponent(stored)}`;
      window.open(`${getApiBase()}${path}`, "_blank");
    },
    [projectId],
  );

  const canCancel =
    busy &&
    !!lastTask &&
    (lastTask.status === "pending" || lastTask.status === "running");

  return {
    busy,
    lastTask,
    recentTasks,
    error,
    files,
    canCancel,
    setError,
    refreshFiles,
    refreshTasks,
    uploadFile,
    runTask,
    cancelTask,
    downloadExport,
  };
}
