"""
模块：P10F 人力项目团队推荐快照定向测试
用途：先写失败用例验收 HR 快照 CRUD 语义、严格 bid_writer 投影、鉴权矩阵、CSRF、审计脱敏。
对接：app.api.hr；app.api.projects 投影；hr_team_recommendation_service；deps.require_hr / require_strict_bid_writer。
二次开发：仅固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    HrCredentialCardRow,
    Project,
    Workspace,
    utc_now,
)
from app.services import auth_service, project_service

# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_hr_p10f"
_OWNER_PASS = "TestPass-Hr-Team-Owner-0001!"
_ROLE_PASSWORDS = {
    "hr": "TestPass-Hr-Team-Role-0001!",
    "finance": "TestPass-Finance-Team-Role-0001!",
    "bidder": "TestPass-Bidder-Team-Role-0001!",
    "bid_writer": "TestPass-Writer-Team-Role-0001!",
}

_HR_PROJECTS = "/api/hr/team-recommendations/projects"
_HR_LIST = "/api/hr/team-recommendations"
_HR_DETAIL = "/api/hr/team-recommendations/{project_id}"
_BW_PROJECTION = "/api/projects/{project_id}/team-recommendation"
_CARDS = "/api/hr/credential-cards"

_SUMMARY_KEYS = frozenset({"projectId", "projectName", "memberCount", "updatedAt"})
_SELECTOR_KEYS = frozenset({"id", "name"})
_HR_MEMBER_KEYS = frozenset(
    {
        "order",
        "personName",
        "category",
        "credentialName",
        "level",
        "validUntil",
        "sourceCardId",
    }
)
_HR_DETAIL_KEYS = frozenset({"projectId", "projectName", "members", "updatedAt"})
_BW_MEMBER_KEYS = frozenset(
    {"order", "personName", "category", "credentialName", "level", "validUntil"}
)
_BW_KEYS = frozenset({"dataState", "members", "updatedAt"})
_FORBIDDEN_MARKERS = (
    "password",
    "csrf",
    "token_digest",
    "apiKey",
    "api_key",
    "createdByUserId",
    "created_by_user_id",
    "updatedByUserId",
    "idNumber",
    "phone",
    "mobile",
    "address",
    "attachment",
    "workspaceId",
    "workspace_id",
    "remark",
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


def _ensure_bootstrap():
    """用途：幂等初始化本地管理员，避免同测多次角色切换重复 bootstrap。"""
    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            return None
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_OWNER_USER,
            password=_OWNER_PASS,
            role=auth_service.ROLE_BID_WRITER,
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
    _ensure_bootstrap()
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


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10f"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
    )
    # 同测多次切换角色时成员可能已存在
    assert created.status_code in (201, 400, 409, 422), created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _login_hr(client: TestClient) -> str:
    return _login_role(client, "hr")


def _login_bid_writer(client: TestClient) -> str:
    return _login_role(client, "bid_writer")


def _assert_no_leak(payload: object) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lower = text.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"


def _create_project_via_orm(
    *,
    name: str = "P10F技术标",
    kind: str = "technical",
    workspace_id: str = "ws_local",
) -> Project:
    db = SessionLocal()
    try:
        return project_service.create_project(
            db,
            workspace_id,
            name=name,
            kind=kind,
        )
    finally:
        db.close()


def _create_card(
    client: TestClient,
    csrf: str,
    *,
    person_name: str = "张工",
    category: str = "professional",
    credential_name: str = "一级建造师",
    level: str = "市政",
    valid_until: str | None = "2028-12-31",
    remark: str = "内部备注不得快照",
    is_active: bool = True,
) -> dict:
    res = client.post(
        _CARDS,
        json={
            "personName": person_name,
            "category": category,
            "credentialName": credential_name,
            "level": level,
            "validUntil": valid_until,
            "remark": remark,
            "isActive": is_active,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_hr_project_selector_only_technical_id_name(required_client):
    """HR 项目选择器仅返回本空间技术标 id/name，不含行业/状态/正文等。"""
    csrf = _login_hr(required_client)
    tech = _create_project_via_orm(name="技术A", kind="technical")
    biz = _create_project_via_orm(name="商务B", kind="business")

    res = required_client.get(_HR_PROJECTS)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert "items" in body
    items = body["items"]
    ids = {x["id"] for x in items}
    assert tech.id in ids
    assert biz.id not in ids
    for item in items:
        assert set(item.keys()) == _SELECTOR_KEYS
        assert isinstance(item["id"], str)
        assert isinstance(item["name"], str)
    _assert_no_leak(body)


def test_put_create_update_empty_clear_and_snapshot_immutable(required_client):
    """
    PUT 首建 201、替换 200、空数组清空成员不删记录；
    快照在资质卡改名/停用后不变；列表摘要正确；详情含 sourceCardId 且无 remark。
    """
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    project = _create_project_via_orm(name="快照项目")
    card_a = _create_card(
        required_client, csrf, person_name="甲", credential_name="证甲", remark="备注甲"
    )
    card_b = _create_card(
        required_client, csrf, person_name="乙", credential_name="证乙", remark="备注乙"
    )

    # 无记录详情 404
    missing = required_client.get(_HR_DETAIL.format(project_id=project.id))
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "hr_team_recommendation_not_found"

    # 首建
    created = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card_a["id"], card_b["id"]]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.headers.get("cache-control", "").lower() == "no-store"
    cbody = created.json()
    assert set(cbody.keys()) == _HR_DETAIL_KEYS
    assert cbody["projectId"] == project.id
    assert cbody["projectName"] == "快照项目"
    assert len(cbody["members"]) == 2
    assert cbody["members"][0]["order"] == 1
    assert cbody["members"][0]["personName"] == "甲"
    assert cbody["members"][0]["sourceCardId"] == card_a["id"]
    assert cbody["members"][1]["order"] == 2
    assert cbody["members"][1]["sourceCardId"] == card_b["id"]
    for m in cbody["members"]:
        assert set(m.keys()) == _HR_MEMBER_KEYS
        assert "remark" not in m
    _assert_no_leak(cbody)

    # 列表摘要
    listed = required_client.get(_HR_LIST)
    assert listed.status_code == 200
    assert listed.headers.get("cache-control", "").lower() == "no-store"
    items = listed.json()["items"]
    assert len(items) == 1
    assert set(items[0].keys()) == _SUMMARY_KEYS
    assert items[0]["projectId"] == project.id
    assert items[0]["projectName"] == "快照项目"
    assert items[0]["memberCount"] == 2

    # 改卡：姓名与启停变化不得自动改快照
    patched_card = required_client.patch(
        f"{_CARDS}/{card_a['id']}",
        json={"personName": "甲改名", "credentialName": "证甲新", "isActive": False},
        headers=headers,
    )
    assert patched_card.status_code == 200

    detail = required_client.get(_HR_DETAIL.format(project_id=project.id))
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["members"][0]["personName"] == "甲"
    assert dbody["members"][0]["credentialName"] == "证甲"
    assert dbody["members"][0]["sourceCardId"] == card_a["id"]

    # 替换为单卡（仍有效的乙）
    updated = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card_b["id"]]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    ubody = updated.json()
    assert len(ubody["members"]) == 1
    assert ubody["members"][0]["personName"] == "乙"
    assert ubody["members"][0]["order"] == 1

    # 空数组清空成员，记录仍在（详情 200、成员空）
    cleared = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": []},
        headers=headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["members"] == []

    still = required_client.get(_HR_DETAIL.format(project_id=project.id))
    assert still.status_code == 200
    assert still.json()["members"] == []
    listed2 = required_client.get(_HR_LIST)
    assert listed2.json()["items"][0]["memberCount"] == 0


def test_put_validation_rejects_invalid_members_and_extra_keys(required_client):
    """重复/非字符串/空值/额外键/非对象/无效卡/停用卡/超 30 统一 422 固定脱敏。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    project = _create_project_via_orm(name="校验项目")
    active = _create_card(required_client, csrf, person_name="有效")
    inactive = _create_card(
        required_client, csrf, person_name="停用", is_active=False
    )

    invalid_payloads = [
        {"memberCardIds": [active["id"], active["id"]]},
        {"memberCardIds": [123]},
        {"memberCardIds": [None]},
        {"memberCardIds": [""]},
        {"memberCardIds": [active["id"]], "extra": 1},
        {"memberCardIds": inactive["id"]},  # 非数组
        {"memberCardIds": ["hcc_not_exist"]},
        {"memberCardIds": [inactive["id"]]},
        {"memberCardIds": [active["id"]] * 31},
        [],  # 非对象
        "x",
    ]
    for payload in invalid_payloads:
        if isinstance(payload, (list, str)):
            res = required_client.put(
                _HR_DETAIL.format(project_id=project.id),
                content=json.dumps(payload),
                headers={**headers, "Content-Type": "application/json"},
            )
        else:
            res = required_client.put(
                _HR_DETAIL.format(project_id=project.id),
                json=payload,
                headers=headers,
            )
        assert res.status_code == 422, f"{payload!r} -> {res.status_code} {res.text}"
        detail = res.json()["detail"]
        assert detail == {
            "code": "invalid_hr_team_recommendation",
            "message": "团队推荐参数不合法",
        }
        text = res.text
        assert active["id"] not in text or "memberCardIds" not in text
        assert "hcc_not_exist" not in text
        assert "Traceback" not in text


