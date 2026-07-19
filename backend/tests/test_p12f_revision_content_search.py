"""
模块：P12F-F-A / P12F-I 修订可见内容与名称联合搜索后端专项测试
用途：验收 POST .../editor-state-revisions/search 的关键词规范、字段白名单、
  有界 20 候选窗、七列 SQL、来源/时间复用、损坏/权限/五域零写与 GET 兼容；
  以及 P12F-I 合法 display_name 与可见内容联合匹配、去重、先验后搜与 20/21 边界。
对接：POST /api/projects/{projectId}/editor-state-revisions/search；
  editor_state_revision_history_service；api.editor_state_revisions；schemas。
二次开发：
  - 禁止 mock SQLite、宽泛状态码、固定 sleep、反射关键词/正文假绿；
  - 红测必须证明业务语义缺失（名称唯一命中期望 1 实际 0），而非收集/导入/语法/环境失败；
  - 字段白名单以“允许标记命中、禁止标记零命中”成对证明，不得只断言响应无正文；
  - P12F-I 禁止恒真 OR、宽状态、`>=1`、truthy、条件断言、空集合来源与只扫源码冒充运行时。
"""

from __future__ import annotations

import ast
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    ProjectTaskRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12ffa"
_SECRET = "SECRET_P12FFA_BODY_MUST_NOT_LEAK"
_SECRET_EXTRA = "SECRET_P12FFA_EXTRA_KEY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/search"
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt", "displayName"}
)
_SEARCH_TOP = frozenset({"items"})
_LIST_TOP = frozenset({"items"})
_PAGE_TOP = frozenset({"items", "nextCursor"})
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")

_CODE_QUERY_INVALID = "editor_state_revision_search_query_invalid"
_MSG_QUERY_INVALID = "修订搜索关键词无效"
_CODE_REQUEST_INVALID = "editor_state_revision_search_request_invalid"
_MSG_REQUEST_INVALID = "修订搜索请求无效"
_CODE_SOURCE_INVALID = "editor_state_revision_source_invalid"
_MSG_SOURCE_INVALID = "修订来源筛选无效"
_CODE_TIME_RANGE_INVALID = "editor_state_revision_time_range_invalid"
_MSG_TIME_RANGE_INVALID = "修订时间范围筛选无效"
_CODE_CORRUPT = "editor_state_revision_corrupt"
_MSG_CORRUPT = "修订记录数据损坏，无法读取"
_CODE_PROJECT_NOT_FOUND = "project_not_found"
_MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_MSG_ROLE_FORBIDDEN = "当前角色无权访问该功能"
_CODE_CSRF_INVALID = "csrf_invalid"
_MSG_CSRF_INVALID = "CSRF 校验失败"

_OWNER_USER = "admin_p12ffa_owner"
_OWNER_PASS = "TestPass-P12FFA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12FFA-Writer-0001!",
    "finance": "TestPass-P12FFA-Finance-0001!",
    "hr": "TestPass-P12FFA-Hr-0001!",
    "bidder": "TestPass-P12FFA-Bidder-0001!",
}

_NINE_SOURCES = (
    "browser_put",
    "task",
    "revise",
    "callback",
    "local_parser",
    "content_fuse_apply",
    "content_fuse_consume",
    "checkpoint_restore",
    "revision_restore",
)

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_history_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_revisions.py"
)
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "schemas.py"
)

_T_FROM = "2026-07-01T00:00:00.000Z"
_T_MID = "2026-07-15T12:00:00.000Z"
_T_BEFORE = "2026-08-01T00:00:00.000Z"

# 允许字段唯一标记（技术标 + 商务标）
_ALLOW_MARKERS = {
    "outline_title": "ALW_OUT_TITLE_α",
    "outline_desc": "ALW_OUT_DESC_β",
    "outline_child_title": "ALW_OUT_CHILD_TITLE_γ",
    "chapter_title": "ALW_CH_TITLE_δ",
    "chapter_preview": "ALW_CH_PREVIEW_ε",
    "chapter_body": "ALW_CH_BODY_ζ",
    "parsed_md": "ALW_PARSED_MD_η",
    "bq_req": "ALW_BQ_REQ_θ",
    "bq_resp": "ALW_BQ_RESP_ι",
    "bq_evid": "ALW_BQ_EVID_κ",
    "btoc_title": "ALW_BTOC_TITLE_λ",
    "btoc_cat": "ALW_BTOC_CAT_μ",
    "btoc_note": "ALW_BTOC_NOTE_ν",
    "bquote_name": "ALW_BQN_NAME_ξ",
    "bquote_unit": "ALW_BQN_UNIT_ο",
    "bquote_qty": "ALW_BQN_QTY_π",
    "bquote_price": "ALW_BQN_PRICE_ρ",
    "bquote_amount": "ALW_BQN_AMT_σ",
    "bquote_remark": "ALW_BQN_RMK_τ",
    "bquote_notes": "ALW_BQN_NOTES_υ",
    "bcommit_title": "ALW_BCM_TITLE_φ",
    "bcommit_body": "ALW_BCM_BODY_χ",
}

# 禁止字段唯一标记
_FORBID_MARKERS = {
    "id": "FBD_ID_MARKER_01",
    "state_version": "FBD_STATE_VERSION_02",
    "source": "FBD_SOURCE_MARKER_03",
    "status": "FBD_STATUS_MARKER_04",
    "mode": "FBD_MODE_MARKER_05",
    "facts": "FBD_FACTS_MARKER_06",
    "analysis": "FBD_ANALYSIS_MARKER_07",
    "analysis_overview": "FBD_ANAL_OV_MARKER_08",
    "response_matrix": "FBD_MATRIX_MARKER_09",
    "guidance": "FBD_GUIDANCE_MARKER_10",
    "outline_id": "FBD_OUT_ID_MARKER_11",
    "chapter_id": "FBD_CH_ID_MARKER_12",
    "unknown_nested": "FBD_UNKNOWN_NEST_13",
    "numeric_leaf": 123456789,
    "bool_leaf": True,
}


# ---------- fixtures ----------


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(disabled_settings):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def required_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    with TestClient(app) as client:
        yield client


# ---------- helpers ----------


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/search"


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions"


def _page_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/page"


def _create_project(client: TestClient, name: str = "P12F-F-A项目", **kwargs) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
        **kwargs,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _state_with_version(**overrides) -> dict:
    analysis = editor_state_service.empty_analysis()
    business = editor_state_service.empty_business()
    state = {
        "outline": None,
        "chapters": None,
        "facts": None,
        "mode": "ALIGNED",
        "analysis": analysis,
        "responseMatrix": [],
        "guidance": None,
        "parsedMarkdown": None,
        "businessQualify": business["qualify"],
        "businessToc": business["toc"],
        "businessQuote": business["quote"],
        "businessCommit": business["commit"],
        "analysisOverview": analysis.get("overview", ""),
    }
    state.update(overrides)
    snap = editor_state_service.extract_canonical_snapshot(state)
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _variant(tag: str) -> dict:
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "preview": f"预览{tag}",
                "body": f"正文{tag}-{_SECRET}",
            }
        ],
        parsedMarkdown=f"md-{tag}-{_SECRET}",
        guidance=f"指引-{tag}",
    )


def _full_allow_state(tag: str = "full") -> dict:
    """用途：技术标 + 商务标全部允许字段各放唯一标记。"""
    m = {k: f"{v}_{tag}" for k, v in _ALLOW_MARKERS.items()}
    return _state_with_version(
        outline={
            "id": "out_root",
            "title": m["outline_title"],
            "description": m["outline_desc"],
            "children": [
                {
                    "id": "out_child",
                    "title": m["outline_child_title"],
                    "description": "child-desc-plain",
                    "children": [],
                }
            ],
        },
        chapters=[
            {
                "id": "ch_allow",
                "title": m["chapter_title"],
                "preview": m["chapter_preview"],
                "body": m["chapter_body"],
                "status": "pending",
            }
        ],
        parsedMarkdown=m["parsed_md"],
        businessQualify=[
            {
                "id": "bq1",
                "requirement": m["bq_req"],
                "response": m["bq_resp"],
                "evidence": m["bq_evid"],
            }
        ],
        businessToc=[
            {
                "id": "toc1",
                "title": m["btoc_title"],
                "category": m["btoc_cat"],
                "note": m["btoc_note"],
            }
        ],
        businessQuote={
            "rows": [
                {
                    "id": "qr1",
                    "name": m["bquote_name"],
                    "unit": m["bquote_unit"],
                    "quantity": m["bquote_qty"],
                    "unitPrice": m["bquote_price"],
                    "amount": m["bquote_amount"],
                    "remark": m["bquote_remark"],
                }
            ],
            "notes": m["bquote_notes"],
        },
        businessCommit=[
            {
                "id": "cm1",
                "title": m["bcommit_title"],
                "body": m["bcommit_body"],
            }
        ],
    )


def _forbid_only_state(tag: str = "fbd") -> dict:
    """用途：仅禁止字段放唯一标记；允许字段不含任何禁止标记。"""
    f = {k: f"{v}_{tag}" if isinstance(v, str) else v for k, v in _FORBID_MARKERS.items()}
    return _state_with_version(
        mode=f["mode"] if isinstance(f["mode"], str) else "ALIGNED",
        outline={
            "id": f["outline_id"],
            "title": "普通标题无禁止标记",
            "description": "普通描述",
            "status": f["status"],
            "source": f["source"],
            "secretPath": f["unknown_nested"],
            "children": [
                {
                    "id": "c1",
                    "title": "子节点",
                    "description": "子描述",
                    "extra": f["unknown_nested"],
                    "count": f["numeric_leaf"],
                    "flag": f["bool_leaf"],
                    "children": [],
                }
            ],
        },
        chapters=[
            {
                "id": f["chapter_id"],
                "title": "章节无禁止",
                "preview": "预览无禁止",
                "body": "正文无禁止",
                "status": f["status"],
                "stateVersion": f["state_version"],
            }
        ],
        facts=[{"id": "fx", "text": f["facts"]}],
        analysis={
            "overview": f["analysis"],
            "techRequirements": [f["analysis"]],
            "rejectionRisks": [],
            "scoringPoints": [],
        },
        analysisOverview=f["analysis_overview"],
        responseMatrix=[
            {
                "id": "rm1",
                "requirement": f["response_matrix"],
                "outlineNodeIds": [f["outline_id"]],
                "chapterIds": [f["chapter_id"]],
            }
        ],
        guidance=f["guidance"],
        parsedMarkdown="普通 markdown 无禁止标记",
        businessQualify=[{"id": "x", "requirement": "普通", "response": "普通", "evidence": "普通"}],
        businessToc=[{"id": "t", "title": "普通", "category": "普通", "note": "普通"}],
        businessQuote={"rows": [], "notes": "普通报价备注"},
        businessCommit=[{"id": "c", "title": "普通", "body": "普通"}],
    )


