"""
模块：结构化招标分析与导出模板测试
用途：editor-state analysis 读写；settings exportFormat；analyze mock LLM。
"""

from app.services.llm_service import ChatResult


def test_editor_state_analysis_roundtrip(client):
    proj = client.post("/api/projects", json={"name": "结构分析"}).json()
    pid = proj["id"]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysis": {
                "overview": "概述A",
                "techRequirements": ["要求1", "要求2"],
                "rejectionRisks": ["风险1"],
                "scoringPoints": [{"name": "架构", "weight": "20%"}],
            }
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["analysis"]["overview"] == "概述A"
    assert body["analysisOverview"] == "概述A"
    assert body["analysis"]["scoringPoints"][0]["name"] == "架构"

    got = client.get(f"/api/projects/{pid}/editor-state").json()
    assert got["analysis"]["techRequirements"] == ["要求1", "要求2"]


def test_analyze_task_structured(client, monkeypatch):
    proj = client.post("/api/projects", json={"name": "AI结构"}).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "# 招标\n\n视频 2000 路，等保三级。"},
    )

    def fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):
        return ChatResult(
            content=(
                '{"overview":"智慧交通项目","techRequirements":["2000路视频"],'
                '"rejectionRisks":["缺★响应"],'
                '"scoringPoints":[{"name":"架构","weight":"20%"}]}'
            ),
            model="demo",
        )

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "analyze"},
    )
    assert res.status_code == 201
    assert res.json()["status"] == "success"
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert state["analysis"]["overview"] == "智慧交通项目"
    assert "2000路视频" in state["analysis"]["techRequirements"]
    assert state["analysis"]["scoringPoints"][0]["weight"] == "20%"


def test_settings_export_format(client):
    put = client.put(
        "/api/settings",
        json={
            "exportFormat": {
                "template_name": "测试模板",
                "body_text": {"font": "仿宋", "size": "小四"},
                "headings": [{"font": "黑体", "size": "三号", "bold": True}],
            }
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["exportFormat"]["template_name"] == "测试模板"
    got = client.get("/api/settings").json()
    assert got["exportFormat"]["body_text"]["font"] == "仿宋"
