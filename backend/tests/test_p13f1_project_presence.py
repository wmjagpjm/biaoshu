"""
模块：P13-F1 项目在线租约后端专项测试
用途：真实 HTTP/DB/并发验收 presence heartbeat/leave；failure-first 禁止假绿。
对接：POST /api/projects/{projectId}/presence/heartbeat|leave；
  project_presence_service；project_presence_leases 表。
二次开发：禁止源码字符串/hasattr/预插入恒真；不得改 deps 与认证中间件。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import LocalUserRow, Project, Workspace, WorkspaceMemberRow
from app.services import auth_service, project_service


# 固定合成口令：仅测试夹具
_TEST_PASSWORD = "TestPass-P13F1-Admin-0001!"
_WRITER_PASSWORD = "TestPass-P13F1-Writer-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-P13F1-Finance!",
    "hr": "TestPass-P13F1-Hr!",
    "bidder": "TestPass-P13F1-Bidder!",
    "bid_writer": _WRITER_PASSWORD,
}
_ADMIN_USER = "admin_p13f1"
_WRITER_USER = "writer_p13f1"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_HEARTBEAT_KEYS = {"leaseExpiresAt", "refreshAfterSeconds", "members", "truncated"}
_MEMBER_KEYS = {"username", "isSelf"}
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _WRITER_PASSWORD,
    *_ROLE_PASSWORDS.values(),
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
    "client_digest",
    "clientId",
    "leaseId",
    "userId",
    "memberId",
    "session",
    "Set-Cookie",
    "csrfToken",
)


def _assert_no_secrets(payload: object) -> None:
    """用途：响应不得回显口令、摘要、内部 ID 或 CSRF。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text, f"敏感标记泄漏: {marker}"


def _client_id(n: int = 24) -> str:
    """用途：生成合法 clientId（[A-Za-z0-9_-]{22..64}）。"""
    raw = secrets.token_urlsafe(48)
    cid = "".join(ch for ch in raw if ch.isalnum() or ch in "_-")[:n]
    if len(cid) < n:
        cid = (cid + "A" * n)[:n]
    assert _CLIENT_RE.fullmatch(cid)
    return cid


def _digest(client_id: str) -> str:
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()


@pytest.fixture
def required_settings(monkeypatch):
    """用途：切换 AUTH_MODE=required。"""
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    """用途：required 模式 TestClient。"""
    with TestClient(app) as client:
        yield client


def _bootstrap(
    username: str = _ADMIN_USER,
    password: str = _TEST_PASSWORD,
    *,
    role: str = auth_service.ROLE_BID_WRITER,
):
    db = SessionLocal()
    try:
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=username,
            password=password,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str):
    client.cookies.clear()
    res = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _csrf(body: dict) -> str:
    return body["csrfToken"]


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
    is_owner: bool = False,
):
    res = client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_project(client: TestClient, csrf: str, name: str = "P13F1 项目") -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _hb_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/presence/heartbeat"


def _leave_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/presence/leave"


def _heartbeat(
    client: TestClient,
    csrf: str,
    project_id: str,
    client_id: str,
    *,
    headers: dict[str, str] | None = None,
):
    hdrs = {"X-CSRF-Token": csrf}
    if headers:
        hdrs.update(headers)
    return client.post(
        _hb_url(project_id),
        json={"clientId": client_id},
        headers=hdrs,
    )


def _leave(
    client: TestClient,
    csrf: str,
    project_id: str,
    client_id: str,
    *,
    headers: dict[str, str] | None = None,
):
    hdrs = {"X-CSRF-Token": csrf}
    if headers:
        hdrs.update(headers)
    return client.post(
        _leave_url(project_id),
        json={"clientId": client_id},
        headers=hdrs,
    )


