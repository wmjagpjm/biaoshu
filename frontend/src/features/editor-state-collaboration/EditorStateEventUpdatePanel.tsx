/**
 * 模块：P13-H3 编辑状态事件前端版本提示面板
 * 用途：在 required + authenticated + bid_writer 门控下连接项目级 editor-state SSE，
 *       严格解析四类事件；仅当远端 stateVersion 与当前已载入版本不同时展示固定提示，
 *       用户确认后单次调用页面传入的刷新函数；坏帧/控制帧/网络错误固定不可用。
 * 对接：useAuthSession；getApiBase；页面传入 projectId / stateVersion / onReload / testId；
 *       技术标 reloadFromApi({ blocking: true })；商务标 refreshFromApi()。
 * 二次开发：禁止自动 GET/PUT editor-state、禁止轮询重连、禁止展示 eventId/版本/正文/
 *       actor/projectId/后端 detail；禁止写入 storage/URL/Cookie/console；
 *       项目切换必须 close 旧连接并清空提示；迟到帧不得污染新项目。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthSession } from "../auth/hooks/useAuthSession";
import {
  isValidUtcMillisString,
  REVISION_SOURCE_KINDS,
  type RevisionSourceKind,
} from "../editor-state-revisions/editorStateRevisionApi";
import { getApiBase } from "../../shared/lib/api";

const UPDATE_TEXT = "检测到远端版本变化，请确认后重新载入";
const RELOAD_BTN = "重新载入远端内容";
const UNAVAILABLE_TEXT = "事件提示暂不可用";
const RELOAD_FAIL_TEXT = "重新载入失败，请稍后重试";

/** 合法 ese_ 事件 ID */
const ESE_RE = /^ese_[0-9a-f]{32}$/;
/** 合法 esv_ 状态版本 */
const ESV_RE = /^esv_[0-9a-f]{32}$/;

const SOURCE_KIND_SET = new Set<string>(REVISION_SOURCE_KINDS);

export type EditorStateEventUpdatePanelProps = {
  /** 当前路由项目 ID；空则不连接 */
  projectId: string;
  /** 当前会话已载入的 stateVersion；null 表示尚未接受合法版本 */
  stateVersion: string | null;
  /** 用户确认后的单次刷新；须返回真实 Promise<boolean> */
  onReload: () => Promise<boolean>;
  /** 固定 data-testid（技术/商务各一） */
  testId: string;
};

type PanelPhase = "idle" | "update" | "unavailable" | "reload_fail";

/**
 * 用途：从 raw 的 index 处解析一个 JSON 字符串字面量（含转义），返回解码后的内容与结束下标。
 * 约束：index 必须指向开引号；失败返回 null（交由 JSON.parse 判定整体合法性）。
 */
function readJsonStringLiteral(
  raw: string,
  index: number,
): { value: string; end: number } | null {
  if (index >= raw.length || raw[index] !== '"') return null;
  let i = index + 1;
  let value = "";
  while (i < raw.length) {
    const ch = raw[i];
    if (ch === '"') {
      return { value, end: i + 1 };
    }
    if (ch === "\\") {
      if (i + 1 >= raw.length) return null;
      const esc = raw[i + 1];
      // 标准 JSON 单字符转义
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
      // unicode \uXXXX
      if (esc === "u" && i + 6 <= raw.length) {
        const hex = raw.slice(i + 2, i + 6);
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) return null;
        value += String.fromCharCode(parseInt(hex, 16));
        i += 6;
        continue;
      }
      return null;
    }
    // JSON 禁止未转义控制字符；此处仅跳过，合法性仍由 JSON.parse 负责
    value += ch;
    i += 1;
  }
  return null;
}

/**
 * 用途：跳过 JSON 空白。
 */
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

/**
 * 用途：跳过一个完整 JSON 值（对象/数组/字符串/字面量/数字），正确处理嵌套与字符串转义。
 * 返回值结束后的下标；失败返回 -1。
 */
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

  // true / false / null
  if (raw.startsWith("true", i)) return i + 4;
  if (raw.startsWith("false", i)) return i + 5;
  if (raw.startsWith("null", i)) return i + 4;

  // number：宽松扫描到非数字词法字符；合法数字仍由 JSON.parse 判定
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

