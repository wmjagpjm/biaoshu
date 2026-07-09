import { useCallback, useEffect, useState } from "react";
import type {
  AiFeedbackRecord,
  FeedbackStage,
} from "../../../shared/types/aiFeedback";
import { createInitialWorkspace } from "../mock";
import type {
  BusinessBidWorkspaceState,
  CommitBlock,
  QualifyItem,
  QuoteRow,
  TocItem,
} from "../types";

/**
 * 模块：商务标工作区状态
 * 用途：分步编辑数据 + 反馈历史；localStorage 演示持久化。
 * 对接：后端就绪后改为 apiFetch，状态形状尽量保持不变。
 */

const storageKey = (projectId: string) =>
  `biaoshu.businessBid.workspace.${projectId}`;
const feedbackKey = (projectId: string) =>
  `biaoshu.businessBid.feedback.${projectId}`;

function loadWorkspace(projectId: string): BusinessBidWorkspaceState {
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (!raw) return createInitialWorkspace(projectId);
    const parsed = JSON.parse(raw) as BusinessBidWorkspaceState;
    return { ...createInitialWorkspace(projectId), ...parsed, projectId };
  } catch {
    return createInitialWorkspace(projectId);
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

export function useBusinessBidWorkspace(projectId: string) {
  const [workspace, setWorkspace] = useState<BusinessBidWorkspaceState>(() =>
    loadWorkspace(projectId),
  );
  const [history, setHistory] = useState<AiFeedbackRecord[]>(() =>
    loadHistory(projectId),
  );

  useEffect(() => {
    setWorkspace(loadWorkspace(projectId));
    setHistory(loadHistory(projectId));
  }, [projectId]);

  useEffect(() => {
    localStorage.setItem(storageKey(projectId), JSON.stringify(workspace));
  }, [projectId, workspace]);

  useEffect(() => {
    localStorage.setItem(feedbackKey(projectId), JSON.stringify(history));
  }, [projectId, history]);

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

  /**
   * 按反馈定向调整（演示）
   * 后端应：POST .../revise + 原产物 + message
   */
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

      await new Promise((r) => setTimeout(r, 650));

      const resultSummary = input.preserveStructure
        ? `已基于商务标原文定向修订（尽量保留结构）。意见：「${input.message.slice(0, 48)}${input.message.length > 48 ? "…" : ""}」`
        : `已按反馈较大幅度调整商务标结构。意见：「${input.message.slice(0, 48)}${input.message.length > 48 ? "…" : ""}」`;

      setHistory((prev) =>
        prev.map((h) =>
          h.id === id
            ? { ...h, status: "applied" as const, resultSummary }
            : h,
        ),
      );
    },
    [],
  );

  return {
    workspace,
    history,
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
