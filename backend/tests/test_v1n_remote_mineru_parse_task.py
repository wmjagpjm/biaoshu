"""
模块：V1-N remote_mineru 任务旁路 failure-first 专项
用途：经真实 task_service 证明 Token/类型零 HTTP、成功五域单事务、失败/取消零部分写回、
  隐私 canary、未注册 parse_engines、lightweight/managed 不回退；production 缺失时业务红。
对接：docs/v1n-remote-mineru-api-contract.md；test_v1n_remote_mineru_client.py。
二次开发：
  - 每测 TEMP uploads；禁止真实 biaoshu.db / 业务 uploads。
  - 禁止 skip/xfail、or True、except Exception:pass、复制 production finalizer/ZIP。
  - 不得 monkeypatch 假 production 模块冒充实现已存在。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.models.entities import (
    EditorStateRevisionRow,
    ProjectFileRow,
    ProjectTaskEventRow,
    ProjectTaskRow,
)
from app.services import parse_engines, task_service

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

CLIENT_MOD = "app.services.remote_mineru_client"
ENGINE = "remote_mineru"
SOURCE_SEPARATOR = "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
SUCCESS_KEYS = frozenset({"engine", "fileCount", "chars"})
FAIL_KEYS = frozenset({"engine", "diagnosticCode"})
TOKEN_ENV = "BIAOSHU_REMOTE_MINERU_TOKEN"
MANIFEST_ENV = "BIAOSHU_MANAGED_OCR_MANIFEST"

FAKE_TOKEN = "test-token-not-real"
FAKE_BATCH = "batch-canary-should-not-leak-001"
FAKE_DATA_ID = "e" * 32
FAKE_PRESIGN = "https://upload.example.test/presign/canary"
FAKE_ZIP_URL = "https://cdn.example.test/zip/canary.zip"
FAKE_CLOUD_ERR = "cloud-err-msg-canary-secret"
FAKE_TRACE = "trace-id-canary-secret"
ORIGINAL_FILENAME = "用户机密招标文件-甲乙丙.pdf"
BODY_CANARY = "BODY_CANARY_REMOTE_MD_SHOULD_NOT_LEAK_IN_ERROR"

FIXED_TOKEN_UNCONFIGURED = "token_unconfigured"
FIXED_UNSUPPORTED = "source_type_unsupported"
FIXED_FINALIZER_ERROR = "任务落盘失败"

FIXED_ERR_TOO_MANY_SOURCES = "源文件数量超过上限"
FIXED_ERR_TOTAL_SIZE = "源文件总大小超过上限"
FIXED_ERR_SIZE_MISMATCH = "源文件大小不一致"
FIXED_ERR_LEAF_REPARSE = "源文件链接或重解析点被拒绝"
FIXED_ERR_PARENT_REPARSE = "源文件父目录链接或重解析点被拒绝"
FIXED_ERR_TRAVERSAL = "源文件路径越界被拒绝"
FIXED_ERR_CODEPOINTS = "解析正文码点超过上限"
FIXED_ERR_UTF8_BYTES = "解析正文体积超过上限"

ALLOWED_SUFFIXES = (
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
)
REJECT_SUFFIXES = (".html", ".txt", ".md", ".markdown", ".exe", ".bin")

# 测试独立常量表：code→中文（不得用 production message_for_code 自证）
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
# 失败 result 语义：remote client/协议/Token/后缀 → 安全二键；
# shared gate/CAS/finalizer/已取消 → 沿用既有 task 语义（result 可能为 None / cancelled）
REMOTE_TWO_KEY_CODES = frozenset(FIXED_MESSAGES.keys()) - {"interrupted"}

_TEMP_ROOTS: list[Path] = []
_NET_GUARD_HITS: list[str] = []
_TRACKED_UPLOAD_ROOTS: list[Path] = []

# ---------------------------------------------------------------------------
# TEMP / 隔离 / 熔断
# ---------------------------------------------------------------------------

def _track_temp(root: Path) -> Path:
    resolved = root.resolve()
    _TEMP_ROOTS.append(resolved)
    return resolved

def _cleanup_temp(root: Path) -> None:
    if not root.exists():
        return
    shutil.rmtree(root)
    assert not root.exists(), f"TEMP 删除后仍存在: {root}"

def _expected_pytest_db_path() -> Path:
    """用途：conftest 固定 sqlite:///./data/biaoshu-pytest.db → 绝对路径。"""
    backend_root = Path(__file__).resolve().parents[1]
    return (backend_root / "data" / "biaoshu-pytest.db").resolve()

def _assert_isolated_test_db() -> None:
    """用途：解析 SQLAlchemy engine.url.database 后比较绝对路径；禁止子串自证。"""
    db_name = engine.url.database
    assert db_name is not None, "业务红：测试库 database 不得为空"
    actual = Path(db_name)
    if not actual.is_absolute():
        # sqlite 相对路径相对启动 cwd（backend）
        actual = (Path.cwd() / actual).resolve()
    else:
        actual = actual.resolve()
    expected = _expected_pytest_db_path()
    assert actual == expected, (
        f"业务红：必须使用隔离测试库绝对路径 equal，actual={actual} expected={expected}"
    )
    # 硬拒绝真实业务库文件名（路径组件）
    assert actual.name == "biaoshu-pytest.db", (
        f"业务红：测试库文件名必须为 biaoshu-pytest.db，actual={actual.name}"
    )

