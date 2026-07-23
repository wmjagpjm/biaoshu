/**
 * 模块：技术标与商务标共用任务流水线
 * 用途：上传文件、创建异步任务、优先订阅 SSE 进度并在断线时回退查询；支持协作式取消；
 *       P13-I4 对当前 runTask 做单飞安全 status 对账（只改 status/progress）。
 * 对接：
 *   - POST /projects/{id}/tasks（默认异步）
 *   - GET  /projects/{id}/tasks/{taskId}/events（SSE）
 *   - GET  /projects/{id}/tasks/{taskId}（SSE 回退）
 *   - GET  /projects/{id}/tasks/{taskId}/status（I4 安全三键，非详情）
 *   - POST /projects/{id}/tasks/{taskId}/cancel
 *   - POST /projects/{id}/files
 *   - POST /projects/{id}/images
 * 二次开发：禁止用 status 对账触发详情/editor-state/轮询/重试；禁止展示 status 外字段。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  apiFetch,
  apiFetchDocxBlob,
  apiUploadFile,
  getApiBase,
  isSafeDocxDownloadFilename,
} from "../../../shared/lib/api";
import { applySafeStatusProjection } from "./projectTaskStatus";

/** 磁盘/路径定位用 storedName：仅 export_<8hex>.docx */
const STORED_NAME_RE = /^export_[0-9a-f]{8}\.docx$/i;
/** 下载失败固定脱敏文案；禁止拼接 detail/path/storedName */
const DOWNLOAD_FAIL_UI = "下载失败，请重试";
const FALLBACK_DOCX_NAME = "标书.docx";

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
/** I4 安全 status 响应：严格三键，不进 UI 原文 */
type SafeTaskStatusProjection = {
  taskId: string;
  status: PipelineTask["status"];
  progress: number;
};

const STATUS_SET = new Set([
  "pending",
  "running",
  "success",
  "failed",
  "cancelled",
]);

function parseSafeStatusProjection(
  raw: unknown,
  expectedTaskId: string,
): SafeTaskStatusProjection | null {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const obj = raw as Record<string, unknown>;
  if (Object.keys(obj).length !== 3) return null;
  if (typeof obj.taskId !== "string" || obj.taskId !== expectedTaskId) {
    return null;
  }
  if (typeof obj.status !== "string" || !STATUS_SET.has(obj.status)) {
    return null;
  }
  if (
    typeof obj.progress !== "number" ||
    !Number.isInteger(obj.progress) ||
    obj.progress < 0 ||
    obj.progress > 100
  ) {
    return null;
  }
  return {
    taskId: obj.taskId,
    status: obj.status,
    progress: obj.progress,
  };
}

/** 集合 owner：files/recentTasks 与 projectId+session 绑定，切项同步视空。 */
type CollectionOwner = {
  projectId: string;
  session: number;
};

