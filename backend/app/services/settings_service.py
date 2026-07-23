"""
模块：工作空间设置服务
用途：读写 LLM 配置 + 默认导出模板 JSON；提供解析策略只读查询。
对接：GET|PUT /api/settings；GET /api/settings/parse-strategy；export_service 读 export_format_json
二次开发：parse-strategy 只读不得建行；完整设置读写仍走 get_or_create_settings。
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
# M3：精确四值 light|managed|local|ask；默认仍 light
ALLOWED_PARSE = frozenset({"light", "managed", "local", "ask"})

# 模块私有 sentinel：区分 update_settings 的 parse_strategy「未传」与显式 None
# （与 export_format 的 Ellipsis 同模式；禁止用 None 表示未传）
_PARSE_STRATEGY_UNSET = object()

# 权威 GET 非法存量固定 code/message；异常不得携带原值
_PARSE_STRATEGY_CORRUPT_CODE = "workspace_parse_strategy_corrupt"
_PARSE_STRATEGY_CORRUPT_MESSAGE = "解析策略配置损坏"


class WorkspaceParseStrategyCorruptError(Exception):
    """
    模块：解析策略配置损坏
    用途：权威 get_parse_strategy 读到非法存量时抛出；固定 code/message。
    对接：GET /api/settings/parse-strategy → 500 detail。
    二次开发：禁止把原 parse_strategy 写入 args/message；禁止 soft fallback light。
    """

    code = _PARSE_STRATEGY_CORRUPT_CODE
    message = _PARSE_STRATEGY_CORRUPT_MESSAGE

    def __init__(self) -> None:
        # 固定中文，不附带原值/字段快照
        super().__init__(self.message)


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


def get_parse_strategy(db: Session, workspace_id: str) -> str:
    """
    模块：解析策略只读查询
    用途：返回工作空间已保存的 parseStrategy；无设置行时返回 DEFAULT_PARSE。
    对接：GET /api/settings/parse-strategy；不得调用 get_or_create_settings，不得 commit。
    二次开发：仅原样精确返回 light|managed|local|ask；禁止 strip/casefold 归一；
              非法存量（含空白/大小写变体）raise 固定损坏异常；
              零 add/flush/commit/refresh；禁止 soft fallback light 与原值回显。
    """
    row = db.get(WorkspaceSettingsRow, workspace_id)
    if row is None:
        return DEFAULT_PARSE
    # 精确四值；禁止 strip/casefold 让「 light 」等变体合法
    value = row.parse_strategy if isinstance(row.parse_strategy, str) else ""
    if value not in ALLOWED_PARSE:
        raise WorkspaceParseStrategyCorruptError()
    return value


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
    parse_strategy: Any = _PARSE_STRATEGY_UNSET,
    embedding_model: str | None = None,
    export_format: dict | Any | None = ...,
) -> WorkspaceSettingsRow:
    """
    用途：部分更新；export_format 用 Ellipsis 表示未传，None 可清空。
    embedding_model：空字符串表示仅用本地哈希向量。
    二次开发：parse_strategy 用模块私有 sentinel 区分「未传」与显式 None；
              显式 None / 非 str / 非精确四值须在 get_or_create/add/flush/commit 前
              固定 raise ValueError("非法 parseStrategy")；未传保持兼容且不改策略；
              合法四值原样写，禁止 strip/casefold。
    """
    # A1+A6：精确四值校验必须先于 get_or_create，避免非法 PUT 先建行再回滚失败
    # 显式 None 不得当未传（JSON null 须 400 零污染）
    if parse_strategy is not _PARSE_STRATEGY_UNSET:
        if (
            not isinstance(parse_strategy, str)
            or parse_strategy not in ALLOWED_PARSE
        ):
            raise ValueError("非法 parseStrategy")
    row = get_or_create_settings(db, workspace_id)
    if provider is not None:
        row.provider = provider.strip() or DEFAULT_PROVIDER
    if api_base_url is not None:
        row.api_base_url = api_base_url.strip().rstrip("/")
    if api_key is not None:
        row.api_key = api_key
    if model is not None:
        row.model = model.strip() or DEFAULT_MODEL
    if parse_strategy is not _PARSE_STRATEGY_UNSET:
        # 已在上方精确校验为合法四值 str；原样写入，禁止 strip/casefold
        row.parse_strategy = parse_strategy
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
