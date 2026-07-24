# -*- coding: utf-8 -*-
"""
模块：V1-K 静默启动诚实诊断专项测试（failure-first）
用途：在系统 TEMP 假仓 + 严格 listener/probe/process 快照 + PlanOnly/DiagnoseOnly
      下验证唯一真源、五入口薄委托、前置、归属、就绪、状态侧车与零副作用。
对接：tools/v1-ops/Start-Biaoshu-Dev.ps1、Diagnose-Biaoshu-Dev.bat、
      根 Start-Biaoshu-Dev.{bat,ps1}、Start-Biaoshu-UI.bat、
      backend/run-dev.bat、frontend/run-dev.bat；pytest/unittest；tempfile。
二次开发：禁止真实 Start-Process/Stop/taskkill/端口 bind/live HTTP/DB/uploads/
         浏览器/联网；禁止 skip/xfail、宽泛 or、固定 sleep、空循环、条件跳过；
         生产未改时必须因真源/委托/行为缺失形成真实业务红。
"""

from __future__ import annotations

import ast
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径锚定：本 worktree 仓库根（禁止依赖进程 cwd；禁止读写主仓真实数据）
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent

_TRUE_SOURCE = _HERE / "Start-Biaoshu-Dev.ps1"
_STOP_PS1 = _HERE / "Stop-Biaoshu-Dev.ps1"
_DIAGNOSE_BAT = _REPO_ROOT / "Diagnose-Biaoshu-Dev.bat"
_ROOT_START_BAT = _REPO_ROOT / "Start-Biaoshu-Dev.bat"
_ROOT_START_PS1 = _REPO_ROOT / "Start-Biaoshu-Dev.ps1"
_ROOT_UI_BAT = _REPO_ROOT / "Start-Biaoshu-UI.bat"
_BACKEND_RUN_BAT = _REPO_ROOT / "backend" / "run-dev.bat"
_FRONTEND_RUN_BAT = _REPO_ROOT / "frontend" / "run-dev.bat"

_BOM = b"\xef\xbb\xbf"
_STATUS_REL = Path("tmp") / "dev-start-status.json"

# 契约 §4 状态顶层精确七键
_STATUS_TOP_KEYS = (
    "schemaVersion",
    "updatedAtUtc",
    "mode",
    "component",
    "overall",
    "code",
    "services",
)
_SERVICE_KEYS = ("backend", "frontend")
_SERVICE_ENTRY_KEYS = ("state", "code")

_ALLOWED_OVERALL = frozenset({"ready", "already_running", "failed", "plan"})
_ALLOWED_MODE = frozenset({"start", "diagnose", "plan"})
_ALLOWED_COMPONENT = frozenset({"all", "backend", "frontend"})
_ALLOWED_SERVICE_STATE = frozenset(
    {
        "not_selected",
        "planned",
        "missing",
        "foreign",
        "not_ready",
        "ready",
        "already_running",
    }
)
# 契约至少覆盖的 code 枚举（顶层与服务层共用池）
_REQUIRED_CODES = frozenset(
    {
        "ready",
        "already_running",
        "venv_missing",
        "backend_entry_missing",
        "npm_missing",
        "frontend_package_missing",
        "frontend_deps_missing",
        "listener_unavailable",
        "backend_port_foreign",
        "frontend_port_foreign",
        "backend_not_ready",
        "frontend_not_ready",
        "snapshot_invalid",
        "status_write_failed",
    }
)
# 固定 allowed code 全集：契约 required codes + plan + not_selected；
# 禁止混入 planned/missing/foreign/not_ready（它们是 service state，不是 code）
_ALLOWED_CODES = frozenset(
    {
        *_REQUIRED_CODES,
        "plan",
        "not_selected",
    }
)

# status_write_failed 唯一固定可观测文本（无宽 or；侧车缺失时也只认此 code 名）
_STATUS_WRITE_FAILED_CODE = "status_write_failed"

# 原子覆盖：
# - 初次无终稿：同目录临时文件 + Move-Item 到终稿
# - 有终稿替换：冻结 [System.IO.File]::Replace(同目录临时, 终稿, [NullString]::Value)
#   （Windows PowerShell 5.1 无备份语义；禁止字面 $null / 双 Replace 异常回退）
# 禁止先 Remove-Item 终稿再 Move-Item（半状态窗口 / 反假绿）
_ATOMIC_MOVE_TRACE_MARKER = "V1K_ATOMIC_MOVE_TRACE="
_STATUS_DIRECT_WRITE_MARKER = "V1K_STATUS_DIRECT_WRITE="
_STATUS_FINAL_REMOVE_MARKER = "V1K_STATUS_FINAL_REMOVE="

# 受控 start 命令捕获
_START_PROCESS_CAPTURE_MARKER = "V1K_START_PROCESS_CAPTURE="

# 前端就绪仅允许 HTTP 200；契约未列 304，默认探测无 If-None-Match，304 必须非 ready
_FRONTEND_READY_HTTP = frozenset({200})
_FRONTEND_NOT_READY_HTTP = frozenset({201, 204, 301, 302, 304, 400, 404, 500, 502})

# V1-Q：禁止固定 Stop SHA（合法 production 修复不得被预填哈希阻挡）。
# Stop 完整性改为 UTF-8 BOM + 真实 powershell.exe 5.1 Parser.ParseFile；
# 行为由 test_biaoshu_backup.py 中 V1-Q Stop 红门证明。

# 真源冻结回环探测 URL（行为/注入主证据；常量自检仅辅助）
_EXPECTED_BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"
_EXPECTED_FRONTEND_PROBE_URL = "http://127.0.0.1:5173/create"

# Plan/Diagnose 副作用计数标记（PowerShell 作用域覆盖后回传）
_SIDE_EFFECT_MARKER = "V1K_SIDE_EFFECT_COUNTS="
_SIDE_EFFECT_FORBIDDEN_PREFIX = "V1K_SIDE_EFFECT_FORBIDDEN:"
_SIDE_EFFECT_KEYS = (
    "Start-Process",
    "Stop-Process",
    "Invoke-WebRequest",
    "Invoke-RestMethod",
    "taskkill",
    "taskkill.exe",
    "curl",
    "curl.exe",
    "wget",
    "wget.exe",
)

# 真源禁用旁路（静态辅助门：对去注释/去字符串文本做扫描；调用/点源/IO.File 另走 PS AST）
_FORBIDDEN_BYPASS_STATIC_PATTERNS = (
    r"(?i)\btaskkill\.exe\b",
    r"(?i)\bcmd(?:\.exe)?\s+/c\s+taskkill\b",
    r"(?i)\bcurl\.exe\b",
    r"(?i)\bwget\.exe\b",
    r"(?i)\[System\.Diagnostics\.Process\]::Start\b",
    r"(?i)\[Diagnostics\.Process\]::Start\b",
    r"(?i)\bSystem\.Net\.Http\.HttpClient\b",
    r"(?i)\bNew-Object\s+System\.Net\.Http\.HttpClient\b",
    r"(?i)\bSystem\.Net\.WebClient\b",
    r"(?i)\bNew-Object\s+System\.Net\.WebClient\b",
)

# 真源冻结探测 URL 变量名（PowerShell AST 赋值门；名与值一并冻结）
_FROZEN_PROBE_URL_ASSIGNMENTS = (
    ("BackendHealthUrl", _EXPECTED_BACKEND_HEALTH_URL),
    ("FrontendProbeUrl", _EXPECTED_FRONTEND_PROBE_URL),
)

# 受控 start 精确命令（主证据：Start-Process capture）
_EXPECTED_BACKEND_START_ARGS = (
    "-m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
)
_EXPECTED_FRONTEND_START_ARGS = "run dev -- --host 127.0.0.1 --port 5173"
_EXPECTED_WINDOW_STYLE = "Hidden"

# 真源禁止的 .NET 直接文件写成员。
# 注意：Replace 不是直写 API，属于有终稿时的合法原子替换，不得列入本禁门。
_FORBIDDEN_IO_FILE_WRITE_MEMBERS = frozenset(
    {
        "WriteAllText",
        "WriteAllBytes",
        "WriteAllLines",
        "AppendAllText",
        "AppendAllBytes",
        "AppendAllLines",
        "CreateText",
        "AppendText",
        "OpenWrite",
        "Create",
    }
)
# 合法有终稿原子替换成员（与禁门互斥）
_ALLOWED_IO_FILE_ATOMIC_REPLACE_MEMBER = "Replace"

# 假敏感标记：断言不得泄漏到状态 JSON / 诊断输出
_FAKE_PID_TOKEN = "PID_SHOULD_NOT_LEAK_424242"
_FAKE_PATH_TOKEN = r"C:\Users\Administrator\secret-v1k-path-DO-NOT-LEAK"
_FAKE_KEY_TOKEN = "sk-fake-v1k-test-key-DO-NOT-LEAK"
_FAKE_COOKIE_TOKEN = "biaoshu_session=fake-cookie-v1k-DO-NOT-LEAK"
_FAKE_CSRF_TOKEN = "csrf-fake-v1k-token-DO-NOT-LEAK"
_FAKE_CMDLINE_TOKEN = "uvicorn-secret-cmdline-marker-DO-NOT-LEAK"

# 状态/诊断禁止出现的敏感形态（子串/正则）
_SENSITIVE_SUBSTRINGS = (
    _FAKE_KEY_TOKEN,
    _FAKE_COOKIE_TOKEN,
    _FAKE_CSRF_TOKEN,
    _FAKE_PATH_TOKEN,
    _FAKE_PID_TOKEN,
    _FAKE_CMDLINE_TOKEN,
    "API_KEY",
    "Authorization",
    "Set-Cookie",
    "uploads\\",
    "uploads/",
    "biaoshu.db",
    "Exception:",
    "Traceback",
)

# 生产七脚本 + 本测试（边界核对用相对路径）
_PROD_SEVEN = (
    "tools/v1-ops/Start-Biaoshu-Dev.ps1",
    "Diagnose-Biaoshu-Dev.bat",
    "Start-Biaoshu-Dev.bat",
    "Start-Biaoshu-Dev.ps1",
    "Start-Biaoshu-UI.bat",
    "backend/run-dev.bat",
    "frontend/run-dev.bat",
)
_TEST_ONLY = "tools/v1-ops/test_start_biaoshu_dev.py"

# 旧入口第二套算法特征（静态门辅助；不能替代行为用例）
_LEGACY_SECOND_ALGO_MARKERS = (
    "cmd /k",
    "cmd.exe",
    "/k",
    "Read-Host",
    "Start-Process $Url",
    "Start-Process $url",
    "netstat",
    "Test-Port",
)

# Stop / host / 端口基线（未变证据：哈希对照，不靠业务源码扫描作主断言）
_EXPECTED_HOST = "127.0.0.1"
_EXPECTED_BACKEND_PORT = 8000
_EXPECTED_FRONTEND_PORT = 5173


