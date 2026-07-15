"""
模块：M3-D 融合写入持久恢复批次定向测试
用途：验收原子确认、任务权威、base/Unicode 校验、20 批裁剪、完整/部分/零恢复、
  并发至多一次成功、表约束索引、权限与 no-store；禁止假绿断言。
对接：content_fuse_applications API；content_fuse_application_service；
  ContentFuseApplicationBatchRow。
二次开发：仅本地 SQLite 与固定合成口令；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    ContentFuseApplicationBatchRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, content_fuse_application_service

_OWNER_USER = "admin_m3d_owner"
_OWNER_PASS = "TestPass-M3D-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-M3D-Writer-0001!",
    "finance": "TestPass-M3D-Finance-0001!",
    "hr": "TestPass-M3D-Hr-0001!",
    "bidder": "TestPass-M3D-Bidder-0001!",
}

_CREATE_KEYS = frozenset({"batchId", "appliedChapterCount", "createdAt", "stateVersion"})
_LIST_TOP = frozenset({"items"})
_LIST_ITEM_KEYS = frozenset(
    {"batchId", "chapterCount", "state", "createdAt", "consumedAt"}
)
_CONSUME_KEYS = frozenset(
    {"restoredChapterCount", "skippedChapterCount", "consumedAt", "stateVersion"}
)
_SECRET = "SECRET_M3D_SHOULD_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/content-fuse"


def _bh(body: str) -> str:
    return "bh_" + sha1(body.encode("utf-8")).hexdigest()[:20]


def _base(title: str, body: str) -> dict:
    return {
        "title": title.strip(),
        "bodyHash": _bh(body),
        "bodyLength": len(body),
    }


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


def _create_member(client, csrf, *, username, password, role):
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


def _login_role(client: TestClient, role: str) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_m3d"
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


def _create_project(client, name: str = "M3D技术标", kind: str = "technical") -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _seed_chapters(client, pid: str, chapters: list[dict] | None = None) -> dict:
    body_chapters = chapters or [
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": "现有架构正文。",
            "status": "pending",
            "preview": "现有架构正文。",
            "wordCount": 7,
        },
        {
            "id": "chap_b",
            "title": "安全设计",
            "body": "现有安全正文。",
            "status": "pending",
            "preview": "现有安全正文。",
            "wordCount": 7,
        },
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in body_chapters
            ],
            "chapters": body_chapters,
            "mode": "ALIGNED",
        },
    )
    assert put.status_code == 200, put.text
    return put.json()


def _seed_success_task(
    project_id: str,
    *,
    suggestions: list[dict],
    task_id: str | None = None,
    task_type: str = "content_fuse",
    status: str = "success",
) -> str:
    tid = task_id or f"task_m3d_{project_id[-8:]}_{len(suggestions)}"
    db = SessionLocal()
    try:
        row = ProjectTaskRow(
            id=tid,
            project_id=project_id,
            type=task_type,
            status=status,
            progress=100 if status == "success" else 0,
            message="ok",
            payload_json=_dumps_safe({"mode": "merge_suggest"}),
            result_json=_dumps_safe(
                {
                    "model": "mock-m3d",
                    "suggestions": suggestions,
                    "quota": {},
                }
            ),
            error=None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    return tid


def _dumps_safe(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _default_suggestions(
    body_a: str = "现有架构正文。",
    body_b: str = "现有安全正文。",
) -> list[dict]:
    return [
        {
            "suggestionId": "sug_a1",
            "targetChapterId": "chap_a",
            "targetTitle": "总体架构",
            "action": "merge",
            "proposedMarkdown": "融合后的架构正文。",
            "base": _base("总体架构", body_a),
            "sourceRefs": [{"kind": "template", "id": "tpl_x", "title": "T"}],
        },
        {
            "suggestionId": "sug_b1",
            "targetChapterId": "chap_b",
            "targetTitle": "安全设计",
            "action": "expand",
            "proposedMarkdown": "追加安全段落。",
            "base": _base("安全设计", body_b),
            "sourceRefs": [{"kind": "card", "id": "card_x", "title": "C"}],
        },
        {
            "suggestionId": "sug_a_rewrite",
            "targetChapterId": "chap_a",
            "targetTitle": "总体架构",
            "action": "rewrite",
            "proposedMarkdown": "重写架构。",
            "base": _base("总体架构", body_a),
            "sourceRefs": [{"kind": "template", "id": "tpl_x", "title": "T"}],
        },
        {
            "suggestionId": "sug_a_merge_suggest",
            "targetChapterId": "chap_a",
            "targetTitle": "总体架构",
            "action": "merge_suggest",
            "proposedMarkdown": "建议合并架构。",
            "base": _base("总体架构", body_a),
            "sourceRefs": [{"kind": "template", "id": "tpl_x", "title": "T"}],
        },
    ]


def _apply_url(pid: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications"


def _consume_url(pid: str, batch_id: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications/{batch_id}/consume"


def _assert_no_store(res):
    assert res.headers.get("Cache-Control") == "no-store"


def _assert_fixed_error(res, status: int, code: str):
    """用途：业务 404/409 固定码断言，并强制 Cache-Control: no-store。"""
    assert res.status_code == status, res.text
    assert res.headers.get("Cache-Control") == "no-store", res.headers
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()


def _chapter_bodies(client, pid: str) -> dict[str, str]:
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    return {
        c["id"]: c.get("body", "")
        for c in (state.get("chapters") or [])
        if isinstance(c, dict) and c.get("id")
    }


def _state_version(client, pid: str) -> str:
    """用途：读取当前服务端权威 stateVersion。"""
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    sv = state.get("stateVersion")
    assert isinstance(sv, str) and re.fullmatch(r"^esv_[0-9a-f]{32}$", sv), sv
    return sv


def _apply_json(client, pid: str, *, task_id: str, suggestion_ids: list[str], expected: str | None = None) -> dict:
    """用途：带强制 expectedStateVersion 的 apply 请求体。"""
    return {
        "taskId": task_id,
        "suggestionIds": suggestion_ids,
        "expectedStateVersion": expected if expected is not None else _state_version(client, pid),
    }


def _consume_json(client, pid: str, *, expected: str | None = None) -> dict:
    """用途：带强制 expectedStateVersion 的 consume 请求体。"""
    return {
        "expectedStateVersion": expected if expected is not None else _state_version(client, pid),
    }



# ---------- 表结构 ----------


def test_table_constraints_and_composite_index_exist(disabled_client):
    """用途：真实检查表字段、state CHECK、外键 CASCADE 与复合索引，禁止应用层假约束。"""
    insp = inspect(engine)
    assert "content_fuse_application_batches" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("content_fuse_application_batches")}
    assert cols == {
        "id",
        "workspace_id",
        "project_id",
        "task_id",
        "snapshot_json",
        "state",
        "created_at",
        "consumed_at",
    }
    fks = insp.get_foreign_keys("content_fuse_application_batches")
    fk_by_col = {
        tuple(f["constrained_columns"]): f for f in fks if f.get("constrained_columns")
    }
    ws_fk = fk_by_col[("workspace_id",)]
    proj_fk = fk_by_col[("project_id",)]
    assert ws_fk["referred_table"] == "workspaces"
    assert proj_fk["referred_table"] == "projects"
    # 精确断言两个 FK 均为 ON DELETE CASCADE
    assert (ws_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
    assert (proj_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"

    indexes = insp.get_indexes("content_fuse_application_batches")
    composite = None
    for ix in indexes:
        if list(ix.get("column_names") or []) == [
            "workspace_id",
            "project_id",
            "created_at",
        ]:
            composite = ix
            break
    assert composite is not None, indexes

    # CHECK：非法 state 必须以 IntegrityError 被数据库拒绝
    pid = _create_project(disabled_client)
    db = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO content_fuse_application_batches "
                    "(id, workspace_id, project_id, task_id, snapshot_json, state, created_at) "
                    "VALUES ('cfab_bad', 'ws_local', :pid, 'task_x', '{}', 'pending', :ts)"
                ),
                {"pid": pid, "ts": utc_now().isoformat()},
            )
            db.commit()
        db.rollback()
    finally:
        db.close()


# ---------- 失败先测核心路径（实现后应变绿） ----------


def test_apply_success_atomic_and_preview_status(disabled_client):
    """用途：merge+expand 原子写入、派生 preview/wordCount/status、建批次。"""
    from app.services.editor_state_service import compute_full_state_version

    client = disabled_client
    pid = _create_project(client)
    before = _seed_chapters(client, pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)

    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1", "sug_b1"]),
    )
    assert res.status_code == 201, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _CREATE_KEYS
    assert body["appliedChapterCount"] == 2
    assert str(body["batchId"]).startswith("cfab_")
    assert "taskId" not in body
    assert "suggestions" not in body
    # P12B-C3：201 必含合法 stateVersion；与 GET 及独立 13 键算法一致
    assert re.fullmatch(r"^esv_[0-9a-f]{32}$", body["stateVersion"]), body["stateVersion"]

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert state["stateVersion"] == body["stateVersion"]
    assert body["stateVersion"] == compute_full_state_version(state)
    by_id = {c["id"]: c for c in state["chapters"]}
    assert by_id["chap_a"]["body"] == "融合后的架构正文。"
    assert by_id["chap_a"]["status"] == "needs_review"
    assert by_id["chap_a"]["preview"]
    assert by_id["chap_a"]["wordCount"] == content_fuse_application_service.count_body_words(
        "融合后的架构正文。"
    )
    assert by_id["chap_b"]["body"] == "现有安全正文。\n\n追加安全段落。"
    assert by_id["chap_b"]["status"] == "needs_review"
    # 未选章节不变
    assert before["updatedAt"] is not None

    listed = client.get(_apply_url(pid))
    assert listed.status_code == 200
    _assert_no_store(listed)
    payload = listed.json()
    assert set(payload.keys()) == _LIST_TOP
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert set(item.keys()) == _LIST_ITEM_KEYS
    assert item["batchId"] == body["batchId"]
    assert item["chapterCount"] == 2
    assert item["state"] == "active"
    assert item["consumedAt"] is None
    assert "taskId" not in item
    assert "snapshot" not in item
    assert "beforeBody" not in json.dumps(payload, ensure_ascii=False)


def test_apply_rejects_extra_keys_and_forged_client_body(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    tid = _seed_success_task(pid, suggestions=_default_suggestions())
    res = client.post(
        _apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_a1"],
            "expectedStateVersion": _state_version(client, pid),
            "proposedMarkdown": "客户端伪造正文",
            "base": {"bodyHash": "bh_dead"},
            "action": "merge",
        },
    )
    assert res.status_code == 422, res.text
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == "现有架构正文。"


def test_apply_task_authority_not_client_text(disabled_client):
    """用途：建议正文仅来自任务 result_json，不接受客户端覆盖。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    sugs = _default_suggestions()
    sugs[0]["proposedMarkdown"] = "任务权威正文AAA"
    tid = _seed_success_task(pid, suggestions=sugs)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1"]),
    )
    assert res.status_code == 201, res.text
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == "任务权威正文AAA"


