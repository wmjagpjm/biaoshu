"""
模块：工作空间设置服务
用途：读写 LLM 供应商 / Base URL / API Key / 模型 / 解析策略。
对接：
  - GET|PUT /api/settings
  - llm_service 调用前读取当前 workspace 配置
二次开发：
  - Key 按产品决策明文存储与回显（保密机）；若改公网部署请改为加密或密钥管理
  - 勿把含 Key 的 SQLite 提交 Git
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.entities import WorkspaceSettingsRow

# 与前端 DEFAULT_SETTINGS 对齐
DEFAULT_PROVIDER = "openai-compatible"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_PARSE = "light"
ALLOWED_PARSE = frozenset({"light", "local", "ask"})


def get_or_create_settings(db: Session, workspace_id: str) -> WorkspaceSettingsRow:
    """
    用途：取当前 workspace 设置；不存在则写入默认行。
    对接：GET /api/settings
    """
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


def update_settings(
    db: Session,
    workspace_id: str,
    *,
    provider: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    parse_strategy: str | None = None,
) -> WorkspaceSettingsRow:
    """
    用途：部分更新设置；None 表示不改该字段。
    对接：PUT /api/settings
    """
    row = get_or_create_settings(db, workspace_id)
    if provider is not None:
        row.provider = provider.strip() or DEFAULT_PROVIDER
    if api_base_url is not None:
        row.api_base_url = api_base_url.strip().rstrip("/")
    if api_key is not None:
        # 明文保存，前端可正常回显
        row.api_key = api_key
    if model is not None:
        row.model = model.strip() or DEFAULT_MODEL
    if parse_strategy is not None:
        ps = parse_strategy.strip()
        if ps not in ALLOWED_PARSE:
            raise ValueError(f"非法 parseStrategy: {parse_strategy}")
        row.parse_strategy = ps
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row
