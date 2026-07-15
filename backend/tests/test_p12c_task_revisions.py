"""
模块：P12C-B-B1 九类任务修订账本接入专项测试
用途：真实 SQLite 验收 task 来源写入、stale 零修订、逐章前缀、
  recorder/commit 失败双零、浏览器与非 writer 隔离。
对接：task_service / business_task_service → upsert_editor_state(revision_source_kind)。
二次开发：禁止 mock 掉 SQLite、假定随机 ID=插入序、仅静态字符串假绿。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.entities import (
    EditorStateRevisionRow,
    ProjectEditorStateRow,
)
from app.services import (
    editor_state_revision_service,
    editor_state_service,
    llm_service,
    task_service,
)
from app.services.llm_service import ChatResult

_WS = "ws_local"
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_SOURCE_TASK = "task"
_SOURCE_BROWSER = "browser_put"
_SECRET = "SECRET_P12CBB1_BODY_MUST_NOT_LEAK"
_STALE_MSG = "任务结果已过期"
_STALE_ERR = "任务基于的编辑内容已变化，请重新载入后重试"
_UPSERT_FAIL_MSG = "编辑内容写入失败，请重试"
_INTERNAL_KEY = "_expectedStateVersion"
_INJECT_AFTER_FLUSH = "p12cbb1_injected_after_flush"
_INJECT_COMMIT_FAIL = "p12cbb1_injected_commit_failure"
_WRAPPER_NAME = "_upsert_editor_state_for_task"

_TASK_SERVICE = Path(__file__).resolve().parents[1] / "app" / "services" / "task_service.py"
_BIZ_SERVICE = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "business_task_service.py"
)


def _create_project(
    client: TestClient, name: str = "P12C-B-B1", kind: str = "technical"
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


def _assert_no_version_leak(obj: object) -> None:
    text = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    assert "esv_" not in text, text
    assert _INTERNAL_KEY not in text, text
    assert "revision_source_kind" not in text
    assert "revisionSourceKind" not in text


def _assert_no_secret_leak(blob: str, *extra: str) -> None:
    low = blob.lower()
    assert _SECRET not in blob
    assert "traceback" not in low
    assert "select " not in low
    assert "sqlite" not in low
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


def _db_editor_analysis_overview(project_id: str) -> str | None:
    """新 Session 读库，避免同身份映射假绿。"""
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.analysis_overview
    finally:
        db.close()


def _db_editor_facts_marker(project_id: str) -> str | None:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return row.facts_json
    finally:
        db.close()


def _rows_by_version(
    rows: list[EditorStateRevisionRow], state_version: str
) -> list[EditorStateRevisionRow]:
    return [r for r in rows if r.state_version == state_version]


def _assert_task_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = _rows_by_version(rows, after_ver)
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert row.source_kind == _SOURCE_TASK
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _call_func_name(node: ast.Call) -> str | None:
    """用途：解析 Call 的简单函数名（Name 或 Attribute.attr）。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _collect_calls_named(path: Path, name: str) -> list[ast.Call]:
    """用途：AST 收集指定函数名的全部 Call 节点。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_func_name(node) == name
    ]


def _find_function_def(path: Path, name: str) -> ast.FunctionDef | None:
    """用途：定位模块顶层函数定义。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _wrapper_task_source_literals(wrapper: ast.FunctionDef) -> list[str]:
    """
    用途：从包装器 AST 提取 revision_source_kind 字符串字面量。
    二次开发：禁止只数源码字符串；必须来自 Call 关键字参数。
    """
    literals: list[str] = []
    for node in ast.walk(wrapper):
        if not isinstance(node, ast.Call):
            continue
        if _call_func_name(node) != "upsert_editor_state":
            continue
        for kw in node.keywords:
            if kw.arg != "revision_source_kind":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                literals.append(kw.value.value)
            else:
                literals.append("<non-literal>")
    return literals


