# -*- coding: utf-8 -*-
"""
模块：V1-C 本机解析运行时预检 failure-first 专项测试
用途：在生产脚本尚未实现时先真实失败；实现后覆盖参数矩阵、dry-run 零副作用、合成 DOCX、假 runner 真值门与固定诊断码。
对接：tools/local-parser/runtime_preflight.py；docs/v1c-local-parser-runtime-preflight-contract.md；P8D/P8E 助手原语。
二次开发：禁止以跳过用例或预期失败标记、按真实 CLI 是否存在分支变绿；禁止复制生产逻辑；禁止读取真实标书/密钥/uploads。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from unittest import mock

# ---------------------------------------------------------------------------
# 固定加载：生产脚本必须位于同目录 runtime_preflight.py（不存在则真实失败）
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_PREFLIGHT_NAME = "runtime_preflight.py"
_PREFLIGHT_PATH = _HERE / _PREFLIGHT_NAME

# 契约固定锚点与 JSON 六键
SAMPLE_MARKER = "SYNTH_BID_SAMPLE_V1"
JSON_KEYS = frozenset(
    {"ok", "engine", "mode", "diagnosticCode", "message", "runtimeVerified"}
)
TEMP_PREFIX = "biaoshu-parser-preflight-"

# 诊断码 → 退出码（契约 §6）
DIAG_EXIT: dict[str, int] = {
    "static_ready": 0,
    "synthetic_passed": 0,
    "argument_invalid": 2,
    "cli_missing": 2,
    "artifacts_invalid": 2,
    "parser_failed": 2,
    "parser_timeout": 2,
    "output_invalid": 2,
    "sample_marker_missing": 2,
    "interrupted": 130,
    "internal_error": 1,
}

# 禁止出现在 stdout/stderr JSON 消息中的敏感形态（抽检）
_ABS_PATH_HINT = re.compile(r"[A-Za-z]:\\|/home/|/Users/|\\\\")
_EXCEPTION_CLASS_HINT = re.compile(
    r"\b(ValueError|RuntimeError|OSError|FileNotFoundError|KeyError|"
    r"TypeError|AttributeError|HelperError|PreflightError|TimeoutExpired|"
    r"KeyboardInterrupt|Exception)\b"
)


def _load_preflight_module() -> Any:
    """
    用途：从固定相对路径加载生产模块；文件缺失必须抛错，形成 failure-first 真证据。
    二次开发：不得把生产逻辑拷入本测试，也不得用假模块顶替。
    """
    if not _PREFLIGHT_PATH.is_file():
        raise FileNotFoundError(
            f"生产脚本缺失（failure-first 预期）：{_PREFLIGHT_NAME}"
        )
    spec = importlib.util.spec_from_file_location(
        "runtime_preflight_under_test", _PREFLIGHT_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载生产脚本：{_PREFLIGHT_NAME}")
    mod = importlib.util.module_from_spec(spec)
    # 注册到 sys.modules，便于 patch 目标稳定
    sys.modules["runtime_preflight_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# 模块导入期加载：生产脚本不存在时 discover/import 即真实失败
preflight = _load_preflight_module()

# 助手模块（仅用于注入哨兵与假 runner，不复制其逻辑）
import docling_callback_helper as docling_helper  # noqa: E402
import mineru_callback_helper as mineru_helper  # noqa: E402


def _sentinel_fail(name: str) -> Callable[..., Any]:
    """用途：触发即失败的副作用哨兵，证明 dry-run/成功路径未触达。"""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(f"禁止调用副作用：{name}")

    return _boom


def _parse_json_stdout(stdout: str) -> dict[str, Any]:
    """
    用途：解析 main 的唯一 JSON 对象输出。
    规则：对完整 strip 后的 stdout 做 json.loads；拒绝尾随安全日志或第二对象；
    允许同一 JSON 对象自身格式化为多行。
    """
    text = (stdout or "").strip()
    if not text:
        raise AssertionError("stdout 为空，未输出 JSON")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "stdout 不是合法唯一 JSON 对象（拒绝尾随日志/第二对象）"
        ) from exc
    if not isinstance(data, dict):
        raise AssertionError("JSON 根必须为对象")
    return data


def _assert_exact_json_keys(tc: unittest.TestCase, data: Mapping[str, Any]) -> None:
    tc.assertEqual(set(data.keys()), JSON_KEYS)


def _assert_hygiene(tc: unittest.TestCase, blob: str, *, forbidden_substrings: Sequence[str] = ()) -> None:
    """用途：输出不得含绝对路径、命令全文形态、异常类名、正文/锚点泄露。"""
    for item in forbidden_substrings:
        if item:
            tc.assertNotIn(item, blob)
    # 不强制整串无反斜杠（JSON 转义可能出现），但拒绝盘符绝对路径与 Unix home
    tc.assertIsNone(
        _ABS_PATH_HINT.search(blob),
        msg="输出疑似含绝对路径",
    )
    tc.assertIsNone(
        _EXCEPTION_CLASS_HINT.search(blob),
        msg="输出疑似含异常类名",
    )
    # 不得回显完整 argv 形态（粗检：-p/-o/--artifacts-path 成串）
    tc.assertNotIn(" -p ", blob)
    tc.assertNotIn(" -o ", blob)
    tc.assertNotIn("--artifacts-path ", blob)
    tc.assertNotIn("convert --from", blob)


def _run_main(
    argv: list[str],
    *,
    extra_patches: list[Any] | None = None,
) -> tuple[int, str, str]:
    """用途：捕获 stdout/stderr 调用 preflight.main。"""
    stdout = io.StringIO()
    stderr = io.StringIO()
    patches = list(extra_patches or [])
    # 默认装上票据/回调哨兵（证明预检零签票零回调）
    patches.extend(
        [
            mock.patch.object(
                mineru_helper, "post_callback", side_effect=_sentinel_fail("post_callback")
            ),
            mock.patch.object(
                mineru_helper,
                "read_ticket_from_getpass",
                side_effect=_sentinel_fail("read_ticket_from_getpass"),
            ),
            mock.patch.object(
                mineru_helper,
                "getpass",
                mock.Mock(getpass=_sentinel_fail("getpass")),
            ),
        ]
    )
    # 若生产模块绑定了同名引用，同步哨兵
    for attr in ("post_callback", "read_ticket_from_getpass"):
        if hasattr(preflight, attr):
            patches.append(
                mock.patch.object(
                    preflight, attr, side_effect=_sentinel_fail(attr)
                )
            )

    stack: list[Any] = []
    try:
        for p in patches:
            stack.append(p)
            p.start()
        with mock.patch.object(sys, "stdout", stdout):
            with mock.patch.object(sys, "stderr", stderr):
                code = preflight.main(argv)
    finally:
        for p in reversed(stack):
            p.stop()
    return int(code), stdout.getvalue(), stderr.getvalue()


def _write_fake_mineru_impl(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = f"# title\n{SAMPLE_MARKER}\n",
    sleep_seconds: float = 0,
    exit_code: int = 0,
    multi: bool = False,
) -> Path:
    """用途：假 MinerU 实现；按 -o 输出根写 Markdown 并记录调用。"""
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
Path({str(record_path)!r}).write_text(
    json.dumps({{"argv": argv, "env": env, "out": str(out) if out else None}}, ensure_ascii=False),
    encoding="utf-8",
)
if out is None:
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
mode = {mode!r}
if {sleep_seconds!r}:
    time.sleep(float({sleep_seconds!r}))
if mode == "fail":
    sys.exit(1)
if mode == "no_md":
    sys.exit(0)
if mode == "multi_md":
    (out / "a.md").write_text("# a\\n", encoding="utf-8")
    (out / "b.md").write_text("# b\\n", encoding="utf-8")
    sys.exit(0)
if mode == "empty_md":
    (out / "empty.md").write_text("   \\n", encoding="utf-8")
    sys.exit(0)
md = out / "result.md"
md.write_bytes({markdown_text!r}.encode("utf-8"))
if {multi!r}:
    (out / "extra.md").write_bytes(b"# extra\\n")
sys.exit({exit_code!r})
""",
        encoding="utf-8",
    )
    return impl


