"""
模块：P10D/P10F/P10H/P10I 人力路由
用途：严格 hr 的资质卡列表/详情/创建/更新（无删除）；P10F 团队推荐快照读写；
  P10H 人员业绩卡列表/详情/创建/更新（无删除）；P10I 资质到期只读摘要。
对接：/api/hr/credential-cards*；/api/hr/team-recommendations*；
  /api/hr/performance-cards*；/api/hr/credential-expiry；
  deps.require_hr；hr_credential_service；hr_team_recommendation_service；
  hr_performance_service；hr_credential_expiry_service。
二次开发：禁止放宽角色；响应 Cache-Control:no-store；写操作依赖既有 CSRF；
  创建/更新须手工安全解析请求体，禁止默认 422 回显证件号/电话等原始输入；
  P10I 禁止客户端 asOf/window，禁止写接口。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import require_hr
from app.api.schemas import (
    HrCredentialCardCreate,
    HrCredentialCardDetailOut,
    HrCredentialCardListOut,
    HrCredentialCardSummaryOut,
    HrCredentialCardUpdate,
    HrCredentialExpiryAttentionItemOut,
    HrCredentialExpiryOut,
    HrPerformanceCardCreate,
    HrPerformanceCardDetailOut,
    HrPerformanceCardListOut,
    HrPerformanceCardSummaryOut,
    HrPerformanceCardUpdate,
    HrTeamMemberSnapshotOut,
    HrTeamProjectSelectorItemOut,
    HrTeamProjectSelectorListOut,
    HrTeamRecommendationDetailOut,
    HrTeamRecommendationPut,
    HrTeamRecommendationSummaryListOut,
    HrTeamRecommendationSummaryOut,
)
from app.core.database import get_db
from app.services import (
    hr_credential_expiry_service,
    hr_credential_service,
    hr_performance_service,
    hr_team_recommendation_service,
)
from app.services.hr_credential_service import (
    CODE_NOT_FOUND,
    MSG_NOT_FOUND,
    HrCredentialNotFoundError,
    HrCredentialValidationError,
)
from app.services.hr_performance_service import (
    CODE_NOT_FOUND as PERF_CODE_NOT_FOUND,
    MSG_NOT_FOUND as PERF_MSG_NOT_FOUND,
    HrPerformanceNotFoundError,
    HrPerformanceValidationError,
)
from app.services.hr_team_recommendation_service import (
    CODE_INVALID as TEAM_CODE_INVALID,
    CODE_PROJECT_NOT_FOUND as TEAM_CODE_PROJECT_NOT_FOUND,
    CODE_RECOMMENDATION_NOT_FOUND as TEAM_CODE_REC_NOT_FOUND,
    MSG_INVALID as TEAM_MSG_INVALID,
    MSG_PROJECT_NOT_FOUND as TEAM_MSG_PROJECT_NOT_FOUND,
    MSG_RECOMMENDATION_NOT_FOUND as TEAM_MSG_REC_NOT_FOUND,
    HrTeamProjectNotFoundError,
    HrTeamRecommendationNotFoundError,
    HrTeamRecommendationValidationError,
)

router = APIRouter(prefix="/hr", tags=["hr"])

TModel = TypeVar("TModel", bound=BaseModel)

_CODE_INVALID = "invalid_hr_credential"
_MSG_INVALID = "人员资质卡参数不合法"
# 请求体校验失败的固定脱敏 detail；不得拼接任何原始输入
_HR_REQUEST_INVALID_DETAIL = {
    "code": _CODE_INVALID,
    "message": _MSG_INVALID,
}
_TEAM_REQUEST_INVALID_DETAIL = {
    "code": TEAM_CODE_INVALID,
    "message": TEAM_MSG_INVALID,
}
_PERF_CODE_INVALID = "invalid_hr_performance"
_PERF_MSG_INVALID = "人员业绩卡参数不合法"
_PERF_REQUEST_INVALID_DETAIL = {
    "code": _PERF_CODE_INVALID,
    "message": _PERF_MSG_INVALID,
}


def _no_store(response: Response) -> None:
    """用途：HR 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_request_invalid(
    *,
    detail: dict[str, str] | None = None,
) -> NoReturn:
    """用途：HR 写路由专用；校验失败返回固定中文，绝不回显原始请求值。"""
    raise HTTPException(
        status_code=422,
        detail=dict(detail or _HR_REQUEST_INVALID_DETAIL),
    ) from None


