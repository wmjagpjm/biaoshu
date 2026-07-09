"""
模块：轻量文档解析
用途：将上传的 txt/md/docx/pdf 转为 Markdown 文本（本机日用，不依赖 MinerU）。
对接：files 上传后的 parse 任务；失败时返回可读错误说明。
二次开发：扫描件/复杂版式走本地 MinerU 回传通道。
"""

from __future__ import annotations

from pathlib import Path


def parse_file_to_markdown(path: Path, original_name: str) -> str:
    """
    用途：按扩展名选择解析器，输出 Markdown 字符串。
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
        parts: list[str] = []
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if t:
                parts.append(t)
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
