"""
模块：P10G 投标人项目级合规统计服务
用途：为严格 bidder 提供技术标项目选择器与单项目响应矩阵统计投影。
对接：api.bidder 的 project-compliance 路由；editor_state_service；auth_service.record_audit。
二次开发：
  - 仅当前 workspace 且 kind=technical；跨空间/不存在/商务标统一 404
  - 选择器仅 id/name；详情仅 dataState + 五项 summary，禁止矩阵/原文/项目字段
  - 计数与基点口径必须与 P10E 一致；选择器不审计；详情审计 target 固定 project_compliance
  - 禁止新建表/任务/缓存/外网；禁止复用或暴露 /api/projects*
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Project
from app.services import auth_service, editor_state_service
from app.services.bidder_compliance_preview_service import (
    _count_matrix_rows,
    _coverage_basis_points,
)

# 成功详情审计：固定 action/result/target，禁止附带项目或业务计数
ACTION_DETAIL_READ = "bidder_project_compliance_read"
AUDIT_TARGET = "project_compliance"

CODE_NOT_FOUND = "bidder_project_compliance_not_found"
MSG_NOT_FOUND = "项目合规统计不存在或不可访问"


class BidderProjectComplianceNotFoundError(Exception):
    """
    模块：投标人项目合规目标不可访问
    用途：跨空间、不存在、非技术标统一抛出；路由映射 404 bidder_project_compliance_not_found。
    对接：get_project_compliance。
    二次开发：禁止区分「不存在」与「跨空间/商务标」以防止探测。
    """

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        super().__init__(CODE_NOT_FOUND)


def list_technical_projects_for_selector(
    db: Session,
    workspace_id: str,
) -> list[dict[str, str]]:
    """
    模块：投标人项目合规选择器
    用途：返回当前 workspace 内 kind=technical 项目的最小 id/name 列表。
    对接：GET /api/bidder/project-compliance/projects。
    二次开发：
      - 禁止调用或放宽 /api/projects*；字段白名单固定 id/name
      - 不写审计；不得返回商务标或跨空间项目
    """
    stmt = (
        select(Project.id, Project.name)
        .where(
            Project.workspace_id == workspace_id,
            Project.kind == "technical",
        )
        .order_by(Project.updated_at.desc(), Project.id.desc())
    )
    rows = db.execute(stmt).all()
    return [{"id": r.id, "name": r.name} for r in rows]


def _require_technical_project(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
) -> Project:
    """
    用途：校验项目属于当前空间且 kind=technical。
    对接：get_project_compliance。
    """
    project = db.get(Project, project_id)
    if (
        project is None
        or project.workspace_id != workspace_id
        or (project.kind or "technical") != "technical"
    ):
        raise BidderProjectComplianceNotFoundError(project_id)
    return project


def get_project_compliance(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    actor_user_id: str | None,
) -> dict[str, Any]:
    """
    模块：投标人单项目合规统计
    用途：返回指定技术标项目的响应矩阵 dataState 与五项 summary。
    对接：GET /api/bidder/project-compliance/{projectId}；editor_state_service.get_editor_state。
    二次开发：
      - 先校验当前空间 technical，再读取已收敛矩阵；empty 为 200
      - 未知 status 按 uncovered；基点口径与 P10E 一致
      - 成功后写脱敏审计 target=project_compliance；禁止回写项目 ID/计数/矩阵
      - 响应不得含项目字段、矩阵行、sourceKey、章节/大纲、人员/财务
    """
    project = _require_technical_project(
        db, workspace_id=workspace_id, project_id=project_id
    )
    state = editor_state_service.get_editor_state(db, workspace_id, project.id)
    total, covered, uncovered, waived = _count_matrix_rows(state.get("responseMatrix"))

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
        action=ACTION_DETAIL_READ,
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
