"""
模块：P10I 人员资质到期提示定向测试
用途：验收严格 hr 只读到期摘要的角色矩阵、UTC 日期边界、90 天窗口、
  停用排除、排序、字段白名单、空态、no-store 与固定审计脱敏。
对接：app.api.hr；app.services.hr_credential_expiry_service；deps.require_hr。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event

from app.api.schemas import HrCredentialExpiryAttentionItemOut
from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import AuthAuditEventRow, Workspace
from app.services import auth_service, hr_credential_expiry_service

# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_hr_p10i"
_OWNER_PASS = "TestPass-Hr-Expiry-Owner-0001!"
_ROLE_PASSWORDS = {
    "hr": "TestPass-Hr-Expiry-Role-0001!",
    "finance": "TestPass-Finance-Expiry-Role-0001!",
    "bidder": "TestPass-Bidder-Expiry-Role-0001!",
    "bid_writer": "TestPass-Writer-Expiry-Role-0001!",
}
_PATH = "/api/hr/credential-expiry"
_CARDS_PATH = "/api/hr/credential-cards"
_TOP_KEYS = frozenset(
    {
        "asOfDate",
        "windowDays",
        "activeTotalCount",
        "expiredCount",
        "expiringSoonCount",
        "validCount",
        "missingExpiryCount",
        "inactiveExcludedCount",
        "attentionItems",
    }
)
_ITEM_KEYS = frozenset(
    {
        "cardId",
        "personName",
        "category",
        "credentialName",
        "level",
        "validUntil",
        "state",
        "daysRemaining",
    }
)
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
    "remark",
    "createdAt",
    "updatedAt",
    "isActive",
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


def _login_hr(client: TestClient) -> str:
    """用途：创建 hr 成员并登录，返回 CSRF。"""
    csrf, _ = _owner_session(client)
    username = "user_hr_p10i"
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
    username = f"user_{role}_p10i"
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


def _card_body(**overrides) -> dict:
    body = {
        "personName": "张工",
        "category": "professional",
        "credentialName": "一级建造师",
        "level": "市政专业",
        "validUntil": "2028-12-31",
        "remark": "仅内部协作备注不得出现在到期响应",
        "isActive": True,
    }
    body.update(overrides)
    return body


def _create_card(client: TestClient, csrf: str, **overrides) -> dict:
    res = client.post(
        _CARDS_PATH,
        json=_card_body(**overrides),
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_empty_state_no_store_and_field_whitelist(required_client):
    """空态仍 200；完整计数与空关注列表；no-store；字段白名单。"""
    _login_hr(required_client)
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    assert body["windowDays"] == 90
    assert body["activeTotalCount"] == 0
    assert body["expiredCount"] == 0
    assert body["expiringSoonCount"] == 0
    assert body["validCount"] == 0
    assert body["missingExpiryCount"] == 0
    assert body["inactiveExcludedCount"] == 0
    assert body["attentionItems"] == []
    # asOfDate 为服务端 UTC 自然日
    as_of = date.fromisoformat(body["asOfDate"])
    assert as_of.isoformat() == body["asOfDate"]
    _assert_no_leak(body)


def test_service_date_boundaries_and_sorting():
    """
    服务层显式 as_of：昨天 expired 负天数、当天/第90天 expiring_soon、
    第91天 valid 只计数、null 进关注列表；排序 expired→soon→missing，
    同组有效期升序再 cardId。
    """
    fixed_as_of = date(2026, 7, 14)
    db = SessionLocal()
    try:
        from app.models.entities import HrCredentialCardRow, utc_now

        now = utc_now()
        workspace_id = "ws_local"
        rows = [
            # expired 较早
            HrCredentialCardRow(
                id="hcc_exp_b",
                workspace_id=workspace_id,
                person_name="过期乙",
                category="professional",
                credential_name="证乙",
                level="",
                valid_until=fixed_as_of - timedelta(days=10),
                remark="不得读出",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # expired 较近（应排在较晚的 expired 之后因升序——更早在前）
            HrCredentialCardRow(
                id="hcc_exp_a",
                workspace_id=workspace_id,
                person_name="过期甲",
                category="safety",
                credential_name="证甲",
                level="A",
                valid_until=fixed_as_of - timedelta(days=1),
                remark="secret",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # 当天到期
            HrCredentialCardRow(
                id="hcc_soon_today",
                workspace_id=workspace_id,
                person_name="当天",
                category="professional",
                credential_name="证当天",
                level="",
                valid_until=fixed_as_of,
                remark="",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # 第 90 天（含端点）
            HrCredentialCardRow(
                id="hcc_soon_90",
                workspace_id=workspace_id,
                person_name="九十",
                category="other",
                credential_name="证90",
                level="",
                valid_until=fixed_as_of + timedelta(days=90),
                remark="",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # 第 91 天 valid 只计数
            HrCredentialCardRow(
                id="hcc_valid_91",
                workspace_id=workspace_id,
                person_name="有效",
                category="performance",
                credential_name="证91",
                level="",
                valid_until=fixed_as_of + timedelta(days=91),
                remark="",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # 无有效期
            HrCredentialCardRow(
                id="hcc_missing_z",
                workspace_id=workspace_id,
                person_name="缺期乙",
                category="other",
                credential_name="缺乙",
                level="",
                valid_until=None,
                remark="",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            HrCredentialCardRow(
                id="hcc_missing_a",
                workspace_id=workspace_id,
                person_name="缺期甲",
                category="other",
                credential_name="缺甲",
                level="",
                valid_until=None,
                remark="",
                is_active=True,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
            # 停用：只计 inactiveExcludedCount
            HrCredentialCardRow(
                id="hcc_inactive",
                workspace_id=workspace_id,
                person_name="停用",
                category="professional",
                credential_name="停用证",
                level="",
                valid_until=fixed_as_of - timedelta(days=100),
                remark="",
                is_active=False,
                created_by_user_id="u1",
                created_at=now,
                updated_at=now,
            ),
        ]
        for row in rows:
            db.add(row)
        db.commit()

        result = hr_credential_expiry_service.get_credential_expiry(
            db,
            workspace_id=workspace_id,
            actor_user_id="actor_p10i",
            as_of=fixed_as_of,
        )
    finally:
        db.close()

    assert result["as_of_date"] == fixed_as_of
    assert result["window_days"] == 90
    assert result["active_total_count"] == 7
    assert result["expired_count"] == 2
    assert result["expiring_soon_count"] == 2
    assert result["valid_count"] == 1
    assert result["missing_expiry_count"] == 2
    assert result["inactive_excluded_count"] == 1

    items = result["attention_items"]
    # valid 不进关注列表；停用也不进
    assert len(items) == 6
    states = [x["state"] for x in items]
    assert states == [
        "expired",
        "expired",
        "expiring_soon",
        "expiring_soon",
        "missing_expiry",
        "missing_expiry",
    ]
    # expired：有效期升序 → -10 天在前
    assert items[0]["card_id"] == "hcc_exp_b"
    assert items[0]["days_remaining"] == -10
    assert items[1]["card_id"] == "hcc_exp_a"
    assert items[1]["days_remaining"] == -1
    # expiring_soon：当天 daysRemaining=0，第90天=90
    assert items[2]["card_id"] == "hcc_soon_today"
    assert items[2]["days_remaining"] == 0
    assert items[2]["valid_until"] == fixed_as_of
    assert items[3]["card_id"] == "hcc_soon_90"
    assert items[3]["days_remaining"] == 90
    # missing：cardId 升序
    assert items[4]["card_id"] == "hcc_missing_a"
    assert items[4]["days_remaining"] is None
    assert items[4]["valid_until"] is None
    assert items[5]["card_id"] == "hcc_missing_z"
    # 关注项字段白名单（服务层 snake_case）
    for item in items:
        assert set(item.keys()) == {
            "card_id",
            "person_name",
            "category",
            "credential_name",
            "level",
            "valid_until",
            "state",
            "days_remaining",
        }
        assert "remark" not in item


def test_http_classifies_and_excludes_inactive(required_client):
    """HTTP：创建启用/停用卡后摘要分类正确；响应不含 remark 与越界字段。"""
    csrf = _login_hr(required_client)
    # 与生产路由一致：按 UTC 自然日构造相对日期，避免本地日与 UTC 跨日漂移
    as_of = datetime.now(timezone.utc).date()
    _create_card(
        required_client,
        csrf,
        personName="过期人",
        credentialName="过期证",
        validUntil=(as_of - timedelta(days=3)).isoformat(),
        remark="备注应被排除",
    )
    _create_card(
        required_client,
        csrf,
        personName="临期人",
        credentialName="临期证",
        validUntil=(as_of + timedelta(days=30)).isoformat(),
    )
    _create_card(
        required_client,
        csrf,
        personName="有效人",
        credentialName="有效证",
        validUntil=(as_of + timedelta(days=120)).isoformat(),
    )
    _create_card(
        required_client,
        csrf,
        personName="缺期人",
        credentialName="缺期证",
        validUntil=None,
    )
    inactive = _create_card(
        required_client,
        csrf,
        personName="停用人",
        credentialName="停用证",
        validUntil=(as_of - timedelta(days=1)).isoformat(),
        isActive=False,
    )
    assert inactive["isActive"] is False

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    assert body["windowDays"] == 90
    assert body["activeTotalCount"] == 4
    assert body["expiredCount"] == 1
    assert body["expiringSoonCount"] == 1
    assert body["validCount"] == 1
    assert body["missingExpiryCount"] == 1
    assert body["inactiveExcludedCount"] == 1
    assert len(body["attentionItems"]) == 3
    states = [x["state"] for x in body["attentionItems"]]
    assert states == ["expired", "expiring_soon", "missing_expiry"]
    for item in body["attentionItems"]:
        assert set(item.keys()) == _ITEM_KEYS
        assert item["personName"] != "停用人"
        assert item["personName"] != "有效人"
    # 不得出现 remark 或停用卡 id
    blob = json.dumps(body, ensure_ascii=False)
    assert "备注应被排除" not in blob
    assert inactive["id"] not in blob
    _assert_no_leak(body)


def test_route_ignores_client_asof_query(required_client):
    """生产路由不接收 asOf/window；查询参数不得改变服务端日期。"""
    _login_hr(required_client)
    base = required_client.get(_PATH)
    assert base.status_code == 200
    as_of = base.json()["asOfDate"]

    tainted = required_client.get(
        _PATH,
        params={"asOf": "1999-01-01", "asOfDate": "1999-01-01", "windowDays": "1"},
    )
    assert tainted.status_code == 200, tainted.text
    body = tainted.json()
    assert body["asOfDate"] == as_of
    assert body["windowDays"] == 90


@pytest.mark.parametrize("role", ["bid_writer", "finance", "bidder"])
def test_non_hr_roles_forbidden(required_client, role):
    """非 hr 角色访问固定 403 role_forbidden。"""
    _login_role(required_client, role)
    res = required_client.get(_PATH)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "role_forbidden"


def test_owner_disabled_unauthenticated_denied(required_client, monkeypatch):
    """所有者、disabled、未登录均不得访问；未登录须精确 401 auth_required。"""
    _owner_session(required_client)
    owner_get = required_client.get(_PATH)
    assert owner_get.status_code == 403
    assert owner_get.json()["detail"]["code"] == "role_forbidden"

    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.get(_PATH)
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
            res = client.get(_PATH)
            assert res.status_code == 401, res.text
            assert res.json()["detail"]["code"] == "auth_required"
    finally:
        get_settings.cache_clear()


def test_owner_with_exact_hr_role_allowed(required_client):
    """isOwner=true 且成员角色精确 hr 可 GET；所有者身份不替代角色。"""
    _bootstrap(role=auth_service.ROLE_HR)
    login_res = _login(required_client, _OWNER_USER, _OWNER_PASS)
    assert login_res.status_code == 200, login_res.text
    workspaces = login_res.json().get("workspaces") or []
    assert any(
        w.get("role") == auth_service.ROLE_HR and w.get("isOwner") is True
        for w in workspaces
    ), login_res.json()

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    assert body["attentionItems"] == []


def test_non_member_workspace_forbidden(required_client):
    """已登录 HR 指定非成员空间：精确 403 workspace_forbidden。"""
    _login_hr(required_client)
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_hr_p10i")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_hr_p10i",
                    name="其他HR空间P10I",
                    owner_user_id="user_other_hr_p10i",
                )
            )
            db.commit()
    finally:
        db.close()

    res = required_client.get(
        _PATH,
        headers={"X-Workspace-Id": "ws_other_hr_p10i"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "workspace_forbidden"


def test_audit_read_desensitized(required_client):
    """成功读取写固定审计；target=credential_expiry；不含卡/人/资质/日期/状态/计数。"""
    csrf = _login_hr(required_client)
    secret_name = "审计敏感姓名P10I"
    secret_cred = "审计敏感证书P10I"
    secret_remark = "审计敏感备注P10I"
    created = _create_card(
        required_client,
        csrf,
        personName=secret_name,
        credentialName=secret_cred,
        remark=secret_remark,
        validUntil="2020-01-01",
    )
    card_id = created["id"]

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["expiredCount"] >= 1

    db = SessionLocal()
    try:
        events = list(
            db.query(AuthAuditEventRow)
            .filter(AuthAuditEventRow.action == "hr_credential_expiry_read")
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.result == "success"
            assert e.target == "credential_expiry"
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
            assert card_id not in blob
            assert "expired" not in blob
            assert "2020-01-01" not in blob
            # 计数不得写入审计字段
            assert str(body["expiredCount"]) not in (e.target or "")
            assert str(body["activeTotalCount"]) not in (e.target or "")
    finally:
        db.close()


def _select_clause_of(sql: str) -> str:
    """
    用途：从 SQL 文本截取 SELECT 列表段（FROM 之前），用于列投影断言。
    对接：test_select_excludes_sensitive_columns。
    二次开发：仅解析 SELECT 投影，WHERE 中的 workspace_id 不在此段。
    """
    text = " ".join(sql.split())
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", text)
    assert match is not None, f"无法解析 SELECT 列表: {sql}"
    return match.group(1)


def test_select_excludes_sensitive_columns():
    """
    捕获实际 hr_credential_cards SELECT 语句：投影不得含
    remark/created_by_user_id/created_at/updated_at/workspace_id；
    workspace_id 仅允许出现在 WHERE。
    """
    fixed_as_of = date(2026, 7, 14)
    db = SessionLocal()
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        # 只关心读取 hr_credential_cards 的 SELECT
        if "hr_credential_cards" not in statement.lower():
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        from app.models.entities import HrCredentialCardRow, utc_now

        now = utc_now()
        db.add(
            HrCredentialCardRow(
                id="hcc_sql_probe",
                workspace_id="ws_local",
                person_name="探针",
                category="professional",
                credential_name="探针证",
                level="",
                valid_until=fixed_as_of + timedelta(days=10),
                remark="不得出现在 SELECT 投影",
                is_active=True,
                created_by_user_id="u_sql_probe",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

        result = hr_credential_expiry_service.get_credential_expiry(
            db,
            workspace_id="ws_local",
            actor_user_id="actor_sql_probe",
            as_of=fixed_as_of,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db.close()

    assert captured, "未捕获到 hr_credential_cards 的 SELECT"
    # 取最后一次读取服务发出的 SELECT
    select_sql = captured[-1]
    select_list = _select_clause_of(select_sql).lower()
    where_part = select_sql.lower().split("where", 1)
    where_clause = where_part[1] if len(where_part) > 1 else ""

    forbidden_in_select = (
        "remark",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "workspace_id",
    )
    for col in forbidden_in_select:
        assert col not in select_list, f"SELECT 投影泄漏列 {col}: {select_sql}"

    # 必要列必须投影
    for col in (
        "id",
        "person_name",
        "category",
        "credential_name",
        "level",
        "valid_until",
        "is_active",
    ):
        assert col in select_list, f"SELECT 缺少必要列 {col}: {select_sql}"

    # workspace_id 仅 WHERE 过滤，不得投影
    assert "workspace_id" in where_clause, f"WHERE 应使用 workspace_id: {select_sql}"
    assert result["expiring_soon_count"] == 1
    assert len(result["attention_items"]) == 1
    assert result["attention_items"][0]["card_id"] == "hcc_sql_probe"


def test_attention_item_schema_rejects_valid_state():
    """
    关注项 Schema 仅允许 expired/expiring_soon/missing_expiry；
    state=valid 必须 Pydantic 校验失败（行为断言，非源码字符串）。
    """
    # 合法状态可通过
    for state, days_remaining, valid_until in (
        ("expired", -1, date(2026, 1, 1)),
        ("expiring_soon", 0, date(2026, 7, 14)),
        ("missing_expiry", None, None),
    ):
        item = HrCredentialExpiryAttentionItemOut(
            card_id="hcc_schema",
            person_name="校验人",
            category="professional",
            credential_name="校验证",
            level="",
            valid_until=valid_until,
            state=state,
            days_remaining=days_remaining,
        )
        assert item.state == state

    with pytest.raises(ValidationError):
        HrCredentialExpiryAttentionItemOut(
            card_id="hcc_schema",
            person_name="校验人",
            category="professional",
            credential_name="校验证",
            level="",
            valid_until=date(2027, 1, 1),
            state="valid",
            days_remaining=200,
        )


def test_classify_valid_until_fixed_window_signature():
    """classify_valid_until 不得暴露 window_days 可变参数；内部固定 90 天。"""
    sig = inspect.signature(hr_credential_expiry_service.classify_valid_until)
    assert "window_days" not in sig.parameters
    as_of = date(2026, 7, 14)
    # 第 90 天含端点 → expiring_soon；第 91 天 → valid
    state_90, days_90 = hr_credential_expiry_service.classify_valid_until(
        as_of + timedelta(days=90),
        as_of=as_of,
    )
    assert state_90 == "expiring_soon"
    assert days_90 == 90
    state_91, days_91 = hr_credential_expiry_service.classify_valid_until(
        as_of + timedelta(days=91),
        as_of=as_of,
    )
    assert state_91 == "valid"
    assert days_91 == 91
