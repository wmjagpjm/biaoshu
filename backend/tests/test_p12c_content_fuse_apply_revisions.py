"""
模块：P12C-B-D1 content-fuse apply 修订账本原子接入专项测试
用途：真实 HTTP + SQLite 验收 content_fuse_apply 同事务记账、精确 +1、
  失败全域回滚、来源隔离、consume/checkpoint 未误接与反假绿。
对接：POST /api/projects/{id}/content-fuse-applications；
  record_editor_state_transition；content_fuse_application_service。
二次开发：禁止 mock 掉 SQLite、>= 宽松增量、空集合、随机 ID 顺序、AST 冒充原子性。
"""

from __future__ import annotations

import ast
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    ContentFuseApplicationBatchRow,
    EditorStateRevisionRow,
    ProjectEditorStateRow,
    ProjectTaskRow,
    utc_now,
)
from app.services import (
    content_fuse_application_service,
    editor_state_revision_service,
    editor_state_service,
)

_WS = "ws_local"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SOURCE_APPLY = "content_fuse_apply"
_SOURCE_CONSUME = "content_fuse_consume"
_SOURCE_BROWSER = "browser_put"
_SOURCE_CHECKPOINT = "checkpoint_restore"
_SECRET = "SECRET_P12CBD1_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbd1_injected_after_flush"
_INJECT_AFTER_TRIM = "p12cbd1_injected_after_trim"
_INJECT_COMMIT_FAIL = "p12cbd1_injected_commit_failure"
_CREATE_KEYS = frozenset({"batchId", "appliedChapterCount", "createdAt", "stateVersion"})

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "content_fuse_application_service.py"
)


# ---------- 基础辅助 ----------


def _bh(body: str) -> str:
    return "bh_" + sha1(body.encode("utf-8")).hexdigest()[:20]


def _base(title: str, body: str) -> dict:
    return {
        "title": title.strip(),
        "bodyHash": _bh(body),
        "bodyLength": len(body),
    }


def _assert_state_version(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _create_project(client: TestClient, name: str = "P12C-B-D1") -> str:
    res = client.post("/api/projects", json={"name": name, "kind": "technical"})
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


def _seed_chapters_via_browser(
    client: TestClient, pid: str, chapters: list[dict] | None = None
) -> dict:
    """用途：浏览器 PUT 写入章节，形成 browser_put 修订基线。"""
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
            "mode": "ALIGNED",
        },
    )


def _seed_chapters_without_revision(
    pid: str, chapters: list[dict] | None = None
) -> dict:
    """
    用途：服务层无 revision_source_kind 写入章节，保持账本为空。
    二次开发：禁止 HTTP PUT（会记 browser_put）。
    """
    chs = chapters or _default_chapters(2)
    db = SessionLocal()
    try:
        state = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            outline=[
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in chs
            ],
            chapters=chs,
            mode="ALIGNED",
        )
        return state
    finally:
        db.close()


def _dumps_safe(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _suggestion(
    sid: str,
    chapter_id: str,
    title: str,
    body: str,
    *,
    action: str = "merge",
    proposed: str | None = None,
    extra: dict | None = None,
) -> dict:
    item = {
        "suggestionId": sid,
        "targetChapterId": chapter_id,
        "targetTitle": title,
        "action": action,
        "proposedMarkdown": proposed
        if proposed is not None
        else f"融合后的{title}正文-{_SECRET}",
        "base": _base(title, body),
        "sourceRefs": [{"kind": "template", "id": "tpl_x", "title": "T"}],
    }
    if extra:
        item.update(extra)
    return item


def _default_suggestions(chapters: list[dict] | None = None) -> list[dict]:
    chs = chapters or _default_chapters(2)
    return [
        _suggestion(
            f"sug_{c['id']}",
            c["id"],
            c["title"],
            c["body"],
            action="merge" if i % 2 == 0 else "expand",
            proposed=(
                f"融合后的{c['title']}正文-{_SECRET}"
                if i % 2 == 0
                else f"追加段落-{c['title']}-{_SECRET}"
            ),
        )
        for i, c in enumerate(chs)
    ]


def _seed_success_task(
    project_id: str,
    *,
    suggestions: list[dict],
    task_id: str | None = None,
    task_type: str = "content_fuse",
    status: str = "success",
    result_extra: dict | None = None,
) -> str:
    tid = task_id or f"task_p12cbd1_{project_id[-8:]}_{len(suggestions)}"
    result: dict = {
        "model": "mock-p12cbd1",
        "suggestions": suggestions,
        "quota": {},
    }
    if result_extra:
        result.update(result_extra)
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
            result_json=_dumps_safe(result),
            error=None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    return tid


def _apply_url(pid: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications"


def _consume_url(pid: str, batch_id: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications/{batch_id}/consume"


def _apply(
    client: TestClient,
    pid: str,
    *,
    task_id: str,
    suggestion_ids: list[str],
    expected: str | None = None,
):
    payload = {
        "taskId": task_id,
        "suggestionIds": suggestion_ids,
        "expectedStateVersion": expected
        if expected is not None
        else _assert_state_version(_get_state(client, pid)["stateVersion"]),
    }
    return client.post(_apply_url(pid), json=payload)


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


def _source_count(
    rows: list[EditorStateRevisionRow], source_kind: str
) -> int:
    return sum(1 for r in rows if r.source_kind == source_kind)


def _apply_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_APPLY)


def _revision_identity_seq(
    rows: list[EditorStateRevisionRow],
) -> list[tuple[str, str, str]]:
    """
    用途：导出修订账本精确身份序列（id / state_version / source_kind）。
    二次开发：consume 隔离断言必须用此序列全等，禁止仅数来源计数。
    """
    return [(r.id, r.state_version, r.source_kind) for r in rows]


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _assert_apply_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_APPLY
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _db_batch_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ContentFuseApplicationBatchRow)
            .filter(ContentFuseApplicationBatchRow.project_id == project_id)
            .count()
        )
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


