"""
模块：P13-I4 项目任务状态安全对账后端专项测试
用途：failure-first 验收 GET /api/projects/{projectId}/tasks/{taskId}/status
  真实 HTTP 路由、精确三键 taskId/status/progress、Cache-Control: no-store、
  required/disabled 鉴权作用域、跨项目/跨 workspace、非法 query/body 与敏感字段零泄漏。
对接：tasks.get_task_status；schemas.ProjectTaskStatusOut；
  task_service.task_status_projection / get_task。
二次开发：禁止只测 service 函数冒充路由通过；跨 workspace 必须真实第二空间；
  禁止 skip/xfail/sleep/并发分组；禁止把任务详情 GET 当状态对账。
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    Project,
    ProjectTaskRow,
    Workspace,
    WorkspaceMemberRow,
)
from app.services import auth_service, project_service, task_service

_WS = "ws_local"
_TASK_RE = re.compile(r"^task_[0-9a-f]+$")
_STATUS_KEYS = frozenset({"taskId", "status", "progress"})
_STATUSES = frozenset({"pending", "running", "success", "failed", "cancelled"})
_TEST_USERNAME = "admin_p13i4"
_TEST_PASSWORD = "P13i4-Test-Pass-9!"
_SECRET_MSG = "SECRET_P13I4_MSG_MUST_NOT_LEAK"
_SECRET_ERR = "SECRET_P13I4_ERR_PATH_C:/leak/secret.bin"
_SECRET_RESULT = {"secretPath": "C:/leak/result.bin", "token": "tok_p13i4_leak"}
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _SECRET_MSG,
    _SECRET_ERR,
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
    "result_json",
    "payload_json",
    "actor_user_id",
    "actorUserId",
    "clientId",
    "client_id",
    "workspace_id",
    "workspaceId",
    "projectId",
    "project_id",
    "parsedMarkdown",
    "stateVersion",
    "C:/leak",
    "tok_p13i4_leak",
)


# ---------- 工具 ----------


def _status_url(project_id: str, task_id: str, *, query: str = "") -> str:
    base = f"/api/projects/{project_id}/tasks/{task_id}/status"
    return f"{base}?{query}" if query else base


def _assert_no_secrets(payload: object) -> None:
    text_blob = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    low = text_blob.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text_blob, f"敏感标记泄漏: {marker}"
        assert marker.lower() not in low, f"敏感标记泄漏: {marker}"
    for banned in (
        "result_json",
        "payload_json",
        "actoruserid",
        "actor_user_id",
        "clientid",
        "password",
        "token_digest",
        "csrf_digest",
        "traceback",
        "sqlalchemy",
        "operationalerror",
        "exception",
        "stack",
    ):
        assert banned not in low.replace("-", "_"), f"敏感/内部标记泄漏: {banned}"


def _assert_no_store(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store", response.headers


def _assert_status_shape(
    body: dict[str, Any], *, task_id: str | None = None
) -> None:
    assert set(body.keys()) == _STATUS_KEYS, body
    assert isinstance(body["taskId"], str) and body["taskId"]
    if task_id is not None:
        assert body["taskId"] == task_id
    assert _TASK_RE.fullmatch(body["taskId"]) or body["taskId"].startswith("task_"), body[
        "taskId"
    ]
    assert body["status"] in _STATUSES
    assert isinstance(body["progress"], int)
    assert not isinstance(body["progress"], bool)
    assert 0 <= body["progress"] <= 100
    for banned in (
        "message",
        "error",
        "result",
        "payload",
        "type",
        "id",
        "projectId",
        "workspaceId",
        "createdAt",
        "updatedAt",
        "actorUserId",
        "actor_user_id",
    ):
        assert banned not in body
    _assert_no_secrets(body)


def _seed_second_workspace_project(
    *,
    workspace_id: str,
    workspace_name: str,
    owner_user_id: str,
    member_user_id: str,
    project_name: str = "跨空间项目",
) -> str:
    """用途：真实第二 workspace + 项目；禁止同空间第二项目冒充跨空间。"""
    db = SessionLocal()
    try:
        if db.get(Workspace, workspace_id) is None:
            db.add(
                Workspace(
                    id=workspace_id,
                    name=workspace_name,
                    owner_user_id=owner_user_id,
                )
            )
        existing = (
            db.query(WorkspaceMemberRow)
            .filter(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == member_user_id,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(
                WorkspaceMemberRow(
                    id=f"wm_{secrets.token_hex(8)}",
                    workspace_id=workspace_id,
                    user_id=member_user_id,
                    role=auth_service.ROLE_BID_WRITER,
                    is_owner=(member_user_id == owner_user_id),
                    is_active=True,
                )
            )
        project = project_service.create_project(
            db,
            workspace_id,
            name=project_name,
        )
        db.commit()
        return project.id
    finally:
        db.close()


def _create_task(
    project_id: str,
    *,
    task_type: str = "export",
    workspace_id: str = _WS,
    payload: dict | None = None,
    actor_user_id: str | None = None,
) -> ProjectTaskRow:
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            workspace_id,
            project_id,
            task_type=task_type,
            payload=payload or {},
            actor_user_id=actor_user_id,
        )
        db.expunge(task)
        return task
    finally:
        db.close()


def _set_task_fields(
    task_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task_id)
        assert row is not None
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status
        if progress is not None:
            kwargs["progress"] = progress
        if message is not None:
            kwargs["message"] = message
        if error is not None:
            kwargs["error"] = error
        if result is not None:
            kwargs["result"] = result
        task_service._set_task(db, row, force=True, **kwargs)
    finally:
        db.close()


# ---------- fixtures ----------


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(disabled_settings):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def required_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    with TestClient(app) as client:
        yield client


def _bootstrap_and_login(client: TestClient) -> tuple[str, str]:
    from app.models.entities import LocalUserRow

    db = SessionLocal()
    try:
        existing = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one_or_none()
        )
        if existing is None:
            try:
                auth_service.bootstrap_local_admin(
                    db,
                    get_settings(),
                    username=_TEST_USERNAME,
                    password=_TEST_PASSWORD,
                    role=auth_service.ROLE_BID_WRITER,
                )
            except Exception:
                db.rollback()
        row = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one()
        )
        user_id = row.id
    finally:
        db.close()
    client.cookies.clear()
    res = client.post(
        "/api/auth/login",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
    )
    assert res.status_code == 200, res.text
    csrf = res.json()["csrfToken"]
    return user_id, csrf


def _create_project_http(
    client: TestClient, csrf: str | None = None, name: str = "P13I4项目"
) -> str:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
    is_owner: bool = False,
):
    res = client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------- failure-first / 路由存在性 ----------


def test_status_route_exists_and_three_keys(disabled_client):
    """用途：真实 HTTP 路由返回 200，精确三键，no-store。"""
    pid = _create_project_http(disabled_client, name="i4-route")
    task = _create_task(pid, task_type="export")
    res = disabled_client.get(_status_url(pid, task.id))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_status_shape(body, task_id=task.id)
    assert body["status"] == "pending"
    assert body["progress"] == 0


def test_status_not_confuse_with_task_detail(disabled_client):
    """用途：状态接口不得回退为任务详情形态（多键/敏感字段）。"""
    pid = _create_project_http(disabled_client, name="i4-vs-detail")
    task = _create_task(pid, task_type="export")
    _set_task_fields(
        task.id,
        status="running",
        progress=42,
        message=_SECRET_MSG,
        error=_SECRET_ERR,
        result=_SECRET_RESULT,
    )
    detail = disabled_client.get(f"/api/projects/{pid}/tasks/{task.id}")
    assert detail.status_code == 200, detail.text
    dbody = detail.json()
    assert "message" in dbody
    assert dbody.get("message") == _SECRET_MSG

    status_res = disabled_client.get(_status_url(pid, task.id))
    assert status_res.status_code == 200, status_res.text
    _assert_no_store(status_res)
    sbody = status_res.json()
    _assert_status_shape(sbody, task_id=task.id)
    assert sbody["status"] == "running"
    assert sbody["progress"] == 42
    assert _SECRET_MSG not in status_res.text
    assert _SECRET_ERR not in status_res.text
    assert "tok_p13i4_leak" not in status_res.text
    assert "message" not in sbody
    assert "error" not in sbody
    assert "result" not in sbody
    assert "payload" not in sbody


# ---------- 状态 / 进度边界 ----------


@pytest.mark.parametrize(
    "status,progress",
    [
        ("pending", 0),
        ("running", 1),
        ("running", 50),
        ("running", 99),
        ("success", 100),
        ("failed", 100),
        ("cancelled", 0),
        ("cancelled", 37),
    ],
)
def test_status_progress_boundaries(disabled_client, status, progress):
    """用途：五态与 0–100 进度经真实路由投影。"""
    pid = _create_project_http(disabled_client, name=f"i4-{status}-{progress}")
    task = _create_task(pid, task_type="export")
    _set_task_fields(
        task.id,
        status=status,
        progress=progress,
        message=_SECRET_MSG,
        error=_SECRET_ERR if status == "failed" else None,
        result=_SECRET_RESULT if status == "success" else None,
    )
    res = disabled_client.get(_status_url(pid, task.id))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_status_shape(body, task_id=task.id)
    assert body["status"] == status
    assert body["progress"] == progress


def test_service_projection_independent_of_task_to_dict(disabled_client):
    """用途：独立安全投影不得依赖 task_to_dict 宽字段。"""
    pid = _create_project_http(disabled_client, name="i4-proj-fn")
    task = _create_task(pid, task_type="export")
    _set_task_fields(
        task.id,
        status="running",
        progress=7,
        message=_SECRET_MSG,
        error=_SECRET_ERR,
        result=_SECRET_RESULT,
    )
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task.id)
        assert row is not None
        wide = task_service.task_to_dict(row)
        assert "message" in wide
        proj = task_service.task_status_projection(row)
    finally:
        db.close()
    assert set(proj.keys()) == {"taskId", "status", "progress"}
    assert proj["taskId"] == task.id
    assert proj["status"] == "running"
    assert proj["progress"] == 7
    assert _SECRET_MSG not in json.dumps(proj, ensure_ascii=False)


# ---------- disabled 兼容 ----------


def test_disabled_default_and_explicit_workspace_header(disabled_client):
    """用途：disabled 默认空间与合法显式 X-Workspace-Id 均可读状态。"""
    pid = _create_project_http(disabled_client, name="i4-disabled-default")
    task = _create_task(pid, task_type="export")
    res = disabled_client.get(_status_url(pid, task.id))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    _assert_status_shape(res.json(), task_id=task.id)

    other_ws = f"ws_p13i4_dis_{secrets.token_hex(3)}"
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=other_ws,
                name="disabled 显式空间",
                owner_user_id="user_test",
            )
        )
        db.commit()
        project = project_service.create_project(db, other_ws, name="i4-dis-other")
        db.commit()
        other_pid = project.id
        other_task = task_service.create_task_record(
            db, other_ws, other_pid, task_type="export", payload={}
        )
        other_tid = other_task.id
    finally:
        db.close()

    res2 = disabled_client.get(
        _status_url(other_pid, other_tid),
        headers={"X-Workspace-Id": other_ws},
    )
    assert res2.status_code == 200, res2.text
    _assert_no_store(res2)
    _assert_status_shape(res2.json(), task_id=other_tid)


# ---------- required 鉴权矩阵 ----------


def test_required_no_session_401(required_client):
    """用途：required 无会话固定 401；错误体无任务载荷。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i4-unauth")
    task = _create_task(pid, task_type="export", actor_user_id=user_id)
    required_client.cookies.clear()
    res = required_client.get(_status_url(pid, task.id))
    assert res.status_code == 401, res.text
    # 认证中间件统一 no-store
    assert res.headers.get("Cache-Control", "").lower() == "no-store"
    detail = res.json().get("detail") or res.json()
    assert isinstance(detail, dict)
    assert detail.get("code") == auth_service.CODE_AUTH_REQUIRED
    assert pid not in res.text
    assert task.id not in res.text
    _assert_no_secrets(res.text)


