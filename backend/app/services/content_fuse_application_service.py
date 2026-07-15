"""
模块：M3-D 融合写入持久恢复批次服务
用途：服务端原子确认 content_fuse 建议、写有限恢复快照、一次性漂移安全恢复。
对接：api.content_fuse_applications；ProjectTaskRow.result_json；ProjectEditorStateRow。
二次开发：
  - 严禁信任客户端 title/base/action/proposedMarkdown/before/after；
  - 严禁调用会自行 commit 的 upsert_editor_state（破坏原子性）；
  - 章节写入、批次插入、20 批裁剪必须同一事务；
  - 恢复只覆盖仍精确等于 after 的章；一次尝试后消费。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import (
    ContentFuseApplicationBatchRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    utc_now,
)
from app.services import editor_state_service

ALLOWED_ACTIONS = frozenset({"merge", "expand", "rewrite", "merge_suggest"})
# 与前端 ChapterContent.status 对齐；缺失按 M3-C 语义落 pending，禁止 draft
ALLOWED_CHAPTER_STATUSES = frozenset(
    {"pending", "generating", "done", "needs_review"}
)
MAX_SUGGESTIONS = 5
MAX_BATCHES_PER_PROJECT = 20
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MiB
PREVIEW_UTF16_LIMIT = 96
MAX_SUGGESTION_ID_LEN = 64

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_TASK_NOT_FOUND = "content_fuse_task_not_found"
MSG_TASK_NOT_FOUND = "融合任务不存在或不可用"
CODE_APPLY_CONFLICT = "content_fuse_apply_conflict"
MSG_APPLY_CONFLICT = "融合确认冲突，未写入任何变更"
CODE_APP_NOT_FOUND = "content_fuse_application_not_found"
MSG_APP_NOT_FOUND = "恢复批次不存在或不可访问"
CODE_APP_CONSUMED = "content_fuse_application_consumed"
MSG_APP_CONSUMED = "该恢复批次已消费，不可再次恢复"

_MD_HEADING_RE = re.compile(r"^#+\s*", re.MULTILINE)
_MD_MARK_RE = re.compile(r"[|>*`_-]")
_WS_RE = re.compile(r"\s+")
_ALL_WS_RE = re.compile(r"\s")


class ContentFuseApplicationError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.content_fuse_applications。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _new_batch_id() -> str:
    """用途：生成不透明批次 ID（cfab_ 前缀）。"""
    return f"cfab_{secrets.token_hex(12)}"


def _utf16_len(text: str) -> int:
    """用途：按 UTF-16 code unit 计长度（对齐 JS string.length）。"""
    return len(text.encode("utf-16-le")) // 2


def _utf16_slice(text: str, limit: int) -> str:
    """用途：按 UTF-16 code unit 截断前 limit 个单位。"""
    if limit <= 0:
        return ""
    out: list[str] = []
    used = 0
    for ch in text:
        units = 2 if ord(ch) > 0xFFFF else 1
        if used + units > limit:
            break
        out.append(ch)
        used += units
    return "".join(out)


def derive_preview(body: str) -> str:
    """
    用途：与前端 derivePreview 对齐的预览派生。
    规则：去行首标题符 → 标记符换空格 → 折叠空白 → trim → UTF-16 截 96。
    """
    plain = _MD_HEADING_RE.sub("", body or "")
    plain = _MD_MARK_RE.sub(" ", plain)
    plain = _WS_RE.sub(" ", plain).strip()
    sliced = _utf16_slice(plain, PREVIEW_UTF16_LIMIT)
    return sliced or "（空正文）"


def count_body_words(body: str) -> int:
    """用途：移除空白后按 UTF-16 code unit 计数（对齐前端 countBodyWords）。"""
    compact = _ALL_WS_RE.sub("", body or "")
    return _utf16_len(compact)


def compute_chapter_base(title: str, body: str) -> dict[str, Any]:
    """
    用途：锁内重算章节 base（title trim / UTF-8 SHA-1 前 20 hex / Unicode 码点长度）。
    对接：fuse_context_service.compute_chapter_base；M3-B 前端 computeChapterBase。
    """
    safe_title = (title or "").strip()
    safe_body = body or ""
    digest = sha1(safe_body.encode("utf-8")).hexdigest()[:20]
    return {
        "bodyHash": f"bh_{digest}",
        "bodyLength": len(safe_body),
        "title": safe_title,
    }


def build_applied_body(action: str, current_body: str, proposed: str) -> str | None:
    """
    用途：按 action 构造 after 正文；空 proposed 返回 None。
    规则：expand 追加（非空旧正文用双换行）；其余规范 action 整章替换。
    """
    if not proposed:
        return None
    if action == "expand":
        return f"{current_body}\n\n{proposed}" if current_body else proposed
    return proposed


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _strict_str(value: Any) -> str | None:
    """用途：仅接受原生 str；拒绝 dict/list/bool/int 等静默强转。"""
    if type(value) is not str:
        return None
    return value


def _strict_nonempty_str(value: Any) -> str | None:
    """用途：原生非空 str（允许首尾空白，但 strip 后不可空）。"""
    text = _strict_str(value)
    if text is None or not text.strip():
        return None
    return text


def _strict_nonneg_int(value: Any) -> int | None:
    """用途：原生非负 int；显式拒绝 bool（bool 是 int 子类）。"""
    if type(value) is not int or value < 0:
        return None
    return value


def _conflict() -> ContentFuseApplicationError:
    return ContentFuseApplicationError(409, CODE_APPLY_CONFLICT, MSG_APPLY_CONFLICT)


def _normalize_before_status(raw: Any) -> str:
    """
    用途：快照 beforeStatus。
    规则：缺失/空串 → pending（M3-C）；非空非法（含 draft）→ 整批冲突。
    """
    if raw is None:
        return "pending"
    if type(raw) is not str:
        raise _conflict()
    if raw == "":
        return "pending"
    if raw not in ALLOWED_CHAPTER_STATUSES:
        raise _conflict()
    return raw


def _strict_chapter_map_for_apply(chapters: list[Any]) -> dict[str, dict[str, Any]]:
    """
    用途：apply 前严格校验 chapters 结构。
    规则：必须全为 dict；id 原生非空 str；无重复 ID；禁止静默过滤/重排。
    """
    mapping: dict[str, dict[str, Any]] = {}
    for item in chapters:
        if not isinstance(item, dict):
            raise _conflict()
        raw_id = item.get("id")
        if type(raw_id) is not str:
            raise _conflict()
        cid = raw_id.strip()
        if not cid:
            raise _conflict()
        if cid in mapping:
            raise _conflict()
        mapping[cid] = item
    return mapping


def _lookup_chapter_map(chapters: list[Any]) -> dict[str, dict[str, Any]]:
    """
    用途：consume 只读查找表；不修改/不丢弃原始 chapters 列表。
    规则：仅收录在原始列表中恰好出现一次的合法 dict+非空字符串 ID。
    同一规范化（strip 后）ID 出现两次及以上时整项排除，禁止 first-wins/last-wins；
    章节身份不唯一即视为漂移，不得覆盖任一重复章。
    """
    mapping: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for item in chapters:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if type(raw_id) is not str:
            continue
        cid = raw_id.strip()
        if not cid:
            continue
        if cid in mapping or cid in duplicates:
            # 重复 ID：从查找表排除，身份不唯一不得恢复
            mapping.pop(cid, None)
            duplicates.add(cid)
            continue
        mapping[cid] = item
    return mapping


def _lock_project(db: Session, workspace_id: str, project_id: str) -> Project:
    """
    用途：项目级写锁，使确认/恢复的读-校-写在事务内串行。
    对接：apply_content_fuse_application；consume_content_fuse_application。
    二次开发：SQLite 无副作用 UPDATE 串行；其他方言 SELECT FOR UPDATE。
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
            raise ContentFuseApplicationError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )
        db.expire_all()
        project = db.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise ContentFuseApplicationError(
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
        raise ContentFuseApplicationError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )
    return project


