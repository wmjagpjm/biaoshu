/**
 * 模块：技术方案大纲 / 正文 / 全局事实 / 分析概述（P11C + P12B 全状态 CAS + P13-B 版本时间）
 * 用途：技术标编辑内容只认 GET|PUT /api/projects/{id}/editor-state；真实空态保持空；
 *       全部 editor-state PUT 携带服务端 expectedStateVersion，同项目串行队列；
 *       P12B-C3 受限「版本化外部写」runner：M3-D apply/consume 与 PUT 共用 matrixSaveChainRef；
 *       P13-B：在合法 stateVersion 被当前会话接受时同步 versionUpdatedAt 供标题区展示。
 * 对接：editor-state API；页面 TechnicalPlanWorkspace；responseMatrixVersion 乐观锁；
 *       全状态 409 code=editor_state_version_conflict；矩阵 409 字段级三方合并；
 *       guidance 纳入主状态同一队列；getCsrfToken 内存 CSRF；ContentFuseDialog；
 *       EditorStateVersionFreshness（只读展示，零额外请求）。
 * 明确非目标：
 *   - 禁止读写/删除/迁移 biaoshu.technicalPlan.editors.*（旧键忽略并保值）
 *   - 禁止生产路径导入 mock 或字段 fallback 伪装成功
 *   - 禁止本地计算 stateVersion；禁止版本落盘/URL/Cookie/console
 *   - versionUpdatedAt 禁止参与 CAS/保存队列/矩阵版本/缓存键
 * 二次开发：矩阵 409 时禁止静默覆盖本地；须用户显式「重新载入远端矩阵」或「应用合并」；
 *       应用合并 PUT 仅含 responseMatrix + responseMatrixVersion + expectedStateVersion；
 *       全状态冲突时禁止矩阵旁路解除阻断；项目切换后须丢弃过期合并/409 异步结果；
 *       M3-D 不得旁路 runner 直连 POST；切项目须立即清空 versionUpdatedAt。
 */

/** 用途：版本化外部写（M3-D apply/consume）结果；Dialog 据此分流文案。 */
export type VersionedExternalWriteOutcome<T> =
  | { status: "success"; data: T }
  | { status: "post_failed"; blocked: boolean }
  | { status: "reload_failed"; data: T };

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createEditorStateCheckpoint as postCreateEditorStateCheckpoint,
  isCheckpointCreateStateVersionError,
  restoreEditorStateCheckpoint as postRestoreEditorStateCheckpoint,
} from "../../editor-state-checkpoints/editorStateCheckpointApi";
import type {
  CheckpointCreateOutcome,
  CheckpointRestoreOutcome,
} from "../../editor-state-checkpoints/EditorStateCheckpointPanel";
import { restoreEditorStateRevision as postRestoreEditorStateRevision } from "../../editor-state-revisions/editorStateRevisionApi";
import type { RevisionRestoreOutcome } from "../../editor-state-revisions/EditorStateRevisionPanel";
import {
  apiFetch,
  getApiBase,
  getCsrfToken,
} from "../../../shared/lib/api";
import type { ProjectGenerationGuidance } from "../../../shared/types/aiFeedback";
import {
  addChild,
  addSibling,
  canMove,
  countTargetWords,
  moveNodeAmongSiblings,
  removeNode,
  updateNode,
} from "../lib/outlineTree";
import {
  cloneResponseMatrix,
  mergeResponseMatrix,
  normalizeResponseMatrix,
  reconcileResponseMatrixLinks,
  resolveResponseMatrixThreeWayChoices,
  sameResponseMatrixEditableSnapshot,
  threeWayMergeResponseMatrix,
  type ResponseMatrixConflictChoice,
  type ResponseMatrixThreeWayMergeResult,
} from "../lib/responseMatrix";
import type {
  BidAnalysis,
  ChapterContent,
  GlobalFact,
  OutlineExpansionMode,
  OutlineNode,
  ResponseMatrixItem,
  ResponseMatrixSuggestion,
} from "../types";
import { emptyBidAnalysis } from "../types";

/** 固定加载失败文案（脱敏，不得拼接后端原文） */
export const TECHNICAL_EDITOR_LOAD_ERROR =
  "技术标工作区加载失败，请稍后重试";

/** 固定保存失败文案（脱敏，不得拼接后端原文） */
export const TECHNICAL_EDITOR_SAVE_ERROR =
  "技术标工作区保存失败，请稍后重试";

/** 固定矩阵冲突文案（不得回显服务端 detail/SECRET） */
const TECHNICAL_MATRIX_CONFLICT_MESSAGE =
  "响应矩阵已被其他终端更新，请重新载入后再保存";

/**
 * 固定全状态版本冲突文案。
 * 用途：P12B CAS 冲突固定中文；禁止拼接服务端 message/version/正文/ID。
 */
export const TECHNICAL_EDITOR_STATE_CONFLICT_MESSAGE =
  "编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。";

/** 服务端 stateVersion 精确格式 */
const STATE_VERSION_RE = /^esv_[0-9a-f]{32}$/;

/** 用途：校验服务端 stateVersion；不得本地生成或用 updatedAt 替代。 */
function isValidStateVersion(value: unknown): value is string {
  return typeof value === "string" && STATE_VERSION_RE.test(value);
}

/**
 * 用途：判断 409 detail 是否携带真实矩阵冲突明细。
 * 对接：普通/合并 PUT 409 分流；仅数组矩阵 + 非空版本串才进矩阵 UX。
 * 二次开发：禁止把缺失明细当成 remoteMatrix=[] 伪造空矩阵冲突。
 */
function hasRealMatrixConflictDetail(detail: {
  responseMatrix?: ResponseMatrixItem[];
  currentResponseMatrixVersion?: string;
} | null | undefined): detail is {
  responseMatrix: ResponseMatrixItem[];
  currentResponseMatrixVersion: string;
} {
  if (!detail || !Array.isArray(detail.responseMatrix)) return false;
  const version = detail.currentResponseMatrixVersion;
  return typeof version === "string" && version.trim() !== "";
}

/** 用途：guidance 默认空态；与 useProjectGuidance 历史语义对齐但不读 localStorage。 */
function emptyGuidance(): ProjectGenerationGuidance {
  return {
    targetWordCount: 80000,
    chapterFocus: "",
    formatRequirements: "",
    extraRequirements: "",
    lockedForNextStage: false,
    kbEnabled: true,
    kbFolderIds: [],
  };
}

type StoredEditors = {
  outline: OutlineNode[];
  chapters: ChapterContent[];
  facts: GlobalFact[];
  mode: OutlineExpansionMode;
  analysisOverview: string;
  analysis: BidAnalysis;
  responseMatrix: ResponseMatrixItem[];
  parsedMarkdown: string;
  /** 服务端权威生成约束；与 outline/chapters 同源水合 */
  guidance: ProjectGenerationGuidance;
};

type EditorStateApi = {
  projectId: string;
  outline?: OutlineNode[] | null;
  chapters?: ChapterContent[] | null;
  facts?: GlobalFact[] | null;
  mode?: string;
  analysisOverview?: string | null;
  analysis?: BidAnalysis | null;
  responseMatrix?: ResponseMatrixItem[] | null;
  responseMatrixVersion?: string | null;
  parsedMarkdown?: string | null;
  guidance?: ProjectGenerationGuidance | Record<string, unknown> | null;
  /** P12B：全状态版本；仅接受 ^esv_[0-9a-f]{32}$ */
  stateVersion?: string | null;
  updatedAt?: string | null;
};

/** 用途：响应矩阵多端冲突时保留本地、展示远端快照；可选附带三方合并预览。 */
export type ResponseMatrixConflict = {
  message: string;
  remoteMatrix: ResponseMatrixItem[];
  remoteVersion: string;
  /** 仅当 baseVersion 匹配请求版本且请求后本地未再改时生成 */
  mergePreview?: ResponseMatrixThreeWayMergeResult | null;
  /**
   * 应用合并失败时的可恢复提示。
   * 二次 409 时 mergePreview 会被清空，仍依赖本字段在冲突条内展示恢复路径。
   */
  applyError?: string | null;
};

