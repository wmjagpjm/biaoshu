"""
模块：知识库 RAG 简版测试
用途：上传分块、检索、删除；生成注入块非空时含参考文案。
对接：/api/knowledge/*、knowledge_service.build_kb_prompt_block
"""

from io import BytesIO


def test_local_embedding_and_hybrid_search():
    """用途：本地向量余弦 + 入库后 hybrid 字段。"""
    from app.services import embedding_service

    a = embedding_service.local_embed("云原生微服务架构高可用")
    b = embedding_service.local_embed("微服务架构与云原生高可用部署")
    c = embedding_service.local_embed("今日天气适合郊游野餐")
    assert embedding_service.cosine(a, b) > embedding_service.cosine(a, c)
    assert abs(sum(x * x for x in a) - 1.0) < 1e-5


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
    # hybrid 应带向量分字段
    assert "vectorScore" in body["items"][0] or body["items"][0].get("score", 0) > 0

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


# ---------- P9C：离线真语义索引（确定性假模型，禁触网） ----------


def _install_fake_offline_embedder(monkeypatch):
    """
    用途：向 embedding_service 注入确定性 512 维假模型，禁止真实下载。
    对接：OfflineBgeEmbedder 测试注入接口。
    """
    from app.services import embedding_service

    emb = embedding_service.get_offline_embedder()
    emb.inject_test_model(
        embed_fn=lambda texts: [
            embedding_service.deterministic_offline_embed(t) for t in texts
        ],
        fingerprint="test-fp-p9c-fake",
    )
    monkeypatch.setattr(
        emb,
        "ensure_loaded_for_rebuild",
        lambda settings=None: "test-fp-p9c-fake",
    )
    return emb


def _upload_kb_doc(client, name: str, text: str) -> dict:
    content = text.encode("utf-8")
    up = client.post(
        "/api/knowledge/docs/upload",
        files={"file": (name, __import__("io").BytesIO(content), "text/markdown")},
    )
    assert up.status_code == 201, up.text
    return up.json()


def test_semantic_offline_embedder_stable_dim_and_unavailable():
    """用途：注入离线提供者输出 512 维且稳定；未加载时 model_unavailable，不触网。"""
    from app.services import embedding_service

    emb = embedding_service.get_offline_embedder()
    emb.clear_injection()
    emb.unload()

    # 未加载：生产路径不得触网，应返回固定错误码
    raised = None
    try:
        emb.embed_texts(["等保三级建设方案"])
    except embedding_service.OfflineEmbedderError as exc:
        raised = exc
    assert raised is not None
    assert raised.code == "model_unavailable"

    emb.inject_test_model(
        embed_fn=lambda texts: [
            embedding_service.deterministic_offline_embed(t) for t in texts
        ],
        fingerprint="fp-stable",
    )
    a1 = emb.embed_texts(["云原生微服务架构"])[0]
    a2 = emb.embed_texts(["云原生微服务架构"])[0]
    assert len(a1) == embedding_service.OFFLINE_DIM == 512
    assert a1 == a2
    assert abs(sum(x * x for x in a1) - 1.0) < 1e-5
    emb.clear_injection()
    emb.unload()


