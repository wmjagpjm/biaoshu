"""
模块：资源中心 API 测试
用途：验收系统资源种子、当前工作空间用户资源 CRUD、跨工作空间隔离、系统写保护和服务端浏览量累加。
对接：/api/resources；resource_service；pytest TestClient。
二次开发：若后续增加受控同步或资源版本，必须补资源来源、可见性和计数一致性的回归用例，不得恢复前端 mock 断言。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.entities import ResourceRow, Workspace


def _create_other_workspace() -> str:
    """用途：创建第二工作空间，验证资源 API 不能越过归属边界。"""
    workspace_id = "ws_resources_other"
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=workspace_id,
                name="资源隔离测试工作空间",
                owner_user_id="user_resources_other",
            )
        )
        db.commit()
    finally:
        db.close()
    return workspace_id


def _create_resource(client, **overrides):
    """用途：创建默认工作空间用户资源，减少各用例无关的请求重复。"""
    payload = {
        "title": "项目资格核验清单",
        "description": "用于提交前核验资格、签章和材料有效期。",
        "category": "合规",
        "tags": ["资格", "合规"],
        "bodyMarkdown": "# 项目资格核验\n\n- [ ] 资格材料在有效期内",
        "tone": "blue",
    }
    payload.update(overrides)
    response = client.post("/api/resources", json=payload)
    assert response.status_code == 201
    return response.json()


def test_system_resources_are_global_read_only_seed_records(client):
    response = client.get("/api/resources")

    assert response.status_code == 200
    items = response.json()
    system_items = [item for item in items if item["source"] == "system"]
    assert len(system_items) == 6
    assert all(item["workspaceId"] is None for item in system_items)
    assert {item["id"] for item in system_items} >= {"res_system_scoring_response"}
    assert all(item["bodyMarkdown"] for item in system_items)


def test_resource_source_and_workspace_relation_has_database_constraint():
    db = SessionLocal()
    try:
        db.add(
            ResourceRow(
                id="res_invalid_source_workspace",
                workspace_id=None,
                source="user",
                title="不应写入",
                body_markdown="不应写入",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()


def test_user_resource_crud_and_server_side_filters(client):
    created = _create_resource(
        client,
        source="system",
        workspaceId="ws_resources_other",
    )

    assert created["source"] == "user"
    assert created["workspaceId"] == "ws_local"
    assert created["title"] == "项目资格核验清单"
    assert created["viewCount"] == 0

    fetched = client.get(f"/api/resources/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["bodyMarkdown"].startswith("# 项目资格核验")

    filtered = client.get(
        "/api/resources",
        params={"q": "资格核验", "tag": "资格", "category": "合规"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [created["id"]]

    patched = client.patch(
        f"/api/resources/{created['id']}",
        json={"title": "项目资格核验清单（修订）", "tags": ["资格", "签章"]},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "项目资格核验清单（修订）"
    assert patched.json()["tags"] == ["资格", "签章"]

    assert client.delete(f"/api/resources/{created['id']}").status_code == 204
    assert client.get(f"/api/resources/{created['id']}").status_code == 404


def test_resource_workspace_isolation_and_system_write_protection(client):
    created = _create_resource(client)
    workspace_id = _create_other_workspace()
    headers = {"X-Workspace-Id": workspace_id}

    assert client.get(f"/api/resources/{created['id']}", headers=headers).status_code == 404
    assert (
        client.patch(
            f"/api/resources/{created['id']}",
            json={"title": "不应被其他工作空间修改"},
            headers=headers,
        ).status_code
        == 404
    )
    assert client.delete(f"/api/resources/{created['id']}", headers=headers).status_code == 404
    assert client.post(f"/api/resources/{created['id']}/view", headers=headers).status_code == 404
    other_workspace_items = client.get("/api/resources", headers=headers)
    assert other_workspace_items.status_code == 200
    assert created["id"] not in {item["id"] for item in other_workspace_items.json()}
    assert sum(item["source"] == "system" for item in other_workspace_items.json()) == 6

    system_id = "res_system_scoring_response"
    assert client.patch(f"/api/resources/{system_id}", json={"title": "篡改"}).status_code == 403
    assert client.delete(f"/api/resources/{system_id}").status_code == 403
    system_before = client.get(f"/api/resources/{system_id}").json()
    system_viewed = client.post(f"/api/resources/{system_id}/view")
    assert system_viewed.status_code == 200
    assert system_viewed.json()["viewCount"] == system_before["viewCount"] + 1
    assert system_viewed.json()["updatedAt"] == system_before["updatedAt"]


def test_resource_view_count_is_persisted_by_server(client):
    created = _create_resource(client)

    first = client.post(f"/api/resources/{created['id']}/view")
    second = client.post(f"/api/resources/{created['id']}/view")

    assert first.status_code == 200
    assert first.json()["viewCount"] == 1
    assert first.json()["updatedAt"] == created["updatedAt"]
    assert second.status_code == 200
    assert second.json()["viewCount"] == 2
    assert client.get(f"/api/resources/{created['id']}").json()["viewCount"] == 2