def _write_fake_mineru(bin_dir: Path, **kwargs: Any) -> Path:
    impl = _write_fake_mineru_impl(bin_dir, **kwargs)
    if os.name == "nt":
        wrapper = bin_dir / "mineru.exe"
        wrapper.write_bytes(b"MZ-fake-not-executed")
        return wrapper
    wrapper = bin_dir / "mineru"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "{impl}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def _write_fake_docling_impl(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = f"# title\n{SAMPLE_MARKER}\n",
    sleep_seconds: float = 0,
    exit_code: int = 0,
) -> Path:
    impl = bin_dir / "_fake_docling_impl.py"
    record_path = bin_dir / "_last_invocation.json"
    impl.write_text(
        f"""# -*- coding: utf-8 -*-
import json, os, sys, time
from pathlib import Path
argv = list(sys.argv)
env = dict(os.environ)
out = None
for i, a in enumerate(argv):
    if a == "--output" and i + 1 < len(argv):
        out = Path(argv[i + 1])
        break
Path({str(record_path)!r}).write_text(
    json.dumps({{"argv": argv, "env": env, "out": str(out) if out else None}}, ensure_ascii=False),
    encoding="utf-8",
)
if out is None:
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
mode = {mode!r}
if {sleep_seconds!r}:
    time.sleep(float({sleep_seconds!r}))
if mode == "fail":
    sys.exit(1)
if mode == "no_md":
    sys.exit(0)
if mode == "multi_md":
    (out / "a.md").write_text("# a\\n", encoding="utf-8")
    (out / "b.md").write_text("# b\\n", encoding="utf-8")
    sys.exit(0)
md = out / "result.md"
md.write_bytes({markdown_text!r}.encode("utf-8"))
sys.exit({exit_code!r})
""",
        encoding="utf-8",
    )
    return impl


def _write_fake_docling(bin_dir: Path, **kwargs: Any) -> Path:
    impl = _write_fake_docling_impl(bin_dir, **kwargs)
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


