"""
模块：设置 API 与 revise 路由测试
用途：验收设置明文读写、M3 四值 parseStrategy、未配置 Key 时 revise 返回 400；mock LLM 时 revise 成功。
对接：pytest；LLM 调用使用 monkeypatch，不访问外网。
二次开发：M3 仅扩 light|managed|local|ask；非法 PUT 零污染；禁止真实 Key/外网。
"""

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import WorkspaceSettingsRow
from app.services.llm_service import ChatResult

_FOUR_PARSE = ("light", "managed", "local", "ask")
_PARSE_PATH = "/api/settings/parse-strategy"


def test_settings_get_default_and_put(client):
    """用途：GET 默认设置；PUT 写入 Key 后 GET 明文回显。"""
    got = client.get("/api/settings")
    assert got.status_code == 200
    body = got.json()
    assert body["provider"] == "openai-compatible"
    assert body["apiKey"] == ""
    assert "apiBaseUrl" in body
    assert body["parseStrategy"] == "light"

    put = client.put(
        "/api/settings",
        json={
            "provider": "deepseek",
            "apiBaseUrl": "https://api.deepseek.com/v1",
            "apiKey": "sk-test-plain-key",
            "model": "deepseek-chat",
            "parseStrategy": "local",
        },
    )
    assert put.status_code == 200
    saved = put.json()
    assert saved["apiKey"] == "sk-test-plain-key"
    assert saved["parseStrategy"] == "local"
    assert saved["provider"] == "deepseek"

    again = client.get("/api/settings").json()
    assert again["apiKey"] == "sk-test-plain-key"