def test_apply_base_drift_and_unicode_hash_conflict(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(
        client,
        pid,
        [
            {
                "id": "chap_a",
                "title": "总体架构",
                "body": "当前已改。",
                "status": "pending",
            }
        ],
    )
    # 任务 base 仍是旧正文
    sugs = [
        {
            "suggestionId": "sug_a1",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "新正文",
            "base": _base("总体架构", "现有架构正文。"),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _chapter_bodies(client, pid)["chap_a"] == "当前已改。"
    assert client.get(_apply_url(pid)).json()["items"] == []


def test_apply_same_chapter_two_suggestions_conflict(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    tid = _seed_success_task(pid, suggestions=_default_suggestions())
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1", "sug_a_rewrite"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _chapter_bodies(client, pid)["chap_a"] == "现有架构正文。"


def test_apply_zero_change_conflict(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    body = "同一正文"
    _seed_chapters(
        client,
        pid,
        [{"id": "chap_a", "title": "总体架构", "body": body, "status": "pending"}],
    )
    sugs = [
        {
            "suggestionId": "sug_same",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": body,
            "base": _base("总体架构", body),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_same"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")


def test_apply_four_actions(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    chapters = [
        {"id": f"chap_{i}", "title": f"章{i}", "body": f"正文{i}", "status": "pending"}
        for i in range(4)
    ]
    _seed_chapters(client, pid, chapters)
    actions = ["merge", "expand", "rewrite", "merge_suggest"]
    sugs = []
    for i, action in enumerate(actions):
        body = f"正文{i}"
        sugs.append(
            {
                "suggestionId": f"sug_{action}",
                "targetChapterId": f"chap_{i}",
                "action": action,
                "proposedMarkdown": f"建议{action}",
                "base": _base(f"章{i}", body),
            }
        )
    tid = _seed_success_task(pid, suggestions=sugs)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=[s["suggestionId"] for s in sugs]),
    )
    assert res.status_code == 201, res.text
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_0"] == "建议merge"
    assert bodies["chap_1"] == "正文1\n\n建议expand"
    assert bodies["chap_2"] == "建议rewrite"
    assert bodies["chap_3"] == "建议merge_suggest"


def test_apply_unknown_task_business_cross_space_no_leak(disabled_client):
    client = disabled_client
    tech = _create_project(client, "技术")
    biz = _create_project(client, "商务", kind="business")
    _seed_chapters(client, tech)
    tid = _seed_success_task(tech, suggestions=_default_suggestions())

    # 商务标：合法版本格式即可进 service；kind 校验固定 404
    fake_sv = "esv_" + ("0" * 32)
    r1 = client.post(
        _apply_url(biz),
        json=_apply_json(
            client, tech, task_id=tid, suggestion_ids=["sug_a1"], expected=fake_sv
        ),
    )
    _assert_fixed_error(r1, 404, "project_not_found")
    assert tech not in r1.text and tid not in r1.text

    # 未知任务
    r2 = client.post(
        _apply_url(tech),
        json=_apply_json(
            client, tech, task_id="task_missing_xxx", suggestion_ids=["sug_a1"]
        ),
    )
    _assert_fixed_error(r2, 404, "content_fuse_task_not_found")
    assert "task_missing_xxx" not in r2.text

    # 错误类型
    bad = _seed_success_task(
        tech,
        suggestions=_default_suggestions(),
        task_id="task_wrong_type",
        task_type="outline",
    )
    r3 = client.post(
        _apply_url(tech),
        json=_apply_json(client, tech, task_id=bad, suggestion_ids=["sug_a1"]),
    )
    _assert_fixed_error(r3, 404, "content_fuse_task_not_found")

    # 跨空间：另一 workspace 项目
    db = SessionLocal()
    try:
        db.add(Workspace(id="ws_other_m3d", name="其他", owner_user_id="u_other"))
        db.add(
            Project(
                id="proj_other_m3d",
                workspace_id="ws_other_m3d",
                name="外空间",
                industry="通用",
                status="draft",
                kind="technical",
            )
        )
        db.commit()
    finally:
        db.close()
    r4 = client.get(
        "/api/projects/proj_other_m3d/content-fuse-applications",
        headers={"X-Workspace-Id": "ws_local"},
    )
    _assert_fixed_error(r4, 404, "project_not_found")
    assert "proj_other_m3d" not in r4.text


def test_list_trim_to_20_and_min_projection(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    # 准备 22 个独立任务/建议
    for i in range(22):
        body_now = _chapter_bodies(client, pid)["chap_a"]
        sug_id = f"sug_t{i}"
        proposed = f"第{i}次写入正文"
        sugs = [
            {
                "suggestionId": sug_id,
                "targetChapterId": "chap_a",
                "action": "merge",
                "proposedMarkdown": proposed,
                "base": _base("总体架构", body_now),
            }
        ]
        tid = _seed_success_task(
            pid, suggestions=sugs, task_id=f"task_trim_{i}"
        )
        res = client.post(
            _apply_url(pid),
            json=_apply_json(client, pid, task_id=tid, suggestion_ids=[sug_id]),
        )
        assert res.status_code == 201, res.text

    listed = client.get(_apply_url(pid)).json()
    assert len(listed["items"]) == 20
    # DB 层也必须只有 20
    db = SessionLocal()
    try:
        count = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
        assert count == 20
        # 不应误删其他项目
        other = _create_project(client, "其他项目")
        _seed_chapters(client, other)
        body_o = _chapter_bodies(client, other)["chap_a"]
        sugs = [
            {
                "suggestionId": "sug_other",
                "targetChapterId": "chap_a",
                "action": "merge",
                "proposedMarkdown": "其他项目正文",
                "base": _base("总体架构", body_o),
            }
        ]
        tid = _seed_success_task(other, suggestions=sugs, task_id="task_other_proj")
        assert (
            client.post(
                _apply_url(other),
                json=_apply_json(
                    client, other, task_id=tid, suggestion_ids=["sug_other"]
                ),
            ).status_code
            == 201
        )
        other_count = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == other)
            .count()
        )
        assert other_count == 1
        still = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
        assert still == 20
    finally:
        db.close()


def test_consume_full_partial_zero_and_once(disabled_client):
    from app.services.editor_state_service import compute_full_state_version

    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)
    applied_res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1", "sug_b1"]),
    )
    assert applied_res.status_code == 201, applied_res.text
    applied = applied_res.json()
    assert re.fullmatch(r"^esv_[0-9a-f]{32}$", applied["stateVersion"])
    batch_id = applied["batchId"]

    # 完整恢复
    c1 = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c1.status_code == 200, c1.text
    _assert_no_store(c1)
    body = c1.json()
    assert set(body.keys()) == _CONSUME_KEYS
    assert body["restoredChapterCount"] == 2
    assert body["skippedChapterCount"] == 0
    assert re.fullmatch(r"^esv_[0-9a-f]{32}$", body["stateVersion"]), body["stateVersion"]
    state_after_full = client.get(f"/api/projects/{pid}/editor-state").json()
    assert body["stateVersion"] == state_after_full["stateVersion"]
    assert body["stateVersion"] == compute_full_state_version(state_after_full)
    assert body["stateVersion"] != applied["stateVersion"]
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == "现有架构正文。"
    assert bodies["chap_b"] == "现有安全正文。"
    listed = client.get(_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "consumed"
    assert listed["consumedAt"] is not None

    # 再次消费 409
    c2 = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    _assert_fixed_error(c2, 409, "content_fuse_application_consumed")

    # 部分恢复：再应用，漂移一章
    body_a = _chapter_bodies(client, pid)["chap_a"]
    body_b = _chapter_bodies(client, pid)["chap_b"]
    sugs2 = [
        {
            "suggestionId": "sug_p_a",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "部分恢复A",
            "base": _base("总体架构", body_a),
        },
        {
            "suggestionId": "sug_p_b",
            "targetChapterId": "chap_b",
            "action": "merge",
            "proposedMarkdown": "部分恢复B",
            "base": _base("安全设计", body_b),
        },
    ]
    tid2 = _seed_success_task(pid, suggestions=sugs2, task_id="task_partial")
    batch2 = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid2, suggestion_ids=["sug_p_a", "sug_p_b"]),
    ).json()["batchId"]
    # 漂移 chap_a
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "用户手工改了A",
                    "status": "needs_review",
                },
                {
                    "id": "chap_b",
                    "title": "安全设计",
                    "body": "部分恢复B",
                    "status": "needs_review",
                },
            ]
        },
    )
    c3 = client.post(_consume_url(pid, batch2), json=_consume_json(client, pid))
    assert c3.status_code == 200, c3.text
    c3_body = c3.json()
    assert set(c3_body.keys()) == _CONSUME_KEYS
    assert c3_body["restoredChapterCount"] == 1
    assert c3_body["skippedChapterCount"] == 1
    assert re.fullmatch(r"^esv_[0-9a-f]{32}$", c3_body["stateVersion"])
    state_partial = client.get(f"/api/projects/{pid}/editor-state").json()
    assert c3_body["stateVersion"] == state_partial["stateVersion"]
    assert c3_body["stateVersion"] == compute_full_state_version(state_partial)
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == "用户手工改了A"  # 漂移不覆盖
    assert bodies["chap_b"] == body_b  # 恢复 before

    # 零恢复：两章都漂移
    body_a = _chapter_bodies(client, pid)["chap_a"]
    body_b = _chapter_bodies(client, pid)["chap_b"]
    sugs3 = [
        {
            "suggestionId": "sug_z_a",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "零恢复A",
            "base": _base("总体架构", body_a),
        },
        {
            "suggestionId": "sug_z_b",
            "targetChapterId": "chap_b",
            "action": "merge",
            "proposedMarkdown": "零恢复B",
            "base": _base("安全设计", body_b),
        },
    ]
    tid3 = _seed_success_task(pid, suggestions=sugs3, task_id="task_zero")
    batch3 = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid3, suggestion_ids=["sug_z_a", "sug_z_b"]),
    ).json()["batchId"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "漂移A",
                    "status": "needs_review",
                },
                {
                    "id": "chap_b",
                    "title": "安全设计",
                    "body": "漂移B",
                    "status": "needs_review",
                },
            ]
        },
    )
    c4 = client.post(_consume_url(pid, batch3), json=_consume_json(client, pid))
    assert c4.status_code == 200
    assert c4.json()["restoredChapterCount"] == 0
    assert c4.json()["skippedChapterCount"] == 2
    listed3 = [
        i for i in client.get(_apply_url(pid)).json()["items"] if i["batchId"] == batch3
    ][0]
    assert listed3["state"] == "consumed"


