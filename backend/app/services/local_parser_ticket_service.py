"""
模块：P8C 本地解析一次性回传票据服务
用途：签发短期单项目单次票据，并在公共回调中原子消费后同事务写入解析结果。
对接：parse_callback 签发/回调路由；LocalParserCallbackTicketRow；auth_service.record_audit。
二次开发：禁止保存/日志输出原始票据；禁止调用会中途 commit 的 service；不启动 MinerU/Docling。
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
from app.services import auth_service
from app.services.project_service import ProjectNotFoundError, get_project

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
    用途：为当前空间项目签发固定 10 分钟、单次消费票据；库内只存摘要。
    对接：POST /api/projects/{projectId}/parse-callback-ticket。
    二次开发：禁止客户端指定 TTL/workspace/user；审计不得记录票据/摘要/项目/正文。
    """
    try:
        get_project(db, workspace_id, project_id)
    except ProjectNotFoundError as exc:
        raise ProjectNotFoundError(project_id) from exc

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
    二次开发：只允许 markdown/source/filename；超限 413，其余非法 400。
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

    if source_raw != "mineru":
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
) -> dict[str, Any]:
    """
    模块：同事务落库解析结果
    用途：写 parsed_markdown、成功 parse task、项目步骤，并记固定审计（不 commit）。
    对接：apply_one_time_callback。
    二次开发：不得调用 update_project 等会中途 commit 的路径；测试可 monkeypatch 本函数验证回滚。
    """
    project = db.get(Project, ticket.project_id)
    if project is None or project.workspace_id != ticket.workspace_id:
        raise TicketServiceError(401, CODE_TICKET_INVALID, MSG_TICKET_INVALID)

    md = markdown
    if filename:
        md = f"# 解析结果：{filename}\n\n> 来源：{source}\n\n" + md

    state = db.get(ProjectEditorStateRow, ticket.project_id)
    if state is None:
        state = ProjectEditorStateRow(project_id=ticket.project_id, mode="ALIGNED")
        db.add(state)
    state.parsed_markdown = md
    state.updated_at = now

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
    用途：原子消费 + 解析结果/任务/项目步骤/审计同一事务提交。
    对接：POST /api/local-parser/callback。
    二次开发：中途失败必须 rollback；不得接受 X-Local-Token 作为回退。
    """
    now = utc_now()
    try:
        ticket = _atomic_consume_ticket(db, raw_ticket=raw_ticket, now=now)
        payload = _finalize_success_writes(
            db,
            ticket,
            markdown=markdown,
            source=source,
            filename=filename,
            now=now,
        )
        db.commit()
        return payload
    except TicketServiceError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
