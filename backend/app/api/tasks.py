"""
模块：项目任务路由
用途：创建/查询/列表/取消任务，并为单任务提供 SSE 状态流；默认异步，?sync=1 同步执行。
对接：POST/GET /api/projects/{id}/tasks；GET .../tasks/{id}/events；POST .../tasks/{id}/cancel
二次开发：SSE 只读数据库快照；多任务总线、事件游标和鉴权升级须独立设计。
"""

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.services import task_service
from app.services.project_service import ProjectNotFoundError, ensure_default_workspace

router = APIRouter(prefix="/projects", tags=["tasks"])

_SSE_POLL_SECONDS = 0.25
_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_MAX_SECONDS = 11 * 60


class TaskCreate(BaseModel):
    """用途：创建任务请求。"""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(
        description=(
            "parse|analyze|outline|chapter|chapters|export|"
            "response_match|content_fuse"
        )
    )
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


@router.get("/{project_id}/tasks/{task_id}/events")
async def stream_task_events(
    project_id: str,
    task_id: str,
    request: Request,
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
):
    """
    用途：以 SSE 推送单个任务的完整快照、状态变化和心跳，终态后自动关闭。
    对接：useProjectPipeline 的 EventSource；GET /api/projects/{id}/tasks/{taskId} 回退查询。
    """
    settings = get_settings()
    workspace_id = (
        x_workspace_id.strip()
        if x_workspace_id and x_workspace_id.strip()
        else settings.default_workspace_id
    )
    # 连接前短会话校验归属，生成器中不持有请求级 Session。
    db = SessionLocal()
    try:
        ensure_default_workspace(db, settings)
        task_service.get_task(db, workspace_id, project_id, task_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    finally:
        db.close()

    async def event_stream():
        started_at = time.monotonic()
        last_signature: str | None = None
        last_heartbeat_at = started_at
        while True:
            if await request.is_disconnected():
                return
            if time.monotonic() - started_at >= _SSE_MAX_SECONDS:
                yield task_service._format_sse_event(
                    "error", {"message": "SSE 连接超时，请改用任务查询接口确认状态"}
                )
                return

            snapshot = await run_in_threadpool(
                task_service._read_task_snapshot, project_id, task_id
            )
            if snapshot is None:
                yield task_service._format_sse_event(
                    "error", {"message": "任务不存在或已删除"}
                )
                return

            signature = task_service._task_snapshot_signature(snapshot)
            if last_signature is None:
                yield task_service._format_sse_event("snapshot", snapshot)
                last_signature = signature
            elif signature != last_signature:
                yield task_service._format_sse_event("task", snapshot)
                last_signature = signature

            if snapshot["status"] in task_service.TERMINAL_STATUSES:
                return

            now = time.monotonic()
            if now - last_heartbeat_at >= _SSE_HEARTBEAT_SECONDS:
                yield task_service._format_sse_event(
                    "heartbeat", {"ts": snapshot["updatedAt"]}
                )
                last_heartbeat_at = now
            await asyncio.sleep(_SSE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{project_id}/tasks/{task_id}/cancel")
def cancel_task(
    project_id: str,
    task_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    """
    用途：取消 pending/running 任务；worker 在检查点协作退出。
    对接：前端 useProjectPipeline.cancelTask
    """
    try:
        task = task_service.cancel_task(db, workspace_id, project_id, task_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return task_service.task_to_dict(task)


@router.post("/{project_id}/tasks", status_code=201)
def create_task(
    project_id: str,
    body: TaskCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    sync: Annotated[
        bool, Query(description="true 时阻塞执行完再返回（测试用）")
    ] = False,
) -> dict:
    """
    用途：默认异步入队；sync=true 同步跑完（pytest / 冒烟）。
    """
    try:
        if sync:
            task = task_service.create_and_run_task(
                db,
                workspace_id,
                project_id,
                task_type=body.type,
                payload=body.payload,
            )
        else:
            task = task_service.enqueue_task(
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
