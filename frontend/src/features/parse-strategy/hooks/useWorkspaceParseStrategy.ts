/**
 * 模块：工作空间解析策略决策 Hook
 * 用途：点击解析时 refresh 读取策略；仅 React 内存保存本次结果。
 * 对接：parseStrategyApi；技术标/商务标解析入口；ParseStrategyChoiceDialog。
 * 二次开发：禁止 localStorage/sessionStorage；失败固定中文，不得回显后端 detail。
 */

import { useCallback, useState } from "react";
import {
  fetchWorkspaceParseStrategy,
  type WorkspaceParseStrategy,
} from "../lib/parseStrategyApi";

/** 失败固定文案（契约 §4）；不得拼接后端 detail/code/URL。 */
export const PARSE_STRATEGY_ERROR_MESSAGE =
  "暂时无法读取解析策略，请稍后重试";

export type ParseStrategyRefreshOk = {
  ok: true;
  strategy: WorkspaceParseStrategy;
};

export type ParseStrategyRefreshFail = {
  ok: false;
  error: string;
};

export type ParseStrategyRefreshResult =
  | ParseStrategyRefreshOk
  | ParseStrategyRefreshFail;

/**
 * 模块：toSafeStrategyError
 * 用途：任意异常映射为固定中文，避免路径/密钥进入界面。
 * 对接：useWorkspaceParseStrategy.refresh。
 * 二次开发：禁止回显 ApiError.message 或 detail。
 */
function toSafeStrategyError(_err: unknown): string {
  void _err;
  return PARSE_STRATEGY_ERROR_MESSAGE;
}

/**
 * 模块：useWorkspaceParseStrategy
 * 用途：按需刷新工作空间解析策略；loading/error/strategy 仅存内存。
 * 对接：TechnicalPlanWorkspace / BusinessBidWorkspace 的 handleParse。
 * 二次开发：每次解析动作必须 refresh()，不得复用旧策略缓存决定动作。
 */
export function useWorkspaceParseStrategy() {
  const [strategy, setStrategy] = useState<WorkspaceParseStrategy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<ParseStrategyRefreshResult> => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchWorkspaceParseStrategy();
      setStrategy(next);
      setError(null);
      return { ok: true, strategy: next };
    } catch (err) {
      const message = toSafeStrategyError(err);
      setStrategy(null);
      setError(message);
      return { ok: false, error: message };
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    strategy,
    loading,
    error,
    refresh,
  };
}
