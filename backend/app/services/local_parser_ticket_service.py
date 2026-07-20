"""
模块：P8C/P8E 本地解析一次性回传票据服务
用途：签发短期单项目单次票据，并在公共回调中原子消费后同事务写入解析结果；来源精确 mineru|docling。
对接：parse_callback 签发/回调路由；LocalParserCallbackTicketRow；auth_service.record_audit；
      editor_state_service 锁后全状态版本校验（P12B-C2）。
二次开发：禁止保存/日志输出原始票据；禁止调用会中途 commit 的 service；不启动 MinerU/Docling 解析器；
      版本冲突必须提交票据消费且零写正文；非版本异常完整 rollback 保持票据可重试。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import (
    LocalParserCallbackTicketRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    utc_now,
)
from app.services import auth_service, editor_state_revision_service, editor_state_service
from app.services.project_service import ProjectNotFoundError

# 固定契约常量
TICKET_TTL = timedelta(minutes=10)
CALLBACK_PATH = "/api/local-parser/callback"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_MARKDOWN_CODEPOINTS = 1_000_000
MAX_FILENAME_CODEPOINTS = 255
CODE_TICKET_INVALID = "local_parser_ticket_invalid"
MSG_TICKET_INVALID = "回传票据无效或已失效"
CODE_BAD_REQUEST = "local_parser_callback_bad_request"
MSG_BAD_REQUEST = "回传请求体无效"
CODE_PAYLOAD_TOO_LARGE = "local_parser_callback_payload_too_large"
MSG_PAYLOAD_TOO_LARGE = "回传请求体过大"
# P12B-C2：公共回调陈旧/空版本固定码与文案（绝不返回 currentStateVersion）
CODE_STATE_VERSION_CONFLICT = "local_parser_state_version_conflict"
MSG_STATE_VERSION_CONFLICT = "编辑内容已变化，请重新签发回传票据后重试"
# 固定来源枚举：精确小写成员校验，禁止 strip/lower/客户端扩展
ALLOWED_CALLBACK_SOURCES = frozenset({"mineru", "docling"})


class TicketServiceError(Exception):
    """用途：映射为固定 HTTP 错误，禁止携带敏感输入。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def digest_ticket(raw_ticket: str) -> str:
    """
    模块：票据摘要
    用途：对原始票据做 SHA-256 十六进制摘要，仅摘要可入库。
    对接：签发与原子消费。
    二次开发：禁止把原文写入数据库、审计或日志。
    """
    return hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()


async def accumulate_body_with_limit(
    chunks: AsyncIterator[bytes],
    *,
    max_bytes: int = MAX_BODY_BYTES,
) -> bytes:
    """
    模块：流式正文累计（带硬上限）
    用途：分块读取请求正文，累计长度一旦超过 max_bytes 立即固定 413，不保留超限正文。
    对接：POST /api/local-parser/callback 的 request.stream()。
    二次开发：禁止先 await request.body() 再检查长度；超限不得回显或缓存完整 body。
    """
    parts: list[bytes] = []
    total = 0
    async for chunk in chunks:
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            parts.clear()
            raise TicketServiceError(413, CODE_PAYLOAD_TOO_LARGE, MSG_PAYLOAD_TOO_LARGE)
        parts.append(chunk)
    return b"".join(parts)


def mint_raw_ticket() -> str:
    """
    模块：随机票据生成
    用途：secrets.token_urlsafe(32) 生成约 256 bit 不透明票据。
    对接：issue_callback_ticket。
    二次开发：强度不得降低；不得改用可预测序列。
    """
    return secrets.token_urlsafe(32)


def issue_callback_ticket(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    issued_by_user_id: str,
) -> dict[str, str]:
    """
    模块：签发一次性回传票据
    用途：为当前空间项目签发固定 10 分钟、单次消费票据；库内只存摘要与签发时权威版本。
    对接：POST /api/projects/{projectId}/parse-callback-ticket。
    二次开发：禁止客户端指定 TTL/workspace/user/版本；审计不得记录票据/摘要/项目/正文。
    """
    try:
        # 服务端捕获当前权威全状态版本；同时校验项目存在
        state = editor_state_service.get_editor_state(db, workspace_id, project_id)
    except ProjectNotFoundError as exc:
        raise ProjectNotFoundError(project_id) from exc

    expected_sv = state.get("stateVersion")
    if not editor_state_service.is_valid_state_version(expected_sv):
        # 规范算法产出应始终合法；防御性拒绝而非落坏票据
        raise ProjectNotFoundError(project_id)

    raw = mint_raw_ticket()
    now = utc_now()
    expires_at = now + TICKET_TTL
    row = LocalParserCallbackTicketRow(
        id=f"lpt_{secrets.token_hex(8)}",
        ticket_digest=digest_ticket(raw),
        workspace_id=workspace_id,
        project_id=project_id,
        issued_by_user_id=issued_by_user_id,
        expires_at=expires_at,
        consumed_at=None,
        expected_state_version=expected_sv,
        created_at=now,
    )
    db.add(row)
    auth_service.record_audit(
        db,
        action="local_parser_callback_ticket_issue",
        result="success",
        actor_user_id=issued_by_user_id,
        workspace_id=workspace_id,
        target="single_project_10m",
        commit=False,
    )
    db.commit()
    return {
        "ticket": raw,
        "expiresAt": expires_at.isoformat(),
        "callbackPath": CALLBACK_PATH,
    }


