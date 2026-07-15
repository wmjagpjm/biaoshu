"""
模块：P12A editor-state 手动检查点只读库服务
用途：在项目锁内读取权威 editor-state，生成规范快照并有限保留最近 20 条。
对接：api.editor_state_checkpoints；editor_state_service.get_editor_state；
  EditorStateCheckpointRow。
二次开发：
  - 禁止接受客户端 snapshot/版本/计数/名称；
  - 创建与裁剪同事务；失败必须 rollback；
  - 列表 SQL 只投影元数据列，绝不 select snapshot_json；
  - 不实现恢复、删除、自动历史或修改当前 editor-state。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.entities import (
    EditorStateCheckpointRow,
    Project,
    ProjectEditorStateRow,
    utc_now,
)
from app.services import editor_state_service
from app.services.project_service import ProjectNotFoundError

MAX_CHECKPOINTS_PER_PROJECT = 20
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MiB
MIN_SNAPSHOT_BYTES = 1

# 契约精确 13 键（排序序列化时由 sort_keys 决定字节序）
SNAPSHOT_KEYS: tuple[str, ...] = (
    "outline",
    "chapters",
    "facts",
    "mode",
    "analysis",
    "responseMatrix",
    "guidance",
    "parsedMarkdown",
    "businessQualify",
    "businessToc",
    "businessQuote",
    "businessCommit",
    "analysisOverview",
)
SNAPSHOT_KEY_SET = frozenset(SNAPSHOT_KEYS)

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_CHECKPOINT_NOT_FOUND = "editor_state_checkpoint_not_found"
MSG_CHECKPOINT_NOT_FOUND = "检查点不存在或不可访问"
CODE_CHECKPOINT_TOO_LARGE = "editor_state_checkpoint_too_large"
MSG_CHECKPOINT_TOO_LARGE = "检查点快照超过大小限制，未写入"
CODE_CHECKPOINT_CORRUPT = "editor_state_checkpoint_corrupt"
MSG_CHECKPOINT_CORRUPT = "检查点数据损坏，无法读取"


class EditorStateCheckpointError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_checkpoints。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _new_checkpoint_id() -> str:
    """用途：生成不透明检查点 ID（escp_ + 32 hex）。"""
    return f"escp_{secrets.token_hex(16)}"


def _canonical_snapshot_json(snapshot: dict[str, Any]) -> str:
    """
    用途：紧凑 sort_keys UTF-8 标准 JSON（规范快照序列化）。
    二次开发：必须 allow_nan=False，禁止写出 NaN/Infinity 非标准常量。
    """
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_state_version(snapshot_json: str) -> str:
    """
    用途：对规范快照 JSON 字节做 SHA-256，取前 32 hex 并加 esv_ 前缀。
    二次开发：必须对独立序列化字节重算，禁止复用其它哈希字段。
    """
    digest = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    return "esv_" + digest[:32]


def count_outline_nodes(outline: Any) -> int:
    """
    用途：迭代统计 outline 树中的字典节点数，禁止递归爆栈。
    规则：仅计 dict 节点；children 为 list 时继续遍历。
    """
    if outline is None:
        return 0
    count = 0
    stack: list[Any] = []
    if isinstance(outline, list):
        stack.extend(outline)
    elif isinstance(outline, dict):
        stack.append(outline)
    else:
        return 0
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            count += 1
            children = node.get("children")
            if isinstance(children, list):
                stack.extend(children)
    return count


def count_chapter_dicts(chapters: Any) -> int:
    """用途：仅统计 chapters 列表中的字典项。"""
    if not isinstance(chapters, list):
        return 0
    return sum(1 for item in chapters if isinstance(item, dict))


def extract_canonical_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """
    用途：从 get_editor_state 规范输出抽取精确 13 键。
    二次开发：不得写入 projectId/updatedAt/responseMatrixVersion 等派生/敏感字段。
    """
    return {key: state.get(key) for key in SNAPSHOT_KEYS}


def _lock_project_for_checkpoint(
    db: Session, workspace_id: str, project_id: str
) -> Project:
    """
    用途：创建检查点前取得项目级写锁，使读状态→插入→裁剪同事务串行。
    二次开发：
      - SQLite：projects 无副作用 UPDATE 取文件库写锁；
      - 其他方言：project 与已存在 editor-state 行 FOR UPDATE。
    """
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        result = db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
            .values(updated_at=Project.updated_at)
        )
        if result.rowcount == 0:
            raise EditorStateCheckpointError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )
        db.expire_all()
        project = db.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise EditorStateCheckpointError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )
        return project

    project = db.execute(
        select(Project)
        .where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if project is None:
        raise EditorStateCheckpointError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )
    # 已存在 editor-state 行一并加锁，避免与其它写路径交错读到半写入状态
    db.execute(
        select(ProjectEditorStateRow)
        .where(ProjectEditorStateRow.project_id == project_id)
        .with_for_update()
    ).scalar_one_or_none()
    return project


def _require_project(
    db: Session, workspace_id: str, project_id: str, *, lock: bool
) -> Project:
    """用途：校验当前空间项目；可选加锁。技术标与商务标均允许。"""
    if lock:
        return _lock_project_for_checkpoint(db, workspace_id, project_id)
    project = db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if project is None:
        raise EditorStateCheckpointError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )
    return project


def _meta_from_row(row: EditorStateCheckpointRow) -> dict[str, Any]:
    """用途：ORM 行 → API 元数据字典。"""
    return {
        "checkpoint_id": row.id,
        "state_version": row.state_version,
        "snapshot_bytes": int(row.snapshot_bytes),
        "outline_node_count": int(row.outline_node_count),
        "chapter_count": int(row.chapter_count),
        "created_at": row.created_at,
    }


def _insert_checkpoint_row(
    db: Session,
    *,
    checkpoint_id: str,
    workspace_id: str,
    project_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    outline_node_count: int,
    chapter_count: int,
) -> EditorStateCheckpointRow:
    """
    用途：插入检查点行（供测试可 patch，模拟插入中途失败）。
    对接：create_editor_state_checkpoint。
    """
    row = EditorStateCheckpointRow(
        id=checkpoint_id,
        workspace_id=workspace_id,
        project_id=project_id,
        snapshot_json=snapshot_json,
        state_version=state_version,
        snapshot_bytes=snapshot_bytes,
        outline_node_count=outline_node_count,
        chapter_count=chapter_count,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _trim_checkpoints(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：同事务内仅保留本项目最近 20 条（created_at DESC, id DESC）。
    二次开发：
      - 只 SELECT id，禁止加载 snapshot_json 正文；
      - DELETE 必须带 workspace_id/project_id/id 范围约束；
      - 禁止跨项目/跨空间删除。
    """
    keep_ids = (
        db.execute(
            select(EditorStateCheckpointRow.id)
            .where(
                EditorStateCheckpointRow.workspace_id == workspace_id,
                EditorStateCheckpointRow.project_id == project_id,
            )
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    drop_ids = list(keep_ids[MAX_CHECKPOINTS_PER_PROJECT:])
    if not drop_ids:
        return
    db.execute(
        delete(EditorStateCheckpointRow).where(
            EditorStateCheckpointRow.workspace_id == workspace_id,
            EditorStateCheckpointRow.project_id == project_id,
            EditorStateCheckpointRow.id.in_(drop_ids),
        )
    )


def create_editor_state_checkpoint(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：锁后读取权威 editor-state，构造规范快照并插入；同事务裁剪。
    对接：POST /api/projects/{projectId}/editor-state-checkpoints。
    二次开发：
      - 自项目锁起至 commit 的全部步骤必须在同一 try 回滚域内；
      - 提交前构造返回元数据；提交后不得 refresh；
      - 任何业务/运行时异常都先 rollback 再原样抛出，不吞异常。
    """
    try:
        _require_project(db, workspace_id, project_id, lock=True)
        # 锁后重读权威状态；get_editor_state 内会再校验项目归属
        try:
            state = editor_state_service.get_editor_state(
                db, workspace_id, project_id
            )
        except ProjectNotFoundError:
            raise EditorStateCheckpointError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            ) from None

        snapshot = extract_canonical_snapshot(state)
        snapshot_json = _canonical_snapshot_json(snapshot)
        snapshot_bytes = len(snapshot_json.encode("utf-8"))
        if snapshot_bytes < MIN_SNAPSHOT_BYTES or snapshot_bytes > MAX_SNAPSHOT_BYTES:
            raise EditorStateCheckpointError(
                413, CODE_CHECKPOINT_TOO_LARGE, MSG_CHECKPOINT_TOO_LARGE
            )

        state_version = compute_state_version(snapshot_json)
        outline_node_count = count_outline_nodes(snapshot.get("outline"))
        chapter_count = count_chapter_dicts(snapshot.get("chapters"))
        checkpoint_id = _new_checkpoint_id()

        row = _insert_checkpoint_row(
            db,
            checkpoint_id=checkpoint_id,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=snapshot_json,
            state_version=state_version,
            snapshot_bytes=snapshot_bytes,
            outline_node_count=outline_node_count,
            chapter_count=chapter_count,
        )
        # 提交前构造元数据，避免 commit 成功后 refresh 失败导致假失败/重复创建
        meta = {
            "checkpoint_id": checkpoint_id,
            "state_version": state_version,
            "snapshot_bytes": snapshot_bytes,
            "outline_node_count": outline_node_count,
            "chapter_count": chapter_count,
            "created_at": row.created_at,
        }
        _trim_checkpoints(db, workspace_id, project_id)
        db.commit()
        return meta
    except EditorStateCheckpointError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def list_editor_state_checkpoints(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：固定最近 20 条元数据列表；SQL 显式投影，不含 snapshot_json。
    对接：GET /api/projects/{projectId}/editor-state-checkpoints。
    """
    _require_project(db, workspace_id, project_id, lock=False)
    rows = db.execute(
        select(
            EditorStateCheckpointRow.id,
            EditorStateCheckpointRow.state_version,
            EditorStateCheckpointRow.snapshot_bytes,
            EditorStateCheckpointRow.outline_node_count,
            EditorStateCheckpointRow.chapter_count,
            EditorStateCheckpointRow.created_at,
        )
        .where(
            EditorStateCheckpointRow.workspace_id == workspace_id,
            EditorStateCheckpointRow.project_id == project_id,
        )
        .order_by(
            EditorStateCheckpointRow.created_at.desc(),
            EditorStateCheckpointRow.id.desc(),
        )
        .limit(MAX_CHECKPOINTS_PER_PROJECT)
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "checkpoint_id": row.id,
                "state_version": row.state_version,
                "snapshot_bytes": int(row.snapshot_bytes),
                "outline_node_count": int(row.outline_node_count),
                "chapter_count": int(row.chapter_count),
                "created_at": row.created_at,
            }
        )
    return {"items": items}


def _corrupt() -> EditorStateCheckpointError:
    """用途：统一构造脱敏损坏错误，禁止附带内部异常细节。"""
    return EditorStateCheckpointError(
        500, CODE_CHECKPOINT_CORRUPT, MSG_CHECKPOINT_CORRUPT
    )


def _safe_nonneg_int(value: Any) -> int:
    """
    用途：安全解析非负整数元数据；失败或负数抛固定损坏错误。
    二次开发：不得把 ValueError/TypeError 原文上抛。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        # 拒绝 bool（是 int 子类）与非 int 类型；再尝试严格字符串整型会泄漏细节，直接损坏
        if isinstance(value, str):
            # 拒绝字符串数字，避免“半解析”路径差异
            raise _corrupt() from None
        try:
            # 仅接受可无损转为 int 且结果非负的有限数值
            if isinstance(value, float):
                raise _corrupt() from None
            parsed = int(value)  # type: ignore[arg-type]
        except EditorStateCheckpointError:
            raise
        except Exception:
            raise _corrupt() from None
        if parsed != value:
            raise _corrupt() from None
        value = parsed
    if value < 0:
        raise _corrupt() from None
    return value


def _validate_snapshot_payload(
    *,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: Any,
    outline_node_count: Any,
    chapter_count: Any,
) -> dict[str, Any]:
    """
    用途：详情读取后严格重验规范 JSON、精确键集、UTF-8 字节、版本与计数。
    任一不一致固定 corrupt，不反射正文/类型细节。
    二次开发：存储正文必须恰好等于 UTF-8 紧凑 sort_keys 规范 JSON。
    """
    try:
        if not isinstance(snapshot_json, str):
            raise _corrupt() from None
        if not isinstance(state_version, str) or not state_version:
            raise _corrupt() from None

        try:
            bytes_val = _safe_nonneg_int(snapshot_bytes)
            outline_val = _safe_nonneg_int(outline_node_count)
            chapter_val = _safe_nonneg_int(chapter_count)
        except EditorStateCheckpointError:
            raise
        except Exception:
            raise _corrupt() from None

        if bytes_val < MIN_SNAPSHOT_BYTES or bytes_val > MAX_SNAPSHOT_BYTES:
            raise _corrupt() from None

        try:
            raw_bytes = snapshot_json.encode("utf-8")
        except Exception:
            raise _corrupt() from None
        if len(raw_bytes) != bytes_val:
            raise _corrupt() from None

        try:
            data = json.loads(snapshot_json)
        except json.JSONDecodeError:
            raise _corrupt() from None
        if not isinstance(data, dict):
            raise _corrupt() from None
        if set(data.keys()) != SNAPSHOT_KEY_SET:
            raise _corrupt() from None

        # 严格规范形式：必须与紧凑 sort_keys UTF-8 完全一致
        recomputed_json = _canonical_snapshot_json(data)
        if recomputed_json != snapshot_json:
            raise _corrupt() from None

        # 版本必须以规范正文字节验证
        expected_version = compute_state_version(recomputed_json)
        if expected_version != state_version:
            raise _corrupt() from None
        if count_outline_nodes(data.get("outline")) != outline_val:
            raise _corrupt() from None
        if count_chapter_dicts(data.get("chapters")) != chapter_val:
            raise _corrupt() from None
        return data
    except EditorStateCheckpointError:
        raise
    except Exception:
        raise _corrupt() from None


def get_editor_state_checkpoint(
    db: Session,
    workspace_id: str,
    project_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    """
    用途：按 ID 读取单条检查点并重验快照；跨项目/空间统一 not_found。
    对接：GET .../editor-state-checkpoints/{checkpointId}。
    二次开发：SQL 必须同时带 id/workspace_id/project_id，禁止先全局 get 再 Python 过滤。
    """
    _require_project(db, workspace_id, project_id, lock=False)
    row = db.execute(
        select(EditorStateCheckpointRow).where(
            EditorStateCheckpointRow.id == checkpoint_id,
            EditorStateCheckpointRow.workspace_id == workspace_id,
            EditorStateCheckpointRow.project_id == project_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise EditorStateCheckpointError(
            404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
        )
    snapshot = _validate_snapshot_payload(
        snapshot_json=row.snapshot_json,
        state_version=row.state_version,
        snapshot_bytes=row.snapshot_bytes,
        outline_node_count=row.outline_node_count,
        chapter_count=row.chapter_count,
    )
    meta = _meta_from_row(row)
    meta["snapshot"] = snapshot
    return meta
