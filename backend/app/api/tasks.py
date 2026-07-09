"""
模块：项目任务路由
用途：创建/查询/列表任务（parse/analyze/outline/chapter/export）。
对接：POST/GET /api/projects/{id}/tasks
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.database import get_db
from app.services import task_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["tasks"])


class TaskCreate(BaseModel):
    """用途：创建任务请求。"""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(description="parse|analyze|outline|chapter|export")
    payload: dict[str, Any] | None = None


@router.get("/{project_id}/tasks")
def list_tasks(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> list[dict]:
    try:
        rows = task_service.list_tasks(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return [task_service.task_to_dict(t) for t in rows]


@router.get("/{project_id}/tasks/{task_id}")
def get_task(
    project_id: str,
    task_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    try:
        task = task_service.get_task(db, workspace_id, project_id, task_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    return task_service.task_to_dict(task)


@router.post("/{project_id}/tasks", status_code=201)
def create_task(
    project_id: str,
    body: TaskCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    """
    用途：创建并同步执行任务，返回最终状态（个人版）。
    """
    try:
        task = task_service.create_and_run_task(
            db,
            workspace_id,
            project_id,
            task_type=body.type,
            payload=body.payload,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return task_service.task_to_dict(task)