# ===========================================================================
# 工具函数
# ===========================================================================


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _decode_ps_output(raw: bytes | str | None) -> str:
    """
    解码 PowerShell/cmd 管道输出。
    确定性顺序：utf-8-sig 严格 → locale/mbcs/gb18030/cp936 严格 → utf-8 replace。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    fallbacks: list[str] = []
    preferred = locale.getpreferredencoding(False)
    if preferred:
        fallbacks.append(preferred)
    if sys.platform == "win32":
        fallbacks.append("mbcs")
    fallbacks.extend(("gb18030", "cp936"))
    seen: set[str] = set()
    for enc in fallbacks:
        key = enc.lower()
        if not enc or key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _require_true_source() -> Path:
    """
    真源必须存在；缺失时抛 AssertionError，形成业务失败（禁止 skip）。
    """
    if not _TRUE_SOURCE.is_file():
        raise AssertionError(
            f"生产真源缺失（failure-first 业务红）：{_TRUE_SOURCE}"
        )
    return _TRUE_SOURCE


def _require_diagnose_bat() -> Path:
    if not _DIAGNOSE_BAT.is_file():
        raise AssertionError(
            f"Diagnose 入口缺失（failure-first 业务红）：{_DIAGNOSE_BAT}"
        )
    return _DIAGNOSE_BAT


def _require_five_entries() -> dict[str, Path]:
    files = {
        "root_start_bat": _ROOT_START_BAT,
        "root_start_ps1": _ROOT_START_PS1,
        "root_ui_bat": _ROOT_UI_BAT,
        "backend_run_bat": _BACKEND_RUN_BAT,
        "frontend_run_bat": _FRONTEND_RUN_BAT,
    }
    missing = [n for n, p in files.items() if not p.is_file()]
    if missing:
        raise AssertionError(
            "五既有入口缺失（failure-first 业务红）："
            + ", ".join(f"{n}={files[n]}" for n in missing)
        )
    return files


def _require_all_prod_for_behavior() -> dict[str, Path]:
    """行为用例前置：真源 + Diagnose + 五入口全部存在。"""
    out: dict[str, Path] = {
        "true_source": _require_true_source(),
        "diagnose_bat": _require_diagnose_bat(),
    }
    out.update(_require_five_entries())
    return out


def _read_text_with_bom_check(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig") if raw.startswith(_BOM) else raw.decode("utf-8", errors="replace")
    return raw, text


def _has_utf8_bom(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(3) == _BOM


def _run_powershell_file(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        *args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or script_path.parent),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=_decode_ps_output(proc.stdout),
        stderr=_decode_ps_output(proc.stderr),
    )


def _run_cmd_bat(
    bat_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
) -> subprocess.CompletedProcess[str]:
    # 使用 cmd /c 执行 bat 并透传参数；禁止 /k
    cmd = ["cmd.exe", "/d", "/c", str(bat_path), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or bat_path.parent),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=_decode_ps_output(proc.stdout),
        stderr=_decode_ps_output(proc.stderr),
    )


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _owned_backend_listener(repo: Path, pid: int = 610001) -> dict[str, Any]:
    py = repo / "backend" / ".venv" / "Scripts" / "python.exe"
    cmd = (
        f'"{py}" -m uvicorn app.main:app --reload '
        f"--host {_EXPECTED_HOST} --port {_EXPECTED_BACKEND_PORT}"
    )
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "pid": pid,
        "executablePath": str(py),
        "commandLine": cmd,
    }


def _owned_frontend_listener(repo: Path, pid: int = 610002) -> dict[str, Any]:
    node = r"C:\Program Files\nodejs\node.exe"
    vite = repo / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    cmd = f'node "{vite}" --host {_EXPECTED_HOST} --port {_EXPECTED_FRONTEND_PORT}'
    return {
        "port": _EXPECTED_FRONTEND_PORT,
        "pid": pid,
        "executablePath": node,
        "commandLine": cmd,
    }


def _foreign_backend_listener(pid: int = 515151) -> dict[str, Any]:
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "pid": pid,
        "executablePath": r"C:\Windows\System32\svchost.exe",
        "commandLine": "svchost -k netsvcs - foreign-backend-v1k",
    }


def _foreign_frontend_listener(pid: int = 515152) -> dict[str, Any]:
    return {
        "port": _EXPECTED_FRONTEND_PORT,
        "pid": pid,
        "executablePath": r"C:\Program Files\nodejs\node.exe",
        "commandLine": r"node C:\other-app\vite",
    }


def _probe_backend_ready() -> dict[str, Any]:
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "httpStatus": 200,
        "status": "ok",
        "dbOk": True,
    }


def _probe_backend_not_ready(
    *,
    http_status: int = 503,
    status: str = "degraded",
    db_ok: bool = False,
) -> dict[str, Any]:
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "httpStatus": http_status,
        "status": status,
        "dbOk": db_ok,
    }


def _probe_frontend_ready(http_status: int = 200) -> dict[str, Any]:
    return {
        "port": _EXPECTED_FRONTEND_PORT,
        "httpStatus": http_status,
    }


def _probe_frontend_not_ready(http_status: int = 502) -> dict[str, Any]:
    return {
        "port": _EXPECTED_FRONTEND_PORT,
        "httpStatus": http_status,
    }


def _process_record(
    pid: int,
    executable_path: str,
    command_line: str,
) -> dict[str, Any]:
    return {
        "pid": pid,
        "executablePath": executable_path,
        "commandLine": command_line,
    }


def _assert_no_sensitive(tc: unittest.TestCase, text: str, where: str) -> None:
    lower = text.lower()
    for tok in _SENSITIVE_SUBSTRINGS:
        tc.assertNotIn(
            tok.lower() if tok.isascii() else tok,
            lower if tok.isascii() else text,
            f"{where} 不得含敏感子串 {tok!r}",
        )
    # PID 形态：状态/诊断固定中文不得出现独立 pid 字段值泄漏
    tc.assertNotRegex(text, r"(?i)\bpid\s*[=:：]\s*\d+", f"{where} 不得含 PID 赋值形态")
    # 任意盘符绝对路径（不仅 C:\Users）
    tc.assertNotRegex(
        text,
        r"[A-Za-z]:\\",
        f"{where} 不得含盘符绝对路径",
    )
    # UNC 与扩展 UNC
    tc.assertNotRegex(
        text,
        r"\\\\[^\s\"']+",
        f"{where} 不得含 UNC 路径",
    )
    tc.assertNotRegex(
        text,
        r"\\\\\?\\",
        f"{where} 不得含扩展 UNC",
    )


def _assert_status_schema(
    tc: unittest.TestCase,
    status: dict[str, Any],
    *,
    mode: str,
    component: str,
) -> None:
    tc.assertIsInstance(status, dict)
    tc.assertEqual(
        sorted(status.keys()),
        sorted(_STATUS_TOP_KEYS),
        f"状态顶层必须精确七键，实际={list(status.keys())}",
    )
    tc.assertIsInstance(status["schemaVersion"], int)
    tc.assertNotIsInstance(status["schemaVersion"], bool)
    tc.assertEqual(status["schemaVersion"], 1)
    ts = status["updatedAtUtc"]
    tc.assertIsInstance(ts, str)
    # 严格 UTC Z：YYYY-MM-DDTHH:MM:SS[.fff]Z
    tc.assertRegex(
        ts,
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        f"updatedAtUtc 必须严格 UTC Z：{ts!r}",
    )
    # 可解析
    datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z") if "." not in ts else None
    if "." in ts:
        # 带小数秒
        base, frac = ts[:-1].split(".", 1)
        datetime.strptime(base + "+0000", "%Y-%m-%dT%H:%M:%S%z")
        tc.assertTrue(frac.isdigit(), f"小数秒必须为数字：{ts!r}")
    tc.assertEqual(status["mode"], mode)
    tc.assertIn(status["mode"], _ALLOWED_MODE)
    tc.assertEqual(status["component"], component)
    tc.assertIn(status["component"], _ALLOWED_COMPONENT)
    tc.assertIn(status["overall"], _ALLOWED_OVERALL)
    tc.assertIsInstance(status["code"], str)
    tc.assertTrue(bool(status["code"].strip()), "顶层 code 不得为空")
    tc.assertIn(
        status["code"],
        _ALLOWED_CODES,
        f"顶层 code 必须属于固定枚举：{status['code']!r}",
    )

    services = status["services"]
    tc.assertIsInstance(services, dict)
    tc.assertEqual(
        sorted(services.keys()),
        sorted(_SERVICE_KEYS),
        f"services 必须精确两键：{list(services.keys())}",
    )
    for sk in _SERVICE_KEYS:
        entry = services[sk]
        tc.assertIsInstance(entry, dict, f"services.{sk} 必须为对象")
        tc.assertEqual(
            sorted(entry.keys()),
            sorted(_SERVICE_ENTRY_KEYS),
            f"services.{sk} 必须精确两键 state/code：{list(entry.keys())}",
        )
        tc.assertIn(entry["state"], _ALLOWED_SERVICE_STATE)
        tc.assertIsInstance(entry["code"], str)
        tc.assertTrue(bool(str(entry["code"]).strip()), f"services.{sk}.code 不得为空")
        tc.assertIn(
            entry["code"],
            _ALLOWED_CODES,
            f"services.{sk}.code 必须属于固定枚举：{entry['code']!r}",
        )


def _strip_side_effect_marker_lines(combined: str) -> str:
    """
    剥离全部 V1K_SIDE_EFFECT_COUNTS= marker 行，以及包装器自身
    V1K_SIDE_EFFECT_FORBIDDEN: 文案行，供辅助 token 扫描。
    主证据仍依赖 counts 解析，不得删除 counts。
    """
    kept: list[str] = []
    for line in (combined or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(_SIDE_EFFECT_MARKER):
            continue
        if _SIDE_EFFECT_FORBIDDEN_PREFIX in stripped:
            continue
        kept.append(line)
    return "\n".join(kept)


def _assert_zero_side_effect_tokens(tc: unittest.TestCase, combined: str) -> None:
    """
    stdout/stderr 子串扫描仅为辅助；精确零调用见副作用作用域覆盖。
    必须先剥离全部 V1K_SIDE_EFFECT_COUNTS= marker 行，再扫描剩余输出，
    避免 wrapper 计数 JSON 键与禁扫 token 自相矛盾。
    """
    residual = _strip_side_effect_marker_lines(combined)
    lowered = residual.lower()
    forbidden = (
        "start-process",
        "taskkill",
        "stop-process",
        "stop-biaoshu",
        "invoke-webrequest",
        "invoke-restmethod",
        "curl ",
        "wget ",
        "start http",
        "cmd /k",
        "read-host",
        "pause",
    )
    for tok in forbidden:
        tc.assertNotIn(tok, lowered, f"Plan/Diagnose 不得出现副作用标记 {tok!r}")


def _parse_side_effect_counts(combined: str) -> dict[str, int]:
    """从包装器 stdout 解析副作用计数；缺失视为包装失败。"""
    for line in (combined or "").splitlines():
        line = line.strip()
        if not line.startswith(_SIDE_EFFECT_MARKER):
            continue
        raw = line[len(_SIDE_EFFECT_MARKER) :].strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise AssertionError(f"副作用计数必须为对象：{raw!r}")
        out: dict[str, int] = {}
        for key in _SIDE_EFFECT_KEYS:
            if key not in data:
                raise AssertionError(f"副作用计数缺键 {key!r}：{data!r}")
            val = data[key]
            if not isinstance(val, int) or isinstance(val, bool):
                raise AssertionError(f"副作用计数 {key!r} 必须为 int：{val!r}")
            out[key] = val
        return out
    raise AssertionError(
        f"未找到副作用计数标记 {_SIDE_EFFECT_MARKER!r}；包装器未生效。输出片段：{combined[:400]!r}"
    )


def _assert_zero_side_effect_counts(
    tc: unittest.TestCase,
    counts: dict[str, int],
) -> None:
    for key in _SIDE_EFFECT_KEYS:
        tc.assertIn(key, counts)
        tc.assertEqual(
            counts[key],
            0,
            f"Plan/Diagnose 精确零调用失败：{key}={counts[key]} 全量={counts}",
        )


def _ps_arg_list_literal(args: list[str]) -> str:
    arg_literals: list[str] = []
    for a in args:
        arg_literals.append("'" + str(a).replace("'", "''") + "'")
    return "@(" + ",".join(arg_literals) + ")" if arg_literals else "@()"


def _ps_side_effect_counter_bootstrap() -> str:
    """
    构造 PowerShell 副作用计数器与可覆盖命令函数。
    含 taskkill.exe/curl.exe/wget.exe 等可命令覆盖旁路；
    调用时递增并抛错，避免计数=0 假绿。
    """
    keys_ps = "; ".join(f"'{k}' = 0" for k in _SIDE_EFFECT_KEYS)
    parts = [
        "$ErrorActionPreference = 'Continue'; ",
        f"$script:__V1K_SC = @{{ {keys_ps} }}; ",
    ]
    # cmdlet 名覆盖
    for name in (
        "Start-Process",
        "Stop-Process",
        "Invoke-WebRequest",
        "Invoke-RestMethod",
    ):
        parts.append(
            f"function global:{name} {{ "
            f"param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
            f"$script:__V1K_SC['{name}']++; "
            f"throw '{_SIDE_EFFECT_FORBIDDEN_PREFIX}{name}' "
            f"}}; "
        )
    # 可调用外部名覆盖（函数优先于 Application）
    for name in ("taskkill", "taskkill.exe", "curl", "curl.exe", "wget", "wget.exe"):
        parts.append(
            f"function global:{name} {{ "
            f"param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
            f"$script:__V1K_SC['{name}']++; "
            f"throw '{_SIDE_EFFECT_FORBIDDEN_PREFIX}{name}' "
            f"}}; "
        )
    return "".join(parts)


def _run_ps1_with_side_effect_guard(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
    """
    PowerShell 作用域覆盖 Start-Process/Stop-Process/Invoke-WebRequest/
    Invoke-RestMethod 与可调用 taskkill/curl/wget（含 .exe）：调用时递增并抛错。
    仅用于 PlanOnly/DiagnoseOnly 精确零调用；不得用于生产 start 失败先测路径。
    """
    if not script_path.is_file():
        raise AssertionError(f"待包装脚本不存在：{script_path}")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    command = (
        _ps_side_effect_counter_bootstrap()
        + f"$__v1k_args = {args_ps}; "
        + f"& '{ps1_lit}' @__v1k_args; "
        + "$__v1k_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }; "
        + "$__v1k_json = ($script:__V1K_SC | ConvertTo-Json -Compress); "
        + f"Write-Output ('{_SIDE_EFFECT_MARKER}' + $__v1k_json); "
        + "exit $__v1k_code"
    )
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    raw = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=str(cwd or script_path.parent),
        capture_output=True,
        timeout=timeout,
        check=False,
        env=run_env,
    )
    proc = subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode,
        stdout=_decode_ps_output(raw.stdout),
        stderr=_decode_ps_output(raw.stderr),
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    counts = _parse_side_effect_counts(combined)
    return proc, counts


def _ps_strip_comments_and_strings(text: str) -> str:
    """
    粗粒度剥离 PowerShell 注释与字符串字面量，供静态旁路门辅助扫描。
    不依赖注释/字符串中的假 token 即可让计数=0 假绿。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # 块注释
        if ch == "<" and i + 1 < n and text[i + 1] == "#":
            end = text.find("#>", i + 2)
            if end < 0:
                break
            i = end + 2
            out.append(" ")
            continue
        # 行注释
        if ch == "#":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        # 双引号字符串
        if ch == '"':
            i += 1
            while i < n:
                if text[i] == "`" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        # 单引号字符串
        if ch == "'":
            i += 1
            while i < n:
                if text[i] == "'" and i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                if text[i] == "'":
                    i += 1
                    break
                i += 1
            out.append("''")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _ps_ast_inspect(source_text: str) -> dict[str, Any]:
    """
    用 PowerShell Language.Parser AST 结构化检查真源：
    - assignments: 字面量字符串赋值 [{left, value, rightText}]
    - invocation_ops: 调用/点源运算符 [{operator, text}]
    - io_file_writes: IO.File/System.IO.File 直接写成员 [{member, expression, text}]
    - io_file_replaces: IO.File.Replace 调用及参数角色
      [{member, expression, arguments, text}]
    不依赖去字符串后的子串扫描；注释与孤立死字符串不会生成目标赋值 AST。
    """
    members_ps = ",".join("'" + m.replace("'", "''") + "'" for m in sorted(_FORBIDDEN_IO_FILE_WRITE_MEMBERS))
    replace_member = _ALLOWED_IO_FILE_ATOMIC_REPLACE_MEMBER.replace("'", "''")
    ps_body = (
        "param([Parameter(Mandatory=$true)][string]$SourcePath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$src = Get-Content -LiteralPath $SourcePath -Raw -Encoding UTF8\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [System.Management.Automation.Language.Parser]::ParseInput("
        "$src, [ref]$tokens, [ref]$errors)\n"
        "$assignments = New-Object System.Collections.Generic.List[object]\n"
        "$invops = New-Object System.Collections.Generic.List[object]\n"
        "$writes = New-Object System.Collections.Generic.List[object]\n"
        "$replaces = New-Object System.Collections.Generic.List[object]\n"
        "$forbidMembers = @(" + members_ps + ")\n"
        f"$replaceMember = '{replace_member}'\n"
        "function script:__V1K_IsIoFile([string]$exprText) {\n"
        "  $norm = ($exprText -replace '\\s','').ToLowerInvariant()\n"
        "  return ($norm -eq '[io.file]') -or ($norm -eq '[system.io.file]') "
        "-or ($norm -eq 'system.io.file') -or ($norm -eq 'io.file')\n"
        "}\n"
        "foreach ($a in $ast.FindAll({\n"
        "  param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst]\n"
        "}, $true)) {\n"
        "  $val = $null\n"
        "  if ($a.Right -is [System.Management.Automation.Language.CommandExpressionAst]) {\n"
        "    $expr = $a.Right.Expression\n"
        "    if ($expr -is [System.Management.Automation.Language.StringConstantExpressionAst]) {\n"
        "      $val = [string]$expr.Value\n"
        "    }\n"
        "  }\n"
        "  $assignments.Add(@{\n"
        "    left = [string]$a.Left.Extent.Text\n"
        "    value = $val\n"
        "    rightText = [string]$a.Right.Extent.Text\n"
        "  }) | Out-Null\n"
        "}\n"
        "foreach ($c in $ast.FindAll({\n"
        "  param($n) $n -is [System.Management.Automation.Language.CommandAst]\n"
        "}, $true)) {\n"
        "  $op = [string]$c.InvocationOperator\n"
        "  if ($op -eq 'Ampersand' -or $op -eq 'Dot') {\n"
        "    $invops.Add(@{ operator = $op; text = [string]$c.Extent.Text }) | Out-Null\n"
        "  }\n"
        "}\n"
        "foreach ($m in $ast.FindAll({\n"
        "  param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst]\n"
        "}, $true)) {\n"
        "  $member = [string]$m.Member.Extent.Text\n"
        "  $exprText = [string]$m.Expression.Extent.Text\n"
        "  if (-not (script:__V1K_IsIoFile $exprText)) { continue }\n"
        "  if ($member -eq $replaceMember) {\n"
        "    $argTexts = New-Object System.Collections.Generic.List[string]\n"
        "    foreach ($arg in @($m.Arguments)) {\n"
        "      $argTexts.Add([string]$arg.Extent.Text) | Out-Null\n"
        "    }\n"
        "    $replaces.Add(@{\n"
        "      member = $member\n"
        "      expression = $exprText\n"
        "      arguments = [object[]]$argTexts.ToArray()\n"
        "      text = [string]$m.Extent.Text\n"
        "    }) | Out-Null\n"
        "    continue\n"
        "  }\n"
        "  if ($forbidMembers -notcontains $member) { continue }\n"
        "  $writes.Add(@{\n"
        "    member = $member\n"
        "    expression = $exprText\n"
        "    text = [string]$m.Extent.Text\n"
        "  }) | Out-Null\n"
        "}\n"
        # 注意：Generic.List 不可用 @($list) 塞进 hashtable（会 ArgumentException 参数类型不匹配）
        "$errCount = 0\n"
        "if ($null -ne $errors) { $errCount = @($errors).Count }\n"
        "$out = [pscustomobject]@{\n"
        "  parseErrorCount = $errCount\n"
        "  assignments = [object[]]$assignments.ToArray()\n"
        "  invocation_ops = [object[]]$invops.ToArray()\n"
        "  io_file_writes = [object[]]$writes.ToArray()\n"
        "  io_file_replaces = [object[]]$replaces.ToArray()\n"
        "}\n"
        "ConvertTo-Json -InputObject $out -Compress -Depth 8\n"
    )
    with tempfile.TemporaryDirectory(prefix="v1k-ps-ast-") as td:
        src_path = Path(td) / "source.ps1"
        probe_path = Path(td) / "probe.ps1"
        src_path.write_text(source_text, encoding="utf-8")
        probe_path.write_text(ps_body, encoding="utf-8")
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(probe_path),
                "-SourcePath",
                str(src_path),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        out = _decode_ps_output(raw.stdout).strip()
        err = _decode_ps_output(raw.stderr).strip()
        if raw.returncode != 0 or not out:
            raise AssertionError(
                f"PowerShell AST 解析失败 code={raw.returncode} err={err!r} out={out[:300]!r}"
            )
        data = json.loads(out)
        if not isinstance(data, dict):
            raise AssertionError(f"AST 检查结果必须为对象：{data!r}")
        # 规范化列表字段
        for key in (
            "assignments",
            "invocation_ops",
            "io_file_writes",
            "io_file_replaces",
        ):
            val = data.get(key, [])
            if val is None:
                data[key] = []
            elif not isinstance(val, list):
                data[key] = [val]
        return data


def _assert_ps_ast_frozen_probe_url_assignments(
    tc: unittest.TestCase,
    source_text: str,
) -> None:
    """
    PowerShell AST：真源必须存在固定变量赋值
    $BackendHealthUrl / $FrontendProbeUrl，值精确等于冻结 URL。
    排除注释与其它变量中的孤立死字符串。
    """
    facts = _ps_ast_inspect(source_text)
    found: dict[str, str] = {}
    for item in facts.get("assignments", []):
        if not isinstance(item, dict):
            continue
        left = str(item.get("left") or "").strip()
        if left.startswith("$"):
            left = left[1:]
        val = item.get("value")
        if not isinstance(val, str):
            continue
        if left in {"BackendHealthUrl", "FrontendProbeUrl"}:
            found[left] = val
    for name, expected in _FROZEN_PROBE_URL_ASSIGNMENTS:
        tc.assertIn(
            name,
            found,
            f"真源 AST 必须含字面量赋值 ${name}；实际 assignments={facts.get('assignments')!r}",
        )
        tc.assertEqual(
            found[name],
            expected,
            f"${name} 必须精确等于 {expected!r}，实际={found[name]!r}",
        )


