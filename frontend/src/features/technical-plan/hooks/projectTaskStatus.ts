/**
 * 模块：项目任务安全 status 投影纯函数
 * 用途：将已严格解析的安全 status/progress 合并到当前任务对象；
 *       只覆盖两字段，返回新对象，不 mutation 原对象。
 * 对接：useProjectPipeline.reconcileCurrentTaskStatus 成功路径；
 *       E2E Q3-pure 直接 import 同一实现。
 * 二次开发：保持零 React / 零 api / 零 import.meta / 零 window·storage·console。
 */

/**
 * 用途：把安全 status/progress 投影到任意含这两字段的对象上。
 *       浅拷贝后仅覆盖 status、progress；其余字段（含 message/result/error）原样保留。
 */
export function applySafeStatusProjection<
  T extends { status: string; progress: number },
>(
  current: T,
  safe: { status: string; progress: number },
): T {
  return {
    ...current,
    status: safe.status,
    progress: safe.progress,
  };
}
