"""
模块：异步任务与 parse-callback 测试
用途：异步创建后轮询 success；MinerU 回传写入 parsedMarkdown；Token 开关契约。
对接：parse-callback；settings.local_parser_token（默认空=不校验）。
二次开发：默认空 token 不校验是部署风险，须在文档与计划中明示，勿擅自改为强制非空。
"""

import os
import time

from app.core.config import get_settings


def test_async_parse_poll(client):
    proj = client.post("/api/projects", json={"name": "异步任务"}).json()
    pid = proj["id"]
    content = b"# async\n\nhello"
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("a.md", content, "text/markdown")},
    )
    created = client.post(
        f"/api/projects/{pid}/tasks",
        json={"type": "parse"},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] in ("pending", "running", "success")
    tid = task["id"]

    final = None
    for _ in range(40):
        got = client.get(f"/api/projects/{pid}/tasks/{tid}").json()
        if got["status"] in ("success", "failed"):
            final = got
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "success"
    assert final["progress"] == 100
    # 异步路径同样应可追溯默认 lightweight 引擎
    assert (final.get("result") or {}).get("engine") == "lightweight"


def test_parse_callback(client):
    """用途：默认 local_parser_token 为空时不校验 X-Local-Token（部署风险见计划文档）。"""
    # 契约：默认空 token → 不强制 Header
    assert (get_settings().local_parser_token or "").strip() == ""

    proj = client.post("/api/projects", json={"name": "回传测试"}).json()
    pid = proj["id"]
    res = client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# MinerU\n\n扫描件解析结果。",
            "source": "mineru",
            "filename": "scan.pdf",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert "扫描件解析结果" in (state.get("parsedMarkdown") or "")


def test_parse_callback_token_required_when_configured(client, monkeypatch):
    """用途：配置 token 后错误 Token=401，正确 Token 成功。"""
    monkeypatch.setenv("LOCAL_PARSER_TOKEN", "test-local-parser-token")
    get_settings.cache_clear()
    try:
        assert get_settings().local_parser_token == "test-local-parser-token"

        proj = client.post("/api/projects", json={"name": "回传鉴权"}).json()
        pid = proj["id"]
        payload = {
            "markdown": "# MinerU\n\n鉴权后的扫描件结果。",
            "source": "mineru",
            "filename": "scan.pdf",
        }

        no_header = client.post(f"/api/projects/{pid}/parse-callback", json=payload)
        assert no_header.status_code == 401

        bad = client.post(
            f"/api/projects/{pid}/parse-callback",
            json=payload,
            headers={"X-Local-Token": "wrong-token"},
        )
        assert bad.status_code == 401

        ok = client.post(
            f"/api/projects/{pid}/parse-callback",
            json=payload,
            headers={"X-Local-Token": "test-local-parser-token"},
        )
        assert ok.status_code == 200
        assert ok.json()["ok"] is True
        state = client.get(f"/api/projects/{pid}/editor-state").json()
        assert "鉴权后的扫描件结果" in (state.get("parsedMarkdown") or "")
    finally:
        monkeypatch.delenv("LOCAL_PARSER_TOKEN", raising=False)
        # 清理可能残留的环境覆盖，恢复默认空 token 契约
        os.environ.pop("LOCAL_PARSER_TOKEN", None)
        get_settings.cache_clear()
