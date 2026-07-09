import { useCallback, useEffect, useState } from "react";
import type {
  AiFeedbackRecord,
  FeedbackStage,
  ProjectFeedbackState,
  ProjectGenerationGuidance,
} from "../../../shared/types/aiFeedback";

const storageKey = (projectId: string) => `biaoshu.projectFeedback.${projectId}`;

function loadState(projectId: string): ProjectFeedbackState {
  const empty: ProjectFeedbackState = {
    projectId,
    guidance: {
      targetWordCount: 80000,
      chapterFocus: "",
      formatRequirements: "",
      extraRequirements: "",
      lockedForNextStage: false,
    },
    history: [],
  };
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as ProjectFeedbackState;
    return {
      ...empty,
      ...parsed,
      projectId,
      guidance: { ...empty.guidance, ...parsed.guidance },
      history: parsed.history ?? [],
    };
  } catch {
    return empty;
  }
}

function saveState(state: ProjectFeedbackState) {
  localStorage.setItem(storageKey(state.projectId), JSON.stringify(state));
}

/**
 * 项目级生成约束 + 各阶段反馈历史
 * 用途：招标分析后可编辑的要求会注入后续大纲/正文；各步「按反馈调整」写入 history。
 * 后端就绪后改为 API，接口形状可直接复用 ProjectFeedbackState。
 */
export function useProjectGuidance(projectId: string) {
  const [state, setState] = useState<ProjectFeedbackState>(() => loadState(projectId));

  useEffect(() => {
    setState(loadState(projectId));
  }, [projectId]);

  useEffect(() => {
    saveState(state);
  }, [state]);

  const updateGuidance = useCallback((patch: Partial<ProjectGenerationGuidance>) => {
    setState((prev) => ({
      ...prev,
      guidance: {
        ...prev.guidance,
        ...patch,
        updatedAt: new Date().toISOString(),
      },
    }));
  }, []);

  const submitRevise = useCallback(
    async (input: {
      stage: FeedbackStage;
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
    }) => {
      const id = `fb_${Date.now()}`;
      const applying: AiFeedbackRecord = {
        id,
        stage: input.stage,
        message: input.message,
        targetId: input.targetId,
        targetLabel: input.targetLabel,
        createdAt: new Date().toISOString(),
        status: "applying",
      };

      setState((prev) => ({
        ...prev,
        history: [applying, ...prev.history],
      }));

      // 演示：模拟 AI 定向修订耗时；后端替换为 revise 任务 + SSE
      await new Promise((r) => setTimeout(r, 700));

      const resultSummary = input.preserveStructure
        ? `已基于原文定向修订（保留结构）。意见：「${input.message.slice(0, 48)}${input.message.length > 48 ? "…" : ""}」`
        : `已按反馈较大幅度调整结构。意见：「${input.message.slice(0, 48)}${input.message.length > 48 ? "…" : ""}」`;

      setState((prev) => ({
        ...prev,
        history: prev.history.map((h) =>
          h.id === id ? { ...h, status: "applied" as const, resultSummary } : h,
        ),
      }));
    },
    [],
  );

  return {
    guidance: state.guidance,
    history: state.history,
    updateGuidance,
    submitRevise,
  };
}
