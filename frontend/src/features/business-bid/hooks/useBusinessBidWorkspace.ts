/**
 * 模块：商务标工作区状态（P11B 服务端权威 + P12B 全状态 CAS）
 * 用途：分步编辑数据只认 GET|PUT /api/projects/{id}/editor-state；真实空态保持空；
 *       全部 editor-state PUT 携带 expectedStateVersion；同项目串行保存链。
 * 对接：
 *   - GET|PUT /api/projects/{id}/editor-state（businessQualify/Toc/Quote/Commit、parsedMarkdown）
 *   - POST /api/projects/{id}/artifacts/workspace/revise（stage=business_*）
 * 明确非目标：
 *   - 禁止读写/删除/迁移 biaoshu.businessBid.workspace.*（旧键忽略并保值）
 *   - biaoshu.businessBid.feedback.{projectId} 仅作 AI 反馈历史本地存储，
 *     绝不参与 workspace 水合、API 成功判定或加载失败回退
 *   - 禁止本地计算 stateVersion；禁止版本落盘
 * 二次开发：形状保持 BusinessBidWorkspaceState；不得复活 createDemoWorkspace 生产路径；
 *       全状态 409 阻断后仅允许显式全量 GET 恢复。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createEditorStateCheckpoint as postCreateEditorStateCheckpoint,
  isCheckpointCreateStateVersionError,
  restoreEditorStateCheckpoint as postRestoreEditorStateCheckpoint,
} from "../../editor-state-checkpoints/editorStateCheckpointApi";
import type {
  CheckpointCreateOutcome,
  CheckpointRestoreOutcome,
} from "../../editor-state-checkpoints/EditorStateCheckpointPanel";
import { restoreEditorStateRevision as postRestoreEditorStateRevision } from "../../editor-state-revisions/editorStateRevisionApi";
import type { RevisionRestoreOutcome } from "../../editor-state-revisions/EditorStateRevisionPanel";
import { apiFetch } from "../../../shared/lib/api";
import type {
  AiFeedbackRecord,
  FeedbackStage,
} from "../../../shared/types/aiFeedback";
import { createEmptyWorkspace } from "../mock";
import type {
  BusinessBidWorkspaceState,
  CommitBlock,
  QualifyItem,
  QuoteRow,
  TocItem,
} from "../types";

/** 固定加载失败文案（脱敏，不得拼接后端原文） */
export const BUSINESS_EDITOR_LOAD_ERROR =
  "商务标工作区加载失败，请稍后重试";

/** 固定保存失败文案（脱敏，不得拼接后端原文） */
export const BUSINESS_EDITOR_SAVE_ERROR =
  "商务标工作区保存失败，请稍后重试";

/**
 * 固定全状态版本冲突文案。
 * 用途：P12B CAS 冲突固定中文；禁止拼接服务端 message/version/正文。
 */
export const BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

/** 服务端 stateVersion 精确格式 */
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

/** 用途：校验服务端 stateVersion。 */
function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/** 用途：版本化外部写（检查点 restore）结果；与技术标 runner 语义对齐。 */
export type BusinessVersionedExternalWriteOutcome<T> =
  | { status: "success"; data: T }
  | { status: "post_failed"; blocked: boolean }
  | { status: "reload_failed"; data: T };

/**
 * 反馈历史 localStorage 键。
 * 非 editor-state 权威；仅保留既有 AI 反馈记录语义。
 */
const feedbackKey = (projectId: string) =>
  `biaoshu.businessBid.feedback.${projectId}`;

type EditorStateApi = {
  projectId?: string;
  parsedMarkdown?: string | null;
  businessQualify?: QualifyItem[] | null;
  businessToc?: TocItem[] | null;
  businessQuote?: { rows?: QuoteRow[]; notes?: string } | null;
  businessCommit?: CommitBlock[] | null;
  /** P12B：全状态版本；仅接受 ^esv_[0-9a-f]{32}$ */
  stateVersion?: string | null;
};

