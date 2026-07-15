"""
模块：P12C-B-A 浏览器 PUT 修订账本原子接入专项测试
用途：真实 FastAPI + SQLite 验收 browser_put 同锁同事务、双零写、
  来源隔离、客户端伪造无效与无额外读/锁/commit。
对接：PUT /api/projects/{id}/editor-state；upsert_editor_state；
  record_editor_state_transition（局部导入）。
二次开发：禁止 mock 掉 SQLite、宽泛状态码、or True；本包不得声称历史浏览已可用。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    ProjectEditorStateRow,
    Workspace,
)
from app.services import editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12cba"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SECRET = "SECRET_P12CBA_BODY_MUST_NOT_LEAK"
_SOURCE_BROWSER = "browser_put"
_INJECT_AFTER_FLUSH = "p12cba_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cba_injected_commit_failure"


def _create_project(client: TestClient, name: str = "P12C-B-A项目") -> str:
    res = client.post("/api/projects", json={"name": name, "mode": "technical"})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _put(
    client: TestClient,
    pid: str,
    payload: dict,
    *,
    expect_status: int = 200,
):
    res = client.put(f"/api/projects/{pid}/editor-state", json=payload)
    assert res.status_code == expect_status, res.text
    return res


def _get(client: TestClient, pid: str) -> dict:
    res = client.get(f"/api/projects/{pid}/editor-state")
    assert res.status_code == 200, res.text
    return res.json()


def _db_rev_rows(
    project_id: str, workspace_id: str | None = None
) -> list[EditorStateRevisionRow]:
    """
    用途：按契约序 created_at DESC, id DESC 读取 revision（最新在前）。
    二次开发：禁止用 ASC/数组尾部臆测插入顺序；定位 after 必须按 state_version。
    """
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


def _db_editor_facts_marker(project_id: str) -> str | None:
    """用途：新 Session 读 editor-state 行，避免同一身份映射假绿。"""
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.facts_json
    finally:
        db.close()


def _assert_state_version(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _assert_no_leak(blob: str, *extra: str) -> None:
    low = blob.lower()
    assert _SECRET not in blob
    assert "traceback" not in low
    assert "select " not in low
    assert "sqlite" not in low
    assert "insert into" not in low
    for m in extra:
        if m:
            assert m not in blob


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    """用途：按 state_version 精确定位，禁止下标/尾部推断。"""
    return [r for r in rows if r.state_version == state_version]


def _assert_browser_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    """用途：恰有一条 after 行，来源必须是 browser_put。"""
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [r.state_version for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_BROWSER
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _ensure_workspace(ws_id: str, name: str = "其他空间P12CBA") -> None:
    """用途：真实插入跨空间 Workspace，禁止伪造仅服务层异常。"""
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12cba",
                )
            )
            db.commit()
    finally:
        db.close()


# ---------- failure-first / 生产写入接入 ----------


def test_first_browser_put_writes_before_and_after_browser_put(client):
    """用途：空账本首次浏览器 PUT 写 before+after，来源均为 browser_put。"""
    pid = _create_project(client, name="首次PUT")
    assert _db_rev_count(pid) == 0

    put = _put(
        client,
        pid,
        {
            "facts": [{"id": "f1", "text": "首次事实"}],
            "guidance": {"note": "g1"},
        },
    )
    body = put.json()
    after_ver = _assert_state_version(body["stateVersion"])
    assert body["facts"][0]["id"] == "f1"
    # 响应不得出现来源或 revision 正文/ID
    raw = put.text
    assert "sourceKind" not in raw
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "browser_put" not in raw
    assert "esr_" not in raw

    rows = _db_rev_rows(pid)
    assert len(rows) == 2, [r.state_version for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_BROWSER}
    for r in rows:
        assert _REVISION_ID_RE.fullmatch(r.id)
        assert r.workspace_id == _WS
        assert r.project_id == pid
    # 按 state_version 精确定位 after，禁止下标/尾部臆测
    after_row = _assert_browser_after(rows, after_ver)
    versions = {r.state_version for r in rows}
    assert len(versions) == 2
    assert after_ver in versions
    before_ver = next(v for v in versions if v != after_ver)
    assert _assert_state_version(before_ver) != after_ver
    assert after_row.state_version == after_ver


def test_second_put_appends_only_new_after_and_dedupes_same_state(client):
    """用途：连续 PUT 只追加新 after；重复同一规范状态不追加相邻重复。"""
    pid = _create_project(client, name="连续去重")
    r1 = _put(
        client,
        pid,
        {"facts": [{"id": "a", "text": "一"}], "guidance": {"n": 1}},
    ).json()
    assert _db_rev_count(pid) == 2
    v1 = r1["stateVersion"]
    _assert_browser_after(_db_rev_rows(pid), v1)

    r2 = _put(
        client,
        pid,
        {
            "facts": [{"id": "b", "text": "二"}],
            "guidance": {"n": 2},
            "expectedStateVersion": v1,
        },
    ).json()
    v2 = r2["stateVersion"]
    assert v2 != v1
    rows_after_second = _db_rev_rows(pid)
    # 第二次：before 已是最新 → 只追加 after → 共 3 条
    assert len(rows_after_second) == 3
    _assert_browser_after(rows_after_second, v2)
    assert _rows_by_version(rows_after_second, v1)
    assert all(r.source_kind == _SOURCE_BROWSER for r in rows_after_second)

    # 重复同一规范状态（facts/guidance 不变）
    r3 = _put(
        client,
        pid,
        {
            "facts": [{"id": "b", "text": "二"}],
            "guidance": {"n": 2},
            "expectedStateVersion": v2,
        },
    ).json()
    assert r3["stateVersion"] == v2
    assert _db_rev_count(pid) == 3  # 相邻同版本去重，不追加
    _assert_browser_after(_db_rev_rows(pid), v2)


def test_expected_and_matrix_and_compat_paths_record_locked_before(client):
    """用途：带 expected / 无 expected 兼容 / 仅矩阵版本 均记录正确锁后 before/after。"""
    pid = _create_project(client, name="三路径")
    base = _put(
        client,
        pid,
        {
            "outline": [
                {
                    "id": "n1",
                    "title": "章",
                    "children": [],
                }
            ],
            "chapters": [
                {
                    "id": "c1",
                    "title": "节",
                    "body": "正文",
                    "status": "pending",
                    "preview": "正文",
                    "wordCount": 2,
                }
            ],
            "responseMatrix": [
                {
                    "id": "mx1",
                    "kind": "requirement",
                    "sourceKey": "requirement:x",
                    "sourceIndex": 0,
                    "sourceText": "x",
                    "chapterIds": ["c1"],
                    "outlineNodeIds": ["n1"],
                    "status": "partial",
                    "notes": "",
                }
            ],
            "facts": [{"id": "f0", "text": "基线"}],
        },
    ).json()
    v0 = base["stateVersion"]
    mv0 = base["responseMatrixVersion"]
    n_base = _db_rev_count(pid)
    assert n_base == 2

    # 1) 带 expected
    r_exp = _put(
        client,
        pid,
        {
            "facts": [{"id": "f_exp", "text": "expected路径"}],
            "expectedStateVersion": v0,
        },
    ).json()
    rows = _db_rev_rows(pid)
    assert len(rows) == n_base + 1
    v1 = r_exp["stateVersion"]
    _assert_browser_after(rows, v1)
    assert _rows_by_version(rows, v0)  # before 版本仍在账本
    mv1 = r_exp["responseMatrixVersion"]

    # 2) 无 expected 兼容写（仍必须进入写锁并记账）
    r_compat = _put(
        client,
        pid,
        {"facts": [{"id": "f_compat", "text": "兼容路径"}]},
    ).json()
    assert r_compat["stateVersion"] != v1
    rows = _db_rev_rows(pid)
    assert len(rows) == n_base + 2
    v2 = r_compat["stateVersion"]
    _assert_browser_after(rows, v2)
    mv2 = r_compat["responseMatrixVersion"]
    # 无 expected 不得因进入来源锁而假 409
    assert r_compat["facts"][0]["id"] == "f_compat"
    assert mv1  # 使用过中间矩阵版本，避免未使用告警

    # 3) 仅矩阵版本写
    matrix_payload = list(r_compat["responseMatrix"] or [])
    if matrix_payload:
        matrix_payload = [
            {**matrix_payload[0], "notes": "矩阵路径备注", "status": "covered"}
        ]
    r_mx = _put(
        client,
        pid,
        {
            "responseMatrix": matrix_payload,
            "responseMatrixVersion": mv2,
        },
    ).json()
    assert r_mx["stateVersion"] != v2
    rows = _db_rev_rows(pid)
    assert len(rows) == n_base + 3
    _assert_browser_after(rows, r_mx["stateVersion"])
    # 矩阵版本应变化
    assert r_mx["responseMatrixVersion"] != mv2 or r_mx["responseMatrix"][0]["notes"] == (
        "矩阵路径备注"
    )


def test_stale_expected_and_matrix_409_double_zero(client):
    """用途：陈旧 expected 与陈旧矩阵版本固定 409，editor-state 与 revision 零写。"""
    pid = _create_project(client, name="陈旧冲突")
    base = _put(
        client,
        pid,
        {
            "facts": [{"id": "stale0", "text": "基线"}],
            "responseMatrix": [
                {
                    "id": "mx_s",
                    "kind": "requirement",
                    "sourceKey": "requirement:s",
                    "sourceIndex": 0,
                    "sourceText": "s",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "uncovered",
                    "notes": "",
                }
            ],
        },
    ).json()
    v0 = base["stateVersion"]
    mv0 = base["responseMatrixVersion"]
    n0 = _db_rev_count(pid)
    facts0 = _db_editor_facts_marker(pid)

    # 先用正确 expected 推进一次，使 v0 陈旧
    mid = _put(
        client,
        pid,
        {
            "facts": [{"id": "stale1", "text": "已前进"}],
            "expectedStateVersion": v0,
        },
    ).json()
    n1 = _db_rev_count(pid)
    assert n1 > n0
    facts1 = _db_editor_facts_marker(pid)
    assert facts1 != facts0

    # 陈旧 expected
    conflict = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": "should_not", "text": _SECRET}],
            "expectedStateVersion": v0,
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json().get("detail") or {}
    _assert_no_leak(conflict.text, pid)
    assert detail.get("code") == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    assert _db_rev_count(pid) == n1
    assert _db_editor_facts_marker(pid) == facts1
    got = _get(client, pid)
    assert got["facts"][0]["id"] == "stale1"
    assert got["stateVersion"] == mid["stateVersion"]

    # 陈旧矩阵版本
    conflict_m = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "responseMatrix": [
                {
                    "id": "mx_s",
                    "kind": "requirement",
                    "sourceKey": "requirement:s",
                    "sourceIndex": 0,
                    "sourceText": "s",
                    "chapterIds": [],
                    "outlineNodeIds": [],
                    "status": "covered",
                    "notes": _SECRET,
                }
            ],
            "responseMatrixVersion": "rmv_stale_not_match",
            "expectedStateVersion": mid["stateVersion"],
        },
    )
    # 若矩阵版本比较触发冲突应为 409；也可能全状态路径先成功再比矩阵
    assert conflict_m.status_code == 409, conflict_m.text
    _assert_no_leak(conflict_m.text, _SECRET)
    assert _db_rev_count(pid) == n1
    assert _db_editor_facts_marker(pid) == facts1
    got2 = _get(client, pid)
    assert got2["stateVersion"] == mid["stateVersion"]
    assert got2["responseMatrixVersion"] == mid["responseMatrixVersion"]
    assert mv0  # 使用过基线矩阵版本，避免未使用告警


def test_missing_project_and_cross_workspace_404_no_revision(client):
    """
    用途：不存在项目与真实跨工作空间隔离均固定 404，不产生旁路 revision。
    二次开发：跨空间必须走真实 Workspace + X-Workspace-Id HTTP，禁止伪服务层异常。
    """
    before_total = SessionLocal()
    try:
        total0 = before_total.query(EditorStateRevisionRow).count()
    finally:
        before_total.close()

    # 1) 不存在项目
    res = client.put(
        "/api/projects/proj_does_not_exist_p12cba/editor-state",
        json={"facts": [{"id": "x", "text": _SECRET}]},
    )
    assert res.status_code == 404, res.text
    _assert_no_leak(res.text, _SECRET, "proj_does_not_exist_p12cba")

    after = SessionLocal()
    try:
        total1 = after.query(EditorStateRevisionRow).count()
    finally:
        after.close()
    assert total1 == total0

    # 2) 真实跨工作空间：ws_local 已有项目，用其他空间头 PUT → 404
    pid = _create_project(client, name="跨空间隔离")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "local_only", "text": "仅本空间"}]},
    ).json()
    n_local = _db_rev_count(pid, workspace_id=_WS)
    facts_local = _db_editor_facts_marker(pid)
    v_local = base["stateVersion"]
    assert n_local >= 2

    _ensure_workspace(_WS_OTHER)
    cross = client.put(
        f"/api/projects/{pid}/editor-state",
        headers={"X-Workspace-Id": _WS_OTHER},
        json={"facts": [{"id": "cross", "text": _SECRET}]},
    )
    assert cross.status_code == 404, cross.text
    _assert_no_leak(cross.text, _SECRET, pid, _WS, _WS_OTHER, v_local)
    assert "local_only" not in cross.text

    # ws_local 正文与 revision 精确不变；其他空间 revision 为零
    assert _db_rev_count(pid, workspace_id=_WS) == n_local
    assert _db_editor_facts_marker(pid) == facts_local
    assert _db_rev_count(pid, workspace_id=_WS_OTHER) == 0
    got = _get(client, pid)
    assert got["stateVersion"] == v_local
    assert got["facts"][0]["id"] == "local_only"


def test_recorder_flush_then_fail_double_zero_rollback(client, monkeypatch):
    """
    用途：recorder 已 flush 后注入失败 → 真实 HTTP 500 脱敏，editor-state 与 revision 双零写。
    二次开发：raise_server_exceptions=False 拿响应证据；禁止吞异常仍提交正文。
    """
    pid = _create_project(client, name="注入回滚")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "ok0", "text": "稳定基线"}], "guidance": {"k": 0}},
    ).json()
    n0 = _db_rev_count(pid)
    facts0 = _db_editor_facts_marker(pid)
    v0 = base["stateVersion"]

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        # 此时 revision 已在同一事务 flush；随后失败必须整事务 rollback
        assert out["added_count"] >= 1
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    # 局部导入路径：upsert 内 from ... import record_editor_state_transition，
    # 必须同时 patch 模块属性，确保真实 recorder 成功后再抛。
    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )

    # 另开 TestClient 且不重置库：文件型 SQLite 共享，正确关闭，无污染
    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.put(
            f"/api/projects/{pid}/editor-state",
            json={
                "facts": [{"id": "boom", "text": _SECRET}],
                "expectedStateVersion": v0,
            },
        )
    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_no_leak(
        res.text,
        _SECRET,
        _INJECT_AFTER_FLUSH,
        pid,
        v0,
        "RuntimeError",
        "p12cba",
    )

    # 新 Session：正文与本轮 revision 精确双零写
    assert _db_rev_count(pid) == n0
    assert _db_editor_facts_marker(pid) == facts0
    got = _get(client, pid)
    assert got["facts"][0]["id"] == "ok0"
    assert got["stateVersion"] == v0
    assert got["facts"][0]["text"] != _SECRET
    assert _SECRET not in (got.get("facts") or [{}])[0].get("text", "")


def test_revision_commit_failure_double_zero_via_service(client):
    """
    用途：带 revision_source_kind 的 commit 失败 → 显式 rollback，editor-state 与 revision 双零。
    二次开发：真实 Session；recorder 已 flush 后 commit 才失败；异常原文不进 HTTP/库。
    """
    pid = _create_project(client, name="commit失败双零")
    base = _put(
        client,
        pid,
        {"facts": [{"id": "stable", "text": "commit基线"}], "guidance": {"c": 1}},
    ).json()
    n0 = _db_rev_count(pid)
    facts0 = _db_editor_facts_marker(pid)
    v0 = base["stateVersion"]
    assert n0 >= 2

    rollbacks = {"n": 0}
    commit_probe = {"n": 0}
    db = SessionLocal()
    try:
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*args, **kwargs):
            # 同一 Session/同一事务：证明 recorder 已在 commit 前 flush 出 after 行
            # （若生产误先 commit 再 recorder，此处仍为 n0，断言会失败）
            commit_probe["n"] += 1
            pending = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            assert pending == n0 + 1, (
                f"commit 前 revision 应已 flush 至 n0+1，实际 {pending}（n0={n0}）"
            )
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        def _count_rollback(*args, **kwargs):
            rollbacks["n"] += 1
            return real_rollback(*args, **kwargs)

        db.commit = _bad_commit  # type: ignore[method-assign]
        db.rollback = _count_rollback  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match=_INJECT_COMMIT_FAIL):
            editor_state_service.upsert_editor_state(
                db,
                _WS,
                pid,
                facts=[{"id": "should_not", "text": _SECRET}],
                expected_state_version=v0,
                revision_source_kind=_SOURCE_BROWSER,
            )
        assert commit_probe["n"] == 1, "commit 探测应精确调用 1 次"
        assert rollbacks["n"] >= 1, "commit 异常必须触发显式 rollback"
        assert not db.in_transaction()
    finally:
        db.close()

    # 新 Session：正文与 revision 均未变化；异常原文不得入库
    assert _db_rev_count(pid) == n0
    assert _db_editor_facts_marker(pid) == facts0
    got = _get(client, pid)
    assert got["stateVersion"] == v0
    assert got["facts"][0]["id"] == "stable"
    assert got["facts"][0]["text"] != _SECRET
    assert _SECRET not in (got.get("facts") or [{}])[0].get("text", "")
    for r in _db_rev_rows(pid):
        assert _INJECT_COMMIT_FAIL not in (r.snapshot_json or "")
        assert _SECRET not in (r.snapshot_json or "")


def test_parallel_created_at_tie_break_stable_order(client):
    """
    用途：真实并列时间戳探针——先 PUT 生成行，再统一 created_at，
      只验证契约排序 created_at DESC,id DESC 稳定；此后不再 transition。
    二次开发：禁止把随机 ID 当插入序；不在改写时间戳后继续记账。
    """
    pid = _create_project(client, name="并列时间戳")
    r1 = _put(
        client,
        pid,
        {"facts": [{"id": "t0", "text": "并列0"}]},
    ).json()
    v1 = r1["stateVersion"]
    r2 = _put(
        client,
        pid,
        {
            "facts": [{"id": "t1", "text": "并列1"}],
            "expectedStateVersion": v1,
        },
    ).json()
    v2 = r2["stateVersion"]
    assert v2 != v1

    fixed_ts = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        rows_live = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == pid)
            .all()
        )
        assert len(rows_live) == 3  # 首写 before+after + 第二次 after
        for row in rows_live:
            row.created_at = fixed_ts
        db.commit()
    finally:
        db.close()

    rows = _db_rev_rows(pid)
    assert len(rows) == 3
    ids = [r.id for r in rows]
    # 契约：created_at 并列 → id DESC
    assert ids == sorted(ids, reverse=True)
    assert rows[0].id == max(ids)
    # 仍可按版本精确定位 after，不依赖下标即插入序
    _assert_browser_after(rows, v1)
    _assert_browser_after(rows, v2)
    assert all(r.source_kind == _SOURCE_BROWSER for r in rows)
    assert all(r.created_at == rows[0].created_at for r in rows)


def test_direct_upsert_without_source_writes_zero_revision(client):
    """用途：直接服务调用不传来源 → 业务可成功但 revision 精确为零。"""
    pid = _create_project(client, name="内部无源")
    # 先用 API 建一条基线（会有 revision）；再证明纯服务调用不追加
    _put(client, pid, {"facts": [{"id": "api", "text": "api基线"}]})
    n0 = _db_rev_count(pid)
    assert n0 >= 2

    db = SessionLocal()
    try:
        result = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            facts=[{"id": "svc", "text": "服务直写"}],
        )
        assert result["facts"][0]["id"] == "svc"
        _assert_state_version(result["stateVersion"])
    finally:
        db.close()

    assert _db_rev_count(pid) == n0  # 不传来源 → 零追加
    got = _get(client, pid)
    assert got["facts"][0]["id"] == "svc"


def test_client_forged_source_fields_ignored(client):
    """用途：客户端伪造 sourceKind 等无效，行来源只能是 browser_put；响应无来源字段。"""
    pid = _create_project(client, name="伪造来源")
    res = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": "forge", "text": "伪造测试"}],
            "sourceKind": "task",
            "revisionSourceKind": "revise",
            "revision_source_kind": "callback",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    raw = res.text
    assert "sourceKind" not in body
    assert "revisionSourceKind" not in body
    assert "revision_source_kind" not in body
    assert "browser_put" not in raw
    assert "esr_" not in raw
    for key in ("sourceKind", "revisionSourceKind", "revision_source_kind"):
        assert key not in body

    rows = _db_rev_rows(pid)
    assert len(rows) == 2
    assert {r.source_kind for r in rows} == {_SOURCE_BROWSER}
    assert "task" not in {r.source_kind for r in rows}
    assert "revise" not in {r.source_kind for r in rows}
    assert "callback" not in {r.source_kind for r in rows}


def test_no_extra_get_lock_refresh_or_multi_commit(client, monkeypatch):
    """用途：记录过程无二次 editor-state 读取、无第二把锁、无 refresh、无多次 commit。"""
    pid = _create_project(client, name="单锁单提交")
    base = _put(client, pid, {"facts": [{"id": "l0", "text": "锁基线"}]}).json()
    v0 = base["stateVersion"]

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "project_editor_states" in low or re.search(r"\bprojects\b", low):
            captured.append(statement)

    # 通过服务层直接调用，精确计数 SQL 与 commit
    project_updates = []
    editor_selects = []
    commits = {"n": 0}
    refreshes = {"n": 0}

    event.listen(engine, "before_cursor_execute", _capture)
    db = SessionLocal()
    try:
        real_commit = db.commit
        real_refresh = db.refresh

        def _count_commit():
            commits["n"] += 1
            return real_commit()

        def _count_refresh(*a, **k):
            refreshes["n"] += 1
            raise RuntimeError("不得 refresh")

        db.commit = _count_commit  # type: ignore[method-assign]
        db.refresh = _count_refresh  # type: ignore[method-assign]

        # 禁止 get_editor_state 二次读取
        def _boom_get(*a, **k):
            raise RuntimeError("不得调用 get_editor_state")

        monkeypatch.setattr(editor_state_service, "get_editor_state", _boom_get)

        result = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            facts=[{"id": "l1", "text": "锁后写"}],
            expected_state_version=v0,
            revision_source_kind=_SOURCE_BROWSER,
        )
        assert result["facts"][0]["id"] == "l1"
        assert commits["n"] == 1
        assert refreshes["n"] == 0
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
        db.close()
        monkeypatch.undo()

    for stmt in captured:
        low = stmt.lower().strip()
        if low.startswith("update") and "projects" in low and "project_editor" not in low:
            project_updates.append(stmt)
        if low.startswith("select") and "project_editor_states" in low:
            editor_selects.append(stmt)

    assert len(project_updates) == 1, project_updates
    assert len(editor_selects) == 1, editor_selects

    # 新 Session 确认 revision 已写入
    rows = _db_rev_rows(pid)
    assert any(r.state_version == result["stateVersion"] for r in rows)


def test_checkpoints_and_p12ca_quota_unaffected(client):
    """用途：P12C-A 最近 10 条语义与检查点域完全不受本包破坏。"""
    pid = _create_project(client, name="域隔离")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    db = SessionLocal()
    try:
        cp_count = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == pid)
            .count()
        )
        cp_row = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == pid)
            .one()
        )
        cp_id = cp_row.id
        cp_json = cp_row.snapshot_json
        cp_ver = cp_row.state_version
    finally:
        db.close()
    assert cp_count == 1

    # 多次 PUT 产生 revision，不超过 10 条裁剪语义（11 次状态变化后仍 ≤10）
    prev = _get(client, pid)
    for i in range(12):
        prev = _put(
            client,
            pid,
            {
                "facts": [{"id": f"q{i}", "text": f"配额{i}"}],
                "expectedStateVersion": prev["stateVersion"],
            },
        ).json()
    n = _db_rev_count(pid)
    assert n == 10, n
    assert all(r.source_kind == _SOURCE_BROWSER for r in _db_rev_rows(pid))

    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cp_id)
        assert row is not None
        assert row.snapshot_json == cp_json
        assert row.state_version == cp_ver
        assert (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == pid)
            .count()
            == 1
        )
    finally:
        db.close()


def test_upsert_default_revision_source_is_none_signature():
    """用途：签名默认 revision_source_kind=None，不得默认 browser_put。"""
    import inspect

    sig = inspect.signature(editor_state_service.upsert_editor_state)
    param = sig.parameters["revision_source_kind"]
    assert param.default is None


def test_compat_put_without_expected_does_not_false_409_on_matrix_none(client):
    """
    用途：无 expected 且无矩阵版本的浏览器 PUT 进入写锁后不得假 409。
    回归：为来源加锁后错误比较 None != current_matrix_version。
    """
    pid = _create_project(client, name="假409防护")
    _put(client, pid, {"facts": [{"id": "z0", "text": "z"}]})
    n0 = _db_rev_count(pid)
    res = _put(client, pid, {"facts": [{"id": "z1", "text": "z2"}]})
    body = res.json()
    assert body["facts"][0]["id"] == "z1"
    assert _db_rev_count(pid) == n0 + 1
