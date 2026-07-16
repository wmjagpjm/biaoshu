"""
模块：P12C-B-C1 个人 callback 修订账本原子接入专项测试
用途：真实 SQLite 验收 callback 来源写入、401/422/409 零修订、
  recorder/commit 失败全域回滚、客户端 source 隔离与 P8C 不误接。
对接：POST /api/projects/{id}/parse-callback；record_editor_state_transition。
二次开发：禁止 mock 掉 SQLite、假定随机 ID=插入序、>= 宽松增量、or True、固定 sleep。
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    EditorStateRevisionRow,
    LocalUserRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
)
from app.services import editor_state_revision_service, editor_state_service
from app.services.local_parser_ticket_service import issue_callback_ticket

_WS = "ws_local"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SOURCE_CALLBACK = "callback"
_SOURCE_BROWSER = "browser_put"
_SOURCE_LOCAL_PARSER = "local_parser"
_SECRET = "SECRET_P12CBC1_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbc1_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cbc1_injected_commit_failure"

_PARSE_CALLBACK = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "parse_callback.py"
)


def _create_project(
    client: TestClient, name: str = "P12C-B-C1", kind: str = "technical"
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


def _assert_state_version(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _assert_no_revision_leak(blob: str, *extra: str) -> None:
    low = (blob or "").lower()
    assert "revision_source_kind" not in blob
    assert "revisionSourceKind" not in blob
    assert "sourceKind" not in blob
    assert "traceback" not in low
    assert "select " not in low
    assert "sqlite" not in low
    assert "sqlalchemy" not in low
    assert _SECRET not in blob
    for m in extra:
        if m:
            assert m not in blob


def _assert_sanitized_500(raw_text: str, inject_marker: str, *extra: str) -> None:
    """用途：强制 JSON 固定脱敏 500；generic 500/plain text 必须失败。"""
    blob = raw_text or ""
    low = blob.lower()
    # 必须可解析为 JSON；plain text / 非 JSON 500 不得静默放行
    try:
        payload = json.loads(blob)
    except Exception as exc:
        raise AssertionError(
            f"500 响应必须是可解析 JSON，实际: {blob[:200]!r}"
        ) from exc
    assert isinstance(payload, dict), f"500 响应体须为 JSON 对象，实际: {type(payload)}"
    detail = payload.get("detail")
    assert isinstance(detail, dict), (
        f"detail 必须精确为 dict，实际 type={type(detail).__name__} value={detail!r}"
    )
    assert set(detail.keys()) == {"code", "message"}, (
        f"detail 字段集合须精确为 code/message，实际 {set(detail.keys())}"
    )
    assert detail["code"] == "parse_callback_failed"
    assert detail["message"] == "回传处理失败"
    # 泄漏禁止：注入 marker/异常类名/版本/正文/内部来源键均不得回显
    assert inject_marker not in blob
    assert "RuntimeError" not in blob
    assert "Exception" not in blob
    assert "traceback" not in low
    assert "select " not in low
    assert "sqlite" not in low
    assert "sqlalchemy" not in low
    assert "revision_source_kind" not in blob
    assert "revisionSourceKind" not in blob
    assert "esv_" not in blob
    assert "esr_" not in blob
    assert _SECRET not in blob
    assert "editor_state_revisions" not in low
    for m in extra:
        if m:
            assert m not in blob


def _db_rev_rows(project_id: str) -> list[EditorStateRevisionRow]:
    """按契约序 created_at DESC, id DESC（最新在前）；定位 after 必须按 state_version。"""
    db = SessionLocal()
    try:
        return list(
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == project_id)
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
    finally:
        db.close()


def _db_rev_count(project_id: str) -> int:
    return len(_db_rev_rows(project_id))


def _callback_count(rows: list[EditorStateRevisionRow]) -> int:
    return sum(1 for r in rows if r.source_kind == _SOURCE_CALLBACK)


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _assert_callback_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_CALLBACK
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _db_editor_parsed_markdown(project_id: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.parsed_markdown
    finally:
        db.close()


def _project_db_snapshot(project_id: str) -> tuple:
    """用途：直接读库快照 Project.status / technical_plan_step / updated_at。"""
    db = SessionLocal()
    try:
        proj = db.get(Project, project_id)
        assert proj is not None
        return (proj.status, proj.technical_plan_step, proj.updated_at)
    finally:
        db.close()


def _db_success_parse_task_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ProjectTaskRow)
            .filter(
                ProjectTaskRow.project_id == project_id,
                ProjectTaskRow.type == "parse",
                ProjectTaskRow.status == "success",
            )
            .count()
        )
    finally:
        db.close()


def _post_personal_callback(
    client: TestClient,
    pid: str,
    *,
    expected: str,
    markdown: str = f"# 个人回传\n\n{_SECRET}",
    source: str = "mineru",
    filename: str | None = "ok.pdf",
    headers: dict | None = None,
):
    payload: dict = {
        "markdown": markdown,
        "source": source,
        "expectedStateVersion": expected,
    }
    if filename is not None:
        payload["filename"] = filename
    return client.post(
        f"/api/projects/{pid}/parse-callback",
        json=payload,
        headers=headers or {},
    )


def _call_func_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _find_function_def(
    path: Path, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """用途：定位模块顶层同步/异步函数定义。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _source_kind_literal_on_call(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg != "source_kind":
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
        return "<non-literal>"
    return None


# ---------- AST 补充（不替代库行为） ----------


def test_personal_callback_ast_records_literal_callback_source():
    """
    用途：AST 证明个人 parse_callback 函数内唯一 record 调用、字面量 callback，
      且不调用 upsert_editor_state。
    二次开发：不得只数源码字符串；不得替代真实 SQLite 行为测试。
    """
    fn = _find_function_def(_PARSE_CALLBACK, "parse_callback")
    assert fn is not None, "缺少 parse_callback 函数"

    record_calls: list[ast.Call] = []
    upsert_calls: list[ast.Call] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = _call_func_name(node)
        if name == "record_editor_state_transition":
            record_calls.append(node)
        if name == "upsert_editor_state":
            upsert_calls.append(node)

    assert len(record_calls) == 1, (
        f"个人 callback 应有且仅有一次 record 调用，实际 {len(record_calls)}"
    )
    assert upsert_calls == [], "禁止个人 callback 调用 upsert_editor_state"
    src = _source_kind_literal_on_call(record_calls[0])
    assert src == _SOURCE_CALLBACK, f"source_kind 必须是字面量 callback，实际 {src!r}"

    # 公共 P8C 路由与票据签发函数内不得出现 callback 记录（C2 未做）
    for other_name in ("local_parser_public_callback", "issue_parse_callback_ticket"):
        other = _find_function_def(_PARSE_CALLBACK, other_name)
        assert other is not None, other_name
        other_records = [
            n
            for n in ast.walk(other)
            if isinstance(n, ast.Call)
            and _call_func_name(n) == "record_editor_state_transition"
        ]
        assert other_records == [], f"{other_name} 不得在 C1 接入 record"


# ---------- 成功路径：真实库 ----------


def test_empty_ledger_success_writes_before_and_after_callback(client: TestClient):
    """用途：空账本首次成功 callback → before+after 均 callback，版本/响应/库一致。"""
    pid = _create_project(client, name="空账本首次callback")
    seed = _get_state(client, pid)
    v0 = _assert_state_version(seed["stateVersion"])
    assert _db_rev_count(pid) == 0
    assert _callback_count(_db_rev_rows(pid)) == 0
    before_proj = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)

    res = _post_personal_callback(
        client,
        pid,
        expected=v0,
        markdown=f"# 首次回传\n\n正文-{_SECRET}",
        filename="first.pdf",
        source="mineru",
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    after_ver = _assert_state_version(body["stateVersion"])
    assert after_ver != v0
    # 响应不得新增历史/来源字段，也不回显内部 revision 键
    raw = res.text
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "sourceKind" not in raw
    assert "esr_" not in raw
    assert set(body.keys()) == {
        "ok",
        "projectId",
        "chars",
        "source",
        "taskId",
        "stateVersion",
    }
    assert body["source"] == "mineru"
    assert body["projectId"] == pid
    # 内部 revision 来源键不得进入响应（既有 source 字段是客户端元数据）
    assert "revisionSourceKind" not in body
    assert "sourceKind" not in body

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert "首次回传" in (state.get("parsedMarkdown") or "")
    assert after_ver == editor_state_service.compute_full_state_version(state)

    rows = _db_rev_rows(pid)
    # 空账本：before + after → 精确 2 条，禁止随机 ID 推断先后
    assert len(rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_CALLBACK}
    after_row = _assert_callback_after(rows, after_ver)
    assert after_row.workspace_id == _WS
    assert after_row.project_id == pid
    versions = {r.state_version for r in rows}
    assert len(versions) == 2
    assert after_ver in versions
    before_ver = next(v for v in versions if v != after_ver)
    assert _assert_state_version(before_ver) == v0
    # snapshot 含 after 正文
    assert "首次回传" in (after_row.snapshot_json or "")
    assert _SECRET in (after_row.snapshot_json or "")

    # 成功任务与项目步骤真实写入
    assert _db_success_parse_task_count(pid) == tasks0 + 1
    status, step, updated = _project_db_snapshot(pid)
    assert status == "analyzing"
    assert step == 1
    assert updated != before_proj[2]


def test_after_browser_put_appends_exact_one_callback_after(client: TestClient):
    """
    用途：已有 browser_put 基线后成功 callback → 精确 +1 callback after；
      浏览器行保持 browser_put；其他项目零变化。
    """
    pid = _create_project(client, name="有浏览器基线")
    other = _create_project(client, name="其他项目隔离")
    base = _put_state(
        client,
        pid,
        {"parsedMarkdown": "浏览器基线正文", "facts": [{"id": "f0", "text": "基线"}]},
    )
    v0 = _assert_state_version(base["stateVersion"])
    other_put = _put_state(
        client, other, {"parsedMarkdown": "其他项目保持不变"}
    )
    other_v = other_put["stateVersion"]
    other_n0 = _db_rev_count(other)
    other_cb0 = _callback_count(_db_rev_rows(other))
    assert other_cb0 == 0

    n0 = _db_rev_count(pid)
    cb0 = _callback_count(_db_rev_rows(pid))
    assert n0 >= 2
    assert cb0 == 0
    # 浏览器 after 精确存在
    rows0 = _db_rev_rows(pid)
    browser_matched = _rows_by_version(rows0, v0)
    assert len(browser_matched) == 1
    assert browser_matched[0].source_kind == _SOURCE_BROWSER

    res = _post_personal_callback(
        client,
        pid,
        expected=v0,
        markdown=f"# callback追加\n\n{_SECRET}",
        source="docling",
        filename="append.pdf",
    )
    assert res.status_code == 200, res.text
    after_ver = _assert_state_version(res.json()["stateVersion"])
    assert after_ver != v0
    assert res.json()["source"] == "docling"

    rows = _db_rev_rows(pid)
    # 连续账本：before 已是最新 → 只追加 after → 精确 +1
    assert len(rows) == n0 + 1, [(r.state_version, r.source_kind) for r in rows]
    assert _callback_count(rows) == cb0 + 1
    after_row = _assert_callback_after(rows, after_ver)
    assert "callback追加" in (after_row.snapshot_json or "")

    # 既有浏览器行保持 browser_put，不得被改写成 callback
    still_browser = _rows_by_version(rows, v0)
    assert len(still_browser) == 1
    assert still_browser[0].source_kind == _SOURCE_BROWSER
    assert all(
        r.source_kind != _SOURCE_CALLBACK or r.state_version == after_ver for r in rows
    )

    # 其他项目精确零变化
    assert _db_rev_count(other) == other_n0
    assert _callback_count(_db_rev_rows(other)) == other_cb0 == 0
    other_state = _get_state(client, other)
    assert other_state["stateVersion"] == other_v
    assert other_state["parsedMarkdown"] == "其他项目保持不变"

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert "callback追加" in (state.get("parsedMarkdown") or "")


def test_client_source_cannot_control_revision_source(client: TestClient):
    """用途：客户端 source 任意值不影响内部 revision 来源，始终 callback。"""
    pid = _create_project(client, name="客户端source隔离")
    v0 = _assert_state_version(_get_state(client, pid)["stateVersion"])
    n0 = _db_rev_count(pid)

    # 故意投稿看起来像其他来源的字符串
    res = _post_personal_callback(
        client,
        pid,
        expected=v0,
        markdown=f"# source伪造\n\n{_SECRET}",
        source="browser_put",
        filename="spoof.pdf",
    )
    assert res.status_code == 200, res.text
    after_ver = _assert_state_version(res.json()["stateVersion"])
    # 响应回显客户端 source 元数据（既有字段），但不得变成内部 revision 字段
    assert res.json()["source"] == "browser_put"
    assert "revisionSourceKind" not in res.text
    assert "revision_source_kind" not in res.text

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 2  # 空账本 before+after
    after_row = _assert_callback_after(rows, after_ver)
    assert after_row.source_kind == _SOURCE_CALLBACK
    # 客户端投稿 "browser_put" 不得成为内部 revision 来源
    assert {r.source_kind for r in rows} == {_SOURCE_CALLBACK}
    assert all(r.source_kind != "browser_put" for r in rows)


# ---------- 401 / 422 / 409 零修订 ----------


def test_token_401_zero_callback_revision(client: TestClient, monkeypatch):
    """用途：配置 Token 后缺失/错误 → 401，callback 修订精确为 0。"""
    monkeypatch.setenv("LOCAL_PARSER_TOKEN", "p12cbc1-token-secret")
    get_settings.cache_clear()
    try:
        pid = _create_project(client, name="Token401")
        # 先用浏览器建基线（不需要 Token）
        base = _put_state(client, pid, {"parsedMarkdown": "token基线"})
        v0 = base["stateVersion"]
        n0 = _db_rev_count(pid)
        cb0 = _callback_count(_db_rev_rows(pid))
        md0 = _db_editor_parsed_markdown(pid)
        proj0 = _project_db_snapshot(pid)
        tasks0 = _db_success_parse_task_count(pid)

        payload = {
            "markdown": f"# 应拒绝\n\n{_SECRET}",
            "source": "mineru",
            "filename": "nope.pdf",
            "expectedStateVersion": v0,
        }
        no_header = client.post(f"/api/projects/{pid}/parse-callback", json=payload)
        assert no_header.status_code == 401, no_header.text
        _assert_no_revision_leak(no_header.text, _SECRET, "应拒绝")

        bad = client.post(
            f"/api/projects/{pid}/parse-callback",
            json=payload,
            headers={"X-Local-Token": "wrong-token"},
        )
        assert bad.status_code == 401, bad.text
        _assert_no_revision_leak(bad.text, _SECRET, "应拒绝")

        assert _db_rev_count(pid) == n0
        assert _callback_count(_db_rev_rows(pid)) == cb0 == 0
        assert _db_editor_parsed_markdown(pid) == md0
        assert _project_db_snapshot(pid) == proj0
        assert _db_success_parse_task_count(pid) == tasks0
    finally:
        monkeypatch.delenv("LOCAL_PARSER_TOKEN", raising=False)
        os.environ.pop("LOCAL_PARSER_TOKEN", None)
        get_settings.cache_clear()


def test_missing_and_invalid_expected_422_zero_callback(client: TestClient):
    """用途：缺失/非法 expected → 422，callback 修订精确为 0。"""
    pid = _create_project(client, name="422零修订")
    seed = _put_state(client, pid, {"parsedMarkdown": "422基线"})
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    cb0 = _callback_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)

    missing = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# 缺版本\n\nNO_WRITE_422",
            "source": "mineru",
            "filename": "x.pdf",
        },
    )
    assert missing.status_code == 422, missing.text
    # 422 校验阶段零写；不要求校验错误体脱敏（Pydantic 会回显字段路径）
    assert "revision_source_kind" not in missing.text
    assert "revisionSourceKind" not in missing.text

    for bad in ("esv_SHORT", "ESV_" + "a" * 32, "esv_" + "A" * 32, "", "not-a-version"):
        res = client.post(
            f"/api/projects/{pid}/parse-callback",
            json={
                "markdown": "# 非法\n\nNO_WRITE_422",
                "source": "mineru",
                "expectedStateVersion": bad,
            },
        )
        assert res.status_code == 422, f"{bad!r}: {res.text}"
        assert "revision_source_kind" not in res.text

    assert _db_rev_count(pid) == n0
    assert _callback_count(_db_rev_rows(pid)) == cb0 == 0
    assert _db_editor_parsed_markdown(pid) == md0
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert "NO_WRITE_422" not in (state.get("parsedMarkdown") or "")


