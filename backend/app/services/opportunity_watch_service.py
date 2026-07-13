"""
模块：国能 e 招计划追踪服务
用途：提供工作空间隔离的计划/运行/命中读取、中断运行收敛，以及本机 .xlsx 计划表内存导入。
对接：app.main.lifespan；app.api.opportunity_watch；BidWatchPlanRow 等追踪实体。
二次开发：不得在此混入任意 URL、Cookie、HTML、JSON 或浏览器请求；外部访问仅能由固定来源客户端承担。
"""

from __future__ import annotations

import hashlib
import io
import secrets
from datetime import datetime, timezone
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import BidSourceHitRow, BidSourceSyncRunRow, BidWatchPlanRow

# 计划表中文表头到实体字段的固定映射；未知列忽略。
_PLAN_HEADER_TO_FIELD = {
    "招标计划名称": "title",
    "招标人": "buyer",
    "范围": "scope",
    "计划工期": "duration",
    "预计发布公告时间": "expected_publish_text",
    "备注": "remark",
}
_REQUIRED_TITLE_HEADER = "招标计划名称"
_HEADER_SCAN_MAX_ROW = 10
_FIELD_LIMITS = {
    "title": 500,
    "buyer": 500,
    "scope": 20000,
    "duration": 300,
    "expected_publish_text": 300,
    "remark": 20000,
}


class WatchPlanImportValidationError(ValueError):
    """
    模块：计划表导入校验异常
    用途：承载可定位的行号/字段错误，保证路由返回 422 且整批不写入。
    对接：import_watch_plans_from_xlsx；POST /api/opportunity-watch/plans/import。
    二次开发：errors 仅允许安全的 row/field/message；禁止拼接原始文件、路径、公式或 Python 异常原文。
    """

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("计划表导入数据不合法")
        self.errors = errors


def _now() -> datetime:
    """用途：统一同步运行恢复与计划导入写入的 UTC 时间源。"""
    return datetime.now(timezone.utc)


def _new_watch_plan_id() -> str:
    """用途：生成服务端追踪计划主键，避免客户端指定持久化标识。"""
    return f"watch_plan_{secrets.token_hex(8)}"