def normalize_callback_body(raw_body: bytes) -> tuple[str, str, str | None]:
    """
    模块：公共回调请求体规范化
    用途：手工解析 JSON，限制键与长度，避免框架 422 回显敏感输入。
    对接：POST /api/local-parser/callback。
    二次开发：只允许 markdown/source/filename；source 仅 mineru|docling 精确成员；超限 413，其余非法 400。
      禁止客户端投稿 expectedStateVersion。
    """
    if len(raw_body) > MAX_BODY_BYTES:
        raise TicketServiceError(413, CODE_PAYLOAD_TOO_LARGE, MSG_PAYLOAD_TOO_LARGE)

    try:
        parsed: Any = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST) from exc

    if not isinstance(parsed, dict):
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    allowed = {"markdown", "source", "filename"}
    if set(parsed.keys()) - allowed:
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)
    if "markdown" not in parsed or "source" not in parsed:
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    markdown_raw = parsed.get("markdown")
    source_raw = parsed.get("source")
    filename_raw = parsed.get("filename", None)

    if not isinstance(markdown_raw, str) or not isinstance(source_raw, str):
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)
    if filename_raw is not None and not isinstance(filename_raw, str):
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    markdown = markdown_raw.strip()
    if not markdown or len(markdown) > MAX_MARKDOWN_CODEPOINTS:
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    if source_raw not in ALLOWED_CALLBACK_SOURCES:
        raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    filename: str | None
    if filename_raw is None:
        filename = None
    else:
        filename = filename_raw.strip()
        if not filename or len(filename) > MAX_FILENAME_CODEPOINTS:
            raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)
        for ch in ("\r", "\n", "\x00", "/", "\\"):
            if ch in filename:
                raise TicketServiceError(400, CODE_BAD_REQUEST, MSG_BAD_REQUEST)

    return markdown, source_raw, filename


