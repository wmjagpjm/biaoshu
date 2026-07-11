"""
模块：本地标讯库路由
用途：提供工作空间隔离的标讯 CRUD、截止状态筛选和从标讯创建技术标项目的 HTTP 入口。
对接：/api/opportunities；frontend/src/features/bid-opportunity；opportunity_service。
二次开发：外部数据源或多用户鉴权应在 service 保持归属校验后再扩展；路由层不得直接读写 ORM。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    OpportunityCreate,
    OpportunityImportOut,
    OpportunityOut,
    OpportunityProjectCreate,
    OpportunityUpdate,
    ProjectOut,
)
from app.core.database import get_db
from app.core.config import Settings, get_settings
from app.services import opportunity_service

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


def _to_out(row) -> OpportunityOut:
    """用途：服务读模型转 Pydantic 响应，统一 snake_case 到 camelCase 序列化。"""
    return OpportunityOut.model_validate(row)


def _to_project_out(project) -> ProjectOut:
    """用途：标讯立项结果复用既有 ProjectOut 响应契约。"""
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[OpportunityOut])
def list_opportunities(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    region: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[
        Literal["all", "open", "closing_soon", "closed"] | None,
        Query(alias="status"),
    ] = None,
) -> list[OpportunityOut]:
    """
    用途：列表读取；关键词、地区与动态截止状态均由服务端筛选。
    对接：GET /api/opportunities；opportunity_service.list_opportunities。
    """
    return [
        _to_out(item)
        for item in opportunity_service.list_opportunities(
            db,
            workspace_id,
            q=q,
            region=region,
            status=status_filter,
        )
    ]


@router.post("", response_model=OpportunityOut, status_code=status.HTTP_201_CREATED)
def create_opportunity(
    body: OpportunityCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> OpportunityOut:
    """
    用途：手工录入一条本地标讯，201 返回服务端计算状态。
    对接：POST /api/opportunities；OpportunityCreate；opportunity_service.create_opportunity。
    """
    try:
        row = opportunity_service.create_opportunity(
            db,
            workspace_id,
            body.model_dump(by_alias=False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(opportunity_service.opportunity_to_data(row))


@router.post(
    "/import",
    response_model=OpportunityImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_opportunities(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="本机 CSV 或 JSON 标讯清单"),
) -> OpportunityImportOut:
    """
    用途：解析本机 CSV/JSON 标讯清单并整批导入当前工作空间，不保存原始文件。
    对接：POST /api/opportunities/import；标讯页导入弹层；opportunity_service.import_opportunities_from_file。
    二次开发：外部同步、URL 下载和附件解析必须另设受控流程，不得扩展此本机导入入口。
    """
    if file.size is not None and file.size > settings.max_opportunity_import_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"导入文件不能超过 {settings.max_opportunity_import_bytes} 字节",
        )
    raw = await file.read()
    if len(raw) > settings.max_opportunity_import_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"导入文件不能超过 {settings.max_opportunity_import_bytes} 字节",
        )
    try:
        result = opportunity_service.import_opportunities_from_file(
            db,
            workspace_id,
            filename=file.filename or "",
            content=raw,
            max_rows=settings.max_opportunity_import_rows,
        )
    except opportunity_service.OpportunityImportValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "errors": exc.errors},
        ) from None
    except opportunity_service.OpportunityImportConflictError:
        raise HTTPException(status_code=409, detail="来源键与现有标讯冲突，请刷新后重试") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return OpportunityImportOut.model_validate(result)


@router.get("/{opportunity_id}", response_model=OpportunityOut)
def get_opportunity(
    opportunity_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> OpportunityOut:
    """
    用途：读取单条标讯；不存在或跨 workspace 时返回 404。
    对接：GET /api/opportunities/{id}；opportunity_service.get_opportunity。
    """
    try:
        row = opportunity_service.get_opportunity(db, workspace_id, opportunity_id)
    except opportunity_service.OpportunityNotFoundError:
        raise HTTPException(status_code=404, detail="标讯不存在") from None
    return _to_out(opportunity_service.opportunity_to_data(row))


@router.patch("/{opportunity_id}", response_model=OpportunityOut)
def patch_opportunity(
    opportunity_id: str,
    body: OpportunityUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> OpportunityOut:
    """
    用途：部分更新标讯；status 不可写，始终由 deadline 计算。
    对接：PATCH /api/opportunities/{id}；OpportunityUpdate；opportunity_service.update_opportunity。
    """
    try:
        row = opportunity_service.update_opportunity(
            db,
            workspace_id,
            opportunity_id,
            body.model_dump(by_alias=False, exclude_unset=True),
        )
    except opportunity_service.OpportunityNotFoundError:
        raise HTTPException(status_code=404, detail="标讯不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(opportunity_service.opportunity_to_data(row))


@router.delete("/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_opportunity(
    opportunity_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> Response:
    """
    用途：删除标讯并清空项目弱关联，不级联删除已有项目。
    对接：DELETE /api/opportunities/{id}；opportunity_service.delete_opportunity。
    """
    try:
        opportunity_service.delete_opportunity(db, workspace_id, opportunity_id)
    except opportunity_service.OpportunityNotFoundError:
        raise HTTPException(status_code=404, detail="标讯不存在") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{opportunity_id}/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
def create_project_from_opportunity(
    opportunity_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    body: OpportunityProjectCreate | None = None,
) -> ProjectOut:
    """
    用途：从未截止标讯原子创建技术标项目；已截止标讯返回 400。
    对接：POST /api/opportunities/{id}/projects；opportunity_service.create_project_from_opportunity。
    """
    payload = body or OpportunityProjectCreate()
    try:
        project = opportunity_service.create_project_from_opportunity(
            db,
            workspace_id,
            opportunity_id,
            name=payload.name,
            industry=payload.industry,
        )
    except opportunity_service.OpportunityNotFoundError:
        raise HTTPException(status_code=404, detail="标讯不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_project_out(project)
