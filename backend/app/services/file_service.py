"""
模块：项目文件与正文图片服务
用途：保存 source 招标文件和 image 正文图片，按角色隔离 parse，并提供数据库归属校验后的安全磁盘路径。
对接：POST/GET /api/projects/{id}/files、POST/GET /api/projects/{id}/images、任务 parse/export。
二次开发：不得从 Markdown 或客户端路径直接读文件；新增对象存储、格式或多用户权限时必须保留 project_id + role 校验链。
"""

from __future__ import annotations

import logging
import secrets
import warnings
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import Project, ProjectFileRow
from app.services.project_service import ProjectNotFoundError, get_project

logger = logging.getLogger(__name__)

FILE_ROLE_SOURCE = "source"
FILE_ROLE_IMAGE = "image"
_ALLOWED_IMAGE_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
}


def _upload_root(settings: Settings) -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _project_dir(settings: Settings, project_id: str) -> Path:
    """用途：返回当前项目上传目录；目录名只由服务端 project_id 控制。"""
    directory = _upload_root(settings) / project_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _role_condition(role: str):
    if role == FILE_ROLE_SOURCE:
        # 历史库在轻量迁移前可能存在空 role，读取时一律按 source 兼容。
        return or_(ProjectFileRow.role == role, ProjectFileRow.role.is_(None))
    return ProjectFileRow.role == role


def list_files(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    role: str = FILE_ROLE_SOURCE,
) -> list[ProjectFileRow]:
    """用途：按角色列出项目文件（新→旧）；默认只返回招标源文件。"""
    get_project(db, workspace_id, project_id)
    if role not in {FILE_ROLE_SOURCE, FILE_ROLE_IMAGE}:
        raise ValueError("非法文件角色")
    stmt = (
        select(ProjectFileRow)
        .where(
            ProjectFileRow.project_id == project_id,
            _role_condition(role),
        )
        .order_by(ProjectFileRow.created_at.desc())
    )
    return list(db.scalars(stmt).all())


def list_images(
    db: Session, workspace_id: str, project_id: str
) -> list[ProjectFileRow]:
    """用途：列出当前项目可插入正文的受控图片。"""
    return list_files(db, workspace_id, project_id, role=FILE_ROLE_IMAGE)


def _safe_destination(settings: Settings, project_id: str, stored_name: str) -> Path:
    """用途：只允许服务端 stored_name 解析到当前项目上传目录内。"""
    name = Path(stored_name).name
    if not name or name != stored_name:
        raise ValueError("非法存储文件名")
    directory = _project_dir(settings, project_id).resolve()
    destination = (directory / name).resolve()
    if not destination.is_relative_to(directory):
        raise ValueError("文件路径越界")
    return destination


def _save_upload(
    db: Session,
    workspace_id: str,
    project_id: str,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    role: str,
    stored_suffix: str | None = None,
) -> ProjectFileRow:
    get_project(db, workspace_id, project_id)
    if role not in {FILE_ROLE_SOURCE, FILE_ROLE_IMAGE}:
        raise ValueError("非法文件角色")
    safe_name = Path(filename).name or "upload.bin"
    file_id = f"file_{secrets.token_hex(8)}"
    suffix = stored_suffix if stored_suffix is not None else Path(safe_name).suffix
    stored = f"{file_id}{suffix.lower()}"
    destination = _safe_destination(settings, project_id, stored)
    row = ProjectFileRow(
        id=file_id,
        project_id=project_id,
        filename=safe_name,
        stored_name=stored,
        content_type=content_type,
        size_bytes=len(content),
        role=role,
        created_at=datetime.now(timezone.utc),
    )
    destination.write_bytes(content)
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "上传提交失败后清理孤儿文件失败：%s",
                destination.name,
                exc_info=True,
            )
        raise
    db.refresh(row)
    return row


def save_upload(
    db: Session,
    workspace_id: str,
    project_id: str,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    content_type: str = "",
) -> ProjectFileRow:
    """用途：保存既有招标源文件；保持原有 50MB 文件上传契约。"""
    if len(content) > settings.max_upload_bytes:
        raise ValueError(
            f"文件过大（{len(content)} 字节），上限 {settings.max_upload_bytes}"
        )
    return _save_upload(
        db,
        workspace_id,
        project_id,
        settings,
        filename=filename,
        content=content,
        content_type=content_type or "",
        role=FILE_ROLE_SOURCE,
    )


def _verified_image_info(content: bytes, settings: Settings) -> tuple[str, str]:
    """用途：以真实解码结果验证图片格式和像素，客户端 MIME 与扩展名不参与信任判断。"""
    if not content:
        raise ValueError("图片不能为空")
    if len(content) > settings.max_image_upload_bytes:
        raise ValueError(
            f"图片过大（{len(content)} 字节），上限 {settings.max_image_upload_bytes}"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image_format = (image.format or "").upper()
                width, height = image.size
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise ValueError("图片格式无效或已损坏") from exc
    if image_format not in _ALLOWED_IMAGE_FORMATS:
        raise ValueError("仅支持 PNG、JPEG、GIF 图片")
    if width < 1 or height < 1 or width * height > settings.max_image_pixels:
        raise ValueError("图片像素超出限制")
    return _ALLOWED_IMAGE_FORMATS[image_format]


def _acquire_image_upload_lock(
    db: Session, workspace_id: str, project_id: str
) -> None:
    """用途：在 SQLite 个人版用无副作用 UPDATE 获得当前项目的写锁。"""
    if db.get_bind().dialect.name == "sqlite":
        # 避免显式 BEGIN IMMEDIATE 与已开始的 SQLAlchemy Session 事务嵌套。
        result = db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
            .values(updated_at=Project.updated_at)
        )
        if result.rowcount == 0:
            raise ProjectNotFoundError(project_id)
        return
    get_project(db, workspace_id, project_id)


def save_image_upload(
    db: Session,
    workspace_id: str,
    project_id: str,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
) -> ProjectFileRow:
    """用途：验证并原子保存 image；SQLite 下并发上传不会绕过项目数量上限。"""
    try:
        _acquire_image_upload_lock(db, workspace_id, project_id)
        content_type, suffix = _verified_image_info(content, settings)
        image_count = len(list_images(db, workspace_id, project_id))
        if image_count >= settings.max_project_images:
            raise ValueError(f"项目图片数量已达上限（{settings.max_project_images}）")
        return _save_upload(
            db,
            workspace_id,
            project_id,
            settings,
            filename=filename,
            content=content,
            content_type=content_type,
            role=FILE_ROLE_IMAGE,
            stored_suffix=suffix,
        )
    except Exception:
        # 数量拒绝、解码失败或写入失败均需回滚并释放 SQLite 写事务锁。
        db.rollback()
        raise


def resolve_path(settings: Settings, project_id: str, stored_name: str) -> Path:
    """用途：解析服务端存储文件路径，并拒绝目录穿越。"""
    return _safe_destination(settings, project_id, stored_name)


def resolve_project_image(
    db: Session,
    workspace_id: str,
    project_id: str,
    settings: Settings,
    file_id: str,
) -> tuple[ProjectFileRow, Path]:
    """用途：按当前项目、图片角色和服务端 stored_name 解析可导出的图片。"""
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectFileRow, file_id)
    if (
        row is None
        or row.project_id != project_id
        or row.role != FILE_ROLE_IMAGE
    ):
        raise KeyError(file_id)
    path = resolve_path(settings, project_id, row.stored_name)
    if not path.is_file():
        raise FileNotFoundError(row.stored_name)
    return row, path
