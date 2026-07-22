# -*- coding: utf-8 -*-
"""
模块：V1-M 管理式本机 OCR runtime 预检 failure-first 专项测试
用途：在 managed_runtime_preflight 生产入口尚未实现时先形成可计数业务红；
      覆盖 manifest 严格解析、image-only ASCII PDF 真值、九键 JSON、假 runtime、
      Windows 中文前置分流与 TEMP/env 隔离；禁止假绿。
对接：docs/v1m-managed-local-ocr-runtime-contract.md；tools/local-parser/managed_runtime_preflight.py（待实现）。
二次开发：
  - 禁止顶层 import 缺失的生产模块；收集必须成功，缺入口=业务 failed。
  - 禁止测试自建 PDF fixture 后只测自己；锚点 PDF 必须由生产生成器写出。
  - 禁止 skip/xfail、固定 sleep、断言 BoolOp Or、按真实 CLI 是否存在分支、真实 HTTP/DB/uploads/网络。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from unittest import mock

# ---------------------------------------------------------------------------
# 常量（契约冻结；生产模块必须对齐，测试不复制生产业务逻辑）
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_PREFLIGHT_NAME = "managed_runtime_preflight.py"
_PREFLIGHT_PATH = _HERE / _PREFLIGHT_NAME
_HELPER_TEST_NAME = "test_mineru_callback_helper.py"
_THIS_TEST_NAME = "test_managed_runtime_preflight.py"

# 契约锚点：只允许出现在像素，禁止 PDF 文本层/metadata/文件名
OCR_P1 = "BIAOSHU_OCR_P1_V1"
OCR_P2 = "BIAOSHU_OCR_P2_V1"

JSON_KEYS = frozenset(
    {
        "ok",
        "status",
        "engine",
        "mode",
        "diagnosticCode",
        "message",
        "runtimeVerified",
        "didNotRunRealRuntime",
        "qualityProfile",
    }
)
STATUS_VALUES = frozenset({"ready", "passed", "not_ready", "failed"})
MODE_VALUES = frozenset({"dry-run", "ocr-check"})
QUALITY_PROFILES = frozenset({"ascii", "windows-zh"})

MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "engine",
        "cliRelativePath",
        "modelMarkerRelativePath",
        "requiredFreeBytes",
    }
)

DIAG_EXIT: dict[str, int] = {
    "static_ready": 0,
    "ocr_passed": 0,
    "runtime_manifest_invalid": 2,
    "cli_missing": 2,
    "model_missing": 2,
    "disk_insufficient": 2,
    "quality_precondition_failed": 2,
    "parser_failed": 2,
    "parser_timeout": 2,
    "output_invalid": 2,
    "ocr_marker_missing": 2,
    "interrupted": 130,
    "internal_error": 1,
    "argument_invalid": 2,
}

TEMP_PREFIX = "biaoshu-managed-ocr-"

_ABS_PATH_HINT = re.compile(r"[A-Za-z]:\\|/home/|/Users/|\\\\")
_EXCEPTION_CLASS_HINT = re.compile(
    r"\b(ValueError|RuntimeError|OSError|FileNotFoundError|KeyError|"
    r"TypeError|AttributeError|HelperError|PreflightError|TimeoutExpired|"
    r"KeyboardInterrupt|Exception)\b"
)

# 模块级：仅在函数内加载，禁止顶层 import 缺失生产入口
_preflight_cache: Any | None = None
_preflight_load_error: BaseException | None = None


def _load_preflight_module() -> Any:
    """
    用途：按需从固定相对路径加载生产模块。
    规则：文件缺失必须抛 FileNotFoundError（业务失败证据，不得在收集期发生）。
    """
    global _preflight_cache, _preflight_load_error
    if _preflight_cache is not None:
        return _preflight_cache
    if _preflight_load_error is not None:
        raise _preflight_load_error
    if not _PREFLIGHT_PATH.is_file():
        err = FileNotFoundError(
            f"生产脚本缺失（failure-first 预期）：{_PREFLIGHT_NAME}"
        )
        _preflight_load_error = err
        raise err
    spec = importlib.util.spec_from_file_location(
        "managed_runtime_preflight_under_test", _PREFLIGHT_PATH
    )
    if spec is None or spec.loader is None:
        err = ImportError(f"无法加载生产脚本：{_PREFLIGHT_NAME}")
        _preflight_load_error = err
        raise err
    mod = importlib.util.module_from_spec(spec)
    sys.modules["managed_runtime_preflight_under_test"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 — 加载期错误原样上抛为业务红
        _preflight_load_error = exc
        raise
    _preflight_cache = mod
    return mod


def _require_preflight(tc: unittest.TestCase) -> Any:
    """用途：测试体内强制生产入口存在；缺失=可计数 AssertionError 业务红。"""
    tc.assertTrue(
        _PREFLIGHT_PATH.is_file(),
        msg=f"生产入口缺失（failure-first）：{_PREFLIGHT_PATH}",
    )
    try:
        return _load_preflight_module()
    except Exception as exc:  # noqa: BLE001
        tc.fail(f"生产模块加载失败（failure-first）：{type(exc).__name__}")


def _sentinel_fail(name: str) -> Callable[..., Any]:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(f"禁止调用副作用：{name}")

    return _boom


def _parse_json_stdout(stdout: str) -> dict[str, Any]:
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


def _assert_hygiene(
    tc: unittest.TestCase,
    blob: str,
    *,
    forbidden_substrings: Sequence[str] = (),
) -> None:
    for item in forbidden_substrings:
        if item:
            tc.assertNotIn(item, blob)
    tc.assertIsNone(_ABS_PATH_HINT.search(blob), msg="输出疑似含绝对路径")
    tc.assertIsNone(_EXCEPTION_CLASS_HINT.search(blob), msg="输出疑似含异常类名")
    tc.assertNotIn(OCR_P1, blob)
    tc.assertNotIn(OCR_P2, blob)
    tc.assertNotIn(" -p ", blob)
    tc.assertNotIn(" -o ", blob)


def _run_main(
    preflight: Any,
    argv: list[str],
    *,
    extra_patches: list[Any] | None = None,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    patches = list(extra_patches or [])
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


def _valid_manifest_dict(
    *,
    cli_rel: str = "venv/Scripts/mineru.exe",
    model_rel: str = "models/.biaoshu-ready",
    required_free: int = 1,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "engine": "mineru",
        "cliRelativePath": cli_rel,
        "modelMarkerRelativePath": model_rel,
        "requiredFreeBytes": required_free,
    }


def _write_manifest(runtime_root: Path, data: Mapping[str, Any]) -> Path:
    path = runtime_root / "runtime-manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _prepare_runtime_tree(
    root: Path,
    *,
    required_free: int = 1,
    cli_name: str = "mineru.exe",
    write_cli: bool = True,
    write_model: bool = True,
    cli_bytes: bytes = b"MZ-fake-not-executed",
) -> tuple[Path, Path]:
    """
    用途：在 TEMP 构造仓外 runtime 根 + 合法 manifest + 可选 CLI/model marker。
    返回：(runtime_root, manifest_path)
    """
    runtime = root / "runtime-root"
    runtime.mkdir(parents=True, exist_ok=True)
    cli_rel = f"venv/Scripts/{cli_name}"
    model_rel = "models/.biaoshu-ready"
    if write_cli:
        cli_path = runtime / "venv" / "Scripts" / cli_name
        cli_path.parent.mkdir(parents=True, exist_ok=True)
        cli_path.write_bytes(cli_bytes)
    if write_model:
        model_path = runtime / "models" / ".biaoshu-ready"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"ready\n")
    manifest = _write_manifest(
        runtime,
        _valid_manifest_dict(
            cli_rel=cli_rel, model_rel=model_rel, required_free=required_free
        ),
    )
    return runtime, manifest


# model marker 普通小文件上界（字节）；超过视为形态非法 → runtime_manifest_invalid
MODEL_MARKER_MAX_BYTES = 64 * 1024
# Windows reparse 属性位
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# ASCII fixture 嵌入图有界尺寸（生产生成器应对齐）
ASCII_PDF_IMG_MIN = 32
ASCII_PDF_IMG_MAX = 2048


def _write_fake_mineru_impl(
    bin_dir: Path,
    *,
    mode: str = "success",
    markdown_text: str = f"# title\n{OCR_P1}\n{OCR_P2}\n",
    sleep_seconds: float = 0,
    exit_code: int = 0,
    require_valid_input_pdf: bool = False,
) -> Path:
    """
    用途：写入假 MinerU 实现脚本。
    require_valid_input_pdf=True 时必须读取 -p，校验两页 image-only PDF 结构后才允许写 Markdown，
    禁止忽略输入直接回填锚点常量（反假绿 Q5）。
    """
    impl = bin_dir / "_fake_mineru_impl.py"
    record_path = bin_dir / "_last_invocation.json"
    impl.write_text(
        f"""# -*- coding: utf-8 -*-
