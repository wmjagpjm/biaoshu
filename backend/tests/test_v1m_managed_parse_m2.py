"""
模块：V1-M M2 管理式 parse 后端 failure-first 专项（反假绿加强）
用途：在 production 未接线时建立可计数业务红，锁定 shared pure core、path-only locator、
  ASC 全 source 聚合、10/200MiB 与双输出上限、三键/二键 result、单事务 finalizer、
  进程内并发 1 与脱敏；生产未改时必须业务断言红，禁止 collection error / skip / 假绿。
对接：docs/v1m-managed-local-ocr-runtime-contract.md；D1–D10 冻结决策；
  managed_ocr_runtime_core / managed_parse_runtime_service / task_service parse finalizer。
二次开发：
  - 禁止顶层 import 尚不存在的 core/service；缺入口=业务 failed。
  - 仅系统 TEMP + pytest 独立库/uploads；禁止真实 CLI/模型/联网/业务 DB/uploads。
  - 禁止 xdist、skip/xfail、真实 sleep、BoolOp Or 多选一 code 集合。
  - 反假绿：行为/spy/精确等式优先；禁止仅源码 token、条件成功、权限失败 return。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    EditorStateRevisionRow,
    ProjectFileRow,
    ProjectTaskEventRow,
    ProjectTaskRow,
)
from app.services import file_service, parse_engines, parse_service

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

SOURCE_SEPARATOR = "\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n"
MAX_SOURCE_FILES = 10
MAX_TOTAL_SOURCE_BYTES = 200 * 1024 * 1024
MAX_MARKDOWN_CODEPOINTS = 1_000_000
MAX_MARKDOWN_UTF8_BYTES = 2 * 1024 * 1024

SUCCESS_RESULT_KEYS = frozenset({"engine", "fileCount", "chars"})
MANAGED_FAIL_RESULT_KEYS = frozenset({"engine", "diagnosticCode"})

# 未配置 managed：精确二键 code + 固定中文（不得接受任意 M1 code）
FIXED_UNCONFIGURED_CODE = "runtime_manifest_invalid"
FIXED_UNCONFIGURED_ERROR = "运行时清单无效"

# managed 异常哨兵（G4）：service boundary 冻结为 internal_error + M1 固定中文
FIXED_MANAGED_EXCEPTION_CODE = "internal_error"
FIXED_MANAGED_EXCEPTION_ERROR = "预检内部错误"

# M1 诊断码→固定中文（禁止新造文案；code 与文案一一对应）
M1_DIAG_MESSAGES: dict[str, str] = {
    "static_ready": "静态检查通过，尚未运行解析器",
    "ocr_passed": "合成扫描样本解析通过并命中预期标记",
    "runtime_manifest_invalid": "运行时清单无效",
    "cli_missing": "未找到解析器命令或安全类型不合格",
    "model_missing": "模型就绪标记缺失",
    "disk_insufficient": "目标卷可用空间不足",
    "quality_precondition_failed": "质量配置前置条件不满足",
    "parser_failed": "解析器运行失败",
    "parser_timeout": "解析器运行超时",
    "output_invalid": "未找到合法的唯一 Markdown 输出",
    "ocr_marker_missing": "输出未包含预期 OCR 标记或顺序错误",
    "interrupted": "操作已中断",
    "internal_error": "预检内部错误",
    "argument_invalid": "参数无效",
}

# lightweight 失败门：契约不保留 result 时精确 None；error 冻结单一中文
FIXED_ERR_TOO_MANY_SOURCES = "源文件数量超过上限"
FIXED_ERR_TOTAL_SIZE = "源文件总大小超过上限"
FIXED_ERR_SIZE_MISMATCH = "源文件大小不一致"
FIXED_ERR_LEAF_REPARSE = "源文件链接或重解析点被拒绝"
FIXED_ERR_PARENT_REPARSE = "源文件父目录链接或重解析点被拒绝"
FIXED_ERR_TRAVERSAL = "源文件路径越界被拒绝"
FIXED_ERR_CODEPOINTS = "解析正文码点超过上限"
FIXED_ERR_UTF8_BYTES = "解析正文体积超过上限"

# 冻结精确 API 面（禁止任意命名枚举削弱契约）
SERVICE_PUBLIC_RUN = "run_managed_parse"
SERVICE_CORE_ACCESSOR = "get_core_module"
CORE_LOAD_MANIFEST = "load_manifest"
CORE_VALIDATE_READY = "validate_runtime_ready"
CORE_RECHECK = "recheck_cli_and_marker"
CORE_RUN_ONE = "parse_one_file_with_manifest_cli"
CORE_HELPERS = (
    "load_manifest",
    "resolve_under_runtime_root",
    "validate_runtime_ready",
    "check_disk_free",
)

M1_DIAG_CODES = frozenset(
    {
        "static_ready",
        "ocr_passed",
        "runtime_manifest_invalid",
        "cli_missing",
        "model_missing",
        "disk_insufficient",
        "quality_precondition_failed",
        "parser_failed",
        "parser_timeout",
        "output_invalid",
        "ocr_marker_missing",
        "interrupted",
        "internal_error",
        "argument_invalid",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = REPO_ROOT / "tools" / "local-parser" / "managed_ocr_runtime_core.py"
PREFLIGHT_PATH = REPO_ROOT / "tools" / "local-parser" / "managed_runtime_preflight.py"
SERVICE_MOD = "app.services.managed_parse_runtime_service"
CORE_MOD_NAME = "managed_ocr_runtime_core"

_FORBIDDEN_LEAK_FRAGMENTS = (
    "C:\\",
    "C:/",
    "/Users/",
    "\\Users\\",
    "Traceback",
    "ValueError",
    "RuntimeError",
    "FileNotFoundError",
    "OSError",
    "HelperError",
    "PreflightError",
    "Exception",
    "mineru.exe",
    "stdout",
    "stderr",
    "argv",
    "BIAOSHU_MANAGED_OCR_MANIFEST",
    "Cookie",
    "CSRF",
    "api_key",
    "sk-",
)

_TEMP_ROOTS: list[Path] = []
_THIS_FILE = Path(__file__).resolve()
_ALLOWED_DIFF_FILES = frozenset(
    {
        "backend/tests/test_v1m_managed_parse_m2.py",
        "backend/tests/test_parse_engines.py",
        "backend/tests/test_parse_export.py",
        "tools/local-parser/test_managed_runtime_preflight.py",
    }
)

# 五域：正文 / revision / project status+step / task success result / success event
_FIVE_DOMAIN_EMPTY_MD = ""


# ---------------------------------------------------------------------------
# TEMP / 上传根隔离（Q1：保留 roots 快照，清理后断言不存在）
# ---------------------------------------------------------------------------


def _track_temp(root: Path) -> Path:
    resolved = root.resolve()
    _TEMP_ROOTS.append(resolved)
    return resolved


def _make_temp(prefix: str = "v1m_m2_") -> Path:
    return _track_temp(Path(tempfile.mkdtemp(prefix=prefix)))


def _cleanup_temp(root: Path) -> None:
    """用途：删除 TEMP 根；失败必须可见，禁止 ignore_errors 后无证据通过。"""
    if not root.exists():
        return
    shutil.rmtree(root)
    assert not root.exists(), f"TEMP 删除后仍存在: {root}"


@pytest.fixture(autouse=True)
def _m2_temp_upload_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """用途：每测独立 TEMP uploads；清理登记的 TEMP 根（Q1 自守卫）。"""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", str(upload_root.resolve()))
    if hasattr(settings, "managed_ocr_manifest_path"):
        monkeypatch.setattr(settings, "managed_ocr_manifest_path", "")
    monkeypatch.delenv("BIAOSHU_MANAGED_OCR_MANIFEST", raising=False)
    yield upload_root.resolve()
    # Q1：快照 roots，逐根清理后断言均不存在，再 clear
    roots_snapshot = list(_TEMP_ROOTS)
    for root in roots_snapshot:
        _cleanup_temp(root)
    left = [p for p in roots_snapshot if p.exists()]
    _TEMP_ROOTS.clear()
    assert left == [], f"TEMP 未清理: {left}"


@pytest.fixture(autouse=True)
def _reset_parse_engines_registry():
    parse_engines.reset_registry()
    yield
    parse_engines.reset_registry()


# ---------------------------------------------------------------------------
# 懒加载（禁止 collection 期失败）
# ---------------------------------------------------------------------------


def _core_module_available() -> bool:
    return CORE_PATH.is_file()


def _load_core_module() -> Any:
    """用途：按仓库根固定相对路径加载 pure core；缺失=业务红。"""
    assert CORE_PATH.is_file(), (
        "业务红：缺少 tools/local-parser/managed_ocr_runtime_core.py 单一真源"
    )
    unique = f"_v1m_m2_core_{os.getpid()}_{id(CORE_PATH)}"
    spec = importlib.util.spec_from_file_location(unique, CORE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    spec.loader.exec_module(mod)
    # Q2：加载路径必须精确等于仓库固定 CORE_PATH
    loaded = Path(getattr(mod, "__file__", "") or "").resolve()
    assert loaded == CORE_PATH.resolve(), (
        f"业务红：core __file__ 必须等于固定 CORE_PATH，"
        f"actual={loaded} expected={CORE_PATH.resolve()}"
    )
    return mod


def _load_managed_service() -> Any:
    """用途：加载 backend managed service；缺失=业务红。"""
    spec = importlib.util.find_spec(SERVICE_MOD)
    assert spec is not None, f"业务红：缺少 {SERVICE_MOD}"
    return importlib.import_module(SERVICE_MOD)


def _try_load_managed_service() -> Any | None:
    try:
        return _load_managed_service()
    except AssertionError:
        return None


def _require_attr(obj: Any, name: str) -> Any:
    assert hasattr(obj, name), f"业务红：缺少属性/函数 {name}"
    return getattr(obj, name)


def _require_service_public_run(svc: Any) -> Callable:
    """用途：冻结 public managed 入口为 run_managed_parse（R3/R4）。"""
    fn = getattr(svc, SERVICE_PUBLIC_RUN, None)
    assert callable(fn), f"业务红：managed service 缺少精确入口 {SERVICE_PUBLIC_RUN}"
    return fn


def _assert_exact_service_run_api(svc: Any) -> Callable:
    """
    用途：T1/U1 冻结 service 精确 API（参数名/顺序/keyword-only/无 *args/**kwargs）：
      run_managed_parse(sources, *, manifest_path, cancel_check)
      sources 精确 POSITIONAL_OR_KEYWORD；三参数全部 required、无默认值。
      sources 元素为不可变 ManagedSource(path, filename, expected_size)
      返回不可变 ManagedParseOutput(markdown, file_count, chars)
    """
    fn = _require_service_public_run(svc)
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["sources", "manifest_path", "cancel_check"], (
        "业务红：run_managed_parse 参数名/顺序必须精确为 "
        f"(sources, manifest_path, cancel_check)，actual={names}"
    )
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
        "业务红：sources 必须精确 POSITIONAL_OR_KEYWORD，不得 POSITIONAL_ONLY"
    )
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY, (
        "业务红：manifest_path 必须为 keyword-only"
    )
    assert params[2].kind is inspect.Parameter.KEYWORD_ONLY, (
        "业务红：cancel_check 必须为 keyword-only"
    )
    for p in params:
        assert p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ), f"业务红：run_managed_parse 禁止 *args/**kwargs，found={p.name}"
        assert p.default is inspect.Parameter.empty, (
            f"业务红：run_managed_parse 参数 {p.name} 必须 required、无默认值"
        )
    ms = _require_attr(svc, "ManagedSource")
    mo = _require_attr(svc, "ManagedParseOutput")
    _assert_immutable_record(
        ms,
        fields=("path", "filename", "expected_size"),
        sample_kwargs={
            "path": Path("x.pdf"),
            "filename": "x.pdf",
            "expected_size": 1,
        },
    )
    _assert_immutable_record(
        mo,
        fields=("markdown", "file_count", "chars"),
        sample_kwargs={"markdown": "m", "file_count": 1, "chars": 1},
    )
    return fn


def _assert_immutable_record(
    cls: Any,
    *,
    fields: tuple[str, ...],
    sample_kwargs: dict[str, Any],
) -> None:
    """
    用途：T1 — 字段精确等式 + 不可变证据。
    NamedTuple 必须是 tuple 子类；dataclass 必须 frozen=True；
    构造后赋值必须抛 FrozenInstanceError/AttributeError/TypeError。
    """
    name = getattr(cls, "__name__", repr(cls))
    if hasattr(cls, "_fields"):
        actual = tuple(cls._fields)
        assert issubclass(cls, tuple), (
            f"业务红：{name} 作为 NamedTuple 必须是 tuple 子类（不可变）"
        )
    elif hasattr(cls, "__dataclass_fields__"):
        actual = tuple(cls.__dataclass_fields__.keys())
        params = getattr(cls, "__dataclass_params__", None)
        assert params is not None and bool(getattr(params, "frozen", False)), (
            f"业务红：{name} dataclass 必须 frozen=True"
        )
    elif hasattr(cls, "__annotations__"):
        actual = tuple(cls.__annotations__.keys())
    else:
        actual = tuple(
            n for n in getattr(cls, "__slots__", ()) if not str(n).startswith("_")
        )
    assert actual == fields, (
        f"业务红：{name} 字段必须精确等于 {fields}，actual={actual}"
    )
    obj = cls(**sample_kwargs)
    mutated = False
    try:
        setattr(obj, fields[0], sample_kwargs[fields[0]])
        mutated = True
    except (AttributeError, TypeError):
        mutated = False
    except Exception as exc:
        # frozen dataclass 常见 FrozenInstanceError（dataclasses 模块）
        if type(exc).__name__ in {"FrozenInstanceError", "FrozenError"}:
            mutated = False
        else:
            raise
    assert not mutated, f"业务红：{name} 构造后赋值必须失败（不可变）"


def _make_managed_source(
    svc: Any, *, path: Path, filename: str, expected_size: int
) -> Any:
    """用途：构造 service 暴露的不可变 ManagedSource（精确 keyword，无 fallback）。"""
    ms_cls = _require_attr(svc, "ManagedSource")
    return ms_cls(path=path, filename=filename, expected_size=expected_size)


def _call_service_run(
    fn: Callable,
    sources: list[Any],
    *,
    manifest_path: Path | str,
    cancel_check: Callable[[], bool],
) -> Any:
    """用途：仅以精确签名调用 public run；禁止零参/猜签名。"""
    return fn(
        sources,
        manifest_path=manifest_path,
        cancel_check=cancel_check,
    )


def _assert_exact_core_runner_api(core: Any) -> Callable:
    """
    用途：T1/U1 冻结 core 精确 runner：
      parse_one_file_with_manifest_cli(ready, input_path, *, task_deadline, cancel_check)
    ready/input_path 精确 POSITIONAL_OR_KEYWORD；四参数全部 required、无默认值；
    禁止 *args/**kwargs 与 args/path 别名。
    """
    runner = _require_attr(core, CORE_RUN_ONE)
    assert callable(runner), f"业务红：core 缺少精确 runner {CORE_RUN_ONE}"
    for banned in ("run_single_file", "run_mineru_on_file", "parse_source_file"):
        assert not callable(getattr(core, banned, None)), (
            f"业务红：core 不得再暴露别名 runner {banned}；唯一名为 {CORE_RUN_ONE}"
        )
    sig = inspect.signature(runner)
    params = list(sig.parameters.values())
    names = [p.name for p in params]
    assert names == ["ready", "input_path", "task_deadline", "cancel_check"], (
        "业务红：core runner 参数名/顺序必须精确为 "
        f"(ready, input_path, *, task_deadline, cancel_check)，actual={names}"
    )
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
        "业务红：ready 必须精确 POSITIONAL_OR_KEYWORD，不得 POSITIONAL_ONLY"
    )
    assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, (
        "业务红：input_path 必须精确 POSITIONAL_OR_KEYWORD，不得 POSITIONAL_ONLY"
    )
    assert params[2].kind is inspect.Parameter.KEYWORD_ONLY, (
        "业务红：task_deadline 必须为 keyword-only"
    )
    assert params[3].kind is inspect.Parameter.KEYWORD_ONLY, (
        "业务红：cancel_check 必须为 keyword-only"
    )
    for p in params:
        assert p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ), f"业务红：core runner 禁止 *args/**kwargs，found={p.name}"
        assert p.default is inspect.Parameter.empty, (
            f"业务红：core runner 参数 {p.name} 必须 required、无默认值"
        )
    assert getattr(core, "FILE_TIMEOUT_SEC", None) == 1800
    assert float(getattr(core, "POLL_INTERVAL_SEC")) <= 1.0
    return runner


def _assert_exact_diagnostic(exc: BaseException, expected_code: str) -> None:
    """
    用途：U2 — 对齐 M1 PreflightError 异常 API：
      必须直接 hasattr diagnostic_code 且精确相等；
      必须直接 hasattr message 且 exc.message == M1 固定文案；
      同时 str(exc) == exc.message。
      禁止 .code/.error/.args fallback、substring in、二选一。
    """
    assert hasattr(exc, "diagnostic_code"), (
        f"业务红：异常必须直接暴露 diagnostic_code，exc={type(exc).__name__!r}"
    )
    assert exc.diagnostic_code == expected_code, (
        f"业务红：diagnostic_code 必须精确为 {expected_code!r}，"
        f"actual={exc.diagnostic_code!r} exc={exc!r}"
    )
    expected_msg = _m1_error_for(expected_code)
    assert hasattr(exc, "message"), (
        f"业务红：异常必须直接暴露 message（对齐 PreflightError），"
        f"exc={type(exc).__name__!r}"
    )
    assert exc.message == expected_msg, (
        f"业务红：exc.message 必须精确等于 M1 文案 {expected_msg!r}，"
        f"actual={exc.message!r}"
    )
    assert str(exc) == exc.message, (
        f"业务红：str(exc) 必须等于 exc.message，"
        f"str={str(exc)!r} message={exc.message!r}"
    )


def _call_core_runner(
    runner: Callable,
    ready: Any,
    input_path: Path,
    *,
    task_deadline: float,
    cancel_check: Callable[[], bool],
) -> Any:
    """用途：仅以精确签名调用 core runner；禁止 Path 猜签名/备用入口。"""
    return runner(
        ready,
        input_path,
        task_deadline=task_deadline,
        cancel_check=cancel_check,
    )


def _m1_error_for(code: str) -> str:
    """用途：M1 code→固定中文；未知 code 立即失败。"""
    assert code in M1_DIAG_MESSAGES, f"业务红：未知 managed diagnosticCode={code!r}"
    return M1_DIAG_MESSAGES[code]


def _require_service_core_module(svc: Any) -> Any:
    """
    用途：冻结 service 精确 core accessor；证明 service 实际加载来源（R1）。
    禁止回退到测试侧独立 _load_core_module 冒充 service 绑定。
    """
    accessor = getattr(svc, SERVICE_CORE_ACCESSOR, None)
    if callable(accessor):
        core_mod = accessor()
    else:
        # 允许模块级只读绑定属性 core（非 loader 回退）
        core_mod = getattr(svc, "core", None)
        assert core_mod is not None and hasattr(core_mod, "__file__"), (
            f"业务红：service 必须暴露 {SERVICE_CORE_ACCESSOR}() 或模块级 core 绑定"
        )
    loaded = Path(getattr(core_mod, "__file__", "") or "").resolve()
    assert loaded == CORE_PATH.resolve(), (
        f"业务红：service 实际 core.__file__ 必须精确等于 CORE_PATH，"
        f"actual={loaded} expected={CORE_PATH.resolve()}"
    )
    return core_mod


def _valid_manifest_dict(
    *,
    cli_rel: str = "venv/Scripts/mineru.exe",
    model_rel: str = "models/.biaoshu-ready",
    required_free: int = 1,
) -> dict[str, Any]:
    """用途：M1 精确五键合法 manifest（禁止 {} 提前无关失败）。"""
    return {
        "schemaVersion": 1,
        "engine": "mineru",
        "cliRelativePath": cli_rel,
        "modelMarkerRelativePath": model_rel,
        "requiredFreeBytes": required_free,
    }


def _prepare_fake_runtime(prefix: str = "v1m_m2_rt_") -> tuple[Path, Path]:
    """
    用途：构造仓外 fake runtime 根 + 五键 manifest + 普通文件 CLI/marker。
    返回：(runtime_root, manifest_path)
    """
    root = _make_temp(prefix)
    runtime = root / "runtime-root"
    runtime.mkdir(parents=True, exist_ok=True)
    cli_rel = "venv/Scripts/mineru.exe"
    model_rel = "models/.biaoshu-ready"
    cli_path = runtime / "venv" / "Scripts" / "mineru.exe"
    cli_path.parent.mkdir(parents=True, exist_ok=True)
    cli_path.write_bytes(b"MZ-fake-not-executed")
    model_path = runtime / "models" / ".biaoshu-ready"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"ready\n")
    manifest = runtime / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(_valid_manifest_dict(cli_rel=cli_rel, model_rel=model_rel), ensure_ascii=False),
        encoding="utf-8",
    )
    return runtime, manifest


def _point_settings_manifest(monkeypatch: pytest.MonkeyPatch, manifest: Path) -> None:
    settings = get_settings()
    assert hasattr(settings, "managed_ocr_manifest_path"), (
        "业务红：Settings 缺少 managed_ocr_manifest_path"
    )
    monkeypatch.setattr(settings, "managed_ocr_manifest_path", str(manifest.resolve()))
    monkeypatch.setenv("BIAOSHU_MANAGED_OCR_MANIFEST", str(manifest.resolve()))


def _assert_failed_fixed(
    body: dict,
    *,
    error: str,
    result_must_be_none: bool = True,
) -> None:
    """用途：失败门精确 error；lightweight 默认 result is None（R5）。"""
    assert body["status"] == "failed", f"必须 failed，实际={body['status']}"
    assert body.get("error") == error, (
        f"必须固定 error={error!r}，实际={body.get('error')!r}"
    )
    if result_must_be_none:
        assert body.get("result") is None, (
            f"lightweight 失败 result 必须为 None，实际={body.get('result')!r}"
        )


def _assert_post_get_error_consistent(client: TestClient, pid: str, body: dict) -> None:
    """用途：POST 与 GET 失败 error/result 一致。"""
    tid = body["id"]
    got = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got.status_code == 200
    data = got.json()
    assert data.get("status") == body.get("status")
    assert data.get("error") == body.get("error")
    assert data.get("result") == body.get("result")


# ---------------------------------------------------------------------------
# 小夹具
# ---------------------------------------------------------------------------


def _create_project(client: TestClient, name: str = "V1M-M2") -> str:
    res = client.post("/api/projects", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _upload_md(client: TestClient, pid: str, filename: str, text: str) -> dict:
    res = client.post(
        f"/api/projects/{pid}/files",
        files={"file": (filename, BytesIO(text.encode("utf-8")), "text/markdown")},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _parse(
    client: TestClient,
    pid: str,
    *,
    engine: str | None = None,
    extra_payload: dict | None = None,
    query: dict | None = None,
    headers: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {"type": "parse"}
    body: dict[str, Any] = {}
    if engine is not None:
        body["engine"] = engine
    if extra_payload:
        body.update(extra_payload)
    if body:
        payload["payload"] = body
    url = f"/api/projects/{pid}/tasks?sync=true"
    if query:
        for k, v in query.items():
            url += f"&{k}={v}"
    res = client.post(url, json=payload, headers=headers or {})
    assert res.status_code == 201, res.text
    return res.json()


def _editor_md(client: TestClient, pid: str) -> str:
    state = client.get(f"/api/projects/{pid}/editor-state").json()
    return state.get("parsedMarkdown") or ""


def _project_snapshot(client: TestClient, pid: str) -> dict:
    return client.get(f"/api/projects/{pid}").json()


def _assert_no_leak(text: str | None) -> None:
    raw = text or ""
    for frag in _FORBIDDEN_LEAK_FRAGMENTS:
        assert frag not in raw, f"脱敏失败，泄漏片段 {frag!r}: {raw[:200]!r}"
    assert not re.search(r"[A-Za-z]:\\", raw), f"疑似绝对路径泄漏: {raw[:200]!r}"
    assert "\\\\unc\\" not in raw.lower(), f"疑似 UNC 路径泄漏: {raw[:200]!r}"


def _assert_success_result(result: Any, *, engine: str, file_count: int, chars: int | None = None) -> None:
    assert isinstance(result, dict), f"result 必须为对象: {result!r}"
    assert set(result.keys()) == SUCCESS_RESULT_KEYS, (
        f"成功 result 必须精确三键 engine/fileCount/chars，实际={set(result.keys())}"
    )
    assert result["engine"] == engine
    assert result["fileCount"] == file_count
    assert isinstance(result["chars"], int)
    if chars is not None:
        assert result["chars"] == chars, (
            f"chars 必须精确 {chars}，实际={result['chars']}"
        )
    else:
        assert result["chars"] >= 0
    assert "parsedMarkdown" not in result
    assert "filename" not in result


def _assert_managed_fail_result(
    result: Any, *, code: str = FIXED_UNCONFIGURED_CODE
) -> None:
    assert isinstance(result, dict), f"result 必须为对象: {result!r}"
    assert set(result.keys()) == MANAGED_FAIL_RESULT_KEYS, (
        f"managed 失败 result 必须精确二键 engine/diagnosticCode，实际={set(result.keys())}"
    )
    assert result["engine"] == "managed"
    assert result["diagnosticCode"] == code, (
        f"diagnosticCode 必须精确 {code!r}，实际={result.get('diagnosticCode')!r}"
    )


def _assert_fixed_unconfigured_error(body: dict) -> None:
    err = body.get("error") or ""
    assert err == FIXED_UNCONFIGURED_ERROR, (
        f"未配置 managed 必须固定中文 {FIXED_UNCONFIGURED_ERROR!r}，实际={err!r}"
    )
    _assert_managed_fail_result(body.get("result"), code=FIXED_UNCONFIGURED_CODE)


def _list_source_rows(pid: str) -> list[ProjectFileRow]:
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
            ).all()
        )
    finally:
        db.close()


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


def _task_event_count(pid: str, *, task_id: str | None = None, status: str | None = None) -> int:
    db = SessionLocal()
    try:
        q = select(ProjectTaskEventRow).where(ProjectTaskEventRow.project_id == pid)
        rows = list(db.scalars(q).all())
        if task_id is not None:
            rows = [r for r in rows if getattr(r, "task_id", None) == task_id]
        if status is not None:
            rows = [r for r in rows if getattr(r, "status", None) == status]
        return len(rows)
    finally:
        db.close()


def _success_event_count_for_task(pid: str, task_id: str) -> int:
    """用途：按 task id 严格统计 status=success AND progress=100（R13：progress=None 不算）。"""
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskEventRow).where(
                    ProjectTaskEventRow.project_id == pid,
                    ProjectTaskEventRow.task_id == task_id,
                )
            ).all()
        )
        n = 0
        for r in rows:
            st = getattr(r, "status", None)
            prog = getattr(r, "progress", None)
            if st == "success" and prog is not None and int(prog) == 100:
                n += 1
        return n
    finally:
        db.close()


def _snapshot_five_domains(client: TestClient, pid: str) -> dict[str, Any]:
    proj = _project_snapshot(client, pid)
    return {
        "md": _editor_md(client, pid),
        "rev": _revision_count(pid),
        "status": proj.get("status"),
        "step": proj.get("technicalPlanStep"),
        "success_tasks": _count_success_tasks(pid),
        "success_events": _count_success_events(pid),
    }


def _count_success_tasks(pid: str) -> int:
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskRow).where(
                    ProjectTaskRow.project_id == pid,
                    ProjectTaskRow.status == "success",
                )
            ).all()
        )
        return len(rows)
    finally:
        db.close()


def _count_success_events(pid: str) -> int:
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectTaskEventRow).where(
                    ProjectTaskEventRow.project_id == pid,
                    ProjectTaskEventRow.status == "success",
                )
            ).all()
        )
        return len(rows)
    finally:
        db.close()


def _assert_five_domains_unchanged(
    client: TestClient, pid: str, before: dict[str, Any], *, label: str
) -> None:
    after = _snapshot_five_domains(client, pid)
    for k in ("md", "rev", "status", "step", "success_tasks", "success_events"):
        assert after[k] == before[k], (
            f"{label}: 五域 {k} 被改写 before={before[k]!r} after={after[k]!r}"
        )


def _force_same_created_at(pid: str) -> list[ProjectFileRow]:
    """用途：强制两 source 的 created_at 相同，暴露 id ASC 次序（Q6）。"""
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
            ).all()
        )
        assert len(rows) >= 2
        fixed = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        for r in rows:
            r.created_at = fixed
        db.commit()
        # 重新加载
        rows = list(
            db.scalars(
                select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
            ).all()
        )
        return rows
    finally:
        db.close()


def _set_explicit_created_at(pid: str, filename_to_ts: dict[str, datetime]) -> None:
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
            ).all()
        )
        for r in rows:
            if r.filename in filename_to_ts:
                r.created_at = filename_to_ts[r.filename]
        db.commit()
    finally:
        db.close()


def _lightweight_expected_for_path(path: Path, original_name: str) -> str:
    return parse_service.parse_file_to_markdown(path, original_name)


def _parse_list_fn() -> Callable | None:
    for name in ("list_files_for_parse", "list_source_files_for_parse"):
        fn = getattr(file_service, name, None)
        if callable(fn):
            return fn
    return None


def _tracking_parser(calls: list) -> type:
    class _Track:
        name = "track_parse"

        def parse(self, path: Path, original_name: str) -> str:
            calls.append((str(path), original_name))
            return f"TRACKED::{original_name}::{path.read_text(encoding='utf-8').strip()}"

    return _Track


def _sse_data_frames(client: TestClient, pid: str, tid: str) -> list[dict]:
    """用途：解析单任务 SSE data: JSON 帧；非法 JSON 必须失败（R13）。"""
    with client.stream("GET", f"/api/projects/{pid}/tasks/{tid}/events") as res:
        assert res.status_code == 200, f"SSE 必须 200，实际={res.status_code}"
        ctype = (res.headers.get("content-type") or "").lower()
        assert "text/event-stream" in ctype, f"SSE content-type 错误: {ctype!r}"
        frames: list[dict] = []
        buf = ""
        for chunk in res.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                data_lines = [
                    ln[5:].lstrip()
                    for ln in block.splitlines()
                    if ln.startswith("data:")
                ]
                if not data_lines:
                    continue
                raw = "\n".join(data_lines).strip()
                if raw == "" or raw == "[DONE]":
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise AssertionError(
                        f"SSE data 帧必须是合法 JSON，禁止静默丢弃: {raw[:200]!r}"
                    ) from exc
                assert isinstance(parsed, dict), f"SSE data 根必须是对象: {parsed!r}"
                frames.append(parsed)
        return frames


# ===========================================================================
# A. shared pure core（Q2 行为证明）
# ===========================================================================


def test_a1_shared_core_file_exists_as_single_source():
    """用途：M2 pure core 必须存在于固定相对路径。"""
    assert CORE_PATH.is_file(), (
        "业务红：managed_ocr_runtime_core.py 缺失（M1/backend 单一真源未抽取）"
    )


def test_a2_core_exposes_manifest_ready_and_runner_surface():
    """用途：core 必须提供 manifest/path/ready 与唯一 runner 名 parse_one_file_with_manifest_cli。"""
    core = _load_core_module()
    required = (
        "load_manifest",
        "resolve_under_runtime_root",
        "validate_runtime_ready",
        "check_disk_free",
    )
    for name in required:
        assert callable(getattr(core, name, None)), f"业务红：core 缺少 {name}"
    # S1/S10：唯一 runner 名精确等于 CORE_RUN_ONE；禁止枚举多别名
    runner = getattr(core, CORE_RUN_ONE, None)
    assert callable(runner), f"业务红：core 缺少唯一 runner {CORE_RUN_ONE}"
    for banned in ("run_single_file", "run_mineru_on_file", "parse_source_file"):
        assert not callable(getattr(core, banned, None)), (
            f"业务红：core 不得暴露别名 runner {banned}"
        )
    _assert_exact_core_runner_api(core)


def test_a3_backend_loads_core_via_fixed_repo_relative_path_only(monkeypatch: pytest.MonkeyPatch):
    """
    用途：backend 只能按仓库根固定相对路径加载 core（R1/Q2）。
    行为：env 不得改变 core 来源；service 实际 core.__file__ 精确等于 CORE_PATH。
    禁止测试侧独立 _load_core_module 冒充 service 绑定证据。
    """
    # 注入误导性 env
    monkeypatch.setenv("BIAOSHU_MANAGED_OCR_CORE", str(_make_temp("evil_core_") / "x.py"))
    monkeypatch.setenv("MANAGED_OCR_CORE_PATH", str(_make_temp("evil_core2_") / "y.py"))
    monkeypatch.setenv("CORE_MODULE_PATH", "/tmp/evil_core.py")

    svc = _load_managed_service()
    src = inspect.getsource(svc)
    for banned in (
        "BIAOSHU_MANAGED_OCR_CORE",
        "MANAGED_OCR_CORE_PATH",
        "CORE_MODULE_PATH",
    ):
        assert banned not in src, f"业务红：禁止 env 指定 core ({banned})"
    # 精确 service core accessor/绑定面；__file__ 必须是 service 实际加载结果
    core_mod = _require_service_core_module(svc)
    loaded = Path(getattr(core_mod, "__file__", "") or "").resolve()
    assert loaded == CORE_PATH.resolve(), (
        f"业务红：backend 暴露/加载的 core.__file__ 必须等于固定 CORE_PATH，"
        f"actual={loaded}"
    )


def test_a4_parse_engines_registry_never_hosts_managed_or_subprocess():
    """用途：managed 不得进入 parse_engines；注册表禁止 import/调用 subprocess。"""
    names = parse_engines.list_registered_engines()
    assert names == ["lightweight"]
    assert "managed" not in names
    src = Path(parse_engines.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad_imports: list[str] = []
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "subprocess":
                    bad_imports.append(alias.name)
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] == "subprocess":
                bad_imports.append(mod)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"Popen", "run", "call"}:
                if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                    bad_calls.append(func.attr)
            if isinstance(func, ast.Name) and func.id == "Popen":
                bad_calls.append("Popen")
    assert bad_imports == [], f"parse_engines 禁止 import subprocess: {bad_imports}"
    assert bad_calls == [], f"parse_engines 禁止调用 subprocess: {bad_calls}"


# ===========================================================================
# B. path-only locator / 客户端零路径（Q3）
# ===========================================================================


def test_b1_settings_exposes_path_only_manifest_locator():
    """用途：仅服务端 path-only manifest 配置项。"""
    settings = get_settings()
    assert hasattr(settings, "managed_ocr_manifest_path"), (
        "业务红：Settings 缺少 managed_ocr_manifest_path"
    )
    field_names = set(type(settings).model_fields.keys())
    for banned in (
        "managed_ocr_executable",
        "managed_ocr_argv",
        "managed_ocr_model_dir",
        "managed_ocr_cli",
    ):
        assert banned not in field_names, f"禁止配置项 {banned}"


def test_b2_unconfigured_managed_is_not_ready_no_light_fallback(client: TestClient):
    """用途：未配置 manifest 时固定 runtime_manifest_invalid + 固定中文，零 light 降级。"""
    pid = _create_project(client, "M2-not-ready")
    _upload_md(client, pid, "a.md", "# A\n\nbody-a\n")
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="managed")
    assert body["status"] == "failed", (
        f"业务红：未配置 managed 不得成功/降级 light，实际 status={body['status']}"
    )
    _assert_fixed_unconfigured_error(body)
    _assert_no_leak(json.dumps(body.get("result") or {}, ensure_ascii=False))
    # 零成功写：正文/revision/success task/event 保持
    assert _editor_md(client, pid) == before["md"]
    assert _revision_count(pid) == before["rev"]
    assert _count_success_tasks(pid) == before["success_tasks"]


def test_b3_client_payload_query_header_cannot_inject_manifest_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：payload/query/header 三路哨兵；只消费服务器 locator（R2/Q3）。
    行为：有效五键 fake manifest；无条件断言服务器 loader 调用与 inner runner 调用；
      客户端三路零读取/零执行。不得“若有调用才断言”。
    """
    pid = _create_project(client, "M2-client-path")
    _upload_md(client, pid, "a.md", "# A\n\nx\n")

    _runtime, server_manifest = _prepare_fake_runtime("srv_manifest_")
    evil_payload = str((_make_temp("evil_p_") / "p.json").resolve())
    evil_query = str((_make_temp("evil_q_") / "q.json").resolve())
    evil_header = str((_make_temp("evil_h_") / "h.json").resolve())
    Path(evil_payload).write_text('{"evil":"payload"}', encoding="utf-8")
    Path(evil_query).write_text('{"evil":"query"}', encoding="utf-8")
    Path(evil_header).write_text('{"evil":"header"}', encoding="utf-8")

    _point_settings_manifest(monkeypatch, server_manifest)

    load_calls: list[Path] = []
    run_calls: list[Any] = []
    read_paths: list[str] = []
    popen_calls: list[Any] = []

    svc = _load_managed_service()
    _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)
    real_load = _require_attr(core, CORE_LOAD_MANIFEST)

    def _wrap_load(path, *a, **k):
        # T2：记录 resolve 后路径，精确一次等于 server_manifest.resolve()
        load_calls.append(Path(path).resolve())
        return real_load(path, *a, **k)

    # S1 B3：patch inner runner 为纯 fake，绝不回落到真实 runner / 真实 Popen
    def _fake_run_one(ready, input_path, *, task_deadline, cancel_check):
        run_calls.append(
            {
                "ready": ready,
                "input_path": str(input_path),
                "task_deadline": task_deadline,
                "cancel_check": cancel_check,
            }
        )
        return "# b3-fake-md\n"

    monkeypatch.setattr(core, CORE_LOAD_MANIFEST, _wrap_load)
    monkeypatch.setattr(core, CORE_RUN_ONE, _fake_run_one)

    import subprocess as _sp

    real_popen = _sp.Popen

    def _guard_popen(*a, **k):
        popen_calls.append((a, k))
        raise AssertionError("B3 禁止真实 Popen")

    monkeypatch.setattr(_sp, "Popen", _guard_popen)
    if hasattr(core, "subprocess"):
        monkeypatch.setattr(core.subprocess, "Popen", _guard_popen)

    real_open = open

    def _spy_open(file, *a, **k):
        s = str(file)
        if any(x in s for x in (evil_payload, evil_query, evil_header)):
            read_paths.append(s)
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", _spy_open)

    body = _parse(
        client,
        pid,
        engine="managed",
        extra_payload={
            "manifestPath": evil_payload,
            "manifest": evil_payload,
            "cliPath": r"C:\evil\mineru.exe",
            "executable": r"C:\evil\mineru.exe",
            "argv": ["--attack"],
            "modelDir": r"C:\evil\models",
        },
        query={"manifestPath": evil_query, "cliPath": r"C:\evil\from-query.exe"},
        headers={
            "X-Managed-Manifest": evil_header,
            "X-Managed-Cli": r"C:\evil\from-header.exe",
        },
    )
    # T2：loader 精确一次 == [server_manifest.resolve()]，非“非空”
    assert load_calls == [server_manifest.resolve()], (
        f"业务红：B3 loader 必须精确一次服务器路径，"
        f"expected={[server_manifest.resolve()]!r} actual={load_calls!r}"
    )
    assert read_paths == [], f"客户端诱饵被读取: {read_paths}"
    # S1/S11 B3：run_calls 无条件精确 1；绝不 Popen
    assert len(run_calls) == 1, (
        f"业务红：B3 inner runner 必须精确调用 1 次，actual={len(run_calls)} {run_calls!r}"
    )
    assert popen_calls == [], f"业务红：B3 禁止 Popen，actual={popen_calls}"
    # payload/query/header 不得影响 runner 参数语义：客户端路径不得出现在 run 参数
    blob_args = repr(run_calls[0])
    assert evil_payload not in blob_args
    assert evil_query not in blob_args
    assert evil_header not in blob_args
    assert "C:\\evil" not in blob_args
    blob = json.dumps(body, ensure_ascii=False)
    assert evil_payload not in blob
    assert evil_query not in blob
    assert evil_header not in blob
    assert "--attack" not in blob


# ===========================================================================
# C. ready 时序 / 无正向缓存（Q4）
# ===========================================================================


def test_c1_per_task_ready_once_and_per_file_recheck_with_fake_core(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：精确 public service + inner core seam；有效 fake manifest；
      无条件断言 ready=1/recheck=2/runner=2（R3）。
    """
    svc = _load_managed_service()
    _require_service_public_run(svc)
    core = _require_service_core_module(svc)
    ready_calls: list[str] = []
    recheck_calls: list[str] = []
    runner_calls: list[str] = []

    def fake_ready(*a, **k):
        ready_calls.append("ready")
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        recheck_calls.append("recheck")
        return True

    def fake_runner(*a, **k):
        runner_calls.append("run")
        return "# fake md\n"

    # 只注入 pure core 精确 seam，禁止 top-level 替换 public service
    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, fake_runner)

    pid = _create_project(client, "M2-ready-once")
    _upload_md(client, pid, "f1.md", "# F1\n\nA\n")
    _upload_md(client, pid, "f2.md", "# F2\n\nB\n")
    _runtime, manifest = _prepare_fake_runtime("ready_m_")
    _point_settings_manifest(monkeypatch, manifest)

    body = _parse(client, pid, engine="managed")
    assert body["status"] == "success", (
        f"业务红：有效 fake 两文件 ready 链必须 success，实际={body['status']} err={body.get('error')}"
    )
    # 无条件精确计数
    assert ready_calls.count("ready") == 1, (
        f"业务红：每任务 ready 必须精确 1，实际={ready_calls}"
    )
    assert len(recheck_calls) == 2, (
        f"业务红：两文件 recheck 必须精确 2，实际={recheck_calls}"
    )
    assert len(runner_calls) == 2, (
        f"业务红：两文件 runner 必须精确 2，实际={runner_calls}"
    )


def test_c2_mid_task_identity_switch_zero_partial_and_no_ready_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：第二文件 recheck 失败；runner 仅首文件；五域零成功写；
      无条件 ready=1/recheck=2/runner=1，第二任务 ready 再+1（R3）。
    """
    svc = _load_managed_service()
    _require_service_public_run(svc)
    core = _require_service_core_module(svc)

    state = {"n": 0, "ready": 0}
    runner_files: list[str] = []

    def fake_ready(*a, **k):
        state["ready"] += 1
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        state["n"] += 1
        if state["n"] >= 2:
            return False
        return True

    def fake_runner(path=None, *a, **k):
        name = Path(str(path)).name if path is not None else "x"
        runner_files.append(name)
        return f"PART_{len(runner_files)}"

    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, fake_runner)

    pid = _create_project(client, "M2-mid-switch")
    _upload_md(client, pid, "f1.md", "# F1\n\nKEEP\n")
    _upload_md(client, pid, "f2.md", "# F2\n\nDROP\n")
    _runtime, manifest = _prepare_fake_runtime("mid_m_")
    _point_settings_manifest(monkeypatch, manifest)
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="managed")
    assert body["status"] == "failed", (
        f"业务红：中途 recheck 失败必须整任务 failed，实际={body['status']}"
    )
    assert state["ready"] == 1, f"业务红：首任务 ready 必须=1，actual={state['ready']}"
    assert state["n"] == 2, f"业务红：recheck 必须=2，actual={state['n']}"
    assert len(runner_files) == 1, f"业务红：runner 必须仅首文件 1 次，actual={runner_files}"
    _assert_five_domains_unchanged(client, pid, before, label="c2-mid")
    # S9：cli_missing 必须使用 M1 固定中文，禁止新造
    _assert_managed_fail_result(body.get("result"), code="cli_missing")
    assert body.get("error") == _m1_error_for("cli_missing")
    assert body.get("error") == "未找到解析器命令或安全类型不合格"

    ready_first = state["ready"]
    state["n"] = 0
    runner_files.clear()
    body2 = _parse(client, pid, engine="managed")
    assert body2["status"] == "failed"
    assert state["ready"] == ready_first + 1, (
        f"业务红：第二任务 ready 必须再+1（无正向缓存），"
        f"first={ready_first} total={state['ready']}"
    )
    assert state["n"] == 2
    assert len(runner_files) == 1
    _assert_five_domains_unchanged(client, pid, before, label="c2-task2")


# ===========================================================================
# D. ASC 全 source 聚合与 GET desc 分离（Q5/Q6）
# ===========================================================================


def test_d1_public_list_files_remains_desc_with_explicit_times(client: TestClient):
    """用途：对外 GET /files 保持新→旧；显式时间避免时钟碰撞（Q6）。"""
    pid = _create_project(client, "M2-desc")
    _upload_md(client, pid, "older.md", "# OLD\n")
    _upload_md(client, pid, "newer.md", "# NEW\n")
    _set_explicit_created_at(
        pid,
        {
            "older.md": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "newer.md": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        },
    )
    listed = client.get(f"/api/projects/{pid}/files").json()
    assert len(listed) == 2
    names = [x["filename"] for x in listed]
    assert names[0] == "newer.md"
    assert names[1] == "older.md"


def test_d2_parse_query_order_by_created_at_asc_id_asc_behavior():
    """
    用途：parse 专用查询行为证明 ORDER BY created_at ASC, id ASC（Q6）。
    禁止仅搜源码 asc/id token。
    """
    fn = _parse_list_fn()
    assert callable(fn), (
        "业务红：file_service 缺少 parse 专用 ASC 列表 "
        "(list_files_for_parse / list_source_files_for_parse)"
    )
    # 构造同 created_at 两行，按 id ASC 期望
    db = SessionLocal()
    try:
        from app.services.project_service import ensure_default_workspace, create_project

        settings = get_settings()
        ensure_default_workspace(db, settings)
        proj = create_project(db, "ws_local", name="ASC-order-probe")
        pid = proj.id
        fixed = datetime(2026, 7, 22, 8, 0, 0, tzinfo=timezone.utc)
        rows = []
        for i, name in enumerate(("b.md", "a.md")):
            r = ProjectFileRow(
                id=f"file_asc_{i}_{os.getpid()}",
                project_id=pid,
                filename=name,
                stored_name=f"stored_{i}.md",
                content_type="text/markdown",
                size_bytes=1,
                role="source",
                created_at=fixed,
            )
            db.add(r)
            rows.append(r)
        db.commit()
        # 期望按 id ASC
        expected_ids = sorted([r.id for r in rows])
        got = fn(db, "ws_local", pid)
        got_ids = [g.id for g in got]
        assert got_ids == expected_ids, (
            f"业务红：parse 列表必须 created_at 相同下 id ASC，"
            f"expected={expected_ids} actual={got_ids}"
        )
    finally:
        db.close()


def test_d3_lightweight_multi_file_exact_aggregate_equality(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：lightweight 多文件全文精确等式（Q5）；同 created_at 下 id ASC（Q6）。
    """
    pid = _create_project(client, "M2-multi-light")
    raw_a = "# First\n\nV1M_M2_BODY_A_UNIQUE\n"
    raw_b = "# Second\n\nV1M_M2_BODY_B_UNIQUE\n"
    _upload_md(client, pid, "first.md", raw_a)
    _upload_md(client, pid, "second.md", raw_b)
    rows = _force_same_created_at(pid)
    # 期望 id ASC
    ordered = sorted(rows, key=lambda r: r.id)
    settings = get_settings()
    parts: list[str] = []
    for r in ordered:
        path = file_service.resolve_path(settings, pid, r.stored_name)
        parts.append(_lightweight_expected_for_path(path, r.filename))
    expected_md = SOURCE_SEPARATOR.join(parts)

    body = _parse(client, pid)
    assert body["status"] == "success", body
    md = _editor_md(client, pid)
    assert md == expected_md, (
        f"业务红：多文件全文必须精确等于已知 parser 输出+分隔符\n"
        f"expected_len={len(expected_md)} actual_len={len(md)}\n"
        f"expected[:200]={expected_md[:200]!r}\nactual[:200]={md[:200]!r}"
    )
    _assert_success_result(
        body["result"], engine="lightweight", file_count=2, chars=len(expected_md)
    )
    for r in rows:
        # 契约：聚合正文不得写入 stored path / 文件 ID（filename 是否出现取决于 parser 头，由精确等式约束）
        assert r.stored_name not in md
        assert r.id not in md


def test_d4_single_file_body_exact_equality(client: TestClient):
    """用途：单文件正文完全相等，不只 anchor/无 separator（Q5）。"""
    pid = _create_project(client, "M2-single")
    raw = "# Only\n\nV1M_M2_SINGLE_ONLY\n"
    up = _upload_md(client, pid, "only.md", raw)
    settings = get_settings()
    rows = _list_source_rows(pid)
    assert len(rows) == 1
    path = file_service.resolve_path(settings, pid, rows[0].stored_name)
    expected = _lightweight_expected_for_path(path, "only.md")
    body = _parse(client, pid)
    assert body["status"] == "success"
    md = _editor_md(client, pid)
    assert md == expected, (
        f"业务红：单文件正文必须完全相等\nexpected={expected!r}\nactual={md!r}"
    )
    assert "BIAOSHU_SOURCE_SEPARATOR" not in md
    _assert_success_result(
        body["result"], engine="lightweight", file_count=1, chars=len(expected)
    )


def test_d5_managed_two_file_success_exact_chain(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：S3 D5 只 patch core runner；检查精确 ready/input_path/task_deadline/cancel_check；
      真实 public service 聚合；无条件两次 runner 的 id ASC 次序、精确全文/三键/chars。
    """
    svc = _load_managed_service()
    _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)

    pid = _create_project(client, "M2-managed-ok")
    _upload_md(client, pid, "a.md", "# A\n\nMA\n")
    _upload_md(client, pid, "b.md", "# B\n\nMB\n")
    rows = _force_same_created_at(pid)
    ordered = sorted(rows, key=lambda r: (r.created_at, r.id))
    bodies = {
        ordered[0].filename: "MANAGED_BODY_FIRST_UNIQUE",
        ordered[1].filename: "MANAGED_BODY_SECOND_UNIQUE",
    }
    expected_md = (
        bodies[ordered[0].filename]
        + SOURCE_SEPARATOR
        + bodies[ordered[1].filename]
    )
    runner_order: list[str] = []
    runner_calls: list[dict[str, Any]] = []

    def fake_ready(*a, **k):
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        return True

    def fake_one(ready, input_path, *, task_deadline, cancel_check):
        # S3：精确四参；按 input_path 反查 filename（禁止猜 original_name 签名）
        assert ready is not None, "业务红：D5 runner ready 不得为 None"
        assert input_path is not None, "业务红：D5 runner input_path 不得为 None"
        assert task_deadline is not None, "业务红：D5 runner task_deadline 不得为 None"
        assert callable(cancel_check), "业务红：D5 runner cancel_check 必须可调用"
        p = Path(str(input_path))
        original_name = None
        for r in ordered:
            if r.stored_name in str(p) or r.filename == p.name:
                original_name = r.filename
                break
        assert original_name in bodies, f"未知 runner 文件: {original_name} path={p}"
        runner_order.append(original_name)
        runner_calls.append(
            {
                "ready": ready,
                "input_path": str(p),
                "task_deadline": task_deadline,
                "cancel_check": cancel_check,
            }
        )
        return bodies[original_name]

    # 只 patch core；不 patch public service
    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, fake_one)
    assert callable(getattr(svc, SERVICE_PUBLIC_RUN))

    _runtime, manifest = _prepare_fake_runtime("m_ok_")
    _point_settings_manifest(monkeypatch, manifest)

    body = _parse(client, pid, engine="managed")
    assert body["status"] == "success", (
        f"业务红：managed 成功链未接通，status={body['status']} err={body.get('error')}"
    )
    assert len(runner_calls) == 2, f"业务红：runner 必须精确 2 次，actual={runner_calls}"
    for c in runner_calls:
        assert c["ready"] is not None
        assert c["input_path"]
        assert c["task_deadline"] is not None
        assert callable(c["cancel_check"])
    assert runner_order == [ordered[0].filename, ordered[1].filename], (
        f"业务红：runner 必须按 id ASC 次序，expected="
        f"{[ordered[0].filename, ordered[1].filename]} actual={runner_order}"
    )
    md = _editor_md(client, pid)
    assert md == expected_md
    _assert_success_result(
        body["result"], engine="managed", file_count=2, chars=len(expected_md)
    )
    assert md.index("MANAGED_BODY_FIRST_UNIQUE") < md.index("MANAGED_BODY_SECOND_UNIQUE")


# ===========================================================================
# E. 10 文件 / 200MiB / no-follow（Q7/Q8）
# ===========================================================================


def test_e1_more_than_ten_source_files_fails_zero_parser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """用途：>10 source 失败；parser 调用计数=0；五域零成功写（Q7）。"""
    calls: list = []
    Track = _tracking_parser(calls)
    parse_engines.register_engine(Track())
    pid = _create_project(client, "M2-11files")
    for i in range(11):
        _upload_md(client, pid, f"f{i:02d}.md", f"# F{i}\n\nbody-{i}\n")
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="track_parse")
    _assert_failed_fixed(body, error=FIXED_ERR_TOO_MANY_SOURCES)
    assert len(calls) == 0, f"边界预检类 parser 必须 0 次，实际={len(calls)}"
    _assert_five_domains_unchanged(client, pid, before, label="e1")
    _assert_post_get_error_consistent(client, pid, body)


def test_e2_total_size_over_200mib_with_controlled_stat_seam(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：两份 120MiB 声明合计触发 200MiB 门；可控 stat seam（Q7）。
    证明生产读取两文件 stat；parser=0；五域零成功写。
    """
    calls: list = []
    Track = _tracking_parser(calls)
    parse_engines.register_engine(Track())

    pid = _create_project(client, "M2-200mib")
    _upload_md(client, pid, "big1.md", "# B1\n\nx\n")
    _upload_md(client, pid, "big2.md", "# B2\n\ny\n")
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
            ).all()
        )
        assert len(rows) == 2
        for row in rows:
            row.size_bytes = 120 * 1024 * 1024
        db.commit()
        stored_names = [r.stored_name for r in rows]
    finally:
        db.close()

    settings = get_settings()
    targets = {
        str(file_service.resolve_path(settings, pid, name).resolve()): 120 * 1024 * 1024
        for name in stored_names
    }
    stat_hits: list[str] = []
    orig_stat = Path.stat

    def _stat(self, *a, **k):
        follow_symlinks = True
        if a:
            # Path.stat(self, *, follow_symlinks=True)
            pass
        follow_symlinks = k.get("follow_symlinks", True)
        try:
            key = str(Path(self).resolve(strict=False))
        except OSError:
            key = str(self)
        st = orig_stat(self, *a, **k)
        for t, sz in targets.items():
            try:
                if Path(t).resolve() == Path(key).resolve():
                    stat_hits.append(t)
                    return os.stat_result(
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
            except OSError:
                continue
        return st

    monkeypatch.setattr(Path, "stat", _stat)
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="track_parse")
    _assert_failed_fixed(body, error=FIXED_ERR_TOTAL_SIZE)
    assert len(calls) == 0, f"200MiB 预检 parser 必须 0，实际={calls}"
    unique_hits = set(stat_hits)
    assert len(unique_hits) == 2, (
        f"业务红：须 no-follow/stat 读取两份 120MiB 目标，hits={stat_hits}"
    )
    _assert_five_domains_unchanged(client, pid, before, label="e2")
    _assert_post_get_error_consistent(client, pid, body)


def test_e3_size_mismatch_between_db_and_disk_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """用途：DB size 与磁盘不一致 → parser=0；五域零成功写（Q7）。"""
    calls: list = []
    Track = _tracking_parser(calls)
    parse_engines.register_engine(Track())
    pid = _create_project(client, "M2-size-mismatch")
    _upload_md(client, pid, "m.md", "# M\n\nhello-size\n")
    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid)
        ).first()
        assert row is not None
        row.size_bytes = (row.size_bytes or 0) + 999
        db.commit()
    finally:
        db.close()
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="track_parse")
    _assert_failed_fixed(body, error=FIXED_ERR_SIZE_MISMATCH)
    assert len(calls) == 0
    assert "hello-size" not in _editor_md(client, pid)
    _assert_five_domains_unchanged(client, pid, before, label="e3")
    _assert_post_get_error_consistent(client, pid, body)


def test_e4_nofollow_leaf_parent_and_traversal_gates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：可控 lstat/reparse 行为门，分别覆盖 leaf、父目录、stored_name traversal（Q8）。
    每项：固定失败、parser=0、五域零成功写；禁止条件成功/关键词 return。
    """
    calls: list = []
    Track = _tracking_parser(calls)
    parse_engines.register_engine(Track())

    # --- leaf symlink/reparse ---
    pid = _create_project(client, "M2-nofollow-leaf")
    _upload_md(client, pid, "real.md", "# REAL\n\nkeep\n")
    rows = _list_source_rows(pid)
    settings = get_settings()
    real_path = file_service.resolve_path(settings, pid, rows[0].stored_name)
    shadow = _make_temp("symlink_tgt_") / "shadow.md"
    shadow.write_text("# SHADOW\n\nleak\n", encoding="utf-8")
    real_path.unlink()
    linked = False
    try:
        real_path.symlink_to(shadow)
        linked = True
    except OSError:
        # 无权限建链：用 lstat seam 模拟 reparse/symlink 叶
        class _FakeStat:
            st_mode = 0o120777  # symlink mode-ish
            st_size = 10
            st_ino = 1
            st_dev = 1
            st_nlink = 1
            st_uid = 0
            st_gid = 0
            st_atime = 0
            st_mtime = 0
            st_ctime = 0
            st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT on Windows

        def _lstat(self, *a, **k):
            if Path(self).resolve() == real_path.resolve() or str(self).endswith(
                rows[0].stored_name
            ):
                return _FakeStat()
            return os.lstat(self)

        # 恢复文件供 resolve 存在
        real_path.write_text("# REAL\n\nkeep\n", encoding="utf-8")
        monkeypatch.setattr(Path, "lstat", _lstat, raising=False)
        if hasattr(os, "lstat"):
            real_os_lstat = os.lstat

            def _os_lstat(path, *a, **k):
                if str(real_path) in str(path) or rows[0].stored_name in str(path):
                    return _FakeStat()
                return real_os_lstat(path, *a, **k)

            monkeypatch.setattr(os, "lstat", _os_lstat)

    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="track_parse")
    _assert_failed_fixed(body, error=FIXED_ERR_LEAF_REPARSE)
    assert len(calls) == 0
    assert "SHADOW" not in _editor_md(client, pid)
    assert "leak" not in _editor_md(client, pid)
    _assert_five_domains_unchanged(client, pid, before, label="e4-leaf")
    _assert_post_get_error_consistent(client, pid, body)

    # --- parent reparse seam ---
    calls.clear()
    pid2 = _create_project(client, "M2-nofollow-parent")
    _upload_md(client, pid2, "p.md", "# P\n\nparent-body\n")
    rows2 = _list_source_rows(pid2)
    leaf = file_service.resolve_path(settings, pid2, rows2[0].stored_name)
    parent = leaf.parent

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

    real_path_lstat = Path.lstat

    def _parent_lstat(self, *a, **k):
        p = Path(self)
        try:
            if p.resolve() == parent.resolve():
                return _ParentReparseStat()
        except OSError:
            pass
        return real_path_lstat(self, *a, **k)

    monkeypatch.setattr(Path, "lstat", _parent_lstat)
    before2 = _snapshot_five_domains(client, pid2)
    body2 = _parse(client, pid2, engine="track_parse")
    _assert_failed_fixed(body2, error=FIXED_ERR_PARENT_REPARSE)
    assert len(calls) == 0
    _assert_five_domains_unchanged(client, pid2, before2, label="e4-parent")
    _assert_post_get_error_consistent(client, pid2, body2)

    # --- stored_name traversal ---
    calls.clear()
    pid3 = _create_project(client, "M2-traverse")
    _upload_md(client, pid3, "t.md", "# T\n\ntrav\n")
    db = SessionLocal()
    try:
        row = db.scalars(
            select(ProjectFileRow).where(ProjectFileRow.project_id == pid3)
        ).first()
        assert row is not None
        row.stored_name = "../outside.md"
        db.commit()
    finally:
        db.close()
    before3 = _snapshot_five_domains(client, pid3)
    body3 = _parse(client, pid3, engine="track_parse")
    _assert_failed_fixed(body3, error=FIXED_ERR_TRAVERSAL)
    assert len(calls) == 0
    _assert_five_domains_unchanged(client, pid3, before3, label="e4-trav")
    _assert_post_get_error_consistent(client, pid3, body3)


# ===========================================================================
# F. 聚合双上限（Q7：输出超限 parser 精确 1）
# ===========================================================================


def test_f1_aggregate_codepoints_cap_zero_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """用途：码点超限失败；parser 精确 1；五域零成功写。"""
    calls: list = []

    class _Huge:
        name = "fake_huge"

        def parse(self, path: Path, original_name: str) -> str:
            calls.append(original_name)
            return "字" * (MAX_MARKDOWN_CODEPOINTS + 1)

    parse_engines.register_engine(_Huge())
    pid = _create_project(client, "M2-cp-cap")
    _upload_md(client, pid, "a.md", "# a\n")
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="fake_huge")
    _assert_failed_fixed(body, error=FIXED_ERR_CODEPOINTS)
    assert len(calls) == 1, f"输出超限 parser 必须精确 1，实际={len(calls)}"
    assert _editor_md(client, pid) == ""
    _assert_five_domains_unchanged(client, pid, before, label="f1")
    _assert_no_leak(body.get("error"))
    _assert_post_get_error_consistent(client, pid, body)


def test_f2_aggregate_utf8_bytes_cap_zero_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """用途：UTF-8 超限失败；parser 精确 1；五域零成功写。"""
    calls: list = []

    class _HugeBytes:
        name = "fake_huge_bytes"

        def parse(self, path: Path, original_name: str) -> str:
            calls.append(original_name)
            return "a" * (MAX_MARKDOWN_UTF8_BYTES + 1)

    parse_engines.register_engine(_HugeBytes())
    pid = _create_project(client, "M2-utf8-cap")
    _upload_md(client, pid, "a.md", "# a\n")
    before = _snapshot_five_domains(client, pid)
    body = _parse(client, pid, engine="fake_huge_bytes")
    _assert_failed_fixed(body, error=FIXED_ERR_UTF8_BYTES)
    assert len(calls) == 1
    assert _editor_md(client, pid) == ""
    _assert_five_domains_unchanged(client, pid, before, label="f2")
    _assert_post_get_error_consistent(client, pid, body)


# ===========================================================================
# G. result 键与全域脱敏（Q9）
# ===========================================================================


def test_g1_lightweight_success_result_exact_three_keys(client: TestClient):
    pid = _create_project(client, "M2-result3")
    raw = "# T\n\nbody-g1\n"
    _upload_md(client, pid, "t.md", raw)
    body = _parse(client, pid)
    assert body["status"] == "success"
    md = _editor_md(client, pid)
    _assert_success_result(
        body["result"], engine="lightweight", file_count=1, chars=len(md)
    )
    assert "body-g1" in md
    assert "body-g1" not in json.dumps(body["result"], ensure_ascii=False)


def test_g2_managed_failure_result_exact_two_keys_and_fixed_error(client: TestClient):
    """用途：未配置 managed → 精确 code + 固定中文（Q9）。"""
    pid = _create_project(client, "M2-fail2")
    _upload_md(client, pid, "t.md", "# T\n\nSECRET_MARKDOWN_SHOULD_NOT_LEAK\n")
    body = _parse(client, pid, engine="managed")
    assert body["status"] == "failed"
    _assert_fixed_unconfigured_error(body)
    blob = json.dumps(body, ensure_ascii=False)
    assert "SECRET_MARKDOWN_SHOULD_NOT_LEAK" not in blob
    _assert_no_leak(body.get("error"))
    _assert_no_leak(blob)


def test_g3_task_get_and_sse_never_carry_markdown_or_paths(client: TestClient):
    """用途：GET 200；SSE 200+event-stream；解析 data 帧后脱敏（Q9）。"""
    pid = _create_project(client, "M2-get-sse")
    _upload_md(client, pid, "t.md", "# T\n\nVISIBLE_ONLY_IN_EDITOR\n")
    body = _parse(client, pid)
    assert body["status"] == "success"
    tid = body["id"]
    got_res = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got_res.status_code == 200, f"GET task 必须 200，实际={got_res.status_code}"
    got = got_res.json()
    blob = json.dumps(got, ensure_ascii=False)
    assert "VISIBLE_ONLY_IN_EDITOR" not in blob
    assert "parsedMarkdown" not in blob
    frames = _sse_data_frames(client, pid, tid)
    assert frames, "SSE 必须至少一帧 data JSON"
    for fr in frames:
        fr_blob = json.dumps(fr, ensure_ascii=False)
        assert "VISIBLE_ONLY_IN_EDITOR" not in fr_blob
        _assert_no_leak(fr_blob)


def test_g4_managed_exception_sentinels_redacted_on_post_get_sse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：有效 fake runtime；inner runner `_boom` 精确调用1；
      冻结 internal_error + 固定中文；POST/GET/SSE 精确一致；frames 非空（R6）。
    """
    svc = _load_managed_service()
    _require_service_public_run(svc)
    core = _require_service_core_module(svc)

    boom_calls = {"n": 0}

    class _LeakError(RuntimeError):
        pass

    def fake_ready(*a, **k):
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        return True

    def _boom(*a, **k):
        boom_calls["n"] += 1
        raise _LeakError(
            "C:\\Users\\Administrator\\secret\\mineru.exe "
            "\\\\unc\\share\\model "
            "/var/lib/mineru/argv "
            "filename=secret.pdf "
            "BODY_SENTINEL_MARKDOWN "
            "stdout=OUT_SENTINEL stderr=ERR_SENTINEL "
            "argv=['--ocr']"
        )

    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, _boom)

    _runtime, manifest = _prepare_fake_runtime("leak_m_")
    _point_settings_manifest(monkeypatch, manifest)

    pid = _create_project(client, "M2-leak")
    _upload_md(client, pid, "t.md", "# T\n\nBODY_SENTINEL_MARKDOWN\n")
    body = _parse(client, pid, engine="managed")
    assert body["status"] == "failed"
    assert boom_calls["n"] == 1, f"inner runner _boom 必须精确调用1，actual={boom_calls['n']}"
    _assert_managed_fail_result(body.get("result"), code=FIXED_MANAGED_EXCEPTION_CODE)
    assert body.get("error") == FIXED_MANAGED_EXCEPTION_ERROR
    err = body.get("error") or ""
    assert "RuntimeError" not in err
    assert "_LeakError" not in err
    assert "BODY_SENTINEL_MARKDOWN" not in err
    _assert_no_leak(err)
    blob = json.dumps(body, ensure_ascii=False)
    for sentinel in (
        "BODY_SENTINEL_MARKDOWN",
        "OUT_SENTINEL",
        "ERR_SENTINEL",
        "C:\\\\Users",
        "/var/lib/mineru",
        "secret.pdf",
        "--ocr",
    ):
        assert sentinel not in blob

    tid = body["id"]
    got = client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert got.status_code == 200
    got_data = got.json()
    assert got_data.get("error") == FIXED_MANAGED_EXCEPTION_ERROR
    assert (got_data.get("result") or {}).get("diagnosticCode") == FIXED_MANAGED_EXCEPTION_CODE
    got_blob = json.dumps(got_data, ensure_ascii=False)
    assert "BODY_SENTINEL_MARKDOWN" not in got_blob
    _assert_no_leak(got_blob)
    frames = _sse_data_frames(client, pid, tid)
    assert frames, "SSE frames 必须非空"
    for fr in frames:
        fb = json.dumps(fr, ensure_ascii=False)
        assert "BODY_SENTINEL_MARKDOWN" not in fb
        _assert_no_leak(fb)


# ===========================================================================
# H. 单事务 finalizer（Q10）
# ===========================================================================


def test_h1_commit_false_hooks_defaults_and_parse_passes_false(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    用途：S4 H1 — 默认 commit=True 行为证据；parse 成功链 upsert/update/_set_task
      各自 commit=False 且各自 flush 增量；仅在五域 staged success 的 finalizer commit arm 计数=1。
    允许事务外 lifecycle/progress commits。禁止源码扫描替代行为证据。
    """
    from app.services import editor_state_service, project_service, task_service
    from app.core import database as dbmod
    from app.core.database import SessionLocal

    for mod, name in (
        (editor_state_service, "upsert_editor_state"),
        (project_service, "update_project"),
        (task_service, "_set_task"),
    ):
        fn = getattr(mod, name)
        sig = inspect.signature(fn)
        assert "commit" in sig.parameters, (
            f"业务红：{mod.__name__}.{name} 缺少 commit 参数"
        )
        assert sig.parameters["commit"].default is True, (
            f"业务红：{name}.commit 默认必须为 True"
        )

    # T3：三函数各自隔离项目/session 做一次合法默认调用；commit spy 精确增量 1
    # 禁止仅用签名布尔冒充行为；不传 commit= 关键字，走默认 True。
    default_commit_deltas: dict[str, int] = {}
    db = SessionLocal()
    try:
        commits_default: list[str] = []
        real_commit0 = dbmod.Session.commit

        def _cap_commit(self, *a, **k):
            commits_default.append("commit")
            return real_commit0(self, *a, **k)

        monkeypatch.setattr(dbmod.Session, "commit", _cap_commit)

        # 1) update_project 默认
        pid_proj = _create_project(client, "M2-h1-def-proj")
        before = len(commits_default)
        project_service.update_project(
            db, "ws_local", pid_proj, name="M2-h1-def-proj-renamed"
        )
        default_commit_deltas["update_project"] = len(commits_default) - before

        # 2) upsert_editor_state 默认（最小合法字段）
        pid_up = _create_project(client, "M2-h1-def-upsert")
        before = len(commits_default)
        editor_state_service.upsert_editor_state(
            db,
            "ws_local",
            pid_up,
            mode="technical",
        )
        default_commit_deltas["upsert_editor_state"] = len(commits_default) - before

        # 3) _set_task 默认（真实 task 行 + 最小 message 心跳）
        pid_task = _create_project(client, "M2-h1-def-task")
        task_row = task_service.create_task_record(
            db, "ws_local", pid_task, task_type="parse"
        )
        before = len(commits_default)
        task_service._set_task(db, task_row, message="h1-default-commit-probe")
        default_commit_deltas["set_task"] = len(commits_default) - before

        monkeypatch.setattr(dbmod.Session, "commit", real_commit0)
    finally:
        db.close()
    for key, delta in default_commit_deltas.items():
        assert delta == 1, (
            f"业务红：{key} 默认调用 commit spy 增量必须精确为 1，actual={delta} "
            f"all={default_commit_deltas}"
        )

    seen: dict[str, list] = {
        "upsert": [],
        "project": [],
        "set_task": [],
        "upsert_flush_delta": [],
        "project_flush_delta": [],
        "set_task_flush_delta": [],
        "flush": [],
        "commit": [],
        "finalizer_commit": [],
    }
    real_up = editor_state_service.upsert_editor_state
    real_proj = project_service.update_project
    real_set = task_service._set_task
    real_flush = dbmod.Session.flush
    real_commit = dbmod.Session.commit
    arm = {"finalizer": False}

    def spy_up(*a, **k):
        flush_before = len(seen["flush"])
        commit_flag = k.get("commit", "MISSING")
        seen["upsert"].append(commit_flag)
        result = real_up(*a, **k)
        delta = len(seen["flush"]) - flush_before
        if commit_flag is False:
            seen["upsert_flush_delta"].append(delta)
            arm["finalizer"] = True
        return result

    def spy_proj(*a, **k):
        flush_before = len(seen["flush"])
        commit_flag = k.get("commit", "MISSING")
        seen["project"].append(commit_flag)
        result = real_proj(*a, **k)
        delta = len(seen["flush"]) - flush_before
        if commit_flag is False:
            seen["project_flush_delta"].append(delta)
            arm["finalizer"] = True
        return result

    def spy_set(*a, **k):
        flush_before = len(seen["flush"])
        entry = {
            "commit": k.get("commit", "MISSING"),
            "status": k.get("status"),
            "progress": k.get("progress"),
        }
        seen["set_task"].append(entry)
        result = real_set(*a, **k)
        delta = len(seen["flush"]) - flush_before
        if entry["commit"] is False:
            seen["set_task_flush_delta"].append(delta)
            if entry.get("status") == "success" or entry.get("progress") == 100:
                arm["finalizer"] = True
        return result

    def spy_flush(self, *a, **k):
        seen["flush"].append("flush")
        return real_flush(self, *a, **k)

    def _session_has_success_package(session: Any) -> bool:
        """用途：五域 staged success 才视为 finalizer commit 候选。"""
        objs = list(getattr(session, "new", set())) + list(
            getattr(session, "dirty", set())
        )
        has_task_success = False
        has_event_success = False
        has_project = False
        has_editor = False
        for obj in objs:
            if isinstance(obj, ProjectTaskRow) and getattr(obj, "status", None) == "success":
                has_task_success = True
            if isinstance(obj, ProjectTaskEventRow) and getattr(obj, "status", None) == "success":
                has_event_success = True
            cls_name = type(obj).__name__
            if "Project" in cls_name and "Task" not in cls_name and "File" not in cls_name:
                has_project = True
            if "EditorState" in cls_name or "Revision" in cls_name:
                has_editor = True
        return has_task_success and (has_event_success or has_project or has_editor)

    def spy_commit(self, *a, **k):
        seen["commit"].append("commit")
        # S4：仅在已 arm 且 session 呈 success 包时统计 finalizer commit
        if arm["finalizer"] and _session_has_success_package(self):
            seen["finalizer_commit"].append("commit")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(editor_state_service, "upsert_editor_state", spy_up)
    monkeypatch.setattr(project_service, "update_project", spy_proj)
    monkeypatch.setattr(task_service, "_set_task", spy_set)
    if hasattr(task_service, "upsert_editor_state"):
        monkeypatch.setattr(task_service, "upsert_editor_state", spy_up)
    if hasattr(task_service, "update_project"):
        monkeypatch.setattr(task_service, "update_project", spy_proj)
    monkeypatch.setattr(dbmod.Session, "flush", spy_flush)
    monkeypatch.setattr(dbmod.Session, "commit", spy_commit)

    pid = _create_project(client, "M2-h1-commit")
    _upload_md(client, pid, "t.md", "# T\n\nH1_OK\n")
    # 重置 finalizer 计数：setup 阶段可能误触 arm
    seen["finalizer_commit"].clear()
    arm["finalizer"] = False
    seen["upsert"].clear()
    seen["project"].clear()
    seen["set_task"].clear()
    seen["upsert_flush_delta"].clear()
    seen["project_flush_delta"].clear()
    seen["set_task_flush_delta"].clear()

    body = _parse(client, pid)
    assert body["status"] == "success", body
    assert seen["upsert"], "业务红：upsert_editor_state 必须被调用"
    assert all(c is False for c in seen["upsert"]), (
        f"业务红：upsert 必须 commit=False，actual={seen['upsert']}"
    )
    assert seen["project"], "业务红：update_project 必须被调用"
    assert all(c is False for c in seen["project"]), (
        f"业务红：update_project 必须 commit=False，actual={seen['project']}"
    )
    success_sets = [
        x for x in seen["set_task"] if x.get("status") == "success" or x.get("progress") == 100
    ]
    assert success_sets, f"业务红：_set_task success 必须被调用，actual={seen['set_task']}"
    assert all(x.get("commit") is False for x in success_sets), (
        f"业务红：_set_task success 必须 commit=False，actual={success_sets}"
    )
    # 各自 wrapper 的 flush 增量（不是全局“出现过任意 flush”）
    assert seen["upsert_flush_delta"] and all(d >= 1 for d in seen["upsert_flush_delta"]), (
        f"业务红：upsert commit=False 必须各自 flush，delta={seen['upsert_flush_delta']}"
    )
    assert seen["project_flush_delta"] and all(d >= 1 for d in seen["project_flush_delta"]), (
        f"业务红：update_project commit=False 必须各自 flush，delta={seen['project_flush_delta']}"
    )
    assert seen["set_task_flush_delta"] and all(
        d >= 1 for d in seen["set_task_flush_delta"]
    ), f"业务红：_set_task commit=False 必须各自 flush，delta={seen['set_task_flush_delta']}"
    assert len(seen["finalizer_commit"]) == 1, (
        f"业务红：五域 staged success 的 finalizer commit 必须精确 1，"
        f"actual={len(seen['finalizer_commit'])} all_commits={len(seen['commit'])}"
    )


def test_h2_success_package_exact_one_success_event(client: TestClient):
    """用途：成功包五域；按 task id 精确 1 条 success/100，禁止 >=（Q10）。"""
    pid = _create_project(client, "M2-success-pkg")
    _upload_md(client, pid, "t.md", "# T\n\nPKG_OK\n")
    before_rev = _revision_count(pid)
    body = _parse(client, pid)
    assert body["status"] == "success"
    tid = body["id"]
    md = _editor_md(client, pid)
    _assert_success_result(
        body["result"], engine="lightweight", file_count=1, chars=len(md)
    )
    assert "PKG_OK" in md
    assert _revision_count(pid) == before_rev + 1
    assert _success_event_count_for_task(pid, tid) == 1, (
        f"业务红：task {tid} 的 success/100 事件必须精确 1 条"
    )
    proj = _project_snapshot(client, pid)
    assert proj.get("status") == "analyzing"
    assert proj.get("technicalPlanStep") == 1


def test_h3_finalizer_fault_points_rollback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """
    用途：S5 H3 — upsert / project / set_task(success) / final commit 四点故障；
      每点调用 _assert_five_domains_unchanged（含 project status/step）；
      GET 与 POST 的 failed/error/result 精确一致；success event/result=0。
    """
    from app.services import editor_state_service, project_service, task_service
    from app.core import database as dbmod

    FIXED_FINALIZER_ERROR = "任务落盘失败"

    faults = [
        ("upsert", editor_state_service, "upsert_editor_state"),
        ("project", project_service, "update_project"),
        ("set_task", task_service, "_set_task"),
    ]

    for label, mod, name in faults:
        pid = _create_project(client, f"M2-rb-{label}")
        _upload_md(client, pid, "t.md", f"# T\n\nSHOULD_NOT_COMMIT_{label}\n")
        before = _snapshot_five_domains(client, pid)
        real = getattr(mod, name)
        inject_count = {"n": 0}

        def _boom(*a, _label=label, _real=real, _cnt=inject_count, **k):
            if _label == "set_task":
                status = k.get("status")
                if status == "success":
                    _cnt["n"] += 1
                    assert _cnt["n"] == 1, "set_task success 注入必须精确一次"
                    raise RuntimeError(f"injected {_label} failure")
                return _real(*a, **k)
            _cnt["n"] += 1
            raise RuntimeError(f"injected {_label} failure")

        monkeypatch.setattr(mod, name, _boom)
        if hasattr(task_service, name) and mod is not task_service:
            monkeypatch.setattr(task_service, name, _boom)

        body = _parse(client, pid)
        assert body["status"] == "failed", (
            f"业务红：{label} 故障后任务必须 failed，实际={body['status']}"
        )
        assert inject_count["n"] == 1, (
            f"业务红：{label} 注入点调用计数必须=1，actual={inject_count['n']}"
        )
        assert body.get("error") == FIXED_FINALIZER_ERROR
        assert body.get("result") is None
        # S5：四故障分支全部 _assert_five_domains_unchanged（含 status/step）
        _assert_five_domains_unchanged(client, pid, before, label=f"h3-{label}")
        _assert_post_get_error_consistent(client, pid, body)
        assert _success_event_count_for_task(pid, body["id"]) == 0
        err = body.get("error") or ""
        assert f"injected {label} failure" not in err
        assert "RuntimeError" not in err
        _assert_no_leak(err)
        monkeypatch.setattr(mod, name, real)
        if hasattr(task_service, name) and mod is not task_service:
            monkeypatch.setattr(task_service, name, real)

    # final commit：list+list 并集；不得 catch 后伪装 false
    pid = _create_project(client, "M2-rb-commit")
    _upload_md(client, pid, "t.md", "# T\n\nSHOULD_NOT_COMMIT_final\n")
    before = _snapshot_five_domains(client, pid)
    real_commit = dbmod.Session.commit
    state = {"fired": 0}

    def _commit_boom(self, *a, **k):
        # S5：list(new)+list(dirty)；禁止 list|list；禁止 except 伪装 false
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
            raise RuntimeError("injected final commit failure")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(dbmod.Session, "commit", _commit_boom)
    body = _parse(client, pid)
    assert body["status"] == "failed", (
        f"业务红：final commit 故障后必须 failed，实际={body['status']}"
    )
    assert state["fired"] == 1, "业务红：final commit 注入必须精确 one-shot=1"
    assert body.get("error") == FIXED_FINALIZER_ERROR
    assert body.get("result") is None
    _assert_five_domains_unchanged(client, pid, before, label="h3-final-commit")
    _assert_post_get_error_consistent(client, pid, body)
    assert _success_event_count_for_task(pid, body["id"]) == 0
    monkeypatch.setattr(dbmod.Session, "commit", real_commit)


# ===========================================================================
# I. 并发 / 超时 / 取消（Q11）
# ===========================================================================


def test_i1_managed_semaphore_surrounds_runner_max_active_one(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    用途：S3 I1 — 两线程各构造真实 ManagedSource+合法 manifest 调 public service；
      只 patch inner runner；真实 semaphore；Event 协调、无 sleep；
      runner=2/max_active=1/errors=[]/两线程结束/sem=1。不得零参。
    """
    svc = _load_managed_service()
    run = _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)
    sem = getattr(svc, "MANAGED_SEMAPHORE", None)
    assert sem is not None, "业务红：managed 必须暴露 MANAGED_SEMAPHORE=BoundedSemaphore(1)"
    initial = getattr(sem, "_initial_value", None)
    if initial is None:
        initial = getattr(sem, "_value", None)
    assert int(initial) == 1, f"业务红：semaphore 必须为 1，actual={initial}"

    active = {"n": 0, "max": 0, "calls": 0}
    lock = threading.Lock()
    first_entered = threading.Event()
    hold_first = threading.Event()
    observed_while_held = {"n": None, "max": None, "calls": None}

    def fake_ready(*a, **k):
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        return True

    def fake_runner(ready, input_path, *, task_deadline, cancel_check):
        with lock:
            active["calls"] += 1
            idx = active["calls"]
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        try:
            if idx == 1:
                first_entered.set()
                assert hold_first.wait(timeout=5), "首 runner 等待放行超时"
            return "#x"
        finally:
            with lock:
                active["n"] -= 1

    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, fake_runner)

    _runtime, manifest = _prepare_fake_runtime("i1_m_")
    # 每线程独立真实 ManagedSource + 合法输入文件
    src_roots: list[Path] = []
    sources_per_thread: list[list[Any]] = []
    for i in range(2):
        root = _make_temp(f"i1_src_{i}_")
        src_roots.append(root)
        fpath = root / f"s{i}.pdf"
        fpath.write_bytes(b"%PDF-1.4 fake")
        sources_per_thread.append(
            [
                _make_managed_source(
                    svc,
                    path=fpath,
                    filename=f"s{i}.pdf",
                    expected_size=fpath.stat().st_size,
                )
            ]
        )

    errors: list[BaseException] = []

    def worker(sources: list[Any]):
        try:
            _call_service_run(
                run,
                sources,
                manifest_path=manifest,
                cancel_check=lambda: False,
            )
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=(sources_per_thread[0],))
    t2 = threading.Thread(target=worker, args=(sources_per_thread[1],))
    t1.start()
    t2.start()
    assert first_entered.wait(timeout=5), "业务红：首个 inner runner 必须进入"
    with lock:
        observed_while_held["n"] = active["n"]
        observed_while_held["max"] = active["max"]
        observed_while_held["calls"] = active["calls"]
    assert observed_while_held["calls"] == 1
    assert observed_while_held["n"] == 1
    assert observed_while_held["max"] == 1
    hold_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive(), "业务红：两线程必须结束"
    assert errors == [], f"业务红：worker 不得异常，errors={errors}"
    assert active["calls"] == 2, f"业务红：runner 调用必须=2，actual={active['calls']}"
    assert active["max"] == 1, f"业务红：max_active 必须=1，actual={active['max']}"
    cur = getattr(sem, "_value", None)
    assert cur == 1, f"业务红：semaphore 结束后必须恢复 1，actual={cur}"


def test_i2_timeout_constants_consumed_by_execution_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    用途：T4 I2 — 仅锁定精确常量值；执行路径行为由 I3 证明。
      禁止重复复杂场景 / 源码扫描 / 手工 consumed 标志。
    """
    svc = _load_managed_service()
    _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)
    _assert_exact_core_runner_api(core)

    assert getattr(core, "FILE_TIMEOUT_SEC", None) == 1800, (
        f"业务红：FILE_TIMEOUT_SEC 必须=1800，actual={getattr(core, 'FILE_TIMEOUT_SEC', None)}"
    )
    poll = float(getattr(core, "POLL_INTERVAL_SEC"))
    assert poll <= 1.0, f"业务红：POLL_INTERVAL_SEC 必须<=1，actual={poll}"
    task_sec = getattr(svc, "TASK_TIMEOUT_SEC", None)
    if task_sec is None:
        task_sec = getattr(core, "TASK_TIMEOUT_SEC", None)
    assert task_sec == 7200, f"业务红：TASK_TIMEOUT_SEC 必须=7200，actual={task_sec}"


def test_i3_cancel_file_timeout_task_timeout_terminate_kill(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    用途：T4 I3 拆三条，精确签名，无 TypeError/BaseException fallback：
      1) core cancel：合法 ready+真实 input；fake Popen；cancel 在 >=2 poll 后 true
      2) core file timeout：fake clock 至少 2 次 poll；monotonic 越过 1800
      3) service task timeout：两 ManagedSource；首 runner 越过 7200；second 不调用
    每条 len(raised)==1 + 精确 diagnostic_code + M1 message；
    terminate 后仍 alive 时无条件 terminate→wait→kill；sleep 补丁为零等待。
    """
    import subprocess as sp
    import time as time_mod

    svc = _load_managed_service()
    run = _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)
    runner = _assert_exact_core_runner_api(core)
    _runtime, manifest = _prepare_fake_runtime("i3_rt_")

    # U3：生产路径 poll wait 证据（time.sleep 或 Popen.wait(timeout)）；
    # cleanup wait（terminate 后）单独记录，不得冒充 poll wait。
    # fake poll() 只返回状态，不得自行推进 monotonic。
    poll_waits: list[float] = []
    cleanup_waits: list[float] = []
    clock_box: dict[str, Any] = {
        "mono": None,
        "events": None,
        "jump_after_n_poll_waits": None,
    }

    def _zero_sleep(seconds: float = 0, *a, **k):
        """用途：记录生产 sleep 传入的真实 interval，并在 hook 内推进时钟。"""
        gap = float(seconds)
        poll_waits.append(gap)
        mono = clock_box.get("mono")
        if mono is not None:
            mono["t"] += gap
            jump_after = clock_box.get("jump_after_n_poll_waits")
            if jump_after is not None and len(poll_waits) >= int(jump_after):
                mono["t"] = float(core.FILE_TIMEOUT_SEC) + 1.0
        return None

    def _record_popen_wait(
        timeout: float | None,
        *,
        events: list[str],
        mono: dict[str, float],
    ) -> None:
        """用途：terminate 前的 wait(timeout) 记 poll wait；之后记 cleanup wait。"""
        if timeout is None:
            return
        gap = float(timeout)
        if "terminate" in events:
            cleanup_waits.append(gap)
            return
        poll_waits.append(gap)
        mono["t"] += gap
        jump_after = clock_box.get("jump_after_n_poll_waits")
        if jump_after is not None and len(poll_waits) >= int(jump_after):
            mono["t"] = float(core.FILE_TIMEOUT_SEC) + 1.0

    monkeypatch.setattr(time_mod, "sleep", _zero_sleep)
    if hasattr(core, "time"):
        monkeypatch.setattr(core.time, "sleep", _zero_sleep)

    def _make_ready() -> Any:
        load = _require_attr(core, CORE_LOAD_MANIFEST)
        validate = _require_attr(core, CORE_VALIDATE_READY)
        return validate(load(manifest))

    def _assert_popen_isolation(kwargs: dict, temp_root: Path | None) -> None:
        assert kwargs.get("shell", False) is False
        assert kwargs.get("stdout") is sp.DEVNULL
        assert kwargs.get("stderr") is sp.DEVNULL
        env = kwargs.get("env")
        cwd = kwargs.get("cwd")
        assert isinstance(env, dict), "业务红：env 必须为全可写隔离 dict"
        assert cwd is not None
        if temp_root is not None:
            assert Path(str(cwd)).resolve() == temp_root.resolve()
        for banned in (
            "BIAOSHU_MANAGED_OCR_MANIFEST",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
        ):
            assert banned not in env, f"业务红：敏感 env 必须剥离 {banned}"

    def _assert_poll_waits_from_production(poll_count: int) -> None:
        """用途：U3 — poll_waits 非空；每条 0 < interval <= 1；>=2 poll。"""
        assert poll_count >= 2, (
            f"业务红：cancel/file 必须至少 2 次 poll，actual={poll_count}"
        )
        assert float(core.POLL_INTERVAL_SEC) <= 1.0
        assert len(poll_waits) >= 1, (
            f"业务红：必须从生产 sleep/wait 记录至少一条 poll wait，"
            f"poll_waits={poll_waits!r} cleanup_waits={cleanup_waits!r}"
        )
        for gap in poll_waits:
            assert 0.0 < gap <= 1.0 + 1e-6, (
                f"业务红：poll wait 必须满足 0 < interval <= 1，gap={gap}"
            )

    # ---------- 1) core cancel ----------
    events_c: list[str] = []
    mono_c: dict[str, float] = {"t": 0.0}
    poll_ts_c: list[float] = []
    temp_holder: list[Path] = []
    cancel_after_poll = {"n": 0}
    poll_waits.clear()
    cleanup_waits.clear()
    clock_box["mono"] = mono_c
    clock_box["events"] = events_c
    clock_box["jump_after_n_poll_waits"] = None

    class FakePopenCancel:
        def __init__(self, *a, **k):
            events_c.append("popen")
            _assert_popen_isolation(k, temp_holder[0] if temp_holder else None)
            self.returncode = None
            self._alive = True

        def poll(self):
            # fake poll 只返回状态，不推进 monotonic（U3）
            events_c.append("poll")
            poll_ts_c.append(mono_c["t"])
            cancel_after_poll["n"] += 1
            return None

        def terminate(self):
            events_c.append("terminate")
            # 仍存活 → 无条件逼 terminate→cleanup wait→kill
            self._alive = True
            self.returncode = None

        def kill(self):
            events_c.append("kill")
            self._alive = False
            self.returncode = -9

        def wait(self, timeout=None):
            events_c.append("wait")
            _record_popen_wait(timeout, events=events_c, mono=mono_c)
            if self._alive:
                return None
            return self.returncode

    real_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*a, **k):
        root = real_mkdtemp(*a, **k)
        temp_holder.append(Path(root).resolve())
        return root

    # 仅 patch core 内部 TEMP seam，避免污染测试夹具
    if hasattr(core, "tempfile"):
        monkeypatch.setattr(core.tempfile, "mkdtemp", tracking_mkdtemp)
    else:
        monkeypatch.setattr(tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(sp, "Popen", FakePopenCancel)
    if hasattr(core, "subprocess"):
        monkeypatch.setattr(core.subprocess, "Popen", FakePopenCancel)
    monkeypatch.setattr(time_mod, "monotonic", lambda: mono_c["t"])
    if hasattr(core, "time"):
        monkeypatch.setattr(core.time, "monotonic", lambda: mono_c["t"])

    def cancel_check_c():
        # 至少 2 次 poll 后才取消，形成多轮询证据
        return cancel_after_poll["n"] >= 2

    input_c = _make_temp("i3_c_") / "c.pdf"
    input_c.write_bytes(b"%PDF-1.4 cancel")
    raised_c: list[BaseException] = []
    try:
        _call_core_runner(
            runner,
            _make_ready(),
            input_c,
            task_deadline=mono_c["t"] + 7200.0,
            cancel_check=cancel_check_c,
        )
    except BaseException as exc:
        raised_c.append(exc)
    assert "popen" in events_c
    assert events_c.count("poll") >= 2, f"cancel 必须>=2 poll，events={events_c}"
    _assert_poll_waits_from_production(events_c.count("poll"))
    assert "terminate" in events_c, f"cancel 必须 terminate，events={events_c}"
    ti = events_c.index("terminate")
    assert "wait" in events_c[ti:], f"terminate 后必须 cleanup wait，events={events_c}"
    assert "kill" in events_c[ti:], f"terminate 后仍 alive 必须 kill，events={events_c}"
    assert len(raised_c) == 1, f"core cancel 必须精确 1 异常，actual={raised_c!r}"
    _assert_exact_diagnostic(raised_c[0], "interrupted")

    # ---------- 2) core file timeout：至少 2 poll，monotonic 越过 1800 ----------
    events_f: list[str] = []
    mono_f: dict[str, float] = {"t": 0.0}
    poll_ts_f: list[float] = []
    temp_f: list[Path] = []
    poll_waits.clear()
    cleanup_waits.clear()
    clock_box["mono"] = mono_f
    clock_box["events"] = events_f
    # 第一条生产 poll wait 后跃迁时钟越过 FILE_TIMEOUT（interval 仍记真实传入值）
    clock_box["jump_after_n_poll_waits"] = 1

    class FakePopenFile:
        def __init__(self, *a, **k):
            events_f.append("popen")
            _assert_popen_isolation(k, temp_f[0] if temp_f else None)
            self.returncode = None
            self._alive = True

        def poll(self):
            # fake poll 只返回状态，不推进 monotonic（U3）
            events_f.append("poll")
            poll_ts_f.append(mono_f["t"])
            return None

        def terminate(self):
            events_f.append("terminate")
            self._alive = True

        def kill(self):
            events_f.append("kill")
            self._alive = False
            self.returncode = -9

        def wait(self, timeout=None):
            events_f.append("wait")
            _record_popen_wait(timeout, events=events_f, mono=mono_f)
            return None if self._alive else self.returncode

    def tracking_mkdtemp_f(*a, **k):
        root = real_mkdtemp(*a, **k)
        temp_f.append(Path(root).resolve())
        return root

    if hasattr(core, "tempfile"):
        monkeypatch.setattr(core.tempfile, "mkdtemp", tracking_mkdtemp_f)
    else:
        monkeypatch.setattr(tempfile, "mkdtemp", tracking_mkdtemp_f)
    monkeypatch.setattr(sp, "Popen", FakePopenFile)
    if hasattr(core, "subprocess"):
        monkeypatch.setattr(core.subprocess, "Popen", FakePopenFile)
    monkeypatch.setattr(time_mod, "monotonic", lambda: mono_f["t"])
    if hasattr(core, "time"):
        monkeypatch.setattr(core.time, "monotonic", lambda: mono_f["t"])

    input_f = _make_temp("i3_f_") / "f.pdf"
    input_f.write_bytes(b"%PDF-1.4 fileto")
    raised_f: list[BaseException] = []
    try:
        _call_core_runner(
            runner,
            _make_ready(),
            input_f,
            task_deadline=10_000.0,
            cancel_check=lambda: False,
        )
    except BaseException as exc:
        raised_f.append(exc)
    assert events_f.count("poll") >= 2, f"file timeout 必须>=2 poll，events={events_f}"
    _assert_poll_waits_from_production(events_f.count("poll"))
    assert "terminate" in events_f, f"file timeout 必须 terminate，events={events_f}"
    ti_f = events_f.index("terminate")
    assert "wait" in events_f[ti_f:], f"terminate 后必须 cleanup wait，events={events_f}"
    assert "kill" in events_f[ti_f:], f"terminate 后仍 alive 必须 kill，events={events_f}"
    assert len(raised_f) == 1, f"file timeout 必须精确 1 异常，actual={raised_f!r}"
    _assert_exact_diagnostic(raised_f[0], "parser_timeout")

    # ---------- 3) service task timeout：两 ManagedSource ----------
    mono_t = {"t": 0.0}
    runner_n = {"n": 0}

    def fake_ready(*a, **k):
        return SimpleNamespace(ok=True, diagnostic_code="static_ready")

    def fake_recheck(*a, **k):
        return True

    def fake_one(ready, input_path, *, task_deadline, cancel_check):
        runner_n["n"] += 1
        if runner_n["n"] == 1:
            mono_t["t"] = 7200.0 + 1.0
        return "#x\n"

    monkeypatch.setattr(core, CORE_VALIDATE_READY, fake_ready)
    monkeypatch.setattr(core, CORE_RECHECK, fake_recheck)
    monkeypatch.setattr(core, CORE_RUN_ONE, fake_one)
    monkeypatch.setattr(time_mod, "monotonic", lambda: mono_t["t"])
    if hasattr(svc, "time"):
        monkeypatch.setattr(svc.time, "monotonic", lambda: mono_t["t"])

    root = _make_temp("i3_task_")
    p1 = root / "a.pdf"
    p2 = root / "b.pdf"
    p1.write_bytes(b"%PDF a")
    p2.write_bytes(b"%PDF b")
    sources = [
        _make_managed_source(svc, path=p1, filename="a.pdf", expected_size=p1.stat().st_size),
        _make_managed_source(svc, path=p2, filename="b.pdf", expected_size=p2.stat().st_size),
    ]
    raised_t: list[BaseException] = []
    try:
        _call_service_run(
            run,
            sources,
            manifest_path=manifest,
            cancel_check=lambda: False,
        )
    except BaseException as exc:
        raised_t.append(exc)
    assert runner_n["n"] == 1, f"second runner 不得调用，n={runner_n['n']}"
    assert mono_t["t"] > 7200.0
    assert len(raised_t) == 1, f"task timeout 必须精确 1 异常，actual={raised_t!r}"
    # 固定 M1：parser_timeout / 解析器运行超时（唯一 code，不二选一）
    _assert_exact_diagnostic(raised_t[0], "parser_timeout")


def test_i4_temp_cleaned_on_failure_timeout_cancel_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """
    用途：T5 I4 — 不得 patch CORE_RUN_ONE；调用真实 core runner；
      输入三文件在 TEMP patch 前用 tmp_path 创建；只记录 runner 内部 TEMP；
      冻结唯一 TEMP seam=core.tempfile.mkdtemp（无四名枚举/全局先 patch 后建输入）；
      三场景各 len(raised)==1 + 精确 code/M1 message；预存在根末尾显式删。
    """
    import subprocess as sp
    import time as time_mod

    # 预存在根与三输入：必须早于 TEMP factory patch
    pre_existing = tmp_path / "biaoshu-managed-preexist"
    pre_existing.mkdir(parents=True, exist_ok=True)
    assert pre_existing.exists()
    scenarios = (
        "parser_failed",
        "parser_timeout",
        "interrupted",
    )
    inputs: dict[str, Path] = {}
    for label in scenarios:
        p = tmp_path / f"i4_in_{label}" / "x.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 i4")
        inputs[label] = p

    svc = _load_managed_service()
    _assert_exact_service_run_api(svc)
    core = _require_service_core_module(svc)
    runner = _assert_exact_core_runner_api(core)
    assert callable(getattr(core, CORE_RUN_ONE))

    _runtime, manifest = _prepare_fake_runtime("i4_rt_")
    load = _require_attr(core, CORE_LOAD_MANIFEST)
    validate = _require_attr(core, CORE_VALIDATE_READY)
    ready = validate(load(manifest))

    real_mkdtemp = tempfile.mkdtemp
    expected_codes = {
        "parser_failed": "parser_failed",
        "parser_timeout": "parser_timeout",
        "interrupted": "interrupted",
    }

    for label in scenarios:
        created: list[Path] = []
        mono = {"t": 0.0}
        cancel_n = {"n": 0}

        def tracking_mkdtemp(*a, _created=created, **k):
            root = real_mkdtemp(*a, **k)
            p = Path(root).resolve()
            _created.append(p)
            return root

        class FakePopen:
            def __init__(self, *a, _label=label, **k):
                assert k.get("shell", False) is False
                assert k.get("stdout") is sp.DEVNULL
                assert k.get("stderr") is sp.DEVNULL
                self.returncode = None
                self._alive = True
                self._label = _label

            def poll(self):
                cancel_n["n"] += 1
                if self._label == "parser_timeout":
                    mono["t"] = float(core.FILE_TIMEOUT_SEC) + 1.0
                if self._label == "parser_failed":
                    self.returncode = 2
                    self._alive = False
                    return 2
                return None

            def terminate(self):
                self._alive = True

            def kill(self):
                self._alive = False
                self.returncode = -9

            def wait(self, timeout=None):
                return None if self._alive else self.returncode

        def cancel_check(_label=label):
            if _label == "interrupted":
                return cancel_n["n"] >= 1
            return False

        # T5：只 patch core 内部 tempfile.mkdtemp（唯一 TEMP factory seam）
        assert hasattr(core, "tempfile"), (
            "业务红：core 必须经 core.tempfile.mkdtemp 自建 work TEMP"
        )
        monkeypatch.setattr(core.tempfile, "mkdtemp", tracking_mkdtemp)
        monkeypatch.setattr(sp, "Popen", FakePopen)
        if hasattr(core, "subprocess"):
            monkeypatch.setattr(core.subprocess, "Popen", FakePopen)
        monkeypatch.setattr(time_mod, "monotonic", lambda: mono["t"])
        if hasattr(core, "time"):
            monkeypatch.setattr(core.time, "monotonic", lambda: mono["t"])
            monkeypatch.setattr(core.time, "sleep", lambda *a, **k: None)

        input_pdf = inputs[label]
        raised: list[BaseException] = []
        try:
            _call_core_runner(
                runner,
                ready,
                input_pdf,
                task_deadline=mono["t"] + 7200.0,
                cancel_check=cancel_check,
            )
        except BaseException as exc:
            raised.append(exc)

        assert len(raised) == 1, (
            f"{label}: 必须精确 1 异常，actual={raised!r}"
        )
        _assert_exact_diagnostic(raised[0], expected_codes[label])
        assert created, f"{label}: runner 内部 TEMP factory 必须真实创建并记录"
        # 输入根不得进入 created（生产不应删测试输入）
        for root in created:
            assert root != input_pdf.parent.resolve()
            assert root != tmp_path.resolve()
            assert not root.exists(), (
                f"{label}: 真实 core finally 必须清理 TEMP {root}"
            )
        assert pre_existing.exists(), "业务红：不得删除测试前已存在目录"
        assert input_pdf.exists(), f"{label}: 不得删除测试输入文件"
        # 每场景恢复 TEMP patch，避免串扰
        monkeypatch.setattr(core.tempfile, "mkdtemp", real_mkdtemp)

    assert pre_existing.exists()
    # 末尾显式删除预存在根
    shutil.rmtree(pre_existing)
    assert not pre_existing.exists()


# ===========================================================================
# J. self-guard（Q13）
# ===========================================================================


def test_j1_no_toplevel_import_of_missing_core_or_service():
    src = _THIS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.endswith("managed_parse_runtime_service") or mod.endswith(
                "managed_ocr_runtime_core"
            ):
                banned.append(mod)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "managed_parse_runtime_service" in alias.name:
                    banned.append(alias.name)
                if "managed_ocr_runtime_core" in alias.name:
                    banned.append(alias.name)
    assert banned == [], f"禁止顶层 import 缺失生产模块: {banned}"


def test_j2_no_skip_xfail_or_assert_or_or_multi_code_sets():
    src = _THIS_FILE.read_text(encoding="utf-8")
    assert "pytest.mark." + "skip" not in src
    assert "pytest.mark." + "xfail" not in src
    assert "unittest." + "skip" not in src
    tree = ast.parse(src)
    sleep_hits = []
    or_hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # 禁止 time.sleep / 真实阻塞 sleep 调用
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                if isinstance(func.value, ast.Name) and func.value.id == "time":
                    sleep_hits.append(node.lineno)
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                if isinstance(func.value, ast.Attribute) and func.value.attr == "time":
                    sleep_hits.append(node.lineno)
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.BoolOp):
            if isinstance(node.test.op, ast.Or):
                or_hits.append(node.lineno)
    assert sleep_hits == [], f"禁止真实 sleep 调用: {sleep_hits}"
    assert or_hits == [], f"禁止断言 BoolOp Or: {or_hits}"


def test_j3_strict_four_file_whitelist_markers():
    """用途：白名单四文件存在；禁止把 live 版本对比写成永久生产门。"""
    src = _THIS_FILE.read_text(encoding="utf-8")
    for snip in (
        "requests." + "get",
        "requests." + "post",
        "httpx." + "Client",
        "urllib.request." + "urlopen",
        "socket." + "create_connection",
        "pip " + "install",
    ):
        assert snip not in src
    assert len(_ALLOWED_DIFF_FILES) == 4
    for rel in _ALLOWED_DIFF_FILES:
        path = REPO_ROOT / rel
        assert path.exists(), f"白名单文件缺失: {rel}"
    # 禁止永久测试依赖版本库对比生产门（外部报告另计）
    # 通过 AST 检查 Call 是否调用 subprocess 跑版本库对比，而非字面自匹配


def test_j4_file_sha_and_bytes_reportable():
    data = _THIS_FILE.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert len(digest) == 64
    assert len(data) > 1000


def test_j5_bans_conditional_success_and_keyword_only_gates():
    """
    用途：AST 自守卫：禁止以调用/事件非空为条件包裹核心断言；
      禁止吞 Exception/pass/continue 作为成功；锁定固定未配置 code/中文（R12）。
      S11/T：禁止 I2/I3 使用 inspect.getsource；禁止 TypeError/BaseException fallback；
      禁止零参 public run；B3 loader/run_calls 精确一次；I3/I4 禁 if raised 包裹核心诊断；
      I4 输入创建必须早于 TEMP patch；API 参数精确等式。
      U1：API helper 锁定 POSITIONAL_OR_KEYWORD + Parameter.empty；
      U2：diagnostic 禁止 .code/.error/.args fallback 与 substring；
      U3：poll_waits 非空、fake poll 不推进 clock、cleanup wait 分离。
    """
    src = _THIS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 旧假绿文案不得再出现（拆字避免本断言自匹配）
    old_gate = "业务红：未实现 source " + "symlink/reparse 拒绝"
    occurrences = [
        ln for ln in src.splitlines() if old_gate in ln and "old_gate" not in ln
    ]
    assert occurrences == [], f"旧假绿关键词门仍存在: {occurrences}"
    assert "FIXED_UNCONFIGURED_CODE" in src
    assert "FIXED_UNCONFIGURED_ERROR" in src
    assert FIXED_UNCONFIGURED_CODE == "runtime_manifest_invalid"
    assert FIXED_UNCONFIGURED_ERROR == "运行时清单无效"
    # S9：G4/C2 固定 M1 文案
    assert FIXED_MANAGED_EXCEPTION_ERROR == "预检内部错误"
    # 拆字避免本断言自匹配
    old_c2_err = "CLI 身份变化" + "或缺失"
    old_c2_hits = [
        ln for ln in src.splitlines() if old_c2_err in ln and "old_c2_err" not in ln
    ]
    assert old_c2_hits == [], f"禁止新造 cli_missing 文案: {old_c2_hits}"
    assert M1_DIAG_MESSAGES["cli_missing"] == "未找到解析器命令或安全类型不合格"
    assert M1_DIAG_MESSAGES["internal_error"] == "预检内部错误"

    banned_cond_names = {
        "load_calls",
        "ready_calls",
        "runner_calls",
        "run_calls",
        "ready_first",
        "events",
        "calls",
        "bound",
        "raised",
        "raised_c",
        "raised_f",
        "raised_t",
        "raised_file",
        "raised_task",
    }
    cond_hits: list[int] = []
    bare_except_hits: list[int] = []
    top_level_public_patch_hits: list[int] = []

    def _name_in(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                names.add(n.id)
        return names

    # 按函数定位 I2/I3/I4/B3 源码范围
    i2_i3_i4_funcs: list[ast.FunctionDef] = []
    b3_func: ast.FunctionDef | None = None
    i4_fn: ast.FunctionDef | None = None
    api_helpers: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name in {
                "test_i2_timeout_constants_consumed_by_execution_path",
                "test_i3_cancel_file_timeout_task_timeout_terminate_kill",
                "test_i4_temp_cleaned_on_failure_timeout_cancel_paths",
            }:
                i2_i3_i4_funcs.append(node)
            if node.name == "test_b3_client_payload_query_header_cannot_inject_manifest_path":
                b3_func = node
            if node.name == "test_i4_temp_cleaned_on_failure_timeout_cancel_paths":
                i4_fn = node
            if node.name in {
                "_assert_exact_service_run_api",
                "_assert_exact_core_runner_api",
            }:
                api_helpers.append(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            names = _name_in(node.test)
            if names & banned_cond_names:
                body_has_assert = any(
                    isinstance(x, ast.Assert)
                    for x in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                )
                if body_has_assert:
                    cond_hits.append(getattr(node, "lineno", -1))
        if isinstance(node, ast.ExceptHandler):
            if node.type is None or (
                isinstance(node.type, ast.Name)
                and node.type.id in {"Exception", "BaseException"}
            ):
                for stmt in node.body:
                    if isinstance(stmt, (ast.Pass, ast.Continue)):
                        bare_except_hits.append(getattr(node, "lineno", -1))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "setattr":
                if len(node.args) >= 2:
                    a1 = node.args[1]
                    if isinstance(a1, ast.Constant) and a1.value == SERVICE_PUBLIC_RUN:
                        top_level_public_patch_hits.append(getattr(node, "lineno", -1))

    assert cond_hits == [], (
        f"业务红：禁止以调用/事件/raised 非空条件包裹核心断言，行={cond_hits}"
    )
    assert bare_except_hits == [], (
        f"业务红：禁止 except Exception 后 pass/continue 当成功，行={bare_except_hits}"
    )
    assert top_level_public_patch_hits == [], (
        f"业务红：禁止 monkeypatch 替换 public {SERVICE_PUBLIC_RUN}，行={top_level_public_patch_hits}"
    )

    # S11/T：I2/I3/I4 禁止 getsource；禁止 TypeError fallback / 备用控件
    for fn in i2_i3_i4_funcs:
        getsource_hits: list[int] = []
        typeerror_hits: list[int] = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "getsource":
                    getsource_hits.append(getattr(n, "lineno", -1))
                if isinstance(f, ast.Name) and f.id == "getsource":
                    getsource_hits.append(getattr(n, "lineno", -1))
            if isinstance(n, ast.ExceptHandler):
                if isinstance(n.type, ast.Name) and n.type.id == "TypeError":
                    typeerror_hits.append(getattr(n, "lineno", -1))
            if isinstance(n, ast.Name) and n.id == "run_with_timeout_controls":
                typeerror_hits.append(getattr(n, "lineno", -1))
        assert getsource_hits == [], (
            f"业务红：{fn.name} 禁止 getsource 调用，行={getsource_hits}"
        )
        assert typeerror_hits == [], (
            f"业务红：{fn.name} 禁止 TypeError fallback/备用入口，行={typeerror_hits}"
        )

    # S11：禁止零参 public run（run() / fn() 无 sources）
    zero_arg_hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and not node.args and not node.keywords:
            if isinstance(node.func, ast.Name) and node.func.id in {"run", SERVICE_PUBLIC_RUN}:
                zero_arg_hits.append(getattr(node, "lineno", -1))
    assert zero_arg_hits == [], (
        f"业务红：禁止零参调用 public run，行={zero_arg_hits}"
    )

    # T2/S11：B3 loader 精确一次 + run_calls 精确 1
    assert b3_func is not None
    b3_src = ast.get_source_segment(src, b3_func) or ""
    assert "len(run_calls) == 1" in b3_src, (
        "业务红：B3 必须无条件精确断言 len(run_calls) == 1"
    )
    assert "load_calls == [server_manifest.resolve()]" in b3_src, (
        "业务红：B3 必须精确断言 load_calls == [server_manifest.resolve()]"
    )
    assert "real_run_one" not in b3_src, "业务红：B3 禁止调用 real_run_one"

    # T1/U1：API helper 必须锁定精确参数列表 + kind/default（无 args/path 别名、无 *args）
    for fn in api_helpers:
        hsrc = ast.get_source_segment(src, fn) or ""
        assert "VAR_POSITIONAL" in hsrc and "VAR_KEYWORD" in hsrc, (
            f"业务红：{fn.name} 必须禁止 *args/**kwargs"
        )
        assert "POSITIONAL_OR_KEYWORD" in hsrc, (
            f"业务红：{fn.name} 必须锁定 POSITIONAL_OR_KEYWORD"
        )
        assert "Parameter.empty" in hsrc, (
            f"业务红：{fn.name} 必须断言 default is Parameter.empty（全部 required）"
        )
        # 不得再把 Parameter.POSITIONAL_ONLY 当作合法 kind（U1 精确等式）
        assert "Parameter.POSITIONAL_ONLY" not in hsrc, (
            f"业务红：{fn.name} 不得接受 Parameter.POSITIONAL_ONLY；"
            "位置参必须精确 POSITIONAL_OR_KEYWORD"
        )
        if fn.name == "_assert_exact_service_run_api":
            assert (
                '["sources", "manifest_path", "cancel_check"]' in hsrc
            ), "业务红：service run API 必须精确三参数等式"
            assert "_assert_immutable_record" in hsrc, (
                "业务红：service 必须校验 ManagedSource/Output 不可变"
            )
        if fn.name == "_assert_exact_core_runner_api":
            assert (
                '["ready", "input_path", "task_deadline", "cancel_check"]' in hsrc
            ), "业务红：core runner 必须精确四参数等式"
            # 禁止允许首参 args / 第二参 path 别名的旧写法
            assert 'in {"ready", "args"}' not in hsrc
            assert 'in {"input_path", "path"}' not in hsrc

    # U2：_assert_exact_diagnostic 禁止 .code/.error/.args fallback 与 substring
    diag_fn = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_assert_exact_diagnostic"
        ),
        None,
    )
    assert diag_fn is not None
    diag_src = ast.get_source_segment(src, diag_fn) or ""
    assert 'hasattr(exc, "diagnostic_code")' in diag_src, (
        "业务红：U2 必须直接 hasattr(exc, \"diagnostic_code\")"
    )
    assert 'hasattr(exc, "message")' in diag_src, (
        "业务红：U2 必须直接 hasattr(exc, \"message\")"
    )
    # 去空格归一后单 membership，避免 assert BoolOp Or（j2 自守卫）
    assert "exc.message==" in diag_src.replace(" ", ""), (
        "业务红：U2 必须精确比较 exc.message == M1 文案"
    )
    assert "str(exc)==exc.message" in diag_src.replace(" ", ""), (
        "业务红：U2 必须断言 str(exc) == exc.message"
    )
    for banned_fb in (
        'getattr(exc, "code"',
        "getattr(exc, 'code'",
        'getattr(exc, "error"',
        "getattr(exc, 'error'",
        'getattr(exc, "args"',
        "getattr(exc, 'args'",
        "expected_msg in",
        " in blob",
    ):
        assert banned_fb not in diag_src, (
            f"业务红：U2 禁止 diagnostic fallback/substring：{banned_fb!r}"
        )

    # T5/S11：I4 禁止 patch CORE_RUN_ONE；输入创建早于 TEMP patch；禁 if raised 条件成功
    assert i4_fn is not None
    i4_src = ast.get_source_segment(src, i4_fn) or ""
    assert "setattr(core, CORE_RUN_ONE" not in i4_src.replace(" ", ""), (
        "业务红：I4 不得 patch CORE_RUN_ONE"
    )
    assert "monkeypatch.setattr(core, CORE_RUN_ONE" not in i4_src, (
        "业务红：I4 不得 monkeypatch CORE_RUN_ONE"
    )
    # 输入创建必须在 patch mkdtemp 之前（源码顺序：建 inputs 字典后才 setattr tracking_mkdtemp）
    idx_inputs = i4_src.find("inputs: dict[str, Path]")
    if idx_inputs < 0:
        idx_inputs = i4_src.find("inputs = {")
    idx_patch = i4_src.find('setattr(core.tempfile, "mkdtemp"')
    if idx_patch < 0:
        idx_patch = i4_src.find("setattr(core.tempfile, 'mkdtemp'")
    assert idx_inputs >= 0 and idx_patch >= 0 and idx_inputs < idx_patch, (
        f"业务红：I4 输入创建必须早于 TEMP mkdtemp patch "
        f"(inputs@{idx_inputs} patch@{idx_patch})"
    )
    assert "if not raised" not in i4_src and "if raised:" not in i4_src, (
        "业务红：I4 禁止 if raised / if not raised 包裹核心诊断"
    )
    assert "make_work_temp" not in i4_src and "mkdtemp_work" not in i4_src, (
        "业务红：I4 禁止枚举多 TEMP factory 名"
    )
    assert "len(raised) == 1" in i4_src, "业务红：I4 必须无条件 len(raised)==1"

    # T4：I3 禁止 if raised 条件诊断；必须 len(raised_*)==1
    i3_fn = next(
        (f for f in i2_i3_i4_funcs if f.name.startswith("test_i3_")),
        None,
    )
    assert i3_fn is not None
    i3_src = ast.get_source_segment(src, i3_fn) or ""
    assert "if raised_f" not in i3_src and "if raised_c" not in i3_src, (
        "业务红：I3 禁止 if raised_* 包裹核心诊断"
    )
    assert "len(raised_c) == 1" in i3_src
    assert "len(raised_f) == 1" in i3_src
    assert "len(raised_t) == 1" in i3_src
    assert "_assert_exact_diagnostic" in i3_src

    # U3：poll_waits 非空断言；fake poll 不推进 clock；cleanup wait 与 poll wait 分离
    assert "poll_waits" in i3_src, "业务红：U3 必须记录 poll_waits"
    assert "cleanup_waits" in i3_src, "业务红：U3 必须分离 cleanup_waits"
    assert "len(poll_waits) >= 1" in i3_src, (
        "业务红：U3 必须无条件要求 poll_waits 非空（len >= 1）"
    )
    # 去空格归一后单 membership，避免 assert BoolOp Or（j2 自守卫）
    assert "0.0<gap" in i3_src.replace(" ", ""), (
        "业务红：U3 每条 poll wait 必须 0 < interval"
    )
    # fake poll 体不得自行 mono[...] += / mono_x["t"] +=
    poll_advance_hits: list[int] = []
    for n in ast.walk(i3_fn):
        if not isinstance(n, ast.FunctionDef) or n.name != "poll":
            continue
        poll_src = ast.get_source_segment(src, n) or ""
        if '["t"] +=' in poll_src or "['t'] +=" in poll_src:
            poll_advance_hits.append(getattr(n, "lineno", -1))
        if "FILE_TIMEOUT_SEC" in poll_src and ("=" in poll_src):
            # 禁止在 poll 内直接写 mono 越过超时
            if "mono" in poll_src:
                poll_advance_hits.append(getattr(n, "lineno", -1))
    assert poll_advance_hits == [], (
        f"业务红：U3 fake poll 不得推进 monotonic/clock，行={poll_advance_hits}"
    )
