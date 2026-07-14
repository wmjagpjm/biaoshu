/**
 * 模块：P10F 团队推荐 HR Hook
 * 用途：初始仅加载项目选择器与资质摘要；选项目后取详情；PUT 成功后重读摘要与详情。
 * 对接：hrTeamRecommendationApi；fetchHrCredentialCards；HrTeamRecommendationsPage。
 * 二次开发：禁止初始/刷新 GET 团队摘要；禁止乐观伪造与浏览器存储；禁止预取卡片详情/remark。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ApiError } from "../../../shared/lib/api";
import { fetchHrCredentialCards } from "../../hr/lib/hrCredentialApi";
import type { HrCredentialCardSummary } from "../../hr/types";
import {
  fetchHrTeamProjects,
  fetchHrTeamRecommendationDetail,
  fetchHrTeamRecommendationSummaries,
  putHrTeamRecommendation,
} from "../lib/hrTeamRecommendationApi";
import type {
  HrTeamProjectSelectorItem,
  HrTeamRecommendationDetail,
  HrTeamRecommendationSummary,
} from "../types";

const ERR_BOOT = "暂时无法加载团队推荐数据，请稍后重试";
const ERR_DETAIL = "暂时无法读取团队推荐";
const ERR_PROJECT = "项目不存在或不可访问";
const ERR_SAVE = "保存团队推荐失败，请稍后重试";
const ERR_INACTIVE = "存在已停用或不可用成员，请移除后再保存";
const ERR_LIMIT = "同一项目最多推荐 30 名成员";
const ERR_FORBIDDEN = "当前账号无权管理团队推荐";

/** 用途：安全列表/启动错误文案。 */
function toSafeBootError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，团队推荐暂时不可用";
  if (status === 403) return ERR_FORBIDDEN;
  return ERR_BOOT;
}

/** 用途：详情错误；404 not_found 由调用方处理为空态。 */
function toSafeDetailError(err: unknown): string {
  const api = err as ApiError | undefined;
  const status = api && typeof api === "object" ? api.status : 0;
  const code = api && typeof api === "object" ? api.code : undefined;
  if (status === 0) return "无法连接后端，详情暂时不可用";
  if (status === 403) return ERR_FORBIDDEN;
  if (status === 404 && code === "hr_team_project_not_found") {
    return ERR_PROJECT;
  }
  return ERR_DETAIL;
}

/** 用途：保存失败固定中文，不回显 detail/卡 ID。 */
function toSafeSaveError(err: unknown): string {
  const status =
    err && typeof err === "object" && "status" in err
      ? (err as ApiError).status
      : 0;
  if (status === 0) return "无法连接后端，保存未完成";
  if (status === 403) return ERR_FORBIDDEN;
  if (status === 404) return ERR_PROJECT;
  if (status === 422) return "提交内容不符合要求，请检查后重试";
  return ERR_SAVE;
}

/**
 * 用途：HR 团队推荐状态机。
 * 对接：HrTeamRecommendationsPage。
 * 二次开发：仅内存；写后必须服务端重读。
 */
