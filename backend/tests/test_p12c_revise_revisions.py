"""
模块：P12C-B-B2 商务 revise 修订账本接入专项测试
用途：真实 SQLite 验收 revise 来源写入、零变化 200、stale/并发 409、
  recorder/commit 失败双零、浏览器/task/无来源隔离。
对接：revise_service → upsert_editor_state(revision_source_kind="revise")。
二次开发：禁止 mock 掉 SQLite、假定随机 ID=插入序、仅静态字符串假绿。
"""

from __future__ import annotations

import ast
import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    EditorStateRevisionRow,
    ProjectEditorStateRow,
)
from app.services import (
    editor_state_revision_service,
    editor_state_service,
    llm_service,
    revise_service,
)
from app.services.llm_service import ChatResult

_WS = "ws_local"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SOURCE_REVISE = "revise"
_SOURCE_BROWSER = "browser_put"
_SOURCE_TASK = "task"
_SECRET = "SECRET_P12CBB2_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cbb2_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cbb2_injected_commit_failure"

_REVISE_SERVICE = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "revise_service.py"
)

_STRUCT_STAGES = (
    "business_qualify",
    "business_toc",
    "business_quote",
    "business_commit",
)
_ALL_WRITE_STAGES = _STRUCT_STAGES + ("business_parse",)


def _create_project(
    client: TestClient, name: str = "P12C-B-B2", kind: str = "business"
) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


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


def _assert_no_source_leak(blob: str) -> None:
    assert "revision_source_kind" not in blob
    assert "revisionSourceKind" not in blob


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


def _db_editor_qualify_marker(project_id: str) -> str | None:
    """新 Session 读库，避免同身份映射假绿。"""
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.business_json
    finally:
        db.close()


