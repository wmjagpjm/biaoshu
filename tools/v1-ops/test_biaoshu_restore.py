# -*- coding: utf-8 -*-
"""
模块：V1-B 离线恢复与回滚专项测试（failure-first）
用途：在临时假仓/假 v2 备份/注入 fault_injector 上验证契约 §5–§8；
      生产无 Restore 入口时形成真实业务失败（禁止 skip/xfail/宽泛存在性）。
对接：Restore-Biaoshu.bat、tools/v1-ops/Restore-Biaoshu.ps1、biaoshu_restore.py；
      可选联动 create_offline_backup（v2）；unittest + tempfile。
二次开发：不得杀真实进程、不得占真实 8000/5173、不得读主仓 data/uploads/密钥；
         fault_injector/service_probe/now/git_head 仅测试注入，不得经 bat/PS 转发。
"""

from __future__ import annotations

import hashlib
import io
import json
import locale
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest import mock

# ---------------------------------------------------------------------------
# 路径锚定：本 worktree 仓库根（禁止依赖进程 cwd）
# TEMP failure-first：环境变量 BIAOSHU_V1OPS_DIR 可指向只读拷贝的 A 生产模块目录
# （仅用于对照 A 返修前版本；禁止写入 A/主仓；包装脚本仍锚定本 worktree）
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_V1OPS_DIR = (
    Path(os.environ["BIAOSHU_V1OPS_DIR"]).resolve()
    if os.environ.get("BIAOSHU_V1OPS_DIR")
    else _HERE
)
_RESTORE_PY = _V1OPS_DIR / "biaoshu_restore.py"
_BACKUP_PY = _V1OPS_DIR / "biaoshu_backup.py"
_RESTORE_PS1 = _HERE / "Restore-Biaoshu.ps1"
_RESTORE_BAT = _REPO_ROOT / "Restore-Biaoshu.bat"

_BOM = b"\xef\xbb\xbf"

# 冻结常量（契约 §8）
_EXPECTED_RESTORE_SCHEMA = "biaoshu-offline-backup-v2"
_EXPECTED_RESTORE_DATA_COMPAT = "biaoshu-data-v1"
_EXPECTED_JOURNAL_SCHEMA = "biaoshu-offline-restore-journal-v1"
_EXPECTED_SCHEMA_V1 = "biaoshu-offline-backup-v1"

_FROZEN_RESTORE_API = (
    "RESTORE_SCHEMA_VERSION",
    "RESTORE_DATA_COMPATIBILITY_VERSION",
    "RESTORE_JOURNAL_SCHEMA_VERSION",
    "RestoreError",
    "load_and_validate_backup",
    "build_restore_plan",
    "recover_incomplete_restore",
    "restore_offline_backup",
    "main",
)

_ROOT_ORDER = (
    "db",
    "uploads",
    "knowledge",
    "knowledge_cards",
    "legacy_uploads",
    "semantic_models",
)

_FAKE_API_KEY_MARKER = "sk-fake-v1b-restore-key-DO-NOT-LEAK"

# live 相对路径（绝对锚定仓库根）
_LIVE = {
    "db": Path("backend") / "data" / "biaoshu.db",
    "uploads": Path("backend") / "uploads",
    "knowledge": Path("backend") / "data" / "knowledge",
    "knowledge_cards": Path("backend") / "data" / "knowledge_cards",
    "legacy_uploads": Path("uploads"),
    "semantic_models": Path("backend") / "data" / "semantic-models",
}


# ===========================================================================
# 基础工具
# ===========================================================================


def _require_restore_entries() -> dict[str, Path]:
    """五入口（bat/ps1/py + 依赖 backup 模块）缺失时抛 AssertionError，禁止 skip。"""
    files = {
        "restore_bat": _RESTORE_BAT,
        "restore_ps1": _RESTORE_PS1,
        "restore_py": _RESTORE_PY,
        "backup_py": _BACKUP_PY,
    }
    missing = [n for n, p in files.items() if not p.is_file()]
    if missing:
        raise AssertionError(
            "恢复生产入口缺失（failure-first 预期）："
            + ", ".join(f"{n}={files[n]}" for n in missing)
        )
    return files


def _import_restore_module():
    if not _RESTORE_PY.is_file():
        raise AssertionError(f"生产模块不存在（failure-first 预期）：{_RESTORE_PY}")
    v1ops = str(_V1OPS_DIR)
    # 保证优先从目标 v1-ops 加载，并丢弃缓存（TEMP 对照 A 时必需）
    while v1ops in sys.path:
        sys.path.remove(v1ops)
    sys.path.insert(0, v1ops)
    for name in ("biaoshu_restore", "biaoshu_backup"):
        if name in sys.modules:
            del sys.modules[name]
    import biaoshu_restore as mod  # noqa: WPS433

    return mod


def _import_backup_module():
    if not _BACKUP_PY.is_file():
        raise AssertionError(f"备份模块不存在：{_BACKUP_PY}")
    v1ops = str(_V1OPS_DIR)
    while v1ops in sys.path:
        sys.path.remove(v1ops)
    sys.path.insert(0, v1ops)
    for name in ("biaoshu_restore", "biaoshu_backup"):
        if name in sys.modules:
            del sys.modules[name]
    import biaoshu_backup as mod  # noqa: WPS433

    return mod


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_minimal_sqlite(db_path: Path, *, marker: str = "v1b-restore") -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
            ("marker", marker),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
            ("api_key", _FAKE_API_KEY_MARKER),
        )
        conn.commit()
        row = conn.execute("PRAGMA integrity_check").fetchone()
        assert row is not None and row[0] == "ok"
    finally:
        conn.close()


def _write_corrupt_sqlite(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    header = b"SQLite format 3\x00"
    junk = b"\x00" * 80 + b"CORRUPT-V1B-RESTORE" + os.urandom(64)
    db_path.write_bytes(header + junk)


def _probe_all_free(host: str, port: int) -> bool:
    return False


def _probe_busy(host: str, port: int) -> bool:
    return True


def _probe_partial(busy_ports: set[int]) -> Callable[[str, int], bool]:
    def _inner(host: str, port: int) -> bool:
        return port in busy_ports

    return _inner


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
        if not enc or key in seen or key in {"utf-8", "utf-8-sig"}:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run_powershell(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    input_text: str | None = None,
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
        cwd=str(cwd or _REPO_ROOT),
        capture_output=True,
        timeout=timeout,
        check=False,
        input=input_text.encode("utf-8") if input_text is not None else None,
    )
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=_decode_ps_output(proc.stdout),
        stderr=_decode_ps_output(proc.stderr),
    )


def _read_bom_prefix(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(3)


def _bat_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(_BOM):
        raw = raw[3:]
    return raw.decode("utf-8", errors="replace")


def _create_windows_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        raise AssertionError("本夹具仅在 Windows 上创建 junction")
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        raise AssertionError(f"junction 路径已存在：{link}")
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not (link.exists() or link.is_symlink()):
        raise AssertionError(
            "无法创建 Windows junction（禁止 skip）："
            f" link={link} target={target} code={proc.returncode}"
            f" out={proc.stdout!r} err={proc.stderr!r}"
        )


def _remove_windows_junction(link: Path) -> None:
    if link.exists() or link.is_symlink():
        subprocess.run(
            ["cmd", "/c", "rmdir", str(link)],
            capture_output=True,
            check=False,
        )


def _file_map(root: Path) -> dict[str, tuple[int, str]]:
    """相对 POSIX 路径 → (size, sha256)；仅普通文件，不跟随 reparse。"""
    out: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return out
    if root.is_file():
        return {".": (root.stat().st_size, _sha256_file(root))}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 不进入 reparse
        cleaned = []
        for d in list(dirnames):
            p = Path(dirpath) / d
            try:
                if p.is_symlink() or getattr(p, "is_junction", lambda: False)():
                    continue
            except OSError:
                continue
            cleaned.append(d)
        dirnames[:] = cleaned
        for name in filenames:
            p = Path(dirpath) / name
            try:
                if p.is_symlink():
                    continue
                if not p.is_file():
                    continue
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            st = p.stat()
            out[rel] = (st.st_size, _sha256_file(p))
    return out


def _live_root_maps(repo: Path) -> dict[str, dict[str, tuple[int, str]]]:
    maps: dict[str, dict[str, tuple[int, str]]] = {}
    for name, rel in _LIVE.items():
        p = repo / rel
        if name == "db":
            if p.is_file():
                maps[name] = {"biaoshu.db": (p.stat().st_size, _sha256_file(p))}
            else:
                maps[name] = {}
        else:
            maps[name] = _file_map(p)
    return maps


def _assert_maps_equal(
    tc: unittest.TestCase,
    left: dict[str, dict[str, tuple[int, str]]],
    right: dict[str, dict[str, tuple[int, str]]],
    *,
    msg: str,
) -> None:
    tc.assertEqual(sorted(left.keys()), sorted(right.keys()), msg)
    for k in left:
        tc.assertEqual(left[k], right[k], f"{msg} root={k}")


def _db_marker(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='marker'").fetchone()
        return None if row is None else str(row[0])
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _db_integrity_ok(db_path: Path) -> bool:
    if not db_path.is_file():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _make_fake_repo(
    root: Path,
    *,
    marker: str = "live-v1b",
    include_legacy: bool = True,
    include_semantic: bool = False,
    empty_uploads: bool = False,
    absent_knowledge_cards: bool = False,
) -> dict[str, Path]:
    backend_data = root / "backend" / "data"
    backend_uploads = root / "backend" / "uploads"
    knowledge = backend_data / "knowledge"
    cards = backend_data / "knowledge_cards"
    backend_data.mkdir(parents=True, exist_ok=True)
    if empty_uploads:
        backend_uploads.mkdir(parents=True, exist_ok=True)
    else:
        backend_uploads.mkdir(parents=True, exist_ok=True)
        uf = backend_uploads / "project-a" / "note.txt"
        uf.parent.mkdir(parents=True, exist_ok=True)
        uf.write_text(f"live-upload-{marker}\n", encoding="utf-8")
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "index.json").write_text(
        json.dumps({"version": 1, "marker": marker}), encoding="utf-8"
    )
    if not absent_knowledge_cards:
        cards.mkdir(parents=True, exist_ok=True)
        (cards / "card-001.json").write_text(
            json.dumps({"id": "card-001", "marker": marker}), encoding="utf-8"
        )
    db_path = backend_data / "biaoshu.db"
    _write_minimal_sqlite(db_path, marker=marker)
    # 默认 .env（仅默认键）；测试非默认路径时覆盖
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "backend" / ".env").write_text(
        "# fake env for restore layout checks\n"
        f"# OPENAI_API_KEY={_FAKE_API_KEY_MARKER}\n",
        encoding="utf-8",
    )
    legacy_file = None
    if include_legacy:
        legacy_root = root / "uploads"
        legacy_root.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_root / "legacy-doc.txt"
        legacy_file.write_text(f"legacy-{marker}\n", encoding="utf-8")
    semantic_file = None
    if include_semantic:
        sem = backend_data / "semantic-models"
        sem.mkdir(parents=True, exist_ok=True)
        semantic_file = sem / "model.bin"
        semantic_file.write_bytes(f"SEM-{marker}".encode("utf-8"))
    return {
        "root": root,
        "db": db_path,
        "legacy_file": legacy_file,
        "semantic_file": semantic_file,
    }


def _write_v2_backup_package(
    backup_dir: Path,
    *,
    marker: str = "bak-v2",
    include_legacy: bool = True,
    include_semantic: bool = False,
    semantic_not_included: bool = True,
    schema_version: str = _EXPECTED_RESTORE_SCHEMA,
    data_compat: str = _EXPECTED_RESTORE_DATA_COMPAT,
    corrupt_db: bool = False,
    extra_physical: bool = False,
    tamper_same_size: bool = False,
) -> dict[str, Any]:
    """
    手写严格 v2 备份包（不依赖生产 v2 写出能力），供恢复预检/事务红测。
    semantic_not_included=True 且 include_semantic=False → state not_included。
    """
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir(parents=True)

    files_meta: list[dict[str, Any]] = []
    roots: dict[str, dict[str, Any]] = {}

    def _add_file(logical: str, rel: str, data: bytes) -> None:
        dest = backup_dir / logical / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files_meta.append(
            {
                "logical_root": logical,
                "relative_path": rel,
                "size_bytes": len(data),
                "sha256": _sha256_bytes(data),
            }
        )

    # db
    db_rel = "biaoshu.db"
    db_path = backup_dir / "db" / db_rel
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if corrupt_db:
        _write_corrupt_sqlite(db_path)
        data = db_path.read_bytes()
        files_meta.append(
            {
                "logical_root": "db",
                "relative_path": db_rel,
                "size_bytes": len(data),
                "sha256": _sha256_bytes(data),
            }
        )
    else:
        _write_minimal_sqlite(db_path, marker=marker)
        data = db_path.read_bytes()
        files_meta.append(
            {
                "logical_root": "db",
                "relative_path": db_rel,
                "size_bytes": len(data),
                "sha256": _sha256_bytes(data),
            }
        )
    roots["db"] = {
        "state": "present",
        "file_count": 1,
        "total_bytes": files_meta[-1]["size_bytes"],
    }

    # uploads present
    up_body = f"backup-upload-{marker}\n".encode("utf-8")
    _add_file("uploads", "project-a/note.txt", up_body)
    roots["uploads"] = {
        "state": "present",
        "file_count": 1,
        "total_bytes": len(up_body),
    }

    # knowledge present
    kn_body = json.dumps({"version": 1, "marker": marker}).encode("utf-8")
    _add_file("knowledge", "index.json", kn_body)
    roots["knowledge"] = {
        "state": "present",
        "file_count": 1,
        "total_bytes": len(kn_body),
    }

    # knowledge_cards present
    card_body = json.dumps({"id": "card-001", "marker": marker}).encode("utf-8")
    _add_file("knowledge_cards", "card-001.json", card_body)
    roots["knowledge_cards"] = {
        "state": "present",
        "file_count": 1,
        "total_bytes": len(card_body),
    }

    # legacy
    if include_legacy:
        leg = f"legacy-bak-{marker}\n".encode("utf-8")
        _add_file("legacy_uploads", "legacy-doc.txt", leg)
        roots["legacy_uploads"] = {
            "state": "present",
            "file_count": 1,
            "total_bytes": len(leg),
        }
    else:
        roots["legacy_uploads"] = {"state": "absent", "file_count": 0, "total_bytes": 0}

    # semantic
    if include_semantic:
        sem = f"SEM-BAK-{marker}".encode("utf-8")
        _add_file("semantic_models", "model.bin", sem)
        roots["semantic_models"] = {
            "state": "present",
            "file_count": 1,
            "total_bytes": len(sem),
        }
    elif semantic_not_included:
        roots["semantic_models"] = {
            "state": "not_included",
            "file_count": 0,
            "total_bytes": 0,
        }
    else:
        roots["semantic_models"] = {"state": "absent", "file_count": 0, "total_bytes": 0}

    if tamper_same_size and files_meta:
        # 同大小篡改 uploads 内容，保持 size，改写字节使哈希失效
        target = backup_dir / "uploads" / "project-a" / "note.txt"
        original = target.read_bytes()
        # 翻转最后一个字节，长度不变
        mutated = bytearray(original)
        mutated[-1] = (mutated[-1] ^ 0x5A) & 0xFF
        target.write_bytes(bytes(mutated))
        # manifest 仍保留旧哈希 → 预检应失败

    if extra_physical:
        (backup_dir / "UNKNOWN-EXTRA.txt").write_text("extra\n", encoding="utf-8")

    manifest = {
        "schema_version": schema_version,
        "data_compatibility_version": data_compat,
        "created_at_utc": "2026-07-21T12:00:00Z",
        "git_head": "deadbeefrestore000000000000000000000000",
        "roots": roots,
        "files": files_meta,
    }
    if schema_version == _EXPECTED_SCHEMA_V1:
        # v1 形态：无 data_compat / roots
        manifest = {
            "schema_version": schema_version,
            "created_at_utc": "2026-07-21T12:00:00Z",
            "git_head": None,
            "files": files_meta,
        }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


# 精确 hook token 表（与生产 _fault(point, logical=...) 对齐；禁止模糊 startswith）
_HOOK_POINTS = frozenset(
    {
        "before_pre_backup",
        "after_pre_backup",
        "after_stage",
        "cutover_intent",
        "cutover_before_hold",
        "cutover_after_hold",
        "cutover_before_install",
        "cutover_after_install",
        "before_verify",
        "after_verify",
        "rollback_begin",
        "rollback_before_remove",
        "rollback_before_restore",
        "rollback_after_restore",
        "rollback_end",
        "after_commit",
        "after_cleanup",
    }
)

_JOURNAL_SCHEMA = _EXPECTED_JOURNAL_SCHEMA
_WORK_LOCK_NAME = "restore.lock"
_WORK_JOURNAL_NAME = "journal.json"
_WORK_STAGING_NAME = "staging"
_WORK_HOLD_NAME = "hold"


