"""
模块：P12A editor-state 手动检查点只读库定向测试
用途：验收创建/列表/详情、13 键规范快照、20 条裁剪、SQL 元数据投影、
  表约束/索引/FK cascade、2 MiB、并发、权限/CSRF、损坏脱敏与未实现方法。
对接：editor_state_checkpoints API；editor_state_checkpoint_service；
  EditorStateCheckpointRow。
二次开发：仅本地 SQLite 与固定合成口令；禁止外网、真实业务口令或白名单外改动。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, editor_state_checkpoint_service

_OWNER_USER = "admin_p12a_owner"
_OWNER_PASS = "TestPass-P12A-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12A-Writer-0001!",
    "finance": "TestPass-P12A-Finance-0001!",
    "hr": "TestPass-P12A-Hr-0001!",
    "bidder": "TestPass-P12A-Bidder-0001!",
}

_META_KEYS = frozenset(
    {
        "checkpointId",
        "stateVersion",
        "snapshotBytes",
        "outlineNodeCount",
        "chapterCount",
        "createdAt",
        "displayName",
    }
)
_DETAIL_KEYS = _META_KEYS | frozenset({"snapshot"})
_LIST_TOP = frozenset({"items"})
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
_FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    {
        "projectId",
        "updatedAt",
        "responseMatrixVersion",
        "workspaceId",
        "name",
        "userId",
        "csrf",
        "apiKey",
        "taskId",
        "batchId",
    }
)
_SECRET = "SECRET_P12A_SHOULD_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints"
_MAX_BYTES = 2 * 1024 * 1024


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


def _url(project_id: str, checkpoint_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-checkpoints"
    if checkpoint_id is None:
        return base
    return f"{base}/{checkpoint_id}"


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
    username = f"user_{role}_p12a{'_own' if is_owner else ''}"
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
    name: str = "P12A技术标",
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
                    "label": "要求甲",
                    "status": "partial",
                    "chapterIds": ["chap_a"],
                    "outlineIds": ["node_a"],
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


def _count_outline_nodes(outline) -> int:
    if outline is None:
        return 0
    count = 0
    stack: list = []
    if isinstance(outline, list):
        stack.extend(outline)
    elif isinstance(outline, dict):
        stack.append(outline)
    else:
        return 0
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            count += 1
            children = node.get("children")
            if isinstance(children, list):
                stack.extend(children)
    return count


def _count_chapters(chapters) -> int:
    if not isinstance(chapters, list):
        return 0
    return sum(1 for c in chapters if isinstance(c, dict))


def _db_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .count()
        )
    finally:
        db.close()


# ---------- 表结构 ----------


def test_table_constraints_indexes_and_fk_cascade(disabled_client):
    """用途：真实检查字段、CHECK、复合索引与项目级联删除。"""
    insp = inspect(engine)
    assert "editor_state_checkpoints" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("editor_state_checkpoints")}
    assert cols == {
        "id",
        "workspace_id",
        "project_id",
        "snapshot_json",
        "state_version",
        "snapshot_bytes",
        "outline_node_count",
        "chapter_count",
        "created_at",
        "display_name",
    }
    fks = insp.get_foreign_keys("editor_state_checkpoints")
    fk_by_col = {
        tuple(f["constrained_columns"]): f for f in fks if f.get("constrained_columns")
    }
    ws_fk = fk_by_col[("workspace_id",)]
    proj_fk = fk_by_col[("project_id",)]
    assert ws_fk["referred_table"] == "workspaces"
    assert proj_fk["referred_table"] == "projects"
    assert (ws_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
    assert (proj_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"

    indexes = insp.get_indexes("editor_state_checkpoints")
    composite = None
    for ix in indexes:
        if list(ix.get("column_names") or []) == [
            "workspace_id",
            "project_id",
            "created_at",
            "id",
        ]:
            composite = ix
            break
    assert composite is not None, indexes

    pid = _create_project(disabled_client)
    now = utc_now().isoformat()
    db = SessionLocal()
    try:
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO editor_state_checkpoints "
                    "(id, workspace_id, project_id, snapshot_json, state_version, "
                    "snapshot_bytes, outline_node_count, chapter_count, created_at) "
                    "VALUES ('escp_bad_bytes', 'ws_local', :pid, '{}', 'esv_x', 0, 0, 0, :ts)"
                ),
                {"pid": pid, "ts": now},
            )
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO editor_state_checkpoints "
                    "(id, workspace_id, project_id, snapshot_json, state_version, "
                    "snapshot_bytes, outline_node_count, chapter_count, created_at) "
                    "VALUES ('escp_bad_out', 'ws_local', :pid, '{}', 'esv_x', 2, -1, 0, :ts)"
                ),
                {"pid": pid, "ts": now},
            )
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO editor_state_checkpoints "
                    "(id, workspace_id, project_id, snapshot_json, state_version, "
                    "snapshot_bytes, outline_node_count, chapter_count, created_at) "
                    "VALUES ('escp_bad_chap', 'ws_local', :pid, '{}', 'esv_x', 2, 0, -3, :ts)"
                ),
                {"pid": pid, "ts": now},
            )
            db.commit()
        db.rollback()
    finally:
        db.close()

    # 项目级联删除
    create = disabled_client.post(
        _url(pid),
        json={},
    )
    assert create.status_code == 201, create.text
    assert _db_count(pid) == 1
    delete = disabled_client.delete(f"/api/projects/{pid}")
    assert delete.status_code in (200, 204), delete.text
    assert _db_count(pid) == 0


# ---------- 创建 / 快照 ----------


def test_create_empty_project_authoritative_empty_snapshot(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="空项目检查点")
    res = client.post(_url(pid), json={})
    assert res.status_code == 201, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _META_KEYS
    assert str(body["checkpointId"]).startswith("escp_")
    assert len(body["checkpointId"]) == len("escp_") + 32
    assert str(body["stateVersion"]).startswith("esv_")
    assert len(body["stateVersion"]) == len("esv_") + 32
    assert body["outlineNodeCount"] == 0
    assert body["chapterCount"] == 0
    assert type(body["snapshotBytes"]) is int and body["snapshotBytes"] >= 1
    assert body["displayName"] is None

    detail = client.get(_url(pid, body["checkpointId"]))
    assert detail.status_code == 200, detail.text
    _assert_no_store(detail)
    dbody = detail.json()
    assert set(dbody.keys()) == _DETAIL_KEYS
    assert dbody["displayName"] is None
    snap = dbody["snapshot"]
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    for bad in _FORBIDDEN_SNAPSHOT_KEYS:
        assert bad not in snap
    assert snap["outline"] is None
    assert snap["chapters"] is None
    assert snap["mode"] == "ALIGNED"
    assert isinstance(snap["analysis"], dict)
    assert snap["responseMatrix"] == []
    assert dbody["stateVersion"] == _expected_version(snap)
    assert dbody["snapshotBytes"] == len(_canonical_bytes(snap))
    # 当前 editor-state 未被修改（仍为空态）
    current = client.get(f"/api/projects/{pid}/editor-state").json()
    assert current["projectId"] == pid
    assert current["chapters"] is None


def test_create_technical_and_business_full_snapshots(disabled_client):
    client = disabled_client
    tech_id = _create_project(client, name="技术标检查点", kind="technical")
    _seed_technical_state(client, tech_id)
    tech = client.post(_url(tech_id), json={})
    assert tech.status_code == 201, tech.text
    tmeta = tech.json()
    assert tmeta["outlineNodeCount"] == 3
    assert tmeta["chapterCount"] == 2
    tdetail = client.get(_url(tech_id, tmeta["checkpointId"])).json()
    tsnap = tdetail["snapshot"]
    assert set(tsnap.keys()) == _SNAPSHOT_KEYS
    assert tsnap["parsedMarkdown"] == "# 招标文件\n正文"
    assert tsnap["chapters"][0]["title"] == "总体架构"
    assert tdetail["stateVersion"] == _expected_version(tsnap)
    assert tdetail["stateVersion"] == tmeta["stateVersion"]
    for bad in _FORBIDDEN_SNAPSHOT_KEYS:
        assert bad not in tsnap
    # 独立重算哈希
    assert tmeta["stateVersion"] == _expected_version(tsnap)

    biz_id = _create_project(client, name="商务标检查点", kind="business")
    _seed_business_state(client, biz_id)
    biz = client.post(_url(biz_id), json={})
    assert biz.status_code == 201, biz.text
    bmeta = biz.json()
    bdetail = client.get(_url(biz_id, bmeta["checkpointId"])).json()
    bsnap = bdetail["snapshot"]
    assert set(bsnap.keys()) == _SNAPSHOT_KEYS
    assert isinstance(bsnap["businessQualify"], list)
    assert bsnap["businessQualify"][0]["name"] == "资质A"
    assert isinstance(bsnap["businessCommit"], list)
    assert bsnap["businessCommit"][0]["text"] == "承诺一"
    assert bdetail["stateVersion"] == _expected_version(bsnap)
    for bad in _FORBIDDEN_SNAPSHOT_KEYS:
        assert bad not in bsnap


def test_create_rejects_extra_body_fields(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    for payload in (
        {"snapshot": {"mode": "ALIGNED"}},
        {"name": "我的检查点"},
        {"stateVersion": "esv_forged"},
        {"outlineNodeCount": 9},
        {"checkpointId": "escp_forged"},
    ):
        res = client.post(_url(pid), json=payload)
        assert res.status_code == 422, (payload, res.text)
    assert _db_count(pid) == 0


def test_each_create_is_new_no_dedupe(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    a = client.post(_url(pid), json={}).json()
    b = client.post(_url(pid), json={}).json()
    assert a["checkpointId"] != b["checkpointId"]
    assert a["stateVersion"] == b["stateVersion"]
    assert _db_count(pid) == 2


# ---------- 列表 / SQL 投影 / 裁剪 ----------


def test_list_metadata_only_and_sql_excludes_snapshot_json(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    created = client.post(_url(pid), json={}).json()

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "editor_state_checkpoints" not in statement.lower():
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        listed = client.get(_url(pid))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    payload = listed.json()
    assert set(payload.keys()) == _LIST_TOP
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert set(item.keys()) == _META_KEYS
    assert item["checkpointId"] == created["checkpointId"]
    assert "snapshot" not in item
    blob = json.dumps(payload, ensure_ascii=False)
    assert "架构正文" not in blob
    assert "招标文件" not in blob

    select_sqls = [
        s
        for s in captured
        if "editor_state_checkpoints" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert select_sqls, f"未捕获列表 SELECT: {captured}"
    # 硬投影断言：列表 SELECT 投影段必须含元数据列且绝不含 snapshot_json
    found_meta_select = False
    for sql in select_sqls:
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", " ".join(sql.split()))
        if match is None:
            continue
        select_list = match.group(1).lower()
        if (
            "state_version" in select_list
            or "snapshot_bytes" in select_list
            or "outline_node_count" in select_list
        ):
            found_meta_select = True
            assert "snapshot_json" not in select_list, sql
            assert "state_version" in select_list, sql
            assert "snapshot_bytes" in select_list, sql
            assert "outline_node_count" in select_list, sql
            assert "chapter_count" in select_list, sql
            assert "created_at" in select_list, sql
    assert found_meta_select, select_sqls


def test_keep_latest_20_and_do_not_delete_other_projects(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="主项目")
    other = _create_project(client, name="旁路项目")
    other_cp = client.post(_url(other), json={}).json()["checkpointId"]

    ids: list[str] = []
    for i in range(21):
        res = client.post(_url(pid), json={})
        assert res.status_code == 201, res.text
        ids.append(res.json()["checkpointId"])

    assert _db_count(pid) == 20
    assert _db_count(other) == 1
    listed = client.get(_url(pid)).json()["items"]
    assert len(listed) == 20
    listed_ids = [i["checkpointId"] for i in listed]
    # 稳定倒序：最新在前
    assert listed_ids[0] == ids[-1]
    assert ids[0] not in listed_ids  # 最早一条被裁
    assert set(listed_ids) == set(ids[1:])
    # 其他项目不受影响
    other_detail = client.get(_url(other, other_cp))
    assert other_detail.status_code == 200, other_detail.text


def test_concurrent_creates_never_exceed_20(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    # 预填 18 条，再并发 4 次 → 最终 <=20
    for _ in range(18):
        assert client.post(_url(pid), json={}).status_code == 201
    barrier = threading.Barrier(4)
    outcomes: list[int] = []

    def worker():
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                editor_state_checkpoint_service.create_editor_state_checkpoint(
                    db, "ws_local", pid
                )
                return 201
            except editor_state_checkpoint_service.EditorStateCheckpointError as exc:
                return exc.status_code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker) for _ in range(4)]
        outcomes = [f.result(timeout=30) for f in futures]

    assert all(code == 201 for code in outcomes), outcomes
    assert _db_count(pid) == 20
    listed = client.get(_url(pid)).json()["items"]
    assert len(listed) == 20


# ---------- 边界 / 回滚 / 损坏 ----------


def test_over_2mib_rejected_zero_write(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)

    def _huge_state(db, workspace_id, project_id):
        huge = "汉" * (2 * 1024 * 1024)
        return {
            "projectId": project_id,
            "outline": None,
            "chapters": None,
            "facts": None,
            "mode": "ALIGNED",
            "analysisOverview": None,
            "analysis": {
                "overview": "",
                "techRequirements": [],
                "rejectionRisks": [],
                "scoringPoints": [],
            },
            "responseMatrix": [],
            "responseMatrixVersion": "rmv_dummy",
            "guidance": None,
            "parsedMarkdown": huge,
            "businessQualify": {"items": []},
            "businessToc": {"sections": []},
            "businessQuote": {"rows": []},
            "businessCommit": {"clauses": []},
            "updatedAt": None,
        }

    monkeypatch.setattr(
        "app.services.editor_state_service.get_editor_state",
        _huge_state,
    )
    res = client.post(_url(pid), json={})
    _assert_fixed_error(res, 413, "editor_state_checkpoint_too_large")
    assert _db_count(pid) == 0


def test_create_exception_rolls_back(disabled_client, monkeypatch):
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_insert_checkpoint_row",
        _boom,
    )
    with pytest.raises(RuntimeError, match="simulated insert failure"):
        client.post(_url(pid), json={})
    assert _db_count(pid) == before


def test_trim_selects_only_ids_not_snapshot_json(disabled_client):
    """用途：淘汰路径 SQL 仅投影 id，绝不加载 snapshot_json。"""
    client = disabled_client
    pid = _create_project(client)
    for _ in range(20):
        assert client.post(_url(pid), json={}).status_code == 201

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        lower = statement.lower()
        if "editor_state_checkpoints" not in lower:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        # 仅关心淘汰候选 SELECT（带 order by created_at）
        if "created_at" in lower or "order by" in lower:
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = client.post(_url(pid), json={})
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert res.status_code == 201, res.text
    assert _db_count(pid) == 20
    assert captured, "未捕获淘汰 SELECT"
    for sql in captured:
        compact = " ".join(sql.split())
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None, sql
        select_list = match.group(1).lower()
        assert "snapshot_json" not in select_list, sql
        # 不得 SELECT * / 全行实体加载：投影应仅为 id 列
        assert "snapshot_bytes" not in select_list, sql
        assert "state_version" not in select_list, sql
        assert re.search(r"\bid\b", select_list), sql


def test_create_succeeds_without_refresh_after_commit(disabled_client, monkeypatch):
    """用途：注入会抛错的 refresh，创建仍成功，证明提交后不调用 refresh。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)
    call_count = {"n": 0}

    real_create = editor_state_checkpoint_service.create_editor_state_checkpoint

    def _wrapped(db, workspace_id, project_id):
        # 直接替换当前会话的 refresh，捕获是否被调用
        if not hasattr(db, "_p12a_refresh_wrapped"):

            def _bad_refresh(*args, **kwargs):
                call_count["n"] += 1
                raise RuntimeError("refresh must not be called after successful commit")

            db.refresh = _bad_refresh  # type: ignore[method-assign]
            db._p12a_refresh_wrapped = True  # type: ignore[attr-defined]
        return real_create(db, workspace_id, project_id)

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "create_editor_state_checkpoint",
        _wrapped,
    )
    # 通过 API 走路由依赖注入的同一 service 符号路径
    monkeypatch.setattr(
        "app.api.editor_state_checkpoints.editor_state_checkpoint_service.create_editor_state_checkpoint",
        _wrapped,
    )

    res = client.post(_url(pid), json={})
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body.keys()) == _META_KEYS
    assert body["checkpointId"].startswith("escp_")
    assert _db_count(pid) == before + 1
    assert call_count["n"] == 0, "提交成功后不得调用 refresh"


