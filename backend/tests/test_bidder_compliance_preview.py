"""
模块：P10E 投标人匿名合规预览定向测试
用途：验收 strict bidder 只读聚合、字段白名单、角色隔离、空态、基点、失效收敛、审计脱敏与 no-store。
对接：app.api.bidder；app.services.bidder_compliance_preview_service；deps.require_bidder。
二次开发：仅固定合成口令与本地 SQLite；禁止外网、真实口令与白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import AuthAuditEventRow, Workspace
from app.services import auth_service, editor_state_service, project_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_bidder_p10e"
_OWNER_PASS = "TestPass-Bidder-Owner-0001!"
_ROLE_PASSWORDS = {
    "bidder": "TestPass-Bidder-Role-0001!",
    "finance": "TestPass-Finance-Bidder-Role-0001!",
    "hr": "TestPass-Hr-Bidder-Role-0001!",
    "bid_writer": "TestPass-Writer-Bidder-Role-0001!",
}
_PREVIEW_PATH = "/api/bidder/compliance-preview"
_TOP_KEYS = frozenset({"dataState", "summary"})
_SUMMARY_KEYS = frozenset(
    {
        "totalItems",
        "coveredItems",
        "uncoveredItems",
        "waivedItems",
        "coverageBasisPoints",
    }
)
# 响应与审计均不得出现的内部标识/原文泄漏标记
_FORBIDDEN_MARKERS = (
    "projectId",
    "project_id",
    "projectName",
    "workspaceId",
    "workspace_id",
    "sourceKey",
    "sourceText",
    "chapterIds",
    "outlineNodeIds",
    "responseMatrix",
    "SECRET_REQUIREMENT",
    "SECRET_CHAPTER",
    "SECRET_OUTLINE",
    "SECRET_NOTES",
    "apiKey",
    "api_key",
    "password",
    "csrf",
    "token_digest",
    "businessQuote",
    "parsedMarkdown",
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


def _login_bidder(client: TestClient) -> str:
    """用途：创建 bidder 成员并登录，返回 CSRF。"""
    csrf, _ = _owner_session(client)
    username = "user_bidder_p10e"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS["bidder"],
        role="bidder",
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS["bidder"])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10e"
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
    return res.json()["csrfToken"]


def _assert_no_leak(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"


def _assert_preview_shape(body: dict) -> None:
    assert set(body.keys()) == _TOP_KEYS
    assert body["dataState"] in {"ready", "empty"}
    summary = body["summary"]
    assert set(summary.keys()) == _SUMMARY_KEYS
    for key in (
        "totalItems",
        "coveredItems",
        "uncoveredItems",
        "waivedItems",
    ):
        assert isinstance(summary[key], int)
        assert summary[key] >= 0
    bp = summary["coverageBasisPoints"]
    assert bp is None or (isinstance(bp, int) and not isinstance(bp, bool))


def _seed_matrix_fixture() -> dict[str, str]:
    """
    用途：写入本空间技术标矩阵（含失效引用/豁免）、商务标与跨空间技术标，供聚合与隔离断言。
    返回：项目 id 字典（测试不得把这些 id 泄漏到预览响应）。
    """
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_bidder_p10e")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_bidder_p10e",
                    name="其他投标人空间",
                    owner_user_id="user_other_bidder_p10e",
                )
            )
            db.commit()

        tech_a = project_service.create_project(
            db,
            "ws_local",
            name="技术标A-SECRET",
            industry="能源",
            kind="technical",
            status="writing",
        )
        tech_b = project_service.create_project(
            db,
            "ws_local",
            name="技术标B-SECRET",
            industry="通用",
            kind="technical",
        )
        business = project_service.create_project(
            db,
            "ws_local",
            name="商务标不得计入",
            industry="能源",
            kind="business",
        )
        foreign = project_service.create_project(
            db,
            "ws_other_bidder_p10e",
            name="跨空间技术标不得计入",
            industry="跨域",
            kind="technical",
        )

        # 项目 A：8 covered + 2 uncovered + 1 waived = 11；另 1 条 covered 但引用失效 → uncovered
        # 有效：covered=8, uncovered=3, waived=1, total=12 → bp=8/11*10000=7273
        rows_a = []
        for i in range(8):
            rows_a.append(
                {
                    "id": f"mx_cov_{i}",
                    "kind": "requirement",
                    "sourceKey": f"requirement:SECRET_REQUIREMENT_COV_{i}",
                    "sourceIndex": i,
                    "sourceText": f"SECRET_REQUIREMENT 覆盖项{i}",
                    "chapterIds": ["chap_a"],
                    "outlineNodeIds": ["node_a"],
                    "status": "covered",
                    "notes": "SECRET_NOTES_covered",
                }
            )
        for i in range(2):
            rows_a.append(
                {
                    "id": f"mx_unc_{i}",
                    "kind": "requirement",
                    "sourceKey": f"requirement:SECRET_REQUIREMENT_UNC_{i}",
                    "sourceIndex": 8 + i,
                    "sourceText": f"SECRET_REQUIREMENT 未覆盖{i}",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "uncovered",
                    "notes": "SECRET_NOTES_uncovered",
                }
            )
        rows_a.append(
            {
                "id": "mx_waived_1",
                "kind": "requirement",
                "sourceKey": "requirement:SECRET_REQUIREMENT_WAIVED",
                "sourceIndex": 10,
                "sourceText": "SECRET_REQUIREMENT 豁免项",
                "chapterIds": [],
                "outlineNodeIds": [],
                "status": "waived",
                "notes": "SECRET_NOTES_waived",
            }
        )
        # 失效引用：原 covered，大纲/章节已不存在 → 收敛为 uncovered
        rows_a.append(
            {
                "id": "mx_dead_1",
                "kind": "requirement",
                "sourceKey": "requirement:SECRET_REQUIREMENT_DEAD",
                "sourceIndex": 11,
                "sourceText": "SECRET_REQUIREMENT 失效链接",
                "chapterIds": ["chap_gone"],
                "outlineNodeIds": ["node_gone"],
                "status": "covered",
                "notes": "SECRET_NOTES_dead",
            }
        )
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            tech_a.id,
            outline=[{"id": "node_a", "title": "SECRET_OUTLINE 安全方案"}],
            chapters=[{"id": "chap_a", "title": "SECRET_CHAPTER 安全方案"}],
            response_matrix=rows_a,
            parsed_markdown="SECRET 解析正文不得进入预览",
        )

        # 项目 B：空矩阵，不改变合计
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            tech_b.id,
            outline=[{"id": "node_b", "title": "空矩阵大纲"}],
            chapters=[{"id": "chap_b", "title": "空矩阵章节"}],
            response_matrix=[],
        )

        # 商务标带矩阵：不得计入
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            business.id,
            response_matrix=[
                {
                    "id": "mx_biz",
                    "kind": "requirement",
                    "sourceKey": "requirement:biz",
                    "sourceIndex": 0,
                    "sourceText": "商务标矩阵不得计入",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "covered",
                    "notes": "",
                }
            ],
        )

        # 跨空间技术标：不得计入
        editor_state_service.upsert_editor_state(
            db,
            "ws_other_bidder_p10e",
            foreign.id,
            outline=[{"id": "node_x", "title": "跨空间"}],
            chapters=[{"id": "chap_x", "title": "跨空间"}],
            response_matrix=[
                {
                    "id": "mx_foreign",
                    "kind": "requirement",
                    "sourceKey": "requirement:foreign",
                    "sourceIndex": 0,
                    "sourceText": "跨空间条目不得计入",
                    "chapterIds": ["chap_x"],
                    "outlineNodeIds": ["node_x"],
                    "status": "covered",
                    "notes": "",
                }
            ],
        )

        return {
            "tech_a": tech_a.id,
            "tech_b": tech_b.id,
            "business": business.id,
            "foreign": foreign.id,
        }
    finally:
        db.close()


def test_bidder_empty_preview_no_store(required_client):
    """bidder 无技术标矩阵时返回 empty 全零，coverageBasisPoints=null，且 no-store。"""
    _login_bidder(required_client)
    res = required_client.get(_PREVIEW_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    _assert_preview_shape(body)
    _assert_no_leak(body)
    assert body["dataState"] == "empty"
    assert body["summary"] == {
        "totalItems": 0,
        "coveredItems": 0,
        "uncoveredItems": 0,
        "waivedItems": 0,
        "coverageBasisPoints": None,
    }


def test_bidder_aggregates_reconciled_matrix_and_basis_points(required_client):
    """
    bidder 可读取当前空间技术标匿名聚合；失效引用按 uncovered 计入；
    基点=covered/(covered+uncovered)*10000 半入；不泄漏项目/原文。
    """
    ids = _seed_matrix_fixture()
    _login_bidder(required_client)
    res = required_client.get(_PREVIEW_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    _assert_preview_shape(body)
    _assert_no_leak(body)
    # 夹具中的项目 id 亦不得出现
    text = json.dumps(body, ensure_ascii=False)
    for pid in ids.values():
        assert pid not in text

    assert body["dataState"] == "ready"
    assert body["summary"] == {
        "totalItems": 12,
        "coveredItems": 8,
        "uncoveredItems": 3,
        "waivedItems": 1,
        "coverageBasisPoints": 7273,
    }


def test_non_bidder_roles_forbidden(required_client, role):
    """非 bidder 业务角色访问固定 403 role_forbidden。"""
    _seed_matrix_fixture()
    _login_role(required_client, role)
    res = required_client.get(_PREVIEW_PATH)
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "role_forbidden"
    _assert_no_leak(res.json())


@pytest.fixture
def role(request):
    return request.param


def pytest_generate_tests(metafunc):
    if "role" in metafunc.fixturenames and metafunc.function.__name__ == (
        "test_non_bidder_roles_forbidden"
    ):
        metafunc.parametrize("role", ["finance", "hr", "bid_writer"])


def test_owner_disabled_unauthenticated_denied(required_client, monkeypatch):
    """所有者、disabled、未登录均不得访问匿名合规预览。"""
    _seed_matrix_fixture()
    _owner_session(required_client)
    owner_get = required_client.get(_PREVIEW_PATH)
    assert owner_get.status_code == 403
    assert owner_get.json()["detail"]["code"] == "role_forbidden"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(_PREVIEW_PATH)
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 未登录：全局认证中间件固定 401 auth_required（P10E 契约第2节）
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            res = client.get(_PREVIEW_PATH)
            assert res.status_code == 401, res.text
            assert res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED
    finally:
        get_settings.cache_clear()


def test_cross_workspace_header_forbidden(required_client):
    """已登录 bidder 用非成员 X-Workspace-Id 必须 403 workspace_forbidden。"""
    _seed_matrix_fixture()
    _login_bidder(required_client)
    res = required_client.get(
        _PREVIEW_PATH,
        headers={"X-Workspace-Id": "ws_other_bidder_p10e"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "workspace_forbidden"
    _assert_no_leak(res.json())


def test_audit_read_is_anonymous_aggregate(required_client):
    """成功读取写固定审计；target=anonymous_aggregate，不含计数/项目/矩阵原文。"""
    _seed_matrix_fixture()
    _login_bidder(required_client)
    res = required_client.get(_PREVIEW_PATH)
    assert res.status_code == 200, res.text

    db = SessionLocal()
    try:
        events = list(
            db.query(AuthAuditEventRow)
            .filter(AuthAuditEventRow.action == "bidder_compliance_preview_read")
            .all()
        )
        assert len(events) >= 1
        latest = max(events, key=lambda e: e.created_at)
        assert latest.result == "success"
        assert latest.target == "anonymous_aggregate"
        blob = json.dumps(
            {
                "action": latest.action,
                "result": latest.result,
                "target": latest.target,
            },
            ensure_ascii=False,
        )
        for marker in (
            "totalItems",
            "coveredItems",
            "SECRET_REQUIREMENT",
            "SECRET_NOTES",
            "7273",
            "tech_a",
        ):
            assert marker not in blob
    finally:
        db.close()


def test_preview_rejects_non_get_methods(required_client):
    """预览路径非 GET 返回 405，且不产生写入副作用。"""
    csrf = _login_bidder(required_client)
    headers = {"X-CSRF-Token": csrf}
    for method in ("post", "put", "patch"):
        res = getattr(required_client, method)(
            _PREVIEW_PATH,
            json={"hack": 1},
            headers=headers,
        )
        assert res.status_code == 405, f"{method}: {res.text}"
    # delete 客户端不接受 json body
    res_del = required_client.delete(_PREVIEW_PATH, headers=headers)
    assert res_del.status_code == 405, res_del.text
