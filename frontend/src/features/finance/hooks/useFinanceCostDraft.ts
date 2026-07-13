/**
 * 模块：P10C 财务成本草案 Hook
 * 用途：加载成本草案；新建/编辑/删除后强制重新 GET；错误固定中文且不透传敏感 detail。
 * 对接：financeApi 成本四端点；FinanceQuotePage；P10A apiFetch CSRF。
 * 二次开发：禁止乐观伪造成功、禁止浏览器持久化成本/毛利/备注；服务端为唯一权威。
 */

import { useCallback, useEffect, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import {
  createFinanceCostEntry,
  deleteFinanceCostEntry,
  fetchFinanceCostDraft,
  updateFinanceCostEntry,
  yuanTextToFen,
} from "../lib/financeApi";
import type {
  FinanceCostCategory,
  FinanceCostDraft,
  FinanceCostEntryCreateBody,
  FinanceCostEntryUpdateBody,
} from "../types";

/** 用途：成本草案加载失败 → 固定中文。 */
function toSafeDraftError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，成本草案暂时不可用";
  if (status === 404) return "项目不存在或不可访问";
  if (status === 403) return "当前账号无权查看成本草案";
  return "成本草案加载失败，请稍后重试";
}

/** 用途：写入失败 → 固定中文，不回显金额/备注/后端 detail。 */
function toSafeWriteError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，操作未完成";
  if (status === 404) return "条目不存在或不可访问";
  if (status === 403) return "当前账号无权修改成本草案";
  if (status === 422) return "提交内容不符合要求，请检查后重试";
  return "操作失败，请稍后重试";
}

export type CostFormInput = {
  category: FinanceCostCategory;
  name: string;
  /** 元，最多两位小数的纯文本 */
  amountYuanText: string;
  remark: string;
};

/**
 * 用途：校验表单并生成写入体；非法金额不发请求。
 * 对接：create / update 入口。
 */
export function buildCostWriteBody(
  input: CostFormInput,
):
  | { ok: true; body: FinanceCostEntryCreateBody }
  | { ok: false; error: string } {
  const name = String(input.name ?? "").trim();
  if (!name) {
    return { ok: false, error: "请输入成本名称" };
  }
  if (name.length > 120) {
    return { ok: false, error: "成本名称不能超过 120 个字符" };
  }
  const category = input.category;
  if (
    category !== "labor" &&
    category !== "material" &&
    category !== "service" &&
    category !== "other"
  ) {
    return { ok: false, error: "请选择成本类别" };
  }
  const amount = yuanTextToFen(input.amountYuanText);
  if (!amount.ok) {
    return amount;
  }
  const remark = String(input.remark ?? "");
  if (remark.length > 500) {
    return { ok: false, error: "备注不能超过 500 个字符" };
  }
  return {
    ok: true,
    body: {
      category,
      name,
      amountFen: amount.fen,
      remark,
    },
  };
}

/**
 * 用途：选定项目下的成本草案状态机（读 + 写后刷新）。
 * 对接：FinanceQuotePage。
 */
export function useFinanceCostDraft(projectId: string | null) {
  const [draft, setDraft] = useState<FinanceCostDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  /** 用于强制成功写后重新拉取（避免仅依赖 projectId）。 */
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!projectId) {
      setDraft(null);
      setError(null);
      setLoading(false);
      setWriteError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setDraft(null);
    setWriteError(null);

    void (async () => {
      try {
        const next = await fetchFinanceCostDraft(projectId);
        if (cancelled) return;
        setDraft(next);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setDraft(null);
        setError(toSafeDraftError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [projectId, reloadToken]);

  const clearWriteError = useCallback(() => {
    setWriteError(null);
  }, []);

  const createEntry = useCallback(
    async (input: CostFormInput): Promise<boolean> => {
      if (!projectId || submitting) return false;
      const built = buildCostWriteBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        await createFinanceCostEntry(projectId, built.body);
        // 成功后必须重新加载草案，不可乐观伪造
        setReloadToken((n) => n + 1);
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [projectId, submitting],
  );

  const updateEntry = useCallback(
    async (entryId: string, input: CostFormInput): Promise<boolean> => {
      if (!projectId || submitting) return false;
      const built = buildCostWriteBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      const body: FinanceCostEntryUpdateBody = {
        category: built.body.category,
        name: built.body.name,
        amountFen: built.body.amountFen,
        remark: built.body.remark,
      };
      setSubmitting(true);
      setWriteError(null);
      try {
        await updateFinanceCostEntry(projectId, entryId, body);
        setReloadToken((n) => n + 1);
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [projectId, submitting],
  );

  const removeEntry = useCallback(
    async (entryId: string): Promise<boolean> => {
      if (!projectId || submitting) return false;
      setSubmitting(true);
      setWriteError(null);
      try {
        await deleteFinanceCostEntry(projectId, entryId);
        setReloadToken((n) => n + 1);
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [projectId, submitting],
  );

  return {
    draft,
    loading,
    error,
    submitting,
    writeError,
    reload,
    clearWriteError,
    createEntry,
    updateEntry,
    removeEntry,
  };
}
