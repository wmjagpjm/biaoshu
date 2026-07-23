/**
 * 模块：managed 解析失败识别与固定中文
 * 用途：纯谓词判断当前项目 managed parse 失败；导出固定提示与人工入口标签。
 * 对接：技术标/商务标工作区失败横幅；契约 M3 冻结决策 §6。
 * 二次开发：不读 diagnosticCode；不依赖 React/pipeline；不做 IO；禁止回显 task.error/路径/命令。
 */

/** managed 失败界面固定中文（禁止拼接 task.error / diagnosticCode）。 */
export const MANAGED_PARSE_UNAVAILABLE_MESSAGE =
  "本机自动 OCR 暂不可用，可改用人工本地回传";

/** 项目化人工本地回传链接可见文案。 */
export const MANAGED_PARSE_LOCAL_FALLBACK_LINK_LABEL = "前往人工本地回传";

/** 谓词可接受的最小任务形状（不依赖 PipelineTask 类型）。 */
export type ManagedParseTaskLike = {
  projectId?: string | null;
  type?: string | null;
  status?: string | null;
  error?: string | null;
  message?: string | null;
  result?: Record<string, unknown> | null;
};

/**
 * 模块：isCurrentManagedParseFailure
 * 用途：仅当任务属于当前 projectId、type=parse、status=failed、
 *       result.engine=managed，且 pipelineError 精确等于
 *       task.error || task.message || 「任务失败」时返回 true。
 * 对接：工作区横幅替换 pipeline.error；新 network/upload 错误须返回 false 以恢复真实文案。
 * 二次开发：禁止读 diagnosticCode；禁止放宽 projectId 或 engine 比较。
 */
export function isCurrentManagedParseFailure(
  projectId: string,
  task: ManagedParseTaskLike | null | undefined,
  pipelineError: string | null | undefined,
): boolean {
  const pid = (projectId || "").trim();
  if (!pid || !task) return false;
  if ((task.projectId || "").trim() !== pid) return false;
  if (task.type !== "parse") return false;
  if (task.status !== "failed") return false;
  const engine =
    task.result && typeof task.result === "object"
      ? (task.result as { engine?: unknown }).engine
      : undefined;
  if (engine !== "managed") return false;
  // 与 useProjectPipeline 失败写 error 规则对齐，关联当前 pipeline.error
  const associated =
    (typeof task.error === "string" && task.error) ||
    (typeof task.message === "string" && task.message) ||
    "任务失败";
  return pipelineError === associated;
}
