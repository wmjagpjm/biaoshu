"""
模块：P13-G1 项目章节编辑意图租约后端专项测试
用途：真实 HTTP/DB/并发验收 chapter-edit-lease heartbeat/leave；failure-first 禁止假绿。
对接：POST /api/projects/{projectId}/chapter-edit-lease/heartbeat|leave；
  project_chapter_edit_lease_service；project_chapter_edit_leases 表。
二次开发：禁止源码字符串/hasattr/预插最终结果/mock service 冒充 HTTP；
  不得改 deps、认证、editor-state 或已有测试。
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
_TEST_PASSWORD = "TestPass-P13G1-Admin-0001!"
_WRITER_PASSWORD = "TestPass-P13G1-Writer-0001!"
_ROLE_PASSWORDS = {
    "finance": "TestPass-P13G1-Finance!",
    "hr": "TestPass-P13G1-Hr!",
    "bidder": "TestPass-P13G1-Bidder!",
    "bid_writer": _WRITER_PASSWORD,
}
_ADMIN_USER = "admin_p13g1"
_WRITER_USER = "writer_p13g1"
_CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_HEARTBEAT_KEYS = {"leaseExpiresAt", "refreshAfterSeconds"}
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
    text_blob = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text_blob, f"敏感标记泄漏: {marker}"


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


def _chapter_id(suffix: str = "a") -> str:
    return f"chap_p13g1_{suffix}"


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


def _create_project(
    client: TestClient,
    csrf: str,
    name: str = "P13G1 项目",
    *,
    kind: str = "technical",
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _put_chapters(
    client: TestClient,
    csrf: str,
    project_id: str,
    chapters: list[dict] | Any,
) -> dict:
    """用途：经公开 PUT 写入真实技术标 chapters。"""
    res = client.put(
        f"/api/projects/{project_id}/editor-state",
        json={"chapters": chapters},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _default_chapters(*ids: str) -> list[dict]:
    out: list[dict] = []
    for i, cid in enumerate(ids):
        out.append(
            {
                "id": cid,
                "title": f"章节{i + 1}",
                "body": f"正文{i + 1}",
                "status": "pending",
            }
        )
    return out


def _hb_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/chapter-edit-lease/heartbeat"


def _leave_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/chapter-edit-lease/leave"


def _heartbeat(
    client: TestClient,
    csrf: str,
    project_id: str,
    client_id: str,
    chapter_id: str,
    *,
    headers: dict[str, str] | None = None,
):
    hdrs = {"X-CSRF-Token": csrf}
    if headers:
        hdrs.update(headers)
    return client.post(
        _hb_url(project_id),
        json={"clientId": client_id, "chapterId": chapter_id},
        headers=hdrs,
    )


def _leave(
    client: TestClient,
    csrf: str,
    project_id: str,
    client_id: str,
    chapter_id: str,
    *,
    headers: dict[str, str] | None = None,
):
    hdrs = {"X-CSRF-Token": csrf}
    if headers:
        hdrs.update(headers)
    return client.post(
        _leave_url(project_id),
        json={"clientId": client_id, "chapterId": chapter_id},
        headers=hdrs,
    )


def _assert_no_store(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store"


def _assert_heartbeat_shape(body: dict) -> None:
    assert set(body.keys()) == _HEARTBEAT_KEYS
    assert body["refreshAfterSeconds"] == 15
    assert isinstance(body["leaseExpiresAt"], str)
    expires = datetime.fromisoformat(body["leaseExpiresAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = (expires - now).total_seconds()
    assert 40 <= delta <= 50, delta
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


def _count_leases(project_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        insp = inspect(engine)
        if "project_chapter_edit_leases" not in insp.get_table_names():
            return -1
        if project_id is None:
            return int(
                db.execute(text("SELECT COUNT(*) FROM project_chapter_edit_leases")).scalar()
                or 0
            )
        return int(
            db.execute(
                text(
                    "SELECT COUNT(*) FROM project_chapter_edit_leases "
                    "WHERE project_id = :pid"
                ),
                {"pid": project_id},
            ).scalar()
            or 0
        )
    finally:
        db.close()


# ---------- failure-first：路由/表缺失与成功 shape ----------


def test_routes_exist_or_failure_first_missing(required_client):
    """用途：heartbeat/leave 路由必须存在；failure-first 阶段应因缺失失败。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("route")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    hb = _heartbeat(required_client, csrf, pid, cid, ch)
    # 实现后 200；failure-first 404/405
    assert hb.status_code == 200, hb.text
    leave = _leave(required_client, csrf, pid, cid, ch)
    assert leave.status_code == 204, leave.text


def test_heartbeat_success_exact_shape_digest_and_no_store(required_client):
    """用途：成功 heartbeat 精确两键、45/15、no-store、digest 落库、原文零库。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("ok")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    res = _heartbeat(required_client, csrf, pid, cid, ch)
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    data = res.json()
    _assert_heartbeat_shape(data)
    assert cid not in res.text
    assert ch not in res.text  # 成功响应不得含原文 chapterId
    assert cid not in json.dumps(data)
    assert ch not in json.dumps(data)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT client_digest, chapter_id FROM project_chapter_edit_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == _digest(cid)
        assert rows[0][1] == ch
        raw = db.execute(
            text("SELECT * FROM project_chapter_edit_leases WHERE project_id = :pid"),
            {"pid": pid},
        ).mappings().all()
        blob = json.dumps([dict(x) for x in raw], default=str)
        assert cid not in blob
    finally:
        db.close()


def test_leave_success_204_empty_body_idempotent(required_client):
    """用途：leave 成功 204 空 body + no-store，重复幂等。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("leave")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid, ch).status_code == 200
    leave1 = _leave(required_client, csrf, pid, cid, ch)
    assert leave1.status_code == 204, leave1.text
    assert leave1.content == b""
    _assert_no_store(leave1)
    leave2 = _leave(required_client, csrf, pid, cid, ch)
    assert leave2.status_code == 204, leave2.text
    assert leave2.content == b""
    assert _count_leases(pid) == 0