def _popen_delegate(impl: Path, exe_names: set[str]):
    real_popen = subprocess.Popen

    def _side_effect(cmd: Any, *args: Any, **kwargs: Any):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return real_popen(cmd, *args, **kwargs)
        first = str(cmd[0])
        name = Path(first).name.lower()
        if name in exe_names or any(first.lower().endswith(n) for n in exe_names):
            new_cmd = [sys.executable, str(impl), *list(cmd)[1:]]
            return real_popen(new_cmd, *args, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    return _side_effect


def _patch_popen_for_impl(impl: Path, exe_names: set[str]) -> list[Any]:
    """用途：在 preflight 与助手 subprocess 上统一委托假实现。"""
    side = _popen_delegate(impl, exe_names)
    targets = [mineru_helper.subprocess, docling_helper.subprocess]
    if hasattr(preflight, "subprocess"):
        targets.append(preflight.subprocess)
    return [mock.patch.object(t, "Popen", side_effect=side) for t in targets]


def _track_tempdirs(module: Any) -> tuple[Any, list[Path]]:
    """用途：跟踪 TemporaryDirectory 创建与清理。"""
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

    # 优先 patch 生产模块绑定的 tempfile
    if hasattr(preflight, "tempfile"):
        patcher = mock.patch.object(preflight.tempfile, "TemporaryDirectory", TrackingTD)
    else:
        patcher = mock.patch.object(tempfile, "TemporaryDirectory", TrackingTD)
    return patcher, created


def _path_under_any_root(path: Path, roots: Sequence[Path]) -> bool:
    """用途：判断路径是否位于任一跟踪 TEMP 根内（含根自身）。"""
    candidates = [path]
    try:
        candidates.append(path.resolve())
    except OSError:
        pass
    for root in roots:
        root_variants = [root]
        try:
            root_variants.append(root.resolve())
        except OSError:
            pass
        for cand in candidates:
            for r in root_variants:
                try:
                    cand.relative_to(r)
                    return True
                except ValueError:
                    pass
                cs, rs = str(cand), str(r)
                if cs == rs or cs.startswith(rs + os.sep) or cs.startswith(rs + "/"):
                    return True
    return False


def _extract_runner_io_paths(
    inv: Mapping[str, Any], *, engine: str
) -> tuple[Path, Path]:
    """
    用途：从假 runner invocation 提取输入/输出路径。
    MinerU：-p 输入、-o 输出；Docling：--output 输出、末尾位置参数为输入。
    """
    argv = list(inv.get("argv") or [])
    if engine == "mineru":
        inp: Path | None = None
        out: Path | None = None
        for i, a in enumerate(argv):
            if a == "-p" and i + 1 < len(argv):
                inp = Path(argv[i + 1])
            elif a == "-o" and i + 1 < len(argv):
                out = Path(argv[i + 1])
        if inp is None or out is None:
            raise AssertionError("MinerU 假 runner 调用缺少 -p 或 -o 路径")
        return inp, out
    if engine == "docling":
        out = None
        for i, a in enumerate(argv):
            if a == "--output" and i + 1 < len(argv):
                out = Path(argv[i + 1])
                break
        if out is None:
            raise AssertionError("Docling 假 runner 调用缺少 --output 路径")
        skip_next = False
        positional: list[str] = []
        for a in argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if a in ("--output", "--from", "--to", "--artifacts-path"):
                skip_next = True
                continue
            if isinstance(a, str) and a.startswith("-"):
                continue
            positional.append(str(a))
        if not positional:
            raise AssertionError("Docling 假 runner 调用缺少末尾输入路径")
        return Path(positional[-1]), out
    raise AssertionError(f"未知 engine: {engine}")


def _assert_preflight_temp_lifecycle(
    tc: unittest.TestCase,
    created: Sequence[Path],
    *,
    inv: Mapping[str, Any] | None = None,
    engine: str | None = None,
    require_io: bool = True,
) -> None:
    """
    用途：可复用 TEMP 真值门——created 非空、前缀正确、
    假 runner 输入/输出落在跟踪根内、最终根消失。
    """
    roots = list(created)
    tc.assertTrue(roots, msg="须至少创建一处被跟踪的 TemporaryDirectory")
    for p in roots:
        tc.assertTrue(
            p.name.startswith(TEMP_PREFIX),
            msg=f"TEMP 根名称须以 {TEMP_PREFIX} 开头，实际: {p.name}",
        )
    if require_io:
        if inv is None or engine is None:
            raise AssertionError("require_io 时必须提供 inv 与 engine")
        inp, out = _extract_runner_io_paths(inv, engine=engine)
        tc.assertTrue(
            _path_under_any_root(inp, roots),
            msg=f"输入路径必须位于跟踪 TEMP 根内: {inp}",
        )
        tc.assertTrue(
            _path_under_any_root(out, roots),
            msg=f"输出路径必须位于跟踪 TEMP 根内: {out}",
        )
    for p in roots:
        tc.assertFalse(p.exists(), msg=f"临时根未清理: {p}")


def _dry_run_zero_temp_patches() -> list[Any]:
    """用途：dry-run 成功路径对合成样本/TEMP 创建装触发即失败哨兵。"""
    patches: list[Any] = []
    if hasattr(preflight, "generate_synthetic_docx"):
        patches.append(
            mock.patch.object(
                preflight,
                "generate_synthetic_docx",
                side_effect=_sentinel_fail("generate_synthetic_docx"),
            )
        )
    tf_mod = preflight.tempfile if hasattr(preflight, "tempfile") else tempfile
    patches.append(
        mock.patch.object(
            tf_mod,
            "TemporaryDirectory",
            side_effect=_sentinel_fail("TemporaryDirectory"),
        )
    )
    if hasattr(tf_mod, "mkdtemp"):
        patches.append(
            mock.patch.object(
                tf_mod, "mkdtemp", side_effect=_sentinel_fail("mkdtemp")
            )
        )
    if hasattr(tf_mod, "mkstemp"):
        patches.append(
            mock.patch.object(
                tf_mod, "mkstemp", side_effect=_sentinel_fail("mkstemp")
            )
        )
    if hasattr(preflight, "TemporaryDirectory"):
        patches.append(
            mock.patch.object(
                preflight,
                "TemporaryDirectory",
                side_effect=_sentinel_fail("TemporaryDirectory"),
            )
        )
    return patches


def _load_invocation(bin_dir: Path) -> dict[str, Any]:
    path = bin_dir / "_last_invocation.json"
    if not path.is_file():
        raise AssertionError("假 runner 未留下调用记录")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestArgumentMatrix(unittest.TestCase):
    """用途：参数矩阵与静默错误（不回显用户原始值/绝对路径）。"""

    def test_missing_mode_is_argument_invalid(self) -> None:
        code, out, err = _run_main(["runtime_preflight.py", "--engine", "mineru"])
        data = _parse_json_stdout(out)
        _assert_exact_json_keys(self, data)
        self.assertEqual(code, DIAG_EXIT["argument_invalid"])
        self.assertFalse(data["ok"])
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        self.assertEqual(data["engine"], "mineru")
        self.assertFalse(data["runtimeVerified"])
        _assert_hygiene(self, out + err)

    def test_both_modes_mutex_is_argument_invalid(self) -> None:
        code, out, err = _run_main(
            [
                "runtime_preflight.py",
                "--engine",
                "mineru",
                "--dry-run",
                "--synthetic-check",
            ]
        )
        data = _parse_json_stdout(out)
        self.assertEqual(code, DIAG_EXIT["argument_invalid"])
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        self.assertFalse(data["ok"])
        _assert_hygiene(self, out + err)

    def test_illegal_engine_is_argument_invalid_and_silent(self) -> None:
        evil = "evil_engine_PATH_C:\\Secret\\Models"
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", evil, "--dry-run"]
        )
        data = _parse_json_stdout(out)
        self.assertEqual(code, DIAG_EXIT["argument_invalid"])
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        blob = out + err
        self.assertNotIn(evil, blob)
        self.assertNotIn("C:\\Secret", blob)
        _assert_hygiene(self, blob)

    def test_default_engine_is_mineru_for_dry_run(self) -> None:
        # 无 CLI：应 cli_missing，但 engine 默认 mineru
        empty = Path(tempfile.mkdtemp(prefix="preflight-empty-path-"))
        try:
            env_patch = mock.patch.dict(os.environ, {"PATH": str(empty)}, clear=False)
            with env_patch:
                code, out, err = _run_main(
                    ["runtime_preflight.py", "--dry-run"]
                )
            data = _parse_json_stdout(out)
            self.assertEqual(data["engine"], "mineru")
            self.assertEqual(data["mode"], "dry-run")
            self.assertEqual(data["diagnosticCode"], "cli_missing")
            self.assertEqual(code, DIAG_EXIT["cli_missing"])
            self.assertFalse(data["runtimeVerified"])
            _assert_hygiene(self, out + err, forbidden_substrings=[str(empty)])
        finally:
            try:
                empty.rmdir()
            except OSError:
                pass

    def test_docling_missing_artifacts_is_argument_invalid(self) -> None:
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "docling", "--dry-run"]
        )
        data = _parse_json_stdout(out)
        self.assertEqual(code, DIAG_EXIT["argument_invalid"])
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        self.assertEqual(data["engine"], "docling")
        _assert_hygiene(self, out + err)

    def test_mineru_rejects_extra_artifacts_path(self) -> None:
        leak = r"D:\models\must-not-echo"
        code, out, err = _run_main(
            [
                "runtime_preflight.py",
                "--engine",
                "mineru",
                "--artifacts-path",
                leak,
                "--dry-run",
            ]
        )
        data = _parse_json_stdout(out)
        self.assertEqual(code, DIAG_EXIT["argument_invalid"])
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        self.assertNotIn(leak, out + err)
        self.assertNotIn("D:\\models", out + err)
        _assert_hygiene(self, out + err)

    def test_engine_exact_enum_rejects_surrounding_whitespace_matrix(self) -> None:
        """
        用途：契约 § 参数要求 --engine 仅允许精确 mineru|docling（不得空白包围后 strip 放行）。
        矩阵：
          1) --engine \" mineru \"（首尾空格）
          2) --engine 制表符包围 docling（合法 TEMP artifacts，避免 artifacts 缺失混淆）
        两者均须：diagnosticCode=argument_invalid、退出码 2、ok=false、runtimeVerified=false；
        不回显原始空白值或路径；Popen/合成 DOCX/TEMP 创建/票据/getpass/回调触发即失败。
        使用假安全 CLI：使当前宽松实现（strip 后枚举命中）确实进入 static_ready，形成真实红测。
        """
        # 用例：(标签, 原始 engine 字面量, 是否 docling+artifacts)
        matrix: list[tuple[str, str, bool]] = [
            ("space_padded_mineru", " mineru ", False),
            ("tab_padded_docling", "\tdocling\t", True),
        ]
        for label, engine_raw, need_docling_artifacts in matrix:
            with self.subTest(case=label, engine_repr=repr(engine_raw)):
                with tempfile.TemporaryDirectory(
                    prefix="preflight-engine-exact-"
                ) as td:
                    root = Path(td)
                    bin_dir = root / "bin"
                    bin_dir.mkdir()
                    artifacts_path: Path | None = None
                    if need_docling_artifacts:
                        _write_fake_docling(bin_dir, mode="success")
                        artifacts_path = root / "artifacts"
                        artifacts_path.mkdir()
                        (artifacts_path / "placeholder.bin").write_bytes(b"x")
                    else:
                        _write_fake_mineru(bin_dir, mode="success")

                    old_path = os.environ.get("PATH", "")
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + old_path
                    try:
                        popen_calls: list[Any] = []

                        def boom_popen(*a: Any, **k: Any) -> Any:
                            popen_calls.append((a, k))
                            raise AssertionError("精确枚举拒绝路径禁止 Popen")

                        patches: list[Any] = [
                            mock.patch.object(
                                mineru_helper.subprocess,
                                "Popen",
                                side_effect=boom_popen,
                            ),
                            mock.patch.object(
                                docling_helper.subprocess,
                                "Popen",
                                side_effect=boom_popen,
                            ),
                        ]
                        if hasattr(preflight, "subprocess"):
                            patches.append(
                                mock.patch.object(
                                    preflight.subprocess,
                                    "Popen",
                                    side_effect=boom_popen,
                                )
                            )
                        # 零副作用：合成 DOCX / TemporaryDirectory / mkdtemp / mkstemp
                        patches.extend(_dry_run_zero_temp_patches())

                        argv = [
                            "runtime_preflight.py",
                            "--engine",
                            engine_raw,
                            "--dry-run",
                        ]
                        if artifacts_path is not None:
                            argv.extend(
                                ["--artifacts-path", str(artifacts_path)]
                            )

                        code, out, err = _run_main(
                            argv, extra_patches=patches
                        )
                        data = _parse_json_stdout(out)
                        _assert_exact_json_keys(self, data)
                        blob = out + err

                        # 契约精确枚举：空白包围必须 argument_invalid（非 static_ready）
                        self.assertEqual(
                            code,
                            DIAG_EXIT["argument_invalid"],
                            msg=(
                                f"{label}: 期望退出码 2/argument_invalid，"
                                f"实际 code={code} diag={data.get('diagnosticCode')!r} "
                                f"（若 static_ready 则生产仍在 strip 后放行）"
                            ),
                        )
                        self.assertIs(data["ok"], False)
                        self.assertEqual(
                            data["diagnosticCode"], "argument_invalid"
                        )
                        self.assertIs(data["runtimeVerified"], False)
                        # 不得进入 dry-run 成功语义
                        self.assertNotEqual(
                            data["diagnosticCode"], "static_ready"
                        )
                        self.assertNotEqual(code, 0)

                        # 不回显原始空白 engine 字面量；不回显 artifacts/PATH 根
                        self.assertNotIn(engine_raw, blob)
                        self.assertNotIn(" mineru ", blob)
                        self.assertNotIn("\tdocling\t", blob)
                        if artifacts_path is not None:
                            self.assertNotIn(str(artifacts_path), blob)
                        self.assertNotIn(str(bin_dir), blob)
                        self.assertNotIn(str(root), blob)
                        _assert_hygiene(
                            self,
                            blob,
                            forbidden_substrings=[
                                str(root),
                                str(bin_dir),
                                *(
                                    [str(artifacts_path)]
                                    if artifacts_path is not None
                                    else []
                                ),
                            ],
                        )
                        # 证明零副作用：假 CLI 存在也不得启动子进程/建 TEMP 样本
                        self.assertEqual(popen_calls, [])
                    finally:
                        os.environ["PATH"] = old_path


