"""
模块：P10J 财务个人成本变更记录服务
用途：从既有 auth_audit_events 固定投影当前用户在当前工作空间最近 50 条成功成本变更。
对接：api.finance GET /api/finance/cost-change-events；auth_service.record_audit。
二次开发：
  - 仅 SQL 投影 action/target/created_at 三列，禁止整实体与 list_recent_audit_events 后过滤
  - 仅本人+当前空间+success+三类 finance_cost_*+fce_ target；固定 LIMIT 50
  - 响应不得含项目/金额/名称/备注/失败尝试/其他用户；读取审计固定脱敏且不进入列表
  - 不新增表、不改 P10B/P10C 语义
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import AuthAuditEventRow
from app.services import auth_service

# 内部审计 action → 对外固定枚举
_ACTION_MAP: dict[str, str] = {
    "finance_cost_create": "create",
    "finance_cost_update": "update",
    "finance_cost_delete": "delete",
}
_INTERNAL_ACTIONS = tuple(_ACTION_MAP.keys())

LIMIT = 50
ACTION_READ = "finance_cost_change_events_read"
TARGET_READ = "self_recent_50"


def _is_valid_entry_target(target: str | None) -> bool:
    """
    用途：历史行 target 必须原样以字面量 fce_ 开头、后缀非空且无首尾空白。
    对接：list_personal_cost_change_events 二次过滤。
    二次开发：
      - 不得 strip 归一化后返回；带首尾空白或仅 fce_ 的历史行一律排除
      - 契约只要求不透明 fce_*，不强制十六进制固定长度
    """
    if not isinstance(target, str):
        return False
    # 原样判断：禁止 strip 后把「  fce_xxx」「fce_xxx  」洗成合法 ID
    if target != target.strip():
        return False
    return target.startswith("fce_") and len(target) > 4


def list_personal_cost_change_events(
    db: Session,
    *,
    workspace_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """
    用途：读取本人当前工作空间最近 50 条成功成本变更并写固定脱敏审计。
    对接：GET /api/finance/cost-change-events。
    二次开发：
      - SELECT 仅 action/target/created_at；WHERE 过滤 workspace/actor/result/action/target
      - target 在 SQL 层必须字面匹配 fce_（转义 LIKE 下划线），避免 fceX... 占满 LIMIT
      - SQL 须 length(target)>4 排除空后缀 fce_；target==trim(target) 排除首尾空白，均在 LIMIT 前
      - 排序 created_at DESC, id DESC；SQL LIMIT 50，客户端 limit 无效
      - 禁止调用 list_recent_audit_events 后在 Python 过滤
      - 读取审计 action/target 固定，不得记录数量、条目 ID 或响应体
    """
    stmt = (
        select(
            AuthAuditEventRow.action,
            AuthAuditEventRow.target,
            AuthAuditEventRow.created_at,
        )
        .where(
            AuthAuditEventRow.workspace_id == workspace_id,
            AuthAuditEventRow.actor_user_id == actor_user_id,
            AuthAuditEventRow.result == "success",
            AuthAuditEventRow.action.in_(_INTERNAL_ACTIONS),
            AuthAuditEventRow.target.is_not(None),
            # LIKE 中 _ 为单字符通配符；escape 后 fce\_ 表示字面量「fce_」
            # SQLite/PostgreSQL 均支持 ESCAPE，防止 fceXbad 等误命中占满 LIMIT 50
            AuthAuditEventRow.target.like(r"fce\_%", escape="\\"),
            # 空后缀 fce_ 不得进入最近 50 候选（length>4 跨 SQLite/PostgreSQL）
            func.length(AuthAuditEventRow.target) > 4,
            # 首尾空白行不得占 LIMIT；Python 亦校验 target==strip，返回仍用原值
            AuthAuditEventRow.target == func.trim(AuthAuditEventRow.target),
        )
        .order_by(
            AuthAuditEventRow.created_at.desc(),
            AuthAuditEventRow.id.desc(),
        )
        .limit(LIMIT)
    )
    # 行映射/tuple，不实例化完整 AuthAuditEventRow 实体
    rows = db.execute(stmt).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        action_raw = str(row.action or "")
        mapped = _ACTION_MAP.get(action_raw)
        target = row.target
        if mapped is None or not _is_valid_entry_target(target):
            # 历史非法 action/target 直接排除
            continue
        occurred_at: datetime = row.created_at
        items.append(
            {
                "action": mapped,
                # 返回原值，禁止 strip 改写历史 target
                "entry_id": str(target),
                "occurred_at": occurred_at,
            }
        )

    # 固定脱敏读取审计：不得记录数量、条目 ID、时间范围或响应
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