def _base_authoritative_state(project_id: str) -> dict:
    """用途：构造带完整 13 键的权威 editor-state 来源（供回滚域专项注入）。"""
    return {
        "projectId": project_id,
        "outline": None,
        "chapters": None,
        "facts": None,
        "mode": "ALIGNED",
        "analysisOverview": None,
        "analysis": {
            "overview": "",
            "techRequirements": [],
            "rejectionRisks": [],
            "scoringPoints": [],
        },
        "responseMatrix": [],
        "responseMatrixVersion": "rmv_dummy",
        "guidance": None,
        "parsedMarkdown": None,
        "businessQualify": {"items": []},
        "businessToc": {"sections": []},
        "businessQuote": {"rows": []},
        "businessCommit": {"clauses": []},
        "updatedAt": None,
    }


def test_create_serialization_error_rolls_back_open_transaction(
    disabled_client, monkeypatch
):
    """用途：锁后序列化失败必须显式 rollback；旧实现会留下打开事务。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    def _unserializable_state(db, workspace_id, project_id):
        state = _base_authoritative_state(project_id)
        # 循环结构：json.dumps 抛 TypeError，模拟序列化失败
        cyclic: list = []
        cyclic.append(cyclic)
        state["parsedMarkdown"] = cyclic
        return state

    monkeypatch.setattr(
        "app.services.editor_state_service.get_editor_state",
        _unserializable_state,
    )

    db = SessionLocal()
    try:
        # Python 3.x 对循环引用抛 ValueError；不可序列化类型抛 TypeError
        with pytest.raises((TypeError, ValueError)):
            editor_state_checkpoint_service.create_editor_state_checkpoint(
                db, "ws_local", pid
            )
        # 修复后不得仍持有写事务
        assert not db.in_transaction()
    finally:
        db.close()

    assert _db_count(pid) == before


def test_create_missing_project_rolls_back_open_transaction():
    """用途：不存在项目在加锁 UPDATE 后必须 rollback，Session 不再 in_transaction。"""
    db = SessionLocal()
    try:
        with pytest.raises(
            editor_state_checkpoint_service.EditorStateCheckpointError
        ) as ei:
            editor_state_checkpoint_service.create_editor_state_checkpoint(
                db, "ws_local", "proj_missing_p12a_rr2_tx"
            )
        assert ei.value.status_code == 404
        assert ei.value.code == "project_not_found"
        assert not db.in_transaction()
    finally:
        db.close()


def test_create_authoritative_read_runtime_error_rolls_back(
    disabled_client, monkeypatch
):
    """用途：权威读取 RuntimeError 原样上抛，但 Session 必须已 rollback。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    def _boom(db, workspace_id, project_id):
        raise RuntimeError("simulated authoritative read failure")

    monkeypatch.setattr(
        "app.services.editor_state_service.get_editor_state",
        _boom,
    )

    db = SessionLocal()
    try:
        with pytest.raises(
            RuntimeError, match="simulated authoritative read failure"
        ):
            editor_state_checkpoint_service.create_editor_state_checkpoint(
                db, "ws_local", pid
            )
        assert not db.in_transaction()
    finally:
        db.close()

    assert _db_count(pid) == before


