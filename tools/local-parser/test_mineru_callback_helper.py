# -*- coding: utf-8 -*-
"""
模块：P8D 本机 MinerU 外置解析助手单元测试
用途：用假 MinerU / 假回环 HTTP 覆盖契约 §7 与计划 §3 的反假绿边界，禁止探测真实 MinerU/模型/公网。
对接：tools/local-parser/mineru_callback_helper.py；unittest；标准库 http.server / tempfile。
二次开发：不得 skip 因环境缺 MinerU；不得把 .cmd 当安全成功证据；票据哨兵必须为合法 43 字符；须证明非 TTY 不调 getpass。
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

# 确保可从仓库根或 tools 目录发现被测模块
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import mineru_callback_helper as helper  # noqa: E402

# P8C secrets.token_urlsafe(32) 形态：恰好 43 个 [A-Za-z0-9_-]
SENTINEL_TICKET = "p8d_SentinelTicket_DoNotLeak_0123456789ABCD"
assert len(SENTINEL_TICKET) == 43
assert helper.TICKET_PATTERN.fullmatch(SENTINEL_TICKET)

FAKE_TASK_ID = "task_fake_p8d_001"


class _RecordingHTTPServer(HTTPServer):
    """用途：记录请求次数与内容，支持预设响应序列。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self.response_queue: list[tuple[int, dict[str, str], bytes]] = []
        self.default_status = 200
        self.default_headers = {"Content-Type": "application/json; charset=utf-8"}
        self.default_body = json.dumps(
            {"ok": True, "chars": 3, "taskId": FAKE_TASK_ID}, ensure_ascii=False
        ).encode("utf-8")
        super().__init__(*args, **kwargs)


