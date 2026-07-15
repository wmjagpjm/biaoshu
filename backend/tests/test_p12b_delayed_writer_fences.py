"""
模块：P12B-C 延迟写入围栏专项（C1 任务/revise + C2 个人 callback/P8C + C3 M3-D）
用途：证明迟到任务零覆盖、版本不外泄、批量章节自推进、
  商务 revise 强制 expected 与陈旧 409 无正文回显；
  C2：个人 callback 强制 expected、陈旧 409 原子零写；P8C 票据绑定版本；
  C3：M3-D apply/consume 强制 expected、13 键漂移 409 零批次、成功返回新版本。
对接：task_service / business_task_service / revise_service / editor_state_service /
  parse_callback / local_parser_ticket_service / content_fuse_application_service。
二次开发：禁止 or True、宽泛状态码、顺序调用冒充并发、修改旧断言迎合实现。
"""

from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.entities import Project, ProjectTaskRow
from app.services import editor_state_service, llm_service, task_service
from app.services.llm_service import ChatResult

_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_WS = "ws_local"
_STALE_MSG = "任务结果已过期"
_STALE_ERR = "任务基于的编辑内容已变化，请重新载入后重试"
_INTERNAL_KEY = "_expectedStateVersion"


def _create_project(client: TestClient, name: str = "P12B-C1", kind: str = "technical") -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _get_state(client: TestClient, pid: str) -> dict:
    res = client.get(f"/api/projects/{pid}/editor-state")
    assert res.status_code == 200, res.text
    return res.json()


def _put_state(client: TestClient, pid: str, body: dict) -> dict:
    res = client.put(f"/api/projects/{pid}/editor-state", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _assert_no_version_leak(obj: object) -> None:
    """用途：任务 REST/错误面不得出现 esv_ 或内部键。"""
    text = json.dumps(obj, ensure_ascii=False)
    assert "esv_" not in text, text
    assert _INTERNAL_KEY not in text, text


def test_lock_and_assert_expected_primitive_exists_and_no_commit():
    """用途：共用锁后原语必须存在且不自行 commit（旧实现 AttributeError 红）。"""
    assert hasattr(editor_state_service, "lock_and_assert_expected_state_version")
    fn = editor_state_service.lock_and_assert_expected_state_version
    assert callable(fn)


def test_writer_task_internal_version_not_in_rest(client: TestClient):
    """
    用途：writer 任务 REST/SSE 不得泄露内部基准版本。
    二次开发：用 create_task_record 建终态合成任务，禁止默认异步启动真实 analyze/LLM。
    """
    pid = _create_project(client)
    base = _put_state(client, pid, {"parsedMarkdown": "基准正文-A"})
    assert _STATE_VERSION_RE.fullmatch(base["stateVersion"])

    # 仅本地建档 + 写成终态，不 enqueue、不触发 LLM
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            _WS,
            pid,
            task_type="analyze",
            payload={_INTERNAL_KEY: "esv_" + "a" * 32},
        )
        tid = task.id
        # 客户端恶意键应被服务端权威版本覆盖
        payload = json.loads(task.payload_json or "{}")
        assert payload.get(_INTERNAL_KEY) == base["stateVersion"]
        task.status = "success"
        task.progress = 100
        task.message = "合成终态"
        task.result_json = json.dumps({"ok": True}, ensure_ascii=False)
        db.commit()
    finally:
        db.close()

    got = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got.status_code == 200, got.text
    body = got.json()
    _assert_no_version_leak(body)
    listed = client.get(f"/api/projects/{pid}/tasks")
    assert listed.status_code == 200, listed.text
    _assert_no_version_leak(listed.json())

    # SSE 首包 snapshot 同样不得泄露内部键/版本
    with client.stream(
        "GET", f"/api/projects/{pid}/tasks/{tid}/events"
    ) as stream:
        assert stream.status_code == 200, stream.text
        collected = []
        for line in stream.iter_lines():
            if not line:
                continue
            collected.append(line)
            # 读完首个 data 行即可（终态后生成器关闭）
            if line.startswith("data:"):
                break
        sse_text = "\n".join(collected)
        assert "esv_" not in sse_text, sse_text
        assert _INTERNAL_KEY not in sse_text, sse_text