def test_same_client_renew_no_duplicate_row(required_client):
    """用途：同 user+client 原行续期，不新增行。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("renew")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    r1 = _heartbeat(required_client, csrf, pid, cid, ch)
    assert r1.status_code == 200, r1.text
    exp1 = r1.json()["leaseExpiresAt"]
    r2 = _heartbeat(required_client, csrf, pid, cid, ch)
    assert r2.status_code == 200, r2.text
    exp2 = r2.json()["leaseExpiresAt"]
    assert exp2 >= exp1
    assert _count_leases(pid) == 1


def test_same_user_different_client_conflict(required_client):
    """用途：同用户不同 client 冲突 409，安全 holder，零内部 ID。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("sameuser")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    c1, c2 = _client_id(), _client_id(28)
    assert _heartbeat(required_client, csrf, pid, c1, ch).status_code == 200
    res = _heartbeat(required_client, csrf, pid, c2, ch)
    assert res.status_code == 409, res.text
    _assert_no_store(res)
    detail = res.json()["detail"]
    assert set(detail.keys()) == {"code", "message", "holderUsername"}
    assert detail["code"] == "chapter_edit_lease_conflict"
    assert detail["message"] == "此章节近期已有处理意图"
    assert detail["holderUsername"] == admin.username
    assert c1 not in res.text and c2 not in res.text
    assert _digest(c1) not in res.text
    assert _count_leases(pid) == 1


def test_different_user_conflict_safe_holder(required_client):
    """用途：不同用户冲突返回安全 holderUsername。"""
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
    pid = _create_project(required_client, csrf, "双用户章节项目")
    ch = _chapter_id("du")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    assert _heartbeat(required_client, csrf, pid, _client_id(), ch).status_code == 200

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    res = _heartbeat(required_client, csrf_w, pid, _client_id(), ch)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "chapter_edit_lease_conflict"
    assert detail["holderUsername"] == admin.username
    _assert_no_secrets(res.json())
    assert _count_leases(pid) == 1


def test_concurrent_two_users_one_winner(required_client):
    """
    用途：两用户真并发抢同章节：恰一 200、一 409、最终一行、无 500。
    证据：Barrier 起跑；禁止只断言不 500。
    """
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    pid = _create_project(required_client, csrf, "并发章节")
    ch = _chapter_id("race")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    # 保留两用户 cookie/csrf
    admin_cookies = dict(required_client.cookies)
    # 重新登录 admin 获取独立 cookie 快照
    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    admin_cookies = {k: v for k, v in required_client.cookies.items()}
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    writer_cookies = {k: v for k, v in required_client.cookies.items()}

    cid_a, cid_w = _client_id(24), _client_id(28)
    barrier = threading.Barrier(2, timeout=15)

    def worker(cookies: dict, csrf_token: str, cid: str) -> int:
        with TestClient(app) as c:
            for k, v in cookies.items():
                c.cookies.set(k, v)
            barrier.wait()
            res = c.post(
                _hb_url(pid),
                json={"clientId": cid, "chapterId": ch},
                headers={"X-CSRF-Token": csrf_token},
            )
            assert cid not in res.text
            assert res.status_code != 500, res.text
            return res.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(worker, admin_cookies, csrf_a, cid_a),
            pool.submit(worker, writer_cookies, csrf_w, cid_w),
        ]
        codes = [f.result() for f in as_completed(futs)]
    assert sorted(codes) == [200, 409], codes
    assert _count_leases(pid) == 1


def test_expired_takeover_and_fresh_now_after_lock(required_client, monkeypatch):
    """
    用途：独立事务真实持有项目 SQLite 写锁；锁外阻塞期间推进可控时钟；
      锁后一次 fresh now 决定过期接管与 expires。
    证据：Event 证明已进入 acquire 且尚未取得锁；禁止真实 sleep 作主同步。
    """
    import app.services.project_chapter_edit_lease_service as lease_svc

    t0 = datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone.utc)
    clock = {"t": t0}
    events: list[str] = []
    entered_acquire = threading.Event()
    holder_ready = threading.Event()
    about_to_block = threading.Event()
    acquired_lock = threading.Event()

    def fake_now() -> datetime:
        events.append("now")
        return clock["t"]

    real_acquire = lease_svc._acquire_project_write_lock

    def gated_acquire(db, project_id: str) -> None:
        # 1) 证明请求已进入 acquire（鉴权/body 已过），此时尚未持锁
        events.append("acquire_enter")
        entered_acquire.set()
        # 2) 等主线程用独立事务真实占住 SQLite 写锁
        assert holder_ready.wait(timeout=10), "holder 未就绪"
        try:
            db.execute(text("PRAGMA busy_timeout = 15000"))
        except Exception:
            pass
        events.append("acquire_blocking")
        about_to_block.set()
        # 3) 真实 acquire：在 holder 释放前阻塞于 SQLite 写锁
        real_acquire(db, project_id)
        events.append("acquire_done")
        acquired_lock.set()

    monkeypatch.setattr(lease_svc, "_utc_now", fake_now)
    monkeypatch.setattr(lease_svc, "_acquire_project_write_lock", gated_acquire)

    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("fresh")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    ws = admin.active_workspace_id or "ws_local"
    cookies = {k: v for k, v in required_client.cookies.items()}

    # 直接插入将在等待中过期的租约（同用户不同 client，过期后可接管）
    old_cid = _client_id(30)
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO project_chapter_edit_leases "
                "(id, workspace_id, project_id, chapter_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :ch, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"pcel_{secrets.token_hex(8)}",
                "ws": ws,
                "pid": pid,
                "ch": ch,
                "uid": admin.user_id,
                "dig": _digest(old_cid),
                "ls": t0.isoformat(),
                "ex": (t0 + timedelta(seconds=2)).isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()

    clock["t"] = t0
    events.clear()
    new_cid = _client_id(32)
    result_box: dict[str, Any] = {}
    worker_error: list[BaseException] = []

    def worker() -> None:
        try:
            with TestClient(app) as c:
                for k, v in cookies.items():
                    c.cookies.set(k, v)
                res = c.post(
                    _hb_url(pid),
                    json={"clientId": new_cid, "chapterId": ch},
                    headers={"X-CSRF-Token": csrf},
                )
                result_box["status"] = res.status_code
                result_box["text"] = res.text
                try:
                    result_box["body"] = res.json()
                except Exception:
                    result_box["body"] = None
        except BaseException as exc:  # noqa: BLE001 — 测试线程收集
            worker_error.append(exc)

    thr = threading.Thread(target=worker, name="p13g1-fresh-now-worker")
    thr.start()
    # 等 worker 完成鉴权并进入 acquire 闸门（此时 DB 尚未被 holder 锁住）
    assert entered_acquire.wait(timeout=15), (
        f"worker 未进入 acquire; err={worker_error}; box={result_box}; events={events}"
    )
    assert "now" not in events
    assert not acquired_lock.is_set()

    # 独立会话真实持有 SQLite 写锁
    holder = SessionLocal()
    try:
        holder.execute(text("PRAGMA busy_timeout = 15000"))
        holder.execute(
            text("UPDATE projects SET updated_at = updated_at WHERE id = :pid"),
            {"pid": pid},
        )
        holder.flush()
        holder_ready.set()
        assert about_to_block.wait(timeout=10), "worker 未到达真实 acquire"
        # 主同步证据：短超时内拿不到锁 → 仍阻塞在 SQLite 写锁上
        assert not acquired_lock.wait(timeout=0.3), events
        assert "now" not in events
        # 锁外阻塞期间推进可控时钟（过期阈值 t0+2 → 已过期）
        clock["t"] = t0 + timedelta(seconds=5)
        holder.rollback()
    finally:
        try:
            holder.rollback()
        except Exception:
            pass
        holder.close()

    thr.join(timeout=15)
    assert not thr.is_alive(), f"fresh-now worker 超时挂死 err={worker_error}"
    assert not worker_error, worker_error
    assert acquired_lock.is_set()
    assert result_box.get("status") == 200, result_box
    assert "acquire_enter" in events and "acquire_done" in events and "now" in events
    assert events.index("acquire_enter") < events.index("acquire_blocking")
    assert events.index("acquire_blocking") < events.index("acquire_done")
    assert events.index("acquire_done") < events.index("now"), events
    assert events.count("now") == 1, events
    expected = t0 + timedelta(seconds=5 + 45)
    expires = datetime.fromisoformat(
        result_box["body"]["leaseExpiresAt"].replace("Z", "+00:00")
    )
    assert expires == expected, (expires, expected)

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT client_digest FROM project_chapter_edit_leases "
                "WHERE project_id = :pid AND chapter_id = :ch"
            ),
            {"pid": pid, "ch": ch},
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == _digest(new_cid)
    finally:
        db.close()


