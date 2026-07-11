"""
模块：本地标讯库服务
用途：维护工作空间内的标讯线索、按截止日计算状态，并在单次事务内从有效标讯创建关联技术标项目。
对接：app.api.opportunities；BidOpportunityPage；project_service.create_project。
二次开发：公开站点导入、RSS 或多工作空间鉴权必须保留 workspace 校验和截止状态单一事实；不得将外部抓取逻辑混入本服务。
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import BidOpportunityRow, Project
from app.services.project_service import create_project

OPPORTUNITY_STATUSES = frozenset({"open", "closing_soon", "closed"})
_CLOSING_SOON_DAYS = 7
_IMPORT_HEADER_ALIASES = {
    "title": ("title", "标题"),
    "buyer": ("buyer", "采购人"),
    "region": ("region", "地区"),
    "budget_label": ("budgetLabel", "budget_label", "预算"),
    "deadline": ("deadline", "截止日期"),
    "tags": ("tags", "标签"),
    "summary": ("summary", "摘要"),
    "source_label": ("sourceLabel", "source_label", "来源"),
    "source_key": ("sourceKey", "source_key", "来源键"),
}


class OpportunityNotFoundError(Exception):
    """
    用途：标讯不存在或不属于当前工作空间时中断服务流程。
    对接：app.api.opportunities 统一映射为 HTTP 404。
    """


class OpportunityImportValidationError(ValueError):
    """
    用途：承载离线导入的逐行校验错误，保证路由能返回行号且整批不写入。
    对接：POST /api/opportunities/import；前端导入弹层错误回显。
    """

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("标讯导入数据不合法")
        self.errors = errors


class OpportunityImportConflictError(Exception):
    """
    用途：捕获并发导入时来源键唯一约束冲突，避免路由误报为成功。
    对接：POST /api/opportunities/import；BidOpportunityRow 来源键唯一约束。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_opportunity_id() -> str:
    """用途：生成服务端标讯主键，避免客户端指定或猜测持久化标识。"""
    return f"opp_{secrets.token_hex(8)}"


def _clean_text(value: Any, default: str = "", limit: int = 1000) -> str:
    """用途：清洗展示文本，统一截断并避免空标题等非业务值进入数据库。"""
    return str(value or "").strip()[:limit] or default


def _clean_tags(value: Any) -> list[str]:
    """用途：归一化标讯标签，去空、去重并限制数量，保持 API 始终返回字符串数组。"""
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = _clean_text(item, limit=60)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 20:
            break
    return tags


def _loads_tags(raw: str | None) -> list[str]:
    """用途：容错读取持久化标签 JSON，损坏历史值降级为空数组而不影响列表。"""
    if not raw:
        return []
    try:
        return _clean_tags(json.loads(raw))
    except json.JSONDecodeError:
        return []


def _clean_source_key(value: Any) -> str | None:
    """用途：规范化离线导入来源键；空键保持 NULL，使手工或无键导入不参与去重。"""
    key = _clean_text(value, limit=200)
    return key or None


def _read_import_value(record: dict[str, Any], field: str) -> Any:
    """用途：按冻结的中英文别名读取导入列，未知列一律忽略。"""
    for name in _IMPORT_HEADER_ALIASES[field]:
        if name in record and record[name] is not None:
            return record[name]
    return None


def _parse_import_tags(value: Any, row_number: int) -> tuple[list[str], list[dict[str, Any]]]:
    """用途：将 JSON 数组或 CSV 分隔文本转为标签数组，并返回可定位的格式错误。"""
    if value is None or value == "":
        return [], []
    if isinstance(value, list):
        return _clean_tags(value), []
    if isinstance(value, str):
        return _clean_tags([item for item in re.split(r"[，,；;|\n]", value)]), []
    return [], [{"row": row_number, "field": "tags", "message": "标签必须为字符串或字符串数组"}]