def _assert_ps_ast_no_invocation_operators(
    tc: unittest.TestCase,
    source_text: str,
) -> None:
    """
    V1-K 真源禁止全部调用运算符(&)与点源运算符(.)；
    外部启动只能走 Start-Process。
    """
    facts = _ps_ast_inspect(source_text)
    ops = facts.get("invocation_ops") or []
    tc.assertEqual(
        ops,
        [],
        f"真源不得使用调用/点源运算符（& / .）：{ops!r}",
    )


def _assert_ps_ast_no_io_file_direct_writes(
    tc: unittest.TestCase,
    source_text: str,
) -> None:
    """真源 AST 拒绝 IO.File / System.IO.File 直接写 API。"""
    facts = _ps_ast_inspect(source_text)
    writes = facts.get("io_file_writes") or []
    tc.assertEqual(
        writes,
        [],
        f"真源不得使用 IO.File/System.IO.File 直接写 API：{writes!r}",
    )


# Write-StatusSidecar 冻结变量：有终稿 File.Replace 参数角色必须精确对齐
# 第三参面向 Windows PowerShell 5.1：必须 [NullString]::Value（无备份语义），
# 禁止字面 $null（5.1 会绑成空串并抛“路径格式不正确”）及双 Replace 异常回退。
_FILE_REPLACE_STATUS_SRC_ARG = "$tempPath"
_FILE_REPLACE_STATUS_DST_ARG = "$StatusFinal"
_FILE_REPLACE_STATUS_BAK_ARG = "[NullString]::Value"
# 字面 $null 仅作 wrong-backup 反例标识（不得充当合法第三参）
_FILE_REPLACE_STATUS_WRONG_BAK_NULL = "$null"


def _normalize_ps_arg_text(text: str) -> str:
    """参数文本规范化：去首尾空白并折叠内部空白，便于精确角色比对。"""
    return re.sub(r"\s+", "", str(text).strip())


def _assert_ps_ast_file_replace_for_status(
    tc: unittest.TestCase,
    source_text: str,
) -> None:
    """
    PowerShell AST：有终稿时 File.Replace 总调用数必须精确为 1，
    且这一处参数角色必须精确对齐：
      0 = $tempPath
      1 = $StatusFinal
      2 = [NullString]::Value（无备份；规范化后精确匹配）
    禁止字面 $null 第三参、双 Replace（$null + NullString）、注释/死字符串/
    去字符串子串/无关三参/第四参充当主证据；Replace 不得被误判为直写禁门。
    """
    facts = _ps_ast_inspect(source_text)
    # 先证明 Replace 未被误伤为直写
    writes_raw = facts.get("io_file_writes")
    if writes_raw is None:
        writes: list[Any] = []
    elif isinstance(writes_raw, list):
        writes = writes_raw
    else:
        writes = [writes_raw]
    for w in writes:
        if not isinstance(w, dict):
            continue
        member_raw = w.get("member")
        if member_raw is None:
            member_text = ""
        else:
            member_text = str(member_raw)
        tc.assertNotEqual(
            member_text,
            _ALLOWED_IO_FILE_ATOMIC_REPLACE_MEMBER,
            f"File.Replace 不得被记入 io_file_writes 直写禁门：{w!r}",
        )
    replaces_raw = facts.get("io_file_replaces")
    if replaces_raw is None:
        replaces: list[Any] = []
    elif isinstance(replaces_raw, list):
        replaces = replaces_raw
    else:
        replaces = [replaces_raw]
    replace_count = len(replaces)
    # 总调用数精确为 1：禁止双 Replace（$null + NullString 异常回退）条件放行
    tc.assertEqual(
        replace_count,
        1,
        "真源 AST 必须精确一处 [System.IO.File]::Replace"
        f"（$tempPath, $StatusFinal, [NullString]::Value）；"
        f"当前总数={replace_count} io_file_replaces={replaces!r}",
    )
    legal: list[Any] = []
    for item in replaces:
        if not isinstance(item, dict):
            continue
        member_raw = item.get("member")
        if member_raw is None:
            member = ""
        else:
            member = str(member_raw).strip()
        if member != _ALLOWED_IO_FILE_ATOMIC_REPLACE_MEMBER:
            continue
        expr_raw = item.get("expression")
        if expr_raw is None:
            expr = ""
        else:
            expr = str(expr_raw)
        norm = re.sub(r"\s+", "", expr).lower()
        if norm not in {"[io.file]", "[system.io.file]", "system.io.file", "io.file"}:
            continue
        args_raw = item.get("arguments")
        if args_raw is None:
            args = []
        elif isinstance(args_raw, list):
            args = args_raw
        else:
            args = [args_raw]
        arg_texts = [str(a) for a in args]
        # 参数数必须精确为 3；第四参及以上不得充当合法证据
        if len(arg_texts) != 3:
            continue
        src_arg = _normalize_ps_arg_text(arg_texts[0])
        dst_arg = _normalize_ps_arg_text(arg_texts[1])
        bak_arg = _normalize_ps_arg_text(arg_texts[2])
        # PowerShell 变量名/类型加速器大小写不敏感；角色名必须精确对齐冻结表达式
        if (
            src_arg.casefold() == _FILE_REPLACE_STATUS_SRC_ARG.casefold()
            and dst_arg.casefold() == _FILE_REPLACE_STATUS_DST_ARG.casefold()
            and bak_arg.casefold() == _FILE_REPLACE_STATUS_BAK_ARG.casefold()
        ):
            legal.append(item)
    tc.assertEqual(
        len(legal),
        1,
        "必须精确一个参数角色合法的 File.Replace"
        f"（$tempPath, $StatusFinal, [NullString]::Value；参数数==3）："
        f"legal={legal!r} all={replaces!r}",
    )


def _assert_true_source_no_bypass_static(tc: unittest.TestCase, text: str) -> None:
    """
    真源辅助静态门：
    1) 去注释/去字符串后拒绝 Process::Start/HttpClient/WebClient/taskkill.exe 等；
    2) PowerShell AST 拒绝全部调用/点源运算符；
    3) PowerShell AST 拒绝 IO.File 直接写。
    """
    cleaned = _ps_strip_comments_and_strings(text)
    for pat in _FORBIDDEN_BYPASS_STATIC_PATTERNS:
        tc.assertIsNone(
            re.search(pat, cleaned),
            f"真源禁用旁路静态门命中 {pat!r}（已忽略注释/字符串）",
        )
    _assert_ps_ast_no_invocation_operators(tc, text)
    _assert_ps_ast_no_io_file_direct_writes(tc, text)


def _parse_json_marker_line(combined: str, marker: str) -> Any:
    for line in (combined or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(marker):
            continue
        raw = stripped[len(marker) :].strip()
        return json.loads(raw)
    raise AssertionError(f"未找到标记 {marker!r}；输出片段：{(combined or '')[:500]!r}")


def _run_ps1_with_atomic_move_trace(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
    final_status_rel: str = "tmp/dev-start-status.json",
) -> tuple[
    subprocess.CompletedProcess[str],
    dict[str, int],
    list[dict[str, str]],
    list[str],
    list[str],
]:
    """
    PlanOnly 专用：在副作用护栏之上
    - trace Move-Item（module-qualified 真 cmdlet；初建终稿仍合法）
    - 拦截对终稿路径的 Set-Content/Out-File/Add-Content/Tee-Object 直接写入
    - 受控捕获 Remove-Item：若目标为终稿 status 则记录并抛错（反假绿）；
      临时 .wip 清理仍允许并委托真实 cmdlet
    有终稿合法方案见 AST File.Replace 门（非本 wrapper 可 hook 的 .NET 静态方法）。
    返回 (proc, side_effect_counts, move_traces, direct_write_paths, final_remove_paths)。
    """
    if not script_path.is_file():
        raise AssertionError(f"待包装脚本不存在：{script_path}")
    joined = " ".join(args).lower()
    if "-planonly" not in joined and "-diagnoseonly" not in joined:
        raise AssertionError("原子覆盖 trace 仅允许 PlanOnly/DiagnoseOnly")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    final_lit = final_status_rel.replace("'", "''").replace("\\", "/")
    command = (
        _ps_side_effect_counter_bootstrap()
        + "$script:__V1K_MOVES = New-Object System.Collections.Generic.List[object]; "
        + "$script:__V1K_DIRECT = New-Object System.Collections.Generic.List[string]; "
        # 使用 global 列表：global:Remove-Item 内 $script: 会指向被测脚本作用域，
        # 生产未同步 __V1K_REMOVES 时仍须可靠回传终稿删除证据。
        + "$global:__V1K_FINAL_REMOVES = New-Object System.Collections.Generic.List[string]; "
        + f"$script:__V1K_FINAL_REL = '{final_lit}'; "
        + "function script:__V1K_IsFinalStatusPath([string]$p) { "
        + "  if ([string]::IsNullOrWhiteSpace($p)) { return $false } "
        + "  $norm = ($p -replace '\\\\','/').ToLowerInvariant(); "
        + "  $leaf = [System.IO.Path]::GetFileName($norm); "
        + "  if ($leaf -match '\\.wip$') { return $false } "
        + "  $final = ($script:__V1K_FINAL_REL -replace '\\\\','/').ToLowerInvariant(); "
        + "  return ($norm.EndsWith('/' + $final) -or $norm.EndsWith($final) -or $norm -eq $final "
        + "    -or $leaf -eq [System.IO.Path]::GetFileName($final)) "
        + "}; "
        + "function global:Move-Item { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true,Position=0)]$Path,"
        + "[Parameter(Position=1)]$Destination,"
        + "[switch]$Force,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "begin { $items = @() } "
        + "process { if ($null -ne $Path) { $items += $Path } } "
        + "end { "
        + "  foreach ($src in $items) { "
        + "    $srcFull = [string](Resolve-Path -LiteralPath $src -ErrorAction SilentlyContinue); "
        + "    if (-not $srcFull) { $srcFull = [string]$src } "
        + "    $dstFull = [string]$Destination; "
        + "    try { "
        + "      $dstParent = Split-Path -Parent $dstFull; "
        + "      if ($dstParent -and (Test-Path -LiteralPath $dstParent)) { "
        + "        $dstFull = [string](Join-Path (Resolve-Path -LiteralPath $dstParent) (Split-Path -Leaf $dstFull)) "
        + "      } "
        + "    } catch {} "
        + "    $script:__V1K_MOVES.Add(@{ source = $srcFull; destination = $dstFull }) | Out-Null; "
        + "    $miArgs = @{ LiteralPath = $src; Destination = $Destination }; "
        + "    if ($Force) { $miArgs['Force'] = $true } "
        + "    Microsoft.PowerShell.Management\\Move-Item @miArgs "
        + "  } "
        + "} "
        + "}; "
        + "function global:Remove-Item { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true,Position=0)]$Path,"
        + "$LiteralPath,"
        + "[switch]$Force,"
        + "[switch]$Recurse,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "begin { $items = @() } "
        + "process { "
        + "  if ($null -ne $LiteralPath) { $items += $LiteralPath } "
        + "  elseif ($null -ne $Path) { $items += $Path } "
        + "} "
        + "end { "
        + "  if ($items.Count -eq 0 -and $null -ne $LiteralPath) { $items = @($LiteralPath) } "
        + "  if ($items.Count -eq 0 -and $null -ne $Path) { $items = @($Path) } "
        + "  foreach ($target in $items) { "
        + "    $t = [string]$target; "
        + "    if (script:__V1K_IsFinalStatusPath $t) { "
        + "      $global:__V1K_FINAL_REMOVES.Add($t) | Out-Null; "
        + "      throw 'V1K_STATUS_FINAL_REMOVE_FORBIDDEN' "
        + "    } "
        + "    $ri = @{ }; "
        + "    if ($PSBoundParameters.ContainsKey('LiteralPath') -and $null -ne $LiteralPath) { "
        + "      $ri['LiteralPath'] = $target "
        + "    } else { "
        + "      $ri['Path'] = $target "
        + "    } "
        + "    if ($Force) { $ri['Force'] = $true } "
        + "    if ($Recurse) { $ri['Recurse'] = $true } "
        + "    Microsoft.PowerShell.Management\\Remove-Item @ri "
        + "  } "
        + "} "
        + "}; "
        + "function global:Set-Content { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true)]$Value,"
        + "$Path,$LiteralPath,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "  $target = if ($LiteralPath) { [string]$LiteralPath } else { [string]$Path }; "
        + "  if (script:__V1K_IsFinalStatusPath $target) { "
        + "    $script:__V1K_DIRECT.Add($target) | Out-Null; "
        + "    throw 'V1K_STATUS_DIRECT_WRITE_FORBIDDEN' "
        + "  } "
        + "  $sc = @{ }; "
        + "  if ($LiteralPath) { $sc['LiteralPath'] = $LiteralPath } "
        + "  elseif ($Path) { $sc['Path'] = $Path } "
        + "  if ($null -ne $Value) { $sc['Value'] = $Value } "
        + "  Microsoft.PowerShell.Management\\Set-Content @sc "
        + "}; "
        + "function global:Out-File { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true)]$InputObject,"
        + "$FilePath,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "  $target = [string]$FilePath; "
        + "  if (script:__V1K_IsFinalStatusPath $target) { "
        + "    $script:__V1K_DIRECT.Add($target) | Out-Null; "
        + "    throw 'V1K_STATUS_DIRECT_WRITE_FORBIDDEN' "
        + "  } "
        + "  $input | Microsoft.PowerShell.Utility\\Out-File -FilePath $FilePath "
        + "}; "
        + "function global:Add-Content { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true)]$Value,"
        + "$Path,$LiteralPath,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "  $target = if ($LiteralPath) { [string]$LiteralPath } else { [string]$Path }; "
        + "  if (script:__V1K_IsFinalStatusPath $target) { "
        + "    $script:__V1K_DIRECT.Add($target) | Out-Null; "
        + "    throw 'V1K_STATUS_DIRECT_WRITE_FORBIDDEN' "
        + "  } "
        + "  $ac = @{ }; "
        + "  if ($LiteralPath) { $ac['LiteralPath'] = $LiteralPath } "
        + "  elseif ($Path) { $ac['Path'] = $Path } "
        + "  if ($null -ne $Value) { $ac['Value'] = $Value } "
        + "  Microsoft.PowerShell.Management\\Add-Content @ac "
        + "}; "
        + "function global:Tee-Object { "
        + "param("
        + "[Parameter(ValueFromPipeline=$true)]$InputObject,"
        + "$FilePath,"
        + "[Parameter(ValueFromRemainingArguments=$true)]$Rest"
        + ") "
        + "  begin { $buf = New-Object System.Collections.Generic.List[object] } "
        + "  process { if ($null -ne $InputObject) { $buf.Add($InputObject) | Out-Null } } "
        + "  end { "
        + "    if (-not [string]::IsNullOrWhiteSpace([string]$FilePath)) { "
        + "      if (script:__V1K_IsFinalStatusPath ([string]$FilePath)) { "
        + "        $script:__V1K_DIRECT.Add([string]$FilePath) | Out-Null; "
        + "        throw 'V1K_STATUS_DIRECT_WRITE_FORBIDDEN' "
        + "      } "
        + "      $buf | Microsoft.PowerShell.Utility\\Tee-Object -FilePath $FilePath "
        + "    } else { "
        + "      $buf | Microsoft.PowerShell.Utility\\Tee-Object @Rest "
        + "    } "
        + "  } "
        + "}; "
        + f"$__v1k_args = {args_ps}; "
        + f"& '{ps1_lit}' @__v1k_args; "
        + "$__v1k_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }; "
        + f"Write-Output ('{_SIDE_EFFECT_MARKER}' + ($script:__V1K_SC | ConvertTo-Json -Compress)); "
        + f"Write-Output ('{_ATOMIC_MOVE_TRACE_MARKER}' + (ConvertTo-Json -Compress @($script:__V1K_MOVES.ToArray()))); "
        + f"Write-Output ('{_STATUS_DIRECT_WRITE_MARKER}' + (ConvertTo-Json -Compress @($script:__V1K_DIRECT.ToArray()))); "
        + f"Write-Output ('{_STATUS_FINAL_REMOVE_MARKER}' + (ConvertTo-Json -Compress @($global:__V1K_FINAL_REMOVES.ToArray()))); "
        + "exit $__v1k_code"
    )
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    raw = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=str(cwd or script_path.parent),
        capture_output=True,
        timeout=timeout,
        check=False,
        env=run_env,
    )
    proc = subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode,
        stdout=_decode_ps_output(raw.stdout),
        stderr=_decode_ps_output(raw.stderr),
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    counts = _parse_side_effect_counts(combined)
    moves_raw = _parse_json_marker_line(combined, _ATOMIC_MOVE_TRACE_MARKER)
    if not isinstance(moves_raw, list):
        raise AssertionError(f"Move-Item trace 必须为数组：{moves_raw!r}")
    moves: list[dict[str, str]] = []
    for item in moves_raw:
        if not isinstance(item, dict):
            raise AssertionError(f"Move-Item trace 项必须为对象：{item!r}")
        moves.append(
            {
                "source": str(item.get("source", "")),
                "destination": str(item.get("destination", "")),
            }
        )
    direct_raw = _parse_json_marker_line(combined, _STATUS_DIRECT_WRITE_MARKER)
    if not isinstance(direct_raw, list):
        raise AssertionError(f"direct-write trace 必须为数组：{direct_raw!r}")
    directs = [str(x) for x in direct_raw]
    removes_raw = _parse_json_marker_line(combined, _STATUS_FINAL_REMOVE_MARKER)
    if not isinstance(removes_raw, list):
        raise AssertionError(f"final-remove trace 必须为数组：{removes_raw!r}")
    final_removes = [str(x) for x in removes_raw]
    return proc, counts, moves, directs, final_removes