def test_settings_parse_strategy_managed_put_get_roundtrip(client):
    """
    模块：M3 managed PUT/GET 往返
    用途：PUT parseStrategy=managed 成功后，完整设置与权威 parse-strategy 均回显 managed。
    对接：PUT /api/settings；GET /api/settings；GET /api/settings/parse-strategy。
    二次开发：不得 422；Schema 保持字符串。
    """
    put = client.put(
        "/api/settings",
        json={
            "provider": "openai-compatible",
            "apiBaseUrl": "https://api.deepseek.com/v1",
            "apiKey": "",
            "model": "deepseek-chat",
            "parseStrategy": "managed",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["parseStrategy"] == "managed"

    full = client.get("/api/settings")
    assert full.status_code == 200, full.text
    assert full.json()["parseStrategy"] == "managed"

    slim = client.get(_PARSE_PATH)
    assert slim.status_code == 200, slim.text
    body = slim.json()
    assert set(body.keys()) == {"parseStrategy"}
    assert body["parseStrategy"] == "managed"
    assert "no-store" in (slim.headers.get("cache-control") or "").lower()


def test_settings_parse_strategy_four_values_put_roundtrip(client):
    """
    模块：M3 四值精确集合
    用途：light|managed|local|ask 均可 PUT 成功并 GET 回显；集合精确四值。
    对接：PUT|GET /api/settings。
    二次开发：不得静默吸收第五值。
    """
    assert len(_FOUR_PARSE) == 4
    assert set(_FOUR_PARSE) == {"light", "managed", "local", "ask"}
    for strategy in _FOUR_PARSE:
        put = client.put(
            "/api/settings",
            json={
                "provider": "openai-compatible",
                "apiBaseUrl": "https://api.deepseek.com/v1",
                "apiKey": "",
                "model": "deepseek-chat",
                "parseStrategy": strategy,
            },
        )
        assert put.status_code == 200, (strategy, put.text)
        assert put.json()["parseStrategy"] == strategy
        again = client.get("/api/settings")
        assert again.status_code == 200, again.text
        assert again.json()["parseStrategy"] == strategy
        slim = client.get(_PARSE_PATH)
        assert slim.status_code == 200, slim.text
        assert slim.json()["parseStrategy"] == strategy


def test_settings_parse_strategy_illegal_put_rejected_no_pollute(client):
    """
    模块：M3 非四值 PUT 拒绝零污染（含空白近合法值）
    用途：fresh workspace 先覆盖非法 PUT 零建行；seed 后非法/空白包裹固定 400；
          恶意 body 实发 embeddingModel 与全字段 HTTP+ORM 零污染。
    对接：PUT /api/settings；ORM WorkspaceSettingsRow。
    二次开发：保持 400（非通用 422）；禁止 strip 归一；禁止部分写入；禁止另复制整段用例。
    """
    ws = get_settings().default_workspace_id
    # Q5：seed 前先覆盖 fresh workspace — 无 workspace_settings 行时非法 PUT 不得建行
    db = SessionLocal()
    try:
        assert db.get(WorkspaceSettingsRow, ws) is None
    finally:
        db.close()

    put_fresh = client.put(
        "/api/settings",
        json={
            "provider": "openai-compatible",
            "apiBaseUrl": "https://evil.example/v1",
            "apiKey": "sk-should-not-write",
            "model": "evil-model",
            "embeddingModel": "evil-embed-should-not-write",
            "parseStrategy": "mineru",
        },
    )
    assert put_fresh.status_code == 400, put_fresh.text
    assert put_fresh.status_code != 422
    db = SessionLocal()
    try:
        # 零 commit 可见写：行仍不存在
        assert db.get(WorkspaceSettingsRow, ws) is None
    finally:
        db.close()

    seed = client.put(
        "/api/settings",
        json={
            "provider": "deepseek",
            "apiBaseUrl": "https://api.example.com/v1",
            "apiKey": "sk-keep-unpolluted",
            "model": "keep-model",
            "embeddingModel": "keep-embed",
            "parseStrategy": "local",
        },
    )
    assert seed.status_code == 200, seed.text
    assert seed.json()["parseStrategy"] == "local"
    assert seed.json().get("embeddingModel", "") == "keep-embed"

    db = SessionLocal()
    try:
        row = db.get(WorkspaceSettingsRow, ws)
        assert row is not None
        before = {
            "parse_strategy": row.parse_strategy,
            "api_key": row.api_key,
            "model": row.model,
            "provider": row.provider,
            "api_base_url": row.api_base_url,
            "embedding_model": row.embedding_model,
            "updated_at": row.updated_at,
        }
    finally:
        db.close()
    assert before["embedding_model"] == "keep-embed"

    for illegal in (
        "mineru",
        "docling",
        "lightweight",
        "ocr",
        "MANAGED",
        "secret-not-allowed",
        "",
        " light ",
        "managed ",
        "\tlocal",
        "ask\t",
        " light",
        "local ",
        "\nmanaged",
        "ask\r\n",
    ):
        # Q6：恶意 body 实发 embeddingModel，与 provider/apiBaseUrl/apiKey/model/parseStrategy 一起证零污染
        put = client.put(
            "/api/settings",
            json={
                "provider": "openai-compatible",
                "apiBaseUrl": "https://evil.example/v1",
                "apiKey": "sk-should-not-write",
                "model": "evil-model",
                "embeddingModel": "evil-embed-should-not-write",
                "parseStrategy": illegal,
            },
        )
        assert put.status_code == 400, (repr(illegal), put.status_code, put.text)
        # 不得变成 422（Schema Literal 会改变既有错误形态）
        assert put.status_code != 422

        full = client.get("/api/settings")
        assert full.status_code == 200, full.text
        body = full.json()
        assert body["parseStrategy"] == "local", repr(illegal)
        assert body["apiKey"] == "sk-keep-unpolluted"
        assert body["model"] == "keep-model"
        assert body["provider"] == "deepseek"
        assert body["apiBaseUrl"] == "https://api.example.com/v1"
        assert body.get("embeddingModel", "") == "keep-embed", repr(illegal)

        db = SessionLocal()
        try:
            row = db.get(WorkspaceSettingsRow, ws)
            assert row is not None
            assert row.parse_strategy == before["parse_strategy"]
            assert row.api_key == before["api_key"]
            assert row.model == before["model"]
            assert row.provider == before["provider"]
            assert row.api_base_url == before["api_base_url"]
            assert row.embedding_model == before["embedding_model"]
            assert row.updated_at == before["updated_at"]
        finally:
            db.close()


def test_revise_without_key_returns_400(client):
    """用途：未配置 Key 时 revise 应 400 提示去设置页。"""
    proj = client.post("/api/projects", json={"name": "修订测试项目"}).json()
    res = client.post(
        f"/api/projects/{proj['id']}/artifacts/outline/revise",
        json={
            "stage": "outline",
            "message": "一级目录对齐招标",
            "preserveStructure": True,
        },
    )
    assert res.status_code == 400
    assert "Key" in res.json()["detail"] or "配置" in res.json()["detail"]


def test_revise_with_mocked_llm(client, monkeypatch):
    """用途：mock chat_completion 后 revise 返回 applied 与摘要。"""
    client.put(
        "/api/settings",
        json={
            "apiBaseUrl": "https://api.example.com/v1",
            "apiKey": "sk-fake",
            "model": "demo-model",
        },
    )
    proj = client.post("/api/projects", json={"name": "mock修订"}).json()

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content="已压缩重复小节。\n\n# 修订后大纲\n## 第一章",
            model="demo-model",
        )

    monkeypatch.setattr(
        "app.services.llm_service.chat_completion",
        fake_chat,
    )

    res = client.post(
        f"/api/projects/{proj['id']}/artifacts/outline/revise",
        json={
            "stage": "outline",
            "message": "压缩重复",
            "preserveStructure": True,
            "baseContent": "# 原大纲\n## A\n## B",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "applied"
    assert data["resultSummary"]
    assert data["revisedContent"]
    assert data["model"] == "demo-model"
    assert data["id"].startswith("fb_")
