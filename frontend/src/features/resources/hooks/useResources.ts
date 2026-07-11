/**
 * 模块：资源中心数据 Hook
 * 用途：加载系统和用户资源，封装用户资源写操作及服务端浏览量累加。
 * 对接：/api/resources；shared/lib/api.ts；ResourcesPage。
 * 二次开发：受控同步完成后仅刷新本地 API；不得在浏览器新增远程 URL 请求或 mock 兜底。
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type { ResourceDraft, ResourceItem } from "../types";

function toPayload(draft: ResourceDraft) {
  return {
    title: draft.title.trim(),
    description: draft.description.trim(),
    category: draft.category.trim() || "资源",
    tags: draft.tagsText
      .split(/[，,\n]/)
      .map((tag) => tag.trim())
      .filter(Boolean),
    bodyMarkdown: draft.bodyMarkdown.trim(),
    tone: draft.tone,
  };
}

/**
 * 用途：将资源读模型转换为编辑草稿，并提供用户新建资源的默认值。
 * 对接：ResourcesPage 的新增和编辑弹层。
 */
export function resourceToDraft(resource?: ResourceItem | null): ResourceDraft {
  if (!resource) {
    return {
      title: "",
      description: "",
      category: "资源",
      tagsText: "",
      bodyMarkdown: "",
      tone: "blue",
    };
  }
  return {
    title: resource.title,
    description: resource.description,
    category: resource.category,
    tagsText: resource.tags.join("，"),
    bodyMarkdown: resource.bodyMarkdown,
    tone: resource.tone,
  };
}

/**
 * 用途：维护资源列表、请求状态和错误，并向页面提供 CRUD 与浏览量操作。
 * 对接：ResourcesPage；shared/lib/api.ts；/api/resources。
 * 二次开发：新增后端筛选时在 refresh 参数中扩展，避免在多个页面分别拼接 API 路径。
 */
export function useResources() {
  const [items, setItems] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await apiFetch<ResourceItem[]>("/resources");
      setItems(Array.isArray(list) ? list : []);
      setError(null);
    } catch (reason) {
      setError((reason as { message?: string }).message || "加载资源失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(
    async (draft: ResourceDraft, resourceId?: string) => {
      setSaving(true);
      try {
        const path = resourceId
          ? `/resources/${encodeURIComponent(resourceId)}`
          : "/resources";
        const item = await apiFetch<ResourceItem>(path, {
          method: resourceId ? "PATCH" : "POST",
          body: JSON.stringify(toPayload(draft)),
        });
        setItems((current) => {
          if (!resourceId) return [item, ...current];
          return current.map((entry) => (entry.id === item.id ? item : entry));
        });
        setError(null);
        return item;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "保存资源失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const remove = useCallback(async (resourceId: string) => {
    setSaving(true);
    try {
      await apiFetch<void>(`/resources/${encodeURIComponent(resourceId)}`, {
        method: "DELETE",
      });
      setItems((current) => current.filter((item) => item.id !== resourceId));
      setError(null);
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "删除资源失败";
      setError(message);
      throw reason;
    } finally {
      setSaving(false);
    }
  }, []);

  const recordView = useCallback(async (resourceId: string) => {
    setSaving(true);
    try {
      const item = await apiFetch<ResourceItem>(
        `/resources/${encodeURIComponent(resourceId)}/view`,
        { method: "POST", body: JSON.stringify({}) },
      );
      setItems((current) =>
        current.map((entry) => (entry.id === item.id ? item : entry)),
      );
      setError(null);
      return item;
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "记录浏览量失败";
      setError(message);
      throw reason;
    } finally {
      setSaving(false);
    }
  }, []);

  return {
    items,
    loading,
    saving,
    error,
    refresh,
    save,
    remove,
    recordView,
  };
}
