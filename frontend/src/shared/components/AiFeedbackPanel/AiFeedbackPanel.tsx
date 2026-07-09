import { useMemo, useState } from "react";
import { MessageSquarePlus, RefreshCw, Send } from "lucide-react";
import type { AiFeedbackRecord, FeedbackStage } from "../../types/aiFeedback";
import { FEEDBACK_STAGE_LABEL } from "../../types/aiFeedback";
import "./AiFeedbackPanel.css";

export type AiFeedbackPanelProps = {
  stage: FeedbackStage;
  /** 作用对象说明，如「当前大纲」「第 2 章」 */
  targetLabel?: string;
  targetId?: string;
  /** 快捷反馈短语 */
  presets?: string[];
  placeholder?: string;
  /** 已有历史（本阶段） */
  history?: AiFeedbackRecord[];
  disabled?: boolean;
  /**
   * 提交定向调整（不是整页重生成）
   * 前端阶段由父组件 mock；后端应携带原产物 + 反馈文本。
   */
  onRevise: (payload: {
    message: string;
    preserveStructure: boolean;
    targetId?: string;
    targetLabel?: string;
  }) => void | Promise<void>;
  /** 可选：整段重新生成（次要操作） */
  onRegenerate?: () => void;
};

const DEFAULT_PRESETS = [
  "突出评分高的章节",
  "压缩篇幅，合并相似小节",
  "补充风险与质量保障",
  "目录对齐招标一级标题",
  "加强实施与进度安排",
];

/**
 * 人工反馈 → AI 调整 面板
 * 用途：在任意 AI 产物旁提供「文字意见 → 定向修订」能力，
 * 与「手动改」「整段重生成」并列，作为核心交互。
 */
export function AiFeedbackPanel({
  stage,
  targetLabel,
  targetId,
  presets = DEFAULT_PRESETS,
  placeholder,
  history = [],
  disabled,
  onRevise,
  onRegenerate,
}: AiFeedbackPanelProps) {
  const [text, setText] = useState("");
  const [preserveStructure, setPreserveStructure] = useState(true);
  const [activePresets, setActivePresets] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState("");

  const stageLabel = FEEDBACK_STAGE_LABEL[stage];

  const composedPlaceholder = useMemo(
    () =>
      placeholder ??
      `例如：把「运维保障」提升为一级目录；实施章节再拆两级；总篇幅控制在 8 万字左右…`,
    [placeholder],
  );

  function togglePreset(p: string) {
    setActivePresets((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    );
  }

  async function handleSubmit() {
    const parts = [...activePresets];
    if (text.trim()) parts.push(text.trim());
    const message = parts.join("；");
    if (!message) return;

    setSubmitting(true);
    setToast("");
    try {
      await onRevise({
        message,
        preserveStructure,
        targetId,
        targetLabel,
      });
      setText("");
      setActivePresets([]);
      setToast("已记录修改意见，将在当前内容基础上修订");
      window.setTimeout(() => setToast(""), 4000);
    } finally {
      setSubmitting(false);
    }
  }

  const stageHistory = history.filter((h) => h.stage === stage).slice(0, 5);

  return (
    <section className="ai-feedback" aria-label={`${stageLabel} 修改意见`}>
      <div className="ai-feedback__head">
        <div>
          <h3 className="ai-feedback__title">
            <MessageSquarePlus size={18} color="var(--primary)" />
            修改意见
            <span className="badge badge-primary">{stageLabel}</span>
            {targetLabel ? <span className="badge badge-muted">{targetLabel}</span> : null}
          </h3>
          <p className="ai-feedback__desc">
            说明需要调整的重点、篇幅或结构。系统将在现有内容上修改
            {preserveStructure ? "，并尽量保持现有结构" : ""}，避免整段推倒重写。
          </p>
        </div>
      </div>

      <div className="ai-feedback__body">
        {presets.length > 0 && (
          <div className="ai-feedback__presets" role="group" aria-label="快捷意见">
            {presets.map((p) => (
              <button
                key={p}
                type="button"
                className={`ai-feedback__preset${activePresets.includes(p) ? " is-on" : ""}`}
                onClick={() => togglePreset(p)}
                disabled={disabled || submitting}
              >
                {p}
              </button>
            ))}
          </div>
        )}

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={composedPlaceholder}
          disabled={disabled || submitting}
        />

        <div className="ai-feedback__meta">
          <label className="ai-feedback__check">
            <input
              type="checkbox"
              checked={preserveStructure}
              onChange={(e) => setPreserveStructure(e.target.checked)}
              disabled={disabled || submitting}
            />
            尽量保留现有结构，只做定向修改
          </label>

          <div className="ai-feedback__actions">
            {onRegenerate && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={onRegenerate}
                disabled={disabled || submitting}
                title="重新生成本段，不带入本次修改意见"
              >
                <RefreshCw size={14} /> 重新生成
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void handleSubmit()}
              disabled={
                disabled ||
                submitting ||
                (!text.trim() && activePresets.length === 0)
              }
            >
              <Send size={14} />
              {submitting ? "提交中…" : "按意见修改"}
            </button>
          </div>
        </div>

        {toast ? <div className="ai-feedback__toast">{toast}</div> : null}

        {stageHistory.length > 0 && (
          <div className="ai-feedback__history">
            <p className="ai-feedback__history-title">本阶段调整记录</p>
            {stageHistory.map((h) => (
              <div key={h.id} className="ai-feedback__item">
                <div className="ai-feedback__item-top">
                  <span>{new Date(h.createdAt).toLocaleString("zh-CN")}</span>
                  <span className="badge badge-muted">
                    {h.status === "applied"
                      ? "已应用"
                      : h.status === "applying"
                        ? "调整中"
                        : h.status === "failed"
                          ? "失败"
                          : "排队"}
                  </span>
                  {h.targetLabel ? <span>{h.targetLabel}</span> : null}
                </div>
                <div className="ai-feedback__item-msg">{h.message}</div>
                {h.resultSummary ? (
                  <div className="ai-feedback__item-result">{h.resultSummary}</div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
