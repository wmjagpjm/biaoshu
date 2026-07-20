"""
模块：本地解析（MinerU）结果回传
用途：个人兼容旧回调写入 parsed_markdown；P8C 签发一次性票据与精确公开回调。
对接：POST /api/projects/{id}/parse-callback；POST /api/projects/{id}/parse-callback-ticket；
      POST /api/local-parser/callback；local_parser_ticket_service；require_strict_bid_writer。
二次开发：个人回调强制 expectedStateVersion 同事务 CAS；公开回调仅认 X-Local-Parse-Ticket；
      禁止调用会自行 commit 的 upsert/update_project；手工校验 body 防 422 回显敏感输入。
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_request_actor_user_id, get_workspace_id, require_strict_bid_writer
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import Project, ProjectEditorStateRow, ProjectTaskRow
from app.services import editor_state_revision_service, editor_state_service
from app.services.local_parser_ticket_service import (
    CODE_TICKET_INVALID,
    MSG_TICKET_INVALID,
    TicketServiceError,
    accumulate_body_with_limit,
    apply_one_time_callback,
    issue_callback_ticket,
    normalize_callback_body,
)
from app.services.project_service import ProjectNotFoundError, get_project

router = APIRouter(prefix="/projects", tags=["parse-callback"])
public_router = APIRouter(tags=["parse-callback-public"])


class ParseCallbackIn(BaseModel):
    """
    用途：MinerU / 本地助手回传体（旧路径，个人兼容）。
    二次开发：P12B-C2 强制合法 expectedStateVersion；缺失/非法格式固定 422 零写。
    """

    model_config = ConfigDict(populate_by_name=True)

    markdown: str = Field(min_length=1, description="解析后的 Markdown 全文")
    source: str = "mineru"
    filename: str | None = None
    expected_state_version: str = Field(
        ...,
        alias="expectedStateVersion",
        pattern=r"^esv_[0-9a-f]{32}$",
        description="服务端权威全状态版本（须先 GET editor-state）",
    )


@router.post("/{project_id}/parse-callback")
def parse_callback(
    project_id: str,
    body: ParseCallbackIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_local_token: Annotated[str | None, Header(alias="X-Local-Token")] = None,
) -> dict:
    """
    用途：写入解析 Markdown；可选 Token 校验；P12B-C2 锁后全状态 CAS 同事务落库。
    二次开发：禁止先 upsert 再补任务/项目；陈旧固定 409 + currentStateVersion；成功返回 stateVersion。
      P12C-B-C1：同事务无提交记录 callback 修订；禁止客户端 source 决定内部来源。
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

    now = datetime.now(timezone.utc)
    # P13-D1：个人 callback 可信 actor 仅来自 request.state；客户端 body 无效
    actor_user_id = get_request_actor_user_id(request)
    try:
        # 共用锁后原语：版本不匹配抛冲突；不自行 commit
        # 保存同一次锁返回的权威 before，供提交前修订账本使用
        row, before_state = editor_state_service.lock_and_assert_expected_state_version(
            db,
            workspace_id,
            project_id,
            body.expected_state_version,
        )
        if row is None:
            row = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
            db.add(row)
        row.parsed_markdown = md
        row.updated_at = now

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
            actor_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(task)

        # 同事务更新项目步骤；禁止调用会自行 commit 的 update_project
        project = db.get(Project, project_id)
        if project is None or project.workspace_id != workspace_id:
            raise ProjectNotFoundError(project_id)
        project.status = "analyzing"
        project.technical_plan_step = 1
        project.updated_at = now

        # commit 前基于内存行构造新版本，保证与落库一致
        new_state = editor_state_service._state_from_row(project_id, row)
        new_sv = new_state["stateVersion"]
        # 唯一 commit 前无提交记录修订；来源固定服务端字面量 callback
        editor_state_revision_service.record_editor_state_transition(
            db,
            workspace_id,
            project_id,
            before_state=before_state,
            after_state=new_state,
            source_kind="callback",
            actor_user_id=actor_user_id,
        )
        db.commit()
    except editor_state_service.EditorStateVersionConflict as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT,
                "message": editor_state_service.MSG_FULL_STATE_VERSION_CONFLICT,
                "currentStateVersion": exc.current_state_version,
            },
        ) from None
    except ProjectNotFoundError:
        db.rollback()
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except HTTPException:
        raise
    except Exception:
        # 中途异常完整 rollback；固定 500，不回显异常原文
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": "parse_callback_failed", "message": "回传处理失败"},
        ) from None

    return {
        "ok": True,
        "projectId": project_id,
        "chars": len(md),
        "source": body.source,
        "taskId": task.id,
        "stateVersion": new_sv,
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
          固定错误不反射输入；响应仅 ok/chars/taskId；版本冲突 409 不返回 currentStateVersion。
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
