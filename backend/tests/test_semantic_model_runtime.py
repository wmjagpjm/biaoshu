"""
模块：P9C-R1 固定离线模型运行时门专项测试
用途：验证固定 revision、严格离线加载、显式准备 CLI、制品清单与缓存路径确定性；反假绿。
对接：config / embedding_service / prepare_semantic_model / semantic_model_preflight。
二次开发：禁止触网、禁止真实下载、禁止写应用数据库或真实模型缓存；行为夹具总磁盘 <1MiB。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

# 生产固定契约常量（只断言，不用于写大夹具）
FIXED_MODEL_ID = "BAAI/bge-small-zh-v1.5"
FIXED_REVISION = "26478543676740eb665f803ca07f3f7f478857c8"
FIXED_DIM = 512
FIXED_HF_ENDPOINT = "https://huggingface.co"
FIXED_MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024
FIXED_SAFETENSORS_SHA256 = (
    "354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026"
)
FIXED_TOTAL_BYTES = 96_378_176
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
FIXED_ALLOW_PATTERNS: tuple[str, ...] = tuple(FIXED_ARTIFACT_FILES.keys())

# 行为测试用独立小型清单（总字节远小于 1MiB）
TINY_WEIGHT = b"P9C-TINY-WEIGHT!"
TINY_ARTIFACT_FILES: dict[str, int] = {
    "1_Pooling/config.json": 3,
    "config.json": 4,
    "config_sentence_transformers.json": 5,
    "model.safetensors": len(TINY_WEIGHT),
    "modules.json": 3,
    "sentence_bert_config.json": 3,
    "special_tokens_map.json": 3,
    "tokenizer.json": 8,
    "tokenizer_config.json": 4,
    "vocab.txt": 6,
}
TINY_TOTAL_BYTES = sum(TINY_ARTIFACT_FILES.values())
TINY_SAFETENSORS_SHA256 = hashlib.sha256(TINY_WEIGHT).hexdigest()
TINY_ALLOW_PATTERNS: tuple[str, ...] = tuple(TINY_ARTIFACT_FILES.keys())
assert TINY_TOTAL_BYTES < 1024
assert TINY_TOTAL_BYTES == 55

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _load_script_module(filename: str, mod_name: str):
    """
    用途：以文件路径加载 backend/scripts 下脚本（scripts 非包，不新增 __init__.py）。
    """
    import importlib.util

    script = BACKEND_ROOT / "scripts" / filename
    if not script.is_file():
        raise ModuleNotFoundError(f"No module named scripts.{filename[:-3]}")
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    spec = importlib.util.spec_from_file_location(mod_name, script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _patch_tiny_artifacts(monkeypatch, embedding_service) -> None:
    """用途：将生产清单/哈希替换为独立小型夹具，避免写 ~91MiB 权重。"""
    monkeypatch.setattr(embedding_service, "FIXED_ARTIFACT_FILES", dict(TINY_ARTIFACT_FILES))
    monkeypatch.setattr(embedding_service, "FIXED_ARTIFACT_TOTAL_BYTES", TINY_TOTAL_BYTES)
    monkeypatch.setattr(embedding_service, "FIXED_ARTIFACT_FILE_COUNT", len(TINY_ARTIFACT_FILES))
    monkeypatch.setattr(
        embedding_service, "FIXED_SAFETENSORS_SHA256", TINY_SAFETENSORS_SHA256
    )
    monkeypatch.setattr(
        embedding_service, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS
    )
    # 准备脚本可能已绑定旧元组引用
    if "prepare_semantic_model" in str(sys.modules):
        for name, mod in list(sys.modules.items()):
            if name.startswith("prepare_semantic_model") and hasattr(
                mod, "FIXED_DOWNLOAD_ALLOW_PATTERNS"
            ):
                monkeypatch.setattr(mod, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS)


def _write_tiny_snapshot(
    cache_dir: Path,
    *,
    corrupt_hash: bool = False,
    extra_file: str | None = None,
    drop_file: str | None = None,
    resize_file: tuple[str, int] | None = None,
) -> Path:
    """
    用途：写入小型固定 revision 快照（总字节 <<1MiB）。
    """
    snap = (
        cache_dir
        / "models--BAAI--bge-small-zh-v1.5"
        / "snapshots"
        / FIXED_REVISION
    )
    snap.mkdir(parents=True, exist_ok=True)
    for rel, size in TINY_ARTIFACT_FILES.items():
        if drop_file and rel == drop_file:
            continue
        path = snap / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "model.safetensors":
            payload = bytearray(TINY_WEIGHT)
            if corrupt_hash:
                payload[-1] = (payload[-1] + 1) % 256
            path.write_bytes(bytes(payload))
        else:
            path.write_bytes(b"x" * size)
    if resize_file is not None:
        rel, new_size = resize_file
        path = snap / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"y" * new_size)
    if extra_file:
        ep = snap / extra_file
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_bytes(b"extra")
    return snap


def _tmp_bytes_written(root: Path) -> int:
    """用途：统计测试临时目录真实写入字节。"""
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


# ---------------------------------------------------------------------------
# 生产常量精确断言（不写大文件）
# ---------------------------------------------------------------------------


def test_production_fixed_artifact_constants_exact():
    """生产固定清单、总量与权重哈希常量必须与契约逐字相等。"""
    from app.services import embedding_service

    assert embedding_service.FIXED_MODEL_ID == FIXED_MODEL_ID
    assert embedding_service.FIXED_MODEL_REVISION == FIXED_REVISION
    assert embedding_service.FIXED_ARTIFACT_FILES == FIXED_ARTIFACT_FILES
    assert embedding_service.FIXED_ARTIFACT_TOTAL_BYTES == FIXED_TOTAL_BYTES
    assert sum(embedding_service.FIXED_ARTIFACT_FILES.values()) == FIXED_TOTAL_BYTES
    assert embedding_service.FIXED_SAFETENSORS_SHA256 == FIXED_SAFETENSORS_SHA256
    assert embedding_service.FIXED_DOWNLOAD_ALLOW_PATTERNS == FIXED_ALLOW_PATTERNS
    assert len(embedding_service.FIXED_DOWNLOAD_ALLOW_PATTERNS) == 10
    assert len(set(embedding_service.FIXED_DOWNLOAD_ALLOW_PATTERNS)) == 10
    assert "pytorch_model.bin" not in embedding_service.FIXED_DOWNLOAD_ALLOW_PATTERNS
    assert getattr(embedding_service, "FIXED_HF_ENDPOINT", None) == FIXED_HF_ENDPOINT

    from app.core import config as cfg

    assert cfg.SEMANTIC_MODEL_ID == FIXED_MODEL_ID
    assert cfg.SEMANTIC_MODEL_REVISION == FIXED_REVISION
    assert cfg.SEMANTIC_EMBEDDING_DIM == FIXED_DIM
    assert cfg.SEMANTIC_MODEL_CACHE_DIR_NAME == "semantic-models"
    assert getattr(cfg, "SEMANTIC_MIN_FREE_DISK_BYTES", None) == FIXED_MIN_FREE_DISK_BYTES
    assert getattr(cfg, "FIXED_HF_ENDPOINT", None) == FIXED_HF_ENDPOINT or getattr(
        embedding_service, "FIXED_HF_ENDPOINT", None
    ) == FIXED_HF_ENDPOINT


def test_relative_cache_dir_is_backend_anchored_across_cwd(tmp_path, monkeypatch):
    """相对 upload_dir 跨 cwd 稳定锚定 backend/data/semantic-models；绝对路径保持父目录语义。"""
    from app.core.config import Settings, resolve_semantic_model_cache_dir

    expected = (BACKEND_ROOT / "data" / "semantic-models").resolve()

    for cwd in (REPO_ROOT, BACKEND_ROOT, tmp_path):
        monkeypatch.chdir(cwd)
        settings = Settings(upload_dir="./uploads")
        cache = resolve_semantic_model_cache_dir(settings)
        assert cache == expected
        assert cache.is_absolute()

    abs_upload = tmp_path / "nested" / "uploads"
    abs_upload.mkdir(parents=True)
    abs_settings = Settings(upload_dir=str(abs_upload))
    abs_cache = resolve_semantic_model_cache_dir(abs_settings)
    assert abs_cache == (tmp_path / "nested" / "data" / "semantic-models").resolve()


def test_runtime_model_contract_is_fixed_and_rejects_override(monkeypatch):
    """模型 ID、512 维、revision、5GiB、cache 名固定；任何非精确覆盖在校验期失败。"""
    from pydantic import ValidationError

    from app.core.config import Settings, get_settings

    get_settings.cache_clear()
    s = Settings()
    assert s.semantic_model_id == FIXED_MODEL_ID
    assert int(s.semantic_embedding_dim) == FIXED_DIM
    assert s.semantic_model_revision == FIXED_REVISION
    assert int(s.semantic_min_free_disk_bytes) == FIXED_MIN_FREE_DISK_BYTES
    assert s.semantic_model_cache_dir == "semantic-models"

    with pytest.raises(ValidationError):
        Settings(semantic_model_id="other/model")
    with pytest.raises(ValidationError):
        Settings(semantic_embedding_dim=256)
    with pytest.raises(ValidationError):
        Settings(semantic_model_revision="deadbeef")
    with pytest.raises(ValidationError):
        Settings(semantic_model_revision=FIXED_REVISION[:16])
    # 5 GiB 不可漂移
    with pytest.raises(ValidationError):
        Settings(semantic_min_free_disk_bytes=1)
    with pytest.raises(ValidationError):
        Settings(semantic_min_free_disk_bytes=FIXED_MIN_FREE_DISK_BYTES + 1)
    # cache 名：只接受去空白后的字面 semantic-models
    with pytest.raises(ValidationError):
        Settings(semantic_model_cache_dir="../semantic-models")
    with pytest.raises(ValidationError):
        Settings(semantic_model_cache_dir="x/semantic-models")
    with pytest.raises(ValidationError):
        Settings(semantic_model_cache_dir=str(Path("C:/data/semantic-models")))
    with pytest.raises(ValidationError):
        Settings(semantic_model_cache_dir="other-models")
    # 允许首尾空白
    ok = Settings(semantic_model_cache_dir="  semantic-models  ")
    assert ok.semantic_model_cache_dir == "semantic-models"

    get_settings.cache_clear()


def test_runtime_loader_is_revision_pinned_and_strictly_offline(tmp_path, monkeypatch):
    """假 SentenceTransformer 必须收到固定 ID/revision 与严格离线参数。"""
    from app.core.config import Settings
    from app.services import embedding_service
    from app.services.embedding_service import OfflineBgeEmbedder

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    cache = tmp_path / "semantic-models"
    _write_tiny_snapshot(cache)

    captured: dict = {}

    class FakeST:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = dict(kwargs)
            self.encode = lambda *a, **k: []

    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    settings = Settings(upload_dir=str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        embedding_service, "resolve_semantic_model_cache_dir", lambda s=None: cache
    )
    monkeypatch.setattr(OfflineBgeEmbedder, "_check_disk", lambda self, settings: None)

    emb = OfflineBgeEmbedder()
    emb.clear_injection()
    fp = emb.ensure_loaded_for_rebuild(settings)

    model_arg = captured["args"][0] if captured["args"] else captured["kwargs"].get(
        "model_name_or_path"
    )
    assert model_arg == FIXED_MODEL_ID
    kw = captured["kwargs"]
    assert kw.get("revision") == FIXED_REVISION
    assert kw.get("cache_folder") == str(cache)
    assert kw.get("device") == "cpu"
    assert kw.get("local_files_only") is True
    assert kw.get("trust_remote_code") is False
    assert fp
    assert _tmp_bytes_written(tmp_path) < 1024 * 1024


def test_offline_loader_missing_sentence_transformers_is_deps_missing(
    tmp_path, monkeypatch
):
    """OfflineBgeEmbedder 缺 sentence_transformers 固定 deps_missing。"""
    from app.core.config import Settings
    from app.services import embedding_service
    from app.services.embedding_service import OfflineBgeEmbedder, OfflineEmbedderError

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    cache = tmp_path / "semantic-models"
    _write_tiny_snapshot(cache)

    # 伪造导入失败
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise ImportError("no st")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(
        embedding_service, "resolve_semantic_model_cache_dir", lambda s=None: cache
    )
    monkeypatch.setattr(OfflineBgeEmbedder, "_check_disk", lambda self, settings: None)

    emb = OfflineBgeEmbedder()
    emb.clear_injection()
    with pytest.raises(OfflineEmbedderError) as ei:
        emb.ensure_loaded_for_rebuild(Settings(upload_dir=str(tmp_path / "uploads")))
    assert ei.value.code == "deps_missing"


def test_prepare_cli_has_only_explicit_download_switch():
    """解析器只允许无参数检查或 --download；任意其他参数拒绝。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_cli"
    )
    parser = prepare._build_arg_parser()

    ns = parser.parse_args([])
    assert ns.download is False
    ns2 = parser.parse_args(["--download"])
    assert ns2.download is True

    forbidden = [
        ["--model", "x"],
        ["--revision", "abc"],
        ["--url", "http://evil"],
        ["--token", "t"],
        ["--cache-dir", "/tmp/x"],
        ["--endpoint", "http://e"],
        ["--proxy", "http://p"],
        ["--skip-space-check"],
        ["--skip-hash"],
        ["--path", "/tmp"],
    ]
    for args in forbidden:
        with pytest.raises(SystemExit):
            parser.parse_args(args)


