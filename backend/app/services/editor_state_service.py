"""
模块：项目编辑器状态服务
用途：读写大纲/章节/事实/结构化分析/guidance/解析文/商务标字段；
  全状态规范版本（stateVersion）与可选 CAS；响应矩阵乐观版本防多端覆盖。
对接：GET|PUT /api/projects/{id}/editor-state；P12A 检查点共享版本算法。
二次开发：
  - business 字段整包存 business_json，API 拆成 businessQualify 等 camelCase。
  - responseMatrixVersion 由收敛后矩阵内容哈希得出，勿绑 updated_at。
  - stateVersion 由精确 13 键规范 JSON 的 SHA-256 得出；P12A 必须委托本模块，禁止双实现。
  - expectedStateVersion 为兼容期可选 CAS，缺失时仍允许覆盖（非最终安全门）。
  - 全状态 CAS 与矩阵版本共用一次项目锁与一次锁后 row；先比全状态再比矩阵；冲突显式 rollback。
  - commit 前构造成功响应；commit 后禁止 refresh/重读，避免写成功但客户端假失败。
  - updatedAt 经 _format_updated_at 去时区后缀，保证 commit 前与后续 GET 字符串一致。
  - 持久 JSON 读写经 _sanitize_json_value 将非有限 float 收敛为 None；规范序列化仍 allow_nan=False。
"""

from __future__ import annotations

import json
import math
import re
from hashlib import sha1, sha256
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import Project, ProjectEditorStateRow
from app.services.project_service import ProjectNotFoundError, get_project

# business_json 内键名（snake）
_BIZ_KEYS = ("qualify", "toc", "quote", "commit")
_MATRIX_KINDS = frozenset({"requirement", "scoring"})
_MATRIX_STATUSES = frozenset({"uncovered", "partial", "covered", "waived"})

# P12A/P12B 精确 13 键（排序序列化由 sort_keys 决定字节序）
CANONICAL_STATE_KEYS: tuple[str, ...] = (
    "outline",
    "chapters",
    "facts",
    "mode",
    "analysis",
    "responseMatrix",
    "guidance",
    "parsedMarkdown",
    "businessQualify",
    "businessToc",
    "businessQuote",
    "businessCommit",
    "analysisOverview",
)
CANONICAL_STATE_KEY_SET = frozenset(CANONICAL_STATE_KEYS)

# 全状态版本格式：esv_ + 32 位小写 hex（P12A 算法产出；C1/C2 共用校验）
STATE_VERSION_PATTERN = re.compile(r"^esv_[0-9a-f]{32}$")

MSG_FULL_STATE_VERSION_CONFLICT = "编辑内容已被其他操作更新，请重新载入后再保存"
CODE_FULL_STATE_VERSION_CONFLICT = "editor_state_version_conflict"


def is_valid_state_version(value: object) -> bool:
    """
    用途：唯一后端版本格式 helper；严格 ^esv_[0-9a-f]{32}$。
    对接：P12B-C 任务内部版本门、revise/callback 入参与成功响应校验。
    二次开发：C2 票据/个人回调必须复用本函数，禁止复制正则。
    """
    return isinstance(value, str) and STATE_VERSION_PATTERN.fullmatch(value) is not None


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