def _assert_success_apply(res, *, applied: int) -> dict:
    assert res.status_code == 201, res.text
    assert res.headers.get("Cache-Control") == "no-store"
    body = res.json()
    assert set(body.keys()) == _CREATE_KEYS
    assert body["appliedChapterCount"] == applied
    assert isinstance(body["batchId"], str) and body["batchId"].startswith("cfab_")
    after_ver = _assert_state_version(body["stateVersion"])
    raw = res.text
    # 响应不得暴露 revision 来源/ID/内部字段
    assert "sourceKind" not in raw
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "content_fuse_apply" not in raw
    assert "content_fuse_consume" not in raw
    assert "esr_" not in raw
    assert "source_kind" not in raw
    return body | {"_after_ver": after_ver}


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
    assert "content_fuse_apply" not in blob
    assert "source_kind" not in low
    assert "revision_source_kind" not in low
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


# ---------- AST 补充（不得替代 HTTP/SQLite） ----------


def test_ast_apply_unique_literal_source_consume_zero_recorder():
    """
    用途：AST 补充证明 apply 内唯一 record、字面量 content_fuse_apply；
      consume 与 restore 零 recorder 调用。不能替代真实 HTTP 证据。
    """
    apply_fn = _find_function_def(_SERVICE_PATH, "apply_content_fuse_application")
    assert apply_fn is not None

    record_calls: list[ast.Call] = []
    get_calls: list[ast.Call] = []
    for node in ast.walk(apply_fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_func_name(node)
        if name == "record_editor_state_transition":
            record_calls.append(node)
        if name == "get_editor_state":
            get_calls.append(node)

    assert len(record_calls) == 1, (
        f"apply 应有且仅有一次 record 调用，实际 {len(record_calls)}"
    )
    assert get_calls == [], "apply 成功路径禁止 get_editor_state 重读"
    src = _source_kind_literal_on_call(record_calls[0])
    assert src == _SOURCE_APPLY, f"source_kind 必须字面量 content_fuse_apply，实际 {src!r}"

    consume_fn = _find_function_def(_SERVICE_PATH, "consume_content_fuse_application")
    assert consume_fn is not None
    consume_records = [
        n
        for n in ast.walk(consume_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    assert consume_records == [], "consume 不得调用 recorder（D2 另包）"

    # 全文件不得出现 checkpoint_restore 来源或 restore 调用
    tree = ast.parse(_SERVICE_PATH.read_text(encoding="utf-8"))
    all_calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call)
    ]
    for call in all_calls:
        name = _call_func_name(call)
        assert name != "restore_editor_state_checkpoint"
        if name == "record_editor_state_transition":
            lit = _source_kind_literal_on_call(call)
            assert lit != _SOURCE_CHECKPOINT
            assert lit != _SOURCE_CONSUME


# ---------- 成功路径 ----------


def test_empty_ledger_apply_writes_before_and_after(client: TestClient):
    """用途：空账本既有技术标状态成功 apply → before+after 均 content_fuse_apply。"""
    pid = _create_project(client, name="空账本apply")
    seed = _seed_chapters_without_revision(pid)
    v0 = _assert_state_version(seed["stateVersion"])
    assert _db_rev_count(pid) == 0
    assert _apply_count(_db_rev_rows(pid)) == 0
    bodies0 = _db_chapter_bodies(pid)
    batches0 = _db_batch_count(pid)

    chs = _default_chapters(2)
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    res = _apply(client, pid, task_id=tid, suggestion_ids=["sug_chap_a"], expected=v0)
    body = _assert_success_apply(res, applied=1)
    after_ver = body["_after_ver"]
    assert after_ver != v0

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(state)
    chap_a = next(c for c in state["chapters"] if c["id"] == "chap_a")
    assert _SECRET in chap_a["body"]
    assert chap_a["status"] == "needs_review"

    rows = _db_rev_rows(pid)
    # 空账本：before + after → 精确 2 条，来源均为 content_fuse_apply
    assert len(rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_APPLY}
    after_row = _assert_apply_after(rows, after_ver)
    assert after_row.workspace_id == _WS
    assert after_row.project_id == pid
    versions = {r.state_version for r in rows}
    assert len(versions) == 2
    assert after_ver in versions
    before_ver = next(v for v in versions if v != after_ver)
    assert _assert_state_version(before_ver) == v0
    assert _SECRET in (after_row.snapshot_json or "")

    assert _db_batch_count(pid) == batches0 + 1
    assert _db_chapter_bodies(pid)["chap_a"] != bodies0["chap_a"]


def test_browser_put_baseline_one_to_five_suggestions_exact_plus_one(
    client: TestClient,
):
    """
    用途：browser_put 基线后，一至五条建议同批 apply 只精确 +1 content_fuse_apply after；
      浏览器行来源不变；其他项目零增量。
    """
    other = _create_project(client, name="其他项目隔离")
    other_seed = _seed_chapters_via_browser(client, other)
    other_v = other_seed["stateVersion"]
    other_n0 = _db_rev_count(other)
    other_apply0 = _apply_count(_db_rev_rows(other))

    for n in (1, 2, 3, 4, 5):
        pid = _create_project(client, name=f"多建议{n}")
        chs = _default_chapters(n)
        base = _seed_chapters_via_browser(client, pid, chs)
        v0 = _assert_state_version(base["stateVersion"])
        n0 = _db_rev_count(pid)
        apply0 = _apply_count(_db_rev_rows(pid))
        assert n0 >= 2
        assert apply0 == 0
        browser_matched = _rows_by_version(_db_rev_rows(pid), v0)
        assert len(browser_matched) == 1
        assert browser_matched[0].source_kind == _SOURCE_BROWSER

        sugs = _default_suggestions(chs)
        tid = _seed_success_task(pid, suggestions=sugs)
        sids = [s["suggestionId"] for s in sugs]
        res = _apply(client, pid, task_id=tid, suggestion_ids=sids, expected=v0)
        body = _assert_success_apply(res, applied=n)
        after_ver = body["_after_ver"]
        assert after_ver != v0

        state = _get_state(client, pid)
        assert state["stateVersion"] == after_ver
        rows = _db_rev_rows(pid)
        # 精确 +1：before 已是最新 browser_put → 只追加 after
        assert len(rows) == n0 + 1, (
            f"n={n} 期望 n0+1={n0 + 1}，实际 {len(rows)}: "
            f"{[(r.state_version, r.source_kind) for r in rows]}"
        )
        assert _apply_count(rows) == apply0 + 1
        after_row = _assert_apply_after(rows, after_ver)
        assert _SECRET in (after_row.snapshot_json or "")

        still_browser = _rows_by_version(rows, v0)
        assert len(still_browser) == 1
        assert still_browser[0].source_kind == _SOURCE_BROWSER
        # 不得按章节数多记
        assert _apply_count(rows) == 1

        # 其他项目全程零增量
        assert _db_rev_count(other) == other_n0
        assert _apply_count(_db_rev_rows(other)) == other_apply0 == 0
        other_state = _get_state(client, other)
        assert other_state["stateVersion"] == other_v


def test_task_metadata_cannot_control_revision_source(client: TestClient):
    """用途：任务结果中的 source/action/正文不能控制内部 revision 来源。"""
    pid = _create_project(client, name="来源隔离")
    base = _seed_chapters_via_browser(client, pid)
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    chs = _default_chapters(1)
    sugs = [
        _suggestion(
            "sug_poison",
            "chap_a",
            "总体架构",
            "现有架构正文。",
            action="rewrite",
            proposed=f"毒化正文-{_SECRET}",
            extra={
                "source": "browser_put",
                "sourceKind": "task",
                "revisionSourceKind": "revise",
                "revision_source_kind": "callback",
                "action": "rewrite",
            },
        )
    ]
    # 覆盖 result 顶层伪造来源字段
    tid = _seed_success_task(
        pid,
        suggestions=sugs,
        result_extra={
            "source": "checkpoint_restore",
            "sourceKind": "content_fuse_consume",
            "revisionSourceKind": "local_parser",
        },
    )
    res = _apply(client, pid, task_id=tid, suggestion_ids=["sug_poison"], expected=v0)
    body = _assert_success_apply(res, applied=1)
    after_ver = body["_after_ver"]

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    after_row = _assert_apply_after(rows, after_ver)
    assert after_row.source_kind == _SOURCE_APPLY
    # 伪造来源不得入库
    kinds = {r.source_kind for r in rows}
    assert "task" not in kinds
    assert "revise" not in kinds
    assert "callback" not in kinds
    assert "local_parser" not in kinds
    assert _SOURCE_CONSUME not in kinds
    assert _SOURCE_CHECKPOINT not in kinds
    assert all(
        r.source_kind in {_SOURCE_BROWSER, _SOURCE_APPLY} for r in rows
    )


# ---------- 失败零增量 ----------


def test_conflict_404_422_overlimit_zero_apply_revision(client: TestClient):
    """用途：缺/坏 expected、陈旧、任务/建议/base 冲突、零变化、超限、404/422 均零 content_fuse_apply。"""
    pid = _create_project(client, name="零增量矩阵")
    base = _seed_chapters_via_browser(client, pid)
    v0 = _assert_state_version(base["stateVersion"])
    n0 = _db_rev_count(pid)
    apply0 = _apply_count(_db_rev_rows(pid))
    batches0 = _db_batch_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)

    def _assert_zero(*, label: str) -> None:
        assert _db_rev_count(pid) == n0, label
        assert _apply_count(_db_rev_rows(pid)) == apply0 == 0, label
        assert _db_batch_count(pid) == batches0, label
        assert _db_chapter_bodies(pid) == bodies0, label
        state = _get_state(client, pid)
        assert state["stateVersion"] == v0, label

    # 422：缺 expectedStateVersion
    missing = client.post(
        _apply_url(pid),
        json={"taskId": tid, "suggestionIds": ["sug_chap_a"]},
    )
    assert missing.status_code == 422, missing.text
    _assert_zero(label="缺 expected")

    # 422：非法 expected 格式
    bad_fmt = client.post(
        _apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_chap_a"],
            "expectedStateVersion": "not_a_version",
        },
    )
    assert bad_fmt.status_code == 422, bad_fmt.text
    _assert_zero(label="坏 expected 格式")

    # 409：全状态陈旧
    advanced = _put_state(
        client,
        pid,
        {
            "chapters": base["chapters"],
            "outline": base["outline"],
            "mode": "ALIGNED",
            "facts": [{"id": "f_adv", "text": "推进版本"}],
            "expectedStateVersion": v0,
        },
    )
    v1 = advanced["stateVersion"]
    n1 = _db_rev_count(pid)
    apply1 = _apply_count(_db_rev_rows(pid))
    batches1 = _db_batch_count(pid)
    bodies1 = _db_chapter_bodies(pid)
    stale = _apply(
        client, pid, task_id=tid, suggestion_ids=["sug_chap_a"], expected=v0
    )
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail.get("code") == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    assert detail.get("currentStateVersion") == v1
    assert _db_rev_count(pid) == n1
    assert _apply_count(_db_rev_rows(pid)) == apply1 == 0
    assert _db_batch_count(pid) == batches1
    assert _db_chapter_bodies(pid) == bodies1

    # 重新对齐当前版本后的业务冲突矩阵
    v_cur = v1
    n_cur = n1
    apply_cur = apply1
    batches_cur = batches1
    bodies_cur = bodies1

    def _assert_zero_cur(label: str) -> None:
        assert _db_rev_count(pid) == n_cur, label
        assert _apply_count(_db_rev_rows(pid)) == apply_cur == 0, label
        assert _db_batch_count(pid) == batches_cur, label
        assert _db_chapter_bodies(pid) == bodies_cur, label
        assert _get_state(client, pid)["stateVersion"] == v_cur, label

    # 404：任务不存在
    miss_task = _apply(
        client,
        pid,
        task_id="task_missing_p12cbd1",
        suggestion_ids=["sug_chap_a"],
        expected=v_cur,
    )
    _assert_fixed_error(
        miss_task, 404, "content_fuse_task_not_found", "task_missing_p12cbd1"
    )
    _assert_zero_cur("缺任务")

    # 409：未知建议
    bad_sug = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_not_exist"],
        expected=v_cur,
    )
    _assert_fixed_error(bad_sug, 409, "content_fuse_apply_conflict")
    _assert_zero_cur("未知建议")

    # 409：base 漂移（建议仍绑旧正文）
    # 当前章节正文仍是 seed 正文，但把任务 base 改成错误 hash
    poison_sugs = [
        {
            **sugs[0],
            "base": {
                "title": "总体架构",
                "bodyHash": "bh_" + "0" * 20,
                "bodyLength": 1,
            },
        }
    ]
    tid_poison = _seed_success_task(pid, suggestions=poison_sugs, task_id="task_poison_base")
    base_drift = _apply(
        client,
        pid,
        task_id=tid_poison,
        suggestion_ids=["sug_chap_a"],
        expected=v_cur,
    )
    _assert_fixed_error(base_drift, 409, "content_fuse_apply_conflict")
    _assert_zero_cur("base 漂移")

    # 409：零变化（proposed == live）
    zero_sugs = [
        _suggestion(
            "sug_zero",
            "chap_a",
            "总体架构",
            "现有架构正文。",
            action="merge",
            proposed="现有架构正文。",
        )
    ]
    tid_zero = _seed_success_task(pid, suggestions=zero_sugs, task_id="task_zero")
    zero = _apply(
        client, pid, task_id=tid_zero, suggestion_ids=["sug_zero"], expected=v_cur
    )
    _assert_fixed_error(zero, 409, "content_fuse_apply_conflict")
    _assert_zero_cur("零变化")

    # 409：超限快照
    huge = "汉" * (2 * 1024 * 1024)
    huge_sugs = [
        _suggestion(
            "sug_huge",
            "chap_a",
            "总体架构",
            "现有架构正文。",
            action="rewrite",
            proposed=huge,
        )
    ]
    tid_huge = _seed_success_task(pid, suggestions=huge_sugs, task_id="task_huge")
    over = _apply(
        client, pid, task_id=tid_huge, suggestion_ids=["sug_huge"], expected=v_cur
    )
    _assert_fixed_error(over, 409, "content_fuse_apply_conflict")
    _assert_zero_cur("超限快照")

    # 404：项目不存在
    miss_proj = client.post(
        _apply_url("proj_does_not_exist_p12cbd1"),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_chap_a"],
            "expectedStateVersion": v_cur,
        },
    )
    assert miss_proj.status_code == 404, miss_proj.text
    assert _db_rev_count(pid) == n_cur
    assert _apply_count(_db_rev_rows(pid)) == 0


