# -*- coding: utf-8 -*-
"""
模块：V1-C 本机解析运行时预检
用途：对 MinerU 默认路径与 Docling 可选路径做零回调 dry-run 静态检查，或用户显式选择时用合成 DOCX 做真值门。
对接：docs/v1c-local-parser-runtime-preflight-contract.md；复用 P8D/P8E resolve/command/env/run/Markdown/artifacts 原语。
二次开发：仅标准库；禁止 run_pipeline/post_callback/getpass/票据/Origin；禁止自动安装下载；JSON 仅六键且不得含路径/正文/异常类名。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from docling_callback_helper import (
    MSG_ERR_ARTIFACTS,
    MSG_ERR_DOCLING_FAILED,
    MSG_ERR_DOCLING_MISSING,
    MSG_ERR_DOCLING_TIMEOUT,
    build_docling_command,
    build_docling_env,
    map_extension_to_from,
    resolve_docling_executable,
    run_docling_process,
    validate_artifacts_path,
)
from mineru_callback_helper import (
    HelperError,
    MSG_ERR_INTERRUPTED,
    MSG_ERR_MARKDOWN,
    MSG_ERR_MINERU_FAILED,
    MSG_ERR_MINERU_MISSING,
    MSG_ERR_MINERU_TIMEOUT,
    MSG_ERR_USAGE,
    build_mineru_command,
    build_mineru_env,
    find_and_read_markdown,
    resolve_mineru_executable,
    run_mineru_process,
)

# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

SAMPLE_MARKER = "SYNTH_BID_SAMPLE_V1"
TEMP_PREFIX = "biaoshu-parser-preflight-"
ALLOWED_ENGINES = frozenset({"mineru", "docling"})
JSON_KEYS = ("ok", "engine", "mode", "diagnosticCode", "message", "runtimeVerified")

# 可测超时（秒）；synthetic-check 传给助手 run_*_process
PARSER_TIMEOUT_SECONDS: float = 30 * 60

# 固定中文消息（不得含路径/锚点字面量/异常类名）
MSG_STATIC_READY = "静态检查通过，尚未运行解析器"
MSG_SYNTHETIC_PASSED = "合成样本解析通过并保留预期标记"
MSG_ARGUMENT_INVALID = "参数无效"
MSG_CLI_MISSING = "未找到解析器命令或安全类型不合格"
MSG_ARTIFACTS_INVALID = "模型目录无效"
MSG_PARSER_FAILED = "解析器运行失败"
MSG_PARSER_TIMEOUT = "解析器运行超时"
MSG_OUTPUT_INVALID = "未找到合法的唯一 Markdown 输出"
MSG_SAMPLE_MARKER_MISSING = "合成输出未包含预期标记"
MSG_INTERRUPTED = "操作已中断"
MSG_INTERNAL_ERROR = "预检内部错误"

# 诊断码 → 退出码
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

# HelperError.message 精确映射（禁止模糊子串）
_HELPER_MSG_TO_DIAG: dict[str, str] = {
    MSG_ERR_USAGE: "argument_invalid",
    MSG_ERR_MINERU_MISSING: "cli_missing",
    MSG_ERR_DOCLING_MISSING: "cli_missing",
    MSG_ERR_ARTIFACTS: "artifacts_invalid",
    MSG_ERR_MINERU_FAILED: "parser_failed",
    MSG_ERR_DOCLING_FAILED: "parser_failed",
    MSG_ERR_MINERU_TIMEOUT: "parser_timeout",
    MSG_ERR_DOCLING_TIMEOUT: "parser_timeout",
    MSG_ERR_MARKDOWN: "output_invalid",
    MSG_ERR_INTERRUPTED: "interrupted",
}

_DIAG_DEFAULT_MESSAGE: dict[str, str] = {
    "static_ready": MSG_STATIC_READY,
    "synthetic_passed": MSG_SYNTHETIC_PASSED,
    "argument_invalid": MSG_ARGUMENT_INVALID,
    "cli_missing": MSG_CLI_MISSING,
    "artifacts_invalid": MSG_ARTIFACTS_INVALID,
    "parser_failed": MSG_PARSER_FAILED,
    "parser_timeout": MSG_PARSER_TIMEOUT,
    "output_invalid": MSG_OUTPUT_INVALID,
    "sample_marker_missing": MSG_SAMPLE_MARKER_MISSING,
    "interrupted": MSG_INTERRUPTED,
    "internal_error": MSG_INTERNAL_ERROR,
}


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
# JSON 与诊断映射
# ---------------------------------------------------------------------------


def build_result(
    *,
    ok: bool,
    engine: str,
    mode: str,
    diagnostic_code: str,
    message: str,
    runtime_verified: bool,
) -> dict[str, Any]:
    """用途：构造契约六键 JSON 对象（字段顺序固定）。"""
    return {
        "ok": bool(ok),
        "engine": engine if engine in ALLOWED_ENGINES else "mineru",
        "mode": mode if mode in ("dry-run", "synthetic-check") else mode or "",
        "diagnosticCode": diagnostic_code,
        "message": message,
        "runtimeVerified": bool(runtime_verified),
    }


def emit_json(payload: Mapping[str, Any]) -> None:
    """用途：stdout 仅输出一个 JSON 对象，不写 stderr 日志。"""
    # 仅输出契约六键，拒绝额外字段
    ordered = {k: payload[k] for k in JSON_KEYS}
    sys.stdout.write(json.dumps(ordered, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def map_helper_error(exc: HelperError) -> tuple[str, str]:
    """
    用途：仅按导入的固定 MSG_* 常量精确映射 HelperError。
    二次开发：禁止模糊匹配或回显异常原文；未知消息固定 internal_error。
    """
    msg = getattr(exc, "message", None)
    if not isinstance(msg, str):
        return "internal_error", MSG_INTERNAL_ERROR
    diag = _HELPER_MSG_TO_DIAG.get(msg)
    if diag is None:
        return "internal_error", MSG_INTERNAL_ERROR
    # 对外消息使用预检固定中文，不回传助手可能变化的文案细节以外的上下文
    return diag, _DIAG_DEFAULT_MESSAGE[diag]


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    用途：静默解析 --engine/--dry-run/--synthetic-check/--artifacts-path。
    二次开发：engine 不在 argparse choices 中校验，避免回显非法值。
    """
    parser = QuietArgumentParser(
        prog="runtime_preflight",
        description="本机解析运行时预检（V1-C）",
    )
    parser.add_argument(
        "--engine",
        default="mineru",
        help="解析引擎：mineru（默认）或 docling",
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="静态检查：不启动解析器、不生成样本",
    )
    mode_group.add_argument(
        "--synthetic-check",
        action="store_true",
        help="合成样本真值门：TEMP 内生成 DOCX 并离线运行",
    )
    parser.add_argument(
        "--artifacts-path",
        default=None,
        help="Docling 本地模型目录（仅 docling 需要）",
    )
    raw = list(argv) if argv is not None else sys.argv
    return parser.parse_args(raw[1:] if raw else [])


