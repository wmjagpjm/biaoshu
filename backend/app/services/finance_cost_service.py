"""
模块：P10C 财务成本草案服务
用途：在商务标上维护人工成本条目，并基于 P10B 报价合计给出整数分毛利快照。
对接：api.finance 成本路由；finance_service.get_business_bid_quote；auth_service.record_audit。
二次开发：
  - 金额仅人民币分整数；禁止浮点持久化与税务/审批推算
  - 审计 target 仅条目 ID，禁止写金额/名称/备注
  - 不得改动 P10B 只读投影语义
"""

from __future__ import annotations

import math
import secrets
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FinanceCostEntryRow, utc_now
from app.services import auth_service, finance_service
from app.services.project_service import ProjectNotFoundError

# 成本类别固定枚举
ALLOWED_CATEGORIES = frozenset({"labor", "material", "service", "other"})
_AMOUNT_FEN_MIN = 1
_AMOUNT_FEN_MAX = 999_999_999_999
_NAME_MAX = 120
_REMARK_MAX = 500

ACTION_CREATE = "finance_cost_create"
ACTION_UPDATE = "finance_cost_update"
ACTION_DELETE = "finance_cost_delete"


class FinanceCostValidationError(Exception):
    """
    模块：成本草案校验错误
    用途：服务层拒绝非法类别/金额/文本时抛出；路由映射为 422。
    对接：create/update 入口。
    二次开发：message 仅短标签，禁止回显敏感原文大段。
    """

    def __init__(self, message: str = "invalid_cost_entry") -> None:
        self.message = message
        super().__init__(message)


def _new_entry_id() -> str:
    """用途：生成不透明成本条目 ID。"""
    return f"fce_{secrets.token_hex(8)}"


