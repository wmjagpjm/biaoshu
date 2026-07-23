/**
 * 模块：工作空间设置类型
 * 用途：模型/Key/解析策略等前端配置契约。
 * 对接：GET|PUT /api/settings；apiKey 明文存与回显（保密机产品决策）。
 */

/** M3：与后端 ALLOWED_PARSE 对齐的四值策略。 */
export type ParseStrategy = "light" | "managed" | "local" | "ask";

export type WorkspaceSettings = {
  provider: string;
  apiBaseUrl: string;
  apiKey: string;
  model: string;
  parseStrategy: ParseStrategy;
  /** 空=仅本地哈希向量；可填 text-embedding-3-small 等 */
  embeddingModel?: string;
  updatedAt?: string;
};

export const DEFAULT_SETTINGS: WorkspaceSettings = {
  provider: "openai-compatible",
  apiBaseUrl: "https://api.deepseek.com/v1",
  apiKey: "",
  model: "deepseek-chat",
  parseStrategy: "light",
  embeddingModel: "",
};
