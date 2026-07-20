"""
模块：P13-C 当前已载入版本修订来源可见性专项测试
用途：验收 GET|PUT editor-state 响应字段 currentRevisionSourceKind 只读解析最新修订来源。
对接：editor_state_revision_service 只读 helper；EditorStateOut；projects GET|PUT editor-state。
二次开发：禁止加载 snapshot、回扫旧同版本、写账本/迁移/改 13 键；并发不匹配必须保守 null。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.database import SessionLocal, engine
from app.models.entities import EditorStateRevisionRow, ProjectEditorStateRow, Workspace
from app.services import editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p13c"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_NINE_SOURCES = (
    "browser_put",
    "task",
    "revise",
    "callback",
    "local_parser",
    "content_fuse_apply",
    "content_fuse_consume",
    "checkpoint_restore",
    "revision_restore",
)
_SECRET = "SECRET_P13C_MUST_NOT_LEAK"


def _create_project(client: TestClient, name: str = "P13C项目") -> str:
    res = client.post("/api/projects", json={"name": name, "mode": "technical"})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _put(client: TestClient, pid: str, payload: dict, *, expect_status: int = 200):
    res = client.put(f"/api/projects/{pid}/editor-state", json=payload)
    assert res.status_code == expect_status, res.text
    return res


def _get(client: TestClient, pid: str, *, expect_status: int = 200):
    res = client.get(f"/api/projects/{pid}/editor-state")
    assert res.status_code == expect_status, res.text
    return res


def _assert_sv(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _db_rev_rows(project_id: str, workspace_id: str | None = None) -> list[EditorStateRevisionRow]:
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


def _db_rev_count(project_id: str, workspace_id: str | None = None) -> int:
    return len(_db_rev_rows(project_id, workspace_id=workspace_id))


def _set_latest_source(project_id: str, source_kind: str) -> None:
    """用途：仅改最新修订来源，保持 state_version 与正文不变。"""
    db = SessionLocal()
    try:
        row = (
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
        assert row is not None
        row.source_kind = source_kind
        db.commit()
    finally:
        db.close()


def _ensure_workspace(ws_id: str, name: str = "其他空间P13C") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(Workspace(id=ws_id, name=name, owner_user_id="user_other_p13c"))
            db.commit()
    finally:
        db.close()


def _assert_no_leak(blob: str, *extra: str) -> None:
    low = blob.lower()
    assert _SECRET not in blob
    assert "traceback" not in low
    assert "select " not in low
    assert "sqlite" not in low
    for m in extra:
        if m:
            assert m not in blob


# ---------- failure-first：字段必须存在 ----------


def test_get_empty_ledger_returns_null_source_and_zero_write(client: TestClient):
    """用途：无账本时 currentRevisionSourceKind 精确 null；GET 零写。"""
    pid = _create_project(client, name="空账本")
    assert _db_rev_count(pid) == 0

    # 预读 editor-state 行是否存在（可能尚无）
    db0 = SessionLocal()
    try:
        editor0 = db0.get(ProjectEditorStateRow, pid)
        editor_updated0 = editor0.updated_at if editor0 is not None else None
        rev_total0 = db0.query(EditorStateRevisionRow).count()
    finally:
        db0.close()

    res = _get(client, pid)
    body = res.json()
    # 首个业务断言：字段必须存在且为 null
    assert "currentRevisionSourceKind" in body, list(body.keys())
    assert body["currentRevisionSourceKind"] is None
    _assert_sv(body["stateVersion"])

    db1 = SessionLocal()
    try:
        editor1 = db1.get(ProjectEditorStateRow, pid)
        editor_updated1 = editor1.updated_at if editor1 is not None else None
        rev_total1 = db1.query(EditorStateRevisionRow).count()
    finally:
        db1.close()
    assert rev_total1 == rev_total0
    assert _db_rev_count(pid) == 0
    assert editor_updated1 == editor_updated0


def test_browser_put_and_get_return_browser_put_source(client: TestClient):
    """用途：真实内容变更 PUT 返回 browser_put，随后 GET 一致。"""
    pid = _create_project(client, name="浏览器PUT来源")
    put = _put(
        client,
        pid,
        {"facts": [{"id": "f1", "text": "来源可见"}]},
    )
    put_body = put.json()
    assert "currentRevisionSourceKind" in put_body
    assert put_body["currentRevisionSourceKind"] == "browser_put"
    after = _assert_sv(put_body["stateVersion"])

    got = _get(client, pid).json()
    assert got["currentRevisionSourceKind"] == "browser_put"
    assert got["stateVersion"] == after


@pytest.mark.parametrize("source_kind", list(_NINE_SOURCES))
def test_nine_sources_match_latest_version(client: TestClient, source_kind: str):
    """用途：九类来源在最新版本匹配时原样返回。"""
    pid = _create_project(client, name=f"九源-{source_kind}")
    put = _put(
        client,
        pid,
        {"facts": [{"id": f"f_{source_kind}", "text": source_kind}]},
    ).json()
    ver = _assert_sv(put["stateVersion"])
    _set_latest_source(pid, source_kind)

    got = _get(client, pid).json()
    assert got["stateVersion"] == ver
    assert got["currentRevisionSourceKind"] == source_kind


def test_latest_version_mismatch_returns_null_no_backscan(client: TestClient):
    """用途：最新修订版本不匹配时返回 null，不得回扫旧同版本。"""
    pid = _create_project(client, name="断链不回扫")
    put1 = _put(
        client,
        pid,
        {"facts": [{"id": "old", "text": "旧版本匹配行"}]},
    ).json()
    old_ver = _assert_sv(put1["stateVersion"])
    assert put1["currentRevisionSourceKind"] == "browser_put"

    put2 = _put(
        client,
        pid,
        {
            "facts": [{"id": "new", "text": "新版本"}],
            "expectedStateVersion": old_ver,
        },
    ).json()
    new_ver = _assert_sv(put2["stateVersion"])
    assert new_ver != old_ver

    # 人为把最新行 state_version 改成永不匹配的假版本，保留旧同版本行
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
        latest.state_version = "esv_" + ("f" * 32)
        latest.source_kind = "task"
        db.commit()
    finally:
        db.close()

    # 当前 editor-state 仍是 new_ver；旧行仍有 old_ver 匹配机会，但禁止回扫
    got = _get(client, pid).json()
    assert got["stateVersion"] == new_ver
    assert got["currentRevisionSourceKind"] is None
    # 旧行确实仍存在且为 browser_put
    rows = _db_rev_rows(pid)
    assert any(r.state_version == old_ver and r.source_kind == "browser_put" for r in rows)


def test_corrupt_latest_source_returns_null_no_500(client: TestClient):
    """用途：最新来源非法时返回 null，不 500、不泄漏异常。
    约束：PRAGMA ignore_check_constraints 在任意成功/异常路径均须恢复为 0。"""
    pid = _create_project(client, name="坏来源")
    put = _put(
        client,
        pid,
        {"facts": [{"id": "c", "text": "正常事实"}]},
    ).json()
    ver = _assert_sv(put["stateVersion"])

    # SQLite CHECK 可能拦截非法 source_kind；能写入则测 HTTP 路径，否则测 helper 保守 null。
    # 必须用同一条显式连接完成 PRAGMA 开启、提交与恢复；Session.commit 会归还连接，
    # 随后的 Session.execute 可能换到池中另一条连接，导致原连接残留 PRAGMA=1。
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
        rid = latest.id
    finally:
        db.close()

    # 关闭约束 → 写坏行并 commit → 在同一物理连接立即恢复 0。
    with engine.connect() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = 1"))
        try:
            conn.execute(
                text(
                    "UPDATE editor_state_revisions SET source_kind = :sk WHERE id = :id"
                ),
                {"sk": "not_a_real_source", "id": rid},
            )
            conn.commit()
        finally:
            conn.execute(text("PRAGMA ignore_check_constraints = 0"))
            pragma_value = conn.execute(
                text("PRAGMA ignore_check_constraints")
            ).scalar()
            assert int(pragma_value) == 0, pragma_value

    wrote_corrupt = False
    db = SessionLocal()
    try:
        corrupted = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.id == rid)
            .one()
        )
        if corrupted.source_kind == "not_a_real_source":
            wrote_corrupt = True
            res = _get(client, pid)
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["stateVersion"] == ver
            assert body["currentRevisionSourceKind"] is None
            low = res.text.lower()
            assert "traceback" not in low
            assert "not_a_real_source" not in res.text
    finally:
        db.close()

    # 测试结束额外查询 PRAGMA 精确为 0（可能复用池连接）
    db_pragma = SessionLocal()
    try:
        pragma_val = db_pragma.execute(text("PRAGMA ignore_check_constraints")).scalar()
        assert int(pragma_val) == 0, pragma_val
    finally:
        db_pragma.close()

    if wrote_corrupt:
        return

    # 回退：helper 对非法 state_version 保守 null；合法匹配仍九类
    db = SessionLocal()
    try:
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS, pid, ""
            )
            is None
        )
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS, pid, "not-a-version"
            )
            is None
        )
        kind = editor_state_revision_service.resolve_current_revision_source_kind(
            db, _WS, pid, ver
        )
        # 明确分支：要么九类之一，要么 None；禁止恒真 or 掩盖
        if kind is not None:
            assert kind in _NINE_SOURCES
    finally:
        db.close()


def test_noop_put_follows_ledger_latest(client: TestClient):
    """用途：no-op PUT 返回值与账本权威最新行一致，不臆造内容变更来源。"""
    pid = _create_project(client, name="noopPUT")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "n1", "text": "稳定"}]},
    ).json()
    ver = _assert_sv(base["stateVersion"])
    assert base["currentRevisionSourceKind"] == "browser_put"
    n0 = _db_rev_count(pid)

    # 同内容 no-op：期望版本匹配，可能 0 新增修订
    noop = _put(
        client,
        pid,
        {
            "facts": [{"id": "n1", "text": "稳定"}],
            "expectedStateVersion": ver,
        },
    ).json()
    assert noop["stateVersion"] == ver
    n1 = _db_rev_count(pid)
    # 相邻同版本去重：条数不增
    assert n1 == n0
    # 来源仍与最新账本一致
    assert noop["currentRevisionSourceKind"] == "browser_put"
    got = _get(client, pid).json()
    assert got["currentRevisionSourceKind"] == "browser_put"
    assert got["stateVersion"] == ver


def test_cross_workspace_and_missing_project_404(client: TestClient):
    """用途：跨空间/不存在项目固定 404，不泄漏来源字段值。"""
    missing = client.get("/api/projects/proj_does_not_exist_p13c/editor-state")
    assert missing.status_code == 404, missing.text
    _assert_no_leak(missing.text)
    assert "browser_put" not in missing.text
    assert "currentRevisionSourceKind" not in missing.text

    pid = _create_project(client, name="跨空间")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "local", "text": "仅本空间"}]},
    ).json()
    assert base["currentRevisionSourceKind"] == "browser_put"

    _ensure_workspace(_WS_OTHER)
    cross = client.get(
        f"/api/projects/{pid}/editor-state",
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert cross.status_code == 404, cross.text
    _assert_no_leak(cross.text, pid, _WS, base["stateVersion"])
    assert "browser_put" not in cross.text
    assert "currentRevisionSourceKind" not in cross.text


def test_conflict_422_zero_write_unchanged(client: TestClient):
    """用途：409/422 合同与零写语义不变。"""
    pid = _create_project(client, name="冲突零写")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "z0", "text": "基线"}]},
    ).json()
    ver = _assert_sv(base["stateVersion"])
    n0 = _db_rev_count(pid)

    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": "z1", "text": _SECRET}],
            "expectedStateVersion": "esv_" + ("0" * 32),
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json().get("detail") or {}
    assert detail.get("code") == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    _assert_no_leak(conflict.text, _SECRET)
    assert _db_rev_count(pid) == n0

    bad = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": "z2", "text": _SECRET}],
            "expectedStateVersion": "not-a-version",
        },
    )
    assert bad.status_code == 422, bad.text
    _assert_no_leak(bad.text, _SECRET)
    assert _db_rev_count(pid) == n0

    got = _get(client, pid).json()
    assert got["stateVersion"] == ver
    assert got["currentRevisionSourceKind"] == "browser_put"


def test_sql_projects_only_two_columns_limit_one(client: TestClient):
    """用途：SQL 合同——GET 期间 editor_state_revisions SELECT 恰好 1 条；
    投影精确 5 列固定顺序 state_version/source_kind/username/user_is_active/member_is_active；
    actor_user_id 仅两处 JOIN ON；WHERE workspace+project；ORDER/LIMIT=1/OFFSET=0；
    无 snapshot/password/salt/hash/session/audit；GET actor null 合法。"""
    pid = _create_project(client, name="SQL证据")
    put = _put(
        client,
        pid,
        {"facts": [{"id": "sql", "text": "投影"}]},
    ).json()
    ver = _assert_sv(put["stateVersion"])

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
        got = _get(client, pid).json()
        assert got["currentRevisionSourceKind"] == "browser_put"
        assert got["stateVersion"] == ver
        # P13-D2 合法新键：无账本 actor 时为 null 但仍必出
        assert "currentRevisionActorUsername" in got
        assert got["currentRevisionActorUsername"] is None
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # 精确恰好一次，不得弱化为「至少含」
    assert len(captured) == 1, [s for s, _ in captured]
    statement, parameters = captured[0]
    low = " ".join(statement.lower().split())

    m = re.search(r"select\s+(.+?)\s+from\s+", low)
    assert m is not None, low
    proj = m.group(1)
    assert "select *" not in low
    proj_items = [p.strip() for p in proj.split(",")]
    assert len(proj_items) == 5, proj_items
    assert "state_version" in proj_items[0]
    assert "source_kind" in proj_items[1]
    assert "username" in proj_items[2]
    assert "user_is_active" in proj_items[3]
    assert "member_is_active" in proj_items[4]
    # actor_user_id 仅 JOIN ON，不得进投影
    assert "actor_user_id" not in proj
    assert low.count("actor_user_id") == 2, low
    assert re.search(
        r"local_users\.id\s*=\s*editor_state_revisions\.actor_user_id", low
    ), low
    assert re.search(
        r"workspace_members\.user_id\s*=\s*editor_state_revisions\.actor_user_id",
        low,
    ), low
    assert re.search(
        r"workspace_members\.workspace_id\s*=\s*editor_state_revisions\.workspace_id",
        low,
    ), low
    assert "local_users" in low
    assert "workspace_members" in low

    assert re.search(
        r"editor_state_revisions\.workspace_id\s*=\s*\?", low
    ), low
    assert re.search(
        r"editor_state_revisions\.project_id\s*=\s*\?", low
    ), low
    assert re.search(
        r"order by\s+editor_state_revisions\.created_at\s+desc\s*,\s*"
        r"editor_state_revisions\.id\s+desc",
        low,
    ), low
    assert "limit ?" in low
    assert "offset ?" in low
    assert parameters is not None
    params = list(parameters)
    assert params == [_WS, pid, 1, 0], params

    for banned in (
        "snapshot",
        "password",
        "salt",
        "hash",
        "session",
        "audit",
    ):
        assert banned not in low, banned


def test_helper_direct_scope_and_order(client: TestClient):
    """用途：helper 仅认本 workspace+project 最新一条；跨项目/跨空间互不污染。"""
    pid_a = _create_project(client, name="作用域A")
    pid_b = _create_project(client, name="作用域B")
    put_a = _put(
        client,
        pid_a,
        {"facts": [{"id": "a", "text": "A"}]},
    ).json()
    put_b = _put(
        client,
        pid_b,
        {"facts": [{"id": "b", "text": "B"}]},
    ).json()
    ver_a = _assert_sv(put_a["stateVersion"])
    ver_b = _assert_sv(put_b["stateVersion"])
    _set_latest_source(pid_a, "task")
    _set_latest_source(pid_b, "revise")

    db = SessionLocal()
    try:
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS, pid_a, ver_a
            )
            == "task"
        )
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS, pid_b, ver_b
            )
            == "revise"
        )
        # 版本串错项目 → null
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS, pid_a, ver_b
            )
            is None
        )
        # 错误 workspace → null
        assert (
            editor_state_revision_service.resolve_current_revision_source_kind(
                db, _WS_OTHER, pid_a, ver_a
            )
            is None
        )
    finally:
        db.close()