def _run_ps1_with_start_process_capture(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], dict[str, int]]:
    """
    受控 start-command capture：
    - 组合既有副作用计数 bootstrap；
    - Start-Process 使用专用 capture 覆盖（只记录不派生，首次后抛错）；
    - 其余 Stop-Process/taskkill/IWR/IRM/curl/wget 全部 trace 后抛错；
    - 端口/进程枚举注入为空，禁止真实枚举。
    返回 (proc, captures, side_effect_counts)。
    """
    if not script_path.is_file():
        raise AssertionError(f"待包装脚本不存在：{script_path}")
    joined = " ".join(args).lower()
    if "-planonly" in joined or "-diagnoseonly" in joined:
        raise AssertionError("start-command capture 仅用于生产 start 路径")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    command = (
        # 先装副作用计数护栏（含 Start-Process 抛错版）
        _ps_side_effect_counter_bootstrap()
        + "$script:__V1K_SP = New-Object System.Collections.Generic.List[object]; "
        # 端口/进程枚举全部注入为空，禁止真实枚举
        "function global:Get-NetTCPConnection { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "@() "
        "}; "
        "function global:Get-NetUDPEndpoint { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "@() "
        "}; "
        "function global:Get-Process { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "@() "
        "}; "
        "function global:Get-CimInstance { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "@() "
        "}; "
        "function global:Get-WmiObject { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "@() "
        "}; "
        "function global:netstat { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "'' "
        "}; "
        "function global:netstat.exe { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
        "'' "
        "}; "
        # 覆盖 Start-Process：计数 + 专用 capture + 抛错，零真实派生
        "function global:Start-Process { "
        "  [CmdletBinding()] "
        "  param( "
        "    [Parameter(Position=0)]$FilePath, "
        "    $ArgumentList, "
        "    $WorkingDirectory, "
        "    $WindowStyle, "
        "    [switch]$PassThru, "
        "    [switch]$NoNewWindow, "
        "    [Parameter(ValueFromRemainingArguments=$true)]$Rest "
        "  ) "
        "  $script:__V1K_SC['Start-Process']++; "
        "  $argText = if ($null -eq $ArgumentList) { '' } "
        "    elseif ($ArgumentList -is [System.Array]) { "
        "      ($ArgumentList | ForEach-Object { [string]$_ }) -join ' ' "
        "    } else { [string]$ArgumentList }; "
        "  $script:__V1K_SP.Add(@{ "
        "    FilePath = [string]$FilePath; "
        "    ArgumentList = $argText; "
        "    WorkingDirectory = [string]$WorkingDirectory; "
        "    WindowStyle = if ($null -eq $WindowStyle) { '' } else { [string]$WindowStyle } "
        "  }) | Out-Null; "
        "  throw 'V1K_START_PROCESS_CAPTURED' "
        "}; "
        f"$__v1k_args = {args_ps}; "
        f"try {{ & '{ps1_lit}' @__v1k_args }} catch {{ "
        "  $__msg = \"$_\"; "
        "  if ($__msg -notmatch 'V1K_START_PROCESS_CAPTURED' "
        "      -and $__msg -notmatch 'V1K_SIDE_EFFECT_FORBIDDEN') { "
        "    Write-Error $_ "
        "  } "
        "}; "
        "$__v1k_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }; "
        f"Write-Output ('{_SIDE_EFFECT_MARKER}' + ($script:__V1K_SC | ConvertTo-Json -Compress)); "
        f"Write-Output ('{_START_PROCESS_CAPTURE_MARKER}' + (ConvertTo-Json -Compress @($script:__V1K_SP.ToArray()))); "
        "exit $__v1k_code"
    )
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    raw = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=str(cwd or script_path.parent),
        capture_output=True,
        timeout=timeout,
        check=False,
        env=run_env,
    )
    proc = subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode,
        stdout=_decode_ps_output(raw.stdout),
        stderr=_decode_ps_output(raw.stderr),
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    counts = _parse_side_effect_counts(combined)
    captures_raw = _parse_json_marker_line(combined, _START_PROCESS_CAPTURE_MARKER)
    if not isinstance(captures_raw, list):
        raise AssertionError(f"Start-Process capture 必须为数组：{captures_raw!r}")
    captures: list[dict[str, Any]] = []
    for item in captures_raw:
        if not isinstance(item, dict):
            raise AssertionError(f"Start-Process capture 项必须为对象：{item!r}")
        captures.append(dict(item))
    return proc, captures, counts


def _assert_start_capture_no_other_side_effects(
    tc: unittest.TestCase,
    counts: dict[str, int],
) -> None:
    """start capture 路径：除 Start-Process 专用捕获外所有副作用计数必须为 0。"""
    for key in _SIDE_EFFECT_KEYS:
        tc.assertIn(key, counts)
        if key == "Start-Process":
            continue
        tc.assertEqual(
            counts[key],
            0,
            f"start capture 除专用捕获外不得有副作用：{key}={counts[key]} 全量={counts}",
        )


def _normalize_path_key(path: str | Path) -> str:
    return str(Path(path)).replace("/", "\\").rstrip("\\").lower()


def _assert_exact_window_style_hidden(tc: unittest.TestCase, ws: str) -> None:
    tc.assertEqual(
        str(ws).strip(),
        _EXPECTED_WINDOW_STYLE,
        f"WindowStyle 必须精确为 {_EXPECTED_WINDOW_STYLE!r}，不得空/其它：{ws!r}",
    )


def _assert_exact_working_directory(
    tc: unittest.TestCase,
    actual: str,
    expected_dir: Path,
) -> None:
    tc.assertTrue(str(actual).strip(), "WorkingDirectory 不得为空")
    try:
        actual_res = Path(actual).resolve()
        expected_res = expected_dir.resolve()
    except OSError as exc:
        raise AssertionError(
            f"WorkingDirectory 无法 resolve：actual={actual!r} expected={expected_dir!r}: {exc}"
        ) from exc
    tc.assertEqual(
        _normalize_path_key(actual_res),
        _normalize_path_key(expected_res),
        f"WorkingDirectory 必须精确等于 {expected_res}，实际={actual!r}",
    )


def _assert_exact_backend_start_capture(
    tc: unittest.TestCase,
    cap: dict[str, Any],
    repo_root: Path,
) -> None:
    expected_py = (
        repo_root / "backend" / ".venv" / "Scripts" / "python.exe"
    ).resolve()
    fp = str(cap.get("FilePath", "")).strip()
    tc.assertTrue(fp, "backend FilePath 不得为空")
    try:
        fp_res = Path(fp).resolve()
    except OSError:
        fp_res = Path(fp)
    tc.assertEqual(
        _normalize_path_key(fp_res),
        _normalize_path_key(expected_py),
        f"backend FilePath 必须精确等于 {expected_py}，实际={fp!r}",
    )
    args = str(cap.get("ArgumentList", "")).strip()
    tc.assertEqual(
        args,
        _EXPECTED_BACKEND_START_ARGS,
        f"backend ArgumentList 必须精确为 {_EXPECTED_BACKEND_START_ARGS!r}，实际={args!r}",
    )
    _assert_exact_working_directory(
        tc,
        str(cap.get("WorkingDirectory", "")),
        repo_root / "backend",
    )
    _assert_exact_window_style_hidden(tc, str(cap.get("WindowStyle", "")))


def _assert_exact_frontend_start_capture(
    tc: unittest.TestCase,
    cap: dict[str, Any],
    repo_root: Path,
    npm_cmd: Path,
) -> None:
    fp = str(cap.get("FilePath", "")).strip()
    tc.assertTrue(fp, "frontend FilePath 不得为空")
    npm_cmd_res = npm_cmd.resolve()
    allowed_names = {"npm", "npm.cmd"}
    fp_ok = fp in allowed_names
    if not fp_ok:
        try:
            fp_ok = _normalize_path_key(Path(fp).resolve()) == _normalize_path_key(
                npm_cmd_res
            )
        except OSError:
            fp_ok = False
    if not fp_ok:
        fp_ok = _normalize_path_key(fp) == _normalize_path_key(npm_cmd_res)
    tc.assertTrue(
        fp_ok,
        f"frontend FilePath 必须精确为 npm/npm.cmd 或 TEMP 假 npm.cmd={npm_cmd_res}，实际={fp!r}",
    )
    args = str(cap.get("ArgumentList", "")).strip()
    tc.assertEqual(
        args,
        _EXPECTED_FRONTEND_START_ARGS,
        f"frontend ArgumentList 必须精确为 {_EXPECTED_FRONTEND_START_ARGS!r}，实际={args!r}",
    )
    _assert_exact_working_directory(
        tc,
        str(cap.get("WorkingDirectory", "")),
        repo_root / "frontend",
    )
    _assert_exact_window_style_hidden(tc, str(cap.get("WindowStyle", "")))


def _ast_contains_boolop_or(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or):
            return True
    return False