export function useProjectPipeline(projectId: string) {
  const [busy, setBusy] = useState(false);
  const [lastTask, setLastTask] = useState<PipelineTask | null>(null);
  const [recentTasksRaw, setRecentTasksRaw] = useState<PipelineTask[]>([]);
  const [recentTasksOwner, setRecentTasksOwner] = useState<CollectionOwner | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [filesRaw, setFilesRaw] = useState<ProjectFileInfo[]>([]);
  const [filesOwner, setFilesOwner] = useState<CollectionOwner | null>(null);
  const taskStreamRef = useRef<TaskStreamController | null>(null);
  const lastTaskRef = useRef<PipelineTask | null>(null);
  const projectSessionRef = useRef(0);
  const taskRunRef = useRef(0);
  const activeProjectIdRef = useRef(projectId);
  activeProjectIdRef.current = projectId;
  /**
   * A3/A8/A9：渲染同步读当前路由 projectId。
   * owner 不匹配时 files/recentTasks 同步视空，不得等 effect 才清旧 A 集合。
   */
  const files =
    filesOwner != null && filesOwner.projectId === projectId
      ? filesRaw
      : [];
  const recentTasks =
    recentTasksOwner != null && recentTasksOwner.projectId === projectId
      ? recentTasksRaw
      : [];
  /** I4：对账世代；项目切换/卸载/新代次递增，作废迟到响应 */
  const reconcileGenRef = useRef(0);
  /** I4：进行中的 status 请求（同 project+task 单飞；配合 AbortController） */
  const reconcileInflightRef = useRef<{
    gen: number;
    projectId: string;
    taskId: string;
  } | null>(null);
  /** I4：当前 status GET 的 AbortController；切换/卸载时 abort */
  const reconcileAbortRef = useRef<AbortController | null>(null);
  /** V1-F：下载世代；项目切换/卸载/新下载递增，作废旧响应与 click */
  const downloadGenRef = useRef(0);
  /** V1-F：当前下载 GET 的 AbortController；旧 finally 不得清新 controller */
  const downloadAbortRef = useRef<AbortController | null>(null);

  /** 取消在途 status 对账并推进世代（旧 finally 不得清新请求） */
  const abortReconcileInflight = useCallback(() => {
    reconcileGenRef.current += 1;
    const prev = reconcileAbortRef.current;
    reconcileAbortRef.current = null;
    reconcileInflightRef.current = null;
    if (prev) {
      try {
        prev.abort();
      } catch {
        /* ignore */
      }
    }
  }, []);

  /**
   * 用途：项目切换/卸载时作废下载代次与 session 归属。
   * 说明：仅推进 downloadGen，默认不 abort 在途 GET——
   *       abort 不能替代 click 前围栏；挂起 GET 释放 200 后仍须零 click/零 setError。
   *       新一次 downloadExport 启动时才会 abort 旧 controller。
   */
  const invalidateDownloadGeneration = useCallback(() => {
    downloadGenRef.current += 1;
  }, []);

  useEffect(() => {
    const session = ++projectSessionRef.current;
    lastTaskRef.current = null;
    setLastTask(null);
    setBusy(false);
    setError(null);
    // A8：切项同步失效集合 owner（渲染层已按 owner 视空；此处清 raw 防迟到写回）
    setFilesRaw([]);
    setFilesOwner(null);
    setRecentTasksRaw([]);
    setRecentTasksOwner(null);
    // 项目切换：取消旧 status fetch；下载仅作废代次（不 abort 挂起响应）
    abortReconcileInflight();
    invalidateDownloadGeneration();
    return () => {
      // 先作废旧回调，再关闭流，避免旧项目的异步收尾覆盖新项目状态。
      taskRunRef.current += 1;
      if (projectSessionRef.current === session) {
        projectSessionRef.current += 1;
      }
      abortReconcileInflight();
      invalidateDownloadGeneration();
      taskStreamRef.current?.close();
      taskStreamRef.current = null;
    };
  }, [projectId, abortReconcileInflight, invalidateDownloadGeneration]);

  const refreshFiles = useCallback(async () => {
    if (!projectId) return;
    const requestedProjectId = projectId;
    const session = projectSessionRef.current;
    try {
      const list = await apiFetch<ProjectFileInfo[]>(
        `/projects/${encodeURIComponent(requestedProjectId)}/files`,
      );
      // 挂起 GET 返回时：仅当 owner projectId+session 仍匹配才写集合
      if (
        activeProjectIdRef.current === requestedProjectId &&
        projectSessionRef.current === session
      ) {
        setFilesRaw(Array.isArray(list) ? list : []);
        setFilesOwner({ projectId: requestedProjectId, session });
      }
    } catch {
      /* ignore：失败不得保留/回写旧 A 集合 */
    }
  }, [projectId]);

  const refreshTasks = useCallback(async () => {
    if (!projectId) return;
    const requestedProjectId = projectId;
    const session = projectSessionRef.current;
    try {
      const list = await apiFetch<PipelineTask[]>(
        `/projects/${encodeURIComponent(requestedProjectId)}/tasks`,
      );
      if (
        activeProjectIdRef.current === requestedProjectId &&
        projectSessionRef.current === session
      ) {
        setRecentTasksRaw(Array.isArray(list) ? list.slice(0, 8) : []);
        setRecentTasksOwner({ projectId: requestedProjectId, session });
      }
    } catch {
      /* ignore：失败不得保留/回写旧 A 集合 */
    }
  }, [projectId]);

  /**
   * A10：上传文件；启动时捕获 projectId+session。
   * 迟到 catch/finally 不得写 B error 或清 B busy；迟到 success 不交给旧 continuation。
   */
  const uploadFile = useCallback(
    async (file: File) => {
      const startedProjectId = projectId;
      const session = projectSessionRef.current;
      const isCurrentOwner = () =>
        Boolean(startedProjectId) &&
        activeProjectIdRef.current === startedProjectId &&
        projectSessionRef.current === session;

      if (isCurrentOwner()) {
        setBusy(true);
        setError(null);
      }
      try {
        const row = await apiUploadFile<ProjectFileInfo>(
          `/projects/${encodeURIComponent(startedProjectId)}/files`,
          file,
        );
        // A10：201 成功后始终对「启动项目」发起 files GET（因果门）；
        // 不得因软切跳过 GET。写集合仅当前 owner（启动 projectId+session）生效。
        try {
          const list = await apiFetch<ProjectFileInfo[]>(
            `/projects/${encodeURIComponent(startedProjectId)}/files`,
          );
          if (isCurrentOwner()) {
            setFilesRaw(Array.isArray(list) ? list : []);
            setFilesOwner({ projectId: startedProjectId, session });
          }
        } catch {
          /* ignore：列表失败不得阻断已成功的上传结果路径 */
        }
        if (!isCurrentOwner()) {
          // 迟到 success：拒绝交给旧页面 continuation（真实 Error，非假对象/永不 settle）
          throw new Error("上传结果已过期");
        }
        return row;
      } catch (err) {
        if (!isCurrentOwner()) {
          // 迟到 catch：不得写 B error，原样拒绝供调用方消费
          throw err;
        }
        const msg = (err as { message?: string })?.message || "上传失败";
        setError(msg);
        throw err;
      } finally {
        // 迟到 finally 不得清 B 新 busy
        if (isCurrentOwner()) setBusy(false);
      }
    },
    [projectId],
  );

  /**
   * A11+A12：上传图片；resolve 给调用方前校验启动 projectId+session。
   * 迟到结果必须 reject，由既有调用 catch 消费；旧 catch/finally 不得污染新项目。
   * 禁止空 ID / 假对象 / 永不 settle 冒充取消。
   */
  const uploadImage = useCallback(
    async (file: File) => {
      const startedProjectId = projectId;
      const session = projectSessionRef.current;
      const isCurrentOwner = () =>
        Boolean(startedProjectId) &&
        activeProjectIdRef.current === startedProjectId &&
        projectSessionRef.current === session;

      if (isCurrentOwner()) {
        setBusy(true);
        setError(null);
      }
      try {
        const row = await apiUploadFile<ProjectImageInfo>(
          `/projects/${encodeURIComponent(startedProjectId)}/images`,
          file,
        );
        if (!isCurrentOwner()) {
          throw new Error("图片上传结果已过期");
        }
        return row;
      } catch (err) {
        if (!isCurrentOwner()) {
          throw err;
        }
        const msg = (err as { message?: string })?.message || "图片上传失败";
        setError(msg);
        throw err;
      } finally {
        if (isCurrentOwner()) setBusy(false);
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

  /**
   * 用途：export success 后同源 Blob 下载；忽略 downloadPath；项目/session 围栏。
   * 返回 true 表示已在当前会话触发 anchor click（可写成功 tip）。
   * 对接：apiFetchDocxBlob；技术/商务导出页 await 本函数。
   * 二次开发：禁止 window.open/data/base64/storage；失败仅固定中文，不反转任务 success。
   */
  const downloadExport = useCallback(
    async (task: PipelineTask): Promise<boolean> => {
      const runProjectId = projectId;
      const sessionAtStart = projectSessionRef.current;

      const stillCurrent = () =>
        activeProjectIdRef.current === runProjectId &&
        projectSessionRef.current === sessionAtStart;

      const failCurrent = () => {
        if (stillCurrent()) {
          setError(DOWNLOAD_FAIL_UI);
        }
        return false;
      };

      if (!runProjectId || task.status !== "success") {
        return failCurrent();
      }
      const result = task.result;
      if (!result || typeof result !== "object" || Array.isArray(result)) {
        return failCurrent();
      }
      const stored = (result as { storedName?: unknown }).storedName;
      if (typeof stored !== "string" || !STORED_NAME_RE.test(stored)) {
        return failCurrent();
      }
      // 完全忽略 downloadPath；路径仅由当前 projectId + storedName 构造
      const path = `/projects/${encodeURIComponent(runProjectId)}/export/download/${encodeURIComponent(stored)}`;

      const gen = ++downloadGenRef.current;
      const prevAc = downloadAbortRef.current;
      downloadAbortRef.current = null;
      if (prevAc) {
        try {
          prevAc.abort();
        } catch {
          /* ignore */
        }
      }
      const ac = new AbortController();
      downloadAbortRef.current = ac;

      let objectUrl: string | null = null;
      try {
        const { blob, filename: headerName } = await apiFetchDocxBlob(path, {
          signal: ac.signal,
        });

        // await 后复核：项目切换/卸载/代次作废 → 零 click、零 setError
        if (ac.signal.aborted || downloadGenRef.current !== gen) {
          return false;
        }
        if (!stillCurrent()) {
          return false;
        }

        let saveName: string | null =
          headerName && isSafeDocxDownloadFilename(headerName)
            ? headerName
            : null;
        if (!saveName) {
          const taskFilename = (result as { filename?: unknown }).filename;
          if (
            typeof taskFilename === "string" &&
            isSafeDocxDownloadFilename(taskFilename)
          ) {
            saveName = taskFilename;
          }
        }
        if (!saveName) {
          saveName = FALLBACK_DOCX_NAME;
        }

        objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = saveName;
        anchor.rel = "noopener";
        anchor.style.display = "none";
        document.body.appendChild(anchor);

        // click 前再围栏：abort 不能替代本检查
        if (
          ac.signal.aborted ||
          downloadGenRef.current !== gen ||
          !stillCurrent()
        ) {
          anchor.remove();
          return false;
        }

        anchor.click();
        anchor.remove();
        return true;
      } catch (err) {
        const aborted =
          ac.signal.aborted ||
          (err instanceof DOMException && err.name === "AbortError") ||
          (err instanceof Error && err.name === "AbortError");
        if (aborted || downloadGenRef.current !== gen || !stillCurrent()) {
          return false;
        }
        setError(DOWNLOAD_FAIL_UI);
        return false;
      } finally {
        if (objectUrl) {
          try {
            URL.revokeObjectURL(objectUrl);
          } catch {
            /* ignore */
          }
        }
        // 仅清理本代次 controller；旧 finally 不得清新 controller
        if (downloadAbortRef.current === ac) {
          downloadAbortRef.current = null;
        }
      }
    },
    [projectId],
  );

  const canCancel =
    busy &&
    !!lastTask &&
    (lastTask.status === "pending" || lastTask.status === "running");

  /**
   * 用途：I3 合法 task-event 触发后，仅对「当前浏览器最近 runTask」且仍 pending/running
   *       的 taskId 发起一次 GET .../status；AbortController 取消旧请求；
   *       同 project+task 单飞；任一时刻最多一个 status GET；迟到响应按世代隔离。
   * 对接：GET /projects/{id}/tasks/{taskId}/status；ProjectTaskEventPanel.onSafeTaskEvent。
   * 二次开发：禁止详情 GET、editor-state、轮询、重试、覆盖 message/result/error。
   */
  const reconcileCurrentTaskStatus = useCallback(
    (eventTaskId: string) => {
      if (typeof eventTaskId !== "string" || !eventTaskId) return;
      const runProjectId = activeProjectIdRef.current;
      if (!runProjectId) return;
      const current = lastTaskRef.current;
      if (!current || current.id !== eventTaskId) return;
      if (!isActiveTask(current)) return;

      const inflight = reconcileInflightRef.current;
      if (
        inflight &&
        inflight.projectId === runProjectId &&
        inflight.taskId === eventTaskId
      ) {
        // 同项目同 task 请求未完成：单飞，不重复发起
        return;
      }

      // 不同 task/项目或无在途：取消旧 status，保证任一时刻最多一个 GET
      const prevAc = reconcileAbortRef.current;
      reconcileAbortRef.current = null;
      if (prevAc) {
        try {
          prevAc.abort();
        } catch {
          /* ignore */
        }
      }

      const gen = ++reconcileGenRef.current;
      const ac = new AbortController();
      reconcileAbortRef.current = ac;
      reconcileInflightRef.current = {
        gen,
        projectId: runProjectId,
        taskId: eventTaskId,
      };

      const path = `/projects/${encodeURIComponent(runProjectId)}/tasks/${encodeURIComponent(eventTaskId)}/status`;

      void (async () => {
        try {
          const raw = await apiFetch<unknown>(path, { signal: ac.signal });
          if (ac.signal.aborted) return;
          if (reconcileGenRef.current !== gen) return;
          if (activeProjectIdRef.current !== runProjectId) return;
          const still = lastTaskRef.current;
          if (!still || still.id !== eventTaskId) return;
          // 终态后忽略迟到 status（await 期间 pipeline/SSE 可能已 success）
          if (!isActiveTask(still)) return;

          const parsed = parseSafeStatusProjection(raw, eventTaskId);
          if (!parsed) return;

          // 只写 status/progress；与 E2E 共用同一纯函数，保留 message/result/error
          const next = applySafeStatusProjection(still, {
            status: parsed.status,
            progress: parsed.progress,
          });
          // 与 publishTask 同守卫：禁止终态被 pending/running 回退
          if (isTaskStateRegression(still, next)) return;
          lastTaskRef.current = next;
          setLastTask({ ...next });
        } catch {
          // AbortError / 接口失败：无 UI 原文、无重试（I3 文案由面板负责）
          if (ac.signal.aborted) return;
        } finally {
          // 仅清理本代次；旧 finally 不得清掉新请求
          if (reconcileInflightRef.current?.gen === gen) {
            reconcileInflightRef.current = null;
          }
          if (reconcileAbortRef.current === ac) {
            reconcileAbortRef.current = null;
          }
        }
      })();
    },
    [],
  );

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
    reconcileCurrentTaskStatus,
  };
}
