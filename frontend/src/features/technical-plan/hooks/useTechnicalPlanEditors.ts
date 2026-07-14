/**
 * 模块：技术方案大纲 / 正文 / 全局事实 / 分析概述
 * 用途：可编辑状态；优先 GET|PUT /api/projects/{id}/editor-state，失败回退 localStorage。
 * 对接：editor-state API；页面 TechnicalPlanWorkspace；responseMatrixVersion 乐观锁；
 *       409 时在 base 快照匹配时生成字段级三方合并预览。
 * 二次开发：矩阵 409 时禁止静默覆盖本地；须用户显式「重新载入远端矩阵」或「应用合并」；
 *       应用合并 PUT 仅含 responseMatrix + responseMatrixVersion；禁止自动重试循环；
 *       项目切换后须丢弃过期合并/409 异步结果，禁止污染新项目编辑器状态。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, getApiBase } from "../../../shared/lib/api";
import {
  addChild,
  addSibling,
  canMove,
  cloneOutline,
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
import { mockChapters, mockFacts, mockOutline } from "../mock";
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

const storageKey = (projectId: string) =>
  `biaoshu.technicalPlan.editors.${projectId}`;

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

function defaultState(): StoredEditors {
  return {
    outline: cloneOutline(mockOutline),
    chapters: mockChapters.map((c) => ({ ...c })),
    facts: mockFacts.map((f) => ({ ...f })),
    mode: "ALIGNED",
    analysisOverview: "",
    analysis: emptyBidAnalysis(),
    responseMatrix: [],
    parsedMarkdown: "",
  };
}

function loadLocal(projectId: string): StoredEditors {
  const empty = defaultState();
  try {
    const raw = localStorage.getItem(storageKey(projectId));
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<StoredEditors>;
    return {
      outline: parsed.outline?.length ? parsed.outline : empty.outline,
      chapters: parsed.chapters?.length
        ? parsed.chapters.map((c) => ({
            ...c,
            body: c.body ?? "",
            preview: c.preview ?? derivePreview(c.body ?? ""),
          }))
        : empty.chapters,
      facts: parsed.facts?.length
        ? parsed.facts.map((f) => ({ ...f }))
        : empty.facts,
      mode: parsed.mode === "FREE" ? "FREE" : "ALIGNED",
      analysisOverview:
        typeof parsed.analysisOverview === "string"
          ? parsed.analysisOverview
          : empty.analysisOverview,
      analysis: normalizeAnalysis(
        parsed.analysis,
        typeof parsed.analysisOverview === "string"
          ? parsed.analysisOverview
          : "",
      ),
      responseMatrix: normalizeResponseMatrix(parsed.responseMatrix),
      parsedMarkdown:
        typeof parsed.parsedMarkdown === "string"
          ? parsed.parsedMarkdown
          : empty.parsedMarkdown,
    };
  } catch {
    return empty;
  }
}

function saveLocal(projectId: string, state: StoredEditors) {
  localStorage.setItem(storageKey(projectId), JSON.stringify(state));
}

function fromApi(data: EditorStateApi, fallback: StoredEditors): StoredEditors {
  const analysis = normalizeAnalysis(
    data.analysis,
    data.analysisOverview || fallback.analysisOverview || "",
  );
  const outline = Array.isArray(data.outline) && data.outline.length
    ? (data.outline as OutlineNode[])
    : fallback.outline;
  const chapters = Array.isArray(data.chapters) && data.chapters.length
    ? (data.chapters as ChapterContent[])
    : fallback.chapters;
  const responseMatrix = reconcileResponseMatrixLinks(
    mergeResponseMatrix(
      analysis,
      Array.isArray(data.responseMatrix)
        ? data.responseMatrix
        : fallback.responseMatrix,
    ),
    chapters,
    outline,
  );
  return {
    outline,
    chapters,
    facts: Array.isArray(data.facts) && data.facts.length
      ? (data.facts as GlobalFact[])
      : fallback.facts,
    mode: data.mode === "FREE" ? "FREE" : "ALIGNED",
    analysisOverview: analysis.overview || data.analysisOverview || "",
    analysis,
    responseMatrix,
    parsedMarkdown:
      typeof data.parsedMarkdown === "string" && data.parsedMarkdown
        ? data.parsedMarkdown
        : fallback.parsedMarkdown,
  };
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
  const [state, setState] = useState<StoredEditors>(() => loadLocal(projectId));
  const [hydrated, setHydrated] = useState(false);
  const [persistSource, setPersistSource] = useState<"api" | "local">("local");
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
   * 置 true 时仍写 localStorage，但跳过下一次全量 PUT（避免把 analysis/outline/chapters/facts 回写远端）。
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
   * 版本化矩阵 PUT 串行链：飞行中不发下一个带矩阵的请求；
   * 完成后用最新 state + 新 version 再保存，避免同页误 409。
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

  // 加载：API 优先；切换项目时作废旧会话的异步写回
  useEffect(() => {
    let cancelled = false;
    const session = ++projectSessionRef.current;
    activeProjectIdRef.current = projectId;
    skipNextSave.current = true;
    skipNextAutosavePutRef.current = false;
    matrixPutBlockedRef.current = false;
    clearMatrixBase();
    setMatrixConflict(null);
    setMergeChoices({});
    setMergeApplying(false);
    setHydrated(false);
    const local = loadLocal(projectId);

    void (async () => {
      try {
        const remote = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(projectId)}/editor-state`,
        );
        if (cancelled || !isCurrentEditorSession(projectId, session)) return;
        const hasRemote =
          (Array.isArray(remote.outline) && remote.outline.length > 0) ||
          (Array.isArray(remote.chapters) && remote.chapters.length > 0) ||
          !!remote.analysisOverview ||
          !!remote.analysis?.overview ||
          (Array.isArray(remote.responseMatrix) &&
            remote.responseMatrix.length > 0) ||
          !!remote.parsedMarkdown ||
          !!remote.responseMatrixVersion;
        const next = hasRemote ? fromApi(remote, local) : local;
        setState(next);
        applyMatrixVersion(remote.responseMatrixVersion);
        if (hasRemote || remote.updatedAt) {
          snapshotMatrixBase(
            next.responseMatrix,
            remote.responseMatrixVersion,
          );
        } else {
          clearMatrixBase();
        }
        setPersistSource(hasRemote || remote.updatedAt ? "api" : "local");
        setSelectedOutlineId(next.outline[0]?.id ?? null);
        setSelectedChapterId(null);
        saveLocal(projectId, next);
      } catch {
        if (cancelled || !isCurrentEditorSession(projectId, session)) return;
        setState(local);
        applyMatrixVersion(null);
        clearMatrixBase();
        setPersistSource("local");
        setSelectedOutlineId(local.outline[0]?.id ?? null);
        setSelectedChapterId(null);
      } finally {
        if (!cancelled && isCurrentEditorSession(projectId, session)) {
          setHydrated(true);
          window.setTimeout(() => {
            // 仅当前会话才解除 skip；避免旧项目超时回调打开新项目的误保存
            if (isCurrentEditorSession(projectId, session)) {
              skipNextSave.current = false;
            }
          }, 50);
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

  // 保存：debounce PUT + localStorage；矩阵带版本且串行；409 不静默覆盖
  useEffect(() => {
    if (!hydrated || skipNextSave.current) return;
    saveLocal(projectId, state);
    // 合并成功写入矩阵后：保留本地缓存，跳过一次普通全量 PUT 副作用
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
        const includeMatrix =
          !matrixPutBlockedRef.current &&
          (persistSource === "api" || latest.responseMatrix.length > 0);
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
        const path = `${getApiBase()}/projects/${encodeURIComponent(requestProjectId)}/editor-state`;
        try {
          const res = await fetch(path, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
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
            setMatrixConflict({
              message:
                (detail?.message && String(detail.message)) ||
                "响应矩阵已被其他终端更新，请重新载入后再保存",
              remoteMatrix,
              remoteVersion,
              mergePreview,
              applyError: null,
            });
            return;
          }
          if (!res.ok) {
            setPersistSource("local");
            return;
          }
          const saved = (await res.json()) as EditorStateApi;
          if (!isCurrentEditorSession(requestProjectId, requestSession)) {
            return;
          }
          setPersistSource("api");
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
          setPersistSource("local");
        }
      };

      // 串行：上一矩阵/整包 PUT 完成并更新 version 后，再用最新 state 发出
      matrixSaveChainRef.current = matrixSaveChainRef.current
        .catch(() => undefined)
        .then(runSave);
    }, 800);
  }, [
    projectId,
    state,
    hydrated,
    persistSource,
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

  const fillDemoAnalysis = useCallback(() => {
    const demo: BidAnalysis = {
      overview:
        "建设覆盖城市主干路网的智慧交通综合管理平台，实现信号优化、违法抓拍汇聚、运行监测与指挥调度一体化。要求国产化适配、等保三级，并与现有交警业务系统对接。",
      techRequirements: [
        "支持视频流接入不少于 2000 路，可横向扩展",
        "提供开放 API 与消息总线对接现有指挥平台",
        "关键组件支持信创环境部署",
        "提供完整的权限、审计与备份恢复方案",
      ],
      rejectionRisks: [
        "未按招标文件规定目录编制",
        "未响应★号关键条款",
        "业绩证明材料不齐",
      ],
      scoringPoints: [
        { name: "总体架构与技术路线", weight: "20%" },
        { name: "功能模块完整性", weight: "25%" },
        { name: "实施与运维保障", weight: "15%" },
        { name: "业绩与团队", weight: "15%" },
        { name: "售后与培训", weight: "10%" },
        { name: "报价合理性", weight: "15%" },
      ],
    };
    setAnalysis(demo);
  }, [setAnalysis]);

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

  /** 用途：任务成功后从服务端重新拉取 editor-state */
  const reloadFromApi = useCallback(async () => {
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(projectId)}/editor-state`,
      );
      setState((prev) => {
        const next = fromApi(remote, prev);
        snapshotMatrixBase(next.responseMatrix, remote.responseMatrixVersion);
        return next;
      });
      applyMatrixVersion(remote.responseMatrixVersion);
      matrixPutBlockedRef.current = false;
      setMatrixConflict(null);
      setMergeChoices({});
      setPersistSource("api");
    } catch {
      /* 保持本地 */
    }
  }, [projectId, applyMatrixVersion, snapshotMatrixBase]);

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
    const path = `${getApiBase()}/projects/${encodeURIComponent(requestProjectId)}/editor-state`;
    // 仅矩阵 PUT：禁止携带 analysis/outline/chapters/facts，避免旧编辑器状态回写
    const body = {
      responseMatrix: mergedMatrix,
      responseMatrixVersion: remoteVersion,
    };

    try {
      const res = await fetch(path, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
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
        setMergeChoices({});
        setMatrixConflict({
          message:
            (detail?.message && String(detail.message)) ||
            conflict.message,
          remoteMatrix: nextRemote,
          remoteVersion: nextVersion || conflict.remoteVersion,
          mergePreview: null,
          applyError:
            "应用合并时远端再次变更（409）。未自动重试；旧合并预览已失效，请点击「重新载入远端矩阵」后从远端状态重新进入合并流程。",
        });
        return;
      }
      if (!res.ok) {
        setMatrixConflict({
          ...conflict,
          mergePreview: preview,
          applyError: `应用合并失败（HTTP ${res.status}）。本地合并预览仍保留，可稍后重试。`,
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
      setPersistSource("api");
      saveLocal(requestProjectId, {
        ...stateRef.current,
        responseMatrix: savedMatrix,
      });
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

  const extractDemoFacts = useCallback(() => {
    const stamp = Date.now().toString(36);
    const extras: GlobalFact[] = [
      {
        id: `fact_ext_${stamp}_1`,
        category: "招标摘录",
        content: "投标人须具备近三年同类业绩不少于 2 个（演示抽取）。",
        source: "tender",
      },
      {
        id: `fact_ext_${stamp}_2`,
        category: "知识库",
        content: "同类项目推荐双机房 + 消息总线架构（演示抽取）。",
        source: "knowledge",
      },
    ];
    setState((prev) => ({
      ...prev,
      facts: [...extras, ...prev.facts],
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
    fillDemoAnalysis,
    parsedMarkdown: state.parsedMarkdown,
    setParsedMarkdown,
    reloadFromApi,
    hydrated,
    persistSource,
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
    extractDemoFacts,
  };
}
