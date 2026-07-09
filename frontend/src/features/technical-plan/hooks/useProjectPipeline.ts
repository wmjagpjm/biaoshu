/**
 * 模块：技术标本机日用流水线（上传 / 任务）
 * 用途：对接 files + tasks + export 下载，供工作区各步按钮调用。
 * 对接：
 *   - POST /projects/{id}/files
 *   - POST /projects/{id}/tasks  type=parse|analyze|outline|chapter|export
 *   - GET  /projects/{id}/export/download/{stored}
 * 二次开发：长任务可改为轮询 GET tasks/{id}，接口形状已兼容。
 */

import { useCallback, useState } from "react";
import { apiFetch, apiUploadFile, getApiBase } from "../../../shared/lib/api";

export type PipelineTask = {
  id: string;
  projectId: string;
  type: string;
  status: "pending" | "running" | "success" | "failed" | string;
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

export function useProjectPipeline(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [lastTask, setLastTask] = useState<PipelineTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<ProjectFileInfo[]>([]);

  const refreshFiles = useCallback(async () => {
    if (!projectId) return;
    try {
      const list = await apiFetch<ProjectFileInfo[]>(
        `/projects/${encodeURIComponent(projectId)}/files`,
      );
      setFiles(Array.isArray(list) ? list : []);
    } catch {
      /* 忽略列表失败 */
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

  const runTask = useCallback(
    async (
      type: "parse" | "analyze" | "outline" | "chapter" | "export",
      payload?: Record<string, unknown>,
    ) => {
      setBusy(true);
      setError(null);
      try {
        const task = await apiFetch<PipelineTask>(
          `/projects/${encodeURIComponent(projectId)}/tasks`,
          {
            method: "POST",
            body: JSON.stringify({ type, payload }),
          },
        );
        setLastTask(task);
        if (task.status === "failed") {
          const msg = task.error || task.message || "任务失败";
          setError(msg);
        }
        return task;
      } catch (err) {
        const msg = (err as { message?: string })?.message || "任务请求失败";
        setError(msg);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  /** 用途：根据 export 任务结果拼下载 URL 并打开。 */
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

  return {
    busy,
    lastTask,
    error,
    files,
    setError,
    refreshFiles,
    uploadFile,
    runTask,
    downloadExport,
  };
}