def test_download_fixed_snapshot_calls_hub_with_fixed_endpoint(
    tmp_path, monkeypatch
):
    """
    直接调用真实 download_fixed_snapshot：fake huggingface_hub.snapshot_download，
    精确断言 endpoint/repo/revision/cache/allow_patterns/token/local_files_only。
    不得 monkeypatch 整个 download_fixed_snapshot。
    """
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_hub"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    monkeypatch.setattr(prepare, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS)

    cache = tmp_path / "semantic-models"
    cache.mkdir()
    captured: dict = {}

    def fake_snapshot_download(**kwargs):
        captured.update(kwargs)
        snap = _write_tiny_snapshot(cache)
        return str(snap)

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    # 签名不得接受额外 kwarg
    import inspect

    sig = inspect.signature(prepare.download_fixed_snapshot)
    assert "**" not in str(sig) and "ignored" not in str(sig).lower()
    for banned in ("endpoint", "url", "proxy", "proxies"):
        assert banned not in sig.parameters

    path = prepare.download_fixed_snapshot(
        cache_dir=cache,
        repo_id=FIXED_MODEL_ID,
        revision=FIXED_REVISION,
        allow_patterns=TINY_ALLOW_PATTERNS,
        token=False,
    )
    assert path
    assert captured.get("repo_id") == FIXED_MODEL_ID
    assert captured.get("revision") == FIXED_REVISION
    assert captured.get("cache_dir") == str(cache)
    assert captured.get("token") is False
    assert captured.get("local_files_only") is False
    assert captured.get("endpoint") == FIXED_HF_ENDPOINT
    assert tuple(captured.get("allow_patterns") or ()) == TINY_ALLOW_PATTERNS
    assert "pytorch_model.bin" not in (captured.get("allow_patterns") or [])

    # 非法 allow_patterns 固定拒绝
    with pytest.raises(Exception) as ei:
        prepare.download_fixed_snapshot(
            cache_dir=cache,
            allow_patterns=list(TINY_ALLOW_PATTERNS) + ["pytorch_model.bin"],
            token=False,
        )
    code = getattr(ei.value, "code", "")
    assert code == "model_artifact_mismatch"


