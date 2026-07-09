/**
 * 模块：工作空间设置持久化 Hook
 * 用途：设置页受控表单；优先读写后端 /api/settings，失败回退 localStorage。
 * 对接：
 *   - GET|PUT /api/settings（apiKey 明文存储与回显，保密机产品决策）
 *   - features/settings/types.ts
 * 二次开发：勿在页面直接 fetch；鉴权头统一走 apiFetch。
 */

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../../../shared/lib/api";
import { DEFAULT_SETTINGS, type WorkspaceSettings } from "../types";

const STORAGE_KEY = "biaoshu.settings.v1";

/** 用途：是否走后端设置 API（默认开；VITE_USE_API_SETTINGS=false 时强制本地）。 */
function useApiSettings(): boolean {
  const flag = import.meta.env.VITE_USE_API_SETTINGS;
  if (flag === "false" || flag === "0") return false;
  return true;
}

/** 用途：从 localStorage 加载；损坏则默认。 */
function loadLocal(): WorkspaceSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<WorkspaceSettings>;
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

/** 用途：写入 localStorage 兜底副本。 */
function saveLocal(next: WorkspaceSettings) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
}

export function useWorkspaceSettings() {
  const [settings, setSettings] = useState<WorkspaceSettings>(() => loadLocal());
  const [savedFlash, setSavedFlash] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [source, setSource] = useState<"api" | "local">("local");

  /** 用途：从后端或本地重新加载。 */
  const reload = useCallback(async () => {
    setLoading(true);
    setSaveError(null);
    if (useApiSettings()) {
      try {
        const remote = await apiFetch<WorkspaceSettings>("/settings");
        const next = { ...DEFAULT_SETTINGS, ...remote };
        setSettings(next);
        saveLocal(next);
        setSource("api");
        setLoading(false);
        return;
      } catch {
        /* 回退本地 */
      }
    }
    setSettings(loadLocal());
    setSource("local");
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const patch = useCallback((partial: Partial<WorkspaceSettings>) => {
    setSettings((prev) => ({ ...prev, ...partial }));
    setSavedFlash(false);
    setSaveError(null);
  }, []);

  /**
   * 用途：保存设置；优先 PUT /api/settings，失败则仅写 localStorage 并提示。
   */
  const save = useCallback(async () => {
    setSaveError(null);
    const next: WorkspaceSettings = {
      ...settings,
      updatedAt: new Date().toISOString(),
    };

    if (useApiSettings()) {
      try {
        const remote = await apiFetch<WorkspaceSettings>("/settings", {
          method: "PUT",
          body: JSON.stringify({
            provider: next.provider,
            apiBaseUrl: next.apiBaseUrl,
            apiKey: next.apiKey,
            model: next.model,
            parseStrategy: next.parseStrategy,
          }),
        });
        const merged = { ...DEFAULT_SETTINGS, ...remote };
        setSettings(merged);
        saveLocal(merged);
        setSource("api");
        setSavedFlash(true);
        window.setTimeout(() => setSavedFlash(false), 2500);
        return;
      } catch (err) {
        const msg =
          (err as { message?: string })?.message || "保存到服务器失败，已写入本机缓存";
        setSaveError(msg);
      }
    }

    saveLocal(next);
    setSettings(next);
    setSource("local");
    setSavedFlash(true);
    window.setTimeout(() => setSavedFlash(false), 2500);
  }, [settings]);

  /**
   * 用途：调用 POST /api/llm/test 验证当前已保存配置（建议先 save）。
   */
  const testConnection = useCallback(async (): Promise<{
    ok: boolean;
    message: string;
  }> => {
    try {
      const res = await apiFetch<{ ok: boolean; model: string; reply: string }>(
        "/llm/test",
        { method: "POST", body: "{}" },
      );
      return {
        ok: true,
        message: `连通成功（${res.model}）：${res.reply}`,
      };
    } catch (err) {
      const message =
        (err as { message?: string })?.message || "连通测试失败";
      return { ok: false, message };
    }
  }, []);

  return {
    settings,
    patch,
    save,
    reload,
    savedFlash,
    loading,
    saveError,
    source,
    testConnection,
  };
}
