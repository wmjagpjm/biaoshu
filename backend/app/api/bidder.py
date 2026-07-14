"""
模块：P10E/P10G 投标人合规预览路由
用途：严格 bidder 只读聚合与项目级合规统计；固定 Cache-Control: no-store。
对接：/api/bidder/*；deps.require_bidder；bidder_compliance_preview_service；
  bidder_project_compliance_service。
二次开发：禁止写接口与放宽角色；P10G 静态 /projects 必须先于 {projectId} 注册；
  响应不得附带矩阵原文；详情禁止回显路径参数。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_bidder
from app.api.schemas import (
    BidderCompliancePreviewOut,
    BidderComplianceSummaryOut,
    BidderProjectComplianceDetailOut,
    BidderProjectComplianceSelectorItemOut,
    BidderProjectComplianceSelectorListOut,
)
from app.core.database import get_db
from app.services import (
    bidder_compliance_preview_service,
    bidder_project_compliance_service,
)
from app.services.bidder_project_compliance_service import (
    CODE_NOT_FOUND as P10G_CODE_NOT_FOUND,
    MSG_NOT_FOUND as P10G_MSG_NOT_FOUND,
    BidderProjectComplianceNotFoundError,
)

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


def _http_p10g_not_found() -> HTTPException:
    """用途：P10G 统一 404；禁止区分跨空间/商务标/伪造 ID。"""
    return HTTPException(
        status_code=404,
        detail={"code": P10G_CODE_NOT_FOUND, "message": P10G_MSG_NOT_FOUND},
    )


def _to_summary(summary: dict) -> BidderComplianceSummaryOut:
    """用途：服务层 snake 摘要 → 响应 schema。"""
    return BidderComplianceSummaryOut(
        total_items=summary["total_items"],
        covered_items=summary["covered_items"],
        uncovered_items=summary["uncovered_items"],
        waived_items=summary["waived_items"],
        coverage_basis_points=summary["coverage_basis_points"],
    )


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
    return BidderCompliancePreviewOut(
        data_state=data["data_state"],
        summary=_to_summary(data["summary"]),
    )


@router.get(
    "/project-compliance/projects",
    response_model=BidderProjectComplianceSelectorListOut,
)
def list_project_compliance_projects(
    response: Response,
    workspace_id: Annotated[str, Depends(require_bidder)],
    db: Annotated[Session, Depends(get_db)],
) -> BidderProjectComplianceSelectorListOut:
    """
    模块：P10G 投标人项目合规选择器
    用途：返回当前空间技术标项目最小 id/name 列表。
    对接：GET /api/bidder/project-compliance/projects；require_bidder；list_technical_projects_for_selector。
    二次开发：
      - 静态路径必须先于 {projectId} 注册；仅 GET；no-store
      - 不写审计；禁止调用 /api/projects* 或扩展字段
    """
    _no_store(response)
    items = bidder_project_compliance_service.list_technical_projects_for_selector(
        db, workspace_id
    )
    return BidderProjectComplianceSelectorListOut(
        items=[
            BidderProjectComplianceSelectorItemOut(id=x["id"], name=x["name"])
            for x in items
        ]
    )


@router.get(
    "/project-compliance/{project_id}",
    response_model=BidderProjectComplianceDetailOut,
)
def get_project_compliance_detail(
    project_id: str,
    request: Request,
    response: Response,
    workspace_id: Annotated[str, Depends(require_bidder)],
    db: Annotated[Session, Depends(get_db)],
) -> BidderProjectComplianceDetailOut:
    """
    模块：P10G 投标人单项目合规统计
    用途：返回指定技术标项目的 dataState 与五项 summary。
    对接：GET /api/bidder/project-compliance/{projectId}；get_project_compliance。
    二次开发：
      - 跨空间/不存在/商务标统一 404 bidder_project_compliance_not_found
      - empty 为 200；响应不得回显路径参数或项目字段；no-store；仅成功读写脱敏审计
    """
    _no_store(response)
    try:
        data = bidder_project_compliance_service.get_project_compliance(
            db,
            workspace_id,
            project_id,
            actor_user_id=_actor_user_id(request),
        )
    except BidderProjectComplianceNotFoundError:
        raise _http_p10g_not_found() from None
    return BidderProjectComplianceDetailOut(
        data_state=data["data_state"],
        summary=_to_summary(data["summary"]),
    )
