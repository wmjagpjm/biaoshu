/**
 * 模块：导出图片失效引用告警展示
 * 用途：将成功 export 任务的 result.imageWarnings 归一化为纯文本列表并展示；不阻断下载。
 * 对接：技术标/商务标导出页；后端任务 result.imageWarnings（数组字符串）。
 * 二次开发：不得解析 HTML/Markdown/URL/文件 ID/路径；不得用 dangerouslySetInnerHTML；
 *       不得新增网络、存储或把告警变成可点击外链；后端仍是唯一判定方。
 */

/** 单条告警最大 Unicode 字符数（按码点，禁止按 UTF-16 码元截断）。 */
const MAX_WARNING_CHARS = 240;
/** 最多展示条数。 */
const MAX_WARNING_COUNT = 20;

/**
 * 模块：normalizeExportImageWarnings
 * 用途：把不可信任务结果收敛为可展示的纯字符串列表。
 * 对接：export 任务 result.imageWarnings。
 * 二次开发：仅保留 trim 后非空字符串；最多 20 条；每条最多 240 个码点；非法结构返回空数组。
 *
 * 纯归一化函数必须与共享展示组件同文件：技术标/商务标两页复用同一收敛规则，
 * 且 P9D 白名单禁止新增文件拆分；与组件同文件导出会触发 only-export-components，
 * 因此仅在本导出行最窄关闭该规则，禁止全局关闭。
 */
// oxlint-disable-next-line react/only-export-components
export function normalizeExportImageWarnings(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const item of raw) {
    if (typeof item !== "string") continue;
    const trimmed = item.trim();
    if (!trimmed) continue;
    const chars = Array.from(trimmed);
    const clipped =
      chars.length > MAX_WARNING_CHARS
        ? chars.slice(0, MAX_WARNING_CHARS).join("")
        : trimmed;
    out.push(clipped);
    if (out.length >= MAX_WARNING_COUNT) break;
  }
  return out;
}

export type ExportImageWarningsProps = {
  /** 已归一化的告警文本；空数组时不渲染。 */
  warnings: string[];
};

/**
 * 模块：ExportImageWarnings
 * 用途：无状态展示导出图片降级原因；有告警时显示标题、条数、继续下载说明与文本列表。
 * 对接：TechnicalPlanWorkspace / BusinessBidWorkspace 导出步骤。
 * 二次开发：仅 React 文本节点；无告警返回 null；禁止生成链接或注入 HTML。
 */
export function ExportImageWarnings(props: ExportImageWarningsProps) {
  const warnings = props.warnings;
  if (!warnings.length) return null;

  return (
    <div
      role="region"
      aria-label="导出图片告警"
      style={{
        marginTop: 16,
        padding: "12px 14px",
        borderRadius: 8,
        border: "1px solid var(--border, #d0d7de)",
        background: "var(--surface-muted, #f6f8fa)",
      }}
    >
      <strong style={{ display: "block", marginBottom: 6, fontSize: "var(--fs-md, 14px)" }}>
        导出图片告警
      </strong>
      <p
        style={{
          margin: "0 0 8px",
          color: "var(--text-secondary, #57606a)",
          fontSize: "var(--fs-sm, 13px)",
        }}
      >
        共 {warnings.length} 条。Word 已生成并继续下载，请在文档中检查降级位置。
      </p>
      <ul
        style={{
          margin: 0,
          paddingLeft: 20,
          fontSize: "var(--fs-sm, 13px)",
          color: "var(--text-primary, #24292f)",
        }}
      >
        {warnings.map((text, index) => (
          <li key={`${index}-${text.slice(0, 24)}`} style={{ marginBottom: 4 }}>
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}
