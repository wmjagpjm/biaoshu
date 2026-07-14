"""
模块：P10D 严格 HR 人员资质素材卡定向测试
用途：验收 HR 素材卡 CRUD（无删除）、字段白名单、鉴权隔离、CSRF、审计脱敏与跨空间 404。
对接：app.api.hr；app.services.hr_credential_service；deps.require_hr。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import AuthAuditEventRow, HrCredentialCardRow, Workspace
from app.services import auth_service


# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_hr_p10d"
_OWNER_PASS = "TestPass-Hr-Credential-Owner-0001!"
_ROLE_PASSWORDS = {
    "hr": "TestPass-Hr-Credential-Role-0001!",
    "finance": "TestPass-Finance-Credential-Role-0001!",
    "bidder": "TestPass-Bidder-Credential-Role-0001!",
    "bid_writer": "TestPass-Writer-Credential-Role-0001!",
}
_SUMMARY_KEYS = frozenset(
    {
        "id",
        "personName",
        "category",
        "credentialName",
        "level",
        "validUntil",
        "isActive",
        "createdAt",
        "updatedAt",
    }
)
_DETAIL_KEYS = _SUMMARY_KEYS | {"remark"}
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
)
_LIST_PATH = "/api/hr/credential-cards"


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
    username = "user_hr_p10d"
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
    username = f"user_{role}_p10d"
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
        "personName": "张工",
        "category": "professional",
        "credentialName": "一级建造师",
        "level": "市政专业",
        "validUntil": "2028-12-31",
        "remark": "仅内部协作备注",
        "isActive": True,
    }
    body.update(overrides)
    return body


def test_hr_crud_list_summary_without_remark_detail_with_remark(required_client):
    """HR 可创建/列表/详情/更新/启停；列表不含 remark，详情含 remark；无 DELETE。"""
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
    assert card["personName"] == "张工"
    assert card["category"] == "professional"
    assert card["credentialName"] == "一级建造师"
    assert card["level"] == "市政专业"
    assert card["validUntil"] == "2028-12-31"
    assert card["remark"] == "仅内部协作备注"
    assert card["isActive"] is True
    assert isinstance(card["id"], str) and card["id"].startswith("hcc_")
    card_id = card["id"]
    _assert_no_leak(card)

    listed = required_client.get(_LIST_PATH)
    assert listed.status_code == 200
    assert listed.headers.get("cache-control", "").lower() == "no-store"
    items = listed.json()["items"]
    assert len(items) == 1
    summary = items[0]
    assert set(summary.keys()) == _SUMMARY_KEYS
    assert "remark" not in summary
    assert summary["id"] == card_id
    _assert_no_leak(listed.json())

    detail = required_client.get(f"{_LIST_PATH}/{card_id}")
    assert detail.status_code == 200
    assert detail.headers.get("cache-control", "").lower() == "no-store"
    dbody = detail.json()
    assert set(dbody.keys()) == _DETAIL_KEYS
    assert dbody["remark"] == "仅内部协作备注"
    _assert_no_leak(dbody)

    patched = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={
            "credentialName": "一级建造师(更新)",
            "isActive": False,
            "remark": "已停用备注",
        },
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    pbody = patched.json()
    assert pbody["credentialName"] == "一级建造师(更新)"
    assert pbody["isActive"] is False
    assert pbody["remark"] == "已停用备注"
    assert pbody["id"] == card_id

    reactivated = required_client.patch(
        f"{_LIST_PATH}/{card_id}",
        json={"isActive": True},
        headers=headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["isActive"] is True

    # 不提供物理删除
    deleted = required_client.delete(
        f"{_LIST_PATH}/{card_id}",
        headers=headers,
    )
    assert deleted.status_code == 405

    still = required_client.get(f"{_LIST_PATH}/{card_id}")
    assert still.status_code == 200
    assert still.json()["id"] == card_id


def test_field_boundaries_and_client_id_ignored_or_rejected(required_client):
    """字段边界非法不得写入；客户端不得指定服务端字段。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    cases = [
        _valid_create_body(personName=""),
        _valid_create_body(personName="p" * 81),
        _valid_create_body(category="illegal"),
        _valid_create_body(credentialName=""),
        _valid_create_body(credentialName="c" * 121),
        _valid_create_body(level="l" * 81),
        _valid_create_body(remark="r" * 501),
        _valid_create_body(validUntil="not-a-date"),
    ]
    for payload in cases:
        res = required_client.post(_LIST_PATH, json=payload, headers=headers)
        assert res.status_code in (400, 422), f"{payload} -> {res.status_code} {res.text}"

    # 合法边界
    ok = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName="人" * 80,
            category="other",
            credentialName="证" * 120,
            level="级" * 80,
            validUntil=None,
            remark="备" * 500,
            isActive=True,
        ),
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["personName"] == "人" * 80
    assert ok.json()["credentialName"] == "证" * 120
    assert ok.json()["level"] == "级" * 80
    assert ok.json()["validUntil"] is None
    assert ok.json()["remark"] == "备" * 500

    # 空 PATCH 拒绝
    empty_patch = required_client.patch(
        f"{_LIST_PATH}/{ok.json()['id']}",
        json={},
        headers=headers,
    )
    assert empty_patch.status_code in (400, 422)

    # 客户端注入 id/workspace/user/time 不得写入（forbid 或忽略后服务端自生成）
    inject = required_client.post(
        _LIST_PATH,
        json={
            **_valid_create_body(personName="注入测试"),
            "id": "hcc_client_forged",
            "workspaceId": "ws_other",
            "createdByUserId": "user_forged",
            "createdAt": "2000-01-01T00:00:00Z",
            "updatedAt": "2000-01-01T00:00:00Z",
        },
        headers=headers,
    )
    # extra=forbid → 422；若 ignore 则 id 不可被客户端指定
    if inject.status_code == 201:
        body = inject.json()
        assert body["id"] != "hcc_client_forged"
        assert body["id"].startswith("hcc_")
        assert "createdByUserId" not in body
    else:
        assert inject.status_code in (400, 422)


