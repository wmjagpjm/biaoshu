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


_SECRET_API_KEY = "sk-secret-must-not-leak"
_SECRET_MODEL = "secret-model"
_SECRET_PROVIDER = "deepseek"
_SECRET_BASE = "https://api.example.com/v1"
_SECRET_EMBED = "secret-embed"
# 非法存量标记：须出现在 ORM 行内，但权威 GET 响应零泄漏；长度 ≤ String(32)
_CORRUPT_PARSE = "SECRET_CORRUPT_mgd_x_bad"
assert len(_CORRUPT_PARSE) <= 32
_FOUR_VALUES = ("light", "managed", "local", "ask")


def _seed_parse_strategy(workspace_id: str, strategy: str) -> None:
    """用途：经 service 写入合法策略行（含敏感字段，验证脱敏）。"""
    db = SessionLocal()
    try:
        settings_service.update_settings(
            db,
            workspace_id,
            parse_strategy=strategy,
            api_key=_SECRET_API_KEY,
            provider=_SECRET_PROVIDER,
            api_base_url=_SECRET_BASE,
            model=_SECRET_MODEL,
            embedding_model=_SECRET_EMBED,
        )
    finally:
        db.close()


def _orm_write_parse_strategy(
    workspace_id: str,
    strategy: str,
    *,
    api_key: str = _SECRET_API_KEY,
    model: str = _SECRET_MODEL,
) -> WorkspaceSettingsRow:
    """
    用途：绕过 service 校验直接写 ORM 行（合法四值或非法存量）。
    对接：M3 权威 GET corrupt / managed 回显 failure-first。
    二次开发：禁止改生产；仅测试夹具。
    """
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        row = db.get(WorkspaceSettingsRow, workspace_id)
        if row is None:
            row = WorkspaceSettingsRow(
                workspace_id=workspace_id,
                provider=_SECRET_PROVIDER,
                api_base_url=_SECRET_BASE,
                api_key=api_key,
                model=model,
                parse_strategy=strategy,
                embedding_model=_SECRET_EMBED,
                updated_at=datetime.now(timezone.utc),
            )
            db.add(row)
        else:
            row.parse_strategy = strategy
            row.api_key = api_key
            row.model = model
            row.provider = _SECRET_PROVIDER
            row.api_base_url = _SECRET_BASE
            row.embedding_model = _SECRET_EMBED
            row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        # 脱离会话后仍可比较字段
        db.expunge(row)
        return row
    finally:
        db.close()


def _read_orm_parse_row(workspace_id: str) -> WorkspaceSettingsRow | None:
    """用途：读取当前 ORM 行快照（零 HTTP）。"""
    db = SessionLocal()
    try:
        row = db.get(WorkspaceSettingsRow, workspace_id)
        if row is None:
            return None
        db.expunge(row)
        return row
    finally:
        db.close()


def _assert_no_leak(payload: object, *extra_markers: str) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"
    for marker in extra_markers:
        if not marker:
            continue
        assert marker not in text, f"响应泄漏原值/哨兵: {marker}"
        assert marker.lower() not in lower, f"响应泄漏原值/哨兵(大小写): {marker}"


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


@pytest.mark.parametrize("strategy", list(_FOUR_VALUES))
def test_disabled_saved_strategy_returned(client, strategy: str):
    """
    模块：已保存策略回显（M3 四值）
    用途：light|managed|local|ask 原样返回；managed 经 ORM 直写避免 PUT 路径干扰。
    对接：ORM WorkspaceSettingsRow；GET parse-strategy。
    二次开发：不得返回完整设置；禁止 soft fallback 吞掉 managed。
    """
    ws = get_settings().default_workspace_id
    # managed 与其余四值统一 ORM 写入，保证生产未扩 ALLOWED_PARSE 时仍可红测 GET
    _orm_write_parse_strategy(ws, strategy)
    res = client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), strategy)
    assert "no-store" in (res.headers.get("cache-control") or "").lower()


def test_disabled_managed_orm_roundtrip_exact(client):
    """
    模块：M3 managed 权威 GET
    用途：ORM 直写 managed 后 GET 精确回显 managed，且 no-store。
    对接：GET /api/settings/parse-strategy。
    二次开发：禁止回退 light。
    """
    ws = get_settings().default_workspace_id
    _orm_write_parse_strategy(ws, "managed")
    res = client.get(_PATH)
    assert res.status_code == 200, res.text
    _assert_strategy_body(res.json(), "managed")
    cache = (res.headers.get("cache-control") or "").lower()
    assert "no-store" in cache