@pytest.fixture(autouse=True)
def _v1n_task_isolation_and_fuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """用途：每测独立 TEMP uploads + 清理；Token/manifest 清空；socket/HTTP 熔断；库路径门。"""
    get_settings.cache_clear()
    _assert_isolated_test_db()

    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    upload_resolved = upload_root.resolve()
    _TRACKED_UPLOAD_ROOTS.append(upload_resolved)
    _track_temp(upload_resolved)

    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", str(upload_resolved))
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(MANIFEST_ENV, raising=False)
    if hasattr(settings, "remote_mineru_token"):
        monkeypatch.setattr(settings, "remote_mineru_token", "")
    if hasattr(settings, "managed_ocr_manifest_path"):
        monkeypatch.setattr(settings, "managed_ocr_manifest_path", "")

    _NET_GUARD_HITS.clear()
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    _LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def _is_loopback_host(host: object) -> bool:
        h = str(host or "").strip().lower().strip("[]")
        return h in _LOOPBACK or h.startswith("127.")

    def _blocked_create_connection(address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_create_connection(address, *a, **k)
        msg = f"外网熔断：禁止 create_connection({host!r})"
        _NET_GUARD_HITS.append(msg)
        raise RuntimeError(msg)

    def _blocked_getaddrinfo(host, *a, **k):
        if _is_loopback_host(host):
            return real_getaddrinfo(host, *a, **k)
        msg = f"外网熔断：禁止 getaddrinfo({host!r})"
        _NET_GUARD_HITS.append(msg)
        raise RuntimeError(msg)

    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    real_sock_connect = socket.socket.connect
    real_sock_connect_ex = socket.socket.connect_ex

    def _blocked_sock_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_sock_connect(self, address)
        msg = f"外网熔断：禁止 socket.connect({host!r})"
        _NET_GUARD_HITS.append(msg)
        raise RuntimeError(msg)

    def _blocked_sock_connect_ex(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if _is_loopback_host(host):
            return real_sock_connect_ex(self, address)
        msg = f"外网熔断：禁止 socket.connect_ex({host!r})"
        _NET_GUARD_HITS.append(msg)
        return 1

    monkeypatch.setattr(socket.socket, "connect", _blocked_sock_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_sock_connect_ex)

    real_client_init = httpx.Client.__init__

    def _guarded_client_init(self, *a, **k):
        transport = k.get("transport")
        if transport is None:
            msg = "外网熔断：httpx.Client 未注入安全 transport"
            _NET_GUARD_HITS.append(msg)
            raise RuntimeError(msg)
        tname = type(transport).__name__
        if not (
            isinstance(transport, httpx.MockTransport)
            or "ASGI" in tname
            or "WSGI" in tname
            or "Mock" in tname
            or "TestClient" in tname
        ):
            msg = f"外网熔断：非法 transport={tname}"
            _NET_GUARD_HITS.append(msg)
            raise RuntimeError(msg)
        return real_client_init(self, *a, **k)

    monkeypatch.setattr(httpx.Client, "__init__", _guarded_client_init)

    real_async_init = httpx.AsyncClient.__init__

    def _guarded_async_init(self, *a, **k):
        transport = k.get("transport")
        if transport is None:
            msg = "外网熔断：httpx.AsyncClient 未注入安全 transport"
            _NET_GUARD_HITS.append(msg)
            raise RuntimeError(msg)
        tname = type(transport).__name__
        if not (
            isinstance(transport, httpx.MockTransport)
            or "ASGI" in tname
            or "WSGI" in tname
            or "Mock" in tname
            or "TestClient" in tname
        ):
            msg = f"外网熔断：AsyncClient 非法 transport={tname}"
            _NET_GUARD_HITS.append(msg)
            raise RuntimeError(msg)
        return real_async_init(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _guarded_async_init)

    yield upload_resolved

    roots = list(_TEMP_ROOTS)
    for r in roots:
        _cleanup_temp(r)
    left = [p for p in roots if p.exists()]
    _TEMP_ROOTS.clear()
    # upload_root 由 tmp_path 管理，仍断言跟踪列表非空且本测根曾登记
    assert upload_resolved in _TRACKED_UPLOAD_ROOTS
    _TRACKED_UPLOAD_ROOTS.clear()
    assert left == [], f"TEMP 未清理: {left}"
    get_settings.cache_clear()

@pytest.fixture(autouse=True)
def _reset_engines():
    parse_engines.reset_registry()
    yield
    parse_engines.reset_registry()

# ---------------------------------------------------------------------------
# 懒加载 / Settings / 旁路
# ---------------------------------------------------------------------------

def _client_available() -> bool:
    found = False
    try:
        found = importlib.util.find_spec(CLIENT_MOD) is not None
    except (ModuleNotFoundError, ValueError):
        found = False
    return found

def _load_client() -> Any:
    assert _client_available(), (
        f"业务红：缺少 {CLIENT_MOD}（remote_mineru 客户端未实现）"
    )
    return importlib.import_module(CLIENT_MOD)

def _assert_remote_settings_field() -> None:
    """
    Q19：Settings 仅通过唯一 validation_alias=BIAOSHU_REMOTE_MINERU_TOKEN；
    精确 model_fields 别名类型与字符串；_env_file=None 隔离。
    """
    from app.core.config import Settings

    # 默认空
    s0 = Settings(_env_file=None)
    assert hasattr(s0, "remote_mineru_token"), (
        "业务红：Settings 缺少 remote_mineru_token 字段"
    )
    raw0 = getattr(s0, "remote_mineru_token")
    normalized0 = "" if raw0 is None else str(raw0).strip()
    assert normalized0 == "", "业务红：默认 remote_mineru_token 必须为空"

    # Q19：validation_alias 精确为单字符串 BIAOSHU_REMOTE_MINERU_TOKEN
    field = Settings.model_fields.get("remote_mineru_token")
    assert field is not None, "业务红：model_fields 缺少 remote_mineru_token"
    alias = field.validation_alias
    assert alias == TOKEN_ENV, (
        f"业务红：validation_alias 必须精确为单字符串 {TOKEN_ENV!r}，actual={alias!r} type={type(alias)}"
    )
    assert isinstance(alias, str), (
        f"业务红：validation_alias 必须为 str（禁止 AliasChoices 多别名），actual={type(alias)}"
    )

    # 别名注入成功
    s1 = Settings(**{TOKEN_ENV: FAKE_TOKEN}, _env_file=None)
    assert getattr(s1, "remote_mineru_token") == FAKE_TOKEN, (
        "业务红：validation_alias 必须接受 BIAOSHU_REMOTE_MINERU_TOKEN"
    )

    # 字段名注入被拒绝或忽略（禁止条件 return 逃逸）
    field_injected = False
    try:
        s2 = Settings(remote_mineru_token="should-not-work-via-field-name", _env_file=None)
        field_injected = True
    except ValidationError:
        field_injected = False
    if field_injected:
        val = getattr(s2, "remote_mineru_token", "")
        assert val != "should-not-work-via-field-name", (
            "业务红：populate_by_name 必须为 False，禁止字段名注入 Token"
        )
    else:
        # ValidationError 路径：字段名注入被拒，符合契约
        pass

def _require_task_remote_bypass_behavior(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用途：行为证据 remote 分流，禁止仅源码子串。"""
    mod = _load_client()
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    get_settings.cache_clear()

    out_cls = _require_attr(mod, "RemoteParseOutput")
    src_cls = _require_attr(mod, "RemoteSource")
    calls: list[Any] = []

    def boom_engine(*a, **k):
        raise AssertionError("remote 旁路不得调用 parse_engines")

    def boom_managed(*a, **k):
        raise AssertionError("remote 旁路不得调用 managed")

    # 一调用即失败
    monkeypatch.setattr(parse_engines, "get_engine", boom_engine)
    if hasattr(parse_engines, "resolve_engine_name"):
        monkeypatch.setattr(parse_engines, "resolve_engine_name", boom_engine)
    managed_spec = importlib.util.find_spec(
        "app.services.managed_parse_runtime_service"
    )
    if managed_spec is not None:
        managed_svc = importlib.import_module(
            "app.services.managed_parse_runtime_service"
        )
        if hasattr(managed_svc, "run_managed_parse"):
            monkeypatch.setattr(managed_svc, "run_managed_parse", boom_managed)

    def fake_run(sources, *, token, cancel_check, **kwargs):
        calls.append({"sources": sources, "token": token})
        assert token == FAKE_TOKEN
        assert callable(cancel_check)
        assert len(sources) == 1
        s0 = sources[0]
        # Q15：精确类，禁止 duck-type or 放行
        assert isinstance(s0, src_cls), (
            f"业务红：RemoteSource 必须精确类，actual={type(s0)}"
        )
        assert hasattr(s0, "path") and hasattr(s0, "filename") and hasattr(s0, "expected_size")
        md = "# REMOTE_ROUTE_OK\n"
        return out_cls(markdown=md, file_count=1, chars=len(md))

    monkeypatch.setattr(mod, "run_remote_mineru_parse", fake_run)
    import app.services.task_service as ts

    if hasattr(ts, "run_remote_mineru_parse"):
        monkeypatch.setattr(ts, "run_remote_mineru_parse", fake_run)
    # 可能以 remote_mineru_client 模块引用
    if hasattr(ts, "remote_mineru_client"):
        monkeypatch.setattr(ts.remote_mineru_client, "run_remote_mineru_parse", fake_run)

    pid = _create_project(client, "V1N-route")
    up = _upload(client, pid, "route.pdf", b"%PDF-route", "application/pdf")
    body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    assert len(calls) == 1, f"业务红：remote runner 必须精确一次，actual={len(calls)}"
    assert body["result"]["engine"] == ENGINE
    # R12：path/filename/expected_size 与 DB+TEMP+no-follow stat 对齐
    # 禁止 original_name/fallback/startswith/可选路径
    s0 = calls[0]["sources"][0]
    upload_root = Path(get_settings().upload_dir).resolve()
    src_path = Path(s0.path).resolve()
    assert src_path.exists(), f"业务红：RemoteSource.path 必须存在: {src_path}"
    # 严格位于 TEMP upload 根：Path.is_relative_to（禁止 startswith 子串自证）
    assert src_path.is_relative_to(upload_root), (
        f"业务红：RemoteSource.path 必须严格位于本测 TEMP upload 根，"
        f"path={src_path} root={upload_root}"
    )
    # no-follow stat
    try:
        st_size = src_path.stat(follow_symlinks=False).st_size
    except TypeError:
        st_size = os.stat(src_path, follow_symlinks=False).st_size
    assert int(s0.expected_size) == int(st_size), (
        f"业务红：expected_size 必须等于 no-follow stat，{s0.expected_size}!={st_size}"
    )
    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
        ).first()
        assert row is not None
        # filename 精确等于 DB filename（禁止 original_name/fallback）
        assert s0.filename == row.filename, (
            f"业务红：filename 必须精确等于 ProjectFileRow.filename，"
            f"actual={s0.filename!r} db={row.filename!r}"
        )
        from app.services import file_service

        stored = Path(
            file_service.resolve_path(get_settings(), pid, row.stored_name)
        ).resolve()
        assert src_path == stored, (
            f"业务红：path 必须精确等于 resolve_path，{src_path}!={stored}"
        )
        assert stored.is_relative_to(upload_root)
    finally:
        db.close()
    get_settings.cache_clear()

def _require_attr(obj: Any, name: str) -> Any:
    assert hasattr(obj, name), f"业务红：缺少属性/函数 {name}"
    return getattr(obj, name)


def _get_run_fn(mod: Any):
    """
    T1：本文件内最小 helper，验证 production callable。
    禁止从另一个测试模块导入；globals 不共享。
    """
    fn = getattr(mod, "run_remote_mineru_parse", None)
    assert callable(fn), "业务红：缺少精确入口 run_remote_mineru_parse"
    sig = inspect.signature(fn)
    names = [p.name for p in sig.parameters.values()]
    assert names[:1] == ["sources"], f"业务红：首参必须 sources，actual={names}"
    for required_kw in ("token", "cancel_check"):
        assert required_kw in names, f"业务红：必须包含 keyword {required_kw}"
    assert "resolve_addresses_fn" in names, (
        "业务红：必须支持 keyword resolve_addresses_fn（可注入解析，禁止真实 DNS）"
    )
    for p in sig.parameters.values():
        assert p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ), f"业务红：禁止 *args/**kwargs，found={p.name}"
    return fn


# 固定公网 resolver 用假公网 IP（ipaddress.is_global=True）
_TASK_FAKE_PUBLIC_IP = "8.8.8.8"


def _task_default_public_resolver(host: str) -> list[str]:
    return [_TASK_FAKE_PUBLIC_IP]


# ---------------------------------------------------------------------------
# HTTP 小夹具
# ---------------------------------------------------------------------------

def _create_project(client: TestClient, name: str = "V1N-remote") -> str:
    res = client.post("/api/projects", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]

def _upload(
    client: TestClient,
    pid: str,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    res = client.post(
        f"/api/projects/{pid}/files",
        files={"file": (filename, BytesIO(content), content_type)},
    )
    assert res.status_code == 201, res.text
    return res.json()

def _parse_remote(
    client: TestClient,
    pid: str,
    *,
    extra_payload: dict | None = None,
    sync: bool = True,
) -> dict:
    payload: dict[str, Any] = {
        "type": "parse",
        "payload": {"engine": ENGINE},
    }
    if extra_payload:
        payload["payload"].update(extra_payload)
    q = "sync=true" if sync else "sync=false"
    res = client.post(f"/api/projects/{pid}/tasks?{q}", json=payload)
    assert res.status_code == 201, res.text
    return res.json()

def _editor_md(client: TestClient, pid: str) -> str:
    return (
        client.get(f"/api/projects/{pid}/editor-state").json().get("parsedMarkdown")
        or ""
    )

def _project_snapshot(client: TestClient, pid: str) -> dict:
    return client.get(f"/api/projects/{pid}").json()

def _revision_count(pid: str) -> int:
    db = SessionLocal()
    try:
        return len(
            list(
                db.scalars(
                    select(EditorStateRevisionRow).where(
                        EditorStateRevisionRow.project_id == pid
                    )
                ).all()
            )
        )
    finally:
        db.close()

def _success_event_count(pid: str, task_id: str) -> int:
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskEventRow).where(
                    ProjectTaskEventRow.project_id == pid
                )
            ).all()
        )
        return sum(
            1
            for r in rows
            if getattr(r, "task_id", None) == task_id
            and getattr(r, "status", None) == "success"
            and int(getattr(r, "progress", -1) or -1) == 100
        )
    finally:
        db.close()

def _load_task_row(task_id: str) -> ProjectTaskRow:
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task_id)
        assert row is not None
        db.expunge(row)
        return row
    finally:
        db.close()

def _project_success_task_count(pid: str) -> int:
    """项目级 status=success 任务数（五域观察，不依赖调用方传 task_id）。"""
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskRow).where(ProjectTaskRow.project_id == pid)
            ).all()
        )
        return sum(1 for r in rows if getattr(r, "status", None) == "success")
    finally:
        db.close()

def _project_success_event_count(pid: str) -> int:
    """项目级 progress=100 且 status=success 的 task-event 数。"""
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskEventRow).where(
                    ProjectTaskEventRow.project_id == pid
                )
            ).all()
        )
        return sum(
            1
            for r in rows
            if getattr(r, "status", None) == "success"
            and int(getattr(r, "progress", -1) or -1) == 100
        )
    finally:
        db.close()

def _snapshot_domains(
    client: TestClient, pid: str, *, task_id: str | None = None
) -> dict[str, Any]:
    """
    Q12 五域：md/rev/project status/step + 项目级 success task/event；
    若绑定 task_id 另附该任务终态/result。
    """
    proj = _project_snapshot(client, pid)
    out: dict[str, Any] = {
        "md": _editor_md(client, pid),
        "rev": _revision_count(pid),
        "status": proj.get("status"),
        "step": proj.get("technicalPlanStep"),
        "success_tasks": _project_success_task_count(pid),
        "success_events": _project_success_event_count(pid),
        "task_status": None,
        "task_result": None,
        "task_id": task_id,
    }
    if task_id:
        row = _load_task_row(task_id)
        out["task_status"] = row.status
        out["task_result"] = getattr(row, "result_json", None)
    return out

def _assert_domains_unchanged(
    client: TestClient,
    pid: str,
    before: dict[str, Any],
    *,
    label: str,
    task_id: str | None = None,
) -> None:
    after = _snapshot_domains(client, pid, task_id=task_id or before.get("task_id"))
    # 比较核心五域（忽略 task_id 绑定时的终态字段若 before 未绑）
    keys = ("md", "rev", "status", "step", "success_tasks", "success_events")
    b = {k: before.get(k) for k in keys}
    a = {k: after.get(k) for k in keys}
    assert a == b, f"业务红：{label} 后五域被部分写回: {b} -> {a}"
    # 失败路径：不得新增 success task / 100 event
    assert after["success_tasks"] == before.get("success_tasks", 0)
    assert after["success_events"] == before.get("success_events", 0)

def _assert_fail_result(body: dict, *, code: str) -> None:
    """remote client/协议/Token/后缀类失败：精确二键 + 冻结中文。"""
    assert body["status"] == "failed"
    result = body.get("result")
    assert isinstance(result, dict), f"失败 result 必须为对象: {result!r}"
    assert set(result.keys()) == FAIL_KEYS, (
        f"失败 result 必须精确二键 engine/diagnosticCode，actual={set(result.keys())}"
    )
    assert result["engine"] == ENGINE
    assert result["diagnosticCode"] == code
    err = body.get("error") or ""
    expected = FIXED_MESSAGES.get(code, FIXED_MESSAGES["internal_error"])
    assert err == expected, (
        f"业务红：error 必须精确等于测试冻结表[{code}]={expected!r}，actual={err!r}"
    )
    # production 若存在则必须与冻结表对齐（不得反向以 production 自证测试）
    if _client_available():
        mod = _load_client()
        msg_fn = getattr(mod, "message_for_code", None)
        if callable(msg_fn):
            assert msg_fn(code) == expected
    assert "解析引擎不可用" not in err, (
        "业务红：remote_mineru 不得回落到 parse_engines 不可用文案"
    )

def _assert_post_get_error_consistent(
    client: TestClient, pid: str, body: dict
) -> None:
    tid = body["id"]
    got = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got.status_code == 200
    data = got.json()
    assert data["status"] == body["status"]
    assert data.get("error") == body.get("error")
    assert data.get("result") == body.get("result")

def _privacy_canaries(extra: list[str] | None = None) -> list[str]:
    base = [
        FAKE_TOKEN,
        "Bearer ",
        FAKE_BATCH,
        FAKE_DATA_ID,
        FAKE_PRESIGN,
        FAKE_ZIP_URL,
        FAKE_CLOUD_ERR,
        FAKE_TRACE,
        ORIGINAL_FILENAME,
        BODY_CANARY,
    ]
    if extra:
        base.extend(extra)
    return base

def _assert_no_privacy_leak(text: str | None, *, where: str, extra: list[str] | None = None) -> None:
    raw = text or ""
    for frag in _privacy_canaries(extra):
        if not frag:
            continue
        assert frag not in raw, f"隐私泄漏 at {where}: {frag!r} in {raw[:400]!r}"

def _scan_task_surfaces(
    client: TestClient,
    pid: str,
    body: dict,
    *,
    extra: list[str] | None = None,
    caplog_text: str = "",
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> None:
    """
    R10/Q18：blob 只来自真实 API/DB/事件/日志；禁止人造 TEMP/path/body canary 拼入冲淡。
    项目 task-events 页必须真实 200；SSE 经可注入 max/poll 短参短退出，禁止固定 11 分钟长轮询。
    """
    from app.main import app as _app
    import app.api.project_task_events as pte_mod

    tid = body["id"]
    got = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got.status_code == 200, f"业务红：任务 GET 必须 200，actual={got.status_code}"
    data = got.json()
    blob_parts = [
        json.dumps(body, ensure_ascii=False),
        json.dumps(data, ensure_ascii=False),
        str(body.get("error") or ""),
        str(body.get("message") or ""),
        str(body.get("result") or ""),
        caplog_text or "",
    ]
    row = _load_task_row(tid)
    td = task_service.task_to_dict(row)
    blob_parts.append(json.dumps(td, ensure_ascii=False))
    # 单任务 events 列表 API（既有路由，开放模式下通常 200）
    ev = client.get(f"/api/projects/{pid}/tasks/{tid}/events")
    if ev.status_code == 200:
        blob_parts.append(ev.text)
    # 项目任务列表
    lst = client.get(f"/api/projects/{pid}/tasks")
    assert lst.status_code == 200, f"业务红：任务列表必须 200，actual={lst.status_code}"
    blob_parts.append(lst.text)

    # task-events 页 / SSE 需 bid_writer scope；测试期 override 依赖以走真实 handler+DB
    # （不伪造事件体；仅打通鉴权门，响应仍来自真实服务层）
    def _scope_ws() -> str:
        return "ws_local"

    prev_override = _app.dependency_overrides.get(
        pte_mod.require_project_task_events_scope
    )
    _app.dependency_overrides[pte_mod.require_project_task_events_scope] = _scope_ws
    # 短 SSE：可注入 max/poll，禁止 11 分钟长轮询
    prev_max = pte_mod._SSE_MAX_SECONDS
    prev_poll = pte_mod._SSE_POLL_SECONDS
    prev_hb = pte_mod._SSE_HEARTBEAT_SECONDS
    pte_mod._SSE_MAX_SECONDS = 0.05
    pte_mod._SSE_POLL_SECONDS = 0.01
    pte_mod._SSE_HEARTBEAT_SECONDS = 1.0
    try:
        pev = client.get(f"/api/projects/{pid}/task-events")
        assert pev.status_code == 200, (
            f"业务红：task-events 页必须真实 200，actual={pev.status_code} body={pev.text[:200]}"
        )
        blob_parts.append(pev.text)
        sse = client.get(f"/api/projects/{pid}/task-events/stream")
        assert sse.status_code == 200, (
            f"业务红：task-events SSE 必须 200，actual={sse.status_code}"
        )
        blob_parts.append(sse.text[:4000])
    finally:
        pte_mod._SSE_MAX_SECONDS = prev_max
        pte_mod._SSE_POLL_SECONDS = prev_poll
        pte_mod._SSE_HEARTBEAT_SECONDS = prev_hb
        if prev_override is None:
            _app.dependency_overrides.pop(
                pte_mod.require_project_task_events_scope, None
            )
        else:
            _app.dependency_overrides[
                pte_mod.require_project_task_events_scope
            ] = prev_override

    # DB：任务字段 + 事件行（真实 ORM 值，不拼人造 canary）
    for attr in ("error", "message", "result_json", "payload_json", "status"):
        blob_parts.append(str(getattr(row, attr, "") or ""))
    db = SessionLocal()
    try:
        events = list(
            db.scalars(
                select(ProjectTaskEventRow).where(
                    ProjectTaskEventRow.project_id == pid
                )
            ).all()
        )
        for e in events:
            for attr in ("status", "message", "payload_json", "task_id"):
                blob_parts.append(str(getattr(e, attr, "") or ""))
    finally:
        db.close()
    # R10：不把 upload_dir/TEMP/人造 path 拼进 blob 冲淡；extra 仅作禁止子串
    blob = "\n".join(blob_parts)
    _assert_no_privacy_leak(blob, where="task-surfaces", extra=extra)

def _patch_remote_run(monkeypatch: pytest.MonkeyPatch, mod: Any, fn: Any) -> None:
    monkeypatch.setattr(mod, "run_remote_mineru_parse", fn)
    import app.services.task_service as ts

    if hasattr(ts, "run_remote_mineru_parse"):
        monkeypatch.setattr(ts, "run_remote_mineru_parse", fn)
    if hasattr(ts, "remote_mineru_client"):
        monkeypatch.setattr(ts.remote_mineru_client, "run_remote_mineru_parse", fn)

def _enable_fake_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """仅 env alias；禁止手工赋值字段替代 env（T11）。"""
    monkeypatch.setenv(TOKEN_ENV, FAKE_TOKEN)
    get_settings.cache_clear()
    # 不 monkeypatch settings.remote_mineru_token 字段；只认 BIAOSHU_REMOTE_MINERU_TOKEN

# ---------------------------------------------------------------------------
# AST 自守卫
# ---------------------------------------------------------------------------


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


# R9-T1：单一 authoritative 四通道集合；scanner/helper 全路径引用，禁止分叉硬编码
_V1N_SKIP_CHANNELS: frozenset[str] = frozenset(
    {"skip", "xfail", "skipif", "importorskip"}
)


def _getattr_skip_name(
    arg_node: ast.AST, *, channels: frozenset[str] | None = None
) -> str | None:
    """折叠 getattr 第二参数；命中 skip 族通道则返回名称。"""
    name = _ast_fold_str(arg_node)
    ch = _V1N_SKIP_CHANNELS if channels is None else channels
    if name in ch:
        return name
    return None


def _task_const_compare_is_statically_true(node: ast.Compare) -> bool:
    """R6 兼容入口：委托 R7 安全折叠。"""
    return _ast_safe_fold(node) is True


def _ast_contains_boolop_or(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or):
            return True
    return False

def _ast_scan_task_fake_green(
    tree: ast.AST,
    *,
    guard_names: frozenset[str],
    skip_channels: frozenset[str] | None = None,
) -> list[str]:
    """
    T6/C9/R9-T1：与 client 同级 AST 守卫；except 子树 walk、marks、相同左右 AST、match return。
    skip_channels：测试专用通道注入 seam；None=完整四通道 authoritative 集合。
    """
    bad: list[str] = []
    channels: frozenset[str] = (
        _V1N_SKIP_CHANNELS if skip_channels is None else frozenset(skip_channels)
    )

    def _hit_getattr(arg_node: ast.AST) -> str | None:
        return _getattr_skip_name(arg_node, channels=channels)

    def _walk(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name in guard_names:
                    continue
                _inspect_function(stmt)
                continue
            if isinstance(stmt, ast.ClassDef):
                _walk(stmt.body)
                continue
            _inspect_node(stmt)

    def _scan_marks_value(node: ast.AST, lineno: int) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in channels:
                bad.append(f"L{lineno}: marks 通道含 {child.attr}")
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                if child.func.attr in channels:
                    bad.append(f"L{lineno}: marks 通道调用 {child.func.attr}()")
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "getattr"
                and len(child.args) >= 2
            ):
                gname = _hit_getattr(child.args[1])
                if gname is not None:
                    bad.append(f"L{lineno}: marks getattr 通道 {gname!r}")

    def _scan_test_body(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Return):
                bad.append(f"L{stmt.lineno}: test_* 无条件/提前 return")
            elif isinstance(stmt, ast.If):
                _scan_test_body(stmt.body)
                _scan_test_body(stmt.orelse)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                _scan_test_body(stmt.body)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                _scan_test_body(stmt.body)
                _scan_test_body(stmt.orelse)
            elif isinstance(stmt, ast.While):
                _scan_test_body(stmt.body)
                _scan_test_body(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                _scan_test_body(stmt.body)
                for h in stmt.handlers:
                    _scan_test_body(h.body)
                _scan_test_body(stmt.orelse)
                _scan_test_body(stmt.finalbody)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    _scan_test_body(case.body)

    def _inspect_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Attribute) and dec.attr in channels:
                bad.append(f"L{fn.lineno}: 禁止装饰器 mark.{dec.attr}")
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr in channels:
                    bad.append(f"L{fn.lineno}: 禁止装饰器 mark.{dec.func.attr}()")
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "getattr"
                and len(dec.args) >= 2
            ):
                gname = _hit_getattr(dec.args[1])
                if gname is not None:
                    bad.append(f"L{fn.lineno}: 禁止装饰器 getattr mark.{gname}")
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "marks" and kw.value is not None:
                        _scan_marks_value(kw.value, fn.lineno)
                _scan_marks_value(dec, fn.lineno)
        if fn.name.startswith("test_"):
            _scan_test_body(fn.body)
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler):
                for sub in ast.walk(node):
                    if sub is node:
                        continue
                    if isinstance(sub, ast.Return):
                        bad.append(f"L{sub.lineno}: except 子树 return in {fn.name}")
                    if isinstance(sub, ast.Continue):
                        bad.append(f"L{sub.lineno}: except 子树 continue in {fn.name}")
        # R6-C6：getattr 别名 / 绑定 skip（通道集合引用 authoritative channels）
        getattr_aliases: set[str] = set()
        skip_bound_names: set[str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            if isinstance(node.value, ast.Name) and node.value.id == "getattr":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        getattr_aliases.add(t.id)
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and (
                    node.value.func.id == "getattr"
                    or node.value.func.id in getattr_aliases
                )
                and len(node.value.args) >= 2
                and _hit_getattr(node.value.args[1]) is not None
            ):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        skip_bound_names.add(t.id)
        if getattr_aliases or skip_bound_names:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    isinstance(func, ast.Call)
                    and isinstance(func.func, ast.Name)
                    and func.func.id in getattr_aliases
                    and len(func.args) >= 2
                ):
                    gname = _hit_getattr(func.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 别名调用 pytest.{gname}"
                        )
                if isinstance(func, ast.Name) and func.id in skip_bound_names:
                    bad.append(f"L{node.lineno}: 禁止绑定 skip 别名调用 {func.id}()")
                if (
                    isinstance(func, ast.Name)
                    and func.id in getattr_aliases
                    and len(node.args) >= 2
                ):
                    gname = _hit_getattr(node.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 别名取 pytest.{gname}"
                        )
        _inspect_node(fn)

    def _inspect_node(root: ast.AST) -> None:
        for node in ast.walk(root):
            if isinstance(node, ast.Assert) and node.test is not None:
                if _ast_contains_boolop_or(node.test):
                    bad.append(f"L{node.lineno}: assert 含 BoolOp Or（含嵌套/推导）")
                # R7-C4b：与 client 对称的安全常量折叠恒真门
                if _assert_test_is_statically_truthy(node.test):
                    bad.append(f"L{node.lineno}: 禁止可静态求值恒真 assert")
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
                    bad.append(f"L{node.lineno}: if False 条件分支")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "pytest" and func.attr in channels:
                        bad.append(f"L{node.lineno}: 禁止 pytest.{func.attr}()")
                    if (
                        func.value.id == "pytest"
                        and func.attr == "param"
                    ):
                        for kw in node.keywords:
                            if kw.arg == "marks" and kw.value is not None:
                                _scan_marks_value(kw.value, node.lineno)
                if isinstance(func, ast.Name) and func.id in channels:
                    bad.append(f"L{node.lineno}: 禁止裸 {func.id}()")
                if (
                    isinstance(func, ast.Call)
                    and isinstance(func.func, ast.Name)
                    and func.func.id == "getattr"
                    and len(func.args) >= 2
                ):
                    gname = _hit_getattr(func.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr pytest.{gname}() 通道"
                        )
                if (
                    isinstance(func, ast.Name)
                    and func.id == "getattr"
                    and len(node.args) >= 2
                ):
                    gname = _hit_getattr(node.args[1])
                    if gname is not None:
                        bad.append(
                            f"L{node.lineno}: 禁止 getattr 取 pytest.{gname}"
                        )

    if isinstance(tree, ast.Module):
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    if isinstance(t, ast.Name) and t.id == "pytestmark":
                        bad.append(f"L{stmt.lineno}: 禁止模块级 pytestmark")
                        if stmt.value is not None:
                            _scan_marks_value(stmt.value, stmt.lineno)
        _walk(tree.body)
    else:
        _inspect_node(tree)
    return bad


def _task_ast_scanner_synthetic_self_test() -> None:
    """R6-C6/R7-C4b：与 client 对称；单一违规 + 精确 reason + 动态负样本。"""
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
        ("assert_same_ast", "def test_w():\n x=1\n assert x==x\n", "左右 AST 完全相同"),
        ("match_return", "def test_m():\n match 1:\n  case 1:\n   return\n", "return"),
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
            "getattr_concat_xfail",
            'import pytest\ndef test_gx():\n getattr(pytest,"x"+"fail")("x")\n',
            "getattr",
        ),
        (
            "getattr_concat_skipif",
            'import pytest\n@pytest.mark.parametrize("a",[pytest.param(1,marks=getattr(pytest.mark,"sk"+"ipif"))])\ndef test_gsi(a):\n assert a==1\n',
            "getattr",
        ),
    ]
    for label, src, expected in samples:
        tree = ast.parse(src.encode("utf-8").decode("unicode_escape"))
        bad = _ast_scan_task_fake_green(tree, guard_names=frozenset())
        assert bad, f"业务红：task synthetic {label} 必须命中"
        joined = "\n".join(bad)
        assert expected in joined, (
            f"业务红：task synthetic {label} 必须命中 reason={expected!r}，bad={bad}"
        )
        assert "__NO_SUCH_REASON_R7__" not in joined

    # R8-A2/R9-T1：四通道 skip/xfail/skipif/importorskip 单目标样本 + 精确 reason
    four_channel: list[tuple[str, str, str]] = [
        (
            "skip",
            'import pytest\ndef test_c():\n getattr(pytest,"sk"+"ip")("r")\n',
            "skip",
        ),
        (
            "xfail",
            'import pytest\ndef test_c():\n getattr(pytest,"x"+"fail")("r")\n',
            "xfail",
        ),
        (
            "skipif",
            'import pytest\n@pytest.mark.parametrize("a",[pytest.param(1,marks=getattr(pytest.mark,"sk"+"ipif"))])\ndef test_c(a):\n assert a==1\n',
            "skipif",
        ),
        (
            "importorskip",
            'import pytest\ndef test_c():\n getattr(pytest,"importorskip")("n")\n',
            "importorskip",
        ),
    ]
    assert {ch for ch, _, _ in four_channel} == set(_V1N_SKIP_CHANNELS), (
        f"业务红：四通道样本集合必须等于 authoritative "
        f"{sorted(_V1N_SKIP_CHANNELS)}"
    )

    def _channel_reason_hit(joined: str, channel: str) -> bool:
        """精确通道归因：避免 skip 误匹配 skipif 子串。"""
        if f"pytest.{channel}" in joined:
            # pytest.skip 不得被 pytest.skipif 误伤
            if channel == "skip" and "pytest.skipif" in joined and "pytest.skip" not in joined.replace(
                "pytest.skipif", ""
            ):
                return False
            if channel == "skip":
                # 存在 pytest.skip 且其后不是 if
                idx = 0
                while True:
                    i = joined.find("pytest.skip", idx)
                    if i < 0:
                        break
                    rest = joined[i + len("pytest.skip") : i + len("pytest.skip") + 2]
                    if not rest.startswith("if"):
                        return True
                    idx = i + 1
                return False
            return True
        if f"通道 {channel!r}" in joined or f"通道 '{channel}'" in joined:
            return True
        if f"mark.{channel}" in joined:
            if channel == "skip":
                idx = 0
                while True:
                    i = joined.find("mark.skip", idx)
                    if i < 0:
                        break
                    rest = joined[i + len("mark.skip") : i + len("mark.skip") + 2]
                    if not rest.startswith("if"):
                        return True
                    idx = i + 1
                return False
            return True
        # 裸通道名作为独立 token（首尾非字母）
        return re.search(rf"(?<![A-Za-z]){re.escape(channel)}(?![A-Za-z])", joined) is not None

    for ch, src, needle in four_channel:
        tree = ast.parse(src.encode("utf-8").decode("unicode_escape"))
        bad = _ast_scan_task_fake_green(tree, guard_names=frozenset())
        assert bad, f"业务红：四通道 {ch} 必须独立命中"
        joined = "\n".join(bad)
        assert _channel_reason_hit(joined, needle), (
            f"业务红：四通道 {ch} 必须可归因 needle={needle!r}，bad={bad}"
        )
        for other_ch, _, other_needle in four_channel:
            if other_ch == ch:
                continue
            assert not _channel_reason_hit(joined, other_needle), (
                f"业务红：单目标 {ch} 不得冒充 {other_ch}，bad={bad}"
            )

    # R9-T1：对四通道分别「仅删除当前一项」后跑完整 scanner（禁止只调局部 drop helper）
    for ch, src, needle in four_channel:
        tree = ast.parse(src.encode("utf-8").decode("unicode_escape"))
        reduced = frozenset(c for c in _V1N_SKIP_CHANNELS if c != ch)
        assert ch not in reduced and len(reduced) == 3
        # 完整 scanner + 缩通道：当前违规不得再被该通道命中
        bad_drop = _ast_scan_task_fake_green(
            tree, guard_names=frozenset(), skip_channels=reduced
        )
        joined_drop = "\n".join(bad_drop)
        assert not _channel_reason_hit(joined_drop, needle), (
            f"业务红：删除通道 {ch} 后完整 scanner 不得再命中 reason={needle!r}，"
            f"bad={bad_drop}"
        )
        # 同步：getattr 注入 seam 对该名必须 None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
            ):
                full_name = _getattr_skip_name(
                    node.args[1], channels=_V1N_SKIP_CHANNELS
                )
                if full_name == ch:
                    assert (
                        _getattr_skip_name(node.args[1], channels=reduced) is None
                    ), f"业务红：删除 {ch} 后 _getattr_skip_name 必须 None"
        # 其余三项各自仍命中且不得冒充当前 reason
        for other_ch, other_src, other_needle in four_channel:
            if other_ch == ch:
                continue
            tree_o = ast.parse(other_src.encode("utf-8").decode("unicode_escape"))
            bad_o = _ast_scan_task_fake_green(
                tree_o, guard_names=frozenset(), skip_channels=reduced
            )
            assert bad_o, (
                f"业务红：删除 {ch} 后通道 {other_ch} 完整 scanner 仍须命中，bad 空"
            )
            joined_o = "\n".join(bad_o)
            assert _channel_reason_hit(joined_o, other_needle), (
                f"业务红：删除 {ch} 后 {other_ch} 必须精确 reason={other_needle!r}，"
                f"bad={bad_o}"
            )
            assert not _channel_reason_hit(joined_o, ch), (
                f"业务红：删除 {ch} 后 {other_ch} 样本不得冒充 reason={ch!r}，"
                f"bad={bad_o}"
            )

    for label, src in [
        ("dyn_call", "def test_x():\n def fn():\n  return 1\n assert fn()\n"),
        ("dyn_attr", "def test_x():\n class O:\n  attr=1\n obj=O()\n assert obj.attr\n"),
        ("dyn_sub", "def test_x():\n data=[1]\n assert data[0]\n"),
    ]:
        tree = ast.parse(src)
        bad = _ast_scan_task_fake_green(tree, guard_names=frozenset())
        for b in bad:
            assert "可静态求值恒真" not in b, (
                f"业务红：task 动态负样本 {label} 不得折叠恒真，bad={bad}"
            )
    # R8-A1：手工 Constant(Evil) Compare/Truth，魔术计数精确 0
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
    folded_evil = _ast_safe_fold(ast.Constant(value=evil))
    assert folded_evil is _AST_UNKNOWN, (
        f"业务红：task Constant(Evil) 必须 UNKNOWN，actual={folded_evil!r}"
    )
    assert _EvilConst.eq_n == 0 and _EvilConst.bool_n == 0 and _EvilConst.ne_n == 0
    folded_cmp = _ast_safe_fold(
        ast.Compare(
            left=ast.Constant(value=evil),
            ops=[ast.Eq()],
            comparators=[ast.Constant(value=1)],
        )
    )
    assert folded_cmp is _AST_UNKNOWN
    assert _EvilConst.eq_n == 0 and _EvilConst.bool_n == 0
    assert _ast_static_truthiness(_ast_safe_fold(ast.Constant(value=evil))) is None
    assert _EvilConst.bool_n == 0

    clean = ast.parse("def test_ok():\n x=1\n assert x==1\n")
    assert _ast_scan_task_fake_green(clean, guard_names=frozenset()) == []


def test_t0_ast_self_guard_no_fake_green_patterns():
    """T6：synthetic 恶意片段必须命中；真实文件扫后为[]。"""
    _task_ast_scanner_synthetic_self_test()
    path = Path(__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = _ast_scan_task_fake_green(
        tree,
        guard_names=frozenset(
            {
                "test_t0_ast_self_guard_no_fake_green_patterns",
                "_ast_scan_task_fake_green",
                "_ast_contains_boolop_or",
                "_task_const_compare_is_statically_true",
                "_assert_test_is_statically_truthy",
                "_ast_safe_fold",
                "_ast_safe_cmp",
                "_ast_static_truthiness",
                "_ast_fold_str",
                "_getattr_skip_name",
                "_const_compare_is_statically_true",
                "_task_ast_scanner_synthetic_self_test",
            }
        ),
    )
    assert bad == [], f"业务红：测试自守卫命中假绿模式: {bad}"


def test_t0_parse_engines_never_hosts_remote_mineru():
    names = parse_engines.list_registered_engines()
    assert ENGINE not in names
    assert "managed" not in names
    src_path = Path(parse_engines.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    bad_imports: list[str] = []
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                    bad_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "subprocess" or node.module.startswith("subprocess.")
            ):
                bad_imports.append(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "subprocess":
                    bad_calls.append(func.attr)
    assert bad_imports == [], f"parse_engines 禁止 import subprocess: {bad_imports}"
    assert bad_calls == [], f"parse_engines 禁止调用 subprocess: {bad_calls}"
    src = src_path.read_text(encoding="utf-8")
    assert ENGINE not in src

# ===========================================================================
# T1 Settings / 旁路
# ===========================================================================

def test_t1_settings_token_alias_only():
    _assert_remote_settings_field()

def test_t2_remote_route_behavior_evidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _require_task_remote_bypass_behavior(client, monkeypatch)

# ===========================================================================
# T3 Token / 类型门
# ===========================================================================

def test_t3_token_missing_zero_http_and_fixed_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    mod = _load_client()
    http_calls: list[str] = []

    def guard_run(*a, **k):
        http_calls.append("run")
        raise AssertionError("Token 缺失时不得进入 run_remote_mineru_parse")

    _patch_remote_run(monkeypatch, mod, guard_run)

    pid = _create_project(client, "V1N-no-token")
    up = _upload(
        client,
        pid,
        ORIGINAL_FILENAME,
        b"%PDF-1.4 fake",
        "application/pdf",
    )
    temp_canary = str(Path(get_settings().upload_dir).resolve())
    with caplog.at_level(logging.DEBUG):
        body = _parse_remote(client, pid)
    assert http_calls == [], f"业务红：Token 缺失零 HTTP/零 run，actual={http_calls}"
    _assert_fail_result(body, code=FIXED_TOKEN_UNCONFIGURED)
    _scan_task_surfaces(
        client, pid, body, extra=[temp_canary, up.get("storedName", "")], caplog_text=caplog.text
    )
    _assert_no_privacy_leak(caplog.text, where="caplog-token-missing", extra=[temp_canary])
    assert _editor_md(client, pid) == ""
    assert _revision_count(pid) == 0

def test_t3b_token_blank_env_zero_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(TOKEN_ENV, "   ")
    get_settings.cache_clear()
    mod = _load_client()
    calls: list[str] = []

    def guard(*a, **k):
        calls.append("x")
        raise AssertionError("空白 Token 不得 run")

    _patch_remote_run(monkeypatch, mod, guard)
    pid = _create_project(client, "V1N-blank-token")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    body = _parse_remote(client, pid)
    assert calls == []
    _assert_fail_result(body, code=FIXED_TOKEN_UNCONFIGURED)
    get_settings.cache_clear()

@pytest.mark.parametrize("suffix", list(REJECT_SUFFIXES))
def test_t4_unsupported_source_zero_http(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    calls: list[str] = []

    def guard(*a, **k):
        calls.append("run")
        raise AssertionError("不支持类型不得调用 run")

    _patch_remote_run(monkeypatch, mod, guard)

    filename = f"doc{suffix}"
    content = b"<html>x</html>" if suffix == ".html" else b"hello"
    ctype = "text/html" if suffix == ".html" else "application/octet-stream"
    pid = _create_project(client, f"V1N-unsup-{suffix}")
    _upload(client, pid, filename, content, ctype)
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert calls == []
    _assert_fail_result(body, code=FIXED_UNSUPPORTED)
    _assert_domains_unchanged(client, pid, before, label=f"unsupported-{filename}")
    _scan_task_surfaces(client, pid, body)
    get_settings.cache_clear()

@pytest.mark.parametrize("suffix", list(ALLOWED_SUFFIXES))
def test_t4b_allowed_suffix_reaches_runner_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, suffix: str
):
    """Q15：14 个允许后缀小写全覆盖；runner 精确一次。"""
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    calls: list[str] = []

    def fake_run(sources, *, token, cancel_check, **kwargs):
        calls.append(sources[0].filename)
        md = f"# OK{suffix}\n"
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)
    pid = _create_project(client, f"V1N-allow-{suffix}")
    name = f"sample{suffix.lower()}"
    _upload(client, pid, name, b"%PDF-or-bin", "application/octet-stream")
    body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    assert len(calls) == 1
    assert calls[0] == name
    get_settings.cache_clear()

@pytest.mark.parametrize(
    "name",
    [
        "sample.PDF",  # upper 代表
        "sample.PdF",  # mixed 代表
    ],
)
def test_t4b_allowed_suffix_case_representatives(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, name: str
):
    """Q15：大小写代表项（非 14×矩阵膨胀）。"""
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    calls: list[str] = []

    def fake_run(sources, *, token, cancel_check, **kwargs):
        calls.append(sources[0].filename)
        return out_cls(markdown="# CASE\n", file_count=1, chars=7)

    _patch_remote_run(monkeypatch, mod, fake_run)
    pid = _create_project(client, f"V1N-case-{name}")
    _upload(client, pid, name, b"%PDF-or-bin", "application/octet-stream")
    body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    assert len(calls) == 1
    assert calls[0] == name
    get_settings.cache_clear()

# ===========================================================================
# T5 共享输入门（remote 分支 runner=0）
# ===========================================================================

def test_t5_shared_gates_eleven_files_200mib_size_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    calls: list[str] = []

    def guard(*a, **k):
        calls.append("run")
        raise AssertionError("共享门失败不得 run")

    _patch_remote_run(monkeypatch, mod, guard)

    # 11 文件
    pid = _create_project(client, "V1N-11files")
    for i in range(11):
        _upload(client, pid, f"f{i:02d}.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert calls == []
    assert body["status"] == "failed"
    assert body.get("error") == FIXED_ERR_TOO_MANY_SOURCES
    # Q16：shared gate result 沿用既有 None 语义
    assert body.get("result") is None
    _assert_domains_unchanged(client, pid, before, label="11files", task_id=body["id"])

    # 200 MiB 总量门：DB 声明 + Path.stat seam 一致（T2，对齐 M2）
    pid2 = _create_project(client, "V1N-200mib")
    _upload(client, pid2, "big1.pdf", b"%PDF1", "application/pdf")
    _upload(client, pid2, "big2.pdf", b"%PDF2", "application/pdf")
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(select(ProjectFileRow).where(ProjectFileRow.project_id == pid2)).all()
        )
        assert len(rows) == 2
        for row in rows:
            row.size_bytes = 120 * 1024 * 1024
        db.commit()
        stored_names = [r.stored_name for r in rows]
    finally:
        db.close()
    from app.services import file_service
    settings = get_settings()
    targets = {
        str(file_service.resolve_path(settings, pid2, name).resolve()): 120 * 1024 * 1024
        for name in stored_names
    }
    import os as _os
    orig_stat = Path.stat
    stat_hits: list[str] = []

    def _stat(self, *a, **k):
        st = orig_stat(self, *a, **k)
        try:
            key = str(Path(self).resolve(strict=False))
        except OSError:
            key = str(self)
        for t, sz in targets.items():
            match = False
            try:
                match = Path(t).resolve() == Path(key).resolve()
            except OSError:
                match = False
            if match:
                stat_hits.append(t)
                return _os.stat_result(
                    (
                        st.st_mode,
                        st.st_ino,
                        st.st_dev,
                        st.st_nlink,
                        st.st_uid,
                        st.st_gid,
                        sz,
                        st.st_atime,
                        st.st_mtime,
                        st.st_ctime,
                    )
                )
        return st

    monkeypatch.setattr(Path, "stat", _stat)
    before2 = _snapshot_domains(client, pid2)
    body2 = _parse_remote(client, pid2)
    assert calls == []
    assert body2["status"] == "failed"
    assert body2.get("error") == FIXED_ERR_TOTAL_SIZE
    assert body2.get("result") is None
    assert len(set(stat_hits)) == 2, f"业务红：200MiB 门须 stat 两文件，hits={stat_hits}"
    _assert_domains_unchanged(client, pid2, before2, label="200mib", task_id=body2["id"])

    # 大小不一致
    pid3 = _create_project(client, "V1N-size-mismatch")
    _upload(client, pid3, "m.pdf", b"%PDF-small", "application/pdf")
    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid3)
        ).first()
        assert row is not None
        row.size_bytes = 999999
        db.commit()
    finally:
        db.close()
    before3 = _snapshot_domains(client, pid3)
    body3 = _parse_remote(client, pid3)
    assert calls == []
    assert body3["status"] == "failed"
    assert body3.get("error") == FIXED_ERR_SIZE_MISMATCH
    assert body3.get("result") is None
    _assert_domains_unchanged(client, pid3, before3, label="size-mismatch", task_id=body3["id"])
    get_settings.cache_clear()

# ===========================================================================
# T6 成功五域 / 多文件 ASC
# ===========================================================================

def test_t6_success_five_domains_and_result_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    md = f"# OK\n\n{BODY_CANARY}\n"

    def fake_run(sources, *, token, cancel_check, **kwargs):
        assert token == FAKE_TOKEN
        assert cancel_check is not None
        return out_cls(markdown=md, file_count=len(sources), chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    pid = _create_project(client, "V1N-success")
    _upload(client, pid, ORIGINAL_FILENAME, b"%PDF-ok", "application/pdf")
    before_rev = _revision_count(pid)
    temp_abs = str(Path(get_settings().upload_dir).resolve())
    with caplog.at_level(logging.DEBUG):
        body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    result = body["result"]
    assert set(result.keys()) == SUCCESS_KEYS
    assert result["engine"] == ENGINE
    assert result["fileCount"] == 1
    assert result["chars"] == len(_editor_md(client, pid))
    assert BODY_CANARY in _editor_md(client, pid)
    assert _revision_count(pid) == before_rev + 1
    assert _success_event_count(pid, body["id"]) == 1
    proj = _project_snapshot(client, pid)
    assert proj.get("status") == "analyzing"
    assert proj.get("technicalPlanStep") == 1
    assert BODY_CANARY not in json.dumps(result, ensure_ascii=False)
    _scan_task_surfaces(client, pid, body, extra=[temp_abs], caplog_text=caplog.text)
    for frag in [FAKE_TOKEN, ORIGINAL_FILENAME, temp_abs, "Bearer "]:
        assert frag not in caplog.text, f"成功路径 caplog 泄漏 {frag!r}"
    get_settings.cache_clear()

def test_t6b_multi_file_id_asc_with_separator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    order: list[str] = []
    seen_sources: list[Any] = []

    def fake_run(sources, *, token, cancel_check, **kwargs):
        # 按每个 RemoteSource.filename 返回唯一绑定正文；client/task 聚合
        parts = []
        seen_sources.clear()
        for s in sources:
            order.append(s.filename)
            seen_sources.append(s)
            parts.append(f"# BODY_FOR_{s.filename}")
        md = SOURCE_SEPARATOR.join(parts)
        return out_cls(markdown=md, file_count=len(sources), chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    pid = _create_project(client, "V1N-multi")
    _upload(client, pid, "first.pdf", b"1", "application/pdf")
    _upload(client, pid, "second.pdf", b"2", "application/pdf")

    # 同 created_at，以 id ASC 期望
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectFileRow)
                .where(ProjectFileRow.project_id == pid)
                .order_by(ProjectFileRow.id.asc())
            ).all()
        )
        assert len(rows) == 2
        same_ts = rows[0].created_at
        for r in rows:
            r.created_at = same_ts
        db.commit()
        expected_names = [r.filename for r in rows]
    finally:
        db.close()

    body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    md = _editor_md(client, pid)
    expected_md = SOURCE_SEPARATOR.join(f"# BODY_FOR_{n}" for n in expected_names)
    assert md == expected_md, (
        f"业务红：多文件 ASC 聚合必须精确，actual={md!r} expected={expected_md!r}"
    )
    assert order == expected_names, (
        f"业务红：RemoteSource 顺序必须 id ASC，actual={order} expected={expected_names}"
    )
    assert body["result"]["fileCount"] == 2
    # Q15：逐字段 path/filename/expected_size 对齐 DB+TEMP+stat
    src_cls = _require_attr(mod, "RemoteSource")
    upload_root = Path(get_settings().upload_dir).resolve()
    from app.services import file_service

    assert len(seen_sources) == 2
    db = SessionLocal()
    try:
        rows2 = list(
            db.scalars(
                select(ProjectFileRow)
                .where(ProjectFileRow.project_id == pid)
                .order_by(ProjectFileRow.id.asc())
            ).all()
        )
        for s, row in zip(seen_sources, rows2, strict=True):
            assert isinstance(s, src_cls)
            assert s.filename == row.filename
            stored = Path(
                file_service.resolve_path(get_settings(), pid, row.stored_name)
            ).resolve()
            assert Path(s.path).resolve() == stored
            assert Path(s.path).resolve().is_relative_to(upload_root), (
                f"业务红：path 必须 is_relative_to TEMP upload 根，path={s.path} root={upload_root}"
            )
            try:
                st = Path(s.path).stat(follow_symlinks=False).st_size
            except TypeError:
                st = Path(s.path).stat().st_size
            assert int(s.expected_size) == int(st)
            assert int(st) == int(row.size_bytes)
    finally:
        db.close()
    get_settings.cache_clear()

# ===========================================================================
# T7 caps / 失败 / 取消 / finalizer
# ===========================================================================

def test_t7_markdown_caps_zero_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    calls = {"n": 0}

    # 码点超限
    def fake_cp(sources, *, token, cancel_check, **kwargs):
        calls["n"] += 1
        md = "字" * 1_000_001
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_cp)
    pid = _create_project(client, "V1N-cp")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert calls["n"] == 1
    assert body["status"] == "failed"
    assert body.get("error") == FIXED_ERR_CODEPOINTS
    assert body.get("result") is None  # Q16：caps 沿用既有 None
    _assert_domains_unchanged(client, pid, before, label="codepoints", task_id=body["id"])

    # UTF-8 超限
    calls["n"] = 0

    def fake_utf8(sources, *, token, cancel_check, **kwargs):
        calls["n"] += 1
        # T1：码点 <=1_000_000 但 UTF-8 >2MiB（"汉"*700000）
        md = "汉" * 700_000
        assert len(md) <= 1_000_000
        assert len(md.encode("utf-8")) > 2 * 1024 * 1024
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_utf8)
    pid2 = _create_project(client, "V1N-utf8")
    _upload(client, pid2, "b.pdf", b"%PDF", "application/pdf")
    before2 = _snapshot_domains(client, pid2)
    body2 = _parse_remote(client, pid2)
    assert calls["n"] == 1
    assert body2["status"] == "failed"
    assert body2.get("error") == FIXED_ERR_UTF8_BYTES
    assert body2.get("result") is None
    _assert_domains_unchanged(client, pid2, before2, label="utf8", task_id=body2["id"])
    get_settings.cache_clear()

def test_t7b_client_through_task_cap_two_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    T4：最小真实 client-through-task cap 组合门。
    task 仅用 wrapper 给真实 run 注入 MockTransport/小 cap/固定公网 resolver；
    真实 client full.md 码点/UTF-8 cap 超限后最终必须精确二键 output_invalid。
    production 缺失时业务红。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    run = _get_run_fn(mod)
    err_cls = _require_attr(mod, "RemoteMineruError")

    # 压低 client 侧 cap，令 full.md 读取超限（唯一场景：output_invalid）
    monkeypatch.setattr(mod, "MAX_MD_CODEPOINTS", 8)
    monkeypatch.setattr(mod, "MAX_MD_UTF8_BYTES", 32)

    real_run = run
    resolve_hits: list[str] = []

    def fixed_public_resolver(host: str) -> list[str]:
        h = str(host or "").strip().lower().strip("[]")
        resolve_hits.append(h)
        return [_TASK_FAKE_PUBLIC_IP]

    def wrapped_run(sources, **kwargs):
        # 注入内存 transport：happy 到 ZIP，full.md 故意超 cap
        holder: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                body = json.loads(request.content.decode())
                holder["id"] = body["files"][0]["data_id"]
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "batch_id": "batch-combo-cap",
                            "file_urls": ["https://upload.example.test/presign/a"],
                        },
                    },
                )
            if request.method == "PUT":
                return httpx.Response(200)
            if request.method == "GET" and "/extract-results/batch/" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {
                                    "data_id": holder["id"],
                                    "state": "done",
                                    "full_zip_url": "https://cdn.example.test/result/a.zip",
                                }
                            ]
                        },
                    },
                )
            if request.method == "GET" and str(request.url).endswith(".zip"):
                # 超码点/字节 full.md → 精确 output_invalid（禁止 zip_unsafe 二选一）
                md = ("字" * 20).encode("utf-8")
                import io as _io
                import zipfile as _zf

                buf = _io.BytesIO()
                with _zf.ZipFile(buf, "w") as zf:
                    zf.writestr("full.md", md)
                return httpx.Response(200, content=buf.getvalue())
            raise AssertionError(f"unexpected {request.method} {request.url}")

        kwargs = dict(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.setdefault("sleep_fn", lambda _s: None)
        kwargs.setdefault("clock_fn", lambda: 0.0)
        # T2：固定公网 resolver；upload 与 ZIP host 均须经 resolver/JIT，零真实 DNS
        kwargs["resolve_addresses_fn"] = fixed_public_resolver
        return real_run(sources, **kwargs)

    _patch_remote_run(monkeypatch, mod, wrapped_run)
    pid = _create_project(client, "V1N-combo-cap")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert body["status"] == "failed"
    result = body.get("result")
    assert isinstance(result, dict), f"业务红：client cap 须二键 result，actual={result!r}"
    assert result.get("engine") == ENGINE
    # T3：diagnosticCode 精确等于 output_invalid（文案/注释禁止二选一）
    assert result.get("diagnosticCode") == "output_invalid", (
        f"业务红：client-through-task full.md cap 须精确 output_invalid，actual={result}"
    )
    assert set(result.keys()) == FAIL_KEYS
    # T2：upload 与 ZIP host 均经过 resolver
    assert any(h == "upload.example.test" for h in resolve_hits), (
        f"业务红：upload host 必须经 resolver/JIT，hits={resolve_hits}"
    )
    assert any(h == "cdn.example.test" for h in resolve_hits), (
        f"业务红：ZIP host 必须经 resolver/JIT，hits={resolve_hits}"
    )
    _assert_domains_unchanged(client, pid, before, label="combo-cap", task_id=body["id"])
    get_settings.cache_clear()


def test_t8_remote_error_mapping_and_privacy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    msg_fn = _require_attr(mod, "message_for_code")
    temp_abs = str(Path(get_settings().upload_dir).resolve())

    def boom(*a, **k):
        # canary 必须真实进入 message/args/__cause__
        cause = RuntimeError(
            f"cause-leak {FAKE_BATCH} {FAKE_PRESIGN} {FAKE_TRACE} {temp_abs}"
        )
        try:
            exc = err_cls(
                diagnostic_code="remote_parse_failed",
                message=(
                    f"{FIXED_MESSAGES['remote_parse_failed']} "
                    f"LEAK {FAKE_DATA_ID} {FAKE_CLOUD_ERR} {BODY_CANARY}"
                ),
            )
        except TypeError:
            exc = err_cls("remote_parse_failed")
        if not hasattr(exc, "message"):
            raise AssertionError("业务红：RemoteMineruError 必须暴露 message")
        # 污染 args；不可写时把 canary 并入 cause
        try:
            exc.args = (exc.message, FAKE_TOKEN, FAKE_ZIP_URL)
        except (AttributeError, TypeError):
            cause = RuntimeError(
                f"{cause} args-canary {FAKE_TOKEN} {FAKE_ZIP_URL}"
            )
        raise exc from cause

    _patch_remote_run(monkeypatch, mod, boom)
    pid = _create_project(client, "V1N-remote-fail")
    _upload(client, pid, ORIGINAL_FILENAME, b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    with caplog.at_level(logging.DEBUG):
        body = _parse_remote(client, pid)
    _assert_fail_result(body, code="remote_parse_failed")
    # 对外 error 必须是冻结中文，不含 canary
    assert body.get("error") == FIXED_MESSAGES["remote_parse_failed"]
    if callable(msg_fn):
        assert msg_fn("remote_parse_failed") == FIXED_MESSAGES["remote_parse_failed"]
    _assert_domains_unchanged(client, pid, before, label="remote_parse_failed")
    assert _success_event_count(pid, body["id"]) == 0
    _scan_task_surfaces(
        client,
        pid,
        body,
        extra=[temp_abs, FAKE_DATA_ID, FAKE_CLOUD_ERR],
        caplog_text=caplog.text,
    )
    get_settings.cache_clear()

def test_t8b_ordinary_exception_folds_internal_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    temp_abs = str(Path(get_settings().upload_dir).resolve())

    def boom(*a, **k):
        raise RuntimeError(
            f"ordinary {BODY_CANARY} {FAKE_TOKEN} {temp_abs} {FAKE_TRACE}"
        )

    _patch_remote_run(monkeypatch, mod, boom)
    pid = _create_project(client, "V1N-internal")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    with caplog.at_level(logging.DEBUG):
        body = _parse_remote(client, pid)
    _assert_fail_result(body, code="internal_error")
    _assert_domains_unchanged(client, pid, before, label="internal_error")
    _scan_task_surfaces(
        client, pid, body, extra=[temp_abs], caplog_text=caplog.text
    )
    get_settings.cache_clear()

def _collect_worker_cleanup_candidates(
    worker: threading.Thread | None,
    bound_worker: dict[str, threading.Thread],
    workers: list[threading.Thread],
) -> list[threading.Thread]:
    """
    R7-T1：从 worker / bound_worker.get('t') / workers 中 task-* 三路收集候选；
    按对象身份去重，排除 current_thread。
    """
    seen: set[int] = set()
    out: list[threading.Thread] = []
    current = threading.current_thread()

    def _add(t: threading.Thread | None) -> None:
        if t is None:
            return
        if t is current:
            return
        tid = id(t)
        if tid in seen:
            return
        seen.add(tid)
        out.append(t)

    _add(worker)
    _add(bound_worker.get("t"))
    for w in list(workers):
        name = getattr(w, "name", "") or ""
        if isinstance(name, str) and name.startswith("task-"):
            _add(w)
    return out


def _bounded_join_worker_candidates(
    candidates: list[threading.Thread],
    *,
    deadline_monotonic: float,
) -> list[BaseException]:
    """
    R7-T1：单一共享 deadline 做 bounded join；
    一个 join 异常不得阻止回收其余候选。返回清理异常列表（不抛）。
    """
    import time as _time

    errs: list[BaseException] = []
    for t in candidates:
        remaining = deadline_monotonic - _time.monotonic()
        if remaining <= 0:
            # 超时后仍尝试 join(0) 探测，不阻塞
            try:
                t.join(timeout=0)
            except BaseException as e:  # noqa: BLE001 — 收集清理错
                errs.append(e)
            continue
        try:
            t.join(timeout=remaining)
        except BaseException as e:  # noqa: BLE001 — 一个失败不阻断其余
            errs.append(e)
    return errs


def _worker_cleanup_finish(
    *,
    release: threading.Event,
    worker: threading.Thread | None,
    bound_worker: dict[str, threading.Thread],
    workers: list[threading.Thread],
    primary_exc: BaseException | None,
    join_budget_sec: float = 15.0,
) -> None:
    """
    R7-T1：finally 路径——先 release.set()，再三路收集 + 共享 deadline join。
    已有 primary_exc 时清理异常/仍存活不得覆盖首错；
    无首错时 join 异常或任一候选仍存活必须固定红。
    """
    import time as _time

    release.set()
    candidates = _collect_worker_cleanup_candidates(worker, bound_worker, workers)
    deadline = _time.monotonic() + float(join_budget_sec)
    join_errs = _bounded_join_worker_candidates(
        candidates, deadline_monotonic=deadline
    )
    # R8-T1：每个 is_alive 单独 try/catch，异常并入 cleanup errors
    alive: list[threading.Thread] = []
    alive_errs: list[BaseException] = []
    for t in candidates:
        try:
            if t.is_alive():
                alive.append(t)
        except BaseException as e:  # noqa: BLE001 — 清理探测不得覆盖首错
            alive_errs.append(e)
    cleanup_errs: list[BaseException] = list(join_errs) + list(alive_errs)
    if primary_exc is not None:
        # 有 primary 时绝不覆盖（join/is_alive 异常均吞并）
        return
    if cleanup_errs:
        raise cleanup_errs[0]
    if alive:
        names = [getattr(t, "name", repr(t)) for t in alive]
        raise AssertionError(
            f"业务红：finally 后任务 worker 必须结束，禁止泄漏阻塞 alive={names}"
        )


def _worker_cleanup_helper_self_proof() -> None:
    """
    R7-T1：不依赖 production 的 worker cleanup 可执行自证。
    覆盖：仅 bound 路径、仅 task-* 兜底、三路同一线程只 join 一次、
    首错+清理错仍保留首错、无首错且存活必红；
    worker 局部赋值前注入失败时也必须退出真实 task-*。
    """
    import time as _time

    join_log: list[str] = []

    class _FakeThread:
        def __init__(self, name: str, *, alive: bool = False, boom: bool = False):
            self.name = name
            self._alive = alive
            self._boom = boom
            self.join_calls = 0

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            self.join_calls += 1
            join_log.append(self.name)
            if self._boom:
                raise RuntimeError(f"join-boom:{self.name}")
            self._alive = False

    # 1) 仅 bound 路径（worker=None，workers 无 task-*）
    bound_only = _FakeThread("task-bound-only")
    c1 = _collect_worker_cleanup_candidates(
        None, {"t": bound_only}, [ _FakeThread("other-thread") ]  # type: ignore[list-item]
    )
    assert c1 == [bound_only], f"业务红：仅 bound 路径必须收集 bound，actual={c1}"

    # 2) 仅 task-* 兜底（worker=None，bound 空）
    task_only = _FakeThread("task-fallback-1")
    noise = _FakeThread("pytest-noise")
    c2 = _collect_worker_cleanup_candidates(
        None, {}, [noise, task_only]  # type: ignore[list-item]
    )
    assert c2 == [task_only], f"业务红：仅 task-* 兜底必须命中，actual={c2}"

    # 3) 三路同一线程只 join 一次（对象身份去重）
    same = _FakeThread("task-same-id")
    join_log.clear()
    c3 = _collect_worker_cleanup_candidates(
        same, {"t": same}, [same]  # type: ignore[list-item]
    )
    assert c3 == [same], f"业务红：三路同一对象必须去重为 1，actual={c3}"
    errs3 = _bounded_join_worker_candidates(
        c3, deadline_monotonic=_time.monotonic() + 5.0
    )
    assert errs3 == []
    assert same.join_calls == 1, (
        f"业务红：同一线程只 join 一次，calls={same.join_calls}"
    )
    assert join_log == ["task-same-id"]

    # 4) 首错 + 清理错：保留首错，不覆盖
    primary = AssertionError("primary-business-red")
    boom_t = _FakeThread("task-boom", boom=True)
    try:
        raise primary
    except AssertionError as pe:
        # 模拟 finally：有 primary 时 join boom 不得覆盖
        try:
            _worker_cleanup_finish(
                release=threading.Event(),
                worker=boom_t,  # type: ignore[arg-type]
                bound_worker={},
                workers=[boom_t],  # type: ignore[list-item]
                primary_exc=pe,
                join_budget_sec=1.0,
            )
        except BaseException as leaked:  # noqa: BLE001
            raise AssertionError(
                f"业务红：有 primary 时清理不得抛出覆盖首错，leaked={leaked!r}"
            ) from leaked
        assert pe is primary

    # 5) 无首错且存活必红
    alive_t = _FakeThread("task-alive", alive=True)

    class _NeverDie(_FakeThread):
        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            self.join_calls += 1
            join_log.append(self.name)
            # 故意不把 _alive 置 False

    stuck = _NeverDie("task-stuck", alive=True)
    with pytest.raises(AssertionError, match="泄漏|结束"):
        _worker_cleanup_finish(
            release=threading.Event(),
            worker=stuck,  # type: ignore[arg-type]
            bound_worker={"t": stuck},  # type: ignore[dict-item]
            workers=[stuck],  # type: ignore[list-item]
            primary_exc=None,
            join_budget_sec=0.05,
        )

    # 6) 真实 task-* 线程：赋值前失败（worker=None）仍须 release/join/not alive
    entered_real = threading.Event()
    release_real = threading.Event()
    done_real = threading.Event()

    def _real_worker_body() -> None:
        entered_real.set()
        release_real.wait(timeout=30.0)
        done_real.set()

    real_t = threading.Thread(
        target=_real_worker_body,
        name="task-preassign-real",
        daemon=True,
    )
    real_t.start()
    assert entered_real.wait(5.0), "业务红：真实 task-* 必须先发 entered"
    try:
        _worker_cleanup_finish(
            release=release_real,
            worker=None,  # 局部赋值前失败
            bound_worker={},
            workers=[real_t],
            primary_exc=AssertionError("injected-before-worker-assign"),
            join_budget_sec=5.0,
        )
        assert release_real.is_set(), "业务红：cleanup 后 release 必须 set"
        done_ok = done_real.wait(5.0)
        not_alive = not real_t.is_alive()
        # 禁止 assert 内 BoolOp Or：用 any 列表中介
        assert any([done_ok, not_alive]), (
            "业务红：cleanup 后 worker 必须完成或 done set"
        )
        assert not real_t.is_alive(), (
            f"业务红：bounded join 后真实 task-* 不得仍存活 name={real_t.name}"
        )
        assert done_real.is_set(), "业务红：真实 worker 退出前必须 done set"
    finally:
        # helper 自身失败也 release/join，不污染后续
        release_real.set()
        real_t.join(timeout=5.0)
        if real_t.is_alive():
            raise AssertionError("业务红：finally 兜底后真实线程仍存活，污染后续")

    # 7) 无首错 + join 异常必须固定红
    boom2 = _FakeThread("task-join-err", boom=True)
    with pytest.raises(RuntimeError, match="join-boom"):
        _worker_cleanup_finish(
            release=threading.Event(),
            worker=boom2,  # type: ignore[arg-type]
            bound_worker={},
            workers=[],
            primary_exc=None,
            join_budget_sec=1.0,
        )

    # 8) is_alive 抛错：有 primary 不覆盖；无 primary 固定红
    class _AliveBoom:
        def __init__(self, name: str):
            self.name = name

        def is_alive(self) -> bool:
            raise RuntimeError(f"is_alive-boom:{self.name}")

        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            return None

    boom_alive = _AliveBoom("task-alive-boom")
    primary_keep = AssertionError("primary-keeps-alive-boom")
    try:
        raise primary_keep
    except AssertionError as pe:
        try:
            _worker_cleanup_finish(
                release=threading.Event(),
                worker=boom_alive,  # type: ignore[arg-type]
                bound_worker={},
                workers=[boom_alive],  # type: ignore[list-item]
                primary_exc=pe,
                join_budget_sec=1.0,
            )
        except BaseException as leaked:  # noqa: BLE001
            raise AssertionError(
                f"业务红：有 primary 时 is_alive 抛错不得覆盖，leaked={leaked!r}"
            ) from leaked
        assert pe is primary_keep

    with pytest.raises(RuntimeError, match="is_alive-boom"):
        _worker_cleanup_finish(
            release=threading.Event(),
            worker=boom_alive,  # type: ignore[arg-type]
            bound_worker={},
            workers=[],
            primary_exc=None,
            join_budget_sec=1.0,
        )

    # 抑制未使用告警（alive_t 仅作对照构造）
    assert alive_t.is_alive() is True


def test_t_worker_cleanup_helper_self_proof():
    """R7-T1：worker cleanup helper 可执行自证（不依赖 production）。"""
    _worker_cleanup_helper_self_proof()


def test_t9_real_cancel_maps_interrupted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """
    T3/T4/R6-T1/T2/R7-T1：API 取消最终 status=cancelled 且 result is None，任何 remote 二键均必红。
    R6-T1：fake_run 记录 current_thread，与 name=task-{tid} 精确绑定，禁止 workers[-1]。
    R6-T2/R7-T1：外层 try/finally 无条件 release.set()；三路收集 + 共享 deadline bounded join；
    清理不得掩盖首个业务异常；worker 赋值前失败也回收 task-*。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    entered = threading.Event()
    release = threading.Event()
    temp_abs = str(Path(get_settings().upload_dir).resolve())
    canaries = [
        FAKE_TOKEN,
        FAKE_BATCH,
        FAKE_DATA_ID,
        FAKE_PRESIGN,
        FAKE_ZIP_URL,
        FAKE_TRACE,
        BODY_CANARY,
        temp_abs,
        ORIGINAL_FILENAME,
    ]

    # T4/R6-T1：捕获实际后台线程（task_service.enqueue_task → threading.Thread）
    workers: list[threading.Thread] = []
    bound_worker: dict[str, threading.Thread] = {}
    real_thread_cls = threading.Thread

    class _TrackingThread(real_thread_cls):  # type: ignore[valid-type,misc]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            workers.append(self)

    monkeypatch.setattr(threading, "Thread", _TrackingThread)
    import app.services.task_service as _ts_mod

    monkeypatch.setattr(_ts_mod.threading, "Thread", _TrackingThread)

    def fake_run(sources, *, token, cancel_check, **kwargs):
        # R6-T1：记录真实任务 worker 线程身份
        bound_worker["t"] = threading.current_thread()
        entered.set()
        assert release.wait(timeout=10), "取消协调超时"
        if cancel_check():
            cause = RuntimeError(
                f"cancel-cause {FAKE_TOKEN} {FAKE_BATCH} {FAKE_DATA_ID} "
                f"{FAKE_PRESIGN} {FAKE_ZIP_URL} {FAKE_TRACE} {BODY_CANARY} {temp_abs}"
            )
            try:
                exc = err_cls(
                    diagnostic_code="interrupted",
                    message=(
                        f"{FIXED_MESSAGES['interrupted']} LEAK "
                        f"{FAKE_TOKEN} {BODY_CANARY} {ORIGINAL_FILENAME}"
                    ),
                )
            except TypeError:
                exc = err_cls("interrupted")
            try:
                exc.args = (getattr(exc, "message", "interrupted"), FAKE_TOKEN, temp_abs)
            except (AttributeError, TypeError):
                pass
            raise exc from cause
        raise AssertionError("业务红：取消后 cancel_check 应为 True")

    _patch_remote_run(monkeypatch, mod, fake_run)

    pid = _create_project(client, "V1N-cancel")
    _upload(client, pid, ORIGINAL_FILENAME, b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)

    worker: threading.Thread | None = None
    final = None
    primary_exc: BaseException | None = None
    try:
        body0 = _parse_remote(client, pid, sync=False)
        tid = body0["id"]
        assert entered.wait(timeout=10), "runner 未进入"
        assert workers, "业务红：必须捕获后台 worker 线程"
        assert "t" in bound_worker, "业务红：fake_run 必须记录 current_thread"
        expected_name = f"task-{tid}"
        matched = [w for w in workers if w.name == expected_name]
        assert len(matched) == 1, (
            f"业务红：必须唯一任务 worker name={expected_name!r}，"
            f"workers={[w.name for w in workers]}"
        )
        worker = matched[0]
        # 禁止 workers[-1]：与 bound current_thread 及 name 精确绑定
        assert bound_worker["t"] is worker, (
            f"业务红：fake_run 线程必须与 task-{{tid}} worker 为同一对象，"
            f"bound={bound_worker['t']!r} worker={worker!r}"
        )
        assert bound_worker["t"].name == expected_name, (
            f"业务红：worker name 必须为 {expected_name}，actual={bound_worker['t'].name}"
        )

        cancel = client.post(f"/api/projects/{pid}/tasks/{tid}/cancel")
        assert cancel.status_code == 200, (
            f"业务红：运行中 cancel 必须 200，actual={cancel.status_code} {cancel.text}"
        )
        with caplog.at_level(logging.DEBUG):
            release.set()
            # T4：bounded wait 至 _execute_task/worker 真正完成，禁止仅凭 cancelled 早退
            worker.join(timeout=15)
            assert not worker.is_alive(), (
                "业务红：后台 _execute_task 必须在退出 caplog/扫描前结束"
            )
            for _ in range(50):
                got = client.get(f"/api/projects/{pid}/tasks/{tid}")
                assert got.status_code == 200
                final = got.json()
                if final["status"] in {"failed", "cancelled"}:
                    break
        assert final is not None
        assert final["status"] == "cancelled", (
            f"业务红：API 已取消任务最终必须 cancelled，actual={final['status']} body={final}"
        )
        assert final.get("result") is None, (
            f"业务红：cancelled 必须 result is None，actual={final.get('result')!r}"
        )
        # 任何 remote 二键均必红
        if isinstance(final.get("result"), dict):
            raise AssertionError("业务红：cancelled 不得带 remote 二键 result")
        # worker 已结束，不污染后续 fixture
        assert not worker.is_alive()
        _assert_domains_unchanged(client, pid, before, label="cancel")
        _scan_task_surfaces(
            client,
            pid,
            final,
            extra=canaries,
            caplog_text=caplog.text,
        )
    except BaseException as exc:  # noqa: BLE001 — 保留首错
        primary_exc = exc
        raise
    finally:
        # R7-T1：先 release.set()；三路收集 + 共享 deadline；不得 worker=worker 自赋值
        _worker_cleanup_finish(
            release=release,
            worker=worker,
            bound_worker=bound_worker,
            workers=workers,
            primary_exc=primary_exc,
            join_budget_sec=15.0,
        )
    get_settings.cache_clear()

def test_t9b_poll_budget_exceeded_mapping(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")

    def boom(*a, **k):
        raise err_cls(
            diagnostic_code="poll_budget_exceeded",
            message=FIXED_MESSAGES["poll_budget_exceeded"],
        )

    _patch_remote_run(monkeypatch, mod, boom)
    pid = _create_project(client, "V1N-budget")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    _assert_fail_result(body, code="poll_budget_exceeded")
    _assert_domains_unchanged(client, pid, before, label="budget", task_id=body["id"])
    get_settings.cache_clear()

@pytest.mark.parametrize("code", sorted(REMOTE_TWO_KEY_CODES))
def test_t9c_all_remote_two_key_codes_via_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, code: str
):
    """Q16：参数化全部 REMOTE_TWO_KEY_CODES 穿真实 task 分支，精确二键。"""
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")

    def boom(*a, **k):
        raise err_cls(
            diagnostic_code=code,
            message=FIXED_MESSAGES[code],
        )

    _patch_remote_run(monkeypatch, mod, boom)
    pid = _create_project(client, f"V1N-rk-{code[:12]}")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    _assert_fail_result(body, code=code)
    _assert_domains_unchanged(client, pid, before, label=f"two-key-{code}", task_id=body["id"])
    assert _success_event_count(pid, body["id"]) == 0
    get_settings.cache_clear()

def test_t9d_interrupted_without_api_cancel_is_failed_two_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Q16：未 API cancel 的 client interrupted → failed + 二键 interrupted（禁止 or 双放行）。"""
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")

    def boom(*a, **k):
        raise err_cls(
            diagnostic_code="interrupted",
            message=FIXED_MESSAGES["interrupted"],
        )

    _patch_remote_run(monkeypatch, mod, boom)
    pid = _create_project(client, "V1N-intr-no-cancel")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert body["status"] == "failed", (
        f"业务红：未取消的 interrupted 必须 failed，actual={body['status']}"
    )
    _assert_fail_result(body, code="interrupted")
    _assert_domains_unchanged(
        client, pid, before, label="interrupted-no-cancel", task_id=body["id"]
    )
    get_settings.cache_clear()

def test_t5b_path_replace_runner_zero(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    T2：复用 M2 真 leaf/parent lstat/reparse seam。
    leaf 精确 FIXED_ERR_LEAF_REPARSE；parent 精确 FIXED_ERR_PARENT_REPARSE；
    两支 result None、runner0、五域零写。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    calls: list[str] = []

    def guard(*a, **k):
        calls.append("run")
        raise AssertionError("reparse 后不得进入 remote runner")

    _patch_remote_run(monkeypatch, mod, guard)
    from app.services import file_service

    settings = get_settings()

    # --- A) leaf reparse/symlink seam（M2 风格 FakeStat）---
    pid = _create_project(client, "V1N-path-leaf")
    _upload(client, pid, "p.pdf", b"%PDF-PATH", "application/pdf")
    # 服务端真值 stored_name（隔离 TEMP 库），不以响应 storedName 作为 path/seam 真源
    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
        ).one()
        server_stored_name = row.stored_name
    finally:
        db.close()
    stored = Path(
        file_service.resolve_path(settings, pid, server_stored_name)
    ).resolve()
    assert stored.exists()

    class _LeafReparseStat:
        st_mode = 0o120777
        st_size = 10
        st_ino = 1
        st_dev = 1
        st_nlink = 1
        st_uid = 0
        st_gid = 0
        st_atime = 0
        st_mtime = 0
        st_ctime = 0
        st_file_attributes = 0x400

    real_os_lstat = os.lstat

    def _leaf_lstat(path, *a, **k):
        if str(stored) in str(path) or server_stored_name in str(path):
            return _LeafReparseStat()
        return real_os_lstat(path, *a, **k)

    monkeypatch.setattr(os, "lstat", _leaf_lstat)
    real_path_lstat = Path.lstat

    def _path_leaf_lstat(self, *a, **k):
        if Path(self).resolve() == stored or str(self).endswith(server_stored_name):
            return _LeafReparseStat()
        return real_path_lstat(self, *a, **k)

    monkeypatch.setattr(Path, "lstat", _path_leaf_lstat, raising=False)

    before = _snapshot_domains(client, pid)
    body = _parse_remote(client, pid)
    assert calls == [], f"业务红：leaf reparse runner 必须为 0，actual={calls}"
    assert body["status"] == "failed"
    assert body.get("result") is None
    assert body.get("error") == FIXED_ERR_LEAF_REPARSE, (
        f"业务红：leaf 必须精确 FIXED_ERR_LEAF_REPARSE，actual={body.get('error')!r}"
    )
    _assert_domains_unchanged(client, pid, before, label="path-leaf", task_id=body["id"])

    # --- B) parent reparse seam ---
    calls.clear()
    # 恢复 os.lstat/Path.lstat 为真实，再装 parent seam
    monkeypatch.setattr(os, "lstat", real_os_lstat)
    monkeypatch.setattr(Path, "lstat", real_path_lstat, raising=False)
    pid2 = _create_project(client, "V1N-path-parent")
    _upload(client, pid2, "q.pdf", b"%PDF-PAR", "application/pdf")
    db2 = SessionLocal()
    try:
        row2 = db2.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid2)
        ).one()
        server_stored_name2 = row2.stored_name
    finally:
        db2.close()
    stored2 = Path(
        file_service.resolve_path(settings, pid2, server_stored_name2)
    ).resolve()
    parent = stored2.parent
    before2 = _snapshot_domains(client, pid2)

    class _ParentReparseStat:
        st_mode = 0o40777
        st_size = 0
        st_ino = 2
        st_dev = 1
        st_nlink = 1
        st_uid = 0
        st_gid = 0
        st_atime = 0
        st_mtime = 0
        st_ctime = 0
        st_file_attributes = 0x400

    def _parent_lstat(self, *a, **k):
        p = Path(self)
        try:
            if p.resolve() == parent.resolve():
                return _ParentReparseStat()
        except OSError:
            pass
        return real_path_lstat(self, *a, **k)

    monkeypatch.setattr(Path, "lstat", _parent_lstat)
    body2 = _parse_remote(client, pid2)
    assert calls == [], f"业务红：parent reparse runner 必须为 0，actual={calls}"
    assert body2["status"] == "failed"
    assert body2.get("result") is None
    assert body2.get("error") == FIXED_ERR_PARENT_REPARSE, (
        f"业务红：parent 必须精确 FIXED_ERR_PARENT_REPARSE，actual={body2.get('error')!r}"
    )
    _assert_domains_unchanged(
        client, pid2, before2, label="path-parent", task_id=body2["id"]
    )
    get_settings.cache_clear()


def test_t10_finalizer_h1_h3_condensed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """
    用途：H1/H3 缩编——三写点 commit=False + 成功最终 commit=1；
      upsert/project/set_task(success)/final commit 四故障点五域回滚。
    """
    from app.services import editor_state_service, project_service
    from app.core import database as dbmod

    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    md = f"# X\n{BODY_CANARY}\n"

    def fake_run(*a, **k):
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    # --- H1：commit=False + finalizer commit=1 ---
    for modu, name in (
        (editor_state_service, "upsert_editor_state"),
        (project_service, "update_project"),
        (task_service, "_set_task"),
    ):
        fn = getattr(modu, name)
        sig = inspect.signature(fn)
        assert "commit" in sig.parameters, f"业务红：{name} 缺少 commit"
        assert sig.parameters["commit"].default is True

    seen: dict[str, list] = {
        "upsert": [],
        "project": [],
        "set_task": [],
        "finalizer_commit": [],
        "flush": [],
    }
    real_up = editor_state_service.upsert_editor_state
    real_proj = project_service.update_project
    real_set = task_service._set_task
    real_flush = dbmod.Session.flush
    real_commit = dbmod.Session.commit
    arm = {"finalizer": False}

    def spy_up(*a, **k):
        flag = k.get("commit", "MISSING")
        seen["upsert"].append(flag)
        if flag is False:
            arm["finalizer"] = True
        return real_up(*a, **k)

    def spy_proj(*a, **k):
        flag = k.get("commit", "MISSING")
        seen["project"].append(flag)
        if flag is False:
            arm["finalizer"] = True
        return real_proj(*a, **k)

    def spy_set(*a, **k):
        entry = {
            "commit": k.get("commit", "MISSING"),
            "status": k.get("status"),
        }
        seen["set_task"].append(entry)
        if entry["commit"] is False and entry.get("status") == "success":
            arm["finalizer"] = True
        return real_set(*a, **k)

    def spy_flush(self, *a, **k):
        seen["flush"].append("f")
        return real_flush(self, *a, **k)

    def _has_success_pkg(session: Any) -> bool:
        objs = list(getattr(session, "new", set())) + list(
            getattr(session, "dirty", set())
        )
        has_task = any(
            isinstance(o, ProjectTaskRow) and getattr(o, "status", None) == "success"
            for o in objs
        )
        return has_task

    def spy_commit(self, *a, **k):
        # Q17：arm 后每一次 Session.commit 无条件计数（不依赖 dirty/new success 过滤）
        if arm["finalizer"]:
            seen["finalizer_commit"].append("c")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy_up)
    # T3：task_service 本地绑定 update_project，必须 patch task_service.update_project
    monkeypatch.setattr(task_service, "update_project", spy_proj)
    monkeypatch.setattr(project_service, "update_project", spy_proj)
    monkeypatch.setattr(task_service, "_set_task", spy_set)
    monkeypatch.setattr(dbmod.Session, "flush", spy_flush)
    monkeypatch.setattr(dbmod.Session, "commit", spy_commit)

    pid = _create_project(client, "V1N-h1")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    # setup 后清零，再 arm 后计数
    seen["finalizer_commit"].clear()
    arm["finalizer"] = False
    seen["upsert"].clear()
    seen["project"].clear()
    seen["set_task"].clear()
    # 预热后再清零 commit 计数：仅统计 parse 路径
    seen["finalizer_commit"].clear()

    body = _parse_remote(client, pid)
    assert body["status"] == "success", body
    assert seen["upsert"] and all(c is False for c in seen["upsert"]), (
        f"业务红：upsert 必须 commit=False，actual={seen['upsert']}"
    )
    assert seen["project"] and all(c is False for c in seen["project"]), (
        f"业务红：project 必须 commit=False，actual={seen['project']}"
    )
    success_sets = [x for x in seen["set_task"] if x.get("status") == "success"]
    assert success_sets and all(x.get("commit") is False for x in success_sets), (
        f"业务红：_set_task success 必须 commit=False，actual={success_sets}"
    )
    assert len(seen["finalizer_commit"]) == 1, (
        f"业务红：成功最终 commit 必须精确 1，actual={len(seen['finalizer_commit'])}"
    )
    # _has_success_pkg 保留作 H3 参考，避免未使用告警
    assert callable(_has_success_pkg)

    # 恢复 spy 以便 H3
    monkeypatch.setattr(editor_state_service, "upsert_editor_state", real_up)
    monkeypatch.setattr(task_service, "update_project", real_proj)
    monkeypatch.setattr(project_service, "update_project", real_proj)
    monkeypatch.setattr(task_service, "_set_task", real_set)
    monkeypatch.setattr(dbmod.Session, "flush", real_flush)
    monkeypatch.setattr(dbmod.Session, "commit", real_commit)

    # --- H3：四故障点 ---
    faults = [
        ("upsert", editor_state_service, "upsert_editor_state"),
        ("project", task_service, "update_project"),
        ("set_task", task_service, "_set_task"),
    ]
    for label, modu, name in faults:
        pid_f = _create_project(client, f"V1N-rb-{label}")
        _upload(client, pid_f, "t.pdf", b"%PDF", "application/pdf")
        before = _snapshot_domains(client, pid_f)
        real = getattr(modu, name)
        inject = {"n": 0}
        temp_abs = str(Path(get_settings().upload_dir).resolve())

        def _boom(*a, _label=label, _real=real, _cnt=inject, **k):
            if _label == "set_task":
                if k.get("status") == "success":
                    _cnt["n"] += 1
                    raise RuntimeError(f"injected {_label} failure {BODY_CANARY}")
                return _real(*a, **k)
            _cnt["n"] += 1
            raise RuntimeError(f"injected {_label} failure {BODY_CANARY}")

        monkeypatch.setattr(modu, name, _boom)
        with caplog.at_level(logging.DEBUG):
            body_f = _parse_remote(client, pid_f)
        assert body_f["status"] == "failed"
        assert inject["n"] == 1
        assert body_f.get("error") == FIXED_FINALIZER_ERROR
        assert body_f.get("result") is None
        _assert_domains_unchanged(
            client, pid_f, before, label=f"h3-{label}", task_id=body_f["id"]
        )
        _assert_post_get_error_consistent(client, pid_f, body_f)
        assert _success_event_count(pid_f, body_f["id"]) == 0
        # R13：四故障点统一 caplog + R10 真实表面扫描（extra 仅禁止子串，不拼入 blob）
        _scan_task_surfaces(
            client,
            pid_f,
            body_f,
            extra=[temp_abs, BODY_CANARY, FAKE_TOKEN],
            caplog_text=caplog.text,
            monkeypatch=monkeypatch,
        )
        _assert_no_privacy_leak(body_f.get("error"), where=f"h3-{label}")
        monkeypatch.setattr(modu, name, real)

    # final commit —— R13 必须含 caplog + 全表面扫描，不可省略
    pid_c = _create_project(client, "V1N-rb-commit")
    _upload(client, pid_c, "t.pdf", b"%PDF", "application/pdf")
    before_c = _snapshot_domains(client, pid_c)
    state = {"fired": 0}
    temp_c = str(Path(get_settings().upload_dir).resolve())

    def _commit_boom(self, *a, **k):
        pending_success = False
        objs = list(getattr(self, "new", set())) + list(getattr(self, "dirty", set()))
        for obj in objs:
            if isinstance(obj, ProjectTaskRow) and getattr(obj, "status", None) == "success":
                pending_success = True
                break
            if isinstance(obj, ProjectTaskEventRow) and getattr(obj, "status", None) == "success":
                pending_success = True
                break
        if pending_success and state["fired"] == 0:
            state["fired"] = 1
            raise RuntimeError(f"injected final commit {BODY_CANARY}")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(dbmod.Session, "commit", _commit_boom)
    with caplog.at_level(logging.DEBUG):
        body_c = _parse_remote(client, pid_c)
    assert body_c["status"] == "failed"
    assert state["fired"] == 1
    assert body_c.get("error") == FIXED_FINALIZER_ERROR
    assert body_c.get("result") is None
    _assert_domains_unchanged(client, pid_c, before_c, label="h3-final-commit")
    _assert_post_get_error_consistent(client, pid_c, body_c)
    assert _success_event_count(pid_c, body_c["id"]) == 0
    _scan_task_surfaces(
        client,
        pid_c,
        body_c,
        extra=[temp_c, BODY_CANARY, FAKE_TOKEN],
        caplog_text=caplog.text,
        monkeypatch=monkeypatch,
    )
    _assert_no_privacy_leak(body_c.get("error"), where="h3-final-commit")
    monkeypatch.setattr(dbmod.Session, "commit", real_commit)
    get_settings.cache_clear()

# ===========================================================================
# T11 CAS / lightweight / managed 回归
# ===========================================================================

def test_t11_cas_stale_semantics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    R11：stateVersion 均为非空字符串且不同；
    任务创建后 payload_json['_expectedStateVersion']==ver0；
    spy 证明 upsert expected_state_version 关键字实参等于 ver0；
    删除 TypeError fallback 与 captured 为空可空过的弱断言。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    md = f"# CAS\n{BODY_CANARY}\n"
    barrier = threading.Event()
    entered = threading.Event()
    captured_expected: list[Any] = []

    def fake_run(*a, **k):
        entered.set()
        assert barrier.wait(timeout=10), "CAS barrier 超时"
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    from app.services import editor_state_service

    real_up = editor_state_service.upsert_editor_state

    def spy_up(*a, **k):
        # 精确关键字 expected_state_version（禁止 TypeError fallback 掩盖）
        if "expected_state_version" in k:
            captured_expected.append(k["expected_state_version"])
        return real_up(*a, **k)

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy_up)

    pid = _create_project(client, "V1N-cas")
    _upload(client, pid, "a.pdf", b"%PDF", "application/pdf")
    es0 = client.get(f"/api/projects/{pid}/editor-state").json()
    ver0 = es0.get("stateVersion")
    assert isinstance(ver0, str) and ver0.strip(), (
        f"业务红：ver0 必须为非空字符串 stateVersion，actual={ver0!r}"
    )

    # 1) 异步创建任务
    body0 = _parse_remote(client, pid, sync=False)
    tid = body0["id"]
    # 创建后立即查 payload_json
    row0 = _load_task_row(tid)
    payload0 = row0.payload_json
    if isinstance(payload0, str):
        payload0 = json.loads(payload0)
    assert isinstance(payload0, dict), f"业务红：payload_json 必须 dict，actual={payload0!r}"
    assert payload0.get("_expectedStateVersion") == ver0, (
        f"业务红：创建后 _expectedStateVersion 必须等于 ver0={ver0!r}，"
        f"actual={payload0.get('_expectedStateVersion')!r}"
    )
    assert entered.wait(timeout=10), "runner 未进入"

    # 2) 第二 Session 推进 editor-state（无 TypeError fallback）
    db2 = SessionLocal()
    try:
        editor_state_service.upsert_editor_state(
            db2,
            "ws_local",
            pid,
            mode="technical",
            parsed_markdown="# concurrent-cas-edit",
        )
        db2.commit()
    except Exception:
        db2.rollback()
        raise
    finally:
        db2.close()

    es1 = client.get(f"/api/projects/{pid}/editor-state").json()
    ver1 = es1.get("stateVersion")
    assert isinstance(ver1, str) and ver1.strip(), (
        f"业务红：ver1 必须为非空字符串，actual={ver1!r}"
    )
    assert ver1 != ver0, f"业务红：第二 Session 必须推进 stateVersion {ver0!r}->{ver1!r}"

    before = _snapshot_domains(client, pid, task_id=tid)
    barrier.set()

    final = None
    for _ in range(100):
        got = client.get(f"/api/projects/{pid}/tasks/{tid}")
        assert got.status_code == 200
        final = got.json()
        if final["status"] in {"failed", "success", "cancelled"}:
            break
    assert final is not None
    assert final["status"] == "failed", (
        f"业务红：CAS 必须 failed，actual={final['status']} body={final}"
    )
    err = final.get("error") or ""
    assert err == task_service.ERR_TASK_BASE_CHANGED, (
        f"业务红：CAS 冲突必须固定 ERR_TASK_BASE_CHANGED，actual={err!r}"
    )
    assert final.get("result") is None
    assert BODY_CANARY not in err
    _assert_domains_unchanged(client, pid, before, label="cas-stale", task_id=tid)
    assert _success_event_count(pid, tid) == 0
    # R11：硬性要求 spy 捕获到 expected_state_version == ver0
    assert captured_expected, (
        "业务红：spy 必须捕获 expected_state_version 关键字实参（禁止空列表空过）"
    )
    assert all(v == ver0 for v in captured_expected), (
        f"业务红：expected_state_version 必须全为 ver0={ver0!r}，actual={captured_expected}"
    )
    get_settings.cache_clear()

def test_t12_lightweight_and_managed_regression(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    Q14：lightweight success；managed 未配置失败；
    managed configured fake-runtime 无条件执行（真实五键 + 严格 TEMP 清理）；remote=0。
    """
    monkeypatch.delenv(MANIFEST_ENV, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    if hasattr(settings, "managed_ocr_manifest_path"):
        monkeypatch.setattr(settings, "managed_ocr_manifest_path", "")

    mod_available = _client_available()
    remote_calls: list[str] = []
    if mod_available:
        mod = _load_client()

        def guard(*a, **k):
            remote_calls.append("run")
            raise AssertionError("lightweight/managed 不得调用 remote runner")

        _patch_remote_run(monkeypatch, mod, guard)

    # lightweight
    pid = _create_project(client, "V1N-light")
    _upload(client, pid, "a.md", b"# L\n\nLIGHT_OK\n", "text/markdown")
    res = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "lightweight"}},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "success"
    assert set(body["result"].keys()) == SUCCESS_KEYS
    assert body["result"]["engine"] == "lightweight"
    assert "LIGHT_OK" in _editor_md(client, pid)
    assert remote_calls == []
    assert _success_event_count(pid, body["id"]) == 1

    # managed 未配置
    pid2 = _create_project(client, "V1N-managed")
    _upload(client, pid2, "a.md", b"# M\n", "text/markdown")
    res2 = client.post(
        f"/api/projects/{pid2}/tasks?sync=true",
        json={"type": "parse", "payload": {"engine": "managed"}},
    )
    assert res2.status_code == 201
    body2 = res2.json()
    assert body2["status"] == "failed"
    result2 = body2.get("result") or {}
    assert set(result2.keys()) == FAIL_KEYS
    assert result2.get("engine") == "managed"
    assert result2.get("diagnosticCode") == "runtime_manifest_invalid"
    assert ENGINE not in json.dumps(result2, ensure_ascii=False)
    assert "LIGHT_OK" not in _editor_md(client, pid2)
    assert remote_calls == []
    assert _success_event_count(pid2, body2["id"]) == 0

    # Q14：managed configured fake-runtime success — 无条件 require API/core
    from app.services import managed_parse_runtime_service as managed_svc

    assert hasattr(managed_svc, "get_core_module"), (
        "业务红：managed_parse_runtime_service 必须暴露 get_core_module"
    )
    core = managed_svc.get_core_module()
    assert core is not None, "业务红：get_core_module 不得返回 None"
    assert hasattr(core, "parse_one_file_with_manifest_cli"), (
        "业务红：core 必须暴露 parse_one_file_with_manifest_cli"
    )

    rt = _track_temp(Path(tempfile.mkdtemp(prefix="v1n_m_rt_")))
    try:
        cli = rt / "venv" / "Scripts" / "mineru.exe"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_bytes(b"MZ-fake")
        marker = rt / "models" / ".biaoshu-ready"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"ready\n")
        manifest = rt / "runtime-manifest.json"
        # 真实五键：schemaVersion/engine/cliRelativePath/modelMarkerRelativePath/requiredFreeBytes
        five = {
            "schemaVersion": 1,
            "engine": "mineru",
            "cliRelativePath": "venv/Scripts/mineru.exe",
            "modelMarkerRelativePath": "models/.biaoshu-ready",
            "requiredFreeBytes": 1,
        }
        assert set(five.keys()) == {
            "schemaVersion",
            "engine",
            "cliRelativePath",
            "modelMarkerRelativePath",
            "requiredFreeBytes",
        }
        manifest.write_text(json.dumps(five, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(MANIFEST_ENV, str(manifest))
        get_settings.cache_clear()
        settings = get_settings()
        if hasattr(settings, "managed_ocr_manifest_path"):
            monkeypatch.setattr(settings, "managed_ocr_manifest_path", str(manifest))

        def fake_ready(*a, **k):
            from types import SimpleNamespace

            return SimpleNamespace(ok=True, diagnostic_code="static_ready")

        def fake_recheck(*a, **k):
            return True

        def fake_one(*a, **k):
            return "# MANAGED_FAKE_RUNTIME_OK\n"

        monkeypatch.setattr(core, "validate_runtime_ready", fake_ready)
        monkeypatch.setattr(core, "recheck_cli_and_marker", fake_recheck)
        monkeypatch.setattr(core, "parse_one_file_with_manifest_cli", fake_one)

        pid3 = _create_project(client, "V1N-managed-ok")
        _upload(client, pid3, "m.md", b"# M\n", "text/markdown")
        res3 = client.post(
            f"/api/projects/{pid3}/tasks?sync=true",
            json={"type": "parse", "payload": {"engine": "managed"}},
        )
        assert res3.status_code == 201
        body3 = res3.json()
        assert body3["status"] == "success", body3
        assert set(body3["result"].keys()) == SUCCESS_KEYS
        assert body3["result"]["engine"] == "managed"
        assert "MANAGED_FAKE_RUNTIME_OK" in _editor_md(client, pid3)
        assert remote_calls == []
        assert _success_event_count(pid3, body3["id"]) == 1
    finally:
        # 严格清理：不允许 ignore_errors 掩盖残留
        if rt.exists():
            shutil.rmtree(rt)
        assert not rt.exists(), f"业务红：managed TEMP 必须清理，left={rt}"

    get_settings.cache_clear()

def test_t13_message_for_code_exact_all_codes():
    """T11：冻结表独立；production 必须对齐，禁止同句恒绿。"""
    for code, expected in FIXED_MESSAGES.items():
        assert expected.strip() and expected != code
    # 唯一性：不同码不得全部相同文案
    assert len(set(FIXED_MESSAGES.values())) >= 10
    mod = _load_client()
    msg_fn = _require_attr(mod, "message_for_code")
    for code, expected in FIXED_MESSAGES.items():
        assert msg_fn(code) == expected, f"{code}: {msg_fn(code)!r} != {expected!r}"
    assert msg_fn("totally_unknown_code") == FIXED_MESSAGES["internal_error"]

# ===========================================================================
# V1-N 任务发布门 Q4：路径/取消/事务仲裁（关键字 v1n_task_release_gate_q4）
# failure-first：生产未修时必须真实 failed；禁止 sleep/宽 OR/仅返回值自证。
# ===========================================================================

# 合成敏感 marker：仅测试注入；公开表面必须零泄漏
_Q4_REFRESH_MARKER = "Q4_SYNTH_REFRESH_FAIL_SENSITIVE_MARKER_v1n"
_Q4_LSTAT_MARKER = "Q4_SYNTH_LSTAT_OSERROR_SENSITIVE_MARKER_v1n"
_Q4_NOFOLLOW_MARKER = "Q4_SYNTH_NOFOLLOW_STAT_OSERROR_SENSITIVE_MARKER_v1n"


def _q4_reparse_stat(*, is_dir: bool = False) -> Any:
    """用途：合成 reparse/symlink 风格 lstat 结果（M2/T5b 同构）。"""

    class _St:
        st_mode = 0o40777 if is_dir else 0o120777
        st_size = 0 if is_dir else 10
        st_ino = 91
        st_dev = 1
        st_nlink = 1
        st_uid = 0
        st_gid = 0
        st_atime = 0
        st_mtime = 0
        st_ctime = 0
        st_file_attributes = 0x400

    return _St()


def _q4_assert_runner_http_zero(calls: list, body: dict, *, label: str) -> None:
    assert calls == [], f"业务红：{label} runner 必须为 0，actual={calls}"
    assert body["status"] == "failed", (
        f"业务红：{label} 必须 fail-closed failed，actual={body['status']} body={body}"
    )
    # 共享路径门：result 不得带 remote 二键/成功三键
    assert body.get("result") is None, (
        f"业务红：{label} 路径门 result 必须 is None，actual={body.get('result')!r}"
    )


def test_v1n_task_release_gate_q4_upload_root_parent_chain_fail_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """
    Q4-G1：上传根/父链 fail-closed（failure-first）。
    - upload_dir 静态 reparse
    - project_dir 检查后被替换为 junction/reparse
    - 任一层 lstat/reparse 探测 OSError
    - nofollow stat OSError
    全部 runner/HTTP=0；五域零写。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    calls: list[str] = []

    def guard(*a, **k):
        calls.append("run")
        raise AssertionError("路径 fail-closed 后不得进入 remote runner")

    _patch_remote_run(monkeypatch, mod, guard)
    settings = get_settings()
    upload_root = Path(settings.upload_dir).resolve()

    # --- A) upload_dir 静态 reparse：可信根本身被标为 reparse 必须拒绝 ---
    calls.clear()
    pid_a = _create_project(client, "V1N-Q4-root")
    _upload(client, pid_a, "root.pdf", b"%PDF-Q4-ROOT", "application/pdf")
    before_a = _snapshot_domains(client, pid_a)
    real_path_lstat = Path.lstat
    real_os_lstat = os.lstat
    real_is_symlink = Path.is_symlink

    def _root_lstat(self, *a, **k):
        p = Path(self)
        try:
            if p.resolve() == upload_root:
                return _q4_reparse_stat(is_dir=True)
        except OSError:
            pass
        # 字面 upload_dir 字符串也命中（resolve 前后）
        if str(p) == str(upload_root) or str(p.resolve()) == str(upload_root):
            return _q4_reparse_stat(is_dir=True)
        return real_path_lstat(self, *a, **k)

    def _root_os_lstat(path, *a, **k):
        try:
            if Path(path).resolve() == upload_root:
                return _q4_reparse_stat(is_dir=True)
        except OSError:
            pass
        return real_os_lstat(path, *a, **k)

    def _root_is_symlink(self) -> bool:
        try:
            if Path(self).resolve() == upload_root:
                return True
        except OSError:
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "lstat", _root_lstat)
    monkeypatch.setattr(os, "lstat", _root_os_lstat)
    monkeypatch.setattr(Path, "is_symlink", _root_is_symlink, raising=False)
    body_a = _parse_remote(client, pid_a)
    _q4_assert_runner_http_zero(calls, body_a, label="upload_dir-static-reparse")
    _assert_domains_unchanged(
        client, pid_a, before_a, label="q4-upload-root", task_id=body_a["id"]
    )
    # 恢复
    monkeypatch.setattr(Path, "lstat", real_path_lstat)
    monkeypatch.setattr(os, "lstat", real_os_lstat)
    monkeypatch.setattr(Path, "is_symlink", real_is_symlink, raising=False)

    # --- B) project_dir 在父检查后被替换为 reparse（TOCTOU）---
    calls.clear()
    pid_b = _create_project(client, "V1N-Q4-proj")
    _upload(client, pid_b, "proj.pdf", b"%PDF-Q4-PROJ", "application/pdf")
    from app.services import file_service

    db_b = SessionLocal()
    try:
        row_b = db_b.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid_b)
        ).one()
        stored_b = row_b.stored_name
    finally:
        db_b.close()
    leaf_b = Path(file_service.resolve_path(settings, pid_b, stored_b)).resolve()
    project_dir_b = leaf_b.parent
    external_b = _track_temp(tmp_path / "q4-external-proj")
    external_b.mkdir(parents=True, exist_ok=True)
    twin_b = external_b / stored_b
    twin_b.write_bytes(leaf_b.read_bytes())
    assert twin_b.stat().st_size == leaf_b.stat().st_size
    before_b = _snapshot_domains(client, pid_b)
    parent_checks = {"n": 0}
    real_is_rep = task_service._is_symlink_or_reparse

    def _parent_then_replace(path: Path) -> bool:
        # 首次对 project_dir 判定为非 reparse 后，立即替换为指向 external 的 junction/目录 reparse 证据
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved == project_dir_b or str(path) == str(project_dir_b):
            parent_checks["n"] += 1
            if parent_checks["n"] == 1:
                # 模拟「检查通过」后目录被换成 reparse 指向外部
                # 不依赖管理员权限：后续 leaf resolve 跟随到 external 时，
                # 正确实现须在使用前再次 no-follow 校验父链并拒绝。
                # 用 lstat seam 让「再检」可见 reparse。
                return False
            return True  # 再次探测必须看见 reparse
        return real_is_rep(path)

    monkeypatch.setattr(task_service, "_is_symlink_or_reparse", _parent_then_replace)
    # 同时让 resolve 后的 path 可落到 external twin（模拟 junction 跟随）
    real_resolve = file_service.resolve_path

    def _resolve_follow_external(settings_a, project_id, stored_name):
        p = real_resolve(settings_a, project_id, stored_name)
        if project_id == pid_b and parent_checks["n"] >= 1:
            return twin_b
        return p

    monkeypatch.setattr(file_service, "resolve_path", _resolve_follow_external)
    body_b = _parse_remote(client, pid_b)
    _q4_assert_runner_http_zero(calls, body_b, label="project_dir-replace-reparse")
    # Q4：必须发生第二次父链 reparse 探测；仅 resolved 越界不得冒充关闭 TOCTOU
    assert parent_checks["n"] >= 2, (
        f"业务红：project_dir 替换后须再次父链 reparse 探测，"
        f"parent_checks.n={parent_checks['n']}"
    )
    _assert_domains_unchanged(
        client, pid_b, before_b, label="q4-project-replace", task_id=body_b["id"]
    )
    monkeypatch.setattr(task_service, "_is_symlink_or_reparse", real_is_rep)
    monkeypatch.setattr(file_service, "resolve_path", real_resolve)

    # --- C) 任一层 lstat/reparse 探测 OSError → fail-closed（禁止 return False）---
    calls.clear()
    pid_c = _create_project(client, "V1N-Q4-lstat")
    _upload(client, pid_c, "lstat.pdf", b"%PDF-Q4-LSTAT", "application/pdf")
    before_c = _snapshot_domains(client, pid_c)
    db_c = SessionLocal()
    try:
        row_c = db_c.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid_c)
        ).one()
        stored_c = row_c.stored_name
    finally:
        db_c.close()
    leaf_c = Path(file_service.resolve_path(settings, pid_c, stored_c)).resolve()

    def _lstat_oserror(self, *a, **k):
        p = Path(self)
        try:
            if p.resolve() == leaf_c or str(self).endswith(stored_c):
                raise OSError(_Q4_LSTAT_MARKER + " " + FAKE_TOKEN)
        except OSError as exc:
            if _Q4_LSTAT_MARKER in str(exc):
                raise
        return real_path_lstat(self, *a, **k)

    def _os_lstat_oserror(path, *a, **k):
        if stored_c in str(path) or str(leaf_c) in str(path):
            raise OSError(_Q4_LSTAT_MARKER + " " + FAKE_TOKEN)
        return real_os_lstat(path, *a, **k)

    monkeypatch.setattr(Path, "lstat", _lstat_oserror)
    monkeypatch.setattr(os, "lstat", _os_lstat_oserror)
    body_c = _parse_remote(client, pid_c)
    _q4_assert_runner_http_zero(calls, body_c, label="lstat-OSError-fail-closed")
    err_c = str(body_c.get("error") or "")
    assert _Q4_LSTAT_MARKER not in err_c, "业务红：lstat OSError marker 不得泄漏到 error"
    assert FAKE_TOKEN not in err_c
    _assert_domains_unchanged(
        client, pid_c, before_c, label="q4-lstat-oserror", task_id=body_c["id"]
    )
    monkeypatch.setattr(Path, "lstat", real_path_lstat)
    monkeypatch.setattr(os, "lstat", real_os_lstat)

    # --- D) nofollow stat OSError → fail-closed（禁止回退 follow stat）---
    calls.clear()
    pid_d = _create_project(client, "V1N-Q4-nf")
    content_d = b"%PDF-Q4-NOFOLLOW-SIZE-16"
    _upload(client, pid_d, "nf.pdf", content_d, "application/pdf")
    before_d = _snapshot_domains(client, pid_d)
    db_d = SessionLocal()
    try:
        row_d = db_d.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid_d)
        ).one()
        stored_d = row_d.stored_name
        declared_d = int(row_d.size_bytes or 0)
    finally:
        db_d.close()
    leaf_d = Path(file_service.resolve_path(settings, pid_d, stored_d)).resolve()
    assert declared_d == len(content_d)
    real_path_stat = Path.stat

    def _nofollow_oserror(self, *a, **k):
        follow = k.get("follow_symlinks", True)
        # Path.stat(follow_symlinks=False) 或位置兼容
        if a:
            # 旧签名极少见；忽略
            pass
        p = Path(self)
        try:
            matched = p.resolve() == leaf_d or str(self).endswith(stored_d)
        except OSError:
            matched = str(self).endswith(stored_d)
        if matched and follow is False:
            raise OSError(_Q4_NOFOLLOW_MARKER + " " + FAKE_TOKEN)
        return real_path_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _nofollow_oserror)
    body_d = _parse_remote(client, pid_d)
    _q4_assert_runner_http_zero(calls, body_d, label="nofollow-stat-OSError")
    err_d = str(body_d.get("error") or "")
    assert _Q4_NOFOLLOW_MARKER not in err_d
    assert FAKE_TOKEN not in err_d
    _assert_domains_unchanged(
        client, pid_d, before_d, label="q4-nofollow-oserror", task_id=body_d["id"]
    )
    monkeypatch.setattr(Path, "stat", real_path_stat)
    get_settings.cache_clear()


def test_v1n_task_release_gate_q4_remote_source_trusted_root_final_handle_contract(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    Q4-G1 契约冻结：RemoteSource 协作须携带启动时冻结的可信非 reparse upload root；
    最终句柄路径校验 seam 存在；禁止仅靠 Path.resolve 字符串边界。
    task 接线必须把 trusted_upload_root 传入 run_remote_mineru_parse。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    run_fn = _require_attr(mod, "run_remote_mineru_parse")
    sig = inspect.signature(run_fn)
    assert "trusted_upload_root" in sig.parameters, (
        "业务红：run_remote_mineru_parse 必须接受 trusted_upload_root（任务/client 协作契约）"
    )
    assert hasattr(mod, "_v1n_final_path_for_fd"), (
        "业务红：必须提供 _v1n_final_path_for_fd(fd)->str 最终句柄路径 seam"
    )
    # 冻结：trusted 根须为 keyword-only 或显式关键字形参
    p_root = sig.parameters["trusted_upload_root"]
    assert p_root.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ), f"业务红：trusted_upload_root 形参 kind 非法 actual={p_root.kind}"

    captured: list[dict[str, Any]] = []
    # Q3：调用前冻结 expected_root；fake 只 capture，禁止内部 AssertionError 假绿
    expected_root = Path(get_settings().upload_dir).resolve()

    def fake_run(sources, *, token, cancel_check, **kwargs):
        captured.append(
            {
                "trusted_upload_root": kwargs.get("trusted_upload_root", "MISSING"),
                "n_sources": len(sources),
            }
        )
        md = "# Q4_TRUSTED_ROOT_OK\n"
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)
    pid = _create_project(client, "V1N-Q4-trust")
    _upload(client, pid, "t.pdf", b"%PDF-Q4-T", "application/pdf")
    body = _parse_remote(client, pid)
    assert len(captured) == 1, (
        f"业务红：runner 必须精确 1 次以观测 trusted_upload_root，actual={len(captured)}"
    )
    root = captured[0]["trusted_upload_root"]
    assert root is not None and root != "MISSING", (
        "业务红：task 接线必须传入 trusted_upload_root，禁止缺省 None 逃逸"
    )
    actual = Path(root).resolve()
    assert actual == expected_root, (
        f"业务红：trusted_upload_root 必须等于启动冻结 upload 根，"
        f"actual={actual} expected={expected_root}"
    )
    # 成功路径必须 success（禁止 failed 折叠假绿）
    assert body["status"] == "success", (
        f"业务红：trusted_upload_root 契约门必须 success，actual={body['status']!r}"
    )
    get_settings.cache_clear()


def test_v1n_task_release_gate_q4_cancel_refresh_fail_closed_interrupted_zero_external(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """
    Q4-G2：remote cancel_check 的 db.refresh 抛合成敏感异常 → 视为 interrupted；
    fake runner 在 cancel_check 后零后续外部动作；公开表面固定中文、marker 零泄漏。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    err_cls = _require_attr(mod, "RemoteMineruError")
    out_cls = _require_attr(mod, "RemoteParseOutput")
    external_actions: list[str] = []
    check_results: list[Any] = []
    from app.core import database as dbmod

    real_refresh = dbmod.Session.refresh
    refresh_hits = {"n": 0}

    def boom_refresh(self, instance, *a, **k):
        # 仅 runner 内 cancel_check 窗口注入；禁止破坏进度/finalizer 的其它 refresh
        if (
            isinstance(instance, ProjectTaskRow)
            and getattr(boom_refresh, "_arm", False)
        ):
            refresh_hits["n"] += 1
            raise RuntimeError(
                f"{_Q4_REFRESH_MARKER} {FAKE_TOKEN} {FAKE_BATCH} {BODY_CANARY}"
            )
        return real_refresh(self, instance, *a, **k)

    def fake_run(sources, *, token, cancel_check, **kwargs):
        boom_refresh._arm = True  # type: ignore[attr-defined]
        try:
            # 每个外部动作前检查（对齐 client 契约）
            r = cancel_check()
        finally:
            # 立即撤防：后续 finalizer/进度 refresh 不得再炸出敏感异常
            boom_refresh._arm = False  # type: ignore[attr-defined]
        check_results.append(r)
        # fail-closed：refresh 失败必须让 check 指示停止（True）或等价中断
        if r is True:
            raise err_cls(
                diagnostic_code="interrupted",
                message=FIXED_MESSAGES["interrupted"],
            )
        # 若错误地返回 False，后续外部动作会被记录 → 业务红
        external_actions.append("POST_file_urls_batch")
        external_actions.append("PUT_presign")
        external_actions.append("GET_poll")
        external_actions.append("GET_zip")
        md = "# Q4_SHOULD_NOT_SUCCEED\n"
        return out_cls(markdown=md, file_count=1, chars=len(md))

    monkeypatch.setattr(dbmod.Session, "refresh", boom_refresh)
    _patch_remote_run(monkeypatch, mod, fake_run)

    pid = _create_project(client, "V1N-Q4-cref")
    _upload(client, pid, ORIGINAL_FILENAME, b"%PDF-Q4-CR", "application/pdf")
    before = _snapshot_domains(client, pid)
    temp_abs = str(Path(get_settings().upload_dir).resolve())
    with caplog.at_level(logging.DEBUG):
        body = _parse_remote(client, pid)

    assert check_results, "业务红：fake runner 必须调用 cancel_check"
    assert check_results[0] is True, (
        f"业务红：refresh 失败时 cancel_check 必须 fail-closed 为 True（interrupted），"
        f"actual={check_results!r}"
    )
    assert external_actions == [], (
        f"业务红：cancel_check 中断后零后续外部动作，actual={external_actions}"
    )
    assert refresh_hits["n"] >= 1, "业务红：必须真实触发 refresh 失败注入"
    # 未 API cancel → failed + 二键 interrupted（与 t9d 一致）
    assert body["status"] == "failed", (
        f"业务红：refresh 失败 interrupted 必须 failed，actual={body['status']}"
    )
    _assert_fail_result(body, code="interrupted")
    _assert_domains_unchanged(
        client, pid, before, label="q4-cancel-refresh", task_id=body["id"]
    )
    _scan_task_surfaces(
        client,
        pid,
        body,
        extra=[
            temp_abs,
            _Q4_REFRESH_MARKER,
            FAKE_TOKEN,
            FAKE_BATCH,
            BODY_CANARY,
            ORIGINAL_FILENAME,
        ],
        caplog_text=caplog.text,
        monkeypatch=monkeypatch,
    )
    public_blob = json.dumps(body, ensure_ascii=False) + (caplog.text or "")
    assert _Q4_REFRESH_MARKER not in public_blob
    assert FAKE_TOKEN not in public_blob
    get_settings.cache_clear()


def test_v1n_task_release_gate_q4_managed_cancel_refresh_fail_closed_representative(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    Q4-G2 代表门：managed 同类 cancel_check 闭包 refresh 失败亦须 fail-closed interrupted；
    不扩大生产文件白名单；仅观测 managed 路径。
    """
    from app.services import managed_parse_runtime_service as managed_svc
    from app.core import database as dbmod

    assert hasattr(managed_svc, "run_managed_parse")
    core = managed_svc.get_core_module()
    assert core is not None

    rt = _track_temp(Path(tempfile.mkdtemp(prefix="v1n_q4_m_")))
    try:
        cli = rt / "venv" / "Scripts" / "mineru.exe"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_bytes(b"MZ-fake")
        marker = rt / "models" / ".biaoshu-ready"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"ready\n")
        manifest = rt / "runtime-manifest.json"
        five = {
            "schemaVersion": 1,
            "engine": "mineru",
            "cliRelativePath": "venv/Scripts/mineru.exe",
            "modelMarkerRelativePath": "models/.biaoshu-ready",
            "requiredFreeBytes": 1,
        }
        manifest.write_text(json.dumps(five, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(MANIFEST_ENV, str(manifest))
        get_settings.cache_clear()
        settings = get_settings()
        if hasattr(settings, "managed_ocr_manifest_path"):
            monkeypatch.setattr(settings, "managed_ocr_manifest_path", str(manifest))

        def fake_ready(*a, **k):
            from types import SimpleNamespace

            return SimpleNamespace(ok=True, diagnostic_code="static_ready")

        def fake_recheck(*a, **k):
            return True

        monkeypatch.setattr(core, "validate_runtime_ready", fake_ready)
        monkeypatch.setattr(core, "recheck_cli_and_marker", fake_recheck)

        external: list[str] = []
        checks: list[Any] = []
        real_refresh = dbmod.Session.refresh

        def boom_refresh(self, instance, *a, **k):
            if isinstance(instance, ProjectTaskRow) and getattr(
                boom_refresh, "_arm", False
            ):
                raise RuntimeError(f"{_Q4_REFRESH_MARKER} managed {FAKE_TOKEN}")
            return real_refresh(self, instance, *a, **k)

        def fake_managed(sources, *, manifest_path, cancel_check, **kwargs):
            boom_refresh._arm = True  # type: ignore[attr-defined]
            try:
                r = cancel_check()
            finally:
                boom_refresh._arm = False  # type: ignore[attr-defined]
            checks.append(r)
            if r is True:
                # managed 中断：抛 PreflightError interrupted 或等价
                err_cls = getattr(core, "PreflightError", None)
                if err_cls is not None:
                    try:
                        raise err_cls(
                            diagnostic_code="interrupted",
                            message="操作已中断",
                        )
                    except TypeError:
                        exc = err_cls("interrupted")
                        try:
                            exc.diagnostic_code = "interrupted"
                        except Exception:
                            pass
                        raise exc
                raise RuntimeError("interrupted")
            external.append("managed_cli_run_one")
            from types import SimpleNamespace

            return SimpleNamespace(
                markdown="# MANAGED_Q4_SHOULD_NOT\n",
                file_count=1,
                chars=20,
            )

        monkeypatch.setattr(dbmod.Session, "refresh", boom_refresh)
        monkeypatch.setattr(managed_svc, "run_managed_parse", fake_managed)

        pid = _create_project(client, "V1N-Q4-mref")
        _upload(client, pid, "m.pdf", b"%PDF-Q4-M", "application/pdf")
        before = _snapshot_domains(client, pid)
        res = client.post(
            f"/api/projects/{pid}/tasks?sync=true",
            json={"type": "parse", "payload": {"engine": "managed"}},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert checks, "业务红：managed runner 必须调用 cancel_check"
        assert checks[0] is True, (
            f"业务红：managed refresh 失败 cancel_check 必须 True，actual={checks!r}"
        )
        assert external == [], (
            f"业务红：managed 中断后零 CLI/外部动作，actual={external}"
        )
        assert body["status"] == "failed", (
            f"业务红：managed interrupted 必须 failed，actual={body['status']}"
        )
        result = body.get("result") or {}
        assert result.get("engine") == "managed"
        assert result.get("diagnosticCode") == "interrupted", (
            f"业务红：managed diagnosticCode 必须 interrupted，actual={result!r}"
        )
        err = str(body.get("error") or "")
        assert _Q4_REFRESH_MARKER not in err
        assert FAKE_TOKEN not in err
        _assert_domains_unchanged(
            client, pid, before, label="q4-managed-refresh", task_id=body["id"]
        )
    finally:
        if rt.exists():
            shutil.rmtree(rt)
        assert not rt.exists()
    get_settings.cache_clear()


def test_v1n_task_release_gate_q4_cancel_wins_finalizer_window_zero_partial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    Q4-G3a：两真实 Session + barrier：cancel 在 finalizer 五域提交窗口胜出；
    最终精确 cancelled、result None、editor/revision/project/success event 零部分写回。
    窗口定义：_upsert_editor_state_for_task（finalizer 首写 helper）调用之前，
    已捕获 worker Session/目标 project 身份且尚未调用 real helper
    （此时尚无五域写锁，cancel 独立 Session 可先真实 commit；
    禁止 sleep/宽 OR/autoflush/session.dirty/flag_modified/仅返回值自证）。
    try/finally 无条件 release + cancel_done.set + worker join，失败不污染 teardown。
    """
    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    md = f"# Q4_FINALIZER_CANCEL_RACE\n{BODY_CANARY}\n"

    def fake_run(*a, **k):
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    in_window = threading.Event()
    cancel_done = threading.Event()
    workers: list[threading.Thread] = []
    matched: list[threading.Thread] = []
    real_thread = threading.Thread
    window_hits = {"n": 0}

    class _Track(real_thread):  # type: ignore[valid-type,misc]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            workers.append(self)

    monkeypatch.setattr(threading, "Thread", _Track)
    import app.services.task_service as _ts

    monkeypatch.setattr(_ts.threading, "Thread", _Track)

    real_upsert = task_service._upsert_editor_state_for_task
    # 仅 worker 同一 Session + 目标 project 在首写 helper 前 barrier，避免 cancel 互锁
    worker_gate: dict[str, Any] = {
        "db": None,
        "workspace_id": None,
        "project_id": None,
    }

    def spy_upsert(*a, **k):
        # _upsert_editor_state_for_task(db, workspace_id, project_id, **kwargs)
        # finalizer 首写：commit=False；禁止依赖 dirty/flag_modified
        if k.get("commit") is False:
            db = a[0] if a else None
            ws = a[1] if len(a) > 1 else None
            proj = a[2] if len(a) > 2 else None
            if worker_gate["db"] is None:
                worker_gate["db"] = db
                worker_gate["workspace_id"] = ws
                worker_gate["project_id"] = proj
            if (
                db is worker_gate["db"]
                and proj == worker_gate["project_id"]
                and ws == worker_gate["workspace_id"]
            ):
                window_hits["n"] += 1
                in_window.set()
                assert cancel_done.wait(timeout=15), (
                    "业务红：cancel 未在 finalizer 首写 helper 前窗口完成"
                )
            # 尚未调用 real helper 时 cancel 已真实 commit
        return real_upsert(*a, **k)

    monkeypatch.setattr(task_service, "_upsert_editor_state_for_task", spy_upsert)

    try:
        pid = _create_project(client, "V1N-Q4-cw")
        _upload(client, pid, "c.pdf", b"%PDF-Q4-CW", "application/pdf")
        before = _snapshot_domains(client, pid)

        body0 = _parse_remote(client, pid, sync=False)
        tid = body0["id"]
        assert in_window.wait(timeout=15), (
            "业务红：必须进入 finalizer _upsert_editor_state_for_task 首写前窗口"
        )

        # 第二真实 Session：HTTP cancel（独立会话；worker 尚未进入五域首写）
        cancel_res = client.post(f"/api/projects/{pid}/tasks/{tid}/cancel")
        assert cancel_res.status_code == 200, (
            f"业务红：窗口内 cancel 必须 200，"
            f"actual={cancel_res.status_code} {cancel_res.text}"
        )
        # cancel 已真实 commit 后再放行 worker 调用 real helper
        cancel_done.set()

        matched = [w for w in workers if w.name == f"task-{tid}"]
        assert len(matched) == 1, (
            f"业务红：必须唯一 worker task-{tid}，"
            f"workers={[w.name for w in workers]}"
        )
        matched[0].join(timeout=15)
        assert not matched[0].is_alive(), "业务红：worker 必须结束"

        final = client.get(f"/api/projects/{pid}/tasks/{tid}")
        assert final.status_code == 200
        body = final.json()
        assert body["status"] == "cancelled", (
            f"业务红：cancel 胜出最终必须 cancelled，"
            f"actual={body['status']} body={body}"
        )
        assert body.get("result") is None, (
            f"业务红：cancelled result 必须 None，actual={body.get('result')!r}"
        )
        # 五域：不得留下成功正文/修订/项目步进/success event
        assert md.strip() not in (_editor_md(client, pid) or ""), (
            "业务红：cancel 胜出不得写回 finalizer 正文"
        )
        assert BODY_CANARY not in (_editor_md(client, pid) or "")
        after = _snapshot_domains(client, pid, task_id=tid)
        assert after["rev"] == before["rev"], (
            f"业务红：cancel 胜出 revision 不得增加，"
            f"{before['rev']}->{after['rev']}"
        )
        assert after["status"] == before["status"], (
            f"业务红：cancel 胜出 project.status 不得被 success 包改写，"
            f"{before['status']}->{after['status']}"
        )
        assert after["step"] == before["step"], (
            f"业务红：cancel 胜出 technical_plan_step 不得改写，"
            f"{before['step']}->{after['step']}"
        )
        assert _success_event_count(pid, tid) == 0, (
            "业务红：cancel 胜出 success event 必须为 0"
        )
        assert window_hits["n"] >= 1, (
            "业务红：必须真实经过 finalizer 首写 helper 前窗口 barrier"
        )
    finally:
        # 无条件 release：断言失败/cancel 异常也不得挂死 worker 或污染 teardown
        cancel_done.set()
        for w in matched:
            if w.is_alive():
                w.join(timeout=15)
        for w in workers:
            if w.is_alive():
                w.join(timeout=5)
        get_settings.cache_clear()


def test_v1n_task_release_gate_q4_finalizer_wins_cancel_cannot_overwrite_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    Q4-G3b：反向——finalizer 已合法抢占 success 后，cancel 不得覆盖 success。
    两 Session + barrier：cancel 在 ACTIVE 检查后写库前挂起；finalizer 先提交 success；
    再放行 cancel；终态必须保持 success + 五域成功包。
    """
    from app.core import database as dbmod
    from app.services import editor_state_service

    _enable_fake_token(monkeypatch)
    mod = _load_client()
    out_cls = _require_attr(mod, "RemoteParseOutput")
    md = f"# Q4_FINALIZER_WINS\n{BODY_CANARY}\n"

    def fake_run(*a, **k):
        return out_cls(markdown=md, file_count=1, chars=len(md))

    _patch_remote_run(monkeypatch, mod, fake_run)

    cancel_in_write_path = threading.Event()
    finalizer_committed = threading.Event()
    allow_cancel_commit = threading.Event()
    workers: list[threading.Thread] = []
    real_thread = threading.Thread

    class _Track(real_thread):  # type: ignore[valid-type,misc]
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            workers.append(self)

    monkeypatch.setattr(threading, "Thread", _Track)
    import app.services.task_service as _ts

    monkeypatch.setattr(_ts.threading, "Thread", _Track)

    real_up = editor_state_service.upsert_editor_state
    real_maybe = task_service._maybe_record_task_status_event
    real_commit = dbmod.Session.commit
    # Q8：按 Session 身份拦 commit，删除对 production dirty/flag_modified 的依赖
    worker_sess: dict[str, Any] = {"db": None}

    def spy_up(*a, **k):
        # finalizer 入口：捕获 worker Session；等 cancel 已进入写路径
        if k.get("commit") is False:
            worker_sess["db"] = a[0] if a else None
            assert cancel_in_write_path.wait(timeout=15), (
                "业务红：finalizer 等待 cancel 进入写路径超时"
            )
        return real_up(*a, **k)

    def spy_commit(self, *a, **k):
        wdb = worker_sess.get("db")
        # cancel Session（非 worker）：须等 finalizer 已提交 success 后再放行
        if wdb is not None and self is not wdb:
            assert finalizer_committed.wait(timeout=15), (
                "业务红：cancel 提交前 finalizer 必须已合法 success"
            )
            assert allow_cancel_commit.wait(timeout=10)
            return real_commit(self, *a, **k)
        result = real_commit(self, *a, **k)
        # worker Session 提交后标记 finalizer 已抢占
        if wdb is not None and self is wdb:
            finalizer_committed.set()
        return result

    def gated_maybe(db, *a, **k):
        t = k.get("task")
        if t is not None and getattr(t, "status", None) == "cancelled":
            cancel_in_write_path.set()
            assert finalizer_committed.wait(timeout=15), (
                "业务红：cancel 写路径等待 finalizer success 超时"
            )
        return real_maybe(db, *a, **k)

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy_up)
    monkeypatch.setattr(dbmod.Session, "commit", spy_commit)
    monkeypatch.setattr(task_service, "_maybe_record_task_status_event", gated_maybe)

    pid = _create_project(client, "V1N-Q4-fw")
    _upload(client, pid, "f.pdf", b"%PDF-Q4-FW", "application/pdf")

    body0 = _parse_remote(client, pid, sync=False)
    tid = body0["id"]

    cancel_holder: dict[str, Any] = {}

    def _cancel_thread():
        try:
            r = client.post(f"/api/projects/{pid}/tasks/{tid}/cancel")
            cancel_holder["status_code"] = r.status_code
            cancel_holder["body"] = r.text
        except Exception as exc:  # noqa: BLE001
            cancel_holder["exc"] = repr(exc)

    t_cancel = real_thread(target=_cancel_thread, name="q4-cancel-racer")
    t_cancel.start()

    matched = [w for w in workers if w.name == f"task-{tid}"]
    assert len(matched) == 1, (
        f"业务红：必须唯一 worker task-{tid}，"
        f"workers={[w.name for w in workers]}"
    )
    matched[0].join(timeout=20)
    assert finalizer_committed.is_set(), (
        f"业务红：finalizer 必须提交 success；cancel_holder={cancel_holder}"
    )
    allow_cancel_commit.set()
    t_cancel.join(timeout=15)
    # Q8：cancel 线程结束 / 无异常 / 确定 HTTP 结果
    assert not t_cancel.is_alive(), (
        f"业务红：cancel 线程必须结束；cancel_holder={cancel_holder}"
    )
    assert "exc" not in cancel_holder, (
        f"业务红：cancel 线程不得抛异常；cancel_holder={cancel_holder}"
    )
    assert isinstance(cancel_holder.get("status_code"), int), (
        f"业务红：cancel 必须产生确定 HTTP status_code；cancel_holder={cancel_holder}"
    )
    assert cancel_holder["status_code"] in {200, 400, 409}, (
        f"业务红：cancel HTTP 状态须为契约终态码 200/400/409，"
        f"actual={cancel_holder['status_code']}"
    )
    assert not matched[0].is_alive(), "业务红：worker 必须结束"

    final = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert final.status_code == 200
    body = final.json()
    assert body["status"] == "success", (
        f"业务红：finalizer 已抢占后 cancel 不得覆盖 success，"
        f"actual={body['status']} cancel_http={cancel_holder}"
    )
    assert isinstance(body.get("result"), dict)
    assert body["result"].get("engine") == ENGINE
    assert BODY_CANARY in (_editor_md(client, pid) or ""), (
        "业务红：success 正文必须保留"
    )
    assert _success_event_count(pid, tid) == 1, (
        "业务红：success event 必须精确 1"
    )
    get_settings.cache_clear()
