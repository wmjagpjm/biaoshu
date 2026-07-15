# -*- coding: utf-8 -*-
"""
模块：P8E 本机 Docling 外置解析助手单元测试
用途：用假 docling.exe/POSIX 可执行文件 + 假回环 HTTP 覆盖契约 §4/§6.2 反假绿边界，禁止探测真实 Docling/模型/公网。
对接：tools/local-parser/docling_callback_helper.py；mineru_callback_helper 共享回调；unittest；标准库 http.server。
二次开发：不得 skip 因环境缺 Docling；不得把 .cmd 当安全成功证据；假 CLI 必须真实写唯一 Markdown；须证明非法内部 source 零 Request。
"""

from __future__ import annotations

import io
import json
import os
import re
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

import docling_callback_helper as helper  # noqa: E402
import mineru_callback_helper as shared  # noqa: E402

# P8C secrets.token_urlsafe(32) 形态：恰好 43 个 [A-Za-z0-9_-]
SENTINEL_TICKET = "p8e_SentinelTicket_DoNotLeak_0123456789ABCD"
assert len(SENTINEL_TICKET) == 43
assert shared.TICKET_PATTERN.fullmatch(SENTINEL_TICKET)

FAKE_TASK_ID = "task_fake_p8e_001"

# 七扩展 → 五种 --from
EXT_FROM_CASES = [
    ("sample.pdf", "pdf"),
    ("a.PNG", "image"),
    ("b.JPG", "image"),
    ("c.jpeg", "image"),
    ("d.DOCX", "docx"),
    ("e.pptx", "pptx"),
    ("f.xlsx", "xlsx"),
]


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
    """用途：本机回环假 HTTP，供回调与零跟随断言。"""

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


def _write_fake_docling_impl(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = "# ok\n",
    sleep_seconds: float = 0,
    markdown_relpath: str = "result.md",
    exit_code: int = 0,
) -> Path:
    """
    用途：写入假 Docling 实现脚本；调用记录落到 bin_dir/_last_invocation.json。
    二次开发：必须按 argv 中 --output 真实写出唯一 Markdown，禁止只 mock 最终结果。
    """
    impl = bin_dir / "_fake_docling_impl.py"
    record_path = bin_dir / "_last_invocation.json"
    impl.write_text(
        f"""# -*- coding: utf-8 -*-
import json, os, sys, time
from pathlib import Path

argv = list(sys.argv)
env = dict(os.environ)
cwd = os.getcwd()
out = None
for i, a in enumerate(argv):
    if a == "--output" and i + 1 < len(argv):
        out = Path(argv[i + 1])
        break
record = {{
    "argv": argv,
    "env": env,
    "out": str(out) if out else None,
    "cwd": cwd,
}}
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
if mode == "oversized_md":
    md_path = out / "huge.md"
    limit = {shared.MAX_JSON_BODY_BYTES!r}
    with md_path.open("wb") as f:
        f.seek(int(limit))
        f.write(b"x")
    sys.exit(0)
rel = {markdown_relpath!r}
md_path = out / rel
md_path.parent.mkdir(parents=True, exist_ok=True)
md_path.write_bytes({markdown_text!r}.encode("utf-8"))
sys.exit({exit_code!r})
""",
        encoding="utf-8",
    )
    return impl


def _write_fake_docling(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = "# ok\n",
    sleep_seconds: float = 0,
    markdown_relpath: str = "result.md",
    exit_code: int = 0,
) -> Path:
    """
    用途：安装 PATH 可解析的假 docling。
    - Windows：只放 docling.exe 占位，由测试侧 mock Popen 跑 impl。
    - POSIX：可执行普通文件包装脚本。
    """
    impl = _write_fake_docling_impl(
        bin_dir,
        mode=mode,
        markdown_text=markdown_text,
        sleep_seconds=sleep_seconds,
        markdown_relpath=markdown_relpath,
        exit_code=exit_code,
    )
    if os.name == "nt":
        wrapper = bin_dir / "docling.exe"
        wrapper.write_bytes(b"MZ-fake-not-executed")
        return wrapper
    wrapper = bin_dir / "docling"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "{impl}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def _popen_delegate_to_impl(impl: Path):
    """
    用途：Windows 上把对 docling.exe 的 Popen 转成 python + impl，精确保留 argv 后缀与 env/shell。
    """
    real_popen = subprocess.Popen

    def _side_effect(cmd: Any, *args: Any, **kwargs: Any):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return real_popen(cmd, *args, **kwargs)
        first = str(cmd[0])
        if first.lower().endswith("docling.exe") or Path(first).name.lower() in {
            "docling.exe",
            "docling",
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


def _make_artifacts_dir(directory: Path, name: str = "models") -> Path:
    path = directory / name
    path.mkdir(parents=True, exist_ok=True)
    # 放一个哨兵文件，证明助手不检查模型内容
    (path / "placeholder.bin").write_bytes(b"not-a-real-model")
    return path


def _load_invocation(bin_dir: Path) -> dict[str, Any]:
    path = bin_dir / "_last_invocation.json"
    if not path.is_file():
        raise AssertionError("未找到假 Docling 调用记录 _last_invocation.json")
    return json.loads(path.read_text(encoding="utf-8"))


class SharedCallbackSourceTests(unittest.TestCase):
    """用途：共享 body/回调 source 参数化与非法 source 零 Request。"""

    def test_build_callback_body_default_mineru(self) -> None:
        body = shared.build_callback_body("ok", "a.pdf")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(
            payload, {"markdown": "ok", "source": "mineru", "filename": "a.pdf"}
        )

    def test_build_callback_body_explicit_docling(self) -> None:
        body = shared.build_callback_body("ok", "a.pdf", source="docling")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(
            payload, {"markdown": "ok", "source": "docling", "filename": "a.pdf"}
        )

    def test_build_callback_body_rejects_illegal_source(self) -> None:
        bad = [
            "Docling",
            " docling",
            "docling ",
            "docling-extra",
            "unknown",
            "",
            "MINERU",
            None,
            123,
        ]
        for item in bad:
            with self.subTest(source=repr(item)):
                with self.assertRaises(shared.HelperError) as ctx:
                    shared.build_callback_body("ok", "a.pdf", source=item)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.message, shared.MSG_ERR_CALLBACK)
                # 空串/None 的 str 会无意义地包含于任意中文消息，仅对非空字符串断言不反射
                if item is not None and str(item):
                    self.assertNotIn(str(item), ctx.exception.message)

    def test_post_callback_illegal_source_zero_request(self) -> None:
        """用途：非法内部 source 在构造 Request 前固定失败，零网络。"""
        request_calls: list[Any] = []
        open_calls: list[Any] = []

        def spy_request(*a: Any, **k: Any):
            request_calls.append((a, k))
            raise AssertionError("不得构造 Request")

        class BoomOpener:
            def open(self, *a: Any, **k: Any):
                open_calls.append((a, k))
                raise AssertionError("不得发起网络")

        with mock.patch.object(shared.urllib.request, "Request", side_effect=spy_request):
            with mock.patch.object(
                shared, "_build_no_proxy_opener", return_value=BoomOpener()
            ):
                with self.assertRaises(shared.HelperError) as ctx:
                    shared.post_callback(
                        "http://127.0.0.1:8000",
                        SENTINEL_TICKET,
                        "# md",
                        "a.pdf",
                        source="Docling",
                    )
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_CALLBACK)
        self.assertEqual(request_calls, [])
        self.assertEqual(open_calls, [])

    def test_allowed_callback_sources_exact(self) -> None:
        self.assertEqual(shared.ALLOWED_CALLBACK_SOURCES, frozenset({"mineru", "docling"}))