class TestDryRunSideEffects(unittest.TestCase):
    """用途：dry-run 零进程、零票据、零回调；成功时 runtimeVerified=false。"""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="preflight-dry-")
        self.root = Path(self._td.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self) -> None:
        os.environ["PATH"] = self._old_path
        self._td.cleanup()

    def test_mineru_dry_run_success_static_ready_no_side_effects(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="success")
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        popen_calls: list[Any] = []

        def boom_popen(*a: Any, **k: Any) -> Any:
            popen_calls.append((a, k))
            raise AssertionError("dry-run 禁止 Popen")

        patches = [
            mock.patch.object(mineru_helper.subprocess, "Popen", side_effect=boom_popen),
            mock.patch.object(docling_helper.subprocess, "Popen", side_effect=boom_popen),
        ]
        if hasattr(preflight, "subprocess"):
            patches.append(
                mock.patch.object(preflight.subprocess, "Popen", side_effect=boom_popen)
            )
        # B3：零 TEMP 样本——合成 DOCX / TemporaryDirectory / mkdtemp / mkstemp 触发即失败
        patches.extend(_dry_run_zero_temp_patches())
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--dry-run"],
            extra_patches=patches,
        )
        data = _parse_json_stdout(out)
        _assert_exact_json_keys(self, data)
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["engine"], "mineru")
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual(data["diagnosticCode"], "static_ready")
        self.assertIs(data["runtimeVerified"], False)
        self.assertIn("尚未运行", data["message"])
        self.assertEqual(popen_calls, [])
        _assert_hygiene(self, out + err)

    def test_docling_dry_run_success_with_artifacts(self) -> None:
        _write_fake_docling(self.bin_dir, mode="success")
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "placeholder.bin").write_bytes(b"x")
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        popen_calls: list[Any] = []

        def boom_popen(*a: Any, **k: Any) -> Any:
            popen_calls.append((a, k))
            raise AssertionError("dry-run 禁止 Popen")

        patches = [
            mock.patch.object(mineru_helper.subprocess, "Popen", side_effect=boom_popen),
            mock.patch.object(docling_helper.subprocess, "Popen", side_effect=boom_popen),
        ]
        if hasattr(preflight, "subprocess"):
            patches.append(
                mock.patch.object(preflight.subprocess, "Popen", side_effect=boom_popen)
            )
        # B3：零 TEMP 样本——合成 DOCX / TemporaryDirectory / mkdtemp / mkstemp 触发即失败
        patches.extend(_dry_run_zero_temp_patches())
        code, out, err = _run_main(
            [
                "runtime_preflight.py",
                "--engine",
                "docling",
                "--artifacts-path",
                str(artifacts),
                "--dry-run",
            ],
            extra_patches=patches,
        )
        data = _parse_json_stdout(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["diagnosticCode"], "static_ready")
        self.assertIs(data["runtimeVerified"], False)
        self.assertEqual(data["engine"], "docling")
        self.assertEqual(popen_calls, [])
        self.assertNotIn(str(artifacts), out + err)
        _assert_hygiene(self, out + err)