def _require_technical_project(
    db: Session, workspace_id: str, project_id: str, *, lock: bool
) -> Project:
    """用途：校验当前空间技术标；可选加锁。"""
    if lock:
        project = _lock_project(db, workspace_id, project_id)
    else:
        project = db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if project is None:
            raise ContentFuseApplicationError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )
    if str(project.kind or "") != "technical":
        raise ContentFuseApplicationError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )
    return project


def _load_task_suggestions(
    db: Session, project_id: str, task_id: str
) -> list[Any]:
    """
    用途：读取同项目成功 content_fuse 任务并返回 suggestions 原始列表。
    规则：不存在/跨项目/错误类型或状态/非 list 统一 task_not_found，不反射 ID。
    二次开发：列表项合法性由 _index_task_suggestions 严格校验，禁止此处过滤。
    """
    task = db.get(ProjectTaskRow, task_id)
    if (
        task is None
        or task.project_id != project_id
        or type(task.type) is not str
        or task.type != "content_fuse"
        or type(task.status) is not str
        or task.status != "success"
    ):
        raise ContentFuseApplicationError(404, CODE_TASK_NOT_FOUND, MSG_TASK_NOT_FOUND)
    result = _loads(task.result_json)
    if not isinstance(result, dict):
        raise ContentFuseApplicationError(404, CODE_TASK_NOT_FOUND, MSG_TASK_NOT_FOUND)
    suggestions = result.get("suggestions")
    if not isinstance(suggestions, list):
        raise ContentFuseApplicationError(404, CODE_TASK_NOT_FOUND, MSG_TASK_NOT_FOUND)
    return suggestions


