"""
模块：文本相似度工具
用途：字符 n-gram Jaccard、段落切分；供查重与后续向量检索预筛。
对接：duplicate_service、（后续）knowledge 混合检索
二次开发：可换成 embedding cosine，保持 similarity(a,b)->float 接口。
"""

from __future__ import annotations

import re
from typing import Iterable

_WS = re.compile(r"\s+")
_HEADING = re.compile(r"^#{1,6}\s+")


def normalize_text(text: str) -> str:
    """用途：去空白压缩，便于 n-gram。"""
    return _WS.sub("", (text or "").strip().lower())


def char_ngrams(text: str, n: int = 2) -> set[str]:
    """用途：字符 n-gram 集合。"""
    s = normalize_text(text)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """用途：Jaccard 系数 0~1。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


def similarity(a: str, b: str, *, n: int = 2) -> float:
    """
    用途：两段文本相似度 0~1（字符 n-gram Jaccard）。
    过短文本降权，减少偶然命中。
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    score = jaccard(char_ngrams(na, n), char_ngrams(nb, n))
    # 极短段降权
    min_len = min(len(na), len(nb))
    if min_len < 20:
        score *= 0.5
    elif min_len < 40:
        score *= 0.85
    return round(min(1.0, max(0.0, score)), 4)


def split_paragraphs(text: str, *, min_len: int = 40) -> list[str]:
    """
    用途：按空行/标题切段，过滤过短段落。
    对接：查重本文段抽取
    """
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    # 标题单独成段边界
    lines = raw.split("\n")
    blocks: list[str] = []
    buf: list[str] = []
    for line in lines:
        if _HEADING.match(line.strip()) and buf:
            blocks.append("\n".join(buf).strip())
            buf = [line]
        elif not line.strip():
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf).strip())

    out: list[str] = []
    for b in blocks:
        t = b.strip()
        # 去掉纯标题行过短
        plain = _HEADING.sub("", t).strip()
        if len(normalize_text(plain or t)) >= min_len:
            out.append(t)
    return out


def top_tokens(text: str, *, limit: int = 8) -> list[str]:
    """用途：抽若干汉字/词片段作检索预筛 query。"""
    s = (text or "").strip()
    # 连续 2+ 汉字
    parts = re.findall(r"[\u4e00-\u9fff]{2,8}", s)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= limit:
            break
    return out


def keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    """用途：返回在 text 中出现的关键词列表。"""
    body = text or ""
    found: list[str] = []
    for kw in keywords:
        if kw and kw in body:
            found.append(kw)
    return found
