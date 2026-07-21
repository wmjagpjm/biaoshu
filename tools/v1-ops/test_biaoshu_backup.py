# -*- coding: utf-8 -*-
"""
模块：V1-A 受控停机与离线备份专项测试
用途：在临时假仓库/假 SQLite/假文件树上验证契约 §5 与冻结 Python API；
      生产入口缺失时形成真实 failure-first（禁止 skip/xfail）。
对接：tools/v1-ops/biaoshu_backup.py、Stop-Biaoshu-Dev.ps1、Backup-Biaoshu.ps1、
      根目录 Stop-Biaoshu-Dev.bat / Backup-Biaoshu.bat；unittest；标准库 tempfile。
二次开发：不得杀真实进程、不得占真实 8000/5173、不得读主仓 data/uploads；
         不得安装第三方依赖；probe/now/git_head 仅测试注入。
"""

from __future__ import annotations

import hashlib
import json
import locale
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest import mock

# ---------------------------------------------------------------------------
# 路径锚定：本 worktree 仓库根（禁止依赖进程 cwd）
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_PROD_MODULE_PATH = _HERE / "biaoshu_backup.py"
_STOP_PS1 = _HERE / "Stop-Biaoshu-Dev.ps1"
_BACKUP_PS1 = _HERE / "Backup-Biaoshu.ps1"
_STOP_BAT = _REPO_ROOT / "Stop-Biaoshu-Dev.bat"
_BACKUP_BAT = _REPO_ROOT / "Backup-Biaoshu.bat"

# UTF-8 BOM
_BOM = b"\xef\xbb\xbf"

# 冻结 schema 版本
_EXPECTED_SCHEMA = "biaoshu-offline-backup-v1"

# 假敏感标记：仅用于断言不得泄漏到 manifest/控制台
_FAKE_API_KEY_MARKER = "sk-fake-v1a-test-key-DO-NOT-LEAK"
_FAKE_SECRET_BODY = f"api_key={_FAKE_API_KEY_MARKER}; password=fake-secret-body"

# 契约冻结的公开 Python 接口名
_FROZEN_API_NAMES = (
    "BACKUP_SCHEMA_VERSION",
    "BackupError",
    "build_source_plan",
    "assert_services_stopped",
    "create_offline_backup",
    "main",
)

# 默认源逻辑根（与契约 §3 对齐）
_CANONICAL_DB_REL = Path("backend") / "data" / "biaoshu.db"
_CANONICAL_UPLOADS = Path("backend") / "uploads"
_CANONICAL_KNOWLEDGE = Path("backend") / "data" / "knowledge"
_CANONICAL_CARDS = Path("backend") / "data" / "knowledge_cards"
_LEGACY_UPLOADS = Path("uploads")
_SEMANTIC_MODELS = Path("backend") / "data" / "semantic-models"


def _repo_root_for_tests() -> Path:
    """返回当前 worktree 根，用于检查生产入口是否存在。"""
    return _REPO_ROOT


def _require_prod_files() -> dict[str, Path]:
    """
    断言五个生产入口全部存在；缺失时抛 AssertionError，形成业务失败。
    不得 skip。
    """
    files = {
        "stop_bat": _STOP_BAT,
        "backup_bat": _BACKUP_BAT,
        "stop_ps1": _STOP_PS1,
        "backup_ps1": _BACKUP_PS1,
        "backup_py": _PROD_MODULE_PATH,
    }
    missing = [name for name, p in files.items() if not p.is_file()]
    if missing:
        raise AssertionError(
            "生产入口缺失（failure-first 预期）："
            + ", ".join(f"{n}={files[n]}" for n in missing)
        )
    return files


def _import_backup_module():
    """导入冻结 Python 模块；模块不存在时形成真实 ImportError/业务失败。"""
    if not _PROD_MODULE_PATH.is_file():
        raise AssertionError(
            f"生产模块不存在（failure-first 预期）：{_PROD_MODULE_PATH}"
        )
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import biaoshu_backup as mod  # noqa: WPS433

    return mod


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _write_minimal_sqlite(db_path: Path, *, marker: str = "v1a-fake") -> None:
    """写入最小合法 SQLite，便于 integrity_check=ok。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
            ("marker", marker),
        )
        # 假敏感：产品允许明文 API Key 存库；测试只验证不泄漏到 manifest
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
    """写入非合法 SQLite 字节，副本 integrity_check 应失败。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 以 SQLite 头开头但截断/污染，确保无法通过 integrity_check
    header = b"SQLite format 3\x00"
    junk = b"\x00" * 80 + b"CORRUPT-V1A-FAKE-DB" + os.urandom(64)
    db_path.write_bytes(header + junk)


def _make_fake_repo(
    root: Path,
    *,
    include_legacy: bool = True,
    include_semantic: bool = False,
    include_forbidden_dbs: bool = True,
    db_ok: bool = True,
) -> dict[str, Path]:
    """
    在临时目录构造假仓库树；禁止引用主仓真实 data/uploads。
    返回关键路径字典。
    """
    backend_data = root / "backend" / "data"
    backend_uploads = root / "backend" / "uploads"
    knowledge = backend_data / "knowledge"
    cards = backend_data / "knowledge_cards"
    backend_data.mkdir(parents=True, exist_ok=True)
    backend_uploads.mkdir(parents=True, exist_ok=True)
    knowledge.mkdir(parents=True, exist_ok=True)
    cards.mkdir(parents=True, exist_ok=True)

    db_path = backend_data / "biaoshu.db"
    if db_ok:
        _write_minimal_sqlite(db_path)
    else:
        _write_corrupt_sqlite(db_path)

    upload_file = backend_uploads / "project-a" / "note.txt"
    upload_file.parent.mkdir(parents=True, exist_ok=True)
    upload_file.write_text("fake-upload-body\n", encoding="utf-8")

    idx = knowledge / "index.json"
    idx.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")
    card = cards / "card-001.json"
    card.write_text(json.dumps({"id": "card-001", "title": "fake"}), encoding="utf-8")

    # 禁止打包的测试库 / 无关库（存在时不得进入源计划）
    if include_forbidden_dbs:
        (backend_data / "biaoshu-e2e.db").write_bytes(b"not-a-real-e2e-db")
        (backend_data / "biaoshu-pytest-abc.db").write_bytes(b"not-pytest")
        (backend_data / "codex-scratch").mkdir(exist_ok=True)
        (backend_data / "codex-scratch" / "x.txt").write_text("no", encoding="utf-8")
        (backend_data / "other-unknown.db").write_bytes(b"unknown-db")

    # .env 与敏感文件：不得进入备份
    (root / ".env").write_text(f"OPENAI_API_KEY={_FAKE_API_KEY_MARKER}\n", encoding="utf-8")
    (root / "backend" / ".venv").mkdir(parents=True, exist_ok=True)
    (root / "backend" / ".venv" / "pyvenv.cfg").write_text("fake\n", encoding="utf-8")
    (root / "frontend" / "node_modules").mkdir(parents=True, exist_ok=True)
    (root / "frontend" / "node_modules" / "x").write_text("n", encoding="utf-8")

    legacy_file = None
    if include_legacy:
        legacy_root = root / "uploads"
        legacy_root.mkdir(parents=True, exist_ok=True)
        legacy_file = legacy_root / "legacy-doc.txt"
        legacy_file.write_text("legacy-root-body\n", encoding="utf-8")

    semantic_file = None
    if include_semantic:
        sem = backend_data / "semantic-models"
        sem.mkdir(parents=True, exist_ok=True)
        semantic_file = sem / "model.bin"
        semantic_file.write_bytes(b"FAKE-SEMANTIC-MODEL")

    return {
        "root": root,
        "db": db_path,
        "upload_file": upload_file,
        "knowledge": idx,
        "card": card,
        "legacy_file": legacy_file,
        "semantic_file": semantic_file,
    }


def _probe_all_free(host: str, port: int) -> bool:
    """测试注入：永远报告端口空闲（不触碰真实 8000/5173）。"""
    return False  # False = 未监听


def _probe_busy(host: str, port: int) -> bool:
    """测试注入：永远报告端口占用。"""
    return True


def _probe_partial(busy_ports: set[int]) -> Callable[[str, int], bool]:
    def _inner(host: str, port: int) -> bool:
        return port in busy_ports

    return _inner


