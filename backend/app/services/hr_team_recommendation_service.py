"""
模块：P10F 人力项目团队推荐快照服务
用途：严格 hr 维护技术标项目团队推荐；严格 bid_writer 读取最小展示投影。
对接：api.hr 团队推荐路由；api.projects 投影路由；auth_service.record_audit。
二次开发：
  - 每个 workspace+technical project 至多一份推荐；空数组清空成员不物理删除
  - 成员行仅快照 P10D 摘要字段，绝不复制 remark
  - 审计 target 仅 htr_*，禁止姓名/资质/项目/卡 ID/数量/请求体
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    HrCredentialCardRow,
    HrTeamRecommendationMemberRow,
    HrTeamRecommendationRow,
    Project,
    utc_now,
)
from app.services import auth_service

MAX_MEMBERS = 30

ACTION_CREATE = "hr_team_recommendation_create"
ACTION_UPDATE = "hr_team_recommendation_update"
ACTION_BW_READ = "bid_writer_team_recommendation_read"

CODE_PROJECT_NOT_FOUND = "hr_team_project_not_found"
MSG_PROJECT_NOT_FOUND = "技术标项目不存在或不可访问"

CODE_RECOMMENDATION_NOT_FOUND = "hr_team_recommendation_not_found"
MSG_RECOMMENDATION_NOT_FOUND = "团队推荐不存在"

CODE_INVALID = "invalid_hr_team_recommendation"
MSG_INVALID = "团队推荐参数不合法"


class HrTeamProjectNotFoundError(Exception):
    """
    模块：团队推荐目标项目不可访问
    用途：跨空间、不存在、非技术标统一抛出；路由映射 404 hr_team_project_not_found。
    对接：list/get/put HR 路径。
    二次开发：禁止区分「不存在」与「跨空间」以防止探测。
    """

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        super().__init__(CODE_PROJECT_NOT_FOUND)


class HrTeamRecommendationNotFoundError(Exception):
    """
    模块：团队推荐记录不存在
    用途：项目合法但尚无推荐时抛出；路由映射 404 hr_team_recommendation_not_found。
    对接：GET HR 详情。
    二次开发：不得与项目 404 混淆。
    """

    def __init__(self, project_id: str = "") -> None:
        self.project_id = project_id
        super().__init__(CODE_RECOMMENDATION_NOT_FOUND)


class HrTeamRecommendationValidationError(Exception):
    """
    模块：团队推荐参数校验错误
    用途：成员列表/卡有效性失败时抛出；路由映射 422。
    对接：put_recommendation。
    二次开发：message 仅固定标签，禁止回显卡 ID 或原始输入。
    """

    def __init__(self, message: str = CODE_INVALID) -> None:
        self.message = message
        super().__init__(message)


def _new_recommendation_id() -> str:
    """用途：生成不透明推荐 ID（htr_ 前缀）。"""
    return f"htr_{secrets.token_hex(8)}"


def _new_member_id() -> str:
    """用途：生成不透明成员快照行 ID。"""
    return f"htrm_{secrets.token_hex(8)}"


def _require_technical_project(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
) -> Project:
    """
    用途：校验项目属于当前空间且 kind=technical。
    对接：HR 读写路径。
    """
    project = db.get(Project, project_id)
    if (
        project is None
        or project.workspace_id != workspace_id
        or (project.kind or "technical") != "technical"
    ):
        raise HrTeamProjectNotFoundError(project_id)
    return project


def list_technical_projects_for_selector(
    db: Session,
    workspace_id: str,
) -> list[dict[str, str]]:
    """
    用途：严格 hr 项目选择器，仅返回当前 workspace 内 kind=technical 的 id/name。
    对接：GET /api/hr/team-recommendations/projects；仅依赖 Project 白名单列。
    二次开发：
      - 禁止调用或放宽 /api/projects*；禁止返回 kind 以外项目或跨空间项目
      - 字段白名单固定为 id/name，禁止扩展 description/owner 等业务字段
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


