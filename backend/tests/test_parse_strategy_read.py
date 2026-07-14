"""
模块：P8B 解析策略脱敏读取定向测试
用途：验收 GET /api/settings/parse-strategy 的默认值、字段白名单、no-store 与角色边界。
对接：app.api.settings；app.services.settings_service；deps.get_workspace_id。
二次开发：仅固定合成口令与本地 SQLite；禁止外网、真实口令与白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Workspace, WorkspaceSettingsRow
from app.services import auth_service, settings_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_parse_p8b"
_OWNER_PASS = "TestPass-Parse-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-Writer-Parse-Role-0001!",
    "finance": "TestPass-Finance-Parse-Role-0001!",
    "hr": "TestPass-Hr-Parse-Role-0001!",
    "bidder": "TestPass-Bidder-Parse-Role-0001!",
}
_PATH = "/api/settings/parse-strategy"
_ALLOWED_KEYS = frozenset({"parseStrategy"})
_FORBIDDEN_MARKERS = (
    "apiKey",
    "api_key",
    "apiBaseUrl",
    "api_base_url",
    "provider",
    "model",
    "embedding",
    "embeddingModel",
    "exportFormat",
    "export_format",
    "workspaceId",
    "workspace_id",
    "updatedAt",
    "updated_at",
    "sk-",
    "password",
    "csrf",
    "token",
)


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


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
    db = SessionLocal()
    try:
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_OWNER_USER,
            password=_OWNER_PASS,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str):
    client.cookies.clear()
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def _owner_session(client: TestClient):
    _bootstrap()
    res = _login(client, _OWNER_USER, _OWNER_PASS)
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"], res.json()


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
    is_owner: bool = False,
):
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


def _login_role(client: TestClient, role: str, *, is_owner: bool = False) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p8b"
    if is_owner:
        username = f"user_owner_{role}_p8b"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
        is_owner=is_owner,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _settings_row_count(workspace_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        stmt = select(func.count()).select_from(WorkspaceSettingsRow)
        if workspace_id is not None:
            stmt = stmt.where(WorkspaceSettingsRow.workspace_id == workspace_id)
        return int(db.scalar(stmt) or 0)
    finally:
        db.close()


def _seed_parse_strategy(workspace_id: str, strategy: str) -> None:
    """用途：直接写入策略行，避免经 GET /api/settings 产生副作用。"""
    db = SessionLocal()
    try:
        settings_service.update_settings(
            db,
            workspace_id,
            parse_strategy=strategy,
            api_key="sk-secret-must-not-leak",
            provider="deepseek",
            api_base_url="https://api.example.com/v1",
            model="secret-model",
            embedding_model="secret-embed",
        )
    finally:
        db.close()


def _assert_no_leak(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"


def _assert_strategy_body(body: dict, expected: str) -> None:
    assert set(body.keys()) == _ALLOWED_KEYS
    assert body["parseStrategy"] == expected
    _assert_no_leak(body)


# ---------- disabled 兼容：默认与三种策略 ----------


def test_disabled_default_no_row_returns_light_without_create(client):
    """
    模块：默认无设置行
    用途：无 workspace_settings 行时返回 light，且读取不建行。
    对接：GET /api/settings/parse-strategy；settings_service 只读。
    二次开发：禁止调用 get_or_create_settings。
    """
    ws = get_settings().default_workspace_id
    before = _settings_row_count(ws)
    assert before == 0

    res = client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), "light")
    assert "no-store" in (res.headers.get("cache-control") or "").lower()

    after = _settings_row_count(ws)
    assert after == 0


@pytest.mark.parametrize("strategy", ["light", "local", "ask"])
def test_disabled_saved_strategy_returned(client, strategy: str):
    """
    模块：已保存策略回显
    用途：三种合法 parseStrategy 原样返回。
    对接：settings_service.update_settings；GET parse-strategy。
    二次开发：不得返回完整设置。
    """
    ws = get_settings().default_workspace_id
    _seed_parse_strategy(ws, strategy)
    res = client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), strategy)
    assert "no-store" in (res.headers.get("cache-control") or "").lower()


def test_disabled_response_whitelist_and_no_store(client):
    """
    模块：字段白名单与缓存头
    用途：仅 parseStrategy；Cache-Control=no-store；无 Key/provider 等。
    对接：ParseStrategyOut；settings 路由。
    二次开发：禁止复用完整 WorkspaceSettingsOut。
    """
    ws = get_settings().default_workspace_id
    _seed_parse_strategy(ws, "ask")
    res = client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_strategy_body(body, "ask")
    assert set(body.keys()) == {"parseStrategy"}
    cache = (res.headers.get("cache-control") or "").lower()
    assert "no-store" in cache


# ---------- required：标书制作者 / 拒绝路径 ----------


def test_required_bid_writer_can_read(required_client):
    """
    模块：required 下精确 bid_writer
    用途：非所有者标书制作者可读策略。
    对接：get_workspace_id(require_bid_writer=True)。
    二次开发：不得改为 require_owner。
    """
    _login_role(required_client, "bid_writer")
    ws = get_settings().default_workspace_id
    _seed_parse_strategy(ws, "local")
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), "local")
    assert "no-store" in (res.headers.get("cache-control") or "").lower()


def test_required_owner_bid_writer_can_read(required_client):
    """
    模块：required 下所有者（默认 bid_writer）
    用途：bootstrap 所有者可读取策略。
    对接：get_workspace_id。
    二次开发：与完整 GET /api/settings 的 require_owner 语义分离。
    """
    _owner_session(required_client)
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), "light")


def test_required_unauthenticated_401(required_client):
    """
    模块：未登录边界
    用途：AUTH_MODE=required 且无会话固定 401 auth_required。
    对接：auth_middleware；get_workspace_id。
    二次开发：不得降级为 403。
    """
    # 先完成管理员初始化，否则中间件可能返回 503 auth_not_bootstrapped
    _bootstrap()
    bare = TestClient(app)
    bare.cookies.clear()
    res = bare.get(_PATH)
    assert res.status_code == 401, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "auth_required"


@pytest.mark.parametrize("role", ["finance", "hr", "bidder"])
def test_required_non_bid_writer_forbidden(required_client, role: str):
    """
    模块：非标书制作者角色
    用途：finance/hr/bidder 固定 403 role_forbidden。
    对接：get_workspace_id require_bid_writer。
    二次开发：禁止放宽角色。
    """
    _login_role(required_client, role)
    res = required_client.get(_PATH)
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "role_forbidden"


def test_required_cross_workspace_forbidden(required_client):
    """
    模块：跨工作空间
    用途：非成员 X-Workspace-Id 固定 403 workspace_forbidden。
    对接：resolve_workspace_for_principal。
    二次开发：禁止因读取策略而跨空间放行。
    """
    _login_role(required_client, "bid_writer")
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_parse_p8b")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_parse_p8b",
                    name="其它解析空间",
                    owner_user_id="user_x",
                )
            )
            db.commit()
    finally:
        db.close()

    res = required_client.get(
        _PATH,
        headers={"X-Workspace-Id": "ws_other_parse_p8b"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "workspace_forbidden"


def test_required_no_row_does_not_create(required_client):
    """
    模块：required 下无行只读
    用途：bid_writer 读取默认 light 且不建行。
    对接：只读 get_parse_strategy。
    二次开发：不得 commit/get_or_create。
    """
    _login_role(required_client, "bid_writer")
    ws = get_settings().default_workspace_id
    before = _settings_row_count(ws)
    assert before == 0
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), "light")
    assert _settings_row_count(ws) == 0
