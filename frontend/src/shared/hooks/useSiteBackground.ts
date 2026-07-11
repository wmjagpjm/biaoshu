import { useCallback, useEffect, useState } from "react";
import {
  BG_CHANGE_EVENT,
  applySiteBackground,
  clearSiteBackground,
  fileToCompressedDataUrl,
  loadSiteBackground,
  saveSiteBackground,
  type SiteBackgroundConfig,
} from "../lib/siteBackground";

/**
 * 模块：站点背景 React Hook
 * 用途：设置页读写背景图；AppShell 监听变更刷新。
 */

export function useSiteBackground() {
  const [config, setConfig] = useState<SiteBackgroundConfig>(() =>
    loadSiteBackground(),
  );

  useEffect(() => {
    applySiteBackground(config);
  }, [config]);

  useEffect(() => {
    function onChange(e: Event) {
      const detail = (e as CustomEvent<SiteBackgroundConfig>).detail;
      if (detail) setConfig(detail);
      else setConfig(loadSiteBackground());
    }
    window.addEventListener(BG_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(BG_CHANGE_EVENT, onChange);
  }, []);

  const setImageFromFile = useCallback(async (file: File) => {
    const dataUrl = await fileToCompressedDataUrl(file);
    const next = {
      ...loadSiteBackground(),
      imageDataUrl: dataUrl,
    };
    saveSiteBackground(next);
    setConfig(next);
  }, []);

  const setOverlay = useCallback((overlayOpacity: number) => {
    const next = { ...loadSiteBackground(), overlayOpacity };
    saveSiteBackground(next);
    setConfig(next);
  }, []);

  const clear = useCallback(() => {
    clearSiteBackground();
    setConfig(loadSiteBackground());
  }, []);

  return {
    config,
    hasImage: Boolean(config.imageDataUrl),
    setImageFromFile,
    setOverlay,
    clear,
  };
}
