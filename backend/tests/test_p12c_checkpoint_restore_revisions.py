"""
模块：P12C-B-D3 checkpoint restore 修订账本原子接入专项测试
用途：真实 HTTP + SQLite 验收 checkpoint_restore 条件记账、精确 +1、
  同内容零修订、失败三域回滚、双并发一次性和反假绿。
对接：POST .../editor-state-checkpoints/{id}/restore；
  record_editor_state_transition；editor_state_checkpoint_service。
二次开发：禁止 mock 掉 SQLite、>= 宽松增量、空集合、随机 ID 顺序、
  固定 sleep、顺序调用冒充并发、跨项目冒充跨空间、AST 冒充原子性。
"""

from __future__ import annotations

import ast
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
)
from app.services import (
    editor_state_checkpoint_service,
    editor_state_revision_service,
    editor_state_service,
)

_WS = "ws_local"
_WS_OTHER = "ws_other_p12cbd3"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_SOURCE_RESTORE = "checkpoint_restore"
_SOURCE_BROWSER = "browser_put"
_SOURCE_APPLY = "content_fuse_apply"
_SOURCE_CONSUME = "content_fuse_consume"
_SECRET = "SECRET_P12CBD3_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbd3_injected_after_flush"
_INJECT_REV_TRIM = "p12cbd3_injected_revision_trim"
_INJECT_CP_TRIM = "p12cbd3_injected_checkpoint_trim"
_INJECT_COMMIT_FAIL = "p12cbd3_injected_commit_failure"
_RESTORE_KEYS = frozenset(
    {
        "restoredCheckpointId",
        "safetyCheckpointId",
        "stateVersion",
        "restoredAt",
    }
)
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_SANITIZE_FORBIDDEN = (
    "editor_state_revisions",
    "editor_state_checkpoints",
    "editor_state_checkpoint_service",
    "editor_state_revision_service",
    "editor_state_service.py",
    "editor_state_checkpoint_service.py",
    "backend/app/services",
    "backend\\app\\services",
    str(Path(__file__).resolve().parents[1] / "app" / "services"),
)

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_service.py"
)


# ---------- 基础辅助 ----------


def _assert_state_version(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _create_project(
    client: TestClient,
    name: str = "P12C-B-D3",
    kind: str = "technical",
) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _get_state(client: TestClient, pid: str) -> dict:
    res = client.get(f"/api/projects/{pid}/editor-state")
    assert res.status_code == 200, res.text
    return res.json()


def _put_state(client: TestClient, pid: str, body: dict) -> dict:
    res = client.put(f"/api/projects/{pid}/editor-state", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _cp_url(project_id: str, checkpoint_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-checkpoints"
    if checkpoint_id is None:
        return base
    return f"{base}/{checkpoint_id}"


def _restore_url(project_id: str, checkpoint_id: str) -> str:
    return f"{_cp_url(project_id, checkpoint_id)}/restore"


def _create_checkpoint(client: TestClient, pid: str) -> dict:
    res = client.post(_cp_url(pid), json={})
    assert res.status_code == 201, res.text
    return res.json()


def _restore(client: TestClient, pid: str, cid: str, expected: str):
    return client.post(
        _restore_url(pid, cid),
        json={"expectedStateVersion": expected},
    )


def _assert_success_restore(res, *, target_version: str) -> dict:
    assert res.status_code == 200, res.text
    assert res.headers.get("Cache-Control") == "no-store"
    body = res.json()
    assert set(body.keys()) == _RESTORE_KEYS
    after_ver = _assert_state_version(body["stateVersion"])
    assert after_ver == target_version
    assert _CHECKPOINT_ID_RE.fullmatch(body["restoredCheckpointId"])
    assert _CHECKPOINT_ID_RE.fullmatch(body["safetyCheckpointId"])
    assert type(body["restoredAt"]) is str and body["restoredAt"]
    raw = res.text
    assert "sourceKind" not in raw
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "checkpoint_restore" not in raw
    assert "esr_" not in raw
    assert "source_kind" not in raw
    return body | {"_after_ver": after_ver}


def _default_chapters(n: int = 2) -> list[dict]:
    titles = ["总体架构", "安全设计", "实施计划", "质量保证", "运维保障"]
    bodies = [
        "现有架构正文。",
        "现有安全正文。",
        "现有实施正文。",
        "现有质量正文。",
        "现有运维正文。",
    ]
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "id": f"chap_{chr(ord('a') + i)}",
                "title": titles[i],
                "body": bodies[i],
                "status": "pending",
                "preview": bodies[i],
                "wordCount": len(bodies[i].replace(" ", "")),
            }
        )
    return out


def _seed_via_browser(
    client: TestClient,
    pid: str,
    *,
    marker: str = "base",
    chapters: list[dict] | None = None,
) -> dict:
    chs = chapters or _default_chapters(2)
    return _put_state(
        client,
        pid,
        {
            "outline": [
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in chs
            ],
            "chapters": chs,
            "facts": [{"id": f"fact_{marker}", "text": f"{marker}-{_SECRET}"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": f"概述-{marker}",
                "techRequirements": [f"要求-{marker}"],
                "rejectionRisks": [],
                "scoringPoints": [f"评分-{marker}"],
            },
            "responseMatrix": [],
            "guidance": {"hints": [f"提示-{marker}"]},
            "parsedMarkdown": f"# 招标文件\n{marker}",
            "analysisOverview": f"概述-{marker}",
        },
    )


def _seed_without_revision(
    pid: str,
    *,
    marker: str = "legacy",
    chapters: list[dict] | None = None,
    workspace_id: str = _WS,
) -> dict:
    """用途：服务层无 revision 写入，保持账本为空。"""
    chs = chapters or _default_chapters(2)
    db = SessionLocal()
    try:
        return editor_state_service.upsert_editor_state(
            db,
            workspace_id,
            pid,
            outline=[
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in chs
            ],
            chapters=chs,
            facts=[{"id": f"fact_{marker}", "text": f"{marker}-{_SECRET}"}],
            mode="ALIGNED",
            analysis={
                "overview": f"概述-{marker}",
                "techRequirements": [f"要求-{marker}"],
                "rejectionRisks": [],
                "scoringPoints": [f"评分-{marker}"],
            },
            response_matrix=[],
            guidance={"hints": [f"提示-{marker}"]},
            parsed_markdown=f"# 招标文件\n{marker}",
            analysis_overview=f"概述-{marker}",
        )
    finally:
        db.close()


def _seed_business_via_browser(client: TestClient, pid: str, *, marker: str) -> dict:
    return _put_state(
        client,
        pid,
        {
            "mode": "ALIGNED",
            "businessQualify": [{"name": f"资质-{marker}"}],
            "businessToc": [{"title": f"目录-{marker}"}],
            "businessQuote": {"rows": [{"item": f"报价-{marker}", "amount": 100}]},
            "businessCommit": [{"text": f"承诺-{marker}"}],
            "analysisOverview": f"商务概述-{marker}",
        },
    )


def _extract_13(state: dict) -> dict:
    return {k: state.get(k) for k in sorted(_SNAPSHOT_KEYS)}


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


def _db_rev_count(project_id: str, workspace_id: str | None = None) -> int:
    return len(_db_rev_rows(project_id, workspace_id=workspace_id))


def _source_count(rows: list[EditorStateRevisionRow], source_kind: str) -> int:
    return sum(1 for r in rows if r.source_kind == source_kind)


def _restore_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_RESTORE)


def _revision_identity_seq(
    rows: list[EditorStateRevisionRow],
) -> list[tuple[str, str, str]]:
    return [(r.id, r.state_version, r.source_kind) for r in rows]


def _filtered_identity_seq(
    rows: list[EditorStateRevisionRow],
    sources: set[str],
) -> list[tuple[str, str, str]]:
    return [
        (r.id, r.state_version, r.source_kind)
        for r in rows
        if r.source_kind in sources
    ]


def _assert_restore_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = [
        r
        for r in rows
        if r.state_version == after_ver and r.source_kind == _SOURCE_RESTORE
    ]
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _db_cp_count(project_id: str, workspace_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(EditorStateCheckpointRow).filter(
            EditorStateCheckpointRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateCheckpointRow.workspace_id == workspace_id)
        return q.count()
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


def _db_chapter_bodies(project_id: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None or not row.chapters_json:
            return {}
        raw = json.loads(row.chapters_json)
        if not isinstance(raw, list):
            return {}
        out: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict) and type(item.get("id")) is str:
                body = item.get("body")
                out[item["id"]] = body if type(body) is str else ""
        return out
    finally:
        db.close()


def _ensure_workspace(ws_id: str, name: str = "其他空间P12CBD3") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12cbd3",
                )
            )
            db.commit()
    finally:
        db.close()


