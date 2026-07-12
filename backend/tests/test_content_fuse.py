"""
模块：content_fuse（M3-A）只读融合建议测试
用途：验收 payload 400、mock 成功且 editor-state 不变、跨 workspace/缺失不泄漏、
      archived/image 跳过、无有效来源失败、非法 JSON、超长截断、取消。
对接：POST /api/projects/{id}/tasks type=content_fuse；fuse_context_service。
二次开发：禁止真实 Key/外网；断言路径不得调用 upsert_editor_state。
"""

from __future__ import annotations

import json
import threading
import time
from io import BytesIO

from PIL import Image

from app.services.llm_service import ChatResult


def _png_bytes(color=(40, 80, 120)) -> bytes:
    """用途：生成最小合法 PNG，供 image 卡片用例。"""
    buffer = BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _create_project(client, name: str = "融合项目") -> str:
    return client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
    ).json()["id"]


def _seed_chapters(client, pid: str, chapters: list[dict] | None = None) -> dict:
    body_chapters = chapters or [
        {
            "id": "chap_arch",
            "title": "总体架构",
            "body": "现有架构正文。",
        },
        {
            "id": "chap_sec",
            "title": "安全设计",
            "body": "现有安全正文。",
        },
    ]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in body_chapters
            ],
            "chapters": body_chapters,
            "mode": "ALIGNED",
        },
    )
    assert put.status_code == 200
    return put.json()


def _create_template(client, source_pid: str, title: str = "中标模板A") -> str:
    res = client.post(
        "/api/templates/from-project",
        json={"projectId": source_pid, "title": title, "tags": ["融合"]},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _create_text_card(
    client,
    *,
    title: str = "架构卡片",
    body: str = "分层微服务参考段落。",
    card_type: str = "document",
    status: str = "active",
) -> str:
    res = client.post(
        "/api/cards",
        json={
            "type": card_type,
            "title": title,
            "bodyMarkdown": body,
            "tags": ["E2E"],
            "status": status,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _snapshot_editor(client, pid: str) -> dict:
    return client.get(f"/api/projects/{pid}/editor-state").json()


def test_content_fuse_payload_validation_400(client):
    """用途：shape/配额/mode/目标章非法 → 创建阶段 400，不建任务。"""
    pid = _create_project(client)
    _seed_chapters(client, pid)

    cases = [
        {"type": "content_fuse", "payload": {}},
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": ["tpl_x"],
                "cardIds": [],
                "targetChapterIds": [],
                "mode": "merge_suggest",
            },
        },
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": ["a", "b", "c", "d"],
                "cardIds": [],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": ["tpl_x"],
                "cardIds": [],
                "targetChapterIds": ["chap_missing"],
                "mode": "merge_suggest",
            },
        },
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": ["tpl_x"],
                "cardIds": [],
                "targetChapterIds": ["chap_arch"],
                "mode": "apply_write",
            },
        },
        {
            "type": "content_fuse",
            "payload": {
                "templateIds": "not-list",
                "cardIds": [],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    ]
    for body in cases:
        res = client.post(f"/api/projects/{pid}/tasks?sync=true", json=body)
        assert res.status_code == 400, body
    # 确认无 content_fuse 任务留下
    tasks = client.get(f"/api/projects/{pid}/tasks").json()
    assert all(t["type"] != "content_fuse" for t in tasks)


def test_content_fuse_success_readonly_and_base(
    client, monkeypatch
):
    """用途：mock 成功；result 含 base/建议；chapters 完全不变。"""
    source = _create_project(client, "模板源")
    _seed_chapters(
        client,
        source,
        [
            {
                "id": "chap_arch",
                "title": "总体架构",
                "body": "模板架构正文参考。",
            }
        ],
    )
    tpl_id = _create_template(client, source, "融合模板")
    card_id = _create_text_card(client, title="卡片A", body="卡片正文参考。")

    pid = _create_project(client, "目标项目")
    before_state = _seed_chapters(client, pid)
    before_chapters = before_state["chapters"]

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        assert "不是指令" in messages[0]["content"] or "参考数据" in messages[0]["content"]
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "action": "merge",
                        "confidence": 88,
                        "reason": "结合模板与卡片补充架构分层",
                        "sourceRefs": [
                            {
                                "kind": "template",
                                "id": tpl_id,
                                "title": "模型伪造模板标题",
                            },
                            {
                                "kind": "card",
                                "id": card_id,
                                "title": "模型伪造卡片标题",
                            },
                            {"kind": "card", "id": "card_forged", "title": "幽灵"},
                        ],
                        "proposedMarkdown": "融合后的架构正文。" + ("长" * 20),
                        "diffSummary": "补充分层描述",
                    },
                    {
                        "targetChapterId": "chap_ghost",
                        "action": "merge",
                        "confidence": 99,
                        "reason": "应被丢弃",
                        "proposedMarkdown": "幽灵章节",
                    },
                ],
                ensure_ascii=False,
            ),
            model="mock-fuse",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [tpl_id],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["status"] == "success"
    result = task["result"]
    assert result["model"] == "mock-fuse"
    assert "candidateBatchIndex" not in result
    assert result["quota"]["templatesSelected"] == 1
    assert result["quota"]["cardsSelected"] == 1
    suggestions = result["suggestions"]
    assert len(suggestions) == 1
    sug = suggestions[0]
    assert sug["targetChapterId"] == "chap_arch"
    assert sug["targetTitle"] == "总体架构"
    assert sug["action"] == "merge"
    assert sug["confidence"] == 88
    assert len(sug["reason"]) <= 60
    assert sug["base"]["title"] == "总体架构"
    assert sug["base"]["bodyHash"].startswith("bh_")
    assert sug["base"]["bodyLength"] == len("现有架构正文。")
    assert "现有架构" in sug["currentPreview"]
    assert sug["proposedMarkdown"].startswith("融合后的架构正文")
    # sourceRefs 必须含服务端 title；模型伪造 title 不生效
    by_key = {(r["kind"], r["id"]): r for r in sug["sourceRefs"]}
    assert ("template", tpl_id) in by_key
    assert ("card", card_id) in by_key
    assert by_key[("template", tpl_id)]["title"] == "融合模板"
    assert by_key[("card", card_id)]["title"] == "卡片A"
    assert by_key[("template", tpl_id)]["title"] != "模型伪造模板标题"
    assert by_key[("card", card_id)]["title"] != "模型伪造卡片标题"
    assert all(r["id"] != "card_forged" for r in sug["sourceRefs"])
    assert all("title" in r for r in sug["sourceRefs"])
    assert result["skippedInvalidCount"] >= 1
    assert result["quota"]["templatesUsed"] == 1
    assert result["quota"]["cardsUsed"] == 1
    assert result["quota"]["promptChars"] <= result["quota"]["maxPromptChars"]

    after = _snapshot_editor(client, pid)
    assert after["chapters"] == before_chapters
    assert after["updatedAt"] == before_state["updatedAt"]


