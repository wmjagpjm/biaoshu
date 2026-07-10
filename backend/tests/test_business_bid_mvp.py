"""
模块：商务标 MVP 验收
用途：kind 过滤、editor-state 商务字段、biz_qualify mock、export 非空。
对接：project_service / editor_state / task_service / export
"""

from __future__ import annotations

import json
from types import SimpleNamespace


def test_create_and_list_business_kind(client):
    """用途：创建 kind=business，列表可按 kind 过滤。"""
    tech = client.post(
        "/api/projects",
        json={"name": "技术甲", "kind": "technical"},
    ).json()
    biz = client.post(
        "/api/projects",
        json={"name": "商务乙", "kind": "business", "industry": "政务"},
    ).json()
    assert tech["kind"] == "technical"
    assert biz["kind"] == "business"
    assert biz["name"] == "商务乙"

    all_p = client.get("/api/projects").json()
    ids = {p["id"] for p in all_p}
    assert tech["id"] in ids and biz["id"] in ids

    only_biz = client.get("/api/projects", params={"kind": "business"}).json()
    assert all(p["kind"] == "business" for p in only_biz)
    assert any(p["id"] == biz["id"] for p in only_biz)

    only_tech = client.get("/api/projects", params={"kind": "technical"}).json()
    assert all(p["kind"] == "technical" for p in only_tech)
    assert any(p["id"] == tech["id"] for p in only_tech)


def test_editor_state_business_fields(client):
    """用途：PUT/GET businessQualify 等字段持久化。"""
    proj = client.post(
        "/api/projects", json={"name": "商务编辑态", "kind": "business"}
    ).json()
    pid = proj["id"]
    body = {
        "parsedMarkdown": "# 资格\n1. 法人资格",
        "businessQualify": [
            {
                "id": "q1",
                "requirement": "法人资格",
                "response": "我司具备",
                "evidence": "执照.pdf",
                "status": "matched",
            }
        ],
        "businessToc": [
            {
                "id": "t1",
                "title": "投标函",
                "category": "法定",
                "status": "required",
                "checked": True,
            }
        ],
        "businessQuote": {
            "rows": [
                {
                    "id": "r1",
                    "name": "实施服务",
                    "unit": "项",
                    "quantity": "1",
                    "unitPrice": "",
                    "amount": "",
                    "remark": "",
                }
            ],
            "notes": "含税",
        },
        "businessCommit": [
            {
                "id": "c1",
                "title": "投标承诺",
                "body": "我方承诺…",
                "needsStamp": True,
            }
        ],
    }
    put = client.put(f"/api/projects/{pid}/editor-state", json=body)
    assert put.status_code == 200, put.text
    data = put.json()
    assert data["parsedMarkdown"].startswith("# 资格")
    assert data["businessQualify"][0]["requirement"] == "法人资格"
    assert data["businessToc"][0]["title"] == "投标函"
    assert data["businessQuote"]["notes"] == "含税"
    assert data["businessCommit"][0]["needsStamp"] is True

    got = client.get(f"/api/projects/{pid}/editor-state").json()
    assert len(got["businessQualify"]) == 1
    assert got["businessQuote"]["rows"][0]["name"] == "实施服务"


def test_biz_qualify_with_mocked_llm(client, monkeypatch):
    """用途：mock LLM 后 sync biz_qualify 写回资格列表。"""
    from app.services import llm_service

    fake_items = [
        {
            "id": "q1",
            "requirement": "独立法人",
            "response": "具备",
            "evidence": "",
            "status": "pending",
        },
        {
            "id": "q2",
            "requirement": "同类业绩 2 项",
            "response": "待补充",
            "evidence": "",
            "status": "partial",
        },
    ]

    def fake_chat(db, workspace_id, messages, **kwargs):
        return SimpleNamespace(
            content=json.dumps(fake_items, ensure_ascii=False),
            model="mock",
        )

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    proj = client.post(
        "/api/projects", json={"name": "商务生成", "kind": "business"}
    ).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "## 资格\n1. 独立法人\n2. 业绩两项"},
    )

    task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "biz_qualify", "payload": {}},
    )
    assert task.status_code in (200, 201), task.text
    body = task.json()
    assert body["status"] == "success", body

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert len(state["businessQualify"]) == 2
    assert state["businessQualify"][0]["requirement"] == "独立法人"


def test_business_qualify_revise_writes_editor_state(client, monkeypatch):
    """用途：mock LLM 后 business_qualify revise 写回资格表。"""
    from app.services import llm_service

    revised_items = [
        {
            "id": "q1",
            "requirement": "独立法人（修订）",
            "response": "我司具备",
            "evidence": "执照.pdf",
            "status": "matched",
        }
    ]

    def fake_chat(db, workspace_id, messages, **kwargs):
        body = "已按意见强化法人条款。\n\n" + json.dumps(
            revised_items, ensure_ascii=False
        )
        return SimpleNamespace(content=body, model="mock")

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    proj = client.post(
        "/api/projects", json={"name": "商务修订", "kind": "business"}
    ).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "pending",
                }
            ]
        },
    )

    res = client.post(
        f"/api/projects/{pid}/artifacts/workspace/revise",
        json={
            "stage": "business_qualify",
            "message": "强化法人响应说明",
            "preserveStructure": True,
            "baseContent": json.dumps(
                [
                    {
                        "id": "q1",
                        "requirement": "法人",
                        "response": "有",
                        "evidence": "",
                        "status": "pending",
                    }
                ],
                ensure_ascii=False,
            ),
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "applied"
    assert data.get("revisedContent")

    state = client.get(f"/api/projects/{pid}/editor-state").json()
    assert len(state["businessQualify"]) == 1
    assert "修订" in state["businessQualify"][0]["requirement"]


def test_apply_business_struct_revise_unit():
    """用途：纯函数解析资格 JSON。"""
    from app.services.revise_service import apply_business_struct_revise

    raw = json.dumps(
        [
            {
                "id": "q1",
                "requirement": "业绩",
                "response": "两项",
                "evidence": "",
                "status": "partial",
            }
        ],
        ensure_ascii=False,
    )
    applied = apply_business_struct_revise("business_qualify", raw)
    assert applied is not None
    kwargs, j = applied
    assert len(kwargs["business_qualify"]) == 1
    assert "业绩" in kwargs["business_qualify"][0]["requirement"]


def test_business_export_nonempty(client, monkeypatch):
    """用途：商务标 export 生成非空 docx。"""
    proj = client.post(
        "/api/projects", json={"name": "商务导出", "kind": "business"}
    ).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "parsedMarkdown": "条款摘要",
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人",
                    "response": "有",
                    "evidence": "",
                    "status": "matched",
                }
            ],
            "businessCommit": [
                {
                    "id": "c1",
                    "title": "承诺",
                    "body": "正式承诺正文。",
                    "needsStamp": True,
                }
            ],
        },
    )

    task = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export", "payload": {"mode": "business"}},
    )
    assert task.status_code in (200, 201), task.text
    body = task.json()
    assert body["status"] == "success", body
    assert body.get("result", {}).get("size", 0) > 1000
    assert body.get("result", {}).get("mode") == "business"