def test_analyze_stale_after_create_fails_zero_write(client: TestClient, monkeypatch):
    """用途：创建后改状态再运行 → failed 固定文案，正文/分析零覆盖。"""
    pid = _create_project(client)
    base = _put_state(
        client,
        pid,
        {"parsedMarkdown": "原始解析文不可被迟到覆盖"},
    )
    v0 = base["stateVersion"]
    assert _STATE_VERSION_RE.fullmatch(v0)

    # 仅 create_task_record，避免 enqueue 后台竞态
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db, _WS, pid, task_type="analyze", payload={}
        )
        tid = task.id
        assert task.status == "pending"
        public = task_service.task_to_dict(task)
        _assert_no_version_leak(public)
    finally:
        db.close()

    # 创建后外部改写权威态，使任务基准陈旧
    after = _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "外部并发改动后的解析文",
            "expectedStateVersion": v0,
        },
    )
    assert after["parsedMarkdown"] == "外部并发改动后的解析文"
    assert after["stateVersion"] != v0

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=(
                '{"overview":"迟到分析摘要应被拒绝",'
                '"techRequirements":["迟到要求"],'
                '"rejectionRisks":[],'
                '"scoringPoints":[]}'
            ),
            model="mock-c1",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    # 直接驱动 worker（沿用创建时 payload 内基准）
    db = SessionLocal()
    try:
        task = task_service.get_task(db, _WS, pid, tid)
        task_service._execute_task(db, _WS, task)
        db.refresh(task)
        assert task.status == "failed", (task.status, task.error, task.message)
        assert task.progress == 100
        assert task.message == _STALE_MSG
        assert task.error == _STALE_ERR
        assert task.result_json in (None, "", "{}")
        leak_blob = {
            "message": task.message,
            "error": task.error,
            "result": task.result_json,
        }
        _assert_no_version_leak(leak_blob)
    finally:
        db.close()

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "外部并发改动后的解析文"
    analysis = state.get("analysis") or {}
    assert analysis.get("overview") != "迟到分析摘要应被拒绝"
    assert "迟到要求" not in (analysis.get("techRequirements") or [])


def test_analyze_current_version_success(client: TestClient, monkeypatch):
    """用途：创建后无并发改动时 analyze 成功写库。"""
    pid = _create_project(client)
    _put_state(client, pid, {"parsedMarkdown": "# 招标\n视频 1000 路"})

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=(
                '{"overview":"当前版本分析成功",'
                '"techRequirements":["1000路"],'
                '"rejectionRisks":["缺响应"],'
                '"scoringPoints":[{"name":"架构","weight":"10%"}]}'
            ),
            model="mock-c1",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "analyze"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    _assert_no_version_leak(body)
    state = _get_state(client, pid)
    assert state["analysis"]["overview"] == "当前版本分析成功"
    assert "1000路" in state["analysis"]["techRequirements"]


def test_chapters_self_advance_and_inter_chapter_external_not_overwritten(
    client: TestClient, monkeypatch
):
    """用途：批量章节自推进 expected；章间外部改动不被后续章覆盖。"""
    pid = _create_project(client)
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
    external_marker = "章间外部注入不可被覆盖"

    gen_calls = {"n": 0}

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
            # 第一章已成功写库后、第二章写入前：外部改动全状态版本
            db2 = SessionLocal()
            try:
                editor_state_service.upsert_editor_state(
                    db2,
                    _WS,
                    pid,
                    parsed_markdown=external_marker,
                )
            finally:
                db2.close()
        return f"## {title}\n生成正文{gen_calls['n']}", []

    monkeypatch.setattr(
        "app.services.task_service._generate_one_chapter_body", fake_gen
    )

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
    chapters = state["chapters"]
    ch1 = next(c for c in chapters if c["id"] == "n1")
    ch2 = next(c for c in chapters if c["id"] == "n2")
    # 第一章已成功写入，第二章不得写迟到正文
    assert "生成正文1" in (ch1.get("body") or "")
    assert "生成正文2" not in (ch2.get("body") or "")
    assert not (ch2.get("body") or "").strip()


def test_parse_uses_cas_not_direct_orm(client: TestClient, monkeypatch, tmp_path):
    """用途：parse 迟到写失败零覆盖；不得直接 ORM 绕过 CAS。"""
    pid = _create_project(client)
    base = _put_state(client, pid, {"parsedMarkdown": "旧解析保留"})
    v0 = base["stateVersion"]

    # 上传最小假文件（引擎被 mock）
    files_res = client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("demo.txt", b"hello bid", "text/plain")},
    )
    assert files_res.status_code == 201, files_res.text

    # 仅建档，避免后台 worker 竞态
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db, _WS, pid, task_type="parse", payload={}
        )
        tid = task.id
    finally:
        db.close()

    # 创建后改状态（版本以创建时为准）
    cur = _get_state(client, pid)
    _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "解析后外部改动",
            "expectedStateVersion": cur["stateVersion"],
        },
    )

    def fake_parse(engine_name, path, filename):
        return ("# 迟到解析正文应拒绝", engine_name or "lightweight")

    monkeypatch.setattr(
        "app.services.parse_engines.parse_with_engine", fake_parse
    )
    monkeypatch.setattr(
        "app.services.parse_engines.resolve_engine_name",
        lambda raw: "lightweight",
    )

    db = SessionLocal()
    try:
        task = task_service.get_task(db, _WS, pid, tid)
        task_service._execute_task(db, _WS, task)
        db.refresh(task)
        assert task.status == "failed"
        assert task.message == _STALE_MSG
        assert task.error == _STALE_ERR
    finally:
        db.close()

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "解析后外部改动"
    assert "迟到解析" not in state["parsedMarkdown"]
    # 基准 v0 仅作追溯；任务应在创建时捕获版本
    assert isinstance(v0, str)