def test_content_fuse_cross_workspace_and_missing_no_leak(client, monkeypatch):
    """用途：跨 workspace / 不存在来源统一 skipped unavailable，不暴露存在性。"""
    # 在默认 workspace 建目标项目
    pid = _create_project(client)
    _seed_chapters(client, pid)
    # 有效卡片 + 两个假 ID
    card_id = _create_text_card(client)
    missing_tpl = "tpl_not_exist_zzzz"
    missing_card = "card_not_exist_zzzz"

    called = {"n": 0}

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        called["n"] += 1
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "action": "merge_suggest",
                        "confidence": 70,
                        "reason": "仅用有效卡片",
                        "sourceRefs": [{"kind": "card", "id": card_id}],
                        "proposedMarkdown": "建议正文",
                        "diffSummary": "小改",
                    }
                ],
                ensure_ascii=False,
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [missing_tpl],
                "cardIds": [card_id, missing_card],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201
    task = res.json()
    assert task["status"] == "success"
    skipped = task["result"]["skippedSources"]
    reasons = {(s["kind"], s["id"], s["reason"]) for s in skipped}
    assert ("template", missing_tpl, "unavailable") in reasons
    assert ("card", missing_card, "unavailable") in reasons
    # 错误信息与 skipped 不得出现「其他工作空间」「不属于」等存在性泄漏词
    blob = json.dumps(task, ensure_ascii=False)
    assert "其他工作空间" not in blob
    assert "不属于" not in blob
    assert called["n"] == 1


