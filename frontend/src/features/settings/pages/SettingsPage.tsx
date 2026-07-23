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
  Users,
} from "lucide-react";
import { currentWorkspace } from "../../../shared/mock/projects";
import { useSiteBackground } from "../../../shared/hooks/useSiteBackground";
import { apiFetch } from "../../../shared/lib/api";
import {
  authRoleLabel,
  useAuthSession,
} from "../../auth/hooks/useAuthSession";
import type { AuthMember, AuthRole } from "../../auth/types";
import { useWorkspaceSettings } from "../hooks/useWorkspaceSettings";
import type { ParseStrategy } from "../types";
import "./Settings.css";

/** 成员列表固定中文文案（不回显 detail/URL/ID） */
const MEMBERS_LOADING_TEXT = "正在加载成员列表…";
const MEMBERS_FAIL_TEXT = "成员列表加载失败，请重试";
const MEMBERS_EMPTY_TEXT = "暂无成员";

const AUTH_ROLES: ReadonlySet<string> = new Set([
  "bid_writer",
  "finance",
  "hr",
  "bidder",
]);

function isAuthRole(value: unknown): value is AuthRole {
  return typeof value === "string" && AUTH_ROLES.has(value);
}

/** 成员项精确 7 键（顺序无关；缺一或额外字段整批失败） */
const MEMBER_KEYS = [
  "userId",
  "username",
  "role",
  "isOwner",
  "isActive",
  "createdAt",
  "updatedAt",
] as const;

const MEMBER_KEY_SET: ReadonlySet<string> = new Set(MEMBER_KEYS);

/**
 * 用途：严格校验 GET /auth/members 整批脱敏形状；坏项整批失败。
 * 每项 Object.keys 必须精确等于 7 字段（userId/username/role/isOwner/isActive/createdAt/updatedAt）；
 * 缺失或额外字段（如 password/token/marker）整批失败，不把半真半假结果写入 UI。
 */
function sanitizeMembers(raw: unknown): AuthMember[] | null {
  if (!Array.isArray(raw)) return null;
  const out: AuthMember[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") return null;
    const m = item as Record<string, unknown>;
    const keys = Object.keys(m);
    // 精确七键：数量与集合必须一致，拒绝额外敏感键
    if (keys.length !== MEMBER_KEYS.length) return null;
    for (const k of keys) {
      if (!MEMBER_KEY_SET.has(k)) return null;
    }
    for (const required of MEMBER_KEYS) {
      if (!(required in m)) return null;
    }
    if (typeof m.userId !== "string" || !m.userId.trim()) return null;
    if (typeof m.username !== "string" || !m.username.trim()) return null;
    if (!isAuthRole(m.role)) return null;
    if (typeof m.isOwner !== "boolean") return null;
    if (typeof m.isActive !== "boolean") return null;
    if (typeof m.createdAt !== "string") return null;
    if (typeof m.updatedAt !== "string") return null;
    out.push({
      userId: m.userId,
      username: m.username,
      role: m.role,
      isOwner: m.isOwner,
      isActive: m.isActive,
      createdAt: m.createdAt,
      updatedAt: m.updatedAt,
    });
  }
  return out;
}