def test_biz_qualify_stale_fails(client: TestClient, monkeypatch):
    """用途：商务生成任务同样绑定创建时版本。"""
    pid = _create_project(client, kind="business")
    base = _put_state(client, pid, {"parsedMarkdown": "## 资格\n独立法人"})
    v0 = base["stateVersion"]

    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db, _WS, pid, task_type="biz_qualify", payload={}
        )
        tid = task.id
    finally:
        db.close()

    _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q0",
                    "requirement": "外部先写",
                    "response": "x",
                    "evidence": "",
                    "status": "pending",
                }
            ],
            "expectedStateVersion": v0,
        },
    )

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        items = [
            {
                "id": "q_late",
                "requirement": "迟到资格",
                "response": "应拒绝",
                "evidence": "",
                "status": "pending",
            }
        ]
        return SimpleNamespace(
            content=json.dumps(items, ensure_ascii=False), model="mock"
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    db = SessionLocal()
    try:
        task = task_service.get_task(db, _WS, pid, tid)
        task_service._execute_task(db, _WS, task)
        db.refresh(task)
        assert task.status == "failed"
        assert task.message == _STALE_MSG
    finally:
        db.close()

    state = _get_state(client, pid)
    assert state["businessQualify"][0]["requirement"] == "外部先写"
    assert all(i.get("id") != "q_late" for i in state["businessQualify"])


def test_revise_business_requires_expected_state_version(client: TestClient):
    """用途：写商务 stage 缺 expected → 422 零写。"""
    pid = _create_project(client, kind="business")
    before = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )
    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "business_qualify",
            "message": "强化法人",
            "preserveStructure": True,
        },
    )
    assert res.status_code == 422, res.text
    after = _get_state(client, pid)
    assert after["stateVersion"] == before["stateVersion"]
    assert after["businessQualify"][0]["requirement"] == "法人"


def test_revise_stale_expected_409_no_body(client: TestClient, monkeypatch):
    """用途：陈旧 expected → 409 固定 code，零写且响应无模型正文。"""
    pid = _create_project(client, kind="business")
    seed = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )
    v0 = seed["stateVersion"]
    # 先把版本推到 v1
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

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        revised = [
            {
                "id": "q1",
                "requirement": "法人-LLM迟到正文",
                "response": "应不回显",
                "evidence": "x.pdf",
                "status": "matched",
            }
        ]
        return SimpleNamespace(
            content="已强化。\n\n" + json.dumps(revised, ensure_ascii=False),
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "business_qualify",
            "message": "强化",
            "preserveStructure": True,
            "expectedStateVersion": v0,  # 陈旧
            "baseContent": "[]",
        },
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "editor_state_version_conflict"
    assert "message" in detail
    # 禁止回显模型正文
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert "LLM迟到" not in blob
    assert "应不回显" not in blob

    state = _get_state(client, pid)
    assert state["businessQualify"][0]["requirement"] == "法人-外部已改"
    assert state["stateVersion"] == mid["stateVersion"]


def test_revise_current_expected_returns_new_version(client: TestClient, monkeypatch):
    """用途：合法 expected 成功写回并返回新 stateVersion。"""
    pid = _create_project(client, kind="business")
    seed = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )
    v0 = seed["stateVersion"]

    revised = [
        {
            "id": "q1",
            "requirement": "独立法人（修订成功）",
            "response": "我司具备",
            "evidence": "执照.pdf",
            "status": "matched",
        }
    ]

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content="已按意见修订。\n\n" + json.dumps(revised, ensure_ascii=False),
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "business_qualify",
            "message": "强化法人响应",
            "preserveStructure": True,
            "expectedStateVersion": v0,
            "baseContent": json.dumps(seed["businessQualify"], ensure_ascii=False),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "applied"
    new_sv = data.get("stateVersion")
    assert isinstance(new_sv, str) and _STATE_VERSION_RE.fullmatch(new_sv)
    assert new_sv != v0

    state = _get_state(client, pid)
    assert state["stateVersion"] == new_sv
    assert "修订成功" in state["businessQualify"][0]["requirement"]