def test_create_rejects_nan_and_infinity_no_write_rolls_back(
    disabled_client, monkeypatch
):
    """用途：NaN/Infinity 不得生成非标准 JSON 落库，Session 必须 rollback。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    for label, value in (
        ("nan", float("nan")),
        ("inf", float("inf")),
        ("ninf", float("-inf")),
    ):
        def _nonfinite_state(db, workspace_id, project_id, _v=value):
            state = _base_authoritative_state(project_id)
            state["analysisOverview"] = _v
            return state

        monkeypatch.setattr(
            "app.services.editor_state_service.get_editor_state",
            _nonfinite_state,
        )

        db = SessionLocal()
        try:
            with pytest.raises(ValueError):
                editor_state_checkpoint_service.create_editor_state_checkpoint(
                    db, "ws_local", pid
                )
            assert not db.in_transaction(), label
        finally:
            db.close()

        assert _db_count(pid) == before, label


def test_detail_rejects_stored_nan_infinity_fixed_corrupt(disabled_client):
    """用途：存量含 NaN/Infinity 的非标准 JSON 详情固定 500，不泄漏正文。"""
    client = disabled_client
    pid = _create_project(client)
    created = client.post(_url(pid), json={}).json()
    cid = created["checkpointId"]

    for poison in (float("nan"), float("inf"), float("-inf")):
        db = SessionLocal()
        try:
            row = db.get(EditorStateCheckpointRow, cid)
            assert row is not None
            data = json.loads(row.snapshot_json)
            data["analysisOverview"] = poison
            # 故意写出 Python 默认 allow_nan 的非标准 JSON 正文
            poisoned = json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=True,
            )
            row.snapshot_json = poisoned
            row.snapshot_bytes = len(poisoned.encode("utf-8"))
            row.state_version = "esv_" + hashlib.sha256(
                poisoned.encode("utf-8")
            ).hexdigest()[:32]
            db.commit()
        finally:
            db.close()

        res = client.get(_url(pid, cid))
        _assert_fixed_error(res, 500, "editor_state_checkpoint_corrupt")
        assert cid not in res.text
        assert "NaN" not in res.text
        assert "Infinity" not in res.text
        assert "ValueError" not in res.text
        assert "Traceback" not in res.text
        assert "allow_nan" not in res.text


def test_trim_exception_rolls_back_new_session_empty(disabled_client, monkeypatch):
    """用途：淘汰阶段异常必须 rollback，新会话看不到新增记录。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    def _boom_trim(*args, **kwargs):
        raise RuntimeError("simulated trim failure")

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_trim_checkpoints",
        _boom_trim,
    )
    with pytest.raises(RuntimeError, match="simulated trim failure"):
        client.post(_url(pid), json={})
    # 新会话计数不变
    assert _db_count(pid) == before


