"""
模块：项目任务路由
用途：创建/查询/列表/取消任务，并为单任务提供 SSE 状态流；默认异步，?sync=1 同步执行。
对接：POST/GET /api/projects/{id}/tasks；GET .../tasks/{id}/events；POST .../tasks/{id}/cancel
二次开发：
  - SSE 连接前经私有依赖短 Session 复用 get_workspace_id（required=活动空间+bid_writer；disabled=默认/显式头）
  - 长连接不得挂 request-scope get_db，不得捕获 Session/ORM 行；流内每轮短 Session 再校验 workspace
  - 多任务总线、事件游标、URL token 鉴权须独立设计，禁止并入本路由
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
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.services import task_service
from app.services.project_service import ProjectNotFoundError

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


def _resolve_sse_workspace_id(
    project_id: str,
    task_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> str:
    """
    用途：SSE 专用连接前鉴权——短 Session 解析 workspace 并校验任务归属后立即关闭。
    对接：stream_task_events；显式调用 get_workspace_id / task_service.get_task。
    二次开发：
      - required：无头用 activeWorkspaceId；显式头仅成员空间；角色精确 bid_writer
      - disabled：保持默认空间与合法 X-Workspace-Id 个人版兼容
      - 只返回 workspace_id 字符串；禁止把 Session/ORM 行交给 StreamingResponse
      - finally 必须在路径返回 StreamingResponse 前关闭会话
    """
    db = SessionLocal()
    try:
        workspace_id = get_workspace_id(request, db, settings, x_workspace_id)
        task_service.get_task(db, workspace_id, project_id, task_id)
        return workspace_id
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    finally:
        db.close()


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
    workspace_id: Annotated[str, Depends(_resolve_sse_workspace_id)],
):
    """
    用途：以 SSE 推送单个任务的完整快照、状态变化和心跳，终态后自动关闭。
    对接：useProjectPipeline 的 EventSource；GET /api/projects/{id}/tasks/{taskId} 回退查询。
    二次开发：
      - workspace_id 由连接前短会话依赖解析；生成器每轮 run_in_threadpool 精确传入该值
      - 不得持有请求 Session；不得回退默认空间或只按任务主键读取
    """
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
                task_service._read_task_snapshot, workspace_id, project_id, task_id
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
