/**
 * 模块：站点背景图
 * 用途：用户自定义背景图存 localStorage，全局 CSS 变量注入。
 * 说明：图片过大时压缩；清除后恢复默认渐变。
 */

export const BG_STORAGE_KEY = "biaoshu.siteBackground.v1";
export const BG_CHANGE_EVENT = "biaoshu:background-change";

export type SiteBackgroundConfig = {
  /** data URL 或空 */
  imageDataUrl: string;
  /** 遮罩不透明度 0~0.85，保证文字可读 */
  overlayOpacity: number;
  updatedAt?: string;
};

export const DEFAULT_BG: SiteBackgroundConfig = {
  imageDataUrl: "",
  overlayOpacity: 0.72,
};

export function loadSiteBackground(): SiteBackgroundConfig {
  try {
    const raw = localStorage.getItem(BG_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_BG };
    const parsed = JSON.parse(raw) as Partial<SiteBackgroundConfig>;
    return {
      ...DEFAULT_BG,
      ...parsed,
      overlayOpacity: clampOpacity(parsed.overlayOpacity ?? DEFAULT_BG.overlayOpacity),
    };
  } catch {
    return { ...DEFAULT_BG };
  }
}

export function saveSiteBackground(cfg: SiteBackgroundConfig): void {
  const next = {
    ...cfg,
    overlayOpacity: clampOpacity(cfg.overlayOpacity),
    updatedAt: new Date().toISOString(),
  };
  localStorage.setItem(BG_STORAGE_KEY, JSON.stringify(next));
  applySiteBackground(next);
  window.dispatchEvent(new CustomEvent(BG_CHANGE_EVENT, { detail: next }));
}

export function clearSiteBackground(): void {
  localStorage.removeItem(BG_STORAGE_KEY);
  applySiteBackground(DEFAULT_BG);
  window.dispatchEvent(new CustomEvent(BG_CHANGE_EVENT, { detail: DEFAULT_BG }));
}

function clampOpacity(n: number): number {
  if (Number.isNaN(n)) return DEFAULT_BG.overlayOpacity;
  return Math.min(0.9, Math.max(0.35, n));
}

/**
 * 将配置写到 documentElement CSS 变量，供 .app-body 使用
 */
export function applySiteBackground(cfg: SiteBackgroundConfig = loadSiteBackground()): void {
  const root = document.documentElement;
  if (cfg.imageDataUrl) {
    root.style.setProperty("--site-bg-image", `url("${cfg.imageDataUrl}")`);
    root.style.setProperty("--site-bg-overlay", String(cfg.overlayOpacity));
    root.dataset.hasBgImage = "1";
  } else {
    root.style.removeProperty("--site-bg-image");
    root.style.removeProperty("--site-bg-overlay");
    delete root.dataset.hasBgImage;
  }
}

/** 压缩图片为 JPEG dataURL，控制 localStorage 体积 */
export function fileToCompressedDataUrl(
  file: File,
  maxEdge = 1920,
  quality = 0.72,
): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith("image/")) {
      reject(new Error("请选择图片文件"));
      return;
    }
    // 约 4MB 以上提示仍会压缩
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("图片解码失败"));
      img.onload = () => {
        let { width, height } = img;
        const scale = Math.min(1, maxEdge / Math.max(width, height));
        width = Math.round(width * scale);
        height = Math.round(height * scale);
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          reject(new Error("浏览器不支持画布"));
          return;
        }
        ctx.drawImage(img, 0, 0, width, height);
        try {
          resolve(canvas.toDataURL("image/jpeg", quality));
        } catch (e) {
          reject(e instanceof Error ? e : new Error("压缩失败"));
        }
      };
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}