def test_concurrent_double_apply_one_success_one_409_single_revision(
    client: TestClient,
):
    """用途：真实双并发恰好一胜一 409；胜者只留一条 content_fuse_apply。"""
    pid = _create_project(client, name="双并发apply")
    base = _seed_chapters_via_browser(client, pid)
    v0 = _assert_state_version(base["stateVersion"])
    n0 = _db_rev_count(pid)
    apply0 = _apply_count(_db_rev_rows(pid))
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)
    barrier = threading.Barrier(2)
    outcomes: list[int] = []

    def worker() -> int:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                content_fuse_application_service.apply_content_fuse_application(
                    db,
                    _WS,
                    pid,
                    task_id=tid,
                    suggestion_ids=["sug_chap_a"],
                    expected_state_version=v0,
                )
                return 201
            except editor_state_service.EditorStateVersionConflict:
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
    assert _db_batch_count(pid) == 1
    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _apply_count(rows) == apply0 + 1 == 1
    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    _assert_apply_after(rows, after_ver)
    assert _SECRET in _db_chapter_bodies(pid)["chap_a"]


# ---------- 失败原子性 ----------


def test_recorder_flush_then_fail_full_rollback(client: TestClient, monkeypatch):
    """用途：recorder 真实 flush 后注入失败 → 章节/批次/revision 全回滚；公开 500 不泄漏。"""
    pid = _create_project(client, name="recorder注入")
    base = _seed_chapters_via_browser(client, pid)
    v0 = _assert_state_version(base["stateVersion"])
    n0 = _db_rev_count(pid)
    apply0 = _apply_count(_db_rev_rows(pid))
    batches0 = _db_batch_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        assert out["added_count"] == 1
        assert kwargs.get("source_kind") == _SOURCE_APPLY
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )
    # 若生产用局部 from-import，也同步 patch 服务模块命名空间
    if hasattr(content_fuse_application_service, "editor_state_revision_service"):
        monkeypatch.setattr(
            content_fuse_application_service.editor_state_revision_service,
            "record_editor_state_transition",
            _record_then_boom,
        )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _apply_url(pid),
            json={
                "taskId": tid,
                "suggestionIds": ["sug_chap_a"],
                "expectedStateVersion": v0,
            },
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_AFTER_FLUSH,
        pid,
        v0,
        tid,
        "RuntimeError",
        "sug_chap_a",
    )

    assert _db_rev_count(pid) == n0
    assert _apply_count(_db_rev_rows(pid)) == apply0 == 0
    assert _db_batch_count(pid) == batches0
    assert _db_chapter_bodies(pid) == bodies0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert _SECRET not in json.dumps(state, ensure_ascii=False)