def _assert_sanitized_upsert_failure(body: dict, raw_text: str, inject_marker: str) -> None:
    """用途：失败响应精确脱敏：固定中文 error，无注入 marker/异常类名/SQL/版本/来源键。"""
    assert body.get("status") == "failed", body
    assert body.get("error") == _UPSERT_FAIL_MSG, body
    assert body.get("message") == "任务失败", body
    blob = raw_text if isinstance(raw_text, str) else json.dumps(body, ensure_ascii=False)
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
    assert _INTERNAL_KEY not in blob
    assert "esv_" not in blob
    assert _SECRET not in blob
    _assert_no_version_leak(body)


def _mock_analyze_chat(monkeypatch, overview: str = "任务分析成功摘要") -> None:
    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=(
                f'{{"overview":"{overview}",'
                '"techRequirements":["1000路视频"],'
                '"rejectionRisks":[],'
                '"scoringPoints":[{"name":"架构","weight":"10%"}]}'
            ),
            model="mock-p12cbb1",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)


# ---------- 静态调用集合（补充，不替代库行为） ----------


def test_nine_writer_calls_pass_literal_task_source():
    """
    用途：AST 验证 9 个 writer 均经任务包装器；两包装器内唯一固定 task 字面量。
    二次开发：不得只数源码字符串；不得替代真实库行为测试。
    """
    for path, expected_writers in ((_TASK_SERVICE, 5), (_BIZ_SERVICE, 4)):
        wrapper = _find_function_def(path, _WRAPPER_NAME)
        assert wrapper is not None, f"缺少 {_WRAPPER_NAME}: {path}"
        # 包装器内：恰好一次 upsert_editor_state，且 revision_source_kind 唯一字面量 task
        inner_upserts = [
            n
            for n in ast.walk(wrapper)
            if isinstance(n, ast.Call) and _call_func_name(n) == "upsert_editor_state"
        ]
        assert len(inner_upserts) == 1, [
            getattr(n, "lineno", None) for n in inner_upserts
        ]
        literals = _wrapper_task_source_literals(wrapper)
        assert literals == [_SOURCE_TASK], literals

        # 包装器外：零直接 upsert_editor_state；writer 数 == 包装器调用数
        tree = ast.parse(path.read_text(encoding="utf-8"))
        outer_direct: list[int] = []
        outer_wrapper: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # 跳过包装器函数体内部节点
            if (
                node.lineno >= wrapper.lineno
                and (
                    wrapper.end_lineno is None or node.lineno <= wrapper.end_lineno
                )
            ):
                # 仅包装器定义范围内：允许内部 upsert；不计入 outer_wrapper
                if _call_func_name(node) == "upsert_editor_state":
                    continue
                if _call_func_name(node) == _WRAPPER_NAME:
                    continue
                continue
            fname = _call_func_name(node)
            if fname == "upsert_editor_state":
                outer_direct.append(node.lineno)
            elif fname == _WRAPPER_NAME:
                outer_wrapper.append(node.lineno)
        assert outer_direct == [], outer_direct
        assert len(outer_wrapper) == expected_writers, outer_wrapper


# ---------- 成功路径：真实库 ----------


def test_analyze_success_records_task_revision(client: TestClient, monkeypatch):
    """用途：analyze 成功 → 来源 task 的 after 修订与权威 stateVersion 精确一致。"""
    pid = _create_project(client, name="analyze成功")
    seed = _put_state(client, pid, {"parsedMarkdown": f"# 招标\n{_SECRET}"})
    v0 = _assert_state_version(seed["stateVersion"])
    n0 = _db_rev_count(pid)
    assert n0 >= 2  # 浏览器 before+after
    browser_n0 = sum(1 for r in _db_rev_rows(pid) if r.source_kind == _SOURCE_BROWSER)
    assert browser_n0 == n0

    _mock_analyze_chat(monkeypatch, overview="当前版本分析成功")
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "analyze"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    _assert_no_version_leak(body)

    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    assert state["analysis"]["overview"] == "当前版本分析成功"
    assert "1000路视频" in (state["analysis"].get("techRequirements") or [])

    rows = _db_rev_rows(pid)
    # 按 state_version 定位 after，禁止下标臆测
    after_row = _assert_task_after(rows, after_ver)
    assert after_row.project_id == pid
    assert after_row.workspace_id == _WS
    # 浏览器基线仍在且仍为 browser_put
    assert any(r.source_kind == _SOURCE_BROWSER for r in rows)
    browser_rows = [r for r in rows if r.source_kind == _SOURCE_BROWSER]
    assert all(r.source_kind == _SOURCE_BROWSER for r in browser_rows)
    # 本轮精确新增 1 条 task after（before 已在账本则不去重再写）
    task_rows = [r for r in rows if r.source_kind == _SOURCE_TASK]
    assert len(task_rows) == 1
    assert after_row in task_rows
    assert _db_rev_count(pid) == n0 + 1


