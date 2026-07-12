"""
模块：技术标中标内容模板 MVP 验收
用途：沉淀/列表/详情/删除、从模板新建独立 editor-state、跨 workspace 404、源项目删除语义与非法快照 400。
对接：template_service；/api/templates；project_service.delete_project。
二次开发：扩展商务模板或融合写入时另立测试文件，勿削弱本文件对「独立快照」边界的断言。
"""

from __future__ import annotations


def _seed_technical_project(client, *, name: str = "中标样例项目"):
    """用途：创建技术标项目并写入非空大纲/章节。"""
    proj = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical", "industry": "政务"},
    ).json()
    pid = proj["id"]
    put = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "outline": [
                {
                    "id": "node_arch",
                    "title": "总体架构",
                    "children": [
                        {"id": "node_arch_1", "title": "分层设计", "children": []}
                    ],
                },
                {"id": "node_sec", "title": "安全方案", "children": []},
            ],
            "chapters": [
                {
                    "id": "chap_arch",
                    "title": "总体架构",
                    "body": "采用分层微服务架构，前后端分离。",
                },
                {
                    "id": "chap_sec",
                    "title": "安全方案",
                    "body": "等保三级，传输加密与审计留痕。",
                },
            ],
            "facts": [{"id": "f1", "label": "部署方式", "value": "私有云"}],
            "mode": "ALIGNED",
            "guidance": {"audience": "评标专家", "tone": "严谨"},
        },
    )
    assert put.status_code == 200, put.text
    return proj


def test_create_list_get_and_create_project_from_template(client):
    """用途：同 workspace 沉淀 → 列表/详情 → 从模板新建，新项目有独立 outline/chapters 副本。"""
    source = _seed_technical_project(client, name="源项目甲")
    created = client.post(
        "/api/templates/from-project",
        json={
            "projectId": source["id"],
            "title": "甲公司中标模板",
            "tags": ["政务", "安全"],
        },
    )
    assert created.status_code == 201, created.text
    tpl = created.json()
    assert tpl["title"] == "甲公司中标模板"
    assert tpl["kind"] == "technical"
    assert tpl["status"] == "active"
    assert tpl["workspaceId"]
    assert tpl["sourceProjectId"] == source["id"]
    assert tpl["sourceProjectName"] == "源项目甲"
    assert tpl["tags"] == ["政务", "安全"]
    assert tpl["snapshot"]["outline"][0]["title"] == "总体架构"
    assert tpl["snapshot"]["chapters"][1]["title"] == "安全方案"
    assert tpl["snapshot"]["facts"][0]["value"] == "私有云"

    listed = client.get("/api/templates", params={"q": "中标"}).json()
    assert any(item["id"] == tpl["id"] for item in listed)
    listed_item = next(item for item in listed if item["id"] == tpl["id"])
    # 列表只返回摘要，不得携带完整 snapshot
    assert "snapshot" not in listed_item
    assert listed_item["chapterCount"] == 2
    assert "总体架构" in listed_item["outlineTitles"]
    assert "安全方案" in listed_item["outlineTitles"]

    detail = client.get(f"/api/templates/{tpl['id']}")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["chapters"][0]["body"].startswith("采用分层")

    project_res = client.post(
        f"/api/templates/{tpl['id']}/projects",
        json={"name": "从模板新建的草稿", "industry": "政务"},
    )
    assert project_res.status_code == 201, project_res.text
    new_proj = project_res.json()
    assert new_proj["id"] != source["id"]
    assert new_proj["name"] == "从模板新建的草稿"
    assert new_proj["kind"] == "technical"
    assert new_proj["status"] == "draft"

    state = client.get(f"/api/projects/{new_proj['id']}/editor-state").json()
    assert state["outline"][0]["title"] == "总体架构"
    assert state["chapters"][1]["body"].startswith("等保三级")
    assert state["facts"][0]["label"] == "部署方式"
    assert state["guidance"]["audience"] == "评标专家"

    # 修改新项目不得回写模板快照
    client.put(
        f"/api/projects/{new_proj['id']}/editor-state",
        json={
            "outline": [{"id": "n_x", "title": "已改大纲", "children": []}],
            "chapters": [{"id": "c_x", "title": "已改章节", "body": "改了"}],
        },
    )
    still = client.get(f"/api/templates/{tpl['id']}").json()
    assert still["snapshot"]["outline"][0]["title"] == "总体架构"
    assert still["snapshot"]["chapters"][0]["body"].startswith("采用分层")


def test_cross_workspace_404(client):
    """用途：另一 X-Workspace-Id 对模板读写均为 404。"""
    source = _seed_technical_project(client)
    tpl = client.post(
        "/api/templates/from-project",
        json={"projectId": source["id"], "title": "隔离模板"},
    ).json()

    other = {"X-Workspace-Id": "ws_other_isolation"}
    assert client.get("/api/templates", headers=other).json() == []
    assert (
        client.get(f"/api/templates/{tpl['id']}", headers=other).status_code == 404
    )
    assert (
        client.delete(f"/api/templates/{tpl['id']}", headers=other).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/templates/{tpl['id']}/projects",
            json={"name": "越权草稿"},
            headers=other,
        ).status_code
        == 404
    )
    # 用其他 workspace 的项目 id 沉淀也 404
    assert (
        client.post(
            "/api/templates/from-project",
            json={"projectId": source["id"], "title": "越权沉淀"},
            headers=other,
        ).status_code
        == 404
    )


