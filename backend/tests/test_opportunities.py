"""
模块：本地标讯库 API 测试
用途：验收工作空间隔离的标讯 CRUD、截止状态筛选和从标讯创建技术标项目的弱关联边界。
对接：/api/opportunities、/api/opportunities/{id}/projects、projects API。
二次开发：引入外部数据源、批量导入或多工作空间鉴权时，必须补跨源归属、截止日和项目删除边界回归。
"""

import json
from datetime import date, timedelta

import pytest

from app.core.database import SessionLocal
from app.models.entities import Workspace
from app.services import opportunity_service


def _create_other_workspace() -> str:
    """用途：写入第二个工作空间，供 API 隔离回归测试使用。"""
    workspace_id = "ws_other"
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=workspace_id,
                name="隔离测试工作空间",
                owner_user_id="user_other",
            )
        )
        db.commit()
    finally:
        db.close()
    return workspace_id


def test_default_lifespan_does_not_seed_sample_opportunities(client):
    response = client.get("/api/opportunities")

    assert response.status_code == 200
    assert response.json() == []


def test_opportunity_status_boundaries():
    today = date(2026, 7, 10)

    assert opportunity_service.calculate_status(today - timedelta(days=1), today=today) == "closed"
    assert opportunity_service.calculate_status(today, today=today) == "closing_soon"
    assert opportunity_service.calculate_status(today + timedelta(days=7), today=today) == "closing_soon"
    assert opportunity_service.calculate_status(today + timedelta(days=8), today=today) == "open"


def _create_opportunity(client, **overrides):
    payload = {
        "title": "智慧园区综合治理平台",
        "buyer": "某区政务服务中心",
        "region": "华东",
        "budgetLabel": "约 300 万",
        "deadline": (date.today() + timedelta(days=14)).isoformat(),
        "tags": ["园区", "平台"],
        "summary": "统一接入园区设备与事件，建设综合治理平台。",
        "sourceLabel": "本地录入",
    }
    payload.update(overrides)
    response = client.post("/api/opportunities", json=payload)
    assert response.status_code == 201
    return response.json()


def _import_opportunities_file(client, filename: str, content: str, content_type: str):
    """用途：提交内存导入文件，统一覆盖 multipart 边界且不在测试目录落盘。"""
    return client.post(
        "/api/opportunities/import",
        files={"file": (filename, content.encode("utf-8"), content_type)},
    )


