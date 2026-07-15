/**
 * 模块：技术方案大纲 / 正文 / 全局事实 / 分析概述（P11C 服务端权威）
 * 用途：技术标编辑内容只认 GET|PUT /api/projects/{id}/editor-state；真实空态保持空。
 * 对接：editor-state API；页面 TechnicalPlanWorkspace；responseMatrixVersion 乐观锁；
 *       409 时在 base 快照匹配时生成字段级三方合并预览；getCsrfToken 内存 CSRF。
 * 明确非目标：
 *   - 禁止读写/删除/迁移 biaoshu.technicalPlan.editors.*（旧键忽略并保值）
 *   - 禁止生产路径导入 mock 或字段 fallback 伪装成功
 * 二次开发：矩阵 409 时禁止静默覆盖本地；须用户显式「重新载入远端矩阵」或「应用合并」；
 *       应用合并 PUT 仅含 responseMatrix + responseMatrixVersion；禁止自动重试循环；
 *       项目切换后须丢弃过期合并/409 异步结果，禁止污染新项目编辑器状态。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiFetch,
  getApiBase,
  getCsrfToken,
} from "../../../shared/lib/api";
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

type StoredEditors = {
  outline: OutlineNode[];
  chapters: ChapterContent[];
  facts: GlobalFact[];
  mode: OutlineExpansionMode;
  analysisOverview: string;
  analysis: BidAnalysis;
  responseMatrix: ResponseMatrixItem[];
  parsedMarkdown: string;
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
  guidance?: Record<string, unknown> | null;
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
  };
}

/**
 * 用途：远端 editor-state → 编辑态；null/[]/空串/updatedAt=null 均为权威空态。
 * 对接：仅消费同一响应内 analysis + responseMatrix 做既有 merge/reconcile。
 * 二次开发：禁止引入响应外 fallback/mock。
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
  const matrixVersionRef = useRef<string | null>(null);
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
   * 当前项目的矩阵/整包 PUT 串行链：同项目飞行中排队；
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

  const applyMatrixVersion = useCallback((version: string | null | undefined) => {
    const next = version && String(version).trim() ? String(version).trim() : null;
    matrixVersionRef.current = next;
    setMatrixVersion(next);
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

  // 切项目：立即作废旧会话、清计时器/错误/冲突，重置空编辑态后唯一 GET
  useEffect(() => {
    let cancelled = false;
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const session = ++projectSessionRef.current;
    activeProjectIdRef.current = projectId;
    // 新项目保存链与旧项目解耦：旧 A 挂起 PUT 不得阻塞 B 的合法防抖保存
    matrixSaveChainRef.current = Promise.resolve();
    skipNextSave.current = true;
    skipNextAutosavePutRef.current = false;
    matrixPutBlockedRef.current = false;
    clearMatrixBase();
    applyMatrixVersion(null);
    setMatrixConflict(null);
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
        const next = fromApi(remote);
        skipNextSave.current = true;
        setState(next);
        applyMatrixVersion(remote.responseMatrixVersion);
        snapshotMatrixBase(next.responseMatrix, remote.responseMatrixVersion);
        setSelectedOutlineId(next.outline[0]?.id ?? null);
        setSelectedChapterId(null);
        setApiReady(true);
        setLoadError(null);
        setSaveError(null);
      } catch {
        if (cancelled || !isCurrentEditorSession(projectId, session)) return;
        skipNextSave.current = true;
        setState(createEmptyEditors());
        applyMatrixVersion(null);
        clearMatrixBase();
        setSelectedOutlineId(null);
        setSelectedChapterId(null);
        setApiReady(false);
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
    snapshotMatrixBase,
    clearMatrixBase,
    isCurrentEditorSession,
  ]);

  // 保存：仅 apiReady 后 debounce PUT；矩阵带版本且串行；409 不静默覆盖；禁止 localStorage
  useEffect(() => {
    if (!apiReady || !projectId) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }
    // 合并成功写入矩阵后：跳过一次普通全量 PUT 副作用
    if (skipNextAutosavePutRef.current) {
      skipNextAutosavePutRef.current = false;
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      return;
    }
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      const requestProjectId = projectId;
      const requestSession = projectSessionRef.current;
      const runSave = async () => {
        // 定时器触发时若已切项目，直接丢弃，避免旧项目 PUT 写回新会话
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return;
        }
        const latest = stateRef.current;
        const body: Record<string, unknown> = {
          outline: latest.outline,
          chapters: latest.chapters,
          facts: latest.facts,
          mode: latest.mode,
          analysisOverview: latest.analysis.overview || latest.analysisOverview,
          analysis: latest.analysis,
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
          // fetch 返回后再次校验：项目切换后禁止写入 mergePreview / 版本 / base
          if (!isCurrentEditorSession(requestProjectId, requestSession)) {
            return;
          }
          if (res.status === 409) {
            // 仅真实版本冲突：串行后仍 409 才提示（同页旧版本重试已被队列消除）
            const raw = (await res.json().catch(() => null)) as {
              detail?: {
                message?: string;
                responseMatrix?: ResponseMatrixItem[];
                currentResponseMatrixVersion?: string;
              };
            } | null;
            if (!isCurrentEditorSession(requestProjectId, requestSession)) {
              return;
            }
            const detail = raw?.detail;
            const remoteMatrix = Array.isArray(detail?.responseMatrix)
              ? normalizeResponseMatrix(detail.responseMatrix)
              : [];
            const remoteVersion = String(
              detail?.currentResponseMatrixVersion || "",
            ).trim();
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
            // 固定中文；不得采用 detail.message / SECRET 原文
            setMatrixConflict({
              message: TECHNICAL_MATRIX_CONFLICT_MESSAGE,
              remoteMatrix,
              remoteVersion,
              mergePreview,
              applyError: null,
            });
            return;
          }
          if (!res.ok) {
            if (!isCurrentEditorSession(requestProjectId, requestSession)) {
              return;
            }
            setSaveError(TECHNICAL_EDITOR_SAVE_ERROR);
            return;
          }
          const saved = (await res.json()) as EditorStateApi;
          if (!isCurrentEditorSession(requestProjectId, requestSession)) {
            return;
          }
          setSaveError(null);
          if (saved.responseMatrixVersion) {
            applyMatrixVersion(saved.responseMatrixVersion);
          }
          // 成功带矩阵 PUT：刷新 base 快照
          if (includeMatrix && matrixAtRequest) {
            const savedMatrix = Array.isArray(saved.responseMatrix)
              ? normalizeResponseMatrix(saved.responseMatrix)
              : matrixAtRequest;
            snapshotMatrixBase(
              savedMatrix,
              saved.responseMatrixVersion ?? versionAtRequest,
            );
          }
        } catch {
          if (!isCurrentEditorSession(requestProjectId, requestSession)) {
            return;
          }
          setSaveError(TECHNICAL_EDITOR_SAVE_ERROR);
        }
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
    applyMatrixVersion,
    snapshotMatrixBase,
    isCurrentEditorSession,
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
      const blocking = options?.blocking === true;
      if (blocking) setLoading(true);
      try {
        const remote = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(requestProjectId)}/editor-state`,
        );
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return false;
        }
        const next = fromApi(remote);
        skipNextSave.current = true;
        setState(next);
        snapshotMatrixBase(next.responseMatrix, remote.responseMatrixVersion);
        applyMatrixVersion(remote.responseMatrixVersion);
        matrixPutBlockedRef.current = false;
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
        skipNextSave.current = true;
        setState(createEmptyEditors());
        applyMatrixVersion(null);
        clearMatrixBase();
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
      snapshotMatrixBase,
      clearMatrixBase,
      isCurrentEditorSession,
    ],
  );

  /**
   * 用途：冲突后显式采用远端矩阵并恢复保存；不提供静默强制覆盖。
   * 对接：ResponseMatrixPanel「重新载入远端矩阵」
   */
  const reloadRemoteResponseMatrix = useCallback(() => {
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
   * 用途：用户确认后写入合并结果；PUT 体仅含 responseMatrix + responseMatrixVersion。
   * 对接：ResponseMatrixPanel「应用合并」；
   * 成功后跳过一次普通全量防抖 PUT；二次 409 清空预览、禁止复用旧预览写新版本；
   * 项目切换后丢弃本请求的一切状态写回。
   */
  const applyResponseMatrixMerge = useCallback(async () => {
    const conflict = matrixConflict;
    const preview = conflict?.mergePreview;
    if (!conflict || !preview || mergeApplying) return;

    const requestProjectId = projectId;
    const requestSession = projectSessionRef.current;

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
    // 仅矩阵 PUT：禁止携带 analysis/outline/chapters/facts，避免旧编辑器状态回写
    const body = {
      responseMatrix: mergedMatrix,
      responseMatrixVersion: remoteVersion,
    };

    try {
      const res = await putEditorState(requestProjectId, body);
      // 切换/卸载后：静默丢弃，不得改写新项目的 matrix/base/version/conflict
      if (!isCurrentEditorSession(requestProjectId, requestSession)) {
        return;
      }
      if (res.status === 409) {
        const raw = (await res.json().catch(() => null)) as {
          detail?: {
            message?: string;
            responseMatrix?: ResponseMatrixItem[];
            currentResponseMatrixVersion?: string;
          };
        } | null;
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
          return;
        }
        const detail = raw?.detail;
        const nextRemote = Array.isArray(detail?.responseMatrix)
          ? normalizeResponseMatrix(detail.responseMatrix)
          : conflict.remoteMatrix;
        const nextVersion = String(
          detail?.currentResponseMatrixVersion || "",
        ).trim();
        // 二次 409：禁止复用旧 mergePreview + 新 remoteVersion 写库；须从远端重进合并
        // 固定中文；不得回显 detail.message / SECRET
        setMergeChoices({});
        setMatrixConflict({
          message: TECHNICAL_MATRIX_CONFLICT_MESSAGE,
          remoteMatrix: nextRemote,
          remoteVersion: nextVersion || conflict.remoteVersion,
          mergePreview: null,
          applyError:
            "应用合并时远端再次变更（409）。未自动重试；旧合并预览已失效，请点击「重新载入远端矩阵」后从远端状态重新进入合并流程。",
        });
        return;
      }
      if (!res.ok) {
        if (!isCurrentEditorSession(requestProjectId, requestSession)) {
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
      if (!isCurrentEditorSession(requestProjectId, requestSession)) {
        return;
      }
      const savedMatrix = Array.isArray(saved.responseMatrix)
        ? reconcileResponseMatrixLinks(
            normalizeResponseMatrix(saved.responseMatrix),
            latest.chapters,
            latest.outline,
          )
        : mergedMatrix;
      // 跳过 setState 触发的普通全量防抖 PUT，避免 analysis/outline/chapters 被旧本地值回写
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
      if (!isCurrentEditorSession(requestProjectId, requestSession)) {
        return;
      }
      setMatrixConflict({
        ...conflict,
        mergePreview: preview,
        applyError: "应用合并时网络异常。本地合并预览仍保留，请检查连接后重试。",
      });
    } finally {
      // 仅当前会话结束 loading，避免旧请求 finally 关掉新项目的 applying 状态
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
    snapshotMatrixBase,
    isCurrentEditorSession,
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
    reloadFromApi,
    loading,
    loadError,
    saveError,
    apiReady,
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