def test_trim_batches_then_fail_full_rollback(client: TestClient, monkeypatch):
    """用途：批次裁剪成功后注入异常 → 章节/批次/revision 全回滚；公开 500 不泄漏。"""
    pid = _create_project(client, name="trim注入")
    base = _seed_chapters_via_browser(client, pid)
    v0 = _assert_state_version(base["stateVersion"])
    n0 = _db_rev_count(pid)
    apply0 = _apply_count(_db_rev_rows(pid))
    batches0 = _db_batch_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)

    real_trim = content_fuse_application_service._trim_batches
    calls = {"n": 0}

    def _trim_then_boom(*args, **kwargs):
        calls["n"] += 1
        real_trim(*args, **kwargs)
        raise RuntimeError(_INJECT_AFTER_TRIM)

    monkeypatch.setattr(
        content_fuse_application_service,
        "_trim_batches",
        _trim_then_boom,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _apply_url(pid),
            json={
                "taskId": tid,
                "suggestionIds": ["sug_chap_a"],
                "expectedStateVersion": v0,
            },
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_AFTER_TRIM,
        pid,
        v0,
        tid,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _apply_count(_db_rev_rows(pid)) == apply0 == 0
    assert _db_batch_count(pid) == batches0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid)["stateVersion"] == v0