def _parse_hook_call(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[str | None, str | None]:
    """解析 hook 调用：位置首参 point + 可选 logical（kwargs 或第二位置参）。

    唯一语义；不得回退 phase/root 别名。
    """
    point: str | None = None
    if args:
        point = str(args[0]) if args[0] is not None else None
    elif "point" in kwargs and kwargs["point"] is not None:
        point = str(kwargs["point"])
    logical: str | None = None
    if "logical" in kwargs and kwargs["logical"] is not None:
        logical = str(kwargs["logical"])
    elif len(args) > 1 and args[1] is not None:
        logical = str(args[1])
    return point, logical


def _make_fault_injector(
    fail_at: str,
    *,
    logical: str | None = None,
    fail_times: int = 1,
    mode: str = "raise",
    hits: list[str] | None = None,
) -> Callable[..., None]:
    """
    统一故障注入（B1）：
    - 生产调用形态：fault_injector(point, *, logical=...) 或 fault_injector(point, logical)
    - fail_at 必须是 _HOOK_POINTS 精确 token；logical 可选，精确相等
    - hits 旁路记录 "point" 或 "point:logical"；未命中不得靠“无异常”冒充通过
    - mode: "raise" 抛 INJECTED_FAULT（供被生产边界转为 RestoreError 的路径）；
            "exit" 使用 os._exit 强退（仅崩溃子进程场景）
    """
    if fail_at not in _HOOK_POINTS:
        raise AssertionError(
            f"非法 hook point（禁止模糊/旧 phase 名）：{fail_at!r}；"
            f"允许={sorted(_HOOK_POINTS)}"
        )
    if mode not in ("raise", "exit"):
        raise AssertionError(f"非法 fault mode: {mode!r}")
    state = {"count": 0}
    hit_log: list[str] = hits if hits is not None else []

    def _injector(*args: Any, **kwargs: Any) -> None:
        point, call_logical = _parse_hook_call(args, kwargs)
        if point is None:
            return
        # 旁路：记录每一次真实 hook 命中（含非目标点，便于诊断）
        token = point if call_logical is None else f"{point}:{call_logical}"
        hit_log.append(token)
        if point != fail_at:
            return
        if logical is not None and call_logical != logical:
            return
        state["count"] += 1
        if state["count"] <= fail_times:
            if mode == "exit":
                os._exit(99)
            raise RuntimeError(f"INJECTED_FAULT:{fail_at}" + (f":{logical}" if logical else ""))

    # 供测试断言：目标点真实命中次数
    _injector.hits = hit_log  # type: ignore[attr-defined]
    _injector.target_count = lambda: state["count"]  # type: ignore[attr-defined]
    return _injector


def _assert_hook_hit(inj: Callable[..., None], *, min_count: int = 1) -> None:
    """故障测试收尾：目标点未命中必须失败。"""
    count_fn = getattr(inj, "target_count", None)
    if count_fn is None:
        raise AssertionError("fault injector 缺少 target_count 旁路")
    n = int(count_fn())
    if n < min_count:
        hits = getattr(inj, "hits", [])
        raise AssertionError(
            f"目标 hook 未真实命中（count={n} < {min_count}）；"
            f"实际 hits={list(hits)[:40]!r}"
        )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _legal_op_id(seed: str) -> str:
    """生成彼此可辨且精确匹配 [0-9a-f]{32} 的 operation_id（仅测试合法 journal）。

    禁止 holdmiss/lockown 等非 hex 前缀；A6 恶意路径穿越须另写非法值，不得走此助手。
    """
    dig = hashlib.sha256(f"v1b-op:{seed}".encode("utf-8")).hexdigest()[:32]
    if not re.fullmatch(r"[0-9a-f]{32}", dig):
        raise AssertionError(f"内部生成的 op_id 非法：{dig!r}")
    return dig


def _new_test_journal(
    operation_id: str,
    *,
    phase: str,
    pre_backup_name: str = "biaoshu-backup-test-pre",
    roots: dict[str, dict[str, Any]] | None = None,
    allow_illegal_operation_id: bool = False,
) -> dict[str, Any]:
    """手工构造 journal v1（仅逻辑名，无绝对路径）。

    默认要求 operation_id 为精确 [0-9a-f]{32}，避免“合法 journal”夹具被 journal 门假绿。
    A6 路径穿越等恶意用例须显式 allow_illegal_operation_id=True。

    每根精确六字段：backup_state / intent / result /
    live_existed_before / hold_moved / new_installed。
    历史事实默认：live_existed_before=None、hold_moved=False、new_installed=False；
    调用方须按场景显式覆盖，避免仅因非法形态抛错掩盖目标缺陷。
    """
    if not allow_illegal_operation_id:
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", operation_id
        ):
            raise AssertionError(
                "合法 journal 夹具的 operation_id 必须是精确 [0-9a-f]{32}；"
                f"收到 {operation_id!r}。恶意用例请设 allow_illegal_operation_id=True"
            )
    root_states = {
        name: {
            "backup_state": "present" if name != "semantic_models" else "not_included",
            "intent": None,
            "result": None,
            "live_existed_before": None,
            "hold_moved": False,
            "new_installed": False,
        }
        for name in _ROOT_ORDER
    }
    if roots:
        for k, v in roots.items():
            root_states[k] = {**root_states.get(k, {}), **v}
    return {
        "schema_version": _JOURNAL_SCHEMA,
        "operation_id": operation_id,
        "phase": phase,
        "pre_restore_backup_name": pre_backup_name,
        "roots": root_states,
    }


def _expected_backup_live_maps(bdir: Path, repo: Path) -> dict[str, dict[str, tuple[int, str]]]:
    """根据 v2 备份包与 not_included 规则推导恢复成功后 live 地图（semantic 保留 live）。"""
    man = _read_json(bdir / "manifest.json")
    maps: dict[str, dict[str, tuple[int, str]]] = {}
    for logical in _ROOT_ORDER:
        state = man["roots"][logical]["state"]
        if state == "not_included":
            maps[logical] = _live_root_maps(repo)[logical]
            continue
        if state == "absent":
            maps[logical] = {}
            continue
        if state == "empty":
            maps[logical] = {}
            continue
        # present
        if logical == "db":
            p = bdir / "db" / "biaoshu.db"
            maps[logical] = {"biaoshu.db": (p.stat().st_size, _sha256_file(p))}
        else:
            maps[logical] = _file_map(bdir / logical)
    return maps


def _plant_work_residue(work_root: Path, operation_id: str, *, tag: str = "residue") -> dict[str, Path]:
    """在 work 根种植 staging/hold/trash 残留（模拟清理失败现场）。"""
    staging = work_root / _WORK_STAGING_NAME / "uploads" / f"{tag}.txt"
    hold = work_root / _WORK_HOLD_NAME / operation_id / "uploads" / f"{tag}.txt"
    trash = work_root / "trash" / operation_id / "uploads" / f"{tag}.txt"
    for p in (staging, hold, trash):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"left-over-{tag}\n", encoding="utf-8")
    return {"staging": staging, "hold": hold, "trash": trash}


def _residue_exists(paths: dict[str, Path]) -> bool:
    return any(p.exists() for p in paths.values())


def _run_restore_crash_subprocess(
    *,
    repo: Path,
    backup_dir: Path,
    work_root: Path,
    pre_root: Path,
    fail_at: str,
    logical: str | None,
) -> subprocess.CompletedProcess[str]:
    """子进程内调用 restore 并在目标 hook 处 os._exit（真实强退，非 RuntimeError 假崩）。

    必须从 _V1OPS_DIR（含 BIAOSHU_V1OPS_DIR TEMP 组合）加载生产模块，
    禁止仅插 worktree tools/v1-ops（B 侧可能无 biaoshu_restore.py）。
    """
    # logical 匹配用布尔常量注入，避免生成 `"db" is None` 的 SyntaxWarning
    logical_is_none = logical is None
    script = f"""
import os, sys
sys.path.insert(0, {str(_V1OPS_DIR)!r})
import biaoshu_restore as mod

_want_logical = {logical!r}
_logical_is_none = {logical_is_none!r}

def inj(*args, **kwargs):
    point = args[0] if args else kwargs.get("point")
    log = kwargs.get("logical")
    if log is None and len(args) > 1:
        log = args[1]
    if point == {fail_at!r} and (_logical_is_none or log == _want_logical):
        os._exit(99)

def probe(host, port):
    return False

try:
    mod.restore_offline_backup(
        repo_root={str(repo)!r},
        backup_dir={str(backup_dir)!r},
        pre_restore_destination_root={str(pre_root)!r},
        work_root={str(work_root)!r},
        service_probe=probe,
        fault_injector=inj,
    )
except Exception as exc:
    sys.stderr.write(type(exc).__name__ + ":" + str(exc) + "\\n")
    sys.exit(2)
sys.exit(0)
"""
    env = os.environ.copy()
    env["BIAOSHU_V1OPS_DIR"] = str(_V1OPS_DIR)
    # 确保子进程也能 import 同目录 backup 依赖
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(_V1OPS_DIR) + (os.pathsep + prev_pp if prev_pp else "")
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        env=env,
        cwd=str(_V1OPS_DIR),
    )


# ===========================================================================
# 0. 测试 helper 自包含元测试（B worktree 无生产模块时仍可运行）
# ===========================================================================


class TestFaultInjectorMeta(unittest.TestCase):
    """用途：验证 B1 hook helper 精确 token / hits 旁路；不依赖 biaoshu_restore.py。"""

    def test_reject_legacy_phase_tokens(self) -> None:
        for bad in (
            "PRE_BACKUP",
            "STAGE",
            "CUTOVER_INTENT:db",
            "VERIFY",
            "ROLLING_BACK",
            "COMMIT_CLEANUP",
            "cutover",  # 模糊前缀
        ):
            with self.subTest(token=bad):
                with self.assertRaises(AssertionError):
                    _make_fault_injector(bad)

    def test_exact_point_and_logical_hit_count(self) -> None:
        hits: list[str] = []
        inj = _make_fault_injector(
            "cutover_before_hold", logical="uploads", hits=hits
        )
        # 不匹配：不同 point
        inj("after_stage")
        # 不匹配：同 point 不同 logical
        inj("cutover_before_hold", logical="db")
        self.assertEqual(inj.target_count(), 0)
        with self.assertRaises(AssertionError):
            _assert_hook_hit(inj)
        # 精确命中
        with self.assertRaises(RuntimeError) as cm:
            inj("cutover_before_hold", logical="uploads")
        self.assertIn("INJECTED_FAULT:cutover_before_hold", str(cm.exception))
        _assert_hook_hit(inj)
        self.assertIn("cutover_before_hold:uploads", hits)
        # 禁止 phase/root 别名触发
        inj2 = _make_fault_injector("before_verify")
        inj2(phase="before_verify", root="db")  # type: ignore[call-arg]
        self.assertEqual(inj2.target_count(), 0)

    def test_positional_point_only_semantics(self) -> None:
        hits: list[str] = []
        inj = _make_fault_injector("rollback_begin", hits=hits)
        with self.assertRaises(RuntimeError):
            inj("rollback_begin")
        self.assertEqual(inj.target_count(), 1)
        self.assertIn("rollback_begin", hits)

    def test_hook_points_table_complete(self) -> None:
        expected = {
            "before_pre_backup",
            "after_pre_backup",
            "after_stage",
            "cutover_intent",
            "cutover_before_hold",
            "cutover_after_hold",
            "cutover_before_install",
            "cutover_after_install",
            "before_verify",
            "after_verify",
            "rollback_begin",
            "rollback_before_remove",
            "rollback_before_restore",
            "rollback_after_restore",
            "rollback_end",
            "after_commit",
            "after_cleanup",
        }
        self.assertEqual(_HOOK_POINTS, expected)

    def test_no_startswith_fuzzy_match(self) -> None:
        hits: list[str] = []
        inj = _make_fault_injector("cutover_before_hold", logical="db", hits=hits)
        # 近似名不得命中
        inj("cutover_before_hold_extra", logical="db")
        inj("cutover_before", logical="db")
        self.assertEqual(inj.target_count(), 0)


# ===========================================================================
# 1. 生产入口存在性与包装表面
# ===========================================================================


class TestRestoreEntryPresence(unittest.TestCase):
    """用途：Restore bat/ps1/py 必须存在；缺失即 failure-first。"""

    def test_restore_bat_exists(self) -> None:
        self.assertTrue(
            _RESTORE_BAT.is_file(),
            f"Restore-Biaoshu.bat 不存在（failure-first）：{_RESTORE_BAT}",
        )

    def test_restore_ps1_exists(self) -> None:
        self.assertTrue(
            _RESTORE_PS1.is_file(),
            f"Restore-Biaoshu.ps1 不存在（failure-first）：{_RESTORE_PS1}",
        )

    def test_restore_py_exists(self) -> None:
        self.assertTrue(
            _RESTORE_PY.is_file(),
            f"biaoshu_restore.py 不存在（failure-first）：{_RESTORE_PY}",
        )


class TestRestorePs1BomAndBatForward(unittest.TestCase):
    """用途：PS1 UTF-8 BOM；根 bat 只转发固定 PS1。"""

    def setUp(self) -> None:
        _require_restore_entries()

    def test_restore_ps1_utf8_bom(self) -> None:
        prefix = _read_bom_prefix(_RESTORE_PS1)
        self.assertEqual(prefix, _BOM, "Restore-Biaoshu.ps1 必须带 UTF-8 BOM")

    def test_restore_bat_only_forwards_to_fixed_ps1(self) -> None:
        text = _bat_text(_RESTORE_BAT)
        self.assertRegex(
            text,
            r"tools\\v1-ops\\Restore-Biaoshu\.ps1",
            "Restore bat 必须转发到 tools\\v1-ops\\Restore-Biaoshu.ps1",
        )
        lowered = text.lower()
        for forbidden in ("xcopy", "robocopy", "sqlite3", "sha256", "rmdir /s"):
            self.assertNotIn(
                forbidden,
                lowered,
                f"Restore bat 不得内嵌 {forbidden}",
            )


# ===========================================================================
# 2. 冻结 Python API 表面
# ===========================================================================


class TestRestoreFrozenApiSurface(unittest.TestCase):
    """用途：契约 §8 常量/异常/五函数精确存在。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()

    def test_all_frozen_names_present(self) -> None:
        missing = [n for n in _FROZEN_RESTORE_API if not hasattr(self.mod, n)]
        self.assertEqual(missing, [], f"冻结 API 缺失：{missing}")

    def test_schema_constants_exact(self) -> None:
        self.assertEqual(self.mod.RESTORE_SCHEMA_VERSION, _EXPECTED_RESTORE_SCHEMA)
        self.assertEqual(
            self.mod.RESTORE_DATA_COMPATIBILITY_VERSION,
            _EXPECTED_RESTORE_DATA_COMPAT,
        )
        self.assertEqual(
            self.mod.RESTORE_JOURNAL_SCHEMA_VERSION,
            _EXPECTED_JOURNAL_SCHEMA,
        )

    def test_restore_error_is_exception(self) -> None:
        self.assertTrue(issubclass(self.mod.RestoreError, Exception))

    def test_public_functions_callable(self) -> None:
        for name in (
            "load_and_validate_backup",
            "build_restore_plan",
            "recover_incomplete_restore",
            "restore_offline_backup",
            "main",
        ):
            self.assertTrue(callable(getattr(self.mod, name)), f"{name} 必须可调用")


# ===========================================================================
# 3. 严格预检
# ===========================================================================


class TestRestoreStrictPrecheck(unittest.TestCase):
    """用途：v1/compat/roots/物理集合/逃逸/篡改/损坏/busy/非默认路径/reparse。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-precheck-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.backup_root = base / "backups"
        self.work_root = base / "restore-work"
        self.pre_root = base / "pre-restore-backups"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        _make_fake_repo(self.repo, marker="live-pre")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _backup_dir(self, name: str = "pkg") -> Path:
        return self.backup_root / name

    def test_reject_v1_schema(self) -> None:
        bdir = self._backup_dir("v1")
        _write_v2_backup_package(bdir, schema_version=_EXPECTED_SCHEMA_V1, marker="v1")
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_missing_data_compat(self) -> None:
        bdir = self._backup_dir("no-compat")
        _write_v2_backup_package(bdir, marker="no-compat")
        raw = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        raw.pop("data_compatibility_version", None)
        (bdir / "manifest.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_unknown_data_compat(self) -> None:
        bdir = self._backup_dir("bad-compat")
        _write_v2_backup_package(
            bdir, data_compat="biaoshu-data-v999", marker="bad-compat"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_roots_missing_key(self) -> None:
        bdir = self._backup_dir("roots-miss")
        manifest = _write_v2_backup_package(bdir, marker="roots-miss")
        roots = dict(manifest["roots"])
        roots.pop("legacy_uploads")
        manifest["roots"] = roots
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_roots_illegal_state(self) -> None:
        bdir = self._backup_dir("roots-bad-state")
        manifest = _write_v2_backup_package(bdir, marker="bad-state")
        manifest["roots"]["uploads"]["state"] = "maybe"
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_files_aggregate_mismatch(self) -> None:
        bdir = self._backup_dir("agg")
        manifest = _write_v2_backup_package(bdir, marker="agg")
        manifest["roots"]["uploads"]["file_count"] = 999
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_extra_physical_file(self) -> None:
        bdir = self._backup_dir("extra-phys")
        _write_v2_backup_package(bdir, marker="extra", extra_physical=True)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_path_escape_relative(self) -> None:
        bdir = self._backup_dir("escape")
        manifest = _write_v2_backup_package(bdir, marker="escape")
        manifest["files"].append(
            {
                "logical_root": "uploads",
                "relative_path": "../evil.txt",
                "size_bytes": 1,
                "sha256": _sha256_bytes(b"x"),
            }
        )
        manifest["roots"]["uploads"]["file_count"] = len(
            [f for f in manifest["files"] if f["logical_root"] == "uploads"]
        )
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_case_collision_paths(self) -> None:
        bdir = self._backup_dir("case")
        manifest = _write_v2_backup_package(bdir, marker="case")
        body = b"dup"
        # 写入第二个物理文件（大小写不同）在大小写不敏感卷上可能碰撞
        rel_a = "project-a/Note.txt"
        dest = bdir / "uploads" / "project-a" / "Note.txt"
        # 若系统已有 note.txt，Windows 上可能覆写；仍写入 manifest 重复语义
        dest.write_bytes(body)
        manifest["files"].append(
            {
                "logical_root": "uploads",
                "relative_path": rel_a,
                "size_bytes": len(body),
                "sha256": _sha256_bytes(body),
            }
        )
        # 同时保留 note.txt 条目 → 大小写碰撞
        ups = [f for f in manifest["files"] if f["logical_root"] == "uploads"]
        manifest["roots"]["uploads"]["file_count"] = len(ups)
        manifest["roots"]["uploads"]["total_bytes"] = sum(f["size_bytes"] for f in ups)
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_same_size_content_tamper(self) -> None:
        bdir = self._backup_dir("tamper")
        _write_v2_backup_package(bdir, marker="tamper", tamper_same_size=True)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_corrupt_sqlite_in_backup(self) -> None:
        bdir = self._backup_dir("corrupt-db")
        _write_v2_backup_package(bdir, marker="corrupt", corrupt_db=True)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.load_and_validate_backup(bdir)

    def test_reject_busy_ports(self) -> None:
        bdir = self._backup_dir("busy")
        _write_v2_backup_package(bdir, marker="busy")
        before = _live_root_maps(self.repo)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(self.pre_root),
                work_root=str(self.work_root),
                service_probe=_probe_busy,
            )
        _assert_maps_equal(self, before, _live_root_maps(self.repo), msg="busy 时 live 零变化")

    def test_reject_non_default_database_url(self) -> None:
        bdir = self._backup_dir("nondef-db")
        _write_v2_backup_package(bdir, marker="nondef")
        env_path = self.repo / "backend" / ".env"
        # 只写键名比较所需内容；测试不得打印值到断言消息外的日志
        env_path.write_text(
            "DATABASE_URL=sqlite:///C:/not-default/path/biaoshu.db\n",
            encoding="utf-8",
        )
        before = _live_root_maps(self.repo)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(self.pre_root),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            )
        _assert_maps_equal(
            self, before, _live_root_maps(self.repo), msg="非默认 DATABASE_URL 零 live 写入"
        )

    def test_reject_non_default_upload_dir(self) -> None:
        bdir = self._backup_dir("nondef-up")
        _write_v2_backup_package(bdir, marker="nondef-up")
        env_path = self.repo / "backend" / ".env"
        env_path.write_text(
            "UPLOAD_DIR=D:/elsewhere/uploads\n",
            encoding="utf-8",
        )
        before = _live_root_maps(self.repo)
        with self.assertRaises(self.mod.RestoreError):
            self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(self.pre_root),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            )
        _assert_maps_equal(
            self, before, _live_root_maps(self.repo), msg="非默认 UPLOAD_DIR 零 live 写入"
        )

    def test_reject_backup_ancestor_junction(self) -> None:
        real_pkg = self.backup_root / "real-pkg"
        _write_v2_backup_package(real_pkg, marker="junc")
        link_parent = self.backup_root / "via-junc"
        if link_parent.exists():
            _remove_windows_junction(link_parent)
        _create_windows_junction(link_parent, self.backup_root)
        try:
            # 通过 junction 祖先访问备份
            bdir = link_parent / "real-pkg"
            with self.assertRaises(self.mod.RestoreError):
                self.mod.load_and_validate_backup(bdir)
        finally:
            _remove_windows_junction(link_parent)


