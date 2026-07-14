"""
模块：P10K 财务项目成本变更最小事件服务
用途：在 P10C 成功事务内追加项目级不可变事件；按商务标项目只读最近 50 条。
对接：finance_cost_service；api.finance GET
  /api/finance/business-bids/{projectId}/cost-change-events；auth_service.record_audit。
二次开发：
  - 仅 SQL 投影 action/entry_id/actor_user_id/created_at 四列，禁止整实体
  - 三 action、字面 fce_ 非空后缀、无首尾空白、非空 actor 须在 LIMIT 前过滤
  - 响应仅 action/entryId/actorScope/occurredAt；actor 身份不得外泄
  - 禁止从 P10J 审计回填或猜项目；禁止返回金额/正文/成员身份
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    FinanceProjectCostChangeEventRow,
    Project,
    utc_now,
)
from app.services import auth_service
from app.services.project_service import ProjectNotFoundError

ALLOWED_ACTIONS = frozenset({"create", "update", "delete"})
LIMIT = 50
ACTION_READ = "finance_project_cost_change_events_read"
TARGET_READ = "current_project_recent_50"


def _new_event_id() -> str:
    """用途：生成不透明项目事件 ID（fpce_ 前缀）。"""
    return f"fpce_{secrets.token_hex(8)}"


def _is_valid_entry_id(entry_id: str | None) -> bool:
    """
    用途：entry_id 必须原样以字面量 fce_ 开头、后缀非空且无首尾空白。
    对接：list_project_cost_change_events 防御性二次过滤。
    二次开发：不得 strip 归一化后返回。
    """
    if not isinstance(entry_id, str):
        return False
    if entry_id != entry_id.strip():
        return False
    return entry_id.startswith("fce_") and len(entry_id) > 4


def require_business_project_id(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> str:
    """
    用途：以最小 SQL 校验当前空间商务标项目，仅投影 Project.id。
    对接：list_project_cost_change_events 读取前校验。
    二次开发：禁止加载 editor-state、报价正文或成本条目实体。
    """
    stmt = select(Project.id).where(
        Project.id == project_id,
        Project.workspace_id == workspace_id,
        Project.kind == "business",
    )
    found = db.execute(stmt).scalar_one_or_none()
    if found is None:
        raise ProjectNotFoundError(project_id)
    return str(found)


def record_project_cost_change_event(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    entry_id: str,
    action: str,
    actor_user_id: str,
    commit: bool = False,
) -> None:
    """
    用途：在当前事务追加一条项目成本变更事件。
    对接：finance_cost_service create/update/delete 成功路径。
    二次开发：
      - 默认 commit=False，由调用方与业务/审计同事务提交
      - 禁止新增第二次 commit；不得写入业务正文
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError("invalid_project_cost_change_action")
    if not actor_user_id:
        raise ValueError("invalid_actor_user_id")
    if not _is_valid_entry_id(entry_id):
        raise ValueError("invalid_entry_id")
    row = FinanceProjectCostChangeEventRow(
        id=_new_event_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        entry_id=entry_id,
        action=action,
        actor_user_id=actor_user_id,
        created_at=utc_now(),
    )
    db.add(row)
    if commit:
        db.commit()


def list_project_cost_change_events(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """
    用途：读取当前空间商务标项目最近 50 条成功成本变更并写固定脱敏审计。
    对接：GET /api/finance/business-bids/{projectId}/cost-change-events。
    二次开发：
      - 先最小投影校验项目；事件 SELECT 仅四列
      - WHERE 过滤 workspace/project/action/entry/actor，均在 LIMIT 前
      - entry 字面 fce_ + length>4 + trim 相等；actor 非空
      - 排序 created_at DESC, id DESC；固定 LIMIT 50
      - actorScope 仅 self|other；读取审计固定脱敏
    """
    require_business_project_id(db, workspace_id, project_id)

    stmt = (
        select(
            FinanceProjectCostChangeEventRow.action,
            FinanceProjectCostChangeEventRow.entry_id,
            FinanceProjectCostChangeEventRow.actor_user_id,
            FinanceProjectCostChangeEventRow.created_at,
        )
        .where(
            FinanceProjectCostChangeEventRow.workspace_id == workspace_id,
            FinanceProjectCostChangeEventRow.project_id == project_id,
            FinanceProjectCostChangeEventRow.action.in_(tuple(ALLOWED_ACTIONS)),
            FinanceProjectCostChangeEventRow.entry_id.is_not(None),
            # LIKE 中 _ 为通配；escape 后字面 fce_
            FinanceProjectCostChangeEventRow.entry_id.like(r"fce\_%", escape="\\"),
            func.length(FinanceProjectCostChangeEventRow.entry_id) > 4,
            FinanceProjectCostChangeEventRow.entry_id
            == func.trim(FinanceProjectCostChangeEventRow.entry_id),
            FinanceProjectCostChangeEventRow.actor_user_id.is_not(None),
            FinanceProjectCostChangeEventRow.actor_user_id != "",
            func.length(func.trim(FinanceProjectCostChangeEventRow.actor_user_id))
            > 0,
        )
        .order_by(
            FinanceProjectCostChangeEventRow.created_at.desc(),
            FinanceProjectCostChangeEventRow.id.desc(),
        )
        .limit(LIMIT)
    )
    rows = db.execute(stmt).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        action_raw = str(row.action or "")
        entry = row.entry_id
        actor = row.actor_user_id
        if action_raw not in ALLOWED_ACTIONS or not _is_valid_entry_id(entry):
            continue
        if not isinstance(actor, str) or not actor.strip():
            continue
        occurred_at: datetime = row.created_at
        items.append(
            {
                "action": action_raw,
                "entry_id": str(entry),
                "actor_scope": "self" if actor == actor_user_id else "other",
                "occurred_at": occurred_at,
            }
        )

    auth_service.record_audit(
        db,
        action=ACTION_READ,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=TARGET_READ,
        commit=True,
    )

    return {"items": items}
