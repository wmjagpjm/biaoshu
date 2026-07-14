"""
模块：P10J 财务个人成本变更记录定向测试
用途：验收严格 finance 本人最近 50 条成功成本变更固定投影、隔离、SQL 三列、
  字段白名单、空态、no-store、读取审计脱敏与权限矩阵。
对接：app.api.finance；app.services.finance_cost_change_event_service；deps.require_finance。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import AuthAuditEventRow, Workspace, utc_now
from app.services import auth_service, finance_cost_change_event_service

# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_finance_p10j"
_OWNER_PASS = "TestPass-Finance-P10J-Owner-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-Finance-P10J-Role-0001!",
    "finance_b": "TestPass-Finance-P10J-RoleB-0002!",
    "hr": "TestPass-Hr-P10J-Role-0001!",
    "bidder": "TestPass-Bidder-P10J-Role-0001!",
    "bid_writer": "TestPass-Writer-P10J-Role-0001!",
}
_PATH = "/api/finance/cost-change-events"
_TOP_KEYS = frozenset({"items"})
_ITEM_KEYS = frozenset({"action", "entryId", "occurredAt"})
_ALLOWED_ACTIONS = frozenset({"create", "update", "delete"})
_FORBIDDEN_MARKERS = (
    "password",
    "csrf",
    "token_digest",
    "apiKey",
    "api_key",
    "actorUserId",
    "actor_user_id",
    "workspaceId",
    "workspace_id",
    "result",
    "finance_cost_create",
    "finance_cost_update",
    "finance_cost_delete",
    "finance_cost_change_events_read",
    "amountFen",
    "amount_fen",
    "quoteTotal",
    "grossProfit",
    "projectId",
    "projectName",
    "remark",
    "category",
    "SECRET",
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


@pytest.fixture
def disabled_settings(monkeypatch):
    """用途：切换 AUTH_MODE=disabled。"""
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(disabled_settings):
    with TestClient(app) as client:
        yield client


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
    """用途：幂等初始化本地管理员；已初始化时直接返回。"""
    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            return None
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
    body = res.json()
    return body["csrfToken"], body


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


def _login_finance(
    client: TestClient,
    *,
    username: str = "user_finance_p10j",
    password: str | None = None,
) -> tuple[str, str]:
    """用途：创建 finance 成员并登录，返回 (csrf, user_id)。"""
    csrf, _ = _owner_session(client)
    pwd = password or _ROLE_PASSWORDS["finance"]
    created = _create_member(
        client,
        csrf,
        username=username,
        password=pwd,
        role="finance",
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["userId"]
    res = _login(client, username, pwd)
    assert res.status_code == 200, res.text
    assert res.json()["user"]["id"] == user_id
    return res.json()["csrfToken"], user_id


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p10j"
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


def _seed_audit(
    *,
    actor_user_id: str,
    workspace_id: str = "ws_local",
    action: str = "finance_cost_create",
    result: str = "success",
    target: str | None = "fce_seed0001",
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """用途：直接写入审计行，便于隔离与排序断言。"""
    db = SessionLocal()
    try:
        eid = event_id or f"aud_p10j_{secrets_token()}"
        row = AuthAuditEventRow(
            id=eid,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            action=action,
            result=result,
            target=target,
            created_at=created_at or utc_now(),
        )
        db.add(row)
        db.commit()
        return eid
    finally:
        db.close()


def secrets_token() -> str:
    import secrets

    return secrets.token_hex(6)


def _ensure_other_workspace(ws_id: str = "ws_other_finance_p10j") -> str:
    db = SessionLocal()
    try:
        other = db.get(Workspace, ws_id)
        if other is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name="其他财务空间P10J",
                    owner_user_id="user_other_finance_p10j",
                )
            )
            db.commit()
        return ws_id
    finally:
        db.close()


# ---------- 失败先测：路由不存在时必须 404 ----------


def test_route_exists_after_implementation(required_client):
    """实现后路由必须存在；先写失败可证明测试先行。"""
    _, _ = _login_finance(required_client)
    res = required_client.get(_PATH)
    # 未实现时 404；实现后 200
    assert res.status_code != 404, "P10J 路由尚未注册（期望失败先测捕获）"
    assert res.status_code == 200, res.text


def test_empty_state_no_store_and_field_whitelist(required_client):
    """空态仍 200；仅 items=[]；no-store；字段白名单。"""
    _, _ = _login_finance(required_client)
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    assert body["items"] == []
    _assert_no_leak(body)


def test_self_three_actions_visible_and_mapped(required_client):
    """本人当前空间三类成功事件可见且映射 create/update/delete。"""
    _, user_id = _login_finance(required_client)
    base = datetime(2026, 7, 14, 10, 0, 0, tzinfo=timezone.utc)
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="fce_aaa11111",
        event_id="aud_p10j_map_c",
        created_at=base,
    )
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_update",
        target="fce_bbb22222",
        event_id="aud_p10j_map_u",
        created_at=base + timedelta(seconds=1),
    )
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_delete",
        target="fce_ccc33333",
        event_id="aud_p10j_map_d",
        created_at=base + timedelta(seconds=2),
    )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    items = body["items"]
    assert len(items) == 3
    # 倒序：delete → update → create
    assert [i["action"] for i in items] == ["delete", "update", "create"]
    assert [i["entryId"] for i in items] == [
        "fce_ccc33333",
        "fce_bbb22222",
        "fce_aaa11111",
    ]
    for item in items:
        assert set(item.keys()) == _ITEM_KEYS
        assert item["action"] in _ALLOWED_ACTIONS
        assert item["entryId"].startswith("fce_")
        assert "T" in item["occurredAt"] or item["occurredAt"]
    _assert_no_leak(body)


def test_isolation_other_actor_workspace_action_result_target(required_client):
    """
    同空间其他 finance、同 actor 其他空间、其他 action、非 success、
    空/非法 target 均不可见。
    """
    csrf_owner, _ = _owner_session(required_client)
    # finance A
    created_a = _create_member(
        required_client,
        csrf_owner,
        username="user_finance_a_p10j",
        password=_ROLE_PASSWORDS["finance"],
        role="finance",
    )
    assert created_a.status_code == 201, created_a.text
    user_a = created_a.json()["userId"]
    # finance B 同空间
    created_b = _create_member(
        required_client,
        csrf_owner,
        username="user_finance_b_p10j",
        password=_ROLE_PASSWORDS["finance_b"],
        role="finance",
    )
    assert created_b.status_code == 201, created_b.text
    user_b = created_b.json()["userId"]

    other_ws = _ensure_other_workspace()
    base = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

    # 可见：本人本空间成功
    _seed_audit(
        actor_user_id=user_a,
        action="finance_cost_create",
        target="fce_visible01",
        event_id="aud_p10j_vis",
        created_at=base,
    )
    # 不可见：同空间其他 finance
    _seed_audit(
        actor_user_id=user_b,
        action="finance_cost_create",
        target="fce_other_actor",
        event_id="aud_p10j_other_actor",
        created_at=base + timedelta(seconds=1),
    )
    # 不可见：同 actor 其他空间
    _seed_audit(
        actor_user_id=user_a,
        workspace_id=other_ws,
        action="finance_cost_update",
        target="fce_other_ws",
        event_id="aud_p10j_other_ws",
        created_at=base + timedelta(seconds=2),
    )
    # 不可见：其他 action
    _seed_audit(
        actor_user_id=user_a,
        action="login_success",
        target="fce_wrong_action",
        event_id="aud_p10j_other_act",
        created_at=base + timedelta(seconds=3),
    )
    # 不可见：非 success
    _seed_audit(
        actor_user_id=user_a,
        action="finance_cost_create",
        result="failure",
        target="fce_failed01",
        event_id="aud_p10j_fail",
        created_at=base + timedelta(seconds=4),
    )
    # 不可见：空 target
    _seed_audit(
        actor_user_id=user_a,
        action="finance_cost_create",
        target=None,
        event_id="aud_p10j_null_tgt",
        created_at=base + timedelta(seconds=5),
    )
    # 不可见：非法 target 前缀
    _seed_audit(
        actor_user_id=user_a,
        action="finance_cost_delete",
        target="hcc_not_cost",
        event_id="aud_p10j_bad_tgt",
        created_at=base + timedelta(seconds=6),
    )
    _seed_audit(
        actor_user_id=user_a,
        action="finance_cost_update",
        target="proj_xyz",
        event_id="aud_p10j_bad_tgt2",
        created_at=base + timedelta(seconds=7),
    )

    login = _login(required_client, "user_finance_a_p10j", _ROLE_PASSWORDS["finance"])
    assert login.status_code == 200, login.text
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["entryId"] == "fce_visible01"
    assert items[0]["action"] == "create"
    text = json.dumps(res.json(), ensure_ascii=False)
    assert "fce_other_actor" not in text
    assert "fce_other_ws" not in text
    assert "fce_failed01" not in text
    assert "hcc_not_cost" not in text
    assert "proj_xyz" not in text
    _assert_no_leak(res.json())


def test_limit_50_stable_desc_and_query_limit_ignored(required_client):
    """
    固定 50 条；同最新时间戳至少 3 条按审计 event id DESC 稳定倒序；
    ?limit=200 不能扩大。
    """
    _, user_id = _login_finance(required_client)
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    latest = base + timedelta(hours=1)

    # 52 条递增时间的旧事件（与下方 3 条同秒最新合计 55，LIMIT 挤出最旧 5 条）
    for i in range(52):
        _seed_audit(
            actor_user_id=user_id,
            action="finance_cost_create",
            target=f"fce_lim{i:04d}xxxx",
            event_id=f"aud_p10j_lim_{i:03d}",
            created_at=base + timedelta(seconds=i),
        )

    # 3 条同一最新 created_at；event id 字典序递增 → 响应须 id DESC 对应 entryId
    same_specs = [
        ("aud_p10j_same_001", "fce_same001xxxx"),
        ("aud_p10j_same_002", "fce_same002xxxx"),
        ("aud_p10j_same_003", "fce_same003xxxx"),
    ]
    for eid, target in same_specs:
        _seed_audit(
            actor_user_id=user_id,
            action="finance_cost_update",
            target=target,
            event_id=eid,
            created_at=latest,
        )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 50

    res_big = required_client.get(f"{_PATH}?limit=200")
    assert res_big.status_code == 200, res_big.text
    assert len(res_big.json()["items"]) == 50

    # 同时间戳三条必须全部进入响应，且按 event id DESC → entryId 顺序
    assert [items[i]["entryId"] for i in range(3)] == [
        "fce_same003xxxx",
        "fce_same002xxxx",
        "fce_same001xxxx",
    ]
    assert all(items[i]["action"] == "update" for i in range(3))
    # 其后为旧事件中较新者
    assert items[3]["entryId"] == "fce_lim0051xxxx"
    entry_ids = [x["entryId"] for x in items]
    # 最旧 5 条（i=0..4）被 LIMIT 挤出；i=5..51 保留
    assert "fce_lim0000xxxx" not in entry_ids
    assert "fce_lim0004xxxx" not in entry_ids
    assert "fce_lim0005xxxx" in entry_ids


def test_literal_fce_prefix_false_positives_do_not_consume_limit(required_client):
    """
    SQL 必须字面匹配 fce_ 前缀。
    旧实现 target.like('fce_%') 中 _ 为单字符通配，会让 fceX... 进入 SQL 候选
    并占满 LIMIT 50，Python 再过滤后合法更旧 fce_ 被挤出甚至空列表。
    本测在旧实现上必须失败，修复后合法行返回且非法行不可见。
    """
    _, user_id = _login_finance(required_client)
    base = datetime(2026, 7, 14, 8, 0, 0, tzinfo=timezone.utc)

    # 50 条时间更新、action/result/workspace/actor 均合法，但 target 会被旧 LIKE 误命中
    for i in range(50):
        _seed_audit(
            actor_user_id=user_id,
            action="finance_cost_create",
            # fce + 任意单字符 + bad...：匹配未转义 fce_%，不匹配字面 fce_
            target=f"fceXbad{i:04d}yyyy",
            event_id=f"aud_p10j_fpos_{i:03d}",
            created_at=base + timedelta(seconds=i + 1),
        )

    # 1 条较旧的合法 fce_ 行
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_update",
        target="fce_legit_old01",
        event_id="aud_p10j_legit_old",
        created_at=base,
    )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"]
    entry_ids = [x["entryId"] for x in items]

    # 修复后：非法行不占 LIMIT，合法行必须返回
    assert "fce_legit_old01" in entry_ids, (
        "合法 fce_ 行被 LIKE 误命中行占满 LIMIT 后挤出（旧实现缺陷）"
    )
    assert len(items) == 1
    assert items[0]["entryId"] == "fce_legit_old01"
    assert items[0]["action"] == "update"
    text = json.dumps(body, ensure_ascii=False)
    assert "fceXbad" not in text
    _assert_no_leak(body)


def test_whitespace_padded_target_not_normalized(required_client):
    """
    带首尾空白的历史 target 不得经 strip 归一化为合法 ID；
    合法 target 原样以 fce_ 开头且后缀非空，返回原值。
    """
    _, user_id = _login_finance(required_client)
    base = datetime(2026, 7, 14, 9, 0, 0, tzinfo=timezone.utc)
    # 前导空白：strip 后会像合法，原样必须以非法排除
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="  fce_padded_lead",
        event_id="aud_p10j_pad_lead",
        created_at=base,
    )
    # 仅前缀无后缀
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="fce_",
        event_id="aud_p10j_empty_suf",
        created_at=base + timedelta(seconds=1),
    )
    # 尾随空白：startswith+len>4 会误收，必须原样排除且不得 strip 返回
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="fce_padded_trail   ",
        event_id="aud_p10j_pad_trail",
        created_at=base + timedelta(seconds=1, milliseconds=500),
    )
    # 合法原值
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="fce_valid_ok01",
        event_id="aud_p10j_valid_ok",
        created_at=base + timedelta(seconds=2),
    )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["entryId"] == "fce_valid_ok01"
    text = json.dumps(res.json(), ensure_ascii=False)
    assert "fce_padded_lead" not in text
    assert "fce_padded_trail" not in text
    assert '"fce_"' not in text


@pytest.mark.parametrize(
    "case_id,build_bad_target,expected_legit,forbidden_fragment",
    [
        (
            "empty_suffix",
            lambda _i: "fce_",
            "fce_legit_empty01",
            None,
        ),
        (
            "trailing_whitespace",
            lambda i: f"fce_bad{i:04d}   ",
            "fce_legit_trail01",
            "fce_bad",
        ),
    ],
    ids=["empty_suffix_fce_", "trailing_whitespace"],
)
def test_invalid_complete_targets_do_not_consume_sql_limit(
    required_client,
    case_id: str,
    build_bad_target,
    expected_legit: str,
    forbidden_fragment: str | None,
):
    """
    完整合法 target 必须在 SQL LIMIT 前过滤，不得仅靠 Python 二次丢弃。
    旧实现缺陷（与 LIKE 误命中同型）：
    - target="fce_" 仅前缀匹配会进入最近 50 条，Python len>4 才丢弃；
    - 尾随空白 target（如 "fce_bad0001   "）startswith+len>4 会误收，
      或即便 Python 丢弃也已占满 LIMIT。
    本测：50 条更新的非法完整 target + 1 条更旧合法 fce_；
    旧实现上合法行被挤出必须失败；修复后合法行返回且非法不可见。
    """
    _, user_id = _login_finance(required_client)
    base = datetime(2026, 7, 14, 7, 0, 0, tzinfo=timezone.utc)
    event_prefix = f"aud_p10j_{case_id}_lim_"

    for i in range(50):
        _seed_audit(
            actor_user_id=user_id,
            action="finance_cost_create",
            target=build_bad_target(i),
            event_id=f"{event_prefix}{i:03d}",
            created_at=base + timedelta(seconds=i + 1),
        )

    # 1 条更旧合法事件：旧实现被 50 条非法行占满 LIMIT 后挤出
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_update",
        target=expected_legit,
        event_id=f"{event_prefix}legit",
        created_at=base,
    )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"]
    entry_ids = [x["entryId"] for x in items]

    assert expected_legit in entry_ids, (
        f"合法 {expected_legit} 被非法完整 target 占满 LIMIT 后挤出（旧实现缺陷）"
    )
    assert len(items) == 1
    assert items[0]["entryId"] == expected_legit
    assert items[0]["action"] == "update"
    text = json.dumps(body, ensure_ascii=False)
    assert all(x["entryId"] != "fce_" for x in items)
    assert '"entryId": "fce_"' not in text
    if forbidden_fragment is not None:
        assert forbidden_fragment not in text
    for item in items:
        tid = item["entryId"]
        assert tid == tid.strip()
        assert tid.startswith("fce_") and len(tid) > 4
    _assert_no_leak(body)


def test_select_projects_only_three_columns():
    """
    捕获实际 auth_audit_events SELECT：投影仅 action/target/created_at，
    不得整实体加载（SELECT 列表无 id/actor/workspace/result）。
    ORDER BY 可引用 id，但不得作为投影列。
    """
    db = SessionLocal()
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "auth_audit_events" not in low:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        # 排除写入后可能的无关语句；只保留三列业务读
        if "finance_cost_create" in low or "action" in low:
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        db.add(
            AuthAuditEventRow(
                id="aud_p10j_sql_probe",
                actor_user_id="actor_sql_probe",
                workspace_id="ws_local",
                action="finance_cost_create",
                result="success",
                target="fce_sql_probe01",
                created_at=utc_now(),
            )
        )
        db.commit()
        result = finance_cost_change_event_service.list_personal_cost_change_events(
            db,
            workspace_id="ws_local",
            actor_user_id="actor_sql_probe",
        )
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db.close()

    assert captured, "未捕获到 auth_audit_events 的 SELECT"
    # 取列表查询（非后续审计 INSERT 相关）
    select_sqls = [s for s in captured if s.lstrip().upper().startswith("SELECT")]
    assert select_sqls, f"无 SELECT: {captured}"
    select_sql = select_sqls[0]
    text = " ".join(select_sql.split())
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", text)
    assert match is not None, f"无法解析 SELECT: {select_sql}"
    select_list = match.group(1).lower()

    # 必须投影三业务列
    for col in ("action", "target", "created_at"):
        assert col in select_list, f"SELECT 缺少列 {col}: {select_sql}"

    # 禁止整实体：投影中不得出现下列列名（ORDER BY 段不在 select_list）
    for col in (
        "actor_user_id",
        "workspace_id",
        "result",
    ):
        assert col not in select_list, f"SELECT 投影泄漏列 {col}: {select_sql}"

    # id 不得作为投影列（ORDER BY 可在 FROM 之后引用 id）
    projected_cols = [p.strip() for p in select_list.split(",")]
    for col in projected_cols:
        bare = col.split(".")[-1].strip().strip('"').strip("'").strip("`")
        assert bare != "id", f"SELECT 投影不得含 id 列: {select_sql}"

    assert len(result["items"]) == 1
    assert result["items"][0]["entry_id"] == "fce_sql_probe01"
    assert result["items"][0]["action"] == "create"


def test_read_audit_fixed_and_not_in_list(required_client):
    """成功读取写固定脱敏审计，且该 action 不进入返回列表。"""
    _, user_id = _login_finance(required_client)
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_create",
        target="fce_audit_item1",
        event_id="aud_p10j_before_read",
    )

    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["entryId"] == "fce_audit_item1"
    text = json.dumps(body, ensure_ascii=False)
    assert "self_recent_50" not in text
    assert "finance_cost_change_events_read" not in text

    db = SessionLocal()
    try:
        events = (
            db.query(AuthAuditEventRow)
            .filter(AuthAuditEventRow.action == "finance_cost_change_events_read")
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.result == "success"
            assert e.target == "self_recent_50"
            assert e.actor_user_id == user_id
            assert e.workspace_id == "ws_local"
            blob = json.dumps(
                {
                    "action": e.action,
                    "result": e.result,
                    "target": e.target,
                    "actor": e.actor_user_id,
                    "workspace": e.workspace_id,
                },
                ensure_ascii=False,
            )
            assert "fce_audit_item1" not in blob
            assert "1" not in (e.target or "")  # 不得记录数量
            assert "amount" not in blob.lower()
    finally:
        db.close()

    # 再读一次：列表仍只有成本变更，不含读取审计
    res2 = required_client.get(_PATH)
    assert res2.status_code == 200
    ids = [i["entryId"] for i in res2.json()["items"]]
    assert ids == ["fce_audit_item1"]


def test_auth_matrix_required_roles(required_client):
    """required：未登录 401；bid_writer/hr/bidder/仅所有者 403 role_forbidden。"""
    # 先完成 bootstrap，再清会话：未登录须精确 401 auth_required
    _owner_session(required_client)
    required_client.cookies.clear()
    bare = required_client.get(_PATH)
    assert bare.status_code == 401, bare.text
    assert bare.json()["detail"]["code"] == "auth_required"

    for role in ("bid_writer", "hr", "bidder"):
        _login_role(required_client, role)
        res = required_client.get(_PATH)
        assert res.status_code == 403, f"{role}: {res.text}"
        assert res.json()["detail"]["code"] == "role_forbidden"

    # 仅所有者（默认 bootstrap role=bid_writer，成员角色非 finance）
    _owner_session(required_client)
    owner_res = required_client.get(_PATH)
    assert owner_res.status_code == 403, owner_res.text
    assert owner_res.json()["detail"]["code"] == "role_forbidden"


def test_owner_with_exact_finance_role_allowed(required_client):
    """isOwner 不能替代角色；bootstrap role=finance 时所有者按角色允许。"""
    _bootstrap(role=auth_service.ROLE_FINANCE)
    res_login = _login(required_client, _OWNER_USER, _OWNER_PASS)
    assert res_login.status_code == 200, res_login.text
    user_id = res_login.json()["user"]["id"]
    _seed_audit(
        actor_user_id=user_id,
        action="finance_cost_update",
        target="fce_owner_fin1",
        event_id="aud_p10j_owner_fin",
    )
    res = required_client.get(_PATH)
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1
    assert res.json()["items"][0]["action"] == "update"


def test_disabled_forbidden(disabled_client):
    """disabled 模式 403 role_forbidden。"""
    res = disabled_client.get(_PATH)
    assert res.status_code == 403, res.text
    detail = res.json().get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("code") == "role_forbidden"


def test_non_member_workspace_forbidden(required_client):
    """已登录 finance 指定非成员 X-Workspace-Id → 403 workspace_forbidden。"""
    _, _ = _login_finance(required_client)
    _ensure_other_workspace("ws_not_member_p10j")
    res = required_client.get(
        _PATH,
        headers={"X-Workspace-Id": "ws_not_member_p10j"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "workspace_forbidden"


def test_p10b_p10c_routes_unchanged(required_client):
    """不改变 P10B/P10C 既有路径可达性（finance 仍可访问列表）。"""
    _, _ = _login_finance(required_client)
    list_res = required_client.get("/api/finance/business-bids")
    assert list_res.status_code == 200, list_res.text
    assert "items" in list_res.json()