def test_stale_409_zero_callback_revision(client: TestClient):
    """用途：陈旧 expected → 409 + currentStateVersion，callback 增量精确为 0。"""
    pid = _create_project(client, name="陈旧409")
    seed = _put_state(client, pid, {"parsedMarkdown": "陈旧-A"})
    v0 = seed["stateVersion"]
    advanced = _put_state(
        client,
        pid,
        {"parsedMarkdown": "陈旧-外部已改-B", "expectedStateVersion": v0},
    )
    v1 = advanced["stateVersion"]
    assert v1 != v0
    n1 = _db_rev_count(pid)
    cb1 = _callback_count(_db_rev_rows(pid))
    assert cb1 == 0
    md1 = _db_editor_parsed_markdown(pid)
    proj1 = _project_db_snapshot(pid)
    tasks1 = _db_success_parse_task_count(pid)

    res = _post_personal_callback(
        client,
        pid,
        expected=v0,
        markdown=f"# 迟到回传\n\n{_SECRET}",
        filename="late.pdf",
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT
    assert detail["message"] == editor_state_service.MSG_FULL_STATE_VERSION_CONFLICT
    assert detail["currentStateVersion"] == v1
    assert set(detail.keys()) == {"code", "message", "currentStateVersion"}
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert _SECRET not in blob
    assert "迟到回传" not in blob
    assert "late.pdf" not in blob
    assert "revision_source_kind" not in blob

    assert _db_rev_count(pid) == n1
    assert _callback_count(_db_rev_rows(pid)) == cb1 == 0
    assert _db_editor_parsed_markdown(pid) == md1
    assert _project_db_snapshot(pid) == proj1
    assert _db_success_parse_task_count(pid) == tasks1
    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "陈旧-外部已改-B"
    assert state["stateVersion"] == v1


# ---------- 失败原子性 ----------


def test_recorder_flush_then_fail_full_rollback(client: TestClient, monkeypatch):
    """
    用途：recorder 真实 flush 后注入失败 → 脱敏 500；
      editor-state / 成功任务 / 项目步骤 / revision 全域回滚。
    """
    pid = _create_project(client, name="recorder注入回滚")
    base = _put_state(client, pid, {"parsedMarkdown": "稳定基线-recorder"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    cb0 = _callback_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        # 必须先真实调用原语完成 flush
        out = real_record(*args, **kwargs)
        assert out["added_count"] >= 1
        # source_kind 为 keyword-only；必须精确为服务端字面量 callback
        assert kwargs.get("source_kind") == _SOURCE_CALLBACK
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            f"/api/projects/{pid}/parse-callback",
            json={
                "markdown": f"# 应回滚\n\n{_SECRET}",
                "source": "mineru",
                "filename": "boom.pdf",
                "expectedStateVersion": v0,
            },
        )

    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_AFTER_FLUSH,
        "应回滚",
        "boom.pdf",
        pid,
        v0,
        "source_kind",
        "revision_source_kind",
    )
    # 既有错误码 parse_callback_failed 含 callback 子串，属契约固定文案；不得因此放宽

    # 全域回滚
    assert _db_rev_count(pid) == n0
    assert _callback_count(_db_rev_rows(pid)) == cb0 == 0
    assert _db_editor_parsed_markdown(pid) == md0
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["parsedMarkdown"] == "稳定基线-recorder"
    assert _SECRET not in (state.get("parsedMarkdown") or "")


def test_commit_failure_pending_flush_then_full_rollback(
    client: TestClient, monkeypatch
):
    """
    用途：同一 Session 在 commit 前精确证明 callback after 已 flush；
      commit 失败后脱敏 500 并全域回滚。
    """
    pid = _create_project(client, name="commit失败回滚")
    base = _put_state(client, pid, {"parsedMarkdown": "稳定基线-commit"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    cb0 = _callback_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    assert cb0 == 0

    # 在锁后同一 Session 上劫持 commit/rollback，避免类级 patch 污染其他请求
    commit_probe = {"n": 0, "pending": None, "callback_pending": None}
    rollbacks = {"n": 0}
    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm_commit(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*a, **k):
            # 禁止在注入内 assert：否则未接 revision 时 AssertionError 被吞造成假绿
            commit_probe["n"] += 1
            commit_probe["pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            commit_probe["callback_pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_CALLBACK,
                )
                .count()
            )
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

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            f"/api/projects/{pid}/parse-callback",
            json={
                "markdown": f"# commit应回滚\n\n{_SECRET}",
                "source": "mineru",
                "filename": "commit-fail.pdf",
                "expectedStateVersion": v0,
            },
        )

    assert commit_probe["n"] == 1
    # 账本已有浏览器 after=before，本轮只 flush 一条 callback after → n0+1
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应已 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert commit_probe["callback_pending"] == 1, (
        f"commit 前 callback 行应精确为 1，实际 {commit_probe['callback_pending']}"
    )
    assert rollbacks["n"] >= 1
    assert res.status_code == 500, res.text
    _assert_sanitized_500(
        res.text,
        _INJECT_COMMIT_FAIL,
        "commit应回滚",
        "commit-fail.pdf",
        pid,
        v0,
        "source_kind",
        "revision_source_kind",
    )

    # 回滚后全域零写
    assert _db_rev_count(pid) == n0
    assert _callback_count(_db_rev_rows(pid)) == cb0 == 0
    assert _db_editor_parsed_markdown(pid) == md0
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["parsedMarkdown"] == "稳定基线-commit"
    for r in _db_rev_rows(pid):
        assert _INJECT_COMMIT_FAIL not in (r.snapshot_json or "")
        assert _SECRET not in (r.snapshot_json or "")


# ---------- P8C 不误接 ----------


def test_p8c_public_callback_does_not_record_callback_source(client: TestClient):
    """
    用途：P8C 真实公开路由绝不产生 source=callback 修订。
      C2 已接入 local_parser：成功后精确 +1 local_parser after，且 state_version=最终版本。
    二次开发：服务层签发票据可接受；消费必须走 TestClient POST /api/local-parser/callback。
      禁止只删旧断言或改成宽松 >=。
    """
    pid = _create_project(client, name="P8C不误接")
    base = _put_state(client, pid, {"parsedMarkdown": "P8C基线"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    cb0 = _callback_count(_db_rev_rows(pid))
    assert cb0 == 0
    tasks0 = _db_success_parse_task_count(pid)
    # 既有 browser_put 基线必须保留
    rows0 = _db_rev_rows(pid)
    browser0 = _rows_by_version(rows0, v0)
    assert len(browser0) == 1
    assert browser0[0].source_kind == _SOURCE_BROWSER
    browser0_id = browser0[0].id

    db = SessionLocal()
    try:
        # 票据 FK 需要真实 local_users 行；服务层签发真实票据可接受
        user_id = "user_p12cbc1_p8c"
        if db.get(LocalUserRow, user_id) is None:
            db.add(
                LocalUserRow(
                    id=user_id,
                    username="p12cbc1_p8c",
                    username_normalized="p12cbc1_p8c",
                    password_salt="00" * 16,
                    password_hash="11" * 32,
                    is_active=True,
                )
            )
            db.commit()
        issued = issue_callback_ticket(
            db,
            workspace_id=_WS,
            project_id=pid,
            issued_by_user_id=user_id,
        )
        ticket = issued["ticket"]
    finally:
        db.close()

    # 消费必须走真实公开路由，禁止直接调用 apply_one_time_callback
    res = client.post(
        "/api/local-parser/callback",
        headers={"X-Local-Parse-Ticket": ticket},
        json={
            "markdown": f"# P8C成功\n\n{_SECRET}",
            "source": "mineru",
            "filename": "p8c.pdf",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 公开响应既有最小字段，不得含版本/revision 字段
    assert set(body.keys()) == {"ok", "chars", "taskId"}
    assert body["ok"] is True
    assert isinstance(body.get("taskId"), str) and body["taskId"]
    assert isinstance(body.get("chars"), int) and body["chars"] > 0
    raw = res.text
    assert "stateVersion" not in raw
    assert "state_version" not in raw
    assert "revision" not in raw.lower()
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "sourceKind" not in raw
    assert "esv_" not in raw
    assert "esr_" not in raw
    assert "projectId" not in raw
    assert "project_id" not in raw

    # 成功后先取得最终 stateVersion，再断言账本
    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    assert "P8C成功" in (state.get("parsedMarkdown") or "")
    assert _SECRET in (state.get("parsedMarkdown") or "")

    rows = _db_rev_rows(pid)
    # P8C 绝不产生 callback（个人 callback 来源隔离）
    assert _callback_count(rows) == 0
    assert all(r.source_kind != _SOURCE_CALLBACK for r in rows)
    # C2 已接 local_parser：修订总数精确 n0+1（有浏览器基线 → 仅 after）
    assert len(rows) == n0 + 1, [(r.state_version, r.source_kind) for r in rows]
    lp_rows = [r for r in rows if r.source_kind == _SOURCE_LOCAL_PARSER]
    assert len(lp_rows) == 1, [(r.state_version, r.source_kind) for r in rows]
    assert lp_rows[0].state_version == after_ver
    assert _REVISION_ID_RE.fullmatch(lp_rows[0].id)

    # 既有 browser_put 基线不变
    still_browser = _rows_by_version(rows, v0)
    assert len(still_browser) == 1
    assert still_browser[0].source_kind == _SOURCE_BROWSER
    assert still_browser[0].id == browser0_id

    # 真实 SQLite：editor-state 与成功 parse 任务确实写入
    assert _db_success_parse_task_count(pid) == tasks0 + 1
    assert _db_editor_parsed_markdown(pid) is not None
    assert "P8C成功" in (_db_editor_parsed_markdown(pid) or "")
