/**
 * 模块：P13-G2 技术标章节编辑意图生命周期与展示面板
 * 用途：eligible 门控、可见性、串行 heartbeat/leave、generation 迟到隔离与固定文案。
 * 对接：useAuthSession；chapterEditIntentApi；TechnicalPlanWorkspace content 薄挂载；
 *       testid=technical-chapter-edit-intent。
 * 二次开发：禁止强制锁/禁用编辑器；禁止展示在线/实时/正在编辑/锁定/lease ID；
 *       禁止 setInterval 并发；冲突与 unavailable 仅提示。
 */

import { useEffect, useState } from "react";
import { useAuthSession } from "../auth/hooks/useAuthSession";
import {
  enqueueChapterEditWrite,
  heartbeatChapterEditIntent,
  leaveChapterEditIntent,
} from "./chapterEditIntentApi";
import { getOrCreatePresenceClientId } from "./projectPresenceApi";

const TITLE_TEXT = "本章处理意图";
const SELF_TEXT = "已记录你的近期处理意图";
const UNAVAILABLE_TEXT = "章节处理意图暂不可用";
const CONFLICT_PREFIX = "近期由 ";
const CONFLICT_SUFFIX = " 处理";
const REFRESH_MS = 15_000;
const TEST_ID = "technical-chapter-edit-intent";

export type ChapterEditIntentPanelProps = {
  /** 当前技术标路由项目 ID */
  projectId: string;
  /** 当前有效章节 ID（editors.selectedChapterId）；空则零请求 */
  chapterId: string | null;
};

type BoundUi =
  | { projectId: string; chapterId: string; phase: "loading" }
  | { projectId: string; chapterId: string; phase: "cleared" }
  | { projectId: string; chapterId: string; phase: "unavailable" }
  | { projectId: string; chapterId: string; phase: "self" }
  | {
      projectId: string;
      chapterId: string;
      phase: "conflict";
      holderUsername: string;
    };

/**
 * 用途：在 content 工具栏与 ChapterEditor 之间展示章节处理意图；
 *       仅 authenticated + bid_writer + 有效章节 + 可见时写租约。
 */
