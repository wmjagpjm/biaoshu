/**
 * 模块：本地标讯库与国能计划追踪 Hook
 * 用途：加载、保存、删除工作空间标讯；维护国能追踪仪表盘、计划导入、同步轮询与人工接受。
 * 对接：/api/opportunities；/api/opportunity-watch/*；BidOpportunityPage。
 * 二次开发：前端只访问本机 /api；禁止直连国能站点、拼接 URL/Cookie/Token 或自动立项。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type {
  BidOpportunity,
  BidOpportunityDraft,
  OpportunityImportResult,
  OpportunityWatchAcceptResult,
  OpportunityWatchDashboard,
  OpportunityWatchPlanImportResult,
  OpportunityWatchSyncRun,
} from "../types";

const TERMINAL_RUN_STATUSES = new Set([
  "succeeded",
  "partial",
  "failed",
]);

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

function delay(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

/**
 * 用途：维护标讯列表、国能追踪状态和接口错误，并封装立项与人工接受。
 * 对接：BidOpportunityPage；shared/lib/api.ts；/api/opportunities 与 /api/opportunity-watch。
 * 二次开发：追踪状态独立于本地标讯列表；同步仅轮询本空间 runs/{runId}。
 */
export function useOpportunities() {
  const [items, setItems] = useState<BidOpportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [watchDashboard, setWatchDashboard] =
    useState<OpportunityWatchDashboard | null>(null);
  const [watchLoading, setWatchLoading] = useState(true);
  const [watchError, setWatchError] = useState<string | null>(null);
  const [watchBusy, setWatchBusy] = useState(false);
  const [watchSyncing, setWatchSyncing] = useState(false);
  const [activeWatchRun, setActiveWatchRun] =
    useState<OpportunityWatchSyncRun | null>(null);
  const [watchImportResult, setWatchImportResult] =
    useState<OpportunityWatchPlanImportResult | null>(null);
  const pollCancelledRef = useRef(false);

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

  const refreshWatchDashboard = useCallback(async () => {
    setWatchLoading(true);
    try {
      const data = await apiFetch<OpportunityWatchDashboard>(
        "/opportunity-watch/dashboard",
      );
      setWatchDashboard(data);
      setWatchError(null);
      return data;
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "加载国能追踪面板失败";
      setWatchError(message);
      throw reason;
    } finally {
      setWatchLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refreshWatchDashboard().catch(() => {
      /* 错误已写入 watchError */
    });
  }, [refreshWatchDashboard]);

  useEffect(() => {
    return () => {
      pollCancelledRef.current = true;
    };
  }, []);

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

  const importWatchPlans = useCallback(
    async (file: File): Promise<OpportunityWatchPlanImportResult> => {
      setWatchBusy(true);
      try {
        const form = new FormData();
        form.append("file", file);
        const result = await apiFetch<OpportunityWatchPlanImportResult>(
          "/opportunity-watch/plans/import",
          { method: "POST", body: form },
        );
        setWatchImportResult(result);
        await refreshWatchDashboard();
        setWatchError(null);
        return result;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "导入招标计划失败";
        setWatchError(message);
        throw reason;
      } finally {
        setWatchBusy(false);
      }
    },
    [refreshWatchDashboard],
  );

  const startWatchSync = useCallback(async () => {
    setWatchBusy(true);
    setWatchSyncing(true);
    pollCancelledRef.current = false;
    try {
      const accepted = await apiFetch<{ runId: string }>(
        "/opportunity-watch/sync",
        { method: "POST" },
      );
      let run = await apiFetch<OpportunityWatchSyncRun>(
        `/opportunity-watch/runs/${encodeURIComponent(accepted.runId)}`,
      );
      setActiveWatchRun(run);

      while (!TERMINAL_RUN_STATUSES.has(run.status) && !pollCancelledRef.current) {
        await delay(400);
        if (pollCancelledRef.current) break;
        run = await apiFetch<OpportunityWatchSyncRun>(
          `/opportunity-watch/runs/${encodeURIComponent(accepted.runId)}`,
        );
        setActiveWatchRun(run);
      }

      await refreshWatchDashboard();
      setWatchError(null);
      return run;
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "同步国能 e 招失败";
      setWatchError(message);
      throw reason;
    } finally {
      setWatchSyncing(false);
      setWatchBusy(false);
    }
  }, [refreshWatchDashboard]);

  const acceptWatchHit = useCallback(
    async (hitId: string): Promise<OpportunityWatchAcceptResult> => {
      setWatchBusy(true);
      try {
        const result = await apiFetch<OpportunityWatchAcceptResult>(
          `/opportunity-watch/hits/${encodeURIComponent(hitId)}/accept`,
          { method: "POST" },
        );
        await Promise.all([refresh(), refreshWatchDashboard()]);
        setWatchError(null);
        return result;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "加入本地标讯失败";
        setWatchError(message);
        throw reason;
      } finally {
        setWatchBusy(false);
      }
    },
    [refresh, refreshWatchDashboard],
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
    watchDashboard,
    watchLoading,
    watchError,
    watchBusy,
    watchSyncing,
    activeWatchRun,
    watchImportResult,
    refreshWatchDashboard,
    importWatchPlans,
    startWatchSync,
    acceptWatchHit,
  };
}
