/**
 * 模块：项目级生成约束反馈历史 + 定向修订
 * 用途：
 *   1. history 仍 localStorage 承载既有反馈语义
 *   2. submitRevise 调 POST revise，返回 revisedContent 供工作区预览/替换
 *   3. guidance 权威改由 useTechnicalPlanEditors 持有并 PUT；本 hook 只接收只读 guidance
 * 对接：页面传入的服务端权威 guidance；/projects/{id}/artifacts/{aid}/revise
 * 二次开发：禁止再发 editor-state GET/PUT；禁止从 localStorage guidance 水合成功内容；
 *       更新 history 时可保留旧对象无关字段，但旧 guidance 永不参与 UI/expected/CAS。
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  AiFeedbackRecord,
  FeedbackStage,
  ProjectFeedbackState,
  ProjectGenerationGuidance,
} from "../../../shared/types/aiFeedback";

const storageKey = (projectId: string) => `biaoshu.projectFeedback.${projectId}`;

/**
 * 用途：只加载 history；忽略旧 guidance 作为成功真值。
 * 对接：localStorage biaoshu.projectFeedback.{projectId}
 */
function loadHistoryOnly(projectId: string): AiFeedbackRecord[] {
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Partial<ProjectFeedbackState>;
    return Array.isArray(parsed.history) ? parsed.history : [];
  } catch {
    return [];
  }
}

/**
 * 用途：写回 history；保留旧对象无关字段，但不把 guidance 当权威。
 * 二次开发：不得写入 stateVersion；不得删除旧键。
 */
function saveHistory(projectId: string, history: AiFeedbackRecord[]) {
  let previous: Record<string, unknown> = {};
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      if (parsed && typeof parsed === "object") {
        previous = parsed;
      }
    }
  } catch {
    previous = {};
  }
  const next = {
    ...previous,
    projectId,
    history,
  };
  localStorage.setItem(storageKey(projectId), JSON.stringify(next));
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

/**
 * 用途：反馈历史 + revise；guidance 由技术主 hook 注入，只读用于 revise payload。
 * 对接：useTechnicalPlanEditors.guidance；页面必须先初始化 editors 再调用本 hook。
 */
export function useProjectGuidance(
  projectId: string,
  authoritativeGuidance: ProjectGenerationGuidance,
) {
  const [history, setHistory] = useState<AiFeedbackRecord[]>(() =>
    loadHistoryOnly(projectId),
  );

  useEffect(() => {
    setHistory(loadHistoryOnly(projectId));
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    saveHistory(projectId, history);
  }, [projectId, history]);

  /**
   * 用途：提交定向修订；更新 history，并向调用方返回 revisedContent。
   * 对接：权威 guidance 来自参数，不读 localStorage guidance。
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

      setHistory((prev) => [applying, ...prev]);

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
                targetWordCount: authoritativeGuidance.targetWordCount,
                chapterFocus: authoritativeGuidance.chapterFocus,
                formatRequirements: authoritativeGuidance.formatRequirements,
                extraRequirements: authoritativeGuidance.extraRequirements,
                kbEnabled: authoritativeGuidance.kbEnabled !== false,
                kbFolderIds: authoritativeGuidance.kbFolderIds ?? [],
              },
            }),
          },
        );

        const summary =
          result.resultSummary ||
          (result.model
            ? `已由 ${result.model} 完成定向修订`
            : "已完成定向修订");

        setHistory((prev) =>
          prev.map((h) =>
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
        );

        return {
          ok: result.status !== "failed",
          resultSummary: summary,
          revisedContent: result.revisedContent,
        };
      } catch (err) {
        const apiMsg =
          (err as { message?: string })?.message || "修订请求失败";
        setHistory((prev) =>
          prev.map((h) =>
            h.id === id
              ? {
                  ...h,
                  status: "failed" as const,
                  resultSummary: `修订失败：${apiMsg.slice(0, 200)}`,
                }
              : h,
          ),
        );
        return { ok: false, error: apiMsg };
      }
    },
    [projectId, authoritativeGuidance],
  );

  return {
    /** 只读镜像：页面展示/卡片请使用 editors.guidance */
    guidance: authoritativeGuidance,
    history,
    submitRevise,
  };
}
