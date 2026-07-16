"""
模块：P12D-A 修订与当前状态差异摘要服务
用途：只读比较所选修订与服务端当前权威 13 键，返回变更字段名与两侧有界摘要。
对接：api.editor_state_revisions comparison GET；
  editor_state_service / editor_state_revision_history_service。
二次开发：
  - 全程只读：禁止 add/delete/flush/commit/rollback/refresh/锁/审计/检查点/修订写/HTTP；
  - 当前侧 get_editor_state→extract_canonical_snapshot；目标侧复用 C1 get_editor_state_revision；
  - 逐字段 canonical_snapshot_json 字节比较；字段序只引用 CANONICAL_STATE_KEYS；
  - 摘要遍历预算 10,000、深度 32；超界与其他非历史错误固定 comparison_failed。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import editor_state_service
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
    get_editor_state_revision,
)

# 与前端 C3 摘要语义一致
MAX_SUMMARY_NODES = 10_000
MAX_SUMMARY_DEPTH = 32

CODE_COMPARISON_FAILED = "editor_state_revision_comparison_failed"
MSG_COMPARISON_FAILED = "修订差异摘要生成失败"


class EditorStateRevisionComparisonError(Exception):
    """
    用途：比较/摘要固定错误；路由映射 500 + no-store。
    对接：api.editor_state_revisions comparison。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _comparison_failed() -> EditorStateRevisionComparisonError:
    """用途：统一构造脱敏比较失败，禁止附带内部异常细节。"""
    return EditorStateRevisionComparisonError(
        500, CODE_COMPARISON_FAILED, MSG_COMPARISON_FAILED
    )


def _bounded_array_length(value: Any, budget: dict[str, int]) -> int:
    """用途：有界统计数组长度；非数组记 0；超预算失败。"""
    if not isinstance(value, list):
        return 0
    budget["n"] += 1
    if budget["n"] > MAX_SUMMARY_NODES:
        raise _comparison_failed()
    return len(value)


def _count_outline_nodes(
    nodes: Any, depth: int, budget: dict[str, int]
) -> int:
    """用途：有界递归统计大纲树节点数；深度/节点超限固定失败。"""
    if depth > MAX_SUMMARY_DEPTH:
        raise _comparison_failed()
    if not isinstance(nodes, list):
        return 0
    total = 0
    for node in nodes:
        budget["n"] += 1
        if budget["n"] > MAX_SUMMARY_NODES:
            raise _comparison_failed()
        total += 1
        if isinstance(node, dict):
            total += _count_outline_nodes(
                node.get("children"), depth + 1, budget
            )
    return total


def summarize_canonical_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    用途：从已校验 13 键 snapshot 压缩固定六项有界摘要。
    对接：comparison 响应 currentSummary/targetSummary。
    二次开发：非法/过深结构固定 comparison_failed；禁止截断伪造成功。
    """
    if not isinstance(snapshot, dict):
        raise _comparison_failed()
    budget: dict[str, int] = {"n": 0}
    outline_node_count = _count_outline_nodes(
        snapshot.get("outline"), 0, budget
    )
    chapter_count = _bounded_array_length(snapshot.get("chapters"), budget)
    fact_count = _bounded_array_length(snapshot.get("facts"), budget)
    response_matrix_row_count = _bounded_array_length(
        snapshot.get("responseMatrix"), budget
    )
    qualify = _bounded_array_length(snapshot.get("businessQualify"), budget)
    toc = _bounded_array_length(snapshot.get("businessToc"), budget)
    commit = _bounded_array_length(snapshot.get("businessCommit"), budget)
    quote_rows = 0
    bq = snapshot.get("businessQuote")
    if isinstance(bq, dict):
        quote_rows = _bounded_array_length(bq.get("rows"), budget)
    parsed = snapshot.get("parsedMarkdown")
    has_parsed = (
        isinstance(parsed, str) and parsed.strip() != ""
    )
    return {
        "outline_node_count": outline_node_count,
        "chapter_count": chapter_count,
        "fact_count": fact_count,
        "response_matrix_row_count": response_matrix_row_count,
        "business_entry_total": qualify + toc + quote_rows + commit,
        "has_parsed_markdown": has_parsed,
    }


def _diff_changed_fields(
    current_snap: dict[str, Any], target_snap: dict[str, Any]
) -> list[str]:
    """
    用途：按 CANONICAL_STATE_KEYS 顺序逐字段规范 JSON 比较。
    二次开发：禁止 Python == / 长度 / 摘要冒充；仅用 canonical_snapshot_json。
    """
    changed: list[str] = []
    for key in editor_state_service.CANONICAL_STATE_KEYS:
        cur_json = editor_state_service.canonical_snapshot_json(
            {key: current_snap.get(key)}
        )
        tgt_json = editor_state_service.canonical_snapshot_json(
            {key: target_snap.get(key)}
        )
        if cur_json != tgt_json:
            changed.append(key)
    return changed


def compare_revision_with_current(
    db: Session,
    workspace_id: str,
    project_id: str,
    revision_id: str,
) -> dict[str, Any]:
    """
    用途：比较目标修订与当前权威状态，返回脱敏差异摘要。
    对接：GET .../editor-state-revisions/{revisionId}/comparison。
    二次开发：
      - 历史服务 404/corrupt 原样上抛；
      - 当前读取/比较/摘要其他异常固定 comparison_failed；
      - 不写库、不加锁、不返回正文/ID/版本。
    """
    # 目标侧：三重作用域 + 规范 13 键重验（C1）
    try:
        target = get_editor_state_revision(
            db, workspace_id, project_id, revision_id
        )
    except EditorStateRevisionHistoryError:
        raise

    try:
        current_state = editor_state_service.get_editor_state(
            db, workspace_id, project_id
        )
        current_snap = editor_state_service.extract_canonical_snapshot(
            current_state
        )
        target_snap = target["snapshot"]
        if not isinstance(target_snap, dict):
            raise _comparison_failed()

        changed_fields = _diff_changed_fields(current_snap, target_snap)
        current_summary = summarize_canonical_snapshot(current_snap)
        target_summary = summarize_canonical_snapshot(target_snap)
        return {
            "same_state": len(changed_fields) == 0,
            "changed_fields": changed_fields,
            "current_summary": current_summary,
            "target_summary": target_summary,
        }
    except EditorStateRevisionHistoryError:
        raise
    except EditorStateRevisionComparisonError:
        raise
    except Exception:
        raise _comparison_failed() from None
