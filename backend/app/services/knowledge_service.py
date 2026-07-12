"""
模块：知识库服务（RAG：入库 + 关键词/向量混合检索）
用途：
  - 文件夹/文档 CRUD；上传落盘 → parse → chunk → embedding → ready
  - search_chunks：关键词分 + 向量余弦混合；可选 folder_ids
  - build_kb_prompt_block / search_prompt_block 供生成注入
对接：
  - 路由 /api/knowledge/*
  - task_service._kb_search_block（outline/chapter）
  - embedding_service（本地哈希 / 可选 API）
二次开发：
  - 可换 FTS5/真语义模型；勿与 project_files 混表混目录
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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import KbChunkRow, KbDocumentRow, KbFolderRow
from app.services import embedding_service, parse_service

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


def search_chunks(
    db: Session,
    workspace_id: str,
    query: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    folder_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    用途：混合检索 ready 文档分块 = 关键词分 + 向量余弦。
    folder_ids：非空时只搜这些文件夹；None/[] 表示全库。
    返回：[{chunkId, documentId, docName, title, content, score, keywordScore, vectorScore}, ...]
    """
    q = (query or "").strip()
    tokens = _tokenize_query(q)
    # 无 token 时仍可用向量（整句 local embed）
    if not tokens and not q:
        return []

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

    q_vec = embedding_service.embed_one(q, db=db, workspace_id=workspace_id)
    scored: list[tuple[float, float, float, KbChunkRow]] = []
    # (hybrid, kw, vec, chunk)
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
        emb = embedding_service.loads_embedding(
            getattr(ch, "embedding_json", None)
        )
        if emb is None and (ch.content or "").strip():
            # 旧数据无向量：即时本地补算（不写库，避免检索侧写）
            emb = embedding_service.local_embed(
                f"{ch.title or ''}\n{ch.content or ''}"
            )
        vec_s = embedding_service.cosine(q_vec, emb) if emb else 0.0
        # 混合：关键词归一近似 + 向量加权
        kw_norm = min(kw / 10.0, 1.0)
        hybrid = kw_norm * 4.0 + vec_s * 6.0
        # 无任何信号则跳过
        if hybrid <= 0 and kw <= 0 and vec_s < 0.15:
            continue
        # 纯向量弱相关也保留（改写查询）
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
                "vectorScore": round(vec_s, 4),
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
