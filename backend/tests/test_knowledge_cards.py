"""
模块：卡片化知识与素材库 MVP 验收
用途：文本/图片卡创建、列表摘要、详情、四类型、跨 workspace 404、源删除后仍可用、
      伪造图片 400、插入项目协议、筛选、删卡不删项目图片、空 title/超大 body 400。
对接：card_service；/api/cards；/api/projects/{id}/insert-card；knowledge/file 服务。
二次开发：扩展融合/向量排序时另立测试，勿削弱本文件对独立快照与插入协议的断言。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.core.database import SessionLocal
from app.models.entities import Workspace


def _png_bytes(color=(40, 120, 200)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (12, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _seed_chunk(client) -> tuple[str, str]:
    """用途：上传知识文档并返回 (doc_id, chunk_id)。"""
    folder = client.post("/api/knowledge/folders", json={"name": "卡片测试夹"}).json()
    upload = client.post(
        "/api/knowledge/docs/upload",
        data={"folderId": folder["id"]},
        files={
            "file": (
                "sample.md",
                BytesIO("# 架构说明\n\n本系统采用分层微服务与前后端分离。\n".encode("utf-8")),
                "text/markdown",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    doc = upload.json()
    assert doc["chunks"] >= 1
    hits = client.get("/api/knowledge/search", params={"q": "分层微服务", "topK": 3})
    assert hits.status_code == 200, hits.text
    items = hits.json()["items"]
    assert items, "应能检索到刚索引的分块"
    return doc["id"], items[0]["chunkId"]


def test_create_list_detail_and_four_types(client):
    """用途：手工创建文本卡 → 列表摘要无全文 → 详情有正文；四类型字段可落库。"""
    created = client.post(
        "/api/cards",
        json={
            "type": "document",
            "title": "总体架构片段",
            "bodyMarkdown": "采用分层微服务架构。",
            "tags": ["架构", "技术"],
            "summary": "分层架构摘要",
            "sourceLabel": "手工录入",
        },
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["type"] == "document"
    assert card["title"] == "总体架构片段"
    assert card["tags"] == ["架构", "技术"]
    assert card["bodyMarkdown"].startswith("采用分层")
    assert card["sourceLabel"] == "手工录入"
    assert card["workspaceId"]

    listed = client.get("/api/cards", params={"q": "总体架构"})
    assert listed.status_code == 200
    items = listed.json()
    assert any(item["id"] == card["id"] for item in items)
    summary_item = next(item for item in items if item["id"] == card["id"])
    assert "bodyMarkdown" not in summary_item
    assert summary_item["hasBody"] is True
    assert summary_item["summary"]

    detail = client.get(f"/api/cards/{card['id']}")
    assert detail.status_code == 200
    assert detail.json()["bodyMarkdown"] == "采用分层微服务架构。"

    for card_type, title, body in [
        ("qualification", "ISO认证", "持有 ISO27001 证书。"),
        ("performance", "某市智慧园区", "2024 年交付智慧园区一期。"),
        ("document", "安全方案摘录", "等保三级与审计留痕。"),
    ]:
        res = client.post(
            "/api/cards",
            json={"type": card_type, "title": title, "bodyMarkdown": body},
        )
        assert res.status_code == 201, res.text
        assert res.json()["type"] == card_type

    by_type = client.get("/api/cards", params={"type": "qualification"}).json()
    assert all(item["type"] == "qualification" for item in by_type)
    assert any(item["title"] == "ISO认证" for item in by_type)


def test_cross_workspace_404(client):
    """用途：跨 workspace 读/写/删一律 404。"""
    created = client.post(
        "/api/cards",
        json={
            "type": "document",
            "title": "隔离卡",
            "bodyMarkdown": "仅本空间可见",
        },
    )
    assert created.status_code == 201
    card_id = created.json()["id"]

    other_ws = "ws_other_cards"
    db = SessionLocal()
    try:
        if db.get(Workspace, other_ws) is None:
            db.add(
                Workspace(
                    id=other_ws,
                    name="其他空间",
                    owner_user_id="user_other",
                )
            )
            db.commit()
    finally:
        db.close()

    headers = {"X-Workspace-Id": other_ws}
    assert client.get(f"/api/cards/{card_id}", headers=headers).status_code == 404
    assert (
        client.patch(
            f"/api/cards/{card_id}",
            headers=headers,
            json={"title": "篡改"},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/cards/{card_id}", headers=headers).status_code == 404


def test_from_chunk_survives_source_delete(client):
    """用途：from-chunk 后删除源文档/分块，卡片详情仍可预览正文。"""
    doc_id, chunk_id = _seed_chunk(client)
    created = client.post(
        "/api/cards/from-chunk",
        json={"chunkId": chunk_id, "title": "沉淀架构块", "tags": ["RAG"]},
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["sourceType"] == "chunk"
    assert card["sourceId"] == chunk_id
    assert "分层微服务" in card["bodyMarkdown"]
    assert "知识分块" in card["sourceLabel"]

    deleted = client.delete(f"/api/knowledge/docs/{doc_id}")
    assert deleted.status_code == 204

    detail = client.get(f"/api/cards/{card['id']}")
    assert detail.status_code == 200
    assert "分层微服务" in detail.json()["bodyMarkdown"]


def test_from_project_image_survives_project_delete(client):
    """用途：from-project-image 后删项目，卡片 content 仍可读。"""
    project = client.post(
        "/api/projects", json={"name": "图片源项目", "kind": "technical"}
    ).json()
    image = client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("topo.png", BytesIO(_png_bytes()), "image/png")},
    )
    assert image.status_code == 201, image.text
    file_id = image.json()["id"]

    created = client.post(
        "/api/cards/from-project-image",
        json={
            "projectId": project["id"],
            "fileId": file_id,
            "title": "拓扑图卡片",
            "tags": ["配图"],
        },
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["type"] == "image"
    assert card["hasImage"] is True
    assert card["sourceType"] == "project_image"
    assert card["sourceId"] == file_id

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204

    content = client.get(f"/api/cards/{card['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/")
    assert content.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_fake_image_rejected(client):
    """用途：伪造图片字节 → 400。"""
    res = client.post(
        "/api/cards/upload-image",
        files={"file": ("fake.png", BytesIO(b"not-an-image"), "image/png")},
        data={"title": "假图"},
    )
    assert res.status_code == 400
    assert "图片" in res.json()["detail"]


def test_insert_image_and_text_into_project(client):
    """用途：图片插入只产生 biaoshu-image；文本插入含标题与来源。"""
    project = client.post(
        "/api/projects", json={"name": "插入目标项目", "kind": "technical"}
    ).json()
    pid = project["id"]

    img_card = client.post(
        "/api/cards/upload-image",
        files={"file": ("net.png", BytesIO(_png_bytes((10, 20, 30))), "image/png")},
        data={"title": "组网图", "tags": "网络"},
    )
    assert img_card.status_code == 201, img_card.text
    image_card_id = img_card.json()["id"]

    insert_img = client.post(
        f"/api/projects/{pid}/insert-card",
        json={"cardId": image_card_id},
    )
    assert insert_img.status_code == 200, insert_img.text
    payload = insert_img.json()
    assert payload["projectImageId"]
    assert payload["projectImageId"].startswith("file_")
    assert f"biaoshu-image://{payload['projectImageId']}" in payload["markdown"]
    assert "http://" not in payload["markdown"]
    assert "data:" not in payload["markdown"]
    assert "knowledge_cards" not in payload["markdown"]

    text_card = client.post(
        "/api/cards",
        json={
            "type": "performance",
            "title": "业绩要点",
            "bodyMarkdown": "完成省级平台建设。",
            "sourceLabel": "历史中标材料",
        },
    ).json()
    insert_text = client.post(
        f"/api/projects/{pid}/insert-card",
        json={"cardId": text_card["id"]},
    )
    assert insert_text.status_code == 200, insert_text.text
    md = insert_text.json()["markdown"]
    assert "业绩要点" in md
    assert "历史中标材料" in md
    assert "完成省级平台建设" in md
    assert insert_text.json()["projectImageId"] is None

    # 删卡不删除已插入项目图片
    assert client.delete(f"/api/cards/{image_card_id}").status_code == 204
    images = client.get(f"/api/projects/{pid}/images")
    assert images.status_code == 200
    assert any(item["id"] == payload["projectImageId"] for item in images.json())


def test_filter_archive_and_validation(client):
    """用途：默认隐藏归档；status=all/archived；空 title/超限 body 400。"""
    a = client.post(
        "/api/cards",
        json={"type": "document", "title": "可检索甲", "bodyMarkdown": "甲内容安全"},
    ).json()
    client.post(
        "/api/cards",
        json={"type": "document", "title": "可检索乙", "bodyMarkdown": "乙内容合规"},
    )

    found = client.get("/api/cards", params={"q": "甲内容"}).json()
    assert any(item["id"] == a["id"] for item in found)

    empty_title = client.post(
        "/api/cards",
        json={"type": "document", "title": "  ", "bodyMarkdown": "有正文"},
    )
    assert empty_title.status_code == 400

    # 阶段2正文上限 20_000；20_001 必须 400
    huge = "x" * (20_000 + 1)
    too_big = client.post(
        "/api/cards",
        json={"type": "document", "title": "超大", "bodyMarkdown": huge},
    )
    assert too_big.status_code == 400

    at_limit = client.post(
        "/api/cards",
        json={"type": "document", "title": "刚好上限", "bodyMarkdown": "y" * 20_000},
    )
    assert at_limit.status_code == 201, at_limit.text

    archived = client.patch(
        f"/api/cards/{a['id']}",
        json={"status": "archived", "title": "已归档甲"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["title"] == "已归档甲"

    # 缺省 status：仅 active，隐藏归档
    default_list = client.get("/api/cards").json()
    assert all(item["status"] == "active" for item in default_list)
    assert not any(item["id"] == a["id"] for item in default_list)

    only_active = client.get("/api/cards", params={"status": "active"}).json()
    assert all(item["status"] == "active" for item in only_active)
    assert not any(item["id"] == a["id"] for item in only_active)

    only_archived = client.get("/api/cards", params={"status": "archived"}).json()
    assert only_archived
    assert all(item["status"] == "archived" for item in only_archived)
    assert any(item["id"] == a["id"] for item in only_archived)

    all_cards = client.get("/api/cards", params={"status": "all"}).json()
    statuses = {item["status"] for item in all_cards}
    assert "active" in statuses and "archived" in statuses
    assert any(item["id"] == a["id"] for item in all_cards)

    bad_status = client.get("/api/cards", params={"status": "deleted"})
    assert bad_status.status_code == 400