/** 用途：读取反馈历史；失败返回 []，不抛原文。 */
function loadHistory(projectId: string): AiFeedbackRecord[] {
  try {
    const raw = localStorage.getItem(feedbackKey(projectId));
    if (!raw) return [];
    return JSON.parse(raw) as AiFeedbackRecord[];
  } catch {
    return [];
  }
}

/**
 * 用途：远端 editor-state → 工作区；空数组/空字段保留为空，不回填演示 mock。
 */
function fromApi(
  projectId: string,
  remote: EditorStateApi,
): BusinessBidWorkspaceState {
  const empty = createEmptyWorkspace(projectId);
  const quote = remote.businessQuote;
  return {
    projectId,
    parseMarkdown:
      remote.parsedMarkdown != null ? String(remote.parsedMarkdown) : "",
    qualifyItems: Array.isArray(remote.businessQualify)
      ? remote.businessQualify
      : empty.qualifyItems,
    tocItems: Array.isArray(remote.businessToc)
      ? remote.businessToc
      : empty.tocItems,
    quoteRows:
      quote && Array.isArray(quote.rows) ? quote.rows : empty.quoteRows,
    quoteNotes:
      quote && typeof quote.notes === "string" ? quote.notes : empty.quoteNotes,
    commitBlocks: Array.isArray(remote.businessCommit)
      ? remote.businessCommit
      : empty.commitBlocks,
  };
}

