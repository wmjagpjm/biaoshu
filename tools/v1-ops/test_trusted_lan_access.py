# -*- coding: utf-8 -*-
"""
模块：V1-L 可信内网访问 failure-first 专项测试
用途：在系统 TEMP 假仓 + 严格 listener/probe/process/auth 快照 + PlanOnly/
      DiagnoseOnly/受控 start-capture + Vite 结构化模块加载下，验证 LAN 显式
      opt-in、RFC1918、required 握手、绑定/代理/Host 白名单与 V1-K 七键隐私。
对接：tools/v1-ops/Start-Biaoshu-Dev.ps1、frontend/vite.config.ts；
      docs/v1l-trusted-lan-access-contract.md §3–§10。
二次开发：禁止真实 Start-Process/端口/HTTP/DB/uploads/防火墙/浏览器/联网；
         禁止 skip/xfail、宽泛 or、固定 sleep、条件跳过、README/宽正则假证明；
         生产未改时必须因 LAN 能力缺失形成真实业务红（收集/依赖/编码失败不算红）。
"""

from __future__ import annotations

import ast
import base64
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
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径锚定
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_TRUE_SOURCE = _HERE / "Start-Biaoshu-Dev.ps1"
_STOP_PS1 = _HERE / "Stop-Biaoshu-Dev.ps1"
_VITE_CONFIG = _REPO_ROOT / "frontend" / "vite.config.ts"

_BOM = b"\xef\xbb\xbf"
_STATUS_REL = Path("tmp") / "dev-start-status.json"

# 契约 §6：状态仍精确七键
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

# V1-K 既有 code + V1-L 契约 §3 新增固定失败 code
# lan_auth_required 无独立可达语义，禁止保留
_V1L_NEW_CODES = frozenset(
    {
        "listen_profile_invalid",
        "lan_host_required",
        "lan_host_invalid",
        "lan_backend_auth_unverified",
        "lan_admin_not_bootstrapped",
        "lan_api_base_invalid",
    }
)
_ALLOWED_CODES = frozenset(
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
        "plan",
        "not_selected",
        *_V1L_NEW_CODES,
    }
)

_SIDE_EFFECT_MARKER = "V1L_SIDE_EFFECT_COUNTS="
_START_PROCESS_CAPTURE_MARKER = "V1L_START_PROCESS_CAPTURE="
_HTTP_EVENT_MARKER = "V1L_HTTP_EVENT_CAPTURE="
_TCP_QUERY_MARKER = "V1L_TCP_QUERY_CAPTURE="
_ENV_BRIDGE_MARKER = "V1L_ENV_BRIDGE_CAPTURE="
_UNIFIED_TRACE_MARKER = "V1L_UNIFIED_TRACE_CAPTURE="
_SIDE_EFFECT_FORBIDDEN_PREFIX = "V1L_SIDE_EFFECT_FORBIDDEN:"
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

# 受控环境键：Vite 加载与普通 runner 默认无条件清理，再注入显式 env
_CONTROLLED_ENV_KEYS = (
    "BIAOSHU_LISTEN_PROFILE",
    "BIAOSHU_LAN_HOST",
    "VITE_API_BASE_URL",
    "VITE_API_PROXY_TARGET",
)

# helper 自检用例名（不计业务覆盖）
_HELPER_SELF_CHECK_TESTS = frozenset(
    {
        "test_capture_records_vite_api_base_presence_true",
        "test_capture_records_vite_api_base_presence_false_when_unset",
    }
)

# 契约冻结 URL / 方法（注入观测，零 live HTTP）
_EXPECTED_BACKEND_HEALTH_URL = "http://127.0.0.1:8000/api/health"
_EXPECTED_AUTH_BOOTSTRAP_URL = "http://127.0.0.1:8000/api/auth/bootstrap-status"
_EXPECTED_AUTH_HTTP_METHOD = "GET"
_AUTH_SNAPSHOT_KEYS = ("port", "httpStatus", "authRequired", "bootstrapped")

# 契约拓扑常量
_EXPECTED_BACKEND_HOST = "127.0.0.1"
_EXPECTED_BACKEND_PORT = 8000
_EXPECTED_FRONTEND_PORT = 5173
_EXPECTED_PROXY_TARGET = "http://127.0.0.1:8000"
_EXPECTED_WINDOW_STYLE = "Hidden"
_VALID_LAN_HOST = "192.168.1.20"
_EXPECTED_BACKEND_START_ARGS_LOOPBACK = (
    "-m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
)
_EXPECTED_FRONTEND_START_ARGS_LOOPBACK = "run dev -- --host 127.0.0.1 --port 5173"
_EXPECTED_FRONTEND_START_ARGS_LAN = (
    f"run dev -- --host {_VALID_LAN_HOST} --port {_EXPECTED_FRONTEND_PORT}"
)

# Vite LAN 模式由启动真源注入的环境变量（生产实现契约；测试只读断言）
_ENV_LISTEN_PROFILE = "BIAOSHU_LISTEN_PROFILE"
_ENV_LAN_HOST = "BIAOSHU_LAN_HOST"

# 假敏感标记
_FAKE_PID_TOKEN = "PID_SHOULD_NOT_LEAK_919191"
_FAKE_PATH_TOKEN = r"C:\Users\Administrator\secret-v1l-path-DO-NOT-LEAK"
_FAKE_KEY_TOKEN = "sk-fake-v1l-test-key-DO-NOT-LEAK"
_FAKE_IP_LEAK = "203.0.113.77"
_SENSITIVE_SUBSTRINGS = (
    _FAKE_KEY_TOKEN,
    _FAKE_PATH_TOKEN,
    _FAKE_PID_TOKEN,
    _FAKE_IP_LEAK,
    "API_KEY",
    "Authorization",
    "Set-Cookie",
    "uploads\\",
    "uploads/",
    "biaoshu.db",
    "Exception:",
    "Traceback",
)

# 严格白名单（本轮 test-only 返修唯一可写）
_WRITABLE_REL = (
    "tools/v1-ops/test_trusted_lan_access.py",
)

# Stop 哈希：证明本测试不得改 Stop（与 V1-K 冻结一致）
_EXPECTED_STOP_SHA256 = (
    "5f7e2f774c0529dc12ca2477fd982538243d1febfb0087797bcb2af9d8e9c23c"
)

# 非法 host / profile 样例（fail-closed）
_INVALID_PROFILES = (
    "",
    " ",
    "LANN",
    "public",
    "0.0.0.0",
    "true",
    "loopback,lan",
    "../x",
)
_INVALID_LAN_HOSTS = (
    "",
    " ",
    "localhost",
    "example.com",
    "0.0.0.0",
    "127.0.0.1",
    "127.1.2.3",
    "::1",
    "2001:db8::1",
    "::ffff:192.168.1.20",
    "169.254.1.1",
    "224.0.0.1",
    "100.64.0.1",
    "8.8.8.8",
    "1.1.1.1",
    "192.168.1.20:5173",
    "http://192.168.1.20",
    "https://192.168.1.20/x",
    "192.168.1.20/24",
    "192.168.1.20 ",
    " 192.168.1.20",
    "172.15.0.1",
    "172.32.0.1",
    "10.0.0.1\n",
    "192.168.1.20;whoami",
    "255.255.255.255",
)
_VALID_LAN_HOSTS = (
    "10.0.0.1",
    "10.255.255.254",
    "172.16.0.1",
    "172.31.255.1",
    "192.168.0.1",
    "192.168.255.254",
    _VALID_LAN_HOST,
)


# ===========================================================================
# 工具
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
    if not _TRUE_SOURCE.is_file():
        raise AssertionError(f"生产真源缺失（业务红）：{_TRUE_SOURCE}")
    return _TRUE_SOURCE


def _require_vite_config() -> Path:
    if not _VITE_CONFIG.is_file():
        raise AssertionError(f"vite.config.ts 缺失（业务红）：{_VITE_CONFIG}")
    return _VITE_CONFIG


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_status(repo_root: Path) -> dict[str, Any]:
    path = repo_root / _STATUS_REL
    if not path.is_file():
        raise AssertionError(f"状态侧车缺失：{path}")
    raw = path.read_bytes()
    if raw.startswith(_BOM):
        raise AssertionError("状态侧车不得带 UTF-8 BOM")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"状态必须为对象：{type(data)}")
    return data


def _assert_no_sensitive(tc: unittest.TestCase, text: str, where: str) -> None:
    lower = text.lower()
    for tok in _SENSITIVE_SUBSTRINGS:
        needle = tok.lower() if tok.isascii() else tok
        hay = lower if tok.isascii() else text
        tc.assertNotIn(needle, hay, f"{where} 不得含敏感子串 {tok!r}")
    tc.assertNotRegex(text, r"(?i)\bpid\s*[=:：]\s*\d+", f"{where} 不得含 PID 赋值")
    tc.assertNotRegex(text, r"[A-Za-z]:\\", f"{where} 不得含盘符绝对路径")
    tc.assertNotRegex(text, r"\\\\[^\s\"']+", f"{where} 不得含 UNC")


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
    tc.assertRegex(
        ts,
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
        f"updatedAtUtc 必须严格 UTC Z：{ts!r}",
    )
    if "." in ts:
        base, frac = ts[:-1].split(".", 1)
        datetime.strptime(base + "+0000", "%Y-%m-%dT%H:%M:%S%z")
        tc.assertTrue(frac.isdigit(), f"小数秒必须为数字：{ts!r}")
    else:
        datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    tc.assertEqual(status["mode"], mode)
    tc.assertIn(status["mode"], _ALLOWED_MODE)
    tc.assertEqual(status["component"], component)
    tc.assertIn(status["component"], _ALLOWED_COMPONENT)
    tc.assertIn(status["overall"], _ALLOWED_OVERALL)
    tc.assertIsInstance(status["code"], str)
    tc.assertTrue(bool(status["code"].strip()), "顶层 code 不得为空")
    tc.assertIn(status["code"], _ALLOWED_CODES, f"非法顶层 code：{status['code']!r}")
    services = status["services"]
    tc.assertIsInstance(services, dict)
    tc.assertEqual(sorted(services.keys()), sorted(_SERVICE_KEYS))
    for sk in _SERVICE_KEYS:
        entry = services[sk]
        tc.assertIsInstance(entry, dict)
        tc.assertEqual(sorted(entry.keys()), sorted(_SERVICE_ENTRY_KEYS))
        tc.assertIn(entry["state"], _ALLOWED_SERVICE_STATE)
        tc.assertIsInstance(entry["code"], str)
        tc.assertTrue(bool(str(entry["code"]).strip()))
        tc.assertIn(entry["code"], _ALLOWED_CODES)


def _assert_status_no_privacy_leak(tc: unittest.TestCase, status: dict[str, Any]) -> None:
    dumped = json.dumps(status, ensure_ascii=False)
    _assert_no_sensitive(tc, dumped, "status-json")
    # 契约：不得塞入 IP/URL/PID/路径/argv
    tc.assertNotRegex(dumped, r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "状态不得含 IPv4")
    tc.assertNotRegex(dumped, r"https?://", "状态不得含 URL")
    tc.assertNotIn("argv", dumped.lower())
    tc.assertNotIn("listenprofile", dumped.lower())
    tc.assertNotIn("lanhost", dumped.lower())
    tc.assertNotIn("allowedhosts", dumped.lower())


def _ps_arg_list_literal(args: list[str]) -> str:
    parts = ["'" + str(a).replace("'", "''") + "'" for a in args]
    return "@(" + ",".join(parts) + ")" if parts else "@()"


def _ps_side_effect_counter_bootstrap() -> str:
    keys_ps = "; ".join(f"'{k}' = 0" for k in _SIDE_EFFECT_KEYS)
    parts = [
        "$ErrorActionPreference = 'Continue'; ",
        f"$script:__V1K_SC = @{{ {keys_ps} }}; ",
        f"$script:__V1L_SC = $script:__V1K_SC; ",
    ]
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
    for name in ("taskkill", "taskkill.exe", "curl", "curl.exe", "wget", "wget.exe"):
        parts.append(
            f"function global:{name} {{ "
            f"param([Parameter(ValueFromRemainingArguments=$true)]$Rest) "
            f"$script:__V1K_SC['{name}']++; "
            f"throw '{_SIDE_EFFECT_FORBIDDEN_PREFIX}{name}' "
            f"}}; "
        )
    return "".join(parts)


def _parse_json_marker_line(combined: str, marker: str) -> Any:
    for line in (combined or "").splitlines():
        line = line.strip()
        if not line.startswith(marker):
            continue
        raw = line[len(marker) :].strip()
        return json.loads(raw)
    raise AssertionError(f"未找到标记 {marker!r}；输出片段：{(combined or '')[:500]!r}")


def _parse_side_effect_counts(combined: str) -> dict[str, int]:
    data = _parse_json_marker_line(combined, _SIDE_EFFECT_MARKER)
    if not isinstance(data, dict):
        raise AssertionError(f"副作用计数必须为对象：{data!r}")
    out: dict[str, int] = {}
    for key in _SIDE_EFFECT_KEYS:
        if key not in data:
            raise AssertionError(f"副作用计数缺键 {key!r}")
        val = data[key]
        if not isinstance(val, int) or isinstance(val, bool):
            raise AssertionError(f"副作用计数 {key!r} 必须为 int：{val!r}")
        out[key] = val
    return out


def _assert_zero_side_effect_counts(
    tc: unittest.TestCase, counts: dict[str, int]
) -> None:
    for key in _SIDE_EFFECT_KEYS:
        tc.assertEqual(counts[key], 0, f"精确零调用失败：{key}={counts[key]} 全量={counts}")


