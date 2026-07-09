/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** 为 "false" / "0" 时强制项目走 localStorage，不调后端 */
  readonly VITE_USE_API_PROJECTS?: string;
  /** 为 "false" / "0" 时强制设置走 localStorage */
  readonly VITE_USE_API_SETTINGS?: string;
  /** 为 "false" / "0" 时列表不合并内置演示项目（联调推荐） */
  readonly VITE_MERGE_MOCK_PROJECTS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
