"""
模块：卡片化知识与素材库服务
用途：在 workspace 内创建/检索/更新/删除独立知识卡片；文本与图片均保存快照，支持安全插入章节。
对接：app.api.cards；file_service.verify_image_content / save_image_upload 路径约定；
      knowledge_service.get_chunk；entities.KnowledgeCardRow。
二次开发：
  - 禁止污染 kb_documents、resources、project_files、bid_templates；
  - 插入项目图片必须复制登记为 role=image，Markdown 仅 biaoshu-image://file_*；
  - AI 自动注入、多卡片融合、向量排序属阶段 3，勿在此扩展。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import KnowledgeCardRow, ProjectFileRow
from app.services import file_service, knowledge_service
from app.services.file_service import FILE_ROLE_IMAGE
from app.services.knowledge_service import KnowledgeNotFoundError
from app.services.project_service import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

ALLOWED_CARD_TYPES = frozenset(
    {"document", "image", "qualification", "performance"}
)
TEXT_CARD_TYPES = frozenset({"document", "qualification", "performance"})
ALLOWED_CARD_STATUS = frozenset({"active", "archived"})
# 列表筛选：active|archived 过滤单状态；all 返回全部；缺省等价 active
ALLOWED_LIST_STATUS = frozenset({"active", "archived", "all"})
# 文本正文上限（阶段2约定 20,000 字符），防止 SQLite 被超大正文撑爆
MAX_BODY_CHARS = 20_000
MAX_SUMMARY_CHARS = 2_000
MAX_SOURCE_LABEL_CHARS = 500


class CardNotFoundError(Exception):
    """
    用途：卡片不存在或不属于当前 workspace（路由统一 404）。
    对接：app.api.cards。
    """


class CardValidationError(ValueError):
    """
    用途：空标题、超大正文、非法类型、伪造图片等业务校验失败（路由 400）。
    对接：create/update/upload/from-* / insert。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_card_id() -> str:
    """用途：生成卡片主键 card_{16hex}。"""
    return f"card_{secrets.token_hex(8)}"


def _clean_title(value: Any, *, required: bool = True) -> str:
    text = str(value or "").strip()[:500]
    if required and not text:
        raise CardValidationError("标题不能为空")
    return text


def _clean_summary(value: Any) -> str:
    return str(value or "").strip()[:MAX_SUMMARY_CHARS]


def _clean_source_label(value: Any, default: str = "") -> str:
    text = str(value or "").strip()[:MAX_SOURCE_LABEL_CHARS]
    return text or default


def _clean_tags(value: Any) -> list[str]:
    """用途：归一化标签列表，去空去重，最多 20 个。"""
    if value is None:
        return []
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


def _loads_payload(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _dumps_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False)


def _clean_body(value: Any, *, required: bool = False) -> str:
    text = str(value or "")
    if len(text) > MAX_BODY_CHARS:
        raise CardValidationError(
            f"正文过长（{len(text)} 字符），上限 {MAX_BODY_CHARS}"
        )
    if required and not text.strip():
        raise CardValidationError("正文不能为空")
    return text


def _cards_root(settings: Settings) -> Path:
    """用途：workspace 级卡片图片存储根目录（与 kb/project 目录分离）。"""
    base = Path(settings.upload_dir).resolve().parent / "data" / "knowledge_cards"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _card_dir(settings: Settings, workspace_id: str) -> Path:
    directory = _cards_root(settings) / workspace_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_card_path(
    settings: Settings, workspace_id: str, stored_name: str
) -> Path:
    name = Path(stored_name).name
    if not name or name != stored_name:
        raise CardValidationError("非法卡片存储文件名")
    directory = _card_dir(settings, workspace_id).resolve()
    destination = (directory / name).resolve()
    if not destination.is_relative_to(directory):
        raise CardValidationError("卡片文件路径越界")
    return destination


def _summary_preview(text: str, limit: int = 160) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def card_to_summary_data(row: KnowledgeCardRow) -> dict[str, Any]:
    """
    用途：列表轻量摘要（snake_case 键，Schema 输出 camelCase），不含正文全文与图片 base64。
    对接：GET /api/cards。
    """
    summary = (row.summary or "").strip() or _summary_preview(row.body_markdown or "")
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "type": row.type,
        "title": row.title,
        "tags": _loads_tags(row.tags_json),
        "status": row.status,
        "summary": summary,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_label": row.source_label or "",
        "has_body": bool((row.body_markdown or "").strip()),
        "has_image": row.type == "image" and bool(row.stored_name),
        "content_type": row.content_type,
        "size_bytes": row.size_bytes or 0,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def card_to_detail_data(row: KnowledgeCardRow) -> dict[str, Any]:
    """
    用途：详情读模型，含正文快照与图片元数据（不含 base64）。
    对接：GET /api/cards/{id}；创建/更新响应。
    """
    data = card_to_summary_data(row)
    data["body_markdown"] = row.body_markdown or ""
    data["payload"] = _loads_payload(row.payload_json)
    data["stored_name"] = row.stored_name
    return data


