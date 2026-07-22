# -*- coding: utf-8 -*-
"""
模块：V1-M 管理式本机 OCR 专用 runtime 预检
用途：从仓外 manifest 严格解析 MinerU CLI/模型门；生成两页 image-only ASCII PDF；
      dry-run 静态就绪或 ocr-check 假/真 runner 包装门；输出固定九键 JSON。
对接：docs/v1m-managed-local-ocr-runtime-contract.md；mineru_callback_helper 原语；
      不签发/不消费票据，不 HTTP，不读业务 uploads/DB。
二次开发：禁止 PATH/客户端 executable 查找；禁止把假 runner 命中写成真实 OCR 质量通过；
      real-runtime/quality 默认 did-not-run；仅标准库 + Pillow（生成 PDF）+ 复用 P8D 原语。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from mineru_callback_helper import (
    HelperError,
    MSG_ERR_INTERRUPTED,
    MSG_ERR_MARKDOWN,
    MSG_ERR_MINERU_FAILED,
    MSG_ERR_MINERU_TIMEOUT,
    build_mineru_command,
    build_mineru_env,
    find_and_read_markdown,
    run_mineru_process,
)

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

OCR_P1 = "BIAOSHU_OCR_P1_V1"
OCR_P2 = "BIAOSHU_OCR_P2_V1"
# windows-zh 两页中文短句（仅像素/Markdown 顺序门；禁止文本层）
ZH_SENTENCE_P1 = "封面验收短句甲"
ZH_SENTENCE_P2 = "正文验收短句乙"

TEMP_PREFIX = "biaoshu-managed-ocr-"
MODEL_MARKER_MAX_BYTES = 64 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ASCII_PDF_IMG_MIN = 32
ASCII_PDF_IMG_MAX = 2048

# 可测超时（秒）；测试可 patch 本常量与 helper 超时
PARSER_TIMEOUT_SECONDS: float = 30 * 60
OCR_TIMEOUT_SECONDS: float = 30 * 60
MINERU_TIMEOUT_SECONDS: float = 30 * 60

ENGINE = "mineru"
ALLOWED_MODES = frozenset({"dry-run", "ocr-check"})
ALLOWED_QUALITY = frozenset({"ascii", "windows-zh"})
MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "engine",
        "cliRelativePath",
        "modelMarkerRelativePath",
        "requiredFreeBytes",
    }
)
JSON_KEYS = (
    "ok",
    "status",
    "engine",
    "mode",
    "diagnosticCode",
    "message",
    "runtimeVerified",
    "didNotRunRealRuntime",
    "qualityProfile",
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

# 固定中文消息（禁止拼接路径/argv/正文/异常类名）
MSG_STATIC_READY = "静态检查通过，尚未运行解析器"
MSG_OCR_PASSED = "合成扫描样本解析通过并命中预期标记"
MSG_ARGUMENT_INVALID = "参数无效"
MSG_MANIFEST_INVALID = "运行时清单无效"
MSG_CLI_MISSING = "未找到解析器命令或安全类型不合格"
MSG_MODEL_MISSING = "模型就绪标记缺失"
MSG_DISK_INSUFFICIENT = "目标卷可用空间不足"
MSG_QUALITY_PRECONDITION = "质量配置前置条件不满足"
MSG_PARSER_FAILED = "解析器运行失败"
MSG_PARSER_TIMEOUT = "解析器运行超时"
MSG_OUTPUT_INVALID = "未找到合法的唯一 Markdown 输出"
MSG_OCR_MARKER_MISSING = "输出未包含预期 OCR 标记或顺序错误"
MSG_INTERRUPTED = "操作已中断"
MSG_INTERNAL_ERROR = "预检内部错误"

_DIAG_DEFAULT_MESSAGE: dict[str, str] = {
    "static_ready": MSG_STATIC_READY,
    "ocr_passed": MSG_OCR_PASSED,
    "runtime_manifest_invalid": MSG_MANIFEST_INVALID,
    "cli_missing": MSG_CLI_MISSING,
    "model_missing": MSG_MODEL_MISSING,
    "disk_insufficient": MSG_DISK_INSUFFICIENT,
    "quality_precondition_failed": MSG_QUALITY_PRECONDITION,
    "parser_failed": MSG_PARSER_FAILED,
    "parser_timeout": MSG_PARSER_TIMEOUT,
    "output_invalid": MSG_OUTPUT_INVALID,
    "ocr_marker_missing": MSG_OCR_MARKER_MISSING,
    "interrupted": MSG_INTERRUPTED,
    "internal_error": MSG_INTERNAL_ERROR,
    "argument_invalid": MSG_ARGUMENT_INVALID,
}

_HELPER_MSG_TO_DIAG: dict[str, str] = {
    MSG_ERR_MINERU_FAILED: "parser_failed",
    MSG_ERR_MINERU_TIMEOUT: "parser_timeout",
    MSG_ERR_MARKDOWN: "output_invalid",
    MSG_ERR_INTERRUPTED: "interrupted",
}

# Windows 中文质量仅只读系统字体候选（禁止下载）
_WIN_FONT_NAMES = ("simhei.ttf", "msyh.ttc")


class PreflightError(Exception):
    """用途：预检固定诊断码 + 中文消息，禁止携带路径或敏感上下文。"""

    def __init__(self, diagnostic_code: str, message: str | None = None) -> None:
        if diagnostic_code not in DIAG_EXIT:
            diagnostic_code = "internal_error"
        self.diagnostic_code = diagnostic_code
        self.message = message or _DIAG_DEFAULT_MESSAGE.get(
            diagnostic_code, MSG_INTERNAL_ERROR
        )
        super().__init__(self.message)


class QuietArgumentParser(argparse.ArgumentParser):
    """用途：参数错误只抛固定诊断，避免 argparse 回显用户原始值。"""

    def error(self, message: str) -> None:  # noqa: A003
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)


# ---------------------------------------------------------------------------
# JSON 与状态
# ---------------------------------------------------------------------------


def _status_for(diagnostic_code: str) -> str:
    """用途：诊断码映射到契约 status 四值。"""
    if diagnostic_code == "static_ready":
        return "ready"
    if diagnostic_code == "ocr_passed":
        return "passed"
    if diagnostic_code in (
        "cli_missing",
        "model_missing",
        "disk_insufficient",
        "quality_precondition_failed",
    ):
        return "not_ready"
    return "failed"


def build_result(
    *,
    ok: bool,
    mode: str,
    diagnostic_code: str,
    message: str,
    runtime_verified: bool,
    did_not_run_real_runtime: bool,
    quality_profile: str,
    status: str | None = None,
) -> dict[str, Any]:
    """用途：构造契约九键 JSON 对象（字段顺序固定）。"""
    qp = quality_profile if quality_profile in ALLOWED_QUALITY else "ascii"
    # Q11：mode 永远只能 dry-run|ocr-check；空串/非法值固定 dry-run fallback
    mode_out = mode if mode in ALLOWED_MODES else "dry-run"
    return {
        "ok": bool(ok),
        "status": status if status is not None else _status_for(diagnostic_code),
        "engine": ENGINE,
        "mode": mode_out,
        "diagnosticCode": diagnostic_code,
        "message": message,
        "runtimeVerified": bool(runtime_verified),
        "didNotRunRealRuntime": bool(did_not_run_real_runtime),
        "qualityProfile": qp,
    }


def emit_json(payload: Mapping[str, Any]) -> None:
    """用途：stdout 仅输出一个九键 JSON 对象。"""
    ordered = {k: payload[k] for k in JSON_KEYS}
    sys.stdout.write(json.dumps(ordered, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def map_helper_error(exc: HelperError) -> tuple[str, str]:
    """用途：仅按固定 MSG_* 精确映射 HelperError。"""
    msg = getattr(exc, "message", None)
    if not isinstance(msg, str):
        return "internal_error", MSG_INTERNAL_ERROR
    diag = _HELPER_MSG_TO_DIAG.get(msg)
    if diag is None:
        return "internal_error", MSG_INTERNAL_ERROR
    return diag, _DIAG_DEFAULT_MESSAGE[diag]


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    用途：解析 --manifest 与互斥 --dry-run/--ocr-check、--quality-profile。
    二次开发：禁止 --executable / PATH 查找入口；非法参数固定 argument_invalid。
    """
    parser = QuietArgumentParser(
        prog="managed_runtime_preflight",
        description="管理式本机 OCR runtime 预检（V1-M）",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="仓外 runtime 清单 JSON 路径",
    )
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="静态检查：不启动解析器",
    )
    mode_group.add_argument(
        "--ocr-check",
        action="store_true",
        help="合成 image-only PDF OCR 门",
    )
    parser.add_argument(
        "--quality-profile",
        default="ascii",
        help="质量配置：ascii 或 windows-zh",
    )
    raw = list(argv) if argv is not None else list(sys.argv)
    return parser.parse_args(raw[1:] if raw else [])


