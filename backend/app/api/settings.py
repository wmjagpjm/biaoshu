"""
模块：工作空间设置路由
用途：读写模型配置 + 默认导出模板；提供解析策略脱敏只读接口。
对接：GET|PUT /api/settings（require_owner）；GET /api/settings/parse-strategy（get_workspace_id）。
二次开发：禁止向非所有者回显 apiKey；parse-strategy 仅返回策略枚举且 Cache-Control:no-store。
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id, require_owner
from app.api.schemas import ParseStrategyOut, WorkspaceSettingsOut, WorkspaceSettingsUpdate
from app.core.database import get_db
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


def _to_out(row) -> WorkspaceSettingsOut:
    """用途：ORM → 响应（含 exportFormat）。"""
    export_format = None
    if getattr(row, "export_format_json", None):
        try:
            export_format = json.loads(row.export_format_json)
        except json.JSONDecodeError:
            export_format = None
    return WorkspaceSettingsOut(
        provider=row.provider,
        api_base_url=row.api_base_url,
        api_key=row.api_key,
        model=row.model,
        parse_strategy=row.parse_strategy,
        embedding_model=getattr(row, "embedding_model", None) or "",
        export_format=export_format if isinstance(export_format, dict) else None,
        updated_at=row.updated_at,
    )


def _no_store(response: Response) -> None:
    """用途：解析策略响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


@router.get("/parse-strategy", response_model=ParseStrategyOut)
def get_parse_strategy(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ParseStrategyOut:
    """
    模块：解析策略脱敏读取
    用途：返回当前工作空间 parseStrategy；无设置行时默认 light 且不建行。
    对接：前端策略决策 Hook；settings_service.get_parse_strategy；deps.get_workspace_id。
    二次开发：仅 GET；固定 Cache-Control:no-store；禁止复用完整 WorkspaceSettingsOut 或返回 Key。
    """
    _no_store(response)
    strategy = settings_service.get_parse_strategy(db, workspace_id)
    return ParseStrategyOut(parse_strategy=strategy)


@router.get("", response_model=WorkspaceSettingsOut)
def get_settings(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_owner)],
) -> WorkspaceSettingsOut:
    """
    模块：完整设置读取
    用途：读取工作空间设置；required 模式仅所有者。
    对接：设置页；require_owner。
    二次开发：不得改为 get_workspace_id；与 parse-strategy 权限语义分离。
    """
    row = settings_service.get_or_create_settings(db, workspace_id)
    return _to_out(row)


@router.put("", response_model=WorkspaceSettingsOut)
def put_settings(
    body: WorkspaceSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_owner)],
) -> WorkspaceSettingsOut:
    """
    模块：完整设置写入
    用途：部分更新工作空间设置；required 模式仅所有者。
    对接：设置页；require_owner。
    二次开发：所有者限制与序列化语义不得因 parse-strategy 而放宽。
    """
    dumped = body.model_dump(by_alias=False, exclude_unset=True)
    kwargs: dict = {}
    for key in (
        "provider",
        "api_base_url",
        "api_key",
        "model",
        "parse_strategy",
        "embedding_model",
    ):
        if key in dumped:
            kwargs[key] = dumped[key]
    if "export_format" in dumped:
        kwargs["export_format"] = dumped["export_format"]
    try:
        row = settings_service.update_settings(db, workspace_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(row)
