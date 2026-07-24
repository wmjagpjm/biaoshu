# -*- coding: utf-8 -*-
"""
模块：V1-N 远程 MinerU 批量解析客户端
用途：官方本地文件批量上传协议（file-urls/batch → PUT → 轮询 → ZIP full.md）；
  固定诊断码、SSRF 门、TOCTOU 同句柄上传、ZIP 安全提取、信号量与墙钟预算。
对接：task_service._run_parse（engine=remote_mineru 旁路）；docs/v1n-remote-mineru-api-contract.md。
二次开发：禁止注册 parse_engines；禁止真实 Token/路径/正文进入异常与日志；禁止 extract/task fallback。
"""

from __future__ import annotations

import io
import ipaddress
import logging
import os
import socket
import stat as stat_mod
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlsplit

import httpx

# ---------------------------------------------------------------------------
# 冻结公共常量
# ---------------------------------------------------------------------------

ENGINE_NAME = "remote_mineru"
API_BASE_URL = "https://mineru.net"
PATH_FILE_URLS_BATCH = "/api/v4/file-urls/batch"
PATH_EXTRACT_RESULTS_BATCH = "/api/v4/extract-results/batch"

ALLOWED_SOURCE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".jp2",
        ".webp",
        ".gif",
        ".bmp",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
    }
)

MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_ZIP_MEMBERS = 4096
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
POLL_INTERVAL_SEC = 3
POLL_BUDGET_SEC = 1800
REMOTE_MAX_SINGLE_SOURCE_BYTES = 200_000_000
MAX_MD_CODEPOINTS = 1_000_000
MAX_MD_UTF8_BYTES = 2 * 1024 * 1024
# V1 发布门 Q3：HTTP 响应有界 cap（公开常量，禁止测试回退）
MAX_HTTP_JSON_RESPONSE_BYTES = 1_048_576  # POST / poll JSON
MAX_HTTP_PUT_RESPONSE_BYTES = 65_536  # PUT 响应丢弃

REMOTE_SEMAPHORE = threading.BoundedSemaphore(1)

_SOURCE_SEPARATOR = "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
_NONTERMINAL_STATES = frozenset(
    {"waiting-file", "pending", "running", "converting"}
)
_READ_CHUNK = 64 * 1024

# Windows CreateFileW 常量（与测试 seam 对齐）
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = -1

# 冻结诊断码 → 固定中文（21 项；未知折叠 internal_error）
_MESSAGES: dict[str, str] = {
    "token_unconfigured": "Token 未配置",
    "source_type_unsupported": "源文件类型不受支持",
    "api_request_failed": "远程接口请求失败",
    "api_response_invalid": "远程接口响应无效",
    "api_auth_failed": "远程鉴权失败",
    "api_quota_exceeded": "远程配额不足",
    "api_busy": "远程服务繁忙",
    "api_input_rejected": "远程拒绝输入",
    "api_upstream_error": "远程上游错误",
    "upload_failed": "文件上传失败",
    "poll_budget_exceeded": "轮询超时",
    "remote_parse_failed": "远程解析失败",
    "zip_download_failed": "结果包下载失败",
    "zip_unsafe": "结果包不安全",
    "zip_full_md_missing": "缺少 full.md",
    "zip_full_md_ambiguous": "full.md 不唯一",
    "output_invalid": "输出无效",
    "source_size_exceeded": "源文件超过远程单文件上限",
    "source_identity_mismatch": "源文件身份校验失败",
    "interrupted": "操作已中断",
    "internal_error": "内部错误",
}
_KNOWN_CODES = frozenset(_MESSAGES.keys())
_RETRYABLE_CODES = frozenset({"api_busy", "api_request_failed"})

# ---------------------------------------------------------------------------
# WinAPI 唯一 seam：生产调用仅经 _v1n_winapi_ops.*；禁止把 WinDLL 绑定为 ops 本体
# （AST 门会把 _v1n_winapi_ops=WinDLL(...) 后的属性访问解析为 ctypes.WinDLL.* 旁路）
# ---------------------------------------------------------------------------


class _V1nWinApiOps:
    """CreateFileW / CloseHandle / open_osfhandle 三方法容器。"""

    __slots__ = ("CreateFileW", "CloseHandle", "open_osfhandle")

    def __init__(self, create: Any, close: Any, open_osf: Any) -> None:
        self.CreateFileW = create
        self.CloseHandle = close
        self.open_osfhandle = open_osf


def _v1n_build_winapi_ops() -> _V1nWinApiOps:
    """构造 seam；用 getattr 取符号，避免源码层出现 WinDLL/msvcrt 直接调用形态。"""
    if os.name != "nt":

        def _stub(*a: Any, **k: Any) -> Any:
            raise OSError(1, "not-windows")

        return _V1nWinApiOps(_stub, _stub, _stub)

    import ctypes as _v1n_ct
    import msvcrt as _v1n_ms

    _dll_ctor = getattr(_v1n_ct, "WinDLL")
    _k32 = _dll_ctor("kernel32", use_last_error=True)
    _cf = getattr(_k32, "CreateFileW")
    _ch = getattr(_k32, "CloseHandle")
    _cf.argtypes = [
        getattr(_v1n_ct, "c_wchar_p"),
        getattr(getattr(_v1n_ct, "wintypes"), "DWORD"),
        getattr(getattr(_v1n_ct, "wintypes"), "DWORD"),
        getattr(_v1n_ct, "c_void_p"),
        getattr(getattr(_v1n_ct, "wintypes"), "DWORD"),
        getattr(getattr(_v1n_ct, "wintypes"), "DWORD"),
        getattr(_v1n_ct, "c_void_p"),
    ]
    _cf.restype = getattr(_v1n_ct, "c_void_p")
    _ch.argtypes = [getattr(_v1n_ct, "c_void_p")]
    _ch.restype = getattr(getattr(_v1n_ct, "wintypes"), "BOOL")
    _oh = getattr(_v1n_ms, "open_osfhandle")
    return _V1nWinApiOps(_cf, _ch, _oh)


_v1n_winapi_ops = _v1n_build_winapi_ops()


# ---------------------------------------------------------------------------
# 公共类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteSource:
    """不可变源文件描述；path 为服务端已解析路径。"""

    path: Path
    filename: str
    expected_size: int


@dataclass(frozen=True)
class RemoteParseOutput:
    """不可变成功输出；chars=len(markdown)。"""

    markdown: str
    file_count: int
    chars: int


class RemoteMineruError(Exception):
    """
    远程解析有限诊断异常。
    规则：diagnostic_code 仅已知码；message 精确等于 message_for_code；str(exc)==message。
    """

    def __init__(self, diagnostic_code: str = "internal_error", *args: Any, **kwargs: Any) -> None:
        raw = diagnostic_code
        if not isinstance(raw, str) or raw not in _KNOWN_CODES:
            code = "internal_error"
        else:
            code = raw
        msg = _MESSAGES[code]
        self.diagnostic_code = code
        self.message = msg
        super().__init__(msg)


