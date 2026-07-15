import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  FolderTree,
  Plus,
  Trash2,
} from "lucide-react";
import {
  countTargetWords,
  flattenOutline,
} from "../lib/outlineTree";
import type { OutlineNode } from "../types";
import "./OutlineStepWorkspace.css";

/**
 * 模块：STEP 03 目录生成（易标式三栏）
 * 用途：左生成过程、中目录树、右详情编辑；底栏步骤导航。
 * 对接：TechnicalPlanWorkspace 大纲步；outline 来自服务端 editor-state。
 * 二次开发：P11C 已移除固定 DEMO_LOGS/伪时间戳；仅据 generating/progress/outline 推导有限状态。
 *       未接 onReset 时不得显示「重置」按钮。
 */

export type OutlineStepWorkspaceProps = {
  projectId: string;
  outline: OutlineNode[];
  selectedId: string | null;
  moveFlags: { up: boolean; down: boolean };
  generating?: boolean;
  progress?: number;
  onSelect: (id: string | null) => void;
  onPatch: (
    id: string,
    patch: Partial<Pick<OutlineNode, "title" | "targetWords" | "description">>,
  ) => void;
  onDelete: (id: string) => void;
  onAddSibling: (afterId: string | null) => void;
  onAddChild: (parentId: string) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onReset?: () => void;
};

function countLevel1(nodes: OutlineNode[]): number {
  return nodes.filter((n) => n.level === 1).length;
}

/** 用途：由真实 generating/progress/outline 推导过程说明，不冒充任务事件时间戳。 */
function buildProcessLogs(input: {
  generating: boolean;
  progress: number;
  isEmpty: boolean;
  level1Count: number;
}): Array<{ id: string; text: string }> {
  if (input.generating) {
    return [
      {
        id: "gen-1",
        text: "正在根据招标分析生成目录结构…",
      },
      {
        id: "gen-2",
        text: `生成进度 ${Math.max(0, Math.min(100, Math.round(input.progress)))}%`,
      },
    ];
  }
  if (input.isEmpty) {
    return [
      {
        id: "empty-1",
        text: "尚无目录，请点击「AI 生成大纲」或手动添加节点",
      },
    ];
  }
  return [
    {
      id: "ready-1",
      text: `目录已就绪，共 ${input.level1Count} 个一级节点`,
    },
  ];
}

