import { useCallback, useMemo, useState } from "react";
import { SYSTEM_PRESETS } from "../systemPresets";
import type { ExportStyleConfig, ExportTemplate } from "../types";
import { createDefaultStyle } from "../types";

const STORAGE_KEY = "biaoshu.exportTemplates.v1";

type StoredState = {
  userTemplates: ExportTemplate[];
  /** 当前默认模板 id（系统或用户） */
  defaultId: string;
};

function loadStored(): StoredState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { userTemplates: [], defaultId: "sys_gov" };
    }
    const parsed = JSON.parse(raw) as StoredState;
    return {
      userTemplates: parsed.userTemplates ?? [],
      defaultId: parsed.defaultId || "sys_gov",
    };
  } catch {
    return { userTemplates: [], defaultId: "sys_gov" };
  }
}

function persist(next: StoredState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

/**
 * 导出模板状态
 * 用途：对齐 C 端 templateStore——系统预设 + 用户模板 CRUD + 默认模板。
 * 后端就绪后改为 API，localStorage 仅作前端原型持久化。
 */
export function useExportTemplates() {
  const [stored, setStored] = useState<StoredState>(() => loadStored());

  /** 同步写入 localStorage，避免跳转后新页面读到旧数据 */
  const commit = useCallback((updater: (prev: StoredState) => StoredState) => {
    setStored((prev) => persist(updater(prev)));
  }, []);

  const allTemplates: ExportTemplate[] = useMemo(() => {
    const system = SYSTEM_PRESETS.map((p) => ({
      ...p,
      isDefault: p.id === stored.defaultId,
    }));
    const users = stored.userTemplates.map((t) => ({
      ...t,
      isDefault: t.id === stored.defaultId,
    }));
    return [...system, ...users];
  }, [stored]);

  const userTemplates = useMemo(
    () => allTemplates.filter((t) => t.source === "user"),
    [allTemplates],
  );

  const systemTemplates = useMemo(
    () => allTemplates.filter((t) => t.source === "system"),
    [allTemplates],
  );

  const defaultTemplate = useMemo(
    () => allTemplates.find((t) => t.isDefault) ?? allTemplates[0],
    [allTemplates],
  );

  const getById = useCallback(
    (id: string) => allTemplates.find((t) => t.id === id),
    [allTemplates],
  );

  const setDefault = useCallback(
    (id: string) => {
      commit((prev) => ({ ...prev, defaultId: id }));
    },
    [commit],
  );

  const createTemplate = useCallback(
    (input: {
      name: string;
      description: string;
      style?: Partial<ExportStyleConfig>;
      setAsDefault?: boolean;
    }) => {
      const now = new Date().toISOString();
      const id = `user_${Date.now()}`;
      const tpl: ExportTemplate = {
        id,
        name: input.name.trim() || "未命名模板",
        description: input.description.trim() || "自定义导出模板",
        source: "user",
        isDefault: false,
        createdAt: now,
        updatedAt: now,
        style: { ...createDefaultStyle(), ...input.style },
      };
      commit((prev) => ({
        userTemplates: [tpl, ...prev.userTemplates],
        defaultId: input.setAsDefault ? id : prev.defaultId,
      }));
      return id;
    },
    [commit],
  );

  const updateTemplate = useCallback(
    (
      id: string,
      patch: {
        name?: string;
        description?: string;
        style?: Partial<ExportStyleConfig>;
      },
    ) => {
      commit((prev) => ({
        ...prev,
        userTemplates: prev.userTemplates.map((t) => {
          if (t.id !== id) return t;
          return {
            ...t,
            name: patch.name?.trim() || t.name,
            description:
              patch.description !== undefined
                ? patch.description.trim()
                : t.description,
            style: patch.style ? { ...t.style, ...patch.style } : t.style,
            updatedAt: new Date().toISOString(),
          };
        }),
      }));
    },
    [commit],
  );

  const deleteTemplate = useCallback(
    (id: string) => {
      commit((prev) => {
        const nextUsers = prev.userTemplates.filter((t) => t.id !== id);
        const nextDefault =
          prev.defaultId === id
            ? nextUsers[0]?.id ?? "sys_gov"
            : prev.defaultId;
        return { userTemplates: nextUsers, defaultId: nextDefault };
      });
    },
    [commit],
  );

  /** 从系统预设复制为用户模板 */
  const cloneFrom = useCallback(
    (sourceId: string, name?: string) => {
      const src = allTemplates.find((t) => t.id === sourceId);
      if (!src) return null;
      return createTemplate({
        name: name || `${src.name}（副本）`,
        description: `基于「${src.name}」自定义`,
        style: { ...src.style },
      });
    },
    [allTemplates, createTemplate],
  );

  return {
    allTemplates,
    systemTemplates,
    userTemplates,
    defaultTemplate,
    getById,
    setDefault,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    cloneFrom,
  };
}