def get_card(db: Session, workspace_id: str, card_id: str) -> KnowledgeCardRow:
    """用途：读取卡片并校验 workspace；跨空间或不存在 → CardNotFoundError。"""
    row = db.get(KnowledgeCardRow, card_id)
    if row is None or row.workspace_id != workspace_id:
        raise CardNotFoundError(card_id)
    return row


def list_cards(
    db: Session,
    workspace_id: str,
    *,
    q: str | None = None,
    card_type: str | None = None,
    status: str | None = None,
) -> list[KnowledgeCardRow]:
    """
    用途：当前 workspace 卡片列表，支持关键词与类型/状态筛选。
    对接：GET /api/cards。
    约定：未传 status 时默认仅 active；status=archived 仅归档；
          status=all 返回 active+archived；非法值 400。
    """
    stmt = select(KnowledgeCardRow).where(
        KnowledgeCardRow.workspace_id == workspace_id
    )
    if card_type:
        if card_type not in ALLOWED_CARD_TYPES:
            raise CardValidationError("非法卡片类型")
        stmt = stmt.where(KnowledgeCardRow.type == card_type)
    # 缺省隐藏归档：仅显式 all 才返回全量
    normalized = (status or "active").strip().lower()
    if normalized not in ALLOWED_LIST_STATUS:
        raise CardValidationError("非法卡片状态（允许 active|archived|all）")
    if normalized != "all":
        stmt = stmt.where(KnowledgeCardRow.status == normalized)
    stmt = stmt.order_by(KnowledgeCardRow.updated_at.desc())
    rows = list(db.scalars(stmt).all())
    needle = (q or "").strip().lower()
    if not needle:
        return rows
    filtered: list[KnowledgeCardRow] = []
    for row in rows:
        hay = " ".join(
            [
                row.title or "",
                row.summary or "",
                row.source_label or "",
                " ".join(_loads_tags(row.tags_json)),
                (row.body_markdown or "")[:500],
            ]
        ).lower()
        if needle in hay:
            filtered.append(row)
    return filtered