@pytest.mark.parametrize(
    "role",
    [
        auth_service.ROLE_FINANCE,
        auth_service.ROLE_HR,
        auth_service.ROLE_BIDDER,
    ],
)
def test_required_non_bid_writer_403(required_client, role):
    """用途：非 bid_writer 读取已知任务状态固定 403 role_forbidden。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name=f"i4-{role}")
    task = _create_task(pid, task_type="export")
    username = f"{role}_p13i4_{secrets.token_hex(3)}"
    password = f"P13i4-{role}-Pass!"
    _create_member(
        required_client,
        csrf,
        username=username,
        password=password,
        role=role,
    )
    required_client.cookies.clear()
    login = required_client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    res = required_client.get(_status_url(pid, task.id))
    assert res.status_code == 403, res.text
    detail = res.json().get("detail") or res.json()
    if isinstance(detail, dict):
        assert detail.get("code") == auth_service.CODE_ROLE_FORBIDDEN
    assert task.id not in res.text
    assert _SECRET_MSG not in res.text
    _assert_no_secrets(res.text)


def test_required_non_member_workspace_header_403(required_client):
    """用途：非成员 X-Workspace-Id 固定 403，优先于资源 404。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i4-nonmember")
    task = _create_task(pid, task_type="export", actor_user_id=user_id)
    foreign_ws = f"ws_foreign_i4_{secrets.token_hex(3)}"
    res = required_client.get(
        _status_url(pid, task.id),
        headers={"X-Workspace-Id": foreign_ws},
    )
    assert res.status_code == 403, res.text
    detail = res.json().get("detail") or res.json()
    if isinstance(detail, dict):
        assert detail.get("code") in (
            auth_service.CODE_WORKSPACE_FORBIDDEN,
            auth_service.CODE_ROLE_FORBIDDEN,
        )
    assert foreign_ws not in res.text
    assert task.id not in res.text
    _assert_no_secrets(res.text)


