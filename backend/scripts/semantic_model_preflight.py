"""
模块：P9C 离线语义模型预检与合成评测
用途：
  - 校验固定模型 BAAI/bge-small-zh-v1.5、512 维与固定缓存目录磁盘空间（固定 5GiB，不可绕过）
  - 基于仓库内合成评测集计算 Recall@5 / NDCG@5；低于阈值非零退出
  - 禁止自动下载；模型/依赖/维度缺失时输出中文说明并失败
对接：
  - app.core.config.resolve_semantic_model_cache_dir / Settings
  - app.services.embedding_service.OfflineBgeEmbedder 制品指纹
  - tests/fixtures/p9c_semantic_eval.json（唯一评测数据源）
二次开发：
  - 禁止写入知识库或数据库；禁止外发正文/查询；禁止触网下载
  - 禁止 CLI 接受外部评测路径、下载开关或跳过磁盘检查
  - 评测与阈值变更须同步计划文档；pytest 应注入假嵌入函数而非加载真实模型
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# 允许从 backend 根目录或仓库根目录启动
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# ---------- 固定契约（与 config / 计划一致，禁止运行时覆盖为在线 API） ----------
FIXED_MODEL_ID = "BAAI/bge-small-zh-v1.5"
FIXED_DIM = 512
DEFAULT_MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_RECALL_AT_5 = 0.80
DEFAULT_NDCG_AT_5 = 0.70
# 相关等级阈值：relevance >= 1 视为“有相关文档”
MIN_RELEVANT_GRADE = 1


class PreflightError(RuntimeError):
    """
    用途：预检/评测受控失败；code 为固定英文短码，message 为中文说明。
    对接：CLI 非零退出与 pytest 断言。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Candidate:
    """用途：单条候选分块（id/text/人工相关度 0~3）。"""

    id: str
    text: str
    relevance: int


@dataclass(frozen=True)
class EvalQuery:
    """用途：单条合成查询及其候选列表。"""

    id: str
    query: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class EvalDataset:
    """用途：加载后的合成评测集只读视图。"""

    fixed_model_id: str
    dimension: int
    recall_threshold: float
    ndcg_threshold: float
    queries: tuple[EvalQuery, ...]


@dataclass(frozen=True)
class MetricResult:
    """用途：聚合指标与逐查询明细（不含正文，仅 id 与分数）。"""

    recall_at_5: float
    ndcg_at_5: float
    query_count: int
    per_query: tuple[dict[str, Any], ...]


EmbedFn = Callable[[Sequence[str]], list[list[float]]]


def default_eval_fixture_path() -> Path:
    """用途：仓库内合成评测 JSON 的固定路径（CLI 唯一数据源）。"""
    return _BACKEND_ROOT / "tests" / "fixtures" / "p9c_semantic_eval.json"


def _require_exact_int(raw: dict[str, Any], key: str, expected: int, code: str) -> int:
    """
    用途：强制字段存在且等于固定整数；禁止默认值掩盖缺字段。
    对接：load_eval_dataset 契约校验。
    """
    if key not in raw or raw[key] is None:
        raise PreflightError(code, f"合成评测集缺少必填字段 {key}")
    try:
        value = int(raw[key])
    except (TypeError, ValueError) as exc:
        raise PreflightError(code, f"合成评测集 {key} 非法") from exc
    if value != expected:
        raise PreflightError(
            code,
            f"合成评测集 {key} 必须为 {expected}，收到 {value}",
        )
    return value


def _require_exact_str(raw: dict[str, Any], key: str, expected: str, code: str) -> str:
    """
    用途：强制字段存在且等于固定字符串；禁止默认值掩盖缺字段。
    对接：load_eval_dataset 契约校验。
    """
    if key not in raw or raw[key] is None:
        raise PreflightError(code, f"合成评测集缺少必填字段 {key}")
    value = str(raw[key]).strip()
    if not value:
        raise PreflightError(code, f"合成评测集 {key} 不能为空")
    if value != expected:
        raise PreflightError(
            code,
            f"合成评测集 {key} 必须为 {expected}，收到 {value}",
        )
    return value