def test_commit_exception_rolls_back_new_session_empty(disabled_client, monkeypatch):
    """用途：commit 异常必须 rollback，新会话看不到新增记录。"""
    client = disabled_client
    pid = _create_project(client)
    before = _db_count(pid)

    real_create = editor_state_checkpoint_service.create_editor_state_checkpoint

    def _wrapped(db, workspace_id, project_id):
        real_commit = db.commit

        def _bad_commit(*args, **kwargs):
            raise RuntimeError("simulated commit failure")

        db.commit = _bad_commit  # type: ignore[method-assign]
        try:
            return real_create(db, workspace_id, project_id)
        finally:
            db.commit = real_commit  # type: ignore[method-assign]

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "create_editor_state_checkpoint",
        _wrapped,
    )
    monkeypatch.setattr(
        "app.api.editor_state_checkpoints.editor_state_checkpoint_service.create_editor_state_checkpoint",
        _wrapped,
    )

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        client.post(_url(pid), json={})
    assert _db_count(pid) == before


def test_detail_rejects_noncanonical_json_even_if_bytes_and_version_synced(
    disabled_client,
):
    """用途：语义有效且 13 键完整，但空白/键序非规范时详情固定 500。"""
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    created = client.post(_url(pid), json={}).json()
    cid = created["checkpointId"]

    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cid)
        assert row is not None
        data = json.loads(row.snapshot_json)
        # 非规范：带空白 + 非 sort_keys 键序（人为逆序键）
        noncanonical = json.dumps(
            {k: data[k] for k in reversed(sorted(data.keys()))},
            ensure_ascii=False,
            sort_keys=False,
            indent=2,
            separators=(", ", ": "),
        )
        # 同步重算字节与版本，模拟攻击者同时篡改三字段
        row.snapshot_json = noncanonical
        row.snapshot_bytes = len(noncanonical.encode("utf-8"))
        row.state_version = "esv_" + hashlib.sha256(
            noncanonical.encode("utf-8")
        ).hexdigest()[:32]
        db.commit()
    finally:
        db.close()

    res = client.get(_url(pid, cid))
    _assert_fixed_error(res, 500, "editor_state_checkpoint_corrupt")
    assert cid not in res.text
    assert "架构正文" not in res.text
    assert "ValueError" not in res.text
    assert "Traceback" not in res.text