def _atomic_consume_ticket(
    db: Session, *, raw_ticket: str, now: datetime
) -> LocalParserCallbackTicketRow:
    """
    模块：原子消费票据
    用途：consumed_at IS NULL 且未过期的条件 UPDATE，rowcount 必须为 1。
    对接：apply_one_time_callback。
    二次开发：禁止先查后改 Python 标志；失败统一 TicketServiceError 401。
    """
    if not raw_ticket or not isinstance(raw_ticket, str):
        raise TicketServiceError(401, CODE_TICKET_INVALID, MSG_TICKET_INVALID)

    digest = digest_ticket(raw_ticket)
    result = db.execute(
        update(LocalParserCallbackTicketRow)
        .where(
            LocalParserCallbackTicketRow.ticket_digest == digest,
            LocalParserCallbackTicketRow.consumed_at.is_(None),
            LocalParserCallbackTicketRow.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if result.rowcount != 1:
        raise TicketServiceError(401, CODE_TICKET_INVALID, MSG_TICKET_INVALID)

    row = db.scalars(
        select(LocalParserCallbackTicketRow).where(
            LocalParserCallbackTicketRow.ticket_digest == digest
        )
    ).first()
    if row is None:
        raise TicketServiceError(401, CODE_TICKET_INVALID, MSG_TICKET_INVALID)
    return row


def _finalize_success_writes(
    db: Session,
    ticket: LocalParserCallbackTicketRow,
    *,
    markdown: str,
    source: str,
    filename: str | None,
    now: datetime,
    locked_state_row: ProjectEditorStateRow | None,
    before_state: dict[str, Any],
) -> dict[str, Any]:
    """
    模块：同事务落库解析结果
    用途：写 parsed_markdown、成功 parse task、项目步骤、成功审计与 local_parser 修订（不 commit）。
    对接：apply_one_time_callback。
    二次开发：不得调用 update_project 等会中途 commit 的路径；复用锁后行，禁止 db.get editor-state；
      调用前须已通过锁后版本校验；revision 来源固定字面量 local_parser，禁止客户端 source 控制。
    """
    project = db.get(Project, ticket.project_id)
    if project is None or project.workspace_id != ticket.workspace_id:
        raise TicketServiceError(401, CODE_TICKET_INVALID, MSG_TICKET_INVALID)

    md = markdown
    if filename:
        md = f"# 解析结果：{filename}\n\n> 来源：{source}\n\n" + md

    # 复用锁后行；原行为空才按既有语义创建，不再 db.get editor-state
    state = locked_state_row
    if state is None:
        state = ProjectEditorStateRow(project_id=ticket.project_id, mode="ALIGNED")
        db.add(state)
    state.parsed_markdown = md
    state.updated_at = now

    # P13-D1：一次性票据无 Request；唯一可信 actor 为签发者 issued_by_user_id
    ticket_actor = ticket.issued_by_user_id
    if not (
        isinstance(ticket_actor, str)
        and ticket_actor
        and ticket_actor.strip() == ticket_actor
        and len(ticket_actor) <= 64
    ):
        ticket_actor = None

    task = ProjectTaskRow(
        id=f"task_{secrets.token_hex(8)}",
        project_id=ticket.project_id,
        type="parse",
        status="success",
        progress=100,
        message=f"本地回传完成（{source}）",
        result_json=json.dumps(
            {
                "source": source,
                "filename": filename,
                "chars": len(md),
            },
            ensure_ascii=False,
        ),
        actor_user_id=ticket_actor,
        created_at=now,
        updated_at=now,
    )
    db.add(task)

    project.status = "analyzing"
    project.technical_plan_step = 1
    project.updated_at = now

    auth_service.record_audit(
        db,
        action="local_parser_callback_apply",
        result="success",
        actor_user_id=ticket.issued_by_user_id,
        workspace_id=ticket.workspace_id,
        target="one_time_ticket",
        commit=False,
    )

    # 业务与成功审计暂存后：同一内存行构造 after，无提交记录修订
    after_state = editor_state_service._state_from_row(ticket.project_id, state)
    editor_state_revision_service.record_editor_state_transition(
        db,
        ticket.workspace_id,
        ticket.project_id,
        before_state=before_state,
        after_state=after_state,
        source_kind="local_parser",
        actor_user_id=ticket_actor,
    )
    return {
        "ok": True,
        "chars": len(md),
        "taskId": task.id,
    }


def apply_one_time_callback(
    db: Session,
    *,
    raw_ticket: str,
    markdown: str,
    source: str,
    filename: str | None,
) -> dict[str, Any]:
    """
    模块：一次性票据回调应用
    用途：原子消费 + 锁后版本 CAS + 解析结果/任务/项目步骤/审计同一事务提交。
    对接：POST /api/local-parser/callback。
    二次开发：
      - 版本陈旧或票据版本 NULL：正文/任务/项目/成功审计零写，但必须单独提交消费 → 409；
      - 再次同票 → 401；绝不返回 currentStateVersion；
      - 非版本中途失败必须完整 rollback，票据保持可重用；
      - 不得接受 X-Local-Token 作为回退。
    """
    now = utc_now()
    # 版本冲突路径会先 commit 消费再抛 409，避免 except 误 rollback
    consumption_committed = False
    try:
        ticket = _atomic_consume_ticket(db, raw_ticket=raw_ticket, now=now)

        expected = ticket.expected_state_version
        # 旧库空版本票据：绝不写 editor-state，但必须消费
        if not editor_state_service.is_valid_state_version(expected):
            db.commit()
            consumption_committed = True
            raise TicketServiceError(
                409, CODE_STATE_VERSION_CONFLICT, MSG_STATE_VERSION_CONFLICT
            )

        try:
            # 保存同一次锁后权威 before 与锁后行，仅 fresh 分支传入 finalize
            locked_state_row, before_state = (
                editor_state_service.lock_and_assert_expected_state_version(
                    db,
                    ticket.workspace_id,
                    ticket.project_id,
                    expected,  # type: ignore[arg-type]
                )
            )
        except editor_state_service.EditorStateVersionConflict:
            # 陈旧：仅提交消费，零写正文/任务/项目/成功审计/修订
            db.commit()
            consumption_committed = True
            raise TicketServiceError(
                409, CODE_STATE_VERSION_CONFLICT, MSG_STATE_VERSION_CONFLICT
            ) from None

        payload = _finalize_success_writes(
            db,
            ticket,
            markdown=markdown,
            source=source,
            filename=filename,
            now=now,
            locked_state_row=locked_state_row,
            before_state=before_state,
        )
        db.commit()
        return payload
    except TicketServiceError:
        if not consumption_committed:
            db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