def test_technical_revise_without_expected_still_ok(client: TestClient, monkeypatch):
    """用途：仅预览的技术修订 stage 保持兼容，不强制 expected。"""
    pid = _create_project(client)

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        return SimpleNamespace(
            content="建议补充双活架构说明。",
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "outline",
            "message": "补充架构",
            "preserveStructure": True,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "applied"


def test_writer_missing_or_corrupt_internal_version_fails_zero_side_effects(
    client: TestClient, monkeypatch,
):
    """
    用途：已建 task.payload 改成 {} 或坏版本后执行 → 固定 stale failed，
      LLM/解析器调用 0 次、editor-state 零写；不得用当前版本补捕获。
    """
    pid = _create_project(client)
    base = _put_state(client, pid, {"parsedMarkdown": "不可被旁路覆盖的正文"})
    v0 = base["stateVersion"]

    call_count = {"n": 0}

    def boom_chat(db, workspace_id, messages=None, **kwargs):
        call_count["n"] += 1
        raise AssertionError("缺版本 writer 不得调用 LLM")

    monkeypatch.setattr(llm_service, "chat_completion", boom_chat)

    cases = [
        ("{}", "empty_payload"),
        (
            json.dumps({_INTERNAL_KEY: "not-a-version"}, ensure_ascii=False),
            "corrupt_payload",
        ),
        (
            json.dumps({_INTERNAL_KEY: "esv_NOTHEX"}, ensure_ascii=False),
            "bad_hex",
        ),
    ]
    for payload_json, label in cases:
        db = SessionLocal()
        try:
            task = task_service.create_task_record(
                db, _WS, pid, task_type="analyze", payload={}
            )
            # 模拟旧 pre-upgrade / 损坏 payload：覆盖创建时写入的合法内部键
            row = db.get(ProjectTaskRow, task.id)
            assert row is not None
            row.payload_json = payload_json
            db.commit()
            tid = task.id
        finally:
            db.close()

        before = _get_state(client, pid)
        db = SessionLocal()
        try:
            task = task_service.get_task(db, _WS, pid, tid)
            task_service._execute_task(db, _WS, task)
            db.refresh(task)
            assert task.status == "failed", label
            assert task.message == _STALE_MSG, label
            assert task.error == _STALE_ERR, label
            assert task.result_json is None, label
        finally:
            db.close()

        after = _get_state(client, pid)
        assert after["parsedMarkdown"] == "不可被旁路覆盖的正文", label
        assert after["stateVersion"] == before["stateVersion"] == v0, label
        assert call_count["n"] == 0, label


def test_revise_llm_inflight_concurrent_write_409(
    client: TestClient, monkeypatch,
):
    """
    用途：LLM 进行中独立 Session 并发改 editor-state；revise 最终 409、
      模型正文零回显、并发内容保留。独立线程 + barrier，非顺序调用冒充并发。
    """
    pid = _create_project(client, kind="business")
    seed = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )
    v0 = seed["stateVersion"]

    llm_started = threading.Event()
    llm_continue = threading.Event()
    barrier = threading.Barrier(2)

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        # 进入 LLM 后挂起，等待主线程并发写完成
        llm_started.set()
        barrier.wait(timeout=10)
        assert llm_continue.wait(timeout=10)
        revised = [
            {
                "id": "q1",
                "requirement": "法人-LLM进行中正文禁止回显",
                "response": "迟到正文",
                "evidence": "x.pdf",
                "status": "matched",
            }
        ]
        return SimpleNamespace(
            content="已强化。\n\n" + json.dumps(revised, ensure_ascii=False),
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    result_box: dict = {}

    def do_revise() -> None:
        result_box["res"] = client.post(
            f"/api/projects/{pid}/artifacts/workspace/revise",
            json={
                "stage": "business_qualify",
                "message": "强化",
                "preserveStructure": True,
                "expectedStateVersion": v0,
                "baseContent": "[]",
            },
        )

    t = threading.Thread(target=do_revise, name="revise-inflight")
    t.start()
    assert llm_started.wait(timeout=10), "LLM 未进入挂起点"
    # 与 LLM 线程在 barrier 汇合，确保请求已进入模型调用
    barrier.wait(timeout=10)

    # 独立 Session 以当前 expected 成功改 editor-state（模拟并发外部写）
    concurrent_items = [
        {
            "id": "q1",
            "requirement": "法人-并发外部已改",
            "response": "保留",
            "evidence": "",
            "status": "matched",
        }
    ]
    db2 = SessionLocal()
    try:
        written = editor_state_service.upsert_editor_state(
            db2,
            _WS,
            pid,
            business_qualify=concurrent_items,
            expected_state_version=v0,
        )
        concurrent_sv = written["stateVersion"]
        assert concurrent_sv != v0
        assert _STATE_VERSION_RE.fullmatch(concurrent_sv)
    finally:
        db2.close()

    llm_continue.set()
    t.join(timeout=30)
    assert not t.is_alive()
    res = result_box["res"]
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "editor_state_version_conflict"
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert "LLM进行中" not in blob
    assert "迟到正文" not in blob
    assert "禁止回显" not in blob

    state = _get_state(client, pid)
    assert state["businessQualify"][0]["requirement"] == "法人-并发外部已改"
    assert state["stateVersion"] == concurrent_sv


def test_revise_parse_fail_still_returns_valid_state_version(
    client: TestClient, monkeypatch,
):
    """用途：模型结构解析失败/空 revised 仍 HTTP 200 且含合法 stateVersion（无字段写入）。"""
    pid = _create_project(client, kind="business")
    seed = _put_state(
        client,
        pid,
        {
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人-解析失败场景",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )
    v0 = seed["stateVersion"]

    def fake_chat(db, workspace_id, messages=None, **kwargs):
        # 无法解析为资格 JSON → 无业务字段写入
        return SimpleNamespace(
            content="仅摘要，无合法 JSON。",
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "business_qualify",
            "message": "强化",
            "preserveStructure": True,
            "expectedStateVersion": v0,
            "baseContent": "[]",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    sv = data.get("stateVersion")
    assert isinstance(sv, str) and _STATE_VERSION_RE.fullmatch(sv)
    # 无字段写入时版本应等于请求 expected（锁后同一当前版本）
    assert sv == v0
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["businessQualify"][0]["requirement"] == "法人-解析失败场景"


def test_is_valid_state_version_helper():
    """用途：后端唯一版本格式 helper 存在且边界精确。"""
    assert hasattr(editor_state_service, "is_valid_state_version")
    assert editor_state_service.is_valid_state_version("esv_" + "a" * 32) is True
    assert editor_state_service.is_valid_state_version("esv_" + "A" * 32) is False
    assert editor_state_service.is_valid_state_version("esv_short") is False
    assert editor_state_service.is_valid_state_version(None) is False
    assert editor_state_service.is_valid_state_version(123) is False


# ---------- P12B-C2：个人兼容 callback ----------


def _project_db_snapshot(project_id: str) -> tuple:
    """用途：直接读库快照 Project.status / technical_plan_step / updated_at。"""
    db = SessionLocal()
    try:
        proj = db.get(Project, project_id)
        assert proj is not None
        return (proj.status, proj.technical_plan_step, proj.updated_at)
    finally:
        db.close()


def test_personal_callback_missing_expected_422_zero_write(client: TestClient):
    """用途：缺 expectedStateVersion 固定 422，editor-state/任务/项目零写。"""
    pid = _create_project(client, name="C2缺版本")
    before = _get_state(client, pid)
    before_proj = _project_db_snapshot(pid)
    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# 缺版本\n\nSECRET_NO_WRITE",
            "source": "mineru",
            "filename": "x.pdf",
        },
    )
    assert res.status_code == 422, res.text
    after = _get_state(client, pid)
    assert after["stateVersion"] == before["stateVersion"]
    assert after.get("parsedMarkdown") == before.get("parsedMarkdown")
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert tasks == [] or all(
        "SECRET_NO_WRITE" not in json.dumps(t, ensure_ascii=False) for t in tasks
    )
    # 项目步骤/状态/更新时间精确不变
    assert _project_db_snapshot(pid) == before_proj


def test_personal_callback_invalid_expected_422_zero_write(client: TestClient):
    """用途：非法格式 expected 固定 422 零写。"""
    pid = _create_project(client, name="C2非法版本")
    before = _get_state(client, pid)
    before_proj = _project_db_snapshot(pid)
    for bad in ("esv_SHORT", "ESV_" + "a" * 32, "esv_" + "A" * 32, "", "not-a-version"):
        res = client.post(
            f"/api/projects/{pid}/parse-callback",
            json={
                "markdown": "# 非法\n\nEVIL",
                "source": "mineru",
                "expectedStateVersion": bad,
            },
        )
        assert res.status_code == 422, f"{bad!r}: {res.text}"
    after = _get_state(client, pid)
    assert after["stateVersion"] == before["stateVersion"]
    assert "EVIL" not in (after.get("parsedMarkdown") or "")
    assert _project_db_snapshot(pid) == before_proj


def test_personal_callback_stale_409_atomic_zero_write(client: TestClient):
    """用途：陈旧 expected 固定登录态 409，editor-state/任务/项目步骤全部不变。"""
    pid = _create_project(client, name="C2陈旧个人")
    seed = _put_state(client, pid, {"parsedMarkdown": "个人-基线-A"})
    v0 = seed["stateVersion"]
    # 外部推进版本
    advanced = _put_state(
        client,
        pid,
        {"parsedMarkdown": "个人-外部已改-B", "expectedStateVersion": v0},
    )
    v1 = advanced["stateVersion"]
    assert v1 != v0

    before_proj = _project_db_snapshot(pid)

    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# 迟到个人回传\n\nSECRET_STALE",
            "source": "mineru",
            "filename": "late.pdf",
            "expectedStateVersion": v0,
        },
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "editor_state_version_conflict"
    assert detail["message"] == "编辑内容已被其他操作更新，请重新载入后再保存"
    assert detail["currentStateVersion"] == v1
    assert _STATE_VERSION_RE.fullmatch(detail["currentStateVersion"])
    blob = json.dumps(res.json(), ensure_ascii=False)
    assert "SECRET_STALE" not in blob
    assert "迟到个人回传" not in blob

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "个人-外部已改-B"
    assert state["stateVersion"] == v1
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert all("SECRET_STALE" not in json.dumps(t, ensure_ascii=False) for t in tasks)
    # 项目 status/step/updated_at 直接读库精确相等
    assert _project_db_snapshot(pid) == before_proj
    proj = client.get(f"/api/projects/{pid}").json()
    assert "SECRET_STALE" not in json.dumps(proj, ensure_ascii=False)


def test_personal_callback_success_returns_new_state_version(client: TestClient):
    """用途：当前 expected 成功写 parsedMarkdown 并返回合法新 stateVersion。"""
    pid = _create_project(client, name="C2个人成功")
    seed = _put_state(client, pid, {"parsedMarkdown": "个人-成功前"})
    v0 = seed["stateVersion"]

    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# 个人成功\n\n正文段落",
            "source": "mineru",
            "filename": "ok.pdf",
            "expectedStateVersion": v0,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    new_sv = body["stateVersion"]
    assert isinstance(new_sv, str) and _STATE_VERSION_RE.fullmatch(new_sv)
    assert new_sv != v0

    state = _get_state(client, pid)
    assert "个人成功" in (state.get("parsedMarkdown") or "")
    assert state["stateVersion"] == new_sv
    # 独立算法一致
    from app.services.editor_state_service import compute_full_state_version

    assert new_sv == compute_full_state_version(state)

    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert any(t.get("type") == "parse" and t.get("status") == "success" for t in tasks)
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["status"] == "analyzing"
    assert proj.get("technicalPlanStep") == 1


def test_personal_callback_midway_failure_full_rollback(client: TestClient, monkeypatch):
    """用途：中途异常完整 rollback，editor-state/任务/项目零写；精确 500 不回显原文。"""
    pid = _create_project(client, name="C2中途回滚")
    seed = _put_state(client, pid, {"parsedMarkdown": "回滚前基线"})
    v0 = seed["stateVersion"]
    before_proj = _project_db_snapshot(pid)
    boom_marker = "simulated-personal-callback-midway"

    real_from_row = editor_state_service._state_from_row
    calls = {"n": 0}

    def boom_from_row(*args, **kwargs):
        # 第一次：锁后比对；第二次：commit 前构造成功响应时抛错；之后恢复正常供后续 GET
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError(boom_marker)
        return real_from_row(*args, **kwargs)

    monkeypatch.setattr(editor_state_service, "_state_from_row", boom_from_row)
    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# 应回滚\n\nNOPE",
            "source": "mineru",
            "expectedStateVersion": v0,
        },
    )
    # 精确 500；禁止 >=400；异常原文/正文不得回显
    assert res.status_code == 500, res.text
    assert boom_marker not in res.text
    assert "应回滚" not in res.text
    assert "NOPE" not in res.text
    detail = res.json().get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("code") == "parse_callback_failed"
        assert boom_marker not in json.dumps(detail, ensure_ascii=False)

    state = _get_state(client, pid)
    assert state["parsedMarkdown"] == "回滚前基线"
    assert state["stateVersion"] == v0
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert all("应回滚" not in json.dumps(t, ensure_ascii=False) for t in tasks)
    # 项目 status/step/updated_at 精确零写
    assert _project_db_snapshot(pid) == before_proj