export function ChapterEditIntentPanel({
  projectId,
  chapterId,
}: ChapterEditIntentPanelProps) {
  const { phase, activeMembership } = useAuthSession();
  const eligible =
    phase === "authenticated" &&
    activeMembership?.role === "bid_writer" &&
    Boolean(projectId) &&
    Boolean(chapterId);

  const [ui, setUi] = useState<BoundUi | null>(null);

  useEffect(() => {
    if (!eligible || !chapterId) {
      setUi(null);
      return;
    }

    let generation = 0;
    let cancelled = false;
    let hasJoined = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    let firstTimer: ReturnType<typeof setTimeout> | null = null;
    let activePid = projectId;
    let activeChapterId = chapterId;

    const clearRefreshTimer = () => {
      if (refreshTimer !== null) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
    };

    const clearFirstTimer = () => {
      if (firstTimer !== null) {
        clearTimeout(firstTimer);
        firstTimer = null;
      }
    };

    const bumpGeneration = () => {
      generation += 1;
      clearRefreshTimer();
      clearFirstTimer();
      return generation;
    };

    const enqueueLeave = (
      pid: string,
      chId: string,
      keepalive: boolean,
    ) => {
      if (!hasJoined) return;
      void enqueueChapterEditWrite(() =>
        leaveChapterEditIntent(pid, chId, { keepalive }),
      );
    };

    const scheduleRefresh = (gen: number, pid: string, chId: string) => {
      clearRefreshTimer();
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        if (cancelled || gen !== generation) return;
        if (
          typeof document !== "undefined" &&
          document.visibilityState !== "visible"
        ) {
          return;
        }
        void runHeartbeat(gen, pid, chId);
      }, REFRESH_MS);
    };

    const runHeartbeat = async (
      gen: number,
      pid: string,
      chId: string,
    ) => {
      hasJoined = true;
      const result = await enqueueChapterEditWrite(() =>
        heartbeatChapterEditIntent(pid, chId),
      );
      if (cancelled || gen !== generation) return;
      if (pid !== activePid || chId !== activeChapterId) return;
      if (
        typeof document !== "undefined" &&
        document.visibilityState !== "visible"
      ) {
        return;
      }
      if (result.kind === "self") {
        setUi({ projectId: pid, chapterId: chId, phase: "self" });
      } else if (result.kind === "conflict") {
        setUi({
          projectId: pid,
          chapterId: chId,
          phase: "conflict",
          holderUsername: result.holderUsername,
        });
      } else {
        setUi({ projectId: pid, chapterId: chId, phase: "unavailable" });
      }
      // 成功/冲突/不可用均在完成后 15s 有限续租；禁止 setInterval
      scheduleRefresh(gen, pid, chId);
    };

    const startVisibleHeartbeat = () => {
      // 首次 visible 才检查/生成 clientId；失败固定 unavailable 且零写
      const clientId = getOrCreatePresenceClientId();
      if (!clientId) {
        bumpGeneration();
        setUi({
          projectId: activePid,
          chapterId: activeChapterId,
          phase: "unavailable",
        });
        return;
      }
      const gen = bumpGeneration();
      setUi({
        projectId: activePid,
        chapterId: activeChapterId,
        phase: "loading",
      });
      // 可取消零延迟：吸收 React StrictMode 首轮 effect 探测
      firstTimer = setTimeout(() => {
        firstTimer = null;
        if (cancelled || gen !== generation) return;
        if (
          typeof document !== "undefined" &&
          document.visibilityState !== "visible"
        ) {
          return;
        }
        void runHeartbeat(gen, activePid, activeChapterId);
      }, 0);
    };

    const onVisibilityChange = () => {
      if (typeof document === "undefined") return;
      if (document.visibilityState === "hidden") {
        bumpGeneration();
        setUi({
          projectId: activePid,
          chapterId: activeChapterId,
          phase: "cleared",
        });
        enqueueLeave(activePid, activeChapterId, false);
        return;
      }
      if (document.visibilityState === "visible") {
        startVisibleHeartbeat();
      }
    };

    const onPageHide = () => {
      enqueueLeave(activePid, activeChapterId, true);
    };

    if (
      typeof document === "undefined" ||
      document.visibilityState === "visible"
    ) {
      startVisibleHeartbeat();
    } else {
      // 初始 hidden：同步 cleared，不 heartbeat/leave、不生成 clientId
      setUi({
        projectId: activePid,
        chapterId: activeChapterId,
        phase: "cleared",
      });
    }

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    if (typeof window !== "undefined") {
      window.addEventListener("pagehide", onPageHide);
    }

    return () => {
      cancelled = true;
      const leavingPid = activePid;
      const leavingChapter = activeChapterId;
      bumpGeneration();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("pagehide", onPageHide);
      }
      // 章节/项目切换 / 卸载：best-effort leave（仅曾发起过 heartbeat 时）
      enqueueLeave(leavingPid, leavingChapter, false);
    };
  }, [eligible, projectId, chapterId]);

  if (!eligible || !chapterId) {
    return null;
  }

  // 渲染同步绑定 projectId+chapterId：拒绝旧章节/旧项目快照首帧泄漏
  const view: BoundUi =
    ui && ui.projectId === projectId && ui.chapterId === chapterId
      ? ui
      : typeof document !== "undefined" &&
          document.visibilityState === "hidden"
        ? { projectId, chapterId, phase: "cleared" }
        : { projectId, chapterId, phase: "loading" };

  return (
    <div data-testid={TEST_ID} style={{ marginTop: 6, marginBottom: 6 }}>
      <div>{TITLE_TEXT}</div>
      {view.phase === "self" ? <div>{SELF_TEXT}</div> : null}
      {view.phase === "conflict" ? (
        <div>
          {CONFLICT_PREFIX}
          {view.holderUsername}
          {CONFLICT_SUFFIX}
        </div>
      ) : null}
      {view.phase === "unavailable" ? <div>{UNAVAILABLE_TEXT}</div> : null}
      {/* loading/cleared：仅保留标题 */}
    </div>
  );
}
