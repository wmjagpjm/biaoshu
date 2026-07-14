/**
 * 模块：标书查重页
 * 用途：命中列表 + 本文/来源对照 + 改写建议。
 * 对接：POST /api/projects/{id}/duplicate-check；listProjectsAsync kind=technical
 * 二次开发：改写建议可接 LLM；历史范围已由后端 kb+history 覆盖。
 *       项目选择器失败时选项为空并固定中文提示，不读演示项目。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FileSearch, Loader2, Sparkles } from "lucide-react";
import { EmptyState } from "../../../shared/components/EmptyState/EmptyState";
import { LoadingBlock } from "../../../shared/components/LoadingBlock/LoadingBlock";
import { apiFetch } from "../../../shared/lib/api";
import type { Project } from "../../../shared/types/workspace";
import { listProjectsAsync } from "../../technical-plan/lib/projectStore";
import type { DupCompareScope, DupHit } from "../types";
import "./DuplicateCheck.css";

type Threshold = 0.6 | 0.7 | 0.8;

type DupCheckResult = {
  projectId: string;
  hits: DupHit[];
  ranAt?: string;
  stats?: { hitCount?: number; selfParagraphs?: number };
};

export function DuplicateCheckPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [scope, setScope] = useState<DupCompareScope>("kb+history");
  const [threshold, setThreshold] = useState<Threshold>(0.6);
  const [hits, setHits] = useState<DupHit[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [running, setRunning] = useState(false);
  const [ranOnce, setRanOnce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadHint, setLoadHint] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await listProjectsAsync({ kind: "technical" });
        setProjects(res.projects);
        setLoadHint(null);
        if (res.projects[0]) setProjectId(res.projects[0].id);
        else setProjectId("");
      } catch {
        setProjects([]);
        setProjectId("");
        setLoadHint("项目列表加载失败，请稍后重试");
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    return hits.filter((h) => h.similarity >= threshold);
  }, [hits, threshold]);

  const selected =
    filtered.find((h) => h.id === selectedId) ?? filtered[0] ?? null;

  const runCheck = useCallback(async () => {
    if (!projectId) {
      setError("请先选择项目");
      return;
    }
    setRunning(true);
    setError(null);
    setRanOnce(true);
    try {
      const data = await apiFetch<DupCheckResult>(
        `/projects/${encodeURIComponent(projectId)}/duplicate-check`,
        {
          method: "POST",
          body: JSON.stringify({
            scope,
            threshold,
            topK: 50,
          }),
        },
      );
      const list = Array.isArray(data.hits) ? data.hits : [];
      setHits(list);
      setSelectedId(list[0]?.id ?? "");
    } catch (err) {
      setHits([]);
      setSelectedId("");
      setError((err as { message?: string })?.message || "查重失败");
    } finally {
      setRunning(false);
    }
  }, [projectId, scope, threshold]);

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
          <Link to="/knowledge-base" className="btn btn-ghost">
            知识库
          </Link>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void runCheck()}
            disabled={running || !projectId}
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

      {error && (
        <div className="card card-pad" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}
      {loadHint && (
        <div
          className="card card-pad"
          style={{ fontSize: 13, color: "var(--text-tertiary)" }}
        >
          {loadHint}
        </div>
      )}

      <div className="card card-pad dup-filters">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="dup-project">目标项目</label>
          <select
            id="dup-project"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          >
            {projects.length === 0 ? (
              <option value="">暂无技术标项目</option>
            ) : (
              projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))
            )}
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
          description="选择项目与范围后点击「开始查重」。请确保项目已有章节正文，知识库已入库。"
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
                <div className="dup-compare">
                  <div>
                    <div className="dup-compare__label">本文段落</div>
                    <div className="dup-compare__body">{selected.currentText}</div>
                  </div>
                  <div>
                    <div className="dup-compare__label">对比来源</div>
                    <div className="dup-compare__body">{selected.sourceText}</div>
                  </div>
                </div>
                {selected.suggestion && (
                  <div className="dup-suggest">
                    <Sparkles size={16} />
                    <div>
                      <strong>改写建议</strong>
                      <p style={{ margin: "6px 0 0" }}>{selected.suggestion}</p>
                    </div>
                  </div>
                )}
                {projectId && (
                  <div style={{ marginTop: 12 }}>
                    <Link
                      to={`/technical-plan/${projectId}`}
                      className="btn btn-soft btn-sm"
                    >
                      打开技术标工作区
                    </Link>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
