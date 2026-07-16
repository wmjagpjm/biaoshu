"""
模块：P12C-B-C2 P8C 公开一次性票据 callback 修订账本原子接入专项测试
用途：真实 HTTP + SQLite 验收 local_parser 来源写入、stale/null 仅消费、
  recorder/commit 失败全域回滚可重试、客户端 source 隔离与个人 callback 不误接。
对接：POST /api/local-parser/callback；record_editor_state_transition。
二次开发：禁止 mock 掉 SQLite、>= 宽松增量、空集合、or True、固定 sleep、通用 500 假绿。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    EditorStateRevisionRow,
    LocalParserCallbackTicketRow,
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
_SOURCE_LOCAL_PARSER = "local_parser"
_SOURCE_BROWSER = "browser_put"
_SOURCE_CALLBACK = "callback"
_SECRET = "SECRET_P12CBC2_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbc2_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cbc2_injected_commit_failure"
_PUBLIC_CALLBACK = "/api/local-parser/callback"
_USER_ID = "user_p12cbc2_issuer"

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "local_parser_ticket_service.py"
)


def _create_project(
    client: TestClient, name: str = "P12C-B-C2", kind: str = "technical"
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


def _ensure_issuer_user() -> str:
    """用途：票据 FK 需要 local_users 行；服务层签发真实票据。"""
    db = SessionLocal()
    try:
        if db.get(LocalUserRow, _USER_ID) is None:
            db.add(
                LocalUserRow(
                    id=_USER_ID,
                    username="p12cbc2_issuer",
                    username_normalized="p12cbc2_issuer",
                    password_salt="00" * 16,
                    password_hash="11" * 32,
                    is_active=True,
                )
            )
            db.commit()
        return _USER_ID
    finally:
        db.close()


def _issue_ticket(project_id: str) -> str:
    _ensure_issuer_user()
    db = SessionLocal()
    try:
        issued = issue_callback_ticket(
            db,
            workspace_id=_WS,
            project_id=project_id,
            issued_by_user_id=_USER_ID,
        )
        return issued["ticket"]
    finally:
        db.close()


def _public_callback(
    client: TestClient,
    *,
    ticket: str,
    markdown: str = f"# 本地回传\n\n{_SECRET}",
    source: str = "mineru",
    filename: str | None = "ok.pdf",
):
    payload: dict = {"markdown": markdown, "source": source}
    if filename is not None:
        payload["filename"] = filename
    return client.post(
        _PUBLIC_CALLBACK,
        headers={"X-Local-Parse-Ticket": ticket},
        json=payload,
    )


def _assert_success_public_response(res) -> dict:
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"ok", "chars", "taskId"}
    assert body["ok"] is True
    assert isinstance(body.get("chars"), int) and body["chars"] > 0
    assert isinstance(body.get("taskId"), str) and body["taskId"]
    raw = res.text
    # 公开成功响应不得泄露版本/revision/内部字段
    assert "stateVersion" not in raw
    assert "state_version" not in raw
    assert "currentStateVersion" not in raw
    assert "revision" not in raw.lower()
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "sourceKind" not in raw
    assert "esv_" not in raw
    assert "esr_" not in raw
    assert "projectId" not in raw
    assert "project_id" not in raw
    assert "local_parser" not in raw
    return body


def _assert_sanitized_500(raw_text: str, inject_marker: str, *extra: str) -> None:
    """用途：强制 JSON 固定脱敏 500；generic 500/plain text 必须失败。"""
    blob = raw_text or ""
    low = blob.lower()
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
    assert detail["code"] == "local_parser_callback_failed"
    assert detail["message"] == "回传处理失败"
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


def _assert_fixed_409(res) -> None:
    assert res.status_code == 409, res.text
    detail = res.json().get("detail") or {}
    assert isinstance(detail, dict)
    assert detail.get("code") == "local_parser_state_version_conflict"
    assert detail.get("message") == "编辑内容已变化，请重新签发回传票据后重试"
    assert set(detail.keys()) == {"code", "message"}
    assert "currentStateVersion" not in res.text
    assert "stateVersion" not in res.text
    assert "revision" not in res.text.lower()
    assert _SECRET not in res.text


def _assert_fixed_401(res, *extra_leaks: str) -> None:
    """
    用途：固定 401；detail 字段集合精确 code/message；
      code=local_parser_ticket_invalid，message=回传票据无效或已失效。
      禁止 conditional dict 假绿；不泄漏票据/正文/revision/version。
    """
    assert res.status_code == 401, res.text
    try:
        payload = json.loads(res.text)
    except Exception as exc:
        raise AssertionError(
            f"401 响应必须是可解析 JSON，实际: {res.text[:200]!r}"
        ) from exc
    assert isinstance(payload, dict), (
        f"401 响应体须为 JSON 对象，实际: {type(payload)}"
    )
    detail = payload.get("detail")
    assert isinstance(detail, dict), (
        f"detail 必须精确为 dict，实际 type={type(detail).__name__} value={detail!r}"
    )
    assert set(detail.keys()) == {"code", "message"}, (
        f"detail 字段集合须精确为 code/message，实际 {set(detail.keys())}"
    )
    assert detail["code"] == "local_parser_ticket_invalid"
    assert detail["message"] == "回传票据无效或已失效"
    raw = res.text
    low = raw.lower()
    # 不泄漏版本 / revision / 内部来源
    assert "esv_" not in raw
    assert "esr_" not in raw
    assert "stateVersion" not in raw
    assert "state_version" not in raw
    assert "currentStateVersion" not in raw
    assert "revision" not in low
    assert "revisionSourceKind" not in raw
    assert "revision_source_kind" not in raw
    assert "sourceKind" not in raw
    # 注意：固定 code 字面量含 local_parser_ticket_invalid，不得误禁该子串
    assert _SECRET not in raw
    for m in extra_leaks:
        if m:
            assert m not in raw


def _db_rev_rows(project_id: str) -> list[EditorStateRevisionRow]:
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


def _local_parser_count(rows: list[EditorStateRevisionRow]) -> int:
    return sum(1 for r in rows if r.source_kind == _SOURCE_LOCAL_PARSER)


def _callback_count(rows: list[EditorStateRevisionRow]) -> int:
    return sum(1 for r in rows if r.source_kind == _SOURCE_CALLBACK)


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _assert_local_parser_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_LOCAL_PARSER
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


def _db_apply_audit_count() -> int:
    db = SessionLocal()
    try:
        return (
            db.query(AuthAuditEventRow)
            .filter(AuthAuditEventRow.action == "local_parser_callback_apply")
            .count()
        )
    finally:
        db.close()


def _ticket_consumed(raw_ticket: str) -> bool:
    digest = hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        row = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).first()
        assert row is not None
        return row.consumed_at is not None
    finally:
        db.close()


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


def test_local_parser_ast_records_literal_local_parser_source():
    """
    用途：AST 证明 _finalize_success_writes 内唯一 record 调用、字面量 local_parser，
      且不调用 upsert_editor_state；apply_one_time_callback 不直接 record。
    """
    fn = _find_function_def(_SERVICE_PATH, "_finalize_success_writes")
    assert fn is not None, "缺少 _finalize_success_writes"

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
        f"_finalize_success_writes 应有且仅有一次 record 调用，实际 {len(record_calls)}"
    )
    assert upsert_calls == [], "禁止调用 upsert_editor_state"
    src = _source_kind_literal_on_call(record_calls[0])
    assert src == _SOURCE_LOCAL_PARSER, (
        f"source_kind 必须是字面量 local_parser，实际 {src!r}"
    )

    apply_fn = _find_function_def(_SERVICE_PATH, "apply_one_time_callback")
    assert apply_fn is not None
    apply_records = [
        n
        for n in ast.walk(apply_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    assert apply_records == [], "apply_one_time_callback 不得直接调用 record"


# ---------- 成功路径：真实库 ----------


def test_empty_ledger_fresh_success_writes_before_and_after(client: TestClient):
    """用途：空账本 fresh 成功 → before+after 均 local_parser，与最终 GET 版本一致。"""
    pid = _create_project(client, name="空账本fresh")
    seed = _get_state(client, pid)
    v0 = _assert_state_version(seed["stateVersion"])
    assert _db_rev_count(pid) == 0
    assert _local_parser_count(_db_rev_rows(pid)) == 0
    before_proj = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    audits0 = _db_apply_audit_count()

    ticket = _issue_ticket(pid)
    res = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# 首次本地回传\n\n正文-{_SECRET}",
        filename="first.pdf",
        source="mineru",
    )
    _assert_success_public_response(res)

    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    assert "首次本地回传" in (state.get("parsedMarkdown") or "")
    assert after_ver == editor_state_service.compute_full_state_version(state)

    rows = _db_rev_rows(pid)
    # 空账本：before + after → 精确 2 条
    assert len(rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    assert {r.source_kind for r in rows} == {_SOURCE_LOCAL_PARSER}
    after_row = _assert_local_parser_after(rows, after_ver)
    assert after_row.workspace_id == _WS
    assert after_row.project_id == pid
    versions = {r.state_version for r in rows}
    assert len(versions) == 2
    assert after_ver in versions
    before_ver = next(v for v in versions if v != after_ver)
    assert _assert_state_version(before_ver) == v0
    assert "首次本地回传" in (after_row.snapshot_json or "")
    assert _SECRET in (after_row.snapshot_json or "")

    assert _db_success_parse_task_count(pid) == tasks0 + 1
    status, step, updated = _project_db_snapshot(pid)
    assert status == "analyzing"
    assert step == 1
    assert updated != before_proj[2]
    assert _db_apply_audit_count() == audits0 + 1
    assert _ticket_consumed(ticket) is True


def test_after_browser_put_appends_exact_one_local_parser_after(client: TestClient):
    """
    用途：已有 browser_put 基线后成功 → 精确 +1 local_parser after；
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
    other_put = _put_state(client, other, {"parsedMarkdown": "其他项目保持不变"})
    other_v = other_put["stateVersion"]
    other_n0 = _db_rev_count(other)
    other_lp0 = _local_parser_count(_db_rev_rows(other))
    assert other_lp0 == 0

    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    assert n0 >= 2
    assert lp0 == 0
    rows0 = _db_rev_rows(pid)
    browser_matched = _rows_by_version(rows0, v0)
    assert len(browser_matched) == 1
    assert browser_matched[0].source_kind == _SOURCE_BROWSER

    ticket = _issue_ticket(pid)
    res = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# local_parser追加\n\n{_SECRET}",
        source="docling",
        filename="append.pdf",
    )
    _assert_success_public_response(res)

    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    assert "local_parser追加" in (state.get("parsedMarkdown") or "")

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1, [(r.state_version, r.source_kind) for r in rows]
    assert _local_parser_count(rows) == lp0 + 1
    after_row = _assert_local_parser_after(rows, after_ver)
    assert "local_parser追加" in (after_row.snapshot_json or "")

    still_browser = _rows_by_version(rows, v0)
    assert len(still_browser) == 1
    assert still_browser[0].source_kind == _SOURCE_BROWSER
    assert all(
        r.source_kind != _SOURCE_LOCAL_PARSER or r.state_version == after_ver
        for r in rows
    )

    assert _db_rev_count(other) == other_n0
    assert _local_parser_count(_db_rev_rows(other)) == other_lp0 == 0
    other_state = _get_state(client, other)
    assert other_state["stateVersion"] == other_v
    assert other_state["parsedMarkdown"] == "其他项目保持不变"