def _seed_foreign_workspace_project_with_checkpoint(
    *,
    project_id: str = "proj_other_p12cbd3",
    checkpoint_id: str = "escp_other_p12cbd3_fixedid00000001",
) -> tuple[str, str, str, dict]:
    """
    用途：真实插入外空间技术标 + 编辑态 + 检查点，供跨空间 HTTP 隔离。
    返回 (project_id, checkpoint_id, state_version, editor_state_dict)。
    """
    _ensure_workspace(_WS_OTHER)
    db = SessionLocal()
    try:
        if db.get(Project, project_id) is None:
            db.add(
                Project(
                    id=project_id,
                    workspace_id=_WS_OTHER,
                    name="外空间技术标-d3",
                    industry="通用",
                    status="draft",
                    kind="technical",
                )
            )
            db.commit()
        state = editor_state_service.upsert_editor_state(
            db,
            _WS_OTHER,
            project_id,
            outline=[{"id": "node_a", "title": "外空间章", "children": []}],
            chapters=[
                {
                    "id": "chap_a",
                    "title": "外空间章",
                    "body": f"外空间正文-{_SECRET}",
                    "status": "pending",
                    "preview": f"外空间正文-{_SECRET}",
                    "wordCount": 8,
                }
            ],
            mode="ALIGNED",
        )
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        ver = state["stateVersion"]
        existing = db.get(EditorStateCheckpointRow, checkpoint_id)
        if existing is None:
            db.add(
                EditorStateCheckpointRow(
                    id=checkpoint_id,
                    workspace_id=_WS_OTHER,
                    project_id=project_id,
                    snapshot_json=snap_json,
                    state_version=ver,
                    snapshot_bytes=len(snap_json.encode("utf-8")),
                    outline_node_count=1,
                    chapter_count=1,
                )
            )
            db.commit()
    finally:
        db.close()
    return project_id, checkpoint_id, _assert_state_version(ver), state


def _assert_fixed_error(res, status: int, code: str, *extra_leaks: str) -> None:
    assert res.status_code == status, res.text
    assert res.headers.get("Cache-Control") == "no-store", res.headers
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    for m in extra_leaks:
        if m:
            assert m not in blob


def _assert_sanitized_500(blob: str, *extra: str) -> None:
    low = blob.lower()
    assert _SECRET not in blob
    assert "traceback" not in low
    assert "sqlite" not in low
    assert "select " not in low
    assert "insert into" not in low
    assert "checkpoint_restore" not in blob
    assert "source_kind" not in low
    assert "revision_source_kind" not in low
    for forbidden in _SANITIZE_FORBIDDEN:
        assert forbidden.lower() not in low, f"500 泄漏: {forbidden!r}"
    for m in extra:
        if m:
            assert m not in blob