/**
 * 用途：在业务校验前检测顶层 JSON 对象是否存在重复键。
 * 约束：结构化扫描，正确跳过字符串内容与转义，不把值字符串里的键名字样误判为键；
 *       仅扫描顶层对象键；非对象/扫描失败返回 false（合法性仍由 JSON.parse 负责）。
 * 返回：true 表示检测到顶层重复键，必须按不可用处理。
 */
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
      // 顶层对象结束后只允许空白
      i = skipJsonWs(raw, i + 1);
      return false;
    }
    return false;
  }
  return false;
}

/**
 * 用途：严格解析 editor-state 事件 data；缺字段/多余字段/格式非法一律失败。
 * 约束：业务校验前拒绝原始 JSON 顶层重复键（JSON.parse 折叠前的结构化扫描）。
 */
function parseEditorStateData(
  lastEventId: string,
  raw: string,
): { stateVersion: string } | null {
  // 重复键：在 JSON.parse 折叠前拒绝（最终折叠值合法也不可用）
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
  const keys = Object.keys(obj);
  if (keys.length !== 4) return null;
  for (const required of [
    "eventId",
    "stateVersion",
    "sourceKind",
    "occurredAt",
  ] as const) {
    if (!Object.prototype.hasOwnProperty.call(obj, required)) return null;
  }
  if (typeof obj.eventId !== "string" || !ESE_RE.test(obj.eventId)) return null;
  if (typeof lastEventId !== "string" || !ESE_RE.test(lastEventId)) return null;
  if (lastEventId !== obj.eventId) return null;
  if (typeof obj.stateVersion !== "string" || !ESV_RE.test(obj.stateVersion)) {
    return null;
  }
  if (
    typeof obj.sourceKind !== "string" ||
    !SOURCE_KIND_SET.has(obj.sourceKind as RevisionSourceKind)
  ) {
    return null;
  }
  if (!isValidUtcMillisString(obj.occurredAt)) {
    return null;
  }
  return { stateVersion: obj.stateVersion };
}

/**
 * 用途：严格解析 cursor 帧；合法时仅作水位，非法一律失败。
 * 约束：lastEventId 合法 ese_；data 为合法 JSON 对象且精确单键 eventId；
 *       eventId 与 lastEventId 相等；禁止额外键/缺键/非法 ID；
 *       业务校验前拒绝原始 JSON 顶层重复键。
 */
function parseCursorData(lastEventId: string, raw: string): boolean {
  // 重复键：在 JSON.parse 折叠前拒绝（最终折叠值合法也不可用）
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
  const keys = Object.keys(obj);
  if (keys.length !== 1) return false;
  if (!Object.prototype.hasOwnProperty.call(obj, "eventId")) return false;
  if (typeof obj.eventId !== "string" || !ESE_RE.test(obj.eventId)) return false;
  if (typeof lastEventId !== "string" || !ESE_RE.test(lastEventId)) return false;
  if (lastEventId !== obj.eventId) return false;
  return true;
}

/**
 * 用途：标题区事件版本提示；仅门控通过时建连，用户确认后单次刷新。
 */
