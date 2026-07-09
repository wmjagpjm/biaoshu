"""
模块：项目 CRUD 路由（薄层）
用途：HTTP 入参校验、调用 project_service、映射 HTTP 状态码；不含业务规则。
对接：
  - 路径前缀：/api/projects（main 挂载 prefix=/api + router prefix=/projects）
  - 前端：apiFetch("/projects")，base 为 /api 或 http://host:8000/api
二次开发：
  - 只加参数与响应转换；复杂逻辑进 services/
  - 新子资源（如 /projects/{id}/artifacts）可同文件新 router 或拆文件
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateOut,
    EditorStateUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)
from app.core.database import get_db
from app.services import editor_state_service, project_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_out(project) -> ProjectOut:
    """用途：ORM 实体 → 响应 Schema（camelCase 序列化）。"""
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> list[ProjectOut]:
    """
    用途：当前 workspace 的项目列表（updatedAt 倒序）。
    对接：前端 listProjectsAsync → GET /projects
    """
    items = project_service.list_projects(db, workspace_id)
    return [_to_out(p) for p in items]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ProjectOut:
    """
    用途：创建项目，201 + ProjectOut。
    对接：前端 createProjectAsync → POST /projects
    """
    project = project_service.create_project(
        db,
        workspace_id,
        name=body.name,
        industry=body.industry or "通用",
        status=body.status or "draft",
        technical_plan_step=body.technical_plan_step or 1,
    )
    return _to_out(project)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ProjectOut:
    """
    用途：项目详情；不存在或非本 workspace → 404。
    对接：前端 getProjectAsync → GET /projects/{id}
    """
    try:
        project = project_service.get_project(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return _to_out(project)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: str,
    body: ProjectUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ProjectOut:
    """
    用途：部分更新；非法 status → 400；不存在 → 404。
    对接：前端 updateProjectAsync → PATCH /projects/{id}
    """
    try:
        project = project_service.update_project(
            db,
            workspace_id,
            project_id,
            name=body.name,
            industry=body.industry,
            status=body.status,
            technical_plan_step=body.technical_plan_step,
            word_count=body.word_count,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_out(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> Response:
    """
    用途：删除项目，成功 204 无 body。
    对接：后续列表页「删除」按钮
    """
    try:
        project_service.delete_project(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/editor-state", response_model=EditorStateOut)
def get_editor_state(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateOut:
    """
    用途：读取技术标工作区编辑状态（无则空字段）。
    对接：前端 editors / guidance 初始化
    """
    try:
        data = editor_state_service.get_editor_state(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return _editor_out(data)


@router.put("/{project_id}/editor-state", response_model=EditorStateOut)
def put_editor_state(
    project_id: str,
    body: EditorStateUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateOut:
    """
    用途：部分写入编辑状态（仅 body 中出现的字段更新）。
    说明：Pydantic 未传字段为 None 时：outline/chapters/facts/guidance/analysis
    若客户端显式传 null 会清空；前端应只发需要更新的键（见 exclude_unset）。
    """
    # exclude_unset：未出现在 JSON 的字段不覆盖
    payload = body.model_dump(by_alias=False, exclude_unset=True)
    kwargs: dict = {}
    if "outline" in payload:
        kwargs["outline"] = payload["outline"]
    if "chapters" in payload:
        kwargs["chapters"] = payload["chapters"]
    if "facts" in payload:
        kwargs["facts"] = payload["facts"]
    if "mode" in payload:
        kwargs["mode"] = payload["mode"]
    if "analysis_overview" in payload:
        kwargs["analysis_overview"] = payload["analysis_overview"]
    if "guidance" in payload:
        kwargs["guidance"] = payload["guidance"]
    if "parsed_markdown" in payload:
        kwargs["parsed_markdown"] = payload["parsed_markdown"]

    try:
        data = editor_state_service.upsert_editor_state(
            db, workspace_id, project_id, **kwargs
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None

    return _editor_out(data)


def _editor_out(data: dict) -> EditorStateOut:
    """用途：service dict → EditorStateOut（含 parsedMarkdown）。"""
    return EditorStateOut.model_validate(
        {
            "project_id": data["projectId"],
            "outline": data["outline"],
            "chapters": data["chapters"],
            "facts": data["facts"],
            "mode": data["mode"],
            "analysis_overview": data["analysisOverview"],
            "guidance": data["guidance"],
            "parsed_markdown": data.get("parsedMarkdown"),
            "updated_at": data["updatedAt"],
        }
    )
