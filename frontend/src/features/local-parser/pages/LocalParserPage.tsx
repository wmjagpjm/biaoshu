/**
 * 模块：本地解析插件说明 + required 一次性回传票据 / disabled 旧 Markdown 回传
 * 用途：
 *   - AUTH_MODE=required 且 strict bid_writer：显式签发短期单次票据，仅组件内存展示固定 curl
 *   - AUTH_MODE=disabled 个人兼容：保留 X-Local-Token + 旧 parse-callback 手工表单
 * 对接：
 *   - POST /api/projects/{id}/parse-callback-ticket（仅显式点击，无 body）
 *   - POST /api/projects/{id}/parse-callback（disabled 旧路径）
 *   - 公共回调固定路径 /api/local-parser/callback + 头 X-Local-Parse-Ticket（仅 curl 文案，页面绝不自动 POST）
 *   - P8B local 跳转 `/local-parser?projectId=` 仅预填
 * 二次开发：
 *   - 禁止挂载/项目 ID 变化/计时器/刷新自动签发、轮询或重试
 *   - 票据禁止写入 localStorage/sessionStorage/IndexedDB/URL/模块全局/日志/剪贴板 API
 *   - 禁止自动启动 MinerU/Docling、禁止外网、禁止读本地文件、禁止新增依赖
 *   - 签发错误只显示固定中文，不拼接服务端 detail/code/path/projectId/票据
 */

import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, Download, KeyRound, Plug, Send, Ticket } from "lucide-react";
import { apiFetch, getApiBase } from "../../../shared/lib/api";
import { useAuthSession } from "../../auth/hooks/useAuthSession";

/** 固定公开回调路径（不得信任签发响应 callbackPath 拼 URL） */
const FIXED_CALLBACK_PATH = "/api/local-parser/callback";
/** 固定票据请求头名 */
const FIXED_TICKET_HEADER = "X-Local-Parse-Ticket";
/** 签发失败固定中文（脱敏） */
const FIXED_ISSUE_ERROR = "生成一次性回传票据失败，请稍后重试";
/** 项目 ID 空白固定提示 */
const EMPTY_PROJECT_ID_MSG = "请填写项目 ID";
/** disabled 个人兼容说明关键字 */
const COMPAT_NOTICE = "无需一次性票据";

const steps = [
  {
    title: "本机运行 MinerU / 解析助手",
    desc: "在保密机本地解析 PDF/扫描件，得到 Markdown 全文。",
  },
  {
    title: "填写项目 ID",
    desc: "在「我的项目」进入工作区后，URL 中 technical-plan 后的一段即为项目 ID。",
  },
  {
    title: "粘贴 Markdown 回传",
    desc: "个人兼容模式使用下方表单；required 模式用一次性票据 curl 调用公共回调。",
  },
  {
    title: "回到网页继续写标书",
    desc: "技术方案「文档解析」步骤将显示回传结果，可进入招标分析。",
  },
];

/**
 * 模块：LocalParserPage
 * 用途：本地回传入口；required 内存票据 / disabled 旧表单双分支。
 * 对接：useAuthSession.authRequired + canAccessBusiness；签发与旧 parse-callback API。
 * 二次开发：query 仅预填，绝不自动提交/签发；路由层 RequireBusiness 已挡非 bid_writer。
 */
