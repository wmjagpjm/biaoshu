/**
 * 模块：知识卡片列表状态
 * 用途：加载/筛选/创建/上传/归档/删除 workspace 卡片；图片预览走后端 content。
 * 对接：/api/cards；KnowledgeBasePage 卡片/图片 Tab。
 * 二次开发：勿回退 localStorage 存图；AI 注入与融合属阶段 3。
 */

import { useCallback, useEffect, useState } from "react";
import {
  createTextCard,
  deleteCard,
  listCards,
  updateCard,
  uploadImageCard,
  type ListCardsStatus,
} from "../api/cardsApi";
import type {
  KnowledgeCardSummary,
  KnowledgeCardType,
} from "../types";

export function useKnowledgeCards(options?: {
  /** 固定类型筛选（图片 Tab 传 image） */
  fixedType?: KnowledgeCardType;
}) {
  const fixedType = options?.fixedType;
  const [items, setItems] = useState<KnowledgeCardSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<KnowledgeCardType | "">(
    fixedType ?? "",
  );
  // 默认 active：与后端约定一致，归档需显式选择
  const [statusFilter, setStatusFilter] = useState<ListCardsStatus>("active");

  const refresh = useCallback(
    async (override?: {
      q?: string;
      type?: KnowledgeCardType | "";
      status?: ListCardsStatus;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const data = await listCards({
          q: override?.q ?? query,
          type: fixedType ?? override?.type ?? typeFilter,
          status: override?.status ?? statusFilter,
        });
        setItems(data);
      } catch (reason) {
        setError(
          (reason as { message?: string }).message || "加载卡片列表失败",
        );
      } finally {
        setLoading(false);
      }
    },
    [fixedType, query, statusFilter, typeFilter],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const createText = useCallback(
    async (input: {
      type: Exclude<KnowledgeCardType, "image">;
      title: string;
      bodyMarkdown: string;
      tags?: string[];
      sourceLabel?: string;
    }) => {
      setBusy(true);
      setError(null);
      try {
        const card = await createTextCard(input);
        await refresh();
        return card;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "创建卡片失败";
        setError(message);
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const uploadImages = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files).filter((f) =>
        ["image/png", "image/jpeg", "image/gif"].includes(f.type),
      );
      if (list.length === 0) {
        setError("仅支持 PNG / JPEG / GIF");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        for (const file of list) {
          await uploadImageCard(file, {
            title: file.name.replace(/\.[^.]+$/, ""),
          });
        }
        await refresh();
      } catch (reason) {
        setError(
          (reason as { message?: string }).message || "上传图片卡片失败",
        );
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const archive = useCallback(
    async (cardId: string) => {
      setBusy(true);
      setError(null);
      try {
        await updateCard(cardId, { status: "archived" });
        await refresh();
      } catch (reason) {
        setError((reason as { message?: string }).message || "归档失败");
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (cardId: string) => {
      setBusy(true);
      setError(null);
      try {
        await deleteCard(cardId);
        await refresh();
      } catch (reason) {
        setError((reason as { message?: string }).message || "删除失败");
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  return {
    items,
    loading,
    busy,
    error,
    query,
    setQuery,
    typeFilter: fixedType ?? typeFilter,
    setTypeFilter,
    statusFilter,
    setStatusFilter,
    refresh,
    createText,
    uploadImages,
    archive,
    remove,
  };
}