def _parse_import_document(filename: str, content: bytes, max_rows: int) -> list[dict[str, Any]]:
    """
    用途：在请求生命周期内解析 UTF-8 CSV 或 JSON 导入文件，不读取路径或写入应用上传目录。
    对接：import_opportunities_from_file；POST /api/opportunities/import。
    二次开发：新增格式必须保持同样的内存解析、行数上限和统一记录结构，不得开放任意文件解析器。
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in {"csv", "json"}:
        raise ValueError("仅支持 CSV 或 JSON 标讯文件")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("导入文件必须为 UTF-8 编码") from exc

    if suffix == "json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 文件格式无效") from exc
        if isinstance(document, list):
            records = document
        elif isinstance(document, dict):
            records = document.get("items", document.get("opportunities"))
        else:
            records = None
        if not isinstance(records, list):
            raise ValueError("JSON 顶层必须为数组，或包含 items/opportunities 数组")
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or not any(name and name.strip() for name in reader.fieldnames):
            raise ValueError("CSV 文件缺少表头")
        records = [
            {str(name).strip(): value for name, value in record.items() if name is not None}
            for record in reader
        ]

    if not records:
        raise ValueError("导入文件没有标讯记录")
    if len(records) > max_rows:
        raise ValueError(f"导入行数不能超过 {max_rows} 行")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("每条导入记录必须为对象")
    return records


def _normalize_import_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    用途：预校验整批导入记录，统一为标讯写模型；出现任意错误时不返回半成品数据。
    对接：import_opportunities_from_file；BidOpportunityRow 字段和现有截止状态计算。
    """
    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_source_keys: set[str] = set()
    for row_number, record in enumerate(records, start=1):
        title = _clean_text(_read_import_value(record, "title"), limit=500)
        deadline_raw = _read_import_value(record, "deadline")
        row_errors: list[dict[str, Any]] = []
        if not title:
            row_errors.append({"row": row_number, "field": "title", "message": "标讯标题不能为空"})
        deadline_text = str(deadline_raw or "").strip()
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline_text):
                raise ValueError
            deadline = date.fromisoformat(deadline_text)
        except ValueError:
            deadline = None
            row_errors.append(
                {"row": row_number, "field": "deadline", "message": "截止日期必须为 YYYY-MM-DD"}
            )
        tags, tag_errors = _parse_import_tags(_read_import_value(record, "tags"), row_number)
        row_errors.extend(tag_errors)
        source_key = _clean_source_key(_read_import_value(record, "source_key"))
        if source_key:
            if source_key in seen_source_keys:
                row_errors.append(
                    {"row": row_number, "field": "sourceKey", "message": "来源键在文件内重复"}
                )
            seen_source_keys.add(source_key)
        if row_errors:
            errors.extend(row_errors)
            continue
        normalized.append(
            {
                "title": title,
                "buyer": _clean_text(_read_import_value(record, "buyer"), limit=500),
                "region": _clean_text(_read_import_value(record, "region"), default="其他", limit=100),
                "budget_label": _clean_text(_read_import_value(record, "budget_label"), limit=200),
                "deadline": deadline,
                "tags": tags,
                "summary": _clean_text(_read_import_value(record, "summary"), limit=20000),
                "source_label": _clean_text(
                    _read_import_value(record, "source_label"), default="本地导入", limit=200
                ),
                "source_key": source_key,
            }
        )
    if errors:
        raise OpportunityImportValidationError(errors)
    return normalized


def calculate_status(deadline: date, *, today: date | None = None) -> str:
    """
    用途：以服务端日历日统一计算标讯状态，避免前后端各自计算造成分叉。
    对接：list_opportunities、立项校验和 OpportunityOut 响应。
    """
    current_day = today or date.today()
    remaining_days = (deadline - current_day).days
    if remaining_days < 0:
        return "closed"
    if remaining_days <= _CLOSING_SOON_DAYS:
        return "closing_soon"
    return "open"


def opportunity_to_data(row: BidOpportunityRow, *, today: date | None = None) -> dict:
    """
    用途：ORM 行转标讯读模型字典，统一补齐动态状态和解析后的标签。
    对接：app.api.opportunities 的 OpportunityOut 序列化与 list_opportunities。
    """
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "buyer": row.buyer,
        "region": row.region,
        "budget_label": row.budget_label,
        "deadline": row.deadline,
        "status": calculate_status(row.deadline, today=today),
        "tags": _loads_tags(row.tags_json),
        "summary": row.summary,
        "source_label": row.source_label,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_opportunity(
    db: Session, workspace_id: str, opportunity_id: str
) -> BidOpportunityRow:
    """
    用途：按标讯 id 读取并校验工作空间归属。
    对接：标讯详情、更新、删除和立项服务；失败抛出 OpportunityNotFoundError。
    """
    row = db.get(BidOpportunityRow, opportunity_id)
    if row is None or row.workspace_id != workspace_id:
        raise OpportunityNotFoundError(opportunity_id)
    return row


