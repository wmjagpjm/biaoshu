"""
模块：P12C-A editor-state 有限自动修订账本定向测试
用途：验收独立表、固定来源、transition 语义、10 条裁剪、SQL 最小投影、
  固定内部错误、回滚双零与检查点域零干扰。
对接：editor_state_revision_service；EditorStateRevisionRow；editor_state_service。
二次开发：仅本地 SQLite 与合成数据；禁止外网、真实业务正文或白名单外改动。
"""

from __future__ import annotations

import json
import re
import secrets
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
    EditorStateRevisionRow,
    Workspace,
    utc_now,
)
from app.services import editor_state_revision_service, editor_state_service
from app.services.editor_state_revision_service import (
    CODE_REVISION_INVALID,
    MSG_REVISION_INVALID,
    EditorStateRevisionError,
    record_editor_state_transition,
)

_WS = "ws_local"
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_SOURCE_OK = "browser_put"
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
_MAX_BYTES = 2 * 1024 * 1024
_SECRET_BODY = "SECRET_P12C_A_BODY_SHOULD_NOT_LEAK"


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


def _create_project(client: TestClient, name: str = "P12C-A项目") -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "mode": "technical", "bidDeadline": None},
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _state_with_version(**overrides) -> dict:
    """用途：构造精确 13 键 + 匹配 stateVersion 的内部状态。"""
    analysis = editor_state_service.empty_analysis()
    business = editor_state_service.empty_business()
    state = {
        "outline": None,
        "chapters": None,
        "facts": None,
        "mode": "ALIGNED",
        "analysis": analysis,
        "responseMatrix": [],
        "guidance": None,
        "parsedMarkdown": None,
        "businessQualify": business["qualify"],
        "businessToc": business["toc"],
        "businessQuote": business["quote"],
        "businessCommit": business["commit"],
        "analysisOverview": analysis.get("overview", ""),
    }
    state.update(overrides)
    snap = editor_state_service.extract_canonical_snapshot(state)
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _variant(tag: str) -> dict:
    return _state_with_version(
        chapters=[{"id": f"ch_{tag}", "title": f"章节{tag}", "content": f"正文{tag}"}],
        guidance=f"指引-{tag}",
    )


def _state_missing_canonical_key(missing_key: str) -> dict:
    """
    用途：从合法完整内部状态删除指定规范键，并按缺失后 extract/canonical 重算匹配 stateVersion。
    二次开发：禁止在测试中复制 13 键字面量集合；键名必须来自 CANONICAL_STATE_KEYS。
    """
    full = _state_with_version()
    body = {k: v for k, v in full.items() if k not in (missing_key, "stateVersion")}
    snap = editor_state_service.extract_canonical_snapshot(body)
    body["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(
            editor_state_service.canonical_snapshot_json(snap)
        )
    )
    assert missing_key not in body
    return body


def _state_version_shell_only() -> dict:
    """用途：仅含按 13 键全 None 规范快照算出的合法 stateVersion 壳。"""
    snap = editor_state_service.extract_canonical_snapshot({})
    return {
        "stateVersion": editor_state_service.compute_state_version_from_canonical_json(
            editor_state_service.canonical_snapshot_json(snap)
        )
    }


def _db_rev_count(project_id: str, workspace_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(EditorStateRevisionRow).filter(
            EditorStateRevisionRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateRevisionRow.workspace_id == workspace_id)
        return q.count()
    finally:
        db.close()


def _db_rev_rows(
    project_id: str, workspace_id: str | None = None
) -> list[EditorStateRevisionRow]:
    db = SessionLocal()
    try:
        q = db.query(EditorStateRevisionRow).filter(
            EditorStateRevisionRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateRevisionRow.workspace_id == workspace_id)
        return list(
            q.order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            ).all()
        )
    finally:
        db.close()


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


def _record(
    project_id: str,
    before: dict,
    after: dict,
    source: str = _SOURCE_OK,
    *,
    workspace_id: str = _WS,
    commit: bool = True,
):
    db = SessionLocal()
    try:
        result = record_editor_state_transition(
            db,
            workspace_id,
            project_id,
            before_state=before,
            after_state=after,
            source_kind=source,
        )
        if commit:
            db.commit()
        return result, db
    except Exception:
        db.rollback()
        db.close()
        raise


