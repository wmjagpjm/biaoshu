"""
模块：项目编辑器状态服务
用途：读写大纲/章节/事实/结构化分析/guidance/解析文/商务标字段；响应矩阵乐观版本防多端覆盖。
对接：GET|PUT /api/projects/{id}/editor-state
二次开发：商务字段整包存 business_json，API 拆成 businessQualify 等 camelCase。
  responseMatrixVersion 由收敛后的矩阵内容哈希得出，勿绑 updated_at；冲突时整包不写。
"""

from __future__ import annotations

import json
from hashlib import sha1
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ProjectEditorStateRow
from app.services.project_service import get_project

# business_json 内键名（snake）
_BIZ_KEYS = ("qualify", "toc", "quote", "commit")
_MATRIX_KINDS = frozenset({"requirement", "scoring"})
_MATRIX_STATUSES = frozenset({"uncovered", "partial", "covered", "waived"})


class ResponseMatrixVersionConflict(Exception):
    """
    用途：PUT 携带陈旧 responseMatrixVersion 时拒绝整包写入。
    对接：projects.put_editor_state → HTTP 409 detail。
    二次开发：detail 必须含 message / responseMatrix / currentResponseMatrixVersion。
    """

    def __init__(
        self,
        *,
        message: str,
        current_matrix: list[dict],
        current_version: str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.current_matrix = current_matrix
        self.current_version = current_version


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def empty_analysis() -> dict:
    """用途：空结构化分析。"""
    return {
        "overview": "",
        "techRequirements": [],
        "rejectionRisks": [],
        "scoringPoints": [],
    }


def empty_response_matrix() -> list[dict]:
    """用途：空响应矩阵，表示尚未建立要求/评分点到章节的映射。"""
    return []


def compute_response_matrix_version(matrix: Any) -> str:
    """
    用途：对收敛/规范化后的 responseMatrix 计算稳定版本号。
    对接：EditorStateOut.responseMatrixVersion；多端 PUT 乐观锁。
    二次开发：仅依赖矩阵行内容；正文/概述/updatedAt 变化不得改变版本。
    """
    rows = normalize_response_matrix(matrix) if not isinstance(matrix, list) else matrix
    # 已是规范行则直接序列化；再走一遍 normalize 保证键序与缺省一致
    canonical_rows = normalize_response_matrix(rows)
    payload = json.dumps(
        canonical_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "rmv_" + sha1(payload.encode("utf-8")).hexdigest()[:20]


def empty_business() -> dict:
    """
    用途：空商务标工作区包。
    对接：businessQualify / businessToc / businessQuote / businessCommit
    """
    return {
        "qualify": [],
        "toc": [],
        "quote": {"rows": [], "notes": ""},
        "commit": [],
    }


def normalize_business(raw: Any) -> dict:
    """用途：规范 business 对象，兼容缺字段。"""
    base = empty_business()
    if not isinstance(raw, dict):
        return base
    q = raw.get("qualify")
    if isinstance(q, list):
        base["qualify"] = q
    t = raw.get("toc")
    if isinstance(t, list):
        base["toc"] = t
    quote = raw.get("quote")
    if isinstance(quote, dict):
        rows = quote.get("rows")
        base["quote"] = {
            "rows": rows if isinstance(rows, list) else [],
            "notes": str(quote.get("notes") or ""),
        }
    c = raw.get("commit")
    if isinstance(c, list):
        base["commit"] = c
    return base


def normalize_analysis(raw: Any, fallback_overview: str = "") -> dict:
    """用途：规范 analysis 对象，兼容缺字段。"""
    base = empty_analysis()
    if isinstance(raw, dict):
        base["overview"] = str(raw.get("overview") or fallback_overview or "")
        tr = raw.get("techRequirements") or raw.get("tech_requirements") or []
        rr = raw.get("rejectionRisks") or raw.get("rejection_risks") or []
        sp = raw.get("scoringPoints") or raw.get("scoring_points") or []
        if isinstance(tr, list):
            base["techRequirements"] = [str(x) for x in tr if str(x).strip()]
        if isinstance(rr, list):
            base["rejectionRisks"] = [str(x) for x in rr if str(x).strip()]
        if isinstance(sp, list):
            points = []
            for p in sp:
                if isinstance(p, dict):
                    points.append(
                        {
                            "name": str(p.get("name") or ""),
                            "weight": str(p.get("weight") or ""),
                        }
                    )
                elif p:
                    points.append({"name": str(p), "weight": ""})
            base["scoringPoints"] = points
    elif fallback_overview:
        base["overview"] = fallback_overview
    return base


def _string_list(raw: Any) -> list[str]:
    values: list[str] = []
    if not isinstance(raw, list):
        return values
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _matrix_id(source_key: str) -> str:
    return f"mx_{sha1(source_key.encode('utf-8')).hexdigest()[:16]}"


def normalize_response_matrix(raw: Any) -> list[dict]:
    """
    用途：规范响应矩阵行，避免坏 JSON、非法状态或错误类型破坏 editor-state。
    对接：EditorStateOut.responseMatrix；useTechnicalPlanEditors。
    """
    if not isinstance(raw, list):
        return empty_response_matrix()
    rows: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in _MATRIX_KINDS:
            continue
        source_text = str(item.get("sourceText") or item.get("source_text") or "").strip()
        if not source_text:
            continue
        source_key = str(item.get("sourceKey") or item.get("source_key") or "").strip()
        if not source_key:
            source_key = f"{kind}:{source_text.casefold()}"
        raw_index = item.get("sourceIndex", item.get("source_index", index))
        try:
            source_index = max(0, int(raw_index))
        except (TypeError, ValueError):
            source_index = index
        status = str(item.get("status") or "uncovered").strip()
        if status not in _MATRIX_STATUSES:
            status = "uncovered"
        row_id = str(item.get("id") or "").strip() or _matrix_id(source_key)
        rows.append(
            {
                "id": row_id[:64],
                "kind": kind,
                "sourceKey": source_key[:240],
                "sourceIndex": source_index,
                "sourceText": source_text,
                "weight": str(item.get("weight") or ""),
                "chapterIds": _string_list(item.get("chapterIds") or item.get("chapter_ids")),
                "outlineNodeIds": _string_list(
                    item.get("outlineNodeIds") or item.get("outline_node_ids")
                ),
                "status": status,
                "notes": str(item.get("notes") or ""),
            }
        )
    return rows


def _id_set(raw: Any) -> set[str]:
    ids: set[str] = set()
    if not isinstance(raw, list):
        return ids
    stack = list(raw)
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id:
            ids.add(item_id)
        children = item.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return ids


def reconcile_response_matrix(raw: Any, outline: Any, chapters: Any) -> list[dict]:
    """
    用途：按当前大纲/章节移除矩阵死链接；无有效链接时降级覆盖状态。
    对接：GET|PUT /api/projects/{id}/editor-state；前端响应矩阵面板。
    """
    outline_ids = _id_set(outline)
    chapter_ids = _id_set(chapters)
    reconciled: list[dict] = []
    for item in normalize_response_matrix(raw):
        valid_chapter_ids = [
            chapter_id for chapter_id in item["chapterIds"] if chapter_id in chapter_ids
        ]
        valid_outline_ids = [
            node_id for node_id in item["outlineNodeIds"] if node_id in outline_ids
        ]
        status = item["status"]
        if status != "waived" and not (valid_chapter_ids or valid_outline_ids):
            status = "uncovered"
        reconciled.append(
            {
                **item,
                "chapterIds": valid_chapter_ids,
                "outlineNodeIds": valid_outline_ids,
                "status": status,
            }
        )
    return reconciled


def _read_business_blob(row: ProjectEditorStateRow | None) -> dict:
    if row is None:
        return empty_business()
    return normalize_business(_loads(getattr(row, "business_json", None)))


def _current_response_matrix(row: ProjectEditorStateRow | None) -> list[dict]:
    """用途：读取并收敛当前库中的响应矩阵（无行则空）。"""
    if row is None:
        return empty_response_matrix()
    return reconcile_response_matrix(
        _loads(getattr(row, "response_matrix_json", None)),
        _loads(row.outline_json),
        _loads(row.chapters_json),
    )


def get_editor_state(db: Session, workspace_id: str, project_id: str) -> dict:
    """
    用途：返回编辑器状态字典（camelCase），含 responseMatrixVersion。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None:
        biz = empty_business()
        empty_matrix = empty_response_matrix()
        return {
            "projectId": project_id,
            "outline": None,
            "chapters": None,
            "facts": None,
            "mode": "ALIGNED",
            "analysisOverview": None,
            "analysis": empty_analysis(),
            "responseMatrix": empty_matrix,
            "responseMatrixVersion": compute_response_matrix_version(empty_matrix),
            "guidance": None,
            "parsedMarkdown": None,
            "businessQualify": biz["qualify"],
            "businessToc": biz["toc"],
            "businessQuote": biz["quote"],
            "businessCommit": biz["commit"],
            "updatedAt": None,
        }
    analysis = normalize_analysis(
        _loads(row.analysis_json),
        fallback_overview=row.analysis_overview or "",
    )
    # 若 JSON 空但有 overview 字段，回填
    if not analysis.get("overview") and row.analysis_overview:
        analysis["overview"] = row.analysis_overview
    outline = _loads(row.outline_json)
    chapters = _loads(row.chapters_json)
    biz = _read_business_blob(row)
    response_matrix = reconcile_response_matrix(
        _loads(getattr(row, "response_matrix_json", None)),
        outline,
        chapters,
    )
    return {
        "projectId": project_id,
        "outline": outline,
        "chapters": chapters,
        "facts": _loads(row.facts_json),
        "mode": row.mode or "ALIGNED",
        "analysisOverview": analysis.get("overview") or row.analysis_overview,
        "analysis": analysis,
        "responseMatrix": response_matrix,
        "responseMatrixVersion": compute_response_matrix_version(response_matrix),
        "guidance": _loads(row.guidance_json),
        "parsedMarkdown": row.parsed_markdown,
        "businessQualify": biz["qualify"],
        "businessToc": biz["toc"],
        "businessQuote": biz["quote"],
        "businessCommit": biz["commit"],
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def upsert_editor_state(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    outline: Any = ...,
    chapters: Any = ...,
    facts: Any = ...,
    mode: str | None = None,
    analysis_overview: str | None = ...,
    analysis: Any = ...,
    response_matrix: Any = ...,
    response_matrix_version: Any = ...,
    guidance: Any = ...,
    parsed_markdown: str | None = ...,
    business_qualify: Any = ...,
    business_toc: Any = ...,
    business_quote: Any = ...,
    business_commit: Any = ...,
) -> dict:
    """
    用途：部分更新；analysis 与 analysis_overview 双写；商务字段合并进 business_json。
    二次开发：同时带 responseMatrix + responseMatrixVersion 时做乐观锁；冲突则整包不写。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)

    writing_matrix = response_matrix is not ... and response_matrix is not None
    client_version = (
        None
        if response_matrix_version is ... or response_matrix_version is None
        else str(response_matrix_version).strip() or None
    )
    if writing_matrix and client_version is not None:
        current_matrix = _current_response_matrix(row)
        current_version = compute_response_matrix_version(current_matrix)
        if client_version != current_version:
            raise ResponseMatrixVersionConflict(
                message="响应矩阵已被其他终端更新，请重新载入后再保存",
                current_matrix=current_matrix,
                current_version=current_version,
            )

    if row is None:
        row = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
        db.add(row)

    if outline is not ...:
        row.outline_json = _dumps(outline)
    if chapters is not ...:
        row.chapters_json = _dumps(chapters)
    if facts is not ...:
        row.facts_json = _dumps(facts)
    if mode is not None:
        row.mode = mode if mode in ("ALIGNED", "FREE") else "ALIGNED"
    if analysis is not ...:
        norm = normalize_analysis(analysis)
        row.analysis_json = _dumps(norm)
        row.analysis_overview = norm.get("overview") or ""
    elif analysis_overview is not ...:
        row.analysis_overview = analysis_overview
        # 合并进 analysis_json
        prev = normalize_analysis(_loads(row.analysis_json), analysis_overview or "")
        prev["overview"] = analysis_overview or ""
        row.analysis_json = _dumps(prev)
    if response_matrix is not ... and response_matrix is not None:
        row.response_matrix_json = _dumps(normalize_response_matrix(response_matrix))
    if guidance is not ...:
        row.guidance_json = _dumps(guidance)
    if parsed_markdown is not ...:
        row.parsed_markdown = parsed_markdown

    biz_touched = any(
        x is not ...
        for x in (business_qualify, business_toc, business_quote, business_commit)
    )
    if biz_touched:
        biz = _read_business_blob(row)
        if business_qualify is not ...:
            biz["qualify"] = business_qualify if isinstance(business_qualify, list) else []
        if business_toc is not ...:
            biz["toc"] = business_toc if isinstance(business_toc, list) else []
        if business_quote is not ...:
            if isinstance(business_quote, dict):
                rows = business_quote.get("rows")
                biz["quote"] = {
                    "rows": rows if isinstance(rows, list) else [],
                    "notes": str(business_quote.get("notes") or ""),
                }
            else:
                biz["quote"] = {"rows": [], "notes": ""}
        if business_commit is not ...:
            biz["commit"] = business_commit if isinstance(business_commit, list) else []
        row.business_json = _dumps(normalize_business(biz))

    row.response_matrix_json = _dumps(
        reconcile_response_matrix(
            _loads(getattr(row, "response_matrix_json", None)),
            _loads(row.outline_json),
            _loads(row.chapters_json),
        )
    )
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return get_editor_state(db, workspace_id, project_id)
