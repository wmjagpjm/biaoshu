"""
模块：P12A/P12B-D1/P12C-B-D3 editor-state 检查点服务
用途：手动创建/列表/详情；以及锁后 CAS 的原子安全恢复与修订账本接入。
对接：api.editor_state_checkpoints；editor_state_service（共享全状态版本算法与写回原语）；
  editor_state_revision_service.record_editor_state_transition；EditorStateCheckpointRow。
二次开发：
  - 禁止接受客户端 snapshot/版本/计数/名称；
  - 创建与裁剪同事务；恢复与安全检查点/写回/修订/裁剪同事务；失败必须 rollback；
  - 列表 SQL 只投影元数据列，绝不 select snapshot_json；
  - 13 键/规范 JSON/stateVersion/ORM 映射必须委托 editor_state_service，禁止第二套算法；
  - 禁止调用会自行 commit 的 create_editor_state_checkpoint 或 upsert_editor_state 做嵌套恢复；
  - 不同版本恢复固定 source_kind=checkpoint_restore；同版本禁止调用 recorder；
  - 不实现删除、自动历史或客户端 force。
"""

from __future__ import annotations

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
from app.services import editor_state_revision_service, editor_state_service
from app.services.project_service import ProjectNotFoundError

MAX_CHECKPOINTS_PER_PROJECT = 20
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MiB
MIN_SNAPSHOT_BYTES = 1