# ---------- 表结构 ----------


def test_table_columns_constraints_indexes_and_fk_cascade(disabled_client):
    """用途：真实 SQLite 验收列、CHECK、复合索引与项目级联删除。"""
    insp = inspect(engine)
    assert "editor_state_revisions" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("editor_state_revisions")}
    assert cols == {
        "id",
        "workspace_id",
        "project_id",
        "snapshot_json",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
    }
    fks = insp.get_foreign_keys("editor_state_revisions")
    fk_by_col = {
        tuple(f["constrained_columns"]): f for f in fks if f.get("constrained_columns")
    }
    ws_fk = fk_by_col[("workspace_id",)]
    proj_fk = fk_by_col[("project_id",)]
    assert ws_fk["referred_table"] == "workspaces"
    assert proj_fk["referred_table"] == "projects"
    assert (ws_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"
    assert (proj_fk.get("options") or {}).get("ondelete", "").upper() == "CASCADE"

    indexes = insp.get_indexes("editor_state_revisions")
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
                    "INSERT INTO editor_state_revisions "
                    "(id, workspace_id, project_id, snapshot_json, state_version, "
                    "snapshot_bytes, source_kind, created_at) "
                    "VALUES ('esr_bad_bytes', 'ws_local', :pid, '{}', 'esv_x', 0, "
                    "'browser_put', :ts)"
                ),
                {"pid": pid, "ts": now},
            )
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(
                text(
                    "INSERT INTO editor_state_revisions "
                    "(id, workspace_id, project_id, snapshot_json, state_version, "
                    "snapshot_bytes, source_kind, created_at) "
                    "VALUES ('esr_bad_src', 'ws_local', :pid, '{}', 'esv_x', 2, "
                    "'client_forged', :ts)"
                ),
                {"pid": pid, "ts": now},
            )
            db.commit()
        db.rollback()
    finally:
        db.close()

    before = _variant("a")
    after = _variant("b")
    result, db = _record(pid, before, after, commit=True)
    db.close()
    assert result["added_count"] == 2
    assert _db_rev_count(pid) == 2
    delete = disabled_client.delete(f"/api/projects/{pid}")
    assert delete.status_code in (200, 204), delete.text
    assert _db_rev_count(pid) == 0


def test_source_kind_enum_exact_set():
    assert editor_state_revision_service.REVISION_SOURCE_KINDS == _ALL_SOURCES


# ---------- transition 语义 ----------


def test_first_transition_writes_before_and_after(disabled_client):
    pid = _create_project(disabled_client, name="首写")
    before = _variant("v0")
    after = _variant("v1")
    result, db = _record(pid, before, after)
    db.close()
    assert result["added_count"] == 2
    assert result["final_state_version"] == after["stateVersion"]
    assert set(result.keys()) == {"added_count", "final_state_version"}
    rows = _db_rev_rows(pid)
    assert len(rows) == 2
    # 最新在前
    assert rows[0].state_version == after["stateVersion"]
    assert rows[1].state_version == before["stateVersion"]
    for r in rows:
        assert r.id.startswith("esr_")
        assert len(r.id) == len("esr_") + 32
        assert r.source_kind == _SOURCE_OK
        assert r.workspace_id == _WS
        assert r.project_id == pid


def test_same_version_first_transition_writes_one(disabled_client):
    pid = _create_project(disabled_client, name="同版本首写")
    state = _variant("same")
    result, db = _record(pid, state, state)
    db.close()
    assert result["added_count"] == 1
    assert result["final_state_version"] == state["stateVersion"]
    assert _db_rev_count(pid) == 1


def test_continuous_transition_only_appends_new_after(disabled_client):
    pid = _create_project(disabled_client, name="连续")
    s0 = _variant("c0")
    s1 = _variant("c1")
    s2 = _variant("c2")
    r1, db = _record(pid, s0, s1)
    db.close()
    assert r1["added_count"] == 2
    r2, db = _record(pid, s1, s2)
    db.close()
    assert r2["added_count"] == 1
    assert r2["final_state_version"] == s2["stateVersion"]
    rows = _db_rev_rows(pid)
    assert len(rows) == 3
    assert [r.state_version for r in rows] == [
        s2["stateVersion"],
        s1["stateVersion"],
        s0["stateVersion"],
    ]


