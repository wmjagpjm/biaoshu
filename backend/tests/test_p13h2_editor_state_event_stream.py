"""
模块：P13-H2 editor-state 事件 SSE 与断线重放专项测试
用途：failure-first 验收项目级 SSE 锚点、Last-Event-ID 重放、跨页、
  心跳、stale 两阶段、鉴权与隐私；禁止直接插事件表作主证据。
对接：GET /api/projects/{projectId}/editor-state-events/stream；
  editor_state_event_service 流页原语；H1 transition 真实写链。
二次开发：禁止宽状态/skip/xfail/源码字符串冒充行为；常量可 monkeypatch 缩短时钟。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import EditorStateEventRow, Project, utc_now
from app.services import auth_service

_WS = "ws_local"
_ESE_RE = re.compile(r"^ese_[0-9a-f]{32}$")
_ESV_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_OCCURRED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_CURSOR_KEYS = frozenset({"eventId"})
_EDITOR_KEYS = frozenset(
    {"eventId", "stateVersion", "sourceKind", "occurredAt"}
)
_ALL_SOURCES = frozenset(
    {
        "browser_put",
        "task",
        "revise",
        "callback",
        "local_parser",
        "content_fuse_apply",
        "content_fuse_consume",
        "checkpoint_restore",
        "revision_restore",
    }
)
_TEST_USERNAME = "admin_p13h2"
_TEST_PASSWORD = "P13h2-Test-Pass-9!"
_WRITER_PASSWORD = "P13h2-Writer-Pass-9!"
_SECRET_BODY = "SECRET_P13H2_BODY_MUST_NOT_LEAK"
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _WRITER_PASSWORD,
    _SECRET_BODY,
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
    "snapshot_json",
    "snapshotJson",
    "actor_user_id",
    "actorUserId",
    "clientId",
    "client_id",
    "workspace_id",
    "workspaceId",
    "Set-Cookie",
    "csrfToken",
)


# ---------- SSE 解析（保留 id/event/data/comment） ----------


def _parse_sse_frames(raw: str) -> list[dict[str, Any]]:
    """用途：精确解析 SSE 帧，保留 id/event/data/comment，不丢弃注释心跳。"""
    frames: list[dict[str, Any]] = []
    # 规范：空行分隔；末尾无空行时仍收尾
    blocks = re.split(r"\n\n", raw)
    for block in blocks:
        if block == "":
            continue
        # 去掉块前多余空行
        lines = block.split("\n")
        frame: dict[str, Any] = {
            "id": None,
            "event": None,
            "data": None,
            "comments": [],
            "raw": block,
        }
        data_lines: list[str] = []
        for line in lines:
            if line.startswith(":"):
                # 注释行：": heartbeat" 或 ":heartbeat"
                frame["comments"].append(line[1:].lstrip() if line.startswith(": ") else line[1:])
                continue
            if line.startswith("id:"):
                frame["id"] = line[3:].lstrip() if line.startswith("id: ") else line[3:]
                continue
            if line.startswith("event:"):
                frame["event"] = (
                    line[6:].lstrip() if line.startswith("event: ") else line[6:]
                )
                continue
            if line.startswith("data:"):
                data_lines.append(
                    line[5:].lstrip() if line.startswith("data: ") else line[5:]
                )
                continue
        if data_lines:
            raw_data = "\n".join(data_lines)
            frame["data_raw"] = raw_data
            frame["data"] = json.loads(raw_data)
        # 仅注释或有字段的块才算帧
        if frame["comments"] or frame["id"] is not None or frame["event"] is not None or data_lines:
            frames.append(frame)
    return frames


def _stream_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-events/stream"


def _assert_no_secrets(payload: object) -> None:
    text_blob = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text_blob, f"敏感标记泄漏: {marker}"
    low = text_blob.lower()
    for banned in (
        "snapshot",
        "actoruserid",
        "password",
        "token_digest",
        "csrftoken",
        "parsedmarkdown",
        "chapters",
    ):
        assert banned not in low.replace("_", ""), f"隐私字段泄漏: {banned}"


def _assert_editor_state_frame(frame: dict[str, Any], *, source: str | None = None) -> dict:
    assert frame["event"] == "editor-state"
    assert frame["id"] is not None and _ESE_RE.fullmatch(frame["id"])
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == _EDITOR_KEYS
    assert data["eventId"] == frame["id"]
    assert _ESE_RE.fullmatch(data["eventId"])
    assert _ESV_RE.fullmatch(data["stateVersion"])
    assert data["sourceKind"] in _ALL_SOURCES
    if source is not None:
        assert data["sourceKind"] == source
    assert _OCCURRED_RE.fullmatch(data["occurredAt"])
    assert not frame["comments"]
    _assert_no_secrets(data)
    return data


def _assert_cursor_frame(frame: dict[str, Any]) -> str:
    assert frame["event"] == "cursor"
    assert frame["id"] is not None and _ESE_RE.fullmatch(frame["id"])
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == _CURSOR_KEYS
    assert data["eventId"] == frame["id"]
    assert not frame["comments"]
    _assert_no_secrets(data)
    return data["eventId"]


def _assert_control_frame(frame: dict[str, Any], event_name: str, code: str) -> None:
    assert frame["event"] == event_name
    assert frame["id"] is None
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == {"code", "message"}
    assert data["code"] == code
    assert isinstance(data["message"], str) and data["message"]
    assert not frame["comments"]
    _assert_no_secrets(data)


def _assert_sse_headers(response) -> None:
    ct = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
    assert "text/event-stream" in ct
    assert "charset=utf-8" in ct.lower()
    cc = response.headers.get("Cache-Control") or ""
    assert "no-cache" in cc
    assert "no-store" in cc
    assert response.headers.get("X-Accel-Buffering") == "no"


def _assert_no_store_http(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store"


# ---------- fixtures / 构造 ----------


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


def _bootstrap_and_login(client: TestClient) -> tuple[str, str]:
    from app.models.entities import LocalUserRow

    db = SessionLocal()
    try:
        existing = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one_or_none()
        )
        if existing is None:
            try:
                auth_service.bootstrap_local_admin(
                    db,
                    get_settings(),
                    username=_TEST_USERNAME,
                    password=_TEST_PASSWORD,
                    role=auth_service.ROLE_BID_WRITER,
                )
            except Exception:
                db.rollback()
        row = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one()
        )
        user_id = row.id
    finally:
        db.close()
    client.cookies.clear()
    res = client.post(
        "/api/auth/login",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
    )
    assert res.status_code == 200, res.text
    return user_id, res.json()["csrfToken"]


def _create_project_http(
    client: TestClient, csrf: str, name: str = "P13H2项目"
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "mode": "technical", "bidDeadline": None},
        headers={"X-CSRF-Token": csrf},
    )
    if res.status_code not in (200, 201):
        res = client.post(
            "/api/projects",
            json={"name": name, "kind": "technical"},
            headers={"X-CSRF-Token": csrf},
        )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
):
    res = client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _put_editor(
    client: TestClient, csrf: str, project_id: str, tag: str
) -> str:
    res = client.put(
        f"/api/projects/{project_id}/editor-state",
        headers={"X-CSRF-Token": csrf},
        json={"parsedMarkdown": f"{tag}-{_SECRET_BODY}"},
    )
    assert res.status_code == 200, res.text
    sv = res.json()["stateVersion"]
    assert _ESV_RE.fullmatch(sv)
    return sv


def _db_events(project_id: str) -> list[EditorStateEventRow]:
    db = SessionLocal()
    try:
        return list(
            db.query(EditorStateEventRow)
            .filter(
                EditorStateEventRow.workspace_id == _WS,
                EditorStateEventRow.project_id == project_id,
            )
            .order_by(
                EditorStateEventRow.occurred_at.asc(),
                EditorStateEventRow.id.asc(),
            )
            .all()
        )
    finally:
        db.close()


def _short_stream_constants(monkeypatch, *, max_s: float = 1.2, hb: float = 0.35, poll: float = 0.05):
    """用途：可控时钟，避免 11 分钟阻塞；只 patch 路由模块常量。"""
    import app.api.editor_state_events as route_mod

    monkeypatch.setattr(route_mod, "_SSE_MAX_SECONDS", max_s, raising=False)
    monkeypatch.setattr(route_mod, "_SSE_HEARTBEAT_SECONDS", hb, raising=False)
    monkeypatch.setattr(route_mod, "_SSE_POLL_SECONDS", poll, raising=False)


def _get_stream(
    client: TestClient,
    project_id: str,
    *,
    last_event_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    headers: dict[str, str] = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    if extra_headers:
        headers.update(extra_headers)
    return client.get(_stream_url(project_id), headers=headers)


# ---------- 核心业务：锚点 / 重放 / 空表 / 跨页 ----------


def test_history_without_last_event_id_cursor_anchor_only(
    required_client, monkeypatch
):
    """已有历史无 Last-Event-ID：只发 cursor 锚点，不回放旧 editor-state；连接后新写入按序发送。"""
    _short_stream_constants(monkeypatch, max_s=2.0, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-anchor")
    sv1 = _put_editor(required_client, csrf, pid, "hist-1")
    sv2 = _put_editor(required_client, csrf, pid, "hist-2")
    rows = _db_events(pid)
    assert len(rows) >= 2
    tip_id = rows[-1].id

    # 后台在连接建立后写入新事件
    barrier = threading.Event()
    new_sv_holder: dict[str, str] = {}

    def _late_write():
        barrier.wait(timeout=5)
        time.sleep(0.15)
        new_sv_holder["sv"] = _put_editor(required_client, csrf, pid, "live-after-anchor")

    t = threading.Thread(target=_late_write, daemon=True)
    t.start()
    barrier.set()
    res = _get_stream(required_client, pid)
    t.join(timeout=5)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    assert frames, res.text

    # 首帧必须是 cursor 锚点 = tip，不得先回放 hist
    tip = _assert_cursor_frame(frames[0])
    assert tip == tip_id
    editor_frames = [f for f in frames if f["event"] == "editor-state"]
    # 历史两条不得出现
    hist_svs = {sv1, sv2}
    for ef in editor_frames:
        data = _assert_editor_state_frame(ef, source="browser_put")
        assert data["stateVersion"] not in hist_svs or data["stateVersion"] == new_sv_holder.get("sv")
    # 至少收到连接后新事件
    assert "sv" in new_sv_holder
    matched = [
        _assert_editor_state_frame(f)
        for f in editor_frames
        if f["data"]["stateVersion"] == new_sv_holder["sv"]
    ]
    assert len(matched) == 1
    _assert_no_secrets(res.text)


def test_last_event_id_replays_only_following_in_order(
    required_client, monkeypatch
):
    """有 Last-Event-ID：精确正序重放其后仍保留事件；id 与 data.eventId 一致。"""
    _short_stream_constants(monkeypatch, max_s=1.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-replay")
    versions: list[str] = []
    for i in range(4):
        versions.append(_put_editor(required_client, csrf, pid, f"rp-{i}"))
    rows = _db_events(pid)
    assert len(rows) >= 4
    after = rows[1].id  # 应收到 rows[2], rows[3] ...
    expected_ids = [r.id for r in rows[2:]]
    expected_svs = [r.state_version for r in rows[2:]]

    res = _get_stream(required_client, pid, last_event_id=after)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    editor_frames = [f for f in frames if f["event"] == "editor-state"]
    assert len(editor_frames) == len(expected_ids)
    for i, ef in enumerate(editor_frames):
        data = _assert_editor_state_frame(ef, source="browser_put")
        assert data["eventId"] == expected_ids[i]
        assert data["stateVersion"] == expected_svs[i]
    # 不得出现 cursor 锚点（已有 Last-Event-ID）
    assert all(f["event"] != "cursor" for f in frames)
    _assert_no_secrets(res.text)


def test_reconnect_with_last_received_id_only_following(
    required_client, monkeypatch
):
    """断线后以最后收到 ID 重连只收到后续事件。"""
    _short_stream_constants(monkeypatch, max_s=1.2, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-reconnect")
    for i in range(3):
        _put_editor(required_client, csrf, pid, f"rc-a-{i}")
    rows = _db_events(pid)
    first_after = rows[0].id
    res1 = _get_stream(required_client, pid, last_event_id=first_after)
    assert res1.status_code == 200, res1.text
    frames1 = _parse_sse_frames(res1.text)
    editors1 = [f for f in frames1 if f["event"] == "editor-state"]
    assert len(editors1) >= 2
    last_id = editors1[-1]["id"]
    assert last_id == rows[-1].id

    # 再写两条，以 last_id 重连
    sv_new1 = _put_editor(required_client, csrf, pid, "rc-b-1")
    sv_new2 = _put_editor(required_client, csrf, pid, "rc-b-2")
    res2 = _get_stream(required_client, pid, last_event_id=last_id)
    assert res2.status_code == 200, res2.text
    frames2 = _parse_sse_frames(res2.text)
    editors2 = [f for f in frames2 if f["event"] == "editor-state"]
    assert len(editors2) == 2
    assert _assert_editor_state_frame(editors2[0])["stateVersion"] == sv_new1
    assert _assert_editor_state_frame(editors2[1])["stateVersion"] == sv_new2
    # 旧事件不得重放
    old_ids = {r.id for r in rows}
    for ef in editors2:
        assert ef["id"] not in old_ids


def test_empty_table_first_and_following_events_sent(
    required_client, monkeypatch
):
    """空表连接后的第一条及连续事件必须作为 editor-state 发送，不被 bootstrap 吸收。"""
    _short_stream_constants(monkeypatch, max_s=2.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-empty")
    assert _db_events(pid) == []

    barrier = threading.Event()
    holder: dict[str, list[str]] = {"svs": []}

    def _writes():
        barrier.wait(timeout=5)
        time.sleep(0.12)
        holder["svs"].append(_put_editor(required_client, csrf, pid, "empty-1"))
        time.sleep(0.08)
        holder["svs"].append(_put_editor(required_client, csrf, pid, "empty-2"))

    t = threading.Thread(target=_writes, daemon=True)
    t.start()
    barrier.set()
    res = _get_stream(required_client, pid)
    t.join(timeout=6)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    # 空表无历史：不得先发 cursor 锚点（无 tip）
    cursor_frames = [f for f in frames if f["event"] == "cursor"]
    assert cursor_frames == []
    editors = [f for f in frames if f["event"] == "editor-state"]
    assert len(holder["svs"]) == 2
    assert len(editors) >= 2
    got_svs = [_assert_editor_state_frame(f, source="browser_put")["stateVersion"] for f in editors[:2]]
    assert got_svs == holder["svs"]


def test_cross_page_51_events_no_loss_dup_or_reorder(
    required_client, monkeypatch
):
    """51 条以上积压跨页：无丢失、重复、乱序；每页最多 50。"""
    _short_stream_constants(monkeypatch, max_s=3.0, hb=30.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-page51")
    # 水位事件
    _put_editor(required_client, csrf, pid, "base")
    base_rows = _db_events(pid)
    watermark = base_rows[0].id
    expected_ids: list[str] = []
    expected_svs: list[str] = []
    for i in range(51):
        sv = _put_editor(required_client, csrf, pid, f"p51-{i}")
        expected_svs.append(sv)
    rows = _db_events(pid)
    # 水位之后的全部
    after_rows = [r for r in rows if (r.occurred_at, r.id) > (base_rows[0].occurred_at, base_rows[0].id)]
    # 同毫秒时靠 id 排序；用 id 列表对齐
    after_ids = []
    seen_base = False
    for r in rows:
        if r.id == watermark:
            seen_base = True
            continue
        if seen_base:
            after_ids.append(r.id)
    assert len(after_ids) == 51, len(after_ids)
    expected_ids = after_ids

    res = _get_stream(required_client, pid, last_event_id=watermark)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    editors = [f for f in frames if f["event"] == "editor-state"]
    assert len(editors) == 51
    got_ids = []
    for ef in editors:
        data = _assert_editor_state_frame(ef, source="browser_put")
        got_ids.append(data["eventId"])
    assert got_ids == expected_ids
    assert len(set(got_ids)) == 51
    # stateVersion 与写链一致（顺序）
    got_svs = [f["data"]["stateVersion"] for f in editors]
    assert got_svs == expected_svs


# ---------- 心跳 / 最大时限 ----------


def test_heartbeat_is_comment_only_without_id(
    required_client, monkeypatch
):
    """空闲发送注释心跳 `: heartbeat`，无 id/event/data，不改变水位。"""
    _short_stream_constants(monkeypatch, max_s=1.0, hb=0.2, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-hb")
    # 有 tip：首帧 cursor，随后空闲心跳
    _put_editor(required_client, csrf, pid, "hb-seed")
    tip = _db_events(pid)[-1].id

    res = _get_stream(required_client, pid)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    assert frames
    assert _assert_cursor_frame(frames[0]) == tip
    comment_frames = [f for f in frames if f["comments"] and f["event"] is None and f["data"] is None]
    assert comment_frames, f"缺少心跳帧: {res.text!r}"
    for cf in comment_frames:
        assert cf["id"] is None
        assert any(c.strip() == "heartbeat" for c in cf["comments"])
        assert "id:" not in cf["raw"].split("\n")[0] or cf["id"] is None
    # 心跳后无额外 editor-state
    assert [f for f in frames if f["event"] == "editor-state"] == []


def test_max_duration_quiet_close(required_client, monkeypatch):
    """最大时限到达安静关闭，无伪造事件 id。"""
    _short_stream_constants(monkeypatch, max_s=0.4, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-max")
    _put_editor(required_client, csrf, pid, "max-seed")
    res = _get_stream(required_client, pid)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    # 仅 cursor，无 error/伪造
    assert frames[0]["event"] == "cursor"
    for f in frames:
        if f["event"] not in (None, "cursor"):
            # 允许 heartbeat comment
            assert f["event"] not in ("error", "timeout")
        if f["id"] is not None:
            assert _ESE_RE.fullmatch(f["id"])


# ---------- stale 两阶段 ----------


def test_connect_stale_unknown_trimmed_cross_project_409(required_client):
    """连接前：未知/裁剪/跨项目 Last-Event-ID 固定 409，不进流，不回显。"""
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-stale-pre")
    pid2 = _create_project_http(required_client, csrf, name="h2-stale-pre2")
    _put_editor(required_client, csrf, pid, "s1")
    _put_editor(required_client, csrf, pid2, "s2")
    other = _db_events(pid2)[0].id
    forged = "ese_" + "a" * 32
    for cursor in (forged, other, "ese_" + "0" * 32):
        res = _get_stream(required_client, pid, last_event_id=cursor)
        assert res.status_code == 409, res.text
        _assert_no_store_http(res)
        detail = res.json().get("detail") or res.json()
        if isinstance(detail, dict):
            assert detail.get("code") == "editor_state_event_cursor_stale"
        assert cursor not in res.text
        _assert_no_secrets(res.text)
        # 不得伪装成 SSE 200
        ct = (res.headers.get("content-type") or "").lower()
        assert "text/event-stream" not in ct


def test_midstream_cursor_stale_control_frame(required_client, monkeypatch):
    """连接中水位因 200 条裁剪失效：无 id 的 cursor-stale 控制帧后关闭。"""
    import app.api.editor_state_events as route_mod
    from app.services.editor_state_revision_service import (
        record_editor_state_transition,
    )
    from app.services import editor_state_service

    _short_stream_constants(monkeypatch, max_s=12.0, hb=30.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-stale-mid")
    # 先写一条作为水位（真实 PUT）
    _put_editor(required_client, csrf, pid, "mid-base")
    watermark = _db_events(pid)[0].id

    # 门闩：首轮流页读取在裁剪完成前阻塞，确保水位被 trim 掉后才真正 after 查询
    started = threading.Event()
    trimmed = threading.Event()
    real_read = route_mod._read_stream_page_sync

    def _gated_read(workspace_id: str, project_id: str, after: str | None):
        if after == watermark and not trimmed.is_set():
            started.set()
            assert trimmed.wait(timeout=60), "裁剪线程超时"
        return real_read(workspace_id, project_id, after)

    monkeypatch.setattr(route_mod, "_read_stream_page_sync", _gated_read)

    def _trim_via_real_transitions():
        assert started.wait(timeout=10), "流未开始读页"
        # 真实 transition 写链（非直接插事件表）触发 200 条裁剪，冲掉 watermark
        analysis = editor_state_service.empty_analysis()
        business = editor_state_service.empty_business()

        def _state(tag: str) -> dict[str, Any]:
            state: dict[str, Any] = {
                "outline": None,
                "chapters": [
                    {
                        "id": f"ch_{tag}",
                        "title": f"t{tag}",
                        "content": f"{_SECRET_BODY}-{tag}",
                    }
                ],
                "facts": None,
                "mode": "ALIGNED",
                "analysis": analysis,
                "responseMatrix": [],
                "guidance": f"g-{tag}",
                "parsedMarkdown": f"md-{tag}-{_SECRET_BODY}",
                "businessQualify": business["qualify"],
                "businessToc": business["toc"],
                "businessQuote": business["quote"],
                "businessCommit": business["commit"],
                "analysisOverview": analysis.get("overview", ""),
            }
            snap = editor_state_service.extract_canonical_snapshot(state)
            payload = editor_state_service.canonical_snapshot_json(snap)
            state["stateVersion"] = (
                editor_state_service.compute_state_version_from_canonical_json(
                    payload
                )
            )
            return state

        prev = _state("m0")
        # 用库内当前 after 状态对齐：从 watermark 对应版本继续写 201 次
        cur_rows = _db_events(pid)
        assert cur_rows and cur_rows[0].id == watermark
        # before/after 真实变化即可；201 次 after 使总数>=202 → 裁到 200，最早 watermark 失效
        for i in range(201):
            nxt = _state(f"m{i+1}")
            db = SessionLocal()
            try:
                record_editor_state_transition(
                    db,
                    _WS,
                    pid,
                    before_state=prev,
                    after_state=nxt,
                    source_kind="browser_put",
                    actor_user_id=None,
                )
                db.commit()
            finally:
                db.close()
            prev = nxt
        # 确认 watermark 已被裁剪
        ids = {r.id for r in _db_events(pid)}
        assert watermark not in ids
        assert len(ids) == 200
        trimmed.set()

    t = threading.Thread(target=_trim_via_real_transitions, daemon=True)
    t.start()
    res = _get_stream(required_client, pid, last_event_id=watermark)
    t.join(timeout=120)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    stale_frames = [f for f in frames if f["event"] == "cursor-stale"]
    assert stale_frames, f"缺少 cursor-stale: {res.text[:800]!r}"
    _assert_control_frame(
        stale_frames[0],
        "cursor-stale",
        "editor_state_event_cursor_stale",
    )
    last_stale_idx = max(
        i for i, f in enumerate(frames) if f["event"] == "cursor-stale"
    )
    after = frames[last_stale_idx + 1 :]
    assert all(f["event"] != "editor-state" for f in after)
    _assert_no_secrets(res.text)


# ---------- 鉴权 / 语法 / 方法 ----------


def test_auth_scope_matrix_exact_status(required_client):
    """required 401；非 bid_writer/活动空间/任意 X-Workspace-Id 403；跨项目 404。"""
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-auth")
    _put_editor(required_client, csrf, pid, "auth-seed")

    # 未登录 401
    required_client.cookies.clear()
    unauth = _get_stream(required_client, pid)
    assert unauth.status_code == 401, unauth.text
    _assert_no_secrets(unauth.text)
    if unauth.headers.get("Cache-Control") is not None:
        _assert_no_store_http(unauth)

    # 重登 + 任意 X-Workspace-Id → 403
    _bootstrap_and_login(required_client)
    for val in ("", "ws_other", _WS, " "):
        res = _get_stream(
            required_client, pid, extra_headers={"X-Workspace-Id": val}
        )
        assert res.status_code == 403, (val, res.text)
        _assert_no_store_http(res)
        _assert_no_secrets(res.text)

    # finance 403
    fin_user = f"fin_p13h2_{secrets.token_hex(3)}"
    _uid2, csrf2 = _bootstrap_and_login(required_client)
    _create_member(
        required_client,
        csrf2,
        username=fin_user,
        password="P13h2-Finance-Pass!",
        role=auth_service.ROLE_FINANCE,
    )
    required_client.cookies.clear()
    login_f = required_client.post(
        "/api/auth/login",
        json={"username": fin_user, "password": "P13h2-Finance-Pass!"},
    )
    assert login_f.status_code == 200, login_f.text
    fin = _get_stream(required_client, pid)
    assert fin.status_code == 403, fin.text
    _assert_no_store_http(fin)
    _assert_no_secrets(fin.text)

    # writer 跨项目/不存在 404
    _bootstrap_and_login(required_client)
    missing = _get_stream(required_client, "proj_does_not_exist_p13h2")
    assert missing.status_code == 404, missing.text
    _assert_no_store_http(missing)
    assert "proj_does_not_exist_p13h2" not in missing.text
    _assert_no_secrets(missing.text)


def test_invalid_request_syntax_422(required_client):
    """未知 query、非空 body、非法/重复/空 Last-Event-ID 固定 422，不回显。"""
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-422")
    _put_editor(required_client, csrf, pid, "q-seed")

    # 未知 query
    res_q = required_client.get(f"{_stream_url(pid)}?foo=1")
    assert res_q.status_code == 422, res_q.text
    _assert_no_store_http(res_q)
    _assert_no_secrets(res_q.text)

    # 非空 body
    res_b = required_client.request(
        "GET",
        _stream_url(pid),
        content=b'{"x":1}',
        headers={"Content-Type": "application/json"},
    )
    assert res_b.status_code == 422, res_b.text
    _assert_no_store_http(res_b)

    # 非法 Last-Event-ID 格式
    leak = "ese_NOT_HEX_SHOULD_NOT_ECHO_XXXXXX"
    for bad in (
        "",
        " ",
        "ese_" + "A" * 32,
        "ese_" + "a" * 31,
        "ese_" + "a" * 33,
        "esr_" + "a" * 32,
        "not-an-id",
        leak,
        " ese_" + "a" * 32,
        "ese_" + "a" * 32 + " ",
    ):
        res = _get_stream(required_client, pid, last_event_id=bad)
        assert res.status_code == 422, (bad, res.status_code, res.text)
        _assert_no_store_http(res)
        if bad.strip():
            assert bad not in res.text
        _assert_no_secrets(res.text)

    # 重复 Last-Event-ID 头
    good = "ese_" + "b" * 32
    res_dup = required_client.get(
        _stream_url(pid),
        headers=[
            ("Last-Event-ID", good),
            ("Last-Event-ID", good),
        ],
    )
    assert res_dup.status_code == 422, res_dup.text
    _assert_no_store_http(res_dup)
    assert good not in res_dup.text


def test_methods_other_than_get_exact_405(required_client):
    """已登录 strict bid_writer 下 stream 仅 GET；其它方法精确 405。"""
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-method")
    url = _stream_url(pid)
    safe_body = {"probe": "p13h2-method-check"}
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
    for method in ("post", "put", "patch", "delete"):
        if method == "delete":
            res = required_client.request(
                "DELETE", url, headers=headers, json=safe_body
            )
        else:
            res = getattr(required_client, method)(
                url, headers=headers, json=safe_body
            )
        assert res.status_code == 405, (method, res.status_code, res.text)
        _assert_no_secrets(res.text)


# ---------- Session 关闭 / 隐私 ----------


class _TrackingSession:
    """用途：包装 Session，记录 close。"""

    def __init__(self, real):
        self._real = real
        self.closed = False

    def close(self):
        self.closed = True
        return self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_connect_and_stream_sessions_closed(required_client, monkeypatch):
    """
    连接前与流内短 Session 均关闭；
    H2 stream 不得触发 request-scope get_db（dependency override 零次调用）。
    """
    import app.api.editor_state_events as route_mod
    import app.core.database as database_mod
    import app.services.editor_state_event_service as svc_mod
    from app.core.database import get_db as real_get_db

    _short_stream_constants(monkeypatch, max_s=0.6, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-sess")
    _put_editor(required_client, csrf, pid, "sess-seed")

    real_local = SessionLocal
    tracked: list[_TrackingSession] = []
    get_db_calls: list[str] = []

    def factory():
        wrapped = _TrackingSession(real_local())
        tracked.append(wrapped)
        return wrapped

    def forbidden_get_db():
        # 一旦 stream 依赖路径触发 request-scope get_db，立即固定失败
        get_db_calls.append("get_db")
        raise AssertionError(
            "H2 stream 不得触发 request-scope get_db Session"
        )

    monkeypatch.setattr(route_mod, "SessionLocal", factory, raising=False)
    # 服务层若直接用 database.SessionLocal，也跟踪
    monkeypatch.setattr(svc_mod, "SessionLocal", factory, raising=False)
    # 覆盖 core.database.SessionLocal：get_db 内部走此工厂，便于发现漏网
    monkeypatch.setattr(database_mod, "SessionLocal", factory, raising=True)
    # 依赖层与模块级引用同时拦截
    monkeypatch.setattr(database_mod, "get_db", forbidden_get_db, raising=True)
    monkeypatch.setattr(route_mod, "get_db", forbidden_get_db, raising=False)
    app.dependency_overrides[real_get_db] = forbidden_get_db
    try:
        res = _get_stream(required_client, pid)
        assert res.status_code == 200, res.text
        assert get_db_calls == [], (
            f"stream 期间 get_db 被调用 {len(get_db_calls)} 次"
        )
        assert tracked, "应至少创建短 Session"
        assert all(s.closed for s in tracked), "存在未关闭 Session"
        frames = _parse_sse_frames(res.text)
        assert frames and frames[0]["event"] == "cursor"
    finally:
        app.dependency_overrides.pop(real_get_db, None)


def test_privacy_no_snapshot_actor_project_or_auth_material(
    required_client, monkeypatch
):
    """成功帧/心跳/控制帧/错误均无正文、快照、actor、项目空间 ID、认证材料。"""
    _short_stream_constants(monkeypatch, max_s=0.8, hb=0.2, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-priv")
    _put_editor(required_client, csrf, pid, "priv")
    res = _get_stream(required_client, pid)
    assert res.status_code == 200, res.text
    assert pid not in res.text
    assert _WS not in res.text
    _assert_no_secrets(res.text)
    for f in _parse_sse_frames(res.text):
        if f["data"] is not None:
            _assert_no_secrets(f["data"])

    # 错误路径
    bad = _get_stream(required_client, pid, last_event_id="ese_" + "c" * 32)
    assert bad.status_code == 409
    assert pid not in bad.text
    _assert_no_secrets(bad.text)


def test_h1_get_route_still_registered_unchanged_shape(required_client):
    """H1 GET 仍可用且形状不变（本包不得破坏 list 端点）。"""
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h2-h1-reg")
    _put_editor(required_client, csrf, pid, "h1-reg")
    res = required_client.get(f"/api/projects/{pid}/editor-state-events")
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"items", "nextCursor", "hasMore"}
    assert body["items"] == []
    assert body["hasMore"] is False
    assert body["nextCursor"] is not None and _ESE_RE.fullmatch(body["nextCursor"])
