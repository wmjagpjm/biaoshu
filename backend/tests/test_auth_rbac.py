"""
模块：P10A 本机身份与 RBAC 定向测试
用途：验收 auth_mode=required 会话、Cookie/CSRF、工作空间成员校验与 disabled 兼容。
对接：app.api.auth、auth_middleware、auth_service、deps.get_workspace_id。
二次开发：仅使用固定合成口令；断言不得依赖真实业务口令或外网。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import AuthSessionRow, Project, Workspace
from app.services import auth_service


# 固定合成口令：仅测试夹具使用，禁止出现在业务配置或日志期望中
_TEST_PASSWORD = "TestPass-Auth-0001!"
_TEST_USERNAME = "admin_local"
_WRONG_PASSWORD = "TestPass-Auth-WRONG!"
_SECRET_MARKERS = (
    _TEST_PASSWORD,
    _WRONG_PASSWORD,
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
)


def _assert_no_secrets(payload: object) -> None:
    """用途：响应/审计文本不得回显口令、Cookie 或摘要字段名的敏感值。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for marker in (_TEST_PASSWORD, _WRONG_PASSWORD):
        assert marker not in text


@pytest.fixture
def required_settings(monkeypatch):
    """用途：切换为 required 模式并刷新配置缓存。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    """用途：required 模式下的 TestClient（走 lifespan）。"""
    with TestClient(app) as client:
        yield client


def _bootstrap(
    username: str = _TEST_USERNAME,
    password: str = _TEST_PASSWORD,
    *,
    role: str = auth_service.ROLE_BID_WRITER,
) -> auth_service.AuthPrincipal:
    """用途：在测试库创建首个本地管理员与默认空间成员。"""
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


def _login(
    client: TestClient,
    username: str = _TEST_USERNAME,
    password: str = _TEST_PASSWORD,
):
    """用途：执行登录并返回响应。"""
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def _csrf_from_login(body: dict) -> str:
    return body["csrfToken"]


def test_uninitialized_business_api_returns_503(required_client):
    """未初始化时业务 API 固定 503；伪造工作空间头不可绕过。"""
    status = required_client.get("/api/auth/bootstrap-status")
    assert status.status_code == 200
    assert status.json()["bootstrapped"] is False

    forged = required_client.get(
        "/api/projects",
        headers={"X-Workspace-Id": "ws_local"},
    )
    assert forged.status_code == 503
    detail = forged.json()["detail"]
    code = detail["code"] if isinstance(detail, dict) else detail
    assert "bootstrap" in str(code).lower() or "not_bootstrapped" in str(code).lower()
    _assert_no_secrets(forged.json())

    health = required_client.get("/api/health")
    assert health.status_code == 200


def test_wrong_credentials_same_401_and_no_secret_leak(required_client):
    """错误用户名与错误口令同为 401；响应与审计不含口令/摘要。"""
    _bootstrap()
    r1 = _login(required_client, username="no_such_user", password=_WRONG_PASSWORD)
    r2 = _login(required_client, username=_TEST_USERNAME, password=_WRONG_PASSWORD)
    assert r1.status_code == 401
    assert r2.status_code == 401
    d1 = r1.json()["detail"]
    d2 = r2.json()["detail"]
    assert d1 == d2
    _assert_no_secrets(r1.json())
    _assert_no_secrets(r2.json())

    db = SessionLocal()
    try:
        events = auth_service.list_recent_audit_events(db, limit=20)
        for event in events:
            blob = f"{event.action}|{event.result}|{event.target or ''}"
            _assert_no_secrets(blob)
            for marker in _SECRET_MARKERS:
                # 审计可含固定字段名式动作码，但不得含原始口令
                if marker in (_TEST_PASSWORD, _WRONG_PASSWORD):
                    assert marker not in blob
    finally:
        db.close()


def test_login_sets_httponly_cookie_and_me_is_desensitized(required_client):
    """正确登录得到 HttpOnly/SameSite Cookie 与 CSRF；me 仅脱敏身份。"""
    _bootstrap()
    res = _login(required_client)
    assert res.status_code == 200
    body = res.json()
    assert "csrfToken" in body and body["csrfToken"]
    assert body["user"]["username"] == _TEST_USERNAME
    assert "password" not in body
    assert "passwordHash" not in body
    _assert_no_secrets(body)

    cookie = res.cookies.get(get_settings().auth_cookie_name)
    assert cookie
    # Set-Cookie 属性
    set_cookie = res.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()
    assert "samesite=strict" in set_cookie.lower()
    assert "path=/api" in set_cookie.lower()

    me = required_client.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["user"]["id"]
    assert me_body["user"]["username"] == _TEST_USERNAME
    assert me_body["activeWorkspaceId"] == "ws_local"
    assert any(w["role"] == "bid_writer" for w in me_body["workspaces"])
    _assert_no_secrets(me_body)
    assert "password" not in json.dumps(me_body)


def test_session_workspace_and_cross_space(required_client):
    """无 Cookie=401；有效会话可访问默认空间；非成员头=403；跨空间资源=404。"""
    _bootstrap()
    bare = TestClient(app)
    no_cookie = bare.get("/api/projects")
    assert no_cookie.status_code == 401

    login = _login(required_client)
    csrf = _csrf_from_login(login.json())
    ok = required_client.get("/api/projects")
    assert ok.status_code == 200

    # 非成员工作空间
    db = SessionLocal()
    try:
        other = Workspace(id="ws_other_p10a", name="其它空间", owner_user_id="user_x")
        db.add(other)
        db.commit()
    finally:
        db.close()

    forbidden = required_client.get(
        "/api/projects",
        headers={"X-Workspace-Id": "ws_other_p10a"},
    )
    assert forbidden.status_code == 403

    # 在默认空间创建项目，再用“看似合法但资源属其它空间”的方式验证 404
    created = required_client.post(
        "/api/projects",
        json={"name": "本空间项目"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    db = SessionLocal()
    try:
        # 将项目强行改到其它空间，模拟跨空间探测
        proj = db.get(Project, pid)
        assert proj is not None
        proj.workspace_id = "ws_other_p10a"
        db.commit()
    finally:
        db.close()

    missing = required_client.get(
        f"/api/projects/{pid}",
        headers={"X-Workspace-Id": "ws_local"},
    )
    assert missing.status_code == 404


def test_logout_expired_revoked_and_csrf(required_client):
    """退出/过期/撤销后为 401；变更请求缺 CSRF 或错误 CSRF 为 403。"""
    _bootstrap()
    login = _login(required_client)
    csrf = _csrf_from_login(login.json())

    # 缺 CSRF
    no_csrf = required_client.post("/api/projects", json={"name": "无CSRF"})
    assert no_csrf.status_code == 403

    # 错误 CSRF
    bad_csrf = required_client.post(
        "/api/projects",
        json={"name": "坏CSRF"},
        headers={"X-CSRF-Token": "definitely-wrong-csrf-token"},
    )
    assert bad_csrf.status_code == 403

    # 正确 CSRF 可创建
    ok = required_client.post(
        "/api/projects",
        json={"name": "有CSRF"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201

    # 退出：响应必须携带清除 Cookie 属性，且客户端 Cookie 罐应被清空
    cookie_name = get_settings().auth_cookie_name
    raw_token = required_client.cookies.get(cookie_name) or login.cookies.get(cookie_name)
    assert raw_token, "登录后客户端应持有会话 Cookie"
    out = required_client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf},
    )
    assert out.status_code == 204
    set_cookie = out.headers.get("set-cookie", "")
    assert set_cookie, "登出响应必须包含 Set-Cookie 以清除浏览器会话"
    assert cookie_name in set_cookie
    # delete_cookie 等价于 Max-Age=0 / expires 过期
    lowered = set_cookie.lower()
    assert "max-age=0" in lowered or "expires=" in lowered
    assert "path=/api" in lowered
    # TestClient 应消费清除指令，后续请求不再携带该 Cookie
    assert required_client.cookies.get(cookie_name) in (None, "")
    after = required_client.get("/api/projects")
    assert after.status_code == 401
    # 即使手动重放旧 Cookie，服务端撤销仍应拒绝（保留既有撤销校验）
    required_client.cookies.set(cookie_name, raw_token, path="/api")
    stale = required_client.get("/api/projects")
    assert stale.status_code == 401
    required_client.cookies.clear()

    # 过期会话
    login2 = _login(required_client)
    csrf2 = _csrf_from_login(login2.json())
    db = SessionLocal()
    try:
        sessions = db.query(AuthSessionRow).all()
        for s in sessions:
            s.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    finally:
        db.close()
    expired = required_client.get("/api/projects")
    assert expired.status_code == 401

    # 撤销会话
    login3 = _login(required_client)
    db = SessionLocal()
    try:
        sessions = db.query(AuthSessionRow).all()
        for s in sessions:
            s.revoked_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    revoked = required_client.get("/api/projects")
    assert revoked.status_code == 401
    # 避免未使用变量告警
    assert csrf2


def test_disabled_mode_keeps_workspace_header_isolation(monkeypatch, client):
    """auth_mode=disabled 维持既有默认工作空间与 X-Workspace-Id 隔离。"""
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()

    created = client.post("/api/projects", json={"name": "默认空间项目"})
    assert created.status_code == 201
    pid = created.json()["id"]
    assert created.json()["workspaceId"] == "ws_local"

    # 其它工作空间头：列表为空（隔离），详情 404
    other_list = client.get(
        "/api/projects",
        headers={"X-Workspace-Id": "ws_other_disabled"},
    )
    assert other_list.status_code == 200
    assert other_list.json() == []

    other_get = client.get(
        f"/api/projects/{pid}",
        headers={"X-Workspace-Id": "ws_other_disabled"},
    )
    assert other_get.status_code == 404

    # 默认头仍可见
    again = client.get("/api/projects")
    assert len(again.json()) == 1


def test_active_workspace_switch_and_non_member_rejected(required_client):
    """仅可切换到已加入的工作空间。"""
    principal = _bootstrap()
    login = _login(required_client)
    csrf = _csrf_from_login(login.json())

    db = SessionLocal()
    try:
        settings = get_settings()
        other = Workspace(id="ws_member_ok", name="成员空间", owner_user_id=principal.user_id)
        db.add(other)
        db.flush()
        auth_service.add_member(
            db,
            workspace_id="ws_member_ok",
            user_id=principal.user_id,
            role=auth_service.ROLE_BID_WRITER,
            is_owner=True,
        )
        db.commit()
    finally:
        db.close()

    switched = required_client.put(
        "/api/auth/active-workspace",
        json={"workspaceId": "ws_member_ok"},
        headers={"X-CSRF-Token": csrf},
    )
    assert switched.status_code == 200
    assert switched.json()["activeWorkspaceId"] == "ws_member_ok"

    bad = required_client.put(
        "/api/auth/active-workspace",
        json={"workspaceId": "ws_not_member"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 403
    _assert_no_secrets(bad.json())
    assert settings  # 配置可读


def test_auth_mode_default_is_disabled(monkeypatch):
    """未设置 AUTH_MODE 时默认 disabled，且不启用强制鉴权。"""
    monkeypatch.delenv("AUTH_MODE", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.auth_mode == "disabled"
        assert settings.is_auth_required() is False
        # 直接构造同样接受默认值
        direct = Settings(_env_file=None)
        assert direct.auth_mode == "disabled"
        assert direct.is_auth_required() is False
    finally:
        get_settings.cache_clear()


def test_auth_mode_required_accepted(monkeypatch):
    """AUTH_MODE=required（大小写不敏感）可加载，is_auth_required 为真。"""
    monkeypatch.setenv("AUTH_MODE", "REQUIRED")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.auth_mode == "required"
        assert settings.is_auth_required() is True
    finally:
        get_settings.cache_clear()

    via_ctor = Settings(auth_mode=" Required ")
    assert via_ctor.auth_mode == "required"
    assert via_ctor.is_auth_required() is True


@pytest.mark.parametrize(
    "illegal",
    [
        "optional",
        "true",
        "false",
        "1",
        "enable",
        "on",
        "off",
        "",
        "   ",
        "disabledx",
        "require",
    ],
)
def test_auth_mode_illegal_rejected_at_load(monkeypatch, illegal):
    """非法 AUTH_MODE 必须在配置加载时拒绝，禁止静默按 disabled 运行。"""
    monkeypatch.setenv("AUTH_MODE", illegal)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError) as exc_info:
            get_settings()
        text = str(exc_info.value)
        assert "AUTH_MODE" in text or "auth_mode" in text
        assert "disabled" in text and "required" in text
    finally:
        get_settings.cache_clear()

    with pytest.raises(ValidationError):
        Settings(auth_mode=illegal)


# ---------- P10A 任务2：成员管理、最后所有者保护、角色默认拒绝 ----------


_MEMBER_PASSWORD = "TestPass-Member-0002!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-Finance-0002!",
    "hr": "TestPass-Hr-0002!",
    "bidder": "TestPass-Bidder-0002!",
    "bid_writer": "TestPass-Writer-0002!",
}


def _owner_session(client: TestClient):
    """用途：引导管理员并登录，返回 (csrf, login_body)。"""
    _bootstrap()
    login = _login(client)
    assert login.status_code == 200
    body = login.json()
    return _csrf_from_login(body), body


def _create_member_via_api(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
    is_owner: bool = False,
):
    """用途：所有者通过 HTTP 创建成员。"""
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _login_as(client: TestClient, username: str, password: str):
    """用途：清空 Cookie 后以指定用户登录。"""
    client.cookies.clear()
    res = _login(client, username=username, password=password)
    assert res.status_code == 200, res.text
    return res


def test_owner_member_crud_list_role_toggle_and_delete(required_client):
    """所有者可创建/列表/改角色/启停/删除成员；响应脱敏。"""
    csrf, _ = _owner_session(required_client)

    created = _create_member_via_api(
        required_client,
        csrf,
        username="writer_one",
        password=_MEMBER_PASSWORD,
        role="bid_writer",
        is_owner=False,
    )
    assert created.status_code == 201
    member = created.json()
    assert member["username"] == "writer_one"
    assert member["role"] == "bid_writer"
    assert member["isOwner"] is False
    assert member["isActive"] is True
    assert member["userId"]
    assert "password" not in member
    _assert_no_secrets(member)
    body_text = json.dumps(member, ensure_ascii=False)
    assert _MEMBER_PASSWORD not in body_text
    for bad in ("passwordHash", "password_hash", "token_digest", "csrf_digest"):
        assert bad not in body_text

    listed = required_client.get("/api/auth/members")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) >= 2
    ids = {r["userId"] for r in rows}
    assert member["userId"] in ids
    _assert_no_secrets(rows)

    patched_role = required_client.patch(
        f"/api/auth/members/{member['userId']}",
        json={"role": "finance"},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched_role.status_code == 200
    assert patched_role.json()["role"] == "finance"

    patched_off = required_client.patch(
        f"/api/auth/members/{member['userId']}",
        json={"isActive": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched_off.status_code == 200
    assert patched_off.json()["isActive"] is False

    patched_on = required_client.patch(
        f"/api/auth/members/{member['userId']}",
        json={"isActive": True, "role": "bid_writer"},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched_on.status_code == 200
    assert patched_on.json()["isActive"] is True
    assert patched_on.json()["role"] == "bid_writer"

    deleted = required_client.delete(
        f"/api/auth/members/{member['userId']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 204

    listed_after = required_client.get("/api/auth/members")
    assert all(r["userId"] != member["userId"] for r in listed_after.json())


def test_last_active_owner_cannot_demote_deactivate_or_delete(required_client):
    """最后一个活跃所有者的降级、停用、删除均受控拒绝。"""
    csrf, me = _owner_session(required_client)
    owner_id = me["user"]["id"]

    demote = required_client.patch(
        f"/api/auth/members/{owner_id}",
        json={"isOwner": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert demote.status_code == 400
    assert demote.json()["detail"]["code"] == "auth_bad_request"
    _assert_no_secrets(demote.json())

    deactivate = required_client.patch(
        f"/api/auth/members/{owner_id}",
        json={"isActive": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert deactivate.status_code == 400
    assert deactivate.json()["detail"]["code"] == "auth_bad_request"

    remove = required_client.delete(
        f"/api/auth/members/{owner_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert remove.status_code == 400
    assert remove.json()["detail"]["code"] == "auth_bad_request"

    # 第二所有者存在后，可降级首个所有者
    second = _create_member_via_api(
        required_client,
        csrf,
        username="co_owner",
        password=_MEMBER_PASSWORD,
        role="bid_writer",
        is_owner=True,
    )
    assert second.status_code == 201
    demote_ok = required_client.patch(
        f"/api/auth/members/{owner_id}",
        json={"isOwner": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert demote_ok.status_code == 200
    assert demote_ok.json()["isOwner"] is False


def test_non_owner_members_api_forbidden(required_client):
    """非所有者访问 members* 固定 403 role_forbidden；不可自助提升。"""
    csrf, _ = _owner_session(required_client)
    created = _create_member_via_api(
        required_client,
        csrf,
        username="plain_writer",
        password=_MEMBER_PASSWORD,
        role="bid_writer",
        is_owner=False,
    )
    assert created.status_code == 201
    target_id = created.json()["userId"]

    _login_as(required_client, "plain_writer", _MEMBER_PASSWORD)
    login2 = required_client.post(
        "/api/auth/login",
        json={"username": "plain_writer", "password": _MEMBER_PASSWORD},
    )
    # 已由 _login_as 登录；再取 csrf
    me = required_client.get("/api/auth/me")
    assert me.status_code == 200
    # 非所有者登录后 CSRF 仅登录响应含有；重新登录取 csrf
    required_client.cookies.clear()
    login_plain = _login(required_client, "plain_writer", _MEMBER_PASSWORD)
    plain_csrf = _csrf_from_login(login_plain.json())

    list_r = required_client.get("/api/auth/members")
    assert list_r.status_code == 403
    assert list_r.json()["detail"]["code"] == "role_forbidden"

    create_r = required_client.post(
        "/api/auth/members",
        json={
            "username": "self_promote",
            "password": _MEMBER_PASSWORD,
            "role": "bid_writer",
            "isOwner": True,
        },
        headers={"X-CSRF-Token": plain_csrf},
    )
    assert create_r.status_code == 403
    assert create_r.json()["detail"]["code"] == "role_forbidden"

    patch_r = required_client.patch(
        f"/api/auth/members/{target_id}",
        json={"isOwner": True},
        headers={"X-CSRF-Token": plain_csrf},
    )
    assert patch_r.status_code == 403
    assert patch_r.json()["detail"]["code"] == "role_forbidden"

    delete_r = required_client.delete(
        f"/api/auth/members/{target_id}",
        headers={"X-CSRF-Token": plain_csrf},
    )
    assert delete_r.status_code == 403
    assert delete_r.json()["detail"]["code"] == "role_forbidden"
    _assert_no_secrets(list_r.json())


@pytest.mark.parametrize("role", ["finance", "hr", "bidder"])
def test_non_bid_writer_roles_forbidden_on_business_and_settings(required_client, role):
    """finance/hr/bidder 访问业务 API 与 settings 均为 403，且 settings 不回显 API Key。"""
    csrf, _ = _owner_session(required_client)
    username = f"user_{role}"
    password = _ROLE_PASSWORDS[role]
    created = _create_member_via_api(
        required_client,
        csrf,
        username=username,
        password=password,
        role=role,
        is_owner=False,
    )
    assert created.status_code == 201

    # 所有者先写入 settings 中的敏感值，供后续确认非所有者不可读
    owner_settings = required_client.put(
        "/api/settings",
        json={"apiKey": "secret-key-should-not-leak", "model": "m-test"},
        headers={"X-CSRF-Token": csrf},
    )
    assert owner_settings.status_code == 200
    assert owner_settings.json()["apiKey"] == "secret-key-should-not-leak"

    login = _login_as(required_client, username, password)
    role_csrf = _csrf_from_login(login.json())

    projects = required_client.get("/api/projects")
    assert projects.status_code == 403
    assert projects.json()["detail"]["code"] == "role_forbidden"

    create_proj = required_client.post(
        "/api/projects",
        json={"name": "不应创建"},
        headers={"X-CSRF-Token": role_csrf},
    )
    assert create_proj.status_code == 403
    assert create_proj.json()["detail"]["code"] == "role_forbidden"

    get_settings = required_client.get("/api/settings")
    assert get_settings.status_code == 403
    assert get_settings.json()["detail"]["code"] == "role_forbidden"
    settings_text = json.dumps(get_settings.json(), ensure_ascii=False)
    assert "secret-key-should-not-leak" not in settings_text
    assert "apiKey" not in settings_text or get_settings.status_code == 403

    put_settings = required_client.put(
        "/api/settings",
        json={"model": "hacked"},
        headers={"X-CSRF-Token": role_csrf},
    )
    assert put_settings.status_code == 403
    assert put_settings.json()["detail"]["code"] == "role_forbidden"
    _assert_no_secrets(projects.json())


def test_bid_writer_keeps_business_but_settings_requires_owner(required_client):
    """bid_writer 保持业务访问；非所有者访问 settings 为 403。"""
    csrf, _ = _owner_session(required_client)
    created = _create_member_via_api(
        required_client,
        csrf,
        username="writer_only",
        password=_ROLE_PASSWORDS["bid_writer"],
        role="bid_writer",
        is_owner=False,
    )
    assert created.status_code == 201

    login = _login_as(required_client, "writer_only", _ROLE_PASSWORDS["bid_writer"])
    writer_csrf = _csrf_from_login(login.json())

    projects = required_client.get("/api/projects")
    assert projects.status_code == 200

    created_proj = required_client.post(
        "/api/projects",
        json={"name": "作者可建项目"},
        headers={"X-CSRF-Token": writer_csrf},
    )
    assert created_proj.status_code == 201

    settings_get = required_client.get("/api/settings")
    assert settings_get.status_code == 403
    assert settings_get.json()["detail"]["code"] == "role_forbidden"

    settings_put = required_client.put(
        "/api/settings",
        json={"model": "nope"},
        headers={"X-CSRF-Token": writer_csrf},
    )
    assert settings_put.status_code == 403


def test_deactivate_or_remove_member_revokes_sessions(required_client):
    """停用或移除成员后，旧 Cookie 立即失效。"""
    csrf, _ = _owner_session(required_client)
    created = _create_member_via_api(
        required_client,
        csrf,
        username="temp_member",
        password=_MEMBER_PASSWORD,
        role="bid_writer",
        is_owner=False,
    )
    assert created.status_code == 201
    user_id = created.json()["userId"]

    # 成员登录并验证业务可访问
    member_client_login = _login_as(required_client, "temp_member", _MEMBER_PASSWORD)
    assert required_client.get("/api/projects").status_code == 200
    cookie_name = get_settings().auth_cookie_name
    raw_token = required_client.cookies.get(cookie_name)
    assert raw_token

    # 切回所有者停用该成员
    required_client.cookies.clear()
    owner_login = _login(required_client)
    owner_csrf = _csrf_from_login(owner_login.json())
    off = required_client.patch(
        f"/api/auth/members/{user_id}",
        json={"isActive": False},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert off.status_code == 200

    # 旧 Cookie 再访问应 401
    stale = TestClient(app)
    stale.cookies.set(cookie_name, raw_token, path="/api")
    assert stale.get("/api/projects").status_code == 401

    # 重新启用后创建另一会话，再删除成员
    on = required_client.patch(
        f"/api/auth/members/{user_id}",
        json={"isActive": True},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert on.status_code == 200
    member_login2 = _login_as(required_client, "temp_member", _MEMBER_PASSWORD)
    raw_token2 = required_client.cookies.get(cookie_name)
    assert raw_token2
    assert member_login2.status_code == 200

    required_client.cookies.clear()
    owner_login2 = _login(required_client)
    owner_csrf2 = _csrf_from_login(owner_login2.json())
    deleted = required_client.delete(
        f"/api/auth/members/{user_id}",
        headers={"X-CSRF-Token": owner_csrf2},
    )
    assert deleted.status_code == 204

    stale2 = TestClient(app)
    stale2.cookies.set(cookie_name, raw_token2, path="/api")
    assert stale2.get("/api/projects").status_code == 401


def test_disabled_mode_settings_still_available(monkeypatch, client):
    """disabled 模式设置 API 保持个人版可用（require_owner 兼容退化）。"""
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        got = client.get("/api/settings")
        assert got.status_code == 200
        assert "apiKey" in got.json()
        put = client.put("/api/settings", json={"model": "disabled-ok"})
        assert put.status_code == 200
        assert put.json()["model"] == "disabled-ok"
    finally:
        get_settings.cache_clear()


# ---------- P10A 任务2返修：认证请求校验错误不得回显口令 ----------


# 唯一合成敏感串：仅用于断言错误响应不得回显，禁止出现在固定错误文案中
_LEAK_PROBE = "SecretProbe-Pwd-Array-9f3c7a2e!"


def _assert_auth_request_invalid_desensitized(response, *extra_markers: str) -> None:
    """用途：断言认证体校验失败为固定脱敏错误，不含原始输入与 Pydantic 明细。"""
    assert response.status_code in (400, 422)
    body = response.json()
    detail = body.get("detail", body)
    # 固定结构：code + 中文 message；不得是 FastAPI 默认 list[loc/input]
    assert isinstance(detail, dict), body
    assert detail.get("code") == "auth_bad_request"
    message = detail.get("message") or ""
    assert isinstance(message, str) and message
    # 中文固定文案（至少一个汉字），且不含英文校验框架噪声
    assert any("\u4e00" <= ch <= "\u9fff" for ch in message)
    full_text = json.dumps(body, ensure_ascii=False)
    assert _LEAK_PROBE not in full_text
    for marker in extra_markers:
        assert marker not in full_text
    # 禁止 Pydantic/FastAPI 默认校验明细回显
    for noise in (
        '"loc"',
        '"input"',
        "string_type",
        "model_attributes_type",
        "literal_error",
        "Input should be",
        "Field required",
    ):
        assert noise not in full_text
    _assert_no_secrets(body)


@pytest.mark.parametrize(
    "password_value",
    [
        [_LEAK_PROBE],
        {"nested": _LEAK_PROBE},
    ],
)
def test_login_malformed_password_no_echo(required_client, password_value):
    """login 畸形 password（数组/对象）受控失败，响应全文不含敏感串与 loc/input。"""
    _bootstrap()
    res = required_client.post(
        "/api/auth/login",
        json={"username": _TEST_USERNAME, "password": password_value},
    )
    _assert_auth_request_invalid_desensitized(res)


def test_login_non_object_body_no_echo(required_client):
    """login 非对象 JSON 体受控失败，不得回显原始数组内容。"""
    _bootstrap()
    res = required_client.post(
        "/api/auth/login",
        content=json.dumps([_LEAK_PROBE, "not-object"]),
        headers={"Content-Type": "application/json"},
    )
    _assert_auth_request_invalid_desensitized(res)


@pytest.mark.parametrize(
    "password_value",
    [
        [_LEAK_PROBE],
        {"nested": _LEAK_PROBE},
    ],
)
def test_create_member_malformed_password_no_echo(required_client, password_value):
    """POST /members 畸形 password 在 owner+正确 CSRF 下受控失败且不回显。"""
    csrf, _ = _owner_session(required_client)
    res = required_client.post(
        "/api/auth/members",
        json={
            "username": "leak_probe_user",
            "password": password_value,
            "role": "bid_writer",
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    _assert_auth_request_invalid_desensitized(res)


def test_create_member_invalid_role_no_echo(required_client):
    """POST /members 无效 role 受控失败；固定错误，无 Pydantic 明细、无口令回显。"""
    csrf, _ = _owner_session(required_client)
    res = required_client.post(
        "/api/auth/members",
        json={
            "username": "bad_role_user",
            "password": _LEAK_PROBE,
            "role": "superadmin",
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    _assert_auth_request_invalid_desensitized(res, "superadmin")


def test_valid_login_and_create_member_still_work_after_safe_parse(required_client):
    """安全解析不得破坏正常 login 与创建成员的 HTTP 契约。"""
    _bootstrap()
    login = _login(required_client)
    assert login.status_code == 200
    body = login.json()
    assert body["csrfToken"]
    assert body["user"]["username"] == _TEST_USERNAME
    cookie_name = get_settings().auth_cookie_name
    assert login.cookies.get(cookie_name) or required_client.cookies.get(cookie_name)

    csrf = _csrf_from_login(body)
    created = _create_member_via_api(
        required_client,
        csrf,
        username="safe_parse_writer",
        password=_MEMBER_PASSWORD,
        role="bid_writer",
        is_owner=False,
    )
    assert created.status_code == 201
    member = created.json()
    assert member["username"] == "safe_parse_writer"
    assert member["role"] == "bid_writer"
    assert _MEMBER_PASSWORD not in json.dumps(member, ensure_ascii=False)
