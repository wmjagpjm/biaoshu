"""
模块：P10B/P10C 财务路由
用途：P10B 两个只读报价 GET；P10C 成本草案读与受控写。
对接：/api/finance/*；deps.require_finance；finance_service；finance_cost_service。
二次开发：禁止放宽角色；P10B 只读语义不得附加成本字段；写操作依赖既有 CSRF。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_finance
from app.api.schemas import (
    FinanceBusinessBidDetailOut,
    FinanceBusinessBidListOut,
    FinanceBusinessBidSummaryOut,
    FinanceCostDraftOut,
    FinanceCostEntryCreate,
    FinanceCostEntryOut,
    FinanceCostEntryUpdate,
    FinanceQuoteRowOut,
)
from app.core.database import get_db
from app.services import finance_cost_service, finance_service
from app.services.finance_cost_service import FinanceCostValidationError
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/finance", tags=["finance"])

_CODE_PROJECT_NOT_FOUND = "project_not_found"
_MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
_CODE_INVALID = "invalid_cost_entry"
_MSG_INVALID = "成本条目参数不合法"


def _no_store(response: Response) -> None:
    """用途：财务响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _actor_user_id(request: Request) -> str:
    """
    用途：从已验证 request.state 读取操作者 user id。
    对接：成本创建人与审计；禁止客户端 body/header 注入。
    """
    principal = getattr(request.state, "auth_principal", None)
    if principal is not None and getattr(principal, "user_id", None):
        return str(principal.user_id)
    db_uid = getattr(request.state, "auth_db_user_id", None)
    if db_uid:
        return str(db_uid)
    raise HTTPException(
        status_code=401,
        detail={"code": "auth_required", "message": "需要登录"},
    )


def _to_summary(item: dict) -> FinanceBusinessBidSummaryOut:
    return FinanceBusinessBidSummaryOut(
        project_id=item["project_id"],
        name=item["name"],
        industry=item["industry"],
        status=item["status"],
        updated_at=item["updated_at"],
        quote_row_count=item["quote_row_count"],
        quote_total=item["quote_total"],
    )


def _to_detail(item: dict) -> FinanceBusinessBidDetailOut:
    rows = [
        FinanceQuoteRowOut(
            id=row["id"],
            name=row["name"],
            unit=row["unit"],
            quantity=row["quantity"],
            unit_price=row["unit_price"],
            amount=row["amount"],
            remark=row["remark"],
        )
        for row in item.get("quote_rows") or []
    ]
    return FinanceBusinessBidDetailOut(
        project_id=item["project_id"],
        name=item["name"],
        industry=item["industry"],
        status=item["status"],
        updated_at=item["updated_at"],
        quote_row_count=item["quote_row_count"],
        quote_total=item["quote_total"],
        quote_rows=rows,
        quote_notes=item.get("quote_notes") or "",
    )


def _to_entry(item: dict) -> FinanceCostEntryOut:
    return FinanceCostEntryOut(
        id=item["id"],
        category=item["category"],
        name=item["name"],
        amount_fen=item["amount_fen"],
        remark=item["remark"],
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _to_draft(item: dict) -> FinanceCostDraftOut:
    return FinanceCostDraftOut(
        project_id=item["project_id"],
        project_name=item["project_name"],
        quote_total_fen=item["quote_total_fen"],
        cost_total_fen=item["cost_total_fen"],
        gross_profit_fen=item["gross_profit_fen"],
        gross_margin_basis_points=item["gross_margin_basis_points"],
        cost_entries=[_to_entry(e) for e in item.get("cost_entries") or []],
    )


def _http_project_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": _CODE_PROJECT_NOT_FOUND,
            "message": _MSG_PROJECT_NOT_FOUND,
        },
    )


def _http_invalid() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": _CODE_INVALID,
            "message": _MSG_INVALID,
        },
    )