def test_chapters_two_success_two_task_revisions(client: TestClient, monkeypatch):
    """用途：批量两章均成功 → 两条连续 task 修订，最终版本与状态一致。"""
    pid = _create_project(client, name="批量两章成功")
    seed = _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "批量基准",
            "outline": [
                {"id": "n1", "title": "第一章", "children": []},
                {"id": "n2", "title": "第二章", "children": []},
            ],
            "chapters": [
                {
                    "id": "n1",
                    "title": "第一章",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                    "targetWords": 100,
                },
                {
                    "id": "n2",
                    "title": "第二章",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                    "targetWords": 100,
                },
            ],
        },
    )
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    gen_calls = {"n": 0}
    versions_after_write: list[str] = []

    def fake_gen(
        db,
        workspace_id,
        *,
        title,
        target_words,
        overview,
        facts_txt,
        analysis_block="",
        project_id=None,
    ):
        gen_calls["n"] += 1
        return f"## {title}\n生成正文{gen_calls['n']}", []

    monkeypatch.setattr(
        "app.services.task_service._generate_one_chapter_body", fake_gen
    )
    monkeypatch.setattr("app.services.task_service.time.sleep", lambda *_a, **_k: None)

    # 拦截 upsert 记录每次成功后的版本（生产改动前后均可）
    real_upsert = editor_state_service.upsert_editor_state

    def spy_upsert(*args, **kwargs):
        out = real_upsert(*args, **kwargs)
        versions_after_write.append(out["stateVersion"])
        return out

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy_upsert)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapters", "payload": {"onlyEmpty": True}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    _assert_no_version_leak(body)
    assert gen_calls["n"] == 2
    assert len(versions_after_write) == 2
    v1, v2 = versions_after_write
    assert v1 != v0 and v2 != v1

    state = _get_state(client, pid)
    assert state["stateVersion"] == v2
    ch1 = next(c for c in state["chapters"] if c["id"] == "n1")
    ch2 = next(c for c in state["chapters"] if c["id"] == "n2")
    assert "生成正文1" in (ch1.get("body") or "")
    assert "生成正文2" in (ch2.get("body") or "")

    rows = _db_rev_rows(pid)
    _assert_task_after(rows, v1)
    _assert_task_after(rows, v2)
    task_after_versions = {
        r.state_version for r in rows if r.source_kind == _SOURCE_TASK
    }
    assert {v1, v2}.issubset(task_after_versions)
    assert _db_rev_count(pid) == n0 + 2


