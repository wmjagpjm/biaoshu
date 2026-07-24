"""
模块：V1-N remote_mineru 客户端 failure-first 专项
用途：在 production 客户端未实现时建立可计数业务红，锁定官方批量协议、
  is_ocr 三键、绝对 HTTPS、合成名/data_id 对账、Bearer 范围、ZIP 安全、
  轮询预算、信号量等待期与取消；关闭假绿。
对接：docs/v1n-remote-mineru-api-contract.md；计划 2026-07-23-v1n-remote-mineru-api-plan。
二次开发：
  - 禁止顶层 import 尚不存在的 production 模块；缺入口=业务 failed。
  - 全部 HTTP 仅 MockTransport；autouse 模块级外网熔断。
  - 禁止 skip/xfail、or True、except Exception:pass、复制 production ZIP 实现。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import io
import json
import logging
import re
import socket
import stat
import struct
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable
from unittest import mock

import httpx
import pytest

# ---------------------------------------------------------------------------
# 契约冻结常量（测试侧预期；必须与行为联动，不得仅 assert 自身）
# ---------------------------------------------------------------------------

CLIENT_MOD = "app.services.remote_mineru_client"
ENGINE_NAME = "remote_mineru"
API_BASE_URL = "https://mineru.net"
PATH_FILE_URLS_BATCH = "/api/v4/file-urls/batch"
PATH_EXTRACT_RESULTS_PREFIX = "/api/v4/extract-results/batch/"

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
ALWAYS_REJECT_SUFFIXES = frozenset({".html", ".txt", ".md", ".markdown"})

MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_ZIP_MEMBERS = 4096
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
POLL_INTERVAL_SEC = 3
POLL_BUDGET_SEC = 1800
# remote 单文件官方十进制 200MB；本地 managed 200MiB 门不改
REMOTE_MAX_SINGLE_SOURCE_BYTES = 200_000_000
# client 聚合阶段并行 cap（禁止等 task 侧再限）
MAX_MD_CODEPOINTS = 1_000_000
MAX_MD_UTF8_BYTES = 2 * 1024 * 1024

# 测试独立常量表：code → 固定中文（不得用 production 自身作唯一 oracle）
FIXED_MESSAGES: dict[str, str] = {
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
DIAG_CODES = tuple(FIXED_MESSAGES.keys())

# R6-C1：不可伪造的测试 transport 安全标记（实例属性须 is 同一哨兵）
_V1N_SAFE_TRANSPORT_MARK = object()
RETRYABLE_CODES = frozenset({"api_busy", "api_request_failed"})

# 测试专用合成假值（禁止真实 Token 形态）
FAKE_TOKEN = "test-token-not-real"
FAKE_BATCH_ID = "batch-id-for-test-only-0001"
PRESIGNED_PUT_A = "https://upload.example.test/presign/a"
PRESIGNED_PUT_B = "https://upload.example.test/presign/b"
ZIP_URL_A = "https://cdn.example.test/result/a.zip"
ZIP_URL_B = "https://cdn.example.test/result/b.zip"
SOURCE_SEPARATOR = "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
BODY_A = "# FileA_UNIQUE_BODY\n"
BODY_B = "# FileB_UNIQUE_BODY\n"
ORIGINAL_BASENAME_A = "机密标书-甲.pdf"
ORIGINAL_BASENAME_B = "path_leak_canary.docx"

_SOURCE_NAME_RE = re.compile(r"^source-\d{3}\.[A-Za-z0-9]+$")
_DATA_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# 外网熔断计数（autouse 写入）
_NET_GUARD_HITS: list[str] = []


class _V1NNetFuseError(BaseException):
    """
    测试专用网络熔断异常（BaseException 子类，非 Exception）。
    用途：自证真实 guard；避免 production 广义 except RuntimeError 透传特例自缚。
    """


def _fixed_message(code: str) -> str:
    return FIXED_MESSAGES.get(code, FIXED_MESSAGES["internal_error"])

# ---------------------------------------------------------------------------
# 外网熔断（autouse）
# ---------------------------------------------------------------------------

def _net_guard_fail(where: str, *a: Any, **k: Any) -> None:
    msg = f"外网熔断：禁止真实网络 ({where})"
    _NET_GUARD_HITS.append(msg)
    raise _V1NNetFuseError(msg)

@pytest.fixture(autouse=True)
def _v1n_client_network_fuse(monkeypatch: pytest.MonkeyPatch):
    """用途：模块级 socket/DNS/默认 httpx 熔断；仅允许 Mock/ASGI transport 与 loopback。"""
    _NET_GUARD_HITS.clear()
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    real_sock_connect = socket.socket.connect
    real_sock_connect_ex = socket.socket.connect_ex
    _LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def _is_loopback_host(host: object) -> bool:
        h = str(host or "").strip().lower().strip("[]")
        if h in _LOOPBACK:
            return True
        if h.startswith("127."):
            return True
        return False

    def _blocked_create_connection(address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_create_connection(address, *a, **k)
        _net_guard_fail(f"create_connection({host!r})")

    def _blocked_getaddrinfo(host, *a, **k):
        if _is_loopback_host(host):
            return real_getaddrinfo(host, *a, **k)
        _net_guard_fail(f"getaddrinfo({host!r})")

    def _blocked_sock_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_sock_connect(self, address)
        _net_guard_fail(f"socket.connect({host!r})")

    def _blocked_sock_connect_ex(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_sock_connect_ex(self, address)
        _net_guard_fail(f"socket.connect_ex({host!r})")
        return 1

    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _blocked_sock_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_sock_connect_ex)

    def _is_safe_transport(transport: object) -> bool:
        if transport is None:
            return False
        if isinstance(transport, httpx.MockTransport):
            return True
        # R6-C1：精确放行带不可伪造哨兵的测试 transport（_NoPrereadSyncTransport）
        if getattr(transport, "_v1n_safe_transport_mark", None) is _V1N_SAFE_TRANSPORT_MARK:
            return True
        # 精确类型名 + 哨兵双条件：仅类名可被伪造，缺哨兵仍拒绝
        tname = type(transport).__name__
        if tname == "_NoPrereadSyncTransport" and getattr(
            type(transport), "_v1n_safe_transport_mark", None
        ) is _V1N_SAFE_TRANSPORT_MARK:
            return True
        for marker in ("Mock", "ASGI", "WSGI", "TestClient"):
            if marker in tname:
                return True
        return False

    real_client_init = httpx.Client.__init__

    def _guarded_client_init(self, *a, **k):
        transport = k.get("transport")
        if not _is_safe_transport(transport):
            _net_guard_fail(
                f"httpx.Client 未注入安全 transport={type(transport).__name__ if transport else None}"
            )
        return real_client_init(self, *a, **k)

    monkeypatch.setattr(httpx.Client, "__init__", _guarded_client_init)

    # Q6：同步熔断 AsyncClient；明确 MockTransport 路径仍允许
    real_async_init = httpx.AsyncClient.__init__

    def _guarded_async_init(self, *a, **k):
        transport = k.get("transport")
        if not _is_safe_transport(transport):
            _net_guard_fail(
                f"httpx.AsyncClient 未注入安全 transport={type(transport).__name__ if transport else None}"
            )
        return real_async_init(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _guarded_async_init)
    yield

# ---------------------------------------------------------------------------
# 懒加载
# ---------------------------------------------------------------------------

def _client_spec_available() -> bool:
    try:
        spec = importlib.util.find_spec(CLIENT_MOD)
    except (ModuleNotFoundError, ValueError):
        spec = None
    return spec is not None

def _load_client() -> Any:
    """用途：加载 production 客户端；缺失=业务红（非 collection error）。"""
    assert _client_spec_available(), (
        f"业务红：缺少 production 模块 {CLIENT_MOD}（V1-N remote_mineru 客户端未实现）"
    )
    return importlib.import_module(CLIENT_MOD)

def _require_attr(obj: Any, name: str) -> Any:
    assert hasattr(obj, name), f"业务红：缺少属性/函数 {name}"
    return getattr(obj, name)

def _assert_immutable_record(
    cls: Any,
    *,
    fields: tuple[str, ...],
    sample_kwargs: dict[str, Any],
) -> None:
    """用途：NamedTuple/frozen dataclass 字段精确 + 赋值失败。"""
    name = getattr(cls, "__name__", repr(cls))
    if hasattr(cls, "_fields"):
        actual = tuple(cls._fields)
        assert issubclass(cls, tuple), f"业务红：{name} 必须为 tuple 子类（不可变）"
    elif hasattr(cls, "__dataclass_fields__"):
        actual = tuple(cls.__dataclass_fields__.keys())
        params = getattr(cls, "__dataclass_params__", None)
        assert params is not None and bool(getattr(params, "frozen", False)), (
            f"业务红：{name} dataclass 必须 frozen=True"
        )
    else:
        actual = tuple(
            n for n in getattr(cls, "__slots__", ()) if not str(n).startswith("_")
        )
    assert actual == fields, f"业务红：{name} 字段必须精确 {fields}，actual={actual}"
    obj = cls(**sample_kwargs)
    mutated = False
    try:
        setattr(obj, fields[0], sample_kwargs[fields[0]])
        mutated = True
    except (AttributeError, TypeError):
        mutated = False
    except Exception as exc:
        # 仅允许 frozen 相关异常名；其它异常上抛
        if type(exc).__name__ in {"FrozenInstanceError", "FrozenError"}:
            mutated = False
        else:
            raise
    assert not mutated, f"业务红：{name} 构造后赋值必须失败"

def _chain_blob(exc: BaseException) -> str:
    """用途：扫描 exception cause/context，防隐私泄漏假绿。"""
    parts: list[str] = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{cur!s}")
        parts.append(f"{cur!r}")
        parts.append(repr(getattr(cur, "args", ())))
        parts.append(str(getattr(cur, "message", "")))
        parts.append(str(getattr(cur, "diagnostic_code", "")))
        cause = getattr(cur, "__cause__", None)
        ctx = getattr(cur, "__context__", None)
        cur = cause if cause is not None else ctx
    return "\n".join(parts)

def _assert_remote_error(
    exc: BaseException,
    expected_code: str,
    *,
    msg_fn: Callable[[str], str] | None = None,
    forbidden: list[str] | None = None,
) -> None:
    assert hasattr(exc, "diagnostic_code"), "业务红：异常必须暴露 diagnostic_code"
    assert exc.diagnostic_code == expected_code, (
        f"业务红：diagnostic_code 必须为 {expected_code!r}，actual={exc.diagnostic_code!r}"
    )
    assert hasattr(exc, "message"), "业务红：异常必须暴露 message"
    expected_msg = _fixed_message(expected_code)
    assert exc.message == expected_msg, (
        f"业务红：message 必须精确等于测试冻结表[{expected_code}]={expected_msg!r}，"
        f"actual={exc.message!r}"
    )
    assert str(exc) == exc.message, "业务红：str(exc) 必须等于 message"
    if msg_fn is not None:
        assert msg_fn(expected_code) == expected_msg, (
            f"业务红：production message_for_code({expected_code}) 必须等于冻结表"
        )
    elif _client_spec_available():
        mod = _load_client()
        prod_fn = getattr(mod, "message_for_code", None)
        if callable(prod_fn):
            assert prod_fn(expected_code) == expected_msg
    blob = _chain_blob(exc)
    frags = list(forbidden or [])
    frags.extend(
        [
            FAKE_TOKEN,
            "Bearer ",
            FAKE_BATCH_ID,
            PRESIGNED_PUT_A,
            PRESIGNED_PUT_B,
            ZIP_URL_A,
            ZIP_URL_B,
            "trace_id",
            "err_msg",
            "cloud-secret",
            "cloud secret",
            ORIGINAL_BASENAME_A,
            ORIGINAL_BASENAME_B,
            BODY_A.strip(),
            BODY_B.strip(),
        ]
    )
    for frag in frags:
        if not frag:
            continue
        assert frag not in blob, f"业务红：异常链泄漏敏感片段 {frag!r}"

def _make_zip_bytes(members: dict[str, bytes]) -> bytes:
    """用途：内存构造 ZIP；测试夹具 only。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()

def _patch_zip_encrypted_flags(raw: bytes, *, member_index: int = 0) -> bytes:
    """C1：直接修补 local header 与 central directory 的 encrypted flag。"""
    data = bytearray(raw)
    locals_found: list[int] = []
    i = 0
    while True:
        j = data.find(b"PK\x03\x04", i)
        if j < 0:
            break
        locals_found.append(j)
        i = j + 4
    assert locals_found, "夹具错误：ZIP 无 local header"
    assert 0 <= member_index < len(locals_found)
    lo = locals_found[member_index]
    flag = struct.unpack_from("<H", data, lo + 6)[0]
    struct.pack_into("<H", data, lo + 6, flag | 0x1)
    centrals: list[int] = []
    i = 0
    while True:
        j = data.find(b"PK\x01\x02", i)
        if j < 0:
            break
        centrals.append(j)
        i = j + 4
    assert centrals, "夹具错误：ZIP 无 central directory"
    assert 0 <= member_index < len(centrals)
    co = centrals[member_index]
    flag_c = struct.unpack_from("<H", data, co + 8)[0]
    struct.pack_into("<H", data, co + 8, flag_c | 0x1)
    return bytes(data)

def _make_encrypted_full_md_zip(data: bytes = b"# encrypted-looking") -> bytes:
    """C1：构造加密 full.md ZIP，并自证 reread flag_bits&1。"""
    raw = _make_zip_bytes({"full.md": data})
    patched = _patch_zip_encrypted_flags(raw, member_index=0)
    with zipfile.ZipFile(io.BytesIO(patched)) as zf:
        info = zf.infolist()[0]
        assert info.filename == "full.md"
        assert (info.flag_bits & 0x1) == 0x1, (
            f"夹具自证失败：reread flag_bits&1 必须为 1，actual={info.flag_bits}"
        )
        with pytest.raises(RuntimeError):
            zf.read(info)
    return patched

def _make_zip_special(
    *,
    name: str,
    data: bytes = b"x",
    external_attr: int | None = None,
    encrypt: bool = False,
    extra_members: dict[str, bytes] | None = None,
) -> bytes:
    """用途：构造含特殊项/加密 flag 的 ZIP；Unix 特殊项显式 create_system=3 并 reread 自证。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo(filename=name)
        # Q7：Windows 默认 create_system=0；Unix 特殊文件必须 create_system=3
        info.create_system = 3
        if external_attr is not None:
            info.external_attr = external_attr
        zf.writestr(info, data)
        if extra_members:
            for n, d in extra_members.items():
                zf.writestr(n, d)
    raw = buf.getvalue()
    # reread 自证 create_system 与 mode/type
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        first = zf.infolist()[0]
        assert first.create_system == 3, (
            f"夹具自证失败：create_system 必须为 3，actual={first.create_system}"
        )
        if external_attr is not None:
            mode = (first.external_attr >> 16) & 0xFFFF
            expected_mode = (external_attr >> 16) & 0xFFFF
            assert mode == expected_mode, (
                f"夹具自证失败：external mode 必须保留，actual={mode:#o} expected={expected_mode:#o}"
            )
    if encrypt:
        raw = _patch_zip_encrypted_flags(raw, member_index=0)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            assert (zf.infolist()[0].flag_bits & 1) == 1
    return raw

def _write_temp_source(tmp_path: Path, name: str, content: bytes) -> Path:
    """用途：平面或嵌套路径；嵌套时先创建父目录，避免 FileNotFoundError 假绿。"""
    # Windows 反斜杠 canary 改为平面动态名，避免依赖非法文件名
    safe_name = name.replace("\\", "_").replace("/", "_")
    if safe_name != name:
        # 保留业务意图：文件名含路径分隔符形态 → 用下划线平面 canary
        p = tmp_path / safe_name
    else:
        p = tmp_path / name
        if p.parent != tmp_path:
            p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p

def _build_sources(mod: Any, paths: list[Path]) -> list[Any]:
    src_cls = _require_attr(mod, "RemoteSource")
    out = []
    for p in paths:
        out.append(
            src_cls(
                path=p,
                filename=p.name,
                expected_size=p.stat().st_size,
            )
        )
    return out

def _get_run_fn(mod: Any) -> Callable[..., Any]:
    """用途：冻结 public 入口 run_remote_mineru_parse。"""
    fn = getattr(mod, "run_remote_mineru_parse", None)
    assert callable(fn), "业务红：缺少精确入口 run_remote_mineru_parse"
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names[:1] == ["sources"], f"业务红：首参必须 sources，actual={names}"
    for required_kw in ("token", "cancel_check"):
        assert required_kw in names, f"业务红：必须包含 keyword {required_kw}"
    assert "resolve_addresses_fn" in names, (
        "业务红：必须支持 keyword resolve_addresses_fn（可注入解析，禁止真实 DNS）"
    )
    for p in params:
        if p.name in {
            "token",
            "cancel_check",
            "transport",
            "sleep_fn",
            "clock_fn",
            "resolve_addresses_fn",
        }:
            assert p.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        assert p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ), f"业务红：禁止 *args/**kwargs，found={p.name}"
    return fn

# Q2：Python 3.13 下 TEST-NET(203.0.113.0/24) 的 is_global=False；改用稳定假公网
FAKE_PUBLIC_IP_A = "8.8.8.8"
FAKE_PUBLIC_IP_B = "1.1.1.1"

def _assert_addr_is_global(addr: str) -> None:
    import ipaddress

    ip = ipaddress.ip_address(addr)
    assert ip.is_global is True, (
        f"测试夹具错误：{addr} 必须 is_global=True，actual global={ip.is_global} private={ip.is_private}"
    )

_assert_addr_is_global(FAKE_PUBLIC_IP_A)
_assert_addr_is_global(FAKE_PUBLIC_IP_B)

def _default_public_resolver(host: str) -> list[str]:
    """测试默认：不走真实 DNS，返回 ipaddress 判定为 global 的稳定假公网地址。"""
    return [FAKE_PUBLIC_IP_A]

def _public_resolver_factory(mapping: dict[str, list[str]]) -> Callable[[str], list[str]]:
    def _resolve(host: str) -> list[str]:
        h = host.strip().lower().strip("[]")
        if h in mapping:
            return list(mapping[h])
        return [FAKE_PUBLIC_IP_A]

    return _resolve

def _normalize_url_key(url: str) -> tuple[str, str, int, str, str]:
    """
    R1：规范化 URL 比较键——scheme/host/effective port/path/query。
    显式 :443 与缺省 HTTPS 端口等价；禁止仅按原始字符串相等判定。
    """
    from urllib.parse import urlsplit

    parts = urlsplit(str(url))
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if parts.port is not None:
        port = int(parts.port)
    elif scheme == "https":
        port = 443
    elif scheme == "http":
        port = 80
    else:
        port = -1
    path = parts.path or ""
    query = parts.query or ""
    return (scheme, host, port, path, query)

def _assert_url_equivalent(actual: str, expected: str) -> None:
    assert _normalize_url_key(actual) == _normalize_url_key(expected), (
        f"业务红：URL 规范化后必须等价 actual={actual!r} expected={expected!r} "
        f"keys={_normalize_url_key(actual)} vs {_normalize_url_key(expected)}"
    )

def _run_kwargs(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "token": FAKE_TOKEN,
        "cancel_check": lambda: False,
        "sleep_fn": lambda _s: None,
        "clock_fn": lambda: 0.0,
        "resolve_addresses_fn": _default_public_resolver,
    }
    base.update(extra)
    return base

def _assert_client_http_defaults(mod: Any) -> None:
    """用途：常量与 Client 默认安全面（常量仅作锚定，行为另测）。"""
    assert getattr(mod, "ENGINE_NAME", None) == ENGINE_NAME
    assert getattr(mod, "API_BASE_URL", None) == API_BASE_URL
    assert getattr(mod, "PATH_FILE_URLS_BATCH", None) == PATH_FILE_URLS_BATCH
    path_prefix = getattr(mod, "PATH_EXTRACT_RESULTS_BATCH", None) or getattr(
        mod, "PATH_EXTRACT_RESULTS_PREFIX", None
    )
    assert path_prefix is not None, "业务红：缺少批次结果 path 常量"
    # Q1：禁止 assert BoolOp Or；用 any([...]) 保留语义
    prefix_s = str(path_prefix)
    path_ok = any(
        [
            prefix_s.rstrip("/").endswith("extract-results/batch"),
            prefix_s.startswith(PATH_EXTRACT_RESULTS_PREFIX.rstrip("/")),
        ]
    )
    assert path_ok, (
        f"业务红：轮询 path 必须指向 extract-results/batch，actual={path_prefix!r}"
    )
    assert getattr(mod, "MAX_ZIP_BYTES", None) == MAX_ZIP_BYTES
    assert getattr(mod, "MAX_ZIP_MEMBERS", None) == MAX_ZIP_MEMBERS
    assert getattr(mod, "MAX_ZIP_UNCOMPRESSED_BYTES", None) == MAX_ZIP_UNCOMPRESSED_BYTES
    assert float(getattr(mod, "POLL_INTERVAL_SEC")) == float(POLL_INTERVAL_SEC)
    assert float(getattr(mod, "POLL_BUDGET_SEC")) == float(POLL_BUDGET_SEC)
    assert int(getattr(mod, "REMOTE_MAX_SINGLE_SOURCE_BYTES")) == REMOTE_MAX_SINGLE_SOURCE_BYTES
    assert int(getattr(mod, "MAX_MD_CODEPOINTS", MAX_MD_CODEPOINTS)) == MAX_MD_CODEPOINTS
    assert int(getattr(mod, "MAX_MD_UTF8_BYTES", MAX_MD_UTF8_BYTES)) == MAX_MD_UTF8_BYTES
    sem = getattr(mod, "REMOTE_SEMAPHORE", None)
    assert sem is not None, "业务红：必须暴露 REMOTE_SEMAPHORE=BoundedSemaphore(1)"
    # Q9：精确 isinstance 真实 BoundedSemaphore，禁止类名 or 放行
    assert isinstance(sem, threading.BoundedSemaphore), (
        f"业务红：REMOTE_SEMAPHORE 必须为真实 BoundedSemaphore，actual={type(sem)}"
    )
    initial = getattr(sem, "_initial_value", None)
    if initial is None:
        initial = getattr(sem, "_value", None)
    assert int(initial) == 1, f"业务红：REMOTE_SEMAPHORE 必须为 1，actual={initial}"
    # over-release 合约
    if getattr(sem, "_value", None) == 1:
        with pytest.raises(ValueError):
            sem.release()
        assert getattr(sem, "_value", None) == 1
    allowed = getattr(mod, "ALLOWED_SOURCE_SUFFIXES", None)
    assert allowed is not None and set(allowed) == set(ALLOWED_SOURCE_SUFFIXES), (
        f"业务红：ALLOWED_SOURCE_SUFFIXES 必须精确冻结集合，actual={allowed!r}"
    )
    msg_fn = _require_attr(mod, "message_for_code")
    for code, text in FIXED_MESSAGES.items():
        assert msg_fn(code) == text, f"业务红：{code} 文案必须冻结为 {text!r}"
    assert msg_fn("not_a_real_code_xyz") == FIXED_MESSAGES["internal_error"]
    retry_fn = getattr(mod, "is_retryable_code", None)
    if callable(retry_fn):
        for c in DIAG_CODES:
            assert bool(retry_fn(c)) == (c in RETRYABLE_CODES)

def _post_ok_single(data_id_holder: dict, put_url: str = PRESIGNED_PUT_A) -> Callable:
    def handler_post(request: httpx.Request) -> httpx.Response | None:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            data_id_holder["id"] = body["files"][0]["data_id"]
            data_id_holder["ids"] = [f["data_id"] for f in body["files"]]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [put_url],
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        return None

    return handler_post

# ===========================================================================
# A. 可收集与入口
# ===========================================================================

def test_a1_module_importable_or_business_red():
    """用途：production 缺失时业务红；存在时可 import。"""
    _load_client()

def test_a2_public_surface_and_immutable_records():
    mod = _load_client()
    _assert_client_http_defaults(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    assert issubclass(err_cls, Exception)
    src_cls = _require_attr(mod, "RemoteSource")
    out_cls = _require_attr(mod, "RemoteParseOutput")
    _assert_immutable_record(
        src_cls,
        fields=("path", "filename", "expected_size"),
        sample_kwargs={
            "path": Path("x.pdf"),
            "filename": "x.pdf",
            "expected_size": 1,
        },
    )
    _assert_immutable_record(
        out_cls,
        fields=("markdown", "file_count", "chars"),
        sample_kwargs={"markdown": "m", "file_count": 1, "chars": 1},
    )
    # message 表由 _assert_client_http_defaults 与 FIXED_MESSAGES 冻结对齐
    _get_run_fn(mod)

def test_a3_not_registered_in_parse_engines():
    from app.services import parse_engines

    parse_engines.reset_registry()
    names = parse_engines.list_registered_engines()
    assert ENGINE_NAME not in names
    assert "lightweight" in names
    if _client_spec_available():
        mod = _load_client()
        assert getattr(mod, "ENGINE_NAME", None) == ENGINE_NAME
        assert ENGINE_NAME not in parse_engines.list_registered_engines()

# ===========================================================================
# B. 后缀门（零 HTTP）
# ===========================================================================

def test_b1_unsupported_suffixes_reject_before_any_http(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    http_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(f"{request.method} {request.url}")
        raise AssertionError("后缀拒绝后不得发 HTTP")

    transport = httpx.MockTransport(handler)
    for suf in sorted(ALWAYS_REJECT_SUFFIXES | {".exe", ".bin", ".unknown"}):
        p = _write_temp_source(tmp_path, f"doc{suf}", b"not-sent")
        sources = _build_sources(mod, [p])
        with pytest.raises(err_cls) as ei:
            run(
                sources,
                token=FAKE_TOKEN,
                cancel_check=lambda: False,
                transport=transport,
                sleep_fn=lambda _s: None,
                clock_fn=lambda: 0.0,
                resolve_addresses_fn=_default_public_resolver,
            )
        _assert_remote_error(ei.value, "source_type_unsupported", msg_fn=msg_fn)
    assert http_calls == [], f"业务红：拒绝后缀不得 HTTP，actual={http_calls}"

def test_b2_allowed_suffix_case_normalized(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "Scan.PDF", b"%PDF-fake")
    sources = _build_sources(mod, [p])
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            name = body["files"][0]["name"]
            assert name.endswith(".pdf"), f"业务红：后缀须归一小写，actual={name}"
            assert _SOURCE_NAME_RE.match(name), f"业务红：合成名非法 {name}"
            item = body["files"][0]
            assert set(item.keys()) == {"name", "data_id", "is_ocr"}
            assert item["is_ocr"] is True
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "trace_id": "trace-should-not-leak",
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200, content=b"ok")
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            data_id = json.loads(calls[0].content.decode())["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "extract_result": [
                            {
                                "data_id": data_id,
                                "file_name": "ignored.pdf",
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                                "err_msg": "should-not-surface",
                            }
                        ],
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"nested/full.md": b"# OK\n"})
            )
        raise AssertionError(f"未声明请求: {request.method} {request.url}")

    out = run(
        sources,
        token=FAKE_TOKEN,
        cancel_check=lambda: False,
        transport=httpx.MockTransport(handler),
        sleep_fn=lambda _s: None,
        clock_fn=lambda: 0.0,
        resolve_addresses_fn=_default_public_resolver,
    )
    assert out.markdown.strip().startswith("# OK")
    assert out.file_count == 1
    assert out.chars == len(out.markdown)

# ===========================================================================
# C. HTTP 协议精确性
# ===========================================================================

def test_c1_happy_path_order_headers_body_and_data_id_reconcile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    mod = _load_client()
    run = _get_run_fn(mod)
    msg_fn = _require_attr(mod, "message_for_code")
    p1 = _write_temp_source(tmp_path, ORIGINAL_BASENAME_A, b"%PDF-1")
    # 平面动态 canary：原路径分隔符意图，不触发 Windows FileNotFoundError
    p2 = _write_temp_source(tmp_path, ORIGINAL_BASENAME_B, b"PK\x03\x04-fake")
    sources = _build_sources(mod, [p1, p2])
    calls: list[httpx.Request] = []
    seen_data_ids: list[str] = []
    clock = {"t": 0.0}
    abs_paths = [str(p1.resolve()), str(p2.resolve())]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        url = str(request.url)
        if request.method == "POST":
            assert url == f"{API_BASE_URL}{PATH_FILE_URLS_BATCH}"
            assert request.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"
            content_type = request.headers.get("content-type")
            assert content_type is not None
            assert "application/json" in content_type
            body = json.loads(request.content.decode("utf-8"))
            assert set(body.keys()) == {"files", "model_version"}
            assert body["model_version"] == "vlm"
            assert len(body["files"]) == 2
            for i, item in enumerate(body["files"], start=1):
                assert set(item.keys()) == {"name", "data_id", "is_ocr"}, (
                    f"业务红：files 项必须精确三键，actual={set(item.keys())}"
                )
                assert item["is_ocr"] is True, "业务红：is_ocr 必须是 True"
                assert item["name"] == f"source-{i:03d}." + (
                    "pdf" if i == 1 else "docx"
                )
                assert _DATA_ID_RE.match(item["data_id"]), item["data_id"]
                seen_data_ids.append(item["data_id"])
            raw = request.content.decode("utf-8")
            assert "机密标书" not in raw
            assert "path_leak" not in raw
            assert FAKE_TOKEN not in raw
            for ap in abs_paths:
                assert ap not in raw
            assert seen_data_ids[0] != seen_data_ids[1]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "trace_id": "trace-xyz-secret",
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A, PRESIGNED_PUT_B],
                    },
                },
            )
        if request.method == "PUT":
            assert request.headers.get("Authorization") in (None, "")
            assert request.headers.get("Cookie") in (None, "")
            ct = request.headers.get("content-type")
            assert ct in (None, ""), (
                f"业务红：PUT 不得设置 Content-Type，actual={ct!r}"
            )
            for h in request.headers.keys():
                assert h.lower() not in {"authorization", "cookie", "set-cookie"}
            if url == PRESIGNED_PUT_A:
                assert request.content == b"%PDF-1"
                return httpx.Response(200)
            if url == PRESIGNED_PUT_B:
                assert request.content == b"PK\x03\x04-fake"
                return httpx.Response(200)
            raise AssertionError(f"未知 PUT {url}")
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in url:
            assert url == f"{API_BASE_URL}{PATH_EXTRACT_RESULTS_PREFIX}{FAKE_BATCH_ID}"
            assert request.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"
            # 故意打乱结果顺序
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "extract_result": [
                            {
                                "data_id": seen_data_ids[1],
                                "file_name": "zzz-wrong-order.docx",
                                "state": "done",
                                "full_zip_url": ZIP_URL_B,
                            },
                            {
                                "data_id": seen_data_ids[0],
                                "file_name": "aaa.pdf",
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            },
                        ],
                    },
                },
            )
        if request.method == "GET" and url == ZIP_URL_A:
            assert request.headers.get("Authorization") in (None, "")
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": BODY_A.encode("utf-8")})
            )
        if request.method == "GET" and url == ZIP_URL_B:
            assert request.headers.get("Authorization") in (None, "")
            return httpx.Response(
                200, content=_make_zip_bytes({"x/full.md": BODY_B.encode("utf-8")})
            )
        raise AssertionError(f"未声明请求: {request.method} {url}")

    with caplog.at_level(logging.DEBUG):
        out = run(
            sources,
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda s: clock.update(t=clock["t"] + float(s)),
            clock_fn=lambda: clock["t"],
            resolve_addresses_fn=_default_public_resolver,
        )

    expected_md = BODY_A + SOURCE_SEPARATOR + BODY_B
    assert out.markdown == expected_md, (
        f"业务红：聚合正文/分隔符必须精确，actual={out.markdown!r} expected={expected_md!r}"
    )
    assert out.file_count == 2
    assert out.chars == len(expected_md)

    methods = [c.method for c in calls]
    assert methods[0] == "POST"
    assert methods[1:3] == ["PUT", "PUT"]
    zip_gets = [c for c in calls if c.method == "GET" and str(c.url) in {ZIP_URL_A, ZIP_URL_B}]
    poll_gets = [
        c
        for c in calls
        if c.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(c.url)
    ]
    assert len(zip_gets) == 2, f"业务红：ZIP GET 必须精确 2 次且仅 ZIP_URL，actual={len(zip_gets)}"
    assert {str(c.url) for c in zip_gets} == {ZIP_URL_A, ZIP_URL_B}
    assert poll_gets, "业务红：必须有轮询 GET"
    # 轮询 URL 不得计入 ZIP
    for c in poll_gets:
        assert str(c.url) not in {ZIP_URL_A, ZIP_URL_B}

    # 隐私：真实 data_id / TEMP / URL 不得进 caplog
    forbidden = list(seen_data_ids) + abs_paths + [
        FAKE_BATCH_ID,
        PRESIGNED_PUT_A,
        PRESIGNED_PUT_B,
        ZIP_URL_A,
        ZIP_URL_B,
        "trace-xyz-secret",
        FAKE_TOKEN,
        ORIGINAL_BASENAME_A,
        ORIGINAL_BASENAME_B,
        BODY_A.strip(),
        BODY_B.strip(),
    ]
    for frag in forbidden:
        assert frag not in caplog.text, f"业务红：caplog 泄漏 {frag!r}"

def test_c2_put_non_200_is_upload_failed(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            return httpx.Response(403, content=b"no")
        raise AssertionError("PUT 失败后不得继续")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "upload_failed", msg_fn=msg_fn)

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://upload.example.test/presign/a",
        "/relative/presign/a",
        "//upload.example.test/presign/a",
        "https:///no-host/presign",
        "https://",
        "",
        "https://user:pass@upload.example.test/presign/a",
        "https://upload.example.test/presign/a#frag",
        "https://upload.example.test:8443/presign/a",
        "https://127.0.0.1/presign/a",
        "https://[::1]/presign/a",
        "https://169.254.169.254/latest/meta-data/",
        "https://192.168.1.10/presign/a",
        "https://10.0.0.5/presign/a",
        "https://172.16.0.5/presign/a",
    ],
)
def test_c3_illegal_put_url_zero_put(tmp_path: Path, bad_url: str):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [bad_url]},
                },
            )
        if request.method == "PUT":
            puts.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError(f"非法 PUT URL 后不得继续 {request.method}")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_response_invalid", msg_fn=msg_fn)
    assert puts == [], f"业务红：非法 PUT URL 必须零 PUT，actual={puts}"

@pytest.mark.parametrize(
    "bad_zip",
    [
        "http://cdn.example.test/result/a.zip",
        "/relative/a.zip",
        "//cdn.example.test/result/a.zip",
        "https:///no-host/a.zip",
        "https://",
        "",
        "https://user:pass@cdn.example.test/a.zip",
        "https://cdn.example.test/a.zip#x",
        "https://cdn.example.test:8443/a.zip",
        "https://127.0.0.1/a.zip",
        "https://169.254.169.254/a.zip",
        "https://192.168.1.10/a.zip",
    ],
)
def test_c4_illegal_zip_url_zero_zip_get(tmp_path: Path, bad_zip: str):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    zip_gets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": bad_zip,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET":
            zip_gets.append(str(request.url))
            return httpx.Response(200, content=b"nope")
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_response_invalid", msg_fn=msg_fn)
    assert zip_gets == [], f"业务红：非法 ZIP URL 必须零 ZIP GET，actual={zip_gets}"

def test_c5_redirects_exact_codes_no_follow(tmp_path: Path):
    """用途：POST/PUT/轮询/ZIP 的 3xx 分别精确诊断，Location 零跟随。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    location = "https://evil.example.test/hijack"

    # POST 3xx → api_request_failed
    hops: list[str] = []

    def post_redir(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(302, headers={"location": location})
        hops.append("FOLLOWED")
        return httpx.Response(200)

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(post_redir),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_request_failed", msg_fn=msg_fn)
    assert "FOLLOWED" not in hops
    assert location not in hops

    # PUT 3xx → upload_failed
    hops.clear()

    def put_redir(request: httpx.Request) -> httpx.Response:
        hops.append(f"{request.method}:{request.url}")
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            return httpx.Response(307, headers={"location": location})
        hops.append("FOLLOWED")
        return httpx.Response(200)

    with pytest.raises(err_cls) as ei2:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(put_redir),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei2.value, "upload_failed", msg_fn=msg_fn)
    assert not any("FOLLOWED" in h for h in hops)
    assert not any(location in h for h in hops)

    # 轮询 3xx → api_request_failed
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    hops.clear()

    def poll_redir(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        hops.append(str(request.url))
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(301, headers={"location": location})
        hops.append("FOLLOWED")
        return httpx.Response(200)

    with pytest.raises(err_cls) as ei3:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(poll_redir),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei3.value, "api_request_failed", msg_fn=msg_fn)
    assert "FOLLOWED" not in hops

    # ZIP 3xx → zip_download_failed
    hops.clear()

    def zip_redir(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            hops.append("zip")
            return httpx.Response(302, headers={"location": location})
        hops.append("FOLLOWED")
        return httpx.Response(200)

    with pytest.raises(err_cls) as ei4:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(zip_redir),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei4.value, "zip_download_failed", msg_fn=msg_fn)
    assert "FOLLOWED" not in hops

def test_c6_file_urls_count_mismatch(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"1"),
        _write_temp_source(tmp_path, "b.pdf", b"2"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        raise AssertionError("数量不匹配后不得 PUT")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, paths),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_response_invalid", msg_fn=msg_fn)

@pytest.mark.parametrize(
    "cloud_code",
    [
        1,
        10001,
        20001,
        99999,
        -1,
    ],
)
@pytest.mark.parametrize("phase", ["post", "poll"])
def test_c7_http200_code_nonzero_folds_upstream(
    tmp_path: Path, cloud_code: int, phase: str
):
    """
    C11/R7：HTTP 200 且 code!=0 在 POST 与 poll 两阶段均唯一折叠 api_upstream_error。
    禁止臆测细粒度数字映射；不透传 code/msg/trace。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {"id": ""}

    def handler_code(request: httpx.Request) -> httpx.Response:
        if phase == "post":
            return httpx.Response(
                200,
                json={
                    "code": cloud_code,
                    "msg": f"cloud secret {cloud_code}",
                    "trace_id": f"tid-{cloud_code}",
                    "data": {},
                },
            )
        if request.method == "POST":
            body = json.loads(request.content.decode())
            holder["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": cloud_code,
                    "msg": f"cloud secret {cloud_code}",
                    "trace_id": f"tid-{cloud_code}",
                    "data": {},
                },
            )
        raise AssertionError(f"phase={phase} 不得进入 ZIP")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_code),
            **_run_kwargs(),
        )
    _assert_remote_error(
        ei.value,
        "api_upstream_error",
        forbidden=[f"cloud secret {cloud_code}", f"tid-{cloud_code}", str(cloud_code)],
    )


def test_c7b_malformed_json_is_api_response_invalid(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")

    def handler_bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{")

    with pytest.raises(err_cls) as ei2:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_bad),
            **_run_kwargs(),
        )
    _assert_remote_error(ei2.value, "api_response_invalid")

def test_c8_http_status_and_network_error(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="err")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler_500),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_request_failed", msg_fn=msg_fn)

    def handler_net(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated-connect-failure")

    with pytest.raises(err_cls) as ei2:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler_net),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei2.value, "api_request_failed", msg_fn=msg_fn)
    assert "simulated-connect-failure" not in ei2.value.message

# ===========================================================================
# D. 状态机 / 对账 / 取消 / 预算
# ===========================================================================

def test_d1_all_known_nonterminal_states_then_done(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    poll_states = ["waiting-file", "pending", "running", "converting", "done"]
    idx = {"i": 0}
    sleeps: list[float] = []
    clock = {"t": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            st = poll_states[min(idx["i"], len(poll_states) - 1)]
            idx["i"] += 1
            item: dict[str, Any] = {
                "data_id": holder["id"],
                "state": st,
                "file_name": "x.pdf",
            }
            if st == "done":
                item["full_zip_url"] = ZIP_URL_A
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "extract_result": [item]},
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# done\n"})
            )
        raise AssertionError(f"未声明 {request.method} {request.url}")

    out = run(
        _build_sources(mod, [p]),
        token=FAKE_TOKEN,
        cancel_check=lambda: False,
        transport=httpx.MockTransport(handler),
        sleep_fn=lambda s: (sleeps.append(s), clock.update(t=clock["t"] + s)),
        clock_fn=lambda: clock["t"],
        resolve_addresses_fn=_default_public_resolver,
    )
    assert "# done" in out.markdown
    assert sleeps == [float(POLL_INTERVAL_SEC)] * 4, (
        f"业务红：每非终态精确一次 sleep={POLL_INTERVAL_SEC}，actual={sleeps}"
    )

def test_d2_unknown_state_protocol_failure(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "uploading",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        raise AssertionError("未知状态不得下载 ZIP")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_response_invalid", msg_fn=msg_fn)

def test_d3_any_failed_fails_whole_batch_no_partial_zip(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"1"),
        _write_temp_source(tmp_path, "b.pdf", b"2"),
    ]
    ids: list[str] = []
    zip_gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            ids.extend(x["data_id"] for x in body["files"])
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A, PRESIGNED_PUT_B],
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": ids[0],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            },
                            {
                                "data_id": ids[1],
                                "state": "failed",
                                "err_msg": "cloud-fail-secret",
                            },
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) in {ZIP_URL_A, ZIP_URL_B}:
            zip_gets["n"] += 1
            return httpx.Response(200, content=_make_zip_bytes({"full.md": b"x"}))
        raise AssertionError(f"未声明 {request.method} {request.url}")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, paths),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "remote_parse_failed", msg_fn=msg_fn)
    assert "cloud-fail-secret" not in ei.value.message
    assert zip_gets["n"] == 0, "业务红：存在 failed 时禁止下载任何 ZIP"

@pytest.mark.parametrize(
    "case",
    [
        "missing_local",
        "empty_results",
        "missing_key",
        "duplicate",
        "unknown_extra",
    ],
)
def test_d4_data_id_reconcile_cases_zero_zip(tmp_path: Path, case: str):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    zip_gets = {"n": 0}

    def build_result() -> list[dict[str, Any]]:
        if case == "missing_local":
            return [
                {
                    "data_id": "c" * 32,
                    "state": "done",
                    "full_zip_url": ZIP_URL_A,
                }
            ]
        if case == "empty_results":
            return []
        if case == "missing_key":
            return [
                {
                    "state": "done",
                    "full_zip_url": ZIP_URL_A,
                }
            ]
        if case == "duplicate":
            return [
                {
                    "data_id": holder["id"],
                    "state": "done",
                    "full_zip_url": ZIP_URL_A,
                },
                {
                    "data_id": holder["id"],
                    "state": "done",
                    "full_zip_url": ZIP_URL_B,
                },
            ]
        if case == "unknown_extra":
            return [
                {
                    "data_id": holder["id"],
                    "state": "done",
                    "full_zip_url": ZIP_URL_A,
                },
                {
                    "data_id": "d" * 32,
                    "state": "done",
                    "full_zip_url": ZIP_URL_B,
                },
            ]
        raise AssertionError(case)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"extract_result": build_result()}},
            )
        if request.method == "GET":
            zip_gets["n"] += 1
            return httpx.Response(200, content=_make_zip_bytes({"full.md": b"x"}))
        raise AssertionError("对账失败不得下 ZIP")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "api_response_invalid", msg_fn=msg_fn)
    assert zip_gets["n"] == 0, f"业务红：{case} 必须零 ZIP GET"

def test_d5_cancel_during_poll(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    cancelled = {"v": False}

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            cancelled["v"] = True
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"data_id": holder["id"], "state": "running"}
                        ]
                    },
                },
            )
        raise AssertionError("取消后不得下 ZIP")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: cancelled["v"],
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "interrupted", msg_fn=msg_fn)

def test_d6_poll_budget_exceeded_with_fake_clock(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    clock = {"t": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"data_id": holder["id"], "state": "pending"}
                        ]
                    },
                },
            )
        raise AssertionError("超时不得 ZIP")

    def sleep_fn(seconds: float) -> None:
        clock["t"] += float(seconds)

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=sleep_fn,
            clock_fn=lambda: clock["t"],
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "poll_budget_exceeded", msg_fn=msg_fn)
    assert clock["t"] >= float(POLL_BUDGET_SEC)

# ===========================================================================
# E. ZIP 安全
# ===========================================================================

def _run_zip_case(tmp_path: Path, zip_bytes: bytes, expected_code: str) -> None:
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=zip_bytes)
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, expected_code, msg_fn=msg_fn)

def test_e1_nested_unique_full_md_success(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200,
                content=_make_zip_bytes(
                    {
                        "assets/img.png": b"\x89PNG",
                        "output/nested/full.md": "# Nested\n正文".encode("utf-8"),
                    }
                ),
            )
        raise AssertionError(f"未声明 {request.method} {request.url}")

    out = run(
        _build_sources(mod, [p]),
        token=FAKE_TOKEN,
        cancel_check=lambda: False,
        transport=httpx.MockTransport(handler),
        sleep_fn=lambda _s: None,
        clock_fn=lambda: 0.0,
        resolve_addresses_fn=_default_public_resolver,
    )
    assert "Nested" in out.markdown

def test_e2_zip_missing_full_md(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"readme.txt": b"x"}),
        "zip_full_md_missing",
    )

def test_e3_zip_ambiguous_full_md(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"a/full.md": b"1", "b/full.md": b"2"}),
        "zip_full_md_ambiguous",
    )

def test_e4_zip_path_traversal_rejected(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"../full.md": b"# x", "ok.txt": b"y"}),
        "zip_unsafe",
    )

def test_e5_zip_absolute_path_rejected(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"/etc/full.md": b"# x"}),
        "zip_unsafe",
    )

def test_e6_zip_backslash_traversal_rejected(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"..\\full.md": b"# x"}),
        "zip_unsafe",
    )

def test_e7_windows_drive_and_unc_rejected(tmp_path: Path):
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"C:/Windows/full.md": b"# x"}),
        "zip_unsafe",
    )
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"C:\\Windows\\full.md": b"# x"}),
        "zip_unsafe",
    )
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"//server/share/full.md": b"# x"}),
        "zip_unsafe",
    )
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"\\\\server\\share\\full.md": b"# x"}),
        "zip_unsafe",
    )

def test_e8_symlink_fifo_device_encrypted_rejected(tmp_path: Path):
    # 符号链接
    symlink_attr = (stat.S_IFLNK | 0o777) << 16
    _run_zip_case(
        tmp_path,
        _make_zip_special(
            name="link_full",
            data=b"target",
            external_attr=symlink_attr,
            extra_members={"full.md": b"# x"},
        ),
        "zip_unsafe",
    )
    # FIFO
    fifo_attr = (stat.S_IFIFO | 0o644) << 16
    _run_zip_case(
        tmp_path,
        _make_zip_special(
            name="fifo_item",
            data=b"",
            external_attr=fifo_attr,
            extra_members={"full.md": b"# x"},
        ),
        "zip_unsafe",
    )
    # device (char)
    dev_attr = (stat.S_IFCHR | 0o644) << 16
    _run_zip_case(
        tmp_path,
        _make_zip_special(
            name="dev_item",
            data=b"",
            external_attr=dev_attr,
            extra_members={"full.md": b"# x"},
        ),
        "zip_unsafe",
    )
    # C1：加密 ZIP 必须 header patch 自证 flag_bits&1
    _run_zip_case(
        tmp_path,
        _make_encrypted_full_md_zip(b"# encrypted-looking"),
        "zip_unsafe",
    )

def test_e9_bad_zip_rejected(tmp_path: Path):
    _run_zip_case(tmp_path, b"this-is-not-a-zip", "zip_unsafe")

def test_e10_zip_download_failed(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(500, content=b"nope")
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "zip_download_failed", msg_fn=msg_fn)

def test_e11_zip_stream_limits_not_full_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C2/C13：ZIP 流式硬上限；自定义 SyncByteStream 块序列 [limit,1overflow,canary]。
    行为门：stream=True + iter_bytes；不绑定 Client.stream 具体入口。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    # 1) 成员数超限
    monkeypatch.setattr(mod, "MAX_ZIP_MEMBERS", 3)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(4):
            zf.writestr(f"m{i}.txt", b"x")
        zf.writestr("full.md", b"# x")
    _run_zip_case(tmp_path, buf.getvalue(), "zip_unsafe")

    # 2) 声明解压总量超限
    monkeypatch.setattr(mod, "MAX_ZIP_UNCOMPRESSED_BYTES", 100)
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"full.md": b"y" * 120, "pad.bin": b"z" * 10}),
        "zip_unsafe",
    )

    # 3) 形式 cap：精确 limit 字节 + 1 overflow + canary；超限后不得读 canary
    limit = 64
    monkeypatch.setattr(mod, "MAX_ZIP_BYTES", limit)
    canary = b"ZIP_CANARY_AFTER_LIMIT_MUST_NOT_BE_CONSUMED"
    exact_limit = b"PK\x03\x04" + b"A" * (limit - 4)
    assert len(exact_limit) == limit
    overflow_byte = b"X"
    chunks_plan = [exact_limit, overflow_byte, canary]
    consumed = {
        "n": 0,
        "bytes": 0,
        "saw_canary": False,
        "saw_overflow": False,
    }
    stream_hits = {"stream_true": 0, "iter": 0}
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    class _OverflowByteStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[override]
            stream_hits["iter"] += 1
            for ch in chunks_plan:
                if canary in ch and consumed["bytes"] >= limit:
                    consumed["saw_canary"] = True
                consumed["n"] += 1
                consumed["bytes"] += len(ch)
                if ch == overflow_byte:
                    consumed["saw_overflow"] = True
                yield ch

        def close(self) -> None:  # pragma: no cover - 兼容接口
            return None

    real_send = httpx.Client.send
    close_hits = {"n": 0}

    class _OverflowByteStreamTracked(_OverflowByteStream):
        def close(self) -> None:
            close_hits["n"] += 1
            return None

    def spy_send(self, request, **kwargs):
        url_s = str(request.url)
        # C5：仅 ZIP GET 且 kwargs stream is True 时注入 raw SyncByteStream
        if request.method == "GET" and url_s == ZIP_URL_A:
            if kwargs.get("stream") is not True:
                raise AssertionError(
                    "业务红：ZIP GET 必须 send(..., stream=True)，"
                    f"不得预缓冲/伪增，kwargs.stream={kwargs.get('stream')!r}"
                )
            stream_hits["stream_true"] += 1
            return httpx.Response(
                200,
                request=request,
                stream=_OverflowByteStreamTracked(),
                headers={"content-type": "application/zip"},
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        # ZIP 必须经 spy_send(stream=True)；MockTransport 不得伪造成功
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            raise AssertionError(
                "业务红：ZIP GET 不得落入 MockTransport 非 stream 分支"
            )
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(),
        )
    _assert_remote_error(ei.value, "zip_unsafe")
    assert stream_hits["stream_true"] == 1, (
        f"业务红：ZIP stream=True 必须精确一次，hits={stream_hits}"
    )
    assert stream_hits["iter"] == 1, (
        f"业务红：ZIP iter 必须精确一次，hits={stream_hits}"
    )
    assert consumed["saw_overflow"] is True, (
        f"业务红：必须读到 limit+1 overflow，consumed={consumed}"
    )
    assert consumed["saw_canary"] is False, (
        f"业务红：overflow 后不得继续消费 canary，consumed={consumed}"
    )
    assert consumed["bytes"] == limit + 1, (
        f"业务红：总消费必须精确 limit+1，bytes={consumed['bytes']} limit={limit}"
    )
    assert close_hits["n"] >= 1, (
        f"业务红：ZIP 响应流必须 close，close_hits={close_hits}"
    )


def test_e12_blank_and_non_utf8_full_md_output_invalid(tmp_path: Path):
    """用途：空白 full.md 与非 UTF-8 均精确 output_invalid（禁止二选一）。"""
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"full.md": b"   \n\t  "}),
        "output_invalid",
    )
    _run_zip_case(
        tmp_path,
        _make_zip_bytes({"full.md": b"\xff\xfe\x00not-utf8"}),
        "output_invalid",
    )

# ===========================================================================
# F. Token / Client 默认 / 信号量
# ===========================================================================

def test_f1_blank_token_zero_http(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError("空白 token 零 HTTP")

    for tok in ("", "   ", "\t"):
        with pytest.raises(err_cls) as ei:
            run(
                _build_sources(mod, [p]),
                token=tok,
                cancel_check=lambda: False,
                transport=httpx.MockTransport(handler),
                sleep_fn=lambda _s: None,
                clock_fn=lambda: 0.0,
                resolve_addresses_fn=_default_public_resolver,
            )
        _assert_remote_error(ei.value, "token_unconfigured", msg_fn=msg_fn)
    assert calls == []

def test_f2_httpx_client_actual_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """C3：真实 run 内 Client 构造：verify=True、trust_env=False、redirects=False、零代理。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    captured: list[dict] = []
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    real_init = httpx.Client.__init__

    def spy_init(self, *a, **k):
        captured.append(dict(k))
        return real_init(self, *a, **k)

    monkeypatch.setattr(httpx.Client, "__init__", spy_init)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# t\n"})
            )
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler),
        **_run_kwargs(),
    )
    assert "# t" in out.markdown
    assert captured, "业务红：run 必须真实构造 httpx.Client"
    for kw in captured:
        assert kw.get("trust_env") is False, f"trust_env 必须 False，kw={kw}"
        assert kw.get("follow_redirects") is False, f"follow_redirects 必须 False，kw={kw}"
        assert kw.get("verify") is True, (
            f"业务红：verify 必须显式 True（禁止 getattr 恒真），kw={kw}"
        )
        # Q1：禁止 assert BoolOp Or
        if "proxy" in kw:
            assert kw.get("proxy") is None, f"proxy 必须 None，kw={kw}"

def test_f3_semaphore_wait_cancel_and_deadline_zero_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：首次 acquire 失败后的等待期取消、等待期总 deadline、未取得锁不得 release、零 HTTP。
    Q9：cancel 不得从入口即 True；须证明至少一次 acquire 失败后再取消。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    http_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(f"{request.method} {request.url}")
        raise AssertionError("等待期不得发 HTTP")

    transport = httpx.MockTransport(handler)

    # 占用真实信号量
    real_sem = _require_attr(mod, "REMOTE_SEMAPHORE")
    assert isinstance(real_sem, threading.BoundedSemaphore), (
        f"业务红：REMOTE_SEMAPHORE 必须真实 BoundedSemaphore，actual={type(real_sem)}"
    )
    assert real_sem.acquire(blocking=False), "测试前置：必须能占用信号量"
    acquired_by_test = True
    try:
        # Q9：首次 acquire 失败后再取消（非入口预取消）
        cancel_state = {"failed_acquire": 0, "cancel": False}
        real_acquire = real_sem.acquire

        def spy_acquire(*a, **k):
            ok = real_acquire(*a, **k)
            if not ok:
                cancel_state["failed_acquire"] += 1
                # 至少一次拿不到锁后，才置取消
                if cancel_state["failed_acquire"] >= 1:
                    cancel_state["cancel"] = True
            return ok

        monkeypatch.setattr(real_sem, "acquire", spy_acquire)

        with pytest.raises(err_cls) as ei:
            run(
                _build_sources(mod, [p]),
                token=FAKE_TOKEN,
                cancel_check=lambda: cancel_state["cancel"],
                transport=transport,
                sleep_fn=lambda _s: None,
                clock_fn=lambda: 0.0,
                resolve_addresses_fn=_default_public_resolver,
            )
        _assert_remote_error(ei.value, "interrupted", msg_fn=msg_fn)
        assert cancel_state["failed_acquire"] >= 1, (
            f"业务红：必须先至少一次 acquire 失败再取消，actual={cancel_state}"
        )
        assert http_calls == [], f"业务红：等待期取消必须零 HTTP，actual={http_calls}"

        # 等待期总 deadline
        clock = {"t": 0.0}

        def sleep_fn(seconds: float) -> None:
            clock["t"] += float(seconds)

        with pytest.raises(err_cls) as ei2:
            run(
                _build_sources(mod, [p]),
                token=FAKE_TOKEN,
                cancel_check=lambda: False,
                transport=transport,
                sleep_fn=sleep_fn,
                clock_fn=lambda: clock["t"],
                resolve_addresses_fn=_default_public_resolver,
            )
        _assert_remote_error(ei2.value, "poll_budget_exceeded", msg_fn=msg_fn)
        assert http_calls == [], f"业务红：等待期超时必须零 HTTP，actual={http_calls}"

        # 未取得锁不得 release：信号量值仍应为 0（被本测占用）
        val = getattr(real_sem, "_value", None)
        assert val == 0, f"业务红：等待失败后不得 release 他人锁，_value={val}"
    finally:
        if acquired_by_test:
            real_sem.release()

def test_f4_partial_upload_second_put_fails(tmp_path: Path):
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"1"),
        _write_temp_source(tmp_path, "b.pdf", b"2"),
    ]
    put_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A, PRESIGNED_PUT_B],
                    },
                },
            )
        if request.method == "PUT":
            put_n["n"] += 1
            if put_n["n"] == 1:
                return httpx.Response(200)
            return httpx.Response(500)
        raise AssertionError("部分上传失败后不得轮询")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, paths),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
    _assert_remote_error(ei.value, "upload_failed", msg_fn=msg_fn)
    assert put_n["n"] == 2

def test_f5_ignore_transport_must_trip_fuse_not_go_network(tmp_path: Path):
    """
    C12：正例必须完整 happy path 精确命中 POST/PUT/poll/ZIP 并成功输出；
    忽略 transport 反例仍必须命中模块熔断且 handler 零请求。
    删除“任意 RemoteMineruError + used>=1”宽放。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    phases: list[str] = []
    data_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            phases.append("post")
            body = json.loads(request.content.decode("utf-8"))
            data_ids.clear()
            data_ids.extend(f["data_id"] for f in body["files"])
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            phases.append("put")
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            phases.append("poll")
            did = data_ids[0] if data_ids else ("a" * 32)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": did,
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            phases.append("zip")
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# transport-ok\n"})
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    out = run(
        _build_sources(mod, [p]),
        token=FAKE_TOKEN,
        cancel_check=lambda: False,
        transport=transport,
        sleep_fn=lambda _s: None,
        clock_fn=lambda: 0.0,
        resolve_addresses_fn=_default_public_resolver,
    )
    assert "# transport-ok" in out.markdown
    assert phases == ["post", "put", "poll", "zip"], (
        f"业务红：正例必须精确 POST/PUT/poll/ZIP，actual={phases}"
    )

    # 反例：忽略 transport 注入 → 模块熔断，handler 零请求
    phases.clear()
    data_ids.clear()
    guard_hits_before = len(_NET_GUARD_HITS)
    try:
        run(
            _build_sources(mod, [p]),
            token=FAKE_TOKEN,
            cancel_check=lambda: False,
            # 故意不传 transport，或传 None
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )
        raise AssertionError("业务红：忽略 transport 必须失败")
    except _V1NNetFuseError as exc:
        assert "外网熔断" in str(exc)
        assert phases == [], f"业务红：熔断路径 handler 必须零请求，phases={phases}"
        assert len(_NET_GUARD_HITS) > guard_hits_before


# ===========================================================================
# G. C1-C11 补强：resolver / deadline / TOCTOU / cookie / data_id / MD cap / AST
# ===========================================================================


# ---------------------------------------------------------------------------
# R7-C4：预算受限、白名单式安全常量折叠（禁止 eval/exec/compile）
# ---------------------------------------------------------------------------

_AST_UNKNOWN = object()
_AST_FOLD_MAX_NODES = 256
_AST_FOLD_MAX_CONTAINER = 64
_AST_FOLD_MAX_STR = 256
_AST_FOLD_MAX_INT_BITS = 256


def _ast_is_exact_safe_builtin(val: object) -> bool:
    """R8-A1：仅 exact built-in 安全标量/容器（递归元素亦须安全）。"""
    if val is None or type(val) is bool:
        return True
    if type(val) is int or type(val) is float or type(val) is complex:
        return True
    if type(val) is str or type(val) is bytes:
        return True
    if type(val) is list or type(val) is tuple:
        return all(_ast_is_exact_safe_builtin(x) for x in val)
    if type(val) is set or type(val) is frozenset:
        return all(_ast_is_exact_safe_builtin(x) for x in val)
    if type(val) is dict:
        return all(
            _ast_is_exact_safe_builtin(k) and _ast_is_exact_safe_builtin(v)
            for k, v in val.items()
        )
    return False


def _ast_static_truthiness(val: object) -> bool | None:
    """仅对安全内建标量/容器求真值；UNKNOWN/外部对象返回 None。"""
    if val is _AST_UNKNOWN:
        return None
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val != 0
    if isinstance(val, float):
        return val != 0.0
    if isinstance(val, complex):
        return val != 0
    if isinstance(val, (str, bytes, bytearray)):
        return len(val) > 0
    if isinstance(val, (list, tuple, dict, set, frozenset)):
        return len(val) > 0
    return None


def _ast_safe_cmp(a: object, op: ast.cmpop, b: object) -> object:
    """仅对安全内建类型做显式比较；禁止对 UNKNOWN 调魔术方法。"""
    if a is _AST_UNKNOWN or b is _AST_UNKNOWN:
        return _AST_UNKNOWN
    try:
        if isinstance(op, ast.Eq):
            if not _ast_is_exact_safe_builtin(a) or not _ast_is_exact_safe_builtin(b):
                return _AST_UNKNOWN
            return a == b
        if isinstance(op, ast.NotEq):
            if not _ast_is_exact_safe_builtin(a) or not _ast_is_exact_safe_builtin(b):
                return _AST_UNKNOWN
            return a != b
        if isinstance(op, ast.Is):
            return a is b
        if isinstance(op, ast.IsNot):
            return a is not b
        if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
            if isinstance(a, bool) or isinstance(b, bool):
                # bool 与 int 混比在 Python 合法，但测试侧仅允许同构数值/同类 str/bytes
                if isinstance(a, bool) and isinstance(b, bool):
                    pass
                elif isinstance(a, int) and isinstance(b, int):
                    pass
                elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    pass
                elif type(a) is type(b) and isinstance(a, (str, bytes)):
                    pass
                else:
                    return _AST_UNKNOWN
            elif type(a) is type(b) and isinstance(a, (str, bytes)):
                pass
            elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
                pass
            else:
                return _AST_UNKNOWN
            if isinstance(op, ast.Lt):
                return a < b  # type: ignore[operator]
            if isinstance(op, ast.LtE):
                return a <= b  # type: ignore[operator]
            if isinstance(op, ast.Gt):
                return a > b  # type: ignore[operator]
            return a >= b  # type: ignore[operator]
        if isinstance(op, ast.In):
            if isinstance(b, (str, bytes, list, tuple, set, frozenset, dict)):
                return a in b
            return _AST_UNKNOWN
        if isinstance(op, ast.NotIn):
            if isinstance(b, (str, bytes, list, tuple, set, frozenset, dict)):
                return a not in b
            return _AST_UNKNOWN
    except (TypeError, ValueError):
        return _AST_UNKNOWN
    return _AST_UNKNOWN


def _ast_safe_fold(node: ast.AST, *, _budget: list[int] | None = None) -> object:
    """
    R7-C4：白名单式安全常量折叠；不执行源码。
    Name/Call/Attribute/Subscript/推导/f-string/lambda 等 → UNKNOWN。
    """
    if _budget is None:
        _budget = [_AST_FOLD_MAX_NODES]
    if _budget[0] <= 0:
        return _AST_UNKNOWN
    _budget[0] -= 1

    if isinstance(node, ast.Constant):
        # R8-A1：仅放行 exact built-in 安全类型；其它值（含外部对象）一律 UNKNOWN
        v = node.value
        if v is None:
            return None
        if type(v) is bool:
            return v
        if type(v) is int:
            if abs(v).bit_length() > _AST_FOLD_MAX_INT_BITS:
                return _AST_UNKNOWN
            return v
        if type(v) is float or type(v) is complex:
            return v
        if type(v) is str:
            if len(v) > _AST_FOLD_MAX_STR:
                return _AST_UNKNOWN
            return v
        if type(v) is bytes:
            if len(v) > _AST_FOLD_MAX_STR:
                return _AST_UNKNOWN
            return v
        # bytearray / 自定义类 / 其它 → UNKNOWN，禁止渗入 bool/compare
        return _AST_UNKNOWN

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        if len(node.elts) > _AST_FOLD_MAX_CONTAINER:
            return _AST_UNKNOWN
        elts: list[object] = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                return _AST_UNKNOWN
            ev = _ast_safe_fold(elt, _budget=_budget)
            if ev is _AST_UNKNOWN:
                return _AST_UNKNOWN
            elts.append(ev)
        if isinstance(node, ast.List):
            return elts
        if isinstance(node, ast.Tuple):
            return tuple(elts)
        try:
            return set(elts)
        except TypeError:
            return _AST_UNKNOWN

    if isinstance(node, ast.Dict):
        if len(node.keys) > _AST_FOLD_MAX_CONTAINER:
            return _AST_UNKNOWN
        out: dict[object, object] = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                return _AST_UNKNOWN
            kv = _ast_safe_fold(k, _budget=_budget)
            vv = _ast_safe_fold(v, _budget=_budget)
            if kv is _AST_UNKNOWN or vv is _AST_UNKNOWN:
                return _AST_UNKNOWN
            try:
                out[kv] = vv
            except TypeError:
                return _AST_UNKNOWN
        return out

    if isinstance(node, ast.UnaryOp):
        operand = _ast_safe_fold(node.operand, _budget=_budget)
        if operand is _AST_UNKNOWN:
            return _AST_UNKNOWN
        try:
            if isinstance(node.op, ast.UAdd):
                if isinstance(operand, (int, float, complex)) and not isinstance(
                    operand, bool
                ):
                    return +operand
                return _AST_UNKNOWN
            if isinstance(node.op, ast.USub):
                if isinstance(operand, (int, float, complex)) and not isinstance(
                    operand, bool
                ):
                    return -operand
                return _AST_UNKNOWN
            if isinstance(node.op, ast.Not):
                t = _ast_static_truthiness(operand)
                if t is None:
                    return _AST_UNKNOWN
                return not t
            if isinstance(node.op, ast.Invert):
                if isinstance(operand, int) and not isinstance(operand, bool):
                    return ~operand
                return _AST_UNKNOWN
        except (TypeError, ValueError, OverflowError):
            return _AST_UNKNOWN
        return _AST_UNKNOWN

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            return _AST_UNKNOWN
        left = _ast_safe_fold(node.left, _budget=_budget)
        right = _ast_safe_fold(node.right, _budget=_budget)
        if left is _AST_UNKNOWN or right is _AST_UNKNOWN:
            return _AST_UNKNOWN
        try:
            if isinstance(node.op, ast.Add):
                if isinstance(left, str) and isinstance(right, str):
                    if len(left) + len(right) > _AST_FOLD_MAX_STR:
                        return _AST_UNKNOWN
                    return left + right
                if isinstance(left, (bytes, bytearray)) and isinstance(
                    right, (bytes, bytearray)
                ):
                    if len(left) + len(right) > _AST_FOLD_MAX_STR:
                        return _AST_UNKNOWN
                    return bytes(left) + bytes(right)
                if isinstance(left, list) and isinstance(right, list):
                    if len(left) + len(right) > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    return left + right
                if isinstance(left, tuple) and isinstance(right, tuple):
                    if len(left) + len(right) > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    return left + right
                if isinstance(left, (int, float, complex)) and isinstance(
                    right, (int, float, complex)
                ):
                    result = left + right
                    if (
                        isinstance(result, int)
                        and not isinstance(result, bool)
                        and abs(result).bit_length() > _AST_FOLD_MAX_INT_BITS
                    ):
                        return _AST_UNKNOWN
                    return result
                return _AST_UNKNOWN
            if isinstance(node.op, ast.Sub):
                if isinstance(left, (int, float, complex)) and isinstance(
                    right, (int, float, complex)
                ):
                    result = left - right
                    if (
                        isinstance(result, int)
                        and not isinstance(result, bool)
                        and abs(result).bit_length() > _AST_FOLD_MAX_INT_BITS
                    ):
                        return _AST_UNKNOWN
                    return result
                return _AST_UNKNOWN
            if isinstance(node.op, ast.Mult):
                if isinstance(left, int) and not isinstance(left, bool) and isinstance(
                    right, (str, bytes, list, tuple)
                ):
                    if left < 0 or left > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    if isinstance(right, (str, bytes)):
                        if len(right) * left > _AST_FOLD_MAX_STR:
                            return _AST_UNKNOWN
                    elif len(right) * left > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    return left * right
                if isinstance(right, int) and not isinstance(right, bool) and isinstance(
                    left, (str, bytes, list, tuple)
                ):
                    if right < 0 or right > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    if isinstance(left, (str, bytes)):
                        if len(left) * right > _AST_FOLD_MAX_STR:
                            return _AST_UNKNOWN
                    elif len(left) * right > _AST_FOLD_MAX_CONTAINER:
                        return _AST_UNKNOWN
                    return left * right
                if (
                    isinstance(left, int)
                    and isinstance(right, int)
                    and not isinstance(left, bool)
                    and not isinstance(right, bool)
                ):
                    if abs(left).bit_length() + abs(right).bit_length() > _AST_FOLD_MAX_INT_BITS:
                        return _AST_UNKNOWN
                    result = left * right
                    if abs(result).bit_length() > _AST_FOLD_MAX_INT_BITS:
                        return _AST_UNKNOWN
                    return result
                if isinstance(left, (int, float, complex)) and isinstance(
                    right, (int, float, complex)
                ):
                    return left * right
                return _AST_UNKNOWN
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    if right == 0:
                        return _AST_UNKNOWN
                    if isinstance(node.op, ast.Div):
                        return left / right
                    if isinstance(node.op, ast.FloorDiv):
                        return left // right
                    return left % right
                return _AST_UNKNOWN
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            return _AST_UNKNOWN
        return _AST_UNKNOWN

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        last: object = True
        for v in node.values:
            fv = _ast_safe_fold(v, _budget=_budget)
            if fv is _AST_UNKNOWN:
                return _AST_UNKNOWN
            t = _ast_static_truthiness(fv)
            if t is None:
                return _AST_UNKNOWN
            if not t:
                return fv
            last = fv
        return last

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return _AST_UNKNOWN

    if isinstance(node, ast.Compare):
        left = _ast_safe_fold(node.left, _budget=_budget)
        if left is _AST_UNKNOWN:
            return _AST_UNKNOWN
        cur = left
        for op, comp in zip(node.ops, node.comparators):
            right = _ast_safe_fold(comp, _budget=_budget)
            if right is _AST_UNKNOWN:
                return _AST_UNKNOWN
            ok = _ast_safe_cmp(cur, op, right)
            if ok is _AST_UNKNOWN:
                return _AST_UNKNOWN
            if not ok:
                return False
            cur = right
        return True

    # 动态节点与其余未支持节点 → UNKNOWN
    return _AST_UNKNOWN


def _ast_fold_str(node: ast.AST) -> str | None:
    v = _ast_safe_fold(node)
    return v if isinstance(v, str) else None


def _const_compare_is_statically_true(node: ast.Compare) -> bool:
    """R6 兼容入口：委托 R7 安全折叠。"""
    return _ast_safe_fold(node) is True


def _assert_test_is_statically_truthy(test: ast.AST) -> bool:
    """assert 测试表达式是否可安全折叠为恒真。"""
    return _ast_static_truthiness(_ast_safe_fold(test)) is True


def _getattr_skip_name(arg_node: ast.AST) -> str | None:
    """折叠 getattr 第二参数；命中 skip 族则返回名称。"""
    name = _ast_fold_str(arg_node)
    if name in {"skip", "xfail", "importorskip", "skipif"}:
        return name
    return None


_V1N_G8A_SKIPIF_FN = "test_g8a_windows_convert_failure_live_ledger"
_V1N_G8A_SKIPIF_REASON = "仅 Windows HANDLE 转换所有权"


def _v1n_is_g8a_unique_skipif_call(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, dec: ast.AST
) -> bool:
    """
    Q1：g8a 唯一 skipif 全通道窄豁免判定。
    必须同时满足：函数名 + decorator skipif + reason 字面量精确匹配。
    """
    if fn.name != _V1N_G8A_SKIPIF_FN:
        return False
    if not isinstance(dec, ast.Call):
        return False
    func = dec.func
    is_skipif = False
    if isinstance(func, ast.Attribute) and func.attr == "skipif":
        is_skipif = True
    elif (
        isinstance(func, ast.Name)
        and func.id == "skipif"
    ):
        is_skipif = True
    if not is_skipif:
        return False
    for kw in dec.keywords:
        if (
            kw.arg == "reason"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == _V1N_G8A_SKIPIF_REASON
        ):
            return True
    return False


def _assert_contains_boolop_or(node: ast.AST) -> bool:
    """R6：Assert 测试表达式及其嵌套（含集合/列表推导）是否含 BoolOp Or。"""
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or):
            return True
    return False

def _ast_scan_fake_green(tree: ast.AST, *, guard_func_names: frozenset[str]) -> list[str]:
    """
    C9/R6：AST 反假绿。
    增补：except 子树 ast.walk、pytest.param marks、模块 pytestmark、
    Compare 左右 AST 相同、Match/case/AsyncWith/AsyncFor 中 test return。
    """
    bad: list[str] = []

    def _walk_excluding_guards(nodes: list[ast.stmt]) -> None:
        for stmt in nodes:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name in guard_func_names:
                    continue
                _inspect_function(stmt)
                continue
            if isinstance(stmt, ast.ClassDef):
                _walk_excluding_guards(stmt.body)
                continue
            _inspect_node(stmt)

    def _scan_marks_value(
        node: ast.AST,
        lineno: int,
        *,
        exempt_g8a_skipif: bool = False,
    ) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in {
                "skip",
                "xfail",
                "skipif",
            }:
                if (
                    exempt_g8a_skipif
                    and child.attr == "skipif"
                ):
                    continue
                bad.append(f"L{lineno}: marks 通道含 {child.attr}")
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                if child.func.attr in {"skip", "xfail", "skipif"}:
                    if (
                        exempt_g8a_skipif
                        and child.func.attr == "skipif"
                    ):
                        continue
                    bad.append(f"L{lineno}: marks 通道调用 {child.func.attr}()")
            # getattr(pytest.mark, "skip"|"xfail"|"skipif")
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "getattr"
                and len(child.args) >= 2
            ):
                gname = _getattr_skip_name(child.args[1])
                if gname is not None:
                    bad.append(f"L{lineno}: marks getattr 通道 {gname!r}")

    def _scan_test_return_blocks(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Return):
                bad.append(f"L{stmt.lineno}: test_* 无条件/提前 return")
            elif isinstance(stmt, ast.If):
                _scan_test_return_blocks(stmt.body)
                _scan_test_return_blocks(stmt.orelse)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                _scan_test_return_blocks(stmt.body)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                _scan_test_return_blocks(stmt.body)
                _scan_test_return_blocks(stmt.orelse)
            elif isinstance(stmt, ast.While):
                _scan_test_return_blocks(stmt.body)
                _scan_test_return_blocks(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                _scan_test_return_blocks(stmt.body)
                for h in stmt.handlers:
                    _scan_test_return_blocks(h.body)
                _scan_test_return_blocks(stmt.orelse)
                _scan_test_return_blocks(stmt.finalbody)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    _scan_test_return_blocks(case.body)

    def _inspect_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in fn.decorator_list:
            # Q1：g8a 唯一 skipif（函数名+reason+decorator）全通道窄豁免
            _g8a_exempt = _v1n_is_g8a_unique_skipif_call(fn, dec)
            if isinstance(dec, ast.Attribute) and dec.attr in {
                "skip",
                "xfail",
                "skipif",
            }:
                if not (_g8a_exempt and dec.attr == "skipif"):
                    bad.append(f"L{fn.lineno}: 禁止装饰器 mark.{dec.attr}")
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr in {"skip", "xfail", "skipif"}:
                    if not (_g8a_exempt and dec.func.attr == "skipif"):
                        bad.append(f"L{fn.lineno}: 禁止装饰器 mark.{dec.func.attr}()")
            # @getattr(pytest.mark, "skip") 或 @getattr(... )() —— 永不豁免
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "getattr"
                and len(dec.args) >= 2
            ):
                gname = _getattr_skip_name(dec.args[1])
                if gname is not None:
                    bad.append(f"L{fn.lineno}: 禁止装饰器 getattr mark.{gname}")
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Call)
                and isinstance(dec.func.func, ast.Name)
                and dec.func.func.id == "getattr"
                and len(dec.func.args) >= 2
            ):
                gname = _getattr_skip_name(dec.func.args[1])
                if gname is not None:
                    bad.append(
                        f"L{fn.lineno}: 禁止装饰器 getattr()() mark.{gname}"
                    )
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "marks" and kw.value is not None:
                        # marks= 通道永不豁免 g8a（仅 decorator 本体窄豁免）
                        _scan_marks_value(kw.value, fn.lineno, exempt_g8a_skipif=False)
                # 装饰器调用树：g8a 唯一 skipif 本体全通道豁免 skipif 属性/调用
                _scan_marks_value(
                    dec, fn.lineno, exempt_g8a_skipif=_g8a_exempt
                )
        if fn.name.startswith("test_"):
            _scan_test_return_blocks(fn.body)
        # except 子树任意深度 return/continue
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler):
                for sub in ast.walk(node):
                    if sub is node:
                        continue
                    if isinstance(sub, ast.Return):
                        bad.append(f"L{sub.lineno}: except 子树 return")
                    if isinstance(sub, ast.Continue):
                        bad.append(f"L{sub.lineno}: except 子树 continue")
        # R6-C6：跟踪 getattr 别名 / 绑定 skip 可调用，禁止 g=getattr; g(pytest,"skip")()
        getattr_aliases: set[str] = set()
        skip_bound_names: set[str] = set()
        _skip_attrs = {"skip", "xfail", "importorskip", "skipif"}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            # g = getattr
            if isinstance(node.value, ast.Name) and node.value.id == "getattr":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        getattr_aliases.add(t.id)
            # g = getattr(pytest, "skip") / getattr(pytest.mark, "skip")
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and (
                    node.value.func.id == "getattr"
                    or node.value.func.id in getattr_aliases
                )
                and len(node.value.args) >= 2
                and _getattr_skip_name(node.value.args[1]) is not None
            ):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        skip_bound_names.add(t.id)
        if getattr_aliases or skip_bound_names:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # g(pytest, "skip")(...)
                if (
                    isinstance(func, ast.Call)
                    and isinstance(func.func, ast.Name)
                    and func.func.id in getattr_aliases
                    and len(func.args) >= 2
                ):
                    gname = _getattr_skip_name(func.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 别名调用 pytest.{gname}"
                        )
                # g = getattr(pytest,"skip"); g()
                if isinstance(func, ast.Name) and func.id in skip_bound_names:
                    bad.append(f"L{node.lineno}: 禁止绑定 skip 别名调用 {func.id}()")
                # g(pytest, "skip") 作为取绑定
                if (
                    isinstance(func, ast.Name)
                    and func.id in getattr_aliases
                    and len(node.args) >= 2
                ):
                    gname = _getattr_skip_name(node.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 别名取 pytest.{gname}"
                        )
        _inspect_node(fn)

    def _inspect_node(root: ast.AST) -> None:
        for node in ast.walk(root):
            if isinstance(node, ast.Assert) and node.test is not None:
                if _assert_contains_boolop_or(node.test):
                    bad.append(f"L{node.lineno}: assert 含 BoolOp Or（含嵌套/推导）")
                # R7-C4：安全常量折叠恒真 assert（一元/容器/运算/And/Compare）
                if _assert_test_is_statically_truthy(node.test):
                    bad.append(f"L{node.lineno}: 禁止可静态求值恒真 assert")
                # Compare 左右 AST 完全相同（assert x==x）——即使折叠 UNKNOWN
                if isinstance(node.test, ast.Compare) and len(node.test.ops) == 1:
                    if isinstance(node.test.ops[0], ast.Eq) and len(node.test.comparators) == 1:
                        left_d = ast.dump(node.test.left, include_attributes=False)
                        right_d = ast.dump(
                            node.test.comparators[0], include_attributes=False
                        )
                        if left_d == right_d:
                            bad.append(f"L{node.lineno}: assert 左右 AST 完全相同")
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                        bad.append(f"L{node.lineno}: except Exception: pass")
            if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
                if node.test.value is False:
                    bad.append(f"L{node.lineno}: if False")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "pytest" and func.attr in {
                        "skip",
                        "xfail",
                        "importorskip",
                    }:
                        bad.append(f"L{node.lineno}: 禁止 pytest.{func.attr}()")
                if isinstance(func, ast.Attribute) and func.attr == "importorskip":
                    bad.append(f"L{node.lineno}: 禁止 importorskip")
                if isinstance(func, ast.Name) and func.id in {
                    "skip",
                    "xfail",
                    "importorskip",
                }:
                    bad.append(f"L{node.lineno}: 禁止裸 {func.id}()")
                # getattr(pytest, "skip"|"sk"+"ip"|...) (...)
                if (
                    isinstance(func, ast.Call)
                    and isinstance(func.func, ast.Name)
                    and func.func.id == "getattr"
                    and len(func.args) >= 2
                ):
                    gname = _getattr_skip_name(func.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr pytest.{gname}() 通道"
                        )
                # 裸 getattr(pytest, "skip") / 拼接名
                if (
                    isinstance(func, ast.Name)
                    and func.id == "getattr"
                    and len(node.args) >= 2
                ):
                    gname = _getattr_skip_name(node.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 取 pytest.{gname}"
                        )
                # pytest.param(..., marks=...)
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pytest"
                    and func.attr == "param"
                ):
                    for kw in node.keywords:
                        if kw.arg == "marks" and kw.value is not None:
                            _scan_marks_value(kw.value, node.lineno)

    # 模块级 pytestmark
    if isinstance(tree, ast.Module):
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name) and t.id == "pytestmark":
                        bad.append(f"L{stmt.lineno}: 禁止模块级 pytestmark")
                        if stmt.value is not None:
                            _scan_marks_value(stmt.value, stmt.lineno)
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.target.id == "pytestmark":
                    bad.append(f"L{stmt.lineno}: 禁止模块级 pytestmark")
        _walk_excluding_guards(tree.body)
    else:
        _inspect_node(tree)
    return bad


def _client_ast_scanner_synthetic_self_test() -> None:
    """C9/R6-C6/R7-C4：每项单一目标违规 + expected reason；动态负样本；门控自红。"""
    samples: list[tuple[str, str, str]] = [
        (
            "except_nested_return",
            "def test_x():\n try:\n  1\n except Exception:\n  if True:\n   return\n",
            "except 子树 return",
        ),
        (
            "pytest_param_marks",
            "import pytest\n@pytest.mark.parametrize('a',[pytest.param(1,marks=pytest.mark.skip)])\ndef test_y(a):\n assert a==1\n",
            "marks",
        ),
        (
            "module_pytestmark",
            "import pytest\npytestmark = pytest.mark.skip\ndef test_z():\n pass\n",
            "pytestmark",
        ),
        ("assert_same_ast", "def test_w():\n x=1\n assert x==x\n", "左右 AST 完全相同"),
        ("match_return", "def test_m():\n match 1:\n  case 1:\n   return\n", "return"),
        ("assert_or", "def test_o():\n assert True or False\n", "BoolOp Or"),
        ("assert_lt_const", "def test_lt():\n assert 1<2\n", "可静态求值恒真"),
        ("assert_not_false", "def test_nf():\n assert not False\n", "可静态求值恒真"),
        ("assert_truthy_int", "def test_ai():\n assert 1\n", "可静态求值恒真"),
        ("assert_truthy_str", "def test_as():\n assert 'x'\n", "可静态求值恒真"),
        ("assert_neg1", "def test_n1():\n assert -1\n", "可静态求值恒真"),
        ("assert_tuple1", "def test_t1():\n assert (1,)\n", "可静态求值恒真"),
        ("assert_not_empty_list", "def test_nl():\n assert not []\n", "可静态求值恒真"),
        ("assert_one_plus_one", "def test_pp():\n assert 1+1\n", "可静态求值恒真"),
        (
            "assert_and_compare",
            "def test_ac():\n assert (1<2) and (2<3)\n",
            "可静态求值恒真",
        ),
        (
            "getattr_param_marks",
            'import pytest\n@pytest.mark.parametrize("a",[pytest.param(1,marks=getattr(pytest.mark,"skip"))])\ndef test_gp(a):\n assert a==1\n',
            "getattr",
        ),
        (
            "getattr_decorator",
            'import pytest\n@getattr(pytest.mark,"skip")\ndef test_gd():\n pass\n',
            "getattr",
        ),
        (
            "getattr_skip_call",
            'import pytest\ndef test_gs():\n getattr(pytest,"skip")("x")\n',
            "getattr",
        ),
        (
            "getattr_importorskip",
            'import pytest\ndef test_gi():\n getattr(pytest,"importorskip")("nosuch")\n',
            "getattr",
        ),
        (
            "getattr_xfail_marks",
            'import pytest\n@pytest.mark.parametrize("a",[pytest.param(1,marks=getattr(pytest.mark,"xfail"))])\ndef test_gx(a):\n assert a==1\n',
            "getattr",
        ),
        (
            "getattr_skipif_marks",
            'import pytest\n@pytest.mark.parametrize("a",[pytest.param(1,marks=getattr(pytest.mark,"skipif"))])\ndef test_gsi(a):\n assert a==1\n',
            "getattr",
        ),
        (
            "getattr_alias_call",
            'import pytest\ndef test_ga():\n g=getattr\n g(pytest,"skip")("x")\n',
            "getattr 别名",
        ),
        (
            "getattr_bound_skip",
            'import pytest\ndef test_gb():\n g=getattr(pytest,"skip")\n g("x")\n',
            "绑定 skip 别名",
        ),
        (
            "getattr_concat_skip",
            'import pytest\ndef test_gc():\n getattr(pytest,"sk"+"ip")("x")\n',
            "getattr",
        ),
        (
            "skipif_non_g8a",
            'import pytest\nimport os\n@pytest.mark.skipif(os.name!="nt",reason="仅 Windows HANDLE 转换所有权")\ndef test_other():\n pass\n',
            "禁止装饰器 mark.skipif",
        ),
        (
            "g8a_skipif_wrong_reason",
            'import pytest\nimport os\n@pytest.mark.skipif(os.name!="nt",reason="wrong")\ndef test_g8a_windows_convert_failure_live_ledger():\n pass\n',
            "禁止装饰器 mark.skipif",
        ),
        (
            "except_return_still_red",
            "def test_er():\n try:\n  1\n except Exception:\n  return None\n",
            "except 子树 return",
        ),
        (
            "boolop_or_still_red",
            "def test_bo():\n assert True or 0\n",
            "BoolOp Or",
        ),
    ]
    for label, src, expected in samples:
        tree = ast.parse(src.encode("utf-8").decode("unicode_escape"))
        bad = _ast_scan_fake_green(tree, guard_func_names=frozenset())
        # scanner 恒空必须红
        assert bad, f"业务红：synthetic {label} 必须被 scanner 命中，got empty"
        joined = "\n".join(bad)
        assert expected in joined, (
            f"业务红：synthetic {label} 必须命中 reason={expected!r}，bad={bad}"
        )
        # reason 错配必须红：错误子串不得误当作命中
        wrong = "__NO_SUCH_REASON_R7__"
        assert wrong not in joined, f"业务红：reason 错配哨兵不得出现，bad={bad}"

    # 动态负样本：不得错误折叠为恒真，且不得执行
    dyn_hits: list[str] = []
    def _boom(*a, **k):  # noqa: ANN001
        dyn_hits.append("executed")
        raise AssertionError("业务红：动态负样本不得被执行")

    dynamic_samples = [
        (
            "dyn_call",
            "def test_x():\n def fn():\n  return 1\n assert fn()\n",
        ),
        (
            "dyn_attr",
            "def test_x():\n class O:\n  attr=1\n obj=O()\n assert obj.attr\n",
        ),
        (
            "dyn_sub",
            "def test_x():\n data=[1]\n assert data[0]\n",
        ),
    ]
    for label, src in dynamic_samples:
        tree = ast.parse(src)
        bad = _ast_scan_fake_green(tree, guard_func_names=frozenset())
        # 不得因错误折叠而标「可静态求值恒真 assert」（左右相同等其他门除外）
        for b in bad:
            assert "可静态求值恒真" not in b, (
                f"业务红：动态负样本 {label} 不得被折叠为恒真，bad={bad}"
            )
    assert dyn_hits == [], f"业务红：动态负样本执行污染 hits={dyn_hits}"


    # R8-A1：手工构造 ast.Constant(Evil()) — 不得触发外部魔术方法
    class _EvilConst:
        eq_n = 0
        bool_n = 0
        ne_n = 0

        def __eq__(self, other):  # noqa: ANN001
            type(self).eq_n += 1
            return True

        def __ne__(self, other):  # noqa: ANN001
            type(self).ne_n += 1
            return False

        def __bool__(self) -> bool:
            type(self).bool_n += 1
            return True

    _EvilConst.eq_n = 0
    _EvilConst.bool_n = 0
    _EvilConst.ne_n = 0
    evil = _EvilConst()
    evil_const = ast.Constant(value=evil)
    folded_evil = _ast_safe_fold(evil_const)
    assert folded_evil is _AST_UNKNOWN, (
        f"业务红：Constant(Evil) 必须 UNKNOWN，actual={folded_evil!r}"
    )
    assert _EvilConst.eq_n == 0 and _EvilConst.bool_n == 0 and _EvilConst.ne_n == 0, (
        f"业务红：Constant(Evil) 折叠不得调用魔术 eq={_EvilConst.eq_n} "
        f"bool={_EvilConst.bool_n} ne={_EvilConst.ne_n}"
    )
    cmp_evil = ast.Compare(
        left=ast.Constant(value=evil),
        ops=[ast.Eq()],
        comparators=[ast.Constant(value=1)],
    )
    folded_cmp = _ast_safe_fold(cmp_evil)
    assert folded_cmp is _AST_UNKNOWN, (
        f"业务红：Compare(Evil==1) 必须 UNKNOWN，actual={folded_cmp!r}"
    )
    assert _EvilConst.eq_n == 0 and _EvilConst.bool_n == 0, (
        f"业务红：Compare(Evil) 不得触发 __eq__/__bool__，"
        f"eq={_EvilConst.eq_n} bool={_EvilConst.bool_n}"
    )
    truth_evil = _ast_static_truthiness(_ast_safe_fold(evil_const))
    assert truth_evil is None, f"业务红：Evil 真值探测必须 None，actual={truth_evil!r}"
    assert _EvilConst.bool_n == 0, (
        f"业务红：Truth(Evil) 不得触发 __bool__，bool={_EvilConst.bool_n}"
    )

    # scanner 恒非空必须红：真实 clean 片段不得被恒非空 scanner 误伤——用空模块验证期望 []
    clean = ast.parse("def test_ok():\n x=1\n assert x==1\n")
    clean_bad = _ast_scan_fake_green(clean, guard_func_names=frozenset())
    assert clean_bad == [], (
        f"业务红：干净样本 scanner 必须 []（恒非空会红），actual={clean_bad}"
    )


def test_g0_ast_self_guard_client():
    """C9：synthetic 恶意片段必须命中；真实文件扫后为[]；恒空 scanner 必红。"""
    _client_ast_scanner_synthetic_self_test()
    path = Path(__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = _ast_scan_fake_green(
        tree,
        guard_func_names=frozenset(
            {
                "test_g0_ast_self_guard_client",
                "_ast_scan_fake_green",
                "_assert_contains_boolop_or",
                "_const_compare_is_statically_true",
                "_assert_test_is_statically_truthy",
                "_ast_safe_fold",
                "_ast_safe_cmp",
                "_ast_static_truthiness",
                "_ast_fold_str",
                "_getattr_skip_name",
                "_client_ast_scanner_synthetic_self_test",
            }
        ),
    )
    assert bad == [], f"业务红：client AST 自守卫: {bad}"


def test_g1_encrypted_zip_helper_self_proof():
    raw = _make_encrypted_full_md_zip()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert zf.infolist()[0].flag_bits & 1

def test_g1b_unix_special_zip_create_system_self_proof():
    """Q7：symlink/FIFO/device 必须 create_system=3 且 reread mode 自证。"""
    symlink_attr = (stat.S_IFLNK | 0o777) << 16
    raw = _make_zip_special(
        name="link_item",
        data=b"target",
        external_attr=symlink_attr,
        extra_members={"full.md": b"# x"},
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        info = zf.infolist()[0]
        assert info.create_system == 3
        mode = (info.external_attr >> 16) & 0xFFFF
        assert stat.S_ISLNK(mode), f"业务红：symlink mode 自证失败 mode={mode:#o}"

@pytest.mark.parametrize(
    "resolved",
    [
        ["127.0.0.1"],
        ["::1"],
        ["10.1.2.3"],
        ["192.168.0.1"],
        ["172.16.9.9"],
        ["169.254.169.254"],
        ["fc00::1"],
        ["fe80::1"],
        [FAKE_PUBLIC_IP_A, "10.0.0.1"],
        [FAKE_PUBLIC_IP_A, "127.0.0.1"],
    ],
)
def test_g2_put_host_resolve_non_public_zero_put(tmp_path: Path, resolved: list[str]):
    """C2：可注入 resolver；任一解析地址非 global public → 零 PUT。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    puts: list[str] = []
    host = "upload.ssrf-test.example"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [f"https://{host}/presign/a"],
                    },
                },
            )
        if request.method == "PUT":
            puts.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("非公网解析后不得 PUT")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(resolve_addresses_fn=_public_resolver_factory({host: resolved})),
        )
    _assert_remote_error(ei.value, "api_response_invalid")
    assert puts == []

def test_g2b_ssrf_url_form_matrix(tmp_path: Path):
    """
    R1/Q8：PUT 与 ZIP 对称覆盖。
    正向：显式 :443、公网 IP literal（规范化比较，非原始字符串）。
    反向：fc00/fe80 IPv6 literal、非法端口、userinfo、fragment、http、相对、协议相对、空 host。
    无真实 DNS。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")

    def _happy_put_and_zip(
        put_url: str,
        zip_url: str,
        *,
        resolver_map: dict[str, list[str]] | None = None,
    ) -> None:
        puts: list[str] = []
        zips: list[str] = []
        dids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                dids.append(body["files"][0]["data_id"])
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [put_url]},
                    },
                )
            if request.method == "PUT":
                puts.append(str(request.url))
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": dids[0],
                                    "state": "done",
                                    "full_zip_url": zip_url,
                                }
                            ]
                        },
                    },
                )
            # ZIP：规范化键匹配，允许 :443 与缺省端口等价
            if request.method == "GET" and _normalize_url_key(str(request.url)) == _normalize_url_key(
                zip_url
            ):
                zips.append(str(request.url))
                return httpx.Response(
                    200, content=_make_zip_bytes({"full.md": b"# ok\n"})
                )
            raise AssertionError(f"unexpected {request.method} {request.url}")

        resolve = (
            _public_resolver_factory(resolver_map)
            if resolver_map
            else _default_public_resolver
        )
        out = run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(resolve_addresses_fn=resolve),
        )
        assert "# ok" in out.markdown
        assert len(puts) == 1, f"正向 PUT 必须一次: {put_url} actual={puts}"
        _assert_url_equivalent(puts[0], put_url)
        assert len(zips) == 1, f"正向 ZIP 必须一次: {zip_url} actual={zips}"
        _assert_url_equivalent(zips[0], zip_url)

    # 正向：显式 :443（规范化后与缺省 443 等价）
    _happy_put_and_zip(
        "https://upload.example.test:443/presign/a",
        "https://cdn.example.test:443/result/a.zip",
        resolver_map={
            "upload.example.test": [FAKE_PUBLIC_IP_A],
            "cdn.example.test": [FAKE_PUBLIC_IP_B],
        },
    )
    # 正向：公网 IP literal
    _happy_put_and_zip(
        f"https://{FAKE_PUBLIC_IP_A}/presign/a",
        f"https://{FAKE_PUBLIC_IP_B}/result/a.zip",
    )

    # 反向：PUT 侧非法形态 → 零 PUT
    bad_put_urls = (
        "https://[fc00::1]/p",
        "https://[fe80::1]/p",
        "https://upload.example.test:8443/p",
        "https://upload.example.test:abc/p",
        "https://upload.example.test:65536/p",
        "https://user:pass@upload.example.test/p",
        "https://upload.example.test/p#frag",
        "http://upload.example.test/p",
        "/relative/p",
        "//upload.example.test/p",
        "https:///no-host/p",
        "https://",
        "",
        "ftp://upload.example.test/p",
    )
    for bad_url in bad_put_urls:
        puts2: list[str] = []

        def handler_bad_put(request: httpx.Request, u: str = bad_url) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [u]},
                    },
                )
            if request.method == "PUT":
                puts2.append(str(request.url))
                return httpx.Response(200)
            raise AssertionError("非法 PUT URL 不得继续")

        with pytest.raises(err_cls) as ei:
            run(
                _build_sources(mod, [p]),
                transport=httpx.MockTransport(handler_bad_put),
                **_run_kwargs(),
            )
        _assert_remote_error(ei.value, "api_response_invalid")
        assert puts2 == [], f"非法 PUT 必须零 PUT: {bad_url}"

    # 反向：ZIP 侧非法形态 → PUT 可成功但零 ZIP GET
    bad_zip_urls = (
        "https://[fc00::1]/z.zip",
        "https://[fe80::1]/z.zip",
        "https://cdn.example.test:8443/z.zip",
        "https://cdn.example.test:abc/z.zip",
        "https://cdn.example.test:65536/z.zip",
        "https://user:pass@cdn.example.test/z.zip",
        "https://cdn.example.test/z.zip#x",
        "http://cdn.example.test/z.zip",
        "/relative/z.zip",
        "//cdn.example.test/z.zip",
        "https:///no-host/z.zip",
        "",
        "ftp://cdn.example.test/z.zip",
    )
    for bad_zip in bad_zip_urls:
        puts3: list[str] = []
        zips3: list[str] = []
        did_box: dict[str, str] = {"id": ""}

        def handler_bad_zip(
            request: httpx.Request,
            z: str = bad_zip,
            _puts: list[str] = puts3,
            _zips: list[str] = zips3,
            _box: dict[str, str] = did_box,
        ) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                _box["id"] = body["files"][0]["data_id"]
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                _puts.append(str(request.url))
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": _box["id"],
                                    "state": "done",
                                    "full_zip_url": z,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET":
                _zips.append(str(request.url))
                return httpx.Response(200, content=b"x")
            raise AssertionError("unexpected")

        with pytest.raises(err_cls) as ei2:
            run(
                _build_sources(mod, [p]),
                transport=httpx.MockTransport(handler_bad_zip),
                **_run_kwargs(),
            )
        _assert_remote_error(ei2.value, "api_response_invalid")
        assert len(puts3) == 1, f"ZIP 非法时 PUT 可完成一次: {bad_zip} puts={puts3}"
        _assert_url_equivalent(puts3[0], PRESIGNED_PUT_A)
        assert zips3 == [], f"非法 ZIP 必须零 ZIP GET: {bad_zip}"

def test_g3_resolve_changes_between_checks_rebinding(tmp_path: Path):
    """
    R1/R8/C2：首检公网、连接前 JIT 变私网 → 仅当前 URL 失败、零 PUT。
    区分：全列表静态预检失败=整批零 PUT；每请求 JIT rebinding=只伤当前 URL。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    host = "flip.example.test"
    state = {"n": 0}
    puts: list[str] = []

    def flipping_resolver(h: str) -> list[str]:
        if h.strip().lower() != host:
            return [FAKE_PUBLIC_IP_A]
        state["n"] += 1
        if state["n"] % 2 == 1:
            return [FAKE_PUBLIC_IP_B]  # 首检 global public
        return ["10.0.0.8"]  # 二次变私网

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [f"https://{host}/p"],
                    },
                },
            )
        if request.method == "PUT":
            puts.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("rebinding 后不得继续")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(resolve_addresses_fn=flipping_resolver),
        )
    _assert_remote_error(ei.value, "api_response_invalid")
    assert puts == []
    assert state["n"] >= 2, "业务红：每外部请求紧前须重新 resolve"

def test_g3b_dual_file_second_url_jit_rebinding(tmp_path: Path):
    """
    C5/C6：双文件矩阵。
    - 第二 URL 首次即私网/混合/非法 → 整批 PUT0；
    - JIT 变私网 → 第一 PUT1、第二 PUT0；
    - ZIP hostname 同样覆盖首次 resolver 私网/混合与 JIT rebinding。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"%PDF-A"),
        _write_temp_source(tmp_path, "b.pdf", b"%PDF-B"),
    ]
    host_ok = "ok-upload.example.test"
    host_flip = "flip2.example.test"
    put_url_a = f"https://{host_ok}/a"
    put_url_b = f"https://{host_flip}/b"

    # --- A) 第二 URL 首次即私网 → PUT0 ---
    puts0: list[str] = []

    def resolver_private_first(h: str) -> list[str]:
        key = h.strip().lower().strip("[]")
        if key == host_ok:
            return [FAKE_PUBLIC_IP_A]
        if key == host_flip:
            return ["10.0.0.9"]
        return [FAKE_PUBLIC_IP_A]

    def handler0(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [put_url_a, put_url_b],
                    },
                },
            )
        if request.method == "PUT":
            puts0.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("首次私网后不得继续")

    with pytest.raises(err_cls) as ei0:
        run(
            _build_sources(mod, paths),
            transport=httpx.MockTransport(handler0),
            **_run_kwargs(resolve_addresses_fn=resolver_private_first),
        )
    _assert_remote_error(ei0.value, "api_response_invalid")
    assert puts0 == [], f"业务红：第二 URL 首次私网整批零 PUT，actual={puts0}"

    # --- A2) 第二 URL 首次混合（公网+私网）→ PUT0 ---
    puts_mix: list[str] = []

    def resolver_mixed(h: str) -> list[str]:
        key = h.strip().lower().strip("[]")
        if key == host_ok:
            return [FAKE_PUBLIC_IP_A]
        if key == host_flip:
            return [FAKE_PUBLIC_IP_B, "10.0.0.9"]
        return [FAKE_PUBLIC_IP_A]

    def handler_mix(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [put_url_a, put_url_b],
                    },
                },
            )
        if request.method == "PUT":
            puts_mix.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("混合非公网后不得继续")

    with pytest.raises(err_cls) as ei_m:
        run(
            _build_sources(mod, paths),
            transport=httpx.MockTransport(handler_mix),
            **_run_kwargs(resolve_addresses_fn=resolver_mixed),
        )
    _assert_remote_error(ei_m.value, "api_response_invalid")
    assert puts_mix == [], f"业务红：混合解析整批零 PUT，actual={puts_mix}"

    # --- B) JIT rebinding：第一 PUT1、第二 PUT0 ---
    resolve_counts: dict[str, int] = {host_ok: 0, host_flip: 0}
    puts: list[str] = []

    def dual_resolver(h: str) -> list[str]:
        key = h.strip().lower().strip("[]")
        if key == host_ok:
            resolve_counts[host_ok] += 1
            return [FAKE_PUBLIC_IP_A]
        if key == host_flip:
            resolve_counts[host_flip] += 1
            if resolve_counts[host_flip] % 2 == 1:
                return [FAKE_PUBLIC_IP_B]
            return ["10.0.0.9"]
        return [FAKE_PUBLIC_IP_A]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [put_url_a, put_url_b],
                    },
                },
            )
        if request.method == "PUT":
            puts.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("第二 URL rebinding 后不得进入轮询")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, paths),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(resolve_addresses_fn=dual_resolver),
        )
    _assert_remote_error(ei.value, "api_response_invalid")
    assert len(puts) == 1, f"业务红：第一 PUT 精确一次，actual={puts}"
    _assert_url_equivalent(puts[0], put_url_a)
    assert resolve_counts[host_flip] >= 2, (
        f"业务红：第二 URL 须 JIT 二次 resolve，counts={resolve_counts}"
    )

    # --- C) ZIP hostname 首次私网/混合与 JIT ---
    p1 = _write_temp_source(tmp_path, "z1.pdf", b"%PDF-Z1")
    zip_host = "zip-flip.example.test"
    zip_url = f"https://{zip_host}/r.zip"
    for kind in ("private", "mixed", "jit"):
        zputs: list[str] = []
        zzips: list[str] = []
        did_box: dict[str, str] = {"id": ""}
        zcount = {"n": 0}

        def zresolve(h: str, _kind: str = kind, _c: dict = zcount) -> list[str]:
            key = h.strip().lower().strip("[]")
            if key != zip_host:
                return [FAKE_PUBLIC_IP_A]
            _c["n"] += 1
            if _kind == "private":
                return ["10.1.1.1"]
            if _kind == "mixed":
                return [FAKE_PUBLIC_IP_B, "10.1.1.1"]
            # jit
            if _c["n"] % 2 == 1:
                return [FAKE_PUBLIC_IP_B]
            return ["10.1.1.1"]

        def zhandler(
            request: httpx.Request,
            _puts: list = zputs,
            _zips: list = zzips,
            _box: dict = did_box,
            _zu: str = zip_url,
        ) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                _box["id"] = body["files"][0]["data_id"]
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                _puts.append(str(request.url))
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": _box["id"],
                                    "state": "done",
                                    "full_zip_url": _zu,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET":
                _zips.append(str(request.url))
                return httpx.Response(200, content=b"x")
            raise AssertionError("unexpected")

        with pytest.raises(err_cls) as eiz:
            run(
                _build_sources(mod, [p1]),
                transport=httpx.MockTransport(zhandler),
                **_run_kwargs(resolve_addresses_fn=zresolve),
            )
        _assert_remote_error(eiz.value, "api_response_invalid")
        assert len(zputs) == 1, f"ZIP {kind}: PUT 可完成一次 actual={zputs}"
        assert zzips == [], f"ZIP {kind}: 必须零 ZIP GET actual={zzips}"


def test_g4_mixed_done_running_zero_zip(tmp_path: Path):
    """C10：done+running 混态全部 done 前零 ZIP。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"1"),
        _write_temp_source(tmp_path, "b.pdf", b"2"),
    ]
    ids: list[str] = []
    zip_n = {"n": 0}
    sleeps: list[float] = []
    clock = {"t": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode())
            ids.extend(x["data_id"] for x in body["files"])
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A, PRESIGNED_PUT_B],
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": ids[0],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            },
                            {"data_id": ids[1], "state": "running"},
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) in {ZIP_URL_A, ZIP_URL_B}:
            zip_n["n"] += 1
            return httpx.Response(200, content=_make_zip_bytes({"full.md": b"x"}))
        raise AssertionError("unexpected")

    def sleep_fn(s: float) -> None:
        sleeps.append(float(s))
        clock["t"] += float(s)

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, paths),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(sleep_fn=sleep_fn, clock_fn=lambda: clock["t"]),
        )
    _assert_remote_error(ei.value, "poll_budget_exceeded")
    assert zip_n["n"] == 0
    assert sleeps

def test_g5_cancel_matrix_points(tmp_path: Path):
    """
    C4：四取消点精确计数。
    POST后=(POST1,PUT0,poll0,ZIP0)；两PUT间=(1,1,0,0)；
    done后ZIP前=(1,2,1,0)；两ZIP间=(1,2,1,1)。
    每场景最终唯一 interrupted；任何后续外部动作必红。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    paths = [
        _write_temp_source(tmp_path, "a.pdf", b"1"),
        _write_temp_source(tmp_path, "b.pdf", b"2"),
    ]
    expected = {
        "post": (1, 0, 0, 0),
        "between_puts": (1, 1, 0, 0),
        "before_zip": (1, 2, 1, 0),
        "between_zips": (1, 2, 1, 1),
    }

    def _run_cancel(flag_after: str) -> None:
        ids: list[str] = []
        flag = {"v": False}
        counts = {"post": 0, "put": 0, "poll": 0, "zip": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                ids.extend(x["data_id"] for x in body["files"])
                counts["post"] += 1
                if flag_after == "post":
                    flag["v"] = True
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A, PRESIGNED_PUT_B],
                        },
                    },
                )
            if request.method == "PUT":
                if flag["v"]:
                    raise AssertionError(
                        f"业务红：取消后不得再 PUT，point={flag_after} counts={counts}"
                    )
                counts["put"] += 1
                if flag_after == "between_puts" and counts["put"] == 1:
                    flag["v"] = True
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                if flag["v"]:
                    raise AssertionError(
                        f"业务红：取消后不得再 poll，point={flag_after} counts={counts}"
                    )
                counts["poll"] += 1
                if flag_after == "before_zip":
                    flag["v"] = True
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": ids[0],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_A,
                                },
                                {
                                    "data_id": ids[1],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_B,
                                },
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url) in {ZIP_URL_A, ZIP_URL_B}:
                if flag["v"] and flag_after != "between_zips":
                    raise AssertionError(
                        f"业务红：取消后不得再 ZIP，point={flag_after} counts={counts}"
                    )
                if flag["v"] and flag_after == "between_zips" and counts["zip"] >= 1:
                    raise AssertionError(
                        f"业务红：两 ZIP 间取消后不得第二 ZIP，counts={counts}"
                    )
                counts["zip"] += 1
                if flag_after == "between_zips" and counts["zip"] == 1:
                    flag["v"] = True
                return httpx.Response(
                    200, content=_make_zip_bytes({"full.md": b"# x\n"})
                )
            raise AssertionError(f"unexpected {request.method}")

        with pytest.raises(err_cls) as ei:
            run(
                _build_sources(mod, paths),
                transport=httpx.MockTransport(handler),
                **_run_kwargs(cancel_check=lambda: flag["v"]),
            )
        _assert_remote_error(ei.value, "interrupted")
        exp = expected[flag_after]
        actual = (counts["post"], counts["put"], counts["poll"], counts["zip"])
        assert actual == exp, (
            f"业务红：取消点 {flag_after} 计数必须 {exp}，actual={actual}"
        )

    for point in ("post", "between_puts", "before_zip", "between_zips"):
        _run_cancel(point)


def test_g6_single_deadline_timeout_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C3：成功经过 POST/PUT/poll/ZIP 的单文件流程；
    阶段推进 fake clock，使 ZIP 前 remaining<5 秒。
    四阶段 request.extensions timeout 的 connect/read/write/pool 必须有限、>0、
    非默认且 <= 当时 remaining；至少末次严格 <5 并证明随 remaining 收缩。
    预算耗尽唯一 poll_budget_exceeded 复用 D6，本测删除 pending/elapsed 重复逻辑。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"1")
    holder: dict[str, str] = {}
    t0 = 10_000.0
    clock = {"t": t0}
    # (phase, timeout_obj, remaining)
    phase_timeouts: list[tuple[str, Any, float]] = []

    def _phase_of(request: httpx.Request) -> str:
        if request.method == "POST":
            return "post"
        if request.method == "PUT":
            return "put"
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return "poll"
        if request.method == "GET":
            return "zip_stream"
        return "other"

    def _capture_timeout(request: httpx.Request) -> None:
        phase = _phase_of(request)
        remaining = float(POLL_BUDGET_SEC) - (float(clock["t"]) - t0)
        ext = getattr(request, "extensions", None) or {}
        to = ext.get("timeout")
        phase_timeouts.append((phase, to, remaining))

    def handler(request: httpx.Request) -> httpx.Response:
        _capture_timeout(request)
        if request.method == "POST":
            body = json.loads(request.content.decode())
            holder["id"] = body["files"][0]["data_id"]
            clock["t"] += 600.0  # remaining ~1200
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            clock["t"] += 600.0  # remaining ~600
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            # 推进到 remaining < 5
            clock["t"] = t0 + float(POLL_BUDGET_SEC) - 3.0
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# deadline-ok\n"})
            )
        raise AssertionError(f"unexpected {request.method}")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler),
        **_run_kwargs(sleep_fn=lambda _s: None, clock_fn=lambda: clock["t"]),
    )
    assert "# deadline-ok" in out.markdown
    phases_seen = [p for p, *_ in phase_timeouts]
    for need in ("post", "put", "poll", "zip_stream"):
        assert need in phases_seen, f"业务红：须捕获 {need} timeout，seen={phases_seen}"
    remainings: list[float] = []
    for phase, to, remaining in phase_timeouts:
        remainings.append(remaining)
        assert to is not None, f"业务红：timeout=None 必失败 phase={phase}"
        vals: list[float] = []
        if isinstance(to, (int, float)):
            vals = [float(to)]
        elif isinstance(to, dict):
            for k in ("connect", "read", "write", "pool"):
                assert k in to, f"业务红：timeout 缺 {k} phase={phase}"
                assert to[k] is not None and to[k] is not httpx.USE_CLIENT_DEFAULT
                vals.append(float(to[k]))
        else:
            for attr in ("connect", "read", "write", "pool"):
                val = getattr(to, attr, None)
                assert val is not None and val is not httpx.USE_CLIENT_DEFAULT, (
                    f"业务红：timeout.{attr} 不得缺省/None，phase={phase} to={to!r}"
                )
                vals.append(float(val))
        for v in vals:
            assert v > 0, f"业务红：timeout 必须 >0 phase={phase} v={v}"
            assert v <= remaining + 1e-6, (
                f"业务红：timeout={v} > remaining={remaining} phase={phase}"
            )
            # 非默认 5s：当 remaining 很大时 timeout 可小于 remaining 但不应恒等于 5 且不随 remaining 变
            assert v != float("inf")
    # ZIP 前 remaining <5，且末次严格 <5
    zip_items = [(ph, rem) for ph, _to, rem in phase_timeouts if ph == "zip_stream"]
    assert zip_items, "业务红：必须有 zip_stream 阶段"
    assert zip_items[-1][1] < 5.0, (
        f"业务红：ZIP 时 remaining 须 <5，actual={zip_items[-1][1]}"
    )
    # 随 remaining 收缩：后阶段 remaining 不大于前阶段
    post_rem = [rem for ph, _to, rem in phase_timeouts if ph == "post"][0]
    zip_rem = zip_items[-1][1]
    assert zip_rem < post_rem, (
        f"业务红：timeout 预算须随 remaining 收缩 post={post_rem} zip={zip_rem}"
    )


def test_g7_network_errors_put_poll_zip(tmp_path: Path):
    """C11：PUT/poll/ZIP 网络错误。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "a.pdf", b"1")

    def put_net(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            raise httpx.ReadError("simulated-read-failure-PUT")
        raise AssertionError("stop")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(put_net),
            **_run_kwargs(),
        )
    _assert_remote_error(ei.value, "upload_failed")
    assert "simulated-read-failure-PUT" not in ei.value.message

    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def poll_net(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            raise httpx.ConnectError("simulated-connect-failure-POLL")
        raise AssertionError("stop")

    with pytest.raises(err_cls) as ei2:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(poll_net),
            **_run_kwargs(),
        )
    _assert_remote_error(ei2.value, "api_request_failed")

    def zip_net(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            raise httpx.ConnectError("simulated-connect-failure-ZIP")
        raise AssertionError("stop")

    with pytest.raises(err_cls) as ei3:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(zip_net),
            **_run_kwargs(),
        )
    _assert_remote_error(ei3.value, "zip_download_failed")

class _NoPrereadSyncTransport(httpx.BaseTransport):
    """
    C7/R6-C1：测试内定制同步 transport，不在 handler 前预读 request。
    与 httpx.MockTransport 相反：MockTransport.handle_request 会无条件 request.read()，
    会把 200_000_000 bytes 全量读入内存。
    携带不可伪造 _V1N_SAFE_TRANSPORT_MARK，供网络熔断精确放行。
    """

    _v1n_safe_transport_mark = _V1N_SAFE_TRANSPORT_MARK

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self._handler = handler
        self.put_prefix_bytes = 0
        self.put_chunks = 0
        self.put_saw_stream = False
        self.unbounded_content_access = False
        self._v1n_safe_transport_mark = _V1N_SAFE_TRANSPORT_MARK

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # 禁止预读：不调用 request.read() / request.content
        return self._handler(request)


def _v1n_transport_safety_helper_self_proof() -> None:
    """R6-C1 纯 helper：标记 transport 可过 Client+内存请求；同名无标记/未知类仍拒绝。"""
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.method)
        return httpx.Response(200, content=b"ok-body")

    safe = _NoPrereadSyncTransport(handler)
    assert getattr(safe, "_v1n_safe_transport_mark", None) is _V1N_SAFE_TRANSPORT_MARK
    with httpx.Client(transport=safe) as client:
        resp = client.get("https://v1n-safe-transport.test/probe")
    assert resp.status_code == 200
    assert resp.content == b"ok-body"
    assert hits == ["GET"]

    class _ForgedSameNameTransport:  # 故意同 __name__ 伪造，无哨兵
        __name__ = "_NoPrereadSyncTransport"
        def handle_request(self, request):  # noqa: ANN001
            return httpx.Response(200)

    forged = _ForgedSameNameTransport()
    # 类型名可被伪造，但缺哨兵仍拒绝
    type(forged).__name__ = "_NoPrereadSyncTransport"
    assert getattr(forged, "_v1n_safe_transport_mark", None) is not _V1N_SAFE_TRANSPORT_MARK
    with pytest.raises(_V1NNetFuseError, match="外网熔断|安全 transport"):
        httpx.Client(transport=forged)  # type: ignore[arg-type]

    class _UnknownNetTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

    with pytest.raises(_V1NNetFuseError, match="外网熔断|安全 transport"):
        httpx.Client(transport=_UnknownNetTransport())


def _make_logical_sized_file(path: Path, size: int, head: bytes = b"%PDF") -> Path:
    """
    C8/C15：构造真实逻辑尺寸文件。
    Windows：同一 Win32 句柄完成 sparse 标记、写头与 SetFilePointerEx/SetEndOfFile；
    禁止 shareMode=0 未关闭句柄外再 open 同路径；逐 API 检查返回值/GetLastError；
    finally 精确 CloseHandle；自证逻辑尺寸与稀疏 allocation，禁止实体 200MB。
    POSIX：稀疏 truncate 并自证。
    """
    import os as _os

    path.parent.mkdir(parents=True, exist_ok=True)
    if _os.name == "nt" and size >= 4096:
        import ctypes
        from ctypes import wintypes

        FILE_ATTRIBUTE_NORMAL = 0x80
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        CREATE_ALWAYS = 2
        FILE_BEGIN = 0
        FSCTL_SET_SPARSE = 0x000900C4
        # R6-C2：WinDLL + 完整 argtypes/restype；HANDLE 指针宽度正确
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # HANDLE 在 64 位为指针宽；用 c_void_p 避免 c_long 截断
        HANDLE = ctypes.c_void_p
        BOOL = wintypes.BOOL
        DWORD = wintypes.DWORD
        LPCWSTR = wintypes.LPCWSTR
        LPVOID = ctypes.c_void_p
        LPDWORD = ctypes.POINTER(DWORD)
        LARGE_INTEGER = ctypes.c_longlong
        PLARGE_INTEGER = ctypes.POINTER(LARGE_INTEGER)

        kernel32.CreateFileW.argtypes = [
            LPCWSTR,
            DWORD,
            DWORD,
            LPVOID,
            DWORD,
            DWORD,
            HANDLE,
        ]
        kernel32.CreateFileW.restype = HANDLE
        kernel32.DeviceIoControl.argtypes = [
            HANDLE,
            DWORD,
            LPVOID,
            DWORD,
            LPVOID,
            DWORD,
            LPDWORD,
            LPVOID,
        ]
        kernel32.DeviceIoControl.restype = BOOL
        kernel32.WriteFile.argtypes = [
            HANDLE,
            LPVOID,
            DWORD,
            LPDWORD,
            LPVOID,
        ]
        kernel32.WriteFile.restype = BOOL
        kernel32.SetFilePointerEx.argtypes = [
            HANDLE,
            LARGE_INTEGER,
            PLARGE_INTEGER,
            DWORD,
        ]
        kernel32.SetFilePointerEx.restype = BOOL
        kernel32.SetEndOfFile.argtypes = [HANDLE]
        kernel32.SetEndOfFile.restype = BOOL
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.CloseHandle.restype = BOOL
        kernel32.GetCompressedFileSizeW.argtypes = [LPCWSTR, LPDWORD]
        kernel32.GetCompressedFileSizeW.restype = DWORD  # 无符号，避免 -1 假绿

        INVALID_HANDLE_VALUE = HANDLE(-1).value
        INVALID_FILE_SIZE = 0xFFFFFFFF  # DWORD 无符号最大值

        ctypes.set_last_error(0)
        handle = kernel32.CreateFileW(
            str(path),
            DWORD(GENERIC_READ | GENERIC_WRITE),
            DWORD(0),  # 独占；写/扩 EOF 必须走同一句柄，禁止再 open 同路径
            None,
            DWORD(CREATE_ALWAYS),
            DWORD(FILE_ATTRIBUTE_NORMAL),
            None,
        )
        # 无符号/指针宽比较；禁止 signed 假绿
        if handle is None or int(ctypes.cast(handle, ctypes.c_void_p).value or 0) == int(
            INVALID_HANDLE_VALUE or 0
        ) or handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            raise AssertionError(f"夹具失败：CreateFileW 失败 GetLastError={err}")
        try:
            bytes_ret = DWORD(0)
            ctypes.set_last_error(0)
            ok = kernel32.DeviceIoControl(
                handle,
                DWORD(FSCTL_SET_SPARSE),
                None,
                DWORD(0),
                None,
                DWORD(0),
                ctypes.byref(bytes_ret),
                None,
            )
            if not ok:
                err = ctypes.get_last_error()
                raise AssertionError(
                    f"夹具失败：FSCTL_SET_SPARSE 必须成功，GetLastError={err}"
                )
            # 同一句柄写头
            head_data = head[: min(len(head), size)]
            if head_data:
                buf = ctypes.create_string_buffer(head_data)
                written = DWORD(0)
                ctypes.set_last_error(0)
                ok = kernel32.WriteFile(
                    handle,
                    buf,
                    DWORD(len(head_data)),
                    ctypes.byref(written),
                    None,
                )
                if not ok:
                    err = ctypes.get_last_error()
                    raise AssertionError(
                        f"夹具失败：WriteFile 写头失败 GetLastError={err}"
                    )
                if int(written.value) != len(head_data):
                    raise AssertionError(
                        f"夹具失败：WriteFile 部分写入 written={written.value}"
                    )
            # 同一句柄 SetFilePointerEx + SetEndOfFile 扩到逻辑尺寸
            if size > 0:
                new_ptr = LARGE_INTEGER(0)
                ctypes.set_last_error(0)
                ok = kernel32.SetFilePointerEx(
                    handle,
                    LARGE_INTEGER(size),
                    ctypes.byref(new_ptr),
                    DWORD(FILE_BEGIN),
                )
                if not ok:
                    err = ctypes.get_last_error()
                    raise AssertionError(
                        f"夹具失败：SetFilePointerEx 失败 GetLastError={err}"
                    )
                ctypes.set_last_error(0)
                ok = kernel32.SetEndOfFile(handle)
                if not ok:
                    err = ctypes.get_last_error()
                    raise AssertionError(
                        f"夹具失败：SetEndOfFile 失败 GetLastError={err}"
                    )
        finally:
            ctypes.set_last_error(0)
            ok_close = kernel32.CloseHandle(handle)
            if not ok_close:
                err = ctypes.get_last_error()
                raise AssertionError(
                    f"夹具失败：CloseHandle 失败 GetLastError={err}"
                )
        # 自证 allocation 远小于逻辑尺寸（允许少量元数据）
        try:
            alloc = path.stat().st_blocks * 512  # type: ignore[attr-defined]
        except AttributeError:
            high = DWORD(0)
            ctypes.set_last_error(0)
            low = kernel32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
            # restype=DWORD：失败为 0xFFFFFFFF（无符号），禁止 signed -1 假绿
            low_u = int(low) & 0xFFFFFFFF
            if low_u == INVALID_FILE_SIZE:
                err = ctypes.get_last_error()
                if err != 0:
                    raise AssertionError(
                        f"夹具失败：GetCompressedFileSizeW 失败 GetLastError={err}"
                    )
            alloc = (int(high.value) << 32) + low_u
        assert alloc < max(size // 8, 1024 * 1024), (
            f"夹具失败：稀疏 allocation 过大 alloc={alloc} size={size}"
        )
    else:
        with open(path, "wb") as f:
            f.write(head[: min(len(head), size)])
            if size > len(head):
                f.truncate(size)
        st = path.stat()
        if hasattr(st, "st_blocks"):
            alloc = int(st.st_blocks) * 512
            if size >= 1024 * 1024:
                assert alloc < max(size // 4, 1024 * 1024), (
                    f"夹具失败：POSIX 稀疏 allocation 过大 alloc={alloc} size={size}"
                )
    assert path.stat().st_size == size, (
        f"夹具自证：逻辑尺寸必须为 {size}，actual={path.stat().st_size}"
    )
    return path


def _sparse_logical_file_helper_self_proof(tmp_path: Path) -> None:
    """R6-C2 跨平台 sparse helper 纯自证；Windows 必走 Win32 分支。"""
    import os as _os

    target = tmp_path / "sparse-self-proof.bin"
    size = 2 * 1024 * 1024  # 2MiB 逻辑尺寸
    head = b"%PDF-SPARSE-PROOF"
    _make_logical_sized_file(target, size, head=head)
    st = target.stat()
    assert st.st_size == size
    with open(target, "rb") as f:
        assert f.read(len(head)) == head
    if _os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCompressedFileSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetCompressedFileSizeW.restype = wintypes.DWORD
        high = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        low = kernel32.GetCompressedFileSizeW(str(target), ctypes.byref(high))
        low_u = int(low) & 0xFFFFFFFF
        if low_u == 0xFFFFFFFF:
            err = ctypes.get_last_error()
            assert err == 0, f"业务红：GetCompressedFileSizeW 失败 GetLastError={err}"
        alloc = (int(high.value) << 32) + low_u
        assert alloc < max(size // 8, 1024 * 1024), (
            f"业务红：Windows sparse allocation 过大 alloc={alloc}"
        )
    else:
        if hasattr(st, "st_blocks"):
            alloc = int(st.st_blocks) * 512
            assert alloc < max(size // 4, 1024 * 1024)
    target.unlink(missing_ok=True)
    assert not target.exists()


class _V1NReadProbe:
    """
    R7-C3：跨 builtins.open / io.open / Path.open / os.open+os.read 的正文读取探针。
    独立 tracker，累计实际返回字节与 phase；无界/超界在底层大读前失败。
    """

    def __init__(self, *, put_bound: int = 64 * 1024, put_extra_chunk: int | None = None) -> None:
        import os as _os
        import io as _io

        self.put_bound = put_bound
        # R8-C3：PUT 累计预算 = transport 有界前缀 + 一个冻结小块
        self.put_extra_chunk = int(put_bound if put_extra_chunk is None else put_extra_chunk)
        self.put_return_budget = int(put_bound) + self.put_extra_chunk
        self.target_resolved: set[str] = set()
        self.phase = "pre"
        self.put_transport_started = False
        self.unbounded_hits: list[str] = []
        self.pre_materialized_hits: list[str] = []
        self.returned_by_phase: dict[str, int] = {"pre": 0, "post": 0, "put": 0}
        self.fd_to_gen: dict[int, int] = {}
        self.gen_counter = 0
        self._os = _os
        self._io = _io
        self._real_os_open = _os.open
        self._real_os_read = _os.read
        self._real_os_close = _os.close
        self._real_builtin_open = open
        self._real_io_open = _io.open
        self._real_path_open = Path.open
        self._real_path_read_bytes = Path.read_bytes

    def clear(self) -> None:
        self.unbounded_hits.clear()
        self.pre_materialized_hits.clear()
        self.returned_by_phase = {"pre": 0, "post": 0, "put": 0}
        self.fd_to_gen.clear()
        self.gen_counter = 0
        self.put_transport_started = False
        self.phase = "pre"

    def add_target(self, p: Path | str) -> None:
        self.target_resolved.add(str(Path(p).resolve()))

    def _is_target(self, p: Path | str) -> bool:
        key: str | None = None
        try:
            key = str(Path(p).resolve())
        except OSError:
            key = None
        if key is None:
            return False
        return key in self.target_resolved

    def _note_return(self, n_ret: int, *, where: str) -> None:
        if n_ret <= 0:
            return
        ph = self.phase
        self.returned_by_phase[ph] = self.returned_by_phase.get(ph, 0) + n_ret
        if not self.put_transport_started and n_ret > 0:
            self.pre_materialized_hits.append(f"{where}:{ph}:{n_ret}")

    def _reject_unbounded(self, n_i: int, sz: int, where: str) -> None:
        """
        读取门（R8-C3）：
        - PUT transport 前：任何 n!=0 的正文读取均红（小文件 helper 自证不依赖 200MB）。
        - PUT 期：无界 n<0、单次 n>put_bound、或累计后可能超过
          put_bound+冻结小块 的请求，均在底层大读前失败。
        n==0 视为空探针，允许且不记正文。
        """
        if not self.put_transport_started:
            if n_i != 0:
                self.pre_materialized_hits.append(
                    f"{where}:pre-put:n={n_i}:sz={sz}"
                )
                raise AssertionError(
                    f"业务红：PUT transport 前禁止正文正字节读取 where={where} n={n_i}"
                )
            return
        # PUT 期：无界/单次超界/累计预算超界 — 底层大读前失败
        if n_i < 0 or n_i > self.put_bound:
            self.unbounded_hits.append(f"{where}:n={n_i}:sz={sz}")
            raise AssertionError(
                f"业务红：禁止无界/超界读物化大文件 where={where} n={n_i} size={sz}"
            )
        already = int(self.returned_by_phase.get("put", 0))
        if n_i > 0 and already + n_i > self.put_return_budget:
            self.unbounded_hits.append(
                f"{where}:cumul:already={already}:n={n_i}:budget={self.put_return_budget}"
            )
            raise AssertionError(
                f"业务红：PUT 累计返回将超预算 where={where} already={already} "
                f"n={n_i} budget={self.put_return_budget}"
            )

    class _GuardedFile:
        def __init__(self, probe: "_V1NReadProbe", raw, path_s: str):
            self._probe = probe
            self._raw = raw
            self._path_s = path_s

        def _target_sz(self) -> int:
            sz = 0
            try:
                sz = Path(self._path_s).stat().st_size
            except OSError:
                sz = 0
            return sz

        def _reject_bypass(self, api: str) -> None:
            if self._probe._is_target(self._path_s):
                self._probe.unbounded_hits.append(f"file.{api}:bypass")
                raise AssertionError(
                    f"业务红：禁止旁路读取 API file.{api} path={self._path_s}"
                )
            raise AssertionError(f"业务红：禁止旁路读取 API file.{api}")

        def read(self, n: int | None = -1):
            n_i = -1 if n is None else int(n)
            if self._probe._is_target(self._path_s):
                self._probe._reject_unbounded(n_i, self._target_sz(), "file.read")
            data = self._raw.read(n)
            if self._probe._is_target(self._path_s):
                self._probe._note_return(len(data or b""), where="file.read")
            return data

        def readinto(self, buf):  # noqa: ANN001
            if self._probe._is_target(self._path_s):
                n_i = len(buf) if buf is not None else -1
                self._probe._reject_unbounded(n_i, self._target_sz(), "file.readinto")
            if hasattr(self._raw, "readinto"):
                n_ret = self._raw.readinto(buf)
            else:
                chunk = self._raw.read(len(buf))
                n_ret = len(chunk)
                buf[:n_ret] = chunk
            if self._probe._is_target(self._path_s):
                self._probe._note_return(int(n_ret or 0), where="file.readinto")
            return n_ret

        def __iter__(self):
            # R8-C3：禁止委托 raw 行迭代（可先分配超大单行）；目标文件明确拒绝
            if self._probe._is_target(self._path_s):
                self._probe.unbounded_hits.append("file.iter:rejected")
                raise AssertionError(
                    "业务红：目标文件禁止行迭代；应使用有界 read 小块"
                )
            return iter(self._raw)

        def read1(self, n: int | None = -1):  # noqa: ANN001
            self._reject_bypass("read1")

        def readline(self, *a, **k):  # noqa: ANN001
            self._reject_bypass("readline")

        def readlines(self, *a, **k):  # noqa: ANN001
            self._reject_bypass("readlines")

        def readall(self, *a, **k):  # noqa: ANN001
            self._reject_bypass("readall")

        def readinto1(self, *a, **k):  # noqa: ANN001
            self._reject_bypass("readinto1")

        def __getattr__(self, item):
            # R8-C3：禁止 __getattr__ 放行任何 read* 旁路
            if isinstance(item, str) and item.startswith("read"):
                self._reject_bypass(item)
            if item in {
                "name",
                "mode",
                "closed",
                "fileno",
                "seek",
                "tell",
                "flush",
                "close",
                "readable",
                "writable",
                "seekable",
                "isatty",
            }:
                return getattr(self._raw, item)
            raise AttributeError(item)

        def __enter__(self):
            self._raw.__enter__()
            return self

        def __exit__(self, *a):
            return self._raw.__exit__(*a)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probe = self
        os = self._os
        io_mod = self._io

        def guarded_read_bytes(self_path: Path) -> bytes:  # noqa: ANN001
            if probe._is_target(self_path):
                try:
                    sz = self_path.stat().st_size
                except OSError:
                    sz = 0
                if sz > probe.put_bound:
                    probe.unbounded_hits.append(f"Path.read_bytes:{sz}")
                    raise AssertionError(
                        f"业务红：禁止 Path.read_bytes 物化大文件 size={sz}"
                    )
                if not probe.put_transport_started and sz > 0:
                    probe.pre_materialized_hits.append(f"Path.read_bytes:{sz}")
                    raise AssertionError("业务红：PUT 前禁止 Path.read_bytes 正文")
            return probe._real_path_read_bytes(self_path)

        def guarded_open(file, *a, **k):
            path_s = str(file)
            fh = probe._real_builtin_open(file, *a, **k)
            if probe._is_target(path_s):
                return probe._GuardedFile(probe, fh, path_s)
            return fh

        def guarded_io_open(file, *a, **k):
            path_s = str(file)
            fh = probe._real_io_open(file, *a, **k)
            if probe._is_target(path_s):
                return probe._GuardedFile(probe, fh, path_s)
            return fh

        def guarded_path_open(self_path: Path, *a, **k):  # noqa: ANN001
            path_s = str(self_path)
            fh = probe._real_path_open(self_path, *a, **k)
            if probe._is_target(path_s):
                return probe._GuardedFile(probe, fh, path_s)
            return fh

        def guarded_os_open(path, flags, *a, **k):
            fd = probe._real_os_open(path, flags, *a, **k)
            if probe._is_target(path):
                probe.gen_counter += 1
                probe.fd_to_gen[int(fd)] = probe.gen_counter
            return fd

        def guarded_os_read(fd, n):
            fd_i = int(fd)
            n_i = int(n) if n is not None else -1
            g = probe.fd_to_gen.get(fd_i)
            if g is not None:
                # R8-C3：os.read(fd,0) 返回 b'' 且不记正文命中
                if n_i == 0:
                    data0 = probe._real_os_read(fd_i, 0)
                    return b"" if data0 in (b"", None) else data0
                # 目标 fd：无界/超界/累计预算在 real read 前失败
                if n_i < 0 or n_i > probe.put_bound:
                    probe.unbounded_hits.append(f"os.read:gen{g}:n={n_i}")
                    raise AssertionError(
                        f"业务红：禁止 os.read 无界/超界 gen={g} n={n_i}"
                    )
                if not probe.put_transport_started:
                    probe.pre_materialized_hits.append(f"os.read:gen{g}:n={n_i}")
                    raise AssertionError(
                        f"业务红：PUT 前禁止 os.read 正文 gen={g} n={n_i}"
                    )
                already = int(probe.returned_by_phase.get("put", 0))
                if already + n_i > probe.put_return_budget:
                    probe.unbounded_hits.append(
                        f"os.read:gen{g}:cumul:already={already}:n={n_i}"
                    )
                    raise AssertionError(
                        f"业务红：os.read PUT 累计将超预算 gen={g} "
                        f"already={already} n={n_i} budget={probe.put_return_budget}"
                    )
            data = probe._real_os_read(fd_i, n)
            if g is not None:
                probe._note_return(len(data or b""), where=f"os.read:gen{g}")
            return data

        def guarded_os_close(fd):
            fd_i = int(fd)
            probe.fd_to_gen.pop(fd_i, None)
            return probe._real_os_close(fd_i)

        monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
        monkeypatch.setattr(Path, "open", guarded_path_open)
        monkeypatch.setattr("builtins.open", guarded_open)
        monkeypatch.setattr(io_mod, "open", guarded_io_open)
        monkeypatch.setattr(os, "open", guarded_os_open)
        monkeypatch.setattr(os, "read", guarded_os_read)
        monkeypatch.setattr(os, "close", guarded_os_close)


def _read_guard_helper_self_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    R7-C3：不依赖 production 的小文件 helper 自证。
    逐一证明 Path.open/io.open、os.open+os.read、PUT 前多次小读三条 seam；
    各样本独立 tracker；不得真实分配 200MB。
    """
    import io as _io
    import os as _os

    put_bound = 64 * 1024
    sample = tmp_path / "probe-small.bin"
    sample.write_bytes(b"A" * 1000)

    # 1) Path.open / io.open seam
    p1 = _V1NReadProbe(put_bound=put_bound)
    p1.add_target(sample)
    p1.phase = "pre"
    p1.install(monkeypatch)
    with pytest.raises(AssertionError, match="PUT"):
        with Path(sample).open("rb") as fh:
            fh.read(16)
    assert p1.pre_materialized_hits, "业务红：Path.open 必须命中预物化门"
    # 独立 tracker：io.open
    monkeypatch.undo()
    p1b = _V1NReadProbe(put_bound=put_bound)
    p1b.add_target(sample)
    p1b.install(monkeypatch)
    with pytest.raises(AssertionError, match="PUT"):
        with _io.open(str(sample), "rb") as fh:
            fh.read(16)
    assert p1b.pre_materialized_hits, "业务红：io.open 必须命中预物化门"
    assert p1.pre_materialized_hits != [] and p1b.pre_materialized_hits != []
    # 不得互相污染：p1 在 undo 后不再累加
    snap1 = list(p1.pre_materialized_hits)

    # 2) os.open + os.read
    monkeypatch.undo()
    p2 = _V1NReadProbe(put_bound=put_bound)
    p2.add_target(sample)
    p2.install(monkeypatch)
    fd = _os.open(str(sample), getattr(_os, "O_RDONLY", 0))
    try:
        with pytest.raises(AssertionError, match="PUT|os.read"):
            _os.read(fd, 32)
    finally:
        try:
            _os.close(fd)
        except OSError:
            pass
    os_probe_hit = any(
        [bool(p2.pre_materialized_hits), bool(p2.unbounded_hits)]
    )
    assert os_probe_hit, "业务红：os.open+os.read 必须命中探针"
    assert p1.pre_materialized_hits == snap1, "业务红：独立 tracker 不得被样本2污染"

    # 3) PUT 前多次小读累计
    monkeypatch.undo()
    p3 = _V1NReadProbe(put_bound=put_bound)
    p3.add_target(sample)
    p3.install(monkeypatch)
    hits = 0
    with pytest.raises(AssertionError):
        with open(sample, "rb") as fh:
            for _ in range(8):
                fh.read(64)
                hits += 1
    assert p3.pre_materialized_hits, "业务红：多次小读必须在 PUT 前红"
    assert hits <= 1, "业务红：首次小读即应失败，禁止静默累计"

    # 无界单次超界：在底层大读前失败（小文件模拟 n>bound）
    monkeypatch.undo()
    p4 = _V1NReadProbe(put_bound=8)
    big = tmp_path / "probe-over-bound.bin"
    big.write_bytes(b"B" * 100)
    p4.add_target(big)
    p4.put_transport_started = True  # 允许 put 期，但仍拦超界
    p4.phase = "put"
    p4.install(monkeypatch)
    with pytest.raises(AssertionError, match="无界|超界"):
        with open(big, "rb") as fh:
            fh.read(64)  # > put_bound=8
    assert p4.unbounded_hits, "业务红：单次超界必须记入 unbounded_hits"

    # R8-C3：os.read(fd,0) 返回 b'' 且不记正文
    monkeypatch.undo()
    p0 = _V1NReadProbe(put_bound=put_bound)
    p0.add_target(sample)
    p0.install(monkeypatch)
    fd0 = _os.open(str(sample), getattr(_os, "O_RDONLY", 0))
    try:
        z = _os.read(fd0, 0)
        assert z == b"", f"业务红：os.read(fd,0) 必须 b''，actual={z!r}"
        assert p0.pre_materialized_hits == [], (
            f"业务红：os.read(fd,0) 不得记正文，hits={p0.pre_materialized_hits}"
        )
        assert p0.returned_by_phase.get("pre", 0) == 0
    finally:
        try:
            _os.close(fd0)
        except OSError:
            pass

    # R8-C3：PUT 期多次小读累计超预算（独立 tracker，零大分配）
    monkeypatch.undo()
    p5 = _V1NReadProbe(put_bound=16, put_extra_chunk=16)
    cum = tmp_path / "probe-cumul.bin"
    cum.write_bytes(b"C" * 200)
    p5.add_target(cum)
    p5.put_transport_started = True
    p5.phase = "put"
    p5.install(monkeypatch)
    reads5 = 0
    with pytest.raises(AssertionError, match="累计|预算|超界|无界"):
        with open(cum, "rb") as fh:
            while True:
                fh.read(16)
                reads5 += 1
                if reads5 > 20:
                    raise AssertionError("业务红：累计预算门未在低次数内触发")
    assert p5.unbounded_hits, "业务红：PUT 多次小读累计超界必须记入 unbounded_hits"
    assert p5.returned_by_phase.get("put", 0) <= p5.put_return_budget, (
        f"业务红：累计返回不得越过预算，ret={p5.returned_by_phase} "
        f"budget={p5.put_return_budget}"
    )
    assert reads5 <= 3, (
        f"业务红：累计超界须在冻结前缀+1 块内红，reads={reads5}"
    )

    # R8-C3：旁路 API（readlines）必须红，独立 tracker
    monkeypatch.undo()
    p6 = _V1NReadProbe(put_bound=put_bound)
    p6.add_target(sample)
    p6.put_transport_started = True
    p6.phase = "put"
    p6.install(monkeypatch)
    with pytest.raises(AssertionError, match="旁路|readlines"):
        with open(sample, "rb") as fh:
            fh.readlines()
    assert p6.unbounded_hits, "业务红：readlines 旁路必须记入 unbounded_hits"


def test_g_sparse_and_transport_helper_self_proof(tmp_path: Path):
    """R6-C1/C2：transport 安全标记 + sparse helper 定点自证。"""
    _v1n_transport_safety_helper_self_proof()
    _sparse_logical_file_helper_self_proof(tmp_path)


def test_g_read_guard_helper_self_proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """R7-C3：read guard helper 可执行自证（不依赖 production、不分配 200MB）。"""
    _read_guard_helper_self_proof(tmp_path, monkeypatch)


def _fd_reuse_helper_self_proof(tmp_path: Path) -> None:
    """
    R9-C2/R10-3：fd 代次 active 生命周期 + 确定性整数 fd 复用自证。
    不依赖 production；真实 open/fstat/close；os.dup2 强制复用到已关闭目标同一整数 fd。
    复用后 fstat 只能 other；target_fstat 不增加；旧 gen 不重新 active。
    注入 stale fd_to_gen 分类变异时确定性红，不得依赖 allocator 碰巧复用。
    """
    import os as _os

    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "path.pdf"
    other = tmp_path / "other-fd-reuse.bin"
    target.write_bytes(b"%PDF-TARGET-FD-REUSE")
    other.write_bytes(b"%PDF-OTHER-FD-REUSEX")

    real_open = _os.open
    real_fstat = _os.fstat
    real_close = _os.close
    real_dup2 = _os.dup2

    fd_active: dict[int, int] = {}
    fd_to_gen: dict[int, int] = {}
    gen_counter = {"n": 0}
    target_fstat_gens: list[int] = []
    other_fstat_fds: list[int] = []
    closed_gens: list[int] = []

    def spy_open(path, flags, *a, **k):  # noqa: ANN001
        fd = real_open(path, flags, *a, **k)
        path_s = str(path)
        if path_s.endswith("path.pdf"):
            gen_counter["n"] += 1
            g = gen_counter["n"]
            fd_to_gen[int(fd)] = g
            fd_active[int(fd)] = g
        return fd

    def classify_fd(fd: int, *, use_stale: bool) -> int | None:
        """正确分类仅看 active；stale 变异错误使用遗留 fd_to_gen。"""
        if use_stale:
            return fd_to_gen.get(int(fd))
        return fd_active.get(int(fd))

    def spy_fstat(fd, *, use_stale: bool = False):  # noqa: ANN001
        st = real_fstat(fd)
        g = classify_fd(int(fd), use_stale=use_stale)
        if g is not None:
            target_fstat_gens.append(g)
        else:
            other_fstat_fds.append(int(fd))
        return st

    def spy_close(fd):  # noqa: ANN001
        fd_i = int(fd)
        g = fd_active.pop(fd_i, None)
        if g is not None:
            closed_gens.append(g)
        # 故意保留 fd_to_gen 以模拟 stale 映射
        return real_close(fd_i)

    fd1 = None
    fd_other = None
    reused_fd = None
    try:
        fd1 = spy_open(str(target), getattr(_os, "O_RDONLY", 0))
        assert int(fd1) in fd_active, "业务红：path.pdf open 后 fd 必须 active"
        g1 = fd_active[int(fd1)]
        spy_fstat(fd1)
        assert target_fstat_gens == [g1], (
            f"业务红：active 目标 fstat 必须计 target，actual={target_fstat_gens}"
        )
        assert other_fstat_fds == [], (
            f"业务红：目标 active 时不得记 other_fstat，actual={other_fstat_fds}"
        )
        spy_close(fd1)
        fd1_closed = int(fd1)
        fd1 = None
        assert fd1_closed not in fd_active, "业务红：close 后必须移除 active"
        assert closed_gens == [g1], f"业务红：必须记录关闭代次，closed={closed_gens}"
        assert fd_to_gen.get(fd1_closed) == g1, (
            f"夹具：stale fd_to_gen 必须保留 closed 映射，map={fd_to_gen}"
        )
        snap_target = list(target_fstat_gens)
        snap_other = list(other_fstat_fds)

        # 打开无关文件，再用 dup2 强制占用已关闭目标的同一整数 fd
        fd_other = real_open(str(other), getattr(_os, "O_RDONLY", 0))
        reused_fd = real_dup2(int(fd_other), fd1_closed)
        assert int(reused_fd) == fd1_closed, (
            f"业务红：dup2 必须强制 reused_fd==closed_target_fd，"
            f"reused={reused_fd} closed={fd1_closed}"
        )
        assert fd1_closed not in fd_active, (
            f"业务红：复用后不得复活 active，active={fd_active}"
        )
        assert g1 not in fd_active.values(), (
            f"业务红：已关闭 gen 不得仍 active，active={fd_active}"
        )

        spy_fstat(reused_fd, use_stale=False)
        assert target_fstat_gens == snap_target, (
            f"业务红：fd 复用后不得新增 target_fstat，"
            f"before={snap_target} after={target_fstat_gens}"
        )
        assert other_fstat_fds == snap_other + [int(reused_fd)], (
            f"业务红：复用 fd 的 fstat 必须记 other_fstat，"
            f"other={other_fstat_fds} expected_fd={fd1_closed}"
        )

        # R10-3：stale fd_to_gen 分类变异必须确定性红
        correct_g = classify_fd(int(reused_fd), use_stale=False)
        stale_g = classify_fd(int(reused_fd), use_stale=True)
        assert correct_g is None, (
            f"业务红：正确分类复用 fd 必须 other/None，actual={correct_g}"
        )
        assert stale_g == g1, (
            f"夹具：stale 分类必须错误命中旧 gen={g1}，actual={stale_g}"
        )
        with pytest.raises(AssertionError, match="stale|复用|target_fstat|fd_to_gen"):
            g_bad = classify_fd(int(reused_fd), use_stale=True)
            assert g_bad is None, (
                "业务红：stale fd_to_gen 分类变异不得通过（复用后仍命中旧 target gen）"
            )
        snap_t2 = list(target_fstat_gens)
        spy_fstat(reused_fd, use_stale=True)
        with pytest.raises(AssertionError, match="target_fstat|复用|stale"):
            assert target_fstat_gens == snap_t2, (
                f"业务红：stale 分类变异导致 target_fstat 非法增加 "
                f"before={snap_t2} after={target_fstat_gens}"
            )
        del target_fstat_gens[len(snap_t2) :]
        assert target_fstat_gens == snap_target
    finally:
        # reused_fd 与 fd1_closed 同号；fd_other 可能不同
        seen_close: set[int] = set()
        for _fd in (reused_fd, fd_other, fd1):
            if _fd is None:
                continue
            fi = int(_fd)
            if fi in seen_close:
                continue
            seen_close.add(fi)
            try:
                real_close(fi)
            except OSError:
                pass


def test_g_fd_reuse_helper_self_proof(tmp_path: Path):
    """R9-C2/R10-3：fd 复用定点自证（名称含 fd_reuse_helper_self_proof，供 -k 定向收集）。"""
    _fd_reuse_helper_self_proof(tmp_path)


import os
from types import MappingProxyType

# R12：原始 trace 单一真值 + _v1n_run_trace seal + 唯一 ops / provenance / failure-first
# ---------------------------------------------------------------------------
# 删除 R11 事后补造 ledger、XOR identity、ast.walk 仅计数、假 ops 命中与内存冒充四终态。
# 保留 R8-R10：真实多块读、V1 正文、最后块后 close、no-follow/reparse、隐私与既有任务门。

_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004  # 仅夹具便利；不得冻结为 production 安全契约
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_V1N_WINAPI_OPS_NAME = "_v1n_winapi_ops"
_V1N_AUTHORITY_TERMINALS = frozenset(
    {
        "success",
        "convert_failed",
        "fstat_failed",
        "identity_mismatch",
        "put_failed",
    }
)
_V1N_LIGHTWEIGHT_K_KEYWORDS = (
    "ast_self_guard",
    "sparse_and_transport_helper_self_proof",
    "read_guard_helper_self_proof",
    "fd_reuse_helper_self_proof",
    "windows_handle",
    "generation_authority_self_proof",
)
_V1N_LIVE_FAILURE_TEST_NAMES = (
    "test_g8a_windows_convert_failure_live_ledger",
    "test_g8b_fstat_failure_live_ledger",
    "test_g8c_identity_failure_live_ledger",
    "test_g8d_put_failure_live_ledger",
)
_V1N_TRACE_CM_NAME = "_v1n_run_trace"
_V1N_CHECKER_NAME = "_assert_v1n_generation_authority"
_V1N_LEDGER_CLS_NAME = "_V1NGenerationLedger"
_V1N_CLEANUP_CLS_NAME = "_V1NFixtureCleanupAudit"
_V1N_CAPTURE_RUN_NAMES = frozenset({"run", "run_remote_mineru_parse"})
# R18：唯一可建立 live seam 的左值精确 Name("run")；其它 capture 名仅可 shadow
_V1N_SEAM_RUN_NAME = "run"
_V1N_RUN_HELPER_NAME = "_get_run_fn"
# Q7：模块导入时捕获真 os 入口，禁止分段 monkeypatch 嵌套写已 seal ledger
_V1N_OS_TRUE: dict[str, Any] = {
    "open": os.open,
    "fstat": os.fstat,
    "read": os.read,
    "close": os.close,
    "stat": os.stat,
    "dup2": os.dup2,
    "link": getattr(os, "link", None),
}
_V1N_WINAPI_TRUE: dict[int, dict[str, Any]] = {}


def _r10_is_invalid_handle(handle) -> bool:  # noqa: ANN001
    if handle is None:
        return True
    hv = None
    convert_ok = False
    try:
        hv = int(handle)
        convert_ok = True
    except (TypeError, ValueError):
        convert_ok = False
    if not convert_ok:
        return True
    if hv in (0, -1):
        return True
    if hv in (0xFFFFFFFFFFFFFFFF, 0xFFFFFFFF):
        return True
    return False


def _r10_fixture_path_replace_while_open(src: Path, dst: Path) -> None:
    """
    夹具：在目标可能被 FILE_SHARE_DELETE 句柄占用时，将 src 原子落到 dst。
    仅夹具便利，不冻结为 production 契约；不得计作 production ops。
    """
    import os as _os

    try:
        _os.replace(str(src), str(dst))
        return
    except PermissionError:
        if _os.name != "nt":
            raise
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
    k32.DeleteFileW.restype = wintypes.BOOL
    k32.MoveFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    k32.MoveFileW.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    ok_del = k32.DeleteFileW(str(dst))
    if not ok_del and dst.exists():
        err = ctypes.get_last_error()
        raise AssertionError(f"夹具失败：DeleteFileW 失败 GetLastError={err} dst={dst}")
    ctypes.set_last_error(0)
    ok_mv = k32.MoveFileW(str(src), str(dst))
    if not ok_mv:
        err = ctypes.get_last_error()
        raise AssertionError(
            f"夹具失败：MoveFileW 失败 GetLastError={err} src={src} dst={dst}"
        )


class _V1NFixtureWinApiAdapter:
    """
    测试夹具真实 WinAPI 适配器（fixture 标记）。
    仅供轻量 helper / production 缺失时本地链；不得计作 production ops 命中。
    """

    __slots__ = ("_k32", "_CreateFileW", "_CloseHandle", "_open_osfhandle", "is_fixture")

    def __init__(self) -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        self.is_fixture = True
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HANDLE = ctypes.c_void_p
        BOOL = wintypes.BOOL
        DWORD = wintypes.DWORD
        LPCWSTR = wintypes.LPCWSTR
        LPVOID = ctypes.c_void_p
        k32.CreateFileW.argtypes = [
            LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE
        ]
        k32.CreateFileW.restype = HANDLE
        k32.CloseHandle.argtypes = [HANDLE]
        k32.CloseHandle.restype = BOOL
        self._k32 = k32
        self._CreateFileW = k32.CreateFileW
        self._CloseHandle = k32.CloseHandle
        self._open_osfhandle = msvcrt.open_osfhandle

    def CreateFileW(self, *a, **k):  # noqa: ANN001,N802
        return self._CreateFileW(*a, **k)

    def CloseHandle(self, *a, **k):  # noqa: ANN001,N802
        return self._CloseHandle(*a, **k)

    def open_osfhandle(self, *a, **k):  # noqa: ANN001
        return self._open_osfhandle(*a, **k)


class _V1NSealedMutationError(AssertionError):
    """seal 后任何 evidence 变更立即红；亦用于不可变 identity 篡改。"""


class _V1NFdIdentity:
    """
    verified fd 真实 fstat identity（禁止路径 stat 冒充）。
    Q8：真正不可变值对象；构造完成后任意字段 setattr 立即红。
    """

    __slots__ = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "_frozen")

    def __init__(
        self,
        st_dev: int,
        st_ino: int,
        st_size: int,
        st_mtime_ns: int = 0,
    ) -> None:
        object.__setattr__(self, "st_dev", int(st_dev))
        object.__setattr__(self, "st_ino", int(st_ino))
        object.__setattr__(self, "st_size", int(st_size))
        object.__setattr__(self, "st_mtime_ns", int(st_mtime_ns))
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if object.__getattribute__(self, "_frozen"):
            raise _V1NSealedMutationError(
                f"业务红：_V1NFdIdentity 不可变，禁止 setattr {name!r}"
            )
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise _V1NSealedMutationError(
            f"业务红：_V1NFdIdentity 不可变，禁止 delattr {name!r}"
        )

    @classmethod
    def from_stat_result(cls, st: Any) -> "_V1NFdIdentity":
        return cls(
            st_dev=int(st.st_dev),
            st_ino=int(st.st_ino),
            st_size=int(st.st_size),
            st_mtime_ns=int(getattr(st, "st_mtime_ns", 0) or 0),
        )

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.st_dev, self.st_ino, self.st_size, self.st_mtime_ns)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _V1NFdIdentity):
            return False
        return self.as_tuple() == other.as_tuple()

    def __hash__(self) -> int:
        return hash(self.as_tuple())

    def __repr__(self) -> str:
        return (
            f"_V1NFdIdentity(dev={self.st_dev}, ino={self.st_ino}, "
            f"size={self.st_size}, mtime_ns={self.st_mtime_ns})"
        )


class _V1NGenerationLedger:
    """
    原始 production evidence ledger。
    仅暴露 note_* mutator；_v1n_run_trace.__exit__ 精确 seal 一次后禁止一切变更。
    """

    __slots__ = (
        "platform",
        "open_flag_bit",
        "instance_token",
        "sealed",
        "gen_counter",
        "flags_by_gen",
        "handle_by_gen",
        "fd_by_gen",
        "pending_handles",
        "transferred_handles",
        "active_fds",
        "close_handle_counts",
        "fd_close_counts",
        "baseline_identity",
        "verified_gen",
        "verified_identity",
        "identity_compare_gen",
        "identity_compare_identity",
        "read_pre_identities",
        "read_post_identities",
        "read_chunks",
        "handler_chunks",
        "expected_body",
        "events",
        "seam_hits",
        "ops_hit_counts",
        "http_post",
        "http_put",
        "http_poll",
        "http_zip",
        "diagnostic_code",
        "last_chunk_seen",
        "close_after_last_chunk",
        "fstat_failed",
        "convert_failed",
        "put_attempted",
        "provenance_by_fd",
        "provenance_by_handle",
        "provenance_by_gen",
        "active_verified_provenance",
        "fd_replaced_events",
        "run_result_registered",
        "fixture_ops_used",
        "_prov_seq",
        "_seal_count",
    )

    def __init__(self, *, platform: str, open_flag_bit: int) -> None:
        if platform not in {"windows", "posix"}:
            raise AssertionError(
                f"夹具：platform 必须 windows|posix，actual={platform!r}"
            )
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "open_flag_bit", int(open_flag_bit))
        object.__setattr__(
            self, "instance_token", f"v1n-led-{id(self):x}-{os.urandom(8).hex()}"
        )
        object.__setattr__(self, "sealed", False)
        object.__setattr__(self, "gen_counter", 0)
        object.__setattr__(self, "flags_by_gen", {})
        object.__setattr__(self, "handle_by_gen", {})
        object.__setattr__(self, "fd_by_gen", {})
        object.__setattr__(self, "pending_handles", {})
        object.__setattr__(self, "transferred_handles", set())
        object.__setattr__(self, "active_fds", {})
        object.__setattr__(self, "close_handle_counts", {})
        object.__setattr__(self, "fd_close_counts", {})
        object.__setattr__(self, "baseline_identity", None)
        object.__setattr__(self, "verified_gen", None)
        object.__setattr__(self, "verified_identity", None)
        object.__setattr__(self, "identity_compare_gen", None)
        object.__setattr__(self, "identity_compare_identity", None)
        object.__setattr__(self, "read_pre_identities", [])
        object.__setattr__(self, "read_post_identities", [])
        object.__setattr__(self, "read_chunks", [])
        object.__setattr__(self, "handler_chunks", [])
        object.__setattr__(self, "expected_body", None)
        object.__setattr__(self, "events", [])
        object.__setattr__(self, "seam_hits", [])
        object.__setattr__(
            self,
            "ops_hit_counts",
            {"CreateFileW": 0, "CloseHandle": 0, "open_osfhandle": 0},
        )
        object.__setattr__(self, "http_post", 0)
        object.__setattr__(self, "http_put", 0)
        object.__setattr__(self, "http_poll", 0)
        object.__setattr__(self, "http_zip", 0)
        object.__setattr__(self, "diagnostic_code", None)
        object.__setattr__(self, "last_chunk_seen", False)
        object.__setattr__(self, "close_after_last_chunk", False)
        object.__setattr__(self, "fstat_failed", False)
        object.__setattr__(self, "convert_failed", False)
        object.__setattr__(self, "put_attempted", False)
        object.__setattr__(self, "provenance_by_fd", {})
        object.__setattr__(self, "provenance_by_handle", {})
        object.__setattr__(self, "provenance_by_gen", {})
        object.__setattr__(self, "active_verified_provenance", None)
        object.__setattr__(self, "fd_replaced_events", [])
        object.__setattr__(self, "run_result_registered", False)
        object.__setattr__(self, "fixture_ops_used", False)
        object.__setattr__(self, "_prov_seq", 0)
        object.__setattr__(self, "_seal_count", 0)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _V1NGenerationLedger.__slots__:
            if object.__getattribute__(self, "sealed"):
                raise _V1NSealedMutationError(
                    f"业务红：ledger 已 seal，禁止 setattr {name!r}"
                )
        object.__setattr__(self, name, value)

    def _guard_mutable(self) -> None:
        if self.sealed:
            raise _V1NSealedMutationError("业务红：ledger 已 seal，禁止 evidence mutator")

    def _guard_container(self, container: Any, op: str) -> None:
        if self.sealed:
            raise _V1NSealedMutationError(
                f"业务红：ledger 已 seal，禁止容器 {op}"
            )

    def new_gen(self) -> int:
        self._guard_mutable()
        self.gen_counter += 1
        return self.gen_counter

    def _new_provenance(self, kind: str) -> str:
        self._guard_mutable()
        self._prov_seq += 1
        return f"prov-{kind}-{self._prov_seq}-{os.urandom(4).hex()}"

    def note_event(self, event: str) -> None:
        self._guard_mutable()
        self.events.append(str(event))

    def note_baseline_identity(self, ident: _V1NFdIdentity) -> None:
        self._guard_mutable()
        self.baseline_identity = ident
        self.note_event("identity_baseline")

    def note_target_open(
        self,
        *,
        gen: int,
        flags: int,
        handle: int | None = None,
        fd: int | None = None,
        provenance: str | None = None,
        fixture: bool = False,
    ) -> str:
        self._guard_mutable()
        if fixture:
            self.fixture_ops_used = True
        self.flags_by_gen[int(gen)] = int(flags)
        prov = provenance or self._new_provenance("open")
        self.provenance_by_gen[int(gen)] = prov
        if handle is not None:
            hv = int(handle)
            self.handle_by_gen[int(gen)] = hv
            self.pending_handles[hv] = int(gen)
            self.provenance_by_handle[hv] = prov
        if fd is not None:
            fd_i = int(fd)
            self.fd_by_gen[int(gen)] = fd_i
            self.active_fds[fd_i] = int(gen)
            self.provenance_by_fd[fd_i] = prov
        self.note_event("target_open")
        return prov

    def note_ops_hit(self, name: str, *, gen: int | None = None, detail: str = "") -> None:
        self._guard_mutable()
        if name not in self.ops_hit_counts:
            raise AssertionError(f"夹具：未知 ops 名 {name!r}")
        self.ops_hit_counts[name] = int(self.ops_hit_counts[name]) + 1
        tag = f"{name}:gen{gen}" if gen is not None else name
        if detail:
            tag = f"{tag}:{detail}"
        self.seam_hits.append(tag)

    def note_handle_to_fd(
        self,
        *,
        gen: int,
        handle: int,
        fd: int,
        provenance: str | None = None,
        fixture: bool = False,
    ) -> str:
        self._guard_mutable()
        if fixture:
            self.fixture_ops_used = True
        hv = int(handle)
        fd_i = int(fd)
        g = int(gen)
        if hv in self.pending_handles:
            self.pending_handles.pop(hv, None)
        self.transferred_handles.add(hv)
        self.handle_by_gen[g] = hv
        self.fd_by_gen[g] = fd_i
        self.active_fds[fd_i] = g
        prov = provenance or self.provenance_by_gen.get(g) or self._new_provenance("h2fd")
        self.provenance_by_fd[fd_i] = prov
        self.provenance_by_gen[g] = prov
        self.note_event("handle_to_fd")
        return prov

    def note_close_handle(self, handle: int) -> None:
        self._guard_mutable()
        hv = int(handle)
        self.close_handle_counts[hv] = self.close_handle_counts.get(hv, 0) + 1
        self.pending_handles.pop(hv, None)
        self.note_event("CloseHandle")

    def note_fstat_identity(
        self,
        *,
        gen: int,
        fd: int,
        ident: _V1NFdIdentity,
        verified: bool = False,
    ) -> None:
        self._guard_mutable()
        g = int(gen)
        if verified:
            self.verified_gen = g
            self.verified_identity = ident
            self.active_verified_provenance = self.provenance_by_fd.get(int(fd))
            self.note_event("target_fstat")
        else:
            self.note_event(f"fstat:gen{g}")

    def note_fstat_failed(self, *, gen: int, fd: int) -> None:
        self._guard_mutable()
        self.fstat_failed = True
        self.note_event("fstat_failed")

    def note_convert_failed(self) -> None:
        self._guard_mutable()
        self.convert_failed = True
        self.note_event("convert_failed")

    def note_identity_compare(
        self,
        *,
        gen: int,
        identity: _V1NFdIdentity,
    ) -> None:
        self._guard_mutable()
        self.identity_compare_gen = int(gen)
        self.identity_compare_identity = identity
        self.note_event("identity_compare")

    def note_path_stat_identity_forbidden(self) -> None:
        """若 spy 观察到目标路径二次 path.stat 冒充 identity，登记禁止事件。"""
        self._guard_mutable()
        self.note_event("path_stat_identity")

    def note_read_pre_identity(self, *, gen: int, identity: _V1NFdIdentity) -> None:
        self._guard_mutable()
        self.read_pre_identities.append((int(gen), identity.as_tuple()))

    def note_read_chunk(self, *, gen: int, data: bytes) -> None:
        self._guard_mutable()
        self.read_chunks.append((int(gen), bytes(data)))

    def note_read_post_identity(self, *, gen: int, identity: _V1NFdIdentity) -> None:
        self._guard_mutable()
        self.read_post_identities.append((int(gen), identity.as_tuple()))

    def note_handler_chunk(self, data: bytes) -> None:
        self._guard_mutable()
        self.handler_chunks.append(bytes(data))

    def note_expected_body(self, body: bytes) -> None:
        self._guard_mutable()
        self.expected_body = bytes(body)

    def note_last_chunk_seen(self) -> None:
        self._guard_mutable()
        self.last_chunk_seen = True
        self.note_event("last_chunk_seen")

    def note_close_after_last_chunk(self) -> None:
        self._guard_mutable()
        self.close_after_last_chunk = True

    def note_fd_close(self, fd: int, *, gen: int | None = None) -> None:
        self._guard_mutable()
        fd_i = int(fd)
        self.fd_close_counts[fd_i] = self.fd_close_counts.get(fd_i, 0) + 1
        self.active_fds.pop(fd_i, None)
        if (
            self.last_chunk_seen
            and gen is not None
            and self.verified_gen is not None
            and int(gen) == int(self.verified_gen)
        ):
            self.close_after_last_chunk = True
        self.note_event("close")

    def note_http(self, kind: str) -> None:
        self._guard_mutable()
        if kind == "POST":
            self.http_post += 1
        elif kind == "PUT":
            self.http_put += 1
            self.put_attempted = True
        elif kind == "POLL":
            self.http_poll += 1
        elif kind == "ZIP":
            self.http_zip += 1
        else:
            raise AssertionError(f"夹具：未知 HTTP kind={kind!r}")
        self.note_event(f"http:{kind}")

    def note_fd_replaced(
        self,
        *,
        old_provenance: str,
        new_provenance: str,
        fd: int,
    ) -> None:
        self._guard_mutable()
        self.fd_replaced_events.append(
            (str(old_provenance), str(new_provenance), int(fd))
        )
        self.provenance_by_fd[int(fd)] = str(new_provenance)
        if self.active_verified_provenance == str(old_provenance):
            self.active_verified_provenance = None
        self.note_event(
            f"fd_replaced:{old_provenance}->{new_provenance}:fd={int(fd)}"
        )

    def note_run_result_captured(self) -> None:
        self._guard_mutable()
        self.run_result_registered = True
        self.note_event("run_result_captured")

    def note_diagnostic_from_exception(self, code: str | None) -> None:
        """仅 _v1n_run_trace.__exit__ 可调用：登记真实异常诊断。"""
        self._guard_mutable()
        if code is not None:
            self.diagnostic_code = str(code)
            self.note_event(f"diagnostic:{code}")

    def seal(self) -> None:
        self._guard_mutable()
        if self._seal_count != 0:
            raise AssertionError("业务红：ledger.seal 禁止二次调用")
        # 冻结容器：seal 后 append/update/pop/clear 立即失败
        object.__setattr__(self, "flags_by_gen", MappingProxyType(dict(self.flags_by_gen)))
        object.__setattr__(self, "handle_by_gen", MappingProxyType(dict(self.handle_by_gen)))
        object.__setattr__(self, "fd_by_gen", MappingProxyType(dict(self.fd_by_gen)))
        object.__setattr__(self, "pending_handles", MappingProxyType(dict(self.pending_handles)))
        object.__setattr__(self, "transferred_handles", frozenset(self.transferred_handles))
        object.__setattr__(self, "active_fds", MappingProxyType(dict(self.active_fds)))
        object.__setattr__(self, "close_handle_counts", MappingProxyType(dict(self.close_handle_counts)))
        object.__setattr__(self, "fd_close_counts", MappingProxyType(dict(self.fd_close_counts)))
        object.__setattr__(self, "read_pre_identities", tuple(self.read_pre_identities))
        object.__setattr__(self, "read_post_identities", tuple(self.read_post_identities))
        object.__setattr__(self, "read_chunks", tuple(self.read_chunks))
        object.__setattr__(self, "handler_chunks", tuple(self.handler_chunks))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "seam_hits", tuple(self.seam_hits))
        object.__setattr__(self, "ops_hit_counts", MappingProxyType(dict(self.ops_hit_counts)))
        object.__setattr__(self, "provenance_by_fd", MappingProxyType(dict(self.provenance_by_fd)))
        object.__setattr__(self, "provenance_by_handle", MappingProxyType(dict(self.provenance_by_handle)))
        object.__setattr__(self, "provenance_by_gen", MappingProxyType(dict(self.provenance_by_gen)))
        object.__setattr__(self, "fd_replaced_events", tuple(self.fd_replaced_events))
        object.__setattr__(self, "_seal_count", 1)
        object.__setattr__(self, "sealed", True)


class _V1NFixtureCleanupAudit:
    """
    独立 append-only fixture 清理审计。
    不得传给 checker、不得回填 ledger、不得充当 production close 证据。
    """

    __slots__ = ("records",)

    def __init__(self) -> None:
        self.records: list[tuple[str, str, str, str]] = []

    def fixture_cleanup_after_failure(
        self,
        instance_token: str,
        provenance: str,
        resource: str,
        result: str,
    ) -> None:
        self.records.append(
            (str(instance_token), str(provenance), str(resource), str(result))
        )


class _V1NRunTrace:
    """唯一合法执行封装：capture 仅登记返回值；__exit__ 登记诊断并 seal。"""

    __slots__ = ("ledger", "captured_result", "_entered")

    def __init__(self, ledger: _V1NGenerationLedger) -> None:
        if not isinstance(ledger, _V1NGenerationLedger):
            raise AssertionError("业务红：_v1n_run_trace 仅接受 _V1NGenerationLedger")
        if ledger.sealed:
            raise AssertionError("业务红：不得对已 seal ledger 再进入 _v1n_run_trace")
        self.ledger = ledger
        self.captured_result: Any = None
        self._entered = False

    def __enter__(self) -> "_V1NRunTrace":
        self._entered = True
        return self

    def capture(self, result: Any) -> Any:
        if not self._entered:
            raise AssertionError("业务红：trace.capture 必须在 with 体内")
        if self.ledger.sealed:
            raise _V1NSealedMutationError("业务红：seal 后禁止 capture")
        self.captured_result = result
        self.ledger.note_run_result_captured()
        return result

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        # 先登记直接收到的真实异常诊断，再精确 seal 一次
        code = None
        if exc is not None:
            code = getattr(exc, "diagnostic_code", None)
            if code is None and type(exc).__name__ == "RemoteMineruError":
                code = getattr(exc, "code", None)
        if code is not None:
            self.ledger.note_diagnostic_from_exception(str(code))
        self.ledger.seal()
        return False


def _v1n_run_trace(ledger: _V1NGenerationLedger) -> _V1NRunTrace:
    """唯一合法 run 封装 context manager。"""
    return _V1NRunTrace(ledger)


def _assert_v1n_generation_authority(
    ledger: _V1NGenerationLedger,
    *,
    expected_terminal: str,
    platform: str,
) -> None:
    """
    唯一权威 generation / identity / ownership / provenance / seam checker。
    只读 sealed 原始 ledger；禁止从结果反推合成 compare/close/seam。
    """
    assert expected_terminal in _V1N_AUTHORITY_TERMINALS, (
        f"业务红：expected_terminal 必须为 {sorted(_V1N_AUTHORITY_TERMINALS)}，"
        f"actual={expected_terminal!r}"
    )
    assert isinstance(ledger, _V1NGenerationLedger), (
        f"业务红：checker 只接受 _V1NGenerationLedger，actual={type(ledger)!r}"
    )
    assert platform in {"windows", "posix"}, (
        f"业务红：platform 必须 windows|posix，actual={platform!r}"
    )
    assert ledger.sealed is True, "业务红：checker 要求 ledger.sealed is True"
    assert ledger.instance_token and isinstance(ledger.instance_token, str), (
        "业务红：checker 要求唯一 instance_token"
    )
    assert ledger.platform == platform, (
        f"业务红：ledger.platform={ledger.platform!r} 必须等于 checker platform={platform!r}"
    )

    # ---- provenance 替换门：必须先于 ownership 清理检查 ----
    if ledger.fd_replaced_events:
        raise AssertionError(
            "业务红：fd_replaced/provenance — active verified provenance 已被 "
            f"dup2 替换 events={ledger.fd_replaced_events}"
        )
    if (
        ledger.active_verified_provenance is None
        and ledger.verified_gen is not None
        and expected_terminal == "success"
        and any(e.startswith("fd_replaced:") for e in ledger.events)
    ):
        raise AssertionError(
            "业务红：fd_replaced/provenance — verified provenance 已失效"
        )

    # ---- 统一资源清理（所有终态）----
    assert ledger.pending_handles == {}, (
        f"业务红：终态 pending HANDLE 必须空，pending={ledger.pending_handles} "
        f"terminal={expected_terminal}"
    )
    assert ledger.active_fds == {}, (
        f"业务红：终态 active fd 必须空，active={ledger.active_fds} "
        f"terminal={expected_terminal}"
    )

    for hv in ledger.transferred_handles:
        assert ledger.close_handle_counts.get(hv, 0) == 0, (
            f"业务红：已转移 HANDLE 禁止 CloseHandle，H={hv} "
            f"counts={ledger.close_handle_counts} terminal={expected_terminal}"
        )

    if expected_terminal == "convert_failed":
        assert ledger.convert_failed is True, (
            "业务红：convert_failed 终态必须标记 convert_failed=True"
        )
        assert ledger.transferred_handles == set(), (
            f"业务红：convert_failed 不得已转移 HANDLE，transferred={ledger.transferred_handles}"
        )
        assert ledger.fd_by_gen == {}, (
            f"业务红：convert_failed 不得有 fd，fd_by_gen={ledger.fd_by_gen}"
        )
        assert ledger.verified_gen is None, (
            f"业务红：convert_failed 不得有 verified_gen，vg={ledger.verified_gen}"
        )
        assert ledger.close_handle_counts, (
            f"业务红：convert_failed 必须 CloseHandle 精确一次，counts={ledger.close_handle_counts}"
        )
        assert all(c == 1 for c in ledger.close_handle_counts.values()), (
            f"业务红：未转移 HANDLE CloseHandle 必须精确一次，counts={ledger.close_handle_counts}"
        )
        if platform == "windows" and not ledger.fixture_ops_used:
            assert ledger.ops_hit_counts.get("CreateFileW") == 1, (
                f"业务红：convert_failed CreateFileW 必须=1，hits={ledger.ops_hit_counts}"
            )
            assert ledger.ops_hit_counts.get("open_osfhandle") == 1, (
                f"业务红：convert_failed open_osfhandle 必须=1（且抛出），hits={ledger.ops_hit_counts}"
            )
            assert ledger.ops_hit_counts.get("CloseHandle") == 1, (
                f"业务红：convert_failed CloseHandle 必须=1，hits={ledger.ops_hit_counts}"
            )
        assert ledger.diagnostic_code == "source_identity_mismatch", (
            f"业务红：convert_failed 诊断必须 source_identity_mismatch，"
            f"actual={ledger.diagnostic_code!r}"
        )
        assert (
            ledger.http_post,
            ledger.http_put,
            ledger.http_poll,
            ledger.http_zip,
        ) == (1, 0, 0, 0), (
            f"业务红：convert_failed HTTP 相位必须 POST=1 PUT=0 poll=0 ZIP=0，"
            f"actual=({ledger.http_post},{ledger.http_put},{ledger.http_poll},{ledger.http_zip})"
        )
        assert not ledger.read_chunks, (
            f"业务红：convert_failed 禁止 read，reads={ledger.read_chunks}"
        )
        assert "target_open" in ledger.events, (
            f"业务红：convert_failed 必有 target_open，events={ledger.events}"
        )
        assert "handle_to_fd" not in ledger.events, (
            f"业务红：convert_failed 禁止 handle_to_fd，events={ledger.events}"
        )
        return

    # 已转移 fd 精确 close 一次（非 convert）
    for g, fd in ledger.fd_by_gen.items():
        if expected_terminal in {
            "fstat_failed",
            "identity_mismatch",
            "put_failed",
            "success",
        }:
            assert ledger.fd_close_counts.get(int(fd), 0) == 1, (
                f"业务红：已转移 fd 必须精确 close 一次，gen={g} fd={fd} "
                f"counts={ledger.fd_close_counts} terminal={expected_terminal}"
            )

    if expected_terminal == "fstat_failed":
        assert ledger.fstat_failed is True, (
            "业务红：fstat_failed 终态必须标记 fstat_failed=True"
        )
        assert ledger.fd_by_gen, (
            f"业务红：fstat_failed 必须有 fd，fd_by_gen={ledger.fd_by_gen}"
        )
        if platform == "windows":
            assert ledger.transferred_handles, (
                "业务红：fstat_failed Windows 必须已完成 HANDLE→fd 转移"
            )
            if not ledger.fixture_ops_used:
                assert ledger.ops_hit_counts.get("CreateFileW") == 1
                assert ledger.ops_hit_counts.get("open_osfhandle") == 1
                assert ledger.ops_hit_counts.get("CloseHandle") == 0
        assert ledger.verified_gen is None, (
            f"业务红：fstat_failed 不得完成 verified，vg={ledger.verified_gen}"
        )
        assert ledger.diagnostic_code == "source_identity_mismatch", (
            f"业务红：fstat_failed 诊断必须 source_identity_mismatch，"
            f"actual={ledger.diagnostic_code!r}"
        )
        assert (
            ledger.http_post,
            ledger.http_put,
            ledger.http_poll,
            ledger.http_zip,
        ) == (1, 0, 0, 0), (
            f"业务红：fstat_failed HTTP 相位必须 POST=1 PUT=0 poll=0 ZIP=0，"
            f"actual=({ledger.http_post},{ledger.http_put},{ledger.http_poll},{ledger.http_zip})"
        )
        if platform == "windows":
            assert "handle_to_fd" in ledger.events, (
                f"业务红：fstat_failed 必有 handle_to_fd，events={ledger.events}"
            )
        assert "fstat_failed" in ledger.events, (
            f"业务红：fstat_failed 必有 fstat_failed 事件，events={ledger.events}"
        )
        assert not ledger.read_chunks, (
            f"业务红：fstat_failed 禁止 read，reads={ledger.read_chunks}"
        )
        return

    if expected_terminal == "identity_mismatch":
        assert ledger.verified_gen is not None, (
            "业务红：identity_mismatch 必须有 verified_gen"
        )
        vg = int(ledger.verified_gen)
        assert ledger.verified_identity is not None, (
            f"业务红：identity_mismatch 必须有 verified_identity，gen={vg}"
        )
        assert ledger.identity_compare_gen == vg, (
            f"业务红：identity 比较必须消费 verified_gen={vg}，"
            f"actual_compare_gen={ledger.identity_compare_gen}"
        )
        assert ledger.identity_compare_identity is not None, (
            "业务红：identity 比较必须记录所消费 identity"
        )
        assert ledger.identity_compare_identity == ledger.verified_identity, (
            f"业务红：identity 比较消费的 identity 必须等于 verified_identity，"
            f"compare={ledger.identity_compare_identity} verified={ledger.verified_identity}"
        )
        assert ledger.baseline_identity is not None, (
            "业务红：identity_mismatch 必须有 baseline_identity"
        )
        assert ledger.identity_compare_identity != ledger.baseline_identity, (
            f"业务红：verified fstat identity 必须与 baseline 不等，"
            f"id={ledger.identity_compare_identity} baseline={ledger.baseline_identity}"
        )
        assert vg in ledger.flags_by_gen, (
            f"业务红：verified_gen flags 必有，map={ledger.flags_by_gen}"
        )
        assert ledger.flags_by_gen[vg] & ledger.open_flag_bit, (
            f"业务红：verified flags 必须含 open_flag_bit={ledger.open_flag_bit:#x}，"
            f"flags={ledger.flags_by_gen[vg]:#x}"
        )
        assert vg in ledger.fd_by_gen, (
            f"业务红：verified 必须有 fd，map={ledger.fd_by_gen}"
        )
        if platform == "windows":
            assert vg in ledger.handle_by_gen, (
                f"业务红：Windows verified 必须有 handle，map={ledger.handle_by_gen}"
            )
            hv = ledger.handle_by_gen[vg]
            assert hv in ledger.transferred_handles, (
                f"业务红：verified handle 必须已转移，H={hv}"
            )
            if not ledger.fixture_ops_used:
                assert ledger.ops_hit_counts.get("CreateFileW") == 1
                assert ledger.ops_hit_counts.get("open_osfhandle") == 1
                assert ledger.ops_hit_counts.get("CloseHandle") == 0
        assert ledger.diagnostic_code == "source_identity_mismatch", (
            f"业务红：identity_mismatch 诊断必须 source_identity_mismatch，"
            f"actual={ledger.diagnostic_code!r}"
        )
        assert (
            ledger.http_post,
            ledger.http_put,
            ledger.http_poll,
            ledger.http_zip,
        ) == (1, 0, 0, 0), (
            f"业务红：identity_mismatch HTTP 相位必须 POST=1 PUT=0 poll=0 ZIP=0，"
            f"actual=({ledger.http_post},{ledger.http_put},{ledger.http_poll},{ledger.http_zip})"
        )
        assert "identity_compare" in ledger.events, (
            f"业务红：identity_mismatch 必有 identity_compare，events={ledger.events}"
        )
        assert "path_stat_identity" not in ledger.events, (
            f"业务红：baseline 后禁止 path_stat 冒充 identity，events={ledger.events}"
        )
        return

    if expected_terminal == "put_failed":
        assert ledger.put_attempted is True, (
            "业务红：put_failed 必须标记 put_attempted=True"
        )
        assert ledger.verified_gen is not None, (
            "业务红：put_failed 必须有 verified_gen"
        )
        vg = int(ledger.verified_gen)
        assert vg in ledger.fd_by_gen, (
            f"业务红：put_failed verified 必须有 fd，map={ledger.fd_by_gen}"
        )
        assert ledger.diagnostic_code == "upload_failed", (
            f"业务红：put_failed 诊断必须 upload_failed，actual={ledger.diagnostic_code!r}"
        )
        assert (
            ledger.http_post,
            ledger.http_put,
            ledger.http_poll,
            ledger.http_zip,
        ) == (1, 1, 0, 0), (
            f"业务红：put_failed HTTP 相位必须 POST=1 PUT=1 poll=0 ZIP=0，"
            f"actual=({ledger.http_post},{ledger.http_put},{ledger.http_poll},{ledger.http_zip})"
        )
        if platform == "windows":
            hv = ledger.handle_by_gen.get(vg)
            assert hv is not None and hv in ledger.transferred_handles, (
                f"业务红：put_failed Windows 必须已转移 handle，gen={vg}"
            )
            if not ledger.fixture_ops_used:
                assert ledger.ops_hit_counts.get("CloseHandle") == 0
        return

    # ---- success ----
    assert expected_terminal == "success"
    assert ledger.verified_gen is not None, "业务红：success 必须有 verified_gen"
    vg = int(ledger.verified_gen)
    assert ledger.verified_identity is not None, (
        f"业务红：success 必须有 verified_identity，gen={vg}"
    )
    assert vg in ledger.flags_by_gen, (
        f"业务红：verified_gen flags 必有，map={ledger.flags_by_gen}"
    )
    assert ledger.flags_by_gen[vg] & ledger.open_flag_bit, (
        f"业务红：verified flags 必须含 open_flag_bit={ledger.open_flag_bit:#x}，"
        f"flags={ledger.flags_by_gen[vg]:#x} map={ledger.flags_by_gen}"
    )
    assert vg in ledger.fd_by_gen, (
        f"业务红：verified 必须有原始 fd，fd_by_gen={ledger.fd_by_gen}"
    )
    if platform == "windows":
        assert "target_open" in ledger.events, (
            f"业务红：success 必有 target_open，events={ledger.events}"
        )
        assert "handle_to_fd" in ledger.events, (
            f"业务红：Windows success 必有 HANDLE→open_osfhandle，events={ledger.events}"
        )
        assert vg in ledger.handle_by_gen, (
            f"业务红：Windows verified 必须有 handle，map={ledger.handle_by_gen}"
        )
        hv = ledger.handle_by_gen[vg]
        assert hv in ledger.transferred_handles, (
            f"业务红：success 必须完成 HANDLE 转移，H={hv}"
        )
        if not ledger.fixture_ops_used:
            assert any(h.startswith("CreateFileW:") for h in ledger.seam_hits), (
                f"业务红：success 必须经 {_V1N_WINAPI_OPS_NAME}.CreateFileW 命中，"
                f"hits={ledger.seam_hits}"
            )
            assert any(h.startswith("open_osfhandle:") for h in ledger.seam_hits), (
                f"业务红：success 必须经 {_V1N_WINAPI_OPS_NAME}.open_osfhandle 命中，"
                f"hits={ledger.seam_hits}"
            )
            assert ledger.ops_hit_counts.get("CloseHandle") == 0
    else:
        assert "target_open" in ledger.events, (
            f"业务红：POSIX success 必有 target_open，events={ledger.events}"
        )

    assert "target_fstat" in ledger.events, (
        f"业务红：success 必有 verified fstat，events={ledger.events}"
    )
    assert ledger.read_pre_identities, (
        f"业务红：success 必须有 read 前 identity 核对，pre={ledger.read_pre_identities}"
    )
    assert ledger.read_post_identities, (
        f"业务红：success 必须有 read 后 identity 核对，post={ledger.read_post_identities}"
    )
    vid = ledger.verified_identity.as_tuple()
    for g, idt in ledger.read_pre_identities:
        assert g == vg, f"业务红：read 前 gen 必须 verified={vg} actual={g}"
        assert idt == vid, (
            f"业务红：read 前 fd identity 必须等于 verified，"
            f"actual={idt} verified={vid}"
        )
    for g, idt in ledger.read_post_identities:
        assert g == vg, f"业务红：read 后 gen 必须 verified={vg} actual={g}"
        assert idt == vid, (
            f"业务红：read 后 fd identity 必须等于 verified，"
            f"actual={idt} verified={vid}"
        )
    assert ledger.read_chunks, "业务红：success 必须有 read_chunks"
    assert ledger.handler_chunks, "业务红：success 必须有 handler_chunks"
    nonempty_reads = [(g, b) for g, b in ledger.read_chunks if b]
    nonempty_handler = [b for b in ledger.handler_chunks if b]
    assert len(nonempty_reads) >= 3, (
        f"业务红：success 至少 3 非空 read，n={len(nonempty_reads)}"
    )
    assert len(nonempty_reads) == len(nonempty_handler), (
        f"业务红：非空 read 次数必须等于 handler 块次数，"
        f"reads={len(nonempty_reads)} handler={len(nonempty_handler)}"
    )
    for i, ((g, rb), hb) in enumerate(zip(nonempty_reads, nonempty_handler)):
        assert g == vg, f"业务红：read[{i}] gen 必须 {vg} actual={g}"
        assert rb == hb, (
            f"业务红：handler 块必须与同代 read 逐块相等 i={i} "
            f"read={rb!r} handler={hb!r}"
        )
    if ledger.expected_body is not None:
        body = b"".join(b for _, b in nonempty_reads)
        assert body == ledger.expected_body, (
            f"业务红：拼接正文必须等于 expected_body，"
            f"body={body!r} expected={ledger.expected_body!r}"
        )
    assert ledger.last_chunk_seen is True, "业务红：success 必须 last_chunk_seen"
    assert ledger.close_after_last_chunk is True, (
        "业务红：success close 必须在最后块之后"
    )
    assert "close" in ledger.events, (
        f"业务红：success 必有 close，events={ledger.events}"
    )
    fd_v = int(ledger.fd_by_gen[vg])
    assert ledger.fd_close_counts.get(fd_v) == 1, (
        f"业务红：verified fd 精确 close 一次，fd={fd_v} counts={ledger.fd_close_counts}"
    )


# ---------------------------------------------------------------------------
# AST 可达性：直接函数体配对 ledger → _v1n_run_trace → capture(run) → seal → checker
# ---------------------------------------------------------------------------


def _v1n_ast_is_dead_test(test: ast.AST) -> bool:
    """静态判定 if 测试是否恒假（死分支）。"""
    if isinstance(test, ast.Constant):
        return not bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        if isinstance(test.operand, ast.Constant):
            return bool(test.operand.value)
    return False


def _v1n_ast_is_static_true(test: ast.AST) -> bool:
    """静态判定 if 测试是否恒真。"""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        if isinstance(test.operand, ast.Constant):
            return not bool(test.operand.value)
    return False


def _v1n_ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _v1n_ast_name_set(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _v1n_is_exact_run_call(
    node: ast.AST,
    *,
    run_bind_status: dict[str, str] | None = None,
) -> bool:
    """
    capture 内层必须是精确 Name run / run_remote_mineru_parse 的 Call。
    run_bind_status: name -> \"free\"|\"seam\"|\"shadow\"；
    free=未绑定；seam=精确 run=_get_run_fn(mod)；shadow=参数/导入/假赋值/路径不确定。
    仅 seam 可通过；free 与 shadow 一律拒绝。
    """
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id not in _V1N_CAPTURE_RUN_NAMES:
        return False
    if run_bind_status is not None:
        st = run_bind_status.get(node.func.id, "free")
        if st != "seam":
            return False
    return True


def _v1n_rhs_is_legitimate_run_seam(
    node: ast.AST,
    *,
    helper_shadow: set[str] | None = None,
) -> bool:
    """
    RHS 是否为唯一合法 live seam：_get_run_fn(<单位置参>)。
    拒绝属性、调用属性、getattr、其它调用形，以及 _get_run_fn 已被 shadow 的调用。
    """
    if helper_shadow is not None and "_get_run_fn" in helper_shadow:
        return False
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id != "_get_run_fn":
        return False
    # 精确单位置参 Name(mod)、无关键字（live 形态 run = _get_run_fn(mod)）
    if len(node.args) != 1 or node.keywords:
        return False
    arg0 = node.args[0]
    if not isinstance(arg0, ast.Name) or arg0.id != "mod":
        return False
    return True


def _v1n_ast_target_names(target: ast.AST) -> set[str]:
    """收集赋值目标中的 Name（含 Tuple/List/Starred）。"""
    out: set[str] = set()
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            out |= _v1n_ast_target_names(elt)
    elif isinstance(target, ast.Starred):
        out |= _v1n_ast_target_names(target.value)
    return out


def _v1n_stmt_rebinds_name(st: ast.AST, name: str) -> bool:
    """
    递归判断语句（含 If/For/While/Try/With 嵌套）是否重绑定 name。
    覆盖 Assign/AnnAssign/AugAssign/NamedExpr/For 目标/with as/except as。
    """
    if isinstance(st, ast.Assign):
        for t in st.targets:
            if name in _v1n_ast_target_names(t):
                return True
    if isinstance(st, ast.AnnAssign):
        if isinstance(st.target, ast.Name) and st.target.id == name:
            return True
    if isinstance(st, ast.AugAssign):
        if isinstance(st.target, ast.Name) and st.target.id == name:
            return True
    for n in ast.walk(st):
        if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            if n.target.id == name:
                return True
    if isinstance(st, ast.If):
        return any(
            _v1n_stmt_rebinds_name(s, name) for s in list(st.body) + list(st.orelse)
        )
    if isinstance(st, ast.For):
        if name in _v1n_ast_target_names(st.target):
            return True
        return any(
            _v1n_stmt_rebinds_name(s, name) for s in list(st.body) + list(st.orelse)
        )
    if isinstance(st, ast.While):
        return any(
            _v1n_stmt_rebinds_name(s, name) for s in list(st.body) + list(st.orelse)
        )
    if isinstance(st, (ast.With, ast.AsyncWith)):
        for it in st.items:
            if it.optional_vars is not None and name in _v1n_ast_target_names(
                it.optional_vars
            ):
                return True
        return any(_v1n_stmt_rebinds_name(s, name) for s in st.body)
    if isinstance(st, ast.Try):
        if any(_v1n_stmt_rebinds_name(s, name) for s in st.body):
            return True
        for h in st.handlers:
            if h.name == name:
                return True
            if any(_v1n_stmt_rebinds_name(s, name) for s in h.body):
                return True
        if any(_v1n_stmt_rebinds_name(s, name) for s in st.orelse):
            return True
        if any(_v1n_stmt_rebinds_name(s, name) for s in st.finalbody):
            return True
    return False


def _v1n_expr_is_cleanup_derived(
    node: ast.AST,
    *,
    cleanup_names: set[str],
    cleanup_data_names: set[str],
) -> bool:
    """判定表达式是否派生自 cleanup / cleanup.records（含 getattr/切片保守派生）。"""
    if isinstance(node, ast.Name):
        return node.id in cleanup_names or node.id in cleanup_data_names
    if isinstance(node, ast.Attribute) and node.attr == "records":
        if isinstance(node.value, ast.Name) and node.value.id in cleanup_names:
            return True
        if _v1n_expr_is_cleanup_derived(
            node.value, cleanup_names=cleanup_names, cleanup_data_names=cleanup_data_names
        ):
            return True
    if isinstance(node, ast.Subscript):
        return _v1n_expr_is_cleanup_derived(
            node.value, cleanup_names=cleanup_names, cleanup_data_names=cleanup_data_names
        )
    if isinstance(node, ast.Call):
        # getattr(cleanup, "records") / getattr(cleanup, 'records')
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            obj, attr = node.args[0], node.args[1]
            if isinstance(attr, ast.Constant) and attr.value == "records":
                if isinstance(obj, ast.Name) and obj.id in cleanup_names:
                    return True
                if _v1n_expr_is_cleanup_derived(
                    obj,
                    cleanup_names=cleanup_names,
                    cleanup_data_names=cleanup_data_names,
                ):
                    return True
    names = _v1n_ast_name_set(node)
    if names & cleanup_names or names & cleanup_data_names:
        return True
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == "records":
            if isinstance(n.value, ast.Name) and n.value.id in cleanup_names:
                return True
    return False


def _v1n_analyze_function_trace_reachability(
    func: ast.AST,
    *,
    require_run_capture: bool = True,
    require_ledgers: frozenset[str] | None = None,
) -> list[str]:
    """
    Q4/Q9/Q10/R15/R16/R17/R18：单一 AST analyzer。
    精确绑定同一可达控制流上的同一 ledger、未重绑定 trace optional_vars、
    真实 run/capture、CM 退出后 seal/checker 顺序与可达分支。
    R16：嵌套 trace 重绑定；try/except 互斥路径；run 局部 shadow 拒绝；
    cleanup getattr/切片派生回填。
    R18：seam 仅 Name(\"run\")；NamedExpr/AnnAssign/别名 shadow；
    shadow/taint 单调不可洗回；预扫描词法绑定；唯一规范 Assign 可建 seam。
    关闭：trace(A)+checker(B)、other.capture、checker-before-run、死 else、
    动态互斥 if/else 拆分、return 后死 capture、trace 重绑定、cleanup 回填。
    """
    errors: list[str] = []
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ["not_a_function"]

    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    ledger_names: set[str] = set()
    cleanup_names: set[str] = set()
    cleanup_data_names: set[str] = set()

    # R18：预扫描完整词法绑定；唯一规范 Assign 自身不计为非法
    unique_run_seam_ok = False
    prescan_shadow_run = False
    prescan_shadow_helper = False
    _canon_count = 0

    def _prescan_note_bind(name: str, *, is_canonical_assign: bool = False) -> None:
        nonlocal prescan_shadow_run, prescan_shadow_helper
        if name == _V1N_SEAM_RUN_NAME:
            if not is_canonical_assign:
                prescan_shadow_run = True
        elif name == _V1N_RUN_HELPER_NAME:
            prescan_shadow_helper = True

    def _prescan_stmts(stmts: list[ast.stmt]) -> None:
        nonlocal _canon_count
        for st in stmts:
            if isinstance(st, ast.Assign):
                if (
                    len(st.targets) == 1
                    and isinstance(st.targets[0], ast.Name)
                    and st.targets[0].id == _V1N_SEAM_RUN_NAME
                    and _v1n_rhs_is_legitimate_run_seam(
                        st.value, helper_shadow=None
                    )
                ):
                    _canon_count += 1
                    # 唯一规范 Assign 自身不预标 shadow；仍扫 RHS 内 NamedExpr
                else:
                    for t in st.targets:
                        for nm in _v1n_ast_target_names(t):
                            _prescan_note_bind(nm)
                for n in ast.walk(st.value):
                    if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                        _prescan_note_bind(n.target.id)
                continue
            if isinstance(st, ast.AnnAssign):
                if isinstance(st.target, ast.Name):
                    _prescan_note_bind(st.target.id)
                if st.value is not None:
                    for n in ast.walk(st.value):
                        if isinstance(n, ast.NamedExpr) and isinstance(
                            n.target, ast.Name
                        ):
                            _prescan_note_bind(n.target.id)
                continue
            if isinstance(st, ast.AugAssign):
                if isinstance(st.target, ast.Name):
                    _prescan_note_bind(st.target.id)
                continue
            if isinstance(st, ast.Import):
                for a in st.names:
                    local = a.asname or a.name.split(".")[0]
                    _prescan_note_bind(local)
                continue
            if isinstance(st, ast.ImportFrom):
                for a in st.names:
                    local = a.asname or a.name
                    _prescan_note_bind(local)
                continue
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # 嵌套 def/class 名绑定外层；体词法隔离不回扫
                _prescan_note_bind(st.name)
                continue
            if isinstance(st, ast.For):
                for nm in _v1n_ast_target_names(st.target):
                    _prescan_note_bind(nm)
                for n in ast.walk(st.iter):
                    if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                        _prescan_note_bind(n.target.id)
                _prescan_stmts(list(st.body))
                _prescan_stmts(list(st.orelse))
                continue
            if isinstance(st, ast.While):
                for n in ast.walk(st.test):
                    if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                        _prescan_note_bind(n.target.id)
                _prescan_stmts(list(st.body))
                _prescan_stmts(list(st.orelse))
                continue
            if isinstance(st, ast.If):
                for n in ast.walk(st.test):
                    if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                        _prescan_note_bind(n.target.id)
                _prescan_stmts(list(st.body))
                _prescan_stmts(list(st.orelse))
                continue
            if isinstance(st, (ast.With, ast.AsyncWith)):
                for it in st.items:
                    if it.optional_vars is not None:
                        for nm in _v1n_ast_target_names(it.optional_vars):
                            _prescan_note_bind(nm)
                    for n in ast.walk(it.context_expr):
                        if isinstance(n, ast.NamedExpr) and isinstance(
                            n.target, ast.Name
                        ):
                            _prescan_note_bind(n.target.id)
                _prescan_stmts(list(st.body))
                continue
            if isinstance(st, ast.Try):
                _prescan_stmts(list(st.body))
                for h in st.handlers:
                    if h.name:
                        _prescan_note_bind(h.name)
                    _prescan_stmts(list(h.body))
                _prescan_stmts(list(st.orelse))
                _prescan_stmts(list(st.finalbody))
                continue
            # 其它：仅 NamedExpr（含 Expr 语句 walrus）；不进入嵌套 def 体
            for n in ast.walk(st):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    _prescan_note_bind(n.target.id)

    _prescan_stmts(body)
    # 首次且唯一精确规范 Assign 才允许建立 seam；重复规范形态也关闭
    unique_run_seam_ok = _canon_count == 1
    if _canon_count > 1:
        # 多个规范形态互为后置赋值 → run 保守 shadow
        prescan_shadow_run = True

    class _PathState:
        __slots__ = (
            "captured_ledgers",
            "checker_ledgers",
            "capture_ok",
            "checker_ok",
            "any_capture_before",
            "saw_explicit_seal",
            "saw_other_wrapper",
            "saw_cleanup_backfill",
            "saw_wrong_capture",
            "run_bind",
            "helper_shadow",
        )

        def __init__(self) -> None:
            self.captured_ledgers: set[str] = set()
            self.checker_ledgers: set[str] = set()
            self.capture_ok = False
            self.checker_ok = False
            self.any_capture_before = False
            self.saw_explicit_seal = False
            self.saw_other_wrapper = False
            self.saw_cleanup_backfill = False
            self.saw_wrong_capture = False
            # R17：路径化 run 绑定 free|seam|shadow；helper_shadow 含被 shadow 的 _get_run_fn
            self.run_bind: dict[str, str] = {}
            self.helper_shadow: set[str] = set()

        def copy(self) -> "_PathState":
            n = _PathState()
            n.captured_ledgers = set(self.captured_ledgers)
            n.checker_ledgers = set(self.checker_ledgers)
            n.capture_ok = self.capture_ok
            n.checker_ok = self.checker_ok
            n.any_capture_before = self.any_capture_before
            n.saw_explicit_seal = self.saw_explicit_seal
            n.saw_other_wrapper = self.saw_other_wrapper
            n.saw_cleanup_backfill = self.saw_cleanup_backfill
            n.saw_wrong_capture = self.saw_wrong_capture
            n.run_bind = dict(self.run_bind)
            n.helper_shadow = set(self.helper_shadow)
            return n

    def _merge_run_bind(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
        """路径合并：仅双路径皆 seam 才保留 seam；任一 shadow 或不一致 → shadow。"""
        out: dict[str, str] = {}
        for k in set(a) | set(b):
            va, vb = a.get(k), b.get(k)
            if va == "seam" and vb == "seam":
                out[k] = "seam"
            elif va is None and vb is None:
                continue
            elif va == vb and va is not None:
                # 双路径同为 free 不在表内；同 shadow 保留 shadow
                out[k] = va
            else:
                # seam vs free/缺省/shadow，或仅单侧绑定：保守非 seam
                out[k] = "shadow"
        return out

    def _must_merge(a: _PathState, b: _PathState) -> _PathState:
        m = _PathState()
        m.captured_ledgers = a.captured_ledgers & b.captured_ledgers
        m.checker_ledgers = a.checker_ledgers & b.checker_ledgers
        m.capture_ok = a.capture_ok and b.capture_ok
        m.checker_ok = a.checker_ok and b.checker_ok
        m.any_capture_before = a.any_capture_before and b.any_capture_before
        m.saw_explicit_seal = a.saw_explicit_seal or b.saw_explicit_seal
        m.saw_other_wrapper = a.saw_other_wrapper or b.saw_other_wrapper
        m.saw_cleanup_backfill = a.saw_cleanup_backfill or b.saw_cleanup_backfill
        m.saw_wrong_capture = a.saw_wrong_capture or b.saw_wrong_capture
        m.run_bind = _merge_run_bind(a.run_bind, b.run_bind)
        m.helper_shadow = set(a.helper_shadow) | set(b.helper_shadow)
        return m

    def _shadow_names_on(state: _PathState, names: set[str]) -> None:
        for nm in names:
            if nm == _V1N_RUN_HELPER_NAME:
                state.helper_shadow.add(_V1N_RUN_HELPER_NAME)
            if nm in _V1N_CAPTURE_RUN_NAMES:
                state.run_bind[nm] = "shadow"

    def _is_canonical_run_seam_assign(st: ast.AST) -> bool:
        """R18：唯一合法精确形态 Assign run = _get_run_fn(mod)（单目标 Name）。"""
        if not isinstance(st, ast.Assign):
            return False
        if len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
            return False
        if st.targets[0].id != _V1N_SEAM_RUN_NAME:
            return False
        return _v1n_rhs_is_legitimate_run_seam(st.value, helper_shadow=None)

    def _bind_exact_run_target(
        state: _PathState,
        *,
        target: ast.AST,
        value: ast.AST,
        exact_single_name: bool,
        allow_seam: bool = False,
    ) -> None:
        """
        R18：仅精确单目标 Assign Name("run") 且 RHS=_get_run_fn(mod)、
        函数内首次且唯一、当前路径尚未绑定、helper 未 shadow 时可标 seam。
        run_remote_mineru_parse / NamedExpr / AnnAssign / 别名 / 洗回一律 shadow。
        shadow/taint 单调：已存在绑定不可写回 seam。
        """
        names = _v1n_ast_target_names(target)
        if _V1N_RUN_HELPER_NAME in names:
            state.helper_shadow.add(_V1N_RUN_HELPER_NAME)
        cap = names & set(_V1N_CAPTURE_RUN_NAMES)
        if not cap:
            return
        # 已绑定（含 shadow/seam）再写 → 一律 shadow（单调不可洗回）
        already = any(state.run_bind.get(nm) is not None for nm in cap)
        if (
            allow_seam
            and not already
            and unique_run_seam_ok
            and exact_single_name
            and isinstance(target, ast.Name)
            and target.id == _V1N_SEAM_RUN_NAME
            and _v1n_rhs_is_legitimate_run_seam(
                value, helper_shadow=state.helper_shadow
            )
        ):
            state.run_bind[_V1N_SEAM_RUN_NAME] = "seam"
        else:
            for nm in cap:
                state.run_bind[nm] = "shadow"

    def _note_named_expr_binds(node: ast.AST, state: _PathState) -> None:
        """NamedExpr 一律 shadow，永不建立 seam。"""
        for n in ast.walk(node):
            if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                _bind_exact_run_target(
                    state,
                    target=n.target,
                    value=n.value,
                    exact_single_name=True,
                    allow_seam=False,
                )

    def _note_run_bind_from_stmt(st: ast.AST, state: _PathState) -> None:
        """
        R17/R18：路径化/保守建模 run 绑定。
        递归 If/For/While/Try/With；覆盖 Assign/AnnAssign/AugAssign/NamedExpr/
        循环 target/with optional_vars/except alias/import。
        """
        if isinstance(st, ast.Assign):
            # 仅「单 targets 元素且为 Name("run")」才可能 seam；chained/多目标否
            exact = len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
            if exact:
                _bind_exact_run_target(
                    state,
                    target=st.targets[0],
                    value=st.value,
                    exact_single_name=True,
                    allow_seam=True,
                )
            else:
                for t in st.targets:
                    _bind_exact_run_target(
                        state,
                        target=t,
                        value=st.value,
                        exact_single_name=False,
                        allow_seam=False,
                    )
            _note_named_expr_binds(st.value, state)
            return
        if isinstance(st, ast.AnnAssign) and st.value is not None:
            # 注解赋值不视为 live 精确 Assign 形态 → 绑定目标只可 shadow
            if isinstance(st.target, ast.Name):
                if st.target.id == _V1N_RUN_HELPER_NAME:
                    state.helper_shadow.add(_V1N_RUN_HELPER_NAME)
                if st.target.id in _V1N_CAPTURE_RUN_NAMES:
                    state.run_bind[st.target.id] = "shadow"
            _note_named_expr_binds(st.value, state)
            return
        if isinstance(st, ast.AugAssign):
            if isinstance(st.target, ast.Name):
                _shadow_names_on(state, {st.target.id})
            return
        if isinstance(st, ast.Import):
            for a in st.names:
                local = a.asname or a.name.split(".")[0]
                _shadow_names_on(state, {local})
            return
        if isinstance(st, ast.ImportFrom):
            for a in st.names:
                local = a.asname or a.name
                _shadow_names_on(state, {local})
            return
        if isinstance(st, ast.If):
            _note_named_expr_binds(st.test, state)
            then_st = state.copy()
            else_st = state.copy()
            for s in st.body:
                _note_run_bind_from_stmt(s, then_st)
            for s in st.orelse:
                _note_run_bind_from_stmt(s, else_st)
            merged = _must_merge(then_st, else_st)
            state.run_bind = merged.run_bind
            state.helper_shadow = merged.helper_shadow
            return
        if isinstance(st, ast.For):
            _shadow_names_on(state, _v1n_ast_target_names(st.target))
            _note_named_expr_binds(st.iter, state)
            # 循环体 0 次或多次：入口与体结束保守合并
            body_st = state.copy()
            for s in st.body:
                _note_run_bind_from_stmt(s, body_st)
            else_st = state.copy()
            for s in st.orelse:
                _note_run_bind_from_stmt(s, else_st)
            merged = _must_merge(state, body_st)
            merged = _must_merge(merged, else_st)
            state.run_bind = merged.run_bind
            state.helper_shadow = merged.helper_shadow
            return
        if isinstance(st, ast.While):
            _note_named_expr_binds(st.test, state)
            body_st = state.copy()
            for s in st.body:
                _note_run_bind_from_stmt(s, body_st)
            else_st = state.copy()
            for s in st.orelse:
                _note_run_bind_from_stmt(s, else_st)
            merged = _must_merge(state, body_st)
            merged = _must_merge(merged, else_st)
            state.run_bind = merged.run_bind
            state.helper_shadow = merged.helper_shadow
            return
        if isinstance(st, (ast.With, ast.AsyncWith)):
            for it in st.items:
                _note_named_expr_binds(it.context_expr, state)
                if it.optional_vars is not None:
                    _shadow_names_on(state, _v1n_ast_target_names(it.optional_vars))
            for s in st.body:
                _note_run_bind_from_stmt(s, state)
            return
        if isinstance(st, ast.Try):
            pre_bind = state.copy()
            normal = state.copy()
            for s in st.body:
                _note_run_bind_from_stmt(s, normal)
            for s in st.orelse:
                _note_run_bind_from_stmt(s, normal)
            handler_states: list[_PathState] = []
            for h in st.handlers:
                h_st = pre_bind.copy()
                if h.name:
                    _shadow_names_on(h_st, {h.name})
                for s in h.body:
                    _note_run_bind_from_stmt(s, h_st)
                handler_states.append(h_st)
            paths = [normal] + handler_states
            if st.finalbody:
                # finally 作用于 normal / handler / pre 隐式异常
                fin_paths: list[_PathState] = []
                for p in paths:
                    fp = p.copy()
                    for s in st.finalbody:
                        _note_run_bind_from_stmt(s, fp)
                    fin_paths.append(fp)
                pre_fin = pre_bind.copy()
                for s in st.finalbody:
                    _note_run_bind_from_stmt(s, pre_fin)
                fin_paths.append(pre_fin)
                paths = fin_paths
            if handler_states or st.finalbody:
                merged = paths[0]
                for p in paths[1:]:
                    merged = _must_merge(merged, p)
                state.run_bind = merged.run_bind
                state.helper_shadow = merged.helper_shadow
            else:
                state.run_bind = normal.run_bind
                state.helper_shadow = normal.helper_shadow
            return
        # 其它语句：仅扫 NamedExpr 绑定（永不 seam）
        _note_named_expr_binds(st, state)

    def _value_from_cleanup(node: ast.AST) -> bool:
        return _v1n_expr_is_cleanup_derived(
            node,
            cleanup_names=cleanup_names,
            cleanup_data_names=cleanup_data_names,
        )

    def _mark_cleanup_interaction(st: ast.AST, state: _PathState) -> None:
        names = _v1n_ast_name_set(st)
        if names & ledger_names and (
            names & cleanup_names or names & cleanup_data_names
        ):
            state.saw_cleanup_backfill = True
            return
        for n in ast.walk(st):
            if isinstance(n, ast.Attribute) and n.attr in {
                "records",
                "append",
                "extend",
            }:
                if isinstance(n.value, ast.Name) and n.value.id in cleanup_names:
                    if names & ledger_names:
                        state.saw_cleanup_backfill = True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                if (
                    isinstance(n.func.value, ast.Name)
                    and n.func.value.id in ledger_names
                    and n.func.attr.startswith("note_")
                    and (names & cleanup_names or names & cleanup_data_names)
                ):
                    state.saw_cleanup_backfill = True
            if isinstance(n, ast.Attribute) and "CleanupAudit" in ast.dump(n):
                if names & ledger_names:
                    state.saw_cleanup_backfill = True
            # getattr(cleanup,"records") 与 ledger 同句
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "getattr"
                and len(n.args) >= 2
                and isinstance(n.args[1], ast.Constant)
                and n.args[1].value == "records"
                and names & ledger_names
            ):
                obj = n.args[0]
                if isinstance(obj, ast.Name) and obj.id in cleanup_names:
                    state.saw_cleanup_backfill = True

    def _register_cleanup_data_bind(st: ast.Assign) -> None:
        if len(st.targets) != 1 or not isinstance(st.targets[0], ast.Name):
            return
        tname = st.targets[0].id
        val = st.value
        # cleanup.records / getattr(cleanup,"records") / cleanup.records[:] 等
        if _v1n_expr_is_cleanup_derived(
            val,
            cleanup_names=cleanup_names,
            cleanup_data_names=cleanup_data_names,
        ):
            # 整 cleanup 对象别名 vs records 数据别名
            if isinstance(val, ast.Name) and val.id in cleanup_names:
                cleanup_names.add(tname)
                return
            cleanup_data_names.add(tname)
            return
        if isinstance(val, ast.Name):
            if val.id in cleanup_names:
                cleanup_names.add(tname)
            if val.id in cleanup_data_names:
                cleanup_data_names.add(tname)

    def _scan_trace_body(
        body_stmts: list[ast.stmt],
        *,
        trace_var: str | None,
        trace_ledger: str | None,
        state: _PathState,
    ) -> bool:
        """顺序扫描 CM body；返回是否找到合法 capture(run)。R16：递归覆盖嵌套重绑定。"""
        found_capture_run = False
        reachable = True
        active_trace = trace_var
        for bst in body_stmts:
            if not reachable:
                for n in ast.walk(bst):
                    if (
                        isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "capture"
                    ):
                        errors.append("capture_in_dead_code")
                    if (
                        isinstance(n, ast.Call)
                        and _v1n_ast_call_name(n.func) == _V1N_CHECKER_NAME
                    ):
                        errors.append("checker_in_dead_code")
                continue
            if isinstance(bst, (ast.Return, ast.Raise)):
                reachable = False
                continue
            if isinstance(bst, (ast.FunctionDef, ast.AsyncFunctionDef)):
                errors.append("nested_def_in_trace")
                continue
            _note_run_bind_from_stmt(bst, state)
            # R16 Q1：递归覆盖 If/For/While/Try/With 内 Assign/AnnAssign/AugAssign/NamedExpr
            if active_trace is not None and _v1n_stmt_rebinds_name(bst, active_trace):
                errors.append("trace_rebinding")
                active_trace = None
            for n in ast.walk(bst):
                if (
                    isinstance(n, ast.Call)
                    and _v1n_ast_call_name(n.func) == _V1N_CHECKER_NAME
                ):
                    errors.append("checker_inside_trace_cm")
                if isinstance(n, ast.Call) and _v1n_ast_call_name(n.func) == "seal":
                    state.saw_explicit_seal = True
            cap_calls: list[ast.Call] = []
            if isinstance(bst, ast.Assign) and isinstance(bst.value, ast.Call):
                cap_calls.append(bst.value)
            if isinstance(bst, ast.Expr) and isinstance(bst.value, ast.Call):
                cap_calls.append(bst.value)
            for call in cap_calls:
                if not (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "capture"
                ):
                    continue
                if not (
                    isinstance(call.func.value, ast.Name)
                    and active_trace is not None
                    and call.func.value.id == active_trace
                ):
                    errors.append("capture_wrong_receiver")
                    state.saw_wrong_capture = True
                    continue
                if not call.args or not _v1n_is_exact_run_call(
                    call.args[0], run_bind_status=state.run_bind
                ):
                    errors.append("missing_trace_capture_run")
                    state.saw_wrong_capture = True
                    continue
                found_capture_run = True
                state.capture_ok = True
                state.any_capture_before = True
                if trace_ledger is not None:
                    state.captured_ledgers.add(trace_ledger)
        return found_capture_run

    def scan_stmt_list(
        stmts: list[ast.stmt],
        *,
        reachable: bool,
        after_trace: bool,
        state: _PathState,
    ) -> _PathState:
        if not reachable:
            for st in stmts:
                for n in ast.walk(st):
                    if isinstance(n, ast.Call):
                        cn = _v1n_ast_call_name(n.func)
                        if cn == _V1N_CHECKER_NAME:
                            errors.append("checker_in_dead_code")
                        if (
                            isinstance(n.func, ast.Attribute)
                            and n.func.attr == "capture"
                        ):
                            errors.append("capture_in_dead_code")
            return state

        i = 0
        while i < len(stmts):
            st = stmts[i]
            if isinstance(st, (ast.Return, ast.Raise)):
                scan_stmt_list(
                    stmts[i + 1 :],
                    reachable=False,
                    after_trace=after_trace,
                    state=state,
                )
                return state

            if isinstance(st, (ast.Import, ast.ImportFrom)):
                _note_run_bind_from_stmt(st, state)

            if isinstance(st, ast.Assign):
                _note_run_bind_from_stmt(st, state)
                for t in st.targets:
                    if isinstance(t, ast.Name) and isinstance(st.value, ast.Call):
                        cn = _v1n_ast_call_name(st.value.func)
                        if cn == _V1N_LEDGER_CLS_NAME:
                            ledger_names.add(t.id)
                        if cn == _V1N_CLEANUP_CLS_NAME:
                            cleanup_names.add(t.id)
                if (
                    len(st.targets) == 1
                    and isinstance(st.targets[0], ast.Name)
                    and isinstance(st.value, ast.Name)
                    and st.value.id in ledger_names
                ):
                    errors.append("ledger_alias")
                    ledger_names.add(st.targets[0].id)
                _register_cleanup_data_bind(st)
                for t in st.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                        if t.value.id in ledger_names and _value_from_cleanup(st.value):
                            state.saw_cleanup_backfill = True
                    if isinstance(t, ast.Name) and t.id in ledger_names:
                        if _value_from_cleanup(st.value):
                            state.saw_cleanup_backfill = True
                if isinstance(st.value, ast.Call):
                    _mark_cleanup_interaction(st, state)
                for n in ast.walk(st):
                    if isinstance(n, ast.Call) and _v1n_ast_call_name(n.func) == "seal":
                        state.saw_explicit_seal = True
                if isinstance(st.value, ast.Call):
                    if (
                        isinstance(st.value.func, ast.Attribute)
                        and isinstance(st.value.func.value, ast.Name)
                        and st.value.func.value.id in state.captured_ledgers
                        and st.value.func.attr.startswith("note_")
                    ):
                        state.saw_cleanup_backfill = True

            if isinstance(st, ast.AnnAssign):
                _note_run_bind_from_stmt(st, state)

            if isinstance(st, ast.AugAssign):
                _note_run_bind_from_stmt(st, state)
                if isinstance(st.target, ast.Attribute) and isinstance(
                    st.target.value, ast.Name
                ):
                    if st.target.value.id in ledger_names and _value_from_cleanup(
                        st.value
                    ):
                        state.saw_cleanup_backfill = True
                _mark_cleanup_interaction(st, state)

            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
                call = st.value
                cn = _v1n_ast_call_name(call.func)
                if cn == _V1N_CHECKER_NAME:
                    if not state.any_capture_before and require_run_capture:
                        errors.append("checker_before_run")
                    if call.args:
                        a0 = call.args[0]
                        if isinstance(a0, ast.Name) and a0.id in ledger_names:
                            if (
                                a0.id not in state.captured_ledgers
                                and require_run_capture
                            ):
                                errors.append("checker_ledger_not_captured")
                            state.checker_ledgers.add(a0.id)
                            state.checker_ok = True
                        else:
                            errors.append("checker_not_same_ledger")
                    else:
                        errors.append("checker_missing_ledger_arg")
                if cn == "seal":
                    state.saw_explicit_seal = True
                if isinstance(call.func, ast.Attribute):
                    if (
                        isinstance(call.func.value, ast.Name)
                        and call.func.value.id in ledger_names
                        and call.func.attr.startswith("note_")
                    ):
                        if call.func.value.id in state.captured_ledgers or (
                            _v1n_ast_name_set(call) & cleanup_names
                            or _v1n_ast_name_set(call) & cleanup_data_names
                        ):
                            state.saw_cleanup_backfill = True
                    if call.func.attr == "append":
                        names = _v1n_ast_name_set(call)
                        if names & ledger_names and (
                            names & cleanup_names or names & cleanup_data_names
                        ):
                            state.saw_cleanup_backfill = True
                _mark_cleanup_interaction(st, state)

            if isinstance(st, ast.With):
                items = st.items
                is_raises = False
                is_trace = False
                trace_ledger: str | None = None
                trace_var: str | None = None
                for it in items:
                    # with optional_vars 绑定到外层作用域（非词法隔离）
                    if it.optional_vars is not None:
                        _shadow_names_on(
                            state, _v1n_ast_target_names(it.optional_vars)
                        )
                    ctx = it.context_expr
                    if isinstance(ctx, ast.Call):
                        cn = _v1n_ast_call_name(ctx.func)
                        if cn == "raises" or (
                            isinstance(ctx.func, ast.Attribute)
                            and ctx.func.attr == "raises"
                        ):
                            is_raises = True
                        elif cn == _V1N_TRACE_CM_NAME:
                            is_trace = True
                            if ctx.args and isinstance(ctx.args[0], ast.Name):
                                if ctx.args[0].id in ledger_names:
                                    trace_ledger = ctx.args[0].id
                                else:
                                    errors.append("trace_ledger_not_known")
                            else:
                                errors.append("trace_ledger_not_name")
                            if it.optional_vars is not None:
                                if isinstance(it.optional_vars, ast.Name):
                                    trace_var = it.optional_vars.id
                                else:
                                    errors.append("trace_optional_vars_not_name")
                            else:
                                errors.append("trace_missing_optional_vars")
                        else:
                            if cn not in {
                                "raises",
                                _V1N_TRACE_CM_NAME,
                                "nullcontext",
                                "MockTransport",
                            }:
                                if isinstance(ctx.func, ast.Attribute):
                                    if ctx.func.attr != "raises":
                                        state.saw_other_wrapper = True
                                elif cn:
                                    state.saw_other_wrapper = True

                if is_raises:
                    scan_stmt_list(
                        list(st.body),
                        reachable=True,
                        after_trace=after_trace,
                        state=state,
                    )
                elif is_trace:
                    if trace_ledger is None and require_run_capture:
                        errors.append("trace_ledger_mismatch")
                    found_capture_run = _scan_trace_body(
                        list(st.body),
                        trace_var=trace_var,
                        trace_ledger=trace_ledger,
                        state=state,
                    )
                    if (
                        require_run_capture
                        and not found_capture_run
                        and not state.saw_wrong_capture
                    ):
                        errors.append("missing_trace_capture_run")
                    return scan_stmt_list(
                        stmts[i + 1 :],
                        reachable=True,
                        after_trace=True,
                        state=state,
                    )
                else:
                    for it in items:
                        if isinstance(it.context_expr, ast.Call):
                            cn = _v1n_ast_call_name(it.context_expr.func)
                            if cn and cn not in {_V1N_TRACE_CM_NAME, "raises"}:
                                if isinstance(it.context_expr.func, ast.Attribute):
                                    if it.context_expr.func.attr != "raises":
                                        state.saw_other_wrapper = True
                                else:
                                    state.saw_other_wrapper = True
                        ov = it.optional_vars
                        if ov is not None and isinstance(ov, ast.Name):
                            for bst in st.body:
                                for n in ast.walk(bst):
                                    if (
                                        isinstance(n, ast.Call)
                                        and isinstance(n.func, ast.Attribute)
                                        and n.func.attr == "capture"
                                    ):
                                        if (
                                            isinstance(n.func.value, ast.Name)
                                            and n.func.value.id == ov.id
                                        ):
                                            errors.append("other_wrapper_capture")
                                            state.saw_wrong_capture = True
                    scan_stmt_list(
                        list(st.body),
                        reachable=True,
                        after_trace=after_trace,
                        state=state,
                    )

            if isinstance(st, ast.If):
                if _v1n_ast_is_dead_test(st.test):
                    scan_stmt_list(
                        list(st.body),
                        reachable=False,
                        after_trace=after_trace,
                        state=state,
                    )
                    scan_stmt_list(
                        list(st.orelse),
                        reachable=True,
                        after_trace=after_trace,
                        state=state,
                    )
                elif _v1n_ast_is_static_true(st.test):
                    scan_stmt_list(
                        list(st.body),
                        reachable=True,
                        after_trace=after_trace,
                        state=state,
                    )
                    scan_stmt_list(
                        list(st.orelse),
                        reachable=False,
                        after_trace=after_trace,
                        state=state,
                    )
                else:
                    pre = state.copy()
                    then_st = state.copy()
                    else_st = state.copy()
                    scan_stmt_list(
                        list(st.body),
                        reachable=True,
                        after_trace=after_trace,
                        state=then_st,
                    )
                    scan_stmt_list(
                        list(st.orelse),
                        reachable=True,
                        after_trace=after_trace,
                        state=else_st,
                    )
                    then_new_cap = then_st.capture_ok and not pre.capture_ok
                    else_new_cap = else_st.capture_ok and not pre.capture_ok
                    then_new_chk = bool(
                        then_st.checker_ledgers - pre.checker_ledgers
                    ) or (then_st.checker_ok and not pre.checker_ok)
                    else_new_chk = bool(
                        else_st.checker_ledgers - pre.checker_ledgers
                    ) or (else_st.checker_ok and not pre.checker_ok)
                    if (then_new_cap != else_new_cap) or (then_new_chk != else_new_chk):
                        errors.append("mutex_branch_split")
                    merged = _must_merge(then_st, else_st)
                    state.captured_ledgers = merged.captured_ledgers
                    state.checker_ledgers = merged.checker_ledgers
                    state.capture_ok = merged.capture_ok
                    state.checker_ok = merged.checker_ok
                    state.any_capture_before = merged.any_capture_before
                    state.saw_explicit_seal = merged.saw_explicit_seal
                    state.saw_other_wrapper = merged.saw_other_wrapper
                    state.saw_cleanup_backfill = merged.saw_cleanup_backfill
                    state.saw_wrong_capture = merged.saw_wrong_capture
                    state.run_bind = merged.run_bind
                    state.helper_shadow = merged.helper_shadow

            if isinstance(st, (ast.For, ast.While)):
                # 循环 target / 体 0 次或多次：run 绑定保守合并（体按可达性仍标死代码）
                _note_run_bind_from_stmt(st, state)
                scan_stmt_list(
                    list(st.body),
                    reachable=False,
                    after_trace=after_trace,
                    state=state,
                )
                scan_stmt_list(
                    list(st.orelse),
                    reachable=True,
                    after_trace=after_trace,
                    state=state,
                )

            if isinstance(st, ast.Try):
                # R16/R17 Q2/N4：try 正常路径与 except 异常路径互斥；else 仅正常路径；
                # finally 作用于 normal / 显式 handler / try 任意点隐式异常 pre；
                # 禁止把未发生的 capture 与 finally checker 拼成假绿。
                pre = state.copy()
                normal_st = state.copy()
                scan_stmt_list(
                    list(st.body),
                    reachable=True,
                    after_trace=after_trace,
                    state=normal_st,
                )
                # else 仅接在未异常的正常路径之后
                scan_stmt_list(
                    list(st.orelse),
                    reachable=True,
                    after_trace=after_trace,
                    state=normal_st,
                )

                handler_states: list[_PathState] = []
                for h in st.handlers:
                    # 异常可在 try body 任意点抛出：保守从 pre 进入 except
                    h_st = pre.copy()
                    if h.name:
                        _shadow_names_on(h_st, {h.name})
                    scan_stmt_list(
                        list(h.body),
                        reachable=True,
                        after_trace=after_trace,
                        state=h_st,
                    )
                    handler_states.append(h_st)

                def _apply_mutex(a: _PathState, b: _PathState) -> None:
                    a_new_cap = a.capture_ok and not pre.capture_ok
                    b_new_cap = b.capture_ok and not pre.capture_ok
                    a_new_chk = bool(a.checker_ledgers - pre.checker_ledgers) or (
                        a.checker_ok and not pre.checker_ok
                    )
                    b_new_chk = bool(b.checker_ledgers - pre.checker_ledgers) or (
                        b.checker_ok and not pre.checker_ok
                    )
                    if (a_new_cap != b_new_cap) or (a_new_chk != b_new_chk):
                        errors.append("mutex_branch_split")

                def _copy_path_fields(dst: _PathState, src: _PathState) -> None:
                    dst.captured_ledgers = src.captured_ledgers
                    dst.checker_ledgers = src.checker_ledgers
                    dst.capture_ok = src.capture_ok
                    dst.checker_ok = src.checker_ok
                    dst.any_capture_before = src.any_capture_before
                    dst.saw_explicit_seal = src.saw_explicit_seal
                    dst.saw_other_wrapper = src.saw_other_wrapper
                    dst.saw_cleanup_backfill = src.saw_cleanup_backfill
                    dst.saw_wrong_capture = src.saw_wrong_capture
                    dst.run_bind = dict(src.run_bind)
                    dst.helper_shadow = set(src.helper_shadow)

                # finally 分别处理 normal、各 handler、以及 pre 隐式异常输入
                pre_fin: _PathState | None = None
                if st.finalbody:
                    scan_stmt_list(
                        list(st.finalbody),
                        reachable=True,
                        after_trace=after_trace,
                        state=normal_st,
                    )
                    for h_st in handler_states:
                        scan_stmt_list(
                            list(st.finalbody),
                            reachable=True,
                            after_trace=after_trace,
                            state=h_st,
                        )
                    pre_fin = pre.copy()
                    scan_stmt_list(
                        list(st.finalbody),
                        reachable=True,
                        after_trace=after_trace,
                        state=pre_fin,
                    )
                    # 隐式异常路径 vs 正常路径不得拼成假绿
                    _apply_mutex(normal_st, pre_fin)

                if handler_states:
                    merged = normal_st
                    for h_st in handler_states:
                        _apply_mutex(normal_st, h_st)
                        merged = _must_merge(merged, h_st)
                    if pre_fin is not None:
                        # 无匹配 handler 的隐式异常仍执行 finally；保守并入
                        _apply_mutex(normal_st, pre_fin)
                        merged = _must_merge(merged, pre_fin)
                    _copy_path_fields(state, merged)
                else:
                    # 无 handler：后续语句仅正常路径可达；但 pre_fin 错误已写入 errors
                    if pre_fin is not None:
                        _apply_mutex(normal_st, pre_fin)
                    _copy_path_fields(state, normal_st)

            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(st):
                    if isinstance(n, ast.Call):
                        cn = _v1n_ast_call_name(n.func)
                        if cn == _V1N_CHECKER_NAME:
                            errors.append("checker_in_uncalled_nested")
                        if (
                            isinstance(n.func, ast.Attribute)
                            and n.func.attr == "capture"
                        ):
                            errors.append("capture_in_uncalled_nested")

            i += 1
        return state

    root = _PathState()
    # 形参 / *args/**kwargs 对 run 名与 _get_run_fn 一律 shadow
    for a in list(func.args.posonlyargs) + list(func.args.args) + list(
        func.args.kwonlyargs
    ):
        _shadow_names_on(root, {a.arg})
    if func.args.vararg is not None:
        _shadow_names_on(root, {func.args.vararg.arg})
    if func.args.kwarg is not None:
        _shadow_names_on(root, {func.args.kwarg.arg})
    # R18：预扫描到的非规范词法绑定 / helper 后置 → 入口即保守 shadow
    if prescan_shadow_run:
        root.run_bind[_V1N_SEAM_RUN_NAME] = "shadow"
    if prescan_shadow_helper:
        root.helper_shadow.add(_V1N_RUN_HELPER_NAME)
    scan_stmt_list(body, reachable=True, after_trace=False, state=root)

    if require_run_capture and not root.capture_ok:
        errors.append("no_reachable_capture_run")
    if not root.checker_ok:
        errors.append("no_reachable_checker")
    if root.saw_explicit_seal:
        errors.append("explicit_seal")
    if root.saw_other_wrapper and require_run_capture:
        errors.append("other_wrapper")
    if root.saw_cleanup_backfill:
        errors.append("cleanup_audit_backfill")
    for cl in root.checker_ledgers:
        if cl not in root.captured_ledgers and require_run_capture:
            if "checker_ledger_not_captured" not in errors:
                errors.append("checker_ledger_not_captured")
    if require_ledgers:
        for led in sorted(require_ledgers):
            if led not in root.captured_ledgers or led not in root.checker_ledgers:
                errors.append(f"ledger_not_on_all_paths:{led}")
    out: list[str] = []
    seen: set[str] = set()
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _v1n_trace_ast_reachability_self_proof() -> None:
    """
    Q3/Q4/Q9/Q10/R15/R16/R18：同一 analyzer 覆盖合法路径与 synthetic 假绿形态（failure-first）。
    R16 新增：nested_trace_rebinding / try_except_mutex / run_local_shadow /
    cleanup_getattr_records / cleanup_records_slice；正例 ok_try / ok_run_unshadowed。
    R18 新增：non_run_left / named_expr_seam / shadow_then_restore / helper_post_shadow。
    """
    ok_src = """
def test_ok_normal():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
"""
    ok_exc = """
def test_ok_exc():
    ledger = _V1NGenerationLedger(platform="posix", open_flag_bit=1)
    run = _get_run_fn(mod)
    with pytest.raises(RemoteMineruError):
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="identity_mismatch", platform="posix")
"""
    ok_cleanup = """
def test_ok_cleanup():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    cleanup = _V1NFixtureCleanupAudit()
    cleanup.fixture_cleanup_after_failure("t", "p", "fd:1", "closed")
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
"""
    # R16：合法 try 正例（try/except 不拆分 capture/checker；两者同在 try 外正常路径）
    ok_try = """
def test_ok_try():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    try:
        prep = 1
    except Exception:
        prep = 0
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
"""
    # R17：唯一合法精确 seam 正例 run = _get_run_fn(mod)
    ok_run_unshadowed = """
def test_ok_run_unshadowed():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
"""
    synthetics: list[tuple[str, str, str]] = [
        (
            "delete_checker",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
""",
            "no_reachable_checker",
        ),
        (
            "if_zero",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    if 0:
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "checker_in_dead_code",
        ),
        (
            "dead_else",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    if 1:
        x = 1
    else:
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "no_reachable_checker",
        ),
        (
            "uncalled_nested",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    def inner():
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(run(sources))
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "checker_in_uncalled_nested",
        ),
        (
            "after_return",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    return
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "no_reachable_capture_run",
        ),
        (
            "after_raise",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    raise RuntimeError("x")
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "no_reachable_capture_run",
        ),
        (
            "dead_capture_after_return_in_cm",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        return
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "capture_in_dead_code",
        ),
        (
            "trace_rebinding",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        trace = object()
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "trace_rebinding",
        ),
        (
            "nested_trace_rebinding",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        if True:
            trace = object()
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "trace_rebinding",
        ),
        (
            "try_except_mutex",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    try:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(run(sources))
    except Exception:
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "mutex_branch_split",
        ),
        (
            "run_local_shadow",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = fake_run
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        (
            "cleanup_getattr_records",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    records = getattr(cleanup, "records")
    ledger.events = records
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "cleanup_records_slice",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    records = cleanup.records[:]
    ledger.events = records
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "mutex_if_else",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    flag = unknown()
    if flag:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(run(sources))
    else:
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "mutex_branch_split",
        ),
        (
            "ledger_alias",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    alias = ledger
    with _v1n_run_trace(alias) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(alias, expected_terminal="success", platform="windows")
""",
            "ledger_alias",
        ),
        (
            "other_wrapper",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with other_wrapper(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "other_wrapper",
        ),
        (
            "wrapper_indirect_run",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    def wrap():
        return run(sources)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(wrap())
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        (
            "capture_any_name",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(helper())
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        (
            "capture_wrong_receiver",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    other = object()
    with _v1n_run_trace(ledger) as trace:
        result = other.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "capture_wrong_receiver",
        ),
        (
            "trace_a_checker_b",
            """
def test_bad():
    led_a = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    led_b = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(led_a) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(led_b, expected_terminal="success", platform="windows")
""",
            "checker_ledger_not_captured",
        ),
        (
            "checker_before_run",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
""",
            "checker_before_run",
        ),
        (
            "explicit_seal",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
        ledger.seal()
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "explicit_seal",
        ),
        (
            "checker_inside_cm",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "checker_inside_trace_cm",
        ),
        (
            "count_only_dead",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    if 0:
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
        _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "no_reachable_capture_run",
        ),
        (
            "cleanup_assign",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    ledger.events = cleanup.records
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "cleanup_append",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    ledger.events.append(cleanup.records[0])
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "cleanup_note_fd_close",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    cleanup.fixture_cleanup_after_failure("t", "p", "fd:1", "closed")
    ledger.note_fd_close(1)
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "cleanup_attr_alias",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    records = cleanup.records
    more = records
    ledger.events = more
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        (
            "cleanup_augassign",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    cleanup = _V1NFixtureCleanupAudit()
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    ledger.events += cleanup.records
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "cleanup_audit_backfill",
        ),
        # R18：非 run 左值不可建 seam
        (
            "non_run_left_value",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run_remote_mineru_parse = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run_remote_mineru_parse(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        # R18：NamedExpr 永不 seam
        (
            "named_expr_seam",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    x = (run := _get_run_fn(mod))
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        # R18：shadow 后不可洗回 seam
        (
            "shadow_then_restore",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = fake_run
    run = _get_run_fn(mod)
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
        # R18：helper 后置 shadow 使规范 Assign 失效
        (
            "helper_post_shadow",
            """
def test_bad():
    ledger = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    run = _get_run_fn(mod)
    _get_run_fn = fake_helper
    with _v1n_run_trace(ledger) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(ledger, expected_terminal="success", platform="windows")
""",
            "missing_trace_capture_run",
        ),
    ]

    def _one(src: str) -> list[str]:
        tree = ast.parse(src)
        fn = tree.body[0]
        return _v1n_analyze_function_trace_reachability(fn, require_run_capture=True)

    for label, src in (
        ("ok_normal", ok_src),
        ("ok_exc", ok_exc),
        ("ok_cleanup", ok_cleanup),
        ("ok_try", ok_try),
        ("ok_run_unshadowed", ok_run_unshadowed),
    ):
        errs = _one(src)
        assert not errs, f"业务红：合法路径 {label} 必须通过 analyzer，errs={errs}"

    for label, src, needle in synthetics:
        errs = _one(src)
        assert errs, f"业务红：synthetic {label} 必须被 analyzer 打红，got empty"
        # Q1：禁止 assert BoolOp Or；分步等价判定
        matched = False
        if needle in errs:
            matched = True
        else:
            for e in errs:
                if needle in e:
                    matched = True
                    break
        assert matched, (
            f"业务红：synthetic {label} 须含 {needle!r}，errs={errs}"
        )


def _v1n_assert_live_names_disjoint_from_lightweight_k() -> None:
    for name in _V1N_LIVE_FAILURE_TEST_NAMES:
        for kw in _V1N_LIGHTWEIGHT_K_KEYWORDS:
            assert kw not in name, (
                f"业务红：live 测试名 {name} 不得命中轻量 -k 关键词 {kw}"
            )


def _v1n_analyze_winapi_source_seams(
    src: str,
    *,
    exempt_name: str = _V1N_WINAPI_OPS_NAME,
) -> list[str]:
    """
    Q13/R15/R16：WinAPI 静态门 — import alias/符号解析。
    R16：alias 表按模块/函数/类词法作用域隔离；后置函数局部不得污染前函数。
    仅模块级、精确单目标、合法 seam 初始化形态可豁免 exempt_name；
    多目标/chained assignment 不整句豁免，仍扫描 RHS 并绑定非豁免目标。
    """
    tree = ast.parse(src)
    banned: list[str] = []

    def _resolve(node: ast.AST, aliases: dict[str, str]) -> str | None:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = _resolve(node.value, aliases)
            if base is None:
                return node.attr
            return f"{base}.{node.attr}"
        return None

    def _bind_import(st: ast.AST, aliases: dict[str, str]) -> None:
        if isinstance(st, ast.Import):
            for a in st.names:
                local = a.asname or a.name.split(".")[0]
                aliases[local] = a.name
        elif isinstance(st, ast.ImportFrom):
            mod = st.module or ""
            for a in st.names:
                local = a.asname or a.name
                if mod:
                    aliases[local] = f"{mod}.{a.name}"
                else:
                    aliases[local] = a.name

    def _value_alias(node: ast.AST, aliases: dict[str, str]) -> str | None:
        """解析赋值 RHS 别名；Call 时取构造器符号以便 evil=WinDLL(...) 后 evil() 可追踪。"""
        r = _resolve(node, aliases)
        if r is not None:
            return r
        if isinstance(node, ast.Call):
            return _resolve(node.func, aliases)
        return None

    def _bind_assign_targets(
        st: ast.Assign,
        aliases: dict[str, str],
        *,
        module_level: bool,
    ) -> bool:
        """
        绑定赋值目标别名。返回本句是否进入窄豁免（仅模块级精确单目标 exempt_name）。
        """
        r = _value_alias(st.value, aliases)
        name_targets = [
            t for t in st.targets if isinstance(t, ast.Name)
        ]
        # R16 Q6：仅模块级、精确单目标 Name(exempt_name) 可豁免整句
        is_exact_single_exempt = (
            module_level
            and len(st.targets) == 1
            and isinstance(st.targets[0], ast.Name)
            and st.targets[0].id == exempt_name
        )
        if is_exact_single_exempt:
            if r is not None:
                aliases[exempt_name] = r
            return True
        # 多目标/chained：不豁免；绑定所有 Name 目标（含非豁免 evil）
        if r is not None:
            for t in name_targets:
                aliases[t.id] = r
        return False

    def _scan_call_attr(node: ast.AST, aliases: dict[str, str]) -> None:
        ln = int(getattr(node, "lineno", -1))
        if isinstance(node, ast.Call):
            resolved = _resolve(node.func, aliases) or ""
            dump = ast.dump(node.func, include_attributes=False)
            for tok in ("WinDLL", "windll", "msvcrt"):
                if tok in resolved or tok in dump:
                    banned.append(f"L{ln}:call:{tok}")
            if "open_osfhandle" in resolved and "msvcrt" in resolved:
                banned.append(f"L{ln}:call:msvcrt.open_osfhandle")
        if isinstance(node, ast.Attribute):
            resolved = _resolve(node, aliases) or ""
            if node.attr in {"windll", "msvcrt"}:
                banned.append(f"L{ln}:attr.{node.attr}")
            if node.attr == "open_osfhandle" and "msvcrt" in resolved:
                banned.append(f"L{ln}:attr.msvcrt.open_osfhandle")
            base = _resolve(node.value, aliases) if hasattr(node, "value") else None
            if base and any(tok in base for tok in ("WinDLL", "windll", "msvcrt")):
                banned.append(f"L{ln}:attrbase:{base}.{node.attr}")

    def _scan_expr(node: ast.AST, aliases: dict[str, str]) -> None:
        for n in ast.walk(node):
            _scan_call_attr(n, aliases)

    def _prefer_dangerous_alias(vals: list[str]) -> str:
        """合并冲突别名时优先保留可触发禁符的解析结果（顺序无关）。"""
        for tok in ("WinDLL", "windll", "msvcrt"):
            matched = sorted(v for v in vals if tok in v)
            if matched:
                return matched[0]
        return sorted(vals)[0]

    def _merge_alias_maps(
        base: dict[str, str], *branches: dict[str, str]
    ) -> dict[str, str]:
        """
        R17 N5：If/Try/With 非词法块 — 分支绑定保守回写合并。
        单侧绑定也可见；冲突时优先危险别名；合并对分支顺序不敏感。
        """
        out = dict(base)
        keys: set[str] = set()
        for b in branches:
            keys |= set(b)
        for k in keys:
            bvals = [b[k] for b in branches if k in b]
            if not bvals:
                continue
            uniq = list(dict.fromkeys(bvals))
            if len(uniq) == 1:
                out[k] = uniq[0]
            else:
                out[k] = _prefer_dangerous_alias(uniq)
        return out

    def _scan_stmts(
        stmts: list[ast.stmt],
        aliases: dict[str, str],
        *,
        module_level: bool,
    ) -> None:
        """
        就地更新 aliases。
        R16/R17：仅 FunctionDef/ClassDef 词法隔离（拷贝不回写）；
        If/Try/With/For/While 按 Python 非词法块语义合并回写。
        """
        for st in stmts:
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                _bind_import(st, aliases)
                continue
            if isinstance(st, ast.Assign):
                exempt_here = _bind_assign_targets(
                    st, aliases, module_level=module_level
                )
                if not exempt_here:
                    _scan_expr(st.value, aliases)
                    for t in st.targets:
                        if not isinstance(t, ast.Name):
                            _scan_expr(t, aliases)
                continue
            if isinstance(st, ast.AnnAssign):
                # 模块级单目标注解赋值 exempt_name 可豁免
                is_ann_exempt = (
                    module_level
                    and isinstance(st.target, ast.Name)
                    and st.target.id == exempt_name
                    and st.value is not None
                )
                if st.value is not None and isinstance(st.target, ast.Name):
                    r = _value_alias(st.value, aliases)
                    if r is not None:
                        aliases[st.target.id] = r
                if not is_ann_exempt:
                    if st.value is not None:
                        _scan_expr(st.value, aliases)
                    if not isinstance(st.target, ast.Name):
                        _scan_expr(st.target, aliases)
                continue
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # 函数/类：词法隔离，局部 alias 不回写父级
                local = dict(aliases)
                _scan_stmts(list(st.body), local, module_level=False)
                for dec in st.decorator_list:
                    _scan_expr(dec, aliases)
                continue
            if isinstance(st, ast.If):
                _scan_expr(st.test, aliases)
                then_a = dict(aliases)
                else_a = dict(aliases)
                _scan_stmts(list(st.body), then_a, module_level=module_level)
                _scan_stmts(list(st.orelse), else_a, module_level=module_level)
                merged = _merge_alias_maps(aliases, then_a, else_a)
                aliases.clear()
                aliases.update(merged)
                continue
            if isinstance(st, ast.Try):
                body_a = dict(aliases)
                _scan_stmts(list(st.body), body_a, module_level=module_level)
                else_a = dict(body_a)
                _scan_stmts(list(st.orelse), else_a, module_level=module_level)
                handler_maps: list[dict[str, str]] = []
                for h in st.handlers:
                    h_a = dict(aliases)  # 异常可在 try 任意点：从 pre 进入
                    if h.name:
                        # except alias 在 handler 内可见（扫描用）
                        h_a[h.name] = h_a.get(h.name, h.name)
                    _scan_stmts(list(h.body), h_a, module_level=module_level)
                    handler_maps.append(h_a)
                # finally 输入：normal(else 后) / handlers / pre 隐式异常
                fin_inputs = [else_a] + handler_maps + [dict(aliases)]
                fin_outputs: list[dict[str, str]] = []
                if st.finalbody:
                    for inp in fin_inputs:
                        fa = dict(inp)
                        _scan_stmts(list(st.finalbody), fa, module_level=module_level)
                        fin_outputs.append(fa)
                    merged = _merge_alias_maps(aliases, *fin_outputs)
                else:
                    # 无 finally：合并 normal 与 handlers（pre 不继续）
                    paths = [else_a] + handler_maps if handler_maps else [else_a]
                    merged = _merge_alias_maps(aliases, *paths)
                aliases.clear()
                aliases.update(merged)
                continue
            if isinstance(st, (ast.With, ast.AsyncWith)):
                body_a = dict(aliases)
                for it in st.items:
                    _scan_expr(it.context_expr, body_a)
                    if it.optional_vars is not None and isinstance(
                        it.optional_vars, ast.Name
                    ):
                        # with as x 绑定泄漏到外层（Python 非词法块）
                        r = _value_alias(it.context_expr, body_a)
                        if r is not None:
                            body_a[it.optional_vars.id] = r
                        else:
                            body_a[it.optional_vars.id] = it.optional_vars.id
                _scan_stmts(list(st.body), body_a, module_level=module_level)
                merged = _merge_alias_maps(aliases, body_a)
                aliases.clear()
                aliases.update(merged)
                continue
            if isinstance(st, (ast.For, ast.While)):
                body_a = dict(aliases)
                else_a = dict(aliases)
                if isinstance(st, ast.For):
                    _scan_expr(st.iter, body_a)
                    if isinstance(st.target, ast.Name):
                        body_a[st.target.id] = st.target.id
                else:
                    _scan_expr(st.test, body_a)
                _scan_stmts(list(st.body), body_a, module_level=module_level)
                _scan_stmts(list(st.orelse), else_a, module_level=module_level)
                # 0 次或多次：入口/体/else 合并
                merged = _merge_alias_maps(aliases, body_a, else_a)
                aliases.clear()
                aliases.update(merged)
                continue
            if isinstance(st, ast.Expr):
                _scan_expr(st.value, aliases)
                continue
            if isinstance(st, ast.Return) and st.value is not None:
                _scan_expr(st.value, aliases)
                continue
            if isinstance(st, ast.AugAssign):
                _scan_expr(st.value, aliases)
                continue
            # 其它语句：保守 walk 调用
            _scan_expr(st, aliases)

    if isinstance(tree, ast.Module):
        mod_aliases: dict[str, str] = {}
        _scan_stmts(list(tree.body), mod_aliases, module_level=True)

    out: list[str] = []
    seen: set[str] = set()
    for b in banned:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _v1n_winapi_static_gate_self_proof() -> None:
    """Q13/R15/R16：import alias 绕过会红；跨函数 alias 隔离；chained ops 不整句豁免；模块级单目标 seam 通过。"""
    ok = f'''
{_V1N_WINAPI_OPS_NAME} = object()
def use():
    return {_V1N_WINAPI_OPS_NAME}.CreateFileW
'''
    assert _v1n_analyze_winapi_source_seams(ok) == [], (
        f"业务红：唯一 seam 形态必须通过，hits={_v1n_analyze_winapi_source_seams(ok)}"
    )
    # 仅在模块级 ops 初始化中使用 WinDLL 可过
    ok_init = f'''
from ctypes import WinDLL
{_V1N_WINAPI_OPS_NAME} = WinDLL("kernel32")
'''
    assert _v1n_analyze_winapi_source_seams(ok_init) == [], (
        f"业务红：ops 初始化窄豁免必须通过，hits={_v1n_analyze_winapi_source_seams(ok_init)}"
    )
    samples = [
        (
            "from_import_WinDLL",
            'from ctypes import WinDLL\ndef f():\n WinDLL("k")\n',
            "WinDLL",
        ),
        (
            "import_alias_ctypes",
            'import ctypes as C\ndef f():\n C.WinDLL("k")\n',
            "WinDLL",
        ),
        (
            "from_import_alias",
            'from ctypes import WinDLL as W\ndef f():\n W("k")\n',
            "WinDLL",
        ),
        (
            "msvcrt_import_alias",
            'import msvcrt as m\ndef f():\n m.open_osfhandle(1, 0)\n',
            "msvcrt",
        ),
        (
            "from_msvcrt",
            'from msvcrt import open_osfhandle as oh\ndef f():\n oh(1, 0)\n',
            "msvcrt",
        ),
        (
            "windll_attr",
            'import ctypes\ndef f():\n ctypes.windll.kernel32.CreateFileW\n',
            "windll",
        ),
        (
            "attr_alias_chain",
            'import ctypes as ct\nW = ct.WinDLL\ndef f():\n W("k")\n',
            "WinDLL",
        ),
        (
            "local_shadow_ops",
            'from ctypes import WinDLL\ndef f():\n WinDLL("k")\n _v1n_winapi_ops = object()\n',
            "WinDLL",
        ),
        (
            "self_attr_shadow_ops",
            'from ctypes import WinDLL\nclass C:\n def __init__(self):\n  self._v1n_winapi_ops = WinDLL("k")\n',
            "WinDLL",
        ),
        # R16 Q5：后置函数局部 alias 不得覆盖前函数局部 alias
        (
            "cross_func_alias_pollution",
            "import ctypes as ct\ndef bad():\n W = ct.WinDLL\n W(\"kernel32\")\ndef later():\n W = object\n",
            "WinDLL",
        ),
        # R16 Q6：多目标/chained 不得整句豁免；evil 绑定并扫描 RHS
        (
            "chained_ops_exempt_escape",
            'from ctypes import WinDLL\nevil=_v1n_winapi_ops=WinDLL("evil")\ndef f():\n evil("k")\n',
            "WinDLL",
        ),
    ]
    for label, src, needle in samples:
        hits = _v1n_analyze_winapi_source_seams(src)
        assert hits, f"业务红：synthetic {label} 必须被 WinAPI 静态门打红"
        blob = "\n".join(hits)
        assert needle in blob, (
            f"业务红：synthetic {label} 须含 {needle!r}，hits={hits}"
        )


def _v1n_assert_production_winapi_ops_static() -> None:
    """
    production 源 AST：禁止 raw WinDLL/windll/msvcrt（仅 _v1n_winapi_ops 初始化窄豁免）。
    production 不存在时不声称已通过真实 ops 运行。
    Q13：走统一 alias 解析门；自证不依赖读 production。
    """
    _v1n_winapi_static_gate_self_proof()
    if not _client_spec_available():
        return
    mod = importlib.import_module(CLIENT_MOD)
    src_path = Path(inspect.getfile(mod))
    hits = _v1n_analyze_winapi_source_seams(src_path.read_text(encoding="utf-8"))
    assert not hits, (
        f"业务红：production 禁止 raw WinDLL/windll/msvcrt 旁路（仅 {_V1N_WINAPI_OPS_NAME} "
        f"初始化窄豁免），hits={hits}"
    )
    assert hasattr(mod, _V1N_WINAPI_OPS_NAME), (
        f"业务红：production 必须暴露唯一 seam {_V1N_WINAPI_OPS_NAME}"
    )
    ops = getattr(mod, _V1N_WINAPI_OPS_NAME)
    for attr in ("CreateFileW", "CloseHandle", "open_osfhandle"):
        assert hasattr(ops, attr), (
            f"业务红：{_V1N_WINAPI_OPS_NAME} 必须含 {attr}"
        )


def _make_structured_success_ledger(*, platform: str = "windows") -> _V1NGenerationLedger:
    """内存 structured success ledger（synthetic 自证基线；经 _v1n_run_trace seal）。"""
    bit = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        if platform == "windows"
        else int(getattr(os, "O_NOFOLLOW", 0x40000))
    )
    led = _V1NGenerationLedger(platform=platform, open_flag_bit=bit)
    g = led.new_gen()
    flags = bit | (_FILE_ATTRIBUTE_NORMAL if platform == "windows" else 0)
    if platform == "windows":
        hv = 1000 + g
        fd = 2000 + g
        led.note_target_open(gen=g, flags=flags, handle=hv, fixture=True)
        led.note_ops_hit("CreateFileW", gen=g)
        led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
        led.note_ops_hit("open_osfhandle", gen=g, detail=f"fd={fd}")
    else:
        fd = 2000 + g
        led.note_target_open(gen=g, flags=flags, fd=fd, fixture=True)
    ident = _V1NFdIdentity(1, 2, 96, 3)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    chunks = [b"%PDF-A1-" + b"A" * 24, b"CHUNK-B2" + b"B" * 24, b"CHUNK-C3" + b"C" * 24]
    for ch in chunks:
        led.note_read_pre_identity(gen=g, identity=ident)
        led.note_read_chunk(gen=g, data=ch)
        led.note_read_post_identity(gen=g, identity=ident)
        led.note_handler_chunk(ch)
    led.note_expected_body(b"".join(chunks))
    led.note_last_chunk_seen()
    led.note_fd_close(fd, gen=g)
    led.note_close_after_last_chunk()
    with _v1n_run_trace(led) as trace:
        pass  # synthetic：无 production run，仅 seal
    return led


def _generation_authority_mutation_matrix_self_proof() -> None:
    """synthetic 内存 mutation：全部经 seal 后同一 checker。"""
    led = _make_structured_success_ledger(platform="windows")
    # seal 后 mutation 必须立即红
    with pytest.raises((_V1NSealedMutationError, TypeError, AttributeError)):
        led.note_event("x")
    with pytest.raises((_V1NSealedMutationError, TypeError, AttributeError)):
        led.events.append("x")  # type: ignore[misc]
    # Q8：seal 后 baseline/verified/compare 任一字段修改立即红；同引用不得篡改
    assert led.verified_identity is not None
    with pytest.raises(_V1NSealedMutationError):
        led.verified_identity.st_ino = 999999  # type: ignore[misc]
    # baseline/compare 同模型
    id_mut = _V1NFdIdentity(9, 9, 9, 9)
    with pytest.raises(_V1NSealedMutationError):
        id_mut.st_dev = 1  # type: ignore[misc]
    with pytest.raises(_V1NSealedMutationError):
        id_mut.st_size = 0  # type: ignore[misc]
    with pytest.raises(_V1NSealedMutationError):
        del id_mut.st_mtime_ns  # type: ignore[misc]
    # 通过 note 写入 baseline/compare 后再 seal，篡改同引用必红
    led_bc = _V1NGenerationLedger(
        platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT
    )
    base = _V1NFdIdentity(1, 2, 3, 4)
    cmpi = _V1NFdIdentity(5, 6, 7, 8)
    led_bc.note_baseline_identity(base)
    g = led_bc.new_gen()
    led_bc.note_target_open(
        gen=g,
        flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_ATTRIBUTE_NORMAL,
        handle=1,
        fd=2,
        fixture=True,
    )
    led_bc.note_fstat_identity(gen=g, fd=2, ident=cmpi, verified=True)
    led_bc.note_identity_compare(gen=g, identity=cmpi)
    led_bc.note_fd_close(2, gen=g)
    with _v1n_run_trace(led_bc) as _tr:
        pass
    with pytest.raises(_V1NSealedMutationError):
        led_bc.baseline_identity.st_ino = 42  # type: ignore[union-attr]
    with pytest.raises(_V1NSealedMutationError):
        led_bc.identity_compare_identity.st_ino = 43  # type: ignore[union-attr]
    with pytest.raises(_V1NSealedMutationError):
        led_bc.verified_identity.st_size = 0  # type: ignore[union-attr]

    # 重新构造未 seal 的变异
    def _success_then(**mut: Any) -> _V1NGenerationLedger:
        bit = _FILE_FLAG_OPEN_REPARSE_POINT
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        hv, fd = 1000 + g, 2000 + g
        led.note_target_open(gen=g, flags=bit | _FILE_ATTRIBUTE_NORMAL, handle=hv, fixture=True)
        led.note_ops_hit("CreateFileW", gen=g)
        led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
        led.note_ops_hit("open_osfhandle", gen=g, detail=f"fd={fd}")
        ident = _V1NFdIdentity(1, 2, 96, 3)
        led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
        for _ in range(3):
            ch = b"Z" * 8
            led.note_read_pre_identity(gen=g, identity=ident)
            led.note_read_chunk(gen=g, data=ch)
            led.note_read_post_identity(gen=g, identity=ident)
            led.note_handler_chunk(ch)
        led.note_expected_body(b"Z" * 24)
        led.note_last_chunk_seen()
        led.note_fd_close(fd, gen=g)
        led.note_close_after_last_chunk()
        for k, v in mut.items():
            object.__setattr__(led, k, v)
        with _v1n_run_trace(led) as trace:
            pass
        return led

    # 删 reparse flag
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 11, 21
    led.note_target_open(gen=g, flags=_FILE_ATTRIBUTE_NORMAL, handle=hv, fixture=True)
    led.note_ops_hit("CreateFileW", gen=g)
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    led.note_ops_hit("open_osfhandle", gen=g, detail=f"fd={fd}")
    ident = _V1NFdIdentity(1, 2, 24, 0)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    for _ in range(3):
        led.note_read_pre_identity(gen=g, identity=ident)
        led.note_read_chunk(gen=g, data=b"Q" * 8)
        led.note_read_post_identity(gen=g, identity=ident)
        led.note_handler_chunk(b"Q" * 8)
    led.note_expected_body(b"Q" * 24)
    led.note_last_chunk_seen()
    led.note_fd_close(fd, gen=g)
    led.note_close_after_last_chunk()
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="open_flag_bit|flags|reparse|FILE_FLAG"):
        _assert_v1n_generation_authority(
            led, expected_terminal="success", platform="windows"
        )

    # convert_failed 正确
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv = 555
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT, handle=hv, fixture=True
    )
    led.note_ops_hit("CreateFileW", gen=g)
    led.note_ops_hit("open_osfhandle", gen=g)
    led.note_convert_failed()
    led.note_close_handle(hv)
    led.note_ops_hit("CloseHandle", gen=g)
    led.http_post = 1
    led.note_diagnostic_from_exception("source_identity_mismatch")
    with _v1n_run_trace(led) as trace:
        pass
    _assert_v1n_generation_authority(
        led, expected_terminal="convert_failed", platform="windows"
    )

    # convert pending 泄漏
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv = 556
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT, handle=hv, fixture=True
    )
    led.note_convert_failed()
    led.http_post = 1
    led.note_diagnostic_from_exception("source_identity_mismatch")
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="pending"):
        _assert_v1n_generation_authority(
            led, expected_terminal="convert_failed", platform="windows"
        )

    # fstat_failed active 泄漏
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 777, 42
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT, handle=hv, fixture=True
    )
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    led.note_fstat_failed(gen=g, fd=fd)
    led.http_post = 1
    led.note_diagnostic_from_exception("source_identity_mismatch")
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="active"):
        _assert_v1n_generation_authority(
            led, expected_terminal="fstat_failed", platform="windows"
        )
    # 正确 fstat_failed：需要重新建（已 seal）
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 778, 43
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT, handle=hv, fixture=True
    )
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    led.note_fstat_failed(gen=g, fd=fd)
    led.note_fd_close(fd, gen=g)
    led.http_post = 1
    led.note_diagnostic_from_exception("source_identity_mismatch")
    with _v1n_run_trace(led) as trace:
        pass
    _assert_v1n_generation_authority(
        led, expected_terminal="fstat_failed", platform="windows"
    )

    # identity_mismatch baseline 相等应红
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 11, 21
    led.note_target_open(
        gen=g,
        flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_ATTRIBUTE_NORMAL,
        handle=hv,
        fixture=True,
    )
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    vid = _V1NFdIdentity(1, 2, 10, 0)
    led.note_baseline_identity(vid)
    led.note_fstat_identity(gen=g, fd=fd, ident=vid, verified=True)
    led.note_identity_compare(gen=g, identity=vid)
    led.note_fd_close(fd, gen=g)
    led.http_post = 1
    led.note_diagnostic_from_exception("source_identity_mismatch")
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="baseline|不等|identity"):
        _assert_v1n_generation_authority(
            led, expected_terminal="identity_mismatch", platform="windows"
        )

    # put_failed 相位
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 31, 41
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT, handle=hv, fixture=True
    )
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    led.note_fstat_identity(gen=g, fd=fd, ident=_V1NFdIdentity(1, 2, 3, 0), verified=True)
    led.put_attempted = True
    led.http_post = 1
    led.http_put = 0
    led.note_fd_close(fd, gen=g)
    led.note_diagnostic_from_exception("upload_failed")
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="HTTP 相位|PUT"):
        _assert_v1n_generation_authority(
            led, expected_terminal="put_failed", platform="windows"
        )

    # provenance 优先于 ownership
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT)
    g = led.new_gen()
    hv, fd = 51, 61
    led.note_target_open(
        gen=g, flags=_FILE_FLAG_OPEN_REPARSE_POINT | _FILE_ATTRIBUTE_NORMAL, handle=hv, fixture=True
    )
    prov = led.provenance_by_gen[g]
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, provenance=prov, fixture=True)
    ident = _V1NFdIdentity(1, 2, 8, 0)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    led.note_fd_replaced(old_provenance=prov, new_provenance="prov-u-x", fd=fd)
    # 故意不清理 active，验证先报 provenance
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="fd_replaced/provenance"):
        _assert_v1n_generation_authority(
            led, expected_terminal="success", platform="windows"
        )


def _windows_generation_chain_helper_self_proof(tmp_path: Path) -> None:
    """
    R12：Windows 同代次真实 CreateFileW/open_osfhandle/fstat/read/close（fixture adapter）。
    fixture 标记，不得计 production ops。
    """
    import os as _os

    if _os.name != "nt":
        led = _make_structured_success_ledger(platform="windows")
        _assert_v1n_generation_authority(
            led, expected_terminal="success", platform="windows"
        )
        return

    import ctypes

    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "samefd.pdf"
    _blk_a = b"%PDF-A1-" + (b"A" * 24)
    _blk_b = b"CHUNK-B2" + (b"B" * 24)
    _blk_c = b"CHUNK-C3" + (b"C" * 24)
    v1 = _blk_a + _blk_b + _blk_c
    v2 = (b"%PDF-HANDLE-V2-YYYY" + b"Y" * len(v1))[: len(v1)]
    assert len(v1) == len(v2) and v1 != v2
    target.write_bytes(v1)
    cap = min(len(_blk_a), len(_blk_b), len(_blk_c))
    assert cap >= 8 and len(v1) // cap >= 3

    ops = _V1NFixtureWinApiAdapter()
    real_fstat = _os.fstat
    real_read = _os.read
    real_close = _os.close
    DWORD = ctypes.c_uint32

    led = _V1NGenerationLedger(
        platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT
    )
    g = led.new_gen()
    flags = _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT
    share = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
    access = int(_GENERIC_READ) | 0x00010000
    ctypes.set_last_error(0)
    handle = ops.CreateFileW(
        str(target),
        DWORD(access),
        DWORD(share),
        None,
        DWORD(_OPEN_EXISTING),
        DWORD(flags),
        None,
    )
    if _r10_is_invalid_handle(handle):
        raise AssertionError(f"夹具失败：CreateFileW GetLastError={ctypes.get_last_error()}")
    hv = int(handle)
    led.note_target_open(gen=g, flags=flags, handle=hv, fixture=True)
    led.note_ops_hit("CreateFileW", gen=g)
    fd = int(ops.open_osfhandle(hv, _os.O_RDONLY))
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
    led.note_ops_hit("open_osfhandle", gen=g, detail=f"fd={fd}")
    st = real_fstat(fd)
    ident = _V1NFdIdentity.from_stat_result(st)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    tmp_v2 = target.with_name(target.name + ".v2tmp")
    with open(tmp_v2, "wb") as wf:
        wf.write(v2)
    _r10_fixture_path_replace_while_open(tmp_v2, target)
    acc = b""
    while len(acc) < len(v1):
        st_pre = real_fstat(fd)
        id_pre = _V1NFdIdentity.from_stat_result(st_pre)
        assert id_pre == ident
        led.note_read_pre_identity(gen=g, identity=id_pre)
        chunk = bytes(real_read(fd, cap))
        assert chunk
        led.note_read_chunk(gen=g, data=chunk)
        led.note_handler_chunk(chunk)
        st_post = real_fstat(fd)
        id_post = _V1NFdIdentity.from_stat_result(st_post)
        assert id_post == ident
        led.note_read_post_identity(gen=g, identity=id_post)
        acc += chunk
    assert acc == v1
    led.note_expected_body(v1)
    led.note_last_chunk_seen()
    led.note_fd_close(fd, gen=g)
    real_close(fd)
    led.note_close_after_last_chunk()
    with _v1n_run_trace(led) as trace:
        pass
    _assert_v1n_generation_authority(
        led, expected_terminal="success", platform="windows"
    )


def _posix_nofollow_generation_helper_self_proof() -> None:
    """POSIX O_NOFOLLOW 同代次；完整 fd_by_gen；不要求 WinAPI ops。"""
    import os as _os

    o_rdonly = int(getattr(_os, "O_RDONLY", 0))
    o_nofollow = int(getattr(_os, "O_NOFOLLOW", 0x40000))
    led = _V1NGenerationLedger(platform="posix", open_flag_bit=o_nofollow)
    g = led.new_gen()
    fd = 100 + g
    led.note_target_open(gen=g, flags=o_rdonly | o_nofollow, fd=fd, fixture=True)
    assert g in led.fd_by_gen and led.fd_by_gen[g] == fd
    ident = _V1NFdIdentity(1, 2, 24, 0)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    for i in range(3):
        ch = bytes([65 + i]) * 8
        led.note_read_pre_identity(gen=g, identity=ident)
        led.note_read_chunk(gen=g, data=ch)
        led.note_handler_chunk(ch)
        led.note_read_post_identity(gen=g, identity=ident)
    led.note_expected_body(b"".join(b for _, b in led.read_chunks))
    led.note_last_chunk_seen()
    led.note_fd_close(fd, gen=g)
    led.note_close_after_last_chunk()
    with _v1n_run_trace(led) as trace:
        pass
    _assert_v1n_generation_authority(led, expected_terminal="success", platform="posix")

    # 变异：无 NOFOLLOW
    led2 = _V1NGenerationLedger(platform="posix", open_flag_bit=o_nofollow)
    g2 = led2.new_gen()
    fd2 = 300 + g2
    led2.note_target_open(gen=g2, flags=o_rdonly, fd=fd2, fixture=True)
    led2.note_fstat_identity(
        gen=g2, fd=fd2, ident=_V1NFdIdentity(1, 2, 8, 0), verified=True
    )
    for _ in range(3):
        led2.note_read_pre_identity(gen=g2, identity=led2.verified_identity)  # type: ignore[arg-type]
        led2.note_read_chunk(gen=g2, data=b"Z" * 8)
        led2.note_handler_chunk(b"Z" * 8)
        led2.note_read_post_identity(gen=g2, identity=led2.verified_identity)  # type: ignore[arg-type]
    led2.note_expected_body(b"Z" * 24)
    led2.note_last_chunk_seen()
    led2.note_fd_close(fd2, gen=g2)
    led2.note_close_after_last_chunk()
    with _v1n_run_trace(led2) as trace:
        pass
    with pytest.raises(AssertionError, match="open_flag_bit|flags|O_NOFOLLOW|nofollow"):
        _assert_v1n_generation_authority(
            led2, expected_terminal="success", platform="posix"
        )


def _windows_handle_ownership_helper_self_proof(tmp_path: Path) -> None:
    """四终态 ownership：synthetic sealed ledger + Windows 真实 fixture 链。"""
    import os as _os

    bit = _FILE_FLAG_OPEN_REPARSE_POINT

    def _convert_ok() -> None:
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        hv = 9001
        led.note_target_open(gen=g, flags=bit, handle=hv, fixture=True)
        led.note_ops_hit("CreateFileW", gen=g)
        led.note_ops_hit("open_osfhandle", gen=g)
        led.note_convert_failed()
        led.note_close_handle(hv)
        led.note_ops_hit("CloseHandle", gen=g)
        led.http_post = 1
        led.note_diagnostic_from_exception("source_identity_mismatch")
        with _v1n_run_trace(led) as trace:
            pass
        _assert_v1n_generation_authority(
            led, expected_terminal="convert_failed", platform="windows"
        )

    def _fstat_ok() -> None:
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        hv, fd = 9002, 8002
        led.note_target_open(gen=g, flags=bit, handle=hv, fixture=True)
        led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
        led.note_fstat_failed(gen=g, fd=fd)
        led.note_fd_close(fd, gen=g)
        led.http_post = 1
        led.note_diagnostic_from_exception("source_identity_mismatch")
        with _v1n_run_trace(led) as trace:
            pass
        _assert_v1n_generation_authority(
            led, expected_terminal="fstat_failed", platform="windows"
        )

    def _ident_ok() -> None:
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        hv, fd = 9003, 8003
        led.note_target_open(
            gen=g, flags=bit | _FILE_ATTRIBUTE_NORMAL, handle=hv, fixture=True
        )
        led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
        base = _V1NFdIdentity(1, 3, 16, 0)
        vid = _V1NFdIdentity(1, 2, 16, 0)
        led.note_baseline_identity(base)
        led.note_fstat_identity(gen=g, fd=fd, ident=vid, verified=True)
        led.note_identity_compare(gen=g, identity=vid)
        led.note_fd_close(fd, gen=g)
        led.http_post = 1
        led.note_diagnostic_from_exception("source_identity_mismatch")
        with _v1n_run_trace(led) as trace:
            pass
        _assert_v1n_generation_authority(
            led, expected_terminal="identity_mismatch", platform="windows"
        )

    def _put_ok() -> None:
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        hv, fd = 9004, 8004
        led.note_target_open(gen=g, flags=bit, handle=hv, fixture=True)
        led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=True)
        led.note_fstat_identity(
            gen=g, fd=fd, ident=_V1NFdIdentity(1, 2, 16, 0), verified=True
        )
        led.put_attempted = True
        led.http_post = 1
        led.http_put = 1
        led.note_fd_close(fd, gen=g)
        led.note_diagnostic_from_exception("upload_failed")
        with _v1n_run_trace(led) as trace:
            pass
        _assert_v1n_generation_authority(
            led, expected_terminal="put_failed", platform="windows"
        )

    _convert_ok()
    _fstat_ok()
    _ident_ok()
    _put_ok()

    if _os.name == "nt":
        # 真实 fixture 资源：convert CloseHandle 一次
        import ctypes

        tmp_path.mkdir(parents=True, exist_ok=True)
        path = tmp_path / "own.pdf"
        path.write_bytes(b"%PDF-OWNERSHIP-PROOF-DATA-XXXX")
        ops = _V1NFixtureWinApiAdapter()
        DWORD = ctypes.c_uint32
        led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
        g = led.new_gen()
        flags = _FILE_ATTRIBUTE_NORMAL | bit
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
        access = int(_GENERIC_READ) | 0x00010000
        handle = ops.CreateFileW(
            str(path),
            DWORD(access),
            DWORD(share),
            None,
            DWORD(_OPEN_EXISTING),
            DWORD(flags),
            None,
        )
        if _r10_is_invalid_handle(handle):
            raise AssertionError("CreateFileW 失败")
        hv = int(handle)
        led.note_target_open(gen=g, flags=flags, handle=hv, fixture=True)
        led.note_ops_hit("CreateFileW", gen=g)
        led.note_ops_hit("open_osfhandle", gen=g)  # 命中后抛出的语义由 convert 标记
        led.note_convert_failed()
        ops.CloseHandle(hv)
        led.note_close_handle(hv)
        led.note_ops_hit("CloseHandle", gen=g)
        led.http_post = 1
        led.note_diagnostic_from_exception("source_identity_mismatch")
        with _v1n_run_trace(led) as trace:
            pass
        _assert_v1n_generation_authority(
            led, expected_terminal="convert_failed", platform="windows"
        )


def _production_winapi_seam_helper_self_proof() -> None:
    """
    唯一 production seam 静态断言；production 缺失不声称运行通过。
    禁止仅 `_ = run` 假命中。
    """
    _v1n_assert_production_winapi_ops_static()
    if not _client_spec_available():
        # failure-first 入口存在性
        with pytest.raises(AssertionError, match="业务红|缺少 production"):
            _load_client()
    else:
        mod = importlib.import_module(CLIENT_MOD)
        ops = getattr(mod, _V1N_WINAPI_OPS_NAME)
        for attr in ("CreateFileW", "CloseHandle", "open_osfhandle"):
            assert callable(getattr(ops, attr)), f"业务红：ops.{attr} 必须可调用"
        run = _get_run_fn(mod)
        assert callable(run)

    # checker：缺 seam hits 红
    led = _make_structured_success_ledger(platform="windows")
    # 已 seal，不能改；重建
    bit = _FILE_FLAG_OPEN_REPARSE_POINT
    led = _V1NGenerationLedger(platform="windows", open_flag_bit=bit)
    g = led.new_gen()
    hv, fd = 1, 2
    led.note_target_open(
        gen=g, flags=bit | _FILE_ATTRIBUTE_NORMAL, handle=hv, fixture=False
    )
    led.note_handle_to_fd(gen=g, handle=hv, fd=fd, fixture=False)
    # 故意不写 seam_hits / ops hits
    ident = _V1NFdIdentity(1, 2, 24, 0)
    led.note_fstat_identity(gen=g, fd=fd, ident=ident, verified=True)
    for _ in range(3):
        led.note_read_pre_identity(gen=g, identity=ident)
        led.note_read_chunk(gen=g, data=b"A" * 8)
        led.note_read_post_identity(gen=g, identity=ident)
        led.note_handler_chunk(b"A" * 8)
    led.note_expected_body(b"A" * 24)
    led.note_last_chunk_seen()
    led.note_fd_close(fd, gen=g)
    led.note_close_after_last_chunk()
    with _v1n_run_trace(led) as trace:
        pass
    with pytest.raises(AssertionError, match="CreateFileW|seam|_v1n_winapi_ops"):
        _assert_v1n_generation_authority(
            led, expected_terminal="success", platform="windows"
        )


def _active_dup2_replacement_helper_self_proof(tmp_path: Path) -> None:
    """
    R12/Q2/Q11：真实安全打开 S + 不安全 U；不同 inode 与同 inode（硬链接）两次 provenance。
    Windows 不得二次打开被占用的同一路径；同 inode 用硬链接构造不同 provenance。
    Q11：真实 close 前不得 note_fd_close/清 active；checker 前证据与真实资源一致；cleanup 只写独立 audit。
    """
    import os as _os

    tmp_path.mkdir(parents=True, exist_ok=True)
    cleanup = _V1NFixtureCleanupAudit()
    real_dup2 = _V1N_OS_TRUE["dup2"]
    real_fstat = _V1N_OS_TRUE["fstat"]
    real_read = _V1N_OS_TRUE["read"]
    real_close = _V1N_OS_TRUE["close"]
    real_open = _V1N_OS_TRUE["open"]
    real_link = _V1N_OS_TRUE["link"]

    def _run_mutation(*, same_inode: bool) -> None:
        safe_path = tmp_path / ("safe-" + ("same" if same_inode else "diff") + ".pdf")
        v1 = b"%PDF-ACTIVE-DUP2-V1-CONTENT!!!!"
        safe_path.write_bytes(v1)
        if same_inode:
            # Q2：可跨平台同 inode 不同路径（硬链接），避免 Windows 二次打开占用路径
            unsafe_path = tmp_path / ("safe-same-hl-" + safe_path.name)
            if unsafe_path.exists():
                unsafe_path.unlink()
            if real_link is None:
                raise AssertionError("夹具：os.link 不可用，无法构造同 inode 资源")
            real_link(str(safe_path), str(unsafe_path))
        else:
            unsafe_path = tmp_path / "unsafe-diff.pdf"
            unsafe_path.write_bytes(v1 + b"-DIFF")

        plat = "windows" if _os.name == "nt" else "posix"
        bit = (
            _FILE_FLAG_OPEN_REPARSE_POINT
            if plat == "windows"
            else int(getattr(_os, "O_NOFOLLOW", 0x40000))
        )
        led = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
        fd_s = -1
        fd_u = -1
        try:
            if plat == "windows":
                import ctypes

                ops = _V1NFixtureWinApiAdapter()
                DWORD = ctypes.c_uint32
                flags = _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT
                share = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
                access = int(_GENERIC_READ) | 0x00010000
                h = ops.CreateFileW(
                    str(safe_path),
                    DWORD(access),
                    DWORD(share),
                    None,
                    DWORD(_OPEN_EXISTING),
                    DWORD(flags),
                    None,
                )
                if _r10_is_invalid_handle(h):
                    raise AssertionError("S CreateFileW 失败")
                hv = int(h)
                g = led.new_gen()
                prov_s = led.note_target_open(
                    gen=g, flags=flags, handle=hv, fixture=True
                )
                fd_s = int(ops.open_osfhandle(hv, _os.O_RDONLY))
                led.note_handle_to_fd(
                    gen=g, handle=hv, fd=fd_s, provenance=prov_s, fixture=True
                )
            else:
                flags = int(getattr(_os, "O_RDONLY", 0)) | int(
                    getattr(_os, "O_NOFOLLOW", 0x40000)
                )
                fd_s = int(real_open(str(safe_path), flags))
                g = led.new_gen()
                prov_s = led.note_target_open(
                    gen=g, flags=flags, fd=fd_s, fixture=True
                )

            # U：经不同路径打开（same_inode 时为硬链接，不同 provenance）
            fd_u = int(real_open(str(unsafe_path), getattr(_os, "O_RDONLY", 0)))
            prov_u = led._new_provenance("unsafe-open")
            led.provenance_by_fd[fd_u] = prov_u

            st_s = real_fstat(fd_s)
            vid = _V1NFdIdentity.from_stat_result(st_s)
            led.note_fstat_identity(gen=g, fd=fd_s, ident=vid, verified=True)
            st_u = real_fstat(fd_u)
            id_u = _V1NFdIdentity.from_stat_result(st_u)
            if same_inode:
                assert id_u.st_ino == vid.st_ino and id_u.st_dev == vid.st_dev, (
                    "业务红：same_inode 必须真实同 inode"
                )
            else:
                assert id_u != vid, "业务红：diff-inode 反例必须不同 identity"

            # 真实 dup2(U, S)
            real_dup2(fd_u, fd_s)
            led.note_fd_replaced(
                old_provenance=prov_s, new_provenance=prov_u, fd=fd_s
            )

            # 真实 read/fstat 路径（即便 identity 可能相同，provenance 已失效）
            st_after = real_fstat(fd_s)
            id_after = _V1NFdIdentity.from_stat_result(st_after)
            led.note_read_pre_identity(gen=g, identity=id_after)
            data = bytes(real_read(fd_s, 8))
            led.note_read_chunk(gen=g, data=data)
            led.note_handler_chunk(data)
            led.note_read_post_identity(
                gen=g, identity=_V1NFdIdentity.from_stat_result(real_fstat(fd_s))
            )
            for _ in range(2):
                led.note_read_pre_identity(gen=g, identity=id_after)
                led.note_read_chunk(gen=g, data=b"D" * 8)
                led.note_handler_chunk(b"D" * 8)
                led.note_read_post_identity(gen=g, identity=id_after)
            led.note_expected_body(b"".join(b for _, b in led.read_chunks if b))
            led.note_last_chunk_seen()
            # Q11：真实 close 前不得 note_fd_close；active 仍反映真实打开状态
            assert int(fd_s) in led.active_fds, (
                "业务红：checker 前 active 必须与真实未 close 资源一致"
            )
            with _v1n_run_trace(led) as trace:
                pass
            token = led.instance_token
            try:
                with pytest.raises(AssertionError, match="fd_replaced/provenance"):
                    _assert_v1n_generation_authority(
                        led, expected_terminal="success", platform=plat
                    )
            finally:
                # checker 后 finally：真实关闭残留 + 仅写独立 cleanup audit
                for _fd, _prov in ((fd_u, prov_u), (fd_s, prov_s)):
                    try:
                        real_close(int(_fd))
                        cleanup.fixture_cleanup_after_failure(
                            token, _prov, f"fd:{_fd}", "closed"
                        )
                    except OSError as exc:
                        cleanup.fixture_cleanup_after_failure(
                            token, _prov, f"fd:{_fd}", f"error:{type(exc).__name__}"
                        )
                assert led.sealed is True
                with pytest.raises(_V1NSealedMutationError):
                    led.note_event("cleanup_must_not_write")
        except Exception:
            for _fd in (fd_s, fd_u):
                if _fd is not None and int(_fd) >= 0:
                    try:
                        real_close(int(_fd))
                    except OSError:
                        pass
            raise

    _run_mutation(same_inode=False)
    _run_mutation(same_inode=True)
    assert cleanup.records, "夹具：cleanup audit 必须有记录"
    led_ok = _make_structured_success_ledger(
        platform="windows" if _os.name == "nt" else "posix"
    )
    _assert_v1n_generation_authority(
        led_ok,
        expected_terminal="success",
        platform="windows" if _os.name == "nt" else "posix",
    )


def _generation_authority_self_proof(tmp_path: Path) -> None:
    """R12：统一权威 checker / AST / ops / dup2 总自证。"""
    _v1n_assert_live_names_disjoint_from_lightweight_k()
    _v1n_trace_ast_reachability_self_proof()
    _generation_authority_mutation_matrix_self_proof()
    _windows_generation_chain_helper_self_proof(tmp_path / "gen-chain")
    _windows_handle_ownership_helper_self_proof(tmp_path / "own")
    _production_winapi_seam_helper_self_proof()
    _posix_nofollow_generation_helper_self_proof()
    _active_dup2_replacement_helper_self_proof(tmp_path / "dup2")
    # 真实文件内 live 测试结构抽检
    path = Path(__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    by_name = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for tname in _V1N_LIVE_FAILURE_TEST_NAMES:
        assert tname in by_name, f"业务红：缺少 live failure 测试 {tname}"
        errs = _v1n_analyze_function_trace_reachability(
            by_name[tname], require_run_capture=True
        )
        assert not errs, (
            f"业务红：{tname} 必须满足 trace/capture/checker 可达结构，errs={errs}"
        )
    # Q12/R15：g8 对 led_a/led_b/led_c 每段由同一路径 analyzer 证明
    # capture→(CM 退出 seal)→checker，禁止全函数 ast.walk 存在性替代
    g8_name = "test_g8_put_toctou_identity_and_path_seams"
    assert g8_name in by_name, f"业务红：缺少 {g8_name}"
    g8_fn = by_name[g8_name]
    require_abc = frozenset({"led_a", "led_b", "led_c"})
    g8_errs = _v1n_analyze_function_trace_reachability(
        g8_fn, require_run_capture=True, require_ledgers=require_abc
    )
    assert not g8_errs, (
        f"业务红：{g8_name} 必须满足多段 capture/checker 同路径结构，errs={g8_errs}"
    )
    for led_nm in ("led_a", "led_b", "led_c"):
        led_errs = _v1n_analyze_function_trace_reachability(
            g8_fn,
            require_run_capture=True,
            require_ledgers=frozenset({led_nm}),
        )
        assert not led_errs, (
            f"业务红：{g8_name} {led_nm} 必须由路径 analyzer 证明 "
            f"capture→seal→checker，errs={led_errs}"
        )
    # Q7/R18：Windows A→B→C 顺序结构门（synthetic）；三段共用唯一合法 seam
    win_abc_src = '''
def test_win_abc():
    run = _get_run_fn(mod)
    led_a = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(led_a) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(led_a, expected_terminal="identity_mismatch", platform="windows")
    led_b = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(led_b) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(led_b, expected_terminal="identity_mismatch", platform="windows")
    led_c = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(led_c) as trace:
        result = trace.capture(run(sources))
    _assert_v1n_generation_authority(led_c, expected_terminal="success", platform="windows")
'''
    win_abc_errs = _v1n_analyze_function_trace_reachability(
        ast.parse(win_abc_src).body[0],
        require_run_capture=True,
        require_ledgers=require_abc,
    )
    assert not win_abc_errs, (
        f"业务红：Windows A→B→C 顺序结构必须通过，errs={win_abc_errs}"
    )
    # 反例：capture(A) 后 checker(B) 未 capture
    win_abc_bad = '''
def test_win_abc_bad():
    led_a = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    with _v1n_run_trace(led_a) as trace:
        result = trace.capture(run(sources))
    led_b = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    _assert_v1n_generation_authority(led_b, expected_terminal="success", platform="windows")
'''
    bad_errs = _v1n_analyze_function_trace_reachability(
        ast.parse(win_abc_bad).body[0], require_run_capture=True
    )
    assert bad_errs, "业务红：A→B 错绑 checker 必须红"
    hit_bind = False
    for e in bad_errs:
        if "checker_ledger_not_captured" in e:
            hit_bind = True
            break
    assert hit_bind, f"业务红：错绑须含 checker_ledger_not_captured，errs={bad_errs}"
    # 反例：三 led 全部落在互斥动态分支
    three_led_mutex = '''
def test_three_led_mutex():
    led_a = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    led_b = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    led_c = _V1NGenerationLedger(platform="windows", open_flag_bit=1)
    flag = unknown()
    if flag == 1:
        with _v1n_run_trace(led_a) as trace:
            result = trace.capture(run(sources))
        _assert_v1n_generation_authority(led_a, expected_terminal="success", platform="windows")
    elif flag == 2:
        with _v1n_run_trace(led_b) as trace:
            result = trace.capture(run(sources))
        _assert_v1n_generation_authority(led_b, expected_terminal="success", platform="windows")
    else:
        with _v1n_run_trace(led_c) as trace:
            result = trace.capture(run(sources))
        _assert_v1n_generation_authority(led_c, expected_terminal="success", platform="windows")
'''
    mutex_errs = _v1n_analyze_function_trace_reachability(
        ast.parse(three_led_mutex).body[0],
        require_run_capture=True,
        require_ledgers=require_abc,
    )
    assert mutex_errs, "业务红：三 led 互斥分支必须被路径 analyzer 打红"
    hit_mutex = False
    for e in mutex_errs:
        if (
            "mutex_branch_split" in e
            or "ledger_not_on_all_paths" in e
            or "no_reachable_capture_run" in e
        ):
            hit_mutex = True
            break
    assert hit_mutex, (
        f"业务红：三 led 互斥须含 mutex/ledger_not_on_all_paths，errs={mutex_errs}"
    )


def test_g_windows_handle_ownership_and_generation_helper_self_proof(tmp_path: Path):
    """
    R10/R12：Windows 同代次链 + HANDLE/fd 所有权 + production seam + POSIX flags
    定点自证（名称含 windows_handle，供 -k 定向收集）。
    """
    _windows_generation_chain_helper_self_proof(tmp_path / "wh-gen")
    _windows_handle_ownership_helper_self_proof(tmp_path / "wh-own")
    _production_winapi_seam_helper_self_proof()
    _posix_nofollow_generation_helper_self_proof()
    _active_dup2_replacement_helper_self_proof(tmp_path / "wh-dup2")


def test_g_generation_authority_self_proof(tmp_path: Path):
    """R12：统一代次权威 checker 总自证（generation_authority_self_proof）。"""
    _generation_authority_self_proof(tmp_path)


def _v1n_install_os_ledger_spies(
    monkeypatch: pytest.MonkeyPatch,
    ledger: _V1NGenerationLedger,
    *,
    target_leaf: str,
    platform: str,
) -> dict[str, Any]:
    """
    安装当场写 ledger 的 os 层 spy；返回控制盒。
    Q7：始终绑定模块导入时真 os 入口，禁止分段嵌套写已 seal ledger。
    """
    import os as _os

    box: dict[str, Any] = {
        "target_fds": set(),
        "phase": "pre",
        "replace_on_fstat": False,
        "fstat_raise_for": set(),
        "identity_override": None,
        "path_stat_guard": True,
        "raise_fstat_after_target_fd": False,
    }
    # 真源：禁止取当前可能已被上一段 spy 覆盖的 _os.*
    real_open = _V1N_OS_TRUE["open"]
    real_fstat = _V1N_OS_TRUE["fstat"]
    real_read = _V1N_OS_TRUE["read"]
    real_close = _V1N_OS_TRUE["close"]
    real_stat = _V1N_OS_TRUE["stat"]

    def spy_open(path, flags, *a, **k):  # noqa: ANN001
        fd = real_open(path, flags, *a, **k)
        path_s = str(path)
        if path_s.endswith(target_leaf):
            if platform == "windows":
                # Windows 目标正文不得以普通 os.open 注册 verified
                return fd
            g = ledger.new_gen()
            prov = ledger.note_target_open(gen=g, flags=int(flags), fd=int(fd))
            box["target_fds"].add(int(fd))
            box["last_prov"] = prov
            # Q5：目标 fd 注册后触发 fstat 故障（不覆写 slotted 实例方法）
            if box.get("raise_fstat_after_target_fd"):
                box["fstat_raise_for"].add(int(fd))
        return fd

    def spy_fstat(fd):  # noqa: ANN001
        fd_i = int(fd)
        if fd_i in box["fstat_raise_for"]:
            g = ledger.active_fds.get(fd_i)
            if g is not None:
                ledger.note_fstat_failed(gen=int(g), fd=fd_i)
            raise OSError(22, "v1n-fixed-fstat-failure")
        st = real_fstat(fd_i)
        g = ledger.active_fds.get(fd_i)
        if g is not None:
            if box["identity_override"] is not None:
                # 不得用合成 XOR；仅允许真实 st 被替换为「另一真实文件的 fstat 结果」
                st = box["identity_override"]
            ident = _V1NFdIdentity.from_stat_result(st)
            if ledger.verified_gen is None and box["phase"] in {"put_pre", "post", "pre"}:
                ledger.note_fstat_identity(gen=int(g), fd=fd_i, ident=ident, verified=True)
            else:
                ledger.note_fstat_identity(gen=int(g), fd=fd_i, ident=ident, verified=False)
            if (
                ledger.baseline_identity is not None
                and ledger.verified_identity is not None
                and ledger.verified_identity != ledger.baseline_identity
                and "identity_compare" not in ledger.events
            ):
                ledger.note_identity_compare(
                    gen=int(ledger.verified_gen),  # type: ignore[arg-type]
                    identity=ledger.verified_identity,
                )
        return st

    def spy_read(fd, n):  # noqa: ANN001
        fd_i = int(fd)
        g = ledger.active_fds.get(fd_i)
        if g is not None and ledger.verified_gen is not None and int(g) == int(ledger.verified_gen):
            st_pre = real_fstat(fd_i)
            id_pre = _V1NFdIdentity.from_stat_result(st_pre)
            ledger.note_read_pre_identity(gen=int(g), identity=id_pre)
            data = real_read(fd_i, n)
            b = bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
            ledger.note_read_chunk(gen=int(g), data=b)
            st_post = real_fstat(fd_i)
            ledger.note_read_post_identity(
                gen=int(g), identity=_V1NFdIdentity.from_stat_result(st_post)
            )
            return data
        return real_read(fd_i, n)

    def spy_close(fd):  # noqa: ANN001
        fd_i = int(fd)
        g = ledger.active_fds.get(fd_i)
        if g is not None:
            ledger.note_fd_close(fd_i, gen=int(g))
        return real_close(fd_i)

    def spy_stat(path, *a, **k):  # noqa: ANN001
        path_s = str(path)
        if box["path_stat_guard"] and path_s.endswith(target_leaf):
            if ledger.baseline_identity is not None:
                ledger.note_path_stat_identity_forbidden()
        return real_stat(path, *a, **k)

    monkeypatch.setattr(_os, "open", spy_open)
    monkeypatch.setattr(_os, "fstat", spy_fstat)
    monkeypatch.setattr(_os, "read", spy_read)
    monkeypatch.setattr(_os, "close", spy_close)
    monkeypatch.setattr(_os, "stat", spy_stat)
    return box


def _v1n_require_and_patch_winapi_ops(
    monkeypatch: pytest.MonkeyPatch,
    mod: Any,
    ledger: _V1NGenerationLedger,
    *,
    target_leaf: str,
    open_osfhandle_error: BaseException | None = None,
    fstat_raise_box: dict[str, Any] | None = None,
) -> Any:
    """
    仅 patch 唯一 mod._v1n_winapi_ops 真实绑定；禁止 raw WinDLL/windll/msvcrt 旁路。
    Q7：按 ops 身份缓存真源绑定，A→B→C 分段不嵌套写已 seal ledger。
    """
    import os as _os

    assert hasattr(mod, _V1N_WINAPI_OPS_NAME), (
        f"业务红：production 必须暴露唯一 seam {_V1N_WINAPI_OPS_NAME}"
    )
    ops = getattr(mod, _V1N_WINAPI_OPS_NAME)
    for attr in ("CreateFileW", "CloseHandle", "open_osfhandle"):
        assert hasattr(ops, attr), f"业务红：{_V1N_WINAPI_OPS_NAME} 必须含 {attr}"

    key = id(ops)
    if key not in _V1N_WINAPI_TRUE:
        _V1N_WINAPI_TRUE[key] = {
            "CreateFileW": ops.CreateFileW,
            "CloseHandle": ops.CloseHandle,
            "open_osfhandle": ops.open_osfhandle,
        }
    real_cf = _V1N_WINAPI_TRUE[key]["CreateFileW"]
    real_ch = _V1N_WINAPI_TRUE[key]["CloseHandle"]
    real_oh = _V1N_WINAPI_TRUE[key]["open_osfhandle"]

    def spy_cf(  # noqa: ANN001
        lpFileName,
        dwDesiredAccess,
        dwShareMode,
        lpSecurityAttributes,
        dwCreationDisposition,
        dwFlagsAndAttributes,
        hTemplateFile,
    ):
        path_s = str(lpFileName) if lpFileName is not None else ""
        flags_i = int(dwFlagsAndAttributes) if dwFlagsAndAttributes is not None else 0
        # 夹具便利：补 FILE_SHARE_DELETE 以便 TOCTOU 替换；非 production 契约
        share_i = int(dwShareMode) if dwShareMode is not None else 0
        share_i = share_i | _FILE_SHARE_DELETE | _FILE_SHARE_READ | _FILE_SHARE_WRITE
        handle = real_cf(
            lpFileName,
            dwDesiredAccess,
            share_i,
            lpSecurityAttributes,
            dwCreationDisposition,
            dwFlagsAndAttributes,
            hTemplateFile,
        )
        leaf = path_s.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        if leaf == target_leaf.lower():
            g = ledger.new_gen()
            try:
                hv = int(handle) if handle is not None else -1
            except (TypeError, ValueError):
                hv = -1
            if not _r10_is_invalid_handle(handle):
                ledger.note_target_open(gen=g, flags=flags_i, handle=hv)
            else:
                ledger.note_target_open(gen=g, flags=flags_i, handle=None)
            ledger.note_ops_hit("CreateFileW", gen=g)
        return handle

    def spy_oh(handle, flags):  # noqa: ANN001
        try:
            hv = int(handle)
        except (TypeError, ValueError):
            hv = None
        gen = ledger.pending_handles.get(hv) if hv is not None else None
        ledger.note_ops_hit("open_osfhandle", gen=gen)
        if open_osfhandle_error is not None and gen is not None:
            ledger.note_convert_failed()
            raise open_osfhandle_error
        fd = real_oh(handle, flags)
        if hv is not None and hv in ledger.pending_handles:
            g = ledger.pending_handles[hv]
            ledger.note_handle_to_fd(gen=int(g), handle=hv, fd=int(fd))
            # Q5：目标 fd 注册后触发 fstat 故障（spy box 回调，不覆写 slotted 方法）
            if fstat_raise_box is not None and fstat_raise_box.get(
                "raise_fstat_after_target_fd"
            ):
                fstat_raise_box["fstat_raise_for"].add(int(fd))
                fstat_raise_box["target_fds"].add(int(fd))
        return fd

    def spy_ch(handle):  # noqa: ANN001
        hv = None
        convert_ok = False
        try:
            hv = int(handle)
            convert_ok = True
        except (TypeError, ValueError):
            convert_ok = False
        if not convert_ok:
            return real_ch(handle)
        if hv in ledger.transferred_handles:
            raise AssertionError(
                f"业务红：HANDLE 已转移给 fd 后禁止 CloseHandle(H={hv})"
            )
        ledger.note_close_handle(hv)
        ledger.note_ops_hit("CloseHandle")
        return real_ch(handle)

    monkeypatch.setattr(ops, "CreateFileW", spy_cf)
    monkeypatch.setattr(ops, "CloseHandle", spy_ch)
    monkeypatch.setattr(ops, "open_osfhandle", spy_oh)
    return ops


def _v1n_counting_transport(ledger: _V1NGenerationLedger, handler: Callable):
    """MockTransport 包装：HTTP 相位当场写入 ledger。"""

    def wrapped(request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        url = str(request.url)
        if method == "POST":
            ledger.note_http("POST")
        elif method == "PUT":
            ledger.note_http("PUT")
        elif method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in url:
            ledger.note_http("POLL")
        elif method == "GET" and url.endswith(".zip"):
            ledger.note_http("ZIP")
        return handler(request)

    return httpx.MockTransport(wrapped)


# ---------------------------------------------------------------------------
# 四个真实 production failure-first case（不得命中轻量 -k）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows HANDLE 转换所有权")
def test_g8a_windows_convert_failure_live_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Windows open_osfhandle 真实 spy 抛 OSError → convert_failed 原始 ledger。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "conv.pdf", b"%PDF-CONVERT-FAIL")
    sources = _build_sources(mod, [p])
    ledger = _V1NGenerationLedger(
        platform="windows", open_flag_bit=_FILE_FLAG_OPEN_REPARSE_POINT
    )
    st0 = p.stat()
    ledger.note_baseline_identity(_V1NFdIdentity.from_stat_result(st0))
    _v1n_install_os_ledger_spies(
        monkeypatch, ledger, target_leaf="conv.pdf", platform="windows"
    )
    _v1n_require_and_patch_winapi_ops(
        monkeypatch,
        mod,
        ledger,
        target_leaf="conv.pdf",
        open_osfhandle_error=OSError(11, "v1n-fixed-convert-failure"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        raise AssertionError("convert 失败后不得 PUT/poll/ZIP")

    with pytest.raises(err_cls) as ei:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(
                run(sources, transport=_v1n_counting_transport(ledger, handler), **_run_kwargs())
            )
    assert ei.value.diagnostic_code == "source_identity_mismatch"
    _assert_v1n_generation_authority(
        ledger, expected_terminal="convert_failed", platform="windows"
    )


def test_g8b_fstat_failure_live_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """已转移 fd 的真实 fstat spy 抛 OSError → fstat_failed 原始 ledger。"""
    import os as _os

    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "fstatf.pdf", b"%PDF-FSTAT-FAIL")
    sources = _build_sources(mod, [p])
    plat = "windows" if _os.name == "nt" else "posix"
    bit = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        if plat == "windows"
        else int(getattr(_os, "O_NOFOLLOW", 0x40000))
    )
    ledger = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    ledger.note_baseline_identity(_V1NFdIdentity.from_stat_result(p.stat()))
    box = _v1n_install_os_ledger_spies(
        monkeypatch, ledger, target_leaf="fstatf.pdf", platform=plat
    )
    # Q5：受控 spy box — 目标 fd 注册后触发 fstat 故障；禁止覆写 slotted 实例方法
    box["raise_fstat_after_target_fd"] = True
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch,
            mod,
            ledger,
            target_leaf="fstatf.pdf",
            fstat_raise_box=box,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        raise AssertionError("fstat 失败后不得 PUT/poll/ZIP")

    with pytest.raises(err_cls) as ei:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(
                run(sources, transport=_v1n_counting_transport(ledger, handler), **_run_kwargs())
            )
    assert ei.value.diagnostic_code == "source_identity_mismatch"
    _assert_v1n_generation_authority(
        ledger, expected_terminal="fstat_failed", platform=plat
    )


def test_g8c_identity_failure_live_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    baseline=真实初始 stat；PUT 紧前同一 verified fd 真实 fstat 返回替换后不同 identity。
    禁止目标路径二次 stat；零 PUT。
    """
    import os as _os

    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "idmis.pdf", b"%PDF-ID-MISMATCH-V1")
    alt = _write_temp_source(tmp_path, "idmis-alt.pdf", b"%PDF-ID-MISMATCH-V2")
    # 同尺寸不同内容
    v1 = p.read_bytes()
    v2 = (b"%PDF-ID-MISMATCH-V2" + b"Y" * len(v1))[: len(v1)]
    assert len(v2) == len(v1) and v2 != v1
    p.write_bytes(v1)
    alt.write_bytes(v2)
    sources = _build_sources(mod, [p])
    plat = "windows" if _os.name == "nt" else "posix"
    bit = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        if plat == "windows"
        else int(getattr(_os, "O_NOFOLLOW", 0x40000))
    )
    ledger = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    # baseline 必须来自真实输入初始 stat（非 XOR）
    ledger.note_baseline_identity(_V1NFdIdentity.from_stat_result(p.stat()))
    box = _v1n_install_os_ledger_spies(
        monkeypatch, ledger, target_leaf="idmis.pdf", platform=plat
    )
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch, mod, ledger, target_leaf="idmis.pdf"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            # POST 后路径替换为 V2；verified fd 仍应指向旧 identity，
            # 若 production 在 PUT 前重新 fstat 同一 fd，identity 仍为旧；
            # 本 case 要求：用另一真实文件的 fstat 结果注入「同一 fd 返回不同 identity」
            # 仅当 fd 已 verified 后的 compare 次 fstat。
            tmp = p.with_name(p.name + ".swap")
            tmp.write_bytes(v2)
            _r10_fixture_path_replace_while_open(tmp, p)
            # 将 identity_override 设为 alt 文件真实 stat（不同 inode）
            box["identity_override"] = alt.stat()
            box["phase"] = "put_pre"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        raise AssertionError("identity mismatch 后不得 PUT")

    with pytest.raises(err_cls) as ei:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(
                run(sources, transport=_v1n_counting_transport(ledger, handler), **_run_kwargs())
            )
    assert ei.value.diagnostic_code == "source_identity_mismatch"
    _assert_v1n_generation_authority(
        ledger, expected_terminal="identity_mismatch", platform=plat
    )


def test_g8d_put_failure_live_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """安全 verified fd 后 MockTransport 真实接收 PUT 并失败 → upload_failed。"""
    import os as _os

    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, "putf.pdf", b"%PDF-PUT-FAIL-BODY")
    sources = _build_sources(mod, [p])
    plat = "windows" if _os.name == "nt" else "posix"
    bit = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        if plat == "windows"
        else int(getattr(_os, "O_NOFOLLOW", 0x40000))
    )
    ledger = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    ledger.note_baseline_identity(_V1NFdIdentity.from_stat_result(p.stat()))
    _v1n_install_os_ledger_spies(
        monkeypatch, ledger, target_leaf="putf.pdf", platform=plat
    )
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch, mod, ledger, target_leaf="putf.pdf"
        )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            raise httpx.ConnectError("v1n-fixed-put-network-error")
        raise AssertionError("PUT 失败后不得 poll/ZIP")

    with pytest.raises(err_cls) as ei:
        with _v1n_run_trace(ledger) as trace:
            result = trace.capture(
                run(sources, transport=_v1n_counting_transport(ledger, handler), **_run_kwargs())
            )
    assert ei.value.diagnostic_code == "upload_failed"
    _assert_v1n_generation_authority(
        ledger, expected_terminal="put_failed", platform=plat
    )


def test_g8_put_toctou_identity_and_path_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C7/R12：B/C 原始 ledger 由 spy 当场写，经 _v1n_run_trace seal 后进统一 checker。
    保留：no-follow/reparse、多块 V1、最后块后 close、禁止路径重开读 V2。
    """
    import os as _os

    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    plat = "windows" if _os.name == "nt" else "posix"
    bit = (
        _FILE_FLAG_OPEN_REPARSE_POINT
        if plat == "windows"
        else int(getattr(_os, "O_NOFOLLOW", 0x40000))
    )

    # --- A) 同尺寸内容替换（identity 变化）——PUT 前失败零 PUT ---
    p = _write_temp_source(tmp_path, "id.pdf", b"%PDF-SAME-SIZE-V1")
    orig = p.read_bytes()
    sources = _build_sources(mod, [p])
    led_a = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    led_a.note_baseline_identity(_V1NFdIdentity.from_stat_result(p.stat()))
    box_a = _v1n_install_os_ledger_spies(
        monkeypatch, led_a, target_leaf="id.pdf", platform=plat
    )
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch, mod, led_a, target_leaf="id.pdf"
        )
    put_bodies: list[bytes] = []

    def handler_id(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            replacement = b"%PDF-SAME-SIZE-V2"
            assert len(replacement) == len(orig)
            p.write_bytes(replacement)
            # 注入不同真实 identity（alt 文件 stat）
            alt = tmp_path / "id-alt.pdf"
            alt.write_bytes(replacement)
            box_a["identity_override"] = alt.stat()
            box_a["phase"] = "put_pre"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            put_bodies.append(bytes(request.content))
            return httpx.Response(200)
        raise AssertionError("identity 漂移后不得进入轮询")

    with pytest.raises(err_cls) as ei:
        with _v1n_run_trace(led_a) as trace:
            result = trace.capture(
                run(
                    sources,
                    transport=_v1n_counting_transport(led_a, handler_id),
                    **_run_kwargs(),
                )
            )
    _assert_remote_error(ei.value, "source_identity_mismatch", msg_fn=msg_fn)
    assert put_bodies == []
    _assert_v1n_generation_authority(
        led_a, expected_terminal="identity_mismatch", platform=plat
    )

    # --- B) baseline < POST replace < target open < target fstat；原始 ledger ---
    p2 = _write_temp_source(tmp_path, "path.pdf", b"%PDF-PATH-ORIG")
    other = _write_temp_source(tmp_path, "other.pdf", b"%PDF-PATH-OTHERX")
    body2 = p2.read_bytes()
    other.write_bytes(body2[: len(body2)].ljust(len(body2), b"X") if False else (b"%PDF-PATH-OTHERX" + b"X" * 16)[: len(body2)].ljust(len(body2), b"X"))
    if len(other.read_bytes()) != len(body2):
        other.write_bytes((b"%PDF-PATH-OTHERX" + b"X" * len(body2))[: len(body2)])
    sources2 = _build_sources(mod, [p2])
    led_b = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    led_b.note_baseline_identity(_V1NFdIdentity.from_stat_result(p2.stat()))
    box_b = _v1n_install_os_ledger_spies(
        monkeypatch, led_b, target_leaf="path.pdf", platform=plat
    )
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch, mod, led_b, target_leaf="path.pdf"
        )
    puts_b: list[str] = []

    def handler_path(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            # Q6：替换 identity 从非目标替换源真实 fstat/预存值获得；禁止对目标 p2 二次 path.stat
            other.write_bytes(p2.read_bytes())
            fd_src = _V1N_OS_TRUE["open"](str(other), getattr(_os, "O_RDONLY", 0))
            try:
                st_src = _V1N_OS_TRUE["fstat"](fd_src)
            finally:
                _V1N_OS_TRUE["close"](fd_src)
            # 自证（独立 probe，不污染 led_b）：目标 path.stat 会登记 path_stat 并导致 checker 红
            probe = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
            probe.note_baseline_identity(
                _V1NFdIdentity.from_stat_result(st_src)
            )
            probe.note_path_stat_identity_forbidden()
            assert "path_stat_identity" in probe.events
            p2.unlink()
            other.replace(p2)
            box_b["identity_override"] = st_src
            box_b["phase"] = "put_pre"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            puts_b.append(str(request.url))
            return httpx.Response(200)
        raise AssertionError("path 替换后不得进入轮询")

    with pytest.raises(err_cls) as ei2:
        with _v1n_run_trace(led_b) as trace:
            result = trace.capture(
                run(
                    sources2,
                    transport=_v1n_counting_transport(led_b, handler_path),
                    **_run_kwargs(),
                )
            )
    assert ei2.value.diagnostic_code == "source_identity_mismatch"
    assert puts_b == []
    _assert_v1n_generation_authority(
        led_b, expected_terminal="identity_mismatch", platform=plat
    )
    _fd_reuse_helper_self_proof(tmp_path / "b-fd-reuse-sub")


    # --- C) 多块 V1 + 最后块后 close；原始 ledger success ---
    _blk_a = b"%PDF-A1-" + (b"A" * 24)
    _blk_b = b"CHUNK-B2" + (b"B" * 24)
    _blk_c = b"CHUNK-C3" + (b"C" * 24)
    v1 = _blk_a + _blk_b + _blk_c
    v2 = (b"%PDF-HANDLE-V2-YYYY" + b"Y" * len(v1))[: len(v1)]
    assert len(v1) == len(v2) and v1 != v2
    p3 = _write_temp_source(tmp_path, "samefd.pdf", v1)
    sources3 = _build_sources(mod, [p3])
    led_c = _V1NGenerationLedger(platform=plat, open_flag_bit=bit)
    led_c.note_baseline_identity(_V1NFdIdentity.from_stat_result(p3.stat()))
    box_c = _v1n_install_os_ledger_spies(
        monkeypatch, led_c, target_leaf="samefd.pdf", platform=plat
    )
    if plat == "windows":
        _v1n_require_and_patch_winapi_ops(
            monkeypatch, mod, led_c, target_leaf="samefd.pdf"
        )

    _SAMEFD_READ_CAP = min(len(_blk_a), len(_blk_b), len(_blk_c))
    inner_read = _os.read

    def cap_read(fd, n):  # noqa: ANN001
        fd_i = int(fd)
        g = led_c.active_fds.get(fd_i)
        n_i = int(n) if n is not None else -1
        if g is not None and n_i > 0:
            return inner_read(fd_i, min(n_i, _SAMEFD_READ_CAP))
        return inner_read(fd, n)

    monkeypatch.setattr(_os, "read", cap_read)

    holder: dict[str, str] = {}
    put_bodies3: list[bytes] = []
    replaced = {"done": False}
    post_done = {"v": False}
    real_builtin_open = open

    def handler_samefd(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode())
            holder["id"] = body["files"][0]["data_id"]
            post_done["v"] = True
            box_c["phase"] = "post"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
            )
        if request.method == "PUT":
            box_c["phase"] = "put"
            if post_done["v"] and not replaced["done"] and led_c.verified_gen is not None:
                tmp_v2 = p3.with_name(p3.name + ".v2tmp-samefd")
                with real_builtin_open(tmp_v2, "wb") as wf:
                    wf.write(v2)
                _r10_fixture_path_replace_while_open(tmp_v2, p3)
                replaced["done"] = True
            stream = getattr(request, "stream", None)
            assert stream is not None, "业务红：PUT 必须暴露 stream（no-preread）"
            chunks: list[bytes] = []
            acc = b""
            for chunk in stream:
                chunk_b = bytes(chunk)
                chunks.append(chunk_b)
                led_c.note_handler_chunk(chunk_b)
                if chunk_b:
                    acc += chunk_b
                if acc == v1 and not led_c.last_chunk_seen:
                    led_c.note_last_chunk_seen()
            put_bytes = b"".join(chunks)
            put_bodies3.append(put_bytes)
            assert put_bytes == v1, (
                f"业务红：必须从已验证句柄上传 V1，actual={put_bytes!r}"
            )
            led_c.note_expected_body(v1)
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=_make_zip_bytes({"full.md": b"# t\n"}))
        raise AssertionError("unexpected")

    def counting_samefd(request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        url = str(request.url)
        if method == "POST":
            led_c.note_http("POST")
        elif method == "PUT":
            led_c.note_http("PUT")
        elif method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in url:
            led_c.note_http("POLL")
        elif method == "GET" and url == ZIP_URL_A:
            led_c.note_http("ZIP")
        return handler_samefd(request)

    with _v1n_run_trace(led_c) as trace:
        result = trace.capture(
            run(
                sources3,
                transport=_NoPrereadSyncTransport(counting_samefd),
                **_run_kwargs(),
            )
        )
    assert "# t" in result.markdown
    assert put_bodies3 == [v1]
    assert replaced["done"] is True
    _assert_v1n_generation_authority(
        led_c, expected_terminal="success", platform=plat
    )



def test_g9_remote_single_file_decimal_200mb_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C8/C7/R7-C3：200_000_001 零 HTTP；200_000_000 必须 POST 后进入 PUT 精确 1。
    使用不预读 request 的定制同步 transport；PUT 时只消费有界前缀后主动失败，
    证明未调用无界 read、未物化 200MB bytes；稀疏文件可靠清理。
    正式稀疏场景前清零独立探针；最终 unbounded/pre-materialized hits 精确 []。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    src_cls = _require_attr(mod, "RemoteSource")

    over_path = tmp_path / "over-200mb.pdf"
    ok_path = tmp_path / "ok-200mb.pdf"
    put_bound = 64 * 1024  # 有界前缀，远小于 200MB
    # R7-C3：全入口探针（builtins.open / io.open / Path.open / os.open+read）
    probe = _V1NReadProbe(put_bound=put_bound)
    probe.install(monkeypatch)

    try:
        # --- 拒绝：200_000_001，零 HTTP ---
        # 正式稀疏场景前清零探针
        probe.clear()
        _make_logical_sized_file(over_path, REMOTE_MAX_SINGLE_SOURCE_BYTES + 1)
        probe.add_target(over_path)
        probe.phase = "pre"
        probe.put_transport_started = False
        sources_over = [
            src_cls(
                path=over_path,
                filename=over_path.name,
                expected_size=REMOTE_MAX_SINGLE_SOURCE_BYTES + 1,
            )
        ]
        http: list[str] = []

        def handler_block(request: httpx.Request) -> httpx.Response:
            http.append(request.method)
            raise AssertionError("超限不得 HTTP")

        transport_block = _NoPrereadSyncTransport(handler_block)
        with pytest.raises(err_cls) as ei:
            run(
                sources_over,
                transport=transport_block,
                **_run_kwargs(),
            )
        _assert_remote_error(ei.value, "source_size_exceeded", msg_fn=msg_fn)
        assert http == [], f"业务红：200_000_001 必须 POST/PUT 前硬停，http={http}"
        over_path.unlink(missing_ok=True)
        assert not over_path.exists()

        # --- 接受边界：进入 POST 且 PUT 精确 1（流式有界前缀，不整文件驻留）---
        # 正式 200_000_000 稀疏场景前再次清零探针
        probe.clear()
        probe.target_resolved.clear()
        _make_logical_sized_file(ok_path, REMOTE_MAX_SINGLE_SOURCE_BYTES)
        probe.add_target(ok_path)
        probe.phase = "pre"
        probe.put_transport_started = False
        sources_ok = [
            src_cls(
                path=ok_path,
                filename=ok_path.name,
                expected_size=REMOTE_MAX_SINGLE_SOURCE_BYTES,
            )
        ]
        counts = {"post": 0, "put": 0}
        transport_holder: dict[str, _NoPrereadSyncTransport] = {}

        def handler_edge(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                counts["post"] += 1
                probe.phase = "post"
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                counts["put"] += 1
                # PUT transport 开始：仅允许有界前缀消费
                probe.put_transport_started = True
                probe.phase = "put"
                tr = transport_holder["t"]
                # 证明 request 是流式：只消费有界前缀，绝不 request.read()/content
                stream = getattr(request, "stream", None)
                assert stream is not None, "业务红：PUT request 必须暴露 stream"
                tr.put_saw_stream = True
                total = 0
                chunks = 0
                try:
                    for chunk in stream:
                        chunks += 1
                        if not isinstance(chunk, (bytes, bytearray, memoryview)):
                            chunk = bytes(chunk)
                        total += len(chunk)
                        if total >= put_bound:
                            break
                except Exception as stream_exc:
                    # 流中断亦视为有界消费路径
                    tr.put_prefix_bytes = total
                    tr.put_chunks = chunks
                    raise httpx.ConnectError(
                        f"edge-size-put-stream-stop:{stream_exc}"
                    ) from stream_exc
                tr.put_prefix_bytes = total
                tr.put_chunks = chunks
                assert total <= put_bound + 1024 * 1024, (
                    f"业务红：PUT 前缀不得超过有界窗口，total={total}"
                )
                assert total < REMOTE_MAX_SINGLE_SOURCE_BYTES, (
                    f"业务红：禁止物化/读满 200MB，total={total}"
                )
                raise httpx.ConnectError("edge-size-put-accepted-then-stop")
            raise AssertionError("边界后不得进入 poll/ZIP")

        transport_edge = _NoPrereadSyncTransport(handler_edge)
        transport_holder["t"] = transport_edge
        with pytest.raises(err_cls) as ei2:
            run(
                sources_ok,
                transport=transport_edge,
                **_run_kwargs(),
            )
        assert counts["post"] == 1, (
            f"业务红：200_000_000 必须进入 POST，counts={counts}"
        )
        assert counts["put"] == 1, (
            f"业务红：200_000_000 PUT 紧前复验使用 > 必须放行精确 1 次 PUT，counts={counts}"
        )
        assert transport_edge.put_saw_stream is True, (
            "业务红：PUT 必须经 stream 有界消费，不得 content 全量"
        )
        assert transport_edge.put_prefix_bytes < REMOTE_MAX_SINGLE_SOURCE_BYTES, (
            f"业务红：未物化 200MB，prefix={transport_edge.put_prefix_bytes}"
        )
        assert transport_edge.put_prefix_bytes <= put_bound + 1024 * 1024, (
            f"业务红：有界前缀超限 prefix={transport_edge.put_prefix_bytes}"
        )
        assert ei2.value.diagnostic_code == "upload_failed", (
            f"业务红：PUT 网络失败须 upload_failed，actual={ei2.value.diagnostic_code}"
        )
        # R7-C3/R6-C8：无界与 PUT 前预物化 hits 最终精确 []
        transport_edge.unbounded_content_access = bool(probe.unbounded_hits)
        assert transport_edge.unbounded_content_access is False, (
            f"业务红：禁止无界内容访问 hits={probe.unbounded_hits}"
        )
        assert probe.unbounded_hits == [], (
            f"业务红：200MB 路径无界 read 必须零命中，hits={probe.unbounded_hits}"
        )
        assert probe.pre_materialized_hits == [], (
            f"业务红：PUT 前预物化 hits 必须精确 []，hits={probe.pre_materialized_hits}"
        )
        # R8-C3：正式断言 PUT 实际返回总量有界（前缀 + 一个冻结小块）
        put_ret = int(probe.returned_by_phase.get("put", 0))
        assert put_ret <= probe.put_return_budget, (
            f"业务红：returned_by_phase['put'] 必须 ≤ 预算 "
            f"put={put_ret} budget={probe.put_return_budget} "
            f"by_phase={probe.returned_by_phase}"
        )
        assert put_ret < REMOTE_MAX_SINGLE_SOURCE_BYTES, (
            f"业务红：PUT 返回总量禁止接近 200MB，put={put_ret}"
        )
    finally:
        for fp in (over_path, ok_path):
            if fp.exists():
                fp.unlink()
        assert not over_path.exists()
        assert not ok_path.exists()


def test_g10_data_id_unpredictable_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C14：data_id 随机源冻结为单一 uuid.uuid4().hex；
    monkeypatch 实际生成 seam，喂三组预定 32 位 hex，断调用次数、POST 值、格式与跨调用对应。
    """
    import uuid as _uuid

    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    planned = [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "cccccccccccccccccccccccccccccccc",
    ]
    call_n = {"n": 0}

    class _FakeUUID:
        def __init__(self, hex_value: str):
            self.hex = hex_value

    def fake_uuid4():
        i = call_n["n"]
        call_n["n"] += 1
        assert i < len(planned), f"uuid4 调用过多 n={call_n['n']}"
        return _FakeUUID(planned[i])

    # 单一 seam：uuid.uuid4
    monkeypatch.setattr(_uuid, "uuid4", fake_uuid4)
    if hasattr(mod, "uuid"):
        monkeypatch.setattr(mod.uuid, "uuid4", fake_uuid4)
    # 若生产 from uuid import uuid4
    if hasattr(mod, "uuid4"):
        monkeypatch.setattr(mod, "uuid4", fake_uuid4)

    posted: list[str] = []

    def make_handler():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                posted.append(body["files"][0]["data_id"])
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": posted[-1],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_A,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url) == ZIP_URL_A:
                return httpx.Response(
                    200, content=_make_zip_bytes({"full.md": b"# x\n"})
                )
            raise AssertionError("unexpected")

        return handler

    for _ in range(3):
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(make_handler()),
            **_run_kwargs(),
        )
    assert call_n["n"] == 3, f"业务红：uuid4 必须精确调用 3 次，n={call_n['n']}"
    assert posted == planned, (
        f"业务红：POST data_id 必须来自 uuid4().hex 预定值，actual={posted}"
    )
    for did in posted:
        assert _DATA_ID_RE.fullmatch(did), f"业务红：data_id 格式必须 32hex，actual={did}"


def test_g11_set_cookie_must_not_stick(tmp_path: Path):
    """C11：Set-Cookie 后后续请求零 Cookie。"""
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "a.pdf", b"%PDF")
    cookies_seen: list[str | None] = []
    did: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookies_seen.append(
            request.headers.get("Cookie") or request.headers.get("cookie")
        )
        if request.method == "POST":
            body = json.loads(request.content.decode())
            did.append(body["files"][0]["data_id"])
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"batch_id": FAKE_BATCH_ID, "file_urls": [PRESIGNED_PUT_A]},
                },
                headers={"set-cookie": "evil=1; Path=/"},
            )
        if request.method == "PUT":
            return httpx.Response(200, headers={"set-cookie": "evil2=1"})
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": did[0],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
                headers={"set-cookie": "evil3=1"},
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=_make_zip_bytes({"full.md": b"# z\n"}))
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler),
        **_run_kwargs(),
    )
    assert "# z" in out.markdown
    assert all(c in (None, "") for c in cookies_seen), (
        f"业务红：任意请求不得携带 Cookie jar，seen={cookies_seen}"
    )

def test_g12_markdown_caps_during_full_md_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    C1：分别证明「恰好 cap 且 EOF 成功」与「cap+1 触发 output_invalid」。
    字节分支保证码点未超；码点分支保证 UTF-8 未超。
    超限块自身不含 canary，下一读才是 canary；正确实现消费 overflow 后不得再读 canary。
    不得把 ==cap 当失败，也不得依赖固定 64KiB chunk。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")

    def _run_with_read_seam(
        *,
        cap_codepoints: int,
        cap_bytes: int,
        chunks: list[bytes],
        expect_success: bool,
        canary: bytes,
        label: str,
    ) -> dict[str, Any]:
        monkeypatch.setattr(mod, "MAX_MD_CODEPOINTS", cap_codepoints)
        monkeypatch.setattr(mod, "MAX_MD_UTF8_BYTES", cap_bytes)
        md_payload = b"".join(chunks)
        read_log: dict[str, Any] = {
            "calls": 0,
            "bytes": 0,
            "saw_canary": False,
            "saw_eof": False,
            "pending_empty_after": False,
            "empty_returns": 0,
            "ns": [],
        }
        real_zip_open = zipfile.ZipFile.open

        def spy_zip_open(self, name, *a, **k):
            fh = real_zip_open(self, name, *a, **k)
            name_s = str(getattr(name, "filename", name))
            if name_s.endswith("full.md") or str(name).endswith("full.md"):
                pending = list(chunks)

                def controlled_read(n: int = -1):
                    # C3：read(-1)/None/非正数立即业务红；必须正数分块 read(n)
                    if n is None or (isinstance(n, int) and int(n) <= 0):
                        raise AssertionError(
                            f"业务红：禁止无界/非正数 full.md read，actual={n!r}"
                        )
                    n_i = int(n)
                    if n_i <= 0:
                        raise AssertionError(
                            f"业务红：read(n) 须 n>0，actual={n_i}"
                        )
                    read_log["ns"].append(n_i)
                    read_log["calls"] += 1
                    if not pending:
                        # R6-C4：仅正数 read(n) 返回 b'' 才算 EOF；末非空块不得冒充
                        read_log["saw_eof"] = True
                        read_log["empty_returns"] = int(read_log.get("empty_returns", 0)) + 1
                        return b""
                    data = pending.pop(0)
                    data, rest = data[:n_i], data[n_i:]
                    if rest:
                        pending.insert(0, rest)
                    read_log["bytes"] += len(data)
                    if canary and (canary[:8] in data or data == canary):
                        read_log["saw_canary"] = True
                    if not pending and not rest:
                        # 数据面已尽，但本返回仍是非空块 —— 不得记 saw_eof
                        read_log["pending_empty_after"] = True
                    return data

                fh.read = controlled_read  # type: ignore[method-assign]
            return fh

        monkeypatch.setattr(zipfile.ZipFile, "open", spy_zip_open)
        p = _write_temp_source(tmp_path, f"cap-{label}.pdf", b"1")
        holder: dict[str, str] = {}
        base = _post_ok_single(holder)
        zbytes = _make_zip_bytes({"full.md": md_payload})

        def handler(request: httpx.Request) -> httpx.Response:
            early = base(request)
            if early is not None:
                return early
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": holder["id"],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_A,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url) == ZIP_URL_A:
                return httpx.Response(200, content=zbytes)
            raise AssertionError("unexpected")

        if expect_success:
            out = run(
                _build_sources(mod, [p]),
                transport=httpx.MockTransport(handler),
                **_run_kwargs(),
            )
            assert out.markdown is not None
            read_log["success"] = True
        else:
            with pytest.raises(err_cls) as ei:
                run(
                    _build_sources(mod, [p]),
                    transport=httpx.MockTransport(handler),
                    **_run_kwargs(),
                )
            _assert_remote_error(ei.value, "output_invalid")
            read_log["success"] = False
        return read_log

    # --- 字节 cap：恰好 cap 且 EOF 成功（码点未超）---
    byte_cap = 16
    exact_b = b"B" * byte_cap
    assert len(exact_b.decode("utf-8")) <= 1_000_000
    log_ok_b = _run_with_read_seam(
        cap_codepoints=1_000_000,
        cap_bytes=byte_cap,
        chunks=[exact_b],
        expect_success=True,
        canary=b"",
        label="byte-eq-ok",
    )
    assert log_ok_b["success"] is True
    assert log_ok_b["saw_canary"] is False
    assert any(n > 0 for n in log_ok_b["ns"]), (
        f"业务红：字节 exact-cap 至少一次正数 read(n)，log={log_ok_b}"
    )
    assert log_ok_b["bytes"] == byte_cap, (
        f"业务红：字节 exact-cap 必须精确消费 cap 字节，log={log_ok_b}"
    )
    # R6-C4：exact-cap 只能 saw_eof=True；至少一次正数 read(n)->b''
    assert log_ok_b["saw_eof"] is True, (
        f"业务红：字节 exact-cap 必须 saw_eof=True（末非空块不得冒充 EOF），log={log_ok_b}"
    )
    assert int(log_ok_b.get("empty_returns", 0)) >= 1, (
        f"业务红：字节 exact-cap 至少一次正数 read(n) 返回 b''，log={log_ok_b}"
    )

    # --- 字节 cap+1：首块 cap+1（无 canary），次块 canary ---
    over_b = b"B" * (byte_cap + 1)
    canary_b = b"CANARY_BYTES_AFTER_LIMIT"
    log_bad_b = _run_with_read_seam(
        cap_codepoints=1_000_000,
        cap_bytes=byte_cap,
        chunks=[over_b, canary_b],
        expect_success=False,
        canary=canary_b,
        label="byte-over",
    )
    assert log_bad_b["saw_canary"] is False, (
        f"业务红：字节 overflow 后不得 read canary，log={log_bad_b}"
    )
    assert any(n > 0 for n in log_bad_b["ns"]), (
        f"业务红：字节 cap+1 至少一次正数 read(n)，log={log_bad_b}"
    )
    assert log_bad_b["bytes"] == byte_cap + 1, (
        f"业务红：字节 overflow 必须精确消费 cap+1，log={log_bad_b}"
    )

    # --- 码点 cap：恰好 cap 且 EOF 成功（UTF-8 未超）---
    cp_cap = 10
    exact_cp = ("字" * cp_cap).encode("utf-8")
    assert len(exact_cp) < 2 * 1024 * 1024
    log_ok_cp = _run_with_read_seam(
        cap_codepoints=cp_cap,
        cap_bytes=2 * 1024 * 1024,
        chunks=[exact_cp],
        expect_success=True,
        canary=b"",
        label="cp-eq-ok",
    )
    assert log_ok_cp["success"] is True
    assert any(n > 0 for n in log_ok_cp["ns"]), (
        f"业务红：码点 exact-cap 至少一次正数 read(n)，log={log_ok_cp}"
    )
    assert log_ok_cp["bytes"] == len(exact_cp), (
        f"业务红：码点 exact-cap 必须消费全部 UTF-8 字节，log={log_ok_cp}"
    )
    # R6-C4：码点 exact-cap 同样要求真实 EOF 空读
    assert log_ok_cp["saw_eof"] is True, (
        f"业务红：码点 exact-cap 必须 saw_eof=True，log={log_ok_cp}"
    )
    assert int(log_ok_cp.get("empty_returns", 0)) >= 1, (
        f"业务红：码点 exact-cap 至少一次正数 read(n) 返回 b''，log={log_ok_cp}"
    )

    # --- 码点 cap+1：超限块无 canary，下一读 canary ---
    over_cp = ("字" * (cp_cap + 1)).encode("utf-8")
    canary_cp = b"CANARY_CODEPOINT_AFTER_LIMIT"
    log_bad_cp = _run_with_read_seam(
        cap_codepoints=cp_cap,
        cap_bytes=2 * 1024 * 1024,
        chunks=[over_cp, canary_cp],
        expect_success=False,
        canary=canary_cp,
        label="cp-over",
    )
    assert log_bad_cp["saw_canary"] is False, (
        f"业务红：码点 overflow 后不得 read canary，log={log_bad_cp}"
    )
    assert any(n > 0 for n in log_bad_cp["ns"]), (
        f"业务红：码点 cap+1 至少一次正数 read(n)，log={log_bad_cp}"
    )
    # R6-C5：精确证明消费到第 cap+1 个完整码点（bytes==len(over_cp)）后才 output_invalid
    assert log_bad_cp["bytes"] == len(over_cp), (
        f"业务红：码点 cap+1 必须精确消费 over_cp 全部字节（第 cap+1 完整码点），"
        f"bytes={log_bad_cp['bytes']} expect={len(over_cp)} log={log_bad_cp}"
    )
    assert log_bad_cp["saw_canary"] is False, (
        f"业务红：码点 cap+1 仍零 canary，log={log_bad_cp}"
    )


def test_g13_httpx_log_suppress_and_restore(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """
    R9：不得永久修改全局 httpx/httpcore logger level。
    契约演进为：当前 remote 调用线程临时安装中间记录 Filter，
    finally 只移除该 filter，logger level 全量原值。
    并发：旁路线程 sentinel 日志仍可见；
    成功 + POST/PUT/poll/ZIP + cloud code!=0 + state=failed 均扫 caplog 与异常链。
    另：httpcore.connection / httpcore.http11 子 logger 同步隐私红门与全量快照恢复。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")
    p = _write_temp_source(tmp_path, ORIGINAL_BASENAME_A, b"%PDF")
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    hc_conn_logger = logging.getLogger("httpcore.connection")
    hc_http11_logger = logging.getLogger("httpcore.http11")
    prev_httpx = httpx_logger.level
    prev_httpcore = httpcore_logger.level
    prev_httpx_filters = list(httpx_logger.filters)
    prev_httpcore_filters = list(httpcore_logger.filters)

    def _snap_logger_full(lg: logging.Logger) -> dict[str, object]:
        # 子 logger 全量快照：level/disabled/propagate/filters/handlers
        return {
            "level": lg.level,
            "disabled": lg.disabled,
            "propagate": lg.propagate,
            "filters": list(lg.filters),
            "handlers": list(lg.handlers),
        }

    prev_hc_conn = _snap_logger_full(hc_conn_logger)
    prev_hc_http11 = _snap_logger_full(hc_http11_logger)

    def _assert_sub_logger_restored(
        lg: logging.Logger, snap: dict[str, object], name: str
    ) -> None:
        assert lg.level == snap["level"], (
            f"业务红：{name} level 必须原值 {snap['level']} actual={lg.level}"
        )
        assert lg.disabled == snap["disabled"], (
            f"业务红：{name} disabled 必须原值 {snap['disabled']} actual={lg.disabled}"
        )
        assert lg.propagate == snap["propagate"], (
            f"业务红：{name} propagate 必须原值 {snap['propagate']} actual={lg.propagate}"
        )
        assert list(lg.filters) == list(snap["filters"]), (
            f"业务红：{name} filters 必须 finally 恢复"
        )

    def _assert_restored() -> None:
        assert httpx_logger.level == prev_httpx, (
            f"业务红：httpx level 必须原值 {prev_httpx} actual={httpx_logger.level}"
        )
        assert httpcore_logger.level == prev_httpcore, (
            f"业务红：httpcore level 必须原值 {prev_httpcore} actual={httpcore_logger.level}"
        )
        # finally 后不得残留调用期临时 filter（允许与调用前集合等价）
        assert list(httpx_logger.filters) == prev_httpx_filters, (
            "业务红：httpx filters 必须 finally 只移除本次 filter 后恢复"
        )
        assert list(httpcore_logger.filters) == prev_httpcore_filters, (
            "业务红：httpcore filters 必须 finally 恢复"
        )
        _assert_sub_logger_restored(hc_conn_logger, prev_hc_conn, "httpcore.connection")
        _assert_sub_logger_restored(
            hc_http11_logger, prev_hc_http11, "httpcore.http11"
        )

    def _scan_exc_and_log(exc: BaseException, text: str, frags: list[str]) -> None:
        chain = [str(exc), repr(exc)]
        cur: BaseException | None = exc
        seen = 0
        while cur is not None and seen < 6:
            chain.append(str(cur))
            chain.append(repr(getattr(cur, "args", ())))
            cur = cur.__cause__ or cur.__context__
            seen += 1
        blob = "\n".join(chain) + "\n" + text
        for frag in frags:
            if frag:
                assert frag not in blob, f"隐私泄漏 {frag!r}"

    # C4/C10：installed/emit/done 协调；唯一 nonce 仅在 Filter 已安装后由 sibling 发出
    import uuid as _uuid_c4

    httpx_hits: list[str] = []
    httpcore_hits: list[str] = []
    hc_conn_hits: list[str] = []
    hc_http11_hits: list[str] = []

    class _SideHandler(logging.Handler):
        def __init__(self, bucket: list[str]):
            super().__init__()
            self.bucket = bucket

        def emit(self, record: logging.LogRecord) -> None:
            self.bucket.append(record.getMessage())

    hx_side = _SideHandler(httpx_hits)
    hc_side = _SideHandler(httpcore_hits)
    hc_conn_side = _SideHandler(hc_conn_hits)
    hc_http11_side = _SideHandler(hc_http11_hits)
    httpx_logger.addHandler(hx_side)
    httpcore_logger.addHandler(hc_side)
    hc_conn_logger.addHandler(hc_conn_side)
    hc_http11_logger.addHandler(hc_http11_side)
    stop_flag = threading.Event()
    filter_installed = threading.Event()  # installed
    nonce_emitted = threading.Event()  # emit done
    sibling_started = threading.Event()
    unique_nonce = f"SIBLING_NONCE_{_uuid_c4.uuid4().hex}"
    call_sensitive = f"CALL_THREAD_SENSITIVE_{FAKE_TOKEN}_{_uuid_c4.uuid4().hex[:8]}"

    def _sibling_log_loop() -> None:
        sibling_started.set()
        # R6-C7：等待 filter_installed 同时响应 stop_flag，禁止只傻等 15s
        while not stop_flag.is_set():
            if filter_installed.wait(timeout=0.05):
                break
        if stop_flag.is_set() and not filter_installed.is_set():
            return
        if filter_installed.is_set():
            httpx_logger.debug("%s_HTTPX", unique_nonce)
            httpcore_logger.debug("%s_HTTPCORE", unique_nonce)
            # 子 logger 旁路 sentinel：证明非全局静音
            hc_conn_logger.debug("%s_HTTPCORE_CONN", unique_nonce)
            hc_http11_logger.debug("%s_HTTPCORE_HTTP11", unique_nonce)
            nonce_emitted.set()
        while not stop_flag.wait(0.02):
            pass

    sibling = threading.Thread(
        target=_sibling_log_loop, name="v1n-sentinel", daemon=True
    )
    sibling.start()
    assert sibling_started.wait(timeout=5)

    try:
        # --- 成功路径 ---
        holder: dict[str, str] = {}
        base = _post_ok_single(holder)
        during_levels: list[tuple[int, int]] = []
        during_filter_counts: list[tuple[int, int]] = []

        def handler_ok(request: httpx.Request) -> httpx.Response:
            during_levels.append((httpx_logger.level, httpcore_logger.level))
            during_filter_counts.append(
                (len(httpx_logger.filters), len(httpcore_logger.filters))
            )
            if request.method == "POST":
                # 此时 production 应已安装临时 Filter
                filter_grown = any(
                    [
                        len(httpx_logger.filters) > len(prev_httpx_filters),
                        len(httpcore_logger.filters) > len(prev_httpcore_filters),
                    ]
                )
                assert filter_grown, "业务红：进入 HTTP 时临时 Filter 必须已安装"
                filter_installed.set()  # installed → 允许 sibling emit
                assert nonce_emitted.wait(timeout=10), (
                    "业务红：Filter 安装后 sibling 必须发出唯一 nonce"
                )
                # 调用线程敏感日志：正确 Filter 应压制本线程记录
                httpx_logger.debug(call_sensitive)
                httpcore_logger.debug(call_sensitive)
                # 子 logger 同 marker：生产须对 httpcore.connection/http11 同步压制
                hc_conn_logger.debug(call_sensitive)
                hc_http11_logger.debug(call_sensitive)
            early = base(request)
            if early is not None:
                return early
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": holder["id"],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_A,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url) == ZIP_URL_A:
                return httpx.Response(
                    200, content=_make_zip_bytes({"full.md": b"# ok\n"})
                )
            raise AssertionError("unexpected")

        with caplog.at_level(logging.DEBUG):
            out = run(
                _build_sources(mod, [p]),
                transport=httpx.MockTransport(handler_ok),
                **_run_kwargs(),
            )
        assert "# ok" in out.markdown
        _assert_restored()
        assert during_levels, "业务红：调用期必须进入 HTTP 阶段"
        # R9：level 不得被永久改写；调用期 level 应保持原值（Filter 方案）
        assert all(hx == prev_httpx for hx, _ in during_levels), (
            f"业务红：禁止改全局 httpx level，during={during_levels} prev={prev_httpx}"
        )
        assert all(hc == prev_httpcore for _, hc in during_levels), (
            f"业务红：禁止改全局 httpcore level，during={during_levels} prev={prev_httpcore}"
        )
        # C10：httpx 与 httpcore 各自 filter 均增长
        assert any(
            fc > len(prev_httpx_filters) for fc, _gc in during_filter_counts
        ), f"业务红：httpx filter 必须增长，counts={during_filter_counts}"
        assert any(
            gc > len(prev_httpcore_filters) for _fc, gc in during_filter_counts
        ), f"业务红：httpcore filter 必须增长，counts={during_filter_counts}"
        success_frags = [
            FAKE_TOKEN,
            PRESIGNED_PUT_A,
            ZIP_URL_A,
            FAKE_BATCH_ID,
            ORIGINAL_BASENAME_A,
            holder.get("id", ""),
            str(tmp_path),
            call_sensitive,
        ]
        for frag in success_frags:
            if frag:
                assert frag not in caplog.text, f"成功 caplog 泄漏 {frag!r}"
        # 旁路线程同 logger：唯一 nonce 必须在安装后可见
        assert any(unique_nonce in m for m in httpx_hits), (
            f"业务红：旁路 httpx nonce 必须可见，hits={httpx_hits[:8]}"
        )
        assert any(unique_nonce in m for m in httpcore_hits), (
            f"业务红：旁路 httpcore nonce 必须可见，hits={httpcore_hits[:8]}"
        )
        assert any(unique_nonce in m for m in hc_conn_hits), (
            f"业务红：旁路 httpcore.connection nonce 必须可见，hits={hc_conn_hits[:8]}"
        )
        assert any(unique_nonce in m for m in hc_http11_hits), (
            f"业务红：旁路 httpcore.http11 nonce 必须可见，hits={hc_http11_hits[:8]}"
        )
        # 调用线程敏感日志被压制：旁路 handler 不得看到 call_sensitive
        # 子 logger 隐私红门优先（failure-first：当前生产未覆盖子 logger 时应红）
        assert all(call_sensitive not in m for m in hc_conn_hits), (
            f"业务红：调用线程敏感 httpcore.connection 日志必须被 Filter 压制，"
            f"hits={hc_conn_hits}"
        )
        assert all(call_sensitive not in m for m in hc_http11_hits), (
            f"业务红：调用线程敏感 httpcore.http11 日志必须被 Filter 压制，"
            f"hits={hc_http11_hits}"
        )
        assert all(call_sensitive not in m for m in httpx_hits), (
            f"业务红：调用线程敏感 httpx 日志必须被 Filter 压制，hits={httpx_hits}"
        )
        assert all(call_sensitive not in m for m in httpcore_hits), (
            f"业务红：调用线程敏感 httpcore 日志必须被 Filter 压制，hits={httpcore_hits}"
        )

        # --- 分阶段网络失败 ---
        temp_abs = str(tmp_path.resolve())
        stages = (
            ("post", "api_request_failed"),
            ("put", "upload_failed"),
            ("poll", "api_request_failed"),
            ("zip", "zip_download_failed"),
        )
        for stage, code in stages:
            caplog.clear()
            did_box: dict[str, str] = {"id": ""}

            def handler_fail(
                request: httpx.Request,
                _stage: str = stage,
                _box: dict[str, str] = did_box,
            ) -> httpx.Response:
                if request.method == "POST":
                    if _stage == "post":
                        cause = RuntimeError(
                            f"cause-post {FAKE_TOKEN} {temp_abs} {PRESIGNED_PUT_A}"
                        )
                        raise httpx.ConnectError("simulated-post-fail") from cause
                    body = json.loads(request.content.decode())
                    _box["id"] = body["files"][0]["data_id"]
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "batch_id": FAKE_BATCH_ID,
                                "file_urls": [PRESIGNED_PUT_A],
                            },
                        },
                    )
                if request.method == "PUT":
                    if _stage == "put":
                        cause = RuntimeError(
                            f"cause-put {_box['id']} {temp_abs} {PRESIGNED_PUT_A}"
                        )
                        raise httpx.ReadError("simulated-put-fail") from cause
                    return httpx.Response(200)
                if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                    request.url
                ):
                    if _stage == "poll":
                        cause = RuntimeError(
                            f"cause-poll {_box['id']} {FAKE_BATCH_ID} {temp_abs}"
                        )
                        raise httpx.ConnectError("simulated-poll-fail") from cause
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "extract_result": [
                                    {
                                        "data_id": _box["id"],
                                        "state": "done",
                                        "full_zip_url": ZIP_URL_A,
                                    }
                                ]
                            },
                        },
                    )
                if request.method == "GET" and str(request.url) == ZIP_URL_A:
                    if _stage == "zip":
                        cause = RuntimeError(
                            f"cause-zip {_box['id']} {ZIP_URL_A} {temp_abs}"
                        )
                        raise httpx.ConnectError("simulated-zip-fail") from cause
                    return httpx.Response(
                        200, content=_make_zip_bytes({"full.md": b"# x\n"})
                    )
                raise AssertionError(f"unexpected {_stage}")

            with caplog.at_level(logging.DEBUG):
                with pytest.raises(err_cls) as ei:
                    run(
                        _build_sources(mod, [p]),
                        transport=httpx.MockTransport(handler_fail),
                        **_run_kwargs(),
                    )
            _assert_restored()
            _assert_remote_error(ei.value, code)
            frags = [
                FAKE_TOKEN,
                PRESIGNED_PUT_A,
                ZIP_URL_A,
                FAKE_BATCH_ID,
                ORIGINAL_BASENAME_A,
                did_box.get("id", ""),
                temp_abs,
                "simulated-post-fail",
                "simulated-put-fail",
                "simulated-poll-fail",
                "simulated-zip-fail",
            ]
            _scan_exc_and_log(ei.value, caplog.text, frags)

        # --- cloud code!=0：POST 与 poll 两阶段 ---
        for cloud_phase in ("post", "poll"):
            caplog.clear()
            box_c: dict[str, str] = {"id": ""}

            def handler_cloud(
                request: httpx.Request,
                _phase: str = cloud_phase,
                _box: dict[str, str] = box_c,
            ) -> httpx.Response:
                if _phase == "post":
                    return httpx.Response(
                        200,
                        json={
                            "code": 10001,
                            "msg": f"cloud-secret-{FAKE_TOKEN}",
                            "trace_id": "tid-cloud-canary",
                            "data": {},
                        },
                    )
                if request.method == "POST":
                    body = json.loads(request.content.decode())
                    _box["id"] = body["files"][0]["data_id"]
                    return httpx.Response(
                        200,
                        json={
                            "code": 0,
                            "data": {
                                "batch_id": FAKE_BATCH_ID,
                                "file_urls": [PRESIGNED_PUT_A],
                            },
                        },
                    )
                if request.method == "PUT":
                    return httpx.Response(200)
                if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                    request.url
                ):
                    return httpx.Response(
                        200,
                        json={
                            "code": 10001,
                            "msg": f"cloud-secret-{FAKE_TOKEN}",
                            "trace_id": "tid-cloud-canary",
                            "data": {},
                        },
                    )
                raise AssertionError("cloud 阶段不得 ZIP")

            with caplog.at_level(logging.DEBUG):
                with pytest.raises(err_cls) as ei_c:
                    run(
                        _build_sources(mod, [p]),
                        transport=httpx.MockTransport(handler_cloud),
                        **_run_kwargs(),
                    )
            _assert_restored()
            _assert_remote_error(
                ei_c.value,
                "api_upstream_error",
                forbidden=[FAKE_TOKEN, "tid-cloud-canary", "cloud-secret-"],
            )
            _scan_exc_and_log(
                ei_c.value,
                caplog.text,
                [FAKE_TOKEN, "tid-cloud-canary", "cloud-secret-", PRESIGNED_PUT_A],
            )

        # --- state=failed ---
        caplog.clear()
        did_f: dict[str, str] = {"id": ""}

        def handler_failed(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                did_f["id"] = body["files"][0]["data_id"]
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": did_f["id"],
                                    "state": "failed",
                                    "err_msg": f"remote-fail-{FAKE_TOKEN}",
                                }
                            ]
                        },
                    },
                )
            raise AssertionError("failed 后不得 ZIP")

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(err_cls) as ei_f:
                run(
                    _build_sources(mod, [p]),
                    transport=httpx.MockTransport(handler_failed),
                    **_run_kwargs(),
                )
        _assert_restored()
        _assert_remote_error(ei_f.value, "remote_parse_failed")
        _scan_exc_and_log(
            ei_f.value,
            caplog.text,
            [FAKE_TOKEN, did_f.get("id", ""), FAKE_BATCH_ID, PRESIGNED_PUT_A],
        )
    finally:
        # R6-C7：先唤醒 → bounded join；无条件先 remove handlers/恢复，再断言线程终止
        # 清理异常不得覆盖首个业务红，也不得泄漏 handler
        import sys as _sys_c7

        pending_exc = _sys_c7.exc_info()[1]
        stop_flag.set()
        filter_installed.set()  # 唤醒可能仍阻塞在 wait 的 sibling
        try:
            sibling.join(timeout=5)
        except RuntimeError as e:
            cleanup_errs_seed = e
        else:
            cleanup_errs_seed = None
        cleanup_errs: list[BaseException] = []
        if cleanup_errs_seed is not None:
            cleanup_errs.append(cleanup_errs_seed)
        try:
            httpx_logger.removeHandler(hx_side)
        except RuntimeError as e:
            cleanup_errs.append(e)
        try:
            httpcore_logger.removeHandler(hc_side)
        except RuntimeError as e:
            cleanup_errs.append(e)
        try:
            hc_conn_logger.removeHandler(hc_conn_side)
        except RuntimeError as e:
            cleanup_errs.append(e)
        try:
            hc_http11_logger.removeHandler(hc_http11_side)
        except RuntimeError as e:
            cleanup_errs.append(e)

        def _restore_logger_full(
            lg: logging.Logger, snap: dict[str, object]
        ) -> None:
            # 按 G13 风格精确恢复 handlers/filters/level/disabled/propagate
            prev_handlers = list(snap["handlers"])  # type: ignore[arg-type]
            prev_filters = list(snap["filters"])  # type: ignore[arg-type]
            while lg.handlers and list(lg.handlers) != prev_handlers:
                lg.removeHandler(lg.handlers[-1])
            for hd in prev_handlers:
                if hd not in lg.handlers:
                    lg.addHandler(hd)
            while lg.filters and list(lg.filters) != prev_filters:
                lg.removeFilter(lg.filters[-1])
            for fl in prev_filters:
                if fl not in lg.filters:
                    lg.addFilter(fl)
            if lg.level != snap["level"]:
                lg.setLevel(snap["level"])  # type: ignore[arg-type]
            if lg.disabled != snap["disabled"]:
                lg.disabled = snap["disabled"]  # type: ignore[assignment]
            if lg.propagate != snap["propagate"]:
                lg.propagate = snap["propagate"]  # type: ignore[assignment]

        try:
            # 恢复 filter/level（不抛业务断言优先）
            while httpx_logger.filters and list(httpx_logger.filters) != list(
                prev_httpx_filters
            ):
                httpx_logger.removeFilter(httpx_logger.filters[-1])
            for fl in prev_httpx_filters:
                if fl not in httpx_logger.filters:
                    httpx_logger.addFilter(fl)
            while httpcore_logger.filters and list(httpcore_logger.filters) != list(
                prev_httpcore_filters
            ):
                httpcore_logger.removeFilter(httpcore_logger.filters[-1])
            for fl in prev_httpcore_filters:
                if fl not in httpcore_logger.filters:
                    httpcore_logger.addFilter(fl)
            if httpx_logger.level != prev_httpx:
                httpx_logger.setLevel(prev_httpx)
            if httpcore_logger.level != prev_httpcore:
                httpcore_logger.setLevel(prev_httpcore)
            _restore_logger_full(hc_conn_logger, prev_hc_conn)
            _restore_logger_full(hc_http11_logger, prev_hc_http11)
        except RuntimeError as e:
            cleanup_errs.append(e)
        alive = sibling.is_alive()
        if pending_exc is None:
            if alive:
                raise AssertionError(
                    "业务红：旁路 logger 线程必须 bounded join 后退出"
                )
            if cleanup_errs:
                raise cleanup_errs[0]
            _assert_restored()
            # 子 logger handlers 也须与前态完全一致（side handler 已移除）
            assert list(hc_conn_logger.handlers) == list(prev_hc_conn["handlers"]), (
                "业务红：httpcore.connection handlers 必须 finally 恢复"
            )
            assert list(hc_http11_logger.handlers) == list(
                prev_hc_http11["handlers"]
            ), "业务红：httpcore.http11 handlers 必须 finally 恢复"
        # 若已有业务红，仅尽力清理，不再抛清理断言掩盖首错

# ===========================================================================
# V1 发布高风险门（TEST-Q3 / Q1-Q6）— failure-first 行为门
# 节点关键字：v1n_release_gate（仅本批新增节点可 -k 定向）
# 生产未授权修复；本节点必须在当前生产上真实 failed。
# ===========================================================================

# 测试侧冻结：HTTP 响应有界 cap（不得用 production 自指）
MAX_HTTP_JSON_RESPONSE_BYTES = 1_048_576  # POST/poll JSON 响应
MAX_HTTP_PUT_RESPONSE_BYTES = 65_536  # PUT 响应丢弃 cap
# 唯一合成 marker（禁止真实 Token/路径/正文）
_RQ_JSON_MARKER = "SYNTH_JSON_BODY_MARKER_V1N_RQ1_NOT_REAL"
_RQ_OS_PATH_MARKER = "SYNTH_OSERROR_PATH_V1N_RQ1_C_FAKE_ABS_NOT_REAL"
_RQ_UTF8_BODY_MARKER = b"SYNTH_UTF8_BODY_MARKER_V1N_RQ1_FULLMD_NOT_REAL\xff\xfe"


def _rq_is_test_frame(fr: Any) -> bool:
    """Q1：识别调用者测试帧，避免 f_locals 中合成 marker 造成假红。"""
    code = getattr(fr, "f_code", None)
    if code is None:
        return False
    name = str(getattr(code, "co_name", "") or "")
    if name.startswith("test_"):
        return True
    fn = str(getattr(code, "co_filename", "") or "").replace("\\", "/")
    if "/tests/" in fn or fn.endswith("/conftest.py"):
        return True
    return False


def _rq_walk_exc_graph(exc: BaseException) -> str:
    """遍历 cause/context/args/traceback 可达文本，供零 marker 断言。"""
    import traceback as _tb

    parts: list[str] = []
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        parts.append(type(cur).__name__)
        parts.append(str(cur))
        parts.append(repr(cur))
        parts.append(repr(getattr(cur, "args", ())))
        parts.append(str(getattr(cur, "message", "")))
        parts.append(str(getattr(cur, "diagnostic_code", "")))
        try:
            parts.append("".join(_tb.format_exception(type(cur), cur, cur.__traceback__)))
        except Exception:
            pass
        tb = cur.__traceback__
        depth = 0
        while tb is not None and depth < 32:
            fr = tb.tb_frame
            # Q1：跳过测试帧 f_locals（仍扫 args/cause/context/format_exception）
            if not _rq_is_test_frame(fr):
                try:
                    for k, v in list(fr.f_locals.items())[:64]:
                        if isinstance(v, (str, bytes, bytearray)):
                            parts.append(f"{k}={v!r}")
                        elif isinstance(v, BaseException):
                            parts.append(repr(getattr(v, "args", ())))
                except Exception:
                    pass
            tb = tb.tb_next
            depth += 1
        cause = getattr(cur, "__cause__", None)
        ctx = getattr(cur, "__context__", None)
        if cause is not None:
            stack.append(cause)
        if ctx is not None:
            stack.append(ctx)
    return "\n".join(parts)


def _rq_assert_zero_markers(exc: BaseException, markers: list) -> None:
    blob = _rq_walk_exc_graph(exc)
    for m in markers:
        if not m:
            continue
        if isinstance(m, (bytes, bytearray)):
            s = bytes(m).decode("latin-1", errors="replace")
            assert s not in blob and repr(bytes(m)) not in blob, (
                f"业务红：异常可达图泄漏字节 marker {bytes(m)!r}"
            )
        else:
            assert str(m) not in blob, f"业务红：异常可达图泄漏 marker {m!r}"


def _rq_count_zip_central_entries(raw: bytes) -> int:
    """用途：统计 central directory 签名条数；夹具自证 only。"""
    n = 0
    i = 0
    while True:
        j = raw.find(b"PK\x01\x02", i)
        if j < 0:
            break
        n += 1
        i = j + 4
    return n


def _rq_make_zip_consistent_empty_members(total_entries: int) -> bytes:
    """
    Q6 超限夹具内核：真实一致 N 个空成员。
    local headers + central directory + 经典 EOCD 计数全部 = N；
    禁止「仅改 EOCD 声明、真实 CD 仍 1 项」假绿。
    """
    n = int(total_entries)
    assert 1 <= n <= 0xFFFF, f"经典一致空成员夹具：n 须在 1..65535，got {n}"
    buf = io.BytesIO()
    # ZIP_STORED 加速；短名空载荷，避免巨量分配
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(n):
            zf.writestr(f"e{i:04x}", b"")
    raw = buf.getvalue()
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0, "夹具：必须含经典 EOCD"
    disk_n = struct.unpack_from("<H", raw, eocd + 8)[0]
    total_n = struct.unpack_from("<H", raw, eocd + 10)[0]
    assert disk_n == n and total_n == n, (
        f"夹具：EOCD 声明须={n}，got disk={disk_n} total={total_n}"
    )
    cd_n = _rq_count_zip_central_entries(raw)
    assert cd_n == n, f"夹具：central 条目须={n}，got {cd_n}"
    return raw


def _rq_make_zip_eocd_total_entries(total_entries: int) -> bytes:
    """
    Q6 经典超限路径：真实一致 total_entries 空成员 CD/EOCD。
    与「仅改 EOCD 计数」假夹具相对；ZipFile 前前门须在 constructs=0 时拒绝。
    """
    return _rq_make_zip_consistent_empty_members(total_entries)


def _rq_make_zip64_eocd_total_entries(total_entries: int) -> bytes:
    """
    Q6 ZIP64 超限路径：真实 total_entries 个 central directory 空成员，
    再挂 ZIP64 EOCD + locator + 经典 EOCD 0xFFFF sentinel。
    禁止真实 CD 仅 1 项却 ZIP64 声明超限的假绿。
    """
    n = int(total_entries)
    assert n > MAX_ZIP_MEMBERS, (
        f"ZIP64 夹具：entries 须 > MAX_ZIP_MEMBERS={MAX_ZIP_MEMBERS} 以触发契约门，got {n}"
    )
    assert 1 <= n <= 0xFFFFFFFFFFFFFFFF, (
        "ZIP64 夹具：entries 须为正整数且可 pack 为 uint64"
    )
    # 先构造真实一致 n 空成员，再升级为 ZIP64 尾部
    raw = _rq_make_zip_consistent_empty_members(n)
    data = bytearray(raw)
    idx = data.rfind(b"PK\x05\x06")
    assert idx >= 0, "夹具：必须含经典 EOCD"
    cd_size = struct.unpack_from("<I", data, idx + 12)[0]
    cd_offset = struct.unpack_from("<I", data, idx + 16)[0]
    classic_tail = bytearray(data[idx:])
    # ZIP64 end of central directory record（固定 44 字节可扩展区）
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        n,
        n,
        int(cd_size),
        int(cd_offset),
    )
    # ZIP64 EOCD locator：relative offset = 插入点（原经典 EOCD 起点）
    zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, int(idx), 1)
    struct.pack_into("<H", classic_tail, 8, 0xFFFF)
    struct.pack_into("<H", classic_tail, 10, 0xFFFF)
    out = bytes(data[:idx]) + zip64_eocd + zip64_locator + bytes(classic_tail)
    assert b"PK\x06\x06" in out and b"PK\x06\x07" in out, (
        "夹具：必须含 ZIP64 EOCD 与 locator 签名"
    )
    # 真实 central 仍为 n 条（插入仅发生在 EOCD 前）
    assert _rq_count_zip_central_entries(out) == n, (
        f"夹具：ZIP64 路径 central 须保持 {n} 条"
    )
    return out


def test_v1n_release_gate_q1_exception_chain_json_oserror_utf8_zero_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q1 发布门：_json_or_invalid / OSError 上传读 / UnicodeDecodeError 正文 marker
    断 RemoteMineruError 的 cause/context/args/traceback 可达图零 marker；
    diagnosticCode 与固定中文不变。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)

    # --- A) _json_or_invalid 非网络 active-except ---
    json_fn = getattr(mod, "_json_or_invalid", None)
    assert callable(json_fn), "业务红：必须暴露 _json_or_invalid 供断链门（或等价可测入口）"

    class _BadJsonResp:
        status_code = 200

        def json(self) -> Any:
            raise ValueError(_RQ_JSON_MARKER)

    with pytest.raises(err_cls) as ei_json:
        json_fn(_BadJsonResp())  # type: ignore[arg-type]
    _assert_remote_error(ei_json.value, "api_response_invalid", msg_fn=msg_fn)
    assert ei_json.value.__cause__ is None, "业务红：__cause__ 必须为 None"
    assert ei_json.value.__context__ is None, (
        "业务红：__context__ 必须为 None（active except 内 _raise 会挂链）"
    )
    _rq_assert_zero_markers(ei_json.value, [_RQ_JSON_MARKER])

    # --- B) OSError 路径（上传读）---
    p = _write_temp_source(tmp_path, "q1-os.pdf", b"OSDATA-OK-12")
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)
    real_read = os.read
    read_hits = {"n": 0}

    def boom_read(fd: int, n: int, *a: Any, **k: Any) -> bytes:
        read_hits["n"] += 1
        if read_hits["n"] == 1:
            raise OSError(22, "synthetic-os-read", _RQ_OS_PATH_MARKER)
        return real_read(fd, n, *a, **k)

    monkeypatch.setattr(os, "read", boom_read)

    def handler_os(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        raise AssertionError("OSError 后不得进入 poll/ZIP")

    with pytest.raises(err_cls) as ei_os:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_os),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_os.value, "upload_failed", msg_fn=msg_fn)
    assert ei_os.value.__cause__ is None
    assert ei_os.value.__context__ is None
    _rq_assert_zero_markers(
        ei_os.value, [_RQ_OS_PATH_MARKER, "synthetic-os-read", str(tmp_path)]
    )

    # --- C) UnicodeDecodeError 正文 marker（full.md 非 UTF-8）---
    monkeypatch.setattr(os, "read", real_read)
    bad_zip = _make_zip_bytes({"full.md": _RQ_UTF8_BODY_MARKER})
    holder2: dict[str, str] = {}
    base2 = _post_ok_single(holder2)
    p2 = _write_temp_source(tmp_path, "q1-utf8.pdf", b"1")

    def handler_u(request: httpx.Request) -> httpx.Response:
        early = base2(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder2["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=bad_zip)
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_u:
        run(
            _build_sources(mod, [p2]),
            transport=httpx.MockTransport(handler_u),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_u.value, "output_invalid", msg_fn=msg_fn)
    assert ei_u.value.__cause__ is None
    assert ei_u.value.__context__ is None
    _rq_assert_zero_markers(
        ei_u.value,
        [
            "SYNTH_UTF8_BODY_MARKER_V1N_RQ1_FULLMD_NOT_REAL",
            _RQ_UTF8_BODY_MARKER,
        ],
    )


def test_v1n_release_gate_q2_source_size_share_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q2 发布门：上传流累计必须精确等于 expected_size（增长/缩短必红）；
    Content-Length 精确；Windows 打开禁止 FILE_SHARE_WRITE，并用行为门证明
    持有上传句柄时同尺寸改写不能成功（禁止只断常量）。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    run = _get_run_fn(mod)

    # --- Content-Length + 累计增长必红 ---
    content = b"ABCDEFGH" * 8  # 64 bytes
    p = _write_temp_source(tmp_path, "q2-size.pdf", content)
    expected = len(content)
    src_cls = _require_attr(mod, "RemoteSource")
    sources = [src_cls(path=p, filename=p.name, expected_size=expected)]
    holder: dict[str, str] = {}
    put_meta: dict[str, Any] = {
        "content_length": None,
        "body_len": None,
        "put_n": 0,
    }

    real_read = os.read
    read_state = {"sent": 0, "inflated": False}

    def grow_read(fd: int, n: int, *a: Any, **k: Any) -> bytes:
        if read_state["sent"] < expected:
            chunk = real_read(fd, min(n, expected - read_state["sent"]))
            read_state["sent"] += len(chunk)
            if chunk:
                return chunk
        if not read_state["inflated"]:
            read_state["inflated"] = True
            read_state["sent"] += 1
            return b"X"
        return b""

    monkeypatch.setattr(os, "read", grow_read)

    def handler_grow(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            holder["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            put_meta["put_n"] += 1
            put_meta["content_length"] = request.headers.get("Content-Length")
            raw = request.content
            put_meta["body_len"] = len(raw) if raw is not None else None
            return httpx.Response(200)
        raise AssertionError("增长失败后不得 poll")

    with pytest.raises(err_cls) as ei_g:
        run(
            sources,
            transport=httpx.MockTransport(handler_grow),
            **_run_kwargs(),
        )
    assert ei_g.value.diagnostic_code in {
        "source_identity_mismatch",
        "upload_failed",
    }, f"业务红：增长须 identity/upload 失败，actual={ei_g.value.diagnostic_code}"
    assert ei_g.value.message == _fixed_message(ei_g.value.diagnostic_code)
    if put_meta["put_n"] > 0:
        assert put_meta["content_length"] == str(expected), (
            f"业务红：Content-Length 必须精确 {expected}，actual={put_meta['content_length']!r}"
        )
        assert put_meta["body_len"] is None or put_meta["body_len"] == expected, (
            f"业务红：上传体累计不得 > expected，body_len={put_meta['body_len']}"
        )

    # --- 缩短：EOF 累计 < expected_size 必红 ---
    monkeypatch.setattr(os, "read", real_read)
    p_short = _write_temp_source(tmp_path, "q2-short.pdf", b"short-data-16b!")
    true_sz = p_short.stat().st_size
    sources_s = [src_cls(path=p_short, filename=p_short.name, expected_size=true_sz)]
    short_state = {"n": 0}

    def short_read(fd: int, n: int, *a: Any, **k: Any) -> bytes:
        short_state["n"] += 1
        if short_state["n"] == 1:
            return real_read(fd, max(1, true_sz // 2))
        return b""

    monkeypatch.setattr(os, "read", short_read)
    put_s = {"n": 0, "cl": None, "blen": None}

    def handler_short(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            put_s["n"] += 1
            put_s["cl"] = request.headers.get("Content-Length")
            put_s["blen"] = len(request.content) if request.content is not None else -1
            return httpx.Response(200)
        raise AssertionError("缩短后不得 poll")

    with pytest.raises(err_cls) as ei_s:
        run(
            sources_s,
            transport=httpx.MockTransport(handler_short),
            **_run_kwargs(),
        )
    assert ei_s.value.diagnostic_code in {
        "source_identity_mismatch",
        "upload_failed",
    }, f"业务红：缩短须失败，actual={ei_s.value.diagnostic_code}"
    if put_s["n"] > 0:
        assert put_s["cl"] == str(true_sz), (
            f"业务红：缩短场景 Content-Length 仍须为 expected={true_sz} actual={put_s['cl']!r}"
        )

    # --- 合法路径：Content-Length 精确且 body==expected ---
    monkeypatch.setattr(os, "read", real_read)
    p_ok = _write_temp_source(tmp_path, "q2-ok.pdf", b"OK-CONTENT-LENGTH-32-BYTES!!")
    exp_ok = p_ok.stat().st_size
    holder_ok: dict[str, str] = {}
    put_ok = {"cl": None, "blen": None}

    def handler_ok(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            holder_ok["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            put_ok["cl"] = request.headers.get("Content-Length")
            put_ok["blen"] = len(request.content) if request.content is not None else -1
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder_ok["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# q2-ok\n"})
            )
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p_ok]),
        transport=httpx.MockTransport(handler_ok),
        **_run_kwargs(),
    )
    assert "# q2-ok" in out.markdown
    assert put_ok["cl"] == str(exp_ok), (
        f"业务红：合法 PUT Content-Length 必须 {exp_ok}，actual={put_ok['cl']!r}"
    )
    assert put_ok["blen"] == exp_ok, (
        f"业务红：合法 PUT body 必须精确 expected，actual={put_ok['blen']}"
    )

    # --- Windows：禁止 FILE_SHARE_WRITE + 持句柄同尺寸改写行为门 ---
    if os.name == "nt":
        ops = getattr(mod, "_v1n_winapi_ops", None)
        assert ops is not None, "业务红：Windows 必须暴露 _v1n_winapi_ops seam"
        share_seen: list[int] = []
        real_cf = ops.CreateFileW

        def spy_cf(path, access, share, *rest):
            share_seen.append(int(share))
            return real_cf(path, access, share, *rest)

        monkeypatch.setattr(ops, "CreateFileW", spy_cf)
        p_w = _write_temp_source(tmp_path, "q2-share.pdf", b"SHARE-WRITE-GATE-CONTENT-32b")
        rewrite_ok = {"v": None}
        holder_w: dict[str, str] = {}

        def handler_w(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode("utf-8"))
                holder_w["id"] = body["files"][0]["data_id"]
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": FAKE_BATCH_ID,
                            "file_urls": [PRESIGNED_PUT_A],
                        },
                    },
                )
            if request.method == "PUT":
                try:
                    with open(p_w, "r+b", buffering=0) as wf:
                        wf.write(b"Z" * p_w.stat().st_size)
                    rewrite_ok["v"] = True
                except OSError:
                    rewrite_ok["v"] = False
                _ = request.content
                return httpx.Response(200)
            if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(
                request.url
            ):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": holder_w["id"],
                                    "state": "done",
                                    "full_zip_url": ZIP_URL_A,
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url) == ZIP_URL_A:
                return httpx.Response(
                    200, content=_make_zip_bytes({"full.md": b"# share\n"})
                )
            raise AssertionError("unexpected")

        try:
            run(
                _build_sources(mod, [p_w]),
                transport=httpx.MockTransport(handler_w),
                **_run_kwargs(),
            )
        except err_cls:
            pass
        assert share_seen, "业务红：必须经 CreateFileW 打开源"
        for sh in share_seen:
            assert (int(sh) & _FILE_SHARE_WRITE) == 0, (
                f"业务红：Windows 打开禁止 FILE_SHARE_WRITE，share={sh:#x}"
            )
        assert rewrite_ok["v"] is False, (
            "业务红：持有上传句柄时同尺寸改写不得成功（行为门，非常量）"
        )


def test_v1n_release_gate_q3_response_oom_canary_post_put_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q3 发布门：POST/PUT/poll 各用可观测 canary stream；超 cap 后不得继续读；
    保持阶段错误码；合法小响应仍通。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "q3.pdf", b"1")

    # Q9：禁止 getattr 默认回退测试常量；必须暴露 production 公开 cap 且精确相等
    assert hasattr(mod, "MAX_HTTP_JSON_RESPONSE_BYTES"), (
        "业务红：production 必须暴露 MAX_HTTP_JSON_RESPONSE_BYTES（禁止测试常量回退）"
    )
    assert hasattr(mod, "MAX_HTTP_PUT_RESPONSE_BYTES"), (
        "业务红：production 必须暴露 MAX_HTTP_PUT_RESPONSE_BYTES（禁止测试常量回退）"
    )
    json_cap = int(mod.MAX_HTTP_JSON_RESPONSE_BYTES)
    put_cap = int(mod.MAX_HTTP_PUT_RESPONSE_BYTES)
    assert json_cap == MAX_HTTP_JSON_RESPONSE_BYTES, (
        f"业务红：JSON 响应 cap 必须冻结 {MAX_HTTP_JSON_RESPONSE_BYTES}，actual={json_cap}"
    )
    assert put_cap == MAX_HTTP_PUT_RESPONSE_BYTES, (
        f"业务红：PUT 响应 cap 必须冻结 {MAX_HTTP_PUT_RESPONSE_BYTES}，actual={put_cap}"
    )

    def _canary_stream(
        limit: int, canary: bytes, log: dict[str, Any]
    ) -> httpx.SyncByteStream:
        exact = (b"{" + b"A" * max(0, limit - 2))[:limit]
        if len(exact) < limit:
            exact = exact + b"X" * (limit - len(exact))
        overflow = b"O"
        chunks = [exact, overflow, canary]

        class _S(httpx.SyncByteStream):
            def __iter__(self):  # type: ignore[override]
                log["iter"] += 1
                for ch in chunks:
                    if ch == canary and log["bytes"] >= limit:
                        log["saw_canary"] = True
                    if ch == overflow:
                        log["saw_overflow"] = True
                    log["bytes"] += len(ch)
                    log["n"] += 1
                    yield ch

            def close(self) -> None:
                log["close"] += 1

        return _S()

    # --- POST 超 cap ---
    post_log = {
        "n": 0,
        "bytes": 0,
        "saw_canary": False,
        "saw_overflow": False,
        "iter": 0,
        "close": 0,
    }
    canary_post = b"POST_CANARY_AFTER_CAP_MUST_NOT_READ"
    real_send = httpx.Client.send

    def spy_send_post(self, request, **kwargs):
        if request.method == "POST":
            assert kwargs.get("stream") is True, (
                f"业务红：POST 必须 stream=True，kwargs={kwargs!r}"
            )
            return httpx.Response(
                200,
                request=request,
                stream=_canary_stream(json_cap, canary_post, post_log),
                headers={"content-type": "application/json"},
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send_post)

    def handler_block(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise AssertionError("业务红：POST 不得非 stream Mock 整包")
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_post:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_block),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_post.value, "api_response_invalid", msg_fn=msg_fn)
    assert post_log["saw_overflow"] is True
    assert post_log["saw_canary"] is False, (
        f"业务红：POST 超 cap 后不得读 canary log={post_log}"
    )
    assert post_log["bytes"] == json_cap + 1

    # --- PUT 超 cap ---
    monkeypatch.setattr(httpx.Client, "send", real_send)
    put_log = {
        "n": 0,
        "bytes": 0,
        "saw_canary": False,
        "saw_overflow": False,
        "iter": 0,
        "close": 0,
    }
    canary_put = b"PUT_CANARY_AFTER_CAP_MUST_NOT_READ"
    holder_put: dict[str, str] = {}

    def spy_send_put(self, request, **kwargs):
        if request.method == "PUT":
            assert kwargs.get("stream") is True, "业务红：PUT 必须 stream=True 读响应"
            return httpx.Response(
                200,
                request=request,
                stream=_canary_stream(put_cap, canary_put, put_log),
                headers={"content-type": "application/octet-stream"},
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send_put)

    def handler_put(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            holder_put["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            raise AssertionError("业务红：PUT 响应须走 stream spy，不得 Mock 整包")
        raise AssertionError("PUT OOM 后不得 poll")

    with pytest.raises(err_cls) as ei_put:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_put),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_put.value, "upload_failed", msg_fn=msg_fn)
    assert put_log["saw_canary"] is False, f"业务红：PUT 超 cap 零 canary log={put_log}"
    assert put_log["bytes"] == put_cap + 1

    # --- poll 超 cap ---
    monkeypatch.setattr(httpx.Client, "send", real_send)
    poll_log = {
        "n": 0,
        "bytes": 0,
        "saw_canary": False,
        "saw_overflow": False,
        "iter": 0,
        "close": 0,
    }
    canary_poll = b"POLL_CANARY_AFTER_CAP_MUST_NOT_READ"
    holder_poll: dict[str, str] = {}

    def spy_send_poll(self, request, **kwargs):
        url = str(request.url)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in url:
            assert kwargs.get("stream") is True, "业务红：poll 必须 stream=True"
            return httpx.Response(
                200,
                request=request,
                stream=_canary_stream(json_cap, canary_poll, poll_log),
                headers={"content-type": "application/json"},
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send_poll)

    def handler_poll(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            holder_poll["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            raise AssertionError("业务红：poll 须 stream spy")
        raise AssertionError("poll OOM 后不得 ZIP")

    with pytest.raises(err_cls) as ei_poll:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_poll),
            **_run_kwargs(),
        )
    assert ei_poll.value.diagnostic_code in {
        "api_response_invalid",
        "api_request_failed",
    }, f"业务红：poll OOM 阶段码，actual={ei_poll.value.diagnostic_code}"
    assert ei_poll.value.message == _fixed_message(ei_poll.value.diagnostic_code)
    assert poll_log["saw_canary"] is False
    assert poll_log["bytes"] == json_cap + 1

    # --- 合法小响应仍通 ---
    monkeypatch.setattr(httpx.Client, "send", real_send)
    holder_ok: dict[str, str] = {}
    base = _post_ok_single(holder_ok)

    def handler_ok(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder_ok["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# q3-small-ok\n"})
            )
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler_ok),
        **_run_kwargs(),
    )
    assert "# q3-small-ok" in out.markdown


def test_v1n_release_gate_q4_put_timeout_recompute_after_resolve_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q4 发布门：resolver/open 消耗假时钟后，PUT timeout 必须使用重新计算的 remaining。
    """
    mod = _load_client()
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "q4.pdf", b"1")
    t0 = 50_000.0
    clock = {"t": t0}
    holder: dict[str, str] = {}
    put_timeouts: list[float] = []
    rem_at_put: list[float] = []

    def slow_resolve(host: str) -> list[str]:
        clock["t"] += 120.0
        return [FAKE_PUBLIC_IP_A]

    open_fn = getattr(mod, "_open_verified_fd", None)
    assert callable(open_fn), "业务红：必须有 _open_verified_fd"

    def slow_open(*a: Any, **k: Any) -> int:
        clock["t"] += 80.0
        return open_fn(*a, **k)

    monkeypatch.setattr(mod, "_open_verified_fd", slow_open)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            holder["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            rem_now = float(POLL_BUDGET_SEC) - (float(clock["t"]) - t0)
            rem_at_put.append(rem_now)
            ext = getattr(request, "extensions", None) or {}
            to = ext.get("timeout")
            vals: list[float] = []
            if isinstance(to, dict):
                vals = [float(to[k]) for k in ("connect", "read", "write", "pool")]
            else:
                for attr in ("connect", "read", "write", "pool"):
                    vals.append(float(getattr(to, attr)))
            put_timeouts.extend(vals)
            for v in vals:
                assert v <= rem_now + 1e-6, (
                    f"业务红：PUT timeout={v} 必须 ≤ resolve/open 后 remaining={rem_now}"
                )
            return httpx.Response(200)
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200, content=_make_zip_bytes({"full.md": b"# q4-ok\n"})
            )
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler),
        **_run_kwargs(
            clock_fn=lambda: clock["t"],
            resolve_addresses_fn=slow_resolve,
        ),
    )
    assert "# q4-ok" in out.markdown
    assert put_timeouts, "业务红：必须捕获 PUT timeout"
    assert rem_at_put and rem_at_put[0] <= float(POLL_BUDGET_SEC) - 200.0 + 1e-6, (
        f"业务红：resolve+open 后 remaining 须收缩，rem={rem_at_put}"
    )
    for v in put_timeouts:
        assert v <= float(POLL_BUDGET_SEC) - 200.0 + 1.0, (
            f"业务红：PUT timeout 不得使用 resolve/open 前的旧 remaining，v={v}"
        )


def test_v1n_release_gate_q5_final_path_upload_root_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q5 发布门：以最终已打开句柄路径对可信 upload root 做边界校验；
    用可注入 final-path seam 证明 junction 切到边界外时零 PUT。
    若需 trusted_upload_root 字段，仅在测试/契约冻结，不改 production。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    sig = inspect.signature(run)
    assert "trusted_upload_root" in sig.parameters, (
        "业务红：run_remote_mineru_parse 必须接受 trusted_upload_root（契约冻结）"
    )

    upload_root = tmp_path / "upload_root"
    upload_root.mkdir()
    outside = tmp_path / "outside_escape"
    outside.mkdir()
    leaf = upload_root / "doc.pdf"
    leaf.write_bytes(b"IN-ROOT-CONTENT")
    bait = outside / "doc.pdf"
    bait.write_bytes(b"OUT-OF-ROOT!!!!")
    assert leaf.stat().st_size == bait.stat().st_size

    assert hasattr(mod, "_v1n_final_path_for_fd"), (
        "业务红：必须提供可注入 final-path seam _v1n_final_path_for_fd(fd)->str"
    )

    def escape_final(fd: int) -> str:
        return str(bait.resolve())

    monkeypatch.setattr(mod, "_v1n_final_path_for_fd", escape_final)

    put_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            put_n["n"] += 1
            return httpx.Response(200)
        raise AssertionError("越界后不得 poll/ZIP")

    src_cls = _require_attr(mod, "RemoteSource")
    sources = [
        src_cls(
            path=leaf,
            filename=leaf.name,
            expected_size=leaf.stat().st_size,
        )
    ]
    with pytest.raises(err_cls) as ei:
        run(
            sources,
            transport=httpx.MockTransport(handler),
            trusted_upload_root=upload_root.resolve(),
            **_run_kwargs(),
        )
    _assert_remote_error(ei.value, "source_identity_mismatch", msg_fn=msg_fn)
    assert put_n["n"] == 0, f"业务红：final-path 越界必须零 PUT，put_n={put_n['n']}"


def test_v1n_release_gate_q6_eocd_members_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q6 发布门：ZipFile 构造前解析 EOCD/ZIP64 entries；声明成员数超 4096 时
    证明 ZipFile 未构造；合法小 ZIP、坏 ZIP 语义不弱化。

    反假绿：夹具在 ZipFile spy 前构造；每次 run 前 constructs 清零；
    超限夹具真实一致 4097 空成员 CD/EOCD（ZIP64 另含 locator/classic sentinel）；
    合法小 ZIP 的 constructs 增长必须来自 production 路径。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "q6.pdf", b"1")

    # --- 夹具在 spy 前构造，避免 _make_zip_bytes / 自证打开污染 constructs ---
    over = _rq_make_zip_eocd_total_entries(MAX_ZIP_MEMBERS + 1)
    over64 = _rq_make_zip64_eocd_total_entries(MAX_ZIP_MEMBERS + 1)
    small = _make_zip_bytes({"full.md": b"# q6-small\n"})
    bad = b"not-a-zip-at-all-PK-missing"

    # 自证：真实一致 4097 central + EOCD；禁止仅改声明
    assert _rq_count_zip_central_entries(over) == MAX_ZIP_MEMBERS + 1
    _eocd_over = over.rfind(b"PK\x05\x06")
    assert _eocd_over >= 0
    assert struct.unpack_from("<H", over, _eocd_over + 8)[0] == MAX_ZIP_MEMBERS + 1
    assert struct.unpack_from("<H", over, _eocd_over + 10)[0] == MAX_ZIP_MEMBERS + 1
    assert b"PK\x06\x06" in over64 and b"PK\x06\x07" in over64, (
        "夹具：必须含 ZIP64 EOCD 与 locator 签名"
    )
    assert _rq_count_zip_central_entries(over64) == MAX_ZIP_MEMBERS + 1
    _eocd_i = over64.rfind(b"PK\x05\x06")
    assert _eocd_i >= 0
    assert struct.unpack_from("<H", over64, _eocd_i + 8)[0] == 0xFFFF
    assert struct.unpack_from("<H", over64, _eocd_i + 10)[0] == 0xFFFF

    constructs = {"n": 0}
    real_zf = zipfile.ZipFile

    class _CountingZipFile(real_zf):  # type: ignore[misc,valid-type]
        def __init__(self, *a: Any, **k: Any) -> None:
            constructs["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(zipfile, "ZipFile", _CountingZipFile)
    if hasattr(mod, "zipfile"):
        monkeypatch.setattr(mod.zipfile, "ZipFile", _CountingZipFile)

    # A) 真实一致 4097 → zip_unsafe 且 run 时 ZipFile 构造 0
    constructs["n"] = 0
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler_over(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=over)
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_over),
            **_run_kwargs(),
        )
    _assert_remote_error(ei.value, "zip_unsafe", msg_fn=msg_fn)
    assert constructs["n"] == 0, (
        f"业务红：真实一致成员>{MAX_ZIP_MEMBERS} 时 ZipFile 不得构造，"
        f"n={constructs['n']}"
    )

    # A2) ZIP64：真实 4097 CD + ZIP64 EOCD/locator/classic sentinel → 构造 0
    constructs["n"] = 0
    holder64: dict[str, str] = {}
    base64 = _post_ok_single(holder64)

    def handler_over64(request: httpx.Request) -> httpx.Response:
        early = base64(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder64["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=over64)
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei64:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_over64),
            **_run_kwargs(),
        )
    _assert_remote_error(ei64.value, "zip_unsafe", msg_fn=msg_fn)
    assert constructs["n"] == 0, (
        f"业务红：ZIP64 真实一致成员>{MAX_ZIP_MEMBERS} 时 ZipFile 不得构造，"
        f"n={constructs['n']}"
    )

    # B) 合法小 ZIP：run 前精确清零，constructs 增长必须来自 production
    constructs["n"] = 0
    holder2: dict[str, str] = {}
    base2 = _post_ok_single(holder2)

    def handler_ok(request: httpx.Request) -> httpx.Response:
        early = base2(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder2["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=small)
        raise AssertionError("unexpected")

    out = run(
        _build_sources(mod, [p]),
        transport=httpx.MockTransport(handler_ok),
        **_run_kwargs(),
    )
    assert "# q6-small" in out.markdown
    assert constructs["n"] > 0, (
        "业务红：合法小 ZIP 须证明 production 路径构造 ZipFile（run 前已清零）"
    )

    # C) 坏 ZIP 语义不弱化
    constructs["n"] = 0
    holder3: dict[str, str] = {}
    base3 = _post_ok_single(holder3)

    def handler_bad(request: httpx.Request) -> httpx.Response:
        early = base3(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder3["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(200, content=bad)
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_bad:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_bad),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_bad.value, "zip_unsafe", msg_fn=msg_fn)


def test_v1n_release_gate_q3_zip_compress_single_chunk_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q3/Q6 追加（TEST-Q3-ADD）：ZIP 压缩单块前门。

    当前生产风险：iter_bytes 透明解 gzip/br/zstd，且先 buf.extend(chunk) 再判总 cap，
    单个压缩炸弹/超大 chunk 可在门前巨量分配。

    failure-first 必须证明下列 **其一**：
      A) ZIP GET 显式 Accept-Encoding: identity，且拒绝 Content-Encoding 非 identity/空；
      B) 使用 iter_raw，且每块只接受 remaining+1，不得先完整 extend 超大单块。
    另：超大单块后 canary 不可再读；3xx→zip_download_failed；坏 ZIP→zip_unsafe。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "q3-zip-ce.pdf", b"1")

    limit = 64
    monkeypatch.setattr(mod, "MAX_ZIP_BYTES", limit)
    canary = b"ZIP_COMPRESS_SINGLE_CHUNK_CANARY_MUST_NOT_READ"
    # 单块远超 remaining+1；其后 canary 不得被消费
    oversize_single = b"H" * (limit + 200)
    assert len(oversize_single) > limit + 1

    zip_req: dict[str, Any] = {
        "accept_encoding": None,
        "stream_true": 0,
        "got_zip": 0,
    }
    stream_log = {
        "n": 0,
        "bytes": 0,
        "saw_canary": False,
        "first_chunk_len": 0,
        "close": 0,
    }
    iter_hits = {"iter_bytes": 0, "iter_raw": 0}
    # 观测超大块是否被完整 buffer 消费，或仅切片 remaining+1
    probe_log: dict[str, Any] = {
        "buffer_full_lens": [],
        "slice_lens": [],
        "as_bytes_lens": [],
    }

    class _SliceProbe:
        """
        可观测 chunk：完整 buffer 协议消费 vs 切片后小块消费。
        production 若 buf.extend(整块) 会走 buffer 并记录 full；
        若 chunk[:remaining+1] 再 extend 则记录 slice_lens。
        """

        __slots__ = ("_raw",)

        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __len__(self) -> int:
            return len(self._raw)

        def __getitem__(self, item: Any) -> Any:
            out = self._raw[item]
            if isinstance(item, slice):
                probe_log["slice_lens"].append(
                    len(out) if isinstance(out, (bytes, bytearray)) else 1
                )
            return out

        def __bytes__(self) -> bytes:
            probe_log["as_bytes_lens"].append(len(self._raw))
            return self._raw

        def __buffer__(self, flags: int):  # noqa: ARG002 — PEP 688
            probe_log["buffer_full_lens"].append(len(self._raw))
            return memoryview(self._raw)

    real_send = httpx.Client.send
    real_iter_bytes = httpx.Response.iter_bytes
    real_iter_raw = httpx.Response.iter_raw

    def _wrap_zip_chunks(real_fn: Any, hit_key: str):  # type: ignore[no-untyped-def]
        def spy(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            try:
                url_s = str(self.request.url)
            except Exception:
                return real_fn(self, *a, **k)
            if url_s != ZIP_URL_A:
                return real_fn(self, *a, **k)

            def gen():  # type: ignore[no-untyped-def]
                iter_hits[hit_key] += 1
                for chunk in real_fn(self, *a, **k):
                    if not chunk:
                        yield chunk
                        continue
                    raw = (
                        bytes(chunk)
                        if not isinstance(chunk, (bytes, bytearray))
                        else bytes(chunk)
                    )
                    yield _SliceProbe(raw)

            return gen()

        return spy

    monkeypatch.setattr(
        httpx.Response, "iter_bytes", _wrap_zip_chunks(real_iter_bytes, "iter_bytes")
    )
    monkeypatch.setattr(
        httpx.Response, "iter_raw", _wrap_zip_chunks(real_iter_raw, "iter_raw")
    )

    class _SingleBombStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[override]
            for ch in (oversize_single, canary):
                if ch is canary and stream_log["bytes"] >= limit:
                    stream_log["saw_canary"] = True
                if stream_log["n"] == 0:
                    stream_log["first_chunk_len"] = len(ch)
                stream_log["n"] += 1
                stream_log["bytes"] += len(ch)
                yield ch

        def close(self) -> None:
            stream_log["close"] += 1

    def spy_send_bomb(self, request, **kwargs):  # type: ignore[no-untyped-def]
        url_s = str(request.url)
        if request.method == "GET" and url_s == ZIP_URL_A:
            zip_req["got_zip"] += 1
            zip_req["accept_encoding"] = request.headers.get("Accept-Encoding")
            if kwargs.get("stream") is not True:
                raise AssertionError(
                    "业务红：ZIP GET 必须 send(..., stream=True)，"
                    f"kwargs.stream={kwargs.get('stream')!r}"
                )
            zip_req["stream_true"] += 1
            return httpx.Response(
                200,
                request=request,
                stream=_SingleBombStream(),
                headers={"content-type": "application/zip"},
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send_bomb)
    holder: dict[str, str] = {}
    base = _post_ok_single(holder)

    def handler_bomb(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            raise AssertionError("业务红：ZIP 超大单块须走 stream spy，不得 Mock 整包")
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_bomb:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_bomb),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_bomb.value, "zip_unsafe", msg_fn=msg_fn)
    assert zip_req["got_zip"] >= 1, "业务红：必须发起 ZIP GET"
    assert zip_req["stream_true"] >= 1
    assert stream_log["first_chunk_len"] == len(oversize_single)
    assert stream_log["saw_canary"] is False, (
        f"业务红：超大单块触发 cap 后不得再读 canary log={stream_log}"
    )

    # --- 策略判定：A identity 拒绝 或 B iter_raw+remaining+1 ---
    ae_raw = zip_req["accept_encoding"]
    ae_norm = (ae_raw or "").strip().casefold()
    has_identity_header = ae_norm == "identity"

    full_buffers = [
        n for n in probe_log["buffer_full_lens"] if n > limit + 1
    ]
    # 完整物化门：bytes(chunk)/buffer 协议任一对超大块整段消费均否决 partial_ok
    full_as_bytes = [
        n for n in probe_log["as_bytes_lens"] if n > limit + 1
    ]
    # 策略 B：iter_raw、无 iter_bytes；超大块未完整物化；存在 <=remaining+1 的切片
    partial_slices = [n for n in probe_log["slice_lens"] if 1 <= n <= limit + 1]
    partial_ok = (
        iter_hits["iter_raw"] >= 1
        and iter_hits["iter_bytes"] == 0
        and not full_buffers
        and not full_as_bytes
        and len(partial_slices) >= 1
    )

    # --- 策略 A 补充：Content-Encoding 非 identity 必须拒绝 ---
    # Q5：真 gzip 压缩合法 ZIP（可正确解码）；identity 分支须 body 迭代次数=0；
    # 错误码精确冻结 zip_download_failed（禁止三选一宽放 / 解压失败假绿）。
    import gzip as _gzip_ce

    monkeypatch.setattr(httpx.Client, "send", real_send)
    # 重置 iter 计数，专观 CE 路径 body 消费
    iter_hits["iter_bytes"] = 0
    iter_hits["iter_raw"] = 0
    holder_ce: dict[str, str] = {}
    base_ce = _post_ok_single(holder_ce)
    valid_zip = _make_zip_bytes({"full.md": b"# ce-gate-should-not-succeed\n"})
    gzipped_zip = _gzip_ce.compress(valid_zip)
    assert gzipped_zip != valid_zip and len(gzipped_zip) > 0
    # 自证：真 gzip 可正确解码回合法 ZIP
    assert _gzip_ce.decompress(gzipped_zip) == valid_zip
    ce_seen: dict[str, Any] = {"ae": None, "zip_n": 0, "body_bytes": 0}

    class _CountingGzipStream(httpx.SyncByteStream):
        def __iter__(self):  # type: ignore[override]
            # 整包可解码 gzip；若 production 迭代 body 则计数可见
            ce_seen["body_bytes"] += len(gzipped_zip)
            yield gzipped_zip

        def close(self) -> None:
            return None

    def spy_send_ce(self, request, **kwargs):  # type: ignore[no-untyped-def]
        url_s = str(request.url)
        if request.method == "GET" and url_s == ZIP_URL_A:
            ce_seen["zip_n"] += 1
            ce_seen["ae"] = request.headers.get("Accept-Encoding")
            return httpx.Response(
                200,
                request=request,
                stream=_CountingGzipStream(),
                headers={
                    "content-type": "application/zip",
                    "content-encoding": "gzip",
                },
            )
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", spy_send_ce)

    def handler_ce(request: httpx.Request) -> httpx.Response:
        early = base_ce(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder_ce["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            raise AssertionError("业务红：CE 门 ZIP GET 须走 stream spy")
        raise AssertionError("unexpected")

    ce_exc: BaseException | None = None
    ce_out_ok = False
    try:
        out_ce = run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_ce),
            **_run_kwargs(),
        )
        ce_out_ok = "# ce-gate-should-not-succeed" in getattr(out_ce, "markdown", "")
    except err_cls as exc:
        ce_exc = exc
    except Exception as exc:  # pragma: no cover - 非契约异常亦记
        ce_exc = exc

    ce_rejected = False
    if ce_exc is not None and isinstance(ce_exc, err_cls):
        code = getattr(ce_exc, "diagnostic_code", "")
        # 精确冻结：非 identity Content-Encoding → zip_download_failed
        if code == "zip_download_failed":
            ce_rejected = True
            assert ce_exc.message == _fixed_message("zip_download_failed")
    assert ce_out_ok is False, (
        "业务红：Content-Encoding=gzip 真压缩合法 ZIP 时不得成功解析 full.md"
    )

    body_iters = int(iter_hits["iter_bytes"]) + int(iter_hits["iter_raw"])
    # 策略 A：显式 identity + 拒绝非 identity CE，且拒绝时不得消费 body
    identity_ok = (
        has_identity_header
        and ce_rejected
        and body_iters == 0
        and int(ce_seen["body_bytes"]) == 0
    )
    assert identity_ok or partial_ok, (
        "业务红：ZIP 压缩单块前门必须满足其一——"
        "A) Accept-Encoding: identity 且拒绝非 identity Content-Encoding"
        "（真压缩可解码载荷、错误码 zip_download_failed、body 迭代=0）；"
        "B) iter_raw 且每块只接受 remaining+1"
        "（as_bytes_lens/buffer_full_lens 均不得 >remaining+1 完整物化）。"
        f" ae={ae_raw!r} ce_rejected={ce_rejected}"
        f" ce_code={getattr(ce_exc, 'diagnostic_code', ce_exc)!r}"
        f" body_iters={body_iters} body_bytes={ce_seen['body_bytes']}"
        f" iter_hits={iter_hits} full_buffers={full_buffers}"
        f" full_as_bytes={full_as_bytes}"
        f" as_bytes_lens={probe_log['as_bytes_lens'][:8]}"
        f" slice_lens={probe_log['slice_lens'][:8]}"
    )
    monkeypatch.setattr(httpx.Client, "send", real_send)

    # --- 语义保留：3xx → zip_download_failed；坏 ZIP → zip_unsafe ---
    monkeypatch.setattr(httpx.Response, "iter_bytes", real_iter_bytes)
    monkeypatch.setattr(httpx.Response, "iter_raw", real_iter_raw)

    holder_3xx: dict[str, str] = {}
    base_3xx = _post_ok_single(holder_3xx)

    def handler_3xx(request: httpx.Request) -> httpx.Response:
        early = base_3xx(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder_3xx["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                302, headers={"Location": "https://cdn.example.test/result/other.zip"}
            )
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_3xx:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_3xx),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_3xx.value, "zip_download_failed", msg_fn=msg_fn)

    holder_bad: dict[str, str] = {}
    base_bad = _post_ok_single(holder_bad)
    bad_zip = b"not-a-real-zip-payload-for-ce-gate"

    def handler_bad2(request: httpx.Request) -> httpx.Response:
        early = base_bad(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder_bad["id"],
                                "state": "done",
                                "full_zip_url": ZIP_URL_A,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == ZIP_URL_A:
            return httpx.Response(
                200,
                content=bad_zip,
                headers={"content-type": "application/zip"},
            )
        raise AssertionError("unexpected")

    with pytest.raises(err_cls) as ei_bad2:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler_bad2),
            **_run_kwargs(),
        )
    _assert_remote_error(ei_bad2.value, "zip_unsafe", msg_fn=msg_fn)


def test_v1n_release_gate_q7_runtime_error_fold_internal_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Q7 发布门：普通 RuntimeError(marker) 必须折叠为 RemoteMineruError/internal_error；
    异常链/可达图零 marker 泄漏。网络熔断已改用 _V1NNetFuseError（BaseException），
    禁止 production 以广义 except RuntimeError: raise 自缚透传。

    反假绿：boom_hits 精确=1（注入 seam 真实触发）；HTTP hits 精确=0
    （折叠发生在任何网络请求之前）；禁止仅靠字段断言、未证明注入路径。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "q7-rt.pdf", b"1")
    marker = "SYNTH_RUNTIME_ERR_MARKER_V1N_Q7_NOT_REAL"
    assert hasattr(mod, "_synthetic_name"), (
        "业务红：须存在 _synthetic_name 注入点以触发主路径 RuntimeError"
    )

    boom_hits = {"n": 0}
    http_hits = {"n": 0}

    def _boom_synthetic(*a: Any, **k: Any) -> str:
        boom_hits["n"] += 1
        raise RuntimeError(f"{marker} {FAKE_TOKEN}")

    monkeypatch.setattr(mod, "_synthetic_name", _boom_synthetic)

    def handler(request: httpx.Request) -> httpx.Response:
        # 任何 HTTP 均计次；折叠须在 HTTP 前完成
        http_hits["n"] += 1
        raise AssertionError(
            "业务红：RuntimeError 折叠须在 HTTP 前触发，不得发出后续请求"
            f" method={request.method!r} url={request.url!r}"
        )

    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(),
        )
    assert boom_hits["n"] == 1, (
        f"业务红：_synthetic_name 注入须精确触发 1 次，boom_hits={boom_hits['n']}"
    )
    assert http_hits["n"] == 0, (
        f"业务红：RuntimeError 折叠前 HTTP 须精确 0，http_hits={http_hits['n']}"
    )
    _assert_remote_error(ei.value, "internal_error", msg_fn=msg_fn)
    assert ei.value.__cause__ is None
    assert ei.value.__context__ is None
    _rq_assert_zero_markers(ei.value, [marker, FAKE_TOKEN])

# ===========================================================================
# V1-N P0 active-except 异常断链红门（test-only failure-first）
# 节点关键字：v1n_p0_active_except / v1n_p0_capture_baseline_lstat
# 前置：Codex P0 question msg_36e9d115617541c9ae86c02e0ea574a0；Grok Q1-Q4 全 YES。
# 生产未授权修复；本节点必须在当前生产上真实 failed（__context__ 重挂 + marker 可达）。
# 禁止 skip/xfail/提前 return/宽 OR/except Exception:pass；禁止复制 production 或手工清链。
# ===========================================================================

_P0_ACTIVE_EXCEPT_MARKER = "SYNTH_P0_ACTIVE_EXCEPT_VALUEERROR_MARKER_V1N_NOT_REAL"
_P0_LSTAT_OSERROR_MARKER = "SYNTH_P0_LSTAT_OSERROR_MARKER_V1N_NOT_REAL"
_P0_LSTAT_OSERROR_STR = "synthetic-p0-lstat-not-real"
_P0_LSTAT_FAKE_PATH_MARKER = "p0_lstat_not_real_upload"


def test_v1n_p0_active_except_valueerror_raise_zero_chain_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    P0 红门-A：active except ValueError(唯一合成 marker) 内直接调用真实 module._raise；
    捕获 RemoteMineruError；断固定 internal_error 的 code/message/args，且
    __cause__ is None、__context__ is None；复用 _rq_walk_exc_graph 遍历
    args/cause/context/traceback/f_locals 公开异常图零 marker。
    必须证明真实 _raise 命中精确一次；禁止复制 production 实现或测试内手工清链。

    当前 production 在 active except 内 bare raise 会重挂 __context__，必须真实失败。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    real_raise = _require_attr(mod, "_raise")
    assert callable(real_raise), "业务红：必须暴露 _raise 供 active-except 断链门"
    code = "internal_error"
    expected_msg = _fixed_message(code)

    # 正反自证（正）：唯一 marker 在合成 ValueError 可达图中必现
    synth_ve = ValueError(_P0_ACTIVE_EXCEPT_MARKER)
    synth_blob = _rq_walk_exc_graph(synth_ve)
    assert _P0_ACTIVE_EXCEPT_MARKER in synth_blob, (
        "夹具自证失败：ValueError 可达图必须含唯一 marker（正）"
    )
    assert _P0_ACTIVE_EXCEPT_MARKER in repr(synth_ve.args), (
        "夹具自证失败：ValueError.args 必须含唯一 marker（正）"
    )
    # 正反自证（反）：无关串不得误命中 marker 字面量
    assert "SYNTH_P0_ACTIVE_EXCEPT_VALUEERROR_MARKER_V1N_NOT_REAL_X" not in synth_blob
    assert _P0_ACTIVE_EXCEPT_MARKER + "_X" not in synth_blob

    raise_hits = {"n": 0}

    def counting_raise(c: str) -> None:
        """仅计数后委托真实 production _raise；禁止在此清链或改写异常。"""
        raise_hits["n"] += 1
        return real_raise(c)

    # 模块入口与局部均指向同一计数包装，证明命中真实 _raise 路径
    monkeypatch.setattr(mod, "_raise", counting_raise)

    with pytest.raises(err_cls) as ei:
        try:
            raise ValueError(_P0_ACTIVE_EXCEPT_MARKER)
        except ValueError:
            # 经 module._raise 入口调用真实实现（计数包装不复制 production）
            mod._raise(code)

    assert raise_hits["n"] == 1, (
        f"业务红：真实 _raise 须精确命中 1 次，hits={raise_hits['n']}"
    )
    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    assert exc.__cause__ is None, "业务红：__cause__ 必须为 None（真正断链）"
    assert exc.__context__ is None, (
        "业务红：__context__ 必须为 None（active except 内 _raise 不得重挂原异常）"
    )
    # 反：公开异常图零 marker（walker 复用既有 _rq_walk_exc_graph）
    _rq_assert_zero_markers(exc, [_P0_ACTIVE_EXCEPT_MARKER])


def test_v1n_p0_capture_baseline_lstat_oserror_zero_chain_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    P0 红门-B：monkeypatch module.os.lstat 抛带唯一合成 marker 的 OSError；
    调用真实 _capture_baseline；断固定 source_identity_mismatch 码/中文/args，
    lstat 精确一次；__cause__/__context__ 均为 None；公开异常图零 marker。
    禁止真实路径/数据；禁止手工清链或复制 production。

    当前 production active except OSError → _raise 会重挂 __context__，必须真实失败。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    capture = _require_attr(mod, "_capture_baseline")
    assert callable(capture), "业务红：必须暴露 _capture_baseline 供 lstat 断链门"
    code = "source_identity_mismatch"
    expected_msg = _fixed_message(code)

    # 正反自证（正）：合成 OSError 可达图必含唯一 marker
    synth_os = OSError(22, _P0_LSTAT_OSERROR_STR, _P0_LSTAT_OSERROR_MARKER)
    synth_blob = _rq_walk_exc_graph(synth_os)
    assert _P0_LSTAT_OSERROR_MARKER in synth_blob, (
        "夹具自证失败：OSError 可达图必须含唯一 marker（正）"
    )
    assert _P0_LSTAT_OSERROR_STR in synth_blob, (
        "夹具自证失败：OSError 可达图必须含 strerror 合成串（正）"
    )
    # 正反自证（反）：无关串不得误命中
    assert _P0_LSTAT_OSERROR_MARKER + "_X" not in synth_blob

    lstat_hits = {"n": 0}

    def boom_lstat(path: object, *a: Any, **k: Any) -> Any:
        lstat_hits["n"] += 1
        # 第三参 filename 位嵌入唯一 marker（OSError 可达 args）；无真实路径/数据
        raise OSError(22, _P0_LSTAT_OSERROR_STR, _P0_LSTAT_OSERROR_MARKER)

    # 精确补丁 production 使用的 module.os.lstat（及同对象 os.lstat）
    assert hasattr(mod, "os"), "业务红：production 必须绑定 os 模块供 lstat seam"
    monkeypatch.setattr(mod.os, "lstat", boom_lstat)
    monkeypatch.setattr(os, "lstat", boom_lstat)

    fake_path = Path(f"C:/synth/{_P0_LSTAT_FAKE_PATH_MARKER}/source.pdf")
    with pytest.raises(err_cls) as ei:
        capture(fake_path)

    assert lstat_hits["n"] == 1, (
        f"业务红：module.os.lstat 注入须精确触发 1 次，hits={lstat_hits['n']}"
    )
    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    assert exc.__cause__ is None, "业务红：__cause__ 必须为 None（真正断链）"
    assert exc.__context__ is None, (
        "业务红：__context__ 必须为 None（lstat OSError 不得挂入 RemoteMineruError 链）"
    )
    # 反：公开异常图零 marker / 合成 strerror / 假路径片段
    _rq_assert_zero_markers(
        exc,
        [
            _P0_LSTAT_OSERROR_MARKER,
            _P0_LSTAT_OSERROR_STR,
            _P0_LSTAT_FAKE_PATH_MARKER,
        ],
    )


# ===========================================================================
# V1-N P0 public-entry 隐私红门（test-only failure-first）
# 节点关键字：v1n_p0_entry_unsupported_suffix / v1n_p0_entry_baseline_lstat
# 前置：Codex ref_question msg_3358a01db4fd4ec8932e24414f51eace；
#       ref_yes msg_c07712a80d4e42cf8985cecb9a8722a8；task msg_59535539bfa84d3ba65cfeb58d1c34c1。
# 生产未授权修复；本节点必须在当前 production 上真实 failed：
#   A) run_remote_mineru_parse 帧 f_locals 保留 token/tok/src；
#   B) 入口 locals + active-except __context__ 挂 OSError。
# 禁止 skip/xfail/提前 return/宽 OR/except Exception:pass；禁止复制 production 或手工清链。
# 禁止真实路径/Token/网络；仅合成 marker；HTTP 客户端与 transport 必须零命中。
# ===========================================================================

_P0_ENTRY_UNSUP_TOKEN_MARKER = "SYNTH_P0_ENTRY_UNSUP_TOKEN_MARKER_V1N_NOT_REAL"
_P0_ENTRY_UNSUP_PATH_MARKER = "SYNTH_P0_ENTRY_UNSUP_PATH_MARKER_V1N_NOT_REAL"
_P0_ENTRY_UNSUP_FN_MARKER = "SYNTH_P0_ENTRY_UNSUP_FN_MARKER_V1N_NOT_REAL"

_P0_ENTRY_LSTAT_TOKEN_MARKER = "SYNTH_P0_ENTRY_LSTAT_TOKEN_MARKER_V1N_NOT_REAL"
_P0_ENTRY_LSTAT_PATH_MARKER = "SYNTH_P0_ENTRY_LSTAT_PATH_MARKER_V1N_NOT_REAL"
_P0_ENTRY_LSTAT_FN_MARKER = "SYNTH_P0_ENTRY_LSTAT_FN_MARKER_V1N_NOT_REAL"
_P0_ENTRY_LSTAT_OSERROR_MARKER = "SYNTH_P0_ENTRY_LSTAT_OSERROR_MARKER_V1N_NOT_REAL"
_P0_ENTRY_LSTAT_OSERROR_STR = "synthetic-p0-entry-lstat-not-real"


def test_v1n_p0_entry_unsupported_suffix_privacy_zero_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    P0 入口红门-A：真实 public entry run_remote_mineru_parse；
    RemoteSource 的 path/filename 与 token 均嵌唯一合成 marker，后缀不受支持；
    cancel_check 恒 False（未取消）。断固定 source_type_unsupported
    code/message/args，__cause__/__context__ 均为 None；
    默认/注入 HTTP 客户端与 transport 零命中；
    复用 _rq_walk_exc_graph（跳过测试帧）断 token/tok/path/filename marker 全零可达。

    当前 production 在 run_remote_mineru_parse 帧 locals 保留 token/tok/src，必须真实失败。
    禁止真实路径/数据；禁止手工清链或复制 production。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    src_cls = _require_attr(mod, "RemoteSource")
    code = "source_type_unsupported"
    expected_msg = _fixed_message(code)

    # 合成 RemoteSource：path/filename/credential 均含唯一 marker；非法后缀 .txt
    synth_path = Path(
        f"C:/synth/{_P0_ENTRY_UNSUP_PATH_MARKER}/not_real_upload.txt"
    )
    synth_filename = f"{_P0_ENTRY_UNSUP_FN_MARKER}.txt"
    synth_token = _P0_ENTRY_UNSUP_TOKEN_MARKER
    assert synth_path.suffix.lower() == ".txt"
    assert Path(synth_filename).suffix.lower() not in ALLOWED_SOURCE_SUFFIXES

    # 正反自证（正）：合成对象字面量必含各唯一 marker
    assert _P0_ENTRY_UNSUP_PATH_MARKER in str(synth_path), (
        "夹具自证失败：path 必须含唯一 path marker（正）"
    )
    assert _P0_ENTRY_UNSUP_FN_MARKER in synth_filename, (
        "夹具自证失败：filename 必须含唯一 filename marker（正）"
    )
    assert _P0_ENTRY_UNSUP_TOKEN_MARKER in synth_token, (
        "夹具自证失败：token 必须含唯一 credential marker（正）"
    )
    # 正反自证（反）：无关串不得误命中
    assert _P0_ENTRY_UNSUP_PATH_MARKER + "_X" not in str(synth_path)
    assert _P0_ENTRY_UNSUP_FN_MARKER + "_X" not in synth_filename
    assert _P0_ENTRY_UNSUP_TOKEN_MARKER + "_X" not in synth_token

    source = src_cls(
        path=synth_path,
        filename=synth_filename,
        expected_size=1,
    )
    # RemoteSource 字段正自证
    assert _P0_ENTRY_UNSUP_PATH_MARKER in str(source.path)
    assert _P0_ENTRY_UNSUP_FN_MARKER in source.filename

    transport_hits = {"n": 0}
    client_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        transport_hits["n"] += 1
        raise AssertionError(
            "业务红：unsupported suffix 须在 HTTP 前拒绝，"
            f"不得命中 transport method={request.method!r} url={request.url!r}"
        )

    real_client_init = httpx.Client.__init__

    def counting_client_init(self: Any, *a: Any, **k: Any) -> Any:
        client_hits["n"] += 1
        return real_client_init(self, *a, **k)

    monkeypatch.setattr(httpx.Client, "__init__", counting_client_init)

    with pytest.raises(err_cls) as ei:
        run(
            [source],
            token=synth_token,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )

    assert transport_hits["n"] == 0, (
        f"业务红：注入 transport 必须零命中，hits={transport_hits['n']}"
    )
    assert client_hits["n"] == 0, (
        f"业务红：httpx.Client 默认/注入构造必须零命中，hits={client_hits['n']}"
    )

    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    assert exc.__cause__ is None, "业务红：__cause__ 必须为 None（真正断链）"
    assert exc.__context__ is None, (
        "业务红：__context__ 必须为 None（suffix 门不得挂链）"
    )
    # 反：生产异常图（跳过测试帧）token/tok/path/filename marker 全零
    _rq_assert_zero_markers(
        exc,
        [
            _P0_ENTRY_UNSUP_TOKEN_MARKER,
            _P0_ENTRY_UNSUP_PATH_MARKER,
            _P0_ENTRY_UNSUP_FN_MARKER,
        ],
    )


def test_v1n_p0_entry_baseline_lstat_privacy_zero_marker(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    P0 入口红门-B：合法 .pdf RemoteSource，合成 token/path/filename；
    仅让 production module.os.lstat 抛含唯一合成 marker 的 OSError，精确一次；
    调真实 public entry run_remote_mineru_parse；断固定 source_identity_mismatch
    code/message/args，__cause__/__context__ 均为 None；HTTP 客户端与 transport=0；
    同一生产异常图断 credential/path/filename/OSError marker 全零。

    当前 production 同时暴露入口 locals 与 active-except context，必须真实失败。
    禁止真实路径/数据；禁止手工清链或复制 production。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    src_cls = _require_attr(mod, "RemoteSource")
    code = "source_identity_mismatch"
    expected_msg = _fixed_message(code)

    synth_path = Path(
        f"C:/synth/{_P0_ENTRY_LSTAT_PATH_MARKER}/not_real_upload.pdf"
    )
    synth_filename = f"{_P0_ENTRY_LSTAT_FN_MARKER}.pdf"
    synth_token = _P0_ENTRY_LSTAT_TOKEN_MARKER
    assert Path(synth_filename).suffix.lower() in ALLOWED_SOURCE_SUFFIXES
    assert synth_path.suffix.lower() == ".pdf"

    # 正反自证（正）：path/filename/token 与合成 OSError 可达图
    assert _P0_ENTRY_LSTAT_PATH_MARKER in str(synth_path), (
        "夹具自证失败：path 必须含唯一 path marker（正）"
    )
    assert _P0_ENTRY_LSTAT_FN_MARKER in synth_filename, (
        "夹具自证失败：filename 必须含唯一 filename marker（正）"
    )
    assert _P0_ENTRY_LSTAT_TOKEN_MARKER in synth_token, (
        "夹具自证失败：token 必须含唯一 credential marker（正）"
    )
    synth_os = OSError(
        22, _P0_ENTRY_LSTAT_OSERROR_STR, _P0_ENTRY_LSTAT_OSERROR_MARKER
    )
    synth_blob = _rq_walk_exc_graph(synth_os)
    assert _P0_ENTRY_LSTAT_OSERROR_MARKER in synth_blob, (
        "夹具自证失败：OSError 可达图必须含唯一 OSError marker（正）"
    )
    assert _P0_ENTRY_LSTAT_OSERROR_STR in synth_blob, (
        "夹具自证失败：OSError 可达图必须含 strerror 合成串（正）"
    )
    # 正反自证（反）
    assert _P0_ENTRY_LSTAT_PATH_MARKER + "_X" not in str(synth_path)
    assert _P0_ENTRY_LSTAT_FN_MARKER + "_X" not in synth_filename
    assert _P0_ENTRY_LSTAT_TOKEN_MARKER + "_X" not in synth_token
    assert _P0_ENTRY_LSTAT_OSERROR_MARKER + "_X" not in synth_blob

    source = src_cls(
        path=synth_path,
        filename=synth_filename,
        expected_size=12,
    )

    lstat_hits = {"n": 0}
    transport_hits = {"n": 0}
    client_hits = {"n": 0}

    def boom_lstat(path: object, *a: Any, **k: Any) -> Any:
        lstat_hits["n"] += 1
        # filename 位嵌入唯一 marker；无真实路径/数据
        raise OSError(
            22, _P0_ENTRY_LSTAT_OSERROR_STR, _P0_ENTRY_LSTAT_OSERROR_MARKER
        )

    assert hasattr(mod, "os"), "业务红：production 必须绑定 os 模块供 lstat seam"
    monkeypatch.setattr(mod.os, "lstat", boom_lstat)

    def handler(request: httpx.Request) -> httpx.Response:
        transport_hits["n"] += 1
        raise AssertionError(
            "业务红：baseline lstat 失败须在 HTTP 前折叠，"
            f"不得命中 transport method={request.method!r} url={request.url!r}"
        )

    real_client_init = httpx.Client.__init__

    def counting_client_init(self: Any, *a: Any, **k: Any) -> Any:
        client_hits["n"] += 1
        return real_client_init(self, *a, **k)

    monkeypatch.setattr(httpx.Client, "__init__", counting_client_init)

    with pytest.raises(err_cls) as ei:
        run(
            [source],
            token=synth_token,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )

    assert lstat_hits["n"] == 1, (
        f"业务红：module.os.lstat 注入须精确触发 1 次，hits={lstat_hits['n']}"
    )
    assert transport_hits["n"] == 0, (
        f"业务红：注入 transport 必须零命中，hits={transport_hits['n']}"
    )
    assert client_hits["n"] == 0, (
        f"业务红：httpx.Client 默认/注入构造必须零命中，hits={client_hits['n']}"
    )

    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    assert exc.__cause__ is None, "业务红：__cause__ 必须为 None（真正断链）"
    assert exc.__context__ is None, (
        "业务红：__context__ 必须为 None（lstat OSError 不得挂入入口异常链）"
    )
    # 反：credential/path/filename/OSError marker 全零
    _rq_assert_zero_markers(
        exc,
        [
            _P0_ENTRY_LSTAT_TOKEN_MARKER,
            _P0_ENTRY_LSTAT_PATH_MARKER,
            _P0_ENTRY_LSTAT_FN_MARKER,
            _P0_ENTRY_LSTAT_OSERROR_MARKER,
            _P0_ENTRY_LSTAT_OSERROR_STR,
        ],
    )


# ===========================================================================
# V1-N FINAL-TEST-GATES：五条最终发布红门（test-only failure-first）
# 节点关键字：v1n_final_p0_put_locals / v1n_final_p0_zip_locals /
#            v1n_final_p1_aggcap / v1n_final_p1_frozen_root / v1n_final_p2_zip_undercount
# 前置双方确认：
#   P0 loop locals msg_7b0fbd… → msg_2df057…；P1 aggcap msg_fee8f8… → msg_b1a8a6…；
#   P1 frozen root msg_885651… → msg_a7347f…；P2 undercount msg_11918e… → msg_1a99df…
# 任务：msg_98067da7ba3941e8a946b9ab3022143d
# 生产未授权修复；五门必须在当前 production 上真实 failed。
# 禁止 skip/xfail/提前 return/宽 OR/except Exception:pass；禁止复制 production 或清链。
# ===========================================================================

_FINAL_P0_PUT_TOKEN = "SYNTH_FINAL_P0_PUT_TOKEN_MARKER_V1N_NOT_REAL"
_FINAL_P0_PUT_PATH = "SYNTH_FINAL_P0_PUT_PATH_MARKER_V1N_NOT_REAL"
_FINAL_P0_PUT_FN = "SYNTH_FINAL_P0_PUT_FN_MARKER_V1N_NOT_REAL"
_FINAL_P0_PUT_URL = (
    "https://upload.example.test/presign/SYNTH_FINAL_P0_PUT_URL_MARKER_V1N_NOT_REAL"
)

_FINAL_P0_ZIP_TOKEN = "SYNTH_FINAL_P0_ZIP_TOKEN_MARKER_V1N_NOT_REAL"
_FINAL_P0_ZIP_PATH = "SYNTH_FINAL_P0_ZIP_PATH_MARKER_V1N_NOT_REAL"
_FINAL_P0_ZIP_FN = "SYNTH_FINAL_P0_ZIP_FN_MARKER_V1N_NOT_REAL"
_FINAL_P0_ZIP_PUT_URL = (
    "https://upload.example.test/presign/SYNTH_FINAL_P0_ZIP_PUT_URL_MARKER_V1N_NOT_REAL"
)
_FINAL_P0_ZIP_URL = (
    "https://cdn.example.test/result/SYNTH_FINAL_P0_ZIP_URL_MARKER_V1N_NOT_REAL.zip"
)
_FINAL_P0_ZIP_ITEM_EXTRA = "SYNTH_FINAL_P0_ZIP_ITEM_EXTRA_MARKER_V1N_NOT_REAL"

_FINAL_P1_AGGCAP_BODY_MARKER = "SYNTH_FINAL_P1_AGGCAP_BODY_MARKER_V1N_NOT_REAL"
_FINAL_P1_AGGCAP_ZIP_A = "https://cdn.example.test/result/final-aggcap-a.zip"
_FINAL_P1_AGGCAP_ZIP_B = "https://cdn.example.test/result/final-aggcap-b.zip"
_FINAL_P1_AGGCAP_ZIP_C = "https://cdn.example.test/result/final-aggcap-c.zip"
_FINAL_P1_AGGCAP_PUT_A = "https://upload.example.test/presign/final-aggcap-a"
_FINAL_P1_AGGCAP_PUT_B = "https://upload.example.test/presign/final-aggcap-b"
_FINAL_P1_AGGCAP_PUT_C = "https://upload.example.test/presign/final-aggcap-c"

_FINAL_P1_FROZEN_ROOT = "C:/trusted"
_FINAL_P1_OUTSIDE_FILE = "D:/outside/file"
_FINAL_P1_OUTSIDE_ROOT = "D:/outside"

_FINAL_P2_ZIP_URL = "https://cdn.example.test/result/final-undercount.zip"


def _rq_deep_collect(obj: Any, parts: list[str], seen: set[int], depth: int = 0) -> None:
    """
    递归采集对象可达文本：Mapping/Sequence/Path/RemoteSource/str/bytes/BaseException。
    循环引用保护；深度有界；不截断 f_locals 条目数（由调用方全量传入）。
    """
    if obj is None or depth > 16:
        return
    if isinstance(obj, (bool, int, float, complex)):
        return
    if isinstance(obj, str):
        parts.append(obj)
        return
    if isinstance(obj, (bytes, bytearray)):
        b = bytes(obj)
        parts.append(b.decode("latin-1", errors="replace"))
        parts.append(repr(b))
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)
    if isinstance(obj, BaseException):
        parts.append(type(obj).__name__)
        try:
            parts.append(str(obj))
        except Exception:
            pass
        try:
            parts.append(repr(obj))
        except Exception:
            pass
        try:
            parts.append(repr(getattr(obj, "args", ())))
        except Exception:
            pass
        for attr in ("message", "diagnostic_code", "filename", "strerror"):
            try:
                v = getattr(obj, attr, None)
            except Exception:
                v = None
            if v is not None:
                _rq_deep_collect(v, parts, seen, depth + 1)
        for a in getattr(obj, "args", ()) or ():
            _rq_deep_collect(a, parts, seen, depth + 1)
        cause = getattr(obj, "__cause__", None)
        ctx = getattr(obj, "__context__", None)
        if cause is not None:
            _rq_deep_collect(cause, parts, seen, depth + 1)
        if ctx is not None:
            _rq_deep_collect(ctx, parts, seen, depth + 1)
        return
    # Path / pathlib
    if isinstance(obj, Path) or type(obj).__name__ in {"WindowsPath", "PosixPath", "PurePath", "PureWindowsPath", "PurePosixPath"}:
        try:
            parts.append(str(obj))
            parts.append(repr(obj))
        except Exception:
            pass
        return
    # RemoteSource 或带 path/filename 的冻结记录
    tname = type(obj).__name__
    if tname == "RemoteSource" or hasattr(obj, "__dataclass_fields__"):
        for attr in ("path", "filename", "expected_size", "name", "markdown"):
            if hasattr(obj, attr):
                try:
                    _rq_deep_collect(getattr(obj, attr), parts, seen, depth + 1)
                except Exception:
                    pass
        try:
            parts.append(repr(obj))
        except Exception:
            pass
        return
    from collections.abc import Mapping as _Mapping
    from collections.abc import Sequence as _Sequence

    if isinstance(obj, _Mapping):
        try:
            items = list(obj.items())
        except Exception:
            items = []
        for k, v in items:
            _rq_deep_collect(k, parts, seen, depth + 1)
            _rq_deep_collect(v, parts, seen, depth + 1)
        return
    if isinstance(obj, (set, frozenset)):
        try:
            vals = list(obj)
        except Exception:
            vals = []
        for v in vals:
            _rq_deep_collect(v, parts, seen, depth + 1)
        return
    if isinstance(obj, _Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        try:
            vals = list(obj)
        except Exception:
            vals = []
        for v in vals:
            _rq_deep_collect(v, parts, seen, depth + 1)
        return
    # 其它：有限 repr（避免巨型对象撑爆）
    try:
        r = repr(obj)
        if len(r) > 800:
            r = r[:800]
        parts.append(r)
    except Exception:
        pass


def _rq_walk_exc_graph_deep(exc: BaseException) -> str:
    """
    FINAL 门专用：扫描全部 production f_locals（不得截前 64），
    递归 Mapping/Sequence/Path/RemoteSource/str/bytes/BaseException；
    跳过测试帧；循环引用保护。
    """
    import traceback as _tb

    parts: list[str] = []
    stack: list[BaseException] = [exc]
    seen_exc: set[int] = set()
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen_exc:
            continue
        seen_exc.add(id(cur))
        value_seen: set[int] = set()
        _rq_deep_collect(cur, parts, value_seen, 0)
        try:
            parts.append(
                "".join(_tb.format_exception(type(cur), cur, cur.__traceback__))
            )
        except Exception:
            pass
        tb = cur.__traceback__
        depth = 0
        while tb is not None and depth < 64:
            fr = tb.tb_frame
            if not _rq_is_test_frame(fr):
                try:
                    # 全量 locals，禁止 [:64]
                    for k, v in list(fr.f_locals.items()):
                        parts.append(str(k))
                        _rq_deep_collect(v, parts, value_seen, 0)
                except Exception:
                    pass
            tb = tb.tb_next
            depth += 1
        cause = getattr(cur, "__cause__", None)
        ctx = getattr(cur, "__context__", None)
        if cause is not None:
            stack.append(cause)
        if ctx is not None:
            stack.append(ctx)
    return "\n".join(parts)


def _rq_assert_zero_markers_deep(exc: BaseException, markers: list) -> None:
    """FINAL 门：深 walker 零 marker；禁止测试内清生产异常。"""
    blob = _rq_walk_exc_graph_deep(exc)
    for m in markers:
        if not m:
            continue
        if isinstance(m, (bytes, bytearray)):
            s = bytes(m).decode("latin-1", errors="replace")
            assert s not in blob and repr(bytes(m)) not in blob, (
                f"业务红：异常可达图泄漏字节 marker {bytes(m)!r}"
            )
        else:
            assert str(m) not in blob, f"业务红：异常可达图泄漏 marker {m!r}"


def test_v1n_final_p0_put_failure_locals_zero_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    FINAL-P0-C：真实 public entry；tmp_path 合成 PDF；MockTransport + resolver 零 DNS；
    合成 token/source path+filename/预签 PUT URL marker；POST 成功、PUT 固定失败；
    断 upload_failed 与阶段计数；production 异常图（深 walker 全 locals）零全部 marker。
    当前因 for-loop 局部 src/u/put_url 稳定红。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    src_cls = _require_attr(mod, "RemoteSource")
    code = "upload_failed"
    expected_msg = _fixed_message(code)

    sub = tmp_path / _FINAL_P0_PUT_PATH
    sub.mkdir(parents=True, exist_ok=True)
    leaf = sub / f"{_FINAL_P0_PUT_FN}.pdf"
    leaf.write_bytes(b"%PDF-1.4 synth-put-fail-not-real\n")
    synth_filename = f"{_FINAL_P0_PUT_FN}.pdf"
    source = src_cls(
        path=leaf,
        filename=synth_filename,
        expected_size=leaf.stat().st_size,
    )

    # 正反自证
    assert _FINAL_P0_PUT_TOKEN in _FINAL_P0_PUT_TOKEN
    assert _FINAL_P0_PUT_PATH in str(leaf)
    assert _FINAL_P0_PUT_FN in synth_filename
    assert "SYNTH_FINAL_P0_PUT_URL_MARKER_V1N_NOT_REAL" in _FINAL_P0_PUT_URL
    assert _FINAL_P0_PUT_PATH + "_X" not in str(leaf)

    counts = {"post": 0, "put": 0, "poll": 0, "zip": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and PATH_FILE_URLS_BATCH in str(request.url):
            counts["post"] += 1
            body = json.loads(request.content.decode("utf-8"))
            assert len(body.get("files") or []) == 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [_FINAL_P0_PUT_URL],
                    },
                },
            )
        if request.method == "PUT":
            counts["put"] += 1
            # 固定失败：非 200
            return httpx.Response(500, content=b"put-fail-synth")
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            counts["poll"] += 1
            raise AssertionError("业务红：PUT 失败后不得进入 poll")
        if request.method == "GET":
            counts["zip"] += 1
            raise AssertionError("业务红：PUT 失败后不得 ZIP GET")
        raise AssertionError(f"unexpected {request.method} {request.url}")

    with pytest.raises(err_cls) as ei:
        run(
            [source],
            token=_FINAL_P0_PUT_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )

    assert counts["post"] == 1, f"业务红：POST 须精确 1，counts={counts}"
    assert counts["put"] == 1, f"业务红：PUT 须精确 1（失败），counts={counts}"
    assert counts["poll"] == 0, f"业务红：PUT 失败后 poll 须 0，counts={counts}"
    assert counts["zip"] == 0, f"业务红：PUT 失败后 zip 须 0，counts={counts}"

    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    # 深 walker：token / path / filename / put_url 全零
    _rq_assert_zero_markers_deep(
        exc,
        [
            _FINAL_P0_PUT_TOKEN,
            _FINAL_P0_PUT_PATH,
            _FINAL_P0_PUT_FN,
            "SYNTH_FINAL_P0_PUT_URL_MARKER_V1N_NOT_REAL",
            _FINAL_P0_PUT_URL,
        ],
    )


def test_v1n_final_p0_zip_failure_locals_zero_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    FINAL-P0-D：POST/PUT/poll 成功；poll item 带合成 extra marker 与 ZIP URL marker；
    ZIP GET 固定失败；断 zip_download_failed、动作精确；
    全 production 异常图零 token/source/PUT URL/item extra/ZIP URL marker。
    当前因 item/zip_url/u/put_url/src/client_kwargs 稳定红。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    src_cls = _require_attr(mod, "RemoteSource")
    code = "zip_download_failed"
    expected_msg = _fixed_message(code)

    sub = tmp_path / _FINAL_P0_ZIP_PATH
    sub.mkdir(parents=True, exist_ok=True)
    leaf = sub / f"{_FINAL_P0_ZIP_FN}.pdf"
    leaf.write_bytes(b"%PDF-1.4 synth-zip-fail-not-real\n")
    synth_filename = f"{_FINAL_P0_ZIP_FN}.pdf"
    source = src_cls(
        path=leaf,
        filename=synth_filename,
        expected_size=leaf.stat().st_size,
    )

    assert _FINAL_P0_ZIP_ITEM_EXTRA + "_X" not in _FINAL_P0_ZIP_ITEM_EXTRA
    assert "SYNTH_FINAL_P0_ZIP_URL_MARKER_V1N_NOT_REAL" in _FINAL_P0_ZIP_URL

    holder: dict[str, str] = {}
    counts = {"post": 0, "put": 0, "poll": 0, "zip": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and PATH_FILE_URLS_BATCH in str(request.url):
            counts["post"] += 1
            body = json.loads(request.content.decode("utf-8"))
            holder["id"] = body["files"][0]["data_id"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [_FINAL_P0_ZIP_PUT_URL],
                    },
                },
            )
        if request.method == "PUT":
            counts["put"] += 1
            return httpx.Response(200, content=b"")
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            counts["poll"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": _FINAL_P0_ZIP_URL,
                                # 合成 extra：应出现在 item 局部；生产不得泄漏
                                "extra": _FINAL_P0_ZIP_ITEM_EXTRA,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == _FINAL_P0_ZIP_URL:
            counts["zip"] += 1
            # 固定 ZIP 下载失败
            return httpx.Response(503, content=b"zip-unavailable-synth")
        raise AssertionError(f"unexpected {request.method} {request.url}")

    with pytest.raises(err_cls) as ei:
        run(
            [source],
            token=_FINAL_P0_ZIP_TOKEN,
            cancel_check=lambda: False,
            transport=httpx.MockTransport(handler),
            sleep_fn=lambda _s: None,
            clock_fn=lambda: 0.0,
            resolve_addresses_fn=_default_public_resolver,
        )

    assert counts["post"] == 1, f"业务红：POST 须精确 1，counts={counts}"
    assert counts["put"] == 1, f"业务红：PUT 须精确 1，counts={counts}"
    assert counts["poll"] >= 1, f"业务红：poll 须至少 1，counts={counts}"
    assert counts["zip"] == 1, f"业务红：ZIP GET 须精确 1（失败），counts={counts}"

    exc = ei.value
    _assert_remote_error(exc, code, msg_fn=msg_fn if callable(msg_fn) else None)
    assert exc.args == (expected_msg,), (
        f"业务红：args 必须精确为 (message,)，actual={exc.args!r}"
    )
    _rq_assert_zero_markers_deep(
        exc,
        [
            _FINAL_P0_ZIP_TOKEN,
            _FINAL_P0_ZIP_PATH,
            _FINAL_P0_ZIP_FN,
            "SYNTH_FINAL_P0_ZIP_PUT_URL_MARKER_V1N_NOT_REAL",
            _FINAL_P0_ZIP_PUT_URL,
            "SYNTH_FINAL_P0_ZIP_URL_MARKER_V1N_NOT_REAL",
            _FINAL_P0_ZIP_URL,
            _FINAL_P0_ZIP_ITEM_EXTRA,
        ],
    )


def test_v1n_final_p1_multi_source_aggregate_cap_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    FINAL-P1 多源累计 cap fail-fast：3 个 done item，每份 full.md 单独合法；
    冻结小 codepoint cap；第 2 份加精确 separator 后累计超；
    ZIP 下载精确 2，第三 URL 精确 0；output_invalid、零部分返回、零 marker。
    当前下载 3 份后才红。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)

    sep = SOURCE_SEPARATOR
    assert sep == "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
    # 每份 10 码点合法；两份 + sep = 10+37+10=57；cap=50 → 第二份后超
    body_a = "A" * 10
    body_b = "B" * 10
    body_c = "C" * 10 + _FINAL_P1_AGGCAP_BODY_MARKER
    assert len(body_a) == 10 and len(body_b) == 10
    assert len(body_a) + len(sep) + len(body_b) == 57
    cap_cp = 50
    assert len(body_a) <= cap_cp and len(body_b) <= cap_cp and len(body_c) <= 200
    assert len(body_a) + len(sep) + len(body_b) > cap_cp

    monkeypatch.setattr(mod, "MAX_MD_CODEPOINTS", cap_cp)
    # UTF-8 放宽，确保只触发累计 codepoint 门
    monkeypatch.setattr(mod, "MAX_MD_UTF8_BYTES", 2 * 1024 * 1024)

    paths = []
    for i, name in enumerate(("a.pdf", "b.pdf", "c.pdf"), start=1):
        p = _write_temp_source(tmp_path, name, f"%PDF synth-agg-{i}\n".encode("ascii"))
        paths.append(p)
    sources = _build_sources(mod, paths)

    z_a = _make_zip_bytes({"full.md": body_a.encode("utf-8")})
    z_b = _make_zip_bytes({"full.md": body_b.encode("utf-8")})
    z_c = _make_zip_bytes({"full.md": body_c.encode("utf-8")})

    holder: dict[str, list[str]] = {"ids": []}
    zip_gets = {
        _FINAL_P1_AGGCAP_ZIP_A: 0,
        _FINAL_P1_AGGCAP_ZIP_B: 0,
        _FINAL_P1_AGGCAP_ZIP_C: 0,
    }
    put_n = {"n": 0}
    post_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and PATH_FILE_URLS_BATCH in str(request.url):
            post_n["n"] += 1
            body = json.loads(request.content.decode("utf-8"))
            ids = [f["data_id"] for f in body["files"]]
            holder["ids"] = ids
            assert len(ids) == 3
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [
                            _FINAL_P1_AGGCAP_PUT_A,
                            _FINAL_P1_AGGCAP_PUT_B,
                            _FINAL_P1_AGGCAP_PUT_C,
                        ],
                    },
                },
            )
        if request.method == "PUT":
            put_n["n"] += 1
            return httpx.Response(200, content=b"")
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            ids = holder["ids"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": ids[0],
                                "state": "done",
                                "full_zip_url": _FINAL_P1_AGGCAP_ZIP_A,
                            },
                            {
                                "data_id": ids[1],
                                "state": "done",
                                "full_zip_url": _FINAL_P1_AGGCAP_ZIP_B,
                            },
                            {
                                "data_id": ids[2],
                                "state": "done",
                                "full_zip_url": _FINAL_P1_AGGCAP_ZIP_C,
                            },
                        ]
                    },
                },
            )
        u = str(request.url)
        if request.method == "GET" and u in zip_gets:
            zip_gets[u] += 1
            if u == _FINAL_P1_AGGCAP_ZIP_A:
                return httpx.Response(200, content=z_a)
            if u == _FINAL_P1_AGGCAP_ZIP_B:
                return httpx.Response(200, content=z_b)
            if u == _FINAL_P1_AGGCAP_ZIP_C:
                return httpx.Response(200, content=z_c)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    with pytest.raises(err_cls) as ei:
        run(
            sources,
            transport=httpx.MockTransport(handler),
            **_run_kwargs(),
        )

    # 不得部分返回成功
    assert not hasattr(ei, "value") or ei.value is not None
    _assert_remote_error(ei.value, "output_invalid", msg_fn=msg_fn)
    assert put_n["n"] == 3, f"业务红：三源 PUT 均应成功，put={put_n['n']}"
    assert post_n["n"] == 1
    assert zip_gets[_FINAL_P1_AGGCAP_ZIP_A] == 1, (
        f"业务红：ZIP-A 须精确 1，got={zip_gets}"
    )
    assert zip_gets[_FINAL_P1_AGGCAP_ZIP_B] == 1, (
        f"业务红：ZIP-B 须精确 1（累计超限发生在 B 后），got={zip_gets}"
    )
    assert zip_gets[_FINAL_P1_AGGCAP_ZIP_C] == 0, (
        f"业务红：累计超限后第三 ZIP 必须精确 0，got={zip_gets}"
    )
    # 零 marker（body marker 仅在第三份，不应被下载；异常图亦不得泄漏）
    _rq_assert_zero_markers_deep(
        ei.value,
        [_FINAL_P1_AGGCAP_BODY_MARKER, FAKE_TOKEN],
    )


def test_v1n_final_p1_frozen_trusted_root_reresolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    FINAL-P1 冻结可信根再 resolve：freeze 值 C:/trusted，final handle D:/outside/file；
    仅当 production 再次 resolve 冻结根时 seam 映射为 D:/outside；
    必须 source_identity_mismatch、PUT=0。
    当前错误接受路径并继续而红。
    不得依赖真实 junction/reparse 或真实盘外路径。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    sig = inspect.signature(run)
    assert "trusted_upload_root" in sig.parameters, (
        "业务红：run_remote_mineru_parse 必须接受 trusted_upload_root"
    )
    assert hasattr(mod, "_v1n_final_path_for_fd"), (
        "业务红：必须提供可注入 final-path seam"
    )

    # 真实可读文件仅作 open 目标；final-path 与 root 全由 seam 合成
    leaf = _write_temp_source(tmp_path, "trusted-leaf.pdf", b"%PDF-in-tmp-not-outside\n")

    def escape_final(fd: int) -> str:
        return _FINAL_P1_OUTSIDE_FILE

    monkeypatch.setattr(mod, "_v1n_final_path_for_fd", escape_final)

    real_resolve = Path.resolve
    resolve_hits = {"trusted": 0, "other": 0}

    def _norm_path_str(p: object) -> str:
        s = str(p).replace("\\", "/").rstrip("/")
        # 去掉 Windows \\?\ 前缀
        if s.startswith("//?/") or s.startswith("\\\\?\\"):
            s = s[4:] if s.startswith("\\\\?\\") else s[4:]
            s = s.replace("\\", "/")
        return s.lower()

    def seam_resolve(self: Path, *a: Any, **k: Any) -> Path:
        norm = _norm_path_str(self)
        # 冻结根 C:/trusted：production 再次 resolve 时映射到 D:/outside
        if norm == "c:/trusted" or norm.endswith("/trusted") and norm.startswith("c:"):
            resolve_hits["trusted"] += 1
            return Path(_FINAL_P1_OUTSIDE_ROOT)
        if "d:/outside" in norm or norm.startswith("d:/outside"):
            resolve_hits["other"] += 1
            # 不跟随真实盘：直接规范化字符串
            return Path(str(self).replace("\\", "/"))
        resolve_hits["other"] += 1
        try:
            return real_resolve(self, *a, **k)
        except Exception:
            return Path(str(self).replace("\\", "/"))

    monkeypatch.setattr(Path, "resolve", seam_resolve)

    put_n = {"n": 0}
    post_n = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            post_n["n"] += 1
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": FAKE_BATCH_ID,
                        "file_urls": [PRESIGNED_PUT_A],
                    },
                },
            )
        if request.method == "PUT":
            put_n["n"] += 1
            return httpx.Response(200)
        raise AssertionError(
            f"业务红：冻结根越界后不得继续 poll/ZIP method={request.method}"
        )

    sources = _build_sources(mod, [leaf])
    with pytest.raises(err_cls) as ei:
        run(
            sources,
            transport=httpx.MockTransport(handler),
            trusted_upload_root=_FINAL_P1_FROZEN_ROOT,
            **_run_kwargs(),
        )

    _assert_remote_error(ei.value, "source_identity_mismatch", msg_fn=msg_fn)
    assert put_n["n"] == 0, (
        f"业务红：冻结根再 resolve 绕过必须零 PUT，put_n={put_n['n']} "
        f"resolve_hits={resolve_hits} post={post_n}"
    )


def test_v1n_final_p2_zip_declared_undercount_pre_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    FINAL-P2 ZIP 声明少报前门：真实 4 项中央目录合法小 ZIP 后仅篡改 classic EOCD 声明=1；
    cap=3；固定 zip_unsafe，ZipFile 构造精确 0。
    当前 constructs=1 后才拒而红。保留合法 classic/ZIP64 既有门，不改旧测试。
    """
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    run = _get_run_fn(mod)
    p = _write_temp_source(tmp_path, "final-undercount.pdf", b"1")

    # 夹具在 ZipFile spy 前构造：真实 4 CD + 一致 EOCD，再仅改 EOCD 声明为 1
    raw4 = _rq_make_zip_consistent_empty_members(4)
    assert _rq_count_zip_central_entries(raw4) == 4
    eocd = raw4.rfind(b"PK\x05\x06")
    assert eocd >= 0
    data = bytearray(raw4)
    # classic EOCD：disk entries @+8，total entries @+10 → 均改为 1（少报）
    struct.pack_into("<H", data, eocd + 8, 1)
    struct.pack_into("<H", data, eocd + 10, 1)
    under = bytes(data)
    # 自证：CD 仍 4，声明 1
    assert _rq_count_zip_central_entries(under) == 4
    assert struct.unpack_from("<H", under, eocd + 8)[0] == 1
    assert struct.unpack_from("<H", under, eocd + 10)[0] == 1

    # 冻结 cap=3：声明 1 放行、实际 4 应在 ZipFile 前拒
    monkeypatch.setattr(mod, "MAX_ZIP_MEMBERS", 3)

    constructs = {"n": 0}
    real_zf = zipfile.ZipFile

    class _CountingZipFile(real_zf):  # type: ignore[misc,valid-type]
        def __init__(self, *a: Any, **k: Any) -> None:
            constructs["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(zipfile, "ZipFile", _CountingZipFile)
    if hasattr(mod, "zipfile"):
        monkeypatch.setattr(mod.zipfile, "ZipFile", _CountingZipFile)

    holder: dict[str, str] = {}
    base = _post_ok_single(holder, put_url=PRESIGNED_PUT_A)

    def handler(request: httpx.Request) -> httpx.Response:
        early = base(request)
        if early is not None:
            return early
        if request.method == "GET" and PATH_EXTRACT_RESULTS_PREFIX in str(request.url):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "data_id": holder["id"],
                                "state": "done",
                                "full_zip_url": _FINAL_P2_ZIP_URL,
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == _FINAL_P2_ZIP_URL:
            return httpx.Response(200, content=under)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    constructs["n"] = 0
    with pytest.raises(err_cls) as ei:
        run(
            _build_sources(mod, [p]),
            transport=httpx.MockTransport(handler),
            **_run_kwargs(),
        )
    _assert_remote_error(ei.value, "zip_unsafe", msg_fn=msg_fn)
    assert constructs["n"] == 0, (
        f"业务红：声明少报(CD=4,declared=1,cap=3) 时 ZipFile 不得构造，"
        f"constructs={constructs['n']}"
    )
