# -*- coding: utf-8 -*-
"""
模块：V1-M 管理式 OCR pure core（M1 CLI 与后端 adapter 唯一真源）
用途：manifest/path/no-follow/ready 校验与单文件 MinerU runner；固定诊断码与中文消息。
对接：managed_runtime_preflight.py 委托；backend managed_parse_runtime_service 固定相对路径加载。
二次开发：禁止 env 指定模块路径；禁止复制第二套逻辑；runner 仅 parse_one_file_with_manifest_cli。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile as _stdlib_tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

# 保证同目录 mineru_callback_helper 可导入（backend 按文件路径加载本模块时）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


class _TempfileFacade:
    """
    用途：暴露 core.tempfile.mkdtemp 作为唯一 TEMP factory seam。
    二次开发：不得把标准库 tempfile 模块直接绑到 core.tempfile，
      否则测试侧 patch 会误伤夹具 mkdtemp；仅 runner 经本门面创建 work TEMP。
    """

    def mkdtemp(self, *args: Any, **kwargs: Any) -> str:
        return _stdlib_tempfile.mkdtemp(*args, **kwargs)


# 测试可 patch core.tempfile.mkdtemp；与全局 tempfile 模块隔离
tempfile = _TempfileFacade()

from mineru_callback_helper import (  # noqa: E402
    HelperError,
    MSG_ERR_INTERRUPTED,
    MSG_ERR_MARKDOWN,
    MSG_ERR_MINERU_FAILED,
    MSG_ERR_MINERU_TIMEOUT,
    build_mineru_command,
    build_mineru_env,
    find_and_read_markdown,
)

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

FILE_TIMEOUT_SEC: float = 1800.0
POLL_INTERVAL_SEC: float = 1.0
TASK_TIMEOUT_SEC: float = 7200.0

TEMP_PREFIX = "biaoshu-managed-ocr-"
MODEL_MARKER_MAX_BYTES = 64 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

ENGINE = "mineru"
MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "engine",
        "cliRelativePath",
        "modelMarkerRelativePath",
        "requiredFreeBytes",
    }
)

DIAG_EXIT: dict[str, int] = {
    "static_ready": 0,
    "ocr_passed": 0,
    "runtime_manifest_invalid": 2,
    "cli_missing": 2,
    "model_missing": 2,
    "disk_insufficient": 2,
    "quality_precondition_failed": 2,
    "parser_failed": 2,
    "parser_timeout": 2,
    "output_invalid": 2,
    "ocr_marker_missing": 2,
    "interrupted": 130,
    "internal_error": 1,
    "argument_invalid": 2,
}

MSG_STATIC_READY = "静态检查通过，尚未运行解析器"
MSG_OCR_PASSED = "合成扫描样本解析通过并命中预期标记"
MSG_ARGUMENT_INVALID = "参数无效"
MSG_MANIFEST_INVALID = "运行时清单无效"
MSG_CLI_MISSING = "未找到解析器命令或安全类型不合格"
MSG_MODEL_MISSING = "模型就绪标记缺失"
MSG_DISK_INSUFFICIENT = "目标卷可用空间不足"
MSG_QUALITY_PRECONDITION = "质量配置前置条件不满足"
MSG_PARSER_FAILED = "解析器运行失败"
MSG_PARSER_TIMEOUT = "解析器运行超时"
MSG_OUTPUT_INVALID = "未找到合法的唯一 Markdown 输出"
MSG_OCR_MARKER_MISSING = "输出未包含预期 OCR 标记或顺序错误"
MSG_INTERRUPTED = "操作已中断"
MSG_INTERNAL_ERROR = "预检内部错误"

_DIAG_DEFAULT_MESSAGE: dict[str, str] = {
    "static_ready": MSG_STATIC_READY,
    "ocr_passed": MSG_OCR_PASSED,
    "runtime_manifest_invalid": MSG_MANIFEST_INVALID,
    "cli_missing": MSG_CLI_MISSING,
    "model_missing": MSG_MODEL_MISSING,
    "disk_insufficient": MSG_DISK_INSUFFICIENT,
    "quality_precondition_failed": MSG_QUALITY_PRECONDITION,
    "parser_failed": MSG_PARSER_FAILED,
    "parser_timeout": MSG_PARSER_TIMEOUT,
    "output_invalid": MSG_OUTPUT_INVALID,
    "ocr_marker_missing": MSG_OCR_MARKER_MISSING,
    "interrupted": MSG_INTERRUPTED,
    "internal_error": MSG_INTERNAL_ERROR,
    "argument_invalid": MSG_ARGUMENT_INVALID,
}

_HELPER_MSG_TO_DIAG: dict[str, str] = {
    MSG_ERR_MINERU_FAILED: "parser_failed",
    MSG_ERR_MINERU_TIMEOUT: "parser_timeout",
    MSG_ERR_MARKDOWN: "output_invalid",
    MSG_ERR_INTERRUPTED: "interrupted",
}


class PreflightError(Exception):
    """用途：固定诊断码 + 中文消息；禁止携带路径或敏感上下文。"""

    def __init__(self, diagnostic_code: str, message: str | None = None) -> None:
        if diagnostic_code not in DIAG_EXIT:
            diagnostic_code = "internal_error"
        self.diagnostic_code = diagnostic_code
        self.message = message or _DIAG_DEFAULT_MESSAGE.get(
            diagnostic_code, MSG_INTERNAL_ERROR
        )
        super().__init__(self.message)


def message_for_code(diagnostic_code: str) -> str:
    """用途：M1 code → 固定中文。"""
    return _DIAG_DEFAULT_MESSAGE.get(diagnostic_code, MSG_INTERNAL_ERROR)


def map_helper_error(exc: HelperError) -> tuple[str, str]:
    """用途：仅按固定 MSG_* 精确映射 HelperError。"""
    msg = getattr(exc, "message", None)
    if not isinstance(msg, str):
        return "internal_error", MSG_INTERNAL_ERROR
    diag = _HELPER_MSG_TO_DIAG.get(msg)
    if diag is None:
        return "internal_error", MSG_INTERNAL_ERROR
    return diag, _DIAG_DEFAULT_MESSAGE[diag]


# ---------------------------------------------------------------------------
# 路径 / reparse
# ---------------------------------------------------------------------------


def _is_reparse_point(path: Path) -> bool:
    """用途：no-follow 检测 Windows reparse 属性；无属性平台恒 False。"""
    try:
        st = path.lstat()
    except OSError:
        return False
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    try:
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc) and bool(is_junc()):
            return True
    except OSError:
        pass
    return False


def _path_is_symlink_or_reparse(path: Path) -> bool:
    """用途：resolve 前判定字面路径是否为 symlink 或 reparse。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        pass
    return _is_reparse_point(path)