def test_gap_fills_before_then_after(disabled_client):
    pid = _create_project(disabled_client, name="断链")
    s0 = _variant("g0")
    s1 = _variant("g1")
    s_other = _variant("gX")
    s_after = _variant("gY")
    r1, db = _record(pid, s0, s1)
    db.close()
    # 断链：before 不是最新
    r2, db = _record(pid, s_other, s_after)
    db.close()
    assert r2["added_count"] == 2
    rows = _db_rev_rows(pid)
    assert len(rows) == 4
    assert rows[0].state_version == s_after["stateVersion"]
    assert rows[1].state_version == s_other["stateVersion"]


def test_restore_old_version_creates_new_row(disabled_client):
    pid = _create_project(disabled_client, name="回退")
    s0 = _variant("r0")
    s1 = _variant("r1")
    s2 = _variant("r2")
    _record(pid, s0, s1)[1].close()
    _record(pid, s1, s2)[1].close()
    # 从 s2 回到 s0：before=s2 after=s0
    result, db = _record(pid, s2, s0)
    db.close()
    assert result["added_count"] == 1
    assert result["final_state_version"] == s0["stateVersion"]
    rows = _db_rev_rows(pid)
    assert len(rows) == 4
    assert rows[0].state_version == s0["stateVersion"]
    # 旧版本正文再次出现为新时间点
    assert rows[0].id != rows[-1].id


def test_adjacent_same_version_dedupe_on_continuous(disabled_client):
    pid = _create_project(disabled_client, name="相邻去重")
    s0 = _variant("d0")
    s1 = _variant("d1")
    _record(pid, s0, s1)[1].close()
    # before=after=s1 且最新已是 s1 → 零新增
    result, db = _record(pid, s1, s1)
    db.close()
    assert result["added_count"] == 0
    assert result["final_state_version"] == s1["stateVersion"]
    assert _db_rev_count(pid) == 2


# ---------- 配额 / 隔离 / 并列 ----------


def test_keep_latest_10_and_do_not_delete_other_projects(disabled_client):
    pid = _create_project(disabled_client, name="主账本")
    other = _create_project(disabled_client, name="旁路账本")
    o0 = _variant("o0")
    o1 = _variant("o1")
    _record(other, o0, o1)[1].close()
    assert _db_rev_count(other) == 2

    prev = _variant("t0")
    for i in range(1, 12):
        nxt = _variant(f"t{i}")
        result, db = _record(pid, prev, nxt)
        db.close()
        prev = nxt
        assert result["final_state_version"] == nxt["stateVersion"]

    # 11 次 transition：首写 2 + 10 次各 1 = 12，裁剪后 10
    assert _db_rev_count(pid) == 10
    assert _db_rev_count(other) == 2
    rows = _db_rev_rows(pid)
    assert len(rows) == 10
    assert rows[0].state_version == prev["stateVersion"]