def _run_ps1_guarded(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
    """PlanOnly/DiagnoseOnly：副作用覆盖 + 精确计数。"""
    joined = " ".join(args).lower()
    if "-planonly" not in joined and "-diagnoseonly" not in joined:
        raise AssertionError("guarded 仅允许 PlanOnly/DiagnoseOnly")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    command = (
        _ps_side_effect_counter_bootstrap()
        + f"$__v1l_args = {args_ps}; "
        + f"& '{ps1_lit}' @__v1l_args; "
        + "$__v1l_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }; "
        + f"Write-Output ('{_SIDE_EFFECT_MARKER}' + ($script:__V1K_SC | ConvertTo-Json -Compress)); "
        + "exit $__v1l_code"
    )
    run_env = _build_run_env(env)
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
    counts = _parse_side_effect_counts((proc.stdout or "") + "\n" + (proc.stderr or ""))
    return proc, counts


def _ps_start_process_capture_body() -> str:
    """
    Start-Process 捕获体：记录环境存在性与值，首次后抛错阻断真实派生。
    若脚本侧已初始化 $script:__V1L_SEQ / __V1L_TRACE，则写入统一单调序号。
    """
    return (
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
        "  $vitePresent = $null -ne (Get-Item -LiteralPath Env:VITE_API_BASE_URL "
        "    -ErrorAction SilentlyContinue); "
        "  $seq = $null; "
        "  if ($null -ne $script:__V1L_SEQ) { "
        "    $script:__V1L_SEQ = [int]$script:__V1L_SEQ + 1; "
        "    $seq = [int]$script:__V1L_SEQ; "
        "    if ($null -ne $script:__V1L_TRACE) { "
        "      $script:__V1L_TRACE.Add(@{ "
        "        kind = 'start'; "
        "        seq = $seq; "
        "        FilePath = [string]$FilePath; "
        "        ArgumentList = $argText "
        "      }) | Out-Null "
        "    } "
        "  }; "
        "  $script:__V1K_SP.Add(@{ "
        "    FilePath = [string]$FilePath; "
        "    ArgumentList = $argText; "
        "    WorkingDirectory = [string]$WorkingDirectory; "
        "    WindowStyle = if ($null -eq $WindowStyle) { '' } else { [string]$WindowStyle }; "
        "    EnvAuthMode = [string]$env:AUTH_MODE; "
        "    EnvListenProfile = [string]$env:BIAOSHU_LISTEN_PROFILE; "
        "    EnvLanHost = [string]$env:BIAOSHU_LAN_HOST; "
        "    EnvViteApiBase = if ($vitePresent) { [string]$env:VITE_API_BASE_URL } else { $null }; "
        "    EnvViteApiBasePresent = [bool]$vitePresent; "
        "    seq = $seq "
        "  }) | Out-Null; "
        "  throw 'V1L_START_PROCESS_CAPTURED' "
        "}; "
    )


def _ps_empty_live_stubs() -> str:
    """非 inject 路径：默认空 live 源；inject 路径用 _ps_cim_process_meta_stub 覆盖 Get-CimInstance。"""
    return (
        "function global:Get-NetUDPEndpoint { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) @() }; "
        "function global:Get-Process { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) @() }; "
        "function global:Get-CimInstance { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) @() }; "
        "function global:Get-WmiObject { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) @() }; "
        "function global:netstat { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) '' }; "
        "function global:netstat.exe { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) '' }; "
    )


def _ps_cim_process_meta_stub() -> str:
    """
    严格按 PID 的 Get-CimInstance Win32_Process 注入。
    生产 Get-LiveListenerRecords 只拿 TCP.OwningProcess，再走真实 Cim 路径取
    ExecutablePath/CommandLine；禁止把假字段塞进 Get-NetTCPConnection 输出。
    """
    return (
        "function global:Get-CimInstance { "
        "  [CmdletBinding()] "
        "  param( "
        "    $ClassName, "
        "    $Class, "
        "    $Filter, "
        "    [Parameter(ValueFromRemainingArguments=$true)]$Rest "
        "  ) "
        "  $cn = if ($null -ne $ClassName -and [string]$ClassName -ne '') { "
        "    [string]$ClassName } else { [string]$Class }; "
        "  if ($cn -ne 'Win32_Process') { return @() }; "
        "  $pidVal = $null; "
        "  if ($null -ne $Filter -and ([string]$Filter -match 'ProcessId\\s*=\\s*(\\d+)')) { "
        "    $pidVal = [int]$Matches[1] "
        "  }; "
        "  if ($null -eq $pidVal) { return @() }; "
        "  foreach ($row in @($script:__V1L_PROC_META)) { "
        "    if ([int]$row.ProcessId -eq $pidVal) { "
        "      return @([pscustomobject]@{ "
        "        ProcessId = [int]$row.ProcessId; "
        "        ExecutablePath = [string]$row.ExecutablePath; "
        "        CommandLine = [string]$row.CommandLine "
        "      }) "
        "    } "
        "  }; "
        "  return @() "
        "}; "
    )


def _process_meta_from_tcp_rows(
    tcp_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """从测试夹具 TCP 行抽取 PID 元数据，供 Get-CimInstance 注入（不回传给 TCP）。"""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in tcp_rows or []:
        raw_pid = row.get("OwningProcess", row.get("pid"))
        if raw_pid is None:
            continue
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if pid in seen:
            continue
        exe = row.get("executablePath", row.get("ExecutablePath", ""))
        cmd = row.get("commandLine", row.get("CommandLine", ""))
        if not str(exe).strip() and not str(cmd).strip():
            continue
        seen.add(pid)
        out.append(
            {
                "ProcessId": pid,
                "ExecutablePath": str(exe or ""),
                "CommandLine": str(cmd or ""),
            }
        )
    return out


def _build_run_env(env: dict[str, str] | None) -> dict[str, str]:
    """
    普通 runner 默认清四个受控键，再合并显式 env。
    桥接恢复用例通过 env 显式传入污染值；未设置 VITE 时 env 不含该键则保持清理。
    """
    run_env = os.environ.copy()
    for key in _CONTROLLED_ENV_KEYS:
        run_env.pop(key, None)
    if env:
        run_env.update(env)
    if env is not None and "VITE_API_BASE_URL" not in env:
        run_env.pop("VITE_API_BASE_URL", None)
    return run_env


def _run_ps1_start_capture(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], dict[str, int]]:
    """
    受控 start capture：记录 Start-Process 参数与当时 AUTH_MODE/BIAOSHU/VITE 环境，
    首次后抛错，禁止真实派生与 live 轮询。
    EnvViteApiBasePresent 区分「未设置」与「显式空串」。
    """
    joined = " ".join(args).lower()
    if "-planonly" in joined or "-diagnoseonly" in joined:
        raise AssertionError("start capture 仅用于 start 路径")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    command = (
        _ps_side_effect_counter_bootstrap()
        + "$script:__V1K_SP = New-Object System.Collections.Generic.List[object]; "
        + "function global:Get-NetTCPConnection { "
        "param([Parameter(ValueFromRemainingArguments=$true)]$Rest) @() }; "
        + _ps_empty_live_stubs()
        + _ps_start_process_capture_body()
        + "$script:__V1L_ENV_BEFORE = @{ "
        "  ListenProfilePresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LISTEN_PROFILE -ErrorAction SilentlyContinue); "
        "  ListenProfile = [string]$env:BIAOSHU_LISTEN_PROFILE; "
        "  LanHostPresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LAN_HOST -ErrorAction SilentlyContinue); "
        "  LanHost = [string]$env:BIAOSHU_LAN_HOST "
        "}; "
        + f"$__v1l_args = {args_ps}; "
        + f"try {{ & '{ps1_lit}' @__v1l_args }} catch {{ "
        "  $__msg = \"$_\"; "
        "  if ($__msg -notmatch 'V1L_START_PROCESS_CAPTURED' "
        f"      -and $__msg -notmatch '{_SIDE_EFFECT_FORBIDDEN_PREFIX}') {{ "
        "    Write-Error $_ "
        "  } "
        "}; "
        + "$script:__V1L_ENV_AFTER = @{ "
        "  ListenProfilePresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LISTEN_PROFILE -ErrorAction SilentlyContinue); "
        "  ListenProfile = [string]$env:BIAOSHU_LISTEN_PROFILE; "
        "  LanHostPresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LAN_HOST -ErrorAction SilentlyContinue); "
        "  LanHost = [string]$env:BIAOSHU_LAN_HOST "
        "}; "
        + "$__v1l_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }; "
        + f"Write-Output ('{_SIDE_EFFECT_MARKER}' + ($script:__V1K_SC | ConvertTo-Json -Compress)); "
        + f"Write-Output ('{_START_PROCESS_CAPTURE_MARKER}' + "
        "(ConvertTo-Json -Compress @($script:__V1K_SP.ToArray()))); "
        + f"Write-Output ('{_ENV_BRIDGE_MARKER}' + "
        "(ConvertTo-Json -Compress @{ before = $script:__V1L_ENV_BEFORE; after = $script:__V1L_ENV_AFTER })); "
        + "exit $__v1l_code"
    )
    run_env = _build_run_env(env)
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
            raise AssertionError(f"capture 项必须为对象：{item!r}")
        captures.append(dict(item))
    return proc, captures, counts


def _parse_env_bridge(combined: str) -> dict[str, Any]:
    data = _parse_json_marker_line(combined, _ENV_BRIDGE_MARKER)
    if not isinstance(data, dict):
        raise AssertionError(f"env bridge 必须为对象：{data!r}")
    for key in ("before", "after"):
        if key not in data or not isinstance(data[key], dict):
            raise AssertionError(f"env bridge 缺 {key} 对象：{data!r}")
    return data


def _run_ps1_start_inject(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 45,
    env: dict[str, str] | None = None,
    tcp_rows: list[dict[str, Any]] | None = None,
    http_map: dict[str, dict[str, Any]] | None = None,
    allow_multiple_starts: bool = False,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[dict[str, Any]],
    dict[str, int],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """
    受控 start 注入：零 live HTTP/端口。
    - Get-NetTCPConnection 仅回 LocalAddress/LocalPort/State/OwningProcess
    - Get-CimInstance Win32_Process 按 PID 回 ExecutablePath/CommandLine（真实归属路径）
    - HTTP 与 Start-Process 共享统一单调 seq/trace
    - Start-Process 捕获环境与参数；默认首次后抛错
    """
    joined = " ".join(args).lower()
    if "-planonly" in joined or "-diagnoseonly" in joined:
        raise AssertionError("start inject 仅用于 start 路径")
    if any(
        x in joined
        for x in (
            "-listenersnapshotjson",
            "-probesnapshotjson",
            "-processsnapshotjson",
            "-authsnapshotjson",
        )
    ):
        raise AssertionError("start 模式不得投稿 snapshot 参数（应用注入夹具）")

    # TCP 注入行不得把进程元数据伪装成 NetTCP 字段；元数据只进 Cim 表
    tcp_for_net: list[dict[str, Any]] = []
    for row in tcp_rows or []:
        tcp_for_net.append(
            {
                "LocalAddress": row.get("LocalAddress"),
                "LocalPort": row.get("LocalPort"),
                "State": row.get("State", "Listen"),
                "OwningProcess": row.get("OwningProcess"),
            }
        )
    proc_meta = _process_meta_from_tcp_rows(tcp_rows)

    tcp_json = json.dumps(tcp_for_net, ensure_ascii=False)
    http_json = json.dumps(http_map or {}, ensure_ascii=False)
    proc_json = json.dumps(proc_meta, ensure_ascii=False)
    tcp_b64 = base64.b64encode(tcp_json.encode("utf-8")).decode("ascii")
    http_b64 = base64.b64encode(http_json.encode("utf-8")).decode("ascii")
    proc_b64 = base64.b64encode(proc_json.encode("utf-8")).decode("ascii")
    ps1_lit = str(script_path.resolve()).replace("'", "''")
    args_ps = _ps_arg_list_literal(args)
    multi_flag = "$true" if allow_multiple_starts else "$false"

    command = (
        _ps_side_effect_counter_bootstrap()
        + "$script:__V1K_SP = New-Object System.Collections.Generic.List[object]; "
        + "$script:__V1L_HTTP = New-Object System.Collections.Generic.List[object]; "
        + "$script:__V1L_TCP = New-Object System.Collections.Generic.List[object]; "
        + "$script:__V1L_TRACE = New-Object System.Collections.Generic.List[object]; "
        + "$script:__V1L_SEQ = 0; "
        + f"$script:__V1L_TCP_ROWS = "
        f"[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{tcp_b64}')) "
        f"| ConvertFrom-Json; "
        + f"$script:__V1L_HTTP_MAP = "
        f"[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{http_b64}')) "
        f"| ConvertFrom-Json; "
        + f"$script:__V1L_PROC_META = @("
        f"[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{proc_b64}')) "
        f"| ConvertFrom-Json); "
        + "function global:Get-NetTCPConnection { "
        "  [CmdletBinding()] "
        "  param( "
        "    $LocalAddress, "
        "    $LocalPort, "
        "    $State, "
        "    [Parameter(ValueFromRemainingArguments=$true)]$Rest "
        "  ) "
        "  $script:__V1L_TCP.Add(@{ "
        "    LocalAddress = if ($null -eq $LocalAddress) { $null } else { [string]$LocalAddress }; "
        "    LocalPort = if ($null -eq $LocalPort) { $null } else { [int]$LocalPort }; "
        "    State = if ($null -eq $State) { $null } else { [string]$State } "
        "  }) | Out-Null; "
        "  $out = New-Object System.Collections.Generic.List[object]; "
        "  foreach ($row in @($script:__V1L_TCP_ROWS)) { "
        "    $ok = $true; "
        "    if ($PSBoundParameters.ContainsKey('LocalPort') -and "
        "        ([int]$row.LocalPort) -ne ([int]$LocalPort)) { $ok = $false }; "
        "    if ($ok -and $PSBoundParameters.ContainsKey('LocalAddress') -and "
        "        ([string]$row.LocalAddress) -ne ([string]$LocalAddress)) { $ok = $false }; "
        "    if ($ok -and $PSBoundParameters.ContainsKey('State') -and $null -ne $State -and "
        "        ([string]$row.State) -ne ([string]$State)) { $ok = $false }; "
        "    if ($ok) { "
        "      $out.Add([pscustomobject]@{ "
        "        LocalAddress = [string]$row.LocalAddress; "
        "        LocalPort = [int]$row.LocalPort; "
        "        State = if ($null -eq $row.State) { 'Listen' } else { [string]$row.State }; "
        "        OwningProcess = [int]$row.OwningProcess "
        "      }) | Out-Null "
        "    } "
        "  }; "
        "  return @($out.ToArray()) "
        "}; "
        + _ps_empty_live_stubs()
        + _ps_cim_process_meta_stub()
        + "function script:__V1L_RecordHttp([string]$Method, [string]$Uri) { "
        "  $script:__V1K_SC['Invoke-WebRequest']++; "
        "  $script:__V1L_SEQ = [int]$script:__V1L_SEQ + 1; "
        "  $seq = [int]$script:__V1L_SEQ; "
        "  $script:__V1L_HTTP.Add(@{ method = $Method; url = $Uri; "
        "    order = $script:__V1L_HTTP.Count; seq = $seq }) | Out-Null; "
        "  $script:__V1L_TRACE.Add(@{ kind = 'http'; method = $Method; "
        "    url = $Uri; seq = $seq }) | Out-Null; "
        "  $map = $script:__V1L_HTTP_MAP; "
        "  $hit = $null; "
        "  if ($null -ne $map) { "
        "    foreach ($p in $map.PSObject.Properties) { "
        "      if ([string]$p.Name -eq $Uri) { $hit = $p.Value; break } "
        "    } "
        "  }; "
        "  if ($null -eq $hit) { throw \"V1L_HTTP_UNMAPPED:$Method $Uri\" }; "
        "  $code = 200; if ($null -ne $hit.StatusCode) { $code = [int]$hit.StatusCode }; "
        "  $content = if ($null -eq $hit.Content) { '' } else { [string]$hit.Content }; "
        "  return [pscustomobject]@{ StatusCode = $code; Content = $content } "
        "}; "
        + "function global:Invoke-WebRequest { "
        "  [CmdletBinding()] param( "
        "    $Uri, $Method = 'GET', "
        "    [Parameter(ValueFromRemainingArguments=$true)]$Rest "
        "  ) "
        "  $m = if ($null -eq $Method -or [string]$Method -eq '') { 'GET' } else { "
        "    ([string]$Method).ToUpperInvariant() }; "
        "  return script:__V1L_RecordHttp -Method $m -Uri ([string]$Uri) "
        "}; "
        + "function global:Invoke-RestMethod { "
        "  [CmdletBinding()] param( "
        "    $Uri, $Method = 'GET', "
        "    [Parameter(ValueFromRemainingArguments=$true)]$Rest "
        "  ) "
        "  $m = if ($null -eq $Method -or [string]$Method -eq '') { 'GET' } else { "
        "    ([string]$Method).ToUpperInvariant() }; "
        "  $resp = script:__V1L_RecordHttp -Method $m -Uri ([string]$Uri); "
        "  try { return ($resp.Content | ConvertFrom-Json) } catch { return $resp.Content } "
        "}; "
        + (
            _ps_start_process_capture_body().replace(
                "throw 'V1L_START_PROCESS_CAPTURED'",
                (
                    f"if (-not {multi_flag}) {{ throw 'V1L_START_PROCESS_CAPTURED' }} "
                    "else { return [pscustomobject]@{ Id = 900000 + $script:__V1K_SC['Start-Process'] } }"
                ),
            )
        )
        + "$script:__V1L_ENV_BEFORE = @{ "
        "  ListenProfilePresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LISTEN_PROFILE -ErrorAction SilentlyContinue); "
        "  ListenProfile = [string]$env:BIAOSHU_LISTEN_PROFILE; "
        "  LanHostPresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LAN_HOST -ErrorAction SilentlyContinue); "
        "  LanHost = [string]$env:BIAOSHU_LAN_HOST "
        "}; "
        + f"$__v1l_args = {args_ps}; "
        + f"try {{ & '{ps1_lit}' @__v1l_args }} catch {{ "
        "  $__msg = \"$_\"; "
        "  if ($__msg -notmatch 'V1L_START_PROCESS_CAPTURED' "
        f"      -and $__msg -notmatch '{_SIDE_EFFECT_FORBIDDEN_PREFIX}' "
        "      -and $__msg -notmatch 'V1L_HTTP_UNMAPPED') { "
        "    Write-Error $_ "
        "  } "
        "}; "
        + "$script:__V1L_ENV_AFTER = @{ "
        "  ListenProfilePresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LISTEN_PROFILE -ErrorAction SilentlyContinue); "
        "  ListenProfile = [string]$env:BIAOSHU_LISTEN_PROFILE; "
        "  LanHostPresent = $null -ne (Get-Item -LiteralPath Env:BIAOSHU_LAN_HOST -ErrorAction SilentlyContinue); "
        "  LanHost = [string]$env:BIAOSHU_LAN_HOST "
        "}; "
        + "$__v1l_code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 1 }; "
        + f"Write-Output ('{_SIDE_EFFECT_MARKER}' + ($script:__V1K_SC | ConvertTo-Json -Compress)); "
        + f"Write-Output ('{_START_PROCESS_CAPTURE_MARKER}' + "
        "(ConvertTo-Json -Compress @($script:__V1K_SP.ToArray()))); "
        + f"Write-Output ('{_HTTP_EVENT_MARKER}' + "
        "(ConvertTo-Json -Compress @($script:__V1L_HTTP.ToArray()))); "
        + f"Write-Output ('{_TCP_QUERY_MARKER}' + "
        "(ConvertTo-Json -Compress @($script:__V1L_TCP.ToArray()))); "
        + f"Write-Output ('{_UNIFIED_TRACE_MARKER}' + "
        "(ConvertTo-Json -Compress @($script:__V1L_TRACE.ToArray()))); "
        + f"Write-Output ('{_ENV_BRIDGE_MARKER}' + "
        "(ConvertTo-Json -Compress @{ before = $script:__V1L_ENV_BEFORE; after = $script:__V1L_ENV_AFTER })); "
        + "exit $__v1l_code"
    )
    run_env = _build_run_env(env)
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
    http_raw = _parse_json_marker_line(combined, _HTTP_EVENT_MARKER)
    tcp_raw = _parse_json_marker_line(combined, _TCP_QUERY_MARKER)
    trace_raw = _parse_json_marker_line(combined, _UNIFIED_TRACE_MARKER)
    if not isinstance(captures_raw, list):
        raise AssertionError(f"Start-Process capture 必须为数组：{captures_raw!r}")
    if not isinstance(http_raw, list):
        raise AssertionError(f"HTTP 事件必须为数组：{http_raw!r}")
    if not isinstance(tcp_raw, list):
        raise AssertionError(f"TCP 查询必须为数组：{tcp_raw!r}")
    if not isinstance(trace_raw, list):
        raise AssertionError(f"统一 trace 必须为数组：{trace_raw!r}")
    captures = [dict(x) for x in captures_raw if isinstance(x, dict)]
    if len(captures) != len(captures_raw):
        raise AssertionError(f"capture 项必须全为对象：{captures_raw!r}")
    http_events = [dict(x) for x in http_raw if isinstance(x, dict)]
    if len(http_events) != len(http_raw):
        raise AssertionError(f"HTTP 事件项必须全为对象：{http_raw!r}")
    tcp_queries = [dict(x) for x in tcp_raw if isinstance(x, dict)]
    if len(tcp_queries) != len(tcp_raw):
        raise AssertionError(f"TCP 查询项必须全为对象：{tcp_raw!r}")
    unified_trace = [dict(x) for x in trace_raw if isinstance(x, dict)]
    if len(unified_trace) != len(trace_raw):
        raise AssertionError(f"统一 trace 项必须全为对象：{trace_raw!r}")
    return proc, captures, counts, http_events, tcp_queries, unified_trace


def _owned_backend_tcp_row(repo: Path, pid: int = 710001) -> dict[str, Any]:
    return {
        "LocalAddress": _EXPECTED_BACKEND_HOST,
        "LocalPort": _EXPECTED_BACKEND_PORT,
        "State": "Listen",
        "OwningProcess": pid,
        "executablePath": str(repo / "backend" / ".venv" / "Scripts" / "python.exe"),
        "commandLine": (
            f'"{repo / "backend" / ".venv" / "Scripts" / "python.exe"}" '
            f"-m uvicorn app.main:app --reload "
            f"--host {_EXPECTED_BACKEND_HOST} --port {_EXPECTED_BACKEND_PORT}"
        ),
    }


def _owned_frontend_tcp_row(
    repo: Path,
    host: str = _VALID_LAN_HOST,
    pid: int = 720001,
) -> dict[str, Any]:
    """
    owned LAN frontend TCP 行。executablePath/commandLine 仅供 Cim 元数据注入，
    不会出现在 Get-NetTCPConnection 返回对象上。
    """
    node = r"C:\Program Files\nodejs\node.exe"
    vite_js = repo / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    return {
        "LocalAddress": host,
        "LocalPort": _EXPECTED_FRONTEND_PORT,
        "State": "Listen",
        "OwningProcess": pid,
        "executablePath": node,
        "commandLine": (
            f'"{node}" "{vite_js}" --host {host} --port {_EXPECTED_FRONTEND_PORT}'
        ),
    }


def _auth_http_content(
    *,
    auth_required: bool = True,
    bootstrapped: bool = True,
) -> str:
    return json.dumps(
        {"bootstrapped": bootstrapped, "authRequired": auth_required},
        ensure_ascii=False,
    )


def _ready_http_map(
    *,
    auth_required: bool = True,
    bootstrapped: bool = True,
    frontend_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    m: dict[str, dict[str, Any]] = {
        _EXPECTED_BACKEND_HEALTH_URL: {
            "StatusCode": 200,
            "Content": json.dumps({"status": "ok", "dbOk": True}, ensure_ascii=False),
        },
        _EXPECTED_AUTH_BOOTSTRAP_URL: {
            "StatusCode": 200,
            "Content": _auth_http_content(
                auth_required=auth_required, bootstrapped=bootstrapped
            ),
        },
    }
    if frontend_url is not None:
        m[frontend_url] = {"StatusCode": 200, "Content": "<html>ok</html>"}
    return m


def _assert_http_event_order_health_then_auth(
    tc: unittest.TestCase, events: list[dict[str, Any]]
) -> tuple[int, int]:
    """
    精确顺序：health → 回环 GET bootstrap-status；两调用均存在且方法为 GET。
    返回 (health_seq, auth_seq)；优先用统一 seq，否则回退列表下标。
    """
    tc.assertGreaterEqual(len(events), 2, f"至少 health+auth 两次观测：{events!r}")
    urls = [
        (str(e.get("method", "")).upper(), str(e.get("url", "")), e) for e in events
    ]
    try:
        i_health = next(
            i for i, (_m, u, _e) in enumerate(urls) if u == _EXPECTED_BACKEND_HEALTH_URL
        )
    except StopIteration as exc:
        raise AssertionError(f"未观测到 health URL：{events!r}") from exc
    try:
        i_auth = next(
            i for i, (_m, u, _e) in enumerate(urls) if u == _EXPECTED_AUTH_BOOTSTRAP_URL
        )
    except StopIteration as exc:
        raise AssertionError(f"未观测到 bootstrap-status URL：{events!r}") from exc
    tc.assertLess(
        i_health,
        i_auth,
        f"事件顺序必须 health 先于 auth：health@{i_health} auth@{i_auth} events={events!r}",
    )
    tc.assertEqual(urls[i_health][0], _EXPECTED_AUTH_HTTP_METHOD)
    tc.assertEqual(urls[i_auth][0], _EXPECTED_AUTH_HTTP_METHOD)
    health_ev = urls[i_health][2]
    auth_ev = urls[i_auth][2]
    health_seq = health_ev.get("seq", i_health)
    auth_seq = auth_ev.get("seq", i_auth)
    try:
        health_seq_i = int(health_seq)
        auth_seq_i = int(auth_seq)
    except (TypeError, ValueError) as exc:
        raise AssertionError(
            f"health/auth seq 必须为 int：health={health_seq!r} auth={auth_seq!r}"
        ) from exc
    tc.assertLess(
        health_seq_i,
        auth_seq_i,
        f"统一 seq 必须 health < auth：health={health_seq_i} auth={auth_seq_i} events={events!r}",
    )
    return health_seq_i, auth_seq_i


def _assert_unified_trace_health_auth_then_unique_frontend_start(
    tc: unittest.TestCase,
    *,
    http_events: list[dict[str, Any]],
    captures: list[dict[str, Any]],
    unified_trace: list[dict[str, Any]],
    host: str = _VALID_LAN_HOST,
) -> None:
    """
    统一单调 trace：精确证明 health < auth < 唯一 frontend_start。
    不得只比较分离的 http 列表与 Start-Process 列表。
    """
    _health_seq, auth_seq = _assert_http_event_order_health_then_auth(tc, http_events)
    tc.assertEqual(len(captures), 1, f"必须唯一 frontend Start-Process：{captures!r}")
    cap = captures[0]
    _assert_lan_frontend_capture(tc, cap, host=host, vite_base="/api", vite_present=True)
    start_seq = cap.get("seq")
    tc.assertIsInstance(start_seq, int, f"start capture 必须有统一 seq：{cap!r}")
    tc.assertNotIsInstance(start_seq, bool)
    tc.assertLess(
        auth_seq,
        int(start_seq),
        f"统一 seq 必须 auth < frontend_start：auth={auth_seq} start={start_seq} "
        f"trace={unified_trace!r}",
    )
    kinds_urls = [
        (
            str(t.get("kind", "")),
            str(t.get("url", "")),
            str(t.get("ArgumentList", "")),
            t.get("seq"),
        )
        for t in unified_trace
    ]
    try:
        t_health = next(
            s
            for k, u, _a, s in kinds_urls
            if k == "http" and u == _EXPECTED_BACKEND_HEALTH_URL
        )
        t_auth = next(
            s
            for k, u, _a, s in kinds_urls
            if k == "http" and u == _EXPECTED_AUTH_BOOTSTRAP_URL
        )
    except StopIteration as exc:
        raise AssertionError(f"统一 trace 缺 health/auth：{unified_trace!r}") from exc
    t_starts = [
        s
        for k, _u, a, s in kinds_urls
        if k == "start"
        and f"--host {host}" in a
        and f"--port {_EXPECTED_FRONTEND_PORT}" in a
    ]
    tc.assertEqual(len(t_starts), 1, f"trace 中 frontend_start 必须唯一：{unified_trace!r}")
    tc.assertLess(int(t_health), int(t_auth))
    tc.assertLess(int(t_auth), int(t_starts[0]))


def _assert_lan_frontend_capture(
    tc: unittest.TestCase,
    cap: dict[str, Any],
    *,
    host: str = _VALID_LAN_HOST,
    vite_base: str | None = "/api",
    vite_present: bool = True,
) -> None:
    args = str(cap.get("ArgumentList", "")).strip()
    expected = f"run dev -- --host {host} --port {_EXPECTED_FRONTEND_PORT}"
    tc.assertEqual(args, expected, f"frontend ArgumentList 必须精确 LAN host：{args!r}")
    tc.assertNotIn("0.0.0.0", args)
    tc.assertNotIn("127.0.0.1", args)
    tc.assertEqual(str(cap.get("WindowStyle", "")).strip(), _EXPECTED_WINDOW_STYLE)
    tc.assertEqual(str(cap.get("EnvListenProfile", "")).strip().lower(), "lan")
    tc.assertEqual(str(cap.get("EnvLanHost", "")).strip(), host)
    present = cap.get("EnvViteApiBasePresent")
    tc.assertIsInstance(present, bool, f"EnvViteApiBasePresent 必须 bool：{present!r}")
    tc.assertEqual(present, vite_present)
    if vite_present:
        tc.assertEqual(cap.get("EnvViteApiBase"), vite_base)
    else:
        tc.assertTrue(
            cap.get("EnvViteApiBase") in (None, ""),
            f"未设置时 EnvViteApiBase 应 null/空：{cap.get('EnvViteApiBase')!r}",
        )


def _normalize_path_key(path: str | Path) -> str:
    return str(Path(path)).replace("/", "\\").rstrip("\\").lower()


def _owned_backend_listener(repo: Path, pid: int = 710001) -> dict[str, Any]:
    py = repo / "backend" / ".venv" / "Scripts" / "python.exe"
    cmd = (
        f'"{py}" -m uvicorn app.main:app --reload '
        f"--host {_EXPECTED_BACKEND_HOST} --port {_EXPECTED_BACKEND_PORT}"
    )
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "pid": pid,
        "executablePath": str(py),
        "commandLine": cmd,
    }


def _foreign_backend_listener(pid: int = 515191) -> dict[str, Any]:
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "pid": pid,
        "executablePath": r"C:\Windows\System32\svchost.exe",
        "commandLine": "svchost -k netsvcs - foreign-backend-v1l",
    }


def _owned_frontend_listener(
    repo: Path,
    *,
    host: str = _VALID_LAN_HOST,
    pid: int = 720001,
) -> dict[str, Any]:
    """
    owned frontend listener 快照：commandLine 必须含 FrontendDir，
    executablePath 为 node.exe，供生产 Test-FrontendOwnership 正确判 owned。
    """
    node = r"C:\Program Files\nodejs\node.exe"
    vite_js = repo / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    return {
        "port": _EXPECTED_FRONTEND_PORT,
        "pid": pid,
        "executablePath": node,
        "commandLine": (
            f'"{node}" "{vite_js}" --host {host} --port {_EXPECTED_FRONTEND_PORT}'
        ),
    }


def _probe_backend_ready() -> dict[str, Any]:
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "httpStatus": 200,
        "status": "ok",
        "dbOk": True,
    }