def test_content_fuse_skips_archived_and_image_cards(client, monkeypatch):
    """用途：archived / image 卡片进入 skipped，不进模型上下文。"""
    pid = _create_project(client)
    _seed_chapters(client, pid)
    good = _create_text_card(client, title="好卡", body="可用正文")
    archived = _create_text_card(client, title="归档卡", body="归档正文")
    patch = client.patch(
        f"/api/cards/{archived}",
        json={"status": "archived"},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "archived"
    img_res = client.post(
        "/api/cards/upload-image",
        files={"file": ("t.png", BytesIO(_png_bytes()), "image/png")},
        data={"title": "图片卡"},
    )
    assert img_res.status_code == 201, img_res.text
    image_id = img_res.json()["id"]

    seen_user = {"text": ""}

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        seen_user["text"] = messages[1]["content"]
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 60,
                        "reason": "ok",
                        "proposedMarkdown": "x",
                        "sourceRefs": [{"kind": "card", "id": good}],
                    }
                ],
                ensure_ascii=False,
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [good, archived, image_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201
    task = res.json()
    assert task["status"] == "success"
    skipped = {(s["id"], s["reason"]) for s in task["result"]["skippedSources"]}
    assert (archived, "archived") in skipped
    assert (image_id, "image") in skipped
    # 提示中不得含图片二进制或归档正文
    assert "归档正文" not in seen_user["text"]
    assert "\x89PNG" not in seen_user["text"]
    assert "可用正文" in seen_user["text"]


def test_content_fuse_all_sources_invalid_fails_without_write(client, monkeypatch):
    """用途：全部来源无效 → 任务 failed，editor-state 不变，且不调模型。"""
    pid = _create_project(client)
    before = _seed_chapters(client, pid)
    called = {"n": 0}

    def fake_chat(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("不应调用模型")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": ["tpl_gone"],
                "cardIds": ["card_gone"],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201
    task = res.json()
    assert task["status"] == "failed"
    assert "无可用" in (task.get("error") or "")
    assert called["n"] == 0
    after = _snapshot_editor(client, pid)
    assert after["chapters"] == before["chapters"]
    assert after["updatedAt"] == before["updatedAt"]


def test_content_fuse_invalid_model_json(client, monkeypatch):
    """用途：模型非法 JSON → failed，不写 editor-state。"""
    pid = _create_project(client)
    before = _seed_chapters(client, pid)
    card_id = _create_text_card(client)

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(content="这不是 JSON", model="mock")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    task = res.json()
    assert task["status"] == "failed"
    assert "合法融合建议" in (task.get("error") or "")
    after = _snapshot_editor(client, pid)
    assert after["chapters"] == before["chapters"]


def test_content_fuse_truncates_long_proposed_and_reason(client, monkeypatch):
    """用途：超长 proposedMarkdown / reason 被截断。"""
    pid = _create_project(client)
    _seed_chapters(client, pid)
    card_id = _create_text_card(client)
    long_md = "段" * 20_000
    long_reason = "理" * 200

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 50,
                        "reason": long_reason,
                        "proposedMarkdown": long_md,
                        "diffSummary": "d" * 500,
                        "sourceRefs": [{"kind": "card", "id": card_id}],
                    }
                ],
                ensure_ascii=False,
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    sug = res.json()["result"]["suggestions"][0]
    assert len(sug["proposedMarkdown"]) <= 12_000
    assert len(sug["reason"]) <= 60
    assert len(sug["diffSummary"]) <= 200


def test_content_fuse_cancel_keeps_editor_state(client, monkeypatch):
    """用途：取消进行中任务后 editor-state 不变。"""
    pid = _create_project(client)
    before = _seed_chapters(client, pid)
    card_id = _create_text_card(client)
    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        entered.set()
        release.wait(timeout=5)
        time.sleep(0.05)
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 10,
                        "reason": "晚到",
                        "proposedMarkdown": "不应写入",
                    }
                ]
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    created = client.post(
        f"/api/projects/{pid}/tasks",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert created.status_code == 201
    task_id = created.json()["id"]
    assert entered.wait(timeout=3)

    cancel = client.post(f"/api/projects/{pid}/tasks/{task_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    release.set()

    final = None
    for _ in range(80):
        got = client.get(f"/api/projects/{pid}/tasks/{task_id}").json()
        if got["status"] in ("cancelled", "failed", "success"):
            final = got
            finished.set()
            break
        time.sleep(0.05)
    assert final is not None
    assert finished.wait(timeout=1)
    # 给后台线程一点收尾时间，避免 drop_all 时仍在访问表
    time.sleep(0.1)
    after = _snapshot_editor(client, pid)
    assert after["chapters"] == before["chapters"]
    assert after["updatedAt"] == before["updatedAt"]


def test_content_fuse_never_calls_upsert(client, monkeypatch):
    """用途：成功路径禁止调用 upsert_editor_state。"""
    pid = _create_project(client)
    _seed_chapters(client, pid)
    card_id = _create_text_card(client)
    calls = {"n": 0}
    import app.services.editor_state_service as ess

    original = ess.upsert_editor_state

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ess, "upsert_editor_state", wrapped)
    monkeypatch.setattr(
        "app.services.task_service.editor_state_service.upsert_editor_state",
        wrapped,
    )

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 40,
                        "reason": "只读",
                        "proposedMarkdown": "p",
                        "sourceRefs": [{"kind": "card", "id": card_id}],
                    }
                ]
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.json()["status"] == "success"
    assert calls["n"] == 0