def _assert_self_no_assert_boolop_or(source: str) -> list[str]:
    """
    用 Python ast.parse 扫描：任何 Assert 或 self.assert* 断言参数树
    含 ast.BoolOp(ast.Or) 均失败。不得跳过本测试行或以字符串自伤规避。
    """
    tree = ast.parse(source)
    bad: list[str] = []
    assert_names = {
        "assertTrue",
        "assertFalse",
        "assertIn",
        "assertNotIn",
        "assertEqual",
        "assertNotEqual",
        "assertIs",
        "assertIsNot",
        "assertIsNone",
        "assertIsNotNone",
        "assertAlmostEqual",
        "assertGreater",
        "assertGreaterEqual",
        "assertLess",
        "assertLessEqual",
        "assertRegex",
        "assertNotRegex",
        "assertCountEqual",
        "assertRaises",
        "assertWarns",
        "assertLogs",
        "fail",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _ast_contains_boolop_or(node.test):
            bad.append(f"L{node.lineno}:Assert-BoolOp-Or")
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in {"self", "cls", "tc"}:
                name = func.attr
        if name is None or name not in assert_names:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if _ast_contains_boolop_or(arg):
                bad.append(f"L{node.lineno}:self.{name}-BoolOp-Or")
                break
    return bad


def _status_path(repo: Path) -> Path:
    return repo / _STATUS_REL


def _load_status(repo: Path) -> dict[str, Any]:
    path = _status_path(repo)
    if not path.is_file():
        raise AssertionError(f"状态侧车未写入：{path}")
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"状态侧车非合法 JSON（半文件？）：{exc}: {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise AssertionError(f"状态侧车必须为对象：{type(data)}")
    return data


# ===========================================================================
# TEMP 假仓
# ===========================================================================


class _TempRepo:
    """
    在系统 TEMP 构造假仓：复制真源与入口布局，注入前置与快照。
    禁止引用主仓真实 data/uploads/密钥。
    """

    def __init__(self, *, with_full_prereqs: bool = True) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="v1k-start-")
        self.root = Path(self._td.name) / "repo"
        self.root.mkdir(parents=True, exist_ok=True)
        self.snap_dir = Path(self._td.name) / "snaps"
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        self._layout(with_full_prereqs=with_full_prereqs)

    def cleanup(self) -> None:
        self._td.cleanup()

    def _layout(self, *, with_full_prereqs: bool) -> None:
        (self.root / "tools" / "v1-ops").mkdir(parents=True, exist_ok=True)
        (self.root / "backend" / "app").mkdir(parents=True, exist_ok=True)
        (self.root / "frontend").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)

        # 复制真源（若不存在则行为测试在 require 阶段已红）
        if _TRUE_SOURCE.is_file():
            shutil.copy2(_TRUE_SOURCE, self.root / "tools" / "v1-ops" / "Start-Biaoshu-Dev.ps1")
        # 复制五入口 + Diagnose（存在才复制；缺失由 require 负责业务红）
        for src, rel in (
            (_ROOT_START_BAT, "Start-Biaoshu-Dev.bat"),
            (_ROOT_START_PS1, "Start-Biaoshu-Dev.ps1"),
            (_ROOT_UI_BAT, "Start-Biaoshu-UI.bat"),
            (_BACKEND_RUN_BAT, "backend/run-dev.bat"),
            (_FRONTEND_RUN_BAT, "frontend/run-dev.bat"),
            (_DIAGNOSE_BAT, "Diagnose-Biaoshu-Dev.bat"),
        ):
            if src.is_file():
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        if with_full_prereqs:
            self.ensure_backend_prereqs()
            self.ensure_frontend_prereqs()

    def ensure_backend_prereqs(self) -> None:
        venv_scripts = self.root / "backend" / ".venv" / "Scripts"
        venv_scripts.mkdir(parents=True, exist_ok=True)
        py = venv_scripts / "python.exe"
        if not py.exists():
            # 占位文件：归属判定只看路径文本；Plan/Diagnose 不得真实执行
            py.write_bytes(b"MZ-fake-python-v1k\n")
        main_py = self.root / "backend" / "app" / "main.py"
        if not main_py.exists():
            main_py.write_text(
                "# fake main for v1k temp repo\napp = None\n",
                encoding="utf-8",
            )

    def ensure_frontend_prereqs(self) -> None:
        pkg = self.root / "frontend" / "package.json"
        if not pkg.exists():
            pkg.write_text(
                json.dumps({"name": "biaoshu-frontend-fake", "private": True}),
                encoding="utf-8",
            )
        nm = self.root / "frontend" / "node_modules"
        nm.mkdir(parents=True, exist_ok=True)
        vite_bin = nm / "vite" / "bin"
        vite_bin.mkdir(parents=True, exist_ok=True)
        (vite_bin / "vite.js").write_text("// fake vite\n", encoding="utf-8")
        # 模拟 npm 可用：在假仓 tools 下放 shim，并把 PATH 由调用方注入时可选
        # 前置检查生产侧用 where npm；TEMP 场景依赖机器有 npm 或生产接受 PATH 注入。
        # 为隔离，写入 frontend/.npm-shim 标记，真正 npm_missing 用例会摘掉 PATH。

    def remove_backend_venv(self) -> None:
        p = self.root / "backend" / ".venv"
        if p.exists():
            shutil.rmtree(p)

    def remove_backend_main(self) -> None:
        p = self.root / "backend" / "app" / "main.py"
        if p.exists():
            p.unlink()

    def remove_frontend_package(self) -> None:
        p = self.root / "frontend" / "package.json"
        if p.exists():
            p.unlink()

    def remove_frontend_node_modules(self) -> None:
        p = self.root / "frontend" / "node_modules"
        if p.exists():
            shutil.rmtree(p)

    def true_source(self) -> Path:
        return self.root / "tools" / "v1-ops" / "Start-Biaoshu-Dev.ps1"

    def write_listener_snapshot(self, records: Any, name: str = "listener.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def write_probe_snapshot(self, records: Any, name: str = "probe.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def write_process_snapshot(self, records: Any, name: str = "process.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def run_true_source(
        self,
        args: list[str],
        *,
        timeout: int = 45,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        直接执行真源（-File）。用于 start 模式失败先测等不得包装的路径。
        PlanOnly/DiagnoseOnly 零调用主证据请用 run_true_source_guarded。
        """
        script = self.true_source()
        if not script.is_file():
            raise AssertionError(f"TEMP 假仓真源不存在：{script}")
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ]
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        proc = subprocess.run(
            cmd,
            cwd=str(self.root),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=run_env,
        )
        return subprocess.CompletedProcess(
            args=proc.args,
            returncode=proc.returncode,
            stdout=_decode_ps_output(proc.stdout),
            stderr=_decode_ps_output(proc.stderr),
        )

    def run_true_source_guarded(
        self,
        args: list[str],
        *,
        timeout: int = 45,
        env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
        """
        PlanOnly/DiagnoseOnly 专用：副作用作用域覆盖 + 精确计数。
        禁止用于 start 模式（以免掩盖生产 start 失败先测）。
        """
        joined = " ".join(args).lower()
        if "-planonly" not in joined and "-diagnoseonly" not in joined:
            raise AssertionError(
                "run_true_source_guarded 仅允许 PlanOnly/DiagnoseOnly，"
                "不得包装生产 start 路径"
            )
        script = self.true_source()
        if not script.is_file():
            raise AssertionError(f"TEMP 假仓真源不存在：{script}")
        return _run_ps1_with_side_effect_guard(
            script,
            args,
            cwd=self.root,
            timeout=timeout,
            env=env,
        )

    def run_entry_bat(
        self,
        rel: str,
        args: list[str],
        *,
        timeout: int = 45,
    ) -> subprocess.CompletedProcess[str]:
        bat = self.root / rel
        if not bat.is_file():
            raise AssertionError(f"TEMP 入口缺失：{bat}")
        return _run_cmd_bat(bat, args, cwd=self.root, timeout=timeout)

    def run_entry_ps1(
        self,
        rel: str,
        args: list[str],
        *,
        timeout: int = 45,
    ) -> subprocess.CompletedProcess[str]:
        ps1 = self.root / rel
        if not ps1.is_file():
            raise AssertionError(f"TEMP 入口缺失：{ps1}")
        return _run_powershell_file(ps1, args, cwd=self.root, timeout=timeout)


# ===========================================================================
# 1. 边界 / 存在性 / 薄委托静态门
# ===========================================================================


class TestBoundaryExistenceAndDelegation(unittest.TestCase):
    """真源与 Diagnose 存在、七脚本/一测试边界、五入口可转发 plan/diagnose。"""

    def test_true_source_exists(self) -> None:
        self.assertTrue(
            _TRUE_SOURCE.is_file(),
            f"唯一逻辑真源必须存在：{_TRUE_SOURCE}",
        )

    def test_diagnose_bat_exists(self) -> None:
        self.assertTrue(
            _DIAGNOSE_BAT.is_file(),
            f"Diagnose-Biaoshu-Dev.bat 必须存在：{_DIAGNOSE_BAT}",
        )

    def test_true_source_utf8_bom(self) -> None:
        src = _require_true_source()
        self.assertTrue(_has_utf8_bom(src), "真源 PS1 必须带 UTF-8 BOM")

    def test_strict_file_boundary_seven_plus_one(self) -> None:
        """
        静态边界：V1-K 生产七脚本 + 本测试；不把 Stop/业务代码算入可写集合。
        主证据仍是 git status 仅本测试变更（由审查报告核对）。
        """
        # 七脚本路径冻结
        self.assertEqual(len(_PROD_SEVEN), 7)
        self.assertEqual(_TEST_ONLY, "tools/v1-ops/test_start_biaoshu_dev.py")
        # Stop 不在可写七脚本内
        self.assertNotIn("tools/v1-ops/Stop-Biaoshu-Dev.ps1", _PROD_SEVEN)
        self.assertTrue(_STOP_PS1.is_file(), "Stop 生产脚本应保持存在且本测试不得删除")

    def test_true_source_static_no_cmd_k_browser_readhost(self) -> None:
        """额外静态门：真源禁用 cmd /k、自动浏览器、Read-Host（不能替代行为）。"""
        src = _require_true_source()
        raw, text = _read_text_with_bom_check(src)
        self.assertTrue(raw.startswith(_BOM))
        lowered = text.lower()
        self.assertNotIn("read-host", lowered)
        self.assertNotIn("cmd /k", lowered)
        # 自动打开浏览器常见形态
        self.assertNotRegex(text, r"(?i)Start-Process\s+['\"]?https?://")
        self.assertNotRegex(text, r"(?i)Start-Process\s+\$Url")

    def test_five_entries_forward_plan_diagnose_in_temp(self) -> None:
        """
        行为：TEMP 中五入口 + Diagnose 必须能把 -PlanOnly/-DiagnoseOnly 交到真源。
        生产未委托时会因第二套算法/不识别参数而失败 → 真实业务红。
        """
        _require_all_prod_for_behavior()
        tr = _TempRepo(with_full_prereqs=True)
        try:
            listener = tr.write_listener_snapshot([])
            probe = tr.write_probe_snapshot([])
            common = [
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
            cases: list[tuple[str, str, list[str]]] = [
                ("bat", "Start-Biaoshu-Dev.bat", list(common)),
                ("ps1", "Start-Biaoshu-Dev.ps1", list(common)),
                ("bat", "Start-Biaoshu-UI.bat", list(common)),
                ("bat", "backend/run-dev.bat", list(common)),
                ("bat", "frontend/run-dev.bat", list(common)),
                (
                    "bat",
                    "Diagnose-Biaoshu-Dev.bat",
                    [
                        "-ListenerSnapshotJson",
                        str(listener),
                        "-ProbeSnapshotJson",
                        str(probe),
                    ],
                ),
            ]
            for kind, rel, args in cases:
                if kind == "bat":
                    proc = tr.run_entry_bat(rel, args)
                else:
                    proc = tr.run_entry_ps1(rel, args)
                combined = (proc.stdout or "") + (proc.stderr or "")
                _assert_zero_side_effect_tokens(self, combined)
                # Plan/Diagnose 在空监听完整前置下：plan 成功或 diagnose 成功路径
                # 委托失败/旧算法会非零或启动副作用——均不得伪装成功且无状态
                status_file = _status_path(tr.root)
                self.assertTrue(
                    status_file.is_file(),
                    f"入口 {rel} 必须驱动真源写状态侧车；code={proc.returncode} out={combined!r}",
                )
                status = _load_status(tr.root)
                if "Diagnose" in rel:
                    self.assertEqual(status["mode"], "diagnose")
                else:
                    self.assertEqual(status["mode"], "plan")
                _assert_no_sensitive(self, json.dumps(status, ensure_ascii=False), f"status@{rel}")
                _assert_no_sensitive(self, combined, f"diag@{rel}")
        finally:
            root_str = str(tr.root)
            tr.cleanup()
            self.assertFalse(
                Path(root_str).exists(),
                "TEMP 自建根清理后不得残留",
            )


# ===========================================================================
# 2. component / 透传 / 退出码稳定 / 无第二套算法
# ===========================================================================


class TestComponentPassThroughAndExitCodes(unittest.TestCase):
    """component=all/backend/frontend；五入口无第二套算法；参数透传且 exit code 稳定。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _plan_args(self, component: str) -> list[str]:
        listener = self.tr.write_listener_snapshot([], name=f"L-{component}.json")
        probe = self.tr.write_probe_snapshot([], name=f"P-{component}.json")
        return [
            "-Component",
            component,
            "-PlanOnly",
            "-ListenerSnapshotJson",
            str(listener),
            "-ProbeSnapshotJson",
            str(probe),
        ]

    def test_component_all_backend_frontend_plan(self) -> None:
        for comp in ("all", "backend", "frontend"):
            proc = self.tr.run_true_source(self._plan_args(comp))
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertEqual(
                proc.returncode,
                0,
                f"PlanOnly component={comp} 应稳定成功：{combined!r}",
            )
            status = _load_status(self.tr.root)
            _assert_status_schema(self, status, mode="plan", component=comp)
            self.assertEqual(status["overall"], "plan")
            if comp == "all":
                self.assertEqual(status["services"]["backend"]["state"], "planned")
                self.assertEqual(status["services"]["frontend"]["state"], "planned")
            elif comp == "backend":
                self.assertEqual(status["services"]["backend"]["state"], "planned")
                self.assertEqual(status["services"]["frontend"]["state"], "not_selected")
            else:
                self.assertEqual(status["services"]["frontend"]["state"], "planned")
                self.assertEqual(status["services"]["backend"]["state"], "not_selected")
            _assert_zero_side_effect_tokens(self, combined)

    def test_five_entries_no_second_algorithm_pass_component(self) -> None:
        """
        五入口透传后状态 component 与入口语义一致，证明无第二套端口/进程算法。
        """
        mapping = [
            ("Start-Biaoshu-Dev.bat", "all"),
            ("Start-Biaoshu-Dev.ps1", "all"),
            ("Start-Biaoshu-UI.bat", "frontend"),
            ("backend/run-dev.bat", "backend"),
            ("frontend/run-dev.bat", "frontend"),
        ]
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        base = [
            "-PlanOnly",
            "-ListenerSnapshotJson",
            str(listener),
            "-ProbeSnapshotJson",
            str(probe),
        ]
        for rel, expected_comp in mapping:
            if rel.endswith(".ps1"):
                proc = self.tr.run_entry_ps1(rel, list(base))
            else:
                proc = self.tr.run_entry_bat(rel, list(base))
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertEqual(
                proc.returncode,
                0,
                f"{rel} PlanOnly 应成功：{combined!r}",
            )
            status = _load_status(self.tr.root)
            self.assertEqual(
                status["component"],
                expected_comp,
                f"{rel} 必须选择 component={expected_comp}",
            )
            self.assertEqual(status["mode"], "plan")
            # 旧算法特征：仅 LISTENING/端口探测即成功且无状态七键——已由 schema 排除

    def test_exit_code_stable_on_repeated_plan(self) -> None:
        args = self._plan_args("all")
        codes = []
        for _ in range(3):
            proc = self.tr.run_true_source(args)
            codes.append(proc.returncode)
        self.assertEqual(codes, [0, 0, 0], f"PlanOnly 退出码必须稳定：{codes}")


# ===========================================================================
# 3. 前置缺失固定 code；all 任一失败零部分启动
# ===========================================================================


class TestPrerequisitesFixedCodes(unittest.TestCase):
    """后端 python/main、前端 npm/package/node_modules 缺失逐项固定 code。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()

    def _run_plan(self, tr: _TempRepo, component: str = "all") -> subprocess.CompletedProcess[str]:
        listener = tr.write_listener_snapshot([])
        probe = tr.write_probe_snapshot([])
        return tr.run_true_source(
            [
                "-Component",
                component,
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )

    def test_venv_missing_fixed_code(self) -> None:
        tr = _TempRepo(with_full_prereqs=True)
        try:
            tr.remove_backend_venv()
            proc = self._run_plan(tr, "backend")
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            self.assertEqual(status["overall"], "failed")
            self.assertEqual(status["code"], "venv_missing")
            self.assertEqual(status["services"]["backend"]["code"], "venv_missing")
            self.assertEqual(status["services"]["backend"]["state"], "missing")
            _assert_no_sensitive(self, json.dumps(status, ensure_ascii=False), "status")
        finally:
            tr.cleanup()

    def test_backend_entry_missing_fixed_code(self) -> None:
        tr = _TempRepo(with_full_prereqs=True)
        try:
            tr.remove_backend_main()
            proc = self._run_plan(tr, "backend")
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            self.assertEqual(status["code"], "backend_entry_missing")
            self.assertEqual(status["services"]["backend"]["code"], "backend_entry_missing")
        finally:
            tr.cleanup()

    def test_frontend_package_missing_fixed_code(self) -> None:
        tr = _TempRepo(with_full_prereqs=True)
        try:
            tr.remove_frontend_package()
            proc = self._run_plan(tr, "frontend")
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            self.assertEqual(status["code"], "frontend_package_missing")
            self.assertEqual(status["services"]["frontend"]["code"], "frontend_package_missing")
        finally:
            tr.cleanup()

    def test_frontend_deps_missing_fixed_code(self) -> None:
        tr = _TempRepo(with_full_prereqs=True)
        try:
            tr.remove_frontend_node_modules()
            proc = self._run_plan(tr, "frontend")
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            self.assertEqual(status["code"], "frontend_deps_missing")
            self.assertEqual(status["services"]["frontend"]["code"], "frontend_deps_missing")
        finally:
            tr.cleanup()

    def test_npm_missing_fixed_code(self) -> None:
        """
        通过掏空 PATH 使 where npm 失败；固定 code=npm_missing。
        不得访问网络安装 npm。
        """
        tr = _TempRepo(with_full_prereqs=True)
        try:
            # 最小 PATH：仅系统根，不含 node/npm
            minimal_path = r"C:\Windows\System32;C:\Windows"
            listener = tr.write_listener_snapshot([])
            probe = tr.write_probe_snapshot([])
            proc = tr.run_true_source(
                [
                    "-Component",
                    "frontend",
                    "-PlanOnly",
                    "-ListenerSnapshotJson",
                    str(listener),
                    "-ProbeSnapshotJson",
                    str(probe),
                ],
                env={"PATH": minimal_path, "Path": minimal_path},
            )
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            self.assertEqual(status["code"], "npm_missing")
            self.assertEqual(status["services"]["frontend"]["code"], "npm_missing")
        finally:
            tr.cleanup()

    def test_all_prereq_fail_zero_partial_start(self) -> None:
        """
        all 同时制造 backend venv + frontend package 明确缺失：
        顶层 code 精确 venv_missing（稳定优先级）、backend=venv_missing、
        frontend=frontend_package_missing；两端均计算且顺序稳定；零副作用。
        """
        tr = _TempRepo(with_full_prereqs=True)
        try:
            tr.remove_backend_venv()
            tr.remove_frontend_package()
            listener = tr.write_listener_snapshot([])
            probe = tr.write_probe_snapshot([])
            proc, counts = tr.run_true_source_guarded(
                [
                    "-Component",
                    "all",
                    "-PlanOnly",
                    "-ListenerSnapshotJson",
                    str(listener),
                    "-ProbeSnapshotJson",
                    str(probe),
                ]
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            _assert_status_schema(self, status, mode="plan", component="all")
            self.assertEqual(status["overall"], "failed")
            # 顶层稳定优先级：先后端前置 → venv_missing
            self.assertEqual(status["code"], "venv_missing")
            for sk in _SERVICE_KEYS:
                st = status["services"][sk]["state"]
                self.assertEqual(
                    st,
                    "missing",
                    f"{sk} 前置失败 state 必须为 missing，实际={st!r}",
                )
            self.assertEqual(status["services"]["backend"]["code"], "venv_missing")
            self.assertEqual(
                status["services"]["frontend"]["code"],
                "frontend_package_missing",
            )
            _assert_zero_side_effect_counts(self, counts)
            _assert_zero_side_effect_tokens(self, combined)
        finally:
            tr.cleanup()


# ===========================================================================
# 4. 归属与就绪矩阵
# ===========================================================================


class TestOwnershipAndReadinessMatrix(unittest.TestCase):
    """无监听 planned；owned+ready；owned+not_ready；foreign/mixed；枚举失败。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def test_no_listener_plan_is_planned(self) -> None:
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        self.assertEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "plan")
        self.assertEqual(status["services"]["backend"]["state"], "planned")
        self.assertEqual(status["services"]["frontend"]["state"], "planned")

    def test_owned_and_probe_ready_is_already_running(self) -> None:
        listeners = [
            _owned_backend_listener(self.tr.root, 710001),
            _owned_frontend_listener(self.tr.root, 710002),
        ]
        probes = [_probe_backend_ready(), _probe_frontend_ready(200)]
        listener = self.tr.write_listener_snapshot(listeners)
        probe = self.tr.write_probe_snapshot(probes)
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        diag_out = f"{proc.stdout or ''}{proc.stderr or ''}"
        self.assertEqual(proc.returncode, 0, diag_out)
        status = _load_status(self.tr.root)
        self.assertEqual(status["mode"], "diagnose")
        self.assertEqual(status["overall"], "already_running")
        self.assertEqual(status["code"], "already_running")
        self.assertEqual(status["services"]["backend"]["state"], "already_running")
        self.assertEqual(status["services"]["frontend"]["state"], "already_running")

    def test_owned_but_not_ready_fails(self) -> None:
        listeners = [
            _owned_backend_listener(self.tr.root, 720001),
            _owned_frontend_listener(self.tr.root, 720002),
        ]
        probes = [
            _probe_backend_not_ready(http_status=200, status="ok", db_ok=False),
            _probe_frontend_not_ready(502),
        ]
        listener = self.tr.write_listener_snapshot(listeners)
        probe = self.tr.write_probe_snapshot(probes)
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        # 已归属未就绪：精确 state/code，禁止宽 assertIn
        self.assertEqual(status["services"]["backend"]["state"], "not_ready")
        self.assertEqual(status["services"]["backend"]["code"], "backend_not_ready")
        self.assertEqual(status["services"]["frontend"]["state"], "not_ready")
        self.assertEqual(status["services"]["frontend"]["code"], "frontend_not_ready")
        combined = (proc.stdout or "") + (proc.stderr or "")
        _assert_zero_side_effect_tokens(self, combined)

    def test_foreign_listener_fails(self) -> None:
        listeners = [_foreign_backend_listener(), _foreign_frontend_listener()]
        listener = self.tr.write_listener_snapshot(listeners)
        probe = self.tr.write_probe_snapshot([])
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["services"]["backend"]["state"], "foreign")
        self.assertEqual(status["services"]["backend"]["code"], "backend_port_foreign")
        self.assertEqual(status["services"]["frontend"]["state"], "foreign")
        self.assertEqual(status["services"]["frontend"]["code"], "frontend_port_foreign")

    def test_mixed_owned_and_foreign_fails(self) -> None:
        listeners = [
            _owned_backend_listener(self.tr.root, 730001),
            _foreign_frontend_listener(730002),
        ]
        probes = [_probe_backend_ready()]
        listener = self.tr.write_listener_snapshot(listeners)
        probe = self.tr.write_probe_snapshot(probes)
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["services"]["frontend"]["state"], "foreign")
        self.assertEqual(status["services"]["frontend"]["code"], "frontend_port_foreign")

    def test_listener_enumeration_failure_code(self) -> None:
        """
        不投稿快照时，在子作用域令 Get-NetTCPConnection 抛错 + DiagnoseOnly，
        必须 listener_unavailable，零终止/启动。
        """
        script = self.tr.true_source()
        ps1 = str(script).replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'; "
            "function global:Get-NetTCPConnection { throw 'SIMULATED_ENUM_FAILURE_V1K' }; "
            f"& '{ps1}' -Component all -DiagnoseOnly; "
            "exit $LASTEXITCODE"
        )
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=str(self.tr.root),
            capture_output=True,
            timeout=45,
            check=False,
        )
        combined = _decode_ps_output(raw.stdout) + _decode_ps_output(raw.stderr)
        self.assertNotEqual(raw.returncode, 0, combined)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "listener_unavailable")
        self.assertEqual(status["overall"], "failed")
        _assert_zero_side_effect_tokens(self, combined)
        _assert_no_sensitive(self, combined, "enum-fail-diag")