# ===========================================================================
# 4. 事务、journal、故障注入与崩溃重入
# ===========================================================================


class TestRestoreTransactionAndCrash(unittest.TestCase):
    """用途：pre-backup、同卷 work、每根故障、逆序回滚、回滚失败、崩溃重入、cleanup。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-tx-")
        base = Path(self._tmp.name)
        # 同卷：全部在同一 tempfile 根下
        self.repo = base / "repo"
        self.backup_root = base / "backups"
        self.work_root = base / "biaoshu-restore-work"
        self.pre_root = base / "biaoshu-backups"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="live-tx", include_legacy=True, include_semantic=True
        )
        self.bdir = self.backup_root / "good-v2"
        _write_v2_backup_package(
            self.bdir,
            marker="bak-tx",
            include_legacy=True,
            include_semantic=False,
            semantic_not_included=True,
        )
        self.before = _live_root_maps(self.repo)
        self.before_marker = _db_marker(self.paths["db"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _restore(self, **kwargs: Any) -> Any:
        params = dict(
            repo_root=str(self.repo),
            backup_dir=str(self.bdir),
            pre_restore_destination_root=str(self.pre_root),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        params.update(kwargs)
        return self.mod.restore_offline_backup(**params)

    def test_default_paths_same_volume_work_root(self) -> None:
        """work_root 与 repo 同卷；成功路径使用显式 work/pre 根。"""
        self.assertEqual(self.repo.drive.lower(), self.work_root.drive.lower())
        result = self._restore()
        # 成功后 live db marker 变为备份 marker
        self.assertEqual(_db_marker(self.paths["db"]), "bak-tx")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        # 结果可含 pre-backup 路径，但不得含密钥
        text = str(result)
        self.assertNotIn(_FAKE_API_KEY_MARKER, text)
        # pre-restore 备份应存在
        pre_dirs = [
            p
            for p in self.pre_root.iterdir()
            if p.is_dir() and p.name.startswith("biaoshu-backup-")
        ]
        self.assertGreaterEqual(len(pre_dirs), 1, "成功恢复必须留下恢复前 v2 备份")

    def test_pre_backup_failure_zero_live_writes(self) -> None:
        before = _live_root_maps(self.repo)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        hits: list[str] = []
        inj = _make_fault_injector("before_pre_backup", hits=hits)
        with self.assertRaises(self.mod.RestoreError):
            self._restore(fault_injector=inj)
        _assert_hook_hit(inj)
        self.assertIn("before_pre_backup", hits)
        _assert_maps_equal(self, before, _live_root_maps(self.repo), msg="before_pre_backup 失败零 live")
        self.assertEqual(_db_marker(self.paths["db"]), self.before_marker)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))

    def test_stage_failure_zero_or_rolled_back_live(self) -> None:
        before = _live_root_maps(self.repo)
        hits: list[str] = []
        inj = _make_fault_injector("after_stage", hits=hits)
        with self.assertRaises(self.mod.RestoreError):
            self._restore(fault_injector=inj)
        _assert_hook_hit(inj)
        self.assertIn("after_stage", hits)
        _assert_maps_equal(self, before, _live_root_maps(self.repo), msg="after_stage 失败后 live 还原")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))

    def test_cutover_fault_each_root_rolls_back(self) -> None:
        """每根 cutover_before_hold / cutover_before_install 故障 → 全根地图回到操作前。

        B10：每个 subTest 使用独立 pre_root + 唯一秒级 now，避免同秒 pre-backup
        目录冲突导致仅 hits=['before_pre_backup'] 假绿/假红；目标 hook 必须真实命中。
        """
        cutover_points = ("cutover_before_hold", "cutover_before_install")
        serial = 0
        for root_name in _ROOT_ORDER:
            if root_name == "semantic_models":
                # 夹具 semantic 为 not_included：走 skip，不 hold/install
                continue
            for point in cutover_points:
                with self.subTest(root=root_name, point=point):
                    serial += 1
                    if self.repo.exists():
                        shutil.rmtree(self.repo)
                    self.repo.mkdir()
                    self.paths = _make_fake_repo(
                        self.repo,
                        marker=f"live-{root_name}",
                        include_legacy=True,
                        include_semantic=True,
                    )
                    before = _live_root_maps(self.repo)
                    self.assertTrue(_db_integrity_ok(self.paths["db"]))
                    if self.work_root.exists():
                        shutil.rmtree(self.work_root)
                    self.work_root.mkdir()
                    # 独立 pre_root：不清空其它子例证据，也不共用同秒 final 名
                    pre_root = self.pre_root.parent / f"pre-{root_name}-{point}-{serial:02d}"
                    if pre_root.exists():
                        shutil.rmtree(pre_root)
                    pre_root.mkdir(parents=True)
                    # 唯一秒级 now（跨分钟），杜绝 create_offline_backup 最终目录已存在
                    now = datetime(
                        2026,
                        7,
                        21,
                        10,
                        (serial // 60) % 60,
                        serial % 60,
                        tzinfo=timezone.utc,
                    )
                    hits: list[str] = []
                    inj = _make_fault_injector(point, logical=root_name, hits=hits)
                    with self.assertRaises(self.mod.RestoreError) as cm:
                        self._restore(
                            fault_injector=inj,
                            pre_restore_destination_root=str(pre_root),
                            now=now,
                        )
                    _assert_hook_hit(inj)
                    self.assertIn(f"{point}:{root_name}", hits)
                    # 禁止仅非零即过：必须精确命中目标 token，不得只停在 pre_backup
                    self.assertNotEqual(
                        hits,
                        ["before_pre_backup"],
                        f"{point}:{root_name} 不得因 pre-backup 撞名而假命中",
                    )
                    after_maps = _live_root_maps(self.repo)
                    try:
                        _assert_maps_equal(
                            self,
                            before,
                            after_maps,
                            msg=f"{point}:{root_name} 后应全根回滚",
                        )
                    except AssertionError as map_exc:
                        # 不放宽：仍失败；附带生产异常与 hits 便于 A 侧对照
                        raise AssertionError(
                            f"{map_exc}; prod_exc={cm.exception!s}; "
                            f"hits_tail={hits[-8:]!r}; "
                            f"db_marker_after={_db_marker(self.paths['db'])!r}"
                        ) from map_exc
                    self.assertTrue(_db_integrity_ok(self.paths["db"]))
                    self.assertEqual(
                        _db_marker(self.paths["db"]),
                        f"live-{root_name}",
                    )

    def test_verify_failure_rolls_back(self) -> None:
        before = _live_root_maps(self.repo)
        hits: list[str] = []
        inj = _make_fault_injector("before_verify", hits=hits)
        with self.assertRaises(self.mod.RestoreError):
            self._restore(fault_injector=inj)
        _assert_hook_hit(inj)
        self.assertIn("before_verify", hits)
        _assert_maps_equal(self, before, _live_root_maps(self.repo), msg="before_verify 失败回滚")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), self.before_marker)

    def test_rollback_failure_fail_closed_preserves_scene(self) -> None:
        """回滚自身故障必须真实命中、非零、现场保留；无 fault 重入后自动收敛成功。

        B12：移除「再次 restore 必拒」假绿——A15 生产在无 fault 重入时会先 recover
        再开新恢复；旧 assertRaises 仅因同秒 pre-backup 撞名而假绿通过。
        首次与重入使用独立秒级 now，杜绝目录撞名掩盖收敛语义。
        """
        hits: list[str] = []
        state = {"cutover_fired": False}

        def combo(*args: Any, **kwargs: Any) -> None:
            point, logical = _parse_hook_call(args, kwargs)
            token = point if logical is None else f"{point}:{logical}"
            if point is not None:
                hits.append(token)
            # 在 uploads 根 hold 成功后、install 前触发，确保 journal 进入需回滚态
            if (
                point == "cutover_before_install"
                and logical == "uploads"
                and not state["cutover_fired"]
            ):
                state["cutover_fired"] = True
                raise RuntimeError("INJECTED_FAULT:cutover_before_install:uploads")
            if point == "rollback_begin":
                raise RuntimeError("INJECTED_FAULT:rollback_begin")

        # 首次故障：独立 now，保证 hook 真命中而非 pre-backup 撞名
        now_first = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(self.mod.RestoreError) as cm:
            self._restore(fault_injector=combo, now=now_first)
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))
        self.assertTrue(state["cutover_fired"], "cutover 故障必须真实触发")
        self.assertIn("rollback_begin", hits, "回滚 hook 必须真实命中")
        # 回滚失败：现场保留（journal 或 hold 至少其一仍在），不得静默清空
        journal_p = self.work_root / _WORK_JOURNAL_NAME
        hold_root = self.work_root / _WORK_HOLD_NAME
        self.assertTrue(
            journal_p.is_file() or hold_root.exists(),
            "回滚失败必须保留 journal 和/或 hold 现场",
        )
        if journal_p.is_file():
            j = _read_json(journal_p)
            self.assertEqual(j.get("schema_version"), _JOURNAL_SCHEMA)
            # 不得谎称已干净回滚完成
            self.assertNotEqual(j.get("phase"), "ROLLED_BACK")
        # 非完整备份态：允许混合现场，但禁止等于完整备份成功全图；DB 必须 integrity
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        after_fail = _live_root_maps(self.repo)
        expected_ok = _expected_backup_live_maps(self.bdir, self.repo)
        self.assertNotEqual(
            after_fail,
            expected_ok,
            "回滚失败不得留下完整新 live 并继续",
        )
        # 无 fault 第二次 _restore：自动 recover + 新恢复必须成功收敛（无需显式 recover 分支）
        now_reentry = datetime(2026, 7, 21, 12, 0, 1, tzinfo=timezone.utc)
        result = self._restore(now=now_reentry)
        self.assertIsInstance(result, dict)
        self.assertIs(result.get("cleanup_completed"), True)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), "bak-tx")
        expected_final = _expected_backup_live_maps(self.bdir, self.repo)
        _assert_maps_equal(
            self,
            expected_final,
            _live_root_maps(self.repo),
            msg="无 fault 重入成功后全根地图=备份权威",
        )
        self.assertFalse(journal_p.is_file(), "收敛后 journal 必须消失")
        self.assertFalse(hold_root.exists(), "收敛后 hold 必须消失")
        self.assertFalse(
            (self.work_root / _WORK_LOCK_NAME).is_file(),
            "收敛后 restore.lock 必须消失",
        )
        self.assertFalse(
            (self.work_root / _WORK_STAGING_NAME).exists(),
            "收敛后 staging 必须消失",
        )
        self.assertFalse(
            (self.work_root / "trash").exists(),
            "收敛后 trash 必须消失",
        )

    def test_hold_missing_must_not_claim_rolled_back(self) -> None:
        """新 live 已安装且 hold 应在却缺失：fail-closed，禁止先删新 live / 写 ROLLED_BACK。"""
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        op_id = _legal_op_id("holdmiss-db-installed")
        live_db = self.repo / _LIVE["db"]
        # 构造“已安装的新 db”：marker/哈希可知；hold 应存在但物理缺失
        new_marker = "new-installed-only"
        _write_minimal_sqlite(live_db, marker=new_marker)
        self.assertTrue(_db_integrity_ok(live_db))
        new_hash = _sha256_file(live_db)
        new_size = live_db.stat().st_size
        hold_db = self.work_root / _WORK_HOLD_NAME / op_id / "db" / "biaoshu.db"
        if hold_db.is_file():
            hold_db.unlink()
        # 确保 hold 父链不留下可恢复载荷
        hold_root = self.work_root / _WORK_HOLD_NAME / op_id
        if hold_root.exists():
            shutil.rmtree(hold_root, ignore_errors=True)
        self.assertFalse(hold_db.exists(), "夹具要求 hold 物理缺失")
        roots = {
            "db": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": "installed",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
            "uploads": {
                "backup_state": "present",
                "intent": None,
                "result": None,
                "live_existed_before": None,
                "hold_moved": False,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        # recover 必须 fail-closed：禁止“先删新 live 再发现 hold 缺失”变绿
        with self.assertRaises(self.mod.RestoreError):
            self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            )
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "hold 缺失 fail-closed 必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(
            j2.get("phase"),
            "ROLLED_BACK",
            "hold 物理缺失不得宣称 ROLLED_BACK",
        )
        # 新 live 必须完整保留（哈希 + marker），不得被先删
        self.assertTrue(live_db.is_file(), "禁止先删新 live")
        self.assertEqual(_db_marker(live_db), new_marker)
        self.assertEqual(_sha256_file(live_db), new_hash)
        self.assertEqual(live_db.stat().st_size, new_size)
        self.assertTrue(_db_integrity_ok(live_db))

    def test_foreign_active_lock_preserves_owner(self) -> None:
        """他人活跃锁：合法 JSON owner + 当前活跃子进程 PID；失败后原锁不变。

        禁止用非 JSON 文本借“pid 未知 fail-closed”假装活跃检测。
        """
        before = _live_root_maps(self.repo)
        op_id = _legal_op_id("lockown-foreign-active")
        journal = _new_test_journal(op_id, phase="STAGE")
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        lock_path = self.work_root / _WORK_LOCK_NAME
        # 真实存活子进程：证明活跃 PID 检测，而非非 JSON fail-closed
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertIsNone(holder.poll(), "夹具要求子进程保持活跃")
            owner_token = "owner-foreign-active-json"
            owner_payload = (
                json.dumps(
                    {"owner": owner_token, "pid": int(holder.pid)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, owner_payload)
                with self.assertRaises(self.mod.RestoreError):
                    self.mod.recover_incomplete_restore(
                        repo_root=str(self.repo),
                        work_root=str(self.work_root),
                        service_probe=_probe_all_free,
                    )
                with self.assertRaises(self.mod.RestoreError):
                    self._restore()
                self.assertTrue(lock_path.is_file(), "不得删除他人活跃锁")
                raw = lock_path.read_bytes()
                # Windows 可能规范化换行；以 JSON 语义比较，禁止改 owner/pid
                parsed = json.loads(raw.decode("utf-8"))
                expected = json.loads(owner_payload.decode("utf-8"))
                self.assertEqual(parsed, expected, "锁内容/所有者不得被篡改")
                self.assertEqual(parsed.get("owner"), owner_token)
                self.assertEqual(int(parsed.get("pid")), int(holder.pid))
                self.assertTrue((self.work_root / _WORK_JOURNAL_NAME).is_file())
                _assert_maps_equal(
                    self,
                    before,
                    _live_root_maps(self.repo),
                    msg="他人活跃锁冲突不得改 live",
                )
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    if lock_path.exists():
                        lock_path.unlink()
                except OSError:
                    pass
        finally:
            try:
                holder.kill()
            except OSError:
                pass
            try:
                holder.wait(timeout=10)
            except Exception:
                pass

    def test_crash_reentry_stale_lock_with_valid_journal(self) -> None:
        """stale lock + 有效未完成 journal：必须安全接管并 recover 成功；禁止永久拒绝。"""
        before = _live_root_maps(self.repo)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        op_id = _legal_op_id("stale-lock-valid-journal")
        # STAGE 相位：live 未变更，仅有 journal + 残留 staging + 可证明失效的僵死锁
        journal = _new_test_journal(op_id, phase="STAGE")
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        staging_marker = self.work_root / _WORK_STAGING_NAME / "uploads" / "x.txt"
        staging_marker.parent.mkdir(parents=True, exist_ok=True)
        staging_marker.write_text("staged\n", encoding="utf-8")
        # 启动已退出子进程，取其 PID 写入 A 当前 JSON 锁格式（owner 非空 + pid 可证失效）
        dead = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        dead_pid = int(dead.pid)
        dead.wait(timeout=30)
        self.assertIsNotNone(dead.returncode)
        lock_path = self.work_root / _WORK_LOCK_NAME
        lock_body = (
            json.dumps(
                {"owner": "stale-owner-dead-pid-token", "pid": dead_pid},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        lock_path.write_text(lock_body, encoding="utf-8")
        # recover 必须成功：安全接管僵死锁并清理 STAGE 现场；禁止永久拒绝
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self, before, after, msg="stale lock+有效 journal recover 后 live 全六根必须等于操作前"
        )
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), self.before_marker)
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "recover 成功后 journal 必须消失",
        )
        self.assertFalse(staging_marker.exists(), "recover 成功后 staging 残留必须消失")
        self.assertFalse(
            (self.work_root / _WORK_STAGING_NAME).exists(),
            "recover 成功后 staging 目录必须消失",
        )
        self.assertFalse(lock_path.is_file(), "recover 成功后 restore.lock 必须消失")

    def test_crash_reentry_after_hold_before_result(self) -> None:
        """真实磁盘态：hold 已成功但 journal result 尚未写入 → recover 后唯一等于操作前。"""
        before = _live_root_maps(self.repo)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        op_id = _legal_op_id("hold-result-window")
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        # hold move 成功
        os.replace(str(live_up), str(hold_up))
        self.assertFalse(live_up.exists())
        self.assertTrue(hold_up.exists())
        roots = {
            "db": {
                "backup_state": "present",
                "intent": None,
                "result": None,
                "live_existed_before": None,
                "hold_moved": False,
                "new_installed": False,
            },
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": None,  # result 前崩溃
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        # 禁止 RuntimeError 假崩；此处仅磁盘/journal 态
        result = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(result, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="hold 成功/result 前崩溃 recover 后必须严格等于操作前",
        )
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), self.before_marker)
        # 唯一终态：marker 只能是操作前
        self.assertEqual(_db_marker(self.paths["db"]), "live-tx")

    def test_crash_reentry_after_install_before_result(self) -> None:
        """真实磁盘态：install 已成功但 journal result 未写入 → recover 后严格等于操作前。"""
        before = _live_root_maps(self.repo)
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        op_id = _legal_op_id("install-result-window")
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(live_up), str(hold_up))
        # 安装“新”uploads（来自备份包）
        bak_up = self.bdir / "uploads"
        shutil.copytree(bak_up, live_up)
        roots = {
            "db": {
                "backup_state": "present",
                "intent": None,
                "result": None,
                "live_existed_before": None,
                "hold_moved": False,
                "new_installed": False,
            },
            "uploads": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": None,  # install 后、result 前
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        result = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(result, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="install 成功/result 前崩溃 recover 后必须严格等于操作前",
        )
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), "live-tx")
        # 禁止多 marker 白名单
        note = (self.repo / _LIVE["uploads"] / "project-a" / "note.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("live-upload-live-tx", note)

    def test_crash_subprocess_os_exit_mid_cutover(self) -> None:
        """子进程 os._exit 强退（非 RuntimeError）：覆盖 hold 后 result 前，再 recover。"""
        if not _RESTORE_PY.is_file():
            raise AssertionError(f"生产模块不存在（failure-first）：{_RESTORE_PY}")
        before = _live_root_maps(self.repo)
        proc = _run_restore_crash_subprocess(
            repo=self.repo,
            backup_dir=self.bdir,
            work_root=self.work_root,
            pre_root=self.pre_root,
            fail_at="cutover_after_hold",
            logical="db",
        )
        # 强退码 99；禁止当成正常 0
        self.assertEqual(proc.returncode, 99, f"stderr={proc.stderr!r} stdout={proc.stdout!r}")
        # 应留下未完成 journal 或半切现场
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(
            jp.is_file() or (self.work_root / _WORK_HOLD_NAME).exists(),
            "强退后必须留下 journal/hold 现场",
        )
        # recover 不得吞异常；成功则唯一终态=操作前
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self, before, after, msg="os._exit 后 recover 必须回到操作前全根地图"
        )
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), "live-tx")

    def test_committed_cleanup_pending_only_cleans(self) -> None:
        """cleanup 真实删除失败：rmtree OSError → COMMITTED_CLEANUP_PENDING；recover 只清理不回滚。"""
        before_restore_marker = self.before_marker
        staging_path = self.work_root / _WORK_STAGING_NAME
        real_rmtree = self.mod.shutil.rmtree
        fail_state = {"armed": True}

        def _rmtree_fail_first_staging(path: Any, *args: Any, **kwargs: Any) -> None:
            """仅让 work_root/staging 第一次删除抛 OSError；目标自然保留；其它路径原样删除。"""
            p = Path(path)
            try:
                p_key = os.path.normcase(str(p.resolve()))
            except OSError:
                p_key = os.path.normcase(str(p))
            try:
                staging_key = os.path.normcase(str(staging_path.resolve()))
            except OSError:
                staging_key = os.path.normcase(str(staging_path))
            if fail_state["armed"] and p_key == staging_key:
                fail_state["armed"] = False
                # 不调用真实 rmtree，目标自然仍存在
                raise OSError(5, "模拟清理删除失败", str(path))
            return real_rmtree(path, *args, **kwargs)

        # 禁止 after_cleanup hook 冒充首次失败；禁止 restore 后手工种植残留
        with mock.patch.object(
            self.mod.shutil, "rmtree", side_effect=_rmtree_fail_first_staging
        ):
            with self.assertRaises(self.mod.RestoreError) as cm:
                self._restore()
        msg = str(cm.exception)
        self.assertNotIn(_FAKE_API_KEY_MARKER, msg)
        self.assertRegex(msg, r"清理未完成|cleanup", msg)
        # 新 live 必须已是备份权威
        self.assertEqual(_db_marker(self.paths["db"]), "bak-tx")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        committed_maps = _live_root_maps(self.repo)
        self.assertNotEqual(
            committed_maps["db"],
            self.before["db"],
            "cleanup-pending 时 live 必须已切换为备份权威",
        )
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "cleanup-pending 必须保留 journal")
        j1 = _read_json(jp)
        self.assertEqual(j1.get("phase"), "COMMITTED_CLEANUP_PENDING")
        self.assertEqual(j1.get("schema_version"), _JOURNAL_SCHEMA)
        # 失败目标自然仍存在（非手工种植）
        self.assertTrue(
            staging_path.exists(),
            "首次 cleanup 真实删除失败后 staging 必须自然保留",
        )
        self.assertFalse(
            fail_state["armed"],
            "必须真实命中 staging 的 rmtree 注入（不得未触发）",
        )
        # 退出 patch 后 recover：只清理，不得回滚新 live
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        self.assertEqual(_db_marker(self.paths["db"]), "bak-tx")
        self.assertNotEqual(_db_marker(self.paths["db"]), before_restore_marker)
        _assert_maps_equal(
            self,
            committed_maps,
            _live_root_maps(self.repo),
            msg="再次 recover 不得回滚已提交新 live",
        )
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertFalse(
            staging_path.exists(),
            "cleanup 成功后 staging 残留必须消失",
        )
        self.assertFalse(
            (self.work_root / _WORK_HOLD_NAME).exists(),
            "cleanup 成功后 hold 残留必须消失",
        )
        self.assertFalse(
            jp.is_file(),
            "cleanup 成功后 journal 应删除",
        )
        self.assertFalse(
            (self.work_root / _WORK_LOCK_NAME).is_file(),
            "cleanup 成功后 restore.lock 必须消失",
        )

    def test_semantic_not_included_preserves_live(self) -> None:
        """semantic not_included：恢复后 live semantic 字节集合不变。"""
        before_sem = _file_map(self.repo / _LIVE["semantic_models"])
        self.assertGreater(len(before_sem), 0, "夹具应有 live semantic")
        self._restore()
        after_sem = _file_map(self.repo / _LIVE["semantic_models"])
        self.assertEqual(before_sem, after_sem, "not_included 时 semantic 必须原样保留")
        # 其它根应切换为备份内容
        self.assertEqual(_db_marker(self.paths["db"]), "bak-tx")
        note = (self.repo / _LIVE["uploads"] / "project-a" / "note.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("backup-upload-bak-tx", note)

    def test_legacy_and_canonical_isolation(self) -> None:
        self._restore()
        # legacy 与 canonical 不得混根
        can_note = self.repo / _LIVE["uploads"] / "project-a" / "note.txt"
        leg = self.repo / _LIVE["legacy_uploads"] / "legacy-doc.txt"
        self.assertTrue(can_note.is_file())
        self.assertTrue(leg.is_file())
        self.assertIn("backup-upload", can_note.read_text(encoding="utf-8"))
        self.assertIn("legacy-bak", leg.read_text(encoding="utf-8"))
        # canonical 树内不得出现 legacy-doc
        can_names = [p.name for p in (self.repo / _LIVE["uploads"]).rglob("*") if p.is_file()]
        self.assertNotIn("legacy-doc.txt", can_names)

    def test_success_full_root_hash_and_sqlite(self) -> None:
        self._restore()
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        # 与备份包权威内容一致（db/uploads/knowledge/cards/legacy）
        bak_db = self.bdir / "db" / "biaoshu.db"
        self.assertEqual(_sha256_file(bak_db), _sha256_file(self.paths["db"]))
        for logical, rel in (
            ("uploads", Path("project-a") / "note.txt"),
            ("knowledge", Path("index.json")),
            ("knowledge_cards", Path("card-001.json")),
            ("legacy_uploads", Path("legacy-doc.txt")),
        ):
            live_p = self.repo / _LIVE[logical] / rel
            bak_p = self.bdir / logical / rel
            self.assertTrue(live_p.is_file(), f"live 缺 {logical}/{rel}")
            self.assertEqual(
                _sha256_file(live_p),
                _sha256_file(bak_p),
                f"哈希不一致 {logical}/{rel}",
            )


# ===========================================================================
# 5. 包装确认与完整往返
# ===========================================================================


class TestRestoreWrapperAndRoundtrip(unittest.TestCase):
    """用途：PS5.1 BOM/中文/空格/精确确认；backup→污染→restore→pre-backup 回滚。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-wrap-")
        base = Path(self._tmp.name)
        # 含空格与中文的路径
        self.repo = base / "仓 库 root"
        self.backup_root = base / "备份 根"
        self.work_root = base / "恢复 工作"
        self.pre_root = base / "恢复前 备份"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ps1_parser_zero_errors_when_present(self) -> None:
        if not _RESTORE_PS1.is_file():
            raise AssertionError(f"Restore-Biaoshu.ps1 缺失（failure-first）：{_RESTORE_PS1}")
        # PowerShell Parser 语法检查
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"$e=$null; [void][System.Management.Automation.Language.Parser]::"
            f"ParseFile('{str(_RESTORE_PS1).replace(chr(39), chr(39)+chr(39))}', [ref]$null, [ref]$e);"
            f"if($e){{$e|ForEach-Object{{$_.ToString()}}; exit 1}} else {{exit 0}}",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
        self.assertEqual(
            proc.returncode,
            0,
            f"PS1 Parser 必须 0 错误：out={_decode_ps_output(proc.stdout)!r} "
            f"err={_decode_ps_output(proc.stderr)!r}",
        )

    def test_ps1_requires_exact_chinese_confirm(self) -> None:
        if not _RESTORE_PS1.is_file():
            raise AssertionError(f"Restore-Biaoshu.ps1 缺失：{_RESTORE_PS1}")
        # 错误确认 → 非 0 且零写入（用假备份目录）
        fake_bak = self.backup_root / "fake"
        fake_bak.mkdir()
        (fake_bak / "manifest.json").write_text("{}", encoding="utf-8")
        proc = _run_powershell(
            _RESTORE_PS1,
            ["-BackupDir", str(fake_bak)],
            cwd=self.repo,
            input_text="确认\n",  # 非精确「恢复」
        )
        self.assertNotEqual(proc.returncode, 0, "错误确认必须非零退出")
        combined = proc.stdout + proc.stderr
        self.assertNotIn(_FAKE_API_KEY_MARKER, combined)

    def test_ps1_cancel_on_empty_confirm_zero_write(self) -> None:
        if not _RESTORE_PS1.is_file():
            raise AssertionError(f"Restore-Biaoshu.ps1 缺失：{_RESTORE_PS1}")
        fake_bak = self.backup_root / "fake2"
        fake_bak.mkdir()
        proc = _run_powershell(
            _RESTORE_PS1,
            ["-BackupDir", str(fake_bak)],
            cwd=self.repo,
            input_text="\n",
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_cli_main_rejects_without_apply(self) -> None:
        mod = _import_restore_module()
        bdir = self.backup_root / "cli"
        _write_v2_backup_package(bdir, marker="cli")
        _make_fake_repo(self.repo, marker="cli-live")
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = mod.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--backup-dir",
                    str(bdir),
                ]
            )
        # 无明确 apply 不得写入；非 0 或帮助
        self.assertNotEqual(code, 0)
        combined = buf_out.getvalue() + buf_err.getvalue()
        self.assertNotIn(_FAKE_API_KEY_MARKER, combined)

    def test_cli_has_no_force_or_skip_flags(self) -> None:
        if not _RESTORE_PY.is_file():
            raise AssertionError(f"biaoshu_restore.py 缺失：{_RESTORE_PY}")
        src = _RESTORE_PY.read_text(encoding="utf-8")
        for banned in (
            "--force",
            "--skip-hash",
            "--skip-journal",
            "--skip-rollback",
            "--skip-integrity",
            "--allow-v1",
            "--no-pre-backup",
        ):
            self.assertNotIn(banned, src, f"CLI 禁止提供 {banned}")

    def test_full_roundtrip_backup_pollute_restore_prebackup_rollback(self) -> None:
        """
        假仓：v2 backup → 污染 live → restore → 再用 pre-restore 备份回滚。
        证据：全根哈希地图 + DB integrity，禁止只看目录存在。
        """
        restore_mod = _import_restore_module()
        backup_mod = _import_backup_module()
        # 1) 初始 live
        _make_fake_repo(
            self.repo, marker="rt-orig", include_legacy=True, include_semantic=True
        )
        orig_maps = _live_root_maps(self.repo)
        orig_marker = _db_marker(self.repo / _LIVE["db"])
        self.assertEqual(orig_marker, "rt-orig")

        # 2) 用生产备份 API 创建包（v2 预期；若仍 v1 则本测试业务失败）
        self.assertEqual(
            backup_mod.BACKUP_SCHEMA_VERSION,
            _EXPECTED_RESTORE_SCHEMA,
            "往返要求备份核心已升级 v2",
        )
        final = backup_mod.create_offline_backup(
            repo_root=str(self.repo),
            destination_root=str(self.backup_root),
            include_semantic_models=False,
            now=datetime(2026, 7, 21, 18, 0, 0, tzinfo=timezone.utc),
            git_head="roundtrip000000000000000000000000000001",
            service_probe=_probe_all_free,
        )
        final_path = Path(str(final))
        self.assertTrue((final_path / "manifest.json").is_file())
        man = json.loads((final_path / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["schema_version"], _EXPECTED_RESTORE_SCHEMA)
        self.assertEqual(man["data_compatibility_version"], _EXPECTED_RESTORE_DATA_COMPAT)
        self.assertEqual(man["roots"]["semantic_models"]["state"], "not_included")

        # 3) 污染 live
        _write_minimal_sqlite(self.repo / _LIVE["db"], marker="rt-polluted")
        pollute_note = self.repo / _LIVE["uploads"] / "project-a" / "note.txt"
        pollute_note.write_text("POLLUTED-CONTENT\n", encoding="utf-8")
        (self.repo / _LIVE["knowledge"] / "index.json").write_text(
            '{"polluted":true}', encoding="utf-8"
        )
        polluted_maps = _live_root_maps(self.repo)
        self.assertNotEqual(polluted_maps["db"], orig_maps["db"])

        # 4) restore 到备份点
        result = restore_mod.restore_offline_backup(
            repo_root=str(self.repo),
            backup_dir=str(final_path),
            pre_restore_destination_root=str(self.pre_root),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(result))
        self.assertEqual(_db_marker(self.repo / _LIVE["db"]), "rt-orig")
        self.assertTrue(_db_integrity_ok(self.repo / _LIVE["db"]))
        restored_maps = _live_root_maps(self.repo)
        # semantic not_included：污染后的 semantic 应保留（若污染未改 semantic，则与 orig 同）
        # 其它根应与备份一致 = 与 orig 一致（semantic 除外可能）
        for root_name in ("db", "uploads", "knowledge", "knowledge_cards", "legacy_uploads"):
            self.assertEqual(
                restored_maps[root_name],
                orig_maps[root_name],
                f"restore 后 {root_name} 应回到备份/原始",
            )

        pre_dirs = sorted(
            p
            for p in self.pre_root.iterdir()
            if p.is_dir() and "backup" in p.name.lower()
        )
        self.assertGreaterEqual(len(pre_dirs), 1, "必须有恢复前备份")
        pre_backup = pre_dirs[-1]
        pre_man = json.loads((pre_backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(pre_man["schema_version"], _EXPECTED_RESTORE_SCHEMA)

        # 5) 用 pre-restore 备份再 restore，回到污染点
        restore_mod.restore_offline_backup(
            repo_root=str(self.repo),
            backup_dir=str(pre_backup),
            pre_restore_destination_root=str(self.pre_root / "second"),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        (self.pre_root / "second").mkdir(exist_ok=True)
        # 上面 work_root 可能需重建
        if not self.work_root.exists():
            self.work_root.mkdir(parents=True)
        # 若第二次 restore 因 work 脏失败，允许显式再调一次干净 work
        if _db_marker(self.repo / _LIVE["db"]) != "rt-polluted":
            work2 = Path(self._tmp.name) / "恢复 工作2"
            work2.mkdir(exist_ok=True)
            pre2 = Path(self._tmp.name) / "恢复前2"
            pre2.mkdir(exist_ok=True)
            restore_mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(pre_backup),
                pre_restore_destination_root=str(pre2),
                work_root=str(work2),
                service_probe=_probe_all_free,
            )
        self.assertEqual(_db_marker(self.repo / _LIVE["db"]), "rt-polluted")
        self.assertTrue(_db_integrity_ok(self.repo / _LIVE["db"]))
        note_now = (self.repo / _LIVE["uploads"] / "project-a" / "note.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("POLLUTED", note_now)


# ===========================================================================
# 6. A6–A9 failure-first / 反假绿（journal 严格、并发 stale、路径重叠、锁清理）
# ===========================================================================


def _tree_sha256_map(root: Path) -> dict[str, tuple[int, str]]:
    """任意目录全树哈希地图（普通文件）；目录不存在 → 空。"""
    return _file_map(root)


def _dead_pid() -> int:
    """返回可证已退出的 PID。"""
    dead = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = int(dead.pid)
    dead.wait(timeout=30)
    return pid


def _write_json_lock(lock_path: Path, *, owner: str, pid: int) -> bytes:
    body = (
        json.dumps({"owner": owner, "pid": int(pid)}, ensure_ascii=False, sort_keys=True)
        + "\n"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    data = body.encode("utf-8")
    lock_path.write_bytes(data)
    return data


def _assert_exact_restore_error(tc: unittest.TestCase, mod: Any, call: Callable[[], Any]) -> str:
    """必须精确 RestoreError，禁止宽泛 Exception / 裸 KeyError 变绿。"""
    try:
        call()
    except mod.RestoreError as exc:
        msg = str(exc)
        tc.assertNotIn(_FAKE_API_KEY_MARKER, msg)
        return msg
    except Exception as exc:  # noqa: BLE001 — 故意捕获以反假绿
        tc.fail(
            f"非法 journal/路径必须抛精确 RestoreError，"
            f"不得泄漏 {type(exc).__name__}: {exc!r}"
        )
    tc.fail("期望 RestoreError，但调用成功返回（假绿）")
    return ""


class TestA6JournalStrictAndPathEscape(unittest.TestCase):
    """A6：journal operation_id 路径穿越 + 严格 schema 表驱动；零 live/外部 sentinel 变化。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a6-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.victim = base / "victim"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.victim.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a6-live", include_legacy=True, include_semantic=True
        )
        self.before_live = _live_root_maps(self.repo)
        # work_root 外 sentinel：路径穿越清理目标
        self.sentinel = self.victim / "SENTINEL.txt"
        self.sentinel.write_bytes(b"A6-VICTIM-SENTINEL-DO-NOT-TOUCH\n")
        self.sentinel_map = _tree_sha256_map(self.victim)
        self.sentinel_hash = _sha256_file(self.sentinel)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _plant_traversal_journal(self) -> str:
        """schema 顶层正确、operation_id='../../victim' 的 journal。"""
        # 相对 work_root：hold/../../victim → 指向 work 外 victim
        # A6 恶意 operation_id 必须保留非法值（显式允许）
        op_id = "../../victim"
        journal = _new_test_journal(
            op_id, phase="STAGE", allow_illegal_operation_id=True
        )
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        # 合法 JSON stale lock（已退出 PID），避免“锁活跃”掩盖路径问题
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME,
            owner="a6-stale-owner",
            pid=_dead_pid(),
        )
        return op_id

    def test_a6_operation_id_path_escape_precise_restore_error(self) -> None:
        """operation_id 逃逸 work_root：精确 RestoreError；sentinel 集合+哈希不变；不得 cleanup。"""
        self._plant_traversal_journal()
        acquire_hits: list[str] = []
        cleanup_hits: list[str] = []
        real_acquire = self.mod._acquire_lock
        real_cleanup = self.mod._cleanup_work

        def _acq(*a: Any, **k: Any) -> Any:
            acquire_hits.append("acquire")
            return real_acquire(*a, **k)

        def _cln(*a: Any, **k: Any) -> Any:
            cleanup_hits.append("cleanup")
            return real_cleanup(*a, **k)

        with mock.patch.object(self.mod, "_acquire_lock", side_effect=_acq), mock.patch.object(
            self.mod, "_cleanup_work", side_effect=_cln
        ):
            _assert_exact_restore_error(
                self,
                self.mod,
                lambda: self.mod.recover_incomplete_restore(
                    repo_root=str(self.repo),
                    work_root=str(self.work_root),
                    service_probe=_probe_all_free,
                ),
            )
        # 不得进入物理 cleanup（路径穿越夹具）
        self.assertEqual(
            cleanup_hits,
            [],
            f"路径穿越 journal 不得 acquire 后 cleanup；hits={cleanup_hits!r}",
        )
        # sentinel 集合 + 哈希不变
        self.assertTrue(self.sentinel.is_file(), "victim sentinel 不得被删除")
        self.assertEqual(_sha256_file(self.sentinel), self.sentinel_hash)
        self.assertEqual(_tree_sha256_map(self.victim), self.sentinel_map)
        _assert_maps_equal(
            self,
            self.before_live,
            _live_root_maps(self.repo),
            msg="A6 路径穿越 recover 不得改 live",
        )
        # journal 必须保留（fail-closed 现场）
        self.assertTrue(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "非法 journal 必须保留现场",
        )

    def test_a6_journal_schema_table_driven_all_restore_error(self) -> None:
        """roots 缺/多键、字段缺/余、bool 用 1/字符串、未知 intent/result、非法 phase/basename。

        全部精确 RestoreError；live 与 victim sentinel 零变化；禁止 KeyError 变绿。
        """
        legal_op = _legal_op_id("a6-schema-table")
        cases: list[tuple[str, dict[str, Any]]] = []

        # 1) roots 缺键
        j = _new_test_journal(legal_op, phase="STAGE")
        del j["roots"]["uploads"]
        cases.append(("roots_missing_key", j))

        # 2) roots 多键
        j = _new_test_journal(legal_op, phase="STAGE")
        j["roots"]["extra_root"] = {
            "backup_state": "present",
            "intent": None,
            "result": None,
            "live_existed_before": None,
            "hold_moved": False,
            "new_installed": False,
        }
        cases.append(("roots_extra_key", j))

        # 3) 根字段缺失
        j = _new_test_journal(legal_op, phase="STAGE")
        del j["roots"]["db"]["hold_moved"]
        cases.append(("root_field_missing", j))

        # 4) 根字段多余
        j = _new_test_journal(legal_op, phase="STAGE")
        j["roots"]["db"]["unexpected"] = True
        cases.append(("root_field_extra", j))

        # 5) bool 字段用整数 1
        j = _new_test_journal(legal_op, phase="STAGE")
        j["roots"]["db"]["hold_moved"] = 1
        cases.append(("bool_as_int", j))

        # 6) bool 字段用字符串
        j = _new_test_journal(legal_op, phase="STAGE")
        j["roots"]["db"]["new_installed"] = "false"
        cases.append(("bool_as_str", j))

        # 7) 未知 intent
        j = _new_test_journal(legal_op, phase="CUTOVER")
        j["roots"]["db"]["intent"] = "not_a_real_intent"
        cases.append(("unknown_intent", j))

        # 8) 未知 result
        j = _new_test_journal(legal_op, phase="CUTOVER")
        j["roots"]["db"]["result"] = "not_a_real_result"
        cases.append(("unknown_result", j))

        # 9) 非法 phase
        j = _new_test_journal(legal_op, phase="NOT_A_PHASE")
        cases.append(("illegal_phase", j))

        # 10) 非法 prebackup basename：含路径分隔
        j = _new_test_journal(
            legal_op, phase="STAGE", pre_backup_name="evil/../pre-name"
        )
        cases.append(("illegal_prebackup_slash", j))

        # 11) 非法 prebackup basename：. 与 ..
        j = _new_test_journal(legal_op, phase="STAGE", pre_backup_name="..")
        cases.append(("illegal_prebackup_dotdot", j))

        # 12) 顶层多余键
        j = _new_test_journal(legal_op, phase="STAGE")
        j["absolute_leak"] = "C:\\\\Users\\\\x"
        # 注意：若生产原子写会拦绝对路径；此处直接写盘测 recover 读取校验
        cases.append(("top_extra_key", j))

        for name, journal in cases:
            with self.subTest(case=name):
                # 每 case 独立 work 子目录，避免互相污染
                wr = self.base / f"work-{name}"
                if wr.exists():
                    shutil.rmtree(wr)
                wr.mkdir()
                # 直接写 journal 文件（绕过生产原子写敏感门）
                jp = wr / _WORK_JOURNAL_NAME
                jp.write_text(
                    json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                _write_json_lock(
                    wr / _WORK_LOCK_NAME, owner=f"a6-{name}", pid=_dead_pid()
                )
                before_live = _live_root_maps(self.repo)
                before_victim = _tree_sha256_map(self.victim)
                before_sent_hash = _sha256_file(self.sentinel)

                _assert_exact_restore_error(
                    self,
                    self.mod,
                    lambda wr=wr: self.mod.recover_incomplete_restore(
                        repo_root=str(self.repo),
                        work_root=str(wr),
                        service_probe=_probe_all_free,
                    ),
                )
                _assert_maps_equal(
                    self,
                    before_live,
                    _live_root_maps(self.repo),
                    msg=f"A6 case={name} live 零变化",
                )
                self.assertEqual(
                    _tree_sha256_map(self.victim),
                    before_victim,
                    f"A6 case={name} victim sentinel 零变化",
                )
                self.assertEqual(_sha256_file(self.sentinel), before_sent_hash)
                self.assertTrue(jp.is_file(), f"A6 case={name} 必须保留 journal 现场")


class TestA7ConcurrentStaleTakeover(unittest.TestCase):
    """A7/B11：可控屏障放大 stale takeover 竞态；行为级计数真实物理收敛入口。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a7-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a7-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)
        op_id = _legal_op_id("a7-concurrent-stale")
        self.op_id = op_id
        journal = _new_test_journal(op_id, phase="STAGE")
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        staging = self.work_root / _WORK_STAGING_NAME / "uploads" / "x.txt"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text("staged-a7\n", encoding="utf-8")
        self.staging = staging
        self.lock_path = self.work_root / _WORK_LOCK_NAME
        self.stale_owner = "a7-stale-shared-owner"
        _write_json_lock(self.lock_path, owner=self.stale_owner, pid=_dead_pid())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a7_concurrent_stale_takeover_exactly_one_cleanup(self) -> None:
        """双线程 + Barrier 放大竞争：恰好一个物理收敛；不绑定废弃 _cleanup_work。

        B11 行为级要求：
        1) 计数当前真实物理入口（优先 _cleanup_artifacts；兼容仍走 _cleanup_work 的旧实现）；
        2) 不强制 unlink/rename 特定事件，允许更安全的 rename/O_EXCL 实现；
        3) 恰好一次物理收敛；败者 RestoreError 或 action=none；
        4) 败者不得改赢家 owner/锁；最终 journal/lock/staging 唯一收敛；live 全图不变。
        """
        start_barrier = threading.Barrier(2, timeout=30)
        results: dict[str, Any] = {}
        errors: dict[str, BaseException] = {}
        physical_entries: list[dict[str, Any]] = []
        physical_guard = threading.Lock()
        winner_owner_at_entry: list[str] = []
        real_acquire = self.mod._acquire_lock
        thread_owner: dict[int, str] = {}

        # 探测当前生产真实物理入口（A 已收敛到 _cleanup_artifacts；兼容旧 _cleanup_work）
        has_artifacts = hasattr(self.mod, "_cleanup_artifacts") and callable(
            getattr(self.mod, "_cleanup_artifacts")
        )
        has_cleanup_work = hasattr(self.mod, "_cleanup_work") and callable(
            getattr(self.mod, "_cleanup_work")
        )
        self.assertTrue(
            has_artifacts or has_cleanup_work,
            "生产必须暴露可观测物理清理入口 _cleanup_artifacts 或 _cleanup_work",
        )
        entry_name = "_cleanup_artifacts" if has_artifacts else "_cleanup_work"
        real_entry = getattr(self.mod, entry_name)

        def _tracked_acquire(work_root: Any, owner_token: str, **kwargs: Any) -> Any:
            tid = threading.current_thread().ident or 0
            thread_owner[tid] = owner_token
            # 双方在 acquire 前汇合，放大双读 stale lock 竞争（不绑定 unlink 事件名）
            try:
                start_barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return real_acquire(work_root, owner_token, **kwargs)

        def _tracked_physical(*a: Any, **k: Any) -> Any:
            tid = threading.current_thread().ident or 0
            owner = thread_owner.get(tid, "?")
            with physical_guard:
                physical_entries.append(
                    {
                        "owner": owner,
                        "tid": tid,
                        "entry": entry_name,
                        "n": len(physical_entries) + 1,
                    }
                )
                if len(physical_entries) == 1:
                    # 记录赢家进入物理清理时锁上的 owner（供败者不改赢家断言）
                    if self.lock_path.is_file():
                        try:
                            body = json.loads(
                                self.lock_path.read_text(encoding="utf-8")
                            )
                            winner_owner_at_entry.append(str(body.get("owner") or ""))
                        except (OSError, json.JSONDecodeError, UnicodeError):
                            winner_owner_at_entry.append(owner)
                    else:
                        winner_owner_at_entry.append(owner)
            # 拉长第一方物理窗口，给败者充分竞争失败机会
            if len(physical_entries) == 1:
                time.sleep(0.05)
            return real_entry(*a, **k)

        def _worker(name: str) -> None:
            try:
                rec = self.mod.recover_incomplete_restore(
                    repo_root=str(self.repo),
                    work_root=str(self.work_root),
                    service_probe=_probe_all_free,
                )
                results[name] = rec
            except BaseException as exc:  # noqa: BLE001
                errors[name] = exc

        patchers = [
            mock.patch.object(self.mod, "_acquire_lock", side_effect=_tracked_acquire),
            mock.patch.object(self.mod, entry_name, side_effect=_tracked_physical),
        ]
        for p in patchers:
            p.start()
        try:
            t1 = threading.Thread(target=_worker, args=("t1",), name="a7-t1")
            t2 = threading.Thread(target=_worker, args=("t2",), name="a7-t2")
            t1.start()
            t2.start()
            t1.join(timeout=90)
            t2.join(timeout=90)
            self.assertFalse(t1.is_alive(), "t1 超时未结束")
            self.assertFalse(t2.is_alive(), "t2 超时未结束")
        finally:
            for p in patchers:
                p.stop()

        err_summary = {
            name: f"{type(exc).__name__}:{exc}" for name, exc in errors.items()
        }
        self.assertEqual(
            len(physical_entries),
            1,
            f"必须恰好一个线程进入物理收敛入口 {entry_name}，实际={physical_entries!r} "
            f"results={results!r} errors={err_summary!r}",
        )
        winner_owner = physical_entries[0]["owner"]
        self.assertTrue(winner_owner and winner_owner != "?", "赢家 owner 必须可观测")

        outcomes: list[tuple[str, Any]] = []
        for name in ("t1", "t2"):
            if name in results:
                outcomes.append(("ok", results[name]))
            elif name in errors:
                exc = errors[name]
                self.assertIsInstance(
                    exc,
                    self.mod.RestoreError,
                    f"{name} 非赢家必须 RestoreError，实际 {type(exc).__name__}: {exc}",
                )
                outcomes.append(("err", str(exc)))
            else:
                self.fail(f"{name} 无结果")
        ok_count = sum(1 for kind, _ in outcomes if kind == "ok")
        err_count = sum(1 for kind, _ in outcomes if kind == "err")
        self.assertGreaterEqual(ok_count, 1, f"至少一方完成 recover：{outcomes!r}")
        if ok_count == 2:
            actions = [r.get("action") for _, r in outcomes if isinstance(r, dict)]
            self.assertTrue(
                "none" in actions
                and (actions.count("cleanup") + actions.count("rollback")) <= 1,
                f"双返回时必须一方 none 且仅一次 cleanup/rollback：{outcomes!r}",
            )
        else:
            self.assertEqual(
                err_count, 1, f"单成功时另一方必须 RestoreError：{outcomes!r}"
            )

        # 败者不得把赢家 owner 改回 stale 或其它值后留下半截锁
        if self.lock_path.is_file():
            body = json.loads(self.lock_path.read_text(encoding="utf-8"))
            self.assertNotEqual(
                body.get("owner"),
                self.stale_owner,
                "最终不得残留 stale owner",
            )
            if winner_owner_at_entry:
                # 若锁仍在，其 owner 只能是赢家会话（或后续合法释放过程中的瞬时态已结束）
                self.assertIn(
                    body.get("owner"),
                    {winner_owner, winner_owner_at_entry[0]},
                    f"败者不得篡改赢家锁 owner：body={body!r} winner={winner_owner!r}",
                )
        else:
            # 成功收敛：锁应消失
            pass

        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "最终 journal 必须收敛消失",
        )
        self.assertFalse(self.staging.exists(), "最终 staging 残留必须消失")
        self.assertFalse(
            (self.work_root / _WORK_STAGING_NAME).exists(),
            "最终 staging 目录必须消失",
        )
        # work_root 允许空目录残留或已 rmdir；但不得残留 journal/staging/hold 载荷
        hold_dir = self.work_root / _WORK_HOLD_NAME
        if hold_dir.exists():
            remaining = list(hold_dir.rglob("*"))
            self.assertEqual(remaining, [], f"hold 载荷必须清空：{remaining!r}")
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self, self.before, after, msg="A7 并发 recover 后 live 唯一=操作前"
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a7-live")


class TestA8PathOverlapGuards(unittest.TestCase):
    """A8：backup/work/pre 重叠在任何写入前拒绝；默认 backup∈pre_root 子目录必须允许。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a8-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.pre_root = base / "pre"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a8-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _pkg(self, d: Path, marker: str = "a8-bak") -> Path:
        _write_v2_backup_package(
            d,
            marker=marker,
            include_legacy=True,
            include_semantic=False,
            semantic_not_included=True,
        )
        return d

    def test_a8_backup_under_work_staging_rejected_before_write(self) -> None:
        """backup_dir 位于 work_root/staging 下：任何写入前拒绝；包/live/pre 不变。"""
        staging = self.work_root / _WORK_STAGING_NAME
        staging.mkdir(parents=True, exist_ok=True)
        bdir = self._pkg(staging / "evil-backup", marker="a8-evil-stg")
        before_pkg = _tree_sha256_map(bdir)
        before_pre = _tree_sha256_map(self.pre_root)
        # 监控 work 下是否新增 journal/lock
        before_work_children = set(p.name for p in self.work_root.iterdir())

        # 必须在任何写入前拒绝；若先破坏备份再抛错仍属失败（反假绿）
        raised = False
        try:
            self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(self.pre_root),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            )
        except self.mod.RestoreError:
            raised = True
        except Exception as exc:  # noqa: BLE001
            self.fail(f"必须精确 RestoreError，实际 {type(exc).__name__}: {exc!r}")
        self.assertTrue(raised, "backup∈work/staging 必须 RestoreError（禁止假绿成功）")
        self.assertEqual(
            _tree_sha256_map(bdir),
            before_pkg,
            "权威备份包不得被改/删（拒绝必须发生在任何写入前）",
        )
        self.assertEqual(_tree_sha256_map(self.pre_root), before_pre, "pre_root 不得写入")
        _assert_maps_equal(
            self, self.before, _live_root_maps(self.repo), msg="重叠拒绝后 live 不变"
        )
        after_work = set(p.name for p in self.work_root.iterdir())
        # 允许仅有预存在的 staging；不得新建 journal/lock 作为“已开始事务”
        self.assertNotIn(_WORK_JOURNAL_NAME, after_work)
        self.assertNotIn(_WORK_LOCK_NAME, after_work)
        self.assertTrue(before_work_children <= after_work or after_work == before_work_children)

    def test_a8_pre_root_under_or_equal_work_rejected(self) -> None:
        """pre_root 位于 work_root 下或等于 work_root：拒绝且零写入。"""
        bdir = self._pkg(self.base / "good-backup", marker="a8-pre-work")
        before_pkg = _tree_sha256_map(bdir)
        # equal
        _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(self.work_root),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        # under
        nested = self.work_root / "nested-pre"
        nested.mkdir()
        _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(nested),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        self.assertEqual(_tree_sha256_map(bdir), before_pkg)
        _assert_maps_equal(
            self, self.before, _live_root_maps(self.repo), msg="pre∈work 拒绝后 live 不变"
        )

    def test_a8_pre_root_under_or_equal_backup_rejected(self) -> None:
        """pre_root 位于 backup_dir 内或等于 backup_dir：拒绝且权威包不变。"""
        bdir = self._pkg(self.base / "auth-backup", marker="a8-pre-bak")
        before_pkg = _tree_sha256_map(bdir)
        # equal
        _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(bdir),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        # under
        nested = bdir / "nested-pre"
        nested.mkdir()
        before_pkg2 = _tree_sha256_map(bdir)
        _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.restore_offline_backup(
                repo_root=str(self.repo),
                backup_dir=str(bdir),
                pre_restore_destination_root=str(nested),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        # 权威包：允许 nested 空目录仍在，但 files/manifest 哈希集合相对 before_pkg2 的文件不变
        self.assertEqual(
            {k: v for k, v in _tree_sha256_map(bdir).items() if not k.startswith("nested-pre")},
            {k: v for k, v in before_pkg2.items() if not k.startswith("nested-pre")},
            "权威备份文件不得被 pre_root 重叠写入破坏",
        )
        _assert_maps_equal(
            self, self.before, _live_root_maps(self.repo), msg="pre∈backup 拒绝后 live 不变"
        )
        self.assertEqual(
            _tree_sha256_map(bdir / "db"),
            {k[3:]: v for k, v in before_pkg.items() if k.startswith("db/")},
        )

    def test_a8_default_backup_child_of_pre_root_allowed(self) -> None:
        """正常默认关系：backup_dir 是 pre_root 既有子目录 → 必须允许并完成恢复。"""
        # 模拟 biaoshu-backups/某个已有包
        bdir = self._pkg(self.pre_root / "biaoshu-backup-existing", marker="a8-ok")
        result = self.mod.restore_offline_backup(
            repo_root=str(self.repo),
            backup_dir=str(bdir),
            pre_restore_destination_root=str(self.pre_root),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(_db_marker(self.paths["db"]), "a8-ok")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        # 不得过度拦截：pre_root 下应新增恢复前备份目录
        children = [p.name for p in self.pre_root.iterdir() if p.is_dir()]
        self.assertIn("biaoshu-backup-existing", children)
        self.assertTrue(
            any(n.startswith("biaoshu-backup-") and n != "biaoshu-backup-existing" for n in children)
            or any(n.startswith("biaoshu-backup-") for n in children),
            f"成功恢复应在 pre_root 留下恢复前备份：{children!r}",
        )


class TestA9LockAndWorkRootCleanup(unittest.TestCase):
    """A9：lock unlink / work_root rmdir OSError → pending；死 PID 重入只清理；外来文件不删。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a9-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.backup_root = base / "backups"
        self.work_root = base / "work"
        self.pre_root = base / "pre"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a9-live", include_legacy=True, include_semantic=True
        )
        self.bdir = self.backup_root / "good-v2"
        _write_v2_backup_package(
            self.bdir,
            marker="a9-bak",
            include_legacy=True,
            include_semantic=False,
            semantic_not_included=True,
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _restore(self, **kwargs: Any) -> Any:
        params = dict(
            repo_root=str(self.repo),
            backup_dir=str(self.bdir),
            pre_restore_destination_root=str(self.pre_root),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        params.update(kwargs)
        return self.mod.restore_offline_backup(**params)

    def test_a9_lock_unlink_oserror_pending_not_after_cleanup(self) -> None:
        """真实定点 patch lock_path.unlink 第一次 OSError；禁止 after_cleanup 冒充。

        首次恢复：RestoreError「清理未完成」；新 live=备份权威；
        journal=COMMITTED_CLEANUP_PENDING；锁仍在。
        """
        lock_path = self.work_root / _WORK_LOCK_NAME
        lock_key_holder: dict[str, str] = {}
        real_unlink = Path.unlink
        fail_state = {"armed": True, "hits": 0}
        after_cleanup_hits: list[str] = []

        def _lock_unlink_fail_first(path_self: Path, *args: Any, **kwargs: Any) -> None:
            try:
                key = os.path.normcase(str(path_self.resolve()))
            except OSError:
                key = os.path.normcase(str(path_self))
            # 延迟解析 lock 路径（work 可能稍后创建）
            try:
                lock_key = os.path.normcase(str(lock_path.resolve()))
            except OSError:
                lock_key = os.path.normcase(str(lock_path))
            lock_key_holder["k"] = lock_key
            if fail_state["armed"] and key == lock_key:
                fail_state["hits"] += 1
                fail_state["armed"] = False
                raise OSError(5, "模拟 lock unlink 失败", str(path_self))
            return real_unlink(path_self, *args, **kwargs)

        def _probe_after_cleanup(*args: Any, **kwargs: Any) -> None:
            # 若生产调用 after_cleanup，仅记录；不得作为首次失败源
            point, _ = _parse_hook_call(args, kwargs)
            if point == "after_cleanup":
                after_cleanup_hits.append("after_cleanup")

        with mock.patch.object(Path, "unlink", _lock_unlink_fail_first):
            with self.assertRaises(self.mod.RestoreError) as cm:
                self._restore(fault_injector=_probe_after_cleanup)
        msg = str(cm.exception)
        self.assertRegex(msg, r"清理未完成")
        self.assertGreaterEqual(fail_state["hits"], 1, "必须真实命中 lock unlink 注入")
        # 新 live 已是备份权威
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "cleanup 未完成必须保留 journal")
        j1 = _read_json(jp)
        self.assertEqual(j1.get("phase"), "COMMITTED_CLEANUP_PENDING")
        self.assertTrue(lock_path.is_file(), "lock unlink 失败后锁必须仍在")
        # 禁止依赖 after_cleanup 作为失败来源（可出现也可不出现，但 hits 必须来自 unlink）
        self.assertGreaterEqual(fail_state["hits"], 1)

    def test_a9_dead_pid_reentry_cleanup_only_converges(self) -> None:
        """模拟原 CLI 结束：锁 PID→已退出；recover 只清理不回滚新 live；现场收敛。"""
        # 先构造 COMMITTED_CLEANUP_PENDING 现场（与上一项类似，但用 rmtree 失败留下 pending）
        staging_path = self.work_root / _WORK_STAGING_NAME
        real_rmtree = self.mod.shutil.rmtree
        fail_state = {"armed": True}

        def _rmtree_fail_staging(path: Any, *args: Any, **kwargs: Any) -> None:
            p = Path(path)
            try:
                p_key = os.path.normcase(str(p.resolve()))
                s_key = os.path.normcase(str(staging_path.resolve()))
            except OSError:
                p_key = os.path.normcase(str(p))
                s_key = os.path.normcase(str(staging_path))
            if fail_state["armed"] and p_key == s_key:
                fail_state["armed"] = False
                raise OSError(5, "模拟 staging 清理失败", str(path))
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(self.mod.shutil, "rmtree", side_effect=_rmtree_fail_staging):
            with self.assertRaises(self.mod.RestoreError):
                self._restore()
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")
        committed = _live_root_maps(self.repo)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file())
        self.assertEqual(_read_json(jp).get("phase"), "COMMITTED_CLEANUP_PENDING")
        lock_path = self.work_root / _WORK_LOCK_NAME
        # 将锁 PID 改为已退出，模拟原 CLI 进程结束
        if lock_path.is_file():
            try:
                old = json.loads(lock_path.read_text(encoding="utf-8"))
                owner = old.get("owner") or "a9-dead-owner"
            except (json.JSONDecodeError, OSError):
                owner = "a9-dead-owner"
        else:
            owner = "a9-dead-owner"
        _write_json_lock(lock_path, owner=str(owner), pid=_dead_pid())

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        # 不回滚新 live
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")
        _assert_maps_equal(
            self, committed, _live_root_maps(self.repo), msg="A9 重入不得回滚已提交 live"
        )
        # 最终收敛：lock/journal/tmp/staging/hold/trash 消失；空 work_root 应消失
        self.assertFalse(lock_path.is_file(), "收敛后 lock 必须消失")
        self.assertFalse(jp.is_file(), "收敛后 journal 必须消失")
        self.assertFalse(
            (self.work_root / "journal.json.tmp").exists(),
            "收敛后 journal.tmp 必须消失",
        )
        self.assertFalse(staging_path.exists(), "收敛后 staging 必须消失")
        self.assertFalse((self.work_root / _WORK_HOLD_NAME).exists(), "收敛后 hold 必须消失")
        self.assertFalse((self.work_root / "trash").exists(), "收敛后 trash 必须消失")
        # 若 work_root 仍在，必须为空或仅 contract 允许的空根；优先期望消失
        if self.work_root.exists():
            remaining = list(self.work_root.iterdir())
            self.assertEqual(
                remaining,
                [],
                f"空 work_root 应删除或为空，残留={remaining!r}",
            )

    def test_a9_work_root_rmdir_oserror_pending_reentrant(self) -> None:
        """work_root rmdir OSError → pending/非零；可重入。"""
        # 完成一次会尝试删除 work_root 的成功 cleanup 路径
        real_rmdir = os.rmdir
        work_key = os.path.normcase(str(self.work_root.resolve()))
        fail_state = {"armed": True, "hits": 0}

        def _rmdir_fail_work(path: Any) -> None:
            try:
                key = os.path.normcase(str(Path(path).resolve()))
            except OSError:
                key = os.path.normcase(str(path))
            if fail_state["armed"] and key == work_key:
                fail_state["hits"] += 1
                fail_state["armed"] = False
                raise OSError(5, "模拟 work_root rmdir 失败", str(path))
            return real_rmdir(path)

        with mock.patch.object(os, "rmdir", side_effect=_rmdir_fail_work):
            with self.assertRaises(self.mod.RestoreError) as cm:
                self._restore()
        msg = str(cm.exception)
        self.assertRegex(msg, r"清理未完成")
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")
        # 若生产尚未实现 work_root 删除，本项对返修前版本应为真实红（hits 或 pending 形态）
        jp = self.work_root / _WORK_JOURNAL_NAME
        if jp.is_file():
            self.assertEqual(_read_json(jp).get("phase"), "COMMITTED_CLEANUP_PENDING")
        # 重入：解除 patch 后应可收敛
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")

    def test_a9_unknown_foreign_file_not_deleted_not_fake_success(self) -> None:
        """未知外来文件不被删除，且不得假称 cleanup 完成。"""
        # 先进入 pending：staging rmtree 失败
        staging_path = self.work_root / _WORK_STAGING_NAME
        real_rmtree = self.mod.shutil.rmtree
        fail_state = {"armed": True}

        def _rmtree_fail_staging(path: Any, *args: Any, **kwargs: Any) -> None:
            p = Path(path)
            try:
                p_key = os.path.normcase(str(p.resolve()))
                s_key = os.path.normcase(str(staging_path.resolve()))
            except OSError:
                p_key = os.path.normcase(str(p))
                s_key = os.path.normcase(str(staging_path))
            if fail_state["armed"] and p_key == s_key:
                fail_state["armed"] = False
                raise OSError(5, "模拟 staging 清理失败", str(path))
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(self.mod.shutil, "rmtree", side_effect=_rmtree_fail_staging):
            with self.assertRaises(self.mod.RestoreError):
                self._restore()
        foreign = self.work_root / "FOREIGN-DO-NOT-DELETE.txt"
        foreign.write_bytes(b"A9-FOREIGN-PAYLOAD\n")
        foreign_hash = _sha256_file(foreign)
        # 死 PID 以便接管
        lock_path = self.work_root / _WORK_LOCK_NAME
        _write_json_lock(lock_path, owner="a9-foreign-owner", pid=_dead_pid())

        # recover 时外来文件存在：不得盲删；不得假称清理完成（若仍无法安全清空 work）
        try:
            rec = self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            )
            # 若返回成功，外来文件必须仍在，且不得假装 work 已安全清空
            self.assertTrue(foreign.is_file(), "未知外来文件不得被删除")
            self.assertEqual(_sha256_file(foreign), foreign_hash)
            # 成功返回时 work_root 仍应存在（因外来文件）
            self.assertTrue(self.work_root.exists())
            self.assertIsInstance(rec, dict)
        except self.mod.RestoreError as exc:
            # fail-closed pending：外来文件仍在
            self.assertRegex(str(exc), r"清理未完成|无法|拒绝|损坏|保留")
            self.assertTrue(foreign.is_file(), "失败路径也不得删除外来文件")
            self.assertEqual(_sha256_file(foreign), foreign_hash)
            jp = self.work_root / _WORK_JOURNAL_NAME
            if jp.is_file():
                phase = _read_json(jp).get("phase")
                self.assertNotEqual(phase, "ROLLED_BACK")
        self.assertEqual(_db_marker(self.paths["db"]), "a9-bak")


# ===========================================================================
# 7. A10/A11/A12 公共边界、回滚证据、phase-aware journal
# ===========================================================================


class TestA10PublicBoundaryRestoreError(unittest.TestCase):
    """A10：before/after_pre_backup 与 recover rollback hook 公共边界固定 RestoreError。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a10-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.backup_root = base / "backups"
        self.work_root = base / "work"
        self.pre_root = base / "pre"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a10-live", include_legacy=True, include_semantic=True
        )
        self.bdir = self.backup_root / "good-v2"
        _write_v2_backup_package(
            self.bdir,
            marker="a10-bak",
            include_legacy=True,
            include_semantic=False,
            semantic_not_included=True,
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _restore(self, **kwargs: Any) -> Any:
        params = dict(
            repo_root=str(self.repo),
            backup_dir=str(self.bdir),
            pre_restore_destination_root=str(self.pre_root),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        params.update(kwargs)
        return self.mod.restore_offline_backup(**params)

    def test_a10_before_pre_backup_runtime_error_is_restore_error(self) -> None:
        """既有 before_pre_backup：只接受 RestoreError，禁止 RuntimeError 泄漏。"""
        hits: list[str] = []
        inj = _make_fault_injector("before_pre_backup", hits=hits)
        with self.assertRaises(self.mod.RestoreError) as cm:
            self._restore(fault_injector=inj)
        _assert_hook_hit(inj)
        self.assertIn("before_pre_backup", hits)
        self.assertNotIsInstance(cm.exception, RuntimeError)
        self.assertIsInstance(cm.exception, self.mod.RestoreError)
        # 禁止多异常宽放：不得因“任意 Exception”变绿
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))
        _assert_maps_equal(
            self, self.before, _live_root_maps(self.repo), msg="before_pre_backup 零 live"
        )

    def test_a10_after_pre_backup_runtime_error_is_restore_error(self) -> None:
        """after_pre_backup 注入 RuntimeError 必须在公共边界成为 RestoreError 且 hits 命中。"""
        hits: list[str] = []
        inj = _make_fault_injector("after_pre_backup", hits=hits)
        with self.assertRaises(self.mod.RestoreError) as cm:
            self._restore(
                fault_injector=inj,
                now=datetime(2026, 7, 21, 11, 1, 1, tzinfo=timezone.utc),
            )
        _assert_hook_hit(inj)
        self.assertIn("after_pre_backup", hits)
        self.assertIsInstance(cm.exception, self.mod.RestoreError)
        # live 不得被 cutover；pre-backup 可能已写出但不构成成功恢复
        after = _live_root_maps(self.repo)
        _assert_maps_equal(self, self.before, after, msg="after_pre_backup 失败 live 不变")
        self.assertEqual(_db_marker(self.paths["db"]), "a10-live")
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))

    def test_a10_recover_rollback_hook_runtime_error_is_restore_error(self) -> None:
        """recover 路径 rollback_begin 注入 RuntimeError → 公共边界 RestoreError + hits。"""
        op_id = _legal_op_id("a10-recover-rollback-hook")
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(live_up), str(hold_up))
        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": None,
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a10-stale", pid=_dead_pid()
        )
        hits: list[str] = []
        inj = _make_fault_injector("rollback_begin", hits=hits)
        with self.assertRaises(self.mod.RestoreError) as cm:
            self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
                fault_injector=inj,
            )
        _assert_hook_hit(inj)
        self.assertIn("rollback_begin", hits)
        self.assertIsInstance(cm.exception, self.mod.RestoreError)
        self.assertTrue(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "rollback hook 失败必须保留 journal 现场",
        )
        j2 = _read_json(self.work_root / _WORK_JOURNAL_NAME)
        self.assertNotEqual(j2.get("phase"), "ROLLED_BACK")
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))


class TestA11RollbackEvidence(unittest.TestCase):
    """A11：回滚证据反例（map 不一致 / DB integrity）+ 正常回滚全图相等。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a11-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a11-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _plant_hold_cutover(self, op_seed: str) -> str:
        """hold 已成功、result 未写的 CUTOVER 现场（合法危险窗口）。"""
        op_id = _legal_op_id(op_seed)
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(live_up), str(hold_up))
        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": None,
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a11-stale", pid=_dead_pid()
        )
        return op_id

    def test_a11_normal_rollback_full_map_equal(self) -> None:
        """正常回滚：recover 后 live 全根地图严格等于操作前。"""
        self._plant_hold_cutover("a11-normal-rollback")
        before = dict(self.before)
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(self, before, after, msg="A11 正常回滚全图相等")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertEqual(_db_marker(self.paths["db"]), "a11-live")
        self.assertFalse((self.work_root / _WORK_JOURNAL_NAME).is_file())

    def test_a11_hold_to_live_map_mismatch_keeps_journal(self) -> None:
        """模拟 hold→live 后 map 不一致：journal 保留、phase 非 ROLLED_BACK、不 cleanup。"""
        op_id = self._plant_hold_cutover("a11-map-mismatch")
        hold_note = (
            self.work_root
            / _WORK_HOLD_NAME
            / op_id
            / "uploads"
            / "project-a"
            / "note.txt"
        )
        self.assertTrue(hold_note.is_file())
        real_replace = os.replace

        def _replace_then_corrupt(src: Any, dst: Any) -> None:
            real_replace(src, dst)
            # hold→live 完成后篡改 live，制造与 hold 快照不一致
            dst_p = Path(dst)
            note = dst_p / "project-a" / "note.txt"
            if note.is_file():
                note.write_bytes(note.read_bytes() + b"\nA11-MAP-MISMATCH\n")

        with mock.patch.object(self.mod.os, "replace", side_effect=_replace_then_corrupt):
            with self.assertRaises(self.mod.RestoreError) as cm:
                self.mod.recover_incomplete_restore(
                    repo_root=str(self.repo),
                    work_root=str(self.work_root),
                    service_probe=_probe_all_free,
                )
        self.assertIsInstance(cm.exception, self.mod.RestoreError)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "map 不一致必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(
            j2.get("phase"),
            "ROLLED_BACK",
            "证据失败不得宣称 ROLLED_BACK",
        )
        # 不得 cleanup 成功（journal 仍在即为未 cleanup 完成）
        self.assertTrue(jp.is_file())
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))

    def test_a11_rollback_db_integrity_failure_keeps_journal(self) -> None:
        """回滚后 live DB integrity 失败：journal 保留、phase 非 ROLLED_BACK、不 cleanup。"""
        op_id = _legal_op_id("a11-db-integrity")
        live_db = self.repo / _LIVE["db"]
        hold_db = self.work_root / _WORK_HOLD_NAME / op_id / "db" / "biaoshu.db"
        hold_db.parent.mkdir(parents=True, exist_ok=True)
        # 把当前 live db 挪到 hold，再在 live 安装“新”db
        os.replace(str(live_db), str(hold_db))
        _write_minimal_sqlite(live_db, marker="a11-new-installed")
        roots = {
            "db": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": "installed",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a11-db-stale", pid=_dead_pid()
        )

        real_replace = os.replace

        def _replace_then_corrupt_db(src: Any, dst: Any) -> None:
            real_replace(src, dst)
            dst_p = Path(dst)
            # hold db → live db 路径（文件级）
            if dst_p.name == "biaoshu.db" and dst_p.is_file():
                _write_corrupt_sqlite(dst_p)

        with mock.patch.object(
            self.mod.os, "replace", side_effect=_replace_then_corrupt_db
        ):
            with self.assertRaises(self.mod.RestoreError) as cm:
                self.mod.recover_incomplete_restore(
                    repo_root=str(self.repo),
                    work_root=str(self.work_root),
                    service_probe=_probe_all_free,
                )
        self.assertIsInstance(cm.exception, self.mod.RestoreError)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "DB integrity 失败必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(j2.get("phase"), "ROLLED_BACK")
        self.assertNotIn(_FAKE_API_KEY_MARKER, str(cm.exception))