class TestSyntheticDocxGenerator(unittest.TestCase):
    """用途：标准库生成的合法 DOCX 与固定锚点 SYNTH_BID_SAMPLE_V1。"""

    def test_generate_synthetic_docx_is_openxml_zip_with_marker_once(self) -> None:
        self.assertTrue(
            hasattr(preflight, "generate_synthetic_docx"),
            msg="生产模块须导出 generate_synthetic_docx",
        )
        with tempfile.TemporaryDirectory(prefix="preflight-docx-") as td:
            root = Path(td)
            path = preflight.generate_synthetic_docx(root)
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix.lower(), ".docx")
            # 合法 ZIP / OpenXML 关键
            self.assertTrue(zipfile.is_zipfile(path))
            with zipfile.ZipFile(path, "r") as zf:
                names = set(zf.namelist())
                self.assertIn("[Content_Types].xml", names)
                self.assertIn("word/document.xml", names)
                # B6：最小 OpenXML 包关系与内容类型覆盖
                self.assertIn("_rels/.rels", names)
                rels = zf.read("_rels/.rels").decode("utf-8")
                self.assertIn("officeDocument", rels)
                self.assertRegex(
                    rels,
                    r'Target\s*=\s*["\']word/document\.xml["\']',
                )
                content_types = zf.read("[Content_Types].xml").decode("utf-8")
                self.assertRegex(
                    content_types,
                    r'PartName\s*=\s*["\']/?word/document\.xml["\']',
                )
                xml = zf.read("word/document.xml").decode("utf-8")
            self.assertEqual(xml.count(SAMPLE_MARKER), 1)
            # 生成物不得嵌入绝对路径或业务样本字样
            raw = path.read_bytes()
            self.assertNotIn(b"C:\\\\", raw)
            self.assertNotIn(b"/Users/", raw)
            self.assertNotIn(b"uploads", raw)
            self.assertNotIn(b"biaoshu.db", raw)