def _auth_snapshot(
    *,
    auth_required: Any = True,
    bootstrapped: Any = True,
    http_status: int = 200,
) -> dict[str, Any]:
    """
    契约握手注入：等价 GET /api/auth/bootstrap-status 的结构化快照。
    生产真源须识别 -AuthSnapshotJson；测试禁止 live HTTP。
    """
    return {
        "port": _EXPECTED_BACKEND_PORT,
        "httpStatus": http_status,
        "authRequired": auth_required,
        "bootstrapped": bootstrapped,
    }


def _find_vite_node_modules() -> Path:
    """
    定位含 vite 的 node_modules（优先本 worktree，其次主仓）。
    仅用于结构化加载配置；不算生产实现。
    """
    candidates = [
        _REPO_ROOT / "frontend" / "node_modules",
        _REPO_ROOT.parent / "biaoshu" / "frontend" / "node_modules",
    ]
    for c in candidates:
        if (c / "vite").is_dir():
            return c
    raise AssertionError(
        "未找到含 vite 的 node_modules；无法做 Vite 结构化加载"
        f"（候选={candidates}）。此属依赖环境问题，不算业务红证据。"
    )


def _load_vite_server_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    """
    实际加载 vite.config.ts 导出对象的 server 段（结构化行为验证）。
    禁止只扫源码字符串或 README。
    """
    cfg = _require_vite_config()
    nm = _find_vite_node_modules()
    with tempfile.TemporaryDirectory(prefix="v1l-vite-") as td:
        root = Path(td)
        # 复制配置到 TEMP，junction node_modules，隔离工作目录
        shutil.copy2(cfg, root / "vite.config.ts")
        # package.json 最小桩，供 vite 解析
        (root / "package.json").write_text(
            json.dumps({"name": "v1l-vite-load", "private": True, "type": "module"}),
            encoding="utf-8",
        )
        junction = root / "node_modules"
        # Windows junction
        link_cmd = [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(nm.resolve()),
        ]
        link_proc = subprocess.run(
            link_cmd, capture_output=True, timeout=30, check=False
        )
        if link_proc.returncode != 0 or not junction.exists():
            raise AssertionError(
                "创建 node_modules junction 失败："
                f"rc={link_proc.returncode} "
                f"out={_decode_ps_output(link_proc.stdout)!r} "
                f"err={_decode_ps_output(link_proc.stderr)!r}"
            )
        loader = (
            "import { loadConfigFromFile } from 'vite';\n"
            "const r = await loadConfigFromFile("
            "{ command: 'serve', mode: 'development' }, './vite.config.ts');\n"
            "if (!r || !r.config) { throw new Error('vite config empty'); }\n"
            "const s = r.config.server || {};\n"
            "const proxy = s.proxy || {};\n"
            "const proxyOut = {};\n"
            "for (const [k, v] of Object.entries(proxy)) {\n"
            "  if (v && typeof v === 'object') {\n"
            "    proxyOut[k] = { target: v.target ?? null, "
            "changeOrigin: v.changeOrigin ?? null };\n"
            "  } else { proxyOut[k] = v; }\n"
            "}\n"
            "const out = {\n"
            "  host: s.host ?? null,\n"
            "  port: s.port ?? null,\n"
            "  strictPort: s.strictPort ?? null,\n"
            "  allowedHosts: s.allowedHosts === undefined ? null : s.allowedHosts,\n"
            "  proxy: proxyOut,\n"
            "};\n"
            "process.stdout.write(JSON.stringify(out));\n"
        )
        # 无条件清四个受控变量，再注入显式 env（env 为空/None 也必须清，防环境污染）
        run_env = os.environ.copy()
        for k in _CONTROLLED_ENV_KEYS:
            run_env.pop(k, None)
        if env:
            run_env.update(env)
        raw = subprocess.run(
            ["node", "--input-type=module", "-e", loader],
            cwd=str(root),
            capture_output=True,
            timeout=60,
            check=False,
            env=run_env,
        )
        if raw.returncode != 0:
            raise AssertionError(
                "Vite 配置模块加载失败："
                f"rc={raw.returncode} "
                f"stdout={_decode_ps_output(raw.stdout)!r} "
                f"stderr={_decode_ps_output(raw.stderr)!r}"
            )
        data = json.loads(_decode_ps_output(raw.stdout))
        if not isinstance(data, dict):
            raise AssertionError(f"Vite server 配置必须为对象：{data!r}")
        return data