def test_required_bid_writer_success_no_store(required_client):
    """用途：required 默认空间 bid_writer 成功；三键 + no-store。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i4-writer-ok")
    task = _create_task(pid, task_type="export", actor_user_id=user_id)
    _set_task_fields(
        task.id,
        status="running",
        progress=33,
        message=_SECRET_MSG,
        error=_SECRET_ERR,
        result=_SECRET_RESULT,
    )
    res = required_client.get(_status_url(pid, task.id))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_status_shape(body, task_id=task.id)
    assert body["status"] == "running"
    assert body["progress"] == 33


def test_required_explicit_member_workspace_header_success(required_client):
    """用途：成员显式 X-Workspace-Id 可读本空间任务状态。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    second_ws = f"ws_i4_mem_{secrets.token_hex(3)}"
    foreign_pid = _seed_second_workspace_project(
        workspace_id=second_ws,
        workspace_name="I4 成员第二空间",
        owner_user_id=user_id,
        member_user_id=user_id,
        project_name="i4-second-ws",
    )
    task = _create_task(
        foreign_pid, workspace_id=second_ws, task_type="export", actor_user_id=user_id
    )
    res = required_client.get(
        _status_url(foreign_pid, task.id),
        headers={"X-Workspace-Id": second_ws},
    )
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    _assert_status_shape(res.json(), task_id=task.id)