def test_client_source_mineru_docling_cannot_control_revision_source(
    client: TestClient,
):
    """用途：客户端 source=mineru|docling 只影响解析元数据，revision 来源固定 local_parser。"""
    for src in ("mineru", "docling"):
        pid = _create_project(client, name=f"source隔离-{src}")
        v0 = _assert_state_version(_get_state(client, pid)["stateVersion"])
        n0 = _db_rev_count(pid)
        ticket = _issue_ticket(pid)
        res = _public_callback(
            client,
            ticket=ticket,
            markdown=f"# source={src}\n\n{_SECRET}",
            source=src,
            filename=f"{src}.pdf",
        )
        _assert_success_public_response(res)
        state = _get_state(client, pid)
        after_ver = _assert_state_version(state["stateVersion"])
        assert after_ver != v0
        # 解析元数据应体现客户端 source（写入 Markdown 前缀）
        assert f"来源：{src}" in (state.get("parsedMarkdown") or "")

        rows = _db_rev_rows(pid)
        assert len(rows) == n0 + 2  # 空账本 before+after
        after_row = _assert_local_parser_after(rows, after_ver)
        assert after_row.source_kind == _SOURCE_LOCAL_PARSER
        assert {r.source_kind for r in rows} == {_SOURCE_LOCAL_PARSER}
        # 客户端 source 不得成为内部 revision 来源
        assert all(r.source_kind != src for r in rows)
        assert all(r.source_kind != _SOURCE_BROWSER for r in rows)
        assert all(r.source_kind != _SOURCE_CALLBACK for r in rows)


