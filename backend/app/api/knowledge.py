"""
模块：知识库路由
用途：文件夹/文档 CRUD、上传并同步解析分块、重索引、移动、混合检索；P9C 语义索引状态与重建。
对接：
  - 前端 useKnowledgeBase / KnowledgeBasePage
  - knowledge_service（业务）
  - 生成侧不经本路由，直接调 knowledge_service.search_prompt_block
二次开发：
  - 大文件可改为异步 task；检索可加 folderId 查询参数（已支持）
  - 语义索引 API 禁止接受模型 URL/Token/路径/维度；重建仅 BackgroundTasks
"""

from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import knowledge_service
from app.services.knowledge_service import (
    KnowledgeNotFoundError,
    SemanticIndexConflictError,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class FolderCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=200)
    parent_id: str | None = Field(default=None, alias="parentId")


class DocsMoveBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ids: list[str] = Field(default_factory=list)
    folder_id: str = Field(..., alias="folderId")


@router.get("/folders")
def list_folders(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> list[dict]:
    rows = knowledge_service.list_folders(db, workspace_id)
    return [knowledge_service.folder_to_dict(r) for r in rows]


@router.post("/folders", status_code=201)
def create_folder(
    body: FolderCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    try:
        row = knowledge_service.create_folder(
            db,
            workspace_id,
            name=body.name,
            parent_id=body.parent_id,
        )
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="父文件夹不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return knowledge_service.folder_to_dict(row)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> None:
    try:
        knowledge_service.delete_folder(db, workspace_id, folder_id)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="文件夹不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/docs")
def list_docs(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    folder_id: Annotated[str | None, Query(alias="folderId")] = None,
) -> list[dict]:
    rows = knowledge_service.list_docs(db, workspace_id, folder_id=folder_id)
    return [knowledge_service.doc_to_dict(r) for r in rows]


@router.post("/docs/upload", status_code=201)
async def upload_doc(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="知识库文档 txt/md/docx/pdf"),
    folder_id: Annotated[str | None, Form(alias="folderId")] = None,
) -> dict:
    raw = await file.read()
    try:
        row = knowledge_service.upload_and_index(
            db,
            workspace_id,
            settings,
            filename=file.filename or "upload.bin",
            content=raw,
            content_type=file.content_type or "",
            folder_id=folder_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return knowledge_service.doc_to_dict(row)


@router.post("/docs/{doc_id}/reindex")
def reindex_doc(
    doc_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    try:
        row = knowledge_service.index_document(db, workspace_id, doc_id, settings)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="文档不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return knowledge_service.doc_to_dict(row)


@router.delete("/docs/{doc_id}", status_code=204)
def delete_doc(
    doc_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        knowledge_service.delete_doc(db, workspace_id, doc_id, settings)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="文档不存在") from None


@router.post("/docs/move")
def move_docs(
    body: DocsMoveBody,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict[str, Any]:
    try:
        n = knowledge_service.move_docs(db, workspace_id, body.ids, body.folder_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"moved": n}


@router.get("/search")
def search(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    q: Annotated[str, Query(min_length=1, description="检索词")],
    top_k: Annotated[int, Query(alias="topK", ge=1, le=20)] = 5,
    folder_ids: Annotated[
        list[str] | None,
        Query(alias="folderId", description="限定文件夹，可重复"),
    ] = None,
) -> dict[str, Any]:
    """
    用途：知识库混合检索；附加 semanticStatus / semanticIndexId / vectorScore。
    说明：无 active 语义索引时仅关键词，vectorScore=0，semanticStatus=index_not_built。
    """
    hits = knowledge_service.search_chunks(
        db, workspace_id, q, top_k=top_k, folder_ids=folder_ids
    )
    if hits:
        semantic_status = hits[0].get("semanticStatus") or "index_not_built"
        semantic_index_id = hits[0].get("semanticIndexId")
    else:
        semantic_status, semantic_index_id, _ = (
            knowledge_service.resolve_search_semantic_meta(db, workspace_id)
        )
    return {
        "query": q,
        "count": len(hits),
        "semanticStatus": semantic_status,
        "semanticIndexId": semantic_index_id,
        "items": [
            {
                "chunkId": h["chunkId"],
                "documentId": h["documentId"],
                "docName": h["docName"],
                "folderId": h.get("folderId"),
                "title": h["title"],
                "content": (h["content"] or "")[:800],
                "score": h["score"],
                "vectorScore": h.get("vectorScore", 0.0),
                "keywordScore": h.get("keywordScore", 0.0),
            }
            for h in hits
        ],
    }


@router.get("/semantic-index")
def get_semantic_index_status(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict[str, Any]:
    """
    用途：当前工作空间语义索引状态读模型（脱敏）。
    对接：GET /api/knowledge/semantic-index。
    """
    return knowledge_service.get_semantic_index_status(db, workspace_id)


@router.get("/semantic-index/{index_id}")
def get_semantic_index_detail(
    index_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict[str, Any]:
    """
    用途：按 id 查询语义索引；跨空间 404。
    对接：GET /api/knowledge/semantic-index/{index_id}。
    """
    try:
        row = knowledge_service.get_semantic_index(db, workspace_id, index_id)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="语义索引不存在") from None
    return knowledge_service.semantic_index_to_dict(row)


@router.post(
    "/semantic-index/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
)
def rebuild_semantic_index(
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict[str, Any]:
    """
    用途：无请求体创建 queued 重建并注册后台执行；并发 409。
    对接：POST /api/knowledge/semantic-index/rebuild；BackgroundTasks。
    二次开发：禁止接受模型名/URL/Token/路径/维度。
    """
    try:
        row = knowledge_service.create_semantic_index_rebuild(db, workspace_id)
    except SemanticIndexConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    background_tasks.add_task(
        knowledge_service.execute_semantic_index_rebuild, row.id
    )
    return knowledge_service.semantic_index_to_dict(row)