def _find_function_def(path: Path, name: str) -> ast.FunctionDef | None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _call_func_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _source_kind_literal_on_call(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "source_kind":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _prepare_diff_restore(
    client: TestClient,
    *,
    name: str = "差异恢复",
    kind: str = "technical",
    via_browser: bool = True,
) -> tuple[str, str, str, str, dict, dict]:
    """
    用途：构造目标检查点 A 与当前 B（版本不同）。
    返回 (pid, cid, target_ver, current_ver, target_state, current_state)。
    """
    pid = _create_project(client, name=name, kind=kind)
    if kind == "business":
        target = _seed_business_via_browser(client, pid, marker="A")
    elif via_browser:
        target = _seed_via_browser(client, pid, marker="A")
    else:
        target = _seed_without_revision(pid, marker="A")
    target_ver = _assert_state_version(target["stateVersion"])
    cp = _create_checkpoint(client, pid)
    cid = cp["checkpointId"]
    assert cp["stateVersion"] == target_ver
    if kind == "business":
        current = _seed_business_via_browser(client, pid, marker="B")
    elif via_browser:
        current = _seed_via_browser(client, pid, marker="B")
    else:
        current = _seed_without_revision(pid, marker="B")
    current_ver = _assert_state_version(current["stateVersion"])
    assert current_ver != target_ver
    return pid, cid, target_ver, current_ver, target, current


# ---------- AST 补充 ----------


def test_ast_restore_conditional_literal_source_no_get_reread():
    """
    用途：AST 补充 checkpoint 编排以字面量 checkpoint_restore 调共享原语，
      且共享原语唯一调用 recorder；无 get_editor_state 重读；不能替代 HTTP 证据。
    二次开发：C2 抽取 stage_locked_canonical_restore 后，不得再要求 restore 函数
      内直接 recorder；D3 行为断言保持不变。
    """
    restore_fn = _find_function_def(_SERVICE_PATH, "restore_editor_state_checkpoint")
    stage_fn = _find_function_def(_SERVICE_PATH, "stage_locked_canonical_restore")
    assert restore_fn is not None
    assert stage_fn is not None, "共享恢复原语 stage_locked_canonical_restore 必须存在"

    # 编排函数：禁止直接 recorder；必须唯一调用共享原语且字面量 checkpoint_restore
    restore_records = [
        n
        for n in ast.walk(restore_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    assert restore_records == [], "checkpoint restore 编排禁止直接 recorder"

    stage_calls = [
        n
        for n in ast.walk(restore_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "stage_locked_canonical_restore"
    ]
    assert len(stage_calls) == 1, (
        f"restore 应唯一调用 stage_locked_canonical_restore，实际 {len(stage_calls)}"
    )
    assert _source_kind_literal_on_call(stage_calls[0]) == _SOURCE_RESTORE

    restore_gets = [
        n
        for n in ast.walk(restore_fn)
        if isinstance(n, ast.Call) and _call_func_name(n) == "get_editor_state"
    ]
    assert restore_gets == [], "restore 禁止 get_editor_state 重读"

    # 共享原语：唯一 recorder；禁止 get_editor_state
    stage_records = [
        n
        for n in ast.walk(stage_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    assert len(stage_records) == 1, (
        f"共享原语应唯一一次 record，实际 {len(stage_records)}"
    )
    stage_gets = [
        n
        for n in ast.walk(stage_fn)
        if isinstance(n, ast.Call) and _call_func_name(n) == "get_editor_state"
    ]
    assert stage_gets == [], "共享原语禁止 get_editor_state 重读"


# ---------- 成功路径 ----------


def test_legacy_empty_ledger_diff_restore_before_and_after(client: TestClient):
    """用途：遗留空账本不同版本恢复 → before+after 两条 checkpoint_restore。"""
    pid, cid, target_ver, current_ver, target, current = _prepare_diff_restore(
        client, name="遗留空账本差异", via_browser=False
    )
    assert _db_rev_count(pid) == 0
    pre_13 = _extract_13(current)
    cp_before = _db_cp_count(pid)

    res = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    after_ver = body["_after_ver"]
    assert after_ver == target_ver
    assert after_ver != current_ver

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(state)
    for key in _SNAPSHOT_KEYS:
        assert state.get(key) == target.get(key), key
    assert state["updatedAt"] == body["restoredAt"]

    # 安全检查点 = 恢复前 13 键
    safety = client.get(_cp_url(pid, body["safetyCheckpointId"]))
    assert safety.status_code == 200, safety.text
    assert safety.json()["stateVersion"] == current_ver
    assert safety.json()["snapshot"] == pre_13
    assert _db_cp_count(pid) == cp_before + 1

    rows = _db_rev_rows(pid)
    assert len(rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_RESTORE}
    after_row = _assert_restore_after(rows, after_ver)
    versions = {r.state_version for r in rows}
    assert versions == {current_ver, target_ver}
    before_rows = [r for r in rows if r.state_version == current_ver]
    assert len(before_rows) == 1
    assert before_rows[0].source_kind == _SOURCE_RESTORE
    # after 快照含目标 marker
    assert "A-" in (after_row.snapshot_json or "") or "概述-A" in (
        after_row.snapshot_json or ""
    )


def test_browser_put_baseline_diff_restore_exact_plus_one(client: TestClient):
    """用途：browser_put 连续基线不同版本恢复只精确 +1 after；旧行身份保留。"""
    other = _create_project(client, name="其他项目隔离-d3")
    other_seed = _seed_via_browser(client, other, marker="other")
    other_v = other_seed["stateVersion"]
    other_n0 = _db_rev_count(other)
    other_restore0 = _restore_count(_db_rev_rows(other))

    pid, cid, target_ver, current_ver, target, _current = _prepare_diff_restore(
        client, name="browser基线差异", via_browser=True
    )
    rows_before = _db_rev_rows(pid)
    n_before = len(rows_before)
    ledger_before = _revision_identity_seq(rows_before)
    browser_before = _filtered_identity_seq(rows_before, {_SOURCE_BROWSER})
    restore0 = _restore_count(rows_before)
    assert restore0 == 0
    assert n_before != 0
    assert len(browser_before) == n_before
    cp_before = _db_cp_count(pid)

    res = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    after_ver = body["_after_ver"]

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver == target_ver
    for key in _SNAPSHOT_KEYS:
        assert state.get(key) == target.get(key), key

    rows = _db_rev_rows(pid)
    assert len(rows) == n_before + 1, (
        f"期望 n_before+1={n_before + 1}，实际 {len(rows)}: "
        f"{[(r.state_version, r.source_kind) for r in rows]}"
    )
    assert _restore_count(rows) == 1
    _assert_restore_after(rows, after_ver)
    assert _filtered_identity_seq(rows, {_SOURCE_BROWSER}) == browser_before
    ledger_after = _revision_identity_seq(rows)
    assert len(ledger_after) == len(ledger_before) + 1
    for ident in ledger_before:
        assert ident in ledger_after
    restore_idents = [
        (rid, ver, src) for rid, ver, src in ledger_after if src == _SOURCE_RESTORE
    ]
    assert len(restore_idents) == 1
    assert restore_idents[0][1] == after_ver
    assert _db_cp_count(pid) == cp_before + 1

    # 其他项目零增量
    assert _db_rev_count(other) == other_n0
    assert _restore_count(_db_rev_rows(other)) == other_restore0 == 0
    assert _get_state(client, other)["stateVersion"] == other_v


def test_business_diff_restore_exact_plus_one_semantics(client: TestClient):
    """用途：商务标不同版本恢复只 +1 checkpoint after，13 键语义不变。"""
    pid, cid, target_ver, current_ver, target, _cur = _prepare_diff_restore(
        client, name="商务差异恢复", kind="business", via_browser=True
    )
    n_before = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    assert restore0 == 0

    res = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    state = _get_state(client, pid)
    assert state["stateVersion"] == target_ver
    assert state["businessQualify"] == target["businessQualify"]
    assert state["businessToc"] == target["businessToc"]
    assert state["businessQuote"] == target["businessQuote"]
    assert state["businessCommit"] == target["businessCommit"]
    assert state["analysisOverview"] == target["analysisOverview"]
    rows = _db_rev_rows(pid)
    assert len(rows) == n_before + 1
    assert _restore_count(rows) == 1
    _assert_restore_after(rows, body["_after_ver"])


def test_legacy_empty_ledger_same_content_zero_revision(client: TestClient):
    """
    用途：遗留空账本同内容恢复：安全检查点 +1，13 键/版本/revision 身份序列全等。
    failure-first 上本用例应通过（旧生产本就不记修订）。
    契约：写回必更新 updatedAt，最终 updatedAt != 恢复前 updatedAt。
    """
    pid = _create_project(client, name="空账本同内容")
    seed = _seed_without_revision(pid, marker="same")
    v0 = _assert_state_version(seed["stateVersion"])
    cp = _create_checkpoint(client, pid)
    cid = cp["checkpointId"]
    assert cp["stateVersion"] == v0
    assert _db_rev_count(pid) == 0
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    assert ledger0 == []
    # 恢复前冻结完整 GET editor-state（含 updatedAt）
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == v0
    pre_13 = _extract_13(state0)
    pre_updated_at = state0["updatedAt"]
    assert type(pre_updated_at) is str and pre_updated_at
    cp_before = _db_cp_count(pid)

    res = _restore(client, pid, cid, v0)
    body = _assert_success_restore(res, target_version=v0)
    assert body["stateVersion"] == v0
    # 安全检查点 +1
    assert _db_cp_count(pid) == cp_before + 1
    safety = client.get(_cp_url(pid, body["safetyCheckpointId"]))
    assert safety.status_code == 200, safety.text
    assert safety.json()["stateVersion"] == v0
    assert safety.json()["snapshot"] == pre_13

    # 规范 13 键与版本不变；写回必更新 updatedAt 且对齐 restoredAt
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert _extract_13(state) == pre_13
    assert state["updatedAt"] == body["restoredAt"]
    assert state["updatedAt"] != pre_updated_at
    # revision 身份序列精确全等（仍为空）
    assert _db_rev_count(pid) == 0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0 == []
    assert _restore_count(_db_rev_rows(pid)) == 0


def test_browser_baseline_same_content_identity_unchanged(client: TestClient):
    """用途：已有 browser_put 基线同内容恢复：安全检查点 +1，revision 身份序列全等。

    契约：13 键/版本/revision 身份不变；写回必更新 updatedAt 且 restoredAt==最终 updatedAt。
    """
    pid = _create_project(client, name="browser同内容")
    seed = _seed_via_browser(client, pid, marker="same-b")
    v0 = _assert_state_version(seed["stateVersion"])
    cp = _create_checkpoint(client, pid)
    cid = cp["checkpointId"]
    rows_before = _db_rev_rows(pid)
    ledger_before = _revision_identity_seq(rows_before)
    n_before = len(rows_before)
    assert n_before != 0
    assert _restore_count(rows_before) == 0
    # 恢复前冻结完整 GET editor-state（含 updatedAt）
    state0 = _get_state(client, pid)
    state0_13 = _extract_13(state0)
    pre_updated_at = state0["updatedAt"]
    assert type(pre_updated_at) is str and pre_updated_at
    cp_before = _db_cp_count(pid)

    res = _restore(client, pid, cid, v0)
    body = _assert_success_restore(res, target_version=v0)
    assert _db_cp_count(pid) == cp_before + 1
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert _extract_13(state) == state0_13
    assert state["updatedAt"] == body["restoredAt"]
    assert state["updatedAt"] != pre_updated_at
    rows_after = _db_rev_rows(pid)
    assert len(rows_after) == n_before
    assert _revision_identity_seq(rows_after) == ledger_before
    assert _restore_count(rows_after) == 0


def test_revisit_historical_version_forms_new_after_timepoint(client: TestClient):
    """用途：回到已出现过的旧版本时形成新的 after 时间点，禁止按版本集合去重。"""
    pid = _create_project(client, name="回退旧版本新时间点")
    state_a = _seed_via_browser(client, pid, marker="hist-A")
    ver_a = _assert_state_version(state_a["stateVersion"])
    cp_a = _create_checkpoint(client, pid)
    cid = cp_a["checkpointId"]

    state_b = _seed_via_browser(client, pid, marker="hist-B")
    ver_b = _assert_state_version(state_b["stateVersion"])
    assert ver_b != ver_a

    res1 = _restore(client, pid, cid, ver_b)
    body1 = _assert_success_restore(res1, target_version=ver_a)
    rows1 = _db_rev_rows(pid)
    restore_rows_1 = [r for r in rows1 if r.source_kind == _SOURCE_RESTORE]
    assert len(restore_rows_1) == 1
    first_after_id = restore_rows_1[0].id
    assert restore_rows_1[0].state_version == ver_a

    # 前进到 C 再恢复 A
    state_c = _seed_via_browser(client, pid, marker="hist-C")
    ver_c = _assert_state_version(state_c["stateVersion"])
    assert ver_c != ver_a
    n_before_2 = _db_rev_count(pid)
    restore_n_before = _restore_count(_db_rev_rows(pid))

    res2 = _restore(client, pid, cid, ver_c)
    body2 = _assert_success_restore(res2, target_version=ver_a)
    assert body2["_after_ver"] == ver_a
    rows2 = _db_rev_rows(pid)
    assert len(rows2) == n_before_2 + 1
    restore_rows_2 = [r for r in rows2 if r.source_kind == _SOURCE_RESTORE]
    assert len(restore_rows_2) == restore_n_before + 1 == 2
    # 两次 after 都是 ver_a，但行 ID 不同 → 新时间点
    after_ids = {r.id for r in restore_rows_2 if r.state_version == ver_a}
    assert len(after_ids) == 2
    assert first_after_id in after_ids
    assert first_after_id != body1["safetyCheckpointId"]


def test_response_get_after_version_consistent(client: TestClient):
    """用途：响应 stateVersion 与 GET / after 行 / 目标检查点精确一致。"""
    pid, cid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="版本一致-d3", via_browser=True
    )
    res = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    after_ver = body["_after_ver"]
    got = _get_state(client, pid)
    assert got["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(got)
    assert got["updatedAt"] == body["restoredAt"]
    rows = _db_rev_rows(pid)
    after_row = _assert_restore_after(rows, after_ver)
    assert after_row.state_version == after_ver
    snap = json.loads(after_row.snapshot_json)
    assert isinstance(snap, dict)
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    detail = client.get(_cp_url(pid, cid))
    assert detail.status_code == 200
    assert detail.json()["stateVersion"] == after_ver


# ---------- 失败零增量 ----------


def test_conflict_404_422_cross_scope_zero_restore_revision(client: TestClient):
    """用途：缺/坏 expected、陈旧、404、跨项目均零写完整 editor-state 与 revision 身份。"""
    pid, cid, target_ver, current_ver, _t, current = _prepare_diff_restore(
        client, name="零增量矩阵-d3", via_browser=True
    )
    other = _create_project(client, name="跨项目-d3")
    other_seed = _seed_via_browser(client, other, marker="other-z")
    other_ledger0 = _revision_identity_seq(_db_rev_rows(other))
    other_state0 = _get_state(client, other)
    other_n0 = _db_rev_count(other)
    other_cp0 = _db_cp_count(other)
    other_restore0 = _restore_count(_db_rev_rows(other))

    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)
    cp0 = _db_cp_count(pid)
    # 主项目冻结完整 revision 身份序列与完整 GET state
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert restore0 == 0
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0

    def _assert_zero(label: str) -> None:
        assert _db_rev_count(pid) == n0, label
        assert _restore_count(_db_rev_rows(pid)) == 0, label
        assert _db_chapter_bodies(pid) == bodies0, label
        assert _db_cp_count(pid) == cp0, label
        # 完整 revision 身份序列与完整 GET state 精确全等（含 updatedAt）
        assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0, label
        assert _get_state(client, pid) == state0, label

    def _assert_other_untouched(label: str) -> None:
        assert _db_rev_count(other) == other_n0, label
        assert _revision_identity_seq(_db_rev_rows(other)) == other_ledger0, label
        assert _get_state(client, other) == other_state0, label
        assert _restore_count(_db_rev_rows(other)) == other_restore0 == 0, label
        assert _db_cp_count(other) == other_cp0, label

    # 422：缺 expected
    missing = client.post(_restore_url(pid, cid), json={})
    assert missing.status_code == 422, missing.text
    _assert_zero("缺 expected")

    # 422：坏格式
    bad = client.post(
        _restore_url(pid, cid),
        json={"expectedStateVersion": "not_a_version"},
    )
    assert bad.status_code == 422, bad.text
    _assert_zero("坏 expected")

    # 409：陈旧 CAS（用 target_ver 作为陈旧 expected）
    stale = _restore(client, pid, cid, target_ver)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail.get("code") == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    assert detail.get("currentStateVersion") == current_ver
    _assert_zero("陈旧 409")
    _assert_other_untouched("陈旧-other")

    # 404：检查点不存在
    miss = client.post(
        _restore_url(pid, "escp_missing_d3_should_not_echo0001"),
        json={"expectedStateVersion": current_ver},
    )
    _assert_fixed_error(
        miss,
        404,
        "editor_state_checkpoint_not_found",
        "escp_missing_d3_should_not_echo0001",
    )
    _assert_zero("缺检查点")

    # 404：跨项目检查点
    cross = client.post(
        _restore_url(other, cid),
        json={"expectedStateVersion": other_state0["stateVersion"]},
    )
    _assert_fixed_error(cross, 404, "editor_state_checkpoint_not_found", cid)
    _assert_zero("跨项目")
    _assert_other_untouched("跨项目-other")

    # 404：项目不存在
    miss_proj = client.post(
        _restore_url("proj_does_not_exist_p12cbd3", cid),
        json={"expectedStateVersion": current_ver},
    )
    assert miss_proj.status_code == 404, miss_proj.text
    _assert_zero("缺项目")
    _assert_other_untouched("缺项目-other")


def test_cross_workspace_restore_404_zero_side_effects(client: TestClient):
    """用途：真实跨空间 HTTP 隔离，禁止跨项目冒充跨空间。"""
    pid_local, cid_local, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="本空间跨空间对照", via_browser=True
    )
    local_ledger0 = _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
    local_state0 = _get_state(client, pid_local)
    local_n0 = _db_rev_count(pid_local, workspace_id=_WS)
    local_cp0 = _db_cp_count(pid_local, workspace_id=_WS)
    local_bodies0 = _db_chapter_bodies(pid_local)

    pid_other, cid_other, ver_other, other_state0 = (
        _seed_foreign_workspace_project_with_checkpoint()
    )
    other_ledger0 = _revision_identity_seq(
        _db_rev_rows(pid_other, workspace_id=_WS_OTHER)
    )
    other_n0 = _db_rev_count(pid_other, workspace_id=_WS_OTHER)
    other_cp0 = _db_cp_count(pid_other, workspace_id=_WS_OTHER)
    other_bodies0 = _db_chapter_bodies(pid_other)
    assert ver_other == other_state0["stateVersion"]
    assert other_cp0 == 1

    res = client.post(
        _restore_url(pid_other, cid_other),
        json={"expectedStateVersion": ver_other},
        headers={"X-Workspace-Id": _WS},
    )
    _assert_fixed_error(
        res,
        404,
        "project_not_found",
        pid_other,
        cid_other,
        _WS_OTHER,
        ver_other,
        _SECRET,
    )
    assert pid_other not in res.text
    assert cid_other not in res.text

    assert _db_rev_count(pid_local, workspace_id=_WS) == local_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
        == local_ledger0
    )
    assert _get_state(client, pid_local) == local_state0
    assert _db_cp_count(pid_local, workspace_id=_WS) == local_cp0
    assert _db_chapter_bodies(pid_local) == local_bodies0

    assert _db_rev_count(pid_other, workspace_id=_WS_OTHER) == other_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_other, workspace_id=_WS_OTHER))
        == other_ledger0
    )
    assert _db_cp_count(pid_other, workspace_id=_WS_OTHER) == other_cp0 == 1
    assert _db_chapter_bodies(pid_other) == other_bodies0
    db = SessionLocal()
    try:
        other_state_after = editor_state_service.get_editor_state(
            db, _WS_OTHER, pid_other
        )
    finally:
        db.close()
    assert other_state_after == other_state0


