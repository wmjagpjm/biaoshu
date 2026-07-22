# -*- coding: utf-8 -*-
"""
模块：V1-M 管理式 OCR 后端 adapter
用途：按仓库根固定相对路径加载 pure core；path-only manifest；进程内并发 1；聚合 Markdown。
对接：task_service._run_parse（engine=managed）；tools/local-parser/managed_ocr_runtime_core.py。
二次开发：禁止 env 指定 core 路径；客户端零路径；异常边界固定 M1 诊断码与中文。
"""

from __future__ import annotations

import importlib.util
import stat as stat_mod
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

# 仓库根：backend/app/services → parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CORE_REL = Path("tools") / "local-parser" / "managed_ocr_runtime_core.py"
_CORE_PATH = (_REPO_ROOT / _CORE_REL).resolve()

TASK_TIMEOUT_SEC: float = 7200.0
SOURCE_SEPARATOR = "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
MAX_MARKDOWN_CODEPOINTS = 1_000_000
MAX_MARKDOWN_UTF8_BYTES = 2 * 1024 * 1024
# Windows reparse 属性位；与 pure core 保持一致
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

MANAGED_SEMAPHORE = threading.BoundedSemaphore(1)

_core_module: Any | None = None


@dataclass(frozen=True)
class ManagedSource:
    """用途：不可变 source 描述；path 为服务端已解析普通文件路径。"""

    path: Path
    filename: str
    expected_size: int


@dataclass(frozen=True)
class ManagedParseOutput:
    """用途：不可变成功输出；chars 为 Unicode 码点数。"""

    markdown: str
    file_count: int
    chars: int


