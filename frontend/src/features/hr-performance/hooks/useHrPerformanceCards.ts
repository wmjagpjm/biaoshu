/**
 * 模块：P10H 人员业绩素材卡 Hook
 * 用途：列表/详情加载；创建与更新后强制重读；按 selectedId/requestSeq 绑定并丢弃过期响应。
 * 对接：hrPerformanceApi；HrPerformanceCardsPage；P10A apiFetch CSRF。
 * 二次开发：禁止乐观伪造成功、禁止 localStorage/sessionStorage、禁止 URL 查询参数。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import {
  createHrPerformanceCard,
  fetchHrPerformanceCard,
  fetchHrPerformanceCards,
  updateHrPerformanceCard,
} from "../lib/hrPerformanceApi";
import type {
  HrPerformanceCardCreateBody,
  HrPerformanceCardDetail,
  HrPerformanceCardSummary,
  HrPerformanceCardUpdateBody,
} from "../types";

/** 用途：列表加载失败 → 固定中文，不透传后端 detail。 */
function toSafeListError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，人员业绩列表暂时不可用";
  if (status === 403) return "当前账号无权查看人员业绩";
  return "人员业绩列表加载失败，请稍后重试";
}

/** 用途：详情加载失败 → 固定中文，不回显路径/ID。 */
function toSafeDetailError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，详情暂时不可用";
  if (status === 404) return "该业绩卡不存在或不可访问";
  if (status === 403) return "当前账号无权查看该业绩卡";
  return "业绩卡详情加载失败，请稍后重试";
}

/** 用途：写入失败 → 固定中文，不回显姓名/项目/摘要/备注/路径/后端 detail。 */
function toSafeWriteError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，操作未完成";
  if (status === 404) return "该业绩卡不存在或不可访问";
  if (status === 403) return "当前账号无权修改人员业绩";
  if (status === 422) return "提交内容不符合要求，请检查后重试";
  return "操作失败，请稍后重试";
}

/** 用途：表单输入（与页面控件绑定；仅 UX 预检）。 */
export type HrPerformanceFormInput = {
  personName: string;
  projectName: string;
  projectRole: string;
  /** 年份文本；空串表示 null */
  completedYear: string;
  performanceSummary: string;
  remark: string;
  isActive: boolean;
};

/** 与加载/失败时 cardId 绑定的详情结果，渲染时须比对当前选中项。 */
type BoundDetail = {
  cardId: string;
  data: HrPerformanceCardDetail | null;
  error: string | null;
};

/**
 * 模块：parseCompletedYearInput
 * 用途：将年份输入解析为 null 或 1900–2100 整数；非法返回错误。
 * 对接：buildHrPerformanceCreateBody / buildHrPerformanceUpdateBody。
 * 二次开发：禁止 Number 浮点估算；空串 → null；非整数不得发请求。
 */
export function parseCompletedYearInput(
  raw: string,
):
  | { ok: true; value: number | null }
  | { ok: false; error: string } {
  const t = String(raw ?? "").trim();
  if (!t) {
    return { ok: true, value: null };
  }
  if (!/^-?\d+$/.test(t)) {
    return { ok: false, error: "完成年份须为 1900–2100 的整数" };
  }
  const n = Number(t);
  if (!Number.isInteger(n) || n < 1900 || n > 2100) {
    return { ok: false, error: "完成年份须为 1900–2100 的整数" };
  }
  return { ok: true, value: n };
}

/**
 * 模块：buildHrPerformanceCreateBody
 * 用途：校验表单并生成创建体；非法时不发请求。
 * 对接：create 入口。
 * 二次开发：服务端仍是唯一权威；此处仅 UX 预检。
 */
export function buildHrPerformanceCreateBody(
  input: HrPerformanceFormInput,
):
  | { ok: true; body: HrPerformanceCardCreateBody }
  | { ok: false; error: string } {
  const personName = String(input.personName ?? "").trim();
  if (!personName) {
    return { ok: false, error: "请输入人员姓名" };
  }
  if (personName.length > 80) {
    return { ok: false, error: "人员姓名不能超过 80 个字符" };
  }
  const projectName = String(input.projectName ?? "").trim();
  if (!projectName) {
    return { ok: false, error: "请输入项目名称" };
  }
  if (projectName.length > 120) {
    return { ok: false, error: "项目名称不能超过 120 个字符" };
  }
  const projectRole = String(input.projectRole ?? "").trim();
  if (projectRole.length > 80) {
    return { ok: false, error: "项目角色不能超过 80 个字符" };
  }
  const yearParsed = parseCompletedYearInput(input.completedYear);
  if (!yearParsed.ok) return yearParsed;
  const performanceSummary = String(input.performanceSummary ?? "").trim();
  if (!performanceSummary) {
    return { ok: false, error: "请输入业绩摘要" };
  }
  if (performanceSummary.length > 1000) {
    return { ok: false, error: "业绩摘要不能超过 1000 个字符" };
  }
  const remark = String(input.remark ?? "");
  if (remark.length > 500) {
    return { ok: false, error: "备注不能超过 500 个字符" };
  }
  if (typeof input.isActive !== "boolean") {
    return { ok: false, error: "启用状态不合法" };
  }
  return {
    ok: true,
    body: {
      personName,
      projectName,
      projectRole,
      completedYear: yearParsed.value,
      performanceSummary,
      remark,
      isActive: input.isActive,
    },
  };
}

/**
 * 模块：buildHrPerformanceUpdateBody
 * 用途：校验表单并生成更新体。
 * 对接：update 入口。
 * 二次开发：与创建共用字段规则；禁止发送非布尔 isActive。
 */