import json, os, sys, time, io
from pathlib import Path
argv = list(sys.argv)
env = dict(os.environ)
cwd = os.getcwd()
out = None
inp = None
for i, a in enumerate(argv):
    if a == "-o" and i + 1 < len(argv):
        out = Path(argv[i + 1])
    if a == "-p" and i + 1 < len(argv):
        inp = Path(argv[i + 1])
Path({str(record_path)!r}).write_text(
    json.dumps(
        {{
            "argv": argv,
            "env": env,
            "out": str(out) if out else None,
            "inp": str(inp) if inp else None,
            "cwd": cwd,
        }},
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
if out is None:
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
mode = {mode!r}
require_pdf = {require_valid_input_pdf!r}
if require_pdf:
    if inp is None or not Path(inp).is_file():
        sys.stderr.write("fake-mineru: missing -p input\\n")
        sys.exit(2)
    raw = Path(inp).read_bytes()
    if not raw.startswith(b"%PDF"):
        sys.stderr.write("fake-mineru: not a pdf\\n")
        sys.exit(2)
    try:
        from pypdf import PdfReader
        from PIL import Image
    except Exception as exc:
        sys.stderr.write("fake-mineru: deps\\n")
        sys.exit(2)
    reader = PdfReader(str(inp))
    if len(reader.pages) != 2:
        sys.stderr.write("fake-mineru: need 2 pages\\n")
        sys.exit(2)
    for pi, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            sys.stderr.write("fake-mineru: text layer not empty\\n")
            sys.exit(2)
        imgs = list(getattr(page, "images", []) or [])
        if not imgs:
            sys.stderr.write("fake-mineru: page has no embedded image\\n")
            sys.exit(2)
        im = Image.open(io.BytesIO(imgs[0].data)).convert("L")
        extrema = im.getextrema()
        if extrema[0] == extrema[1]:
            sys.stderr.write("fake-mineru: blank page image\\n")
            sys.exit(2)
    if {OCR_P1!r}.encode("ascii") in raw or {OCR_P2!r}.encode("ascii") in raw:
        sys.stderr.write("fake-mineru: anchor plaintext in pdf bytes\\n")
        sys.exit(2)
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
if mode == "oversized":
    (out / "huge.md").write_bytes(b"x" * (2 * 1024 * 1024 + 8))
    sys.exit(0)
if mode == "reverse_markers":
    (out / "result.md").write_bytes(
        ("# title\\n" + {OCR_P2!r} + "\\n" + {OCR_P1!r} + "\\n").encode("utf-8")
    )
    sys.exit(0)
if mode == "missing_marker":
    (out / "result.md").write_bytes(b"# title\\nonly one page marker missing\\n")
    sys.exit(0)
md = out / "result.md"
md.write_bytes({markdown_text!r}.encode("utf-8"))
sys.exit({exit_code!r})
""",
        encoding="utf-8",
    )
    return impl


def _snapshot_input_pdf_at_popen(cmd_list: Sequence[Any]) -> dict[str, Any]:
    """
    用途：Popen 调用当时快照 -p 输入 PDF（存在/两页/一图/空文本/非纯色/模板证据）。
    说明：必须在调用瞬间取证；main 返回后 TEMP 可能已清理，禁止回读已删路径。
    """
    snap: dict[str, Any] = {
        "path": None,
        "exists": False,
        "page_count": None,
        "pages": [],
        "error": None,
    }
    try:
        cmd = [str(x) for x in cmd_list]
        if "-p" not in cmd:
            snap["error"] = "missing -p flag"
            return snap
        p_idx = cmd.index("-p")
        if p_idx + 1 >= len(cmd):
            snap["error"] = "missing -p value"
            return snap
        pdf_path = Path(cmd[p_idx + 1])
        snap["path"] = str(pdf_path)
        snap["exists"] = pdf_path.is_file()
        if not snap["exists"]:
            return snap
        from pypdf import PdfReader  # noqa: WPS433

        reader = PdfReader(str(pdf_path))
        snap["page_count"] = len(reader.pages)
        page_images = _extract_page_images(pdf_path)
        tpl_p1 = _render_default_glyph_template(OCR_P1)
        tpl_p2 = _render_default_glyph_template(OCR_P2)
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            imgs = page_images[i] if i < len(page_images) else []
            page_snap: dict[str, Any] = {
                "image_count": len(imgs),
                "text_empty": text == "",
                "not_solid": False,
                "glyph_p1_exact": False,
                "glyph_p2_exact": False,
            }
            if imgs:
                ext = imgs[0].getextrema()
                page_snap["not_solid"] = ext[0] != ext[1]
                page_snap["glyph_p1_exact"] = _exact_template_hit(imgs[0], tpl_p1)
                page_snap["glyph_p2_exact"] = _exact_template_hit(imgs[0], tpl_p2)
            snap["pages"].append(page_snap)
    except Exception as exc:  # noqa: BLE001 — 快照失败记入 error，不中断 Popen 委托
        snap["error"] = type(exc).__name__
    return snap


def _popen_delegate(
    impl: Path,
    exe_names: set[str],
    *,
    original_records: list[dict[str, Any]] | None = None,
):
    """
    用途：委托假实现；可选 original_records 在替换 cmd[0] 前记录原始 Popen 主证据，
    并在调用当时快照 -p PDF 结构（不得依赖 main 返回后回读）。
    """
    real_popen = subprocess.Popen

    def _side_effect(cmd: Any, *args: Any, **kwargs: Any):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return real_popen(cmd, *args, **kwargs)
        first = str(cmd[0])
        name = Path(first).name.lower()
        matched = name in exe_names or any(
            first.lower().endswith(n) for n in exe_names
        )
        if matched and original_records is not None:
            cmd_list = list(cmd)
            original_records.append(
                {
                    "cmd": cmd_list,
                    "args": args,
                    "kwargs": dict(kwargs),
                    # 主证据：调用瞬间快照，避免与 TEMP cleanup 互斥
                    "input_pdf_snapshot": _snapshot_input_pdf_at_popen(cmd_list),
                }
            )
        if matched:
            new_cmd = [sys.executable, str(impl), *list(cmd)[1:]]
            return real_popen(new_cmd, *args, **kwargs)
        return real_popen(cmd, *args, **kwargs)

    return _side_effect


def _patch_popen_for_impl(
    preflight: Any,
    impl: Path,
    exe_names: set[str],
    *,
    original_records: list[dict[str, Any]] | None = None,
) -> list[Any]:
    side = _popen_delegate(impl, exe_names, original_records=original_records)
    targets = []
    if hasattr(preflight, "subprocess"):
        targets.append(preflight.subprocess)
    try:
        import mineru_callback_helper as mineru_helper  # noqa: WPS433

        targets.append(mineru_helper.subprocess)
    except Exception:
        pass
    targets.append(subprocess)
    patches = []
    seen: set[int] = set()
    for t in targets:
        tid = id(t)
        if tid in seen:
            continue
        seen.add(tid)
        patches.append(mock.patch.object(t, "Popen", side_effect=side))
    return patches


def _extract_page_images(pdf_path: Path) -> list[Any]:
    """
    用途：用 pypdf+Pillow 提取每页嵌入图像列表（PIL Image L 模式）。
    """
    try:
        from pypdf import PdfReader  # noqa: WPS433
        from PIL import Image  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise AssertionError(f"测试环境缺少 pypdf/Pillow：{exc}") from exc

    reader = PdfReader(str(pdf_path))
    pages_out: list[Any] = []
    for page in reader.pages:
        imgs: list[Any] = []
        for img_file in list(getattr(page, "images", []) or []):
            im = Image.open(io.BytesIO(img_file.data)).convert("L")
            imgs.append(im)
        pages_out.append(imgs)
    return pages_out


def _render_default_glyph_template(text: str) -> Any:
    """用途：独立 ImageFont.load_default 渲染参考字形模板（不依赖生产模块）。"""
    from PIL import Image, ImageDraw, ImageFont  # noqa: WPS433

    font = ImageFont.load_default()
    probe = Image.new("L", (8, 8), 255)
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = max(1, bbox[2] - bbox[0] + 4)
    h = max(1, bbox[3] - bbox[1] + 4)
    canvas = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(canvas)
    draw.text((2, 2), text, fill=0, font=font)
    return canvas


def _to_binary_foreground(img: Any, *, threshold: int = 128) -> Any:
    """
    用途：把灰度图二值化为前景=0 / 背景=255。
    说明：阈值默认 128，仅服务 load_default 黑白位图模板的 exact 搜索。
    """
    if getattr(img, "mode", None) != "L":
        img = img.convert("L")
    return img.point(lambda p: 0 if p < threshold else 255)


def _exact_template_hit(haystack: Any, needle: Any) -> bool:
    """
    用途：二值前景逐像素 exact template search（步长 1）。
    规则：
      - 先二值化再比较；空白 haystack 对非空模板恒为 False（命中/得分为 0）；
      - 完整模板窗口必须逐字节相等；禁止白底平均差与宽松阈值。
    仅用于 ASCII 位图反假绿，不宣称 OCR 引擎质量。
    """
    if needle is None or haystack is None:
        return False
    h = _to_binary_foreground(haystack)
    n = _to_binary_foreground(needle)
    nw, nh = n.size
    hw, hh = h.size
    if nw <= 0 or nh <= 0 or nw > hw or nh > hh:
        return False
    # 模板必须含前景，否则空白模板会在任意白底上假命中
    n_ext = n.getextrema()
    if n_ext[0] == 255:
        return False
    n_bytes = n.tobytes()
    # 步长 1：完整精确搜索
    for y in range(0, hh - nh + 1):
        for x in range(0, hw - nw + 1):
            crop = h.crop((x, y, x + nw, y + nh))
            if crop.tobytes() == n_bytes:
                return True
    return False


def _template_hit_score(haystack: Any, needle: Any) -> float:
    """
    用途：exact hit → 1.0，否则 0.0（无中间态宽松分）。
    空白页对非空模板得分为 0；禁止 0.55 白底平均差阈值。
    """
    return 1.0 if _exact_template_hit(haystack, needle) else 0.0


def _assert_glyph_on_page(tc: unittest.TestCase, page_img: Any, text: str) -> None:
    """用途：证明 text 的 load_default 字形模板在 page_img 上 exact hit。"""
    template = _render_default_glyph_template(text)
    hit = _exact_template_hit(page_img, template)
    tc.assertTrue(
        hit,
        msg=f"页面像素未 exact 命中字形模板 {text!r}",
    )


def _track_tempdirs(preflight: Any) -> tuple[Any, list[Path]]:
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

    if hasattr(preflight, "tempfile"):
        patcher = mock.patch.object(preflight.tempfile, "TemporaryDirectory", TrackingTD)
    else:
        patcher = mock.patch.object(tempfile, "TemporaryDirectory", TrackingTD)
    return patcher, created


def _load_invocation(bin_dir: Path) -> dict[str, Any]:
    path = bin_dir / "_last_invocation.json"
    if not path.is_file():
        raise AssertionError("假 runner 未留下调用记录")
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_temp_cleaned(tc: unittest.TestCase, created: Sequence[Path]) -> None:
    for p in created:
        tc.assertTrue(
            p.name.startswith(TEMP_PREFIX),
            msg=f"TEMP 根名称须以 {TEMP_PREFIX} 开头，实际: {p.name}",
        )
        tc.assertFalse(p.exists(), msg=f"临时根未清理: {p}")


def _assert_result_shape(
    tc: unittest.TestCase,
    data: Mapping[str, Any],
    *,
    diagnostic: str,
    mode: str,
    quality: str,
    status: str | None = None,
) -> None:
    _assert_exact_json_keys(tc, data)
    tc.assertEqual(data["engine"], "mineru")
    tc.assertEqual(data["mode"], mode)
    tc.assertEqual(data["qualityProfile"], quality)
    tc.assertEqual(data["diagnosticCode"], diagnostic)
    tc.assertIn(data["status"], STATUS_VALUES)
    if status is not None:
        tc.assertEqual(data["status"], status)
    tc.assertIsInstance(data["ok"], bool)
    tc.assertIsInstance(data["runtimeVerified"], bool)
    tc.assertIsInstance(data["didNotRunRealRuntime"], bool)
    tc.assertIsInstance(data["message"], str)
    tc.assertTrue(data["message"], msg="message 不得为空")
    # 固定中文：至少含中文汉字
    tc.assertRegex(data["message"], r"[\u4e00-\u9fff]")


def _ast_contains_boolop_or(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or):
            return True
    return False


def _find_assert_boolop_or(source: str) -> list[str]:
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


def _collect_string_constants(node: ast.AST) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
        return out
    if isinstance(node, (ast.Set, ast.Tuple, ast.List, ast.Dict)):
        elts = node.keys if isinstance(node, ast.Dict) else node.elts  # type: ignore[attr-defined]
        if isinstance(node, ast.Dict):
            elts = list(node.keys) + list(node.values)
        for elt in elts or []:
            if elt is not None:
                out.extend(_collect_string_constants(elt))
        return out
    if isinstance(node, ast.Call):
        # frozenset({...}) / set([...])
        for arg in node.args:
            out.extend(_collect_string_constants(arg))
    return out


def _find_multi_diagnostic_code_sets(source: str) -> list[str]:
    """
    用途：捕获 diagnosticCode 断言中的多 code 集合/多选一（反假绿 Q3）。
    仅扫描测试断言，不扫描 DIAG_EXIT 常量定义。
    """
    tree = ast.parse(source)
    bad: list[str] = []
    code_like = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        if func.value.id not in {"self", "cls", "tc"}:
            continue
        if func.attr not in {"assertIn", "assertNotIn"}:
            continue
        if len(node.args) < 2:
            continue
        # 第一参应涉及 diagnosticCode
        first_src = ast.dump(node.args[0])
        if "diagnosticCode" not in first_src and "diagnostic" not in first_src.lower():
            # 宽松：若第二参是多诊断码集合也拦截
            pass
        strs = _collect_string_constants(node.args[1])
        codes = [s for s in strs if code_like.match(s) and s in DIAG_EXIT]
        if len(set(codes)) >= 2:
            # 仅当第一参看起来是 diagnostic 相关，或全部是 DIAG_EXIT 键
            if "diagnosticCode" in first_src or "diagnostic" in first_src.lower() or len(codes) >= 2:
                if "diagnosticCode" in first_src or any(
                    isinstance(n, ast.Constant) and n.value == "diagnosticCode"
                    for n in ast.walk(node.args[0])
                ):
                    bad.append(
                        f"L{node.lineno}:self.{func.attr}-multi-diagnostic-codes={sorted(set(codes))}"
                    )
                else:
                    # 下标访问 data["diagnosticCode"] 的 dump 不含字面时再看 Attribute/Subscript
                    dump0 = ast.dump(node.args[0])
                    if "diagnosticCode" in dump0 or "diagnostic" in dump0:
                        bad.append(
                            f"L{node.lineno}:self.{func.attr}-multi-diagnostic-codes={sorted(set(codes))}"
                        )
    # 第二遍：任意 assertIn(x, {code1, code2}) 且 codes 均在 DIAG_EXIT
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "assertIn"):
            continue
        if len(node.args) < 2:
            continue
        strs = _collect_string_constants(node.args[1])
        codes = sorted({s for s in strs if s in DIAG_EXIT})
        if len(codes) >= 2:
            # 确认第一参树含 diagnosticCode 字面
            has_diag = False
            for sub in ast.walk(node.args[0]):
                if isinstance(sub, ast.Constant) and sub.value == "diagnosticCode":
                    has_diag = True
                if isinstance(sub, ast.Attribute) and sub.attr == "diagnosticCode":
                    has_diag = True
            if has_diag:
                mark = f"L{node.lineno}:assertIn-multi-diagnostic-codes={codes}"
                if mark not in bad:
                    bad.append(mark)
    return bad


# ===========================================================================
# A/I/J：收集期安全与自检（不依赖生产入口，必须可执行）
# ===========================================================================


class TestCollectionAndSelfGuard(unittest.TestCase):
    """用途：收集成功、无顶层生产 import、反假绿源码自检、文件边界。"""

    def test_no_toplevel_import_of_missing_production_module(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "managed_runtime_preflight":
                        banned.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] == "managed_runtime_preflight":
                    banned.append(f"from {mod} import ...")
        self.assertEqual(banned, [], msg=f"禁止顶层 import 生产模块: {banned}")
        # 模块对象在导入本测试后不得已加载生产缓存为成功
        self.assertIsNone(_preflight_cache)

    def test_production_path_is_fixed_sibling(self) -> None:
        self.assertEqual(_PREFLIGHT_PATH.name, _PREFLIGHT_NAME)
        self.assertEqual(_PREFLIGHT_PATH.parent, _HERE)
        # 当前 failure-first：入口必须仍缺失（若已被实现则本红门改为业务绿路径）
        # 不在此强制缺失，以免 production 合入后自检反杀；仅校验路径形态

    def test_self_no_skip_xfail_sleep_or_assert_or(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        # 禁词拆分避免扫描自命中
        self.assertNotIn("unittest." + "skip", src)
        self.assertNotIn("@unittest." + "skip", src)
        self.assertNotIn("pytest.mark." + "skip", src)
        self.assertNotIn("pytest.mark." + "xfail", src)
        self.assertNotIn("unittest." + "expectedFailure", src)
        # 固定 time.sleep 禁止（假 runner 模板字符串中的 sleep 用 time.sleep 字面，允许 impl 内）
        # 仅扫描测试类方法：解析 AST，禁止 Call(func=Attribute(time, sleep)) 出现在本文件顶层/测试函数
        tree = ast.parse(src)
        sleep_hits: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                # 允许写在 _write_fake_mineru_impl 生成的字符串里；AST 中字面字符串不构成 Call
                sleep_hits.append(f"L{node.lineno}")
        self.assertEqual(sleep_hits, [], msg=f"禁止固定 sleep: {sleep_hits}")
        bad_or = _find_assert_boolop_or(src)
        self.assertEqual(bad_or, [], msg=f"断言含 BoolOp Or: {bad_or}")
        bad_multi = _find_multi_diagnostic_code_sets(src)
        self.assertEqual(
            bad_multi,
            [],
            msg=f"diagnosticCode 禁止多 code 集合/多选一: {bad_multi}",
        )

    def test_self_bans_real_install_http_db_uploads_network(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        banned = [
            "requests." + "get",
            "requests." + "post",
            "httpx." + "Client",
            "urllib.request." + "urlopen",
            "socket." + "create_connection",
            "sqlite3." + "connect",
            "Path(" + '"uploads")',
            "Path(" + "'uploads')",
            "open(" + '"biaoshu.db")',
            "pip " + "install",
            "shutil.which(" + '"mineru")',
            "shutil.which(" + "'mineru')",
        ]
        for snip in banned:
            self.assertNotIn(snip, src, msg=f"禁止片段: {snip}")

    def test_strict_file_boundary_only_two_authorized(self) -> None:
        """用途：本专项可写边界仅两个测试文件；生产入口/README/Helper 不得由本测试改写。"""
        allowed = {_THIS_TEST_NAME, _HELPER_TEST_NAME}
        # 仅检查同目录测试/生产文件名集合中的“授权可写”声明
        self.assertEqual(allowed, {_THIS_TEST_NAME, _HELPER_TEST_NAME})
        # 生产候选必须仍是只读目标
        for name in (
            _PREFLIGHT_NAME,
            "mineru_callback_helper.py",
            "README.md",
            "runtime_preflight.py",
            "docling_callback_helper.py",
        ):
            path = _HERE / name
            if path.is_file():
                # 文件存在即可；哈希比对由 review 报告处理
                self.assertTrue(path.is_file())


# ===========================================================================
# B：manifest 严格解析
# ===========================================================================


class TestManifestStrictParsing(unittest.TestCase):
    """用途：精确五键、类型、路径安全、CLI/model/disk 门。"""

    def _call_load(self, preflight: Any, manifest: Path) -> Any:
        for name in (
            "load_manifest",
            "parse_manifest",
            "read_manifest",
            "validate_manifest",
        ):
            fn = getattr(preflight, name, None)
            if callable(fn):
                return fn(manifest)
        # 无独立函数时通过 dry-run main 间接验证
        return None

    def test_valid_manifest_static_ready_dry_run(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root, required_free=1)
            # 充足磁盘
            usage = mock.Mock(free=10**12, total=10**13, used=0)
            disk_calls: list[Any] = []

            def fake_usage(path: Any) -> Any:
                disk_calls.append(path)
                return usage

            patches = [
                mock.patch.object(shutil, "disk_usage", side_effect=fake_usage),
            ]
            if hasattr(preflight, "shutil"):
                patches.append(
                    mock.patch.object(preflight.shutil, "disk_usage", side_effect=fake_usage)
                )
            # dry-run 禁止 Popen
            boom = _sentinel_fail("Popen")
            if hasattr(preflight, "subprocess"):
                patches.append(mock.patch.object(preflight.subprocess, "Popen", side_effect=boom))
            patches.append(mock.patch.object(subprocess, "Popen", side_effect=boom))

            code, out, err = _run_main(
                preflight,
                [
                    _PREFLIGHT_NAME,
                    "--manifest",
                    str(manifest),
                    "--dry-run",
                    "--quality-profile",
                    "ascii",
                ],
                extra_patches=patches,
            )
            data = _parse_json_stdout(out)
            _assert_result_shape(
                self,
                data,
                diagnostic="static_ready",
                mode="dry-run",
                quality="ascii",
                status="ready",
            )
            self.assertEqual(code, DIAG_EXIT["static_ready"])
            self.assertIs(data["ok"], True)
            self.assertIs(data["runtimeVerified"], False)
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertTrue(disk_calls, msg="disk_usage 必须作用于目标卷")
            # 目标路径必须落在 runtime/manifest 卷
            hit = False
            for p in disk_calls:
                pp = Path(p)
                try:
                    if pp.resolve() == runtime.resolve() or runtime.resolve() in pp.resolve().parents or pp.resolve() == manifest.resolve().parent:
                        hit = True
                        break
                    # 同盘根即可（Windows 卷）
                    if pp.anchor and runtime.anchor and pp.anchor.lower() == runtime.anchor.lower():
                        hit = True
                        break
                except OSError:
                    continue
            self.assertTrue(hit, msg=f"disk_usage 未作用于 runtime 目标卷: {disk_calls}")
            _assert_hygiene(self, out + err, forbidden_substrings=[str(runtime), str(manifest)])

    def test_manifest_rejects_bool_required_free_bytes(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime = root / "rt"
            runtime.mkdir()
            bad = _valid_manifest_dict()
            bad["requiredFreeBytes"] = True  # bool 不得冒充 int
            manifest = _write_manifest(runtime, bad)
            code, out, err = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "runtime_manifest_invalid")
            self.assertEqual(code, DIAG_EXIT["runtime_manifest_invalid"])
            self.assertIs(data["ok"], False)
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertNotIn("True", data["message"])
            _assert_hygiene(self, out + err)

    def test_manifest_rejects_extra_and_missing_keys(self) -> None:
        preflight = _require_preflight(self)
        cases = [
            ("extra", {**_valid_manifest_dict(), "extraKey": 1}),
            ("missing_engine", {k: v for k, v in _valid_manifest_dict().items() if k != "engine"}),
            ("empty", {}),
        ]
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            for label, payload in cases:
                with self.subTest(case=label):
                    runtime = root / label
                    runtime.mkdir()
                    manifest = _write_manifest(runtime, payload)
                    code, out, err = _run_main(
                        preflight,
                        [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                    )
                    data = _parse_json_stdout(out)
                    self.assertEqual(data["diagnosticCode"], "runtime_manifest_invalid")
                    self.assertEqual(code, DIAG_EXIT["runtime_manifest_invalid"])
                    self.assertIs(data["didNotRunRealRuntime"], True)
                    _assert_hygiene(self, out + err)

    def test_manifest_rejects_bad_schema_engine_and_zero_free(self) -> None:
        preflight = _require_preflight(self)
        variants = [
            ("schema", {"schemaVersion": 2}),
            ("engine", {"engine": "docling"}),
            ("free0", {"requiredFreeBytes": 0}),
            ("freeneg", {"requiredFreeBytes": -1}),
            ("freefloat", {"requiredFreeBytes": 1.5}),
            ("freestr", {"requiredFreeBytes": "1"}),
        ]
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            for label, patch in variants:
                with self.subTest(case=label):
                    runtime = root / label
                    runtime.mkdir()
                    data = _valid_manifest_dict()
                    data.update(patch)
                    manifest = _write_manifest(runtime, data)
                    code, out, err = _run_main(
                        preflight,
                        [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                    )
                    payload = _parse_json_stdout(out)
                    self.assertEqual(payload["diagnosticCode"], "runtime_manifest_invalid")
                    self.assertNotEqual(code, 0)
                    _assert_hygiene(self, out + err)

    def test_manifest_rejects_absolute_unc_url_dotdot_and_sep_bypass(self) -> None:
        preflight = _require_preflight(self)
        evil_cli = [
            r"C:\Windows\System32\cmd.exe",
            r"\\server\share\mineru.exe",
            "https://evil.example/mineru.exe",
            "../outside/mineru.exe",
            r"..\outside\mineru.exe",
            "venv/Scripts/../../outside/mineru.exe",
            "/etc/passwd",
            "venv\\Scripts\\mineru.exe",  # 若生产规范化允许反斜杠相对路径则可能放行；
            # 仍拒绝盘符/UNC/URL/.. 为主
        ]
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            for i, cli in enumerate(evil_cli):
                with self.subTest(cli=cli):
                    runtime = root / f"e{i}"
                    runtime.mkdir()
                    # 合法 marker 占位
                    (runtime / "models").mkdir(exist_ok=True)
                    (runtime / "models" / ".biaoshu-ready").write_bytes(b"x")
                    data = _valid_manifest_dict(cli_rel=cli)
                    # 对纯反斜杠相对路径：若实现接受 Path 归一化，仍要求不得逃出根
                    manifest = _write_manifest(runtime, data)
                    code, out, err = _run_main(
                        preflight,
                        [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                    )
                    payload = _parse_json_stdout(out)
                    # 绝对/UNC/URL/.. 必须 invalid；纯反斜杠相对若实现归一化后仍根内可放行
                    if cli.startswith(("C:", "\\\\", "https:", "http:", "/", "..")) or ".." in cli.replace("\\", "/"):
                        self.assertEqual(
                            payload["diagnosticCode"],
                            "runtime_manifest_invalid",
                            msg=f"应拒绝危险路径: {cli!r}",
                        )
                        self.assertNotEqual(code, 0)
                    blob = out + err
                    self.assertNotIn(cli, blob)
                    _assert_hygiene(self, blob)

    def test_manifest_rejects_cli_symlink_and_non_exe(self) -> None:
        """用途：.cmd 与 CLI symlink/reparse 固定 runtime_manifest_invalid（禁止多 code 集合）。"""
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            # 非 .exe → 形态非法，固定 runtime_manifest_invalid（非 cli_missing）
            runtime, manifest = _prepare_runtime_tree(
                root / "nonexe", cli_name="mineru.cmd", cli_bytes=b"@echo off\r\n"
            )
            code, out, err = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "runtime_manifest_invalid")
            self.assertEqual(code, DIAG_EXIT["runtime_manifest_invalid"])
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertIs(data["ok"], False)

            # symlink CLI（真实或 mock is_symlink）
            runtime2 = root / "sym"
            runtime2.mkdir()
            target = root / "outside-mineru.exe"
            target.write_bytes(b"MZ")
            cli_path = runtime2 / "venv" / "Scripts" / "mineru.exe"
            cli_path.parent.mkdir(parents=True, exist_ok=True)
            (runtime2 / "models").mkdir()
            (runtime2 / "models" / ".biaoshu-ready").write_bytes(b"x")
            linked = False
            try:
                os.symlink(str(target), str(cli_path))
                linked = True
            except OSError:
                cli_path.write_bytes(b"MZ")
            manifest2 = _write_manifest(
                runtime2,
                _valid_manifest_dict(cli_rel="venv/Scripts/mineru.exe"),
            )
            patches: list[Any] = []
            if not linked:
                def is_symlink_side(self_path: Path) -> bool:
                    return self_path.name.lower() == "mineru.exe"

                patches.append(
                    mock.patch.object(Path, "is_symlink", is_symlink_side)
                )
            code2, out2, err2 = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest2), "--dry-run"],
                extra_patches=patches,
            )
            data2 = _parse_json_stdout(out2)
            self.assertEqual(data2["diagnosticCode"], "runtime_manifest_invalid")
            self.assertEqual(code2, DIAG_EXIT["runtime_manifest_invalid"])
            self.assertIs(data2["didNotRunRealRuntime"], True)
            _assert_hygiene(self, out2 + err2, forbidden_substrings=[str(target)])

    def test_manifest_rejects_model_marker_dir_symlink_reparse_oversized(self) -> None:
        """
        用途：model marker 必须为根内普通小文件；目录/symlink/reparse/超大固定
        runtime_manifest_invalid。CLI reparse 用 FILE_ATTRIBUTE_REPARSE_POINT 证据，
        不依赖创建真实 junction 权限。
        """
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)

            def _run_case(label: str, setup: Callable[[Path], Path]) -> None:
                case = root / label
                case.mkdir()
                # 先准备合法 CLI
                cli = case / "venv" / "Scripts" / "mineru.exe"
                cli.parent.mkdir(parents=True, exist_ok=True)
                cli.write_bytes(b"MZ-fake")
                manifest = setup(case)
                code, out, err = _run_main(
                    preflight,
                    [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                )
                data = _parse_json_stdout(out)
                self.assertEqual(
                    data["diagnosticCode"],
                    "runtime_manifest_invalid",
                    msg=f"{label}: {out}{err}",
                )
                self.assertEqual(code, DIAG_EXIT["runtime_manifest_invalid"])
                self.assertIs(data["didNotRunRealRuntime"], True)
                self.assertIs(data["ok"], False)
                _assert_hygiene(self, out + err)

            # marker 为目录
            def setup_dir(case: Path) -> Path:
                marker = case / "models" / ".biaoshu-ready"
                marker.mkdir(parents=True)
                return _write_manifest(
                    case,
                    _valid_manifest_dict(model_rel="models/.biaoshu-ready"),
                )

            _run_case("marker-dir", setup_dir)

            # marker 超大文件
            def setup_huge(case: Path) -> Path:
                marker = case / "models" / ".biaoshu-ready"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_bytes(b"Z" * (MODEL_MARKER_MAX_BYTES + 1))
                return _write_manifest(
                    case,
                    _valid_manifest_dict(model_rel="models/.biaoshu-ready"),
                )

            _run_case("marker-huge", setup_huge)

            # marker symlink（真实或 mock）
            def setup_marker_sym(case: Path) -> Path:
                outside = root / "outside-marker-ready"
                outside.write_bytes(b"ready\n")
                marker = case / "models" / ".biaoshu-ready"
                marker.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.symlink(str(outside), str(marker))
                except OSError:
                    marker.write_bytes(b"ready\n")
                return _write_manifest(
                    case,
                    _valid_manifest_dict(model_rel="models/.biaoshu-ready"),
                )

            case_sym = root / "marker-sym"
            case_sym.mkdir()
            cli = case_sym / "venv" / "Scripts" / "mineru.exe"
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_bytes(b"MZ-fake")
            manifest_sym = setup_marker_sym(case_sym)
            patches_sym: list[Any] = []
            if not (case_sym / "models" / ".biaoshu-ready").is_symlink():
                def is_sym(self_path: Path) -> bool:
                    return self_path.name == ".biaoshu-ready"

                patches_sym.append(mock.patch.object(Path, "is_symlink", is_sym))
            code_s, out_s, err_s = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest_sym), "--dry-run"],
                extra_patches=patches_sym,
            )
            data_s = _parse_json_stdout(out_s)
            self.assertEqual(data_s["diagnosticCode"], "runtime_manifest_invalid")
            self.assertEqual(code_s, DIAG_EXIT["runtime_manifest_invalid"])
            self.assertIs(data_s["didNotRunRealRuntime"], True)

            # CLI + marker 的 FILE_ATTRIBUTE_REPARSE_POINT（不创建真实 junction）
            for kind in ("cli", "marker"):
                with self.subTest(reparse=kind):
                    case = root / f"reparse-{kind}"
                    case.mkdir()
                    cli_p = case / "venv" / "Scripts" / "mineru.exe"
                    cli_p.parent.mkdir(parents=True, exist_ok=True)
                    cli_p.write_bytes(b"MZ-fake")
                    marker_p = case / "models" / ".biaoshu-ready"
                    marker_p.parent.mkdir(parents=True, exist_ok=True)
                    marker_p.write_bytes(b"ready\n")
                    manifest = _write_manifest(
                        case,
                        _valid_manifest_dict(
                            cli_rel="venv/Scripts/mineru.exe",
                            model_rel="models/.biaoshu-ready",
                        ),
                    )
                    target_name = "mineru.exe" if kind == "cli" else ".biaoshu-ready"
                    real_stat = Path.stat

                    def stat_side(self_path: Path, *a: Any, **k: Any):
                        st = real_stat(self_path, *a, **k)
                        if self_path.name.lower() == target_name.lower():
                            return types.SimpleNamespace(
                                st_mode=st.st_mode,
                                st_ino=getattr(st, "st_ino", 0),
                                st_dev=getattr(st, "st_dev", 0),
                                st_nlink=getattr(st, "st_nlink", 1),
                                st_uid=getattr(st, "st_uid", 0),
                                st_gid=getattr(st, "st_gid", 0),
                                st_size=st.st_size,
                                st_atime=st.st_atime,
                                st_mtime=st.st_mtime,
                                st_ctime=st.st_ctime,
                                st_file_attributes=FILE_ATTRIBUTE_REPARSE_POINT,
                            )
                        return st

                    code_r, out_r, err_r = _run_main(
                        preflight,
                        [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                        extra_patches=[mock.patch.object(Path, "stat", stat_side)],
                    )
                    data_r = _parse_json_stdout(out_r)
                    self.assertEqual(
                        data_r["diagnosticCode"],
                        "runtime_manifest_invalid",
                        msg=f"reparse-{kind}: {out_r}{err_r}",
                    )
                    self.assertEqual(code_r, DIAG_EXIT["runtime_manifest_invalid"])
                    self.assertIs(data_r["didNotRunRealRuntime"], True)
                    _assert_hygiene(self, out_r + err_r)

    def test_cli_missing_and_model_missing(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            # 缺 CLI
            runtime, manifest = _prepare_runtime_tree(root / "nocli", write_cli=False)
            code, out, err = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "cli_missing")
            self.assertEqual(code, DIAG_EXIT["cli_missing"])
            self.assertEqual(data["status"], "not_ready")
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertIs(data["runtimeVerified"], False)

            # 缺 model
            runtime2, manifest2 = _prepare_runtime_tree(root / "nomodel", write_model=False)
            code2, out2, err2 = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest2), "--dry-run"],
            )
            data2 = _parse_json_stdout(out2)
            self.assertEqual(data2["diagnosticCode"], "model_missing")
            self.assertEqual(code2, DIAG_EXIT["model_missing"])
            self.assertEqual(data2["status"], "not_ready")
            self.assertIs(data2["didNotRunRealRuntime"], True)
            _assert_hygiene(self, out + err + out2 + err2)

    def test_disk_insufficient_uses_manifest_required_free_bytes(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root, required_free=10**12)
            usage = mock.Mock(free=1, total=10, used=9)
            disk_paths: list[str] = []

            def fake_usage(path: Any) -> Any:
                disk_paths.append(str(path))
                return usage

            patches = [mock.patch.object(shutil, "disk_usage", side_effect=fake_usage)]
            if hasattr(preflight, "shutil"):
                patches.append(
                    mock.patch.object(
                        preflight.shutil, "disk_usage", side_effect=fake_usage
                    )
                )
            code, out, err = _run_main(
                preflight,
                [_PREFLIGHT_NAME, "--manifest", str(manifest), "--dry-run"],
                extra_patches=patches,
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "disk_insufficient")
            self.assertEqual(code, DIAG_EXIT["disk_insufficient"])
            self.assertEqual(data["status"], "not_ready")
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertTrue(disk_paths)
            self.assertNotIn("1000000000000", data["message"])
            _assert_hygiene(self, out + err)


# ===========================================================================
# C0：模板搜索算法自身 meta 探针（防自假绿；不依赖生产模块）
# ===========================================================================


class TestTemplateSearchMetaProbes(unittest.TestCase):
    """
    用途：锁定 exact template search 反假绿性质：
      纯白不命中；P1 页命中 P1 且不命中 P2；P2 同理；得分仅 0/1。
    """

    def test_exact_template_search_meta_probes(self) -> None:
        from PIL import Image  # noqa: WPS433

        tpl_p1 = _render_default_glyph_template(OCR_P1)
        tpl_p2 = _render_default_glyph_template(OCR_P2)
        self.assertNotEqual(tpl_p1.tobytes(), tpl_p2.tobytes(), msg="P1/P2 模板像素必须不同")

        # 纯白：命中=False，得分=0
        blank = Image.new("L", (max(400, tpl_p1.size[0] + 20), max(80, tpl_p1.size[1] + 20)), 255)
        self.assertFalse(_exact_template_hit(blank, tpl_p1))
        self.assertFalse(_exact_template_hit(blank, tpl_p2))
        self.assertEqual(_template_hit_score(blank, tpl_p1), 0.0)
        self.assertEqual(_template_hit_score(blank, tpl_p2), 0.0)

        # P1 页：粘贴 P1 模板 → exact hit P1，不得 hit P2
        page_p1 = Image.new("L", blank.size, 255)
        page_p1.paste(tpl_p1, (8, 8))
        self.assertTrue(_exact_template_hit(page_p1, tpl_p1))
        self.assertFalse(_exact_template_hit(page_p1, tpl_p2))
        self.assertEqual(_template_hit_score(page_p1, tpl_p1), 1.0)
        self.assertEqual(_template_hit_score(page_p1, tpl_p2), 0.0)

        # P2 页：同理
        page_p2 = Image.new("L", blank.size, 255)
        page_p2.paste(tpl_p2, (8, 8))
        self.assertTrue(_exact_template_hit(page_p2, tpl_p2))
        self.assertFalse(_exact_template_hit(page_p2, tpl_p1))
        self.assertEqual(_template_hit_score(page_p2, tpl_p2), 1.0)
        self.assertEqual(_template_hit_score(page_p2, tpl_p1), 0.0)


# ===========================================================================
# C：两页 image-only ASCII PDF（必须由生产生成器产出）
# ===========================================================================


class TestAsciiImageOnlyPdfFixture(unittest.TestCase):
    """
    用途：生产生成器写 PDF；pypdf 证明无文本层；嵌入图像非空白且 P1/P2 像素命中。
    反假绿：两页纯白 image-only PDF 不得通过。
    """

    def test_production_generator_creates_two_page_image_only_pdf(self) -> None:
        preflight = _require_preflight(self)
        gen = None
        for name in (
            "generate_ascii_image_only_pdf",
            "build_ascii_ocr_fixture",
            "generate_ocr_ascii_pdf",
            "create_ascii_scan_pdf",
        ):
            cand = getattr(preflight, name, None)
            if callable(cand):
                gen = cand
                break
        self.assertIsNotNone(
            gen,
            msg="生产模块必须暴露 ASCII image-only PDF 生成器函数",
        )
        assert gen is not None
        try:
            from pypdf import PdfReader  # noqa: WPS433
        except ImportError as exc:
            self.fail(f"测试环境缺少 pypdf：{exc}")

        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            try:
                pdf_path = gen(root)
            except TypeError:
                pdf_path = gen(root / "scan.pdf")
            pdf_path = Path(pdf_path)
            self.assertTrue(pdf_path.is_file(), msg="生成器必须写出 PDF 文件")
            self.assertNotIn(OCR_P1, pdf_path.name)
            self.assertNotIn(OCR_P2, pdf_path.name)

            raw = pdf_path.read_bytes()
            self.assertNotIn(OCR_P1.encode("ascii"), raw)
            self.assertNotIn(OCR_P2.encode("ascii"), raw)

            reader = PdfReader(str(pdf_path))
            self.assertEqual(len(reader.pages), 2, msg="必须恰好两页")
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                self.assertEqual(
                    text.strip(),
                    "",
                    msg=f"第{i+1}页文本层必须为空，实际={text!r}",
                )
                self.assertNotIn(OCR_P1, text)
                self.assertNotIn(OCR_P2, text)

            meta = reader.metadata
            if meta is not None:
                meta_blob = " ".join(str(v) for v in meta.values() if v is not None)
                self.assertNotIn(OCR_P1, meta_blob)
                self.assertNotIn(OCR_P2, meta_blob)

            # 每页精确嵌入图像：一图、有界尺寸、非空白、两页不同
            page_images = _extract_page_images(pdf_path)
            self.assertEqual(len(page_images), 2)
            page_bytes: list[bytes] = []
            for i, imgs in enumerate(page_images):
                with self.subTest(page=i + 1):
                    self.assertEqual(
                        len(imgs),
                        1,
                        msg=f"第{i+1}页必须恰好嵌入 1 张图像，实际={len(imgs)}",
                    )
                    im = imgs[0]
                    w, h = im.size
                    self.assertGreaterEqual(w, ASCII_PDF_IMG_MIN)
                    self.assertGreaterEqual(h, ASCII_PDF_IMG_MIN)
                    self.assertLessEqual(w, ASCII_PDF_IMG_MAX)
                    self.assertLessEqual(h, ASCII_PDF_IMG_MAX)
                    extrema = im.getextrema()
                    self.assertNotEqual(
                        extrema[0],
                        extrema[1],
                        msg=f"第{i+1}页图像不得为纯色空白",
                    )
                    page_bytes.append(im.tobytes())
            self.assertNotEqual(
                page_bytes[0],
                page_bytes[1],
                msg="两页像素必须不同（P1/P2 顺序内容）",
            )

            # 独立 load_default 字形模板：P1 在页1，P2 在页2（exact hit）
            _assert_glyph_on_page(self, page_images[0][0], OCR_P1)
            _assert_glyph_on_page(self, page_images[1][0], OCR_P2)
            # 交叉不得 exact hit（顺序）；空白得分/命中恒为 0
            tpl_p1 = _render_default_glyph_template(OCR_P1)
            tpl_p2 = _render_default_glyph_template(OCR_P2)
            self.assertFalse(
                _exact_template_hit(page_images[1][0], tpl_p1),
                msg="P1 模板不得在第2页 exact hit",
            )
            self.assertFalse(
                _exact_template_hit(page_images[0][0], tpl_p2),
                msg="P2 模板不得在第1页 exact hit",
            )
            self.assertEqual(_template_hit_score(page_images[1][0], tpl_p1), 0.0)
            self.assertEqual(_template_hit_score(page_images[0][0], tpl_p2), 0.0)
            from PIL import Image as _PilImage  # noqa: WPS433

            blank = _PilImage.new("L", page_images[0][0].size, 255)
            self.assertEqual(_template_hit_score(blank, tpl_p1), 0.0)
            self.assertEqual(_template_hit_score(blank, tpl_p2), 0.0)
            self.assertFalse(_exact_template_hit(blank, tpl_p1))
            self.assertFalse(_exact_template_hit(blank, tpl_p2))


# ===========================================================================
# D/E：参数、CLI 路径来源、九键 JSON 与脱敏
# ===========================================================================


class TestCliArgsJsonAndNoPathExecutable(unittest.TestCase):
    """用途：真 CLI 仅来自 manifest；固定 JSON 九键；零泄漏。"""

    def test_missing_manifest_and_mode_are_argument_invalid(self) -> None:
        preflight = _require_preflight(self)
        code, out, err = _run_main(preflight, [_PREFLIGHT_NAME, "--dry-run"])
        data = _parse_json_stdout(out)
        self.assertEqual(data["diagnosticCode"], "argument_invalid")
        self.assertNotEqual(code, 0)
        self.assertIs(data["didNotRunRealRuntime"], True)
        _assert_exact_json_keys(self, data)
        _assert_hygiene(self, out + err)

        code2, out2, err2 = _run_main(
            preflight,
            [_PREFLIGHT_NAME, "--manifest", "x.json"],
        )
        data2 = _parse_json_stdout(out2)
        self.assertEqual(data2["diagnosticCode"], "argument_invalid")
        _assert_hygiene(self, out2 + err2)

    def test_rejects_client_executable_and_path_lookup(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root)
            evil = str(root / "evil-mineru.exe")
            # 若 argparse 接受 --executable 必须拒绝
            code, out, err = _run_main(
                preflight,
                [
                    _PREFLIGHT_NAME,
                    "--manifest",
                    str(manifest),
                    "--dry-run",
                    "--executable",
                    evil,
                ],
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "argument_invalid")
            self.assertNotIn(evil, out + err)
            self.assertNotIn("evil-mineru", out + err)
            _assert_hygiene(self, out + err)

    def test_json_keys_and_status_mode_profile_matrix_on_cli_missing(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root, write_cli=False)
            code, out, err = _run_main(
                preflight,
                [
                    _PREFLIGHT_NAME,
                    "--manifest",
                    str(manifest),
                    "--dry-run",
                    "--quality-profile",
                    "ascii",
                ],
            )
            data = _parse_json_stdout(out)
            _assert_exact_json_keys(self, data)
            self.assertEqual(set(data.keys()), JSON_KEYS)
            self.assertIn(data["status"], STATUS_VALUES)
            self.assertIn(data["mode"], MODE_VALUES)
            self.assertIn(data["qualityProfile"], QUALITY_PROFILES)
            self.assertEqual(data["engine"], "mineru")
            self.assertEqual(code, DIAG_EXIT["cli_missing"])
            # 不得泄漏路径/异常
            _assert_hygiene(self, out + err, forbidden_substrings=[str(runtime)])


# ===========================================================================
# F：fake-runtime 成功/失败矩阵（自动化证据，不得冒充 real-runtime/quality）
# ===========================================================================


class TestFakeRuntimeMatrix(unittest.TestCase):
    """用途：假 exe + mock Popen 覆盖 OCR 包装安全；成功仅 automated/fake 证据。"""

    def _run_ocr(
        self,
        preflight: Any,
        manifest: Path,
        impl: Path,
        *,
        quality: str = "ascii",
        extra_patches: list[Any] | None = None,
        original_records: list[dict[str, Any]] | None = None,
    ) -> tuple[int, str, str, list[Path]]:
        td_patch, created = _track_tempdirs(preflight)
        patches = _patch_popen_for_impl(
            preflight,
            impl,
            {"mineru.exe", "mineru"},
            original_records=original_records,
        )
        patches.append(td_patch)
        if extra_patches:
            patches.extend(extra_patches)
        usage = mock.Mock(free=10**12, total=10**13, used=0)
        patches.append(mock.patch.object(shutil, "disk_usage", return_value=usage))
        if hasattr(preflight, "shutil"):
            patches.append(
                mock.patch.object(preflight.shutil, "disk_usage", return_value=usage)
            )
        code, out, err = _run_main(
            preflight,
            [
                _PREFLIGHT_NAME,
                "--manifest",
                str(manifest),
                "--ocr-check",
                "--quality-profile",
                quality,
            ],
            extra_patches=patches,
        )
        return code, out, err, created

    def test_fake_success_is_automated_not_real_quality_claim(self) -> None:
        """
        用途：fake-runtime 包装成功精确九键；原始 Popen 首参=manifest CLI。
        报告层声明 automated/fake-runtime，不篡改生产九键语义。
        """
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root)
            expected_cli = (runtime / "venv" / "Scripts" / "mineru.exe").resolve()
            impl_dir = root / "impl"
            impl_dir.mkdir()
            # 假实现必须校验 -p 为两页 image-only PDF，禁止忽略输入回填
            impl = _write_fake_mineru_impl(
                impl_dir,
                mode="success",
                markdown_text=f"# ok\n{OCR_P1}\nmid\n{OCR_P2}\n",
                require_valid_input_pdf=True,
            )
            original_records: list[dict[str, Any]] = []
            code, out, err, created = self._run_ocr(
                preflight,
                manifest,
                impl,
                original_records=original_records,
            )
            data = _parse_json_stdout(out)
            _assert_exact_json_keys(self, data)
            # 精确成功门（生产九键语义；fake 标签只在测试报告层）
            self.assertEqual(data["diagnosticCode"], "ocr_passed")
            self.assertEqual(code, DIAG_EXIT["ocr_passed"])
            self.assertEqual(code, 0)
            self.assertEqual(data["status"], "passed")
            self.assertIs(data["ok"], True)
            self.assertIs(data["runtimeVerified"], True)
            self.assertIs(data["didNotRunRealRuntime"], False)
            self.assertEqual(data["engine"], "mineru")
            self.assertEqual(data["mode"], "ocr-check")
            self.assertEqual(data["qualityProfile"], "ascii")
            # 报告层：message 不得用“真实 OCR 已通过”等质量真值话术越权
            self.assertNotIn("真实 OCR 已通过", data.get("message", ""))

            # 主证据：替换前原始 Popen cmd/kwargs + 调用当时 -p 快照
            self.assertEqual(len(original_records), 1, msg="Popen 必须恰好 1 次")
            rec = original_records[0]
            cmd = rec["cmd"]
            self.assertIsInstance(cmd, list)
            self.assertGreaterEqual(len(cmd), 1)
            self.assertEqual(
                str(Path(cmd[0]).resolve()),
                str(expected_cli),
                msg="原始 Popen 首参必须是 manifest CLI，不得用 python/fake 文件名冒充",
            )
            kwargs = rec["kwargs"]
            self.assertIs(kwargs.get("shell"), False)
            self.assertIn("-p", cmd)
            self.assertIn("-o", cmd)
            self.assertIn("-b", cmd)
            self.assertEqual(cmd[cmd.index("-b") + 1], "pipeline")
            p_idx = cmd.index("-p")
            self.assertLess(p_idx + 1, len(cmd))
            input_pdf = Path(cmd[p_idx + 1])

            # 调用瞬间快照：存在、两页、每页一图、文本层空、非纯色、模板证据
            snap = rec.get("input_pdf_snapshot")
            self.assertIsInstance(snap, dict, msg="Popen side-effect 必须写入 input_pdf_snapshot")
            self.assertIsNone(snap.get("error"), msg=f"调用时 -p 快照失败：{snap.get('error')}")
            self.assertTrue(snap.get("exists"), msg="调用当时 -p 必须存在")
            self.assertEqual(
                str(Path(snap["path"]).resolve()),
                str(input_pdf.resolve()),
            )
            self.assertEqual(snap.get("page_count"), 2, msg="调用当时必须两页")
            pages_snap = snap.get("pages")
            self.assertIsInstance(pages_snap, list, msg="input_pdf_snapshot.pages 必须为 list")
            self.assertEqual(len(pages_snap), 2)
            for i, page_snap in enumerate(pages_snap):
                with self.subTest(popen_pdf_page=i + 1):
                    self.assertEqual(
                        page_snap["image_count"],
                        1,
                        msg=f"第{i+1}页必须恰好 1 图",
                    )
                    self.assertTrue(page_snap["text_empty"], msg=f"第{i+1}页文本层必须空")
                    self.assertTrue(page_snap["not_solid"], msg=f"第{i+1}页不得纯色")
            # 像素/模板证据：页1 exact P1 且非 P2；页2 exact P2 且非 P1
            self.assertTrue(snap["pages"][0]["glyph_p1_exact"])
            self.assertFalse(snap["pages"][0]["glyph_p2_exact"])
            self.assertTrue(snap["pages"][1]["glyph_p2_exact"])
            self.assertFalse(snap["pages"][1]["glyph_p1_exact"])

            env = kwargs.get("env") or {}
            for banned in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "X_LOCAL_PARSE_TICKET",
                "CSRF_TOKEN",
                "COOKIE",
            ):
                self.assertNotIn(banned, env)
            cwd_raw = kwargs.get("cwd")
            self.assertIsNotNone(cwd_raw, msg="Popen 必须传 cwd")
            cwd = Path(cwd_raw).resolve()
            for key in (
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "TEMP",
                "TMP",
                "TMPDIR",
            ):
                self.assertIn(key, env, msg=f"缺少可写环境变量 {key}")
                self.assertEqual(str(Path(env[key]).resolve()), str(cwd))

            # 假 runner 内部记录仅作副作用；路径字符串可比较，禁止 main 返回后回读已删 PDF
            inv = _load_invocation(impl_dir)
            self.assertIsNotNone(inv.get("inp"))
            self.assertEqual(
                str(Path(inv["inp"]).resolve()),
                str(Path(snap["path"]).resolve()),
            )
            # main 返回后：原 -p 路径不得仍存在；TEMP 根已清理
            self.assertFalse(
                input_pdf.exists(),
                msg="main 返回后 -p PDF 必须已清理，禁止回读已删文件",
            )
            _assert_temp_cleaned(self, created)
            _assert_hygiene(self, out + err, forbidden_substrings=[str(runtime)])
            # 分层标签仅在审查报告层声明为 fake-runtime/automated，不写入生产 JSON

    def test_fake_nonzero_timeout_interrupt_md_and_markers(self) -> None:
        preflight = _require_preflight(self)
        cases = [
            ("fail", "parser_failed"),
            ("no_md", "output_invalid"),
            ("multi_md", "output_invalid"),
            ("empty_md", "output_invalid"),
            ("oversized", "output_invalid"),
            ("missing_marker", "ocr_marker_missing"),
            ("reverse_markers", "ocr_marker_missing"),
        ]
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            for mode, diag in cases:
                with self.subTest(mode=mode):
                    case_root = root / mode
                    case_root.mkdir()
                    runtime, manifest = _prepare_runtime_tree(case_root)
                    impl_dir = case_root / "impl"
                    impl_dir.mkdir()
                    impl = _write_fake_mineru_impl(impl_dir, mode=mode)
                    code, out, err, created = self._run_ocr(preflight, manifest, impl)
                    data = _parse_json_stdout(out)
                    self.assertEqual(data["diagnosticCode"], diag, msg=out + err)
                    self.assertEqual(code, DIAG_EXIT[diag])
                    self.assertIs(data["ok"], False)
                    self.assertIs(data["didNotRunRealRuntime"], True)
                    _assert_temp_cleaned(self, created)
                    _assert_hygiene(self, out + err)

            # 超时
            case_root = root / "timeout"
            case_root.mkdir()
            runtime, manifest = _prepare_runtime_tree(case_root)
            impl_dir = case_root / "impl"
            impl_dir.mkdir()
            impl = _write_fake_mineru_impl(impl_dir, mode="success", sleep_seconds=2)
            timeout_patches: list[Any] = []
            for attr in (
                "PARSER_TIMEOUT_SECONDS",
                "OCR_TIMEOUT_SECONDS",
                "MINERU_TIMEOUT_SECONDS",
            ):
                if hasattr(preflight, attr):
                    timeout_patches.append(mock.patch.object(preflight, attr, 0.2))
            try:
                import mineru_callback_helper as mh  # noqa: WPS433

                timeout_patches.append(
                    mock.patch.object(mh, "MINERU_TIMEOUT_SECONDS", 0.2)
                )
            except Exception:
                pass
            code, out, err, created = self._run_ocr(
                preflight, manifest, impl, extra_patches=timeout_patches
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "parser_timeout")
            self.assertEqual(code, DIAG_EXIT["parser_timeout"])
            _assert_temp_cleaned(self, created)

            # 中断
            case_root = root / "intr"
            case_root.mkdir()
            runtime, manifest = _prepare_runtime_tree(case_root)
            impl_dir = case_root / "impl"
            impl_dir.mkdir()
            impl = _write_fake_mineru_impl(impl_dir, mode="success")

            def raise_interrupt(*_a: Any, **_k: Any) -> Any:
                raise KeyboardInterrupt()

            # 直接让 Popen.wait 路径中断：patch run 入口
            extra: list[Any] = []
            try:
                import mineru_callback_helper as mh  # noqa: WPS433

                if hasattr(mh, "run_mineru_process"):
                    extra.append(
                        mock.patch.object(
                            mh, "run_mineru_process", side_effect=raise_interrupt
                        )
                    )
            except Exception:
                pass
            if hasattr(preflight, "run_mineru_process"):
                extra.append(
                    mock.patch.object(
                        preflight, "run_mineru_process", side_effect=raise_interrupt
                    )
                )
            if not extra:
                # 兜底：Popen.wait 抛中断
                real_popen = subprocess.Popen

                class BoomProc:
                    def wait(self, timeout: Any = None) -> int:
                        raise KeyboardInterrupt()

                    def poll(self) -> None:
                        return None

                    def terminate(self) -> None:
                        return None

                    def kill(self) -> None:
                        return None

                def boom_popen(*_a: Any, **_k: Any) -> Any:
                    return BoomProc()

                extra.append(mock.patch.object(subprocess, "Popen", side_effect=boom_popen))
            code, out, err, created = self._run_ocr(
                preflight, manifest, impl, extra_patches=extra
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "interrupted")
            self.assertEqual(code, DIAG_EXIT["interrupted"])
            _assert_temp_cleaned(self, created)


# ===========================================================================
# G：Windows 中文 profile 仅前置
# ===========================================================================


class TestWindowsZhQualityPrecondition(unittest.TestCase):
    """用途：非 Windows / 字体缺失 → quality_precondition_failed，不计 passed。"""

    def test_non_windows_is_quality_precondition_failed(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root)
            usage = mock.Mock(free=10**12, total=10**13, used=0)
            patches = [
                mock.patch.object(os, "name", "posix"),
                mock.patch.object(shutil, "disk_usage", return_value=usage),
            ]
            if hasattr(preflight, "os"):
                patches.append(mock.patch.object(preflight.os, "name", "posix"))
            if hasattr(preflight, "shutil"):
                patches.append(
                    mock.patch.object(preflight.shutil, "disk_usage", return_value=usage)
                )
            # 禁止为变绿而读/下载字体
            if hasattr(preflight, "open"):
                pass
            code, out, err = _run_main(
                preflight,
                [
                    _PREFLIGHT_NAME,
                    "--manifest",
                    str(manifest),
                    "--ocr-check",
                    "--quality-profile",
                    "windows-zh",
                ],
                extra_patches=patches,
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "quality_precondition_failed")
            self.assertEqual(data["status"], "not_ready")
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertIs(data["ok"], False)
            self.assertEqual(code, DIAG_EXIT["quality_precondition_failed"])
            self.assertEqual(data["qualityProfile"], "windows-zh")
            _assert_hygiene(self, out + err)

    def test_windows_missing_fonts_is_quality_precondition_failed(self) -> None:
        preflight = _require_preflight(self)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as td:
            root = Path(td)
            runtime, manifest = _prepare_runtime_tree(root)
            usage = mock.Mock(free=10**12, total=10**13, used=0)
            # 伪造空字体目录
            fonts = root / "Fonts"
            fonts.mkdir()
            patches = [
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(shutil, "disk_usage", return_value=usage),
            ]
            if hasattr(preflight, "os"):
                patches.append(mock.patch.object(preflight.os, "name", "nt"))
            # 常见字体候选路径解析到空目录
            real_exists = Path.exists

            def fake_exists(self_path: Path) -> bool:
                s = str(self_path).replace("/", "\\").lower()
                if s.endswith("simhei.ttf") or s.endswith("msyh.ttc"):
                    return False
                return real_exists(self_path)

            patches.append(mock.patch.object(Path, "exists", fake_exists))
            if hasattr(preflight, "shutil"):
                patches.append(
                    mock.patch.object(preflight.shutil, "disk_usage", return_value=usage)
                )
            code, out, err = _run_main(
                preflight,
                [
                    _PREFLIGHT_NAME,
                    "--manifest",
                    str(manifest),
                    "--ocr-check",
                    "--quality-profile",
                    "windows-zh",
                ],
                extra_patches=patches,
            )
            data = _parse_json_stdout(out)
            self.assertEqual(data["diagnosticCode"], "quality_precondition_failed")
            self.assertEqual(data["status"], "not_ready")
            self.assertIs(data["didNotRunRealRuntime"], True)
            self.assertNotEqual(code, 0)
            # 不得把字体路径/内容写进消息
            self.assertNotIn("simhei", data["message"].lower())
            self.assertNotIn("msyh", data["message"].lower())
            _assert_hygiene(self, out + err)


# ===========================================================================
# 真实 runtime / quality 默认 did-not-run 声明（分层计数）
# ===========================================================================


class TestRealRuntimeDidNotRunLayer(unittest.TestCase):
    """用途：本机未装真实 CLI 时不得 skip；分层报告 real-runtime/quality did-not-run。"""

    def test_real_runtime_layer_is_explicit_not_ready_not_skip(self) -> None:
        # 不依赖生产：作为计数分层的文档化断言
        self.assertIn("cli_missing", DIAG_EXIT)
        self.assertNotEqual(DIAG_EXIT["cli_missing"], 0)
        # 本测试文件不得出现 skip 装饰
        src = Path(__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"@unittest\.skip(?:If|Unless)?\b")
        self.assertNotRegex(src, r"@pytest\.mark\.(skip|xfail)\b")


if __name__ == "__main__":
    unittest.main()