def test_chapter_limit_eight_renew_ninth_429(required_client):
    """用途：每用户项目最多 8 活动章节；旧持有续期成功，第 9 新章节 429。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    chapter_ids = [_chapter_id(f"L{i}") for i in range(9)]
    _put_chapters(required_client, csrf, pid, _default_chapters(*chapter_ids))
    cid = _client_id()
    for ch in chapter_ids[:8]:
        res = _heartbeat(required_client, csrf, pid, cid, ch)
        assert res.status_code == 200, (ch, res.text)
    # 旧持有续期
    renew = _heartbeat(required_client, csrf, pid, cid, chapter_ids[0])
    assert renew.status_code == 200, renew.text
    # 第 9 章节 429
    limited = _heartbeat(required_client, csrf, pid, cid, chapter_ids[8])
    assert limited.status_code == 429, limited.text
    detail = limited.json()["detail"]
    assert detail.get("code") == "chapter_edit_lease_limit"
    assert detail.get("message") == "当前项目章节处理意图数量已达上限"
    _assert_no_secrets(limited.json())
    assert _count_leases(pid) == 8


# ---------- 章节命中 / 项目边界 ----------


def test_chapter_missing_non_array_duplicate_business(required_client):
    """用途：缺失/非数组/重复目标/商务项目边界。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("miss")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()

    # 目标缺失
    missing = _heartbeat(required_client, csrf, pid, cid, "chap_not_exist_p13g1")
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"]["code"] == "chapter_not_found"
    assert "chap_not_exist_p13g1" not in missing.text

    # 非数组 chapters
    _put_chapters(required_client, csrf, pid, {"broken": True})
    non_arr = _heartbeat(required_client, csrf, pid, cid, ch)
    assert non_arr.status_code == 404, non_arr.text
    assert non_arr.json()["detail"]["code"] == "chapter_not_found"

    # 重复目标
    _put_chapters(
        required_client,
        csrf,
        pid,
        [
            {"id": ch, "title": "A", "body": "1"},
            {"id": ch, "title": "B", "body": "2"},
        ],
    )
    dup = _heartbeat(required_client, csrf, pid, cid, ch)
    assert dup.status_code == 409, dup.text
    assert dup.json()["detail"]["code"] == "chapter_state_invalid"
    assert ch not in dup.text

    # 商务项目
    biz = _create_project(required_client, csrf, "商务项目", kind="business")
    # 商务也可能写 chapters；但租约仅 technical
    _put_chapters(required_client, csrf, biz, _default_chapters(ch))
    biz_res = _heartbeat(required_client, csrf, biz, cid, ch)
    assert biz_res.status_code == 404, biz_res.text
    assert biz_res.json()["detail"]["code"] == "project_not_found"


def test_leave_after_chapter_deleted_still_cleans(required_client):
    """用途：章节删除后 leave 仍可精确清理。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("del")
    other = _chapter_id("keep")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch, other))
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid, ch).status_code == 200
    assert _heartbeat(required_client, csrf, pid, cid, other).status_code == 200
    # 删除目标章节
    _put_chapters(required_client, csrf, pid, _default_chapters(other))
    leave = _leave(required_client, csrf, pid, cid, ch)
    assert leave.status_code == 204, leave.text
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT chapter_id FROM project_chapter_edit_leases "
                "WHERE project_id = :pid"
            ),
            {"pid": pid},
        ).fetchall()
        assert {r[0] for r in rows} == {other}
    finally:
        db.close()


# ---------- 鉴权 / 作用域 / CSRF / 角色 ----------


def test_csrf_required(required_client):
    """用途：缺/错 CSRF 固定 403，零写。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("csrf")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    no = required_client.post(
        _hb_url(pid),
        json={"clientId": cid, "chapterId": ch},
    )
    assert no.status_code == 403, no.text
    assert no.json()["detail"]["code"] == "csrf_invalid"
    bad = required_client.post(
        _hb_url(pid),
        json={"clientId": cid, "chapterId": ch},
        headers={"X-CSRF-Token": "wrong-csrf-token-value"},
    )
    assert bad.status_code == 403, bad.text
    assert _count_leases(pid) == 0


