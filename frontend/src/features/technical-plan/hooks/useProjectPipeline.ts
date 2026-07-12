/**
 * 模块：技术标与商务标共用任务流水线
 * 用途：上传文件、创建异步任务、优先订阅 SSE 进度并在断线时回退查询；支持协作式取消。
 * 对接：
 *   - POST /projects/{id}/tasks（默认异步）
 *   - GET  /projects/{id}/tasks/{taskId}/events（SSE）
 *   - GET  /projects/{id}/tasks/{taskId}（SSE 回退）
 *   - POST /projects/{id}/tasks/{taskId}/cancel
 *   - POST /projects/{id}/files
 *   - POST /projects/{id}/images
 * 二次开发：默认工作空间使用原生 EventSource；多工作空间鉴权或事件游标需独立设计。
 */

import { useCallback, useEffect, useRef, useState } from "react";
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

export type ProjectImageInfo = ProjectFileInfo;

export type TaskType =
  | "parse"
  | "analyze"
  | "outline"
  | "chapter"
  | "chapters"
  | "export"
  | "response_match"
  | "content_fuse"
  | "biz_qualify"
  | "biz_toc"
  | "biz_quote"
  | "biz_commit";

const FALLBACK_POLL_MS = 2000;
const TASK_MAX_MS = 10 * 60 * 1000;
const SSE_IDLE_MAX_MS = 45 * 1000;

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function isActiveTask(task: PipelineTask) {
  return task.status === "pending" || task.status === "running";
}

function isTaskStateRegression(
  current: PipelineTask | null,
  next: PipelineTask,
) {
  return (
    current?.id === next.id &&
    !isActiveTask(current) &&
    isActiveTask(next)
  );
}

class TaskStreamUnavailableError extends Error {
  constructor(message = "SSE 任务状态流不可用") {
    super(message);
    this.name = "TaskStreamUnavailableError";
  }
}

class TaskStreamClosedError extends Error {
  constructor() {
    super("任务状态流已关闭");
    this.name = "TaskStreamClosedError";
  }
}

class TaskTimeoutError extends Error {
  constructor() {
    super("任务超时（超过 10 分钟），请查看后端日志后重试");
    this.name = "TaskTimeoutError";
  }
}

type TaskStreamController = {
  close: () => void;
};

function waitForTaskEvents(
  eventPath: string,
  deadline: number,
  onTask: (task: PipelineTask) => void,
  onController: (controller: TaskStreamController | null) => void,
) {
  return new Promise<PipelineTask>((resolve, reject) => {
    let source: EventSource | null = null;
    let settled = false;
    let timeoutId: number | null = null;
    let idleTimeoutId: number | null = null;

    const cleanup = () => {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      if (idleTimeoutId !== null) window.clearTimeout(idleTimeoutId);
      source?.close();
      onController(null);
    };

    const rejectOnce = (error: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };

    const resolveOnce = (task: PipelineTask) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(task);
    };

    const resetIdleTimeout = () => {
      if (idleTimeoutId !== null) window.clearTimeout(idleTimeoutId);
      idleTimeoutId = window.setTimeout(() => {
        rejectOnce(new TaskStreamUnavailableError("SSE 心跳超时"));
      }, SSE_IDLE_MAX_MS);
    };

    const consumeTaskEvent = (event: Event) => {
      resetIdleTimeout();
      try {
        const task = JSON.parse((event as MessageEvent<string>).data) as PipelineTask;
        onTask(task);
        if (!isActiveTask(task)) resolveOnce(task);
      } catch {
        rejectOnce(new TaskStreamUnavailableError("SSE 任务数据格式异常"));
      }
    };

    try {
      source = new EventSource(`${getApiBase()}${eventPath}`);
    } catch {
      rejectOnce(new TaskStreamUnavailableError());
      return;
    }

    onController({
      close: () => rejectOnce(new TaskStreamClosedError()),
    });
    source.addEventListener("snapshot", consumeTaskEvent);
    source.addEventListener("task", consumeTaskEvent);
    source.addEventListener("heartbeat", resetIdleTimeout);
    source.addEventListener("error", () => {
      rejectOnce(new TaskStreamUnavailableError());
    });
    source.onerror = () => {
      rejectOnce(new TaskStreamUnavailableError());
    };

    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      rejectOnce(new TaskTimeoutError());
      return;
    }
    timeoutId = window.setTimeout(
      () => rejectOnce(new TaskTimeoutError()),
      remaining,
    );
    resetIdleTimeout();
  });
}

