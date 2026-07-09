"""
模块：Word 导出服务
用途：将 editor-state 中的大纲/章节/概述导出为 .docx 字节流。
对接：POST/GET 导出任务与下载
二次开发：对齐 export-format 模板样式（字体/页边距）。
"""

from __future__ import annotations

import io
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Project, ProjectEditorStateRow
from app.services.project_service import get_project


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def build_docx_bytes(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> tuple[bytes, str]:
    """
    用途：生成 Word 文档。
    返回：(文件字节, 下载文件名)
    """
    try:
        from docx import Document  # type: ignore
        from docx.shared import Pt  # type: ignore
    except ImportError as exc:
        raise RuntimeError("未安装 python-docx，无法导出 Word") from exc

    project = get_project(db, workspace_id, project_id)
    state = db.get(ProjectEditorStateRow, project_id)

    doc = Document()
    title = project.name or "技术标"
    doc.add_heading(title, level=0)

    overview = (state.analysis_overview if state else None) or ""
    if overview.strip():
        doc.add_heading("一、项目概述 / 招标分析", level=1)
        for para in overview.strip().split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip())

    parsed = (state.parsed_markdown if state else None) or ""
    if parsed.strip():
        doc.add_heading("二、招标文件解析摘录", level=1)
        # 控制长度，避免超大文档
        clip = parsed.strip()
        if len(clip) > 20000:
            clip = clip[:20000] + "\n\n…（导出已截断）"
        for para in clip.split("\n"):
            p = doc.add_paragraph(para)
            for run in p.runs:
                run.font.size = Pt(10.5)

    outline = _loads(state.outline_json) if state else None
    if isinstance(outline, list) and outline:
        doc.add_heading("三、目录大纲", level=1)

        def walk(nodes: list, depth: int = 1) -> None:
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                t = str(n.get("title") or "未命名")
                level = min(depth + 1, 4)
                doc.add_heading(t, level=level)
                desc = n.get("description")
                if desc:
                    doc.add_paragraph(str(desc))
                children = n.get("children")
                if isinstance(children, list) and children:
                    walk(children, depth + 1)

        walk(outline)

    chapters = _loads(state.chapters_json) if state else None
    if isinstance(chapters, list) and chapters:
        doc.add_heading("四、正文", level=1)
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            doc.add_heading(str(ch.get("title") or "章节"), level=2)
            body = str(ch.get("body") or "").strip()
            if not body:
                doc.add_paragraph("（本章暂无正文）")
                continue
            for para in body.split("\n"):
                if para.strip():
                    doc.add_paragraph(para.strip())

    facts = _loads(state.facts_json) if state else None
    if isinstance(facts, list) and facts:
        doc.add_heading("五、全局事实", level=1)
        for f in facts:
            if not isinstance(f, dict):
                continue
            cat = f.get("category") or ""
            content = f.get("content") or ""
            doc.add_paragraph(f"[{cat}] {content}", style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    filename = f"{title}.docx".replace("/", "_").replace("\\", "_")
    return buf.getvalue(), filename