def _reject_relative_path_string(rel: str) -> None:
    """用途：拒绝绝对/UNC/URL/穿越/空等非法相对路径字面量。"""
    if not isinstance(rel, str) or not rel or not rel.strip():
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if "\x00" in rel or "\r" in rel or "\n" in rel:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    text = rel.strip()
    lower = text.lower()
    if "://" in text or lower.startswith("file:"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if text.startswith("\\\\") or text.startswith("//"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if text.startswith("/") or text.startswith("\\"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    try:
        if Path(text).is_absolute():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    normalized = text.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if not parts:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)


def resolve_under_runtime_root(runtime_root: Path, rel: str) -> Path:
    """
    用途：将相对路径解析到 runtime 根内；逃逸固定 runtime_manifest_invalid。
    二次开发：resolve 前逐组件拒绝 symlink/reparse，再 resolve 校验仍在根内。
    """
    _reject_relative_path_string(rel)
    try:
        root = runtime_root.resolve()
    except OSError as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    cursor = root
    for part in parts:
        cursor = cursor / part
        if _path_is_symlink_or_reparse(cursor):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    try:
        resolved = cursor.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    if _path_is_symlink_or_reparse(resolved):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    return resolved


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """
    用途：读取并严格校验五键 manifest；返回含 runtime 根与已解析路径的内部字典。
    """
    try:
        if manifest_path.is_symlink() or _is_reparse_point(manifest_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if not manifest_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        raw_text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc

    if not isinstance(data, dict):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if set(data.keys()) != MANIFEST_KEYS:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    schema = data.get("schemaVersion")
    if type(schema) is not int or schema != 1:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    engine = data.get("engine")
    if engine != "mineru":
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    free = data.get("requiredFreeBytes")
    if type(free) is not int or free <= 0:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    cli_rel = data.get("cliRelativePath")
    model_rel = data.get("modelMarkerRelativePath")
    if not isinstance(cli_rel, str) or not isinstance(model_rel, str):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    try:
        runtime_root = manifest_path.resolve(strict=True).parent
    except OSError as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc

    cli_path = resolve_under_runtime_root(runtime_root, cli_rel)
    model_path = resolve_under_runtime_root(runtime_root, model_rel)

    if cli_path.suffix.lower() != ".exe":
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    return {
        "runtime_root": runtime_root,
        "cli_path": cli_path,
        "model_path": model_path,
        "required_free_bytes": free,
        "cli_rel": cli_rel,
        "model_rel": model_rel,
    }


def _validate_cli_file(cli_path: Path) -> None:
    """用途：CLI 必须是根内普通非 symlink/reparse 的 .exe；缺失→cli_missing。"""
    try:
        if not cli_path.exists():
            raise PreflightError("cli_missing", MSG_CLI_MISSING)
        if cli_path.is_symlink() or _is_reparse_point(cli_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if not cli_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if cli_path.suffix.lower() != ".exe":
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError("cli_missing", MSG_CLI_MISSING) from exc


def _validate_model_marker(model_path: Path) -> None:
    """用途：marker 必须是根内普通小文件；缺失→model_missing。"""
    try:
        if not model_path.exists():
            raise PreflightError("model_missing", MSG_MODEL_MISSING)
        if model_path.is_symlink() or _is_reparse_point(model_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if model_path.is_dir() or not model_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        size = int(model_path.stat().st_size)
        if size <= 0 or size > MODEL_MARKER_MAX_BYTES:
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError("model_missing", MSG_MODEL_MISSING) from exc


def check_disk_free(runtime_root: Path, required_free_bytes: int) -> None:
    """用途：对 manifest 所在目标卷检查 available >= requiredFreeBytes。"""
    try:
        usage = shutil.disk_usage(str(runtime_root))
        free = int(usage.free)
    except Exception as exc:
        raise PreflightError("disk_insufficient", MSG_DISK_INSUFFICIENT) from exc
    if free < int(required_free_bytes):
        raise PreflightError("disk_insufficient", MSG_DISK_INSUFFICIENT)


def _file_identity(path: Path) -> tuple[int, int] | None:
    """用途：廉价 no-follow 身份指纹 (size, mtime_ns)；失败返回 None。"""
    try:
        st = path.lstat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        return int(st.st_size), mtime_ns
    except OSError:
        return None


def validate_runtime_ready(manifest_info: Mapping[str, Any]) -> dict[str, Any]:
    """
    用途：CLI + model + disk 串行门；返回可交给 runner/recheck 的 ready 对象。
    二次开发：不得正向缓存 ready；每任务重新 load+validate。
    """
    cli_path = Path(manifest_info["cli_path"])
    model_path = Path(manifest_info["model_path"])
    runtime_root = Path(manifest_info["runtime_root"])
    required = int(manifest_info["required_free_bytes"])
    _validate_cli_file(cli_path)
    _validate_model_marker(model_path)
    check_disk_free(runtime_root, required)
    cli_id = _file_identity(cli_path)
    model_id = _file_identity(model_path)
    if cli_id is None or model_id is None:
        raise PreflightError("cli_missing", MSG_CLI_MISSING)
    return {
        "ok": True,
        "diagnostic_code": "static_ready",
        "cli_path": cli_path,
        "model_path": model_path,
        "runtime_root": runtime_root,
        "required_free_bytes": required,
        "cli_identity": cli_id,
        "model_identity": model_id,
    }


def recheck_cli_and_marker(ready: Mapping[str, Any] | Any) -> bool:
    """
    用途：每文件启动前廉价 no-follow recheck；身份变化或缺失返回 False。
    二次开发：禁止抛出路径；仅 bool；调用方映射 cli_missing。
    """
    try:
        if ready is None:
            return False
        if isinstance(ready, Mapping):
            cli_path = Path(ready["cli_path"])
            model_path = Path(ready["model_path"])
            cli_id_expected = ready.get("cli_identity")
            model_id_expected = ready.get("model_identity")
        else:
            cli_path = Path(getattr(ready, "cli_path"))
            model_path = Path(getattr(ready, "model_path"))
            cli_id_expected = getattr(ready, "cli_identity", None)
            model_id_expected = getattr(ready, "model_identity", None)

        if _path_is_symlink_or_reparse(cli_path) or _path_is_symlink_or_reparse(
            model_path
        ):
            return False
        if not cli_path.is_file() or not model_path.is_file():
            return False
        if cli_path.suffix.lower() != ".exe":
            return False
        cli_id = _file_identity(cli_path)
        model_id = _file_identity(model_path)
        if cli_id is None or model_id is None:
            return False
        if cli_id_expected is not None and tuple(cli_id) != tuple(cli_id_expected):
            return False
        if model_id_expected is not None and tuple(model_id) != tuple(
            model_id_expected
        ):
            return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 单文件 runner
# ---------------------------------------------------------------------------


def _terminate_then_kill(proc: Any) -> None:
    """用途：terminate → wait → 仍存活则 kill（契约：无条件链）。"""
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=float(POLL_INTERVAL_SEC))
    except Exception:
        pass
    still_alive = False
    try:
        if proc.poll() is None:
            still_alive = True
        elif getattr(proc, "returncode", None) is None and getattr(
            proc, "_alive", None
        ):
            still_alive = True
    except Exception:
        still_alive = True
    # 测试 FakePopen 在 terminate 后仍 _alive=True：无条件 kill
    try:
        if still_alive or getattr(proc, "_alive", False) is True:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=float(POLL_INTERVAL_SEC))
            except Exception:
                pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cleanup_temp_root(root: Path | None) -> None:
    """用途：成功/失败/超时/取消均清理单次 TEMP。"""
    if root is None:
        return
    try:
        if root.exists():
            shutil.rmtree(root, ignore_errors=False)
    except Exception:
        try:
            shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass


def parse_one_file_with_manifest_cli(
    ready: Any,
    input_path: Path,
    *,
    task_deadline: float,
    cancel_check: Callable[[], bool],
) -> str:
    """
    用途：用 ready 中固定 CLI 解析单文件；轮询取消/超时；返回唯一 Markdown。
    参数：ready/input_path 位置或关键字；task_deadline/cancel_check 仅关键字且必填。
    异常：PreflightError，diagnostic_code/message 固定，str(exc)==message。
    """
    if ready is None:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if not callable(cancel_check):
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)

    if isinstance(ready, Mapping):
        cli_path = Path(ready["cli_path"])
    else:
        try:
            cli_path = Path(getattr(ready, "cli_path"))
        except Exception as exc:
            raise PreflightError("internal_error", MSG_INTERNAL_ERROR) from exc

    src = Path(input_path)
    temp_root: Path | None = None
    proc: Any = None
    file_start = time.monotonic()
    file_deadline = file_start + float(FILE_TIMEOUT_SEC)

    try:
        # 输入存在性（不跟随链路外解析业务路径；runner 信任调用方已 gate）
        try:
            if not src.is_file():
                raise PreflightError("parser_failed", MSG_PARSER_FAILED)
        except PreflightError:
            raise
        except OSError as exc:
            raise PreflightError("parser_failed", MSG_PARSER_FAILED) from exc

        temp_root = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))  # 经 core.tempfile 门面
        # cwd 与可写根绑定 TEMP 根（测试断言 cwd == mkdtemp 根）
        out_dir = temp_root
        cmd = build_mineru_command(str(cli_path), src, out_dir)
        env = build_mineru_env(out_dir, os.environ)
        # 强制剥离敏感键（即使白名单漏网）
        for banned in (
            "BIAOSHU_MANAGED_OCR_MANIFEST",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env.pop(banned, None)

        try:
            proc = subprocess.Popen(
                cmd,
                shell=False,
                env=env,
                cwd=str(out_dir.resolve()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise PreflightError("parser_failed", MSG_PARSER_FAILED) from exc

        while True:
            # 先 poll 再判超时，保证 file-timeout 路径至少 2 次 poll 证据
            try:
                rc = proc.poll()
            except Exception as exc:
                _terminate_then_kill(proc)
                raise PreflightError("parser_failed", MSG_PARSER_FAILED) from exc

            if cancel_check():
                _terminate_then_kill(proc)
                raise PreflightError("interrupted", MSG_INTERRUPTED)

            if rc is not None:
                if int(rc) != 0:
                    raise PreflightError("parser_failed", MSG_PARSER_FAILED)
                break

            now = time.monotonic()
            if now >= float(task_deadline) or now >= file_deadline:
                _terminate_then_kill(proc)
                raise PreflightError("parser_timeout", MSG_PARSER_TIMEOUT)

            # 生产 poll wait：sleep 传入真实 POLL_INTERVAL_SEC（0 < x <= 1）
            time.sleep(float(POLL_INTERVAL_SEC))

        try:
            markdown = find_and_read_markdown(out_dir)
        except HelperError as exc:
            diag, msg = map_helper_error(exc)
            raise PreflightError(diag, msg) from exc
        except Exception as exc:
            raise PreflightError("output_invalid", MSG_OUTPUT_INVALID) from exc

        if not isinstance(markdown, str) or not markdown:
            raise PreflightError("output_invalid", MSG_OUTPUT_INVALID)
        return markdown

    except PreflightError:
        raise
    except HelperError as exc:
        diag, msg = map_helper_error(exc)
        raise PreflightError(diag, msg) from exc
    except Exception as exc:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR) from exc
    finally:
        if proc is not None:
            try:
                if proc.poll() is None:
                    _terminate_then_kill(proc)
            except Exception:
                pass
        _cleanup_temp_root(temp_root)
