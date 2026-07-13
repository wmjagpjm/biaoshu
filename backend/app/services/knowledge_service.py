"""
模块：知识库服务（RAG：入库 + 关键词/P9C 离线语义混合检索）
用途：
  - 文件夹/文档 CRUD；上传落盘 → parse → chunk → 兼容哈希 → ready
  - P9C 版本化语义索引：queued/running → 校验后切 active；失败保留旧 active
  - search_chunks：关键词 + 仅 active 同维语义向量；无索引时关键词降级
  - build_kb_prompt_block / search_prompt_block 供生成注入
对接：
  - 路由 /api/knowledge/*、/api/knowledge/semantic-index*
  - task_service._kb_search_block（outline/chapter）
  - embedding_service.OfflineBgeEmbedder（离线 512 维，禁外发）
二次开发：
  - 禁止把 legacy embedding_json 或 API embedding 当作语义结果
  - 注入硬顶见 _PROMPT_MAX_CHARS；analyze 任务禁止调用本检索
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.models.entities import (
    KbChunkRow,
    KbDocumentRow,
    KbFolderRow,
    SemanticChunkEmbeddingRow,
    SemanticEmbeddingIndexRow,
)
from app.services import embedding_service, parse_service

# 语义索引固定错误/状态码（API 与落库共用）
SEMANTIC_READY = "ready"
SEMANTIC_INDEX_NOT_BUILT = "index_not_built"
SEMANTIC_INDEX_BUILDING = "index_building"
SEMANTIC_INDEX_FAILED = "index_failed"
SEMANTIC_INDEX_INTERRUPTED = "index_interrupted"

# 分块参数
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 80
# 检索注入硬顶
_PROMPT_MAX_CHARS = 4000
_DEFAULT_TOP_K = 5

_ALLOWED_EXT = {".txt", ".md", ".markdown", ".docx", ".pdf"}

_TOKEN_SPLIT = re.compile(r"[\s,，。；;、\|/\\（）()【】\[\]「」\"'`~!@#$%^&*+=<>?：:]+")


class KnowledgeNotFoundError(KeyError):
    """用途：文档/文件夹不存在。"""


class SemanticIndexConflictError(RuntimeError):
    """用途：同工作空间已存在 queued/running 语义索引重建。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kb_root(settings: Settings) -> Path:
    # 与 uploads 并列：./uploads 旁用 ./data/knowledge，或 upload_dir 父目录
    base = Path(settings.upload_dir).resolve().parent / "data" / "knowledge"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _doc_dir(settings: Settings, workspace_id: str, doc_id: str) -> Path:
    d = _kb_root(settings) / workspace_id / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _size_label(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _relative_updated(iso: str | None) -> str:
    if not iso:
        return ""
    return "最近"


def ensure_default_folder(db: Session, workspace_id: str) -> KbFolderRow:
    """用途：保证存在默认「收件箱」文件夹。"""
    stmt = (
        select(KbFolderRow)
        .where(
            KbFolderRow.workspace_id == workspace_id,
            KbFolderRow.name == "收件箱",
        )
        .limit(1)
    )
    row = db.scalars(stmt).first()
    if row:
        return row
    row = KbFolderRow(
        id=f"fld_{secrets.token_hex(6)}",
        workspace_id=workspace_id,
        name="收件箱",
        parent_id=None,
        created_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def folder_to_dict(row: KbFolderRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "parentId": row.parent_id,
    }


def doc_to_dict(row: KbDocumentRow) -> dict:
    tags: list[str] = []
    if row.tags_json:
        try:
            raw = json.loads(row.tags_json)
            if isinstance(raw, list):
                tags = [str(x) for x in raw]
        except json.JSONDecodeError:
            tags = []
    updated = row.updated_at.isoformat() if row.updated_at else None
    return {
        "id": row.id,
        "name": row.name,
        "tags": tags,
        "chunks": row.chunk_count,
        "updated": _relative_updated(updated),
        "updatedAt": updated,
        "category": "知识库",
        "folderId": row.folder_id,
        "status": row.status,
        "statusMessage": row.status_message,
        "sizeLabel": _size_label(row.size_bytes) if row.size_bytes else None,
    }


def list_folders(db: Session, workspace_id: str) -> list[KbFolderRow]:
    ensure_default_folder(db, workspace_id)
    stmt = (
        select(KbFolderRow)
        .where(KbFolderRow.workspace_id == workspace_id)
        .order_by(KbFolderRow.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def create_folder(
    db: Session, workspace_id: str, *, name: str, parent_id: str | None = None
) -> KbFolderRow:
    name = (name or "").strip()
    if not name:
        raise ValueError("文件夹名称不能为空")
    if parent_id:
        parent = db.get(KbFolderRow, parent_id)
        if parent is None or parent.workspace_id != workspace_id:
            raise KnowledgeNotFoundError(parent_id)
    row = KbFolderRow(
        id=f"fld_{secrets.token_hex(6)}",
        workspace_id=workspace_id,
        name=name[:200],
        parent_id=parent_id,
        created_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_folder(db: Session, workspace_id: str, folder_id: str) -> None:
    row = db.get(KbFolderRow, folder_id)
    if row is None or row.workspace_id != workspace_id:
        raise KnowledgeNotFoundError(folder_id)
    # 有文档则禁止删
    stmt = (
        select(KbDocumentRow)
        .where(
            KbDocumentRow.workspace_id == workspace_id,
            KbDocumentRow.folder_id == folder_id,
        )
        .limit(1)
    )
    if db.scalars(stmt).first():
        raise ValueError("文件夹非空，请先移动或删除文档")
    # 至少保留一个文件夹
    others = list_folders(db, workspace_id)
    if len(others) <= 1:
        raise ValueError("至少保留一个文件夹")
    db.delete(row)
    db.commit()


def list_docs(
    db: Session, workspace_id: str, *, folder_id: str | None = None
) -> list[KbDocumentRow]:
    ensure_default_folder(db, workspace_id)
    stmt = select(KbDocumentRow).where(KbDocumentRow.workspace_id == workspace_id)
    if folder_id:
        stmt = stmt.where(KbDocumentRow.folder_id == folder_id)
    stmt = stmt.order_by(KbDocumentRow.updated_at.desc())
    return list(db.scalars(stmt).all())


def get_doc(db: Session, workspace_id: str, doc_id: str) -> KbDocumentRow:
    row = db.get(KbDocumentRow, doc_id)
    if row is None or row.workspace_id != workspace_id:
        raise KnowledgeNotFoundError(doc_id)
    return row


def get_chunk(db: Session, workspace_id: str, chunk_id: str) -> KbChunkRow:
    """
    用途：按 workspace 读取单个知识分块（跨空间/不存在 → KnowledgeNotFoundError）。
    对接：card_service.create_from_chunk；禁止把 chunk 表当卡片库复用。
    """
    row = db.get(KbChunkRow, chunk_id)
    if row is None or row.workspace_id != workspace_id:
        raise KnowledgeNotFoundError(chunk_id)
    return row


def _tokenize_query(query: str) -> list[str]:
    raw = (query or "").strip().lower()
    if not raw:
        return []
    parts = [p for p in _TOKEN_SPLIT.split(raw) if p]
    # 中文连续串也按整体保留；短 token 过滤
    tokens: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) >= 2:
            tokens.append(p)
    # 无分隔时整句也作为 token（中文检索）
    if not tokens and len(raw) >= 2:
        tokens = [raw[:32]]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:20]


def chunk_markdown(text: str) -> list[dict[str, str]]:
    """
    用途：按标题切分，过长块再按字数滑窗。
    返回：[{title, content}, ...]
    """
    text = (text or "").strip()
    if not text:
        return []

    sections: list[tuple[str, str]] = []
    current_title = ""
    buf: list[str] = []

    for line in text.replace("\r\n", "\n").split("\n"):
        if re.match(r"^#{1,6}\s+\S", line):
            if buf:
                sections.append((current_title, "\n".join(buf).strip()))
                buf = []
            current_title = re.sub(r"^#{1,6}\s+", "", line).strip()
            buf.append(line)
        else:
            buf.append(line)
    if buf:
        sections.append((current_title, "\n".join(buf).strip()))

    chunks: list[dict[str, str]] = []
    for title, body in sections:
        if not body:
            continue
        if len(body) <= _CHUNK_SIZE:
            chunks.append({"title": title or body[:40], "content": body})
            continue
        start = 0
        while start < len(body):
            end = min(len(body), start + _CHUNK_SIZE)
            piece = body[start:end]
            chunks.append(
                {
                    "title": title or piece[:40],
                    "content": piece,
                }
            )
            if end >= len(body):
                break
            start = max(0, end - _CHUNK_OVERLAP)
    return chunks


def _replace_chunks(
    db: Session, workspace_id: str, doc: KbDocumentRow, pieces: list[dict[str, str]]
) -> int:
    # 删旧块
    old = list(
        db.scalars(
            select(KbChunkRow).where(KbChunkRow.document_id == doc.id)
        ).all()
    )
    for c in old:
        db.delete(c)
    db.flush()
    # 批量向量化（API 或本地）
    texts: list[str] = []
    metas: list[tuple[int, str, str]] = []  # ordinal, title, content
    for i, p in enumerate(pieces):
        content = (p.get("content") or "").strip()
        if not content:
            continue
        title = (p.get("title") or "")[:500]
        texts.append(f"{title}\n{content}" if title else content)
        metas.append((i, title, content))
    vectors = embedding_service.embed_texts(db, workspace_id, texts)
    n = 0
    for (i, title, content), vec in zip(metas, vectors):
        db.add(
            KbChunkRow(
                id=f"chk_{secrets.token_hex(8)}",
                document_id=doc.id,
                workspace_id=workspace_id,
                ordinal=i,
                title=title or None,
                content=content,
                embedding_json=embedding_service.dumps_embedding(vec),
                created_at=_now(),
            )
        )
        n += 1
    doc.chunk_count = n
    return n


def index_document(
    db: Session,
    workspace_id: str,
    doc_id: str,
    settings: Settings | None = None,
) -> KbDocumentRow:
    """用途：解析磁盘文件并重建分块。"""
    settings = settings or get_settings()
    doc = get_doc(db, workspace_id, doc_id)
    if not doc.stored_name:
        raise ValueError("文档无落盘文件，无法索引")
    path = _doc_dir(settings, workspace_id, doc.id) / doc.stored_name
    if not path.exists():
        doc.status = "failed"
        doc.status_message = "文件已丢失"
        doc.updated_at = _now()
        db.commit()
        db.refresh(doc)
        return doc

    try:
        doc.status = "parsing"
        doc.status_message = "解析中…"
        doc.updated_at = _now()
        db.commit()

        md = parse_service.parse_file_to_markdown(path, doc.name)

        doc.status = "indexing"
        doc.status_message = "分块索引中…"
        doc.updated_at = _now()
        db.commit()

        pieces = chunk_markdown(md)
        if not pieces:
            pieces = [{"title": doc.name, "content": md[:_CHUNK_SIZE] or doc.name}]
        n = _replace_chunks(db, workspace_id, doc, pieces)
        doc.status = "ready"
        doc.status_message = None
        doc.chunk_count = n
        doc.updated_at = _now()
        db.commit()
        db.refresh(doc)
        return doc
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.status_message = f"{type(exc).__name__}: {exc}"[:1000]
        doc.updated_at = _now()
        db.commit()
        db.refresh(doc)
        return doc


def upload_and_index(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    content_type: str = "",
    folder_id: str | None = None,
    tags: list[str] | None = None,
) -> KbDocumentRow:
    """用途：上传 → 落盘 → 同步解析分块。"""
    if len(content) > settings.max_upload_bytes:
        raise ValueError(
            f"文件过大（{len(content)} 字节），上限 {settings.max_upload_bytes}"
        )
    safe_name = Path(filename).name or "upload.bin"
    ext = Path(safe_name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise ValueError(
            f"不支持的文件类型 {ext or '（无扩展名）'}，请上传 txt/md/docx/pdf"
        )

    default = ensure_default_folder(db, workspace_id)
    fid = folder_id or default.id
    folder = db.get(KbFolderRow, fid)
    if folder is None or folder.workspace_id != workspace_id:
        raise ValueError("文件夹不存在")

    doc_id = f"kbd_{secrets.token_hex(8)}"
    stored = f"{doc_id}{ext}"
    dest_dir = _doc_dir(settings, workspace_id, doc_id)
    dest = dest_dir / stored
    dest.write_bytes(content)

    doc = KbDocumentRow(
        id=doc_id,
        workspace_id=workspace_id,
        folder_id=fid,
        name=safe_name,
        tags_json=json.dumps(tags or ["上传"], ensure_ascii=False),
        status="pending",
        status_message="排队解析…",
        size_bytes=len(content),
        stored_name=stored,
        mime=content_type or "",
        chunk_count=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return index_document(db, workspace_id, doc.id, settings)


def delete_doc(
    db: Session,
    workspace_id: str,
    doc_id: str,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    doc = get_doc(db, workspace_id, doc_id)
    # chunks 级联
    db.delete(doc)
    db.commit()
    # 磁盘
    try:
        d = _kb_root(settings) / workspace_id / doc_id
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def move_docs(
    db: Session, workspace_id: str, doc_ids: list[str], folder_id: str
) -> int:
    folder = db.get(KbFolderRow, folder_id)
    if folder is None or folder.workspace_id != workspace_id:
        raise ValueError("目标文件夹不存在")
    n = 0
    for did in doc_ids:
        try:
            doc = get_doc(db, workspace_id, did)
        except KnowledgeNotFoundError:
            continue
        doc.folder_id = folder_id
        doc.updated_at = _now()
        n += 1
    if n:
        db.commit()
    return n


def semantic_index_to_dict(row: SemanticEmbeddingIndexRow | None) -> dict[str, Any]:
    """
    用途：语义索引脱敏读模型；不含路径、密钥、正文或远端错误。
    说明：chunkCount 兼容字段，语义等价于 embeddedChunks（已成功嵌入分块数）。
    """
    if row is None:
        return {
            "id": None,
            "workspaceId": None,
            "status": SEMANTIC_INDEX_NOT_BUILT,
            "provider": embedding_service.OFFLINE_PROVIDER,
            "modelId": get_settings().semantic_model_id,
            "modelFingerprint": None,
            "dimension": embedding_service.OFFLINE_DIM,
            "totalChunks": 0,
            "embeddedChunks": 0,
            "chunkCount": 0,
            "errorCode": SEMANTIC_INDEX_NOT_BUILT,
            "startedAt": None,
            "finishedAt": None,
            "createdAt": None,
            "updatedAt": None,
        }
    total = int(getattr(row, "total_chunks", 0) or 0)
    embedded = int(getattr(row, "embedded_chunks", 0) or 0)
    # 兼容：chunkCount 等价 embeddedChunks；旧行可能仅有 chunk_count
    if embedded <= 0 and int(row.chunk_count or 0) > 0:
        embedded = int(row.chunk_count)
    return {
        "id": row.id,
        "workspaceId": row.workspace_id,
        "status": row.status,
        "provider": row.provider,
        "modelId": row.model_id,
        "modelFingerprint": row.model_fingerprint or None,
        "dimension": row.dimension,
        "totalChunks": total,
        "embeddedChunks": embedded,
        "chunkCount": embedded,
        "errorCode": row.error_code,
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_active_semantic_index(
    db: Session, workspace_id: str
) -> SemanticEmbeddingIndexRow | None:
    """用途：读取工作空间当前 active 语义索引（0 或 1）。"""
    stmt = (
        select(SemanticEmbeddingIndexRow)
        .where(
            SemanticEmbeddingIndexRow.workspace_id == workspace_id,
            SemanticEmbeddingIndexRow.status == "active",
        )
        .limit(1)
    )
    return db.scalars(stmt).first()


def get_semantic_index(
    db: Session, workspace_id: str, index_id: str
) -> SemanticEmbeddingIndexRow:
    """用途：按 workspace 读取索引；跨空间/不存在 → KnowledgeNotFoundError。"""
    row = db.get(SemanticEmbeddingIndexRow, index_id)
    if row is None or row.workspace_id != workspace_id:
        raise KnowledgeNotFoundError(index_id)
    return row


def get_semantic_index_status(db: Session, workspace_id: str) -> dict[str, Any]:
    """
    用途：汇总当前空间语义索引状态。
    规则：优先报告 queued/running（errorCode=index_building），即使存在旧 active，
    以便任务2禁用按钮与轮询；search 仍继续读旧 active 向量。
    运行时：active 存在但 OfflineBgeEmbedder 未 ready 时，保留 status=active 与 id，
    仅临时覆盖 errorCode=model_unavailable；只读 is_ready，禁止加载/下载/写库/触网。
    """
    building = db.scalars(
        select(SemanticEmbeddingIndexRow)
        .where(
            SemanticEmbeddingIndexRow.workspace_id == workspace_id,
            SemanticEmbeddingIndexRow.status.in_(("queued", "running")),
        )
        .order_by(SemanticEmbeddingIndexRow.created_at.desc())
        .limit(1)
    ).first()
    if building is not None:
        data = semantic_index_to_dict(building)
        # 对外状态码：构建中（覆盖库内 null，供前端轮询）
        data["errorCode"] = SEMANTIC_INDEX_BUILDING
        return data

    active = get_active_semantic_index(db, workspace_id)
    if active is not None:
        data = semantic_index_to_dict(active)
        # 进程重启后索引仍 active，但内存模型未就绪 → 可见关键词降级
        embedder = embedding_service.get_offline_embedder()
        if not embedder.is_ready():
            data["errorCode"] = embedding_service.ERR_MODEL_UNAVAILABLE
        return data

    latest = db.scalars(
        select(SemanticEmbeddingIndexRow)
        .where(SemanticEmbeddingIndexRow.workspace_id == workspace_id)
        .order_by(SemanticEmbeddingIndexRow.created_at.desc())
        .limit(1)
    ).first()
    if latest is not None:
        return semantic_index_to_dict(latest)
    return semantic_index_to_dict(None)


def _count_active_semantic_builds(db: Session, workspace_id: str) -> int:
    stmt = select(SemanticEmbeddingIndexRow).where(
        SemanticEmbeddingIndexRow.workspace_id == workspace_id,
        SemanticEmbeddingIndexRow.status.in_(("queued", "running")),
    )
    return len(list(db.scalars(stmt).all()))


def create_semantic_index_rebuild(
    db: Session, workspace_id: str
) -> SemanticEmbeddingIndexRow:
    """
    用途：无请求体创建 queued 语义索引运行；同空间并发 queued/running → 409 语义。
    对接：POST /api/knowledge/semantic-index/rebuild。
    二次开发：快路径 count 防常见冲突；最终靠 SQLite 部分唯一索引堵竞态，
    IntegrityError 统一映射 SemanticIndexConflictError，禁止向上暴露库细节。
    """
    if _count_active_semantic_builds(db, workspace_id) > 0:
        raise SemanticIndexConflictError("语义索引正在构建，请稍后再试")
    settings = get_settings()
    now = _now()
    row = SemanticEmbeddingIndexRow(
        id=f"sem_{secrets.token_hex(8)}",
        workspace_id=workspace_id,
        status="queued",
        provider=embedding_service.OFFLINE_PROVIDER,
        model_id=settings.semantic_model_id,
        model_fingerprint="",
        dimension=int(settings.semantic_embedding_dim),
        total_chunks=0,
        embedded_chunks=0,
        chunk_count=0,
        error_code=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # 并发：两条 queued/running 同 workspace 时部分唯一索引拒绝
        db.rollback()
        raise SemanticIndexConflictError("语义索引正在构建，请稍后再试") from None
    db.refresh(row)
    return row


def mark_interrupted_semantic_indexes(db: Session) -> int:
    """
    模块：语义索引中断收敛
    用途：将进程重启前残留的 queued/running 标为 failed/index_interrupted，保留 active。
    对接：app.main.lifespan。
    """
    now = _now()
    result = db.execute(
        update(SemanticEmbeddingIndexRow)
        .where(SemanticEmbeddingIndexRow.status.in_(("queued", "running")))
        .values(
            status="failed",
            error_code=SEMANTIC_INDEX_INTERRUPTED,
            finished_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def execute_semantic_index_rebuild(index_id: str) -> None:
    """
    模块：语义索引后台执行器
    用途：独立 Session 将 queued 推进为 running，写全部分块向量，校验后单事务切 active。
    对接：BackgroundTasks；OfflineBgeEmbedder；任何异常仅 failed 新索引。
    二次开发：禁止记录正文/路径/远端错误原文；不得删除旧 active 向量直至切换成功。
    """
    db = SessionLocal()
    try:
        row = db.get(SemanticEmbeddingIndexRow, index_id)
        if row is None:
            return
        if row.status not in ("queued", "running"):
            return
        workspace_id = row.workspace_id
        settings = get_settings()
        now = _now()
        row.status = "running"
        row.started_at = row.started_at or now
        row.updated_at = now
        row.error_code = None
        db.commit()

        embedder = embedding_service.get_offline_embedder()
        try:
            fingerprint = embedder.ensure_loaded_for_rebuild(settings)
            dim = int(settings.semantic_embedding_dim)
            if dim != embedding_service.OFFLINE_DIM:
                raise embedding_service.OfflineEmbedderError(
                    embedding_service.ERR_MODEL_UNAVAILABLE, "dim"
                )

            # 仅 ready 文档的分块
            ready_docs = list(
                db.scalars(
                    select(KbDocumentRow).where(
                        KbDocumentRow.workspace_id == workspace_id,
                        KbDocumentRow.status == "ready",
                    )
                ).all()
            )
            ready_ids = {d.id for d in ready_docs}
            chunks = []
            if ready_ids:
                chunks = list(
                    db.scalars(
                        select(KbChunkRow).where(
                            KbChunkRow.workspace_id == workspace_id,
                            KbChunkRow.document_id.in_(ready_ids),
                        )
                    ).all()
                )

            texts: list[str] = []
            chunk_ids: list[str] = []
            for ch in chunks:
                content = (ch.content or "").strip()
                if not content:
                    continue
                title = (ch.title or "").strip()
                texts.append(f"{title}\n{content}" if title else content)
                chunk_ids.append(ch.id)

            # 收集有效分块后先写 total，embedded 从 0 起；失败不得虚报完成
            row = db.get(SemanticEmbeddingIndexRow, index_id)
            if row is None:
                return
            row.total_chunks = len(chunk_ids)
            row.embedded_chunks = 0
            row.chunk_count = 0
            row.updated_at = _now()
            db.commit()

            vectors = embedder.embed_texts(texts) if texts else []
            if len(vectors) != len(chunk_ids):
                raise embedding_service.OfflineEmbedderError(
                    embedding_service.ERR_MODEL_UNAVAILABLE, "len"
                )

            # 写入新索引向量（旧 active 不动）
            embedded_n = 0
            for cid, vec in zip(chunk_ids, vectors):
                if len(vec) != dim:
                    raise embedding_service.OfflineEmbedderError(
                        embedding_service.ERR_MODEL_UNAVAILABLE, "dim"
                    )
                db.add(
                    SemanticChunkEmbeddingRow(
                        id=f"sce_{secrets.token_hex(8)}",
                        index_id=index_id,
                        chunk_id=cid,
                        workspace_id=workspace_id,
                        dimension=dim,
                        embedding_json=embedding_service.dumps_embedding(vec) or "[]",
                        created_at=_now(),
                    )
                )
                embedded_n += 1
            db.flush()

            # 校验计数
            written = list(
                db.scalars(
                    select(SemanticChunkEmbeddingRow).where(
                        SemanticChunkEmbeddingRow.index_id == index_id,
                        SemanticChunkEmbeddingRow.workspace_id == workspace_id,
                    )
                ).all()
            )
            if len(written) != len(chunk_ids) or embedded_n != len(chunk_ids):
                raise RuntimeError("index_count_mismatch")
            for w in written:
                if w.dimension != dim:
                    raise RuntimeError("index_dim_mismatch")

            # 写入成功后 embedded 与 total 一致，再允许切 active
            row = db.get(SemanticEmbeddingIndexRow, index_id)
            if row is None:
                return
            row.embedded_chunks = len(written)
            row.chunk_count = len(written)  # 兼容：等价 embeddedChunks
            row.updated_at = _now()
            if int(row.embedded_chunks) != int(row.total_chunks):
                raise RuntimeError("index_progress_mismatch")

            # 单事务切换 active
            old_actives = list(
                db.scalars(
                    select(SemanticEmbeddingIndexRow).where(
                        SemanticEmbeddingIndexRow.workspace_id == workspace_id,
                        SemanticEmbeddingIndexRow.status == "active",
                        SemanticEmbeddingIndexRow.id != index_id,
                    )
                ).all()
            )
            for old in old_actives:
                old.status = "superseded"
                old.updated_at = _now()

            row.status = "active"
            row.model_fingerprint = fingerprint
            row.dimension = dim
            row.error_code = None
            row.finished_at = _now()
            row.updated_at = _now()
            db.commit()
        except embedding_service.OfflineEmbedderError as exc:
            db.rollback()
            _fail_semantic_index(db, index_id, exc.code)
        except Exception:  # noqa: BLE001
            db.rollback()
            _fail_semantic_index(db, index_id, SEMANTIC_INDEX_FAILED)
    finally:
        db.close()


def _fail_semantic_index(db: Session, index_id: str, error_code: str) -> None:
    """用途：将新索引标 failed，不触碰旧 active。"""
    row = db.get(SemanticEmbeddingIndexRow, index_id)
    if row is None:
        return
    if row.status == "active":
        return
    now = _now()
    row.status = "failed"
    row.error_code = error_code if error_code in {
        "model_unavailable",
        "model_storage_insufficient",
        "index_interrupted",
        "index_failed",
        "index_not_built",
        "index_building",
    } else SEMANTIC_INDEX_FAILED
    row.finished_at = now
    row.updated_at = now
    db.commit()


def resolve_search_semantic_meta(
    db: Session, workspace_id: str
) -> tuple[str, str | None, SemanticEmbeddingIndexRow | None]:
    """
    用途：决定检索侧 semanticStatus / semanticIndexId / active 行。
    返回：(status, index_id|None, active_row|None)
    说明：active 存在但 embedder 未 ready 时，无论命中与否均 model_unavailable；
    本函数只读 is_ready，绝不加载模型或触网。
    """
    active = get_active_semantic_index(db, workspace_id)
    if active is not None:
        # 维度必须匹配固定契约
        if int(active.dimension) != embedding_service.OFFLINE_DIM:
            return SEMANTIC_INDEX_NOT_BUILT, None, None
        embedder = embedding_service.get_offline_embedder()
        if not embedder.is_ready():
            return (
                embedding_service.ERR_MODEL_UNAVAILABLE,
                active.id,
                active,
            )
        return SEMANTIC_READY, active.id, active
    if _count_active_semantic_builds(db, workspace_id) > 0:
        return SEMANTIC_INDEX_BUILDING, None, None
    latest_failed = db.scalars(
        select(SemanticEmbeddingIndexRow)
        .where(
            SemanticEmbeddingIndexRow.workspace_id == workspace_id,
            SemanticEmbeddingIndexRow.status == "failed",
        )
        .order_by(SemanticEmbeddingIndexRow.created_at.desc())
        .limit(1)
    ).first()
    if latest_failed is not None and latest_failed.error_code:
        # 无 active 时对外仍可提示最近失败码，但默认关键词降级用 index_not_built
        # 计划要求：无 active → index_not_built/关键词降级
        return SEMANTIC_INDEX_NOT_BUILT, None, None
    return SEMANTIC_INDEX_NOT_BUILT, None, None


def search_chunks(
    db: Session,
    workspace_id: str,
    query: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    folder_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    用途：混合检索 ready 文档分块 = 关键词分 +（仅 active P9C 索引）语义余弦。
    folder_ids：非空时只搜这些文件夹；None/[] 表示全库。
    返回项含 semanticStatus/semanticIndexId（每条一致）与 vectorScore（非 ready 为 0）。
    说明：绝不读取 legacy embedding_json 计算 vectorScore，禁止静默哈希伪语义。
    """
    q = (query or "").strip()
    tokens = _tokenize_query(q)
    if not tokens and not q:
        return []

    semantic_status, semantic_index_id, active = resolve_search_semantic_meta(
        db, workspace_id
    )

    stmt_docs = select(KbDocumentRow).where(
        KbDocumentRow.workspace_id == workspace_id,
        KbDocumentRow.status == "ready",
    )
    folder_filter = [f for f in (folder_ids or []) if f]
    if folder_filter:
        stmt_docs = stmt_docs.where(KbDocumentRow.folder_id.in_(folder_filter))

    ready_rows = list(db.scalars(stmt_docs).all())
    ready_ids = {r.id for r in ready_rows}
    if not ready_ids:
        return []

    docs = {r.id: r for r in ready_rows}
    chunks = list(
        db.scalars(
            select(KbChunkRow).where(
                KbChunkRow.workspace_id == workspace_id,
                KbChunkRow.document_id.in_(ready_ids),
            )
        ).all()
    )

    # 语义向量：仅 active + 模型可用时
    q_vec: list[float] | None = None
    chunk_vecs: dict[str, list[float]] = {}
    if active is not None and semantic_status == SEMANTIC_READY:
        embedder = embedding_service.get_offline_embedder()
        try:
            if not embedder.is_ready():
                # 搜索不得触发下载；若仅本地缓存可加载由 ensure 的注入/已载路径覆盖
                # 无注入且未加载 → 降级为无语义分
                raise embedding_service.OfflineEmbedderError(
                    embedding_service.ERR_MODEL_UNAVAILABLE
                )
            q_vec = embedder.embed_one(q)
            if len(q_vec) != int(active.dimension):
                q_vec = None
                semantic_status = SEMANTIC_INDEX_NOT_BUILT
                semantic_index_id = None
            else:
                rows = list(
                    db.scalars(
                        select(SemanticChunkEmbeddingRow).where(
                            SemanticChunkEmbeddingRow.workspace_id == workspace_id,
                            SemanticChunkEmbeddingRow.index_id == active.id,
                            SemanticChunkEmbeddingRow.dimension == active.dimension,
                        )
                    ).all()
                )
                for r in rows:
                    vec = embedding_service.loads_embedding(r.embedding_json)
                    if vec and len(vec) == active.dimension:
                        chunk_vecs[r.chunk_id] = vec
        except embedding_service.OfflineEmbedderError:
            q_vec = None
            chunk_vecs = {}
            # active 存在但模型不可用：状态改为 model_unavailable，仍返回关键词
            semantic_status = embedding_service.ERR_MODEL_UNAVAILABLE
            semantic_index_id = active.id if active is not None else None

    scored: list[tuple[float, float, float, KbChunkRow]] = []
    for ch in chunks:
        text = (ch.content or "").lower()
        title = (ch.title or "").lower()
        kw = 0.0
        for t in tokens:
            if t in title:
                kw += 3.0
            c = text.count(t)
            if c:
                kw += min(c, 5) * 1.0

        vec_s = 0.0
        if q_vec is not None:
            emb = chunk_vecs.get(ch.id)
            if emb is not None:
                vec_s = embedding_service.cosine(q_vec, emb)

        kw_norm = min(kw / 10.0, 1.0)
        hybrid = kw_norm * 4.0 + vec_s * 6.0
        if hybrid <= 0 and kw <= 0 and vec_s < 0.15:
            continue
        if kw <= 0 and vec_s < 0.22:
            continue
        scored.append((hybrid, kw, vec_s, ch))

    scored.sort(key=lambda x: (-x[0], x[3].ordinal))
    top_k = max(1, min(int(top_k or _DEFAULT_TOP_K), 20))
    results: list[dict[str, Any]] = []
    for hybrid, kw, vec_s, ch in scored[:top_k]:
        doc = docs.get(ch.document_id)
        results.append(
            {
                "chunkId": ch.id,
                "documentId": ch.document_id,
                "docName": doc.name if doc else "",
                "folderId": doc.folder_id if doc else None,
                "title": ch.title or "",
                "content": ch.content,
                "score": round(hybrid, 4),
                "keywordScore": round(kw, 4),
                "vectorScore": round(vec_s, 4)
                if semantic_status == SEMANTIC_READY
                else 0.0,
                "semanticStatus": semantic_status,
                "semanticIndexId": semantic_index_id
                if semantic_status == SEMANTIC_READY
                else (
                    semantic_index_id
                    if semantic_status == embedding_service.ERR_MODEL_UNAVAILABLE
                    else None
                ),
            }
        )
    return results


def build_kb_prompt_block(
    chunks: list[dict[str, Any]], *, max_chars: int = _PROMPT_MAX_CHARS
) -> str:
    """用途：把检索结果压成注入 prompt 的文本块；空则 \"\"。"""
    if not chunks:
        return ""
    lines = ["【知识库参考】（仅作写法与要点参考，禁止照抄未在招标中出现的硬性指标）"]
    used = 0
    for i, c in enumerate(chunks, 1):
        name = c.get("docName") or "文档"
        title = c.get("title") or ""
        body = (c.get("content") or "").strip()
        head = f"{i}. 《{name}》{(' · ' + title) if title else ''}"
        room = max_chars - used - len(head) - 10
        if room < 80:
            break
        if len(body) > room:
            body = body[: room - 1] + "…"
        block = f"{head}\n{body}"
        lines.append(block)
        used += len(block)
        if used >= max_chars:
            break
    if len(lines) <= 1:
        return ""
    return "\n\n".join(lines)


def search_prompt_block(
    db: Session,
    workspace_id: str,
    query: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    folder_ids: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    用途：检索并生成 prompt 块；供 task_service 一次调用。
    返回：(block, citations 精简列表)
    """
    hits = search_chunks(
        db, workspace_id, query, top_k=top_k, folder_ids=folder_ids
    )
    block = build_kb_prompt_block(hits)
    citations = [
        {
            "docName": h.get("docName"),
            "title": h.get("title"),
            "excerpt": (h.get("content") or "")[:160],
            "folderId": h.get("folderId"),
        }
        for h in hits
    ]
    return block, citations
