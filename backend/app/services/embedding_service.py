"""
模块：文本向量（embedding）与 P9C 离线语义提供者
用途：
  - 保留本地哈希向量工具（历史 embedding_json 兼容，不作语义结果）
  - OfflineBgeEmbedder：固定 BAAI/bge-small-zh-v1.5、revision、512 维，严格离线加载
  - 共享制品清单校验（精确 10 文件/大小/权重 SHA-256）
  - 测试可注入确定性假模型，禁止下载/触网
对接：knowledge_service 语义索引重建与 hybrid 检索；prepare/preflight 只读校验接口
二次开发：
  - 模型 ID/revision/维度/缓存目录只读 config 常量，禁止从 API 或工作空间设置读取
  - 知识库检索不得再调用 try_api_embed 外发正文/查询
  - 生产服务不得反向导入 CLI 脚本
  - 纯制品接口导入不得加载 sqlalchemy/app.models/settings_service
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.core.config import (
    FIXED_HF_ENDPOINT,
    SEMANTIC_MIN_FREE_DISK_BYTES,
    SEMANTIC_MODEL_ID,
    SEMANTIC_MODEL_REVISION,
    Settings,
    get_settings,
    resolve_semantic_model_cache_dir,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# 本地哈希向量维度（历史兼容；非 P9C 语义维）
LOCAL_DIM = 256
# P9C 固定维度（与 config.semantic_embedding_dim 一致，代码侧常量便于测试断言）
OFFLINE_DIM = 512
OFFLINE_PROVIDER = "offline_bge"
FIXED_MODEL_ID = SEMANTIC_MODEL_ID
FIXED_MODEL_REVISION = SEMANTIC_MODEL_REVISION
# FIXED_HF_ENDPOINT 已从 config 导入并再导出，供 prepare 与测试断言
_WS = re.compile(r"\s+")

# 固定错误码
ERR_MODEL_UNAVAILABLE = "model_unavailable"
ERR_STORAGE_INSUFFICIENT = "model_storage_insufficient"
ERR_ARTIFACT_MISMATCH = "model_artifact_mismatch"
ERR_DOWNLOAD_FAILED = "model_download_failed"
ERR_DEPS_MISSING = "deps_missing"

# 固定制品：10 必需文件与精确大小；总量 96,378,176 字节
FIXED_SAFETENSORS_NAME = "model.safetensors"
FIXED_SAFETENSORS_SHA256 = (
    "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
)
FIXED_ARTIFACT_FILES: dict[str, int] = {
    "1_Pooling/config.json": 190,
    "config.json": 776,
    "config_sentence_transformers.json": 124,
    "model.safetensors": 95_827_648,
    "modules.json": 229,
    "sentence_bert_config.json": 52,
    "special_tokens_map.json": 125,
    "tokenizer.json": 439_125,
    "tokenizer_config.json": 367,
    "vocab.txt": 109_540,
}
FIXED_ARTIFACT_TOTAL_BYTES = sum(FIXED_ARTIFACT_FILES.values())  # 96378176
FIXED_ARTIFACT_FILE_COUNT = len(FIXED_ARTIFACT_FILES)
# 下载白名单：仅 10 文件，顺序与 FIXED_ARTIFACT_FILES 键序精确一致
FIXED_DOWNLOAD_ALLOW_PATTERNS: tuple[str, ...] = tuple(FIXED_ARTIFACT_FILES.keys())


class OfflineEmbedderError(RuntimeError):
    """
    用途：离线向量提供者可观察失败；code 仅为服务端固定码。
    对接：knowledge_service 重建失败落库 error_code；prepare/preflight JSON errorCode。
    """

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def resolve_fixed_snapshot_dir(cache_dir: Path) -> Path:
    """
    用途：解析固定 revision 的 Hugging Face 快照目录（相对 cache 根，不含绝对路径输出）。
    布局：models--BAAI--bge-small-zh-v1.5/snapshots/<revision>/
    """
    repo_dir = "models--" + FIXED_MODEL_ID.replace("/", "--")
    return Path(cache_dir) / repo_dir / "snapshots" / FIXED_MODEL_REVISION


def sha256_file(path: Path) -> str:
    """用途：对单文件做分块 SHA-256；供制品校验与测试 monkeypatch。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def compute_fixed_artifact_fingerprint(cache_dir: Path) -> str:
    """
    用途：对固定 revision 快照内 10 个必需文件做确定性内容指纹。
    说明：仅相对路径名 + 内容；不含绝对路径。
    失败：任一必需文件缺失/不可读 → model_artifact_mismatch（不得写 missing 后成功）。
    """
    snap = resolve_fixed_snapshot_dir(cache_dir)
    h = hashlib.sha256()
    h.update(FIXED_MODEL_ID.encode("utf-8"))
    h.update(FIXED_MODEL_REVISION.encode("utf-8"))
    for rel in sorted(FIXED_ARTIFACT_FILES.keys()):
        h.update(rel.encode("utf-8"))
        path = snap / rel
        try:
            with path.open("rb") as fh:
                while True:
                    block = fh.read(1024 * 1024)
                    if not block:
                        break
                    h.update(block)
        except OSError as exc:
            raise OfflineEmbedderError(
                ERR_ARTIFACT_MISMATCH, "fingerprint_unreadable"
            ) from exc
    return h.hexdigest()[:32]


