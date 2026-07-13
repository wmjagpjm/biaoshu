"""
模块：P10B 财务只读商务投标报价路由
用途：仅提供两个 GET 端点，返回白名单投影；响应禁止缓存。
对接：/api/finance/business-bids*；deps.require_finance；finance_service。
二次开发：禁止增加写方法、导出或放宽角色；错误码保持 project_not_found / role_forbidden。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import require_finance
from app.api.schemas import (
    FinanceBusinessBidDetailOut,
    FinanceBusinessBidListOut,
    FinanceBusinessBidSummaryOut,
    FinanceQuoteRowOut,
)
from app.core.database import get_db
from app.services import finance_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/finance", tags=["finance"])

_CODE_PROJECT_NOT_FOUND = "project_not_found"
_MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"


def _no_store(response: Response) -> None:
    """用途：财务报价响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


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


@router.get("/business-bids", response_model=FinanceBusinessBidListOut)
def list_business_bids(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_finance)],
) -> FinanceBusinessBidListOut:
    """
    用途：财务角色查看当前工作空间商务标报价列表。
    对接：前端后续财务列表页 GET /api/finance/business-bids。
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
    对接：前端后续财务明细 GET /api/finance/business-bids/{project_id}。
    二次开发：技术标/跨空间/缺失统一 404 project_not_found。
    """
    _no_store(response)
    try:
        item = finance_service.get_business_bid_quote(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": _CODE_PROJECT_NOT_FOUND,
                "message": _MSG_PROJECT_NOT_FOUND,
            },
        ) from None
    return _to_detail(item)
