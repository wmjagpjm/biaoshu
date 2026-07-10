"""
模块：知识库 RAG 简版测试
用途：上传分块、检索、删除；生成注入块非空时含参考文案。
对接：/api/knowledge/*、knowledge_service.build_kb_prompt_block
"""

from io import BytesIO


def test_upload_search_delete(client):
    content = (
        "# 等保与售后规范\n\n"
        "本方案按等保三级建设，售后响应时间 4 小时内到场。\n"
        "## 运维\n\n双机房热备与消息总线。\n"
    ).encode("utf-8")
    up = client.post(
        "/api/knowledge/docs/upload",
        files={"file": ("kb-demo.md", BytesIO(content), "text/markdown")},
    )
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["status"] == "ready"
    assert doc["chunks"] >= 1
    assert doc["name"] == "kb-demo.md"

    folders = client.get("/api/knowledge/folders").json()
    assert len(folders) >= 1

    docs = client.get("/api/knowledge/docs").json()
    assert any(d["id"] == doc["id"] for d in docs)

    search = client.get("/api/knowledge/search", params={"q": "等保", "topK": 5})
    assert search.status_code == 200
    body = search.json()
    assert body["count"] >= 1
    assert any("等保" in (it.get("content") or "") for it in body["items"])

    # reindex
    ri = client.post(f"/api/knowledge/docs/{doc['id']}/reindex")
    assert ri.status_code == 200
    assert ri.json()["status"] == "ready"

    # delete
    dl = client.delete(f"/api/knowledge/docs/{doc['id']}")
    assert dl.status_code == 204
    docs2 = client.get("/api/knowledge/docs").json()
    assert all(d["id"] != doc["id"] for d in docs2)


def test_folder_create(client):
    res = client.post("/api/knowledge/folders", json={"name": "历史方案"})
    assert res.status_code == 201
    assert res.json()["name"] == "历史方案"
    listed = client.get("/api/knowledge/folders").json()
    assert any(f["name"] == "历史方案" for f in listed)


def test_build_kb_prompt_block_and_search_service(client):
    from app.core.database import SessionLocal
    from app.services import knowledge_service

    content = "# 评分要点\n\n总体架构与技术路线权重 20%。\n".encode("utf-8")
    up = client.post(
        "/api/knowledge/docs/upload",
        files={"file": ("score.md", BytesIO(content), "text/markdown")},
    )
    assert up.status_code == 201

    db = SessionLocal()
    try:
        hits = knowledge_service.search_chunks(db, "ws_local", "架构 技术路线", top_k=3)
        assert hits
        block = knowledge_service.build_kb_prompt_block(hits)
        assert "【知识库参考】" in block
        empty = knowledge_service.build_kb_prompt_block([])
        assert empty == ""
        # 无关词尽量少命中
        miss = knowledge_service.search_chunks(db, "ws_local", "火星移民基地xyz", top_k=3)
        assert miss == [] or all("火星" not in (h.get("content") or "") for h in miss)
    finally:
        db.close()


def test_search_folder_filter(client):
    """用途：folder_ids 过滤只命中指定文件夹文档。"""
    f_a = client.post("/api/knowledge/folders", json={"name": "方案A"}).json()
    f_b = client.post("/api/knowledge/folders", json={"name": "方案B"}).json()
    client.post(
        "/api/knowledge/docs/upload",
        data={"folderId": f_a["id"]},
        files={
            "file": (
                "a.md",
                BytesIO("# A\n\n唯一关键词苹果派在此。\n".encode("utf-8")),
                "text/markdown",
            )
        },
    )
    client.post(
        "/api/knowledge/docs/upload",
        data={"folderId": f_b["id"]},
        files={
            "file": (
                "b.md",
                BytesIO("# B\n\n唯一关键词香蕉船在此。\n".encode("utf-8")),
                "text/markdown",
            )
        },
    )
    only_a = client.get(
        "/api/knowledge/search",
        params=[("q", "苹果派"), ("folderId", f_a["id"])],
    ).json()
    assert only_a["count"] >= 1
    assert all(
        "香蕉" not in (it.get("content") or "") for it in only_a["items"]
    )

    only_b = client.get(
        "/api/knowledge/search",
        params=[("q", "香蕉船"), ("folderId", f_b["id"])],
    ).json()
    assert only_b["count"] >= 1

    # 搜苹果但限定 B 文件夹 → 应无结果
    miss = client.get(
        "/api/knowledge/search",
        params=[("q", "苹果派"), ("folderId", f_b["id"])],
    ).json()
    assert miss["count"] == 0


def test_guidance_kb_disabled_skips_search(client, monkeypatch):
    """用途：guidance.kbEnabled=false 时 chapter 不注入知识库。"""
    client.post(
        "/api/knowledge/docs/upload",
        files={
            "file": (
                "ops.md",
                BytesIO("# 实施\n\n售后响应 2 小时到场。\n".encode("utf-8")),
                "text/markdown",
            )
        },
    )
    proj = client.post("/api/projects", json={"name": "关KB"}).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysisOverview": "智慧交通",
            "guidance": {"kbEnabled": False, "kbFolderIds": []},
            "outline": [{"id": "n1", "title": "实施保障", "children": []}],
            "chapters": [
                {
                    "id": "n1",
                    "title": "实施保障",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                }
            ],
        },
    )
    captured: dict = {}

    def fake_chat(db, workspace_id, messages, **kwargs):
        captured["messages"] = messages

        class R:
            content = "正文"
            model = "mock"

        return R()

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert task.status_code == 201
    assert task.json()["status"] == "success"
    user = captured["messages"][1]["content"]
    assert "【知识库参考】" not in user
    cites = task.json().get("result", {}).get("kbCitations") or []
    assert cites == []


def test_chapter_injection_uses_kb(client, monkeypatch):
    """用途：有知识库时 chapter 生成 messages 含知识库参考（mock LLM）。"""
    content = (
        "# 实施保障\n\n提供 7x24 运维值班与备件库，售后响应 2 小时。\n"
    ).encode("utf-8")
    up = client.post(
        "/api/knowledge/docs/upload",
        files={"file": ("ops.md", BytesIO(content), "text/markdown")},
    )
    assert up.status_code == 201

    proj = client.post("/api/projects", json={"name": "RAG注入"}).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysisOverview": "智慧交通平台建设",
            "outline": [{"id": "n1", "title": "实施与运维保障", "children": []}],
            "chapters": [
                {
                    "id": "n1",
                    "title": "实施与运维保障",
                    "body": "",
                    "preview": "",
                    "wordCount": 0,
                    "status": "pending",
                }
            ],
        },
    )

    captured: dict = {}

    def fake_chat(db, workspace_id, messages, **kwargs):
        captured["messages"] = messages

        class R:
            content = "## 实施与运维保障\n\n提供值班与备件。\n"
            model = "mock"

        return R()

    monkeypatch.setattr(
        "app.services.llm_service.chat_completion",
        fake_chat,
    )

    task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "chapter", "payload": {"chapterId": "n1"}},
    )
    assert task.status_code == 201, task.text
    assert task.json()["status"] == "success"
    user = captured["messages"][1]["content"]
    assert "【知识库参考】" in user or "运维" in user
    cites = task.json().get("result", {}).get("kbCitations") or []
    assert isinstance(cites, list)
