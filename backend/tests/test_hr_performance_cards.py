"""
模块：P10H 严格 HR 人员业绩素材卡定向测试
用途：验收业绩卡 CRUD（无删除）、摘要/详情投影、鉴权隔离、CSRF、审计脱敏与跨空间 404。
对接：app.api.hr；app.services.hr_performance_service；deps.require_hr。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import AuthAuditEventRow, HrPerformanceCardRow, Workspace
from app.services import auth_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_hr_p10h"
_OWNER_PASS = "TestPass-Hr-Performance-Owner-0001!"
_ROLE_PASSWORDS = {
    "hr": "TestPass-Hr-Performance-Role-0001!",
    "finance": "TestPass-Finance-Performance-Role-0001!",
    "bidder": "TestPass-Bidder-Performance-Role-0001!",
    "bid_writer": "TestPass-Writer-Performance-Role-0001!",
}
_SUMMARY_KEYS = frozenset(
    {
        "id",
        "personName",
        "projectName",
        "projectRole",
        "completedYear",
        "isActive",
        "createdAt",
        "updatedAt",
    }
)
_DETAIL_KEYS = _SUMMARY_KEYS | {"performanceSummary", "remark"}
_FORBIDDEN_MARKERS = (
    "password",
    "csrf",
    "token_digest",
    "apiKey",
    "api_key",
    "createdByUserId",
    "created_by_user_id",
    "idNumber",
    "id_card",
    "phone",
    "mobile",
    "address",
    "attachment",
    "photo",
    "workspaceId",
    "workspace_id",
    "contractAmount",
    "resume",
)
_LIST_PATH = "/api/hr/performance-cards"
_INVALID_DETAIL = {
    "code": "invalid_hr_performance",
    "message": "人员业绩卡参数不合法",
}
_NOT_FOUND_CODE = "hr_performance_not_found"


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


def _login_hr(client: TestClient) -> str:
    """用途：创建 hr 成员并登录，返回 CSRF。"""
    csrf, _ = _owner_session(client)
    username = "user_hr_p10h"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS["hr"],
        role="hr",
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS["hr"])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10h"
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


def _valid_create_body(**overrides) -> dict:
    body = {
        "personName": "李工",
        "projectName": "某市政管廊项目",
        "projectRole": "项目经理",
        "completedYear": 2024,
        "performanceSummary": "负责总体统筹与关键节点交付",
        "remark": "仅内部协作备注",
        "isActive": True,
    }
    body.update(overrides)
    return body


def test_hr_crud_list_summary_without_detail_fields(required_client):
    """HR 可创建/列表/详情/更新/启停；列表不含摘要与备注；无 DELETE。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    empty = required_client.get(_LIST_PATH)
    assert empty.status_code == 200, empty.text
    assert empty.headers.get("cache-control", "").lower() == "no-store"
    assert empty.json() == {"items": []}

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.headers.get("cache-control", "").lower() == "no-store"
    card = created.json()
    assert set(card.keys()) == _DETAIL_KEYS
    assert card["personName"] == "李工"
    assert card["projectName"] == "某市政管廊项目"
    assert card["projectRole"] == "项目经理"
    assert card["completedYear"] == 2024
    assert card["performanceSummary"] == "负责总体统筹与关键节点交付"
    assert card["remark"] == "仅内部协作备注"
    assert card["isActive"] is True
    assert isinstance(card["id"], str) and card["id"].startswith("hpc_")
    card_id = card["id"]
    _assert_no_leak(card)

    listed = required_client.get(_LIST_PATH)
    assert listed.status_code == 200
    assert listed.headers.get("cache-control", "").lower() == "no-store"
    items = listed.json()["items"]
    assert len(items) == 1
    summary = items[0]
    assert set(summary.keys()) == _SUMMARY_KEYS
    assert "performanceSummary" not in summary
    assert "remark" not in summary
    assert summary["id"] == card_id
    _assert_no_leak(listed.json())

    detail = required_client.get(f"{_LIST_PATH}/{card_id}")
    assert detail.status_code == 200
    assert detail.headers.get("cache-control", "").lower() == "no-store"
    dbody = detail.json()
    assert set(dbody.keys()) == _DETAIL_KEYS
    assert dbody["performanceSummary"] == "负责总体统筹与关键节点交付"
    assert dbody["remark"] == "仅内部协作备注"
    _assert_no_leak(dbody)

    patched = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={
            "projectName": "某市政管廊项目(更新)",
            "isActive": False,
            "remark": "已停用备注",
            "performanceSummary": "更新后业绩概述",
        },
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    pbody = patched.json()
    assert set(pbody.keys()) == _DETAIL_KEYS
    assert pbody["projectName"] == "某市政管廊项目(更新)"
    assert pbody["isActive"] is False
    assert pbody["remark"] == "已停用备注"
    assert pbody["performanceSummary"] == "更新后业绩概述"
    assert pbody["id"] == card_id

    reactivated = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={"isActive": True},
        headers=headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["isActive"] is True

    deleted = required_client.delete(
        f"{_LIST_PATH}/{card_id}",
        headers=headers,
    )
    assert deleted.status_code == 405

    still = required_client.get(f"{_LIST_PATH}/{card_id}")
    assert still.status_code == 200
    assert still.json()["id"] == card_id