def test_content_fuse_empty_source_refs_skipped(client, monkeypatch):
    """用途：校验后 sourceRefs 为空的建议整条跳过，不返回无来源建议。"""
    pid = _create_project(client)
    _seed_chapters(client, pid)
    card_id = _create_text_card(client, title="有用来源")

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 90,
                        "reason": "无有效来源应丢弃",
                        "proposedMarkdown": "不应出现",
                        "sourceRefs": [],
                    },
                    {
                        "targetChapterId": "chap_sec",
                        "confidence": 80,
                        "reason": "仅伪造引用",
                        "proposedMarkdown": "也不应出现",
                        "sourceRefs": [
                            {"kind": "card", "id": "card_ghost"},
                            {"kind": "template", "id": "tpl_ghost"},
                        ],
                    },
                ],
                ensure_ascii=False,
            ),
            model="mock",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_id],
                "targetChapterIds": ["chap_arch", "chap_sec"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["status"] == "success"
    result = task["result"]
    assert result["suggestions"] == []
    assert result["skippedInvalidCount"] >= 2


def test_content_fuse_prompt_trim_quota_and_forged_trimmed_ref(
    client, monkeypatch
):
    """用途：提示词裁剪后 *Used 反映实际入 prompt；裁掉来源的伪造 ref 被跳过。"""
    # 压低上限，迫使多卡片中后段整卡被裁掉（仅缩短正文不够）
    monkeypatch.setattr(
        "app.services.fuse_context_service.MAX_PROMPT_CHARS", 1_200
    )
    pid = _create_project(client)
    _seed_chapters(client, pid)
    # 三张超长卡片：裁剪后至少一张整卡被剔除
    long_body = "卡片正文参考段落。" * 400
    card_keep = _create_text_card(
        client, title="保留卡", body=long_body, card_type="document"
    )
    card_mid = _create_text_card(
        client, title="中间卡", body=long_body, card_type="document"
    )
    card_drop = _create_text_card(
        client, title="裁掉卡", body=long_body, card_type="document"
    )

    seen = {"user": "", "cards_in_prompt": []}

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        user = messages[1]["content"]
        seen["user"] = user
        in_prompt = []
        for cid, title in (
            (card_keep, "保留卡"),
            (card_mid, "中间卡"),
            (card_drop, "裁掉卡"),
        ):
            if f"id={cid}" in user:
                in_prompt.append(cid)
        seen["cards_in_prompt"] = in_prompt
        # 引用全部三张卡；被裁掉的 id 必须在 normalize 时丢弃
        return ChatResult(
            content=json.dumps(
                [
                    {
                        "targetChapterId": "chap_arch",
                        "confidence": 70,
                        "reason": "裁剪后引用校验",
                        "proposedMarkdown": "建议",
                        "sourceRefs": [
                            {
                                "kind": "card",
                                "id": card_keep,
                                "title": "假标题保留",
                            },
                            {
                                "kind": "card",
                                "id": card_mid,
                                "title": "假标题中间",
                            },
                            {
                                "kind": "card",
                                "id": card_drop,
                                "title": "假标题裁掉",
                            },
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
            model="mock-trim",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={
            "type": "content_fuse",
            "payload": {
                "templateIds": [],
                "cardIds": [card_keep, card_mid, card_drop],
                "targetChapterIds": ["chap_arch"],
                "mode": "merge_suggest",
            },
        },
    )
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["status"] == "success", task.get("error")
    result = task["result"]
    quota = result["quota"]
    assert quota["cardsSelected"] == 3
    assert quota["cardsUsed"] == len(seen["cards_in_prompt"])
    assert quota["cardsUsed"] >= 1
    assert quota["cardsUsed"] < 3, "应触发至少一张卡片被裁出 prompt"
    assert quota["promptChars"] <= 1_200
    assert quota["maxPromptChars"] == 1_200
    assert quota["promptChars"] <= quota["maxPromptChars"]

    assert result["suggestions"], "至少保留对入 prompt 来源的有效建议"
    sug = result["suggestions"][0]
    ref_ids = {r["id"] for r in sug["sourceRefs"]}
    # 仅允许实际入 prompt 的卡；裁掉来源的伪造 ref 不得出现
    assert ref_ids == set(seen["cards_in_prompt"])
    assert card_drop not in seen["cards_in_prompt"]
    assert card_drop not in ref_ids
    assert result["skippedInvalidCount"] >= 1
    # title 来自服务端，忽略模型伪造
    for r in sug["sourceRefs"]:
        assert r["title"] in ("保留卡", "中间卡")
        assert not r["title"].startswith("假标题")
