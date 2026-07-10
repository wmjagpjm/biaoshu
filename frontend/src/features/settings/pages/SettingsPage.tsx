import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FileType,
  ImageIcon,
  KeyRound,
  Plug,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { currentWorkspace } from "../../../shared/mock/projects";
import { useSiteBackground } from "../../../shared/hooks/useSiteBackground";
import { useWorkspaceSettings } from "../hooks/useWorkspaceSettings";
import type { ParseStrategy } from "../types";
import "./Settings.css";

/**
 * 模块：设置页
 * 用途：工作空间、模型 Key（明文输入输出）、解析策略、站点背景图、导出模板。
 * 对接：useWorkspaceSettings → GET|PUT /api/settings；连通测试 POST /api/llm/test
 */
export function SettingsPage() {
  const {
    settings,
    patch,
    save,
    savedFlash,
    loading,
    saveError,
    source,
    testConnection,
  } = useWorkspaceSettings();
  const bg = useSiteBackground();
  const fileRef = useRef<HTMLInputElement>(null);
  const [bgError, setBgError] = useState("");
  const [bgBusy, setBgBusy] = useState(false);
  const [testMsg, setTestMsg] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    setTestMsg("");
    try {
      await save();
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTestBusy(true);
    setTestMsg("");
    // 先保存再测，避免测到旧配置
    await save();
    const res = await testConnection();
    setTestMsg(res.message);
    setTestBusy(false);
  }

  return (
    <div className="page settings-page">
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p>
            个人版：算力走你自己的 API Key。配置保存在本机后端（明文可回显），用于 revise
            等编排调用。
          </p>
        </div>
      </header>

      {loading && (
        <div className="settings-flash" role="status">
          正在加载设置…
        </div>
      )}

      {savedFlash && (
        <div className="settings-flash" role="status">
          {source === "api"
            ? "已保存到后端，刷新后仍生效。"
            : "已保存到本机缓存（后端未连通时的兜底）。"}
        </div>
      )}

      {saveError && (
        <div className="settings-flash settings-flash--error" role="alert">
          {saveError}
        </div>
      )}

      {testMsg && (
        <div className="settings-flash" role="status">
          {testMsg}
        </div>
      )}

      {/* 1. 工作空间 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <ShieldCheck size={18} />
          </div>
          <div>
            <h2>工作空间</h2>
            <p>一账号 ≈ 一工作空间；项目、知识库、任务均挂在此空间下。</p>
          </div>
        </div>
        <div className="settings-grid">
          <div className="field">
            <label>当前工作空间</label>
            <input
              value={
                source === "api" ? "我的工作空间（后端）" : currentWorkspace.name
              }
              readOnly
            />
          </div>
          <div className="field">
            <label>空间 ID</label>
            <input
              className="mono"
              value={source === "api" ? "ws_local" : currentWorkspace.id}
              readOnly
            />
          </div>
          <div className="field">
            <label>配置存储</label>
            <input
              readOnly
              value={
                source === "api"
                  ? "后端 SQLite（Key 明文回显）"
                  : "本机 localStorage 兜底"
              }
            />
          </div>
        </div>
      </section>

      {/* 2. 模型与算力 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <KeyRound size={18} />
          </div>
          <div>
            <h2>模型与算力</h2>
            <p>
              填写 OpenAI 兼容接口与密钥。本机保密环境：Key
              明文保存并可正常显示，便于核对输入输出。
            </p>
          </div>
        </div>
        <div className="settings-grid">
          <div className="field">
            <label htmlFor="provider">文本模型供应商</label>
            <select
              id="provider"
              value={settings.provider}
              onChange={(e) => patch({ provider: e.target.value })}
            >
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
              value={settings.apiBaseUrl}
              onChange={(e) => patch({ apiBaseUrl: e.target.value })}
              placeholder="https://api.deepseek.com/v1"
            />
          </div>
          <div className="field">
            <label htmlFor="key">API Key</label>
            <input
              id="key"
              type="text"
              className="mono"
              value={settings.apiKey}
              onChange={(e) => patch({ apiKey: e.target.value })}
              placeholder="sk-...（明文显示）"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="field">
            <label htmlFor="model">默认模型</label>
            <input
              id="model"
              className="mono"
              value={settings.model}
              onChange={(e) => patch({ model: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="emb">Embedding 模型（可选）</label>
            <input
              id="emb"
              className="mono"
              value={settings.embeddingModel ?? ""}
              onChange={(e) => patch({ embeddingModel: e.target.value })}
              placeholder="留空=本地哈希向量；如 text-embedding-3-small"
            />
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 12,
                color: "var(--text-tertiary)",
              }}
            >
              知识库检索默认本地向量+关键词混合；填写后将优先调用兼容 /embeddings。
            </p>
          </div>
        </div>
      </section>

      {/* 3. 解析策略 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <Plug size={18} />
          </div>
          <div>
            <h2>解析策略</h2>
            <p>在线轻量解析默认可用；复杂版式/扫描件优先本地 MinerU 插件。</p>
          </div>
        </div>
        <div className="settings-grid">
          <div className="field">
            <label htmlFor="parse">默认解析策略</label>
            <select
              id="parse"
              value={settings.parseStrategy}
              onChange={(e) =>
                patch({ parseStrategy: e.target.value as ParseStrategy })
              }
            >
              <option value="light">在线轻量解析</option>
              <option value="local">优先本地 MinerU 插件</option>
              <option value="ask">每次询问</option>
            </select>
          </div>
          <div>
            <Link to="/local-parser" className="btn btn-soft btn-sm">
              <Plug size={14} /> 配置本地解析插件
            </Link>
          </div>
        </div>
      </section>

      {/* 4. 站点背景 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <ImageIcon size={18} />
          </div>
          <div>
            <h2>站点背景</h2>
            <p>
              自定义主内容区背景图，避免界面过于空旷。图片保存在本机浏览器中，自动压缩。
            </p>
          </div>
        </div>

        <div className="settings-bg">
          <div
            className="settings-bg__preview"
            style={
              bg.hasImage
                ? {
                    backgroundImage: `linear-gradient(rgba(245,247,255,${bg.config.overlayOpacity}), rgba(245,247,255,${bg.config.overlayOpacity})), url(${bg.config.imageDataUrl})`,
                  }
                : undefined
            }
          >
            {!bg.hasImage && (
              <span className="settings-bg__placeholder">当前为默认浅色渐变</span>
            )}
          </div>

          <div className="settings-bg__controls">
            <input
              ref={fileRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                setBgError("");
                setBgBusy(true);
                void bg
                  .setImageFromFile(file)
                  .catch((err: unknown) => {
                    setBgError(
                      err instanceof Error ? err.message : "上传失败",
                    );
                  })
                  .finally(() => setBgBusy(false));
              }}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button
                type="button"
                className="btn btn-soft btn-sm"
                disabled={bgBusy}
                onClick={() => fileRef.current?.click()}
              >
                <Upload size={14} />
                {bgBusy ? "处理中…" : bg.hasImage ? "更换图片" : "上传背景图"}
              </button>
              {bg.hasImage && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    bg.clear();
                    setBgError("");
                  }}
                >
                  <Trash2 size={14} /> 清除背景
                </button>
              )}
            </div>

            {bg.hasImage && (
              <div className="field" style={{ marginTop: 12 }}>
                <label htmlFor="bg-overlay">
                  遮罩浓度（越高内容越清晰）·{" "}
                  {Math.round(bg.config.overlayOpacity * 100)}%
                </label>
                <input
                  id="bg-overlay"
                  type="range"
                  min={35}
                  max={90}
                  step={1}
                  value={Math.round(bg.config.overlayOpacity * 100)}
                  onChange={(e) =>
                    bg.setOverlay(Number(e.target.value) / 100)
                  }
                />
              </div>
            )}

            {bgError && (
              <p className="settings-bg__error" role="alert">
                {bgError}
              </p>
            )}
            <p className="settings-bg__hint">
              建议使用横图、分辨率适中。过大图片会自动压缩以便本地保存。
            </p>
          </div>
        </div>
      </section>

      {/* 5. 导出与模板 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <FileType size={18} />
          </div>
          <div>
            <h2>导出与模板</h2>
            <p>版面预设、我的模板、自定义 Word 导出样式。</p>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <Link to="/export-format" className="btn btn-soft btn-sm">
            <FileType size={14} /> 模板设置
          </Link>
          <Link to="/export-format/my-templates" className="btn btn-ghost btn-sm">
            我的模板
          </Link>
          <Link to="/export-format/new" className="btn btn-ghost btn-sm">
            新建模板
          </Link>
        </div>
      </section>

      {/* 6. 关于产品 */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <Sparkles size={18} />
          </div>
          <div>
            <h2>产品说明</h2>
            <p>本机部署的投标文件工作台。功能说明见资源中心与仓库文档。</p>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <Link to="/resources" className="btn btn-ghost btn-sm">
            打开资源中心
          </Link>
          <Link to="/create" className="btn btn-ghost btn-sm">
            返回创建
          </Link>
        </div>
      </section>

      <div className="settings-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void handleSave()}
          disabled={saving || loading}
        >
          <Save size={16} /> {saving ? "保存中…" : "保存设置"}
        </button>
        <button
          type="button"
          className="btn btn-soft"
          onClick={() => void handleTest()}
          disabled={testBusy || loading}
        >
          <Plug size={16} /> {testBusy ? "测试中…" : "测试模型连通"}
        </button>
      </div>
    </div>
  );
}
