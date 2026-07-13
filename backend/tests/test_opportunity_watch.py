"""
模块：国能 e 招计划追踪测试
用途：验收公告详情地址安全构造与正文截止时间解析（任务1，纯本地无网络）。
对接：chnenergy_client；fixtures/chnenergy_notice_*.html；后续 opportunity_watch 同步链路。
二次开发：禁止真实 HTTP；扩展检索/同步用例时仍须阻断外网并保持字段白名单。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