/** 用途：面板展示用的合并预览与冲突选择状态。 */
export type ResponseMatrixMergeUi = {
  preview: ResponseMatrixThreeWayMergeResult;
  remoteVersion: string;
  choices: Record<string, ResponseMatrixConflictChoice>;
  applyError: string | null;
  applying: boolean;
};

function uniqueIds(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function sameIds(left: string[], right: string[]): boolean {
  const a = uniqueIds(left).sort();
  const b = uniqueIds(right).sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function normalizeAnalysis(
  raw: Partial<BidAnalysis> | null | undefined,
  overviewFallback = "",
): BidAnalysis {
  const base = emptyBidAnalysis();
  if (!raw || typeof raw !== "object") {
    base.overview = overviewFallback;
    return base;
  }
  base.overview = String(raw.overview ?? overviewFallback ?? "");
  base.techRequirements = Array.isArray(raw.techRequirements)
    ? raw.techRequirements.map(String)
    : [];
  base.rejectionRisks = Array.isArray(raw.rejectionRisks)
    ? raw.rejectionRisks.map(String)
    : [];
  base.scoringPoints = Array.isArray(raw.scoringPoints)
    ? raw.scoringPoints.map((p) =>
        typeof p === "object" && p
          ? {
              name: String((p as { name?: string }).name ?? ""),
              weight: String((p as { weight?: string }).weight ?? ""),
            }
          : { name: String(p), weight: "" },
      )
    : [];
  return base;
}

function derivePreview(body: string): string {
  const plain = body
    .replace(/^#+\s*/gm, "")
    .replace(/[|>*`_-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return plain.slice(0, 96) || "（空正文）";
}

function countBodyWords(body: string): number {
  return body.replace(/\s/g, "").length;
}

/**
 * 用途：内存空编辑态；不含 mock、不读 localStorage。
 * 对接：初始 state、切项目重置、GET 失败重置。
 */
function createEmptyEditors(): StoredEditors {
  return {
    outline: [],
    chapters: [],
    facts: [],
    mode: "ALIGNED",
    analysisOverview: "",
    analysis: emptyBidAnalysis(),
    responseMatrix: [],
    parsedMarkdown: "",
    guidance: emptyGuidance(),
  };
}

/**
 * 用途：远端 editor-state → 编辑态；null/[]/空串/updatedAt=null 均为权威空态。
 * 对接：仅消费同一响应内 analysis + responseMatrix + guidance 做既有 merge/reconcile。
 * 二次开发：禁止引入响应外 fallback/mock；guidance 不得从 localStorage 水合。
 */
function fromApi(data: EditorStateApi): StoredEditors {
  const analysis = normalizeAnalysis(
    data.analysis,
    data.analysisOverview || "",
  );
  const outline = Array.isArray(data.outline)
    ? (data.outline as OutlineNode[])
    : [];
  const chapters = Array.isArray(data.chapters)
    ? (data.chapters as ChapterContent[]).map((c) => ({
        ...c,
        body: c.body ?? "",
        preview: c.preview ?? derivePreview(c.body ?? ""),
      }))
    : [];
  const facts = Array.isArray(data.facts)
    ? (data.facts as GlobalFact[]).map((f) => ({ ...f }))
    : [];
  const remoteMatrix = Array.isArray(data.responseMatrix)
    ? data.responseMatrix
    : [];
  const responseMatrix = reconcileResponseMatrixLinks(
    mergeResponseMatrix(analysis, remoteMatrix),
    chapters,
    outline,
  );
  const guidanceRaw =
    data.guidance && typeof data.guidance === "object" ? data.guidance : null;
  return {
    outline,
    chapters,
    facts,
    mode: data.mode === "FREE" ? "FREE" : "ALIGNED",
    analysisOverview: analysis.overview || data.analysisOverview || "",
    analysis,
    responseMatrix,
    parsedMarkdown:
      typeof data.parsedMarkdown === "string" ? data.parsedMarkdown : "",
    guidance: guidanceRaw
      ? { ...emptyGuidance(), ...(guidanceRaw as ProjectGenerationGuidance) }
      : emptyGuidance(),
  };
}

/**
 * 用途：同源 PUT editor-state；携带内存 CSRF，credentials=same-origin。
 * 对接：普通防抖 PUT 与矩阵合并 PUT；禁止读 Cookie/存储或输出 Token。
 */
async function putEditorState(
  projectId: string,
  body: unknown,
): Promise<Response> {
  const path = `${getApiBase()}/projects/${encodeURIComponent(projectId)}/editor-state`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const csrf = getCsrfToken();
  if (csrf) {
    headers["X-CSRF-Token"] = csrf;
  }
  return fetch(path, {
    method: "PUT",
    headers,
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
}

function syncChapterTitles(
  chapters: ChapterContent[],
  outline: OutlineNode[],
): ChapterContent[] {
  const titleById = new Map<string, string>();
  const walk = (nodes: OutlineNode[]) => {
    for (const n of nodes) {
      titleById.set(n.id, n.title);
      if (n.children) walk(n.children);
    }
  };
  walk(outline);
  return chapters.map((c) => {
    const t = titleById.get(c.id);
    return t && t !== c.title ? { ...c, title: t } : c;
  });
}

/** 大纲树转 Markdown 文本，供 revise baseContent */
export function outlineToMarkdown(nodes: OutlineNode[], depth = 1): string {
  const lines: string[] = [];
  for (const n of nodes) {
    lines.push(`${"#".repeat(Math.min(depth, 6))} ${n.title}`);
    if (n.description) lines.push(n.description);
    if (n.targetWords) lines.push(`（目标字数：${n.targetWords}）`);
    if (n.children?.length) {
      lines.push(outlineToMarkdown(n.children, depth + 1));
    }
  }
  return lines.join("\n");
}

/** 事实列表转文本 */
export function factsToText(facts: GlobalFact[]): string {
  return facts
    .map((f) => `- [${f.category}] ${f.content}${f.source ? `（${f.source}）` : ""}`)
    .join("\n");
}

export function useTechnicalPlanEditors(projectId: string) {
  const [state, setState] = useState<StoredEditors>(() => createEmptyEditors());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  /** 当前项目初始 GET 成功且会话有效后才允许 PUT */
  const [apiReady, setApiReady] = useState(false);
  const [matrixVersion, setMatrixVersion] = useState<string | null>(null);
  /**
   * 全状态冲突/版本未知阻断。
   * 用途：保留本地内容；阻断全部 editor-state PUT；仅显式全量重载可恢复。
   */
  const [fullStateConflict, setFullStateConflict] = useState(false);
  /**
   * P13-B：当前项目会话已接受的服务端 updatedAt（仅展示）。
   * 切项目立即清空；仅在合法 stateVersion 被接受时更新。
   */
  const [versionUpdatedAt, setVersionUpdatedAt] = useState<string | null>(null);
  const [matrixConflict, setMatrixConflict] =
    useState<ResponseMatrixConflict | null>(null);
  const [mergeChoices, setMergeChoices] = useState<
    Record<string, ResponseMatrixConflictChoice>
  >({});
  const [mergeApplying, setMergeApplying] = useState(false);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(
    null,
  );
  const [selectedOutlineId, setSelectedOutlineId] = useState<string | null>(
    null,
  );
  const skipNextSave = useRef(true);
  const saveTimer = useRef<number | null>(null);
  /**
   * 合并成功后的 setState 会触发本 hook 的普通防抖保存 effect；
   * 置 true 时跳过下一次全量 PUT（避免把 analysis/outline/chapters/facts 回写远端）。
   */
  const skipNextAutosavePutRef = useRef(false);
  /** 409 后停止携带旧版本写矩阵，直至用户显式载入远端或应用合并 */
  const matrixPutBlockedRef = useRef(false);
  /** 全状态冲突或 200 缺/非法版本后阻断全部 editor-state PUT */
  const fullStateBlockedRef = useRef(false);
  /**
   * 创建/恢复操作 token（项目绑定）：
   * - 同项目连点：token 已属本项目 → 拒绝第二请求
   * - 不同项目：允许新项目启动并把 ref 覆盖为新 token
   * - finally 仅当 projectId+token 仍匹配时清空，绝不能误清新项目 token
   * 禁止：跨项目共享 boolean，或切项目时简单 boolean=false
   */
  const checkpointOpTokenRef = useRef<{
    projectId: string;
    token: number;
  } | null>(null);
  const checkpointOpTokenSeqRef = useRef(0);
  const matrixVersionRef = useRef<string | null>(null);
  /** 当前项目内存中的服务端全状态版本；禁止落盘 */
  const stateVersionRef = useRef<string | null>(null);
  /** 成功同步后的 base 矩阵深拷贝；project 切换/卸载清空 */
  const matrixBaseRef = useRef<ResponseMatrixItem[] | null>(null);
  /** base 对应的 responseMatrixVersion */
  const matrixBaseVersionRef = useRef<string | null>(null);
  /** 最新编辑态：防抖/串行保存避免闭包过期 */
  const stateRef = useRef(state);
  stateRef.current = state;
  /**
   * 当前项目与会话代际：projectId 切换时递增；
   * 异步 PUT/合并返回后须匹配，否则静默丢弃，避免污染新项目。
   */
  const activeProjectIdRef = useRef(projectId);
  const projectSessionRef = useRef(0);
  activeProjectIdRef.current = projectId;
  /**
   * 同项目写入代次：显式/任务重载时递增；旧代次 PUT 回调不得覆盖新 GET 的版本/冲突。
   */
  const writeEpochRef = useRef(0);
  /**
   * 当前项目的矩阵/整包/guidance PUT 串行链：同项目飞行中排队；
   * 完成后用最新 state + 新 version 再保存，避免同页误 409。
   * 切项目时必须重置为已解决链，禁止被旧项目挂起 Promise 队头阻塞。
   */
  const matrixSaveChainRef = useRef(Promise.resolve());

  /** 用途：判断异步请求是否仍属于当前 hook 项目会话。 */
  const isCurrentEditorSession = useCallback(
    (requestProjectId: string, requestSession: number) =>
      activeProjectIdRef.current === requestProjectId &&
      projectSessionRef.current === requestSession,
    [],
  );

  /** 用途：判断是否仍属于当前会话且同一写入代次。 */
  const isCurrentWriteEpoch = useCallback(
    (
      requestProjectId: string,
      requestSession: number,
      requestEpoch: number,
    ) =>
      isCurrentEditorSession(requestProjectId, requestSession) &&
      writeEpochRef.current === requestEpoch,
    [isCurrentEditorSession],
  );

  const applyMatrixVersion = useCallback((version: string | null | undefined) => {
    const next = version && String(version).trim() ? String(version).trim() : null;
    matrixVersionRef.current = next;
    setMatrixVersion(next);
  }, []);

  /** 用途：仅接受合法服务端 stateVersion 写入内存 ref。 */
  const applyStateVersion = useCallback((version: unknown) => {
    if (!isValidStateVersion(version)) {
      stateVersionRef.current = null;
      return false;
    }
    stateVersionRef.current = version;
    return true;
  }, []);

  /**
   * 用途：P13-B 在合法 stateVersion 已被接受后，同步同一响应的 updatedAt 供展示。
   * 对接：仅 string 原样接受；null/缺失/非字符串记为 null（组件显示未知）；
   *       不得用 updatedAt 替代 stateVersion。
   */
  const acceptVersionUpdatedAt = useCallback((updatedAt: unknown) => {
    setVersionUpdatedAt(typeof updatedAt === "string" ? updatedAt : null);
  }, []);

  /**
   * 用途：进入全状态阻断；保留本地 UI；禁止自动重试。
   * 对接：CAS 409、PUT 200 缺/非法版本。
   */
  const enterFullStateBlock = useCallback(() => {
    fullStateBlockedRef.current = true;
    setFullStateConflict(true);
    setSaveError(null);
  }, []);

  /** 用途：仅在成功 GET / 成功带矩阵 PUT / 显式载入远端时更新 base 快照。 */
  const snapshotMatrixBase = useCallback(
    (matrix: ResponseMatrixItem[], version: string | null | undefined) => {
      const nextVersion =
        version && String(version).trim() ? String(version).trim() : null;
      matrixBaseRef.current = cloneResponseMatrix(matrix);
      matrixBaseVersionRef.current = nextVersion;
    },
    [],
  );

  const clearMatrixBase = useCallback(() => {
    matrixBaseRef.current = null;
    matrixBaseVersionRef.current = null;
  }, []);

  // 切项目：立即作废旧会话、清计时器/错误/冲突/版本，重置空编辑态后唯一 GET
  useEffect(() => {
    let cancelled = false;
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const session = ++projectSessionRef.current;
    writeEpochRef.current += 1;
    activeProjectIdRef.current = projectId;
    // 新项目保存链与旧项目解耦：旧 A 挂起 PUT 不得阻塞 B 的合法防抖保存
    matrixSaveChainRef.current = Promise.resolve();
    skipNextSave.current = true;
    skipNextAutosavePutRef.current = false;
    matrixPutBlockedRef.current = false;
    fullStateBlockedRef.current = false;
    stateVersionRef.current = null;
    // P13-B：切项目立即清空，禁止短暂显示旧项目时间
    setVersionUpdatedAt(null);
    clearMatrixBase();
    applyMatrixVersion(null);
    setMatrixConflict(null);
    setFullStateConflict(false);
    setMergeChoices({});
    setMergeApplying(false);
    setApiReady(false);
    setLoadError(null);
    setSaveError(null);
    setState(createEmptyEditors());
    setSelectedOutlineId(null);
    setSelectedChapterId(null);
    setLoading(true);

    void (async () => {
      try {
        const remote = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(projectId)}/editor-state`,
        );
        if (cancelled || !isCurrentEditorSession(projectId, session)) return;
        // 缺失/非法 stateVersion：固定加载失败、零 PUT、无 mock fallback
        if (!isValidStateVersion(remote.stateVersion)) {
          skipNextSave.current = true;
          setState(createEmptyEditors());
          applyMatrixVersion(null);
          clearMatrixBase();
          stateVersionRef.current = null;
          setSelectedOutlineId(null);
          setSelectedChapterId(null);
          setApiReady(false);
          setFullStateConflict(false);
          fullStateBlockedRef.current = false;
          setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
          setSaveError(null);
          return;
        }
        const next = fromApi(remote);
        skipNextSave.current = true;
        setState(next);
        applyStateVersion(remote.stateVersion);
        acceptVersionUpdatedAt(remote.updatedAt);
        applyMatrixVersion(remote.responseMatrixVersion);
        snapshotMatrixBase(next.responseMatrix, remote.responseMatrixVersion);
        setSelectedOutlineId(next.outline[0]?.id ?? null);
        setSelectedChapterId(null);
        setApiReady(true);
        setFullStateConflict(false);
        fullStateBlockedRef.current = false;
        setLoadError(null);
        setSaveError(null);
      } catch {
        if (cancelled || !isCurrentEditorSession(projectId, session)) return;
        skipNextSave.current = true;
        setState(createEmptyEditors());
        applyMatrixVersion(null);
        clearMatrixBase();
        stateVersionRef.current = null;
        setSelectedOutlineId(null);
        setSelectedChapterId(null);
        setApiReady(false);
        setFullStateConflict(false);
        fullStateBlockedRef.current = false;
        setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
        setSaveError(null);
      } finally {
        if (!cancelled && isCurrentEditorSession(projectId, session)) {
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      // 卸载/切换：作废本会话，使飞行中的合并/409 写回失效
      if (projectSessionRef.current === session) {
        projectSessionRef.current += 1;
      }
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      clearMatrixBase();
    };
  }, [
    projectId,
    applyMatrixVersion,
    applyStateVersion,
    acceptVersionUpdatedAt,
    snapshotMatrixBase,
    clearMatrixBase,
    isCurrentEditorSession,
  ]);

  // 保存：仅 apiReady 后 debounce PUT；全状态 expected + 矩阵版本串行；409 分流；禁止 localStorage

  /**
   * 用途：共享「构造最新 body + 执行 PUT + 接受版本/冲突处理」执行器。
   * 对接：普通防抖 autosave 与显式创建检查点强制即时 PUT 共用；禁止第二套 body。
   * 二次开发：真正执行时读 stateRef + stateVersionRef；不在此 commit 检查点。
   */
  type ImmediatePutStatus =
    | "ok"
    | "stale"
    | "blocked"
    | "full_conflict"
    | "matrix_conflict"
    | "error"
    | "invalid_version";

  const executeImmediateEditorStatePut = useCallback(
    async (
      requestProjectId: string,
      requestSession: number,
      requestEpoch: number,
    ): Promise<ImmediatePutStatus> => {
      if (
        !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
      ) {
        return "stale";
      }
      if (fullStateBlockedRef.current) {
        return "blocked";
      }
      const expected = stateVersionRef.current;
      if (!isValidStateVersion(expected)) {
        enterFullStateBlock();
        return "invalid_version";
      }
      const latest = stateRef.current;
      const body: Record<string, unknown> = {
        outline: latest.outline,
        chapters: latest.chapters,
        facts: latest.facts,
        mode: latest.mode,
        analysisOverview: latest.analysis.overview || latest.analysisOverview,
        analysis: latest.analysis,
        guidance: latest.guidance,
        expectedStateVersion: expected,
      };
      const includeMatrix = !matrixPutBlockedRef.current;
      const matrixAtRequest = includeMatrix
        ? cloneResponseMatrix(latest.responseMatrix)
        : null;
      const versionAtRequest = matrixVersionRef.current;
      if (includeMatrix) {
        body.responseMatrix = matrixAtRequest;
        if (versionAtRequest) {
          body.responseMatrixVersion = versionAtRequest;
        }
      }
      try {
        const res = await putEditorState(requestProjectId, body);
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return "stale";
        }
        if (res.status === 409) {
          const raw = (await res.json().catch(() => null)) as {
            detail?: {
              code?: string;
              message?: string;
              responseMatrix?: ResponseMatrixItem[];
              currentResponseMatrixVersion?: string;
              currentStateVersion?: string;
            };
          } | null;
          if (
            !isCurrentWriteEpoch(
              requestProjectId,
              requestSession,
              requestEpoch,
            )
          ) {
            return "stale";
          }
          const detail = raw?.detail;
          if (detail?.code === "editor_state_version_conflict") {
            enterFullStateBlock();
            return "full_conflict";
          }
          if (!hasRealMatrixConflictDetail(detail)) {
            setSaveError(TECHNICAL_EDITOR_SAVE_ERROR);
            return "error";
          }
          const remoteMatrix = normalizeResponseMatrix(detail.responseMatrix);
          const remoteVersion = detail.currentResponseMatrixVersion.trim();
          matrixPutBlockedRef.current = true;

          const base = matrixBaseRef.current;
          const baseVersion = matrixBaseVersionRef.current;
          const localUnchanged =
            matrixAtRequest != null &&
            sameResponseMatrixEditableSnapshot(
              stateRef.current.responseMatrix,
              matrixAtRequest,
            );
          const canThreeWay =
            Boolean(base) &&
            Boolean(baseVersion) &&
            Boolean(versionAtRequest) &&
            baseVersion === versionAtRequest &&
            localUnchanged &&
            matrixAtRequest != null;

          let mergePreview: ResponseMatrixThreeWayMergeResult | null = null;
          if (canThreeWay && base && matrixAtRequest) {
            mergePreview = threeWayMergeResponseMatrix(
              base,
              matrixAtRequest,
              remoteMatrix,
            );
          }

          setMergeChoices({});
          setMatrixConflict({
            message: TECHNICAL_MATRIX_CONFLICT_MESSAGE,
            remoteMatrix,
            remoteVersion,
            mergePreview,
            applyError: null,
          });
          return "matrix_conflict";
        }
        if (!res.ok) {
          if (
            !isCurrentWriteEpoch(
              requestProjectId,
              requestSession,
              requestEpoch,
            )
          ) {
            return "stale";
          }
          setSaveError(TECHNICAL_EDITOR_SAVE_ERROR);
          return "error";
        }
        const saved = (await res.json()) as EditorStateApi;
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return "stale";
        }
        if (!isValidStateVersion(saved.stateVersion)) {
          enterFullStateBlock();
          return "invalid_version";
        }
        applyStateVersion(saved.stateVersion);
        acceptVersionUpdatedAt(saved.updatedAt);
        setSaveError(null);
        if (saved.responseMatrixVersion) {
          applyMatrixVersion(saved.responseMatrixVersion);
        }
        if (includeMatrix && matrixAtRequest) {
          const savedMatrix = Array.isArray(saved.responseMatrix)
            ? normalizeResponseMatrix(saved.responseMatrix)
            : matrixAtRequest;
          snapshotMatrixBase(
            savedMatrix,
            saved.responseMatrixVersion ?? versionAtRequest,
          );
        }
        return "ok";
      } catch {
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return "stale";
        }
        setSaveError(TECHNICAL_EDITOR_SAVE_ERROR);
        return "error";
      }
    },
    [
      isCurrentWriteEpoch,
      enterFullStateBlock,
      applyStateVersion,
      acceptVersionUpdatedAt,
      applyMatrixVersion,
      snapshotMatrixBase,
    ],
  );

  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    // 合并成功写回后跳过下一次普通整包 PUT（防止 analysis/outline 等回写远端）
    if (skipNextAutosavePutRef.current) {
      skipNextAutosavePutRef.current = false;
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      return;
    }
    // 全状态阻断期间：继续编辑也不自动重试 PUT
    if (fullStateBlockedRef.current) {
      return;
    }
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      const requestProjectId = projectId;
      const requestSession = projectSessionRef.current;
      const requestEpoch = writeEpochRef.current;
      const runSave = async () => {
        await executeImmediateEditorStatePut(
          requestProjectId,
          requestSession,
          requestEpoch,
        );
      };

      // 串行：上一矩阵/整包 PUT 完成并更新 version 后，再用最新 state 发出
      matrixSaveChainRef.current = matrixSaveChainRef.current
        .catch(() => undefined)
        .then(runSave);
    }, 800);
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [
    projectId,
    state,
    apiReady,
    executeImmediateEditorStatePut,
  ]);

  const targetWordsTotal = useMemo(
    () => countTargetWords(state.outline),
    [state.outline],
  );

  const selectedChapter: ChapterContent | undefined =
    state.chapters.find((c) => c.id === selectedChapterId) ??
    state.chapters.find((c) => c.status === "done") ??
    state.chapters[0];

  const moveFlags = useMemo(
    () =>
      selectedOutlineId
        ? canMove(state.outline, selectedOutlineId)
        : { up: false, down: false },
    [state.outline, selectedOutlineId],
  );

  const setMode = useCallback((mode: OutlineExpansionMode) => {
    setState((prev) => ({ ...prev, mode }));
  }, []);

  const setAnalysisOverview = useCallback((analysisOverview: string) => {
    setState((prev) => ({
      ...prev,
      analysisOverview,
      analysis: { ...prev.analysis, overview: analysisOverview },
    }));
  }, []);

  const setAnalysis = useCallback((analysis: BidAnalysis) => {
    setState((prev) => ({
      ...prev,
      analysis,
      analysisOverview: analysis.overview,
      responseMatrix: reconcileResponseMatrixLinks(
        mergeResponseMatrix(analysis, prev.responseMatrix),
        prev.chapters,
        prev.outline,
      ),
    }));
  }, []);

  const patchAnalysis = useCallback((partial: Partial<BidAnalysis>) => {
    setState((prev) => {
      const next = { ...prev.analysis, ...partial };
      return {
        ...prev,
        analysis: next,
        analysisOverview: next.overview,
        responseMatrix: reconcileResponseMatrixLinks(
          mergeResponseMatrix(next, prev.responseMatrix),
          prev.chapters,
          prev.outline,
        ),
      };
    });
  }, []);

  const setParsedMarkdown = useCallback((parsedMarkdown: string) => {
    setState((prev) => ({ ...prev, parsedMarkdown }));
  }, []);

  /**
   * 用途：更新服务端权威 guidance；走同一 800ms 防抖与 expected 队列。
   * 对接：ProjectGuidanceCard；禁止独立 guidance PUT。
   */
  const updateGuidance = useCallback(
    (patch: Partial<ProjectGenerationGuidance>) => {
      setState((prev) => ({
        ...prev,
        guidance: {
          ...prev.guidance,
          ...patch,
          updatedAt: new Date().toISOString(),
        },
      }));
    },
    [],
  );

  const refreshResponseMatrix = useCallback(() => {
    setState((prev) => ({
      ...prev,
      responseMatrix: reconcileResponseMatrixLinks(
        mergeResponseMatrix(prev.analysis, prev.responseMatrix),
        prev.chapters,
        prev.outline,
      ),
    }));
  }, []);

  const updateResponseMatrixItem = useCallback(
    (id: string, patch: Partial<ResponseMatrixItem>) => {
      setState((prev) => ({
        ...prev,
        responseMatrix: reconcileResponseMatrixLinks(
          normalizeResponseMatrix(
            prev.responseMatrix.map((item) =>
              item.id === id ? { ...item, ...patch } : item,
            ),
          ),
          prev.chapters,
          prev.outline,
        ),
      }));
    },
    [],
  );

  const applyResponseMatrixSuggestions = useCallback(
    (suggestions: ResponseMatrixSuggestion[]) => {
      if (suggestions.length === 0) return;
      const bySourceKey = new Map(
        suggestions.map((suggestion) => [suggestion.sourceKey, suggestion]),
      );
      setState((prev) => {
        const nextItems = prev.responseMatrix.map((item) => {
          const suggestion = bySourceKey.get(item.sourceKey);
          if (!suggestion || item.status === "waived") return item;
          const baseMatches =
            item.status === suggestion.base.status &&
            sameIds(item.chapterIds, suggestion.base.chapterIds) &&
            sameIds(item.outlineNodeIds, suggestion.base.outlineNodeIds);
          if (!baseMatches) return item;
          const chapterIds = uniqueIds([...item.chapterIds, ...suggestion.chapterIds]);
          const outlineNodeIds = uniqueIds([
            ...item.outlineNodeIds,
            ...suggestion.outlineNodeIds,
          ]);
          const hasSuggestedLink =
            suggestion.chapterIds.length + suggestion.outlineNodeIds.length > 0;
          return {
            ...item,
            chapterIds,
            outlineNodeIds,
            status:
              item.status === "uncovered" && hasSuggestedLink
                ? suggestion.status
                : item.status,
          };
        });
        return {
          ...prev,
          // 建议生成后章节或大纲可能已被删除，收敛会移除死链接并把空关联降为未覆盖。
          responseMatrix: reconcileResponseMatrixLinks(
            normalizeResponseMatrix(nextItems),
            prev.chapters,
            prev.outline,
          ),
        };
      });
    },
    [],
  );

  /**
   * 用途：任务成功后 / 显式重试 从服务端重新拉取 editor-state（单次 GET）。
   * 返回 true：GET 成功且已完成 setState/版本/冲突态更新；
   * 返回 false：失败时设置固定 loadError、apiReady=false、重置空态；不抛原文。
   * @param options.blocking 为 true 时置 loading（页面普通任务）；M3-D 无参调用保持 false，避免卸载对话框。
   * 二次开发：ContentFuseDialog 必须据返回值判定刷新成败；页面可在 false 时不提示“已刷新”。
   */
  const reloadFromApi = useCallback(
    async (options?: { blocking?: boolean }): Promise<boolean> => {
      const requestProjectId = projectId;
      if (!requestProjectId) return false;
      const requestSession = projectSessionRef.current;
      // 同项目重载：递增写入代次并清未发送 timer；旧代次回调不得覆盖新 GET
      writeEpochRef.current += 1;
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      const blocking = options?.blocking === true;
      if (blocking) setLoading(true);
      try {
        const remote = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(requestProjectId)}/editor-state`,
        );
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return false;
        }
        if (!isValidStateVersion(remote.stateVersion)) {
          if (fullStateBlockedRef.current) {
            // 全状态阻断下重载失败：保留本地内容与阻断，不卸载工作区
            setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
            return false;
          }
          // 非阻断场景的缺版本 GET：固定加载失败并重置
          skipNextSave.current = true;
          setState(createEmptyEditors());
          applyMatrixVersion(null);
          clearMatrixBase();
          stateVersionRef.current = null;
          matrixPutBlockedRef.current = false;
          setMatrixConflict(null);
          setMergeChoices({});
          setSelectedOutlineId(null);
          setSelectedChapterId(null);
          setApiReady(false);
          setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
          setSaveError(null);
          return false;
        }
        const next = fromApi(remote);
        skipNextSave.current = true;
        setState(next);
        applyStateVersion(remote.stateVersion);
        acceptVersionUpdatedAt(remote.updatedAt);
        snapshotMatrixBase(next.responseMatrix, remote.responseMatrixVersion);
        applyMatrixVersion(remote.responseMatrixVersion);
        matrixPutBlockedRef.current = false;
        fullStateBlockedRef.current = false;
        setFullStateConflict(false);
        setMatrixConflict(null);
        setMergeChoices({});
        setSelectedOutlineId(next.outline[0]?.id ?? null);
        setApiReady(true);
        setLoadError(null);
        setSaveError(null);
        return true;
      } catch {
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return false;
        }
        if (fullStateBlockedRef.current) {
          // 保持本地与阻断；loadError 供横幅展示，页面不得因阻断重载失败卸载正文
          setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
          return false;
        }
        // 非冲突场景的任务后刷新失败：重置空态（保持 P11C 既有语义）
        skipNextSave.current = true;
        setState(createEmptyEditors());
        applyMatrixVersion(null);
        clearMatrixBase();
        stateVersionRef.current = null;
        matrixPutBlockedRef.current = false;
        setMatrixConflict(null);
        setMergeChoices({});
        setSelectedOutlineId(null);
        setSelectedChapterId(null);
        setApiReady(false);
        setLoadError(TECHNICAL_EDITOR_LOAD_ERROR);
        setSaveError(null);
        return false;
      } finally {
        if (
          blocking &&
          isCurrentEditorSession(requestProjectId, requestSession)
        ) {
          setLoading(false);
        }
      }
    },
    [
      projectId,
      applyMatrixVersion,
      applyStateVersion,
      acceptVersionUpdatedAt,
      snapshotMatrixBase,
      clearMatrixBase,
      isCurrentEditorSession,
    ],
  );

  /**
   * 用途：P12B-C3/D2 受限版本化外部写 runner（M3-D apply/consume + 检查点 restore）。
   * 规则：
   *   - 进入 matrixSaveChainRef，与普通 PUT / 矩阵合并串行；
   *   - 真正执行时才读 stateVersionRef 最新合法 expected；
   *   - execute 返回必须含服务端 stateVersion；非法/409/网络不确定一律阻断；
   *   - POST 成功后阻断自动保存，仅做唯一一次 reloadFromApi；GET 成功才解除；
   *   - 禁止自动重试、拿 currentStateVersion 重发、旧 UI 带新版本自动 PUT。
   */
  const runVersionedExternalWrite = useCallback(
    async <T extends { stateVersion: string }>(
      execute: (expectedStateVersion: string) => Promise<T>,
    ): Promise<VersionedExternalWriteOutcome<T>> => {
      const requestProjectId = projectId;
      const requestSession = projectSessionRef.current;
      const requestEpoch = writeEpochRef.current;

      const run = async (): Promise<VersionedExternalWriteOutcome<T>> => {
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return { status: "post_failed", blocked: false };
        }
        if (fullStateBlockedRef.current) {
          return { status: "post_failed", blocked: true };
        }
        const expected = stateVersionRef.current;
        if (!isValidStateVersion(expected)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        let data: T;
        try {
          data = await execute(expected);
        } catch {
          if (
            !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
          ) {
            return { status: "post_failed", blocked: false };
          }
          // 409 / 网络不确定 / 其它失败：保守阻断，禁止旧 UI 自动保存
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return { status: "post_failed", blocked: false };
        }

        // 200/201 缺/非法版本：可能已写入但客户端版本未知
        if (!isValidStateVersion(data.stateVersion)) {
          enterFullStateBlock();
          return { status: "post_failed", blocked: true };
        }

        // 接受服务端新版本并阻断，直到唯一 GET 成功
        applyStateVersion(data.stateVersion);
        fullStateBlockedRef.current = true;
        setFullStateConflict(true);
        // 清未发送的防抖 PUT，避免旧本地带新版本自动写
        if (saveTimer.current) {
          window.clearTimeout(saveTimer.current);
          saveTimer.current = null;
        }
        // 禁止设 skipNextAutosavePutRef：reloadFromApi 水合已用 skipNextSave 吃掉一次
        // effect；若再置本标志，会残留吞掉重读成功后用户的下一次真实编辑 PUT。
        // 旧 epoch 飞行中/已排队 PUT 由 writeEpoch 递增 + isCurrentWriteEpoch 丢弃。

        // reloadFromApi 会递增 writeEpoch；成功后仅校验项目会话，勿用入队 epoch
        const reloaded = await reloadFromApi();
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return { status: "reload_failed", data };
        }
        if (!reloaded) {
          // GET 失败：保留本地 UI 与阻断；业务已成功
          enterFullStateBlock();
          return { status: "reload_failed", data };
        }
        // 重读成功后 residual 标志必须清空，确保下一次用户编辑正常发 PUT
        skipNextAutosavePutRef.current = false;
        return { status: "success", data };
      };

      const queued = matrixSaveChainRef.current
        .catch(() => undefined)
        .then(run);
      // 后续 PUT 等待本外部写；失败不卡死整条链
      matrixSaveChainRef.current = queued
        .then(() => undefined)
        .catch(() => undefined);
      return queued;
    },
    [
      projectId,
      isCurrentWriteEpoch,
      isCurrentEditorSession,
      enterFullStateBlock,
      applyStateVersion,
      reloadFromApi,
    ],
  );

  /**
   * 用途：冲突后显式采用远端矩阵并恢复保存；不提供静默强制覆盖。
   * 对接：ResponseMatrixPanel「重新载入远端矩阵」
   * 二次开发：全状态阻断期间不得解除阻断或发 PUT。
   */

  /**
   * 用途：P12B-D2 显式创建检查点——清 timer、串行链内强制即时 PUT，再 POST 精确 {}。
   * 约束：create 版本必须精确等于已接受 PUT 版本；PUT 挂起时 POST 不得发出。
   */
  const createCheckpoint = useCallback(async (): Promise<CheckpointCreateOutcome> => {
    if (!projectId) return { status: "blocked" };
    if (fullStateBlockedRef.current) return { status: "blocked" };
    if (!isValidStateVersion(stateVersionRef.current)) return { status: "blocked" };
    const requestProjectId = projectId;
    // 同项目连点：已有本项目 token 则拒绝；不同项目可覆盖启动新操作
    const existingOp = checkpointOpTokenRef.current;
    if (existingOp && existingOp.projectId === requestProjectId) {
      return { status: "failed" };
    }
    const myToken = ++checkpointOpTokenSeqRef.current;
    checkpointOpTokenRef.current = {
      projectId: requestProjectId,
      token: myToken,
    };

    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }

    const requestSession = projectSessionRef.current;

    const run = async (): Promise<CheckpointCreateOutcome> => {
      try {
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return { status: "failed" };
        }
        if (fullStateBlockedRef.current) {
          return { status: "blocked" };
        }
        if (!isValidStateVersion(stateVersionRef.current)) {
          return { status: "blocked" };
        }
        const requestEpoch = writeEpochRef.current;
        const putStatus = await executeImmediateEditorStatePut(
          requestProjectId,
          requestSession,
          requestEpoch,
        );
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return { status: "failed" };
        }
        if (putStatus === "ok") {
          const putVersion = stateVersionRef.current;
          if (!isValidStateVersion(putVersion)) {
            enterFullStateBlock();
            return { status: "blocked" };
          }
          try {
            const meta = await postCreateEditorStateCheckpoint(requestProjectId);
            if (!isCurrentEditorSession(requestProjectId, requestSession)) {
              return { status: "failed" };
            }
            // POST 版本不等于已接受 PUT 版本：远端期间可能已变，全状态阻断
            // （缺失/空白/非法 stateVersion 由 parse 专用错误在 catch 中阻断）
            if (
              !isValidStateVersion(meta.stateVersion) ||
              meta.stateVersion !== putVersion
            ) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "success" };
          } catch (err) {
            if (!isCurrentEditorSession(requestProjectId, requestSession)) {
              return { status: "failed" };
            }
            // 仅 create 成功体 stateVersion 语义失败 → 全量阻断；
            // 网络/HTTP/额外字段等普通 shape 失败仍 failed，禁止扩大阻断语义
            if (isCheckpointCreateStateVersionError(err)) {
              enterFullStateBlock();
              return { status: "blocked" };
            }
            return { status: "failed" };
          }
        }
        if (
          putStatus === "full_conflict" ||
          putStatus === "invalid_version" ||
          putStatus === "blocked"
        ) {
          return { status: "blocked" };
        }
        // forced-create：网络 abort / 非 409 HTTP / 不可判定失败 → 保守全状态阻断
        // 普通防抖 PUT 的 error 语义不变（executeImmediateEditorStatePut 仅 setSaveError）
        if (putStatus === "error") {
          enterFullStateBlock();
          return { status: "blocked" };
        }
        if (putStatus === "stale") {
          return { status: "failed" };
        }
        // matrix_conflict：保留矩阵冲突 UI，不伪造成全状态冲突
        return { status: "failed" };
      } finally {
        // 仅清自己的 token；若 ref 已被 B 项目覆盖则不动
        const cur = checkpointOpTokenRef.current;
        if (
          cur &&
          cur.projectId === requestProjectId &&
          cur.token === myToken
        ) {
          checkpointOpTokenRef.current = null;
        }
      }
    };

    const queued = matrixSaveChainRef.current
      .catch(() => undefined)
      .then(run);
    matrixSaveChainRef.current = queued
      .then(() => undefined)
      .catch(() => undefined);
    return queued;
  }, [
    projectId,
    isCurrentEditorSession,
    executeImmediateEditorStatePut,
    enterFullStateBlock,
  ]);

  /**
   * 用途：P12B-D2 检查点安全恢复——进入版本化外部写 runner；执行时读最新 expected。
   * 约束：成功唯一 GET；409/abort/非法版本阻断且零自动重试。
   */
  const restoreCheckpoint = useCallback(
    async (checkpointId: string): Promise<CheckpointRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestProjectId = projectId;
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestProjectId) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = {
        projectId: requestProjectId,
        token: myToken,
      };

      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateCheckpoint(
            requestProjectId,
            checkpointId,
            expectedStateVersion,
          ),
        );

        if (outcome.status === "success") {
          return { status: "success" };
        }
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (
          cur &&
          cur.projectId === requestProjectId &&
          cur.token === myToken
        ) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  /**
   * 用途：P12C-C3 修订受限恢复——复用检查点操作令牌与版本化外部写 runner。
   * 约束：执行时读最新 expected；成功唯一 GET；零自动重试；不得用列表 stateVersion 当 expected。
   */
  const restoreRevision = useCallback(
    async (revisionId: string): Promise<RevisionRestoreOutcome> => {
      if (!projectId) return { status: "blocked" };
      if (fullStateBlockedRef.current) return { status: "blocked" };
      if (!isValidStateVersion(stateVersionRef.current)) {
        return { status: "blocked" };
      }
      const requestProjectId = projectId;
      // 与 createCheckpoint/restoreCheckpoint 共用令牌，禁止并行版本化写
      const existingOp = checkpointOpTokenRef.current;
      if (existingOp && existingOp.projectId === requestProjectId) {
        return { status: "post_failed" };
      }
      const myToken = ++checkpointOpTokenSeqRef.current;
      checkpointOpTokenRef.current = {
        projectId: requestProjectId,
        token: myToken,
      };

      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }

      try {
        const outcome = await runVersionedExternalWrite((expectedStateVersion) =>
          postRestoreEditorStateRevision(
            requestProjectId,
            revisionId,
            expectedStateVersion,
          ),
        );

        if (outcome.status === "success") {
          return { status: "success" };
        }
        if (outcome.status === "reload_failed") {
          return { status: "reload_failed" };
        }
        return outcome.blocked
          ? { status: "blocked" }
          : { status: "post_failed" };
      } finally {
        const cur = checkpointOpTokenRef.current;
        if (
          cur &&
          cur.projectId === requestProjectId &&
          cur.token === myToken
        ) {
          checkpointOpTokenRef.current = null;
        }
      }
    },
    [projectId, runVersionedExternalWrite],
  );

  const reloadRemoteResponseMatrix = useCallback(() => {
    if (fullStateBlockedRef.current) {
      return;
    }
    setMatrixConflict((conflict) => {
      if (!conflict) return null;
      const remoteMatrix = reconcileResponseMatrixLinks(
        normalizeResponseMatrix(conflict.remoteMatrix),
        stateRef.current.chapters,
        stateRef.current.outline,
      );
      setState((prev) => ({
        ...prev,
        responseMatrix: remoteMatrix,
      }));
      applyMatrixVersion(conflict.remoteVersion || null);
      snapshotMatrixBase(remoteMatrix, conflict.remoteVersion || null);
      matrixPutBlockedRef.current = false;
      setMergeChoices({});
      return null;
    });
  }, [applyMatrixVersion, snapshotMatrixBase]);

  /** 用途：用户为冲突字段/行选择采用本地或远端；不得预选。 */
  const setResponseMatrixMergeChoice = useCallback(
    (choiceKey: string, choice: ResponseMatrixConflictChoice) => {
      setMergeChoices((prev) => ({ ...prev, [choiceKey]: choice }));
      setMatrixConflict((conflict) =>
        conflict ? { ...conflict, applyError: null } : conflict,
      );
    },
    [],
  );

  /**
   * 用途：用户确认后写入合并结果；进入技术主保存队列；
   *       PUT 体仅含 responseMatrix + responseMatrixVersion + expectedStateVersion。
   * 对接：ResponseMatrixPanel「应用合并」；
   * 成功后跳过一次普通全量防抖 PUT；二次 409 清空预览、禁止复用旧预览写新版本；
   * 项目切换后丢弃本请求的一切状态写回；全状态阻断期间不得发 PUT。
   */
  const applyResponseMatrixMerge = useCallback(async () => {
    const conflict = matrixConflict;
    const preview = conflict?.mergePreview;
    if (!conflict || !preview || mergeApplying) return;
    if (fullStateBlockedRef.current) return;

    const requestProjectId = projectId;
    const requestSession = projectSessionRef.current;
    const requestEpoch = writeEpochRef.current;

    const resolved = resolveResponseMatrixThreeWayChoices(preview, mergeChoices);
    if (!resolved) {
      setMatrixConflict({
        ...conflict,
        applyError: "请先为每一个冲突字段选择「采用本地」或「采用远端」",
      });
      return;
    }

    const latest = stateRef.current;
    const mergedMatrix = reconcileResponseMatrixLinks(
      resolved,
      latest.chapters,
      latest.outline,
    );
    const remoteVersion = String(conflict.remoteVersion || "").trim();
    if (!remoteVersion) {
      setMatrixConflict({
        ...conflict,
        applyError: "缺少远端矩阵版本，无法应用合并，请重新载入远端矩阵",
      });
      return;
    }

    setMergeApplying(true);

    const runMerge = async () => {
      if (
        !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
      ) {
        return;
      }
      if (fullStateBlockedRef.current) {
        return;
      }
      // 执行时读取最新 expected，不得在入队时固化旧版本
      const expected = stateVersionRef.current;
      if (!isValidStateVersion(expected)) {
        enterFullStateBlock();
        return;
      }
      // 仅矩阵 PUT：禁止携带 analysis/outline/chapters/facts/guidance
      const body = {
        responseMatrix: mergedMatrix,
        responseMatrixVersion: remoteVersion,
        expectedStateVersion: expected,
      };

      try {
        const res = await putEditorState(requestProjectId, body);
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return;
        }
        if (res.status === 409) {
          const raw = (await res.json().catch(() => null)) as {
            detail?: {
              code?: string;
              message?: string;
              responseMatrix?: ResponseMatrixItem[];
              currentResponseMatrixVersion?: string;
            };
          } | null;
          if (
            !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
          ) {
            return;
          }
          const detail = raw?.detail;
          // 全状态 code 优先：不构造矩阵伪冲突
          if (detail?.code === "editor_state_version_conflict") {
            enterFullStateBlock();
            return;
          }
          // 无真实矩阵明细：保留本地预览与既有远端快照，固定 applyError
          if (!hasRealMatrixConflictDetail(detail)) {
            setMatrixConflict({
              ...conflict,
              mergePreview: preview,
              applyError:
                "应用合并失败（409）。本地合并预览仍保留，请稍后重试或重新载入远端矩阵。",
            });
            return;
          }
          const nextRemote = normalizeResponseMatrix(detail.responseMatrix);
          const nextVersion = detail.currentResponseMatrixVersion.trim();
          // 二次 409 且有真实明细：禁止复用旧 mergePreview + 新 remoteVersion 写库
          setMergeChoices({});
          setMatrixConflict({
            message: TECHNICAL_MATRIX_CONFLICT_MESSAGE,
            remoteMatrix: nextRemote,
            remoteVersion: nextVersion,
            mergePreview: null,
            applyError:
              "应用合并时远端再次变更（409）。未自动重试；旧合并预览已失效，请点击「重新载入远端矩阵」后从远端状态重新进入合并流程。",
          });
          return;
        }
        if (!res.ok) {
          if (
            !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
          ) {
            return;
          }
          setMatrixConflict({
            ...conflict,
            mergePreview: preview,
            applyError: "应用合并失败，请稍后重试。本地合并预览仍保留。",
          });
          return;
        }
        const saved = (await res.json()) as EditorStateApi;
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return;
        }
        if (!isValidStateVersion(saved.stateVersion)) {
          enterFullStateBlock();
          return;
        }
        applyStateVersion(saved.stateVersion);
        acceptVersionUpdatedAt(saved.updatedAt);
        const savedMatrix = Array.isArray(saved.responseMatrix)
          ? reconcileResponseMatrixLinks(
              normalizeResponseMatrix(saved.responseMatrix),
              latest.chapters,
              latest.outline,
            )
          : mergedMatrix;
        // 跳过 setState 触发的普通全量防抖 PUT
        skipNextAutosavePutRef.current = true;
        if (saveTimer.current) {
          window.clearTimeout(saveTimer.current);
          saveTimer.current = null;
        }
        setState((prev) => ({
          ...prev,
          responseMatrix: savedMatrix,
        }));
        applyMatrixVersion(saved.responseMatrixVersion ?? remoteVersion);
        snapshotMatrixBase(
          savedMatrix,
          saved.responseMatrixVersion ?? remoteVersion,
        );
        matrixPutBlockedRef.current = false;
        setMatrixConflict(null);
        setMergeChoices({});
        setSaveError(null);
      } catch {
        if (
          !isCurrentWriteEpoch(requestProjectId, requestSession, requestEpoch)
        ) {
          return;
        }
        setMatrixConflict({
          ...conflict,
          mergePreview: preview,
          applyError:
            "应用合并时网络异常。本地合并预览仍保留，请检查连接后重试。",
        });
      }
    };

    try {
      // 矩阵合并进入技术主队列，不得旁路 PUT
      await (matrixSaveChainRef.current = matrixSaveChainRef.current
        .catch(() => undefined)
        .then(runMerge));
    } finally {
      if (isCurrentEditorSession(requestProjectId, requestSession)) {
        setMergeApplying(false);
      }
    }
  }, [
    matrixConflict,
    mergeChoices,
    mergeApplying,
    projectId,
    applyMatrixVersion,
    applyStateVersion,
    acceptVersionUpdatedAt,
    snapshotMatrixBase,
    isCurrentEditorSession,
    isCurrentWriteEpoch,
    enterFullStateBlock,
  ]);

  /**
   * 用途：替换单章正文并重新派生 preview/wordCount；可选恢复原 status。
   * 对接：修订预览、M3-B 确认写入、M3-C 批次撤销。
   * 二次开发：第三参数仅允许明确的 ChapterContent.status；未传时保持既有
   *       「有正文 → needs_review」行为；禁止写入标题/ID 或其他字段。
   */
  const replaceChapterBody = useCallback(
    (
      id: string,
      body: string,
      originalStatus?: ChapterContent["status"],
    ) => {
      setState((prev) => ({
        ...prev,
        chapters: prev.chapters.map((c) =>
          c.id === id
            ? {
                ...c,
                body,
                preview: derivePreview(body),
                wordCount: countBodyWords(body),
                status:
                  originalStatus !== undefined
                    ? originalStatus
                    : body.trim()
                      ? "needs_review"
                      : c.status,
              }
            : c,
        ),
      }));
    },
    [],
  );

  const patchOutlineNode = useCallback(
    (
      id: string,
      patch: Partial<Pick<OutlineNode, "title" | "targetWords" | "description">>,
    ) => {
      setState((prev) => {
        const outline = updateNode(prev.outline, id, patch);
        const chapters = patch.title
          ? syncChapterTitles(prev.chapters, outline)
          : prev.chapters;
        return { ...prev, outline, chapters };
      });
    },
    [],
  );

  const deleteOutlineNode = useCallback((id: string) => {
    setState((prev) => {
      const outline = removeNode(prev.outline, id);
      return {
        ...prev,
        outline,
        responseMatrix: reconcileResponseMatrixLinks(
          prev.responseMatrix,
          prev.chapters,
          outline,
        ),
      };
    });
    setSelectedOutlineId((cur) => (cur === id ? null : cur));
  }, []);

  /**
   * 用途：用 revise 解析后的大纲整树替换；按标题尽量保留已有章节正文。
   * 对接：大纲步「应用到大纲树」
   */
  const replaceOutline = useCallback((outline: OutlineNode[]) => {
    setState((prev) => {
      const byTitle = new Map(
        prev.chapters.map((c) => [c.title.trim(), c] as const),
      );
      const byId = new Map(prev.chapters.map((c) => [c.id, c] as const));
      const nextChapters: ChapterContent[] = [];
      const walkTop = (nodes: OutlineNode[]) => {
        for (const n of nodes) {
          if (n.level === 1) {
            const old = byId.get(n.id) || byTitle.get(n.title.trim());
            if (old) {
              nextChapters.push({
                ...old,
                id: n.id,
                title: n.title,
              });
            } else {
              nextChapters.push({
                id: n.id,
                title: n.title,
                body: "",
                preview: "（待生成）",
                wordCount: 0,
                status: "pending",
              });
            }
          }
          if (n.children?.length) walkTop(n.children);
        }
      };
      walkTop(outline);
      // 若没有一级标题，用根节点当章
      if (nextChapters.length === 0) {
        for (const n of outline) {
          nextChapters.push({
            id: n.id,
            title: n.title,
            body: "",
            preview: "（待生成）",
            wordCount: 0,
            status: "pending",
          });
        }
      }
      return {
        ...prev,
        outline,
        chapters: nextChapters,
        responseMatrix: reconcileResponseMatrixLinks(
          prev.responseMatrix,
          nextChapters,
          outline,
        ),
      };
    });
    setSelectedOutlineId(outline[0]?.id ?? null);
  }, []);

  const addOutlineSibling = useCallback((afterId: string | null) => {
    setState((prev) => {
      const outline = addSibling(prev.outline, afterId);
      return { ...prev, outline };
    });
  }, []);

  const addOutlineChild = useCallback((parentId: string) => {
    setState((prev) => ({
      ...prev,
      outline: addChild(prev.outline, parentId),
    }));
  }, []);

  const moveOutline = useCallback(
    (id: string, direction: "up" | "down") => {
      setState((prev) => ({
        ...prev,
        outline: moveNodeAmongSiblings(prev.outline, id, direction),
      }));
    },
    [],
  );

  const updateChapterBody = useCallback((id: string, body: string) => {
    setState((prev) => ({
      ...prev,
      chapters: prev.chapters.map((c) =>
        c.id === id
          ? {
              ...c,
              body,
              preview: derivePreview(body),
              wordCount: countBodyWords(body),
              status:
                c.status === "pending" && body.trim()
                  ? "needs_review"
                  : c.status,
            }
          : c,
      ),
    }));
  }, []);

  const updateChapterTitle = useCallback((id: string, title: string) => {
    setState((prev) => ({
      ...prev,
      chapters: prev.chapters.map((c) =>
        c.id === id ? { ...c, title } : c,
      ),
    }));
  }, []);

  const addFact = useCallback(() => {
    const id = `fact_${Date.now().toString(36)}`;
    setState((prev) => ({
      ...prev,
      facts: [
        {
          id,
          category: "手动",
          content: "",
          source: "manual",
        },
        ...prev.facts,
      ],
    }));
    return id;
  }, []);

  const updateFact = useCallback(
    (id: string, patch: Partial<Omit<GlobalFact, "id">>) => {
      setState((prev) => ({
        ...prev,
        facts: prev.facts.map((f) =>
          f.id === id ? { ...f, ...patch } : f,
        ),
      }));
    },
    [],
  );

  const removeFact = useCallback((id: string) => {
    setState((prev) => ({
      ...prev,
      facts: prev.facts.filter((f) => f.id !== id),
    }));
  }, []);

  const responseMatrixMergeUi: ResponseMatrixMergeUi | null =
    matrixConflict?.mergePreview
      ? {
          preview: matrixConflict.mergePreview,
          remoteVersion: matrixConflict.remoteVersion,
          choices: mergeChoices,
          applyError: matrixConflict.applyError ?? null,
          applying: mergeApplying,
        }
      : null;

  return {
    outline: state.outline,
    chapters: state.chapters,
    facts: state.facts,
    mode: state.mode,
    analysisOverview: state.analysis.overview || state.analysisOverview,
    analysis: state.analysis,
    responseMatrix: state.responseMatrix,
    responseMatrixVersion: matrixVersion,
    responseMatrixConflict: matrixConflict,
    responseMatrixMergeUi,
    reloadRemoteResponseMatrix,
    setResponseMatrixMergeChoice,
    applyResponseMatrixMerge,
    refreshResponseMatrix,
    updateResponseMatrixItem,
    applyResponseMatrixSuggestions,
    setAnalysisOverview,
    setAnalysis,
    patchAnalysis,
    parsedMarkdown: state.parsedMarkdown,
    setParsedMarkdown,
    guidance: state.guidance,
    updateGuidance,
    reloadFromApi,
    /** P12B-C3/D2：M3-D apply/consume 与检查点 restore 必须经此 runner，禁止 Dialog 旁路 */
    runVersionedExternalWrite,
    /** P12B-D2：显式创建检查点（强制即时 PUT + POST {}） */
    createCheckpoint,
    /** P12B-D2：检查点安全恢复（版本化外部写 + 唯一 GET） */
    restoreCheckpoint,
    /** P12C-C3：修订受限恢复（共用操作令牌 + 版本化外部写 + 唯一 GET） */
    restoreRevision,
    loading,
    loadError,
    saveError,
    apiReady,
    /** 全状态 CAS 冲突或 200 版本未知阻断；页面展示固定文案与显式重载 */
    fullStateConflict,
    fullStateConflictMessage: fullStateConflict
      ? TECHNICAL_EDITOR_STATE_CONFLICT_MESSAGE
      : null,
    /** P13-B：当前已载入版本的服务端 updatedAt（仅展示） */
    versionUpdatedAt,
    targetWordsTotal,
    selectedOutlineId,
    setSelectedOutlineId,
    selectedChapter,
    selectedChapterId: selectedChapter?.id ?? null,
    setSelectedChapterId,
    moveFlags,
    setMode,
    patchOutlineNode,
    deleteOutlineNode,
    replaceOutline,
    addOutlineSibling,
    addOutlineChild,
    moveOutline,
    updateChapterBody,
    updateChapterTitle,
    replaceChapterBody,
    addFact,
    updateFact,
    removeFact,
  };
}