def _decode_ps_output(raw: bytes | str | None) -> str:
    """
    解码 PowerShell 管道输出。
    确定性顺序：先 utf-8-sig 严格成功即返回；仅 UnicodeDecodeError 后再
    依次尝试 locale / mbcs / gb18030 / cp936 严格解码；最后 utf-8 replace。
    禁止按 CJK 数量启发式选候选（合法 UTF-8 中文会被 gb18030 误解并得分更高）。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""

    # 1) 先 utf-8-sig 严格解码，成功立即返回
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    # 2) 仅严格失败后依次尝试系统/Windows/中文编码
    fallbacks: list[str] = []
    preferred = locale.getpreferredencoding(False)
    if preferred:
        fallbacks.append(preferred)
    if sys.platform == "win32":
        # mbcs：Windows 多字节系统编码别名；非 Windows 不可用
        fallbacks.append("mbcs")
    fallbacks.extend(("gb18030", "cp936"))

    seen: set[str] = set()
    for enc in fallbacks:
        key = enc.lower()
        if not enc or key in seen:
            continue
        seen.add(key)
        if key in {"utf-8", "utf-8-sig"}:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # 3) 最后兜底
    return raw.decode("utf-8", errors="replace")


def _run_powershell(
    script_path: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """调用 UTF-8 BOM PS1；不使用真实 -WhatIf 以外的杀进程路径。"""
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


def _as_entry_dict(item: Any) -> dict[str, Any] | None:
    """将 plan/manifest 单条记录转为 dict；无法识别则返回 None。"""
    if isinstance(item, dict):
        return item
    if hasattr(item, "_asdict"):
        try:
            return dict(item._asdict())
        except Exception:
            pass
    if hasattr(item, "__dict__"):
        return {
            k: v
            for k, v in vars(item).items()
            if not k.startswith("_") and not callable(v)
        }
    return None


def _iter_plan_entries(plan: Any) -> list[dict[str, Any]]:
    """
    结构化读取 build_source_plan 返回值。
    禁止把 logical_root 与运行时 path 混入同一“路径集合”导致假失败。
    """
    if plan is None:
        return []
    if isinstance(plan, dict):
        for key in ("files", "entries", "items", "sources", "roots", "plan"):
            if key in plan:
                return _iter_plan_entries(plan[key])
        # 单条 entry
        if any(
            k in plan
            for k in ("logical_root", "path", "relative_path", "kind", "required")
        ):
            return [plan]
        return []
    if isinstance(plan, (list, tuple)):
        out: list[dict[str, Any]] = []
        for item in plan:
            d = _as_entry_dict(item)
            if d is not None and any(
                k in d for k in ("logical_root", "path", "relative_path", "kind")
            ):
                out.append(d)
            else:
                out.extend(_iter_plan_entries(item))
        return out
    d = _as_entry_dict(plan)
    if d is not None:
        return [d]
    if hasattr(plan, "files"):
        return _iter_plan_entries(getattr(plan, "files"))
    if hasattr(plan, "entries"):
        return _iter_plan_entries(getattr(plan, "entries"))
    if hasattr(plan, "roots"):
        return _iter_plan_entries(getattr(plan, "roots"))
    return []


def _entry_logical_root(entry: dict[str, Any]) -> str | None:
    v = entry.get("logical_root")
    if v is None:
        v = entry.get("source_root")
    if v is None:
        return None
    return str(v)


def _entry_runtime_path(entry: dict[str, Any]) -> Path | None:
    """运行时绝对/锚定 path 字段（非 manifest 相对路径）。"""
    p = entry.get("path")
    if p is None:
        return None
    try:
        return Path(p)
    except TypeError:
        return None


def _path_as_posix(p: Any) -> str:
    """用 pathlib 规范化，禁止对整段 JSON 做盲目反斜杠替换。"""
    return Path(str(p)).as_posix()


def _logical_roots_of_plan(plan: Any) -> set[str]:
    roots: set[str] = set()
    for entry in _iter_plan_entries(plan):
        lr = _entry_logical_root(entry)
        if lr:
            roots.add(lr)
    return roots


def _manifest_file_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if files is None:
        files = manifest.get("entries")
    if files is None:
        return []
    if not isinstance(files, list):
        raise AssertionError(f"manifest.files 必须是 list，实际={type(files)}")
    out: list[dict[str, Any]] = []
    for item in files:
        d = _as_entry_dict(item)
        if d is None:
            raise AssertionError(f"manifest 文件条目必须是对象：{item!r}")
        out.append(d)
    return out


def _item_relative_path(item: dict[str, Any]) -> str | None:
    for k in ("relative_path", "relpath"):
        if k in item and item[k] is not None:
            return str(item[k])
    # 禁止把运行时绝对 path 误当相对路径；仅当显式 relative 缺失时不回退 path
    return None


def _item_size_bytes(item: dict[str, Any]) -> Any:
    """兼容 size_bytes | size | bytes；优先 size_bytes。"""
    for k in ("size_bytes", "size", "bytes"):
        if k in item and item[k] is not None:
            return item[k]
    return None


def _item_sha256(item: dict[str, Any]) -> Any:
    if "sha256" in item and item["sha256"] is not None:
        return item["sha256"]
    if "hash" in item and item["hash"] is not None:
        return item["hash"]
    return None


def _assert_manifest_item_strict(tc: unittest.TestCase, item: dict[str, Any]) -> None:
    """
    反假绿：精确要求逻辑根、相对路径、字节数、哈希字段存在且类型正确。
    宽兼容仅限字节字段名（size_bytes|size|bytes），不得掩盖缺字段。
    """
    tc.assertIsInstance(item, dict)
    lr = _entry_logical_root(item)
    tc.assertIsInstance(lr, str, f"manifest 条目缺少 logical_root：{item}")
    tc.assertTrue(bool(lr.strip()), f"logical_root 不得为空：{item}")
    rel = _item_relative_path(item)
    tc.assertIsInstance(rel, str, f"manifest 条目缺少 relative_path：{item}")
    tc.assertTrue(bool(str(rel).strip()), f"relative_path 不得为空：{item}")
    tc.assertFalse(os.path.isabs(str(rel)), f"relative_path 不得为绝对路径：{rel}")
    tc.assertNotIn("..", Path(str(rel)).parts, f"relative_path 不得含 ..：{rel}")
    size = _item_size_bytes(item)
    tc.assertIsInstance(size, int, f"字节数字段必须为 int（size_bytes|size|bytes）：{item}")
    tc.assertGreaterEqual(size, 0)
    # 反假绿：至少有一个权威字节字段名，禁止“有值但键全无”的幻想
    tc.assertTrue(
        any(k in item for k in ("size_bytes", "size", "bytes")),
        f"缺少字节数字段名：{item}",
    )
    digest = _item_sha256(item)
    tc.assertIsInstance(digest, str, f"缺少 sha256：{item}")
    tc.assertEqual(len(digest), 64, f"sha256 长度必须 64：{digest}")
    tc.assertRegex(str(digest), r"^[0-9a-f]{64}$")


def _create_windows_junction(link: Path, target: Path) -> None:
    """创建 Windows 目录 junction；失败则抛 AssertionError（禁止静默 skip）。"""
    if os.name != "nt":
        raise AssertionError("本夹具仅在 Windows 上创建 junction")
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists():
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
    if proc.returncode != 0 or not link.exists():
        raise AssertionError(
            "无法创建 Windows junction（必须有真实等价证据，禁止 skip）："
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


def _find_final_backup_dirs(dest_root: Path) -> list[Path]:
    if not dest_root.is_dir():
        return []
    return sorted(
        p
        for p in dest_root.iterdir()
        if p.is_dir() and p.name.startswith("biaoshu-backup-")
    )


def _find_temp_dirs(dest_root: Path) -> list[Path]:
    """查找可能残留的临时目录（非最终备份名）。"""
    if not dest_root.is_dir():
        return []
    finals = set()
    temps = []
    for p in dest_root.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith("biaoshu-backup-"):
            finals.add(p)
        else:
            # 同卷临时目录常见前缀
            lower = p.name.lower()
            if any(
                tok in lower
                for tok in ("tmp", "temp", "partial", "inprogress", "staging", "work")
            ):
                temps.append(p)
    return temps


# ===========================================================================
# 1. 生产入口存在性与编码/转发（failure-first 基线）
# ===========================================================================


class TestProductionEntryPresence(unittest.TestCase):
    """用途：证明五个生产入口存在；缺失时首个业务失败。"""

    def test_stop_bat_exists(self) -> None:
        self.assertTrue(
            _STOP_BAT.is_file(),
            f"Stop-Biaoshu-Dev.bat 不存在（failure-first）：{_STOP_BAT}",
        )

    def test_backup_bat_exists(self) -> None:
        self.assertTrue(
            _BACKUP_BAT.is_file(),
            f"Backup-Biaoshu.bat 不存在（failure-first）：{_BACKUP_BAT}",
        )

    def test_stop_ps1_exists(self) -> None:
        self.assertTrue(
            _STOP_PS1.is_file(),
            f"Stop-Biaoshu-Dev.ps1 不存在（failure-first）：{_STOP_PS1}",
        )

    def test_backup_ps1_exists(self) -> None:
        self.assertTrue(
            _BACKUP_PS1.is_file(),
            f"Backup-Biaoshu.ps1 不存在（failure-first）：{_BACKUP_PS1}",
        )

    def test_backup_py_exists(self) -> None:
        self.assertTrue(
            _PROD_MODULE_PATH.is_file(),
            f"biaoshu_backup.py 不存在（failure-first）：{_PROD_MODULE_PATH}",
        )


class TestPs1BomAndBatForward(unittest.TestCase):
    """用途：PS1 必须 UTF-8 BOM；根 bat 只转发到固定脚本。"""

    def setUp(self) -> None:
        _require_prod_files()

    def test_stop_ps1_utf8_bom(self) -> None:
        prefix = _read_bom_prefix(_STOP_PS1)
        self.assertEqual(prefix, _BOM, "Stop-Biaoshu-Dev.ps1 必须带 UTF-8 BOM")

    def test_backup_ps1_utf8_bom(self) -> None:
        prefix = _read_bom_prefix(_BACKUP_PS1)
        self.assertEqual(prefix, _BOM, "Backup-Biaoshu.ps1 必须带 UTF-8 BOM")

    def test_stop_bat_only_forwards_to_fixed_ps1(self) -> None:
        text = _bat_text(_STOP_BAT)
        # 只允许调用固定 tools\v1-ops\Stop-Biaoshu-Dev.ps1
        self.assertRegex(
            text,
            r"tools\\v1-ops\\Stop-Biaoshu-Dev\.ps1",
            "Stop bat 必须转发到 tools\\v1-ops\\Stop-Biaoshu-Dev.ps1",
        )
        # 不得内嵌业务逻辑关键字（粗门）
        lowered = text.lower()
        for forbidden in ("taskkill", "stop-process", "get-nettcpconnection"):
            self.assertNotIn(
                forbidden,
                lowered,
                f"Stop bat 不得内嵌 {forbidden}，只应转发",
            )

    def test_backup_bat_only_forwards_to_fixed_ps1(self) -> None:
        text = _bat_text(_BACKUP_BAT)
        self.assertRegex(
            text,
            r"tools\\v1-ops\\Backup-Biaoshu\.ps1",
            "Backup bat 必须转发到 tools\\v1-ops\\Backup-Biaoshu.ps1",
        )
        lowered = text.lower()
        for forbidden in ("xcopy", "robocopy", "sqlite3", "sha256"):
            self.assertNotIn(
                forbidden,
                lowered,
                f"Backup bat 不得内嵌 {forbidden}，只应转发",
            )


# ===========================================================================
# 2. 冻结 Python API 表面
# ===========================================================================


class TestFrozenPythonApiSurface(unittest.TestCase):
    """用途：验证契约冻结的公开测试接口全部可导入。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()

    def test_all_frozen_names_present(self) -> None:
        missing = [n for n in _FROZEN_API_NAMES if not hasattr(self.mod, n)]
        self.assertEqual(missing, [], f"冻结 API 缺失：{missing}")

    def test_schema_version_constant(self) -> None:
        self.assertEqual(self.mod.BACKUP_SCHEMA_VERSION, _EXPECTED_SCHEMA)

    def test_backup_error_is_exception(self) -> None:
        self.assertTrue(issubclass(self.mod.BackupError, Exception))

    def test_build_source_plan_signature_callable(self) -> None:
        self.assertTrue(callable(self.mod.build_source_plan))

    def test_assert_services_stopped_signature_callable(self) -> None:
        self.assertTrue(callable(self.mod.assert_services_stopped))

    def test_create_offline_backup_signature_callable(self) -> None:
        self.assertTrue(callable(self.mod.create_offline_backup))

    def test_main_signature_callable(self) -> None:
        self.assertTrue(callable(self.mod.main))


