# -*- coding: utf-8 -*-
"""标书 V1 离线备份核心（仅标准库）。

冻结公开测试接口：
- BACKUP_SCHEMA_VERSION
- DATA_COMPATIBILITY_VERSION
- BackupError
- build_source_plan
- assert_services_stopped
- create_offline_backup
- main

安全约定：不读取危险绕过开关；不把绝对路径/主机名/用户名/密钥写入清单。
V1-B：写出严格 v2 manifest（独立数据兼容版本 + 六根四态）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

BACKUP_SCHEMA_VERSION = "biaoshu-offline-backup-v2"
DATA_COMPATIBILITY_VERSION = "biaoshu-data-v1"

# 逻辑根名称（manifest 使用）
LOGICAL_DB = "db"
LOGICAL_UPLOADS = "uploads"
LOGICAL_KNOWLEDGE = "knowledge"
LOGICAL_KNOWLEDGE_CARDS = "knowledge_cards"
LOGICAL_LEGACY_UPLOADS = "legacy_uploads"
LOGICAL_SEMANTIC_MODELS = "semantic_models"

# 固定六根顺序
ALL_LOGICAL_ROOTS: Tuple[str, ...] = (
    LOGICAL_DB,
    LOGICAL_UPLOADS,
    LOGICAL_KNOWLEDGE,
    LOGICAL_KNOWLEDGE_CARDS,
    LOGICAL_LEGACY_UPLOADS,
    LOGICAL_SEMANTIC_MODELS,
)

STATE_PRESENT = "present"
STATE_EMPTY = "empty"
STATE_ABSENT = "absent"
STATE_NOT_INCLUDED = "not_included"

_DEFAULT_PORTS: Tuple[int, ...] = (8000, 5173)
_CHUNK_SIZE = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# 目录遍历时跳过的名称（精确或前缀）——出现在可恢复数据根内时 v2 固定失败
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".agent-collaboration",
    "logs",
    "log",
}
_SKIP_DIR_PREFIXES = ("codex-",)
_SKIP_FILE_NAMES = {".env", ".env.local", ".env.production"}
_SKIP_DB_EXACT = {"biaoshu-e2e.db"}
_SKIP_DB_PREFIXES = ("biaoshu-pytest",)


class BackupError(Exception):
    """离线备份失败（固定中文原因由调用方展示）。"""


def _abspath_no_follow(path: Any) -> Path:
    """规范化为绝对路径，但不 resolve 跟随 junction/symlink。"""
    p = Path(path).expanduser()
    return Path(os.path.abspath(str(p)))


def _resolve_repo_root(repo_root: Any) -> Path:
    root = _abspath_no_follow(repo_root)
    if not root.exists() or not root.is_dir():
        raise BackupError("仓库根目录不存在或不是目录")
    _assert_no_reparse_in_chain(root, "仓库根目录不能是符号链接或重解析点")
    return root


def _is_reparse_or_symlink(path: Path) -> bool:
    """检测符号链接、junction 或其它 reparse point（不跟随）。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))  # type: ignore[attr-defined]
            if attrs != -1 and (attrs & _FILE_ATTRIBUTE_REPARSE_POINT):
                return True
        except Exception:
            pass
    return False


def _path_component_exists(path: Path) -> bool:
    """组件是否存在（不跟随 reparse 目标是否可达）。"""
    try:
        return os.path.lexists(str(path))
    except OSError:
        return False


def _assert_no_reparse_in_chain(
    path: Path,
    message: str = "路径不能包含符号链接或重解析点",
) -> None:
    """检查路径自身及现存祖先组件中的 symlink/junction/reparse。

    逐段拼接现存组件，避免只检查最终叶子而放行中间 junction。
    不存在的尾部组件跳过（创建前目标路径可仅检查已存在父级）。
    """
    raw = _abspath_no_follow(path)
    parts = raw.parts
    if not parts:
        raise BackupError(message)
    # Windows: parts[0] 为 'C:\\'；POSIX: '/'
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if not _path_component_exists(current):
            break
        if _is_reparse_or_symlink(current):
            raise BackupError(message)