def test_corrupt_oversize_drift_zero_three_domains(
    client: TestClient, monkeypatch
):
    """用途：损坏目标 / 安全超限 / 写回语义漂移 → 三域零写（完整 GET state 全等）。"""
    pid, cid, target_ver, current_ver, _t, current = _prepare_diff_restore(
        client, name="损坏超限漂移", via_browser=True
    )
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    assert restore0 == 0

    # ---- 损坏目标：污染 snapshot_json 后 500 corrupt ----
    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, cid)
        assert row is not None
        row.snapshot_json = '{"broken": true, "secret": "' + _SECRET + '"}'
        db.commit()
    finally:
        db.close()

    # 请求前冻结对应项目完整 GET state 与 revision 身份序列
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0

    res_corrupt = _restore(client, pid, cid, current_ver)
    _assert_fixed_error(
        res_corrupt, 500, "editor_state_checkpoint_corrupt", cid, current_ver, _SECRET
    )
    assert _db_rev_count(pid) == n0
    assert _restore_count(_db_rev_rows(pid)) == 0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 重建干净目标检查点供后续
    pid2, cid2, target_ver2, current_ver2, _t2, _c2 = _prepare_diff_restore(
        client, name="超限漂移2", via_browser=True
    )
    n2 = _db_rev_count(pid2)
    cp2 = _db_cp_count(pid2)
    bodies2 = _db_chapter_bodies(pid2)
    # 超限请求前冻结 pid2 完整 GET state 与 revision 身份
    ledger2 = _revision_identity_seq(_db_rev_rows(pid2))
    state2 = _get_state(client, pid2)
    assert state2["stateVersion"] == current_ver2
    assert len(ledger2) == n2

    # ---- 安全快照超限 413 ----
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
        editor_state_checkpoint_service.editor_state_service,
        "lock_and_assert_expected_state_version",
        _huge_current,
    )
    res_413 = _restore(client, pid2, cid2, current_ver2)
    _assert_fixed_error(res_413, 413, "editor_state_checkpoint_too_large")
    assert _db_rev_count(pid2) == n2
    assert _restore_count(_db_rev_rows(pid2)) == 0
    assert _db_cp_count(pid2) == cp2
    assert _db_chapter_bodies(pid2) == bodies2
    assert _revision_identity_seq(_db_rev_rows(pid2)) == ledger2
    assert _get_state(client, pid2) == state2

    # 恢复 lock
    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    monkeypatch.setattr(
        editor_state_checkpoint_service.editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )

    # ---- 写回后版本漂移 500 corrupt ----
    # 漂移请求前再次冻结（lock 已恢复；状态应仍等于 state2）
    ledger2_drift = _revision_identity_seq(_db_rev_rows(pid2))
    state2_drift = _get_state(client, pid2)
    assert ledger2_drift == ledger2
    assert state2_drift == state2

    real_apply = editor_state_service.apply_canonical_snapshot_to_locked_row

    def _drift(db, project_id, row, snapshot):
        out = real_apply(db, project_id, row, snapshot)
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
        editor_state_checkpoint_service.editor_state_service,
        "apply_canonical_snapshot_to_locked_row",
        _drift,
    )
    res_drift = _restore(client, pid2, cid2, current_ver2)
    _assert_fixed_error(
        res_drift, 500, "editor_state_checkpoint_corrupt", _SECRET, cid2
    )
    assert _db_rev_count(pid2) == n2
    assert _restore_count(_db_rev_rows(pid2)) == 0
    assert _db_cp_count(pid2) == cp2
    assert _db_chapter_bodies(pid2) == bodies2
    assert _revision_identity_seq(_db_rev_rows(pid2)) == ledger2_drift
    assert _get_state(client, pid2) == state2_drift


