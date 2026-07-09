"""
模块：编辑器状态 API 测试
用途：验收 GET 空状态、PUT 部分字段、GET 回读。
"""


def test_editor_state_roundtrip(client):
    proj = client.post("/api/projects", json={"name": "编辑器联调"}).json()
    pid = proj["id"]

    empty = client.get(f"/api/projects/{pid}/editor-state")
    assert empty.status_code == 200
    body = empty.json()
    assert body["projectId"] == pid
    assert body["outline"] is None
    assert body["mode"] == "ALIGNED"

    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [{"id": "n1", "title": "总则", "children": []}],
            "analysisOverview": "这是概述",
            "mode": "FREE",
            "guidance": {"targetWordCount": 50000, "chapterFocus": "实施"},
        },
    )
    assert put.status_code == 200
    saved = put.json()
    assert saved["analysisOverview"] == "这是概述"
    assert saved["mode"] == "FREE"
    assert saved["outline"][0]["title"] == "总则"
    assert saved["guidance"]["targetWordCount"] == 50000

    # 仅更新 facts，不丢 outline
    put2 = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"facts": [{"id": "f1", "category": "A", "content": "x", "source": "manual"}]},
    )
    assert put2.status_code == 200
    again = client.get(f"/api/projects/{pid}/editor-state").json()
    assert again["outline"][0]["title"] == "总则"
    assert again["facts"][0]["id"] == "f1"


def test_health_includes_workspace_and_db(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["defaultWorkspaceId"] == "ws_local"
    assert body["dbOk"] is True