def validate_parsed_args(args: argparse.Namespace) -> tuple[str, str, Path]:
    """
    用途：校验模式互斥、清单路径与质量配置枚举。
    返回：(mode, quality_profile, manifest_path)
    """
    dry = bool(getattr(args, "dry_run", False))
    ocr = bool(getattr(args, "ocr_check", False))
    if dry == ocr:
        # 都未选或异常同时选（互斥组通常不会同时）
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
    mode = "dry-run" if dry else "ocr-check"

    quality = str(getattr(args, "quality_profile", "ascii") or "")
    if quality not in ALLOWED_QUALITY:
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)

    manifest_raw = getattr(args, "manifest", None)
    if manifest_raw is None or not str(manifest_raw).strip():
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
    # 仅接受路径字符串；不打开、不回显
    return mode, quality, Path(str(manifest_raw))


# ---------------------------------------------------------------------------
# Manifest 严格解析
# ---------------------------------------------------------------------------


def _is_reparse_point(path: Path) -> bool:
    """
    用途：no-follow 检测 Windows reparse 属性；无属性平台恒 False。
    二次开发：必须读字面路径自身属性。path.stat() 会跟随 junction 丢失 0x400；
    主证据为 path.lstat()（或等价 stat(follow_symlinks=False)）。
    """
    try:
        # 主证据：lstat 不跟随 reparse，保留 FILE_ATTRIBUTE_REPARSE_POINT
        st = path.lstat()
    except OSError:
        return False
    attrs = int(getattr(st, "st_file_attributes", 0) or 0)
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    # 可选辅助：不得替代 lstat；新 API 缺失时静默忽略
    try:
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc) and bool(is_junc()):
            return True
    except OSError:
        pass
    return False