@router.get("/business-bids", response_model=FinanceBusinessBidListOut)
def list_business_bids(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceBusinessBidListOut:
    """
    用途：财务角色查看当前工作空间商务标报价列表。
    对接：前端财务列表页 GET /api/finance/business-bids。
    """
    _no_store(response)
    items = finance_service.list_business_bid_quotes(db, workspace_id)
    return FinanceBusinessBidListOut(items=[_to_summary(x) for x in items])


@router.get(
    "/business-bids/{project_id}",
    response_model=FinanceBusinessBidDetailOut,
)
def get_business_bid(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceBusinessBidDetailOut:
    """
    用途：财务角色查看单项目商务标报价明细。
    对接：前端财务明细 GET /api/finance/business-bids/{project_id}。
    二次开发：技术标/跨空间/缺失统一 404 project_not_found。
    """
    _no_store(response)
    try:
        item = finance_service.get_business_bid_quote(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise _http_project_not_found() from None
    return _to_detail(item)


@router.get(
    "/business-bids/{project_id}/cost-draft",
    response_model=FinanceCostDraftOut,
)
def get_cost_draft(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceCostDraftOut:
    """
    用途：读取商务标成本草案与毛利快照。
    对接：GET /api/finance/business-bids/{project_id}/cost-draft。
    二次开发：不得返回报价行、创建人或审计细节。
    """
    _no_store(response)
    try:
        item = finance_cost_service.get_cost_draft(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise _http_project_not_found() from None
    return _to_draft(item)


@router.post(
    "/business-bids/{project_id}/cost-entries",
    response_model=FinanceCostEntryOut,
    status_code=201,
)
def create_cost_entry(
    project_id: str,
    body: FinanceCostEntryCreate,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceCostEntryOut:
    """
    用途：新建成本条目；CSRF 由中间件校验。
    对接：POST /api/finance/business-bids/{project_id}/cost-entries。
    """
    _no_store(response)
    actor = _actor_user_id(request)
    try:
        item = finance_cost_service.create_entry(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor,
            category=body.category,
            name=body.name,
            amount_fen=body.amount_fen,
            remark=body.remark,
        )
    except ProjectNotFoundError:
        raise _http_project_not_found() from None
    except FinanceCostValidationError:
        raise _http_invalid() from None
    return _to_entry(item)


@router.patch(
    "/business-bids/{project_id}/cost-entries/{entry_id}",
    response_model=FinanceCostEntryOut,
)
def update_cost_entry(
    project_id: str,
    entry_id: str,
    body: FinanceCostEntryUpdate,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceCostEntryOut:
    """
    用途：部分更新成本条目；空补丁拒绝。
    对接：PATCH /api/finance/business-bids/{project_id}/cost-entries/{entry_id}。
    """
    _no_store(response)
    actor = _actor_user_id(request)
    # 仅传递客户端实际给出的字段，避免 None 覆盖
    raw = body.model_dump(exclude_unset=True)
    try:
        item = finance_cost_service.update_entry(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            entry_id=entry_id,
            actor_user_id=actor,
            category=raw.get("category"),
            name=raw.get("name"),
            amount_fen=raw.get("amount_fen"),
            remark=raw.get("remark"),
        )
    except ProjectNotFoundError:
        raise _http_project_not_found() from None
    except FinanceCostValidationError:
        raise _http_invalid() from None
    return _to_entry(item)


@router.delete(
    "/business-bids/{project_id}/cost-entries/{entry_id}",
    status_code=204,
)
def delete_cost_entry(
    project_id: str,
    entry_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> None:
    """
    用途：删除当前项目成本条目。
    对接：DELETE /api/finance/business-bids/{project_id}/cost-entries/{entry_id}。
    """
    _no_store(response)
    actor = _actor_user_id(request)
    try:
        finance_cost_service.delete_entry(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            entry_id=entry_id,
            actor_user_id=actor,
        )
    except ProjectNotFoundError:
        raise _http_project_not_found() from None