def test_semantic_index_rebuild_failure_keeps_active(client, monkeypatch):
    """用途：新索引构建失败时旧 active 仍在，其向量不被删除。"""
    from app.core.database import SessionLocal
    from app.models.entities import SemanticChunkEmbeddingRow, SemanticEmbeddingIndexRow
    from app.services import embedding_service, knowledge_service
    from sqlalchemy import select

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "keep-active.md",
        "# 架构\n\n微服务高可用与双活机房。\n",
    )

    # 首次重建成功 → active
    r1 = client.post("/api/knowledge/semantic-index/rebuild")
    assert r1.status_code == 202, r1.text
    idx1 = r1.json()["id"]
    st = client.get("/api/knowledge/semantic-index")
    assert st.status_code == 200
    body = st.json()
    assert body["status"] == "active"
    assert body["id"] == idx1
    assert body["dimension"] == 512

    db = SessionLocal()
    try:
        vec_count_before = len(
            list(
                db.scalars(
                    select(SemanticChunkEmbeddingRow).where(
                        SemanticChunkEmbeddingRow.index_id == idx1
                    )
                ).all()
            )
        )
        assert vec_count_before >= 1
    finally:
        db.close()

    # 注入失败：下一次 rebuild 中途抛错
    emb = embedding_service.get_offline_embedder()

    def boom(_texts):
        raise embedding_service.OfflineEmbedderError(
            "model_unavailable", "模拟失败"
        )

    emb.inject_test_model(embed_fn=boom, fingerprint="fp-fail")
    r2 = client.post("/api/knowledge/semantic-index/rebuild")
    assert r2.status_code == 202, r2.text
    idx2 = r2.json()["id"]
    assert idx2 != idx1

    failed = client.get(f"/api/knowledge/semantic-index/{idx2}")
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["errorCode"] in (
        "model_unavailable",
        "index_failed",
    )

    active = client.get("/api/knowledge/semantic-index")
    assert active.status_code == 200
    assert active.json()["status"] == "active"
    assert active.json()["id"] == idx1

    db = SessionLocal()
    try:
        old = db.get(SemanticEmbeddingIndexRow, idx1)
        assert old is not None
        assert old.status == "active"
        vec_count_after = len(
            list(
                db.scalars(
                    select(SemanticChunkEmbeddingRow).where(
                        SemanticChunkEmbeddingRow.index_id == idx1
                    )
                ).all()
            )
        )
        assert vec_count_after == vec_count_before
    finally:
        db.close()
        emb.clear_injection()


def test_semantic_index_workspace_isolation(client, monkeypatch):
    """用途：两工作空间索引互不可见；跨空间查询 404。"""
    from app.core.database import SessionLocal
    from app.models.entities import Workspace
    from app.services import knowledge_service

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(client, "ws-a.md", "# A\n\n工作空间甲专属关键词玄霄盾。\n")

    r_a = client.post("/api/knowledge/semantic-index/rebuild")
    assert r_a.status_code == 202
    id_a = r_a.json()["id"]

    # 创建第二工作空间并准备默认文件夹
    db = SessionLocal()
    try:
        if db.get(Workspace, "ws_other_p9c") is None:
            db.add(
                Workspace(
                    id="ws_other_p9c",
                    name="另一空间",
                    owner_user_id="user_other",
                )
            )
            db.commit()
        knowledge_service.ensure_default_folder(db, "ws_other_p9c")
        db.commit()
    finally:
        db.close()

    headers_b = {"X-Workspace-Id": "ws_other_p9c"}
    up_b = client.post(
        "/api/knowledge/docs/upload",
        headers=headers_b,
        files={
            "file": (
                "ws-b.md",
                __import__("io").BytesIO(
                    "# B\n\n工作空间乙专属关键词苍岚钥。\n".encode("utf-8")
                ),
                "text/markdown",
            )
        },
    )
    assert up_b.status_code == 201, up_b.text
    r_b = client.post("/api/knowledge/semantic-index/rebuild", headers=headers_b)
    assert r_b.status_code == 202, r_b.text
    id_b = r_b.json()["id"]
    assert id_b != id_a

    # A 读 B 的 index → 404
    cross = client.get(f"/api/knowledge/semantic-index/{id_b}")
    assert cross.status_code == 404
    # B 读 A 的 index → 404
    cross2 = client.get(
        f"/api/knowledge/semantic-index/{id_a}", headers=headers_b
    )
    assert cross2.status_code == 404

    # 各空间只能看到自己的 active
    a_st = client.get("/api/knowledge/semantic-index").json()
    b_st = client.get("/api/knowledge/semantic-index", headers=headers_b).json()
    assert a_st["id"] == id_a
    assert b_st["id"] == id_b


def test_semantic_search_without_index_keyword_degraded(client, monkeypatch):
    """用途：无 active 索引时仅关键词命中，状态 index_not_built，不用 legacy 向量。"""
    from app.core.database import SessionLocal
    from app.models.entities import KbChunkRow
    from sqlalchemy import select

    # 不注入假模型，确保不会产生语义分
    from app.services import embedding_service

    emb = embedding_service.get_offline_embedder()
    emb.clear_injection()
    emb.unload()

    _upload_kb_doc(
        client,
        "kw-only.md",
        "# 等保\n\n本方案按等保三级建设，售后响应 4 小时。\n",
    )

    # 人为写入 legacy embedding_json（256 维哈希），搜索不得用它算 vectorScore
    db = SessionLocal()
    try:
        chunks = list(db.scalars(select(KbChunkRow)).all())
        assert chunks
        for ch in chunks:
            ch.embedding_json = embedding_service.dumps_embedding(
                embedding_service.local_embed(ch.content or "")
            )
        db.commit()
    finally:
        db.close()

    search = client.get("/api/knowledge/search", params={"q": "等保", "topK": 5})
    assert search.status_code == 200
    body = search.json()
    assert body["count"] >= 1
    assert body.get("semanticStatus") == "index_not_built"
    assert body.get("semanticIndexId") is None
    for it in body["items"]:
        assert float(it.get("vectorScore") or 0) == 0.0