# ===========================================================================
# 5. 就绪探测规则
# ===========================================================================


class TestHealthProbeRules(unittest.TestCase):
    """backend 200+ok+dbOk；frontend 冻结成功状态；超时/早退 code。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _diagnose(self, listeners: list[dict], probes: list[dict]) -> subprocess.CompletedProcess[str]:
        return self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(probes)),
            ]
        )

    def test_backend_requires_200_ok_dbok_true(self) -> None:
        # 200 但 dbOk=false → not_ready
        listeners = [_owned_backend_listener(self.tr.root, 740001)]
        probes = [_probe_backend_not_ready(http_status=200, status="ok", db_ok=False)]
        proc = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(probes)),
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["services"]["backend"]["code"], "backend_not_ready")

        # status!=ok
        probes2 = [_probe_backend_not_ready(http_status=200, status="error", db_ok=True)]
        proc2 = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners, name="L2.json")),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(probes2, name="P2.json")),
            ]
        )
        self.assertNotEqual(proc2.returncode, 0)
        status2 = _load_status(self.tr.root)
        self.assertEqual(status2["services"]["backend"]["code"], "backend_not_ready")

        # http!=200
        probes3 = [_probe_backend_not_ready(http_status=500, status="ok", db_ok=True)]
        proc3 = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners, name="L3.json")),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(probes3, name="P3.json")),
            ]
        )
        self.assertNotEqual(proc3.returncode, 0)
        status3 = _load_status(self.tr.root)
        self.assertEqual(status3["services"]["backend"]["code"], "backend_not_ready")

    def test_frontend_only_frozen_success_statuses(self) -> None:
        """前端 ready 仅 HTTP 200；304 与其它状态一律非 ready。"""
        self.assertEqual(_FRONTEND_READY_HTTP, frozenset({200}))
        self.assertIn(304, _FRONTEND_NOT_READY_HTTP)
        listeners = [_owned_frontend_listener(self.tr.root, 750001)]
        for code in sorted(_FRONTEND_READY_HTTP):
            proc = self.tr.run_true_source(
                [
                    "-Component",
                    "frontend",
                    "-DiagnoseOnly",
                    "-ListenerSnapshotJson",
                    str(self.tr.write_listener_snapshot(listeners, name=f"Lf{code}.json")),
                    "-ProbeSnapshotJson",
                    str(
                        self.tr.write_probe_snapshot(
                            [_probe_frontend_ready(code)],
                            name=f"Pf{code}.json",
                        )
                    ),
                ]
            )
            fe_out = f"{proc.stdout or ''}{proc.stderr or ''}"
            self.assertEqual(
                proc.returncode,
                0,
                f"frontend httpStatus={code} 应 ready：{fe_out!r}",
            )
            status = _load_status(self.tr.root)
            self.assertEqual(status["services"]["frontend"]["state"], "already_running")

        # 非冻结状态（含 304：契约未列，不得 ready）
        for bad in sorted(_FRONTEND_NOT_READY_HTTP):
            proc_b = self.tr.run_true_source(
                [
                    "-Component",
                    "frontend",
                    "-DiagnoseOnly",
                    "-ListenerSnapshotJson",
                    str(self.tr.write_listener_snapshot(listeners, name=f"Lfb{bad}.json")),
                    "-ProbeSnapshotJson",
                    str(
                        self.tr.write_probe_snapshot(
                            [_probe_frontend_not_ready(bad)],
                            name=f"Pfb{bad}.json",
                        )
                    ),
                ]
            )
            self.assertNotEqual(proc_b.returncode, 0, f"httpStatus={bad} 不得 ready")
            status_b = _load_status(self.tr.root)
            self.assertEqual(status_b["services"]["frontend"]["code"], "frontend_not_ready")
            self.assertEqual(status_b["services"]["frontend"]["state"], "not_ready")

    def test_probe_timeout_or_missing_is_not_ready(self) -> None:
        """
        owned 监听但 probe 快照缺对应端口 → 视为未就绪/早退，固定 not_ready code。
        禁止固定 sleep 轮询。
        """
        listeners = [
            _owned_backend_listener(self.tr.root, 760001),
            _owned_frontend_listener(self.tr.root, 760002),
        ]
        # 空 probe：两端均无就绪证据
        proc = self._diagnose(listeners, [])
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["services"]["backend"]["state"], "not_ready")
        self.assertEqual(status["services"]["frontend"]["state"], "not_ready")


# ===========================================================================
# 6. 状态 JSON 精确性与原子覆盖
# ===========================================================================


class TestStatusJsonStrictAndAtomic(unittest.TestCase):
    """七键、services 两键、枚举、UTC Z、schemaVersion=1、原子覆盖无半文件。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def test_status_exact_keys_and_enums(self) -> None:
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        self.assertEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="plan", component="all")
        # 确认契约要求的 code 池被生产侧使用（本路径 code 应可解析为合法字符串）
        self.assertIsInstance(status["code"], str)

    def _assert_move_to_final(
        self,
        moves: list[dict[str, str]],
        path: Path,
    ) -> None:
        """断言至少一次同目录临时文件 Move-Item 到终稿。"""
        final_name = path.name.lower()
        final_parent = path.parent.resolve()
        matched = False
        for mv in moves:
            src_p = Path(mv["source"])
            dst_p = Path(mv["destination"])
            if dst_p.name.lower() != final_name:
                continue
            dst_norm = str(dst_p).replace("\\", "/").lower()
            dst_is_final = dst_norm.endswith("tmp/dev-start-status.json")
            if not dst_is_final:
                try:
                    dst_is_final = dst_p.resolve() == path.resolve()
                except OSError:
                    dst_is_final = False
            self.assertTrue(dst_is_final, f"destination 必须为终稿 status：{mv!r}")
            self.assertNotEqual(
                src_p.name.lower(),
                final_name,
                f"source 不得直接是终稿文件名：{mv!r}",
            )
            try:
                src_resolved = str(src_p.resolve()).lower()
                final_resolved = str(path.resolve()).lower()
            except OSError:
                src_resolved = str(src_p).lower()
                final_resolved = str(path).lower()
            self.assertNotEqual(
                src_resolved,
                final_resolved,
                f"source 不得等于终稿路径：{mv!r}",
            )
            try:
                src_parent = src_p.parent.resolve()
            except OSError:
                src_parent = Path(mv["source"]).parent
            try:
                dst_parent = dst_p.parent.resolve()
            except OSError:
                dst_parent = Path(mv["destination"]).parent
            self.assertEqual(
                str(src_parent).lower(),
                str(dst_parent).lower(),
                f"source/destination 必须同目录：{mv!r}",
            )
            self.assertEqual(
                str(dst_parent).lower(),
                str(final_parent).lower(),
                f"destination 目录必须是假仓 tmp：{mv!r}",
            )
            matched = True
            break
        self.assertTrue(matched, f"未找到指向终稿的 Move-Item：{moves!r}")

    def test_atomic_status_create_uses_move_item(self) -> None:
        """
        无终稿初建行为：受控 PlanOnly wrapper trace Move-Item，
        同目录临时文件 → tmp/dev-start-status.json；禁止直写终稿；
        禁止 Remove-Item 终稿（本路径终稿本不存在，列表须空）。
        静态辅助：去注释后须含 Move-Item；AST 拒绝 IO.File 直写。
        """
        src = _require_true_source()
        raw, text = _read_text_with_bom_check(src)
        self.assertTrue(raw.startswith(_BOM))
        cleaned = _ps_strip_comments_and_strings(text)
        self.assertRegex(
            cleaned,
            r"(?i)\bMove-Item\b",
            "真源（去注释后）必须保留初次创建 Move-Item 路径",
        )
        _assert_ps_ast_no_io_file_direct_writes(self, text)

        path = _status_path(self.tr.root)
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.assertFalse(path.exists(), "本用例要求运行前无终稿")
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        script = self.tr.true_source()
        proc, counts, moves, directs, final_removes = _run_ps1_with_atomic_move_trace(
            script,
            [
                "-Component",
                "backend",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ],
            cwd=self.tr.root,
        )
        self.assertEqual(
            final_removes,
            [],
            f"禁止 Remove-Item 删除终稿（反假绿）：{final_removes!r}",
        )
        self.assertEqual(directs, [], f"禁止直接写终稿：{directs}")
        self.assertEqual(proc.returncode, 0)
        self.assertGreaterEqual(len(moves), 1, "无终稿初建必须至少一次 Move-Item")
        self._assert_move_to_final(moves, path)
        raw_status = path.read_text(encoding="utf-8")
        data = json.loads(raw_status)
        _assert_status_schema(self, data, mode="plan", component="backend")
        leftovers = [
            p
            for p in path.parent.iterdir()
            if p.is_file()
            and p.name != path.name
            and any(
                tok in p.name.lower()
                for tok in ("tmp", "temp", "partial", "writing", ".bak", ".wip")
            )
        ]
        self.assertEqual(leftovers, [], f"初建后不得残留半文件：{leftovers}")
        _assert_zero_side_effect_counts(self, counts)

    def test_atomic_status_replace_uses_file_replace_no_final_remove(self) -> None:
        """
        有终稿替换行为（反假绿主证据）：
        1) PowerShell AST 必须精确一处
           [System.IO.File]::Replace(临时, 终稿, [NullString]::Value)
           及正确参数角色；注释/死字符串/字面 $null/双 Replace 不算；
        2) 受控 wrapper 捕获对终稿的 Remove-Item 并断言失败（禁止先删后移）；
        3) 禁止直写终稿；.wip 清理仍允许；
        4) Replace 不得被 IO.File 直写禁门误伤。
        生产若仍 Remove-Item 终稿再 Move-Item，或双 Replace（$null + NullString），
        首红须指向总数/参数角色不合法。
        """
        src = _require_true_source()
        raw, text = _read_text_with_bom_check(src)
        self.assertTrue(raw.startswith(_BOM))
        # 首红目标 1：缺少 File.Replace（参数角色 AST 门）
        _assert_ps_ast_file_replace_for_status(self, text)
        _assert_ps_ast_no_io_file_direct_writes(self, text)
        cleaned = _ps_strip_comments_and_strings(text)
        self.assertRegex(
            cleaned,
            r"(?i)\bMove-Item\b",
            "真源（去注释后）仍须保留初次创建 Move-Item",
        )

        path = _status_path(self.tr.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 预置半 JSON 终稿：合法实现须 File.Replace 覆盖，不得先删
        path.write_text('{"schemaVersion":1,"partial":', encoding="utf-8")
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        script = self.tr.true_source()
        proc, counts, moves, directs, final_removes = _run_ps1_with_atomic_move_trace(
            script,
            [
                "-Component",
                "backend",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ],
            cwd=self.tr.root,
        )
        # 首红目标 2：删除终稿（Remove-Item 终稿）
        self.assertEqual(
            final_removes,
            [],
            "禁止 Remove-Item 删除终稿后再 Move-Item（半状态窗口/反假绿）；"
            f"观测到 final_removes={final_removes!r}",
        )
        self.assertEqual(directs, [], f"禁止直接写终稿：{directs}")
        self.assertEqual(proc.returncode, 0)
        raw_status = path.read_text(encoding="utf-8")
        data = json.loads(raw_status)
        _assert_status_schema(self, data, mode="plan", component="backend")
        leftovers = [
            p
            for p in path.parent.iterdir()
            if p.is_file()
            and p.name != path.name
            and any(
                tok in p.name.lower()
                for tok in ("tmp", "temp", "partial", "writing", ".bak", ".wip")
            )
        ]
        self.assertEqual(leftovers, [], f"替换后不得残留半文件：{leftovers}")
        _assert_zero_side_effect_counts(self, counts)
        # moves 在有终稿+Replace 路径上可为 0；若仍 Move-Item 到终稿也须同目录临时源
        if moves:
            self._assert_move_to_final(moves, path)

    def test_status_write_failed_when_status_dir_blocked(self) -> None:
        """
        写状态失败（确定性注入，不靠 ACL）：
        1) 真源必须包含 status_write_failed 失败路径（静态机制门，辅助）；
        2) 将 tmp 占位为文件，阻断 status 目录创建/写入；
        3) 整体失败；侧车可观测时 code 精确 status_write_failed；
           侧车不可写时，stdout/stderr 必须精确出现唯一 code 文本
           status_write_failed（禁止多选宽 or）。
        """
        src = _require_true_source()
        src_text = src.read_text(encoding="utf-8-sig")
        self.assertIn(
            _STATUS_WRITE_FAILED_CODE,
            src_text,
            "真源必须定义 status_write_failed 固定 code 路径",
        )

        tmp_dir = self.tr.root / "tmp"
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir)
        elif tmp_dir.is_file():
            tmp_dir.unlink()
        # 文件占位目录名：创建 tmp/dev-start-status.json 必然失败
        tmp_dir.write_text("not-a-directory-block-status-write", encoding="utf-8")
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        proc, counts = self.tr.run_true_source_guarded(
            [
                "-Component",
                "all",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        residual = _strip_side_effect_marker_lines(combined)
        self.assertNotEqual(proc.returncode, 0)
        status_path = _status_path(self.tr.root)
        if status_path.is_file():
            status = _load_status(self.tr.root)
            self.assertEqual(status["overall"], "failed")
            self.assertEqual(status["code"], _STATUS_WRITE_FAILED_CODE)
            _assert_status_schema(self, status, mode="plan", component="all")
            for sk in _SERVICE_KEYS:
                self.assertNotIn(
                    status["services"][sk]["state"],
                    ("ready", "already_running", "planned"),
                )
        else:
            # 唯一固定 code 文本，禁止宽 or
            self.assertIn(
                _STATUS_WRITE_FAILED_CODE,
                residual,
                "写状态失败必须精确可观测 status_write_failed；"
                f"out={residual[:400]!r}",
            )
            self.assertNotIn("already_running", residual.lower())
        _assert_zero_side_effect_counts(self, counts)
        _assert_zero_side_effect_tokens(self, combined)

    def test_required_code_enum_surface_via_failure_paths(self) -> None:
        """
        通过多条失败路径触及契约要求的 code 枚举子集，证明不是自由文本。
        """
        seen: set[str] = set()

        # venv_missing
        self.tr.remove_backend_venv()
        self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot([], name="e1.json")),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot([], name="ep1.json")),
            ]
        )
        seen.add(_load_status(self.tr.root)["code"])
        # 恢复 venv 再测 foreign
        self.tr.ensure_backend_prereqs()
        self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(
                    self.tr.write_listener_snapshot(
                        [_foreign_backend_listener()],
                        name="e2.json",
                    )
                ),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot([], name="ep2.json")),
            ]
        )
        seen.add(_load_status(self.tr.root)["code"])
        # snapshot_invalid
        bad = self.tr.write_listener_snapshot(
            [{"port": 8000, "pid": 1, "executablePath": r"C:\x\python.exe", "commandLine": "a", "extra": 1}],
            name="bad.json",
        )
        self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(bad),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot([], name="ep3.json")),
            ]
        )
        seen.add(_load_status(self.tr.root)["code"])
        for c in seen:
            self.assertIn(c, _ALLOWED_CODES, f"code {c!r} 必须属于固定 allowed 枚举")
            # 失败路径触及的 code 应落在契约要求池或 plan/not_selected
            code_ok = c in _REQUIRED_CODES
            if not code_ok:
                code_ok = c in {"plan", "not_selected"}
            self.assertTrue(
                code_ok,
                f"失败路径 code {c!r} 应可归属契约池或 plan/not_selected",
            )
        status_last = _load_status(self.tr.root)
        _assert_status_schema(
            self,
            status_last,
            mode=status_last["mode"],
            component=status_last["component"],
        )


# ===========================================================================
# 7. 敏感字段零出口
# ===========================================================================


