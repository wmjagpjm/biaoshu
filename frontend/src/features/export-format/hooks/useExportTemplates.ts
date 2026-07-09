import { useCallback, useMemo, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import type { ExportFormatConfig, ExportTemplateRecord } from "../model/exportFormat";
import { createDefaultExportFormat, withExportFormatDefaults } from "../model/cloneConfig";
import {
  applyExportLayoutPreset,
  applyExportThemePreset,
  EXPORT_LAYOUT_PRESETS,
} from "../model/exportFormatPresets";

const STORAGE_KEY = "biaoshu.exportTemplates.v2";

/** 用途：把默认模板配置同步到后端 settings.exportFormat */
async function syncDefaultToBackend(config: ExportFormatConfig | null) {
  try {
    await apiFetch("/settings", {
      method: "PUT",
      body: JSON.stringify({ exportFormat: config }),
    });
  } catch {
    /* 后端未起时忽略 */
  }
}

type StoredState = {
  templates: ExportTemplateRecord[];
  defaultId: string;
};

function loadStored(): StoredState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // 用标准投标版初始化一条系统默认，方便导出页有默认项
      const config = applyExportLayoutPreset(
        createDefaultExportFormat("标准投标版"),
        "standard-bid",
      );
      const now = new Date().toISOString();
      const seed: ExportTemplateRecord = {
        template_id: "sys_standard_bid",
        template_name: "标准投标版",
        config,
        created_at: now,
        updated_at: now,
      };
      return { templates: [seed], defaultId: seed.template_id };
    }
    const parsed = JSON.parse(raw) as StoredState;
    return {
      templates: (parsed.templates || []).map((t) => ({
        ...t,
        config: withExportFormatDefaults(t.config),
      })),
      defaultId: parsed.defaultId || parsed.templates?.[0]?.template_id || "",
    };
  } catch {
    return { templates: [], defaultId: "" };
  }
}

function persist(next: StoredState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

/**
 * 导出模板仓库（对齐 C 端 templateStore）
 * - 用户模板 CRUD
 * - 默认模板
 * - 版面/主题预设应用（保存进 config）
 */
export function useExportTemplates() {
  const [stored, setStored] = useState<StoredState>(() => loadStored());

  const commit = useCallback((updater: (prev: StoredState) => StoredState) => {
    setStored((prev) => persist(updater(prev)));
  }, []);

  const templates = stored.templates;
  const defaultTemplate = useMemo(
    () =>
      templates.find((t) => t.template_id === stored.defaultId) ||
      templates[0] ||
      null,
    [templates, stored.defaultId],
  );

  const getById = useCallback(
    (id: string) => templates.find((t) => t.template_id === id),
    [templates],
  );

  const setDefault = useCallback(
    (id: string) => {
      commit((prev) => {
        const next = { ...prev, defaultId: id };
        const cfg =
          next.templates.find((t) => t.template_id === id)?.config || null;
        void syncDefaultToBackend(cfg);
        return next;
      });
    },
    [commit],
  );

  const createTemplate = useCallback(
    (input: {
      name: string;
      config?: ExportFormatConfig;
      setAsDefault?: boolean;
    }) => {
      const now = new Date().toISOString();
      const id = `user_${Date.now()}`;
      const config = withExportFormatDefaults(
        input.config || createDefaultExportFormat(input.name),
      );
      config.template_name = input.name.trim() || "未命名模板";
      const record: ExportTemplateRecord = {
        template_id: id,
        template_name: config.template_name,
        config,
        created_at: now,
        updated_at: now,
      };
      commit((prev) => {
        const defaultId = input.setAsDefault ? id : prev.defaultId || id;
        const state = {
          templates: [record, ...prev.templates],
          defaultId,
        };
        if (input.setAsDefault || !prev.defaultId) {
          void syncDefaultToBackend(config);
        }
        return state;
      });
      return id;
    },
    [commit],
  );

  const updateTemplate = useCallback(
    (id: string, config: ExportFormatConfig) => {
      const next = withExportFormatDefaults(config);
      commit((prev) => {
        const state = {
          ...prev,
          templates: prev.templates.map((t) =>
            t.template_id === id
              ? {
                  ...t,
                  template_name: next.template_name || t.template_name,
                  config: next,
                  updated_at: new Date().toISOString(),
                }
              : t,
          ),
        };
        if (prev.defaultId === id) {
          void syncDefaultToBackend(next);
        }
        return state;
      });
    },
    [commit],
  );

  const deleteTemplate = useCallback(
    (id: string) => {
      commit((prev) => {
        const nextTemplates = prev.templates.filter((t) => t.template_id !== id);
        const nextDefault =
          prev.defaultId === id
            ? nextTemplates[0]?.template_id || ""
            : prev.defaultId;
        return { templates: nextTemplates, defaultId: nextDefault };
      });
    },
    [commit],
  );

  /** 从版面预设新建一条模板 */
  const createFromLayoutPreset = useCallback(
    (layoutId: string) => {
      const preset = EXPORT_LAYOUT_PRESETS.find((p) => p.id === layoutId);
      const base = createDefaultExportFormat(preset?.label || "自定义模板");
      const config = applyExportLayoutPreset(base, layoutId);
      return createTemplate({
        name: preset?.label || "自定义模板",
        config,
        setAsDefault: false,
      });
    },
    [createTemplate],
  );

  const applyLayoutToConfig = useCallback(
    (config: ExportFormatConfig, layoutId: string) =>
      applyExportLayoutPreset(withExportFormatDefaults(config), layoutId),
    [],
  );

  const applyThemeToConfig = useCallback(
    (config: ExportFormatConfig, themeId: string) =>
      applyExportThemePreset(withExportFormatDefaults(config), themeId),
    [],
  );

  return {
    templates,
    defaultTemplate,
    defaultId: stored.defaultId,
    layoutPresets: EXPORT_LAYOUT_PRESETS,
    getById,
    setDefault,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    createFromLayoutPreset,
    applyLayoutToConfig,
    applyThemeToConfig,
  };
}