def test_validate_snapshot_bad_metadata_fixed_corrupt():
    """用途：错误类型/负数/越界元数据统一固定损坏错误，无类型细节泄漏。"""
    snap = {
        "outline": None,
        "chapters": None,
        "facts": None,
        "mode": "ALIGNED",
        "analysis": {
            "overview": "",
            "techRequirements": [],
            "rejectionRisks": [],
            "scoringPoints": [],
        },
        "responseMatrix": [],
        "guidance": None,
        "parsedMarkdown": None,
        "businessQualify": {"items": []},
        "businessToc": {"sections": []},
        "businessQuote": {"rows": []},
        "businessCommit": {"clauses": []},
        "analysisOverview": None,
    }
    canonical = json.dumps(
        snap, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    version = "esv_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    nbytes = len(canonical.encode("utf-8"))

    bad_cases = [
        # 错误类型
        {"snapshot_bytes": "not-int", "outline_node_count": 0, "chapter_count": 0},
        {"snapshot_bytes": nbytes, "outline_node_count": 1.5, "chapter_count": 0},
        {"snapshot_bytes": nbytes, "outline_node_count": 0, "chapter_count": object()},
        # 负数
        {"snapshot_bytes": nbytes, "outline_node_count": -1, "chapter_count": 0},
        {"snapshot_bytes": nbytes, "outline_node_count": 0, "chapter_count": -2},
        # 越界字节
        {"snapshot_bytes": 0, "outline_node_count": 0, "chapter_count": 0},
        {
            "snapshot_bytes": _MAX_BYTES + 1,
            "outline_node_count": 0,
            "chapter_count": 0,
        },
    ]
    for meta in bad_cases:
        with pytest.raises(
            editor_state_checkpoint_service.EditorStateCheckpointError
        ) as ei:
            editor_state_checkpoint_service._validate_snapshot_payload(
                snapshot_json=canonical,
                state_version=version,
                snapshot_bytes=meta["snapshot_bytes"],
                outline_node_count=meta["outline_node_count"],
                chapter_count=meta["chapter_count"],
            )
        exc = ei.value
        assert exc.status_code == 500
        assert exc.code == "editor_state_checkpoint_corrupt"
        assert "ValueError" not in exc.message
        assert "TypeError" not in exc.message
        assert "int" not in exc.message.lower() or "检查点" in exc.message


def test_cross_project_detail_scopes_in_sql_not_python_filter(disabled_client):
    """用途：跨项目详情查询必须在 SQL 谓词限定 workspace/project/id，不先全局 get。"""
    client = disabled_client
    a = _create_project(client, name="项目A-scope")
    b = _create_project(client, name="项目B-scope")
    _seed_technical_state(client, a)
    cid = client.post(_url(a), json={}).json()["checkpointId"]

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        lower = statement.lower()
        if "editor_state_checkpoints" not in lower:
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        cross = client.get(_url(b, cid))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    _assert_fixed_error(cross, 404, "editor_state_checkpoint_not_found")
    assert cid not in cross.text
    assert captured, "未捕获详情 SELECT"
    # 必须存在 WHERE 同时含 id + workspace_id + project_id 的查询（非仅 SELECT 列名）
    scoped = False
    for sql, _params in captured:
        compact = " ".join(sql.split()).lower()
        if "editor_state_checkpoints" not in compact:
            continue
        where_match = re.search(r"(?is)\bwhere\b(.*)$", compact)
        if where_match is None:
            continue
        where_clause = where_match.group(1)
        has_id = bool(
            re.search(r"\bid\s*=", where_clause)
            or re.search(r"\.id\s*=", where_clause)
        )
        has_ws = "workspace_id" in where_clause
        has_proj = "project_id" in where_clause
        if has_id and has_ws and has_proj:
            scoped = True
            break
    assert scoped, f"跨项目详情未在 SQL WHERE 作用域过滤: {captured}"

    # 额外证明：不得进入正文校验路径（通过损坏 A 的快照后访问 B 仍 404 且无 500）
    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cid)
        assert row is not None
        row.snapshot_json = '{"evil": true}'
        db.commit()
    finally:
        db.close()
    again = client.get(_url(b, cid))
    _assert_fixed_error(again, 404, "editor_state_checkpoint_not_found")


