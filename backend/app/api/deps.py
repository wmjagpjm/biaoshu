"""
模块：API 公共依赖
用途：为路由注入数据库会话上下文与「当前工作空间 id」。
对接：各业务路由 Depends(get_workspace_id) / Depends(get_db)
二次开发：
  - 登录态：在此解析 Authorization，映射 user → workspace
  - 多成员：校验 X-Workspace-Id 是否属于当前用户
"""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services.project_service import ensure_default_workspace


def get_workspace_id(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> str:
    """
    用途：解析当前请求的 workspace。
    规则：
      1. 先 ensure 默认 workspace 存在
      2. 若带请求头 X-Workspace-Id 且非空，使用该 id（个人版高级/调试）
      3. 否则使用 settings.default_workspace_id
    返回：workspace 主键字符串。
    """
    ensure_default_workspace(db, settings)
    if x_workspace_id and x_workspace_id.strip():
        return x_workspace_id.strip()
    return settings.default_workspace_id
