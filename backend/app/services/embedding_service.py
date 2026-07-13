"""
模块：文本向量（embedding）与 P9C 离线语义提供者
用途：
  - 保留本地哈希向量工具（历史 embedding_json 兼容，不作语义结果）
  - OfflineBgeEmbedder：固定 BAAI/bge-small-zh-v1.5、512 维，仅受控后台重建加载
  - 测试可注入确定性假模型，禁止下载/触网
对接：knowledge_service 语义索引重建与 hybrid 检索
二次开发：
  - 模型 ID/维度/缓存目录只读 config 常量，禁止从 API 或工作空间设置读取
  - 知识库检索不得再调用 try_api_embed 外发正文/查询
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings, resolve_semantic_model_cache_dir
from app.services import settings_service

# 本地哈希向量维度（历史兼容；非 P9C 语义维）
LOCAL_DIM = 256
# P9C 固定维度（与 config.semantic_embedding_dim 一致，代码侧常量便于测试断言）
OFFLINE_DIM = 512
OFFLINE_PROVIDER = "offline_bge"
_WS = re.compile(r"\s+")

# 固定错误码
ERR_MODEL_UNAVAILABLE = "model_unavailable"
ERR_STORAGE_INSUFFICIENT = "model_storage_insufficient"


class OfflineEmbedderError(RuntimeError):
    """
    用途：离线向量提供者可观察失败；code 仅为服务端固定码。
    对接：knowledge_service 重建失败落库 error_code。
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def local_embed(text: str, *, dim: int = LOCAL_DIM) -> list[float]:
    """
    用途：字符 bigram 哈希到固定维并 L2 归一化。
    说明：非语义大模型；P9C 搜索不得用其结果冒充语义 vectorScore。
    """
    s = _WS.sub("", (text or "").strip().lower())
    if not s:
        return [0.0] * dim
    vec = [0.0] * dim
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
    """用途：余弦相似度 0~1（负值钳 0）；维度不一致返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    for x, y in zip(a, b):
        dot += x * y
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


def deterministic_offline_embed(text: str, *, dim: int = OFFLINE_DIM) -> list[float]:
    """
    用途：跨进程稳定的确定性 512 维假向量（hashlib，非 Python hash）。
    对接：pytest 注入；禁止用于生产语义检索。
    """
    s = (text or "").strip()
    if not s:
        return [0.0] * dim
    vec = [0.0] * dim
    # unigram + bigram 用 blake2b 派生桶，保证 PYTHONHASHSEED 无关
    for i, ch in enumerate(s):
        h1 = int(hashlib.blake2b(ch.encode("utf-8"), digest_size=8).hexdigest(), 16)
        vec[h1 % dim] += 1.0
        if i + 1 < len(s):
            bg = s[i : i + 2].encode("utf-8")
            h2 = int(hashlib.blake2b(bg, digest_size=8).hexdigest(), 16)
            vec[h2 % dim] += 1.5
    return _l2_normalize(vec)


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
    用途：历史 OpenAI 兼容 embeddings 调用（非知识库路径）。
    说明：P9C 起知识库入库/查询禁止调用本函数外发正文或查询。
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
    用途：知识库分块写入用的兼容向量（仅本地哈希，不再走外部 API）。
    说明：语义检索向量改由 OfflineBgeEmbedder 在索引重建时写入独立表。
    """
    if not texts:
        return []
    # 刻意忽略 db/workspace embeddingModel，防止知识正文出域
    _ = (db, workspace_id)
    return [local_embed(t) for t in texts]


def embed_one(
    text: str,
    *,
    db: Session | None = None,
    workspace_id: str | None = None,
) -> list[float]:
    """用途：单条兼容哈希向量（非 P9C 语义）。"""
    return embed_texts(db, workspace_id, [text or ""])[0]