@pytest.mark.parametrize(
    "corrupt_value",
    [
        _CORRUPT_PARSE,
        " light ",
        "managed ",
        "\tlocal",
        "ask\t",
        " light",
        "local ",
    ],
)
def test_disabled_corrupt_stock_fixed_500_no_store_no_leak_no_commit(
    client, monkeypatch, corrupt_value: str
):
    """
    模块：M3 非法存量 corrupt（含 fixed secret 与空白近合法值）
    用途：ORM 直写非法/空白包裹策略后，权威 GET 固定 500 + 精确 detail，
          Cache-Control=no-store，零 commit（Session.commit 计数=0），
          原值/Key/model 零泄漏；ORM 原值保留。
    对接：契约 M3 冻结决策 §2；GET /api/settings/parse-strategy。
    二次开发：禁止 soft fallback light / strip 归一；禁止 detail 回显原值；
              commit 计数须继续调用原实现，禁止抛异常制造 500 假绿。
    """
    from sqlalchemy.orm import Session

    stripped = corrupt_value.strip()
    if corrupt_value != stripped:
        assert stripped in _FOUR_VALUES

    ws = get_settings().default_workspace_id
    before_row = _orm_write_parse_strategy(ws, corrupt_value)
    before_updated = before_row.updated_at
    before_count = _settings_row_count(ws)
    assert before_row.parse_strategy == corrupt_value
    assert before_row.api_key == _SECRET_API_KEY
    assert before_row.model == _SECRET_MODEL

    # seed 之后再 monkeypatch：计数真实 commit 调用，并转发原实现
    commit_calls = {"n": 0}
    original_commit = Session.commit

    def counting_commit(self, *args, **kwargs):
        commit_calls["n"] += 1
        return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", counting_commit)

    res = client.get(_PATH)
    assert res.status_code == 500, (repr(corrupt_value), res.text)
    body = res.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), body
    assert set(detail.keys()) == {"code", "message"}
    assert detail["code"] == "workspace_parse_strategy_corrupt"
    assert detail["message"] == "解析策略配置损坏"
    cache = (res.headers.get("cache-control") or "").lower()
    assert "no-store" in cache
    leak_markers: list[str] = [
        corrupt_value,
        _SECRET_API_KEY,
        _SECRET_MODEL,
        _SECRET_PROVIDER,
        _SECRET_BASE,
        _SECRET_EMBED,
        "sk-",
    ]
    if corrupt_value != stripped:
        leak_markers.append(stripped)
    _assert_no_leak(body, *leak_markers)
    # 响应体不得误回退 light，也不得出现 parseStrategy 键或原非法值
    raw_text = res.text
    assert "parseStrategy" not in raw_text
    assert '"light"' not in raw_text
    assert corrupt_value not in raw_text

    # 权威 GET 路径零 commit（契约硬锁，非仅零可见写）
    assert commit_calls["n"] == 0

    after_row = _read_orm_parse_row(ws)
    assert after_row is not None
    assert after_row.parse_strategy == corrupt_value
    assert after_row.api_key == _SECRET_API_KEY
    assert after_row.model == _SECRET_MODEL
    assert after_row.updated_at == before_updated
    assert _settings_row_count(ws) == before_count


def test_disabled_owner_full_get_readable_and_put_repairs_corrupt(client):
    """
    模块：M3 所有者修复入口
    用途：非法存量时完整 GET /api/settings 仍可读；合法 PUT 可修复；
          修复后权威 GET 恢复合法四值。
    对接：GET|PUT /api/settings；GET parse-strategy。
    二次开发：完整设置不得驱动解析动作，但须保留修复能力。
    """
    ws = get_settings().default_workspace_id
    _orm_write_parse_strategy(ws, _CORRUPT_PARSE)

    full = client.get("/api/settings")
    assert full.status_code == 200, full.text
    full_body = full.json()
    assert full_body["parseStrategy"] == _CORRUPT_PARSE
    # 所有者可读完整行，但权威策略接口仍应 corrupt
    bad = client.get(_PATH)
    assert bad.status_code == 500, bad.text
    assert bad.json()["detail"]["code"] == "workspace_parse_strategy_corrupt"

    put = client.put(
        "/api/settings",
        json={
            "provider": "openai-compatible",
            "apiBaseUrl": "https://api.deepseek.com/v1",
            "apiKey": "",
            "model": "deepseek-chat",
            "parseStrategy": "managed",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["parseStrategy"] == "managed"

    fixed = client.get(_PATH)
    assert fixed.status_code == 200, fixed.text
    _assert_strategy_body(fixed.json(), "managed")
    assert "no-store" in (fixed.headers.get("cache-control") or "").lower()

    row = _read_orm_parse_row(ws)
    assert row is not None
    assert row.parse_strategy == "managed"


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