# ---------- 真实双并发 ----------


def test_concurrent_diff_version_one_success_one_409_single_transition(
    client: TestClient,
):
    """
    用途：不同版本真实双并发精确一个 (200,None) 与一个
      (409,editor_state_version_conflict)；一份安全检查点、一次 transition。
    """
    pid, cid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="双并发差异", via_browser=True
    )
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    cp0 = _db_cp_count(pid)
    assert restore0 == 0
    barrier = threading.Barrier(2)
    conflict_code = editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT

    def worker() -> tuple[int, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                editor_state_checkpoint_service.restore_editor_state_checkpoint(
                    db,
                    _WS,
                    pid,
                    cid,
                    current_ver,
                )
                return (200, None)
            except editor_state_service.EditorStateVersionConflict:
                db.rollback()
                return (409, conflict_code)
            except editor_state_checkpoint_service.EditorStateCheckpointError as exc:
                db.rollback()
                return (exc.status_code, exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    assert sorted(outcomes, key=lambda x: (x[0], x[1] or "")) == sorted(
        [(200, None), (409, conflict_code)],
        key=lambda x: (x[0], x[1] or ""),
    ), outcomes
    assert outcomes.count((200, None)) == 1
    assert outcomes.count((409, conflict_code)) == 1
    assert all(
        o == (200, None) or o == (409, conflict_code) for o in outcomes
    ), outcomes

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _restore_count(rows) == 1
    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver == target_ver
    _assert_restore_after(rows, after_ver)
    assert _db_cp_count(pid) == cp0 + 1


def test_concurrent_same_content_not_forced_single_winner(client: TestClient):
    """
    用途：同内容恢复不强制单胜契约；允许双 200 或一胜一冲突，
      但不得伪造 checkpoint_restore 修订。
    """
    pid = _create_project(client, name="双并发同内容")
    seed = _seed_via_browser(client, pid, marker="same-conc")
    v0 = _assert_state_version(seed["stateVersion"])
    cid = _create_checkpoint(client, pid)["checkpointId"]
    n0 = _db_rev_count(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    restore0 = _restore_count(_db_rev_rows(pid))
    assert restore0 == 0
    barrier = threading.Barrier(2)
    conflict_code = editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT

    def worker() -> tuple[int, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                editor_state_checkpoint_service.restore_editor_state_checkpoint(
                    db,
                    _WS,
                    pid,
                    cid,
                    v0,
                )
                return (200, None)
            except editor_state_service.EditorStateVersionConflict:
                db.rollback()
                return (409, conflict_code)
            except editor_state_checkpoint_service.EditorStateCheckpointError as exc:
                db.rollback()
                return (exc.status_code, exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    # 不强制单胜：仅要求每条结果是 200 或精确 version conflict
    assert all(
        o == (200, None) or o == (409, conflict_code) for o in outcomes
    ), outcomes
    assert any(o == (200, None) for o in outcomes), outcomes
    # 同内容零 restore 修订；既有 revision 身份序列精确全等
    rows = _db_rev_rows(pid)
    assert _restore_count(rows) == 0
    assert len(rows) == n0
    assert _revision_identity_seq(rows) == ledger0
    assert _get_state(client, pid)["stateVersion"] == v0


# ---------- 失败原子性 ----------


def test_recorder_flush_then_fail_full_rollback_and_retryable(
    client: TestClient, monkeypatch
):
    """用途：recorder 真实 flush 后注入失败 → 三域全回滚且可重试。"""
    pid, cid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="recorder注入-d3", via_browser=True
    )
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    assert restore0 == 0
    # 失败前冻结完整 GET state 与 revision 身份序列
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        assert out["added_count"] == 1
        assert kwargs.get("source_kind") == _SOURCE_RESTORE
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )
    if hasattr(editor_state_checkpoint_service, "editor_state_revision_service"):
        monkeypatch.setattr(
            editor_state_checkpoint_service.editor_state_revision_service,
            "record_editor_state_transition",
            _record_then_boom,
        )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, cid),
            json={"expectedStateVersion": current_ver},
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_AFTER_FLUSH,
        pid,
        current_ver,
        cid,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _restore_count(_db_rev_rows(pid)) == restore0 == 0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 去掉注入后可重试
    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        real_record,
    )
    if hasattr(editor_state_checkpoint_service, "editor_state_revision_service"):
        monkeypatch.setattr(
            editor_state_checkpoint_service.editor_state_revision_service,
            "record_editor_state_transition",
            real_record,
        )
    retry = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(retry, target_version=target_ver)
    assert _restore_count(_db_rev_rows(pid)) == 1
    _assert_restore_after(_db_rev_rows(pid), body["_after_ver"])
    assert _db_cp_count(pid) == cp0 + 1


def test_revision_trim_failure_full_rollback(client: TestClient, monkeypatch):
    """用途：revision 裁剪失败 → 三域全回滚；恢复真实 trim 后原目标可重试成功。"""
    pid, cid, target_ver, current_ver, target, _c = _prepare_diff_restore(
        client, name="revision裁剪失败", via_browser=True
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    assert restore0 == 0
    # 失败前冻结完整 GET state 与 revision 身份序列
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0
    target_13 = _extract_13(target)

    real_trim = editor_state_revision_service._trim_revisions
    calls = {"n": 0}

    def _trim_then_boom(*args, **kwargs):
        calls["n"] += 1
        real_trim(*args, **kwargs)
        raise RuntimeError(_INJECT_REV_TRIM)

    monkeypatch.setattr(
        editor_state_revision_service,
        "_trim_revisions",
        _trim_then_boom,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, cid),
            json={"expectedStateVersion": current_ver},
        )

    # 生产已接入 recorder：trim 必被调用且失败回滚
    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text, _INJECT_REV_TRIM, pid, current_ver, cid, "RuntimeError"
    )
    assert _db_rev_count(pid) == n0
    assert _restore_count(_db_rev_rows(pid)) == 0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 重试前：原目标 checkpoint 仍存在且版本=target
    cp_detail = client.get(_cp_url(pid, cid))
    assert cp_detail.status_code == 200, cp_detail.text
    assert cp_detail.json()["stateVersion"] == target_ver
    assert _get_state(client, pid)["stateVersion"] == current_ver

    # 恢复真实 _trim_revisions，同一 checkpoint/current expected 重试
    monkeypatch.setattr(
        editor_state_revision_service,
        "_trim_revisions",
        real_trim,
    )
    retry = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(retry, target_version=target_ver)
    assert body["stateVersion"] == target_ver
    assert _restore_count(_db_rev_rows(pid)) == restore0 + 1 == 1
    _assert_restore_after(_db_rev_rows(pid), body["_after_ver"])
    assert _db_cp_count(pid) == cp0 + 1
    assert _CHECKPOINT_ID_RE.fullmatch(body["safetyCheckpointId"])
    final = _get_state(client, pid)
    assert final["stateVersion"] == target_ver
    assert _extract_13(final) == target_13