def test_prepare_download_uses_fixed_snapshot_and_no_token(tmp_path, monkeypatch, capsys):
    """CLI --download 在无有效缓存时走真实 download_fixed_snapshot（hub fake）。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_dl"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    monkeypatch.setattr(prepare, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS)

    cache = tmp_path / "semantic-models"
    cache.mkdir()
    monkeypatch.setattr(prepare, "resolve_cache_dir", lambda: cache)
    monkeypatch.setattr(prepare, "check_min_disk", lambda cache_dir: None)

    calls: list[dict] = []

    def fake_snapshot_download(**kwargs):
        calls.append(dict(kwargs))
        return str(_write_tiny_snapshot(cache))

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = fake_snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    code = prepare.main(["--download"])
    assert code == 0
    assert len(calls) == 1
    assert calls[0].get("endpoint") == FIXED_HF_ENDPOINT
    assert calls[0].get("repo_id") == FIXED_MODEL_ID
    assert calls[0].get("revision") == FIXED_REVISION
    assert calls[0].get("token") is False
    assert tuple(calls[0].get("allow_patterns") or ()) == TINY_ALLOW_PATTERNS
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload.get("ok") is True
    assert payload.get("revision") == FIXED_REVISION
    assert str(cache.resolve()) not in out


def test_prepare_no_arg_and_valid_cache_download_zero_hub_calls(
    tmp_path, monkeypatch, capsys
):
    """无参数 prepare 与有效缓存 --download 均不得调用 snapshot_download。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_zero"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    monkeypatch.setattr(prepare, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS)

    cache = tmp_path / "semantic-models"
    _write_tiny_snapshot(cache)
    monkeypatch.setattr(prepare, "resolve_cache_dir", lambda: cache)
    monkeypatch.setattr(prepare, "check_min_disk", lambda cache_dir: None)

    calls: list = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise AssertionError("不得调用 snapshot_download")

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = boom
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    # 无参数只读
    code0 = prepare.main([])
    assert code0 == 0
    assert calls == []
    out0 = json.loads(capsys.readouterr().out)
    assert out0.get("ok") is True

    # 有效缓存 --download：下载调用精确 0
    code1 = prepare.main(["--download"])
    assert code1 == 0
    assert calls == []
    out1 = json.loads(capsys.readouterr().out)
    assert out1.get("ok") is True
    assert out1.get("fileCount") == 10


