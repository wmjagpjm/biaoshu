"""
模块：P12C-B-D2 content-fuse consume 修订账本原子接入专项测试
用途：真实 HTTP + SQLite 验收 content_fuse_consume 条件记账、精确 +1、
  零恢复不伪造、失败全域回滚、双并发一次性和反假绿。
对接：POST .../content-fuse-applications/{batchId}/consume；
  record_editor_state_transition；content_fuse_application_service。
二次开发：禁止 mock 掉 SQLite、>= 宽松增量、空集合、随机 ID 顺序、
  固定 sleep、顺序调用冒充并发、AST 冒充原子性。
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
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    Workspace,
    utc_now,
)
from app.services import (
    content_fuse_application_service,
    editor_state_revision_service,
    editor_state_service,
)

_WS = "ws_local"
_WS_OTHER = "ws_other_p12cbd2"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SOURCE_APPLY = "content_fuse_apply"
_SOURCE_CONSUME = "content_fuse_consume"
_SOURCE_BROWSER = "browser_put"
_SOURCE_CHECKPOINT = "checkpoint_restore"
_SECRET = "SECRET_P12CBD2_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbd2_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cbd2_injected_commit_failure"
_CONSUME_KEYS = frozenset(
    {"restoredChapterCount", "skippedChapterCount", "consumedAt", "stateVersion"}
)
# 公开 500 固定禁止泄漏的表名 / 服务路径片段（大小写不敏感）
_SANITIZE_FORBIDDEN = (
    "editor_state_revisions",
    "content_fuse_application_batches",
    "content_fuse_application_service",
    "editor_state_revision_service",
    "editor_state_service.py",
    "content_fuse_application_service.py",
    "backend/app/services",
    "backend\\app\\services",
    str(Path(__file__).resolve().parents[1] / "app" / "services"),
)

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


def _create_project(client: TestClient, name: str = "P12C-B-D2") -> str:
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
    """用途：服务层无 revision 写入，保持账本为空。"""
    chs = chapters or _default_chapters(2)
    db = SessionLocal()
    try:
        return editor_state_service.upsert_editor_state(
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
) -> dict:
    return {
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
) -> str:
    tid = task_id or f"task_p12cbd2_{project_id[-8:]}_{len(suggestions)}"
    result = {
        "model": "mock-p12cbd2",
        "suggestions": suggestions,
        "quota": {},
    }
    db = SessionLocal()
    try:
        row = ProjectTaskRow(
            id=tid,
            project_id=project_id,
            type="content_fuse",
            status="success",
            progress=100,
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


def _assert_success_apply(res, *, applied: int) -> dict:
    assert res.status_code == 201, res.text
    body = res.json()
    after_ver = _assert_state_version(body["stateVersion"])
    assert body["appliedChapterCount"] == applied
    return body | {"_after_ver": after_ver}


def _assert_success_consume(
    res, *, restored: int, skipped: int | None = None
) -> dict:
    assert res.status_code == 200, res.text
    assert res.headers.get("Cache-Control") == "no-store"
    body = res.json()
    assert set(body.keys()) == _CONSUME_KEYS
    assert body["restoredChapterCount"] == restored
    if skipped is not None:
        assert body["skippedChapterCount"] == skipped
    after_ver = _assert_state_version(body["stateVersion"])
    raw = res.text
    assert "sourceKind" not in raw
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "content_fuse_consume" not in raw
    assert "content_fuse_apply" not in raw
    assert "esr_" not in raw
    assert "source_kind" not in raw
    return body | {"_after_ver": after_ver}


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


def _consume_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_CONSUME)


def _apply_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_APPLY)


def _revision_identity_seq(
    rows: list[EditorStateRevisionRow],
) -> list[tuple[str, str, str]]:
    return [(r.id, r.state_version, r.source_kind) for r in rows]


def _filtered_identity_seq(
    rows: list[EditorStateRevisionRow],
    sources: set[str],
) -> list[tuple[str, str, str]]:
    """用途：按来源过滤后的精确身份序列，禁止 subset / >= 假绿。"""
    return [
        (r.id, r.state_version, r.source_kind)
        for r in rows
        if r.source_kind in sources
    ]


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _ensure_workspace(ws_id: str, name: str = "其他空间P12CBD2") -> None:
    """用途：真实插入跨空间 Workspace，禁止伪造仅服务层异常。"""
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12cbd2",
                )
            )
            db.commit()
    finally:
        db.close()


def _assert_consume_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    """
    用途：按版本+来源锁定 consume after 行。
    二次开发：完整恢复可能回到历史 browser_put 同一 stateVersion，
      禁止仅按版本匹配（会命中历史行）。
    """
    matched = [
        r
        for r in rows
        if r.state_version == after_ver and r.source_kind == _SOURCE_CONSUME
    ]
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _db_batch(project_id: str, batch_id: str) -> ContentFuseApplicationBatchRow | None:
    db = SessionLocal()
    try:
        row = db.get(ContentFuseApplicationBatchRow, batch_id)
        if row is None or row.project_id != project_id:
            return None
        # 脱离 session 前读属性
        _ = (row.state, row.consumed_at, row.snapshot_json)
        db.expunge(row)
        return row
    finally:
        db.close()


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


def _db_active_batch_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ContentFuseApplicationBatchRow)
            .filter(
                ContentFuseApplicationBatchRow.project_id == project_id,
                ContentFuseApplicationBatchRow.state == "active",
            )
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


def _db_chapter_statuses(project_id: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None or not row.chapters_json:
            return {}
        raw = json.loads(row.chapters_json)
        out: dict[str, str] = {}
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and type(item.get("id")) is str:
                    st = item.get("status")
                    out[item["id"]] = st if type(st) is str else ""
        return out
    finally:
        db.close()


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
    assert "content_fuse_consume" not in blob
    assert "content_fuse_apply" not in blob
    assert "source_kind" not in low
    assert "revision_source_kind" not in low
    # 固定禁止表名 / 服务文件名 / 绝对仓库路径泄漏
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


def _insert_legacy_active_batch(
    *,
    project_id: str,
    chapters_after: list[dict],
    before_bodies: dict[str, str],
    before_statuses: dict[str, str] | None = None,
    batch_id: str | None = None,
    workspace_id: str = _WS,
) -> str:
    """
    用途：模拟 D1 以前遗留的空账本 active 批次（当前章已是 after）。
    """
    bid = batch_id or f"cfab_legacy_{project_id[-8:]}"
    snap_chapters = []
    for c in chapters_after:
        cid = c["id"]
        snap_chapters.append(
            {
                "suggestionId": f"sug_legacy_{cid}",
                "chapterId": cid,
                "title": c["title"],
                "beforeBody": before_bodies[cid],
                "beforeStatus": (before_statuses or {}).get(cid, "pending"),
                "afterBody": c["body"],
                "afterStatus": c.get("status") or "needs_review",
            }
        )
    now = utc_now()
    db = SessionLocal()
    try:
        row = ContentFuseApplicationBatchRow(
            id=bid,
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=f"task_legacy_{project_id[-8:]}",
            snapshot_json=_dumps_safe({"chapters": snap_chapters}),
            state="active",
            created_at=now,
            consumed_at=None,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    return bid


def _seed_foreign_workspace_technical_with_batch(
    *,
    project_id: str = "proj_other_p12cbd2",
    batch_id: str = "cfab_other_p12cbd2",
) -> tuple[str, str, str, dict]:
    """
    用途：真实插入外空间技术标 + 编辑态 + active 批次，供跨空间 HTTP 隔离。
    返回 (project_id, batch_id, state_version, editor_state_dict)。
    """
    _ensure_workspace(_WS_OTHER)
    chs = _default_chapters(2)
    after_chs = [
        {
            **c,
            "body": f"外空间融合后-{c['title']}-{_SECRET}",
            "status": "needs_review",
        }
        for c in chs
    ]
    db = SessionLocal()
    try:
        if db.get(Project, project_id) is None:
            db.add(
                Project(
                    id=project_id,
                    workspace_id=_WS_OTHER,
                    name="外空间技术标-d2",
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
            outline=[
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in after_chs
            ],
            chapters=after_chs,
            mode="ALIGNED",
        )
    finally:
        db.close()
    ver = _assert_state_version(state["stateVersion"])
    # 若批次已存在则保持 active 快照供本测复用
    existing = _db_batch(project_id, batch_id)
    if existing is None:
        _insert_legacy_active_batch(
            project_id=project_id,
            chapters_after=after_chs,
            before_bodies={c["id"]: c["body"] for c in chs},
            batch_id=batch_id,
            workspace_id=_WS_OTHER,
        )
    return project_id, batch_id, ver, state


def _apply_then_get_batch(
    client: TestClient,
    pid: str,
    *,
    chapters: list[dict] | None = None,
    suggestion_ids: list[str] | None = None,
) -> tuple[str, str, str, list[dict]]:
    """返回 (batch_id, after_version, task_id, chapters)。"""
    chs = chapters or _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = _assert_state_version(base["stateVersion"])
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    sids = suggestion_ids or [s["suggestionId"] for s in sugs]
    res = _apply(client, pid, task_id=tid, suggestion_ids=sids, expected=v0)
    body = _assert_success_apply(res, applied=len(sids))
    return body["batchId"], body["_after_ver"], tid, chs


# ---------- AST 补充 ----------


def test_ast_consume_conditional_literal_source_no_get_reread():
    """
    用途：AST 补充 consume 内唯一 recorder、字面量 content_fuse_consume、
      无 get_editor_state；不能替代 HTTP 证据。
    """
    consume_fn = _find_function_def(_SERVICE_PATH, "consume_content_fuse_application")
    assert consume_fn is not None
    records = [
        n
        for n in ast.walk(consume_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    gets = [
        n
        for n in ast.walk(consume_fn)
        if isinstance(n, ast.Call) and _call_func_name(n) == "get_editor_state"
    ]
    assert len(records) == 1, f"consume 应唯一一次 record，实际 {len(records)}"
    assert gets == [], "consume 禁止 get_editor_state 重读"
    assert _source_kind_literal_on_call(records[0]) == _SOURCE_CONSUME


# ---------- 成功路径 ----------


def test_legacy_empty_ledger_full_restore_before_and_after(client: TestClient):
    """用途：遗留空账本 active 批次完整恢复 → before+after 均 content_fuse_consume。"""
    pid = _create_project(client, name="遗留空账本完整")
    # 当前章已是 after 态
    after_chs = []
    before_bodies = {}
    for i, c in enumerate(_default_chapters(2)):
        before_bodies[c["id"]] = c["body"]
        after_chs.append(
            {
                **c,
                "body": f"融合后的{c['title']}正文-{_SECRET}",
                "status": "needs_review",
            }
        )
    seed = _seed_chapters_without_revision(pid, after_chs)
    v0 = _assert_state_version(seed["stateVersion"])
    assert _db_rev_count(pid) == 0
    bid = _insert_legacy_active_batch(
        project_id=pid,
        chapters_after=after_chs,
        before_bodies=before_bodies,
    )
    bodies0 = _db_chapter_bodies(pid)

    res = client.post(
        _consume_url(pid, bid),
        json={"expectedStateVersion": v0},
    )
    body = _assert_success_consume(res, restored=2, skipped=0)
    after_ver = body["_after_ver"]
    assert after_ver != v0
    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(state)
    # 恢复到 before
    assert _db_chapter_bodies(pid) == before_bodies
    assert bodies0 != before_bodies

    rows = _db_rev_rows(pid)
    # 空账本：before + after → 精确 2 条，来源均为 content_fuse_consume
    assert len(rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_CONSUME}
    after_row = _assert_consume_after(rows, after_ver)
    versions = {r.state_version for r in rows}
    assert len(versions) == 2
    assert after_ver in versions
    before_ver = next(v for v in versions if v != after_ver)
    assert before_ver == v0
    assert before_bodies["chap_a"] in (after_row.snapshot_json or "")
    batch = _db_batch(pid, bid)
    assert batch is not None
    assert batch.state == "consumed"
    assert batch.consumed_at is not None


def test_legacy_empty_ledger_zero_restore_no_revision(client: TestClient):
    """用途：空账本零恢复只消费批次，修订仍精确为零。"""
    pid = _create_project(client, name="遗留空账本零恢复")
    # 当前章已漂移，与 snapshot after 全不等
    live_chs = _default_chapters(2)
    for c in live_chs:
        c["body"] = f"已漂移-{c['id']}"
        c["status"] = "done"
    seed = _seed_chapters_without_revision(pid, live_chs)
    v0 = _assert_state_version(seed["stateVersion"])
    assert _db_rev_count(pid) == 0
    # snapshot after 是旧融合态，与 live 全不等 → restored=0
    after_chs = [
        {
            **c,
            "body": f"融合后的{c['title']}",
            "status": "needs_review",
        }
        for c in _default_chapters(2)
    ]
    bid = _insert_legacy_active_batch(
        project_id=pid,
        chapters_after=after_chs,
        before_bodies={c["id"]: c["body"] for c in _default_chapters(2)},
    )
    bodies0 = _db_chapter_bodies(pid)
    statuses0 = _db_chapter_statuses(pid)
    # 请求前完整 GET 字典（含 13 键 + updatedAt + stateVersion）
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == v0
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    assert ledger0 == []

    res = client.post(
        _consume_url(pid, bid),
        json={"expectedStateVersion": v0},
    )
    body = _assert_success_consume(res, restored=0, skipped=2)
    assert body["_after_ver"] == v0
    # 完整 GET 精确全等：13 键及 updatedAt 均不变
    assert _get_state(client, pid) == state0
    assert _db_chapter_bodies(pid) == bodies0
    assert _db_chapter_statuses(pid) == statuses0
    assert _db_rev_count(pid) == 0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0 == []
    batch = _db_batch(pid, bid)
    assert batch is not None
    assert batch.state == "consumed"
    assert batch.consumed_at is not None


def test_d1_apply_full_restore_exact_plus_one(client: TestClient):
    """用途：D1 apply 后完整恢复一至五章，同批精确 +1 content_fuse_consume。"""
    other = _create_project(client, name="其他项目隔离-d2")
    other_seed = _seed_chapters_via_browser(client, other)
    other_v = other_seed["stateVersion"]
    other_n0 = _db_rev_count(other)
    other_consume0 = _consume_count(_db_rev_rows(other))

    for n in (1, 2, 3, 4, 5):
        pid = _create_project(client, name=f"完整恢复{n}")
        chs = _default_chapters(n)
        base = _seed_chapters_via_browser(client, pid, chs)
        v0 = _assert_state_version(base["stateVersion"])
        sugs = _default_suggestions(chs)
        tid = _seed_success_task(pid, suggestions=sugs)
        sids = [s["suggestionId"] for s in sugs]
        apply_res = _apply(client, pid, task_id=tid, suggestion_ids=sids, expected=v0)
        apply_body = _assert_success_apply(apply_res, applied=n)
        after_apply = apply_body["_after_ver"]
        batch_id = apply_body["batchId"]
        n_before = _db_rev_count(pid)
        apply_n = _apply_count(_db_rev_rows(pid))
        consume_n0 = _consume_count(_db_rev_rows(pid))
        assert apply_n == 1
        assert consume_n0 == 0
        # apply after 已在账本
        assert len(_rows_by_version(_db_rev_rows(pid), after_apply)) == 1

        res = client.post(
            _consume_url(pid, batch_id),
            json={"expectedStateVersion": after_apply},
        )
        body = _assert_success_consume(res, restored=n, skipped=0)
        after_ver = body["_after_ver"]
        assert after_ver != after_apply

        state = _get_state(client, pid)
        assert state["stateVersion"] == after_ver
        assert after_ver == editor_state_service.compute_full_state_version(state)
        # 恢复到 seed 正文
        for c in chs:
            live = next(x for x in state["chapters"] if x["id"] == c["id"])
            assert live["body"] == c["body"]
            assert live["status"] == "pending"

        rows = _db_rev_rows(pid)
        assert len(rows) == n_before + 1, (
            f"n={n} 期望 n_before+1={n_before + 1}，实际 {len(rows)}: "
            f"{[(r.state_version, r.source_kind) for r in rows]}"
        )
        assert _consume_count(rows) == 1
        assert _apply_count(rows) == apply_n == 1
        after_row = _assert_consume_after(rows, after_ver)
        assert chs[0]["body"] in (after_row.snapshot_json or "")
        # apply 行保留
        apply_matched = [
            r for r in rows if r.source_kind == _SOURCE_APPLY and r.state_version == after_apply
        ]
        assert len(apply_matched) == 1

        # 其他项目零增量
        assert _db_rev_count(other) == other_n0
        assert _consume_count(_db_rev_rows(other)) == other_consume0 == 0
        assert _get_state(client, other)["stateVersion"] == other_v


def test_browser_put_partial_restore_exact_plus_one(client: TestClient):
    """用途：browser_put 漂移后部分恢复只精确 +1 consume；browser/apply 身份不变。"""
    pid = _create_project(client, name="部分恢复")
    chs = _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    apply_res = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_chap_a", "sug_chap_b"],
        expected=v0,
    )
    apply_body = _assert_success_apply(apply_res, applied=2)
    after_apply = apply_body["_after_ver"]
    batch_id = apply_body["batchId"]

    # 漂移 chap_b
    state_after = _get_state(client, pid)
    drifted = _put_state(
        client,
        pid,
        {
            "chapters": [
                next(c for c in state_after["chapters"] if c["id"] == "chap_a"),
                {
                    **(next(c for c in state_after["chapters"] if c["id"] == "chap_b")),
                    "body": f"外部漂移-不得恢复-{_SECRET}",
                    "status": "needs_review",
                },
            ],
            "expectedStateVersion": after_apply,
        },
    )
    v_drift = drifted["stateVersion"]
    rows_before = _db_rev_rows(pid)
    n_before = len(rows_before)
    ledger_before = _revision_identity_seq(rows_before)
    # consume 前保存 browser/apply 精确身份序列（禁止 subset / >=）
    browser_apply_before = _filtered_identity_seq(
        rows_before, {_SOURCE_BROWSER, _SOURCE_APPLY}
    )
    browser_only_before = _filtered_identity_seq(rows_before, {_SOURCE_BROWSER})
    apply_only_before = _filtered_identity_seq(rows_before, {_SOURCE_APPLY})
    browser_n_before = _source_count(rows_before, _SOURCE_BROWSER)
    apply_n_before = _apply_count(rows_before)
    assert apply_n_before == 1
    assert len(apply_only_before) == 1
    assert len(browser_only_before) == browser_n_before
    assert len(browser_apply_before) == browser_n_before + apply_n_before
    # 精确非空：seed 至少 1 条 browser（用精确计数等式，不用 >=）
    assert browser_n_before == len(
        [r for r in rows_before if r.source_kind == _SOURCE_BROWSER]
    )
    assert browser_n_before != 0
    assert _consume_count(rows_before) == 0

    res = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": v_drift},
    )
    body = _assert_success_consume(res, restored=1, skipped=1)
    after_ver = body["_after_ver"]
    assert after_ver != v_drift

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    bodies = {c["id"]: c["body"] for c in state["chapters"]}
    assert bodies["chap_a"] == chs[0]["body"]  # 恢复
    assert bodies["chap_b"] == f"外部漂移-不得恢复-{_SECRET}"  # 漂移保留

    rows = _db_rev_rows(pid)
    assert len(rows) == n_before + 1
    assert _consume_count(rows) == 1
    assert _apply_count(rows) == 1
    _assert_consume_after(rows, after_ver)
    # 按来源过滤后精确全等：旧 browser/apply 行未增删改（禁止 subset / >=）
    browser_apply_after = _filtered_identity_seq(
        rows, {_SOURCE_BROWSER, _SOURCE_APPLY}
    )
    assert browser_apply_after == browser_apply_before
    assert _filtered_identity_seq(rows, {_SOURCE_BROWSER}) == browser_only_before
    assert _filtered_identity_seq(rows, {_SOURCE_APPLY}) == apply_only_before
    # 账本总身份：before 逐条原样保留，且仅多 1 条 consume
    ledger_after = _revision_identity_seq(rows)
    assert len(ledger_after) == len(ledger_before) + 1
    for ident in ledger_before:
        assert ident in ledger_after
    consume_idents = [
        (rid, ver, src)
        for rid, ver, src in ledger_after
        if src == _SOURCE_CONSUME
    ]
    assert len(consume_idents) == 1
    assert consume_idents[0][1] == after_ver
    assert all(r.source_kind != _SOURCE_CHECKPOINT for r in rows)


def test_zero_restore_identity_sequence_unchanged(client: TestClient):
    """用途：零恢复批次 consumed、版本/13 键不变、revision 身份序列全等。"""
    pid = _create_project(client, name="零恢复身份全等")
    chs = _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    apply_res = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_chap_a", "sug_chap_b"],
        expected=v0,
    )
    apply_body = _assert_success_apply(apply_res, applied=2)
    after_apply = apply_body["_after_ver"]
    batch_id = apply_body["batchId"]

    # 双章漂移 → 零恢复
    state_after = _get_state(client, pid)
    drifted = _put_state(
        client,
        pid,
        {
            "chapters": [
                {
                    **(next(c for c in state_after["chapters"] if c["id"] == "chap_a")),
                    "body": "零恢复漂移A",
                    "status": "done",
                },
                {
                    **(next(c for c in state_after["chapters"] if c["id"] == "chap_b")),
                    "body": "零恢复漂移B",
                    "status": "done",
                },
            ],
            "expectedStateVersion": after_apply,
        },
    )
    v_zero = drifted["stateVersion"]
    rows_before = _db_rev_rows(pid)
    ledger_before = _revision_identity_seq(rows_before)
    n_before = len(rows_before)
    bodies0 = _db_chapter_bodies(pid)
    statuses0 = _db_chapter_statuses(pid)
    # 请求前完整 GET 字典（13 键 + updatedAt + stateVersion）
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == v_zero
    assert _consume_count(rows_before) == 0

    res = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": v_zero},
    )
    body = _assert_success_consume(res, restored=0, skipped=2)
    assert body["_after_ver"] == v_zero
    # 完整 GET 精确全等：13 键及 updatedAt 均不变
    assert _get_state(client, pid) == state0
    assert _db_chapter_bodies(pid) == bodies0
    assert _db_chapter_statuses(pid) == statuses0

    rows_after = _db_rev_rows(pid)
    assert len(rows_after) == n_before
    assert _revision_identity_seq(rows_after) == ledger_before
    assert _consume_count(rows_after) == 0
    assert _apply_count(rows_after) == 1
    batch = _db_batch(pid, batch_id)
    assert batch is not None
    assert batch.state == "consumed"
    assert batch.consumed_at is not None


def test_response_get_after_version_consistent(client: TestClient):
    """用途：响应 stateVersion 与 GET / after 行精确一致。"""
    pid = _create_project(client, name="版本一致-d2")
    batch_id, after_apply, _tid, _chs = _apply_then_get_batch(client, pid)
    res = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": after_apply},
    )
    body = _assert_success_consume(res, restored=2)
    after_ver = body["_after_ver"]
    got = _get_state(client, pid)
    assert got["stateVersion"] == after_ver
    assert after_ver == editor_state_service.compute_full_state_version(got)
    rows = _db_rev_rows(pid)
    after_row = _assert_consume_after(rows, after_ver)
    assert after_row.state_version == after_ver
    snap = json.loads(after_row.snapshot_json)
    assert isinstance(snap, dict)
    assert "chapters" in snap


# ---------- 失败零增量 ----------


def test_conflict_404_422_cross_scope_zero_consume_revision(client: TestClient):
    """用途：缺/坏 expected、陈旧、已消费、404、跨作用域均零 consume 修订。"""
    pid = _create_project(client, name="零增量矩阵-d2")
    other = _create_project(client, name="跨项目")
    chs = _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    apply_res = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_chap_a", "sug_chap_b"],
        expected=v0,
    )
    apply_body = _assert_success_apply(apply_res, applied=2)
    after_apply = apply_body["_after_ver"]
    batch_id = apply_body["batchId"]

    n0 = _db_rev_count(pid)
    consume0 = _consume_count(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)
    active0 = _db_active_batch_count(pid)
    assert consume0 == 0
    assert active0 == 1
    # 跨项目基线：请求前保存 other 身份 / 完整 GET / 批次计数（禁止恒真自比较）
    other_ledger0 = _revision_identity_seq(_db_rev_rows(other))
    other_state0 = _get_state(client, other)
    other_n0 = _db_rev_count(other)
    other_consume0 = _consume_count(_db_rev_rows(other))
    other_batch0 = _db_batch_count(other)
    other_active0 = _db_active_batch_count(other)

    def _assert_zero(label: str) -> None:
        assert _db_rev_count(pid) == n0, label
        assert _consume_count(_db_rev_rows(pid)) == 0, label
        assert _db_chapter_bodies(pid) == bodies0, label
        assert _db_active_batch_count(pid) == active0, label
        assert _get_state(client, pid)["stateVersion"] == after_apply, label

    def _assert_other_untouched(label: str) -> None:
        assert _db_rev_count(other) == other_n0, label
        assert _revision_identity_seq(_db_rev_rows(other)) == other_ledger0, label
        assert _get_state(client, other) == other_state0, label
        assert _consume_count(_db_rev_rows(other)) == other_consume0 == 0, label
        assert _db_batch_count(other) == other_batch0, label
        assert _db_active_batch_count(other) == other_active0, label

    # 422：缺 expected
    missing = client.post(_consume_url(pid, batch_id), json={})
    assert missing.status_code == 422, missing.text
    _assert_zero("缺 expected")

    # 422：坏格式
    bad = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": "not_a_version"},
    )
    assert bad.status_code == 422, bad.text
    _assert_zero("坏 expected")

    # 409：陈旧 CAS
    advanced = _put_state(
        client,
        pid,
        {
            "chapters": _get_state(client, pid)["chapters"],
            "facts": [{"id": "f_adv", "text": "推进"}],
            "expectedStateVersion": after_apply,
        },
    )
    v1 = advanced["stateVersion"]
    n1 = _db_rev_count(pid)
    consume1 = _consume_count(_db_rev_rows(pid))
    bodies1 = _db_chapter_bodies(pid)
    active1 = _db_active_batch_count(pid)
    stale = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": after_apply},
    )
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail.get("code") == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    assert detail.get("currentStateVersion") == v1
    assert _db_rev_count(pid) == n1
    assert _consume_count(_db_rev_rows(pid)) == consume1 == 0
    assert _db_chapter_bodies(pid) == bodies1
    assert _db_active_batch_count(pid) == active1 == 1

    # 对齐当前版本后继续失败矩阵
    v_cur = v1
    n_cur = n1
    bodies_cur = bodies1

    def _assert_zero_cur(label: str) -> None:
        assert _db_rev_count(pid) == n_cur, label
        assert _consume_count(_db_rev_rows(pid)) == 0, label
        assert _db_chapter_bodies(pid) == bodies_cur, label
        assert _db_active_batch_count(pid) == 1, label
        assert _get_state(client, pid)["stateVersion"] == v_cur, label

    # 404：批次不存在
    miss = client.post(
        _consume_url(pid, "cfab_missing_d2_should_not_echo"),
        json={"expectedStateVersion": v_cur},
    )
    _assert_fixed_error(
        miss, 404, "content_fuse_application_not_found", "cfab_missing_d2_should_not_echo"
    )
    _assert_zero_cur("缺批次")

    # 404：跨项目批次（other 请求前快照后逐项精确全等）
    cross = client.post(
        _consume_url(other, batch_id),
        json={"expectedStateVersion": other_state0["stateVersion"]},
    )
    _assert_fixed_error(cross, 404, "content_fuse_application_not_found", batch_id)
    _assert_zero_cur("跨项目")
    _assert_other_untouched("跨项目-other")

    # 404：项目不存在
    miss_proj = client.post(
        _consume_url("proj_does_not_exist_p12cbd2", batch_id),
        json={"expectedStateVersion": v_cur},
    )
    assert miss_proj.status_code == 404, miss_proj.text
    _assert_zero_cur("缺项目")
    _assert_other_untouched("缺项目-other")

    # 成功消费后再次 409 已消费 → 零增量
    ok = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": v_cur},
    )
    # 当前章是 apply after（facts 推进不改 chapters 的 after 匹配）
    # facts 变化后 chapters 仍是 after → 仍可完整恢复
    ok_body = _assert_success_consume(ok, restored=2)
    after_consume = ok_body["_after_ver"]
    n_after_ok = _db_rev_count(pid)
    consume_after_ok = _consume_count(_db_rev_rows(pid))
    assert consume_after_ok == 1
    assert n_after_ok == n_cur + 1

    again = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": after_consume},
    )
    _assert_fixed_error(again, 409, "content_fuse_application_consumed")
    assert _db_rev_count(pid) == n_after_ok
    assert _consume_count(_db_rev_rows(pid)) == 1
    assert _get_state(client, pid)["stateVersion"] == after_consume
    _assert_other_untouched("已消费后-other")


# ---------- 真实双并发 ----------


def test_concurrent_full_restore_one_success_one_409_single_consume(
    client: TestClient,
):
    """
    用途：完整恢复双并发恰好一胜一 full_state_version_conflict，
      胜者只留一条 consume after；禁止仅 status=409 宽泛集合假绿。
    """
    pid = _create_project(client, name="双并发完整恢复")
    batch_id, after_apply, _tid, _chs = _apply_then_get_batch(client, pid)
    n0 = _db_rev_count(pid)
    consume0 = _consume_count(_db_rev_rows(pid))
    assert consume0 == 0
    barrier = threading.Barrier(2)
    conflict_code = editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT

    def worker() -> tuple[int, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                content_fuse_application_service.consume_content_fuse_application(
                    db,
                    _WS,
                    pid,
                    batch_id,
                    expected_state_version=after_apply,
                )
                return (200, None)
            except editor_state_service.EditorStateVersionConflict:
                db.rollback()
                return (409, conflict_code)
            except content_fuse_application_service.ContentFuseApplicationError as exc:
                db.rollback()
                return (exc.status_code, exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    # 精确区分成功与 editor_state_version_conflict（CODE_FULL_STATE_VERSION_CONFLICT）
    assert sorted(outcomes, key=lambda x: (x[0], x[1] or "")) == sorted(
        [(200, None), (409, conflict_code)],
        key=lambda x: (x[0], x[1] or ""),
    ), outcomes
    assert outcomes.count((200, None)) == 1
    assert outcomes.count((409, conflict_code)) == 1
    # 禁止任意 409 码冒充（例如 content_fuse_application_consumed 不算本用例胜负）
    assert all(
        o == (200, None) or o == (409, conflict_code) for o in outcomes
    ), outcomes

    batch = _db_batch(pid, batch_id)
    assert batch is not None
    assert batch.state == "consumed"
    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _consume_count(rows) == 1
    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != after_apply
    _assert_consume_after(rows, after_ver)


def test_concurrent_zero_restore_one_success_one_409_zero_consume(
    client: TestClient,
):
    """
    用途：零恢复双并发一胜一 content_fuse_application_consumed；
      状态完整 GET 不变且 consume 修订为零；禁止仅 status=409 假绿。
    """
    pid = _create_project(client, name="双并发零恢复")
    chs = _default_chapters(2)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    apply_res = _apply(
        client,
        pid,
        task_id=tid,
        suggestion_ids=["sug_chap_a", "sug_chap_b"],
        expected=v0,
    )
    apply_body = _assert_success_apply(apply_res, applied=2)
    after_apply = apply_body["_after_ver"]
    batch_id = apply_body["batchId"]

    state_after = _get_state(client, pid)
    drifted = _put_state(
        client,
        pid,
        {
            "chapters": [
                {
                    **(next(c for c in state_after["chapters"] if c["id"] == "chap_a")),
                    "body": "并发零恢复A",
                    "status": "done",
                },
                {
                    **(next(c for c in state_after["chapters"] if c["id"] == "chap_b")),
                    "body": "并发零恢复B",
                    "status": "done",
                },
            ],
            "expectedStateVersion": after_apply,
        },
    )
    v_zero = drifted["stateVersion"]
    n0 = _db_rev_count(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)
    # 请求前完整 GET 字典
    state0 = _get_state(client, pid)
    assert state0["stateVersion"] == v_zero
    barrier = threading.Barrier(2)
    consumed_code = content_fuse_application_service.CODE_APP_CONSUMED

    def worker() -> tuple[int, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                content_fuse_application_service.consume_content_fuse_application(
                    db,
                    _WS,
                    pid,
                    batch_id,
                    expected_state_version=v_zero,
                )
                return (200, None)
            except editor_state_service.EditorStateVersionConflict:
                db.rollback()
                return (409, editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT)
            except content_fuse_application_service.ContentFuseApplicationError as exc:
                db.rollback()
                return (exc.status_code, exc.code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    # 精确区分成功与 content_fuse_application_consumed
    assert sorted(outcomes, key=lambda x: (x[0], x[1] or "")) == sorted(
        [(200, None), (409, consumed_code)],
        key=lambda x: (x[0], x[1] or ""),
    ), outcomes
    assert outcomes.count((200, None)) == 1
    assert outcomes.count((409, consumed_code)) == 1
    assert all(o == (200, None) or o == (409, consumed_code) for o in outcomes), outcomes

    batch = _db_batch(pid, batch_id)
    assert batch is not None
    assert batch.state == "consumed"
    # 完整 GET 精确全等：13 键及 updatedAt 均不变
    assert _get_state(client, pid) == state0
    assert _db_chapter_bodies(pid) == bodies0
    rows = _db_rev_rows(pid)
    assert len(rows) == n0
    assert _revision_identity_seq(rows) == ledger0
    assert _consume_count(rows) == 0


def test_cross_workspace_consume_404_zero_side_effects(client: TestClient):
    """
    用途：真实跨工作空间公开 HTTP 隔离——外空间技术标+批次存在，
      从 ws_local 请求其 consume 固定 404、不泄漏 ID；本/外空间 revision/状态/批次零变化。
      不得仅复用跨项目冒充跨空间。
    """
    # 本空间基线：真实 apply 批次
    pid_local = _create_project(client, name="本空间跨空间对照")
    batch_local, after_local, _tid, _chs = _apply_then_get_batch(client, pid_local)
    local_ledger0 = _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
    local_state0 = _get_state(client, pid_local)
    local_n0 = _db_rev_count(pid_local, workspace_id=_WS)
    local_batch0 = _db_batch_count(pid_local)
    local_active0 = _db_active_batch_count(pid_local)
    local_bodies0 = _db_chapter_bodies(pid_local)

    # 外空间：真实 Workspace + 技术项目 + 编辑态 + active 批次
    pid_other, bid_other, ver_other, other_state0 = (
        _seed_foreign_workspace_technical_with_batch()
    )
    other_ledger0 = _revision_identity_seq(
        _db_rev_rows(pid_other, workspace_id=_WS_OTHER)
    )
    other_n0 = _db_rev_count(pid_other, workspace_id=_WS_OTHER)
    other_batch0 = _db_batch_count(pid_other)
    other_active0 = _db_active_batch_count(pid_other)
    other_bodies0 = _db_chapter_bodies(pid_other)
    other_batch_row0 = _db_batch(pid_other, bid_other)
    assert other_batch_row0 is not None
    assert other_batch_row0.state == "active"
    assert other_batch_row0.workspace_id == _WS_OTHER
    assert other_active0 == 1
    assert ver_other == other_state0["stateVersion"]

    # 从 ws_local 请求外空间项目的 consume 路由
    res = client.post(
        _consume_url(pid_other, bid_other),
        json={"expectedStateVersion": ver_other},
        headers={"X-Workspace-Id": _WS},
    )
    # 固定 404 project_not_found；禁止泄漏外空间项目/批次 ID
    _assert_fixed_error(
        res,
        404,
        "project_not_found",
        pid_other,
        bid_other,
        _WS_OTHER,
        ver_other,
        _SECRET,
    )
    assert pid_other not in res.text
    assert bid_other not in res.text

    # 本空间精确零变化
    assert _db_rev_count(pid_local, workspace_id=_WS) == local_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
        == local_ledger0
    )
    assert _get_state(client, pid_local) == local_state0
    assert _db_batch_count(pid_local) == local_batch0
    assert _db_active_batch_count(pid_local) == local_active0
    assert _db_chapter_bodies(pid_local) == local_bodies0
    local_batch = _db_batch(pid_local, batch_local)
    assert local_batch is not None
    assert local_batch.state == "active"
    assert local_batch.consumed_at is None

    # 外空间精确零变化（revision / 状态 / 批次）
    assert _db_rev_count(pid_other, workspace_id=_WS_OTHER) == other_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_other, workspace_id=_WS_OTHER))
        == other_ledger0
    )
    assert _db_batch_count(pid_other) == other_batch0
    assert _db_active_batch_count(pid_other) == other_active0 == 1
    assert _db_chapter_bodies(pid_other) == other_bodies0
    other_batch_after = _db_batch(pid_other, bid_other)
    assert other_batch_after is not None
    assert other_batch_after.state == "active"
    assert other_batch_after.consumed_at is None
    assert other_batch_after.workspace_id == _WS_OTHER
    # 服务层再读外空间编辑态：完整 GET/editor-state 字典精确全等（13 键及派生字段）
    db = SessionLocal()
    try:
        other_state_after = editor_state_service.get_editor_state(
            db, _WS_OTHER, pid_other
        )
    finally:
        db.close()
    _assert_state_version(other_state_after["stateVersion"])
    assert other_state_after == other_state0


# ---------- 失败原子性 ----------


def test_recorder_flush_then_fail_full_rollback_and_retryable(
    client: TestClient, monkeypatch
):
    """用途：recorder flush 后注入失败 → 章节/批次/revision 全回滚；批次仍可重试。"""
    pid = _create_project(client, name="recorder注入-d2")
    batch_id, after_apply, _tid, chs = _apply_then_get_batch(client, pid)
    n0 = _db_rev_count(pid)
    consume0 = _consume_count(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)
    batch0 = _db_batch(pid, batch_id)
    assert batch0 is not None
    assert batch0.state == "active"

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        assert out["added_count"] == 1
        assert kwargs.get("source_kind") == _SOURCE_CONSUME
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )
    if hasattr(content_fuse_application_service, "editor_state_revision_service"):
        monkeypatch.setattr(
            content_fuse_application_service.editor_state_revision_service,
            "record_editor_state_transition",
            _record_then_boom,
        )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _consume_url(pid, batch_id),
            json={"expectedStateVersion": after_apply},
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_AFTER_FLUSH,
        pid,
        after_apply,
        batch_id,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _consume_count(_db_rev_rows(pid)) == consume0 == 0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid)["stateVersion"] == after_apply
    batch_after = _db_batch(pid, batch_id)
    assert batch_after is not None
    assert batch_after.state == "active"
    assert batch_after.consumed_at is None

    # 去掉注入后可重试成功
    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        real_record,
    )
    if hasattr(content_fuse_application_service, "editor_state_revision_service"):
        monkeypatch.setattr(
            content_fuse_application_service.editor_state_revision_service,
            "record_editor_state_transition",
            real_record,
        )
    retry = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": after_apply},
    )
    body = _assert_success_consume(retry, restored=2)
    after_ver = body["_after_ver"]
    assert _consume_count(_db_rev_rows(pid)) == 1
    _assert_consume_after(_db_rev_rows(pid), after_ver)
    assert _db_batch(pid, batch_id).state == "consumed"  # type: ignore[union-attr]
    for c in chs:
        assert _db_chapter_bodies(pid)[c["id"]] == c["body"]


def test_commit_failure_pending_flush_then_full_rollback(
    client: TestClient, monkeypatch
):
    """
    用途：同一 Session 在 commit 前精确证明 content_fuse_consume after 已 flush；
      commit 失败后固定 500、章节/批次/revision 全域回滚；批次可重试。
    """
    pid = _create_project(client, name="commit失败-d2")
    batch_id, after_apply, _tid, chs = _apply_then_get_batch(client, pid)
    n0 = _db_rev_count(pid)
    consume0 = _consume_count(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)

    commit_probe: dict = {
        "n": 0,
        "pending": None,
        "consume_pending": None,
        "source": None,
        "after_ver": None,
        "batch_state": None,
        "batch_consumed_at": None,
        "chapter_changed": None,
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
            consume_rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_CONSUME,
                )
                .all()
            )
            commit_probe["consume_pending"] = len(consume_rows)
            if consume_rows:
                commit_probe["source"] = consume_rows[-1].source_kind
                commit_probe["after_ver"] = consume_rows[-1].state_version
            batch_row = db.get(ContentFuseApplicationBatchRow, batch_id)
            assert batch_row is not None
            commit_probe["batch_state"] = batch_row.state
            commit_probe["batch_consumed_at"] = batch_row.consumed_at
            state_row = db.get(ProjectEditorStateRow, pid)
            assert state_row is not None
            live = json.loads(state_row.chapters_json or "[]")
            live_bodies = {
                i["id"]: i.get("body")
                for i in live
                if isinstance(i, dict) and type(i.get("id")) is str
            }
            # 完整恢复后正文应已回到 seed before
            commit_probe["chapter_changed"] = live_bodies != bodies0
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        db.commit = _bad_commit  # type: ignore[method-assign]
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
            _consume_url(pid, batch_id),
            json={"expectedStateVersion": after_apply},
        )

    assert commit_probe["n"] == 1
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert commit_probe["consume_pending"] == 1
    assert commit_probe["source"] == _SOURCE_CONSUME
    assert _assert_state_version(commit_probe["after_ver"]) != after_apply
    assert commit_probe["batch_state"] == "consumed"
    assert commit_probe["batch_consumed_at"] is not None
    assert commit_probe["chapter_changed"] is True

    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_COMMIT_FAIL,
        pid,
        after_apply,
        batch_id,
        "RuntimeError",
    )

    assert _db_rev_count(pid) == n0
    assert _consume_count(_db_rev_rows(pid)) == consume0 == 0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid)["stateVersion"] == after_apply
    batch_after = _db_batch(pid, batch_id)
    assert batch_after is not None
    assert batch_after.state == "active"
    assert batch_after.consumed_at is None

    # 去掉注入后可重试
    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    if hasattr(content_fuse_application_service, "editor_state_service"):
        monkeypatch.setattr(
            content_fuse_application_service.editor_state_service,
            "lock_and_assert_expected_state_version",
            real_lock,
        )
    retry = client.post(
        _consume_url(pid, batch_id),
        json={"expectedStateVersion": after_apply},
    )
    body = _assert_success_consume(retry, restored=2)
    assert _consume_count(_db_rev_rows(pid)) == 1
    _assert_consume_after(_db_rev_rows(pid), body["_after_ver"])
    for c in chs:
        assert _db_chapter_bodies(pid)[c["id"]] == c["body"]


def test_apply_still_only_records_apply_source(client: TestClient):
    """用途：D1 apply 仍只记录 content_fuse_apply；不误写 consume/checkpoint。"""
    pid = _create_project(client, name="apply来源隔离")
    chs = _default_chapters(1)
    base = _seed_chapters_via_browser(client, pid, chs)
    v0 = base["stateVersion"]
    sugs = _default_suggestions(chs)
    tid = _seed_success_task(pid, suggestions=sugs)
    res = _apply(client, pid, task_id=tid, suggestion_ids=["sug_chap_a"], expected=v0)
    body = _assert_success_apply(res, applied=1)
    rows = _db_rev_rows(pid)
    assert _apply_count(rows) == 1
    assert _consume_count(rows) == 0
    assert _source_count(rows, _SOURCE_CHECKPOINT) == 0
    matched = _rows_by_version(rows, body["_after_ver"])
    assert len(matched) == 1
    assert matched[0].source_kind == _SOURCE_APPLY