class EditorStateVersionConflict(Exception):
    """
    用途：PUT 携带陈旧 expectedStateVersion 时拒绝整包写入。
    对接：projects.put_editor_state → HTTP 409 固定最小 detail。
    二次开发：detail 仅 code/message/currentStateVersion；禁止回显正文或矩阵。
    """

    def __init__(
        self,
        *,
        message: str,
        current_state_version: str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.current_state_version = current_state_version


def _sanitize_json_value(value: Any) -> Any:
    """
    用途：确定性 JSON 安全收敛——嵌套 dict/list 中非有限 float（NaN/±Inf）→ None。
    对接：_loads/_dumps 与权威状态组装，使写入与读取得到相同安全值。
    二次开发：只收敛非有限 float；循环/不可序列化对象仍由上层序列化失败并 rollback。
      不得在此吞异常；不得改写 canonical_snapshot_json 的 allow_nan=False 严格性。
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    return value


def _format_updated_at(value: datetime | None) -> str | None:
    """
    用途：跨方言稳定的 updatedAt 序列化——commit 前响应与任意后续 GET 字符串完全一致。
    对接：_state_from_row（新行/内存行与数据库重读行共用）。
    二次开发：aware 转 UTC 后去 tzinfo 再 isoformat；naive 按既有 UTC 语义直接 isoformat。
      禁止依赖 commit 后 refresh；禁止输出 +00:00 后缀以免 SQLite 重读漂移。
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return _sanitize_json_value(json.loads(raw))
    except json.JSONDecodeError:
        return None


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    # 写入前收敛非有限 float，避免落非标准 JSON；规范版本仍走 allow_nan=False
    return json.dumps(_sanitize_json_value(value), ensure_ascii=False)


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


def extract_canonical_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """
    用途：从服务端规范 editor-state 抽取精确 13 键快照。
    对接：全状态 stateVersion；P12A 检查点 snapshot。
    二次开发：不得写入 projectId/updatedAt/responseMatrixVersion 等派生/敏感字段。
    """
    return {key: state.get(key) for key in CANONICAL_STATE_KEYS}


def canonical_snapshot_json(snapshot: dict[str, Any]) -> str:
    """
    用途：紧凑 sort_keys UTF-8 标准 JSON（全状态规范序列化）。
    二次开发：必须 allow_nan=False，禁止写出 NaN/Infinity。
    """
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_state_version_from_canonical_json(snapshot_json: str) -> str:
    """
    用途：对规范快照 JSON 字节做 SHA-256，取前 32 hex 并加 esv_ 前缀。
    对接：stateVersion；P12A checkpoint state_version。
    二次开发：必须对独立序列化字节重算，禁止复用矩阵哈希或 updatedAt。
    """
    digest = sha256(snapshot_json.encode("utf-8")).hexdigest()
    return "esv_" + digest[:32]


def compute_full_state_version(state: dict[str, Any]) -> str:
    """
    用途：由规范 13 键内容计算全状态版本（权威入口）。
    对接：GET/PUT EditorStateOut.stateVersion；CAS 锁后比对。
    """
    snapshot = extract_canonical_snapshot(state)
    return compute_state_version_from_canonical_json(canonical_snapshot_json(snapshot))


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


def _state_from_row(project_id: str, row: ProjectEditorStateRow | None) -> dict:
    """
    用途：纯内存从 ORM 行构造 editor-state 响应（含 stateVersion），不访问数据库。
    对接：get_editor_state；CAS 锁后版本比对；commit 前成功响应构造。
    二次开发：禁止在此函数内 db.get/SELECT/commit；CAS 必须复用锁后同一 row，禁止漂移快照。
    """
    if row is None:
        biz = empty_business()
        empty_matrix = empty_response_matrix()
        state = {
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
        state["stateVersion"] = compute_full_state_version(state)
        return state

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
    state = {
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
        "updatedAt": _format_updated_at(row.updated_at),
    }
    state["stateVersion"] = compute_full_state_version(state)
    return state


def _lock_for_versioned_write(
    db: Session, workspace_id: str, project_id: str
) -> ProjectEditorStateRow | None:
    """
    用途：全状态 CAS / 矩阵版本写入前取得项目级写锁，使「读版本→比对→写」在事务内串行。
    对接：upsert_editor_state 带 expectedStateVersion 或 responseMatrixVersion 的路径。
    二次开发：
      - SQLite：对 projects 行做无副作用 UPDATE（与图片上传锁同策略），依赖文件库写锁串行。
      - PostgreSQL 等：SELECT projects / project_editor_states FOR UPDATE。
      - 全状态与矩阵共用一次锁；禁止仅依赖进程内锁或 GIL。
    """
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        result = db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
            .values(updated_at=Project.updated_at)
        )
        if result.rowcount == 0:
            raise ProjectNotFoundError(project_id)
        # 锁后再读，避免读到过期快照
        db.expire_all()
        return db.get(ProjectEditorStateRow, project_id)

    project = db.execute(
        select(Project)
        .where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if project is None:
        raise ProjectNotFoundError(project_id)
    return db.execute(
        select(ProjectEditorStateRow)
        .where(ProjectEditorStateRow.project_id == project_id)
        .with_for_update()
    ).scalar_one_or_none()


def get_editor_state(db: Session, workspace_id: str, project_id: str) -> dict:
    """
    用途：返回编辑器状态字典（camelCase），含 responseMatrixVersion 与 stateVersion。
    二次开发：仅项目校验 + 一次 db.get，序列化委托 _state_from_row，禁止重复读行。
    """
    get_project(db, workspace_id, project_id)
    row = db.get(ProjectEditorStateRow, project_id)
    return _state_from_row(project_id, row)


def lock_and_assert_expected_state_version(
    db: Session,
    workspace_id: str,
    project_id: str,
    expected_state_version: str,
) -> tuple[ProjectEditorStateRow | None, dict]:
    """
    用途：公开锁后全状态版本校验原语（不自行 commit）。
    对接：P12B-A upsert CAS；P12B-C 任务/revise/callback 共用。
    二次开发：
      - 取得与 upsert 相同的项目级写锁；只读一次 editor-state ORM 行；
      - 以 _state_from_row 规范视图重算 stateVersion，禁止复制 13 键算法或信任 updatedAt；
      - 不匹配抛 EditorStateVersionConflict；调用方负责同事务业务写与 rollback。
    """
    row = _lock_for_versioned_write(db, workspace_id, project_id)
    current_state = _state_from_row(project_id, row)
    current_sv = current_state["stateVersion"]
    if expected_state_version != current_sv:
        raise EditorStateVersionConflict(
            message=MSG_FULL_STATE_VERSION_CONFLICT,
            current_state_version=current_sv,
        )
    return row, current_state


def apply_canonical_snapshot_to_locked_row(
    db: Session,
    project_id: str,
    row: ProjectEditorStateRow | None,
    snapshot: dict[str, Any],
) -> ProjectEditorStateRow:
    """
    用途：对已持锁的 editor-state ORM 行写回精确 13 键规范快照（无锁/无项目查询/无 commit）。
    对接：P12B-D1 restore_editor_state_checkpoint；与 upsert 共用序列化与矩阵收敛。
    二次开发：
      - 入口复用 CANONICAL_STATE_KEY_SET：拒绝非 dict、缺键或额外键；校验必须在新建/修改 ORM 行之前；
      - 仅操作调用方传入的已持锁 row；row 为 None 时新建内存行并 add，不得自行锁或 SELECT 项目；
      - 禁止 commit/refresh/get_editor_state；调用方负责版本复核与事务边界；
      - analysis 与 analysisOverview 双写；商务字段合并进 business_json；矩阵写后 reconcile。
      - 禁止本地复刻 13 键字面量集合；键集防御只引用 CANONICAL_STATE_KEY_SET。
    """
    # 精确键集防御：未来误调用时不得静默接受缺键/多键并清空字段
    if not isinstance(snapshot, dict):
        raise ValueError(
            "apply_canonical_snapshot_to_locked_row 要求 snapshot 为精确键集 dict"
        )
    if frozenset(snapshot.keys()) != CANONICAL_STATE_KEY_SET:
        raise ValueError(
            "apply_canonical_snapshot_to_locked_row 要求 snapshot 键集精确等于 "
            "CANONICAL_STATE_KEY_SET"
        )

    if row is None:
        row = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
        db.add(row)

    outline = snapshot["outline"]
    chapters = snapshot["chapters"]
    facts = snapshot["facts"]
    mode = snapshot["mode"]
    analysis = snapshot["analysis"]
    response_matrix = snapshot["responseMatrix"]
    guidance = snapshot["guidance"]
    parsed_markdown = snapshot["parsedMarkdown"]
    business_qualify = snapshot["businessQualify"]
    business_toc = snapshot["businessToc"]
    business_quote = snapshot["businessQuote"]
    business_commit = snapshot["businessCommit"]
    analysis_overview = snapshot["analysisOverview"]

    row.outline_json = _dumps(outline)
    row.chapters_json = _dumps(chapters)
    row.facts_json = _dumps(facts)
    row.mode = mode if mode in ("ALIGNED", "FREE") else "ALIGNED"

    # analysis 与 analysisOverview 双写，保证 _state_from_row 重读与检查点 13 键一致
    norm = normalize_analysis(analysis)
    if isinstance(analysis_overview, str):
        norm = {**norm, "overview": analysis_overview}
        row.analysis_overview = analysis_overview
    elif analysis_overview is None:
        # 空态：overview 可能为 ""，analysisOverview 规范为 None
        overview = norm.get("overview") or ""
        if overview:
            row.analysis_overview = overview
        else:
            row.analysis_overview = None
            norm = {**norm, "overview": ""}
    else:
        # 非字符串非 None：收敛为字符串，避免第二套语义
        text = str(analysis_overview)
        norm = {**norm, "overview": text}
        row.analysis_overview = text
    row.analysis_json = _dumps(norm)

    if response_matrix is not None:
        row.response_matrix_json = _dumps(normalize_response_matrix(response_matrix))
    else:
        row.response_matrix_json = _dumps(empty_response_matrix())

    row.guidance_json = _dumps(guidance)
    row.parsed_markdown = parsed_markdown

    if isinstance(business_quote, dict):
        quote_rows = business_quote.get("rows")
        quote = {
            "rows": quote_rows if isinstance(quote_rows, list) else [],
            "notes": str(business_quote.get("notes") or ""),
        }
    else:
        quote = {"rows": [], "notes": ""}
    biz = {
        "qualify": business_qualify if isinstance(business_qualify, list) else [],
        "toc": business_toc if isinstance(business_toc, list) else [],
        "quote": quote,
        "commit": business_commit if isinstance(business_commit, list) else [],
    }
    row.business_json = _dumps(normalize_business(biz))

    # 与 upsert 相同：写后按 outline/chapters 收敛矩阵，禁止第二套映射
    row.response_matrix_json = _dumps(
        reconcile_response_matrix(
            _loads(getattr(row, "response_matrix_json", None)),
            _loads(row.outline_json),
            _loads(row.chapters_json),
        )
    )
    row.updated_at = datetime.now(timezone.utc)
    return row


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
    expected_state_version: Any = ...,
    guidance: Any = ...,
    parsed_markdown: str | None = ...,
    business_qualify: Any = ...,
    business_toc: Any = ...,
    business_quote: Any = ...,
    business_commit: Any = ...,
) -> dict:
    """
    用途：部分更新；analysis 与 analysis_overview 双写；商务字段合并进 business_json。
    二次开发：
      - 可选 expectedStateVersion：锁后仅用同一 ORM 行重算全状态版本，不等则固定冲突整包零写。
      - 同时带 responseMatrix + responseMatrixVersion 时矩阵乐观锁；与全状态共用一次锁与一次锁后读取。
      - 先比全状态再比矩阵；任一冲突显式 rollback。
      - commit 前构造成功响应；commit 后禁止 refresh/重读/再序列化，避免假失败。
      - 缺 expected 时保持兼容覆盖（非最终安全门）。
    """
    writing_matrix = response_matrix is not ... and response_matrix is not None
    client_matrix_version = (
        None
        if response_matrix_version is ... or response_matrix_version is None
        else str(response_matrix_version).strip() or None
    )
    versioned_matrix_write = writing_matrix and client_matrix_version is not None
    expected_sv = (
        None
        if expected_state_version is ... or expected_state_version is None
        else str(expected_state_version).strip() or None
    )
    needs_version_lock = expected_sv is not None or versioned_matrix_write

    try:
        if expected_sv is not None:
            # 共用锁后原语：一次锁 + 一次规范版本比对；再在同一事务比矩阵
            row, current_state = lock_and_assert_expected_state_version(
                db, workspace_id, project_id, expected_sv
            )
            if versioned_matrix_write:
                current_matrix = current_state["responseMatrix"]
                current_matrix_version = current_state["responseMatrixVersion"]
                if client_matrix_version != current_matrix_version:
                    raise ResponseMatrixVersionConflict(
                        message="响应矩阵已被其他终端更新，请重新载入后再保存",
                        current_matrix=current_matrix,
                        current_version=current_matrix_version,
                    )
            # 禁止再次 get_editor_state / db.get；写入复用锁后同一 row
        elif needs_version_lock:
            # 仅矩阵版本写：仍走同一项目锁
            row = _lock_for_versioned_write(db, workspace_id, project_id)
            current_state = _state_from_row(project_id, row)
            current_matrix = current_state["responseMatrix"]
            current_matrix_version = current_state["responseMatrixVersion"]
            if client_matrix_version != current_matrix_version:
                raise ResponseMatrixVersionConflict(
                    message="响应矩阵已被其他终端更新，请重新载入后再保存",
                    current_matrix=current_matrix,
                    current_version=current_matrix_version,
                )
        else:
            get_project(db, workspace_id, project_id)
            row = db.get(ProjectEditorStateRow, project_id)

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
                biz["qualify"] = (
                    business_qualify if isinstance(business_qualify, list) else []
                )
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
                biz["commit"] = (
                    business_commit if isinstance(business_commit, list) else []
                )
            row.business_json = _dumps(normalize_business(biz))

        row.response_matrix_json = _dumps(
            reconcile_response_matrix(
                _loads(getattr(row, "response_matrix_json", None)),
                _loads(row.outline_json),
                _loads(row.chapters_json),
            )
        )
        row.updated_at = datetime.now(timezone.utc)
        # 提交前基于本轮内存赋值构造完整成功响应；失败则同域 rollback，库不变
        response = _state_from_row(project_id, row)
        db.commit()
    except EditorStateVersionConflict:
        db.rollback()
        raise
    except ResponseMatrixVersionConflict:
        db.rollback()
        raise
    except ProjectNotFoundError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    # commit 成功后直接返回；禁止 refresh / 再次 GET / 再算哈希
    return response