def test_artifact_manifest_rejects_missing_size_hash_extra(tmp_path, monkeypatch):
    """空缓存 unavailable；缺/多/大小/哈希错误精确 mismatch；错误不含绝对路径。"""
    from app.services import embedding_service
    from app.services.embedding_service import OfflineEmbedderError

    _patch_tiny_artifacts(monkeypatch, embedding_service)

    # 空缓存
    cache = tmp_path / "empty"
    cache.mkdir()
    with pytest.raises(OfflineEmbedderError) as ei_empty:
        embedding_service.validate_semantic_model_artifacts(cache)
    assert ei_empty.value.code == "model_unavailable"
    assert str(cache.resolve()) not in str(ei_empty.value)

    # 缺文件
    cache2 = tmp_path / "c2"
    snap2 = _write_tiny_snapshot(cache2, drop_file="vocab.txt")
    with pytest.raises(OfflineEmbedderError) as ei_file:
        embedding_service.validate_semantic_model_artifacts(cache2)
    assert ei_file.value.code == "model_artifact_mismatch"
    assert str(cache2.resolve()) not in str(ei_file.value)

    # 额外文件
    cache_extra = tmp_path / "c_extra"
    _write_tiny_snapshot(cache_extra, extra_file="README.md")
    with pytest.raises(OfflineEmbedderError) as ei_extra:
        embedding_service.validate_semantic_model_artifacts(cache_extra)
    assert ei_extra.value.code == "model_artifact_mismatch"

    # 大小异常
    cache3 = tmp_path / "c3"
    _write_tiny_snapshot(cache3, resize_file=("config.json", 1))
    with pytest.raises(OfflineEmbedderError) as ei_size:
        embedding_service.validate_semantic_model_artifacts(cache3)
    assert ei_size.value.code == "model_artifact_mismatch"
    assert str(cache3.resolve()) not in str(ei_size.value)

    # 哈希异常
    cache4 = tmp_path / "c4"
    _write_tiny_snapshot(cache4, corrupt_hash=True)
    with pytest.raises(OfflineEmbedderError) as ei_hash:
        embedding_service.validate_semantic_model_artifacts(cache4)
    assert ei_hash.value.code == "model_artifact_mismatch"
    assert str(cache4.resolve()) not in str(ei_hash.value)

    # 指纹：缺失文件必须 mismatch，不得写 missing 后成功
    cache5 = tmp_path / "c5"
    snap5 = _write_tiny_snapshot(cache5)
    (snap5 / "vocab.txt").unlink()
    with pytest.raises(OfflineEmbedderError) as ei_fp:
        embedding_service.compute_fixed_artifact_fingerprint(cache5)
    assert ei_fp.value.code == "model_artifact_mismatch"

    assert _tmp_bytes_written(tmp_path) < 1024 * 1024