# ===========================================================================
# TEMP 假仓
# ===========================================================================


class _TempRepo:
    def __init__(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="v1l-lan-")
        self.root = Path(self._td.name) / "repo"
        self.root.mkdir(parents=True)
        self.snap_dir = Path(self._td.name) / "snaps"
        self.snap_dir.mkdir(parents=True)
        self._layout()

    def cleanup(self) -> None:
        self._td.cleanup()

    def _layout(self) -> None:
        (self.root / "tools" / "v1-ops").mkdir(parents=True)
        (self.root / "backend" / "app").mkdir(parents=True)
        (self.root / "frontend").mkdir(parents=True)
        (self.root / "tmp").mkdir(parents=True)
        if _TRUE_SOURCE.is_file():
            shutil.copy2(
                _TRUE_SOURCE, self.root / "tools" / "v1-ops" / "Start-Biaoshu-Dev.ps1"
            )
        venv_scripts = self.root / "backend" / ".venv" / "Scripts"
        venv_scripts.mkdir(parents=True)
        (venv_scripts / "python.exe").write_bytes(b"MZ-fake-python-v1l\n")
        (self.root / "backend" / "app" / "main.py").write_text(
            "# fake main v1l\napp = None\n", encoding="utf-8"
        )
        (self.root / "frontend" / "package.json").write_text(
            json.dumps({"name": "biaoshu-frontend-fake", "private": True}),
            encoding="utf-8",
        )
        nm = self.root / "frontend" / "node_modules" / "vite" / "bin"
        nm.mkdir(parents=True)
        (nm / "vite.js").write_text("// fake vite v1l\n", encoding="utf-8")
        # 可解析假 npm
        shim = self.root / "tools" / "v1l-shims"
        shim.mkdir(parents=True)
        (shim / "npm.cmd").write_text(
            "@echo off\r\nrem v1l-fake-npm\r\n", encoding="utf-8"
        )
        self.npm_shim_dir = shim

    def true_source(self) -> Path:
        p = self.root / "tools" / "v1-ops" / "Start-Biaoshu-Dev.ps1"
        if not p.is_file():
            raise AssertionError(f"TEMP 真源缺失：{p}")
        return p

    def write_listener(self, records: Any, name: str = "listener.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def write_probe(self, records: Any, name: str = "probe.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def write_process(self, records: Any, name: str = "process.json") -> Path:
        return _write_json(self.snap_dir / name, records)

    def write_auth(self, record: Any, name: str = "auth.json") -> Path:
        return _write_json(self.snap_dir / name, record)

    def npm_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = str(self.npm_shim_dir.resolve()) + os.pathsep + env.get("PATH", "")
        env["Path"] = env["PATH"]
        if extra:
            env.update(extra)
        return env


# ===========================================================================
# 1. 边界与默认 loopback 不回归
# ===========================================================================


class TestBoundaryAndLoopbackBaseline(unittest.TestCase):
    """默认 loopback、七键、Stop 未改、白名单边界。"""

    def test_writable_boundary_is_test_only(self) -> None:
        self.assertEqual(
            Path(__file__).name, "test_trusted_lan_access.py"
        )
        self.assertIn("tools/v1-ops/test_trusted_lan_access.py", _WRITABLE_REL)
        # 生产真源不在本专项可写集合
        self.assertNotIn("tools/v1-ops/Start-Biaoshu-Dev.ps1", _WRITABLE_REL)
        self.assertNotIn("frontend/vite.config.ts", _WRITABLE_REL)

    def test_stop_sha256_unchanged(self) -> None:
        self.assertTrue(_STOP_PS1.is_file())
        digest = _sha256_file(_STOP_PS1)
        self.assertEqual(digest.lower(), _EXPECTED_STOP_SHA256)

    def test_default_planonly_loopback_seven_keys(self) -> None:
        """无 LAN 参数时 PlanOnly 仍成功、七键、零副作用。"""
        _require_true_source()
        tr = _TempRepo()
        try:
            proc, counts = _run_ps1_guarded(
                tr.true_source(),
                [
                    "-Component",
                    "all",
                    "-PlanOnly",
                    "-ListenerSnapshotJson",
                    str(tr.write_listener([])),
                    "-ProbeSnapshotJson",
                    str(tr.write_probe([])),
                ],
                cwd=tr.root,
                env=tr.npm_env(),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            status = _load_status(tr.root)
            _assert_status_schema(self, status, mode="plan", component="all")
            self.assertEqual(status["overall"], "plan")
            self.assertEqual(status["code"], "plan")
            _assert_status_no_privacy_leak(self, status)
            _assert_zero_side_effect_counts(self, counts)
        finally:
            tr.cleanup()

    def test_default_start_capture_still_loopback_hosts(self) -> None:
        """默认 start：backend/frontend 仍精确 127.0.0.1（不回归）。"""
        _require_true_source()
        tr = _TempRepo()
        try:
            _proc_b, caps_b, counts_b = _run_ps1_start_capture(
                tr.true_source(),
                ["-Component", "backend"],
                cwd=tr.root,
                env=tr.npm_env(),
            )
            self.assertEqual(len(caps_b), 1, caps_b)
            self.assertEqual(counts_b.get("Start-Process"), 1)
            args_b = str(caps_b[0].get("ArgumentList", "")).strip()
            self.assertEqual(args_b, _EXPECTED_BACKEND_START_ARGS_LOOPBACK)
            self.assertEqual(
                str(caps_b[0].get("WindowStyle", "")).strip(), _EXPECTED_WINDOW_STYLE
            )

            _proc_f, caps_f, counts_f = _run_ps1_start_capture(
                tr.true_source(),
                ["-Component", "frontend"],
                cwd=tr.root,
                env=tr.npm_env(),
            )
            self.assertEqual(len(caps_f), 1, caps_f)
            self.assertEqual(counts_f.get("Start-Process"), 1)
            args_f = str(caps_f[0].get("ArgumentList", "")).strip()
            self.assertEqual(args_f, _EXPECTED_FRONTEND_START_ARGS_LOOPBACK)
        finally:
            tr.cleanup()


# ===========================================================================
# 2. profile / host 严格输入 fail-closed
# ===========================================================================


class TestListenProfileAndHostValidation(unittest.TestCase):
    """ListenProfile/LanHost 严格校验；未知参数拒绝；零启动。"""

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _plan(
        self, extra_args: list[str], *, env: dict[str, str] | None = None
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
        args = [
            "-Component",
            "all",
            "-PlanOnly",
            "-ListenerSnapshotJson",
            str(self.tr.write_listener([])),
            "-ProbeSnapshotJson",
            str(self.tr.write_probe([])),
            *extra_args,
        ]
        return _run_ps1_guarded(
            self.tr.true_source(),
            args,
            cwd=self.tr.root,
            env=self.tr.npm_env(env),
        )

    def test_lan_without_host_fails_lan_host_required(self) -> None:
        proc, counts = self._plan(["-ListenProfile", "lan"])
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="plan", component="all")
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "lan_host_required")
        _assert_status_no_privacy_leak(self, status)
        _assert_zero_side_effect_counts(self, counts)

    def test_loopback_with_host_fails_listen_profile_invalid(self) -> None:
        proc, counts = self._plan(
            ["-ListenProfile", "loopback", "-LanHost", _VALID_LAN_HOST]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "listen_profile_invalid")
        _assert_zero_side_effect_counts(self, counts)

    def test_invalid_profiles_fail_closed(self) -> None:
        for prof in _INVALID_PROFILES:
            with self.subTest(profile=prof):
                # 每个用例独立状态文件：复用同一 TEMP 仓顺序覆盖
                proc, counts = self._plan(
                    ["-ListenProfile", prof, "-LanHost", _VALID_LAN_HOST]
                )
                self.assertNotEqual(proc.returncode, 0, f"profile={prof!r}")
                status = _load_status(self.tr.root)
                self.assertEqual(
                    status["code"],
                    "listen_profile_invalid",
                    f"profile={prof!r} status={status}",
                )
                _assert_zero_side_effect_counts(self, counts)

    def test_invalid_lan_hosts_fail_closed(self) -> None:
        for host in _INVALID_LAN_HOSTS:
            with self.subTest(host=host):
                proc, counts = self._plan(
                    ["-ListenProfile", "lan", "-LanHost", host]
                )
                self.assertNotEqual(proc.returncode, 0, f"host={host!r}")
                status = _load_status(self.tr.root)
                self.assertEqual(
                    status["code"],
                    "lan_host_invalid",
                    f"host={host!r} code={status.get('code')}",
                )
                _assert_status_no_privacy_leak(self, status)
                _assert_zero_side_effect_counts(self, counts)

    def test_unknown_parameter_rejected(self) -> None:
        proc, counts = self._plan(["-UnknownSwitch", "x"])
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        # 未知/重复/冲突 profile 参数族：精确 listen_profile_invalid
        self.assertEqual(status["code"], "listen_profile_invalid")
        _assert_zero_side_effect_counts(self, counts)

    def test_duplicate_listen_profile_rejected(self) -> None:
        proc, counts = self._plan(
            [
                "-ListenProfile",
                "lan",
                "-ListenProfile",
                "loopback",
                "-LanHost",
                _VALID_LAN_HOST,
            ]
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "listen_profile_invalid")
        _assert_zero_side_effect_counts(self, counts)

    def test_profile_case_and_trim_lan_with_valid_host_plans(self) -> None:
        """
        大小写不敏感、去首尾空白后 lan + 合法 host 应 Plan 成功。
        主证据另见 start-capture 的精确 bind；此处只证明校验门放行。
        """
        proc, counts = self._plan(
            ["-ListenProfile", " LAN ", "-LanHost", _VALID_LAN_HOST]
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="plan", component="all")
        self.assertEqual(status["overall"], "plan")
        self.assertEqual(status["code"], "plan")
        _assert_status_no_privacy_leak(self, status)
        _assert_zero_side_effect_counts(self, counts)
        # 与“非法 host 仍 0 退出”形成对照：合法必须成功；非法必须非 0。
        # 生产若忽略全部 LAN 参数，非法用例先红，本用例可能暂绿，不算假绿主证据。

    def test_valid_rfc1918_hosts_plan_then_frontend_only_requires_auth_proof(self) -> None:
        """
        每个合法 RFC1918：PlanOnly 放行；frontend-only 无后端证明时必须
        精确 lan_backend_auth_unverified 且 0 次 Start-Process。
        正向 bind 证据见 TestLanPositiveStartOrder（禁止本处条件绿）。
        """
        for host in _VALID_LAN_HOSTS:
            with self.subTest(host=host):
                proc, counts = self._plan(
                    ["-ListenProfile", "lan", "-LanHost", host]
                )
                self.assertEqual(proc.returncode, 0, f"host={host!r} plan")
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "plan")
                _assert_status_no_privacy_leak(self, status)
                _assert_zero_side_effect_counts(self, counts)

                _p2, caps, counts2 = _run_ps1_start_capture(
                    self.tr.true_source(),
                    [
                        "-Component",
                        "frontend",
                        "-ListenProfile",
                        "lan",
                        "-LanHost",
                        host,
                    ],
                    cwd=self.tr.root,
                    env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
                )
                self.assertEqual(
                    caps,
                    [],
                    f"frontend-only 无 auth 证明不得 Start-Process host={host!r} caps={caps!r}",
                )
                self.assertEqual(counts2.get("Start-Process", 0), 0)
                self.assertNotEqual(_p2.returncode, 0)
                st2 = _load_status(self.tr.root)
                self.assertEqual(st2["overall"], "failed")
                self.assertEqual(st2["code"], "lan_backend_auth_unverified")


