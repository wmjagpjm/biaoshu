import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, Download, KeyRound, Plug, Send } from "lucide-react";
import { apiFetch, getApiBase } from "../../../shared/lib/api";

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
    desc: "使用下方表单，或 curl 调用 POST /api/projects/{id}/parse-callback。",
  },
  {
    title: "回到网页继续写标书",
    desc: "技术方案「文档解析」步骤将显示回传结果，可进入招标分析。",
  },
];

/**
 * 模块：本地解析插件说明 + Markdown 回传表单
 * 用途：MinerU 本机解析后回写项目 parsedMarkdown，降低服务器解析压力。
 * 对接：POST /api/projects/{id}/parse-callback
 */
export function LocalParserPage() {
  const [projectId, setProjectId] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const base = getApiBase();
  const curlSample = `curl -X POST "${base}/projects/PROJ_ID/parse-callback" ^
  -H "Content-Type: application/json" ^
  -H "X-Local-Token: 可选" ^
  -d "{\\"markdown\\":\\"# 标题\\\\n正文...\\",\\"source\\":\\"mineru\\"}"`;

  async function handleSubmit() {
    setBusy(true);
    setMsg("");
    setErr("");
    const pid = projectId.trim();
    if (!pid) {
      setErr("请填写项目 ID");
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
    <div className="page">
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

      {(msg || err) && (
        <div
          className={`tp-source-banner ${err ? "is-local" : "is-api"}`}
          role="status"
          style={{ marginBottom: 12 }}
        >
          {err || msg}
        </div>
      )}

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