def _assert_no_store(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store"


def _assert_heartbeat_shape(body: dict, *, expect_self: str | None = None) -> None:
    assert set(body.keys()) == _HEARTBEAT_KEYS
    assert body["refreshAfterSeconds"] == 15
    assert isinstance(body["truncated"], bool)
    assert isinstance(body["members"], list)
    assert isinstance(body["leaseExpiresAt"], str)
    # 租约约 45 秒（允许少量时钟误差）
    expires = datetime.fromisoformat(body["leaseExpiresAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = (expires - now).total_seconds()
    assert 40 <= delta <= 50, delta
    for m in body["members"]:
        assert set(m.keys()) == _MEMBER_KEYS
        assert isinstance(m["username"], str)
        assert isinstance(m["isSelf"], bool)
    if expect_self is not None:
        selves = [m for m in body["members"] if m["isSelf"] is True]
        assert len(selves) == 1
        assert selves[0]["username"] == expect_self
    _assert_no_secrets(body)


def _seed_second_workspace_project(
    *,
    workspace_id: str,
    workspace_name: str,
    owner_user_id: str,
    project_name: str = "跨空间项目",
) -> str:
    """用途：直接入库第二空间与项目。"""
    db = SessionLocal()
    try:
        if db.get(Workspace, workspace_id) is None:
            db.add(
                Workspace(
                    id=workspace_id,
                    name=workspace_name,
                    owner_user_id=owner_user_id,
                )
            )
        project = project_service.create_project(
            db,
            workspace_id,
            name=project_name,
        )
        db.commit()
        return project.id
    finally:
        db.close()


def _add_member_to_workspace(
    *,
    workspace_id: str,
    user_id: str,
    role: str = auth_service.ROLE_BID_WRITER,
    is_owner: bool = False,
) -> None:
    db = SessionLocal()
    try:
        mid = f"wm_{secrets.token_hex(8)}"
        db.add(
            WorkspaceMemberRow(
                id=mid,
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
                is_owner=is_owner,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()


def _switch_active(client: TestClient, csrf: str, workspace_id: str) -> str:
    res = client.put(
        "/api/auth/active-workspace",
        json={"workspaceId": workspace_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    # 活动空间切换后 CSRF 可能轮换；login 再取或用 resume
    resumed = client.get("/api/auth/csrf")
    assert resumed.status_code == 200, resumed.text
    return resumed.json()["csrfToken"]


# ---------- failure-first / 成功 shape ----------


def test_heartbeat_success_exact_shape_and_no_store(required_client):
    """用途：成功 heartbeat 精确四键、45/15、no-store、自身成员。"""
    principal = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    res = _heartbeat(required_client, csrf, pid, cid)
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    data = res.json()
    _assert_heartbeat_shape(data, expect_self=principal.username)
    assert data["truncated"] is False


def test_leave_success_204_empty_body_no_store(required_client):
    """用途：leave 成功 204 空 body + no-store，且幂等。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid).status_code == 200
    leave1 = _leave(required_client, csrf, pid, cid)
    assert leave1.status_code == 204, leave1.text
    assert leave1.content == b""
    _assert_no_store(leave1)
    leave2 = _leave(required_client, csrf, pid, cid)
    assert leave2.status_code == 204, leave2.text
    assert leave2.content == b""


def test_same_client_renew_no_duplicate_row(required_client):
    """用途：同 client 续租不新增行，仅刷新过期时间。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    r1 = _heartbeat(required_client, csrf, pid, cid)
    assert r1.status_code == 200, r1.text
    exp1 = r1.json()["leaseExpiresAt"]
    r2 = _heartbeat(required_client, csrf, pid, cid)
    assert r2.status_code == 200, r2.text
    exp2 = r2.json()["leaseExpiresAt"]
    assert exp2 >= exp1
    digest = _digest(cid)
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT client_digest FROM project_presence_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == digest
        # 明文 clientId 不得落库
        raw = db.execute(
            text("SELECT * FROM project_presence_leases WHERE project_id = :pid"),
            {"pid": pid},
        ).mappings().all()
        blob = json.dumps([dict(x) for x in raw], default=str)
        assert cid not in blob
    finally:
        db.close()


def test_two_users_visible_and_is_self(required_client):
    """用途：两用户互相可见；isSelf 仅标记自身。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    pid = _create_project(required_client, csrf, "双用户项目")
    cid_a = _client_id()
    ra = _heartbeat(required_client, csrf, pid, cid_a)
    assert ra.status_code == 200, ra.text
    names_a = {m["username"] for m in ra.json()["members"]}
    assert admin.username in names_a

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    cid_w = _client_id()
    rw = _heartbeat(required_client, csrf_w, pid, cid_w)
    assert rw.status_code == 200, rw.text
    members = rw.json()["members"]
    by_name = {m["username"]: m for m in members}
    assert admin.username in by_name
    assert _WRITER_USER in by_name
    assert by_name[_WRITER_USER]["isSelf"] is True
    assert by_name[admin.username]["isSelf"] is False


def test_multi_client_same_user_aggregated(required_client):
    """用途：同用户多 client 只输出一次成员。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    c1, c2 = _client_id(), _client_id(28)
    assert _heartbeat(required_client, csrf, pid, c1).status_code == 200
    r = _heartbeat(required_client, csrf, pid, c2)
    assert r.status_code == 200, r.text
    names = [m["username"] for m in r.json()["members"]]
    assert names.count(admin.username) == 1
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar()
        assert n == 2
    finally:
        db.close()


def test_client_limit_eight_existing_renew_new_429(required_client):
    """用途：每用户每项目最多 8 活动 client；已有可续租，新 client 429 零新增。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ids = [_client_id(22 + (i % 5)) for i in range(8)]
    # 保证唯一
    ids = list(dict.fromkeys(ids))
    while len(ids) < 8:
        ids.append(_client_id(30 + len(ids)))
    ids = ids[:8]
    for cid in ids:
        res = _heartbeat(required_client, csrf, pid, cid)
        assert res.status_code == 200, res.text
    # 已有续租
    renew = _heartbeat(required_client, csrf, pid, ids[0])
    assert renew.status_code == 200, renew.text
    # 新 client 429
    new_cid = _client_id(32)
    limited = _heartbeat(required_client, csrf, pid, new_cid)
    assert limited.status_code == 429, limited.text
    detail = limited.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "presence_client_limit"
    _assert_no_secrets(limited.json())
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar()
        assert n == 8
        digests = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT client_digest FROM project_presence_leases "
                    "WHERE project_id = :pid"
                ),
                {"pid": pid},
            ).fetchall()
        }
        assert _digest(new_cid) not in digests
    finally:
        db.close()


def test_concurrent_same_client_no_duplicate_or_500(required_client):
    """
    用途：同 client 首次真并发 heartbeat 全 200、最终一行、无 500。
    证据：Barrier 起跑门保证 8 路几乎同时首次插入，禁止仅靠 ThreadPool 假并发。
    """
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    workers = 8
    barrier = threading.Barrier(workers, timeout=15)

    def worker() -> int:
        with TestClient(app) as c:
            for k, v in required_client.cookies.items():
                c.cookies.set(k, v)
            barrier.wait()
            res = c.post(
                _hb_url(pid),
                json={"clientId": cid},
                headers={"X-CSRF-Token": csrf},
            )
            # 响应不得 500，且不得回显 clientId
            assert cid not in res.text
            assert res.status_code != 500, res.text
            return res.status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(worker) for _ in range(workers)]
        codes = [f.result() for f in as_completed(futs)]
    assert all(c == 200 for c in codes), codes
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE project_id = :pid AND client_digest = :d"
            ),
            {"pid": pid, "d": _digest(cid)},
        ).scalar()
        assert n == 1
    finally:
        db.close()


def test_concurrent_seven_plus_two_client_limit_boundary(required_client):
    """
    用途：已有 7 条时两个不同新 client 真并发：恰 1 个 200、1 个 429、最终 8、无 500。
    证据：Barrier 起跑门；禁止只靠 ThreadPool 无同步。
    """
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    existing: list[str] = []
    while len(existing) < 7:
        cid = _client_id(24 + len(existing) % 5)
        if cid in existing:
            continue
        res = _heartbeat(required_client, csrf, pid, cid)
        assert res.status_code == 200, res.text
        existing.append(cid)

    new_a = _client_id(32)
    new_b = _client_id(34)
    assert new_a != new_b
    assert new_a not in existing and new_b not in existing
    barrier = threading.Barrier(2, timeout=15)
    payload_ids = [new_a, new_b]

    def worker(cid: str) -> int:
        with TestClient(app) as c:
            for k, v in required_client.cookies.items():
                c.cookies.set(k, v)
            barrier.wait()
            res = c.post(
                _hb_url(pid),
                json={"clientId": cid},
                headers={"X-CSRF-Token": csrf},
            )
            assert cid not in res.text
            assert res.status_code != 500, res.text
            return res.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker, cid) for cid in payload_ids]
        codes = [f.result() for f in as_completed(futs)]
    assert sorted(codes) == [200, 429], codes
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar()
        assert n == 8
        digests = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT client_digest FROM project_presence_leases "
                    "WHERE project_id = :pid"
                ),
                {"pid": pid},
            ).fetchall()
        }
        # 恰好一个新 client 进入
        entered = sum(1 for c in (new_a, new_b) if _digest(c) in digests)
        assert entered == 1
    finally:
        db.close()


def test_expired_filtered_and_cleaned_on_heartbeat(required_client):
    """用途：过期租约不进快照，心跳机会性清理。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    live = _client_id()
    assert _heartbeat(required_client, csrf, pid, live).status_code == 200

    expired_cid = _client_id(26)
    digest = _digest(expired_cid)
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    db = SessionLocal()
    try:
        # 直接插入过期行（表须存在）
        db.execute(
            text(
                "INSERT INTO project_presence_leases "
                "(id, workspace_id, project_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"ppl_{secrets.token_hex(8)}",
                "ws": admin.active_workspace_id or "ws_local",
                "pid": pid,
                "uid": admin.user_id,
                "dig": digest,
                "ls": past.isoformat(),
                "ex": past.isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()

    r = _heartbeat(required_client, csrf, pid, live)
    assert r.status_code == 200, r.text
    # 自身仅出现一次
    names = [m["username"] for m in r.json()["members"]]
    assert names.count(admin.username) == 1
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE client_digest = :d"
            ),
            {"d": digest},
        ).scalar()
        assert n == 0
    finally:
        db.close()


def test_leave_isolates_other_clients_users_projects(required_client):
    """用途：leave 仅删当前 actor+项目+摘要，不误删其它。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    p1 = _create_project(required_client, csrf, "项目一")
    p2 = _create_project(required_client, csrf, "项目二")
    c_keep = _client_id()
    c_drop = _client_id(28)
    c_p2 = _client_id(30)
    assert _heartbeat(required_client, csrf, p1, c_keep).status_code == 200
    assert _heartbeat(required_client, csrf, p1, c_drop).status_code == 200
    assert _heartbeat(required_client, csrf, p2, c_p2).status_code == 200

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    c_w = _client_id(32)
    assert _heartbeat(required_client, csrf_w, p1, c_w).status_code == 200

    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    leave = _leave(required_client, csrf_a, p1, c_drop)
    assert leave.status_code == 204, leave.text

    db = SessionLocal()
    try:
        digests = {
            r[0]
            for r in db.execute(
                text("SELECT client_digest FROM project_presence_leases")
            ).fetchall()
        }
        assert _digest(c_drop) not in digests
        assert _digest(c_keep) in digests
        assert _digest(c_p2) in digests
        assert _digest(c_w) in digests
    finally:
        db.close()


# ---------- 鉴权 / 作用域 / CSRF / 角色 ----------


def test_csrf_required_for_heartbeat_and_leave(required_client):
    """用途：缺/错 CSRF 固定 403，零写。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    no = required_client.post(_hb_url(pid), json={"clientId": cid})
    assert no.status_code == 403, no.text
    assert no.json()["detail"]["code"] == "csrf_invalid"
    bad = required_client.post(
        _hb_url(pid),
        json={"clientId": cid},
        headers={"X-CSRF-Token": "wrong-csrf-token-value"},
    )
    assert bad.status_code == 403, bad.text
    leave_no = required_client.post(_leave_url(pid), json={"clientId": cid})
    assert leave_no.status_code == 403, leave_no.text
    db = SessionLocal()
    try:
        # 表可能尚不存在（failure-first）；存在则计数为 0
        insp = inspect(engine)
        if "project_presence_leases" in insp.get_table_names():
            n = db.execute(
                text("SELECT COUNT(*) FROM project_presence_leases")
            ).scalar()
            assert n == 0
    finally:
        db.close()


def test_x_workspace_id_any_value_rejected(required_client):
    """用途：任何 X-Workspace-Id（含空）拒绝 presence。"""
    principal = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    for val in ("ws_local", "", "ws_other"):
        res = _heartbeat(
            required_client,
            csrf,
            pid,
            cid,
            headers={"X-Workspace-Id": val},
        )
        assert res.status_code in (403, 400), (val, res.status_code, res.text)
        # 不得成功写租约
        assert res.status_code != 200
    # 合法无头仍可用
    ok = _heartbeat(required_client, csrf, pid, cid)
    assert ok.status_code == 200, ok.text


def test_role_matrix_and_owner_not_bypass(required_client):
    """用途：非 bid_writer / disabled 成员拒绝；owner 不替代角色。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    # 各角色成员
    for role, pwd in _ROLE_PASSWORDS.items():
        if role == "bid_writer":
            continue
        # 每次以 admin 会话创建成员，避免角色登录后 CSRF 串扰
        body_admin = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
        csrf_admin = _csrf(body_admin)
        uname = f"role_{role}_p13f1"
        _create_member(
            required_client,
            csrf_admin,
            username=uname,
            password=pwd,
            role=role,
            is_owner=(role == "finance"),
        )
        b = _login(required_client, uname, pwd)
        c = _csrf(b)
        res = _heartbeat(required_client, c, pid, _client_id())
        assert res.status_code == 403, (role, res.text)
        detail = res.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "role_forbidden"

    # bid_writer 可用
    body_admin = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_admin = _csrf(body_admin)
    _create_member(
        required_client,
        csrf_admin,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    ok = _heartbeat(required_client, csrf_w, pid, _client_id())
    assert ok.status_code == 200, ok.text


def test_cross_workspace_and_missing_project_404(required_client):
    """用途：跨空间/不存在项目统一 404，不泄漏。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf, "本空间项目")
    other_pid = _seed_second_workspace_project(
        workspace_id="ws_p13f1_other",
        workspace_name="其它空间",
        owner_user_id=admin.user_id,
    )
    # 跨空间项目
    cross = _heartbeat(required_client, csrf, other_pid, _client_id())
    assert cross.status_code == 404, cross.text
    _assert_no_secrets(cross.text)
    assert "ws_p13f1_other" not in cross.text
    # 不存在
    missing = _heartbeat(required_client, csrf, "proj_missing_p13f1", _client_id())
    assert missing.status_code == 404, missing.text
    # leave 同样 404 或幂等——契约：项目不存在统一 404
    leave_missing = _leave(required_client, csrf, "proj_missing_p13f1", _client_id())
    assert leave_missing.status_code == 404, leave_missing.text


def test_unauthenticated_rejected(required_client):
    """用途：无会话拒绝。"""
    _bootstrap()
    required_client.cookies.clear()
    res = required_client.post(
        _hb_url("any_project"),
        json={"clientId": _client_id()},
        headers={"X-CSRF-Token": "x"},
    )
    assert res.status_code in (401, 403), res.text


# ---------- clientId 校验矩阵 ----------


def _forbidden_echo_fragments(payload: dict[str, Any]) -> list[str]:
    """用途：从非法 payload 提取必须不得出现在 422 响应中的原文片段。"""
    frags: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str) and value:
            frags.append(value)
            # 空白包裹时原文与 strip 后核心均不得回显
            stripped = value.strip()
            if stripped and stripped != value:
                frags.append(stripped)
        elif isinstance(value, (int, float)):
            frags.append(str(value))
        elif isinstance(value, list):
            frags.extend(str(x) for x in value)
        elif value is not None and not isinstance(value, (dict, bool)):
            frags.append(str(value))
        # 额外键名也不得作为 input 结构泄漏（框架默认 loc 含键名时可接受 code 级固定文案）
        if key not in ("clientId",) and key:
            # 额外值若为简单标量已收集；键名 snake_case 场景保留检测 client_id 原文路径
            pass
    # snake_case 别名中的合法长度串
    if "client_id" in payload and isinstance(payload["client_id"], str):
        frags.append(payload["client_id"])
    return [f for f in frags if f]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"clientId": "short"},
        {"clientId": "a" * 21},
        {"clientId": "a" * 65},
        {"clientId": "bad client id!!!!!!!!!!!!!!!"},
        {"clientId": "  " + "a" * 22},
        {"clientId": "a" * 22 + " "},
        {"clientId": "   "},
        {"clientId": "\t\n"},
        {"clientId": "a" * 22, "extra": 1},
        {"clientId": "a" * 24, "extraKey": "LEAK_EXTRA_P13F1"},
        {"client_id": "a" * 24},
        {"clientId": 123456789012345678901234},
        {"clientId": None},
        {"clientId": ["a" * 24]},
        {"clientId": {"nested": "x" * 24}},
    ],
)
def test_client_id_validation_matrix(required_client, payload):
    """
    用途：clientId 短/长/空白/非法字符/snake/extra/类型等固定 422；
    真实 HTTP 响应不得包含原始 clientId 或额外值（禁止默认 input 回显）。
    """
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    forbidden = _forbidden_echo_fragments(payload)
    # 固定额外标记
    if "LEAK_EXTRA_P13F1" not in forbidden:
        if any(v == "LEAK_EXTRA_P13F1" for v in payload.values()):
            forbidden.append("LEAK_EXTRA_P13F1")

    res = required_client.post(
        _hb_url(pid),
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422, (payload, res.status_code, res.text)
    for frag in forbidden:
        assert frag not in res.text, (frag, res.text)
    # 框架默认 422 常含 "input"；脱敏实现不得带 input 字段
    if res.headers.get("content-type", "").startswith("application/json"):
        try:
            body_json = res.json()
        except Exception:
            body_json = None
        if body_json is not None:
            dumped = json.dumps(body_json, ensure_ascii=False)
            assert "input" not in dumped
            _assert_no_secrets(body_json)

    leave = required_client.post(
        _leave_url(pid),
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert leave.status_code == 422, (payload, leave.status_code, leave.text)
    for frag in forbidden:
        assert frag not in leave.text, (frag, leave.text)


def test_client_id_boundary_22_and_64_accepted(required_client):
    """用途：22 与 64 位合法 clientId 接受。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    for n in (22, 64):
        cid = _client_id(n)
        assert len(cid) == n
        res = _heartbeat(required_client, csrf, pid, cid)
        assert res.status_code == 200, (n, res.text)


# ---------- 安全用户名 / 截断 / 敏感字段 ----------


def test_unsafe_username_hidden_from_snapshot(required_client):
    """用途：坏用户名整用户隐藏，不回显占位。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    pid = _create_project(required_client, csrf)
    assert _heartbeat(required_client, csrf, pid, _client_id()).status_code == 200

    # writer 先建立租约，再污染展示用户名（保留 username_normalized 以便登录）
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    assert _heartbeat(required_client, csrf_w, pid, _client_id()).status_code == 200

    db = SessionLocal()
    try:
        user = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username_normalized == _WRITER_USER.lower())
            .one()
        )
        user.username = " bad\nname "
        db.commit()
    finally:
        db.close()

    # admin 快照看坏名应被隐藏
    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    r = _heartbeat(required_client, csrf_a, pid, _client_id())
    assert r.status_code == 200, r.text
    names = [m["username"] for m in r.json()["members"]]
    assert " bad\nname " not in names
    assert "bad\nname" not in names
    assert _WRITER_USER not in names
    assert admin.username in names
    _assert_no_secrets(r.json())


def test_truncated_when_over_50_members(required_client, monkeypatch):
    """用途：候选超 50 时 truncated=true，最多 50，自身优先。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    live = _client_id()
    assert _heartbeat(required_client, csrf, pid, live).status_code == 200

    # 直接插入 55 个不同用户的活动租约（跳过 HTTP 建 55 用户的成本）
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=45)
    ws = admin.active_workspace_id or "ws_local"
    db = SessionLocal()
    try:
        for i in range(55):
            uid = f"user_trunc_{i:03d}"
            uname = f"user_trunc_{i:03d}"
            db.add(
                LocalUserRow(
                    id=uid,
                    username=uname,
                    username_normalized=uname.lower(),
                    password_salt="s" * 32,
                    password_hash="h" * 64,
                    is_active=True,
                )
            )
            db.add(
                WorkspaceMemberRow(
                    id=f"wm_trunc_{i:03d}",
                    workspace_id=ws,
                    user_id=uid,
                    role=auth_service.ROLE_BID_WRITER,
                    is_owner=False,
                    is_active=True,
                )
            )
        db.flush()
        for i in range(55):
            uid = f"user_trunc_{i:03d}"
            db.execute(
                text(
                    "INSERT INTO project_presence_leases "
                    "(id, workspace_id, project_id, user_id, client_digest, "
                    "last_seen_at, expires_at) "
                    "VALUES (:id, :ws, :pid, :uid, :dig, :ls, :ex)"
                ),
                {
                    "id": f"ppl_trunc_{i:03d}",
                    "ws": ws,
                    "pid": pid,
                    "uid": uid,
                    "dig": hashlib.sha256(f"cid_trunc_{i}".encode()).hexdigest(),
                    "ls": now.isoformat(),
                    "ex": exp.isoformat(),
                },
            )
        db.commit()
    finally:
        db.close()

    r = _heartbeat(required_client, csrf, pid, live)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["truncated"] is True
    assert len(data["members"]) == 50
    # 自身优先
    assert data["members"][0]["isSelf"] is True
    assert data["members"][0]["username"] == admin.username


def test_zero_sensitive_fields_in_success_and_errors(required_client):
    """用途：成功/错误响应零敏感内部字段。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()
    ok = _heartbeat(required_client, csrf, pid, cid)
    assert ok.status_code == 200, ok.text
    _assert_no_secrets(ok.json())
    text_all = ok.text + json.dumps(ok.json())
    for bad in (
        "client_digest",
        "leaseId",
        "user_id",
        "userId",
        "password",
        "token_digest",
        "csrf_digest",
        "last_seen_at",
        "lastSeenAt",
    ):
        assert bad not in text_all


# ---------- 表约束 / 索引 / 级联 / rollback ----------


def test_table_constraints_indexes_and_cascade(required_client):
    """用途：表唯一/索引/项目与用户级联删除。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf, "级联项目")
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid).status_code == 200

    insp = inspect(engine)
    assert "project_presence_leases" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("project_presence_leases")}
    for required in {
        "id",
        "workspace_id",
        "project_id",
        "user_id",
        "client_digest",
        "last_seen_at",
        "expires_at",
    }:
        assert required in cols
    # 唯一约束：精确四元组，禁止 set 超集假绿
    expected_uq = ("workspace_id", "project_id", "user_id", "client_digest")
    uqs = insp.get_unique_constraints("project_presence_leases")
    uk_cols = {tuple(u["column_names"]) for u in uqs}
    idxs = insp.get_indexes("project_presence_leases")
    unique_idx_cols = {
        tuple(i["column_names"])
        for i in idxs
        if i.get("unique")
    }
    combined_exact = uk_cols | unique_idx_cols
    assert expected_uq in combined_exact, (uk_cols, unique_idx_cols)
    # 两个复合索引均须精确存在（项目过期 + 用户活动计数）
    flat_idx = [tuple(i["column_names"]) for i in idxs]
    assert (
        "workspace_id",
        "project_id",
        "expires_at",
    ) in flat_idx or any(
        list(cols_t) == ["workspace_id", "project_id", "expires_at"]
        for cols_t in flat_idx
    ), flat_idx
    assert (
        "workspace_id",
        "project_id",
        "user_id",
        "expires_at",
    ) in flat_idx or any(
        list(cols_t) == ["workspace_id", "project_id", "user_id", "expires_at"]
        for cols_t in flat_idx
    ), flat_idx

    # 项目级联
    del_p = required_client.delete(
        f"/api/projects/{pid}",
        headers={"X-CSRF-Token": csrf},
    )
    assert del_p.status_code == 204, del_p.text
    db = SessionLocal()
    try:
        n = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).scalar()
        assert n == 0
    finally:
        db.close()

    # 用户级联：新建项目+成员租约后删用户
    pid2 = _create_project(required_client, csrf, "用户级联项目")
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    assert _heartbeat(required_client, csrf_w, pid2, _client_id()).status_code == 200
    db = SessionLocal()
    try:
        uid = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _WRITER_USER)
            .one()
            .id
        )
        # 先清会话/成员依赖后删用户（ON DELETE CASCADE 租约）
        db.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": uid})
        db.execute(text("DELETE FROM workspace_members WHERE user_id = :u"), {"u": uid})
        db.execute(text("DELETE FROM local_users WHERE id = :u"), {"u": uid})
        db.commit()
        n2 = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases WHERE user_id = :u"
            ),
            {"u": uid},
        ).scalar()
        assert n2 == 0
    finally:
        db.close()


def test_db_failure_rollbacks_without_partial_write(required_client, monkeypatch):
    """
    用途：新租约已 flush 后快照失败 → 新租约与机会清理均回滚；
    leave 已删除后 commit 失败 → 原租约仍在。禁止入口零写入前 boom 冒充。
    """
    import importlib

    import app.api.project_presence as presence_api
    import app.services.project_presence_service as presence_svc

    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    live_cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, live_cid).status_code == 200

    # 插入一条过期租约，供机会清理路径在同一事务内删除
    expired_cid = _client_id(26)
    digest_expired = _digest(expired_cid)
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    admin_id: str
    ws_id: str
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT user_id, workspace_id FROM project_presence_leases "
                "WHERE project_id = :pid LIMIT 1"
            ),
            {"pid": pid},
        ).fetchone()
        assert row is not None
        admin_id, ws_id = str(row[0]), str(row[1])
        db.execute(
            text(
                "INSERT INTO project_presence_leases "
                "(id, workspace_id, project_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"ppl_{secrets.token_hex(8)}",
                "ws": ws_id,
                "pid": pid,
                "uid": admin_id,
                "dig": digest_expired,
                "ls": past.isoformat(),
                "ex": past.isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()

    new_cid = _client_id(30)
    real_snapshot = presence_svc._build_snapshot

    def boom_after_flush(*args, **kwargs):
        # 此时 upsert/清理应已 flush；随后失败触发路由 rollback
        raise RuntimeError("simulated_snapshot_failure_p13f1")

    monkeypatch.setattr(presence_svc, "_build_snapshot", boom_after_flush)
    res = _heartbeat(required_client, csrf, pid, new_cid)
    assert res.status_code == 500, res.text
    detail = res.json().get("detail")
    assert isinstance(detail, dict)
    dumped = json.dumps(detail, ensure_ascii=False)
    assert "simulated_snapshot_failure" not in dumped
    assert "RuntimeError" not in res.text
    _assert_no_secrets(res.json())

    db = SessionLocal()
    try:
        digests = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT client_digest FROM project_presence_leases "
                    "WHERE project_id = :pid"
                ),
                {"pid": pid},
            ).fetchall()
        }
        # 新租约未提交
        assert _digest(new_cid) not in digests
        # 机会清理回滚：过期行仍在
        assert digest_expired in digests
        # 原 live 租约仍在
        assert _digest(live_cid) in digests
    finally:
        db.close()

    monkeypatch.setattr(presence_svc, "_build_snapshot", real_snapshot)

    # leave：删除已 flush 后 commit 失败 → 原租约仍在
    leave_cid = _client_id(28)
    assert _heartbeat(required_client, csrf, pid, leave_cid).status_code == 200
    real_leave = presence_api.leave_presence

    def leave_then_poison_commit(db_sess, **kwargs):
        real_leave(db=db_sess, **kwargs)

        def boom_commit() -> None:
            raise RuntimeError("simulated_commit_failure_leave_p13f1")

        db_sess.commit = boom_commit  # type: ignore[method-assign]

    monkeypatch.setattr(presence_api, "leave_presence", leave_then_poison_commit)
    leave_res = _leave(required_client, csrf, pid, leave_cid)
    assert leave_res.status_code == 500, leave_res.text
    leave_detail = leave_res.json().get("detail")
    assert isinstance(leave_detail, dict)
    assert "simulated_commit_failure" not in json.dumps(leave_detail)
    assert "RuntimeError" not in leave_res.text

    db = SessionLocal()
    try:
        n_leave = db.execute(
            text(
                "SELECT COUNT(*) FROM project_presence_leases "
                "WHERE project_id = :pid AND client_digest = :d"
            ),
            {"pid": pid, "d": _digest(leave_cid)},
        ).scalar()
        assert n_leave == 1
    finally:
        db.close()


def test_disabled_member_or_user_converges(required_client):
    """用途：停用成员、停用用户、角色改出 bid_writer 后立即从快照消失。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    writer2 = "writer2_p13f1"
    writer2_pwd = "TestPass-P13F1-Writer-0002!"
    _create_member(
        required_client,
        csrf,
        username=writer2,
        password=writer2_pwd,
        role=auth_service.ROLE_BID_WRITER,
    )
    writer3 = "writer3_p13f1"
    writer3_pwd = "TestPass-P13F1-Writer-0003!"
    _create_member(
        required_client,
        csrf,
        username=writer3,
        password=writer3_pwd,
        role=auth_service.ROLE_BID_WRITER,
    )
    pid = _create_project(required_client, csrf)

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    assert _heartbeat(required_client, csrf_w, pid, _client_id()).status_code == 200

    body_w2 = _login(required_client, writer2, writer2_pwd)
    csrf_w2 = _csrf(body_w2)
    assert _heartbeat(required_client, csrf_w2, pid, _client_id()).status_code == 200

    body_w3 = _login(required_client, writer3, writer3_pwd)
    csrf_w3 = _csrf(body_w3)
    assert _heartbeat(required_client, csrf_w3, pid, _client_id()).status_code == 200

    db = SessionLocal()
    try:
        # 1) 仅停用成员
        m = (
            db.query(WorkspaceMemberRow)
            .filter(WorkspaceMemberRow.user_id == body_w["user"]["id"])
            .one()
        )
        m.is_active = False
        # 2) LocalUserRow.is_active=False（用户停用）
        u2 = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.id == body_w2["user"]["id"])
            .one()
        )
        u2.is_active = False
        # 3) 已有租约后 role 改出 bid_writer
        m3 = (
            db.query(WorkspaceMemberRow)
            .filter(WorkspaceMemberRow.user_id == body_w3["user"]["id"])
            .one()
        )
        m3.role = auth_service.ROLE_BIDDER
        db.commit()
    finally:
        db.close()

    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    r = _heartbeat(required_client, csrf_a, pid, _client_id())
    assert r.status_code == 200, r.text
    names = [m["username"] for m in r.json()["members"]]
    assert _WRITER_USER not in names
    assert writer2 not in names
    assert writer3 not in names
    assert admin.username in names


