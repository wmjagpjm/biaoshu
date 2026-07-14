"""
模块：P10D 人员资质素材卡服务
用途：在当前工作空间维护 strict hr 的最小人员资质卡（创建/列表/详情/更新启停，无物理删除）。
对接：api.hr 路由；auth_service.record_audit；实体 HrCredentialCardRow。
二次开发：
  - 禁止身份证号/手机/住址/照片/附件/URL/证件号码字段
  - 审计 target 仅卡片 ID，禁止写姓名/证书/备注/原始请求
  - 跨空间或不存在统一 HrCredentialNotFoundError
"""

from __future__ import annotations

import secrets
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import HrCredentialCardRow, utc_now
from app.services import auth_service

# 资质类别固定枚举
ALLOWED_CATEGORIES = frozenset({"professional", "safety", "performance", "other"})
_PERSON_NAME_MAX = 80
_CREDENTIAL_NAME_MAX = 120
_LEVEL_MAX = 80
_REMARK_MAX = 500

ACTION_CREATE = "hr_credential_create"
ACTION_UPDATE = "hr_credential_update"

CODE_NOT_FOUND = "hr_credential_not_found"
MSG_NOT_FOUND = "人员资质卡不存在或不可访问"


class HrCredentialNotFoundError(Exception):
    """
    模块：人员资质卡未找到
    用途：跨空间、伪造 id、不存在卡统一抛出；路由映射 404 hr_credential_not_found。
    对接：get_card / update_card。
    二次开发：禁止区分「不存在」与「跨空间」以防止 id 探测。
    """

    def __init__(self, card_id: str = "") -> None:
        self.card_id = card_id
        super().__init__(CODE_NOT_FOUND)


class HrCredentialValidationError(Exception):
    """
    模块：人员资质卡校验错误
    用途：服务层拒绝非法类别/长度/日期/空补丁时抛出；路由映射 422。
    对接：create_card / update_card。
    二次开发：message 仅短标签，禁止回显敏感原文大段。
    """

    def __init__(self, message: str = "invalid_hr_credential") -> None:
        self.message = message
        super().__init__(message)


def _new_card_id() -> str:
    """用途：生成不透明人员资质卡 ID（hcc_ 前缀，同类 fce 风格）。"""
    return f"hcc_{secrets.token_hex(8)}"


def _validate_category(category: str) -> str:
    if category not in ALLOWED_CATEGORIES:
        raise HrCredentialValidationError("invalid_category")
    return category


def _validate_person_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > _PERSON_NAME_MAX:
        raise HrCredentialValidationError("invalid_person_name")
    return text


def _validate_credential_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > _CREDENTIAL_NAME_MAX:
        raise HrCredentialValidationError("invalid_credential_name")
    return text


def _validate_level(level: str | None) -> str:
    if level is None:
        return ""
    text = str(level)
    if len(text) > _LEVEL_MAX:
        raise HrCredentialValidationError("invalid_level")
    return text


def _validate_remark(remark: str | None) -> str:
    text = "" if remark is None else str(remark)
    if len(text) > _REMARK_MAX:
        raise HrCredentialValidationError("invalid_remark")
    return text


def _validate_valid_until(value: date | None) -> date | None:
    """用途：可空 ISO 日期；不做过期提醒或自动判定。"""
    if value is None:
        return None
    # datetime 是 date 子类，必须先排除
    if isinstance(value, datetime) or type(value) is not date:
        raise HrCredentialValidationError("invalid_valid_until")
    return value


