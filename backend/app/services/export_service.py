"""
模块：Word 导出服务
用途：将 editor-state 导出为 .docx；应用 workspace 默认 ExportFormat 核心样式。
对接：export 任务；settings.export_format_json
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ProjectEditorStateRow
from app.services.project_service import get_project
from app.services import settings_service


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# 中文字号 → 磅值
_CN_SIZE_PT: dict[str, float] = {
    "初号": 42,
    "小初": 36,
    "一号": 26,
    "小一": 24,
    "二号": 22,
    "小二": 18,
    "三号": 16,
    "小三": 15,
    "四号": 14,
    "小四": 12,
    "五号": 10.5,
    "小五": 9,
    "六号": 7.5,
    "小六": 6.5,
}


def _pt(size_name: str | None, default: float = 12.0) -> float:
    if not size_name:
        return default
    if size_name in _CN_SIZE_PT:
        return _CN_SIZE_PT[size_name]
    try:
        return float(str(size_name).replace("pt", "").strip())
    except ValueError:
        return default


def _align(name: str | None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore

    m = {
        "居中对齐": WD_ALIGN_PARAGRAPH.CENTER,
        "居中": WD_ALIGN_PARAGRAPH.CENTER,
        "两端对齐": WD_ALIGN_PARAGRAPH.JUSTIFY,
        "左对齐": WD_ALIGN_PARAGRAPH.LEFT,
        "右对齐": WD_ALIGN_PARAGRAPH.RIGHT,
    }
    return m.get(name or "", WD_ALIGN_PARAGRAPH.LEFT)


def _apply_template_styles(doc, cfg: dict) -> None:
    """用途：把 ExportFormatConfig 核心字段应用到 docx 样式。"""
    from docx.shared import Pt, Twips  # type: ignore

    body = cfg.get("body_text") or cfg.get("bodyText") or {}
    if isinstance(body, dict):
        style = doc.styles["Normal"]
        font = style.font
        font.name = body.get("font") or "宋体"
        font.size = Pt(_pt(body.get("size"), 12))
        pf = style.paragraph_format
        try:
            pf.line_spacing = float(body.get("line_spacing_multiple") or body.get("lineSpacingMultiple") or 1.5)
        except (TypeError, ValueError):
            pf.line_spacing = 1.5
        indent = body.get("first_line_indent_chars") or body.get("firstLineIndentChars") or 0
        try:
            # 约 1 字符 ≈ 210 twips（小四）
            pf.first_line_indent = Twips(int(float(indent) * 210))
        except (TypeError, ValueError):
            pass

    headings = cfg.get("headings") or []
    if isinstance(headings, list):
        for i, h in enumerate(headings[:3]):
            if not isinstance(h, dict):
                continue
            style_name = f"Heading {i + 1}"
            try:
                st = doc.styles[style_name]
            except KeyError:
                continue
            st.font.name = h.get("font") or "黑体"
            st.font.size = Pt(_pt(h.get("size"), 16 - i * 2))
            st.font.bold = bool(h.get("bold", True))
            try:
                st.paragraph_format.alignment = _align(h.get("alignment"))
            except Exception:
                pass

    # 页边距（若有 page / margins）
    page = cfg.get("page") or cfg.get("page_setup") or {}
    if isinstance(page, dict):
        section = doc.sections[0]
        from docx.shared import Cm  # type: ignore

        def margin(key_cm: str, key_pt: str, default_cm: float):
            v = page.get(key_cm)
            if v is None:
                v = page.get(key_pt)
            try:
                return Cm(float(v))
            except (TypeError, ValueError):
                return Cm(default_cm)

        # 常见字段名兼容
        section.top_margin = margin("margin_top_cm", "top", 2.54)
        section.bottom_margin = margin("margin_bottom_cm", "bottom", 2.54)
        section.left_margin = margin("margin_left_cm", "left", 3.17)
        section.right_margin = margin("margin_right_cm", "right", 3.17)


def build_docx_bytes(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> tuple[bytes, str]:
    """
    用途：生成 Word 文档（封面 + 正文，应用默认导出模板）。
    """
    try:
        from docx import Document  # type: ignore
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
        from docx.shared import Pt, RGBColor  # type: ignore
    except ImportError as exc:
        raise RuntimeError("未安装 python-docx，无法导出 Word") from exc

    project = get_project(db, workspace_id, project_id)
    state = db.get(ProjectEditorStateRow, project_id)
    template = settings_service.get_export_format(db, workspace_id)

    doc = Document()
    if template:
        try:
            _apply_template_styles(doc, template)
        except Exception:
            pass

    title = project.name or "技术标"

    cover = doc.add_paragraph()
    cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = cover.add_run("技术标书")
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = RGBColor(0x1E, 0x3A, 0x5F)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = t.add_run(title)
    tr.bold = True
    tr.font.size = Pt(16)

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tpl_name = ""
    if template:
        tpl_name = str(template.get("template_name") or template.get("name") or "")
    meta_text = (
        f"行业：{project.industry or '通用'}　｜　"
        f"导出日期：{datetime.now().strftime('%Y-%m-%d')}　｜　"
        f"状态：{project.status}"
        + (f"　｜　模板：{tpl_name}" if tpl_name else "")
    )
    mr = meta.add_run(meta_text)
    mr.font.size = Pt(10.5)
    mr.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    doc.add_page_break()

    doc.add_heading(title, level=0)

    overview = (state.analysis_overview if state else None) or ""
    analysis = _loads(state.analysis_json) if state else None
    if isinstance(analysis, dict) and analysis.get("overview"):
        overview = analysis["overview"]

    if overview.strip():
        doc.add_heading("一、项目概述 / 招标分析", level=1)
        for para in overview.strip().split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip())

    if isinstance(analysis, dict):
        tr = analysis.get("techRequirements") or []
        if tr:
            doc.add_heading("技术要求", level=2)
            for item in tr:
                doc.add_paragraph(str(item), style="List Bullet")
        sp = analysis.get("scoringPoints") or []
        if sp:
            doc.add_heading("评分点", level=2)
            for p in sp:
                if isinstance(p, dict):
                    doc.add_paragraph(
                        f"{p.get('name', '')}　{p.get('weight', '')}",
                        style="List Bullet",
                    )
        rr = analysis.get("rejectionRisks") or []
        if rr:
            doc.add_heading("废标风险", level=2)
            for item in rr:
                doc.add_paragraph(str(item), style="List Bullet")

    parsed = (state.parsed_markdown if state else None) or ""
    if parsed.strip():
        doc.add_heading("二、招标文件解析摘录", level=1)
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
