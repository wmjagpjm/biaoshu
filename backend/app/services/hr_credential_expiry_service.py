"""
模块：P10I 人员资质到期提示服务
用途：按 UTC 自然日与固定 90 天窗口，对当前空间 P10D 启用资质卡做只读到期分类与计数。
对接：api.hr GET /api/hr/credential-expiry；auth_service.record_audit；实体 HrCredentialCardRow。
二次开发：
  - 不新增表、不修改卡片、不读取 remark、不访问 P10F/P10H/项目/文件/外网
  - 生产路由禁止客户端传入 asOf/window；仅测试可显式传 as_of
  - SELECT 仅投影分类所需最小列，不得实例化完整 ORM 行
  - 审计 action/target/result 固定，禁止写入卡 ID、人员、资质、日期、状态、计数或响应体
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import HrCredentialCardRow
from app.services import auth_service

# 固定提示窗口（天）；客户端与调用方均不可覆盖
WINDOW_DAYS = 90

STATE_EXPIRED = "expired"
STATE_EXPIRING_SOON = "expiring_soon"
STATE_VALID = "valid"
STATE_MISSING_EXPIRY = "missing_expiry"

ACTION_READ = "hr_credential_expiry_read"
TARGET_READ = "credential_expiry"

# 关注列表状态排序：expired → expiring_soon → missing_expiry
_ATTENTION_STATE_ORDER = {
    STATE_EXPIRED: 0,
    STATE_EXPIRING_SOON: 1,
    STATE_MISSING_EXPIRY: 2,
}


def utc_as_of_date() -> date:
    """
    用途：生产默认 asOf 取 UTC 自然日。
    对接：get_credential_expiry 未传 as_of 时。
    二次开发：禁止改用服务器本地时区。
    """
    return datetime.now(timezone.utc).date()


def classify_valid_until(
    valid_until: date | None,
    *,
    as_of: date,
) -> tuple[str, int | None]:
    """
    用途：单卡状态与剩余天数；仅服务端日期规则，窗口固定 WINDOW_DAYS。
    对接：get_credential_expiry 分类循环。
    二次开发：
      - 禁止暴露可变 window_days；内部只读 WINDOW_DAYS
      - expired：validUntil < asOf，daysRemaining 为负
      - expiring_soon：asOf..asOf+90 含端点
      - valid：大于 90 天只计数
      - missing_expiry：null，daysRemaining=null
    """
    if valid_until is None:
        return STATE_MISSING_EXPIRY, None
    days_remaining = (valid_until - as_of).days
    if valid_until < as_of:
        return STATE_EXPIRED, days_remaining
    if valid_until <= as_of + timedelta(days=WINDOW_DAYS):
        return STATE_EXPIRING_SOON, days_remaining
    return STATE_VALID, days_remaining


def _attention_sort_key(item: dict[str, Any]) -> tuple[int, date, str]:
    """
    用途：关注项稳定排序键；同组有效期升序，再 cardId。
    对接：get_credential_expiry 排序。
    二次开发：missing 的 valid_until 为 None 时仅靠 cardId 区分。
    """
    state = str(item["state"])
    vu = item.get("valid_until")
    # missing 的 valid_until 为 None：同组仅靠 cardId 区分
    date_key: date = vu if isinstance(vu, date) else date.min
    return (
        _ATTENTION_STATE_ORDER.get(state, 99),
        date_key,
        str(item["card_id"]),
    )


def get_credential_expiry(
    db: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
    as_of: date | None = None,
) -> dict[str, Any]:
    """
    用途：读取当前工作空间资质到期提示摘要并写脱敏审计。
    对接：GET /api/hr/credential-expiry。
    二次开发：
      - 仅最小列投影本空间 hr_credential_cards；不得 select 整行/remark
      - workspace_id 仅 WHERE，不得作为结果投影
      - 停用只计 inactiveExcludedCount；valid 只计数不进 attentionItems
      - as_of 仅测试注入；生产传 None 使用 UTC 自然日
    """
    as_of_date = as_of if as_of is not None else utc_as_of_date()

    # 最小列：禁止 remark/created_by/时间戳/workspace 投影；workspace 仅 WHERE
    stmt = (
        select(
            HrCredentialCardRow.id,
            HrCredentialCardRow.person_name,
            HrCredentialCardRow.category,
            HrCredentialCardRow.credential_name,
            HrCredentialCardRow.level,
            HrCredentialCardRow.valid_until,
            HrCredentialCardRow.is_active,
        )
        .where(HrCredentialCardRow.workspace_id == workspace_id)
    )
    # 行映射/tuple，不实例化完整 ORM 实体
    rows = db.execute(stmt).all()

    active_total = 0
    expired_count = 0
    expiring_soon_count = 0
    valid_count = 0
    missing_expiry_count = 0
    inactive_excluded = 0
    attention: list[dict[str, Any]] = []

    for row in rows:
        if not bool(row.is_active):
            inactive_excluded += 1
            continue
        active_total += 1
        state, days_remaining = classify_valid_until(
            row.valid_until,
            as_of=as_of_date,
        )
        if state == STATE_EXPIRED:
            expired_count += 1
        elif state == STATE_EXPIRING_SOON:
            expiring_soon_count += 1
        elif state == STATE_VALID:
            valid_count += 1
            # valid 只计数，不进入关注列表
            continue
        else:
            missing_expiry_count += 1

        attention.append(
            {
                "card_id": row.id,
                "person_name": row.person_name,
                "category": row.category,
                "credential_name": row.credential_name,
                "level": row.level or "",
                "valid_until": row.valid_until,
                "state": state,
                "days_remaining": days_remaining,
            }
        )

    attention.sort(key=_attention_sort_key)

    # 固定脱敏审计：不得写入卡/人/资质/日期/状态/计数
    auth_service.record_audit(
        db,
        action=ACTION_READ,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=TARGET_READ,
        commit=True,
    )

    return {
        "as_of_date": as_of_date,
        "window_days": WINDOW_DAYS,
        "active_total_count": active_total,
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "valid_count": valid_count,
        "missing_expiry_count": missing_expiry_count,
        "inactive_excluded_count": inactive_excluded,
        "attention_items": attention,
    }