class TestA12PhaseAwareJournalGate(unittest.TestCase):
    """A12：phase-aware backup_state 门；cleanup-pending None 可只清理；合法窗口不误拒。"""

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a12-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a12-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a12_backup_state_none_table_driven_precise_restore_error(self) -> None:
        """CUTOVER/VERIFY/ROLLING_BACK 等 phase 下 backup_state=None → 精确 RestoreError。"""
        phases = (
            "PRE_BACKUP",
            "STAGE",
            "CUTOVER",
            "VERIFY",
            "COMMIT",
            "ROLLING_BACK",
            "ROLLED_BACK",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                wr = self.base / f"work-none-{phase.lower()}"
                if wr.exists():
                    shutil.rmtree(wr)
                wr.mkdir()
                op_id = _legal_op_id(f"a12-none-{phase}")
                journal = _new_test_journal(op_id, phase=phase)
                # 六根全部 backup_state=None（不可能持有态，除 cleanup-pending 重建）
                for name in _ROOT_ORDER:
                    journal["roots"][name]["backup_state"] = None
                jp = wr / _WORK_JOURNAL_NAME
                jp.write_text(
                    json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                _write_json_lock(wr / _WORK_LOCK_NAME, owner=f"a12-{phase}", pid=_dead_pid())
                before_live = _live_root_maps(self.repo)
                _assert_exact_restore_error(
                    self,
                    self.mod,
                    lambda wr=wr: self.mod.recover_incomplete_restore(
                        repo_root=str(self.repo),
                        work_root=str(wr),
                        service_probe=_probe_all_free,
                    ),
                )
                _assert_maps_equal(
                    self,
                    before_live,
                    _live_root_maps(self.repo),
                    msg=f"A12 phase={phase} None 门拒绝不得改 live",
                )
                self.assertTrue(jp.is_file(), f"A12 phase={phase} 必须保留 journal")

    def test_a12_committed_cleanup_pending_none_roots_cleanup_only(self) -> None:
        """COMMITTED_CLEANUP_PENDING 安全重建 None 仍可只清理，不得回滚 live。"""
        op_id = _legal_op_id("a12-cleanup-pending-none")
        # live 保持 a12-live；种植 staging 残留模拟 pending cleanup
        staging = self.work_root / _WORK_STAGING_NAME / "uploads" / "left.txt"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text("pending-residue\n", encoding="utf-8")
        journal = _new_test_journal(op_id, phase="COMMITTED_CLEANUP_PENDING")
        for name in _ROOT_ORDER:
            journal["roots"][name]["backup_state"] = None
            journal["roots"][name]["intent"] = None
            journal["roots"][name]["result"] = None
            journal["roots"][name]["live_existed_before"] = None
            journal["roots"][name]["hold_moved"] = False
            journal["roots"][name]["new_installed"] = False
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a12-pending", pid=_dead_pid()
        )
        before = _live_root_maps(self.repo)
        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        self.assertEqual(rec.get("action"), "cleanup")
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self, before, after, msg="cleanup-pending None 只清理不得回滚 live"
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a12-live")
        self.assertFalse(staging.exists(), "只清理必须去掉 staging 残留")
        self.assertFalse((self.work_root / _WORK_JOURNAL_NAME).is_file())

    def test_a12_legal_hold_install_result_windows_not_rejected(self) -> None:
        """合法 hold/install result 前窗口不得被 journal 门拒绝（应进入 recover 收敛）。"""
        cases: list[tuple[str, dict[str, dict[str, Any]], Callable[[str], None]]] = []

        def plant_hold(op_id: str) -> None:
            live_up = self.repo / _LIVE["uploads"]
            hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
            if live_up.exists() and not hold_up.exists():
                hold_up.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(live_up), str(hold_up))

        def plant_install(op_id: str) -> None:
            live_up = self.repo / _LIVE["uploads"]
            hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
            hold_up.parent.mkdir(parents=True, exist_ok=True)
            if live_up.exists() and not hold_up.exists():
                os.replace(str(live_up), str(hold_up))
            if not live_up.exists():
                live_up.mkdir(parents=True)
                (live_up / "project-a").mkdir(parents=True, exist_ok=True)
                (live_up / "project-a" / "note.txt").write_text(
                    "new-installed-a12\n", encoding="utf-8"
                )

        cases.append(
            (
                "hold_before_result",
                {
                    "uploads": {
                        "backup_state": "present",
                        "intent": "hold_live",
                        "result": None,
                        "live_existed_before": True,
                        "hold_moved": True,
                        "new_installed": False,
                    },
                },
                plant_hold,
            )
        )
        cases.append(
            (
                "install_before_result",
                {
                    "uploads": {
                        "backup_state": "present",
                        "intent": "install_stage",
                        "result": None,
                        "live_existed_before": True,
                        "hold_moved": True,
                        "new_installed": True,
                    },
                },
                plant_install,
            )
        )

        for name, roots, planter in cases:
            with self.subTest(window=name):
                # 每 case 重置 live/work
                if self.repo.exists():
                    shutil.rmtree(self.repo)
                self.repo.mkdir()
                self.paths = _make_fake_repo(
                    self.repo,
                    marker="a12-live",
                    include_legacy=True,
                    include_semantic=True,
                )
                before = _live_root_maps(self.repo)
                if self.work_root.exists():
                    shutil.rmtree(self.work_root)
                self.work_root.mkdir()
                op_id = _legal_op_id(f"a12-window-{name}")
                planter(op_id)
                journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
                _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
                _write_json_lock(
                    self.work_root / _WORK_LOCK_NAME,
                    owner=f"a12-{name}",
                    pid=_dead_pid(),
                )
                # 不得在 journal 门被拒：应成功 recover 收敛
                rec = self.mod.recover_incomplete_restore(
                    repo_root=str(self.repo),
                    work_root=str(self.work_root),
                    service_probe=_probe_all_free,
                )
                self.assertIsInstance(rec, dict)
                self.assertEqual(rec.get("action"), "rollback")
                after = _live_root_maps(self.repo)
                _assert_maps_equal(
                    self, before, after, msg=f"A12 合法窗口 {name} 必须回滚到操作前"
                )
                self.assertFalse(
                    (self.work_root / _WORK_JOURNAL_NAME).is_file(),
                    f"A12 合法窗口 {name} recover 后 journal 应消失",
                )


