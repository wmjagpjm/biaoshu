/**
 * 模块：商务标工作区状态（P11B 服务端权威）
 * 用途：分步编辑数据只认 GET|PUT /api/projects/{id}/editor-state；真实空态保持空。
 * 对接：
 *   - GET|PUT /api/projects/{id}/editor-state（businessQualify/Toc/Quote/Commit、parsedMarkdown）
 *   - POST /api/projects/{id}/artifacts/workspace/revise（stage=business_*）
 * 明确非目标：
 *   - 禁止读写/删除/迁移 biaoshu.businessBid.workspace.*（旧键忽略并保值）
 *   - biaoshu.businessBid.feedback.{projectId} 仅作 AI 反馈历史本地存储，
 *     绝不参与 workspace 水合、API 成功判定或加载失败回退
 * 二次开发：形状保持 BusinessBidWorkspaceState；不得复活 createDemoWorkspace 生产路径。
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

  /** 跳过水合后的下一次防抖 PUT */
  const skipNextSave = useRef(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 项目会话代次：切项目或重置时递增，作废所有飞行中回调 */
  const sessionRef = useRef(0);
  /** 当前活跃项目 id（与 session 成对校验） */
  const activeProjectRef = useRef(projectId);
  /** 最新 workspace 引用，供防抖 PUT 闭包读取 */
  const workspaceRef = useRef(workspace);
  workspaceRef.current = workspace;

  /** 用途：判断回调是否仍属于当前项目会话。 */
  const isCurrentSession = useCallback(
    (session: number, pid: string) =>
      sessionRef.current === session &&
      activeProjectRef.current === pid &&
      pid === projectId,
    [projectId],
  );

  /**
   * 用途：从 editor-state 刷新当前项目。
   * 成功：水合真实字段、清 load/save error、apiReady=true，返回 true。
   * 失败：重置空 workspace、apiReady=false、固定 loadError，返回 false；不抛原文。
   */
  const refreshFromApi = useCallback(async (): Promise<boolean> => {
    const pid = projectId;
    if (!pid) {
      setLoading(false);
      return false;
    }
    const session = sessionRef.current;
    setLoading(true);
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(pid)}/editor-state`,
      );
      if (!isCurrentSession(session, pid)) return false;
      skipNextSave.current = true;
      setWorkspace(fromApi(pid, remote));
      setApiReady(true);
      setLoadError(null);
      setSaveError(null);
      return true;
    } catch {
      if (!isCurrentSession(session, pid)) return false;
      skipNextSave.current = true;
      setWorkspace(createEmptyWorkspace(pid));
      setApiReady(false);
      setLoadError(BUSINESS_EDITOR_LOAD_ERROR);
      setSaveError(null);
      return false;
    } finally {
      if (isCurrentSession(session, pid)) {
        setLoading(false);
      }
    }
  }, [isCurrentSession, projectId]);

  // 切项目：立即作废旧会话、清计时器/错误、重置空 workspace，再拉真实 GET
  useEffect(() => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    sessionRef.current += 1;
    activeProjectRef.current = projectId;
    skipNextSave.current = true;
    setApiReady(false);
    setLoadError(null);
    setSaveError(null);
    setWorkspace(createEmptyWorkspace(projectId));
    setHistory(loadHistory(projectId));
    setLoading(true);
    void refreshFromApi();
  }, [projectId, refreshFromApi]);

  // 仅 feedback 历史落盘；禁止写入 workspace 键
  useEffect(() => {
    if (!projectId) return;
    try {
      localStorage.setItem(feedbackKey(projectId), JSON.stringify(history));
    } catch {
      // 存储失败静默；不得 console 敏感内容
    }
  }, [projectId, history]);

  // 防抖写回 editor-state：仅当前项目 GET 成功（apiReady）后可 PUT
  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const session = sessionRef.current;
    const pid = projectId;
    saveTimer.current = setTimeout(() => {
      void (async () => {
        if (!isCurrentSession(session, pid)) return;
        const ws = workspaceRef.current;
        try {
          await apiFetch(
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
              }),
            },
          );
          if (!isCurrentSession(session, pid)) return;
          setSaveError(null);
        } catch {
          if (!isCurrentSession(session, pid)) return;
          // 固定中文；不得写异常 message 到页面/console/history/存储
          setSaveError(BUSINESS_EDITOR_SAVE_ERROR);
        }
      })();
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [apiReady, isCurrentSession, projectId, workspace]);

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
