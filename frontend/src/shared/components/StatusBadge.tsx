import type { ProjectStatus, TaskStatus } from "../types/workspace";

const projectMap: Record<ProjectStatus, { label: string; className: string }> = {
  draft: { label: "草稿", className: "badge-muted" },
  analyzing: { label: "分析中", className: "badge-gold" },
  writing: { label: "撰写中", className: "badge-teal" },
  reviewing: { label: "审校中", className: "badge-gold" },
  exported: { label: "已导出", className: "badge-seal" },
};

const taskMap: Record<TaskStatus, { label: string; className: string }> = {
  pending: { label: "排队中", className: "badge-muted" },
  running: { label: "进行中", className: "badge-teal" },
  paused: { label: "已暂停", className: "badge-gold" },
  success: { label: "已完成", className: "badge-seal" },
  failed: { label: "失败", className: "badge-muted" },
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  const item = projectMap[status];
  return <span className={`badge ${item.className}`}>{item.label}</span>;
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const item = taskMap[status];
  return <span className={`badge ${item.className}`}>{item.label}</span>;
}
