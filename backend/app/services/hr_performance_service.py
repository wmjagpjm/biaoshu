"""
模块：P10H 人员业绩素材卡服务
用途：在当前工作空间维护 strict hr 的最小人员业绩卡（创建/列表/详情/更新启停，无物理删除）。
对接：api.hr 路由；auth_service.record_audit；实体 HrPerformanceCardRow。
二次开发：
  - 禁止身份证号/手机/住址/照片/附件/URL/金额/简历全文字段
  - 审计 target 仅卡片 ID，禁止写姓名/项目/角色/年份/摘要/备注/原始请求
  - 跨空间或不存在统一 HrPerformanceNotFoundError
  - 列表不含 performanceSummary/remark；详情与写响应才返回
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import HrPerformanceCardRow, utc_now
from app.services import auth_service

_PERSON_NAME_MAX = 80
_PROJECT_NAME_MAX = 120
_PROJECT_ROLE_MAX = 80
_SUMMARY_MAX = 1000
_REMARK_MAX = 500
_YEAR_MIN = 1900
_YEAR_MAX = 2100

ACTION_CREATE = "hr_performance_create"
ACTION_UPDATE = "hr_performance_update"

CODE_NOT_FOUND = "hr_performance_not_found"
MSG_NOT_FOUND = "人员业绩卡不存在或不可访问"


class HrPerformanceNotFoundError(Exception):
    """
    模块：人员业绩卡未找到
    用途：跨空间、伪造 id、不存在卡统一抛出；路由映射 404 hr_performance_not_found。
    对接：get_card / update_card。
    二次开发：禁止区分「不存在」与「跨空间」以防止 id 探测；异常消息不得回显 id。
    """

    def __init__(self) -> None:
        super().__init__(CODE_NOT_FOUND)


class HrPerformanceValidationError(Exception):
    """
    模块：人员业绩卡校验错误
    用途：服务层拒绝非法长度/年份/空补丁时抛出；路由映射 422。
    对接：create_card / update_card。
    二次开发：message 仅短标签，禁止回显敏感原文大段。
    """

    def __init__(self, message: str = "invalid_hr_performance") -> None:
        self.message = message
        super().__init__(message)


def _new_card_id() -> str:
    """用途：生成不透明人员业绩卡 ID（hpc_ 前缀）。"""
    return f"hpc_{secrets.token_hex(8)}"


def _validate_person_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > _PERSON_NAME_MAX:
        raise HrPerformanceValidationError("invalid_person_name")
    return text


def _validate_project_name(name: str) -> str:
    text = (name or "").strip()
    if not text or len(text) > _PROJECT_NAME_MAX:
        raise HrPerformanceValidationError("invalid_project_name")
    return text


def _validate_project_role(role: str | None) -> str:
    if role is None:
        return ""
    text = str(role)
    if len(text) > _PROJECT_ROLE_MAX:
        raise HrPerformanceValidationError("invalid_project_role")
    return text


def _validate_completed_year(value: int | None) -> int | None:
    """用途：可空严格整数年份；范围 1900–2100。"""
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool):
        raise HrPerformanceValidationError("invalid_completed_year")
    if value < _YEAR_MIN or value > _YEAR_MAX:
        raise HrPerformanceValidationError("invalid_completed_year")
    return value


def _validate_performance_summary(summary: str) -> str:
    text = (summary or "").strip() if summary is not None else ""
    # 业绩摘要允许保留两端空格外的原文长度约束；空内容非法
    if summary is None:
        raise HrPerformanceValidationError("invalid_performance_summary")
    text = str(summary)
    if not text.strip() or len(text) > _SUMMARY_MAX:
        raise HrPerformanceValidationError("invalid_performance_summary")
    # 与 P10D 不同：这里按契约 1–1000，采用 strip 后内容存储更稳妥
    stripped = text.strip()
    if not stripped or len(stripped) > _SUMMARY_MAX:
        raise HrPerformanceValidationError("invalid_performance_summary")
    return stripped


def _validate_remark(remark: str | None) -> str:
    text = "" if remark is None else str(remark)
    if len(text) > _REMARK_MAX:
        raise HrPerformanceValidationError("invalid_remark")
    return text


def _card_to_dict(row: HrPerformanceCardRow, *, include_detail: bool) -> dict[str, Any]:
    """
    用途：卡片白名单投影；列表不含 performanceSummary/remark。
    对接：list_cards / get_card / create / update。
    """
    out: dict[str, Any] = {
        "id": row.id,
        "person_name": row.person_name,
        "project_name": row.project_name,
        "project_role": row.project_role or "",
        "completed_year": row.completed_year,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if include_detail:
        out["performance_summary"] = row.performance_summary or ""
        out["remark"] = row.remark or ""
    return out


def list_cards(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    """
    用途：当前工作空间业绩卡摘要列表（不含 performanceSummary/remark）。
    对接：GET /api/hr/performance-cards。
    """
    stmt = (
        select(HrPerformanceCardRow)
        .where(HrPerformanceCardRow.workspace_id == workspace_id)
        .order_by(
            HrPerformanceCardRow.updated_at.desc(),
            HrPerformanceCardRow.id.desc(),
        )
    )
    rows = list(db.scalars(stmt).all())
    return [_card_to_dict(r, include_detail=False) for r in rows]


def _get_row_or_404(
    db: Session,
    *,
    workspace_id: str,
    card_id: str,
) -> HrPerformanceCardRow:
    """用途：卡片须属于当前空间；否则统一 not found。"""
    row = db.get(HrPerformanceCardRow, card_id)
    if row is None or row.workspace_id != workspace_id:
        raise HrPerformanceNotFoundError()
    return row


def get_card(
    db: Session,
    workspace_id: str,
    card_id: str,
) -> dict[str, Any]:
    """
    用途：读取单卡详情（含 performanceSummary 与 remark）。
    对接：GET /api/hr/performance-cards/{cardId}。
    """
    row = _get_row_or_404(db, workspace_id=workspace_id, card_id=card_id)
    return _card_to_dict(row, include_detail=True)


def create_card(
    db: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
    person_name: str,
    project_name: str,
    project_role: str | None = "",
    completed_year: int | None = None,
    performance_summary: str,
    remark: str | None = "",
    is_active: bool = True,
) -> dict[str, Any]:
    """
    用途：新建人员业绩卡并写脱敏审计。
    对接：POST /api/hr/performance-cards。
    """
    pn = _validate_person_name(person_name)
    proj = _validate_project_name(project_name)
    role = _validate_project_role(project_role)
    year = _validate_completed_year(completed_year)
    summary = _validate_performance_summary(performance_summary)
    rm = _validate_remark(remark)
    now = utc_now()
    row = HrPerformanceCardRow(
        id=_new_card_id(),
        workspace_id=workspace_id,
        person_name=pn,
        project_name=proj,
        project_role=role,
        completed_year=year,
        performance_summary=summary,
        remark=rm,
        is_active=bool(is_active),
        created_by_user_id=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    # 审计仅 target=hpc_*；业务字段不得写入 action/result/target
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
    return _card_to_dict(row, include_detail=True)


def update_card(
    db: Session,
    *,
    workspace_id: str,
    card_id: str,
    actor_user_id: str,
    person_name: str | None = None,
    project_name: str | None = None,
    project_role: str | None = None,
    completed_year: int | None | object = ...,
    performance_summary: str | None = None,
    remark: str | None = None,
    is_active: bool | None = None,
) -> dict[str, Any]:
    """
    用途：部分更新业绩卡（含启停）；至少一项可改字段。
    对接：PATCH /api/hr/performance-cards/{cardId}。
    二次开发：completed_year 用省略号区分「未传」与「显式 null」。
    """
    has_any = any(
        v is not None
        for v in (
            person_name,
            project_name,
            project_role,
            performance_summary,
            remark,
            is_active,
        )
    )
    if completed_year is not ...:
        has_any = True
    if not has_any:
        raise HrPerformanceValidationError("empty_patch")

    row = _get_row_or_404(db, workspace_id=workspace_id, card_id=card_id)
    if person_name is not None:
        row.person_name = _validate_person_name(person_name)
    if project_name is not None:
        row.project_name = _validate_project_name(project_name)
    if project_role is not None:
        row.project_role = _validate_project_role(project_role)
    if completed_year is not ...:
        if completed_year is None:
            row.completed_year = None
        else:
            row.completed_year = _validate_completed_year(completed_year)  # type: ignore[arg-type]
    if performance_summary is not None:
        row.performance_summary = _validate_performance_summary(performance_summary)
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
    return _card_to_dict(row, include_detail=True)