def _load_core() -> Any:
    """用途：仅按固定仓库相对路径加载 pure core；禁止 env 覆盖。"""
    global _core_module
    if _core_module is not None:
        return _core_module
    if not _CORE_PATH.is_file():
        raise RuntimeError("managed_ocr_runtime_core 缺失")
    # 保证 helper 同目录可导入
    core_dir = str(_CORE_PATH.parent)
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    unique = f"managed_ocr_runtime_core_{id(_CORE_PATH)}"
    spec = importlib.util.spec_from_file_location(unique, _CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("managed_ocr_runtime_core 无法加载")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    # 同时以规范名注册，便于测试 patch core 属性
    sys.modules["managed_ocr_runtime_core"] = mod
    spec.loader.exec_module(mod)
    _core_module = mod
    return mod


def get_core_module() -> Any:
    """用途：返回 service 实际绑定的 core 模块（__file__ 必须等于固定 CORE_PATH）。"""
    return _load_core()


# 模块级只读绑定（测试可识别）
core = get_core_module()


def _raise_diag(core_mod: Any, code: str) -> None:
    """用途：抛出 core.PreflightError，message 固定 M1 中文。"""
    err_cls = getattr(core_mod, "PreflightError")
    msg_fn = getattr(core_mod, "message_for_code", None)
    if callable(msg_fn):
        message = msg_fn(code)
    else:
        message = code
    raise err_cls(code, message)


def _path_is_symlink_or_reparse(path: Path) -> bool:
    """用途：字面 no-follow 判定 symlink/reparse（含 Windows reparse 属性）。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc) and bool(is_junc()):
            return True
    except OSError:
        return True
    try:
        st = path.lstat()
    except OSError:
        return True
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def _assert_source_literal_before_run(core_mod: Any, src: ManagedSource) -> None:
    """
    用途：每次 run_one 紧前对 src.path 做字面 no-follow 完整性检查。
    规则：leaf 与 immediate parent 均非 symlink/reparse；lstat 为普通文件；
      st_size 精确等于 expected_size；expected_size 必须严格 int（非 bool）且 >=0。
    失败：parser_failed + M1 固定中文；不回显 path/filename/size。
    """
    expected = src.expected_size
    # bool 是 int 子类，必须用 type(...) is int 排除 True/False
    if type(expected) is not int or expected < 0:
        _raise_diag(core_mod, "parser_failed")

    leaf = Path(src.path)
    parent = leaf.parent
    if _path_is_symlink_or_reparse(leaf) or _path_is_symlink_or_reparse(parent):
        _raise_diag(core_mod, "parser_failed")

    try:
        st = leaf.lstat()
    except OSError:
        _raise_diag(core_mod, "parser_failed")
        return  # 供类型检查器；_raise_diag 必抛

    if not stat_mod.S_ISREG(int(st.st_mode)):
        _raise_diag(core_mod, "parser_failed")
    if int(st.st_size) != expected:
        _raise_diag(core_mod, "parser_failed")


def _acquire_managed_slot(
    core_mod: Any,
    *,
    cancel_check: Callable[[], bool],
    task_deadline: float,
) -> None:
    """
    用途：有界等待进程内信号量；轮询间隔 <= POLL_INTERVAL_SEC（且 <=1s）。
    每轮先/后检查 cancel 与任务总时限；取消→interrupted；超时→parser_timeout。
    """
    poll = float(getattr(core_mod, "POLL_INTERVAL_SEC", 1.0) or 1.0)
    if poll <= 0.0 or poll > 1.0:
        poll = 1.0

    while True:
        if cancel_check():
            _raise_diag(core_mod, "interrupted")
        remaining = float(task_deadline) - time.monotonic()
        if remaining <= 0.0:
            _raise_diag(core_mod, "parser_timeout")
        timeout = min(poll, remaining)
        got = MANAGED_SEMAPHORE.acquire(timeout=timeout)
        if got:
            # 取锁后立即再检一次，避免窗口内已取消/已超时
            if cancel_check():
                MANAGED_SEMAPHORE.release()
                _raise_diag(core_mod, "interrupted")
            if time.monotonic() >= float(task_deadline):
                MANAGED_SEMAPHORE.release()
                _raise_diag(core_mod, "parser_timeout")
            return


def run_managed_parse(
    sources: Sequence[ManagedSource],
    *,
    manifest_path: Path | str,
    cancel_check: Callable[[], bool],
) -> ManagedParseOutput:
    """
    用途：顺序解析全部 source；每任务 ready 一次、每文件 recheck；进程内锁 1。
    参数：sources 位置参数；manifest_path/cancel_check 仅关键字且必填、无默认。
    """
    if not callable(cancel_check):
        core_mod = _load_core()
        _raise_diag(core_mod, "argument_invalid")

    core_mod = _load_core()
    err_cls = getattr(core_mod, "PreflightError")
    load_manifest = getattr(core_mod, "load_manifest")
    validate_ready = getattr(core_mod, "validate_runtime_ready")
    recheck = getattr(core_mod, "recheck_cli_and_marker")
    run_one = getattr(core_mod, "parse_one_file_with_manifest_cli")

    src_list = list(sources)
    if not src_list:
        _raise_diag(core_mod, "argument_invalid")

    # P1：任务墙钟必须在任何 acquire 前建立；锁等待与文件循环共享同一 deadline
    task_start = time.monotonic()
    task_deadline = task_start + float(TASK_TIMEOUT_SEC)

    acquired = False
    try:
        _acquire_managed_slot(
            core_mod,
            cancel_check=cancel_check,
            task_deadline=task_deadline,
        )
        acquired = True

        try:
            info = load_manifest(Path(manifest_path))
            ready = validate_ready(info)
        except err_cls:
            raise
        except Exception as exc:
            raise err_cls("runtime_manifest_invalid") from exc

        parts: list[str] = []
        for src in src_list:
            if cancel_check():
                _raise_diag(core_mod, "interrupted")
            now = time.monotonic()
            if now >= task_deadline:
                _raise_diag(core_mod, "parser_timeout")

            if not recheck(ready):
                _raise_diag(core_mod, "cli_missing")

            # P4：parser 调用前每文件一次字面 no-follow 完整性检查
            _assert_source_literal_before_run(core_mod, src)

            try:
                md = run_one(
                    ready,
                    Path(src.path),
                    task_deadline=task_deadline,
                    cancel_check=cancel_check,
                )
            except err_cls:
                raise
            except Exception as exc:
                raise err_cls("internal_error") from exc

            if not isinstance(md, str):
                _raise_diag(core_mod, "output_invalid")
            parts.append(md)

            # 单文件后再次检查任务墙钟（两文件第二 runner 不得调用）
            if time.monotonic() >= task_deadline:
                _raise_diag(core_mod, "parser_timeout")

        if len(parts) == 1:
            combined = parts[0]
        else:
            combined = SOURCE_SEPARATOR.join(parts)

        if len(combined) > MAX_MARKDOWN_CODEPOINTS:
            _raise_diag(core_mod, "output_invalid")
        if len(combined.encode("utf-8")) > MAX_MARKDOWN_UTF8_BYTES:
            _raise_diag(core_mod, "output_invalid")

        return ManagedParseOutput(
            markdown=combined,
            file_count=len(parts),
            chars=len(combined),
        )
    finally:
        if acquired:
            MANAGED_SEMAPHORE.release()
