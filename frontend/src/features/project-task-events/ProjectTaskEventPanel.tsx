/**
 * 模块：P13-I3 项目任务事件前端提示面板
 * 用途：required + authenticated + bid_writer 下连接项目级 task-events SSE；
 *       严格解析 cursor/task-event/cursor-stale/unavailable；仅展示固定中文
 *       任务类型/状态/进度；坏帧/控制帧/网络错误固定「项目任务提示暂不可用」。
 * 对接：useAuthSession；getApiBase；页面传入 projectId / testId。
 * 二次开发：禁止展示 taskId/eventId/occurredAt/后端 detail；禁止任务详情/
 *       editor-state 请求；禁止 storage/URL/console；A→B 必须 close 并清空。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthSession } from "../auth/hooks/useAuthSession";
import { isValidUtcMillisString } from "../editor-state-revisions/editorStateRevisionApi";
import { getApiBase } from "../../shared/lib/api";

const UNAVAILABLE_TEXT = "项目任务提示暂不可用";
const OTHER_TASK_LABEL = "其他任务";

const PTE_RE = /^pte_[0-9a-f]{32}$/;
const TASK_RE = /^task_[0-9a-f]{16}$/;
const CONTROL_CODE_STALE = "project_task_event_cursor_stale";
const CONTROL_CODE_UNAVAILABLE = "project_task_event_unavailable";
const STATUSES = new Set([
  "pending",
  "running",
  "success",
  "failed",
  "cancelled",
]);

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "进行中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
};

const TYPE_LABEL: Record<string, string> = {
  parse: "解析",
  analyze: "分析",
  outline: "大纲",
  chapter: "章节",
  chapters: "批量章节",
  export: "导出",
  response_match: "响应匹配",
  content_fuse: "内容融合",
  biz_qualify: "资格审查",
  biz_toc: "商务目录",
  biz_quote: "报价",
  biz_commit: "商务承诺",
};

export type ProjectTaskEventPanelProps = {
  projectId: string;
  testId: string;
};

type SafeEvent = {
  typeLabel: string;
  statusLabel: string;
  progress: number;
  eventId: string;
};

type PanelPhase = "idle" | "event" | "unavailable";

function readJsonStringLiteral(
  raw: string,
  index: number,
): { value: string; end: number } | null {
  if (index >= raw.length || raw[index] !== '"') return null;
  let i = index + 1;
  let value = "";
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === '"') return { value, end: i + 1 };
    if (ch === "\\") {
      if (i + 1 >= raw.length) return null;
      const esc = raw[i + 1];
      if (
        esc === '"' ||
        esc === "\\" ||
        esc === "/" ||
        esc === "b" ||
        esc === "f" ||
        esc === "n" ||
        esc === "r" ||
        esc === "t"
      ) {
        const map: Record<string, string> = {
          '"': '"',
          "\\": "\\",
          "/": "/",
          b: "\b",
          f: "\f",
          n: "\n",
          r: "\r",
          t: "\t",
        };
        value += map[esc];
        i += 2;
        continue;
      }
      if (esc === "u" && i + 6 <= raw.length) {
        const hex = raw.slice(i + 2, i + 6);
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) return null;
        value += String.fromCharCode(parseInt(hex, 16));
        i += 6;
        continue;
      }
      return null;
    }
    value += ch;
    i += 1;
  }
  return null;
}

function skipJsonWs(raw: string, index: number): number {
  let i = index;
  while (i < raw.length) {
    const c = raw[i];
    if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      i += 1;
      continue;
    }
    break;
  }
  return i;
}

function skipJsonValue(raw: string, index: number): number {
  let i = skipJsonWs(raw, index);
  if (i >= raw.length) return -1;
  const c = raw[i];
  if (c === '"') {
    const str = readJsonStringLiteral(raw, i);
    return str ? str.end : -1;
  }
  if (c === "{") {
    i += 1;
    i = skipJsonWs(raw, i);
    if (i < raw.length && raw[i] === "}") return i + 1;
    while (i < raw.length) {
      i = skipJsonWs(raw, i);
      if (i >= raw.length || raw[i] !== '"') return -1;
      const key = readJsonStringLiteral(raw, i);
      if (!key) return -1;
      i = skipJsonWs(raw, key.end);
      if (i >= raw.length || raw[i] !== ":") return -1;
      i = skipJsonValue(raw, i + 1);
      if (i < 0) return -1;
      i = skipJsonWs(raw, i);
      if (i < raw.length && raw[i] === ",") {
        i += 1;
        continue;
      }
      if (i < raw.length && raw[i] === "}") return i + 1;
      return -1;
    }
    return -1;
  }
  if (c === "[") {
    i += 1;
    i = skipJsonWs(raw, i);
    if (i < raw.length && raw[i] === "]") return i + 1;
    while (i < raw.length) {
      i = skipJsonValue(raw, i);
      if (i < 0) return -1;
      i = skipJsonWs(raw, i);
      if (i < raw.length && raw[i] === ",") {
        i += 1;
        continue;
      }
      if (i < raw.length && raw[i] === "]") return i + 1;
      return -1;
    }
    return -1;
  }
  if (raw.startsWith("true", i)) return i + 4;
  if (raw.startsWith("false", i)) return i + 5;
  if (raw.startsWith("null", i)) return i + 4;
  if (c === "-" || (c >= "0" && c <= "9")) {
    i += 1;
    while (i < raw.length) {
      const ch = raw[i];
      if (
        (ch >= "0" && ch <= "9") ||
        ch === "+" ||
        ch === "-" ||
        ch === "e" ||
        ch === "E" ||
        ch === "."
      ) {
        i += 1;
        continue;
      }
      break;
    }
    return i;
  }
  return -1;
}

function hasDuplicateTopLevelObjectKeys(raw: string): boolean {
  if (typeof raw !== "string") return false;
  let i = skipJsonWs(raw, 0);
  if (i >= raw.length || raw[i] !== "{") return false;
  i += 1;
  i = skipJsonWs(raw, i);
  if (i < raw.length && raw[i] === "}") return false;
  const seen = new Set<string>();
  while (i < raw.length) {
    i = skipJsonWs(raw, i);
    if (i >= raw.length) return false;
    if (raw[i] !== '"') return false;
    const keyLit = readJsonStringLiteral(raw, i);
    if (!keyLit) return false;
    if (seen.has(keyLit.value)) return true;
    seen.add(keyLit.value);
    i = skipJsonWs(raw, keyLit.end);
    if (i >= raw.length || raw[i] !== ":") return false;
    i = skipJsonValue(raw, i + 1);
    if (i < 0) return false;
    i = skipJsonWs(raw, i);
    if (i < raw.length && raw[i] === ",") {
      i += 1;
      continue;
    }
    if (i < raw.length && raw[i] === "}") {
      i = skipJsonWs(raw, i + 1);
      return false;
    }
    return false;
  }
  return false;
}

function isValidTaskType(v: unknown): v is string {
  if (typeof v !== "string" || v.length === 0 || v.length > 64) return false;
  for (let i = 0; i < v.length; i += 1) {
    const code = v.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return false;
  }
  return true;
}

function parseCursorData(lastEventId: string, raw: string): boolean {
  if (hasDuplicateTopLevelObjectKeys(raw)) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return false;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return false;
  }
  const obj = parsed as Record<string, unknown>;
  if (Object.keys(obj).length !== 1) return false;
  if (!Object.prototype.hasOwnProperty.call(obj, "eventId")) return false;
  if (typeof obj.eventId !== "string" || !PTE_RE.test(obj.eventId)) return false;
  if (typeof lastEventId !== "string" || !PTE_RE.test(lastEventId)) return false;
  if (lastEventId !== obj.eventId) return false;
  return true;
}

function parseTaskEventData(
  lastEventId: string,
  raw: string,
): SafeEvent | null {
  if (hasDuplicateTopLevelObjectKeys(raw)) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  const obj = parsed as Record<string, unknown>;
  if (Object.keys(obj).length !== 6) return null;
  for (const required of [
    "eventId",
    "taskId",
    "taskType",
    "status",
    "progress",
    "occurredAt",
  ] as const) {
    if (!Object.prototype.hasOwnProperty.call(obj, required)) return null;
  }
  if (typeof obj.eventId !== "string" || !PTE_RE.test(obj.eventId)) return null;
  if (typeof lastEventId !== "string" || !PTE_RE.test(lastEventId)) return null;
  if (lastEventId !== obj.eventId) return null;
  if (typeof obj.taskId !== "string" || !TASK_RE.test(obj.taskId)) return null;
  if (!isValidTaskType(obj.taskType)) return null;
  if (typeof obj.status !== "string" || !STATUSES.has(obj.status)) return null;
  if (
    typeof obj.progress !== "number" ||
    !Number.isInteger(obj.progress) ||
    obj.progress < 0 ||
    obj.progress > 100
  ) {
    return null;
  }
  if (!isValidUtcMillisString(obj.occurredAt)) {
    return null;
  }
  const typeLabel = TYPE_LABEL[obj.taskType] ?? OTHER_TASK_LABEL;
  const statusLabel = STATUS_LABEL[obj.status] ?? OTHER_TASK_LABEL;
  return {
    typeLabel,
    statusLabel,
    progress: obj.progress,
    eventId: obj.eventId,
  };
}

/**
 * 用途：严格解析控制帧 data——精确 code/message 两键；
 * code 按事件名固定；message 非空字符串；禁止重复键/额外键/缺键。
 * 返回 true 表示合法控制帧（UI 仍固定 unavailable，不展示 message）。
 */