# ---------- 非法/过期/重放/坏正文零修订 ----------


def test_invalid_expired_replay_bad_body_zero_local_parser_revision(
    client: TestClient,
):
    """用途：非法 source/正文、超限、无效/过期/重放票据均零 local_parser 修订。"""
    pid = _create_project(client, name="零修订矩阵")
    base = _put_state(client, pid, {"parsedMarkdown": "零修订基线"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    assert lp0 == 0
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    audits0 = _db_apply_audit_count()

    # 无效票据
    fake_ticket = "p12cbc2-fake-ticket-not-real"
    bad_ticket = client.post(
        _PUBLIC_CALLBACK,
        headers={"X-Local-Parse-Ticket": fake_ticket},
        json={"markdown": f"# 假票\n\n{_SECRET}", "source": "mineru"},
    )
    _assert_fixed_401(bad_ticket, fake_ticket, "假票")

    # 缺票
    no_ticket = client.post(
        _PUBLIC_CALLBACK,
        json={"markdown": f"# 缺票\n\n{_SECRET}", "source": "mineru"},
    )
    _assert_fixed_401(no_ticket, "缺票")

    # 非法 source
    ticket_bad_src = _issue_ticket(pid)
    bad_src = client.post(
        _PUBLIC_CALLBACK,
        headers={"X-Local-Parse-Ticket": ticket_bad_src},
        json={"markdown": f"# 坏源\n\n{_SECRET}", "source": "browser_put"},
    )
    assert bad_src.status_code == 400, bad_src.text
    assert _ticket_consumed(ticket_bad_src) is False

    # 空正文
    ticket_empty = _issue_ticket(pid)
    empty_md = client.post(
        _PUBLIC_CALLBACK,
        headers={"X-Local-Parse-Ticket": ticket_empty},
        json={"markdown": "   ", "source": "mineru"},
    )
    assert empty_md.status_code == 400, empty_md.text
    assert _ticket_consumed(ticket_empty) is False

    # 坏 JSON
    ticket_json = _issue_ticket(pid)
    bad_json = client.post(
        _PUBLIC_CALLBACK,
        headers={
            "X-Local-Parse-Ticket": ticket_json,
            "Content-Type": "application/json",
        },
        content=b"{not-json",
    )
    assert bad_json.status_code == 400, bad_json.text
    assert _ticket_consumed(ticket_json) is False

    # 过期
    ticket_exp = _issue_ticket(pid)
    digest_exp = hashlib.sha256(ticket_exp.encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        row = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest_exp
            )
        ).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()
    exp = _public_callback(
        client,
        ticket=ticket_exp,
        markdown=f"# 过期\n\n{_SECRET}",
        source="mineru",
    )
    _assert_fixed_401(exp, ticket_exp, "过期")

    # 成功一次后重放
    ticket_ok = _issue_ticket(pid)
    ok = _public_callback(
        client,
        ticket=ticket_ok,
        markdown=f"# 一次成功\n\n{_SECRET}",
        source="mineru",
        filename="once.pdf",
    )
    _assert_success_public_response(ok)
    n_after_ok = _db_rev_count(pid)
    lp_after_ok = _local_parser_count(_db_rev_rows(pid))
    assert lp_after_ok == lp0 + 1  # 有浏览器基线 → 仅 after
    assert n_after_ok == n0 + 1

    replay = _public_callback(
        client,
        ticket=ticket_ok,
        markdown=f"# 重放\n\n{_SECRET}",
        source="mineru",
    )
    _assert_fixed_401(replay, ticket_ok, "重放")

    # 重放不得再增
    assert _db_rev_count(pid) == n_after_ok
    assert _local_parser_count(_db_rev_rows(pid)) == lp_after_ok
    # 非法路径整体：除一次成功外，其它尝试不得额外增加 local_parser
    # 非法路径前基线保持业务字段合法（成功路径已推进版本）
    assert _db_success_parse_task_count(pid) == tasks0 + 1
    assert _db_apply_audit_count() == audits0 + 1
    state = _get_state(client, pid)
    assert "一次成功" in (state.get("parsedMarkdown") or "")
    assert state["stateVersion"] != v0
    # 坏路径不应留下假票正文
    assert "假票" not in (state.get("parsedMarkdown") or "")
    assert "坏源" not in (state.get("parsedMarkdown") or "")
    assert "过期" not in (state.get("parsedMarkdown") or "")
    assert "重放" not in (state.get("parsedMarkdown") or "")
    # 非法路径未成功前快照与成功后状态已不同，额外断言 md0 已被合法成功覆盖
    assert md0 != _db_editor_parsed_markdown(pid)
    assert proj0 != _project_db_snapshot(pid)


# ---------- stale / null：仅消费、零修订 ----------


def test_stale_409_consumes_ticket_zero_local_parser_and_business(
    client: TestClient,
):
    """
    用途：stale → 固定 409、票据已消费、重放 401；
      正文/任务/项目/成功审计/local_parser 修订精确零写；外部 browser_put 不计入本次增量。
    """
    pid = _create_project(client, name="陈旧409")
    seed = _put_state(client, pid, {"parsedMarkdown": "陈旧-A"})
    v0 = seed["stateVersion"]
    ticket = _issue_ticket(pid)

    # 签发后外部并发改写 → 形成 browser_put after
    advanced = _put_state(
        client,
        pid,
        {"parsedMarkdown": "陈旧-外部已改-B", "expectedStateVersion": v0},
    )
    v1 = advanced["stateVersion"]
    assert v1 != v0
    n1 = _db_rev_count(pid)
    lp1 = _local_parser_count(_db_rev_rows(pid))
    assert lp1 == 0
    browser_rows = _rows_by_version(_db_rev_rows(pid), v1)
    assert len(browser_rows) == 1
    assert browser_rows[0].source_kind == _SOURCE_BROWSER
    md1 = _db_editor_parsed_markdown(pid)
    proj1 = _project_db_snapshot(pid)
    tasks1 = _db_success_parse_task_count(pid)
    audits1 = _db_apply_audit_count()

    res = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# 迟到回传\n\n{_SECRET}",
        filename="late.pdf",
        source="mineru",
    )
    _assert_fixed_409(res)
    assert _ticket_consumed(ticket) is True

    # 精确零增量：外部 browser_put 不得被算成本次 local_parser
    assert _db_rev_count(pid) == n1
    assert _local_parser_count(_db_rev_rows(pid)) == lp1 == 0
    assert _db_editor_parsed_markdown(pid) == md1
    assert _project_db_snapshot(pid) == proj1
    assert _db_success_parse_task_count(pid) == tasks1
    assert _db_apply_audit_count() == audits1
    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "陈旧-外部已改-B"
    assert state["stateVersion"] == v1
    still_browser = _rows_by_version(_db_rev_rows(pid), v1)
    assert len(still_browser) == 1
    assert still_browser[0].source_kind == _SOURCE_BROWSER

    # stale 重放 401（固定 helper，禁止 conditional dict 假绿）
    again = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# 重放\n\n{_SECRET}",
        source="mineru",
    )
    _assert_fixed_401(again, ticket, "重放", "迟到回传")
    assert _db_rev_count(pid) == n1
    assert _local_parser_count(_db_rev_rows(pid)) == 0


