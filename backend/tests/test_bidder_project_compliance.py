"""
模块：P10G 投标人项目级合规统计预览定向测试
用途：验收 strict bidder 项目选择器与单项目统计投影、角色隔离、统一 404、基点口径、审计脱敏与 no-store。
对接：app.api.bidder；app.services.bidder_project_compliance_service；deps.require_bidder。
二次开发：仅固定合成口令与本地 SQLite；禁止外网、真实口令与白名单外改动；不得改变 P10E 响应语义。
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
_OWNER_USER = "admin_bidder_p10g"
_OWNER_PASS = "TestPass-Bidder-Owner-P10G-0001!"
_ROLE_PASSWORDS = {
    "bidder": "TestPass-Bidder-Role-P10G-0001!",
    "finance": "TestPass-Finance-Bidder-P10G-0001!",
    "hr": "TestPass-Hr-Bidder-P10G-0001!",
    "bid_writer": "TestPass-Writer-Bidder-P10G-0001!",
}
_SELECTOR_PATH = "/api/bidder/project-compliance/projects"
_DETAIL_PATH = "/api/bidder/project-compliance/{project_id}"
_P10E_PREVIEW_PATH = "/api/bidder/compliance-preview"

_SUMMARY_KEYS = frozenset(
    {
        "totalItems",
        "coveredItems",
        "uncoveredItems",
        "waivedItems",
        "coverageBasisPoints",
    }
)
_DETAIL_TOP_KEYS = frozenset({"dataState", "summary"})
_SELECTOR_ITEM_KEYS = frozenset({"id", "name"})

# 详情响应与审计均不得出现的内部标识/原文泄漏标记
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
    "industry",
    "technicalPlanStep",
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
    username = "user_bidder_p10g"
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
    username = f"user_{role}_p10g"
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


def _assert_detail_shape(body: dict) -> None:
    assert set(body.keys()) == _DETAIL_TOP_KEYS
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


def _assert_not_found(res, *, project_id: str | None = None) -> None:
    """
    用途：断言统一 404 且固定错误码；可选校验路径 project_id 不回显。
    对接：P10G 固定 bidder_project_compliance_not_found 契约。
    二次开发：响应文本不得出现请求路径中的 project_id 或敏感标记。
    """
    assert res.status_code == 404, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "bidder_project_compliance_not_found"
    _assert_no_leak(res.json())
    # 固定错误不得回显路径输入的 project_id
    if project_id is not None:
        assert project_id not in res.text
        body_text = json.dumps(res.json(), ensure_ascii=False)
        assert project_id not in body_text


def _seed_projects() -> dict[str, str]:
    """
    用途：写入本空间技术标（含 ready/empty/未知状态）、商务标与跨空间技术标。
    返回：项目 id 字典。
    """
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_bidder_p10g")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_bidder_p10g",
                    name="其他投标人空间P10G",
                    owner_user_id="user_other_bidder_p10g",
                )
            )
            db.commit()

        tech_ready = project_service.create_project(
            db,
            "ws_local",
            name="技术标Ready-P10G",
            industry="能源",
            kind="technical",
            status="writing",
        )
        tech_empty = project_service.create_project(
            db,
            "ws_local",
            name="技术标Empty-P10G",
            industry="通用",
            kind="technical",
        )
        business = project_service.create_project(
            db,
            "ws_local",
            name="商务标不得出现",
            industry="能源",
            kind="business",
        )
        foreign = project_service.create_project(
            db,
            "ws_other_bidder_p10g",
            name="跨空间技术标不得出现",
            industry="跨域",
            kind="technical",
        )

        # ready：8 covered + 2 uncovered + 1 waived + 1 未知状态(按 uncovered)
        # + 1 covered 失效引用 → uncovered
        # 有效：covered=8, uncovered=2+1+1=4, waived=1, total=13 → bp=8/12*10000=6667
        rows = []
        for i in range(8):
            rows.append(
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
            rows.append(
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
        rows.append(
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
        rows.append(
            {
                "id": "mx_unknown_1",
                "kind": "requirement",
                "sourceKey": "requirement:SECRET_REQUIREMENT_UNKNOWN",
                "sourceIndex": 11,
                "sourceText": "SECRET_REQUIREMENT 未知状态",
                "chapterIds": [],
                "outlineNodeIds": [],
                "status": "weird_status",
                "notes": "SECRET_NOTES_unknown",
            }
        )
        # 失效引用：原 covered，大纲/章节已不存在 → 收敛为 uncovered
        rows.append(
            {
                "id": "mx_dead_1",
                "kind": "requirement",
                "sourceKey": "requirement:SECRET_REQUIREMENT_DEAD",
                "sourceIndex": 12,
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
            tech_ready.id,
            outline=[{"id": "node_a", "title": "SECRET_OUTLINE 安全方案"}],
            chapters=[{"id": "chap_a", "title": "SECRET_CHAPTER 安全方案"}],
            response_matrix=rows,
            parsed_markdown="SECRET 解析正文不得进入预览",
        )

        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            tech_empty.id,
            outline=[{"id": "node_b", "title": "空矩阵大纲"}],
            chapters=[{"id": "chap_b", "title": "空矩阵章节"}],
            response_matrix=[],
        )

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

        editor_state_service.upsert_editor_state(
            db,
            "ws_other_bidder_p10g",
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
            "tech_ready": tech_ready.id,
            "tech_empty": tech_empty.id,
            "business": business.id,
            "foreign": foreign.id,
        }
    finally:
        db.close()


# ---------- 失败先测：路由尚不存在时应 404（实现前）/ 实现后变 200 ----------


def test_selector_lists_only_local_technical_id_name(required_client):
    """选择器仅返回当前空间 technical 的 id/name，不含商务标/跨空间，且 no-store。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    res = required_client.get(_SELECTOR_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == {"items"}
    items = body["items"]
    assert isinstance(items, list)
    assert len(items) == 2
    by_id = {x["id"]: x for x in items}
    assert set(by_id.keys()) == {ids["tech_ready"], ids["tech_empty"]}
    assert by_id[ids["tech_ready"]]["name"] == "技术标Ready-P10G"
    assert by_id[ids["tech_empty"]]["name"] == "技术标Empty-P10G"
    for item in items:
        assert set(item.keys()) == _SELECTOR_ITEM_KEYS
    # 商务标与跨空间不得出现
    all_ids = {x["id"] for x in items}
    assert ids["business"] not in all_ids
    assert ids["foreign"] not in all_ids
    # 选择器允许 id/name，但不得泄漏其它项目字段
    text = json.dumps(body, ensure_ascii=False)
    for marker in (
        "industry",
        "workspaceId",
        "technicalPlanStep",
        "responseMatrix",
        "SECRET_REQUIREMENT",
    ):
        assert marker not in text


def test_detail_ready_unknown_status_and_basis_points(required_client):
    """
    单项目 ready：未知 status 按 uncovered；失效引用收敛为 uncovered；
    基点=covered/(covered+uncovered)*10000 半入；响应无项目字段。
    """
    ids = _seed_projects()
    _login_bidder(required_client)
    path = _DETAIL_PATH.format(project_id=ids["tech_ready"])
    res = required_client.get(path)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    _assert_detail_shape(body)
    _assert_no_leak(body)
    # 路径参数 project id 不得回显到响应体
    assert ids["tech_ready"] not in json.dumps(body, ensure_ascii=False)
    assert body["dataState"] == "ready"
    assert body["summary"] == {
        "totalItems": 13,
        "coveredItems": 8,
        "uncoveredItems": 4,
        "waivedItems": 1,
        "coverageBasisPoints": 6667,
    }


def test_detail_empty_is_200(required_client):
    """空矩阵技术标返回 200 empty，全零且 coverageBasisPoints=null。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    path = _DETAIL_PATH.format(project_id=ids["tech_empty"])
    res = required_client.get(path)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    _assert_detail_shape(body)
    _assert_no_leak(body)
    assert body["dataState"] == "empty"
    assert body["summary"] == {
        "totalItems": 0,
        "coveredItems": 0,
        "uncoveredItems": 0,
        "waivedItems": 0,
        "coverageBasisPoints": None,
    }


def test_foreign_business_fake_id_unified_404(required_client):
    """跨空间、商务标、伪造 ID 统一 404；响应不得回显请求的 project_id。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    for pid in (ids["foreign"], ids["business"], "proj_fake_p10g_not_exist"):
        res = required_client.get(_DETAIL_PATH.format(project_id=pid))
        _assert_not_found(res, project_id=pid)


def test_owner_with_exact_bidder_role_allowed(required_client):
    """
    所有者身份不能替代角色；但所有者当前成员角色精确为 bidder 时按角色允许。
    使用 bootstrap_local_admin(role=ROLE_BIDDER) 创建该所有者后登录，
    断言选择器与当前空间技术标详情均 200，详情仍最小投影与 no-store。
    """
    ids = _seed_projects()
    # 所有者身份 + 精确 bidder 角色（非 is_owner 隐式绕过）
    _bootstrap(role=auth_service.ROLE_BIDDER)
    login_res = _login(required_client, _OWNER_USER, _OWNER_PASS)
    assert login_res.status_code == 200, login_res.text
    login_body = login_res.json()
    workspaces = login_body.get("workspaces") or []
    assert any(
        w.get("role") == auth_service.ROLE_BIDDER and w.get("isOwner") is True
        for w in workspaces
    ), login_body

    # 选择器：200 + 仅 id/name + no-store
    selector = required_client.get(_SELECTOR_PATH)
    assert selector.status_code == 200, selector.text
    assert selector.headers.get("cache-control", "").lower() == "no-store"
    sel_body = selector.json()
    assert set(sel_body.keys()) == {"items"}
    items = sel_body["items"]
    assert isinstance(items, list)
    assert len(items) == 2
    by_id = {x["id"]: x for x in items}
    assert set(by_id.keys()) == {ids["tech_ready"], ids["tech_empty"]}
    for item in items:
        assert set(item.keys()) == _SELECTOR_ITEM_KEYS

    # 技术标详情：200 + 最小投影 + no-store
    detail = required_client.get(
        _DETAIL_PATH.format(project_id=ids["tech_ready"])
    )
    assert detail.status_code == 200, detail.text
    assert detail.headers.get("cache-control", "").lower() == "no-store"
    body = detail.json()
    _assert_detail_shape(body)
    _assert_no_leak(body)
    assert ids["tech_ready"] not in json.dumps(body, ensure_ascii=False)
    assert body["dataState"] == "ready"
    assert body["summary"] == {
        "totalItems": 13,
        "coveredItems": 8,
        "uncoveredItems": 4,
        "waivedItems": 1,
        "coverageBasisPoints": 6667,
    }


def test_non_bidder_roles_forbidden_on_both_paths(required_client, role):
    """非 bidder 业务角色访问选择器与详情均 403 role_forbidden。"""
    ids = _seed_projects()
    _login_role(required_client, role)
    for path in (
        _SELECTOR_PATH,
        _DETAIL_PATH.format(project_id=ids["tech_ready"]),
    ):
        res = required_client.get(path)
        assert res.status_code == 403, f"{role} {path}: {res.text}"
        assert res.json()["detail"]["code"] == "role_forbidden"
        _assert_no_leak(res.json())


@pytest.fixture
def role(request):
    return request.param


def pytest_generate_tests(metafunc):
    if "role" in metafunc.fixturenames and metafunc.function.__name__ == (
        "test_non_bidder_roles_forbidden_on_both_paths"
    ):
        metafunc.parametrize("role", ["finance", "hr", "bid_writer"])


def test_owner_disabled_unauthenticated_denied(required_client, monkeypatch):
    """所有者（非 bidder 角色）、disabled、未登录均不得访问。"""
    ids = _seed_projects()
    detail = _DETAIL_PATH.format(project_id=ids["tech_ready"])

    _owner_session(required_client)
    for path in (_SELECTOR_PATH, detail):
        owner_get = required_client.get(path)
        assert owner_get.status_code == 403, path
        assert owner_get.json()["detail"]["code"] == "role_forbidden"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            for path in (_SELECTOR_PATH, detail):
                res = client.get(path)
                assert res.status_code == 403, path
                assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 未登录：全局认证中间件固定 401 auth_required
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            for path in (_SELECTOR_PATH, detail):
                res = client.get(path)
                assert res.status_code == 401, f"{path}: {res.text}"
                assert res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED
    finally:
        get_settings.cache_clear()


def test_cross_workspace_header_forbidden(required_client):
    """已登录 bidder 用非成员 X-Workspace-Id 必须 403 workspace_forbidden。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    for path in (
        _SELECTOR_PATH,
        _DETAIL_PATH.format(project_id=ids["tech_ready"]),
    ):
        res = required_client.get(
            path,
            headers={"X-Workspace-Id": "ws_other_bidder_p10g"},
        )
        assert res.status_code == 403, path
        assert res.json()["detail"]["code"] == "workspace_forbidden"
        _assert_no_leak(res.json())


def test_selector_does_not_write_audit(required_client):
    """项目选择器成功读取不写审计。"""
    _seed_projects()
    _login_bidder(required_client)

    db = SessionLocal()
    try:
        before = (
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action.in_(
                    [
                        "bidder_project_compliance_read",
                        "bidder_compliance_preview_read",
                    ]
                )
            )
            .count()
        )
    finally:
        db.close()

    res = required_client.get(_SELECTOR_PATH)
    assert res.status_code == 200, res.text

    db = SessionLocal()
    try:
        after = (
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action.in_(
                    [
                        "bidder_project_compliance_read",
                        "bidder_compliance_preview_read",
                    ]
                )
            )
            .count()
        )
        assert after == before
    finally:
        db.close()


def test_detail_audit_is_desensitized(required_client):
    """成功详情只写固定审计；target=project_compliance，不含项目/计数/矩阵原文。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    res = required_client.get(_DETAIL_PATH.format(project_id=ids["tech_ready"]))
    assert res.status_code == 200, res.text

    db = SessionLocal()
    try:
        events = list(
            db.query(AuthAuditEventRow)
            .filter(AuthAuditEventRow.action == "bidder_project_compliance_read")
            .all()
        )
        assert len(events) >= 1
        latest = max(events, key=lambda e: e.created_at)
        assert latest.result == "success"
        assert latest.target == "project_compliance"
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
            "6667",
            ids["tech_ready"],
            "技术标Ready-P10G",
        ):
            assert marker not in blob
    finally:
        db.close()