def test_x_workspace_id_any_value_rejected(required_client):
    """用途：任何 X-Workspace-Id（含空）精确 403，固定 code。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("xws")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    before = _count_leases(pid)
    assert before == 0
    for val in ("ws_local", "", "ws_other"):
        res = _heartbeat(
            required_client,
            csrf,
            pid,
            cid,
            ch,
            headers={"X-Workspace-Id": val},
        )
        assert res.status_code == 403, (val, res.status_code, res.text)
        detail = res.json()["detail"]
        assert detail["code"] == "workspace_header_forbidden"
        _assert_no_store(res)
    assert _count_leases(pid) == 0
    ok = _heartbeat(required_client, csrf, pid, cid, ch)
    assert ok.status_code == 200, ok.text


def test_role_matrix_owner_not_bypass(required_client):
    """用途：非 bid_writer 拒绝；owner 不替代角色。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("role")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    for role, pwd in _ROLE_PASSWORDS.items():
        if role == "bid_writer":
            continue
        body_admin = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
        csrf_admin = _csrf(body_admin)
        uname = f"role_{role}_p13g1"
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
        res = _heartbeat(required_client, c, pid, _client_id(), ch)
        assert res.status_code == 403, (role, res.text)
        assert res.json()["detail"]["code"] == "role_forbidden"

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
    ok = _heartbeat(required_client, csrf_w, pid, _client_id(), ch)
    assert ok.status_code == 200, ok.text