def list_opportunities(
    db: Session,
    workspace_id: str,
    *,
    q: str | None = None,
    region: str | None = None,
    status: str | None = None,
    today: date | None = None,
) -> list[dict]:
    """
    用途：列出并筛选当前工作空间标讯；状态和关键词在单一读模型中计算。
    对接：GET /api/opportunities；筛选参数 q、region、status。
    """
    stmt = (
        select(BidOpportunityRow)
        .where(BidOpportunityRow.workspace_id == workspace_id)
        .order_by(BidOpportunityRow.deadline.asc(), BidOpportunityRow.updated_at.desc())
    )
    rows = list(db.scalars(stmt).all())
    query = (q or "").strip().lower()
    selected_region = (region or "").strip()
    selected_status = (status or "").strip()
    items: list[dict] = []
    for row in rows:
        data = opportunity_to_data(row, today=today)
        if selected_region and selected_region != "全部" and data["region"] != selected_region:
            continue
        if selected_status and selected_status != "all" and data["status"] != selected_status:
            continue
        if query:
            searchable = "\n".join(
                [
                    data["title"],
                    data["buyer"],
                    data["summary"],
                    " ".join(data["tags"]),
                ]
            ).lower()
            if query not in searchable:
                continue
        items.append(data)
    return items


def create_opportunity(
    db: Session, workspace_id: str, payload: dict[str, Any]
) -> BidOpportunityRow:
    """
    用途：创建手工录入的本地标讯；标题与截止日期是最小必备事实。
    对接：POST /api/opportunities；OpportunityCreate 传入已校验日期。
    """
    title = _clean_text(payload.get("title"), limit=500)
    if not title:
        raise ValueError("标讯标题不能为空")
    deadline = payload.get("deadline")
    if not isinstance(deadline, date):
        raise ValueError("截止日期格式无效")
    row = BidOpportunityRow(
        id=_new_opportunity_id(),
        workspace_id=workspace_id,
        title=title,
        buyer=_clean_text(payload.get("buyer"), limit=500),
        region=_clean_text(payload.get("region"), default="其他", limit=100),
        budget_label=_clean_text(payload.get("budget_label"), limit=200),
        deadline=deadline,
        tags_json=json.dumps(_clean_tags(payload.get("tags")), ensure_ascii=False),
        summary=_clean_text(payload.get("summary"), limit=20000),
        source_label=_clean_text(
            payload.get("source_label"), default="本地录入", limit=200
        ),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def import_opportunities_from_file(
    db: Session,
    workspace_id: str,
    *,
    filename: str,
    content: bytes,
    max_rows: int,
) -> dict[str, int]:
    """
    用途：在单次事务内导入本机 CSV 或 JSON 标讯；任一非法行时零写入，来源键重复时跳过。
    对接：POST /api/opportunities/import；导入文件不由应用持久化。
    二次开发：外部同步应另建受控来源和审计流程，不得复用本函数下载 URL、解析附件或写入密钥。
    """
    records = _parse_import_document(filename, content, max_rows)
    payloads = _normalize_import_records(records)
    source_keys = {payload["source_key"] for payload in payloads if payload["source_key"]}
    existing_keys: set[str] = set()
    if source_keys:
        existing_keys = set(
            db.scalars(
                select(BidOpportunityRow.source_key).where(
                    BidOpportunityRow.workspace_id == workspace_id,
                    BidOpportunityRow.source_key.in_(source_keys),
                )
            ).all()
        )

    inserted = 0
    skipped = 0
    now = _now()
    try:
        for payload in payloads:
            source_key = payload["source_key"]
            if source_key and source_key in existing_keys:
                skipped += 1
                continue
            db.add(
                BidOpportunityRow(
                    id=_new_opportunity_id(),
                    workspace_id=workspace_id,
                    title=payload["title"],
                    buyer=payload["buyer"],
                    region=payload["region"],
                    budget_label=payload["budget_label"],
                    deadline=payload["deadline"],
                    tags_json=json.dumps(payload["tags"], ensure_ascii=False),
                    summary=payload["summary"],
                    source_label=payload["source_label"],
                    source_key=source_key,
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise OpportunityImportConflictError() from exc
    return {"inserted": inserted, "skipped": skipped, "total": len(payloads)}


def update_opportunity(
    db: Session,
    workspace_id: str,
    opportunity_id: str,
    payload: dict[str, Any],
) -> BidOpportunityRow:
    """
    用途：更新已存在标讯；不接受 status 字段，避免状态与 deadline 脱节。
    对接：PATCH /api/opportunities/{id}；get_opportunity 负责工作空间校验。
    """
    row = get_opportunity(db, workspace_id, opportunity_id)
    if "title" in payload:
        title = _clean_text(payload["title"], limit=500)
        if not title:
            raise ValueError("标讯标题不能为空")
        row.title = title
    if "buyer" in payload:
        row.buyer = _clean_text(payload["buyer"], limit=500)
    if "region" in payload:
        row.region = _clean_text(payload["region"], default="其他", limit=100)
    if "budget_label" in payload:
        row.budget_label = _clean_text(payload["budget_label"], limit=200)
    if "deadline" in payload:
        deadline = payload["deadline"]
        if not isinstance(deadline, date):
            raise ValueError("截止日期格式无效")
        row.deadline = deadline
    if "tags" in payload:
        row.tags_json = json.dumps(_clean_tags(payload["tags"]), ensure_ascii=False)
    if "summary" in payload:
        row.summary = _clean_text(payload["summary"], limit=20000)
    if "source_label" in payload:
        row.source_label = _clean_text(
            payload["source_label"], default="本地录入", limit=200
        )
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def delete_opportunity(db: Session, workspace_id: str, opportunity_id: str) -> None:
    """
    用途：删除标讯并清空项目弱关联，不级联删除项目及其产物。
    对接：DELETE /api/opportunities/{id}；projects.source_opportunity_id。
    """
    row = get_opportunity(db, workspace_id, opportunity_id)
    db.execute(
        update(Project)
        .where(
            Project.workspace_id == workspace_id,
            Project.source_opportunity_id == opportunity_id,
        )
        .values(source_opportunity_id=None)
    )
    db.delete(row)
    db.commit()


def create_project_from_opportunity(
    db: Session,
    workspace_id: str,
    opportunity_id: str,
    *,
    name: str | None = None,
    industry: str | None = None,
) -> Project:
    """
    用途：从未截止标讯原子创建技术标项目；任一步失败均不保留半成品项目。
    对接：POST /api/opportunities/{id}/projects；project_service.create_project。
    二次开发：多项目类型或审批流应扩展参数，不得拆开当前事务边界。
    """
    try:
        opportunity = get_opportunity(db, workspace_id, opportunity_id)
        if calculate_status(opportunity.deadline) == "closed":
            raise ValueError("标讯已截止，不能创建技术方案项目")
        project = create_project(
            db,
            workspace_id,
            name=_clean_text(name, default=opportunity.title, limit=500),
            industry=_clean_text(industry, default="通用", limit=100),
            kind="technical",
            source_opportunity_id=opportunity.id,
            commit=False,
        )
        db.commit()
        db.refresh(project)
        return project
    except Exception:
        db.rollback()
        raise


def ensure_sample_opportunities(db: Session, workspace_id: str) -> None:
    """
    用途：为显式开启演示开关的空本地库幂等写入最小示例标讯。
    对接：app.main.lifespan；Settings.seed_sample_opportunities。
    二次开发：示例仅限本地演示，禁止改为生产启动默认写入。
    """
    exists = db.scalars(
        select(BidOpportunityRow.id)
        .where(BidOpportunityRow.workspace_id == workspace_id)
        .limit(1)
    ).first()
    if exists:
        return
    today = date.today()
    samples = [
        {
            "title": "本地示例：智慧交通综合管理平台",
            "buyer": "某市交通治理中心",
            "region": "华东",
            "budget_label": "约 680 万",
            "deadline": today + timedelta(days=12),
            "tags": ["智慧交通", "软件", "信创"],
            "summary": "用于本地演示的标讯线索，可编辑、删除或直接创建技术标项目。",
            "source_label": "本地示例",
        },
        {
            "title": "本地示例：医院信息集成平台改造",
            "buyer": "某综合医院",
            "region": "华北",
            "budget_label": "约 420 万",
            "deadline": today + timedelta(days=5),
            "tags": ["医疗", "集成", "HIS"],
            "summary": "用于演示截止预警状态的本地线索，不代表实时公开招标信息。",
            "source_label": "本地示例",
        },
    ]
    for sample in samples:
        row = BidOpportunityRow(
            id=_new_opportunity_id(),
            workspace_id=workspace_id,
            title=sample["title"],
            buyer=sample["buyer"],
            region=sample["region"],
            budget_label=sample["budget_label"],
            deadline=sample["deadline"],
            tags_json=json.dumps(sample["tags"], ensure_ascii=False),
            summary=sample["summary"],
            source_label=sample["source_label"],
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
    db.commit()