def test_failed_download_preserves_existing_valid_snapshot(tmp_path, monkeypatch, capsys):
    """已有有效制品时下载异常不改写文件；固定 model_download_failed。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_preserve"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    monkeypatch.setattr(prepare, "FIXED_DOWNLOAD_ALLOW_PATTERNS", TINY_ALLOW_PATTERNS)

    cache = tmp_path / "semantic-models"
    # 先无有效缓存，强制走下载路径：先写有效再删除? 
    # 场景：无有效缓存 + hub 抛错 → download_failed
    # 以及：有有效缓存时不应调用 hub（已由 zero 测覆盖）
    # 本测：模拟 validate 先失败（空）再 download 抛错
    cache.mkdir()
    monkeypatch.setattr(prepare, "resolve_cache_dir", lambda: cache)
    monkeypatch.setattr(prepare, "check_min_disk", lambda cache_dir: None)

    # 先建立有效快照，再让 download 在“被强制调用”时失败——但生产有缓存应 0 次。
    # 契约：无有效缓存且真实下载异常才 download_failed。
    # 清空后再测无缓存下载失败
    def boom(**kwargs):
        raise RuntimeError("network down secret path C:\\Users\\x\\repo")

    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = boom
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    code = prepare.main(["--download"])
    assert code != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload.get("ok") is False
    assert payload.get("errorCode") == "model_download_failed"
    assert "network down" not in out
    assert "C:\\Users" not in out
    assert str(cache.resolve()) not in out

    # 有有效快照时 download 失败不得发生（0 调用）；若 prior 有效应直接成功
    snap = _write_tiny_snapshot(cache)
    before = {rel: (snap / rel).read_bytes() for rel in TINY_ARTIFACT_FILES}
    code2 = prepare.main(["--download"])
    assert code2 == 0
    for rel, content in before.items():
        assert (snap / rel).read_bytes() == content


def test_hub_import_missing_is_deps_missing_not_download_failed(
    tmp_path, monkeypatch, capsys
):
    """huggingface_hub 导入缺失固定 deps_missing，不得折叠为 model_download_failed。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_deps"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    cache = tmp_path / "semantic-models"
    cache.mkdir()
    monkeypatch.setattr(prepare, "resolve_cache_dir", lambda: cache)
    monkeypatch.setattr(prepare, "check_min_disk", lambda cache_dir: None)

    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "huggingface_hub" or name.startswith("huggingface_hub."):
            raise ImportError("no hub")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)

    code = prepare.main(["--download"])
    assert code != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload.get("errorCode") == "deps_missing"
    assert payload.get("errorCode") != "model_download_failed"