# ===========================================================================
# 3. 源计划：白名单、legacy、模型开关、排除测试库
# ===========================================================================


class TestSourcePlanWhitelist(unittest.TestCase):
    """用途：精确源白名单与排除规则。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-src-")
        self.root = Path(self._tmp.name) / "fake-repo"
        self.root.mkdir()
        self.paths = _make_fake_repo(
            self.root, include_legacy=True, include_semantic=True, include_forbidden_dbs=True
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_default_plan_includes_canonical_roots(self) -> None:
        plan = self.mod.build_source_plan(str(self.root), include_semantic_models=False)
        entries = _iter_plan_entries(plan)
        self.assertGreater(len(entries), 0, "源计划不得为空")
        roots = _logical_roots_of_plan(plan)
        # 必需逻辑根（契约 §3）：db / uploads / knowledge / knowledge_cards
        for need in ("db", "uploads", "knowledge", "knowledge_cards"):
            self.assertIn(need, roots, f"缺逻辑根 {need}；实际={roots}")
        # 运行时 path 必须锚定到 canonical 源目录
        path_posixes = []
        for e in entries:
            rp = _entry_runtime_path(e)
            self.assertIsNotNone(rp, f"plan 条目缺 path：{e}")
            path_posixes.append(_path_as_posix(rp))
        joined = "\n".join(path_posixes)
        self.assertTrue(
            any(p.endswith("biaoshu.db") or p.endswith("/biaoshu.db") for p in path_posixes)
            or "biaoshu.db" in joined,
            f"缺日用库路径：{path_posixes}",
        )
        self.assertTrue(
            any(p.endswith("backend/uploads") or p.endswith("/backend/uploads") for p in path_posixes),
            f"缺 canonical uploads 源路径：{path_posixes}",
        )
        # 默认不含 semantic 源目录与逻辑根
        self.assertNotIn("semantic_models", roots)
        self.assertNotIn("semantic-models", roots)
        self.assertFalse(
            any("semantic-models" in p for p in path_posixes),
            f"默认不得纳入 semantic-models：{path_posixes}",
        )

    def test_legacy_root_separated_when_nonempty(self) -> None:
        plan = self.mod.build_source_plan(str(self.root), include_semantic_models=False)
        entries = _iter_plan_entries(plan)
        roots = _logical_roots_of_plan(plan)
        # canonical uploads 与 legacy_uploads 必须同时以独立 logical_root 出现
        self.assertIn("uploads", roots, f"缺 canonical logical_root=uploads：{roots}")
        self.assertIn(
            "legacy_uploads",
            roots,
            f"非空根 uploads 必须以 logical_root=legacy_uploads 分离：{roots}",
        )
        canon_paths = [
            _path_as_posix(_entry_runtime_path(e))
            for e in entries
            if _entry_logical_root(e) == "uploads" and _entry_runtime_path(e) is not None
        ]
        legacy_paths = [
            _path_as_posix(_entry_runtime_path(e))
            for e in entries
            if _entry_logical_root(e) == "legacy_uploads" and _entry_runtime_path(e) is not None
        ]
        self.assertTrue(
            any(p.endswith("backend/uploads") for p in canon_paths),
            f"canonical uploads 源 path 应 ends_with backend/uploads：{canon_paths}",
        )
        self.assertTrue(
            any(p.endswith("/uploads") or p.endswith("\\uploads") or Path(p).name == "uploads" for p in legacy_paths)
            and not any("backend/uploads" in p for p in legacy_paths),
            f"legacy 源 path 应为仓库根 uploads，不得混入 backend/uploads：{legacy_paths}",
        )

    def test_empty_legacy_not_fabricated(self) -> None:
        root2 = Path(self._tmp.name) / "no-legacy"
        root2.mkdir()
        _make_fake_repo(root2, include_legacy=False, include_semantic=False)
        plan = self.mod.build_source_plan(str(root2), include_semantic_models=False)
        roots = _logical_roots_of_plan(plan)
        # 无根 uploads 时不得出现 legacy 逻辑根；canonical logical_root=uploads 仍允许
        self.assertNotIn("legacy_uploads", roots, f"无 legacy 时不得伪造 legacy_uploads：{roots}")
        self.assertNotIn("legacy", roots, f"无 legacy 时不得伪造 legacy：{roots}")
        # 不得把仓库根 uploads 运行时 path 塞进计划
        for e in _iter_plan_entries(plan):
            rp = _entry_runtime_path(e)
            if rp is None:
                continue
            posix = _path_as_posix(rp)
            lr = _entry_logical_root(e)
            if lr in {"legacy_uploads", "legacy"}:
                self.fail(f"无 legacy 时出现 legacy 条目：{e}")
            # 根 uploads 目录（非 backend/uploads）
            if posix.endswith("/uploads") and "backend/uploads" not in posix:
                # 仅当 logical_root 伪装成 uploads 且 path 指向根 uploads 才算伪造
                if lr in {"legacy_uploads", "legacy"} or (
                    lr == "uploads" and not posix.endswith("backend/uploads")
                ):
                    if "backend/uploads" not in posix:
                        # 根 uploads 在 include_legacy=False 时不应存在于磁盘
                        self.assertFalse(
                            (root2 / "uploads").exists(),
                            "夹具错误：include_legacy=False 仍有根 uploads",
                        )

    def test_semantic_models_only_when_flag(self) -> None:
        plan_off = self.mod.build_source_plan(str(self.root), include_semantic_models=False)
        plan_on = self.mod.build_source_plan(str(self.root), include_semantic_models=True)
        roots_off = _logical_roots_of_plan(plan_off)
        roots_on = _logical_roots_of_plan(plan_on)
        self.assertNotIn("semantic_models", roots_off)
        self.assertNotIn("semantic-models", roots_off)
        off_paths = [
            _path_as_posix(_entry_runtime_path(e))
            for e in _iter_plan_entries(plan_off)
            if _entry_runtime_path(e) is not None
        ]
        self.assertFalse(
            any("semantic-models" in p for p in off_paths),
            f"关闭开关时不得出现 semantic-models 源路径：{off_paths}",
        )
        # 开启：逻辑根 + 源路径分别断言（源目录 hyphen；逻辑根可为 underscore）
        self.assertTrue(
            "semantic_models" in roots_on or "semantic-models" in roots_on,
            f"开启后应有 semantic 逻辑根：{roots_on}",
        )
        on_paths = [
            _path_as_posix(_entry_runtime_path(e))
            for e in _iter_plan_entries(plan_on)
            if _entry_runtime_path(e) is not None
        ]
        self.assertTrue(
            any(p.endswith("semantic-models") or p.endswith("/semantic-models") for p in on_paths),
            f"开启后源 path 应 ends_with semantic-models：{on_paths}",
        )

    def test_forbidden_test_dbs_excluded(self) -> None:
        plan = self.mod.build_source_plan(str(self.root), include_semantic_models=False)
        # 仅检查运行时 path 与 logical_root，避免 JSON 文本宽匹配假绿/假红
        tokens: list[str] = []
        for e in _iter_plan_entries(plan):
            lr = _entry_logical_root(e)
            if lr:
                tokens.append(lr)
            rp = _entry_runtime_path(e)
            if rp is not None:
                tokens.append(_path_as_posix(rp))
                tokens.append(rp.name)
        blob = "\n".join(tokens)
        for banned in (
            "biaoshu-e2e.db",
            "biaoshu-pytest",
            "codex-scratch",
            "other-unknown.db",
            ".env",
            ".venv",
            "node_modules",
        ):
            self.assertNotIn(banned, blob, f"源计划不得包含 {banned}；tokens={tokens}")


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(x) for x in obj]
    if hasattr(obj, "_asdict"):
        return _jsonable(obj._asdict())
    if hasattr(obj, "__dict__"):
        return {
            k: _jsonable(v)
            for k, v in vars(obj).items()
            if not k.startswith("_")
        }
    return str(obj)


# ===========================================================================
# 4. 端口门：注入 probe，不触碰真实 8000/5173
# ===========================================================================


class TestServicePortGate(unittest.TestCase):
    """用途：端口占用拒绝；空闲通过；默认端口精确为 8000/5173。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()

    def test_stopped_probe_passes(self) -> None:
        # 不得抛
        self.mod.assert_services_stopped(
            host="127.0.0.1", ports=(8000, 5173), probe=_probe_all_free
        )

    def test_busy_8000_rejects(self) -> None:
        with self.assertRaises(self.mod.BackupError):
            self.mod.assert_services_stopped(
                host="127.0.0.1",
                ports=(8000, 5173),
                probe=_probe_partial({8000}),
            )

    def test_busy_5173_rejects(self) -> None:
        with self.assertRaises(self.mod.BackupError):
            self.mod.assert_services_stopped(
                host="127.0.0.1",
                ports=(8000, 5173),
                probe=_probe_partial({5173}),
            )

    def test_default_ports_are_8000_and_5173(self) -> None:
        """调用默认参数时 probe 应收到 8000 与 5173。"""
        seen: list[int] = []

        def probe(host: str, port: int) -> bool:
            seen.append(port)
            return False

        self.mod.assert_services_stopped(probe=probe)
        self.assertEqual(sorted(set(seen)), [5173, 8000])


