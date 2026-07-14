/**
 * 模块：P10G 投标人项目级合规统计 Hook
 * 用途：初始只读选择器；选中后按需读详情；按 projectId 绑定结果并作废过期响应。
 * 对接：bidderProjectComplianceApi；BidderProjectCompliancePage。
 * 二次开发：禁止 localStorage/sessionStorage/URL 查询参数；禁止预取详情或 P10E 聚合。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import {
  fetchBidderProjectComplianceDetail,
  fetchBidderProjectComplianceProjects,
} from "../lib/bidderProjectComplianceApi";
import type {
  BidderProjectComplianceDetail,
  BidderProjectComplianceProjectItem,
} from "../types";

/** 失败固定文案（契约 §4）；不得拼接后端 detail/code/路径/ID。 */
export const BIDDER_PROJECT_COMPLIANCE_ERROR_MESSAGE =
  "暂时无法读取项目合规统计";

/** 列表失败固定文案。 */
export const BIDDER_PROJECT_LIST_ERROR_MESSAGE =
  "暂时无法读取技术标项目列表";

/**
 * 模块：toSafeListError
 * 用途：任意列表接口异常映射为固定中文。
 * 对接：useBidderProjectCompliance。
 * 二次开发：禁止回显 ApiError.message 或 detail。
 */
function toSafeListError(_err: unknown): string {
  void _err;
  return BIDDER_PROJECT_LIST_ERROR_MESSAGE;
}

/**
 * 模块：toSafeDetailError
 * 用途：任意详情接口异常映射为固定中文；404 亦脱敏。
 * 对接：useBidderProjectCompliance。
 * 二次开发：禁止回显路径参数、projectId 或后端 code。
 */
function toSafeDetailError(_err: unknown): string {
  void _err;
  const status =
    _err && typeof _err === "object" && "status" in _err
      ? (_err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，项目合规统计暂时不可用";
  return BIDDER_PROJECT_COMPLIANCE_ERROR_MESSAGE;
}

/** 与加载/失败时 projectId 绑定的详情结果，渲染时须比对当前选中项。 */
type BoundDetail = {
  projectId: string;
  data: BidderProjectComplianceDetail | null;
  error: string | null;
};

/**
 * 模块：useBidderProjectCompliance
 * 用途：选择器 + 按需单项目统计状态机；切换时立即清空可见旧数据。
 * 对接：BidderProjectCompliancePage。
 * 二次开发：仅展示 bound.projectId === selectedId 的 data/error；
 *   未选择不得请求详情；结果仅存 React state。
 */
export function useBidderProjectCompliance() {
  const [projects, setProjects] = useState<BidderProjectComplianceProjectItem[]>(
    [],
  );
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bound, setBound] = useState<BoundDetail | null>(null);
  const [loadingFor, setLoadingFor] = useState<string | null>(null);
  /** 选中切换或新请求时递增，用于丢弃过期响应 */
  const requestSeqRef = useRef(0);

  const reloadProjects = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const res = await fetchBidderProjectComplianceProjects();
      const items = Array.isArray(res?.items) ? res.items : [];
      setProjects(items);
      setSelectedId((prev) => {
        if (!prev) return prev;
        return items.some((x) => x.id === prev) ? prev : null;
      });
    } catch (err) {
      setProjects([]);
      setListError(toSafeListError(err));
      setSelectedId(null);
      setBound(null);
      setLoadingFor(null);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadProjects();
  }, [reloadProjects]);

  const selectProject = useCallback((projectId: string) => {
    const id = String(projectId ?? "").trim();
    if (!id) return;
    // 切换时立即递增序列并清空加载标记；渲染层按 projectId 过滤，不依赖后置 setState
    requestSeqRef.current += 1;
    setLoadingFor(null);
    setSelectedId(id);
  }, []);

  const clearSelection = useCallback(() => {
    requestSeqRef.current += 1;
    setSelectedId(null);
    setBound(null);
    setLoadingFor(null);
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setLoadingFor(null);
      return;
    }

    const requestedId = selectedId;
    const seq = ++requestSeqRef.current;
    setLoadingFor(requestedId);
    // 仅清除当前请求项目的可见结果；绑定 projectId，避免跨项目写入
    setBound((prev) =>
      prev?.projectId === requestedId
        ? { projectId: requestedId, data: null, error: null }
        : prev,
    );

    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchBidderProjectComplianceDetail(requestedId);
        if (cancelled || seq !== requestSeqRef.current) return;
        setBound({ projectId: requestedId, data: next, error: null });
      } catch (err) {
        if (cancelled || seq !== requestSeqRef.current) return;
        setBound({
          projectId: requestedId,
          data: null,
          error: toSafeDetailError(err),
        });
      } finally {
        if (!cancelled && seq === requestSeqRef.current) {
          setLoadingFor((cur) => (cur === requestedId ? null : cur));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // 渲染守卫：只暴露属于当前 selectedId 的 data/error（不依赖 effect 后置清空）
  const visible: BoundDetail | null =
    selectedId && bound?.projectId === selectedId
      ? bound
      : selectedId
        ? { projectId: selectedId, data: null, error: null }
        : null;

  return {
    projects,
    listLoading,
    listError,
    selectedId,
    detail: visible?.data ?? null,
    detailError: visible?.error ?? null,
    detailLoading: selectedId != null && loadingFor === selectedId,
    selectProject,
    clearSelection,
    reloadProjects,
  };
}