def test_delete_source_project_keeps_template_snapshot(client):
    """用途：删除源项目后模板仍可读，sourceProjectId 置空，快照内容不变。"""
    source = _seed_technical_project(client, name="将删除源项目")
    tpl = client.post(
        "/api/templates/from-project",
        json={"projectId": source["id"], "title": "保留快照模板"},
    ).json()
    original_body = tpl["snapshot"]["chapters"][0]["body"]

    deleted = client.delete(f"/api/projects/{source['id']}")
    assert deleted.status_code == 204

    after = client.get(f"/api/templates/{tpl['id']}")
    assert after.status_code == 200, after.text
    body = after.json()
    assert body["sourceProjectId"] is None
    assert body["sourceProjectName"] == "将删除源项目"
    assert body["snapshot"]["chapters"][0]["body"] == original_body
    assert body["snapshot"]["outline"][0]["title"] == "总体架构"

    # 仍可从模板新建
    proj = client.post(
        f"/api/templates/{tpl['id']}/projects",
        json={"name": "源删后仍可建"},
    )
    assert proj.status_code == 201, proj.text
    state = client.get(f"/api/projects/{proj.json()['id']}/editor-state").json()
    assert state["outline"][0]["id"] == "node_arch"


def test_delete_template_does_not_affect_project(client):
    """用途：删除模板不影响源项目及其 editor-state。"""
    source = _seed_technical_project(client, name="模板可删源保留")
    tpl = client.post(
        "/api/templates/from-project",
        json={"projectId": source["id"], "title": "将被删除的模板"},
    ).json()
    assert client.delete(f"/api/templates/{tpl['id']}").status_code == 204
    assert client.get(f"/api/templates/{tpl['id']}").status_code == 404

    proj = client.get(f"/api/projects/{source['id']}")
    assert proj.status_code == 200
    state = client.get(f"/api/projects/{source['id']}/editor-state").json()
    assert state["outline"][0]["title"] == "总体架构"
    assert state["chapters"][0]["body"].startswith("采用分层")


def test_empty_outline_rejected(client):
    """用途：空大纲沉淀返回 400。"""
    proj = client.post(
        "/api/projects", json={"name": "无大纲项目", "kind": "technical"}
    ).json()
    # 默认 editor-state 无大纲
    res = client.post(
        "/api/templates/from-project",
        json={"projectId": proj["id"], "title": "应失败"},
    )
    assert res.status_code == 400
    assert "大纲" in res.json()["detail"]

    # 显式空列表
    client.put(
        f"/api/projects/{proj['id']}/editor-state",
        json={"outline": [], "chapters": []},
    )
    res2 = client.post(
        "/api/templates/from-project",
        json={"projectId": proj["id"], "title": "仍应失败"},
    )
    assert res2.status_code == 400


def test_oversized_snapshot_rejected(client, monkeypatch):
    """用途：超过合理上限的快照返回 400。"""
    from app.services import template_service as ts

    monkeypatch.setattr(ts, "MAX_SNAPSHOT_CHARS", 200)
    source = _seed_technical_project(client, name="超大快照项目")
    # 写入较长正文，使序列化后超过 200
    client.put(
        f"/api/projects/{source['id']}/editor-state",
        json={
            "outline": [{"id": "n1", "title": "章", "children": []}],
            "chapters": [
                {
                    "id": "c1",
                    "title": "章",
                    "body": "超长正文" * 80,
                }
            ],
        },
    )
    res = client.post(
        "/api/templates/from-project",
        json={"projectId": source["id"], "title": "超大应拒"},
    )
    assert res.status_code == 400
    assert "过大" in res.json()["detail"] or "上限" in res.json()["detail"]


def test_business_project_cannot_create_template(client):
    """用途：商务标项目沉淀被拒绝。"""
    biz = client.post(
        "/api/projects", json={"name": "商务项", "kind": "business"}
    ).json()
    client.put(
        f"/api/projects/{biz['id']}/editor-state",
        json={
            "outline": [{"id": "n1", "title": "资格", "children": []}],
            "chapters": [{"id": "c1", "title": "资格", "body": "x"}],
        },
    )
    res = client.post(
        "/api/templates/from-project",
        json={"projectId": biz["id"], "title": "商务模板"},
    )
    assert res.status_code == 400
    assert "技术标" in res.json()["detail"]


def test_list_templates_returns_summary_without_snapshot(client):
    """用途：列表接口返回元数据与轻量摘要，不含完整 snapshot；详情仍可取完整快照。"""
    source = _seed_technical_project(client, name="列表摘要源项目")
    tpl = client.post(
        "/api/templates/from-project",
        json={
            "projectId": source["id"],
            "title": "列表摘要模板",
            "tags": ["摘要"],
        },
    ).json()
    assert "snapshot" in tpl
    assert tpl["snapshot"]["chapters"][0]["body"].startswith("采用分层")

    listed = client.get("/api/templates", params={"q": "列表摘要"}).json()
    assert len(listed) >= 1
    item = next(row for row in listed if row["id"] == tpl["id"])
    assert "snapshot" not in item
    assert item["title"] == "列表摘要模板"
    assert item["tags"] == ["摘要"]
    assert item["chapterCount"] == 2
    assert item["outlineTitles"] == ["总体架构", "安全方案"]
    assert item["sourceProjectId"] == source["id"]
    assert item["sourceProjectName"] == "列表摘要源项目"

    # 列表 JSON 中不得出现章节正文（防止误回填 snapshot 字段）
    raw = client.get("/api/templates", params={"q": "列表摘要"}).text
    assert "采用分层微服务架构" not in raw
    assert "等保三级" not in raw

    detail = client.get(f"/api/templates/{tpl['id']}").json()
    assert detail["snapshot"]["chapters"][0]["body"].startswith("采用分层")
    assert detail["snapshot"]["outline"][0]["title"] == "总体架构"