def test_detail_corrupt_fixed_500_no_leak(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    created = client.post(_url(pid), json={}).json()
    cid = created["checkpointId"]

    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cid)
        assert row is not None
        # 损坏：错误键集 + 植入敏感正文
        row.snapshot_json = json.dumps(
            {
                "mode": "ALIGNED",
                "evil": _SECRET,
                "projectId": pid,
                "path": _PATH_MARKER,
            },
            ensure_ascii=False,
        )
        db.commit()
    finally:
        db.close()

    res = client.get(_url(pid, cid))
    _assert_fixed_error(res, 500, "editor_state_checkpoint_corrupt")
    assert cid not in res.text
    assert _SECRET not in res.text
    assert pid not in res.text
    assert _PATH_MARKER not in res.text
    assert "架构正文" not in res.text


def test_detail_not_found_cross_project_and_workspace(disabled_client):
    client = disabled_client
    a = _create_project(client, name="项目A")
    b = _create_project(client, name="项目B")
    _seed_technical_state(client, a)
    cid = client.post(_url(a), json={}).json()["checkpointId"]

    missing = client.get(_url(a, "escp_" + "0" * 32))
    _assert_fixed_error(missing, 404, "editor_state_checkpoint_not_found")
    assert "escp_" not in missing.text or "000000" not in missing.text

    cross = client.get(_url(b, cid))
    _assert_fixed_error(cross, 404, "editor_state_checkpoint_not_found")
    assert cid not in cross.text

    # 跨空间：同 ID 项目在另一 workspace 头下表现为 project_not_found
    db = SessionLocal()
    try:
        if db.get(Workspace, "ws_other_p12a") is None:
            db.add(
                Workspace(
                    id="ws_other_p12a",
                    name="其他空间",
                    owner_user_id="user_other",
                )
            )
            db.commit()
    finally:
        db.close()
    cross_ws = client.get(
        _url(a, cid),
        headers={"X-Workspace-Id": "ws_other_p12a"},
    )
    _assert_fixed_error(cross_ws, 404, "project_not_found")
    assert cid not in cross_ws.text