def _clean_text(value: Any, *, limit: int = 1000) -> str:
    """用途：清洗单元格文本，统一 strip 与截断，不保留公式对象。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        text = value.isoformat(sep=" ", timespec="seconds")
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    return text.strip()[:limit]


def _plan_fingerprint(title: str, buyer: str, scope: str) -> str:
    """
    用途：对清洗后的计划名、招标人、范围计算确定性指纹，供工作空间内幂等去重。
    对接：BidWatchPlanRow.fingerprint；(workspace_id, fingerprint) 唯一约束。
    """
    material = f"{title}\n{buyer}\n{scope}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _cell_values(row: tuple[Any, ...] | list[Any]) -> list[Any]:
    """用途：将 openpyxl 行单元格规范为原始值列表。"""
    values: list[Any] = []
    for cell in row:
        if hasattr(cell, "value"):
            values.append(cell.value)
        else:
            values.append(cell)
    return values


def _is_blank_row(values: list[Any]) -> bool:
    """用途：判断数据行是否全空，供空行跳过裁定使用。"""
    return all(_clean_text(value) == "" for value in values)


def _locate_header(rows: list[tuple[int, list[Any]]]) -> tuple[int, dict[str, int]]:
    """
    用途：在前十个 Excel 行定位中文表头，并建立字段到 0 基列下标的映射。
    对接：import_watch_plans_from_xlsx 的表头扫描边界。
    """
    for excel_row, values in rows:
        if excel_row > _HEADER_SCAN_MAX_ROW:
            break
        cleaned = [_clean_text(value, limit=100) for value in values]
        if _REQUIRED_TITLE_HEADER not in cleaned:
            continue
        mapping: dict[str, int] = {}
        for index, header in enumerate(cleaned):
            field = _PLAN_HEADER_TO_FIELD.get(header)
            if field and field not in mapping:
                mapping[field] = index
        if "title" in mapping:
            return excel_row, mapping
    raise WatchPlanImportValidationError(
        [
            {
                "row": None,
                "field": _REQUIRED_TITLE_HEADER,
                "message": "前十行未找到表头「招标计划名称」",
            }
        ]
    )


def _read_field(values: list[Any], mapping: dict[str, int], field: str) -> str:
    """用途：按表头映射读取并清洗单个计划字段。"""
    index = mapping.get(field)
    if index is None or index >= len(values):
        return ""
    return _clean_text(values[index], limit=_FIELD_LIMITS[field])


def list_watch_plans(db: Session, workspace_id: str) -> list[BidWatchPlanRow]:
    """
    模块：计划追踪列表查询
    用途：只读取当前工作空间的计划追踪记录，避免服务层遗漏归属过滤。
    对接：BidWatchPlanRow；OpportunityWatchPlanOut；计划导入后的列表校验。
    二次开发：调用方必须传入已校验的 workspace_id；禁止省略 where 过滤或跨工作空间查询。
    """
    stmt = (
        select(BidWatchPlanRow)
        .where(BidWatchPlanRow.workspace_id == workspace_id)
        .order_by(BidWatchPlanRow.updated_at.desc(), BidWatchPlanRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def list_watch_runs(db: Session, workspace_id: str) -> list[BidSourceSyncRunRow]:
    """
    模块：同步运行列表查询
    用途：只读取当前工作空间的同步运行记录，供后续运行状态和审计展示。
    对接：BidSourceSyncRunRow；OpportunityWatchSyncRunOut；当前尚无公开 HTTP 路由。
    二次开发：调用方必须传入已校验的 workspace_id；禁止跨工作空间查询或回传错误原文。
    """
    stmt = (
        select(BidSourceSyncRunRow)
        .where(BidSourceSyncRunRow.workspace_id == workspace_id)
        .order_by(BidSourceSyncRunRow.started_at.desc(), BidSourceSyncRunRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def list_watch_hits(db: Session, workspace_id: str) -> list[BidSourceHitRow]:
    """
    模块：公告命中列表查询
    用途：只读取当前工作空间的公告命中，绝不跨工作空间返回追踪结果。
    对接：BidSourceHitRow；OpportunityWatchHitOut；当前尚无公开 HTTP 路由。
    二次开发：调用方必须传入已校验的 workspace_id；不得返回其它 workspace 命中或外部正文。
    """
    stmt = (
        select(BidSourceHitRow)
        .where(BidSourceHitRow.workspace_id == workspace_id)
        .order_by(BidSourceHitRow.updated_at.desc(), BidSourceHitRow.id.asc())
    )
    return list(db.scalars(stmt).all())


def mark_interrupted_watch_runs(db: Session) -> int:
    """
    模块：中断同步运行收敛
    用途：将进程重启前未结束的 queued/running 同步运行标为 failed/interrupted。
    对接：app.main.lifespan；仅修改运行状态，命中和本地标讯均保留。
    二次开发：不得删除命中、计划或 bid_opportunities；新增运行状态时同步扩展过滤条件与测试。
    """
    now = _now()
    result = db.execute(
        update(BidSourceSyncRunRow)
        .where(BidSourceSyncRunRow.status.in_(("queued", "running")))
        .values(
            status="failed",
            error_code="interrupted",
            finished_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def import_watch_plans_from_xlsx(
    db: Session,
    workspace_id: str,
    *,
    filename: str,
    content: bytes,
    max_rows: int,
) -> dict[str, int]:
    """
    模块：国能计划表内存导入
    用途：仅在内存中解析本机 .xlsx，全量校验后于单一事务按指纹幂等写入追踪计划。
    对接：POST /api/opportunity-watch/plans/import；BidWatchPlanRow；Settings 行数上限。
    二次开发：禁止持久化 content/工作簿/路径；不得接受 URL、Cookie、Token 或发起外网请求。
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix != "xlsx":
        raise ValueError("仅支持 .xlsx 招标计划文件")

    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        # 不向外透传 openpyxl 或 zip 原始异常文本。
        raise ValueError("无法解析 .xlsx 计划文件") from exc

    try:
        sheet = workbook.worksheets[0] if workbook.worksheets else None
        if sheet is None:
            raise WatchPlanImportValidationError(
                [{"row": None, "field": _REQUIRED_TITLE_HEADER, "message": "工作簿没有可用工作表"}]
            )

        # 流式读取：表头至多扫描前 10 行；定位后逐行处理，禁止全表物化。
        row_iter = enumerate(sheet.iter_rows(), start=1)
        header_scan: list[tuple[int, list[Any]]] = []
        for excel_row, row in row_iter:
            values = _cell_values(row)
            header_scan.append((excel_row, values))
            # 一旦本行含必填表头即停止扫描，后续行改由数据流处理。
            cleaned = [_clean_text(value, limit=100) for value in values]
            if _REQUIRED_TITLE_HEADER in cleaned:
                break
            if excel_row >= _HEADER_SCAN_MAX_ROW:
                break

        header_row, mapping = _locate_header(header_scan)

        payloads: list[dict[str, str]] = []
        errors: list[dict[str, Any]] = []
        non_blank_plan_rows = 0

        def _consume_data_row(excel_row: int, values: list[Any]) -> None:
            """用途：流式消费单行数据；超限立即抛错，全空白行跳过。"""
            nonlocal non_blank_plan_rows
            if excel_row <= header_row:
                return
            if _is_blank_row(values):
                return
            non_blank_plan_rows += 1
            if non_blank_plan_rows > max_rows:
                raise ValueError(f"导入计划行数不能超过 {max_rows} 行")
            title = _read_field(values, mapping, "title")
            buyer = _read_field(values, mapping, "buyer")
            scope = _read_field(values, mapping, "scope")
            duration = _read_field(values, mapping, "duration")
            expected_publish_text = _read_field(values, mapping, "expected_publish_text")
            remark = _read_field(values, mapping, "remark")
            if not title:
                errors.append(
                    {
                        "row": excel_row,
                        "field": _REQUIRED_TITLE_HEADER,
                        "message": "招标计划名称不能为空",
                    }
                )
                return
            payloads.append(
                {
                    "title": title,
                    "buyer": buyer,
                    "scope": scope,
                    "duration": duration,
                    "expected_publish_text": expected_publish_text,
                    "remark": remark,
                    "fingerprint": _plan_fingerprint(title, buyer, scope),
                }
            )

        # 表头扫描窗口内、表头之后的行先消费（仅当表头未提前命中时可能存在）。
        for excel_row, values in header_scan:
            _consume_data_row(excel_row, values)

        # 继续流式读取剩余行；触发上限时不再请求后续行。
        for excel_row, row in row_iter:
            _consume_data_row(excel_row, _cell_values(row))

        if errors:
            raise WatchPlanImportValidationError(errors)

        fingerprints = {item["fingerprint"] for item in payloads}
        existing: set[str] = set()
        if fingerprints:
            existing = set(
                db.scalars(
                    select(BidWatchPlanRow.fingerprint).where(
                        BidWatchPlanRow.workspace_id == workspace_id,
                        BidWatchPlanRow.fingerprint.in_(fingerprints),
                    )
                ).all()
            )

        inserted = 0
        skipped = 0
        seen_in_batch: set[str] = set()
        now = _now()
        for payload in payloads:
            fingerprint = payload["fingerprint"]
            if fingerprint in existing or fingerprint in seen_in_batch:
                skipped += 1
                continue
            seen_in_batch.add(fingerprint)
            db.add(
                BidWatchPlanRow(
                    id=_new_watch_plan_id(),
                    workspace_id=workspace_id,
                    title=payload["title"],
                    buyer=payload["buyer"],
                    scope=payload["scope"],
                    duration=payload["duration"],
                    expected_publish_text=payload["expected_publish_text"],
                    remark=payload["remark"],
                    fingerprint=fingerprint,
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            inserted += 1

        db.commit()
        return {
            "inserted": inserted,
            "skipped": skipped,
            "total": len(payloads),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        workbook.close()
