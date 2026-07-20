/**
 * 模块：P13-F2 项目近期成员生命周期与展示面板
 * 用途：eligible 门控、可见性、串行 heartbeat/leave、generation 迟到隔离与固定文案展示。
 * 对接：useAuthSession；projectPresenceApi；技术/商务工作区标题区薄挂载；
 *       testid 由调用方固定传入 technical-project-presence / business-project-presence。
 * 二次开发：禁止在线/实时/正在编辑/正在输入/最后活跃等承诺；禁止展示
 *       status/detail/code/URL/projectId/clientId/secret；禁止并发 setInterval。
 */

import { useEffect, useState } from "react";
import { useAuthSession } from "../auth/hooks/useAuthSession";
import {
  enqueuePresenceWrite,
  getOrCreatePresenceClientId,
  heartbeatProjectPresence,
  leaveProjectPresence,
  type ProjectPresenceMember,
} from "./projectPresenceApi";

const TITLE_TEXT = "近期在此项目";
const LOADING_TEXT = "近期成员加载中";
const UNAVAILABLE_TEXT = "近期成员暂不可用";
const TRUNCATED_TEXT = "另有更多近期成员";
const SELF_SUFFIX = "（我）";
const REFRESH_MS = 15_000;

export type ProjectPresencePanelProps = {
  /** 当前路由项目 ID；空则不启用 */
  projectId: string;
  /** 固定 data-testid（技术/商务各一） */
  testId: string;
};

type BoundUi =
  | { projectId: string; phase: "loading" }
  | { projectId: string; phase: "cleared" }
  | { projectId: string; phase: "unavailable" }
  | {
      projectId: string;
      phase: "ready";
      members: ProjectPresenceMember[];
      truncated: boolean;
    };

/**
 * 用途：在标题区展示服务端短租约成员快照；仅 authenticated + bid_writer + 可见时启用。
 */
export function ProjectPresencePanel({
  projectId,
  testId,
}: ProjectPresencePanelProps) {
  const { phase, activeMembership } = useAuthSession();
  const eligible =
    phase === "authenticated" &&
    activeMembership?.role === "bid_writer" &&
    Boolean(projectId);

  const [ui, setUi] = useState<BoundUi | null>(null);

  useEffect(() => {
    if (!eligible) {
      setUi(null);
      return;
    }

    // clientId 延迟到首次 visible：初始 hidden 不为主动 presence 生成
    let generation = 0;
    let cancelled = false;
    let hasJoined = false;
    let refreshTimer: ReturnType<typeof setTimeout> | null = null;
    let firstTimer: ReturnType<typeof setTimeout> | null = null;
    let activePid = projectId;

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

    const enqueueLeave = (pid: string, keepalive: boolean) => {
      if (!hasJoined) return;
      void enqueuePresenceWrite(() =>
        leaveProjectPresence(pid, { keepalive }),
      );
    };

    const scheduleRefresh = (gen: number, pid: string) => {
      clearRefreshTimer();
      refreshTimer = setTimeout(() => {
        refreshTimer = null;
        if (cancelled || gen !== generation) return;
        if (typeof document !== "undefined" && document.visibilityState !== "visible") {
          return;
        }
        void runHeartbeat(gen, pid);
      }, REFRESH_MS);
    };

    const runHeartbeat = async (gen: number, pid: string) => {
      hasJoined = true;
      const snap = await enqueuePresenceWrite(() =>
        heartbeatProjectPresence(pid),
      );
      if (cancelled || gen !== generation || pid !== activePid) return;
      if (typeof document !== "undefined" && document.visibilityState !== "visible") {
        return;
      }
      if (snap) {
        setUi({
          projectId: pid,
          phase: "ready",
          members: snap.members,
          truncated: snap.truncated,
        });
      } else {
        setUi({ projectId: pid, phase: "unavailable" });
      }
      // 成功或失败均在 15s 后有限重试/续租；禁止并发 setInterval
      scheduleRefresh(gen, pid);
    };

    const startVisibleHeartbeat = () => {
      // 首次 visible 才检查/生成 clientId；失败固定 unavailable 且零写
      const clientId = getOrCreatePresenceClientId();
      if (!clientId) {
        bumpGeneration();
        setUi({ projectId: activePid, phase: "unavailable" });
        return;
      }
      const gen = bumpGeneration();
      setUi({ projectId: activePid, phase: "loading" });
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
        void runHeartbeat(gen, activePid);
      }, 0);
    };

    const onVisibilityChange = () => {
      if (typeof document === "undefined") return;
      if (document.visibilityState === "hidden") {
        bumpGeneration();
        // 立即清空可见成员，不显示加载/不可用占位
        setUi({ projectId: activePid, phase: "cleared" });
        enqueueLeave(activePid, false);
        return;
      }
      if (document.visibilityState === "visible") {
        startVisibleHeartbeat();
      }
    };

    const onPageHide = () => {
      enqueueLeave(activePid, true);
    };

    if (
      typeof document === "undefined" ||
      document.visibilityState === "visible"
    ) {
      startVisibleHeartbeat();
    } else {
      // 初始 hidden：同步 cleared，不 heartbeat/leave、不生成 clientId
      setUi({ projectId: activePid, phase: "cleared" });
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
      bumpGeneration();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      if (typeof window !== "undefined") {
        window.removeEventListener("pagehide", onPageHide);
      }
      // 项目切换 / 卸载：best-effort leave（仅曾发起过 heartbeat 时）
      enqueueLeave(leavingPid, false);
    };
  }, [eligible, projectId]);

  if (!eligible) {
    return null;
  }

  // 渲染同步绑定 projectId：拒绝旧项目快照首帧泄漏
  // 初始 hidden 时 ui 尚未提交前也呈现 cleared，避免短暂 loading
  const view: BoundUi =
    ui && ui.projectId === projectId
      ? ui
      : typeof document !== "undefined" &&
          document.visibilityState === "hidden"
        ? { projectId, phase: "cleared" }
        : { projectId, phase: "loading" };

  return (
    <div data-testid={testId} style={{ marginTop: 6 }}>
      <div>{TITLE_TEXT}</div>
      {view.phase === "loading" ? <div>{LOADING_TEXT}</div> : null}
      {view.phase === "unavailable" ? <div>{UNAVAILABLE_TEXT}</div> : null}
      {view.phase === "ready" ? (
        <>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {view.members.map((m) => (
              <li key={`${m.isSelf ? "self" : "peer"}:${m.username}`}>
                {m.username}
                {m.isSelf ? SELF_SUFFIX : ""}
              </li>
            ))}
          </ul>
          {view.truncated ? <div>{TRUNCATED_TEXT}</div> : null}
        </>
      ) : null}
      {/* phase=cleared：仅保留标题，成员区为空 */}
    </div>
  );
}