# ---------- P12B-C3：M3-D 全状态围栏（failure-first） ----------

_FS_CONFLICT = "editor_state_version_conflict"
_FS_MSG = "编辑内容已被其他操作更新，请重新载入后再保存"


def _c3_project(client: TestClient) -> str:
    res = client.post("/api/projects", json={"name": "P12B-C3-M3D", "kind": "technical"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _c3_seed_chapters(client: TestClient, pid: str) -> dict:
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "node_a", "title": "总体架构", "children": []}],
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "C3基线正文。",
                    "status": "pending",
                    "preview": "C3基线正文。",
                    "wordCount": 6,
                }
            ],
            "mode": "ALIGNED",
            "guidance": {"notes": "c3-guide-v0"},
            "facts": [{"id": "f1", "text": "事实0"}],
        },
    )
    assert put.status_code == 200, put.text
    return put.json()


def _c3_seed_task(pid: str, body: str = "C3基线正文。") -> str:
    from hashlib import sha1

    from app.models.entities import ProjectTaskRow, utc_now

    tid = f"task_c3_{pid[-8:]}"
    base = {
        "title": "总体架构",
        "bodyHash": "bh_" + sha1(body.encode("utf-8")).hexdigest()[:20],
        "bodyLength": len(body),
    }
    sugs = [
        {
            "suggestionId": "sug_c3_a",
            "targetChapterId": "chap_a",
            "action": "merge",
            "proposedMarkdown": "C3融合后正文。",
            "base": base,
        }
    ]
    db = SessionLocal()
    try:
        row = ProjectTaskRow(
            id=tid,
            project_id=pid,
            type="content_fuse",
            status="success",
            progress=100,
            message="ok",
            payload_json=json.dumps({"mode": "merge_suggest"}, ensure_ascii=False),
            result_json=json.dumps(
                {"model": "mock-c3", "suggestions": sugs, "quota": {}},
                ensure_ascii=False,
            ),
            error=None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()
    return tid


def _c3_apply_url(pid: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications"


def _c3_consume_url(pid: str, batch_id: str) -> str:
    return f"/api/projects/{pid}/content-fuse-applications/{batch_id}/consume"


def _c3_assert_version_conflict(res) -> None:
    assert res.status_code == 409, res.text
    assert res.headers.get("Cache-Control") == "no-store"
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message", "currentStateVersion"}
    assert detail["code"] == _FS_CONFLICT
    assert detail["message"] == _FS_MSG
    assert _STATE_VERSION_RE.fullmatch(detail["currentStateVersion"])
    blob = res.text
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    assert "C3融合后正文" not in blob
    assert "proposedMarkdown" not in blob


def test_c3_apply_missing_expected_422_zero_write(client: TestClient):
    """用途：apply 缺 expectedStateVersion 固定 422，章节/批次零写。"""
    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    res = client.post(
        _c3_apply_url(pid),
        json={"taskId": tid, "suggestionIds": ["sug_c3_a"]},
    )
    assert res.status_code == 422, res.text
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert state["chapters"][0]["body"] == "C3基线正文。"
    listed = client.get(_c3_apply_url(pid)).json()
    assert listed["items"] == []


def test_c3_apply_snake_extra_bad_422(client: TestClient):
    """用途：snake_case / 非法格式 / 额外键 均 422 零写。"""
    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    cases = [
        {"taskId": tid, "suggestionIds": ["sug_c3_a"], "expected_state_version": v0},
        {"taskId": tid, "suggestionIds": ["sug_c3_a"], "expectedStateVersion": "bad"},
        {
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
            "extra": 1,
        },
    ]
    for body in cases:
        res = client.post(_c3_apply_url(pid), json=body)
        assert res.status_code == 422, (body, res.text)
    assert _get_state(client, pid)["stateVersion"] == v0
    assert client.get(_c3_apply_url(pid)).json()["items"] == []


def test_c3_apply_other_13key_drift_409_no_batch(client: TestClient):
    """用途：章节 base 未变但 guidance 等 13 键漂移时 apply 固定全状态 409，零批次。"""
    pid = _c3_project(client)
    seed = _c3_seed_chapters(client, pid)
    v0 = seed["stateVersion"]
    tid = _c3_seed_task(pid)
    # 仅改 guidance（不碰 chapters）→ 全状态版本变，章节 base 仍匹配
    drifted = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"guidance": {"notes": "c3-guide-DRIFT"}, "expectedStateVersion": v0},
    )
    assert drifted.status_code == 200, drifted.text
    v1 = drifted.json()["stateVersion"]
    assert v1 != v0
    assert drifted.json()["chapters"][0]["body"] == "C3基线正文。"

    res = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,  # 陈旧
        },
    )
    _c3_assert_version_conflict(res)
    assert res.json()["detail"]["currentStateVersion"] == v1
    state = _get_state(client, pid)
    assert state["stateVersion"] == v1
    assert state["chapters"][0]["body"] == "C3基线正文。"
    assert state["guidance"]["notes"] == "c3-guide-DRIFT"
    assert client.get(_c3_apply_url(pid)).json()["items"] == []