def _index_task_suggestions(
    raw_suggestions: list[Any],
) -> dict[str, dict[str, Any]]:
    """
    用途：按原生字符串 suggestionId 建严格索引。
    规则：每项必须是 dict；suggestionId 原生非空 str 且 ≤64；全局唯一；
      任一非法/重复整批 409，禁止静默跳过或 first-wins。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for item in raw_suggestions:
        if not isinstance(item, dict):
            raise _conflict()
        sid = _strict_nonempty_str(item.get("suggestionId"))
        if sid is None:
            raise _conflict()
        sid = sid.strip()
        if len(sid) > MAX_SUGGESTION_ID_LEN:
            raise _conflict()
        if sid in by_id:
            raise _conflict()
        by_id[sid] = item
    return by_id


def _parse_suggestion_fields(sug: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """
    用途：严格解析任务建议字段；任一项非原生类型即冲突。
    返回：(target_chapter_id, action, proposed_markdown, base_dict)
    """
    target_id = _strict_nonempty_str(sug.get("targetChapterId"))
    if target_id is None:
        raise _conflict()
    target_id = target_id.strip()

    action = _strict_nonempty_str(sug.get("action"))
    if action is None:
        raise _conflict()
    action = action.strip()
    if action not in ALLOWED_ACTIONS:
        raise _conflict()

    proposed = _strict_str(sug.get("proposedMarkdown"))
    if proposed is None or proposed == "":
        raise _conflict()

    base_raw = sug.get("base")
    if not isinstance(base_raw, dict):
        raise _conflict()

    expect_hash = _strict_nonempty_str(base_raw.get("bodyHash"))
    if expect_hash is None:
        raise _conflict()
    expect_hash = expect_hash.strip()

    expect_title = _strict_str(base_raw.get("title"))
    if expect_title is None:
        raise _conflict()
    # base 比较侧与 compute_chapter_base 一致：title 使用 trim 后值
    expect_title_trimmed = expect_title.strip()

    expect_len = _strict_nonneg_int(base_raw.get("bodyLength"))
    if expect_len is None:
        raise _conflict()

    return target_id, action, proposed, {
        "bodyHash": expect_hash,
        "title": expect_title_trimmed,
        "bodyLength": expect_len,
    }


def _read_chapters_locked(
    db: Session, project_id: str
) -> tuple[ProjectEditorStateRow | None, list[Any]]:
    """
    用途：锁后重读 editor-state chapters 原始列表。
    规则：不得过滤、删除或重排原始项；结构合法性由调用方处理。
    """
    row = db.get(ProjectEditorStateRow, project_id)
    raw = _loads(row.chapters_json) if row is not None else None
    if not isinstance(raw, list):
        return row, []
    return row, raw


def _apply_chapter_fields(chapter: dict[str, Any], body: str, status: str) -> None:
    """用途：就地更新章节 body/status 并重算 preview/wordCount。"""
    chapter["body"] = body
    chapter["status"] = status
    chapter["preview"] = derive_preview(body)
    chapter["wordCount"] = count_body_words(body)


def _snapshot_chapter_count(snapshot_json: str) -> int:
    data = _loads(snapshot_json)
    if not isinstance(data, dict):
        return 0
    items = data.get("chapters")
    if not isinstance(items, list):
        return 0
    return len(items)


def _trim_batches(db: Session, workspace_id: str, project_id: str) -> None:
    """用途：同事务内仅保留本项目最近 20 批（created_at DESC, id DESC）。"""
    rows = (
        db.execute(
            select(ContentFuseApplicationBatchRow)
            .where(
                ContentFuseApplicationBatchRow.workspace_id == workspace_id,
                ContentFuseApplicationBatchRow.project_id == project_id,
            )
            .order_by(
                ContentFuseApplicationBatchRow.created_at.desc(),
                ContentFuseApplicationBatchRow.id.desc(),
            )
        )
        .scalars()
        .all()
    )
    for old in rows[MAX_BATCHES_PER_PROJECT:]:
        db.delete(old)


def apply_content_fuse_application(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    task_id: str,
    suggestion_ids: list[str],
    expected_state_version: str,
) -> dict[str, Any]:
    """
    用途：原子确认所选融合建议；整批成功或零写。
    对接：POST /api/projects/{projectId}/content-fuse-applications。
    二次开发：
      - 锁后先全状态 CAS，再章节 base 校验；不得部分写入；
      - 不得调用会自行 commit 的 upsert_editor_state；
      - 全状态冲突抛 EditorStateVersionConflict，由路由映射固定 409。
    """
    if not suggestion_ids or len(suggestion_ids) > MAX_SUGGESTIONS:
        raise _conflict()

    _require_technical_project(db, workspace_id, project_id, lock=False)
    # 共用原语：项目级锁 + 锁后规范视图比较 expected（全状态优先）
    state_row, _current_state = (
        editor_state_service.lock_and_assert_expected_state_version(
            db, workspace_id, project_id, expected_state_version
        )
    )

    raw_suggestions = _load_task_suggestions(db, project_id, task_id)
    by_id = _index_task_suggestions(raw_suggestions)

    raw_chapters = _loads(state_row.chapters_json) if state_row is not None else None
    chapters: list[Any] = raw_chapters if isinstance(raw_chapters, list) else []
    if state_row is None or not chapters:
        raise _conflict()
    # 严格校验：禁止过滤非 dict / 空白 ID / 重复 ID 后再写回
    chap_map = _strict_chapter_map_for_apply(chapters)

    # (suggestionId, chapter, after_body, before_status, title_exact, chapter_id)
    selected: list[tuple[str, dict[str, Any], str, str, str, str]] = []
    seen_targets: set[str] = set()

    for sid in suggestion_ids:
        sug = by_id.get(sid)
        if sug is None:
            raise _conflict()
        target_id, action, proposed, expect_base = _parse_suggestion_fields(sug)
        if target_id in seen_targets:
            raise _conflict()
        seen_targets.add(target_id)

        chapter = chap_map.get(target_id)
        if chapter is None:
            raise _conflict()

        # 所选章 title/body 必须原生 str；禁止当空串后继续写入
        live_title_exact = chapter.get("title")
        live_body = chapter.get("body")
        if type(live_title_exact) is not str or type(live_body) is not str:
            raise _conflict()
        live_base = compute_chapter_base(live_title_exact, live_body)
        if (
            live_base["title"] != expect_base["title"]
            or live_base["bodyHash"] != expect_base["bodyHash"]
            or live_base["bodyLength"] != expect_base["bodyLength"]
        ):
            raise _conflict()

        before_status = _normalize_before_status(chapter.get("status"))

        after_body = build_applied_body(action, live_body, proposed)
        if after_body is None or after_body == live_body:
            raise _conflict()
        selected.append(
            (sid, chapter, after_body, before_status, live_title_exact, target_id)
        )

    snapshot_chapters: list[dict[str, str]] = []
    for sid, chapter, after_body, before_status, title_exact, chapter_id in selected:
        # title/body 已在选型阶段校验为原生 str
        before_body = chapter.get("body")
        if type(before_body) is not str:
            raise _conflict()
        snapshot_chapters.append(
            {
                "suggestionId": sid,
                "chapterId": chapter_id,
                # 快照保存未 trim 的精确 title；恢复时精确比较
                "title": title_exact,
                "beforeBody": before_body,
                "beforeStatus": before_status,
                "afterBody": after_body,
                "afterStatus": "needs_review",
            }
        )
        _apply_chapter_fields(chapter, after_body, "needs_review")

    snapshot_payload = {"chapters": snapshot_chapters}
    snapshot_json = _dumps(snapshot_payload)
    if len(snapshot_json.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        # 未 commit；回滚当前 session 变更
        db.rollback()
        raise _conflict()

    now = utc_now()
    state_row.chapters_json = _dumps(chapters)
    state_row.updated_at = now

    batch = ContentFuseApplicationBatchRow(
        id=_new_batch_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        snapshot_json=snapshot_json,
        state="active",
        created_at=now,
        consumed_at=None,
    )
    db.add(batch)
    db.flush()
    _trim_batches(db, workspace_id, project_id)
    # commit 前由规范视图独立计算新版本，禁止客户端自报
    new_state = editor_state_service.get_editor_state(db, workspace_id, project_id)
    new_version = new_state["stateVersion"]
    db.commit()
    db.refresh(batch)
    return {
        "batch_id": batch.id,
        "applied_chapter_count": len(snapshot_chapters),
        "created_at": batch.created_at,
        "state_version": new_version,
    }


def list_content_fuse_applications(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：固定最近 20 条最小投影列表。
    对接：GET /api/projects/{projectId}/content-fuse-applications。
    """
    _require_technical_project(db, workspace_id, project_id, lock=False)
    rows = (
        db.execute(
            select(ContentFuseApplicationBatchRow)
            .where(
                ContentFuseApplicationBatchRow.workspace_id == workspace_id,
                ContentFuseApplicationBatchRow.project_id == project_id,
            )
            .order_by(
                ContentFuseApplicationBatchRow.created_at.desc(),
                ContentFuseApplicationBatchRow.id.desc(),
            )
            .limit(MAX_BATCHES_PER_PROJECT)
        )
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        state = str(row.state or "")
        if state not in ("active", "consumed"):
            continue
        items.append(
            {
                "batch_id": row.id,
                "chapter_count": _snapshot_chapter_count(row.snapshot_json),
                "state": state,
                "created_at": row.created_at,
                "consumed_at": row.consumed_at,
            }
        )
    return {"items": items}


def consume_content_fuse_application(
    db: Session,
    workspace_id: str,
    project_id: str,
    batch_id: str,
    *,
    expected_state_version: str,
) -> dict[str, Any]:
    """
    用途：对 active 批次执行一次漂移安全恢复；0/部分/全部均消费。
    对接：POST .../content-fuse-applications/{batchId}/consume。
    二次开发：
      - 锁后先全状态 CAS；冲突时批次不消费、章节零写；
      - 全状态匹配后仍执行原 after 漂移规则；
      - 零恢复时版本等于操作前（批次消费不进 13 键）。
    """
    _require_technical_project(db, workspace_id, project_id, lock=False)
    # 全状态优先：不匹配则抛 EditorStateVersionConflict，不消费批次
    state_row, current_state = (
        editor_state_service.lock_and_assert_expected_state_version(
            db, workspace_id, project_id, expected_state_version
        )
    )
    pre_version = current_state["stateVersion"]

    batch = db.get(ContentFuseApplicationBatchRow, batch_id)
    if (
        batch is None
        or batch.workspace_id != workspace_id
        or batch.project_id != project_id
    ):
        raise ContentFuseApplicationError(404, CODE_APP_NOT_FOUND, MSG_APP_NOT_FOUND)
    if str(batch.state or "") != "active":
        raise ContentFuseApplicationError(409, CODE_APP_CONSUMED, MSG_APP_CONSUMED)

    snapshot = _loads(batch.snapshot_json)
    snap_chapters = (
        snapshot.get("chapters") if isinstance(snapshot, dict) else None
    )
    if not isinstance(snap_chapters, list):
        snap_chapters = []

    raw_chapters = _loads(state_row.chapters_json) if state_row is not None else None
    chapters: list[Any] = raw_chapters if isinstance(raw_chapters, list) else []
    # 只建查找表，不清洗原始 chapters；写回时保持原序与非 dict 项
    chap_map = _lookup_chapter_map(chapters)
    restored = 0
    skipped = 0

    for item in snap_chapters:
        if not isinstance(item, dict):
            skipped += 1
            continue
        chapter_id_raw = item.get("chapterId")
        chapter_id = (
            chapter_id_raw.strip()
            if type(chapter_id_raw) is str and chapter_id_raw.strip()
            else ""
        )
        # 快照 title 与 live title 均未 trim，精确字节级比较
        expect_title = item.get("title") if type(item.get("title")) is str else ""
        after_body = item.get("afterBody") if type(item.get("afterBody")) is str else ""
        after_status = (
            item.get("afterStatus") if type(item.get("afterStatus")) is str else ""
        )
        before_body = (
            item.get("beforeBody") if type(item.get("beforeBody")) is str else ""
        )
        before_status_raw = item.get("beforeStatus")
        if type(before_status_raw) is str and before_status_raw in ALLOWED_CHAPTER_STATUSES:
            before_status = before_status_raw
        elif before_status_raw is None or before_status_raw == "":
            before_status = "pending"
        else:
            # 历史非法状态不可写回；计为 skipped，不覆盖当前章
            skipped += 1
            continue
        chapter = chap_map.get(chapter_id)
        if chapter is None:
            skipped += 1
            continue
        # live title/body/status 非原生 str 视为漂移，禁止与空串误判相等后覆盖
        live_title = chapter.get("title")
        live_body = chapter.get("body")
        live_status = chapter.get("status")
        if (
            type(live_title) is not str
            or type(live_body) is not str
            or type(live_status) is not str
        ):
            skipped += 1
            continue
        if (
            live_title != expect_title
            or live_body != after_body
            or live_status != after_status
        ):
            skipped += 1
            continue
        _apply_chapter_fields(chapter, before_body, before_status)
        restored += 1

    now = utc_now()
    if state_row is not None and restored > 0:
        state_row.chapters_json = _dumps(chapters)
        state_row.updated_at = now
    batch.state = "consumed"
    batch.consumed_at = now
    # 零恢复：批次消费不进 13 键，版本等于操作前；有恢复则 commit 前重算
    if restored > 0:
        new_state = editor_state_service.get_editor_state(
            db, workspace_id, project_id
        )
        new_version = new_state["stateVersion"]
    else:
        new_version = pre_version
    db.commit()
    db.refresh(batch)
    return {
        "restored_chapter_count": restored,
        "skipped_chapter_count": skipped,
        "consumed_at": batch.consumed_at,
        "state_version": new_version,
    }