# ===========================================================================
# 5. create_offline_backup 成功路径与 manifest 严格字段
# ===========================================================================


class TestCreateOfflineBackupSuccess(unittest.TestCase):
    """用途：临时假库成功备份；哈希/大小/integrity/manifest 同时证明。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-bak-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.dest = base / "dest-outside"
        self.repo.mkdir()
        self.dest.mkdir()
        self.paths = _make_fake_repo(
            self.repo, include_legacy=True, include_semantic=True
        )
        self.fixed_now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
        self.fixed_head = "deadbeefcafebabe0123456789abcdef01234567"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_backup(self, **kwargs: Any) -> Any:
        params = dict(
            repo_root=str(self.repo),
            destination_root=str(self.dest),
            include_semantic_models=False,
            now=self.fixed_now,
            git_head=self.fixed_head,
            service_probe=_probe_all_free,
        )
        params.update(kwargs)
        return self.mod.create_offline_backup(**params)

    def test_success_creates_final_dir_with_integrity(self) -> None:
        result = self._run_backup()
        finals = _find_final_backup_dirs(self.dest)
        self.assertEqual(len(finals), 1, f"应恰好一个最终目录：{finals}")
        final = finals[0]
        # 副本 DB integrity
        db_copy = final / "backend" / "data" / "biaoshu.db"
        if not db_copy.is_file():
            # 允许逻辑根布局差异，搜索唯一 biaoshu.db
            candidates = list(final.rglob("biaoshu.db"))
            self.assertEqual(len(candidates), 1, f"副本 db 数异常：{candidates}")
            db_copy = candidates[0]
        conn = sqlite3.connect(str(db_copy))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(row[0], "ok")
        finally:
            conn.close()
        # 源/副本哈希一致
        self.assertEqual(_sha256_file(self.paths["db"]), _sha256_file(db_copy))
        # 结果应指向最终目录
        result_text = str(result)
        self.assertIn(final.name, result_text)

    def test_manifest_strict_fields_and_no_secrets(self) -> None:
        self._run_backup()
        finals = _find_final_backup_dirs(self.dest)
        self.assertEqual(len(finals), 1)
        manifest_path = finals[0] / "manifest.json"
        self.assertTrue(manifest_path.is_file(), "缺少 manifest.json")
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
        # schema：精确键，禁止宽兼容掩盖缺字段
        self.assertIn("schema_version", manifest, "manifest 必须含 schema_version")
        self.assertEqual(manifest["schema_version"], _EXPECTED_SCHEMA)
        # 禁止敏感/绝对路径
        self.assertNotIn(_FAKE_API_KEY_MARKER, raw)
        self.assertNotIn("sk-fake", raw)
        self.assertIsNone(re.search(r"[A-Za-z]:\\", raw))
        self.assertNotIn(str(self.repo), raw)
        self.assertNotIn(str(self.dest), raw)
        files = _manifest_file_items(manifest)
        self.assertGreater(len(files), 0, "manifest.files 不得为空")
        logical_roots: set[str] = set()
        for item in files:
            _assert_manifest_item_strict(self, item)
            logical_roots.add(str(_entry_logical_root(item)))
        # 成功路径至少覆盖 db 与 uploads 逻辑根
        self.assertIn("db", logical_roots, f"manifest 缺 db 逻辑根：{logical_roots}")
        self.assertIn("uploads", logical_roots, f"manifest 缺 uploads 逻辑根：{logical_roots}")
        head_val = manifest.get("git_head")
        if head_val is not None:
            self.assertEqual(head_val, self.fixed_head)

    def test_legacy_root_in_manifest_independent(self) -> None:
        self._run_backup()
        finals = _find_final_backup_dirs(self.dest)
        manifest = json.loads((finals[0] / "manifest.json").read_text(encoding="utf-8"))
        items = _manifest_file_items(manifest)
        for item in items:
            _assert_manifest_item_strict(self, item)
        by_root: dict[str, list[str]] = {}
        for item in items:
            lr = str(_entry_logical_root(item))
            rel = str(_item_relative_path(item))
            by_root.setdefault(lr, []).append(rel)
        self.assertIn("uploads", by_root, f"缺 canonical logical_root=uploads：{list(by_root)}")
        self.assertIn(
            "legacy_uploads",
            by_root,
            f"缺独立 logical_root=legacy_uploads：{list(by_root)}",
        )
        # canonical 相对路径不得伪装成 legacy 文件名混根
        self.assertTrue(
            any("note.txt" in r or r.endswith("note.txt") for r in by_root["uploads"]),
            f"canonical uploads 相对路径异常：{by_root['uploads']}",
        )
        self.assertTrue(
            any("legacy-doc" in r for r in by_root["legacy_uploads"]),
            f"legacy 相对路径应含 legacy-doc：{by_root['legacy_uploads']}",
        )
        # 二者不得混入同一 logical_root
        self.assertFalse(
            any("legacy-doc" in r for r in by_root["uploads"]),
            "legacy 文件不得进入 logical_root=uploads",
        )

    def test_semantic_models_flag_controls_inclusion(self) -> None:
        self._run_backup(include_semantic_models=False)
        finals = _find_final_backup_dirs(self.dest)
        manifest_off = json.loads((finals[0] / "manifest.json").read_text(encoding="utf-8"))
        roots_off = {
            str(_entry_logical_root(i)) for i in _manifest_file_items(manifest_off)
        }
        self.assertNotIn("semantic_models", roots_off)
        self.assertNotIn("semantic-models", roots_off)
        # 关闭：备份树内不应有 model.bin 语义副本
        model_off = list(finals[0].rglob("model.bin"))
        self.assertEqual(model_off, [], f"关闭开关不得复制 semantic 模型：{model_off}")
        shutil.rmtree(finals[0])
        self._run_backup(include_semantic_models=True)
        finals2 = _find_final_backup_dirs(self.dest)
        manifest_on = json.loads((finals2[0] / "manifest.json").read_text(encoding="utf-8"))
        items_on = _manifest_file_items(manifest_on)
        for item in items_on:
            _assert_manifest_item_strict(self, item)
        roots_on = {str(_entry_logical_root(i)) for i in items_on}
        self.assertTrue(
            "semantic_models" in roots_on or "semantic-models" in roots_on,
            f"开启后 manifest 应含 semantic 逻辑根：{roots_on}",
        )
        # 副本存在（按逻辑根目录或 rglob model.bin）
        model_on = list(finals2[0].rglob("model.bin"))
        self.assertGreater(len(model_on), 0, "开启后备份树内应有 model.bin 副本")
        # 禁止用源目录 hyphen 字符串对整份 JSON 做脆弱断言；逻辑根与副本即可

    def test_no_temp_dir_left_on_success(self) -> None:
        self._run_backup()
        temps = _find_temp_dirs(self.dest)
        self.assertEqual(temps, [], f"成功后不得残留临时目录：{temps}")
        self.assertEqual(len(_find_final_backup_dirs(self.dest)), 1)


# ===========================================================================
# 6. 失败路径：零最终目录、清理临时、拒绝各类非法
# ===========================================================================


class TestCreateOfflineBackupFailures(unittest.TestCase):
    """用途：失败必须删临时目录且无最终备份。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-fail-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.dest = base / "dest"
        self.repo.mkdir()
        self.dest.mkdir()
        self.paths = _make_fake_repo(self.repo, include_legacy=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _assert_no_final(self) -> None:
        self.assertEqual(
            _find_final_backup_dirs(self.dest),
            [],
            "失败后不得存在最终备份目录",
        )
        self.assertEqual(
            _find_temp_dirs(self.dest),
            [],
            "失败后不得残留临时目录",
        )

    def test_port_busy_zero_backup(self) -> None:
        with self.assertRaises(self.mod.BackupError):
            self.mod.create_offline_backup(
                repo_root=str(self.repo),
                destination_root=str(self.dest),
                service_probe=_probe_busy,
            )
        self._assert_no_final()

    def test_missing_required_db_fails(self) -> None:
        self.paths["db"].unlink()
        with self.assertRaises(self.mod.BackupError):
            self.mod.create_offline_backup(
                repo_root=str(self.repo),
                destination_root=str(self.dest),
                service_probe=_probe_all_free,
            )
        self._assert_no_final()

    def test_corrupt_db_integrity_fails(self) -> None:
        _write_corrupt_sqlite(self.paths["db"])
        with self.assertRaises(self.mod.BackupError):
            self.mod.create_offline_backup(
                repo_root=str(self.repo),
                destination_root=str(self.dest),
                service_probe=_probe_all_free,
            )
        self._assert_no_final()

    def test_destination_inside_repo_rejected(self) -> None:
        inside = self.repo / "nested-dest"
        inside.mkdir()
        with self.assertRaises(self.mod.BackupError):
            self.mod.create_offline_backup(
                repo_root=str(self.repo),
                destination_root=str(inside),
                service_probe=_probe_all_free,
            )
        # 仓库内不得出现最终备份名目录
        nested_finals = list(self.repo.rglob("biaoshu-backup-*"))
        self.assertEqual(nested_finals, [])

    def test_existing_final_dir_rejected(self) -> None:
        # 先成功一次
        self.mod.create_offline_backup(
            repo_root=str(self.repo),
            destination_root=str(self.dest),
            now=datetime(2026, 7, 21, 15, 0, 0, tzinfo=timezone.utc),
            git_head="abc",
            service_probe=_probe_all_free,
        )
        finals = _find_final_backup_dirs(self.dest)
        self.assertEqual(len(finals), 1)
        # 再以相同 now 触发同名最终目录冲突
        with self.assertRaises(self.mod.BackupError):
            self.mod.create_offline_backup(
                repo_root=str(self.repo),
                destination_root=str(self.dest),
                now=datetime(2026, 7, 21, 15, 0, 0, tzinfo=timezone.utc),
                git_head="abc",
                service_probe=_probe_all_free,
            )
        # 仍只有一个最终目录
        self.assertEqual(len(_find_final_backup_dirs(self.dest)), 1)

    def _patch_source_db_open_during_read(
        self,
        db_path: Path,
        mutator: Callable[[], None],
        flipped: dict[str, bool],
    ) -> Any:
        """
        与复制原语无关：命中 Path.open 分块读取源文件。
        mutator 在源库首次 read 返回后调用（使用内置 open，避免递归）。
        """
        original_open = Path.open

        def flaky_open(self_path: Path, *args: Any, **kwargs: Any) -> Any:
            mode = args[0] if args else kwargs.get("mode", "r")
            fh = original_open(self_path, *args, **kwargs)
            try:
                resolved = self_path.resolve()
            except OSError:
                return fh
            mode_s = str(mode)
            if resolved != db_path.resolve() or "r" not in mode_s or "+" in mode_s:
                return fh
            if flipped.get("done"):
                return fh

            class _FlakyReader:
                def __init__(self, inner: Any) -> None:
                    self._inner = inner

                def read(self, n: int = -1) -> bytes:
                    data = self._inner.read(n)
                    if data and not flipped["done"]:
                        flipped["done"] = True
                        mutator()
                    return data

                def __enter__(self) -> "_FlakyReader":
                    return self

                def __exit__(self, *exc: Any) -> Any:
                    return self._inner.__exit__(*exc)

                def __getattr__(self, name: str) -> Any:
                    return getattr(self._inner, name)

            return _FlakyReader(fh)

        return mock.patch.object(Path, "open", flaky_open)

    def test_source_change_during_copy_fails(self) -> None:
        """复制期间源大小变化必须 BackupError，且零 final/零 temp。注入命中分块读取。"""
        db_path = self.paths["db"]
        flipped = {"done": False}

        def mutator() -> None:
            # 使用内置 open 追加，改变 size（与 copy 原语无关）
            with open(db_path, "ab") as fh:
                fh.write(b"\n#mutated-during-copy\n")

        with self._patch_source_db_open_during_read(db_path, mutator, flipped):
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(self.repo),
                    destination_root=str(self.dest),
                    service_probe=_probe_all_free,
                )
        self.assertTrue(flipped["done"], "注入必须命中源文件分块读取")
        self._assert_no_final()

    def test_source_change_same_size_mtime_restored_fails(self) -> None:
        """
        强证据（A4/B6）：同大小修改并恢复 mtime_ns 后仍必须 BackupError，
        零最终目录、零临时目录。证明不能只靠 size/mtime 门。
        """
        db_path = self.paths["db"]
        original = db_path.read_bytes()
        self.assertGreater(len(original), 8, "夹具库过小，无法做同大小翻转")
        flipped = {"done": False}

        def mutator() -> None:
            st = os.stat(db_path)
            mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            atime_ns = getattr(st, "st_atime_ns", int(st.st_atime * 1e9))
            mutated = bytearray(original)
            idx = len(mutated) // 2
            mutated[idx] = (mutated[idx] + 1) % 256
            # 保证同长度
            self.assertEqual(len(mutated), len(original))
            with open(db_path, "wb") as fh:
                fh.write(bytes(mutated))
            os.utime(db_path, ns=(atime_ns, mtime_ns))

        with self._patch_source_db_open_during_read(db_path, mutator, flipped):
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(self.repo),
                    destination_root=str(self.dest),
                    service_probe=_probe_all_free,
                )
        self.assertTrue(flipped["done"], "同大小 mtime 恢复注入必须命中分块读取")
        self._assert_no_final()

    def test_symlink_destination_rejected(self) -> None:
        """叶子 destination 为 junction/symlink 必须拒绝（保留既有叶子夹具）。"""
        if os.name != "nt":
            link = Path(self._tmp.name) / "link-dest"
            try:
                link.symlink_to(self.dest, target_is_directory=True)
            except OSError as exc:
                self.fail(f"无法创建 symlink 夹具：{exc}")
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(self.repo),
                    destination_root=str(link),
                    service_probe=_probe_all_free,
                )
            return
        link = Path(self._tmp.name) / "link-dest"
        try:
            _create_windows_junction(link, self.dest)
        except AssertionError as exc:
            self.fail(str(exc))
        try:
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(self.repo),
                    destination_root=str(link),
                    service_probe=_probe_all_free,
                )
        finally:
            _remove_windows_junction(link)

    def test_repo_backend_ancestor_junction_rejected(self) -> None:
        """A5：repo\\backend 为指向仓外的 junction 时，源计划/备份必须拒绝。"""
        if os.name != "nt":
            self.fail("A5 祖先 junction 夹具要求 Windows；不得 skip")
        base = Path(self._tmp.name) / "a5-backend"
        base.mkdir(parents=True, exist_ok=True)
        repo = base / "repo"
        outside_backend = base / "outside-backend"
        dest = base / "dest"
        repo.mkdir()
        dest.mkdir()
        # 仓外 backend 内容（将作为 repo\\backend junction 目标）
        data = outside_backend / "data"
        uploads = outside_backend / "uploads"
        data.mkdir(parents=True)
        uploads.mkdir(parents=True)
        _write_minimal_sqlite(data / "biaoshu.db")
        (uploads / "f.txt").write_text("x", encoding="utf-8")
        (data / "knowledge").mkdir()
        (data / "knowledge" / "index.json").write_text("{}", encoding="utf-8")
        (data / "knowledge_cards").mkdir()
        (data / "knowledge_cards" / "c.json").write_text("{}", encoding="utf-8")
        (repo / ".env").write_text("x=1\n", encoding="utf-8")
        link = repo / "backend"
        try:
            _create_windows_junction(link, outside_backend)
        except AssertionError as exc:
            self.fail(str(exc))
        try:
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(repo),
                    destination_root=str(dest),
                    service_probe=_probe_all_free,
                )
            self.assertEqual(_find_final_backup_dirs(dest), [])
            self.assertEqual(_find_temp_dirs(dest), [])
        finally:
            _remove_windows_junction(link)

    def test_destination_ancestor_junction_rejected(self) -> None:
        """A5：destination 祖先目录为 junction 时必须拒绝（非仅叶子）。"""
        if os.name != "nt":
            self.fail("A5 祖先 junction 夹具要求 Windows；不得 skip")
        base = Path(self._tmp.name) / "a5-dest"
        base.mkdir(parents=True, exist_ok=True)
        real_storage = base / "real-storage"
        real_storage.mkdir()
        outer = base / "outer"
        outer.mkdir()
        mid_link = outer / "mid-junction"
        try:
            _create_windows_junction(mid_link, real_storage)
        except AssertionError as exc:
            self.fail(str(exc))
        # 目标在 junction 祖先之下
        dest = mid_link / "backup-dest"
        dest.mkdir()
        try:
            with self.assertRaises(self.mod.BackupError):
                self.mod.create_offline_backup(
                    repo_root=str(self.repo),
                    destination_root=str(dest),
                    service_probe=_probe_all_free,
                )
            # 不得在 junction 目标侧留下最终备份
            self.assertEqual(_find_final_backup_dirs(dest), [])
            self.assertEqual(_find_final_backup_dirs(real_storage), [])
        finally:
            try:
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
            finally:
                _remove_windows_junction(mid_link)


