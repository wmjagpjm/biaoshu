"""
模块：工作空间设置路由
用途：读写模型配置 + 默认导出模板。
对接：GET|PUT /api/settings
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import WorkspaceSettingsOut, WorkspaceSettingsUpdate
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


@router.get("", response_model=WorkspaceSettingsOut)
def get_settings(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> WorkspaceSettingsOut:
    row = settings_service.get_or_create_settings(db, workspace_id)
    return _to_out(row)


@router.put("", response_model=WorkspaceSettingsOut)
def put_settings(
    body: WorkspaceSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> WorkspaceSettingsOut:
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
