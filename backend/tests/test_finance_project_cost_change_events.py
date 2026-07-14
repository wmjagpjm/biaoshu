"""
模块：P10K 财务项目成本变更记录定向测试
用途：验收商务标项目最近 50 条最小不可变成本事件写入/只读、事务原子、隔离、
  四列 SQL 投影、字段白名单、no-store、脱敏读取审计与权限矩阵。
对接：app.api.finance；app.services.finance_project_cost_change_event_service；
  app.services.finance_cost_service；deps.require_finance。
二次开发：仅使用固定合成口令与本地 SQLite；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    FinanceCostEntryRow,
    Project,
    Workspace,
    utc_now,
)
from app.services import (
    auth_service,
    editor_state_service,
    finance_cost_service,
    finance_project_cost_change_event_service,
    project_service,
)

# 固定合成口令：仅测试夹具
_OWNER_USER = "admin_finance_p10k"
_OWNER_PASS = "TestPass-Finance-P10K-Owner-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-Finance-P10K-Role-0001!",
    "finance_b": "TestPass-Finance-P10K-RoleB-0002!",
    "hr": "TestPass-Hr-P10K-Role-0001!",
    "bidder": "TestPass-Bidder-P10K-Role-0001!",
    "bid_writer": "TestPass-Writer-P10K-Role-0001!",
}
_TOP_KEYS = frozenset({"items"})
_ITEM_KEYS = frozenset({"action", "entryId", "actorScope", "occurredAt"})
_ALLOWED_ACTIONS = frozenset({"create", "update", "delete"})
_ALLOWED_SCOPES = frozenset({"self", "other"})
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
    "projectId",
    "projectName",
    "project_id",
    "fpce_",
    "amountFen",
    "amount_fen",
    "quoteTotal",
    "grossProfit",
    "remark",
    "category",
    "finance_cost_create",
    "finance_cost_update",
    "finance_cost_delete",
    "finance_project_cost_change_events_read",
    "current_project_recent_50",
    "SECRET",
    "username",
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
    username: str = "user_finance_p10k",
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
    username = f"user_{role}_p10k"
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


def _seed_projects() -> dict[str, str]:
    """用途：写入本空间商务标/技术标、另一商务标与跨空间商务标。"""
    db = SessionLocal()
    try:
        other = db.get(Workspace, "ws_other_finance_p10k")
        if other is None:
            db.add(
                Workspace(
                    id="ws_other_finance_p10k",
                    name="其他财务空间P10K",
                    owner_user_id="user_other_finance_p10k",
                )
            )
            db.commit()

        biz = project_service.create_project(
            db,
            "ws_local",
            name="P10K财务成本商务标",
            industry="能源",
            kind="business",
            status="draft",
        )
        biz2 = project_service.create_project(
            db,
            "ws_local",
            name="P10K另一商务标",
            industry="通用",
            kind="business",
            status="draft",
        )
        tech = project_service.create_project(
            db,
            "ws_local",
            name="P10K不可见技术标",
            industry="通用",
            kind="technical",
        )
        foreign = project_service.create_project(
            db,
            "ws_other_finance_p10k",
            name="跨空间P10K商务标",
            industry="跨域",
            kind="business",
        )
        for pid, ws in (
            (biz.id, "ws_local"),
            (biz2.id, "ws_local"),
            (tech.id, "ws_local"),
            (foreign.id, "ws_other_finance_p10k"),
        ):
            editor_state_service.upsert_editor_state(
                db,
                ws,
                pid,
                business_quote={
                    "rows": [
                        {
                            "id": "r1",
                            "name": "主机",
                            "unit": "套",
                            "quantity": "1",
                            "unitPrice": "10000",
                            "amount": 10000.0,
                            "remark": "",
                        }
                    ],
                    "notes": "P10K 报价",
                },
            )
        return {
            "business_id": biz.id,
            "business2_id": biz2.id,
            "technical_id": tech.id,
            "foreign_id": foreign.id,
        }
    finally:
        db.close()


def _events_path(project_id: str) -> str:
    return f"/api/finance/business-bids/{project_id}/cost-change-events"


def _cost_entries_path(project_id: str) -> str:
    return f"/api/finance/business-bids/{project_id}/cost-entries"


def _assert_no_leak(payload: object) -> None:
    text_blob = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    lower = text_blob.lower()
    for marker in _FORBIDDEN_MARKERS:
        assert marker.lower() not in lower, f"响应泄漏敏感标记: {marker}"


def _seed_event(
    *,
    workspace_id: str,
    project_id: str,
    entry_id: str,
    action: str,
    actor_user_id: str,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """用途：直接写入项目事件行，便于隔离/排序/非法过滤断言。"""
    from app.models.entities import FinanceProjectCostChangeEventRow

    db = SessionLocal()
    try:
        eid = event_id or f"fpce_{secrets.token_hex(8)}"
        db.add(
            FinanceProjectCostChangeEventRow(
                id=eid,
                workspace_id=workspace_id,
                project_id=project_id,
                entry_id=entry_id,
                action=action,
                actor_user_id=actor_user_id,
                created_at=created_at or utc_now(),
            )
        )
        db.commit()
        return eid
    finally:
        db.close()


# ---------- 失败先测：路由/表未实现时必须失败 ----------


def test_route_exists_after_implementation(required_client):
    """实现后项目事件路由必须存在；先写失败可证明测试先行。"""
    ids = _seed_projects()
    _, _ = _login_finance(required_client)
    res = required_client.get(_events_path(ids["business_id"]))
    assert res.status_code != 404, "P10K 路由尚未注册（期望失败先测捕获）"
    assert res.status_code == 200, res.text


def test_table_schema_constraints_and_indexes():
    """新表字段/CHECK/无 entry 外键/必要索引。"""
    insp = inspect(engine)
    assert "finance_project_cost_change_events" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("finance_project_cost_change_events")}
    assert cols == {
        "id",
        "workspace_id",
        "project_id",
        "entry_id",
        "action",
        "actor_user_id",
        "created_at",
    }
    fks = insp.get_foreign_keys("finance_project_cost_change_events")
    fk_cols = {tuple(fk["constrained_columns"]) for fk in fks}
    assert ("entry_id",) not in fk_cols
    # action CHECK 存在
    ck_sql = ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='finance_project_cost_change_events'"
            )
        ).fetchall()
        if rows:
            ck_sql = (rows[0][0] or "").lower()
    assert "create" in ck_sql and "update" in ck_sql and "delete" in ck_sql
    indexes = insp.get_indexes("finance_project_cost_change_events")
    index_cols = [tuple(ix.get("column_names") or []) for ix in indexes]
    # 契约要求复合索引列顺序精确为 (workspace_id, project_id, created_at)
    assert ("workspace_id", "project_id", "created_at") in index_cols, (
        f"缺少复合索引 (workspace_id, project_id, created_at)，实际: {index_cols}"
    )


def test_three_write_paths_each_record_one_event(required_client):
    """create/update/delete 各写一条项目事件；动作与 entryId 正确；self 映射。"""
    ids = _seed_projects()
    csrf, user_id = _login_finance(required_client)
    pid = ids["business_id"]
    headers = {"X-CSRF-Token": csrf}

    created = required_client.post(
        _cost_entries_path(pid),
        json={
            "category": "material",
            "name": "P10K设备",
            "amountFen": 1000,
            "remark": "仅事件测试",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    assert entry_id.startswith("fce_")

    patched = required_client.patch(
        f"{_cost_entries_path(pid)}/{entry_id}",
        json={"amountFen": 2000},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text

    deleted = required_client.delete(
        f"{_cost_entries_path(pid)}/{entry_id}",
        headers=headers,
    )
    assert deleted.status_code == 204, deleted.text

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    items = body["items"]
    assert len(items) == 3
    # 倒序：delete → update → create
    assert [i["action"] for i in items] == ["delete", "update", "create"]
    assert all(i["entryId"] == entry_id for i in items)
    assert all(i["actorScope"] == "self" for i in items)
    for item in items:
        assert set(item.keys()) == _ITEM_KEYS
        assert item["action"] in _ALLOWED_ACTIONS
        assert item["actorScope"] in _ALLOWED_SCOPES
        assert "T" in item["occurredAt"] or item["occurredAt"]
    _assert_no_leak(body)


def test_delete_preserves_event_after_entry_gone(required_client):
    """删除后业务行不存在，但项目 delete 事件仍在。"""
    ids = _seed_projects()
    csrf, _ = _login_finance(required_client)
    pid = ids["business_id"]
    headers = {"X-CSRF-Token": csrf}

    created = required_client.post(
        _cost_entries_path(pid),
        json={"category": "labor", "name": "保留事件", "amountFen": 500, "remark": ""},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]
    required_client.delete(f"{_cost_entries_path(pid)}/{entry_id}", headers=headers)

    db = SessionLocal()
    try:
        assert db.get(FinanceCostEntryRow, entry_id) is None
    finally:
        db.close()

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    actions = {i["action"]: i for i in res.json()["items"]}
    assert "delete" in actions
    assert actions["delete"]["entryId"] == entry_id
    assert "create" in actions


def test_same_transaction_rollback_when_event_record_fails(required_client):
    """事件写入异常时业务变更与审计不得提交。"""
    ids = _seed_projects()
    csrf, _ = _login_finance(required_client)
    pid = ids["business_id"]
    headers = {"X-CSRF-Token": csrf}

    with mock.patch(
        "app.services.finance_cost_service.finance_project_cost_change_event_service."
        "record_project_cost_change_event",
        side_effect=RuntimeError("p10k_event_fail"),
    ):
        with pytest.raises(RuntimeError, match="p10k_event_fail"):
            # 直接走服务层，避免 TestClient 吞异常
            db = SessionLocal()
            try:
                finance_cost_service.create_entry(
                    db,
                    workspace_id="ws_local",
                    project_id=pid,
                    actor_user_id="actor_tx_fail",
                    category="other",
                    name="应回滚",
                    amount_fen=123,
                    remark="",
                )
            finally:
                db.close()

    db = SessionLocal()
    try:
        entries = (
            db.query(FinanceCostEntryRow)
            .filter(
                FinanceCostEntryRow.project_id == pid,
                FinanceCostEntryRow.name == "应回滚",
            )
            .all()
        )
        assert entries == []
        from app.models.entities import FinanceProjectCostChangeEventRow

        events = (
            db.query(FinanceProjectCostChangeEventRow)
            .filter(FinanceProjectCostChangeEventRow.project_id == pid)
            .all()
        )
        assert events == []
        # 直接 SQL：actor_tx_fail 不得留下 finance_cost_create 成功审计
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM auth_audit_events "
                "WHERE actor_user_id = :actor "
                "AND action = :action "
                "AND result = :result"
            ),
            {
                "actor": "actor_tx_fail",
                "action": "finance_cost_create",
                "result": "success",
            },
        ).scalar()
        assert n == 0, f"事件失败回滚后仍有成功 create 审计: count={n}"
    finally:
        db.close()


def test_same_transaction_rollback_when_audit_fails(required_client):
    """脱敏审计异常时业务与项目事件均回滚。"""
    ids = _seed_projects()
    _ = _login_finance(required_client)
    pid = ids["business_id"]

    with mock.patch(
        "app.services.finance_cost_service.auth_service.record_audit",
        side_effect=RuntimeError("p10k_audit_fail"),
    ):
        db = SessionLocal()
        try:
            with pytest.raises(RuntimeError, match="p10k_audit_fail"):
                finance_cost_service.create_entry(
                    db,
                    workspace_id="ws_local",
                    project_id=pid,
                    actor_user_id="actor_audit_fail",
                    category="service",
                    name="审计失败回滚",
                    amount_fen=77,
                    remark="",
                )
        finally:
            db.close()

    db = SessionLocal()
    try:
        assert (
            db.query(FinanceCostEntryRow)
            .filter(FinanceCostEntryRow.name == "审计失败回滚")
            .count()
            == 0
        )
        from app.models.entities import FinanceProjectCostChangeEventRow

        assert (
            db.query(FinanceProjectCostChangeEventRow)
            .filter(FinanceProjectCostChangeEventRow.project_id == pid)
            .count()
            == 0
        )
    finally:
        db.close()


def test_no_backfill_from_old_p10j_audit(required_client):
    """旧 P10J 审计不得被猜项目回填到项目事件列表。"""
    ids = _seed_projects()
    _, user_id = _login_finance(required_client)
    pid = ids["business_id"]
    db = SessionLocal()
    try:
        db.add(
            AuthAuditEventRow(
                id=f"aud_p10k_old_{secrets.token_hex(4)}",
                actor_user_id=user_id,
                workspace_id="ws_local",
                action="finance_cost_create",
                result="success",
                target="fce_old_p10j_only",
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []
    text_blob = json.dumps(res.json(), ensure_ascii=False)
    assert "fce_old_p10j_only" not in text_blob


def test_other_actor_maps_to_other_scope(required_client):
    """同空间其他 finance 写入的事件对当前用户映射 actorScope=other。"""
    ids = _seed_projects()
    csrf_owner, _ = _owner_session(required_client)
    created_a = _create_member(
        required_client,
        csrf_owner,
        username="user_finance_a_p10k",
        password=_ROLE_PASSWORDS["finance"],
        role="finance",
    )
    assert created_a.status_code == 201, created_a.text
    user_a = created_a.json()["userId"]
    created_b = _create_member(
        required_client,
        csrf_owner,
        username="user_finance_b_p10k",
        password=_ROLE_PASSWORDS["finance_b"],
        role="finance",
    )
    assert created_b.status_code == 201, created_b.text

    # A 写一条事件
    _seed_event(
        workspace_id="ws_local",
        project_id=ids["business_id"],
        entry_id="fce_other_actor01",
        action="create",
        actor_user_id=user_a,
        event_id="fpce_other_actor01",
    )

    login_b = _login(
        required_client, "user_finance_b_p10k", _ROLE_PASSWORDS["finance_b"]
    )
    assert login_b.status_code == 200, login_b.text
    res = required_client.get(_events_path(ids["business_id"]))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["entryId"] == "fce_other_actor01"
    assert items[0]["actorScope"] == "other"
    _assert_no_leak(res.json())
    assert user_a not in json.dumps(res.json(), ensure_ascii=False)


def test_project_and_workspace_isolation(required_client):
    """其他项目/跨空间事件不可见。"""
    ids = _seed_projects()
    _, user_id = _login_finance(required_client)
    _seed_event(
        workspace_id="ws_local",
        project_id=ids["business_id"],
        entry_id="fce_vis_proj01",
        action="create",
        actor_user_id=user_id,
        event_id="fpce_vis_proj01",
    )
    _seed_event(
        workspace_id="ws_local",
        project_id=ids["business2_id"],
        entry_id="fce_other_proj01",
        action="update",
        actor_user_id=user_id,
        event_id="fpce_other_proj01",
    )
    _seed_event(
        workspace_id="ws_other_finance_p10k",
        project_id=ids["foreign_id"],
        entry_id="fce_foreign01",
        action="delete",
        actor_user_id=user_id,
        event_id="fpce_foreign01",
    )

    res = required_client.get(_events_path(ids["business_id"]))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["entryId"] == "fce_vis_proj01"
    text_blob = json.dumps(res.json(), ensure_ascii=False)
    assert "fce_other_proj01" not in text_blob
    assert "fce_foreign01" not in text_blob


def test_technical_foreign_missing_unified_404(required_client):
    """技术标/跨空间/伪造项目统一 404 project_not_found，不反射路径 ID。"""
    ids = _seed_projects()
    _, _ = _login_finance(required_client)

    for pid in (ids["technical_id"], ids["foreign_id"], "proj_fake_not_exist_p10k"):
        res = required_client.get(_events_path(pid))
        assert res.status_code == 404, f"{pid}: {res.text}"
        detail = res.json()["detail"]
        assert detail["code"] == "project_not_found"
        blob = json.dumps(res.json(), ensure_ascii=False)
        assert pid not in blob


def test_limit_50_stable_order(required_client):
    """固定 50；同时间戳按 id DESC；客户端 limit 无效。"""
    ids = _seed_projects()
    _, user_id = _login_finance(required_client)
    pid = ids["business_id"]
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    latest = base + timedelta(hours=1)

    for i in range(52):
        _seed_event(
            workspace_id="ws_local",
            project_id=pid,
            entry_id=f"fce_lim{i:04d}xxxx",
            action="create",
            actor_user_id=user_id,
            event_id=f"fpce_lim_{i:03d}",
            created_at=base + timedelta(seconds=i),
        )
    for eid, entry in (
        ("fpce_same_001", "fce_same001xxxx"),
        ("fpce_same_002", "fce_same002xxxx"),
        ("fpce_same_003", "fce_same003xxxx"),
    ):
        _seed_event(
            workspace_id="ws_local",
            project_id=pid,
            entry_id=entry,
            action="update",
            actor_user_id=user_id,
            event_id=eid,
            created_at=latest,
        )

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 50

    res_big = required_client.get(f"{_events_path(pid)}?limit=200")
    assert res_big.status_code == 200, res_big.text
    assert len(res_big.json()["items"]) == 50

    assert [items[i]["entryId"] for i in range(3)] == [
        "fce_same003xxxx",
        "fce_same002xxxx",
        "fce_same001xxxx",
    ]
    entry_ids = [x["entryId"] for x in items]
    assert "fce_lim0000xxxx" not in entry_ids
    assert "fce_lim0004xxxx" not in entry_ids
    assert "fce_lim0005xxxx" in entry_ids


def test_invalid_rows_filtered_before_limit(required_client):
    """非法 entry/空 actor 在 SQL LIMIT 前排除，不挤出合法更旧行。"""
    ids = _seed_projects()
    _, user_id = _login_finance(required_client)
    pid = ids["business_id"]
    base = datetime(2026, 7, 14, 8, 0, 0, tzinfo=timezone.utc)

    # 合法旧行
    _seed_event(
        workspace_id="ws_local",
        project_id=pid,
        entry_id="fce_legit_old01",
        action="update",
        actor_user_id=user_id,
        event_id="fpce_legit_old",
        created_at=base,
    )

    # 50 条更晚非法行：契约表结构允许写入，但查询应排除
    # - 仅 fce_ 无后缀 / 假前缀 fceX / 首尾空白 entry / 空 actor
    # 禁止吞异常或二次兜底；插入失败必须让测试失败
    illegal_rows: list[dict[str, object]] = []
    for i in range(50):
        kind = i % 5
        if kind == 0:
            entry_id, actor = "fce_", user_id
        elif kind == 1:
            entry_id, actor = f"fceXbad{i:04d}yyyy", user_id
        elif kind == 2:
            entry_id, actor = f"  fce_pad{i:04d}", user_id
        elif kind == 3:
            entry_id, actor = f"fce_trail{i:04d}   ", user_id
        else:
            # 空 actor：entry 使用合法形态仍应被 actor 条件排除
            entry_id, actor = f"fce_emptyactor{i:04d}", ""
        illegal_rows.append(
            {
                "id": f"fpce_bad_{i:03d}",
                "ws": "ws_local",
                "pid": pid,
                "eid": entry_id,
                "act": "create",
                "actor": actor,
                "ts": (base + timedelta(seconds=i + 1)).isoformat(),
            }
        )

    db = SessionLocal()
    try:
        for row in illegal_rows:
            db.execute(
                text(
                    "INSERT INTO finance_project_cost_change_events "
                    "(id, workspace_id, project_id, entry_id, action, "
                    "actor_user_id, created_at) "
                    "VALUES (:id, :ws, :pid, :eid, :act, :actor, :ts)"
                ),
                row,
            )
        db.commit()
        # 查询前直接 SQL COUNT 精确等于 50
        bad_count = db.execute(
            text(
                "SELECT COUNT(*) FROM finance_project_cost_change_events "
                "WHERE project_id = :pid AND id LIKE 'fpce\\_bad\\_%' ESCAPE '\\'"
            ),
            {"pid": pid},
        ).scalar()
        assert bad_count == 50, f"必须成功插入恰好 50 条非法行，实际={bad_count}"
        total = db.execute(
            text(
                "SELECT COUNT(*) FROM finance_project_cost_change_events "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar()
        assert total == 51, f"合法旧行+50非法行应为 51，实际={total}"
    finally:
        db.close()

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    entry_ids = [x["entryId"] for x in items]
    assert "fce_legit_old01" in entry_ids, "合法行被非法行占满 LIMIT 后挤出"
    text_blob = json.dumps(res.json(), ensure_ascii=False)
    assert "fceXbad" not in text_blob
    assert "fce_pad" not in text_blob
    assert "fce_trail" not in text_blob
    assert "fce_emptyactor" not in text_blob
    for item in items:
        tid = item["entryId"]
        assert tid == tid.strip()
        assert tid.startswith("fce_") and len(tid) > 4


def test_select_projects_only_four_columns():
    """事件 SELECT 仅投影 action/entry_id/actor_user_id/created_at 四列。"""
    db = SessionLocal()
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "finance_project_cost_change_events" not in low:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append(statement)

    # 准备项目与事件
    try:
        if db.get(Workspace, "ws_local") is None:
            db.add(
                Workspace(
                    id="ws_local", name="本地", owner_user_id="u_sql"
                )
            )
            db.commit()
        proj = project_service.create_project(
            db,
            "ws_local",
            name="SQL投影商务标",
            industry="测",
            kind="business",
        )
        from app.models.entities import FinanceProjectCostChangeEventRow

        db.add(
            FinanceProjectCostChangeEventRow(
                id="fpce_sql_probe01",
                workspace_id="ws_local",
                project_id=proj.id,
                entry_id="fce_sql_probe01",
                action="create",
                actor_user_id="actor_sql_probe",
                created_at=utc_now(),
            )
        )
        db.commit()

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            result = finance_project_cost_change_event_service.list_project_cost_change_events(
                db,
                workspace_id="ws_local",
                project_id=proj.id,
                actor_user_id="actor_sql_probe",
            )
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
    finally:
        db.close()

    # 过滤出事件表业务读 SELECT（排除项目校验可能的 projects 查询）
    select_sqls = [
        s
        for s in captured
        if s.lstrip().upper().startswith("SELECT")
        and "finance_project_cost_change_events" in s.lower()
    ]
    assert select_sqls, f"未捕获事件 SELECT: {captured}"
    select_sql = select_sqls[0]
    text_sql = " ".join(select_sql.split())
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", text_sql)
    assert match is not None, f"无法解析 SELECT: {select_sql}"
    select_list = match.group(1).lower()

    for col in ("action", "entry_id", "actor_user_id", "created_at"):
        assert col in select_list, f"SELECT 缺少列 {col}: {select_sql}"

    for col in ("workspace_id", "project_id"):
        # 可能出现在 WHERE，但不得在投影列表
        projected_cols = [p.strip() for p in select_list.split(",")]
        for pcol in projected_cols:
            bare = pcol.split(".")[-1].strip().strip('"').strip("'").strip("`")
            assert bare != col, f"SELECT 投影泄漏列 {col}: {select_sql}"

    projected_cols = [p.strip() for p in select_list.split(",")]
    for pcol in projected_cols:
        bare = pcol.split(".")[-1].strip().strip('"').strip("'").strip("`")
        assert bare != "id", f"SELECT 投影不得含 id 列: {select_sql}"

    assert len(result["items"]) == 1
    assert result["items"][0]["entry_id"] == "fce_sql_probe01"
    assert result["items"][0]["actor_scope"] == "self"


def test_project_check_selects_only_project_id(required_client):
    """读取前项目校验仅 select(Project.id)，不加载 editor-state/报价/成本实体。"""
    ids = _seed_projects()
    _, _ = _login_finance(required_client)
    pid = ids["business_id"]
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        low = statement.lower()
        if "projects" in low or "editor" in low or "finance_cost_entries" in low:
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = required_client.get(_events_path(pid))
        assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # 精确找到 P10K 项目校验 SELECT：FROM projects 且校验 business/kind
    projects_selects = [
        s
        for s in captured
        if re.search(r"(?is)\bfrom\b\s+[\"`]?projects[\"`]?", s)
    ]
    assert projects_selects, f"未捕获 projects SELECT: {captured}"

    validation_sqls = [
        s
        for s in projects_selects
        if "business" in s.lower() or re.search(r"(?is)\bkind\b", s)
    ]
    assert validation_sqls, (
        f"未找到含 kind/business 的 P10K 项目校验 SELECT: {projects_selects}"
    )

    for s in validation_sqls:
        text_sql = " ".join(s.split())
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", text_sql)
        assert match is not None, f"无法解析 SELECT: {s}"
        select_list = match.group(1)
        projected = [p.strip() for p in select_list.split(",") if p.strip()]
        bares = [
            p.split(".")[-1].strip().strip('"').strip("'").strip("`").lower()
            for p in projected
        ]
        # 投影必须精确只有 projects.id（WHERE 可含 workspace/kind）
        assert bares == ["id"], f"项目校验投影必须仅为 id，实际 {bares}: {s}"

    # 请求期间不得 SELECT editor-state 或成本实体
    for s in captured:
        low = s.lower()
        assert "editor" not in low, f"请求期间加载了 editor-state: {s}"
        assert "finance_cost_entries" not in low, (
            f"请求期间加载了 finance_cost_entries: {s}"
        )


def test_read_audit_fixed_and_desensitized(required_client):
    """成功读取写固定脱敏审计，且不记录项目/条目/数量。"""
    ids = _seed_projects()
    _, user_id = _login_finance(required_client)
    pid = ids["business_id"]
    _seed_event(
        workspace_id="ws_local",
        project_id=pid,
        entry_id="fce_audit_item1",
        action="create",
        actor_user_id=user_id,
        event_id="fpce_audit_item1",
    )

    res = required_client.get(_events_path(pid))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    text_blob = json.dumps(body, ensure_ascii=False)
    assert "current_project_recent_50" not in text_blob
    assert "finance_project_cost_change_events_read" not in text_blob

    db = SessionLocal()
    try:
        events = (
            db.query(AuthAuditEventRow)
            .filter(
                AuthAuditEventRow.action
                == "finance_project_cost_change_events_read"
            )
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.result == "success"
            assert e.target == "current_project_recent_50"
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
            assert pid not in blob
            assert "amount" not in blob.lower()
    finally:
        db.close()


def test_empty_state_field_whitelist_no_store(required_client):
    """空态 200；仅 items=[]；no-store；字段白名单。"""
    ids = _seed_projects()
    _, _ = _login_finance(required_client)
    res = required_client.get(_events_path(ids["business_id"]))
    assert res.status_code == 200, res.text
    assert res.headers.get("cache-control", "").lower() == "no-store"
    body = res.json()
    assert set(body.keys()) == _TOP_KEYS
    assert body["items"] == []
    _assert_no_leak(body)


def test_auth_matrix_required_roles(required_client):
    """required：未登录 401；bid_writer/hr/bidder/仅所有者 403 role_forbidden。"""
    ids = _seed_projects()
    path = _events_path(ids["business_id"])
    _owner_session(required_client)
    required_client.cookies.clear()
    bare = required_client.get(path)
    assert bare.status_code == 401, bare.text
    assert bare.json()["detail"]["code"] == "auth_required"

    for role in ("bid_writer", "hr", "bidder"):
        _login_role(required_client, role)
        res = required_client.get(path)
        assert res.status_code == 403, f"{role}: {res.text}"
        assert res.json()["detail"]["code"] == "role_forbidden"

    _owner_session(required_client)
    owner_res = required_client.get(path)
    assert owner_res.status_code == 403, owner_res.text
    assert owner_res.json()["detail"]["code"] == "role_forbidden"


def test_owner_with_exact_finance_role_allowed(required_client):
    """isOwner 不能替代角色；bootstrap role=finance 时允许。"""
    ids = _seed_projects()
    _bootstrap(role=auth_service.ROLE_FINANCE)
    res_login = _login(required_client, _OWNER_USER, _OWNER_PASS)
    assert res_login.status_code == 200, res_login.text
    user_id = res_login.json()["user"]["id"]
    _seed_event(
        workspace_id="ws_local",
        project_id=ids["business_id"],
        entry_id="fce_owner_fin1",
        action="update",
        actor_user_id=user_id,
        event_id="fpce_owner_fin1",
    )
    res = required_client.get(_events_path(ids["business_id"]))
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1
    assert res.json()["items"][0]["actorScope"] == "self"


def test_disabled_forbidden(disabled_client):
    """disabled 模式 403 role_forbidden。"""
    # disabled 下无项目 seed 也可直接测 403；路径存在即可
    res = disabled_client.get(
        "/api/finance/business-bids/any_project/cost-change-events"
    )
    assert res.status_code == 403, res.text
    detail = res.json().get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("code") == "role_forbidden"


def test_non_member_workspace_forbidden(required_client):
    """已登录 finance 指定非成员 X-Workspace-Id → 403 workspace_forbidden。"""
    ids = _seed_projects()
    _, _ = _login_finance(required_client)
    res = required_client.get(
        _events_path(ids["business_id"]),
        headers={"X-Workspace-Id": "ws_other_finance_p10k"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "workspace_forbidden"


def test_p10c_p10j_routes_still_work(required_client):
    """P10C/P10J 既有接口兼容可达。"""
    ids = _seed_projects()
    csrf, _ = _login_finance(required_client)
    headers = {"X-CSRF-Token": csrf}
    list_res = required_client.get("/api/finance/business-bids")
    assert list_res.status_code == 200, list_res.text
    draft = required_client.get(
        f"/api/finance/business-bids/{ids['business_id']}/cost-draft"
    )
    assert draft.status_code == 200, draft.text
    personal = required_client.get("/api/finance/cost-change-events")
    assert personal.status_code == 200, personal.text
    created = required_client.post(
        _cost_entries_path(ids["business_id"]),
        json={"category": "other", "name": "兼容", "amountFen": 1, "remark": ""},
        headers=headers,
    )
    assert created.status_code == 201, created.text
