"""
模块：项目文件上传路由
用途：multipart 上传招标源文件和受控正文图片，并按 role 隔离列表/预览。
对接：POST/GET /api/projects/{project_id}/files；POST/GET /api/projects/{project_id}/images。
二次开发：图片字节必须经 file_service 的项目归属、角色和真实格式校验；禁止静态目录直出或远程抓图。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import file_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["files"])


def _file_to_dict(row) -> dict:
    """用途：项目文件 ORM 行转换为不暴露 stored_name 的 API 元数据。"""
    return {
        "id": row.id,
        "projectId": row.project_id,
        "filename": row.filename,
        "contentType": row.content_type,
        "sizeBytes": row.size_bytes,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


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
    return [_file_to_dict(row) for row in rows]


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
    return _file_to_dict(row)


@router.get("/{project_id}/images")
def list_project_images(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> list[dict]:
    """
    用途：列出当前项目可安全插入正文的图片，不返回招标源文件。
    对接：技术标正文编辑器图片选择器。
    """
    try:
        rows = file_service.list_images(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return [_file_to_dict(row) for row in rows]


@router.post("/{project_id}/images", status_code=201)
async def upload_project_image(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="正文图片 png/jpeg/gif"),
) -> dict:
    """
    用途：上传正文图片；服务端以真实格式而非客户端 MIME 校验后保存为 image。
    对接：技术标 ChapterEditor 的“插入图片”操作。
    """
    raw = await file.read()
    try:
        row = file_service.save_image_upload(
            db,
            workspace_id,
            project_id,
            settings,
            filename=file.filename or "image.bin",
            content=raw,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _file_to_dict(row)


@router.get("/{project_id}/images/{file_id}")
def download_project_image(
    project_id: str,
    file_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    """
    用途：按 workspace、项目和 image 角色校验后返回单张图片预览。
    对接：正文编辑器预览；不接受客户端路径或 stored_name。
    """
    try:
        row, path = file_service.resolve_project_image(
            db, workspace_id, project_id, settings, file_id
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="图片不存在") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="图片文件不存在") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="图片路径无效") from None
    return FileResponse(path, media_type=row.content_type, filename=row.filename)