def test_paths_reject_non_get_methods(required_client):
    """两条路径非 GET 返回 405，且不产生写入副作用。"""
    ids = _seed_projects()
    csrf = _login_bidder(required_client)
    headers = {"X-CSRF-Token": csrf}
    paths = (
        _SELECTOR_PATH,
        _DETAIL_PATH.format(project_id=ids["tech_ready"]),
    )
    for path in paths:
        for method in ("post", "put", "patch"):
            res = getattr(required_client, method)(
                path,
                json={"hack": 1},
                headers=headers,
            )
            assert res.status_code == 405, f"{method} {path}: {res.text}"
        res_del = required_client.delete(path, headers=headers)
        assert res_del.status_code == 405, f"delete {path}: {res_del.text}"


def test_p10e_anonymous_preview_not_regressed(required_client):
    """P10E 匿名预览在有技术标矩阵时仍返回工作空间级聚合，语义不变。"""
    ids = _seed_projects()
    _login_bidder(required_client)
    res = required_client.get(_P10E_PREVIEW_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"dataState", "summary"}
    assert body["dataState"] == "ready"
    # 工作空间级：ready 项目 13 条 + empty 0 = 13；不得含项目 id
    assert body["summary"]["totalItems"] == 13
    assert body["summary"]["coveredItems"] == 8
    assert body["summary"]["uncoveredItems"] == 4
    assert body["summary"]["waivedItems"] == 1
    assert body["summary"]["coverageBasisPoints"] == 6667
    text = json.dumps(body, ensure_ascii=False)
    for pid in ids.values():
        assert pid not in text
