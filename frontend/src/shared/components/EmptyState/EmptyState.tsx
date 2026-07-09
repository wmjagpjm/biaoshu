import type { ReactNode } from "react";
import "./EmptyState.css";

/**
 * 模块：统一空状态
 * 用途：各业务页无数据时的占位，避免页面各自拼 empty 样式。
 */

export type EmptyStateProps = {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
};

export function EmptyState({
  icon,
  title,
  description,
  action,
}: EmptyStateProps) {
  return (
    <div className="ui-empty card">
      {icon ? <div className="ui-empty__icon">{icon}</div> : null}
      <strong className="ui-empty__title">{title}</strong>
      {description ? (
        <div className="ui-empty__desc">{description}</div>
      ) : null}
      {action ? <div className="ui-empty__action">{action}</div> : null}
    </div>
  );
}