export function EditorStateEventUpdatePanel({
  projectId,
  stateVersion,
  onReload,
  testId,
}: EditorStateEventUpdatePanelProps) {
  const { phase, authRequired, activeMembership } = useAuthSession();
  const eligible =
    authRequired === true &&
    phase === "authenticated" &&
    activeMembership?.role === "bid_writer" &&
    Boolean(projectId);

  const [panelPhase, setPanelPhase] = useState<PanelPhase>("idle");
  const [reloading, setReloading] = useState(false);

  /** 最新已载入版本；避免 version 变化触发重连 */
  const stateVersionRef = useRef(stateVersion);
  stateVersionRef.current = stateVersion;

  /** 项目/门控代次：迟到事件与迟到刷新不得写新代次 UI */
  const generationRef = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);
  const onReloadRef = useRef(onReload);
  onReloadRef.current = onReload;
  /**
   * 同步单飞门：同批次连续 click 在 setState 提交前也会被挡住，
   * 保证 onReload 最多调用一次；成功/失败/代次切换后释放。
   */
  const reloadingRef = useRef(false);

  const closeSource = useCallback(() => {
    const src = sourceRef.current;
    sourceRef.current = null;
    if (src) {
      try {
        src.close();
      } catch {
        /* 关闭失败忽略 */
      }
    }
  }, []);

  useEffect(() => {
    if (!eligible) {
      generationRef.current += 1;
      closeSource();
      setPanelPhase("idle");
      reloadingRef.current = false;
      setReloading(false);
      return;
    }

    const gen = ++generationRef.current;
    const activePid = projectId;
    setPanelPhase("idle");
    reloadingRef.current = false;
    setReloading(false);
    closeSource();

    const isCurrent = () =>
      generationRef.current === gen && sourceRef.current !== null;

    const markUnavailable = () => {
      if (generationRef.current !== gen) return;
      setPanelPhase("unavailable");
      closeSource();
    };

    const url = `${getApiBase()}/projects/${encodeURIComponent(activePid)}/editor-state-events/stream`;

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
      // 合法 cursor 仅水位：不展示刷新提示、不写 stateVersion
      if (!parseCursorData(me.lastEventId, me.data)) {
        markUnavailable();
      }
    };

    const onEditorState = (ev: Event) => {
      if (!isCurrent() || sourceRef.current !== es) return;
      const me = ev as MessageEvent<string>;
      const parsed = parseEditorStateData(me.lastEventId, me.data);
      if (!parsed) {
        markUnavailable();
        return;
      }
      const current = stateVersionRef.current;
      // 尚无已载入版本时不做提示，避免 GET 完成前误报
      if (current == null) return;
      if (parsed.stateVersion !== current) {
        setPanelPhase("update");
      }
    };

    const onControlUnavailable = () => {
      if (!isCurrent() || sourceRef.current !== es) return;
      markUnavailable();
    };

    es.addEventListener("cursor", onCursor);
    es.addEventListener("editor-state", onEditorState);
    es.addEventListener("cursor-stale", onControlUnavailable);
    es.addEventListener("unavailable", onControlUnavailable);

    // 缺省 message / 非四类但进入 onmessage 的帧 → 不可用
    // 注：原生 EventSource 不会把未注册的命名 event 投递给 onmessage；
    // 未知命名 event 对本组件不可观测（契约裁定见 review_request）。
    es.onmessage = () => {
      if (!isCurrent() || sourceRef.current !== es) return;
      markUnavailable();
    };

    // 网络错误：固定不可用并 close，禁止浏览器无限重连
    es.onerror = () => {
      if (generationRef.current !== gen) return;
      // 已主动 close 后部分浏览器仍回调 onerror，忽略
      if (sourceRef.current !== es) return;
      markUnavailable();
    };

    return () => {
      if (generationRef.current === gen) {
        generationRef.current += 1;
      }
      reloadingRef.current = false;
      es.removeEventListener("cursor", onCursor);
      es.removeEventListener("editor-state", onEditorState);
      es.removeEventListener("cursor-stale", onControlUnavailable);
      es.removeEventListener("unavailable", onControlUnavailable);
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

  const handleReload = useCallback(async () => {
    // 同步门：同拍连续 click 在 React state 提交前也只放行一次
    if (reloadingRef.current) return;
    reloadingRef.current = true;
    const gen = generationRef.current;
    setReloading(true);
    try {
      const ok = await onReloadRef.current();
      if (generationRef.current !== gen) return;
      if (ok) {
        setPanelPhase("idle");
      } else {
        setPanelPhase("reload_fail");
      }
    } catch {
      if (generationRef.current !== gen) return;
      setPanelPhase("reload_fail");
    } finally {
      if (generationRef.current === gen) {
        reloadingRef.current = false;
        setReloading(false);
      }
    }
  }, []);

  if (!eligible) return null;

  const showUpdate =
    panelPhase === "update" || panelPhase === "reload_fail";
  const showUnavailable = panelPhase === "unavailable";
  const showReloadFail = panelPhase === "reload_fail";

  return (
    <div
      data-testid={testId}
      style={{ margin: "6px 0 0", minHeight: 4 }}
      aria-live="polite"
    >
      {showUpdate ? (
        <div style={{ marginTop: 4 }}>
          <p style={{ margin: "0 0 6px", color: "var(--warning, #b45309)" }}>
            {UPDATE_TEXT}
          </p>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={reloading}
            onClick={() => {
              void handleReload();
            }}
          >
            {RELOAD_BTN}
          </button>
        </div>
      ) : null}
      {showReloadFail ? (
        <p
          style={{
            margin: "6px 0 0",
            color: "var(--danger)",
          }}
        >
          {RELOAD_FAIL_TEXT}
        </p>
      ) : null}
      {showUnavailable ? (
        <p
          style={{
            margin: "4px 0 0",
            color: "var(--muted, #64748b)",
          }}
        >
          {UNAVAILABLE_TEXT}
        </p>
      ) : null}
    </div>
  );
}
