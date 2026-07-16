"""
模块：P9C-R1 固定离线语义模型显式准备 CLI
用途：无参数只读校验固定 revision 制品；仅 --download 可联网拉取固定 10 文件快照。
对接：app.core.config 固定常量；app.services.embedding_service 制品校验与清单。
二次开发：
  - 禁止任意模型/URL/revision/Token/路径/endpoint/代理/跳过参数
  - 禁止导入数据库或知识库服务模块；禁止读取知识库正文
  - 生产服务不得反向导入本脚本；import 期不得执行网络
  - download_fixed_snapshot 签名不得接受额外 endpoint/URL/代理 kwargs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# 允许从 backend 根目录或仓库根目录启动
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import (  # noqa: E402
    FIXED_HF_ENDPOINT,
    SEMANTIC_MIN_FREE_DISK_BYTES,
    SEMANTIC_MODEL_ID,
    SEMANTIC_MODEL_REVISION,
    get_settings,
    resolve_semantic_model_cache_dir,
)
from app.services.embedding_service import (  # noqa: E402
    ERR_ARTIFACT_MISMATCH,
    ERR_DEPS_MISSING,
    ERR_DOWNLOAD_FAILED,
    ERR_MODEL_UNAVAILABLE,
    ERR_STORAGE_INSUFFICIENT,
    FIXED_DOWNLOAD_ALLOW_PATTERNS,
    FIXED_MODEL_ID,
    FIXED_MODEL_REVISION,
    OfflineEmbedderError,
    validate_semantic_model_artifacts,
)


def resolve_cache_dir() -> Path:
    """用途：与生产一致的固定缓存根（相对 upload_dir 锚定 backend）。"""
    return resolve_semantic_model_cache_dir(get_settings())


def check_min_disk(cache_dir: Path) -> None:
    """
    用途：准备前检查最低 5 GiB 可用空间；不足固定 model_storage_insufficient。
    说明：使用不可漂移固定常量；不输出绝对路径。
    """
    import shutil

    min_free = int(SEMANTIC_MIN_FREE_DISK_BYTES)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        free = int(shutil.disk_usage(str(cache_dir)).free)
    except OSError as exc:
        raise OfflineEmbedderError(ERR_MODEL_UNAVAILABLE, "cache_unusable") from exc
    if free < min_free:
        raise OfflineEmbedderError(ERR_STORAGE_INSUFFICIENT, "disk_low")


def download_fixed_snapshot(
    *,
    cache_dir: Path | None = None,
    repo_id: str = FIXED_MODEL_ID,
    revision: str = FIXED_MODEL_REVISION,
    allow_patterns: tuple[str, ...] | list[str] = FIXED_DOWNLOAD_ALLOW_PATTERNS,
    token: bool = False,
) -> str:
    """
    用途：唯一联网入口；固定 repo/revision/10 文件白名单/endpoint，token=False。
    返回：快照目录字符串（仅内部使用，不进入用户输出）。
    二次开发：签名禁止 **kwargs；测试可 fake huggingface_hub.snapshot_download。
    """
    if repo_id != FIXED_MODEL_ID or revision != FIXED_MODEL_REVISION:
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "download_contract")
    if token is not False:
        raise OfflineEmbedderError(ERR_DOWNLOAD_FAILED, "token_forbidden")

    patterns = tuple(allow_patterns)
    # 必须与固定 10 文件元组精确相等：数量、顺序、无重复、无额外、无 bin
    if patterns != tuple(FIXED_DOWNLOAD_ALLOW_PATTERNS):
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "patterns_invalid")
    if "pytorch_model.bin" in patterns:
        raise OfflineEmbedderError(ERR_ARTIFACT_MISMATCH, "bin_forbidden")

    target = cache_dir or resolve_cache_dir()

    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise OfflineEmbedderError(ERR_DEPS_MISSING, "deps_missing") from exc

    try:
        # 显式 endpoint：固定 huggingface.co，不受 HF_ENDPOINT 环境改写
        # token=False：不携带 Hugging Face Token；正文/查询/工作空间永不进入请求
        path = snapshot_download(
            repo_id=repo_id,
            revision=revision,
            cache_dir=str(target),
            allow_patterns=list(patterns),
            ignore_patterns=["pytorch_model.bin", "README.md", ".gitattributes"],
            token=False,
            local_files_only=False,
            endpoint=FIXED_HF_ENDPOINT,
        )
    except OfflineEmbedderError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 不泄露第三方异常正文
        raise OfflineEmbedderError(ERR_DOWNLOAD_FAILED, "download_failed") from exc
    return str(path)


def _bounded_payload(
    *,
    ok: bool,
    error_code: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用途：单一有界 JSON；不含绝对路径、第三方正文。"""
    base: dict[str, Any] = {
        "ok": ok,
        "modelId": SEMANTIC_MODEL_ID,
        "revision": SEMANTIC_MODEL_REVISION,
        "errorCode": error_code,
        "artifactFingerprint": None,
        "fileCount": None,
        "totalBytes": None,
    }
    if meta:
        base["artifactFingerprint"] = meta.get("artifactFingerprint")
        base["fileCount"] = meta.get("fileCount")
        base["totalBytes"] = meta.get("totalBytes")
        if meta.get("modelId"):
            base["modelId"] = meta["modelId"]
        if meta.get("revision"):
            base["revision"] = meta["revision"]
    return base


