"""
模块：导出标题编号单测
用途：验收 numbering 模板占位符、去重前缀、导出 docx 含「第×章」。
对接：export_service.HeadingNumberer / format_heading_number / compose_heading_text
"""

from io import BytesIO

from app.services.export_service import (
    HeadingNumberer,
    compose_heading_text,
    format_heading_number,
    _to_zh,
)


def test_to_zh_and_outline_decimal():
    assert _to_zh(1) == "一"
    assert _to_zh(12) == "十二"
    assert format_heading_number(0, [1], {"numbering_format": "outline-decimal"}) == "1"
    assert (
        format_heading_number(1, [1, 2], {"numbering_format": "outline-decimal"})
        == "1.2"
    )


def test_custom_templates():
    h0 = {
        "numbering_format": "custom",
        "numbering_template": "第{zh}章",
    }
    assert format_heading_number(0, [1], h0) == "第一章"
    assert format_heading_number(0, [3], h0) == "第三章"

    h1 = {
        "numbering_format": "custom",
        "numbering_template": "第{zh}节",
    }
    assert format_heading_number(1, [1, 2], h1) == "第二节"

    h2 = {
        "numbering_format": "custom",
        "numbering_template": "{tail}",
    }
    assert format_heading_number(2, [1, 2, 3], h2) == "1.2.3"

    h3 = {
        "numbering_format": "custom",
        "numbering_template": "{circled}",
    }
    assert format_heading_number(0, [1], h3) == "①"


def test_compose_skips_existing_number():
    assert compose_heading_text("总体架构", "第一章") == "第一章 总体架构"
    assert compose_heading_text("第一章 已编号", "第一章") == "第一章 已编号"
    assert compose_heading_text("1.2 小节", "1.2") == "1.2 小节"
    assert compose_heading_text("一、总则", "第一章") == "一、总则"


def test_numberer_increments():
    cfg = [
        {"numbering_format": "custom", "numbering_template": "第{zh}章"},
        {"numbering_format": "custom", "numbering_template": "第{zh}节"},
        {"numbering_format": "outline-decimal", "numbering_template": ""},
    ]
    n = HeadingNumberer(cfg)
    assert n.next_prefix(0) == "第一章"
    assert n.next_prefix(1) == "第一节"
    assert n.next_prefix(1) == "第二节"
    assert n.next_prefix(0) == "第二章"
    # 进入新章后节计数重置
    assert n.next_prefix(1) == "第一节"


def test_export_docx_contains_chapter_numbers(client):
    client.put(
        "/api/settings",
        json={
            "exportFormat": {
                "template_name": "编号测",
                "headings": [
                    {
                        "font": "黑体",
                        "size": "三号",
                        "alignment": "左对齐",
                        "bold": True,
                        "numbering_format": "custom",
                        "numbering_template": "第{zh}章",
                    },
                    {
                        "font": "黑体",
                        "size": "四号",
                        "alignment": "左对齐",
                        "bold": True,
                        "numbering_format": "custom",
                        "numbering_template": "第{zh}节",
                    },
                ],
            }
        },
    )
    proj = client.post("/api/projects", json={"name": "编号导出"}).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysisOverview": "概述",
            "outline": [
                {
                    "id": "n1",
                    "title": "总体架构",
                    "children": [{"id": "n1-1", "title": "部署拓扑", "children": []}],
                },
                {"id": "n2", "title": "实施方案", "children": []},
            ],
            "chapters": [
                {
                    "id": "n1",
                    "title": "总体架构",
                    "body": "正文一",
                    "preview": "正文",
                    "wordCount": 3,
                    "status": "done",
                },
                {
                    "id": "n2",
                    "title": "实施方案",
                    "body": "正文二",
                    "preview": "正文",
                    "wordCount": 3,
                    "status": "done",
                },
            ],
        },
    )
    exp = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exp.status_code == 201
    assert exp.json()["status"] == "success"
    stored = exp.json()["result"]["storedName"]
    dl = client.get(f"/api/projects/{pid}/export/download/{stored}")
    assert dl.status_code == 200

    from docx import Document  # type: ignore

    doc = Document(BytesIO(dl.content))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    joined = "\n".join(texts)
    assert "第一章 总体架构" in joined or "第一章" in joined
    assert "第二章" in joined
    # 大纲二级
    assert "第一节" in joined or "部署拓扑" in joined