/**
 * 模块：设置页
 * 用途：工作空间真值、成员只读列表、模型 Key、解析策略、站点背景、导出模板。
 * 对接：useAuthSession 活动空间；apiFetch GET /auth/members；useWorkspaceSettings。
 * 二次开发：禁止自动拉取成员、userId 出 DOM、在线 presence 文案。
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
  const { phase, activeMembership } = useAuthSession();
  const bg = useSiteBackground();
  const fileRef = useRef<HTMLInputElement>(null);
  const [bgError, setBgError] = useState("");
  const [bgBusy, setBgBusy] = useState(false);
  const [testMsg, setTestMsg] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [saving, setSaving] = useState(false);

  /** 成员列表仅内存；成功后不再自动/重复请求 */
  const [members, setMembers] = useState<AuthMember[] | null>(null);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState(false);
  const [membersStatus, setMembersStatus] = useState<string | null>(null);
  const membersInFlightRef = useRef(false);

  /** required 所有者才显示显式加载入口；disabled/非 owner 零 UI 零请求 */
  const canLoadMembers =
    phase === "authenticated" && Boolean(activeMembership?.isOwner);

  /** 工作空间展示真值：required 用 activeMembership；disabled 明确个人版 */
  const workspaceName =
    phase === "disabled"
      ? "个人版默认空间"
      : (activeMembership?.name ?? "未选择空间");
  const workspaceId =
    phase === "disabled"
      ? currentWorkspace.id
      : (activeMembership?.id ?? "");
  const workspaceRole =
    phase === "disabled"
      ? "个人版"
      : authRoleLabel(activeMembership?.role);
  const workspaceOwner =
    phase === "disabled" ? "—" : activeMembership?.isOwner ? "是" : "否";

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

  async function handleLoadMembers() {
    if (!canLoadMembers) return;
    // 单飞
    if (membersInFlightRef.current || membersLoading) return;
    // 成功已加载则不再请求（仅失败后允许显式重试）
    if (members !== null && !membersError) return;

    membersInFlightRef.current = true;
    setMembersLoading(true);
    setMembersError(false);
    setMembersStatus(MEMBERS_LOADING_TEXT);
    setMembers(null);

    try {
      const raw = await apiFetch<unknown>("/auth/members");
      const list = sanitizeMembers(raw);
      if (!list) {
        throw new Error("bad_members");
      }
      setMembers(list);
      setMembersError(false);
      setMembersStatus(list.length === 0 ? MEMBERS_EMPTY_TEXT : null);
    } catch {
      setMembers(null);
      setMembersError(true);
      setMembersStatus(MEMBERS_FAIL_TEXT);
    } finally {
      membersInFlightRef.current = false;
      setMembersLoading(false);
    }
  }

  return (
    <div className="page settings-page">
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p>
            {phase === "disabled"
              ? "个人版：算力走你自己的 API Key。配置保存在本机后端（明文可回显），用于 revise 等编排调用。"
              : "工作空间设置：算力走当前空间配置的 API Key。配置保存在本机后端（明文可回显），用于 revise 等编排调用。"}
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

      {/* 1. 工作空间（P13-E：活动空间真值 + 所有者成员只读） */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <ShieldCheck size={18} />
          </div>
          <div>
            <h2>工作空间</h2>
            <p>
              {phase === "disabled"
                ? "个人版默认空间：配置与项目均挂在本机空间下，无多人成员能力。"
                : "当前会话活动工作空间；项目、知识库、任务均挂在此空间下。"}
            </p>
          </div>
        </div>
        <div className="settings-grid">
          <div className="field">
            <label htmlFor="settings-workspace-name">当前工作空间</label>
            <input
              id="settings-workspace-name"
              data-testid="settings-workspace-name"
              value={workspaceName}
              readOnly
            />
          </div>
          <div className="field">
            <label htmlFor="settings-workspace-id">空间 ID</label>
            <input
              id="settings-workspace-id"
              data-testid="settings-workspace-id"
              className="mono"
              value={workspaceId}
              readOnly
            />
          </div>
          <div className="field">
            <label htmlFor="settings-workspace-role">当前角色</label>
            <input
              id="settings-workspace-role"
              data-testid="settings-workspace-role"
              value={workspaceRole}
              readOnly
            />
          </div>
          <div className="field">
            <label htmlFor="settings-workspace-owner">是否所有者</label>
            <input
              id="settings-workspace-owner"
              data-testid="settings-workspace-owner"
              value={workspaceOwner}
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

        {canLoadMembers && (
          <div className="settings-members">
            <div className="settings-members__head">
              <div className="settings-section__icon">
                <Users size={18} />
              </div>
              <div>
                <h3 className="settings-members__title">空间成员</h3>
                <p className="settings-members__hint">
                  仅所有者可查看；需手动加载。启用/停用表示成员关系状态，不代表在线。
                </p>
              </div>
            </div>
            <button
              type="button"
              className="btn btn-soft btn-sm"
              data-testid="load-members-button"
              onClick={() => void handleLoadMembers()}
              disabled={membersLoading}
            >
              {membersLoading
                ? "加载中…"
                : membersError
                  ? "重试加载成员"
                  : members !== null
                    ? "已加载成员"
                    : "加载成员列表"}
            </button>
            {membersStatus && (
              <div
                className={`settings-members__status${
                  membersError ? " is-error" : ""
                }`}
                data-testid="members-status"
                role="status"
                aria-live="polite"
              >
                {membersStatus}
              </div>
            )}
            {members !== null && members.length > 0 && (
              <ul className="settings-members__list" data-testid="members-list">
                {members.map((m, index) => (
                  <li
                    key={`${m.username}-${m.role}-${index}`}
                    className="settings-members__item"
                  >
                    <span className="settings-members__name">{m.username}</span>
                    <span className="settings-members__meta">
                      {authRoleLabel(m.role)}
                      {m.isOwner ? " · 所有者" : ""}
                      {" · "}
                      {m.isActive ? "启用" : "停用"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
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

      {/* 3. 解析策略（M3 四值：light|managed|local|ask；不得声称 OCR 已安装） */}
      <section className="card card-pad settings-section">
        <div className="settings-section__head">
          <div className="settings-section__icon">
            <Plug size={18} />
          </div>
          <div>
            <h2>解析策略</h2>
            <p>
              轻量解析默认可用；本机自动 OCR 需管理员另行准备运行时，未配置时请改用人工本地回传。
            </p>
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
              <option value="light">轻量解析</option>
              <option value="managed">本机自动 OCR</option>
              <option value="local">人工本地回传</option>
              <option value="ask">每次询问</option>
            </select>
          </div>
          <div>
            <Link to="/local-parser" className="btn btn-soft btn-sm">
              <Plug size={14} /> 打开人工本地回传
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