def test_trim_does_not_touch_other_workspace_same_project_id(disabled_client):
    """
    用途：真实 SQLite 证明裁剪仅限 workspace+project 域；同 project_id 的另一 workspace 行零误删。
    说明：entities 两外键独立，允许同 project_id 不同 workspace_id 预置旁路行。
    """
    pid = _create_project(disabled_client, name="跨空间裁剪")
    other_ws = f"ws_p12c_a_other_{pid[-8:]}"
    # 契约 ID：esr_ + 32 位小写 hex（token_hex(16)=32 字符；禁止 31 位旁路夹具假绿）
    foreign_id = f"esr_{secrets.token_hex(16)}"
    assert re.fullmatch(r"^esr_[0-9a-f]{32}$", foreign_id)
    foreign_state = _variant("foreign_ws")
    foreign_snap = editor_state_service.extract_canonical_snapshot(foreign_state)
    foreign_json = editor_state_service.canonical_snapshot_json(foreign_snap)
    foreign_ver = foreign_state["stateVersion"]
    foreign_bytes = len(foreign_json.encode("utf-8"))

    db = SessionLocal()
    try:
        if db.get(Workspace, other_ws) is None:
            db.add(
                Workspace(
                    id=other_ws,
                    name="P12C-A旁路空间",
                    owner_user_id="u_p12c_a_other",
                )
            )
            db.flush()
        db.add(
            EditorStateRevisionRow(
                id=foreign_id,
                workspace_id=other_ws,
                project_id=pid,
                snapshot_json=foreign_json,
                state_version=foreign_ver,
                snapshot_bytes=foreign_bytes,
                source_kind=_SOURCE_OK,
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()

    assert _db_rev_count(pid, workspace_id=other_ws) == 1

    prev = _variant("iso0")
    for i in range(1, 12):
        nxt = _variant(f"iso{i}")
        result, db = _record(pid, prev, nxt, workspace_id=_WS)
        db.close()
        prev = nxt
        assert result["final_state_version"] == nxt["stateVersion"]

    assert _db_rev_count(pid, workspace_id=_WS) == 10
    assert _db_rev_count(pid, workspace_id=other_ws) == 1

    db = SessionLocal()
    try:
        foreign = db.get(EditorStateRevisionRow, foreign_id)
        assert foreign is not None
        assert foreign.workspace_id == other_ws
        assert foreign.project_id == pid
        assert foreign.id == foreign_id
        assert foreign.snapshot_json == foreign_json
        assert foreign.state_version == foreign_ver
        assert foreign.snapshot_bytes == foreign_bytes
        assert foreign.source_kind == _SOURCE_OK
    finally:
        db.close()


def test_tie_break_by_id_stable_order(disabled_client):
    """
    用途：created_at 并列时按 id DESC 稳定排序（最新/列表/裁剪共用）。
    说明：先以自然时间戳完成连续 transition，再统一时间戳；
      避免在改写时间戳后继续 transition（随机 id 会使“最新”与插入顺序解耦）。
    """
    pid = _create_project(disabled_client, name="并列时间")
    s0 = _variant("tie0")
    s1 = _variant("tie1")
    s2 = _variant("tie2")
    fixed_ts = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)

    db = SessionLocal()
    try:
        r1 = record_editor_state_transition(
            db,
            _WS,
            pid,
            before_state=s0,
            after_state=s1,
            source_kind=_SOURCE_OK,
        )
        r2 = record_editor_state_transition(
            db,
            _WS,
            pid,
            before_state=s1,
            after_state=s2,
            source_kind=_SOURCE_OK,
        )
        assert r1["added_count"] == 2
        assert r2["added_count"] == 1
        # 强制并列时间戳后，仅验证 id 打破次序（不在此后再 transition）
        for row in (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == pid)
            .all()
        ):
            row.created_at = fixed_ts
        db.commit()
    finally:
        db.close()

    rows = _db_rev_rows(pid)
    assert len(rows) == 3
    ids = [r.id for r in rows]
    # created_at 相同 → id DESC
    assert ids == sorted(ids, reverse=True)
    assert rows[0].id == max(ids)

    # 最新最小投影也应遵循同一排序键
    db = SessionLocal()
    try:
        latest = editor_state_revision_service._latest_id_and_version(db, _WS, pid)
    finally:
        db.close()
    assert latest is not None
    assert latest[0] == max(ids)


# ---------- SQL 投影 / 事务 / 返回最小化 ----------