def _require_threshold_floor(
    thresholds: Any,
    *,
    key: str,
    floor: float,
    code: str,
) -> float:
    """
    用途：强制 thresholds 中指标存在且不低于固定下限；禁止降低阈值。
    对接：load_eval_dataset 契约校验。
    """
    if not isinstance(thresholds, dict):
        raise PreflightError(code, "合成评测集缺少 thresholds 对象")
    if key not in thresholds or thresholds[key] is None:
        raise PreflightError(code, f"合成评测集 thresholds 缺少 {key}")
    try:
        value = float(thresholds[key])
    except (TypeError, ValueError) as exc:
        raise PreflightError(code, f"合成评测集 thresholds.{key} 非法") from exc
    if value + 1e-12 < float(floor):
        raise PreflightError(
            code,
            f"合成评测集 thresholds.{key}={value} 低于下限 {floor}",
        )
    return value


def load_eval_dataset(path: Path | str | None = None) -> EvalDataset:
    """
    用途：读取合成评测 JSON 并做结构校验。
    说明：默认指向仓库 fixtures；path 仅供 pytest 注入非法样本，CLI 不得暴露。
    禁止：从知识库或用户上传目录读取；禁止默认值掩盖契约字段；禁止降低阈值。
    """
    p = Path(path) if path else default_eval_fixture_path()
    if not p.is_file():
        # 仅输出文件名，不泄露绝对路径
        raise PreflightError("eval_missing", f"合成评测集不存在：{p.name}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError("eval_invalid", "合成评测集无法解析为 JSON") from exc
    if not isinstance(raw, dict):
        raise PreflightError("eval_invalid", "合成评测集根节点必须为对象")

    # 固定评测契约：缺字段/错值一律拒绝，不得回落默认
    _require_exact_int(raw, "schemaVersion", 1, "eval_schema_invalid")
    model_id = _require_exact_str(
        raw, "fixedModelId", FIXED_MODEL_ID, "model_id_mismatch"
    )
    dimension = _require_exact_int(raw, "dimension", FIXED_DIM, "embed_dim_mismatch")
    th_raw = raw.get("thresholds")
    if "thresholds" not in raw or th_raw is None:
        raise PreflightError("eval_threshold_invalid", "合成评测集缺少 thresholds")
    recall_th = _require_threshold_floor(
        th_raw,
        key="recallAt5",
        floor=DEFAULT_RECALL_AT_5,
        code="eval_threshold_invalid",
    )
    ndcg_th = _require_threshold_floor(
        th_raw,
        key="ndcgAt5",
        floor=DEFAULT_NDCG_AT_5,
        code="eval_threshold_invalid",
    )
    items = raw.get("queries")
    if not isinstance(items, list) or not items:
        raise PreflightError("eval_empty", "合成评测集 queries 为空")

    queries: list[EvalQuery] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise PreflightError("eval_invalid", f"第 {i + 1} 条 query 不是对象")
        qid = str(item.get("id") or "").strip()
        qtext = str(item.get("query") or "").strip()
        cands_raw = item.get("candidates")
        if not qid or not qtext:
            raise PreflightError("eval_invalid", f"第 {i + 1} 条缺少 id 或 query")
        if not isinstance(cands_raw, list) or not cands_raw:
            raise PreflightError(
                "eval_empty_candidates",
                f"查询 {qid} 的 candidates 为空",
            )
        cands: list[Candidate] = []
        seen: set[str] = set()
        for j, c in enumerate(cands_raw):
            if not isinstance(c, dict):
                raise PreflightError(
                    "eval_invalid", f"查询 {qid} 候选 {j + 1} 不是对象"
                )
            cid = str(c.get("id") or "").strip()
            ctext = str(c.get("text") or "").strip()
            if not cid or not ctext:
                raise PreflightError(
                    "eval_invalid", f"查询 {qid} 候选 {j + 1} 缺少 id 或 text"
                )
            if cid in seen:
                raise PreflightError(
                    "eval_duplicate_id",
                    f"查询 {qid} 存在重复候选 id：{cid}",
                )
            seen.add(cid)
            try:
                rel = int(c.get("relevance"))
            except (TypeError, ValueError) as exc:
                raise PreflightError(
                    "eval_invalid",
                    f"查询 {qid} 候选 {cid} 的 relevance 非法",
                ) from exc
            if rel < 0 or rel > 3:
                raise PreflightError(
                    "eval_invalid",
                    f"查询 {qid} 候选 {cid} 的 relevance 须在 0~3",
                )
            cands.append(Candidate(id=cid, text=ctext, relevance=rel))

        # 每条查询必须至少有一条人工相关（relevance>=1），否则评测无意义
        if not any(c.relevance >= MIN_RELEVANT_GRADE for c in cands):
            raise PreflightError(
                "eval_no_relevant",
                f"查询 {qid} 没有 relevance>={MIN_RELEVANT_GRADE} 的候选",
            )

        queries.append(
            EvalQuery(id=qid, query=qtext, candidates=tuple(cands))
        )

    if len(queries) < 20:
        raise PreflightError(
            "eval_too_few",
            f"合成评测集至少 20 条查询，当前 {len(queries)}",
        )

    # 跨查询候选 id 也不得重复，避免评测混入
    all_ids: set[str] = set()
    for q in queries:
        if q.id in all_ids:
            raise PreflightError("eval_duplicate_id", f"重复查询 id：{q.id}")
        all_ids.add(q.id)
        for c in q.candidates:
            if c.id in all_ids:
                raise PreflightError(
                    "eval_duplicate_id", f"重复候选 id：{c.id}"
                )
            all_ids.add(c.id)

    return EvalDataset(
        fixed_model_id=model_id,
        dimension=dimension,
        recall_threshold=recall_th,
        ndcg_threshold=ndcg_th,
        queries=tuple(queries),
    )


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / n for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """用途：余弦相似度，维度不一致返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(0.0, min(1.0, float(sum(x * y for x, y in zip(a, b)))))


def validate_embedding_batch(
    texts: Sequence[str],
    vectors: Sequence[Sequence[float]],
    *,
    expected_dim: int = FIXED_DIM,
) -> list[list[float]]:
    """
    用途：校验嵌入批次数、维度与非空；错维/数量不符抛 PreflightError。
    对接：注入假模型与真实模型 encode 输出。
    """
    if len(vectors) != len(texts):
        raise PreflightError(
            "embed_len_mismatch",
            f"嵌入条数 {len(vectors)} 与文本条数 {len(texts)} 不一致",
        )
    out: list[list[float]] = []
    for i, v in enumerate(vectors):
        if v is None:
            raise PreflightError("embed_empty", f"第 {i + 1} 条嵌入为空")
        if len(v) != expected_dim:
            raise PreflightError(
                "embed_dim_mismatch",
                f"第 {i + 1} 条嵌入维度为 {len(v)}，期望 {expected_dim}",
            )
        out.append(_l2_normalize(v))
    return out


def rank_candidates(
    query: str,
    candidates: Sequence[Candidate],
    embed_fn: EmbedFn,
    *,
    expected_dim: int = FIXED_DIM,
) -> list[Candidate]:
    """
    用途：按查询与候选余弦相似度降序排列候选。
    说明：排序仅依赖嵌入与标注；与 JSON 中候选原始顺序无关。
    """
    if not candidates:
        raise PreflightError("eval_empty_candidates", "候选列表为空，无法排序")
    texts = [query] + [c.text for c in candidates]
    try:
        raw = embed_fn(texts)
    except PreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreflightError(
            "model_unavailable",
            "嵌入函数不可用或执行失败",
        ) from exc
    if raw is None:
        raise PreflightError("model_unavailable", "嵌入函数返回空结果")
    vectors = validate_embedding_batch(
        texts, raw, expected_dim=expected_dim
    )
    qv = vectors[0]
    scored: list[tuple[float, int, Candidate]] = []
    for i, c in enumerate(candidates):
        scored.append((cosine(qv, vectors[i + 1]), i, c))
    # 分数降序；同分保留原相对次序（稳定排序），不按 relevance 重排
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [t[2] for t in scored]


def recall_at_k(
    ranked_relevances: Sequence[int],
    *,
    k: int = 5,
    min_grade: int = MIN_RELEVANT_GRADE,
) -> float:
    """
    用途：单查询 Recall@k = 是否在前 k 命中至少一条相关（grade>=min_grade）。
    返回 0.0 或 1.0。
    """
    if k <= 0:
        return 0.0
    top = list(ranked_relevances)[:k]
    return 1.0 if any(int(r) >= min_grade for r in top) else 0.0


def dcg_at_k(relevances: Sequence[int], *, k: int = 5) -> float:
    """用途：DCG@k（relevance 为增益，位置从 1 起折扣）。"""
    total = 0.0
    for i, rel in enumerate(list(relevances)[:k]):
        # 位置 i+1；log2(i+2)
        total += float(rel) / math.log2(i + 2)
    return total


def ndcg_at_k(ranked_relevances: Sequence[int], *, k: int = 5) -> float:
    """用途：NDCG@k = DCG / IDCG；无相关文档时返回 0。"""
    actual = dcg_at_k(ranked_relevances, k=k)
    ideal = dcg_at_k(sorted(ranked_relevances, reverse=True), k=k)
    if ideal <= 0:
        return 0.0
    return actual / ideal


def evaluate_dataset(
    dataset: EvalDataset,
    embed_fn: EmbedFn,
    *,
    k: int = 5,
) -> MetricResult:
    """
    用途：对全部合成查询跑排序并聚合 Recall@k / NDCG@k。
    对接：pytest 注入假嵌入；预检脚本真实模型路径。
    """
    if not dataset.queries:
        raise PreflightError("eval_empty", "评测查询列表为空")
    if dataset.dimension != FIXED_DIM:
        raise PreflightError(
            "embed_dim_mismatch",
            f"评测集维度 {dataset.dimension} 与固定维度 {FIXED_DIM} 不一致",
        )

    per_query: list[dict[str, Any]] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    for q in dataset.queries:
        ranked = rank_candidates(
            q.query,
            q.candidates,
            embed_fn,
            expected_dim=FIXED_DIM,
        )
        rels = [c.relevance for c in ranked]
        r = recall_at_k(rels, k=k)
        n = ndcg_at_k(rels, k=k)
        recalls.append(r)
        ndcgs.append(n)
        per_query.append(
            {
                "queryId": q.id,
                "recallAt5": r,
                "ndcgAt5": n,
                "topIds": [c.id for c in ranked[:k]],
            }
        )

    nq = len(recalls)
    return MetricResult(
        recall_at_5=sum(recalls) / nq,
        ndcg_at_5=sum(ndcgs) / nq,
        query_count=nq,
        per_query=tuple(per_query),
    )


def assert_metrics_pass(
    metrics: MetricResult,
    *,
    recall_threshold: float = DEFAULT_RECALL_AT_5,
    ndcg_threshold: float = DEFAULT_NDCG_AT_5,
) -> None:
    """用途：指标低于阈值时抛 PreflightError（受控失败，不伪造成功）。"""
    if metrics.recall_at_5 + 1e-12 < recall_threshold:
        raise PreflightError(
            "metric_below_threshold",
            (
                f"Recall@5={metrics.recall_at_5:.4f} "
                f"低于阈值 {recall_threshold:.2f}"
            ),
        )
    if metrics.ndcg_at_5 + 1e-12 < ndcg_threshold:
        raise PreflightError(
            "metric_below_threshold",
            (
                f"NDCG@5={metrics.ndcg_at_5:.4f} "
                f"低于阈值 {ndcg_threshold:.2f}"
            ),
        )


def check_disk_space(
    cache_dir: Path,
    *,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
) -> int:
    """
    用途：检查固定缓存目录所在卷可用空间；不足则 storage 错误。
    返回：可用字节数。CLI 固定使用 5GiB，不可跳过。
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        free = int(shutil.disk_usage(str(cache_dir)).free)
    except OSError as exc:
        raise PreflightError(
            "cache_unusable",
            "无法访问语义模型固定缓存目录",
        ) from exc
    if free < int(min_free_bytes):
        need_gib = int(min_free_bytes) / (1024**3)
        free_gib = free / (1024**3)
        raise PreflightError(
            "model_storage_insufficient",
            (
                f"磁盘可用约 {free_gib:.2f} GiB，"
                f"低于要求 {need_gib:.0f} GiB，请清理空间后重试"
            ),
        )
    return free


def resolve_cache_dir_from_settings() -> Path:
    """用途：从服务端 Settings 推导固定缓存目录（与生产一致）。"""
    from app.core.config import get_settings, resolve_semantic_model_cache_dir

    settings = get_settings()
    return resolve_semantic_model_cache_dir(settings)


def model_cache_seems_present(cache_dir: Path, model_id: str = FIXED_MODEL_ID) -> bool:
    """
    用途：粗检缓存是否已有本地文件（不加载模型、不触网）。
    说明：huggingface 缓存通常含 models--BAAI--bge-small-zh-v1.5。
    """
    _ = model_id
    marker = cache_dir / "models--BAAI--bge-small-zh-v1.5"
    if marker.is_dir():
        try:
            return any(marker.rglob("*"))
        except OSError:
            return False
    # 兼容直接展开权重目录
    for name in ("config.json", "modules.json", "pytorch_model.bin", "model.safetensors"):
        if (cache_dir / name).is_file():
            return True
    return False


def load_local_sentence_transformer(
    cache_dir: Path,
    *,
    model_id: str = FIXED_MODEL_ID,
) -> Any:
    """
    用途：仅在本地缓存已存在时以 local_files_only=True 加载；禁止下载。
    返回：SentenceTransformer 实例；缺失/失败一律 model_unavailable 或 deps_missing。
    """
    if model_id != FIXED_MODEL_ID:
        raise PreflightError(
            "model_id_mismatch",
            f"仅允许固定模型 {FIXED_MODEL_ID}，收到 {model_id}",
        )
    if not model_cache_seems_present(cache_dir, model_id):
        raise PreflightError(
            "model_unavailable",
            (
                "本地未找到离线模型缓存。"
                "请先在受控环境将 BAAI/bge-small-zh-v1.5 放入服务端固定缓存目录。"
                "本脚本禁止自动下载，不会写入知识库或数据库。"
            ),
        )
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise PreflightError(
            "deps_missing",
            "缺少 sentence-transformers 依赖，无法加载离线模型",
        ) from exc

    try:
        # 硬编码 local_files_only=True，禁止任何下载路径
        model = SentenceTransformer(
            model_id,
            cache_folder=str(cache_dir),
            device="cpu",
            local_files_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise PreflightError(
            "model_unavailable",
            (
                "无法从本地缓存加载 BAAI/bge-small-zh-v1.5。"
                "请确认缓存完整且未损坏；本脚本禁止自动下载。"
            ),
        ) from exc
    return model


def make_real_embed_fn(model: Any, *, expected_dim: int = FIXED_DIM) -> EmbedFn:
    """用途：包装真实模型 encode 为可注入的 embed_fn。"""

    def _embed(texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            raw = model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise PreflightError(
                "model_unavailable",
                "模型编码失败",
            ) from exc
        vectors = [[float(x) for x in row] for row in raw]
        return validate_embedding_batch(texts, vectors, expected_dim=expected_dim)

    return _embed


def compute_artifact_fingerprint(cache_dir: Path, model_id: str = FIXED_MODEL_ID) -> str:
    """用途：复用 OfflineBgeEmbedder 的内容指纹算法（不加载权重）。"""
    from app.services.embedding_service import OfflineBgeEmbedder

    return OfflineBgeEmbedder()._compute_artifact_fingerprint(cache_dir, model_id)


def run_preflight(
    *,
    embed_fn: EmbedFn | None = None,
    cache_dir: Path | None = None,
    min_free_bytes: int | None = None,
) -> dict[str, Any]:
    """
    用途：执行完整预检+合成评测；成功返回指标字典。
    参数 embed_fn：pytest 注入假模型时传入，跳过真实加载。
    固定：始终读取仓库 fixtures 评测集；始终执行磁盘检查（默认 5GiB）。
    禁止：下载模型、读写知识库表、调用业务 search API、外部评测路径。
    """
    # CLI 与生产预检仅允许固定 fixtures；path 参数不在此暴露
    dataset = load_eval_dataset(None)
    if dataset.fixed_model_id != FIXED_MODEL_ID:
        raise PreflightError(
            "model_id_mismatch",
            f"评测集 fixedModelId 必须为 {FIXED_MODEL_ID}",
        )
    if dataset.dimension != FIXED_DIM:
        raise PreflightError(
            "embed_dim_mismatch",
            f"评测集 dimension 必须为 {FIXED_DIM}",
        )

    resolved_cache = cache_dir or resolve_cache_dir_from_settings()
    # 磁盘检查不可跳过；pytest 可通过 min_free_bytes 降低阈值，CLI 固定 5GiB
    free_bytes = check_disk_space(
        resolved_cache,
        min_free_bytes=min_free_bytes
        if min_free_bytes is not None
        else DEFAULT_MIN_FREE_BYTES,
    )

    fingerprint = ""
    used_real_model = False
    if embed_fn is None:
        model = load_local_sentence_transformer(
            resolved_cache,
            model_id=FIXED_MODEL_ID,
        )
        embed_fn = make_real_embed_fn(model, expected_dim=FIXED_DIM)
        fingerprint = compute_artifact_fingerprint(resolved_cache, FIXED_MODEL_ID)
        used_real_model = True
    else:
        # 注入路径仍可计算指纹（缓存可空）；失败时用占位，不抛路径细节
        try:
            fingerprint = compute_artifact_fingerprint(
                resolved_cache, FIXED_MODEL_ID
            )
        except Exception:  # noqa: BLE001
            fingerprint = "injected-no-fingerprint"

    metrics = evaluate_dataset(dataset, embed_fn, k=5)
    assert_metrics_pass(
        metrics,
        recall_threshold=dataset.recall_threshold,
        ndcg_threshold=dataset.ndcg_threshold,
    )

    return {
        "ok": True,
        "modelId": FIXED_MODEL_ID,
        "dimension": FIXED_DIM,
        "cacheDirName": resolved_cache.name,
        "modelFingerprint": fingerprint,
        "usedRealModel": used_real_model,
        "queryCount": metrics.query_count,
        "recallAt5": round(metrics.recall_at_5, 6),
        "ndcgAt5": round(metrics.ndcg_at_5, 6),
        "recallThreshold": dataset.recall_threshold,
        "ndcgThreshold": dataset.ndcg_threshold,
        "freeDiskBytes": free_bytes,
        # 不输出查询正文、候选正文、绝对路径
        "perQuery": [
            {
                "queryId": row["queryId"],
                "recallAt5": row["recallAt5"],
                "ndcgAt5": round(float(row["ndcgAt5"]), 6),
                "topIds": row["topIds"],
            }
            for row in metrics.per_query
        ],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    用途：构建 CLI；故意不提供下载、跳过磁盘、外部评测路径等开关。
    """
    p = argparse.ArgumentParser(
        description=(
            "P9C 离线语义模型预检：固定 BAAI/bge-small-zh-v1.5 / 512 维，"
            "禁止下载，固定 5GiB 磁盘检查，仅读取仓库合成评测集，"
            "计算 Recall@5 与 NDCG@5"
        )
    )
    # 无 --allow-download / --skip-disk-check / --eval-json
    return p


def main(argv: Iterable[str] | None = None) -> int:
    """用途：CLI 入口；成功 0，受控失败 2，未捕获异常 1。"""
    _build_arg_parser().parse_args(list(argv) if argv is not None else None)
    try:
        # CLI 固定：仓库 fixtures、5GiB 磁盘、禁止下载
        result = run_preflight()
    except PreflightError as exc:
        # 仅输出固定 code 与中文说明；不打印路径、查询正文、候选正文
        payload = {
            "ok": False,
            "errorCode": exc.code,
            "message": str(exc),
            "modelId": FIXED_MODEL_ID,
            "dimension": FIXED_DIM,
            "hint": (
                "请准备本机离线模型缓存并确保磁盘≥5GiB；"
                "本脚本不访问知识库、不写数据库、禁止下载模型、"
                "仅读取仓库内固定合成评测集。"
            ),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "errorCode": "internal_error",
            "message": "预检发生未预期错误",
            "detailType": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
