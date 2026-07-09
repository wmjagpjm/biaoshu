import { Plus, Trash2 } from "lucide-react";
import type { GlobalFact } from "../types";

/**
 * 模块：全局事实编辑器
 * 用途：抗幻觉事实清单增删改；来源区分招标/知识库/手动。
 * 对接：状态由 useTechnicalPlanEditors 持有。
 */

export type FactsEditorProps = {
  facts: GlobalFact[];
  onAdd: () => void;
  onUpdate: (id: string, patch: Partial<Omit<GlobalFact, "id">>) => void;
  onRemove: (id: string) => void;
  onExtractDemo: () => void;
};

export function FactsEditor({
  facts,
  onAdd,
  onUpdate,
  onRemove,
  onExtractDemo,
}: FactsEditorProps) {
  return (
    <div className="tp-facts-editor">
      <div className="tp-toolbar">
        <strong>事实清单</strong>
        <span
          className="mono"
          style={{ fontSize: 12, color: "var(--text-tertiary)" }}
        >
          共 {facts.length} 条
        </span>
        <div className="tp-toolbar__spacer" />
        <button type="button" className="btn btn-ghost btn-sm" onClick={onAdd}>
          <Plus size={14} /> 手动添加
        </button>
        <button
          type="button"
          className="btn btn-soft btn-sm"
          onClick={() => {
            onExtractDemo();
          }}
          title="前端演示：追加示例事实"
        >
          从招标/知识库抽取
        </button>
      </div>

      {facts.length === 0 ? (
        <div className="empty-state" style={{ padding: 28 }}>
          <strong>暂无全局事实</strong>
          <p style={{ margin: "8px 0 12px" }}>
            添加后将注入后续各章生成 Prompt，降低幻觉与冲突表述。
          </p>
          <button type="button" className="btn btn-primary btn-sm" onClick={onAdd}>
            <Plus size={14} /> 添加第一条
          </button>
        </div>
      ) : (
        <div className="fact-list">
          {facts.map((f) => (
            <div key={f.id} className="fact-item fact-item--edit">
              <div className="field" style={{ margin: 0 }}>
                <label htmlFor={`fact-cat-${f.id}`}>类别</label>
                <input
                  id={`fact-cat-${f.id}`}
                  value={f.category}
                  onChange={(e) =>
                    onUpdate(f.id, { category: e.target.value })
                  }
                />
              </div>
              <div className="field" style={{ margin: 0 }}>
                <label htmlFor={`fact-body-${f.id}`}>内容</label>
                <textarea
                  id={`fact-body-${f.id}`}
                  rows={2}
                  value={f.content}
                  onChange={(e) =>
                    onUpdate(f.id, { content: e.target.value })
                  }
                  placeholder="可核验的事实陈述，避免空话…"
                />
              </div>
              <div className="fact-item__side">
                <div className="field" style={{ margin: 0 }}>
                  <label htmlFor={`fact-src-${f.id}`}>来源</label>
                  <select
                    id={`fact-src-${f.id}`}
                    value={f.source}
                    onChange={(e) =>
                      onUpdate(f.id, {
                        source: e.target.value as GlobalFact["source"],
                      })
                    }
                  >
                    <option value="tender">招标文件</option>
                    <option value="knowledge">知识库</option>
                    <option value="manual">手动</option>
                  </select>
                </div>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    if (window.confirm("确定删除这条事实？")) onRemove(f.id);
                  }}
                  aria-label="删除事实"
                >
                  <Trash2 size={14} /> 删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
