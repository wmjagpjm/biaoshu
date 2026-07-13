"""
模块：国能 e 招计划追踪路由
用途：提供本机招标计划 .xlsx 受控导入入口；仅内存解析、工作空间隔离写入。
对接：/api/opportunity-watch；opportunity_watch_service.import_watch_plans_from_xlsx；OpportunityWatchPlanImportOut。
二次开发：本阶段不得增加同步、URL/Cookie/Token 入参、后台任务、前端代理或真实外网请求。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import OpportunityWatchPlanImportOut
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