def test_null_expected_version_409_consumes_zero_writes(client: TestClient):
    """用途：旧空版本票据 → 409、消费、业务/审计/local_parser 零写；重放 401。"""
    pid = _create_project(client, name="空版本票据")
    put = _put_state(client, pid, {"parsedMarkdown": "空版本基线-不得被写"})
    baseline = put["parsedMarkdown"]
    v0 = put["stateVersion"]
    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    audits0 = _db_apply_audit_count()

    ticket = _issue_ticket(pid)
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        row = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        row.expected_state_version = None
        db.commit()
    finally:
        db.close()

    res = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# null-ticket\n\n{_SECRET}",
        source="mineru",
    )
    _assert_fixed_409(res)
    assert _ticket_consumed(ticket) is True

    assert _db_rev_count(pid) == n0
    assert _local_parser_count(_db_rev_rows(pid)) == lp0 == 0
    assert _db_editor_parsed_markdown(pid) == baseline
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    assert _db_apply_audit_count() == audits0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert "null-ticket" not in (state.get("parsedMarkdown") or "")
    assert _SECRET not in (state.get("parsedMarkdown") or "")

    # null 重放 401（固定 helper）
    again = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# again\n\n{_SECRET}",
        source="mineru",
    )
    _assert_fixed_401(again, ticket, "again", "null-ticket")
    assert _local_parser_count(_db_rev_rows(pid)) == 0


