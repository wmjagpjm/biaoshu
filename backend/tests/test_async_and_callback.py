"""
模块：异步任务与 parse-callback 测试
用途：异步创建后轮询 success；MinerU 回传写入 parsedMarkdown。
"""

import time


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


def test_parse_callback(client):
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
