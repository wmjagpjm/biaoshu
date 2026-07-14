"""
模块：P10E 投标人匿名合规预览路由
用途：严格 bidder 只读聚合 GET；固定 Cache-Control: no-store。
对接：/api/bidder/compliance-preview；deps.require_bidder；bidder_compliance_preview_service。
二次开发：禁止写接口、项目参数与放宽角色；不得在响应中附带项目或矩阵原文。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_bidder
from app.api.schemas import (
    BidderCompliancePreviewOut,
    BidderComplianceSummaryOut,
)
from app.core.database import get_db
from app.services import bidder_compliance_preview_service

router = APIRouter(prefix="/bidder", tags=["bidder"])


def _no_store(response: Response) -> None:
    """用途：投标人预览响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _actor_user_id(request: Request) -> str | None:
    """
    用途：从已验证 request.state 读取操作者 user id，供审计。
    对接：record_audit.actor_user_id；禁止客户端 body/header 注入。
    """
    principal = getattr(request.state, "auth_principal", None)
    if principal is not None and getattr(principal, "user_id", None):
        return str(principal.user_id)
    db_uid = getattr(request.state, "auth_db_user_id", None)
    if db_uid:
        return str(db_uid)
    return None


@router.get(
    "/compliance-preview",
    response_model=BidderCompliancePreviewOut,
)
def get_compliance_preview(
    request: Request,
    response: Response,
    workspace_id: Annotated[str, Depends(require_bidder)],
    db: Annotated[Session, Depends(get_db)],
) -> BidderCompliancePreviewOut:
    """
    模块：P10E 投标人匿名合规预览读接口
    用途：返回当前工作空间匿名合规预览。
    对接：前端 /bidder 页；服务层 get_anonymous_compliance_preview；依赖 require_bidder。
    二次开发：仅 GET；固定 Cache-Control: no-store；响应字段由 Pydantic 白名单锁定，禁止附带项目/矩阵原文。
    """
    _no_store(response)
    data = bidder_compliance_preview_service.get_anonymous_compliance_preview(
        db,
        workspace_id,
        actor_user_id=_actor_user_id(request),
    )
    summary = data["summary"]
    return BidderCompliancePreviewOut(
        data_state=data["data_state"],
        summary=BidderComplianceSummaryOut(
            total_items=summary["total_items"],
            covered_items=summary["covered_items"],
            uncovered_items=summary["uncovered_items"],
            waived_items=summary["waived_items"],
            coverage_basis_points=summary["coverage_basis_points"],
        ),
    )