def test_consume_missing_batch_no_leak(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    res = client.post(_consume_url(pid, "cfab_missing_should_not_echo"), json=_consume_json(client, pid))
    _assert_fixed_error(res, 404, "content_fuse_application_not_found")
    assert "cfab_missing_should_not_echo" not in res.text
    assert _PATH_MARKER not in res.text


def test_concurrent_double_apply_at_most_one(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)
    barrier = threading.Barrier(2)
    outcomes: list[int] = []

    from app.services import editor_state_service as _ess

    expected = _state_version(client, pid)

    def worker():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                content_fuse_application_service.apply_content_fuse_application(
                    db,
                    "ws_local",
                    pid,
                    task_id=tid,
                    suggestion_ids=["sug_a1"],
                    expected_state_version=expected,
                )
                return 201
            except _ess.EditorStateVersionConflict:
                db.rollback()
                return 409
            except content_fuse_application_service.ContentFuseApplicationError as exc:
                db.rollback()
                return exc.status_code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    assert sorted(outcomes) == [201, 409], outcomes
    db = SessionLocal()
    try:
        n = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
        assert n == 1
    finally:
        db.close()
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == "融合后的架构正文。"


def test_concurrent_double_consume_at_most_one(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    tid = _seed_success_task(pid, suggestions=_default_suggestions())
    batch_id = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1"]),
    ).json()["batchId"]
    barrier = threading.Barrier(2)
    from app.services import editor_state_service as _ess

    expected = _state_version(client, pid)

    def worker():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                content_fuse_application_service.consume_content_fuse_application(
                    db,
                    "ws_local",
                    pid,
                    batch_id,
                    expected_state_version=expected,
                )
                return 200
            except _ess.EditorStateVersionConflict:
                db.rollback()
                return 409
            except content_fuse_application_service.ContentFuseApplicationError as exc:
                db.rollback()
                return exc.status_code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result(timeout=20) for f in [pool.submit(worker), pool.submit(worker)]]
    assert sorted(outcomes) == [200, 409], outcomes
    listed = client.get(_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "consumed"


def test_snapshot_over_2mib_rejected(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    huge = "汉" * (2 * 1024 * 1024)
    _seed_chapters(
        client,
        pid,
        [{"id": "chap_a", "title": "总体架构", "body": "小", "status": "pending"}],
    )
    sugs = [
        {
            "suggestionId": "sug_huge",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": huge,
            "base": _base("总体架构", "小"),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_huge")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_huge"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _chapter_bodies(client, pid)["chap_a"] == "小"
    db = SessionLocal()
    try:
        assert (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
            == 0
        )
    finally:
        db.close()


def test_failed_apply_leaves_no_half_batch(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    # 第二建议目标不存在 → 整批零写
    sugs = _default_suggestions()
    sugs[1]["targetChapterId"] = "chap_ghost"
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_half")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1", "sug_b1"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _chapter_bodies(client, pid)["chap_a"] == "现有架构正文。"
    assert client.get(_apply_url(pid)).json()["items"] == []


def test_auth_required_bid_writer_only(required_client):
    client = required_client
    # finance / hr / bidder 对 GET 精确 403 role_forbidden
    for role in ("finance", "hr", "bidder"):
        csrf_role = _login_role(client, role)
        r = client.get(
            "/api/projects/any/content-fuse-applications",
            headers={"X-CSRF-Token": csrf_role},
        )
        assert r.status_code == 403, (role, r.text)
        detail = r.json().get("detail")
        assert isinstance(detail, dict), r.text
        assert detail.get("code") == "role_forbidden", (role, detail)

    # bid_writer 精确可用
    csrf_w = _login_role(client, "bid_writer")
    create = client.post(
        "/api/projects",
        json={"name": "required技术标", "kind": "technical"},
        headers={"X-CSRF-Token": csrf_w},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "现有架构正文。",
                    "status": "pending",
                    "preview": "现有架构正文。",
                    "wordCount": 7,
                },
                {
                    "id": "chap_b",
                    "title": "安全设计",
                    "body": "现有安全正文。",
                    "status": "done",
                    "preview": "现有安全正文。",
                    "wordCount": 7,
                },
            ]
        },
        headers={"X-CSRF-Token": csrf_w},
    )
    assert put.status_code == 200, put.text
    # 首次应用使用独立任务
    tid = _seed_success_task(
        pid, suggestions=_default_suggestions(), task_id="task_auth_apply_ok"
    )
    apply = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1"]),
        headers={"X-CSRF-Token": csrf_w},
    )
    assert apply.status_code == 201, apply.text
    _assert_no_store(apply)

    # CSRF：使用尚未应用且 base 匹配的新任务；缺 token 精确 403 csrf_invalid
    body_a = _chapter_bodies(client, pid)["chap_a"]
    sugs_csrf = [
        {
            "suggestionId": "sug_csrf_only",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "CSRF应被拒绝不得写入",
            "base": _base("总体架构", body_a),
        }
    ]
    tid_csrf = _seed_success_task(
        pid, suggestions=sugs_csrf, task_id="task_auth_csrf_fresh"
    )
    before_bodies = _chapter_bodies(client, pid)
    db = SessionLocal()
    try:
        before_batches = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
    finally:
        db.close()
    no_csrf = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid_csrf, suggestion_ids=["sug_csrf_only"]),
    )
    assert no_csrf.status_code == 403, no_csrf.text
    detail_csrf = no_csrf.json().get("detail")
    assert isinstance(detail_csrf, dict), no_csrf.text
    assert detail_csrf.get("code") == "csrf_invalid"
    assert _chapter_bodies(client, pid) == before_bodies
    db = SessionLocal()
    try:
        after_batches = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
        assert after_batches == before_batches
    finally:
        db.close()