# 与 editor_state_service 共享精确 13 键（薄兼容常量，禁止本地另起一套）
SNAPSHOT_KEYS: tuple[str, ...] = editor_state_service.CANONICAL_STATE_KEYS
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
    二次开发：薄包装，委托 editor_state_service 权威实现。
    """
    return editor_state_service.canonical_snapshot_json(snapshot)


def compute_state_version(snapshot_json: str) -> str:
    """
    用途：对规范快照 JSON 字节做 SHA-256，取前 32 hex 并加 esv_ 前缀。
    二次开发：薄包装，委托 editor_state_service 权威实现。
    """
    return editor_state_service.compute_state_version_from_canonical_json(snapshot_json)


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
    二次开发：薄包装，委托 editor_state_service 权威实现。
    """
    return editor_state_service.extract_canonical_snapshot(state)


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
    """用途：ORM 行 → API 元数据字典（七键含 display_name）。"""
    return {
        "checkpoint_id": row.id,
        "state_version": row.state_version,
        "snapshot_bytes": int(row.snapshot_bytes),
        "outline_node_count": int(row.outline_node_count),
        "chapter_count": int(row.chapter_count),
        "created_at": row.created_at,
        "display_name": row.display_name,
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


def _trim_checkpoints(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    protect_id: str | None = None,
) -> None:
    """
    用途：同事务内仅保留本项目最近 20 条（created_at DESC, id DESC）。
    二次开发：
      - 只 SELECT id，禁止加载 snapshot_json 正文；
      - DELETE 必须带 workspace_id/project_id/id 范围约束；
      - 禁止跨项目/跨空间删除；
      - protect_id（恢复前安全检查点）绝不可因并列时间戳/随机 ID 被本轮裁掉。
    """
    keep_ids = list(
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
    if not keep_ids:
        return
    if protect_id and protect_id in keep_ids:
        others = [cid for cid in keep_ids if cid != protect_id]
        retain = {protect_id, *others[: MAX_CHECKPOINTS_PER_PROJECT - 1]}
    else:
        retain = set(keep_ids[:MAX_CHECKPOINTS_PER_PROJECT])
    drop_ids = [cid for cid in keep_ids if cid not in retain]
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
        # 提交前构造元数据，避免 commit 成功后 refresh 失败导致假失败/重复创建；
        # 名称固定初始 null（不接受客户端投稿，列默认 NULL）
        meta = {
            "checkpoint_id": checkpoint_id,
            "state_version": state_version,
            "snapshot_bytes": snapshot_bytes,
            "outline_node_count": outline_node_count,
            "chapter_count": chapter_count,
            "created_at": row.created_at,
            "display_name": None,
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
            EditorStateCheckpointRow.display_name,
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
                "display_name": row.display_name,
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


# P12C-C2：共享恢复原语仅允许这两类准确内部来源
_RESTORE_SOURCE_KINDS: frozenset[str] = frozenset(
    {"checkpoint_restore", "revision_restore"}
)


def stage_locked_canonical_restore(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    row: ProjectEditorStateRow | None,
    current_state: dict[str, Any],
    target_snapshot: dict[str, Any],
    target_version: str,
    source_kind: str,
) -> dict[str, Any]:
    """
    用途：已锁定、已验证规范目标后的无提交共享恢复原语。
    对接：checkpoint restore 与 revision restore 共用；P12C-C2。
    规则：
      1. 仅允许 source_kind ∈ {checkpoint_restore, revision_restore}；
      2. 从 current_state 构造安全检查点 → 13 键写回 → 结果版本复核；
      3. 仅当 result_version != current 时记 transition；同版本禁止 recorder；
      4. 保护新安全检查点裁剪到 20；不 commit/rollback/refresh/加锁/查目标。
    """
    if not isinstance(source_kind, str) or source_kind not in _RESTORE_SOURCE_KINDS:
        raise EditorStateCheckpointError(
            500, CODE_CHECKPOINT_CORRUPT, MSG_CHECKPOINT_CORRUPT
        )
    if not isinstance(workspace_id, str) or not workspace_id:
        raise _corrupt()
    if not isinstance(project_id, str) or not project_id:
        raise _corrupt()
    if not isinstance(current_state, dict) or not isinstance(target_snapshot, dict):
        raise _corrupt()
    if not isinstance(target_version, str) or not target_version:
        raise _corrupt()

    # 1) 从锁后当前权威状态构造恢复前安全快照（1–2 MiB）
    safety_snapshot = extract_canonical_snapshot(current_state)
    safety_json = _canonical_snapshot_json(safety_snapshot)
    safety_bytes = len(safety_json.encode("utf-8"))
    if safety_bytes < MIN_SNAPSHOT_BYTES or safety_bytes > MAX_SNAPSHOT_BYTES:
        raise EditorStateCheckpointError(
            413, CODE_CHECKPOINT_TOO_LARGE, MSG_CHECKPOINT_TOO_LARGE
        )
    safety_version = compute_state_version(safety_json)
    if safety_version != current_state["stateVersion"]:
        raise _corrupt()
    safety_outline = count_outline_nodes(safety_snapshot.get("outline"))
    safety_chapters = count_chapter_dicts(safety_snapshot.get("chapters"))
    safety_id = _new_checkpoint_id()

    safety_row = _insert_checkpoint_row(
        db,
        checkpoint_id=safety_id,
        workspace_id=workspace_id,
        project_id=project_id,
        snapshot_json=safety_json,
        state_version=safety_version,
        snapshot_bytes=safety_bytes,
        outline_node_count=safety_outline,
        chapter_count=safety_chapters,
    )
    safety_id = safety_row.id

    # 2) 写回精确 13 键
    row = editor_state_service.apply_canonical_snapshot_to_locked_row(
        db, project_id, row, target_snapshot
    )

    # 3) 写回后重算版本，必须精确等于目标版本
    result_state = editor_state_service._state_from_row(project_id, row)
    result_version = result_state["stateVersion"]
    if result_version != target_version:
        raise _corrupt()

    # 4) 仅不同规范版本时记准确来源；同版本禁止伪造修订
    if result_version != current_state["stateVersion"]:
        editor_state_revision_service.record_editor_state_transition(
            db,
            workspace_id,
            project_id,
            before_state=current_state,
            after_state=result_state,
            source_kind=source_kind,
        )

    # 5) 保护新安全记录地裁剪到最多 20
    _trim_checkpoints(db, workspace_id, project_id, protect_id=safety_id)

    restored_at = result_state.get("updatedAt")
    if not isinstance(restored_at, str) or not restored_at:
        raise _corrupt()
    return {
        "safety_checkpoint_id": safety_id,
        "state_version": target_version,
        "restored_at": restored_at,
        "result_state": result_state,
        "result_version": result_version,
    }


def restore_editor_state_checkpoint(
    db: Session,
    workspace_id: str,
    project_id: str,
    checkpoint_id: str,
    expected_state_version: str,
) -> dict[str, Any]:
    """
    用途：锁后 CAS + 恢复前安全检查点 + 13 键写回 + 条件修订 + 保护裁剪，一次原子 commit。
    对接：POST .../editor-state-checkpoints/{checkpointId}/restore；P12C-B-D3 / P12C-C2。
    二次开发：
      - 禁止嵌套调用 create_editor_state_checkpoint / upsert_editor_state；
      - 写回/安全检查点/条件修订走 stage_locked_canonical_restore；
      - 目标读取必须 id+workspace_id+project_id 三重 SQL；
      - 409 必须在任何安全插入之前；失败 editor-state/安全检查点/revision 三域零写；
      - 仅当 result_version != current_state["stateVersion"] 时固定 checkpoint_restore；
      - commit 前构造响应；commit 后禁止 refresh / get_editor_state。
    """
    try:
        # 1) 项目写锁 + 全状态 CAS（陈旧 expected 在此抛 EditorStateVersionConflict）
        row, current_state = (
            editor_state_service.lock_and_assert_expected_state_version(
                db, workspace_id, project_id, expected_state_version
            )
        )

        # 2) 目标检查点：id/workspace/project 三重 SQL，禁止先全局 get
        target_row = db.execute(
            select(EditorStateCheckpointRow).where(
                EditorStateCheckpointRow.id == checkpoint_id,
                EditorStateCheckpointRow.workspace_id == workspace_id,
                EditorStateCheckpointRow.project_id == project_id,
            )
        ).scalar_one_or_none()
        if target_row is None:
            raise EditorStateCheckpointError(
                404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
            )

        # 3) P12A 严格重验目标快照
        target_snapshot = _validate_snapshot_payload(
            snapshot_json=target_row.snapshot_json,
            state_version=target_row.state_version,
            snapshot_bytes=target_row.snapshot_bytes,
            outline_node_count=target_row.outline_node_count,
            chapter_count=target_row.chapter_count,
        )
        target_version = target_row.state_version

        # 4–8) 共享无提交原语：安全检查点 + 写回 + 条件修订 + 保护裁剪
        staged = stage_locked_canonical_restore(
            db,
            workspace_id,
            project_id,
            row=row,
            current_state=current_state,
            target_snapshot=target_snapshot,
            target_version=target_version,
            source_kind="checkpoint_restore",
        )

        response = {
            "restored_checkpoint_id": checkpoint_id,
            "safety_checkpoint_id": staged["safety_checkpoint_id"],
            "state_version": staged["state_version"],
            "restored_at": staged["restored_at"],
        }
        db.commit()
        return response
    except editor_state_service.EditorStateVersionConflict:
        db.rollback()
        raise
    except EditorStateCheckpointError:
        db.rollback()
        raise
    except ProjectNotFoundError:
        db.rollback()
        raise EditorStateCheckpointError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        ) from None
    except Exception:
        db.rollback()
        raise
