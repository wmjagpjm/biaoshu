"""
模块：P10E 投标人匿名合规预览服务
用途：聚合当前工作空间技术标的收敛响应矩阵状态，仅输出匿名计数与覆盖率基点。
对接：api.bidder；project_service.list_projects；editor_state_service.get_editor_state；auth_service.record_audit。
二次开发：
  - 仅 kind=technical；禁止返回项目/原文/sourceKey/章节大纲
  - 覆盖率分母=covered+uncovered，豁免不入分母；半入整数基点
  - 成功读取审计 target 固定 anonymous_aggregate，禁止写计数或矩阵内容
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services import auth_service, editor_state_service, project_service

# 成功读取审计：固定 action/result/target，禁止附带业务计数
ACTION_PREVIEW_READ = "bidder_compliance_preview_read"
AUDIT_TARGET = "anonymous_aggregate"


def _coverage_basis_points(covered: int, uncovered: int) -> int | None:
    """
    用途：按 covered/(covered+uncovered)*10000 计算整数基点；分母为 0 返回 None。
    对接：summary.coverageBasisPoints。
    二次开发：必须使用半入（ROUND_HALF_UP），禁止银行家舍入。
    """
    denom = covered + uncovered
    if denom <= 0:
        return None
    raw = (Decimal(covered) * Decimal(10000)) / Decimal(denom)
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _count_matrix_rows(matrix: Any) -> tuple[int, int, int, int]:
    """
    用途：对已收敛矩阵按 status 计数。
    返回：(total, covered, uncovered, waived)。
    """
    if not isinstance(matrix, list):
        return 0, 0, 0, 0
    covered = 0
    uncovered = 0
    waived = 0
    total = 0
    for item in matrix:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip()
        if status == "covered":
            covered += 1
            total += 1
        elif status == "waived":
            waived += 1
            total += 1
        elif status == "uncovered":
            uncovered += 1
            total += 1
        else:
            # 未知状态按未覆盖计入，保持 total 完整
            uncovered += 1
            total += 1
    return total, covered, uncovered, waived


def get_anonymous_compliance_preview(
    db: Session,
    workspace_id: str,
    *,
    actor_user_id: str | None,
) -> dict[str, Any]:
    """
    模块：P10E 投标人匿名合规预览聚合
    用途：返回当前空间技术标响应矩阵的匿名合规汇总。
    对接：GET /api/bidder/compliance-preview；project_service.list_projects；editor_state_service.get_editor_state；auth_service.record_audit。
    二次开发：
      - 必须经 list_projects(kind=technical) 与 get_editor_state 的收敛矩阵
      - 响应仅 dataState + summary 五计数；成功后写脱敏审计 target=anonymous_aggregate
      - 禁止返回项目 ID/名称/原文/sourceKey/章节大纲
    """
    projects = project_service.list_projects(db, workspace_id, kind="technical")
    total = 0
    covered = 0
    uncovered = 0
    waived = 0
    for project in projects:
        state = editor_state_service.get_editor_state(db, workspace_id, project.id)
        # get_editor_state 已 reconcile_response_matrix；此处只聚合 status
        t, c, u, w = _count_matrix_rows(state.get("responseMatrix"))
        total += t
        covered += c
        uncovered += u
        waived += w

    if total <= 0:
        data_state = "empty"
        summary = {
            "total_items": 0,
            "covered_items": 0,
            "uncovered_items": 0,
            "waived_items": 0,
            "coverage_basis_points": None,
        }
    else:
        data_state = "ready"
        summary = {
            "total_items": total,
            "covered_items": covered,
            "uncovered_items": uncovered,
            "waived_items": waived,
            "coverage_basis_points": _coverage_basis_points(covered, uncovered),
        }

    auth_service.record_audit(
        db,
        action=ACTION_PREVIEW_READ,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=AUDIT_TARGET,
        commit=True,
    )

    return {
        "data_state": data_state,
        "summary": summary,
    }