def test_project_not_found_and_missing_project(disabled_client):
    client = disabled_client
    res = client.post(
        _url("proj_missing_p12a"),
        json={},
    )
    _assert_fixed_error(res, 404, "project_not_found")
    assert "proj_missing_p12a" not in res.text

    listed = client.get(_url("proj_missing_p12a"))
    _assert_fixed_error(listed, 404, "project_not_found")


def test_disallowed_methods_405_and_restore_missing_expected_is_422(disabled_client):
    """
    用途：详情 PUT/PATCH 仍精确 405；集合 PUT/PATCH/DELETE 仍精确 405；
      详情 DELETE 已由 P12H 接管，本守卫不再要求 405；
      /restore 已注册但空体缺 expectedStateVersion 时精确 422（不得宽泛 404/2xx）。
    """
    client = disabled_client
    pid = _create_project(client)
    cid_res = client.post(_url(pid), json={})
    assert cid_res.status_code == 201
    cid = cid_res.json()["checkpointId"]

    # 已存在资源上的不允许方法 → 精确 405（P12H：详情 DELETE 不再守卫为 405）
    for method, with_json in (
        ("put", True),
        ("patch", True),
    ):
        path = _url(pid, cid)
        fn = getattr(client, method)
        res = fn(path, json={}) if with_json else fn(path)
        assert res.status_code == 405, (method, path, res.status_code, res.text)

    # 集合资源上的不允许方法 → 精确 405（含集合 DELETE）
    for method, with_json in (
        ("put", True),
        ("patch", True),
        ("delete", False),
    ):
        path = _url(pid)
        fn = getattr(client, method)
        res = fn(path, json={}) if with_json else fn(path)
        assert res.status_code == 405, (method, path, res.status_code, res.text)

    # /restore 已注册：空体缺 expectedStateVersion → 精确 422，错误位置 body.expectedStateVersion
    restore = client.post(f"{_url(pid, cid)}/restore", json={})
    assert restore.status_code == 422, (restore.status_code, restore.text)
    detail = restore.json().get("detail")
    assert isinstance(detail, list), restore.text
    locs = [
        tuple(item.get("loc", ()))
        for item in detail
        if isinstance(item, dict)
    ]
    assert ("body", "expectedStateVersion") in locs, (locs, restore.text)