# ---------- 跨项目 / 跨 workspace ----------


def test_cross_project_task_404(disabled_client):
    """用途：任务属于另一项目时 404，不泄漏任务状态。"""
    p1 = _create_project_http(disabled_client, name="i4-cross-p1")
    p2 = _create_project_http(disabled_client, name="i4-cross-p2")
    t1 = _create_task(p1, task_type="export")
    _set_task_fields(t1.id, status="running", progress=55, message=_SECRET_MSG)
    res = disabled_client.get(_status_url(p2, t1.id))
    assert res.status_code == 404, res.text
    _assert_no_store(res)
    # 跨项目 404 不得回显 taskId，也不得以成功态三键形态泄漏
    assert t1.id not in res.text
    err_body = res.json()
    assert "taskId" not in err_body
    assert "status" not in err_body
    assert "progress" not in err_body
    detail = err_body.get("detail") if isinstance(err_body, dict) else None
    if isinstance(detail, dict):
        assert "taskId" not in detail
        assert "status" not in detail
        assert "progress" not in detail
        assert detail.get("taskId") != t1.id
    assert "running" not in res.text
    assert "55" not in res.text
    assert _SECRET_MSG not in res.text
    assert "项目不存在" in res.text or "任务不存在" in res.text
    _assert_no_secrets(res.text)


