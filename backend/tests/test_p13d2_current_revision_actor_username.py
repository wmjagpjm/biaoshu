"""
模块：P13-D2 当前已载入版本操作者用户名展示专项测试
用途：验收 GET|PUT editor-state 必出可空 currentRevisionActorUsername；
      仅当最新修订版本匹配且 actor 可解析为启用用户+同工作区启用成员时返回安全用户名。
对接：editor_state_revision_service 元数据 resolver；EditorStateOut；projects GET|PUT。
二次开发：禁止加载 snapshot、回扫旧同版本、客户端投稿、公开 actor ID；来源与用户名独立降级。
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateRevisionRow,
    LocalUserRow,
    ProjectEditorStateRow,
    Workspace,
    WorkspaceMemberRow,
    utc_now,
)
from app.services import auth_service, editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p13d2"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_SECRET = "SECRET_P13D2_MUST_NOT_LEAK"
_TEST_USERNAME = "admin_p13d2"
_TEST_PASSWORD = "P13d2-Test-Pass-9!"
_FAKE_ACTOR = "user_client_forged_p13d2"


# ---------- 基础工具 ----------


def _assert_sv(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _create_project(client: TestClient, name: str = "P13D2项目") -> str:
    res = client.post("/api/projects", json={"name": name, "mode": "technical"})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _put(client: TestClient, pid: str, payload: dict, *, headers: dict | None = None):
    res = client.put(
        f"/api/projects/{pid}/editor-state",
        json=payload,
        headers=headers or {},
    )
    assert res.status_code == 200, res.text
    return res


def _get(client: TestClient, pid: str, *, headers: dict | None = None):
    res = client.get(
        f"/api/projects/{pid}/editor-state",
        headers=headers or {},
    )
    assert res.status_code == 200, res.text
    return res


def _db_rev_rows(project_id: str) -> list[EditorStateRevisionRow]:
    db = SessionLocal()
    try:
        return list(
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
    finally:
        db.close()


def _assert_no_actor_id_leak(blob: str, *ids: str) -> None:
    """用途：递归检查 dict/list；禁止 actor ID / 口令 / 近似 actor 键；
    唯一放行精确键 currentRevisionActorUsername；逐个断言 ids 不在响应。"""
    import json as _json

    try:
        payload = _json.loads(blob)
    except Exception:
        payload = None

    allowed_exact = {"currentRevisionActorUsername"}
    banned_keys = {
        "actoruserid",
        "actor_user_id",
        "currentrevisionactor",
        "actor",
        "actors",
        "actorid",
        "actor_id",
    }

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert isinstance(key, str), key
                if key in allowed_exact:
                    _walk(value)
                    continue
                assert key.lower() not in banned_keys, f"响应泄漏 actor 键: {key}"
                assert "actor" not in key.lower(), f"响应泄漏 actor 相关键: {key}"
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    if payload is not None:
        _walk(payload)

    low = blob.lower()
    assert "traceback" not in low
    assert "password" not in low
    assert "password_hash" not in low
    assert "password_salt" not in low
    assert _SECRET not in blob
    assert "select " not in low
    assert "sqlite" not in low
    # 必须实际使用 ids：逐个断言 user_id/伪造 ID 不在响应
    assert ids is not None
    for uid in ids:
        if uid:
            assert uid not in blob, f"响应泄漏 id: {uid}"


def _ensure_workspace(ws_id: str, name: str = "其他空间P13D2") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(Workspace(id=ws_id, name=name, owner_user_id="user_other_p13d2"))
            db.commit()
    finally:
        db.close()


# ---------- required 认证夹具 ----------


@pytest.fixture
def required_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    with TestClient(app) as client:
        yield client


def _bootstrap_and_login(
    client: TestClient,
    *,
    username: str = _TEST_USERNAME,
    password: str = _TEST_PASSWORD,
    role: str = auth_service.ROLE_BID_WRITER,
) -> tuple[str, str, str]:
    """用途：bootstrap 本地用户并登录；返回 (user_id, csrf, username)。"""
    db = SessionLocal()
    try:
        principal = auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=username,
            password=password,
            role=role,
        )
        user_id = principal.user_id
    finally:
        db.close()
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrfToken"]
    return user_id, csrf, username


def _create_project_required(
    client: TestClient, headers: dict, name: str = "P13D2必登"
) -> str:
    res = client.post(
        "/api/projects",
        headers=headers,
        json={"name": name, "mode": "technical"},
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _set_user_active(user_id: str, active: bool) -> None:
    db = SessionLocal()
    try:
        row = db.get(LocalUserRow, user_id)
        assert row is not None
        row.is_active = active
        db.commit()
    finally:
        db.close()


def _set_member_active(user_id: str, active: bool, workspace_id: str = _WS) -> None:
    db = SessionLocal()
    try:
        m = (
            db.query(WorkspaceMemberRow)
            .filter(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == user_id,
            )
            .one()
        )
        m.is_active = active
        db.commit()
    finally:
        db.close()


def _set_member_role(user_id: str, role: str, workspace_id: str = _WS) -> None:
    db = SessionLocal()
    try:
        m = (
            db.query(WorkspaceMemberRow)
            .filter(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == user_id,
            )
            .one()
        )
        m.role = role
        db.commit()
    finally:
        db.close()


def _delete_member(user_id: str, workspace_id: str = _WS) -> None:
    db = SessionLocal()
    try:
        m = (
            db.query(WorkspaceMemberRow)
            .filter(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == user_id,
            )
            .one_or_none()
        )
        if m is not None:
            db.delete(m)
            db.commit()
    finally:
        db.close()


def _set_username(user_id: str, username: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(LocalUserRow, user_id)
        assert row is not None
        row.username = username
        row.username_normalized = username.strip().lower()
        db.commit()
    finally:
        db.close()


def _set_latest_actor(project_id: str, actor_user_id: str | None) -> None:
    db = SessionLocal()
    try:
        latest = (
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .first()
        )
        assert latest is not None
        latest.actor_user_id = actor_user_id
        db.commit()
    finally:
        db.close()


def _set_latest_source(project_id: str, source_kind: str) -> None:
    db = SessionLocal()
    try:
        latest = (
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .first()
        )
        assert latest is not None
        latest.source_kind = source_kind
        db.commit()
    finally:
        db.close()


def _insert_orphan_user(
    *,
    username: str = "orphan_p13d2",
    workspace_id: str | None = None,
    active: bool = True,
) -> str:
    """用途：插入用户；可选仅加入指定 workspace。"""
    uid = f"user_{secrets.token_hex(8)}"
    db = SessionLocal()
    try:
        db.add(
            LocalUserRow(
                id=uid,
                username=username,
                username_normalized=username.lower(),
                password_salt="s" * 32,
                password_hash="h" * 64,
                is_active=active,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        if workspace_id is not None:
            db.add(
                WorkspaceMemberRow(
                    id=f"wsm_{secrets.token_hex(8)}",
                    workspace_id=workspace_id,
                    user_id=uid,
                    role="bid_writer",
                    is_owner=False,
                    is_active=True,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
        db.commit()
    finally:
        db.close()
    return uid


# ---------- failure-first / 行为验收 ----------


def test_get_empty_ledger_returns_null_actor_username_zero_write(client: TestClient):
    """用途：无账本时 currentRevisionActorUsername 精确 null；GET 五域零写。"""
    pid = _create_project(client, name="空账本-actor")
    assert len(_db_rev_rows(pid)) == 0

    db0 = SessionLocal()
    try:
        editor0 = db0.get(ProjectEditorStateRow, pid)
        editor_updated0 = editor0.updated_at if editor0 is not None else None
        rev_total0 = db0.query(EditorStateRevisionRow).count()
        user_total0 = db0.query(LocalUserRow).count()
        member_total0 = db0.query(WorkspaceMemberRow).count()
    finally:
        db0.close()

    res = _get(client, pid)
    body = res.json()
    # 首个业务断言：字段必须存在且为 null
    assert "currentRevisionActorUsername" in body, list(body.keys())
    assert body["currentRevisionActorUsername"] is None
    assert "currentRevisionSourceKind" in body
    _assert_sv(body["stateVersion"])
    _assert_no_actor_id_leak(res.text)

    db1 = SessionLocal()
    try:
        editor1 = db1.get(ProjectEditorStateRow, pid)
        editor_updated1 = editor1.updated_at if editor1 is not None else None
        rev_total1 = db1.query(EditorStateRevisionRow).count()
        user_total1 = db1.query(LocalUserRow).count()
        member_total1 = db1.query(WorkspaceMemberRow).count()
    finally:
        db1.close()
    assert rev_total1 == rev_total0
    assert user_total1 == user_total0
    assert member_total1 == member_total0
    assert editor_updated1 == editor_updated0
    assert len(_db_rev_rows(pid)) == 0


def test_required_put_and_get_return_actor_username(required_client: TestClient):
    """用途：required browser PUT 后 GET/PUT 返回当前活动同工作区用户名。"""
    user_id, csrf, username = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="真实actor")

    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={
            "facts": [{"id": "f1", "text": "有操作者"}],
            # 客户端投稿同名字段与 actor ID 必须无效
            "currentRevisionActorUsername": "forged-name",
            "actorUserId": _FAKE_ACTOR,
            "actor_user_id": _FAKE_ACTOR,
        },
    )
    assert put.status_code == 200, put.text
    put_body = put.json()
    assert "currentRevisionActorUsername" in put_body
    assert put_body["currentRevisionActorUsername"] == username
    after = _assert_sv(put_body["stateVersion"])
    _assert_no_actor_id_leak(put.text, user_id, _FAKE_ACTOR)

    got = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    ).json()
    assert got["currentRevisionActorUsername"] == username
    assert got["stateVersion"] == after
    assert got["currentRevisionSourceKind"] == "browser_put"
    _assert_no_actor_id_leak(
        required_client.get(
            f"/api/projects/{pid}/editor-state", headers=headers
        ).text,
        user_id,
    )


@pytest.mark.parametrize(
    "case",
    [
        "actor_null",
        "user_missing",
        "user_inactive",
        "member_missing",
        "member_inactive",
        "other_workspace_only",
        "username_blank",
        "username_padded",
        "username_too_long",
        "username_c0",
        "username_c1",
        "username_del",
        "username_line_sep",
        "username_bidi",
    ],
)
def test_actor_username_null_cases(required_client: TestClient, case: str):
    """用途：不可解析 actor/用户名时统一 null 且不 500。"""
    user_id, csrf, _ = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name=f"null-{case}")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "n", "text": case}]},
    )
    assert put.status_code == 200, put.text
    ver = _assert_sv(put.json()["stateVersion"])

    # 用户/成员停用或移除不得破坏当前会话查询：把最新 actor 指到另一目标用户再测
    if case == "actor_null":
        _set_latest_actor(pid, None)
    elif case == "user_missing":
        _set_latest_actor(pid, f"user_missing_{secrets.token_hex(4)}")
    elif case == "user_inactive":
        target = _insert_orphan_user(
            username="inactive_target", workspace_id=_WS, active=True
        )
        _set_latest_actor(pid, target)
        _set_user_active(target, False)
    elif case == "member_missing":
        target = _insert_orphan_user(
            username="no_member_target", workspace_id=None, active=True
        )
        _set_latest_actor(pid, target)
    elif case == "member_inactive":
        target = _insert_orphan_user(
            username="member_off", workspace_id=_WS, active=True
        )
        _set_latest_actor(pid, target)
        _set_member_active(target, False)
    elif case == "other_workspace_only":
        _ensure_workspace(_WS_OTHER)
        other = _insert_orphan_user(username="only_other_ws", workspace_id=_WS_OTHER)
        _set_latest_actor(pid, other)
    elif case == "username_blank":
        _set_username(user_id, "")
    elif case == "username_padded":
        _set_username(user_id, " padded ")
    elif case == "username_too_long":
        _set_username(user_id, "汉" * 101)
    elif case == "username_c0":
        _set_username(user_id, "bad\x01name")
    elif case == "username_c1":
        _set_username(user_id, "bad\x81name")
    elif case == "username_del":
        _set_username(user_id, "bad\x7fname")
    elif case == "username_line_sep":
        _set_username(user_id, "bad\u2028name")
    elif case == "username_bidi":
        _set_username(user_id, "bad\u202ename")
    else:
        raise AssertionError(case)

    res = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stateVersion"] == ver
    assert body["currentRevisionActorUsername"] is None
    _assert_no_actor_id_leak(res.text, user_id)


def test_no_backscan_when_latest_mismatched(required_client: TestClient):
    """用途：最新修订版本不匹配时两项元数据均 null，不回扫旧同版本合法 actor。"""
    user_id, csrf, username = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="不回扫")

    put1 = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "old", "text": "旧合法"}]},
    )
    assert put1.status_code == 200, put1.text
    old_ver = _assert_sv(put1.json()["stateVersion"])
    assert put1.json()["currentRevisionActorUsername"] == username

    put2 = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={
            "facts": [{"id": "new", "text": "新版本"}],
            "expectedStateVersion": old_ver,
        },
    )
    assert put2.status_code == 200, put2.text
    new_ver = _assert_sv(put2.json()["stateVersion"])
    assert new_ver != old_ver

    # 污染最新行：版本永不匹配 + 坏 actor；旧行仍合法
    db = SessionLocal()
    try:
        latest = (
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == pid,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .first()
        )
        assert latest is not None
        assert latest.state_version == new_ver
        latest.state_version = "esv_" + ("e" * 32)
        latest.actor_user_id = None
        latest.source_kind = "task"
        db.commit()
    finally:
        db.close()

    got = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    ).json()
    assert got["stateVersion"] == new_ver
    assert got["currentRevisionActorUsername"] is None
    assert got["currentRevisionSourceKind"] is None
    rows = _db_rev_rows(pid)
    assert any(
        r.state_version == old_ver and r.actor_user_id == user_id for r in rows
    )


@pytest.mark.parametrize("role", ["finance", "hr", "bidder"])
def test_role_change_keeps_username(required_client: TestClient, role: str):
    """用途：活动成员角色变更仍显示当前用户名（角色不参与历史归因）。
    说明：会话用户保持 bid_writer 以便 HTTP 访问；目标 actor 改为 finance/hr/bidder。"""
    user_id, csrf, _ = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name=f"角色-{role}")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "r", "text": role}]},
    )
    assert put.status_code == 200, put.text
    target_name = f"角色用户_{role}"
    target = _insert_orphan_user(username=target_name, workspace_id=_WS, active=True)
    _set_latest_actor(pid, target)
    _set_member_role(target, role)
    res = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    )
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["currentRevisionActorUsername"] == target_name
    # 会话用户自身角色未变，确认非会话猜值
    assert got["currentRevisionActorUsername"] != _TEST_USERNAME


def test_current_username_after_rename(required_client: TestClient):
    """用途：直接更新用户名后返回新当前名；不做历史快照。"""
    user_id, csrf, _ = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="改名")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "rn", "text": "改名前"}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["currentRevisionActorUsername"] == _TEST_USERNAME

    new_name = "改名后用户_p13d2"
    _set_username(user_id, new_name)
    got = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    ).json()
    assert got["currentRevisionActorUsername"] == new_name


def test_source_and_actor_independent_degrade(required_client: TestClient):
    """用途：来源与用户名独立校验，单侧损坏不连带另一侧。"""
    user_id, csrf, username = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="独立降级")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "ind", "text": "独立"}]},
    )
    assert put.status_code == 200, put.text
    ver = _assert_sv(put.json()["stateVersion"])

    # 合法 actor + 非法来源
    with engine.connect() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = 1"))
        try:
            rid = (
                conn.execute(
                    text(
                        "SELECT id FROM editor_state_revisions "
                        "WHERE project_id = :pid ORDER BY created_at DESC, id DESC LIMIT 1"
                    ),
                    {"pid": pid},
                ).scalar()
            )
            conn.execute(
                text(
                    "UPDATE editor_state_revisions SET source_kind = :sk WHERE id = :id"
                ),
                {"sk": "not_a_real_source", "id": rid},
            )
            conn.commit()
        finally:
            conn.execute(text("PRAGMA ignore_check_constraints = 0"))
            assert int(conn.execute(text("PRAGMA ignore_check_constraints")).scalar()) == 0

    body1 = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    ).json()
    assert body1["stateVersion"] == ver
    assert body1["currentRevisionSourceKind"] is None
    assert body1["currentRevisionActorUsername"] == username

    # 恢复合法来源 + 坏 actor
    _set_latest_source(pid, "browser_put")
    _set_latest_actor(pid, None)
    body2 = required_client.get(
        f"/api/projects/{pid}/editor-state", headers=headers
    ).json()
    assert body2["currentRevisionSourceKind"] == "browser_put"
    assert body2["currentRevisionActorUsername"] is None


def test_sql_one_query_limit_one_no_sensitive(required_client: TestClient):
    """用途：GET 期间 editor_state_revisions SELECT 恰好 1 条；
    投影精确 5 列固定顺序；actor_user_id 仅两处 JOIN ON；无敏感列。"""
    user_id, csrf, username = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="SQL一次")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "sql", "text": "投影"}]},
    )
    assert put.status_code == 200, put.text
    ver = _assert_sv(put.json()["stateVersion"])

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" not in low:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = required_client.get(
            f"/api/projects/{pid}/editor-state", headers=headers
        )
        got = res.json()
        assert got["currentRevisionActorUsername"] == username
        assert got["currentRevisionSourceKind"] == "browser_put"
        assert got["stateVersion"] == ver
        _assert_no_actor_id_leak(res.text, user_id, _FAKE_ACTOR)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # GET 期间 editor_state_revisions SELECT 精确恰好 1 条
    assert len(captured) == 1, [s for s, _ in captured]
    statement, parameters = captured[0]
    low = " ".join(statement.lower().split())

    # 投影：精确 5 项，顺序固定 state_version/source_kind/username/user_is_active/member_is_active
    m = re.search(r"select\s+(.+?)\s+from\s+", low)
    assert m is not None, low
    proj = m.group(1)
    assert "select *" not in low
    # 按逗号拆投影项（本查询无函数逗号）
    proj_items = [p.strip() for p in proj.split(",")]
    assert len(proj_items) == 5, proj_items
    assert "state_version" in proj_items[0]
    assert "source_kind" in proj_items[1]
    assert "username" in proj_items[2]
    assert "user_is_active" in proj_items[3]
    assert "member_is_active" in proj_items[4]
    # actor_user_id 不得进入 projection；仅两个 JOIN ON 条件
    assert "actor_user_id" not in proj
    assert low.count("actor_user_id") == 2, low
    assert re.search(
        r"local_users\.id\s*=\s*editor_state_revisions\.actor_user_id", low
    ), low
    assert re.search(
        r"workspace_members\.user_id\s*=\s*editor_state_revisions\.actor_user_id",
        low,
    ), low
    # 精确同 workspace 成员 join
    assert re.search(
        r"workspace_members\.workspace_id\s*=\s*editor_state_revisions\.workspace_id",
        low,
    ), low
    assert "local_users" in low
    assert "workspace_members" in low

    # WHERE workspace+project
    assert re.search(
        r"editor_state_revisions\.workspace_id\s*=\s*\?", low
    ), low
    assert re.search(
        r"editor_state_revisions\.project_id\s*=\s*\?", low
    ), low
    # ORDER BY created_at DESC, id DESC
    assert re.search(
        r"order by\s+editor_state_revisions\.created_at\s+desc\s*,\s*"
        r"editor_state_revisions\.id\s+desc",
        low,
    ), low
    # LIMIT=1 / OFFSET=0 与绑定参数
    assert "limit ?" in low
    assert "offset ?" in low
    assert parameters is not None
    params = list(parameters)
    assert params == [_WS, pid, 1, 0], params

    # 无 snapshot/password/salt/hash/session/audit
    for banned in (
        "snapshot",
        "password",
        "salt",
        "hash",
        "session",
        "audit",
    ):
        assert banned not in low, banned


def test_resolver_zero_session_writes(required_client: TestClient):
    """用途：解析路径零 add/delete/flush/commit/rollback/refresh。"""
    user_id, csrf, username = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="零写")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "zw", "text": "零写"}]},
    )
    assert put.status_code == 200, put.text
    ver = put.json()["stateVersion"]

    hits: list[str] = []
    db = SessionLocal()
    try:
        orig_add = db.add
        orig_delete = db.delete
        orig_flush = db.flush
        orig_commit = db.commit
        orig_rollback = db.rollback
        orig_refresh = db.refresh

        def _hit(name):
            def _wrapped(*a, **k):
                hits.append(name)
                raise AssertionError(f"解析路径禁止 {name}")

            return _wrapped

        db.add = _hit("add")  # type: ignore[method-assign]
        db.delete = _hit("delete")  # type: ignore[method-assign]
        db.flush = _hit("flush")  # type: ignore[method-assign]
        db.commit = _hit("commit")  # type: ignore[method-assign]
        db.rollback = _hit("rollback")  # type: ignore[method-assign]
        db.refresh = _hit("refresh")  # type: ignore[method-assign]

        meta = editor_state_revision_service.resolve_current_revision_meta(
            db, _WS, pid, ver
        )
        assert meta.actor_username == username
        assert meta.source_kind == "browser_put"
        # 兼容入口不得二次查询写副作用
        kind = editor_state_revision_service.resolve_current_revision_source_kind(
            db, _WS, pid, ver
        )
        assert kind == "browser_put"
        assert hits == []

        # 恢复
        db.add = orig_add  # type: ignore[method-assign]
        db.delete = orig_delete  # type: ignore[method-assign]
        db.flush = orig_flush  # type: ignore[method-assign]
        db.commit = orig_commit  # type: ignore[method-assign]
        db.rollback = orig_rollback  # type: ignore[method-assign]
        db.refresh = orig_refresh  # type: ignore[method-assign]
    finally:
        db.close()


def test_disabled_mode_actor_username_null(client: TestClient):
    """用途：disabled 模式 actor 账本为空时字段仍必出且为 null。"""
    pid = _create_project(client, name="disabled-null")
    put = _put(client, pid, {"facts": [{"id": "d", "text": "disabled"}]})
    body = put.json()
    assert "currentRevisionActorUsername" in body
    assert body["currentRevisionActorUsername"] is None
    got = _get(client, pid).json()
    assert got["currentRevisionActorUsername"] is None
    assert got["currentRevisionSourceKind"] == "browser_put"


def test_chinese_username_passthrough(required_client: TestClient):
    """用途：合法中文用户名原样返回，不 trim/normalize。"""
    user_id, csrf, _ = _bootstrap_and_login(
        required_client, username="中文用户甲", password=_TEST_PASSWORD
    )
    headers = {"X-CSRF-Token": csrf}
    pid = _create_project_required(required_client, headers, name="中文名")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"facts": [{"id": "cn", "text": "中文"}]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["currentRevisionActorUsername"] == "中文用户甲"