# ---------- 权限 / CSRF ----------


def test_auth_required_strict_bid_writer_csrf_and_owner_no_bypass(required_client):
    client = required_client

    # 非 bid_writer 精确 403
    for role in ("finance", "hr", "bidder"):
        csrf_role = _login_role(client, role)
        r = client.get(_url("any"), headers={"X-CSRF-Token": csrf_role})
        assert r.status_code == 403, (role, r.text)
        detail = r.json().get("detail")
        assert isinstance(detail, dict), r.text
        assert detail.get("code") == "role_forbidden", (role, detail)

    # owner 身份 + 非 bid_writer 不得绕过
    csrf_owner_finance = _login_role(client, "finance", is_owner=True)
    r_owner = client.get(
        _url("any"),
        headers={"X-CSRF-Token": csrf_owner_finance},
    )
    assert r_owner.status_code == 403, r_owner.text
    assert r_owner.json()["detail"]["code"] == "role_forbidden"

    # bid_writer 可用
    csrf_w = _login_role(client, "bid_writer")
    create = client.post(
        "/api/projects",
        json={"name": "required检查点", "kind": "technical"},
        headers={"X-CSRF-Token": csrf_w},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    _seed_technical_state(client, pid, headers={"X-CSRF-Token": csrf_w})

    ok = client.post(
        _url(pid),
        json={},
        headers={"X-CSRF-Token": csrf_w},
    )
    assert ok.status_code == 201, ok.text
    _assert_no_store(ok)
    assert _db_count(pid) == 1

    # CSRF 缺失 → 403 csrf_invalid，零写入
    before = _db_count(pid)
    no_csrf = client.post(_url(pid), json={})
    assert no_csrf.status_code == 403, no_csrf.text
    detail_csrf = no_csrf.json().get("detail")
    assert isinstance(detail_csrf, dict), no_csrf.text
    assert detail_csrf.get("code") == "csrf_invalid"
    assert _db_count(pid) == before


def test_disabled_personal_mode_allows_create_list_detail(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _seed_technical_state(client, pid)
    created = client.post(_url(pid), json={})
    assert created.status_code == 201
    listed = client.get(_url(pid))
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    detail = client.get(_url(pid, created.json()["checkpointId"]))
    assert detail.status_code == 200
    assert set(detail.json()["snapshot"].keys()) == _SNAPSHOT_KEYS


def test_create_does_not_mutate_current_editor_state(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    before = _seed_technical_state(client, pid)
    assert client.post(_url(pid), json={}).status_code == 201
    after = client.get(f"/api/projects/{pid}/editor-state").json()
    assert after["chapters"] == before["chapters"]
    assert after["outline"] == before["outline"]
    assert after["parsedMarkdown"] == before["parsedMarkdown"]
    assert after["responseMatrixVersion"] == before["responseMatrixVersion"]
