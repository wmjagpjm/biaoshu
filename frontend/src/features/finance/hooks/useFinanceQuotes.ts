/**
 * 模块：P10B 财务报价数据 Hook
 * 用途：加载列表与明细；维护加载/错误/空状态；仅走专用财务端点。
 * 对接：financeApi；FinanceQuotePage。
 * 二次开发：禁止用通用项目/编辑器接口降级；错误文案固定中文，不透传敏感 detail。
 */

import { useCallback, useEffect, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import {
  fetchFinanceBusinessBidDetail,
  fetchFinanceBusinessBids,
} from "../lib/financeApi";
import type {
  FinanceBusinessBidDetail,
  FinanceBusinessBidSummary,
} from "../types";

/** 用途：把接口异常映射为固定中文提示，避免路径/密钥进入界面。 */
function toSafeListError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，财务报价列表暂时不可用";
  if (status === 403) return "当前账号无权查看财务报价";
  return "财务报价列表加载失败，请稍后重试";
}

/** 用途：明细异常映射；404 单独提示空项目。 */
function toSafeDetailError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，报价明细暂时不可用";
  if (status === 404) return "项目不存在或不可访问";
  if (status === 403) return "当前账号无权查看该报价明细";
  return "报价明细加载失败，请稍后重试";
}

/**
 * 用途：财务报价列表 + 选中项目明细的只读状态机。
 * 对接：FinanceQuotePage。
 */
export function useFinanceQuotes() {
  const [items, setItems] = useState<FinanceBusinessBidSummary[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FinanceBusinessBidDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const refreshList = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const list = await fetchFinanceBusinessBids();
      setItems(list);
      // 列表刷新后：若当前选中项已不在列表中则清空
      setSelectedId((prev) => {
        if (!prev) return prev;
        return list.some((x) => x.projectId === prev) ? prev : null;
      });
    } catch (err) {
      setItems([]);
      setListError(toSafeListError(err));
      setSelectedId(null);
      setDetail(null);
      setDetailError(null);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const selectProject = useCallback((projectId: string) => {
    setSelectedId(projectId);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(false);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }

    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);

    void (async () => {
      try {
        const next = await fetchFinanceBusinessBidDetail(selectedId);
        if (cancelled) return;
        setDetail(next);
        setDetailError(null);
      } catch (err) {
        if (cancelled) return;
        setDetail(null);
        setDetailError(toSafeDetailError(err));
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  return {
    items,
    listLoading,
    listError,
    selectedId,
    detail,
    detailLoading,
    detailError,
    refreshList,
    selectProject,
    clearSelection,
  };
}
