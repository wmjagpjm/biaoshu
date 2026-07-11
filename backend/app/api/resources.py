"""
模块：资源中心路由
用途：提供系统精选与当前工作空间资源的读取、用户资源 CRUD 及服务端浏览量累加 HTTP 入口。
对接：/api/resources；frontend/src/features/resources；resource_service。
二次开发：受控外部同步应先进入服务层和审计流程；路由层不得直连外部 URL、读取密钥或直接写 ORM。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    ResourceCreate,
    ResourceOut,
    ResourceSyncSourceOut,
    ResourceUpdate,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.services import resource_service, resource_sync_service

router = APIRouter(prefix="/resources", tags=["resources"])


def _to_out(row) -> ResourceOut:
    """用途：服务读模型转 Pydantic 响应，统一 snake_case 到 camelCase 序列化。"""
    return ResourceOut.model_validate(row)


@router.get("", response_model=list[ResourceOut])
def list_resources(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=60)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
) -> list[ResourceOut]:
    """
    用途：读取系统精选和当前工作空间资源，关键词、标签与分类均由服务端筛选。
    对接：GET /api/resources；resource_service.list_resources；前端资源搜索。
    """
    return [
        _to_out(item)
        for item in resource_service.list_resources(
            db, workspace_id, q=q, tag=tag, category=category
        )
    ]


@router.post("", response_model=ResourceOut, status_code=status.HTTP_201_CREATED)
def create_resource(
    body: ResourceCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ResourceOut:
    """
    用途：创建当前工作空间用户资源，忽略任何客户端来源或 workspace 伪造意图。
    对接：POST /api/resources；ResourceCreate；resource_service.create_resource。
    """
    try:
        row = resource_service.create_resource(
            db, workspace_id, body.model_dump(by_alias=False)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(resource_service.resource_to_data(row))


@router.get("/sync-sources", response_model=list[ResourceSyncSourceOut])
def list_sync_sources(
    db: Annotated[Session, Depends(get_db)],
) -> list[ResourceSyncSourceOut]:
    """
    用途：读取当前服务端配置来源的脱敏同步状态；不提供浏览器触发同步或远端连接信息。
    对接：GET /api/resources/sync-sources；resource_sync_service.list_sync_source_statuses；管理员本机同步命令。
    二次开发：若未来加入管理员界面，仍应将实际同步保留在鉴权后的服务端任务，禁止接受 URL 请求参数。
    """
    try:
        return [
            ResourceSyncSourceOut.model_validate(item)
            for item in resource_sync_service.list_sync_source_statuses(db, get_settings())
        ]
    except resource_sync_service.ResourceSyncError:
        raise HTTPException(status_code=503, detail="同步来源配置不可用") from None


@router.get("/{resource_id}", response_model=ResourceOut)
def get_resource(
    resource_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ResourceOut:
    """
    用途：读取单条可见资源；其它工作空间用户资源统一隐藏为 404。
    对接：GET /api/resources/{id}；resource_service.get_visible_resource。
    """
    try:
        row = resource_service.get_visible_resource(db, workspace_id, resource_id)
    except resource_service.ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在") from None
    return _to_out(resource_service.resource_to_data(row))


@router.patch("/{resource_id}", response_model=ResourceOut)
def patch_resource(
    resource_id: str,
    body: ResourceUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ResourceOut:
    """
    用途：部分更新当前工作空间用户资源；系统资源返回 403。
    对接：PATCH /api/resources/{id}；resource_service.update_resource。
    """
    try:
        row = resource_service.update_resource(
            db,
            workspace_id,
            resource_id,
            body.model_dump(by_alias=False, exclude_unset=True),
        )
    except resource_service.ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在") from None
    except resource_service.ResourceReadOnlyError:
        raise HTTPException(status_code=403, detail="系统资源不可修改") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(resource_service.resource_to_data(row))


@router.delete("/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resource(
    resource_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> Response:
    """
    用途：删除当前工作空间用户资源；系统资源返回 403。
    对接：DELETE /api/resources/{id}；resource_service.delete_resource。
    """
    try:
        resource_service.delete_resource(db, workspace_id, resource_id)
    except resource_service.ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在") from None
    except resource_service.ResourceReadOnlyError:
        raise HTTPException(status_code=403, detail="系统资源不可删除") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{resource_id}/view", response_model=ResourceOut)
def record_resource_view(
    resource_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ResourceOut:
    """
    用途：服务端原子累加可见资源浏览量，并返回最新资源读模型。
    对接：POST /api/resources/{id}/view；resource_service.record_resource_view。
    """
    try:
        row = resource_service.record_resource_view(db, workspace_id, resource_id)
    except resource_service.ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在") from None
    return _to_out(resource_service.resource_to_data(row))