def test_preflight_loads_with_fixed_offline_params(tmp_path, monkeypatch):
    """preflight 真实加载路径：Fake ST 精确收到 revision/local_files_only/trust_remote_code/CPU/cache。"""
    preflight = _load_script_module(
        "semantic_model_preflight.py", "semantic_model_preflight_under_test_load"
    )
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    cache = tmp_path / "semantic-models"
    _write_tiny_snapshot(cache)

    captured: dict = {}

    class FakeST:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = dict(kwargs)

        def encode(self, texts, **kwargs):
            from app.services.embedding_service import deterministic_offline_embed

            return [deterministic_offline_embed(t) for t in texts]

    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    model = preflight.load_local_sentence_transformer(cache, model_id=FIXED_MODEL_ID)
    assert model is not None
    model_arg = captured["args"][0] if captured["args"] else None
    assert model_arg == FIXED_MODEL_ID
    kw = captured["kwargs"]
    assert kw.get("revision") == FIXED_REVISION
    assert kw.get("local_files_only") is True
    assert kw.get("trust_remote_code") is False
    assert kw.get("device") == "cpu"
    assert kw.get("cache_folder") == str(cache)


def test_preflight_unexpected_error_json_has_no_third_party_type(monkeypatch, capsys):
    """preflight 未预期异常 JSON 不得含 detailType 等第三方类型泄漏。"""
    preflight = _load_script_module(
        "semantic_model_preflight.py", "semantic_model_preflight_under_test_err"
    )

    def boom():
        raise RuntimeError("secret boom")

    monkeypatch.setattr(preflight, "run_preflight", boom)
    code = preflight.main([])
    assert code == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload.get("ok") is False
    assert payload.get("errorCode") == "internal_error"
    assert "detailType" not in payload
    assert "RuntimeError" not in out
    assert "secret boom" not in out
    # 仅固定有界字段
    allowed = {"ok", "errorCode", "message", "modelId", "revision", "dimension", "hint"}
    assert set(payload.keys()) <= allowed