def test_cross_workspace_real_second_space_404(required_client):
    """用途：真实第二 workspace 任务用当前空间请求 → 404；禁止同空间第二项目冒充。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    own_pid = _create_project_http(required_client, csrf, name="i4-own")
    own_task = _create_task(own_pid, task_type="export", actor_user_id=user_id)

    foreign_ws = f"ws_i4_x_{secrets.token_hex(3)}"
    foreign_pid = _seed_second_workspace_project(
        workspace_id=foreign_ws,
        workspace_name="I4 跨空间真实第二",
        owner_user_id=user_id,
        member_user_id=user_id,
        project_name="i4-foreign",
    )
    foreign_task = _create_task(
        foreign_pid,
        workspace_id=foreign_ws,
        task_type="export",
        actor_user_id=user_id,
    )
    _set_task_fields(
        foreign_task.id,
        status="running",
        progress=88,
        message=_SECRET_MSG,
    )

    # 无头（活动默认空间）读跨空间项目/任务
    res = required_client.get(_status_url(foreign_pid, foreign_task.id))
    assert res.status_code == 404, res.text
    _assert_no_store(res)
    assert "running" not in res.text
    assert "88" not in res.text
    assert _SECRET_MSG not in res.text
    assert foreign_ws not in res.text
    _assert_no_secrets(res.text)

    # 本空间项目 + 跨空间任务 id
    res2 = required_client.get(_status_url(own_pid, foreign_task.id))
    assert res2.status_code == 404, res2.text
    _assert_no_store(res2)
    assert "running" not in res2.text
    _assert_no_secrets(res2.text)

    # 对照：本空间合法任务仍 200
    ok = required_client.get(_status_url(own_pid, own_task.id))
    assert ok.status_code == 200, ok.text
    _assert_status_shape(ok.json(), task_id=own_task.id)


def test_missing_project_and_task_404(disabled_client):
    """用途：不存在项目/任务固定 404，错误体不回显路径与 SQL。"""
    pid = _create_project_http(disabled_client, name="i4-missing")
    task = _create_task(pid, task_type="export")

    missing_proj = disabled_client.get(
        _status_url("proj_does_not_exist_p13i4", task.id)
    )
    assert missing_proj.status_code == 404, missing_proj.text
    _assert_no_store(missing_proj)
    assert "proj_does_not_exist_p13i4" not in missing_proj.text
    assert "SELECT" not in missing_proj.text.upper()
    _assert_no_secrets(missing_proj.text)

    missing_task = disabled_client.get(
        _status_url(pid, f"task_{'a' * 32}")
    )
    assert missing_task.status_code == 404, missing_task.text
    _assert_no_store(missing_task)
    assert "任务不存在" in missing_task.text
    _assert_no_secrets(missing_task.text)


# ---------- 非法 query / body ----------


def test_illegal_query_and_body_422(disabled_client):
    """用途：任何 query、带 body 固定 422 + no-store，不回显输入。"""
    pid = _create_project_http(disabled_client, name="i4-qbody")
    task = _create_task(pid, task_type="export")
    url = _status_url(pid, task.id)
    invalid_code = "project_task_status_request_invalid"
    invalid_message = "请求无效"

    def _assert_request_invalid_422(res) -> dict[str, Any]:
        """用途：对齐固定 422 外层/内层键与 code/message + no-store。"""
        assert res.status_code == 422, res.text
        _assert_no_store(res)
        body = res.json()
        assert set(body.keys()) == {"detail"}, body
        detail = body["detail"]
        assert isinstance(detail, dict), res.text
        assert set(detail.keys()) == {"code", "message"}, detail
        assert detail["code"] == invalid_code
        assert detail["message"] == invalid_message
        return body

    res_q = disabled_client.get(f"{url}?token=abc&sync=1")
    body_q = _assert_request_invalid_422(res_q)
    detail_q = body_q["detail"]
    # query 输入键值不回显（token/abc/sync 不在固定 code 中，可做原文断言）
    assert "token" not in detail_q
    assert "sync" not in detail_q
    assert "token" not in res_q.text
    assert "abc" not in res_q.text
    assert "sync" not in res_q.text
    _assert_no_secrets(res_q.text)

    res_q2 = disabled_client.get(f"{url}?progress=1")
    body_q2 = _assert_request_invalid_422(res_q2)
    detail_q2 = body_q2["detail"]
    assert "progress" not in detail_q2
    assert "progress" not in res_q2.text
    _assert_no_secrets(res_q2.text)

    res_body = disabled_client.request(
        "GET",
        url,
        content=b'{"status":"running","secret":"leak"}',
        headers={"Content-Type": "application/json"},
    )
    body = _assert_request_invalid_422(res_body)
    detail = body["detail"]
    # 请求输入键/值 status、running、secret、leak 均不回显。
    # 固定 code 自身含 "status" 子串：仅对 JSON 字段名与字段值做结构化区分，
    # 禁止 assert "status" not in res_body.text 这类会被合法 code 误伤的全文断言。
    for banned_key in ("status", "secret", "running", "leak"):
        assert banned_key not in body
        assert banned_key not in detail
    for field_val in (detail["code"], detail["message"]):
        assert field_val not in ("status", "running", "secret", "leak")
    # running/secret/leak 不在固定 code/message 字面量中，可直接原文断言
    assert "running" not in res_body.text
    assert "secret" not in res_body.text
    assert "leak" not in res_body.text
    _assert_no_secrets(res_body.text)


def test_url_token_query_rejected(required_client):
    """用途：URL token 查询不得作为鉴权旁路，固定 422。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i4-url-token")
    task = _create_task(pid, task_type="export", actor_user_id=user_id)
    # 有会话但带 token query 仍应拒绝
    res = required_client.get(_status_url(pid, task.id, query="token=forged_token_xyz"))
    assert res.status_code == 422, res.text
    _assert_no_store(res)
    assert "forged_token_xyz" not in res.text
    _assert_no_secrets(res.text)


