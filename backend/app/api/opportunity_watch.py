"""
模块：国能 e 招计划追踪路由
用途：提供本机招标计划 .xlsx 受控导入，以及固定来源同步的受理与运行查询。
对接：/api/opportunity-watch；opportunity_watch_service；OpportunityWatchPlanImportOut / Sync 读模型。
二次开发：禁止增加 URL/Cookie/Token 入参、浏览器代理、dashboard 或真实外网直出；同步仅 BackgroundTasks。
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    OpportunityWatchPlanImportOut,
    OpportunityWatchSyncAcceptedOut,
    OpportunityWatchSyncRunOut,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import opportunity_watch_service

router = APIRouter(prefix="/opportunity-watch", tags=["opportunity-watch"])


@router.post(
    "/plans/import",
    response_model=OpportunityWatchPlanImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_watch_plans(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="本机招标计划 .xlsx"),
) -> OpportunityWatchPlanImportOut:
    """
    模块：计划表导入接口
    用途：接收本机 .xlsx，校验体积与扩展名后交给服务层内存导入；成功仅返回计数。
    对接：POST /api/opportunity-watch/plans/import；Settings 导入字节/行数上限。
    二次开发：禁止扩展为 URL 下载、路径读取、Cookie 提交或同步触发入口。
    """
    max_bytes = settings.max_opportunity_watch_import_bytes
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"导入文件不能超过 {max_bytes} 字节",
        )

    filename = file.filename or ""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix != "xlsx":
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 招标计划文件")

    raw = await file.read()
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"导入文件不能超过 {max_bytes} 字节",
        )

    try:
        result = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            workspace_id,
            filename=filename,
            content=raw,
            max_rows=settings.max_opportunity_watch_plan_rows,
        )
    except opportunity_watch_service.WatchPlanImportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "errors": exc.errors},
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    finally:
        # 请求结束后丢弃上传字节引用，避免误持久化。
        raw = b""

    return OpportunityWatchPlanImportOut.model_validate(result)


@router.post(
    "/sync",
    response_model=OpportunityWatchSyncAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_watch_sync(
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> OpportunityWatchSyncAcceptedOut:
    """
    模块：受控同步受理
    用途：无请求体创建 queued 运行并注册后台执行；同空间并发返回 409。
    对接：POST /api/opportunity-watch/sync；execute_sync_run；BackgroundTasks。
    二次开发：禁止接受 URL、Cookie、Token、主机或搜索条件；不得在此同步阻塞跑完外网。
    """
    try:
        run = opportunity_watch_service.create_watch_sync_run(db, workspace_id)
    except opportunity_watch_service.WatchSyncConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    background_tasks.add_task(opportunity_watch_service.execute_sync_run, run.id)
    return OpportunityWatchSyncAcceptedOut(run_id=run.id)


@router.get(
    "/runs/{run_id}",
    response_model=OpportunityWatchSyncRunOut,
)
def get_watch_sync_run(
    run_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> OpportunityWatchSyncRunOut:
    """
    模块：同步运行状态查询
    用途：仅返回当前工作空间的脱敏运行读模型；跨空间或不存在为 404。
    对接：GET /api/opportunity-watch/runs/{run_id}；OpportunityWatchSyncRunOut。
    二次开发：禁止回传 Cookie、URL、HTML、JSON 或远端错误原文。
    """
    row = opportunity_watch_service.get_watch_sync_run(db, workspace_id, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="同步运行不存在")
    return OpportunityWatchSyncRunOut.model_validate(row)
