"""
模块：P12C-C1/C2/P12D-A/P12E-A/P12E-B/P12F-B/P12F-D/P12F-E-A/P12F-F-A/P12F-G-A/P12F-H/P12F-J-A/P12F-J-B
  editor-state 修订历史只读、游标页、来源/时间范围筛选、可见内容搜索、受限恢复、
  差异摘要与正文差异、单条物理删除、单条展示命名、单条固定状态路由
用途：项目最近 10 条修订元数据列表、键集游标页（可选 sourceKind/createdFrom/createdBefore）、
  有界可见内容搜索、单条按需详情、单条受限恢复、与当前状态差异摘要、单/双修订正文差异、
  单条自动修订物理删除、单条展示名称 PATCH、单条固定状态 PATCH。
对接：/api/projects/{projectId}/editor-state-revisions*；
  editor_state_revision_history_service；
  editor_state_revision_restore_service；
  editor_state_revision_comparison_service；
  editor_state_revision_body_diff_service；
  editor_state_revision_delete_service；
  editor_state_revision_name_service；
  editor_state_revision_pin_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - POST/DELETE/PATCH 继续既有 CSRF；所有成功/业务错误 Cache-Control: no-store；
  - 错误固定 code/message（409 另含 currentStateVersion），不反射 ID/正文/路径/SQL/关键词/名称；
  - 未知查询参数不得改变固定排序/上限/来源全集/正文不可搜索边界；
  - 静态 /page 与 /search 必须注册在动态 /{revision_id} 之前；页大小服务端固定 10；
  - page 扩展可选 query 别名 sourceKind/createdFrom/createdBefore；旧列表路由完全不变；
  - search 仅 POST body 承载关键词；list/page/search 七键含 displayName/isPinned；detail 八键含 snapshot；
  - comparison/body-diff 只读，禁止写库/锁/审计；
  - P12E-B 双修订 body-diff 两侧均经 C1 校验，禁止读取当前 editor-state；
  - DELETE 必须无 query 且 body 严格零长度；成功固定空 204；
  - PATCH display-name：query 空、body≤1024、精确一键；成功仅回 displayName；
  - PATCH pin：query 空、body≤1024、精确一键 isPinned 原生 bool；成功仅回 isPinned；
  - 不得把 revision ID/state version/原始快照/关键词/名称放入错误响应。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateRevisionBodyDiffHunkOut,
    EditorStateRevisionBodyDiffItemOut,
    EditorStateRevisionBodyDiffOut,
    EditorStateRevisionComparisonOut,
    EditorStateRevisionComparisonSummaryOut,
    EditorStateRevisionDetailOut,
    EditorStateRevisionDisplayNameOut,
    EditorStateRevisionDisplayNameUpdate,
    EditorStateRevisionListOut,
    EditorStateRevisionMetaOut,
    EditorStateRevisionPageOut,
    EditorStateRevisionPairBodyDiffOut,
    EditorStateRevisionPinOut,
    EditorStateRevisionPinUpdate,
    EditorStateRevisionRestore,
    EditorStateRevisionRestoreOut,
    EditorStateRevisionSearch,
)
from app.core.database import get_db
from app.services import (
    editor_state_revision_body_diff_service,
    editor_state_revision_comparison_service,
    editor_state_revision_history_service,
    editor_state_revision_restore_service,
    editor_state_service,
)
from app.services.editor_state_revision_body_diff_service import (
    EditorStateRevisionBodyDiffError,
)
from app.services.editor_state_revision_comparison_service import (
    EditorStateRevisionComparisonError,
)
from app.services.editor_state_revision_delete_service import (
    EditorStateRevisionDeleteError,
    delete_editor_state_revision as delete_editor_state_revision_svc,
)
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
)
from app.services.editor_state_revision_name_service import (
    CODE_NAME_INVALID,
    MSG_NAME_INVALID,
    EditorStateRevisionNameError,
    set_editor_state_revision_display_name as set_display_name_svc,
)
from app.services.editor_state_revision_pin_service import (
    CODE_PIN_INVALID,
    MSG_PIN_INVALID,
    EditorStateRevisionPinError,
    set_editor_state_revision_pin as set_pin_svc,
)
from app.services.editor_state_revision_restore_service import (
    EditorStateRevisionRestoreError,
)

# P12F-H：命名请求体原始字节硬上限
_DISPLAY_NAME_BODY_MAX_BYTES = 1024
# P12F-J-A：固定请求体原始字节硬上限
_PIN_BODY_MAX_BYTES = 1024

router = APIRouter(prefix="/projects", tags=["editor-state-revisions"])

# P12F-F-A 搜索请求体外壳校验失败的固定脱敏 detail；禁止拼接任何原始输入
_SEARCH_REQUEST_INVALID_DETAIL = {
    "code": "editor_state_revision_search_request_invalid",
    "message": "修订搜索请求无效",
}

# P12F-G-A 删除请求 query/body 校验失败的固定脱敏 detail；禁止反射输入
_DELETE_REQUEST_INVALID_DETAIL = {
    "code": "editor_state_revision_delete_request_invalid",
    "message": "修订删除请求无效",
}

# P12F-H 命名请求 query/body 外壳失败的固定脱敏 detail；禁止反射输入
_NAME_REQUEST_INVALID_DETAIL = {
    "code": CODE_NAME_INVALID,
    "message": MSG_NAME_INVALID,
}

# P12F-J-A 固定请求 query/body 外壳失败的固定脱敏 detail；禁止反射输入
_PIN_REQUEST_INVALID_DETAIL = {
    "code": CODE_PIN_INVALID,
    "message": MSG_PIN_INVALID,
}


def _no_store(response: Response) -> None:
    """用途：P12C-C1/C2 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_delete_request_invalid() -> NoReturn:
    """
    用途：DELETE 路由专用；任意 query 或非空 body 固定脱敏 422。
    二次开发：禁止回显 query/body/路径/header/异常原文。
    """
    raise HTTPException(
        status_code=422,
        detail=dict(_DELETE_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_delete_error(exc: EditorStateRevisionDeleteError) -> NoReturn:
    """用途：映射删除服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_name_request_invalid() -> NoReturn:
    """
    用途：PATCH display-name 路由专用；query/体非法固定脱敏 422。
    二次开发：禁止回显 query/body/路径/header/异常原文/名称。
    """
    raise HTTPException(
        status_code=422,
        detail=dict(_NAME_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_name_error(exc: EditorStateRevisionNameError) -> NoReturn:
    """用途：映射命名服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_pin_request_invalid() -> NoReturn:
    """
    用途：PATCH pin 路由专用；query/体非法固定脱敏 422。
    二次开发：禁止回显 query/body/路径/header/异常原文。
    """
    raise HTTPException(
        status_code=422,
        detail=dict(_PIN_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_pin_error(exc: EditorStateRevisionPinError) -> NoReturn:
    """用途：映射固定服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


async def _read_pin_json_object(request: Request) -> dict[str, Any]:
    """
    用途：仅 pin 路由读取 ≤1024 字节 JSON 对象；失败固定 422。
    二次开发：禁止把解析异常或 body 片段写入 detail/日志。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_pin_request_invalid()
    if not raw or len(raw) > _PIN_BODY_MAX_BYTES:
        _raise_pin_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_pin_request_invalid()
    if not isinstance(data, dict):
        _raise_pin_request_invalid()
    return data


def _parse_pin_body(data: dict[str, Any]) -> EditorStateRevisionPinUpdate:
    """用途：JSON 对象 → 固定 Schema；失败固定脱敏，不暴露 loc/input。"""
    try:
        return EditorStateRevisionPinUpdate.model_validate(data)
    except ValidationError:
        _raise_pin_request_invalid()


async def _read_display_name_json_object(request: Request) -> dict[str, Any]:
    """
    用途：仅 display-name 路由读取 ≤1024 字节 JSON 对象；失败固定 422。
    二次开发：禁止把解析异常或 body 片段写入 detail/日志。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_name_request_invalid()
    if not raw or len(raw) > _DISPLAY_NAME_BODY_MAX_BYTES:
        _raise_name_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_name_request_invalid()
    if not isinstance(data, dict):
        _raise_name_request_invalid()
    return data


def _parse_display_name_body(data: dict[str, Any]) -> EditorStateRevisionDisplayNameUpdate:
    """用途：JSON 对象 → 命名 Schema；失败固定脱敏，不暴露 loc/input。"""
    try:
        return EditorStateRevisionDisplayNameUpdate.model_validate(data)
    except ValidationError:
        _raise_name_request_invalid()


def _raise_search_request_invalid() -> NoReturn:
    """
    用途：search 路由专用；请求体读取/JSON/非对象/Schema 校验失败时固定脱敏 422。
    二次开发：禁止回显 loc/input/type/url/原始 body/query/额外键值。
    """
    raise HTTPException(
        status_code=422,
        detail=dict(_SEARCH_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


async def _read_search_json_object(request: Request) -> dict[str, Any]:
    """
    用途：仅 search 路由读取 JSON 对象；非对象/非法 JSON 一律固定 422。
    二次开发：禁止把解析异常信息或 body 片段写入 detail/日志。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_search_request_invalid()
    if not raw:
        _raise_search_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_search_request_invalid()
    if not isinstance(data, dict):
        _raise_search_request_invalid()
    return data


def _parse_search_body(data: dict[str, Any]) -> EditorStateRevisionSearch:
    """用途：JSON 对象 → EditorStateRevisionSearch；失败固定脱敏，不暴露 loc/input。"""
    try:
        return EditorStateRevisionSearch.model_validate(data)
    except ValidationError:
        _raise_search_request_invalid()


def _raise_history_error(exc: EditorStateRevisionHistoryError) -> None:
    """
    用途：映射只读服务层固定错误，不附加路径或异常原文。
    二次开发：业务 404/500 必须自带 Cache-Control: no-store。
    """
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_restore_error(exc: EditorStateRevisionRestoreError) -> None:
    """用途：映射恢复服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_comparison_error(exc: EditorStateRevisionComparisonError) -> None:
    """用途：映射差异摘要服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_body_diff_error(exc: EditorStateRevisionBodyDiffError) -> None:
    """用途：映射正文差异服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _meta_out(data: dict) -> EditorStateRevisionMetaOut:
    """用途：service 元数据 dict → 七键响应模型。"""
    return EditorStateRevisionMetaOut(
        revision_id=data["revision_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        source_kind=data["source_kind"],
        created_at=data["created_at"],
        display_name=data["display_name"],
        is_pinned=data["is_pinned"],
    )


@router.get(
    "/{project_id}/editor-state-revisions",
    response_model=EditorStateRevisionListOut,
)
def list_editor_state_revisions(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionListOut:
    """
    用途：读取当前项目最近 10 条修订元数据（无 snapshot 正文）。
    对接：修订历史列表浏览。
    """
    _no_store(response)
    try:
        data = editor_state_revision_history_service.list_editor_state_revisions(
            db, workspace_id, project_id
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    return EditorStateRevisionListOut(
        items=[_meta_out(item) for item in data["items"]]
    )


@router.get(
    "/{project_id}/editor-state-revisions/page",
    response_model=EditorStateRevisionPageOut,
)
def list_editor_state_revisions_page(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    cursor: str | None = None,
    source_kind: Annotated[str | None, Query(alias="sourceKind")] = None,
    created_from: Annotated[str | None, Query(alias="createdFrom")] = None,
    created_before: Annotated[str | None, Query(alias="createdBefore")] = None,
) -> EditorStateRevisionPageOut:
    """
    用途：固定每页 10 条的只读键集分页；可选 cursor 取下一页；
      可选 sourceKind 与 createdFrom/createdBefore 筛选。
    对接：P12F-B/D/E-A；editor_state_revision_history_service.list_editor_state_revisions_page。
    二次开发：必须静态注册在 /{revision_id} 之前；非法游标/来源/时间固定 400；全程 no-store；
      缺 sourceKind 表示全部；缺时间边界表示无时间筛选；
      未知 limit/offset/page/source/search/q/dateFrom 不得改变固定页。
    """
    _no_store(response)
    try:
        data = (
            editor_state_revision_history_service.list_editor_state_revisions_page(
                db,
                workspace_id,
                project_id,
                cursor,
                source_kind,
                created_from,
                created_before,
            )
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    return EditorStateRevisionPageOut(
        items=[_meta_out(item) for item in data["items"]],
        next_cursor=data["next_cursor"],
    )


@router.post(
    "/{project_id}/editor-state-revisions/search",
    response_model=EditorStateRevisionListOut,
)
async def search_editor_state_revisions(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionListOut:
    """
    用途：在最新 20 条候选修订中按可见字段字面搜索；仅返回五键元数据。
    对接：P12F-F-A；editor_state_revision_history_service.list_editor_state_revision_search。
    二次开发：
      - 必须静态注册在 /{revision_id} 之前；
      - 请求体手工安全解析 + extra=forbid Schema；值由 service 判型；
      - 外壳失败固定 422 脱敏，禁止默认 Pydantic 回显 input；
      - 成功/业务错误 no-store；不反射 query/正文/ID；不写库。
    """
    _no_store(response)
    body = _parse_search_body(await _read_search_json_object(request))
    try:
        data = (
            editor_state_revision_history_service.list_editor_state_revision_search(
                db,
                workspace_id,
                project_id,
                body.query,
                body.source_kind,
                body.created_from,
                body.created_before,
            )
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    return EditorStateRevisionListOut(
        items=[_meta_out(item) for item in data["items"]]
    )


@router.get(
    "/{project_id}/editor-state-revisions/{revision_id}",
    response_model=EditorStateRevisionDetailOut,
)
def get_editor_state_revision(
    project_id: str,
    revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionDetailOut:
    """
    用途：按 ID 读取单条修订详情（元数据 + 已校验 snapshot）。
    对接：只读浏览；损坏数据固定 500 脱敏。
    """
    _no_store(response)
    try:
        data = editor_state_revision_history_service.get_editor_state_revision(
            db, workspace_id, project_id, revision_id
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    return EditorStateRevisionDetailOut(
        revision_id=data["revision_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        source_kind=data["source_kind"],
        created_at=data["created_at"],
        display_name=data["display_name"],
        is_pinned=data["is_pinned"],
        snapshot=data["snapshot"],
    )


@router.patch(
    "/{project_id}/editor-state-revisions/{revision_id}/pin",
    response_model=EditorStateRevisionPinOut,
)
async def patch_editor_state_revision_pin(
    project_id: str,
    revision_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionPinOut:
    """
    用途：单条更新当前工作空间项目内自动修订的固定状态；成功仅回 isPinned。
    对接：P12F-J-A；editor_state_revision_pin_service。
    二次开发：
      - 任意 query 或 body 外壳失败固定 422 脱敏；
      - body 原始长度 ≤1024；精确一键 isPinned 原生 bool；
      - 超限 409 零写；成功/业务错误 no-store；不回显 ID/版本/正文/输入。
    """
    _no_store(response)
    if request.query_params:
        _raise_pin_request_invalid()
    body = _parse_pin_body(await _read_pin_json_object(request))
    try:
        stored = set_pin_svc(
            db, workspace_id, project_id, revision_id, body.is_pinned
        )
    except EditorStateRevisionPinError as exc:
        _raise_pin_error(exc)
    return EditorStateRevisionPinOut(is_pinned=stored)


@router.patch(
    "/{project_id}/editor-state-revisions/{revision_id}/display-name",
    response_model=EditorStateRevisionDisplayNameOut,
)
async def patch_editor_state_revision_display_name(
    project_id: str,
    revision_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionDisplayNameOut:
    """
    用途：单条更新当前工作空间项目内自动修订的展示名称；成功仅回 displayName。
    对接：P12F-H；editor_state_revision_name_service。
    二次开发：
      - 任意 query 或 body 外壳失败固定 422 脱敏；
      - body 原始长度 ≤1024；精确一键 displayName；
      - 成功/业务错误 no-store；不回显 ID/版本/正文/输入。
    """
    _no_store(response)
    if request.query_params:
        _raise_name_request_invalid()
    body = _parse_display_name_body(await _read_display_name_json_object(request))
    try:
        stored = set_display_name_svc(
            db, workspace_id, project_id, revision_id, body.display_name
        )
    except EditorStateRevisionNameError as exc:
        _raise_name_error(exc)
    return EditorStateRevisionDisplayNameOut(display_name=stored)


@router.delete(
    "/{project_id}/editor-state-revisions/{revision_id}",
    status_code=204,
    response_class=Response,
)
async def delete_editor_state_revision(
    project_id: str,
    revision_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> Response:
    """
    用途：单条物理删除当前工作空间项目内的自动修订；成功空 204。
    对接：P12F-G-A；editor_state_revision_delete_service。
    二次开发：
      - 任意 query 或非空 body（含 {}、null、文本）固定 422 脱敏；
      - 成功严格空正文 + no-store；不回显 ID/版本/计数/正文；
      - 不改变 /page /search 静态优先级与其它 GET/POST 语义。
    """
    _no_store(response)
    if request.query_params:
        _raise_delete_request_invalid()
    try:
        raw = await request.body()
    except Exception:
        _raise_delete_request_invalid()
    if raw:
        _raise_delete_request_invalid()
    try:
        delete_editor_state_revision_svc(
            db, workspace_id, project_id, revision_id
        )
    except EditorStateRevisionDeleteError as exc:
        _raise_delete_error(exc)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get(
    "/{project_id}/editor-state-revisions/{revision_id}/comparison",
    response_model=EditorStateRevisionComparisonOut,
)
def compare_editor_state_revision_with_current(
    project_id: str,
    revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionComparisonOut:
    """
    用途：只读比较目标修订与服务端当前 13 键，返回变更字段名与两侧有界摘要。
    对接：P12D-A；editor_state_revision_comparison_service。
    二次开发：历史 404/corrupt 原样映射；其他失败固定 comparison_failed；全程 no-store。
    """
    _no_store(response)
    try:
        data = (
            editor_state_revision_comparison_service.compare_revision_with_current(
                db, workspace_id, project_id, revision_id
            )
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    except EditorStateRevisionComparisonError as exc:
        _raise_comparison_error(exc)

    def _summary(raw: dict) -> EditorStateRevisionComparisonSummaryOut:
        return EditorStateRevisionComparisonSummaryOut(
            outline_node_count=raw["outline_node_count"],
            chapter_count=raw["chapter_count"],
            fact_count=raw["fact_count"],
            response_matrix_row_count=raw["response_matrix_row_count"],
            business_entry_total=raw["business_entry_total"],
            has_parsed_markdown=raw["has_parsed_markdown"],
        )

    return EditorStateRevisionComparisonOut(
        same_state=data["same_state"],
        changed_fields=data["changed_fields"],
        current_summary=_summary(data["current_summary"]),
        target_summary=_summary(data["target_summary"]),
    )


@router.get(
    "/{project_id}/editor-state-revisions/{revision_id}/body-diff",
    response_model=EditorStateRevisionBodyDiffOut,
)
def compare_editor_state_revision_body_with_current(
    project_id: str,
    revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionBodyDiffOut:
    """
    用途：只读比较目标修订与当前 chapters，返回有界行差异。
    对接：P12E-A；editor_state_revision_body_diff_service。
    二次开发：历史 404/corrupt 原样映射；其他失败固定 body_diff_failed；全程 no-store。
    """
    _no_store(response)
    try:
        data = (
            editor_state_revision_body_diff_service.compare_revision_body_with_current(
                db, workspace_id, project_id, revision_id
            )
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    except EditorStateRevisionBodyDiffError as exc:
        _raise_body_diff_error(exc)

    items: list[EditorStateRevisionBodyDiffItemOut] = []
    for item in data["items"]:
        items.append(
            EditorStateRevisionBodyDiffItemOut(
                ordinal=item["ordinal"],
                kind=item["kind"],
                before_title=item["before_title"],
                after_title=item["after_title"],
                hunks=[
                    EditorStateRevisionBodyDiffHunkOut(
                        op=h["op"], text=h["text"]
                    )
                    for h in item["hunks"]
                ],
            )
        )
    return EditorStateRevisionBodyDiffOut(
        same_body=data["same_body"],
        changed_chapter_count=data["changed_chapter_count"],
        current_chapter_count=data["current_chapter_count"],
        target_chapter_count=data["target_chapter_count"],
        truncated=data["truncated"],
        items=items,
    )


@router.get(
    "/{project_id}/editor-state-revisions/{before_revision_id}/body-diff/{after_revision_id}",
    response_model=EditorStateRevisionPairBodyDiffOut,
)
def compare_editor_state_revision_bodies(
    project_id: str,
    before_revision_id: str,
    after_revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionPairBodyDiffOut:
    """
    用途：只读比较同一项目两条历史修订 chapters，返回有界行差异。
    对接：P12E-B；editor_state_revision_body_diff_service.compare_revision_bodies
    二次开发：
      - 两侧均经 C1 三重作用域与快照完整性重验；不读当前 editor-state；
      - 历史 404/corrupt 原样映射；其他失败固定 body_diff_failed；
      - 成功/业务错误 Cache-Control: no-store；响应禁止 ID/版本/原始快照。
    """
    _no_store(response)
    try:
        data = editor_state_revision_body_diff_service.compare_revision_bodies(
            db,
            workspace_id,
            project_id,
            before_revision_id,
            after_revision_id,
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    except EditorStateRevisionBodyDiffError as exc:
        _raise_body_diff_error(exc)

    items: list[EditorStateRevisionBodyDiffItemOut] = []
    for item in data["items"]:
        items.append(
            EditorStateRevisionBodyDiffItemOut(
                ordinal=item["ordinal"],
                kind=item["kind"],
                before_title=item["before_title"],
                after_title=item["after_title"],
                hunks=[
                    EditorStateRevisionBodyDiffHunkOut(
                        op=h["op"], text=h["text"]
                    )
                    for h in item["hunks"]
                ],
            )
        )
    return EditorStateRevisionPairBodyDiffOut(
        same_body=data["same_body"],
        changed_chapter_count=data["changed_chapter_count"],
        before_chapter_count=data["before_chapter_count"],
        after_chapter_count=data["after_chapter_count"],
        truncated=data["truncated"],
        items=items,
    )


@router.post(
    "/{project_id}/editor-state-revisions/{revision_id}/restore",
    response_model=EditorStateRevisionRestoreOut,
    status_code=200,
)
def restore_editor_state_revision(
    project_id: str,
    revision_id: str,
    body: EditorStateRevisionRestore,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionRestoreOut:
    """
    用途：原子恢复目标修订；先写恢复前安全检查点，再覆盖当前 13 键。
    对接：P12C-C2；服务层 restore_editor_state_revision。
    二次开发：body 仅 expectedStateVersion；409 复用全状态冲突协议；成功/业务错误 no-store。
    """
    _no_store(response)
    try:
        data = editor_state_revision_restore_service.restore_editor_state_revision(
            db,
            workspace_id,
            project_id,
            revision_id,
            body.expected_state_version,
        )
    except editor_state_service.EditorStateVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT,
                "message": exc.message,
                "currentStateVersion": exc.current_state_version,
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    except EditorStateRevisionRestoreError as exc:
        _raise_restore_error(exc)
    return EditorStateRevisionRestoreOut(
        safety_checkpoint_id=data["safety_checkpoint_id"],
        state_version=data["state_version"],
        restored_at=data["restored_at"],
    )
