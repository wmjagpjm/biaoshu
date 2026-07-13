"""
模块：P10B 财务只读商务投标报价定向测试
用途：验收 finance 专用只读接口的鉴权、白名单投影、工作空间隔离与越权拒绝。
对接：app.api.finance；app.services.finance_service；deps.require_finance。
二次开发：仅使用固定合成口令与本地 SQLite；禁止依赖外网、真实业务口令或写接口副作用。
"""

from __future__ import annotations

import json
import math

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import Workspace
from app.services import auth_service, editor_state_service, project_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_finance_p10b"
_OWNER_PASS = "TestPass-Finance-Owner-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-Finance-Role-0001!",
    "hr": "TestPass-Hr-Role-0001!",
    "bidder": "TestPass-Bidder-Role-0001!",
    "bid_writer": "TestPass-Writer-Role-0001!",
}
_LIST_ITEM_KEYS = frozenset(
    {
        "projectId",
        "name",
        "industry",
        "status",
        "updatedAt",
        "quoteRowCount",
        "quoteTotal",
    }
)
_ROW_KEYS = frozenset(
    {"id", "name", "unit", "quantity", "unitPrice", "amount", "remark"}
)
_FORBIDDEN_MARKERS = (
    "businessQualify",
    "businessToc",
    "businessCommit",
    "business_json",
    "qualify",
    "toc",
    "commit",
    "parsedMarkdown",
    "outline",
    "chapters",
    "responseMatrix",
    "apiKey",
    "api_key",
    "password",
    "csrf",
    "token_digest",
    "SECRET_QUALIFY",
    "SECRET_COMMIT",
    "SECRET_TECH",
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


def _seed_quote_fixture() -> dict[str, str]:
    """
    用途：写入本空间商务标/技术标与跨空间商务标及敏感商务字段，供投影与隔离断言。
    返回：各项目 id 字典。
    """
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_finance")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_finance",
                    name="其他财务空间",
                    owner_user_id="user_other_finance",
                )
            )
            db.commit()

        biz = project_service.create_project(
            db,
            "ws_local",
            name="财务可见商务标",
            industry="能源",
            kind="business",
            status="draft",
        )
        tech = project_service.create_project(
            db,
            "ws_local",
            name="不可见技术标",
            industry="通用",
            kind="technical",
        )
        foreign = project_service.create_project(
            db,
            "ws_other_finance",
            name="跨空间商务标",
            industry="跨域",
            kind="business",
        )

        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            biz.id,
            business_qualify=[{"id": "q1", "title": "SECRET_QUALIFY"}],
            business_toc=[{"id": "t1", "title": "SECRET_TOC"}],
            business_commit=[{"id": "c1", "title": "SECRET_COMMIT"}],
            business_quote={
                "rows": [
                    {
                        "id": "r1",
                        "name": "主机设备",
                        "unit": "套",
                        "quantity": "2",
                        "unitPrice": "50000",
                        "amount": 100000.0,
                        "remark": "含安装",
                        "extraLeak": "SHOULD_NOT_APPEAR",
                    },
                    {
                        "id": "r2",
                        "name": "运维服务",
                        "unit": "年",
                        "quantity": "1",
                        "unitPrice": "28000",
                        "amount": 28000,
                        "remark": "",
                    },
                    {
                        "id": "r3",
                        "name": "异常金额行",
                        "unit": "项",
                        "quantity": "1",
                        "unitPrice": "x",
                        "amount": float("nan"),
                        "remark": "nan",
                    },
                    {
                        "id": "r4",
                        "name": "无穷大行",
                        "unit": "项",
                        "quantity": "1",
                        "unitPrice": "x",
                        "amount": float("inf"),
                        "remark": "inf",
                    },
                    {
                        "id": "r5",
                        "name": "字符串金额行",
                        "unit": "项",
                        "quantity": "1",
                        "unitPrice": "9",
                        "amount": "9000",
                        "remark": "str-amount",
                    },
                    {
                        "id": "r6",
                        "name": "嵌套金额行",
                        "unit": "项",
                        "quantity": "1",
                        "unitPrice": "1",
                        "amount": {"nested": 1},
                        "remark": "obj-amount",
                    },
                ],
                "notes": "仅财务可见备注",
                "hiddenCost": 999,
            },
            parsed_markdown="SECRET_TECH 解析正文不得泄露",
        )
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            tech.id,
            business_quote={
                "rows": [
                    {
                        "id": "tr1",
                        "name": "技术标伪装报价",
                        "amount": 1.0,
                    }
                ],
                "notes": "tech",
            },
        )
        editor_state_service.upsert_editor_state(
            db,
            "ws_other_finance",
            foreign.id,
            business_quote={
                "rows": [{"id": "fr1", "name": "跨空间", "amount": 9.0}],
                "notes": "foreign",
            },
        )
        return {
            "business_id": biz.id,
            "technical_id": tech.id,
            "foreign_id": foreign.id,
        }
    finally:
        db.close()