class TestSyntheticCheckFakeRunners(unittest.TestCase):
    """用途：MinerU/Docling 假 runner 的 synthetic-check、离线环境、锚点门与 TEMP 清理。"""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="preflight-syn-")
        self.root = Path(self._td.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        (self.artifacts / "m.bin").write_bytes(b"x")
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self) -> None:
        os.environ["PATH"] = self._old_path
        self._td.cleanup()

    def test_mineru_synthetic_check_success_offline_unique_md_cleanup(self) -> None:
        _write_fake_mineru(
            self.bin_dir,
            mode="success",
            markdown_text=f"# ok\nanchor {SAMPLE_MARKER} end\n",
        )
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        # 污染环境：必须被剥离
        polluted = {
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "socks5://127.0.0.1:9",
            "OPENAI_API_KEY": "sk-leak-must-not-pass",
            "X_LOCAL_PARSE_TICKET": "ticket_must_not_pass_0123456789ABCDXX",
            "MINERU_MODEL_SOURCE": "should-be-overwritten",
        }
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.append(patcher_td)
        with mock.patch.dict(os.environ, polluted, clear=False):
            code, out, err = _run_main(
                [
                    "runtime_preflight.py",
                    "--engine",
                    "mineru",
                    "--synthetic-check",
                ],
                extra_patches=patches,
            )
        data = _parse_json_stdout(out)
        _assert_exact_json_keys(self, data)
        self.assertEqual(code, 0, msg=out + err)
        self.assertTrue(data["ok"])
        self.assertEqual(data["diagnosticCode"], "synthetic_passed")
        self.assertEqual(data["mode"], "synthetic-check")
        self.assertIs(data["runtimeVerified"], True)
        self.assertEqual(data["engine"], "mineru")
        inv = _load_invocation(self.bin_dir)
        env = inv["env"]
        self.assertEqual(env.get("MINERU_MODEL_SOURCE"), "local")
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        for banned in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "OPENAI_API_KEY",
            "X_LOCAL_PARSE_TICKET",
        ):
            self.assertNotIn(banned, env)
        argv = inv["argv"]
        self.assertIn("-p", argv)
        self.assertIn("-o", argv)
        self.assertIn("-b", argv)
        self.assertIn("pipeline", argv)
        # B2：TEMP 非空、前缀、IO 落在根内、最终消失
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="mineru", require_io=True
        )
        # 输出无正文/锚点/路径
        blob = out + err
        self.assertNotIn(SAMPLE_MARKER, blob)
        self.assertNotIn("# ok", blob)
        _assert_hygiene(self, blob)

    def test_docling_synthetic_check_success_offline_cleanup(self) -> None:
        _write_fake_docling(
            self.bin_dir,
            mode="success",
            markdown_text=f"body\n{SAMPLE_MARKER}\n",
        )
        impl = self.bin_dir / "_fake_docling_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        polluted = {
            "HTTP_PROXY": "http://127.0.0.1:9",
            "OPENAI_API_KEY": "sk-docling-leak",
            "HF_HUB_OFFLINE": "0",
        }
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"docling.exe", "docling"})
        patches.append(patcher_td)
        with mock.patch.dict(os.environ, polluted, clear=False):
            code, out, err = _run_main(
                [
                    "runtime_preflight.py",
                    "--engine",
                    "docling",
                    "--artifacts-path",
                    str(self.artifacts),
                    "--synthetic-check",
                ],
                extra_patches=patches,
            )
        data = _parse_json_stdout(out)
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(data["diagnosticCode"], "synthetic_passed")
        self.assertIs(data["runtimeVerified"], True)
        inv = _load_invocation(self.bin_dir)
        env = inv["env"]
        self.assertEqual(env.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(env.get("TRANSFORMERS_OFFLINE"), "1")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        argv = inv["argv"]
        self.assertIn("convert", argv)
        self.assertIn("--from", argv)
        self.assertIn("docx", argv)
        self.assertIn("--to", argv)
        self.assertIn("md", argv)
        self.assertIn("--no-enable-remote-services", argv)
        self.assertIn("--no-allow-external-plugins", argv)
        # B2：TEMP 非空、前缀、IO 落在根内、最终消失
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="docling", require_io=True
        )
        self.assertNotIn(str(self.artifacts), out + err)
        self.assertNotIn(SAMPLE_MARKER, out + err)
        _assert_hygiene(self, out + err)