def test_field_boundaries_strict_year_and_empty_patch(required_client):
    """字段边界、严格年份、空补丁与额外服务端字段拒绝。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    cases = [
        _valid_create_body(personName=""),
        _valid_create_body(personName="p" * 81),
        _valid_create_body(projectName=""),
        _valid_create_body(projectName="n" * 121),
        _valid_create_body(projectRole="r" * 81),
        _valid_create_body(completedYear=1899),
        _valid_create_body(completedYear=2101),
        _valid_create_body(completedYear="2024"),
        _valid_create_body(completedYear=2024.0),
        _valid_create_body(completedYear=True),
        _valid_create_body(performanceSummary=""),
        _valid_create_body(performanceSummary="s" * 1001),
        _valid_create_body(remark="r" * 501),
    ]
    for payload in cases:
        res = required_client.post(_LIST_PATH, json=payload, headers=headers)
        assert res.status_code == 422, f"{payload} -> {res.status_code} {res.text}"
        assert res.json()["detail"] == _INVALID_DETAIL

    ok = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="人" * 80,
            projectName="项" * 120,
            projectRole="角" * 80,
            completedYear=1900,
            performanceSummary="概" * 1000,
            remark="备" * 500,
            isActive=True,
        ),
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["personName"] == "人" * 80
    assert body["projectName"] == "项" * 120
    assert body["projectRole"] == "角" * 80
    assert body["completedYear"] == 1900
    assert body["performanceSummary"] == "概" * 1000
    assert body["remark"] == "备" * 500

    ok_year = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="年份上界",
            completedYear=2100,
            performanceSummary="边界年份",
        ),
        headers=headers,
    )
    assert ok_year.status_code == 201, ok_year.text
    assert ok_year.json()["completedYear"] == 2100

    ok_null_year = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="无年份",
            projectRole="",
            completedYear=None,
            remark="",
        ),
        headers=headers,
    )
    assert ok_null_year.status_code == 201, ok_null_year.text
    assert ok_null_year.json()["completedYear"] is None
    assert ok_null_year.json()["projectRole"] == ""

    empty_patch = required_client.patch(
        f"{_LIST_PATH}/{ok.json()['id']}",
        json={},
        headers=headers,
    )
    assert empty_patch.status_code == 422
    assert empty_patch.json()["detail"] == _INVALID_DETAIL

    inject = required_client.post(
        _LIST_PATH,
        json={
            **_valid_create_body(personName="注入测试"),
            "id": "hpc_client_forged",
            "workspaceId": "ws_other",
            "createdByUserId": "user_forged",
            "createdAt": "2000-01-01T00:00:00Z",
            "updatedAt": "2000-01-01T00:00:00Z",
        },
        headers=headers,
    )
    assert inject.status_code == 422
    assert inject.json()["detail"] == _INVALID_DETAIL


def test_rejects_sensitive_extra_fields_and_non_object(required_client):
    """拒绝额外敏感键、非对象 JSON；固定脱敏且不写入。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    before = required_client.get(_LIST_PATH).json()["items"]

    sensitive_payloads = [
        {**_valid_create_body(), "idNumber": "110101199001011234"},
        {**_valid_create_body(), "phone": "13800138000"},
        {**_valid_create_body(), "attachmentUrl": "https://evil.example/file.pdf"},
        {**_valid_create_body(), "contractAmount": 1000000},
        {**_valid_create_body(), "resume": "全文简历"},
    ]
    for payload in sensitive_payloads:
        res = required_client.post(_LIST_PATH, json=payload, headers=headers)
        assert res.status_code == 422, f"{payload} -> {res.status_code} {res.text}"
        assert res.json()["detail"] == _INVALID_DETAIL
        text = res.text
        assert "110101199001011234" not in text
        assert "13800138000" not in text
        assert "evil.example" not in text
        assert "全文简历" not in text

    # 非对象 JSON
    array_body = required_client.post(
        _LIST_PATH,
        content=b'[{"personName":"x"}]',
        headers={**headers, "Content-Type": "application/json"},
    )
    assert array_body.status_code == 422
    assert array_body.json()["detail"] == _INVALID_DETAIL

    invalid_json = required_client.post(
        _LIST_PATH,
        content=b"{not-json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert invalid_json.status_code == 422
    assert invalid_json.json()["detail"] == _INVALID_DETAIL

    after = required_client.get(_LIST_PATH).json()["items"]
    assert len(after) == len(before)


def test_is_active_rejects_coerced_non_booleans(required_client):
    """isActive 仅接受 JSON 布尔；\"false\" / 0 在 POST 与 PATCH 均 422 且不写入。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    seeded = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="布尔基线", isActive=True),
        headers=headers,
    )
    assert seeded.status_code == 201, seeded.text
    card_id = seeded.json()["id"]
    before_list = required_client.get(_LIST_PATH).json()["items"]
    before_count = len(before_list)
    before_detail = required_client.get(f"{_LIST_PATH}/{card_id}").json()
    assert before_detail["isActive"] is True

    for bad in ("false", 0):
        post_res = required_client.post(
            _LIST_PATH,
            json=_valid_create_body(personName="强制转换拒绝", isActive=bad),
            headers=headers,
        )
        assert post_res.status_code == 422, post_res.text
        assert post_res.json()["detail"] == _INVALID_DETAIL

        patch_res = required_client.patch(
            f"{_LIST_PATH}/{card_id}",
            json={"isActive": bad},
            headers=headers,
        )
        assert patch_res.status_code == 422, patch_res.text
        assert patch_res.json()["detail"] == _INVALID_DETAIL

    after_list = required_client.get(_LIST_PATH).json()["items"]
    assert len(after_list) == before_count
    after_detail = required_client.get(f"{_LIST_PATH}/{card_id}").json()
    assert after_detail["isActive"] is True
    assert after_detail["updatedAt"] == before_detail["updatedAt"]


def test_csrf_required_for_mutations(required_client):
    """POST/PATCH 无 CSRF 或错误 CSRF 须拒绝。"""
    csrf = _login_hr(required_client)

    no_csrf = required_client.post(_LIST_PATH, json=_valid_create_body())
    assert no_csrf.status_code == 403

    bad = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="坏csrf"),
        headers={"X-CSRF-Token": "definitely-wrong-csrf"},
    )
    assert bad.status_code == 403

    ok = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="有csrf"),
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201, ok.text

    patch_no = required_client.patch(
        f"{_LIST_PATH}/{ok.json()['id']}",
        json={"remark": "no"},
    )
    assert patch_no.status_code == 403


@pytest.mark.parametrize("role", ["bid_writer", "finance", "bidder"])
def test_non_hr_roles_forbidden(required_client, role):
    """非 hr 角色访问固定 403 role_forbidden。"""
    csrf = _login_role(required_client, role)
    headers = {"X-CSRF-Token": csrf}

    get_res = required_client.get(_LIST_PATH)
    assert get_res.status_code == 403
    assert get_res.json()["detail"]["code"] == "role_forbidden"

    post_res = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(),
        headers=headers,
    )
    assert post_res.status_code == 403
    assert post_res.json()["detail"]["code"] == "role_forbidden"


def test_owner_disabled_unauthenticated_denied(required_client, monkeypatch):
    """所有者、disabled、未登录均不得访问业绩卡；未登录须精确 401 auth_required。"""
    csrf, _ = _owner_session(required_client)
    owner_get = required_client.get(_LIST_PATH)
    assert owner_get.status_code == 403
    assert owner_get.json()["detail"]["code"] == "role_forbidden"
    owner_post = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(),
        headers={"X-CSRF-Token": csrf},
    )
    assert owner_post.status_code == 403
    assert owner_post.json()["detail"]["code"] == "role_forbidden"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(_LIST_PATH)
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "role_forbidden"
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            res = client.get(_LIST_PATH)
            # required 未登录：全局认证中间件精确 401，不得接受 403
            assert res.status_code == 401, res.text
            assert res.json()["detail"]["code"] == "auth_required"
    finally:
        get_settings.cache_clear()


def test_owner_with_exact_hr_role_allowed(required_client):
    """
    所有者身份不能替代角色；但所有者当前成员角色精确为 hr 时按角色允许。
    使用 bootstrap_local_admin(role=ROLE_HR) 创建该所有者后登录，
    断言 GET 列表与 POST 创建均 200/201，响应仍最小投影与 no-store。
    """
    _bootstrap(role=auth_service.ROLE_HR)
    login_res = _login(required_client, _OWNER_USER, _OWNER_PASS)
    assert login_res.status_code == 200, login_res.text
    login_body = login_res.json()
    workspaces = login_body.get("workspaces") or []
    assert any(
        w.get("role") == auth_service.ROLE_HR and w.get("isOwner") is True
        for w in workspaces
    ), login_body
    csrf = login_body["csrfToken"]

    listed = required_client.get(_LIST_PATH)
    assert listed.status_code == 200, listed.text
    assert listed.headers.get("cache-control", "").lower() == "no-store"
    assert listed.json() == {"items": []}

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="所有者兼HR"),
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.headers.get("cache-control", "").lower() == "no-store"
    body = created.json()
    assert set(body.keys()) == _DETAIL_KEYS
    assert body["personName"] == "所有者兼HR"
    assert isinstance(body["id"], str) and body["id"].startswith("hpc_")
    _assert_no_leak(body)


def test_cross_workspace_and_forged_id_404_no_echo(required_client):
    """跨空间与伪造 ID 统一 404；响应不回显请求 cardId。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="本空间卡"),
        headers=headers,
    )
    assert created.status_code == 201
    local_id = created.json()["id"]

    foreign_id = "hpc_foreign_only_p10h"
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_hr_p10h")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_hr_p10h",
                    name="其他HR空间P10H",
                    owner_user_id="user_other_hr_p10h",
                )
            )
            db.commit()
        from app.models.entities import utc_now

        now = utc_now()
        db.add(
            HrPerformanceCardRow(
                id=foreign_id,
                workspace_id="ws_other_hr_p10h",
                person_name="外空间",
                project_name="外项目",
                project_role="外角色",
                completed_year=2020,
                performance_summary="外摘要",
                remark="外备注",
                is_active=True,
                created_by_user_id="user_other_hr_p10h",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()

    for card_id in (foreign_id, "hpc_not_exist", "hpc_probe_secret_xyz"):
        get_res = required_client.get(f"{_LIST_PATH}/{card_id}")
        assert get_res.status_code == 404, get_res.text
        body = get_res.json()
        assert body["detail"]["code"] == _NOT_FOUND_CODE
        assert card_id not in get_res.text
        patch_res = required_client.patch(
            f"{_LIST_PATH}/{card_id}",
            json={"remark": "hack"},
            headers=headers,
        )
        assert patch_res.status_code == 404
        assert patch_res.json()["detail"]["code"] == _NOT_FOUND_CODE
        assert card_id not in patch_res.text

    still = required_client.get(f"{_LIST_PATH}/{local_id}")
    assert still.status_code == 200
    assert still.json()["personName"] == "本空间卡"

    forbidden_ws = required_client.get(
        _LIST_PATH,
        headers={"X-Workspace-Id": "ws_other_hr_p10h"},
    )
    # 已登录 HR 指定非成员空间：精确 403 workspace_forbidden，不得接受 role_forbidden
    assert forbidden_ws.status_code == 403, forbidden_ws.text
    assert forbidden_ws.json()["detail"]["code"] == "workspace_forbidden"


def test_audit_create_update_without_sensitive_body(required_client):
    """成功创建/更新写审计；target 仅卡片 ID，不含业务字段。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    secret_name = "审计敏感姓名XYZ"
    secret_project = "审计敏感项目ABC"
    secret_role = "审计敏感角色ROLE"
    secret_summary = "审计敏感业绩摘要SUMMARY"
    secret_remark = "审计敏感备注SECRET"

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName=secret_name,
            projectName=secret_project,
            projectRole=secret_role,
            performanceSummary=secret_summary,
            remark=secret_remark,
            completedYear=2023,
        ),
        headers=headers,
    )
    assert created.status_code == 201
    card_id = created.json()["id"]

    patched = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={"remark": "更新后备注"},
        headers=headers,
    )
    assert patched.status_code == 200

    db = SessionLocal()
    try:
        events = list(
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action.in_(
                    ["hr_performance_create", "hr_performance_update"]
                )
            )
            .all()
        )
        actions = {e.action for e in events}
        assert "hr_performance_create" in actions
        assert "hr_performance_update" in actions
        for e in events:
            assert e.target == card_id
            assert e.target.startswith("hpc_")
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
            assert secret_project not in blob
            assert secret_role not in blob
            assert secret_summary not in blob
            assert secret_remark not in blob
            assert "更新后备注" not in blob
            assert "2023" not in blob
    finally:
        db.close()


def test_no_delete_route_and_detail_projection_only(required_client):
    """确认无 DELETE 路由；可选空 projectRole；列表永不含详情字段。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="投影人",
            projectRole="",
            completedYear=None,
            performanceSummary="仅详情可见摘要",
            remark="仅详情可见备注",
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    card_id = created.json()["id"]

    listed = required_client.get(_LIST_PATH)
    assert listed.status_code == 200
    summary = next(x for x in listed.json()["items"] if x["id"] == card_id)
    assert "performanceSummary" not in summary
    assert "remark" not in summary
    assert summary["projectRole"] == ""
    assert summary["completedYear"] is None

    # 集合层确认无 DELETE 方法暴露
    options = required_client.request("OPTIONS", f"{_LIST_PATH}/{card_id}")
    allow = (options.headers.get("allow") or "").upper()
    if allow:
        assert "DELETE" not in allow


def test_explicit_null_rejected_except_completed_year(required_client):
    """
    显式 null 收口：
    - POST projectRole=null 固定 422；
    - PATCH 六个非空字段显式 null 固定 422 且不写入；
    - completedYear=null 仍可成功清空。
    """
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    # POST：projectRole 显式 null → 422；省略则默认空串
    post_null_role = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="角色null拒绝", projectRole=None),
        headers=headers,
    )
    assert post_null_role.status_code == 422, post_null_role.text
    assert post_null_role.json()["detail"] == _INVALID_DETAIL
    assert "角色null拒绝" not in post_null_role.text

    omit_role = required_client.post(
        _LIST_PATH,
        json={
            "personName": "角色省略",
            "projectName": "某项目",
            "performanceSummary": "省略 projectRole",
        },
        headers=headers,
    )
    assert omit_role.status_code == 201, omit_role.text
    assert omit_role.json()["projectRole"] == ""

    seeded = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="null基线",
            projectName="基线项目",
            projectRole="基线角色",
            completedYear=2024,
            performanceSummary="基线摘要",
            remark="基线备注",
            isActive=True,
        ),
        headers=headers,
    )
    assert seeded.status_code == 201, seeded.text
    card_id = seeded.json()["id"]
    before = required_client.get(f"{_LIST_PATH}/{card_id}").json()

    null_fields = [
        ("personName", None),
        ("projectName", None),
        ("projectRole", None),
        ("performanceSummary", None),
        ("remark", None),
        ("isActive", None),
    ]
    for field, value in null_fields:
        patch_res = required_client.patch(
            f"{_LIST_PATH}/{card_id}",
            json={field: value},
            headers=headers,
        )
        assert patch_res.status_code == 422, f"{field}=null -> {patch_res.text}"
        assert patch_res.json()["detail"] == _INVALID_DETAIL
        assert "null基线" not in patch_res.text

    after_reject = required_client.get(f"{_LIST_PATH}/{card_id}").json()
    assert after_reject == before
    assert after_reject["personName"] == "null基线"
    assert after_reject["projectName"] == "基线项目"
    assert after_reject["projectRole"] == "基线角色"
    assert after_reject["completedYear"] == 2024
    assert after_reject["performanceSummary"] == "基线摘要"
    assert after_reject["remark"] == "基线备注"
    assert after_reject["isActive"] is True
    assert after_reject["updatedAt"] == before["updatedAt"]

    # completedYear 显式 null 仍用于清空
    clear_year = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={"completedYear": None},
        headers=headers,
    )
    assert clear_year.status_code == 200, clear_year.text
    assert clear_year.json()["completedYear"] is None
    assert clear_year.json()["personName"] == "null基线"