def _insert_raw_revision(
    project_id: str,
    revision_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    source_kind: str,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            EditorStateRevisionRow(
                id=revision_id,
                workspace_id=workspace_id,
                project_id=project_id,
                snapshot_json=snapshot_json,
                state_version=state_version,
                snapshot_bytes=snapshot_bytes,
                source_kind=source_kind,
                created_at=created_at or utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_state(
    project_id: str,
    state: dict,
    *,
    source_kind: str = "task",
    created_at: datetime | None = None,
    workspace_id: str = _WS,
    revision_id: str | None = None,
) -> dict:
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    rid = revision_id or ("esr_" + secrets.token_hex(16))
    ver = state["stateVersion"]
    nbytes = len(snap_json.encode("utf-8"))
    _insert_raw_revision(
        project_id=project_id,
        revision_id=rid,
        snapshot_json=snap_json,
        state_version=ver,
        snapshot_bytes=nbytes,
        source_kind=source_kind,
        created_at=created_at,
        workspace_id=workspace_id,
    )
    return {
        "id": rid,
        "state_version": ver,
        "snapshot_bytes": nbytes,
        "source_kind": source_kind,
        "snapshot_json": snap_json,
        "created_at": created_at,
    }


def _seed_n(
    project_id: str,
    n: int,
    *,
    tag_prefix: str = "n",
    source_kind: str = "task",
    base_time: datetime | None = None,
    body_override: dict[int, dict] | None = None,
) -> list[dict]:
    """
    用途：插入 n 条修订；created_at 递增，返回 created_at DESC,id DESC 有序。
    body_override: 索引 i → 覆盖 state。
    """
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    for i in range(n):
        if body_override and i in body_override:
            state = body_override[i]
        else:
            state = _variant(f"{tag_prefix}{i:02d}")
        created = base + timedelta(seconds=i)
        rows.append(
            _seed_state(
                project_id,
                state,
                source_kind=source_kind,
                created_at=created,
            )
        )
    return _db_rev_rows(project_id)


def _raw_sql_update_revision(
    revision_id: str,
    *,
    ignore_check: bool = False,
    **fields: object,
) -> None:
    if not fields:
        raise ValueError("fields required")
    set_parts: list[str] = []
    params: dict[str, object] = {"rid": revision_id}
    for key, value in fields.items():
        set_parts.append(f"{key} = :{key}")
        params[key] = value
    sql = (
        "UPDATE editor_state_revisions SET "
        + ", ".join(set_parts)
        + " WHERE id = :rid"
    )
    with engine.begin() as conn:
        if ignore_check:
            conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        conn.execute(text(sql), params)
        if ignore_check:
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))


def _db_rev_rows(project_id: str, workspace_id: str | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(EditorStateRevisionRow).filter(
            EditorStateRevisionRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateRevisionRow.workspace_id == workspace_id)
        rows = list(
            q.order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            ).all()
        )
        return [
            {
                "id": r.id,
                "workspace_id": r.workspace_id,
                "project_id": r.project_id,
                "state_version": r.state_version,
                "snapshot_bytes": int(r.snapshot_bytes),
                "source_kind": r.source_kind,
                "created_at": r.created_at,
                "created_at_iso": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
                "snapshot_json": r.snapshot_json,
            }
            for r in rows
        ]
    finally:
        db.close()