class TestDiagnosticMapping(unittest.TestCase):
    """用途：固定诊断码、退出码与 JSON 六键精确映射。"""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(prefix="preflight-diag-")
        self.root = Path(self._td.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self) -> None:
        os.environ["PATH"] = self._old_path
        self._td.cleanup()

    def _assert_diag(
        self,
        code: int,
        out: str,
        err: str,
        *,
        diagnostic: str,
        engine: str,
        mode: str,
        ok: bool = False,
    ) -> dict[str, Any]:
        data = _parse_json_stdout(out)
        _assert_exact_json_keys(self, data)
        self.assertEqual(code, DIAG_EXIT[diagnostic])
        self.assertEqual(data["diagnosticCode"], diagnostic)
        self.assertEqual(data["engine"], engine)
        self.assertEqual(data["mode"], mode)
        self.assertIs(data["ok"], ok)
        self.assertIs(data["runtimeVerified"], False)
        self.assertIsInstance(data["message"], str)
        self.assertTrue(data["message"].strip())
        _assert_hygiene(self, out + err)
        return data

    def test_cli_missing_mineru(self) -> None:
        empty = self.root / "empty-bin"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--dry-run"]
        )
        self._assert_diag(
            code, out, err, diagnostic="cli_missing", engine="mineru", mode="dry-run"
        )

    def test_cli_missing_docling(self) -> None:
        empty = self.root / "empty-bin2"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        code, out, err = _run_main(
            [
                "runtime_preflight.py",
                "--engine",
                "docling",
                "--artifacts-path",
                str(self.artifacts),
                "--dry-run",
            ]
        )
        self._assert_diag(
            code, out, err, diagnostic="cli_missing", engine="docling", mode="dry-run"
        )
        self.assertNotIn(str(self.artifacts), out + err)

    def test_artifacts_invalid(self) -> None:
        _write_fake_docling(self.bin_dir)
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        missing = self.root / "no-such-artifacts-dir"
        code, out, err = _run_main(
            [
                "runtime_preflight.py",
                "--engine",
                "docling",
                "--artifacts-path",
                str(missing),
                "--dry-run",
            ]
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="artifacts_invalid",
            engine="docling",
            mode="dry-run",
        )
        self.assertNotIn(str(missing), out + err)

    def test_parser_failed_mineru(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="fail")
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="parser_failed",
            engine="mineru",
            mode="synthetic-check",
        )
        inv = _load_invocation(self.bin_dir)
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="mineru", require_io=True
        )

    def test_parser_timeout_via_injected_short_timeout(self) -> None:
        """用途：假 runner 睡眠 + 可补丁超时秒数 → parser_timeout。"""
        _write_fake_mineru(self.bin_dir, mode="success", sleep_seconds=3)
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        # 生产须暴露可测超时（常量或助手超时）；优先补丁生产，再补丁助手
        timeout_patches: list[Any] = []
        if hasattr(preflight, "PARSER_TIMEOUT_SECONDS"):
            timeout_patches.append(
                mock.patch.object(preflight, "PARSER_TIMEOUT_SECONDS", 0.2)
            )
        elif hasattr(preflight, "MINERU_TIMEOUT_SECONDS"):
            timeout_patches.append(
                mock.patch.object(preflight, "MINERU_TIMEOUT_SECONDS", 0.2)
            )
        else:
            timeout_patches.append(
                mock.patch.object(mineru_helper, "MINERU_TIMEOUT_SECONDS", 0.2)
            )
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.extend(timeout_patches)
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="parser_timeout",
            engine="mineru",
            mode="synthetic-check",
        )
        # 超时路径可能未留下完整 runner 记录；仍须非空前缀与根消失
        if (self.bin_dir / "_last_invocation.json").is_file():
            inv = _load_invocation(self.bin_dir)
            _assert_preflight_temp_lifecycle(
                self, created, inv=inv, engine="mineru", require_io=True
            )
        else:
            _assert_preflight_temp_lifecycle(
                self, created, require_io=False
            )

    def test_output_invalid_zero_md(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="no_md")
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="output_invalid",
            engine="mineru",
            mode="synthetic-check",
        )
        inv = _load_invocation(self.bin_dir)
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="mineru", require_io=True
        )

    def test_output_invalid_multi_md(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="multi_md")
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="output_invalid",
            engine="mineru",
            mode="synthetic-check",
        )
        inv = _load_invocation(self.bin_dir)
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="mineru", require_io=True
        )

    def test_sample_marker_missing(self) -> None:
        _write_fake_mineru(
            self.bin_dir,
            mode="success",
            markdown_text="# no marker here\nonly text\n",
        )
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        patcher_td, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(impl, {"mineru.exe", "mineru"})
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="sample_marker_missing",
            engine="mineru",
            mode="synthetic-check",
        )
        self.assertNotIn("no marker", out + err)
        inv = _load_invocation(self.bin_dir)
        _assert_preflight_temp_lifecycle(
            self, created, inv=inv, engine="mineru", require_io=True
        )

    def test_interrupted(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="success")
        impl = self.bin_dir / "_fake_mineru_impl.py"
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path

        def raise_interrupt(*_a: Any, **_k: Any) -> Any:
            raise KeyboardInterrupt()

        # B5：补丁必须实际传入 _run_main，覆盖 helper 属性、preflight 绑定与 Popen
        patches: list[Any] = []
        if hasattr(mineru_helper, "run_mineru_process"):
            patches.append(
                mock.patch.object(
                    mineru_helper, "run_mineru_process", side_effect=raise_interrupt
                )
            )
        if hasattr(preflight, "run_mineru_process"):
            patches.append(
                mock.patch.object(
                    preflight, "run_mineru_process", side_effect=raise_interrupt
                )
            )
        # Popen 允许路径：直接子进程启动时同样注入中断（与假 runner 委托并存）
        popen_side = raise_interrupt
        targets = [mineru_helper.subprocess, docling_helper.subprocess]
        if hasattr(preflight, "subprocess"):
            targets.append(preflight.subprocess)
        for t in targets:
            patches.append(mock.patch.object(t, "Popen", side_effect=popen_side))
        # 保留假 runner 路径信息（若中断发生在 resolve 之后、Popen 之前则不会用到）
        _ = impl
        # B7：跟踪 TEMP 生命周期——补丁须实际传入 _run_main，并断言 created 非空、前缀与清理
        patcher_td, created = _track_tempdirs(preflight)
        patches.append(patcher_td)
        code, out, err = _run_main(
            ["runtime_preflight.py", "--engine", "mineru", "--synthetic-check"],
            extra_patches=patches,
        )
        self._assert_diag(
            code,
            out,
            err,
            diagnostic="interrupted",
            engine="mineru",
            mode="synthetic-check",
        )
        _assert_preflight_temp_lifecycle(self, created, require_io=False)

    def test_internal_error_unknown_exception(self) -> None:
        _write_fake_mineru(self.bin_dir, mode="success")
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + self._old_path
        secret = "SecretInternal_C:\\Leak\\Path_ValueError"

        def boom(*_a: Any, **_k: Any) -> str:
            raise RuntimeError(secret)

        with mock.patch.object(
            mineru_helper, "resolve_mineru_executable", side_effect=boom
        ):
            # 同步生产模块上的绑定（若有）
            extra = []
            if hasattr(preflight, "resolve_mineru_executable"):
                extra.append(
                    mock.patch.object(
                        preflight, "resolve_mineru_executable", side_effect=boom
                    )
                )
            code, out, err = _run_main(
                ["runtime_preflight.py", "--engine", "mineru", "--dry-run"],
                extra_patches=extra,
            )
        data = self._assert_diag(
            code,
            out,
            err,
            diagnostic="internal_error",
            engine="mineru",
            mode="dry-run",
        )
        self.assertNotIn(secret, out + err)
        self.assertNotIn("RuntimeError", out + err)
        self.assertNotIn("C:\\Leak", out + err)
        self.assertEqual(code, 1)


