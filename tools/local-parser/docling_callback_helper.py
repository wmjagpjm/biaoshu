# -*- coding: utf-8 -*-
"""
模块：P8E 本机 Docling 外置解析助手
用途：用户显式选择本地源文件与离线模型目录，离线调用 PATH 中已安装的 docling，再用 P8C 一次性票据以 source=docling 向回环后端回传 Markdown。
对接：docs/p8e-docling-local-helper-contract.md；复用 mineru_callback_helper 已验收原语；POST /api/local-parser/callback。
二次开发：仅标准库；不复制 P8D 输入/票据/Origin/Markdown/HTTP 实现；Windows 仅 docling.exe；固定 convert argv；禁止远程服务/插件/代理/自定义 executable；禁止打印票据/路径/模型目录/正文/taskId。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from mineru_callback_helper import (
    DEFAULT_BACKEND_ORIGIN,
    HelperError,
    MSG_ERR_CALLBACK,
    MSG_ERR_INPUT,
    MSG_ERR_INTERRUPTED,
    MSG_ERR_ORIGIN,
    MSG_ERR_TICKET,
    MSG_ERR_USAGE,
    MSG_SUCCESS,
    find_and_read_markdown,
    normalize_backend_origin,
    post_callback,
    read_ticket_from_getpass,
    terminate_process,
    validate_filename_basename,
    validate_input_file,
    validate_ticket,
)


# ---------------------------------------------------------------------------
# 契约常量（Docling 专属）
# ---------------------------------------------------------------------------

DOCLING_TIMEOUT_SECONDS = 30 * 60
DOCLING_DOCUMENT_TIMEOUT = 1800

MSG_ERR_ARTIFACTS = "模型目录无效"
MSG_ERR_DOCLING_MISSING = "未找到 Docling 命令，请先按官方文档完成安装与模型准备"
MSG_ERR_DOCLING_FAILED = "Docling 解析失败"
MSG_ERR_DOCLING_TIMEOUT = "Docling 解析超时"

# 只读运行所需：可从父环境白名单继承（PATH/系统根/locale）
_READONLY_RUNTIME_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "LANG",
    "LC_ALL",
)

# 可写运行目录：全部强制指向本次 TemporaryDirectory 根，禁止继承用户固定缓存
_WRITABLE_RUNTIME_ENV_KEYS: tuple[str, ...] = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "HF_HOME",
    "TORCH_HOME",
    "MPLCONFIGDIR",
    "PYTHONPYCACHEPREFIX",
)

# 扩展名 → 官方 CLI --from（大小写不敏感由 basename 映射）
EXTENSION_TO_FROM: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}

# --from 精确固定枚举（禁止大小写/空白/前后缀变体）
ALLOWED_FROM_FORMATS: frozenset[str] = frozenset(
    {"pdf", "image", "docx", "pptx", "xlsx"}
)


# ---------------------------------------------------------------------------
# 模型目录与 --from 映射
# ---------------------------------------------------------------------------


def validate_artifacts_path(path_str: str) -> Path:
    """
    用途：校验 --artifacts-path 为已存在普通非符号链接目录，解析后仍为目录。
    二次开发：拒绝 URL/文件/不存在路径；错误信息不得包含绝对路径。
    """
    if not path_str or not str(path_str).strip():
        raise HelperError(MSG_ERR_ARTIFACTS)
    text = str(path_str).strip()
    # 禁止 URL / 远程 scheme 伪装成本地路径
    if "://" in text:
        raise HelperError(MSG_ERR_ARTIFACTS)
    path = Path(text)
    try:
        if path.is_symlink():
            raise HelperError(MSG_ERR_ARTIFACTS)
        if not path.exists() or not path.is_dir():
            raise HelperError(MSG_ERR_ARTIFACTS)
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_dir():
            raise HelperError(MSG_ERR_ARTIFACTS)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_ARTIFACTS) from exc
    return resolved


def map_extension_to_from(filename: str) -> str:
    """用途：按输入 basename 精确映射七类扩展到五种官方 --from 值。"""
    name = Path(filename).name
    if "." not in name:
        raise HelperError(MSG_ERR_INPUT)
    ext = "." + name.rsplit(".", 1)[-1].lower()
    mapped = EXTENSION_TO_FROM.get(ext)
    if mapped is None:
        raise HelperError(MSG_ERR_INPUT)
    return mapped


# ---------------------------------------------------------------------------
# Docling 子进程
# ---------------------------------------------------------------------------


def resolve_docling_executable() -> str:
    """
    用途：仅 shutil.which('docling')；Windows 只接受 .exe；POSIX 只接受可执行普通非 symlink 文件。
    二次开发：拒绝 .cmd/.bat/.com/无后缀，避免 shell=False 仍经命令解释器的假绿。
    """
    path = shutil.which("docling")
    if not path:
        raise HelperError(MSG_ERR_DOCLING_MISSING)
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            raise HelperError(MSG_ERR_DOCLING_MISSING)
        if not candidate.is_file():
            raise HelperError(MSG_ERR_DOCLING_MISSING)
        if os.name == "nt":
            if candidate.suffix.lower() != ".exe":
                raise HelperError(MSG_ERR_DOCLING_MISSING)
        else:
            if not os.access(str(candidate), os.X_OK):
                raise HelperError(MSG_ERR_DOCLING_MISSING)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_DOCLING_MISSING) from exc
    return str(candidate)


def build_docling_command(
    docling: str,
    input_path: Path,
    output_dir: Path,
    artifacts_path: Path,
    from_format: str,
) -> list[str]:
    """
    用途：固定 argv 顺序（契约 §4.2）：docling convert … --output <临时> <绝对输入>。
    二次开发：禁止 convert-remote、URL、headers、service/API、VLM/ASR、额外用户参数。
    from_format 仅允许 pdf/image/docx/pptx/xlsx 精确成员，非法值在构 argv 前固定失败。
    """
    if not isinstance(from_format, str) or from_format not in ALLOWED_FROM_FORMATS:
        raise HelperError(MSG_ERR_INPUT)
    return [
        docling,
        "convert",
        "--from",
        from_format,
        "--to",
        "md",
        "--image-export-mode",
        "placeholder",
        "--pipeline",
        "standard",
        "--artifacts-path",
        str(artifacts_path.resolve()),
        "--no-enable-remote-services",
        "--no-allow-external-plugins",
        "--abort-on-error",
        "--document-timeout",
        str(DOCLING_DOCUMENT_TIMEOUT),
        "--num-threads",
        "1",
        "--device",
        "cpu",
        "--output",
        str(output_dir.resolve()),
        str(input_path.resolve()),
    ]


def build_docling_env(
    runtime_dir: Path | str,
    source_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    用途：显式绑定本次运行目录；只读白名单可继承；所有可写目录强制指向 runtime_dir；
    强制 HF/Transformers 离线；不继承代理/Docling service/API/artifacts/票据/用户固定缓存。
    二次开发：禁止无参调用或回退 USERPROFILE/TEMP；runtime_dir 必须是已存在普通目录。
    """
    # 校验本次运行目录：必须存在且为目录，不得回退用户固定路径
    try:
        path = Path(runtime_dir)
        if path.is_symlink() or not path.exists() or not path.is_dir():
            raise HelperError(MSG_ERR_DOCLING_FAILED)
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_dir():
            raise HelperError(MSG_ERR_DOCLING_FAILED)
    except HelperError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise HelperError(MSG_ERR_DOCLING_FAILED) from exc

    runtime = str(resolved)
    src = os.environ if source_env is None else source_env
    env: dict[str, str] = {}

    # 只读运行所需：PATH/SystemRoot/WINDIR/LANG/LC_ALL（不从父环境拷可写目录）
    for key in _READONLY_RUNTIME_ENV_KEYS:
        if key in src and src[key] is not None:
            env[key] = str(src[key])

    # 可写目录全部强制到本次 biaoshu-docling-* 临时根（契约 §4.3）
    for key in _WRITABLE_RUNTIME_ENV_KEYS:
        env[key] = runtime

    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def run_docling_process(
    docling: str,
    input_path: Path,
    output_dir: Path,
    artifacts_path: Path,
    from_format: str,
    *,
    timeout_seconds: int | None = None,
) -> None:
    """
    用途：shell=False 参数数组启动 Docling；丢弃 stdin/stdout/stderr；超时/中断终止进程。
    二次开发：显式 cwd=输出临时目录；环境仅由已解析 output_dir 构造；超时直接调用共享 terminate_process，不修改共享模块全局状态。
    """
    cmd = build_docling_command(
        docling, input_path, output_dir, artifacts_path, from_format
    )
    # 仅用本次已解析 output_dir 构造环境，禁止回退用户固定目录
    workdir_path = output_dir.resolve()
    env = build_docling_env(workdir_path)
    timeout = DOCLING_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    workdir = str(workdir_path)
    try:
        proc = subprocess.Popen(
            cmd,
            shell=False,
            env=env,
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HelperError(MSG_ERR_DOCLING_FAILED) from exc

    try:
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process(proc)
            raise HelperError(MSG_ERR_DOCLING_TIMEOUT) from exc
        except KeyboardInterrupt as exc:
            terminate_process(proc)
            raise HelperError(MSG_ERR_INTERRUPTED) from exc
    except HelperError:
        raise
    except Exception as exc:
        terminate_process(proc)
        raise HelperError(MSG_ERR_DOCLING_FAILED) from exc

    if returncode != 0:
        raise HelperError(MSG_ERR_DOCLING_FAILED)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _QuietArgumentParser(argparse.ArgumentParser):
    """用途：参数错误只抛固定中文，避免 argparse 回显用户参数中的敏感片段。"""

    def error(self, message: str) -> None:  # noqa: A003
        raise HelperError(MSG_ERR_USAGE, exit_code=2)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """用途：仅接受 --input、--artifacts-path 与可选 --backend-origin。"""
    parser = _QuietArgumentParser(
        prog="docling_callback_helper",
        description="本机 Docling 外置解析并回传（P8E）",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="本地源文件路径（单文件）",
    )
    parser.add_argument(
        "--artifacts-path",
        required=True,
        help="本地 Docling 模型目录（已存在普通非符号链接目录）",
    )
    parser.add_argument(
        "--backend-origin",
        default=None,
        help=f"回环后端 Origin，默认 {DEFAULT_BACKEND_ORIGIN}",
    )
    return parser.parse_args(list(argv)[1:] if argv is not None else None)


def run_pipeline(
    *,
    input_path: Path,
    artifacts_path: Path,
    origin: str,
    ticket: str,
    docling: str | None = None,
) -> None:
    """用途：临时目录内跑 Docling → 读唯一 Markdown → source=docling 单次回调；全程 finally 清理。"""
    exe = docling if docling is not None else resolve_docling_executable()
    filename = validate_filename_basename(input_path.name)
    from_format = map_extension_to_from(filename)
    safe_origin = normalize_backend_origin(origin)
    safe_ticket = validate_ticket(ticket)
    # 无条件再校验：调用者传入绝对 Path 也不得绕过目录/symlink/存在性检查
    safe_artifacts = validate_artifacts_path(str(artifacts_path))

    with tempfile.TemporaryDirectory(prefix="biaoshu-docling-") as tmp:
        out_dir = Path(tmp)
        run_docling_process(exe, input_path, out_dir, safe_artifacts, from_format)
        markdown = find_and_read_markdown(out_dir)
        post_callback(
            safe_origin,
            safe_ticket,
            markdown,
            filename,
            source="docling",
        )


def main(argv: Sequence[str] | None = None) -> int:
    """用途：最薄 CLI 入口；所有异常转为固定中文与非零退出码。"""
    try:
        args = parse_args(argv if argv is not None else sys.argv)
        input_path = validate_input_file(args.input)
        artifacts_path = validate_artifacts_path(args.artifacts_path)
        origin = normalize_backend_origin(args.backend_origin)
        ticket = read_ticket_from_getpass()
        run_pipeline(
            input_path=input_path,
            artifacts_path=artifacts_path,
            origin=origin,
            ticket=ticket,
        )
        print(MSG_SUCCESS)
        return 0
    except HelperError as exc:
        print(exc.message, file=sys.stderr)
        return int(exc.exit_code) if exc.exit_code else 1
    except KeyboardInterrupt:
        print(MSG_ERR_INTERRUPTED, file=sys.stderr)
        return 130
    except Exception:
        # 兜底：不回显异常细节
        print(MSG_ERR_CALLBACK, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