export function OutlineStepWorkspace({
  projectId,
  outline,
  selectedId,
  moveFlags,
  generating = false,
  progress = 0,
  onSelect,
  onPatch,
  onDelete,
  onAddSibling,
  onAddChild,
  onMove,
  onReset,
}: OutlineStepWorkspaceProps) {
  const [expanded, setExpanded] = useState(true);
  const flat = useMemo(() => flattenOutline(outline), [outline]);
  const level1Count = countLevel1(outline);
  const totalWords = countTargetWords(outline);
  const selected = flat.find((n) => n.id === selectedId) ?? null;
  const isEmpty = outline.length === 0;
  const processLogs = buildProcessLogs({
    generating,
    progress,
    isEmpty,
    level1Count,
  });

  function expandAll() {
    setExpanded(true);
  }

  function collapseAll() {
    setExpanded(false);
    onSelect(null);
  }

  return (
    <div className="od-step">
      {/* 顶栏 */}
      <header className="od-step__head">
        <div className="od-step__head-left">
          <span className="badge-step">STEP 03</span>
          <h1 className="od-step__title">目录生成</h1>
        </div>
        <div className="od-step__head-right">
          {generating ? (
            <span className="badge-ai">
              <span className="od-pulse" />
              AI 正在生成目录
            </span>
          ) : (
            <span className="badge badge-primary">
              目录已就绪 · 目标约 {totalWords || "—"} 字
            </span>
          )}
        </div>
      </header>

      {/* 三栏 */}
      <div className="od-step__grid">
        {/* 左：生成过程 */}
        <section className="card od-panel">
          <div className="od-panel__head">
            <h2>生成过程</h2>
          </div>
          <div className="od-progress">
            <div className="od-progress__row">
              <span>生成进度</span>
              <strong>{generating ? progress : isEmpty ? 0 : 100}%</strong>
            </div>
            <div className="od-progress__track">
              <div
                className="od-progress__bar"
                style={{
                  width: `${generating ? progress : isEmpty ? 0 : 100}%`,
                }}
              />
            </div>
          </div>
          <ul className="od-timeline">
            {processLogs.map((log, i) => (
              <li
                key={log.id}
                className={`od-timeline__item${
                  generating && i === processLogs.length - 1 ? " is-live" : ""
                }`}
              >
                <span className="od-timeline__dot" />
                <div className="od-timeline__body">
                  <p>{log.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        {/* 中：目录结构 */}
        <section className="card od-panel od-panel--mid">
          <div className="od-panel__head od-panel__head--row">
            <h2>
              目录结构
              <span className="od-muted"> · {level1Count} 个一级目录</span>
            </h2>
            <div className="od-panel__actions">
              <button type="button" className="btn btn-ghost btn-sm" onClick={expandAll}>
                全部展开
              </button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={collapseAll}>
                全部折叠
              </button>
              <button
                type="button"
                className="btn btn-soft btn-sm"
                onClick={() => onAddSibling(selectedId)}
              >
                <Plus size={14} /> 添加
              </button>
            </div>
          </div>

          {isEmpty ? (
            <div className="od-empty">
              <FolderTree size={40} strokeWidth={1.4} />
              <strong>尚未生成目录</strong>
              <p>先完成招标文件解析，再生成技术方案目录</p>
            </div>
          ) : (
            <div className="od-tree">
              {(expanded ? flat : outline).map((node) => {
                const active = node.id === selectedId;
                const show =
                  expanded || node.level === 1;
                if (!show) return null;
                return (
                  <button
                    key={node.id}
                    type="button"
                    className={`od-tree__node is-l${node.level}${active ? " is-active" : ""}`}
                    onClick={() => onSelect(node.id)}
                  >
                    <span className="od-tree__chev">
                      {node.level < 3 ? (
                        expanded ? (
                          <ChevronDown size={14} />
                        ) : (
                          <ChevronRight size={14} />
                        )
                      ) : null}
                    </span>
                    <span className="od-tree__title">{node.title}</span>
                    <span className="od-tree__meta">
                      L{node.level}
                      {node.targetWords ? ` · ${node.targetWords}字` : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {/* 右：详情 */}
        <section className="card od-panel">
          <div className="od-panel__head">
            <h2>目录项详情</h2>
          </div>
          {!selected ? (
            <div className="od-empty od-empty--sm">
              <strong>未选择</strong>
              <p>在左侧目录树中选择章节后，可查看并编辑标题和描述</p>
            </div>
          ) : (
            <div className="od-detail">
              <div className="field">
                <label htmlFor="od-title">标题</label>
                <input
                  id="od-title"
                  value={selected.title}
                  onChange={(e) =>
                    onPatch(selected.id, { title: e.target.value })
                  }
                />
              </div>
              <div className="field">
                <label htmlFor="od-words">目标字数</label>
                <input
                  id="od-words"
                  type="number"
                  min={0}
                  step={100}
                  value={selected.targetWords ?? ""}
                  placeholder="—"
                  onChange={(e) => {
                    const v = e.target.value;
                    onPatch(selected.id, {
                      targetWords: v === "" ? undefined : Number(v),
                    });
                  }}
                />
              </div>
              <div className="field">
                <label htmlFor="od-desc">描述</label>
                <textarea
                  id="od-desc"
                  rows={5}
                  value={selected.description ?? ""}
                  placeholder="写作侧重点、需响应的评分点…"
                  onChange={(e) =>
                    onPatch(selected.id, { description: e.target.value })
                  }
                />
              </div>
              <div className="od-detail__ops">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!moveFlags.up}
                  onClick={() => onMove(selected.id, "up")}
                >
                  <ChevronUp size={14} /> 上移
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!moveFlags.down}
                  onClick={() => onMove(selected.id, "down")}
                >
                  <ChevronDown size={14} /> 下移
                </button>
                {selected.level < 3 && (
                  <button
                    type="button"
                    className="btn btn-soft btn-sm"
                    onClick={() => onAddChild(selected.id)}
                  >
                    <Plus size={14} /> 子节
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    if (window.confirm(`删除「${selected.title}」及其子节点？`)) {
                      onDelete(selected.id);
                    }
                  }}
                >
                  <Trash2 size={14} /> 删除
                </button>
              </div>
            </div>
          )}
        </section>
      </div>

      {/* 底栏：仅在父级提供 onReset 时显示重置；本包不新增示例目录恢复 */}
      <footer className="od-step__foot card">
        {onReset ? (
          <button
            type="button"
            className="btn btn-danger-text"
            onClick={() => onReset()}
          >
            重置
          </button>
        ) : null}
        <div className="od-step__foot-spacer" />
        <Link to="/projects" className="btn btn-ghost">
          首页
        </Link>
        <Link
          to={`/technical-plan/${projectId}/analysis`}
          className="btn btn-ghost"
        >
          上一步
        </Link>
        <Link
          to={`/technical-plan/${projectId}/facts`}
          className="btn btn-primary"
        >
          下一步
        </Link>
      </footer>
    </div>
  );
}