def test_cross_workspace_and_missing_project_404(required_client):
    """用途：跨空间/不存在项目统一 404。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf, "本空间")
    ch = _chapter_id("cross")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    other_pid = _seed_second_workspace_project(
        workspace_id="ws_p13g1_other",
        workspace_name="其它空间G1",
        owner_user_id=admin.user_id,
    )
    cross = _heartbeat(required_client, csrf, other_pid, _client_id(), ch)
    assert cross.status_code == 404, cross.text
    assert "ws_p13g1_other" not in cross.text
    missing = _heartbeat(required_client, csrf, "proj_missing_p13g1", _client_id(), ch)
    assert missing.status_code == 404, missing.text
    leave_missing = _leave(
        required_client, csrf, "proj_missing_p13g1", _client_id(), ch
    )
    assert leave_missing.status_code == 404, leave_missing.text


def test_unauthenticated_rejected(required_client):
    """用途：无会话精确 401，零写。"""
    _bootstrap()
    before = _count_leases()
    required_client.cookies.clear()
    res = required_client.post(
        _hb_url("any_project"),
        json={"clientId": _client_id(), "chapterId": _chapter_id()},
        headers={"X-CSRF-Token": "x"},
    )
    assert res.status_code == 401, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "auth_required"
    # 中间件 401 路径；精确状态码即可，零租约副作用
    assert _count_leases() == before


def test_disabled_auth_mode_rejected(monkeypatch):
    """用途：AUTH_MODE=disabled 时精确 403 拒绝 chapter-edit-lease。"""
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            res = client.post(
                _hb_url("any"),
                json={"clientId": _client_id(), "chapterId": _chapter_id()},
            )
            assert res.status_code == 403, res.text
            detail = res.json()["detail"]
            assert detail["code"] == "role_forbidden"
            _assert_no_store(res)
    finally:
        get_settings.cache_clear()


# ---------- 请求体校验 ----------


def _forbidden_echo_fragments(payload: dict[str, Any]) -> list[str]:
    frags: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str) and value:
            frags.append(value)
            stripped = value.strip()
            if stripped and stripped != value:
                frags.append(stripped)
        elif isinstance(value, (int, float)):
            frags.append(str(value))
        elif isinstance(value, list):
            frags.extend(str(x) for x in value)
        elif value is not None and not isinstance(value, (dict, bool)):
            frags.append(str(value))
    if "client_id" in payload and isinstance(payload["client_id"], str):
        frags.append(payload["client_id"])
    if "chapter_id" in payload and isinstance(payload["chapter_id"], str):
        frags.append(payload["chapter_id"])
    return [f for f in frags if f]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"clientId": "a" * 24},
        {"chapterId": "chap_only"},
        {"clientId": "short", "chapterId": "chap_x"},
        {"clientId": "a" * 21, "chapterId": "chap_x"},
        {"clientId": "a" * 65, "chapterId": "chap_x"},
        {"clientId": "bad client id!!!!!!!!!!!!!!!", "chapterId": "chap_x"},
        {"clientId": "  " + "a" * 22, "chapterId": "chap_x"},
        {"clientId": "a" * 24, "chapterId": " chap_x"},
        {"clientId": "a" * 24, "chapterId": "chap_x "},
        {"clientId": "a" * 24, "chapterId": ""},
        {"clientId": "a" * 24, "chapterId": "x" * 129},
        {"clientId": "a" * 24, "chapterId": "bad\nline"},
        {"clientId": "a" * 24, "chapterId": "bad\u2028line"},
        {"clientId": "a" * 24, "chapterId": "chap_x", "extra": 1},
        {"clientId": "a" * 24, "chapterId": "chap_x", "extraKey": "LEAK_EXTRA_P13G1"},
        {"client_id": "a" * 24, "chapter_id": "chap_x"},
        {"clientId": 123456789012345678901234, "chapterId": "chap_x"},
        {"clientId": "a" * 24, "chapterId": None},
        {"clientId": ["a" * 24], "chapterId": "chap_x"},
    ],
)
def test_body_validation_matrix_no_echo(required_client, payload):
    """用途：非法 body 固定 422；不得回显 clientId/chapterId/extra。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("val")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    forbidden = _forbidden_echo_fragments(payload)
    if any(v == "LEAK_EXTRA_P13G1" for v in payload.values()):
        forbidden.append("LEAK_EXTRA_P13G1")

    res = required_client.post(
        _hb_url(pid),
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 422, (payload, res.status_code, res.text)
    for frag in forbidden:
        assert frag not in res.text, (frag, res.text)
    if res.headers.get("content-type", "").startswith("application/json"):
        body_json = res.json()
        dumped = json.dumps(body_json, ensure_ascii=False)
        assert "input" not in dumped
        detail = body_json.get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") == "chapter_edit_lease_request_invalid"
            assert detail.get("message") == "章节编辑意图请求无效"
        _assert_no_secrets(body_json)

    leave = required_client.post(
        _leave_url(pid),
        json=payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert leave.status_code == 422, (payload, leave.status_code, leave.text)
    for frag in forbidden:
        assert frag not in leave.text, (frag, leave.text)


def test_client_and_chapter_id_boundary_accepted(required_client):
    """用途：clientId 22/64 与 chapterId 1/128 合法边界接受。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch1 = "c"
    ch128 = "c" * 128
    _put_chapters(required_client, csrf, pid, _default_chapters(ch1, ch128))
    for n, ch in ((22, ch1), (64, ch128)):
        cid = _client_id(n)
        assert len(cid) == n
        res = _heartbeat(required_client, csrf, pid, cid, ch)
        assert res.status_code == 200, (n, ch, res.text)


def _assert_body_invalid_response(res, *, markers: list[str], leases_before: int, pid: str) -> None:
    """用途：统一断言固定脱敏 422 + no-store + 零回显 + 零租约副作用。"""
    assert res.status_code == 422, res.text
    _assert_no_store(res)
    body_json = res.json()
    detail = body_json.get("detail")
    assert detail == {
        "code": "chapter_edit_lease_request_invalid",
        "message": "章节编辑意图请求无效",
    }
    dumped = json.dumps(body_json, ensure_ascii=False)
    assert "input" not in dumped
    for m in markers:
        if m:
            assert m not in res.text, (m, res.text)
            assert m not in dumped
    _assert_no_secrets(body_json)
    assert _count_leases(pid) == leases_before


@pytest.mark.parametrize(
    "raw,marker",
    [
        (b"", "EMPTY_BODY_MARKER"),
        (b"{not-json", "MALFORMED_JSON_P13G1_MARKER"),
        (b'["array","not","object"]', "ARRAY_ROOT_P13G1"),
        (b'"scalar-string-root"', "SCALAR_ROOT_P13G1"),
        (b"null", "null"),
        (b"12345", "12345"),
    ],
)
def test_json_public_matrix_heartbeat_and_leave(required_client, raw, marker):
    """用途：空体/malformed/array/scalar/null 公开 HTTP 精确 422，heartbeat+leave。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("jsonmat")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    before = _count_leases(pid)
    assert before == 0
    markers = [marker]
    if raw and raw not in (b"", b"null"):
        try:
            markers.append(raw.decode("utf-8", errors="ignore")[:40])
        except Exception:
            pass

    hb = required_client.post(
        _hb_url(pid),
        content=raw,
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    _assert_body_invalid_response(hb, markers=markers, leases_before=before, pid=pid)

    leave = required_client.post(
        _leave_url(pid),
        content=raw,
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    _assert_body_invalid_response(leave, markers=markers, leases_before=before, pid=pid)


def test_oversized_body_http_and_content_length_early_reject(required_client):
    """用途：>4096 body 公开 422；合法 Content-Length 早拒绝；零写。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("oversize")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    before = _count_leases(pid)
    assert before == 0
    marker = "OVERSIZE_BODY_MARKER_P13G1_"
    # 构造超过 4096 的 JSON 对象（合法形状但超限）
    pad = "x" * 4200
    oversize = json.dumps(
        {"clientId": "a" * 24, "chapterId": ch, "pad": marker + pad},
        ensure_ascii=False,
    ).encode("utf-8")
    assert len(oversize) > 4096

    for url in (_hb_url(pid), _leave_url(pid)):
        res = required_client.post(
            url,
            content=oversize,
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        )
        _assert_body_invalid_response(
            res,
            markers=[marker, pad[:32], ch, "a" * 24],
            leases_before=before,
            pid=pid,
        )

    # Content-Length 早拒绝：声明超限长度 + 小/空 body（客户端可发送不一致 CL）
    for url in (_hb_url(pid), _leave_url(pid)):
        res = required_client.post(
            url,
            content=b"{}",
            headers={
                "X-CSRF-Token": csrf,
                "Content-Type": "application/json",
                "Content-Length": "9999",
            },
        )
        # httpx 可能重写 Content-Length；若被重写则至少小 body 走正常 422
        assert res.status_code == 422, res.text
        detail = res.json()["detail"]
        assert detail["code"] == "chapter_edit_lease_request_invalid"
        assert detail["message"] == "章节编辑意图请求无效"
        _assert_no_store(res)
        assert _count_leases(pid) == 0


def test_stream_limit_unit_without_trusted_content_length():
    """
    用途：无可信 Content-Length 时 stream 累计上限为最终门（精确单元证据）。
    覆盖：缺失 CL 与伪造非数字 CL；超过 4096 立即固定 422，不得读完整超限体。
    """
    import asyncio

    from fastapi import HTTPException

    import app.api.project_chapter_edit_leases as lease_api

    class _FakeRequest:
        def __init__(self, headers: dict[str, str], chunks: list[bytes]) -> None:
            self.headers = headers
            self._chunks = chunks
            self.read_chunks = 0

        async def stream(self):
            for c in self._chunks:
                self.read_chunks += 1
                yield c

    # 两块累计 2000+2500=4500 > 4096；读到第二块即拒，不应再读第三块
    chunks = [b"a" * 2000, b"b" * 2500, b"SHOULD_NOT_READ"]

    async def _run(headers: dict[str, str]) -> None:
        req = _FakeRequest(headers, chunks)
        with pytest.raises(HTTPException) as ei:
            await lease_api._read_limited_body_bytes(req)
        assert ei.value.status_code == 422
        assert ei.value.detail == {
            "code": "chapter_edit_lease_request_invalid",
            "message": "章节编辑意图请求无效",
        }
        assert ei.value.headers.get("Cache-Control") == "no-store"
        # 最终门在第二块触发，第三块未读
        assert req.read_chunks == 2

    asyncio.run(_run({}))  # 缺失 Content-Length
    asyncio.run(_run({"content-length": "not-a-number"}))  # 伪造 CL

    # 合法 Content-Length 早拒绝：不得进入 stream
    class _NoStreamRequest:
        headers = {"content-length": "5000"}

        async def stream(self):
            raise AssertionError("早拒绝后不得 stream")

    with pytest.raises(HTTPException) as early:
        asyncio.run(lease_api._read_limited_body_bytes(_NoStreamRequest()))  # type: ignore[arg-type]
    assert early.value.status_code == 422
    assert early.value.detail["code"] == "chapter_edit_lease_request_invalid"


# ---------- holder 失效接管 / leave 隔离 ----------


def test_holder_deactivated_role_change_bad_username_takeover(required_client):
    """用途：holder 停用/改角色/坏用户名后当前 actor 可接管。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    w2, w2_pwd = "writer2_p13g1", "TestPass-P13G1-Writer-0002!"
    w3, w3_pwd = "writer3_p13g1", "TestPass-P13G1-Writer-0003!"
    _create_member(
        required_client, csrf, username=w2, password=w2_pwd, role=auth_service.ROLE_BID_WRITER
    )
    _create_member(
        required_client, csrf, username=w3, password=w3_pwd, role=auth_service.ROLE_BID_WRITER
    )
    pid = _create_project(required_client, csrf)
    chs = [_chapter_id(f"H{i}") for i in range(3)]
    _put_chapters(required_client, csrf, pid, _default_chapters(*chs))

    # holder1: writer 建租约后停用成员
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    uid_w = body_w["user"]["id"]
    assert _heartbeat(required_client, csrf_w, pid, _client_id(), chs[0]).status_code == 200

    # holder2: writer2 建租约后改角色
    body_w2 = _login(required_client, w2, w2_pwd)
    csrf_w2 = _csrf(body_w2)
    uid_w2 = body_w2["user"]["id"]
    assert _heartbeat(required_client, csrf_w2, pid, _client_id(), chs[1]).status_code == 200

    # holder3: writer3 建租约后坏用户名
    body_w3 = _login(required_client, w3, w3_pwd)
    csrf_w3 = _csrf(body_w3)
    uid_w3 = body_w3["user"]["id"]
    assert _heartbeat(required_client, csrf_w3, pid, _client_id(), chs[2]).status_code == 200

    db = SessionLocal()
    try:
        m1 = (
            db.query(WorkspaceMemberRow)
            .filter(WorkspaceMemberRow.user_id == uid_w)
            .one()
        )
        m1.is_active = False
        m2 = (
            db.query(WorkspaceMemberRow)
            .filter(WorkspaceMemberRow.user_id == uid_w2)
            .one()
        )
        m2.role = auth_service.ROLE_BIDDER
        u3 = db.query(LocalUserRow).filter(LocalUserRow.id == uid_w3).one()
        u3.username = " bad\nname "
        db.commit()
    finally:
        db.close()

    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    for ch in chs:
        res = _heartbeat(required_client, csrf_a, pid, _client_id(), ch)
        assert res.status_code == 200, (ch, res.text)
        # 冲突响应不得出现坏用户名
        assert " bad\nname " not in res.text


def test_actor_unsafe_username_forbidden_zero_lease(required_client):
    """用途：当前 actor 用户名不安全固定 403，零租约。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("actor")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    db = SessionLocal()
    try:
        u = db.query(LocalUserRow).filter(LocalUserRow.id == admin.user_id).one()
        u.username = "\tbadsactor "
        db.commit()
    finally:
        db.close()
    res = _heartbeat(required_client, csrf, pid, _client_id(), ch)
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "role_forbidden"
    assert _count_leases(pid) == 0


def test_leave_isolates_other_clients_chapters_users_projects(required_client):
    """用途：leave 五维精确删除，不误删其它 client/章节/用户/项目。"""
    _bootstrap()
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
    ch_a, ch_b, ch_c = _chapter_id("a"), _chapter_id("b"), _chapter_id("c")
    _put_chapters(required_client, csrf, p1, _default_chapters(ch_a, ch_b, ch_c))
    _put_chapters(required_client, csrf, p2, _default_chapters(ch_a))
    c_keep, c_drop, c_p2 = _client_id(), _client_id(28), _client_id(30)
    assert _heartbeat(required_client, csrf, p1, c_keep, ch_a).status_code == 200
    assert _heartbeat(required_client, csrf, p1, c_drop, ch_b).status_code == 200
    assert _heartbeat(required_client, csrf, p2, c_p2, ch_a).status_code == 200

    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    c_w = _client_id(32)
    # writer 对 p1 另一章节（不得与 admin 已持有章节冲突）
    assert _heartbeat(required_client, csrf_w, p1, c_w, ch_c).status_code == 200

    body_a = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf_a = _csrf(body_a)
    leave = _leave(required_client, csrf_a, p1, c_drop, ch_b)
    assert leave.status_code == 204, leave.text

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT project_id, chapter_id, client_digest "
                "FROM project_chapter_edit_leases"
            )
        ).fetchall()
        digests = {r[2] for r in rows}
        assert _digest(c_drop) not in digests
        assert _digest(c_keep) in digests
        assert _digest(c_p2) in digests
        assert _digest(c_w) in digests
        # 错 client leave 幂等
    finally:
        db.close()

    wrong = _leave(required_client, csrf_a, p1, _client_id(40), ch_a)
    assert wrong.status_code == 204, wrong.text
    db2 = SessionLocal()
    try:
        keep_digests = {
            r[0]
            for r in db2.execute(
                text("SELECT client_digest FROM project_chapter_edit_leases")
            ).fetchall()
        }
        assert _digest(c_keep) in keep_digests
    finally:
        db2.close()


# ---------- 表约束 / rollback / 禁止能力 / PUT 诚实边界 ----------


def test_table_constraints_indexes_and_cascade(required_client):
    """用途：表精确八列/唯一键/两复合索引/三 FK CASCADE；workspace/project/user 级联。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf, "级联项目")
    ch = _chapter_id("cascade")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid, ch).status_code == 200

    insp = inspect(engine)
    assert "project_chapter_edit_leases" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("project_chapter_edit_leases")}
    assert cols == {
        "id",
        "workspace_id",
        "project_id",
        "chapter_id",
        "user_id",
        "client_digest",
        "last_seen_at",
        "expires_at",
    }

    expected_uq = ("workspace_id", "project_id", "chapter_id")
    uqs = insp.get_unique_constraints("project_chapter_edit_leases")
    uk_cols = {tuple(u["column_names"]) for u in uqs}
    idxs = insp.get_indexes("project_chapter_edit_leases")
    unique_idx_cols = {tuple(i["column_names"]) for i in idxs if i.get("unique")}
    assert expected_uq in (uk_cols | unique_idx_cols), (uk_cols, unique_idx_cols)

    flat_idx = {tuple(i["column_names"]) for i in idxs}
    assert ("workspace_id", "project_id", "expires_at") in flat_idx, flat_idx
    assert ("workspace_id", "project_id", "user_id", "expires_at") in flat_idx, flat_idx

    fks = insp.get_foreign_keys("project_chapter_edit_leases")
    fk_by_cols = {tuple(fk["constrained_columns"]): fk for fk in fks}

    def _fk_ondelete(fk: dict) -> str:
        opts = fk.get("options") or {}
        return str(opts.get("ondelete") or fk.get("ondelete") or "").upper()

    assert ("workspace_id",) in fk_by_cols
    assert fk_by_cols[("workspace_id",)]["referred_table"] == "workspaces"
    assert fk_by_cols[("workspace_id",)]["referred_columns"] == ["id"]
    assert _fk_ondelete(fk_by_cols[("workspace_id",)]) == "CASCADE"
    assert ("project_id",) in fk_by_cols
    assert fk_by_cols[("project_id",)]["referred_table"] == "projects"
    assert fk_by_cols[("project_id",)]["referred_columns"] == ["id"]
    assert _fk_ondelete(fk_by_cols[("project_id",)]) == "CASCADE"
    assert ("user_id",) in fk_by_cols
    assert fk_by_cols[("user_id",)]["referred_table"] == "local_users"
    assert fk_by_cols[("user_id",)]["referred_columns"] == ["id"]
    assert _fk_ondelete(fk_by_cols[("user_id",)]) == "CASCADE"

    # 运行时 project 级联
    del_p = required_client.delete(
        f"/api/projects/{pid}",
        headers={"X-CSRF-Token": csrf},
    )
    assert del_p.status_code == 204, del_p.text
    assert _count_leases(pid) == 0

    # 运行时 workspace 级联：仅 DB seed 租约后直接删 workspace（不得冒充路由成功）
    other_ws = "ws_p13g1_cascade_ws"
    other_pid = _seed_second_workspace_project(
        workspace_id=other_ws,
        workspace_name="级联空间G1",
        owner_user_id=admin.user_id,
        project_name="空间级联项目",
    )
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO project_chapter_edit_leases "
                "(id, workspace_id, project_id, chapter_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :ch, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"pcel_{secrets.token_hex(8)}",
                "ws": other_ws,
                "pid": other_pid,
                "ch": "chap_ws_cascade",
                "uid": admin.user_id,
                "dig": _digest(_client_id(26)),
                "ls": datetime.now(timezone.utc).isoformat(),
                "ex": (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(),
            },
        )
        db.commit()
        n_ws = db.execute(
            text(
                "SELECT COUNT(*) FROM project_chapter_edit_leases WHERE workspace_id = :ws"
            ),
            {"ws": other_ws},
        ).scalar()
        assert int(n_ws or 0) == 1
        # 直接删 workspace，依赖 workspace_id→workspaces.id ON DELETE CASCADE
        db.execute(text("DELETE FROM workspaces WHERE id = :ws"), {"ws": other_ws})
        db.commit()
        n_ws2 = db.execute(
            text(
                "SELECT COUNT(*) FROM project_chapter_edit_leases WHERE workspace_id = :ws"
            ),
            {"ws": other_ws},
        ).scalar()
        assert int(n_ws2 or 0) == 0
        n_pid = db.execute(
            text(
                "SELECT COUNT(*) FROM project_chapter_edit_leases WHERE project_id = :pid"
            ),
            {"pid": other_pid},
        ).scalar()
        assert int(n_pid or 0) == 0
    finally:
        db.close()

    # 用户级联
    pid2 = _create_project(required_client, csrf, "用户级联")
    ch2 = _chapter_id("uc")
    _put_chapters(required_client, csrf, pid2, _default_chapters(ch2))
    _create_member(
        required_client,
        csrf,
        username=_WRITER_USER,
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_BID_WRITER,
    )
    body_w = _login(required_client, _WRITER_USER, _WRITER_PASSWORD)
    csrf_w = _csrf(body_w)
    assert _heartbeat(required_client, csrf_w, pid2, _client_id(), ch2).status_code == 200
    db = SessionLocal()
    try:
        uid = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _WRITER_USER)
            .one()
            .id
        )
        db.execute(text("DELETE FROM auth_sessions WHERE user_id = :u"), {"u": uid})
        db.execute(text("DELETE FROM workspace_members WHERE user_id = :u"), {"u": uid})
        db.execute(text("DELETE FROM local_users WHERE id = :u"), {"u": uid})
        db.commit()
        n2 = db.execute(
            text(
                "SELECT COUNT(*) FROM project_chapter_edit_leases WHERE user_id = :u"
            ),
            {"u": uid},
        ).scalar()
        assert n2 == 0
    finally:
        db.close()


