import { useCallback, useEffect, useMemo, useState } from "react";
import { mockDocs, mockFolders } from "../mock";
import type {
  DocParseStatus,
  KbFolder,
  KnowledgeDoc,
} from "../types";
import { KB_FOLDER_ALL } from "../types";

/**
 * 模块：知识库文档状态
 * 用途：文件夹树 + 文档列表/状态筛选/批量移动/重试索引；localStorage 持久化。
 * 对接：后端就绪后改为 apiFetch，状态形状尽量保持。
 */

type StoredKb = {
  folders: KbFolder[];
  docs: KnowledgeDoc[];
};

const STORAGE_KEY = "biaoshu.knowledgeBase.docs.v1";

function seed(): StoredKb {
  return {
    folders: mockFolders.map((f) => ({ ...f })),
    docs: mockDocs.map((d) => ({ ...d })),
  };
}

function load(): StoredKb {
  const empty = seed();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<StoredKb>;
    if (!parsed.folders?.length || !parsed.docs?.length) return empty;
    return {
      folders: parsed.folders,
      docs: parsed.docs.map((d) => ({
        ...d,
        folderId: d.folderId || "fld_inbox",
        status: d.status || "ready",
        updatedAt: d.updatedAt || new Date().toISOString(),
      })),
    };
  } catch {
    return empty;
  }
}

export function useKnowledgeBase() {
  const [folders, setFolders] = useState<KbFolder[]>(() => load().folders);
  const [docs, setDocs] = useState<KnowledgeDoc[]>(() => load().docs);
  const [selectedFolderId, setSelectedFolderId] = useState<string>(KB_FOLDER_ALL);
  const [docQuery, setDocQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<DocParseStatus | "all">("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  useEffect(() => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ folders, docs } satisfies StoredKb),
    );
  }, [folders, docs]);

  const folderCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const d of docs) {
      map.set(d.folderId, (map.get(d.folderId) ?? 0) + 1);
    }
    return map;
  }, [docs]);

  const filteredDocs = useMemo(() => {
    const q = docQuery.trim().toLowerCase();
    return docs.filter((d) => {
      if (selectedFolderId !== KB_FOLDER_ALL && d.folderId !== selectedFolderId) {
        return false;
      }
      if (statusFilter !== "all" && d.status !== statusFilter) return false;
      if (!q) return true;
      return (
        d.name.toLowerCase().includes(q) ||
        d.tags.some((t) => t.toLowerCase().includes(q)) ||
        d.category.includes(q) ||
        (d.statusMessage?.toLowerCase().includes(q) ?? false)
      );
    });
  }, [docs, docQuery, selectedFolderId, statusFilter]);

  const createFolder = useCallback((name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const id = `fld_${Date.now().toString(36)}`;
    setFolders((prev) => [...prev, { id, name: trimmed, parentId: null }]);
    setSelectedFolderId(id);
  }, []);

  const moveDocs = useCallback((ids: string[], folderId: string) => {
    if (!ids.length) return;
    setDocs((prev) =>
      prev.map((d) =>
        ids.includes(d.id)
          ? {
              ...d,
              folderId,
              updated: "刚刚",
              updatedAt: new Date().toISOString(),
            }
          : d,
      ),
    );
    setSelectedIds([]);
  }, []);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const toggleSelectAllFiltered = useCallback(() => {
    setSelectedIds((prev) => {
      const ids = filteredDocs.map((d) => d.id);
      const allOn = ids.length > 0 && ids.every((id) => prev.includes(id));
      return allOn ? prev.filter((id) => !ids.includes(id)) : [...new Set([...prev, ...ids])];
    });
  }, [filteredDocs]);

  const clearSelection = useCallback(() => setSelectedIds([]), []);

  /**
   * 重试解析/索引（演示）
   * 后端：重新投递 parse/index 任务
   */
  const retryParse = useCallback((ids: string[]) => {
    if (!ids.length) return;
    setDocs((prev) =>
      prev.map((d) =>
        ids.includes(d.id)
          ? {
              ...d,
              status: "indexing" as const,
              statusMessage: "重新索引中…",
              updated: "刚刚",
              updatedAt: new Date().toISOString(),
            }
          : d,
      ),
    );

    window.setTimeout(() => {
      setDocs((prev) =>
        prev.map((d) =>
          ids.includes(d.id) && d.status === "indexing"
            ? {
                ...d,
                status: "ready",
                statusMessage: undefined,
                chunks: d.chunks > 0 ? d.chunks : 18,
                updated: "刚刚",
                updatedAt: new Date().toISOString(),
              }
            : d,
        ),
      );
    }, 900);
  }, []);

  const addDemoDoc = useCallback(() => {
    const folderId =
      selectedFolderId === KB_FOLDER_ALL ? "fld_inbox" : selectedFolderId;
    const id = `kb_${Date.now().toString(36)}`;
    const doc: KnowledgeDoc = {
      id,
      name: `新上传文档-${new Date().toLocaleTimeString("zh-CN")}.pdf`,
      tags: ["上传"],
      chunks: 0,
      updated: "刚刚",
      updatedAt: new Date().toISOString(),
      category: "待整理",
      folderId,
      status: "parsing",
      statusMessage: "前端演示：模拟解析…",
      sizeLabel: "1.0 MB",
    };
    setDocs((prev) => [doc, ...prev]);
    window.setTimeout(() => {
      setDocs((prev) =>
        prev.map((d) =>
          d.id === id
            ? {
                ...d,
                status: "ready",
                statusMessage: undefined,
                chunks: 15,
              }
            : d,
        ),
      );
    }, 1200);
  }, [selectedFolderId]);

  return {
    folders,
    docs,
    folderCounts,
    filteredDocs,
    selectedFolderId,
    setSelectedFolderId,
    docQuery,
    setDocQuery,
    statusFilter,
    setStatusFilter,
    selectedIds,
    toggleSelect,
    toggleSelectAllFiltered,
    clearSelection,
    createFolder,
    moveDocs,
    retryParse,
    addDemoDoc,
    totalDocCount: docs.length,
  };
}
