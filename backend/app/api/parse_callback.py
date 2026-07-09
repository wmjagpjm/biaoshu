"""
模块：本地解析（MinerU）结果回传
用途：接收本机解析出的 Markdown，写入项目 editor-state.parsed_markdown。
对接：POST /api/projects/{id}/parse-callback
二次开发：可校验 X-Local-Token（settings.local_parser_token 非空时强制）。
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import ProjectEditorStateRow, ProjectTaskRow
from app.services.project_service import ProjectNotFoundError, get_project, update_project

router = APIRouter(prefix="/projects", tags=["parse-callback"])


class ParseCallbackIn(BaseModel):
    """用途：MinerU / 本地助手回传体。"""

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
