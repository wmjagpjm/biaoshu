"""
模块：项目编辑器状态服务
用途：读写技术标大纲/章节/事实/分析概述/guidance 的整包 JSON。
对接：GET|PUT /api/projects/{id}/editor-state
二次开发：字段级 PATCH、版本号、冲突检测可在此扩展。
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


def get_editor_state(db: Session, workspace_id: str, project_id: str) -> dict:
    """
    用途：返回编辑器状态字典（camelCase 键，便于前端直接用）。
    无行时返回空结构（非 404），项目不存在仍抛 ProjectNotFoundError。
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
            "guidance": None,
            "parsedMarkdown": None,
            "updatedAt": None,
        }
    return {
        "projectId": project_id,
        "outline": _loads(row.outline_json),
        "chapters": _loads(row.chapters_json),
        "facts": _loads(row.facts_json),
        "mode": row.mode or "ALIGNED",
        "analysisOverview": row.analysis_overview,
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
    guidance: Any = ...,
    parsed_markdown: str | None = ...,
) -> dict:
    """
    用途：部分更新编辑器状态；未传的字段（Ellipsis）保持原值。
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
    if analysis_overview is not ...:
        row.analysis_overview = analysis_overview
    if guidance is not ...:
        row.guidance_json = _dumps(guidance)
    if parsed_markdown is not ...:
        row.parsed_markdown = parsed_markdown

    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return get_editor_state(db, workspace_id, project_id)