def test_preview_wordcount_utf16_rules():
    """用途：派生规则单测，对齐前端 UTF-16 截断与去空白字数。"""
    body = "# 标题\n**加粗** |> code _x_"
    preview = content_fuse_application_service.derive_preview(body)
    assert "标题" in preview
    assert "**" not in preview
    assert len(preview.encode("utf-16-le")) // 2 <= 96
    words = content_fuse_application_service.count_body_words("a b\n中文")
    assert words == content_fuse_application_service._utf16_len("ab中文")


def test_unicode_body_length_and_hash_match_fuse_context(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    body = "中文👍与ascii"
    _seed_chapters(
        client,
        pid,
        [{"id": "chap_a", "title": " 总体架构 ", "body": body, "status": "pending"}],
    )
    base = content_fuse_application_service.compute_chapter_base(" 总体架构 ", body)
    assert base["title"] == "总体架构"
    assert base["bodyLength"] == len(body)
    assert base["bodyHash"] == _bh(body)
    sugs = [
        {
            "suggestionId": "sug_u",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "替换",
            "base": base,
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs)
    assert (
        client.post(
            _apply_url(pid),
            json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_u"]),
        ).status_code
        == 201
    )


def test_empty_list_and_order(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    empty = client.get(_apply_url(pid))
    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    _assert_no_store(empty)


def test_snapshot_title_untrimmed_and_whitespace_title_skip(disabled_client):
    """用途：快照保存未 trim title；仅空白变化的标题在 consume 时必须 skipped。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(
        client,
        pid,
        [
            {
                "id": "chap_a",
                "title": " 总体架构 ",
                "body": "正文A",
                "status": "pending",
                "preview": "正文A",
                "wordCount": 3,
            }
        ],
    )
    sugs = [
        {
            "suggestionId": "sug_title_ws",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "融合A",
            "base": _base(" 总体架构 ", "正文A"),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_title_ws")
    applied = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_title_ws"]),
    )
    assert applied.status_code == 201, applied.text
    batch_id = applied.json()["batchId"]

    db = SessionLocal()
    try:
        row = db.get(ContentFuseApplicationBatchRow, batch_id)
        assert row is not None
        snap = json.loads(row.snapshot_json)
        assert snap["chapters"][0]["title"] == " 总体架构 "
        assert snap["chapters"][0]["beforeStatus"] == "pending"
    finally:
        db.close()

    # 标题仅空白变化 → 必须 skipped，正文不得回滚
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "融合A",
                    "status": "needs_review",
                }
            ]
        },
    )
    c = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c.status_code == 200, c.text
    _assert_no_store(c)
    assert c.json()["restoredChapterCount"] == 0
    assert c.json()["skippedChapterCount"] == 1
    assert _chapter_bodies(client, pid)["chap_a"] == "融合A"


def test_missing_status_pending_and_illegal_status_conflict(disabled_client):
    """用途：缺失状态按 pending 快照；非空非法状态整批冲突；恢复精确原状态。"""
    client = disabled_client
    pid = _create_project(client)
    # 缺失 status → pending
    _seed_chapters(
        client,
        pid,
        [
            {
                "id": "chap_a",
                "title": "总体架构",
                "body": "正文缺省状态",
                "preview": "正文缺省状态",
                "wordCount": 6,
            }
        ],
    )
    sugs = [
        {
            "suggestionId": "sug_miss_st",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "写入后",
            "base": _base("总体架构", "正文缺省状态"),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_miss_st")
    applied = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_miss_st"]),
    )
    assert applied.status_code == 201, applied.text
    batch_id = applied.json()["batchId"]
    db = SessionLocal()
    try:
        snap = json.loads(db.get(ContentFuseApplicationBatchRow, batch_id).snapshot_json)
        assert snap["chapters"][0]["beforeStatus"] == "pending"
        assert "draft" not in json.dumps(snap, ensure_ascii=False)
    finally:
        db.close()
    # 完整恢复后 status 精确为 pending
    c = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c.status_code == 200
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    by_id = {ch["id"]: ch for ch in state["chapters"]}
    assert by_id["chap_a"]["status"] == "pending"
    assert by_id["chap_a"]["body"] == "正文缺省状态"

    # 非空非法 status（draft）整批冲突零写
    _seed_chapters(
        client,
        pid,
        [
            {
                "id": "chap_a",
                "title": "总体架构",
                "body": "非法状态正文",
                "status": "draft_illegal",
            }
        ],
    )
    # editor-state 可能原样保存任意 status；服务层必须拒绝
    sugs2 = [
        {
            "suggestionId": "sug_bad_st",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "不应写入",
            "base": _base("总体架构", "非法状态正文"),
        }
    ]
    tid2 = _seed_success_task(pid, suggestions=sugs2, task_id="task_bad_st")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid2, suggestion_ids=["sug_bad_st"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _chapter_bodies(client, pid)["chap_a"] == "非法状态正文"
    assert client.get(_apply_url(pid)).json()["items"][0]["batchId"] == batch_id


def test_create_schema_camel_case_only_and_suggestion_id_bounds(disabled_client):
    """用途：仅 camelCase；snake_case 422；建议 ID 超长 422。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    tid = _seed_success_task(pid, suggestions=_default_suggestions(), task_id="task_schema")

    snake = client.post(
        _apply_url(pid),
        json={"task_id": tid, "suggestion_ids": ["sug_a1"]},
    )
    assert snake.status_code == 422, snake.text
    assert _chapter_bodies(client, pid)["chap_a"] == "现有架构正文。"

    too_long = "s" * 65
    long_id = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=[too_long]),
    )
    assert long_id.status_code == 422, long_id.text
    assert client.get(_apply_url(pid)).json()["items"] == []


def test_result_structure_rejects_silent_coercion(disabled_client):
    """用途：任务 result 禁止把非字符串/布尔整数静默强转；畸形选择固定 409 零写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    base = _base("总体架构", body_a)

    cases = [
        # proposedMarkdown 为 int
        {
            "suggestionId": "sug_pm_int",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": 12345,
            "base": base,
        },
        # action 为 list
        {
            "suggestionId": "sug_act_list",
            "targetChapterId": "chap_a",
            "action": ["merge"],
            "proposedMarkdown": "x",
            "base": base,
        },
        # suggestionId 为 int（索引不到 → 冲突）
        {
            "suggestionId": 99,
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": base,
        },
        # bodyHash 为 int
        {
            "suggestionId": "sug_hash_int",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": {**base, "bodyHash": 1},
        },
        # title 为 dict
        {
            "suggestionId": "sug_title_dict",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": {**base, "title": {"t": 1}},
        },
        # bodyLength 为 bool（int 子类）
        {
            "suggestionId": "sug_len_bool",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": {**base, "bodyLength": True},
        },
        # bodyLength 为字符串
        {
            "suggestionId": "sug_len_str",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": {**base, "bodyLength": str(base["bodyLength"])},
        },
        # bodyLength 负数
        {
            "suggestionId": "sug_len_neg",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "x",
            "base": {**base, "bodyLength": -1},
        },
    ]

    for i, sug in enumerate(cases):
        sid = sug["suggestionId"] if type(sug["suggestionId"]) is str else f"missing_{i}"
        tid = _seed_success_task(
            pid, suggestions=[sug], task_id=f"task_coerce_{i}"
        )
        # 请求里的 ID 必须是字符串；对 int suggestionId 用例用占位字符串触发 not found 冲突
        req_id = sid if type(sug["suggestionId"]) is str else "sug_not_in_task"
        res = client.post(
            _apply_url(pid),
            json=_apply_json(client, pid, task_id=tid, suggestion_ids=[req_id]),
        )
        _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
        assert _chapter_bodies(client, pid)["chap_a"] == body_a

    assert client.get(_apply_url(pid)).json()["items"] == []


def test_trim_exception_rolls_back_editor_and_batch(disabled_client, monkeypatch):
    """用途：章节已改且 batch flush 后裁剪抛异常 → 真实回滚；新 Session 见证零写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    tid = _seed_success_task(
        pid, suggestions=_default_suggestions(), task_id="task_tx_rollback"
    )
    before_bodies = _chapter_bodies(client, pid)
    before_state = client.get(f"/api/projects/{pid}/editor-state").json()

    def _boom(*_a, **_k):
        raise RuntimeError("simulated_trim_failure_m3d")

    monkeypatch.setattr(
        content_fuse_application_service, "_trim_batches", _boom
    )
    # TestClient 默认 raise_server_exceptions=True，会把未捕获异常原样抛出；
    # 依赖 get_db 的 finally 仍会 close/回滚未提交事务。
    with pytest.raises(RuntimeError, match="simulated_trim_failure_m3d"):
        client.post(
            _apply_url(pid),
            json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1"]),
        )

    # 新 Session 证明 editor-state 未变且批次为 0
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, pid)
        assert row is not None
        chapters = json.loads(row.chapters_json or "[]")
        by_id = {c["id"]: c for c in chapters if isinstance(c, dict)}
        assert by_id["chap_a"]["body"] == before_bodies["chap_a"]
        assert by_id["chap_a"]["status"] == "pending"
        n = (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == pid)
            .count()
        )
        assert n == 0
    finally:
        db.close()

    after_bodies = _chapter_bodies(client, pid)
    assert after_bodies == before_bodies
    assert client.get(_apply_url(pid)).json()["items"] == []
    # 防止误用 before_state 变量被优化掉
    assert before_state["chapters"][0]["body"] == before_bodies["chap_a"]


