"""
模块：国能 e 招计划追踪服务
用途：提供工作空间隔离的计划/运行/命中读取，并在应用重启后收敛中断的同步运行。
对接：app.main.lifespan；后续 opportunity_watch 路由、Excel 导入和受控同步任务。
二次开发：不得在此混入任意 URL、Cookie、HTML、JSON 或浏览器请求；外部访问仅能由固定来源客户端承担。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import BidSourceHitRow, BidSourceSyncRunRow, BidWatchPlanRow


def _now() -> datetime:
    """用途：统一同步运行恢复写入的 UTC 时间源。"""
    return datetime.now(timezone.utc)


def list_watch_plans(db: Session, workspace_id: str) -> list[BidWatchPlanRow]:
    """
    模块：计划追踪列表查询
    用途：只读取当前工作空间的计划追踪记录，避免服务层遗漏归属过滤。
    对接：BidWatchPlanRow；OpportunityWatchPlanOut；当前尚无公开 HTTP 路由。
    二次开发：调用方必须传入已校验的 workspace_id；禁止省略 where 过滤或跨工作空间查询。
    """
    stmt = (
        select(BidWatchPlanRow)
        .where(BidWatchPlanRow.workspace_id == workspace_id)
        .order_by(BidWatchPlanRow.updated_at.desc(), BidWatchPlanRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def list_watch_runs(db: Session, workspace_id: str) -> list[BidSourceSyncRunRow]:
    """
    模块：同步运行列表查询
    用途：只读取当前工作空间的同步运行记录，供后续运行状态和审计展示。
    对接：BidSourceSyncRunRow；OpportunityWatchSyncRunOut；当前尚无公开 HTTP 路由。
    二次开发：调用方必须传入已校验的 workspace_id；禁止跨工作空间查询或回传错误原文。
    """
    stmt = (
        select(BidSourceSyncRunRow)
        .where(BidSourceSyncRunRow.workspace_id == workspace_id)
        .order_by(BidSourceSyncRunRow.started_at.desc(), BidSourceSyncRunRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def list_watch_hits(db: Session, workspace_id: str) -> list[BidSourceHitRow]:
    """
    模块：公告命中列表查询
    用途：只读取当前工作空间的公告命中，绝不跨工作空间返回追踪结果。
    对接：BidSourceHitRow；OpportunityWatchHitOut；当前尚无公开 HTTP 路由。
    二次开发：调用方必须传入已校验的 workspace_id；不得返回其它 workspace 命中或外部正文。
    """
    stmt = (
        select(BidSourceHitRow)
        .where(BidSourceHitRow.workspace_id == workspace_id)
        .order_by(BidSourceHitRow.updated_at.desc(), BidSourceHitRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def mark_interrupted_watch_runs(db: Session) -> int:
    """
    模块：中断同步运行收敛
    用途：将进程重启前未结束的 queued/running 同步运行标为 failed/interrupted。
    对接：app.main.lifespan；仅修改运行状态，命中和本地标讯均保留。
    二次开发：不得删除命中、计划或 bid_opportunities；新增运行状态时同步扩展过滤条件与测试。
    """
    now = _now()
    result = db.execute(
        update(BidSourceSyncRunRow)
        .where(BidSourceSyncRunRow.status.in_(("queued", "running")))
        .values(
            status="failed",
            error_code="interrupted",
            finished_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return int(result.rowcount or 0)