def yuan_to_fen(value: Any) -> int:
    """
    用途：将有限数值报价金额（元）用 Decimal 量化为人民币分。
    对接：get_cost_draft 的 quoteTotalFen。
    二次开发：非 int/float、bool、非有限值一律视为 0，禁止解析字符串/对象。
    """
    if isinstance(value, bool):
        return 0
    if not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    try:
        fen = (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    except Exception:
        return 0
    return int(fen)


def gross_margin_basis_points(quote_total_fen: int, gross_profit_fen: int) -> int | None:
    """
    用途：按整数/Decimal 计算毛利率基点（万分之一）；报价合计<=0 返回 null。
    对接：get_cost_draft.grossMarginBasisPoints。
    二次开发：禁止浮点除法直出；不得从成本反推税务。
    """
    if quote_total_fen <= 0:
        return None
    bp = (
        Decimal(gross_profit_fen) * Decimal(10000) / Decimal(quote_total_fen)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(bp)


def _validate_category(category: str) -> str:
    if category not in ALLOWED_CATEGORIES:
        raise FinanceCostValidationError("invalid_category")
    return category


def _validate_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > _NAME_MAX:
        raise FinanceCostValidationError("invalid_name")
    return text


def _validate_amount_fen(amount_fen: Any) -> int:
    if isinstance(amount_fen, bool) or not isinstance(amount_fen, int):
        raise FinanceCostValidationError("invalid_amount")
    if amount_fen < _AMOUNT_FEN_MIN or amount_fen > _AMOUNT_FEN_MAX:
        raise FinanceCostValidationError("invalid_amount")
    return amount_fen


def _validate_remark(remark: str | None) -> str:
    text = "" if remark is None else str(remark)
    if len(text) > _REMARK_MAX:
        raise FinanceCostValidationError("invalid_remark")
    return text


def _entry_to_dict(row: FinanceCostEntryRow) -> dict[str, Any]:
    """用途：条目白名单投影；不含创建人与工作空间。"""
    return {
        "id": row.id,
        "category": row.category,
        "name": row.name,
        "amount_fen": int(row.amount_fen),
        "remark": row.remark or "",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _require_business_project(db: Session, workspace_id: str, project_id: str):
    """
    用途：校验项目属于当前空间且为商务标；否则统一 ProjectNotFoundError。
    对接：全部成本读写入口。
    """
    # 复用 P10B 读路径：非商务/跨空间/缺失均 ProjectNotFoundError
    return finance_service.get_business_bid_quote(db, workspace_id, project_id)


def list_entries(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> list[FinanceCostEntryRow]:
    """用途：当前项目成本条目，按 updated_at 降序、id 稳定次序。"""
    stmt = (
        select(FinanceCostEntryRow)
        .where(
            FinanceCostEntryRow.workspace_id == workspace_id,
            FinanceCostEntryRow.project_id == project_id,
        )
        .order_by(
            FinanceCostEntryRow.updated_at.desc(),
            FinanceCostEntryRow.id.desc(),
        )
    )
    return list(db.scalars(stmt).all())


def get_cost_draft(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：返回成本草案汇总与条目列表（字段白名单）。
    对接：GET .../cost-draft。
    """
    quote = _require_business_project(db, workspace_id, project_id)
    quote_total_fen = yuan_to_fen(quote.get("quote_total"))
    entries = list_entries(db, workspace_id, project_id)
    cost_total_fen = sum(int(e.amount_fen) for e in entries)
    gross_profit_fen = quote_total_fen - cost_total_fen
    return {
        "project_id": quote["project_id"],
        "project_name": quote["name"],
        "quote_total_fen": quote_total_fen,
        "cost_total_fen": cost_total_fen,
        "gross_profit_fen": gross_profit_fen,
        "gross_margin_basis_points": gross_margin_basis_points(
            quote_total_fen, gross_profit_fen
        ),
        "cost_entries": [_entry_to_dict(e) for e in entries],
    }


def create_entry(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    category: str,
    name: str,
    amount_fen: int,
    remark: str | None = "",
) -> dict[str, Any]:
    """
    用途：新建成本条目并写脱敏审计。
    对接：POST .../cost-entries。
    """
    _require_business_project(db, workspace_id, project_id)
    cat = _validate_category(category)
    nm = _validate_name(name)
    amt = _validate_amount_fen(amount_fen)
    rm = _validate_remark(remark)
    now = utc_now()
    row = FinanceCostEntryRow(
        id=_new_entry_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        category=cat,
        name=nm,
        amount_fen=amt,
        remark=rm,
        created_by_user_id=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    auth_service.record_audit(
        db,
        action=ACTION_CREATE,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=row.id,
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _entry_to_dict(row)


def _get_entry_or_404(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    entry_id: str,
) -> FinanceCostEntryRow:
    """用途：条目须属于当前空间与项目；否则统一 404。"""
    _require_business_project(db, workspace_id, project_id)
    row = db.get(FinanceCostEntryRow, entry_id)
    if (
        row is None
        or row.workspace_id != workspace_id
        or row.project_id != project_id
    ):
        raise ProjectNotFoundError(project_id)
    return row


def update_entry(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    entry_id: str,
    actor_user_id: str,
    category: str | None = None,
    name: str | None = None,
    amount_fen: int | None = None,
    remark: str | None = None,
) -> dict[str, Any]:
    """
    用途：部分更新成本条目；至少一项可改字段。
    对接：PATCH .../cost-entries/{entry_id}。
    """
    if category is None and name is None and amount_fen is None and remark is None:
        raise FinanceCostValidationError("empty_patch")
    row = _get_entry_or_404(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        entry_id=entry_id,
    )
    if category is not None:
        row.category = _validate_category(category)
    if name is not None:
        row.name = _validate_name(name)
    if amount_fen is not None:
        row.amount_fen = _validate_amount_fen(amount_fen)
    if remark is not None:
        row.remark = _validate_remark(remark)
    row.updated_at = utc_now()
    auth_service.record_audit(
        db,
        action=ACTION_UPDATE,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=row.id,
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _entry_to_dict(row)


def delete_entry(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    entry_id: str,
    actor_user_id: str,
) -> None:
    """
    用途：删除当前项目成本条目并写脱敏审计。
    对接：DELETE .../cost-entries/{entry_id}。
    """
    row = _get_entry_or_404(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        entry_id=entry_id,
    )
    target_id = row.id
    db.delete(row)
    auth_service.record_audit(
        db,
        action=ACTION_DELETE,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=target_id,
        commit=False,
    )
    db.commit()