def test_restore_exact_original_statuses(disabled_client):
    """用途：写入前 generating/done 状态经恢复精确回写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(
        client,
        pid,
        [
            {
                "id": "chap_a",
                "title": "总体架构",
                "body": "现有架构正文。",
                "status": "generating",
                "preview": "现有架构正文。",
                "wordCount": 7,
            },
            {
                "id": "chap_b",
                "title": "安全设计",
                "body": "现有安全正文。",
                "status": "done",
                "preview": "现有安全正文。",
                "wordCount": 7,
            },
        ],
    )
    tid = _seed_success_task(pid, suggestions=_default_suggestions(), task_id="task_st_restore")
    batch_id = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_a1", "sug_b1"]),
    ).json()["batchId"]
    state_mid = client.get(f"/api/projects/{pid}/editor-state").json()
    mid = {c["id"]: c for c in state_mid["chapters"]}
    assert mid["chap_a"]["status"] == "needs_review"
    assert mid["chap_b"]["status"] == "needs_review"
    c = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c.status_code == 200
    assert c.json()["restoredChapterCount"] == 2
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    by_id = {ch["id"]: ch for ch in state["chapters"]}
    assert by_id["chap_a"]["status"] == "generating"
    assert by_id["chap_b"]["status"] == "done"
    assert by_id["chap_a"]["body"] == "现有架构正文。"
    assert by_id["chap_b"]["body"] == "现有安全正文。"


def _raw_chapters_json(project_id: str) -> str:
    """用途：直接读取库内 chapters_json 原始字节串，用于零写精确比对。"""
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        assert row is not None
        return row.chapters_json or ""
    finally:
        db.close()


def _set_raw_chapters_json(project_id: str, chapters: list) -> str:
    """用途：绕过 API 写入畸形 chapters，模拟历史/损坏 editor-state。"""
    payload = _dumps_safe(chapters)
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        assert row is not None
        row.chapters_json = payload
        row.updated_at = utc_now()
        db.commit()
    finally:
        db.close()
    return payload


def _batch_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == project_id)
            .count()
        )
    finally:
        db.close()


def test_apply_rejects_nondict_suggestion_beside_valid(disabled_client):
    """用途：合法被选建议旁存在非 dict 项 → 整批 409 零写，禁止静默丢弃。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    valid = {
        "suggestionId": "sug_ok",
        "targetChapterId": "chap_a",
        "action": "merge",
        "proposedMarkdown": "融合后正文。",
        "base": _base("总体架构", body_a),
    }
    tid = _seed_success_task(
        pid,
        suggestions=[valid, "not-a-dict", 42, None],
        task_id="task_nondict_sug",
    )
    before = _raw_chapters_json(pid)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_ok"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _raw_chapters_json(pid) == before
    assert json.loads(before)[0]["body"] == body_a
    assert _batch_count(pid) == 0
    assert client.get(_apply_url(pid)).json()["items"] == []


