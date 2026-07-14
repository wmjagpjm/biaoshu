/**
 * 模块：P10J 财务个人成本变更记录 Hook
 * 用途：初始/刷新仅 GET 变更列表；加载与错误固定中文；结果仅存 React 内存。
 * 对接：financeCostChangeEventsApi；FinanceCostChangeEventsPage。
 * 二次开发：禁止 localStorage/sessionStorage/URL 参数；禁止模块全局 Promise 跨实例复用。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import { fetchFinanceCostChangeEvents } from "../lib/financeCostChangeEventsApi";
import type { FinanceCostChangeEventsResponse } from "../types";

/** 失败固定文案；不得拼接后端 detail/code/路径/SECRET。 */
export const FINANCE_COST_CHANGE_EVENTS_ERROR_MESSAGE =
  "成本变更记录加载失败，请稍后重试";

/**
 * 模块：toSafeError
 * 用途：任意接口异常映射为固定中文，不透传 detail。
 * 对接：useFinanceCostChangeEvents。
 * 二次开发：禁止回显 ApiError.message、code、entryId 或路径。
 */
function toSafeError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，成本变更记录暂时不可用";
  if (status === 403) return "当前账号无权查看成本变更记录";
  return FINANCE_COST_CHANGE_EVENTS_ERROR_MESSAGE;
}

/**
 * 模块：useFinanceCostChangeEvents
 * 用途：成本变更列表只读状态机（初始加载 + 手动刷新）。
 * 对接：FinanceCostChangeEventsPage。
 * 二次开发：仅展示服务端投影；禁止乐观伪造条目；禁止模块级 Promise 跨实例复用。
 *
 * 请求去重：组件实例内 pendingRef 共享同一飞行请求，
 * React Strict Mode cleanup/setup 复用 in-flight Promise（首次挂载严格 1 次 GET）；
 * settle 后清空，手动刷新再发恰好 1 次（累计严格 2 次）；真实卸载后新实例不得跨会话复用。
 */
export function useFinanceCostChangeEvents() {
  const [data, setData] = useState<FinanceCostChangeEventsResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  /**
   * 组件实例内共享同一飞行请求：Strict Mode cleanup/setup 复用 in-flight Promise，
   * settle 后清空，手动刷新再发恰好 1 次；真实卸载后新实例不得跨会话复用。
   */
  const pendingRef = useRef<Promise<FinanceCostChangeEventsResponse> | null>(
    null,
  );

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    if (!pendingRef.current) {
      const request = fetchFinanceCostChangeEvents().finally(() => {
        if (pendingRef.current === request) {
          pendingRef.current = null;
        }
      });
      pendingRef.current = request;
    }

    const inflight = pendingRef.current;
    void (async () => {
      try {
        const next = await inflight;
        if (cancelled) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setData(null);
        setError(toSafeError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  return {
    data,
    loading,
    error,
    reload,
  };
}
