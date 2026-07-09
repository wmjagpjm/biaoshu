import { Link } from "react-router-dom";
import type { TechnicalPlanStepId, TechnicalPlanStepMeta } from "../types";

export const STEPS: TechnicalPlanStepMeta[] = [
  { id: "document", index: 1, title: "文档解析", description: "导入招标文件" },
  { id: "analysis", index: 2, title: "招标分析", description: "概述与评分点" },
  { id: "outline", index: 3, title: "大纲编辑", description: "三级目录" },
  { id: "facts", index: 4, title: "全局事实", description: "抗幻觉约束" },
  { id: "content", index: 5, title: "正文生成", description: "分章撰写" },
  { id: "export", index: 6, title: "导出交付", description: "Word 导出" },
];

type Props = {
  projectId: string;
  active: TechnicalPlanStepId;
  /** 已完成到第几步（含），用于样式 */
  doneUntil?: number;
};

/**
 * 技术方案步骤条
 * 用途：在各子页间导航，映射 C 端 technical-plan 六步工作流。
 */
export function StepStepper({ projectId, active, doneUntil = 0 }: Props) {
  return (
    <nav className="tp-stepper" aria-label="技术方案步骤">
      {STEPS.map((step) => {
        const isActive = step.id === active;
        const isDone = step.index <= doneUntil && !isActive;
        return (
          <Link
            key={step.id}
            to={`/technical-plan/${projectId}/${step.id}`}
            className={`tp-step${isActive ? " is-active" : ""}${isDone ? " is-done" : ""}`}
          >
            <span className="tp-step__idx">STEP {step.index}</span>
            <span className="tp-step__title">{step.title}</span>
          </Link>
        );
      })}
    </nav>
  );
}