def test_opportunity_crud_and_server_side_status_filters(client):
    open_item = _create_opportunity(
        client,
        title="环城信号优化平台",
        deadline=(date.today() + timedelta(days=8)).isoformat(),
        tags=["交通", "信创"],
    )
    closing_item = _create_opportunity(
        client,
        title="医院集成平台",
        region="华北",
        deadline=(date.today() + timedelta(days=7)).isoformat(),
    )
    closed_item = _create_opportunity(
        client,
        title="已截止项目",
        deadline=(date.today() - timedelta(days=1)).isoformat(),
    )

    assert open_item["status"] == "open"
    assert closing_item["status"] == "closing_soon"
    assert closed_item["status"] == "closed"
    assert open_item["workspaceId"] == "ws_local"

    filtered = client.get(
        "/api/opportunities",
        params={"q": "环城", "region": "华东", "status": "open"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [open_item["id"]]

    patched = client.patch(
        f"/api/opportunities/{open_item['id']}",
        json={"title": "环城信号优化平台（已修订）", "tags": ["交通", "视频"]},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "环城信号优化平台（已修订）"
    assert patched.json()["tags"] == ["交通", "视频"]

    assert client.delete(f"/api/opportunities/{closed_item['id']}").status_code == 204
    assert client.get(f"/api/opportunities/{closed_item['id']}").status_code == 404


def test_create_project_from_open_opportunity_and_keep_weak_link(client):
    opportunity = _create_opportunity(client)

    created = client.post(f"/api/opportunities/{opportunity['id']}/projects")
    assert created.status_code == 201
    project = created.json()
    assert project["kind"] == "technical"
    assert project["name"] == opportunity["title"]
    assert project["sourceOpportunityId"] == opportunity["id"]

    second = client.post(
        f"/api/opportunities/{opportunity['id']}/projects",
        json={"name": "同标讯备选方案", "industry": "智慧城市"},
    )
    assert second.status_code == 201
    assert second.json()["id"] != project["id"]
    assert second.json()["sourceOpportunityId"] == opportunity["id"]

    assert client.delete(f"/api/opportunities/{opportunity['id']}").status_code == 204
    retained = client.get(f"/api/projects/{project['id']}")
    assert retained.status_code == 200
    assert retained.json()["sourceOpportunityId"] is None


def test_closed_opportunity_cannot_create_project(client):
    closed = _create_opportunity(
        client,
        title="已截止不得立项",
        deadline=(date.today() - timedelta(days=1)).isoformat(),
    )

    response = client.post(f"/api/opportunities/{closed['id']}/projects")
    assert response.status_code == 400
    assert "截止" in response.json()["detail"]


def test_import_json_opportunities_is_idempotent_by_source_key(client):
    payload = [
        {
            "title": "离线导入智慧园区平台",
            "buyer": "导入采购中心",
            "region": "华东",
            "budgetLabel": "约 360 万",
            "deadline": (date.today() + timedelta(days=9)).isoformat(),
            "tags": ["园区", "平台"],
            "summary": "来自本机 JSON 的导入测试记录。",
            "sourceLabel": "本地 JSON",
            "sourceKey": "local-json-001",
            "workspaceId": "ws_resources_other",
        },
        {
            "title": "离线导入临期项目",
            "deadline": (date.today() + timedelta(days=2)).isoformat(),
            "sourceKey": "local-json-002",
        },
    ]

    first = _import_opportunities_file(
        client,
        "opportunities.json",
        json.dumps(payload, ensure_ascii=False),
        "application/json",
    )
    assert first.status_code == 201
    assert first.json() == {"inserted": 2, "skipped": 0, "total": 2}

    listed = client.get("/api/opportunities")
    assert listed.status_code == 200
    items = {item["title"]: item for item in listed.json()}
    assert items["离线导入智慧园区平台"]["workspaceId"] == "ws_local"
    assert items["离线导入智慧园区平台"]["tags"] == ["园区", "平台"]
    assert items["离线导入临期项目"]["status"] == "closing_soon"

    second = _import_opportunities_file(
        client,
        "opportunities.json",
        json.dumps(payload, ensure_ascii=False),
        "application/json",
    )
    assert second.status_code == 201
    assert second.json() == {"inserted": 0, "skipped": 2, "total": 2}
    assert len(client.get("/api/opportunities").json()) == 2

    other_headers = {"X-Workspace-Id": _create_other_workspace()}
    other_workspace_import = client.post(
        "/api/opportunities/import",
        files={
            "file": (
                "opportunities.json",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json",
            )
        },
        headers=other_headers,
    )
    assert other_workspace_import.status_code == 201
    assert other_workspace_import.json() == {"inserted": 2, "skipped": 0, "total": 2}
    assert len(client.get("/api/opportunities", headers=other_headers).json()) == 2


def test_import_csv_with_chinese_headers_and_keep_workspace_isolation(client):
    csv_content = "\n".join(
        [
            " 标题 , 采购人,地区 ,预算,截止日期,标签,摘要,来源,来源键 ",
            (
                "中文表头导入,本地采购人,华南,约 88 万,"
                f"{(date.today() + timedelta(days=10)).isoformat()},"
                "教育；安全,CSV 摘要,本地 CSV,csv-source-001"
            ),
        ]
    )

    imported = _import_opportunities_file(
        client,
        "opportunities.csv",
        csv_content,
        "text/csv",
    )
    assert imported.status_code == 201
    assert imported.json() == {"inserted": 1, "skipped": 0, "total": 1}

    item = next(
        item
        for item in client.get("/api/opportunities").json()
        if item["title"] == "中文表头导入"
    )
    assert item["buyer"] == "本地采购人"
    assert item["budgetLabel"] == "约 88 万"
    assert item["tags"] == ["教育", "安全"]
    assert item["sourceLabel"] == "本地 CSV"

    headers = {"X-Workspace-Id": _create_other_workspace()}
    assert client.get("/api/opportunities", headers=headers).json() == []


def test_import_is_atomic_when_any_row_is_invalid(client):
    payload = [
        {
            "title": "本应回滚的有效标讯",
            "deadline": (date.today() + timedelta(days=10)).isoformat(),
            "sourceKey": "atomic-valid",
        },
        {
            "title": "日期非法标讯",
            "deadline": "20269999",
            "sourceKey": "atomic-invalid",
        },
    ]

    response = _import_opportunities_file(
        client,
        "invalid.json",
        json.dumps(payload, ensure_ascii=False),
        "application/json",
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["errors"] == [{"row": 2, "field": "deadline", "message": "截止日期必须为 YYYY-MM-DD"}]
    assert client.get("/api/opportunities").json() == []


def test_import_rejects_unsupported_file_extension(client):
    response = _import_opportunities_file(
        client,
        "not-supported.txt",
        "标题,截止日期\n不应导入,2026-08-01",
        "text/plain",
    )

    assert response.status_code == 400
    assert "CSV 或 JSON" in response.json()["detail"]


def test_import_rejects_duplicate_source_key_without_writing_any_row(client):
    payload = [
        {
            "title": "重复来源键第一行",
            "deadline": (date.today() + timedelta(days=10)).isoformat(),
            "sourceKey": "same-source-key",
        },
        {
            "title": "重复来源键第二行",
            "deadline": (date.today() + timedelta(days=11)).isoformat(),
            "sourceKey": "same-source-key",
        },
    ]

    response = _import_opportunities_file(
        client,
        "duplicate-key.json",
        json.dumps(payload, ensure_ascii=False),
        "application/json",
    )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == [
        {"row": 2, "field": "sourceKey", "message": "来源键在文件内重复"}
    ]
    assert client.get("/api/opportunities").json() == []


def test_import_rejects_file_larger_than_configured_limit(client):
    response = client.post(
        "/api/opportunities/import",
        files={
            "file": (
                "too-large.json",
                b"[" + (b" " * (2 * 1024 * 1024)) + b"]",
                "application/json",
            )
        },
    )

    assert response.status_code == 400
    assert "2097152" in response.json()["detail"]


def test_import_rejects_non_utf8_and_too_many_rows(client):
    non_utf8 = client.post(
        "/api/opportunities/import",
        files={"file": ("invalid-encoding.json", b"\xff", "application/json")},
    )
    assert non_utf8.status_code == 400
    assert "UTF-8" in non_utf8.json()["detail"]

    too_many_rows = "title,deadline\n" + "超限标讯,2026-08-01\n" * 2001
    over_row_limit = _import_opportunities_file(
        client,
        "too-many.csv",
        too_many_rows,
        "text/csv",
    )
    assert over_row_limit.status_code == 400
    assert "2000" in over_row_limit.json()["detail"]
    assert client.get("/api/opportunities").json() == []


def test_opportunity_cross_workspace_access_returns_not_found(client):
    opportunity = _create_opportunity(client)
    workspace_id = _create_other_workspace()
    headers = {"X-Workspace-Id": workspace_id}

    assert client.get(f"/api/opportunities/{opportunity['id']}", headers=headers).status_code == 404
    assert (
        client.patch(
            f"/api/opportunities/{opportunity['id']}",
            json={"title": "不应被另一个工作空间修改"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/opportunities/{opportunity['id']}/projects", headers=headers).status_code
        == 404
    )
    assert client.delete(f"/api/opportunities/{opportunity['id']}", headers=headers).status_code == 404
    assert client.get("/api/projects", headers=headers).json() == []


def test_project_creation_failure_rolls_back_the_partial_project(client, monkeypatch):
    opportunity = _create_opportunity(client, title="事务回滚验证标讯")
    original_create_project = opportunity_service.create_project

    def _create_then_fail(*args, **kwargs):
        original_create_project(*args, **kwargs)
        raise RuntimeError("模拟项目创建后异常")

    monkeypatch.setattr(opportunity_service, "create_project", _create_then_fail)
    db = SessionLocal()
    try:
        with pytest.raises(RuntimeError, match="模拟项目创建后异常"):
            opportunity_service.create_project_from_opportunity(
                db,
                "ws_local",
                opportunity["id"],
            )
    finally:
        db.close()

    projects = client.get("/api/projects")
    assert projects.status_code == 200
    assert all(project["sourceOpportunityId"] != opportunity["id"] for project in projects.json())