# ===========================================================================
# 3. LAN 绑定：后端恒回环、前端精确私有 IPv4
# ===========================================================================


class TestLanBindAndStartCapture(unittest.TestCase):
    """LAN start capture：backend 回环+required；frontend 精确 LanHost。"""

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()

    def tearDown(self) -> None:
        self.tr.cleanup()

    def test_lan_backend_start_is_loopback_and_auth_required(self) -> None:
        """无快照 start-capture：LAN 后端必须回环 + AUTH_MODE=required。"""
        proc, caps, counts = _run_ps1_start_capture(
            self.tr.true_source(),
            [
                "-Component",
                "backend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env(),
        )
        self.assertEqual(len(caps), 1, f"必须恰好一次 backend capture：{caps!r}")
        self.assertEqual(counts.get("Start-Process"), 1)
        for k in _SIDE_EFFECT_KEYS:
            if k == "Start-Process":
                continue
            self.assertEqual(counts[k], 0, f"其它副作用必须为 0：{k}")
        args = str(caps[0].get("ArgumentList", "")).strip()
        self.assertEqual(args, _EXPECTED_BACKEND_START_ARGS_LOOPBACK)
        self.assertNotIn("0.0.0.0", args)
        self.assertNotIn(_VALID_LAN_HOST, args)
        self.assertIn("--host 127.0.0.1", args)
        self.assertEqual(
            str(caps[0].get("EnvAuthMode", "")).strip().lower(),
            "required",
            f"LAN 后端必须 AUTH_MODE=required，实际 EnvAuthMode={caps[0].get('EnvAuthMode')!r}",
        )
        self.assertEqual(
            str(caps[0].get("WindowStyle", "")).strip(), _EXPECTED_WINDOW_STYLE
        )
        if (self.tr.root / _STATUS_REL).is_file():
            status = _load_status(self.tr.root)
            _assert_status_schema(self, status, mode="start", component="backend")
            _assert_status_no_privacy_leak(self, status)
        _ = proc

    def test_lan_frontend_only_without_backend_is_auth_unverified(self) -> None:
        """
        frontend-only + LAN：无 owned 后端 auth 证明时必须 0 capture，
        精确 code=lan_backend_auth_unverified。永远拒绝亦不得绿（本断言强制失败路径）。
        正向 bind 见 TestLanPositiveStartOrder。
        """
        proc, caps, counts = _run_ps1_start_capture(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )
        self.assertEqual(caps, [], f"不得启动 LAN 前端：{caps!r}")
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_status_no_privacy_leak(self, status)


# ===========================================================================
# 4. required 握手：先证明再暴露前端；false/非法/超时/foreign fail-closed
# ===========================================================================


class TestLanAuthHandshakeFailClosed(unittest.TestCase):
    """
    authRequired 证明先于 LAN 前端。
    快照仅用于 PlanOnly/DiagnoseOnly（V1-K：start 模式禁 listener/probe 快照）。
    start 零暴露门用无快照 capture 证明。
    """

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _diagnose_lan(
        self,
        *,
        listeners: list[dict[str, Any]],
        probes: list[dict[str, Any]],
        auth: Any,
        auth_name: str = "auth.json",
        component: str = "all",
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
        return _run_ps1_guarded(
            self.tr.true_source(),
            [
                "-Component",
                component,
                "-DiagnoseOnly",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
                "-ListenerSnapshotJson",
                str(self.tr.write_listener(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe(probes)),
                "-AuthSnapshotJson",
                str(self.tr.write_auth(auth, name=auth_name)),
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )

    def test_auth_required_false_diagnose_fail_closed(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=False, bootstrapped=True)
        proc, counts = self._diagnose_lan(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="diagnose", component="all")
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_status_no_privacy_leak(self, status)
        _assert_zero_side_effect_counts(self, counts)

    def test_auth_missing_key_fail_closed(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = {
            "port": _EXPECTED_BACKEND_PORT,
            "httpStatus": 200,
            "bootstrapped": True,
        }
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-miss.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_zero_side_effect_counts(self, counts)

    def test_auth_non_boolean_fail_closed(self) -> None:
        for bad in ("true", 1, 0, "yes", None, "required"):
            with self.subTest(authRequired=bad):
                listeners = [_owned_backend_listener(self.tr.root, pid=710100)]
                probes = [_probe_backend_ready()]
                auth = _auth_snapshot(auth_required=bad, bootstrapped=True)
                proc, counts = self._diagnose_lan(
                    listeners=listeners,
                    probes=probes,
                    auth=auth,
                    auth_name=f"auth-bad-{hash(str(bad)) & 0xFFFF:x}.json",
                )
                self.assertNotEqual(proc.returncode, 0)
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "lan_backend_auth_unverified")
                _assert_zero_side_effect_counts(self, counts)

    def test_auth_http_not_200_fail_closed(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=True, http_status=503)
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-503.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_zero_side_effect_counts(self, counts)

    def test_foreign_backend_fail_closed(self) -> None:
        listeners = [_foreign_backend_listener()]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=True)
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-foreign.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "backend_port_foreign")
        _assert_zero_side_effect_counts(self, counts)

    def test_lan_frontend_start_without_backend_zero_capture(self) -> None:
        """
        start 路径、无快照：LAN frontend-only 在无后端 auth 证明时
        必须 0 次 Start-Process（禁止抢先暴露 5173）。
        生产忽略 LAN 并启动 frontend 时本用例业务红。
        """
        proc, caps, counts = _run_ps1_start_capture(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )
        self.assertEqual(
            caps,
            [],
            f"无 auth 证明时 LAN 前端不得 Start-Process：{caps!r}",
        )
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "lan_backend_auth_unverified")

    def test_bootstrapped_false_is_lan_admin_not_bootstrapped(self) -> None:
        """authRequired=true 但 bootstrapped=false → 精确 lan_admin_not_bootstrapped。"""
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=True, bootstrapped=False)
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-noboot.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "lan_admin_not_bootstrapped")
        _assert_status_no_privacy_leak(self, status)
        _assert_zero_side_effect_counts(self, counts)

    def test_auth_snapshot_non_object_fail_closed(self) -> None:
        for bad, name in (
            (["not-object"], "auth-arr.json"),
            ("string", "auth-str.json"),
            (42, "auth-int.json"),
            (None, "auth-null.json"),
        ):
            with self.subTest(auth=bad):
                listeners = [_owned_backend_listener(self.tr.root)]
                probes = [_probe_backend_ready()]
                proc, counts = self._diagnose_lan(
                    listeners=listeners,
                    probes=probes,
                    auth=bad,
                    auth_name=name,
                )
                self.assertNotEqual(proc.returncode, 0)
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "lan_backend_auth_unverified")
                _assert_zero_side_effect_counts(self, counts)

    def test_auth_snapshot_extra_key_fail_closed(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot()
        auth["extra"] = True
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-extra.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_zero_side_effect_counts(self, counts)

    def test_auth_snapshot_wrong_port_fail_closed(self) -> None:
        listeners = [_owned_backend_listener(self.tr.root)]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot()
        auth["port"] = 8010
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-port.json",
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")
        _assert_zero_side_effect_counts(self, counts)

    def test_auth_snapshot_http_status_must_be_strict_int(self) -> None:
        for bad in ("200", 200.0, True, None):
            with self.subTest(httpStatus=bad):
                listeners = [_owned_backend_listener(self.tr.root)]
                probes = [_probe_backend_ready()]
                auth = {
                    "port": _EXPECTED_BACKEND_PORT,
                    "httpStatus": bad,
                    "authRequired": True,
                    "bootstrapped": True,
                }
                proc, counts = self._diagnose_lan(
                    listeners=listeners,
                    probes=probes,
                    auth=auth,
                    auth_name=f"auth-http-{hash(str(bad)) & 0xFFFF:x}.json",
                )
                self.assertNotEqual(proc.returncode, 0)
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "lan_backend_auth_unverified")
                _assert_zero_side_effect_counts(self, counts)

    def test_auth_bootstrapped_non_boolean_fail_closed(self) -> None:
        for bad in ("true", 1, 0, "yes", None):
            with self.subTest(bootstrapped=bad):
                listeners = [_owned_backend_listener(self.tr.root)]
                probes = [_probe_backend_ready()]
                auth = _auth_snapshot(auth_required=True, bootstrapped=bad)
                proc, counts = self._diagnose_lan(
                    listeners=listeners,
                    probes=probes,
                    auth=auth,
                    auth_name=f"auth-boot-{hash(str(bad)) & 0xFFFF:x}.json",
                )
                self.assertNotEqual(proc.returncode, 0)
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "lan_backend_auth_unverified")
                _assert_zero_side_effect_counts(self, counts)

    def test_start_mode_auth_snapshot_is_snapshot_invalid(self) -> None:
        """start 模式携带 AuthSnapshotJson 必须 snapshot_invalid，零 Start-Process。"""
        auth_path = self.tr.write_auth(_auth_snapshot(), name="auth-start.json")
        proc, caps, counts = _run_ps1_start_capture(
            self.tr.true_source(),
            [
                "-Component",
                "all",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
                "-AuthSnapshotJson",
                str(auth_path),
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )
        self.assertEqual(caps, [])
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "snapshot_invalid")

    def test_auth_required_true_diagnose_with_owned_backend_ok(self) -> None:
        """owned + ready + authRequired=true + bootstrapped=true 的 Diagnose 应 already_running。"""
        listeners = [
            _owned_backend_listener(self.tr.root),
        ]
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_lan(
            listeners=listeners,
            probes=probes,
            auth=auth,
            auth_name="auth-ok.json",
            component="backend",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        status = _load_status(self.tr.root)
        _assert_status_schema(self, status, mode="diagnose", component="backend")
        self.assertEqual(status["overall"], "already_running")
        self.assertEqual(status["code"], "already_running")
        _assert_status_no_privacy_leak(self, status)
        _assert_zero_side_effect_counts(self, counts)
        # 精确四键契约面（快照文件本身）
        self.assertEqual(
            sorted(_auth_snapshot().keys()), sorted(_AUTH_SNAPSHOT_KEYS)
        )


# ===========================================================================
# 5. API base / proxy / allowedHosts（Vite 结构化加载）
# ===========================================================================


class TestViteConfigStructuredAndApiBase(unittest.TestCase):
    """Vite 必须模块加载验证；API base 非 /api 时 LAN 启动失败。"""

    def test_default_vite_server_is_loopback_proxy_only(self) -> None:
        server = _load_vite_server_config({})
        self.assertEqual(server.get("host"), "127.0.0.1")
        self.assertEqual(server.get("port"), 5173)
        self.assertIs(server.get("strictPort"), True)
        # 默认不得 allowedHosts=true 通配
        ah = server.get("allowedHosts")
        self.assertNotEqual(ah, True)
        if isinstance(ah, str):
            self.assertNotEqual(ah.strip(), "true")
        proxy = server.get("proxy") or {}
        self.assertIn("/api", proxy)
        self.assertEqual(sorted(proxy.keys()), ["/api"])
        api = proxy["/api"]
        self.assertIsInstance(api, dict)
        self.assertEqual(api.get("target"), _EXPECTED_PROXY_TARGET)
        self.assertIs(api.get("changeOrigin"), True)

    def test_lan_env_vite_bind_exact_host_and_allowed_hosts(self) -> None:
        """
        LAN 环境变量下：host 精确 LanHost；allowedHosts 仅
        [LanHost, 127.0.0.1, localhost]；proxy 仍精确回环。
        生产未实现时本用例业务红。
        """
        server = _load_vite_server_config(
            {
                _ENV_LISTEN_PROFILE: "lan",
                _ENV_LAN_HOST: _VALID_LAN_HOST,
                "VITE_API_BASE_URL": "/api",
            }
        )
        self.assertEqual(
            server.get("host"),
            _VALID_LAN_HOST,
            f"LAN Vite host 必须精确 {_VALID_LAN_HOST}，实际={server.get('host')!r}",
        )
        self.assertEqual(server.get("port"), 5173)
        self.assertIs(server.get("strictPort"), True)
        ah = server.get("allowedHosts")
        self.assertIsInstance(ah, list, f"allowedHosts 必须为有限列表：{ah!r}")
        self.assertEqual(
            sorted(str(x) for x in ah),
            sorted([_VALID_LAN_HOST, "127.0.0.1", "localhost"]),
        )
        self.assertNotIn(True, ah if isinstance(ah, list) else [])
        proxy = server.get("proxy") or {}
        self.assertEqual(sorted(proxy.keys()), ["/api"])
        self.assertEqual(proxy["/api"].get("target"), _EXPECTED_PROXY_TARGET)
        # 禁止外部代理
        self.assertNotIn("0.0.0.0", json.dumps(server, ensure_ascii=False))

    def test_lan_env_rejects_external_proxy_target_in_config(self) -> None:
        """
        外部 VITE_API_PROXY_TARGET 不得改变 LAN proxy。
        必须成功结构化加载，且 target 精确回环（禁止宽 except/加载失败即绿）。
        """
        server = _load_vite_server_config(
            {
                _ENV_LISTEN_PROFILE: "lan",
                _ENV_LAN_HOST: _VALID_LAN_HOST,
                "VITE_API_PROXY_TARGET": "http://203.0.113.9:8000",
            }
        )
        proxy = server.get("proxy") or {}
        target = (proxy.get("/api") or {}).get("target")
        self.assertEqual(
            target,
            _EXPECTED_PROXY_TARGET,
            f"LAN 模式 proxy target 必须精确回环，实际={target!r}",
        )
        self.assertEqual(server.get("host"), _VALID_LAN_HOST)

    def test_loopback_and_e2e_keep_existing_8010_proxy_coverage(self) -> None:
        """
        loopback/E2E 既有 8010 覆盖保持：非 LAN 下 VITE_API_PROXY_TARGET
        仍可指向回环 8010；LAN 路径不得沿用该覆盖（另见外部 target 用例）。
        """
        server_default = _load_vite_server_config({})
        proxy_default = server_default.get("proxy")
        self.assertIsInstance(proxy_default, dict)
        api_default = proxy_default.get("/api")
        self.assertIsInstance(api_default, dict)
        self.assertEqual(api_default.get("target"), _EXPECTED_PROXY_TARGET)
        server_e2e = _load_vite_server_config(
            {"VITE_API_PROXY_TARGET": "http://127.0.0.1:8010"}
        )
        proxy_e2e = server_e2e.get("proxy")
        self.assertIsInstance(proxy_e2e, dict)
        api_e2e = proxy_e2e.get("/api")
        self.assertIsInstance(api_e2e, dict)
        self.assertEqual(
            api_e2e.get("target"),
            "http://127.0.0.1:8010",
            "loopback/E2E 必须保留 8010 显式代理覆盖",
        )

    def test_lan_api_base_absolute_url_fail_closed(self) -> None:
        """
        PlanOnly：LAN + 绝对 VITE_API_BASE_URL 必须 lan_api_base_invalid，零副作用。
        start-capture 对照：非法 base 不得出现 frontend Start-Process。
        """
        _require_true_source()
        tr = _TempRepo()
        try:
            bad_env = {"VITE_API_BASE_URL": "http://127.0.0.1:8000/api"}
            proc, counts = _run_ps1_guarded(
                tr.true_source(),
                [
                    "-Component",
                    "all",
                    "-PlanOnly",
                    "-ListenProfile",
                    "lan",
                    "-LanHost",
                    _VALID_LAN_HOST,
                    "-ListenerSnapshotJson",
                    str(tr.write_listener([])),
                    "-ProbeSnapshotJson",
                    str(tr.write_probe([])),
                ],
                cwd=tr.root,
                env=tr.npm_env(bad_env),
            )
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            _assert_status_schema(self, status, mode="plan", component="all")
            self.assertEqual(status["code"], "lan_api_base_invalid")
            _assert_status_no_privacy_leak(self, status)
            _assert_zero_side_effect_counts(self, counts)

            proc2, caps, counts2 = _run_ps1_start_capture(
                tr.true_source(),
                [
                    "-Component",
                    "frontend",
                    "-ListenProfile",
                    "lan",
                    "-LanHost",
                    _VALID_LAN_HOST,
                ],
                cwd=tr.root,
                env=tr.npm_env(bad_env),
            )
            self.assertEqual(caps, [], f"非法 API base 不得启动前端：{caps!r}")
            self.assertEqual(counts2.get("Start-Process", 0), 0)
            self.assertNotEqual(proc2.returncode, 0)
            status2 = _load_status(tr.root)
            self.assertEqual(status2["code"], "lan_api_base_invalid")
        finally:
            tr.cleanup()

    def test_lan_api_base_explicit_empty_or_blank_fail_closed(self) -> None:
        """显式空/纯空白 VITE_API_BASE_URL → lan_api_base_invalid（未设置则允许）。"""
        _require_true_source()
        for bad in ("", " ", "\t", "   "):
            with self.subTest(base=bad):
                tr = _TempRepo()
                try:
                    proc, counts = _run_ps1_guarded(
                        tr.true_source(),
                        [
                            "-Component",
                            "all",
                            "-PlanOnly",
                            "-ListenProfile",
                            "lan",
                            "-LanHost",
                            _VALID_LAN_HOST,
                            "-ListenerSnapshotJson",
                            str(tr.write_listener([])),
                            "-ProbeSnapshotJson",
                            str(tr.write_probe([])),
                        ],
                        cwd=tr.root,
                        env=tr.npm_env({"VITE_API_BASE_URL": bad}),
                    )
                    self.assertNotEqual(proc.returncode, 0)
                    status = _load_status(tr.root)
                    self.assertEqual(status["code"], "lan_api_base_invalid")
                    _assert_zero_side_effect_counts(self, counts)
                finally:
                    tr.cleanup()

    def test_lan_api_base_unset_is_allowed_on_plan(self) -> None:
        """VITE_API_BASE_URL 未设置时允许（前端 ?? '/api' 回退）；Plan 放行。"""
        _require_true_source()
        tr = _TempRepo()
        try:
            env = tr.npm_env()
            env.pop("VITE_API_BASE_URL", None)
            proc, counts = _run_ps1_guarded(
                tr.true_source(),
                [
                    "-Component",
                    "all",
                    "-PlanOnly",
                    "-ListenProfile",
                    "lan",
                    "-LanHost",
                    _VALID_LAN_HOST,
                    "-ListenerSnapshotJson",
                    str(tr.write_listener([])),
                    "-ProbeSnapshotJson",
                    str(tr.write_probe([])),
                ],
                cwd=tr.root,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            status = _load_status(tr.root)
            self.assertEqual(status["code"], "plan")
            _assert_zero_side_effect_counts(self, counts)
        finally:
            tr.cleanup()


# ===========================================================================
# 5b. 正向 start 注入：health → auth → 唯一 LAN frontend
# ===========================================================================


class TestLanPositiveStartOrder(unittest.TestCase):
    """
    严格正向：owned+ready backend 后事件顺序
    health → 回环 GET /api/auth/bootstrap-status（两键严格 true）
    → 唯一 LAN frontend Hidden 启动。
    永远拒绝前端不得绿。
    """

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()

    def tearDown(self) -> None:
        self.tr.cleanup()

    def test_owned_ready_auth_true_starts_single_lan_frontend_in_order(self) -> None:
        """
        注入 owned backend TCP + Cim 进程元数据 + health/auth HTTP 映射。
        统一单调 trace 精确证明 health < auth < 唯一 LAN frontend_start。
        """
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        http = _ready_http_map(auth_required=True, bootstrapped=True)
        # Get-CimInstance 按 PID 回 executablePath/commandLine 后，正确生产可判 owned；
        # 本用例以「实现后必须满足」为业务门；生产未实现时业务红。
        proc, caps, counts, http_events, tcp_queries, unified_trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
            allow_multiple_starts=False,
        )
        # TCP 枚举：backend 回环 8000 + frontend 精确 LanHost:5173
        ports_addrs = {
            (q.get("LocalPort"), q.get("LocalAddress")) for q in tcp_queries
        }
        self.assertIn(
            (_EXPECTED_BACKEND_PORT, _EXPECTED_BACKEND_HOST),
            ports_addrs,
            f"必须枚举回环 backend listener：{tcp_queries!r}",
        )
        self.assertTrue(
            any(
                q.get("LocalPort") == _EXPECTED_FRONTEND_PORT
                and q.get("LocalAddress") == _VALID_LAN_HOST
                for q in tcp_queries
            ),
            f"LAN 5173 必须按精确 LanHost 枚举：{tcp_queries!r}",
        )
        self.assertEqual(counts.get("Start-Process"), 1)
        _assert_unified_trace_health_auth_then_unique_frontend_start(
            self,
            http_events=http_events,
            captures=caps,
            unified_trace=unified_trace,
            host=_VALID_LAN_HOST,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_auth_false_never_starts_frontend(self) -> None:
        """authRequired=false：必须精确观测 health+auth GET，且零 Start-Process。"""
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        http = _ready_http_map(auth_required=False, bootstrapped=True)
        proc, caps, counts, http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        _assert_http_event_order_health_then_auth(self, http_events)
        self.assertEqual(caps, [])
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")

    def test_bootstrapped_false_never_starts_frontend(self) -> None:
        """bootstrapped=false：必须精确观测 health+auth GET，且零 Start-Process。"""
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        http = _ready_http_map(auth_required=True, bootstrapped=False)
        proc, caps, counts, http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        _assert_http_event_order_health_then_auth(self, http_events)
        self.assertEqual(caps, [])
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_admin_not_bootstrapped")

    def test_live_auth_response_missing_key_fail_closed(self) -> None:
        """live auth JSON 缺键 fail-closed，不得仅严校 AuthSnapshot。"""
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        http = {
            _EXPECTED_BACKEND_HEALTH_URL: {
                "StatusCode": 200,
                "Content": json.dumps({"status": "ok", "dbOk": True}, ensure_ascii=False),
            },
            _EXPECTED_AUTH_BOOTSTRAP_URL: {
                "StatusCode": 200,
                # 缺 authRequired
                "Content": json.dumps({"bootstrapped": True}, ensure_ascii=False),
            },
        }
        proc, caps, counts, http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        _assert_http_event_order_health_then_auth(self, http_events)
        self.assertEqual(caps, [])
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")

    def test_live_auth_response_extra_key_fail_closed(self) -> None:
        """live auth JSON 额外键 fail-closed。"""
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        body = {
            "bootstrapped": True,
            "authRequired": True,
            "extra": True,
        }
        http = {
            _EXPECTED_BACKEND_HEALTH_URL: {
                "StatusCode": 200,
                "Content": json.dumps({"status": "ok", "dbOk": True}, ensure_ascii=False),
            },
            _EXPECTED_AUTH_BOOTSTRAP_URL: {
                "StatusCode": 200,
                "Content": json.dumps(body, ensure_ascii=False),
            },
        }
        proc, caps, counts, http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        _assert_http_event_order_health_then_auth(self, http_events)
        self.assertEqual(caps, [])
        self.assertEqual(counts.get("Start-Process", 0), 0)
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "lan_backend_auth_unverified")

    def test_live_auth_response_non_boolean_fail_closed(self) -> None:
        """live auth/bootstrapped 非布尔 fail-closed。"""
        for field, bad in (
            ("authRequired", "true"),
            ("authRequired", 1),
            ("bootstrapped", "yes"),
            ("bootstrapped", 0),
        ):
            with self.subTest(field=field, bad=bad):
                tcp = [_owned_backend_tcp_row(self.tr.root)]
                payload = {"authRequired": True, "bootstrapped": True}
                payload[field] = bad
                http = {
                    _EXPECTED_BACKEND_HEALTH_URL: {
                        "StatusCode": 200,
                        "Content": json.dumps(
                            {"status": "ok", "dbOk": True}, ensure_ascii=False
                        ),
                    },
                    _EXPECTED_AUTH_BOOTSTRAP_URL: {
                        "StatusCode": 200,
                        "Content": json.dumps(payload, ensure_ascii=False),
                    },
                }
                proc, caps, counts, http_events, _tcp, _trace = _run_ps1_start_inject(
                    self.tr.true_source(),
                    [
                        "-Component",
                        "frontend",
                        "-ListenProfile",
                        "lan",
                        "-LanHost",
                        _VALID_LAN_HOST,
                    ],
                    cwd=self.tr.root,
                    env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
                    tcp_rows=tcp,
                    http_map=http,
                )
                _assert_http_event_order_health_then_auth(self, http_events)
                self.assertEqual(caps, [])
                self.assertEqual(counts.get("Start-Process", 0), 0)
                self.assertNotEqual(proc.returncode, 0)
                status = _load_status(self.tr.root)
                self.assertEqual(status["code"], "lan_backend_auth_unverified")


# ===========================================================================
# 5c. LAN 5173 枚举 / owned 幂等 / foreign 不冒充
# ===========================================================================


class TestLanFrontendListenerIdempotency(unittest.TestCase):
    """LAN 5173：精确 LanHost 枚举与探测；owned+ready 幂等；错误地址不冒充。"""

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()

    def tearDown(self) -> None:
        self.tr.cleanup()

    def _diagnose_frontend(
        self,
        *,
        listeners: list[dict[str, Any]],
        probes: list[dict[str, Any]],
        auth: dict[str, Any],
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, int]]:
        return _run_ps1_guarded(
            self.tr.true_source(),
            [
                "-Component",
                "all",
                "-DiagnoseOnly",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
                "-ListenerSnapshotJson",
                str(self.tr.write_listener(listeners)),
                "-ProbeSnapshotJson",
                str(self.tr.write_probe(probes)),
                "-AuthSnapshotJson",
                str(self.tr.write_auth(auth)),
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )

    def test_owned_ready_frontend_is_already_running_zero_start(self) -> None:
        """
        owned+ready LAN frontend：Diagnose overall already_running；
        start 路径 Start-Process=0。
        """
        be = _owned_backend_listener(self.tr.root)
        # 生产 listener 快照仅 4 键；commandLine 含 FrontendDir 以便归属算法可满足
        fe_listener = _owned_frontend_listener(self.tr.root, host=_VALID_LAN_HOST)
        listeners = [be, fe_listener]
        probes = [
            _probe_backend_ready(),
            {"port": _EXPECTED_FRONTEND_PORT, "httpStatus": 200},
        ]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_frontend(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "already_running")
        self.assertEqual(status["code"], "already_running")
        _assert_zero_side_effect_counts(self, counts)

        # start 幂等：注入 TCP owned + ready 探测 → 0 Start-Process
        fe_url = f"http://{_VALID_LAN_HOST}:{_EXPECTED_FRONTEND_PORT}/create"
        tcp = [
            _owned_backend_tcp_row(self.tr.root),
            _owned_frontend_tcp_row(self.tr.root, _VALID_LAN_HOST),
        ]
        http = _ready_http_map(
            auth_required=True, bootstrapped=True, frontend_url=fe_url
        )
        proc2, caps, counts2, http_events, tcp_queries, _trace2 = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        self.assertEqual(caps, [], f"owned+ready 不得重复启动：{caps!r}")
        self.assertEqual(counts2.get("Start-Process", 0), 0)
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        status2 = _load_status(self.tr.root)
        self.assertEqual(status2["overall"], "already_running")
        self.assertEqual(status2["code"], "already_running")
        # 探测 URL 必须精确 LanHost
        probe_urls = [str(e.get("url")) for e in http_events]
        self.assertIn(fe_url, probe_urls, f"必须用 LanHost 探测：{http_events!r}")
        self.assertTrue(
            any(
                q.get("LocalPort") == _EXPECTED_FRONTEND_PORT
                and q.get("LocalAddress") == _VALID_LAN_HOST
                for q in tcp_queries
            ),
            f"必须按 LanHost 枚举 5173：{tcp_queries!r}",
        )

    def test_wrong_address_listener_not_treated_as_owned(self) -> None:
        """错误地址/回环 5173 不得冒充 LAN owned → 精确 frontend_port_foreign。"""
        be = _owned_backend_listener(self.tr.root)
        # 进程形态 owned，但地址为回环而非 LanHost（不得因缺 FrontendDir 假 foreign）
        fe_loop = _owned_frontend_listener(
            self.tr.root, host="127.0.0.1", pid=720002
        )
        listeners = [be, fe_loop]
        probes = [
            _probe_backend_ready(),
            {"port": _EXPECTED_FRONTEND_PORT, "httpStatus": 200},
        ]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_frontend(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "frontend_port_foreign")
        self.assertNotEqual(status["code"], "already_running")
        _assert_zero_side_effect_counts(self, counts)

    def test_owned_lan_frontend_probe_500_is_frontend_not_ready(self) -> None:
        """精确 LanHost owned listener + frontend 500 探针 → frontend_not_ready、零启动。"""
        be = _owned_backend_listener(self.tr.root)
        fe_listener = _owned_frontend_listener(self.tr.root, host=_VALID_LAN_HOST)
        listeners = [be, fe_listener]
        probes = [
            _probe_backend_ready(),
            {"port": _EXPECTED_FRONTEND_PORT, "httpStatus": 500},
        ]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_frontend(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["overall"], "failed")
        self.assertEqual(status["code"], "frontend_not_ready")
        _assert_zero_side_effect_counts(self, counts)

        # start 路径：owned FE + 500 探针映射 → 零 Start-Process
        fe_url = f"http://{_VALID_LAN_HOST}:{_EXPECTED_FRONTEND_PORT}/create"
        tcp = [
            _owned_backend_tcp_row(self.tr.root),
            _owned_frontend_tcp_row(self.tr.root, _VALID_LAN_HOST),
        ]
        http = _ready_http_map(auth_required=True, bootstrapped=True)
        http[fe_url] = {"StatusCode": 500, "Content": "err"}
        proc2, caps, counts2, http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
            tcp_rows=tcp,
            http_map=http,
        )
        self.assertEqual(caps, [], f"owned+500 不得启动：{caps!r}")
        self.assertEqual(counts2.get("Start-Process", 0), 0)
        self.assertNotEqual(proc2.returncode, 0)
        status2 = _load_status(self.tr.root)
        self.assertEqual(status2["code"], "frontend_not_ready")

    def test_owned_lan_frontend_missing_probe_is_frontend_not_ready(self) -> None:
        """精确 LanHost owned listener + 缺探针 → frontend_not_ready、零启动。"""
        be = _owned_backend_listener(self.tr.root)
        fe_listener = _owned_frontend_listener(
            self.tr.root, host=_VALID_LAN_HOST, pid=720003
        )
        listeners = [be, fe_listener]
        # 仅 backend 探针，缺 frontend 探针
        probes = [_probe_backend_ready()]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_frontend(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "frontend_not_ready")
        _assert_zero_side_effect_counts(self, counts)

    def test_foreign_frontend_not_ready_not_impersonated(self) -> None:
        be = _owned_backend_listener(self.tr.root)
        fe_foreign = {
            "port": _EXPECTED_FRONTEND_PORT,
            "pid": 515192,
            "executablePath": r"C:\Windows\System32\svchost.exe",
            "commandLine": "svchost -k netsvcs - foreign-frontend-v1l",
        }
        listeners = [be, fe_foreign]
        probes = [
            _probe_backend_ready(),
            {"port": _EXPECTED_FRONTEND_PORT, "httpStatus": 200},
        ]
        auth = _auth_snapshot(auth_required=True, bootstrapped=True)
        proc, counts = self._diagnose_frontend(
            listeners=listeners, probes=probes, auth=auth
        )
        self.assertNotEqual(proc.returncode, 0)
        status = _load_status(self.tr.root)
        self.assertEqual(status["code"], "frontend_port_foreign")
        _assert_zero_side_effect_counts(self, counts)