def list_recommendation_summaries(
    db: Session,
    workspace_id: str,
) -> list[dict[str, Any]]:
    """
    用途：严格 hr 查看当前 workspace 内团队推荐摘要（项目 id/name、成员数、更新时间）。
    对接：GET /api/hr/team-recommendations；聚合 HrTeamRecommendation* 与 Project。
    二次开发：
      - 仅摘要字段；禁止返回成员明细、sourceCardId、remark、操作者
      - 不得跨空间泄漏；不得扩展为通用项目管理列表
    """
    member_count_sq = (
        select(
            HrTeamRecommendationMemberRow.recommendation_id.label("rid"),
            func.count(HrTeamRecommendationMemberRow.id).label("cnt"),
        )
        .where(HrTeamRecommendationMemberRow.workspace_id == workspace_id)
        .group_by(HrTeamRecommendationMemberRow.recommendation_id)
        .subquery()
    )
    stmt = (
        select(
            HrTeamRecommendationRow,
            Project.name,
            func.coalesce(member_count_sq.c.cnt, 0),
        )
        .join(
            Project,
            Project.id == HrTeamRecommendationRow.project_id,
        )
        .outerjoin(
            member_count_sq,
            member_count_sq.c.rid == HrTeamRecommendationRow.id,
        )
        .where(HrTeamRecommendationRow.workspace_id == workspace_id)
        .order_by(
            HrTeamRecommendationRow.updated_at.desc(),
            HrTeamRecommendationRow.id.desc(),
        )
    )
    out: list[dict[str, Any]] = []
    for rec, project_name, member_count in db.execute(stmt).all():
        out.append(
            {
                "project_id": rec.project_id,
                "project_name": project_name,
                "member_count": int(member_count or 0),
                "updated_at": rec.updated_at,
            }
        )
    return out


def _load_members_ordered(
    db: Session,
    recommendation_id: str,
) -> list[HrTeamRecommendationMemberRow]:
    """用途：按 display_order 升序加载成员快照。"""
    stmt = (
        select(HrTeamRecommendationMemberRow)
        .where(HrTeamRecommendationMemberRow.recommendation_id == recommendation_id)
        .order_by(
            HrTeamRecommendationMemberRow.display_order.asc(),
            HrTeamRecommendationMemberRow.id.asc(),
        )
    )
    return list(db.scalars(stmt).all())


def _member_to_hr_dict(row: HrTeamRecommendationMemberRow) -> dict[str, Any]:
    """用途：HR 详情成员投影（含 sourceCardId，无 remark）。"""
    return {
        "order": int(row.display_order),
        "person_name": row.person_name,
        "category": row.category,
        "credential_name": row.credential_name,
        "level": row.level or "",
        "valid_until": row.valid_until,
        "source_card_id": row.source_card_id,
    }


def _detail_dict(
    *,
    project: Project,
    rec: HrTeamRecommendationRow,
    members: list[HrTeamRecommendationMemberRow],
) -> dict[str, Any]:
    """用途：组装 HR 编辑详情。"""
    return {
        "project_id": project.id,
        "project_name": project.name,
        "members": [_member_to_hr_dict(m) for m in members],
        "updated_at": rec.updated_at,
    }