# ---------- 失败原子性：recorder / commit ----------


def test_recorder_flush_then_fail_full_rollback_ticket_reusable(
    client: TestClient, monkeypatch
):
    """
    用途：recorder 真实 flush 后注入失败 → 固定 JSON 500；
      票据/正文/任务/项目/成功审计/revision 全域回滚；同票可重试且最终只留一次。
    """
    pid = _create_project(client, name="recorder注入回滚")
    base = _put_state(client, pid, {"parsedMarkdown": "稳定基线-recorder"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    audits0 = _db_apply_audit_count()

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        # 基线已有 browser_put → after 已存在 → 本轮精确只追加 1 条
        assert out["added_count"] == 1
        assert kwargs.get("source_kind") == _SOURCE_LOCAL_PARSER
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )

    ticket = _issue_ticket(pid)
    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _PUBLIC_CALLBACK,
            headers={"X-Local-Parse-Ticket": ticket},
            json={
                "markdown": f"# 应回滚\n\n{_SECRET}",
                "source": "mineru",
                "filename": "boom.pdf",
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
        ticket,
    )

    # 全域回滚：含票据可重用
    assert _ticket_consumed(ticket) is False
    assert _db_rev_count(pid) == n0
    assert _local_parser_count(_db_rev_rows(pid)) == lp0 == 0
    assert _db_editor_parsed_markdown(pid) == md0
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    assert _db_apply_audit_count() == audits0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["parsedMarkdown"] == "稳定基线-recorder"
    assert _SECRET not in (state.get("parsedMarkdown") or "")

    # 移除注入后同票可重试，且只成功留史一次
    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        real_record,
    )
    ok = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# 重试成功\n\n{_SECRET}",
        source="mineru",
        filename="retry.pdf",
    )
    _assert_success_public_response(ok)
    state2 = _get_state(client, pid)
    after_ver = _assert_state_version(state2["stateVersion"])
    assert after_ver != v0
    assert "重试成功" in (state2.get("parsedMarkdown") or "")
    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _local_parser_count(rows) == 1
    _assert_local_parser_after(rows, after_ver)
    assert _ticket_consumed(ticket) is True
    assert _db_apply_audit_count() == audits0 + 1