def _reject_relative_path_string(rel: str) -> None:
    """用途：拒绝绝对/UNC/URL/穿越/空等非法相对路径字面量。"""
    if not isinstance(rel, str) or not rel or not rel.strip():
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if "\x00" in rel or "\r" in rel or "\n" in rel:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    text = rel.strip()
    lower = text.lower()
    if "://" in text or lower.startswith("file:"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if text.startswith("\\\\") or text.startswith("//"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    # 盘符绝对路径
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if text.startswith("/") or text.startswith("\\"):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    try:
        if Path(text).is_absolute():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    # 分段拒绝 .. 与空段（保留 . 归一）
    normalized = text.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if not parts:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)


def _path_is_symlink_or_reparse(path: Path) -> bool:
    """
    用途：在 resolve 前判定字面路径身份是否为 symlink 或 reparse。
    二次开发：不得先 follow 再判定；缺失路径恒 False（留给 cli_missing/model_missing）。
    """
    try:
        if path.is_symlink():
            return True
    except OSError:
        pass
    return _is_reparse_point(path)


def resolve_under_runtime_root(runtime_root: Path, rel: str) -> Path:
    """
    用途：将相对路径解析到 runtime 根内；逃逸固定 runtime_manifest_invalid。
    Q9：保留 manifest 相对路径字面身份——resolve 前从 runtime_root 到 leaf
    逐组件拒绝 symlink 与 FILE_ATTRIBUTE_REPARSE_POINT，再 resolve 校验仍在根内。
    不得检查根外祖先；缺文件不在此映射为 invalid（由 CLI/marker 门分别处理）。
    """
    _reject_relative_path_string(rel)
    try:
        root = runtime_root.resolve()
    except OSError as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    # 逐组件字面身份检查（仅 runtime 根内路径；不检查根外祖先）
    cursor = root
    for part in parts:
        cursor = cursor / part
        if _path_is_symlink_or_reparse(cursor):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    try:
        resolved = cursor.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc
    # resolve 后再次拒绝身份异常（防御 follow 后仍可见的 reparse）
    if _path_is_symlink_or_reparse(resolved):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    return resolved


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """
    用途：读取并严格校验五键 manifest；返回含 runtime 根与已解析路径的内部字典。
    二次开发：禁止额外键、bool 冒充 int、客户端路径反射。
    """
    try:
        if manifest_path.is_symlink() or _is_reparse_point(manifest_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if not manifest_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        raw_text = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc

    if not isinstance(data, dict):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    if set(data.keys()) != MANIFEST_KEYS:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    schema = data.get("schemaVersion")
    if type(schema) is not int or schema != 1:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    engine = data.get("engine")
    if engine != "mineru":
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    free = data.get("requiredFreeBytes")
    if type(free) is not int or free <= 0:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    cli_rel = data.get("cliRelativePath")
    model_rel = data.get("modelMarkerRelativePath")
    if not isinstance(cli_rel, str) or not isinstance(model_rel, str):
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    try:
        runtime_root = manifest_path.resolve(strict=True).parent
    except OSError as exc:
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID) from exc

    cli_path = resolve_under_runtime_root(runtime_root, cli_rel)
    model_path = resolve_under_runtime_root(runtime_root, model_rel)

    # CLI 形态门：后缀/链接/reparse 在存在前也可判定（路径已解析）
    if cli_path.suffix.lower() != ".exe":
        raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)

    return {
        "runtime_root": runtime_root,
        "cli_path": cli_path,
        "model_path": model_path,
        "required_free_bytes": free,
        "cli_rel": cli_rel,
        "model_rel": model_rel,
    }