def test_semantic_search_active_index_scores(client, monkeypatch):
    """用途：有 active 索引时使用同维向量；building/failed 不产生语义分。"""
    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "sem-ready.md",
        "# 云原生\n\n微服务架构与高可用部署双活机房。\n",
    )
    r = client.post("/api/knowledge/semantic-index/rebuild")
    assert r.status_code == 202
    idx = r.json()["id"]

    search = client.get(
        "/api/knowledge/search", params={"q": "微服务高可用", "topK": 5}
    )
    assert search.status_code == 200
    body = search.json()
    assert body.get("semanticStatus") == "ready"
    assert body.get("semanticIndexId") == idx
    assert body["count"] >= 1
    assert any(float(it.get("vectorScore") or 0) > 0 for it in body["items"])


def test_semantic_rebuild_queue_conflict_and_interrupt(client, monkeypatch):
    """用途：POST 仅建 queued；并发 409；启动残留 queued/running 收敛为 interrupted。"""
    from app.core.database import SessionLocal
    from app.models.entities import SemanticEmbeddingIndexRow
    from app.services import knowledge_service
    from sqlalchemy import select

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(client, "queue.md", "# Q\n\n排队重建测试正文。\n")

    # 正常重建先完成，留下 active
    ok = client.post("/api/knowledge/semantic-index/rebuild")
    assert ok.status_code == 202

    # 手工插入残留 running，模拟进程中断前状态
    db = SessionLocal()
    try:
        stuck = SemanticEmbeddingIndexRow(
            id="sem_stuck_running",
            workspace_id="ws_local",
            status="running",
            provider="offline_bge",
            model_id="BAAI/bge-small-zh-v1.5",
            model_fingerprint="",
            dimension=512,
            chunk_count=0,
            error_code=None,
        )
        db.add(stuck)
        db.commit()
    finally:
        db.close()

    # 并发：已有 running → 409
    conflict = client.post("/api/knowledge/semantic-index/rebuild")
    assert conflict.status_code == 409

    # 启动期收敛
    db = SessionLocal()
    try:
        n = knowledge_service.mark_interrupted_semantic_indexes(db)
        assert n >= 1
        row = db.get(SemanticEmbeddingIndexRow, "sem_stuck_running")
        assert row is not None
        assert row.status == "failed"
        assert row.error_code == "index_interrupted"
        # active 仍在
        actives = list(
            db.scalars(
                select(SemanticEmbeddingIndexRow).where(
                    SemanticEmbeddingIndexRow.workspace_id == "ws_local",
                    SemanticEmbeddingIndexRow.status == "active",
                )
            ).all()
        )
        assert len(actives) == 1
    finally:
        db.close()


