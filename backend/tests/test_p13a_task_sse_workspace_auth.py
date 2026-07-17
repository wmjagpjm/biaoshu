"""
模块：P13-A 任务 SSE 工作空间鉴权专项测试
用途：验收单任务 SSE 复用 get_workspace_id 角色/成员语义，连接前短 Session 与流内 workspace 再校验。
对接：GET /api/projects/{projectId}/tasks/{taskId}/events；deps.get_workspace_id；task_service._read_task_snapshot。
二次开发：禁止放宽角色/成员边界或改用 URL token；事件游标、多任务总线须独立契约，不得并入本专项。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import tasks as tasks_api
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Project, ProjectTaskRow, Workspace
from app.services import auth_service, project_service, task_service
from app.services.project_service import ProjectNotFoundError


# 固定合成口令：仅测试夹具，禁止出现在业务配置或日志期望
_TEST_PASSWORD = "TestPass-P13A-0001!"
_TEST_USERNAME = "admin_p13a"
_ROLE_PASSWORDS = {
    "finance": "TestPass-P13A-Finance!",
    "hr": "TestPass-P13A-Hr!",
    "bidder": "TestPass-P13A-Bidder!",
    "bid_writer": "TestPass-P13A-Writer!",
}
_SECRET_MARKERS = (
    _TEST_PASSWORD,
    *_ROLE_PASSWORDS.values(),
    "password_hash",
    "token_digest",
    "csrf_digest",
)


def _assert_no_secrets(payload: object) -> None:
    """用途：响应不得回显口令、Cookie、CSRF 或摘要字段名/敏感值。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for marker in _SECRET_MARKERS:
        # password_hash/token_digest/csrf_digest 为字段名泄漏探针，不得跳过
        assert marker not in text


def _assert_error_code(response, status: int, code: str) -> dict:
    """用途：断言固定错误码形状，并确认无任务载荷泄漏。"""
    assert response.status_code == status
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict):
        assert detail.get("code") == code
    else:
        assert code in str(detail)
    text = json.dumps(body, ensure_ascii=False)
    for forbidden in ("snapshot", "progress", "result", "Set-Cookie", "csrfToken"):
        assert forbidden not in text
    # 响应头不得下发 Set-Cookie（鉴权失败不得刷新会话）
    header_names = {k.lower() for k in response.headers.keys()}
    assert "set-cookie" not in header_names
    # 任务业务字段不得出现在鉴权错误体
    assert "parsedMarkdown" not in text
    assert "stateVersion" not in text
    _assert_no_secrets(body)
    return body


def _read_sse_events(raw: str) -> list[tuple[str, dict]]:
    """用途：将 SSE 文本解析为事件名与 JSON 载荷。"""
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


