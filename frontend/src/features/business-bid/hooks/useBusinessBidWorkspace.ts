/**
 * 模块：商务标工作区状态
 * 用途：分步编辑数据 + 反馈历史；优先 editor-state API，失败回退 localStorage。
 * 对接：
 *   - GET|PUT /api/projects/{id}/editor-state（businessQualify/Toc/Quote/Commit、parsedMarkdown）
 *   - POST /api/projects/{id}/artifacts/workspace/revise（stage=business_*）
 * 二次开发：形状保持 BusinessBidWorkspaceState，勿拆 UI 信息架构。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  AiFeedbackRecord,
  FeedbackStage,
} from "../../../shared/types/aiFeedback";
import { createDemoWorkspace, createEmptyWorkspace } from "../mock";
import type {
  BusinessBidWorkspaceState,
  CommitBlock,
  QualifyItem,
  QuoteRow,
  TocItem,
} from "../types";

const storageKey = (projectId: string) =>
  `biaoshu.businessBid.workspace.${projectId}`;
const feedbackKey = (projectId: string) =>
  `biaoshu.businessBid.feedback.${projectId}`;

/** 演示 id 前缀：离线仍可看满数据 */
function isDemoProjectId(projectId: string): boolean {
  return projectId.startsWith("bb_");
}

type EditorStateApi = {
  projectId: string;
  parsedMarkdown?: string | null;
  businessQualify?: QualifyItem[] | null;
  businessToc?: TocItem[] | null;
  businessQuote?: { rows?: QuoteRow[]; notes?: string } | null;
  businessCommit?: CommitBlock[] | null;
};

function loadLocalWorkspace(projectId: string): BusinessBidWorkspaceState {
  const fallback = isDemoProjectId(projectId)
    ? createDemoWorkspace(projectId)
    : createEmptyWorkspace(projectId);
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as BusinessBidWorkspaceState;
    return { ...fallback, ...parsed, projectId };
  } catch {
    return fallback;
  }
}

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
 * 用途：远端 editor-state → 工作区；空数组保留为空，不回填演示 mock。
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
    loadLocalWorkspace(projectId),
  );
  const [history, setHistory] = useState<AiFeedbackRecord[]>(() =>
    loadHistory(projectId),
  );
  const [loading, setLoading] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [apiReady, setApiReady] = useState(false);
  const skipNextSave = useRef(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshFromApi = useCallback(async () => {
    if (!projectId) return;
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(projectId)}/editor-state`,
      );
      skipNextSave.current = true;
      setWorkspace(fromApi(projectId, remote));
      setApiReady(true);
      setSaveError(null);
    } catch {
      skipNextSave.current = true;
      setWorkspace(loadLocalWorkspace(projectId));
      setApiReady(false);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    setHistory(loadHistory(projectId));
    void refreshFromApi();
  }, [projectId, refreshFromApi]);

  // 本地备份
  useEffect(() => {
    localStorage.setItem(storageKey(projectId), JSON.stringify(workspace));
  }, [projectId, workspace]);

  useEffect(() => {
    localStorage.setItem(feedbackKey(projectId), JSON.stringify(history));
  }, [projectId, history]);

  // 防抖写回 editor-state
  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      void (async () => {
        try {
          await apiFetch(
            `/projects/${encodeURIComponent(projectId)}/editor-state`,
            {
              method: "PUT",
              body: JSON.stringify({
                parsedMarkdown: workspace.parseMarkdown,
                businessQualify: workspace.qualifyItems,
                businessToc: workspace.tocItems,
                businessQuote: {
                  rows: workspace.quoteRows,
                  notes: workspace.quoteNotes,
                },
                businessCommit: workspace.commitBlocks,
              }),
            },
          );
          setSaveError(null);
        } catch (err) {
          setSaveError(
            (err as { message?: string })?.message || "保存工作区失败",
          );
        }
      })();
    }, 600);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [apiReady, projectId, workspace]);

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
        // 按阶段取 baseContent
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

        // 后端已写 editor-state（结构化/解析）；刷新对齐表格
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
