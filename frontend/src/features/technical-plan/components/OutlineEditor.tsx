import {
  ChevronDown,
  ChevronUp,
  Plus,
  Trash2,
} from "lucide-react";
import {
  countTargetWords,
  flattenOutline,
} from "../lib/outlineTree";
import type { OutlineExpansionMode, OutlineNode } from "../types";

/**
 * 模块：大纲可编辑树
 * 用途：选中节点、改标题/目标字数、增删、同级上移下移、FREE/ALIGNED 模式展示。
 * 对接：状态由 useTechnicalPlanEditors 持有；AI 反馈在父级 AiFeedbackPanel。
 */

export type OutlineEditorProps = {
  outline: OutlineNode[];
  mode: OutlineExpansionMode;
  selectedId: string | null;
  moveFlags: { up: boolean; down: boolean };
  onSelect: (id: string) => void;
  onModeChange: (mode: OutlineExpansionMode) => void;
  onPatch: (
    id: string,
    patch: Partial<Pick<OutlineNode, "title" | "targetWords" | "description">>,
  ) => void;
  onDelete: (id: string) => void;
  onAddSibling: (afterId: string | null) => void;
  onAddChild: (parentId: string) => void;
  onMove: (id: string, direction: "up" | "down") => void;
};

export function OutlineEditor({
  outline,
  mode,
  selectedId,
  moveFlags,
  onSelect,
  onModeChange,
  onPatch,
  onDelete,
  onAddSibling,
  onAddChild,
  onMove,
}: OutlineEditorProps) {
  const flat = flattenOutline(outline);
  const selected = flat.find((n) => n.id === selectedId) ?? null;
  const totalWords = countTargetWords(outline);

  return (
    <div className="tp-outline-editor">
      <div className="tp-toolbar">
        <span className="badge badge-primary">
          模式：{mode === "ALIGNED" ? "ALIGNED（对齐技术要求）" : "FREE（自由组织）"}
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() =>
            onModeChange(mode === "ALIGNED" ? "FREE" : "ALIGNED")
          }
        >
          切换 {mode === "ALIGNED" ? "FREE" : "ALIGNED"}
        </button>
        <span
          className="mono"
          style={{ fontSize: 12, color: "var(--text-tertiary)" }}
        >
          节点 {flat.length} · 目标字数合计 {totalWords || "—"}
        </span>
        <div className="tp-toolbar__spacer" />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onAddSibling(selectedId)}
          title="在选中节点后添加同级；未选中则添加一级"
        >
          <Plus size={14} /> 添加同级
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          disabled={!selected || selected.level >= 3}
          onClick={() => selected && onAddChild(selected.id)}
          title="在选中节点下添加子节（最多三级）"
        >
          <Plus size={14} /> 添加子节
        </button>
      </div>

      {mode === "FREE" && (
        <div className="hint-banner" style={{ marginBottom: 12 }}>
          <span>
            FREE 模式：目录可按写作习惯自由组织，不强制对齐招标一级标题；导出前请自行核对形式评审要求。
          </span>
        </div>
      )}

      <div className="outline-tree">
        {flat.map((node) => {
          const isSelected = node.id === selectedId;
          return (
            <div
              key={node.id}
              className={`outline-node is-l${node.level}${isSelected ? " is-selected" : ""}`}
              onClick={() => onSelect(node.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(node.id);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <div className="outline-node__row">
                <div className="outline-node__main">
                  {isSelected ? (
                    <input
                      className="outline-node__title-input"
                      value={node.title}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) =>
                        onPatch(node.id, { title: e.target.value })
                      }
                      aria-label="章节标题"
                    />
                  ) : (
                    <span className="outline-node__title">{node.title}</span>
                  )}
                </div>
                <span className="outline-node__meta">
                  L{node.level}
                  {node.targetWords ? ` · ${node.targetWords} 字` : ""}
                </span>
              </div>

              {isSelected && (
                <div
                  className="outline-node__edit"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="outline-node__fields">
                    <label className="outline-node__field">
                      <span>目标字数</span>
                      <input
                        type="number"
                        min={0}
                        step={100}
                        value={node.targetWords ?? ""}
                        placeholder="—"
                        onChange={(e) => {
                          const v = e.target.value;
                          onPatch(node.id, {
                            targetWords: v === "" ? undefined : Number(v),
                          });
                        }}
                      />
                    </label>
                    <label className="outline-node__field outline-node__field--grow">
                      <span>说明</span>
                      <input
                        value={node.description ?? ""}
                        placeholder="可选：写作侧重点"
                        onChange={(e) =>
                          onPatch(node.id, { description: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  <div className="outline-node__actions">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={!moveFlags.up}
                      onClick={() => onMove(node.id, "up")}
                      title="同级上移"
                    >
                      <ChevronUp size={14} /> 上移
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      disabled={!moveFlags.down}
                      onClick={() => onMove(node.id, "down")}
                      title="同级下移"
                    >
                      <ChevronDown size={14} /> 下移
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => {
                        if (
                          window.confirm(
                            `确定删除「${node.title}」及其子节点？`,
                          )
                        ) {
                          onDelete(node.id);
                        }
                      }}
                    >
                      <Trash2 size={14} /> 删除
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {flat.length === 0 && (
        <div className="empty-state" style={{ padding: 24 }}>
          <strong>暂无大纲节点</strong>
          <p style={{ margin: "8px 0 12px", color: "var(--text-secondary)" }}>
            可手动添加一级目录，或通过 AI 反馈生成。
          </p>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => onAddSibling(null)}
          >
            <Plus size={14} /> 添加一级章节
          </button>
        </div>
      )}
    </div>
  );
}