# ===========================================================================
# 5d. BIAOSHU 桥接短生命周期 + VITE 存在性 capture
# ===========================================================================


class TestLanEnvBridgeAndViteBasePresence(unittest.TestCase):
    """BIAOSHU_* 仅短生命周期注入并恢复；capture 记录 VITE 是否存在。"""

    def setUp(self) -> None:
        _require_true_source()
        self.tr = _TempRepo()
        self._saved_profile = os.environ.get(_ENV_LISTEN_PROFILE)
        self._saved_host = os.environ.get(_ENV_LAN_HOST)
        self._saved_vite = os.environ.get("VITE_API_BASE_URL")
        # 污染父进程环境，验证脚本调用后可恢复（夹具层断言）
        os.environ[_ENV_LISTEN_PROFILE] = "POLLUTED_PROFILE"
        os.environ[_ENV_LAN_HOST] = "203.0.113.9"

    def tearDown(self) -> None:
        self.tr.cleanup()
        # 恢复测试前环境
        for key, saved in (
            (_ENV_LISTEN_PROFILE, self._saved_profile),
            (_ENV_LAN_HOST, self._saved_host),
            ("VITE_API_BASE_URL", self._saved_vite),
        ):
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved

    def test_biaoshu_bridge_restored_after_start_capture(self) -> None:
        """
        子进程内：调用前污染 BIAOSHU_*；脚本返回后 after 必须等于 before（短生命周期恢复）。
        Vite 子进程瞬时注入由正向 LAN frontend capture 的 EnvListenProfile/EnvLanHost 证明。
        若生产 start 后把 bridge 泄漏为 lan/host 且不恢复 → 本用例业务红。
        """
        self.assertEqual(os.environ.get(_ENV_LISTEN_PROFILE), "POLLUTED_PROFILE")
        self.assertEqual(os.environ.get(_ENV_LAN_HOST), "203.0.113.9")
        # 使用 inject 正向路径：owned+ready+auth 后应启动唯一 frontend，capture 含瞬时 bridge
        tcp = [_owned_backend_tcp_row(self.tr.root)]
        http = _ready_http_map(auth_required=True, bootstrapped=True)
        proc, caps, counts, _http_events, _tcp, _trace = _run_ps1_start_inject(
            self.tr.true_source(),
            [
                "-Component",
                "frontend",
                "-ListenProfile",
                "lan",
                "-LanHost",
                _VALID_LAN_HOST,
            ],
            cwd=self.tr.root,
            env=self.tr.npm_env(
                {
                    "VITE_API_BASE_URL": "/api",
                    _ENV_LISTEN_PROFILE: "POLLUTED_PROFILE",
                    _ENV_LAN_HOST: "203.0.113.9",
                }
            ),
            tcp_rows=tcp,
            http_map=http,
        )
        # 瞬时：frontend Start-Process 时必须注入 lan + host（可失败主证据）
        self.assertEqual(len(caps), 1, f"必须启动 LAN frontend 以观测 bridge：{caps!r}")
        self.assertEqual(counts.get("Start-Process"), 1)
        _assert_lan_frontend_capture(
            self, caps[0], host=_VALID_LAN_HOST, vite_base="/api", vite_present=True
        )
        # 恢复：注入夹具同样输出 env bridge 标记（见 inject 扩展）
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        bridge = _parse_env_bridge(combined)
        before = bridge["before"]
        after = bridge["after"]
        self.assertEqual(str(before.get("ListenProfile", "")), "POLLUTED_PROFILE")
        self.assertEqual(str(before.get("LanHost", "")), "203.0.113.9")
        self.assertEqual(
            str(after.get("ListenProfile", "")),
            str(before.get("ListenProfile", "")),
            f"ListenProfile 值必须恢复：before={before!r} after={after!r}",
        )
        self.assertEqual(
            str(after.get("LanHost", "")),
            str(before.get("LanHost", "")),
            f"LanHost 值必须恢复：before={before!r} after={after!r}",
        )
        self.assertEqual(after.get("ListenProfilePresent"), before.get("ListenProfilePresent"))
        self.assertEqual(after.get("LanHostPresent"), before.get("LanHostPresent"))

    def test_capture_records_vite_api_base_presence_true(self) -> None:
        """
        【helper 自检 / 不计业务覆盖】
        仅验证 start-capture stub 自身 EnvViteApiBasePresent=true 字段形态。
        生产业务门由显式空白失败、未设置正向与 LAN frontend capture 证明。
        """
        _proc, caps, _counts = _run_ps1_start_capture(
            self.tr.true_source(),
            ["-Component", "backend"],
            cwd=self.tr.root,
            env=self.tr.npm_env({"VITE_API_BASE_URL": "/api"}),
        )
        self.assertEqual(len(caps), 1, caps)
        self.assertIs(caps[0].get("EnvViteApiBasePresent"), True)
        self.assertEqual(caps[0].get("EnvViteApiBase"), "/api")

    def test_capture_records_vite_api_base_presence_false_when_unset(self) -> None:
        """
        【helper 自检 / 不计业务覆盖】
        仅验证 stub 在未设置 VITE_API_BASE_URL 时 Present=false；不计业务红。
        """
        env = self.tr.npm_env()
        env.pop("VITE_API_BASE_URL", None)
        _proc, caps, _counts = _run_ps1_start_capture(
            self.tr.true_source(),
            ["-Component", "backend"],
            cwd=self.tr.root,
            env=env,
        )
        self.assertEqual(len(caps), 1, caps)
        self.assertIs(caps[0].get("EnvViteApiBasePresent"), False)


