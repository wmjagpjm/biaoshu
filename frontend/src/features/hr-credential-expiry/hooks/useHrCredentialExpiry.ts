/**
 * 模块：P10I 人员资质到期提示 Hook
 * 用途：初始/刷新仅 GET 到期摘要；加载与错误固定中文；结果仅存 React 内存。
 * 对接：hrCredentialExpiryApi；HrCredentialExpiryPage。
 * 二次开发：禁止 localStorage/sessionStorage/URL 参数；禁止客户端重算 state/daysRemaining。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import { fetchHrCredentialExpiry } from "../lib/hrCredentialExpiryApi";
import type { HrCredentialExpirySummary } from "../types";

/** 失败固定文案；不得拼接后端 detail/code/路径/SECRET。 */
export const HR_CREDENTIAL_EXPIRY_ERROR_MESSAGE =
  "人员资质到期提示加载失败，请稍后重试";

/**
 * 模块：toSafeError
 * 用途：任意接口异常映射为固定中文，不透传 detail。
 * 对接：useHrCredentialExpiry。
 * 二次开发：禁止回显 ApiError.message、code、人员或资质信息。
 */
function toSafeError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，人员资质到期提示暂时不可用";
  if (status === 403) return "当前账号无权查看人员资质到期提示";
  return HR_CREDENTIAL_EXPIRY_ERROR_MESSAGE;
}

/**
 * 模块：useHrCredentialExpiry
 * 用途：到期摘要只读状态机（初始加载 + 手动刷新）。
 * 对接：HrCredentialExpiryPage。
 * 二次开发：仅展示服务端投影；禁止乐观伪造计数或关注项；禁止模块级 Promise 跨实例复用。
 */
export function useHrCredentialExpiry() {
  const [data, setData] = useState<HrCredentialExpirySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  /**
   * 组件实例内共享同一飞行请求：Strict Mode cleanup/setup 复用 in-flight Promise，
   * settle 后清空，手动刷新再发恰好 1 次；真实卸载后新实例不得跨会话复用。
   */
  const pendingRef = useRef<Promise<HrCredentialExpirySummary> | null>(null);

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    if (!pendingRef.current) {
      const request = fetchHrCredentialExpiry().finally(() => {
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
