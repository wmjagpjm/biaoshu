"""
模块：本地解析（MinerU）结果回传
用途：个人兼容旧回调写入 parsed_markdown；P8C 签发一次性票据与精确公开回调。
对接：POST /api/projects/{id}/parse-callback；POST /api/projects/{id}/parse-callback-ticket；
      POST /api/local-parser/callback；local_parser_ticket_service；require_strict_bid_writer。
二次开发：旧回调语义不变；公开回调仅认 X-Local-Parse-Ticket，禁止 X-Local-Token 回退；
      手工校验 body，避免 Pydantic 422 回显敏感输入。
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id, require_strict_bid_writer
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import ProjectEditorStateRow, ProjectTaskRow
from app.services.local_parser_ticket_service import (
    CODE_TICKET_INVALID,
    MSG_TICKET_INVALID,
    TicketServiceError,
    accumulate_body_with_limit,
    apply_one_time_callback,
    issue_callback_ticket,
    normalize_callback_body,
)
from app.services.project_service import ProjectNotFoundError, get_project, update_project

router = APIRouter(prefix="/projects", tags=["parse-callback"])
public_router = APIRouter(tags=["parse-callback-public"])


class ParseCallbackIn(BaseModel):
    """用途：MinerU / 本地助手回传体（旧路径，个人兼容）。"""

    model_config = ConfigDict(populate_by_name=True)

    markdown: str = Field(min_length=1, description="解析后的 Markdown 全文")
    source: str = "mineru"
    filename: str | None = None


@router.post("/{project_id}/parse-callback")
def parse_callback(
    project_id: str,
    body: ParseCallbackIn,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_local_token: Annotated[str | None, Header(alias="X-Local-Token")] = None,
) -> dict:
    """
    用途：写入解析 Markdown；可选 Token 校验。
    """
    expected = (settings.local_parser_token or "").strip()
    if expected and (x_local_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="本地解析 Token 无效")

    try:
        get_project(db, workspace_id, project_id)
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None

    md = body.markdown.strip()
    if body.filename:
        md = f"# 解析结果：{body.filename}\n\n> 来源：{body.source}\n\n" + md

    state = db.get(ProjectEditorStateRow, project_id)
    if state is None:
        state = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
        db.add(state)
    state.parsed_markdown = md
    state.updated_at = datetime.now(timezone.utc)

    # 记一条成功 parse 任务，便于工作区「最近任务」
    task = ProjectTaskRow(
        id=f"task_{secrets.token_hex(8)}",
        project_id=project_id,
        type="parse",
        status="success",
        progress=100,
        message=f"本地回传完成（{body.source}）",
        result_json=json.dumps(
            {
                "source": body.source,
                "filename": body.filename,
                "chars": len(md),
            },
            ensure_ascii=False,
        ),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.commit()

    update_project(
        db, workspace_id, project_id, status="analyzing", technical_plan_step=1
    )

    return {
        "ok": True,
        "projectId": project_id,
        "chars": len(md),
        "source": body.source,
        "taskId": task.id,
    }


@router.post("/{project_id}/parse-callback-ticket", status_code=201)
def issue_parse_callback_ticket(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_strict_bid_writer)],
) -> dict:
    """
    模块：P8C 一次性回传票据签发
    用途：strict bid_writer 为当前空间项目签发 10 分钟单次票据；无请求体。
    对接：require_strict_bid_writer；local_parser_ticket_service.issue_callback_ticket。
    二次开发：禁止客户端 workspace/user/TTL；响应仅 ticket/expiresAt/callbackPath；no-store。
    """
    user_id = getattr(request.state, "auth_db_user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"code": "auth_required", "message": "未登录或会话已失效"},
        )
    try:
        payload = issue_callback_ticket(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            issued_by_user_id=user_id,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None

    response.headers["Cache-Control"] = "no-store"
    return payload


@public_router.post("/local-parser/callback")
async def local_parser_public_callback(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    模块：P8C 精确公开回传回调
    用途：无会话消费一次性票据并同事务写入解析结果；仅认 X-Local-Parse-Ticket。
    对接：auth_middleware POST-only 精确公开；local_parser_ticket_service。
    二次开发：禁止 X-Local-Token 回退；缺/空票据须在读正文前 401；正文用 stream 硬限 2MiB；
          固定错误不反射输入；响应仅 ok/chars/taskId。
    """
    # 缺票/空票先拒绝，避免无授权请求消耗大正文读取
    raw_ticket = (request.headers.get("X-Local-Parse-Ticket") or "").strip()
    if not raw_ticket:
        raise HTTPException(
            status_code=401,
            detail={"code": CODE_TICKET_INVALID, "message": MSG_TICKET_INVALID},
        )

    try:
        # 分块累计，超 2 MiB 立即 413，不先整包载入内存
        raw_body = await accumulate_body_with_limit(request.stream())
        markdown, source, filename = normalize_callback_body(raw_body)
        payload = apply_one_time_callback(
            db,
            raw_ticket=raw_ticket,
            markdown=markdown,
            source=source,
            filename=filename,
        )
    except TicketServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.as_detail()
        ) from None
    except Exception:
        # 中途未捕获异常已在 service 内 rollback；不回显细节
        raise HTTPException(
            status_code=500,
            detail={"code": "local_parser_callback_failed", "message": "回传处理失败"},
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return payload