def test_chapters_inter_drift_keeps_success_prefix_only(
    client: TestClient, monkeypatch
):
    """用途：章间外部漂移 → 仅成功前缀 task 修订，冲突章零增量。"""
    pid = _create_project(client, name="章间漂移前缀")
    seed = _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "章间基准",
            "outline": [
                {"id": "n1", "title": "第一章", "children": []},
                {"id": "n2", "title": "第二章", "children": []},
            ],
            "chapters": [
                {
                    "id": "n1",
                    "title": "第一章",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                    "targetWords": 100,
                },
                {
                    "id": "n2",
                    "title": "第二章",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                    "targetWords": 100,
                },
            ],
        },
    )
    external_marker = f"章间外部注入-{_SECRET}"
    gen_calls = {"n": 0}
    ch1_version: dict[str, str | None] = {"v": None}
    external_put_version: dict[str, str | None] = {"v": None}

    def fake_gen(
        db,
        workspace_id,
        *,
        title,
        target_words,
        overview,
        facts_txt,
        analysis_block="",
        project_id=None,
    ):
        gen_calls["n"] += 1
        if gen_calls["n"] == 2:
            # 第一章已成功；外部用浏览器 PUT 漂移（记 browser_put，非 task）
            cur = _get_state(client, pid)
            ch1_version["v"] = cur["stateVersion"]
            put_out = _put_state(
                client,
                pid,
                {
                    "parsedMarkdown": external_marker,
                    "expectedStateVersion": cur["stateVersion"],
                },
            )
            # 保存外部 PUT 精确 stateVersion，禁止 any(browser) 假绿
            external_put_version["v"] = put_out["stateVersion"]
        return f"## {title}\n生成正文{gen_calls['n']}", []

    monkeypatch.setattr(
        "app.services.task_service._generate_one_chapter_body", fake_gen
    )
    monkeypatch.setattr("app.services.task_service.time.sleep", lambda *_a, **_k: None)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapters", "payload": {"onlyEmpty": True}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "failed", body
    assert body["message"] == _STALE_MSG
    assert body["error"] == _STALE_ERR
    _assert_no_version_leak(body)

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == external_marker
    ch1 = next(c for c in state["chapters"] if c["id"] == "n1")
    ch2 = next(c for c in state["chapters"] if c["id"] == "n2")
    assert "生成正文1" in (ch1.get("body") or "")
    assert "生成正文2" not in (ch2.get("body") or "")
    assert not (ch2.get("body") or "").strip()

    rows = _db_rev_rows(pid)
    # 第一章 after 必须有且仅有一条 task 修订
    assert ch1_version["v"] is not None
    v_ch1 = _assert_state_version(ch1_version["v"])
    _assert_task_after(rows, v_ch1)
    task_rows = [r for r in rows if r.source_kind == _SOURCE_TASK]
    # 冲突章不得再记 task；外部 PUT 为 browser_put
    assert len(task_rows) == 1, [(r.state_version, r.source_kind) for r in task_rows]
    # 按外部 PUT 精确版本定位唯一 revision
    assert external_put_version["v"] is not None
    v_ext = _assert_state_version(external_put_version["v"])
    ext_matched = _rows_by_version(rows, v_ext)
    assert len(ext_matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    assert ext_matched[0].source_kind == _SOURCE_BROWSER
    assert external_marker in (ext_matched[0].snapshot_json or "")
    # 最终权威版本来自外部 PUT，不是 ch2 迟到写
    assert state["stateVersion"] == v_ext
    assert state["stateVersion"] != v_ch1
    assert not any(
        r.source_kind == _SOURCE_TASK and r.state_version == state["stateVersion"]
        for r in rows
    )


def test_biz_qualify_success_records_task_revision(client: TestClient, monkeypatch):
    """用途：至少一类商务任务成功写回 → task 修订与资格字段一致。"""
    pid = _create_project(client, name="商务资格成功", kind="business")
    seed = _put_state(
        client, pid, {"parsedMarkdown": f"## 资格\n独立法人\n{_SECRET}"}
    )
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)

    fake_items = [
        {
            "id": "q1",
            "requirement": "独立法人",
            "response": "具备",
            "evidence": "",
            "status": "pending",
        }
    ]

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content=json.dumps(fake_items, ensure_ascii=False), model="mock"
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "biz_qualify", "payload": {}},
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    assert body["status"] == "success", body
    _assert_no_version_leak(body)

    state = _get_state(client, pid)
    after_ver = _assert_state_version(state["stateVersion"])
    assert after_ver != v0
    assert state["businessQualify"][0]["requirement"] == "独立法人"

    rows = _db_rev_rows(pid)
    _assert_task_after(rows, after_ver)
    assert _db_rev_count(pid) == n0 + 1