class TestAntiFakeGreen(unittest.TestCase):
    """用途：扫描本测试源码，禁止跳过/预期失败标记与真实 CLI 分支变绿。"""

    def test_source_bans_skip_expected_fail_and_real_cli_branches(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        # B1：禁词拆分构造，避免扫描自命中；仍对完整源码做真实禁词扫描
        banned_snippets = [
            "skip" + "Test",
            "unittest." + "skip",
            "@unittest." + "skip",
            "pytest." + "skip",
            "x" + "fail",
            "pytest.mark." + "skip",
            "shutil." + "which(",
            "if which" + "(",
        ]
        for snip in banned_snippets:
            self.assertNotIn(snip, src, msg=f"禁止假绿片段: {snip}")
        # 禁止按真实 mineru/docling 是否存在决定断言（字面量亦拆分）
        which_tok = "which"
        self.assertNotIn(which_tok + '("mineru")', src)
        self.assertNotIn(which_tok + '("docling")', src)
        self.assertNotIn(which_tok + "('mineru')", src)
        self.assertNotIn(which_tok + "('docling')", src)
        # 不得读取真实业务路径（可执行字面量形态）
        self.assertNotIn("open(" + '"biaoshu.db")', src)
        self.assertNotIn("Path(" + "'uploads')", src)
        self.assertNotIn("Path(" + '"uploads")', src)

    def test_production_script_path_is_fixed_sibling(self) -> None:
        self.assertEqual(_PREFLIGHT_PATH.name, _PREFLIGHT_NAME)
        self.assertEqual(_PREFLIGHT_PATH.parent, _HERE)


class TestParseJsonStdoutHelper(unittest.TestCase):
    """用途：本测试助手级 _parse_json_stdout 反例与多行合法对象。"""

    def test_rejects_trailing_log_or_second_object_allows_multiline_one(self) -> None:
        # B4：完整 strip 后 loads；尾随日志/第二对象必须拒绝
        one = json.dumps(
            {
                "ok": True,
                "engine": "mineru",
                "mode": "dry-run",
                "diagnosticCode": "static_ready",
                "message": "x",
                "runtimeVerified": False,
            },
            ensure_ascii=False,
        )
        with self.assertRaises(AssertionError):
            _parse_json_stdout(one + "\nINFO trailing safety log")
        with self.assertRaises(AssertionError):
            _parse_json_stdout(one + "\n" + one)
        # 同一对象多行格式化必须接受
        multi = "{\n  \"ok\": false,\n  \"engine\": \"docling\"\n}"
        data = _parse_json_stdout(multi)
        self.assertIs(data["ok"], False)
        self.assertEqual(data["engine"], "docling")


class TestJsonContractSurface(unittest.TestCase):
    """用途：成功/失败路径的六键类型与 mode/engine 枚举。"""

    def test_static_ready_message_not_claim_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preflight-surface-") as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_mineru(bin_dir)
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = str(bin_dir) + os.pathsep + old
            try:
                def boom_popen(*_a: Any, **_k: Any) -> Any:
                    raise AssertionError("dry-run 禁止 Popen")

                patches = [
                    mock.patch.object(
                        mineru_helper.subprocess, "Popen", side_effect=boom_popen
                    ),
                ]
                code, out, err = _run_main(
                    ["runtime_preflight.py", "--engine", "mineru", "--dry-run"],
                    extra_patches=patches,
                )
                data = _parse_json_stdout(out)
                self.assertEqual(code, 0)
                self.assertIsInstance(data["ok"], bool)
                self.assertIsInstance(data["runtimeVerified"], bool)
                self.assertIs(data["runtimeVerified"], False)
                self.assertNotIn("解析质量", data["message"])
                self.assertNotIn("OCR", data["message"])
                _assert_hygiene(self, out + err)
            finally:
                os.environ["PATH"] = old


if __name__ == "__main__":
    unittest.main()
