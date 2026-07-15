"""
模块：P12B-D1 editor-state 检查点原子恢复定向测试
用途：验收 restore API 的 CAS、安全检查点、13 键写回、裁剪保护、
  损坏/超限/回滚、并发、权限/CSRF、精确 Schema 与 no-store。
对接：POST .../editor-state-checkpoints/{id}/restore；
  editor_state_checkpoint_service.restore_editor_state_checkpoint；
  editor_state_service.apply_canonical_snapshot_to_locked_row。
二次开发：仅本地 SQLite 与固定合成口令；禁止外网与白名单外改动。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    ProjectEditorStateRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, editor_state_checkpoint_service, editor_state_service

_OWNER_USER = "admin_p12bd1_owner"
_OWNER_PASS = "TestPass-P12BD1-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12BD1-Writer-0001!",
    "finance": "TestPass-P12BD1-Finance-0001!",
    "hr": "TestPass-P12BD1-Hr-0001!",
    "bidder": "TestPass-P12BD1-Bidder-0001!",
}

_RESTORE_KEYS = frozenset(
    {
        "restoredCheckpointId",
        "safetyCheckpointId",
        "stateVersion",
        "restoredAt",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {
        "outline",
        "chapters",
        "facts",
        "mode",
        "analysis",
        "responseMatrix",
        "guidance",
        "parsedMarkdown",
        "businessQualify",
        "businessToc",
        "businessQuote",
        "businessCommit",
        "analysisOverview",
    }
)
_SECRET = "SECRET_P12BD1_SHOULD_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints"
_MAX_BYTES = 2 * 1024 * 1024
_ESV_OK = "esv_" + "a" * 32
_WS = "ws_local"


# ---------- fixtures / helpers ----------


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


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(disabled_settings):
    with TestClient(app) as client:
        yield client


def _cp_url(project_id: str, checkpoint_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-checkpoints"
    if checkpoint_id is None:
        return base
    return f"{base}/{checkpoint_id}"


def _restore_url(project_id: str, checkpoint_id: str) -> str:
    return f"{_cp_url(project_id, checkpoint_id)}/restore"


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
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
    return res.json()["csrfToken"], res.json()


def _create_member(client, csrf, *, username, password, role, is_owner=False):
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _login_role(client: TestClient, role: str, *, is_owner: bool = False) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p12bd1{'_own' if is_owner else ''}"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
        is_owner=is_owner,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _create_project(
    client: TestClient,
    name: str = "P12BD1技术标",
    kind: str = "technical",
    *,
    headers: dict | None = None,
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind},
        headers=headers or {},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _seed_technical_state(client: TestClient, pid: str, *, headers: dict | None = None) -> dict:
    outline = [
        {
            "id": "node_root",
            "title": "根节点",
            "children": [
                {"id": "node_a", "title": "子A", "children": []},
                {"id": "node_b", "title": "子B", "children": []},
            ],
        }
    ]
    chapters = [
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": "架构正文。",
            "status": "pending",
            "preview": "架构正文。",
            "wordCount": 5,
        },
        {
            "id": "chap_b",
            "title": "安全设计",
            "body": "安全正文。",
            "status": "done",
            "preview": "安全正文。",
            "wordCount": 5,
        },
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": outline,
            "chapters": chapters,
            "facts": [{"id": "fact_1", "text": "事实一"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": "分析概述",
                "techRequirements": ["要求甲"],
                "rejectionRisks": [],
                "scoringPoints": ["评分点1"],
            },
            "responseMatrix": [
                {
                    "sourceKey": "req:要求甲",
                    "kind": "requirement",
                    "sourceText": "要求甲",
                    "status": "partial",
                    "chapterIds": ["chap_a"],
                    "outlineNodeIds": ["node_a"],
                    "notes": "人工备注",
                }
            ],
            "guidance": {"hints": ["提示1"]},
            "parsedMarkdown": "# 招标文件\n正文",
            "analysisOverview": "分析概述",
        },
        headers=headers or {},
    )
    assert put.status_code == 200, put.text
    return put.json()


def _seed_business_state(client: TestClient, pid: str, *, headers: dict | None = None) -> dict:
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "mode": "ALIGNED",
            "businessQualify": [{"name": "资质A"}],
            "businessToc": [{"title": "目录一"}],
            "businessQuote": {"rows": [{"item": "报价项", "amount": 100}]},
            "businessCommit": [{"text": "承诺一"}],
            "analysisOverview": "商务概述",
        },
        headers=headers or {},
    )
    assert put.status_code == 200, put.text
    return put.json()


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(res, status: int, code: str) -> None:
    assert res.status_code == status, res.text
    _assert_no_store(res)
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    assert "SELECT" not in blob
    assert _PATH_MARKER not in blob


def _assert_409_version(res, current_sv: str) -> None:
    assert res.status_code == 409, res.text
    _assert_no_store(res)
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message", "currentStateVersion"}
    assert detail["code"] == "editor_state_version_conflict"
    assert detail["message"] == "编辑内容已被其他操作更新，请重新载入后再保存"
    assert detail["currentStateVersion"] == current_sv
    assert _SECRET not in res.text
    assert "Traceback" not in res.text
    assert "responseMatrix" not in detail
    assert "outline" not in detail


def _canonical_bytes(snapshot: dict) -> bytes:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _expected_version(snapshot: dict) -> str:
    digest = hashlib.sha256(_canonical_bytes(snapshot)).hexdigest()
    return "esv_" + digest[:32]


def _extract_13(state: dict) -> dict:
    return {k: state.get(k) for k in sorted(_SNAPSHOT_KEYS)}


def _db_cp_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .count()
        )
    finally:
        db.close()


def _db_cp_ids(project_id: str) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
            .all()
        )
        return [r.id for r in rows]
    finally:
        db.close()


def _db_get_cp(checkpoint_id: str) -> EditorStateCheckpointRow | None:
    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, checkpoint_id)
        if row is None:
            return None
        db.expunge(row)
        return row
    finally:
        db.close()


def _mutate_state(client: TestClient, pid: str, marker: str, *, headers: dict | None = None) -> dict:
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": f"fact_{marker}", "text": marker}]},
        headers=headers or {},
    )
    assert put.status_code == 200, put.text
    return put.json()


def _create_checkpoint(client: TestClient, pid: str, *, headers: dict | None = None) -> dict:
    res = client.post(_cp_url(pid), json={}, headers=headers or {})
    assert res.status_code == 201, res.text
    return res.json()


def _restore(
    client: TestClient,
    pid: str,
    cid: str,
    expected: str,
    *,
    headers: dict | None = None,
):
    return client.post(
        _restore_url(pid, cid),
        json={"expectedStateVersion": expected},
        headers=headers or {},
    )


# ---------- Schema / 权限 / CSRF ----------


def test_restore_schema_rejects_missing_snake_extra_blank_invalid(disabled_client):
    """用途：请求体严格 camelCase expected；缺失/snake/额外/空白/非法均 422。"""
    client = disabled_client
    pid = _create_project(client)
    state = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    before_cp = _db_cp_count(pid)
    before_sv = state["stateVersion"]

    cases = [
        {},
        {"expected_state_version": before_sv},
        {"expectedStateVersion": before_sv, "force": True},
        {"expectedStateVersion": before_sv, "snapshot": {"mode": "ALIGNED"}},
        {"expectedStateVersion": " " + before_sv},
        {"expectedStateVersion": before_sv + " "},
        {"expectedStateVersion": "esv_ABCDEF0123456789abcdef0123456789"},
        {"expectedStateVersion": "esv_short"},
        {"expectedStateVersion": "rmv_" + "a" * 32},
        {"expectedStateVersion": "esv_" + "g" * 32},
        {"expectedStateVersion": ""},
    ]
    for body in cases:
        res = client.post(_restore_url(pid, cid), json=body)
        assert res.status_code == 422, (body, res.status_code, res.text)

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == before_sv
    assert _db_cp_count(pid) == before_cp


def test_apply_canonical_snapshot_rejects_non_exact_keyset_before_orm_touch(
    disabled_client, monkeypatch
):
    """
    用途：apply_canonical_snapshot_to_locked_row 入口复用 CANONICAL_STATE_KEY_SET，
      非 dict / 缺键 / 额外键在新建或修改 ORM 行之前失败；异常后零写、零字段变化。
    """
    client = disabled_client
    pid = _create_project(client)
    seeded = _seed_technical_state(client, pid)
    assert isinstance(seeded.get("stateVersion"), str)

    key_set = editor_state_service.CANONICAL_STATE_KEY_SET
    # 必须复用权威集合，禁止测试侧硬编码 13 键字面量
    assert isinstance(key_set, frozenset) and len(key_set) >= 1

    full = {k: None for k in key_set}
    missing_one = {k: None for k in key_set if k != "facts"}
    extra_one = {**full, "forgedExtra": True}
    non_dicts = (None, [], "not-a-dict", 13, True)
    bad_cases = (*non_dicts, missing_one, extra_one)

    # ---- row is None：校验失败不得 db.add / 不得落库 ----
    for bad in bad_cases:
        db = SessionLocal()
        try:
            added_before = {id(obj) for obj in db.new}
            with pytest.raises(ValueError):
                editor_state_service.apply_canonical_snapshot_to_locked_row(
                    db, "proj_keyset_none", None, bad  # type: ignore[arg-type]
                )
            assert {id(obj) for obj in db.new} == added_before
            assert db.get(ProjectEditorStateRow, "proj_keyset_none") is None
        finally:
            db.rollback()
            db.close()

    # ---- 已有 ORM 行：校验失败后字段零变化，且校验先于任何 _dumps 写回 ----
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, pid)
        assert row is not None
        before = {
            "outline_json": row.outline_json,
            "chapters_json": row.chapters_json,
            "facts_json": row.facts_json,
            "mode": row.mode,
            "analysis_overview": row.analysis_overview,
            "analysis_json": row.analysis_json,
            "response_matrix_json": row.response_matrix_json,
            "guidance_json": row.guidance_json,
            "parsed_markdown": row.parsed_markdown,
            "business_json": row.business_json,
            "updated_at": row.updated_at,
        }

        write_hits = {"n": 0}
        real_dumps = editor_state_service._dumps

        def _count_dumps(value):
            write_hits["n"] += 1
            return real_dumps(value)

        monkeypatch.setattr(editor_state_service, "_dumps", _count_dumps)

        for bad in bad_cases:
            write_hits["n"] = 0
            with pytest.raises(ValueError):
                editor_state_service.apply_canonical_snapshot_to_locked_row(
                    db, pid, row, bad  # type: ignore[arg-type]
                )
            assert write_hits["n"] == 0, bad
            assert row.outline_json == before["outline_json"]
            assert row.chapters_json == before["chapters_json"]
            assert row.facts_json == before["facts_json"]
            assert row.mode == before["mode"]
            assert row.analysis_overview == before["analysis_overview"]
            assert row.analysis_json == before["analysis_json"]
            assert row.response_matrix_json == before["response_matrix_json"]
            assert row.guidance_json == before["guidance_json"]
            assert row.parsed_markdown == before["parsed_markdown"]
            assert row.business_json == before["business_json"]
            assert row.updated_at == before["updated_at"]

        # 合法精确键集仍可写回（防御不得误伤主路径）
        good = {k: None for k in key_set}
        good["mode"] = "ALIGNED"
        good["outline"] = [{"id": "n1", "title": "新", "children": []}]
        good["chapters"] = []
        good["facts"] = []
        good["analysis"] = {
            "overview": "",
            "techRequirements": [],
            "rejectionRisks": [],
            "scoringPoints": [],
        }
        good["responseMatrix"] = []
        good["guidance"] = {}
        good["parsedMarkdown"] = None
        good["businessQualify"] = []
        good["businessToc"] = []
        good["businessQuote"] = {"rows": [], "notes": ""}
        good["businessCommit"] = []
        good["analysisOverview"] = None
        assert frozenset(good.keys()) == key_set
        write_hits["n"] = 0
        out = editor_state_service.apply_canonical_snapshot_to_locked_row(
            db, pid, row, good
        )
        assert out is row
        assert write_hits["n"] > 0
        assert row.mode == "ALIGNED"
        assert row.outline_json is not None
        db.rollback()
    finally:
        db.close()


def test_auth_required_strict_bid_writer_csrf_owner_no_bypass(required_client):
    client = required_client

    for role in ("finance", "hr", "bidder"):
        csrf_role = _login_role(client, role)
        r = client.post(
            _restore_url("any", "escp_" + "0" * 32),
            json={"expectedStateVersion": _ESV_OK},
            headers={"X-CSRF-Token": csrf_role},
        )
        assert r.status_code == 403, (role, r.text)
        assert r.json()["detail"]["code"] == "role_forbidden"

    csrf_owner_finance = _login_role(client, "finance", is_owner=True)
    r_owner = client.post(
        _restore_url("any", "escp_" + "0" * 32),
        json={"expectedStateVersion": _ESV_OK},
        headers={"X-CSRF-Token": csrf_owner_finance},
    )
    assert r_owner.status_code == 403, r_owner.text
    assert r_owner.json()["detail"]["code"] == "role_forbidden"

    csrf_w = _login_role(client, "bid_writer")
    create = client.post(
        "/api/projects",
        json={"name": "required恢复", "kind": "technical"},
        headers={"X-CSRF-Token": csrf_w},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    state = _seed_technical_state(client, pid, headers={"X-CSRF-Token": csrf_w})
    cid = _create_checkpoint(client, pid, headers={"X-CSRF-Token": csrf_w})["checkpointId"]
    mutated = _mutate_state(client, pid, "后改", headers={"X-CSRF-Token": csrf_w})

    no_csrf = client.post(
        _restore_url(pid, cid),
        json={"expectedStateVersion": mutated["stateVersion"]},
    )
    assert no_csrf.status_code == 403, no_csrf.text
    assert no_csrf.json()["detail"]["code"] == "csrf_invalid"
    assert client.get(
        f"/api/projects/{pid}/editor-state",
        headers={"X-CSRF-Token": csrf_w},
    ).json()["stateVersion"] == mutated["stateVersion"]

    ok = _restore(
        client,
        pid,
        cid,
        mutated["stateVersion"],
        headers={"X-CSRF-Token": csrf_w},
    )
    assert ok.status_code == 200, ok.text
    _assert_no_store(ok)
    assert set(ok.json().keys()) == _RESTORE_KEYS
    assert ok.json()["stateVersion"] == state["stateVersion"]


def test_disabled_personal_mode_allows_restore(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    base = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "disabled-mut")
    res = _restore(client, pid, cid, cur["stateVersion"])
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == base["stateVersion"]
    assert after["facts"][0]["id"] == "fact_1"


# ---------- 成功路径：技术/商务 13 键 + 安全检查点 ----------


def test_restore_technical_13_keys_and_safety_equals_pre(disabled_client):
    """用途：技术标完整 13 键恢复；安全检查点详情精确等于恢复前权威状态。"""
    client = disabled_client
    pid = _create_project(client, kind="technical")
    target_state = _seed_technical_state(client, pid)
    target_cp = _create_checkpoint(client, pid)
    cid = target_cp["checkpointId"]
    target_sv = target_state["stateVersion"]
    assert target_cp["stateVersion"] == target_sv

    pre = _mutate_state(client, pid, "恢复前技术")
    pre_sv = pre["stateVersion"]
    assert pre_sv != target_sv
    pre_13 = _extract_13(pre)

    before_count = _db_cp_count(pid)
    res = _restore(client, pid, cid, pre_sv)
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _RESTORE_KEYS
    assert body["restoredCheckpointId"] == cid
    assert body["stateVersion"] == target_sv
    assert body["safetyCheckpointId"].startswith("escp_")
    assert body["safetyCheckpointId"] != cid
    assert type(body["restoredAt"]) is str and body["restoredAt"]

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == target_sv
    # 独立算法重算
    assert _expected_version(_extract_13(after)) == target_sv
    for key in _SNAPSHOT_KEYS:
        assert after.get(key) == target_state.get(key), key

    # 安全检查点详情 = 恢复前
    safety = client.get(_cp_url(pid, body["safetyCheckpointId"]))
    assert safety.status_code == 200, safety.text
    snap = safety.json()["snapshot"]
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    assert snap == pre_13
    assert safety.json()["stateVersion"] == pre_sv
    assert _db_cp_count(pid) == before_count + 1


def test_restore_business_13_keys(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="商务恢复", kind="business")
    target = _seed_business_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    pre = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "businessQualify": [{"name": "后改资质"}],
            "businessCommit": [{"text": "后改承诺"}],
        },
    )
    assert pre.status_code == 200
    res = _restore(client, pid, cid, pre.json()["stateVersion"])
    assert res.status_code == 200, res.text
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == target["stateVersion"]
    assert after["businessQualify"] == target["businessQualify"]
    assert after["businessToc"] == target["businessToc"]
    assert after["businessQuote"] == target["businessQuote"]
    assert after["businessCommit"] == target["businessCommit"]
    assert after["analysisOverview"] == target["analysisOverview"]


def test_restore_empty_and_same_content_creates_safety(disabled_client):
    """用途：空态恢复与同内容恢复均成功并创建安全检查点；同内容版本不变。"""
    client = disabled_client
    pid = _create_project(client)
    empty = client.get(f"/api/projects/{pid}/editor-state").json()
    empty_sv = empty["stateVersion"]
    empty_cp = _create_checkpoint(client, pid)
    assert empty_cp["stateVersion"] == empty_sv

    # 同内容恢复
    before = _db_cp_count(pid)
    same = _restore(client, pid, empty_cp["checkpointId"], empty_sv)
    assert same.status_code == 200, same.text
    body = same.json()
    assert body["stateVersion"] == empty_sv
    assert body["safetyCheckpointId"]
    assert _db_cp_count(pid) == before + 1
    after_same = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after_same["stateVersion"] == empty_sv

    # 有内容后再回到空态
    mut = _seed_technical_state(client, pid)
    back = _restore(client, pid, empty_cp["checkpointId"], mut["stateVersion"])
    assert back.status_code == 200, back.text
    assert back.json()["stateVersion"] == empty_sv
    restored = client.get(f"/api/projects/{pid}/editor-state").json()
    assert restored["stateVersion"] == empty_sv
    assert restored["chapters"] is None
    assert restored["outline"] is None


# ---------- 409 CAS / 并发 ----------


def test_stale_expected_409_zero_writes(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    target = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    v0 = target["stateVersion"]
    mid = _mutate_state(client, pid, "中态")
    before_cp = _db_cp_count(pid)

    res = _restore(client, pid, cid, v0)
    _assert_409_version(res, mid["stateVersion"])
    assert pid not in res.text
    assert cid not in res.text
    assert _SECRET not in res.text

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == mid["stateVersion"]
    assert after["facts"][0]["id"] == "fact_中态"
    assert _db_cp_count(pid) == before_cp


def test_concurrent_same_expected_one_success_one_conflict(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    base = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "并发前")
    expected = cur["stateVersion"]
    before_cp = _db_cp_count(pid)

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str | None]] = []
    conflict_cls = editor_state_service.EditorStateVersionConflict

    def worker(label: str) -> tuple[str, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                data = editor_state_checkpoint_service.restore_editor_state_checkpoint(
                    db,
                    _WS,
                    pid,
                    cid,
                    expected,
                )
                return ("ok", data["state_version"])
            except conflict_cls as exc:
                return ("conflict", exc.current_state_version)
            except editor_state_checkpoint_service.EditorStateCheckpointError as exc:
                return (f"cp_{exc.status_code}", exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, "甲"), pool.submit(worker, "乙")]
        outcomes = [f.result(timeout=30) for f in futures]

    statuses = sorted(o[0] for o in outcomes)
    assert statuses == ["conflict", "ok"], outcomes
    ok_sv = next(o[1] for o in outcomes if o[0] == "ok")
    assert ok_sv == base["stateVersion"]
    final = client.get(f"/api/projects/{pid}/editor-state").json()
    assert final["stateVersion"] == base["stateVersion"]
    # 成功 1 次 → 恰好 +1 安全检查点
    assert _db_cp_count(pid) == before_cp + 1


# ---------- 跨作用域 / 损坏 / 语义漂移 / 2MiB ----------


def test_missing_and_cross_scope_404(disabled_client):
    client = disabled_client
    a = _create_project(client, name="范围A")
    b = _create_project(client, name="范围B")
    _seed_technical_state(client, a)
    cid = _create_checkpoint(client, a)["checkpointId"]
    cur_b = client.get(f"/api/projects/{b}/editor-state").json()
    cur_a = client.get(f"/api/projects/{a}/editor-state").json()
    before_a = _db_cp_count(a)
    before_b = _db_cp_count(b)

    missing = _restore(client, a, "escp_" + "f" * 32, cur_a["stateVersion"])
    _assert_fixed_error(missing, 404, "editor_state_checkpoint_not_found")

    cross = _restore(client, b, cid, cur_b["stateVersion"])
    _assert_fixed_error(cross, 404, "editor_state_checkpoint_not_found")
    assert cid not in cross.text

    proj_missing = _restore(
        client, "proj_missing_p12bd1", "escp_" + "a" * 32, _ESV_OK
    )
    _assert_fixed_error(proj_missing, 404, "project_not_found")

    db = SessionLocal()
    try:
        if db.get(Workspace, "ws_other_p12bd1") is None:
            db.add(
                Workspace(
                    id="ws_other_p12bd1",
                    name="其他空间D1",
                    owner_user_id="user_other_d1",
                )
            )
            db.commit()
    finally:
        db.close()
    cross_ws = client.post(
        _restore_url(a, cid),
        json={"expectedStateVersion": cur_a["stateVersion"]},
        headers={"X-Workspace-Id": "ws_other_p12bd1"},
    )
    _assert_fixed_error(cross_ws, 404, "project_not_found")
    assert cid not in cross_ws.text

    assert _db_cp_count(a) == before_a
    assert _db_cp_count(b) == before_b
    assert client.get(f"/api/projects/{a}/editor-state").json()["stateVersion"] == cur_a[
        "stateVersion"
    ]


def test_restore_triple_scope_in_sql(disabled_client):
    """用途：目标读取必须 id+workspace+project 三重 SQL，禁止先全局 get。"""
    client = disabled_client
    a = _create_project(client, name="SQL-A")
    b = _create_project(client, name="SQL-B")
    _seed_technical_state(client, a)
    cid = _create_checkpoint(client, a)["checkpointId"]
    cur_b = client.get(f"/api/projects/{b}/editor-state").json()

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        lower = statement.lower()
        if "editor_state_checkpoints" not in lower:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _restore(client, b, cid, cur_b["stateVersion"])
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    _assert_fixed_error(res, 404, "editor_state_checkpoint_not_found")
    scoped = False
    for sql in captured:
        compact = " ".join(sql.split()).lower()
        where_match = re.search(r"(?is)\bwhere\b(.*)$", compact)
        if where_match is None:
            continue
        where_clause = where_match.group(1)
        has_id = bool(re.search(r"\bid\s*=", where_clause) or re.search(r"\.id\s*=", where_clause))
        if has_id and "workspace_id" in where_clause and "project_id" in where_clause:
            scoped = True
            break
    assert scoped, f"restore 目标未三重 SQL 限定: {captured}"


def _poison_checkpoint(cid: str, *, mode: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cid)
        assert row is not None
        if mode == "bad_json":
            row.snapshot_json = "{not-json"
            row.snapshot_bytes = len(row.snapshot_json.encode("utf-8"))
        elif mode == "bad_keys":
            payload = json.dumps(
                {"mode": "ALIGNED", "evil": _SECRET, "path": _PATH_MARKER},
                ensure_ascii=False,
            )
            row.snapshot_json = payload
            row.snapshot_bytes = len(payload.encode("utf-8"))
            row.state_version = "esv_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[
                :32
            ]
        elif mode == "bad_bytes":
            row.snapshot_bytes = row.snapshot_bytes + 7
        elif mode == "bad_version":
            row.state_version = "esv_" + "b" * 32
        elif mode == "bad_counts":
            row.outline_node_count = row.outline_node_count + 99
            row.chapter_count = row.chapter_count + 99
        elif mode == "noncanonical":
            data = json.loads(row.snapshot_json)
            noncanonical = json.dumps(
                {k: data[k] for k in reversed(sorted(data.keys()))},
                ensure_ascii=False,
                sort_keys=False,
                indent=2,
                separators=(", ", ": "),
            )
            row.snapshot_json = noncanonical
            row.snapshot_bytes = len(noncanonical.encode("utf-8"))
            row.state_version = "esv_" + hashlib.sha256(
                noncanonical.encode("utf-8")
            ).hexdigest()[:32]
        elif mode == "nan":
            data = json.loads(row.snapshot_json)
            data["analysisOverview"] = float("nan")
            poisoned = json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=True,
            )
            row.snapshot_json = poisoned
            row.snapshot_bytes = len(poisoned.encode("utf-8"))
            row.state_version = "esv_" + hashlib.sha256(poisoned.encode("utf-8")).hexdigest()[
                :32
            ]
        else:
            raise AssertionError(mode)
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize(
    "mode",
    [
        "bad_json",
        "bad_keys",
        "bad_bytes",
        "bad_version",
        "bad_counts",
        "noncanonical",
        "nan",
    ],
)
def test_corrupt_target_fixed_500_zero_write(disabled_client, mode):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, f"毒前-{mode}")
    before_cp = _db_cp_count(pid)
    before_sv = cur["stateVersion"]
    _poison_checkpoint(cid, mode=mode)

    res = _restore(client, pid, cid, before_sv)
    _assert_fixed_error(res, 500, "editor_state_checkpoint_corrupt")
    assert cid not in res.text
    assert _SECRET not in res.text
    assert "架构正文" not in res.text
    assert "NaN" not in res.text
    assert "Infinity" not in res.text

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == before_sv
    assert after["facts"][0]["id"] == f"fact_毒前-{mode}"
    assert _db_cp_count(pid) == before_cp


def test_semantic_drift_after_apply_fixed_500_rollback(disabled_client, monkeypatch):
    """用途：写回后重算版本 ≠ 目标 → 固定 corrupt，editor-state 与安全检查点双零写。"""
    client = disabled_client
    pid = _create_project(client)
    target = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "漂移前")
    before_cp = _db_cp_count(pid)
    before_sv = cur["stateVersion"]
    before_facts = cur["facts"]

    real_apply = editor_state_service.apply_canonical_snapshot_to_locked_row

    def _drift(db, project_id, row, snapshot):
        out = real_apply(db, project_id, row, snapshot)
        # 额外污染 facts，使结果版本偏离目标检查点
        out.facts_json = json.dumps(
            [{"id": "drift", "text": _SECRET}], ensure_ascii=False
        )
        return out

    monkeypatch.setattr(
        editor_state_service,
        "apply_canonical_snapshot_to_locked_row",
        _drift,
    )
    monkeypatch.setattr(
        "app.services.editor_state_checkpoint_service.editor_state_service.apply_canonical_snapshot_to_locked_row",
        _drift,
    )

    res = _restore(client, pid, cid, before_sv)
    _assert_fixed_error(res, 500, "editor_state_checkpoint_corrupt")
    assert _SECRET not in res.text
    assert cid not in res.text

    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == before_sv
    assert after["facts"] == before_facts
    assert after["stateVersion"] != target["stateVersion"]
    assert _db_cp_count(pid) == before_cp


def test_safety_snapshot_over_2mib_413_zero_write(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = client.get(f"/api/projects/{pid}/editor-state").json()
    before_cp = _db_cp_count(pid)

    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _huge_current(db, workspace_id, project_id, expected_state_version):
        row, state = real_lock(db, workspace_id, project_id, expected_state_version)
        huge = "汉" * (2 * 1024 * 1024)
        state = dict(state)
        state["parsedMarkdown"] = huge
        return row, state

    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        _huge_current,
    )
    monkeypatch.setattr(
        "app.services.editor_state_checkpoint_service.editor_state_service.lock_and_assert_expected_state_version",
        _huge_current,
    )

    res = _restore(client, pid, cid, cur["stateVersion"])
    _assert_fixed_error(res, 413, "editor_state_checkpoint_too_large")
    assert _db_cp_count(pid) == before_cp
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == cur["stateVersion"]


# ---------- 回滚域 / 无 refresh / 20 条安全保护 ----------


def test_insert_failure_rolls_back(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    target = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "插失败前")
    before_cp = _db_cp_count(pid)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_insert_checkpoint_row",
        _boom,
    )
    with pytest.raises(RuntimeError, match="simulated insert failure"):
        _restore(client, pid, cid, cur["stateVersion"])

    assert _db_cp_count(pid) == before_cp
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == cur["stateVersion"]
    assert after["facts"][0]["id"] == "fact_插失败前"
    assert after["stateVersion"] != target["stateVersion"]


def test_apply_failure_rolls_back_safety(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "写回失败前")
    before_cp = _db_cp_count(pid)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated apply failure")

    monkeypatch.setattr(
        editor_state_service,
        "apply_canonical_snapshot_to_locked_row",
        _boom,
    )
    monkeypatch.setattr(
        "app.services.editor_state_checkpoint_service.editor_state_service.apply_canonical_snapshot_to_locked_row",
        _boom,
    )
    with pytest.raises(RuntimeError, match="simulated apply failure"):
        _restore(client, pid, cid, cur["stateVersion"])

    assert _db_cp_count(pid) == before_cp
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == cur["stateVersion"]


def test_trim_failure_rolls_back(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "裁剪失败前")
    before_cp = _db_cp_count(pid)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated trim failure")

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_trim_checkpoints",
        _boom,
    )
    with pytest.raises(RuntimeError, match="simulated trim failure"):
        _restore(client, pid, cid, cur["stateVersion"])
    assert _db_cp_count(pid) == before_cp
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == cur["stateVersion"]


def test_commit_failure_rolls_back(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "提交失败前")
    before_cp = _db_cp_count(pid)

    real = editor_state_checkpoint_service.restore_editor_state_checkpoint

    def _wrapped(db, workspace_id, project_id, checkpoint_id, expected_state_version):
        real_commit = db.commit

        def _bad_commit(*args, **kwargs):
            raise RuntimeError("simulated commit failure")

        db.commit = _bad_commit  # type: ignore[method-assign]
        try:
            return real(db, workspace_id, project_id, checkpoint_id, expected_state_version)
        finally:
            db.commit = real_commit  # type: ignore[method-assign]

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "restore_editor_state_checkpoint",
        _wrapped,
    )
    monkeypatch.setattr(
        "app.api.editor_state_checkpoints.editor_state_checkpoint_service.restore_editor_state_checkpoint",
        _wrapped,
    )
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        _restore(client, pid, cid, cur["stateVersion"])
    assert _db_cp_count(pid) == before_cp
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["stateVersion"] == cur["stateVersion"]


def test_success_without_refresh_or_get_after_commit(disabled_client, monkeypatch):
    """用途：commit 后禁止 refresh / get_editor_state 重读。"""
    client = disabled_client
    pid = _create_project(client)
    target = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "无refresh")

    refresh_hits = {"n": 0}
    get_hits = {"n": 0}
    real_restore = editor_state_checkpoint_service.restore_editor_state_checkpoint
    real_get = editor_state_service.get_editor_state

    def _wrapped(db, workspace_id, project_id, checkpoint_id, expected_state_version):
        if not hasattr(db, "_p12bd1_refresh_wrapped"):

            def _bad_refresh(*args, **kwargs):
                refresh_hits["n"] += 1
                raise RuntimeError("refresh must not be called after commit")

            db.refresh = _bad_refresh  # type: ignore[method-assign]
            db._p12bd1_refresh_wrapped = True  # type: ignore[attr-defined]
        return real_restore(
            db, workspace_id, project_id, checkpoint_id, expected_state_version
        )

    def _count_get(db, workspace_id, project_id):
        get_hits["n"] += 1
        return real_get(db, workspace_id, project_id)

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "restore_editor_state_checkpoint",
        _wrapped,
    )
    monkeypatch.setattr(
        "app.api.editor_state_checkpoints.editor_state_checkpoint_service.restore_editor_state_checkpoint",
        _wrapped,
    )
    monkeypatch.setattr(editor_state_service, "get_editor_state", _count_get)
    monkeypatch.setattr(
        "app.services.editor_state_checkpoint_service.editor_state_service.get_editor_state",
        _count_get,
    )

    res = _restore(client, pid, cid, cur["stateVersion"])
    assert res.status_code == 200, res.text
    assert res.json()["stateVersion"] == target["stateVersion"]
    assert refresh_hits["n"] == 0
    # restore 路径不得依赖 get_editor_state（应用锁后 current_state + _state_from_row）
    assert get_hits["n"] == 0


def test_trim_protects_safety_when_20_exist(disabled_client):
    """用途：已有 20 条时恢复后仍精确 20；新安全检查点必保留；他项不误删。"""
    client = disabled_client
    pid = _create_project(client)
    other = _create_project(client, name="他项")
    other_cp = _create_checkpoint(client, other)["checkpointId"]

    # 目标检查点（较旧）
    base = _seed_technical_state(client, pid)
    target_cid = _create_checkpoint(client, pid)["checkpointId"]
    # 再填到 20
    for i in range(19):
        _mutate_state(client, pid, f"fill-{i}")
        assert client.post(_cp_url(pid), json={}).status_code == 201
    assert _db_cp_count(pid) == 20
    ids_before = set(_db_cp_ids(pid))
    assert target_cid in ids_before

    cur = client.get(f"/api/projects/{pid}/editor-state").json()
    res = _restore(client, pid, target_cid, cur["stateVersion"])
    assert res.status_code == 200, res.text
    safety_id = res.json()["safetyCheckpointId"]
    assert _db_cp_count(pid) == 20
    ids_after = set(_db_cp_ids(pid))
    assert safety_id in ids_after
    # 目标若被自然淘汰可接受；安全必须在
    assert len(ids_after) == 20
    # 他项不受影响
    assert _db_get_cp(other_cp) is not None
    assert client.get(_cp_url(other, other_cp)).status_code == 200


def test_trim_protects_safety_on_tied_created_at(disabled_client):
    """用途：并列 created_at + 不利 ID 排序时，新安全检查点仍不被裁剪。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    target_cid = _create_checkpoint(client, pid)["checkpointId"]

    # 人工塞满 20 条，且 created_at 全部相同、id 故意更大（DESC 排序更靠前）
    fixed_ts = utc_now()
    db = SessionLocal()
    try:
        # 清掉现有（含 target）后重建可控集合：保留 target + 19 条高 id
        existing = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == pid)
            .all()
        )
        target_row = db.get(EditorStateCheckpointRow, target_cid)
        assert target_row is not None
        snap = target_row.snapshot_json
        ver = target_row.state_version
        nbytes = target_row.snapshot_bytes
        # 必须保留与正文一致的 outline/chapter 计数，否则严格重验会固定 corrupt
        outline_n = int(target_row.outline_node_count)
        chapter_n = int(target_row.chapter_count)
        for row in existing:
            db.delete(row)
        db.flush()
        # target 用更旧时间
        old_ts = fixed_ts - timedelta(seconds=30)
        db.add(
            EditorStateCheckpointRow(
                id=target_cid,
                workspace_id=_WS,
                project_id=pid,
                snapshot_json=snap,
                state_version=ver,
                snapshot_bytes=nbytes,
                outline_node_count=outline_n,
                chapter_count=chapter_n,
                created_at=old_ts,
            )
        )
        for i in range(19):
            # id 前缀 z 保证 id DESC 时排在 escp_ 安全 id 之前（若时间相同）
            cid = f"escp_zzzzzzzzzzzzzzzzzzzzzzzzzzzz{i:02d}"[:37]
            # 固定长度 escp_ + 32 hex-like
            cid = f"escp_z{i:031d}"
            db.add(
                EditorStateCheckpointRow(
                    id=cid,
                    workspace_id=_WS,
                    project_id=pid,
                    snapshot_json=snap,
                    state_version=ver,
                    snapshot_bytes=nbytes,
                    outline_node_count=outline_n,
                    chapter_count=chapter_n,
                    created_at=fixed_ts,
                )
            )
        db.commit()
    finally:
        db.close()
    assert _db_cp_count(pid) == 20

    # 强制安全检查点使用与 19 条相同的 created_at，且 id 字典序更小
    real_insert = editor_state_checkpoint_service._insert_checkpoint_row
    forced_safety_id = "escp_" + "0" * 32

    def _insert_tied(db, **kwargs):
        kwargs = dict(kwargs)
        kwargs["checkpoint_id"] = forced_safety_id
        row = real_insert(db, **kwargs)
        row.created_at = fixed_ts
        db.flush()
        return row

    # 用 monkeypatch 于调用处
    import app.services.editor_state_checkpoint_service as cps

    original = cps._insert_checkpoint_row
    cps._insert_checkpoint_row = _insert_tied  # type: ignore[assignment]
    try:
        cur = client.get(f"/api/projects/{pid}/editor-state").json()
        res = _restore(client, pid, target_cid, cur["stateVersion"])
    finally:
        cps._insert_checkpoint_row = original  # type: ignore[assignment]

    assert res.status_code == 200, res.text
    assert res.json()["safetyCheckpointId"] == forced_safety_id
    assert _db_cp_count(pid) == 20
    assert forced_safety_id in _db_cp_ids(pid)


def test_stale_conflict_happens_before_any_safety_insert(disabled_client, monkeypatch):
    """用途：409 必须发生在任何安全插入之前。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    v0 = client.get(f"/api/projects/{pid}/editor-state").json()["stateVersion"]
    mid = _mutate_state(client, pid, "插前冲突")
    insert_hits = {"n": 0}

    real_insert = editor_state_checkpoint_service._insert_checkpoint_row

    def _count_insert(*args, **kwargs):
        insert_hits["n"] += 1
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_insert_checkpoint_row",
        _count_insert,
    )
    res = _restore(client, pid, cid, v0)
    _assert_409_version(res, mid["stateVersion"])
    assert insert_hits["n"] == 0


def test_response_updated_at_matches_editor_state(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    target = _seed_technical_state(client, pid)
    cid = _create_checkpoint(client, pid)["checkpointId"]
    cur = _mutate_state(client, pid, "时间对齐")
    res = _restore(client, pid, cid, cur["stateVersion"])
    assert res.status_code == 200, res.text
    body = res.json()
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert body["stateVersion"] == after["stateVersion"] == target["stateVersion"]
    assert body["restoredAt"] == after["updatedAt"]
