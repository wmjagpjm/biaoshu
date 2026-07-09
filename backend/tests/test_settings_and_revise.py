"""
模块：设置 API 与 revise 路由测试
用途：验收设置明文读写、未配置 Key 时 revise 返回 400；mock LLM 时 revise 成功。
对接：pytest；LLM 调用使用 monkeypatch，不访问外网。
"""

from app.services.llm_service import ChatResult


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