# ---------- 隐私门 / actor 不泄漏 ----------


def test_privacy_no_sensitive_fields_even_with_actor(required_client):
    """用途：成功响应无 message/error/result/payload/actor/workspace/project。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i4-privacy")
    task = _create_task(pid, task_type="export", actor_user_id=user_id)
    _set_task_fields(
        task.id,
        status="failed",
        progress=100,
        message=_SECRET_MSG,
        error=_SECRET_ERR,
        result=_SECRET_RESULT,
    )
    # 确认 DB 行确有敏感列
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task.id)
        assert row is not None
        assert row.message == _SECRET_MSG
        assert row.error == _SECRET_ERR
        assert row.actor_user_id == user_id
        assert row.result_json is not None
    finally:
        db.close()

    res = required_client.get(_status_url(pid, task.id))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_status_shape(body, task_id=task.id)
    assert body["status"] == "failed"
    assert body["progress"] == 100
    raw = res.text
    assert user_id not in raw
    assert _SECRET_MSG not in raw
    assert _SECRET_ERR not in raw
    assert "tok_p13i4_leak" not in raw
    assert "message" not in body
    assert "error" not in body
    assert "result" not in body
    assert "payload" not in body
    assert "actorUserId" not in body
    assert "workspaceId" not in body
    assert "projectId" not in body
    _assert_no_secrets(body)
    _assert_no_secrets(raw)


def test_error_responses_no_stack_or_ids_leak(disabled_client):
    """用途：业务错误不泄漏栈、SQL、内部路径。"""
    res = disabled_client.get(
        _status_url("proj_x", f"task_{'b' * 32}")
    )
    assert res.status_code == 404, res.text
    _assert_no_store(res)
    low = res.text.lower()
    for banned in ("traceback", "sqlalchemy", "sqlite", "file://", "c:\\"):
        assert banned not in low
    _assert_no_secrets(res.text)