def test_latest_and_trim_sql_exclude_snapshot_json(disabled_client):
    pid = _create_project(disabled_client, name="SQL投影")
    prev = _variant("sql0")
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "editor_state_revisions" not in statement.lower():
            return
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        for i in range(1, 12):
            nxt = _variant(f"sql{i}")
            result, db = _record(pid, prev, nxt)
            db.close()
            prev = nxt
            assert result["added_count"] in (1, 2)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    select_sqls = [
        s
        for s in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert select_sqls, f"未捕获 SELECT: {captured}"
    for sql in select_sqls:
        compact = " ".join(sql.split())
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None, sql
        select_list = match.group(1).lower()
        assert "snapshot_json" not in select_list, sql
        assert "state_version" in select_list, sql
        assert "id" in select_list, sql

    delete_sqls = [
        s
        for s in captured
        if s.lstrip().upper().startswith("DELETE")
        and "editor_state_revisions" in s.lower()
    ]
    assert delete_sqls, "第 11 条后应触发 DELETE 裁剪"
    for sql in delete_sqls:
        low = sql.lower()
        assert "workspace_id" in low
        assert "project_id" in low
        # 必须证明行 id 列条件（表名.列名），禁止被 workspace_id/project_id 子串假通过
        assert re.search(
            r"(?is)\beditor_state_revisions\s*\.\s*id\b",
            sql,
        ), f"DELETE 缺少行 id 列条件: {sql}"


def test_no_commit_rollback_refresh_project_lock(disabled_client, monkeypatch):
    pid = _create_project(disabled_client, name="无提交原语")
    before = _variant("nc0")
    after = _variant("nc1")
    db = SessionLocal()
    calls = {"commit": 0, "rollback": 0, "refresh": 0}

    real_commit = db.commit
    real_rollback = db.rollback
    real_refresh = db.refresh

    def _commit():
        calls["commit"] += 1
        return real_commit()

    def _rollback():
        calls["rollback"] += 1
        return real_rollback()

    def _refresh(*a, **k):
        calls["refresh"] += 1
        return real_refresh(*a, **k)

    db.commit = _commit  # type: ignore[method-assign]
    db.rollback = _rollback  # type: ignore[method-assign]
    db.refresh = _refresh  # type: ignore[method-assign]

    project_queries: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if " from projects" in low or "from projects " in low or low.strip().startswith(
            "update projects"
        ):
            project_queries.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        out = record_editor_state_transition(
            db,
            _WS,
            pid,
            before_state=before,
            after_state=after,
            source_kind=_SOURCE_OK,
        )
        # 调用方尚未 commit：行应仅在本会话可见（flush 后）
        assert out["added_count"] == 2
        assert calls["commit"] == 0
        assert calls["rollback"] == 0
        assert calls["refresh"] == 0
        assert project_queries == []
        # 其他会话在 commit 前不可见
        assert _db_rev_count(pid) == 0
        real_commit()
        assert _db_rev_count(pid) == 2
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db.close()


def test_caller_rollback_leaves_zero_rows(disabled_client):
    pid = _create_project(disabled_client, name="回滚双零")
    before = _variant("rb0")
    after = _variant("rb1")
    db = SessionLocal()
    try:
        out = record_editor_state_transition(
            db,
            _WS,
            pid,
            before_state=before,
            after_state=after,
            source_kind=_SOURCE_OK,
        )
        assert out["added_count"] == 2
        db.rollback()
    finally:
        db.close()
    assert _db_rev_count(pid) == 0


def test_return_value_has_no_snapshot_ids_project_or_workspace(disabled_client):
    pid = _create_project(disabled_client, name="返回最小化")
    before = _state_with_version(parsedMarkdown=_SECRET_BODY)
    after = _variant("ret1")
    result, db = _record(pid, before, after)
    db.close()
    blob = json.dumps(result, ensure_ascii=False)
    assert "snapshot" not in result
    assert "id" not in result
    assert "project" not in blob.lower()
    assert "workspace" not in blob.lower()
    assert pid not in blob
    assert _WS not in blob
    assert _SECRET_BODY not in blob
    assert "esr_" not in blob


# ---------- 校验 / 安全 ----------


@pytest.mark.parametrize(
    "missing_key",
    list(editor_state_service.CANONICAL_STATE_KEYS),
)
@pytest.mark.parametrize("side", ["before", "after"])
def test_rejects_missing_canonical_key_with_matching_version(
    disabled_client, missing_key, side
):
    """
    用途：缺任一规范键即使重算匹配 stateVersion 也必须拒绝；before/after 各覆盖一轮。
    二次开发：参数化直接引用 CANONICAL_STATE_KEYS，禁止复制 13 键集合。
    """
    pid = _create_project(
        disabled_client, name=f"缺键-{side}-{missing_key}"[:40]
    )
    good = _variant("full_ok")
    incomplete = _state_missing_canonical_key(missing_key)
    before = incomplete if side == "before" else good
    after = incomplete if side == "after" else good
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, before, after)
    assert ei.value.code == CODE_REVISION_INVALID
    assert ei.value.message == MSG_REVISION_INVALID
    assert missing_key not in str(ei.value)
    assert pid not in str(ei.value)
    assert _db_rev_count(pid) == 0