def _make_handler(server_holder: list[_RecordingHTTPServer]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            srv = server_holder[0]
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            srv.requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "headers": {k: v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            if srv.response_queue:
                status, headers, resp_body = srv.response_queue.pop(0)
            else:
                status = srv.default_status
                headers = dict(srv.default_headers)
                resp_body = srv.default_body
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(405)
            self.end_headers()

    return Handler


class FakeHTTPServer:
    """用途：本机回环假 HTTP，供回调与 302 零跟随断言。"""

    def __init__(self) -> None:
        self._holder: list[_RecordingHTTPServer] = []
        handler = _make_handler(self._holder)
        self.server = _RecordingHTTPServer(("127.0.0.1", 0), handler)
        self._holder.append(self.server)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.server.requests


def _write_fake_mineru_impl(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = "# ok\n",
    sleep_seconds: float = 0,
    extra_md_names: list[str] | None = None,
    markdown_relpath: str = "result.md",
    exit_code: int = 0,
) -> Path:
    """
    用途：写入假 MinerU 实现脚本；调用记录落到 bin_dir/_last_invocation.json。
    返回：实现脚本路径（由包装或 mock Popen 调用）。
    """
    impl = bin_dir / "_fake_mineru_impl.py"
    record_path = bin_dir / "_last_invocation.json"
    impl.write_text(
        f"""# -*- coding: utf-8 -*-
import json, os, sys, time
from pathlib import Path

argv = list(sys.argv)
env = dict(os.environ)
out = None
for i, a in enumerate(argv):
    if a == "-o" and i + 1 < len(argv):
        out = Path(argv[i + 1])
        break
record = {{"argv": argv, "env": env, "out": str(out) if out else None}}
Path({str(record_path)!r}).write_text(
    json.dumps(record, ensure_ascii=False),
    encoding="utf-8",
)
if out is None:
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
(out / "_invocation.json").write_text(
    json.dumps(record, ensure_ascii=False),
    encoding="utf-8",
)
mode = {mode!r}
sleep_seconds = {sleep_seconds!r}
if sleep_seconds:
    time.sleep(float(sleep_seconds))
if mode == "fail":
    sys.exit(1)
if mode == "no_md":
    sys.exit(0)
if mode == "empty_md":
    (out / "empty.md").write_text("   \\n\\t  ", encoding="utf-8")
    sys.exit(0)
if mode == "multi_md":
    (out / "a.md").write_text("# a\\n", encoding="utf-8")
    (out / "b.md").write_text("# b\\n", encoding="utf-8")
    sys.exit(0)
if mode == "outside_md":
    target = out.parent / "escaped.md"
    target.write_text("# escaped\\n", encoding="utf-8")
    try:
        link = out / "link.md"
        if hasattr(os, "symlink"):
            os.symlink(str(target), str(link))
    except OSError:
        (out / "a.md").write_text("# a\\n", encoding="utf-8")
        (out / "b.md").write_text("# b\\n", encoding="utf-8")
    sys.exit(0)
if mode == "oversized_md":
    # 稀疏/构造超大 .md：成功退出，但字节超过 MAX_JSON_BODY_BYTES，主流程必须拒绝
    md_path = out / "huge.md"
    limit = {helper.MAX_JSON_BODY_BYTES!r}
    with md_path.open("wb") as f:
        f.seek(int(limit))
        f.write(b"x")
    sys.exit(0)
rel = {markdown_relpath!r}
md_path = out / rel
md_path.parent.mkdir(parents=True, exist_ok=True)
# 二进制写入，避免 Windows 文本模式把 \\n 改成 \\r\\n 造成假绿差异
md_path.write_bytes({markdown_text!r}.encode("utf-8"))
for name in {extra_md_names!r} or []:
    (out / name).write_bytes(b"# extra\\n")
sys.exit({exit_code!r})
""",
        encoding="utf-8",
    )
    return impl


def _write_fake_mineru(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = "# ok\n",
    sleep_seconds: float = 0,
    extra_md_names: list[str] | None = None,
    markdown_relpath: str = "result.md",
    exit_code: int = 0,
) -> Path:
    """
    用途：安装 PATH 可解析的假 mineru。
    - Windows：只放 mineru.exe 占位文件（不可当作真实安全 shell 证据），由测试侧 mock Popen 跑 impl。
    - POSIX：可执行普通文件包装脚本。
    返回：which 应解析到的路径。
    """
    impl = _write_fake_mineru_impl(
        bin_dir,
        mode=mode,
        markdown_text=markdown_text,
        sleep_seconds=sleep_seconds,
        extra_md_names=extra_md_names,
        markdown_relpath=markdown_relpath,
        exit_code=exit_code,
    )
    if os.name == "nt":
        wrapper = bin_dir / "mineru.exe"
        # 占位：通过 is_file + .exe 后缀；真正执行由 mock Popen 转调 Python impl
        wrapper.write_bytes(b"MZ-fake-not-executed")
        return wrapper
    wrapper = bin_dir / "mineru"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "{impl}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def _popen_delegate_to_impl(impl: Path):
    """
    用途：Windows 上把对 mineru.exe 的 Popen 转成 python + impl，并精确保留 argv 后缀与 env/shell。
    """
    real_popen = subprocess.Popen

    def _side_effect(cmd: Any, *args: Any, **kwargs: Any):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return real_popen(cmd, *args, **kwargs)
        first = str(cmd[0])
        if first.lower().endswith("mineru.exe") or Path(first).name.lower() in {
            "mineru.exe",
            "mineru",
        }:
            new_cmd = [sys.executable, str(impl), *list(cmd)[1:]]
            return real_popen(new_cmd, *args, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    return _side_effect


def _make_input_file(
    directory: Path,
    name: str = "sample.pdf",
    content: bytes = b"%PDF-1.4 fake",
) -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def _load_invocation(bin_dir: Path) -> dict[str, Any]:
    path = bin_dir / "_last_invocation.json"
    if not path.is_file():
        raise AssertionError("未找到假 MinerU 调用记录 _last_invocation.json")
    return json.loads(path.read_text(encoding="utf-8"))


class HelperUnitTests(unittest.TestCase):
    """用途：纯函数与边界单测。"""

    def test_allowed_extensions_case_insensitive(self) -> None:
        for ext in (".PDF", ".Png", ".JPG", ".JPEG", ".DOCX", ".PPTX", ".XLSX"):
            self.assertTrue(helper.is_allowed_extension(f"a{ext}"))
        self.assertFalse(helper.is_allowed_extension("a.txt"))
        self.assertFalse(helper.is_allowed_extension("a.pdf.exe"))

    def test_validate_input_rejects_missing(self) -> None:
        with self.assertRaises(helper.HelperError) as ctx:
            helper.validate_input_file(str(_HERE / "no_such_file_p8d.pdf"))
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)
        self.assertNotIn("no_such_file", ctx.exception.message)

    def test_validate_input_rejects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(helper.HelperError) as ctx:
                helper.validate_input_file(td)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)

    def test_validate_input_rejects_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "empty.pdf"
            p.write_bytes(b"")
            with self.assertRaises(helper.HelperError) as ctx:
                helper.validate_input_file(str(p))
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)

    def test_validate_input_rejects_bad_extension(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _make_input_file(Path(td), "x.txt")
            with self.assertRaises(helper.HelperError) as ctx:
                helper.validate_input_file(str(p))
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)

    def test_validate_input_rejects_over_50mib(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.pdf"
            with p.open("wb") as f:
                f.seek(helper.MAX_INPUT_BYTES)
                f.write(b"x")
            self.assertEqual(p.stat().st_size, helper.MAX_INPUT_BYTES + 1)
            with self.assertRaises(helper.HelperError) as ctx:
                helper.validate_input_file(str(p))
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)

    def test_validate_input_accepts_exactly_50mib(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "exact.pdf"
            with p.open("wb") as f:
                f.seek(helper.MAX_INPUT_BYTES - 1)
                f.write(b"x")
            self.assertEqual(p.stat().st_size, helper.MAX_INPUT_BYTES)
            got = helper.validate_input_file(str(p))
            self.assertTrue(got.is_file())

    def test_validate_input_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = _make_input_file(root, "real.pdf")
            link = root / "link.pdf"
            try:
                os.symlink(str(real), str(link))
            except OSError:
                with mock.patch.object(Path, "is_symlink", return_value=True):
                    with self.assertRaises(helper.HelperError) as ctx:
                        helper.validate_input_file(str(real))
                    self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)
                return
            with self.assertRaises(helper.HelperError) as ctx:
                helper.validate_input_file(str(link))
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_INPUT)

    def test_normalize_backend_origin_default_and_whitelist(self) -> None:
        self.assertEqual(
            helper.normalize_backend_origin(None),
            "http://127.0.0.1:8000",
        )
        self.assertEqual(
            helper.normalize_backend_origin("http://localhost:8000/"),
            "http://localhost:8000",
        )
        self.assertEqual(
            helper.normalize_backend_origin("http://[::1]:8000"),
            "http://[::1]:8000",
        )
        self.assertEqual(
            helper.normalize_backend_origin("https://127.0.0.1"),
            "https://127.0.0.1:443",
        )

    def test_normalize_backend_origin_rejects_bad(self) -> None:
        bad = [
            "http://example.com:8000",
            "http://127.0.0.1:8000/api",
            "http://127.0.0.1:8000?x=1",
            "http://127.0.0.1:8000#frag",
            "http://user:pass@127.0.0.1:8000",
            "ftp://127.0.0.1:8000",
            "file:///tmp",
            "not-a-url",
            "http://0.0.0.0:8000",
            "http://192.168.1.1:8000",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:-1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:abc",
            "http://::1:8000",  # 缺括号 IPv6
            "http://127.0.0.1:99999",
        ]
        for item in bad:
            with self.subTest(item=item):
                with self.assertRaises(helper.HelperError) as ctx:
                    helper.normalize_backend_origin(item)
                self.assertEqual(ctx.exception.message, helper.MSG_ERR_ORIGIN)

    def test_validate_ticket_format(self) -> None:
        self.assertEqual(helper.validate_ticket(SENTINEL_TICKET), SENTINEL_TICKET)
        bad = [
            "",
            "short",
            "a" * 42,
            "a" * 44,
            SENTINEL_TICKET + "\n",
            SENTINEL_TICKET[:-1] + "\r",
            " " + SENTINEL_TICKET,
            SENTINEL_TICKET + " ",
            SENTINEL_TICKET[:20] + " " + SENTINEL_TICKET[21:],
            SENTINEL_TICKET[:10] + "\n" + SENTINEL_TICKET[11:],
            SENTINEL_TICKET[:10] + "@" + SENTINEL_TICKET[11:],
            SENTINEL_TICKET[:10] + "." + SENTINEL_TICKET[11:],
            "Bearer " + "a" * 36,
            SENTINEL_TICKET[:5] + "\x00" + SENTINEL_TICKET[6:],
        ]
        for item in bad:
            with self.subTest(item=repr(item)[:40]):
                with self.assertRaises(helper.HelperError) as ctx:
                    helper.validate_ticket(item)
                self.assertEqual(ctx.exception.message, helper.MSG_ERR_TICKET)
                self.assertNotIn(SENTINEL_TICKET, ctx.exception.message)

    def test_build_mineru_command_fixed_argv(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inp = _make_input_file(Path(td))
            out = Path(td) / "out"
            out.mkdir()
            cmd = helper.build_mineru_command("/fake/mineru", inp, out)
            self.assertEqual(
                cmd,
                [
                    "/fake/mineru",
                    "-p",
                    str(inp.resolve()),
                    "-o",
                    str(out.resolve()),
                    "-b",
                    "pipeline",
                ],
            )

    # V1-M M1：子进程可写目录必须全部强制到本次 runtime/output 根，不得继承父环境固定缓存
    WRITABLE_RUNTIME_ENV_KEYS = (
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
    READONLY_RUNTIME_ENV_KEYS = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "LANG",
        "LC_ALL",
    )

    def test_build_mineru_env_whitelist_offline_and_strips_secrets(self) -> None:
        """
        用途：V1-M 收紧——build_mineru_env(runtime_dir, source_env) 必须绑定本次运行根；
        可写目录全部等于 runtime_dir；代理/Key/票据不继承；只读系统变量可有限继承。
        """
        with tempfile.TemporaryDirectory(prefix="biaoshu-mineru-env-") as td:
            runtime = Path(td)
            parent = {
                "PATH": "C:\\bin",
                "SystemRoot": "C:\\Windows",
                "WINDIR": "C:\\Windows",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "C",
                "TEMP": "C:\\Temp\\sentinel-parent",
                "TMP": "C:\\Temp\\sentinel-parent-tmp",
                "TMPDIR": "/tmp/sentinel-parent-tmpdir",
                "HTTP_PROXY": "http://proxy.example:8080",
                "HTTPS_PROXY": "http://proxy.example:8080",
                "ALL_PROXY": "socks5://proxy.example:1080",
                "http_proxy": "http://proxy.example:8080",
                "OPENAI_API_KEY": "sk-test",
                "ANTHROPIC_API_KEY": "sk-ant",
                "MINERU_API_KEY": "mk-test",
                "X_LOCAL_PARSE_TICKET": SENTINEL_TICKET,
                "RANDOM_SENTINEL": SENTINEL_TICKET,
                "TICKET": SENTINEL_TICKET,
                "CSRF_TOKEN": "csrf-sentinel",
                "COOKIE": "session=evil",
                "USERPROFILE": "C:\\Users\\sentinel-user",
                "HOME": "/home/sentinel-home",
                "APPDATA": "C:\\Users\\sentinel-user\\AppData\\Roaming",
                "LOCALAPPDATA": "C:\\Users\\sentinel-user\\AppData\\Local",
                "XDG_CACHE_HOME": "/home/sentinel-home/.cache",
                "XDG_CONFIG_HOME": "/home/sentinel-home/.config",
                "XDG_DATA_HOME": "/home/sentinel-home/.local/share",
                "HF_HOME": "/home/sentinel-home/.cache/huggingface",
                "TORCH_HOME": "/home/sentinel-home/.cache/torch",
                "MPLCONFIGDIR": "/home/sentinel-home/.config/matplotlib",
                "PYTHONPYCACHEPREFIX": "/home/sentinel-home/.cache/pycache",
                "NOT_ALLOWED": "drop-me",
            }
            try:
                env = helper.build_mineru_env(runtime, parent)
            except TypeError as exc:
                # failure-first：旧签名只接受 0~1 参 → 记为可计数业务 FAIL，而非 ERROR
                self.fail(
                    "build_mineru_env 尚未切换为 (runtime_dir, source_env) 双参签名"
                    f"（failure-first）：{exc}"
                )
            self.assertEqual(env["PATH"], "C:\\bin")
            self.assertEqual(env["SystemRoot"], "C:\\Windows")
            self.assertEqual(env["MINERU_MODEL_SOURCE"], "local")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")
            self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")
            expected = str(runtime.resolve())
            for key in self.WRITABLE_RUNTIME_ENV_KEYS:
                with self.subTest(writable=key):
                    self.assertIn(key, env)
                    self.assertEqual(str(Path(env[key]).resolve()), expected)
                    if key in parent:
                        self.assertNotEqual(env[key], parent[key])
            for banned in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "MINERU_API_KEY",
                "X_LOCAL_PARSE_TICKET",
                "RANDOM_SENTINEL",
                "TICKET",
                "CSRF_TOKEN",
                "COOKIE",
                "NOT_ALLOWED",
            ):
                self.assertNotIn(banned, env)
            blob = json.dumps(env, ensure_ascii=False)
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sentinel-parent", blob)
            self.assertNotIn("sentinel-user", blob)
            self.assertNotIn("sentinel-home", blob)

    def test_build_mineru_env_requires_existing_runtime_dir(self) -> None:
        """
        用途：V1-C 零参/旧单 Mapping 保持兼容；显式 runtime_dir 缺失/非目录必须失败。
        说明：禁止循环/break/任一异常成功；三种坏路径调用各自独立执行。
        显式双参必须以 HelperError/ValueError/OSError 业务校验失败；
        禁止仅因旧签名 TypeError 被当作门已满足；valid 双参仍为主 failure-first。
        """
        business_errors = (helper.HelperError, ValueError, OSError)

        # V1-C 兼容：零参 dry-run 合法，只校验离线/白名单语义
        env0 = helper.build_mineru_env()
        self.assertEqual(env0["MINERU_MODEL_SOURCE"], "local")
        self.assertEqual(env0["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env0["TRANSFORMERS_OFFLINE"], "1")
        # 旧单 Mapping 兼容：剥离密钥，不得因 V1-M 收紧而硬崩
        env1 = helper.build_mineru_env(
            {
                "PATH": "C:\\compat-bin",
                "OPENAI_API_KEY": "sk-must-strip",
                "HTTP_PROXY": "http://proxy.example:1",
            }
        )
        self.assertEqual(env1.get("PATH"), "C:\\compat-bin")
        self.assertNotIn("OPENAI_API_KEY", env1)
        self.assertNotIn("HTTP_PROXY", env1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = root / "no-such-runtime"
            file_path = root / "not-a-dir.bin"
            file_path.write_bytes(b"x")
            valid = root / "valid-runtime"
            valid.mkdir()

            # valid 双参：新签名 failure-first 主红（TypeError=签名未切换）
            try:
                env_valid = helper.build_mineru_env(valid, {"PATH": "C:\\valid-bin"})
            except TypeError as exc:
                self.fail(
                    "build_mineru_env 尚未切换为 (runtime_dir, source_env) 双参签名"
                    f"（failure-first）：{exc}"
                )
            # 若新签名已就绪：可写根必须绑定 valid，不得静默继承父 HOME/TEMP
            self.assertEqual(env_valid.get("PATH"), "C:\\valid-bin")
            self.assertEqual(env_valid.get("MINERU_MODEL_SOURCE"), "local")
            expected_root = str(valid.resolve())
            for key in (
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "TEMP",
                "TMP",
                "TMPDIR",
            ):
                self.assertIn(key, env_valid)
                self.assertEqual(str(Path(env_valid[key]).resolve()), expected_root)

            # 坏 runtime：三种调用各自独立执行，禁止 break/条件成功
            for bad, bad_label in (
                (missing, "missing"),
                (file_path, "not_a_dir"),
            ):
                # 1) 单参 Path：必须失败（业务异常或旧签名 TypeError 均可计红/绿分支外的失败）
                with self.subTest(form="single_path", bad=bad_label):
                    raised_single = False
                    try:
                        helper.build_mineru_env(bad)
                    except business_errors:
                        raised_single = True
                    except TypeError:
                        # 旧签名把 Path 当 Mapping 的 TypeError：仍视为该调用失败
                        raised_single = True
                    self.assertTrue(
                        raised_single,
                        msg="单参显式 runtime_dir 缺失/非目录必须失败"
                        "（不得静默回退用户 HOME/TEMP）",
                    )

                # 2) 显式双参 (bad, None)：必须业务校验失败；TypeError=签名未就绪=failure-first 红
                with self.subTest(form="dual_none", bad=bad_label):
                    try:
                        helper.build_mineru_env(bad, None)
                        self.fail(
                            "显式双参 build_mineru_env(bad, None) 必须对坏 runtime 失败"
                        )
                    except TypeError as exc:
                        self.fail(
                            "显式双参签名未就绪或把 Path 当 Mapping"
                            f"（failure-first，禁止 TypeError 冒充门通过）：{exc}"
                        )
                    except business_errors:
                        pass

                # 3) 显式双参 (bad, {})：同上，独立执行
                with self.subTest(form="dual_empty_env", bad=bad_label):
                    try:
                        helper.build_mineru_env(bad, {})
                        self.fail(
                            "显式双参 build_mineru_env(bad, {}) 必须对坏 runtime 失败"
                        )
                    except TypeError as exc:
                        self.fail(
                            "显式双参签名未就绪或把 Path 当 Mapping"
                            f"（failure-first，禁止 TypeError 冒充门通过）：{exc}"
                        )
                    except business_errors:
                        pass

    def test_run_mineru_process_popen_kwargs_bind_cwd_and_writable_env(self) -> None:
        """
        用途：V1-M M1 H 主证据——真实 mock Popen kwargs 证明：
        1) cwd 精确等于本次 output_dir；
        2) env 中 USERPROFILE/HOME/APPDATA/LOCALAPPDATA/TEMP/TMP/TMPDIR 全部等于 output 根；
        3) shell=False，且父环境代理/Key/票据哨兵不进入 kwargs.env。
        counts/kwargs 是主证据；源码字符串扫描仅辅助。
        """
        with tempfile.TemporaryDirectory(prefix="biaoshu-mineru-cwd-") as td:
            root = Path(td)
            output_dir = root / "out-root"
            output_dir.mkdir()
            input_path = root / "sample.pdf"
            input_path.write_bytes(b"%PDF-1.4 fake")
            parent_env = {
                "PATH": "C:\\bin",
                "SystemRoot": "C:\\Windows",
                "USERPROFILE": "C:\\Users\\popen-sentinel-user",
                "HOME": "/home/popen-sentinel-home",
                "APPDATA": "C:\\Users\\popen-sentinel-user\\AppData\\Roaming",
                "LOCALAPPDATA": "C:\\Users\\popen-sentinel-user\\AppData\\Local",
                "TEMP": "C:\\Temp\\popen-sentinel-parent",
                "TMP": "C:\\Temp\\popen-sentinel-parent-tmp",
                "TMPDIR": "/tmp/popen-sentinel-parent-tmpdir",
                "HTTP_PROXY": "http://proxy.example:8080",
                "OPENAI_API_KEY": "sk-popen-should-not-leak",
                "X_LOCAL_PARSE_TICKET": SENTINEL_TICKET,
            }
            recorded: list[dict[str, Any]] = []

            class _FakeProc:
                def __init__(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = -15

                def kill(self) -> None:
                    self.returncode = -9

            def spy_popen(cmd: Any, *args: Any, **kwargs: Any):
                recorded.append(
                    {
                        "cmd": list(cmd) if isinstance(cmd, (list, tuple)) else cmd,
                        "args": args,
                        "kwargs": kwargs,
                    }
                )
                return _FakeProc()

            with mock.patch.dict(os.environ, parent_env, clear=False):
                with mock.patch.object(
                    helper.subprocess, "Popen", side_effect=spy_popen
                ) as popen_mock:
                    # 允许实现通过 build_mineru_env(output_dir, ...) 绑定；此处直接跑真实入口
                    helper.run_mineru_process(
                        str(root / "mineru.exe"),
                        input_path,
                        output_dir,
                        timeout_seconds=5,
                    )

            self.assertEqual(popen_mock.call_count, 1, msg="Popen 必须恰好调用 1 次")
            self.assertEqual(len(recorded), 1)
            kwargs = recorded[0]["kwargs"]
            self.assertIs(kwargs.get("shell"), False)
            # 主证据：cwd 精确 output_dir
            cwd_raw = kwargs.get("cwd")
            self.assertIsNotNone(cwd_raw, msg="run_mineru_process 必须向 Popen 传 cwd")
            self.assertEqual(str(Path(cwd_raw).resolve()), str(output_dir.resolve()))
            env = kwargs.get("env")
            self.assertIsInstance(env, dict)
            expected = str(output_dir.resolve())
            for key in (
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "TEMP",
                "TMP",
                "TMPDIR",
            ):
                with self.subTest(popen_env=key):
                    self.assertIn(key, env)
                    self.assertEqual(str(Path(env[key]).resolve()), expected)
                    self.assertNotEqual(env[key], parent_env.get(key))
            blob = json.dumps(env, ensure_ascii=False)
            self.assertNotIn("popen-sentinel-user", blob)
            self.assertNotIn("popen-sentinel-home", blob)
            self.assertNotIn("popen-sentinel-parent", blob)
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sk-popen-should-not-leak", blob)
            self.assertNotIn("HTTP_PROXY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("X_LOCAL_PARSE_TICKET", env)

    def test_validate_filename_basename(self) -> None:
        self.assertEqual(helper.validate_filename_basename("a.pdf"), "a.pdf")
        with self.assertRaises(helper.HelperError):
            helper.validate_filename_basename("")
        with self.assertRaises(helper.HelperError):
            helper.validate_filename_basename("a/b.pdf")
        with self.assertRaises(helper.HelperError):
            helper.validate_filename_basename("a\\b.pdf")
        with self.assertRaises(helper.HelperError):
            helper.validate_filename_basename("a\nb.pdf")
        with self.assertRaises(helper.HelperError):
            helper.validate_filename_basename("x" * 256)

    def test_find_and_read_markdown_zero_one_multi_empty_over(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(helper.HelperError) as ctx:
                helper.find_and_read_markdown(root)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)

            (root / "one.md").write_text("  # hi  \n", encoding="utf-8")
            self.assertEqual(helper.find_and_read_markdown(root), "# hi")

            (root / "two.md").write_text("# two\n", encoding="utf-8")
            with self.assertRaises(helper.HelperError):
                helper.find_and_read_markdown(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "empty.md").write_text("  \n\t", encoding="utf-8")
            with self.assertRaises(helper.HelperError):
                helper.find_and_read_markdown(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            over = "字" * (helper.MAX_MARKDOWN_CODEPOINTS + 1)
            (root / "over.md").write_text(over, encoding="utf-8")
            with self.assertRaises(helper.HelperError):
                helper.find_and_read_markdown(root)

    def test_find_and_read_markdown_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "tmp"
            root.mkdir()
            outside = Path(td) / "outside.md"
            outside.write_text("# outside\n", encoding="utf-8")
            link = root / "link.md"
            try:
                os.symlink(str(outside), str(link))
            except OSError:
                with mock.patch.object(Path, "is_symlink", return_value=True):
                    (root / "link.md").write_text("# x\n", encoding="utf-8")
                    with mock.patch.object(
                        Path,
                        "is_symlink",
                        side_effect=lambda self: self.name == "link.md",
                    ):
                        with self.assertRaises(helper.HelperError) as ctx:
                            helper.find_and_read_markdown(root)
                        self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)
                return
            with self.assertRaises(helper.HelperError) as ctx:
                helper.find_and_read_markdown(root)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)

    def test_find_and_read_markdown_rejects_oversized_bytes_without_read_text(
        self,
    ) -> None:
        """用途：超大 .md 在有界读前拒绝；证明无界 read_text 未被调用。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            huge = root / "huge.md"
            with huge.open("wb") as f:
                f.seek(helper.MAX_JSON_BODY_BYTES)
                f.write(b"x")
            self.assertEqual(huge.stat().st_size, helper.MAX_JSON_BODY_BYTES + 1)

            def boom_read_text(self: Path, *a: Any, **k: Any) -> str:
                raise AssertionError("禁止无界 read_text")

            with mock.patch.object(Path, "read_text", boom_read_text):
                with mock.patch.object(
                    Path, "read_bytes", side_effect=AssertionError("禁止无界 read_bytes")
                ):
                    with self.assertRaises(helper.HelperError) as ctx:
                        helper.find_and_read_markdown(root)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)

    def test_find_and_read_markdown_output_entries_limit_via_mock_walk(self) -> None:
        """用途：输出树条目超限固定失败；mock os.walk，禁止落成千 fixture。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ok.md").write_text("# ok\n", encoding="utf-8")
            limit = helper.MAX_OUTPUT_ENTRIES
            # 模拟：单层即超过上限（目录项+文件项合计）
            fake_dirs = [f"d{i}" for i in range(limit // 2 + 1)]
            fake_files = [f"f{i}.txt" for i in range(limit // 2 + 1)] + ["ok.md"]

            def fake_walk(top: Any, topdown: bool = True, onerror: Any = None, followlinks: bool = False):
                self.assertIs(followlinks, False)
                yield str(root), fake_dirs, fake_files

            with mock.patch.object(helper.os, "walk", side_effect=fake_walk):
                with self.assertRaises(helper.HelperError) as ctx:
                    helper.find_and_read_markdown(root)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)

    def test_find_and_read_markdown_second_md_stops_walk_immediately(self) -> None:
        """用途：遇到第二个 .md 立即失败，不再继续拉取后续 walk 结果。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            walk_steps: list[int] = []

            def fake_walk(top: Any, topdown: bool = True, onerror: Any = None, followlinks: bool = False):
                self.assertIs(followlinks, False)
                walk_steps.append(1)
                yield str(root), ["sub"], ["a.md"]
                walk_steps.append(2)
                yield str(root / "sub"), ["more"], ["b.md"]
                walk_steps.append(3)
                # 若未立即停止，会继续取下一层
                yield str(root / "sub" / "more"), [], ["c.txt"]
                walk_steps.append(4)

            with mock.patch.object(helper.os, "walk", side_effect=fake_walk):
                with self.assertRaises(helper.HelperError) as ctx:
                    helper.find_and_read_markdown(root)
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)
            # 第二层拿到 b.md 后立即失败：仅推进到第二 yield（steps=[1,2]），不得取第三层
            self.assertEqual(walk_steps, [1, 2])
            self.assertNotIn(3, walk_steps)
            self.assertNotIn(4, walk_steps)

    def test_find_and_read_markdown_bounded_binary_read_accepts_small(self) -> None:
        """用途：合法小 md 走二进制有界读，不调用 read_text。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "one.md").write_text("  # hi  \n", encoding="utf-8")

            def boom_read_text(self: Path, *a: Any, **k: Any) -> str:
                raise AssertionError("禁止无界 read_text")

            with mock.patch.object(Path, "read_text", boom_read_text):
                self.assertEqual(helper.find_and_read_markdown(root), "# hi")

    def test_json_body_byte_limit(self) -> None:
        big = "A" * (helper.MAX_JSON_BODY_BYTES)
        with self.assertRaises(helper.HelperError) as ctx:
            helper.build_callback_body(big, "a.pdf")
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_MARKDOWN)

        small = "ok"
        body = helper.build_callback_body(small, "a.pdf")
        self.assertLessEqual(len(body), helper.MAX_JSON_BODY_BYTES)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(
            payload, {"markdown": "ok", "source": "mineru", "filename": "a.pdf"}
        )

    def test_resolve_mineru_rejects_windows_batch_extensions(self) -> None:
        """用途：Windows 明确拒绝 .cmd/.bat/.com/无后缀；POSIX 同步验证后缀拒绝逻辑。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = [
                ("mineru.cmd", b"@echo off\r\n"),
                ("mineru.bat", b"@echo off\r\n"),
                ("mineru.com", b"x"),
                ("mineru", b"x"),
            ]
            for name, content in cases:
                path = root / name
                path.write_bytes(content)
                with self.subTest(name=name):
                    with mock.patch.object(helper.shutil, "which", return_value=str(path)):
                        with mock.patch.object(helper.os, "name", "nt"):
                            with self.assertRaises(helper.HelperError) as ctx:
                                helper.resolve_mineru_executable()
                            self.assertEqual(
                                ctx.exception.message, helper.MSG_ERR_MINERU_MISSING
                            )

    def test_resolve_mineru_accepts_windows_exe_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "mineru.exe"
            exe.write_bytes(b"MZ")
            with mock.patch.object(helper.shutil, "which", return_value=str(exe)):
                with mock.patch.object(helper.os, "name", "nt"):
                    got = helper.resolve_mineru_executable()
            self.assertEqual(Path(got).name.lower(), "mineru.exe")

    def test_post_callback_rejects_malicious_origin_before_request(self) -> None:
        """用途：直接调用 post_callback，恶意 origin 在构造 Request/网络前拒绝。"""
        request_calls: list[Any] = []

        def spy_request(*a: Any, **k: Any):
            request_calls.append((a, k))
            raise AssertionError("不得构造 Request")

        open_calls: list[Any] = []

        class BoomOpener:
            def open(self, *a: Any, **k: Any):
                open_calls.append((a, k))
                raise AssertionError("不得发起网络")

        with mock.patch.object(helper.urllib.request, "Request", side_effect=spy_request):
            with mock.patch.object(
                helper, "_build_no_proxy_opener", return_value=BoomOpener()
            ):
                with self.assertRaises(helper.HelperError) as ctx:
                    helper.post_callback(
                        "http://evil.example:8000",
                        SENTINEL_TICKET,
                        "# md",
                        "a.pdf",
                    )
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_ORIGIN)
        self.assertEqual(request_calls, [])
        self.assertEqual(open_calls, [])

    def test_post_callback_rejects_bad_ticket_before_request(self) -> None:
        request_calls: list[Any] = []

        def spy_request(*a: Any, **k: Any):
            request_calls.append((a, k))
            raise AssertionError("不得构造 Request")

        with mock.patch.object(helper.urllib.request, "Request", side_effect=spy_request):
            with self.assertRaises(helper.HelperError) as ctx:
                helper.post_callback(
                    "http://127.0.0.1:8000",
                    "bad-ticket",
                    "# md",
                    "a.pdf",
                )
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_TICKET)
        self.assertEqual(request_calls, [])


class HelperProcessAndCallbackTests(unittest.TestCase):
    """用途：假进程 + 假 HTTP 集成路径。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.in_dir = self.root / "in"
        self.in_dir.mkdir()
        self.input_path = _make_input_file(self.in_dir)
        self.http = FakeHTTPServer()
        self.http.start()
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        self._old_timeout = helper.MINERU_TIMEOUT_SECONDS
        self._impl: Path | None = None
        self._popen_patcher: Any = None

    def tearDown(self) -> None:
        if self._popen_patcher is not None:
            self._popen_patcher.stop()
            self._popen_patcher = None
        helper.MINERU_TIMEOUT_SECONDS = self._old_timeout
        os.environ["PATH"] = self._old_path
        self.http.stop()
        self._tmpdir.cleanup()

    def _install_fake(self, **kwargs: Any) -> Path:
        wrapper = _write_fake_mineru(self.bin_dir, **kwargs)
        self._impl = self.bin_dir / "_fake_mineru_impl.py"
        if os.name == "nt":
            if self._popen_patcher is not None:
                self._popen_patcher.stop()
            self._popen_patcher = mock.patch.object(
                helper.subprocess,
                "Popen",
                side_effect=_popen_delegate_to_impl(self._impl),
            )
            self._popen_patcher.start()
        return wrapper

    def _patch_tempdir_track(self):
        created: list[Path] = []
        real_td = tempfile.TemporaryDirectory

        class TrackingTD:
            def __init__(self, *a: Any, **k: Any) -> None:
                self._impl = real_td(*a, **k)
                self.name = self._impl.name
                created.append(Path(self.name))

            def __enter__(self) -> str:
                return self._impl.__enter__()

            def __exit__(self, *exc: Any) -> Any:
                return self._impl.__exit__(*exc)

            def cleanup(self) -> None:
                self._impl.cleanup()

        return mock.patch.object(helper.tempfile, "TemporaryDirectory", TrackingTD), created

    def _run_main(
        self,
        *,
        ticket: str = SENTINEL_TICKET,
        origin: str | None = None,
        input_path: Path | None = None,
        argv_extra: list[str] | None = None,
        stdin_isatty: bool = True,
        getpass_side_effect: Any = None,
    ) -> tuple[int, str, str, list[Any]]:
        args = [
            "mineru_callback_helper.py",
            "--input",
            str(input_path or self.input_path),
        ]
        if origin is None:
            origin = self.http.origin
        args.extend(["--backend-origin", origin])
        if argv_extra:
            args.extend(argv_extra)
        stdout = io.StringIO()
        stderr = io.StringIO()
        getpass_calls: list[Any] = []

        def _gp(*a: Any, **k: Any) -> str:
            getpass_calls.append((a, k))
            if getpass_side_effect is not None:
                if callable(getpass_side_effect):
                    return getpass_side_effect(*a, **k)
                raise getpass_side_effect
            return ticket

        with mock.patch.object(sys.stdin, "isatty", return_value=stdin_isatty):
            with mock.patch.object(helper.getpass, "getpass", side_effect=_gp):
                with mock.patch.object(sys, "stdout", stdout):
                    with mock.patch.object(sys, "stderr", stderr):
                        code = helper.main(args)
        return code, stdout.getvalue(), stderr.getvalue(), getpass_calls

    def test_path_missing_mineru(self) -> None:
        os.environ["PATH"] = str(self.root / "empty-bin")
        (self.root / "empty-bin").mkdir(exist_ok=True)
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MINERU_MISSING, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_non_tty_stdin_rejects_without_getpass_or_mineru(self) -> None:
        """用途：stdin 非 TTY 时固定 MSG_ERR_TICKET，getpass 未调用，MinerU/回调均为 0。"""
        self._install_fake(mode="success")
        code, out, err, gp_calls = self._run_main(stdin_isatty=False)
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_TICKET, out + err)
        self.assertEqual(gp_calls, [])
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())
        self.assertEqual(len(self.http.requests), 0)
        self.assertNotIn(SENTINEL_TICKET, out + err)

    def test_success_exact_argv_env_headers_body_once(self) -> None:
        self._install_fake(mode="success", markdown_text="# 标题\n正文")
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn(helper.MSG_SUCCESS, out)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        self.assertNotIn(FAKE_TASK_ID, out + err)
        self.assertNotIn(str(self.input_path.resolve()), out + err)
        self.assertNotIn("# 标题", out + err)

        inv = _load_invocation(self.bin_dir)
        argv = inv["argv"]
        self.assertIn("-p", argv)
        p_idx = argv.index("-p")
        self.assertEqual(Path(argv[p_idx + 1]).resolve(), self.input_path.resolve())
        self.assertEqual(argv[p_idx + 2], "-o")
        out_dir = Path(argv[p_idx + 3]).resolve()
        self.assertEqual(argv[p_idx + 4], "-b")
        self.assertEqual(argv[p_idx + 5], "pipeline")
        self.assertEqual(len(argv), p_idx + 6)
        self.assertNotIn("--api-url", argv)

        env = inv["env"]
        self.assertEqual(env.get("MINERU_MODEL_SOURCE"), "local")
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        for banned in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "OPENAI_API_KEY",
            "X_LOCAL_PARSE_TICKET",
            "RANDOM_SENTINEL",
        ):
            self.assertNotIn(banned, env)
        self.assertNotIn(SENTINEL_TICKET, json.dumps(env, ensure_ascii=False))
        self.assertNotIn("NOT_IN_WHITELIST", env)

        self.assertEqual(len(self.http.requests), 1)
        req = self.http.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/api/local-parser/callback")
        headers = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(headers.get("x-local-parse-ticket"), SENTINEL_TICKET)
        body = json.loads(req["body"].decode("utf-8"))
        self.assertEqual(body["source"], "mineru")
        self.assertEqual(body["filename"], self.input_path.name)
        self.assertEqual(body["markdown"], "# 标题\n正文".strip())

        for p in created:
            self.assertFalse(p.exists(), f"临时目录未清理: {p}")
        self.assertTrue(
            any(str(out_dir).startswith(str(p.resolve())) for p in created)
            or not out_dir.exists()
        )

    def test_windows_shell_false_observes_exe_argv_via_mock(self) -> None:
        """用途：Windows 下 mock Popen 精确观测 shell=False 与 mineru.exe argv/env。"""
        self._install_fake()
        recorded: list[dict[str, Any]] = []
        impl = self.bin_dir / "_fake_mineru_impl.py"
        real_side = _popen_delegate_to_impl(impl)

        def spy(cmd: Any, *a: Any, **k: Any):
            recorded.append({"cmd": list(cmd) if isinstance(cmd, (list, tuple)) else cmd, "kwargs": k})
            return real_side(cmd, *a, **k)

        # 覆盖 setUp 安装的 patcher
        if self._popen_patcher is not None:
            self._popen_patcher.stop()
        self._popen_patcher = mock.patch.object(
            helper.subprocess, "Popen", side_effect=spy
        )
        self._popen_patcher.start()

        # 强制走 Windows 可执行校验语义：which 指向 .exe
        exe = self.bin_dir / "mineru.exe"
        if not exe.is_file():
            exe.write_bytes(b"MZ")
        with mock.patch.object(helper.shutil, "which", return_value=str(exe)):
            with mock.patch.object(helper.os, "name", "nt"):
                code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(len(recorded), 1)
        self.assertIs(recorded[0]["kwargs"].get("shell"), False)
        cmd = recorded[0]["cmd"]
        self.assertIsInstance(cmd, list)
        self.assertTrue(str(cmd[0]).lower().endswith("mineru.exe"))
        self.assertEqual(cmd[1], "-p")
        self.assertEqual(Path(cmd[2]).resolve(), self.input_path.resolve())
        self.assertEqual(cmd[3], "-o")
        self.assertEqual(cmd[5], "-b")
        self.assertEqual(cmd[6], "pipeline")
        self.assertEqual(len(cmd), 7)
        env = recorded[0]["kwargs"].get("env") or {}
        self.assertEqual(env.get("MINERU_MODEL_SOURCE"), "local")
        self.assertNotIn(SENTINEL_TICKET, json.dumps(env, ensure_ascii=False))

    def test_reject_batch_wrapper_on_path(self) -> None:
        """用途：PATH 上仅有 mineru.cmd 时必须失败，不得当作安全成功。"""
        # 清理可能的 exe，只放 .cmd
        for p in self.bin_dir.glob("mineru*"):
            if p.is_file():
                p.unlink()
        cmd_path = self.bin_dir / "mineru.cmd"
        cmd_path.write_text("@echo off\r\necho should-not-run\r\n", encoding="utf-8")
        with mock.patch.object(helper.shutil, "which", return_value=str(cmd_path)):
            with mock.patch.object(helper.os, "name", "nt"):
                code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MINERU_MISSING, out + err)
        self.assertEqual(len(self.http.requests), 0)
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())

    def test_parent_proxy_and_api_key_not_inherited(self) -> None:
        self._install_fake()
        os.environ["HTTP_PROXY"] = "http://evil-proxy.example:8080"
        os.environ["HTTPS_PROXY"] = "http://evil-proxy.example:8080"
        os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
        os.environ["RANDOM_SENTINEL"] = SENTINEL_TICKET
        try:
            code, out, err, _ = self._run_main()
            self.assertEqual(code, 0, msg=out + err)
            inv = _load_invocation(self.bin_dir)
            env = inv["env"]
            self.assertNotIn("HTTP_PROXY", env)
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("RANDOM_SENTINEL", env)
            blob = json.dumps(env, ensure_ascii=False) + out + err
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sk-should-not-leak", blob)
        finally:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "OPENAI_API_KEY", "RANDOM_SENTINEL"):
                os.environ.pop(k, None)

    def test_shell_false_and_no_extra_args(self) -> None:
        self._install_fake()
        recorded: list[dict[str, Any]] = []
        impl = self.bin_dir / "_fake_mineru_impl.py"
        real_side = (
            _popen_delegate_to_impl(impl) if os.name == "nt" else subprocess.Popen
        )

        def spy_popen(*args: Any, **kwargs: Any):
            recorded.append({"args": args, "kwargs": kwargs})
            return real_side(*args, **kwargs)

        if self._popen_patcher is not None:
            self._popen_patcher.stop()
        self._popen_patcher = mock.patch.object(
            helper.subprocess, "Popen", side_effect=spy_popen
        )
        self._popen_patcher.start()
        code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(len(recorded), 1)
        self.assertIs(recorded[0]["kwargs"].get("shell"), False)
        cmd = recorded[0]["args"][0]
        self.assertIsInstance(cmd, list)
        self.assertNotIn("--api-url", cmd)
        self.assertEqual(cmd[-2:], ["-b", "pipeline"])

    def test_ticket_not_in_argv_env_files_output(self) -> None:
        self._install_fake()
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main(ticket=SENTINEL_TICKET)
        self.assertEqual(code, 0, msg=out + err)
        inv = _load_invocation(self.bin_dir)
        self.assertNotIn(SENTINEL_TICKET, json.dumps(inv["argv"], ensure_ascii=False))
        self.assertNotIn(SENTINEL_TICKET, json.dumps(inv["env"], ensure_ascii=False))
        self.assertNotIn(SENTINEL_TICKET, out)
        self.assertNotIn(SENTINEL_TICKET, err)
        side = (self.bin_dir / "_last_invocation.json").read_text(encoding="utf-8")
        self.assertNotIn(SENTINEL_TICKET, side)
        for p in created:
            if p.exists():
                for f in p.rglob("*"):
                    if f.is_file():
                        data = f.read_bytes()
                        self.assertNotIn(SENTINEL_TICKET.encode("utf-8"), data)

    def test_ticket_empty_rejected_no_callback(self) -> None:
        self._install_fake()
        code, out, err, gp = self._run_main(ticket="  ")
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_TICKET, out + err)
        self.assertEqual(len(gp), 1)  # TTY 下会调 getpass，但校验失败
        self.assertEqual(len(self.http.requests), 0)
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())

    def test_ticket_crlf_and_header_injection_rejected(self) -> None:
        self._install_fake()
        poisoned = [
            SENTINEL_TICKET[:20] + "\r\nX-Inject: 1" + SENTINEL_TICKET[20:31],
            SENTINEL_TICKET + "\n",
            "A" * 100,
        ]
        for t in poisoned:
            with self.subTest(t=repr(t)[:30]):
                self.http.server.requests.clear()
                code, out, err, _ = self._run_main(ticket=t)
                self.assertNotEqual(code, 0)
                self.assertIn(helper.MSG_ERR_TICKET, out + err)
                self.assertEqual(len(self.http.requests), 0)
                self.assertNotIn("X-Inject", out + err)
                self.assertNotIn(SENTINEL_TICKET, out + err)

    def test_mineru_nonzero_no_callback_and_cleanup(self) -> None:
        self._install_fake(mode="fail")
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MINERU_FAILED, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_timeout_terminates_and_cleanup(self) -> None:
        self._install_fake(mode="success", sleep_seconds=30)
        helper.MINERU_TIMEOUT_SECONDS = 1
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MINERU_TIMEOUT, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_keyboard_interrupt_terminates_and_cleanup(self) -> None:
        self._install_fake()

        class FakeProc:
            def __init__(self) -> None:
                self.returncode = None
                self.terminated = False
                self.killed = False

            def wait(self, timeout: float | None = None) -> int:
                if self.terminated or self.killed:
                    self.returncode = -1
                    return -1
                raise KeyboardInterrupt

            def terminate(self) -> None:
                self.terminated = True
                self.returncode = -15

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            def poll(self) -> int | None:
                return self.returncode

        fake = FakeProc()
        if self._popen_patcher is not None:
            self._popen_patcher.stop()
            self._popen_patcher = None
        patcher, created = self._patch_tempdir_track()
        with patcher:
            with mock.patch.object(helper.subprocess, "Popen", return_value=fake):
                code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertTrue(fake.terminated or fake.killed)
        self.assertIn(helper.MSG_ERR_INTERRUPTED, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_markdown_zero_multi_empty_no_callback(self) -> None:
        for mode in ("no_md", "multi_md", "empty_md"):
            with self.subTest(mode=mode):
                self.http.server.requests.clear()
                self._install_fake(mode=mode)
                code, out, err, _ = self._run_main()
                self.assertNotEqual(code, 0)
                self.assertIn(helper.MSG_ERR_MARKDOWN, out + err)
                self.assertEqual(len(self.http.requests), 0)

    def test_markdown_codepoint_limit_no_callback(self) -> None:
        over = "字" * (helper.MAX_MARKDOWN_CODEPOINTS + 1)
        self._install_fake(mode="success", markdown_text=over)
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MARKDOWN, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_oversized_markdown_file_no_callback_no_read_text_cleanup(self) -> None:
        """用途：假 MinerU 稀疏超大 md 成功退出；主流程固定失败、回调 0、临时根清理、无 read_text。"""
        self._install_fake(mode="oversized_md")
        patcher, created = self._patch_tempdir_track()
        read_text_calls: list[Any] = []

        def spy_read_text(self: Path, *a: Any, **k: Any) -> str:
            read_text_calls.append((str(self), a, k))
            raise AssertionError("禁止无界 read_text")

        with patcher:
            with mock.patch.object(Path, "read_text", spy_read_text):
                code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MARKDOWN, out + err)
        self.assertEqual(len(self.http.requests), 0)
        self.assertEqual(read_text_calls, [])
        for p in created:
            self.assertFalse(p.exists(), f"临时目录未清理: {p}")

    def test_json_byte_limit_no_callback(self) -> None:
        n = 700_000
        text = "中" * n
        self.assertLessEqual(len(text), helper.MAX_MARKDOWN_CODEPOINTS)
        self._install_fake(mode="success", markdown_text=text)
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_MARKDOWN, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_redirect_302_not_followed_single_request(self) -> None:
        self._install_fake()
        self.http.server.response_queue.append(
            (
                302,
                {"Location": "http://203.0.113.50/steal", "Content-Type": "text/plain"},
                b"redirect",
            )
        )
        self.http.server.response_queue.append(
            (
                200,
                {"Content-Type": "application/json"},
                json.dumps({"ok": True, "chars": 1, "taskId": "x"}).encode("utf-8"),
            )
        )
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_CALLBACK, out + err)
        self.assertEqual(len(self.http.requests), 1)
        self.assertNotIn("203.0.113.50", out + err)
        self.assertNotIn(SENTINEL_TICKET, out + err)

    def test_non_2xx_desensitized_no_retry(self) -> None:
        self._install_fake()
        detail = {
            "code": "local_parser_ticket_invalid",
            "message": "回传票据无效或已失效",
        }
        self.http.server.default_status = 401
        self.http.server.default_body = json.dumps(detail, ensure_ascii=False).encode(
            "utf-8"
        )
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_CALLBACK, out + err)
        self.assertNotIn("local_parser_ticket_invalid", out + err)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        self.assertEqual(len(self.http.requests), 1)

    def test_oversized_2xx_response_fixed_fail_once(self) -> None:
        """用途：超大 2xx JSON 固定 MSG_ERR_CALLBACK_RESPONSE，零 detail 泄漏，一次请求。"""
        self._install_fake()
        huge = b"{" + (b"x" * (helper.MAX_CALLBACK_RESPONSE_BYTES + 1)) + b"}"
        self.http.server.default_status = 200
        self.http.server.default_body = huge
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_CALLBACK_RESPONSE, out + err)
        self.assertEqual(len(self.http.requests), 1)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        # 不回显超大正文片段
        self.assertNotIn("x" * 100, out + err)

    def test_oversized_non_2xx_no_detail_leak_once(self) -> None:
        """用途：超大非 2xx body 不整包读入输出，固定 MSG_ERR_CALLBACK，一次请求。"""
        self._install_fake()
        secret_detail = ("SECRET_DETAIL_" + "Z" * 10000).encode("utf-8")
        self.http.server.default_status = 500
        self.http.server.default_body = secret_detail
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_CALLBACK, out + err)
        self.assertEqual(len(self.http.requests), 1)
        self.assertNotIn("SECRET_DETAIL_", out + err)
        self.assertNotIn(SENTINEL_TICKET, out + err)

    def test_http_error_does_not_read_full_body(self) -> None:
        """用途：HTTPError 路径禁止 exc.read() 整包。"""
        import urllib.error as ue

        self._install_fake()
        read_calls: list[int] = []

        class RealishHTTPError(ue.HTTPError):
            def __init__(self) -> None:
                Exception.__init__(self, "fake")
                self.code = 500
                self.msg = "err"
                self.hdrs = {}  # type: ignore[assignment]
                self.fp = None
                self.filename = "http://127.0.0.1/x"
                self.closed = False

            def read(self, n: int = -1) -> bytes:  # type: ignore[override]
                read_calls.append(1)
                return b"DETAIL_LEAK_" + b"Q" * 50000

            def close(self) -> None:
                self.closed = True

        err_obj = RealishHTTPError()

        class BoomOpener:
            def open(self, *a: Any, **k: Any):
                raise err_obj

        with mock.patch.object(
            helper, "_build_no_proxy_opener", return_value=BoomOpener()
        ):
            code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_CALLBACK, out + err)
        self.assertEqual(read_calls, [])
        self.assertTrue(err_obj.closed)
        self.assertNotIn("DETAIL_LEAK_", out + err)

    def test_success_response_must_have_ok_chars_taskid(self) -> None:
        self._install_fake()
        bad_bodies = [
            {"ok": False, "chars": 1, "taskId": "t1"},
            {"ok": True, "chars": -1, "taskId": "t1"},
            {"ok": True, "chars": 1, "taskId": ""},
            {"ok": True, "chars": 1},
            {"ok": True, "taskId": "t1"},
            "not-json-object",
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.http.server.requests.clear()
                if isinstance(body, str):
                    raw = body.encode("utf-8")
                else:
                    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.http.server.default_status = 200
                self.http.server.default_body = raw
                code, out, err, _ = self._run_main()
                self.assertNotEqual(code, 0)
                self.assertIn(helper.MSG_ERR_CALLBACK_RESPONSE, out + err)
                self.assertEqual(len(self.http.requests), 1)
                self.assertNotIn("task_", out + err)

    def test_argv_rejects_ticket_and_unknown_flags(self) -> None:
        self._install_fake()
        code, out, err, _ = self._run_main(argv_extra=["--ticket", SENTINEL_TICKET])
        self.assertNotEqual(code, 0)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_main_only_input_and_origin_flags(self) -> None:
        ns = helper.parse_args(
            ["mineru_callback_helper.py", "--input", str(self.input_path)]
        )
        self.assertEqual(ns.input, str(self.input_path))
        self.assertIsNone(ns.backend_origin)
        with self.assertRaises(helper.HelperError) as ctx:
            helper.parse_args(["mineru_callback_helper.py"])
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_USAGE)

    def test_main_bad_origin_port_reports_origin_not_callback(self) -> None:
        self._install_fake()
        code, out, err, gp = self._run_main(origin="http://127.0.0.1:65536")
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_ORIGIN, out + err)
        self.assertNotIn(helper.MSG_ERR_CALLBACK, out + err)
        # origin 在 getpass 之前失败
        self.assertEqual(gp, [])
        self.assertEqual(len(self.http.requests), 0)


class ParseArgsAndModuleTests(unittest.TestCase):
    def test_module_is_stdlib_only_imports(self) -> None:
        source = Path(helper.__file__).read_text(encoding="utf-8")
        for banned in ("requests", "httpx", "fastapi", "aiohttp", "urllib3"):
            self.assertNotRegex(source, rf"^\s*import {banned}\b", banned)
            self.assertNotRegex(source, rf"^\s*from {banned}\b", banned)

    def test_no_executable_flag_in_parser(self) -> None:
        with self.assertRaises(helper.HelperError) as ctx:
            helper.parse_args(
                [
                    "mineru_callback_helper.py",
                    "--input",
                    "a.pdf",
                    "--executable",
                    "evil",
                ]
            )
        self.assertEqual(ctx.exception.message, helper.MSG_ERR_USAGE)
        self.assertNotIn("evil", ctx.exception.message)

    def test_ticket_length_constant_matches_token_urlsafe_32(self) -> None:
        import secrets

        sample = secrets.token_urlsafe(32)
        self.assertEqual(len(sample), helper.TICKET_LENGTH)
        self.assertIsNotNone(helper.TICKET_PATTERN.fullmatch(sample))


if __name__ == "__main__":
    unittest.main()