def test_prepare_and_preflight_import_purity_no_db_chain(tmp_path):
    """
    导入 prepare/preflight 不得加载 sqlalchemy / app.models / app.db / knowledge_service。
    使用隔离子进程 + CREATE_NO_WINDOW，禁止“模块此前已加载则允许”假绿。
    """
    probe = r"""
import sys
from pathlib import Path
backend = Path(r"%s")
sys.path.insert(0, str(backend))
banned_prefixes = (
    "sqlalchemy",
    "app.models",
    "app.db",
    "app.services.knowledge_service",
    "app.services.settings_service",
)
# 清理可能残留
for k in list(sys.modules):
    if k == "sqlalchemy" or k.startswith("sqlalchemy.") or k.startswith("app."):
        del sys.modules[k]

import importlib.util

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# 只导入纯制品接口链
load("prepare_semantic_model_probe", backend / "scripts" / "prepare_semantic_model.py")
load("semantic_model_preflight_probe", backend / "scripts" / "semantic_model_preflight.py")

leaked = []
for k in sys.modules:
    for b in banned_prefixes:
        if k == b or k.startswith(b + "."):
            leaked.append(k)
if leaked:
    print("LEAK:" + ",".join(sorted(set(leaked))))
    sys.exit(3)
print("PURE_OK")
sys.exit(0)
""" % str(BACKEND_ROOT).replace("\\", "\\\\")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=creationflags,
    )
    assert proc.returncode == 0, (
        f"import purity failed rc={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "PURE_OK" in proc.stdout
    assert "LEAK:" not in proc.stdout


def test_prepare_and_preflight_source_has_no_db_calls():
    """源码层禁止数据库/知识库可执行导入痕迹。"""
    prepare = _load_script_module(
        "prepare_semantic_model.py", "prepare_semantic_model_under_test_src"
    )
    preflight = _load_script_module(
        "semantic_model_preflight.py", "semantic_model_preflight_under_test_src"
    )
    for mod in (prepare, preflight):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for bad in (
            "import knowledge_service",
            "from app.services import knowledge",
            "from app.services.knowledge_service",
            "from app.services import settings_service",
            "from app.services.settings_service",
            "from app.models",
            "import app.models",
            "SessionLocal",
            "get_db(",
            "create_engine(",
            "kb_chunks",
        ):
            assert bad not in src, f"{mod.__file__} contains {bad}"


def test_tmp_fixture_budget_under_1mib(tmp_path, monkeypatch):
    """整个专项行为夹具真实临时文件总量必须 <1MiB（本用例自身验证小型写入）。"""
    from app.services import embedding_service

    _patch_tiny_artifacts(monkeypatch, embedding_service)
    for i in range(5):
        _write_tiny_snapshot(tmp_path / f"c{i}")
    total = _tmp_bytes_written(tmp_path)
    assert total < 1024 * 1024
    assert total < 4096
