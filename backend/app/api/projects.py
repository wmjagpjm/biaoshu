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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_request_actor_user_id, get_workspace_id, require_strict_bid_writer
from app.api.schemas import (
    BidWriterTeamMemberOut,
    BidWriterTeamRecommendationOut,
    EditorStateOut,
    EditorStateUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)
from app.core.database import get_db
from app.services import (
    editor_state_revision_service,
    editor_state_service,
    hr_team_recommendation_service,
    project_service,
)
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_out(project) -> ProjectOut:
    """用途：ORM 实体 → 响应 Schema（camelCase 序列化）。"""
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    kind: Annotated[str | None, Query(description="technical|business，空=全部")] = None,
) -> list[ProjectOut]:
    """
    用途：当前 workspace 的项目列表（updatedAt 倒序）。
    对接：前端 listProjectsAsync → GET /projects?kind=
    """
    items = project_service.list_projects(db, workspace_id, kind=kind)
    return [_to_out(p) for p in items]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ProjectOut:
    """
    用途：创建项目，201 + ProjectOut。
    对接：前端 createProjectAsync → POST /projects（可带 kind=business）
    """
    project = project_service.create_project(
        db,
        workspace_id,
        name=body.name,
        industry=body.industry or "通用",
        status=body.status or "draft",
        technical_plan_step=body.technical_plan_step or 1,
        kind=body.kind or "technical",
        linked_project_id=body.linked_project_id,
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
        patch = body.model_dump(by_alias=False, exclude_unset=True)
        kwargs: dict = {}
        for key in (
            "name",
            "industry",
            "status",
            "technical_plan_step",
            "word_count",
            "kind",
        ):
            if key in patch:
                kwargs[key] = patch[key]
        if "linked_project_id" in patch:
            kwargs["linked_project_id"] = patch["linked_project_id"]
        project = project_service.update_project(
            db,
            workspace_id,
            project_id,
            **kwargs,
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


@router.get(
    "/{project_id}/team-recommendation",
    response_model=BidWriterTeamRecommendationOut,
)
def get_project_team_recommendation(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_strict_bid_writer)],
) -> BidWriterTeamRecommendationOut:
    """
    用途：严格 bid_writer 按需读取单项目团队推荐最小展示投影。
    对接：GET /api/projects/{projectId}/team-recommendation；require_strict_bid_writer；get_bid_writer_projection。
    二次开发：
      - 角色须精确 match bid_writer；is_owner 不能替代 member.role；
        若 owner 同时 member.role 精确为 bid_writer 则允许（角色匹配），
        disabled 与非 bid_writer 均拒绝（含 owner 隐式绕过）
      - 无记录/已清空返回 200 empty，不得 404；跨空间/非技术标映射既有项目 404
      - 禁止返回 htr id、sourceCardId、remark、操作者、项目字段；Cache-Control: no-store
    """
    response.headers["Cache-Control"] = "no-store"
    principal = getattr(request.state, "auth_principal", None)
    actor = getattr(principal, "user_id", None) if principal is not None else None
    try:
        data = hr_team_recommendation_service.get_bid_writer_projection(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=str(actor) if actor else None,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return BidWriterTeamRecommendationOut(
        data_state=data["data_state"],
        members=[
            BidWriterTeamMemberOut(
                order=m["order"],
                person_name=m["person_name"],
                category=m["category"],
                credential_name=m["credential_name"],
                level=m.get("level") or "",
                valid_until=m.get("valid_until"),
            )
            for m in data.get("members") or []
        ],
        updated_at=data.get("updated_at"),
    )


@router.get("/{project_id}/editor-state", response_model=EditorStateOut)
def get_editor_state(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateOut:
    """
    用途：读取技术标工作区编辑状态（无则空字段）。
    对接：前端 editors / guidance 初始化
    二次开发：P13-C 在响应中附带只读 currentRevisionSourceKind（可 null）。
    """
    try:
        data = editor_state_service.get_editor_state(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    return _editor_out(db, workspace_id, data)


@router.put("/{project_id}/editor-state", response_model=EditorStateOut)
def put_editor_state(
    project_id: str,
    body: EditorStateUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateOut:
    """
    用途：部分写入编辑状态（仅 body 中出现的字段更新）。
    说明：Pydantic 未传字段为 None 时：outline/chapters/facts/guidance/analysis
    若客户端显式传 null 会清空；前端应只发需要更新的键（见 exclude_unset）。
    responseMatrix 例外：null 视为未更新，只有 [] 清空，避免整包回写误删映射。
    二次开发：
      - 可选 expectedStateVersion 全状态 CAS；冲突固定 409 最小 detail。
      - 同时带 responseMatrix 与 responseMatrixVersion 时矩阵乐观锁；
        与全状态共用一次锁，全状态冲突优先。
      - 缺 expected 保持兼容写入（非最终安全门）。
      - P13-C：成功响应附带只读 currentRevisionSourceKind；并发漂移保守 null。
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
    if "analysis" in payload:
        kwargs["analysis"] = payload["analysis"]
    if "analysis_overview" in payload:
        kwargs["analysis_overview"] = payload["analysis_overview"]
    if "response_matrix" in payload and payload["response_matrix"] is not None:
        kwargs["response_matrix"] = payload["response_matrix"]
    if "response_matrix_version" in payload and payload["response_matrix_version"] is not None:
        kwargs["response_matrix_version"] = payload["response_matrix_version"]
    if "expected_state_version" in payload and payload["expected_state_version"] is not None:
        kwargs["expected_state_version"] = payload["expected_state_version"]
    if "guidance" in payload:
        kwargs["guidance"] = payload["guidance"]
    if "parsed_markdown" in payload:
        kwargs["parsed_markdown"] = payload["parsed_markdown"]
    if "business_qualify" in payload:
        kwargs["business_qualify"] = payload["business_qualify"]
    if "business_toc" in payload:
        kwargs["business_toc"] = payload["business_toc"]
    if "business_quote" in payload:
        kwargs["business_quote"] = payload["business_quote"]
    if "business_commit" in payload:
        kwargs["business_commit"] = payload["business_commit"]

    try:
        # P12C-B-A：公开浏览器 PUT 唯一写入修订账本；字面量来源，禁止读客户端字段
        # P13-D1：actor 仅来自 request.state helper，禁止 body/query/header 投稿
        data = editor_state_service.upsert_editor_state(
            db,
            workspace_id,
            project_id,
            revision_source_kind="browser_put",
            actor_user_id=get_request_actor_user_id(request),
            **kwargs,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except editor_state_service.EditorStateVersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT,
                "message": exc.message,
                "currentStateVersion": exc.current_state_version,
            },
        ) from None
    except editor_state_service.ResponseMatrixVersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": exc.message,
                "responseMatrix": exc.current_matrix,
                "currentResponseMatrixVersion": exc.current_version,
            },
        ) from None

    return _editor_out(db, workspace_id, data)


def _editor_out(
    db: Session, workspace_id: str, data: dict
) -> EditorStateOut:
    """
    用途：service dict → EditorStateOut（含 analysis / 商务字段 / 矩阵与全状态版本）。
    二次开发：P13-C/D2 用响应 stateVersion 只读解析来源与操作者用户名；
      仅调用一次 resolve_current_revision_meta；查询不写库；
      版本不匹配时两项均为 null；来源与用户名独立降级。
    """
    state_version = data.get("stateVersion") or ""
    project_id = data["projectId"]
    meta = editor_state_revision_service.resolve_current_revision_meta(
        db,
        workspace_id,
        project_id,
        state_version if isinstance(state_version, str) else "",
    )
    return EditorStateOut.model_validate(
        {
            "project_id": project_id,
            "outline": data["outline"],
            "chapters": data["chapters"],
            "facts": data["facts"],
            "mode": data["mode"],
            "analysis_overview": data["analysisOverview"],
            "analysis": data.get("analysis"),
            "response_matrix": data.get("responseMatrix"),
            "response_matrix_version": data.get("responseMatrixVersion") or "",
            "state_version": state_version,
            "guidance": data["guidance"],
            "parsed_markdown": data.get("parsedMarkdown"),
            "business_qualify": data.get("businessQualify"),
            "business_toc": data.get("businessToc"),
            "business_quote": data.get("businessQuote"),
            "business_commit": data.get("businessCommit"),
            "updated_at": data["updatedAt"],
            "current_revision_source_kind": meta.source_kind,
            "current_revision_actor_username": meta.actor_username,
        }
    )