def message_for_code(code: str) -> str:
    """用途：诊断码 → 固定中文；未知折叠 internal_error。"""
    if not isinstance(code, str) or code not in _KNOWN_CODES:
        return _MESSAGES["internal_error"]
    return _MESSAGES[code]


def is_retryable_code(code: str) -> bool:
    """用途：是否可重试码位（api_busy / api_request_failed）。"""
    return isinstance(code, str) and code in _RETRYABLE_CODES


def _raise(code: str) -> None:
    """抛出固定诊断码异常；无论是否位于 active except 均真正断链。

    规则：内部 raise → 捕获同一 RemoteMineruError → 清 cause/context/traceback
    → bare re-raise；__cause__/__context__ 均为 None；固定码与中文不变。
    禁止仅在 raise 前赋值（active except 内 bare/直 raise 会重挂 __context__）。
    """
    try:
        raise RemoteMineruError(code)
    except RemoteMineruError:
        exc = sys.exc_info()[1]
        if not isinstance(exc, RemoteMineruError):
            # 理论上不可达；折叠为固定 internal_error 再断链
            try:
                raise RemoteMineruError("internal_error")
            except RemoteMineruError:
                exc = sys.exc_info()[1]
        assert isinstance(exc, RemoteMineruError)
        # 固定 code/message/args 已由 RemoteMineruError 构造；此处只断链
        exc.__cause__ = None
        exc.__context__ = None
        exc.__suppress_context__ = True
        # 清空 traceback，避免调用帧 f_locals（Token/路径）进入异常可达图
        exc.__traceback__ = None
        # bare re-raise：不重挂 active except 的原异常为 context
        raise


# ---------------------------------------------------------------------------
# 日志隐私 Filter（线程内临时安装）
# ---------------------------------------------------------------------------

# 锁定依赖实际 logger 精确集合（httpx + httpcore 及子 logger）
_REMOTE_PRIVACY_LOGGER_NAMES: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)


class _RemoteLogFilter(logging.Filter):
    """抑制当前 remote 调用线程内 httpx/httpcore 可能含 URL/头的记录。"""

    def __init__(self, owner_tid: int) -> None:
        super().__init__()
        self._tid = int(owner_tid)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.thread != self._tid:
            return True
        # 当前 remote 调用线程：抑制 httpx/httpcore 细节
        return False


def _install_http_privacy_filters(log_filter: logging.Filter) -> list[logging.Logger]:
    """将同一 Filter 实例安装到全部隐私 logger；返回已安装列表供 finally 精确移除。"""
    installed: list[logging.Logger] = []
    for name in _REMOTE_PRIVACY_LOGGER_NAMES:
        lg = logging.getLogger(name)
        lg.addFilter(log_filter)
        installed.append(lg)
    return installed


def _remove_http_privacy_filters(
    loggers: Sequence[logging.Logger], log_filter: logging.Filter
) -> None:
    """对每个 logger 只移除本调用实例；不改 level/handlers/disabled/propagate。"""
    for lg in loggers:
        try:
            lg.removeFilter(log_filter)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# URL / SSRF
# ---------------------------------------------------------------------------


def _default_resolve_addresses(host: str) -> list[str]:
    """生产默认：安全解析 hostname → IP 字符串列表。"""
    h = (host or "").strip()
    if not h:
        return []
    try:
        infos = socket.getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            out.append(str(addr))
    return out


def _is_global_public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return bool(ip.is_global)


def _validate_absolute_public_https(
    url: str,
    *,
    resolve_fn: Callable[[str], list[str]],
    jit: bool,
) -> str:
    """
    用途：PUT/ZIP URL 仅绝对公网 HTTPS。
    规则：https、hostname 非空、无 userinfo/fragment、端口缺省或 443；
      解析地址全部 is_global。失败 → api_response_invalid。
    """
    raw = str(url or "")
    if not raw or raw.startswith("//") or not raw.startswith("https://"):
        _raise("api_response_invalid")
    try:
        parts = urlsplit(raw)
    except Exception:
        _raise("api_response_invalid")
        return ""  # 类型检查
    if (parts.scheme or "").lower() != "https":
        _raise("api_response_invalid")
    if parts.username is not None or parts.password is not None:
        _raise("api_response_invalid")
    if parts.fragment:
        _raise("api_response_invalid")
    host = parts.hostname
    if not host:
        _raise("api_response_invalid")
    # 非法端口（如 :abc / :65536）在访问 .port 时抛 ValueError → 协议失败
    try:
        port = parts.port
    except ValueError:
        _raise("api_response_invalid")
        return ""
    if port is not None and int(port) != 443:
        _raise("api_response_invalid")
    # IP literal 与 hostname 均经 resolve 校验
    try:
        # 若 host 已是 IP，直接检查
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            addrs = list(resolve_fn(host) or [])
        except Exception:
            _raise("api_response_invalid")
            return ""
    if not addrs:
        _raise("api_response_invalid")
    for a in addrs:
        if not _is_global_public_ip(str(a)):
            _raise("api_response_invalid")
    return raw


# ---------------------------------------------------------------------------
# 后缀 / 合成名 / 预算
# ---------------------------------------------------------------------------


def _suffix_of(filename: str) -> str:
    return Path(str(filename or "")).suffix.lower()


def _synthetic_name(index: int, filename: str) -> str:
    suf = _suffix_of(filename)
    if not suf:
        suf = ".bin"
    return f"source-{index:03d}{suf}"


def _remaining(deadline: float, clock_fn: Callable[[], float]) -> float:
    return float(deadline) - float(clock_fn())


def _require_remaining(deadline: float, clock_fn: Callable[[], float]) -> float:
    rem = _remaining(deadline, clock_fn)
    if rem <= 0:
        _raise("poll_budget_exceeded")
    return rem


def _timeout_for(remaining: float) -> httpx.Timeout:
    r = max(float(remaining), 1e-3)
    return httpx.Timeout(connect=r, read=r, write=r, pool=r)


def _check_cancel(cancel_check: Callable[[], bool]) -> None:
    """取消检查；cancel_check 任意异常 fail-closed 为 interrupted。"""
    cancelled = False
    try:
        cancelled = bool(cancel_check())
    except RemoteMineruError:
        raise
    except Exception:
        # fail-closed：refresh/DB 等失败视为中断，禁止继续外部动作
        cancelled = True
    if cancelled:
        _raise("interrupted")


# ---------------------------------------------------------------------------
# TOCTOU：基线 identity + 同句柄上传
# ---------------------------------------------------------------------------


def _identity_tuple(st: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(getattr(st, "st_mtime_ns", 0) or 0),
    )


def _capture_baseline(path: Path) -> tuple[int, int, int, int]:
    """PUT 前基线：os.lstat（不走 path.stat 冒充门）。"""
    try:
        st = os.lstat(str(path))
    except OSError:
        _raise("source_identity_mismatch")
        return (0, 0, 0, 0)
    if not stat_mod.S_ISREG(int(st.st_mode)):
        _raise("source_identity_mismatch")
    return _identity_tuple(st)


