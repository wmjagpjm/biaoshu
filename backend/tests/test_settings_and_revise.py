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
    模块：M3 非四值 PUT 拒绝零污染
    用途：非法 parseStrategy 固定拒绝；完整设置行策略与敏感字段不被改写。
    对接：PUT /api/settings；ORM WorkspaceSettingsRow。
    二次开发：保持 400（非通用 422）；禁止部分写入。
    """
    seed = client.put(
        "/api/settings",
        json={
            "provider": "deepseek",
            "apiBaseUrl": "https://api.example.com/v1",
            "apiKey": "sk-keep-unpolluted",
            "model": "keep-model",
            "parseStrategy": "local",
        },
    )
    assert seed.status_code == 200, seed.text
    assert seed.json()["parseStrategy"] == "local"

    ws = get_settings().default_workspace_id
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
            "updated_at": row.updated_at,
        }
    finally:
        db.close()

    for illegal in (
        "mineru",
        "docling",
        "lightweight",
        "ocr",
        "MANAGED",
        "secret-not-allowed",
        "",
    ):
        put = client.put(
            "/api/settings",
            json={
                "provider": "openai-compatible",
                "apiBaseUrl": "https://evil.example/v1",
                "apiKey": "sk-should-not-write",
                "model": "evil-model",
                "parseStrategy": illegal,
            },
        )
        assert put.status_code == 400, (illegal, put.status_code, put.text)
        # 不得变成 422（Schema Literal 会改变既有错误形态）
        assert put.status_code != 422

        full = client.get("/api/settings")
        assert full.status_code == 200, full.text
        body = full.json()
        assert body["parseStrategy"] == "local"
        assert body["apiKey"] == "sk-keep-unpolluted"
        assert body["model"] == "keep-model"
        assert body["provider"] == "deepseek"

        db = SessionLocal()
        try:
            row = db.get(WorkspaceSettingsRow, ws)
            assert row is not None
            assert row.parse_strategy == before["parse_strategy"]
            assert row.api_key == before["api_key"]
            assert row.model == before["model"]
            assert row.provider == before["provider"]
            assert row.api_base_url == before["api_base_url"]
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