def _card_to_dict(row: HrCredentialCardRow, *, include_remark: bool) -> dict[str, Any]:
    """
    用途：卡片白名单投影；列表不含 remark，详情/写响应可含。
    对接：list_cards / get_card / create / update。
    """
    out: dict[str, Any] = {
        "id": row.id,
        "person_name": row.person_name,
        "category": row.category,
        "credential_name": row.credential_name,
        "level": row.level or "",
        "valid_until": row.valid_until,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if include_remark:
        out["remark"] = row.remark or ""
    return out


def list_cards(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    """
    用途：当前工作空间素材卡摘要列表（不含 remark）。
    对接：GET /api/hr/credential-cards。
    """
    stmt = (
        select(HrCredentialCardRow)
        .where(HrCredentialCardRow.workspace_id == workspace_id)
        .order_by(
            HrCredentialCardRow.updated_at.desc(),
            HrCredentialCardRow.id.desc(),
        )
    )
    rows = list(db.scalars(stmt).all())
    return [_card_to_dict(r, include_remark=False) for r in rows]


def _get_row_or_404(
    db: Session,
    *,
    workspace_id: str,
    card_id: str,
) -> HrCredentialCardRow:
    """用途：卡片须属于当前空间；否则统一 not found。"""
    row = db.get(HrCredentialCardRow, card_id)
    if row is None or row.workspace_id != workspace_id:
        raise HrCredentialNotFoundError(card_id)
    return row


def get_card(
    db: Session,
    workspace_id: str,
    card_id: str,
) -> dict[str, Any]:
    """
    用途：读取单卡详情（含 remark）。
    对接：GET /api/hr/credential-cards/{cardId}。
    """
    row = _get_row_or_404(db, workspace_id=workspace_id, card_id=card_id)
    return _card_to_dict(row, include_remark=True)


def create_card(
    db: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
    person_name: str,
    category: str,
    credential_name: str,
    level: str | None = "",
    valid_until: date | None = None,
    remark: str | None = "",
    is_active: bool = True,
) -> dict[str, Any]:
    """
    用途：新建人员资质卡并写脱敏审计。
    对接：POST /api/hr/credential-cards。
    """
    pn = _validate_person_name(person_name)
    cat = _validate_category(category)
    cn = _validate_credential_name(credential_name)
    lv = _validate_level(level)
    vu = _validate_valid_until(valid_until)
    rm = _validate_remark(remark)
    now = utc_now()
    row = HrCredentialCardRow(
        id=_new_card_id(),
        workspace_id=workspace_id,
        person_name=pn,
        category=cat,
        credential_name=cn,
        level=lv,
        valid_until=vu,
        remark=rm,
        is_active=bool(is_active),
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
    return _card_to_dict(row, include_remark=True)


def update_card(
    db: Session,
    *,
    workspace_id: str,
    card_id: str,
    actor_user_id: str,
    person_name: str | None = None,
    category: str | None = None,
    credential_name: str | None = None,
    level: str | None = None,
    valid_until: date | None | object = ...,
    remark: str | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    """
    用途：部分更新资质卡（含启停）；至少一项可改字段。
    对接：PATCH /api/hr/credential-cards/{cardId}。
    二次开发：valid_until 用省略号区分「未传」与「显式 null」。
    """
    # 判断是否有任意可改字段（valid_until 用 ... 表示未设置）
    has_any = any(
        v is not None
        for v in (
            person_name,
            category,
            credential_name,
            level,
            remark,
            is_active,
        )
    )
    if valid_until is not ...:
        has_any = True
    if not has_any:
        raise HrCredentialValidationError("empty_patch")

    row = _get_row_or_404(db, workspace_id=workspace_id, card_id=card_id)
    if person_name is not None:
        row.person_name = _validate_person_name(person_name)
    if category is not None:
        row.category = _validate_category(category)
    if credential_name is not None:
        row.credential_name = _validate_credential_name(credential_name)
    if level is not None:
        row.level = _validate_level(level)
    if valid_until is not ...:
        # 显式 null 清空；否则须为纯 date
        if valid_until is None:
            row.valid_until = None
        else:
            row.valid_until = _validate_valid_until(valid_until)  # type: ignore[arg-type]
    if remark is not None:
        row.remark = _validate_remark(remark)
    if is_active is not None:
        row.is_active = bool(is_active)
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
    return _card_to_dict(row, include_remark=True)