def test_commit_failure_pending_flush_then_full_rollback(
    client: TestClient, monkeypatch
):
    """
    用途：同一 Session 在 commit 前精确证明 content_fuse_apply after 已 flush；
      commit 失败后固定 500、章节/批次/revision 全域回滚。
    """
    pid = _create_project(client, name="commit失败")
    base = _seed_chapters_via_browser(client, pid)
    v0 = _assert_state_version(base["stateVersion"])
    n0 = _db_rev_count(pid)
    apply0 = _apply_count(_db_rev_rows(pid))
    batches0 = _db_batch_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)

    commit_probe = {"n": 0, "pending": None, "apply_pending": None, "source": None}
    rollbacks = {"n": 0}
    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm_commit(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*a, **k):
            commit_probe["n"] += 1
            commit_probe["pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            apply_rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_APPLY,
                )
                .all()
            )
            commit_probe["apply_pending"] = len(apply_rows)
            if apply_rows:
                commit_probe["source"] = apply_rows[-1].source_kind
                commit_probe["after_ver"] = apply_rows[-1].state_version
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        def _count_rollback(*a, **k):
            rollbacks["n"] += 1
            return real_rollback(*a, **k)

        db.commit = _bad_commit  # type: ignore[method-assign]
        db.rollback = _count_rollback  # type: ignore[method-assign]
        return out

    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm_commit,
    )
    if hasattr(content_fuse_application_service, "editor_state_service"):
        monkeypatch.setattr(
            content_fuse_application_service.editor_state_service,
            "lock_and_assert_expected_state_version",
            _lock_then_arm_commit,
        )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _apply_url(pid),
            json={
                "taskId": tid,
                "suggestionIds": ["sug_chap_a"],
                "expectedStateVersion": v0,
            },
        )

    assert commit_probe["n"] == 1
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应已 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert commit_probe["apply_pending"] == 1, (
        f"commit 前 content_fuse_apply 行应精确为 1，实际 {commit_probe['apply_pending']}"
    )
    assert commit_probe["source"] == _SOURCE_APPLY
    assert _assert_state_version(commit_probe["after_ver"]) != v0
    # apply 服务本身不新增 rollback 包装；依赖 Session 关闭/连接回滚
    # 以新 Session 全域零写为硬证据，rollback 计数仅作辅助观测
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_COMMIT_FAIL,
        pid,
        v0,
        tid,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _apply_count(_db_rev_rows(pid)) == apply0 == 0
    assert _db_batch_count(pid) == batches0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid)["stateVersion"] == v0
    _ = rollbacks  # 观测用；apply 不新增 rollback 包装，以库内零写为准


