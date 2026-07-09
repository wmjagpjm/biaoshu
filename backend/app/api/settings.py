"""
模块：工作空间设置路由
用途：读写模型供应商、Base URL、API Key（明文）、模型名、解析策略。
对接：
  - GET|PUT /api/settings
  - 前端 useWorkspaceSettings / features/settings/types.ts
二次开发：Key 明文回显为产品决策（保密机）；勿提交含 Key 的数据库文件。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import WorkspaceSettingsOut, WorkspaceSettingsUpdate
from app.core.database import get_db
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


def _to_out(row) -> WorkspaceSettingsOut:
    """用途：ORM → 前端 WorkspaceSettings（camelCase）。"""
    return WorkspaceSettingsOut(
        provider=row.provider,
        api_base_url=row.api_base_url,
        api_key=row.api_key,
        model=row.model,
        parse_strategy=row.parse_strategy,
        updated_at=row.updated_at,
    )


@router.get("", response_model=WorkspaceSettingsOut)
def get_settings(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> WorkspaceSettingsOut:
    """
    用途：读取当前 workspace 设置；apiKey 明文返回以便设置页正常显示。
    对接：前端 useWorkspaceSettings 初始加载
    """
    row = settings_service.get_or_create_settings(db, workspace_id)
    return _to_out(row)


@router.put("", response_model=WorkspaceSettingsOut)
def put_settings(
    body: WorkspaceSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> WorkspaceSettingsOut:
    """
    用途：整包/部分更新设置并落库（明文 Key）。
    对接：设置页「保存」
    """
    try:
        row = settings_service.update_settings(
            db,
            workspace_id,
            provider=body.provider,
            api_base_url=body.api_base_url,
            api_key=body.api_key,
            model=body.model,
            parse_strategy=body.parse_strategy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(row)
