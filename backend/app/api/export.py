"""
模块：导出下载路由
用途：下载已生成的 docx；也可直接触发导出任务后下载。
对接：GET /api/projects/{id}/export/download/{stored}
"""

from typing import Annotated
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services.export_service import build_safe_docx_filename
from app.services.project_service import ProjectNotFoundError, get_project

router = APIRouter(prefix="/projects", tags=["export"])


@router.get("/{project_id}/export/download/{stored_name}")
def download_export(
    project_id: str,
    stored_name: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """
    用途：下载 exports 目录下的 docx（stored_name 仅允许 export_*.docx）。
    Content-Disposition 人读名取下载时权威 project.name，经 build_safe_docx_filename 收敛。
    """
    try:
        project = get_project(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None

    name = Path(stored_name).name
    if not name.startswith("export_") or not name.endswith(".docx"):
        raise HTTPException(status_code=400, detail="非法文件名")
    path = Path(settings.upload_dir) / project_id / "exports" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在，请先执行 export 任务")
    # 磁盘仍按随机 basename；FileResponse 交给 Starlette 编码人读 filename
    human_name = build_safe_docx_filename(project.name)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=human_name,
    )