def test_checkpoint_trim_failure_full_rollback(client: TestClient, monkeypatch):
    """用途：后续 checkpoint 裁剪失败 → 三域全回滚；恢复真实 trim 后原目标可重试成功。"""
    pid, cid, target_ver, current_ver, target, _c = _prepare_diff_restore(
        client, name="checkpoint裁剪失败", via_browser=True
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    assert restore0 == 0
    # 失败前冻结完整 GET state 与 revision 身份序列
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0
    target_13 = _extract_13(target)

    real_trim = editor_state_checkpoint_service._trim_checkpoints
    calls = {"n": 0}

    def _trim_then_boom(*args, **kwargs):
        calls["n"] += 1
        real_trim(*args, **kwargs)
        raise RuntimeError(_INJECT_CP_TRIM)

    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_trim_checkpoints",
        _trim_then_boom,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, cid),
            json={"expectedStateVersion": current_ver},
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text, _INJECT_CP_TRIM, pid, current_ver, cid, "RuntimeError"
    )
    assert _db_rev_count(pid) == n0
    assert _restore_count(_db_rev_rows(pid)) == 0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 重试前：原目标 checkpoint 仍存在且版本=target
    cp_detail = client.get(_cp_url(pid, cid))
    assert cp_detail.status_code == 200, cp_detail.text
    assert cp_detail.json()["stateVersion"] == target_ver
    assert _get_state(client, pid)["stateVersion"] == current_ver

    # 恢复真实 _trim_checkpoints，同一 checkpoint/current expected 重试
    monkeypatch.setattr(
        editor_state_checkpoint_service,
        "_trim_checkpoints",
        real_trim,
    )
    retry = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(retry, target_version=target_ver)
    assert body["stateVersion"] == target_ver
    assert _restore_count(_db_rev_rows(pid)) == restore0 + 1 == 1
    _assert_restore_after(_db_rev_rows(pid), body["_after_ver"])
    assert _db_cp_count(pid) == cp0 + 1
    assert _CHECKPOINT_ID_RE.fullmatch(body["safetyCheckpointId"])
    final = _get_state(client, pid)
    assert final["stateVersion"] == target_ver
    assert _extract_13(final) == target_13