def validate_parsed_args(args: argparse.Namespace) -> tuple[str, str, str | None]:
    """
    用途：校验 engine 枚举、模式互斥已由 argparse 保证后的 Docling/MinerU artifacts 规则。
    返回：(engine, mode, artifacts_path_or_none)
    二次开发：engine 不得 strip/规范化；仅精确 mineru|docling 放行。
    """
    engine = str(getattr(args, "engine", "") or "")
    if engine not in ALLOWED_ENGINES:
        # 不把用户原始 engine 写入异常；空白/大小写变体一律 argument_invalid
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)

    if bool(getattr(args, "dry_run", False)):
        mode = "dry-run"
    elif bool(getattr(args, "synthetic_check", False)):
        mode = "synthetic-check"
    else:
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)

    artifacts = getattr(args, "artifacts_path", None)
    if engine == "docling":
        if artifacts is None or not str(artifacts).strip():
            raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
        return engine, mode, str(artifacts)
    # mineru：禁止出现 artifacts
    if artifacts is not None:
        raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
    return engine, mode, None


# ---------------------------------------------------------------------------
# 合成 DOCX
# ---------------------------------------------------------------------------


def generate_synthetic_docx(root: Path) -> Path:
    """
    用途：在 root 下用标准库 zipfile 生成最小合法 DOCX，正文含固定锚点一次。
    对接：synthetic-check 真值门；不得嵌入绝对路径或业务样本。
    """
    if not isinstance(root, Path):
        root = Path(root)
    out_path = root / "synth_sample.docx"

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    # 锚点精确一次；不写入任何路径或业务字样
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{SAMPLE_MARKER}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
    return out_path


