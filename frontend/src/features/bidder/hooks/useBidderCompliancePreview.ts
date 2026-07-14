/**
 * 模块：P10E 投标人匿名合规预览 Hook
 * 用途：挂载时 GET 预览；维护加载/错误/数据态；错误固定中文脱敏。
 * 对接：bidderComplianceApi；BidderCompliancePreviewPage；P10A apiFetch。
 * 二次开发：禁止 localStorage/sessionStorage 持久化；禁止请求项目/财务/人力接口。
 */

import { useCallback, useEffect, useState } from "react";
import { fetchBidderCompliancePreview } from "../lib/bidderComplianceApi";
import type { BidderCompliancePreview } from "../types";

/** 失败固定文案（契约 §5）；不得拼接后端 detail/code/URL。 */
export const BIDDER_PREVIEW_ERROR_MESSAGE = "暂时无法读取匿名合规预览";

/**
 * 模块：toSafePreviewError
 * 用途：任意接口异常映射为固定中文，避免路径/密钥/矩阵内容进入界面。
 * 对接：useBidderCompliancePreview。
 * 二次开发：禁止回显 ApiError.message 或 detail。
 */
function toSafePreviewError(_err: unknown): string {
  void _err;
  return BIDDER_PREVIEW_ERROR_MESSAGE;
}

/**
 * 模块：useBidderCompliancePreview
 * 用途：匿名合规预览只读状态机（加载 / 就绪 / 空 / 失败）。
 * 对接：BidderCompliancePreviewPage。
 * 二次开发：结果仅存 React state；reload 仅重发同一 GET。
 */
export function useBidderCompliancePreview() {
  const [data, setData] = useState<BidderCompliancePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => {
    setReloadToken((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const next = await fetchBidderCompliancePreview();
        if (cancelled) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setData(null);
        setError(toSafePreviewError(err));
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