def test_rejects_state_version_shell_only(disabled_client):
    """用途：仅 stateVersion 壳（对应全 None 规范快照版本）不得入账。"""
    pid = _create_project(disabled_client, name="仅版本壳")
    shell = _state_version_shell_only()
    good = _variant("shell_peer")
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, shell, good)
    assert ei.value.code == CODE_REVISION_INVALID
    assert ei.value.message == MSG_REVISION_INVALID
    assert _db_rev_count(pid) == 0
    with pytest.raises(EditorStateRevisionError) as ei2:
        _record(pid, good, shell)
    assert ei2.value.code == CODE_REVISION_INVALID
    assert _db_rev_count(pid) == 0


def test_allows_server_derived_extra_keys(disabled_client):
    """
    用途：允许 projectId/updatedAt/responseMatrixVersion 等服务端派生额外键；
      禁止 exact-keys 误杀真实 get_editor_state 返回。
    """
    pid = _create_project(disabled_client, name="派生额外键")
    before = _variant("extra0")
    after = _variant("extra1")
    for state in (before, after):
        state["projectId"] = pid
        state["updatedAt"] = "2026-07-15T00:00:00"
        state["responseMatrixVersion"] = "rmv_extra_ok"
    result, db = _record(pid, before, after)
    db.close()
    assert result["added_count"] == 2
    assert result["final_state_version"] == after["stateVersion"]
    assert _db_rev_count(pid, workspace_id=_WS) == 2


@pytest.mark.parametrize(
    "bad_version",
    [
        None,
        "",
        "esv_ABCDEF0123456789abcdef0123456789",
        "esv_short",
        "esv_" + "g" * 32,
        " esv_" + "a" * 32,
        "esv_" + "a" * 32 + " ",
        "esv_" + "a" * 31 + "A",
        "not_a_version",
    ],
)
def test_rejects_missing_illegal_whitespace_state_version(disabled_client, bad_version):
    pid = _create_project(disabled_client, name="坏版本")
    good = _variant("ok")
    bad = dict(good)
    bad["stateVersion"] = bad_version
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, bad, good)
    err = ei.value
    assert err.code == CODE_REVISION_INVALID
    assert err.message == MSG_REVISION_INVALID
    assert _SECRET_BODY not in str(err)
    assert pid not in str(err)
    assert _db_rev_count(pid) == 0


def test_rejects_mismatched_state_version(disabled_client):
    pid = _create_project(disabled_client, name="版本不匹配")
    good = _variant("m0")
    bad = dict(good)
    bad["stateVersion"] = "esv_" + "0" * 32
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, good, bad)
    assert ei.value.code == CODE_REVISION_INVALID
    assert _db_rev_count(pid) == 0


def test_rejects_nan_infinity(disabled_client):
    pid = _create_project(disabled_client, name="非有限")
    good = _variant("n0")
    for poison in (float("nan"), float("inf"), float("-inf")):
        bad = _state_with_version()
        # 绕过规范重算：直接塞入非有限后伪造版本字段
        bad["facts"] = {"score": poison}
        # 重新计算会失败；若先算再塞毒也会在序列化失败
        with pytest.raises(EditorStateRevisionError) as ei:
            # 先构造合法壳再注入 NaN
            shell = dict(good)
            shell["facts"] = {"score": poison}
            # 仍用 good 的版本 → 不匹配或序列化失败，均固定错误
            _record(pid, shell, good)
        assert ei.value.code == CODE_REVISION_INVALID
        assert "nan" not in str(ei.value).lower()
        assert "inf" not in str(ei.value).lower()
        assert _db_rev_count(pid) == 0


def test_rejects_oversize_snapshot(disabled_client):
    pid = _create_project(disabled_client, name="超限")
    # 构造 >2MiB 的规范 JSON
    huge = "X" * (_MAX_BYTES + 1024)
    big = _state_with_version(parsedMarkdown=huge)
    # _state_with_version 会成功算出版本，但字节超限应拒绝
    good = _variant("small")
    assert len(
        editor_state_service.canonical_snapshot_json(
            editor_state_service.extract_canonical_snapshot(big)
        ).encode("utf-8")
    ) > _MAX_BYTES
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, good, big)
    assert ei.value.code == CODE_REVISION_INVALID
    assert str(_MAX_BYTES) not in str(ei.value)
    assert _db_rev_count(pid) == 0


