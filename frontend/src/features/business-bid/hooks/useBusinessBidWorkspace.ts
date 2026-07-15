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
  const refreshFromApi = useCallback(async (): Promise<boolean> => {
    const pid = projectId;
    if (!pid) {
      setLoading(false);
      return false;
    }
    const session = sessionRef.current;
    // 同项目重载：递增写入代次并清未发送 timer
    writeEpochRef.current += 1;
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    setLoading(true);
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
      if (isCurrentSession(session, pid)) {
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
        if (!isCurrentWriteEpoch(session, pid, epoch)) return;
        if (fullStateBlockedRef.current) return;
        const expected = stateVersionRef.current;
        if (!isValidStateVersion(expected)) {
          enterFullStateBlock();
          return;
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
          if (!isCurrentWriteEpoch(session, pid, epoch)) return;
          if (!isValidStateVersion(saved.stateVersion)) {
            enterFullStateBlock();
            return;
          }
          stateVersionRef.current = saved.stateVersion;
          setSaveError(null);
        } catch (err) {
          if (!isCurrentWriteEpoch(session, pid, epoch)) return;
          const status = (err as { status?: number })?.status;
          const code = (err as { code?: string })?.code;
          if (
            status === 409 &&
            code === "editor_state_version_conflict"
          ) {
            enterFullStateBlock();
            return;
          }
          // 固定中文；不得写异常 message 到页面/console/history/存储
          setSaveError(BUSINESS_EDITOR_SAVE_ERROR);
        }
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
    enterFullStateBlock,
    isCurrentWriteEpoch,
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

      try {
        let baseContent = workspace.parseMarkdown;
        if (input.stage === "business_qualify") {
          baseContent = JSON.stringify(workspace.qualifyItems, null, 2);
        } else if (input.stage === "business_toc") {
          baseContent = JSON.stringify(workspace.tocItems, null, 2);
        } else if (input.stage === "business_quote") {
          baseContent = JSON.stringify(
            { rows: workspace.quoteRows, notes: workspace.quoteNotes },
            null,
            2,
          );
        } else if (input.stage === "business_commit") {
          baseContent = JSON.stringify(workspace.commitBlocks, null, 2);
        }

        const res = await apiFetch<{
          resultSummary?: string;
          revisedContent?: string | null;
          status?: string;
        }>(
          `/projects/${encodeURIComponent(projectId)}/artifacts/workspace/revise`,
          {
            method: "POST",
            body: JSON.stringify({
              stage: input.stage,
              message: input.message,
              preserveStructure: input.preserveStructure,
              baseContent,
              targetId: input.targetId,
              targetLabel: input.targetLabel,
            }),
          },
        );

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

        // 业务成功事实不反转；刷新失败进入固定加载失败态，不把旧内容当最新
        if (
          input.stage === "business_parse" ||
          input.stage === "business_qualify" ||
          input.stage === "business_toc" ||
          input.stage === "business_quote" ||
          input.stage === "business_commit"
        ) {
          await refreshFromApi();
        }
        return res;
      } catch (err) {
        const msg = (err as { message?: string })?.message || "修订失败";
        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? { ...h, status: "failed" as const, resultSummary: msg }
              : h,
          ),
        );
        throw err;
      }
    },
    [projectId, refreshFromApi, workspace],
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
