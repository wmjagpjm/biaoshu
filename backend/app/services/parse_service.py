"""
模块：轻量文档解析
用途：将上传的 txt/md/docx/pdf 转为 Markdown 文本（本机日用，不依赖 MinerU）。
对接：files 上传后的 parse 任务；失败时返回可读错误说明。
二次开发：扫描件/复杂版式走本地 MinerU 回传通道。

DOCX 结构边界（V1-D）：
- 严格按 document.element.body 子节点顺序输出 w:p / w:tbl，禁止 paragraphs 后再追加 tables。
- Heading 1–6 样式名精确映射为 # 至 ######；其它非空段落为普通块；空段落忽略。
- 普通表格输出 GFM pipe table（首行表头、--- 分隔、最大列数右侧补空）；空表固定「（空表）」。
- 复杂合并单元格/嵌套表仅接受 python-docx 展平视图，不声明 rowspan/colspan 支持；
  图片、图表、SmartArt、文本框、页眉页脚、脚注等均不处理。
"""

from __future__ import annotations

import re
from pathlib import Path

# Heading 样式名 → Markdown 标题前缀（精确匹配，含末尾空格占位由调用方拼）
_HEADING_STYLE_PREFIX: dict[str, str] = {
    "Heading 1": "#",
    "Heading 2": "##",
    "Heading 3": "###",
    "Heading 4": "####",
    "Heading 5": "#####",
    "Heading 6": "######",
}

# 单元格内空白：CR/LF/制表/连续空白统一折叠为单个 ASCII 空格
_CELL_WS_RE = re.compile(r"[ \t\r\n]+")


def _normalize_cell_text(raw: str | None) -> str:
    """
    用途：规范化表格单元格文本。
    规则：strip → 空白折叠为单空格 → 精确将 | 转义为 \\|。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = _CELL_WS_RE.sub(" ", text)
    return text.replace("|", r"\|")


def _render_paragraph_block(paragraph) -> str | None:
    """
    用途：将单个段落渲染为 Markdown 块；空段落返回 None。
    Heading 1–6 → # 至 ###### + 空格 + strip 文本；其它样式保持普通文本块。
    """
    text = (paragraph.text or "").strip()
    if not text:
        return None
    style_name = ""
    try:
        style = paragraph.style
        if style is not None and style.name:
            style_name = style.name
    except Exception:
        style_name = ""
    prefix = _HEADING_STYLE_PREFIX.get(style_name)
    if prefix is not None:
        return f"{prefix} {text}"
    return text


def _render_table_block(table) -> str:
    """
    用途：将普通表格渲染为 GFM pipe table；零行或零逻辑列输出「（空表）」。
    首行作表头，等列数 --- 分隔，后续为数据行；按最大列数右侧补空，保留空单元格。
    复杂合并/嵌套仅使用 python-docx 展平单元格视图，不声明 rowspan/colspan。
    """
    rows_data: list[list[str]] = []
    for row in table.rows:
        cells = [_normalize_cell_text(cell.text) for cell in row.cells]
        rows_data.append(cells)

    if not rows_data:
        return "（空表）"

    max_cols = max((len(r) for r in rows_data), default=0)
    if max_cols == 0:
        return "（空表）"

    padded = [r + [""] * (max_cols - len(r)) for r in rows_data]

    def _format_row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    lines = [
        _format_row(padded[0]),
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    for data_row in padded[1:]:
        lines.append(_format_row(data_row))
    return "\n".join(lines)


def _docx_body_to_markdown_blocks(doc) -> list[str]:
    """
    用途：按 body 真实子节点顺序收集非空 Markdown 块。
    仅处理 w:p / w:tbl；其它节点（如 sectPr）忽略。
    """
    from docx.oxml.ns import qn  # type: ignore
    from docx.table import Table  # type: ignore
    from docx.text.paragraph import Paragraph  # type: ignore

    parts: list[str] = []
    body = doc.element.body
    tag_p = qn("w:p")
    tag_tbl = qn("w:tbl")
    for child in body.iterchildren():
        if child.tag == tag_p:
            block = _render_paragraph_block(Paragraph(child, doc))
            if block:
                parts.append(block)
        elif child.tag == tag_tbl:
            parts.append(_render_table_block(Table(child, doc)))
    return parts


def parse_file_to_markdown(path: Path, original_name: str) -> str:
    """
    用途：按扩展名选择解析器，输出 Markdown 字符串。

    DOCX：按 document.element.body 的 w:p / w:tbl 原始顺序构造块；
    Heading 1–6 映射标题，普通表格转 GFM，空文档保持「（DOCX 无段落文本）」。
    复杂版式（合并单元格语义、嵌套表、图片等）不在本函数还原范围内。
    """
    suffix = path.suffix.lower()
    header = f"# 解析结果：{original_name}\n\n"

    if suffix in {".txt", ".md", ".markdown"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        return header + text.strip() + "\n"

    if suffix == ".docx":
        try:
            from docx import Document  # type: ignore
        except ImportError as exc:
            raise RuntimeError("未安装 python-docx，无法解析 DOCX") from exc
        doc = Document(str(path))
        parts = _docx_body_to_markdown_blocks(doc)
        body = "\n\n".join(parts) if parts else "（DOCX 无段落文本）"
        return header + body + "\n"

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise RuntimeError("未安装 pypdf，无法解析 PDF") from exc
        reader = PdfReader(str(path))
        pages: list[str] = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                t = ""
            if t:
                pages.append(f"## 第 {i} 页\n\n{t}")
            else:
                pages.append(f"## 第 {i} 页\n\n（本页未提取到文本，可能是扫描件，请用本地 MinerU）")
        body = "\n\n".join(pages) if pages else "（PDF 无页面）"
        return header + body + "\n"

    # 未知类型：尝试按文本读
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return header + f"> 未识别扩展名 `{suffix}`，按文本读取\n\n" + text[:50000]
    except Exception as exc:
        raise RuntimeError(f"不支持的文件类型：{suffix}（{exc}）") from exc
