import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileSearch, Loader2, Sparkles } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { mockDupHits } from "../mock";
import type { DupCompareScope, DupHit } from "../types";
import "./DuplicateCheck.css";

/**
 * 模块：标书查重页
 * 用途：命中列表 + 本文/来源对照 + 改写建议（mock）。
 * 对接：后端查重任务就绪后替换「开始查重」数据源。
 */

type Threshold = 0.6 | 0.7 | 0.8;

export function DuplicateCheckPage() {
  const [projectId, setProjectId] = useState("proj_01");
  const [scope, setScope] = useState<DupCompareScope>("kb+history");
  const [threshold, setThreshold] = useState<Threshold>(0.6);
  const [hits, setHits] = useState<DupHit[]>(mockDupHits);
  const [selectedId, setSelectedId] = useState<string>(mockDupHits[0]?.id ?? "");
  const [running, setRunning] = useState(false);
  const [ranOnce, setRanOnce] = useState(true);

  const filtered = useMemo(() => {
    let list = hits.filter((h) => h.similarity >= threshold);
    if (scope === "self") {
      list = list.filter((h) => h.sourceLabel.includes("本文内部"));
    } else if (scope === "kb") {
      list = list.filter((h) => h.sourceLabel.includes("知识库"));
    }
    return list;
  }, [hits, threshold, scope]);

  const selected =
    filtered.find((h) => h.id === selectedId) ?? filtered[0] ?? null;

  function runCheck() {
    setRunning(true);
    setRanOnce(true);
    window.setTimeout(() => {
      // 演示：轻微打乱顺序模拟新结果
      setHits([...mockDupHits].sort((a, b) => b.similarity - a.similarity));
      setSelectedId(mockDupHits[0]?.id ?? "");
      setRunning(false);
    }, 700);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>标书查重</h1>
          <p>
            对比技术标正文与知识库、历史项目或本文内部，左右对照重复段落并给出改写建议。
          </p>
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={runCheck}
            disabled={running}
          >
            {running ? (
              <>
                <Loader2 size={16} /> 查重中…
              </>
            ) : (
              <>
                <FileSearch size={16} /> 开始查重
              </>
            )}
          </button>
        </div>
      </header>

      <div className="card card-pad dup-filters">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="dup-project">目标项目</label>
          <select
            id="dup-project"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          >
            <option value="proj_01">某市智慧交通综合管理平台技术标</option>
            <option value="proj_03">医院信息集成平台改造</option>
          </select>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="dup-scope">对比范围</label>
          <select
            id="dup-scope"
            value={scope}
            onChange={(e) => setScope(e.target.value as DupCompareScope)}
          >
            <option value="kb+history">知识库 + 历史项目</option>
            <option value="kb">仅知识库</option>
            <option value="self">仅本文内部重复</option>
          </select>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="dup-th">相似度阈值</label>
          <select
            id="dup-th"
            value={String(threshold)}
            onChange={(e) => setThreshold(Number(e.target.value) as Threshold)}
          >
            <option value="0.6">≥ 60%</option>
            <option value="0.7">≥ 70%</option>
            <option value="0.8">≥ 80%</option>
          </select>
        </div>
      </div>

      {running ? (
        <LoadingBlock label="正在比对正文与知识库/历史稿…" />
      ) : !ranOnce ? (
        <EmptyState
          icon={<FileSearch size={28} />}
          title="尚未运行查重"
          description="选择项目与范围后点击「开始查重」。"
        />
      ) : (
        <div className="dup-layout">
          <div className="dup-list" aria-label="查重命中列表">
            {filtered.length === 0 ? (
              <EmptyState
                title="当前阈值下无命中"
                description="可降低相似度阈值，或更换对比范围后重试。"
              />
            ) : (
              filtered.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className={`dup-hit${selected?.id === h.id ? " is-active" : ""}`}
                  onClick={() => setSelectedId(h.id)}
                >
                  <div className="dup-hit__top">
                    <strong>{h.chapter}</strong>
                    <span className="badge badge-primary">
                      {(h.similarity * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="dup-hit__snippet">{h.currentText}</p>
                  <div className="dup-hit__src">对比来源：{h.sourceLabel}</div>
                </button>
              ))
            )}
          </div>

          <div className="dup-panel" aria-label="对照详情">
            {!selected ? (
              <div className="dup-empty">请选择左侧命中项查看对照</div>
            ) : (
              <>
                <div className="dup-hit__top">
                  <div>
                    <strong style={{ fontSize: 16 }}>{selected.chapter}</strong>
                    <div className="dup-hit__src" style={{ marginTop: 4 }}>
                      {selected.sourceLabel}
                    </div>
                  </div>
                  <span className="badge badge-primary">
                    相似度 {(selected.similarity * 100).toFixed(0)}%
                  </span>
                </div>

                <div className="dup-panel__cols">
                  <div className="dup-col">
                    <h4>本文摘录</h4>
                    <p>{selected.currentText}</p>
                  </div>
                  <div className="dup-col">
                    <h4>对比来源</h4>
                    <p>{selected.sourceText}</p>
                  </div>
                </div>

                {selected.suggestion && (
                  <div className="dup-suggest">
                    <strong style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <Sparkles size={14} /> 改写建议
                    </strong>
                    <div style={{ marginTop: 6 }}>{selected.suggestion}</div>
                  </div>
                )}

                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  <Link
                    to={`/technical-plan/${projectId}/content`}
                    className="btn btn-soft btn-sm"
                  >
                    打开正文编辑
                  </Link>
                  <span style={{ fontSize: 12, color: "var(--text-tertiary)", alignSelf: "center" }}>
                    当前为示例结果，正式环境由后端计算相似度
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