def _db_editor_parsed_markdown(project_id: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.parsed_markdown
    finally:
        db.close()


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _assert_revise_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_REVISE
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _revise_count(rows: list[EditorStateRevisionRow]) -> int:
    return sum(1 for r in rows if r.source_kind == _SOURCE_REVISE)


def _call_func_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _collect_upsert_source_literals(path: Path) -> list[tuple[int, str]]:
    """
    用途：AST 收集 upsert_editor_state 的 revision_source_kind 字面量。
    二次开发：禁止只数源码字符串；必须来自 Call 关键字参数。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_func_name(node) != "upsert_editor_state":
            continue
        source_val: str | None = None
        for kw in node.keywords:
            if kw.arg != "revision_source_kind":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                source_val = kw.value.value
            else:
                source_val = "<non-literal>"
        found.append((getattr(node, "lineno", -1), source_val or "<missing>"))
    return found


def _assert_sanitized_http_500(raw_text: str, inject_marker: str, *extra: str) -> None:
    """用途：真实 HTTP 500 脱敏：无注入 marker/异常类名/SQL/版本/正文/来源内部键。"""
    blob = raw_text or ""
    low = blob.lower()
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
    assert _SECRET not in blob
    for m in extra:
        if m:
            assert m not in blob


def _seed_qualify(client: TestClient, pid: str, requirement: str = "法人") -> dict:
    return _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": requirement,
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ],
            "parsedMarkdown": f"## 资格\n{requirement}\n{_SECRET}",
        },
    )


def _qualify_revised_items(
    requirement: str = "独立法人（修订成功）",
) -> list[dict]:
    return [
        {
            "id": "q1",
            "requirement": requirement,
            "response": "我司具备",
            "evidence": "执照.pdf",
            "status": "matched",
        }
    ]


def _mock_struct_llm(monkeypatch, items: list[dict], summary: str = "已按意见修订。") -> None:
    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content=f"{summary}\n\n" + json.dumps(items, ensure_ascii=False),
            model="mock-p12cbb2",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)


def _mock_parse_llm(monkeypatch, body: str, summary: str = "已修订解析。") -> None:
    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content=f"{summary}\n\n{body}",
            model="mock-p12cbb2",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)


def _post_revise(
    client: TestClient,
    pid: str,
    *,
    stage: str,
    expected: str | None,
    message: str = "强化",
    base_content: str = "[]",
    artifact_id: str = "workspace",
):
    payload: dict = {
        "stage": stage,
        "message": message,
        "preserveStructure": True,
        "baseContent": base_content,
    }
    if expected is not None:
        payload["expectedStateVersion"] = expected
    return client.post(
        f"/api/projects/{pid}/artifacts/{artifact_id}/revise",
        json=payload,
    )


# ---------- AST 调用集合（补充，不替代库行为） ----------


def test_two_upsert_write_points_pass_literal_revise_source():
    """
    用途：AST 证明两个真实 upsert 写点均固定字面量 revise；覆盖五类商务阶段。
    二次开发：不得只数源码字符串；不得替代真实库行为测试。
    """
    literals = _collect_upsert_source_literals(_REVISE_SERVICE)
    assert len(literals) == 2, literals
    assert all(src == _SOURCE_REVISE for _ln, src in literals), literals

    # 运行时集合：四类结构化 + business_parse
    assert set(revise_service.BUSINESS_STRUCT_STAGES) == set(_STRUCT_STAGES)
    assert set(revise_service.BUSINESS_WRITE_STAGES) == set(_ALL_WRITE_STAGES)

    src = _REVISE_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 结构化分支与 business_parse 分支各有一个 upsert（按行序）
    struct_lines = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and _call_func_name(n) == "upsert_editor_state"
    ]
    assert len(struct_lines) == 2
    # 源码中 BUSINESS_STRUCT_STAGES 与 business_parse 均存在写回路径
    assert "BUSINESS_STRUCT_STAGES" in src
    assert 'stage == "business_parse"' in src or "stage == 'business_parse'" in src


# ---------- 成功路径：真实库 ----------


def test_business_parse_success_records_exact_one_revise(
    client: TestClient, monkeypatch
):
    """用途：business_parse 成功写回 → 精确 +1 revise after，版本/正文/snapshot 一致。"""
    pid = _create_project(client, name="parse成功")
    seed = _put_state(
        client, pid, {"parsedMarkdown": f"## 原解析\n{_SECRET}"}
    )
    v0 = _assert_state_version(seed["stateVersion"])
    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))
    assert revise_n0 == 0

    new_md = f"## 修订后解析\n条款已更新-{_SECRET[-8:]}"
    _mock_parse_llm(monkeypatch, new_md)

    res = _post_revise(
        client,
        pid,
        stage="business_parse",
        expected=v0,
        message="更新条款",
        base_content=seed["parsedMarkdown"],
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "applied"
    after_ver = _assert_state_version(data.get("stateVersion"))
    assert after_ver != v0
    _assert_no_source_leak(res.text)
    assert "esr_" not in res.text

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert state["parsedMarkdown"] == new_md.strip()

    rows = _db_rev_rows(pid)
    after_row = _assert_revise_after(rows, after_ver)
    assert after_row.project_id == pid
    assert after_row.workspace_id == _WS
    # snapshot 为规范 JSON：换行已转义，按字段解析比对
    snap = json.loads(after_row.snapshot_json or "{}")
    assert snap.get("parsedMarkdown") == new_md.strip()
    assert _revise_count(rows) == revise_n0 + 1
    assert _db_rev_count(pid) == n0 + 1
    # 浏览器基线仍在
    assert any(r.source_kind == _SOURCE_BROWSER for r in rows)


def test_business_qualify_success_records_exact_one_revise(
    client: TestClient, monkeypatch
):
    """用途：结构化商务阶段成功写回 → 精确 +1 revise after，字段与 snapshot 一致。"""
    pid = _create_project(client, name="qualify成功")
    seed = _seed_qualify(client, pid, requirement="法人")
    v0 = _assert_state_version(seed["stateVersion"])
    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))

    items = _qualify_revised_items("独立法人（修订成功）")
    _mock_struct_llm(monkeypatch, items)

    res = _post_revise(
        client,
        pid,
        stage="business_qualify",
        expected=v0,
        message="强化法人响应",
        base_content=json.dumps(seed["businessQualify"], ensure_ascii=False),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "applied"
    after_ver = _assert_state_version(data.get("stateVersion"))
    assert after_ver != v0

    state = _get_state(client, pid)
    assert state["stateVersion"] == after_ver
    assert "修订成功" in state["businessQualify"][0]["requirement"]

    rows = _db_rev_rows(pid)
    after_row = _assert_revise_after(rows, after_ver)
    assert "修订成功" in (after_row.snapshot_json or "")
    assert _revise_count(rows) == revise_n0 + 1
    assert _db_rev_count(pid) == n0 + 1


# ---------- 零变化 / 技术 revise ----------


def test_struct_parse_fail_zero_revise_keeps_version(
    client: TestClient, monkeypatch
):
    """用途：结构解析失败 → 200 只校验版本，editor-state/revision 精确零增量。"""
    pid = _create_project(client, name="解析失败零修订")
    seed = _seed_qualify(client, pid, requirement="法人-解析失败场景")
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))
    biz0 = _db_editor_qualify_marker(pid)

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content="仅摘要，无合法 JSON。",
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    res = _post_revise(
        client,
        pid,
        stage="business_qualify",
        expected=v0,
        message="强化",
        base_content="[]",
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "applied"
    sv = _assert_state_version(data.get("stateVersion"))
    assert sv == v0

    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["businessQualify"][0]["requirement"] == "法人-解析失败场景"
    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0
    assert _db_editor_qualify_marker(pid) == biz0


def test_empty_revised_business_parse_zero_revise(
    client: TestClient, monkeypatch
):
    """用途：business_parse 无 revised 正文 → 200 零迁移、零修订。"""
    pid = _create_project(client, name="空正文零修订")
    seed = _put_state(client, pid, {"parsedMarkdown": "原解析保留"})
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))
    md0 = _db_editor_parsed_markdown(pid)

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        # has_base=True 但拆不出正文时：仅摘要一段
        return SimpleNamespace(content="仅摘要无可写正文", model="mock")

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    # 无 baseContent → has_base=False → revised=None → 走只校验版本
    res = _post_revise(
        client,
        pid,
        stage="business_parse",
        expected=v0,
        message="只给方向",
        base_content="",
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert _assert_state_version(data.get("stateVersion")) == v0
    assert _get_state(client, pid)["parsedMarkdown"] == "原解析保留"
    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0
    assert _db_editor_parsed_markdown(pid) == md0


def test_technical_revise_zero_revision(client: TestClient, monkeypatch):
    """用途：普通技术 revise 不写 editor-state，revision 精确零增量。"""
    pid = _create_project(client, name="技术修订", kind="technical")
    seed = _put_state(client, pid, {"parsedMarkdown": "技术基线"})
    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))
    v0 = seed["stateVersion"]

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content="建议补充双活架构说明。",
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    res = _post_revise(
        client,
        pid,
        stage="outline",
        expected=None,
        message="补充架构",
        base_content="",
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "applied"
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0 == 0


# ---------- stale / 并发漂移 ----------


def test_stale_expected_409_zero_revise_no_body(
    client: TestClient, monkeypatch
):
    """用途：陈旧 expected → 409，模型正文不回显，revise 零增量。"""
    pid = _create_project(client, name="陈旧409")
    seed = _seed_qualify(client, pid)
    v0 = seed["stateVersion"]
    mid = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人-外部已改",
                    "response": "有",
                    "evidence": "",
                    "status": "matched",
                }
            ],
            "expectedStateVersion": v0,
        },
    )
    assert mid["stateVersion"] != v0
    n_mid = _db_rev_count(pid)
    revise_mid = _revise_count(_db_rev_rows(pid))
    leak_marker = "法人-LLM迟到正文-禁止回显"

    items = _qualify_revised_items(leak_marker)
    _mock_struct_llm(monkeypatch, items, summary="已强化。")

    res = _post_revise(
        client,
        pid,
        stage="business_qualify",
        expected=v0,
        message="强化",
        base_content="[]",
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "editor_state_version_conflict"
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert leak_marker not in blob
    assert "禁止回显" not in blob
    assert _SECRET not in blob

    state = _get_state(client, pid)
    assert state["businessQualify"][0]["requirement"] == "法人-外部已改"
    assert state["stateVersion"] == mid["stateVersion"]
    assert _db_rev_count(pid) == n_mid
    assert _revise_count(_db_rev_rows(pid)) == revise_mid


def test_llm_inflight_concurrent_browser_409_excludes_browser_from_revise(
    client: TestClient, monkeypatch
):
    """
    用途：LLM 期间真实并发浏览器 PUT → 409、模型正文不回显、revise 零增量；
      外部 browser_put 行按来源+精确版本排除，不得把总数当 revise 增量。
    """
    pid = _create_project(client, name="并发浏览器漂移")
    seed = _seed_qualify(client, pid)
    v0 = seed["stateVersion"]
    revise_n0 = _revise_count(_db_rev_rows(pid))

    llm_started = threading.Event()
    llm_continue = threading.Event()
    barrier = threading.Barrier(2)
    leak_marker = "法人-LLM进行中正文禁止回显"
    external_marker = f"法人-并发浏览器已改-{_SECRET[-6:]}"
    external_put_version: dict[str, str | None] = {"v": None}

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        llm_started.set()
        barrier.wait(timeout=10)
        assert llm_continue.wait(timeout=10)
        items = _qualify_revised_items(leak_marker)
        return SimpleNamespace(
            content="已强化。\n\n" + json.dumps(items, ensure_ascii=False),
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    result_box: dict = {}

    def do_revise() -> None:
        result_box["res"] = _post_revise(
            client,
            pid,
            stage="business_qualify",
            expected=v0,
            message="强化",
            base_content="[]",
        )

    t = threading.Thread(target=do_revise, name="revise-inflight-bb2")
    t.start()
    assert llm_started.wait(timeout=10), "LLM 未进入挂起点"
    barrier.wait(timeout=10)

    # 真实浏览器 PUT（记 browser_put），非顺序服务直写冒充
    put_out = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": external_marker,
                    "response": "保留",
                    "evidence": "",
                    "status": "matched",
                }
            ],
            "expectedStateVersion": v0,
        },
    )
    external_put_version["v"] = put_out["stateVersion"]
    assert put_out["stateVersion"] != v0

    llm_continue.set()
    t.join(timeout=30)
    assert not t.is_alive()
    res = result_box["res"]
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "editor_state_version_conflict"
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert leak_marker not in blob
    assert "禁止回显" not in blob

    state = _get_state(client, pid)
    assert state["businessQualify"][0]["requirement"] == external_marker
    v_ext = _assert_state_version(external_put_version["v"])
    assert state["stateVersion"] == v_ext

    rows = _db_rev_rows(pid)
    # 按来源+精确版本定位外部浏览器行；禁止 any 假绿 / 总数误判
    ext_matched = _rows_by_version(rows, v_ext)
    assert len(ext_matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    assert ext_matched[0].source_kind == _SOURCE_BROWSER
    assert external_marker in (ext_matched[0].snapshot_json or "")
    # revise 增量精确为 0（外部 browser 行不得计入）
    assert _revise_count(rows) == revise_n0
    assert not any(
        r.source_kind == _SOURCE_REVISE and r.state_version == v_ext for r in rows
    )


# ---------- recorder / commit 失败双零 ----------


def test_recorder_flush_then_fail_http_500_double_zero(
    client: TestClient, monkeypatch
):
    """
    用途：recorder 已 flush 后注入失败 → 真实 HTTP 500 脱敏，state/revision 双零。
    """
    pid = _create_project(client, name="flush失败双零")
    seed = _seed_qualify(client, pid, requirement="稳定基线")
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    biz0 = _db_editor_qualify_marker(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _record_then_boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        assert out["added_count"] >= 1
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service,
        "record_editor_state_transition",
        _record_then_boom,
    )
    items = _qualify_revised_items(f"不应落库-{_SECRET}")
    _mock_struct_llm(monkeypatch, items)

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            f"/api/projects/{pid}/artifacts/workspace/revise",
            json={
                "stage": "business_qualify",
                "message": "强化",
                "preserveStructure": True,
                "expectedStateVersion": v0,
                "baseContent": "[]",
            },
        )
    assert calls["n"] == 1
    assert res.status_code == 500, res.text
    _assert_sanitized_http_500(
        res.text,
        _INJECT_AFTER_FLUSH,
        "不应落库",
        v0,
        "p12cbb2",
    )

    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0
    assert _db_editor_qualify_marker(pid) == biz0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["businessQualify"][0]["requirement"] == "稳定基线"
    assert _SECRET not in state["businessQualify"][0]["requirement"]


def test_commit_failure_after_flush_http_500_double_zero(
    client: TestClient, monkeypatch
):
    """
    用途：commit 前 revision 已 flush 后失败 → HTTP 500 脱敏；回滚后双零。
    二次开发：同一 Session 证明 pending==n0+1，不得假定 ID 序。
    """
    pid = _create_project(client, name="commit失败双零")
    seed = _seed_qualify(client, pid, requirement="commit基线")
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    biz0 = _db_editor_qualify_marker(pid)
    revise_n0 = _revise_count(_db_rev_rows(pid))

    real_upsert = editor_state_service.upsert_editor_state
    commit_probe = {"n": 0, "pending": None}
    rollbacks = {"n": 0}

    def wrap_upsert(db, *args, **kwargs):
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*a, **k):
            commit_probe["n"] += 1
            commit_probe["pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        def _count_rollback(*a, **k):
            rollbacks["n"] += 1
            return real_rollback(*a, **k)

        db.commit = _bad_commit  # type: ignore[method-assign]
        db.rollback = _count_rollback  # type: ignore[method-assign]
        try:
            return real_upsert(db, *args, **kwargs)
        finally:
            db.commit = real_commit  # type: ignore[method-assign]
            db.rollback = real_rollback  # type: ignore[method-assign]

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", wrap_upsert)
    items = _qualify_revised_items(f"commit失败不应落库-{_SECRET}")
    _mock_struct_llm(monkeypatch, items)

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            f"/api/projects/{pid}/artifacts/workspace/revise",
            json={
                "stage": "business_qualify",
                "message": "强化",
                "preserveStructure": True,
                "expectedStateVersion": v0,
                "baseContent": "[]",
            },
        )
    assert commit_probe["n"] == 1
    # 精确 n0+1：账本已有浏览器 after=before，本轮只 flush 一条 after
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应已 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert rollbacks["n"] >= 1
    assert res.status_code == 500, res.text
    _assert_sanitized_http_500(
        res.text,
        _INJECT_COMMIT_FAIL,
        "不应落库",
        v0,
        "p12cbb2",
    )

    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0
    assert _db_editor_qualify_marker(pid) == biz0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["businessQualify"][0]["requirement"] == "commit基线"
    for r in _db_rev_rows(pid):
        assert _INJECT_COMMIT_FAIL not in (r.snapshot_json or "")
        assert _SECRET not in (r.snapshot_json or "") or r.source_kind == _SOURCE_BROWSER


# ---------- 来源隔离 ----------


def test_browser_task_and_plain_upsert_isolation(
    client: TestClient, monkeypatch
):
    """用途：browser_put/task/无来源直写均不误记 revise；成功 revise 后三类并存。"""
    pid = _create_project(client, name="来源隔离")
    put = _put_state(client, pid, {"parsedMarkdown": "浏览器正文"})
    v_put = put["stateVersion"]
    rows0 = _db_rev_rows(pid)
    put_matched0 = _rows_by_version(rows0, v_put)
    assert len(put_matched0) == 1
    assert put_matched0[0].source_kind == _SOURCE_BROWSER

    n0 = _db_rev_count(pid)
    revise_n0 = _revise_count(rows0)

    # 1) 无来源直写 → 成功但 revision 不增
    db = SessionLocal()
    try:
        out = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            facts=[{"id": "svc", "text": "服务直写"}],
            expected_state_version=v_put,
        )
        v_plain = out["stateVersion"]
        assert v_plain != v_put
    finally:
        db.close()
    assert _db_rev_count(pid) == n0
    assert _revise_count(_db_rev_rows(pid)) == revise_n0

    # 2) task 来源写（生产 task 包装）不得变成 revise
    from app.services import task_service

    db = SessionLocal()
    try:
        written = task_service._upsert_editor_state_for_task(
            db,
            _WS,
            pid,
            analysis_overview="任务分析摘要",
            expected_state_version=v_plain,
        )
        v_task = written["stateVersion"]
    finally:
        db.close()
    rows_task = _db_rev_rows(pid)
    task_matched = _rows_by_version(rows_task, v_task)
    assert len(task_matched) == 1
    assert task_matched[0].source_kind == _SOURCE_TASK
    assert _revise_count(rows_task) == revise_n0

    # 3) 本轮 revise 成功 → 仅新增 revise，浏览器/task 不串
    items = _qualify_revised_items("隔离后的修订")
    _mock_struct_llm(monkeypatch, items)
    # 先补资格字段基线
    cur = _get_state(client, pid)
    seed_q = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "隔离法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ],
            "expectedStateVersion": cur["stateVersion"],
        },
    )
    v_q = seed_q["stateVersion"]
    revise_before = _revise_count(_db_rev_rows(pid))

    res = _post_revise(
        client,
        pid,
        stage="business_qualify",
        expected=v_q,
        message="隔离修订",
        base_content="[]",
    )
    assert res.status_code == 200, res.text
    after_ver = _assert_state_version(res.json().get("stateVersion"))
    rows = _db_rev_rows(pid)
    _assert_revise_after(rows, after_ver)
    # 浏览器版本匹配集合必须恰好 1 条
    put_matched = _rows_by_version(rows, v_put)
    assert len(put_matched) == 1
    assert put_matched[0].source_kind == _SOURCE_BROWSER
    task_matched2 = _rows_by_version(rows, v_task)
    assert len(task_matched2) == 1
    assert task_matched2[0].source_kind == _SOURCE_TASK
    assert _revise_count(rows) == revise_before + 1
    assert not any(
        r.source_kind == _SOURCE_REVISE and r.state_version == v_put for r in rows
    )
    assert not any(
        r.source_kind == _SOURCE_REVISE and r.state_version == v_task for r in rows
    )
