"""
模块：文本向量（embedding）
用途：
  - 默认本地哈希向量（无需外网/额外模型，本机日用可用）
  - 可选 OpenAI 兼容 /embeddings（settings.embedding_model 非空时尝试）
对接：knowledge_service 入库与 hybrid 检索
二次开发：可换 sentence-transformers 本地模型；保持 list[float] 契约。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlalchemy.orm import Session

from app.services import settings_service
from app.services.llm_service import LlmCallError, LlmConfigError

# 本地哈希向量维度（固定，便于存 JSON）
LOCAL_DIM = 256
_WS = re.compile(r"\s+")


def local_embed(text: str, *, dim: int = LOCAL_DIM) -> list[float]:
    """
    用途：字符 bigram 哈希到固定维并 L2 归一化。
    说明：非语义大模型，但对近义改写/重叠文本仍优于纯精确关键词。
    """
    s = _WS.sub("", (text or "").strip().lower())
    if not s:
        return [0.0] * dim
    vec = [0.0] * dim
    # unigram + bigram
    for i, ch in enumerate(s):
        vec[hash(ch) % dim] += 1.0
        if i + 1 < len(s):
            bg = s[i : i + 2]
            vec[hash(bg) % dim] += 1.5
    return _l2_normalize(vec)


def _l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """用途：余弦相似度 0~1（负值钳 0）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    for x, y in zip(a, b):
        dot += x * y
    # 已归一化时 dot 即 cosine
    return max(0.0, min(1.0, float(dot)))


def dumps_embedding(vec: list[float] | None) -> str | None:
    if not vec:
        return None
    return json.dumps(vec, ensure_ascii=False)


def loads_embedding(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data and all(isinstance(x, (int, float)) for x in data):
            return [float(x) for x in data]
    except json.JSONDecodeError:
        return None
    return None


def _embeddings_url(api_base_url: str) -> str:
    base = api_base_url.rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


def try_api_embed(
    db: Session,
    workspace_id: str,
    texts: list[str],
    *,
    timeout_sec: float = 60.0,
) -> list[list[float]] | None:
    """
    用途：若配置了 embedding_model，调用 OpenAI 兼容 embeddings。
    失败返回 None（调用方回退 local_embed）。
    """
    if not texts:
        return []
    cfg = settings_service.get_or_create_settings(db, workspace_id)
    model = (getattr(cfg, "embedding_model", None) or "").strip()
    if not model:
        return None
    if not (cfg.api_base_url or "").strip():
        return None

    import httpx

    url = _embeddings_url(cfg.api_base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key or ''}",
    }
    payload = {"model": model, "input": texts}
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            res = client.post(url, headers=headers, json=payload)
        if res.status_code >= 400:
            return None
        data = res.json()
        items = data.get("data") or []
        # 按 index 排序
        items = sorted(items, key=lambda x: int(x.get("index") or 0))
        out: list[list[float]] = []
        for it in items:
            emb = it.get("embedding")
            if not isinstance(emb, list):
                return None
            out.append(_l2_normalize([float(x) for x in emb]))
        if len(out) != len(texts):
            return None
        return out
    except Exception:
        return None


def embed_texts(
    db: Session | None,
    workspace_id: str | None,
    texts: list[str],
) -> list[list[float]]:
    """
    用途：批量向量化；优先 API，否则本地哈希。
    """
    if not texts:
        return []
    if db is not None and workspace_id:
        api_vecs = try_api_embed(db, workspace_id, texts)
        if api_vecs is not None:
            return api_vecs
    return [local_embed(t) for t in texts]


def embed_one(
    text: str,
    *,
    db: Session | None = None,
    workspace_id: str | None = None,
) -> list[float]:
    """用途：单条文本向量。"""
    return embed_texts(db, workspace_id, [text or ""])[0]