export function buildHrPerformanceUpdateBody(
  input: HrPerformanceFormInput,
):
  | { ok: true; body: HrPerformanceCardUpdateBody }
  | { ok: false; error: string } {
  const built = buildHrPerformanceCreateBody(input);
  if (!built.ok) return built;
  return {
    ok: true,
    body: {
      personName: built.body.personName,
      projectName: built.body.projectName,
      projectRole: built.body.projectRole,
      completedYear: built.body.completedYear ?? null,
      performanceSummary: built.body.performanceSummary,
      remark: built.body.remark,
      isActive: built.body.isActive,
    },
  };
}

/**
 * 模块：useHrPerformanceCards
 * 用途：人员业绩列表 + 选中详情状态机（写后强制重读；切换绑定丢弃过期响应）。
 * 对接：HrPerformanceCardsPage。
 * 二次开发：仅展示 bound.cardId === selectedId 的 data/error；结果仅存 React 内存。
 */
export function useHrPerformanceCards() {
  const [items, setItems] = useState<HrPerformanceCardSummary[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bound, setBound] = useState<BoundDetail | null>(null);
  const [loadingFor, setLoadingFor] = useState<string | null>(null);
  const requestSeqRef = useRef(0);

  const [submitting, setSubmitting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);

  const [listReloadToken, setListReloadToken] = useState(0);
  const [detailReloadToken, setDetailReloadToken] = useState(0);

  const reloadList = useCallback(() => {
    setListReloadToken((n) => n + 1);
  }, []);

  const clearWriteError = useCallback(() => {
    setWriteError(null);
  }, []);

  // 列表加载：初始与重读仅 GET 摘要
  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    setListError(null);
    void (async () => {
      try {
        const next = await fetchHrPerformanceCards();
        if (cancelled) return;
        setItems(next);
        setListError(null);
      } catch (err) {
        if (cancelled) return;
        setItems([]);
        setListError(toSafeListError(err));
      } finally {
        if (!cancelled) setListLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [listReloadToken]);

  // 详情：仅在选中后 GET；按 selectedId/requestSeq 绑定；切换立即丢弃可见旧详情
  useEffect(() => {
    if (!selectedId) {
      setLoadingFor(null);
      return;
    }

    const requestedId = selectedId;
    const seq = ++requestSeqRef.current;
    setLoadingFor(requestedId);
    // 不保留旧 cardId 的可见结果（渲染层再按 cardId 过滤）
    setBound((prev) =>
      prev?.cardId === requestedId
        ? { cardId: requestedId, data: null, error: null }
        : prev,
    );

    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchHrPerformanceCard(requestedId);
        if (cancelled || seq !== requestSeqRef.current) return;
        setBound({ cardId: requestedId, data: next, error: null });
      } catch (err) {
        if (cancelled || seq !== requestSeqRef.current) return;
        setBound({
          cardId: requestedId,
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
  }, [selectedId, detailReloadToken]);

  const selectCard = useCallback((cardId: string | null) => {
    setWriteError(null);
    // 切换时立即递增序列并进入加载态；渲染层按 cardId 过滤，不依赖后置 setState 清空
    requestSeqRef.current += 1;
    setLoadingFor(cardId);
    setSelectedId(cardId);
  }, []);

  /**
   * 模块：createCard
   * 用途：创建成功后重读列表；若返回 id 则选中并触发详情 GET。
   * 对接：POST /hr/performance-cards。
   * 二次开发：禁止乐观写入 items/detail。
   */
  const createCard = useCallback(
    async (input: HrPerformanceFormInput): Promise<boolean> => {
      if (submitting) return false;
      const built = buildHrPerformanceCreateBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        const created = await createHrPerformanceCard(built.body);
        setListReloadToken((n) => n + 1);
        if (created?.id) {
          requestSeqRef.current += 1;
          setSelectedId(created.id);
          setDetailReloadToken((n) => n + 1);
        } else {
          setDetailReloadToken((n) => n + 1);
        }
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [submitting],
  );

  /**
   * 模块：updateCard
   * 用途：更新成功后重读列表与当前详情。
   * 对接：PATCH /hr/performance-cards/{cardId}。
   * 二次开发：禁止乐观伪造字段。
   */
  const updateCard = useCallback(
    async (cardId: string, input: HrPerformanceFormInput): Promise<boolean> => {
      if (submitting) return false;
      const built = buildHrPerformanceUpdateBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        await updateHrPerformanceCard(cardId, built.body);
        setListReloadToken((n) => n + 1);
        setDetailReloadToken((n) => n + 1);
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [submitting],
  );

  /**
   * 模块：setCardActive
   * 用途：仅切换启停布尔；成功后强制重读。
   * 对接：PATCH { isActive }。
   * 二次开发：isActive 必须为真实布尔。
   */
  const setCardActive = useCallback(
    async (cardId: string, isActive: boolean): Promise<boolean> => {
      if (submitting) return false;
      if (typeof isActive !== "boolean") {
        setWriteError("启用状态不合法");
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        await updateHrPerformanceCard(cardId, { isActive });
        setListReloadToken((n) => n + 1);
        setDetailReloadToken((n) => n + 1);
        return true;
      } catch (err) {
        setWriteError(toSafeWriteError(err));
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [submitting],
  );

  // 渲染守卫：只暴露属于当前 selectedId 的 data/error
  const visible: BoundDetail | null =
    selectedId && bound?.cardId === selectedId
      ? bound
      : selectedId
        ? { cardId: selectedId, data: null, error: null }
        : null;

  return {
    items,
    listLoading,
    listError,
    selectedId,
    detail: visible?.data ?? null,
    detailLoading: selectedId != null && loadingFor === selectedId,
    detailError: visible?.error ?? null,
    submitting,
    writeError,
    selectCard,
    clearWriteError,
    reloadList,
    createCard,
    updateCard,
    setCardActive,
  };
}
