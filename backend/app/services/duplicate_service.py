"""
模块：标书查重服务
用途：对比项目正文段落与知识库 / 本文内部 / 同 workspace 历史章节。
对接：
  - POST /api/projects/{id}/duplicate-check
  - editor_state_service、knowledge_service、text_similarity
二次开发：可换向量相似度；改写建议可接 LLM。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Project, ProjectEditorStateRow
from app.services import editor_state_service, knowledge_service
from app.services.project_service import get_project
from app.services.text_similarity import similarity, split_paragraphs, top_tokens

Scope = Literal["kb+history", "kb", "self"]

_MAX_SELF_PARAS = 80
_MAX_CANDIDATES_PER_PARA = 12
_MAX_HITS = 80


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_self_paragraphs(state: dict) -> list[dict[str, str]]:
    """用途：从 editor-state 抽 {chapter, chapterId, text}。"""
    items: list[dict[str, str]] = []
    chapters = state.get("chapters")
    if isinstance(chapters, list):
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            cid = str(ch.get("id") or "")
            title = str(ch.get("title") or "未命名章节")
            body = str(ch.get("body") or "")
            for para in split_paragraphs(body, min_len=40):
                items.append(
                    {
                        "chapter": title,
                        "chapterId": cid,
                        "text": para,
                    }
                )
    if not items:
        md = str(state.get("parsedMarkdown") or "")
        for para in split_paragraphs(md, min_len=40):
            items.append({"chapter": "解析文本", "chapterId": "", "text": para})
    return items[:_MAX_SELF_PARAS]


def _history_paragraphs(
    db: Session, workspace_id: str, exclude_project_id: str
) -> list[dict[str, str]]:
    """用途：同 workspace 其它技术标项目的章节段落。"""
    stmt = select(Project).where(
        Project.workspace_id == workspace_id,
        Project.id != exclude_project_id,
    )
    projects = list(db.scalars(stmt).all())
    out: list[dict[str, str]] = []
    for p in projects:
        kind = getattr(p, "kind", None) or "technical"
        if kind not in ("technical", "business"):
            continue
        st = editor_state_service.get_editor_state(db, workspace_id, p.id)
        chapters = st.get("chapters")
        if not isinstance(chapters, list):
            continue
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            title = str(ch.get("title") or "章节")
            body = str(ch.get("body") or "")
            for para in split_paragraphs(body, min_len=50)[:20]:
                out.append(
                    {
                        "label": f"历史项目 · {p.name} · {title}",
                        "text": para,
                    }
                )
        if len(out) > 400:
            break
    return out[:400]


def _kb_candidates(
    db: Session, workspace_id: str, para: str
) -> list[dict[str, Any]]:
    """用途：用关键词预筛知识库块，再精算相似度。"""
    tokens = top_tokens(para, limit=6)
    query = " ".join(tokens) if tokens else para[:40]
    if not query.strip():
        return []
    return knowledge_service.search_chunks(
        db, workspace_id, query, top_k=_MAX_CANDIDATES_PER_PARA
    )


def run_duplicate_check(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    scope: str = "kb+history",
    threshold: float = 0.6,
    top_k: int = 50,
) -> dict[str, Any]:
    """
    用途：执行查重，返回 hits 列表（camelCase）。
    """
    get_project(db, workspace_id, project_id)
    thr = max(0.3, min(0.99, float(threshold or 0.6)))
    top_k = max(1, min(int(top_k or 50), _MAX_HITS))
    scope_n = scope if scope in ("kb+history", "kb", "self") else "kb+history"

    state = editor_state_service.get_editor_state(db, workspace_id, project_id)
    self_paras = _extract_self_paragraphs(state)
    hits: list[dict[str, Any]] = []
    compared = 0
    sources = 0

    do_kb = scope_n in ("kb", "kb+history")
    do_hist = scope_n == "kb+history"
    do_self = scope_n == "self"

    hist_pool: list[dict[str, str]] = []
    if do_hist:
        hist_pool = _history_paragraphs(db, workspace_id, project_id)
        sources += len(hist_pool)

    if do_self:
        n = len(self_paras)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = self_paras[i], self_paras[j]
                if a["chapterId"] and a["chapterId"] == b["chapterId"]:
                    # 同章内相邻略过部分噪声：仍比
                    pass
                compared += 1
                sim = similarity(a["text"], b["text"])
                if sim >= thr:
                    hits.append(
                        {
                            "id": f"dup_{secrets.token_hex(4)}",
                            "chapter": a["chapter"],
                            "chapterId": a.get("chapterId") or None,
                            "similarity": sim,
                            "currentText": a["text"][:800],
                            "sourceText": b["text"][:800],
                            "sourceLabel": f"本文内部 · {b['chapter']}",
                            "suggestion": (
                                f"本文「{a['chapter']}」与「{b['chapter']}」重合约 "
                                f"{int(sim * 100)}%。建议改写语序与表述，避免段落复读。"
                            ),
                        }
                    )
    else:
        for para in self_paras:
            if do_kb:
                cands = _kb_candidates(db, workspace_id, para["text"])
                sources += len(cands)
                for c in cands:
                    body = str(c.get("content") or "")
                    compared += 1
                    sim = similarity(para["text"], body)
                    if sim >= thr:
                        doc = c.get("docName") or "知识库文档"
                        title = c.get("title") or ""
                        label = f"知识库 · {doc}" + (f" · {title}" if title else "")
                        hits.append(
                            {
                                "id": f"dup_{secrets.token_hex(4)}",
                                "chapter": para["chapter"],
                                "chapterId": para.get("chapterId") or None,
                                "similarity": sim,
                                "currentText": para["text"][:800],
                                "sourceText": body[:800],
                                "sourceLabel": label,
                                "suggestion": (
                                    f"与《{doc}》重合约 {int(sim * 100)}%。"
                                    "请改写语序/同义表述，保留招标要求的技术事实，勿整段照抄。"
                                ),
                            }
                        )
            if do_hist and hist_pool:
                # 历史：对每段取前若干条粗比（限制计算量）
                sample = hist_pool[:80]
                for h in sample:
                    compared += 1
                    sim = similarity(para["text"], h["text"])
                    if sim >= thr:
                        hits.append(
                            {
                                "id": f"dup_{secrets.token_hex(4)}",
                                "chapter": para["chapter"],
                                "chapterId": para.get("chapterId") or None,
                                "similarity": sim,
                                "currentText": para["text"][:800],
                                "sourceText": h["text"][:800],
                                "sourceLabel": h["label"],
                                "suggestion": (
                                    f"与历史稿重合约 {int(sim * 100)}%。"
                                    "建议差异化表述，避免多项目雷同。"
                                ),
                            }
                        )

    hits.sort(key=lambda x: -float(x.get("similarity") or 0))
    # 去重：同一 current+source 文本前缀
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for h in hits:
        key = (h.get("currentText") or "")[:80] + "|" + (h.get("sourceText") or "")[:80]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
        if len(uniq) >= top_k:
            break

    return {
        "projectId": project_id,
        "scope": scope_n,
        "threshold": thr,
        "hits": uniq,
        "ranAt": _now_iso(),
        "stats": {
            "selfParagraphs": len(self_paras),
            "compared": compared,
            "sourceUnits": sources,
            "hitCount": len(uniq),
        },
    }