class OfflineBgeEmbedder:
    """
    模块：P9C 离线 BGE 向量提供者
    用途：固定模型 ID 与 512 维；仅重建路径可加载；测试可注入假模型。
    对接：knowledge_service.execute_semantic_index_rebuild / search_chunks。
    二次开发：禁止从请求体读取 URL/Token/路径/模型名；日志不得含正文与绝对用户路径。
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._fingerprint: str = ""
        self._injected: bool = False
        self._inject_fn: Callable[[list[str]], list[list[float]]] | None = None

    def inject_test_model(
        self,
        *,
        embed_fn: Callable[[list[str]], list[list[float]]],
        fingerprint: str = "test-fingerprint",
    ) -> None:
        """用途：pytest 注入确定性假模型，跳过真实依赖与下载。"""
        self._injected = True
        self._inject_fn = embed_fn
        self._fingerprint = fingerprint or "test-fingerprint"
        self._model = object()  # 标记已“加载”

    def clear_injection(self) -> None:
        """用途：清除测试注入。"""
        self._injected = False
        self._inject_fn = None
        self._fingerprint = ""
        self._model = None

    def unload(self) -> None:
        """用途：卸载内存中的模型（测试或主动释放）。"""
        if self._injected:
            self._model = None
            return
        self._model = None
        self._fingerprint = ""

    def is_ready(self) -> bool:
        return self._model is not None and (
            self._injected or bool(self._fingerprint)
        )

    def fingerprint(self) -> str:
        return self._fingerprint

    def _check_disk(self, settings: Settings) -> None:
        cache = resolve_semantic_model_cache_dir(settings)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(str(cache)).free
        except OSError as exc:
            raise OfflineEmbedderError(
                ERR_MODEL_UNAVAILABLE, "cache_unusable"
            ) from exc
        if free < int(settings.semantic_min_free_disk_bytes):
            raise OfflineEmbedderError(ERR_STORAGE_INSUFFICIENT)

    def _compute_artifact_fingerprint(self, cache_dir: Path, model_id: str) -> str:
        """
        用途：对模型缓存内文件做确定性内容 SHA-256 指纹（相对路径名 + 分块读内容）。
        说明：不含绝对路径；同名同尺寸内容不同必须得到不同指纹。
        """
        h = hashlib.sha256()
        h.update(model_id.encode("utf-8"))
        root = cache_dir / "models--BAAI--bge-small-zh-v1.5"
        scan_root = root if root.is_dir() else cache_dir
        try:
            files = sorted(p for p in scan_root.rglob("*") if p.is_file())
            for p in files:
                # 仅相对路径名进入哈希，绝不写入绝对路径
                rel = p.relative_to(scan_root).as_posix()
                h.update(rel.encode("utf-8"))
                try:
                    with p.open("rb") as fh:
                        while True:
                            block = fh.read(1024 * 1024)
                            if not block:
                                break
                            h.update(block)
                except OSError:
                    continue
        except OSError:
            h.update(b"empty")
        return h.hexdigest()[:32]

    def ensure_loaded_for_rebuild(self, settings: Settings | None = None) -> str:
        """
        用途：仅后台重建调用；检查磁盘后按固定模型 ID 懒加载。
        返回：制品指纹。失败抛 OfflineEmbedderError（固定 code）。
        """
        settings = settings or get_settings()
        if self._injected and self._inject_fn is not None:
            if not self._fingerprint:
                self._fingerprint = "test-fingerprint"
            self._model = self._model or object()
            return self._fingerprint

        self._check_disk(settings)
        cache = resolve_semantic_model_cache_dir(settings)
        cache.mkdir(parents=True, exist_ok=True)
        model_id = settings.semantic_model_id
        dim = int(settings.semantic_embedding_dim)
        if dim != OFFLINE_DIM:
            # 服务端常量被错误覆盖时拒绝，避免错维激活
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "dim_mismatch")

        try:
            # 懒导入：未安装依赖时给出 model_unavailable，不在 import 期崩
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "deps_missing") from exc

        try:
            # local_files_only=False 仅重建路径；测试不得走到此处
            self._model = SentenceTransformer(
                model_id,
                cache_folder=str(cache),
                device="cpu",
            )
        except Exception as exc:  # noqa: BLE001
            self._model = None
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "load_failed") from exc

        self._fingerprint = self._compute_artifact_fingerprint(cache, model_id)
        return self._fingerprint

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        用途：批量生成归一化 512 维向量。
        未加载时 model_unavailable；不得在此触发下载。
        """
        if not texts:
            return []
        if self._injected and self._inject_fn is not None:
            if self._model is None:
                raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE)
            out = self._inject_fn(list(texts))
            if len(out) != len(texts):
                raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "len_mismatch")
            fixed: list[list[float]] = []
            for v in out:
                if len(v) != OFFLINE_DIM:
                    raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "dim_mismatch")
                fixed.append(_l2_normalize([float(x) for x in v]))
            return fixed

        if self._model is None:
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE)

        try:
            raw = self._model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "encode_failed") from exc

        out = []
        for row in raw:
            vec = [float(x) for x in row]
            if len(vec) != OFFLINE_DIM:
                raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "dim_mismatch")
            out.append(_l2_normalize(vec))
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed_texts([text or ""])[0]


_OFFLINE_EMBEDDER = OfflineBgeEmbedder()


def get_offline_embedder() -> OfflineBgeEmbedder:
    """用途：进程内单例离线提供者（测试可 inject/clear）。"""
    return _OFFLINE_EMBEDDER