def _validate_cli_file(cli_path: Path) -> None:
    """用途：CLI 必须是根内普通非 symlink/reparse 的 .exe；缺失→cli_missing。"""
    try:
        if not cli_path.exists():
            raise PreflightError("cli_missing", MSG_CLI_MISSING)
        if cli_path.is_symlink() or _is_reparse_point(cli_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if not cli_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if cli_path.suffix.lower() != ".exe":
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError("cli_missing", MSG_CLI_MISSING) from exc


def _validate_model_marker(model_path: Path) -> None:
    """用途：marker 必须是根内普通小文件；缺失→model_missing；形态非法→manifest_invalid。"""
    try:
        if not model_path.exists():
            raise PreflightError("model_missing", MSG_MODEL_MISSING)
        if model_path.is_symlink() or _is_reparse_point(model_path):
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        if model_path.is_dir() or not model_path.is_file():
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
        size = int(model_path.stat().st_size)
        if size <= 0 or size > MODEL_MARKER_MAX_BYTES:
            raise PreflightError("runtime_manifest_invalid", MSG_MANIFEST_INVALID)
    except PreflightError:
        raise
    except OSError as exc:
        raise PreflightError("model_missing", MSG_MODEL_MISSING) from exc


def check_disk_free(runtime_root: Path, required_free_bytes: int) -> None:
    """用途：对 manifest 所在目标卷检查 available >= requiredFreeBytes。"""
    try:
        usage = shutil.disk_usage(str(runtime_root))
        free = int(usage.free)
    except Exception as exc:
        raise PreflightError("disk_insufficient", MSG_DISK_INSUFFICIENT) from exc
    if free < int(required_free_bytes):
        raise PreflightError("disk_insufficient", MSG_DISK_INSUFFICIENT)


def validate_runtime_ready(manifest_info: Mapping[str, Any]) -> None:
    """用途：CLI + model + disk 串行门。"""
    _validate_cli_file(Path(manifest_info["cli_path"]))
    _validate_model_marker(Path(manifest_info["model_path"]))
    check_disk_free(
        Path(manifest_info["runtime_root"]),
        int(manifest_info["required_free_bytes"]),
    )


# ---------------------------------------------------------------------------
# ASCII image-only PDF 生成器
# ---------------------------------------------------------------------------


def _render_ascii_page(text: str) -> Any:
    """
    用途：用 Pillow 内置 ASCII 位图字体把锚点画到像素图（不进 PDF 文本层）。
    返回：L 模式 Image，尺寸有界且非纯色。
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: WPS433

    font = ImageFont.load_default()
    probe = Image.new("L", (8, 8), 255)
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    glyph_w = max(1, bbox[2] - bbox[0])
    glyph_h = max(1, bbox[3] - bbox[1])
    # 留边，保证模板 exact 搜索窗口可命中
    pad_x, pad_y = 16, 16
    width = max(ASCII_PDF_IMG_MIN, min(ASCII_PDF_IMG_MAX, glyph_w + pad_x * 2))
    height = max(ASCII_PDF_IMG_MIN, min(ASCII_PDF_IMG_MAX, glyph_h + pad_y * 2))
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    # 与测试模板一致：字号同源 load_default，前景=0
    draw.text((pad_x, pad_y), text, fill=0, font=font)
    extrema = canvas.getextrema()
    if extrema[0] == extrema[1]:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    return canvas


def _write_lossless_gray_image_pdf(images: Sequence[Any], out_path: Path) -> None:
    """
    用途：将多张 L 模式图以 Flate DeviceGray 无损嵌入 image-only PDF。
    二次开发：禁止 Pillow PDF 的 JPEG 有损路径（会破坏 exact glyph 模板匹配）。
    """
    import zlib

    if not images:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)

    # 对象布局：1 Catalog, 2 Pages, 每页 Page/Content/Image 各一
    page_ids: list[int] = []
    page_payloads: list[tuple[int, int, int, int, int, bytes, bytes]] = []
    # tuple: page_id, content_id, image_id, w, h, content_stream, compressed_image
    next_id = 3
    for im in images:
        if getattr(im, "mode", None) != "L":
            im = im.convert("L")
        w, h = int(im.size[0]), int(im.size[1])
        if w < ASCII_PDF_IMG_MIN or h < ASCII_PDF_IMG_MIN:
            raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
        if w > ASCII_PDF_IMG_MAX or h > ASCII_PDF_IMG_MAX:
            raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
        compressed = zlib.compress(im.tobytes(), level=9)
        page_id = next_id
        content_id = next_id + 1
        image_id = next_id + 2
        next_id += 3
        page_ids.append(page_id)
        content_stream = f"q\n{w} 0 0 {h} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
        page_payloads.append(
            (page_id, content_id, image_id, w, h, content_stream, compressed)
        )

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    parts: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets: list[int] = [0]

    def emit(obj_num: int, body: bytes) -> None:
        while len(offsets) <= obj_num:
            offsets.append(0)
        offsets[obj_num] = sum(len(p) for p in parts)
        parts.append(f"{obj_num} 0 obj\n".encode("ascii"))
        parts.append(body)
        if not body.endswith(b"\n"):
            parts.append(b"\n")
        parts.append(b"endobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    emit(
        2,
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"),
    )
    for page_id, content_id, image_id, w, h, content_stream, compressed in page_payloads:
        emit(
            page_id,
            (
                b"<< /Type /Page /Parent 2 0 R "
                + f"/MediaBox [0 0 {w} {h}] ".encode("ascii")
                + f"/Contents {content_id} 0 R ".encode("ascii")
                + f"/Resources << /XObject << /Im0 {image_id} 0 R >> >> >>".encode(
                    "ascii"
                )
            ),
        )
        emit(
            content_id,
            (
                f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
                + content_stream
                + b"endstream"
            ),
        )
        emit(
            image_id,
            (
                b"<< /Type /XObject /Subtype /Image "
                + f"/Width {w} /Height {h} ".encode("ascii")
                + b"/ColorSpace /DeviceGray /BitsPerComponent 8 "
                + f"/Length {len(compressed)} /Filter /FlateDecode >>\n".encode(
                    "ascii"
                )
                + b"stream\n"
                + compressed
                + b"\nendstream"
            ),
        )

    xref_pos = sum(len(p) for p in parts)
    max_obj = next_id - 1
    xref = [f"xref\n0 {max_obj + 1}\n".encode("ascii")]
    xref.append(b"0000000000 65535 f \n")
    for i in range(1, max_obj + 1):
        xref.append(f"{offsets[i]:010d} 00000 n \n".encode("ascii"))
    parts.extend(xref)
    parts.append(
        (
            f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    out_path.write_bytes(b"".join(parts))


def _resolve_pdf_out_path(target: Path | str, default_name: str) -> Path:
    """用途：目录→默认文件名；.pdf 路径→直接使用。"""
    target_path = Path(target)
    if target_path.suffix.lower() == ".pdf":
        out_path = target_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path
    target_path.mkdir(parents=True, exist_ok=True)
    return target_path / default_name


def generate_ascii_image_only_pdf(target: Path | str) -> Path:
    """
    用途：生成两页 image-only PDF；P1/P2 锚点仅存在于像素，不在文本层/metadata/文件名。
    参数：目录则写入其下固定文件名；若以 .pdf 结尾则作为输出路径。
    """
    out_path = _resolve_pdf_out_path(target, "managed_ocr_scan.pdf")

    page1 = _render_ascii_page(OCR_P1)
    page2 = _render_ascii_page(OCR_P2)
    _write_lossless_gray_image_pdf([page1, page2], out_path)
    # 防御：文件名不得含锚点；原始字节不得含锚点明文
    if OCR_P1 in out_path.name or OCR_P2 in out_path.name:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    raw = out_path.read_bytes()
    if OCR_P1.encode("ascii") in raw or OCR_P2.encode("ascii") in raw:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    return out_path


def generate_ocr_ascii_pdf(target: Path | str) -> Path:
    """用途：generate_ascii_image_only_pdf 的别名，满足测试探测的多名称。"""
    return generate_ascii_image_only_pdf(target)


def build_ascii_ocr_fixture(target: Path | str) -> Path:
    """用途：generate_ascii_image_only_pdf 的别名。"""
    return generate_ascii_image_only_pdf(target)


def create_ascii_scan_pdf(target: Path | str) -> Path:
    """用途：generate_ascii_image_only_pdf 的别名。"""
    return generate_ascii_image_only_pdf(target)


# ---------------------------------------------------------------------------
# Windows 中文质量前置与 image-only 生成器
# ---------------------------------------------------------------------------


def _windows_font_candidates() -> list[Path]:
    """用途：只读系统字体候选路径列表。"""
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    fonts_dir = Path(system_root) / "Fonts"
    return [fonts_dir / name for name in _WIN_FONT_NAMES]


def ensure_windows_zh_precondition() -> None:
    """
    用途：windows-zh 仅在 Windows 且存在候选字体时放行；否则 quality_precondition_failed。
    二次开发：禁止下载字体；失败不得 skip/pass。
    """
    if os.name != "nt":
        raise PreflightError("quality_precondition_failed", MSG_QUALITY_PRECONDITION)
    for cand in _windows_font_candidates():
        try:
            if cand.exists() and cand.is_file() and not cand.is_symlink():
                return
        except OSError:
            continue
    raise PreflightError("quality_precondition_failed", MSG_QUALITY_PRECONDITION)


def _load_windows_zh_truetype_font() -> Any:
    """
    用途：用系统候选字体经 ImageFont.truetype 加载；禁止下载字体。
    二次开发：必须真实调用 truetype（测试以此证明中文像素路径）。
    """
    from PIL import ImageFont  # noqa: WPS433

    for cand in _windows_font_candidates():
        try:
            if not cand.exists() or not cand.is_file() or cand.is_symlink():
                continue
            # 显式 truetype：中文短句像素绘制入口
            return ImageFont.truetype(str(cand), size=28)
        except OSError:
            continue
        except Exception:
            continue
    raise PreflightError("quality_precondition_failed", MSG_QUALITY_PRECONDITION)


def _render_windows_zh_page(zh_text: str, ascii_text: str, font: Any) -> Any:
    """
    用途：一页同时绘制中文短句 + ASCII 伴随锚点到像素（无 PDF 文本层）。
    """
    from PIL import Image, ImageDraw  # noqa: WPS433

    probe = Image.new("L", (8, 8), 255)
    draw = ImageDraw.Draw(probe)
    b1 = draw.textbbox((0, 0), zh_text, font=font)
    b2 = draw.textbbox((0, 0), ascii_text, font=font)
    w1 = max(1, b1[2] - b1[0])
    h1 = max(1, b1[3] - b1[1])
    w2 = max(1, b2[2] - b2[0])
    h2 = max(1, b2[3] - b2[1])
    pad_x, pad_y, gap = 16, 16, 8
    width = max(ASCII_PDF_IMG_MIN, min(ASCII_PDF_IMG_MAX, max(w1, w2) + pad_x * 2))
    height = max(
        ASCII_PDF_IMG_MIN,
        min(ASCII_PDF_IMG_MAX, h1 + h2 + gap + pad_y * 2),
    )
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    draw.text((pad_x, pad_y), zh_text, fill=0, font=font)
    draw.text((pad_x, pad_y + h1 + gap), ascii_text, fill=0, font=font)
    extrema = canvas.getextrema()
    if extrema[0] == extrema[1]:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    return canvas


def generate_windows_zh_image_only_pdf(target: Path | str) -> Path:
    """
    用途：windows-zh 专用两页 image-only PDF。
    第 1 页：ZH_SENTENCE_P1 + OCR_P1；第 2 页：ZH_SENTENCE_P2 + OCR_P2。
    仅像素绘制（truetype 系统字体）；禁止文本层/metadata/下载字体。
    """
    out_path = _resolve_pdf_out_path(target, "managed_ocr_scan_zh.pdf")
    font = _load_windows_zh_truetype_font()
    page1 = _render_windows_zh_page(ZH_SENTENCE_P1, OCR_P1, font)
    page2 = _render_windows_zh_page(ZH_SENTENCE_P2, OCR_P2, font)
    _write_lossless_gray_image_pdf([page1, page2], out_path)
    forbidden_name = (OCR_P1, OCR_P2, ZH_SENTENCE_P1, ZH_SENTENCE_P2)
    if any(token in out_path.name for token in forbidden_name):
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    raw = out_path.read_bytes()
    if OCR_P1.encode("ascii") in raw or OCR_P2.encode("ascii") in raw:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    # 中文短句不得以 UTF-8 明文进入 PDF（仅像素）
    if ZH_SENTENCE_P1.encode("utf-8") in raw or ZH_SENTENCE_P2.encode("utf-8") in raw:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    return out_path


# ---------------------------------------------------------------------------
# dry-run / ocr-check
# ---------------------------------------------------------------------------


def run_dry_run(*, manifest_info: Mapping[str, Any], quality_profile: str) -> dict[str, Any]:
    """
    用途：静态 CLI/model/disk 门；零 TEMP、零 Popen、零 HTTP。
    成功：static_ready / ready / didNotRunRealRuntime=true / runtimeVerified=false。
    """
    validate_runtime_ready(manifest_info)
    # 内存校验固定 argv 形态（不启动进程）
    cli = Path(manifest_info["cli_path"])
    placeholder_in = Path("preflight_placeholder_input.pdf")
    placeholder_out = Path("preflight_placeholder_output")
    cmd = build_mineru_command(str(cli), placeholder_in, placeholder_out)
    if (
        len(cmd) < 7
        or cmd[1] != "-p"
        or cmd[3] != "-o"
        or cmd[5] != "-b"
        or cmd[6] != "pipeline"
    ):
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    # dry-run 不要求真实 runtime 目录绑定；仅校验离线标志可构造
    env = build_mineru_env()
    if env.get("MINERU_MODEL_SOURCE") != "local":
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if env.get("HF_HUB_OFFLINE") != "1" or env.get("TRANSFORMERS_OFFLINE") != "1":
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)

    return build_result(
        ok=True,
        mode="dry-run",
        diagnostic_code="static_ready",
        message=MSG_STATIC_READY,
        runtime_verified=False,
        did_not_run_real_runtime=True,
        quality_profile=quality_profile,
        status="ready",
    )


def _assert_markers_in_order(markdown: str, quality_profile: str = "ascii") -> None:
    """
    用途：唯一 Markdown 按 profile 冻结顺序命中标记。
    ascii：P1 ASCII → P2 ASCII。
    windows-zh：P1 中文 → P1 ASCII → P2 中文 → P2 ASCII（ASCII-only 固定 ocr_marker_missing）。
    """
    if not isinstance(markdown, str) or not markdown:
        raise PreflightError("ocr_marker_missing", MSG_OCR_MARKER_MISSING)
    if quality_profile == "windows-zh":
        tokens = (ZH_SENTENCE_P1, OCR_P1, ZH_SENTENCE_P2, OCR_P2)
    else:
        tokens = (OCR_P1, OCR_P2)
    cursor = -1
    for token in tokens:
        pos = markdown.find(token)
        if pos < 0 or pos <= cursor:
            raise PreflightError("ocr_marker_missing", MSG_OCR_MARKER_MISSING)
        cursor = pos


def run_ocr_check(*, manifest_info: Mapping[str, Any], quality_profile: str) -> dict[str, Any]:
    """
    用途：TEMP 内生成 image-only PDF → 固定 argv 跑 manifest CLI → 唯一 Markdown 标记门。
    二次开发：成功清理 TEMP；失败同样清理；不 HTTP/不票据。
    Q10：ascii / windows-zh 分流调用各自生成器；中文成功不得宣称真实中文质量。
    """
    validate_runtime_ready(manifest_info)
    if quality_profile == "windows-zh":
        ensure_windows_zh_precondition()
    elif quality_profile != "ascii":
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)

    cli = str(Path(manifest_info["cli_path"]))
    timeout = float(
        PARSER_TIMEOUT_SECONDS
        if PARSER_TIMEOUT_SECONDS is not None
        else OCR_TIMEOUT_SECONDS
    )
    # 与 helper 超时对齐：优先使用本模块可 patch 常量
    timeout_int = max(1, int(timeout)) if timeout >= 1 else timeout

    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
        root = Path(tmp)
        # profile 分流：windows-zh 必须走中文生成器（测试按名称追踪调用）
        if quality_profile == "windows-zh":
            pdf_path = generate_windows_zh_image_only_pdf(root)
        else:
            pdf_path = generate_ascii_image_only_pdf(root)
        out_dir = root / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 跑 MinerU：cwd/可写根绑定 output_dir（由 helper 保证）
        try:
            run_mineru_process(
                cli,
                pdf_path,
                out_dir,
                timeout_seconds=timeout_int,  # type: ignore[arg-type]
            )
        except HelperError:
            raise
        except KeyboardInterrupt:
            raise PreflightError("interrupted", MSG_INTERRUPTED)
        markdown = find_and_read_markdown(out_dir)
        _assert_markers_in_order(markdown, quality_profile)

    return build_result(
        ok=True,
        mode="ocr-check",
        diagnostic_code="ocr_passed",
        message=MSG_OCR_PASSED,
        runtime_verified=True,
        did_not_run_real_runtime=False,
        quality_profile=quality_profile,
        status="passed",
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _probe_mode_from_argv(raw: Sequence[str]) -> str:
    """
    用途：在 argparse 失败前从 argv 探测可判定 mode。
    仅 --dry-run → dry-run；仅 --ocr-check → ocr-check；缺/双选/歧义 → dry-run。
    """
    has_dry = False
    has_ocr = False
    for token in raw:
        if token == "--dry-run":
            has_dry = True
        elif token == "--ocr-check":
            has_ocr = True
    if has_dry and not has_ocr:
        return "dry-run"
    if has_ocr and not has_dry:
        return "ocr-check"
    return "dry-run"


def _probe_quality_from_argv(raw: Sequence[str]) -> str:
    """用途：从 argv 探测 --quality-profile；非法/缺失固定 ascii。"""
    for i, token in enumerate(raw):
        if token == "--quality-profile" and i + 1 < len(raw):
            val = str(raw[i + 1])
            if val in ALLOWED_QUALITY:
                return val
            return "ascii"
        if token.startswith("--quality-profile="):
            val = token.split("=", 1)[1]
            if val in ALLOWED_QUALITY:
                return val
            return "ascii"
    return "ascii"


def main(argv: Sequence[str] | None = None) -> int:
    """
    用途：最薄 CLI；所有结局输出唯一九键 JSON 与契约退出码。
    Q11：任何错误路径 mode 均属于 dry-run|ocr-check；缺模式固定 dry-run fallback。
    """
    raw = list(argv) if argv is not None else list(sys.argv)
    # 尽早探测：argparse 未知参数（如 --executable）抛错时仍保留可判定 mode
    mode = _probe_mode_from_argv(raw)
    quality_profile = _probe_quality_from_argv(raw)
    try:
        args = parse_args(raw)
        # 成功解析后以校验结果为准
        raw_q = str(getattr(args, "quality_profile", "ascii") or "ascii")
        if raw_q in ALLOWED_QUALITY:
            quality_profile = raw_q
        if bool(getattr(args, "dry_run", False)):
            mode = "dry-run"
        elif bool(getattr(args, "ocr_check", False)):
            mode = "ocr-check"

        mode, quality_profile, manifest_path = validate_parsed_args(args)
        # windows-zh 前置必须在 load_manifest/Path.resolve 之前：
        # 测试在 Windows 上会 mock os.name=posix，此时 resolve 会抛 UnsupportedOperation。
        if mode == "ocr-check" and quality_profile == "windows-zh":
            ensure_windows_zh_precondition()

        manifest_info = load_manifest(manifest_path)

        if mode == "dry-run":
            result = run_dry_run(
                manifest_info=manifest_info, quality_profile=quality_profile
            )
        else:
            result = run_ocr_check(
                manifest_info=manifest_info, quality_profile=quality_profile
            )

        emit_json(result)
        return int(DIAG_EXIT.get(str(result["diagnosticCode"]), 1))

    except PreflightError as exc:
        diag = exc.diagnostic_code
        result = build_result(
            ok=False,
            mode=mode,
            diagnostic_code=diag,
            message=exc.message,
            runtime_verified=False,
            did_not_run_real_runtime=True,
            quality_profile=quality_profile,
        )
        emit_json(result)
        return int(DIAG_EXIT.get(diag, 1))

    except HelperError as exc:
        diag, message = map_helper_error(exc)
        result = build_result(
            ok=False,
            mode=mode,
            diagnostic_code=diag,
            message=message,
            runtime_verified=False,
            did_not_run_real_runtime=True,
            quality_profile=quality_profile,
        )
        emit_json(result)
        return int(DIAG_EXIT.get(diag, 1))

    except KeyboardInterrupt:
        result = build_result(
            ok=False,
            mode=mode,
            diagnostic_code="interrupted",
            message=MSG_INTERRUPTED,
            runtime_verified=False,
            did_not_run_real_runtime=True,
            quality_profile=quality_profile,
        )
        emit_json(result)
        return int(DIAG_EXIT["interrupted"])

    except Exception:
        result = build_result(
            ok=False,
            mode=mode,
            diagnostic_code="internal_error",
            message=MSG_INTERNAL_ERROR,
            runtime_verified=False,
            did_not_run_real_runtime=True,
            quality_profile=quality_profile,
        )
        emit_json(result)
        return int(DIAG_EXIT["internal_error"])


if __name__ == "__main__":
    raise SystemExit(main())
