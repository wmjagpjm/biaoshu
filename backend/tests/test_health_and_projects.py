"""
模块：健康检查与项目 CRUD 测试
用途：验收 P0 契约——探活 200、创建/列表/详情/更新/删除、JSON 字段 camelCase。
对接：pytest + TestClient(app)；数据库由 conftest 隔离为内存 SQLite。
二次开发：新增 API 时在此或同级文件补对应用例，避免只靠手测。
"""


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "biaoshu-backend"


def test_create_and_list_projects(client):
    assert client.get("/api/projects").json() == []

    created = client.post(
        "/api/projects",
        json={
            "name": "  测试项目  ",
            "industry": "智慧城市",
            "technicalPlanStep": 2,
        },
    )
    assert created.status_code == 201
    data = created.json()
    assert data["name"] == "测试项目"
    assert data["industry"] == "智慧城市"
    assert data["status"] == "draft"
    assert data["workspaceId"] == "ws_local"
    assert data["technicalPlanStep"] == 2
    assert data["wordCount"] == 0
    assert "updatedAt" in data
    assert data["id"].startswith("proj_")

    listed = client.get("/api/projects").json()
    assert len(listed) == 1
    assert listed[0]["id"] == data["id"]


def test_get_patch_delete_project(client):
    created = client.post(
        "/api/projects",
        json={"name": "可更新项目"},
    ).json()
    pid = created["id"]

    got = client.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    assert got.json()["name"] == "可更新项目"

    patched = client.patch(
        f"/api/projects/{pid}",
        json={
            "name": "已改名",
            "status": "writing",
            "technicalPlanStep": 5,
            "wordCount": 1200,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "已改名"
    assert body["status"] == "writing"
    assert body["technicalPlanStep"] == 5
    assert body["wordCount"] == 1200

    missing = client.get("/api/projects/proj_not_exist")
    assert missing.status_code == 404

    deleted = client.delete(f"/api/projects/{pid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
