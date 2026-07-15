# -*- coding: utf-8 -*-
"""
模块：P8D 本机 MinerU 外置解析助手
用途：用户显式选择本地源文件，离线调用 PATH 中已安装的 mineru，再用 P8C 一次性票据向回环后端回传 Markdown。
对接：docs/p8d-mineru-local-helper-contract.md；POST /api/local-parser/callback；P8C X-Local-Parse-Ticket。
二次开发：仅标准库；票据仅交互 TTY+getpass 且固定 43 字符 URL-safe；Windows 仅 mineru.exe；禁止代理/重定向/自动安装；禁止打印票据/路径/正文/taskId。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# 契约常量
# ---------------------------------------------------------------------------

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_MARKDOWN_CODEPOINTS = 1_000_000
MAX_JSON_BODY_BYTES = 2 * 1024 * 1024
MAX_FILENAME_CODEPOINTS = 255
MAX_CALLBACK_RESPONSE_BYTES = 64 * 1024
# MinerU 输出树目录项+文件项合计上限，防止超大输出树拖垮扫描
MAX_OUTPUT_ENTRIES = 4096
MINERU_TIMEOUT_SECONDS = 30 * 60
MINERU_TERMINATE_WAIT_SECONDS = 5

# P8C secrets.token_urlsafe(32) 的实际字符长度与字符集
TICKET_LENGTH = 43
TICKET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

ALLOWED_EXTENSIONS = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".pptx", ".xlsx"}
)

ENV_WHITELIST = frozenset(
    {
        "PATH",
        "SystemRoot",
        "WINDIR",
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
)

DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000"
CALLBACK_PATH = "/api/local-parser/callback"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# 固定中文输出（不得拼接敏感信息）
MSG_SUCCESS = "本地解析回传成功"
MSG_ERR_INPUT = "输入文件无效"
MSG_ERR_MINERU_MISSING = "未找到 MinerU 命令，请先按官方文档完成安装与模型准备"
MSG_ERR_MINERU_FAILED = "MinerU 解析失败"
MSG_ERR_MINERU_TIMEOUT = "MinerU 解析超时"
MSG_ERR_INTERRUPTED = "操作已中断"
MSG_ERR_MARKDOWN = "未找到合法的唯一 Markdown 输出"
MSG_ERR_ORIGIN = "后端地址无效，仅允许本机回环地址"
MSG_ERR_TICKET = "回传票据无效"
MSG_ERR_CALLBACK = "回传失败"
MSG_ERR_CALLBACK_RESPONSE = "回传响应无效"
MSG_ERR_USAGE = "参数无效"


class HelperError(Exception):
    """用途：固定中文失败信息 + 非零退出码，禁止携带敏感上下文。"""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# 输入与 Origin
# ---------------------------------------------------------------------------


def is_allowed_extension(filename: str) -> bool:
    """用途：大小写不敏感校验源文件扩展名。"""
    name = Path(filename).name
    if "." not in name:
        return False
    ext = "." + name.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_EXTENSIONS


def validate_input_file(path_str: str) -> Path:
    """
    用途：校验 --input 为已存在普通文件、非符号链接、非空、≤50MiB、扩展名白名单。
    二次开发：拒绝目录/MIME 猜测/URL；错误信息不得包含绝对路径。
    """
    if not path_str or not str(path_str).strip():
        raise HelperError(MSG_ERR_INPUT)
    path = Path(path_str)
    try:
        if path.is_symlink():
            raise HelperError(MSG_ERR_INPUT)
        if not path.exists() or not path.is_file():
            raise HelperError(MSG_ERR_INPUT)
        # 解析后仍须是普通文件
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_file():
            raise HelperError(MSG_ERR_INPUT)
        size = resolved.stat().st_size
        if size <= 0 or size > MAX_INPUT_BYTES:
            raise HelperError(MSG_ERR_INPUT)
        if not is_allowed_extension(resolved.name):
            raise HelperError(MSG_ERR_INPUT)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_INPUT) from exc
    return resolved


def normalize_backend_origin(raw: str | None) -> str:
    """
    用途：归一化后端 Origin；仅 http(s)+回环主机，禁止凭据/路径/查询/fragment。
    对接：默认 http://127.0.0.1:8000。非法/超范围端口统一 MSG_ERR_ORIGIN。
    """
    text = DEFAULT_BACKEND_ORIGIN if raw is None else str(raw).strip()
    if not text:
        raise HelperError(MSG_ERR_ORIGIN)
    try:
        parsed = urlparse(text)
    except Exception as exc:
        raise HelperError(MSG_ERR_ORIGIN) from exc
    if parsed.scheme not in ("http", "https"):
        raise HelperError(MSG_ERR_ORIGIN)
    if parsed.username is not None or parsed.password is not None:
        raise HelperError(MSG_ERR_ORIGIN)
    if parsed.path not in ("", "/"):
        raise HelperError(MSG_ERR_ORIGIN)
    if parsed.query or parsed.fragment:
        raise HelperError(MSG_ERR_ORIGIN)
    host = parsed.hostname
    if host is None or host.lower() not in LOOPBACK_HOSTS:
        raise HelperError(MSG_ERR_ORIGIN)
    host = host.lower()
    # parsed.port 对非法/超范围端口抛 ValueError，必须统一为 MSG_ERR_ORIGIN
    try:
        port = parsed.port
    except ValueError as exc:
        raise HelperError(MSG_ERR_ORIGIN) from exc
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        port_int = int(port)
    except (TypeError, ValueError) as exc:
        raise HelperError(MSG_ERR_ORIGIN) from exc
    if not (1 <= port_int <= 65535):
        raise HelperError(MSG_ERR_ORIGIN)
    if host == "::1":
        netloc = f"[{host}]:{port_int}"
    else:
        netloc = f"{host}:{port_int}"
    return f"{parsed.scheme}://{netloc}"


def validate_filename_basename(name: str) -> str:
    """用途：对齐 P8C filename 1–255 码点及 CR/LF/NUL/斜杠禁令。"""
    if not isinstance(name, str):
        raise HelperError(MSG_ERR_INPUT)
    base = name.strip()
    if not base or len(base) > MAX_FILENAME_CODEPOINTS:
        raise HelperError(MSG_ERR_INPUT)
    for ch in ("\r", "\n", "\x00", "/", "\\"):
        if ch in base:
            raise HelperError(MSG_ERR_INPUT)
    return base


def validate_ticket(ticket: str) -> str:
    """
    用途：校验 P8C secrets.token_urlsafe(32) 实际格式（43 个 ASCII URL-safe）。
    二次开发：拒绝 CRLF、超长、空白与任意 header 注入字符；错误不得回显票据。
    """
    if not isinstance(ticket, str):
        raise HelperError(MSG_ERR_TICKET)
    # 不允许首尾空白“洗成”合法长度；也不允许内部空白/控制字符
    if ticket != ticket.strip():
        raise HelperError(MSG_ERR_TICKET)
    if len(ticket) != TICKET_LENGTH:
        raise HelperError(MSG_ERR_TICKET)
    if TICKET_PATTERN.fullmatch(ticket) is None:
        raise HelperError(MSG_ERR_TICKET)
    return ticket


# ---------------------------------------------------------------------------
# MinerU 子进程
# ---------------------------------------------------------------------------


def resolve_mineru_executable() -> str:
    """
    用途：仅 shutil.which('mineru')；Windows 只接受 .exe；POSIX 只接受可执行普通非 symlink 文件。
    二次开发：拒绝 .cmd/.bat/.com/无后缀，避免 shell=False 仍经命令解释器的假绿。
    """
    path = shutil.which("mineru")
    if not path:
        raise HelperError(MSG_ERR_MINERU_MISSING)
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            raise HelperError(MSG_ERR_MINERU_MISSING)
        if not candidate.is_file():
            raise HelperError(MSG_ERR_MINERU_MISSING)
        if os.name == "nt":
            # Windows 仅接受 .exe，拒绝 .cmd/.bat/.com/无后缀（避免经命令解释器假绿）
            if candidate.suffix.lower() != ".exe":
                raise HelperError(MSG_ERR_MINERU_MISSING)
        else:
            # POSIX：普通非 symlink 且可执行
            if not os.access(str(candidate), os.X_OK):
                raise HelperError(MSG_ERR_MINERU_MISSING)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_MINERU_MISSING) from exc
    return str(candidate)


def build_mineru_command(mineru: str, input_path: Path, output_dir: Path) -> list[str]:
    """用途：固定 argv：mineru -p <绝对输入> -o <绝对临时目录> -b pipeline。"""
    return [
        mineru,
        "-p",
        str(input_path.resolve()),
        "-o",
        str(output_dir.resolve()),
        "-b",
        "pipeline",
    ]


def build_mineru_env(source_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """
    用途：仅保留环境白名单，并强制本地离线变量；不继承代理/API Key/票据哨兵。
    """
    src = os.environ if source_env is None else source_env
    env: dict[str, str] = {}
    for key in ENV_WHITELIST:
        if key in src and src[key] is not None:
            env[key] = str(src[key])
    env["MINERU_MODEL_SOURCE"] = "local"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def terminate_process(proc: subprocess.Popen[Any] | Any) -> None:
    """用途：先 terminate，短暂等待后必要时 kill。"""
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=MINERU_TERMINATE_WAIT_SECONDS)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=MINERU_TERMINATE_WAIT_SECONDS)
    except Exception:
        pass


def run_mineru_process(
    mineru: str,
    input_path: Path,
    output_dir: Path,
    *,
    timeout_seconds: int | None = None,
) -> None:
    """
    用途：shell=False 参数数组启动 MinerU；丢弃 stdout/stderr；超时/中断终止进程。
    """
    cmd = build_mineru_command(mineru, input_path, output_dir)
    env = build_mineru_env()
    timeout = MINERU_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        proc = subprocess.Popen(
            cmd,
            shell=False,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HelperError(MSG_ERR_MINERU_FAILED) from exc

    try:
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process(proc)
            raise HelperError(MSG_ERR_MINERU_TIMEOUT) from exc
        except KeyboardInterrupt as exc:
            terminate_process(proc)
            raise HelperError(MSG_ERR_INTERRUPTED) from exc
    except HelperError:
        raise
    except Exception as exc:
        terminate_process(proc)
        raise HelperError(MSG_ERR_MINERU_FAILED) from exc

    if returncode != 0:
        raise HelperError(MSG_ERR_MINERU_FAILED)


# ---------------------------------------------------------------------------
# Markdown 发现与回调
# ---------------------------------------------------------------------------


def _read_markdown_bytes_bounded(resolved: Path) -> str:
    """
    用途：在 stat 校验后以二进制有界读取 Markdown，防止 stat/read TOCTOU 增长与内存 DoS。
    二次开发：禁止 Path.read_text / 无界 read_bytes；超限或非法 UTF-8 固定 MSG_ERR_MARKDOWN。
    """
    try:
        st = resolved.stat()
        size = int(st.st_size)
        if size <= 0 or size > MAX_JSON_BODY_BYTES:
            raise HelperError(MSG_ERR_MARKDOWN)
        # 有界读取 limit+1，防御校验后文件被拉大
        with resolved.open("rb") as fp:
            data = fp.read(MAX_JSON_BODY_BYTES + 1)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc

    if not data or len(data) > MAX_JSON_BODY_BYTES:
        raise HelperError(MSG_ERR_MARKDOWN)
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc


def find_and_read_markdown(temp_root: Path) -> str:
    """
    用途：在临时根内递归恰好发现一个非符号链接 .md，有界读取后校验码点并返回 strip 后正文。
    二次开发：输出树目录项+文件项合计 ≤ MAX_OUTPUT_ENTRIES；第二个 .md 立即失败；
    读取前 stat 校验普通非 symlink、仍在临时根、字节 1..MAX_JSON_BODY_BYTES，再二进制有界读。
    零个/多个/越界/逃逸/超大树均固定 MSG_ERR_MARKDOWN。
    """
    try:
        root = temp_root.resolve()
    except OSError as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc

    found: Path | None = None
    entry_count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            entry_count += len(dirnames) + len(filenames)
            if entry_count > MAX_OUTPUT_ENTRIES:
                raise HelperError(MSG_ERR_MARKDOWN)
            for name in filenames:
                if not name.lower().endswith(".md"):
                    continue
                # 第二个 .md 立即失败，禁止继续扫描整树
                if found is not None:
                    raise HelperError(MSG_ERR_MARKDOWN)
                found = Path(dirpath) / name
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc

    if found is None:
        raise HelperError(MSG_ERR_MARKDOWN)

    md_path = found
    try:
        if md_path.is_symlink():
            raise HelperError(MSG_ERR_MARKDOWN)
        resolved = md_path.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_file():
            raise HelperError(MSG_ERR_MARKDOWN)
        # 必须位于临时根内，防止目录逃逸
        resolved.relative_to(root)
        text = _read_markdown_bytes_bounded(resolved)
    except HelperError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc

    stripped = text.strip()
    if not stripped or len(stripped) > MAX_MARKDOWN_CODEPOINTS:
        raise HelperError(MSG_ERR_MARKDOWN)
    return stripped


def build_callback_body(markdown: str, filename: str) -> bytes:
    """用途：构造精确 JSON body，并强制 UTF-8 字节 ≤ 2 MiB。"""
    payload = {
        "markdown": markdown,
        "source": "mineru",
        "filename": filename,
    }
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise HelperError(MSG_ERR_MARKDOWN) from exc
    if len(body) > MAX_JSON_BODY_BYTES:
        raise HelperError(MSG_ERR_MARKDOWN)
    return body


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """用途：禁止跟随任何 3xx，确保票据/正文不会被转发到其他主机。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _build_no_proxy_opener() -> urllib.request.OpenerDirector:
    """用途：标准库 opener，清空代理且不跟随重定向。"""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )


