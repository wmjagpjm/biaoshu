/**
 * 模块：知识库文档状态
 * 用途：文件夹树 + 文档列表/筛选/批量移动/上传索引/重试；优先 API，失败回退 localStorage。
 * 对接：GET|POST /api/knowledge/*；页面 KnowledgeBasePage
 * 二次开发：图片库仍独立 localStorage，勿混进本 hook。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import { mockDocs, mockFolders } from "../mock";
import type { DocParseStatus, KbFolder, KnowledgeDoc } from "../types";
import { KB_FOLDER_ALL } from "../types";

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

function loadLocal(): StoredKb {
  const empty = seed();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<StoredKb>;
    // API 模式空库允许为空；仅 local 回退时用 mock seed
    return {
      folders: Array.isArray(parsed.folders) ? parsed.folders : empty.folders,
      docs: Array.isArray(parsed.docs)
        ? parsed.docs.map((d) => ({
            ...d,
            folderId: d.folderId || "fld_inbox",
            status: d.status || "ready",
            updatedAt: d.updatedAt || new Date().toISOString(),
          }))
        : empty.docs,
    };
  } catch {
    return empty;
  }
}

function saveLocal(data: StoredKb) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* ignore quota */
  }
}

/**
 * 用途：multipart 上传知识库文档（含 folderId）。
 */
async function uploadKbDoc(file: File, folderId?: string): Promise<KnowledgeDoc> {
  const form = new FormData();
  form.append("file", file);
  if (folderId) form.append("folderId", folderId);
  return apiFetch<KnowledgeDoc>("/knowledge/docs/upload", {
    method: "POST",
    body: form,
  });
}

export function useKnowledgeBase() {
  const [folders, setFolders] = useState<KbFolder[]>([]);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [source, setSource] = useState<"api" | "local">("local");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [selectedFolderId, setSelectedFolderId] = useState<string>(KB_FOLDER_ALL);
  const [docQuery, setDocQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<DocParseStatus | "all">("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [f, d] = await Promise.all([
        apiFetch<KbFolder[]>("/knowledge/folders"),
        apiFetch<KnowledgeDoc[]>("/knowledge/docs"),
      ]);
      const foldersNext = Array.isArray(f) ? f : [];
      const docsNext = Array.isArray(d) ? d : [];
      setFolders(foldersNext);
      setDocs(docsNext);
      setSource("api");
      setError(null);
      saveLocal({ folders: foldersNext, docs: docsNext });
      return true;
    } catch (err) {
      const local = loadLocal();
      setFolders(local.folders);
      setDocs(local.docs);
      setSource("local");
      setError((err as { message?: string })?.message || "知识库 API 不可用，已用本地数据");
      return false;
    } finally {
      setHydrated(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 仅 local 模式写 localStorage（API 成功时 refresh 已写缓存）
  useEffect(() => {
    if (!hydrated || source !== "local") return;
    saveLocal({ folders, docs });
  }, [folders, docs, hydrated, source]);

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

  const createFolder = useCallback(
    async (name: string) => {
      const trimmed = name.trim();
      if (!trimmed) return;
      if (source === "api") {
        try {
          const row = await apiFetch<KbFolder>("/knowledge/folders", {
            method: "POST",
            body: JSON.stringify({ name: trimmed }),
          });
          setFolders((prev) => [...prev, row]);
          setSelectedFolderId(row.id);
          return;
        } catch (err) {
          setError((err as { message?: string })?.message || "创建文件夹失败");
          return;
        }
      }
      const id = `fld_${Date.now().toString(36)}`;
      setFolders((prev) => [...prev, { id, name: trimmed, parentId: null }]);
      setSelectedFolderId(id);
    },
    [source],
  );

  const moveDocs = useCallback(
    async (ids: string[], folderId: string) => {
      if (!ids.length) return;
      if (source === "api") {
        try {
          await apiFetch("/knowledge/docs/move", {
            method: "POST",
            body: JSON.stringify({ ids, folderId }),
          });
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
          return;
        } catch (err) {
          setError((err as { message?: string })?.message || "移动失败");
          return;
        }
      }
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
    },
    [source],
  );

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const toggleSelectAllFiltered = useCallback(() => {
    setSelectedIds((prev) => {
      const ids = filteredDocs.map((d) => d.id);
      const allOn = ids.length > 0 && ids.every((id) => prev.includes(id));
      return allOn
        ? prev.filter((id) => !ids.includes(id))
        : [...new Set([...prev, ...ids])];
    });
  }, [filteredDocs]);

  const clearSelection = useCallback(() => setSelectedIds([]), []);

  const deleteDocs = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      if (source === "api") {
        try {
          for (const id of ids) {
            await apiFetch(`/knowledge/docs/${encodeURIComponent(id)}`, {
              method: "DELETE",
            });
          }
          setDocs((prev) => prev.filter((d) => !ids.includes(d.id)));
          setSelectedIds([]);
          return;
        } catch (err) {
          setError((err as { message?: string })?.message || "删除失败");
          return;
        }
      }
      setDocs((prev) => prev.filter((d) => !ids.includes(d.id)));
      setSelectedIds([]);
    },
    [source],
  );

  /**
   * 用途：重试解析/索引（API reindex；本地演示）。
   */
  const retryParse = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      if (source === "api") {
        setBusy(true);
        try {
          for (const id of ids) {
            const row = await apiFetch<KnowledgeDoc>(
              `/knowledge/docs/${encodeURIComponent(id)}/reindex`,
              { method: "POST" },
            );
            setDocs((prev) => prev.map((d) => (d.id === id ? row : d)));
          }
          setSelectedIds([]);
        } catch (err) {
          setError((err as { message?: string })?.message || "重新索引失败");
        } finally {
          setBusy(false);
        }
        return;
      }
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
    },
    [source],
  );

  /**
   * 用途：真实上传并索引；API 离线时回退演示。
   */
  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      const folderId =
        selectedFolderId === KB_FOLDER_ALL
          ? folders[0]?.id
          : selectedFolderId;

      if (source === "api") {
        setBusy(true);
        setError(null);
        try {
          for (const file of list) {
            const row = await uploadKbDoc(file, folderId);
            setDocs((prev) => [row, ...prev.filter((d) => d.id !== row.id)]);
          }
          await refresh();
        } catch (err) {
          setError((err as { message?: string })?.message || "上传失败");
        } finally {
          setBusy(false);
        }
        return;
      }

      // local 演示
      for (const file of list) {
        const id = `kb_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 5)}`;
        const doc: KnowledgeDoc = {
          id,
          name: file.name,
          tags: ["上传"],
          chunks: 0,
          updated: "刚刚",
          updatedAt: new Date().toISOString(),
          category: "待整理",
          folderId: folderId || "fld_inbox",
          status: "parsing",
          statusMessage: "离线演示：模拟解析…",
          sizeLabel: `${(file.size / 1024).toFixed(1)} KB`,
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
                    chunks: 12,
                  }
                : d,
            ),
          );
        }, 800);
      }
    },
    [source, selectedFolderId, folders, refresh],
  );

  /** 兼容旧按钮名：无文件时触发 input 由页面处理 */
  const addDemoDoc = useCallback(() => {
    void uploadFiles([]);
  }, [uploadFiles]);

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
    deleteDocs,
    retryParse,
    uploadFiles,
    addDemoDoc,
    refresh,
    busy,
    error,
    setError,
    source,
    hydrated,
    totalDocCount: docs.length,
  };
}