export function useBusinessBidWorkspace(projectId: string) {
  const [workspace, setWorkspace] = useState<BusinessBidWorkspaceState>(() =>
    createEmptyWorkspace(projectId),
  );
  const [history, setHistory] = useState<AiFeedbackRecord[]>(() =>
    loadHistory(projectId),
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [apiReady, setApiReady] = useState(false);
  /** 全状态 CAS 冲突/版本未知阻断 */
  const [fullStateConflict, setFullStateConflict] = useState(false);

  /** 跳过水合后的下一次防抖 PUT */
  const skipNextSave = useRef(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 项目会话代次：切项目或重置时递增，作废所有飞行中回调 */
  const sessionRef = useRef(0);
  /** 同项目写入代次：重载时递增，旧代次回调不得覆盖新 GET */
  const writeEpochRef = useRef(0);
  /** 当前活跃项目 id（与 session 成对校验） */
  const activeProjectRef = useRef(projectId);
  /** 最新 workspace 引用，供队列执行时读取 */
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;
  /** 当前内存服务端全状态版本 */
  const stateVersionRef = useRef<string | null>(null);
  /** 全状态阻断后禁止自动 PUT */
  const fullStateBlockedRef = useRef(false);
  /**
   * 创建/恢复操作 token（项目绑定）：
   * - 同项目连点：token 已属本项目 → 拒绝第二请求
   * - 不同项目：允许新项目启动并把 ref 覆盖为新 token
   * - finally 仅当 projectId+token 仍匹配时清空，绝不能误清新项目 token
   * 禁止：跨项目共享 boolean，或切项目时简单 boolean=false
   */
  const checkpointOpTokenRef = useRef<{
    projectId: string;
    token: number;
  } | null>(null);
  const checkpointOpTokenSeqRef = useRef(0);
  /** 同项目串行保存链 */
  const saveChainRef = useRef(Promise.resolve());

  /** 用途：判断回调是否仍属于当前项目会话。 */
  const isCurrentSession = useCallback(
    (session: number, pid: string) =>
      sessionRef.current === session &&
      activeProjectRef.current === pid &&
      pid === projectId,
    [projectId],
  );

  const isCurrentWriteEpoch = useCallback(
    (session: number, pid: string, epoch: number) =>
      isCurrentSession(session, pid) && writeEpochRef.current === epoch,
    [isCurrentSession],
  );

  const enterFullStateBlock = useCallback(() => {
    fullStateBlockedRef.current = true;
    setFullStateConflict(true);
    setSaveError(null);
  }, []);

  /**
   * 用途：从 editor-state 刷新当前项目。
   * 成功：水合真实字段、接受合法 stateVersion、清冲突、apiReady=true。
   * 失败：见全状态阻断分支；不抛原文。
   */
  const refreshFromApi = useCallback(async (options?: { silent?: boolean }): Promise<boolean> => {
    const pid = projectId;
    if (!pid) {
      setLoading(false);
      return false;
    }
    const session = sessionRef.current;
    const silent = options?.silent === true;
    // 同项目重载：递增写入代次并清未发送 timer
    writeEpochRef.current += 1;
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    // silent：检查点 restore 唯一 GET，避免 loading 卸载工作区导致面板状态丢失
    if (!silent) setLoading(true);
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(pid)}/editor-state`,
      );
      if (!isCurrentSession(session, pid)) return false;
      if (!isValidStateVersion(remote.stateVersion)) {
        if (fullStateBlockedRef.current) {
          // 保持本地与阻断；不卸载正文
          setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
          return false;
        }
        skipNextSave.current = true;
        setWorkspace(createEmptyWorkspace(pid));
        stateVersionRef.current = null;
        setApiReady(false);
        setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
        setSaveError(null);
        setFullStateConflict(false);
        fullStateBlockedRef.current = false;
        return false;
      }
      skipNextSave.current = true;
      setWorkspace(fromApi(pid, remote));
      stateVersionRef.current = remote.stateVersion;
      setApiReady(true);
      setLoadError(null);
      setSaveError(null);
      setFullStateConflict(false);
      fullStateBlockedRef.current = false;
      return true;
    } catch {
      if (!isCurrentSession(session, pid)) return false;
      if (fullStateBlockedRef.current) {
        setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
        return false;
      }
      skipNextSave.current = true;
      setWorkspace(createEmptyWorkspace(pid));
      stateVersionRef.current = null;
      setApiReady(false);
      setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
      setSaveError(null);
      setFullStateConflict(false);
      fullStateBlockedRef.current = false;
      return false;
    } finally {
      if (!silent && isCurrentSession(session, pid)) {
        setLoading(false);
      }
    }
  }, [isCurrentSession, projectId]);

  // 切项目：立即作废旧会话、清计时器/错误/版本/链，重置空 workspace，再拉真实 GET
  useEffect(() => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    sessionRef.current += 1;
    writeEpochRef.current += 1;
    activeProjectRef.current = projectId;
    saveChainRef.current = Promise.resolve();
    skipNextSave.current = true;
    stateVersionRef.current = null;
    fullStateBlockedRef.current = false;
    setApiReady(false);
    setLoadError(null);
    setSaveError(null);
    setFullStateConflict(false);
    setWorkspace(createEmptyWorkspace(projectId));
    setHistory(loadHistory(projectId));
    setLoading(true);
    void refreshFromApi();
  }, [projectId, refreshFromApi]);

  // 仅 feedback 历史落盘；禁止写入 workspace 键或版本
  useEffect(() => {
    if (!projectId) return;
    try {
      localStorage.setItem(feedbackKey(projectId), JSON.stringify(history));
    } catch {
      // 存储失败静默；不得 console 敏感内容
    }
  }, [projectId, history]);


  /**
   * 用途：共享「构造最新 body + 执行 PUT + 接受版本/冲突处理」执行器。
   * 对接：普通防抖 autosave 与显式创建检查点强制即时 PUT 共用；禁止第二套 body。
   */
  type ImmediatePutStatus =
    | "ok"
    | "stale"
    | "blocked"
    | "full_conflict"
    | "error"
    | "invalid_version";

  const executeImmediateEditorStatePut = useCallback(
    async (
      session: number,
      pid: string,
      epoch: number,
    ): Promise<ImmediatePutStatus> => {
      if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
      if (fullStateBlockedRef.current) return "blocked";
      const expected = stateVersionRef.current;
      if (!isValidStateVersion(expected)) {
        enterFullStateBlock();
        return "invalid_version";
      }
      const ws = workspaceRef.current;
      try {
        const saved = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(pid)}/editor-state`,
          {
            method: "PUT",
            body: JSON.stringify({
              parsedMarkdown: ws.parseMarkdown,
              businessQualify: ws.qualifyItems,
              businessToc: ws.tocItems,
              businessQuote: {
                rows: ws.quoteRows,
                notes: ws.quoteNotes,
              },
              businessCommit: ws.commitBlocks,
              expectedStateVersion: expected,
            }),
          },
        );
        if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
        if (!isValidStateVersion(saved.stateVersion)) {
          enterFullStateBlock();
          return "invalid_version";
        }
        stateVersionRef.current = saved.stateVersion;
        setSaveError(null);
        return "ok";
      } catch (err) {
        if (!isCurrentWriteEpoch(session, pid, epoch)) return "stale";
        const status = (err as { status?: number })?.status;
        const code = (err as { code?: string })?.code;
        if (status === 409 && code === "editor_state_version_conflict") {
          enterFullStateBlock();
          return "full_conflict";
        }
        setSaveError(BUSINESS_EDITOR_SAVE_ERROR);
        return "error";
      }
    },
    [enterFullStateBlock, isCurrentWriteEpoch],
  );

  // 防抖入队：真正执行时读取 workspaceRef + stateVersionRef 最新值
  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (fullStateBlockedRef.current) {
      return;
    }
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const session = sessionRef.current;
    const pid = projectId;
    const epoch = writeEpochRef.current;
    saveTimer.current = setTimeout(() => {
      const runSave = async () => {
        await executeImmediateEditorStatePut(session, pid, epoch);
      };
      saveChainRef.current = saveChainRef.current
        .catch(() => undefined)
        .then(runSave);
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [
    apiReady,
    executeImmediateEditorStatePut,
    projectId,
    workspace,
  ]);

  const setParseMarkdown = useCallback((parseMarkdown: string) => {
    setWorkspace((prev) => ({ ...prev, parseMarkdown }));
  }, []);

  const updateQualifyItem = useCallback(
    (id: string, patch: Partial<QualifyItem>) => {
      setWorkspace((prev) => ({
        ...prev,
        qualifyItems: prev.qualifyItems.map((item) =>
          item.id === id ? { ...item, ...patch } : item,
        ),
      }));
    },
    [],
  );

  const toggleTocItem = useCallback((id: string) => {
    setWorkspace((prev) => ({
      ...prev,
      tocItems: prev.tocItems.map((item) =>
        item.id === id ? { ...item, checked: !item.checked } : item,
      ),
    }));
  }, []);

  const updateTocItem = useCallback((id: string, patch: Partial<TocItem>) => {
    setWorkspace((prev) => ({
      ...prev,
      tocItems: prev.tocItems.map((item) =>
        item.id === id ? { ...item, ...patch } : item,
      ),
    }));
  }, []);

  const updateQuoteRow = useCallback((id: string, patch: Partial<QuoteRow>) => {
    setWorkspace((prev) => ({
      ...prev,
      quoteRows: prev.quoteRows.map((row) =>
        row.id === id ? { ...row, ...patch } : row,
      ),
    }));
  }, []);

  const setQuoteNotes = useCallback((quoteNotes: string) => {
    setWorkspace((prev) => ({ ...prev, quoteNotes }));
  }, []);

  const updateCommitBlock = useCallback(
    (id: string, patch: Partial<CommitBlock>) => {
      setWorkspace((prev) => ({
        ...prev,
        commitBlocks: prev.commitBlocks.map((block) =>
          block.id === id ? { ...block, ...patch } : block,
        ),
      }));
    },
    [],
  );

  const submitRevise = useCallback(
    async (input: {
      stage: FeedbackStage;
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
    }) => {
      const id = `bb_fb_${Date.now()}`;
      const applying: AiFeedbackRecord = {
        id,
        stage: input.stage,
        message: input.message,
        targetId: input.targetId,
        targetLabel: input.targetLabel,
        createdAt: new Date().toISOString(),
        status: "applying",
      };
      setHistory((prev) => [applying, ...prev]);

      const writesState =
        input.stage === "business_parse" ||
        input.stage === "business_qualify" ||
        input.stage === "business_toc" ||
        input.stage === "business_quote" ||
        input.stage === "business_commit";

      /**
       * 用途：商务 revise 进入既有 saveChainRef；真正执行时读最新 expected；
       * 成功后阻断旧本地写并单次 refresh；409/非法版本/网络不确定均保留本地并阻断。
       */
      const runRevise = async () => {
        const session = sessionRef.current;
        const pid = projectId;
        if (!isCurrentSession(session, pid)) {
          throw new Error("修订已取消");
        }

        const ws = workspaceRef.current;
        let baseContent = ws.parseMarkdown;
        if (input.stage === "business_qualify") {
          baseContent = JSON.stringify(ws.qualifyItems, null, 2);
        } else if (input.stage === "business_toc") {
          baseContent = JSON.stringify(ws.tocItems, null, 2);
        } else if (input.stage === "business_quote") {
          baseContent = JSON.stringify(
            { rows: ws.quoteRows, notes: ws.quoteNotes },
            null,
            2,
          );
        } else if (input.stage === "business_commit") {
          baseContent = JSON.stringify(ws.commitBlocks, null, 2);
        }

        const body: Record<string, unknown> = {
          stage: input.stage,
          message: input.message,
          preserveStructure: input.preserveStructure,
          baseContent,
          targetId: input.targetId,
          targetLabel: input.targetLabel,
        };

        if (writesState) {
          if (fullStateBlockedRef.current) {
            const err = new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE) as Error & {
              status?: number;
              code?: string;
            };
            err.status = 409;
            err.code = "editor_state_version_conflict";
            throw err;
          }
          const expected = stateVersionRef.current;
          if (!isValidStateVersion(expected)) {
            enterFullStateBlock();
            throw new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE);
          }
          body.expectedStateVersion = expected;
        }

        let res: {
          resultSummary?: string;
          revisedContent?: string | null;
          status?: string;
          stateVersion?: string | null;
        };
        try {
          res = await apiFetch(
            `/projects/${encodeURIComponent(pid)}/artifacts/workspace/revise`,
            {
              method: "POST",
              body: JSON.stringify(body),
            },
          );
        } catch (err) {
          if (!isCurrentSession(session, pid)) throw err;
          if (writesState) {
            const status = (err as { status?: number })?.status;
            const code = (err as { code?: string })?.code;
            if (
              status === 409 &&
              code === "editor_state_version_conflict"
            ) {
              // 陈旧：保留本地，阻断旧自动 PUT
              enterFullStateBlock();
            } else {
              // 网络不确定 / 其它失败：保守阻断，禁止旧 UI 自动保存
              enterFullStateBlock();
            }
          }
          throw err;
        }

        if (!isCurrentSession(session, pid)) return res;

        if (writesState) {
          // 成功响应必须带合法新版本；否则阻断且不自动覆盖本地
          if (!isValidStateVersion(res.stateVersion)) {
            enterFullStateBlock();
            setHistory((prev) =>
              prev.map((h) =>
                h.id === id
                  ? {
                      ...h,
                      status: "failed" as const,
                      resultSummary: "修订响应缺少有效版本，已停止自动保存",
                    }
                  : h,
              ),
            );
            throw new Error(BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE);
          }
          // 先接受服务端新版本并阻断，再单次 refresh；成功才解除阻断
          stateVersionRef.current = res.stateVersion;
          fullStateBlockedRef.current = true;
          setFullStateConflict(true);
          const ok = await refreshFromApi();
          if (!isCurrentSession(session, pid)) return res;
          if (!ok) {
            // 重读失败：保持本地与阻断
            enterFullStateBlock();
          }
        }

        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? {
                  ...h,
                  status: "applied" as const,
                  resultSummary:
                    res.resultSummary ||
                    res.revisedContent?.slice(0, 120) ||
                    "已修订",
                }
              : h,
          ),
        );
        return res;
      };

      const queued = saveChainRef.current
        .catch(() => undefined)
        .then(runRevise);
      // 后续保存等待 revise 完成；revise 失败不卡死整条链（链上仅 void）
      saveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);

      try {
        return await queued;
      } catch {
        // 页面以 void 调用 submitRevise；此处不得再抛，避免 unhandled rejection
        // 固定脱敏失败摘要，禁止把异常正文写入 UI/history/console
        const msg =
          writesState && fullStateBlockedRef.current
            ? BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE
            : "修订失败";
        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? { ...h, status: "failed" as const, resultSummary: msg }
              : h,
          ),
        );
        return undefined;
      }
    },
    [
      enterFullStateBlock,
      isCurrentSession,
      projectId,
      refreshFromApi,
    ],
  );


  /**
   * 用途：P12B-D2 版本化外部写 runner（检查点 restore）；与技术标语义对齐。
   * 约束：进入 saveChainRef；执行时读最新 expected；成功唯一 refreshFromApi；零自动重试。
   */
  const runVersionedExternalWrite = useCallback(
    async <T extends { stateVersion: string }>(
      execute: (expectedStateVersion: string) => Promise<T>,
    ): Promise<BusinessVersionedExternalWriteOutcome<T>> => {
      const requestSession = sessionRef.current;
      const requestPid = projectId;
      const requestEpoch = writeEpochRef.current;

      const run = async (): Promise<BusinessVersionedExternalWriteOutcome<T>> => {
        if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
          return { status: "post_failed", blocked: false };
        }
        if (fullStateBlockedRef.current) {
          return { status: "post_failed", blocked: true };
        }
        const expected = stateVersionRef.current;
        if (!isValidStateVersion(expected)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        let data: T;
        try {
          data = await execute(expected);
        } catch {
          if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
            return { status: "post_failed", blocked: false };
          }
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        if (!isCurrentWriteEpoch(requestSession, requestPid, requestEpoch)) {
          return { status: "post_failed", blocked: false };
        }
        if (!isValidStateVersion(data.stateVersion)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        stateVersionRef.current = data.stateVersion;
        fullStateBlockedRef.current = true;
        setFullStateConflict(true);
        if (saveTimer.current) {
          clearTimeout(saveTimer.current);
          saveTimer.current = null;
        }

        const reloaded = await refreshFromApi({ silent: true });
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "reload_failed", data };
        }
        if (!reloaded) {
          enterFullStateBlock();
          return { status: "reload_failed", data };
        }
        // 禁止在此将 skipNextSave 置 false：
        // refreshFromApi 水合已设 skipNextSave=true，须由 effect 只吞一次水合触发的
        // 自动保存；若此处清零会形成恢复后旧 UI 自动 PUT。
        // 用户下一次真实编辑时 skip 已消费完毕，PUT 正常发出。
        return { status: "success", data };
      };

      const queued = saveChainRef.current.catch(() => undefined).then(run);
      saveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);
      return queued;
    },
    [
      projectId,
      isCurrentWriteEpoch,
      isCurrentSession,
      enterFullStateBlock,
      refreshFromApi,
    ],
  );

  /**
   * 用途：P12B-D2 显式创建检查点——清 timer、串行链内强制即时 PUT，再 POST 精确 {}。
   */
  const createCheckpoint = useCallback(async (): Promise<CheckpointCreateOutcome> => {
    if (!projectId) return { status: "blocked" };
    if (fullStateBlockedRef.current) return { status: "blocked" };
    if (!isValidStateVersion(stateVersionRef.current)) return { status: "blocked" };
    const requestPid = projectId;
    const existingOp = checkpointOpTokenRef.current;
    if (existingOp && existingOp.projectId === requestPid) {
      return { status: "failed" };
    }
    const myToken = ++checkpointOpTokenSeqRef.current;
    checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };

    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }

    const requestSession = sessionRef.current;

    const run = async (): Promise<CheckpointCreateOutcome> => {
      try {
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "failed" };
        }
        if (fullStateBlockedRef.current) return { status: "blocked" };
        if (!isValidStateVersion(stateVersionRef.current)) {
          return { status: "blocked" };
        }

        const epoch = writeEpochRef.current;
        const putStatus = await executeImmediateEditorStatePut(
          requestSession,
          requestPid,
          epoch,
        );
        if (!isCurrentSession(requestSession, requestPid)) {
          return { status: "failed" };
        }
        if (putStatus === "ok") {
          const putVersion = stateVersionRef.current;
          if (!isValidStateVersion(putVersion)) {
            enterFullStateBlock();
            return { status: "blocked" };
          }
          try {
            const meta = await postCreateEditorStateCheckpoint(requestPid);
            if (!isCurrentSession(requestSession, requestPid)) {
              return { status: "failed" };
            }
            // POST 版本不等于已接受 PUT 版本：远端期间可能已变，全状态阻断
            // （缺失/空白/非法 stateVersion 由 parse 专用错误在 catch 中阻断）
            if (
              !isValidStateVersion(meta.stateVersion) ||
              meta.stateVersion !== putVersion
            ) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "success" };
          } catch (err) {
            if (!isCurrentSession(requestSession, requestPid)) {
              return { status: "failed" };
            }
            // 仅 create 成功体 stateVersion 语义失败 → 全量阻断；
            // 网络/HTTP/额外字段等普通 shape 失败仍 failed，禁止扩大阻断语义
            if (isCheckpointCreateStateVersionError(err)) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "failed" };
          }
        }
        if (
          putStatus === "full_conflict" ||
          putStatus === "invalid_version" ||
          putStatus === "blocked"
        ) {
          return { status: "blocked" };
        }
        // forced-create：网络 abort / 非 409 HTTP / 不可判定失败 → 保守全状态阻断
        // 普通防抖 PUT 的 error 语义不变（executeImmediateEditorStatePut 仅 setSaveError）
        if (putStatus === "error") {
          enterFullStateBlock();
          return { status: "blocked" };
        }
        return { status: "failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    };

    const queued = saveChainRef.current.catch(() => undefined).then(run);
    saveChainRef.current = queued
      .then(() => undefined)
      .catch(() => undefined);
    return queued;
  }, [
    projectId,
    isCurrentSession,
    executeImmediateEditorStatePut,
    enterFullStateBlock,
  ]);

  /**
   * 用途：P12B-D2 检查点安全恢复。
   */
  const restoreCheckpoint = useCallback(
    async (checkpointId: string): Promise<CheckpointRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestPid = projectId;
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestPid) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateCheckpoint(
            requestPid,
            checkpointId,
            expectedStateVersion,
          ),
        );
        if (outcome.status === "success") return { status: "success" };
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  /**
   * 用途：P12C-C3 修订受限恢复——复用检查点操作令牌与版本化外部写 runner。
   * 约束：执行时读最新 expected；成功唯一 GET；零自动重试。
   */
  const restoreRevision = useCallback(
    async (revisionId: string): Promise<RevisionRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestPid = projectId;
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestPid) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = { projectId: requestPid, token: myToken };
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateRevision(
            requestPid,
            revisionId,
            expectedStateVersion,
          ),
        );
        if (outcome.status === "success") return { status: "success" };
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (cur && cur.projectId === requestPid && cur.token === myToken) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  return {
    workspace,
    history,
    loading,
    loadError,
    saveError,
    apiReady,
    fullStateConflict,
    fullStateConflictMessage: fullStateConflict
      ? BUSINESS_EDITOR_STATE_CONFLICT_MESSAGE
      : null,
    refreshFromApi,
    createCheckpoint,
    restoreCheckpoint,
    restoreRevision,
    setParseMarkdown,
    updateQualifyItem,
    toggleTocItem,
    updateTocItem,
    updateQuoteRow,
    setQuoteNotes,
    updateCommitBlock,
    submitRevise,
  };
}