class TestNoSensitiveLeakage(unittest.TestCase):
    """状态与固定中文诊断不含 PID/绝对路径/cmdline/异常/密钥等。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def test_status_and_diag_have_no_sensitive_fields(self) -> None:
        listeners = [
            {
                "port": _EXPECTED_BACKEND_PORT,
                "pid": 424242,
                "executablePath": _FAKE_PATH_TOKEN + r"\python.exe",
                "commandLine": _FAKE_CMDLINE_TOKEN
                + f" {_FAKE_KEY_TOKEN} {_FAKE_COOKIE_TOKEN} {_FAKE_CSRF_TOKEN}",
            }
        ]
        # foreign 路径：诊断应失败但不得回显快照敏感内容
        proc = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot([])),
            ]
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        blob = json.dumps(status, ensure_ascii=False) + "\n" + combined
        _assert_no_sensitive(self, blob, "diagnose-output")
        # 状态对象本身不得出现这些键
        raw_status = json.dumps(status)
        for banned_key in (
            "pid",
            "executablePath",
            "commandLine",
            "stdout",
            "stderr",
            "exception",
            "stack",
            "path",
            "argv",
        ):
            # 键名级禁止（code/state 值中的普通英文词除外：用 JSON 键形态）
            self.assertNotIn(f'"{banned_key}"', raw_status)


# ===========================================================================
# 8. 快照严格 schema
# ===========================================================================


class TestSnapshotStrictSchema(unittest.TestCase):
    """listener/probe/process 严格 schema；start 模式固定拒绝快照。"""

    def setUp(self) -> None:
        _require_all_prod_for_behavior()
        self.tr = _TempRepo(with_full_prereqs=True)

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _plan_with_listener(self, records: Any) -> subprocess.CompletedProcess[str]:
        return self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(records)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot([])),
            ]
        )

    def test_listener_rejects_extra_keys(self) -> None:
        bad = [
            {
                "port": 8000,
                "pid": 1,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
                "extra": "nope",
            }
        ]
        proc = self._plan_with_listener(bad)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "snapshot_invalid")

    def test_listener_rejects_duplicate_pid(self) -> None:
        bad = [
            {
                "port": 8000,
                "pid": 99,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "a",
            },
            {
                "port": 5173,
                "pid": 99,
                "executablePath": r"C:\fake\node.exe",
                "commandLine": "b",
            },
        ]
        proc = self._plan_with_listener(bad)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

    def test_listener_rejects_illegal_port(self) -> None:
        bad = [
            {
                "port": 99999,
                "pid": 1,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
            }
        ]
        proc = self._plan_with_listener(bad)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

    def test_listener_rejects_non_int_pid_and_relative_path(self) -> None:
        bad_pid = [
            {
                "port": 8000,
                "pid": "123",
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
            }
        ]
        proc = self._plan_with_listener(bad_pid)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

        bad_rel = [
            {
                "port": 8000,
                "pid": 12,
                "executablePath": r".\python.exe",
                "commandLine": "x",
            }
        ]
        proc2 = self._plan_with_listener(bad_rel)
        self.assertNotEqual(proc2.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

    def test_listener_rejects_newline_and_overlong_command(self) -> None:
        bad_nl = [
            {
                "port": 8000,
                "pid": 13,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "line1\nline2",
            }
        ]
        proc = self._plan_with_listener(bad_nl)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

        bad_long = [
            {
                "port": 8000,
                "pid": 14,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x" * 9000,
            }
        ]
        proc2 = self._plan_with_listener(bad_long)
        self.assertNotEqual(proc2.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

    def test_probe_rejects_extra_keys_and_illegal_bool(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root, 770001)]
        bad_probe = [
            {
                "port": 8000,
                "httpStatus": 200,
                "status": "ok",
                "dbOk": True,
                "extra": True,
            }
        ]
        proc = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(bad_probe)),
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

        # dbOk 非法类型（字符串）
        bad_bool = [
            {
                "port": 8000,
                "httpStatus": 200,
                "status": "ok",
                "dbOk": "true",
            }
        ]
        proc2 = self.tr.run_true_source(
            [
                "-Component",
                "backend",
                "-DiagnoseOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners, name="Lb.json")),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(bad_bool, name="Pb.json")),
            ]
        )
        self.assertNotEqual(proc2.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")

    def _plan_with_process(
        self,
        process_records: Any,
        *,
        listeners: list[dict] | None = None,
        probes: list[dict] | None = None,
        name: str = "process.json",
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
        return self.tr.run_true_source_guarded(
            [
                "-Component",
                "all",
                "-PlanOnly",
                "-ListenerSnapshotJson",
                str(self.tr.write_listener_snapshot(listeners or [], name=f"L-{name}")),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe_snapshot(probes or [], name=f"P-{name}")),
                "-ProcessSnapshotJson",
                str(self.tr.write_process_snapshot(process_records, name=name)),
            ]
        )

    def test_process_snapshot_accepts_minimal_legal(self) -> None:
        """
        合法 ProcessSnapshot：returncode=0、status 必须存在、
        overall=plan、code=plan，两服务 state/code 精确 planned/plan。
        删除条件分支，禁止 status 缺失时假绿。
        """
        legal = [
            _process_record(
                880001,
                r"C:\fake\owned\python.exe",
                "uvicorn app.main:app --host 127.0.0.1 --port 8000",
            )
        ]
        proc, counts = self._plan_with_process(legal, name="proc-legal.json")
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, "合法 ProcessSnapshot 必须 returncode=0")
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="plan", component="all")
        self.assertEqual(status["overall"], "plan")
        self.assertEqual(status["code"], "plan")
        for sk in _SERVICE_KEYS:
            self.assertEqual(
                status["services"][sk]["state"],
                "planned",
                f"{sk}.state 必须 planned",
            )
            self.assertEqual(
                status["services"][sk]["code"],
                "plan",
                f"{sk}.code 必须 plan",
            )
        _assert_zero_side_effect_counts(self, counts)
        _assert_zero_side_effect_tokens(self, combined)

    def test_process_snapshot_rejects_extra_keys(self) -> None:
        bad = [
            {
                "pid": 42,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
                "extra": 1,
            }
        ]
        proc, counts = self._plan_with_process(bad, name="proc-extra.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(_load_status(self.tr.root)["code"], "snapshot_invalid")
        _assert_zero_side_effect_counts(self, counts)

    def test_process_snapshot_rejects_illegal_pid_path_command(self) -> None:
        """非法 PID 类型/相对路径/换行/超限命令 → snapshot_invalid + 零副作用。"""
        cases: list[tuple[str, list[dict[str, Any]]]] = [
            (
                "pid-str",
                [
                    {
                        "pid": "42",
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "x",
                    }
                ],
            ),
            (
                "pid-float",
                [
                    {
                        "pid": 42.5,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "x",
                    }
                ],
            ),
            (
                "rel-path",
                [
                    {
                        "pid": 43,
                        "executablePath": r".\python.exe",
                        "commandLine": "x",
                    }
                ],
            ),
            (
                "nl-cmd",
                [
                    {
                        "pid": 44,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "line1\nline2",
                    }
                ],
            ),
            (
                "long-cmd",
                [
                    {
                        "pid": 45,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "x" * 9000,
                    }
                ],
            ),
        ]
        for name, records in cases:
            proc, counts = self._plan_with_process(records, name=f"proc-{name}.json")
            self.assertNotEqual(
                proc.returncode,
                0,
                f"ProcessSnapshot 非法 {name} 必须失败",
            )
            status = _load_status(self.tr.root)
            self.assertEqual(
                status["code"],
                "snapshot_invalid",
                f"ProcessSnapshot 非法 {name} 必须 snapshot_invalid",
            )
            self.assertEqual(status["overall"], "failed")
            _assert_zero_side_effect_counts(self, counts)

    def test_process_snapshot_invalid_early_exit_zero_side_effects(self) -> None:
        """snapshot_invalid 早退：不得启动/停止/探测副作用。"""
        bad = [
            {
                "pid": 46,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
                "unexpected": True,
            }
        ]
        proc, counts = self._plan_with_process(bad, name="proc-early.json")
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "snapshot_invalid")
        self.assertEqual(status["overall"], "failed")
        for sk in _SERVICE_KEYS:
            self.assertNotEqual(status["services"][sk]["state"], "ready")
            self.assertNotEqual(status["services"][sk]["state"], "already_running")
        _assert_zero_side_effect_counts(self, counts)
        _assert_zero_side_effect_tokens(self, combined)

    def test_start_mode_rejects_snapshot_params(self) -> None:
        """生产 start 模式投稿快照必须固定失败（无 PlanOnly/DiagnoseOnly）。"""
        listener = self.tr.write_listener_snapshot([])
        probe = self.tr.write_probe_snapshot([])
        proc = self.tr.run_true_source(
            [
                "-Component",
                "all",
                "-ListenerSnapshotJson",
                str(listener),
                "-ProbeSnapshotJson",
                str(probe),
            ]
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotEqual(proc.returncode, 0, "start 模式快照必须拒绝")
        # 不得因此真实启动
        _assert_zero_side_effect_tokens(self, combined)
        if _status_path(self.tr.root).is_file():
            status = _load_status(self.tr.root)
            self.assertEqual(status["code"], "snapshot_invalid")
            self.assertEqual(status["mode"], "start")


# ===========================================================================
# 9. DiagnoseOnly/PlanOnly 零副作用与 TEMP 清理
# ===========================================================================


class TestZeroSideEffectsAndTempCleanup(unittest.TestCase):
    """DiagnoseOnly/PlanOnly 零进程/停止/端口/live HTTP/浏览器/pause；TEMP 清理。"""

    def test_plan_diagnose_zero_side_effects(self) -> None:
        """
        主证据：PowerShell 作用域覆盖计数=0；
        辅助：stdout/stderr 子串扫描。
        不得影响 start 路径的失败先测（本用例仅 Plan/Diagnose）。
        """
        _require_all_prod_for_behavior()
        tr = _TempRepo(with_full_prereqs=True)
        try:
            listener = tr.write_listener_snapshot(
                [
                    _owned_backend_listener(tr.root, 780001),
                    _owned_frontend_listener(tr.root, 780002),
                ]
            )
            probe = tr.write_probe_snapshot(
                [_probe_backend_ready(), _probe_frontend_ready()]
            )
            for mode_args in (
                ["-PlanOnly"],
                ["-DiagnoseOnly"],
            ):
                proc, counts = tr.run_true_source_guarded(
                    [
                        "-Component",
                        "all",
                        *mode_args,
                        "-ListenerSnapshotJson",
                        str(listener),
                        "-ProbeSnapshotJson",
                        str(probe),
                    ]
                )
                combined = (proc.stdout or "") + (proc.stderr or "")
                _assert_zero_side_effect_counts(self, counts)
                _assert_zero_side_effect_tokens(self, combined)
                self.assertNotRegex(combined, r"(?i)\bpause\b")
        finally:
            root = tr.root
            tr.cleanup()
            self.assertFalse(root.exists(), "TEMP 自建根清理后不存在")

    def test_temp_repo_root_removed_after_cleanup(self) -> None:
        tr = _TempRepo(with_full_prereqs=False)
        root = tr.root
        self.assertTrue(root.is_dir())
        tr.cleanup()
        self.assertFalse(root.exists())


# ===========================================================================
# 10. Stop/host/端口/业务未变 — 非源码扫描主证据
# ===========================================================================


class TestAllowedCodesAndSchemaMembership(unittest.TestCase):
    """纯枚举面：不依赖生产真源；schema 成员检查在行为用例中复用。"""

    def test_allowed_codes_cover_contract_and_schema_membership(self) -> None:
        self.assertTrue(_REQUIRED_CODES.issubset(_ALLOWED_CODES))
        for code in (
            "ready",
            "already_running",
            "venv_missing",
            "backend_entry_missing",
            "npm_missing",
            "frontend_package_missing",
            "frontend_deps_missing",
            "listener_unavailable",
            "backend_port_foreign",
            "frontend_port_foreign",
            "backend_not_ready",
            "frontend_not_ready",
            "snapshot_invalid",
            "status_write_failed",
            "plan",
            "not_selected",
        ):
            self.assertIn(code, _ALLOWED_CODES)
        # state 值不得混入 code 池
        for state_only in ("planned", "missing", "foreign", "not_ready"):
            self.assertNotIn(state_only, _ALLOWED_CODES)
        self.assertNotIn("custom_free_text_code", _ALLOWED_CODES)
        self.assertNotIn("", _ALLOWED_CODES)
        # schema 对自由文本 code 必须拒绝
        bad = {
            "schemaVersion": 1,
            "updatedAtUtc": "2026-07-22T00:00:00Z",
            "mode": "plan",
            "component": "all",
            "overall": "failed",
            "code": "custom_free_text_code",
            "services": {
                "backend": {"state": "missing", "code": "venv_missing"},
                "frontend": {"state": "missing", "code": "npm_missing"},
            },
        }
        with self.assertRaises(AssertionError):
            _assert_status_schema(self, bad, mode="plan", component="all")
        good = dict(bad)
        good["code"] = "venv_missing"
        _assert_status_schema(self, good, mode="plan", component="all")
        # state 值作为 code 必须被 schema 拒绝
        state_as_code = dict(good)
        state_as_code["code"] = "missing"
        with self.assertRaises(AssertionError):
            _assert_status_schema(self, state_as_code, mode="plan", component="all")


class TestSideEffectTokenHelperIsolation(unittest.TestCase):
    """
    纯 helper 回归：不依赖生产真源。
    证明 marker 五键全 0 不触发辅助扫描，而普通输出出现任一副作用 token 仍失败。
    """

    def test_marker_all_zero_does_not_trip_token_scan(self) -> None:
        payload = {k: 0 for k in _SIDE_EFFECT_KEYS}
        marker_line = _SIDE_EFFECT_MARKER + json.dumps(payload, ensure_ascii=False)
        combined = (
            "plan completed quietly\n"
            f"{marker_line}\n"
            f"{_SIDE_EFFECT_FORBIDDEN_PREFIX}should-be-stripped\n"
        )
        # 不得因 marker 键名误伤
        _assert_zero_side_effect_tokens(self, combined)
        residual = _strip_side_effect_marker_lines(combined)
        self.assertNotIn(_SIDE_EFFECT_MARKER, residual)
        self.assertNotIn("start-process", residual.lower())

    def test_plain_output_with_any_side_effect_token_still_fails(self) -> None:
        samples = (
            "calling Start-Process now",
            "taskkill /F /PID 1",
            "Stop-Process -Id 1",
            "Invoke-WebRequest http://example",
            "Invoke-RestMethod http://example",
            "curl http://x",
            "wget http://x",
            "start http://127.0.0.1:5173",
            "cmd /k something",
            "Read-Host pause here",
            "please Pause",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(AssertionError):
                    _assert_zero_side_effect_tokens(self, sample)


class TestPsAstStaticBypassHelpers(unittest.TestCase):
    """
    纯 helper 回归：不依赖生产真源。
    证明旁路样例会红、正确形态（临时文件 + Move-Item / File.Replace / 冻结 URL 赋值）不误伤。
    """

    def test_probe_url_assignment_ast_accepts_correct_literals(self) -> None:
        good = (
            "$BackendHealthUrl = 'http://127.0.0.1:8000/api/health'\n"
            "$FrontendProbeUrl = 'http://127.0.0.1:5173/create'\n"
            "Start-Process -FilePath 'x' -WindowStyle Hidden\n"
        )
        _assert_ps_ast_frozen_probe_url_assignments(self, good)

    def test_probe_url_assignment_ast_rejects_comment_or_dead_string(self) -> None:
        # 注释与其它变量中的死字符串不得充当冻结赋值
        bad = (
            "# $BackendHealthUrl = 'http://127.0.0.1:8000/api/health'\n"
            "$dead = 'http://127.0.0.1:8000/api/health'\n"
            "$FrontendProbeUrl = 'http://example.invalid/create'\n"
        )
        with self.assertRaises(AssertionError):
            _assert_ps_ast_frozen_probe_url_assignments(self, bad)

    def test_invocation_operator_samples_fail(self) -> None:
        samples = (
            "& 'python.exe' -m uvicorn app.main:app",
            ". .\\helper.ps1",
            "& $python -m x",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(AssertionError):
                    _assert_ps_ast_no_invocation_operators(self, sample)

    def test_start_process_without_invocation_operator_ok(self) -> None:
        good = (
            "Start-Process -FilePath $py -ArgumentList $args "
            "-WorkingDirectory $wd -WindowStyle Hidden\n"
        )
        _assert_ps_ast_no_invocation_operators(self, good)

    def test_io_file_write_samples_fail(self) -> None:
        samples = (
            "[IO.File]::WriteAllText('tmp/dev-start-status.json','{}')",
            "[System.IO.File]::WriteAllBytes('x',[byte[]](1))",
            "[IO.File]::CreateText('tmp/dev-start-status.json')",
            "[System.IO.File]::OpenWrite('tmp/dev-start-status.json')",
            "[IO.File]::Create('tmp/dev-start-status.json')",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaises(AssertionError):
                    _assert_ps_ast_no_io_file_direct_writes(self, sample)

    def test_setcontent_temp_then_moveitem_not_flagged_as_io_file(self) -> None:
        """正确形态：Set-Content 写同目录临时文件后 Move-Item，不得误伤为 IO.File 直写。"""
        good = (
            "$final = Join-Path $tmpDir 'dev-start-status.json'\n"
            "$partial = Join-Path $tmpDir ('dev-start-status.' + [guid]::NewGuid().ToString('N') + '.tmp')\n"
            "Set-Content -LiteralPath $partial -Value $json -Encoding utf8\n"
            "Move-Item -LiteralPath $partial -Destination $final -Force\n"
        )
        _assert_ps_ast_no_io_file_direct_writes(self, good)
        _assert_ps_ast_no_invocation_operators(self, good)
        facts = _ps_ast_inspect(good)
        self.assertEqual(facts.get("io_file_writes"), [])
        self.assertEqual(facts.get("invocation_ops"), [])

    def test_file_replace_ast_accepts_legal_atomic_roles(self) -> None:
        """
        合法有终稿方案：
        File.Replace($tempPath, $StatusFinal, [NullString]::Value)
        参数角色通过、总数精确 1，且不进直写禁门。
        """
        good = (
            "$StatusFinal = Join-Path $StatusDir 'dev-start-status.json'\n"
            "$tempPath = Join-Path $StatusDir ('dev-start-status.' + [guid]::NewGuid().ToString('N') + '.wip')\n"
            "Set-Content -LiteralPath $tempPath -Value $json -Encoding utf8\n"
            "if (Test-Path -LiteralPath $StatusFinal) {\n"
            "  [System.IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value)\n"
            "} else {\n"
            "  Move-Item -LiteralPath $tempPath -Destination $StatusFinal -Force\n"
            "}\n"
        )
        _assert_ps_ast_file_replace_for_status(self, good)
        _assert_ps_ast_no_io_file_direct_writes(self, good)
        facts = _ps_ast_inspect(good)
        self.assertEqual(facts.get("io_file_writes"), [])
        replaces_fact = facts.get("io_file_replaces")
        if replaces_fact is None:
            replaces_list: list[Any] = []
        elif isinstance(replaces_fact, list):
            replaces_list = replaces_fact
        else:
            replaces_list = [replaces_fact]
        self.assertEqual(len(replaces_list), 1)
        only = replaces_list[0]
        self.assertIsInstance(only, dict)
        args_raw = only.get("arguments")
        if args_raw is None:
            args: list[Any] = []
        elif isinstance(args_raw, list):
            args = args_raw
        else:
            args = [args_raw]
        self.assertEqual(len(args), 3)
        self.assertEqual(
            _normalize_ps_arg_text(str(args[2])).casefold(),
            _FILE_REPLACE_STATUS_BAK_ARG.casefold(),
        )

    def test_file_replace_ast_rejects_comment_or_dead_string(self) -> None:
        """注释/死字符串中的 Replace 不得充当 AST 主证据。"""
        bad = (
            "# [System.IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value)\n"
            "$dead = '[System.IO.File]::Replace'\n"
            "Remove-Item -Path $StatusFinal -Force\n"
            "Move-Item -LiteralPath $tempPath -Destination $StatusFinal -Force\n"
        )
        with self.assertRaises(AssertionError) as ctx:
            _assert_ps_ast_file_replace_for_status(self, bad)
        msg = str(ctx.exception)
        self.assertRegex(
            msg,
            r"Replace|精确一处|总数",
            f"首红应指向缺少/总数非法的 Replace，实际：{msg!r}",
        )

    def test_file_replace_ast_rejects_wrong_backup_arg(self) -> None:
        """
        第三参非 [NullString]::Value 的 Replace 不得过门。
        字面 $null 为 wrong-backup 主反例（PS5.1 绑成空串，语义不等于无备份调用）。
        """
        # 主反例：字面 $null（原合法第三参，现必须红）
        bad_null = (
            f"[IO.File]::Replace($tempPath, $StatusFinal, {_FILE_REPLACE_STATUS_WRONG_BAK_NULL})\n"
        )
        with self.assertRaises(AssertionError) as ctx_null:
            _assert_ps_ast_file_replace_for_status(self, bad_null)
        msg_null = str(ctx_null.exception)
        self.assertRegex(
            msg_null,
            r"NullString|参数角色|legal",
            f"$null 第三参必须红，实际：{msg_null!r}",
        )
        # 变量备份路径亦必须红
        bad_path = (
            "[IO.File]::Replace($tempPath, $StatusFinal, $backupPath)\n"
        )
        with self.assertRaises(AssertionError):
            _assert_ps_ast_file_replace_for_status(self, bad_path)

    def test_file_replace_ast_rejects_wrong_source_arg(self) -> None:
        """第一参非 $tempPath 的无关 Replace 不得充当状态终稿主证据。"""
        bad = (
            "[IO.File]::Replace($unrelatedA, $StatusFinal, [NullString]::Value)\n"
        )
        with self.assertRaises(AssertionError):
            _assert_ps_ast_file_replace_for_status(self, bad)

    def test_file_replace_ast_rejects_wrong_destination_arg(self) -> None:
        """第二参非 $StatusFinal 的无关 Replace 不得充当状态终稿主证据。"""
        bad = (
            "[IO.File]::Replace($tempPath, $unrelatedB, [NullString]::Value)\n"
        )
        with self.assertRaises(AssertionError):
            _assert_ps_ast_file_replace_for_status(self, bad)

    def test_file_replace_ast_rejects_extra_fourth_arg(self) -> None:
        """第四参存在时参数数 !=3，不得充当合法 File.Replace 证据。"""
        bad = (
            "[IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value, $extra)\n"
        )
        with self.assertRaises(AssertionError):
            _assert_ps_ast_file_replace_for_status(self, bad)

    def test_file_replace_ast_rejects_dual_replace_null_then_nullstring(self) -> None:
        """
        双 Replace 反例（对应当前 A 生产 try/$null + catch/NullString）：
        即使含一处合法 NullString，总调用数 !=1 必须业务红，禁止条件放行。
        """
        bad = (
            "$StatusFinal = Join-Path $StatusDir 'dev-start-status.json'\n"
            "$tempPath = Join-Path $StatusDir ('dev-start-status.' + [guid]::NewGuid().ToString('N') + '.wip')\n"
            "try {\n"
            "  [System.IO.File]::Replace($tempPath, $StatusFinal, $null)\n"
            "} catch {\n"
            "  [System.IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value)\n"
            "}\n"
        )
        facts = _ps_ast_inspect(bad)
        replaces_fact = facts.get("io_file_replaces")
        if replaces_fact is None:
            replaces_list: list[Any] = []
        elif isinstance(replaces_fact, list):
            replaces_list = replaces_fact
        else:
            replaces_list = [replaces_fact]
        self.assertEqual(
            len(replaces_list),
            2,
            f"样例须先产出两处 Replace AST 以便断言总数门：{replaces_list!r}",
        )
        with self.assertRaises(AssertionError) as ctx:
            _assert_ps_ast_file_replace_for_status(self, bad)
        msg = str(ctx.exception)
        self.assertRegex(
            msg,
            r"精确一处|总数|==\s*1|当前总数",
            f"双 Replace 首红应指向总数精确 1，实际：{msg!r}",
        )
        # 双 Replace 形态不得被误判为 IO.File 直写
        _assert_ps_ast_no_io_file_direct_writes(self, bad)

    def test_remove_then_move_without_replace_is_not_legal_ast(self) -> None:
        """反假绿样例：先删终稿再 Move-Item，AST 无 Replace → 必须红。"""
        bad = (
            "if (Test-Path -Path $StatusFinal) {\n"
            "  Remove-Item -Path $StatusFinal -Force -ErrorAction Stop\n"
            "}\n"
            "Move-Item -Path $tempPath -Destination $StatusFinal -Force\n"
        )
        with self.assertRaises(AssertionError) as ctx:
            _assert_ps_ast_file_replace_for_status(self, bad)
        self.assertIn("Replace", str(ctx.exception))
        # 同时该形态不得被误判为 IO.File 直写
        _assert_ps_ast_no_io_file_direct_writes(self, bad)


class TestUnchangedStopHostPortsEvidence(unittest.TestCase):
    """
    Stop：UTF-8 BOM + PS5.1 ParseFile（V1-Q 取消固定 SHA）；
    探测 URL 走 PowerShell AST 赋值门；host/port 主证据为受控 Start-Process capture。
    """

    def test_stop_ps1_utf8_bom_and_ps51_parse_zero_errors(self) -> None:
        """Stop 必须存在、UTF-8 BOM（EF-BB-BF）、powershell Parser.ParseFile errors=0。"""
        self.assertTrue(_STOP_PS1.is_file(), f"Stop 缺失：{_STOP_PS1}")
        raw = _STOP_PS1.read_bytes()
        self.assertTrue(
            raw.startswith(_BOM),
            "Stop-Biaoshu-Dev.ps1 必须 UTF-8 BOM（EF-BB-BF）",
        )
        # 禁止预填 future production hash：此处只做 BOM/Parse，不锁 SHA
        src_text = Path(__file__).read_text(encoding="utf-8")
        frozen_hash_const = "_EXPECTED_" + "STOP_SHA256"
        self.assertNotIn(frozen_hash_const, src_text)
        # 拆分字面量，避免本断言自命中
        legacy_sha = (
            "5f7e2f774c0529dc12ca2477fd982538"
            + "243d1febfb0087797bcb2af9d8e9c23c"
        )
        self.assertNotIn(legacy_sha, src_text)
        ps_path = str(_STOP_PS1).replace("'", "''")
        cmd = (
            "$e=$null; $t=$null; "
            "[void][System.Management.Automation.Language.Parser]::"
            f"ParseFile('{ps_path}', [ref]$t, [ref]$e); "
            "if ($e -and @($e).Count -gt 0) { "
            "@($e) | ForEach-Object { $_.ToString() }; exit 1 "
            "} else { Write-Output ('PARSE_ERRORS=' + @($e).Count); exit 0 }"
        )
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                cmd,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        self.assertEqual(
            proc.returncode,
            0,
            f"Stop PS5.1 ParseFile 必须 errors 精确 0：out={out!r} err={err!r}",
        )
        self.assertIn("PARSE_ERRORS=0", out)

    def test_frozen_loopback_ports_constants(self) -> None:
        """
        常量自检 + PowerShell AST 冻结探测 URL 变量赋值。
        禁止去字符串后扫 host/URL（会与正确字面量赋值矛盾）。
        host/port 主证据见 test_start_command_capture_loopback_backend_frontend。
        """
        self.assertEqual(_EXPECTED_HOST, "127.0.0.1")
        self.assertEqual(_EXPECTED_BACKEND_PORT, 8000)
        self.assertEqual(_EXPECTED_FRONTEND_PORT, 5173)
        self.assertEqual(
            _EXPECTED_BACKEND_HEALTH_URL,
            "http://127.0.0.1:8000/api/health",
        )
        self.assertEqual(
            _EXPECTED_FRONTEND_PROBE_URL,
            "http://127.0.0.1:5173/create",
        )
        src = _require_true_source()
        text = src.read_text(encoding="utf-8-sig")
        # 变量名与值一并冻结；排除注释/孤立死字符串
        _assert_ps_ast_frozen_probe_url_assignments(self, text)

    def test_start_command_capture_loopback_backend_frontend(self) -> None:
        """
        受控 start-command capture（主证据）：
        - 组合副作用计数 bootstrap；Start-Process 专用 capture；
        - 其余 Stop/taskkill/IWR/IRM/curl/wget trace 后抛错且计数=0；
        - backend/frontend 各恰好 1 次 capture；
        - WindowStyle 精确 Hidden；WorkingDirectory 精确 TEMP backend/frontend；
        - backend FilePath 精确 TEMP venv python.exe，ArgumentList 精确 uvicorn 命令；
        - frontend FilePath 精确 npm/npm.cmd（TEMP 可解析假 npm.cmd），
          ArgumentList 精确 run dev -- --host 127.0.0.1 --port 5173。
        生产未实现时保持业务红，不得为测试生成伪生产。
        """
        _require_all_prod_for_behavior()
        tr = _TempRepo(with_full_prereqs=True)
        try:
            script = tr.true_source()
            # TEMP 可解析假 npm.cmd，供 frontend FilePath 精确比较
            shim_dir = tr.root / "tools" / "v1k-shims"
            shim_dir.mkdir(parents=True, exist_ok=True)
            npm_cmd = shim_dir / "npm.cmd"
            npm_cmd.write_text(
                "@echo off\r\nrem v1k-fake-npm-no-real-side-effect\r\n",
                encoding="utf-8",
            )
            run_env = os.environ.copy()
            run_env["PATH"] = str(shim_dir.resolve()) + os.pathsep + run_env.get(
                "PATH", ""
            )

            # backend
            _proc_b, caps_b, counts_b = _run_ps1_with_start_process_capture(
                script,
                ["-Component", "backend"],
                cwd=tr.root,
                env=run_env,
            )
            self.assertEqual(len(caps_b), 1, f"backend captures 必须恰好 1：{caps_b!r}")
            _assert_start_capture_no_other_side_effects(self, counts_b)
            self.assertEqual(
                counts_b.get("Start-Process", 0),
                1,
                f"backend Start-Process 计数必须为 1：{counts_b}",
            )
            _assert_exact_backend_start_capture(self, caps_b[0], tr.root)

            # frontend（分开执行）
            _proc_f, caps_f, counts_f = _run_ps1_with_start_process_capture(
                script,
                ["-Component", "frontend"],
                cwd=tr.root,
                env=run_env,
            )
            self.assertEqual(len(caps_f), 1, f"frontend captures 必须恰好 1：{caps_f!r}")
            _assert_start_capture_no_other_side_effects(self, counts_f)
            self.assertEqual(
                counts_f.get("Start-Process", 0),
                1,
                f"frontend Start-Process 计数必须为 1：{counts_f}",
            )
            _assert_exact_frontend_start_capture(self, caps_f[0], tr.root, npm_cmd)
        finally:
            tr.cleanup()

    def test_true_source_static_no_bypass_side_channels(self) -> None:
        """
        辅助静态门：去注释旁路 + AST 拒绝调用/点源运算符与 IO.File 直写。
        """
        src = _require_true_source()
        text = src.read_text(encoding="utf-8-sig")
        _assert_true_source_no_bypass_static(self, text)

    def test_this_module_is_only_intended_writable_test(self) -> None:
        self.assertEqual(Path(__file__).name, "test_start_biaoshu_dev.py")
        self.assertEqual(
            Path(__file__).resolve(),
            (_HERE / "test_start_biaoshu_dev.py").resolve(),
        )

    def test_self_no_skip_xfail_sleep_or_wide_or_residue(self) -> None:
        """
        扫描本文件：不得残留 skip/xfail、固定 sleep；
        任意 Assert / self.assert* 参数树不得含 ast.BoolOp(Or)。
        """
        source = Path(__file__).read_text(encoding="utf-8")
        lines = source.splitlines()
        bad: list[str] = []
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "test_self_no_skip_xfail_sleep_or_wide_or_residue" in line:
                continue
            if stripped.startswith("@unittest.skip") or stripped.startswith(
                "@pytest.mark.skip"
            ) or stripped.startswith("@pytest.mark.xfail"):
                bad.append(f"{idx}:skip-decorator:{stripped}")
                continue
            if re.search(r"(?<!['\"])\bpytest\.skip\s*\(", line) and "assertNot" not in line:
                bad.append(f"{idx}:pytest.skip:{stripped}")
            if re.search(r"(?<!['\"])\bunittest\.skip\s*\(", line) and "assertNot" not in line:
                bad.append(f"{idx}:unittest.skip:{stripped}")
            if re.search(r"\btime\.sleep\s*\(", line) and "assertNot" not in line:
                bad.append(f"{idx}:time.sleep:{stripped}")
        # AST 禁止任何断言参数树中的 BoolOp Or（不得行正则假绿）
        bad.extend(_assert_self_no_assert_boolop_or(source))
        self.assertEqual(bad, [], f"不得残留 skip/xfail/sleep/宽 or：{bad}")


# ===========================================================================
# 额外：旧入口静态第二套算法门（辅助，failure-first 时与行为红并存）
# ===========================================================================


class TestLegacyEntryStaticGate(unittest.TestCase):
    """
    额外静态门：五入口委托真源后不得保留第二套算法特征。
    不能替代 TEMP 行为转发用例。
    """

    def test_entries_should_not_keep_legacy_second_algorithm(self) -> None:
        _require_five_entries()
        # 真源存在后才强制薄委托静态检查
        _require_true_source()
        true_name = "Start-Biaoshu-Dev.ps1"
        for path in (
            _ROOT_START_BAT,
            _ROOT_START_PS1,
            _ROOT_UI_BAT,
            _BACKEND_RUN_BAT,
            _FRONTEND_RUN_BAT,
        ):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertIn(
                true_name,
                text,
                f"{path.name} 必须委托唯一真源 {true_name}",
            )
            lowered = text.lower()
            # 旧根 PS1 的 Read-Host / 开浏览器不得保留
            if path.suffix.lower() == ".ps1":
                self.assertNotIn("read-host", lowered, f"{path.name} 不得 Read-Host")
            # bat 不得 cmd /k 常驻
            self.assertNotIn("cmd /k", lowered, f"{path.name} 不得 cmd /k")


if __name__ == "__main__":
    unittest.main(verbosity=2)