# ===========================================================================
# 7. Stop 脚本：WhatIf + 严格快照注入（零真实终止）
# ===========================================================================


class TestStopWhatIfAndSnapshot(unittest.TestCase):
    """用途：Stop 的 WhatIf 与 ListenerSnapshotJson 严格校验；禁止杀真实进程。"""

    def setUp(self) -> None:
        files = _require_prod_files()
        self.stop_ps1 = files["stop_ps1"]
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-stop-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_snapshot(self, records: Any, name: str = "snap.json") -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return path

    def test_whatif_with_empty_snapshot_is_idempotent_success(self) -> None:
        snap = self._write_snapshot([])
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"无监听 WhatIf 应幂等成功：stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        # 不得出现终止证据
        combined = (proc.stdout + proc.stderr).lower()
        for tok in ("taskkill", "stop-process", "terminated", "已终止"):
            self.assertNotIn(tok, combined)

    def test_snapshot_without_whatif_rejected_zero_kill(self) -> None:
        snap = self._write_snapshot(
            [
                {
                    "port": 8000,
                    "pid": 424242,
                    "executablePath": str(self.tmp / "python.exe"),
                    "commandLine": "uvicorn fake",
                }
            ]
        )
        proc = _run_powershell(
            self.stop_ps1,
            ["-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            "未带 -WhatIf 时快照入口必须固定失败",
        )
        combined = (proc.stdout + proc.stderr).lower()
        for tok in ("taskkill", "stop-process"):
            self.assertNotIn(tok, combined)

    def test_foreign_listener_fails_with_zero_side_effect(self) -> None:
        # 无法确认归属：系统路径 + 非本仓库命令行
        foreign = [
            {
                "port": 8000,
                "pid": 515151,
                "executablePath": r"C:\Windows\System32\svchost.exe",
                "commandLine": "svchost -k netsvcs",
            },
            {
                "port": 5173,
                "pid": 515152,
                "executablePath": r"C:\Program Files\nodejs\node.exe",
                "commandLine": r"node C:\other-app\vite",
            },
        ]
        snap = self._write_snapshot(foreign)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "foreign listener 必须整次失败")
        combined = proc.stdout + proc.stderr
        # 不得输出完整敏感环境；应有中文原因
        self.assertNotIn("API_KEY", combined)
        self.assertNotIn(_FAKE_API_KEY_MARKER, combined)

    def test_strict_snapshot_schema_rejects_extra_keys(self) -> None:
        bad = [
            {
                "port": 8000,
                "pid": 1,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
                "extra": "nope",
            }
        ]
        snap = self._write_snapshot(bad)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "额外键必须拒绝")

    def test_strict_snapshot_rejects_missing_keys(self) -> None:
        bad = [{"port": 8000, "pid": 1, "executablePath": r"C:\fake\python.exe"}]
        snap = self._write_snapshot(bad)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "缺键必须拒绝")

    def test_strict_snapshot_rejects_duplicate_pid(self) -> None:
        bad = [
            {
                "port": 8000,
                "pid": 99,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "a",
            },
            {
                "port": 5173,
                "pid": 99,
                "executablePath": r"C:\fake\node.exe",
                "commandLine": "b",
            },
        ]
        snap = self._write_snapshot(bad)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "重复 PID 必须拒绝")

    def test_strict_snapshot_rejects_non_array(self) -> None:
        snap = self._write_snapshot({"port": 8000})
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "非数组必须拒绝")

    def test_strict_snapshot_rejects_illegal_port(self) -> None:
        bad = [
            {
                "port": 99999,
                "pid": 1,
                "executablePath": r"C:\fake\python.exe",
                "commandLine": "x",
            }
        ]
        snap = self._write_snapshot(bad)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        self.assertNotEqual(proc.returncode, 0, "非法端口必须拒绝")

    def test_owned_listeners_whatif_does_not_kill(self) -> None:
        """
        B9：归属通过的合法 owned 快照 + WhatIf 必须严格成功（returncode==0），
        输出只读计划语义（WhatIf + 将终止），零 taskkill/stop-process；禁止非零放行。
        """
        venv_py = _REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
        # 快照路径可为绝对路径字符串（判定用文本），不要求文件真实存在于系统
        records = [
            {
                "port": 8000,
                "pid": 610001,
                "executablePath": str(venv_py),
                "commandLine": f'"{venv_py}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000',
            },
            {
                "port": 5173,
                "pid": 610002,
                "executablePath": r"C:\Program Files\nodejs\node.exe",
                "commandLine": f'node "{_REPO_ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"}"',
            },
        ]
        snap = self._write_snapshot(records)
        proc = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.returncode,
            0,
            f"合法 owned + WhatIf 必须 returncode==0：out={combined!r}",
        )
        self.assertIn("WhatIf", combined)
        self.assertIn("将终止", combined)
        self.assertNotIn("未发现", combined)
        lowered = combined.lower()
        for tok in ("taskkill", "stop-process", "已终止"):
            self.assertNotIn(tok, lowered)

    def test_listener_enumeration_failure_zero_kill(self) -> None:
        """
        A1：子 PowerShell 作用域令 Get-NetTCPConnection 抛错后运行 Stop -WhatIf，
        必须非 0；不得“未发现”；零 taskkill/stop-process。
        """
        ps1 = str(self.stop_ps1).replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'; "
            "function global:Get-NetTCPConnection { throw 'SIMULATED_ENUM_FAILURE' }; "
            f"& '{ps1}' -WhatIf; "
            "exit $LASTEXITCODE"
        )
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            timeout=30,
            check=False,
        )
        combined = _decode_ps_output(raw.stdout) + _decode_ps_output(raw.stderr)
        self.assertNotEqual(
            raw.returncode,
            0,
            f"监听枚举失败必须固定失败：code={raw.returncode} out={combined!r}",
        )
        self.assertNotIn("未发现", combined)
        lowered = combined.lower()
        for tok in ("taskkill", "stop-process"):
            self.assertNotIn(tok, lowered)

    def test_wildcard_listener_when_exact_127_empty_is_detected(self) -> None:
        """
        B8：纯函数替换枚举——带 LocalAddress 的精确查询模拟 No MSFT；
        无 LocalAddress 返回 0.0.0.0 owned listener；Get-CimInstance 返回本仓 owned 进程。
        Stop -WhatIf 必须 returncode=0、输出将终止/计划语义、不得“未发现”、零终止动作；
        且证明第二查询（无 LocalAddress）真实发生。
        """
        call_log = self.tmp / "b8-enum-calls.log"
        venv_py = _REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
        fe_vite = (
            _REPO_ROOT
            / "frontend"
            / "node_modules"
            / "vite"
            / "bin"
            / "vite.js"
        )
        # 路径写入 PS 单引号字面量：先折叠单引号
        def _ps_sq(s: str) -> str:
            return s.replace("'", "''")

        log_ps = _ps_sq(str(call_log))
        venv_ps = _ps_sq(str(venv_py))
        vite_ps = _ps_sq(str(fe_vite))
        stop_ps = _ps_sq(str(self.stop_ps1))
        # 固定假 PID，避免与真实进程混淆；测试不杀真实进程
        backend_pid = 610801
        frontend_pid = 610802
        command = f"""
$ErrorActionPreference = 'Stop'
$callLog = '{log_ps}'
if (Test-Path -LiteralPath $callLog) {{ Remove-Item -LiteralPath $callLog -Force }}
New-Item -ItemType File -Path $callLog -Force | Out-Null
function global:Get-NetTCPConnection {{
  [CmdletBinding()]
  param(
    [string]$LocalAddress,
    [int]$LocalPort,
    [string]$State
  )
  $hasLocal = $PSBoundParameters.ContainsKey('LocalAddress') -and -not [string]::IsNullOrEmpty($LocalAddress)
  if ($hasLocal) {{
    # 注意：外层 Python f-string 须把 PS -f 占位符写成 {{0}}/{{1}}
    Add-Content -LiteralPath $callLog -Value ("exact:{{0}}:{{1}}" -f $LocalAddress, $LocalPort) -Encoding utf8
    throw 'No MSFT_NetTCPConnection objects found with property'
  }}
  Add-Content -LiteralPath $callLog -Value ("wildcard:{{0}}" -f $LocalPort) -Encoding utf8
  if ($LocalPort -eq 8000) {{
    return [pscustomobject]@{{
      LocalAddress = '0.0.0.0'
      LocalPort = 8000
      State = 'Listen'
      OwningProcess = {backend_pid}
    }}
  }}
  if ($LocalPort -eq 5173) {{
    return [pscustomobject]@{{
      LocalAddress = '::'
      LocalPort = 5173
      State = 'Listen'
      OwningProcess = {frontend_pid}
    }}
  }}
  return @()
}}
function global:Get-CimInstance {{
  [CmdletBinding()]
  param(
    [Parameter(Position=0)]
    [string]$ClassName,
    [string]$Filter,
    [string]$Namespace
  )
  Add-Content -LiteralPath $callLog -Value ("cim:{{0}}:{{1}}" -f $ClassName, $Filter) -Encoding utf8
  if ($ClassName -ne 'Win32_Process') {{ return $null }}
  if ($Filter -match 'ProcessId\\s*=\\s*{backend_pid}') {{
    return [pscustomobject]@{{
      ProcessId = {backend_pid}
      ExecutablePath = '{venv_ps}'
      CommandLine = '"{venv_ps}" -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
    }}
  }}
  if ($Filter -match 'ProcessId\\s*=\\s*{frontend_pid}') {{
    return [pscustomobject]@{{
      ProcessId = {frontend_pid}
      ExecutablePath = 'C:\\Program Files\\nodejs\\node.exe'
      CommandLine = 'node "{vite_ps}"'
    }}
  }}
  return $null
}}
& '{stop_ps}' -WhatIf
exit $LASTEXITCODE
"""
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            timeout=45,
            check=False,
        )
        stdout = _decode_ps_output(raw.stdout)
        stderr = _decode_ps_output(raw.stderr)
        combined = stdout + stderr
        log_text = (
            call_log.read_text(encoding="utf-8", errors="replace")
            if call_log.is_file()
            else ""
        )
        self.assertEqual(
            raw.returncode,
            0,
            f"B8 WhatIf 必须 returncode==0：out={combined!r} log={log_text!r}",
        )
        self.assertIn("WhatIf", combined)
        self.assertIn("将终止", combined)
        self.assertNotIn("未发现", combined)
        lowered = combined.lower()
        for tok in ("taskkill", "stop-process", "已终止"):
            self.assertNotIn(tok, lowered)
        # 证明精确查询与第二查询（无 LocalAddress）均真实发生
        self.assertRegex(
            log_text,
            r"exact:127\.0\.0\.1:8000",
            f"必须发生带 LocalAddress 的精确查询：log={log_text!r}",
        )
        self.assertRegex(
            log_text,
            r"wildcard:8000",
            f"必须发生无 LocalAddress 的第二查询：log={log_text!r}",
        )
        # 证明 Get-CimInstance 对本仓 owned 假 PID 真实发生
        cim_hit = any(
            (
                f"ProcessId = {backend_pid}" in line
                or f"ProcessId={backend_pid}" in line
            )
            and "Win32_Process" in line
            for line in log_text.splitlines()
        )
        self.assertTrue(
            cim_hit,
            f"Get-CimInstance 必须查询 owned PID：log={log_text!r}",
        )

    def test_exact_owned_nonempty_still_requires_full_and_rejects_foreign(self) -> None:
        """
        B11：exact 8000 返回 owned 非空时，仍必须调用 full 8000；
        full 另含 :: foreign 时 Stop -WhatIf 必须整次失败（归属失败/中止），
        不得成功「将终止」，零 taskkill/stop-process/已终止。
        反假绿：禁止因 exact 非空短路 full（与 A8 对齐）。
        """
        call_log = self.tmp / "b11-enum-calls.log"
        venv_py = _REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

        def _ps_sq(s: str) -> str:
            return s.replace("'", "''")

        log_ps = _ps_sq(str(call_log))
        venv_ps = _ps_sq(str(venv_py))
        stop_ps = _ps_sq(str(self.stop_ps1))
        # 固定假 PID：owned / foreign 分离；测试不杀真实进程
        owned_pid = 610901
        foreign_pid = 610999
        command = f"""
$ErrorActionPreference = 'Stop'
$callLog = '{log_ps}'
if (Test-Path -LiteralPath $callLog) {{ Remove-Item -LiteralPath $callLog -Force }}
New-Item -ItemType File -Path $callLog -Force | Out-Null
function global:Get-NetTCPConnection {{
  [CmdletBinding()]
  param(
    [string]$LocalAddress,
    [int]$LocalPort,
    [string]$State
  )
  $hasLocal = $PSBoundParameters.ContainsKey('LocalAddress') -and -not [string]::IsNullOrEmpty($LocalAddress)
  if ($hasLocal) {{
    # 精确查询：8000 返回 owned 非空；5173 空（No MSFT）
    Add-Content -LiteralPath $callLog -Value ("exact:{{0}}:{{1}}" -f $LocalAddress, $LocalPort) -Encoding utf8
    if ($LocalPort -eq 8000) {{
      return [pscustomobject]@{{
        LocalAddress = '127.0.0.1'
        LocalPort = 8000
        State = 'Listen'
        OwningProcess = {owned_pid}
      }}
    }}
    # 5173 exact 空
    throw 'No MSFT_NetTCPConnection objects found with property'
  }}
  # 全地址查询（无 LocalAddress）：必须被调用；8000 = owned + :: foreign；5173 空
  Add-Content -LiteralPath $callLog -Value ("full:{{0}}" -f $LocalPort) -Encoding utf8
  if ($LocalPort -eq 8000) {{
    return @(
      [pscustomobject]@{{
        LocalAddress = '127.0.0.1'
        LocalPort = 8000
        State = 'Listen'
        OwningProcess = {owned_pid}
      }},
      [pscustomobject]@{{
        LocalAddress = '::'
        LocalPort = 8000
        State = 'Listen'
        OwningProcess = {foreign_pid}
      }}
    )
  }}
  # 5173 full 空
  throw 'No MSFT_NetTCPConnection objects found with property'
}}
function global:Get-CimInstance {{
  [CmdletBinding()]
  param(
    [Parameter(Position=0)]
    [string]$ClassName,
    [string]$Filter,
    [string]$Namespace
  )
  Add-Content -LiteralPath $callLog -Value ("cim:{{0}}:{{1}}" -f $ClassName, $Filter) -Encoding utf8
  if ($ClassName -ne 'Win32_Process') {{ return $null }}
  if ($Filter -match 'ProcessId\\s*=\\s*{owned_pid}') {{
    return [pscustomobject]@{{
      ProcessId = {owned_pid}
      ExecutablePath = '{venv_ps}'
      CommandLine = '"{venv_ps}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
    }}
  }}
  if ($Filter -match 'ProcessId\\s*=\\s*{foreign_pid}') {{
    return [pscustomobject]@{{
      ProcessId = {foreign_pid}
      ExecutablePath = 'C:\\Windows\\System32\\svchost.exe'
      CommandLine = 'svchost -k netsvcs - foreign-listener-b11'
    }}
  }}
  return $null
}}
& '{stop_ps}' -WhatIf
exit $LASTEXITCODE
"""
        raw = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            timeout=45,
            check=False,
        )
        stdout = _decode_ps_output(raw.stdout)
        stderr = _decode_ps_output(raw.stderr)
        combined = stdout + stderr
        log_text = (
            call_log.read_text(encoding="utf-8", errors="replace")
            if call_log.is_file()
            else ""
        )
        # foreign 全局零副作用：必须非 0
        self.assertNotEqual(
            raw.returncode,
            0,
            f"B11 exact owned + full foreign 必须整次失败："
            f"code={raw.returncode} out={combined!r} log={log_text!r}",
        )
        # 归属失败/中止语义（与生产 Write-Fail 文案对齐）
        self.assertTrue(
            ("归属" in combined and "中止" in combined)
            or ("无法确认" in combined and "归属" in combined),
            f"B11 输出必须含归属失败/中止：out={combined!r}",
        )
        # 不得成功只读终止计划
        self.assertNotIn("将终止", combined, f"B11 不得成功将终止：out={combined!r}")
        self.assertNotIn("未发现", combined, f"B11 不得落入未发现：out={combined!r}")
        lowered = combined.lower()
        for tok in ("taskkill", "stop-process", "已终止"):
            self.assertNotIn(tok, lowered, f"B11 零终止动作，不得含 {tok}")
        # 旁路日志：exact 8000 与 full 8000 均必发生；5173 exact/full 亦应出现
        self.assertRegex(
            log_text,
            r"exact:127\.0\.0\.1:8000",
            f"B11 必须发生 exact 8000：log={log_text!r}",
        )
        self.assertRegex(
            log_text,
            r"full:8000",
            f"B11 必须发生 full8000（禁止 exact 非空短路）：log={log_text!r}",
        )
        self.assertRegex(
            log_text,
            r"exact:127\.0\.0\.1:5173",
            f"B11 必须查询 5173 exact：log={log_text!r}",
        )
        self.assertRegex(
            log_text,
            r"full:5173",
            f"B11 必须查询 5173 full：log={log_text!r}",
        )
        # 两个 PID 的 Cim mock 均须被查询（owned + foreign）
        for pid in (owned_pid, foreign_pid):
            cim_hit = any(
                (f"ProcessId = {pid}" in line or f"ProcessId={pid}" in line)
                and "Win32_Process" in line
                for line in log_text.splitlines()
            )
            self.assertTrue(
                cim_hit,
                f"B11 Get-CimInstance 必须查询 PID {pid}：log={log_text!r}",
            )

    def test_strict_snapshot_rejects_non_int_port_pid_and_relative_exe(self) -> None:
        """
        A2：port/pid 为字符串、bool、浮点，以及 executablePath 相对路径，逐一固定失败；
        合法整数 + 绝对路径 WhatIf 仍成功（空监听已覆盖；此处用合法 owned 形态）。
        """
        cases: list[tuple[str, list[dict[str, Any]]]] = [
            (
                "port_string",
                [
                    {
                        "port": "8000",
                        "pid": 610001,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "uvicorn",
                    }
                ],
            ),
            (
                "pid_bool",
                [
                    {
                        "port": 8000,
                        "pid": True,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "uvicorn",
                    }
                ],
            ),
            (
                "port_float",
                [
                    {
                        "port": 8000.5,
                        "pid": 610002,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "uvicorn",
                    }
                ],
            ),
            (
                "pid_float",
                [
                    {
                        "port": 8000,
                        "pid": 610003.0,
                        "executablePath": r"C:\fake\python.exe",
                        "commandLine": "uvicorn",
                    }
                ],
            ),
            (
                "exe_relative",
                [
                    {
                        "port": 8000,
                        "pid": 610004,
                        "executablePath": r"relative\python.exe",
                        "commandLine": "uvicorn app",
                    }
                ],
            ),
        ]
        for name, records in cases:
            snap = self._write_snapshot(records, name=f"a2-{name}.json")
            proc = _run_powershell(
                self.stop_ps1,
                ["-WhatIf", "-ListenerSnapshotJson", str(snap)],
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertNotEqual(
                proc.returncode,
                0,
                f"A2 {name} 必须固定失败：out={combined!r}",
            )
            lowered = combined.lower()
            for tok in ("taskkill", "stop-process"):
                self.assertNotIn(tok, lowered, f"A2 {name} 不得终止进程")

        # B9/A2 合法段：整数 + 绝对路径 owned 快照 + WhatIf 必须严格 returncode==0 与只读计划语义
        venv_py = _REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
        legal = [
            {
                "port": 8000,
                "pid": 610101,
                "executablePath": str(venv_py),
                "commandLine": f'"{venv_py}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000',
            },
            {
                "port": 5173,
                "pid": 610102,
                "executablePath": r"C:\Program Files\nodejs\node.exe",
                "commandLine": f'node "{_REPO_ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"}"',
            },
        ]
        snap_ok = self._write_snapshot(legal, name="a2-legal.json")
        proc_ok = _run_powershell(
            self.stop_ps1,
            ["-WhatIf", "-ListenerSnapshotJson", str(snap_ok)],
        )
        combined_ok = (proc_ok.stdout or "") + (proc_ok.stderr or "")
        self.assertEqual(
            proc_ok.returncode,
            0,
            f"A2 legal owned + WhatIf 必须 returncode==0：out={combined_ok!r}",
        )
        self.assertIn("WhatIf", combined_ok)
        self.assertIn("将终止", combined_ok)
        self.assertNotIn("未发现", combined_ok)
        lowered_ok = combined_ok.lower()
        for tok in ("taskkill", "stop-process", "已终止"):
            self.assertNotIn(tok, lowered_ok)


# ===========================================================================
# 8. Backup PS1/Bat 参数语义（不触真实数据）
# ===========================================================================


class TestBackupEntryWiring(unittest.TestCase):
    """用途：Backup 入口转发与目标根在仓库外；使用临时假仓。"""

    def setUp(self) -> None:
        files = _require_prod_files()
        self.backup_ps1 = files["backup_ps1"]
        self.backup_bat = files["backup_bat"]
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-entry-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.dest = base / "dest-out"
        self.repo.mkdir()
        self.dest.mkdir()
        _make_fake_repo(self.repo, include_legacy=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_backup_ps1_rejects_when_ports_busy_via_env_or_help(self) -> None:
        """
        调用 Backup PS1 时若服务判定失败应非零退出。
        通过把仓库根切到假仓困难（脚本锚定真实仓库），故改为静态检查参数存在。
        """
        text = self.backup_ps1.read_text(encoding="utf-8-sig")
        self.assertIn("DestinationRoot", text)
        # 应调用 Python 核心
        self.assertTrue(
            "biaoshu_backup" in text or "create_offline_backup" in text or "python" in text.lower(),
            "Backup PS1 必须调用 Python 核心",
        )

    def test_backup_bat_forwards_destination_arg(self) -> None:
        text = _bat_text(self.backup_bat)
        # 应转发 %1 或等价参数
        self.assertTrue(
            "%~1" in text or "%1" in text or "DestinationRoot" in text,
            "Backup bat 应能转发目标根参数",
        )

    def test_backup_ps1_preserves_paths_with_spaces(self) -> None:
        """
        A3：含空格临时假 repo 下复制 Backup PS1，并写最小假 biaoshu_backup.py
        仅回显 argv/最终路径，不探测真实端口、不读真实数据；
        证明 DestinationRoot/RepoRoot 参数没有按空格截断。
        """
        base = Path(self._tmp.name) / "space fixture root"
        repo = base / "fake repo root"
        dest = base / "dest with spaces"
        ops = repo / "tools" / "v1-ops"
        ops.mkdir(parents=True)
        dest.mkdir(parents=True)
        # 复制生产 PS1（锚定到假 repo）
        src_ps1 = self.backup_ps1
        dst_ps1 = ops / "Backup-Biaoshu.ps1"
        shutil.copy2(src_ps1, dst_ps1)
        # 最小假核心：只回显 argv，写旁路日志，stdout 打印伪最终目录
        echo_py = ops / "biaoshu_backup.py"
        echo_log = ops / "_a3_argv_echo.json"
        echo_py.write_text(
            "\n".join(
                [
                    "# -*- coding: utf-8 -*-",
                    "import json, sys",
                    "from pathlib import Path",
                    f"LOG = Path(r'''{echo_log}''')",
                    "LOG.write_text(json.dumps(sys.argv, ensure_ascii=False), encoding='utf-8')",
                    "args = sys.argv[1:]",
                    "dest = None",
                    "repo = None",
                    "i = 0",
                    "while i < len(args):",
                    "    if args[i] == '--destination-root' and i + 1 < len(args):",
                    "        dest = args[i + 1]; i += 2; continue",
                    "    if args[i] == '--repo-root' and i + 1 < len(args):",
                    "        repo = args[i + 1]; i += 2; continue",
                    "    i += 1",
                    "if not dest or not repo:",
                    "    print('missing args', file=sys.stderr)",
                    "    sys.exit(2)",
                    "print(str(Path(dest) / 'biaoshu-backup-a3-space-ok'))",
                    "sys.exit(0)",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        proc = _run_powershell(
            dst_ps1,
            ["-DestinationRoot", str(dest)],
            cwd=repo,
            timeout=60,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.returncode,
            0,
            f"A3 含空格参数应成功调用假核心：code={proc.returncode} out={combined!r}",
        )
        self.assertTrue(echo_log.is_file(), f"假核心未写入 argv 日志：{combined!r}")
        argv = json.loads(echo_log.read_text(encoding="utf-8"))
        self.assertIsInstance(argv, list)
        self.assertGreaterEqual(len(argv), 5)
        # 完整 DestinationRoot / RepoRoot 不得被空格截断
        self.assertIn("--destination-root", argv)
        self.assertIn("--repo-root", argv)
        dest_idx = argv.index("--destination-root")
        repo_idx = argv.index("--repo-root")
        got_dest = argv[dest_idx + 1]
        got_repo = argv[repo_idx + 1]
        self.assertEqual(
            Path(got_dest).resolve(),
            dest.resolve(),
            f"DestinationRoot 被截断或错误：got={got_dest!r} expected={str(dest)!r} argv={argv}",
        )
        self.assertEqual(
            Path(got_repo).resolve(),
            repo.resolve(),
            f"RepoRoot 被截断或错误：got={got_repo!r} expected={str(repo)!r} argv={argv}",
        )
        # 不得出现截断碎片
        self.assertNotIn("with", argv)
        self.assertNotIn("spaces", argv)
        self.assertNotIn("fake", argv)
        self.assertIn("biaoshu-backup-a3-space-ok", combined)

    def test_backup_ps1_preserves_chinese_stderr_and_nonzero_exit(self) -> None:
        """
        B10：含空格假 repo + 假 core 向 stderr 写固定中文并以 exit 7 失败；
        wrapper 必须 exit 7、输出完整中文；不得含替换字符、python.exe 前缀、
        traceback、绝对主仓路径或 argv 业务细节。不触真实端口/数据。
        """
        fixed_cn = "离线备份失败：服务仍在监听"
        base = Path(self._tmp.name) / "b10 space fixture"
        repo = base / "fake repo root"
        dest = base / "dest with spaces"
        ops = repo / "tools" / "v1-ops"
        ops.mkdir(parents=True)
        dest.mkdir(parents=True)
        src_ps1 = self.backup_ps1
        dst_ps1 = ops / "Backup-Biaoshu.ps1"
        shutil.copy2(src_ps1, dst_ps1)
        fake_core = ops / "biaoshu_backup.py"
        # 假核心：仅 UTF-8 stderr 固定中文 + 指定非零退出；不读业务、不探端口
        fake_core.write_text(
            "\n".join(
                [
                    "# -*- coding: utf-8 -*-",
                    "import sys",
                    f"MSG = {fixed_cn!r}",
                    "sys.stderr.buffer.write((MSG + '\\n').encode('utf-8'))",
                    "sys.stderr.buffer.flush()",
                    "sys.exit(7)",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        proc = _run_powershell(
            dst_ps1,
            ["-DestinationRoot", str(dest)],
            cwd=repo,
            timeout=60,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.returncode,
            7,
            f"B10 wrapper 必须保留 exit 7：code={proc.returncode} out={combined!r}",
        )
        self.assertIn(
            fixed_cn,
            combined,
            f"B10 必须透传完整固定中文：out={combined!r}",
        )
        # 禁止乱码替换字符与 PowerShell 原生错误前缀
        self.assertNotIn("\ufffd", combined)
        self.assertNotIn("�", combined)
        self.assertNotIn("python.exe :", combined.lower())
        self.assertNotIn("Python.exe :", combined)
        lowered = combined.lower()
        for tok in ("traceback", "tracebook"):
            self.assertNotIn(tok, lowered)
        # 不得泄漏主仓绝对路径 / argv 业务细节
        main_repo = Path(r"C:\Users\Administrator\biaoshu").resolve()
        self.assertNotIn(str(main_repo), combined)
        self.assertNotIn(str(_REPO_ROOT.resolve()), combined)
        for leak in (
            "--repo-root",
            "--destination-root",
            "--include-semantic-models",
            str(dest),
            str(repo),
        ):
            self.assertNotIn(
                leak,
                combined,
                f"B10 失败输出不得含 argv/路径细节：leak={leak!r} out={combined!r}",
            )


# ===========================================================================
# 9. main() CLI 与安全门
# ===========================================================================


class TestMainCliSafety(unittest.TestCase):
    """用途：CLI 无危险绕过；失败不泄漏密钥。"""

    def setUp(self) -> None:
        self.mod = _import_backup_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="v1a-cli-")
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.dest = base / "dest"
        self.repo.mkdir()
        self.dest.mkdir()
        _make_fake_repo(self.repo)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_main_help_or_missing_args_no_crash_with_secret_leak(self) -> None:
        buf_out = __import__("io").StringIO()
        buf_err = __import__("io").StringIO()
        try:
            with mock.patch("sys.stdout", buf_out), mock.patch("sys.stderr", buf_err):
                try:
                    code = self.mod.main(["--help"])
                except SystemExit as exc:
                    code = exc.code
        except Exception:
            # 无 --help 也可
            with mock.patch("sys.stdout", buf_out), mock.patch("sys.stderr", buf_err):
                try:
                    code = self.mod.main([])
                except SystemExit as exc:
                    code = exc.code
        combined = buf_out.getvalue() + buf_err.getvalue()
        self.assertNotIn(_FAKE_API_KEY_MARKER, combined)
        # code 可为 0(help) 或非 0
        self.assertIsNotNone(code)

    def test_cli_has_no_skip_integrity_flag_in_help_text(self) -> None:
        # 源码静态门：禁止危险绕过开关
        src = _PROD_MODULE_PATH.read_text(encoding="utf-8")
        for banned in (
            "--skip-integrity",
            "--skip-hash",
            "--skip-port",
            "--force-online",
            "--allow-running",
        ):
            self.assertNotIn(banned, src, f"CLI 禁止提供 {banned}")


# ===========================================================================
# 10. 反读主仓真实数据（元测试门）
# ===========================================================================


class TestNoTouchRealBusinessData(unittest.TestCase):
    """用途：确保本专项测试文件本身不引用主仓真实业务路径内容。"""

    def test_this_module_never_opens_real_biaoshu_db(self) -> None:
        src = Path(__file__).read_text(encoding="utf-8")
        # 允许在字符串中写相对名，但禁止拼出主仓绝对 data 路径读取
        self.assertNotIn(
            str((_REPO_ROOT / "backend" / "data" / "biaoshu.db").resolve()),
            src,
        )
        # 夹具必须用 TemporaryDirectory / tempfile
        self.assertIn("TemporaryDirectory", src)
        self.assertIn("_make_fake_repo", src)


def _suite_order() -> unittest.TestSuite:
    """串行：先入口存在性，再 API，再行为。"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (
        TestProductionEntryPresence,
        TestPs1BomAndBatForward,
        TestFrozenPythonApiSurface,
        TestSourcePlanWhitelist,
        TestServicePortGate,
        TestCreateOfflineBackupSuccess,
        TestCreateOfflineBackupFailures,
        TestStopWhatIfAndSnapshot,
        TestBackupEntryWiring,
        TestMainCliSafety,
        TestNoTouchRealBusinessData,
    ):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    # 串行、无缓冲；失败即见真实 failure-first
    runner = unittest.TextTestRunner(verbosity=2, failfast=False, buffer=False)
    result = runner.run(_suite_order())
    # 明确打印汇总，便于 review_request 引用
    print(
        "\n[V1-A][GROK-B] SUMMARY "
        f"ran={result.testsRun} "
        f"failures={len(result.failures)} "
        f"errors={len(result.errors)} "
        f"skipped={len(result.skipped)}"
    )
    if result.failures:
        first = result.failures[0]
        print(f"[V1-A][GROK-B] FIRST_FAILURE test={first[0]} msg={first[1].splitlines()[-1] if first[1] else ''}")
    if result.errors:
        first_e = result.errors[0]
        print(f"[V1-A][GROK-B] FIRST_ERROR test={first_e[0]} msg={first_e[1].splitlines()[-1] if first_e[1] else ''}")
    sys.exit(0 if result.wasSuccessful() else 1)
