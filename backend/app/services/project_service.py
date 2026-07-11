"""
模块：项目业务服务
用途：项目 CRUD 的全部业务规则与数据库操作；路由层禁止直接拼 SQL。
对接：
  - 路由：app.api.projects
  - 启动 seed：app.main.lifespan → ensure_default_workspace
  - 前端：features/technical-plan/lib/projectStore.ts（createProjectAsync 等）
二次开发：
  - 鉴权：在 service 入口校验 user 与 workspace 归属
  - 软删除 / 归档：扩展 status 或 deleted_at，勿破坏现有 list 排序语义
  - 与 mock 演示 id（如 proj_01）无关；演示数据仅在前端
"""

from __future__ import annotations

import logging
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import Project, Workspace

logger = logging.getLogger(__name__)

# 与前端 ProjectStatus 保持一致
ALLOWED_STATUS = frozenset(
    {"draft", "analyzing", "writing", "reviewing", "exported"}
)

# technical=技术标；business=商务标
ALLOWED_KINDS = frozenset({"technical", "business"})


class ProjectNotFoundError(Exception):
    """
    用途：项目不存在，或不属于当前 workspace（避免越权探测时区分可在路由统一 404）。
    """


def ensure_default_workspace(db: Session, settings: Settings) -> Workspace:
    """
    用途：个人版启动/请求前保证默认 workspace 行存在。
    参数：db 会话、settings 配置（含 default_workspace_*）。
    返回：已存在或新建的 Workspace。
    """
    ws = db.get(Workspace, settings.default_workspace_id)
    if ws is not None:
        return ws
    ws = Workspace(
        id=settings.default_workspace_id,
        name=settings.default_workspace_name,
        owner_user_id=settings.default_owner_user_id,
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _new_project_id() -> str:
    """
    用途：生成项目主键，格式 proj_{8hex}_{4hex}，与前端本地 id 风格接近但由服务端签发。
    """
    return f"proj_{secrets.token_hex(4)}_{secrets.token_hex(2)}"


def list_projects(
    db: Session,
    workspace_id: str,
    *,
    kind: str | None = None,
) -> list[Project]:
    """
    用途：列出某工作空间下项目，按更新时间倒序。
    对接：GET /api/projects?kind=technical|business
    说明：kind 为空则返回全部（兼容旧客户端）。
    """
    stmt = select(Project).where(Project.workspace_id == workspace_id)
    if kind and kind in ALLOWED_KINDS:
        stmt = stmt.where(Project.kind == kind)
    stmt = stmt.order_by(Project.updated_at.desc())
    return list(db.scalars(stmt).all())


def get_project(db: Session, workspace_id: str, project_id: str) -> Project:
    """
    用途：按 id 取项目，并校验归属 workspace。
    对接：GET /api/projects/{id}
    异常：ProjectNotFoundError
    """
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectNotFoundError(project_id)
    return project


def create_project(
    db: Session,
    workspace_id: str,
    *,
    name: str,
    industry: str = "通用",
    status: str = "draft",
    technical_plan_step: int = 1,
    word_count: int = 0,
    kind: str = "technical",
    linked_project_id: str | None = None,
    source_opportunity_id: str | None = None,
    commit: bool = True,
) -> Project:
    """
    用途：创建项目并落库。
    规则：空名称→未命名；非法 status→draft；步骤钳制 1–6；word_count≥0；
      kind 非法则 technical。
    对接：POST /api/projects；opportunity_service.create_project_from_opportunity
    source_opportunity_id：仅受控立项路径写入；普通项目创建保持 None。
    commit=False：由调用方与标讯校验合并为单次事务，调用方必须负责 commit/rollback。
    """
    cleaned_kind = kind if kind in ALLOWED_KINDS else "technical"
    default_name = (
        "未命名商务标项目" if cleaned_kind == "business" else "未命名技术标项目"
    )
    cleaned_name = name.strip() or default_name
    cleaned_industry = industry.strip() or "通用"
    if status not in ALLOWED_STATUS:
        status = "draft"
    step = max(1, min(6, technical_plan_step))
    project = Project(
        id=_new_project_id(),
        workspace_id=workspace_id,
        name=cleaned_name,
        industry=cleaned_industry,
        status=status,
        updated_at=datetime.now(timezone.utc),
        technical_plan_step=step,
        word_count=max(0, word_count),
        kind=cleaned_kind,
        linked_project_id=linked_project_id,
        source_opportunity_id=source_opportunity_id,
    )
    db.add(project)
    if commit:
        db.commit()
        db.refresh(project)
    else:
        db.flush()
    return project


def update_project(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    name: str | None = None,
    industry: str | None = None,
    status: str | None = None,
    technical_plan_step: int | None = None,
    word_count: int | None = None,
    kind: str | None = None,
    linked_project_id: str | None = ...,  # type: ignore[assignment]
) -> Project:
    """
    用途：部分更新项目；仅非 None 字段生效；自动刷新 updated_at。
    对接：PATCH /api/projects/{id}
    异常：ProjectNotFoundError；非法 status 时 ValueError
    说明：linked_project_id 传 ... 表示不改；传 None 表示清空关联。
    """
    project = get_project(db, workspace_id, project_id)
    if name is not None:
        project.name = name.strip() or project.name
    if industry is not None:
        project.industry = industry.strip() or project.industry
    if status is not None:
        if status not in ALLOWED_STATUS:
            raise ValueError(f"非法 status: {status}")
        project.status = status
    if technical_plan_step is not None:
        project.technical_plan_step = max(1, min(6, technical_plan_step))
    if word_count is not None:
        project.word_count = max(0, word_count)
    if kind is not None:
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"非法 kind: {kind}")
        project.kind = kind
    if linked_project_id is not ...:
        project.linked_project_id = linked_project_id
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：物理删除项目、级联数据库产物并清理 uploads/{project_id} 磁盘目录。
    对接：DELETE /api/projects/{id}
    二次开发：对象存储或软删除上线后改为延迟清理；不得在 commit 前删除目录。
    """
    project = get_project(db, workspace_id, project_id)
    db.delete(project)
    db.commit()
    upload_root = Path(get_settings().upload_dir).resolve()
    project_dir = (upload_root / project_id).resolve()
    if project_dir.is_relative_to(upload_root):
        try:
            shutil.rmtree(project_dir)
        except FileNotFoundError:
            pass
        except OSError:
            # 数据已删除时不回滚；保留异常栈，便于运维清理遗留文件。
            logger.warning("删除项目后清理上传目录失败：%s", project_id, exc_info=True)