def test_service_and_commit_failure_rollback(required_client, monkeypatch):
    """
    用途：service/flush 后失败与 commit 失败完整 rollback；无部分写。
    """
    import app.api.project_chapter_edit_leases as lease_api
    import app.services.project_chapter_edit_lease_service as lease_svc

    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("rb")
    other = _chapter_id("rb2")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch, other))
    live_cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, live_cid, ch).status_code == 200

    # 插入过期行供清理路径
    expired_cid = _client_id(26)
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    admin_id: str
    ws_id: str
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT user_id, workspace_id FROM project_chapter_edit_leases "
                "WHERE project_id = :pid LIMIT 1"
            ),
            {"pid": pid},
        ).fetchone()
        assert row is not None
        admin_id, ws_id = str(row[0]), str(row[1])
        db.execute(
            text(
                "INSERT INTO project_chapter_edit_leases "
                "(id, workspace_id, project_id, chapter_id, user_id, client_digest, "
                "last_seen_at, expires_at) "
                "VALUES (:id, :ws, :pid, :ch, :uid, :dig, :ls, :ex)"
            ),
            {
                "id": f"pcel_{secrets.token_hex(8)}",
                "ws": ws_id,
                "pid": pid,
                "ch": other,
                "uid": admin_id,
                "dig": _digest(expired_cid),
                "ls": past.isoformat(),
                "ex": past.isoformat(),
            },
        )
        db.commit()
    finally:
        db.close()

    real_flush_tail = lease_svc.heartbeat_chapter_edit_lease
    new_cid = _client_id(30)

    def boom_after_work(db_sess, **kwargs):
        result = real_flush_tail(db=db_sess, **kwargs)
        raise RuntimeError("simulated_heartbeat_failure_p13g1")

    monkeypatch.setattr(lease_svc, "heartbeat_chapter_edit_lease", boom_after_work)
    # 路由直接调 service 名；若 API 从模块导入了函数，需补丁 API 层
    monkeypatch.setattr(lease_api, "heartbeat_chapter_edit_lease", boom_after_work)
    res = _heartbeat(required_client, csrf, pid, new_cid, other)
    assert res.status_code == 500, res.text
    detail = res.json().get("detail")
    assert isinstance(detail, dict)
    dumped = json.dumps(detail, ensure_ascii=False)
    assert "simulated_heartbeat_failure" not in dumped
    assert "RuntimeError" not in res.text
    assert detail.get("code") == "chapter_edit_lease_failed"
    _assert_no_secrets(res.json())

    db = SessionLocal()
    try:
        digests = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT client_digest FROM project_chapter_edit_leases "
                    "WHERE project_id = :pid"
                ),
                {"pid": pid},
            ).fetchall()
        }
        assert _digest(new_cid) not in digests
        assert _digest(expired_cid) in digests  # 清理回滚
        assert _digest(live_cid) in digests
    finally:
        db.close()

    monkeypatch.setattr(lease_svc, "heartbeat_chapter_edit_lease", real_flush_tail)
    monkeypatch.setattr(lease_api, "heartbeat_chapter_edit_lease", real_flush_tail)

    # leave commit 失败 → 原租约仍在
    leave_cid = _client_id(28)
    assert _heartbeat(required_client, csrf, pid, leave_cid, other).status_code == 200
    real_leave = lease_api.leave_chapter_edit_lease

    def leave_then_poison(db_sess, **kwargs):
        real_leave(db=db_sess, **kwargs)

        def boom_commit() -> None:
            raise RuntimeError("simulated_commit_failure_leave_p13g1")

        db_sess.commit = boom_commit  # type: ignore[method-assign]

    monkeypatch.setattr(lease_api, "leave_chapter_edit_lease", leave_then_poison)
    leave_res = _leave(required_client, csrf, pid, leave_cid, other)
    assert leave_res.status_code == 500, leave_res.text
    assert "simulated_commit_failure" not in leave_res.text
    db = SessionLocal()
    try:
        n_leave = db.execute(
            text(
                "SELECT COUNT(*) FROM project_chapter_edit_leases "
                "WHERE project_id = :pid AND client_digest = :d"
            ),
            {"pid": pid, "d": _digest(leave_cid)},
        ).scalar()
        assert n_leave == 1
    finally:
        db.close()


