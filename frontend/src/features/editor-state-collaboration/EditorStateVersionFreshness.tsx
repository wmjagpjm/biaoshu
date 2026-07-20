/**
 * 模块：P13-B/P13-C/P13-D2 已载入编辑版本时间、修订来源与操作者展示组件
 * 用途：以纯函数严格格式化 editor-state 服务端 UTC `updatedAt`，在工作区标题区展示
 *       「当前已载入版本」时间；展示「当前版本来源」中文标签；
 *       展示「当前版本操作者」用户名（非法/缺失为操作者未知）。
 * 对接：useTechnicalPlanEditors / useBusinessBidWorkspace 的 versionUpdatedAt、
 *       currentRevisionSourceKind 与 currentRevisionActorUsername；
 *       技术标 testid=`technical-editor-version-freshness` /
 *       `technical-editor-version-source` / `technical-editor-version-actor`；
 *       商务标 testid=`business-editor-version-freshness` /
 *       `business-editor-version-source` / `business-editor-version-actor`。
 * 二次开发：禁止发请求、设定时器、读 storage/Cookie/URL 或持有项目状态；
 *       不得按浏览器时区重解释；不得使用 toLocaleString；
 *       不得声称远端最新/实时/在线；来源标签必须复用 REVISION_SOURCE_LABELS，
 *       禁止第二套中文映射；用户名只作 React 文本节点，不进 HTML/属性/title/URL。
 */

import {
  formatRevisionSourceLabel,
  type RevisionSourceKind,
} from "../editor-state-revisions/editorStateRevisionApi";

/** 服务端 UTC 无后缀 ISO：YYYY-MM-DDTHH:mm:ss 可选 1–6 位小数 */
const STRICT_UTC_ISO_RE =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$/;

const UNKNOWN_TIME_BODY = "更新时间未知";
const UNKNOWN_SOURCE_BODY = "来源未知";
const UNKNOWN_ACTOR_BODY = "操作者未知";

/**
 * 用途：校验日历日期是否真实存在（拒绝 2026-02-30 等）。
 */
function isRealCalendarDate(year: number, month: number, day: number): boolean {
  if (month < 1 || month > 12 || day < 1 || day > 31) return false;
  const dt = new Date(Date.UTC(year, month - 1, day));
  return (
    dt.getUTCFullYear() === year &&
    dt.getUTCMonth() === month - 1 &&
    dt.getUTCDate() === day
  );
}

/**
 * 用途：严格格式化服务端 updatedAt；合法值到秒并追加 UTC，其余返回未知正文。
 * 对接：仅接受无 Z/无偏移/无空白包裹/无尾随字符的精确结构。
 */
function formatServerUpdatedAt(
  updatedAt: string | null | undefined,
): string {
  if (typeof updatedAt !== "string") return UNKNOWN_TIME_BODY;
  const match = STRICT_UTC_ISO_RE.exec(updatedAt);
  if (!match) return UNKNOWN_TIME_BODY;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);

  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(hour) ||
    !Number.isFinite(minute) ||
    !Number.isFinite(second)
  ) {
    return UNKNOWN_TIME_BODY;
  }
  if (hour > 23 || minute > 59 || second > 59) return UNKNOWN_TIME_BODY;
  if (!isRealCalendarDate(year, month, day)) return UNKNOWN_TIME_BODY;

  const y = String(year).padStart(4, "0");
  const mo = String(month).padStart(2, "0");
  const d = String(day).padStart(2, "0");
  const h = String(hour).padStart(2, "0");
  const mi = String(minute).padStart(2, "0");
  const s = String(second).padStart(2, "0");
  return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC`;
}

/**
 * 用途：格式化九类来源；null/非法由调用方已归一，此处 null → 来源未知。
 */
function formatSourceBody(sourceKind: RevisionSourceKind | null): string {
  if (sourceKind == null) return UNKNOWN_SOURCE_BODY;
  return formatRevisionSourceLabel(sourceKind);
}

/**
 * 用途：格式化操作者；null → 操作者未知；非空原样文本。
 */
function formatActorBody(actorUsername: string | null): string {
  if (actorUsername == null) return UNKNOWN_ACTOR_BODY;
  return actorUsername;
}

export type EditorStateVersionFreshnessProps = {
  /** 当前会话已接受的服务端 updatedAt；null 表示未知 */
  updatedAt: string | null;
  /** P13-C：当前会话已接受的修订来源；null 表示未知 */
  sourceKind?: RevisionSourceKind | null;
  /** P13-D2：当前会话已接受的操作者用户名；null 表示未知 */
  actorUsername?: string | null;
  /** 固定 data-testid（技术/商务各一，时间行） */
  testId: string;
  /** 固定来源行 data-testid（技术/商务各一） */
  sourceTestId: string;
  /** 固定操作者行 data-testid（技术/商务各一） */
  actorTestId: string;
};

/**
 * 用途：标题区无副作用展示「当前已载入版本：…」「当前版本来源：…」
 *       与「当前版本操作者：…」。
 */
export function EditorStateVersionFreshness({
  updatedAt,
  sourceKind = null,
  actorUsername = null,
  testId,
  sourceTestId,
  actorTestId,
}: EditorStateVersionFreshnessProps) {
  const timeBody = formatServerUpdatedAt(updatedAt);
  const sourceBody = formatSourceBody(sourceKind);
  const actorBody = formatActorBody(actorUsername);
  return (
    <div style={{ margin: "6px 0 0" }}>
      <p data-testid={testId} style={{ margin: 0 }}>
        {`当前已载入版本：${timeBody}`}
      </p>
      <p data-testid={sourceTestId} style={{ margin: "2px 0 0" }}>
        {`当前版本来源：${sourceBody}`}
      </p>
      <p data-testid={actorTestId} style={{ margin: "2px 0 0" }}>
        {`当前版本操作者：${actorBody}`}
      </p>
    </div>
  );
}
