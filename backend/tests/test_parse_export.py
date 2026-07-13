"""
模块：上传解析与导出任务测试（不调用外网 LLM）
用途：验收 parse / export 任务闭环；parse 默认引擎 lightweight 可追溯。
对接：task_service parse/export；parse_engines.lightweight。
二次开发：勿在此引入真实 MinerU/Docling 或外网 Key。
"""

from io import BytesIO


def test_upload_parse_and_export(client):
    proj = client.post("/api/projects", json={"name": "日用链路测试"}).json()
    pid = proj["id"]

    content = "# 招标文件\n\n项目概况：智慧城市示范。\n".encode("utf-8")
    files = {"file": ("tender.md", BytesIO(content), "text/markdown")}
    up = client.post(f"/api/projects/{pid}/files", files=files)
    assert up.status_code == 201
    assert up.json()["filename"] == "tender.md"

    listed = client.get(f"/api/projects/{pid}/files").json()
    assert len(listed) == 1

    parse_task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse"},
    )
    assert parse_task.status_code == 201
    body = parse_task.json()
    assert body["status"] == "success"
    assert body["type"] == "parse"
    assert body["result"]["engine"] == "lightweight"

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert state.get("parsedMarkdown")
    assert "智慧城市" in state["parsedMarkdown"]

    # 写入一点正文便于导出
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysisOverview": "概述测试",
            "outline": [{"id": "n1", "title": "第一章", "children": []}],
            "chapters": [
                {
                    "id": "n1",
                    "title": "第一章",
                    "body": "正文内容",
                    "preview": "正文",
                    "wordCount": 4,
                    "status": "done",
                }
            ],
        },
    )

    export_task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert export_task.status_code == 201
    exp = export_task.json()
    assert exp["status"] == "success"
    stored = exp["result"]["storedName"]
    dl = client.get(f"/api/projects/{pid}/export/download/{stored}")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    ) or "octet-stream" in dl.headers.get("content-type", "")
    assert len(dl.content) > 100