async function pollTaskUntilFinished(
  task: PipelineTask,
  taskPath: string,
  deadline: number,
  onTask: (next: PipelineTask) => void,
) {
  let current = task;
  while (isActiveTask(current)) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new TaskTimeoutError();
    await sleep(Math.min(FALLBACK_POLL_MS, remaining));
    current = await apiFetch<PipelineTask>(taskPath);
    onTask(current);
  }
  return current;
}

/**
 * 模块：技术标与商务标的项目任务流水线 Hook。
 * 用途：向页面提供上传、任务 SSE、回退查询和协作式取消，并隔离项目切换时的旧任务状态。
 * 对接：TechnicalPlanWorkspace、BusinessBidWorkspace、项目任务 API。
 * 二次开发：多任务并行或跨项目保活前，需把单控制器重构为按任务 id 管理的连接表。
 */
export function useProjectPipeline(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [lastTask, setLastTask] = useState<PipelineTask | null>(null);
  const [recentTasks, setRecentTasks] = useState<PipelineTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [files, setFiles] = useState<ProjectFileInfo[]>([]);
  const taskStreamRef = useRef<TaskStreamController | null>(null);
  const lastTaskRef = useRef<PipelineTask | null>(null);
  const projectSessionRef = useRef(0);
  const taskRunRef = useRef(0);
  const activeProjectIdRef = useRef(projectId);
  activeProjectIdRef.current = projectId;

  useEffect(() => {
    const session = ++projectSessionRef.current;
    lastTaskRef.current = null;
    setLastTask(null);
    setBusy(false);
    setError(null);
    return () => {
      // 先作废旧回调，再关闭流，避免旧项目的异步收尾覆盖新项目状态。
      taskRunRef.current += 1;
      if (projectSessionRef.current === session) {
        projectSessionRef.current += 1;
      }
      taskStreamRef.current?.close();
      taskStreamRef.current = null;
    };
  }, [projectId]);

  const refreshFiles = useCallback(async () => {
    if (!projectId) return;
    const requestedProjectId = projectId;
    try {
      const list = await apiFetch<ProjectFileInfo[]>(
        `/projects/${encodeURIComponent(requestedProjectId)}/files`,
      );
      if (activeProjectIdRef.current === requestedProjectId) {
        setFiles(Array.isArray(list) ? list : []);
      }
    } catch {
      /* ignore */
    }
  }, [projectId]);

  const refreshTasks = useCallback(async () => {
    if (!projectId) return;
    const requestedProjectId = projectId;
    try {
      const list = await apiFetch<PipelineTask[]>(
        `/projects/${encodeURIComponent(requestedProjectId)}/tasks`,
      );
      if (activeProjectIdRef.current === requestedProjectId) {
        setRecentTasks(Array.isArray(list) ? list.slice(0, 8) : []);
      }
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

  const uploadImage = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        return await apiUploadFile<ProjectImageInfo>(
          `/projects/${encodeURIComponent(projectId)}/images`,
          file,
        );
      } catch (err) {
        const msg = (err as { message?: string })?.message || "图片上传失败";
        setError(msg);
        throw err;
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  /**
   * 用途：创建异步任务，优先订阅 SSE；流不可用时回退 GET 查询直到结束。
   * 对接：POST /tasks；GET /tasks/{taskId}/events；GET /tasks/{taskId}。
   */
  const runTask = useCallback(
    async (type: TaskType, payload?: Record<string, unknown>) => {
      const runId = taskRunRef.current + 1;
      const session = projectSessionRef.current;
      const runProjectId = projectId;
      taskRunRef.current = runId;
      const isCurrentRun = () =>
        taskRunRef.current === runId &&
        projectSessionRef.current === session &&
        activeProjectIdRef.current === runProjectId;
      const publishTask = (next: PipelineTask) => {
        if (!isCurrentRun()) return false;
        // 取消或完成已在本地确认时，忽略管道中滞后的 pending/running 帧。
        if (isTaskStateRegression(lastTaskRef.current, next)) return false;
        lastTaskRef.current = next;
        setLastTask({ ...next });
        return true;
      };

      setBusy(true);
      setError(null);
      try {
        let task = await apiFetch<PipelineTask>(
          `/projects/${encodeURIComponent(projectId)}/tasks`,
          {
            method: "POST",
            body: JSON.stringify({ type, payload }),
          },
        );
        if (!isCurrentRun()) return task;
        publishTask(task);

        if (isActiveTask(task)) {
          const taskPath = `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(task.id)}`;
          const deadline = Date.now() + TASK_MAX_MS;
          const updateTask = (next: PipelineTask) => {
            if (!isCurrentRun()) return;
            if (isTaskStateRegression(lastTaskRef.current, next)) return;
            task = next;
            publishTask(next);
          };
          let streamController: TaskStreamController | null = null;
          try {
            task = await waitForTaskEvents(
              `${taskPath}/events`,
              deadline,
              updateTask,
              (controller) => {
                const previousController = streamController;
                streamController = controller;
                if (controller) {
                  taskStreamRef.current = controller;
                } else if (taskStreamRef.current === previousController) {
                  taskStreamRef.current = null;
                }
              },
            );
          } catch (streamError) {
            if (!isCurrentRun()) return task;
            if (
              streamError instanceof TaskTimeoutError ||
              streamError instanceof TaskStreamClosedError
            ) {
              throw streamError;
            }
            task = await apiFetch<PipelineTask>(taskPath);
            updateTask(task);
            if (isActiveTask(task)) {
              task = await pollTaskUntilFinished(
                task,
                taskPath,
                deadline,
                updateTask,
              );
            }
          }
        }

        if (!isCurrentRun()) return task;

        if (task.status === "failed") {
          const msg = task.error || task.message || "任务失败";
          setError(msg);
        } else if (task.status === "cancelled") {
          setError(null);
        }
        await refreshTasks();
        return task;
      } catch (err) {
        if (!isCurrentRun()) throw err;
        const msg = (err as { message?: string })?.message || "任务请求失败";
        setError(msg);
        throw err;
      } finally {
        if (isCurrentRun()) setBusy(false);
      }
    },
    [projectId, refreshTasks],
  );

  /**
    * 用途：取消当前进行中任务（协作式，章间/步骤间生效）。
    * 对接：POST .../tasks/{id}/cancel
    */
  const cancelTask = useCallback(async () => {
    const currentTask = lastTaskRef.current;
    const tid = currentTask?.id;
    if (!tid || !projectId) return null;
    if (
      currentTask.status !== "pending" &&
      currentTask.status !== "running"
    ) {
      return currentTask;
    }
    try {
      const task = await apiFetch<PipelineTask>(
        `/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(tid)}/cancel`,
        { method: "POST" },
      );
      if (
        activeProjectIdRef.current === projectId &&
        lastTaskRef.current?.id === tid
      ) {
        lastTaskRef.current = task;
        setLastTask({ ...task });
        setError(null);
      }
      await refreshTasks();
      return task;
    } catch (err) {
      const msg = (err as { message?: string })?.message || "取消失败";
      if (
        activeProjectIdRef.current === projectId &&
        lastTaskRef.current?.id === tid
      ) {
        setError(msg);
      }
      throw err;
    }
  }, [projectId, refreshTasks]);

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
    uploadImage,
    runTask,
    cancelTask,
    downloadExport,
  };
}