def _login_role(client: TestClient, role: str) -> None:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10b"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text


def _assert_no_leak(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"
    assert "SHOULD_NOT_APPEAR" not in text
    assert "hiddenCost" not in text


def test_finance_list_and_detail_whitelist_projection(required_client):
    """财务角色可读本空间商务标列表与明细；字段白名单且金额安全归一。"""
    ids = _seed_quote_fixture()
    _login_role(required_client, "finance")

    listed = required_client.get("/api/finance/business-bids")
    assert listed.status_code == 200, listed.text
    assert listed.headers.get("cache-control", "").lower() == "no-store"
    body = listed.json()
    assert set(body.keys()) == {"items"}
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item.keys()) == _LIST_ITEM_KEYS
    assert item["projectId"] == ids["business_id"]
    assert item["name"] == "财务可见商务标"
    assert item["industry"] == "能源"
    assert item["status"] == "draft"
    assert item["quoteRowCount"] == 6
    # 仅累加有限数值 amount：100000 + 28000
    assert item["quoteTotal"] == 128000.0
    assert math.isfinite(item["quoteTotal"])
    _assert_no_leak(body)

    detail = required_client.get(f"/api/finance/business-bids/{ids['business_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.headers.get("cache-control", "").lower() == "no-store"
    dbody = detail.json()
    expected_keys = _LIST_ITEM_KEYS | {"quoteRows", "quoteNotes"}
    assert set(dbody.keys()) == expected_keys
    assert dbody["projectId"] == ids["business_id"]
    assert dbody["quoteNotes"] == "仅财务可见备注"
    assert dbody["quoteRowCount"] == 6
    assert dbody["quoteTotal"] == 128000.0
    assert len(dbody["quoteRows"]) == 6
    for row in dbody["quoteRows"]:
        assert set(row.keys()) == _ROW_KEYS
        assert isinstance(row["id"], str)
        assert isinstance(row["name"], str)
        assert isinstance(row["unit"], str)
        assert isinstance(row["quantity"], str)
        assert isinstance(row["unitPrice"], str)
        assert isinstance(row["remark"], str)
        amount = row["amount"]
        assert amount is None or (isinstance(amount, (int, float)) and math.isfinite(float(amount)))
    by_id = {r["id"]: r for r in dbody["quoteRows"]}
    assert by_id["r1"]["amount"] == 100000.0
    assert by_id["r2"]["amount"] == 28000.0
    assert by_id["r3"]["amount"] is None
    assert by_id["r4"]["amount"] is None
    assert by_id["r5"]["amount"] is None  # 字符串金额不解析进响应
    assert by_id["r6"]["amount"] is None
    assert "extraLeak" not in json.dumps(dbody, ensure_ascii=False)
    _assert_no_leak(dbody)


def test_finance_detail_technical_foreign_missing_are_404(required_client):
    """技术标、跨工作空间、不存在项目统一 404 project_not_found。"""
    ids = _seed_quote_fixture()
    _login_role(required_client, "finance")

    for pid in (ids["technical_id"], ids["foreign_id"], "proj_not_exist_p10b"):
        res = required_client.get(f"/api/finance/business-bids/{pid}")
        assert res.status_code == 404, res.text
        detail = res.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["code"] == "project_not_found"
        _assert_no_leak(res.json())


@pytest.mark.parametrize("role", ["bid_writer", "hr", "bidder"])
def test_non_finance_roles_forbidden(required_client, role):
    """非 finance 业务角色访问财务接口固定 403 role_forbidden。"""
    _seed_quote_fixture()
    _login_role(required_client, role)
    for path in ("/api/finance/business-bids", "/api/finance/business-bids/any"):
        res = required_client.get(path)
        assert res.status_code == 403, res.text
        assert res.json()["detail"]["code"] == "role_forbidden"
        _assert_no_leak(res.json())


def test_owner_bid_writer_forbidden_on_finance_routes(required_client):
    """所有者（默认 bid_writer）亦不得调用专用财务读接口。"""
    ids = _seed_quote_fixture()
    _owner_session(required_client)
    listed = required_client.get("/api/finance/business-bids")
    assert listed.status_code == 403
    assert listed.json()["detail"]["code"] == "role_forbidden"
    detail = required_client.get(f"/api/finance/business-bids/{ids['business_id']}")
    assert detail.status_code == 403
    assert detail.json()["detail"]["code"] == "role_forbidden"


def test_disabled_mode_and_unauthenticated_denied(monkeypatch):
    """禁用模式与未登录均不得读取财务报价。"""
    # disabled：路由依赖固定 role_forbidden
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            _seed_quote_fixture()
            res = client.get("/api/finance/business-bids")
            assert res.status_code == 403, res.text
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 未登录：中间件先拦 401；仍不得 200
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            _bootstrap()
            _seed_quote_fixture()
            client.cookies.clear()
            res = client.get("/api/finance/business-bids")
            assert res.status_code in (401, 403)
            assert res.status_code != 200
            detail = res.json()["detail"]
            if isinstance(detail, dict):
                assert detail["code"] in {
                    auth_service.CODE_AUTH_REQUIRED,
                    auth_service.CODE_ROLE_FORBIDDEN,
                }
    finally:
        get_settings.cache_clear()


def test_finance_still_blocked_from_general_business_apis(required_client):
    """财务仍不能访问通用 projects/settings/editor-state/files。"""
    ids = _seed_quote_fixture()
    _login_role(required_client, "finance")
    pid = ids["business_id"]

    projects = required_client.get("/api/projects")
    assert projects.status_code == 403
    assert projects.json()["detail"]["code"] == "role_forbidden"

    settings = required_client.get("/api/settings")
    assert settings.status_code == 403
    assert settings.json()["detail"]["code"] == "role_forbidden"

    editor = required_client.get(f"/api/projects/{pid}/editor-state")
    assert editor.status_code == 403
    assert editor.json()["detail"]["code"] == "role_forbidden"

    files = required_client.get(f"/api/projects/{pid}/files")
    assert files.status_code == 403
    assert files.json()["detail"]["code"] == "role_forbidden"


def test_finance_routes_reject_non_get_methods(required_client):
    """新 URL 的非 GET 方法返回 405，且不产生写入。"""
    ids = _seed_quote_fixture()
    # 所有者创建 finance 成员后，用返回的 CSRF 覆盖变更请求校验
    csrf, _ = _owner_session(required_client)
    username = "user_finance_methods_p10b"
    created = _create_member(
        required_client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS["finance"],
        role="finance",
    )
    assert created.status_code == 201, created.text
    login = _login(required_client, username, _ROLE_PASSWORDS["finance"])
    assert login.status_code == 200
    finance_csrf = login.json()["csrfToken"]
    pid = ids["business_id"]

    before = required_client.get("/api/finance/business-bids")
    assert before.status_code == 200
    before_total = before.json()["items"][0]["quoteTotal"]

    paths = [
        "/api/finance/business-bids",
        f"/api/finance/business-bids/{pid}",
    ]
    headers = {"X-CSRF-Token": finance_csrf}
    for path in paths:
        for method in ("post", "put", "patch", "delete"):
            if method == "delete":
                res = required_client.delete(path, headers=headers)
            else:
                res = getattr(required_client, method)(
                    path, json={"name": "hack"}, headers=headers
                )
            assert res.status_code == 405, f"{method.upper()} {path} -> {res.status_code}"

    after = required_client.get(f"/api/finance/business-bids/{pid}")
    assert after.status_code == 200
    assert after.json()["quoteTotal"] == before_total
    assert after.json()["name"] == "财务可见商务标"