# ===========================================================================
# 6. V1-K 兼容：七键/原子/隐私/零真实副作用
# ===========================================================================


class TestV1kCompatibilityPrivacyAndZeroEffects(unittest.TestCase):
    def test_lan_failure_status_still_seven_keys(self) -> None:
        _require_true_source()
        tr = _TempRepo()
        try:
            proc, counts = _run_ps1_guarded(
                tr.true_source(),
                [
                    "-Component",
                    "all",
                    "-PlanOnly",
                    "-ListenProfile",
                    "lan",
                    # 故意缺 LanHost
                    "-ListenerSnapshotJson",
                    str(tr.write_listener([])),
                    "-ProbeSnapshotJson",
                    str(tr.write_probe([])),
                ],
                cwd=tr.root,
                env=tr.npm_env(),
            )
            self.assertNotEqual(proc.returncode, 0)
            status = _load_status(tr.root)
            _assert_status_schema(self, status, mode="plan", component="all")
            self.assertEqual(len(status.keys()), 7)
            _assert_status_no_privacy_leak(self, status)
            # 诊断输出不得泄漏假路径/密钥
            combined = (proc.stdout or "") + (proc.stderr or "")
            _assert_no_sensitive(self, combined, "diag-output")
            _assert_zero_side_effect_counts(self, counts)
        finally:
            tr.cleanup()

    def test_true_source_has_no_firewall_cmdlets_static(self) -> None:
        """辅助静态门：生产真源不得调用防火墙写入（契约 §7）。"""
        src = _require_true_source()
        text = src.read_text(encoding="utf-8-sig")
        lowered = text.lower()
        for forbidden in (
            "new-netfirewallrule",
            "set-netfirewallrule",
            "netsh advfirewall",
            "enable-netfirewallrule",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_self_module_no_skip_xfail_sleep_or_assert_or(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        # 禁止 skip/xfail 装饰（只认装饰器形态，避免自检字符串误伤）
        self.assertNotRegex(source, r"@unittest\.skip(?:If|Unless)?\b")
        self.assertNotRegex(source, r"@pytest\.mark\.(skip|xfail)\b")
        # 禁止固定 sleep 冒充行为
        self.assertNotRegex(source, r"\btime\.sleep\s*\(")
        # 死 code 不得进入 _V1L_NEW_CODES 运行时集合
        self.assertNotIn("lan_auth_required", _V1L_NEW_CODES)
        self.assertNotIn("lan_auth_required", _ALLOWED_CODES)
        # 任意 assert* 调用参数树不得含 BoolOp Or
        tree = ast.parse(source)
        bad: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if not name.startswith("assert"):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or):
                    bad.append(f"line {node.lineno}: assert* 含 or")
        self.assertEqual(bad, [], "assert 参数不得宽泛 or：" + "; ".join(bad))

    def test_self_module_no_multi_code_conditional_return_or_wide_except(self) -> None:
        """
        反假绿自检：测试方法内禁止
        1) assertIn(..., frozenset/set/list/tuple 多 code 集合)
        2) 条件成功后 early return（if len(caps)==0: ... return）
        3) except AssertionError 后当通过
        4) Q4 缺陷形态：if http_events 软观测；Vite 仅 if env 才清四键；
           inject 缺 Get-CimInstance PID 真路径；TCP 输出 executablePath 假字段
        """
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bad: list[str] = []

        # Q4：禁止对 http_events 做条件软观测（fail 路径必须强制观测 health+auth）
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # 自检文案/字符串字面量放行；仅拦可执行 if 语句
            if re.match(r"if\s+http_events\s*:", stripped):
                bad.append(f"line {i}: 禁止对 http_events 条件软观测")

        # Q4：_load_vite_server_config 必须无条件清四键（不得只在 if env 内 pop）
        m_vite = re.search(
            r"def _load_vite_server_config\([\s\S]*?\n(?:def |class )",
            source,
        )
        if not m_vite:
            bad.append("未找到 _load_vite_server_config")
        else:
            body = m_vite.group(0)
            # 必须出现 for k in _CONTROLLED_ENV_KEYS 且在 if env 之前
            idx_for = body.find("for k in _CONTROLLED_ENV_KEYS")
            idx_if_env = body.find("if env:")
            if idx_for < 0:
                bad.append("Vite 加载必须无条件遍历 _CONTROLLED_ENV_KEYS 清理")
            elif idx_if_env >= 0 and idx_for > idx_if_env:
                bad.append("Vite 四键清理不得嵌在 if env 之后")

        # Q4：inject 必须经 Get-CimInstance + ProcessId 真路径，不得只靠 TCP 假字段
        if "def _ps_cim_process_meta_stub" not in source:
            bad.append("缺少 _ps_cim_process_meta_stub（PID→ExecutablePath/CommandLine）")
        if "Win32_Process" not in source or "ProcessId" not in source:
            bad.append("inject 必须模拟 Win32_Process ProcessId 过滤")
        # Get-NetTCPConnection 返回对象构造处不得含 executablePath
        for m in re.finditer(
            r"function global:Get-NetTCPConnection[\s\S]*?return @\(\$out\.ToArray\(\)\)",
            source,
        ):
            block = m.group(0)
            if "executablePath" in block or "commandLine" in block:
                bad.append("Get-NetTCPConnection 输出不得含 executablePath/commandLine")

        # helper 自检集合必须覆盖两项 EnvViteApiBasePresent stub
        if "_HELPER_SELF_CHECK_TESTS" not in source:
            bad.append("缺少 _HELPER_SELF_CHECK_TESTS")
        else:
            for name in (
                "test_capture_records_vite_api_base_presence_true",
                "test_capture_records_vite_api_base_presence_false_when_unset",
            ):
                if name not in source:
                    bad.append(f"缺少 helper 用例 {name}")

        def _is_multi_container(node: ast.AST) -> bool:
            if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                return len(getattr(node, "elts", [])) >= 2
            if isinstance(node, ast.Call):
                f = node.func
                fname = ""
                if isinstance(f, ast.Name):
                    fname = f.id
                elif isinstance(f, ast.Attribute):
                    fname = f.attr
                if fname in {"frozenset", "set", "list", "tuple"}:
                    if node.args and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple)):
                        return len(node.args[0].elts) >= 2
                    if node.args and isinstance(node.args[0], ast.Dict):
                        return False
            return False

        for node in ast.walk(tree):
            # 多 code assertIn
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name == "assertIn" and len(node.args) >= 2:
                    container = node.args[1]
                    # 仅拦截疑似 code 多选：容器字面量或 frozenset({...})
                    if _is_multi_container(container):
                        # 允许非 code 的合法多值白名单容器（顶层键/状态枚举）通过字段名启发
                        # 若第二参源码含 _ALLOWED 常量名则放行
                        try:
                            seg = ast.get_source_segment(source, container) or ""
                        except Exception:
                            seg = ""
                        if "_ALLOWED_" in seg or "_STATUS_" in seg or "_SERVICE_" in seg:
                            pass
                        elif "lan_" in seg or "listen_profile" in seg or "backend_" in seg or "snapshot_" in seg or "frontend_" in seg:
                            bad.append(
                                f"line {node.lineno}: 禁止多 code assertIn：{seg[:80]}"
                            )
                        elif "{" in seg and ("_" in seg):
                            # 其它裸 frozenset 多值也拒（偏严）
                            bad.append(
                                f"line {node.lineno}: 禁止多值 assertIn 容器：{seg[:80]}"
                            )

            # except AssertionError 宽捕获
            if isinstance(node, ast.ExceptHandler):
                typ = node.type
                tname = ""
                if isinstance(typ, ast.Name):
                    tname = typ.id
                elif isinstance(typ, ast.Attribute):
                    tname = typ.attr
                if tname == "AssertionError":
                    bad.append(f"line {node.lineno}: 禁止 except AssertionError 宽通过")

        # 测试方法内：if ...: ... return 且同分支无 assert 失败语义的条件绿
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            if not cls.name.startswith("Test"):
                continue
            for item in cls.body:
                if not isinstance(item, ast.FunctionDef) or not item.name.startswith(
                    "test_"
                ):
                    continue
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.If):
                        continue
                    # if len(caps) == 0 类条件成功 return
                    for sub in stmt.body:
                        if isinstance(sub, ast.Return):
                            # 若 if 测试含 caps/load_failed 则视为假绿形态
                            try:
                                cond_src = ast.get_source_segment(source, stmt.test) or ""
                            except Exception:
                                cond_src = ""
                            if any(
                                k in cond_src
                                for k in (
                                    "caps",
                                    "load_failed",
                                )
                            ):
                                bad.append(
                                    f"line {stmt.lineno}: 禁止条件成功 early return：{cond_src[:60]}"
                                )

        self.assertEqual(bad, [], "反假绿自检失败：" + "; ".join(bad))


# ===========================================================================
# 7. 自检：新 code 枚举面
# ===========================================================================


class TestV1lCodeEnumeration(unittest.TestCase):
    def test_v1l_new_codes_are_allowed_and_distinct(self) -> None:
        required = {
            "listen_profile_invalid",
            "lan_host_required",
            "lan_host_invalid",
            "lan_backend_auth_unverified",
            "lan_admin_not_bootstrapped",
            "lan_api_base_invalid",
        }
        self.assertEqual(required, set(_V1L_NEW_CODES))
        self.assertTrue(required.issubset(_ALLOWED_CODES))
        self.assertNotIn("lan_auth_required", _V1L_NEW_CODES)
        self.assertNotIn("lan_auth_required", _ALLOWED_CODES)
        # state 不得混入 code
        for state_only in ("planned", "missing", "foreign", "not_ready"):
            self.assertNotIn(state_only, _ALLOWED_CODES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