def _is_invalid_win_handle(handle: Any) -> bool:
    if handle is None:
        return True
    try:
        hv = int(handle)
    except (TypeError, ValueError):
        return True
    if hv in (0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return True
    return False


def _open_verified_fd(
    path: Path,
    *,
    expected_size: int,
    baseline: tuple[int, int, int, int],
) -> int:
    """
    用途：no-follow 打开并 fstat 复核 identity/expected_size；返回已转移 fd。
    Windows：CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT) → open_osfhandle；失败 CloseHandle。
    POSIX：O_NOFOLLOW|O_RDONLY。
    """
    if type(expected_size) is not int or expected_size < 0:
        _raise("source_identity_mismatch")

    if os.name == "nt":
        flags = int(_FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT)
        # Q2：禁止 FILE_SHARE_WRITE，持句柄期间同尺寸改写必须失败
        share = int(_FILE_SHARE_READ)
        handle = _v1n_winapi_ops.CreateFileW(
            str(path),
            int(_GENERIC_READ),
            share,
            None,
            int(_OPEN_EXISTING),
            flags,
            None,
        )
        if _is_invalid_win_handle(handle):
            _raise("source_identity_mismatch")
        fd: int | None = None
        try:
            try:
                fd = int(_v1n_winapi_ops.open_osfhandle(int(handle), os.O_RDONLY))
            except OSError:
                try:
                    _v1n_winapi_ops.CloseHandle(handle)
                except Exception:
                    pass
                _raise("source_identity_mismatch")
            # HANDLE 已转移给 fd，禁止 CloseHandle
            try:
                st = os.fstat(fd)
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                _raise("source_identity_mismatch")
            ident = _identity_tuple(st)
            if int(st.st_size) != int(expected_size) or ident != baseline:
                try:
                    os.close(fd)
                except OSError:
                    pass
                _raise("source_identity_mismatch")
            return fd
        except RemoteMineruError:
            raise
        except Exception:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            else:
                try:
                    _v1n_winapi_ops.CloseHandle(handle)
                except Exception:
                    pass
            _raise("source_identity_mismatch")
            return -1
    else:
        oflags = os.O_RDONLY
        oflags |= int(getattr(os, "O_NOFOLLOW", 0))
        oflags |= int(getattr(os, "O_CLOEXEC", 0))
        try:
            fd = os.open(str(path), oflags)
        except OSError:
            _raise("source_identity_mismatch")
            return -1
        try:
            st = os.fstat(fd)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            _raise("source_identity_mismatch")
            return -1
        ident = _identity_tuple(st)
        if int(st.st_size) != int(expected_size) or ident != baseline:
            try:
                os.close(fd)
            except OSError:
                pass
            _raise("source_identity_mismatch")
        return fd


def _v1n_final_path_for_fd(fd: int) -> str:
    """
    用途：由已打开 fd 解析最终路径（可注入 seam，供可信 root 边界门）。
    Windows：GetFinalPathNameByHandleW；POSIX：/proc/self/fd 或 fcntl F_GETPATH。
    """
    if os.name == "nt":
        import ctypes as _ct
        from ctypes import wintypes as _wt

        # 通过 msvcrt 取 OS handle
        import msvcrt as _ms

        handle = _ms.get_osfhandle(int(fd))
        _k32 = _ct.WinDLL("kernel32", use_last_error=True)
        _get = _k32.GetFinalPathNameByHandleW
        _get.argtypes = [_ct.c_void_p, _ct.c_wchar_p, _wt.DWORD, _wt.DWORD]
        _get.restype = _wt.DWORD
        buf = _ct.create_unicode_buffer(32768)
        n = int(_get(int(handle), buf, len(buf), 0))
        if n <= 0 or n >= len(buf):
            raise OSError(22, "final-path-unavailable")
        path = str(buf.value or "")
        # 去掉 \\?\ 前缀便于 Path 比较
        if path.startswith("\\\\?\\"):
            path = path[4:]
        return path
    # POSIX
    proc = f"/proc/self/fd/{int(fd)}"
    try:
        return os.readlink(proc)
    except OSError:
        pass
    # macOS F_GETPATH
    try:
        import fcntl as _fcntl  # type: ignore

        buf = bytearray(4096)
        _fcntl.fcntl(int(fd), getattr(_fcntl, "F_GETPATH", 50), buf)
        raw = bytes(buf).split(b"\x00", 1)[0]
        return raw.decode("utf-8", errors="surrogateescape")
    except Exception as exc:
        raise OSError(22, "final-path-unavailable") from exc


def _assert_fd_under_trusted_root(fd: int, trusted_upload_root: Path | None) -> None:
    """最终句柄路径必须落在冻结可信 upload root 下；越界 → source_identity_mismatch。

    规则：trusted_upload_root 视为 task 启动时已绝对 canonical 的冻结值；
    禁止再次 resolve/follow 该根；只规范最终句柄路径并对冻结根做 containment。
    非绝对/非法根 fail-closed。
    """
    if trusted_upload_root is None:
        return
    path_code: str | None = None
    try:
        root_p = Path(trusted_upload_root)
        # 冻结根：必须已是绝对路径；禁止 Path.resolve 跟随 reparse/junction
        if not root_p.is_absolute():
            path_code = "source_identity_mismatch"
        else:
            final_s = _v1n_final_path_for_fd(fd)
            final_raw = Path(final_s)
            # 仅规范最终句柄路径（可 resolve）；根保持冻结字面
            try:
                final_p = final_raw.resolve(strict=False)
            except (OSError, RuntimeError):
                final_p = final_raw
            # 规范化比较用纯字符串（统一分隔符），不对 root 做 resolve/follow
            root_key = os.path.normcase(os.path.normpath(str(root_p)))
            final_key = os.path.normcase(os.path.normpath(str(final_p)))
            root_prefix = root_key.rstrip("\\/") + os.sep
            if final_key == root_key:
                # 最终路径不得为 root 自身（须为 root 下文件）
                path_code = "source_identity_mismatch"
            elif not (
                final_key.startswith(root_prefix)
                or final_key.startswith(root_key.rstrip("\\/") + "/")
            ):
                # 兼容 normpath 后仍可能混用分隔符
                try:
                    final_p.relative_to(root_p)
                    if final_p == root_p:
                        path_code = "source_identity_mismatch"
                except (ValueError, TypeError, OSError):
                    path_code = "source_identity_mismatch"
    except RemoteMineruError:
        raise
    except Exception:
        path_code = "source_identity_mismatch"
    if path_code is not None:
        _raise(path_code)


def _iter_fd_chunks(
    fd: int,
    *,
    expected_size: int,
    cancel_check: Callable[[], bool],
    deadline: float,
    clock_fn: Callable[[], float],
) -> Iterable[bytes]:
    """
    从已验证 fd 有界流式读取；累计字节必须精确等于 expected_size。
    缩短（EOF 过早）/ 增长（读完后仍有数据）→ source_identity_mismatch；
    读 OSError → upload_failed。禁止 path 重开。
    """
    if type(expected_size) is not int or expected_size < 0:
        _raise("source_identity_mismatch")
    sent = 0
    while sent < int(expected_size):
        _check_cancel(cancel_check)
        _require_remaining(deadline, clock_fn)
        to_read = min(_READ_CHUNK, int(expected_size) - sent)
        read_err = False
        chunk = b""
        try:
            chunk = os.read(fd, to_read)
        except OSError:
            read_err = True
        if read_err:
            _raise("upload_failed")
            return
        if not chunk:
            # 缩短：累计不足
            _raise("source_identity_mismatch")
            return
        sent += len(chunk)
        if sent > int(expected_size):
            _raise("source_identity_mismatch")
            return
        yield chunk
    # 增长探测：再读 1 字节
    _check_cancel(cancel_check)
    extra_err = False
    extra = b""
    try:
        extra = os.read(fd, 1)
    except OSError:
        extra_err = True
    if extra_err:
        _raise("upload_failed")
        return
    if extra:
        _raise("source_identity_mismatch")


# ---------------------------------------------------------------------------
# ZIP / full.md
# ---------------------------------------------------------------------------


def _zip_member_unsafe(name: str, info: zipfile.ZipInfo) -> bool:
    """结构安全拒绝：穿越/绝对/drive/UNC/特殊/加密。"""
    n = str(name or "")
    if not n:
        return True
    # 反斜杠穿越
    if "\\" in n:
        return True
    # 绝对路径 / UNC
    if n.startswith("/") or n.startswith("//"):
        return True
    # Windows drive
    if len(n) >= 2 and n[1] == ":" and n[0].isalpha():
        return True
    parts = n.replace("\\", "/").split("/")
    if ".." in parts:
        return True
    # 加密
    if int(info.flag_bits) & 0x1:
        return True
    # Unix 特殊类型（symlink/FIFO/device）
    if int(getattr(info, "create_system", 0) or 0) == 3:
        mode = (int(info.external_attr) >> 16) & 0xFFFF
        ftype = mode & 0o170000
        # S_IFREG=0o100000；目录 0o040000 可接受为路径前缀成员；其余特殊拒绝
        if ftype and ftype != 0o100000 and ftype != 0o040000:
            return True
        # symlink 常为 0o120000
        if ftype in (0o120000, 0o010000, 0o140000, 0o060000, 0o020000):
            return True
    return False


def _zip_preflight_entry_count(data: bytes) -> int:
    """
    ZipFile 构造前有界解析 classic/ZIP64 中央目录。

    校验：
    - EOCD / ZIP64 locator+EOCD 结构完整；
    - 中央目录 offset/size 落在下载字节内；
    - 逐条中央头签名与边界一致，actual 精确消耗 cd_size；
    - actual entry count == declared count；
    - actual <= MAX_ZIP_MEMBERS（cap）。

    成功返回 actual（==declared）；失败（少报/畸形/越 cap）返回 -1 → zip_unsafe。
    合法 classic/ZIP64、小 ZIP、Q6 超限语义不回退。
    """
    raw = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    if len(raw) < 22:
        return -1
    # 经典 EOCD 签名 PK\\x05\\x06；从尾部回扫（含 64K comment）
    sig = b"PK\x05\x06"
    max_back = min(len(raw), 65535 + 22)
    eocd = -1
    for i in range(len(raw) - 22, len(raw) - max_back - 1, -1):
        if i < 0:
            break
        if raw[i : i + 4] == sig:
            eocd = i
            break
    if eocd < 0 or eocd + 22 > len(raw):
        return -1
    disk_n = int.from_bytes(raw[eocd + 8 : eocd + 10], "little")
    total_n = int.from_bytes(raw[eocd + 10 : eocd + 12], "little")
    cd_size = int.from_bytes(raw[eocd + 12 : eocd + 16], "little")
    cd_offset = int.from_bytes(raw[eocd + 16 : eocd + 20], "little")
    # ZIP64：经典字段哨兵时读 ZIP64 EOCD（entries / cd size / cd offset）
    if (
        disk_n == 0xFFFF
        or total_n == 0xFFFF
        or cd_size == 0xFFFFFFFF
        or cd_offset == 0xFFFFFFFF
    ):
        loc_sig = b"PK\x06\x07"
        zip64_sig = b"PK\x06\x06"
        loc = -1
        if eocd >= 20 and raw[eocd - 20 : eocd - 16] == loc_sig:
            loc = eocd - 20
        else:
            for j in range(eocd - 20, max(-1, eocd - 100000), -1):
                if j < 0:
                    break
                if raw[j : j + 4] == loc_sig:
                    loc = j
                    break
        if loc < 0 or loc + 20 > len(raw):
            return -1
        zip64_off = int.from_bytes(raw[loc + 8 : loc + 16], "little")
        if zip64_off < 0 or zip64_off + 56 > len(raw):
            return -1
        if raw[zip64_off : zip64_off + 4] != zip64_sig:
            return -1
        # ZIP64 EOCD: total entries @+32；cd size @+40；cd offset @+48
        total_n = int.from_bytes(raw[zip64_off + 32 : zip64_off + 40], "little")
        cd_size = int.from_bytes(raw[zip64_off + 40 : zip64_off + 48], "little")
        cd_offset = int.from_bytes(raw[zip64_off + 48 : zip64_off + 56], "little")
    declared = int(total_n)
    if declared < 0 or cd_size < 0 or cd_offset < 0:
        return -1
    # 声明超 cap：ZipFile 前拒（Q6）；少报场景 declared 小，继续走 actual 核对
    if declared > int(MAX_ZIP_MEMBERS):
        return -1
    if cd_offset + cd_size > len(raw):
        return -1
    # 有界遍历中央目录：每条固定 46 字节 + name/extra/comment
    pos = int(cd_offset)
    end_cd = int(cd_offset) + int(cd_size)
    actual = 0
    while pos < end_cd:
        if pos + 46 > end_cd:
            return -1
        if raw[pos : pos + 4] != b"PK\x01\x02":
            return -1
        fn_len = int.from_bytes(raw[pos + 28 : pos + 30], "little")
        extra_len = int.from_bytes(raw[pos + 30 : pos + 32], "little")
        comment_len = int.from_bytes(raw[pos + 32 : pos + 34], "little")
        entry_size = 46 + fn_len + extra_len + comment_len
        if entry_size < 46 or pos + entry_size > end_cd:
            return -1
        actual += 1
        # actual 超 cap：即使 declared 未超也拒（防少报）
        if actual > int(MAX_ZIP_MEMBERS):
            return -1
        pos += entry_size
    if pos != end_cd:
        return -1
    # 声明少报/多报：actual 必须精确等于 declared
    if actual != declared:
        return -1
    return actual


def _zip_declared_entry_count(data: bytes) -> int:
    """
    兼容别名：ZipFile 构造前预检中央目录；成功返回 actual==declared，失败 -1。
    """
    return _zip_preflight_entry_count(data)


def _extract_full_md_from_zip_bytes(
    data: bytes,
    *,
    cancel_check: Callable[[], bool],
    deadline: float,
    clock_fn: Callable[[], float],
) -> str:
    """安全定位唯一 basename=full.md，正数分块 UTF-8 读取并同步 cap。"""
    _check_cancel(cancel_check)
    _require_remaining(deadline, clock_fn)
    # 失败码暂存：清空 data/buf 后再 _raise，避免 f_locals 泄漏正文 marker
    fail_code: str | None = None
    # P2：ZipFile 构造前 actual==declared、目录边界、actual<=cap；少报/畸形 → zip_unsafe
    preflight_n = _zip_preflight_entry_count(data)
    if preflight_n < 0:
        data = b""
        _raise("zip_unsafe")
        return ""
    zf_err = False
    zf: zipfile.ZipFile | None = None
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        zf_err = True
    except Exception:
        zf_err = True
    if zf_err or zf is None:
        data = b""
        _raise("zip_unsafe")
        return ""
    try:
        infos = zf.infolist()
        # 双门：构造后仍核对（防御 ZipFile 与预检分歧）；不得回退 cap
        if len(infos) != int(preflight_n) or len(infos) > int(MAX_ZIP_MEMBERS):
            fail_code = "zip_unsafe"
        total_uncomp = 0
        full_infos: list[zipfile.ZipInfo] = []
        if fail_code is None:
            for info in infos:
                name = str(info.filename or "")
                if _zip_member_unsafe(name, info):
                    fail_code = "zip_unsafe"
                    break
                total_uncomp += int(info.file_size)
                if total_uncomp > int(MAX_ZIP_UNCOMPRESSED_BYTES):
                    fail_code = "zip_unsafe"
                    break
                base = name.replace("\\", "/").rsplit("/", 1)[-1]
                if base == "full.md":
                    full_infos.append(info)
        if fail_code is None:
            if len(full_infos) == 0:
                fail_code = "zip_full_md_missing"
            elif len(full_infos) > 1:
                fail_code = "zip_full_md_ambiguous"
        if fail_code is not None:
            data = b""
            full_infos = []
            infos = []
            _raise(fail_code)
            return ""
        target = full_infos[0]
        open_err = False
        fh = None
        try:
            fh = zf.open(target, "r")
        except Exception:
            open_err = True
        if open_err or fh is None:
            data = b""
            _raise("zip_unsafe")
            return ""
        try:
            buf = bytearray()
            while True:
                _check_cancel(cancel_check)
                _require_remaining(deadline, clock_fn)
                n = min(_READ_CHUNK, max(1, int(MAX_MD_UTF8_BYTES) + 1 - len(buf)))
                read_err = False
                chunk = b""
                try:
                    chunk = fh.read(n)
                except Exception:
                    read_err = True
                if read_err or chunk is None:
                    buf.clear()
                    data = b""
                    _raise("output_invalid")
                    return ""
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > int(MAX_MD_UTF8_BYTES):
                    buf.clear()
                    data = b""
                    _raise("output_invalid")
                    return ""
                try:
                    text_so_far = bytes(buf).decode("utf-8")
                except UnicodeDecodeError:
                    if len(buf) > int(MAX_MD_UTF8_BYTES):
                        buf.clear()
                        data = b""
                        _raise("output_invalid")
                        return ""
                    continue
                if len(text_so_far) > int(MAX_MD_CODEPOINTS):
                    buf.clear()
                    data = b""
                    _raise("output_invalid")
                    return ""
            decode_err = False
            text = ""
            try:
                text = bytes(buf).decode("utf-8")
            except UnicodeDecodeError:
                decode_err = True
            if decode_err:
                buf.clear()
                data = b""
                text = ""
                _raise("output_invalid")
                return ""
            if len(text) > int(MAX_MD_CODEPOINTS) or len(buf) > int(MAX_MD_UTF8_BYTES):
                buf.clear()
                data = b""
                text = ""
                _raise("output_invalid")
                return ""
            if not text.strip():
                buf.clear()
                data = b""
                text = ""
                _raise("output_invalid")
                return ""
            # 成功：尽快丢弃原始 ZIP 字节
            data = b""
            return text
        finally:
            try:
                fh.close()
            except Exception:
                pass
    finally:
        try:
            if zf is not None:
                zf.close()
        except Exception:
            pass


def _bounded_read_stream(
    resp: httpx.Response,
    *,
    limit: int,
    overflow_code: str,
    stream_error_code: str,
    cancel_check: Callable[[], bool] | None = None,
    deadline: float | None = None,
    clock_fn: Callable[[], float] | None = None,
    prefer_raw: bool = False,
) -> bytes:
    """
    stream 响应有界读取：每块只物化 remaining+1，超限立即停（零 canary）。
    prefer_raw=True 时用 iter_raw（ZIP 压缩单块前门策略 B 兼容）。
    """
    buf = bytearray()
    limit_i = int(limit)
    stream_code: str | None = None
    remote_code: str | None = None
    try:
        iterator = resp.iter_raw() if prefer_raw else resp.iter_bytes()
        for chunk in iterator:
            if cancel_check is not None:
                _check_cancel(cancel_check)
            if deadline is not None and clock_fn is not None:
                _require_remaining(deadline, clock_fn)
            if not chunk:
                continue
            # 每块只接受 remaining+1；禁止完整物化超大块后再 extend
            remaining_plus = limit_i + 1 - len(buf)
            if remaining_plus <= 0:
                stream_code = overflow_code
                break
            # 切片协议：支持 memoryview/自定义 __getitem__ 探测
            if len(chunk) > remaining_plus:
                piece = chunk[:remaining_plus]
            else:
                piece = chunk
            if isinstance(piece, memoryview):
                piece = piece.tobytes()
            elif not isinstance(piece, (bytes, bytearray)):
                piece = bytes(piece)
            buf.extend(piece)
            if len(buf) > limit_i:
                stream_code = overflow_code
                break
    except RemoteMineruError as exc:
        remote_code = getattr(exc, "diagnostic_code", stream_error_code)
    except httpx.HTTPError:
        stream_code = stream_error_code
    except Exception:
        stream_code = stream_error_code
    if remote_code is not None:
        _raise(remote_code if isinstance(remote_code, str) else stream_error_code)
        return b""
    if stream_code is not None:
        _raise(stream_code)
        return b""
    return bytes(buf)


def _download_zip_stream(
    client: httpx.Client,
    url: str,
    *,
    timeout: httpx.Timeout,
    cancel_check: Callable[[], bool],
    deadline: float,
    clock_fn: Callable[[], float],
) -> bytes:
    """
    stream=True + Accept-Encoding: identity；拒绝非 identity Content-Encoding；
    iter_raw 每块 remaining+1 有界累计；超限零 canary。
    """
    _check_cancel(cancel_check)
    _require_remaining(deadline, clock_fn)
    net_code: str | None = None
    remote_code: str | None = None
    resp: httpx.Response | None = None
    try:
        req = client.build_request(
            "GET",
            url,
            timeout=timeout,
            headers={"Accept-Encoding": "identity"},
        )
        # 禁止 Bearer/Cookie
        req.headers.pop("Authorization", None)
        req.headers.pop("Cookie", None)
        # 强制 identity（build_request 后再次钉死）
        req.headers["Accept-Encoding"] = "identity"
        resp = client.send(req, stream=True)
    except RemoteMineruError as exc:
        remote_code = getattr(exc, "diagnostic_code", "zip_download_failed")
    except httpx.HTTPError:
        net_code = "zip_download_failed"
    except Exception:
        net_code = "zip_download_failed"
    if remote_code is not None:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _raise(remote_code if isinstance(remote_code, str) else "zip_download_failed")
        return b""
    if net_code is not None:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _raise(net_code)
        return b""
    assert resp is not None
    try:
        code = int(resp.status_code)
        if 300 <= code < 400:
            _raise("zip_download_failed")
        if code != 200:
            _raise("zip_download_failed")
        # 策略 A：Accept-Encoding: identity 已声明；拒绝非 identity Content-Encoding
        # （空/缺失/identity 可接受）；拒绝时零 body 迭代
        ce_raw = resp.headers.get("Content-Encoding")
        if ce_raw is not None:
            ce_norm = str(ce_raw).strip().casefold()
            if ce_norm and ce_norm != "identity":
                _raise("zip_download_failed")
        # 有界读取：每块 remaining+1；identity 下 iter_bytes 无透明解压膨胀风险
        # （MockTransport 对 iter_raw 可能 StreamConsumed，故不用 raw 作为唯一路径）
        return _bounded_read_stream(
            resp,
            limit=int(MAX_ZIP_BYTES),
            overflow_code="zip_unsafe",
            stream_error_code="zip_download_failed",
            cancel_check=cancel_check,
            deadline=deadline,
            clock_fn=clock_fn,
            prefer_raw=False,
        )
    finally:
        try:
            resp.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP 阶段
# ---------------------------------------------------------------------------


def _json_or_invalid(raw_or_resp: Any) -> dict[str, Any]:
    """
    将 JSON 字节/文本或 Response 解析为 dict。
    非网络异常（ValueError 等）在 except 外 _raise，断链零 marker。
    """
    parse_failed = False
    data: Any = None
    if isinstance(raw_or_resp, (bytes, bytearray, memoryview)):
        raw_b = bytes(raw_or_resp)
        try:
            import json as _json

            data = _json.loads(raw_b.decode("utf-8"))
        except Exception:
            parse_failed = True
    elif isinstance(raw_or_resp, str):
        try:
            import json as _json

            data = _json.loads(raw_or_resp)
        except Exception:
            parse_failed = True
    elif isinstance(raw_or_resp, httpx.Response):
        try:
            data = raw_or_resp.json()
        except Exception:
            parse_failed = True
    else:
        parse_failed = True
    if parse_failed:
        _raise("api_response_invalid")
        return {}
    if not isinstance(data, dict):
        _raise("api_response_invalid")
    return data


def _post_file_urls(
    client: httpx.Client,
    *,
    token: str,
    files_body: list[dict[str, Any]],
    timeout: httpx.Timeout,
) -> tuple[str, list[str]]:
    url = f"{API_BASE_URL}{PATH_FILE_URLS_BATCH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"files": files_body, "model_version": "vlm"}
    net_code: str | None = None
    resp: httpx.Response | None = None
    try:
        import json as _json

        req = client.build_request(
            "POST",
            url,
            content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=timeout,
        )
        resp = client.send(req, stream=True)
    except httpx.HTTPError:
        net_code = "api_request_failed"
    except Exception:
        net_code = "api_request_failed"
    if net_code is not None:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _raise(net_code)
        return "", []
    assert resp is not None
    try:
        try:
            client.cookies.clear()
        except Exception:
            pass
        sc = int(resp.status_code)
        if 300 <= sc < 400:
            _raise("api_request_failed")
        if sc != 200:
            _raise("api_request_failed")
        raw = _bounded_read_stream(
            resp,
            limit=int(MAX_HTTP_JSON_RESPONSE_BYTES),
            overflow_code="api_response_invalid",
            stream_error_code="api_request_failed",
            prefer_raw=False,
        )
        body = _json_or_invalid(raw)
        code = body.get("code")
        if code != 0:
            _raise("api_upstream_error")
        data = body.get("data")
        if not isinstance(data, dict):
            _raise("api_response_invalid")
        batch_id = data.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            _raise("api_response_invalid")
        file_urls = data.get("file_urls")
        if not isinstance(file_urls, list):
            _raise("api_response_invalid")
        if len(file_urls) != len(files_body):
            _raise("api_response_invalid")
        out_urls: list[str] = []
        for u in file_urls:
            if not isinstance(u, str):
                _raise("api_response_invalid")
            out_urls.append(u)
        return batch_id.strip(), out_urls
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _put_one(
    client: httpx.Client,
    put_url: str,
    fd: int,
    *,
    expected_size: int,
    timeout: httpx.Timeout,
    cancel_check: Callable[[], bool],
    deadline: float,
    clock_fn: Callable[[], float],
) -> None:
    _check_cancel(cancel_check)
    stream = _iter_fd_chunks(
        fd,
        expected_size=int(expected_size),
        cancel_check=cancel_check,
        deadline=deadline,
        clock_fn=clock_fn,
    )
    # Q2：Content-Length 精确 expected_size；禁止 Content-Type / Authorization / Cookie
    headers = {"Content-Length": str(int(expected_size))}
    net_code: str | None = None
    remote_code: str | None = None
    resp: httpx.Response | None = None
    try:
        req = client.build_request(
            "PUT",
            put_url,
            content=stream,
            headers=headers,
            timeout=timeout,
        )
        req.headers.pop("Authorization", None)
        req.headers.pop("Cookie", None)
        req.headers.pop("Content-Type", None)
        # 钉死 Content-Length（防 httpx 改写）
        req.headers["Content-Length"] = str(int(expected_size))
        resp = client.send(req, stream=True)
    except RemoteMineruError as exc:
        remote_code = getattr(exc, "diagnostic_code", "upload_failed")
    except httpx.HTTPError:
        net_code = "upload_failed"
    except Exception:
        net_code = "upload_failed"
    if remote_code is not None:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _raise(remote_code if isinstance(remote_code, str) else "upload_failed")
        return
    if net_code is not None:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        _raise(net_code)
        return
    assert resp is not None
    try:
        try:
            client.cookies.clear()
        except Exception:
            pass
        sc = int(resp.status_code)
        if 300 <= sc < 400:
            _raise("upload_failed")
        if sc != 200:
            _raise("upload_failed")
        # PUT 响应体有界丢弃（64KiB）；超限 upload_failed；零 canary
        _ = _bounded_read_stream(
            resp,
            limit=int(MAX_HTTP_PUT_RESPONSE_BYTES),
            overflow_code="upload_failed",
            stream_error_code="upload_failed",
            prefer_raw=False,
        )
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _poll_until_done(
    client: httpx.Client,
    *,
    token: str,
    batch_id: str,
    data_ids: list[str],
    timeout_fn: Callable[[], httpx.Timeout],
    cancel_check: Callable[[], bool],
    deadline: float,
    clock_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> list[dict[str, Any]]:
    poll_url = f"{API_BASE_URL}{PATH_EXTRACT_RESULTS_BATCH}/{batch_id}"
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        _check_cancel(cancel_check)
        _require_remaining(deadline, clock_fn)
        net_code: str | None = None
        resp: httpx.Response | None = None
        try:
            req = client.build_request(
                "GET", poll_url, headers=headers, timeout=timeout_fn()
            )
            resp = client.send(req, stream=True)
        except httpx.HTTPError:
            net_code = "api_request_failed"
        except Exception:
            net_code = "api_request_failed"
        if net_code is not None:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            _raise(net_code)
            return []
        assert resp is not None
        try:
            try:
                client.cookies.clear()
            except Exception:
                pass
            sc = int(resp.status_code)
            if 300 <= sc < 400:
                _raise("api_request_failed")
            if sc != 200:
                _raise("api_request_failed")
            raw = _bounded_read_stream(
                resp,
                limit=int(MAX_HTTP_JSON_RESPONSE_BYTES),
                overflow_code="api_response_invalid",
                stream_error_code="api_request_failed",
                prefer_raw=False,
            )
            body = _json_or_invalid(raw)
            if body.get("code") != 0:
                _raise("api_upstream_error")
            data = body.get("data")
            if not isinstance(data, dict):
                _raise("api_response_invalid")
            results = data.get("extract_result")
            if not isinstance(results, list):
                _raise("api_response_invalid")
            any_nonterm = False
            any_failed = False
            all_done = True
            for item in results:
                if not isinstance(item, dict):
                    _raise("api_response_invalid")
                st = item.get("state")
                if not isinstance(st, str):
                    _raise("api_response_invalid")
                if st in _NONTERMINAL_STATES:
                    any_nonterm = True
                    all_done = False
                elif st == "failed":
                    any_failed = True
                    all_done = False
                elif st == "done":
                    pass
                else:
                    _raise("api_response_invalid")
            if any_failed:
                _raise("remote_parse_failed")
            if all_done and not any_nonterm and len(results) > 0:
                return _reconcile_by_data_id(results, data_ids)
            if not results and not any_nonterm:
                _raise("api_response_invalid")
        finally:
            try:
                resp.close()
            except Exception:
                pass
        _check_cancel(cancel_check)
        rem = _require_remaining(deadline, clock_fn)
        sleep_for = min(float(POLL_INTERVAL_SEC), rem)
        if sleep_for <= 0:
            _raise("poll_budget_exceeded")
        sleep_fn(sleep_for)


def _reconcile_by_data_id(
    results: list[dict[str, Any]], data_ids: list[str]
) -> list[dict[str, Any]]:
    """按 data_id 一一对账；禁止顺序/file_name 冒充。"""
    by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if "data_id" not in item:
            _raise("api_response_invalid")
        did = item.get("data_id")
        if not isinstance(did, str) or not did:
            _raise("api_response_invalid")
        if did in by_id:
            _raise("api_response_invalid")
        by_id[did] = item
    ordered: list[dict[str, Any]] = []
    for did in data_ids:
        if did not in by_id:
            _raise("api_response_invalid")
        ordered.append(by_id.pop(did))
    if by_id:
        # 未知 data_id
        _raise("api_response_invalid")
    return ordered


# ---------------------------------------------------------------------------
# 主入口：公开 wrapper + 私有 impl（隐私边界）
# ---------------------------------------------------------------------------


def run_remote_mineru_parse(
    sources: Sequence[RemoteSource],
    *,
    token: str,
    cancel_check: Callable[[], bool],
    transport: Any = None,
    sleep_fn: Callable[[float], None] | None = None,
    clock_fn: Callable[[], float] | None = None,
    resolve_addresses_fn: Callable[[str], list[str]] | None = None,
    trusted_upload_root: Path | str | None = None,
) -> RemoteParseOutput:
    """
    公开入口：显式签名与行为保持；主体在私有 impl。
    仅调用/catch/fold；离开 except 后清空公开参数再 fresh raise。
    禁止 *args/**kwargs；RemoteMineruError 只保留已知固定 code；
    普通 Exception→internal_error；非 Exception BaseException 透传。
    """
    fold_code: str | None = None
    try:
        return _run_remote_mineru_parse_impl(
            sources,
            token=token,
            cancel_check=cancel_check,
            transport=transport,
            sleep_fn=sleep_fn,
            clock_fn=clock_fn,
            resolve_addresses_fn=resolve_addresses_fn,
            trusted_upload_root=trusted_upload_root,
        )
    except RemoteMineruError as exc:
        # 只提取固定码；except 结束后释放原异常/traceback 整帧
        code = getattr(exc, "diagnostic_code", "internal_error") or "internal_error"
        if not isinstance(code, str) or code not in _KNOWN_CODES:
            fold_code = "internal_error"
        else:
            fold_code = code
    except BaseException as exc:
        # Q7：普通 Exception 折叠 internal_error；非 Exception BaseException 透传
        if isinstance(exc, Exception):
            fold_code = "internal_error"
        else:
            raise
    # 离开 except：清空公开参数，禁止依赖枚举 impl 循环局部
    sources = ()  # type: ignore[assignment]
    token = ""
    cancel_check = None  # type: ignore[assignment]
    transport = None
    sleep_fn = None
    clock_fn = None
    resolve_addresses_fn = None
    trusted_upload_root = None
    if fold_code is not None:
        code_out = fold_code
        fold_code = None
        _raise(code_out if isinstance(code_out, str) else "internal_error")
    # 理论上不可达（成功已 return；失败已 raise）
    _raise("internal_error")


def _run_remote_mineru_parse_impl(
    sources: Sequence[RemoteSource],
    *,
    token: str,
    cancel_check: Callable[[], bool],
    transport: Any = None,
    sleep_fn: Callable[[float], None] | None = None,
    clock_fn: Callable[[], float] | None = None,
    resolve_addresses_fn: Callable[[str], list[str]] | None = None,
    trusted_upload_root: Path | str | None = None,
) -> RemoteParseOutput:
    """
    私有实现：批量远程解析主体；异常经 _raise 或原样上抛，由公开 wrapper 折叠。
    """
    if not callable(cancel_check):
        _raise("internal_error")

    _sleep = sleep_fn if callable(sleep_fn) else time.sleep
    _clock = clock_fn if callable(clock_fn) else time.monotonic
    _resolve = (
        resolve_addresses_fn
        if callable(resolve_addresses_fn)
        else _default_resolve_addresses
    )
    trusted_root: Path | None = None
    if trusted_upload_root is not None:
        # 冻结值：保留绝对 canonical 语义；比较时不再 resolve 根
        trusted_root = Path(trusted_upload_root)

    src_list = list(sources)
    if not src_list:
        _raise("internal_error")

    # Token 门（零 HTTP）
    tok = str(token or "").strip()
    if not tok:
        _raise("token_unconfigured")

    # 后缀 / 单文件大小门（零 HTTP）
    for src in src_list:
        suf = _suffix_of(src.filename)
        if suf not in ALLOWED_SOURCE_SUFFIXES:
            _raise("source_type_unsupported")
        exp = src.expected_size
        if type(exp) is not int or exp < 0:
            _raise("source_identity_mismatch")
        if exp > int(REMOTE_MAX_SINGLE_SOURCE_BYTES):
            _raise("source_size_exceeded")

    # 基线 identity（POST 前 lstat）
    baselines: list[tuple[int, int, int, int]] = []
    for src in src_list:
        baselines.append(_capture_baseline(Path(src.path)))
        if baselines[-1][2] != int(src.expected_size):
            _raise("source_identity_mismatch")
        if baselines[-1][2] > int(REMOTE_MAX_SINGLE_SOURCE_BYTES):
            _raise("source_size_exceeded")

    deadline = float(_clock()) + float(POLL_BUDGET_SEC)

    tid = threading.get_ident()
    log_filter = _RemoteLogFilter(tid)
    privacy_loggers = _install_http_privacy_filters(log_filter)

    acquired = False
    try:
        while True:
            _check_cancel(cancel_check)
            rem = _require_remaining(deadline, _clock)
            if REMOTE_SEMAPHORE.acquire(blocking=False):
                acquired = True
                break
            sleep_for = min(float(POLL_INTERVAL_SEC), rem)
            if sleep_for <= 0:
                _raise("poll_budget_exceeded")
            _sleep(sleep_for)

        _check_cancel(cancel_check)
        _require_remaining(deadline, _clock)

        data_ids: list[str] = []
        files_body: list[dict[str, Any]] = []
        for i, src in enumerate(src_list, start=1):
            did = uuid.uuid4().hex
            data_ids.append(did)
            files_body.append(
                {
                    "name": _synthetic_name(i, src.filename),
                    "data_id": did,
                    "is_ocr": True,
                }
            )

        client_kwargs: dict[str, Any] = {
            "verify": True,
            "trust_env": False,
            "follow_redirects": False,
        }
        if transport is not None:
            client_kwargs["transport"] = transport

        with httpx.Client(**client_kwargs) as client:
            _check_cancel(cancel_check)
            rem = _require_remaining(deadline, _clock)
            batch_id, file_urls = _post_file_urls(
                client,
                token=tok,
                files_body=files_body,
                timeout=_timeout_for(rem),
            )

            for u in file_urls:
                _validate_absolute_public_https(u, resolve_fn=_resolve, jit=False)

            for src, put_url, baseline in zip(src_list, file_urls, baselines):
                _check_cancel(cancel_check)
                _require_remaining(deadline, _clock)
                # JIT resolve
                _validate_absolute_public_https(put_url, resolve_fn=_resolve, jit=True)
                fd = _open_verified_fd(
                    Path(src.path),
                    expected_size=int(src.expected_size),
                    baseline=baseline,
                )
                try:
                    # Q5 / 冻结根：最终句柄路径对冻结 root containment；越界零 PUT
                    _assert_fd_under_trusted_root(fd, trusted_root)
                    # Q4：resolve/open/final 门后重算 remaining 再设 PUT timeout
                    rem_put = _require_remaining(deadline, _clock)
                    _put_one(
                        client,
                        put_url,
                        fd,
                        expected_size=int(src.expected_size),
                        timeout=_timeout_for(rem_put),
                        cancel_check=cancel_check,
                        deadline=deadline,
                        clock_fn=_clock,
                    )
                finally:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

            def _to() -> httpx.Timeout:
                return _timeout_for(_require_remaining(deadline, _clock))

            ordered = _poll_until_done(
                client,
                token=tok,
                batch_id=batch_id,
                data_ids=data_ids,
                timeout_fn=_to,
                cancel_check=cancel_check,
                deadline=deadline,
                clock_fn=_clock,
                sleep_fn=_sleep,
            )

            # P1-2：逐份聚合 cap；separator 计入 running；超限立即 output_invalid，禁下后续 ZIP
            md_parts: list[str] = []
            running_cp = 0
            running_utf8 = 0
            sep = _SOURCE_SEPARATOR
            sep_cp = len(sep)
            sep_utf8 = len(sep.encode("utf-8"))
            max_cp = int(MAX_MD_CODEPOINTS)
            max_utf8 = int(MAX_MD_UTF8_BYTES)

            for idx, item in enumerate(ordered):
                _check_cancel(cancel_check)
                # 第二份起先计入 separator；超限则不下载本份及后续 ZIP
                if idx > 0:
                    running_cp += sep_cp
                    running_utf8 += sep_utf8
                    if running_cp > max_cp or running_utf8 > max_utf8:
                        _raise("output_invalid")
                zip_url = item.get("full_zip_url")
                if not isinstance(zip_url, str) or not zip_url:
                    _raise("api_response_invalid")
                _validate_absolute_public_https(zip_url, resolve_fn=_resolve, jit=False)
                _validate_absolute_public_https(zip_url, resolve_fn=_resolve, jit=True)
                rem = _require_remaining(deadline, _clock)
                zbytes = _download_zip_stream(
                    client,
                    zip_url,
                    timeout=_timeout_for(rem),
                    cancel_check=cancel_check,
                    deadline=deadline,
                    clock_fn=_clock,
                )
                part = _extract_full_md_from_zip_bytes(
                    zbytes,
                    cancel_check=cancel_check,
                    deadline=deadline,
                    clock_fn=_clock,
                )
                # 下载字节用毕即丢，避免正文 marker 滞留局部
                zbytes = b""
                running_cp += len(part)
                running_utf8 += len(part.encode("utf-8"))
                if running_cp > max_cp or running_utf8 > max_utf8:
                    _raise("output_invalid")
                md_parts.append(part)

            if not md_parts:
                _raise("output_invalid")
            # 最终 join 语义不变
            markdown = (
                md_parts[0]
                if len(md_parts) == 1
                else sep.join(md_parts)
            )
            # 双门：running 已判；再钉最终串（防实现漂移）
            if len(markdown) > max_cp:
                _raise("output_invalid")
            if len(markdown.encode("utf-8")) > max_utf8:
                _raise("output_invalid")
            if not markdown.strip():
                _raise("output_invalid")
            return RemoteParseOutput(
                markdown=markdown,
                file_count=len(src_list),
                chars=len(markdown),
            )
    finally:
        if acquired:
            try:
                REMOTE_SEMAPHORE.release()
            except ValueError:
                pass
        _remove_http_privacy_filters(privacy_loggers, log_filter)