def _is_regular_file(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError as exc:
        raise BackupError(f"无法读取源文件属性: {path.name}") from exc
    if stat.S_ISLNK(st.st_mode):
        return False
    return stat.S_ISREG(st.st_mode)


def _dir_nonempty(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False
    except OSError:
        return False


def _should_skip_dir(name: str) -> bool:
    if name in _SKIP_DIR_NAMES:
        return True
    for prefix in _SKIP_DIR_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def _should_skip_file(name: str) -> bool:
    if name in _SKIP_FILE_NAMES:
        return True
    lower = name.lower()
    if lower in _SKIP_DB_EXACT:
        return True
    if lower.endswith(".db"):
        for prefix in _SKIP_DB_PREFIXES:
            if lower.startswith(prefix):
                return True
        # 未知 data 侧 db：仅允许 biaoshu.db 作为日用库；其它 .db 在逻辑根内一律跳过
        if lower != "biaoshu.db":
            # 在 uploads/knowledge 树中若出现 .db，按契约排除未知库
            return True
    if lower.endswith(".log"):
        return True
    return False


def _live_paths(repo_root: Path) -> Dict[str, Path]:
    """固定锚定的六根 live 路径（绝对，不跟随 reparse）。"""
    root = _abspath_no_follow(repo_root)
    return {
        LOGICAL_DB: root / "backend" / "data" / "biaoshu.db",
        LOGICAL_UPLOADS: root / "backend" / "uploads",
        LOGICAL_KNOWLEDGE: root / "backend" / "data" / "knowledge",
        LOGICAL_KNOWLEDGE_CARDS: root / "backend" / "data" / "knowledge_cards",
        LOGICAL_LEGACY_UPLOADS: root / "uploads",
        LOGICAL_SEMANTIC_MODELS: root / "backend" / "data" / "semantic-models",
    }


def build_source_plan(
    repo_root: Any,
    include_semantic_models: bool = False,
) -> List[Dict[str, Any]]:
    """构建固定锚定的源计划（绝对路径在返回值 path 字段，仅运行时使用）。

    每项：logical_root, path(Path), kind("file"|"dir"), required(bool)
    不存在且非必需的项不会进入计划；legacy 仅在非空时进入。
    保持 V1-A 参数与语义兼容；v2 根状态由 create_offline_backup 另行聚合。
    """
    root = _resolve_repo_root(repo_root)
    plan: List[Dict[str, Any]] = []

    db_path = root / "backend" / "data" / "biaoshu.db"
    # A5：必需库路径链（含 backend 祖先）不得含 junction 指向仓外
    _assert_no_reparse_in_chain(db_path, "源目录不能是符号链接或重解析点")
    plan.append(
        {
            "logical_root": LOGICAL_DB,
            "path": db_path,
            "kind": "file",
            "required": True,
        }
    )

    optional_dirs = [
        (LOGICAL_UPLOADS, root / "backend" / "uploads"),
        (LOGICAL_KNOWLEDGE, root / "backend" / "data" / "knowledge"),
        (LOGICAL_KNOWLEDGE_CARDS, root / "backend" / "data" / "knowledge_cards"),
    ]
    for logical, path in optional_dirs:
        if _path_component_exists(path):
            _assert_no_reparse_in_chain(path, "源目录不能是符号链接或重解析点")
        if path.exists() and path.is_dir():
            plan.append(
                {
                    "logical_root": logical,
                    "path": path,
                    "kind": "dir",
                    "required": False,
                }
            )

    legacy = root / "uploads"
    if _path_component_exists(legacy):
        _assert_no_reparse_in_chain(legacy, "源目录不能是符号链接或重解析点")
    if _dir_nonempty(legacy):
        plan.append(
            {
                "logical_root": LOGICAL_LEGACY_UPLOADS,
                "path": legacy,
                "kind": "dir",
                "required": False,
            }
        )

    if include_semantic_models:
        sem = root / "backend" / "data" / "semantic-models"
        if _path_component_exists(sem):
            _assert_no_reparse_in_chain(sem, "源目录不能是符号链接或重解析点")
        if sem.exists() and sem.is_dir():
            plan.append(
                {
                    "logical_root": LOGICAL_SEMANTIC_MODELS,
                    "path": sem,
                    "kind": "dir",
                    "required": False,
                }
            )

    return plan


def _default_port_probe(host: str, port: int) -> bool:
    """返回 True 表示端口可连接（视为仍在监听）。

    仅明确 ConnectionRefused 视为空闲（False）。
    timeout / permission / unreachable 等其它 OS 错误向上抛出，由调用方 fail-closed。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect((host, int(port)))
        return True
    except ConnectionRefusedError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _local_ipv4_probe_hosts() -> List[str]:
    """构造 probe=None 时的本机候选：回环 + 已分配非回环 IPv4。

    仅枚举本机 hostname 的 AF_INET 地址，不构造外部/公网探测目标，不做 DNS 远端查询。
    枚举异常由调用方转为 BackupError。
    """
    hosts: List[str] = ["127.0.0.1"]
    seen = {"127.0.0.1"}
    infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
    for info in infos:
        ip = info[4][0]
        if not ip or ip.startswith("127.") or ip in seen:
            continue
        seen.add(ip)
        hosts.append(ip)
    return hosts


def assert_services_stopped(
    host: str = "127.0.0.1",
    ports: Sequence[int] = _DEFAULT_PORTS,
    probe: Optional[Callable[[str, int], bool]] = None,
) -> None:
    """确认指定端口均未监听；任一占用或不确定错误则失败。

    - 注入 probe(host, port) 时：保持原注入契约，仅按传入 host 与 ports 调用。
    - probe=None 时：host 不再缩窄安全面；对每个端口覆盖 127.0.0.1 与本机已分配
      非回环 IPv4。仅 ConnectionRefused 为空闲；其它探测/枚举异常均为 BackupError。
    """
    busy: List[int] = []

    if probe is not None:
        for port in ports:
            try:
                if probe(host, int(port)):
                    busy.append(int(port))
            except Exception as exc:
                raise BackupError(f"无法探测服务端口 {port}") from exc
    else:
        try:
            candidates = _local_ipv4_probe_hosts()
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError("无法枚举本机地址以确认服务已停止") from exc
        for port in ports:
            port_i = int(port)
            for candidate in candidates:
                try:
                    if _default_port_probe(candidate, port_i):
                        busy.append(port_i)
                        break
                except BackupError:
                    raise
                except Exception as exc:
                    raise BackupError(f"无法探测服务端口 {port_i}") from exc

    if busy:
        ports_text = "、".join(str(p) for p in busy)
        raise BackupError(
            f"服务仍在监听（{ports_text}），请先受控停机后再备份"
        )


def _read_git_head(repo_root: Path) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            return None
        head = (completed.stdout or "").strip()
        return head or None
    except Exception:
        return None


def _ensure_outside_repo_and_sources(
    destination_root: Path,
    repo_root: Path,
    source_paths: Iterable[Path],
) -> None:
    dest = _abspath_no_follow(destination_root)
    repo = _abspath_no_follow(repo_root)

    # A5：目标及现存祖先组件不得含 reparse/junction
    _assert_no_reparse_in_chain(dest, "目标根不能是符号链接或重解析点")

    # 目标根不得位于仓库内
    try:
        dest.relative_to(repo)
        raise BackupError("目标根必须位于仓库外")
    except ValueError:
        pass

    # 目标根不得位于任一源目录内
    for src in source_paths:
        src_abs = _abspath_no_follow(src)
        if not _path_component_exists(src_abs):
            continue
        try:
            dest.relative_to(src_abs)
            raise BackupError("目标根不能位于任一备份源目录内")
        except ValueError:
            pass

    # 拒绝路径逃逸到源/仓库的奇异构造（再比字符串规范化）
    dest_s = os.path.normcase(str(dest))
    repo_s = os.path.normcase(str(repo))
    if dest_s == repo_s or dest_s.startswith(repo_s + os.sep):
        raise BackupError("目标根必须位于仓库外")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _copy_file_verified(src: Path, dst: Path) -> Tuple[int, str]:
    """复制普通文件并校验 size/mtime 与 stream/source/destination 三者 SHA-256。

    A4：复制流哈希、复制后完整重算源哈希、副本哈希必须三者相等；
    同大小且恢复 mtime 的内容篡改也会因源复算哈希不一致而失败。
    """
    _assert_no_reparse_in_chain(src, "拒绝备份符号链接、重解析点或非普通文件")
    if _is_reparse_or_symlink(src) or not _is_regular_file(src):
        raise BackupError("拒绝备份符号链接、重解析点或非普通文件")

    st_before = src.lstat()
    if not stat.S_ISREG(st_before.st_mode):
        raise BackupError("拒绝备份非普通文件")

    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    bytes_written = 0
    with src.open("rb") as rf, dst.open("wb") as wf:
        while True:
            chunk = rf.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
            wf.write(chunk)
            bytes_written += len(chunk)

    st_after = src.lstat()
    if (
        st_after.st_size != st_before.st_size
        or getattr(st_after, "st_mtime_ns", st_after.st_mtime)
        != getattr(st_before, "st_mtime_ns", st_before.st_mtime)
    ):
        raise BackupError("源文件在复制期间发生变化")

    if bytes_written != st_before.st_size:
        raise BackupError("复制后大小与源不一致")

    stream_digest = h.hexdigest()
    # 完整重算源 SHA-256（捕获同 size/mtime 恢复下的内容变化）
    source_digest = _sha256_file(src)
    dest_digest = _sha256_file(dst)
    if not (stream_digest == source_digest == dest_digest):
        raise BackupError("源与副本 SHA-256 不一致")
    if dst.stat().st_size != st_before.st_size:
        raise BackupError("副本大小校验失败")

    return st_before.st_size, stream_digest


def _iter_source_files(entry: Dict[str, Any]) -> List[Tuple[str, Path]]:
    """返回 (relative_posix_path, absolute_path) 列表。

    V1-A 兼容：对计划内目录使用宽松跳过规则。
    """
    path: Path = entry["path"]
    kind: str = entry["kind"]
    required: bool = entry["required"]

    if kind == "file":
        if not path.exists():
            if required:
                raise BackupError("必需的日用数据库不存在")
            return []
        if _is_reparse_or_symlink(path) or not _is_regular_file(path):
            raise BackupError("拒绝备份符号链接、重解析点或非普通文件")
        return [("biaoshu.db", path)]

    # directory
    if not path.exists():
        if required:
            raise BackupError(f"必需源目录不存在: {entry['logical_root']}")
        return []
    if not path.is_dir():
        raise BackupError(f"源路径不是目录: {entry['logical_root']}")
    if _is_reparse_or_symlink(path):
        raise BackupError("拒绝遍历符号链接或重解析点源目录")

    results: List[Tuple[str, Path]] = []
    root_resolved = path.resolve(strict=False)

    for dirpath, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        current = Path(dirpath)
        if _is_reparse_or_symlink(current):
            raise BackupError("拒绝遍历符号链接或重解析点")

        # 过滤子目录
        kept_dirs: List[str] = []
        for name in list(dirnames):
            if _should_skip_dir(name):
                continue
            child = current / name
            if _is_reparse_or_symlink(child):
                raise BackupError("拒绝遍历符号链接或重解析点子目录")
            # 路径逃逸检查
            try:
                child.resolve(strict=False).relative_to(root_resolved)
            except ValueError as exc:
                raise BackupError("源路径逃逸出逻辑根") from exc
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            if _should_skip_file(name):
                continue
            fp = current / name
            if _is_reparse_or_symlink(fp):
                raise BackupError("拒绝备份符号链接或重解析点文件")
            if not _is_regular_file(fp):
                raise BackupError("拒绝备份非普通文件")
            try:
                resolved = fp.resolve(strict=False)
                rel = resolved.relative_to(root_resolved)
            except ValueError as exc:
                raise BackupError("源路径逃逸出逻辑根") from exc
            rel_posix = rel.as_posix()
            if rel_posix.startswith("../") or rel_posix == ".." or rel_posix.startswith("/"):
                raise BackupError("源相对路径非法")
            results.append((rel_posix, fp))

    return results


def _iter_dir_files_strict(root_path: Path, logical: str) -> List[Tuple[str, Path]]:
    """严格遍历数据根：被排除/未知/非普通文件一律失败，不得静默跳过。"""
    if not root_path.exists():
        return []
    if not root_path.is_dir():
        raise BackupError(f"源路径不是目录: {logical}")
    if _is_reparse_or_symlink(root_path):
        raise BackupError("拒绝遍历符号链接或重解析点源目录")

    results: List[Tuple[str, Path]] = []
    root_resolved = root_path.resolve(strict=False)
    seen_case: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(
        root_path, topdown=True, followlinks=False
    ):
        current = Path(dirpath)
        if _is_reparse_or_symlink(current):
            raise BackupError("拒绝遍历符号链接或重解析点")

        kept_dirs: List[str] = []
        for name in list(dirnames):
            if _should_skip_dir(name):
                raise BackupError(
                    f"可恢复数据根含被排除或未知内容，无法安全备份: {logical}"
                )
            child = current / name
            if _is_reparse_or_symlink(child):
                raise BackupError("拒绝遍历符号链接或重解析点子目录")
            try:
                child.resolve(strict=False).relative_to(root_resolved)
            except ValueError as exc:
                raise BackupError("源路径逃逸出逻辑根") from exc
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            if _should_skip_file(name):
                raise BackupError(
                    f"可恢复数据根含被排除或未知内容，无法安全备份: {logical}"
                )
            fp = current / name
            if _is_reparse_or_symlink(fp):
                raise BackupError("拒绝备份符号链接或重解析点文件")
            if not _is_regular_file(fp):
                raise BackupError("拒绝备份非普通文件")
            try:
                resolved = fp.resolve(strict=False)
                rel = resolved.relative_to(root_resolved)
            except ValueError as exc:
                raise BackupError("源路径逃逸出逻辑根") from exc
            rel_posix = rel.as_posix()
            if (
                not rel_posix
                or rel_posix in (".", "..")
                or rel_posix.startswith("../")
                or rel_posix.startswith("/")
                or "\\" in rel_posix
                or "\x00" in rel_posix
            ):
                raise BackupError("源相对路径非法")
            # 控制字符
            if any(ord(ch) < 32 for ch in rel_posix):
                raise BackupError("源相对路径非法")
            if rel_posix != rel_posix.strip():
                raise BackupError("源相对路径非法")
            key = rel_posix.casefold()
            if key in seen_case and seen_case[key] != rel_posix:
                raise BackupError("源相对路径存在大小写碰撞")
            seen_case[key] = rel_posix
            results.append((rel_posix, fp))

    results.sort(key=lambda x: (x[0].casefold(), x[0]))
    return results


def _integrity_check_sqlite(db_path: Path) -> None:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError("副本数据库无法打开以进行完整性检查") from exc
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise BackupError("副本数据库完整性检查未通过")
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError("副本数据库完整性检查失败") from exc
    finally:
        conn.close()


def _utc_stamp(now: Optional[datetime]) -> Tuple[str, str]:
    """返回 (iso_utc, dirname_stamp)。"""
    if now is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = now
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = dt.strftime("%Y%m%dT%H%M%SZ")
    return iso, stamp


def _root_state_entry(
    state: str, file_count: int, total_bytes: int
) -> Dict[str, Any]:
    return {
        "state": state,
        "file_count": int(file_count),
        "total_bytes": int(total_bytes),
    }


def _collect_v2_sources(
    repo_root: Path,
    include_semantic_models: bool,
) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, str, Path]]]:
    """聚合六根状态与待复制文件列表。

    返回：
    - roots: logical -> {state, file_count, total_bytes}
    - items: [(logical, rel_posix, abs_path), ...]
    """
    paths = _live_paths(repo_root)
    roots: Dict[str, Dict[str, Any]] = {}
    items: List[Tuple[str, str, Path]] = []

    # db：只能 present，且仅 biaoshu.db
    db_path = paths[LOGICAL_DB]
    _assert_no_reparse_in_chain(db_path, "源目录不能是符号链接或重解析点")
    if not db_path.exists():
        raise BackupError("必需的日用数据库不存在")
    if _is_reparse_or_symlink(db_path) or not _is_regular_file(db_path):
        raise BackupError("拒绝备份符号链接、重解析点或非普通文件")
    # 日用库旁未知 .db 不在 db 逻辑根内（db 根是单文件）；data 目录其它文件不属本根
    items.append((LOGICAL_DB, "biaoshu.db", db_path))
    size = db_path.lstat().st_size
    roots[LOGICAL_DB] = _root_state_entry(STATE_PRESENT, 1, size)

    # 目录型根
    dir_logicals = [
        LOGICAL_UPLOADS,
        LOGICAL_KNOWLEDGE,
        LOGICAL_KNOWLEDGE_CARDS,
        LOGICAL_LEGACY_UPLOADS,
    ]
    for logical in dir_logicals:
        path = paths[logical]
        if _path_component_exists(path):
            _assert_no_reparse_in_chain(path, "源目录不能是符号链接或重解析点")
        if not _path_component_exists(path) or not path.exists():
            roots[logical] = _root_state_entry(STATE_ABSENT, 0, 0)
            continue
        if not path.is_dir():
            raise BackupError(f"源路径不是目录: {logical}")
        pairs = _iter_dir_files_strict(path, logical)
        if not pairs:
            roots[logical] = _root_state_entry(STATE_EMPTY, 0, 0)
            continue
        total = 0
        for rel, abs_path in pairs:
            items.append((logical, rel, abs_path))
            total += abs_path.lstat().st_size
        roots[logical] = _root_state_entry(STATE_PRESENT, len(pairs), total)

    # semantic_models
    logical = LOGICAL_SEMANTIC_MODELS
    sem_path = paths[logical]
    if not include_semantic_models:
        roots[logical] = _root_state_entry(STATE_NOT_INCLUDED, 0, 0)
    else:
        if _path_component_exists(sem_path):
            _assert_no_reparse_in_chain(sem_path, "源目录不能是符号链接或重解析点")
        if not _path_component_exists(sem_path) or not sem_path.exists():
            roots[logical] = _root_state_entry(STATE_ABSENT, 0, 0)
        elif not sem_path.is_dir():
            raise BackupError(f"源路径不是目录: {logical}")
        else:
            pairs = _iter_dir_files_strict(sem_path, logical)
            if not pairs:
                roots[logical] = _root_state_entry(STATE_EMPTY, 0, 0)
            else:
                total = 0
                for rel, abs_path in pairs:
                    items.append((logical, rel, abs_path))
                    total += abs_path.lstat().st_size
                roots[logical] = _root_state_entry(STATE_PRESENT, len(pairs), total)

    # 保证六键齐全
    for name in ALL_LOGICAL_ROOTS:
        if name not in roots:
            raise BackupError("根状态聚合不完整")

    return roots, items


def create_offline_backup(
    repo_root: Any,
    destination_root: Any,
    include_semantic_models: bool = False,
    now: Optional[datetime] = None,
    git_head: Optional[str] = None,
    service_probe: Optional[Callable[[str, int], bool]] = None,
) -> Path:
    """创建离线备份目录，成功返回最终备份 Path；失败清理临时目录并抛 BackupError。"""
    root = _resolve_repo_root(repo_root)
    dest_root = _abspath_no_follow(destination_root)

    # 仍调用 build_source_plan 保持接口与 reparse 预检路径一致
    plan = build_source_plan(root, include_semantic_models=include_semantic_models)
    # 源路径门：六根 live 路径均参与目标冲突检查
    live_map = _live_paths(root)
    source_paths = list(live_map.values())
    # 同时包含 plan 中的路径（兼容）
    source_paths.extend(entry["path"] for entry in plan)
    _ensure_outside_repo_and_sources(dest_root, root, source_paths)

    assert_services_stopped(probe=service_probe)

    # 解析 git_head：测试可注入；生产可自动探测；允许空
    resolved_head: Optional[str]
    if git_head is not None:
        resolved_head = git_head if git_head else None
    else:
        resolved_head = _read_git_head(root)

    created_at, stamp = _utc_stamp(now)
    final_name = f"biaoshu-backup-{stamp}"
    final_path = dest_root / final_name

    if final_path.exists():
        raise BackupError("最终备份目录已存在")

    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError("无法创建目标根目录") from exc

    # 创建目标根后再复查祖先/自身 reparse（避免只检查最终叶子）
    _assert_no_reparse_in_chain(dest_root, "目标根不能是符号链接或重解析点")

    roots_meta, source_items = _collect_v2_sources(
        root, include_semantic_models=include_semantic_models
    )

    tmp_dir: Optional[Path] = None
    try:
        # 同卷临时目录
        tmp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".biaoshu-backup-tmp-{uuid.uuid4().hex[:8]}-",
                dir=str(dest_root),
            )
        )
        _assert_no_reparse_in_chain(tmp_dir, "临时目录异常（重解析点）")

        files_meta: List[Dict[str, Any]] = []

        for logical, rel_posix, src_path in source_items:
            rel_out = Path(logical) / Path(rel_posix)
            rel_s = rel_out.as_posix()
            if (
                rel_out.is_absolute()
                or rel_s.startswith("/")
                or ".." in rel_out.parts
            ):
                raise BackupError("清单相对路径非法")
            dst_path = tmp_dir / rel_out
            try:
                dst_path.resolve(strict=False).relative_to(
                    tmp_dir.resolve(strict=False)
                )
            except ValueError as exc:
                raise BackupError("备份路径逃逸出临时目录") from exc

            size, digest = _copy_file_verified(src_path, dst_path)
            files_meta.append(
                {
                    "logical_root": logical,
                    "relative_path": rel_posix,
                    "size_bytes": size,
                    "sha256": digest,
                }
            )

        # 必需库必须已复制
        db_entries = [m for m in files_meta if m["logical_root"] == LOGICAL_DB]
        if not db_entries:
            raise BackupError("必需的日用数据库不存在")

        copied_db = tmp_dir / LOGICAL_DB / "biaoshu.db"
        if not copied_db.is_file():
            raise BackupError("副本数据库缺失")
        _integrity_check_sqlite(copied_db)

        # 用复制后真实大小/哈希复核 roots 聚合
        agg: Dict[str, Dict[str, int]] = {
            name: {"file_count": 0, "total_bytes": 0} for name in ALL_LOGICAL_ROOTS
        }
        for m in files_meta:
            lr = m["logical_root"]
            if lr not in agg:
                raise BackupError("清单含未知逻辑根")
            agg[lr]["file_count"] += 1
            agg[lr]["total_bytes"] += int(m["size_bytes"])

        for name in ALL_LOGICAL_ROOTS:
            state = roots_meta[name]["state"]
            fc = agg[name]["file_count"]
            tb = agg[name]["total_bytes"]
            if state == STATE_PRESENT:
                if fc < 1 or fc != roots_meta[name]["file_count"] or tb != roots_meta[name]["total_bytes"]:
                    raise BackupError("根状态与文件聚合不一致")
                # 用复制后计数写回
                roots_meta[name] = _root_state_entry(STATE_PRESENT, fc, tb)
            elif state in (STATE_EMPTY, STATE_ABSENT, STATE_NOT_INCLUDED):
                if fc != 0 or tb != 0:
                    raise BackupError("根状态与文件聚合不一致")
                roots_meta[name] = _root_state_entry(state, 0, 0)
            else:
                raise BackupError("非法根状态")

        # db 仅允许 present 且单文件 biaoshu.db
        if roots_meta[LOGICAL_DB]["state"] != STATE_PRESENT:
            raise BackupError("数据库根状态非法")
        if len(db_entries) != 1 or db_entries[0]["relative_path"] != "biaoshu.db":
            raise BackupError("数据库根文件集合非法")

        # 稳定排序
        files_meta.sort(
            key=lambda m: (
                ALL_LOGICAL_ROOTS.index(m["logical_root"])
                if m["logical_root"] in ALL_LOGICAL_ROOTS
                else 99,
                m["relative_path"].casefold(),
                m["relative_path"],
            )
        )

        # 精确六键；sort_keys 保证稳定序列化
        manifest = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "data_compatibility_version": DATA_COMPATIBILITY_VERSION,
            "created_at_utc": created_at,
            "git_head": resolved_head,
            "roots": {name: roots_meta[name] for name in ALL_LOGICAL_ROOTS},
            "files": files_meta,
        }
        if set(manifest.keys()) != {
            "schema_version",
            "data_compatibility_version",
            "created_at_utc",
            "git_head",
            "roots",
            "files",
        }:
            raise BackupError("清单顶层键非法")

        # 敏感门：序列化后不得含绝对盘符路径痕迹（粗检）、以及常见密钥字段名正文
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        lowered = manifest_text.lower()
        for banned in ("api_key", "apikey", "authorization", "cookie", "set-cookie", "bearer "):
            if banned in lowered:
                raise BackupError("清单内容未通过敏感字段门")
        # 不得记录绝对路径（Windows 盘符或 UNC）
        if ":\\" in manifest_text or ":/" in manifest_text or "\\\\" in manifest_text:
            raise BackupError("清单不得包含绝对路径")

        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(manifest_text + "\n", encoding="utf-8")

        # 原子改名
        if final_path.exists():
            raise BackupError("最终备份目录已存在")
        os.replace(str(tmp_dir), str(final_path))
        tmp_dir = None  # 已移交
        return final_path
    except BackupError:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise BackupError(f"离线备份失败: {exc}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="标书离线备份（标准库核心）")
    parser.add_argument("--repo-root", required=True, help="仓库根绝对路径")
    parser.add_argument("--destination-root", required=True, help="目标根（仓库外）")
    parser.add_argument(
        "--include-semantic-models",
        action="store_true",
        help="包含 semantic-models 缓存",
    )
    # 故意不提供跳过端口/完整性/哈希/源变化检查的选项
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        final = create_offline_backup(
            repo_root=args.repo_root,
            destination_root=args.destination_root,
            include_semantic_models=bool(args.include_semantic_models),
        )
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # 成功：只输出最终目录（包装层再附加敏感提示）
    print(str(final))
    return 0


if __name__ == "__main__":
    sys.exit(main())