def test_apply_rejects_invalid_or_oversized_suggestion_id_items(disabled_client):
    """用途：合法被选旁存在非 str/空白/超长 suggestionId → 409 零写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    valid = {
        "suggestionId": "sug_ok_id",
        "targetChapterId": "chap_a",
        "action": "merge",
        "proposedMarkdown": "融合后正文。",
        "base": _base("总体架构", body_a),
    }
    cases = [
        {"suggestionId": 123, "targetChapterId": "chap_a", "action": "merge",
         "proposedMarkdown": "x", "base": _base("总体架构", body_a)},
        {"suggestionId": "   ", "targetChapterId": "chap_a", "action": "merge",
         "proposedMarkdown": "x", "base": _base("总体架构", body_a)},
        {"suggestionId": "s" * 65, "targetChapterId": "chap_a", "action": "merge",
         "proposedMarkdown": "x", "base": _base("总体架构", body_a)},
        {"suggestionId": None, "targetChapterId": "chap_a", "action": "merge",
         "proposedMarkdown": "x", "base": _base("总体架构", body_a)},
    ]
    for i, bad in enumerate(cases):
        tid = _seed_success_task(
            pid,
            suggestions=[valid, bad],
            task_id=f"task_bad_sid_{i}",
        )
        before = _raw_chapters_json(pid)
        res = client.post(
            _apply_url(pid),
            json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_ok_id"]),
        )
        _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
        assert _raw_chapters_json(pid) == before
        assert _batch_count(pid) == 0


def test_apply_rejects_duplicate_suggestion_ids_in_task_result(disabled_client):
    """用途：任务结果内重复 suggestionId → 409 零写，禁止 first-wins 成功。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    body_b = "现有安全正文。"
    first = {
        "suggestionId": "sug_dup",
        "targetChapterId": "chap_a",
        "action": "merge",
        "proposedMarkdown": "第一条文案。",
        "base": _base("总体架构", body_a),
    }
    second = {
        "suggestionId": "sug_dup",
        "targetChapterId": "chap_b",
        "action": "merge",
        "proposedMarkdown": "第二条文案。",
        "base": _base("安全设计", body_b),
    }
    tid = _seed_success_task(
        pid, suggestions=[first, second], task_id="task_dup_sid"
    )
    before = _raw_chapters_json(pid)
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_dup"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _raw_chapters_json(pid) == before
    bodies = _chapter_bodies(client, pid)
    assert bodies["chap_a"] == body_a
    assert bodies["chap_b"] == body_b
    assert _batch_count(pid) == 0