def test_commit_failure_pending_flush_then_full_rollback_ticket_reusable(
    client: TestClient, monkeypatch
):
    """
    用途：同一 Session 在 commit 前精确证明 local_parser after 已 flush；
      commit 失败后固定 500、全域回滚、同票可重试，最终只留一次。
    """
    pid = _create_project(client, name="commit失败回滚")
    base = _put_state(client, pid, {"parsedMarkdown": "稳定基线-commit"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)
    proj0 = _project_db_snapshot(pid)
    tasks0 = _db_success_parse_task_count(pid)
    audits0 = _db_apply_audit_count()
    assert lp0 == 0

    commit_probe = {"n": 0, "pending": None, "lp_pending": None}
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
            commit_probe["lp_pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_LOCAL_PARSER,
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

    ticket = _issue_ticket(pid)
    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _PUBLIC_CALLBACK,
            headers={"X-Local-Parse-Ticket": ticket},
            json={
                "markdown": f"# commit应回滚\n\n{_SECRET}",
                "source": "mineru",
                "filename": "commit-fail.pdf",
            },
        )

    assert commit_probe["n"] == 1
    # 账本已有浏览器 after=before，本轮只 flush 一条 local_parser after → n0+1
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应已 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert commit_probe["lp_pending"] == 1, (
        f"commit 前 local_parser 行应精确为 1，实际 {commit_probe['lp_pending']}"
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
        ticket,
    )

    assert _ticket_consumed(ticket) is False
    assert _db_rev_count(pid) == n0
    assert _local_parser_count(_db_rev_rows(pid)) == lp0 == 0
    assert _db_editor_parsed_markdown(pid) == md0
    assert _project_db_snapshot(pid) == proj0
    assert _db_success_parse_task_count(pid) == tasks0
    assert _db_apply_audit_count() == audits0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["parsedMarkdown"] == "稳定基线-commit"
    for r in _db_rev_rows(pid):
        assert _INJECT_COMMIT_FAIL not in (r.snapshot_json or "")
        assert _SECRET not in (r.snapshot_json or "")

    # 移除注入后同票重试，只留一次
    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    ok = _public_callback(
        client,
        ticket=ticket,
        markdown=f"# commit重试成功\n\n{_SECRET}",
        source="docling",
        filename="retry-commit.pdf",
    )
    _assert_success_public_response(ok)
    state2 = _get_state(client, pid)
    after_ver = _assert_state_version(state2["stateVersion"])
    assert after_ver != v0
    assert "commit重试成功" in (state2.get("parsedMarkdown") or "")
    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _local_parser_count(rows) == 1
    _assert_local_parser_after(rows, after_ver)
    assert _ticket_consumed(ticket) is True
    assert _db_apply_audit_count() == audits0 + 1


# ---------- 个人 callback 不误接 ----------


def test_personal_callback_still_only_callback_not_local_parser(client: TestClient):
    """用途：个人 callback 真实路由仍只产生 callback，不得被误记为 local_parser。"""
    pid = _create_project(client, name="个人callback隔离")
    base = _put_state(client, pid, {"parsedMarkdown": "个人基线"})
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)
    lp0 = _local_parser_count(_db_rev_rows(pid))
    cb0 = _callback_count(_db_rev_rows(pid))
    assert lp0 == 0
    assert cb0 == 0

    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": f"# 个人成功\n\n{_SECRET}",
            "source": "mineru",
            "filename": "personal.pdf",
            "expectedStateVersion": v0,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    after_ver = _assert_state_version(body["stateVersion"])
    assert after_ver != v0
    # 个人响应含既有字段，不得变成公开最小字段
    assert "stateVersion" in body
    assert "taskId" in body

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1  # 有浏览器基线 → 仅 after
    assert _callback_count(rows) == cb0 + 1
    assert _local_parser_count(rows) == 0
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1
    assert matched[0].source_kind == _SOURCE_CALLBACK
    assert all(r.source_kind != _SOURCE_LOCAL_PARSER for r in rows)
    state = _get_state(client, pid)
    assert "个人成功" in (state.get("parsedMarkdown") or "")
    assert state["stateVersion"] == after_ver