def run_check(cache_dir: Path | None = None) -> dict[str, Any]:
    """用途：无参数只读检查；不联网、不写库。"""
    cache = cache_dir or resolve_cache_dir()
    meta = validate_semantic_model_artifacts(cache)
    return _bounded_payload(ok=True, meta=meta)


def run_download(cache_dir: Path | None = None) -> dict[str, Any]:
    """
    用途：显式 --download；既有有效快照直接成功且下载调用 0 次；
    无有效缓存且下载异常才 model_download_failed；deps_missing 原样上抛。
    """
    cache = cache_dir or resolve_cache_dir()
    check_min_disk(cache)

    # 既有有效固定快照：直接返回成功元数据，避免无意义联网
    try:
        meta = validate_semantic_model_artifacts(cache)
        return _bounded_payload(ok=True, meta=meta)
    except OfflineEmbedderError as prior_exc:
        if prior_exc.code not in (ERR_MODEL_UNAVAILABLE, ERR_ARTIFACT_MISMATCH):
            raise
        # 无有效缓存，继续下载

    try:
        download_fixed_snapshot(
            cache_dir=cache,
            repo_id=FIXED_MODEL_ID,
            revision=FIXED_MODEL_REVISION,
            allow_patterns=FIXED_DOWNLOAD_ALLOW_PATTERNS,
            token=False,
        )
    except OfflineEmbedderError as exc:
        # deps_missing 不得折叠为 download_failed
        if exc.code == ERR_DEPS_MISSING:
            raise
        raise OfflineEmbedderError(ERR_DOWNLOAD_FAILED, "download_failed") from exc
    except Exception as exc:  # noqa: BLE001
        raise OfflineEmbedderError(ERR_DOWNLOAD_FAILED, "download_failed") from exc

    meta = validate_semantic_model_artifacts(cache)
    return _bounded_payload(ok=True, meta=meta)


def _build_arg_parser() -> argparse.ArgumentParser:
    """
    用途：构建 CLI；仅允许无参数或 --download。
    故意不提供模型/revision/URL/Token/路径/endpoint/代理/跳过空间/哈希等开关。
    """
    p = argparse.ArgumentParser(
        description=(
            "P9C 固定离线模型准备：仅 BAAI/bge-small-zh-v1.5 固定提交；"
            "无参数只读检查，--download 为唯一联网入口"
        )
    )
    p.add_argument(
        "--download",
        action="store_true",
        default=False,
        help="显式联网下载固定 revision 的 10 个必需文件（唯一联网开关）",
    )
    return p


def main(argv: Iterable[str] | None = None) -> int:
    """用途：CLI 入口；成功 0，受控失败 2，未捕获 1。"""
    args = _build_arg_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.download:
            result = run_download()
        else:
            result = run_check()
    except OfflineEmbedderError as exc:
        payload = _bounded_payload(ok=False, error_code=exc.code)
        # 补充固定 hint（无路径、无异常正文）
        if exc.code == ERR_MODEL_UNAVAILABLE:
            payload["message"] = "本地固定模型制品未就绪"
        elif exc.code == ERR_ARTIFACT_MISMATCH:
            payload["message"] = "固定模型制品与契约不符"
        elif exc.code == ERR_STORAGE_INSUFFICIENT:
            payload["message"] = "磁盘空间不足，无法准备模型"
        elif exc.code == ERR_DOWNLOAD_FAILED:
            payload["message"] = "模型下载失败，已保留既有有效缓存"
        elif exc.code == ERR_DEPS_MISSING:
            payload["message"] = "缺少模型准备依赖，请安装固定直接依赖后重试"
        else:
            payload["message"] = "模型准备失败"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    except Exception:  # noqa: BLE001
        payload = _bounded_payload(ok=False, error_code="internal_error")
        payload["message"] = "准备过程发生未预期错误"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
