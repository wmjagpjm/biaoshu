"""
模块：工作空间设置服务
用途：读写 LLM 配置 + 默认导出模板 JSON。
对接：GET|PUT /api/settings；export_service 读 export_format_json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import WorkspaceSettingsRow

DEFAULT_PROVIDER = "openai-compatible"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_PARSE = "light"
ALLOWED_PARSE = frozenset({"light", "local", "ask"})


def get_or_create_settings(db: Session, workspace_id: str) -> WorkspaceSettingsRow:
    """用途：取当前 workspace 设置；不存在则写入默认行。"""
    row = db.get(WorkspaceSettingsRow, workspace_id)
    if row is not None:
        return row
    row = WorkspaceSettingsRow(
        workspace_id=workspace_id,
        provider=DEFAULT_PROVIDER,
        api_base_url=DEFAULT_API_BASE,
        api_key="",
        model=DEFAULT_MODEL,
        parse_strategy=DEFAULT_PARSE,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_export_format(db: Session, workspace_id: str) -> dict | None:
    """用途：读取默认导出模板 JSON 对象。"""
    row = get_or_create_settings(db, workspace_id)
    if not row.export_format_json:
        return None
    try:
        data = json.loads(row.export_format_json)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def update_settings(
    db: Session,
    workspace_id: str,
    *,
    provider: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    parse_strategy: str | None = None,
    embedding_model: str | None = None,
    export_format: dict | Any | None = ...,
) -> WorkspaceSettingsRow:
    """
    用途：部分更新；export_format 用 Ellipsis 表示未传，None 可清空。
    embedding_model：空字符串表示仅用本地哈希向量。
    """
    row = get_or_create_settings(db, workspace_id)
    if provider is not None:
        row.provider = provider.strip() or DEFAULT_PROVIDER
    if api_base_url is not None:
        row.api_base_url = api_base_url.strip().rstrip("/")
    if api_key is not None:
        row.api_key = api_key
    if model is not None:
        row.model = model.strip() or DEFAULT_MODEL
    if parse_strategy is not None:
        ps = parse_strategy.strip()
        if ps not in ALLOWED_PARSE:
            raise ValueError(f"非法 parseStrategy: {parse_strategy}")
        row.parse_strategy = ps
    if embedding_model is not None:
        row.embedding_model = embedding_model.strip()
    if export_format is not ...:
        if export_format is None:
            row.export_format_json = None
        elif isinstance(export_format, dict):
            row.export_format_json = json.dumps(export_format, ensure_ascii=False)
        else:
            raise ValueError("exportFormat 须为对象")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