def test_no_get_sse_or_query_endpoints(required_client):
    """用途：不新增 GET/查询 presence 接口。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    get_res = required_client.get(f"/api/projects/{pid}/presence")
    assert get_res.status_code in (404, 405)
    get_hb = required_client.get(_hb_url(pid))
    assert get_hb.status_code in (404, 405)


# ---------- 建议5：租约时钟在写串行化之后取样 ----------


def test_heartbeat_samples_now_only_after_write_serialization(
    required_client, monkeypatch
):
    """
    用途：failure-first 证明 heartbeat 必须先取得写串行化，再调用唯一 _utc_now。
    证据：可控时钟 + 可控 _acquire_presence_write_serialization；禁止真实 sleep。
    通过真实 HTTP/DB；禁止源码字符串/hasattr/签名检查。
    """
    import app.services.project_presence_service as presence_svc

    t0 = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    clock = {"t": t0}
    events: list[str] = []

    def fake_now() -> datetime:
        events.append("now")
        return clock["t"]

    real_acquire = presence_svc._acquire_presence_write_serialization

    def delayed_acquire(db) -> None:
        events.append("acquire")
        # 模拟写锁等待：推进 5 秒可控时钟，禁止真实 sleep
        clock["t"] = clock["t"] + timedelta(seconds=5)
        real_acquire(db)

    monkeypatch.setattr(presence_svc, "_utc_now", fake_now)
    monkeypatch.setattr(
        presence_svc, "_acquire_presence_write_serialization", delayed_acquire
    )

    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    cid = _client_id()

    events.clear()
    res = _heartbeat(required_client, csrf, pid, cid)
    assert res.status_code == 200, res.text
    # 顺序必须是 acquire → now；且 heartbeat 路径只取一次 now
    assert "acquire" in events
    assert "now" in events
    assert events.index("acquire") < events.index("now"), events
    assert events.count("now") == 1, events

    # leaseExpiresAt 必须对齐“串行化之后”的 now+45s
    expected_expires = t0 + timedelta(seconds=5 + 45)
    expires = datetime.fromisoformat(
        res.json()["leaseExpiresAt"].replace("Z", "+00:00")
    )
    assert expires == expected_expires, (expires, expected_expires)
    # 等待期间推进的 5 秒不得被扣成 ~40s 剩余
    assert expires != t0 + timedelta(seconds=45)


def test_heartbeat_wait_expired_slot_frees_limit_with_fresh_now(
    required_client, monkeypatch
):
    """
    用途：等待写串行化期间将满的旧租约过期后，新 client 应 200 而非误 429；
    活动计数不得因陈旧 now 把已过期租约当活动；最终活动数 ≤ 8。
    场景：7 条长活 + 1 条将在等待中过期；第 9 次心跳为新 client。
    """
    import app.services.project_presence_service as presence_svc

    t0 = datetime(2026, 7, 20, 15, 30, 0, tzinfo=timezone.utc)
    clock = {"t": t0}

    def fake_now() -> datetime:
        return clock["t"]

    real_acquire = presence_svc._acquire_presence_write_serialization

    def delayed_acquire(db) -> None:
        # 写锁等待期间时钟 +5s：将过期租约（t0+2）变为已过期
        clock["t"] = clock["t"] + timedelta(seconds=5)
        real_acquire(db)

    monkeypatch.setattr(presence_svc, "_utc_now", fake_now)
    monkeypatch.setattr(
        presence_svc, "_acquire_presence_write_serialization", delayed_acquire
    )

    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ws = admin.active_workspace_id or "ws_local"

    # 先建 7 条“长活”租约（不走 delayed acquire 污染时钟：直接入库）
    long_exp = t0 + timedelta(seconds=300)
    live_cids: list[str] = []
    db = SessionLocal()
    try:
        for i in range(7):
            cid = _client_id(24 + (i % 4))
            while cid in live_cids:
                cid = _client_id(30 + i)
            live_cids.append(cid)
            db.execute(
                text(
                    "INSERT INTO project_presence_leases "
                    "(id, workspace_id, project_id, user_id, client_digest, "
                    "last_seen_at, expires_at) "
                    "VALUES (:id, :ws, :pid, :uid, :dig, :ls, :ex)"
                ),
                {
                    "id": f"ppl_live_{i:02d}_{secrets.token_hex(4)}",
                    "ws": ws,
                    "pid": pid,
                    "uid": admin.user_id,
                    "dig": _digest(cid),
                    "ls": t0.isoformat(),
                    "ex": long_exp.isoformat(),
                },
            )
        # 第 8 条：t0+2s 过期，等待 +5s 后应被 purge / 不计活动
        dying_cid = _client_id(36)
        assert dying_cid not in live_cids
        db.execute(
            text(
                "INSERT INTO project_presence_leases "
                "(id, workspace_id, project_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"ppl_dying_{secrets.token_hex(4)}",
                "ws": ws,
                "pid": pid,
                "uid": admin.user_id,
                "dig": _digest(dying_cid),
                "ls": t0.isoformat(),
                "ex": (t0 + timedelta(seconds=2)).isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()

    # 重置时钟到 t0；下一次 heartbeat 才会在 acquire 时推进
    clock["t"] = t0
    new_cid = _client_id(40)
    assert new_cid not in live_cids and new_cid != dying_cid
    res = _heartbeat(required_client, csrf, pid, new_cid)
    # 正确实现：等待后 now=t0+5，dying 已过期，活动=7，新 client 200
    assert res.status_code == 200, res.text
    expected_expires = t0 + timedelta(seconds=5 + 45)
    expires = datetime.fromisoformat(
        res.json()["leaseExpiresAt"].replace("Z", "+00:00")
    )
    assert expires == expected_expires, (expires, expected_expires)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT client_digest, expires_at FROM project_presence_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).fetchall()
        digests = {r[0] for r in rows}
        assert _digest(new_cid) in digests
        # dying 应被机会清理删除
        assert _digest(dying_cid) not in digests
        # 活动（expires > post-wait now）不得超过 8
        post_now = t0 + timedelta(seconds=5)
        active = 0
        for _dig, exp_raw in rows:
            exp = exp_raw
            if isinstance(exp, str):
                exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > post_now:
                active += 1
        assert active <= 8
        assert active == 8  # 7 long + 1 new
    finally:
        db.close()