async def _read_json_object(
    request: Request,
    *,
    invalid_detail: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    用途：读取 JSON 对象；非对象/非法 JSON 一律固定错误。
    二次开发：禁止把解析异常信息或 body 片段写入 detail。
    """
    detail = invalid_detail or _HR_REQUEST_INVALID_DETAIL
    try:
        raw = await request.body()
    except Exception:
        _raise_request_invalid(detail=detail)
    if not raw:
        _raise_request_invalid(detail=detail)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_request_invalid(detail=detail)
    if not isinstance(data, dict):
        _raise_request_invalid(detail=detail)
    return data


def _parse_hr_model(
    model_cls: type[TModel],
    data: dict[str, Any],
    *,
    invalid_detail: dict[str, str] | None = None,
) -> TModel:
    """用途：将 JSON 对象校验为 HR 模型；失败时固定脱敏，不暴露 loc/input。"""
    try:
        return model_cls.model_validate(data)
    except ValidationError:
        _raise_request_invalid(detail=invalid_detail)


def _actor_user_id(request: Request) -> str:
    """
    用途：从已验证 request.state 读取操作者 user id。
    对接：创建人与审计；禁止客户端 body/header 注入。
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


def _to_summary(item: dict) -> HrCredentialCardSummaryOut:
    return HrCredentialCardSummaryOut(
        id=item["id"],
        person_name=item["person_name"],
        category=item["category"],
        credential_name=item["credential_name"],
        level=item.get("level") or "",
        valid_until=item.get("valid_until"),
        is_active=bool(item["is_active"]),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _to_detail(item: dict) -> HrCredentialCardDetailOut:
    return HrCredentialCardDetailOut(
        id=item["id"],
        person_name=item["person_name"],
        category=item["category"],
        credential_name=item["credential_name"],
        level=item.get("level") or "",
        valid_until=item.get("valid_until"),
        remark=item.get("remark") or "",
        is_active=bool(item["is_active"]),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _http_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": CODE_NOT_FOUND, "message": MSG_NOT_FOUND},
    )


def _http_invalid() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": _CODE_INVALID, "message": _MSG_INVALID},
    )


@router.get("/credential-expiry", response_model=HrCredentialExpiryOut)
def get_credential_expiry(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrCredentialExpiryOut:
    """
    用途：严格 hr 读取当前空间人员资质到期提示（只读，无查询参数）。
    对接：GET /api/hr/credential-expiry；require_hr；hr_credential_expiry_service。
    二次开发：
      - 禁止 asOf/window/query/body；asOf 由服务端 UTC 自然日决定
      - Cache-Control:no-store；审计 target 固定 credential_expiry
      - 不读取 remark；不写卡片；不访问 P10F/P10H/外网
    """
    _no_store(response)
    actor = _actor_user_id(request)
    raw = hr_credential_expiry_service.get_credential_expiry(
        db,
        workspace_id=workspace_id,
        actor_user_id=actor,
    )
    return HrCredentialExpiryOut(
        as_of_date=raw["as_of_date"],
        window_days=raw["window_days"],
        active_total_count=raw["active_total_count"],
        expired_count=raw["expired_count"],
        expiring_soon_count=raw["expiring_soon_count"],
        valid_count=raw["valid_count"],
        missing_expiry_count=raw["missing_expiry_count"],
        inactive_excluded_count=raw["inactive_excluded_count"],
        attention_items=[
            HrCredentialExpiryAttentionItemOut(
                card_id=item["card_id"],
                person_name=item["person_name"],
                category=item["category"],
                credential_name=item["credential_name"],
                level=item.get("level") or "",
                valid_until=item.get("valid_until"),
                state=item["state"],
                days_remaining=item.get("days_remaining"),
            )
            for item in raw.get("attention_items") or []
        ],
    )


@router.get("/credential-cards", response_model=HrCredentialCardListOut)
def list_credential_cards(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrCredentialCardListOut:
    """
    用途：严格 hr 查看当前工作空间人员资质卡摘要列表。
    对接：GET /api/hr/credential-cards。
    二次开发：列表不含 remark。
    """
    _no_store(response)
    items = hr_credential_service.list_cards(db, workspace_id)
    return HrCredentialCardListOut(items=[_to_summary(x) for x in items])


@router.get(
    "/credential-cards/{card_id}",
    response_model=HrCredentialCardDetailOut,
)
def get_credential_card(
    card_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrCredentialCardDetailOut:
    """
    用途：严格 hr 查看单卡详情（含 remark）。
    对接：GET /api/hr/credential-cards/{cardId}。
    二次开发：跨空间/不存在统一 404 hr_credential_not_found。
    """
    _no_store(response)
    try:
        item = hr_credential_service.get_card(db, workspace_id, card_id)
    except HrCredentialNotFoundError:
        raise _http_not_found() from None
    return _to_detail(item)


@router.post(
    "/credential-cards",
    response_model=HrCredentialCardDetailOut,
    status_code=201,
)
async def create_credential_card(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrCredentialCardDetailOut:
    """
    用途：新建人员资质卡；CSRF 由中间件校验；请求体安全解析不回显。
    对接：POST /api/hr/credential-cards。
    """
    _no_store(response)
    body = _parse_hr_model(HrCredentialCardCreate, await _read_json_object(request))
    actor = _actor_user_id(request)
    try:
        item = hr_credential_service.create_card(
            db,
            workspace_id=workspace_id,
            actor_user_id=actor,
            person_name=body.person_name,
            category=body.category,
            credential_name=body.credential_name,
            level=body.level,
            valid_until=body.valid_until,
            remark=body.remark,
            is_active=body.is_active,
        )
    except HrCredentialValidationError:
        raise _http_invalid() from None
    return _to_detail(item)


@router.patch(
    "/credential-cards/{card_id}",
    response_model=HrCredentialCardDetailOut,
)
async def update_credential_card(
    card_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrCredentialCardDetailOut:
    """
    用途：部分更新资质卡或启停；空补丁拒绝；请求体安全解析不回显。
    对接：PATCH /api/hr/credential-cards/{cardId}。
    """
    _no_store(response)
    body = _parse_hr_model(HrCredentialCardUpdate, await _read_json_object(request))
    actor = _actor_user_id(request)
    raw = body.model_dump(exclude_unset=True)
    kwargs: dict = {
        "person_name": raw.get("person_name"),
        "category": raw.get("category"),
        "credential_name": raw.get("credential_name"),
        "level": raw.get("level"),
        "remark": raw.get("remark"),
        "is_active": raw.get("is_active"),
    }
    if "valid_until" in raw:
        kwargs["valid_until"] = raw["valid_until"]
    try:
        item = hr_credential_service.update_card(
            db,
            workspace_id=workspace_id,
            card_id=card_id,
            actor_user_id=actor,
            **kwargs,
        )
    except HrCredentialNotFoundError:
        raise _http_not_found() from None
    except HrCredentialValidationError:
        raise _http_invalid() from None
    return _to_detail(item)


def _http_team_project_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": TEAM_CODE_PROJECT_NOT_FOUND,
            "message": TEAM_MSG_PROJECT_NOT_FOUND,
        },
    )


def _http_team_rec_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": TEAM_CODE_REC_NOT_FOUND,
            "message": TEAM_MSG_REC_NOT_FOUND,
        },
    )


def _http_team_invalid() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=dict(_TEAM_REQUEST_INVALID_DETAIL),
    )


def _to_team_member(item: dict) -> HrTeamMemberSnapshotOut:
    return HrTeamMemberSnapshotOut(
        order=item["order"],
        person_name=item["person_name"],
        category=item["category"],
        credential_name=item["credential_name"],
        level=item.get("level") or "",
        valid_until=item.get("valid_until"),
        source_card_id=item["source_card_id"],
    )


def _to_team_detail(item: dict) -> HrTeamRecommendationDetailOut:
    return HrTeamRecommendationDetailOut(
        project_id=item["project_id"],
        project_name=item["project_name"],
        members=[_to_team_member(m) for m in item.get("members") or []],
        updated_at=item["updated_at"],
    )


def _to_performance_summary(item: dict) -> HrPerformanceCardSummaryOut:
    return HrPerformanceCardSummaryOut(
        id=item["id"],
        person_name=item["person_name"],
        project_name=item["project_name"],
        project_role=item.get("project_role") or "",
        completed_year=item.get("completed_year"),
        is_active=bool(item["is_active"]),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _to_performance_detail(item: dict) -> HrPerformanceCardDetailOut:
    return HrPerformanceCardDetailOut(
        id=item["id"],
        person_name=item["person_name"],
        project_name=item["project_name"],
        project_role=item.get("project_role") or "",
        completed_year=item.get("completed_year"),
        performance_summary=item.get("performance_summary") or "",
        remark=item.get("remark") or "",
        is_active=bool(item["is_active"]),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
    )


def _http_performance_not_found() -> HTTPException:
    """用途：固定 404，不回显请求 cardId。"""
    return HTTPException(
        status_code=404,
        detail={"code": PERF_CODE_NOT_FOUND, "message": PERF_MSG_NOT_FOUND},
    )


def _http_performance_invalid() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=dict(_PERF_REQUEST_INVALID_DETAIL),
    )


@router.get("/performance-cards", response_model=HrPerformanceCardListOut)
def list_performance_cards(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrPerformanceCardListOut:
    """
    用途：严格 hr 查看当前工作空间人员业绩卡摘要列表。
    对接：GET /api/hr/performance-cards；require_hr；hr_performance_service.list_cards。
    二次开发：列表不含 performanceSummary/remark；Cache-Control:no-store。
    """
    _no_store(response)
    items = hr_performance_service.list_cards(db, workspace_id)
    return HrPerformanceCardListOut(
        items=[_to_performance_summary(x) for x in items]
    )


@router.get(
    "/performance-cards/{card_id}",
    response_model=HrPerformanceCardDetailOut,
)
def get_performance_card(
    card_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrPerformanceCardDetailOut:
    """
    用途：严格 hr 查看单卡详情（含 performanceSummary 与 remark）。
    对接：GET /api/hr/performance-cards/{cardId}；require_hr；get_card。
    二次开发：跨空间/不存在统一 404 hr_performance_not_found；响应不回显 id。
    """
    _no_store(response)
    try:
        item = hr_performance_service.get_card(db, workspace_id, card_id)
    except HrPerformanceNotFoundError:
        raise _http_performance_not_found() from None
    return _to_performance_detail(item)


@router.post(
    "/performance-cards",
    response_model=HrPerformanceCardDetailOut,
    status_code=201,
)
async def create_performance_card(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrPerformanceCardDetailOut:
    """
    用途：新建人员业绩卡；CSRF 由中间件校验；请求体安全解析不回显。
    对接：POST /api/hr/performance-cards；require_hr；create_card。
    二次开发：手工 JSON + extra=forbid；固定 422 invalid_hr_performance。
    """
    _no_store(response)
    body = _parse_hr_model(
        HrPerformanceCardCreate,
        await _read_json_object(
            request, invalid_detail=_PERF_REQUEST_INVALID_DETAIL
        ),
        invalid_detail=_PERF_REQUEST_INVALID_DETAIL,
    )
    actor = _actor_user_id(request)
    try:
        item = hr_performance_service.create_card(
            db,
            workspace_id=workspace_id,
            actor_user_id=actor,
            person_name=body.person_name,
            project_name=body.project_name,
            project_role=body.project_role,
            completed_year=body.completed_year,
            performance_summary=body.performance_summary,
            remark=body.remark,
            is_active=body.is_active,
        )
    except HrPerformanceValidationError:
        raise _http_performance_invalid() from None
    return _to_performance_detail(item)


@router.patch(
    "/performance-cards/{card_id}",
    response_model=HrPerformanceCardDetailOut,
)
async def update_performance_card(
    card_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrPerformanceCardDetailOut:
    """
    用途：部分更新业绩卡或启停；空补丁拒绝；请求体安全解析不回显。
    对接：PATCH /api/hr/performance-cards/{cardId}；require_hr；update_card。
    二次开发：completedYear 显式 null 可清空；固定 404/422 不回显输入。
    """
    _no_store(response)
    body = _parse_hr_model(
        HrPerformanceCardUpdate,
        await _read_json_object(
            request, invalid_detail=_PERF_REQUEST_INVALID_DETAIL
        ),
        invalid_detail=_PERF_REQUEST_INVALID_DETAIL,
    )
    actor = _actor_user_id(request)
    raw = body.model_dump(exclude_unset=True)
    kwargs: dict = {
        "person_name": raw.get("person_name"),
        "project_name": raw.get("project_name"),
        "project_role": raw.get("project_role"),
        "performance_summary": raw.get("performance_summary"),
        "remark": raw.get("remark"),
        "is_active": raw.get("is_active"),
    }
    if "completed_year" in raw:
        kwargs["completed_year"] = raw["completed_year"]
    try:
        item = hr_performance_service.update_card(
            db,
            workspace_id=workspace_id,
            card_id=card_id,
            actor_user_id=actor,
            **kwargs,
        )
    except HrPerformanceNotFoundError:
        raise _http_performance_not_found() from None
    except HrPerformanceValidationError:
        raise _http_performance_invalid() from None
    return _to_performance_detail(item)


@router.get(
    "/team-recommendations/projects",
    response_model=HrTeamProjectSelectorListOut,
)
def list_team_recommendation_projects(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrTeamProjectSelectorListOut:
    """
    用途：严格 hr 获取本空间技术标项目选择器（仅 id/name）。
    对接：GET /api/hr/team-recommendations/projects；require_hr；hr_team_recommendation_service。
    二次开发：
      - 禁止调用或放宽 /api/projects*；字段白名单固定 id/name
      - Cache-Control: no-store；不得扩权给非 strict hr
    """
    _no_store(response)
    items = hr_team_recommendation_service.list_technical_projects_for_selector(
        db, workspace_id
    )
    return HrTeamProjectSelectorListOut(
        items=[HrTeamProjectSelectorItemOut(id=x["id"], name=x["name"]) for x in items]
    )


@router.get(
    "/team-recommendations",
    response_model=HrTeamRecommendationSummaryListOut,
)
def list_team_recommendations(
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrTeamRecommendationSummaryListOut:
    """
    用途：严格 hr 查看当前空间团队推荐摘要列表。
    对接：GET /api/hr/team-recommendations；require_hr；list_recommendation_summaries。
    二次开发：
      - 仅摘要投影；禁止返回成员明细、remark、sourceCardId
      - Cache-Control: no-store；禁止扩为通用项目列表或跨空间查询
    """
    _no_store(response)
    items = hr_team_recommendation_service.list_recommendation_summaries(
        db, workspace_id
    )
    return HrTeamRecommendationSummaryListOut(
        items=[
            HrTeamRecommendationSummaryOut(
                project_id=x["project_id"],
                project_name=x["project_name"],
                member_count=x["member_count"],
                updated_at=x["updated_at"],
            )
            for x in items
        ]
    )


@router.get(
    "/team-recommendations/{project_id}",
    response_model=HrTeamRecommendationDetailOut,
)
def get_team_recommendation(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrTeamRecommendationDetailOut:
    """
    用途：严格 hr 读取单项目团队推荐编辑详情。
    对接：GET /api/hr/team-recommendations/{projectId}；require_hr；get_recommendation_detail。
    二次开发：
      - 项目不可访问 404 hr_team_project_not_found；推荐不存在 404 hr_team_recommendation_not_found
      - 二者禁止混淆，避免跨空间探测；no-store；成员快照不含 remark
    """
    _no_store(response)
    try:
        item = hr_team_recommendation_service.get_recommendation_detail(
            db, workspace_id, project_id
        )
    except HrTeamProjectNotFoundError:
        raise _http_team_project_not_found() from None
    except HrTeamRecommendationNotFoundError:
        raise _http_team_rec_not_found() from None
    return _to_team_detail(item)


@router.put(
    "/team-recommendations/{project_id}",
    response_model=HrTeamRecommendationDetailOut,
)
async def put_team_recommendation(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_hr)],
) -> HrTeamRecommendationDetailOut:
    """
    用途：严格 hr 首建/整表替换团队推荐；空数组清空成员、保留记录、不物理删除。
    对接：PUT /api/hr/team-recommendations/{projectId}；require_hr；put_recommendation。
    二次开发：
      - 手工读取 JSON + schema extra=forbid，仅接受有序 memberCardIds
      - 超限/重复/无效/跨空间/停用卡统一固定 422，不回显后端细节
      - CSRF 由中间件校验；审计 target 仅 htr_*；禁止写入 remark 或扩展写权
    """
    _no_store(response)
    body = _parse_hr_model(
        HrTeamRecommendationPut,
        await _read_json_object(
            request, invalid_detail=_TEAM_REQUEST_INVALID_DETAIL
        ),
        invalid_detail=_TEAM_REQUEST_INVALID_DETAIL,
    )
    actor = _actor_user_id(request)
    try:
        item, created = hr_team_recommendation_service.put_recommendation(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor,
            member_card_ids=list(body.member_card_ids),
        )
    except HrTeamProjectNotFoundError:
        raise _http_team_project_not_found() from None
    except HrTeamRecommendationValidationError:
        raise _http_team_invalid() from None
    response.status_code = 201 if created else 200
    return _to_team_detail(item)