# ---------------------------------------------------------------------------
# dry-run / synthetic-check
# ---------------------------------------------------------------------------


def _validate_mineru_command_shape(cmd: Sequence[str]) -> None:
    """用途：内存校验 MinerU 固定 argv 形态，不打印命令。"""
    if (
        len(cmd) < 7
        or cmd[1] != "-p"
        or cmd[3] != "-o"
        or cmd[5] != "-b"
        or cmd[6] != "pipeline"
    ):
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)


def _validate_docling_command_shape(cmd: Sequence[str]) -> None:
    """用途：内存校验 Docling 固定 argv 关键形态，不打印命令。"""
    if len(cmd) < 3 or cmd[1] != "convert":
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if "--from" not in cmd or "docx" not in cmd:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if "--to" not in cmd or "md" not in cmd:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if "--output" not in cmd:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if "--no-enable-remote-services" not in cmd:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    if "--no-allow-external-plugins" not in cmd:
        raise PreflightError("internal_error", MSG_INTERNAL_ERROR)


def run_dry_run(*, engine: str, artifacts_path: str | None) -> dict[str, Any]:
    """
    用途：静态 resolve/config/内存命令形态；物理零 TEMP/样本/进程/票据/HTTP。
    成功：ok=true、runtimeVerified=false、diagnosticCode=static_ready。
    """
    # 占位路径仅用于内存构 argv；不创建、不读写、不打印
    placeholder_in = Path("preflight_placeholder_input.docx")
    placeholder_out = Path("preflight_placeholder_output")

    if engine == "mineru":
        exe = resolve_mineru_executable()
        cmd = build_mineru_command(exe, placeholder_in, placeholder_out)
        _validate_mineru_command_shape(cmd)
        env = build_mineru_env()
        if env.get("MINERU_MODEL_SOURCE") != "local":
            raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
        if env.get("HF_HUB_OFFLINE") != "1" or env.get("TRANSFORMERS_OFFLINE") != "1":
            raise PreflightError("internal_error", MSG_INTERNAL_ERROR)
    else:
        exe = resolve_docling_executable()
        if artifacts_path is None:
            raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
        safe_artifacts = validate_artifacts_path(artifacts_path)
        cmd = build_docling_command(
            exe,
            placeholder_in,
            placeholder_out,
            safe_artifacts,
            "docx",
        )
        _validate_docling_command_shape(cmd)
        # dry-run 不调用 build_docling_env：其要求真实存在的 runtime 目录

    return build_result(
        ok=True,
        engine=engine,
        mode="dry-run",
        diagnostic_code="static_ready",
        message=MSG_STATIC_READY,
        runtime_verified=False,
    )


