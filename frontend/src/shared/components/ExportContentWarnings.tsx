/**
 * 模块：导出正文完整性提醒展示（V1-H2）
 * 用途：将成功 export 任务的 result.contentWarnings 归一化为纯文本列表并展示；不阻断下载。
 * 对接：技术标导出页；后端任务 result.contentWarnings（数组字符串）。
 * 二次开发：不得解析 HTML/Markdown/URL/章节 ID/路径；不得用 dangerouslySetInnerHTML；
 *       不得新增网络、存储或把告警变成可点击外链；不得接入商务页或复用 imageWarnings。
 */

/** 单条提醒最大 Unicode 码点数（禁止按 UTF-16 码元截断）。 */
const MAX_WARNING_CHARS = 240;
/** 最多展示条数。 */
const MAX_WARNING_COUNT = 20;

/**
 * 模块：normalizeExportContentWarnings
 * 用途：把不可信任务结果收敛为可展示的纯字符串列表。
 * 对接：export 任务 result.contentWarnings。
 * 二次开发：非数组→[]；仅保留 trim 后非空字符串；最多 20 条；每条最多 240 个码点。
 *
 * 纯归一化函数与共享展示组件同文件导出；与 only-export-components 冲突时
 * 仅在本导出行最窄关闭，禁止全局关闭。
 */
// oxlint-disable-next-line react/only-export-components
export function normalizeExportContentWarnings(raw: unknown): string[] {
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

export type ExportContentWarningsProps = {
  /** 已归一化的提醒文本；空数组时不渲染。 */
  warnings: string[];
};

/**
 * 模块：ExportContentWarnings
 * 用途：无状态展示导出正文完整性提醒；有提醒时显示标题、条数、继续下载说明与文本列表。
 * 对接：TechnicalPlanWorkspace 导出步骤（仅技术标）。
 * 二次开发：仅 React 文本节点；无提醒返回 null；禁止生成链接或注入 HTML。
 */
export function ExportContentWarnings(props: ExportContentWarningsProps) {
  const warnings = props.warnings;
  if (!warnings.length) return null;

  return (
    <div
      role="region"
      aria-label="正文完整性提醒"
      style={{
        marginTop: 16,
        padding: "12px 14px",
        borderRadius: 8,
        border: "1px solid var(--border, #d0d7de)",
        background: "var(--surface-muted, #f6f8fa)",
      }}
    >
      <strong
        style={{
          display: "block",
          marginBottom: 6,
          fontSize: "var(--fs-md, 14px)",
        }}
      >
        正文完整性提醒
      </strong>
      <p
        style={{
          margin: "0 0 8px",
          color: "var(--text-secondary, #57606a)",
          fontSize: "var(--fs-sm, 13px)",
        }}
      >
        共 {warnings.length} 条。Word 已生成并继续下载，请补齐后定稿。
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
