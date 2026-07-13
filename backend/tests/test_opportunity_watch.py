"""
模块：国能 e 招计划追踪测试
用途：验收公告详情地址安全、正文截止时间解析，以及本机计划表 .xlsx 受控导入。
对接：chnenergy_client；opportunity_watch_service；fixtures/chnenergy_notice_*.html。
二次开发：禁止真实 HTTP；扩展检索/同步用例时仍须阻断外网并保持字段白名单。
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from pydantic import ValidationError

from app.api.schemas import (
    OpportunityWatchAcceptOut,
    OpportunityWatchHitOut,
    OpportunityWatchPlanImportOut,
    OpportunityWatchPlanOut,
    OpportunityWatchSyncRunOut,
)
from app.core.config import Settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    BidOpportunityRow,
    BidSourceHitRow,
    BidSourceSyncRunRow,
    BidWatchPlanRow,
    Workspace,
)
from app.services import opportunity_watch_service

# 任务2 固定错误码字典；非法值必须被 ORM 约束与读模型同时拒绝。
WATCH_ERROR_CODES = (
    "source_unavailable",
    "rate_limited",
    "malformed_response",
    "interrupted",
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_detail_url_builds_fixed_https_static_path():
    from app.services.chnenergy_client import build_notice_detail_url

    url = build_notice_detail_url(
        infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
        categorynum="001002001",
        infodate="20260709",
    )
    assert url == (
        "https://www.chnenergybidding.com.cn/bidweb/"
        "001/001002/001002001/20260709/"
        "b2363623-ea1e-4cc1-8e2d-0c2d2850b697.html"
    )


def test_detail_url_from_jump_query_fields():
    from app.services.chnenergy_client import build_notice_detail_url_from_jump

    url = build_notice_detail_url_from_jump(
        "jump.html?infoid=b2363623-ea1e-4cc1-8e2d-0c2d2850b697"
        "&categorynum=001002001&infodate=20260709"
    )
    assert url.endswith(
        "/bidweb/001/001002/001002001/20260709/"
        "b2363623-ea1e-4cc1-8e2d-0c2d2850b697.html"
    )
    assert url.startswith("https://www.chnenergybidding.com.cn/")


def test_detail_url_rejects_external_host():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url_from_jump,
    )

    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url_from_jump(
            "https://evil.example/jump.html?"
            "infoid=b2363623-ea1e-4cc1-8e2d-0c2d2850b697"
            "&categorynum=001002001&infodate=20260709"
        )


def test_detail_url_rejects_non_uuid_infoid():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url,
    )

    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url(
            infoid="not-a-uuid",
            categorynum="001002001",
            infodate="20260709",
        )


def test_detail_url_rejects_non_eight_digit_date():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url,
    )

    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url(
            infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
            categorynum="001002001",
            infodate="2026-07-09",
        )


def test_detail_url_rejects_non_bid_category():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url,
    )

    # 中标结果等非 001002 前缀类别
    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url(
            infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
            categorynum="001006001",
            infodate="20260709",
        )


def test_detail_url_six_digit_category_path_segments():
    """6 位类别按每 3 位追加：/001/001002/日期/id.html，不得重复整段。"""
    from app.services.chnenergy_client import build_notice_detail_url

    url = build_notice_detail_url(
        infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
        categorynum="001002",
        infodate="20260709",
    )
    assert url == (
        "https://www.chnenergybidding.com.cn/bidweb/"
        "001/001002/20260709/"
        "b2363623-ea1e-4cc1-8e2d-0c2d2850b697.html"
    )


def test_detail_url_twelve_digit_category_path_segments():
    from app.services.chnenergy_client import build_notice_detail_url

    url = build_notice_detail_url(
        infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
        categorynum="001002001001",
        infodate="20260709",
    )
    assert url == (
        "https://www.chnenergybidding.com.cn/bidweb/"
        "001/001002/001002001/001002001001/20260709/"
        "b2363623-ea1e-4cc1-8e2d-0c2d2850b697.html"
    )


def test_detail_url_rejects_impossible_calendar_date():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url,
    )

    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url(
            infoid="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
            categorynum="001002001",
            infodate="20260231",
        )


def test_detail_url_rejects_http_scheme_jump():
    from app.services.chnenergy_client import (
        ChnenergyClientError,
        build_notice_detail_url_from_jump,
    )

    with pytest.raises(ChnenergyClientError):
        build_notice_detail_url_from_jump(
            "http://www.chnenergybidding.com.cn/jump.html?"
            "infoid=b2363623-ea1e-4cc1-8e2d-0c2d2850b697"
            "&categorynum=001002001&infodate=20260709"
        )


def test_extract_invalid_calendar_time_needs_review():
    from app.services.chnenergy_client import extract_notice_times

    html = (
        "<html><body><p>投标文件递交的截止时间（投标截止时间，下同）及开标时间为 "
        "2026-02-30 09:00:00（北京时间）。</p></body></html>"
    )
    result = extract_notice_times(html)
    assert result["extraction_status"] == "needs_review"
    assert result.get("deadline_at_local") in (None, "")


def test_extract_conflicting_deadlines_needs_review():
    from app.services.chnenergy_client import extract_notice_times

    html = (
        "<html><body>"
        "<p>投标截止时间为 2026-07-29 09:00:00。</p>"
        "<p>投标截止时间为 2026-07-30 10:00:00。</p>"
        "</body></html>"
    )
    result = extract_notice_times(html)
    assert result["extraction_status"] == "needs_review"
    assert result.get("deadline_at_local") in (None, "")


def test_extract_separate_deadline_and_opening_clauses():
    """计划 §2.3：独立「投标截止时间为」「开标时间为」条款兼容。"""
    from app.services.chnenergy_client import extract_notice_times

    html = (
        "<html><body><p>"
        "投标截止时间为 2026-07-29 09:00:00；开标时间为 2026-07-29 09:30:00。"
        "</p></body></html>"
    )
    result = extract_notice_times(html)
    assert result["extraction_status"] == "resolved"
    assert result["deadline_at_local"] == "2026-07-29 09:00:00"
    assert result["opening_at_local"] == "2026-07-29 09:30:00"
    assert result["source_timezone"] == "Asia/Shanghai"


def test_extract_deadline_and_opening_from_fixture():
    from app.services.chnenergy_client import extract_notice_times

    html = (FIXTURES / "chnenergy_notice_deadline.html").read_text(encoding="utf-8")
    result = extract_notice_times(html)

    assert result["extraction_status"] == "resolved"
    assert result["deadline_at_local"] == "2026-07-29 09:00:00"
    assert result["opening_at_local"] == "2026-07-29 09:00:00"
    assert result["source_timezone"] == "Asia/Shanghai"
    # 不得采信 script/注释中的假时间
    assert "2099" not in (result["deadline_at_local"] or "")
    assert "2020" not in (result["deadline_at_local"] or "")


def test_extract_needs_review_when_deadline_missing():
    from app.services.chnenergy_client import extract_notice_times

    html = (FIXTURES / "chnenergy_notice_needs_review.html").read_text(
        encoding="utf-8"
    )
    result = extract_notice_times(html)

    assert result["extraction_status"] == "needs_review"
    assert result.get("deadline_at_local") in (None, "")
    assert result["source_timezone"] == "Asia/Shanghai"


def _create_watch_workspace(workspace_id: str) -> None:
    """用途：创建独立工作空间，供计划追踪的归属隔离回归测试使用。"""
    db = SessionLocal()
    try:
        db.add(
            Workspace(
                id=workspace_id,
                name="计划追踪隔离工作空间",
                owner_user_id="user_watch_other",
            )
        )
        db.commit()
    finally:
        db.close()


def _watch_plan(workspace_id: str, plan_id: str, fingerprint: str) -> BidWatchPlanRow:
    """用途：构造最小追踪计划实体，避免测试重复填写与业务无关字段。"""
    return BidWatchPlanRow(
        id=plan_id,
        workspace_id=workspace_id,
        title="某项目招标计划",
        buyer="某招标人",
        scope="建设范围",
        duration="12 个月",
        expected_publish_text="2026 年 7 月",
        remark="测试备注",
        fingerprint=fingerprint,
        enabled=True,
    )


def test_watch_tables_are_created_and_keep_workspace_isolation():
    """用途：验收三张追踪表建表、唯一键与服务读取的工作空间边界。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert {"bid_watch_plans", "bid_source_sync_runs", "bid_source_hits"} <= table_names
    assert BidOpportunityRow.__tablename__ == "bid_opportunities"

    other_workspace_id = "ws_watch_other"
    _create_watch_workspace(other_workspace_id)
    db = SessionLocal()
    try:
        local_plan = _watch_plan("ws_local", "watch_plan_local", "fp-local")
        other_plan = _watch_plan(other_workspace_id, "watch_plan_other", "fp-other")
        db.add_all([local_plan, other_plan])
        db.commit()

        assert [item.id for item in opportunity_watch_service.list_watch_plans(db, "ws_local")] == [
            "watch_plan_local"
        ]
        assert opportunity_watch_service.list_watch_plans(db, other_workspace_id)[0].id == "watch_plan_other"

        db.add(_watch_plan("ws_local", "watch_plan_duplicate", "fp-local"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_lifespan_marks_interrupted_runs_failed_and_keeps_hits():
    """用途：验收应用启动后的未完成运行恢复不删除已写入的命中记录。"""
    db = SessionLocal()
    try:
        plan = _watch_plan("ws_local", "watch_plan_recovery", "fp-recovery")
        queued = BidSourceSyncRunRow(
            id="watch_run_queued",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="queued",
            started_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        running = BidSourceSyncRunRow(
            id="watch_run_running",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="running",
            started_at=datetime(2026, 7, 13, 1, tzinfo=timezone.utc),
        )
        hit = BidSourceHitRow(
            id="watch_hit_recovery",
            workspace_id="ws_local",
            watch_plan_id=plan.id,
            sync_run_id=queued.id,
            source_name="chnenergy",
            source_info_id="b2363623-ea1e-4cc1-8e2d-0c2d2850b697",
            category_num="001002001",
            source_publish_text="2026-07-09 17:14:11",
            title="某项目招标公告",
            deadline_at_local="2026-07-29 09:00:00",
            opening_at_local="2026-07-29 09:00:00",
            source_timezone="Asia/Shanghai",
            extraction_status="resolved",
        )
        db.add_all([plan, queued, running])
        # 先写入被命中的计划和运行记录，明确外键父记录的落库顺序。
        db.flush()
        db.add(hit)
        db.commit()
    finally:
        db.close()

    # lifespan 必须调用恢复服务，而不是仅让 service 单测通过。
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    db = SessionLocal()
    try:
        runs = {run.id: run for run in opportunity_watch_service.list_watch_runs(db, "ws_local")}
        assert runs["watch_run_queued"].status == "failed"
        assert runs["watch_run_running"].status == "failed"
        assert runs["watch_run_queued"].error_code == "interrupted"
        assert runs["watch_run_running"].error_code == "interrupted"
        assert runs["watch_run_queued"].finished_at is not None
        assert [item.id for item in opportunity_watch_service.list_watch_hits(db, "ws_local")] == [
            "watch_hit_recovery"
        ]
    finally:
        db.close()


def test_watch_schemas_and_settings_keep_sensitive_fields_outside_contract():
    """用途：冻结追踪读模型和服务端限额，防止 URL/Cookie/原文进入 API 契约。"""
    schema_fields = set()
    for schema in (
        OpportunityWatchPlanOut,
        OpportunityWatchSyncRunOut,
        OpportunityWatchHitOut,
        OpportunityWatchAcceptOut,
    ):
        schema_fields.update(schema.model_fields)
    lowered = {field.lower() for field in schema_fields}
    assert not any(
        forbidden in field
        for field in lowered
        for forbidden in ("cookie", "html", "raw", "response", "url")
    )
    assert {"fingerprint", "error_code", "deadline_at_local", "source_timezone"} <= schema_fields

    settings = Settings()
    # 任务2 固定默认上限：2 MiB、120、5、50、1s、5s、15s、重试 1。
    assert settings.max_opportunity_watch_import_bytes == 2 * 1024 * 1024
    assert settings.max_opportunity_watch_plan_rows == 120
    assert settings.max_opportunity_watch_plans_per_sync == 120
    assert settings.max_opportunity_watch_candidates_per_plan == 5
    assert settings.max_opportunity_watch_detail_pages_per_sync == 50
    assert settings.opportunity_watch_min_interval_seconds == 1
    assert settings.opportunity_watch_connect_timeout_seconds == 5
    assert settings.opportunity_watch_read_timeout_seconds == 15
    assert settings.opportunity_watch_search_retry_count == 1


def test_sync_run_error_code_rejects_unknown_values():
    """用途：验收同步运行 error_code 仅允许固定字典或 NULL，非法值被数据库拒绝。"""
    db = SessionLocal()
    try:
        allowed = BidSourceSyncRunRow(
            id="watch_run_error_ok",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="failed",
            started_at=datetime(2026, 7, 13, 2, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 13, 2, 1, tzinfo=timezone.utc),
            error_code="source_unavailable",
        )
        empty = BidSourceSyncRunRow(
            id="watch_run_error_null",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="queued",
            started_at=datetime(2026, 7, 13, 2, 2, tzinfo=timezone.utc),
            error_code=None,
        )
        db.add_all([allowed, empty])
        db.commit()

        for code in WATCH_ERROR_CODES:
            row = BidSourceSyncRunRow(
                id=f"watch_run_error_{code}",
                workspace_id="ws_local",
                source_name="chnenergy",
                status="failed",
                started_at=datetime(2026, 7, 13, 3, tzinfo=timezone.utc),
                finished_at=datetime(2026, 7, 13, 3, 1, tzinfo=timezone.utc),
                error_code=code,
            )
            db.add(row)
            db.commit()

        db.add(
            BidSourceSyncRunRow(
                id="watch_run_error_illegal",
                workspace_id="ws_local",
                source_name="chnenergy",
                status="failed",
                started_at=datetime(2026, 7, 13, 4, tzinfo=timezone.utc),
                finished_at=datetime(2026, 7, 13, 4, 1, tzinfo=timezone.utc),
                error_code="remote_timeout_stacktrace",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_sync_run_out_error_code_schema_is_fixed_dictionary():
    """用途：验收读模型 error_code 收紧为固定字典或 None，拒绝远端原文类字符串。"""
    base = {
        "id": "watch_run_schema",
        "workspace_id": "ws_local",
        "source_name": "chnenergy",
        "status": "failed",
        "started_at": datetime(2026, 7, 13, 5, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 7, 13, 5, 1, tzinfo=timezone.utc),
        "plan_count": 0,
        "candidate_count": 0,
        "detail_page_count": 0,
        "resolved_count": 0,
        "needs_review_count": 0,
        "skipped_count": 0,
        "created_at": datetime(2026, 7, 13, 5, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 13, 5, 1, tzinfo=timezone.utc),
    }
    assert OpportunityWatchSyncRunOut(**base, error_code=None).error_code is None
    for code in WATCH_ERROR_CODES:
        assert OpportunityWatchSyncRunOut(**base, error_code=code).error_code == code
    with pytest.raises(ValidationError):
        OpportunityWatchSyncRunOut(**base, error_code="HTTPError: 500 Internal Server Error")


def test_list_watch_runs_and_hits_keep_workspace_isolation():
    """用途：验收 list_watch_runs / list_watch_hits 严格按 workspace_id 过滤，跨空间不可见。"""
    other_workspace_id = "ws_watch_list_other"
    _create_watch_workspace(other_workspace_id)
    db = SessionLocal()
    try:
        local_plan = _watch_plan("ws_local", "watch_plan_list_local", "fp-list-local")
        other_plan = _watch_plan(other_workspace_id, "watch_plan_list_other", "fp-list-other")
        local_run = BidSourceSyncRunRow(
            id="watch_run_list_local",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="succeeded",
            started_at=datetime(2026, 7, 13, 6, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 13, 6, 1, tzinfo=timezone.utc),
            error_code=None,
        )
        other_run = BidSourceSyncRunRow(
            id="watch_run_list_other",
            workspace_id=other_workspace_id,
            source_name="chnenergy",
            status="failed",
            started_at=datetime(2026, 7, 13, 6, 2, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 13, 6, 3, tzinfo=timezone.utc),
            error_code="rate_limited",
        )
        db.add_all([local_plan, other_plan, local_run, other_run])
        db.flush()
        local_hit = BidSourceHitRow(
            id="watch_hit_list_local",
            workspace_id="ws_local",
            watch_plan_id=local_plan.id,
            sync_run_id=local_run.id,
            source_name="chnenergy",
            source_info_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            category_num="001002001",
            source_publish_text="2026-07-09 10:00:00",
            title="本空间命中",
            deadline_at_local="2026-07-29 09:00:00",
            opening_at_local="2026-07-29 09:00:00",
            source_timezone="Asia/Shanghai",
            extraction_status="resolved",
        )
        other_hit = BidSourceHitRow(
            id="watch_hit_list_other",
            workspace_id=other_workspace_id,
            watch_plan_id=other_plan.id,
            sync_run_id=other_run.id,
            source_name="chnenergy",
            source_info_id="ffffffff-1111-2222-3333-444444444444",
            category_num="001002001",
            source_publish_text="2026-07-09 11:00:00",
            title="他空间命中",
            deadline_at_local=None,
            opening_at_local=None,
            source_timezone="Asia/Shanghai",
            extraction_status="needs_review",
        )
        db.add_all([local_hit, other_hit])
        db.commit()

        assert [item.id for item in opportunity_watch_service.list_watch_runs(db, "ws_local")] == [
            "watch_run_list_local"
        ]
        assert [
            item.id for item in opportunity_watch_service.list_watch_runs(db, other_workspace_id)
        ] == ["watch_run_list_other"]
        assert [item.id for item in opportunity_watch_service.list_watch_hits(db, "ws_local")] == [
            "watch_hit_list_local"
        ]
        assert [
            item.id for item in opportunity_watch_service.list_watch_hits(db, other_workspace_id)
        ] == ["watch_hit_list_other"]
        # 空/未知工作空间必须返回空列表，不得泄漏其他空间数据。
        assert opportunity_watch_service.list_watch_runs(db, "ws_missing") == []
        assert opportunity_watch_service.list_watch_hits(db, "ws_missing") == []
    finally:
        db.close()


def _make_hit(
    *,
    hit_id: str,
    workspace_id: str,
    watch_plan_id: str,
    sync_run_id: str,
    source_info_id: str,
) -> BidSourceHitRow:
    """用途：构造最小命中实体，便于跨空间外键拒绝回归。"""
    return BidSourceHitRow(
        id=hit_id,
        workspace_id=workspace_id,
        watch_plan_id=watch_plan_id,
        sync_run_id=sync_run_id,
        source_name="chnenergy",
        source_info_id=source_info_id,
        category_num="001002001",
        source_publish_text="2026-07-09 12:00:00",
        title="跨空间关系约束样本",
        deadline_at_local="2026-07-29 09:00:00",
        opening_at_local="2026-07-29 09:00:00",
        source_timezone="Asia/Shanghai",
        extraction_status="resolved",
    )


def test_hit_rejects_cross_workspace_plan_or_run_reference():
    """
    用途：验收命中、计划、运行必须同属一个 workspace；跨空间计划或运行引用被数据库拒绝。
    对接：BidSourceHitRow 复合外键；(workspace_id, watch_plan_id)/(workspace_id, sync_run_id)。
    二次开发：不得仅靠服务层过滤；非法组合必须在 ORM/SQLite 层失败。
    """
    other_workspace_id = "ws_watch_fk_other"
    _create_watch_workspace(other_workspace_id)
    db = SessionLocal()
    try:
        local_plan = _watch_plan("ws_local", "watch_plan_fk_local", "fp-fk-local")
        other_plan = _watch_plan(other_workspace_id, "watch_plan_fk_other", "fp-fk-other")
        local_run = BidSourceSyncRunRow(
            id="watch_run_fk_local",
            workspace_id="ws_local",
            source_name="chnenergy",
            status="succeeded",
            started_at=datetime(2026, 7, 13, 7, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 13, 7, 1, tzinfo=timezone.utc),
        )
        other_run = BidSourceSyncRunRow(
            id="watch_run_fk_other",
            workspace_id=other_workspace_id,
            source_name="chnenergy",
            status="succeeded",
            started_at=datetime(2026, 7, 13, 7, 2, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 13, 7, 3, tzinfo=timezone.utc),
        )
        db.add_all([local_plan, other_plan, local_run, other_run])
        db.flush()

        # 合法：命中、计划、运行均属 ws_local。
        db.add(
            _make_hit(
                hit_id="watch_hit_fk_ok",
                workspace_id="ws_local",
                watch_plan_id=local_plan.id,
                sync_run_id=local_run.id,
                source_info_id="11111111-2222-3333-4444-555555555555",
            )
        )
        db.commit()

        # 计划跨空间：hit.workspace=ws_local，但 watch_plan 属于 other。
        db.add(
            _make_hit(
                hit_id="watch_hit_fk_bad_plan",
                workspace_id="ws_local",
                watch_plan_id=other_plan.id,
                sync_run_id=local_run.id,
                source_info_id="aaaaaaaa-bbbb-cccc-dddd-000000000001",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # 运行跨空间：hit.workspace=ws_local，但 sync_run 属于 other。
        db.add(
            _make_hit(
                hit_id="watch_hit_fk_bad_run",
                workspace_id="ws_local",
                watch_plan_id=local_plan.id,
                sync_run_id=other_run.id,
                source_info_id="aaaaaaaa-bbbb-cccc-dddd-000000000002",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


# ---------- P9B 任务3：计划表 .xlsx 受控导入 ----------

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PLAN_HEADERS = (
    "招标计划名称",
    "招标人",
    "范围",
    "计划工期",
    "预计发布公告时间",
    "备注",
)


def _workbook_bytes(
    rows: list[list[object]],
    *,
    header_row_index: int = 3,
    headers: tuple[str, ...] | list[str] = _PLAN_HEADERS,
) -> bytes:
    """用途：在内存构造国能计划表样式工作簿，供导入契约测试使用，不落盘。"""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    # 前两行说明文字，表头默认落在第 3 行，与日常计划表一致。
    for idx in range(1, header_row_index):
        ws.cell(idx, 1, f"说明行 {idx}")
    for col, name in enumerate(headers, start=1):
        ws.cell(header_row_index, col, name)
    for offset, row in enumerate(rows):
        for col, value in enumerate(row, start=1):
            ws.cell(header_row_index + 1 + offset, col, value)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _post_plan_import(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "plans.xlsx",
    workspace_id: str | None = None,
):
    """用途：统一调用计划导入路由，可选切换工作空间请求头。"""
    headers = {}
    if workspace_id is not None:
        headers["X-Workspace-Id"] = workspace_id
    return client.post(
        "/api/opportunity-watch/plans/import",
        files={"file": (filename, content, _XLSX_MIME)},
        headers=headers,
    )


def _count_watch_plans(workspace_id: str) -> int:
    """用途：统计指定工作空间已写入的追踪计划数量。"""
    db = SessionLocal()
    try:
        return len(
            list(
                db.scalars(
                    select(BidWatchPlanRow).where(BidWatchPlanRow.workspace_id == workspace_id)
                ).all()
            )
        )
    finally:
        db.close()


def test_plan_import_out_schema_only_exposes_counts():
    """用途：冻结导入成功响应只含 inserted/skipped/total，禁止文件与远端字段。"""
    fields = set(OpportunityWatchPlanImportOut.model_fields)
    assert fields == {"inserted", "skipped", "total"}
    lowered = {name.lower() for name in fields}
    assert not any(
        forbidden in name
        for name in lowered
        for forbidden in ("cookie", "url", "path", "file", "html", "raw")
    )
    assert OpportunityWatchPlanImportOut(inserted=2, skipped=0, total=2).model_dump() == {
        "inserted": 2,
        "skipped": 0,
        "total": 2,
    }


def test_plan_import_service_inserts_and_skips_duplicates_on_reimport():
    """用途：验收表头在前十行、有效计划写入，以及重复导入按指纹跳过。"""
    content = _workbook_bytes(
        [
            ["计划甲", "招标人甲", "范围甲", "12个月", "2026年7月", "备注甲"],
            ["计划乙", "招标人乙", "范围乙", "6个月", "2026年8月", "备注乙"],
        ]
    )
    db = SessionLocal()
    try:
        first = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            "ws_local",
            filename="plans.xlsx",
            content=content,
            max_rows=120,
        )
        assert first == {"inserted": 2, "skipped": 0, "total": 2}
        second = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            "ws_local",
            filename="plans.xlsx",
            content=content,
            max_rows=120,
        )
        assert second == {"inserted": 0, "skipped": 2, "total": 2}
        plans = opportunity_watch_service.list_watch_plans(db, "ws_local")
        assert len(plans) == 2
        titles = {item.title for item in plans}
        assert titles == {"计划甲", "计划乙"}
        assert all(item.fingerprint for item in plans)
    finally:
        db.close()


def test_plan_import_service_rejects_missing_title_header_with_zero_write():
    """用途：缺少招标计划名称表头时整批零写入。"""
    content = _workbook_bytes(
        [["计划甲", "招标人甲", "范围甲", "12个月", "2026年7月", "备注"]],
        headers=("计划名称", "招标人", "范围", "计划工期", "预计发布公告时间", "备注"),
    )
    db = SessionLocal()
    try:
        with pytest.raises(opportunity_watch_service.WatchPlanImportValidationError) as exc_info:
            opportunity_watch_service.import_watch_plans_from_xlsx(
                db,
                "ws_local",
                filename="plans.xlsx",
                content=content,
                max_rows=120,
            )
        assert exc_info.value.errors
        assert any("招标计划名称" in str(item.get("message", "")) for item in exc_info.value.errors)
        assert _count_watch_plans("ws_local") == 0
    finally:
        db.close()


def test_plan_import_service_rejects_blank_title_on_partial_row():
    """用途：非空行缺计划名返回 Excel 实际行号，整批零写入；全空白行仍跳过。"""
    content = _workbook_bytes(
        [
            ["", "招标人甲", "范围甲", "", "", ""],
            [None, None, None, None, None, None],
            ["计划乙", "招标人乙", "范围乙", "6个月", "2026年8月", "备注乙"],
        ]
    )
    db = SessionLocal()
    try:
        with pytest.raises(opportunity_watch_service.WatchPlanImportValidationError) as exc_info:
            opportunity_watch_service.import_watch_plans_from_xlsx(
                db,
                "ws_local",
                filename="plans.xlsx",
                content=content,
                max_rows=120,
            )
        # 表头在第 3 行，首条数据为 Excel 第 4 行。
        assert any(
            item.get("row") == 4 and item.get("field") == "招标计划名称"
            for item in exc_info.value.errors
        )
        assert _count_watch_plans("ws_local") == 0
    finally:
        db.close()


def test_plan_import_service_rejects_when_plan_rows_exceed_limit():
    """用途：超过 max_rows 计划行时拒绝写入。"""
    rows = [[f"计划{i}", "招标人", "范围", "1个月", "2026年7月", ""] for i in range(121)]
    content = _workbook_bytes(rows)
    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match="120"):
            opportunity_watch_service.import_watch_plans_from_xlsx(
                db,
                "ws_local",
                filename="plans.xlsx",
                content=content,
                max_rows=120,
            )
        assert _count_watch_plans("ws_local") == 0
    finally:
        db.close()


def test_plan_import_service_stops_iterating_after_max_rows(monkeypatch: pytest.MonkeyPatch):
    """用途：超过 max_rows 时立即停止枚举行，禁止先物化全表再截断。"""
    max_rows = 3
    total_data_rows = 80
    yielded = {"count": 0}

    header = list(_PLAN_HEADERS)
    data_rows = [
        [f"计划{i}", "招标人", "范围", "1个月", "2026年7月", ""] for i in range(total_data_rows)
    ]

    class _CountingSheet:
        """可控假工作表：记录 iter_rows 实际产出次数。"""

        def iter_rows(self, *args, **kwargs):
            yielded["count"] += 1
            yield header
            for row in data_rows:
                yielded["count"] += 1
                yield row

    class _CountingWorkbook:
        worksheets = [_CountingSheet()]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        opportunity_watch_service,
        "load_workbook",
        lambda *args, **kwargs: _CountingWorkbook(),
    )

    db = SessionLocal()
    try:
        with pytest.raises(ValueError, match=str(max_rows)):
            opportunity_watch_service.import_watch_plans_from_xlsx(
                db,
                "ws_local",
                filename="plans.xlsx",
                content=b"fake-xlsx-bytes",
                max_rows=max_rows,
            )
        # 表头 1 行 + 上限内计划行 + 触发上限的第 max_rows+1 行，不得继续枚举剩余行。
        assert yielded["count"] == 1 + max_rows + 1
        assert yielded["count"] < total_data_rows
        assert _count_watch_plans("ws_local") == 0
    finally:
        db.close()


def test_plan_import_service_skips_duplicate_fingerprints_in_same_batch():
    """用途：同批重复计划名+招标人+范围只计 skipped，不得产生重复行。"""
    content = _workbook_bytes(
        [
            ["计划甲", "招标人甲", "范围甲", "12个月", "2026年7月", "备注1"],
            ["计划甲", "招标人甲", "范围甲", "24个月", "2026年8月", "备注2"],
        ]
    )
    db = SessionLocal()
    try:
        result = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            "ws_local",
            filename="plans.xlsx",
            content=content,
            max_rows=120,
        )
        assert result == {"inserted": 1, "skipped": 1, "total": 2}
        plans = opportunity_watch_service.list_watch_plans(db, "ws_local")
        assert len(plans) == 1
        assert plans[0].title == "计划甲"
        assert plans[0].duration == "12个月"
    finally:
        db.close()


def test_plan_import_service_allows_same_plan_across_workspaces():
    """用途：跨工作空间允许各自导入同一计划指纹。"""
    other_workspace_id = "ws_watch_import_other"
    _create_watch_workspace(other_workspace_id)
    content = _workbook_bytes(
        [["共享计划", "共享招标人", "共享范围", "12个月", "2026年7月", "备注"]]
    )
    db = SessionLocal()
    try:
        local = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            "ws_local",
            filename="plans.xlsx",
            content=content,
            max_rows=120,
        )
        other = opportunity_watch_service.import_watch_plans_from_xlsx(
            db,
            other_workspace_id,
            filename="plans.xlsx",
            content=content,
            max_rows=120,
        )
        assert local == {"inserted": 1, "skipped": 0, "total": 1}
        assert other == {"inserted": 1, "skipped": 0, "total": 1}
        assert _count_watch_plans("ws_local") == 1
        assert _count_watch_plans(other_workspace_id) == 1
    finally:
        db.close()


def test_plan_import_api_success_and_reimport(client: TestClient):
    """用途：验收导入路由 201 响应契约与重复上传跳过。"""
    content = _workbook_bytes(
        [
            ["计划甲", "招标人甲", "范围甲", "12个月", "2026年7月", "备注甲"],
            ["计划乙", "招标人乙", "范围乙", "6个月", "2026年8月", "备注乙"],
        ]
    )
    first = _post_plan_import(client, content)
    assert first.status_code == 201
    assert first.json() == {"inserted": 2, "skipped": 0, "total": 2}
    second = _post_plan_import(client, content)
    assert second.status_code == 201
    assert second.json() == {"inserted": 0, "skipped": 2, "total": 2}
    assert _count_watch_plans("ws_local") == 2


def test_plan_import_api_isolates_workspaces(client: TestClient):
    """用途：验收跨工作空间导入互不影响。"""
    other_workspace_id = "ws_watch_api_other"
    _create_watch_workspace(other_workspace_id)
    content = _workbook_bytes(
        [["跨空间计划", "招标人", "范围", "12个月", "2026年7月", ""]]
    )
    local = _post_plan_import(client, content)
    other = _post_plan_import(client, content, workspace_id=other_workspace_id)
    assert local.status_code == 201
    assert other.status_code == 201
    assert local.json() == {"inserted": 1, "skipped": 0, "total": 1}
    assert other.json() == {"inserted": 1, "skipped": 0, "total": 1}
    assert _count_watch_plans("ws_local") == 1
    assert _count_watch_plans(other_workspace_id) == 1


def test_plan_import_api_validation_errors_zero_write(client: TestClient):
    """用途：缺表头与错误数据行返回 422，当前工作空间零写入。"""
    missing_header = _workbook_bytes(
        [["计划甲", "招标人", "范围", "1个月", "2026年7月", ""]],
        headers=("计划名称", "招标人", "范围", "计划工期", "预计发布公告时间", "备注"),
    )
    resp = _post_plan_import(client, missing_header)
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert _count_watch_plans("ws_local") == 0

    blank_title = _workbook_bytes(
        [["", "招标人甲", "范围甲", "1个月", "2026年7月", "备注"]]
    )
    resp2 = _post_plan_import(client, blank_title)
    assert resp2.status_code == 422
    detail = resp2.json()["detail"]
    errors = detail["errors"] if isinstance(detail, dict) else detail
    assert any(
        (item.get("row") == 4 and item.get("field") == "招标计划名称")
        for item in errors
    )
    assert _count_watch_plans("ws_local") == 0


def test_plan_import_api_rejects_non_xlsx_and_oversized(client: TestClient):
    """用途：非 .xlsx 扩展名与超 2MiB 文件返回 400，且不写入计划。"""
    valid_content = _workbook_bytes(
        [["计划甲", "招标人", "范围", "1个月", "2026年7月", ""]]
    )
    for filename in ("plans.csv", "plans.json", "plans.xlsx.exe", "plans"):
        resp = _post_plan_import(client, valid_content, filename=filename)
        assert resp.status_code == 400, filename
    assert _count_watch_plans("ws_local") == 0

    oversized = b"A" * (2 * 1024 * 1024 + 1)
    resp = _post_plan_import(client, oversized, filename="plans.xlsx")
    assert resp.status_code == 400
    assert _count_watch_plans("ws_local") == 0