# ---------- stale / 失败原子性 ----------


def test_analyze_stale_zero_task_revision(client: TestClient, monkeypatch):
    """用途：创建后漂移 → failed 固定文案，editor-state/revision 零任务增量。"""
    pid = _create_project(client)
    base = _put_state(
        client, pid, {"parsedMarkdown": f"原始解析不可被覆盖-{_SECRET}"}
    )
    v0 = base["stateVersion"]
    n0 = _db_rev_count(pid)

    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db, _WS, pid, task_type="analyze", payload={}
        )
        tid = task.id
    finally:
        db.close()

    after = _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "外部并发改动后的解析文",
            "expectedStateVersion": v0,
        },
    )
    assert after["stateVersion"] != v0
    n_after_put = _db_rev_count(pid)
    # 浏览器 PUT 可增 revision；任务不得再增 task
    task_n_before_run = sum(
        1 for r in _db_rev_rows(pid) if r.source_kind == _SOURCE_TASK
    )

    _mock_analyze_chat(monkeypatch, overview="迟到分析摘要应被拒绝")

    db = SessionLocal()
    try:
        task = task_service.get_task(db, _WS, pid, tid)
        task_service._execute_task(db, _WS, task)
        db.refresh(task)
        assert task.status == "failed", (task.status, task.error, task.message)
        assert task.message == _STALE_MSG
        assert task.error == _STALE_ERR
        leak = {
            "message": task.message,
            "error": task.error,
            "result": task.result_json,
        }
        _assert_no_version_leak(leak)
        _assert_no_secret_leak(json.dumps(leak, ensure_ascii=False))
    finally:
        db.close()

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "外部并发改动后的解析文"
    analysis = state.get("analysis") or {}
    assert analysis.get("overview") != "迟到分析摘要应被拒绝"
    assert _db_rev_count(pid) == n_after_put
    task_n_after = sum(1 for r in _db_rev_rows(pid) if r.source_kind == _SOURCE_TASK)
    assert task_n_after == task_n_before_run == 0
    assert n0 >= 2


def test_recorder_flush_then_fail_double_zero_on_analyze(
    client: TestClient, monkeypatch
):
    """
    用途：recorder 已 flush 后注入失败 → 本次 editor-state/revision 双零；
      任务按既有规则失败且不泄密。
    """
    pid = _create_project(client, name="flush失败双零")
    seed = _put_state(
        client, pid, {"parsedMarkdown": "稳定基线", "facts": [{"id": "f0", "text": "ok"}]}
    )
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    overview0 = _db_editor_analysis_overview(pid)

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
    _mock_analyze_chat(monkeypatch, overview="不应落库的分析")

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "analyze"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert calls["n"] == 1
    # 窄范围脱敏：固定中文 error；注入 marker / 异常类名 / SQL / 版本 / 来源键均不得回显
    _assert_sanitized_upsert_failure(body, res.text, _INJECT_AFTER_FLUSH)
    assert v0 not in res.text

    assert _db_rev_count(pid) == n0
    assert _db_editor_analysis_overview(pid) == overview0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert (state.get("analysis") or {}).get("overview") != "不应落库的分析"


