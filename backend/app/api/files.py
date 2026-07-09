"""
模块：项目文件上传路由
用途：multipart 上传招标文件、列表文件。
对接：POST/GET /api/projects/{project_id}/files
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import file_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["files"])


@router.get("/{project_id}/files")
def list_project_files(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> list[dict]:
    """用途：列出项目已上传文件。"""
    try:
        rows = file_service.list_files(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return [
        {
            "id": r.id,
            "projectId": r.project_id,
            "filename": r.filename,
            "contentType": r.content_type,
            "sizeBytes": r.size_bytes,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/{project_id}/files", status_code=201)
async def upload_project_file(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="招标文件 pdf/docx/txt/md"),
) -> dict:
    """
    用途：上传单个文件到项目。
    对接：前端 document 步 FormData
    """
    raw = await file.read()
    try:
        row = file_service.save_upload(
            db,
            workspace_id,
            project_id,
            settings,
            filename=file.filename or "upload.bin",
            content=raw,
            content_type=file.content_type or "",
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "id": row.id,
        "projectId": row.project_id,
        "filename": row.filename,
        "contentType": row.content_type,
        "sizeBytes": row.size_bytes,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