def create_text_card(
    db: Session,
    workspace_id: str,
    *,
    card_type: str,
    title: str,
    body_markdown: str,
    tags: list[str] | None = None,
    summary: str | None = None,
    source_type: str = "manual",
    source_id: str | None = None,
    source_label: str | None = None,
    payload: dict[str, Any] | None = None,
    status: str = "active",
) -> KnowledgeCardRow:
    """
    用途：手工或从分块创建文本类卡片（document/qualification/performance）。
    对接：POST /api/cards；POST /api/cards/from-chunk。
    """
    if card_type not in TEXT_CARD_TYPES:
        raise CardValidationError("文本卡片类型必须是 document/qualification/performance")
    if status not in ALLOWED_CARD_STATUS:
        raise CardValidationError("非法卡片状态")
    clean_title = _clean_title(title)
    clean_body = _clean_body(body_markdown, required=True)
    clean_tags = _clean_tags(tags)
    clean_summary = _clean_summary(summary) or _summary_preview(clean_body)
    row = KnowledgeCardRow(
        id=_new_card_id(),
        workspace_id=workspace_id,
        type=card_type,
        title=clean_title,
        tags_json=_dumps_tags(clean_tags),
        status=status,
        summary=clean_summary,
        source_type=(source_type or "manual").strip()[:64] or "manual",
        source_id=(str(source_id).strip()[:64] if source_id else None),
        source_label=_clean_source_label(source_label, default="手工录入"),
        body_markdown=clean_body,
        payload_json=_dumps_payload(payload),
        stored_name=None,
        content_type=None,
        size_bytes=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_from_chunk(
    db: Session,
    workspace_id: str,
    *,
    chunk_id: str,
    title: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
    card_type: str = "document",
) -> KnowledgeCardRow:
    """
    用途：从知识分块复制正文快照为 document 卡；源 chunk 删除后卡片仍可用。
    对接：POST /api/cards/from-chunk。
    """
    if card_type not in TEXT_CARD_TYPES:
        raise CardValidationError("from-chunk 仅支持文本卡片类型")
    try:
        chunk = knowledge_service.get_chunk(db, workspace_id, chunk_id)
    except KnowledgeNotFoundError as exc:
        raise CardNotFoundError(chunk_id) from exc
    doc_name = ""
    try:
        doc = knowledge_service.get_doc(db, workspace_id, chunk.document_id)
        doc_name = doc.name or ""
    except KnowledgeNotFoundError:
        doc_name = ""
    body = chunk.content or ""
    default_title = (chunk.title or "").strip() or _summary_preview(body, 40) or "知识分块"
    source_label = (
        f"知识分块 · {doc_name}" if doc_name else f"知识分块 · {chunk_id}"
    )
    return create_text_card(
        db,
        workspace_id,
        card_type=card_type,
        title=title if title is not None else default_title,
        body_markdown=body,
        tags=tags,
        summary=summary,
        source_type="chunk",
        source_id=chunk_id,
        source_label=source_label,
        payload={
            "documentId": chunk.document_id,
            "chunkOrdinal": chunk.ordinal,
        },
    )


def _write_card_image(
    settings: Settings,
    workspace_id: str,
    content: bytes,
    content_type: str,
    suffix: str,
) -> tuple[str, int]:
    """用途：将已验证图片字节写入卡片存储，返回 (stored_name, size)。"""
    file_token = secrets.token_hex(8)
    stored = f"cardimg_{file_token}{suffix.lower()}"
    destination = _safe_card_path(settings, workspace_id, stored)
    destination.write_bytes(content)
    return stored, len(content)


def create_image_card_from_bytes(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    title: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
    source_type: str = "upload",
    source_id: str | None = None,
    source_label: str | None = None,
) -> KnowledgeCardRow:
    """
    用途：校验并保存图片快照为 image 卡片。
    对接：POST /api/cards/upload-image；from-project-image。
    """
    try:
        content_type, suffix = file_service.verify_image_content(content, settings)
    except ValueError as exc:
        raise CardValidationError(str(exc)) from exc
    safe_name = Path(filename).name or "image.png"
    clean_title = _clean_title(
        title if title is not None else Path(safe_name).stem,
        required=True,
    )
    stored, size = _write_card_image(
        settings, workspace_id, content, content_type, suffix
    )
    row = KnowledgeCardRow(
        id=_new_card_id(),
        workspace_id=workspace_id,
        type="image",
        title=clean_title,
        tags_json=_dumps_tags(_clean_tags(tags)),
        status="active",
        summary=_clean_summary(summary) or clean_title,
        source_type=(source_type or "upload").strip()[:64] or "upload",
        source_id=(str(source_id).strip()[:64] if source_id else None),
        source_label=_clean_source_label(source_label, default=safe_name),
        body_markdown="",
        payload_json=_dumps_payload({"originalFilename": safe_name}),
        stored_name=stored,
        content_type=content_type,
        size_bytes=size,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        try:
            _safe_card_path(settings, workspace_id, stored).unlink(missing_ok=True)
        except (OSError, CardValidationError):
            logger.warning("卡片图片提交失败后清理失败：%s", stored, exc_info=True)
        raise
    db.refresh(row)
    return row


def create_from_project_image(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    project_id: str,
    file_id: str,
    title: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
) -> KnowledgeCardRow:
    """
    用途：从项目 role=image 复制已验证字节到卡片存储；源项目删除后卡片仍可读。
    对接：POST /api/cards/from-project-image。
    """
    try:
        row, path = file_service.resolve_project_image(
            db, workspace_id, project_id, settings, file_id
        )
    except ProjectNotFoundError as exc:
        raise CardNotFoundError(project_id) from exc
    except KeyError as exc:
        raise CardNotFoundError(file_id) from exc
    except FileNotFoundError as exc:
        raise CardValidationError("项目图片文件不存在") from exc
    content = path.read_bytes()
    project = get_project(db, workspace_id, project_id)
    return create_image_card_from_bytes(
        db,
        workspace_id,
        settings,
        filename=row.filename or f"{file_id}.png",
        content=content,
        title=title,
        tags=tags,
        summary=summary,
        source_type="project_image",
        source_id=file_id,
        source_label=f"项目图片 · {project.name}",
    )


def update_card(
    db: Session,
    workspace_id: str,
    card_id: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    summary: str | None = None,
    body_markdown: str | None = None,
    source_label: str | None = None,
    payload: dict[str, Any] | None = None,
) -> KnowledgeCardRow:
    """
    用途：更新卡片元数据/正文；图片字节不可通过本接口改写。
    对接：PATCH /api/cards/{id}。
    """
    row = get_card(db, workspace_id, card_id)
    if title is not None:
        row.title = _clean_title(title)
    if tags is not None:
        row.tags_json = _dumps_tags(_clean_tags(tags))
    if status is not None:
        if status not in ALLOWED_CARD_STATUS:
            raise CardValidationError("非法卡片状态")
        row.status = status
    if summary is not None:
        row.summary = _clean_summary(summary)
    if body_markdown is not None:
        if row.type == "image":
            raise CardValidationError("图片卡片不支持修改正文")
        row.body_markdown = _clean_body(body_markdown, required=True)
    if source_label is not None:
        row.source_label = _clean_source_label(source_label)
    if payload is not None:
        row.payload_json = _dumps_payload(payload)
    row.updated_at = _now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_card(
    db: Session,
    workspace_id: str,
    settings: Settings,
    card_id: str,
) -> None:
    """
    用途：删除卡片并清理卡片图片文件；不影响已插入项目的图片副本。
    对接：DELETE /api/cards/{id}。
    """
    row = get_card(db, workspace_id, card_id)
    stored = row.stored_name
    db.delete(row)
    db.commit()
    if stored:
        try:
            _safe_card_path(settings, workspace_id, stored).unlink(missing_ok=True)
        except (OSError, CardValidationError):
            logger.warning("删除卡片图片失败：%s", stored, exc_info=True)


def resolve_card_image(
    db: Session,
    workspace_id: str,
    settings: Settings,
    card_id: str,
) -> tuple[KnowledgeCardRow, Path]:
    """
    用途：解析图片卡片磁盘路径（仅服务端 stored_name）。
    对接：GET /api/cards/{id}/content。
    """
    row = get_card(db, workspace_id, card_id)
    if row.type != "image" or not row.stored_name:
        raise CardValidationError("该卡片不是可读图片")
    path = _safe_card_path(settings, workspace_id, row.stored_name)
    if not path.is_file():
        raise FileNotFoundError(row.stored_name)
    return row, path


def build_text_insert_markdown(row: KnowledgeCardRow) -> str:
    """
    用途：文本/资质/业绩卡生成带标题与来源的引用块，供用户确认后追加正文。
    对接：insert_card_into_project。
    """
    title = (row.title or "未命名卡片").replace("\n", " ").strip()
    source = (row.source_label or "").replace("\n", " ").strip()
    body = (row.body_markdown or "").strip()
    lines = [f"> **{title}**"]
    if source:
        lines.append(f"> 来源：{source}")
    lines.append(">")
    if body:
        for line in body.splitlines() or [""]:
            lines.append(f"> {line}")
    else:
        lines.append("> （无正文）")
    return "\n".join(lines) + "\n"


def insert_card_into_project(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    project_id: str,
    card_id: str,
) -> dict[str, Any]:
    """
    用途：返回可插入 Markdown；图片卡先复制为项目 role=image。
    对接：POST /api/projects/{projectId}/insert-card。
    二次开发：本函数不写 editor-state，仅返回片段供前端用户操作写入。
    """
    get_project(db, workspace_id, project_id)
    row = get_card(db, workspace_id, card_id)
    if row.type == "image":
        _, path = resolve_card_image(db, workspace_id, settings, card_id)
        content = path.read_bytes()
        filename = row.title or "card-image"
        if row.content_type == "image/png" and not filename.lower().endswith(".png"):
            filename = f"{filename}.png"
        elif row.content_type == "image/jpeg" and not (
            filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg")
        ):
            filename = f"{filename}.jpg"
        elif row.content_type == "image/gif" and not filename.lower().endswith(".gif"):
            filename = f"{filename}.gif"
        try:
            image_row = file_service.save_image_upload(
                db,
                workspace_id,
                project_id,
                settings,
                filename=filename,
                content=content,
            )
        except ProjectNotFoundError as exc:
            raise CardNotFoundError(project_id) from exc
        except ValueError as exc:
            raise CardValidationError(str(exc)) from exc
        alt = (row.title or image_row.filename or "项目图片").replace(
            "\n", " "
        ).strip() or "项目图片"
        markdown = f"![{alt}](biaoshu-image://{image_row.id})\n"
        return {
            "markdown": markdown,
            "project_image_id": image_row.id,
            "card_id": row.id,
            "card_type": row.type,
            "title": row.title,
            "source_label": row.source_label or "",
        }

    if row.type not in TEXT_CARD_TYPES:
        raise CardValidationError("不支持的卡片类型")
    return {
        "markdown": build_text_insert_markdown(row),
        "project_image_id": None,
        "card_id": row.id,
        "card_type": row.type,
        "title": row.title,
        "source_label": row.source_label or "",
    }