def get_recommendation_detail(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：严格 hr 读取单项目团队推荐编辑详情（有序成员摘要 + 项目信息）。
    对接：GET /api/hr/team-recommendations/{projectId}。
    二次开发：
      - 跨空间/非技术标统一 HrTeamProjectNotFoundError；无记录为 RecommendationNotFound
      - 成员快照不含 remark；禁止扩权为任意项目读写或跨空间探测
    """
    project = _require_technical_project(
        db, workspace_id=workspace_id, project_id=project_id
    )
    rec = db.scalar(
        select(HrTeamRecommendationRow).where(
            HrTeamRecommendationRow.workspace_id == workspace_id,
            HrTeamRecommendationRow.project_id == project_id,
        )
    )
    if rec is None:
        raise HrTeamRecommendationNotFoundError(project_id)
    members = _load_members_ordered(db, rec.id)
    return _detail_dict(project=project, rec=rec, members=members)


def _resolve_active_cards(
    db: Session,
    *,
    workspace_id: str,
    member_card_ids: list[str],
) -> list[HrCredentialCardRow]:
    """
    用途：按输入顺序解析有效同空间资质卡；失败统一 ValidationError。
    对接：put_recommendation。
    """
    if len(member_card_ids) > MAX_MEMBERS:
        raise HrTeamRecommendationValidationError(CODE_INVALID)
    seen: set[str] = set()
    for card_id in member_card_ids:
        if type(card_id) is not str or card_id == "" or card_id in seen:
            raise HrTeamRecommendationValidationError(CODE_INVALID)
        seen.add(card_id)

    if not member_card_ids:
        return []

    stmt = select(HrCredentialCardRow).where(
        HrCredentialCardRow.workspace_id == workspace_id,
        HrCredentialCardRow.id.in_(member_card_ids),
        HrCredentialCardRow.is_active.is_(True),
    )
    by_id = {row.id: row for row in db.scalars(stmt).all()}
    ordered: list[HrCredentialCardRow] = []
    for card_id in member_card_ids:
        row = by_id.get(card_id)
        if row is None:
            raise HrTeamRecommendationValidationError(CODE_INVALID)
        ordered.append(row)
    return ordered


def _replace_members(
    db: Session,
    *,
    rec: HrTeamRecommendationRow,
    cards: list[HrCredentialCardRow],
) -> list[HrTeamRecommendationMemberRow]:
    """用途：在同一事务内替换成员快照行（不含 remark）。"""
    db.execute(
        delete(HrTeamRecommendationMemberRow).where(
            HrTeamRecommendationMemberRow.recommendation_id == rec.id
        )
    )
    members: list[HrTeamRecommendationMemberRow] = []
    for index, card in enumerate(cards, start=1):
        row = HrTeamRecommendationMemberRow(
            id=_new_member_id(),
            recommendation_id=rec.id,
            workspace_id=rec.workspace_id,
            source_card_id=card.id,
            display_order=index,
            person_name=card.person_name,
            category=card.category,
            credential_name=card.credential_name,
            level=card.level or "",
            valid_until=card.valid_until,
        )
        db.add(row)
        members.append(row)
    return members


def put_recommendation(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    member_card_ids: list[str],
) -> tuple[dict[str, Any], bool]:
    """
    用途：严格 hr 首建或整表替换团队推荐；空数组清空成员、保留记录、不物理删除。
    对接：PUT /api/hr/team-recommendations/{projectId}；审计 action 为 create/update。
    二次开发：
      - 仅接受有序 memberCardIds；>30、重复、空串、跨空间或 isActive=false 卡统一 ValidationError
      - 快照仅复制 P10D 摘要字段，绝不写入 remark；审计 target 仅 htr_*
      - 每 workspace+technical project 至多一份；禁止扩展为多版本/通用协作
      - 返回 (详情字典, 是否新建)
    """
    project = _require_technical_project(
        db, workspace_id=workspace_id, project_id=project_id
    )
    cards = _resolve_active_cards(
        db, workspace_id=workspace_id, member_card_ids=member_card_ids
    )
    now = utc_now()
    rec = db.scalar(
        select(HrTeamRecommendationRow).where(
            HrTeamRecommendationRow.workspace_id == workspace_id,
            HrTeamRecommendationRow.project_id == project_id,
        )
    )
    created = rec is None
    if created:
        rec = HrTeamRecommendationRow(
            id=_new_recommendation_id(),
            workspace_id=workspace_id,
            project_id=project_id,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(rec)
        db.flush()
    else:
        rec.updated_by_user_id = actor_user_id
        rec.updated_at = now

    members = _replace_members(db, rec=rec, cards=cards)
    auth_service.record_audit(
        db,
        action=ACTION_CREATE if created else ACTION_UPDATE,
        result="success",
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        target=rec.id,
        commit=False,
    )
    db.commit()
    db.refresh(rec)
    for m in members:
        db.refresh(m)
    return _detail_dict(project=project, rec=rec, members=members), created


def get_bid_writer_projection(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """
    用途：严格 bid_writer 单项目只读最小展示投影；无记录或成员已清空均为 data_state=empty。
    对接：GET /api/projects/{projectId}/team-recommendation；可读审计 bid_writer_team_recommendation_read。
    二次开发：
      - 投影白名单仅 order/personName/category/credentialName/level/validUntil 等展示字段
      - 禁止返回 htr id、sourceCardId、remark、操作者、项目字段
      - 跨空间/非技术标抛 ProjectNotFoundError，由路由映射既有「项目不存在」404；empty 不得 404
      - 角色门禁在 require_strict_bid_writer：is_owner 不能替代 member.role；
        若 owner 同时 member.role 精确为 bid_writer 则允许（角色匹配），
        disabled 与非 bid_writer 均拒绝
    """
    project = db.get(Project, project_id)
    if (
        project is None
        or project.workspace_id != workspace_id
        or (project.kind or "technical") != "technical"
    ):
        # 与项目路由统一：由调用方映射既有「项目不存在」
        from app.services.project_service import ProjectNotFoundError

        raise ProjectNotFoundError(project_id)

    rec = db.scalar(
        select(HrTeamRecommendationRow).where(
            HrTeamRecommendationRow.workspace_id == workspace_id,
            HrTeamRecommendationRow.project_id == project_id,
        )
    )
    if rec is None:
        return {
            "data_state": "empty",
            "members": [],
            "updated_at": None,
        }

    members = _load_members_ordered(db, rec.id)
    if not members:
        return {
            "data_state": "empty",
            "members": [],
            "updated_at": None,
        }

    if actor_user_id:
        auth_service.record_audit(
            db,
            action=ACTION_BW_READ,
            result="success",
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            target=rec.id,
            commit=True,
        )

    return {
        "data_state": "ready",
        "members": [
            {
                "order": int(m.display_order),
                "person_name": m.person_name,
                "category": m.category,
                "credential_name": m.credential_name,
                "level": m.level or "",
                "valid_until": m.valid_until,
            }
            for m in members
        ],
        "updated_at": rec.updated_at,
    }