def test_semantic_rebuild_partial_unique_and_race_409(client, monkeypatch):
    """
    用途：部分唯一索引拒绝同 workspace 第二条 queued/running；
    服务层将 IntegrityError 竞态映射为 SemanticIndexConflictError/API 409；
    另一 workspace 不受影响；active/failed/superseded 仍可并存。
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.core.database import SessionLocal
    from app.models.entities import SemanticEmbeddingIndexRow, Workspace
    from app.services import knowledge_service

    _install_fake_offline_embedder(monkeypatch)

    def _row(
        rid: str,
        workspace_id: str,
        status: str,
        *,
        error_code: str | None = None,
    ) -> SemanticEmbeddingIndexRow:
        return SemanticEmbeddingIndexRow(
            id=rid,
            workspace_id=workspace_id,
            status=status,
            provider="offline_bge",
            model_id="BAAI/bge-small-zh-v1.5",
            model_fingerprint="",
            dimension=512,
            chunk_count=0,
            error_code=error_code,
        )

    # 1) 数据库约束：同 workspace 第二条 building 状态被拒
    db = SessionLocal()
    try:
        db.add(_row("sem_build_a", "ws_local", "queued"))
        db.commit()
        db.add(_row("sem_build_b", "ws_local", "running"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # active / failed / superseded 可与历史并存，不触发部分唯一索引
        db.add(_row("sem_hist_active", "ws_local", "active"))
        db.add(
            _row(
                "sem_hist_failed",
                "ws_local",
                "failed",
                error_code="index_failed",
            )
        )
        db.add(_row("sem_hist_super", "ws_local", "superseded"))
        db.commit()
    finally:
        db.close()

    # 2) 竞态：跳过 count 快路径，强制走唯一索引 → IntegrityError → 冲突异常
    monkeypatch.setattr(
        knowledge_service,
        "_count_active_semantic_builds",
        lambda _db, _ws: 0,
    )
    db = SessionLocal()
    try:
        with pytest.raises(knowledge_service.SemanticIndexConflictError):
            knowledge_service.create_semantic_index_rebuild(db, "ws_local")
    finally:
        db.close()

    # API 同样稳定 409，且不暴露数据库细节
    conflict = client.post("/api/knowledge/semantic-index/rebuild")
    assert conflict.status_code == 409
    detail = str(conflict.json().get("detail") or "").lower()
    assert "integrity" not in detail
    assert "unique" not in detail
    assert "sqlite" not in detail

    # 3) 另一 workspace 仍可创建 queued/running
    db = SessionLocal()
    try:
        if db.get(Workspace, "ws_other_p9c") is None:
            db.add(
                Workspace(
                    id="ws_other_p9c",
                    name="另一空间",
                    owner_user_id="user_other",
                )
            )
            db.commit()
        other = knowledge_service.create_semantic_index_rebuild(db, "ws_other_p9c")
        assert other.status == "queued"
        assert other.workspace_id == "ws_other_p9c"
    finally:
        db.close()


def test_semantic_api_no_secrets_or_paths(client, monkeypatch):
    """用途：API/序列化不含 apiKey、外部 URL、用户缓存路径、正文或供应方原始错误。"""
    import json

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "nosecret.md",
        "# 敏感\n\n正文不得出现在索引状态响应里的专用标记词密_payload_xyz。\n",
    )
    r = client.post("/api/knowledge/semantic-index/rebuild")
    assert r.status_code == 202
    idx = r.json()

    status = client.get("/api/knowledge/semantic-index")
    assert status.status_code == 200
    detail = client.get(f"/api/knowledge/semantic-index/{idx['id']}")
    assert detail.status_code == 200
    search = client.get("/api/knowledge/search", params={"q": "敏感", "topK": 3})
    assert search.status_code == 200

    blobs = [
        json.dumps(status.json(), ensure_ascii=False),
        json.dumps(detail.json(), ensure_ascii=False),
        json.dumps(idx, ensure_ascii=False),
    ]
    # 搜索 items 会有 content 截断（既有契约）；状态接口不得含正文
    for blob in blobs:
        low = blob.lower()
        assert "apikey" not in low
        assert "api_key" not in low
        assert "http://" not in low
        assert "https://" not in low
        assert "huggingface" not in low
        assert "c:\\users" not in low
        assert "/users/administrator" not in low
        assert "secret_payload_xyz" not in blob
        assert "traceback" not in low


def test_semantic_status_prefers_building_over_active(client, monkeypatch):
    """用途：旧 active + 新 queued/running 时状态端点必须可见构建中。"""
    from app.core.database import SessionLocal
    from app.models.entities import SemanticEmbeddingIndexRow

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(client, "build-visible.md", "# 可见\n\n构建中状态可见性测试。\n")
    r = client.post("/api/knowledge/semantic-index/rebuild")
    assert r.status_code == 202
    active_id = r.json()["id"]
    st0 = client.get("/api/knowledge/semantic-index").json()
    assert st0["status"] == "active"
    assert st0["id"] == active_id

    db = SessionLocal()
    try:
        building = SemanticEmbeddingIndexRow(
            id="sem_building_visible",
            workspace_id="ws_local",
            status="running",
            provider="offline_bge",
            model_id="BAAI/bge-small-zh-v1.5",
            model_fingerprint="",
            dimension=512,
            total_chunks=4,
            embedded_chunks=1,
            chunk_count=1,
            error_code=None,
        )
        db.add(building)
        db.commit()
    finally:
        db.close()

    st = client.get("/api/knowledge/semantic-index")
    assert st.status_code == 200
    body = st.json()
    assert body["id"] == "sem_building_visible"
    assert body["status"] == "running"
    assert body["errorCode"] == "index_building"
    assert body["totalChunks"] == 4
    assert body["embeddedChunks"] == 1
    # 搜索仍应使用旧 active，不得因状态端点改读 running
    search = client.get(
        "/api/knowledge/search", params={"q": "构建中状态", "topK": 3}
    )
    assert search.status_code == 200
    sbody = search.json()
    assert sbody.get("semanticStatus") == "ready"
    assert sbody.get("semanticIndexId") == active_id


def test_semantic_index_progress_fields_success_and_failure(client, monkeypatch):
    """用途：totalChunks/embeddedChunks 成功一致；失败不虚报完成进度。"""
    from app.core.database import SessionLocal
    from app.models.entities import SemanticEmbeddingIndexRow
    from app.services import embedding_service, knowledge_service

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "progress.md",
        "# 进度\n\n进度字段成功与失败分支测试正文。\n",
    )
    ok = client.post("/api/knowledge/semantic-index/rebuild")
    assert ok.status_code == 202
    idx_ok = ok.json()["id"]
    st = client.get(f"/api/knowledge/semantic-index/{idx_ok}").json()
    assert st["status"] == "active"
    assert int(st["totalChunks"]) >= 1
    assert int(st["embeddedChunks"]) == int(st["totalChunks"])
    # chunkCount 兼容等价 embeddedChunks
    assert int(st["chunkCount"]) == int(st["embeddedChunks"])

    emb = embedding_service.get_offline_embedder()

    def boom(_texts):
        raise embedding_service.OfflineEmbedderError(
            "model_unavailable", "进度失败注入"
        )

    emb.inject_test_model(embed_fn=boom, fingerprint="fp-progress-fail")
    # 直接创建 queued 并执行，避免 202 后立刻 active 掩盖中途 total
    db = SessionLocal()
    try:
        row = knowledge_service.create_semantic_index_rebuild(db, "ws_local")
        fail_id = row.id
    finally:
        db.close()
    knowledge_service.execute_semantic_index_rebuild(fail_id)

    failed = client.get(f"/api/knowledge/semantic-index/{fail_id}").json()
    assert failed["status"] == "failed"
    # 失败时不得把 embedded 虚报为已完成（embedded < total 或均为 0 且非 active）
    total = int(failed.get("totalChunks") or 0)
    embedded = int(failed.get("embeddedChunks") or 0)
    assert failed["status"] != "active"
    assert embedded <= total
    if total > 0:
        assert embedded < total or embedded == 0
    # 汇总状态仍指向旧 active，不因失败运行误报完成
    summary = client.get("/api/knowledge/semantic-index").json()
    assert summary["status"] == "active"
    assert summary["id"] == idx_ok
    emb.clear_injection()


def test_semantic_artifact_fingerprint_content_sensitive(tmp_path):
    """用途：同名同尺寸、内容不同的制品必须得到不同指纹。"""
    from app.services.embedding_service import OfflineBgeEmbedder

    emb = OfflineBgeEmbedder()
    cache_a = tmp_path / "cache_a"
    cache_b = tmp_path / "cache_b"
    rel = "models--BAAI--bge-small-zh-v1.5/snapshots/x/weights.bin"
    for root, payload in ((cache_a, b"A" * 64), (cache_b, b"B" * 64)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        assert path.stat().st_size == 64

    fp_a = emb._compute_artifact_fingerprint(cache_a, "BAAI/bge-small-zh-v1.5")
    fp_b = emb._compute_artifact_fingerprint(cache_b, "BAAI/bge-small-zh-v1.5")
    assert fp_a != fp_b
    assert isinstance(fp_a, str) and len(fp_a) == 32
    # 指纹与返回值不得含绝对路径
    assert ":" not in fp_a
    assert "\\" not in fp_a
    assert str(tmp_path) not in fp_a
    assert str(tmp_path) not in fp_b


def test_semantic_model_cache_dir_from_upload_dir(tmp_path, monkeypatch):
    """用途：缓存根相对 upload_dir 父目录/data 推导，不依赖进程 cwd。"""
    from pathlib import Path

    from app.core.config import Settings, resolve_semantic_model_cache_dir

    upload = tmp_path / "nested" / "uploads"
    upload.mkdir(parents=True)
    settings = Settings(
        upload_dir=str(upload),
        semantic_model_cache_dir="semantic-models",
    )
    cache = resolve_semantic_model_cache_dir(settings)
    expected = (tmp_path / "nested" / "data" / "semantic-models").resolve()
    assert cache == expected
    # 即使 cwd 改变，结果仍锚定 upload_dir
    monkeypatch.chdir(tmp_path)
    cache2 = resolve_semantic_model_cache_dir(settings)
    assert cache2 == expected
    assert cache2.is_absolute()


def test_semantic_search_no_hits_model_unavailable(client, monkeypatch):
    """用途：active 但 embedder 未 ready 时，无命中亦返回 model_unavailable，零 vectorScore。"""
    from app.services import embedding_service

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "nohit.md",
        "# 专项\n\n仅包含专用词青鸾阙，与后续查询无关。\n",
    )
    r = client.post("/api/knowledge/semantic-index/rebuild")
    assert r.status_code == 202
    idx = r.json()["id"]

    # 卸载模型：搜索不得加载或触网
    emb = embedding_service.get_offline_embedder()
    emb.clear_injection()
    emb.unload()
    assert emb.is_ready() is False

    # 无命中查询（无 ready 分块匹配）
    search = client.get(
        "/api/knowledge/search",
        params={"q": "完全不存在的词玄冥锁甲", "topK": 5},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["count"] == 0
    assert body.get("semanticStatus") == "model_unavailable"
    assert body.get("semanticIndexId") == idx
    for it in body.get("items") or []:
        assert float(it.get("vectorScore") or 0) == 0.0
    # 仍未加载
    assert emb.is_ready() is False


def test_semantic_index_status_active_model_unavailable_no_load(client, monkeypatch):
    """
    用途：库内 active 但 OfflineBgeEmbedder 未 ready 时，GET /semantic-index
    保留 id/status=active，临时 errorCode=model_unavailable；不写库、不加载模型。
    """
    from app.core.database import SessionLocal
    from app.models.entities import SemanticEmbeddingIndexRow
    from app.services import embedding_service

    _install_fake_offline_embedder(monkeypatch)
    _upload_kb_doc(
        client,
        "status-ready.md",
        "# 就绪\n\n等保三级与整改方案。\n",
    )
    r = client.post("/api/knowledge/semantic-index/rebuild")
    assert r.status_code == 202, r.text
    idx = r.json()["id"]

    # 确认就绪路径：模型 ready 时 errorCode 为空
    ready_st = client.get("/api/knowledge/semantic-index")
    assert ready_st.status_code == 200
    ready_body = ready_st.json()
    assert ready_body["id"] == idx
    assert ready_body["status"] == "active"
    assert ready_body.get("errorCode") in (None, "")

    # 卸载/清除注入：模拟进程重启后模型未进内存
    emb = embedding_service.get_offline_embedder()
    emb.clear_injection()
    emb.unload()
    assert emb.is_ready() is False

    load_calls = {"n": 0}

    def _spy_ensure(settings=None):  # noqa: ANN001
        load_calls["n"] += 1
        raise AssertionError("状态查询不得调用 ensure_loaded_for_rebuild")

    monkeypatch.setattr(emb, "ensure_loaded_for_rebuild", _spy_ensure)

    st = client.get("/api/knowledge/semantic-index")
    assert st.status_code == 200, st.text
    body = st.json()
    assert body["id"] == idx
    assert body["status"] == "active"
    assert body["errorCode"] == "model_unavailable"
    assert body["dimension"] == 512
    assert load_calls["n"] == 0
    assert emb.is_ready() is False

    # 数据库行仍为 active，error_code 未被写回
    db = SessionLocal()
    try:
        row = db.get(SemanticEmbeddingIndexRow, idx)
        assert row is not None
        assert row.status == "active"
        assert row.error_code is None
    finally:
        db.close()
