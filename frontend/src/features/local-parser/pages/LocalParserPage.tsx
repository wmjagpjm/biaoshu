import { Check, Download, KeyRound, Plug } from "lucide-react";

const steps = [
  {
    title: "下载本地解析助手",
    desc: "Windows 安装包 / 解压即用版本（内置或引导安装 MinerU 引擎）。",
  },
  {
    title: "粘贴工作空间 Token",
    desc: "在网站设置中复制 Token，助手内粘贴一次即可绑定当前账号工作空间。",
  },
  {
    title: "拖入招标 PDF",
    desc: "本机解析，进度在助手窗口显示；完成后自动回传到对应项目。",
  },
  {
    title: "回到网页继续写标书",
    desc: "技术方案「文档解析」步骤将显示本地解析结果，可进入招标分析。",
  },
];

/**
 * 本地解析插件说明页
 * 用途：降低服务器解析压力；MinerU 以易用外壳形态提供给用户本机运行。
 */
export function LocalParserPage() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>本地解析插件</h1>
          <p>
            复杂版式与扫描件建议本机解析。网站只收结果，不在服务器跑 MinerU。
            插件必须极简：安装 → Token → 拖文件。
          </p>
        </div>
        <div className="page-actions">
          <button type="button" className="btn btn-primary" disabled title="打包产物后续发布">
            <Download size={16} /> 下载助手（即将提供）
          </button>
        </div>
      </header>

      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: "var(--seal-soft)",
              display: "grid",
              placeItems: "center",
              color: "var(--seal)",
            }}
          >
            <Plug size={22} />
          </div>
          <div>
            <strong>连接状态</strong>
            <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
              未检测到本机助手 · 后端就绪后将轮询 / 助手主动心跳
            </div>
          </div>
          <span className="badge badge-muted" style={{ marginLeft: "auto" }}>
            离线
          </span>
        </div>
        <div className="field">
          <label htmlFor="token">工作空间 Token（演示）</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input id="token" className="mono" readOnly value="bs_demo_xxxxxxxxxxxxxxxx" />
            <button type="button" className="btn btn-ghost">
              <KeyRound size={16} /> 复制
            </button>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        {steps.map((s, i) => (
          <div key={s.title} className="card card-pad">
            <div
              className="mono"
              style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}
            >
              STEP 0{i + 1}
            </div>
            <strong style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Check size={16} color="var(--teal)" />
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