def test_commit_failure_pending_three_domains_then_full_rollback(
    client: TestClient, monkeypatch
):
    """
    用途：commit 前同 Session 观测 after revision、安全检查点、写回状态均 pending；
      commit 失败后三域全回滚且可重试。
    """
    pid, cid, target_ver, current_ver, target, _c = _prepare_diff_restore(
        client, name="commit失败-d3", via_browser=True
    )
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    # 失败前冻结完整 GET state 与 revision 身份序列
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    facts0 = state0.get("facts") or []
    assert state0["stateVersion"] == current_ver
    assert len(ledger0) == n0

    commit_probe: dict = {
        "n": 0,
        "pending": None,
        "restore_pending": None,
        "source": None,
        "after_ver": None,
        "cp_pending": None,
        "safety_version": None,
        "facts_restored": None,
        "state_version": None,
    }

    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm_commit(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit

        def _bad_commit(*a, **k):
            commit_probe["n"] += 1
            commit_probe["pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            restore_rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_RESTORE,
                )
                .all()
            )
            commit_probe["restore_pending"] = len(restore_rows)
            if restore_rows:
                commit_probe["source"] = restore_rows[-1].source_kind
                commit_probe["after_ver"] = restore_rows[-1].state_version
            commit_probe["cp_pending"] = (
                db.query(EditorStateCheckpointRow)
                .filter(EditorStateCheckpointRow.project_id == pid)
                .count()
            )
            # 最新检查点应为安全检查点，版本=恢复前 current
            latest_cp = (
                db.query(EditorStateCheckpointRow)
                .filter(EditorStateCheckpointRow.project_id == pid)
                .order_by(
                    EditorStateCheckpointRow.created_at.desc(),
                    EditorStateCheckpointRow.id.desc(),
                )
                .first()
            )
            if latest_cp is not None:
                commit_probe["safety_version"] = latest_cp.state_version
            state_row = db.get(ProjectEditorStateRow, pid)
            assert state_row is not None
            # 写回后版本应已是目标；facts 应回到目标 marker A
            rebuilt = editor_state_service._state_from_row(pid, state_row)
            commit_probe["state_version"] = rebuilt["stateVersion"]
            facts = rebuilt.get("facts") or []
            commit_probe["facts_restored"] = any(
                isinstance(f, dict) and f.get("id") == "fact_A" for f in facts
            )
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        db.commit = _bad_commit  # type: ignore[method-assign]
        return out

    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm_commit,
    )
    monkeypatch.setattr(
        editor_state_checkpoint_service.editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm_commit,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, cid),
            json={"expectedStateVersion": current_ver},
        )

    assert commit_probe["n"] == 1
    # failure-first：未接入时 restore_pending 为 0，强制断言失败
    assert commit_probe["restore_pending"] == 1, (
        f"commit 前 checkpoint_restore 应精确为 1，实际 {commit_probe['restore_pending']}"
    )
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert commit_probe["source"] == _SOURCE_RESTORE
    assert _assert_state_version(commit_probe["after_ver"]) == target_ver
    assert commit_probe["cp_pending"] == cp0 + 1
    assert commit_probe["safety_version"] == current_ver
    assert commit_probe["facts_restored"] is True
    assert commit_probe["state_version"] == target_ver

    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_COMMIT_FAIL,
        pid,
        current_ver,
        cid,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _restore_count(_db_rev_rows(pid)) == restore0 == 0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0
    assert (_get_state(client, pid).get("facts") or []) == facts0

    # 去掉注入后可重试
    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    monkeypatch.setattr(
        editor_state_checkpoint_service.editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    retry = _restore(client, pid, cid, current_ver)
    body = _assert_success_restore(retry, target_version=target_ver)
    assert _restore_count(_db_rev_rows(pid)) == 1
    _assert_restore_after(_db_rev_rows(pid), body["_after_ver"])
    assert _db_cp_count(pid) == cp0 + 1
    for key in ("chapters", "facts", "outline"):
        assert _get_state(client, pid).get(key) == target.get(key)


def test_source_isolation_no_apply_consume_control(client: TestClient):
    """用途：D3 restore 只允许 checkpoint_restore；请求体不能控制来源。

    反假绿：不筛 restore_rows 后再否定 apply/consume（同义反复）；
    必须在伪造请求前冻结完整身份/分来源计数，成功后精确 +1 且旧行全保留。
    """
    pid, cid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="来源隔离", via_browser=True
    )
    # 1) 伪造请求前冻结完整修订身份、browser_put 身份、各来源精确计数与总行数
    rows_before = _db_rev_rows(pid)
    n_before = len(rows_before)
    ledger_before = _revision_identity_seq(rows_before)
    browser_before = _filtered_identity_seq(rows_before, {_SOURCE_BROWSER})
    apply_before = _source_count(rows_before, _SOURCE_APPLY)
    consume_before = _source_count(rows_before, _SOURCE_CONSUME)
    restore_before = _source_count(rows_before, _SOURCE_RESTORE)
    browser_count_before = _source_count(rows_before, _SOURCE_BROWSER)
    assert restore_before == 0
    assert apply_before == 0
    assert consume_before == 0
    assert n_before == browser_count_before
    assert len(browser_before) == browser_count_before

    # 请求体夹带伪造来源字段 → 422 或忽略且仍只写 checkpoint_restore
    forged = client.post(
        _restore_url(pid, cid),
        json={
            "expectedStateVersion": current_ver,
            "sourceKind": "content_fuse_apply",
            "revisionSourceKind": "browser_put",
        },
    )
    # 2) 422：失败后完整身份序列精确不变，再走合法 restore；
    #    成功忽略额外字段：不得重复请求
    if forged.status_code == 422:
        rows_after_fail = _db_rev_rows(pid)
        assert len(rows_after_fail) == n_before
        assert _revision_identity_seq(rows_after_fail) == ledger_before
        assert _filtered_identity_seq(rows_after_fail, {_SOURCE_BROWSER}) == browser_before
        assert _source_count(rows_after_fail, _SOURCE_APPLY) == apply_before
        assert _source_count(rows_after_fail, _SOURCE_CONSUME) == consume_before
        assert _source_count(rows_after_fail, _SOURCE_RESTORE) == restore_before
        res = _restore(client, pid, cid, current_ver)
    else:
        res = forged
    body = _assert_success_restore(res, target_version=target_ver)
    after_ver = body["_after_ver"]
    assert after_ver == target_ver

    # 3) 成功后总行数精确 +1；旧完整身份逐行保留；browser_put 身份序列精确不变
    rows = _db_rev_rows(pid)
    assert len(rows) == n_before + 1
    ledger_after = _revision_identity_seq(rows)
    assert len(ledger_after) == n_before + 1
    for ident in ledger_before:
        assert ident in ledger_after
    assert set(ledger_before).issubset(set(ledger_after))
    assert _filtered_identity_seq(rows, {_SOURCE_BROWSER}) == browser_before

    # 4) 各来源计数：apply/consume 精确不变；restore 精确 +1；新增唯一行 source/version 精确
    assert _source_count(rows, _SOURCE_APPLY) == apply_before
    assert _source_count(rows, _SOURCE_CONSUME) == consume_before
    assert _source_count(rows, _SOURCE_RESTORE) == restore_before + 1
    assert _source_count(rows, _SOURCE_BROWSER) == browser_count_before
    new_idents = [ident for ident in ledger_after if ident not in set(ledger_before)]
    assert len(new_idents) == 1
    new_id, new_ver, new_src = new_idents[0]
    assert _REVISION_ID_RE.fullmatch(new_id)
    assert new_ver == target_ver == after_ver
    assert new_src == _SOURCE_RESTORE
    _assert_restore_after(rows, after_ver)
