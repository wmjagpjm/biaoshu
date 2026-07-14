"""
模块：P10D 人员资质素材卡路由
用途：严格 hr 的资质卡列表/详情/创建/更新（无删除）。
对接：/api/hr/credential-cards*；deps.require_hr；hr_credential_service。
二次开发：禁止放宽角色；响应 Cache-Control:no-store；写操作依赖既有 CSRF；
  创建/更新须手工安全解析请求体，禁止默认 422 回显证件号/电话等原始输入。
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
)
from app.core.database import get_db
from app.services import hr_credential_service
from app.services.hr_credential_service import (
    CODE_NOT_FOUND,
    MSG_NOT_FOUND,
    HrCredentialNotFoundError,
    HrCredentialValidationError,
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


def _no_store(response: Response) -> None:
    """用途：HR 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_request_invalid() -> NoReturn:
    """用途：HR 写路由专用；校验失败返回固定中文，绝不回显原始请求值。"""
    raise HTTPException(status_code=422, detail=dict(_HR_REQUEST_INVALID_DETAIL)) from None


async def _read_json_object(request: Request) -> dict[str, Any]:
    """
    用途：读取 JSON 对象；非对象/非法 JSON 一律固定错误。
    二次开发：禁止把解析异常信息或 body 片段写入 detail。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_request_invalid()
    if not raw:
        _raise_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_request_invalid()
    if not isinstance(data, dict):
        _raise_request_invalid()
    return data


def _parse_hr_model(model_cls: type[TModel], data: dict[str, Any]) -> TModel:
    """用途：将 JSON 对象校验为 HR 模型；失败时固定脱敏，不暴露 loc/input。"""
    try:
        return model_cls.model_validate(data)
    except ValidationError:
        _raise_request_invalid()


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