def test_revision_commit_failure_double_zero_via_analyze(
    client: TestClient, monkeypatch
):
    """
    用途：analyze upsert 的 commit 失败 → commit 前 revision 已 flush，
      回滚后 editor-state/revision 精确不变。
    """
    pid = _create_project(client, name="commit失败双零")
    seed = _put_state(client, pid, {"parsedMarkdown": "commit基线"})
    v0 = seed["stateVersion"]
    n0 = _db_rev_count(pid)
    overview0 = _db_editor_analysis_overview(pid)

    real_upsert = editor_state_service.upsert_editor_state
    commit_probe = {"n": 0, "pending": None}
    rollbacks = {"n": 0}

    def wrap_upsert(db, *args, **kwargs):
        real_commit = db.commit
        real_rollback = db.rollback

        def _bad_commit(*a, **k):
            # 禁止在注入内 assert：否则未接 revision 时 AssertionError 被任务吞掉造成假绿
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
    _mock_analyze_chat(monkeypatch, overview="commit失败不应落库")

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "analyze"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert commit_probe["n"] == 1
    # 精确 n0+1：账本已有浏览器 after=before，本轮只 flush 一条 after
    assert commit_probe["pending"] == n0 + 1, (
        f"commit 前 revision 应已 flush 至 n0+1，实际 {commit_probe['pending']}（n0={n0}）"
    )
    assert rollbacks["n"] >= 1
    # 窄范围脱敏：固定中文 error；注入 marker / 异常类名不得回显
    _assert_sanitized_upsert_failure(body, res.text, _INJECT_COMMIT_FAIL)
    assert v0 not in res.text

    # 回滚后双零
    assert _db_rev_count(pid) == n0
    assert _db_editor_analysis_overview(pid) == overview0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert (state.get("analysis") or {}).get("overview") != "commit失败不应落库"
    for r in _db_rev_rows(pid):
        assert _INJECT_COMMIT_FAIL not in (r.snapshot_json or "")


# ---------- 隔离：浏览器 / 非 writer / 无来源直写 ----------


def test_browser_put_remains_browser_put_not_task(client: TestClient, monkeypatch):
    """用途：浏览器 PUT 仍记 browser_put；task 成功后两类来源并存且不串。"""
    pid = _create_project(client, name="来源隔离")
    put = _put_state(client, pid, {"parsedMarkdown": "浏览器正文"})
    v_put = put["stateVersion"]
    rows0 = _db_rev_rows(pid)
    assert {r.source_kind for r in rows0} == {_SOURCE_BROWSER}
    _assert_state_version(v_put)

    _mock_analyze_chat(monkeypatch, overview="任务写分析")
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true", json={"type": "analyze"}
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "success"

    state = _get_state(client, pid)
    after = state["stateVersion"]
    rows = _db_rev_rows(pid)
    # 浏览器版本匹配集合必须恰好 1 条，禁止空集合 for 假绿
    put_matched = _rows_by_version(rows, v_put)
    assert len(put_matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    assert put_matched[0].source_kind == _SOURCE_BROWSER
    _assert_task_after(rows, after)
    assert not any(
        r.source_kind == _SOURCE_TASK and r.state_version == v_put for r in rows
    )


def test_plain_upsert_and_non_writer_do_not_record_task(
    client: TestClient, monkeypatch
):
    """用途：无来源直写与 export 非 writer 不得误记 task 修订。"""
    pid = _create_project(client, name="非writer隔离")
    seed = _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "导出基准",
            "outline": [{"id": "n1", "title": "一", "children": []}],
            "chapters": [
                {
                    "id": "n1",
                    "title": "一",
                    "body": "正文足够导出",
                    "preview": "正文",
                    "wordCount": 6,
                    "status": "done",
                    "targetWords": 100,
                }
            ],
        },
    )
    n0 = _db_rev_count(pid)
    task_n0 = sum(1 for r in _db_rev_rows(pid) if r.source_kind == _SOURCE_TASK)

    # 1) 直接 upsert 不传来源 → 业务成功但 revision 不增
    db = SessionLocal()
    try:
        out = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            facts=[{"id": "svc", "text": "服务直写"}],
        )
        assert out["facts"][0]["id"] == "svc"
    finally:
        db.close()
    assert _db_rev_count(pid) == n0

    # 2) export 非 writer：成功不得产生 task 修订
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export", "payload": {}},
    )
    assert res.status_code == 201, res.text
    # export 可能 success 或因环境失败；无论终态，revision 不得出现 task
    task_n1 = sum(1 for r in _db_rev_rows(pid) if r.source_kind == _SOURCE_TASK)
    assert task_n1 == task_n0 == 0
    assert all(r.source_kind != _SOURCE_TASK for r in _db_rev_rows(pid))
    # seed 版本仍合法
    _assert_state_version(seed["stateVersion"])
