"""
模块：Word 导出标题边框测试
用途：验收标题段落描边、分级底色、关闭状态及 camelCase 配置兼容。
对接：export_service 标题渲染；设置 exportFormat；export 同步任务。
二次开发：整章页框和最小标题左栏不属于本测试范围，新增语义需另建用例。
"""

from io import BytesIO

from app.services.export_service import (
    _apply_heading_border,
    _heading_border_cfg,
    write_markdown_body,
)


def test_heading_border_config_accepts_camel_case_and_sanitizes_colors():
    cfg = _heading_border_cfg(
        {
            "headingBorder": {
                "enabled": True,
                "borderColor": "#123abc",
                "levelCellColors": ["#abcdef", "不是颜色"],
            }
        }
    )

    assert cfg["enabled"] is True
    assert cfg["border_color"] == "123ABC"
    assert cfg["level_cell_colors"] == ["ABCDEF", "FFFFFF"]

    disabled = _heading_border_cfg({"headingBorder": {"enabled": "false"}})
    enabled = _heading_border_cfg({"headingBorder": {"enabled": "true"}})
    assert disabled["enabled"] is False
    assert enabled["enabled"] is True


def test_apply_heading_border_writes_paragraph_ooxml():
    from docx import Document  # type: ignore

    doc = Document()
    paragraph = doc.add_heading("一级标题", level=1)

    _apply_heading_border(
        paragraph,
        {
            "enabled": True,
            "border_color": "123456",
            "level_cell_colors": ["ABCDEF"],
        },
        level_index=0,
    )

    xml = paragraph._p.xml
    assert "w:pBdr" in xml
    assert 'w:color="123456"' in xml
    assert 'w:fill="ABCDEF"' in xml
    for edge_name in ("top", "left", "bottom", "right"):
        assert f"w:{edge_name}" in xml


def test_heading_border_keeps_ooxml_property_order():
    from docx import Document  # type: ignore
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
    from docx.shared import Pt  # type: ignore

    doc = Document()
    paragraph = doc.add_heading("已有段落属性", level=1)
    paragraph.paragraph_format.space_before = Pt(6)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    _apply_heading_border(
        paragraph,
        {
            "enabled": True,
            "border_color": "123456",
            "level_cell_colors": ["ABCDEF"],
        },
        level_index=0,
    )

    properties = paragraph._p.get_or_add_pPr()
    child_names = [child.tag.rsplit("}", 1)[-1] for child in properties]
    assert child_names.index("pStyle") < child_names.index("pBdr")
    assert child_names.index("pBdr") < child_names.index("shd")
    assert child_names.index("shd") < child_names.index("spacing")
    assert child_names.index("spacing") < child_names.index("jc")


def test_apply_heading_border_disabled_keeps_paragraph_plain():
    from docx import Document  # type: ignore

    doc = Document()
    paragraph = doc.add_heading("普通标题", level=1)

    _apply_heading_border(
        paragraph,
        {
            "enabled": False,
            "border_color": "123456",
            "level_cell_colors": ["ABCDEF"],
        },
        level_index=0,
    )

    xml = paragraph._p.xml
    assert "w:pBdr" not in xml
    assert "w:shd" not in xml


def test_markdown_heading_uses_matching_level_background():
    from docx import Document  # type: ignore

    doc = Document()
    write_markdown_body(
        doc,
        "## 部署拓扑\n正文内容",
        heading_border_cfg={
            "enabled": True,
            "border_color": "123456",
            "level_cell_colors": ["DDEEFF", "EEF6FF", "CCDDEE"],
        },
    )

    heading = next(paragraph for paragraph in doc.paragraphs if paragraph.text == "部署拓扑")
    assert 'w:fill="CCDDEE"' in heading._p.xml


def test_export_heading_border_applies_to_generated_heading(client):
    settings = client.put(
        "/api/settings",
        json={
            "exportFormat": {
                "template_name": "标题边框测试",
                "heading_border": {
                    "enabled": True,
                    "border_color": "#2468AC",
                    "level_cell_colors": [
                        "#DDEEFF",
                        "#EEF6FF",
                        "#FFFFFF",
                        "#FFFFFF",
                        "#FFFFFF",
                        "#FFFFFF",
                    ],
                },
            }
        },
    )
    assert settings.status_code == 200

    project = client.post("/api/projects", json={"name": "标题边框导出"}).json()
    project_id = project["id"]
    state = client.put(
        f"/api/projects/{project_id}/editor-state",
        json={
            "analysisOverview": "用于检查一级标题边框。",
            "chapters": [
                {
                    "id": "chapter-1",
                    "title": "实施方案",
                    "body": "## 部署拓扑\n正文内容",
                    "preview": "正文内容",
                    "wordCount": 4,
                    "status": "done",
                }
            ],
        },
    )
    assert state.status_code == 200

    exported = client.post(
        f"/api/projects/{project_id}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exported.status_code == 201
    stored_name = exported.json()["result"]["storedName"]
    downloaded = client.get(
        f"/api/projects/{project_id}/export/download/{stored_name}"
    )
    assert downloaded.status_code == 200

    from docx import Document  # type: ignore

    doc = Document(BytesIO(downloaded.content))
    overview_heading = next(
        paragraph
        for paragraph in doc.paragraphs
        if paragraph.text == "一、项目概述 / 招标分析"
    )
    xml = overview_heading._p.xml
    assert "w:pBdr" in xml
    assert 'w:color="2468AC"' in xml
    assert 'w:fill="DDEEFF"' in xml


def test_business_export_heading_border_is_applied(client):
    client.put(
        "/api/settings",
        json={
            "exportFormat": {
                "template_name": "商务标题边框测试",
                "heading_border": {
                    "enabled": True,
                    "border_color": "#3579BD",
                    "level_cell_colors": ["#DDEEFF", "#E6F2FF"],
                },
            }
        },
    )
    project = client.post(
        "/api/projects",
        json={"name": "商务边框导出", "kind": "business"},
    ).json()
    project_id = project["id"]
    client.put(
        f"/api/projects/{project_id}/editor-state",
        json={
            "businessQualify": [
                {
                    "id": "qualify-1",
                    "requirement": "法人资格",
                    "response": "已响应",
                    "evidence": "营业执照",
                    "status": "matched",
                }
            ]
        },
    )

    exported = client.post(
        f"/api/projects/{project_id}/tasks?sync=true",
        json={"type": "export", "payload": {"mode": "business"}},
    )
    assert exported.status_code == 201
    stored_name = exported.json()["result"]["storedName"]
    downloaded = client.get(
        f"/api/projects/{project_id}/export/download/{stored_name}"
    )
    assert downloaded.status_code == 200

    from docx import Document  # type: ignore

    doc = Document(BytesIO(downloaded.content))
    matching_titles = [
        paragraph
        for paragraph in doc.paragraphs
        if paragraph.text == "商务边框导出"
    ]
    assert any("w:pBdr" in paragraph._p.xml for paragraph in matching_titles)