# ===========================================================================
# A13：rollback_restore_hold / 已 restored 重入反假绿
# ===========================================================================


class TestA13RollbackReentryAntiFalseGreen(unittest.TestCase):
    """A13：回滚 restore move 后 result 前崩溃 / 多根已 restored 不得误删 / A3 负例。

    手工合法 journal + 纯 hex32 op_id；禁止 skip/xfail/多终态/宽异常。
    """

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a13-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a13-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _reset_repo_and_work(self, *, marker: str = "a13-live") -> None:
        if self.repo.exists():
            shutil.rmtree(self.repo)
        self.repo.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker=marker, include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)
        if self.work_root.exists():
            shutil.rmtree(self.work_root)
        self.work_root.mkdir()

    def test_a13_rollback_restore_hold_result_window_converges(self) -> None:
        """手工 ROLLING_BACK：intent=rollback_restore_hold、result 前值、hold 缺、live=旧树。

        recover 必须收敛；最终 live 全图=操作前；禁止永久 A3。
        """
        op_id = _legal_op_id("a13-restore-hold-result-window")
        # 物理态：hold 缺失；live 仍为操作前旧树（模拟同卷 rename 已完成、result 未写）
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        self.assertFalse(hold_up.exists(), "夹具要求 hold 物理缺失")
        live_up = self.repo / _LIVE["uploads"]
        self.assertTrue(live_up.exists(), "夹具要求 live 旧树存在")
        note = live_up / "project-a" / "note.txt"
        self.assertTrue(note.is_file())
        self.assertIn("a13-live", note.read_text(encoding="utf-8"))

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "rollback_restore_hold",
                "result": "rollback_removed_new",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="ROLLING_BACK", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a13-window-stale", pid=_dead_pid()
        )
        before = dict(self.before)

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="A13 restore_hold result 前窗口 recover 后 live 全图必须=操作前",
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a13-live")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "A13 收敛成功后 journal 必须消失（非永久 A3）",
        )
        self.assertFalse(
            (self.work_root / _WORK_HOLD_NAME / op_id / "uploads").exists(),
            "收敛后 hold 仍应缺失",
        )
        # live 旧树内容未变
        self.assertTrue(note.is_file())
        self.assertIn("a13-live", note.read_text(encoding="utf-8"))

    def test_a13_multi_root_restored_not_deleted_on_reentry(self) -> None:
        """多根：根A已 rollback_restored；根B仍需恢复。重入不得删除根A，最终全图正确。"""
        op_id = _legal_op_id("a13-multi-root-restored-reentry")
        # 根 A=uploads：已 restored（hold 缺失、live=旧数据、new_installed=True）
        # 根 B=knowledge：回滚中断——新 live 已移除、hold 仍在、intent 待 restore
        live_kn = self.repo / _LIVE["knowledge"]
        hold_kn = self.work_root / _WORK_HOLD_NAME / op_id / "knowledge"
        hold_kn.parent.mkdir(parents=True, exist_ok=True)
        # 把操作前 knowledge 挪到 hold，模拟仍需 restore
        os.replace(str(live_kn), str(hold_kn))
        self.assertFalse(live_kn.exists())
        self.assertTrue(hold_kn.exists())
        # uploads live 保持操作前旧树；hold 缺失
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        self.assertFalse(hold_up.exists())
        live_up = self.repo / _LIVE["uploads"]
        self.assertTrue(live_up.exists())
        up_note = live_up / "project-a" / "note.txt"
        up_before_hash = _sha256_file(up_note)
        up_before_text = up_note.read_text(encoding="utf-8")

        # before 地图：knowledge 在 hold 中，live knowledge 缺；最终应把 hold 还回
        expected = dict(self.before)

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "rollback_restore_hold",
                "result": "rollback_restored",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
            "knowledge": {
                "backup_state": "present",
                "intent": "rollback_restore_hold",
                "result": "rollback_removed_new",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="ROLLING_BACK", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a13-multi-stale", pid=_dead_pid()
        )

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            expected,
            after,
            msg="A13 多根重入最终 live 全图必须=操作前",
        )
        # 根 A 不得被 remove_new 误删
        self.assertTrue(up_note.is_file(), "已 restored 的 uploads 不得被误删")
        self.assertEqual(_sha256_file(up_note), up_before_hash)
        self.assertEqual(up_note.read_text(encoding="utf-8"), up_before_text)
        self.assertIn("a13-live", up_before_text)
        # knowledge 已从 hold 还回
        self.assertTrue(live_kn.exists(), "knowledge hold 必须还回 live")
        self.assertFalse(hold_kn.exists(), "knowledge hold 还回后应消失")
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "多根收敛成功后 journal 必须消失",
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a13-live")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))

    def test_a13_install_stage_hold_missing_new_live_a3_preserves_hash(self) -> None:
        """负例：install_stage/installed + hold 缺失 + live 新数据 → RestoreError 且新 live 哈希不变。"""
        self._reset_repo_and_work(marker="a13-neg-install")
        op_id = _legal_op_id("a13-neg-install-hold-missing")
        live_up = self.repo / _LIVE["uploads"]
        # 安装“新” live（与操作前不同）
        if live_up.exists():
            shutil.rmtree(live_up)
        live_up.mkdir(parents=True)
        new_note = live_up / "project-a" / "note.txt"
        new_note.parent.mkdir(parents=True, exist_ok=True)
        new_note.write_text("a13-NEW-installed-must-preserve\n", encoding="utf-8")
        new_hash = _sha256_file(new_note)
        new_size = new_note.stat().st_size
        new_maps = _live_root_maps(self.repo)
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        if hold_up.exists():
            shutil.rmtree(hold_up)
        self.assertFalse(hold_up.exists(), "夹具要求 hold 物理缺失")

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": "installed",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a13-neg-install", pid=_dead_pid()
        )

        msg = _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        self.assertIn("回滚所需旧数据缺失", msg)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "A3 负例必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(j2.get("phase"), "ROLLED_BACK")
        # 新 live 完整保留
        self.assertTrue(new_note.is_file(), "禁止先删新 live")
        self.assertEqual(_sha256_file(new_note), new_hash)
        self.assertEqual(new_note.stat().st_size, new_size)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            new_maps,
            after,
            msg="A13 install_stage A3 负例不得改新 live 全图",
        )
        self.assertNotIn(_FAKE_API_KEY_MARKER, msg)

    def test_a13_restore_hold_missing_live_missing_a3_keeps_journal(self) -> None:
        """负例：rollback_restore_hold + hold 缺失 + live 缺失 → RestoreError、journal 保留。"""
        self._reset_repo_and_work(marker="a13-neg-both-missing")
        op_id = _legal_op_id("a13-neg-hold-live-both-missing")
        live_up = self.repo / _LIVE["uploads"]
        if live_up.exists():
            shutil.rmtree(live_up)
        self.assertFalse(live_up.exists(), "夹具要求 live 缺失")
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        if hold_up.exists():
            shutil.rmtree(hold_up)
        self.assertFalse(hold_up.exists(), "夹具要求 hold 缺失")
        before = _live_root_maps(self.repo)

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "rollback_restore_hold",
                "result": "rollback_removed_new",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="ROLLING_BACK", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a13-neg-both", pid=_dead_pid()
        )

        msg = _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        self.assertIn("回滚所需旧数据缺失", msg)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "hold+live 双缺失必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(j2.get("phase"), "ROLLED_BACK")
        _assert_maps_equal(
            self,
            before,
            _live_root_maps(self.repo),
            msg="A13 双缺失负例不得改其它 live 根",
        )
        self.assertNotIn(_FAKE_API_KEY_MARKER, msg)


