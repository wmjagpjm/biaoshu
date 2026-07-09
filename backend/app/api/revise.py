"""
模块：产物修订路由
用途：接收用户反馈，调用 LLM 定向修订（同步返回摘要与可选正文）。
对接：
  - POST /api/projects/{project_id}/artifacts/{artifact_id}/revise
  - 前端 useProjectGuidance.submitRevise
  - docs/ai-feedback-loop.md
二次开发：异步化时改为创建 task 并 SSE 推送进度。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import ReviseIn, ReviseOut
from app.core.database import get_db
from app.services import revise_service
from app.services.llm_service import LlmCallError, LlmConfigError
from app.services.project_service import ProjectNotFoundError

router = APIRouter(tags=["revise"])


@router.post(
    "/projects/{project_id}/artifacts/{artifact_id}/revise",
    response_model=ReviseOut,
)
def revise_artifact(
    project_id: str,
    artifact_id: str,
    body: ReviseIn,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ReviseOut:
    """
    用途：对指定项目产物做一次「按反馈调整」。
    artifact_id：前端可传阶段名或章节 id（产物表未建前仅作追踪标识）。
    """
    try:
        data = revise_service.revise_artifact(
            db,
            workspace_id,
            project_id,
            artifact_id,
            stage=body.stage,
            message=body.message,
            preserve_structure=body.preserve_structure,
            base_content=body.base_content,
            guidance=body.guidance,
            target_id=body.target_id,
            target_label=body.target_label,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LlmConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LlmCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return ReviseOut.model_validate(data)
