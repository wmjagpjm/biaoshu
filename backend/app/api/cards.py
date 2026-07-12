"""
模块：卡片化知识与素材库路由
用途：提供卡片列表/详情/创建/上传/沉淀/更新/删除/图片内容与插入项目的 HTTP 入口；跨 workspace 统一 404。
对接：/api/cards；/api/projects/{id}/insert-card；card_service；frontend knowledge-base / ChapterEditor。
二次开发：路由层只做参数与状态码映射；禁止直接读写 ORM；勿扩展为 AI 自动注入或跨 workspace 共享。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    InsertCardBody,
    InsertCardOut,
    KnowledgeCardCreate,
    KnowledgeCardFromChunkCreate,
    KnowledgeCardFromProjectImageCreate,
    KnowledgeCardOut,
    KnowledgeCardSummaryOut,
    KnowledgeCardUpdate,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import card_service
from app.services.card_service import CardNotFoundError, CardValidationError
from app.services.project_service import ProjectNotFoundError

router = APIRouter(tags=["cards"])


def _to_summary_out(row) -> KnowledgeCardSummaryOut:
    data = (
        row
        if isinstance(row, dict)
        else card_service.card_to_summary_data(row)
    )
    return KnowledgeCardSummaryOut.model_validate(data)


def _to_detail_out(row) -> KnowledgeCardOut:
    data = (
        row
        if isinstance(row, dict)
        else card_service.card_to_detail_data(row)
    )
    return KnowledgeCardOut.model_validate(data)


@router.get("/cards", response_model=list[KnowledgeCardSummaryOut])
def list_cards(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    card_type: Annotated[
        str | None,
        Query(alias="type", description="document|image|qualification|performance"),
    ] = None,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="active|archived|all；缺省=active（隐藏归档）",
        ),
    ] = None,
) -> list[KnowledgeCardSummaryOut]:
    """
    用途：当前 workspace 卡片列表（轻量摘要，不含全文/base64）。
    对接：GET /api/cards；知识库卡片 Tab。
    约定：默认仅 active；status=all 才包含归档。
    """
    try:
        rows = card_service.list_cards(
            db, workspace_id, q=q, card_type=card_type, status=status_filter
        )
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return [_to_summary_out(r) for r in rows]


@router.post(
    "/cards",
    response_model=KnowledgeCardOut,
    status_code=status.HTTP_201_CREATED,
)
def create_card(
    body: KnowledgeCardCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> KnowledgeCardOut:
    """
    用途：手工创建文本类卡片（document/qualification/performance）。
    对接：POST /api/cards。
    """
    try:
        row = card_service.create_text_card(
            db,
            workspace_id,
            card_type=body.type,
            title=body.title,
            body_markdown=body.body_markdown,
            tags=body.tags,
            summary=body.summary,
            source_label=body.source_label,
            payload=body.payload,
        )
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_detail_out(row)


@router.post(
    "/cards/upload-image",
    response_model=KnowledgeCardOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_image_card(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: UploadFile = File(..., description="PNG/JPEG/GIF 图片"),
    title: Annotated[str | None, Form()] = None,
    tags: Annotated[str | None, Form(description="逗号分隔标签")] = None,
    summary: Annotated[str | None, Form()] = None,
) -> KnowledgeCardOut:
    """
    用途：上传并验证图片，沉淀为 image 卡片快照。
    对接：POST /api/cards/upload-image。
    """
    raw = await file.read()
    tag_list = None
    if tags:
        tag_list = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
    try:
        row = card_service.create_image_card_from_bytes(
            db,
            workspace_id,
            settings,
            filename=file.filename or "image.png",
            content=raw,
            title=title,
            tags=tag_list,
            summary=summary,
            source_type="upload",
            source_label=file.filename or "本地上传",
        )
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_detail_out(row)


@router.post(
    "/cards/from-chunk",
    response_model=KnowledgeCardOut,
    status_code=status.HTTP_201_CREATED,
)
def create_card_from_chunk(
    body: KnowledgeCardFromChunkCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> KnowledgeCardOut:
    """
    用途：从知识分块复制正文快照；源删除后卡片仍可预览。
    对接：POST /api/cards/from-chunk。
    """
    try:
        row = card_service.create_from_chunk(
            db,
            workspace_id,
            chunk_id=body.chunk_id,
            title=body.title,
            tags=body.tags,
            summary=body.summary,
            card_type=body.type if body.type != "image" else "document",
        )
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="知识分块不存在") from None
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_detail_out(row)


@router.post(
    "/cards/from-project-image",
    response_model=KnowledgeCardOut,
    status_code=status.HTTP_201_CREATED,
)
def create_card_from_project_image(
    body: KnowledgeCardFromProjectImageCreate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> KnowledgeCardOut:
    """
    用途：从项目图片复制字节快照；源项目删除后卡片 content 仍可读。
    对接：POST /api/cards/from-project-image。
    """
    try:
        row = card_service.create_from_project_image(
            db,
            workspace_id,
            settings,
            project_id=body.project_id,
            file_id=body.file_id,
            title=body.title,
            tags=body.tags,
            summary=body.summary,
        )
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="项目或图片不存在") from None
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_detail_out(row)


@router.get("/cards/{card_id}", response_model=KnowledgeCardOut)
def get_card(
    card_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> KnowledgeCardOut:
    """
    用途：卡片详情（含正文或图片元数据）；跨 workspace → 404。
    对接：GET /api/cards/{id}。
    """
    try:
        row = card_service.get_card(db, workspace_id, card_id)
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="卡片不存在") from None
    return _to_detail_out(row)


@router.patch("/cards/{card_id}", response_model=KnowledgeCardOut)
def update_card(
    card_id: str,
    body: KnowledgeCardUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> KnowledgeCardOut:
    """
    用途：更新标题/标签/状态/摘要/正文；归档走 status=archived。
    对接：PATCH /api/cards/{id}。
    """
    try:
        row = card_service.update_card(
            db,
            workspace_id,
            card_id,
            title=body.title,
            tags=body.tags,
            status=body.status,
            summary=body.summary,
            body_markdown=body.body_markdown,
            source_label=body.source_label,
            payload=body.payload,
        )
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="卡片不存在") from None
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_detail_out(row)


@router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(
    card_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """
    用途：删除卡片及卡片图片文件；不影响已插入项目的图片副本。
    对接：DELETE /api/cards/{id}。
    """
    try:
        card_service.delete_card(db, workspace_id, settings, card_id)
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="卡片不存在") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cards/{card_id}/content")
def get_card_image_content(
    card_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """
    用途：读取图片卡片二进制内容（服务端路径，禁止客户端路径）。
    对接：GET /api/cards/{id}/content。
    """
    try:
        row, path = card_service.resolve_card_image(
            db, workspace_id, settings, card_id
        )
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="卡片不存在") from None
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="卡片图片文件不存在") from None
    media = row.content_type or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=row.title or path.name,
    )


@router.post(
    "/projects/{project_id}/insert-card",
    response_model=InsertCardOut,
)
def insert_card_into_project(
    project_id: str,
    body: InsertCardBody,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InsertCardOut:
    """
    用途：生成可插入 Markdown；图片卡先复制为项目 role=image。
    对接：POST /api/projects/{projectId}/insert-card；章节「插入卡片」。
    二次开发：不写 editor-state；前端用户确认后追加。
    """
    try:
        data = card_service.insert_card_into_project(
            db,
            workspace_id,
            settings,
            project_id=project_id,
            card_id=body.card_id,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
    except CardNotFoundError:
        raise HTTPException(status_code=404, detail="卡片不存在") from None
    except CardValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return InsertCardOut.model_validate(data)