def test_no_get_sse_query_websocket_and_put_not_blocked(required_client):
    """用途：无 GET/HEAD/query/SSE；editor-state PUT 不被租约强制拒绝。"""
    _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("honest")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    assert _heartbeat(required_client, csrf, pid, cid, ch).status_code == 200
    before = _count_leases(pid)
    assert before == 1

    # 基础路径无路由 → 404
    get_res = required_client.get(f"/api/projects/{pid}/chapter-edit-lease")
    assert get_res.status_code == 404, get_res.text
    # 已注册 POST 路径的 GET → 405
    get_hb = required_client.get(_hb_url(pid))
    assert get_hb.status_code == 405, get_hb.text
    # heartbeat HEAD 精确拒绝（FastAPI 对仅 POST 路由公开 405）
    head = required_client.head(_hb_url(pid))
    assert head.status_code == 405, head.status_code
    # 未知后缀无能力 → 404
    unknown = required_client.post(
        f"/api/projects/{pid}/chapter-edit-lease/stream",
        json={"clientId": cid, "chapterId": ch},
        headers={"X-CSRF-Token": csrf},
    )
    assert unknown.status_code == 404, unknown.text

    # query 不能替代 body：合法 query + 空/缺 body → 固定 422，零写副作用
    q_empty = required_client.post(
        f"{_hb_url(pid)}?clientId={cid}&chapterId={ch}",
        content=b"",
        headers={
            "X-CSRF-Token": csrf,
            "Content-Type": "application/json",
        },
    )
    assert q_empty.status_code == 422, q_empty.text
    q_detail = q_empty.json()["detail"]
    assert q_detail == {
        "code": "chapter_edit_lease_request_invalid",
        "message": "章节编辑意图请求无效",
    }
    _assert_no_store(q_empty)
    assert cid not in q_empty.text
    assert ch not in q_empty.text
    assert _count_leases(pid) == before

    # 持有租约后仍可 PUT 覆盖章节（诚实边界：非强制锁）
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {"id": ch, "title": "被覆盖", "body": "旧客户端仍可写"},
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert put.status_code == 200, put.text
    assert put.json()["chapters"][0]["title"] == "被覆盖"


def test_zero_sensitive_fields_in_success_and_errors(required_client):
    """用途：成功/错误响应零敏感内部字段。"""
    admin = _bootstrap()
    body = _login(required_client, _ADMIN_USER, _TEST_PASSWORD)
    csrf = _csrf(body)
    pid = _create_project(required_client, csrf)
    ch = _chapter_id("sens")
    _put_chapters(required_client, csrf, pid, _default_chapters(ch))
    cid = _client_id()
    ok = _heartbeat(required_client, csrf, pid, cid, ch)
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
        cid,
    ):
        assert bad not in text_all

    # 冲突错误也不泄漏
    res = _heartbeat(required_client, csrf, pid, _client_id(30), ch)
    assert res.status_code == 409, res.text
    assert admin.username in res.text
    assert cid not in res.text
    assert _digest(cid) not in res.text
    _assert_no_secrets(res.json())