def _db_cp_rows(project_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "state_version": r.state_version,
                "snapshot_bytes": int(r.snapshot_bytes),
                "snapshot_json": r.snapshot_json,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def _db_editor_state_row(project_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return {
            "project_id": row.project_id,
            "outline_json": row.outline_json,
            "chapters_json": row.chapters_json,
            "facts_json": row.facts_json,
            "mode": row.mode,
            "analysis_json": row.analysis_json,
            "response_matrix_json": row.response_matrix_json,
            "guidance_json": row.guidance_json,
            "parsed_markdown": row.parsed_markdown,
            "business_json": row.business_json,
            "analysis_overview": row.analysis_overview,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at is not None and hasattr(row.updated_at, "isoformat")
            else row.updated_at,
        }
    finally:
        db.close()


def _db_project_row(project_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(Project, project_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "industry": row.industry,
            "word_count": row.word_count,
            "technical_plan_step": row.technical_plan_step,
            "linked_project_id": row.linked_project_id,
            "source_opportunity_id": row.source_opportunity_id,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at is not None and hasattr(row.updated_at, "isoformat")
            else str(row.updated_at),
        }
    finally:
        db.close()


def _db_audit_rows() -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(AuthAuditEventRow)
            .order_by(
                AuthAuditEventRow.created_at.desc(),
                AuthAuditEventRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "actor_user_id": r.actor_user_id,
                "workspace_id": r.workspace_id,
                "action": r.action,
                "result": r.result,
                "target": r.target,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def _db_task_rows(project_id: str) -> list[dict]:
    """用途：按 project 排序的真实任务域快照。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .order_by(
                ProjectTaskRow.created_at.desc(),
                ProjectTaskRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "project_id": r.project_id,
                "type": r.type,
                "status": r.status,
                "progress": int(r.progress),
                "message": r.message,
                "payload_json": r.payload_json,
                "result_json": r.result_json,
                "error": r.error,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
                "updated_at": r.updated_at.isoformat()
                if hasattr(r.updated_at, "isoformat")
                else str(r.updated_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def _seed_synthetic_task(project_id: str, *, tag: str = "p12ffa") -> str:
    """用途：基线前插入至少一条合成任务，证明 tasks 域可观测且不被搜索改写。"""
    tid = f"task_p12ffa_{tag}_{secrets.token_hex(4)}"
    db = SessionLocal()
    try:
        db.add(
            ProjectTaskRow(
                id=tid,
                project_id=project_id,
                type="parse",
                status="pending",
                progress=0,
                message=f"synthetic-{tag}",
                payload_json=json.dumps({"tag": tag}, ensure_ascii=False),
                result_json=None,
                error=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    return tid


def _domain_snapshot(project_id: str) -> dict:
    return {
        "revisions": [
            {
                "id": r["id"],
                "workspace_id": r["workspace_id"],
                "project_id": r["project_id"],
                "state_version": r["state_version"],
                "snapshot_bytes": r["snapshot_bytes"],
                "source_kind": r["source_kind"],
                "created_at": r["created_at_iso"],
                "snapshot_json": r["snapshot_json"],
            }
            for r in _db_rev_rows(project_id)
        ],
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "tasks": _db_task_rows(project_id),
        "project": _db_project_row(project_id),
        "audits": _db_audit_rows(),
    }


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FFA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12ffa",
                )
            )
            db.commit()
    finally:
        db.close()


def _search(
    client: TestClient,
    project_id: str,
    body: dict,
    *,
    headers: dict | None = None,
):
    return client.post(
        _search_url(project_id),
        json=body,
        headers=headers or {},
    )


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(
    res,
    status: int,
    code: str,
    *,
    message: str | None = None,
    forbid_echo: str | None = None,
    forbid_parts: list[str] | None = None,
) -> None:
    assert res.status_code == status, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == {"detail"}, res.text
    detail = body.get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    if message is not None:
        assert detail.get("message") == message
    blob = res.text
    assert _SECRET not in blob
    assert _SECRET_EXTRA not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    assert "SELECT" not in blob
    assert "INSERT" not in blob
    assert "OFFSET" not in blob
    assert "editor_state_revisions" not in blob
    assert "editor_state_revision_history_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "JSONDecodeError" not in blob
    assert "ValidationError" not in blob
    if forbid_echo is not None and forbid_echo != "":
        assert forbid_echo not in blob
    if forbid_parts:
        for part in forbid_parts:
            if part is not None and part != "":
                assert part not in blob, f"不应回显: {part!r}"


def _assert_query_invalid(res, *, forbid_parts: list[str] | None = None) -> None:
    _assert_fixed_error(
        res,
        400,
        _CODE_QUERY_INVALID,
        message=_MSG_QUERY_INVALID,
        forbid_parts=forbid_parts,
    )
    body = res.json()
    assert "items" not in body


def _assert_request_invalid(res, *, forbid_parts: list[str] | None = None) -> None:
    """用途：缺 query/非法 JSON/非对象/额外键/snake_case 固定 422 脱敏。"""
    _assert_fixed_error(
        res,
        422,
        _CODE_REQUEST_INVALID,
        message=_MSG_REQUEST_INVALID,
        forbid_parts=forbid_parts,
    )
    body = res.json()
    assert "items" not in body
    blob = res.text
    # 禁止默认 Pydantic 字段名与校验类型泄漏
    assert '"loc"' not in blob
    assert "'loc'" not in blob
    assert '"input"' not in blob
    assert "'input'" not in blob
    assert '"type"' not in blob
    assert "'type'" not in blob
    assert '"url"' not in blob
    assert "ValidationError" not in blob


def _assert_auth_gate_error(
    res,
    status: int,
    code: str,
    message: str,
    *,
    forbid_parts: list[str] | None = None,
) -> None:
    """
    用途：中间件/依赖层角色与 CSRF 闸门；精确 status/code/message。
    说明：既有 auth 中间件错误响应不强制 Cache-Control:no-store，
      与 search 路由自有业务错误的 no-store 门禁分离。
    """
    assert res.status_code == status, res.text
    body = res.json()
    assert set(body.keys()) == {"detail"}, res.text
    detail = body["detail"]
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert detail.get("message") == message
    blob = res.text
    assert _SECRET not in blob
    assert _SECRET_EXTRA not in blob
    assert "Traceback" not in blob
    assert "ValidationError" not in blob
    if forbid_parts:
        for part in forbid_parts:
            if part is not None and part != "":
                assert part not in blob, f"不应回显: {part!r}"


def _assert_search_shape(body: dict, *, max_items: int = 20) -> None:
    assert set(body.keys()) == _SEARCH_TOP, body.keys()
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= max_items
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert _REVISION_ID_RE.fullmatch(item["revisionId"])
        assert _STATE_VERSION_RE.fullmatch(item["stateVersion"])
        assert isinstance(item["snapshotBytes"], int)
        assert item["sourceKind"] in _NINE_SOURCES
        assert "snapshot" not in item
        assert "nextCursor" not in item
        assert "matchedFields" not in item
        assert "snippet" not in item
        assert "score" not in item
        assert "query" not in item
        assert "projectId" not in item


def _assert_search_ok(res, *, max_items: int = 20) -> dict:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_search_shape(body, max_items=max_items)
    assert _SECRET not in res.text
    return body


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            return None
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_OWNER_USER,
            password=_OWNER_PASS,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str):
    client.cookies.clear()
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def _owner_session(client: TestClient):
    _bootstrap()
    res = _login(client, _OWNER_USER, _OWNER_PASS)
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"], res.json()


def _create_member(client, csrf, *, username, password, role, is_owner=False):
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _login_role(client: TestClient, role: str, *, is_owner: bool = False) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p12ffa{'_own' if is_owner else ''}"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
        is_owner=is_owner,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _parse_utc_ms_literal(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _as_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------- 1. 允许字段逐类命中 / NFKC / 空结果 / 去重倒序 ----------


def test_allow_fields_tech_and_business_each_hit(disabled_client):
    """用途：技术标与商务标全部允许字段各自唯一标记均可命中；响应精确五键。"""
    client = disabled_client
    pid = _create_project(client, name="允许字段全覆盖")
    state = _full_allow_state("A")
    row = _seed_state(pid, state, source_kind="task")
    # 噪声修订不含标记
    _seed_state(pid, _variant("noise"), source_kind="revise")

    for key, base_marker in _ALLOW_MARKERS.items():
        marker = f"{base_marker}_A"
        res = _search(client, pid, {"query": marker})
        body = _assert_search_ok(res)
        ids = [it["revisionId"] for it in body["items"]]
        assert ids == [row["id"]], f"字段 {key} 标记 {marker} 未精确命中: {ids}"
        assert set(body["items"][0].keys()) == _META_KEYS
        assert body["items"][0]["sourceKind"] == "task"
        assert marker not in res.text
        assert _SECRET not in res.text


def test_nfkc_casefold_literal_substring_and_empty(disabled_client):
    """用途：NFKC + casefold 连续字面；全角/大小写兼容；无匹配空 items。"""
    client = disabled_client
    pid = _create_project(client, name="NFKC匹配")
    # 正文含 "Cafe" 与全角数字
    state = _state_with_version(
        chapters=[
            {
                "id": "ch1",
                "title": "标题 Cafe 测试",
                "preview": "预览",
                "body": "包含全角１２３与 ASCII",
            }
        ],
        parsedMarkdown="Hello WORLD 混排",
    )
    row = _seed_state(pid, state)
    _seed_state(pid, _variant("other"))

    # casefold：cafe 命中 Cafe
    body = _assert_search_ok(_search(client, pid, {"query": "cafe"}))
    assert [it["revisionId"] for it in body["items"]] == [row["id"]]

    # NFKC：半角 123 命中全角 １２３
    fullwidth_query = unicodedata.normalize("NFKC", "１２３")
    # 查询用半角，正文是全角 → NFKC 后应命中
    body2 = _assert_search_ok(_search(client, pid, {"query": "123"}))
    assert [it["revisionId"] for it in body2["items"]] == [row["id"]]

    # 连续字面
    body3 = _assert_search_ok(_search(client, pid, {"query": "Hello WORLD"}))
    assert [it["revisionId"] for it in body3["items"]] == [row["id"]]

    # 空匹配不得回退为未筛选列表
    empty = _assert_search_ok(_search(client, pid, {"query": "NOMATCH_ZZZ_P12FFA"}))
    assert empty["items"] == []
    assert set(empty.keys()) == _SEARCH_TOP


def test_same_revision_dedup_and_fixed_desc_order(disabled_client):
    """用途：同修订多字段命中只返回一次；多修订固定 created_at DESC,id DESC。"""
    client = disabled_client
    pid = _create_project(client, name="去重倒序")
    base = datetime(2026, 7, 10, 0, 0, 0, tzinfo=timezone.utc)
    # 三修订均含共享标记
    shared = "DEDUP_SHARED_MARK_P12FFA"
    rows = []
    for i in range(3):
        st = _state_with_version(
            chapters=[
                {
                    "id": f"c{i}",
                    "title": f"{shared}-title-{i}",
                    "preview": f"{shared}-preview-{i}",
                    "body": f"{shared}-body-{i}",
                }
            ],
            parsedMarkdown=f"{shared}-md-{i}",
        )
        # 固定 id 保证同秒时 id 序
        rid = f"esr_{i:032x}"
        rows.append(
            _seed_state(
                pid,
                st,
                created_at=base + timedelta(seconds=i),
                revision_id=rid,
            )
        )
    res = _search(client, pid, {"query": shared})
    body = _assert_search_ok(res)
    ids = [it["revisionId"] for it in body["items"]]
    # 倒序：i=2,1,0
    assert ids == [f"esr_{i:032x}" for i in (2, 1, 0)]
    assert len(ids) == len(set(ids))


# ---------- 2. 禁止字段零命中 / 预算超限 ----------


def test_forbid_fields_each_miss_and_allow_control(disabled_client):
    """
    用途：禁止字段逐类放唯一标记 → 零命中；同项目允许标记对照 → 命中。
    反假绿：真实 revisionId/stateVersion/sourceKind/数值/布尔字面均不得命中。
    """
    client = disabled_client
    pid = _create_project(client, name="禁止字段白名单")
    fbd_state = _forbid_only_state("X")
    fbd_row = _seed_state(pid, fbd_state, source_kind="task")
    allow_state = _full_allow_state("CTRL")
    allow_row = _seed_state(pid, allow_state, source_kind="revise")

    # 禁止标记不得命中（即使存在于 snapshot）
    str_forbids = {
        k: f"{v}_X"
        for k, v in _FORBID_MARKERS.items()
        if isinstance(v, str)
    }
    for key, marker in str_forbids.items():
        res = _search(client, pid, {"query": marker})
        body = _assert_search_ok(res)
        assert body["items"] == [], f"禁止字段 {key} 不应命中: {body}"
        assert fbd_row["id"] not in res.text
        assert marker not in res.text

    # 真实元数据身份不得被搜索：revisionId / stateVersion / sourceKind 字面
    empty_id = _assert_search_ok(_search(client, pid, {"query": fbd_row["id"]}))
    assert empty_id["items"] == []
    empty_ver = _assert_search_ok(
        _search(client, pid, {"query": fbd_row["state_version"]})
    )
    assert empty_ver["items"] == []
    empty_src = _assert_search_ok(
        _search(client, pid, {"query": fbd_row["source_kind"]})
    )
    assert empty_src["items"] == []
    empty_src2 = _assert_search_ok(
        _search(client, pid, {"query": allow_row["source_kind"]})
    )
    assert empty_src2["items"] == []

    # 禁止数值/布尔叶子的文本形式不得命中
    empty_num = _assert_search_ok(_search(client, pid, {"query": "123456789"}))
    assert empty_num["items"] == []
    empty_bool = _assert_search_ok(_search(client, pid, {"query": "True"}))
    assert empty_bool["items"] == []

    # 同一入口允许字段对照精确命中
    ctrl_marker = f"{_ALLOW_MARKERS['chapter_title']}_CTRL"
    ok = _assert_search_ok(_search(client, pid, {"query": ctrl_marker}))
    assert [it["revisionId"] for it in ok["items"]] == [allow_row["id"]]

    # 未知嵌套键与异型叶子不得被“全树扫字符串”命中
    nested = _state_with_version(
        chapters=[
            {
                "id": "ch",
                "title": "正常",
                "preview": "正常",
                "body": "正常正文",
                "hiddenBlob": "HIDDEN_BLOB_P12FFA_NEVER",
                "meta": {"deep": "DEEP_HIDDEN_P12FFA"},
            }
        ]
    )
    _seed_state(pid, nested)
    empty1 = _assert_search_ok(
        _search(client, pid, {"query": "HIDDEN_BLOB_P12FFA_NEVER"})
    )
    assert empty1["items"] == []
    empty2 = _assert_search_ok(
        _search(client, pid, {"query": "DEEP_HIDDEN_P12FFA"})
    )
    assert empty2["items"] == []


def test_object_and_string_leaf_budget_exceed_corrupt(disabled_client):
    """
    用途：对象 4096 成功/4097 corrupt；字符串 8192 成功/8193 corrupt；
      quote 容器计入；超限即使用早期命中 query 也 corrupt。
    """
    client = disabled_client

    # 中和默认商务容器，避免污染对象/字符串预算
    _budget_neutral = {
        "businessQualify": [],
        "businessToc": [],
        "businessQuote": None,
        "businessCommit": [],
        "parsedMarkdown": None,
        "outline": None,
    }

    # --- 仅对象预算：零允许字符串，只带禁止 id ---
    pid_obj_ok = _create_project(client, name="对象预算4096成功")
    chapters_ok = [{"id": f"oid{i}"} for i in range(4096)]
    st_ok = _state_with_version(chapters=chapters_ok, **_budget_neutral)
    _seed_state(pid_obj_ok, st_ok, source_kind="task")
    res_ok = _search(client, pid_obj_ok, {"query": "NOMATCH_OBJ_BUDGET"})
    body_ok = _assert_search_ok(res_ok)
    assert body_ok["items"] == []

    pid_obj_bad = _create_project(client, name="对象预算4097损坏")
    # 首章放唯一允许 title；其余仅禁止 id。总允许字符串叶=1，对象精确 4097，
    # 不会先撞 8192 字符叶 / 2 MiB；用允许 title 查询仍须先完整对象计数 → corrupt。
    early_title = "OBJ_EARLY_HIT_P12FFA"
    chapters_bad = [{"id": f"oid{i}"} for i in range(4097)]
    chapters_bad[0] = {"id": "oid0", "title": early_title}
    assert len(chapters_bad) == 4097
    allowed_string_leaves = sum(
        1
        for c in chapters_bad
        for k in ("title", "preview", "body")
        if type(c.get(k)) is str
    )
    assert allowed_string_leaves == 1
    assert chapters_bad[0]["title"] == early_title
    # 对象 4097 先于字符串叶 8192 / 2 MiB 触发（本样本仅 1 个允许字符串叶）
    assert allowed_string_leaves < 8192
    st_bad = _state_with_version(chapters=chapters_bad, **_budget_neutral)
    _seed_state(pid_obj_bad, st_bad, source_kind="task")
    res_bad = _search(client, pid_obj_bad, {"query": early_title})
    _assert_fixed_error(
        res_bad, 500, _CODE_CORRUPT, message=_MSG_CORRUPT
    )
    assert "items" not in res_bad.json()
    assert early_title not in res_bad.text

    # --- 字符串叶预算：对象数 < 4096；精确 8192 成功 / 8193 corrupt ---
    # 2730 chapter * 3 叶 = 8190；+ parsedMarkdown + outline.title = 8192；对象 2731
    hit_marker = "STR_LEAF_HIT_P12FFA"
    pid_str_ok = _create_project(client, name="字符串叶8192成功")
    chapters_8190 = [
        {
            "id": f"s{i}",
            "title": hit_marker if i == 0 else f"st{i}",
            "preview": f"sp{i}",
            "body": f"sb{i}",
        }
        for i in range(2730)
    ]
    st_str_ok = _state_with_version(
        outline={"id": "o1", "title": "outline-title-only", "children": []},
        chapters=chapters_8190,
        parsedMarkdown="parsed-extra-leaf",
        businessQualify=[],
        businessToc=[],
        businessQuote=None,
        businessCommit=[],
    )
    row_str = _seed_state(pid_str_ok, st_str_ok)
    hit = _assert_search_ok(_search(client, pid_str_ok, {"query": hit_marker}))
    assert [it["revisionId"] for it in hit["items"]] == [row_str["id"]]

    pid_str_bad = _create_project(client, name="字符串叶8193损坏")
    chapters_8193 = [
        {
            "id": f"t{i}",
            "title": f"tt{i}",
            "preview": f"tp{i}",
            "body": f"tb{i}",
        }
        for i in range(2731)  # 2731*3=8193
    ]
    st_str_bad = _state_with_version(chapters=chapters_8193, **_budget_neutral)
    _seed_state(pid_str_bad, st_str_bad)
    res_str_bad = _search(client, pid_str_bad, {"query": "tt0"})
    _assert_fixed_error(
        res_str_bad, 500, _CODE_CORRUPT, message=_MSG_CORRUPT
    )

    # --- quote 容器计数：4095 空 rows + quote 容器 = 4096 成功；4096+1=4097 corrupt ---
    pid_q_ok = _create_project(client, name="quote容器4096成功")
    q_hit = "QUOTE_NOTES_HIT_P12FFA"
    st_q_ok = _state_with_version(
        chapters=None,
        businessQualify=[],
        businessToc=[],
        businessCommit=[],
        businessQuote={
            "rows": [{"id": f"qr{i}"} for i in range(4095)],
            "notes": q_hit,
        },
    )
    row_q = _seed_state(pid_q_ok, st_q_ok)
    hit_q = _assert_search_ok(_search(client, pid_q_ok, {"query": q_hit}))
    assert [it["revisionId"] for it in hit_q["items"]] == [row_q["id"]]

    pid_q_bad = _create_project(client, name="quote容器4097损坏")
    st_q_bad = _state_with_version(
        chapters=None,
        businessQualify=[],
        businessToc=[],
        businessCommit=[],
        businessQuote={
            "rows": [{"id": f"qr{i}"} for i in range(4096)],
            "notes": "should-not-reach",
        },
    )
    _seed_state(pid_q_bad, st_q_bad)
    res_q_bad = _search(client, pid_q_bad, {"query": "should-not-reach"})
    _assert_fixed_error(
        res_q_bad, 500, _CODE_CORRUPT, message=_MSG_CORRUPT
    )


# ---------- 3. 关键词规范 / 外壳 422 / 错误优先级 ----------


def test_query_invalid_matrix_no_echo(disabled_client):
    """用途：空白/控制字符/65 码点/非字符串全部精确 400 query_invalid，零反射。"""
    client = disabled_client
    pid = _create_project(client, name="关键词非法矩阵")
    _seed_state(pid, _variant("q0"))

    str_cases: list[str] = [
        "",
        "   ",
        "\t",
        "\n",
        "a\nb",
        "a\tb",
        "lead ",
        " trail",
        " a ",
        "\x00hidden",
        "x\x01y",
        "x\x1fy",
        "x\x7fy",
        "x\x9fy",
        "A" * 65,
    ]
    for bad in str_cases:
        res = _search(client, pid, {"query": bad})
        echo_parts: list[str] | None = None
        if (
            bad.strip() != ""
            and "\x00" not in bad
            and "\n" not in bad
            and "\t" not in bad
            and all(ord(ch) >= 0x20 for ch in bad)
        ):
            echo_parts = [bad]
        _assert_query_invalid(res, forbid_parts=echo_parts)

    # 非字符串 query：null/bool/int/float/list/dict 全部精确 400，对象/数组含秘密零反射
    non_str_cases: list[object] = [
        None,
        True,
        False,
        0,
        12,
        3.14,
        [],
        {},
        ["q", _SECRET],
        {"q": _SECRET},
    ]
    for bad in non_str_cases:
        res = _search(client, pid, {"query": bad})
        forbid: list[str] = [_SECRET]
        if bad is None:
            forbid.append("null")
        elif isinstance(bad, bool):
            forbid.append("true" if bad else "false")
            forbid.append(str(bad))
        elif isinstance(bad, (int, float)) and not isinstance(bad, bool):
            forbid.append(str(bad))
        _assert_query_invalid(res, forbid_parts=forbid)


def test_missing_and_extra_keys_422(disabled_client):
    """用途：缺 query/非法 JSON/非对象/额外键/snake_case 精确固定 422 脱敏。"""
    client = disabled_client
    pid = _create_project(client, name="外壳422")
    _seed_state(pid, _variant("e0"))

    # 缺 query
    r1 = client.post(_search_url(pid), json={})
    _assert_request_invalid(r1)

    # 非法 JSON
    r_bad_json = client.post(
        _search_url(pid),
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    _assert_request_invalid(r_bad_json)

    # 非对象
    r_arr = client.post(_search_url(pid), json=["query", _SECRET])
    _assert_request_invalid(r_arr, forbid_parts=[_SECRET])
    r_str = client.post(_search_url(pid), json=_SECRET)
    _assert_request_invalid(r_str, forbid_parts=[_SECRET])

    # 合法 query 与额外键值分别携两个不同秘密
    r2 = _search(
        client,
        pid,
        {
            "query": _SECRET,
            "cursor": _SECRET_EXTRA,
            "limit": 10,
            "offset": 0,
            "page": 1,
            "search": _SECRET_EXTRA,
            "q": _SECRET_EXTRA,
            "snippet": True,
            "extraSecret": _SECRET_EXTRA,
        },
    )
    _assert_request_invalid(r2, forbid_parts=[_SECRET, _SECRET_EXTRA])

    # snake_case 当作额外/未知
    r3 = _search(
        client,
        pid,
        {"query": _SECRET, "source_kind": "task"},
    )
    _assert_request_invalid(r3, forbid_parts=[_SECRET, "source_kind"])

    r4 = _search(
        client,
        pid,
        {
            "query": _SECRET,
            "created_from": _T_FROM,
            "created_before": _T_BEFORE,
        },
    )
    _assert_request_invalid(
        r4, forbid_parts=[_SECRET, "created_from", "created_before", _T_FROM]
    )


def test_error_priority_project_source_time_query(disabled_client):
    """
    用途：外壳成立后优先级：项目404 → 来源 → 时间 → 关键词；逐类精确 message。
    """
    client = disabled_client
    pid = _create_project(client, name="错误优先级")
    _seed_state(pid, _variant("prio"))

    # 不存在项目优先 404（即使关键词非法）
    missing = _search(
        client,
        "proj_not_exist_p12ffa",
        {"query": "  ", "sourceKind": "NOT_A_SOURCE"},
    )
    _assert_fixed_error(
        missing,
        404,
        _CODE_PROJECT_NOT_FOUND,
        message=_MSG_PROJECT_NOT_FOUND,
        forbid_parts=["NOT_A_SOURCE", "proj_not_exist_p12ffa"],
    )

    # 项目存在 + 非法来源 → source_invalid（优先于关键词/时间）
    bad_src = _search(
        client,
        pid,
        {
            "query": "  ",
            "sourceKind": "NOT_A_SOURCE",
            "createdFrom": "bad-time",
        },
    )
    _assert_fixed_error(
        bad_src,
        400,
        _CODE_SOURCE_INVALID,
        message=_MSG_SOURCE_INVALID,
        forbid_parts=["NOT_A_SOURCE", "bad-time"],
    )

    # 合法来源 + 非法时间 → time_range_invalid（优先于关键词）
    bad_time = _search(
        client,
        pid,
        {
            "query": "  ",
            "sourceKind": "task",
            "createdFrom": "not-a-time",
        },
    )
    _assert_fixed_error(
        bad_time,
        400,
        _CODE_TIME_RANGE_INVALID,
        message=_MSG_TIME_RANGE_INVALID,
        forbid_parts=["not-a-time"],
    )

    # 合法来源时间 + 非法关键词 → query_invalid
    bad_q = _search(
        client,
        pid,
        {
            "query": "  ",
            "sourceKind": "task",
            "createdFrom": _T_FROM,
            "createdBefore": _T_BEFORE,
        },
    )
    _assert_query_invalid(bad_q)


# ---------- 4. 来源 + 时间组合 / 20-21 候选窗 ----------


def test_source_and_time_range_combo_and_bounds(disabled_client):
    """用途：来源+单/双边时间组合；下界包含、上界排除。"""
    client = disabled_client
    pid = _create_project(client, name="来源时间组合")
    t0 = _parse_utc_ms_literal("2026-07-10T00:00:00.000Z")
    t1 = _parse_utc_ms_literal("2026-07-15T00:00:00.000Z")
    t2 = _parse_utc_ms_literal("2026-07-20T00:00:00.000Z")
    marker = "TIMECOMBO_MARK_P12FFA"

    def _mk(i: int) -> dict:
        return _state_with_version(
            chapters=[
                {
                    "id": f"c{i}",
                    "title": f"{marker}-{i}",
                    "preview": "p",
                    "body": "b",
                }
            ]
        )

    r0 = _seed_state(pid, _mk(0), source_kind="task", created_at=t0)
    r1 = _seed_state(pid, _mk(1), source_kind="task", created_at=t1)
    r2 = _seed_state(pid, _mk(2), source_kind="revise", created_at=t1)
    r3 = _seed_state(pid, _mk(3), source_kind="task", created_at=t2)

    # 仅来源 task
    body = _assert_search_ok(
        _search(client, pid, {"query": marker, "sourceKind": "task"})
    )
    assert [it["revisionId"] for it in body["items"]] == [
        r3["id"],
        r1["id"],
        r0["id"],
    ]

    # 双边：from=t1 包含，before=t2 排除 t2
    body2 = _assert_search_ok(
        _search(
            client,
            pid,
            {
                "query": marker,
                "sourceKind": "task",
                "createdFrom": "2026-07-15T00:00:00.000Z",
                "createdBefore": "2026-07-20T00:00:00.000Z",
            },
        )
    )
    assert [it["revisionId"] for it in body2["items"]] == [r1["id"]]

    # 仅 createdFrom
    body3 = _assert_search_ok(
        _search(
            client,
            pid,
            {
                "query": marker,
                "createdFrom": "2026-07-15T00:00:00.000Z",
            },
        )
    )
    got = {it["revisionId"] for it in body3["items"]}
    assert r1["id"] in got and r2["id"] in got and r3["id"] in got
    assert r0["id"] not in got

    # 仅 createdBefore
    body4 = _assert_search_ok(
        _search(
            client,
            pid,
            {
                "query": marker,
                "createdBefore": "2026-07-15T00:00:00.000Z",
            },
        )
    )
    assert [it["revisionId"] for it in body4["items"]] == [r0["id"]]

    # null 可选字段等价省略
    body5 = _assert_search_ok(
        _search(
            client,
            pid,
            {
                "query": marker,
                "sourceKind": None,
                "createdFrom": None,
                "createdBefore": None,
            },
        )
    )
    assert len(body5["items"]) == 4


def test_candidate_window_20_hits_21st_not_scanned(disabled_client):
    """用途：最新 20 候选窗；关键词只在第 20 条可命中，第 21 条不扫描。"""
    client = disabled_client
    pid = _create_project(client, name="候选窗20-21")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    mark20 = "WIN20_ONLY_P12FFA"
    mark21 = "WIN21_ONLY_P12FFA"

    # i=0 最旧 ... i=20 最新；DESC 后 index0=最新=i20，index19=第20新=i1，index20=第21=i0
    for i in range(21):
        if i == 1:
            # 第 20 新（从新往旧数第 20 个 = i=1）
            st = _state_with_version(
                chapters=[
                    {
                        "id": "c",
                        "title": mark20,
                        "preview": "p",
                        "body": "b",
                    }
                ]
            )
        elif i == 0:
            st = _state_with_version(
                chapters=[
                    {
                        "id": "c",
                        "title": mark21,
                        "preview": "p",
                        "body": "b",
                    }
                ]
            )
        else:
            st = _variant(f"w{i:02d}")
        _seed_state(
            pid,
            st,
            created_at=base + timedelta(seconds=i),
            revision_id=f"esr_{i:032x}",
        )

    ordered = _db_rev_rows(pid)
    assert len(ordered) == 21
    # ordered[0] 最新 i=20；ordered[19] = i=1；ordered[20] = i=0
    assert ordered[19]["id"] == f"esr_{1:032x}"
    assert ordered[20]["id"] == f"esr_{0:032x}"

    hit20 = _assert_search_ok(_search(client, pid, {"query": mark20}))
    assert [it["revisionId"] for it in hit20["items"]] == [ordered[19]["id"]]

    miss21 = _assert_search_ok(_search(client, pid, {"query": mark21}))
    assert miss21["items"] == []

    # 响应无 cursor/total/片段
    assert "nextCursor" not in hit20
    assert "total" not in hit20
    assert "snippet" not in hit20
    assert "matchedFields" not in hit20


# ---------- 5. SQL 六列 + 兼容 ----------


def test_search_sql_six_columns_limit_20_no_forbidden_constructs(disabled_client):
    """
    用途：搜索捕获期恰好一次 revision SELECT；精确六列+谓词+双键倒序+LIMIT20；
      项目存在性查询只投影 Project.id；禁 OFFSET/COUNT/LIKE/JSON。
    """
    client = disabled_client
    pid = _create_project(client, name="SQL六列搜索")
    _seed_n(pid, 5, tag_prefix="sql_", source_kind="task")
    _seed_n(
        pid,
        3,
        tag_prefix="sqln_",
        source_kind="revise",
        base_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low or "projects" in low:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _search(
            client,
            pid,
            {
                "query": "章节",
                "sourceKind": "task",
                "createdFrom": _T_FROM,
                "createdBefore": _T_BEFORE,
            },
        )
        assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    rev_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    # 反假绿：必须恰好一次 revision SELECT，禁止 N+1
    assert len(rev_selects) == 1, f"revision SELECT 次数异常: {len(rev_selects)} {rev_selects}"

    # P12F-H：搜索候选 SQL 七列（元数据六键对应五原列 + display_name + snapshot_json）
    _SEARCH_COLS = (
        "id",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
        "display_name",
        "snapshot_json",
    )

    def _param_list(parameters: object) -> list[object]:
        if isinstance(parameters, dict):
            return list(parameters.values())
        if isinstance(parameters, (list, tuple)):
            return list(parameters)
        return [parameters]

    def _normalize_cols(select_list: str) -> list[str]:
        raw_parts = [p.strip() for p in select_list.split(",")]
        normalized: list[str] = []
        for part in raw_parts:
            col = part.lower()
            if " as " in col:
                col = col.split(" as ", 1)[0].strip()
            if "." in col:
                col = col.rsplit(".", 1)[-1].strip()
            col = col.strip('`"[]')
            normalized.append(col)
        return normalized

    sql, params = rev_selects[0]
    compact = " ".join(sql.split())
    low = compact.lower()
    assert "count(" not in low
    assert re.search(r"\blike\b", low) is None, sql
    assert "json_extract" not in low
    assert "json_each" not in low
    # 禁非零 OFFSET；SQLite 方言可能把 limit(20) 编译为 LIMIT ? OFFSET ? 且末参为 0
    assert re.search(r"\boffset\s+[1-9]", low) is None, sql

    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
    assert match is not None
    normalized = _normalize_cols(match.group(1).strip())
    assert normalized == list(_SEARCH_COLS), normalized

    assert re.search(r"\bworkspace_id\s*=", low)
    assert re.search(r"\bproject_id\s*=", low)
    assert re.search(r"\bsource_kind\s*=", low)
    assert re.search(r"\bcreated_at\s*>=", low)
    assert re.search(r"\bcreated_at\s*<", low)
    assert re.search(
        r"order\s+by\s+.*created_at\s+desc.*,\s*.*id\s+desc", low
    ), compact

    vals = _param_list(params)
    if re.search(r"\blimit\s+20\b", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-1] == 0, params
    elif re.search(r"\blimit\s+\?", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-2:] == [20, 0], params
        else:
            assert vals[-1] == 20, params
    else:
        raise AssertionError(f"未发现 LIMIT 20: {compact} {params}")

    assert "task" in vals
    assert pid in vals
    assert _WS in vals

    # 项目存在性：恰好一次 SELECT，只投影 id，且 workspace/project 双谓词
    proj_selects = [
        (s, p)
        for s, p in captured
        if re.search(r"\bprojects\b", s, re.I)
        and s.lstrip().upper().startswith("SELECT")
        and "editor_state_revisions" not in s.lower()
    ]
    assert len(proj_selects) == 1, (
        f"projects SELECT 次数异常: {len(proj_selects)} {proj_selects}"
    )
    psql, pparams = proj_selects[0]
    pcompact = " ".join(psql.split())
    plow = pcompact.lower()
    pmatch = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", pcompact)
    assert pmatch is not None
    pcols = _normalize_cols(pmatch.group(1).strip())
    assert pcols == ["id"], f"项目查询必须只投影 id: {pcols} / {pcompact}"
    assert re.search(r"\bworkspace_id\s*=", plow), pcompact
    assert re.search(r"\bid\s*=", plow), pcompact
    pvals = _param_list(pparams)
    assert pid in pvals, pparams
    assert _WS in pvals, pparams

    # 源码禁区
    svc_src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert "func.count" not in svc_src


def test_old_list_page_compat_unknown_search_q_ignored(disabled_client):
    """用途：旧 list/page 五列/10+1 与未知 search/q 兼容不变。"""
    client = disabled_client
    pid = _create_project(client, name="GET兼容")
    ordered = _seed_n(pid, 12, tag_prefix="cmp_")

    listed = client.get(_list_url(pid))
    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    body = listed.json()
    assert set(body.keys()) == _LIST_TOP
    assert len(body["items"]) == 10
    assert [it["revisionId"] for it in body["items"]] == [
        r["id"] for r in ordered[:10]
    ]
    for it in body["items"]:
        assert set(it.keys()) == _META_KEYS
        assert "snapshot" not in it

    tampered_list = client.get(
        _list_url(pid),
        params={"search": _SECRET, "q": "章节", "limit": 100, "sourceKind": "task"},
    )
    assert tampered_list.status_code == 200
    assert tampered_list.json() == body

    page = client.get(_page_url(pid))
    assert page.status_code == 200
    pbody = page.json()
    assert set(pbody.keys()) == _PAGE_TOP
    assert len(pbody["items"]) == 10
    assert pbody["nextCursor"] is not None

    tampered_page = client.get(
        _page_url(pid),
        params={"search": _SECRET, "q": "正文", "limit": 1, "offset": 3},
    )
    assert tampered_page.status_code == 200
    assert tampered_page.json() == pbody


# ---------- 6. 损坏 / 跨作用域 ----------


def test_candidate_corrupt_fails_whole_search_even_if_query_miss(disabled_client):
    """用途：候选窗坏行即使用不命中关键词仍整次 corrupt；不泄漏。"""
    client = disabled_client
    pid = _create_project(client, name="候选损坏")
    ordered = _seed_n(pid, 5, tag_prefix="cr_")
    bad_id = ordered[2]["id"]
    _raw_sql_update_revision(bad_id, state_version="not_a_valid_esv")

    res = _search(client, pid, {"query": "NOMATCH_CORRUPT_ZZZ"})
    _assert_fixed_error(
        res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT, forbid_parts=[bad_id]
    )
    assert "items" not in res.json()
    assert _SECRET not in res.text
    assert "not_a_valid_esv" not in res.text


def test_corrupt_json_and_noncanonical_and_bytes_mismatch(disabled_client):
    client = disabled_client
    cases = [
        ("坏JSON", {"snapshot_json": "{not-json"}, "badjson"),
        (
            "非13键",
            {
                "snapshot_json": json.dumps(
                    {"outline": None, "chapters": None},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
            "badkeys",
        ),
    ]
    for name, fields, tag in cases:
        pid = _create_project(client, name=name)
        ordered = _seed_n(pid, 2, tag_prefix=tag)
        _raw_sql_update_revision(ordered[0]["id"], ignore_check=True, **fields)
        res = _search(client, pid, {"query": "章节"})
        _assert_fixed_error(
            res,
            500,
            _CODE_CORRUPT,
            message=_MSG_CORRUPT,
            forbid_parts=[ordered[0]["id"]],
        )

    # 字节不符
    pid3 = _create_project(client, name="字节不符")
    ordered3 = _seed_n(pid3, 2, tag_prefix="by_")
    _raw_sql_update_revision(ordered3[0]["id"], snapshot_bytes=1)
    res3 = _search(client, pid3, {"query": "章节"})
    _assert_fixed_error(res3, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)


def test_cross_workspace_project_no_leak(disabled_client):
    client = disabled_client
    _ensure_workspace(_WS_OTHER)
    a = _create_project(client, name="空间A")
    marker = "CROSS_WS_MARK_P12FFA"
    st = _state_with_version(
        chapters=[{"id": "c", "title": marker, "preview": "p", "body": "b"}]
    )
    row_a = _seed_state(a, st, workspace_id=_WS)

    # 在其他空间建项目与同标记修订
    db = SessionLocal()
    try:
        other_pid = "proj_other_p12ffa_" + secrets.token_hex(4)
        db.add(
            Project(
                id=other_pid,
                workspace_id=_WS_OTHER,
                name="其他项目",
                kind="technical",
                status="draft",
            )
        )
        db.commit()
    finally:
        db.close()
    row_b = _seed_state(
        other_pid, st, workspace_id=_WS_OTHER, source_kind="task"
    )

    before_a = _domain_snapshot(a)

    ok_res = _search(client, a, {"query": marker})
    ok = _assert_search_ok(ok_res)
    assert [it["revisionId"] for it in ok["items"]] == [row_a["id"]]
    assert row_b["id"] not in ok_res.text
    assert row_b["id"] not in json.dumps(ok)

    cross = _search(
        client,
        a,
        {"query": marker},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(
        cross,
        404,
        _CODE_PROJECT_NOT_FOUND,
        message=_MSG_PROJECT_NOT_FOUND,
        forbid_parts=[row_a["id"], row_b["id"], a, other_pid],
    )
    assert row_a["id"] not in cross.text
    assert row_b["id"] not in cross.text

    # 他空间自己的项目可搜到
    other_ok = _search(
        client,
        other_pid,
        {"query": marker},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    body_o = _assert_search_ok(other_ok)
    assert [it["revisionId"] for it in body_o["items"]] == [row_b["id"]]
    assert row_a["id"] not in other_ok.text

    assert _domain_snapshot(a) == before_a


# ---------- 7. 认证 CSRF / 五域零写 / AST ----------


def test_required_bid_writer_csrf_and_role_gates(required_client):
    """用途：required 仅 bid_writer+Cookie+CSRF；角色/CSRF 精确 403 固定码。"""
    client = required_client
    for role in ("finance", "hr", "bidder"):
        csrf = _login_role(client, role)
        res = _search(
            client,
            "any",
            {"query": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        _assert_auth_gate_error(
            res,
            403,
            _CODE_ROLE_FORBIDDEN,
            _MSG_ROLE_FORBIDDEN,
        )

    csrf_own = _login_role(client, "finance", is_owner=True)
    res_own = _search(
        client,
        "any",
        {"query": "x"},
        headers={"X-CSRF-Token": csrf_own},
    )
    _assert_auth_gate_error(
        res_own,
        403,
        _CODE_ROLE_FORBIDDEN,
        _MSG_ROLE_FORBIDDEN,
    )

    csrf_w = _login_role(client, "bid_writer")
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert set(me_body.keys()) >= {"user", "activeWorkspaceId", "workspaces"}
    writer_user_id = me_body["user"]["id"]
    assert type(writer_user_id) is str and writer_user_id != ""
    assert "password" not in json.dumps(me_body, ensure_ascii=False)
    headers = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="required搜索", headers=headers)
    marker = "AUTH_HIT_P12FFA"
    st = _state_with_version(
        chapters=[{"id": "c", "title": marker, "preview": "p", "body": "b"}]
    )
    row = _seed_state(pid, st)
    _seed_synthetic_task(pid, tag="auth")

    # 缺 CSRF：精确 403 csrf_invalid；安全审计基线后只新增 1 条
    audits_before = _db_audit_rows()
    no_csrf = client.post(_search_url(pid), json={"query": marker})
    _assert_auth_gate_error(
        no_csrf,
        403,
        _CODE_CSRF_INVALID,
        _MSG_CSRF_INVALID,
        forbid_parts=[marker, _SECRET],
    )
    audits_after_missing = _db_audit_rows()
    before_ids = {a["id"] for a in audits_before}
    new_only = [a for a in audits_after_missing if a["id"] not in before_ids]
    assert len(new_only) == 1, new_only
    audit = new_only[0]
    assert audit["action"] == "csrf_check"
    assert audit["result"] == "invalid"
    assert audit["target"] == "POST"
    assert audit["workspace_id"] == _WS
    assert audit["actor_user_id"] == writer_user_id
    assert marker not in json.dumps(audit, ensure_ascii=False)
    assert _SECRET not in json.dumps(audit, ensure_ascii=False)

    # 错 CSRF
    bad_csrf = client.post(
        _search_url(pid),
        json={"query": marker},
        headers={"X-CSRF-Token": "wrong-csrf-token-p12ffa"},
    )
    _assert_auth_gate_error(
        bad_csrf,
        403,
        _CODE_CSRF_INVALID,
        _MSG_CSRF_INVALID,
        forbid_parts=["wrong-csrf-token-p12ffa", marker],
    )

    # 合法 required 搜索成功且不再增加审计
    audits_mid = _db_audit_rows()
    domain_before = _domain_snapshot(pid)
    ok = _search(client, pid, {"query": marker}, headers=headers)
    body = _assert_search_ok(ok)
    assert [it["revisionId"] for it in body["items"]] == [row["id"]]
    assert _db_audit_rows() == audits_mid
    assert _domain_snapshot(pid) == domain_before


def test_disabled_mode_search_ok(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="disabled搜索")
    marker = "DISABLED_HIT_P12FFA"
    st = _state_with_version(
        chapters=[{"id": "c", "title": marker, "preview": "p", "body": "b"}]
    )
    row = _seed_state(pid, st)
    body = _assert_search_ok(_search(client, pid, {"query": marker}))
    assert [it["revisionId"] for it in body["items"]] == [row["id"]]


def test_search_five_domain_zero_write_disabled(disabled_client):
    """用途：disabled 下成功/source/time/query/corrupt 业务错误五域（含 tasks）零写。"""
    client = disabled_client
    pid = _create_project(client, name="搜索五域零写")
    _seed_n(pid, 3, tag_prefix="zw_")
    _seed_synthetic_task(pid, tag="zw")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    before = _domain_snapshot(pid)
    assert before["tasks"], "基线必须含合成任务"

    ok = _search(client, pid, {"query": "章节"})
    assert ok.status_code == 200, ok.text
    assert _domain_snapshot(pid) == before

    bad_q = _search(client, pid, {"query": "  "})
    _assert_query_invalid(bad_q)
    assert _domain_snapshot(pid) == before

    bad_src = _search(client, pid, {"query": "章节", "sourceKind": "BAD"})
    _assert_fixed_error(
        bad_src, 400, _CODE_SOURCE_INVALID, message=_MSG_SOURCE_INVALID
    )
    assert _domain_snapshot(pid) == before

    bad_time = _search(
        client,
        pid,
        {"query": "章节", "createdFrom": "not-a-time"},
    )
    _assert_fixed_error(
        bad_time,
        400,
        _CODE_TIME_RANGE_INVALID,
        message=_MSG_TIME_RANGE_INVALID,
        forbid_parts=["not-a-time"],
    )
    assert _domain_snapshot(pid) == before

    # corrupt 路径也零写
    ordered = _db_rev_rows(pid)
    _raw_sql_update_revision(ordered[0]["id"], state_version="not_a_valid_esv")
    before_corrupt = _domain_snapshot(pid)
    bad_corrupt = _search(client, pid, {"query": "章节"})
    _assert_fixed_error(
        bad_corrupt, 500, _CODE_CORRUPT, message=_MSG_CORRUPT
    )
    assert _domain_snapshot(pid) == before_corrupt


def test_search_csrf_failure_keeps_security_audit_not_search_write(required_client):
    """用途：缺 CSRF 仅新增 1 条安全审计；合法搜索不改业务五域且审计不增。"""
    rclient = required_client
    csrf_w = _login_role(rclient, "bid_writer")
    me = rclient.get("/api/auth/me")
    assert me.status_code == 200, me.text
    writer_user_id = me.json()["user"]["id"]
    assert type(writer_user_id) is str and writer_user_id != ""
    headers = {"X-CSRF-Token": csrf_w}
    pid2 = _create_project(rclient, name="CSRF审计拆分", headers=headers)
    _seed_state(pid2, _variant("csrf"))
    _seed_synthetic_task(pid2, tag="csrf")
    audits_before = _db_audit_rows()
    before_ids = {a["id"] for a in audits_before}
    no_csrf = rclient.post(_search_url(pid2), json={"query": "章节"})
    _assert_auth_gate_error(
        no_csrf, 403, _CODE_CSRF_INVALID, _MSG_CSRF_INVALID
    )
    audits_after = _db_audit_rows()
    new_only = [a for a in audits_after if a["id"] not in before_ids]
    assert len(new_only) == 1, new_only
    assert new_only[0]["action"] == "csrf_check"
    assert new_only[0]["result"] == "invalid"
    assert new_only[0]["target"] == "POST"
    assert new_only[0]["workspace_id"] == _WS
    assert new_only[0]["actor_user_id"] == writer_user_id

    domain_before = _domain_snapshot(pid2)
    audits_mid = _db_audit_rows()
    ok2 = _search(rclient, pid2, {"query": "章节"}, headers=headers)
    assert ok2.status_code == 200, ok2.text
    after = _domain_snapshot(pid2)
    assert after["revisions"] == domain_before["revisions"]
    assert after["checkpoints"] == domain_before["checkpoints"]
    assert after["editor_state"] == domain_before["editor_state"]
    assert after["tasks"] == domain_before["tasks"]
    assert after["project"] == domain_before["project"]
    assert _db_audit_rows() == audits_mid


def test_search_service_api_ast_and_source_bans():
    """用途：实现落地后 AST/源码禁写与搜索路径约束；红测阶段允许函数尚不存在。"""
    # 若生产文件尚未实现 search，跳过硬断言函数名，但文件须可 parse
    for path in (_SERVICE_PATH, _API_PATH, _SCHEMA_PATH):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned_calls = {
            "commit",
            "rollback",
            "flush",
            "refresh",
            "with_for_update",
            "record_editor_state_transition",
            "create_editor_state_checkpoint",
            "restore_editor_state_checkpoint",
            "upsert_editor_state",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in banned_calls:
                    # history service / api 既有只读文件本就无写；实现后仍须保持
                    pytest.fail(f"{path.name} 禁止调用 {name}")

    svc_src = _SERVICE_PATH.read_text(encoding="utf-8")
    # 禁止 SQL LIKE / JSON 抽取用于搜索
    assert "json_extract" not in svc_src.lower()
    assert ".like(" not in svc_src
    # 禁止递归全树字符串收集模式（def 名暗示）
    assert "collect_all_strings" not in svc_src

    # 搜索路径函数内禁止 regex；不误伤范围外既有 revision ID/cursor 正则
    search_fn_names = {
        "_normalize_search_query",
        "_fold_for_search",
        "_extract_allowed_search_strings",
        "_snapshot_matches_query",
        "list_editor_state_revision_search",
    }
    regex_attrs = {
        "compile",
        "search",
        "match",
        "fullmatch",
        "findall",
        "finditer",
        "sub",
        "subn",
    }
    svc_tree = ast.parse(svc_src)
    found_fns: dict[str, ast.AST] = {}
    for node in svc_tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in search_fn_names
        ):
            found_fns[node.name] = node
    assert set(found_fns) == search_fn_names, f"搜索函数缺失: {search_fn_names - set(found_fns)}"
    for fname, fn in found_fns.items():
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr in regex_attrs:
                base = func.value
                if isinstance(base, ast.Name) and base.id == "re":
                    raise AssertionError(
                        f"{fname} 禁止调用 re.{func.attr}"
                    )
                # 等价 regex：pattern.fullmatch / .match 等
                raise AssertionError(
                    f"{fname} 禁止等价 regex 调用 .{func.attr}"
                )
            if isinstance(func, ast.Name) and func.id in regex_attrs:
                raise AssertionError(
                    f"{fname} 禁止直接调用 regex 函数 {func.id}"
                )


def test_response_never_echoes_query_or_body(disabled_client):
    """用途：成功/错误响应与日志路径均不回显关键词/正文。"""
    client = disabled_client
    pid = _create_project(client, name="零回显")
    secret_q = "QSECRET_NEVER_ECHO_P12FFA"
    st = _state_with_version(
        chapters=[
            {
                "id": "c",
                "title": secret_q,
                "preview": _SECRET,
                "body": _SECRET,
            }
        ]
    )
    _seed_state(pid, st)
    res = _search(client, pid, {"query": secret_q})
    body = _assert_search_ok(res)
    assert secret_q not in res.text
    assert _SECRET not in res.text
    assert "query" not in body
    assert body["items"], "应命中以证明成功路径"


def test_heterogeneous_structure_allow_hit_and_unknown_miss(disabled_client):
    """
    用途：outline 根数组真实命中；各允许数组混入非对象项/未知嵌套字符串时，
      允许对象仍命中、异型/未知标记精确不命中。
    """
    client = disabled_client
    pid = _create_project(client, name="异型结构")
    allow_out = "HETERO_OUTLINE_ARR_P12FFA"
    allow_ch = "HETERO_CHAPTER_P12FFA"
    allow_bq = "HETERO_QUALIFY_P12FFA"
    allow_toc = "HETERO_TOC_P12FFA"
    allow_row = "HETERO_QUOTE_ROW_P12FFA"
    allow_cm = "HETERO_COMMIT_P12FFA"
    bad_out = "HETERO_BAD_OUT_STR_P12FFA"
    bad_ch = "HETERO_BAD_CH_STR_P12FFA"
    bad_nested = "HETERO_UNKNOWN_NEST_P12FFA"
    bad_child = "HETERO_CHILD_STR_P12FFA"
    bad_row = "HETERO_ROW_STR_P12FFA"
    bad_ch_item = "HETERO_CH_ITEM_STR_P12FFA"

    st = _state_with_version(
        outline=[
            bad_out,
            123,
            None,
            {
                "id": "o1",
                "title": allow_out,
                "description": "desc",
                "children": [
                    bad_child,
                    {
                        "id": "oc",
                        "title": "child-ok",
                        "description": "cd",
                        "secret": bad_nested,
                        "children": [],
                    },
                ],
            },
        ],
        chapters=[
            bad_ch_item,
            {
                "id": "c1",
                "title": allow_ch,
                "preview": "p",
                "body": "b",
                "hidden": bad_ch,
            },
            99,
        ],
        businessQualify=[
            None,
            {
                "id": "q1",
                "requirement": allow_bq,
                "response": "r",
                "evidence": "e",
                "extra": bad_nested,
            },
            "str-item",
        ],
        businessToc=[
            1,
            {
                "id": "t1",
                "title": allow_toc,
                "category": "c",
                "note": "n",
            },
        ],
        businessQuote={
            "rows": [
                bad_row,
                {
                    "id": "r1",
                    "name": allow_row,
                    "unit": "u",
                    "quantity": "1",
                    "unitPrice": "2",
                    "amount": "3",
                    "remark": "rm",
                    "meta": {"x": bad_nested},
                },
            ],
            "notes": "notes-plain",
        },
        businessCommit=[
            False,
            {
                "id": "cm1",
                "title": allow_cm,
                "body": "body",
            },
        ],
    )
    row = _seed_state(pid, st)

    for marker in (
        allow_out,
        allow_ch,
        allow_bq,
        allow_toc,
        allow_row,
        allow_cm,
    ):
        body = _assert_search_ok(_search(client, pid, {"query": marker}))
        assert [it["revisionId"] for it in body["items"]] == [row["id"]], marker
        assert marker not in body and marker not in str(body)

    for bad in (
        bad_out,
        bad_ch,
        bad_nested,
        bad_child,
        bad_ch_item,
        bad_row,
        "str-item",
    ):
        empty = _assert_search_ok(_search(client, pid, {"query": bad}))
        assert empty["items"] == [], bad


# ---------- 最终路由锚点（实现后仅接受精确 200） ----------


def test_search_route_exists_exact_success(disabled_client):
    """
    用途：实现后最终锚点——POST search 精确 200、命中预期 revisionId、精确五键。
    说明：原 failure-first 首个真实业务失败 405 仅保留在 review_request 历史证据，
      本测试不得再接受缺失能力分支。
    """
    client = disabled_client
    pid = _create_project(client, name="路由锚点")
    row = _seed_state(pid, _variant("route"))
    res = _search(client, pid, {"query": "章节route"})
    body = _assert_search_ok(res)
    assert [it["revisionId"] for it in body["items"]] == [row["id"]]
    assert set(body["items"][0].keys()) == _META_KEYS


# ---------- P12F-I 名称与可见内容联合搜索 ----------


def _set_display_name(revision_id: str, value: object) -> None:
    """用途：绕过命名服务，直接写入库内 display_name 供联合搜索夹具。"""
    _raw_sql_update_revision(revision_id, display_name=value)


def _plain_state(tag: str) -> dict:
    """用途：章节标题不含特殊搜索标记，仅供名称唯一命中夹具。"""
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"普通章节{tag}",
                "preview": f"普通预览{tag}",
                "body": f"普通正文{tag}",
            }
        ],
        parsedMarkdown=f"普通md-{tag}",
    )


def test_p12fi_name_only_hit_returns_exact_single_meta(disabled_client):
    """
    用途：合法非空 display_name 是唯一命中字段时精确返回该行。
    failure-first：实现前期望 1、实际 0 的真实业务失败。
    """
    client = disabled_client
    pid = _create_project(client, name="P12FI名称唯一命中")
    name_only = "P12FI_NAME_ONLY_HIT_α"
    content_marker = "P12FI_CONTENT_OTHER_β"
    row_name = _seed_state(
        pid,
        _plain_state("n1"),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "a" * 32,
    )
    _set_display_name(row_name["id"], name_only)
    row_content = _seed_state(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": content_marker,
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "b" * 32,
    )
    # 第三条 null 名称且无内容标记
    _seed_state(
        pid,
        _plain_state("n3"),
        created_at=datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "c" * 32,
    )

    before = _domain_snapshot(pid)
    res = _search(client, pid, {"query": name_only})
    body = _assert_search_ok(res)
    ids = [it["revisionId"] for it in body["items"]]
    assert ids == [row_name["id"]], (
        f"名称唯一命中必须恰好 1 条且为名称行: 期望 {[row_name['id']]} 实际 {ids}"
    )
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item.keys()) == _META_KEYS
    assert item["displayName"] == name_only
    assert item["stateVersion"] == row_name["state_version"]
    assert item["sourceKind"] == "task"
    assert row_content["id"] not in ids
    # 成功路径允许结果 displayName 合法文本；禁止片段/分数/匹配字段扩展
    assert "matchedFields" not in body
    assert "snippet" not in body
    assert "score" not in body
    assert "query" not in body
    assert _domain_snapshot(pid) == before


def test_p12fi_name_content_union_dedup_and_desc_order(disabled_client):
    """
    用途：名称+内容双命中同一修订只返一次；多行分别命中时严格 created_at DESC,id DESC。
    """
    client = disabled_client
    pid = _create_project(client, name="P12FI并集去重")
    shared = "P12FI_UNION_SHARED"
    base = datetime(2026, 7, 18, 8, 0, 0, tzinfo=timezone.utc)

    # r0 最旧：仅内容命中
    r0 = _seed_state(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c", "title": f"t-{shared}", "preview": "p", "body": "b"}
            ]
        ),
        created_at=base + timedelta(seconds=0),
        revision_id="esr_" + "d0" + "0" * 30,
    )
    # r1：名称+内容双命中
    r1 = _seed_state(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c", "title": f"body-{shared}", "preview": "p", "body": "b"}
            ]
        ),
        created_at=base + timedelta(seconds=1),
        revision_id="esr_" + "d1" + "0" * 30,
    )
    _set_display_name(r1["id"], f"name-{shared}")
    # r2 最新：仅名称命中
    r2 = _seed_state(
        pid,
        _plain_state("u2"),
        created_at=base + timedelta(seconds=2),
        revision_id="esr_" + "d2" + "0" * 30,
    )
    _set_display_name(r2["id"], f"only-{shared}")
    # 干扰：名称与内容均不命中
    r3 = _seed_state(
        pid,
        _plain_state("u3"),
        created_at=base + timedelta(seconds=3),
        revision_id="esr_" + "d3" + "0" * 30,
    )
    _set_display_name(r3["id"], "NOMATCH_NAME_P12FI")

    body = _assert_search_ok(_search(client, pid, {"query": shared}))
    ids = [it["revisionId"] for it in body["items"]]
    # 候选倒序：r2(name), r1(both once), r0(content)；r3 不入选
    assert ids == [r2["id"], r1["id"], r0["id"]]
    assert len(ids) == len(set(ids))
    assert r3["id"] not in ids
    # 六键与 displayName
    by_id = {it["revisionId"]: it for it in body["items"]}
    assert by_id[r2["id"]]["displayName"] == f"only-{shared}"
    assert by_id[r1["id"]]["displayName"] == f"name-{shared}"
    assert by_id[r0["id"]]["displayName"] is None
    for it in body["items"]:
        assert set(it.keys()) == _META_KEYS


def test_p12fi_null_and_nonmatch_name_keep_content_and_nfkc(disabled_client):
    """
    用途：null/非命中名称不改变内容匹配；名称侧 NFKC+casefold 连续字面子串。
    """
    client = disabled_client
    pid = _create_project(client, name="P12FI空名与Unicode")
    content_hit = "P12FI_CONTENT_KEEP"
    # null 名称 + 内容命中
    r_null = _seed_state(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": content_hit,
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 9, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "e0" + "0" * 30,
    )
    # 非命中名称 + 内容命中
    r_nm = _seed_state(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": f"x-{content_hit}",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 9, 1, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "e1" + "0" * 30,
    )
    _set_display_name(r_nm["id"], "OtherNameNoHit")

    body_c = _assert_search_ok(_search(client, pid, {"query": content_hit}))
    assert [it["revisionId"] for it in body_c["items"]] == [r_nm["id"], r_null["id"]]

    # 名称 Unicode：库内已是 NFKC 规范的 "CafeＡ" 风格 → 用 cafea 命中
    # 注意：库内 display_name 必须等于 NFKC(自身)；全角 Ａ 的 NFKC 是半角 A
    name_fw = unicodedata.normalize("NFKC", "CafeＡ")
    assert name_fw == "CafeA"
    r_uni = _seed_state(
        pid,
        _plain_state("uni"),
        created_at=datetime(2026, 7, 18, 9, 2, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "e2" + "0" * 30,
    )
    _set_display_name(r_uni["id"], name_fw)
    body_u = _assert_search_ok(_search(client, pid, {"query": "cafea"}))
    assert [it["revisionId"] for it in body_u["items"]] == [r_uni["id"]]
    assert body_u["items"][0]["displayName"] == name_fw

    # 大小写：HelloName 用 helloname 命中
    r_case = _seed_state(
        pid,
        _plain_state("case"),
        created_at=datetime(2026, 7, 18, 9, 3, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "e3" + "0" * 30,
    )
    _set_display_name(r_case["id"], "HelloName")
    body_case = _assert_search_ok(_search(client, pid, {"query": "helloname"}))
    assert [it["revisionId"] for it in body_case["items"]] == [r_case["id"]]


def test_p12fi_name_candidate_window_20_skips_21st(disabled_client):
    """用途：固定只扫最新 20 条；第 21 条即使名称命中也不返回、不补扫。"""
    client = disabled_client
    pid = _create_project(client, name="P12FI名称窗20-21")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    mark20 = "WIN20_NAME_P12FI"
    mark21 = "WIN21_NAME_P12FI"
    # i=0 最旧 ... i=20 最新
    for i in range(21):
        row = _seed_state(
            pid,
            _plain_state(f"w{i:02d}"),
            created_at=base + timedelta(seconds=i),
            revision_id=f"esr_{i:032x}",
        )
        if i == 1:
            # 从新往旧第 20 条
            _set_display_name(row["id"], mark20)
        elif i == 0:
            _set_display_name(row["id"], mark21)

    ordered = _db_rev_rows(pid)
    assert len(ordered) == 21
    assert ordered[19]["id"] == f"esr_{1:032x}"
    assert ordered[20]["id"] == f"esr_{0:032x}"

    hit20 = _assert_search_ok(_search(client, pid, {"query": mark20}))
    assert [it["revisionId"] for it in hit20["items"]] == [ordered[19]["id"]]
    assert hit20["items"][0]["displayName"] == mark20

    miss21 = _assert_search_ok(_search(client, pid, {"query": mark21}))
    assert miss21["items"] == []
    assert "nextCursor" not in hit20
    assert "total" not in hit20


def test_p12fi_name_would_hit_but_corrupt_or_budget_still_whole_fail(disabled_client):
    """
    用途：名称已可命中时，坏 snapshot/meta/display_name 或提取预算超限仍整次 corrupt；
      证明先完整验证全部候选、后联合匹配，不得名称短路。
    """
    client = disabled_client
    needle = "P12FI_CORRUPT_NAME"

    # 1) 坏 state_version：名称本可命中
    pid1 = _create_project(client, name="P12FI坏版本")
    r1 = _seed_state(pid1, _plain_state("cv"), revision_id="esr_" + "f1" + "0" * 30)
    _set_display_name(r1["id"], needle)
    _raw_sql_update_revision(r1["id"], state_version="not_a_valid_esv")
    res1 = _search(client, pid1, {"query": needle})
    _assert_fixed_error(
        res1,
        500,
        _CODE_CORRUPT,
        message=_MSG_CORRUPT,
        forbid_parts=[r1["id"], needle, "not_a_valid_esv"],
    )
    assert "items" not in res1.json()

    # 2) 坏 snapshot_json
    pid2 = _create_project(client, name="P12FI坏JSON")
    r2 = _seed_state(pid2, _plain_state("cj"), revision_id="esr_" + "f2" + "0" * 30)
    _set_display_name(r2["id"], needle)
    _raw_sql_update_revision(r2["id"], ignore_check=True, snapshot_json="{not-json")
    res2 = _search(client, pid2, {"query": needle})
    _assert_fixed_error(
        res2,
        500,
        _CODE_CORRUPT,
        message=_MSG_CORRUPT,
        forbid_parts=[r2["id"], needle],
    )

    # 3) 坏 display_name（空串）在候选中：整次 corrupt，即使其它行名称可命中
    pid3 = _create_project(client, name="P12FI坏名称")
    good = _seed_state(
        pid3,
        _plain_state("good"),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "f3" + "0" * 30,
    )
    _set_display_name(good["id"], needle)
    bad = _seed_state(
        pid3,
        _plain_state("bad"),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
        revision_id="esr_" + "f4" + "0" * 30,
    )
    _set_display_name(bad["id"], "")
    res3 = _search(client, pid3, {"query": needle})
    _assert_fixed_error(
        res3,
        500,
        _CODE_CORRUPT,
        message=_MSG_CORRUPT,
        forbid_parts=[good["id"], bad["id"], needle],
    )

    # 4) 对象预算 4097：名称本可命中仍 corrupt（中和商务容器，仅 chapters 计对象）
    pid4 = _create_project(client, name="P12FI预算")
    _budget_neutral = {
        "businessQualify": [],
        "businessToc": [],
        "businessQuote": None,
        "businessCommit": [],
        "parsedMarkdown": None,
        "outline": None,
    }
    chapters_bad = [{"id": f"oid{i}"} for i in range(4097)]
    st_budget = _state_with_version(chapters=chapters_bad, **_budget_neutral)
    r4 = _seed_state(pid4, st_budget, revision_id="esr_" + "f5" + "0" * 30)
    _set_display_name(r4["id"], needle)
    res4 = _search(client, pid4, {"query": needle})
    _assert_fixed_error(
        res4,
        500,
        _CODE_CORRUPT,
        message=_MSG_CORRUPT,
        forbid_parts=[r4["id"], needle],
    )


def test_p12fi_combo_isolation_sql_six_keys_zero_write(disabled_client):
    """
    用途：来源/时间/空间组合隔离、六键响应、关键词不回显错误、五域零写、
      七列投影与 LIMIT 20 运行时证据。
    """
    client = disabled_client
    _ensure_workspace(_WS_OTHER)
    pid = _create_project(client, name="P12FI组合隔离")
    marker = "P12FI_COMBO_MARK"
    base = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    r_task = _seed_state(
        pid,
        _plain_state("ct"),
        source_kind="task",
        created_at=base + timedelta(days=0),
        revision_id="esr_" + "c1" + "0" * 30,
    )
    _set_display_name(r_task["id"], f"{marker}_task")
    r_rev = _seed_state(
        pid,
        _plain_state("cr"),
        source_kind="revise",
        created_at=base + timedelta(days=5),
        revision_id="esr_" + "c2" + "0" * 30,
    )
    _set_display_name(r_rev["id"], f"{marker}_revise")
    r_old = _seed_state(
        pid,
        _plain_state("co"),
        source_kind="task",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        revision_id="esr_" + "c3" + "0" * 30,
    )
    _set_display_name(r_old["id"], f"{marker}_old")

    # 他空间同名
    db = SessionLocal()
    try:
        other_pid = "proj_other_p12fi_" + secrets.token_hex(4)
        db.add(
            Project(
                id=other_pid,
                workspace_id=_WS_OTHER,
                name="其他项目I",
                kind="technical",
                status="draft",
            )
        )
        db.commit()
    finally:
        db.close()
    r_other = _seed_state(
        other_pid,
        _plain_state("ox"),
        workspace_id=_WS_OTHER,
        revision_id="esr_" + "c4" + "0" * 30,
    )
    _set_display_name(r_other["id"], f"{marker}_other")

    _seed_synthetic_task(pid, tag="p12fi")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    before = _domain_snapshot(pid)
    assert before["tasks"], "基线须含合成任务"

    # 仅来源 task + 时间窗
    body = _assert_search_ok(
        _search(
            client,
            pid,
            {
                "query": marker,
                "sourceKind": "task",
                "createdFrom": "2026-07-01T00:00:00.000Z",
                "createdBefore": "2026-08-01T00:00:00.000Z",
            },
        )
    )
    assert [it["revisionId"] for it in body["items"]] == [r_task["id"]]
    assert body["items"][0]["displayName"] == f"{marker}_task"
    assert set(body["items"][0].keys()) == _META_KEYS
    assert r_rev["id"] not in [it["revisionId"] for it in body["items"]]
    assert r_old["id"] not in [it["revisionId"] for it in body["items"]]
    assert r_other["id"] not in [it["revisionId"] for it in body["items"]]

    # 跨空间不得泄漏
    cross = _search(
        client,
        pid,
        {"query": marker},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(
        cross,
        404,
        _CODE_PROJECT_NOT_FOUND,
        message=_MSG_PROJECT_NOT_FOUND,
        forbid_parts=[r_task["id"], r_other["id"], marker, pid, other_pid],
    )

    # 坏关键词错误不回显
    bad_q = _search(client, pid, {"query": "  " + marker})
    _assert_query_invalid(bad_q, forbid_parts=[marker, "  " + marker])

    # SQL 七列 + LIMIT 20 运行时证据
    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res_sql = _search(
            client,
            pid,
            {
                "query": marker,
                "sourceKind": "task",
                "createdFrom": "2026-07-01T00:00:00.000Z",
                "createdBefore": "2026-08-01T00:00:00.000Z",
            },
        )
        assert res_sql.status_code == 200, res_sql.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    rev_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert len(rev_selects) == 1, f"revision SELECT 次数: {len(rev_selects)}"
    sql, params = rev_selects[0]
    compact = " ".join(sql.split())
    low = compact.lower()
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
    assert match is not None
    raw_parts = [p.strip() for p in match.group(1).split(",")]
    normalized: list[str] = []
    for part in raw_parts:
        col = part.lower()
        if " as " in col:
            col = col.split(" as ", 1)[0].strip()
        if "." in col:
            col = col.rsplit(".", 1)[-1].strip()
        col = col.strip('`"[]')
        normalized.append(col)
    assert normalized == [
        "id",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
        "display_name",
        "snapshot_json",
    ], normalized
    assert re.search(r"\blike\b", low) is None
    assert "json_extract" not in low
    assert "count(" not in low
    assert re.search(r"order\s+by\s+.*created_at\s+desc.*,\s*.*id\s+desc", low)
    vals = list(params.values()) if isinstance(params, dict) else list(params)
    if re.search(r"\blimit\s+20\b", low):
        pass
    elif re.search(r"\blimit\s+\?", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-2:] == [20, 0] or vals[-1] == 20
        else:
            assert vals[-1] == 20
    else:
        raise AssertionError(f"未发现 LIMIT 20: {compact}")

    assert _domain_snapshot(pid) == before
    # 错误路径零写
    _assert_fixed_error(
        _search(client, pid, {"query": marker, "sourceKind": "BAD"}),
        400,
        _CODE_SOURCE_INVALID,
        message=_MSG_SOURCE_INVALID,
    )
    assert _domain_snapshot(pid) == before