class DoclingUnitTests(unittest.TestCase):
    """用途：Docling 专属纯函数与边界。"""

    def test_map_extension_to_from_seven_types(self) -> None:
        for name, expected in EXT_FROM_CASES:
            with self.subTest(name=name):
                self.assertEqual(helper.map_extension_to_from(name), expected)

    def test_map_extension_rejects_unknown(self) -> None:
        with self.assertRaises(shared.HelperError) as ctx:
            helper.map_extension_to_from("a.txt")
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_INPUT)

    def test_validate_artifacts_accepts_plain_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            art = _make_artifacts_dir(Path(td))
            got = helper.validate_artifacts_path(str(art))
            self.assertTrue(got.is_dir())

    def test_validate_artifacts_rejects_missing_file_url_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = [
                str(root / "no-such-dir"),
                str(_make_input_file(root)),  # 文件
                "http://example.com/models",
                "https://127.0.0.1/models",
                "file:///tmp/models",
                "",
                "   ",
            ]
            for item in cases:
                with self.subTest(item=item[:40]):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.validate_artifacts_path(item)
                    self.assertEqual(ctx.exception.message, helper.MSG_ERR_ARTIFACTS)
                    # 不得回显绝对路径/URL
                    if item.strip():
                        self.assertNotIn(item.strip(), ctx.exception.message)

            real = _make_artifacts_dir(root, "real-models")
            link = root / "link-models"
            try:
                os.symlink(str(real), str(link), target_is_directory=True)
            except OSError:
                with mock.patch.object(Path, "is_symlink", return_value=True):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.validate_artifacts_path(str(real))
                    self.assertEqual(ctx.exception.message, helper.MSG_ERR_ARTIFACTS)
                return
            with self.assertRaises(shared.HelperError) as ctx:
                helper.validate_artifacts_path(str(link))
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_ARTIFACTS)

    def test_resolve_docling_rejects_windows_batch_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = [
                ("docling.cmd", b"@echo off\r\n"),
                ("docling.bat", b"@echo off\r\n"),
                ("docling.com", b"x"),
                ("docling", b"x"),
            ]
            for name, content in cases:
                path = root / name
                path.write_bytes(content)
                with self.subTest(name=name):
                    with mock.patch.object(helper.shutil, "which", return_value=str(path)):
                        with mock.patch.object(helper.os, "name", "nt"):
                            with self.assertRaises(shared.HelperError) as ctx:
                                helper.resolve_docling_executable()
                            self.assertEqual(
                                ctx.exception.message, helper.MSG_ERR_DOCLING_MISSING
                            )

    def test_resolve_docling_accepts_windows_exe_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "docling.exe"
            exe.write_bytes(b"MZ")
            with mock.patch.object(helper.shutil, "which", return_value=str(exe)):
                with mock.patch.object(helper.os, "name", "nt"):
                    got = helper.resolve_docling_executable()
            self.assertEqual(Path(got).name.lower(), "docling.exe")

    def test_resolve_docling_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "docling.exe"
            real.write_bytes(b"MZ")
            link = root / "docling-link.exe"
            try:
                os.symlink(str(real), str(link))
            except OSError:
                with mock.patch.object(helper.shutil, "which", return_value=str(real)):
                    with mock.patch.object(Path, "is_symlink", return_value=True):
                        with self.assertRaises(shared.HelperError) as ctx:
                            helper.resolve_docling_executable()
                        self.assertEqual(
                            ctx.exception.message, helper.MSG_ERR_DOCLING_MISSING
                        )
                return
            with mock.patch.object(helper.shutil, "which", return_value=str(link)):
                with mock.patch.object(helper.os, "name", "nt"):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.resolve_docling_executable()
                    self.assertEqual(
                        ctx.exception.message, helper.MSG_ERR_DOCLING_MISSING
                    )

    def test_resolve_docling_missing(self) -> None:
        with mock.patch.object(helper.shutil, "which", return_value=None):
            with self.assertRaises(shared.HelperError) as ctx:
                helper.resolve_docling_executable()
            self.assertEqual(ctx.exception.message, helper.MSG_ERR_DOCLING_MISSING)

    def test_build_docling_command_fixed_argv_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = _make_input_file(root)
            out = root / "out"
            out.mkdir()
            art = _make_artifacts_dir(root)
            cmd = helper.build_docling_command(
                "/fake/docling", inp, out, art, "pdf"
            )
            self.assertEqual(
                cmd,
                [
                    "/fake/docling",
                    "convert",
                    "--from",
                    "pdf",
                    "--to",
                    "md",
                    "--image-export-mode",
                    "placeholder",
                    "--pipeline",
                    "standard",
                    "--artifacts-path",
                    str(art.resolve()),
                    "--no-enable-remote-services",
                    "--no-allow-external-plugins",
                    "--abort-on-error",
                    "--document-timeout",
                    "1800",
                    "--num-threads",
                    "1",
                    "--device",
                    "cpu",
                    "--output",
                    str(out.resolve()),
                    str(inp.resolve()),
                ],
            )
            self.assertNotIn("convert-remote", cmd)
            self.assertNotIn("--headers", cmd)
            self.assertNotIn("--api-key", cmd)

    def test_build_docling_command_rejects_illegal_from_format(self) -> None:
        """用途：from_format 仅允许 pdf/image/docx/pptx/xlsx 精确枚举。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = _make_input_file(root)
            out = root / "out"
            out.mkdir()
            art = _make_artifacts_dir(root)
            for bad in (
                "PDF",
                "pdf ",
                " pdf",
                "md",
                "html",
                "ocr",
                "image/png",
                "docx-extra",
                "",
                "unknown",
            ):
                with self.subTest(from_format=repr(bad)):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.build_docling_command(
                            "/fake/docling", inp, out, art, bad
                        )
                    self.assertEqual(ctx.exception.message, shared.MSG_ERR_INPUT)
                    if bad:
                        self.assertNotIn(bad, ctx.exception.message)

    # 契约 §4.3：子进程可写目录必须全部强制到本次 output_dir，不得继承父环境固定缓存
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
    # 只读运行所需：可从父环境白名单继承
    READONLY_RUNTIME_ENV_KEYS = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "LANG",
        "LC_ALL",
    )

    def test_build_docling_env_whitelist_offline_and_strips_secrets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="biaoshu-docling-") as td:
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
                "DOCLING_SERVICE_URL": "https://evil.example",
                "DOCLING_SERVICE_API_KEY": "dk-test",
                "DOCLING_ARTIFACTS_PATH": "C:\\evil\\models",
                "HF_TOKEN": "hf_secret",
                "HUGGING_FACE_HUB_TOKEN": "hf_secret2",
                "OPENAI_API_KEY": "sk-test",
                "X_LOCAL_PARSE_TICKET": SENTINEL_TICKET,
                "RANDOM_SENTINEL": SENTINEL_TICKET,
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
            env = helper.build_docling_env(runtime, parent)
            self.assertEqual(env["PATH"], "C:\\bin")
            self.assertEqual(env["SystemRoot"], "C:\\Windows")
            self.assertEqual(env["WINDIR"], "C:\\Windows")
            self.assertEqual(env["LANG"], "en_US.UTF-8")
            self.assertEqual(env["LC_ALL"], "C")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")
            self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")
            self.assertNotIn("MINERU_MODEL_SOURCE", env)
            expected = str(runtime.resolve())
            for key in self.WRITABLE_RUNTIME_ENV_KEYS:
                with self.subTest(writable=key):
                    self.assertIn(key, env)
                    self.assertEqual(str(Path(env[key]).resolve()), expected)
                    # 不得继承父环境哨兵路径
                    if key in parent:
                        self.assertNotEqual(env[key], parent[key])
            for banned in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "DOCLING_SERVICE_URL",
                "DOCLING_SERVICE_API_KEY",
                "DOCLING_ARTIFACTS_PATH",
                "HF_TOKEN",
                "HUGGING_FACE_HUB_TOKEN",
                "OPENAI_API_KEY",
                "X_LOCAL_PARSE_TICKET",
                "RANDOM_SENTINEL",
                "NOT_ALLOWED",
            ):
                self.assertNotIn(banned, env)
            blob = json.dumps(env, ensure_ascii=False)
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sentinel-parent", blob)
            self.assertNotIn("sentinel-user", blob)
            self.assertNotIn("sentinel-home", blob)

    def test_build_docling_env_requires_existing_runtime_dir(self) -> None:
        """用途：build_docling_env 必须显式接收并校验本次运行目录，不得回退用户固定目录。"""
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no-such-runtime"
            with self.assertRaises((shared.HelperError, ValueError, OSError, TypeError)):
                helper.build_docling_env(missing)
            # 文件路径不得当作运行目录
            file_path = Path(td) / "not-a-dir.bin"
            file_path.write_bytes(b"x")
            with self.assertRaises((shared.HelperError, ValueError, OSError, TypeError)):
                helper.build_docling_env(file_path)
        # 缺少 runtime 参数应失败（禁止无参回退到用户 HOME/TEMP）
        with self.assertRaises(TypeError):
            helper.build_docling_env()  # type: ignore[call-arg]

    def test_no_docling_terminate_wait_constant(self) -> None:
        """用途：删除已无用途的 DOCLING_TERMINATE_WAIT_SECONDS，避免误导为 Docling 自有等待值。"""
        self.assertFalse(hasattr(helper, "DOCLING_TERMINATE_WAIT_SECONDS"))
        source = Path(helper.__file__).read_text(encoding="utf-8")
        self.assertNotIn("DOCLING_TERMINATE_WAIT_SECONDS", source)


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
        self.artifacts_path = _make_artifacts_dir(self.root)
        self.http = FakeHTTPServer()
        self.http.start()
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        self._old_timeout = helper.DOCLING_TIMEOUT_SECONDS
        self._impl: Path | None = None
        self._popen_patcher: Any = None

    def tearDown(self) -> None:
        if self._popen_patcher is not None:
            self._popen_patcher.stop()
            self._popen_patcher = None
        helper.DOCLING_TIMEOUT_SECONDS = self._old_timeout
        os.environ["PATH"] = self._old_path
        self.http.stop()
        self._tmpdir.cleanup()

    def _install_fake(self, **kwargs: Any) -> Path:
        wrapper = _write_fake_docling(self.bin_dir, **kwargs)
        self._impl = self.bin_dir / "_fake_docling_impl.py"
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
                # 前缀契约
                if k.get("prefix"):
                    self.prefix = k["prefix"]

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
        artifacts_path: Path | None = None,
        argv_extra: list[str] | None = None,
        stdin_isatty: bool = True,
        getpass_side_effect: Any = None,
    ) -> tuple[int, str, str, list[Any]]:
        args = [
            "docling_callback_helper.py",
            "--input",
            str(input_path or self.input_path),
            "--artifacts-path",
            str(artifacts_path or self.artifacts_path),
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
            with mock.patch.object(shared.getpass, "getpass", side_effect=_gp):
                with mock.patch.object(sys, "stdout", stdout):
                    with mock.patch.object(sys, "stderr", stderr):
                        code = helper.main(args)
        return code, stdout.getvalue(), stderr.getvalue(), getpass_calls

    def test_path_missing_docling(self) -> None:
        os.environ["PATH"] = str(self.root / "empty-bin")
        (self.root / "empty-bin").mkdir(exist_ok=True)
        code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_MISSING, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_non_tty_stdin_rejects_without_getpass_or_docling(self) -> None:
        self._install_fake(mode="success")
        code, out, err, gp_calls = self._run_main(stdin_isatty=False)
        self.assertNotEqual(code, 0)
        self.assertIn(shared.MSG_ERR_TICKET, out + err)
        self.assertEqual(gp_calls, [])
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())
        self.assertEqual(len(self.http.requests), 0)
        self.assertNotIn(SENTINEL_TICKET, out + err)

    def test_success_exact_argv_env_headers_body_source_docling_once(self) -> None:
        self._install_fake(mode="success", markdown_text="# 标题\n正文")
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn(shared.MSG_SUCCESS, out)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        self.assertNotIn(FAKE_TASK_ID, out + err)
        self.assertNotIn(str(self.input_path.resolve()), out + err)
        self.assertNotIn(str(self.artifacts_path.resolve()), out + err)
        self.assertNotIn("# 标题", out + err)

        inv = _load_invocation(self.bin_dir)
        argv = inv["argv"]
        # 假 impl 的 argv[0] 是脚本路径；真实观测看 convert 起的固定后缀
        # Windows mock 会把 exe 换成 python+impl，因此找 convert 起点
        if "convert" in argv:
            c_idx = argv.index("convert")
        else:
            self.fail(f"argv 缺少 convert: {argv}")
        fixed = argv[c_idx:]
        self.assertEqual(fixed[0], "convert")
        self.assertEqual(fixed[1], "--from")
        self.assertEqual(fixed[2], "pdf")
        self.assertEqual(fixed[3], "--to")
        self.assertEqual(fixed[4], "md")
        self.assertEqual(fixed[5], "--image-export-mode")
        self.assertEqual(fixed[6], "placeholder")
        self.assertEqual(fixed[7], "--pipeline")
        self.assertEqual(fixed[8], "standard")
        self.assertEqual(fixed[9], "--artifacts-path")
        self.assertEqual(Path(fixed[10]).resolve(), self.artifacts_path.resolve())
        self.assertEqual(fixed[11], "--no-enable-remote-services")
        self.assertEqual(fixed[12], "--no-allow-external-plugins")
        self.assertEqual(fixed[13], "--abort-on-error")
        self.assertEqual(fixed[14], "--document-timeout")
        self.assertEqual(fixed[15], "1800")
        self.assertEqual(fixed[16], "--num-threads")
        self.assertEqual(fixed[17], "1")
        self.assertEqual(fixed[18], "--device")
        self.assertEqual(fixed[19], "cpu")
        self.assertEqual(fixed[20], "--output")
        out_dir = Path(fixed[21]).resolve()
        self.assertEqual(Path(fixed[22]).resolve(), self.input_path.resolve())
        self.assertEqual(len(fixed), 23)
        self.assertNotIn("convert-remote", argv)
        self.assertNotIn("--headers", argv)

        # 子进程 cwd 必须是本次临时输出目录，而非仓库/输入/模型目录
        self.assertIn("cwd", inv)
        self.assertEqual(Path(inv["cwd"]).resolve(), out_dir)
        self.assertNotEqual(Path(inv["cwd"]).resolve(), Path.cwd().resolve())
        self.assertNotEqual(Path(inv["cwd"]).resolve(), self.in_dir.resolve())
        self.assertNotEqual(Path(inv["cwd"]).resolve(), self.artifacts_path.resolve())

        env = inv["env"]
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        # 假 CLI 真实记录的 env/cwd：所有可写目录必须等于本次 cwd/--output
        expected_runtime = str(out_dir.resolve())
        self.assertEqual(str(Path(inv["cwd"]).resolve()), expected_runtime)
        for key in DoclingUnitTests.WRITABLE_RUNTIME_ENV_KEYS:
            with self.subTest(writable_env=key):
                self.assertIn(key, env, f"缺少可写目录变量 {key}")
                self.assertEqual(
                    str(Path(env[key]).resolve()),
                    expected_runtime,
                    f"{key} 未指向本次 output_dir",
                )
        for banned in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "DOCLING_SERVICE_URL",
            "DOCLING_SERVICE_API_KEY",
            "DOCLING_ARTIFACTS_PATH",
            "OPENAI_API_KEY",
            "X_LOCAL_PARSE_TICKET",
            "RANDOM_SENTINEL",
            "MINERU_MODEL_SOURCE",
        ):
            self.assertNotIn(banned, env)
        self.assertNotIn(SENTINEL_TICKET, json.dumps(env, ensure_ascii=False))

        self.assertEqual(len(self.http.requests), 1)
        req = self.http.requests[0]
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["path"], "/api/local-parser/callback")
        headers = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(headers.get("x-local-parse-ticket"), SENTINEL_TICKET)
        body = json.loads(req["body"].decode("utf-8"))
        self.assertEqual(body["source"], "docling")
        self.assertEqual(body["filename"], self.input_path.name)
        self.assertEqual(body["markdown"], "# 标题\n正文".strip())

        for p in created:
            self.assertFalse(p.exists(), f"临时目录未清理: {p}")
        self.assertFalse(out_dir.exists(), f"输出目录未清理: {out_dir}")

    def test_extension_from_mapping_via_real_argv(self) -> None:
        """用途：七类扩展通过真实假 CLI 观测 --from 精确映射。"""
        for name, expected_from in EXT_FROM_CASES:
            with self.subTest(name=name):
                self.http.server.requests.clear()
                # 清理上次 invocation
                inv_path = self.bin_dir / "_last_invocation.json"
                if inv_path.is_file():
                    inv_path.unlink()
                inp = _make_input_file(self.in_dir, name=name)
                self._install_fake(mode="success", markdown_text="# ok\n")
                code, out, err, _ = self._run_main(input_path=inp)
                self.assertEqual(code, 0, msg=out + err)
                inv = _load_invocation(self.bin_dir)
                argv = inv["argv"]
                self.assertIn("--from", argv)
                f_idx = argv.index("--from")
                self.assertEqual(argv[f_idx + 1], expected_from)
                body = json.loads(self.http.requests[-1]["body"].decode("utf-8"))
                self.assertEqual(body["source"], "docling")
                self.assertEqual(body["filename"], name if name == Path(name).name else Path(name).name)

    def test_windows_shell_false_observes_exe_argv_via_mock(self) -> None:
        """用途：Windows 下 mock Popen 精确观测 shell=False 与 docling.exe 完整 argv/env/DEVNULL。"""
        self._install_fake()
        recorded: list[dict[str, Any]] = []
        impl = self.bin_dir / "_fake_docling_impl.py"
        real_side = _popen_delegate_to_impl(impl)

        def spy(cmd: Any, *a: Any, **k: Any):
            recorded.append(
                {"cmd": list(cmd) if isinstance(cmd, (list, tuple)) else cmd, "kwargs": k}
            )
            return real_side(cmd, *a, **k)

        if self._popen_patcher is not None:
            self._popen_patcher.stop()
        self._popen_patcher = mock.patch.object(
            helper.subprocess, "Popen", side_effect=spy
        )
        self._popen_patcher.start()

        exe = self.bin_dir / "docling.exe"
        if not exe.is_file():
            exe.write_bytes(b"MZ")
        with mock.patch.object(helper.shutil, "which", return_value=str(exe)):
            with mock.patch.object(helper.os, "name", "nt"):
                code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(len(recorded), 1)
        kw = recorded[0]["kwargs"]
        self.assertIs(kw.get("shell"), False)
        self.assertIs(kw.get("stdout"), subprocess.DEVNULL)
        self.assertIs(kw.get("stderr"), subprocess.DEVNULL)
        self.assertIs(kw.get("stdin"), subprocess.DEVNULL)
        # cwd 必须显式等于 --output 临时目录
        self.assertIn("cwd", kw)
        out_idx = recorded[0]["cmd"].index("--output")
        expected_cwd = str(Path(recorded[0]["cmd"][out_idx + 1]).resolve())
        self.assertEqual(str(Path(kw["cwd"]).resolve()), expected_cwd)
        cmd = recorded[0]["cmd"]
        self.assertIsInstance(cmd, list)
        self.assertTrue(str(cmd[0]).lower().endswith("docling.exe"))
        self.assertEqual(cmd[1], "convert")
        self.assertEqual(cmd[2], "--from")
        self.assertEqual(cmd[3], "pdf")
        self.assertEqual(cmd[-1], str(self.input_path.resolve()))
        self.assertNotIn("convert-remote", cmd)
        env = kw.get("env") or {}
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        self.assertNotIn(SENTINEL_TICKET, json.dumps(env, ensure_ascii=False))
        self.assertNotIn("DOCLING_SERVICE_URL", env)

    def test_reject_batch_wrapper_on_path(self) -> None:
        for p in self.bin_dir.glob("docling*"):
            if p.is_file():
                p.unlink()
        cmd_path = self.bin_dir / "docling.cmd"
        cmd_path.write_text("@echo off\r\necho should-not-run\r\n", encoding="utf-8")
        with mock.patch.object(helper.shutil, "which", return_value=str(cmd_path)):
            with mock.patch.object(helper.os, "name", "nt"):
                code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_MISSING, out + err)
        self.assertEqual(len(self.http.requests), 0)
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())

    def test_parent_proxy_and_docling_secrets_not_inherited(self) -> None:
        """用途：父进程敏感环境不继承；用 patch.dict 可恢复，不永久污染测试进程。"""
        self._install_fake()
        dirty = {
            "HTTP_PROXY": "http://evil-proxy.example:8080",
            "HTTPS_PROXY": "http://evil-proxy.example:8080",
            "DOCLING_SERVICE_URL": "https://evil.example",
            "DOCLING_SERVICE_API_KEY": "dk-should-not-leak",
            "DOCLING_ARTIFACTS_PATH": "C:\\evil\\models",
            "HF_TOKEN": "hf_should_not_leak",
            "OPENAI_API_KEY": "sk-should-not-leak",
            "RANDOM_SENTINEL": SENTINEL_TICKET,
        }
        # 先快照原值（含原本不存在），退出后必须精确恢复，禁止“与脏值不等”近似
        original_snapshot: dict[str, str | None] = {
            k: os.environ.get(k) for k in dirty
        }
        with mock.patch.dict(os.environ, dirty, clear=False):
            code, out, err, _ = self._run_main()
            self.assertEqual(code, 0, msg=out + err)
            inv = _load_invocation(self.bin_dir)
            env = inv["env"]
            for banned in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "DOCLING_SERVICE_URL",
                "DOCLING_SERVICE_API_KEY",
                "DOCLING_ARTIFACTS_PATH",
                "HF_TOKEN",
                "OPENAI_API_KEY",
                "RANDOM_SENTINEL",
            ):
                self.assertNotIn(banned, env)
            blob = json.dumps(env, ensure_ascii=False) + out + err
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sk-should-not-leak", blob)
            self.assertNotIn("dk-should-not-leak", blob)
            self.assertNotIn("hf_should_not_leak", blob)
        for k, original in original_snapshot.items():
            with self.subTest(restore=k):
                if original is None:
                    self.assertNotIn(k, os.environ)
                else:
                    self.assertEqual(os.environ.get(k), original)

    def test_writable_runtime_dirs_isolated_from_parent_sentinels(self) -> None:
        """
        用途：父环境注入不同哨兵路径后，假 CLI 记录的每个可写目录变量仍等于 cwd/--output；
        TemporaryDirectory 退出后该目录不存在；票据/代理/API Key 仍不进入。
        """
        self._install_fake(mode="success", markdown_text="# ok\n")
        sentinel_root = self.root / "parent-fixed-cache-sentinel"
        sentinel_root.mkdir(exist_ok=True)
        dirty = {
            "HOME": str(sentinel_root / "home"),
            "USERPROFILE": str(sentinel_root / "userprofile"),
            "APPDATA": str(sentinel_root / "appdata"),
            "LOCALAPPDATA": str(sentinel_root / "localappdata"),
            "TEMP": str(sentinel_root / "temp"),
            "TMP": str(sentinel_root / "tmp"),
            "TMPDIR": str(sentinel_root / "tmpdir"),
            "XDG_CACHE_HOME": str(sentinel_root / "xdg-cache"),
            "XDG_CONFIG_HOME": str(sentinel_root / "xdg-config"),
            "XDG_DATA_HOME": str(sentinel_root / "xdg-data"),
            "HF_HOME": str(sentinel_root / "hf-home"),
            "TORCH_HOME": str(sentinel_root / "torch-home"),
            "MPLCONFIGDIR": str(sentinel_root / "mpl"),
            "PYTHONPYCACHEPREFIX": str(sentinel_root / "pycache"),
            "HTTP_PROXY": "http://evil-proxy.example:8080",
            "HTTPS_PROXY": "http://evil-proxy.example:8080",
            "DOCLING_SERVICE_URL": "https://evil.example",
            "DOCLING_SERVICE_API_KEY": "dk-should-not-leak",
            "OPENAI_API_KEY": "sk-should-not-leak",
            "X_LOCAL_PARSE_TICKET": SENTINEL_TICKET,
            "RANDOM_SENTINEL": SENTINEL_TICKET,
        }
        original_snapshot: dict[str, str | None] = {
            k: os.environ.get(k) for k in dirty
        }
        patcher, created = self._patch_tempdir_track()
        with mock.patch.dict(os.environ, dirty, clear=False):
            with patcher:
                code, out, err, _ = self._run_main()
            self.assertEqual(code, 0, msg=out + err)
            inv = _load_invocation(self.bin_dir)
            env = inv["env"]
            argv = inv["argv"]
            self.assertIn("--output", argv)
            out_dir = Path(argv[argv.index("--output") + 1]).resolve()
            expected = str(out_dir)
            self.assertEqual(str(Path(inv["cwd"]).resolve()), expected)
            for key in DoclingUnitTests.WRITABLE_RUNTIME_ENV_KEYS:
                with self.subTest(key=key):
                    self.assertIn(key, env)
                    self.assertEqual(str(Path(env[key]).resolve()), expected)
                    self.assertNotEqual(str(Path(env[key]).resolve()), str(Path(dirty[key]).resolve()))
                    # 哨兵路径字符串不得出现在子进程 env
                    self.assertNotIn(str(sentinel_root), env[key])
            for banned in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "DOCLING_SERVICE_URL",
                "DOCLING_SERVICE_API_KEY",
                "OPENAI_API_KEY",
                "X_LOCAL_PARSE_TICKET",
                "RANDOM_SENTINEL",
            ):
                self.assertNotIn(banned, env)
            blob = json.dumps(env, ensure_ascii=False) + out + err
            self.assertNotIn(SENTINEL_TICKET, blob)
            self.assertNotIn("sk-should-not-leak", blob)
            self.assertNotIn("dk-should-not-leak", blob)
            self.assertNotIn(str(sentinel_root), blob)
            # TemporaryDirectory 退出后运行目录必须不存在
            self.assertFalse(out_dir.exists(), f"运行目录未清理: {out_dir}")
            for p in created:
                self.assertFalse(p.exists(), f"临时目录未清理: {p}")
        for k, original in original_snapshot.items():
            with self.subTest(restore=k):
                if original is None:
                    self.assertNotIn(k, os.environ)
                else:
                    self.assertEqual(os.environ.get(k), original)

    def test_tempdir_prefix_biaoshu_docling(self) -> None:
        self._install_fake()
        prefixes: list[str] = []
        real_td = tempfile.TemporaryDirectory

        class CaptureTD:
            def __init__(self, *a: Any, **k: Any) -> None:
                prefixes.append(str(k.get("prefix") or (a[0] if a else "")))
                self._impl = real_td(*a, **k)
                self.name = self._impl.name

            def __enter__(self) -> str:
                return self._impl.__enter__()

            def __exit__(self, *exc: Any) -> Any:
                return self._impl.__exit__(*exc)

        with mock.patch.object(helper.tempfile, "TemporaryDirectory", CaptureTD):
            code, out, err, _ = self._run_main()
        self.assertEqual(code, 0, msg=out + err)
        self.assertTrue(any(p == "biaoshu-docling-" for p in prefixes))

    def test_input_rejects_empty_oversize_bad_ext(self) -> None:
        self._install_fake()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            empty = root / "empty.pdf"
            empty.write_bytes(b"")
            code, out, err, _ = self._run_main(input_path=empty)
            self.assertNotEqual(code, 0)
            self.assertIn(shared.MSG_ERR_INPUT, out + err)
            self.assertEqual(len(self.http.requests), 0)

            bad = _make_input_file(root, "x.txt")
            code, out, err, _ = self._run_main(input_path=bad)
            self.assertNotEqual(code, 0)
            self.assertIn(shared.MSG_ERR_INPUT, out + err)

            big = root / "big.pdf"
            with big.open("wb") as f:
                f.seek(shared.MAX_INPUT_BYTES)
                f.write(b"x")
            code, out, err, _ = self._run_main(input_path=big)
            self.assertNotEqual(code, 0)
            self.assertIn(shared.MSG_ERR_INPUT, out + err)
            self.assertEqual(len(self.http.requests), 0)

    def test_artifacts_invalid_no_process_no_callback(self) -> None:
        self._install_fake()
        missing = self.root / "no-models"
        code, out, err, gp = self._run_main(artifacts_path=missing)
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_ARTIFACTS, out + err)
        self.assertEqual(gp, [])  # artifacts 在 getpass 前失败
        self.assertFalse((self.bin_dir / "_last_invocation.json").is_file())
        self.assertEqual(len(self.http.requests), 0)

        file_as_art = _make_input_file(self.root, "not-dir.bin")
        code, out, err, gp = self._run_main(artifacts_path=file_as_art)
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_ARTIFACTS, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_docling_nonzero_no_callback_and_cleanup(self) -> None:
        self._install_fake(mode="fail")
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_FAILED, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_timeout_terminates_and_cleanup(self) -> None:
        self._install_fake(mode="success", sleep_seconds=30)
        helper.DOCLING_TIMEOUT_SECONDS = 1
        before_wait = shared.MINERU_TERMINATE_WAIT_SECONDS
        patcher, created = self._patch_tempdir_track()
        with patcher:
            code, out, err, _ = self._run_main()
        after_wait = shared.MINERU_TERMINATE_WAIT_SECONDS
        self.assertEqual(before_wait, after_wait)
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_TIMEOUT, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_timeout_does_not_mutate_shared_terminate_wait_constant(self) -> None:
        """用途：超时路径直接 terminate_process，不得读写/修改共享 MINERU_TERMINATE_WAIT_SECONDS。"""
        self._install_fake(mode="success", sleep_seconds=30)
        helper.DOCLING_TIMEOUT_SECONDS = 1
        before = shared.MINERU_TERMINATE_WAIT_SECONDS
        # 源码静态断言：不得出现对共享等待常量的赋值
        source = Path(helper.__file__).read_text(encoding="utf-8")
        self.assertNotIn("MINERU_TERMINATE_WAIT_SECONDS", source)
        self.assertNotRegex(source, r"(?m)^\s*import mineru_callback_helper as p8d\b")
        code, out, err, _ = self._run_main()
        self.assertEqual(shared.MINERU_TERMINATE_WAIT_SECONDS, before)
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_TIMEOUT, out + err)
        self.assertEqual(len(self.http.requests), 0)

    def test_run_pipeline_rejects_invalid_absolute_artifacts_before_popen(self) -> None:
        """用途：run_pipeline 必须无条件 validate_artifacts_path，绝对 Path 不可绕过。"""
        self._install_fake()
        popen_calls: list[Any] = []
        real_popen = helper.subprocess.Popen

        def spy_popen(*a: Any, **k: Any):
            popen_calls.append((a, k))
            return real_popen(*a, **k)

        cases: list[tuple[str, Path]] = []
        missing = (self.root / "abs-missing-models").resolve()
        cases.append(("missing", missing))
        file_path = _make_input_file(self.root, "abs-file-as-models.bin").resolve()
        cases.append(("file", file_path))

        real_art = _make_artifacts_dir(self.root, "abs-real-models").resolve()
        link = (self.root / "abs-link-models").resolve()
        symlink_ready = False
        try:
            if link.exists():
                link.unlink()
            os.symlink(str(real_art), str(link), target_is_directory=True)
            symlink_ready = True
            cases.append(("symlink", link))
        except OSError:
            pass

        for label, art in cases:
            with self.subTest(case=label, path=str(art)[:60]):
                self.http.server.requests.clear()
                popen_calls.clear()
                inv_path = self.bin_dir / "_last_invocation.json"
                if inv_path.is_file():
                    inv_path.unlink()
                with mock.patch.object(helper.subprocess, "Popen", side_effect=spy_popen):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.run_pipeline(
                            input_path=self.input_path.resolve(),
                            artifacts_path=art,
                            origin=self.http.origin,
                            ticket=SENTINEL_TICKET,
                            docling=str(self.bin_dir / ("docling.exe" if os.name == "nt" else "docling")),
                        )
                self.assertEqual(ctx.exception.message, helper.MSG_ERR_ARTIFACTS)
                self.assertEqual(popen_calls, [])
                self.assertEqual(len(self.http.requests), 0)
                self.assertFalse(inv_path.is_file())
                self.assertNotIn(str(art), ctx.exception.message)

        if not symlink_ready:
            # Windows 无 symlink 权限时用 is_symlink mock 覆盖绝对路径绕过
            with mock.patch.object(Path, "is_symlink", return_value=True):
                with mock.patch.object(helper.subprocess, "Popen", side_effect=spy_popen):
                    with self.assertRaises(shared.HelperError) as ctx:
                        helper.run_pipeline(
                            input_path=self.input_path.resolve(),
                            artifacts_path=real_art,
                            origin=self.http.origin,
                            ticket=SENTINEL_TICKET,
                            docling=str(self.bin_dir / ("docling.exe" if os.name == "nt" else "docling")),
                        )
                self.assertEqual(ctx.exception.message, helper.MSG_ERR_ARTIFACTS)
                self.assertEqual(popen_calls, [])
                self.assertEqual(len(self.http.requests), 0)

    def test_run_pipeline_illegal_from_format_before_popen_via_map(self) -> None:
        """用途：非法 from_format 在 Popen/callback 前失败（直接 build + map 旁路）。"""
        self._install_fake()
        popen_calls: list[Any] = []

        def boom_popen(*a: Any, **k: Any):
            popen_calls.append((a, k))
            raise AssertionError("不得 Popen")

        with mock.patch.object(helper, "map_extension_to_from", return_value="PDF"):
            with mock.patch.object(helper.subprocess, "Popen", side_effect=boom_popen):
                with self.assertRaises(shared.HelperError) as ctx:
                    helper.run_pipeline(
                        input_path=self.input_path.resolve(),
                        artifacts_path=self.artifacts_path.resolve(),
                        origin=self.http.origin,
                        ticket=SENTINEL_TICKET,
                        docling=str(self.bin_dir / ("docling.exe" if os.name == "nt" else "docling")),
                    )
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_INPUT)
        self.assertEqual(popen_calls, [])
        self.assertEqual(len(self.http.requests), 0)

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
        self.assertIn(shared.MSG_ERR_INTERRUPTED, out + err)
        self.assertEqual(len(self.http.requests), 0)
        for p in created:
            self.assertFalse(p.exists())

    def test_startup_oserror_fixed_fail_no_callback(self) -> None:
        self._install_fake()
        if self._popen_patcher is not None:
            self._popen_patcher.stop()
            self._popen_patcher = None

        def boom(*a: Any, **k: Any):
            raise OSError("cannot start")

        with mock.patch.object(helper.subprocess, "Popen", side_effect=boom):
            code, out, err, _ = self._run_main()
        self.assertNotEqual(code, 0)
        self.assertIn(helper.MSG_ERR_DOCLING_FAILED, out + err)
        self.assertEqual(len(self.http.requests), 0)
        self.assertNotIn("cannot start", out + err)

    def test_markdown_zero_multi_empty_no_callback(self) -> None:
        for mode in ("no_md", "multi_md", "empty_md"):
            with self.subTest(mode=mode):
                self.http.server.requests.clear()
                self._install_fake(mode=mode)
                code, out, err, _ = self._run_main()
                self.assertNotEqual(code, 0)
                self.assertIn(shared.MSG_ERR_MARKDOWN, out + err)
                self.assertEqual(len(self.http.requests), 0)

    def test_oversized_markdown_file_no_callback_no_read_text_cleanup(self) -> None:
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
        self.assertIn(shared.MSG_ERR_MARKDOWN, out + err)
        self.assertEqual(len(self.http.requests), 0)
        self.assertEqual(read_text_calls, [])
        for p in created:
            self.assertFalse(p.exists())

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

    def test_argv_rejects_ticket_executable_unknown_flags(self) -> None:
        self._install_fake()
        for extra in (
            ["--ticket", SENTINEL_TICKET],
            ["--executable", "evil"],
            ["--api-key", "x"],
        ):
            with self.subTest(extra=extra):
                code, out, err, _ = self._run_main(argv_extra=list(extra))
                self.assertNotEqual(code, 0)
                self.assertNotIn(SENTINEL_TICKET, out + err)
                self.assertNotIn("evil", out + err)
                self.assertEqual(len(self.http.requests), 0)

    def test_main_requires_input_and_artifacts(self) -> None:
        with self.assertRaises(shared.HelperError) as ctx:
            helper.parse_args(["docling_callback_helper.py"])
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_USAGE)

        with self.assertRaises(shared.HelperError) as ctx:
            helper.parse_args(
                ["docling_callback_helper.py", "--input", str(self.input_path)]
            )
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_USAGE)

        ns = helper.parse_args(
            [
                "docling_callback_helper.py",
                "--input",
                str(self.input_path),
                "--artifacts-path",
                str(self.artifacts_path),
            ]
        )
        self.assertEqual(ns.input, str(self.input_path))
        self.assertEqual(ns.artifacts_path, str(self.artifacts_path))
        self.assertIsNone(ns.backend_origin)

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
        self.assertIn(shared.MSG_ERR_CALLBACK, out + err)
        self.assertNotIn("local_parser_ticket_invalid", out + err)
        self.assertNotIn(SENTINEL_TICKET, out + err)
        self.assertEqual(len(self.http.requests), 1)


class ModuleBoundaryTests(unittest.TestCase):
    # 多行模式：^ 匹配每一行行首，否则仅文件首行会被检查（假绿）
    _RE_IMPORT = r"(?m)^\s*import {name}\b"
    _RE_FROM = r"(?m)^\s*from {name}\b"
    _RE_DEF = r"(?m)^def {name}\("
    _BANNED_THIRD_PARTY = ("requests", "httpx", "fastapi", "aiohttp", "urllib3")
    _BANNED_COPY_DEFS = (
        "post_callback",
        "find_and_read_markdown",
        "build_callback_body",
        "normalize_backend_origin",
    )

    def _assert_no_banned_third_party(self, source: str) -> None:
        for banned in self._BANNED_THIRD_PARTY:
            self.assertNotRegex(
                source, self._RE_IMPORT.format(name=banned), banned
            )
            self.assertNotRegex(source, self._RE_FROM.format(name=banned), banned)

    def _assert_no_copied_p8d_defs(self, source: str) -> None:
        for name in self._BANNED_COPY_DEFS:
            self.assertNotRegex(source, self._RE_DEF.format(name=name), name)

    def _assert_detects_banned_third_party(self, source: str) -> None:
        """用途：变异探针——合成源码必须被检测为违规。"""
        detected = False
        for banned in self._BANNED_THIRD_PARTY:
            if re.search(self._RE_IMPORT.format(name=banned), source) or re.search(
                self._RE_FROM.format(name=banned), source
            ):
                detected = True
                break
        self.assertTrue(detected, "合成第三方 import 未被多行正则捕获")

    def _assert_detects_copied_p8d_defs(self, source: str) -> None:
        detected = False
        for name in self._BANNED_COPY_DEFS:
            if re.search(self._RE_DEF.format(name=name), source):
                detected = True
                break
        self.assertTrue(detected, "合成复制 P8D 函数定义未被多行正则捕获")

    def test_module_is_stdlib_only_imports(self) -> None:
        source = Path(helper.__file__).read_text(encoding="utf-8")
        self._assert_no_banned_third_party(source)

    def test_no_copy_of_p8d_http_markdown_impl(self) -> None:
        """用途：Docling 助手应 import 复用，而非复制 post_callback/find_and_read_markdown 实现。"""
        source = Path(helper.__file__).read_text(encoding="utf-8")
        self.assertIn("from mineru_callback_helper import", source)
        self.assertIn("post_callback", source)
        self.assertIn("find_and_read_markdown", source)
        self._assert_no_copied_p8d_defs(source)

    def test_static_anti_cheat_detects_synthetic_violations(self) -> None:
        """用途：最小合成源码/变异探针，证明反作弊正则真正有效，不能只扫真文件得绿。"""
        synthetic_imports = (
            "import os\n"
            "import requests\n"
            "from httpx import Client\n"
            "import fastapi\n"
            "from aiohttp import ClientSession\n"
            "import urllib3\n"
        )
        self._assert_detects_banned_third_party(synthetic_imports)
        # 无违规的干净合成源码必须通过
        clean = "import os\nimport sys\nfrom pathlib import Path\n"
        self._assert_no_banned_third_party(clean)

        synthetic_defs = (
            "import os\n"
            "def post_callback(origin, ticket, markdown, filename):\n"
            "    pass\n"
            "def find_and_read_markdown(root):\n"
            "    pass\n"
            "def build_callback_body(md, name):\n"
            "    pass\n"
            "def normalize_backend_origin(origin):\n"
            "    pass\n"
        )
        self._assert_detects_copied_p8d_defs(synthetic_defs)
        clean_defs = "def run_pipeline():\n    pass\ndef main():\n    pass\n"
        self._assert_no_copied_p8d_defs(clean_defs)

        # 证明无 (?m) 的旧写法会漏检行中部 import（反假绿元测试）
        mid_file = "# header\nimport requests\n"
        self.assertIsNone(
            re.search(r"^\s*import requests\b", mid_file),
            "对照组：无多行模式应漏检",
        )
        self.assertIsNotNone(
            re.search(r"(?m)^\s*import requests\b", mid_file),
            "多行模式必须检出中部 import",
        )

    def test_no_executable_or_remote_flags_in_parser(self) -> None:
        with self.assertRaises(shared.HelperError) as ctx:
            helper.parse_args(
                [
                    "docling_callback_helper.py",
                    "--input",
                    "a.pdf",
                    "--artifacts-path",
                    "models",
                    "--executable",
                    "evil",
                ]
            )
        self.assertEqual(ctx.exception.message, shared.MSG_ERR_USAGE)
        self.assertNotIn("evil", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
