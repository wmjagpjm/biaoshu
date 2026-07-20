/**
 * 模块：P13-B 已载入编辑版本更新时间展示组件
 * 用途：以纯函数严格格式化 editor-state 服务端 UTC `updatedAt`，在工作区标题区展示
 *       「当前已载入版本」时间；非法/缺失值显示固定未知文案。
 * 对接：useTechnicalPlanEditors / useBusinessBidWorkspace 的 versionUpdatedAt；
 *       技术标 testid=`technical-editor-version-freshness`；
 *       商务标 testid=`business-editor-version-freshness`。
 * 二次开发：禁止发请求、设定时器、读 storage/Cookie/URL 或持有项目状态；
 *       不得按浏览器时区重解释；不得使用 toLocaleString；不得声称远端最新/实时/在线。
 */

/** 服务端 UTC 无后缀 ISO：YYYY-MM-DDTHH:mm:ss 可选 1–6 位小数 */
const STRICT_UTC_ISO_RE =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$/;

const UNKNOWN_BODY = "更新时间未知";

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
  if (typeof updatedAt !== "string") return UNKNOWN_BODY;
  const match = STRICT_UTC_ISO_RE.exec(updatedAt);
  if (!match) return UNKNOWN_BODY;

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
    return UNKNOWN_BODY;
  }
  if (hour > 23 || minute > 59 || second > 59) return UNKNOWN_BODY;
  if (!isRealCalendarDate(year, month, day)) return UNKNOWN_BODY;

  const y = String(year).padStart(4, "0");
  const mo = String(month).padStart(2, "0");
  const d = String(day).padStart(2, "0");
  const h = String(hour).padStart(2, "0");
  const mi = String(minute).padStart(2, "0");
  const s = String(second).padStart(2, "0");
  return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC`;
}

export type EditorStateVersionFreshnessProps = {
  /** 当前会话已接受的服务端 updatedAt；null 表示未知 */
  updatedAt: string | null;
  /** 固定 data-testid（技术/商务各一） */
  testId: string;
};

/**
 * 用途：标题区无副作用展示「当前已载入版本：…」。
 */
export function EditorStateVersionFreshness({
  updatedAt,
  testId,
}: EditorStateVersionFreshnessProps) {
  const body = formatServerUpdatedAt(updatedAt);
  return (
    <p data-testid={testId} style={{ margin: "6px 0 0" }}>
      {`当前已载入版本：${body}`}
    </p>
  );
}