# ---------- consume / checkpoint 未误接 ----------


def test_consume_after_apply_does_not_record_consume_or_extra_apply(
    client: TestClient,
):
    """
    用途：成功 apply 后执行零/部分/完整 consume，本包只保留 apply 修订；
      content_fuse_consume 行数精确为零，且修订账本身份序列在 consume 前后完全不变。
    """
    pid = _create_project(client, name="consume未误接")
    chs = _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)

    # 完整恢复路径：双章 apply
    res = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_chap_a", "sug_chap_b"],
        expected=v0,
    )
    body = _assert_success_apply(res, applied=2)
    after_apply = body["_after_ver"]
    batch_full = body["batchId"]
    rows_after_apply = _db_rev_rows(pid)
    apply_n = _apply_count(rows_after_apply)
    assert apply_n == 1
    assert _source_count(rows_after_apply, _SOURCE_CONSUME) == 0
    # 完整 consume 前：锁定精确修订身份序列（id/版本/来源）
    ledger_before_full_consume = _revision_identity_seq(rows_after_apply)
    assert len(ledger_before_full_consume) == len(rows_after_apply)
    assert all(
        _REVISION_ID_RE.fullmatch(rid) and _STATE_VERSION_RE.fullmatch(ver)
        for rid, ver, _src in ledger_before_full_consume
    )

    # 完整 consume（两章均可恢复）
    c1 = client.post(
        _consume_url(pid, batch_full),
        json={"expectedStateVersion": after_apply},
    )
    assert c1.status_code == 200, c1.text
    assert c1.json()["restoredChapterCount"] == 2
    rows1 = _db_rev_rows(pid)
    # D1 不接 consume：章节状态可变，修订账本身份序列必须完全不变
    assert len(rows1) == len(ledger_before_full_consume)
    assert _revision_identity_seq(rows1) == ledger_before_full_consume
    assert _apply_count(rows1) == apply_n == 1
    assert _source_count(rows1, _SOURCE_CONSUME) == 0
    assert all(r.source_kind != _SOURCE_CONSUME for r in rows1)
    assert all(r.source_kind != _SOURCE_CHECKPOINT for r in rows1)

    # 部分恢复：再 apply 双章；consume 不记账 → 最新仍是上次 apply after → 补 before+after
    state_mid = _get_state(client, pid)
    v_mid = state_mid["stateVersion"]
    assert v_mid != after_apply  # 完整 consume 已改状态版本但未落账
    chs_now = state_mid["chapters"]
    live_a = next(c for c in chs_now if c["id"] == "chap_a")
    live_b = next(c for c in chs_now if c["id"] == "chap_b")
    sugs2 = [
        _suggestion(
            "sug2_a",
            "chap_a",
            live_a["title"],
            live_a["body"],
            proposed=f"第二批架构-{_SECRET}",
        ),
        _suggestion(
            "sug2_b",
            "chap_b",
            live_b["title"],
            live_b["body"],
            proposed=f"第二批安全-{_SECRET}",
        ),
    ]
    tid2 = _seed_success_task(pid, suggestions=sugs2, task_id="task_partial")
    res2 = _apply(
        client,
        pid,
        task_id=tid2,
        suggestion_ids=["sug2_a", "sug2_b"],
        expected=v_mid,
    )
    body2 = _assert_success_apply(res2, applied=2)
    after2 = body2["_after_ver"]
    batch2 = body2["batchId"]
    rows_after_apply2 = _db_rev_rows(pid)
    apply_n2 = _apply_count(rows_after_apply2)
    # 精确 +2：before（consume 后断链补点）+ after
    assert apply_n2 == apply_n + 2 == 3
    assert _source_count(rows_after_apply2, _SOURCE_CONSUME) == 0

    # 外部 browser_put 改写 chap_b → 部分漂移已落账
    state_after2 = _get_state(client, pid)
    drifted = _put_state(
        client,
        pid,
        {
            "chapters": [
                next(c for c in state_after2["chapters"] if c["id"] == "chap_a"),
                {
                    **(next(c for c in state_after2["chapters"] if c["id"] == "chap_b")),
                    "body": "外部漂移正文-不得被 consume 记修订",
                    "status": "needs_review",
                },
            ],
            "expectedStateVersion": after2,
        },
    )
    v_drift = drifted["stateVersion"]
    rows_before_partial = _db_rev_rows(pid)
    apply_before_partial = _apply_count(rows_before_partial)
    # browser_put 漂移落账后、部分 consume 前：锁定精确身份序列
    ledger_before_partial_consume = _revision_identity_seq(rows_before_partial)
    assert apply_before_partial == apply_n2 == 3
    assert len(ledger_before_partial_consume) == len(rows_before_partial)
    # 漂移 PUT 相对第二轮 apply 后精确 +1 browser_put（总数 = apply2 账本 + 1）
    assert len(ledger_before_partial_consume) == len(rows_after_apply2) + 1
    drift_rows = _rows_by_version(rows_before_partial, v_drift)
    assert len(drift_rows) == 1
    assert drift_rows[0].source_kind == _SOURCE_BROWSER

    c_partial = client.post(
        _consume_url(pid, batch2),
        json={"expectedStateVersion": v_drift},
    )
    assert c_partial.status_code == 200, c_partial.text
    assert c_partial.json()["restoredChapterCount"] == 1
    assert c_partial.json()["skippedChapterCount"] == 1
    rows_partial = _db_rev_rows(pid)
    # 部分恢复会改 editor-state，但修订账本身份序列必须完全不变
    assert len(rows_partial) == len(ledger_before_partial_consume)
    assert _revision_identity_seq(rows_partial) == ledger_before_partial_consume
    assert _apply_count(rows_partial) == apply_before_partial == 3
    assert _source_count(rows_partial, _SOURCE_CONSUME) == 0
    assert all(r.source_kind != _SOURCE_CHECKPOINT for r in rows_partial)

    # 零恢复：再 apply 后外部双章漂移
    state3 = _get_state(client, pid)
    v3 = state3["stateVersion"]
    assert v3 != v_drift  # 部分 consume 已改状态版本但未落账
    live_a3 = next(c for c in state3["chapters"] if c["id"] == "chap_a")
    sugs3 = [
        _suggestion(
            "sug3_a",
            "chap_a",
            live_a3["title"],
            live_a3["body"],
            proposed=f"第三批架构-{_SECRET}",
        )
    ]
    tid3 = _seed_success_task(pid, suggestions=sugs3, task_id="task_zero_consume")
    res3 = _apply(client, pid, task_id=tid3, suggestion_ids=["sug3_a"], expected=v3)
    body3 = _assert_success_apply(res3, applied=1)
    after3 = body3["_after_ver"]
    batch3 = body3["batchId"]
    rows_after_apply3 = _db_rev_rows(pid)
    apply_n3 = _apply_count(rows_after_apply3)
    # 精确 +2：before（部分 consume 后断链补点）+ after
    assert apply_n3 == apply_n2 + 2 == 5
    assert _source_count(rows_after_apply3, _SOURCE_CONSUME) == 0

    state_after3 = _get_state(client, pid)
    drifted_zero = _put_state(
        client,
        pid,
        {
            "chapters": [
                {
                    **(next(c for c in state_after3["chapters"] if c["id"] == "chap_a")),
                    "body": "零恢复漂移A",
                    "status": "done",
                },
                {
                    **(next(c for c in state_after3["chapters"] if c["id"] == "chap_b")),
                    "body": "零恢复漂移B",
                    "status": "done",
                },
            ],
            "expectedStateVersion": after3,
        },
    )
    v_zero = drifted_zero["stateVersion"]
    rows_before_zero = _db_rev_rows(pid)
    apply_before_zero = _apply_count(rows_before_zero)
    n_before_zero = len(rows_before_zero)
    ledger_before_zero_consume = _revision_identity_seq(rows_before_zero)
    assert apply_before_zero == apply_n3 == 5
    # 零恢复漂移 PUT 相对第三轮 apply 后精确 +1 browser_put
    assert n_before_zero == len(rows_after_apply3) + 1

    c_zero = client.post(
        _consume_url(pid, batch3),
        json={"expectedStateVersion": v_zero},
    )
    assert c_zero.status_code == 200, c_zero.text
    assert c_zero.json()["restoredChapterCount"] == 0
    assert c_zero.json()["stateVersion"] == v_zero  # 零恢复版本不变
    rows_zero = _db_rev_rows(pid)
    # 零恢复：版本不变，修订账本身份序列精确零增量
    assert len(rows_zero) == n_before_zero
    assert _revision_identity_seq(rows_zero) == ledger_before_zero_consume
    assert _apply_count(rows_zero) == apply_before_zero == apply_n3 == 5
    assert _source_count(rows_zero, _SOURCE_CONSUME) == 0
    assert all(r.source_kind != _SOURCE_CHECKPOINT for r in rows_zero)
    # 全程：完整/部分/零 consume 均未写入 content_fuse_consume
    assert _source_count(rows_zero, _SOURCE_CONSUME) == 0
    # 全程精确 apply 计数：1 → 3 → 5（每次 consume 断链后 apply 补 before+after）
    assert apply_n == 1 and apply_n2 == 3 and apply_n3 == 5


def test_response_version_matches_get_and_after_snapshot(client: TestClient):
    """用途：响应 stateVersion 与 GET / after 行精确一致。"""
    pid = _create_project(client, name="版本一致")
    base = _seed_chapters_via_browser(client, pid)
    v0 = base["stateVersion"]
    sugs = _default_suggestions()
    tid = _seed_success_task(pid, suggestions=sugs)
    res = _apply(client, pid, task_id=tid, suggestion_ids=["sug_chap_a"], expected=v0)
    body = _assert_success_apply(res, applied=1)
    after_ver = body["_after_ver"]

    got = _get_state(client, pid)
    assert got["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(got)
    rows = _db_rev_rows(pid)
    after_row = _assert_apply_after(rows, after_ver)
    # after 快照版本字段必须与 state_version 列一致
    snap = json.loads(after_row.snapshot_json)
    # snapshot 是 13 键规范快照，不一定含 stateVersion；以行 state_version 为准
    assert after_row.state_version == after_ver
    assert isinstance(snap, dict)
    assert "chapters" in snap
