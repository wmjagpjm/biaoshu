import { Save } from "lucide-react";

/**
 * 设置页
 * 用途：模型供应商、API Key、解析策略等；Key 仅存当前工作空间（后端加密存储）。
 */
export function SettingsPage() {
  return (
    <div className="page" style={{ maxWidth: 720 }}>
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p>个人版：算力走你自己的 API Key。服务器只做编排与存储。</p>
        </div>
      </header>

      <form
        className="card card-pad"
        onSubmit={(e) => {
          e.preventDefault();
        }}
        style={{ display: "grid", gap: 16 }}
      >
        <div className="field">
          <label htmlFor="provider">文本模型供应商</label>
          <select id="provider" defaultValue="openai-compatible">
            <option value="openai-compatible">OpenAI 兼容</option>
            <option value="deepseek">DeepSeek</option>
            <option value="volcengine">火山方舟</option>
            <option value="ollama">Ollama（本机）</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="base">API Base URL</label>
          <input
            id="base"
            className="mono"
            defaultValue="https://api.deepseek.com/v1"
            placeholder="https://..."
          />
        </div>
        <div className="field">
          <label htmlFor="key">API Key</label>
          <input id="key" type="password" placeholder="sk-..." autoComplete="off" />
        </div>
        <div className="field">
          <label htmlFor="model">默认模型</label>
          <input id="model" className="mono" defaultValue="deepseek-chat" />
        </div>
        <div className="field">
          <label htmlFor="parse">默认解析策略</label>
          <select id="parse" defaultValue="light">
            <option value="light">在线轻量解析</option>
            <option value="local">优先本地 MinerU 插件</option>
            <option value="ask">每次询问</option>
          </select>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button type="submit" className="btn btn-primary">
            <Save size={16} /> 保存（前端 mock）
          </button>
        </div>
      </form>
    </div>
  );
}