def _read_response_limited(fp: Any, limit: int = MAX_CALLBACK_RESPONSE_BYTES) -> bytes:
    """
    用途：成功响应固定小上限读取（limit+1 检测超限），防止本机内存 DoS。
    """
    try:
        chunk = fp.read(limit + 1)
    except Exception as exc:
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE) from exc
    if chunk is None:
        return b""
    data = bytes(chunk)
    if len(data) > limit:
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE)
    return data


def _close_quietly(fp: Any) -> None:
    """用途：关闭响应体，不读取、不回显。"""
    try:
        if fp is not None:
            fp.close()
    except Exception:
        pass


def post_callback(
    origin: str,
    ticket: str,
    markdown: str,
    filename: str,
    *,
    timeout_seconds: float = 60.0,
) -> None:
    """
    用途：向 <归一化 origin>/api/local-parser/callback 发起一次无代理、无重定向 POST。
    二次开发：入口内再次校验 origin/ticket/filename；零重试；成功响应 ≤64KiB；
    非 2xx/HTTPError 不整包读取 body；成功须 ok=true 且 chars 非负整数、taskId 非空字符串。
    """
    # 防御深度：不依赖 main，内部必须归一化/校验
    safe_origin = normalize_backend_origin(origin)
    safe_ticket = validate_ticket(ticket)
    safe_filename = validate_filename_basename(filename)
    body = build_callback_body(markdown, safe_filename)
    url = f"{safe_origin}{CALLBACK_PATH}"
    headers = {
        "Content-Type": "application/json",
        "X-Local-Parse-Ticket": safe_ticket,
    }
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    opener = _build_no_proxy_opener()
    try:
        with opener.open(request, timeout=timeout_seconds) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status is None or int(status) < 200 or int(status) >= 300:
                _close_quietly(resp)
                raise HelperError(MSG_ERR_CALLBACK)
            raw = _read_response_limited(resp)
    except HelperError:
        raise
    except urllib.error.HTTPError as exc:
        # 3xx 与非 2xx：不得 exc.read() 整包，直接固定失败并关闭
        _close_quietly(exc)
        raise HelperError(MSG_ERR_CALLBACK) from exc
    except urllib.error.URLError as exc:
        raise HelperError(MSG_ERR_CALLBACK) from exc
    except TimeoutError as exc:
        raise HelperError(MSG_ERR_CALLBACK) from exc
    except Exception as exc:
        raise HelperError(MSG_ERR_CALLBACK) from exc

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE) from exc

    if not isinstance(parsed, dict):
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE)
    if parsed.get("ok") is not True:
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE)
    chars = parsed.get("chars")
    task_id = parsed.get("taskId")
    if type(chars) is not int or chars < 0:
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE)
    if not isinstance(task_id, str) or not task_id.strip():
        raise HelperError(MSG_ERR_CALLBACK_RESPONSE)
    # 故意不返回/打印 taskId


