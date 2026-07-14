/**
 * 模块：P10D 人员资质素材卡 Hook
 * 用途：列表/详情加载；创建与更新后强制重读列表与当前详情；错误固定中文脱敏。
 * 对接：hrCredentialApi；HrCredentialCardsPage；P10A apiFetch CSRF。
 * 二次开发：禁止乐观伪造成功、禁止 localStorage/sessionStorage 持久化卡片数据。
 */

import { useCallback, useEffect, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import {
  createHrCredentialCard,
  fetchHrCredentialCard,
  fetchHrCredentialCards,
  updateHrCredentialCard,
} from "../lib/hrCredentialApi";
import type {
  HrCredentialCardCreateBody,
  HrCredentialCardDetail,
  HrCredentialCardSummary,
  HrCredentialCardUpdateBody,
  HrCredentialCategory,
} from "../types";

/** 用途：列表加载失败 → 固定中文，不透传后端 detail。 */
function toSafeListError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，人员资质列表暂时不可用";
  if (status === 403) return "当前账号无权查看人员资质";
  return "人员资质列表加载失败，请稍后重试";
}

/** 用途：详情加载失败 → 固定中文。 */
function toSafeDetailError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，详情暂时不可用";
  if (status === 404) return "该资质卡不存在或不可访问";
  if (status === 403) return "当前账号无权查看该资质卡";
  return "资质卡详情加载失败，请稍后重试";
}

/** 用途：写入失败 → 固定中文，不回显姓名/备注/路径/后端 detail。 */
function toSafeWriteError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，操作未完成";
  if (status === 404) return "该资质卡不存在或不可访问";
  if (status === 403) return "当前账号无权修改人员资质";
  if (status === 422) return "提交内容不符合要求，请检查后重试";
  return "操作失败，请稍后重试";
}

/** 用途：表单输入（与页面控件绑定；仅 UX 预检）。 */
export type HrCardFormInput = {
  personName: string;
  category: HrCredentialCategory;
  credentialName: string;
  level: string;
  /** ISO 日期 YYYY-MM-DD 或空串 */
  validUntil: string;
  remark: string;
  isActive: boolean;
};

const CATEGORIES: HrCredentialCategory[] = [
  "professional",
  "safety",
  "performance",
  "other",
];

/**
 * 用途：校验表单并生成创建体；非法时不发请求。
 * 对接：create 入口。
 */
export function buildHrCreateBody(
  input: HrCardFormInput,
):
  | { ok: true; body: HrCredentialCardCreateBody }
  | { ok: false; error: string } {
  const personName = String(input.personName ?? "").trim();
  if (!personName) {
    return { ok: false, error: "请输入人员姓名" };
  }
  if (personName.length > 80) {
    return { ok: false, error: "人员姓名不能超过 80 个字符" };
  }
  const category = input.category;
  if (!CATEGORIES.includes(category)) {
    return { ok: false, error: "请选择资质类别" };
  }
  const credentialName = String(input.credentialName ?? "").trim();
  if (!credentialName) {
    return { ok: false, error: "请输入资质名称" };
  }
  if (credentialName.length > 120) {
    return { ok: false, error: "资质名称不能超过 120 个字符" };
  }
  const level = String(input.level ?? "").trim();
  if (level.length > 80) {
    return { ok: false, error: "级别不能超过 80 个字符" };
  }
  const validRaw = String(input.validUntil ?? "").trim();
  let validUntil: string | null = null;
  if (validRaw) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(validRaw)) {
      return { ok: false, error: "有效期须为 YYYY-MM-DD 格式" };
    }
    validUntil = validRaw;
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
      category,
      credentialName,
      level,
      validUntil,
      remark,
      isActive: input.isActive,
    },
  };
}

/**
 * 用途：校验表单并生成更新体。
 * 对接：update 入口。
 */
export function buildHrUpdateBody(
  input: HrCardFormInput,
):
  | { ok: true; body: HrCredentialCardUpdateBody }
  | { ok: false; error: string } {
  const built = buildHrCreateBody(input);
  if (!built.ok) return built;
  return {
    ok: true,
    body: {
      personName: built.body.personName,
      category: built.body.category,
      credentialName: built.body.credentialName,
      level: built.body.level,
      validUntil: built.body.validUntil ?? null,
      remark: built.body.remark,
      isActive: built.body.isActive,
    },
  };
}

/**
 * 用途：人员资质列表 + 选中详情状态机（写后强制重读）。
 * 对接：HrCredentialCardsPage。
 */
export function useHrCredentialCards() {
  const [items, setItems] = useState<HrCredentialCardSummary[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<HrCredentialCardDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

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

  // 列表加载
  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    setListError(null);
    void (async () => {
      try {
        const next = await fetchHrCredentialCards();
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

  // 详情：仅在选中后 GET；切换/写后强制重读
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
        const next = await fetchHrCredentialCard(selectedId);
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
  }, [selectedId, detailReloadToken]);

  const selectCard = useCallback((cardId: string | null) => {
    setWriteError(null);
    setSelectedId(cardId);
  }, []);

  /**
   * 用途：创建成功后重读列表；若返回 id 则选中并触发详情 GET。
   */
  const createCard = useCallback(
    async (input: HrCardFormInput): Promise<boolean> => {
      if (submitting) return false;
      const built = buildHrCreateBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        const created = await createHrCredentialCard(built.body);
        // 成功后必须重读服务端；不可乐观伪造
        setListReloadToken((n) => n + 1);
        if (created?.id) {
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
   * 用途：更新/启停成功后重读列表与当前详情。
   */
  const updateCard = useCallback(
    async (cardId: string, input: HrCardFormInput): Promise<boolean> => {
      if (submitting) return false;
      const built = buildHrUpdateBody(input);
      if (!built.ok) {
        setWriteError(built.error);
        return false;
      }
      setSubmitting(true);
      setWriteError(null);
      try {
        await updateHrCredentialCard(cardId, built.body);
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
   * 用途：仅切换启停布尔；成功后强制重读。
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
        await updateHrCredentialCard(cardId, { isActive });
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

  return {
    items,
    listLoading,
    listError,
    selectedId,
    detail,
    detailLoading,
    detailError,
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