@pytest.fixture
def required_settings(monkeypatch):
    """用途：切换 AUTH_MODE=required 并刷新配置缓存。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    """用途：required 模式下的 TestClient。"""
    with TestClient(app) as client:
        yield client


def _bootstrap(
    username: str = _TEST_USERNAME,
    password: str = _TEST_PASSWORD,
    *,
    role: str = auth_service.ROLE_BID_WRITER,
):
    """用途：创建首个本地管理员与默认空间成员。"""
    db = SessionLocal()
    try:
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=username,
            password=password,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str = _TEST_USERNAME, password: str = _TEST_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _csrf(body: dict) -> str:
    return body["csrfToken"]


def _login_as(client: TestClient, username: str, password: str):
    client.cookies.clear()
    res = _login(client, username=username, password=password)
    assert res.status_code == 200, res.text
    return res


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
):
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _events_url(project_id: str, task_id: str, *, query: str = "") -> str:
    base = f"/api/projects/{project_id}/tasks/{task_id}/events"
    return f"{base}?{query}" if query else base


def _create_terminal_parse_task(
    client: TestClient,
    csrf: str,
    *,
    name: str = "P13A 终态任务",
    headers: dict[str, str] | None = None,
) -> tuple[str, str]:
    """用途：在当前授权空间同步创建成功 parse 任务，供 SSE 立即读终态。"""
    hdrs: dict[str, str] = {"X-CSRF-Token": csrf}
    if headers:
        hdrs.update(headers)
    created = client.post("/api/projects", json={"name": name}, headers=hdrs)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    upload = client.post(
        f"/api/projects/{project_id}/files",
        files={"file": ("p13a.md", b"# P13A\n\nready", "text/markdown")},
        headers=hdrs,
    )
    assert upload.status_code == 201, upload.text
    task_res = client.post(
        f"/api/projects/{project_id}/tasks?sync=true",
        json={"type": "parse"},
        headers=hdrs,
    )
    assert task_res.status_code == 201, task_res.text
    task = task_res.json()
    assert task["status"] == "success", task
    return project_id, task["id"]


def _seed_terminal_task_in_workspace(
    workspace_id: str,
    *,
    project_name: str = "种子终态项目",
    task_suffix: str = "seed",
) -> tuple[str, str]:
    """用途：在已存在工作空间内直接入库终态任务（避免依赖 LLM/解析）。"""
    db = SessionLocal()
    try:
        project = project_service.create_project(
            db,
            workspace_id,
            name=project_name,
        )
        task = ProjectTaskRow(
            id=f"task_p13a_{task_suffix}",
            project_id=project.id,
            type="parse",
            status="success",
            progress=100,
            message="种子终态",
            payload_json="{}",
            result_json=json.dumps({"ok": True}, ensure_ascii=False),
            error=None,
        )
        db.add(task)
        db.commit()
        return project.id, task.id
    finally:
        db.close()


def _seed_workspace_with_terminal_task(
    *,
    workspace_id: str,
    workspace_name: str,
    owner_user_id: str,
    project_name: str = "跨空间任务项目",
    task_suffix: str | None = None,
) -> tuple[str, str]:
    """用途：直接入库创建工作空间、项目与终态任务（不经 HTTP 成员校验）。"""
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
            db.commit()
    finally:
        db.close()
    suffix = task_suffix or workspace_id.replace("ws_", "")[-12:]
    return _seed_terminal_task_in_workspace(
        workspace_id,
        project_name=project_name,
        task_suffix=suffix,
    )


# ---------------------------------------------------------------------------
# 契约 §6：鉴权闸门与角色/成员边界
# ---------------------------------------------------------------------------


def test_required_no_session_returns_401_auth_required(required_client):
    """required 无会话固定 401；?token 无效且不反射。"""
    _bootstrap()
    project_id, task_id = "proj_ghost", "task_ghost"
    # 先用所有者造真实任务，再无 Cookie 探测
    login = _login(required_client)
    csrf = _csrf(login.json())
    project_id, task_id = _create_terminal_parse_task(required_client, csrf)

    bare = TestClient(app)
    secret_token = "should-not-reflect-p13a-token-xyz"
    res = bare.get(_events_url(project_id, task_id, query=f"token={secret_token}"))
    body = _assert_error_code(res, 401, auth_service.CODE_AUTH_REQUIRED)
    text = json.dumps(body, ensure_ascii=False)
    assert secret_token not in text
    assert "should-not-reflect" not in text


@pytest.mark.parametrize("role", ["finance", "hr", "bidder"])
def test_required_non_bid_writer_roles_forbidden_on_known_task(required_client, role):
    """
    required 下 finance/hr/bidder 读取已知终态任务固定 403 role_forbidden。
    failure-first：旧实现错误返回 200。
    """
    _bootstrap()
    owner_login = _login(required_client)
    owner_csrf = _csrf(owner_login.json())
    project_id, task_id = _create_terminal_parse_task(
        required_client, owner_csrf, name=f"P13A {role} 探测"
    )

    username = f"p13a_{role}"
    password = _ROLE_PASSWORDS[role]
    created = _create_member(
        required_client,
        owner_csrf,
        username=username,
        password=password,
        role=role,
    )
    assert created.status_code == 201

    _login_as(required_client, username, password)
    res = required_client.get(_events_url(project_id, task_id))
    body = _assert_error_code(res, 403, auth_service.CODE_ROLE_FORBIDDEN)
    text = json.dumps(body, ensure_ascii=False)
    assert task_id not in text
    assert project_id not in text
    assert role not in text


def test_required_non_member_workspace_header_forbidden_before_resource(
    required_client,
):
    """
    required 非成员 X-Workspace-Id 固定 403 workspace_forbidden，且优先于资源 404。
    failure-first：旧实现对该空间已知任务错误返回 200。
    """
    principal = _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())

    foreign_ws = "ws_p13a_foreign"
    project_id, task_id = _seed_workspace_with_terminal_task(
        workspace_id=foreign_ws,
        workspace_name="非成员空间",
        owner_user_id="user_foreign_owner",
    )

    # 同空间合法任务也存在，确保失败不是因为任务缺失
    own_project, own_task = _create_terminal_parse_task(
        required_client, csrf, name="本空间对照"
    )
    assert own_task

    res = required_client.get(
        _events_url(project_id, task_id),
        headers={"X-Workspace-Id": foreign_ws},
    )
    body = _assert_error_code(res, 403, auth_service.CODE_WORKSPACE_FORBIDDEN)
    text = json.dumps(body, ensure_ascii=False)
    assert task_id not in text
    assert project_id not in text
    assert foreign_ws not in text
    # 不得因探测资源而变成 404
    assert res.status_code != 404
    assert principal.user_id


def test_required_default_bid_writer_sse_success(required_client):
    """required 默认空间 bid_writer 无头 SSE 成功，首帧 snapshot。"""
    _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())
    project_id, task_id = _create_terminal_parse_task(required_client, csrf)

    res = required_client.get(_events_url(project_id, task_id))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _read_sse_events(res.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task_id
    assert events[0][1]["status"] == "success"
    # 公开载荷不得含 workspace/用户/角色
    snap = events[0][1]
    for banned in ("workspaceId", "workspace", "userId", "role", "csrfToken"):
        assert banned not in snap


def test_required_active_second_member_workspace_headerless_success(required_client):
    """
    已切换到第二成员空间的 bid_writer 无头 SSE 使用 activeWorkspaceId 成功。
    failure-first：旧实现错误使用默认空间而 404。
    """
    principal = _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())

    second_ws = "ws_p13a_second"
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=second_ws,
                name="第二成员空间",
                owner_user_id=principal.user_id,
            )
        )
        db.flush()
        auth_service.add_member(
            db,
            workspace_id=second_ws,
            user_id=principal.user_id,
            role=auth_service.ROLE_BID_WRITER,
            is_owner=True,
        )
        db.commit()
    finally:
        db.close()

    # 在第二空间创建终态任务
    project_id, task_id = _create_terminal_parse_task(
        required_client,
        csrf,
        name="第二空间任务",
        headers={"X-Workspace-Id": second_ws},
    )

    switched = required_client.put(
        "/api/auth/active-workspace",
        json={"workspaceId": second_ws},
        headers={"X-CSRF-Token": csrf},
    )
    assert switched.status_code == 200
    assert switched.json()["activeWorkspaceId"] == second_ws

    # 无头：应走 active workspace，而非默认 ws_local
    res = required_client.get(_events_url(project_id, task_id))
    assert res.status_code == 200, res.text
    events = _read_sse_events(res.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task_id
    assert events[0][1]["status"] == "success"


def test_required_explicit_member_workspace_header_success(required_client):
    """成员内显式 X-Workspace-Id 选择第二空间成功。"""
    principal = _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())

    second_ws = "ws_p13a_explicit"
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=second_ws,
                name="显式成员空间",
                owner_user_id=principal.user_id,
            )
        )
        db.flush()
        auth_service.add_member(
            db,
            workspace_id=second_ws,
            user_id=principal.user_id,
            role=auth_service.ROLE_BID_WRITER,
            is_owner=True,
        )
        db.commit()
    finally:
        db.close()

    project_id, task_id = _create_terminal_parse_task(
        required_client,
        csrf,
        name="显式头空间任务",
        headers={"X-Workspace-Id": second_ws},
    )

    # active 仍为默认；显式头选择成员空间
    res = required_client.get(
        _events_url(project_id, task_id),
        headers={"X-Workspace-Id": second_ws},
    )
    assert res.status_code == 200
    events = _read_sse_events(res.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task_id


def test_required_authorized_cross_space_project_task_404(required_client):
    """已授权默认空间内，跨空间项目/任务统一 404，零快照泄漏。"""
    principal = _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())
    own_project, own_task = _create_terminal_parse_task(required_client, csrf)

    foreign_ws = "ws_p13a_cross"
    foreign_project, foreign_task = _seed_workspace_with_terminal_task(
        workspace_id=foreign_ws,
        workspace_name="跨空间探测",
        owner_user_id=principal.user_id,
    )

    # 用默认授权空间访问外空间资源 ID → 404 项目不存在（不得 200）
    res = required_client.get(_events_url(foreign_project, foreign_task))
    assert res.status_code == 404
    detail = res.json()["detail"]
    assert detail == "项目不存在"
    text = json.dumps(res.json(), ensure_ascii=False)
    assert foreign_task not in text
    assert "snapshot" not in text
    assert own_task  # 本空间任务存在，对照未泄漏

    # 本空间项目 + 外空间任务 id → 404 任务不存在
    res2 = required_client.get(_events_url(own_project, foreign_task))
    assert res2.status_code == 404
    assert res2.json()["detail"] == "任务不存在"


def test_disabled_default_and_explicit_workspace_sse_success(client):
    """disabled 默认空间与合法显式工作空间仍成功。"""
    # 默认空间
    project = client.post("/api/projects", json={"name": "P13A disabled 默认"}).json()
    project_id = project["id"]
    client.post(
        f"/api/projects/{project_id}/files",
        files={"file": ("d.md", b"# d\n", "text/markdown")},
    )
    task = client.post(
        f"/api/projects/{project_id}/tasks?sync=true",
        json={"type": "parse"},
    ).json()
    assert task["status"] == "success"

    res = client.get(_events_url(project_id, task["id"]))
    assert res.status_code == 200
    events = _read_sse_events(res.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task["id"]

    # 显式工作空间：先建空间与项目任务
    other_ws = "ws_p13a_disabled_other"
    other_project, other_task = _seed_workspace_with_terminal_task(
        workspace_id=other_ws,
        workspace_name="disabled 显式空间",
        owner_user_id="user_test",
    )
    res2 = client.get(
        _events_url(other_project, other_task),
        headers={"X-Workspace-Id": other_ws},
    )
    assert res2.status_code == 200
    events2 = _read_sse_events(res2.text)
    assert events2[0][0] == "snapshot"
    assert events2[0][1]["id"] == other_task


# ---------------------------------------------------------------------------
# Session 生命周期与流内 workspace 再校验
# ---------------------------------------------------------------------------


class _TrackingSession:
    """用途：包装真实 Session，记录 close 调用，供生命周期断言。"""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self._real.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_connect_session_closed_before_first_stream_snapshot(
    required_client, monkeypatch
):
    """连接前 Session 在首个流内快照读取前已关闭；生成器每次传授权 workspace。"""
    _bootstrap()
    login = _login(required_client)
    csrf = _csrf(login.json())
    project_id, task_id = _create_terminal_parse_task(required_client, csrf)

    real_local = SessionLocal
    auth_sessions: list[_TrackingSession] = []
    stream_sessions: list[_TrackingSession] = []
    phase = {"name": "auth"}
    snapshot_calls: list[tuple] = []

    def factory() -> _TrackingSession:
        wrapped = _TrackingSession(real_local())
        if phase["name"] == "auth":
            auth_sessions.append(wrapped)
        else:
            stream_sessions.append(wrapped)
        return wrapped

    monkeypatch.setattr(tasks_api, "SessionLocal", factory)
    monkeypatch.setattr(task_service, "SessionLocal", factory)

    real_read = task_service._read_task_snapshot

    def tracking_read(*args, **kwargs):
        phase["name"] = "stream"
        # 首帧读取前：所有连接前会话必须已关闭
        assert auth_sessions, "连接前应至少打开过一次 Session"
        assert all(s.closed for s in auth_sessions), (
            "连接前 Session 必须在首个流内快照前关闭"
        )
        snapshot_calls.append((args, kwargs))
        return real_read(*args, **kwargs)

    monkeypatch.setattr(task_service, "_read_task_snapshot", tracking_read)
    # 路由通过 task_service 模块属性调用
    monkeypatch.setattr(tasks_api.task_service, "_read_task_snapshot", tracking_read)

    res = required_client.get(_events_url(project_id, task_id))
    assert res.status_code == 200
    assert snapshot_calls, "生成器应至少调用一次快照读取"
    # 每轮精确位置参数，拒绝额外位置/关键字参数
    for args, kwargs in snapshot_calls:
        assert args == ("ws_local", project_id, task_id)
        assert kwargs == {}
    # 流内短会话最终关闭
    assert stream_sessions
    assert all(s.closed for s in stream_sessions)


def test_read_task_snapshot_same_space_success_cross_space_none_and_closes():
    """
    快照 helper 直测：同空间返回公开快照；跨空间 None；每次 Session 关闭。
    """
    db = SessionLocal()
    try:
        project = project_service.create_project(db, "ws_local", name="快照直测")
        task = task_service.create_task_record(
            db, "ws_local", project.id, task_type="export"
        )
        task_id = task.id
        project_id = project.id
        # 标为终态，避免无关副作用
        task_service._set_task(
            db, task, status="success", progress=100, message="直测完成"
        )
    finally:
        db.close()

    real_local = SessionLocal
    tracked: list[_TrackingSession] = []

    def factory() -> _TrackingSession:
        wrapped = _TrackingSession(real_local())
        tracked.append(wrapped)
        return wrapped

    # 直接替换服务模块 SessionLocal
    original = task_service.SessionLocal
    task_service.SessionLocal = factory  # type: ignore[assignment]
    try:
        ok = task_service._read_task_snapshot("ws_local", project_id, task_id)
        assert ok is not None
        assert ok["id"] == task_id
        assert ok["status"] == "success"
        for banned in ("workspaceId", "password", "csrfToken"):
            assert banned not in ok
        assert tracked and tracked[-1].closed

        # 跨空间：项目不在该 workspace
        before = len(tracked)
        cross = task_service._read_task_snapshot("ws_other_no_access", project_id, task_id)
        assert cross is None
        assert len(tracked) > before
        assert tracked[-1].closed

        # 任务不匹配
        before2 = len(tracked)
        missing = task_service._read_task_snapshot("ws_local", project_id, "task_no_such")
        assert missing is None
        assert len(tracked) > before2
        assert tracked[-1].closed
    finally:
        task_service.SessionLocal = original  # type: ignore[assignment]


def test_read_task_snapshot_project_not_found_returns_none():
    """ProjectNotFoundError 路径统一返回 None。"""
    result = task_service._read_task_snapshot("ws_local", "proj_missing_p13a", "task_x")
    assert result is None
    # 确认 get_project 语义仍抛 ProjectNotFoundError（对照）
    db = SessionLocal()
    try:
        with pytest.raises(ProjectNotFoundError):
            project_service.get_project(db, "ws_local", "proj_missing_p13a")
    finally:
        db.close()
