"""
模块：项目文件上传服务
用途：保存上传文件到本地 uploads，并记录 ProjectFileRow。
对接：POST /api/projects/{id}/files、GET 列表
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import ProjectFileRow
from app.services.project_service import get_project


def _upload_root(settings: Settings) -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_files(db: Session, workspace_id: str, project_id: str) -> list[ProjectFileRow]:
    """用途：列出项目文件（新→旧）。"""
    get_project(db, workspace_id, project_id)
    stmt = (
        select(ProjectFileRow)
        .where(ProjectFileRow.project_id == project_id)
        .order_by(ProjectFileRow.created_at.desc())
    )
    return list(db.scalars(stmt).all())


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
    """
    用途：校验大小后落盘并写库。
    """
    get_project(db, workspace_id, project_id)
    if len(content) > settings.max_upload_bytes:
        raise ValueError(
            f"文件过大（{len(content)} 字节），上限 {settings.max_upload_bytes}"
        )
    safe_name = Path(filename).name or "upload.bin"
    file_id = f"file_{secrets.token_hex(8)}"
    ext = Path(safe_name).suffix
    stored = f"{file_id}{ext}"
    proj_dir = _upload_root(settings) / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    dest = proj_dir / stored
    dest.write_bytes(content)

    row = ProjectFileRow(
        id=file_id,
        project_id=project_id,
        filename=safe_name,
        stored_name=stored,
        content_type=content_type or "",
        size_bytes=len(content),
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def resolve_path(settings: Settings, project_id: str, stored_name: str) -> Path:
    """用途：解析磁盘绝对路径。"""
    return _upload_root(settings) / project_id / stored_name
