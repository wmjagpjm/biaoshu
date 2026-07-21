"""
模块：V1-H1 章节生成空白正文质量门 failure-first 专项
用途：从真实任务路径证明空白模型输出不得写入 editor-state 且不得谎报成功；
  合法短章与带首尾空白的有效 Markdown 不得误杀或被裁剪。
对接：task_service._generate_one_chapter_body / _run_chapter / _run_chapters；
  POST /api/projects/{id}/tasks?sync=true；editor-state 读写。
二次开发：禁止 mock 掉判空门、复制生产 strip 逻辑、skip/xfail、真实 LLM 或源码扫描假绿。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from fastapi.testclient import TestClient

from app.services import editor_state_service, task_service
from app.services.llm_service import ChatResult

# 契约固定失败文案（与生产 ValueError 一致后才会全绿）
_FAIL_MSG = "任务失败"
_FAIL_ERR = "模型未返回有效章节正文，请重试"

# 合成锚点：全部为测试专用，禁止真实业务数据
_ANCHOR_EMPTY = ""
_ANCHOR_WS = " \n\t "
_ANCHOR_SHORT = "无。"
_ANCHOR_PADDED = "\n## V1H1有效章\n\n保留首尾空白的正文锚点。\n"
_ANCHOR_CH1 = "## 第一章有效锚点\n\n第一章已提交正文。"
_ANCHOR_CH3_SHOULD_NOT = "## 第三章不应生成\n\n若出现则假绿。"


def _create_project(client: TestClient, name: str) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": "technical"})
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


def _chapter(
    cid: str,
    title: str,
    *,
    body: str = "",
    status: str = "pending",
    target_words: int = 100,
) -> dict:
    return {
        "id": cid,
        "title": title,
        "body": body,
        "preview": (body[:96].replace("\n", " ") if body else ""),
        "wordCount": len(body.replace(" ", "")) if body else 0,
        "status": status,
        "targetWords": target_words,
    }


def _seed_single_chapter(client: TestClient, pid: str, *, title: str = "单章目标") -> dict:
    """用途：写入单章 pending 编辑态，返回完整 seed 快照。"""
    return _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "V1H1-单章基准",
            "analysisOverview": "V1H1合成概述",
            "outline": [{"id": "n1", "title": title, "children": []}],
            "chapters": [_chapter("n1", title)],
        },
    )


def _seed_three_chapters(client: TestClient, pid: str) -> dict:
    """用途：写入三章全部 pending 空正文，供多章空白门红测。"""
    return _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "V1H1-多章基准",
            "analysisOverview": "V1H1多章合成概述",
            "outline": [
                {"id": "n1", "title": "第一章", "children": []},
                {"id": "n2", "title": "第二章", "children": []},
                {"id": "n3", "title": "第三章", "children": []},
            ],
            "chapters": [
                _chapter("n1", "第一章"),
                _chapter("n2", "第二章"),
                _chapter("n3", "第三章"),
            ],
        },
    )


def _chapter_snapshot(state: dict) -> list[dict[str, Any]]:
    """用途：抽取章节可比字段，避免无关键抖动。"""
    out: list[dict[str, Any]] = []
    for c in state.get("chapters") or []:
        assert isinstance(c, dict), c
        out.append(
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "body": c.get("body"),
                "status": c.get("status"),
                "wordCount": c.get("wordCount"),
                "preview": c.get("preview"),
            }
        )
    return out


def _full_state_fingerprint(state: dict) -> dict[str, Any]:
    """用途：失败路径前后完整对照 body/status/version。"""
    return {
        "stateVersion": state.get("stateVersion"),
        "chapters": _chapter_snapshot(state),
        "parsedMarkdown": state.get("parsedMarkdown"),
        "analysisOverview": state.get("analysisOverview"),
    }


def _install_chat_sequence(
    monkeypatch,
    contents: list[str] | Callable[[int, str], str],
) -> list[dict[str, Any]]:
    """
    用途：按调用序返回合成模型正文；同时精确记录 chat 调用序。
    说明：不替换 _generate_one_chapter_body，确保生产判空门可被真实执行路径命中。
    """
    chat_log: list[dict[str, Any]] = []

    def fake_chat(
        db,
        workspace_id,
        *,
        messages,
        temperature=0.4,
        timeout_sec=120.0,
    ):
        idx = len(chat_log)
        # 从 user 消息抽取章节标题，便于多章序列断言
        title = ""
        if messages and isinstance(messages, list):
            user = messages[-1].get("content") if isinstance(messages[-1], dict) else ""
            if isinstance(user, str) and "章节标题：" in user:
                line = user.split("\n", 1)[0]
                title = line.replace("章节标题：", "", 1).strip()
        if callable(contents):
            text = contents(idx, title)
        else:
            assert idx < len(contents), (
                f"chat_completion 超额调用 index={idx} title={title!r} log={chat_log}"
            )
            text = contents[idx]
        chat_log.append({"index": idx, "title": title, "content": text})
        return ChatResult(content=text, model="mock-v1h1")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    return chat_log


def _install_gen_call_recorder(monkeypatch) -> list[str]:
    """
    用途：包装真实 _generate_one_chapter_body，仅记录 title 调用序列，不改返回值/异常。
    禁止在包装器内做 strip 判空，避免复制生产逻辑。
    """
    real = task_service._generate_one_chapter_body
    gen_titles: list[str] = []

    def wrapper(
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
        gen_titles.append(str(title))
        return real(
            db,
            workspace_id,
            title=title,
            target_words=target_words,
            overview=overview,
            facts_txt=facts_txt,
            analysis_block=analysis_block,
            project_id=project_id,
        )

    monkeypatch.setattr(
        "app.services.task_service._generate_one_chapter_body", wrapper
    )
    return gen_titles


def _install_upsert_spy(monkeypatch) -> list[dict[str, Any]]:
    """用途：记录任务路径每次 editor-state CAS 写入快照（章节 id/status/body）。"""
    real = editor_state_service.upsert_editor_state
    writes: list[dict[str, Any]] = []

    def spy(*args, **kwargs):
        out = real(*args, **kwargs)
        chapters = out.get("chapters") or []
        writes.append(
            {
                "stateVersion": out.get("stateVersion"),
                "chapters": [
                    {
                        "id": c.get("id"),
                        "status": c.get("status"),
                        "body": c.get("body"),
                    }
                    for c in chapters
                    if isinstance(c, dict)
                ],
            }
        )
        return out

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy)
    return writes


def _assert_failed_empty_body(task_body: dict) -> None:
    """用途：空白门失败任务固定三态断言。"""
    assert task_body["status"] == "failed", task_body
    assert task_body["message"] == _FAIL_MSG, task_body
    assert task_body["error"] == _FAIL_ERR, task_body
    # 失败面不得回显堆栈/SQL/内部版本
    blob = json.dumps(task_body, ensure_ascii=False)
    low = blob.lower()
    assert "traceback" not in low, blob
    assert "esv_" not in blob, blob
    assert "_expectedStateVersion" not in blob, blob


# ---------------------------------------------------------------------------
# 1) 单章空串
# ---------------------------------------------------------------------------


def test_single_chapter_empty_string_fails_zero_write(client: TestClient, monkeypatch):
    """单章模型返回空串：failed + 固定错误 + editor-state 完整不变。"""
    pid = _create_project(client, "V1H1-单章空串")
    seed = _seed_single_chapter(client, pid)
    before = _full_state_fingerprint(seed)
    before_copy = copy.deepcopy(before)

    chat_log = _install_chat_sequence(monkeypatch, [_ANCHOR_EMPTY])
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    _assert_failed_empty_body(body)

    # 生成函数恰好一次；不得有 CAS 写入
    assert gen_titles == ["单章目标"], gen_titles
    assert len(chat_log) == 1, chat_log
    assert chat_log[0]["content"] == _ANCHOR_EMPTY
    assert writes == [], writes

    after = _full_state_fingerprint(_get_state(client, pid))
    assert after == before_copy
    ch = next(c for c in after["chapters"] if c["id"] == "n1")
    assert ch["body"] == ""
    assert ch["status"] == "pending"
    assert after["stateVersion"] == before_copy["stateVersion"]


# ---------------------------------------------------------------------------
# 2) 单章混合空白（独立用例，不与空串合并）
# ---------------------------------------------------------------------------


def test_single_chapter_whitespace_only_fails_zero_write(
    client: TestClient, monkeypatch
):
    """单章返回混合空白：与空串同义 failed，状态完整不变。"""
    pid = _create_project(client, "V1H1-单章混合空白")
    seed = _seed_single_chapter(client, pid, title="空白目标章")
    before = _full_state_fingerprint(seed)
    before_copy = copy.deepcopy(before)

    chat_log = _install_chat_sequence(monkeypatch, [_ANCHOR_WS])
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    _assert_failed_empty_body(body)

    assert gen_titles == ["空白目标章"], gen_titles
    assert len(chat_log) == 1, chat_log
    assert chat_log[0]["content"] == _ANCHOR_WS
    assert writes == [], writes

    after = _full_state_fingerprint(_get_state(client, pid))
    assert after == before_copy
    ch = next(c for c in after["chapters"] if c["id"] == "n1")
    assert ch["body"] == ""
    assert ch["status"] == "pending"
    assert after["stateVersion"] == before_copy["stateVersion"]


# ---------------------------------------------------------------------------
# 3) 多章首章空白：零写入、后续章生成零调用
# ---------------------------------------------------------------------------


def test_multi_first_chapter_empty_zero_write_no_followup(
    client: TestClient, monkeypatch
):
    """多章首章空白：failed、零 editor-state 写入、后续章生成函数零调用。"""
    pid = _create_project(client, "V1H1-多章首章空白")
    seed = _seed_three_chapters(client, pid)
    before = _full_state_fingerprint(seed)
    before_copy = copy.deepcopy(before)

    # 生产若继续推进会再要第二/三章正文；统一给空白以暴露“继续生成并写入”真缺口，
    # 禁止因 mock 序列耗尽抛 AssertionError 冒充空白门红测。
    chat_log = _install_chat_sequence(
        monkeypatch, [_ANCHOR_EMPTY, _ANCHOR_EMPTY, _ANCHOR_EMPTY]
    )
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)
    monkeypatch.setattr("app.services.task_service.time.sleep", lambda *_a, **_k: None)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapters", "payload": {"onlyEmpty": True}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    _assert_failed_empty_body(body)

    # 仅第一章进入生成；第二章/第三章不得调用；零 CAS
    assert gen_titles == ["第一章"], gen_titles
    assert len(chat_log) == 1, chat_log
    assert writes == [], writes

    after = _full_state_fingerprint(_get_state(client, pid))
    assert after == before_copy
    for cid in ("n1", "n2", "n3"):
        ch = next(c for c in after["chapters"] if c["id"] == cid)
        assert ch["body"] == ""
        assert ch["status"] == "pending"
    assert after["stateVersion"] == before_copy["stateVersion"]


# ---------------------------------------------------------------------------
# 4) 多章：第一章有效、第二章空白、第三章不得调用；保留逐章 CAS 前缀
# ---------------------------------------------------------------------------


def test_multi_second_chapter_empty_keeps_first_prefix_only(
    client: TestClient, monkeypatch
):
    """
    多章中途空白：failed；第一章仅一次 CAS 且 needs_review；
    第二/三章原样 pending；第三章零调用；不得错误断言全书回滚。
    """
    pid = _create_project(client, "V1H1-多章中途空白")
    seed = _seed_three_chapters(client, pid)
    v0 = seed["stateVersion"]
    assert isinstance(v0, str) and v0.startswith("esv_"), v0

    def content_for(idx: int, title: str) -> str:
        if idx == 0:
            return _ANCHOR_CH1
        if idx == 1:
            return _ANCHOR_EMPTY
        # 若生产继续调用第三章，返回可识别锚点以便断言失败更清晰
        return _ANCHOR_CH3_SHOULD_NOT

    chat_log = _install_chat_sequence(monkeypatch, content_for)
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)
    monkeypatch.setattr("app.services.task_service.time.sleep", lambda *_a, **_k: None)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapters", "payload": {"onlyEmpty": True}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    _assert_failed_empty_body(body)

    # 调用序列：第一章 + 第二章；第三章不得出现
    assert gen_titles == ["第一章", "第二章"], gen_titles
    assert len(chat_log) == 2, chat_log
    assert chat_log[0]["content"] == _ANCHOR_CH1
    assert chat_log[1]["content"] == _ANCHOR_EMPTY

    # 恰好一次 CAS 落盘（第一章）
    assert len(writes) == 1, writes
    w0 = writes[0]
    assert w0["stateVersion"] != v0
    ch1_w = next(c for c in w0["chapters"] if c["id"] == "n1")
    assert ch1_w["body"] == _ANCHOR_CH1
    assert ch1_w["status"] == "needs_review"
    ch2_w = next(c for c in w0["chapters"] if c["id"] == "n2")
    assert ch2_w["body"] == ""
    assert ch2_w["status"] == "pending"
    ch3_w = next(c for c in w0["chapters"] if c["id"] == "n3")
    assert ch3_w["body"] == ""
    assert ch3_w["status"] == "pending"

    state = _get_state(client, pid)
    assert state["stateVersion"] == w0["stateVersion"]
    # 前缀保留：第一章成功提交，不得全书回滚到 v0
    assert state["stateVersion"] != v0
    ch1 = next(c for c in state["chapters"] if c["id"] == "n1")
    ch2 = next(c for c in state["chapters"] if c["id"] == "n2")
    ch3 = next(c for c in state["chapters"] if c["id"] == "n3")
    assert ch1["body"] == _ANCHOR_CH1
    assert ch1["status"] == "needs_review"
    assert ch2["body"] == ""
    assert ch2["status"] == "pending"
    assert ch3["body"] == ""
    assert ch3["status"] == "pending"
    # 第三章正文不得出现“应不生成”锚点
    assert _ANCHOR_CH3_SHOULD_NOT not in (ch3.get("body") or "")


# ---------------------------------------------------------------------------
# 5) 合法短章「无。」：不得最小字数误杀
# ---------------------------------------------------------------------------


def test_single_chapter_short_legal_body_success(client: TestClient, monkeypatch):
    """单章返回「无。」：success、正文精确保留、chars=2。"""
    pid = _create_project(client, "V1H1-合法短章")
    seed = _seed_single_chapter(client, pid, title="短章目标")
    v0 = seed["stateVersion"]

    chat_log = _install_chat_sequence(monkeypatch, [_ANCHOR_SHORT])
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    assert body.get("error") in (None, ""), body
    result = body.get("result") or {}
    assert result.get("chars") == 2, result
    assert result.get("chapterId") == "n1"
    assert gen_titles == ["短章目标"], gen_titles
    assert len(chat_log) == 1
    assert len(writes) == 1, writes

    state = _get_state(client, pid)
    assert state["stateVersion"] != v0
    ch = next(c for c in state["chapters"] if c["id"] == "n1")
    assert ch["body"] == _ANCHOR_SHORT
    assert ch["status"] == "needs_review"


# ---------------------------------------------------------------------------
# 6) 带首尾空白的有效 Markdown：仅判空不裁剪
# ---------------------------------------------------------------------------


def test_single_chapter_padded_markdown_preserved(client: TestClient, monkeypatch):
    """单章返回带首尾空白的有效 Markdown：success 且原字符串完整保留。"""
    pid = _create_project(client, "V1H1-首尾空白有效")
    seed = _seed_single_chapter(client, pid, title="填充目标章")
    v0 = seed["stateVersion"]

    chat_log = _install_chat_sequence(monkeypatch, [_ANCHOR_PADDED])
    gen_titles = _install_gen_call_recorder(monkeypatch)
    writes = _install_upsert_spy(monkeypatch)

    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    result = body.get("result") or {}
    assert result.get("chars") == len(_ANCHOR_PADDED), result
    assert gen_titles == ["填充目标章"], gen_titles
    assert len(chat_log) == 1
    assert chat_log[0]["content"] == _ANCHOR_PADDED
    assert len(writes) == 1, writes

    state = _get_state(client, pid)
    assert state["stateVersion"] != v0
    ch = next(c for c in state["chapters"] if c["id"] == "n1")
    # 关键相等：证明未 strip 裁剪
    assert ch["body"] == _ANCHOR_PADDED
    assert ch["body"].startswith("\n")
    assert ch["body"].endswith("\n")
    assert ch["status"] == "needs_review"