def test_rejects_sensitive_extra_fields(required_client):
    """拒绝证件号/电话/附件等敏感额外字段，且不写入。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    before = required_client.get(_LIST_PATH).json()["items"]

    sensitive_payloads = [
        {**_valid_create_body(), "idNumber": "110101199001011234"},
        {**_valid_create_body(), "phone": "13800138000"},
        {**_valid_create_body(), "mobile": "13900139000"},
        {**_valid_create_body(), "address": "某市某路1号"},
        {**_valid_create_body(), "attachmentUrl": "https://evil.example/file.pdf"},
        {**_valid_create_body(), "photo": "base64data"},
        {**_valid_create_body(), "idCard": "X"},
    ]
    for payload in sensitive_payloads:
        res = required_client.post(_LIST_PATH, json=payload, headers=headers)
        assert res.status_code in (400, 422), f"{payload} -> {res.status_code} {res.text}"
        text = res.text.lower()
        assert "110101199001011234" not in text
        assert "13800138000" not in text

    after = required_client.get(_LIST_PATH).json()["items"]
    assert len(after) == len(before)


def test_is_active_rejects_coerced_non_booleans(required_client):
    """
    用途：isActive 仅接受 JSON 布尔；\"false\" / 0 在 POST 与 PATCH 均 422 且不写入。
    对接：HrCredentialCardCreate / Update 的 StrictBool 契约。
    二次开发：响应须固定脱敏 invalid_hr_credential，禁止回显原始 isActive 输入。
    """
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    # 基线：合法 true 布尔写入一条，供 PATCH 与不写入断言
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

    # 普通 bool 会把 "false"/0 强制为 False；StrictBool 必须拒绝
    coerced_values = ["false", 0]
    for bad in coerced_values:
        post_res = required_client.post(
            _LIST_PATH,
            json=_valid_create_body(personName="强制转换拒绝", isActive=bad),
            headers=headers,
        )
        assert post_res.status_code == 422, (
            f"POST isActive={bad!r} -> {post_res.status_code} {post_res.text}"
        )
        post_detail = post_res.json()["detail"]
        assert post_detail == {
            "code": "invalid_hr_credential",
            "message": "人员资质卡参数不合法",
        }
        # 不得把原始输入值写回 detail（固定脱敏）
        assert "false" not in json.dumps(post_detail, ensure_ascii=False).lower()
        assert '"0"' not in json.dumps(post_detail, ensure_ascii=False)

        patch_res = required_client.patch(
            f"{_LIST_PATH}/{card_id}",
            json={"isActive": bad},
            headers=headers,
        )
        assert patch_res.status_code == 422, (
            f"PATCH isActive={bad!r} -> {patch_res.status_code} {patch_res.text}"
        )
        patch_detail = patch_res.json()["detail"]
        assert patch_detail == {
            "code": "invalid_hr_credential",
            "message": "人员资质卡参数不合法",
        }
        assert "false" not in json.dumps(patch_detail, ensure_ascii=False).lower()
        assert '"0"' not in json.dumps(patch_detail, ensure_ascii=False)

    after_list = required_client.get(_LIST_PATH).json()["items"]
    assert len(after_list) == before_count
    after_detail = required_client.get(f"{_LIST_PATH}/{card_id}").json()
    assert after_detail["isActive"] is True
    assert after_detail["personName"] == "布尔基线"
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
    """owner 以外非 hr 角色访问固定 403 role_forbidden。"""
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
    """所有者、disabled、未登录均不得访问 HR 素材卡。"""
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
            assert res.status_code in (401, 403)
            assert res.status_code != 200
    finally:
        get_settings.cache_clear()


def test_cross_workspace_and_forged_id_404(required_client):
    """跨空间与伪造卡片统一 404 hr_credential_not_found，不允许探测。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(personName="本空间卡"),
        headers=headers,
    )
    assert created.status_code == 201
    local_id = created.json()["id"]

    # 在另一工作空间直接写一条 ORM 记录
    foreign_id = "hcc_foreign_only"
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_hr_p10d")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_hr_p10d",
                    name="其他HR空间P10D",
                    owner_user_id="user_other_hr_p10d",
                )
            )
            db.commit()
        from app.models.entities import utc_now

        now = utc_now()
        db.add(
            HrCredentialCardRow(
                id=foreign_id,
                workspace_id="ws_other_hr_p10d",
                person_name="外空间",
                category="other",
                credential_name="外证",
                level="",
                valid_until=None,
                remark="外备注",
                is_active=True,
                created_by_user_id="user_other_hr_p10d",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()

    for card_id in (foreign_id, "hcc_not_exist", "hcc_probe"):
        get_res = required_client.get(f"{_LIST_PATH}/{card_id}")
        assert get_res.status_code == 404, get_res.text
        assert get_res.json()["detail"]["code"] == "hr_credential_not_found"
        patch_res = required_client.patch(
            f"{_LIST_PATH}/{card_id}",
            json={"remark": "hack"},
            headers=headers,
        )
        assert patch_res.status_code == 404
        assert patch_res.json()["detail"]["code"] == "hr_credential_not_found"

    # 本空间卡仍在
    still = required_client.get(f"{_LIST_PATH}/{local_id}")
    assert still.status_code == 200
    assert still.json()["personName"] == "本空间卡"

    # 非成员 X-Workspace-Id 不得借头读取
    forbidden_ws = required_client.get(
        _LIST_PATH,
        headers={"X-Workspace-Id": "ws_other_hr_p10d"},
    )
    assert forbidden_ws.status_code == 403
    assert forbidden_ws.json()["detail"]["code"] in (
        "workspace_forbidden",
        "role_forbidden",
    )


def test_audit_create_update_without_sensitive_body(required_client):
    """成功创建/更新写审计；target 仅卡片 ID，不含姓名/证书/备注。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    secret_name = "审计敏感姓名XYZ"
    secret_cred = "审计敏感证书ABC"
    secret_remark = "审计敏感备注SECRET"

    created = required_client.post(
        _LIST_PATH,
        json=_valid_create_body(
            personName=secret_name,
            credentialName=secret_cred,
            remark=secret_remark,
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
                    ["hr_credential_create", "hr_credential_update"]
                )
            )
            .all()
        )
        actions = {e.action for e in events}
        assert "hr_credential_create" in actions
        assert "hr_credential_update" in actions
        for e in events:
            assert e.target == card_id
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
            assert secret_cred not in blob
            assert secret_remark not in blob
            assert "更新后备注" not in blob
    finally:
        db.close()


def test_categories_and_optional_fields(required_client):
    """四类 category 均可创建；level/validUntil 可空。"""
    csrf = _login_hr(required_client)
    headers = {"X-CSRF-Token": csrf}
    for cat in ("professional", "safety", "performance", "other"):
        res = required_client.post(
            _LIST_PATH,
            json=_valid_create_body(
                personName=f"人-{cat}",
                category=cat,
                level=None,
                validUntil=None,
                remark="",
            ),
            headers=headers,
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["category"] == cat
        assert body["level"] in (None, "")
        assert body["validUntil"] is None
        assert body["remark"] == ""