def test_apply_rejects_nondict_chapter_item_and_preserves_json(disabled_client):
    """用途：chapters 含合法目标 dict + 非 dict 原始项 → apply 409，字节级不变。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    raw_chapters = [
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": body_a,
            "status": "pending",
            "preview": body_a,
            "wordCount": 7,
        },
        "orphan_non_dict_item",
        99,
    ]
    before = _set_raw_chapters_json(pid, raw_chapters)
    parsed_before = json.loads(before)
    assert any(not isinstance(x, dict) for x in parsed_before)

    sugs = [
        {
            "suggestionId": "sug_ndc",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "不应写入",
            "base": _base("总体架构", body_a),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_ndc")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_ndc"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    after = _raw_chapters_json(pid)
    assert after == before
    assert json.loads(after) == parsed_before
    assert _batch_count(pid) == 0


def test_apply_rejects_duplicate_chapter_ids(disabled_client):
    """用途：重复 chapter ID → apply 409 零写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    raw_chapters = [
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": body_a,
            "status": "pending",
        },
        {
            "id": "chap_a",
            "title": "重复章",
            "body": "另一正文",
            "status": "pending",
        },
    ]
    before = _set_raw_chapters_json(pid, raw_chapters)
    sugs = [
        {
            "suggestionId": "sug_dup_chap",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "不应写入",
            "base": _base("总体架构", body_a),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_dup_chap")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_dup_chap"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _raw_chapters_json(pid) == before
    assert _batch_count(pid) == 0


def test_apply_rejects_non_str_title_or_body_on_selected_chapter(disabled_client):
    """用途：所选章 title 或 body 非原生 str → 409 零写。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"

    # title 为 int
    before_title = _set_raw_chapters_json(
        pid,
        [
            {
                "id": "chap_a",
                "title": 12345,
                "body": body_a,
                "status": "pending",
            }
        ],
    )
    sugs = [
        {
            "suggestionId": "sug_title_int",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "不应写入",
            # base title 用空串模拟旧逻辑把非 str 当空串后可能“对齐”的假绿路径
            "base": _base("", body_a),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_title_int")
    res = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_title_int"]),
    )
    _assert_fixed_error(res, 409, "content_fuse_apply_conflict")
    assert _raw_chapters_json(pid) == before_title
    assert _batch_count(pid) == 0

    # body 为 list
    before_body = _set_raw_chapters_json(
        pid,
        [
            {
                "id": "chap_a",
                "title": "总体架构",
                "body": ["not", "str"],
                "status": "pending",
            }
        ],
    )
    sugs2 = [
        {
            "suggestionId": "sug_body_list",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "不应写入",
            "base": _base("总体架构", ""),
        }
    ]
    tid2 = _seed_success_task(pid, suggestions=sugs2, task_id="task_body_list")
    res2 = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid2, suggestion_ids=["sug_body_list"]),
    )
    _assert_fixed_error(res2, 409, "content_fuse_apply_conflict")
    assert _raw_chapters_json(pid) == before_body
    assert _batch_count(pid) == 0


def test_consume_skips_non_str_live_title_body_and_does_not_overwrite(disabled_client):
    """用途：consume 前 live title/body 改成非 str → 该章 skipped，一次消费，畸形值不被覆盖。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_a = "现有架构正文。"
    sugs = [
        {
            "suggestionId": "sug_cons_ns",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "融合后正文。",
            "base": _base("总体架构", body_a),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_cons_ns")
    applied = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_cons_ns"]),
    )
    assert applied.status_code == 201, applied.text
    batch_id = applied.json()["batchId"]
    assert _chapter_bodies(client, pid)["chap_a"] == "融合后正文。"

    # 人为把 live title/body 改成非 str，模拟损坏态
    corrupted = [
        {
            "id": "chap_a",
            "title": {"broken": True},
            "body": 999,
            "status": "needs_review",
            "preview": "融合后正文。",
            "wordCount": 6,
        },
        {
            "id": "chap_b",
            "title": "安全设计",
            "body": "现有安全正文。",
            "status": "pending",
        },
    ]
    before_consume = _set_raw_chapters_json(pid, corrupted)

    c = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c.status_code == 200, c.text
    _assert_no_store(c)
    assert c.json()["restoredChapterCount"] == 0
    assert c.json()["skippedChapterCount"] == 1
    # 批次一次消费
    listed = client.get(_apply_url(pid)).json()["items"]
    assert len(listed) == 1
    assert listed[0]["batchId"] == batch_id
    assert listed[0]["state"] == "consumed"
    # 畸形 live 值不得被 before 覆盖
    after = _raw_chapters_json(pid)
    assert after == before_consume
    parsed = json.loads(after)
    by_id = {ch["id"]: ch for ch in parsed if isinstance(ch, dict)}
    assert by_id["chap_a"]["title"] == {"broken": True}
    assert by_id["chap_a"]["body"] == 999
    # 二次 consume 固定已消费
    again = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    _assert_fixed_error(again, 409, "content_fuse_application_consumed")


def test_consume_skips_duplicate_chapter_ids_as_drift(disabled_client):
    """用途：consume 时重复 chapter ID 必须视为漂移 skipped，禁止 first-wins 覆盖。

    反假绿：先正确 apply 得 active 批次；再把 editor-state 写成两个相同目标 ID，
    其中第一条 title/body/status 精确等于快照 after、第二条不同。旧 first-wins
    会恢复第一条（restored=1、body 回滚 before）；正确实现必须 restored=0、
    skipped=1、批次一次消费、两重复章与原始 JSON 精确不变。
    """
    client = disabled_client
    pid = _create_project(client)
    _seed_chapters(client, pid)
    body_before = "现有架构正文。"
    body_after = "融合后正文。"
    sugs = [
        {
            "suggestionId": "sug_dup_cons",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": body_after,
            "base": _base("总体架构", body_before),
        }
    ]
    tid = _seed_success_task(pid, suggestions=sugs, task_id="task_dup_cons")
    applied = client.post(
        _apply_url(pid),
        json=_apply_json(client, pid, task_id=tid, suggestion_ids=["sug_dup_cons"]),
    )
    assert applied.status_code == 201, applied.text
    batch_id = applied.json()["batchId"]
    assert _chapter_bodies(client, pid)["chap_a"] == body_after

    # 第一条精确等于 after（旧 first-wins 会命中并恢复）；第二条不同
    drifted = [
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": body_after,
            "status": "needs_review",
            "preview": body_after,
            "wordCount": len(body_after),
        },
        {
            "id": "chap_a",
            "title": "总体架构",
            "body": "另一条重复章正文，不可被 first-wins 误判",
            "status": "pending",
        },
        {
            "id": "chap_b",
            "title": "安全设计",
            "body": "现有安全正文。",
            "status": "pending",
        },
    ]
    before_consume = _set_raw_chapters_json(pid, drifted)

    c = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    assert c.status_code == 200, c.text
    _assert_no_store(c)
    assert c.json()["restoredChapterCount"] == 0
    assert c.json()["skippedChapterCount"] == 1

    listed = client.get(_apply_url(pid)).json()["items"]
    assert len(listed) == 1
    assert listed[0]["batchId"] == batch_id
    assert listed[0]["state"] == "consumed"

    after = _raw_chapters_json(pid)
    assert after == before_consume
    parsed = json.loads(after)
    assert parsed == drifted
    # 证明旧 first-wins 会失败：若恢复成功，第一条 body 会变回 before
    assert parsed[0]["body"] == body_after
    assert parsed[0]["body"] != body_before
    assert parsed[1]["body"] == "另一条重复章正文，不可被 first-wins 误判"
    assert parsed[1]["id"] == "chap_a"
    assert parsed[0]["id"] == "chap_a"

    again = client.post(_consume_url(pid, batch_id), json=_consume_json(client, pid))
    _assert_fixed_error(again, 409, "content_fuse_application_consumed")