# ===========================================================================
# A14：直接状态矩阵（hold_live 未生效 no-op + 组合回滚 / 窗口确认）
# ===========================================================================


class TestA14DirectStateMatrix(unittest.TestCase):
    """A14：cutover hold_live 未 effect 的直接状态矩阵与组合回滚。

    契约对齐（Codex q=msg_32f3946d / A confirm=msg_264fb5ca）：
    1) intent=hold_live + result 非 held + hold_moved=False + live 旧数据存在 + hold 缺失
       → need_hold no-op，不得 A3，并允许其它已切根完整回滚；
    2) result=held + hold_moved=True + live 存在 + hold 缺失 → 仍 A3，live 哈希不变；
    3) hold 后 / result 前仍可 recover；
    4) install 后 / result 前仍可 recover。

    既有 test_cutover_fault_each_root_rolls_back 四个 cutover_before_hold subTest
    不得放宽；本类为直接矩阵补强。禁止 skip/xfail/宽异常。
    """

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a14-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a14-live", include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _reset_repo_and_work(self, *, marker: str = "a14-live") -> None:
        if self.repo.exists():
            shutil.rmtree(self.repo)
        self.repo.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker=marker, include_legacy=True, include_semantic=True
        )
        self.before = _live_root_maps(self.repo)
        if self.work_root.exists():
            shutil.rmtree(self.work_root)
        self.work_root.mkdir()

    def test_a14_hold_live_no_effect_noop_allows_other_root_full_rollback(
        self,
    ) -> None:
        """hold_live 未 effect 必须 no-op，并允许其它已切根完整回滚到操作前。

        物理态：
        - db：已安装新 live + hold 仍有旧树（result=installed, hold_moved=True）
        - uploads：intent=hold_live、result=None（非 held）、hold_moved=False、
          live=操作前旧数据、hold 物理缺失
        recover 后全图=操作前；journal 消失；禁止因 uploads 伪 need_hold 永久 A3。
        """
        op_id = _legal_op_id("a14-hold-live-noop-combo")
        before = dict(self.before)
        live_db = self.repo / _LIVE["db"]
        hold_db = self.work_root / _WORK_HOLD_NAME / op_id / "db" / "biaoshu.db"
        hold_db.parent.mkdir(parents=True, exist_ok=True)
        # 已切根 db：旧树进 hold，live 换新 marker
        os.replace(str(live_db), str(hold_db))
        self.assertTrue(hold_db.is_file())
        _write_minimal_sqlite(live_db, marker="a14-NEW-db-installed")
        self.assertEqual(_db_marker(live_db), "a14-NEW-db-installed")
        self.assertTrue(_db_integrity_ok(live_db))
        # uploads：live 旧数据仍在，hold 缺失（cutover_before_hold 写 intent 后崩溃）
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        self.assertTrue(live_up.exists(), "夹具要求 uploads live 旧数据存在")
        self.assertFalse(hold_up.exists(), "夹具要求 uploads hold 物理缺失")
        up_note = live_up / "project-a" / "note.txt"
        up_hash = _sha256_file(up_note)
        up_text = up_note.read_text(encoding="utf-8")
        self.assertIn("a14-live", up_text)

        roots = {
            "db": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": "installed",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": None,  # 非 held：intent 已写、effect 未发生
                "live_existed_before": True,
                "hold_moved": False,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a14-noop-stale", pid=_dead_pid()
        )

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="A14 hold_live no-op + 已切根回滚后 live 全图必须=操作前",
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a14-live")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))
        self.assertTrue(up_note.is_file(), "uploads no-op 不得破坏旧 live")
        self.assertEqual(_sha256_file(up_note), up_hash)
        self.assertEqual(up_note.read_text(encoding="utf-8"), up_text)
        self.assertFalse(hold_db.exists(), "db hold 还回后应消失")
        self.assertFalse(
            hold_up.exists(),
            "uploads hold 本就缺失且 no-op 后仍应缺失",
        )
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "A14 组合收敛成功后 journal 必须消失（非永久 A3）",
        )

    def test_a14_result_held_hold_missing_still_a3_preserves_live_hash(self) -> None:
        """result=held + hold_moved=True + live 存在 + hold 缺失 → 仍 A3，live 哈希不变。"""
        self._reset_repo_and_work(marker="a14-held-a3")
        op_id = _legal_op_id("a14-held-hold-missing-a3")
        live_up = self.repo / _LIVE["uploads"]
        self.assertTrue(live_up.exists(), "夹具要求 live 存在")
        note = live_up / "project-a" / "note.txt"
        # 固定内容后取哈希（A3 过程不得改动）
        note.write_text("a14-held-must-preserve-hash\n", encoding="utf-8")
        live_hash = _sha256_file(note)
        live_size = note.stat().st_size
        live_maps = _live_root_maps(self.repo)
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        if hold_up.exists():
            shutil.rmtree(hold_up)
        self.assertFalse(hold_up.exists(), "夹具要求 hold 物理缺失")

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": "held",
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a14-held-a3", pid=_dead_pid()
        )

        msg = _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        self.assertIn("回滚所需旧数据缺失", msg)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "held+hold 缺失 A3 必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(j2.get("phase"), "ROLLED_BACK")
        self.assertTrue(note.is_file(), "A3 禁止先删/改 live")
        self.assertEqual(_sha256_file(note), live_hash)
        self.assertEqual(note.stat().st_size, live_size)
        _assert_maps_equal(
            self,
            live_maps,
            _live_root_maps(self.repo),
            msg="A14 held A3 负例不得改 live 全图",
        )
        self.assertNotIn(_FAKE_API_KEY_MARKER, msg)

    def test_a14_hold_after_before_result_still_recovers(self) -> None:
        """hold 后 / result 前窗口：hold_now=True，recover 后 live 全图=操作前。"""
        self._reset_repo_and_work(marker="a14-hold-window")
        op_id = _legal_op_id("a14-hold-after-before-result")
        before = dict(self.before)
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(live_up), str(hold_up))
        self.assertFalse(live_up.exists())
        self.assertTrue(hold_up.exists())

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": None,  # result 前
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a14-hold-win", pid=_dead_pid()
        )

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="A14 hold 后/result 前 recover 后 live 全图必须=操作前",
        )
        self.assertTrue(live_up.exists(), "hold 必须还回 live")
        self.assertFalse(hold_up.exists(), "还回后 hold 应消失")
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "A14 hold 窗口收敛后 journal 必须消失",
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a14-hold-window")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))

    def test_a14_install_after_before_result_still_recovers(self) -> None:
        """install 后 / result 前窗口：新 live+hold 旧树，recover 后 live 全图=操作前。"""
        self._reset_repo_and_work(marker="a14-install-window")
        op_id = _legal_op_id("a14-install-after-before-result")
        before = dict(self.before)
        live_up = self.repo / _LIVE["uploads"]
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        hold_up.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(live_up), str(hold_up))
        # 安装“新”uploads
        live_up.mkdir(parents=True)
        new_note = live_up / "project-a" / "note.txt"
        new_note.parent.mkdir(parents=True, exist_ok=True)
        new_note.write_text("a14-NEW-installed-window\n", encoding="utf-8")
        self.assertTrue(hold_up.exists())
        self.assertTrue(live_up.exists())

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": None,  # install 后、result 前
                "live_existed_before": True,
                "hold_moved": True,
                "new_installed": True,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a14-install-win", pid=_dead_pid()
        )

        rec = self.mod.recover_incomplete_restore(
            repo_root=str(self.repo),
            work_root=str(self.work_root),
            service_probe=_probe_all_free,
        )
        self.assertIsInstance(rec, dict)
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            before,
            after,
            msg="A14 install 后/result 前 recover 后 live 全图必须=操作前",
        )
        self.assertTrue(live_up.exists())
        note = live_up / "project-a" / "note.txt"
        self.assertTrue(note.is_file())
        self.assertIn("a14-install-window", note.read_text(encoding="utf-8"))
        self.assertNotIn("a14-NEW-installed-window", note.read_text(encoding="utf-8"))
        self.assertFalse(hold_up.exists())
        self.assertFalse(
            (self.work_root / _WORK_JOURNAL_NAME).is_file(),
            "A14 install 窗口收敛后 journal 必须消失",
        )
        self.assertEqual(_db_marker(self.paths["db"]), "a14-install-window")
        self.assertTrue(_db_integrity_ok(self.paths["db"]))