def run_synthetic_check(*, engine: str, artifacts_path: str | None) -> dict[str, Any]:
    """
    用途：TEMP 合成 DOCX → 复用离线 runner → 唯一 Markdown 与锚点门 → 全路径清理。
    二次开发：零回调、零票据、零 Origin；输入输出均在 preflight 临时根内。
    """
    timeout = PARSER_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
        root = Path(tmp)
        input_path = generate_synthetic_docx(root)
        out_dir = root / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        if engine == "mineru":
            exe = resolve_mineru_executable()
            run_mineru_process(
                exe,
                input_path,
                out_dir,
                timeout_seconds=timeout,
            )
        else:
            if artifacts_path is None:
                raise PreflightError("argument_invalid", MSG_ARGUMENT_INVALID)
            exe = resolve_docling_executable()
            safe_artifacts = validate_artifacts_path(artifacts_path)
            from_format = map_extension_to_from(input_path.name)
            # 预热 env 构造（校验 runtime 目录），实际环境由 run_docling_process 再建
            _ = build_docling_env(out_dir)
            run_docling_process(
                exe,
                input_path,
                out_dir,
                safe_artifacts,
                from_format,
                timeout_seconds=timeout,
            )

        markdown = find_and_read_markdown(out_dir)
        if SAMPLE_MARKER not in markdown:
            raise PreflightError(
                "sample_marker_missing", MSG_SAMPLE_MARKER_MISSING
            )

    return build_result(
        ok=True,
        engine=engine,
        mode="synthetic-check",
        diagnostic_code="synthetic_passed",
        message=MSG_SYNTHETIC_PASSED,
        runtime_verified=True,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """
    用途：最薄 CLI；所有结局输出唯一六键 JSON 与契约退出码。
    对接：unittest 注入 argv；禁止副作用回显。
    """
    engine = "mineru"
    mode = ""
    try:
        args = parse_args(argv if argv is not None else sys.argv)
        # 失败 JSON 候选 engine：不得 strip；非精确允许成员固定安全默认 mineru
        try:
            raw_engine = str(getattr(args, "engine", "mineru") or "")
            engine = raw_engine if raw_engine in ALLOWED_ENGINES else "mineru"
        except Exception:
            engine = "mineru"
        if bool(getattr(args, "dry_run", False)):
            mode = "dry-run"
        elif bool(getattr(args, "synthetic_check", False)):
            mode = "synthetic-check"

        engine, mode, artifacts_path = validate_parsed_args(args)

        if mode == "dry-run":
            result = run_dry_run(engine=engine, artifacts_path=artifacts_path)
        else:
            result = run_synthetic_check(engine=engine, artifacts_path=artifacts_path)

        emit_json(result)
        return int(DIAG_EXIT.get(str(result["diagnosticCode"]), 1))

    except PreflightError as exc:
        diag = exc.diagnostic_code
        result = build_result(
            ok=False,
            engine=engine if engine in ALLOWED_ENGINES else "mineru",
            mode=mode,
            diagnostic_code=diag,
            message=exc.message,
            runtime_verified=False,
        )
        emit_json(result)
        return int(DIAG_EXIT.get(diag, 1))

    except HelperError as exc:
        diag, message = map_helper_error(exc)
        result = build_result(
            ok=False,
            engine=engine if engine in ALLOWED_ENGINES else "mineru",
            mode=mode,
            diagnostic_code=diag,
            message=message,
            runtime_verified=False,
        )
        emit_json(result)
        return int(DIAG_EXIT.get(diag, 1))

    except KeyboardInterrupt:
        result = build_result(
            ok=False,
            engine=engine if engine in ALLOWED_ENGINES else "mineru",
            mode=mode,
            diagnostic_code="interrupted",
            message=MSG_INTERRUPTED,
            runtime_verified=False,
        )
        emit_json(result)
        return int(DIAG_EXIT["interrupted"])

    except Exception:
        result = build_result(
            ok=False,
            engine=engine if engine in ALLOWED_ENGINES else "mineru",
            mode=mode,
            diagnostic_code="internal_error",
            message=MSG_INTERNAL_ERROR,
            runtime_verified=False,
        )
        emit_json(result)
        return int(DIAG_EXIT["internal_error"])


if __name__ == "__main__":
    raise SystemExit(main())
