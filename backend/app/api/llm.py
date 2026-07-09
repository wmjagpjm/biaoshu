"""
模块：LLM 辅助路由
用途：连通性测试；后续可挂非 revise 的通用 completion。
对接：POST /api/llm/test；设置页「测试连接」可接此接口。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import LlmTestOut
from app.core.database import get_db
from app.services import llm_service
from app.services.llm_service import LlmCallError, LlmConfigError

router = APIRouter(prefix="/llm", tags=["llm"])


@router.post("/test", response_model=LlmTestOut)
def test_llm(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> LlmTestOut:
    """
    用途：用当前 settings 中的 Key/Base/模型发一条极短请求，验证配置可用。
    """
    try:
        data = llm_service.test_connection(db, workspace_id)
    except LlmConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LlmCallError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return LlmTestOut(ok=True, model=data["model"], reply=data["reply"])