def validate_semantic_model_artifacts(cache_dir: Path) -> dict[str, Any]:
    """
    用途：校验固定 revision 快照精确为 10 必需文件（无额外）、精确大小与 safetensors SHA-256。
    返回：ok/fileCount/totalBytes/artifactFingerprint/revision/modelId（无绝对路径）。
    失败：
      - 快照目录不存在 → model_unavailable
      - 文件缺失/额外/大小不符/哈希不符 → model_artifact_mismatch
    错误消息禁止包含绝对路径、第三方异常原文。
    """
    root = Path(cache_dir)
    snap = resolve_fixed_snapshot_dir(root)
    if not snap.is_dir():
        raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "snapshot_missing")

    # 收集快照内全部相对文件路径（精确集合，禁止额外文件）
    found: set[str] = set()
    try:
        for p in snap.rglob("*"):
            if p.is_file():
                found.add(p.relative_to(snap).as_posix())
    except OSError as exc:
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "scan_failed") from exc

    expected = set(FIXED_ARTIFACT_FILES.keys())
    if found != expected:
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "file_set_mismatch")

    total = 0
    for rel, expected_size in FIXED_ARTIFACT_FILES.items():
        path = snap / rel
        if not path.is_file():
            raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "file_missing")
        try:
            size = int(path.stat().st_size)
        except OSError as exc:
            raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "file_unreadable") from exc
        if size != int(expected_size):
            raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "size_mismatch")
        total += size
        if rel == FIXED_SAFETENSORS_NAME:
            try:
                digest = sha256_file(path)
            except OSError as exc:
                raise OfflineEmbedderError(
                    ERR_ARTIFACT_MISMATCH, "hash_unreadable"
                ) from exc
            if digest.lower() != FIXED_SAFETENSORS_SHA256:
                raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "hash_mismatch")

    if total != FIXED_ARTIFACT_TOTAL_BYTES:
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "total_mismatch")

    fingerprint = compute_fixed_artifact_fingerprint(root)
    return {
        "ok": True,
        "modelId": FIXED_MODEL_ID,
        "revision": FIXED_MODEL_REVISION,
        "fileCount": FIXED_ARTIFACT_FILE_COUNT,
        "totalBytes": total,
        "artifactFingerprint": fingerprint,
    }


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
    导入：settings_service 仅在本函数内延迟导入，避免纯制品导入链拉起数据库模型。
    """
    if not texts:
        return []
    # 延迟导入：prepare/preflight 不触碰本路径
    from app.services import settings_service

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
        # 使用不可漂移的固定 5 GiB，不读取可被错误覆盖的运行时字段
        min_free = SEMANTIC_MIN_FREE_DISK_BYTES
        try:
            cache.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(str(cache)).free
        except OSError as exc:
            raise OfflineEmbedderError(
                ERR_MODEL_UNAVAILABLE, "cache_unusable"
            ) from exc
        if free < int(min_free):
            raise OfflineEmbedderError(ERR_STORAGE_INSUFFICIENT)

    def _compute_artifact_fingerprint(self, cache_dir: Path, model_id: str) -> str:
        """
        用途：兼容旧扫描指纹（仅注入/回退路径）；生产固定指纹见 compute_fixed_artifact_fingerprint。
        说明：preflight 不得回退到本方法；prepare 使用固定指纹。
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
        用途：仅后台重建调用；检查磁盘与制品后按固定 revision 严格离线加载。
        返回：制品指纹。失败抛 OfflineEmbedderError（固定 code，无路径/第三方原文）。
        """
        settings = settings or get_settings()
        if self._injected and self._inject_fn is not None:
            if not self._fingerprint:
                self._fingerprint = "test-fingerprint"
            self._model = self._model or object()
            return self._fingerprint

        self._check_disk(settings)
        cache = resolve_semantic_model_cache_dir(settings)
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "cache_unusable") from exc

        model_id = settings.semantic_model_id
        revision = getattr(settings, "semantic_model_revision", FIXED_MODEL_REVISION)
        dim = int(settings.semantic_embedding_dim)
        if model_id != FIXED_MODEL_ID or revision != FIXED_MODEL_REVISION:
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "model_contract_mismatch")
        if dim != OFFLINE_DIM:
            # 服务端常量被错误覆盖时拒绝，避免错维激活
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "dim_mismatch")

        # 加载前共享制品校验：缺失 → unavailable；损坏 → mismatch
        try:
            meta = validate_semantic_model_artifacts(cache)
        except OfflineEmbedderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "artifact_check_failed") from exc

        try:
            # 懒导入：未安装依赖时固定 deps_missing，不在 import 期崩
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise OfflineEmbedderError(ERR_DEPS_MISSING, "deps_missing") from exc

        try:
            # 生产加载必须固定 revision + 严格离线 + 禁止远程代码
            self._model = SentenceTransformer(
                model_id,
                cache_folder=str(cache),
                device="cpu",
                revision=FIXED_MODEL_REVISION,
                local_files_only=True,
                trust_remote_code=False,
            )
        except OfflineEmbedderError:
            self._model = None
            raise
        except Exception as exc:  # noqa: BLE001
            self._model = None
            # 不泄露第三方异常原文与绝对路径
            raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "load_failed") from exc

        self._fingerprint = str(meta.get("artifactFingerprint") or "") or (
            compute_fixed_artifact_fingerprint(cache)
        )
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
