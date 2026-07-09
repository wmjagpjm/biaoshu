import { Lock, Unlock } from "lucide-react";
import type { ProjectGenerationGuidance } from "../../../shared/types/aiFeedback";

type Props = {
  guidance: ProjectGenerationGuidance;
  onChange: (patch: Partial<ProjectGenerationGuidance>) => void;
  /** compact：后续步骤只读摘要 */
  mode?: "edit" | "summary";
};

/**
 * 项目级生成要求
 * 用途：招标分析后编辑字数、侧重点、格式要求，并向下传递给大纲/正文 AI 任务。
 */
export function ProjectGuidanceCard({ guidance, onChange, mode = "edit" }: Props) {
  if (mode === "summary") {
    const chips: string[] = [];
    if (guidance.targetWordCount) chips.push(`目标约 ${guidance.targetWordCount.toLocaleString()} 字`);
    if (guidance.chapterFocus?.trim()) chips.push("已设章节侧重点");
    if (guidance.formatRequirements?.trim()) chips.push("已设格式要求");
    if (guidance.extraRequirements?.trim()) chips.push("已有补充要求");

    return (
      <div className="card card-pad" style={{ marginBottom: 12, background: "var(--primary-soft)", borderColor: "rgba(100,56,255,0.15)" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <strong style={{ color: "var(--primary-deep)" }}>已注入后续生成的项目要求</strong>
          {chips.length === 0 ? (
            <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
              尚未填写（可在「招标分析」步补充）
            </span>
          ) : (
            chips.map((c) => (
              <span key={c} className="badge badge-primary">
                {c}
              </span>
            ))
          )}
        </div>
        {(guidance.chapterFocus || guidance.formatRequirements || guidance.extraRequirements) && (
          <div style={{ marginTop: 10, fontSize: "var(--fs-sm)", color: "var(--text-body)", lineHeight: 1.6 }}>
            {guidance.chapterFocus ? <div>· 侧重点：{guidance.chapterFocus}</div> : null}
            {guidance.formatRequirements ? <div>· 格式：{guidance.formatRequirements}</div> : null}
            {guidance.extraRequirements ? <div>· 其它：{guidance.extraRequirements}</div> : null}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
        <div>
          <strong style={{ fontSize: "var(--fs-md)" }}>生成要求（将带入大纲 / 正文 AI）</strong>
          <p style={{ margin: "6px 0 0", fontSize: "var(--fs-sm)", color: "var(--text-secondary)" }}>
            解析结果可人工修订；这里补充字数、侧重点与格式，避免下一阶段只能「盲生成」。
          </p>
        </div>
        <button
          type="button"
          className={`btn btn-sm ${guidance.lockedForNextStage ? "btn-soft" : "btn-ghost"}`}
          onClick={() =>
            onChange({ lockedForNextStage: !guidance.lockedForNextStage })
          }
        >
          {guidance.lockedForNextStage ? (
            <>
              <Lock size={14} /> 已确认带入
            </>
          ) : (
            <>
              <Unlock size={14} /> 标记为已确认
            </>
          )}
        </button>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <div className="field">
          <label htmlFor="gw-words">目标总字数（约）</label>
          <input
            id="gw-words"
            type="number"
            min={5000}
            step={1000}
            value={guidance.targetWordCount ?? ""}
            onChange={(e) =>
              onChange({
                targetWordCount: e.target.value ? Number(e.target.value) : undefined,
              })
            }
            placeholder="例如 80000"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-focus">章节侧重点 / 必须展开的内容</label>
          <textarea
            id="gw-focus"
            value={guidance.chapterFocus ?? ""}
            onChange={(e) => onChange({ chapterFocus: e.target.value })}
            placeholder="例如：重点写信创适配与等保三级；实施进度按 180 天里程碑展开；弱化通用产品介绍…"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-format">特殊格式 / 目录强制要求</label>
          <textarea
            id="gw-format"
            value={guidance.formatRequirements ?? ""}
            onChange={(e) => onChange({ formatRequirements: e.target.value })}
            placeholder="例如：一级目录必须与招标文件一致；需含横道图说明位；正文字体与导出模板按政务投标…"
          />
        </div>
        <div className="field">
          <label htmlFor="gw-extra">其它补充要求</label>
          <textarea
            id="gw-extra"
            value={guidance.extraRequirements ?? ""}
            onChange={(e) => onChange({ extraRequirements: e.target.value })}
            placeholder="例如：避免空话套话；业绩只写同城同类；运维承诺与全局事实一致…"
          />
        </div>
      </div>
    </div>
  );
}
