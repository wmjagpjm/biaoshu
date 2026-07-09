import "./LoadingBlock.css";

/**
 * 模块：统一加载块
 * 用途：异步操作进行中的居中提示（查重/检查等）。
 */

export type LoadingBlockProps = {
  label?: string;
};

export function LoadingBlock({ label = "加载中…" }: LoadingBlockProps) {
  return (
    <div className="ui-loading card" role="status" aria-live="polite">
      <span className="ui-spinner" aria-hidden />
      <span className="ui-loading__label">{label}</span>
    </div>
  );
}
