/**
 * 模块：技术方案大纲 / 正文 / 全局事实 / 分析概述
 * 用途：可编辑状态；优先 GET|PUT /api/projects/{id}/editor-state，失败回退 localStorage。
 * 对接：editor-state API；页面 TechnicalPlanWorkspace
 * 二次开发：冲突合并、版本号可在 PUT 增加 if-match。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
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
import { mockChapters, mockFacts, mockOutline, mockAnalysis } from "../mock";
import type {
  ChapterContent,
  GlobalFact,
  OutlineExpansionMode,
  OutlineNode,
} from "../types";

type StoredEditors = {
  outline: OutlineNode[];
  chapters: ChapterContent[];
  facts: GlobalFact[];
  mode: OutlineExpansionMode;
  analysisOverview: string;
  parsedMarkdown: string;
};

type EditorStateApi = {
  projectId: string;
  outline?: OutlineNode[] | null;
  chapters?: ChapterContent[] | null;
  facts?: GlobalFact[] | null;
  mode?: string;
  analysisOverview?: string | null;
  parsedMarkdown?: string | null;
  guidance?: Record<string, unknown> | null;
  updatedAt?: string | null;
};

const storageKey = (projectId: string) =>
  `biaoshu.technicalPlan.editors.${projectId}`;

function derivePreview(body: string): string {
  const plain = body
    .replace(/^#+\s*/gm, "")
    .replace(/[|>*`_\-]/g, " ")
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
    analysisOverview: mockAnalysis.overview,
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
  return {
    outline: Array.isArray(data.outline) && data.outline.length
      ? (data.outline as OutlineNode[])
      : fallback.outline,
    chapters: Array.isArray(data.chapters) && data.chapters.length
      ? (data.chapters as ChapterContent[])
      : fallback.chapters,
    facts: Array.isArray(data.facts) && data.facts.length
      ? (data.facts as GlobalFact[])
      : fallback.facts,
    mode: data.mode === "FREE" ? "FREE" : "ALIGNED",
    analysisOverview:
      typeof data.analysisOverview === "string" && data.analysisOverview
        ? data.analysisOverview
        : fallback.analysisOverview,
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
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(
    null,
  );
  const [selectedOutlineId, setSelectedOutlineId] = useState<string | null>(
    null,
  );
  const skipNextSave = useRef(true);
  const saveTimer = useRef<number | null>(null);

  // 加载：API 优先
  useEffect(() => {
    let cancelled = false;
    skipNextSave.current = true;
    setHydrated(false);
    const local = loadLocal(projectId);

    void (async () => {
      try {
        const remote = await apiFetch<EditorStateApi>(
          `/projects/${encodeURIComponent(projectId)}/editor-state`,
        );
        if (cancelled) return;
        const hasRemote =
          (Array.isArray(remote.outline) && remote.outline.length > 0) ||
          (Array.isArray(remote.chapters) && remote.chapters.length > 0) ||
          !!remote.analysisOverview ||
          !!remote.parsedMarkdown;
        const next = hasRemote ? fromApi(remote, local) : local;
        setState(next);
        setPersistSource(hasRemote || remote.updatedAt ? "api" : "local");
        setSelectedOutlineId(next.outline[0]?.id ?? null);
        setSelectedChapterId(null);
        saveLocal(projectId, next);
      } catch {
        if (cancelled) return;
        setState(local);
        setPersistSource("local");
        setSelectedOutlineId(local.outline[0]?.id ?? null);
        setSelectedChapterId(null);
      } finally {
        if (!cancelled) {
          setHydrated(true);
          window.setTimeout(() => {
            skipNextSave.current = false;
          }, 50);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [projectId]);

  // 保存：debounce PUT + localStorage
  useEffect(() => {
    if (!hydrated || skipNextSave.current) return;
    saveLocal(projectId, state);
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void apiFetch(`/projects/${encodeURIComponent(projectId)}/editor-state`, {
        method: "PUT",
        body: JSON.stringify({
          outline: state.outline,
          chapters: state.chapters,
          facts: state.facts,
          mode: state.mode,
          analysisOverview: state.analysisOverview,
        }),
      })
        .then(() => setPersistSource("api"))
        .catch(() => setPersistSource("local"));
    }, 800);
  }, [projectId, state, hydrated]);

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
    setState((prev) => ({ ...prev, analysisOverview }));
  }, []);

  const setParsedMarkdown = useCallback((parsedMarkdown: string) => {
    setState((prev) => ({ ...prev, parsedMarkdown }));
  }, []);

  /** 用途：任务成功后从服务端重新拉取 editor-state */
  const reloadFromApi = useCallback(async () => {
    try {
      const remote = await apiFetch<EditorStateApi>(
        `/projects/${encodeURIComponent(projectId)}/editor-state`,
      );
      setState((prev) => fromApi(remote, prev));
      setPersistSource("api");
    } catch {
      /* 保持本地 */
    }
  }, [projectId]);

  const replaceChapterBody = useCallback((id: string, body: string) => {
    setState((prev) => ({
      ...prev,
      chapters: prev.chapters.map((c) =>
        c.id === id
          ? {
              ...c,
              body,
              preview: derivePreview(body),
              wordCount: countBodyWords(body),
              status: body.trim() ? "needs_review" : c.status,
            }
          : c,
      ),
    }));
  }, []);

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
    setState((prev) => ({
      ...prev,
      outline: removeNode(prev.outline, id),
    }));
    setSelectedOutlineId((cur) => (cur === id ? null : cur));
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

  return {
    outline: state.outline,
    chapters: state.chapters,
    facts: state.facts,
    mode: state.mode,
    analysisOverview: state.analysisOverview,
    setAnalysisOverview,
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