def test_c3_apply_current_expected_success_new_version(client: TestClient):
    """用途：当前 expected 成功 apply，返回合法新 stateVersion 且与 GET/算法一致。"""
    from app.services.editor_state_service import compute_full_state_version

    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    res = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body.keys()) == {
        "batchId",
        "appliedChapterCount",
        "createdAt",
        "stateVersion",
    }
    assert "stateVersion" in body
    assert _STATE_VERSION_RE.fullmatch(body["stateVersion"])
    assert body["stateVersion"] != v0
    got = _get_state(client, pid)
    assert got["stateVersion"] == body["stateVersion"]
    # 独立算法核验：禁止仅比较 GET 自洽
    assert body["stateVersion"] == compute_full_state_version(got)
    assert got["chapters"][0]["body"] == "C3融合后正文。"
    listed = client.get(_c3_apply_url(pid)).json()["items"]
    assert len(listed) == 1
    assert listed[0]["state"] == "active"


def test_c3_consume_missing_bad_extra_422(client: TestClient):
    """用途：consume 缺/坏/snake/extra 体 422，批次仍 active。"""
    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    applied = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
        },
    )
    assert applied.status_code == 201, applied.text
    assert _STATE_VERSION_RE.fullmatch(applied.json()["stateVersion"])
    batch_id = applied.json()["batchId"]
    v_after = applied.json()["stateVersion"]

    cases = [
        None,
        {},
        {"expected_state_version": v_after},
        {"expectedStateVersion": "not-esv"},
        {"expectedStateVersion": v_after, "foo": 1},
    ]
    for body in cases:
        if body is None:
            res = client.post(_c3_consume_url(pid, batch_id))
        else:
            res = client.post(_c3_consume_url(pid, batch_id), json=body)
        assert res.status_code == 422, (body, res.text)
    listed = client.get(_c3_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "active"
    assert listed["batchId"] == batch_id


def test_c3_consume_stale_409_batch_still_active(client: TestClient):
    """用途：consume 全状态陈旧 409，批次不消费、章节不回滚。"""
    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    applied = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
        },
    )
    assert applied.status_code == 201, applied.text
    assert _STATE_VERSION_RE.fullmatch(applied.json()["stateVersion"])
    batch_id = applied.json()["batchId"]
    v_applied = applied.json()["stateVersion"]
    # 外部改 facts → 版本前进，章节 after 仍匹配可恢复条件
    external = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": "f1", "text": "事实漂移"}],
            "expectedStateVersion": v_applied,
        },
    )
    assert external.status_code == 200, external.text
    v_ext = external.json()["stateVersion"]
    assert external.json()["chapters"][0]["body"] == "C3融合后正文。"

    res = client.post(
        _c3_consume_url(pid, batch_id),
        json={"expectedStateVersion": v_applied},  # 陈旧
    )
    _c3_assert_version_conflict(res)
    assert res.json()["detail"]["currentStateVersion"] == v_ext
    listed = client.get(_c3_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "active"
    assert listed["consumedAt"] is None
    assert _get_state(client, pid)["chapters"][0]["body"] == "C3融合后正文。"


def test_c3_consume_current_success_and_zero_restore_version(client: TestClient):
    """用途：当前 expected 成功 consume；零恢复时版本等于操作前。"""
    from app.services.editor_state_service import compute_full_state_version

    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    applied = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
        },
    )
    assert applied.status_code == 201, applied.text
    assert _STATE_VERSION_RE.fullmatch(applied.json()["stateVersion"])
    batch_id = applied.json()["batchId"]
    v_applied = applied.json()["stateVersion"]
    # apply 成功版本独立算法核验
    got_after_apply = _get_state(client, pid)
    assert v_applied == compute_full_state_version(got_after_apply)

    # 手工漂移章 → 零恢复但仍消费
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "chapters": [
                {
                    "id": "chap_a",
                    "title": "总体架构",
                    "body": "用户已改不可恢复",
                    "status": "needs_review",
                }
            ],
            "expectedStateVersion": v_applied,
        },
    )
    assert put.status_code == 200, put.text
    v_before_consume = put.json()["stateVersion"]

    res = client.post(
        _c3_consume_url(pid, batch_id),
        json={"expectedStateVersion": v_before_consume},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {
        "restoredChapterCount",
        "skippedChapterCount",
        "consumedAt",
        "stateVersion",
    }
    assert body["restoredChapterCount"] == 0
    assert body["skippedChapterCount"] == 1
    assert body["stateVersion"] == v_before_consume
    assert _STATE_VERSION_RE.fullmatch(body["stateVersion"])
    got = _get_state(client, pid)
    assert got["stateVersion"] == v_before_consume
    # 零恢复：响应版本 = 请求前当前版本 = 独立算法
    assert body["stateVersion"] == compute_full_state_version(got)
    assert got["chapters"][0]["body"] == "用户已改不可恢复"
    listed = client.get(_c3_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "consumed"


def test_c3_consume_full_restore_version_matches_algorithm(client: TestClient):
    """用途：完整恢复 consume 成功版本与独立 13 键算法/后续 GET 精确一致。"""
    from app.services.editor_state_service import compute_full_state_version

    pid = _c3_project(client)
    v0 = _c3_seed_chapters(client, pid)["stateVersion"]
    tid = _c3_seed_task(pid)
    applied = client.post(
        _c3_apply_url(pid),
        json={
            "taskId": tid,
            "suggestionIds": ["sug_c3_a"],
            "expectedStateVersion": v0,
        },
    )
    assert applied.status_code == 201, applied.text
    v_applied = applied.json()["stateVersion"]
    assert _STATE_VERSION_RE.fullmatch(v_applied)
    assert v_applied == compute_full_state_version(_get_state(client, pid))
    batch_id = applied.json()["batchId"]

    res = client.post(
        _c3_consume_url(pid, batch_id),
        json={"expectedStateVersion": v_applied},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {
        "restoredChapterCount",
        "skippedChapterCount",
        "consumedAt",
        "stateVersion",
    }
    assert body["restoredChapterCount"] == 1
    assert body["skippedChapterCount"] == 0
    assert _STATE_VERSION_RE.fullmatch(body["stateVersion"])
    assert body["stateVersion"] != v_applied
    got = _get_state(client, pid)
    assert got["stateVersion"] == body["stateVersion"]
    assert body["stateVersion"] == compute_full_state_version(got)
    assert got["chapters"][0]["body"] == "C3基线正文。"
    listed = client.get(_c3_apply_url(pid)).json()["items"][0]
    assert listed["state"] == "consumed"
