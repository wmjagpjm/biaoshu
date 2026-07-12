"""
模块：技术标中标内容模板路由
用途：提供从项目沉淀、列表/详情/删除、从模板新建项目草稿的 HTTP 入口；跨 workspace 统一 404。
对接：/api/templates；template_service；frontend/src/features/bid-templates。
二次开发：路由层只做参数与状态码映射；禁止直接读写 ORM；勿扩展为导出版式模板。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    BidTemplateFromProjectCreate,
    BidTemplateOut,
    BidTemplateProjectCreate,
    BidTemplateSummaryOut,
    ProjectOut,
)
from app.core.database import get_db
from app.services import template_service
from app.services.project_service import ProjectNotFoundError
from app.services.template_service import (
    TemplateNotFoundError,
    TemplateValidationError,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _to_summary_out(row) -> BidTemplateSummaryOut:
    """用途：ORM → 列表摘要（无完整 snapshot）。"""
    data = (
        row
        if isinstance(row, dict)
        else template_service.template_to_summary_data(row)
    )
    return BidTemplateSummaryOut.model_validate(data)


def _to_out(row) -> BidTemplateOut:
    """用途：服务读模型/ORM → 详情 BidTemplateOut（含 snapshot）。"""
    data = (
        row
        if isinstance(row, dict)
        else template_service.template_to_data(row)
    )
    return BidTemplateOut.model_validate(data)


def _to_project_out(project) -> ProjectOut:
    """用途：从模板立项结果复用 ProjectOut。"""
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[BidTemplateSummaryOut])
def list_templates(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="active|archived，空=全部"),
    ] = None,
) -> list[BidTemplateSummaryOut]:
    """
    用途：当前 workspace 内容模板列表（元数据 + 轻量摘要，不含完整 snapshot）。
    对接：GET /api/templates；BidTemplatesPage。
    """
    rows = template_service.list_templates(
        db, workspace_id, q=q, status=status_filter
    )
    return [_to_summary_out(r) for r in rows]


@router.post(
    "/from-project",
    response_model=BidTemplateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_template_from_project(
    body: BidTemplateFromProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> BidTemplateOut:
    """
    用途：从本 workspace 技术标项目深拷贝大纲/章节为独立模板快照。
    对接：POST /api/templates/from-project；工作区「沉淀为模板」。
    """
    try:
        row = template_service.create_template_from_project(
            db,
            workspace_id,
            body.project_id,
            title=body.title,
            tags=body.tags,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except TemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(row)


@router.get("/{template_id}", response_model=BidTemplateOut)
def get_template(
    template_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> BidTemplateOut:
    """
    用途：模板详情（含 snapshot）；跨 workspace → 404。
    对接：GET /api/templates/{id}。
    """
    try:
        row = template_service.get_template(db, workspace_id, template_id)
    except TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="模板不存在") from None
    return _to_out(row)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> Response:
    """
    用途：删除模板，不影响任何项目。
    对接：DELETE /api/templates/{id}。
    """
    try:
        template_service.delete_template(db, workspace_id, template_id)
    except TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="模板不存在") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{template_id}/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
def create_project_from_template(
    template_id: str,
    body: BidTemplateProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ProjectOut:
    """
    用途：从模板创建全新技术标项目草稿并写入独立 editor-state 副本。
    对接：POST /api/templates/{id}/projects；模板库「从模板新建」。
    """
    try:
        project = template_service.create_project_from_template(
            db,
            workspace_id,
            template_id,
            name=body.name,
            industry=body.industry,
        )
    except TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="模板不存在") from None
    except TemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_project_out(project)
