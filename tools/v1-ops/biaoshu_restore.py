# -*- coding: utf-8 -*-
# 模块：标书 V1-B 离线恢复核心
# 用途：校验 v2 备份并以 journal/staging/hold 完成可回滚离线恢复与崩溃重入
# 对接：Restore-Biaoshu.bat / tools/v1-ops/Restore-Biaoshu.ps1；复用 biaoshu_backup 冻结接口
# 二次开发：禁止暴露 fault_injector/skip/force；公共 API 仅抛 RestoreError；锁必须 owner token
"""标书 V1-B 离线恢复核心（仅标准库）。

冻结公开接口：
- RESTORE_SCHEMA_VERSION
- RESTORE_DATA_COMPATIBILITY_VERSION
- RESTORE_JOURNAL_SCHEMA_VERSION
- RestoreError
- load_and_validate_backup
- build_restore_plan
- recover_incomplete_restore
- restore_offline_backup
- main

注入参数 service_probe/now/git_head/fault_injector 仅供临时假仓测试，
不得由 bat/PS 生产 CLI 转发。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from biaoshu_backup import (
    ALL_LOGICAL_ROOTS,
    BACKUP_SCHEMA_VERSION,
    DATA_COMPATIBILITY_VERSION,
    LOGICAL_DB,
    LOGICAL_KNOWLEDGE,
    LOGICAL_KNOWLEDGE_CARDS,
    LOGICAL_LEGACY_UPLOADS,
    LOGICAL_SEMANTIC_MODELS,
    LOGICAL_UPLOADS,
    STATE_ABSENT,
    STATE_EMPTY,
    STATE_NOT_INCLUDED,
    STATE_PRESENT,
    BackupError,
    _abspath_no_follow,
    _assert_no_reparse_in_chain,
    _integrity_check_sqlite,
    _is_reparse_or_symlink,
    _is_regular_file,
    _live_paths,
    _path_component_exists,
    _sha256_file,
    assert_services_stopped,
    create_offline_backup,
)

RESTORE_SCHEMA_VERSION = BACKUP_SCHEMA_VERSION
RESTORE_DATA_COMPATIBILITY_VERSION = DATA_COMPATIBILITY_VERSION
RESTORE_JOURNAL_SCHEMA_VERSION = "biaoshu-offline-restore-journal-v1"

_CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_DATABASE_URL = "sqlite:///./data/biaoshu.db"
_DEFAULT_UPLOAD_DIR = "./uploads"

# journal 相位
PHASE_PRECHECK = "PRECHECK"
PHASE_PRE_BACKUP = "PRE_BACKUP"
PHASE_STAGE = "STAGE"
PHASE_CUTOVER = "CUTOVER"
PHASE_VERIFY = "VERIFY"
PHASE_COMMIT = "COMMIT"
PHASE_COMMITTED_CLEANUP_PENDING = "COMMITTED_CLEANUP_PENDING"
PHASE_ROLLING_BACK = "ROLLING_BACK"
PHASE_ROLLED_BACK = "ROLLED_BACK"

# 固定退出码：恢复已提交但清理未完成
EXIT_CLEANUP_PENDING = 3

# 固定中文错误（journal 损坏 / 清理未完成）
_JOURNAL_CORRUPT_MSG = "恢复日志损坏，已保留现场并拒绝继续"
_CLEANUP_PENDING_MSG = "恢复完成但清理未完成"

# 锁/ journal 逻辑名（相对 work root）
_LOCK_NAME = "restore.lock"
_JOURNAL_NAME = "journal.json"
_JOURNAL_TMP_NAME = "journal.json.tmp"
_STAGING_NAME = "staging"
_HOLD_NAME = "hold"
_TRASH_NAME = "trash"

# journal 顶层精确五键
_JOURNAL_TOP_KEYS = frozenset(
    {
        "schema_version",
        "operation_id",
        "phase",
        "pre_restore_backup_name",
        "roots",
    }
)
# 每根精确六字段
_JOURNAL_ROOT_KEYS = frozenset(
    {
        "backup_state",
        "intent",
        "result",
        "live_existed_before",
        "hold_moved",
        "new_installed",
    }
)
_KNOWN_PHASES = frozenset(
    {
        PHASE_PRECHECK,
        PHASE_PRE_BACKUP,
        PHASE_STAGE,
        PHASE_CUTOVER,
        PHASE_VERIFY,
        PHASE_COMMIT,
        PHASE_COMMITTED_CLEANUP_PENDING,
        PHASE_ROLLING_BACK,
        PHASE_ROLLED_BACK,
    }
)
# 真实四态（持久 journal 在非 cleanup-pending 安全重建形态下必须）
_REAL_BACKUP_STATES = frozenset(
    {STATE_PRESENT, STATE_EMPTY, STATE_ABSENT, STATE_NOT_INCLUDED}
)
# 读侧允许集合：真实四态 + None（None 仅限 PRECHECK 或 cleanup-pending 安全重建）
_KNOWN_BACKUP_STATES = _REAL_BACKUP_STATES | {None}
# 这些 phase 的六根 backup_state 必须是真实四态，禁止 None
_PHASES_REQUIRE_REAL_BACKUP_STATE = frozenset(
    {
        PHASE_PRE_BACKUP,
        PHASE_STAGE,
        PHASE_CUTOVER,
        PHASE_VERIFY,
        PHASE_COMMIT,
        PHASE_ROLLING_BACK,
        PHASE_ROLLED_BACK,
    }
)
_KNOWN_INTENTS = frozenset(
    {
        None,
        "skip_not_included",
        "hold_live",
        "no_live",
        "ensure_absent",
        "install_stage",
        "rollback_remove_new",
        "rollback_restore_hold",
    }
)
_KNOWN_RESULTS = frozenset(
    {
        None,
        "skipped",
        "held",
        "no_live",
        "absent",
        "installed",
        "rollback_removed_new",
        "rollback_restored",
    }
)
_OP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")


class RestoreError(Exception):
    """离线恢复失败（固定中文原因由调用方展示）。"""


# ---------------------------------------------------------------------------
# 路径与 reparse 辅助
# ---------------------------------------------------------------------------


def _as_restore_error(exc: BaseException, fallback: str = "离线恢复失败") -> RestoreError:
    """公共边界类型归一：RestoreError 原样；BackupError 转 RestoreError；
    其它 operational Exception 固定中文且不拼接原始文本（防泄密）。
    不捕获 KeyboardInterrupt/SystemExit 等 BaseException。
    """
    if isinstance(exc, RestoreError):
        return exc
    if isinstance(exc, BackupError):
        msg = str(exc).strip() if str(exc) else fallback
        return RestoreError(msg or fallback)
    # 其它 Exception：固定中文，不泄漏注入/内部细节
    return RestoreError(fallback)


def _assert_no_reparse_restore(path: Path, message: str) -> None:
    try:
        _assert_no_reparse_in_chain(path, message)
    except BackupError as exc:
        raise _as_restore_error(exc, message) from exc


def _resolve_repo_root(repo_root: Any) -> Path:
    root = _abspath_no_follow(repo_root)
    if not root.exists() or not root.is_dir():
        raise RestoreError("仓库根目录不存在或不是目录")
    _assert_no_reparse_restore(root, "仓库根目录不能是符号链接或重解析点")
    return root


def _same_volume(a: Path, b: Path) -> bool:
    """判断两路径是否同卷（Windows 盘符；其它比较根）。"""
    a_abs = _abspath_no_follow(a)
    b_abs = _abspath_no_follow(b)
    if os.name == "nt":
        return os.path.splitdrive(str(a_abs))[0].upper() == os.path.splitdrive(
            str(b_abs)
        )[0].upper()
    return a_abs.parts[0] == b_abs.parts[0]


def _is_outside_repo(path: Path, repo_root: Path) -> bool:
    dest = _abspath_no_follow(path)
    repo = _abspath_no_follow(repo_root)
    try:
        dest.relative_to(repo)
        return False
    except ValueError:
        pass
    dest_s = os.path.normcase(str(dest))
    repo_s = os.path.normcase(str(repo))
    if dest_s == repo_s or dest_s.startswith(repo_s + os.sep):
        return False
    return True


def _normcase_abs(path: Path) -> str:
    """Windows normcase 语义下的绝对路径字符串（去尾部分隔符）。"""
    s = os.path.normcase(str(_abspath_no_follow(path)))
    if len(s) > 3 and s.endswith(("\\", "/")):
        s = s.rstrip("\\/")
    return s


def _is_same_or_nested(a: Path, b: Path) -> bool:
    """两路径相等或互为祖先/后代（Windows normcase）。"""
    sa = _normcase_abs(a)
    sb = _normcase_abs(b)
    if sa == sb:
        return True
    sep = os.sep
    return sa.startswith(sb + sep) or sb.startswith(sa + sep)


def _assert_restore_layout_isolation(
    backup_dir: Path,
    work_root: Path,
    pre_root: Path,
) -> None:
    """在任何 pre-backup/lock/staging 写入前校验目录隔离。

    - backup_dir 与 work_root 不得相等或互为祖先/后代
    - pre_root 与 work_root 不得相等或互为祖先/后代
    - pre_root 等于或位于 backup_dir 内必须拒绝
    - 允许 backup_dir 位于 pre_root 下（默认 biaoshu-backups/具体包）
    """
    if _is_same_or_nested(backup_dir, work_root):
        raise RestoreError("备份目录与恢复工作根不得重叠")
    if _is_same_or_nested(pre_root, work_root):
        raise RestoreError("恢复前备份目标与恢复工作根不得重叠")
    # pre_root 等于或位于 backup_dir 内 → 拒绝（避免向所选权威包内写新备份）
    pre_s = _normcase_abs(pre_root)
    bkp_s = _normcase_abs(backup_dir)
    if pre_s == bkp_s or pre_s.startswith(bkp_s + os.sep):
        raise RestoreError("恢复前备份目标不得位于所选备份包内")


def _validate_relative_posix(rel: str) -> None:
    if not isinstance(rel, str):
        raise RestoreError("清单相对路径非法")
    if not rel or rel in (".", ".."):
        raise RestoreError("清单相对路径非法")
    if rel != rel.strip():
        raise RestoreError("清单相对路径非法")
    if "\\" in rel or rel.startswith("/") or ":" in rel:
        raise RestoreError("清单相对路径非法")
    if "\x00" in rel or any(ord(ch) < 32 for ch in rel):
        raise RestoreError("清单相对路径非法")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise RestoreError("清单相对路径非法")
    if rel.startswith("//") or rel.startswith("\\\\"):
        raise RestoreError("清单相对路径非法")


def _fault(injector: Optional[Callable[..., None]], point: str, **kwargs: Any) -> None:
    if injector is not None:
        injector(point, **kwargs)


# ---------------------------------------------------------------------------
# 默认布局 / .env 门（只比较 DATABASE_URL / UPLOAD_DIR，不记录值）
# ---------------------------------------------------------------------------


def _parse_env_two_keys(text: str) -> Dict[str, str]:
    """仅提取 DATABASE_URL 与 UPLOAD_DIR（大小写不敏感键名）。"""
    found: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key_u = key.strip().upper()
        if key_u in ("DATABASE_URL", "UPLOAD_DIR"):
            # 去掉可选引号，不打印/不记录
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            found[key_u] = v
    return found


def _assert_default_data_layout(repo_root: Path) -> None:
    """非默认 DATABASE_URL/UPLOAD_DIR 固定拒绝；不输出具体值。"""
    # 环境变量
    for env_key, default in (
        ("DATABASE_URL", _DEFAULT_DATABASE_URL),
        ("UPLOAD_DIR", _DEFAULT_UPLOAD_DIR),
    ):
        if env_key in os.environ:
            cur = os.environ.get(env_key, "")
            if cur != default:
                raise RestoreError("检测到非默认数据路径配置，已拒绝恢复")

    env_file = repo_root / "backend" / ".env"
    if not env_file.exists():
        return
    if _is_reparse_or_symlink(env_file):
        raise RestoreError("配置文件路径非法")
    try:
        # 只读文本；失败则拒绝
        text = env_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RestoreError("无法读取本地配置以校验数据路径") from exc
    parsed = _parse_env_two_keys(text)
    if "DATABASE_URL" in parsed and parsed["DATABASE_URL"] != _DEFAULT_DATABASE_URL:
        raise RestoreError("检测到非默认数据路径配置，已拒绝恢复")
    if "UPLOAD_DIR" in parsed and parsed["UPLOAD_DIR"] != _DEFAULT_UPLOAD_DIR:
        raise RestoreError("检测到非默认数据路径配置，已拒绝恢复")


# ---------------------------------------------------------------------------
# 备份包严格加载
# ---------------------------------------------------------------------------


def _list_physical_files(backup_dir: Path) -> List[Path]:
    """列出备份目录内全部普通文件（相对 backup_dir），拒绝 reparse。"""
    results: List[Path] = []
    if _is_reparse_or_symlink(backup_dir):
        raise RestoreError("备份目录不能是符号链接或重解析点")
    for dirpath, dirnames, filenames in os.walk(
        backup_dir, topdown=True, followlinks=False
    ):
        current = Path(dirpath)
        if _is_reparse_or_symlink(current):
            raise RestoreError("备份目录含符号链接或重解析点")
        # 就地过滤：拒绝 reparse 子目录
        kept: List[str] = []
        for name in list(dirnames):
            child = current / name
            if _is_reparse_or_symlink(child):
                raise RestoreError("备份目录含符号链接或重解析点")
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            fp = current / name
            if _is_reparse_or_symlink(fp):
                raise RestoreError("备份目录含符号链接或重解析点")
            if not _is_regular_file(fp):
                raise RestoreError("备份目录含非普通文件")
            results.append(fp)
    return results


def load_and_validate_backup(backup_dir: Any) -> Dict[str, Any]:
    """严格只读加载并验证 v2 备份包。

    返回内存结构（无绝对业务路径正文）：
    {
      manifest, backup_dir(Path), files_by_root, roots, db_path(Path)
    }
    公共 API：BackupError 一律转为 RestoreError。
    """
    try:
        return _load_and_validate_backup_impl(backup_dir)
    except BackupError as exc:
        raise _as_restore_error(exc, "备份校验失败") from exc


def _load_and_validate_backup_impl(backup_dir: Any) -> Dict[str, Any]:
    bdir = _abspath_no_follow(backup_dir)
    if not bdir.exists() or not bdir.is_dir():
        raise RestoreError("备份目录不存在或不是目录")
    _assert_no_reparse_restore(bdir, "备份目录不能是符号链接或重解析点")

    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        raise RestoreError("备份清单不存在")
    if _is_reparse_or_symlink(manifest_path):
        raise RestoreError("备份清单路径非法")

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RestoreError("备份清单无法解析") from exc

    if not isinstance(manifest, dict):
        raise RestoreError("备份清单格式非法")

    expected_keys = {
        "schema_version",
        "data_compatibility_version",
        "created_at_utc",
        "git_head",
        "roots",
        "files",
    }
    if set(manifest.keys()) != expected_keys:
        # v1 或其它：固定拒绝
        schema = manifest.get("schema_version")
        if schema == "biaoshu-offline-backup-v1" or "data_compatibility_version" not in manifest:
            raise RestoreError("不支持的备份格式版本，已拒绝自动恢复")
        raise RestoreError("备份清单顶层键非法")

    schema = manifest.get("schema_version")
    if schema != RESTORE_SCHEMA_VERSION:
        if schema == "biaoshu-offline-backup-v1":
            raise RestoreError("不支持的备份格式版本，已拒绝自动恢复")
        raise RestoreError("不支持的备份格式版本，已拒绝自动恢复")

    compat = manifest.get("data_compatibility_version")
    if compat != RESTORE_DATA_COMPATIBILITY_VERSION:
        raise RestoreError("备份数据兼容版本不匹配，已拒绝恢复")

    created = manifest.get("created_at_utc")
    if not isinstance(created, str) or not created.endswith("Z"):
        raise RestoreError("备份创建时间非法")
    git_head = manifest.get("git_head")
    if git_head is not None and not isinstance(git_head, str):
        raise RestoreError("备份 git_head 类型非法")

    roots = manifest.get("roots")
    if not isinstance(roots, dict) or set(roots.keys()) != set(ALL_LOGICAL_ROOTS):
        raise RestoreError("备份根状态键非法")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise RestoreError("备份文件清单非法")

    # 敏感门（清单正文）
    lowered = raw_text.lower()
    for banned in ("api_key", "apikey", "authorization", "cookie", "set-cookie", "bearer "):
        if banned in lowered:
            raise RestoreError("备份清单未通过敏感字段门")
    if ":\\" in raw_text or "\\\\" in raw_text:
        # 粗检绝对路径；允许 manifest 内无盘符
        if re.search(r"[A-Za-z]:[\\/]", raw_text) or "\\\\" in raw_text:
            raise RestoreError("备份清单不得包含绝对路径")

    allowed_states_common = {STATE_PRESENT, STATE_EMPTY, STATE_ABSENT}
    for name in ALL_LOGICAL_ROOTS:
        entry = roots[name]
        if not isinstance(entry, dict) or set(entry.keys()) != {
            "state",
            "file_count",
            "total_bytes",
        }:
            raise RestoreError(f"根状态字段非法: {name}")
        state = entry["state"]
        fc = entry["file_count"]
        tb = entry["total_bytes"]
        if type(fc) is not int or type(tb) is not int or isinstance(fc, bool) or isinstance(tb, bool):
            raise RestoreError(f"根状态计数类型非法: {name}")
        if fc < 0 or tb < 0:
            raise RestoreError(f"根状态计数非法: {name}")
        if name == LOGICAL_DB:
            if state != STATE_PRESENT:
                raise RestoreError("数据库根状态非法")
        elif name == LOGICAL_SEMANTIC_MODELS:
            if state not in allowed_states_common | {STATE_NOT_INCLUDED}:
                raise RestoreError(f"根状态非法: {name}")
        else:
            if state not in allowed_states_common:
                raise RestoreError(f"根状态非法: {name}")
        if state == STATE_PRESENT:
            if fc < 1:
                raise RestoreError(f"根状态与文件聚合不一致: {name}")
        else:
            if fc != 0 or tb != 0:
                raise RestoreError(f"根状态与文件聚合不一致: {name}")

    # 校验 files 项
    seen_keys: Set[Tuple[str, str]] = set()
    seen_case: Dict[Tuple[str, str], str] = {}
    files_by_root: Dict[str, List[Dict[str, Any]]] = {n: [] for n in ALL_LOGICAL_ROOTS}
    agg_count: Dict[str, int] = {n: 0 for n in ALL_LOGICAL_ROOTS}
    agg_bytes: Dict[str, int] = {n: 0 for n in ALL_LOGICAL_ROOTS}

    for item in files:
        if not isinstance(item, dict) or set(item.keys()) != {
            "logical_root",
            "relative_path",
            "size_bytes",
            "sha256",
        }:
            raise RestoreError("备份文件条目键非法")
        logical = item["logical_root"]
        rel = item["relative_path"]
        size = item["size_bytes"]
        digest = item["sha256"]
        if logical not in ALL_LOGICAL_ROOTS:
            raise RestoreError("备份文件逻辑根非法")
        _validate_relative_posix(rel)
        if type(size) is not int or isinstance(size, bool) or size < 0:
            raise RestoreError("备份文件大小非法")
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise RestoreError("备份文件哈希非法")
        key = (logical, rel)
        if key in seen_keys:
            raise RestoreError("备份文件清单存在重复项")
        seen_keys.add(key)
        case_key = (logical, rel.casefold())
        if case_key in seen_case and seen_case[case_key] != rel:
            raise RestoreError("备份文件路径存在大小写碰撞")
        seen_case[case_key] = rel
        if roots[logical]["state"] in (
            STATE_EMPTY,
            STATE_ABSENT,
            STATE_NOT_INCLUDED,
        ):
            raise RestoreError("根状态与文件聚合不一致")
        files_by_root[logical].append(item)
        agg_count[logical] += 1
        agg_bytes[logical] += size

    for name in ALL_LOGICAL_ROOTS:
        st = roots[name]["state"]
        if st == STATE_PRESENT:
            if agg_count[name] != roots[name]["file_count"]:
                raise RestoreError(f"根状态与文件聚合不一致: {name}")
            if agg_bytes[name] != roots[name]["total_bytes"]:
                raise RestoreError(f"根状态与文件聚合不一致: {name}")
        else:
            if agg_count[name] != 0 or agg_bytes[name] != 0:
                raise RestoreError(f"根状态与文件聚合不一致: {name}")

    # db 仅 biaoshu.db
    db_files = files_by_root[LOGICAL_DB]
    if len(db_files) != 1 or db_files[0]["relative_path"] != "biaoshu.db":
        raise RestoreError("数据库根文件集合非法")

    # 物理集合：manifest + 声明文件 + 必要目录；不得有未知文件
    physical = _list_physical_files(bdir)
    expected_rel: Set[str] = {"manifest.json"}
    for logical, rel in seen_keys:
        expected_rel.add(f"{logical}/{rel}")

    physical_rel: Set[str] = set()
    for fp in physical:
        try:
            rel_p = fp.relative_to(bdir).as_posix()
        except ValueError as exc:
            raise RestoreError("备份物理路径逃逸") from exc
        physical_rel.add(rel_p)

    if physical_rel != expected_rel:
        # 多或少
        raise RestoreError("备份物理文件集合与清单不一致")

    # 逐文件大小/哈希
    for logical, rel in sorted(seen_keys):
        item = next(
            x for x in files_by_root[logical] if x["relative_path"] == rel
        )
        fp = bdir / logical / Path(rel)
        if not fp.is_file():
            raise RestoreError("备份声明文件缺失")
        try:
            st = fp.lstat()
        except OSError as exc:
            raise RestoreError("无法读取备份文件") from exc
        if st.st_size != item["size_bytes"]:
            raise RestoreError("备份文件大小与清单不一致")
        digest = _sha256_file(fp)
        if digest != item["sha256"]:
            raise RestoreError("备份文件哈希与清单不一致")

    # 备份 DB 完整性
    db_path = bdir / LOGICAL_DB / "biaoshu.db"
    try:
        _integrity_check_sqlite(db_path)
    except BackupError as exc:
        raise RestoreError(str(exc) if str(exc) else "备份数据库完整性检查未通过") from exc

    return {
        "manifest": manifest,
        "backup_dir": bdir,
        "files_by_root": files_by_root,
        "roots": roots,
        "db_path": db_path,
        "files": files,
    }


# ---------------------------------------------------------------------------
# 恢复计划
# ---------------------------------------------------------------------------


def build_restore_plan(repo_root: Any, validated_backup: Dict[str, Any]) -> Dict[str, Any]:
    """构建绝对锚定的恢复计划（不写入 live）。公共 API 不泄漏 BackupError。"""
    try:
        return _build_restore_plan_impl(repo_root, validated_backup)
    except BackupError as exc:
        raise _as_restore_error(exc) from exc


def _build_restore_plan_impl(
    repo_root: Any, validated_backup: Dict[str, Any]
) -> Dict[str, Any]:
    root = _resolve_repo_root(repo_root)
    _assert_default_data_layout(root)

    live = _live_paths(root)
    for logical, path in live.items():
        # 对现存路径检查 reparse 链
        if _path_component_exists(path):
            _assert_no_reparse_restore(path, "目标路径不能是符号链接或重解析点")
        # 祖先 backend/data 等
        parent = path.parent
        if _path_component_exists(parent):
            _assert_no_reparse_restore(parent, "目标路径不能是符号链接或重解析点")

    roots = validated_backup["roots"]
    plan_roots: List[Dict[str, Any]] = []
    for logical in ALL_LOGICAL_ROOTS:
        plan_roots.append(
            {
                "logical_root": logical,
                "live_path": live[logical],
                "backup_state": roots[logical]["state"],
                "file_count": roots[logical]["file_count"],
                "total_bytes": roots[logical]["total_bytes"],
            }
        )

    return {
        "repo_root": root,
        "roots": plan_roots,
        "include_semantic_in_prebackup": roots[LOGICAL_SEMANTIC_MODELS]["state"]
        != STATE_NOT_INCLUDED,
        "validated": validated_backup,
    }


# ---------------------------------------------------------------------------
# journal / 锁
# ---------------------------------------------------------------------------


def _default_work_root(repo_root: Path) -> Path:
    return _abspath_no_follow(repo_root.parent / "biaoshu-restore-work")


def _default_prebackup_root(repo_root: Path) -> Path:
    return _abspath_no_follow(repo_root.parent / "biaoshu-backups")


def _journal_path(work_root: Path) -> Path:
    return work_root / _JOURNAL_NAME


def _lock_path(work_root: Path) -> Path:
    return work_root / _LOCK_NAME


def _atomic_write_journal(work_root: Path, data: Dict[str, Any]) -> None:
    """临时文件 + fsync + 原子替换。仅逻辑名，无绝对业务路径。"""
    work_root.mkdir(parents=True, exist_ok=True)
    tmp = work_root / _JOURNAL_TMP_NAME
    final = work_root / _JOURNAL_NAME
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # 敏感门：journal 不得含绝对盘符路径
    if re.search(r"[A-Za-z]:[\\/]", text) or "\\\\" in text:
        raise RestoreError("恢复日志不得包含绝对路径")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(str(tmp), str(final))


def _journal_corrupt(cause: Optional[BaseException] = None) -> RestoreError:
    if cause is None:
        return RestoreError(_JOURNAL_CORRUPT_MSG)
    return RestoreError(_JOURNAL_CORRUPT_MSG)


def _is_safe_prebackup_basename(name: Any) -> bool:
    """pre_restore_backup_name：非空安全 basename，无 . / .. / 分隔符 / 盘符 / 控制字符。"""
    if not isinstance(name, str) or not name:
        return False
    if name in (".", ".."):
        return False
    if name != name.strip():
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    if "\x00" in name or any(ord(ch) < 32 for ch in name):
        return False
    return bool(_SAFE_BASENAME_RE.fullmatch(name))


def _strict_validate_journal(data: Any) -> Dict[str, Any]:
    """全面校验 journal；非法一律固定 RestoreError，禁止 KeyError/TypeError 外溢。"""
    if not isinstance(data, dict):
        raise _journal_corrupt()
    try:
        keys = frozenset(data.keys())
    except Exception as exc:
        raise _journal_corrupt(exc) from exc
    if keys != _JOURNAL_TOP_KEYS:
        raise _journal_corrupt()
    if data.get("schema_version") != RESTORE_JOURNAL_SCHEMA_VERSION:
        raise _journal_corrupt()
    op_id = data.get("operation_id")
    if not isinstance(op_id, str) or not _OP_ID_RE.fullmatch(op_id):
        raise _journal_corrupt()
    phase = data.get("phase")
    if not isinstance(phase, str) or phase not in _KNOWN_PHASES:
        raise _journal_corrupt()
    if not _is_safe_prebackup_basename(data.get("pre_restore_backup_name")):
        raise _journal_corrupt()

    roots = data.get("roots")
    if not isinstance(roots, dict):
        raise _journal_corrupt()
    try:
        root_keys = frozenset(roots.keys())
    except Exception as exc:
        raise _journal_corrupt(exc) from exc
    expected_roots = frozenset(ALL_LOGICAL_ROOTS)
    if root_keys != expected_roots:
        raise _journal_corrupt()

    for logical in ALL_LOGICAL_ROOTS:
        info = roots.get(logical)
        if not isinstance(info, dict):
            raise _journal_corrupt()
        try:
            info_keys = frozenset(info.keys())
        except Exception as exc:
            raise _journal_corrupt(exc) from exc
        if info_keys != _JOURNAL_ROOT_KEYS:
            raise _journal_corrupt()
        bstate = info.get("backup_state")
        # A12：phase-aware backup_state
        # - COMMITTED_CLEANUP_PENDING：允许真实四态或安全重建全 None
        # - PRECHECK：允许 None（内存/极少持久）
        # - 其余实际持久 phase：六根必须真实四态，禁止 None
        if phase in _PHASES_REQUIRE_REAL_BACKUP_STATE:
            if bstate not in _REAL_BACKUP_STATES:
                raise _journal_corrupt()
        elif bstate not in _KNOWN_BACKUP_STATES:
            raise _journal_corrupt()
        intent = info.get("intent")
        if intent not in _KNOWN_INTENTS:
            raise _journal_corrupt()
        result = info.get("result")
        if result not in _KNOWN_RESULTS:
            raise _journal_corrupt()
        leb = info.get("live_existed_before")
        if leb is not None and not isinstance(leb, bool):
            raise _journal_corrupt()
        # 禁止 bool 子类之外的“假 bool”（如 0/1 伪装）—— bool 本身是 int 子类，仅接受 True/False
        hold_moved = info.get("hold_moved")
        new_installed = info.get("new_installed")
        if type(hold_moved) is not bool or type(new_installed) is not bool:
            raise _journal_corrupt()
        # 必要一致性：hold 已移动则 live 不可能“事先不存在”
        if hold_moved is True and leb is False:
            raise _journal_corrupt()
        # skipped 仅配合 not_included / skip 意图
        if result == "skipped" and intent not in (None, "skip_not_included"):
            raise _journal_corrupt()
        if intent == "skip_not_included":
            if phase in _PHASES_REQUIRE_REAL_BACKUP_STATE:
                if bstate != STATE_NOT_INCLUDED:
                    raise _journal_corrupt()
            elif bstate not in (STATE_NOT_INCLUDED, None):
                raise _journal_corrupt()
        if result == "skipped" and bstate not in (STATE_NOT_INCLUDED, None):
            raise _journal_corrupt()
        # 状态与终态结果的明显非法组合（保留 intent 已写/result 仍空的合法崩溃窗口）
        if result == "installed" and bstate == STATE_ABSENT:
            raise _journal_corrupt()
        if result == "absent" and bstate == STATE_PRESENT:
            raise _journal_corrupt()
        if result == "skipped" and bstate == STATE_PRESENT:
            raise _journal_corrupt()
        # new_installed 与 absent 备份态互斥
        if new_installed is True and bstate == STATE_ABSENT:
            raise _journal_corrupt()
    return data


def _read_journal(work_root: Path) -> Optional[Dict[str, Any]]:
    """读取并严格校验 journal；任何物理动作前必须经此门。"""
    jp = _journal_path(work_root)
    if not jp.exists():
        return None
    try:
        text = jp.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RestoreError(_JOURNAL_CORRUPT_MSG) from exc
    return _strict_validate_journal(data)


def _lock_owner_payload(owner_token: str) -> str:
    """锁文件正文：owner token + pid（用于判定活跃/失效所有者）。"""
    return json.dumps(
        {"owner": owner_token, "pid": os.getpid()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _parse_lock_payload(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("owner"), str) and data["owner"]:
            return data
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # 非 JSON 历史正文：仅 owner 行，pid 未知 → 无法证明失效
    first = raw.splitlines()[0].strip()
    if not first:
        return None
    return {"owner": first, "pid": None}


def _pid_is_alive(pid: Any) -> bool:
    """判定进程是否仍存活；无法判定时视为可能存活（fail-closed）。"""
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return True
    if pid_i <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_i)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return int(exit_code.value) == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid_i, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _stale_sidecar_name(payload_text: str) -> str:
    """由 stale 锁正文派生唯一 sidecar 名，供 rename CAS 使用。"""
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()[:20]
    return f"{_LOCK_NAME}.stale-{digest}"


def _acquire_lock(
    work_root: Path,
    owner_token: str,
    *,
    allow_stale_takeover: bool = False,
) -> Any:
    """独占创建锁文件并写入 owner token；返回可关闭的 fd。

    allow_stale_takeover 仅用于存在有效未完成 journal 的崩溃重入：
    使用 os.rename CAS 原子接管失效锁，禁止“读 stale→unlink→重建” TOCTOU。
    并发 stale recover 最多一个进入 cleanup/rollback；败者不得删除赢家锁。
    无 journal 路径不得调用 allow_stale_takeover=True。
    """
    if not owner_token or not isinstance(owner_token, str):
        raise RestoreError("无法获取恢复独占锁")
    work_root.mkdir(parents=True, exist_ok=True)
    lp = _lock_path(work_root)
    payload = (_lock_owner_payload(owner_token) + "\n").encode("utf-8")

    # 至多：一次直接创建 + 一次 rename CAS 后创建
    for _attempt in range(2):
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if not allow_stale_takeover:
                raise RestoreError("已有恢复操作正在进行或遗留锁未清理")
            try:
                text = lp.read_text(encoding="utf-8")
            except OSError as exc:
                raise RestoreError("无法获取恢复独占锁") from exc
            info = _parse_lock_payload(text)
            if info is None:
                raise RestoreError("恢复锁状态无法判定，已保留现场并拒绝继续")
            existing_owner = info.get("owner")
            if existing_owner == owner_token:
                raise RestoreError("已有恢复操作正在进行或遗留锁未清理")
            pid = info.get("pid")
            if pid is None:
                raise RestoreError("恢复锁状态无法判定，已保留现场并拒绝继续")
            if _pid_is_alive(pid):
                raise RestoreError("已有恢复操作正在进行或遗留锁未清理")
            # rename CAS：仅赢家能把 restore.lock 移到唯一 sidecar
            sidecar = work_root / _stale_sidecar_name(text)
            try:
                os.rename(str(lp), str(sidecar))
            except FileNotFoundError as exc:
                # 他方已接管
                raise RestoreError("无法获取恢复独占锁") from exc
            except FileExistsError as exc:
                # 同 payload 的 sidecar 已存在：他方已 rename 或残留；fail-closed
                raise RestoreError("无法获取恢复独占锁") from exc
            except OSError as exc:
                raise RestoreError("无法获取恢复独占锁") from exc
            # 赢家：O_EXCL 重建本方锁
            try:
                fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError as exc:
                # 理论上不应发生；fail-closed，不 unlink 他人锁
                raise RestoreError("无法获取恢复独占锁") from exc
            except OSError as exc:
                raise RestoreError("无法获取恢复独占锁") from exc
            try:
                os.write(fd, payload)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            except OSError as exc:
                try:
                    os.close(fd)
                except OSError:
                    pass
                # 仅删除本方刚创建且 owner 匹配的锁
                try:
                    if lp.exists():
                        cur = _parse_lock_payload(lp.read_text(encoding="utf-8"))
                        if cur and cur.get("owner") == owner_token:
                            lp.unlink()
                except OSError:
                    pass
                raise RestoreError("无法获取恢复独占锁") from exc
            # 清理 sidecar（失败不阻断；非 restore.lock）
            try:
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                pass
            return fd
        except OSError as exc:
            raise RestoreError("无法获取恢复独占锁") from exc
        # 直接 O_EXCL 成功
        try:
            os.write(fd, payload)
            try:
                os.fsync(fd)
            except OSError:
                pass
            return fd
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                if lp.exists():
                    try:
                        cur = _parse_lock_payload(lp.read_text(encoding="utf-8"))
                        if cur and cur.get("owner") == owner_token:
                            lp.unlink()
                    except OSError:
                        pass
            except OSError:
                pass
            raise RestoreError("无法获取恢复独占锁") from exc
    raise RestoreError("无法获取恢复独占锁")


def _release_lock(
    work_root: Path,
    fd: Any,
    owner_token: Optional[str] = None,
) -> str:
    """释放本方锁：close → owner 复查 → unlink → 存在性复查。

    返回:
      - "no_fd"：未持有 fd（调用方未获锁）
      - "released"：本方 owner 匹配且 unlink 成功
      - "already_absent"：锁文件已不存在

    失败一律 Raise RestoreError，禁止吞 OSError。
    未成功获得锁（fd is None）时不得删除锁文件。
    """
    if fd is None:
        return "no_fd"
    try:
        os.close(fd)
    except OSError as exc:
        raise RestoreError("释放恢复独占锁失败，已保留现场") from exc
    if not owner_token:
        raise RestoreError("释放恢复独占锁失败，已保留现场")
    lp = _lock_path(work_root)
    try:
        if not lp.exists():
            return "already_absent"
        info = _parse_lock_payload(lp.read_text(encoding="utf-8"))
        if info is None or info.get("owner") != owner_token:
            raise RestoreError("释放恢复独占锁失败，已保留现场")
        lp.unlink()
        if lp.exists() or _path_component_exists(lp):
            raise RestoreError("释放恢复独占锁失败，已保留现场")
        return "released"
    except RestoreError:
        raise
    except OSError as exc:
        raise RestoreError("释放恢复独占锁失败，已保留现场") from exc


def _new_journal(operation_id: str, pre_backup_name: str) -> Dict[str, Any]:
    root_states = {
        name: {
            "backup_state": None,
            "intent": None,
            "result": None,
            # 以下为不可被后续 intent/result 覆盖语义的历史事实
            "live_existed_before": None,
            "hold_moved": False,
            "new_installed": False,
        }
        for name in ALL_LOGICAL_ROOTS
    }
    return {
        "schema_version": RESTORE_JOURNAL_SCHEMA_VERSION,
        "operation_id": operation_id,
        "phase": PHASE_PRECHECK,
        "pre_restore_backup_name": pre_backup_name,
        "roots": root_states,
    }


# ---------------------------------------------------------------------------
# 文件地图（哈希）— 用于 VERIFY / 回滚证据
# ---------------------------------------------------------------------------


def _hash_tree_map(root_path: Path) -> Dict[str, Tuple[int, str]]:
    """相对 POSIX 路径 -> (size, sha256)。根不存在返回空。"""
    if not root_path.exists():
        return {}
    if root_path.is_file():
        if _is_reparse_or_symlink(root_path) or not _is_regular_file(root_path):
            raise RestoreError("目标含符号链接或非普通文件")
        return {".": (root_path.lstat().st_size, _sha256_file(root_path))}
    if not root_path.is_dir():
        raise RestoreError("目标路径类型非法")
    if _is_reparse_or_symlink(root_path):
        raise RestoreError("目标含符号链接或重解析点")
    out: Dict[str, Tuple[int, str]] = {}
    for dirpath, dirnames, filenames in os.walk(
        root_path, topdown=True, followlinks=False
    ):
        current = Path(dirpath)
        if _is_reparse_or_symlink(current):
            raise RestoreError("目标含符号链接或重解析点")
        kept: List[str] = []
        for name in list(dirnames):
            child = current / name
            if _is_reparse_or_symlink(child):
                raise RestoreError("目标含符号链接或重解析点")
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            fp = current / name
            if _is_reparse_or_symlink(fp) or not _is_regular_file(fp):
                raise RestoreError("目标含符号链接或非普通文件")
            rel = fp.relative_to(root_path).as_posix()
            out[rel] = (fp.lstat().st_size, _sha256_file(fp))
    return out


def _snapshot_all_live(repo_root: Path) -> Dict[str, Dict[str, Tuple[int, str]]]:
    live = _live_paths(repo_root)
    snap: Dict[str, Dict[str, Tuple[int, str]]] = {}
    for name in ALL_LOGICAL_ROOTS:
        snap[name] = _hash_tree_map(live[name])
    return snap


def _copy_file_to(src: Path, dst: Path) -> Tuple[int, str]:
    if _is_reparse_or_symlink(src) or not _is_regular_file(src):
        raise RestoreError("拒绝复制符号链接、重解析点或非普通文件")
    st = src.lstat()
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    written = 0
    with src.open("rb") as rf, dst.open("wb") as wf:
        while True:
            chunk = rf.read(_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
            wf.write(chunk)
            written += len(chunk)
    if written != st.st_size:
        raise RestoreError("暂存复制大小不一致")
    digest = h.hexdigest()
    if _sha256_file(dst) != digest or _sha256_file(src) != digest:
        raise RestoreError("暂存复制哈希不一致")
    return written, digest


# ---------------------------------------------------------------------------
# staging / cutover / rollback
# ---------------------------------------------------------------------------


def _stage_backup(
    validated: Dict[str, Any],
    work_root: Path,
    fault_injector: Optional[Callable[..., None]] = None,
) -> Path:
    staging = work_root / _STAGING_NAME
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    bdir: Path = validated["backup_dir"]
    roots = validated["roots"]

    for logical in ALL_LOGICAL_ROOTS:
        state = roots[logical]["state"]
        if state == STATE_NOT_INCLUDED:
            continue
        if state == STATE_ABSENT:
            continue
        if state == STATE_EMPTY:
            (staging / logical).mkdir(parents=True, exist_ok=True)
            continue
        # present
        for item in validated["files_by_root"][logical]:
            rel = item["relative_path"]
            src = bdir / logical / Path(rel)
            dst = staging / logical / Path(rel)
            size, digest = _copy_file_to(src, dst)
            if size != item["size_bytes"] or digest != item["sha256"]:
                raise RestoreError("暂存文件与备份权威不一致")

    # 再核 DB
    if roots[LOGICAL_DB]["state"] == STATE_PRESENT:
        staged_db = staging / LOGICAL_DB / "biaoshu.db"
        try:
            _integrity_check_sqlite(staged_db)
        except BackupError as exc:
            raise RestoreError("暂存数据库完整性检查未通过") from exc

    _fault(fault_injector, "after_stage")
    return staging


def _hold_dir(work_root: Path, operation_id: str) -> Path:
    return work_root / _HOLD_NAME / operation_id


def _live_exists(path: Path) -> bool:
    return _path_component_exists(path) and path.exists()


def _hold_path_for(hold_root: Path, logical: str) -> Path:
    """hold 布局：db 为 hold/<logical>/biaoshu.db；其它根为 hold/<logical>/ 目录。"""
    if logical == LOGICAL_DB:
        return hold_root / logical / "biaoshu.db"
    return hold_root / logical


def _stage_payload_path(staging: Path, logical: str, state: str) -> Path:
    """staging 中待安装到 live 的载荷路径。"""
    if logical == LOGICAL_DB:
        return staging / logical / "biaoshu.db"
    return staging / logical


def _cutover_one_root(
    logical: str,
    live_path: Path,
    state: str,
    staging: Path,
    hold_root: Path,
    journal: Dict[str, Any],
    work_root: Path,
    fault_injector: Optional[Callable[..., None]] = None,
) -> None:
    """对单根执行 hold + 安装/移除；每步写 intent/result。

    live_existed_before / hold_moved / new_installed 为持久历史事实，
    不被后续 intent/result 覆盖语义。
    """
    root_info = journal["roots"][logical]
    if state == STATE_NOT_INCLUDED:
        root_info["live_existed_before"] = None
        root_info["intent"] = "skip_not_included"
        _atomic_write_journal(work_root, journal)
        _fault(fault_injector, "cutover_intent", logical=logical, step="skip")
        root_info["result"] = "skipped"
        _atomic_write_journal(work_root, journal)
        return

    hold_path = _hold_path_for(hold_root, logical)
    live_existed = _live_exists(live_path)
    # 历史事实：仅首次写入
    if root_info.get("live_existed_before") is None:
        root_info["live_existed_before"] = bool(live_existed)

    # 1) hold live（若存在）
    if live_existed:
        root_info["intent"] = "hold_live"
        _atomic_write_journal(work_root, journal)
        _fault(fault_injector, "cutover_before_hold", logical=logical)
        hold_path.parent.mkdir(parents=True, exist_ok=True)
        if hold_path.exists():
            raise RestoreError("回滚暂存位已存在，无法继续")
        os.replace(str(live_path), str(hold_path))
        root_info["hold_moved"] = True
        root_info["result"] = "held"
        _atomic_write_journal(work_root, journal)
        _fault(fault_injector, "cutover_after_hold", logical=logical)
    else:
        root_info["intent"] = "no_live"
        _atomic_write_journal(work_root, journal)
        root_info["result"] = "no_live"
        _atomic_write_journal(work_root, journal)

    # 2) 安装 staging 或保持 absent
    if state == STATE_ABSENT:
        root_info["intent"] = "ensure_absent"
        _atomic_write_journal(work_root, journal)
        _fault(fault_injector, "cutover_before_install", logical=logical)
        if _live_exists(live_path):
            raise RestoreError("目标根在应缺席时仍存在")
        root_info["result"] = "absent"
        _atomic_write_journal(work_root, journal)
        _fault(fault_injector, "cutover_after_install", logical=logical)
        return

    # present 或 empty：从 staging 安装到 live
    stage_path = _stage_payload_path(staging, logical, state)
    root_info["intent"] = "install_stage"
    _atomic_write_journal(work_root, journal)
    _fault(fault_injector, "cutover_before_install", logical=logical)
    live_path.parent.mkdir(parents=True, exist_ok=True)
    if _live_exists(live_path):
        raise RestoreError("目标根安装前仍被占用")
    if state == STATE_EMPTY:
        # 目录型空根：确保 stage 空目录存在
        if logical == LOGICAL_DB:
            raise RestoreError("数据库根不能为空状态")
        if not stage_path.exists():
            stage_path.mkdir(parents=True, exist_ok=True)
        os.replace(str(stage_path), str(live_path))
    else:
        if not stage_path.exists():
            raise RestoreError("暂存根缺失")
        os.replace(str(stage_path), str(live_path))
    root_info["new_installed"] = True
    root_info["result"] = "installed"
    _atomic_write_journal(work_root, journal)
    _fault(fault_injector, "cutover_after_install", logical=logical)


def _infer_root_rollback_state(
    info: Dict[str, Any],
    live_now: bool,
    hold_now: bool,
) -> Tuple[bool, bool, Optional[bool], bool]:
    """结合 journal 历史与物理态推断 hold_moved / new_installed / need_hold / touched。

    返回 (hold_moved, new_installed, need_hold_restore, touched)
    need_hold_restore: True 必须恢复 hold；False 不需要；无法唯一判定时抛错由调用方处理前返回 True。
    """
    intent = info.get("intent")
    result = info.get("result")
    live_existed_before = info.get("live_existed_before")
    hold_moved = bool(info.get("hold_moved"))
    new_installed = bool(info.get("new_installed"))

    # 物理态补判：hold 后 / result 前
    if hold_now and not live_now and intent in ("hold_live", "install_stage", "ensure_absent", None):
        if intent == "hold_live" or hold_moved or live_existed_before is True:
            hold_moved = True
    if hold_now and intent == "hold_live":
        hold_moved = True
        if live_existed_before is None:
            live_existed_before = True

    # 物理态补判：install 后 / result 前（result 可能仍为 held/no_live）
    if live_now and hold_now and intent == "install_stage":
        new_installed = True
        hold_moved = True
    if live_now and intent == "install_stage" and result in ("held", "no_live", None):
        if hold_now or hold_moved or live_existed_before is False:
            new_installed = True

    if result == "held":
        hold_moved = True
    if result == "installed":
        new_installed = True
    if result == "rollback_restored":
        return hold_moved, new_installed, False, True

    touched = bool(
        hold_moved
        or new_installed
        or intent
        in (
            "hold_live",
            "no_live",
            "install_stage",
            "ensure_absent",
            "rollback_remove_new",
            "rollback_restore_hold",
        )
        or result
        in (
            "held",
            "no_live",
            "installed",
            "absent",
            "rollback_removed_new",
            "rollback_restored",
        )
    )

    # 是否必须能从 hold 恢复旧数据
    if hold_now or hold_moved:
        need_hold = True
    elif live_existed_before is False:
        need_hold = False
    elif live_existed_before is True:
        need_hold = bool(
            new_installed
            or result
            in ("held", "installed", "absent", "rollback_removed_new")
            or intent
            in (
                "hold_live",
                "install_stage",
                "ensure_absent",
                "rollback_remove_new",
                "rollback_restore_hold",
            )
        )
    else:
        # live_existed_before 未知：仅当明确走过 no_live 才不需要 hold
        if result == "no_live" or intent == "no_live":
            need_hold = False
        elif result in ("held", "installed", "absent") or intent == "hold_live":
            need_hold = True
        elif new_installed or intent == "install_stage":
            # 无法唯一判定是否曾 hold
            need_hold = True
        else:
            need_hold = False

    # A14：hold_live 已写 intent、物理移动尚未发生 → 本根 no-op（不得因 intent 单独要求 hold）
    # 条件严格合取（A15 收紧）：
    #   intent=hold_live ∧ result is None ∧ hold_moved=False ∧ 推断后 new_installed is False
    #   ∧ live 在 ∧ hold 不在
    # 真实 cutover_before_hold 窗口 result 只能是 None；result=installed/absent 等非 held
    # 值不得再放行 A14（否则推断 new_installed 后会 remove 唯一 live 且逃过 A3）。
    # 不得影响 hold 后/result 前、install 后/result 前、result=held/installed/absent、
    # hold_moved=True 及真正 A3 hold 缺失门；不改 journal schema/validator（本轮靠 A3）。
    if (
        intent == "hold_live"
        and result is None
        and not hold_moved
        and new_installed is False
        and live_now
        and not hold_now
    ):
        need_hold = False

    return hold_moved, new_installed, need_hold, touched


def _strict_remove_path(path: Path, *, what: str) -> None:
    """严格删除文件或目录并复查；失败抛 RestoreError。"""
    if not path.exists() and not _path_component_exists(path):
        return
    try:
        if path.is_symlink() or _is_reparse_or_symlink(path):
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        elif path.is_file() or path.exists():
            path.unlink()
    except OSError as exc:
        raise RestoreError(f"{what}失败，已保留现场") from exc
    if path.exists() or _path_component_exists(path):
        raise RestoreError(f"{what}失败，已保留现场")


def _rollback_evidence_error() -> RestoreError:
    """回滚证据失败：固定中文，不写入/不泄漏文件名或 SHA。"""
    return RestoreError("回滚证据校验失败，已保留现场并拒绝继续")


def _root_restore_done_short_circuit(
    info: Dict[str, Any],
    *,
    live_now: bool,
    hold_now: bool,
    work_root: Path,
    journal: Dict[str, Any],
) -> bool:
    """A13：判定本根是否已 restore_done，若是则短路跳过（不 A3、不 remove_new）。

    条件必须收紧：
    1) result==rollback_restored 且 hold 缺失且 live 存在 → 直接跳过；
    2) intent==rollback_restore_hold 且 hold 缺失且 live 存在，且
       live_existed_before is True、hold_moved is True
       → 同卷原子 rename 指纹（intent 在 move 前落盘、move 后 hold 消失 live 出现），
         补写 result=rollback_restored 后跳过。

    负例（不得短路）：
    - install_stage/installed + hold 缺失 + 新 live → 继续 A3；
    - intent=rollback_restore_hold + hold 缺失 + live 缺失 → 继续 A3；
    - 仅 hold 缺失不足以证明 restored。
    不写摘要/路径/SHA；不扩展 journal 六字段。
    """
    result = info.get("result")
    intent = info.get("intent")

    # 已明确 restored：hold 已还回 live，禁止再 remove
    if result == "rollback_restored" and (not hold_now) and live_now:
        return True

    # rename 成功、result 未落盘：严格指纹，避免误判新 live
    if (
        intent == "rollback_restore_hold"
        and result != "rollback_restored"
        and (not hold_now)
        and live_now
        and info.get("live_existed_before") is True
        and info.get("hold_moved") is True
    ):
        info["result"] = "rollback_restored"
        _atomic_write_journal(work_root, journal)
        return True

    return False


def _rollback_from_journal(
    repo_root: Path,
    work_root: Path,
    journal: Dict[str, Any],
    fault_injector: Optional[Callable[..., None]] = None,
) -> None:
    """按 journal 历史 ∧ live/hold/stage 物理态逆序恢复。

    hold 应存在却缺失：fail-closed，禁止先删新 live、禁止写 ROLLED_BACK。
    无法唯一判定：保留 journal/现场并抛 RestoreError。

    A11 回滚证据：每个 hold→live 前在内存读取完整 rel→(size,sha256)；
    move 后 live 地图精确相等才可写 rollback_restored；全部应恢复根完成后
    对 live db 做 integrity_check。任一失败：固定 RestoreError、保留 journal/现场、
    phase 不得 ROLLED_BACK、不得 cleanup。证据不入 journal/日志。

    A13：每根在 A3/need_remove_new 前短路 restore_done，已 restored 根多根重入不误删。
    """
    journal["phase"] = PHASE_ROLLING_BACK
    _atomic_write_journal(work_root, journal)
    _fault(fault_injector, "rollback_begin")

    live = _live_paths(repo_root)
    op_id = journal["operation_id"]
    hold_root = _hold_dir(work_root, op_id)

    for logical in reversed(ALL_LOGICAL_ROOTS):
        info = journal["roots"].get(logical) or {}
        backup_state = info.get("backup_state")
        result = info.get("result")
        intent = info.get("intent")

        if backup_state == STATE_NOT_INCLUDED or result == "skipped" or intent == "skip_not_included":
            continue

        live_path = live[logical]
        hold_path = _hold_path_for(hold_root, logical)
        live_now = _live_exists(live_path)
        hold_now = hold_path.exists()

        hold_moved, new_installed, need_hold, touched = _infer_root_rollback_state(
            info, live_now, hold_now
        )
        if not touched and result is None and intent is None and not hold_now and not live_now:
            continue
        if not touched and result is None and intent is None and not hold_moved and not new_installed:
            # 尚未开始的根：若 live 仍在则无需动作
            continue

        # 持久化推断出的历史事实（只升不降）
        if hold_moved and not info.get("hold_moved"):
            info["hold_moved"] = True
        if new_installed and not info.get("new_installed"):
            info["new_installed"] = True
        if info.get("live_existed_before") is None and hold_moved:
            info["live_existed_before"] = True

        # A13：restore_done 短路 —— 必须在 A3 hold 缺失门与任何 need_remove_new 之前。
        # 已 restored 根（含多根重入）不得因 new_installed=True 再 remove；
        # 同卷原子 rename 成功但 result 未落盘时按指纹补写后跳过。
        # 严格限制条件：不得把 install_stage/installed + hold 缺失 + 新 live 误判为 restored。
        if _root_restore_done_short_circuit(
            info,
            live_now=live_now,
            hold_now=hold_now,
            work_root=work_root,
            journal=journal,
        ):
            continue

        # A3：hold 应在却缺失 → 在任何破坏性动作前 fail-closed
        if need_hold and not hold_now:
            _atomic_write_journal(work_root, journal)
            raise RestoreError("回滚所需旧数据缺失，已保留现场并拒绝继续")

        # 若新 live 已安装（含 install 后/result 前窗口），先移入 trash
        need_remove_new = bool(new_installed and live_now)
        if result == "installed" and live_now:
            need_remove_new = True
        if intent == "install_stage" and live_now and (hold_now or hold_moved):
            need_remove_new = True
        # held 后尚未 install：live 应不在；若误占用则也需清掉才能还 hold
        if hold_now and live_now and not need_remove_new:
            need_remove_new = True

        if need_remove_new:
            info["intent"] = "rollback_remove_new"
            _atomic_write_journal(work_root, journal)
            _fault(fault_injector, "rollback_before_remove", logical=logical)
            if _live_exists(live_path):
                trash = work_root / "trash" / op_id / (
                    "biaoshu.db" if logical == LOGICAL_DB else logical
                )
                trash.parent.mkdir(parents=True, exist_ok=True)
                if trash.exists():
                    _strict_remove_path(trash, what="回滚清理临时目录")
                try:
                    os.replace(str(live_path), str(trash))
                except OSError as exc:
                    raise RestoreError("回滚移除新数据失败，已保留现场") from exc
                _strict_remove_path(trash, what="回滚清理临时目录")
            info["result"] = "rollback_removed_new"
            _atomic_write_journal(work_root, journal)

        # 恢复 hold（A11：move 前后完整地图证据）
        if hold_path.exists():
            info["intent"] = "rollback_restore_hold"
            _atomic_write_journal(work_root, journal)
            _fault(fault_injector, "rollback_before_restore", logical=logical)
            if _live_exists(live_path):
                raise RestoreError("回滚时目标仍被占用，已保留现场")
            # move 前：内存完整 rel→(size,sha256)；证据不写 journal
            try:
                expected_map = _hash_tree_map(hold_path)
            except RestoreError:
                raise
            except Exception as exc:
                raise _rollback_evidence_error() from exc
            live_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(str(hold_path), str(live_path))
            except OSError as exc:
                raise RestoreError("回滚恢复旧数据失败，已保留现场") from exc
            # move 后：live 地图必须精确相等，才可写 rollback_restored
            try:
                actual_map = _hash_tree_map(live_path)
            except RestoreError as exc:
                # 不写 success result / 不写 ROLLED_BACK
                raise _rollback_evidence_error() from exc
            except Exception as exc:
                raise _rollback_evidence_error() from exc
            if actual_map != expected_map:
                raise _rollback_evidence_error()
            info["result"] = "rollback_restored"
            _atomic_write_journal(work_root, journal)
            _fault(fault_injector, "rollback_after_restore", logical=logical)
        elif need_hold:
            raise RestoreError("回滚所需旧数据缺失，已保留现场并拒绝继续")
        else:
            # 原本不存在的根：回滚后必须 absent；合法 no_live/absent 不误要求 hold
            leb = info.get("live_existed_before")
            if leb is False or result in ("no_live", "absent", "rollback_removed_new") or intent in (
                "no_live",
                "ensure_absent",
            ):
                if _live_exists(live_path):
                    raise _rollback_evidence_error()

    # 全部应恢复根完成后：对 live db 做 SQLite integrity_check
    db_live = live[LOGICAL_DB]
    if _live_exists(db_live):
        try:
            _integrity_check_sqlite(db_live)
        except BackupError as exc:
            raise RestoreError("回滚后数据库完整性检查未通过，已保留现场") from exc
        except Exception as exc:
            raise RestoreError("回滚后数据库完整性检查未通过，已保留现场") from exc

    journal["phase"] = PHASE_ROLLED_BACK
    _atomic_write_journal(work_root, journal)
    _fault(fault_injector, "rollback_end")


def _verify_live_against_backup(
    repo_root: Path,
    validated: Dict[str, Any],
    pre_not_included_snap: Optional[Dict[str, Tuple[int, str]]],
) -> None:
    live = _live_paths(repo_root)
    roots = validated["roots"]

    for logical in ALL_LOGICAL_ROOTS:
        state = roots[logical]["state"]
        path = live[logical]
        if state == STATE_NOT_INCLUDED:
            cur = _hash_tree_map(path)
            if pre_not_included_snap is None:
                raise RestoreError("未纳入根缺少操作前快照")
            if cur != pre_not_included_snap:
                raise RestoreError("未纳入根在恢复后发生了变化")
            continue
        if state == STATE_ABSENT:
            if _live_exists(path):
                raise RestoreError(f"恢复后根应不存在: {logical}")
            continue
        if state == STATE_EMPTY:
            if not path.exists() or not path.is_dir():
                raise RestoreError(f"恢复后空根缺失: {logical}")
            if _is_reparse_or_symlink(path):
                raise RestoreError("恢复后目标含重解析点")
            # 空目录：无文件
            if any(path.rglob("*")):
                # 仅允许无文件；子空目录也不应有（staging 是空根）
                files = [p for p in path.rglob("*") if p.is_file()]
                if files:
                    raise RestoreError(f"恢复后空根非空: {logical}")
            continue
        # present
        if not _live_exists(path):
            raise RestoreError(f"恢复后根缺失: {logical}")
        expected = {
            item["relative_path"]: (item["size_bytes"], item["sha256"])
            for item in validated["files_by_root"][logical]
        }
        if logical == LOGICAL_DB:
            # 单文件
            if not path.is_file():
                raise RestoreError("恢复后数据库不是文件")
            if _is_reparse_or_symlink(path):
                raise RestoreError("恢复后数据库路径非法")
            size = path.lstat().st_size
            digest = _sha256_file(path)
            exp = expected.get("biaoshu.db")
            if not exp or size != exp[0] or digest != exp[1]:
                raise RestoreError("恢复后数据库与备份不一致")
            try:
                _integrity_check_sqlite(path)
            except BackupError as exc:
                raise RestoreError("恢复后数据库完整性检查未通过") from exc
            continue

        actual = _hash_tree_map(path)
        if set(actual.keys()) != set(expected.keys()):
            raise RestoreError(f"恢复后文件集合不一致: {logical}")
        for rel, (sz, dg) in expected.items():
            if actual[rel] != (sz, dg):
                raise RestoreError(f"恢复后文件内容不一致: {logical}")


def _cleanup_artifacts(work_root: Path, operation_id: str) -> None:
    """严格清理 staging/hold/trash（不含 journal/锁/work_root）。"""
    # operation_id 必须已是 32 位 hex（由 journal 严格校验保证），禁止路径穿越
    if not isinstance(operation_id, str) or not _OP_ID_RE.fullmatch(operation_id):
        raise RestoreError(_JOURNAL_CORRUPT_MSG)
    staging = work_root / _STAGING_NAME
    hold = work_root / _HOLD_NAME / operation_id
    trash = work_root / _TRASH_NAME / operation_id
    for p in (staging, hold, trash):
        if p.exists() or _path_component_exists(p):
            _strict_remove_path(p, what="清理恢复工作文件")
    for parent in (work_root / _HOLD_NAME, work_root / _TRASH_NAME):
        if parent.exists() and parent.is_dir():
            try:
                remaining = any(parent.iterdir())
            except OSError as exc:
                raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
            if not remaining:
                _strict_remove_path(parent, what="清理恢复工作文件")


def _remove_journal_files(work_root: Path) -> None:
    """删除 journal.json 与 journal.json.tmp 并复查。"""
    jp = _journal_path(work_root)
    if jp.exists() or _path_component_exists(jp):
        try:
            jp.unlink()
        except OSError as exc:
            raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
        if jp.exists() or _path_component_exists(jp):
            raise RestoreError("清理恢复工作文件失败，已保留现场")
    tmp = work_root / _JOURNAL_TMP_NAME
    if tmp.exists() or _path_component_exists(tmp):
        _strict_remove_path(tmp, what="清理恢复工作文件")


def _remove_work_root_if_safe(work_root: Path) -> None:
    """仅当 work_root 为空（或仅剩可安全删除的空已知目录）时 rmdir。

    未知外来文件不得盲删：fail-closed。
    """
    if not work_root.exists() and not _path_component_exists(work_root):
        return
    if not work_root.is_dir():
        raise RestoreError("清理恢复工作文件失败，已保留现场")
    known_empty_dirs = {_STAGING_NAME, _HOLD_NAME, _TRASH_NAME}
    try:
        entries = list(work_root.iterdir())
    except OSError as exc:
        raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
    for entry in entries:
        name = entry.name
        # 残留 sidecar（stale rename）允许删除
        if name.startswith(_LOCK_NAME + ".stale-") and entry.is_file():
            _strict_remove_path(entry, what="清理恢复工作文件")
            continue
        if name in known_empty_dirs and entry.is_dir():
            try:
                if any(entry.iterdir()):
                    raise RestoreError("清理恢复工作文件失败，已保留现场")
            except RestoreError:
                raise
            except OSError as exc:
                raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
            _strict_remove_path(entry, what="清理恢复工作文件")
            continue
        # 任何其它残留（含 lock/journal/外来文件）均 fail-closed
        raise RestoreError("清理恢复工作文件失败，已保留现场")
    try:
        work_root.rmdir()
    except OSError as exc:
        raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
    if work_root.exists() or _path_component_exists(work_root):
        raise RestoreError("清理恢复工作文件失败，已保留现场")


def _rebuild_cleanup_pending_journal(
    work_root: Path,
    journal: Dict[str, Any],
) -> None:
    """保持或重建 COMMITTED_CLEANUP_PENDING journal（最小合法结构）。"""
    try:
        rebuilt = {
            "schema_version": RESTORE_JOURNAL_SCHEMA_VERSION,
            "operation_id": journal.get("operation_id"),
            "phase": PHASE_COMMITTED_CLEANUP_PENDING,
            "pre_restore_backup_name": journal.get("pre_restore_backup_name"),
            "roots": journal.get("roots"),
        }
        # 若结构已坏，用安全占位 roots（仅清理用，不再回滚 live）
        try:
            _strict_validate_journal(
                {
                    **rebuilt,
                    "phase": PHASE_COMMITTED_CLEANUP_PENDING,
                }
            )
            data = {
                "schema_version": RESTORE_JOURNAL_SCHEMA_VERSION,
                "operation_id": rebuilt["operation_id"],
                "phase": PHASE_COMMITTED_CLEANUP_PENDING,
                "pre_restore_backup_name": rebuilt["pre_restore_backup_name"],
                "roots": rebuilt["roots"],
            }
        except RestoreError:
            op = journal.get("operation_id")
            if not isinstance(op, str) or not _OP_ID_RE.fullmatch(op):
                op = "0" * 32
            name = journal.get("pre_restore_backup_name")
            if not _is_safe_prebackup_basename(name):
                name = "unknown-pre-restore"
            roots = {
                n: {
                    "backup_state": None,
                    "intent": None,
                    "result": None,
                    "live_existed_before": None,
                    "hold_moved": False,
                    "new_installed": False,
                }
                for n in ALL_LOGICAL_ROOTS
            }
            data = {
                "schema_version": RESTORE_JOURNAL_SCHEMA_VERSION,
                "operation_id": op,
                "phase": PHASE_COMMITTED_CLEANUP_PENDING,
                "pre_restore_backup_name": name,
                "roots": roots,
            }
        _atomic_write_journal(work_root, data)
    except Exception:
        # 重建失败：保留现场，由调用方仍抛固定清理未完成
        pass


def _cleanup_work(
    work_root: Path,
    operation_id: str,
    keep_journal: bool = False,
) -> None:
    """严格清理 staging/hold/trash[/journal] 并复查；真实删除失败不得吞。

    注意：不释放锁、不删除 work_root。调用方须按 A9 顺序：
    artifacts →（可选保留 journal）→ release lock → 删 journal → 空 work_root。
    """
    _cleanup_artifacts(work_root, operation_id)
    if not keep_journal:
        _remove_journal_files(work_root)


def _session_cleanup(
    work_root: Path,
    operation_id: str,
    lock_fd: Any,
    owner_token: str,
    *,
    journal: Optional[Dict[str, Any]] = None,
    committed: bool = False,
) -> Any:
    """会话级清理：artifacts → 释放锁 → 删 journal → 安全空 work_root。

    committed=True 时任何失败保持/重建 COMMITTED_CLEANUP_PENDING 并抛
    「恢复完成但清理未完成」；提交前失败抛清理失败，且优先保留 journal
    避免无 journal 孤立锁。返回 None 表示锁已释放（调用方勿再 release）。
    """
    fd = lock_fd

    def _best_effort_release() -> None:
        nonlocal fd
        if fd is None:
            return
        try:
            _release_lock(work_root, fd, owner_token)
        except RestoreError:
            # 关闭 fd 语义：release 内部先 close；若已 close 失败则下面兜底
            try:
                os.close(fd)
            except OSError:
                pass
        fd = None

    try:
        _cleanup_artifacts(work_root, operation_id)
    except BaseException as exc:
        if committed:
            if journal is not None:
                try:
                    j = dict(journal)
                    j["phase"] = PHASE_COMMITTED_CLEANUP_PENDING
                    _atomic_write_journal(work_root, j)
                except Exception:
                    _rebuild_cleanup_pending_journal(work_root, journal)
            # 保持 pending journal；尽量释放本方锁（失败则残留锁+journal，供下次 stale recover）
            _best_effort_release()
            raise RestoreError(_CLEANUP_PENDING_MSG) from exc
        # 提交前：保留 journal，仍尝试释放锁以免无 journal 孤立锁
        _best_effort_release()
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError("清理恢复工作文件失败，已保留现场") from exc

    # 锁释放必须在删 journal 之前；结果必须被消费
    if fd is not None:
        try:
            status = _release_lock(work_root, fd, owner_token)
            if status not in ("released", "already_absent", "no_fd"):
                raise RestoreError("释放恢复独占锁失败，已保留现场")
            fd = None
        except BaseException as exc:
            if committed:
                if journal is not None:
                    try:
                        j = dict(journal)
                        j["phase"] = PHASE_COMMITTED_CLEANUP_PENDING
                        _atomic_write_journal(work_root, j)
                    except Exception:
                        _rebuild_cleanup_pending_journal(work_root, journal)
                # fd 可能已 close；标记消费
                fd = None
                raise RestoreError(_CLEANUP_PENDING_MSG) from exc
            # 提交前：journal 仍在，锁可能残留但非“无 journal 孤立”
            fd = None
            if isinstance(exc, RestoreError):
                raise
            raise RestoreError("释放恢复独占锁失败，已保留现场") from exc

    try:
        _remove_journal_files(work_root)
        _remove_work_root_if_safe(work_root)
    except BaseException as exc:
        if committed:
            if journal is not None:
                _rebuild_cleanup_pending_journal(work_root, journal)
            raise RestoreError(_CLEANUP_PENDING_MSG) from exc
        # 提交前：锁已释放；journal 删除失败则 journal 仍在
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError("清理恢复工作文件失败，已保留现场") from exc
    return None  # 锁已释放


# ---------------------------------------------------------------------------
# 崩溃重入
# ---------------------------------------------------------------------------


def recover_incomplete_restore(
    repo_root: Any,
    work_root: Any = None,
    service_probe: Optional[Callable[[str, int], bool]] = None,
    fault_injector: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """收敛未完成 journal；无 journal 则空操作。

    返回 {"action": "none|cleanup|rollback|blocked", "phase": ...}
    公共 API 边界（A10）：RestoreError 原样；BackupError→RestoreError；
    其它 Exception→固定中文 RestoreError；不吞 KeyboardInterrupt/SystemExit。
    """
    try:
        return _recover_incomplete_restore_impl(
            repo_root,
            work_root=work_root,
            service_probe=service_probe,
            fault_injector=fault_injector,
        )
    except RestoreError:
        raise
    except BackupError as exc:
        raise _as_restore_error(exc) from exc
    except Exception as exc:
        raise _as_restore_error(exc, "离线恢复失败") from exc


def _recover_incomplete_restore_impl(
    repo_root: Any,
    work_root: Any = None,
    service_probe: Optional[Callable[[str, int], bool]] = None,
    fault_injector: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    root = _resolve_repo_root(repo_root)
    wr = (
        _abspath_no_follow(work_root)
        if work_root is not None
        else _default_work_root(root)
    )

    if not wr.exists():
        return {"action": "none", "phase": None}

    # 任何 lock 接管 / cleanup / hold 路径前：全面 journal 校验
    journal = _read_journal(wr)

    if journal is None:
        # 无 journal 的孤立锁：不盲删，留给后续 acquire fail-closed
        return {"action": "none", "phase": None}

    phase = journal["phase"]
    op_id = journal["operation_id"]

    owner_token = uuid.uuid4().hex
    lock_fd: Any = None
    lock_consumed = False
    try:
        # 有效未完成 journal + 失效 owner → rename CAS 接管
        lock_fd = _acquire_lock(wr, owner_token, allow_stale_takeover=True)
    except RestoreError:
        raise

    try:
        if phase in (PHASE_PRECHECK, PHASE_PRE_BACKUP, PHASE_STAGE, PHASE_ROLLED_BACK):
            lock_fd = _session_cleanup(
                wr,
                op_id,
                lock_fd,
                owner_token,
                journal=journal,
                committed=False,
            )
            lock_consumed = True
            return {"action": "cleanup", "phase": phase}

        if phase in (PHASE_CUTOVER, PHASE_VERIFY, PHASE_ROLLING_BACK):
            try:
                assert_services_stopped(probe=service_probe)
            except BackupError as exc:
                raise _as_restore_error(exc) from exc
            _rollback_from_journal(root, wr, journal, fault_injector=fault_injector)
            lock_fd = _session_cleanup(
                wr,
                op_id,
                lock_fd,
                owner_token,
                journal=journal,
                committed=False,
            )
            lock_consumed = True
            return {"action": "rollback", "phase": phase}

        if phase in (PHASE_COMMIT, PHASE_COMMITTED_CLEANUP_PENDING):
            # 只清理 hold/stage/锁/journal/空 work_root，不回滚新 live
            try:
                lock_fd = _session_cleanup(
                    wr,
                    op_id,
                    lock_fd,
                    owner_token,
                    journal=journal,
                    committed=True,
                )
                lock_consumed = True
            except RestoreError as exc:
                if str(exc) == _CLEANUP_PENDING_MSG:
                    raise
                journal["phase"] = PHASE_COMMITTED_CLEANUP_PENDING
                try:
                    _atomic_write_journal(wr, journal)
                except RestoreError:
                    _rebuild_cleanup_pending_journal(wr, journal)
                raise RestoreError(_CLEANUP_PENDING_MSG) from exc
            return {"action": "cleanup", "phase": phase}

        # 已知 phase 集合已在校验期穷尽；防御分支
        raise RestoreError(_JOURNAL_CORRUPT_MSG)
    finally:
        if not lock_consumed and lock_fd is not None:
            try:
                _release_lock(wr, lock_fd, owner_token)
            except RestoreError:
                # 不覆盖主路径更重要异常；journal 仍在则非无 journal 孤立锁
                if sys.exc_info()[1] is None:
                    raise


# ---------------------------------------------------------------------------
# 主恢复流程
# ---------------------------------------------------------------------------


def restore_offline_backup(
    repo_root: Any,
    backup_dir: Any,
    pre_restore_destination_root: Any = None,
    work_root: Any = None,
    service_probe: Optional[Callable[[str, int], bool]] = None,
    now: Optional[datetime] = None,
    git_head: Optional[str] = None,
    fault_injector: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """执行完整离线恢复。成功返回摘要字典。

    公共 API 边界（A10）：RestoreError 原样；BackupError→RestoreError；
    其它 Exception→固定中文 RestoreError；不吞 KeyboardInterrupt/SystemExit。
    业务回滚语义仍在实现层完成，此处仅做类型归一。
    """
    try:
        return _restore_offline_backup_impl(
            repo_root,
            backup_dir,
            pre_restore_destination_root=pre_restore_destination_root,
            work_root=work_root,
            service_probe=service_probe,
            now=now,
            git_head=git_head,
            fault_injector=fault_injector,
        )
    except RestoreError:
        raise
    except BackupError as exc:
        raise _as_restore_error(exc) from exc
    except Exception as exc:
        raise _as_restore_error(exc, "离线恢复失败") from exc


def _restore_offline_backup_impl(
    repo_root: Any,
    backup_dir: Any,
    pre_restore_destination_root: Any = None,
    work_root: Any = None,
    service_probe: Optional[Callable[[str, int], bool]] = None,
    now: Optional[datetime] = None,
    git_head: Optional[str] = None,
    fault_injector: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    root = _resolve_repo_root(repo_root)
    bdir = _abspath_no_follow(backup_dir)

    if not _is_outside_repo(bdir, root):
        raise RestoreError("备份目录必须位于仓库外")
    _assert_no_reparse_restore(bdir, "备份目录不能是符号链接或重解析点")

    wr = (
        _abspath_no_follow(work_root)
        if work_root is not None
        else _default_work_root(root)
    )
    if not _is_outside_repo(wr, root):
        raise RestoreError("恢复工作根必须位于仓库外")
    if not _same_volume(wr, root):
        raise RestoreError("恢复工作根必须与仓库同卷")
    # 工作根祖先 reparse
    if _path_component_exists(wr):
        _assert_no_reparse_restore(wr, "恢复工作根不能是符号链接或重解析点")
    else:
        _assert_no_reparse_restore(wr.parent, "恢复工作根不能是符号链接或重解析点")

    pre_root = (
        _abspath_no_follow(pre_restore_destination_root)
        if pre_restore_destination_root is not None
        else _default_prebackup_root(root)
    )
    if not _is_outside_repo(pre_root, root):
        raise RestoreError("恢复前备份目标必须位于仓库外")
    if _path_component_exists(pre_root):
        _assert_no_reparse_restore(pre_root, "恢复前备份目标不能是符号链接或重解析点")

    # A8：任何 pre-backup/lock/staging 写入前目录隔离
    _assert_restore_layout_isolation(bdir, wr, pre_root)

    # 先收敛遗留 journal（可接管失效锁）
    if wr.exists() and _journal_path(wr).exists():
        recover_incomplete_restore(
            root, work_root=wr, service_probe=service_probe, fault_injector=fault_injector
        )

    # PRECHECK（零 live 写入）
    try:
        assert_services_stopped(probe=service_probe)
    except BackupError as exc:
        raise _as_restore_error(exc) from exc

    validated = load_and_validate_backup(bdir)
    plan = build_restore_plan(root, validated)

    # not_included 操作前快照
    pre_sem_snap: Optional[Dict[str, Tuple[int, str]]] = None
    if validated["roots"][LOGICAL_SEMANTIC_MODELS]["state"] == STATE_NOT_INCLUDED:
        pre_sem_snap = _hash_tree_map(_live_paths(root)[LOGICAL_SEMANTIC_MODELS])

    operation_id = uuid.uuid4().hex
    owner_token = operation_id
    # PRE_BACKUP
    _fault(fault_injector, "before_pre_backup")
    include_sem = bool(plan["include_semantic_in_prebackup"])
    try:
        pre_backup_path = create_offline_backup(
            repo_root=root,
            destination_root=pre_root,
            include_semantic_models=include_sem,
            now=now,
            git_head=git_head,
            service_probe=service_probe,
        )
    except BackupError as exc:
        # BackupError 中文原因可保留；公共边界另有归一
        msg = str(exc).strip()
        raise RestoreError(
            f"恢复前备份失败: {msg}" if msg else "恢复前备份失败"
        ) from exc
    except Exception as exc:
        # 不拼接原始异常文本，避免泄密
        raise RestoreError("恢复前备份失败") from exc

    _fault(fault_injector, "after_pre_backup")
    pre_backup_name = pre_backup_path.name
    # 新 journal 的 basename 必须可被严格校验接受
    if not _is_safe_prebackup_basename(pre_backup_name):
        raise RestoreError("恢复前备份名称非法")

    lock_fd: Any = None
    lock_consumed = False
    journal = _new_journal(operation_id, pre_backup_name)
    for logical in ALL_LOGICAL_ROOTS:
        journal["roots"][logical]["backup_state"] = validated["roots"][logical]["state"]

    try:
        lock_fd = _acquire_lock(wr, owner_token, allow_stale_takeover=False)
        journal["phase"] = PHASE_PRE_BACKUP
        _atomic_write_journal(wr, journal)

        # STAGE
        journal["phase"] = PHASE_STAGE
        _atomic_write_journal(wr, journal)
        staging = _stage_backup(validated, wr, fault_injector=fault_injector)

        # CUTOVER
        journal["phase"] = PHASE_CUTOVER
        _atomic_write_journal(wr, journal)
        hold_root = _hold_dir(wr, operation_id)
        hold_root.mkdir(parents=True, exist_ok=True)
        live = _live_paths(root)

        try:
            for logical in ALL_LOGICAL_ROOTS:
                state = validated["roots"][logical]["state"]
                _cutover_one_root(
                    logical=logical,
                    live_path=live[logical],
                    state=state,
                    staging=staging,
                    hold_root=hold_root,
                    journal=journal,
                    work_root=wr,
                    fault_injector=fault_injector,
                )
        except Exception:
            # 提交前失败 → 回滚
            try:
                _rollback_from_journal(
                    root, wr, journal, fault_injector=fault_injector
                )
            except RestoreError:
                raise
            except Exception as exc:
                raise RestoreError("回滚失败，已保留现场") from exc
            lock_fd = _session_cleanup(
                wr,
                operation_id,
                lock_fd,
                owner_token,
                journal=journal,
                committed=False,
            )
            lock_consumed = True
            raise

        # VERIFY
        journal["phase"] = PHASE_VERIFY
        _atomic_write_journal(wr, journal)
        try:
            _fault(fault_injector, "before_verify")
            _verify_live_against_backup(root, validated, pre_sem_snap)
            _fault(fault_injector, "after_verify")
        except Exception:
            try:
                _rollback_from_journal(
                    root, wr, journal, fault_injector=fault_injector
                )
            except RestoreError:
                raise
            except Exception as exc:
                raise RestoreError("回滚失败，已保留现场") from exc
            lock_fd = _session_cleanup(
                wr,
                operation_id,
                lock_fd,
                owner_token,
                journal=journal,
                committed=False,
            )
            lock_consumed = True
            raise

        # COMMIT
        journal["phase"] = PHASE_COMMIT
        _atomic_write_journal(wr, journal)
        _fault(fault_injector, "after_commit")

        cleanup_ok = True
        try:
            # 提交后：artifacts → 释放锁 → 删 journal → 空 work_root
            lock_fd = _session_cleanup(
                wr,
                operation_id,
                lock_fd,
                owner_token,
                journal=journal,
                committed=True,
            )
            lock_consumed = True
            _fault(fault_injector, "after_cleanup")
        except Exception as exc:
            cleanup_ok = False
            lock_consumed = True  # session_cleanup 已处理或保留 pending
            lock_fd = None
            if str(exc) != _CLEANUP_PENDING_MSG:
                journal["phase"] = PHASE_COMMITTED_CLEANUP_PENDING
                try:
                    _atomic_write_journal(wr, journal)
                except Exception:
                    _rebuild_cleanup_pending_journal(wr, journal)

        # 摘要（逻辑根计数，无路径正文）
        summary_roots = {
            name: {
                "state": validated["roots"][name]["state"],
                "file_count": validated["roots"][name]["file_count"],
            }
            for name in ALL_LOGICAL_ROOTS
        }
        result = {
            "pre_restore_backup": pre_backup_path,
            "roots": summary_roots,
            "cleanup_completed": cleanup_ok,
        }
        if not cleanup_ok:
            raise RestoreError(_CLEANUP_PENDING_MSG)
        return result
    except RestoreError:
        raise
    except BackupError as exc:
        raise _as_restore_error(exc) from exc
    except Exception as exc:
        # 尝试回滚（业务语义）；类型归一用固定中文，不泄密
        try:
            if journal.get("phase") in (
                PHASE_CUTOVER,
                PHASE_VERIFY,
                PHASE_ROLLING_BACK,
            ):
                _rollback_from_journal(
                    root, wr, journal, fault_injector=fault_injector
                )
                if not lock_consumed:
                    lock_fd = _session_cleanup(
                        wr,
                        operation_id,
                        lock_fd,
                        owner_token,
                        journal=journal,
                        committed=False,
                    )
                    lock_consumed = True
        except Exception:
            pass
        raise RestoreError("离线恢复失败") from exc
    finally:
        # 未成功获锁不得删锁；已由 session_cleanup 消费则跳过
        if not lock_consumed and lock_fd is not None:
            try:
                _release_lock(wr, lock_fd, owner_token)
            except RestoreError:
                if sys.exc_info()[1] is None:
                    raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="标书离线恢复（标准库核心）")
    parser.add_argument("--repo-root", required=True, help="仓库根绝对路径")
    parser.add_argument("--backup-dir", required=True, help="备份目录（仓库外）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="在用户确认后由包装层传入；执行实际恢复",
    )
    parser.add_argument(
        "--pre-restore-destination-root",
        default=None,
        help="恢复前备份目标根（可选，默认仓库同级 biaoshu-backups）",
    )
    parser.add_argument(
        "--work-root",
        default=None,
        help="恢复工作根（可选，默认仓库同级 biaoshu-restore-work）",
    )
    # 故意不提供 skip/force/注入参数
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.apply:
        print("未确认执行恢复（缺少 --apply）", file=sys.stderr)
        return 1

    try:
        result = restore_offline_backup(
            repo_root=args.repo_root,
            backup_dir=args.backup_dir,
            pre_restore_destination_root=args.pre_restore_destination_root,
            work_root=args.work_root,
        )
    except RestoreError as exc:
        msg = str(exc)
        print(msg, file=sys.stderr)
        if msg == _CLEANUP_PENDING_MSG:
            return EXIT_CLEANUP_PENDING
        return 1

    pre_path = result["pre_restore_backup"]
    print(str(pre_path))
    # 固定摘要：逻辑根与文件计数
    parts = []
    for name in ALL_LOGICAL_ROOTS:
        info = result["roots"][name]
        parts.append(f"{name}={info['state']}:{info['file_count']}")
    print("恢复成功 " + " ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
