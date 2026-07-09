"""
模块：项目编辑器状态服务
用途：读写大纲/章节/事实/结构化分析/guidance/解析文。
对接：GET|PUT /api/projects/{id}/editor-state
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ProjectEditorStateRow
from app.services.project_service import get_project


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


def get_editor_state(db: Session, workspace_id: str, project_id: str) -> dict:
    """
    用途：返回编辑器状态字典（camelCase）。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None:
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
            "updatedAt": None,
        }
    analysis = normalize_analysis(
        _loads(row.analysis_json),
        fallback_overview=row.analysis_overview or "",
    )
    # 若 JSON 空但有 overview 字段，回填
    if not analysis.get("overview") and row.analysis_overview:
        analysis["overview"] = row.analysis_overview
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
) -> dict:
    """
    用途：部分更新；analysis 与 analysis_overview 双写。
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

    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return get_editor_state(db, workspace_id, project_id)
