/**
 * 模块：本地标讯库数据 Hook
 * 用途：加载、保存、删除工作空间标讯，并从有效标讯创建关联技术标项目。
 * 对接：/api/opportunities、/api/opportunities/{id}/projects、BidOpportunityPage。
 * 二次开发：外部同步应仅新增服务端导入任务；前端继续使用本 Hook 的本地 API，不得回退到 mock。
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  BidOpportunity,
  BidOpportunityDraft,
  OpportunityImportResult,
} from "../types";

function toPayload(draft: BidOpportunityDraft) {
  return {
    title: draft.title.trim(),
    buyer: draft.buyer.trim(),
    region: draft.region.trim() || "其他",
    budgetLabel: draft.budgetLabel.trim(),
    deadline: draft.deadline,
    tags: draft.tagsText
      .split(/[，,\n]/)
      .map((tag) => tag.trim())
      .filter(Boolean),
    summary: draft.summary.trim(),
    sourceLabel: draft.sourceLabel.trim() || "本地录入",
  };
}

/**
 * 用途：将标讯读模型转为可编辑草稿，并提供新增标讯的默认值。
 * 对接：BidOpportunityPage 的新增和编辑弹层。
 */
export function opportunityToDraft(
  opportunity?: BidOpportunity | null,
): BidOpportunityDraft {
  if (!opportunity) {
    return {
      title: "",
      buyer: "",
      region: "其他",
      budgetLabel: "",
      deadline: "",
      tagsText: "",
      summary: "",
      sourceLabel: "本地录入",
    };
  }
  return {
    title: opportunity.title,
    buyer: opportunity.buyer,
    region: opportunity.region,
    budgetLabel: opportunity.budgetLabel,
    deadline: opportunity.deadline,
    tagsText: opportunity.tags.join("，"),
    summary: opportunity.summary,
    sourceLabel: opportunity.sourceLabel,
  };
}

/**
 * 用途：维护标讯列表、写入状态和接口错误，并封装立项操作。
 * 对接：BidOpportunityPage；shared/lib/api.ts；/api/opportunities。
 * 二次开发：外部同步完成后仍通过后端 API 刷新，不在 Hook 中维护第二份数据源。
 */
export function useOpportunities() {
  const [items, setItems] = useState<BidOpportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await apiFetch<BidOpportunity[]>("/opportunities");
      setItems(Array.isArray(list) ? list : []);
      setError(null);
    } catch (reason) {
      setError((reason as { message?: string }).message || "加载标讯失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(
    async (draft: BidOpportunityDraft, opportunityId?: string) => {
      setSaving(true);
      try {
        const path = opportunityId
          ? `/opportunities/${encodeURIComponent(opportunityId)}`
          : "/opportunities";
        const item = await apiFetch<BidOpportunity>(path, {
          method: opportunityId ? "PATCH" : "POST",
          body: JSON.stringify(toPayload(draft)),
        });
        setItems((current) => {
          if (!opportunityId) return [item, ...current];
          return current.map((entry) => (entry.id === item.id ? item : entry));
        });
        setError(null);
        return item;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "保存标讯失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const remove = useCallback(async (opportunityId: string) => {
    setSaving(true);
    try {
      await apiFetch<void>(`/opportunities/${encodeURIComponent(opportunityId)}`, {
        method: "DELETE",
      });
      setItems((current) => current.filter((item) => item.id !== opportunityId));
      setError(null);
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "删除标讯失败";
      setError(message);
      throw reason;
    } finally {
      setSaving(false);
    }
  }, []);

  const importOpportunities = useCallback(
    async (file: File): Promise<OpportunityImportResult> => {
      setSaving(true);
      try {
        const form = new FormData();
        form.append("file", file);
        const result = await apiFetch<OpportunityImportResult>("/opportunities/import", {
          method: "POST",
          body: form,
        });
        await refresh();
        setError(null);
        return result;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "导入标讯失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [refresh],
  );

  const createProject = useCallback(
    async (opportunityId: string) => {
      setSaving(true);
      try {
        const project = await apiFetch<{ id: string }>(
          `/opportunities/${encodeURIComponent(opportunityId)}/projects`,
          { method: "POST", body: JSON.stringify({}) },
        );
        setError(null);
        return project;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "创建技术标项目失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  return {
    items,
    loading,
    saving,
    error,
    refresh,
    save,
    remove,
    importOpportunities,
    createProject,
  };
}
