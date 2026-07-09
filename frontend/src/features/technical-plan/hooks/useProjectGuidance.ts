/**
 * 模块：项目级生成约束 + 各阶段反馈历史
 * 用途：
 *   1. guidance 随 editor-state 持久化到后端
 *   2. submitRevise 调 POST revise，返回 revisedContent 供工作区预览/替换
 * 对接：/projects/{id}/editor-state、/projects/{id}/artifacts/{aid}/revise
 * 二次开发：history 也可入库；当前 history 仍 localStorage
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  AiFeedbackRecord,
  FeedbackStage,
  ProjectFeedbackState,
  ProjectGenerationGuidance,
} from "../../../shared/types/aiFeedback";

const storageKey = (projectId: string) => `biaoshu.projectFeedback.${projectId}`;

function emptyGuidance(): ProjectGenerationGuidance {
  return {
    targetWordCount: 80000,
    chapterFocus: "",
    formatRequirements: "",
    extraRequirements: "",
    lockedForNextStage: false,
  };
}

function loadState(projectId: string): ProjectFeedbackState {
  const empty: ProjectFeedbackState = {
    projectId,
    guidance: emptyGuidance(),
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

export type ReviseSubmitResult = {
  ok: boolean;
  resultSummary?: string;
  revisedContent?: string | null;
  error?: string;
};

type ReviseApiResult = {
  id: string;
  stage: FeedbackStage;
  message: string;
  targetId?: string;
  targetLabel?: string;
  createdAt: string;
  status: "queued" | "applying" | "applied" | "failed";
  resultSummary?: string;
  revisedContent?: string | null;
  model?: string;
};

export function useProjectGuidance(projectId: string) {
  const [state, setState] = useState<ProjectFeedbackState>(() =>
    loadState(projectId),
  );
  const skipSave = useRef(true);
  const saveTimer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    skipSave.current = true;
    const local = loadState(projectId);
    setState(local);

    void (async () => {
      try {
        const remote = await apiFetch<{
          guidance?: ProjectGenerationGuidance | null;
        }>(`/projects/${encodeURIComponent(projectId)}/editor-state`);
        if (cancelled) return;
        if (remote.guidance && typeof remote.guidance === "object") {
          setState((prev) => ({
            ...prev,
            projectId,
            guidance: { ...emptyGuidance(), ...remote.guidance },
            history: prev.history.length ? prev.history : local.history,
          }));
        }
      } catch {
        /* 保持 local */
      } finally {
        if (!cancelled) {
          window.setTimeout(() => {
            skipSave.current = false;
          }, 50);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [projectId]);

  useEffect(() => {
    saveState(state);
    if (skipSave.current) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void apiFetch(
        `/projects/${encodeURIComponent(projectId)}/editor-state`,
        {
          method: "PUT",
          body: JSON.stringify({ guidance: state.guidance }),
        },
      ).catch(() => undefined);
    }, 800);
  }, [state, projectId]);

  const updateGuidance = useCallback(
    (patch: Partial<ProjectGenerationGuidance>) => {
      setState((prev) => ({
        ...prev,
        guidance: {
          ...prev.guidance,
          ...patch,
          updatedAt: new Date().toISOString(),
        },
      }));
    },
    [],
  );

  /**
   * 用途：提交定向修订；更新 history，并向调用方返回 revisedContent。
   */
  const submitRevise = useCallback(
    async (input: {
      stage: FeedbackStage;
      message: string;
      preserveStructure: boolean;
      targetId?: string;
      targetLabel?: string;
      baseContent?: string;
    }): Promise<ReviseSubmitResult> => {
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

      const artifactId = input.targetId || input.stage || "default";

      try {
        const result = await apiFetch<ReviseApiResult>(
          `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/revise`,
          {
            method: "POST",
            body: JSON.stringify({
              stage: input.stage,
              message: input.message,
              preserveStructure: input.preserveStructure,
              baseContent: input.baseContent,
              targetId: input.targetId,
              targetLabel: input.targetLabel,
              guidance: {
                targetWordCount: state.guidance.targetWordCount,
                chapterFocus: state.guidance.chapterFocus,
                formatRequirements: state.guidance.formatRequirements,
                extraRequirements: state.guidance.extraRequirements,
              },
            }),
          },
        );

        const summary =
          result.resultSummary ||
          (result.model
            ? `已由 ${result.model} 完成定向修订`
            : "已完成定向修订");

        setState((prev) => ({
          ...prev,
          history: prev.history.map((h) =>
            h.id === id
              ? {
                  ...h,
                  id: result.id || h.id,
                  status: (result.status === "failed"
                    ? "failed"
                    : "applied") as AiFeedbackRecord["status"],
                  resultSummary: summary,
                }
              : h,
          ),
        }));

        return {
          ok: result.status !== "failed",
          resultSummary: summary,
          revisedContent: result.revisedContent,
        };
      } catch (err) {
        const apiMsg =
          (err as { message?: string })?.message || "修订请求失败";
        setState((prev) => ({
          ...prev,
          history: prev.history.map((h) =>
            h.id === id
              ? {
                  ...h,
                  status: "failed" as const,
                  resultSummary: `修订失败：${apiMsg.slice(0, 200)}`,
                }
              : h,
          ),
        }));
        return { ok: false, error: apiMsg };
      }
    },
    [projectId, state.guidance],
  );

  return {
    guidance: state.guidance,
    history: state.history,
    updateGuidance,
    submitRevise,
  };
}
