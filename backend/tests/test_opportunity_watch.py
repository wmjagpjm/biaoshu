"""
模块：国能 e 招计划追踪测试
用途：验收公告详情地址安全构造与正文截止时间解析（任务1，纯本地无网络）。
对接：chnenergy_client；fixtures/chnenergy_notice_*.html；后续 opportunity_watch 同步链路。
二次开发：禁止真实 HTTP；扩展检索/同步用例时仍须阻断外网并保持字段白名单。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from pydantic import ValidationError

from app.api.schemas import (
    OpportunityWatchAcceptOut,
    OpportunityWatchHitOut,
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