export function useHrTeamRecommendations() {
  const [projects, setProjects] = useState<HrTeamProjectSelectorItem[]>([]);
  const [cards, setCards] = useState<HrCredentialCardSummary[]>([]);
  const [summaries, setSummaries] = useState<HrTeamRecommendationSummary[]>(
    [],
  );
  const [bootLoading, setBootLoading] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [detail, setDetail] = useState<HrTeamRecommendationDetail | null>(
    null,
  );
  /** 项目合法但尚未组装 */
  const [detailEmpty, setDetailEmpty] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  /** 用户点选的有序有效卡 ID */
  const [selectedCardIds, setSelectedCardIds] = useState<string[]>([]);
  /**
   * 快照中已停用/不可用的来源卡（展示提示；禁止静默写入）。
   */
  const [inactiveSnapshotIds, setInactiveSnapshotIds] = useState<string[]>(
    [],
  );
  const [inactiveLabels, setInactiveLabels] = useState<string[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);

  const [bootToken, setBootToken] = useState(0);
  const [detailToken, setDetailToken] = useState(0);

  const activeCards = useMemo(
    () => cards.filter((c) => c.isActive === true),
    [cards],
  );

  const activeIdSet = useMemo(
    () => new Set(activeCards.map((c) => c.id)),
    [activeCards],
  );

  const reloadBoot = useCallback(() => {
    setBootToken((n) => n + 1);
  }, []);

  const clearWriteError = useCallback(() => {
    setWriteError(null);
  }, []);

  // 初始/刷新：仅 projects + credential-cards；不得 GET 团队摘要或详情
  useEffect(() => {
    let cancelled = false;
    setBootLoading(true);
    setBootError(null);
    // 摘要仅允许 PUT 成功后重读；刷新不保留也不预取
    setSummaries([]);
    void (async () => {
      try {
        const [projList, cardList] = await Promise.all([
          fetchHrTeamProjects(),
          fetchHrCredentialCards(),
        ]);
        if (cancelled) return;
        setProjects(projList);
        setCards(cardList);
        setBootError(null);
      } catch (err) {
        if (cancelled) return;
        setProjects([]);
        setCards([]);
        setSummaries([]);
        setBootError(toSafeBootError(err));
      } finally {
        if (!cancelled) setBootLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bootToken]);

  // 选中项目后才 GET 详情
  useEffect(() => {
    if (!selectedProjectId) {
      setDetail(null);
      setDetailEmpty(false);
      setDetailError(null);
      setDetailLoading(false);
      setSelectedCardIds([]);
      setInactiveSnapshotIds([]);
      setInactiveLabels([]);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    setDetailEmpty(false);
    setWriteError(null);
    void (async () => {
      try {
        const next = await fetchHrTeamRecommendationDetail(selectedProjectId);
        if (cancelled) return;
        setDetail(next);
        setDetailEmpty(false);
        const ids = next.members.map((m) => m.sourceCardId);
        const inactive: string[] = [];
        const labels: string[] = [];
        const activeSelected: string[] = [];
        for (const m of next.members) {
          if (activeIdSet.has(m.sourceCardId)) {
            activeSelected.push(m.sourceCardId);
          } else {
            inactive.push(m.sourceCardId);
            labels.push(
              m.personName?.trim()
                ? `${m.personName}（已停用或不可用）`
                : "已停用或不可用成员",
            );
          }
        }
        // 预选仅有效卡；停用项单独提示，不得静默保留 ID
        setSelectedCardIds(activeSelected);
        setInactiveSnapshotIds(inactive);
        setInactiveLabels(labels);
        // 兼容：若 members 为空
        if (ids.length === 0) {
          setSelectedCardIds([]);
          setInactiveSnapshotIds([]);
          setInactiveLabels([]);
        }
      } catch (err) {
        if (cancelled) return;
        const api = err as ApiError;
        if (
          api &&
          typeof api === "object" &&
          api.status === 404 &&
          api.code === "hr_team_recommendation_not_found"
        ) {
          setDetail(null);
          setDetailEmpty(true);
          setDetailError(null);
          setSelectedCardIds([]);
          setInactiveSnapshotIds([]);
          setInactiveLabels([]);
          return;
        }
        setDetail(null);
        setDetailEmpty(false);
        setDetailError(toSafeDetailError(err));
        setSelectedCardIds([]);
        setInactiveSnapshotIds([]);
        setInactiveLabels([]);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, detailToken, activeIdSet]);

  const selectProject = useCallback((projectId: string | null) => {
    setWriteError(null);
    setSelectedProjectId(projectId);
  }, []);

  /**
   * 用途：按用户点击顺序追加有效卡；已选则忽略；上限 30。
   */
  const toggleSelectCard = useCallback(
    (cardId: string) => {
      setWriteError(null);
      if (!activeIdSet.has(cardId)) return;
      setSelectedCardIds((prev) => {
        if (prev.includes(cardId)) {
          return prev.filter((id) => id !== cardId);
        }
        if (prev.length >= 30) {
          setWriteError(ERR_LIMIT);
          return prev;
        }
        return [...prev, cardId];
      });
    },
    [activeIdSet],
  );

  /**
   * 用途：从当前选择中移除指定卡（含清理停用提示中的 ID）。
   */
  const removeSelectedCard = useCallback((cardId: string) => {
    setWriteError(null);
    setSelectedCardIds((prev) => prev.filter((id) => id !== cardId));
  }, []);

  /**
   * 用途：清除快照中的停用成员提示（不保留其 ID）。
   */
  const clearInactiveSnapshot = useCallback(() => {
    setWriteError(null);
    setInactiveSnapshotIds([]);
    setInactiveLabels([]);
  }, []);

  /**
   * 用途：保存有序 memberCardIds；成功后才允许 GET 摘要并重读详情。
   * 对接：PUT；禁止带停用卡 ID；摘要不得出现在初始/刷新路径。
   */
  const saveRecommendation = useCallback(async (): Promise<boolean> => {
    if (submitting || !selectedProjectId) return false;
    if (inactiveSnapshotIds.length > 0) {
      setWriteError(ERR_INACTIVE);
      return false;
    }
    const ids = selectedCardIds.filter((id) => activeIdSet.has(id));
    if (ids.length !== selectedCardIds.length) {
      setWriteError(ERR_INACTIVE);
      return false;
    }
    if (ids.length > 30) {
      setWriteError(ERR_LIMIT);
      return false;
    }
    setSubmitting(true);
    setWriteError(null);
    try {
      await putHrTeamRecommendation(selectedProjectId, {
        memberCardIds: ids,
      });
      // 强制服务端重读，不可乐观伪造
      try {
        const summaryList = await fetchHrTeamRecommendationSummaries();
        setSummaries(summaryList);
      } catch {
        /* 摘要失败不阻断详情重读 */
      }
      setDetailToken((n) => n + 1);
      return true;
    } catch (err) {
      setWriteError(toSafeSaveError(err));
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [
    submitting,
    selectedProjectId,
    inactiveSnapshotIds,
    selectedCardIds,
    activeIdSet,
  ]);

  return {
    projects,
    cards,
    activeCards,
    summaries,
    bootLoading,
    bootError,
    selectedProjectId,
    detail,
    detailEmpty,
    detailLoading,
    detailError,
    selectedCardIds,
    inactiveSnapshotIds,
    inactiveLabels,
    submitting,
    writeError,
    selectProject,
    toggleSelectCard,
    removeSelectedCard,
    clearInactiveSnapshot,
    clearWriteError,
    reloadBoot,
    saveRecommendation,
  };
}