@pytest.mark.parametrize(
    "bad_source",
    [
        "",
        "Browser_Put",
        "browser_put ",
        " autosave",
        "user",
        "client",
        None,
        123,
    ],
)
def test_rejects_illegal_source(disabled_client, bad_source):
    pid = _create_project(disabled_client, name="非法来源")
    a = _variant("s0")
    b = _variant("s1")
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, a, b, source=bad_source)  # type: ignore[arg-type]
    assert ei.value.code == CODE_REVISION_INVALID
    assert _db_rev_count(pid) == 0


def test_all_legal_sources_accepted(disabled_client):
    pid = _create_project(disabled_client, name="合法来源")
    prev = _variant("src0")
    for i, src in enumerate(sorted(_ALL_SOURCES)):
        nxt = _variant(f"src{i+1}")
        result, db = _record(pid, prev, nxt, source=src)
        db.close()
        assert result["added_count"] in (1, 2)
        prev = nxt
    rows = _db_rev_rows(pid)
    kinds = {r.source_kind for r in rows}
    assert kinds <= _ALL_SOURCES
    assert len(kinds) == len(_ALL_SOURCES)


# ---------- 共享算法委托 / 检查点零干扰 ----------


def test_delegates_to_editor_state_service_algorithms(disabled_client, monkeypatch):
    pid = _create_project(disabled_client, name="委托算法")
    before = _variant("alg0")
    after = _variant("alg1")
    counts = {"extract": 0, "canonical": 0, "version": 0}

    real_extract = editor_state_service.extract_canonical_snapshot
    real_canonical = editor_state_service.canonical_snapshot_json
    real_version = editor_state_service.compute_state_version_from_canonical_json

    def _extract(state):
        counts["extract"] += 1
        return real_extract(state)

    def _canonical(snapshot):
        counts["canonical"] += 1
        return real_canonical(snapshot)

    def _version(payload):
        counts["version"] += 1
        return real_version(payload)

    monkeypatch.setattr(
        editor_state_revision_service.editor_state_service,
        "extract_canonical_snapshot",
        _extract,
    )
    monkeypatch.setattr(
        editor_state_revision_service.editor_state_service,
        "canonical_snapshot_json",
        _canonical,
    )
    monkeypatch.setattr(
        editor_state_revision_service.editor_state_service,
        "compute_state_version_from_canonical_json",
        _version,
    )

    result, db = _record(pid, before, after)
    db.close()
    assert result["added_count"] == 2
    assert counts["extract"] >= 2
    assert counts["canonical"] >= 2
    assert counts["version"] >= 2


def test_checkpoints_untouched(disabled_client):
    pid = _create_project(disabled_client, name="检查点零干扰")
    # 建一个手动检查点
    cp = disabled_client.post(
        f"/api/projects/{pid}/editor-state-checkpoints",
        json={},
    )
    assert cp.status_code == 201, cp.text
    before_cp = _db_cp_count(pid)
    assert before_cp == 1
    cp_row = SessionLocal()
    try:
        row = (
            cp_row.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == pid)
            .one()
        )
        cp_id = row.id
        cp_json = row.snapshot_json
        cp_ver = row.state_version
    finally:
        cp_row.close()

    s0 = _variant("cp0")
    s1 = _variant("cp1")
    _record(pid, s0, s1)[1].close()
    assert _db_rev_count(pid) == 2
    assert _db_cp_count(pid) == before_cp

    cp_row = SessionLocal()
    try:
        row = cp_row.get(EditorStateCheckpointRow, cp_id)
        assert row is not None
        assert row.snapshot_json == cp_json
        assert row.state_version == cp_ver
    finally:
        cp_row.close()


def test_error_messages_do_not_leak_sensitive(disabled_client):
    pid = _create_project(disabled_client, name="脱敏")
    good = _variant("sec0")
    bad = dict(good)
    bad["stateVersion"] = "esv_" + "f" * 32
    bad["parsedMarkdown"] = _SECRET_BODY
    with pytest.raises(EditorStateRevisionError) as ei:
        _record(pid, bad, good)
    text_blob = f"{ei.value} {ei.value.code} {ei.value.message}"
    assert _SECRET_BODY not in text_blob
    assert pid not in text_blob
    assert "esv_" not in text_blob or ei.value.message == MSG_REVISION_INVALID
    assert "SELECT" not in text_blob
    assert "sqlite" not in text_blob.lower()
    assert "Traceback" not in text_blob