def read_ticket_from_getpass() -> str:
    """
    用途：仅在交互终端通过 getpass 读取票据；stdin 非 TTY 时固定失败且不调用 getpass。
    二次开发：禁止 argv/env/管道/文件/剪贴板替代来源。
    """
    try:
        is_tty = sys.stdin.isatty()
    except Exception:
        is_tty = False
    if not is_tty:
        raise HelperError(MSG_ERR_TICKET)
    try:
        raw = getpass.getpass("请输入本地解析回传票据（输入不回显）: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise HelperError(MSG_ERR_TICKET) from exc
    except Exception as exc:
        raise HelperError(MSG_ERR_TICKET) from exc
    return validate_ticket(raw if raw is not None else "")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _QuietArgumentParser(argparse.ArgumentParser):
    """用途：参数错误只抛固定中文，避免 argparse 回显用户参数中的敏感片段。"""

    def error(self, message: str) -> None:  # noqa: A003
        raise HelperError(MSG_ERR_USAGE, exit_code=2)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """用途：仅接受 --input 与可选 --backend-origin。"""
    parser = _QuietArgumentParser(
        prog="mineru_callback_helper",
        description="本机 MinerU 外置解析并回传（P8D）",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="本地源文件路径（单文件）",
    )
    parser.add_argument(
        "--backend-origin",
        default=None,
        help=f"回环后端 Origin，默认 {DEFAULT_BACKEND_ORIGIN}",
    )
    return parser.parse_args(list(argv)[1:] if argv is not None else None)


def run_pipeline(
    *,
    input_path: Path,
    origin: str,
    ticket: str,
    mineru: str | None = None,
) -> None:
    """用途：临时目录内跑 MinerU → 读唯一 Markdown → 单次回调；全程 finally 清理。"""
    exe = mineru if mineru is not None else resolve_mineru_executable()
    filename = validate_filename_basename(input_path.name)
    # 入口再校验，确保 run_pipeline 单独调用也安全
    safe_origin = normalize_backend_origin(origin)
    safe_ticket = validate_ticket(ticket)

    with tempfile.TemporaryDirectory(prefix="biaoshu-mineru-") as tmp:
        out_dir = Path(tmp)
        run_mineru_process(exe, input_path, out_dir)
        markdown = find_and_read_markdown(out_dir)
        post_callback(safe_origin, safe_ticket, markdown, filename)


def main(argv: Sequence[str] | None = None) -> int:
    """用途：最薄 CLI 入口；所有异常转为固定中文与非零退出码。"""
    try:
        args = parse_args(argv if argv is not None else sys.argv)
        input_path = validate_input_file(args.input)
        origin = normalize_backend_origin(args.backend_origin)
        ticket = read_ticket_from_getpass()
        run_pipeline(input_path=input_path, origin=origin, ticket=ticket)
        print(MSG_SUCCESS)
        return 0
    except HelperError as exc:
        print(exc.message, file=sys.stderr)
        return int(exc.exit_code) if exc.exit_code else 1
    except KeyboardInterrupt:
        print(MSG_ERR_INTERRUPTED, file=sys.stderr)
        return 130
    except Exception:
        # 兜底：不回显异常细节
        print(MSG_ERR_CALLBACK, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
