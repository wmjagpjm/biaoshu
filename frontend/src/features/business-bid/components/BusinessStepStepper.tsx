import { Link } from "react-router-dom";
import type { BusinessBidStepId, BusinessBidStepMeta } from "../types";

/**
 * 模块：商务标步骤条
 * 用途：六步导航，映射「解析 → 资格 → 目录 → 报价 → 承诺 → 导出」。
 * 对接：纯前端路由；后端任务状态可在 is-done 上叠加。
 */

export const BUSINESS_STEPS: BusinessBidStepMeta[] = [
  { id: "parse", index: 1, title: "条款解析", description: "商务与资格条款" },
  { id: "qualify", index: 2, title: "资格响应", description: "逐条响应与证明" },
  { id: "toc", index: 3, title: "目录清单", description: "递交材料勾选" },
  { id: "quote", index: 4, title: "报价说明", description: "分项与偏离" },
  { id: "commit", index: 5, title: "授权承诺", description: "固定格式正文" },
  { id: "export", index: 6, title: "导出交付", description: "Word 打包" },
];

type Props = {
  projectId: string;
  active: BusinessBidStepId;
  /** 已完成到第几步（含），用于样式 */
  doneUntil?: number;
};

export function BusinessStepStepper({
  projectId,
  active,
  doneUntil = 0,
}: Props) {
  return (
    <nav className="bb-stepper" aria-label="商务标步骤">
      {BUSINESS_STEPS.map((step) => {
        const isActive = step.id === active;
        const isDone = step.index <= doneUntil && !isActive;
        return (
          <Link
            key={step.id}
            to={`/business-bid/${projectId}/${step.id}`}
            className={`bb-step${isActive ? " is-active" : ""}${isDone ? " is-done" : ""}`}
          >
            <span className="bb-step__idx">STEP {step.index}</span>
            <span className="bb-step__title">{step.title}</span>
            <span className="bb-step__desc">{step.description}</span>
          </Link>
        );
      })}
    </nav>
  );
}