def test_put_rejects_business_and_missing_project(required_client):
    """商务标/不存在/跨空间项目统一 404 hr_team_project_not_found。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    biz = _create_project_via_orm(name="商务", kind="business")
    card = _create_card(required_client, csrf)

    for pid in (biz.id, "prj_missing", "prj_probe"):
        res = required_client.put(
            _HR_DETAIL.format(project_id=pid),
            json={"memberCardIds": [card["id"]]},
            headers=headers,
        )
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["code"] == "hr_team_project_not_found"
        get_res = required_client.get(_HR_DETAIL.format(project_id=pid))
        assert get_res.status_code == 404
        assert get_res.json()["detail"]["code"] == "hr_team_project_not_found"


def test_csrf_required_for_put(required_client):
    """PUT 无 CSRF 或错误 CSRF 须拒绝。"""
    csrf = _login_hr(required_client)
    project = _create_project_via_orm()
    card = _create_card(required_client, csrf)

    no_csrf = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card["id"]]},
    )
    assert no_csrf.status_code == 403

    bad = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card["id"]]},
        headers={"X-CSRF-Token": "definitely-wrong-csrf"},
    )
    assert bad.status_code == 403


@pytest.mark.parametrize("role", ["bid_writer", "finance", "bidder"])
def test_non_hr_roles_forbidden_on_hr_routes(required_client, role):
    """非 hr 角色访问 HR 团队推荐固定 403 role_forbidden。"""
    csrf = _login_role(required_client, role)
    headers = {"X-CSRF-Token": csrf}
    project = _create_project_via_orm()

    for method, path in (
        ("get", _HR_PROJECTS),
        ("get", _HR_LIST),
        ("get", _HR_DETAIL.format(project_id=project.id)),
    ):
        res = getattr(required_client, method)(path)
        assert res.status_code == 403, path
        assert res.json()["detail"]["code"] == "role_forbidden"

    put_res = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": []},
        headers=headers,
    )
    assert put_res.status_code == 403
    assert put_res.json()["detail"]["code"] == "role_forbidden"


def test_owner_disabled_unauthenticated_denied_hr(required_client, monkeypatch):
    """
    所有者、disabled、未登录均不得访问 HR 团队推荐。
    required 已 bootstrap 后未登录：全局中间件固定 401 + detail.code=auth_required。
    """
    csrf, _ = _owner_session(required_client)
    project = _create_project_via_orm()
    owner_get = required_client.get(_HR_LIST)
    assert owner_get.status_code == 403
    assert owner_get.json()["detail"]["code"] == "role_forbidden"
    owner_put = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert owner_put.status_code == 403
    assert owner_put.json()["detail"]["code"] == "role_forbidden"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(_HR_LIST)
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 已完成 bootstrap 后无会话：精确 401 auth_required（不得接受 403）
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            for path in (
                _HR_PROJECTS,
                _HR_LIST,
                _HR_DETAIL.format(project_id=project.id),
            ):
                res = client.get(path)
                assert res.status_code == 401, f"{path} -> {res.status_code} {res.text}"
                assert res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED
            put_res = client.put(
                _HR_DETAIL.format(project_id=project.id),
                json={"memberCardIds": []},
                headers={"X-CSRF-Token": "no-session"},
            )
            assert put_res.status_code == 401, put_res.text
            assert put_res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED
    finally:
        get_settings.cache_clear()


def test_non_member_workspace_header_forbidden(required_client):
    """非成员 X-Workspace-Id 保留 workspace_forbidden。"""
    _login_hr(required_client)
    res = required_client.get(
        _HR_LIST,
        headers={"X-Workspace-Id": "ws_other_p10f_not_member"},
    )
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "workspace_forbidden"


def test_bid_writer_projection_empty_ready_and_field_min(required_client):
    """严格 bid_writer 投影：无记录 empty；有成员 ready；字段最小；无 htr/sourceCardId/remark。"""
    # 先以 HR 建卡
    hr_csrf = _login_hr(required_client)
    project = _create_project_via_orm(name="投影项目")
    card = _create_card(
        required_client,
        hr_csrf,
        person_name="协作显示名",
        category="safety",
        credential_name="安全员B证",
        level="B",
        valid_until="2027-12-31",
        remark="不得出现在投影",
    )
    # 无记录时 bid_writer empty
    _login_bid_writer(required_client)
    empty = required_client.get(_BW_PROJECTION.format(project_id=project.id))
    assert empty.status_code == 200, empty.text
    assert empty.headers.get("cache-control", "").lower() == "no-store"
    ebody = empty.json()
    assert set(ebody.keys()) == _BW_KEYS
    assert ebody["dataState"] == "empty"
    assert ebody["members"] == []
    assert ebody["updatedAt"] is None
    _assert_no_leak(ebody)

    # HR 写入（重新登录刷新 CSRF）
    hr_csrf = _login_hr(required_client)
    put = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card["id"]]},
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert put.status_code == 201, put.text

    _login_bid_writer(required_client)
    ready = required_client.get(_BW_PROJECTION.format(project_id=project.id))
    assert ready.status_code == 200, ready.text
    assert ready.headers.get("cache-control", "").lower() == "no-store"
    rbody = ready.json()
    assert set(rbody.keys()) == _BW_KEYS
    assert rbody["dataState"] == "ready"
    assert rbody["updatedAt"] is not None
    assert len(rbody["members"]) == 1
    m0 = rbody["members"][0]
    assert set(m0.keys()) == _BW_MEMBER_KEYS
    assert m0["order"] == 1
    assert m0["personName"] == "协作显示名"
    assert m0["category"] == "safety"
    assert m0["credentialName"] == "安全员B证"
    assert m0["level"] == "B"
    assert m0["validUntil"] == "2027-12-31"
    blob = json.dumps(rbody, ensure_ascii=False)
    assert "sourceCardId" not in blob
    assert "source_card_id" not in blob
    assert "htr_" not in blob
    assert "备注" not in blob
    assert "projectName" not in blob
    assert "projectId" not in blob
    _assert_no_leak(rbody)

    # 清空后 empty，且不得 404 泄露
    hr_csrf = _login_hr(required_client)
    cleared = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": []},
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert cleared.status_code == 200
    _login_bid_writer(required_client)
    empty2 = required_client.get(_BW_PROJECTION.format(project_id=project.id))
    assert empty2.status_code == 200
    assert empty2.json()["dataState"] == "empty"
    assert empty2.json()["members"] == []
    assert empty2.json()["updatedAt"] is None


def test_bid_writer_projection_auth_matrix(required_client, monkeypatch):
    """
    投影依赖矩阵：
    - disabled / hr / finance / bidder → 403 role_forbidden
    - owner 会话仅当 member.role 精确为 bid_writer 时允许（角色精确匹配，非 is_owner 隐式放行）
    - 非成员 X-Workspace-Id → workspace_forbidden
    - required 已 bootstrap 后未登录 → 精确 401 auth_required
    """
    project = _create_project_via_orm(name="投影鉴权")
    path = _BW_PROJECTION.format(project_id=project.id)

    # 非 bid_writer 角色一律拒绝（含 is_owner=false 的 hr/finance/bidder）
    for role in ("hr", "finance", "bidder"):
        _login_role(required_client, role)
        res = required_client.get(path)
        assert res.status_code == 403, role
        assert res.json()["detail"]["code"] == "role_forbidden"

    # bootstrap owner 的 member.role=bid_writer 且 is_owner=True：
    # 允许投影是因为 role 精确匹配 bid_writer，不是 is_owner 隐式绕过。
    # 反证：同一 owner 访问 HR 路由在 test_owner_disabled_unauthenticated_denied_hr 中固定 403。
    _owner_session(required_client)
    owner_ok = required_client.get(path)
    assert owner_ok.status_code == 200
    assert owner_ok.json()["dataState"] == "empty"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(path)
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    # required 已完成 bootstrap 后无会话：精确 401 auth_required（不得接受 403）
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            res = client.get(path)
            assert res.status_code == 401, res.text
            assert res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED
    finally:
        get_settings.cache_clear()

    bw_csrf = _login_bid_writer(required_client)
    forbidden_ws = required_client.get(
        path,
        headers={"X-Workspace-Id": "ws_not_member_p10f"},
    )
    assert forbidden_ws.status_code == 403
    assert forbidden_ws.json()["detail"]["code"] == "workspace_forbidden"
    _ = bw_csrf


def _create_foreign_workspace_technical_project() -> Project:
    """
    用途：建立真实第二工作空间及其真实技术标项目，供跨空间 404/选择器隔离断言。
    对接：Workspace ORM；project_service.create_project。
    二次开发：不得把当前用户加入该空间成员表。
    """
    foreign_ws = "ws_other_p10f_team"
    db = SessionLocal()
    try:
        other = db.get(Workspace, foreign_ws)
        if other is None:
            db.add(
                Workspace(
                    id=foreign_ws,
                    name="其他团队推荐空间P10F",
                    owner_user_id="user_other_p10f_team",
                )
            )
            db.commit()
        return project_service.create_project(
            db,
            foreign_ws,
            name="跨空间技术标不得出现",
            kind="technical",
        )
    finally:
        db.close()


def test_real_cross_workspace_technical_project_isolation(required_client):
    """
    真实第二工作空间技术标：
    - 严格 HR 对其 projectId 的 GET/PUT → 404 hr_team_project_not_found
    - 严格 bid_writer 投影同 projectId → 既有项目 404（禁止 HR 专用码）
    - HR 项目选择器不得返回该项目
    """
    foreign = _create_foreign_workspace_technical_project()
    local = _create_project_via_orm(name="本空间技术标可见")

    hr_csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": hr_csrf}

    # HR 选择器：仅本空间技术标，绝不含跨空间真实项目
    selector = required_client.get(_HR_PROJECTS)
    assert selector.status_code == 200, selector.text
    ids = {item["id"] for item in selector.json()["items"]}
    assert local.id in ids
    assert foreign.id not in ids

    # 严格 HR：跨空间真实 projectId 统一 404 hr_team_project_not_found
    hr_get = required_client.get(_HR_DETAIL.format(project_id=foreign.id))
    assert hr_get.status_code == 404, hr_get.text
    assert hr_get.json()["detail"]["code"] == "hr_team_project_not_found"

    card = _create_card(required_client, hr_csrf, person_name="跨空间探测")
    hr_put = required_client.put(
        _HR_DETAIL.format(project_id=foreign.id),
        json={"memberCardIds": [card["id"]]},
        headers=headers,
    )
    assert hr_put.status_code == 404, hr_put.text
    assert hr_put.json()["detail"]["code"] == "hr_team_project_not_found"

    # 严格 bid_writer：同一跨空间 projectId 使用既有项目不可访问 404，禁止 HR 错误码
    _login_bid_writer(required_client)
    bw = required_client.get(_BW_PROJECTION.format(project_id=foreign.id))
    assert bw.status_code == 404, bw.text
    detail = bw.json()["detail"]
    if isinstance(detail, dict):
        assert detail.get("code") != "hr_team_project_not_found"
        assert detail.get("code") != "hr_team_recommendation_not_found"
    else:
        assert "hr_team" not in str(detail)


def test_bid_writer_projection_project_not_found_semantics(required_client):
    """不存在/非技术标使用既有项目不可访问 404，不得用 HR 错误码。"""
    _login_bid_writer(required_client)
    biz = _create_project_via_orm(name="商务投影", kind="business")
    for pid in (biz.id, "prj_missing_bw"):
        res = required_client.get(_BW_PROJECTION.format(project_id=pid))
        assert res.status_code == 404, res.text
        detail = res.json()["detail"]
        # 既有项目 404 文案/结构，禁止 HR 专用码
        if isinstance(detail, dict):
            assert detail.get("code") != "hr_team_project_not_found"
            assert detail.get("code") != "hr_team_recommendation_not_found"
        else:
            assert "hr_team" not in str(detail)


def test_audit_desensitized_for_create_update_and_read(required_client):
    """创建/更新/ready 读取写审计；target 仅 htr_*，无姓名/资质/项目/卡/数量/原文。"""
    hr_csrf = _login_hr(required_client)
    project = _create_project_via_orm(name="审计敏感项目名XYZ")
    secret_name = "审计姓名SECRET"
    card = _create_card(
        required_client,
        hr_csrf,
        person_name=secret_name,
        credential_name="审计证书ABC",
        remark="审计备注SECRET",
    )
    created = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card["id"]]},
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert created.status_code == 201
    updated = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": [card["id"]]},
        headers={"X-CSRF-Token": hr_csrf},
    )
    assert updated.status_code == 200

    _login_bid_writer(required_client)
    ready = required_client.get(_BW_PROJECTION.format(project_id=project.id))
    assert ready.status_code == 200
    assert ready.json()["dataState"] == "ready"

    db = SessionLocal()
    try:
        events = list(
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action.in_(
                    [
                        "hr_team_recommendation_create",
                        "hr_team_recommendation_update",
                        "bid_writer_team_recommendation_read",
                    ]
                )
            )
            .all()
        )
        actions = {e.action for e in events}
        assert "hr_team_recommendation_create" in actions
        assert "hr_team_recommendation_update" in actions
        assert "bid_writer_team_recommendation_read" in actions
        for e in events:
            assert e.target is not None
            assert e.target.startswith("htr_")
            blob = " ".join(
                [
                    e.action or "",
                    e.result or "",
                    e.target or "",
                    e.actor_user_id or "",
                    e.workspace_id or "",
                ]
            )
            assert secret_name not in blob
            assert "审计证书ABC" not in blob
            assert "审计备注SECRET" not in blob
            assert "审计敏感项目名XYZ" not in blob
            assert project.id not in blob
            assert card["id"] not in blob
            assert "memberCardIds" not in blob
            assert "memberCount" not in blob
    finally:
        db.close()


def test_boundary_zero_and_thirty_members(required_client):
    """成员数 0 与 30 边界合法。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    project = _create_project_via_orm(name="边界项目")
    card_ids = []
    for i in range(30):
        card = _create_card(
            required_client,
            csrf,
            person_name=f"人{i}",
            credential_name=f"证{i}",
        )
        card_ids.append(card["id"])

    ok30 = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": card_ids},
        headers=headers,
    )
    assert ok30.status_code == 201, ok30.text
    assert len(ok30.json()["members"]) == 30
    assert ok30.json()["members"][-1]["order"] == 30

    ok0 = required_client.put(
        _HR_DETAIL.format(project_id=project.id),
        json={"memberCardIds": []},
        headers=headers,
    )
    assert ok0.status_code == 200
    assert ok0.json()["members"] == []