export function LocalParserPage() {
  const [searchParams] = useSearchParams();
  const { authRequired, canAccessBusiness } = useAuthSession();

  // required 且可进业务（strict bid_writer）才展示一次性票据入口
  const showOneTimeTicket = authRequired === true && canAccessBusiness;
  // disabled 个人兼容：完整保留旧手工表单
  const showLegacyForm = authRequired === false;

  const [projectId, setProjectId] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  // 一次性票据：仅当前组件内存，刷新/离开即丢失
  const [ticket, setTicket] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [issueBusy, setIssueBusy] = useState(false);
  const [issueErr, setIssueErr] = useState("");

  useEffect(() => {
    const fromQuery = (searchParams.get("projectId") || "").trim();
    if (fromQuery) {
      setProjectId(fromQuery);
    }
  }, [searchParams]);

  const base = getApiBase();
  const curlSample = `curl -X POST "${base}/projects/PROJ_ID/parse-callback" ^
  -H "Content-Type: application/json" ^
  -H "X-Local-Token: 可选" ^
  -d "{\\"markdown\\":\\"# 标题\\\\n正文...\\",\\"source\\":\\"mineru\\"}"`;

  /**
   * 固定路径 + 内存票据的可执行 Windows curl。
   * 用当前页 origin + 字面 FIXED_CALLBACK_PATH 拼绝对 URL；命令名 curl.exe 避开 PowerShell alias。
   * 绝不用签发响应 callbackPath。
   */
  const ticketCallbackAbsoluteUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}${FIXED_CALLBACK_PATH}`
      : FIXED_CALLBACK_PATH;
  const ticketCurl = ticket
    ? `curl.exe -X POST "${ticketCallbackAbsoluteUrl}" ^
  -H "Content-Type: application/json" ^
  -H "${FIXED_TICKET_HEADER}: ${ticket}" ^
  -d "{\\"markdown\\":\\"# 标题\\\\n正文...\\",\\"source\\":\\"mineru\\"}"`
    : "";

  /**
   * 用途：显式签发一次性回传票据；每次点击先清空旧票据/旧错误。
   * 对接：POST /projects/{id}/parse-callback-ticket，无 body；CSRF 由 apiFetch 附加。
   * 二次开发：禁止自动调用；失败固定中文，不拼接 e.message。
   */
  async function handleIssueTicket() {
    // 每次显式重新签发：先清空旧票据与旧错误
    setTicket("");
    setExpiresAt("");
    setIssueErr("");
    const pid = projectId.trim();
    if (!pid) {
      setIssueErr(EMPTY_PROJECT_ID_MSG);
      return;
    }
    setIssueBusy(true);
    try {
      const res = await apiFetch<{
        ticket?: unknown;
        expiresAt?: unknown;
      }>(`/projects/${encodeURIComponent(pid)}/parse-callback-ticket`, {
        method: "POST",
      });
      const rawTicket =
        typeof res?.ticket === "string" ? res.ticket.trim() : "";
      if (!rawTicket) {
        setIssueErr(FIXED_ISSUE_ERROR);
        return;
      }
      setTicket(rawTicket);
      if (typeof res?.expiresAt === "string" && res.expiresAt.trim()) {
        setExpiresAt(res.expiresAt.trim());
      }
    } catch {
      // 固定中文：不得拼接服务端 detail/code/path/projectId/票据
      setIssueErr(FIXED_ISSUE_ERROR);
    } finally {
      setIssueBusy(false);
    }
  }

  /**
   * 用途：disabled 旧手工 Markdown 回传。
   * 对接：POST /projects/{id}/parse-callback + 可选 X-Local-Token。
   */
  async function handleSubmit() {
    setBusy(true);
    setMsg("");
    setErr("");
    const pid = projectId.trim();
    if (!pid) {
      setErr(EMPTY_PROJECT_ID_MSG);
      setBusy(false);
      return;
    }
    if (!markdown.trim()) {
      setErr("请粘贴 Markdown");
      setBusy(false);
      return;
    }
    try {
      const headers: Record<string, string> = {};
      if (token.trim()) headers["X-Local-Token"] = token.trim();
      const res = await apiFetch<{ ok: boolean; chars: number; taskId: string }>(
        `/projects/${encodeURIComponent(pid)}/parse-callback`,
        {
          method: "POST",
          headers,
          body: JSON.stringify({
            markdown: markdown.trim(),
            source: "mineru",
            filename: "local-mineru.md",
          }),
        },
      );
      setMsg(
        `回传成功（${res.chars} 字）。可打开工作区文档解析步查看。任务 ${res.taskId}`,
      );
    } catch (e) {
      setErr((e as { message?: string })?.message || "回传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page" data-testid="local-parser-page">
      <header className="page-header">
        <div>
          <h1>本地解析插件</h1>
          <p>
            复杂版式与扫描件建议本机解析。网站只收 Markdown 结果，不在服务器跑
            MinerU。
          </p>
        </div>
        <div className="page-actions">
          <Link to="/technical-plan" className="btn btn-ghost">
            我的项目
          </Link>
          <button type="button" className="btn btn-primary" disabled title="打包产物后续发布">
            <Download size={16} /> 下载助手（即将提供）
          </button>
        </div>
      </header>

      {showLegacyForm && (msg || err) && (
        <div
          className={`tp-source-banner ${err ? "is-local" : "is-api"}`}
          role="status"
          style={{ marginBottom: 12 }}
        >
          {err || msg}
        </div>
      )}

      {showOneTimeTicket && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 12,
                background: "var(--primary-soft)",
                display: "grid",
                placeItems: "center",
                color: "var(--primary)",
              }}
            >
              <Ticket size={22} />
            </div>
            <div>
              <strong>生成一次性回传票据</strong>
              <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                仅当前页面内存展示；刷新或离开即丢失。固定 10 分钟、单项目、成功消费一次。
              </div>
            </div>
          </div>

          <div className="field">
            <label htmlFor="pid">项目 ID</label>
            <input
              id="pid"
              className="mono"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              placeholder="proj_xxxxxxxx"
            />
          </div>

          {issueErr && (
            <div
              className="tp-source-banner is-local"
              role="alert"
              data-testid="lp-ticket-error"
              style={{ marginBottom: 12 }}
            >
              {issueErr}
            </div>
          )}

          <button
            type="button"
            className="btn btn-primary"
            disabled={issueBusy}
            onClick={() => void handleIssueTicket()}
          >
            <Ticket size={16} />{" "}
            {issueBusy ? "签发中…" : "生成一次性回传票据"}
          </button>

          {ticket ? (
            <div style={{ marginTop: 16 }}>
              <div className="field">
                <label>一次性票据（仅内存）</label>
                <div
                  className="mono"
                  data-testid="lp-ticket-value"
                  style={{
                    padding: 10,
                    borderRadius: 8,
                    background: "var(--surface-card)",
                    wordBreak: "break-all",
                  }}
                >
                  {ticket}
                </div>
              </div>
              {expiresAt ? (
                <div className="field">
                  <label>过期时间（服务端）</label>
                  <div className="mono" data-testid="lp-ticket-expires">
                    {expiresAt}
                  </div>
                </div>
              ) : null}
              <div className="field">
                <label>固定回调路径</label>
                <div className="mono" data-testid="lp-ticket-callback-path">
                  {FIXED_CALLBACK_PATH}
                </div>
              </div>
              <div className="field">
                <label>请求头</label>
                <div className="mono" data-testid="lp-ticket-header-name">
                  {FIXED_TICKET_HEADER}
                </div>
              </div>
              <div className="field">
                <label>curl 示例（Windows，固定路径 + 内存票据）</label>
                <pre
                  className="mono"
                  data-testid="lp-ticket-curl"
                  style={{
                    marginTop: 10,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    background: "var(--surface-card)",
                    padding: 12,
                    borderRadius: 8,
                  }}
                >
                  {ticketCurl}
                </pre>
              </div>
              <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0 }}>
                请在本机助手中使用上述 curl 回传 Markdown。本页不会自动调用公共回调，也不会启动
                MinerU/Docling。
              </p>
            </div>
          ) : null}
        </div>
      )}

      {showLegacyForm && (
        <>
          <div
            className="tp-source-banner is-api"
            data-testid="lp-compat-notice"
            role="status"
            style={{ marginBottom: 12 }}
          >
            个人兼容模式：{COMPAT_NOTICE}。可继续使用下方 X-Local-Token 与旧手工
            Markdown 回传表单。
          </div>

          <div className="card card-pad" style={{ marginBottom: 16 }} data-testid="lp-old-form">
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 12,
                  background: "var(--primary-soft)",
                  display: "grid",
                  placeItems: "center",
                  color: "var(--primary)",
                }}
              >
                <Plug size={22} />
              </div>
              <div>
                <strong>Markdown 回传（已可用）</strong>
                <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  将 MinerU 输出粘贴到下方，写入指定项目的解析结果
                </div>
              </div>
            </div>

            <div className="field">
              <label htmlFor="pid">项目 ID</label>
              <input
                id="pid"
                className="mono"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                placeholder="proj_xxxxxxxx"
              />
            </div>
            <div className="field">
              <label htmlFor="token">X-Local-Token（可选，后端配置了才需要）</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  id="token"
                  className="mono"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="与 backend LOCAL_PARSER_TOKEN 一致"
                />
                <button type="button" className="btn btn-ghost" title="仅提示">
                  <KeyRound size={16} />
                </button>
              </div>
            </div>
            <div className="field">
              <label htmlFor="md">解析 Markdown</label>
              <textarea
                id="md"
                value={markdown}
                onChange={(e) => setMarkdown(e.target.value)}
                placeholder="# 招标文件&#10;&#10;…"
                style={{ minHeight: 180, width: "100%" }}
              />
            </div>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void handleSubmit()}
            >
              <Send size={16} /> {busy ? "提交中…" : "回传到项目"}
            </button>
          </div>

          <div className="card card-pad" style={{ marginBottom: 16 }}>
            <strong>curl 示例（Windows）</strong>
            <pre
              className="mono"
              style={{
                marginTop: 10,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                background: "var(--surface-card)",
                padding: 12,
                borderRadius: 8,
              }}
            >
              {curlSample}
            </pre>
          </div>
        </>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        {steps.map((s, i) => (
          <div key={s.title} className="card card-pad">
            <div
              className="mono"
              style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 8 }}
            >
              STEP 0{i + 1}
            </div>
            <strong style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Check size={16} color="var(--success)" />
              {s.title}
            </strong>
            <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--text-secondary)" }}>
              {s.desc}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
