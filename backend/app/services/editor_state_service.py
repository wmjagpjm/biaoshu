"""
模块：项目编辑器状态服务
用途：读写大纲/章节/事实/结构化分析/guidance/解析文/商务标字段。
对接：GET|PUT /api/projects/{id}/editor-state
二次开发：商务字段整包存 business_json，API 拆成 businessQualify 等 camelCase。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ProjectEditorStateRow
from app.services.project_service import get_project

# business_json 内键名（snake）
_BIZ_KEYS = ("qualify", "toc", "quote", "commit")


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def empty_analysis() -> dict:
    """用途：空结构化分析。"""
    return {
        "overview": "",
        "techRequirements": [],
        "rejectionRisks": [],
        "scoringPoints": [],
    }


def empty_business() -> dict:
    """
    用途：空商务标工作区包。
    对接：businessQualify / businessToc / businessQuote / businessCommit
    """
    return {
        "qualify": [],
        "toc": [],
        "quote": {"rows": [], "notes": ""},
        "commit": [],
    }


def normalize_business(raw: Any) -> dict:
    """用途：规范 business 对象，兼容缺字段。"""
    base = empty_business()
    if not isinstance(raw, dict):
        return base
    q = raw.get("qualify")
    if isinstance(q, list):
        base["qualify"] = q
    t = raw.get("toc")
    if isinstance(t, list):
        base["toc"] = t
    quote = raw.get("quote")
    if isinstance(quote, dict):
        rows = quote.get("rows")
        base["quote"] = {
            "rows": rows if isinstance(rows, list) else [],
            "notes": str(quote.get("notes") or ""),
        }
    c = raw.get("commit")
    if isinstance(c, list):
        base["commit"] = c
    return base


def normalize_analysis(raw: Any, fallback_overview: str = "") -> dict:
    """用途：规范 analysis 对象，兼容缺字段。"""
    base = empty_analysis()
    if isinstance(raw, dict):
        base["overview"] = str(raw.get("overview") or fallback_overview or "")
        tr = raw.get("techRequirements") or raw.get("tech_requirements") or []
        rr = raw.get("rejectionRisks") or raw.get("rejection_risks") or []
        sp = raw.get("scoringPoints") or raw.get("scoring_points") or []
        if isinstance(tr, list):
            base["techRequirements"] = [str(x) for x in tr if str(x).strip()]
        if isinstance(rr, list):
            base["rejectionRisks"] = [str(x) for x in rr if str(x).strip()]
        if isinstance(sp, list):
            points = []
            for p in sp:
                if isinstance(p, dict):
                    points.append(
                        {
                            "name": str(p.get("name") or ""),
                            "weight": str(p.get("weight") or ""),
                        }
                    )
                elif p:
                    points.append({"name": str(p), "weight": ""})
            base["scoringPoints"] = points
    elif fallback_overview:
        base["overview"] = fallback_overview
    return base


def _read_business_blob(row: ProjectEditorStateRow | None) -> dict:
    if row is None:
        return empty_business()
    return normalize_business(_loads(getattr(row, "business_json", None)))


def get_editor_state(db: Session, workspace_id: str, project_id: str) -> dict:
    """
    用途：返回编辑器状态字典（camelCase）。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None:
        biz = empty_business()
        return {
            "projectId": project_id,
            "outline": None,
            "chapters": None,
            "facts": None,
            "mode": "ALIGNED",
            "analysisOverview": None,
            "analysis": empty_analysis(),
            "guidance": None,
            "parsedMarkdown": None,
            "businessQualify": biz["qualify"],
            "businessToc": biz["toc"],
            "businessQuote": biz["quote"],
            "businessCommit": biz["commit"],
            "updatedAt": None,
        }
    analysis = normalize_analysis(
        _loads(row.analysis_json),
        fallback_overview=row.analysis_overview or "",
    )
    # 若 JSON 空但有 overview 字段，回填
    if not analysis.get("overview") and row.analysis_overview:
        analysis["overview"] = row.analysis_overview
    biz = _read_business_blob(row)
    return {
        "projectId": project_id,
        "outline": _loads(row.outline_json),
        "chapters": _loads(row.chapters_json),
        "facts": _loads(row.facts_json),
        "mode": row.mode or "ALIGNED",
        "analysisOverview": analysis.get("overview") or row.analysis_overview,
        "analysis": analysis,
        "guidance": _loads(row.guidance_json),
        "parsedMarkdown": row.parsed_markdown,
        "businessQualify": biz["qualify"],
        "businessToc": biz["toc"],
        "businessQuote": biz["quote"],
        "businessCommit": biz["commit"],
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def upsert_editor_state(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    outline: Any = ...,
    chapters: Any = ...,
    facts: Any = ...,
    mode: str | None = None,
    analysis_overview: str | None = ...,
    analysis: Any = ...,
    guidance: Any = ...,
    parsed_markdown: str | None = ...,
    business_qualify: Any = ...,
    business_toc: Any = ...,
    business_quote: Any = ...,
    business_commit: Any = ...,
) -> dict:
    """
    用途：部分更新；analysis 与 analysis_overview 双写；商务字段合并进 business_json。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None:
        row = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
        db.add(row)

    if outline is not ...:
        row.outline_json = _dumps(outline)
    if chapters is not ...:
        row.chapters_json = _dumps(chapters)
    if facts is not ...:
        row.facts_json = _dumps(facts)
    if mode is not None:
        row.mode = mode if mode in ("ALIGNED", "FREE") else "ALIGNED"
    if analysis is not ...:
        norm = normalize_analysis(analysis)
        row.analysis_json = _dumps(norm)
        row.analysis_overview = norm.get("overview") or ""
    elif analysis_overview is not ...:
        row.analysis_overview = analysis_overview
        # 合并进 analysis_json
        prev = normalize_analysis(_loads(row.analysis_json), analysis_overview or "")
        prev["overview"] = analysis_overview or ""
        row.analysis_json = _dumps(prev)
    if guidance is not ...:
        row.guidance_json = _dumps(guidance)
    if parsed_markdown is not ...:
        row.parsed_markdown = parsed_markdown

    biz_touched = any(
        x is not ...
        for x in (business_qualify, business_toc, business_quote, business_commit)
    )
    if biz_touched:
        biz = _read_business_blob(row)
        if business_qualify is not ...:
            biz["qualify"] = business_qualify if isinstance(business_qualify, list) else []
        if business_toc is not ...:
            biz["toc"] = business_toc if isinstance(business_toc, list) else []
        if business_quote is not ...:
            if isinstance(business_quote, dict):
                rows = business_quote.get("rows")
                biz["quote"] = {
                    "rows": rows if isinstance(rows, list) else [],
                    "notes": str(business_quote.get("notes") or ""),
                }
            else:
                biz["quote"] = {"rows": [], "notes": ""}
        if business_commit is not ...:
            biz["commit"] = business_commit if isinstance(business_commit, list) else []
        row.business_json = _dumps(normalize_business(biz))

    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return get_editor_state(db, workspace_id, project_id)
