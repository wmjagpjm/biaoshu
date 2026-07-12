/**
 * 模块：中标内容模板数据 Hook
 * 用途：列表检索、从项目沉淀、从模板新建技术标项目、删除模板。
 * 对接：/api/templates；BidTemplatesPage；技术标工作区沉淀入口。
 * 二次开发：不得在此实现多模板融合或覆盖已有项目 editor-state；列表状态仅存摘要。
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type { Project } from "../../../shared/types/workspace";
import type {
  BidTemplate,
  BidTemplateSummary,
  SaveAsTemplateDraft,
} from "../types";

function parseTags(tagsText: string): string[] {
  return tagsText
    .split(/[，,\n]/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 20);
}

/**
 * 用途：将列表/沉淀响应收敛为列表摘要，避免列表缓存完整 snapshot。
 */
function toSummary(
  item: BidTemplateSummary | BidTemplate,
): BidTemplateSummary {
  if ("chapterCount" in item && !("snapshot" in item)) {
    return item;
  }
  if ("chapterCount" in item && Array.isArray(item.outlineTitles)) {
    return {
      id: item.id,
      workspaceId: item.workspaceId,
      title: item.title,
      tags: item.tags,
      status: item.status,
      kind: item.kind,
      sourceProjectId: item.sourceProjectId,
      sourceProjectName: item.sourceProjectName,
      chapterCount: item.chapterCount,
      outlineTitles: item.outlineTitles,
      createdAt: item.createdAt,
      updatedAt: item.updatedAt,
    };
  }
  const detail = item as BidTemplate;
  const outline = detail.snapshot?.outline;
  const chapters = detail.snapshot?.chapters;
  const outlineTitles = Array.isArray(outline)
    ? outline
        .map((node) => {
          if (node && typeof node === "object" && "title" in node) {
            return String((node as { title?: unknown }).title || "").trim();
          }
          return "";
        })
        .filter(Boolean)
        .slice(0, 8)
    : [];
  return {
    id: detail.id,
    workspaceId: detail.workspaceId,
    title: detail.title,
    tags: detail.tags,
    status: detail.status,
    kind: detail.kind,
    sourceProjectId: detail.sourceProjectId,
    sourceProjectName: detail.sourceProjectName,
    chapterCount: Array.isArray(chapters) ? chapters.length : 0,
    outlineTitles,
    createdAt: detail.createdAt,
    updatedAt: detail.updatedAt,
  };
}

/**
 * 用途：维护模板列表、加载/写入状态与接口错误。
 * 对接：BidTemplatesPage；SaveAsTemplateDialog。
 */
export function useBidTemplates() {
  const [items, setItems] = useState<BidTemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (q?: string) => {
    setLoading(true);
    try {
      const query = q?.trim()
        ? `?q=${encodeURIComponent(q.trim())}`
        : "";
      const list = await apiFetch<BidTemplateSummary[]>(`/templates${query}`);
      setItems(Array.isArray(list) ? list.map(toSummary) : []);
      setError(null);
    } catch (reason) {
      setError((reason as { message?: string }).message || "加载模板失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveFromProject = useCallback(
    async (projectId: string, draft: SaveAsTemplateDraft) => {
      setSaving(true);
      try {
        const item = await apiFetch<BidTemplate>("/templates/from-project", {
          method: "POST",
          body: JSON.stringify({
            projectId,
            title: draft.title.trim() || undefined,
            tags: parseTags(draft.tagsText),
          }),
        });
        const summary = toSummary(item);
        setItems((prev) => {
          const rest = prev.filter((row) => row.id !== summary.id);
          return [summary, ...rest];
        });
        setError(null);
        return item;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "沉淀模板失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const createProject = useCallback(
    async (
      templateId: string,
      options?: { name?: string; industry?: string },
    ) => {
      setSaving(true);
      try {
        const project = await apiFetch<Project>(
          `/templates/${encodeURIComponent(templateId)}/projects`,
          {
            method: "POST",
            body: JSON.stringify({
              name: options?.name?.trim() || undefined,
              industry: options?.industry?.trim() || undefined,
            }),
          },
        );
        setError(null);
        return project;
      } catch (reason) {
        const message =
          (reason as { message?: string }).message || "从模板创建项目失败";
        setError(message);
        throw reason;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const remove = useCallback(async (templateId: string) => {
    setSaving(true);
    try {
      await apiFetch<void>(`/templates/${encodeURIComponent(templateId)}`, {
        method: "DELETE",
      });
      setItems((prev) => prev.filter((row) => row.id !== templateId));
      setError(null);
    } catch (reason) {
      const message =
        (reason as { message?: string }).message || "删除模板失败";
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
    saveFromProject,
    createProject,
    remove,
  };
}