# ===========================================================================
# A15：不一致终态反假绿（hold_live + result=installed 不得删唯一 live）
# ===========================================================================


class TestA15InconsistentEndStateAntiFalseGreen(unittest.TestCase):
    """A15：phase=ROLLING_BACK 下不一致终态必须 A3 fail-closed，禁止假绿删 live。

    契约对齐（Codex q=msg_e9910bb425954c83ae6aae9cd874e9a7；
    Grok A confirm=msg_9ef96c7b8efd4474b488b8e403ec2066；
    前 RR=msg_a93a3256e6734016ba7f53cf86bc4b62）：

    夹具（严格负例，不得放宽）：
    - phase=ROLLING_BACK
    - intent=hold_live
    - result=installed（与 hold_live 语义不一致的脏终态）
    - live_existed_before=True
    - hold_moved=False
    - new_installed=False（journal 字段保持 False，允许生产按 result=installed 推断抬升）
    - hold 物理缺失
    - live 为唯一可知新树（内容固定为 NEW，与操作前不同）

    期望：
    - recover 精确 RestoreError（A3：回滚所需旧数据缺失）
    - live 单文件哈希 + 全图不变
    - journal 保留且 phase 不得变为 ROLLED_BACK
    - 禁止 skip/xfail/宽异常/先删后报

    真红基线：A14 生产 SHA256=FC61339E... 在 A14 宽 need_hold 放行下会删唯一 live
    或非 A3 收敛；A15 收窄后本项必须稳定 PASS。
    """

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1b-a15-")
        base = Path(self._tmp.name)
        self.base = base
        self.repo = base / "repo"
        self.work_root = base / "work"
        self.repo.mkdir()
        self.work_root.mkdir()
        self.paths = _make_fake_repo(
            self.repo, marker="a15-live", include_legacy=True, include_semantic=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a15_hold_live_result_installed_hold_missing_a3_preserves_only_new_live(
        self,
    ) -> None:
        """不一致终态：hold_live+installed+hold 缺+唯一新 live → 精确 A3，live 不变。"""
        op_id = _legal_op_id("a15-hold-live-installed-hold-missing")
        live_up = self.repo / _LIVE["uploads"]
        # 唯一可知新树：替换为 NEW 内容（模拟已 install 后 hold 丢失）
        if live_up.exists():
            shutil.rmtree(live_up)
        live_up.mkdir(parents=True)
        new_note = live_up / "project-a" / "note.txt"
        new_note.parent.mkdir(parents=True, exist_ok=True)
        new_note.write_text(
            "a15-ONLY-NEW-live-must-preserve-hash\n", encoding="utf-8"
        )
        new_hash = _sha256_file(new_note)
        new_size = new_note.stat().st_size
        new_maps = _live_root_maps(self.repo)
        hold_up = self.work_root / _WORK_HOLD_NAME / op_id / "uploads"
        if hold_up.exists():
            shutil.rmtree(hold_up)
        self.assertFalse(hold_up.exists(), "夹具要求 hold 物理缺失")
        self.assertTrue(live_up.exists(), "夹具要求 live 为唯一可知新树")
        self.assertTrue(new_note.is_file())

        roots = {
            "uploads": {
                "backup_state": "present",
                "intent": "hold_live",
                "result": "installed",
                "live_existed_before": True,
                "hold_moved": False,
                "new_installed": False,  # 让生产推断抬升；测试不预写 True
            },
        }
        journal = _new_test_journal(op_id, phase="ROLLING_BACK", roots=roots)
        # 夹具自检：journal 字段必须严格匹配任务矩阵（防夹具漂移假绿）
        ju = journal["roots"]["uploads"]
        self.assertEqual(ju["intent"], "hold_live")
        self.assertEqual(ju["result"], "installed")
        self.assertIs(ju["live_existed_before"], True)
        self.assertIs(ju["hold_moved"], False)
        self.assertIs(ju["new_installed"], False)
        self.assertEqual(journal["phase"], "ROLLING_BACK")
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        _write_json_lock(
            self.work_root / _WORK_LOCK_NAME, owner="a15-inconsist-a3", pid=_dead_pid()
        )

        msg = _assert_exact_restore_error(
            self,
            self.mod,
            lambda: self.mod.recover_incomplete_restore(
                repo_root=str(self.repo),
                work_root=str(self.work_root),
                service_probe=_probe_all_free,
            ),
        )
        self.assertIn("回滚所需旧数据缺失", msg)
        jp = self.work_root / _WORK_JOURNAL_NAME
        self.assertTrue(jp.is_file(), "A15 A3 负例必须保留 journal")
        j2 = _read_json(jp)
        self.assertNotEqual(
            j2.get("phase"),
            "ROLLED_BACK",
            "A15 不一致终态不得宣称 ROLLED_BACK",
        )
        # 唯一新 live 完整保留：禁止先删后报 / 禁止 trash 移走
        self.assertTrue(new_note.is_file(), "A15 禁止删除唯一可知新 live")
        self.assertEqual(_sha256_file(new_note), new_hash)
        self.assertEqual(new_note.stat().st_size, new_size)
        self.assertEqual(
            new_note.read_text(encoding="utf-8"),
            "a15-ONLY-NEW-live-must-preserve-hash\n",
        )
        after = _live_root_maps(self.repo)
        _assert_maps_equal(
            self,
            new_maps,
            after,
            msg="A15 不一致终态 A3 不得改 live 全图",
        )
        self.assertFalse(hold_up.exists(), "A3 后 hold 仍应缺失（不得伪造 hold）")
        self.assertNotIn(_FAKE_API_KEY_MARKER, msg)


# ===========================================================================
# 8. 反假绿与安全门
# ===========================================================================


class TestRestoreNoTouchRealData(unittest.TestCase):
    """用途：本文件不得读主仓真实业务路径；禁止 skip/xfail。"""

    def test_module_uses_tempfile_only(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("TemporaryDirectory", src)
        self.assertIn("_make_fake_repo", src)
        self.assertIsNone(re.search(r"(?m)^\s*@unittest\.skip\b", src))
        self.assertIsNone(re.search(r"(?m)^\s*@pytest\.mark\.skip\b", src))
        skip_call = "skip" + "Test"
        self.assertIsNone(re.search(rf"(?m)^\s*self\.{skip_call}\(", src))
        self.assertIsNone(re.search(r"(?m)^\s*@unittest\.expectedFailure\b", src))
        # 禁止把主仓真实 db 绝对路径写进源码用于打开
        real_db = str((_REPO_ROOT / "backend" / "data" / "biaoshu.db").resolve())
        self.assertNotIn(real_db, src)


# ===========================================================================
# V1-Q：Restore / CUTOVER recover 经 backup.assert_services_stopped 的 LAN 红门
# ===========================================================================

_V1Q_FORBIDDEN_PORTS = frozenset({8000, 5173})


def _v1q_is_rfc1918_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    if any(n < 0 or n > 255 for n in nums):
        return False
    a, b = nums[0], nums[1]
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def _v1q_bindable_non_loopback_ipv4() -> str:
    import socket

    seen: list[str] = []
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith("127.") or ip in seen:
            continue
        seen.append(ip)
    bindable: list[str] = []
    for ip in seen:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((ip, 0))
            bindable.append(ip)
        except OSError:
            continue
        finally:
            try:
                sock.close()
            except OSError:
                pass
    if not bindable:
        raise AssertionError(
            "无本机已分配非回环 IPv4 可绑定（V1-Q 禁止 skip；硬失败）"
        )
    rfc = [ip for ip in bindable if _v1q_is_rfc1918_ipv4(ip)]
    return rfc[0] if rfc else bindable[0]


def _v1q_bind_high_port_on(ip: str) -> tuple[Any, int]:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((ip, 0))
    port = int(sock.getsockname()[1])
    if port in _V1Q_FORBIDDEN_PORTS or port < 1024:
        sock.close()
        raise AssertionError(f"非法隔离端口：{port}")
    sock.listen(1)
    return sock, port


def _v1q_full_tree_maps(
    repo: Path, backup: Path, pre: Path, work: Path
) -> dict[str, dict[str, tuple[int, str]]]:
    return {
        "repo": _file_map(repo),
        "backup": _file_map(backup),
        "pre": _file_map(pre),
        "work": _file_map(work),
    }


class TestV1QRestoreRecoverLanPortGate(unittest.TestCase):
    """
    V1-Q R5：restore 与 CUTOVER recover 各自独立红门。
    monkeypatch restore 已导入的 assert_services_stopped，精确一次路由到
    backup.assert_services_stopped(ports=(P,), probe=None)；live 拒绝；
    调用计数 1；repo/backup/pre/work 全文件哈希图不变。
    """

    def setUp(self) -> None:
        self.mod = _import_restore_module()
        self.bak = _import_backup_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1q-restore-r5-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.backup_root = base / "backups"
        self.work_root = base / "restore-work"
        self.pre_root = base / "pre-restore-backups"
        self.repo.mkdir()
        self.backup_root.mkdir()
        self.work_root.mkdir()
        self.pre_root.mkdir()
        _make_fake_repo(self.repo, marker="v1q-live")
        self.bdir = self.backup_root / "pkg"
        _write_v2_backup_package(self.bdir, marker="v1q-bak")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _route_once(self, port: int) -> tuple[Callable[..., Any], dict[str, int]]:
        counter = {"n": 0}
        bak = self.bak

        def _routed(*_a: Any, **_k: Any) -> None:
            counter["n"] += 1
            # 精确一次路由：忽略 service_probe，强制 probe=None + 隔离端口
            bak.assert_services_stopped(ports=(port,), probe=None)

        return _routed, counter

    def test_restore_live_lan_port_rejected_hash_unchanged(self) -> None:
        ip = _v1q_bindable_non_loopback_ipv4()
        sock, port = _v1q_bind_high_port_on(ip)
        routed, counter = self._route_once(port)
        before = _v1q_full_tree_maps(
            self.repo, self.bdir, self.pre_root, self.work_root
        )
        try:
            with mock.patch.object(self.mod, "assert_services_stopped", side_effect=routed):
                with self.assertRaises(self.mod.RestoreError):
                    self.mod.restore_offline_backup(
                        repo_root=str(self.repo),
                        backup_dir=str(self.bdir),
                        pre_restore_destination_root=str(self.pre_root),
                        work_root=str(self.work_root),
                        service_probe=_probe_all_free,
                    )
            self.assertEqual(
                counter["n"],
                1,
                f"assert_services_stopped 路由计数必须精确 1，实际={counter['n']}",
            )
            after = _v1q_full_tree_maps(
                self.repo, self.bdir, self.pre_root, self.work_root
            )
            for key in ("repo", "backup", "pre", "work"):
                self.assertEqual(
                    before[key],
                    after[key],
                    f"restore live 拒绝后 {key} 全文件哈希图必须不变",
                )
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def test_cutover_recover_live_lan_port_rejected_hash_unchanged(self) -> None:
        ip = _v1q_bindable_non_loopback_ipv4()
        sock, port = _v1q_bind_high_port_on(ip)
        routed, counter = self._route_once(port)
        # 种植 CUTOVER journal：recover 在该 phase 调用 assert_services_stopped
        op_id = _legal_op_id("v1q-cutover-lan")
        roots = {
            "db": {
                "backup_state": "present",
                "intent": "install_stage",
                "result": None,
                "live_existed_before": True,
                "hold_moved": False,
                "new_installed": False,
            },
            "uploads": {
                "backup_state": "present",
                "intent": None,
                "result": None,
                "live_existed_before": None,
                "hold_moved": False,
                "new_installed": False,
            },
        }
        journal = _new_test_journal(op_id, phase="CUTOVER", roots=roots)
        _write_json(self.work_root / _WORK_JOURNAL_NAME, journal)
        before = _v1q_full_tree_maps(
            self.repo, self.bdir, self.pre_root, self.work_root
        )
        try:
            with mock.patch.object(self.mod, "assert_services_stopped", side_effect=routed):
                with self.assertRaises(self.mod.RestoreError):
                    self.mod.recover_incomplete_restore(
                        repo_root=str(self.repo),
                        work_root=str(self.work_root),
                        service_probe=_probe_all_free,
                    )
            self.assertEqual(
                counter["n"],
                1,
                f"recover 路由计数必须精确 1，实际={counter['n']}",
            )
            after = _v1q_full_tree_maps(
                self.repo, self.bdir, self.pre_root, self.work_root
            )
            for key in ("repo", "backup", "pre", "work"):
                self.assertEqual(
                    before[key],
                    after[key],
                    f"recover live 拒绝后 {key} 全文件哈希图必须不变",
                )
        finally:
            try:
                sock.close()
            except OSError:
                pass


class TestV1QRestoreSelfGuard(unittest.TestCase):
    """V1-Q 自守卫：仅扫描本文件新增 V1-Q 方法。"""

    def test_v1q_restore_methods_forbid_skip_and_real_services(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        marker = "# V1-Q：Restore / CUTOVER recover"
        idx = src.find(marker)
        self.assertGreater(idx, 0)
        end = src.find("def _suite_order", idx)
        chunk = src[idx:end] if end > idx else src[idx:]
        self.assertIsNone(re.search(r"(?m)^\s*@unittest\.skip\b", chunk))
        self.assertIsNone(re.search(r"(?m)^\s*self\.skipTest\(", chunk))
        self.assertIsNone(re.search(r"(?m)^\s*return\s*$", chunk))
        self.assertIsNone(re.search(r"(?ms)except\s+Exception\s*:\s*pass\b", chunk))
        banned_pid = "31" + "76"
        self.assertNotIn(banned_pid, chunk)
        self.assertNotIn("8.8.8.8", chunk)
        real_db = str((_REPO_ROOT / "backend" / "data" / "biaoshu.db").resolve())
        self.assertNotIn(real_db, chunk)


def _suite_order() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestFaultInjectorMeta,
        TestRestoreEntryPresence,
        TestRestorePs1BomAndBatForward,
        TestRestoreFrozenApiSurface,
        TestRestoreStrictPrecheck,
        TestRestoreTransactionAndCrash,
        TestRestoreWrapperAndRoundtrip,
        TestA6JournalStrictAndPathEscape,
        TestA7ConcurrentStaleTakeover,
        TestA8PathOverlapGuards,
        TestA9LockAndWorkRootCleanup,
        TestA10PublicBoundaryRestoreError,
        TestA11RollbackEvidence,
        TestA12PhaseAwareJournalGate,
        TestA13RollbackReentryAntiFalseGreen,
        TestA14DirectStateMatrix,
        TestA15InconsistentEndStateAntiFalseGreen,
        TestRestoreNoTouchRealData,
        TestV1QRestoreRecoverLanPortGate,
        TestV1QRestoreSelfGuard,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2, failfast=False, buffer=False)
    result = runner.run(_suite_order())
    print(
        "\n[V1-B][GROK-B][RESTORE] SUMMARY "
        f"ran={result.testsRun} "
        f"failures={len(result.failures)} "
        f"errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    print(
        "\n[V1-Q][GROK-B][RESTORE] SUMMARY "
        f"ran={result.testsRun} "
        f"failures={len(result.failures)} "
        f"errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    if result.failures:
        first = result.failures[0]
        print(
            f"[V1-Q][GROK-B][RESTORE] FIRST_FAILURE test={first[0]} "
            f"msg={first[1].splitlines()[-1] if first[1] else ''}"
        )
    if result.errors:
        first_e = result.errors[0]
        print(
            f"[V1-Q][GROK-B][RESTORE] FIRST_ERROR test={first_e[0]} "
            f"msg={first_e[1].splitlines()[-1] if first_e[1] else ''}"
        )
    if result.errors:
        print(f"[V1-Q][GROK-B][RESTORE] COLLECTION_OR_SETUP_ERRORS={len(result.errors)}")
    if result.skipped:
        print(f"[V1-Q][GROK-B][RESTORE] UNEXPECTED_SKIP={len(result.skipped)}")
    sys.exit(0 if result.wasSuccessful() else 1)
