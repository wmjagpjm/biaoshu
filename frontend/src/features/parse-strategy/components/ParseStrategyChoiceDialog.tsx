/**
 * 模块：解析方式一次性选择框
 * 用途：ask 策略下让用户选择本次「在线轻量」或「本地 MinerU 回传」。
 * 对接：useWorkspaceParseStrategy；技术标/商务标 handleParse。
 * 二次开发：仅发出当前一次选择事件；不得读写设置、文件、编辑态或创建任务。
 */

import "./ParseStrategyChoiceDialog.css";

export type ParseStrategyChoice = "light" | "local";

export type ParseStrategyChoiceDialogProps = {
  open: boolean;
  /** 用途：用户确认本次选择；父级负责关闭并执行 light/local 路径。 */
  onChoose: (choice: ParseStrategyChoice) => void;
  /** 用途：取消/关闭；父级不得创建解析任务。 */
  onCancel: () => void;
};

/**
 * 模块：ParseStrategyChoiceDialog
 * 用途：可访问模态选择框，三按钮明确：在线轻量 / 本地回传 / 取消。
 * 对接：role=dialog aria-label=选择解析方式，供 E2E 定位。
 * 二次开发：选择不回写 parseStrategy；本地文案须声明不在服务器启动 MinerU。
 */
export function ParseStrategyChoiceDialog({
  open,
  onChoose,
  onCancel,
}: ParseStrategyChoiceDialogProps) {
  if (!open) return null;

  return (
    <div
      className="parse-strategy-dialog-backdrop"
      role="presentation"
      onClick={onCancel}
    >
      <div
        className="parse-strategy-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="选择解析方式"
        onClick={(event) => event.stopPropagation()}
      >
        <h2>选择解析方式</h2>
        <p>
          本次选择仅作用于当前一次解析，不会修改工作空间默认策略。本地路径为
          <strong>本地回传，不在服务器启动 MinerU</strong>。
        </p>
        <div className="parse-strategy-dialog__actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onChoose("light")}
          >
            在线轻量解析
          </button>
          <button
            type="button"
            className="btn btn-soft"
            onClick={() => onChoose("local")}
          >
            本地 MinerU 回传
          </button>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
