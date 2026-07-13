"""
模块：P10B 财务只读商务投标报价服务
用途：从商务标项目与编辑器状态投影白名单报价视图；不透传 business_json。
对接：api.finance；project_service.list_projects/get_project；editor_state_service.get_editor_state。
二次开发：
  - 仅 kind=business；跨空间/技术标/缺失统一 ProjectNotFoundError → 路由 404
  - 金额合计只累加有限数值 amount；禁止解析字符串或嵌套对象金额
  - 禁止在此实现写入、导出、成本利润推算
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.services import editor_state_service, project_service
from app.services.project_service import ProjectNotFoundError


def _safe_text(value: Any) -> str:
    """用途：将任意值收敛为展示用字符串；对象/None 不直出。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value)


def _safe_amount(value: Any) -> float | None:
    """
    用途：仅接受有限 int/float 作为金额；拒绝 bool、NaN、Inf、字符串、嵌套对象。
    对接：quoteRows.amount 与 quoteTotal 累加。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return float(value)
        return None
    return None


def _project_quote(quote_raw: Any) -> tuple[list[dict[str, Any]], str, float]:
    """
    用途：从 businessQuote 构造白名单行与合计。
    返回：(rows, notes, total)。
    """
    rows_out: list[dict[str, Any]] = []
    notes = ""
    total = 0.0
    if not isinstance(quote_raw, dict):
        return rows_out, notes, total

    notes = _safe_text(quote_raw.get("notes"))
    raw_rows = quote_raw.get("rows")
    if not isinstance(raw_rows, list):
        return rows_out, notes, total

    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        amount = _safe_amount(raw.get("amount"))
        if amount is not None:
            total += amount
        rows_out.append(
            {
                "id": _safe_text(raw.get("id")) or f"r{index + 1}",
                "name": _safe_text(raw.get("name")),
                "unit": _safe_text(raw.get("unit")),
                "quantity": _safe_text(raw.get("quantity")),
                "unit_price": _safe_text(
                    raw.get("unitPrice")
                    if "unitPrice" in raw
                    else raw.get("unit_price")
                ),
                "amount": amount,
                "remark": _safe_text(raw.get("remark")),
            }
        )
    return rows_out, notes, total


def _summary_from_project(
    project,
    *,
    quote_row_count: int,
    quote_total: float,
) -> dict[str, Any]:
    """用途：项目实体 + 报价统计 → 列表/明细共用摘要字典。"""
    return {
        "project_id": project.id,
        "name": project.name,
        "industry": project.industry,
        "status": project.status,
        "updated_at": project.updated_at,
        "quote_row_count": quote_row_count,
        "quote_total": quote_total,
    }


def list_business_bid_quotes(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    """
    用途：列出当前工作空间全部商务标的财务报价摘要。
    对接：GET /api/finance/business-bids。
    二次开发：必须经 list_projects(kind=business)；禁止扫技术标或其它空间。
    """
    projects = project_service.list_projects(db, workspace_id, kind="business")
    items: list[dict[str, Any]] = []
    for project in projects:
        state = editor_state_service.get_editor_state(db, workspace_id, project.id)
        rows, _notes, total = _project_quote(state.get("businessQuote"))
        items.append(
            _summary_from_project(
                project,
                quote_row_count=len(rows),
                quote_total=total,
            )
        )
    return items


def get_business_bid_quote(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：返回单项目财务报价明细；非本空间/非商务标/不存在一律 ProjectNotFoundError。
    对接：GET /api/finance/business-bids/{project_id}。
    二次开发：先查项目再校验 kind，错误不得区分存在性。
    """
    try:
        project = project_service.get_project(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise ProjectNotFoundError(project_id) from None

    if project.kind != "business":
        raise ProjectNotFoundError(project_id)

    state = editor_state_service.get_editor_state(db, workspace_id, project.id)
    rows, notes, total = _project_quote(state.get("businessQuote"))
    summary = _summary_from_project(
        project,
        quote_row_count=len(rows),
        quote_total=total,
    )
    summary["quote_rows"] = rows
    summary["quote_notes"] = notes
    return summary