function parseControlFrameData(
  eventName: "cursor-stale" | "unavailable",
  raw: string,
): boolean {
  if (typeof raw !== "string") return false;
  if (hasDuplicateTopLevelObjectKeys(raw)) return false;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return false;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return false;
  }
  const obj = parsed as Record<string, unknown>;
  if (Object.keys(obj).length !== 2) return false;
  if (!Object.prototype.hasOwnProperty.call(obj, "code")) return false;
  if (!Object.prototype.hasOwnProperty.call(obj, "message")) return false;
  if (typeof obj.code !== "string" || obj.code.length === 0) return false;
  if (typeof obj.message !== "string" || obj.message.length === 0) return false;
  const expected =
    eventName === "cursor-stale"
      ? CONTROL_CODE_STALE
      : CONTROL_CODE_UNAVAILABLE;
  if (obj.code !== expected) return false;
  return true;
}

/** 用途：标题区任务事件提示；门控通过时建连；仅展示固定安全标签。 */
export function ProjectTaskEventPanel({
  projectId,
  testId,
}: ProjectTaskEventPanelProps) {
  const { phase, authRequired, activeMembership } = useAuthSession();
  const eligible =
    authRequired === true &&
    phase === "authenticated" &&
    activeMembership?.role === "bid_writer" &&
    Boolean(projectId);

  const [panelPhase, setPanelPhase] = useState<PanelPhase>("idle");
  const [safeEvent, setSafeEvent] = useState<SafeEvent | null>(null);

  const generationRef = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);

  const closeSource = useCallback(() => {
    const src = sourceRef.current;
    sourceRef.current = null;
    if (src) {
      try {
        src.close();
      } catch {
        /* ignore */
      }
    }
  }, []);

  useEffect(() => {
    if (!eligible) {
      generationRef.current += 1;
      closeSource();
      setPanelPhase("idle");
      setSafeEvent(null);
      return;
    }

    const gen = ++generationRef.current;
    const activePid = projectId;
    setPanelPhase("idle");
    setSafeEvent(null);
    closeSource();

    const isCurrent = () =>
      generationRef.current === gen && sourceRef.current !== null;

    const markUnavailable = () => {
      if (generationRef.current !== gen) return;
      setSafeEvent(null);
      setPanelPhase("unavailable");
      closeSource();
    };

    const url = `${getApiBase()}/projects/${encodeURIComponent(activePid)}/task-events/stream`;

    let es: EventSource;
    try {
      es = new EventSource(url, { withCredentials: true });
    } catch {
      markUnavailable();
      return;
    }
    sourceRef.current = es;

    const onCursor = (ev: Event) => {
      if (!isCurrent() || sourceRef.current !== es) return;
      const me = ev as MessageEvent<string>;
      if (!parseCursorData(me.lastEventId, me.data)) {
        markUnavailable();
      }
    };

    const onTaskEvent = (ev: Event) => {
      if (!isCurrent() || sourceRef.current !== es) return;
      const me = ev as MessageEvent<string>;
      const parsed = parseTaskEventData(me.lastEventId, me.data);
      if (!parsed) {
        markUnavailable();
        return;
      }
      setSafeEvent((prev) => {
        if (prev && prev.eventId === parsed.eventId) return prev;
        return parsed;
      });
      setPanelPhase("event");
    };

    const onControlFrame = (eventName: "cursor-stale" | "unavailable") => {
      return (ev: Event) => {
        if (!isCurrent() || sourceRef.current !== es) return;
        const me = ev as MessageEvent<string>;
        // 无论 parser 成败：固定 unavailable UI 并 close；不展示后端 message
        try {
          void parseControlFrameData(eventName, me.data ?? "");
        } catch {
          /* parser 不得抛穿 */
        }
        markUnavailable();
      };
    };

    const onCursorStale = onControlFrame("cursor-stale");
    const onUnavailable = onControlFrame("unavailable");

    es.addEventListener("cursor", onCursor);
    es.addEventListener("task-event", onTaskEvent);
    es.addEventListener("cursor-stale", onCursorStale);
    es.addEventListener("unavailable", onUnavailable);

    es.onmessage = () => {
      if (!isCurrent() || sourceRef.current !== es) return;
      markUnavailable();
    };

    es.onerror = () => {
      if (generationRef.current !== gen) return;
      if (sourceRef.current !== es) return;
      markUnavailable();
    };

    return () => {
      if (generationRef.current === gen) {
        generationRef.current += 1;
      }
      es.removeEventListener("cursor", onCursor);
      es.removeEventListener("task-event", onTaskEvent);
      es.removeEventListener("cursor-stale", onCursorStale);
      es.removeEventListener("unavailable", onUnavailable);
      es.onmessage = null;
      es.onerror = null;
      if (sourceRef.current === es) {
        sourceRef.current = null;
      }
      try {
        es.close();
      } catch {
        /* ignore */
      }
    };
  }, [eligible, projectId, closeSource]);

  if (!eligible) return null;

  return (
    <div
      data-testid={testId}
      style={{ margin: "6px 0 0", minHeight: 4 }}
      aria-live="polite"
    >
      {panelPhase === "event" && safeEvent ? (
        <p style={{ margin: "4px 0 0", color: "var(--text, #0f172a)" }}>
          {safeEvent.typeLabel} · {safeEvent.statusLabel} · {safeEvent.progress}
          %
        </p>
      ) : null}
      {panelPhase === "unavailable" ? (
        <p style={{ margin: "4px 0 0", color: "var(--muted, #64748b)" }}>
          {UNAVAILABLE_TEXT}
        </p>
      ) : null}
    </div>
  );
}
