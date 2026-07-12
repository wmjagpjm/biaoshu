"""
模块：技术标中标内容模板服务
用途：从 technical 项目深拷贝大纲/章节快照、workspace 隔离检索，以及从模板创建全新项目草稿。
对接：app.api.templates；project_service；editor_state_service；entities.BidTemplateRow。
二次开发：
  - 禁止覆盖已有项目 editor-state；创建只走「新项目 + 独立副本」；
  - 勿与导出版式模板混用；多模板融合、商务模板、跨 workspace 共享另立项。
"""

from __future__ import annotations

import copy
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import BidTemplateRow, Project, ProjectEditorStateRow
from app.services.project_service import (
    ProjectNotFoundError,
    create_project,
    get_project,
)

# 模板状态
ALLOWED_TEMPLATE_STATUS = frozenset({"active", "archived"})
# 阶段 1 仅允许沉淀/复用 technical
TEMPLATE_KIND = "technical"
# snapshot JSON 字符上限，防止 SQLite 被超大正文撑爆
MAX_SNAPSHOT_CHARS = 1_500_000


class TemplateNotFoundError(Exception):
    """
    用途：模板不存在或不属于当前 workspace 时中断服务（路由统一 404）。
    对接：app.api.templates。
    """


class TemplateValidationError(ValueError):
    """
    用途：空大纲、非 technical、超大快照等业务校验失败（路由 400）。
    对接：create_from_project / create_project_from_template。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_template_id() -> str:
    """用途：生成模板主键 tpl_{16hex}。"""
    return f"tpl_{secrets.token_hex(8)}"


def _clean_title(value: Any, default: str = "未命名模板") -> str:
    text = str(value or "").strip()[:500]
    return text or default


def _clean_tags(value: Any) -> list[str]:
    """用途：归一化标签列表，去空去重，最多 20 个。"""
    if value is None:
        return []
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [part for part in value.replace("，", ",").split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        tag = str(item or "").strip()[:40]
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= 20:
            break
    return tags


def _dumps_tags(tags: list[str]) -> str | None:
    if not tags:
        return None
    return json.dumps(tags, ensure_ascii=False)


def _loads_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _clean_tags(data)


def _deep_copy_jsonable(value: Any) -> Any:
    """用途：深拷贝可 JSON 化结构，切断与源 editor-state 的引用。"""
    return copy.deepcopy(value)


def _loads_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _build_snapshot_from_editor_row(row: ProjectEditorStateRow | None) -> dict[str, Any]:
    """
    用途：从项目 editor-state 行组装模板快照。
    规则：至少 outline + chapters；可附带 mode/facts/guidance 作为写作上下文。
    """
    if row is None:
        raise TemplateValidationError("项目尚无大纲与章节，无法沉淀为模板")
    outline = _loads_json(row.outline_json)
    if not isinstance(outline, list) or len(outline) == 0:
        raise TemplateValidationError("大纲为空，无法沉淀为模板")
    chapters = _loads_json(row.chapters_json)
    if chapters is None:
        chapters = []
    if not isinstance(chapters, list):
        raise TemplateValidationError("章节数据非法，无法沉淀为模板")
    snapshot: dict[str, Any] = {
        "outline": _deep_copy_jsonable(outline),
        "chapters": _deep_copy_jsonable(chapters),
    }
    mode = (row.mode or "ALIGNED").strip()
    if mode in ("ALIGNED", "FREE"):
        snapshot["mode"] = mode
    facts = _loads_json(row.facts_json)
    if facts is not None:
        snapshot["facts"] = _deep_copy_jsonable(facts)
    guidance = _loads_json(row.guidance_json)
    if guidance is not None:
        snapshot["guidance"] = _deep_copy_jsonable(guidance)
    return snapshot


def _serialize_snapshot(snapshot: dict[str, Any]) -> str:
    """用途：序列化快照并强制体积上限。"""
    raw = json.dumps(snapshot, ensure_ascii=False)
    if len(raw) > MAX_SNAPSHOT_CHARS:
        raise TemplateValidationError(
            f"模板快照过大（{len(raw)} 字符，上限 {MAX_SNAPSHOT_CHARS}），请精简大纲或章节后再沉淀"
        )
    return raw


def _snapshot_summary(snapshot: dict[str, Any]) -> tuple[int, list[str]]:
    """
    用途：从快照提取列表用轻量摘要（章节数 + 顶层大纲标题，最多 8 个）。
    对接：template_to_summary_data。
    """
    chapters = snapshot.get("chapters")
    chapter_count = len(chapters) if isinstance(chapters, list) else 0
    outline = snapshot.get("outline")
    titles: list[str] = []
    if isinstance(outline, list):
        for node in outline:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "").strip()
            if title:
                titles.append(title[:200])
            if len(titles) >= 8:
                break
    return chapter_count, titles


def template_to_summary_data(row: BidTemplateRow) -> dict[str, Any]:
    """
    用途：ORM → 列表摘要读模型（不含完整 snapshot，避免列表拖垮带宽）。
    对接：GET /api/templates。
    """
    snapshot = _loads_json(row.snapshot_json)
    if not isinstance(snapshot, dict):
        snapshot = {"outline": [], "chapters": []}
    chapter_count, outline_titles = _snapshot_summary(snapshot)
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "tags": _loads_tags(row.tags_json),
        "status": row.status,
        "kind": row.kind,
        "source_project_id": row.source_project_id,
        "source_project_name": row.source_project_name or "",
        "chapter_count": chapter_count,
        "outline_titles": outline_titles,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def template_to_data(row: BidTemplateRow) -> dict[str, Any]:
    """
    用途：ORM → 详情读模型（含完整 snapshot；snake_case 键，由 Schema 输出 camelCase）。
    对接：GET /api/templates/{id}；POST from-project。
    """
    snapshot = _loads_json(row.snapshot_json)
    if not isinstance(snapshot, dict):
        snapshot = {"outline": [], "chapters": []}
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "tags": _loads_tags(row.tags_json),
        "status": row.status,
        "kind": row.kind,
        "source_project_id": row.source_project_id,
        "source_project_name": row.source_project_name or "",
        "snapshot": snapshot,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_template(db: Session, workspace_id: str, template_id: str) -> BidTemplateRow:
    """
    用途：按 id 读取并校验 workspace 归属。
    异常：TemplateNotFoundError → 路由 404。
    """
    row = db.get(BidTemplateRow, template_id)
    if row is None or row.workspace_id != workspace_id:
        raise TemplateNotFoundError(template_id)
    return row


def list_templates(
    db: Session,
    workspace_id: str,
    *,
    q: str | None = None,
    status: str | None = None,
) -> list[BidTemplateRow]:
    """
    用途：当前 workspace 模板列表，按 updated_at 倒序；可选标题/标签关键词与状态过滤。
    对接：GET /api/templates。
    """
    stmt = select(BidTemplateRow).where(BidTemplateRow.workspace_id == workspace_id)
    if status and status in ALLOWED_TEMPLATE_STATUS:
        stmt = stmt.where(BidTemplateRow.status == status)
    stmt = stmt.order_by(BidTemplateRow.updated_at.desc())
    rows = list(db.scalars(stmt).all())
    needle = (q or "").strip().casefold()
    if not needle:
        return rows
    filtered: list[BidTemplateRow] = []
    for row in rows:
        hay = " ".join(
            [
                row.title or "",
                row.source_project_name or "",
                " ".join(_loads_tags(row.tags_json)),
            ]
        ).casefold()
        if needle in hay:
            filtered.append(row)
    return filtered


def create_template_from_project(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
) -> BidTemplateRow:
    """
    用途：从当前 workspace 的 technical 项目沉淀独立快照模板。
    规则：非 technical / 空大纲 / 超大快照 → TemplateValidationError；跨 workspace 项目 → 404。
    对接：POST /api/templates/from-project。
    """
    project = get_project(db, workspace_id, project_id)
    if (project.kind or "technical") != "technical":
        raise TemplateValidationError("仅支持从技术标项目沉淀内容模板")
    editor = db.get(ProjectEditorStateRow, project.id)
    snapshot = _build_snapshot_from_editor_row(editor)
    snapshot_raw = _serialize_snapshot(snapshot)
    cleaned_tags = _clean_tags(tags)
    row = BidTemplateRow(
        id=_new_template_id(),
        workspace_id=workspace_id,
        title=_clean_title(title, default=f"{project.name} · 模板"),
        tags_json=_dumps_tags(cleaned_tags),
        status="active",
        kind=TEMPLATE_KIND,
        source_project_id=project.id,
        source_project_name=project.name or "",
        snapshot_json=snapshot_raw,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_template(db: Session, workspace_id: str, template_id: str) -> None:
    """
    用途：删除模板；不影响任何项目与 editor-state。
    对接：DELETE /api/templates/{id}。
    """
    row = get_template(db, workspace_id, template_id)
    db.delete(row)
    db.commit()


def create_project_from_template(
    db: Session,
    workspace_id: str,
    template_id: str,
    *,
    name: str | None = None,
    industry: str | None = None,
) -> Project:
    """
    用途：从模板快照创建**全新技术标项目草稿**，写入独立 editor-state 副本；绝不覆盖已有项目。
    对接：POST /api/templates/{id}/projects。
    二次开发：融合写入既有项目属于阶段 3，禁止在此扩展。
    """
    row = get_template(db, workspace_id, template_id)
    if row.kind != TEMPLATE_KIND:
        raise TemplateValidationError("当前仅支持从技术标内容模板创建项目")
    snapshot = _loads_json(row.snapshot_json)
    if not isinstance(snapshot, dict):
        raise TemplateValidationError("模板快照损坏，无法创建项目")
    outline = snapshot.get("outline")
    if not isinstance(outline, list) or len(outline) == 0:
        raise TemplateValidationError("模板大纲为空，无法创建项目")
    chapters = snapshot.get("chapters")
    if not isinstance(chapters, list):
        chapters = []
    mode = str(snapshot.get("mode") or "ALIGNED")
    if mode not in ("ALIGNED", "FREE"):
        mode = "ALIGNED"
    facts = snapshot.get("facts")
    guidance = snapshot.get("guidance")
    project_name = _clean_title(name, default=f"{row.title} · 副本")
    cleaned_industry = str(industry or "通用").strip()[:100] or "通用"

    try:
        project = create_project(
            db,
            workspace_id,
            name=project_name,
            industry=cleaned_industry,
            kind=TEMPLATE_KIND,
            status="draft",
            technical_plan_step=3,
            commit=False,
        )
        editor = ProjectEditorStateRow(
            project_id=project.id,
            outline_json=json.dumps(_deep_copy_jsonable(outline), ensure_ascii=False),
            chapters_json=json.dumps(_deep_copy_jsonable(chapters), ensure_ascii=False),
            facts_json=(
                json.dumps(_deep_copy_jsonable(facts), ensure_ascii=False)
                if facts is not None
                else None
            ),
            guidance_json=(
                json.dumps(_deep_copy_jsonable(guidance), ensure_ascii=False)
                if guidance is not None
                else None
            ),
            mode=mode,
            updated_at=_now(),
        )
        db.add(editor)
        db.commit()
        db.refresh(project)
        return project
    except Exception:
        db.rollback()
        raise
