/**
 * 模块：工作空间设置类型
 * 用途：模型/Key/解析策略等前端配置契约。
 * 对接：GET|PUT /api/settings；apiKey 明文存与回显（保密机产品决策）。
 */

export type ParseStrategy = "light" | "local" | "ask";

export type WorkspaceSettings = {
  provider: string;
  apiBaseUrl: string;
  apiKey: string;
  model: string;
  parseStrategy: ParseStrategy;
  updatedAt?: string;
};

export const DEFAULT_SETTINGS: WorkspaceSettings = {
  provider: "openai-compatible",
  apiBaseUrl: "https://api.deepseek.com/v1",
  apiKey: "",
  model: "deepseek-chat",
  parseStrategy: "light",
};
