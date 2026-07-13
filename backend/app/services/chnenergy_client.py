"""
模块：国能 e 招受控客户端（解析与地址安全）
用途：从 jump 字段安全重建固定 HTTPS 公告详情地址；从公告 HTML 可见正文提取投标截止/开标时间。
对接：后续 opportunity_watch_service 同步任务；test_opportunity_watch 与 HTML fixture。
二次开发：主机与路径规则固定在国能 e 招；禁止把本模块泛化为任意 URL 抓取；不得落库 Cookie/原文 HTML。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

# 唯一允许的公告主机（不含协议与路径）
_ALLOWED_HOST = "www.chnenergybidding.com.cn"
_CATEGORY_BID_PREFIX = "001002"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DATE8_RE = re.compile(r"^\d{8}$")
# 类别号：3 位一组，长度 6/9/12
_CATEGORY_RE = re.compile(r"^(?:\d{3}){2,4}$")

# 完整本地时间：年-月-日 时:分[:秒]
_FULL_TIME_RE = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
    r"[\sT]+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{1,2})(?::(?P<s>\d{1,2}))?"
)

# 优先联合条款，再兼容独立条款（在可见文本上匹配，非整页 HTML）
_COMBINED_CLAUSE_RE = re.compile(
    r"投标文件递交的截止时间（投标截止时间，下同）及开标时间为"
    r"(?P<body>.{0,120}?)(?=(?:。|\.|$|；|;|其他|请|本))"
)
_DEADLINE_ONLY_RE = re.compile(
    r"投标截止时间为(?P<body>.{0,80}?)(?=(?:。|\.|$|；|;|开标|其他|请|本))"
)
_OPENING_ONLY_RE = re.compile(
    r"开标时间为(?P<body>.{0,80}?)(?=(?:。|\.|$|；|;|其他|请|本))"
)

# 不收集可见文本的标签
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


class ChnenergyClientError(ValueError):
    """用途：国能 e 招地址字段或跳转链接校验失败时的受控错误。"""


class _VisibleTextExtractor(HTMLParser):
    """用途：仅收集页面可见文本，跳过 script/style 与标签属性。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._chunks.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._chunks))
        # 折叠空白，便于条款匹配
        return re.sub(r"\s+", " ", raw).strip()


def _validate_infoid(infoid: str) -> str:
    value = (infoid or "").strip()
    if not _UUID_RE.fullmatch(value):
        raise ChnenergyClientError("公告 ID 必须是 UUID")
    try:
        UUID(value)
    except ValueError as exc:
        raise ChnenergyClientError("公告 ID 必须是 UUID") from exc
    return value.lower()


def _validate_categorynum(categorynum: str) -> str:
    value = (categorynum or "").strip()
    if not _CATEGORY_RE.fullmatch(value):
        raise ChnenergyClientError("类别号非法（须为 6/9/12 位且按 3 位分组）")
    if not value.startswith(_CATEGORY_BID_PREFIX):
        raise ChnenergyClientError("仅允许招标公告类别（001002 前缀）")
    return value


def _category_path_segments(category: str) -> list[str]:
    """用途：按每 3 位追加生成目录段，如 001002001 → [001, 001002, 001002001]。"""
    segments: list[str] = []
    for end in range(3, len(category) + 1, 3):
        segments.append(category[:end])
    return segments


def _validate_infodate(infodate: str) -> str:
    value = (infodate or "").strip()
    if not _DATE8_RE.fullmatch(value):
        raise ChnenergyClientError("发布日期必须是八位数字 YYYYMMDD")
    year = int(value[0:4])
    month = int(value[4:6])
    day = int(value[6:8])
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ChnenergyClientError("发布日期不是真实日历日期") from exc
    if year < 2000 or year > 2100:
        raise ChnenergyClientError("发布日期超出合理范围")
    return value


def build_notice_detail_url(
    *,
    infoid: str,
    categorynum: str,
    infodate: str,
) -> str:
    """
    用途：用已校验的 jump 字段重建固定 HTTPS 静态公告详情地址。
    对接：同步任务读取详情页前；命中响应中的 announcementUrl 生成。
    路径：/bidweb/{每3位累积段...}/{发布日期}/{公告ID}.html
    """
    info_id = _validate_infoid(infoid)
    category = _validate_categorynum(categorynum)
    publish_date = _validate_infodate(infodate)
    segments = "/".join(_category_path_segments(category))
    return (
        f"https://{_ALLOWED_HOST}/bidweb/"
        f"{segments}/{publish_date}/{info_id}.html"
    )


def build_notice_detail_url_from_jump(jump: str) -> str:
    """
    用途：从 jump 相对路径或完整链接解析查询字段后重建详情地址。
    对接：检索候选中的 linkurl；外部主机必须受控拒绝。
    """
    raw = (jump or "").strip()
    if not raw:
        raise ChnenergyClientError("jump 链接为空")

    # 相对 jump.html?... 补全为仅用于解析的伪 URL
    if "://" not in raw:
        parsed = urlparse("https://placeholder.local/" + raw.lstrip("/"))
        host_ok = True
    else:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host != _ALLOWED_HOST:
            raise ChnenergyClientError("禁止外部主机")
        if (parsed.scheme or "").lower() != "https":
            raise ChnenergyClientError("仅允许 HTTPS")
        host_ok = True

    if not host_ok:
        raise ChnenergyClientError("禁止外部主机")

    query = parse_qs(parsed.query, keep_blank_values=False)
    try:
        infoid = (query.get("infoid") or [""])[0]
        categorynum = (query.get("categorynum") or [""])[0]
        infodate = (query.get("infodate") or [""])[0]
    except (TypeError, IndexError) as exc:
        raise ChnenergyClientError("jump 查询字段缺失") from exc

    return build_notice_detail_url(
        infoid=infoid,
        categorynum=categorynum,
        infodate=infodate,
    )


def _normalize_local_time(match: re.Match[str]) -> str | None:
    """用途：将捕获的时间标准化为 YYYY-MM-DD HH:mm:ss；非法日历返回 None。"""
    second = match.group("s")
    if second is None:
        second = "00"
    try:
        year = int(match.group("y"))
        month = int(match.group("m"))
        day = int(match.group("d"))
        hour = int(match.group("h"))
        minute = int(match.group("mi"))
        sec = int(second)
        datetime(year, month, day, hour, minute, sec)
    except ValueError:
        return None
    return (
        f"{year:04d}-{month:02d}-{day:02d} "
        f"{hour:02d}:{minute:02d}:{sec:02d}"
    )


def _times_in_body(body: str) -> list[str]:
    result: list[str] = []
    for match in _FULL_TIME_RE.finditer(body or ""):
        normalized = _normalize_local_time(match)
        if normalized is not None:
            result.append(normalized)
    return result


def _html_to_visible_text(html: str) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(html or "")
    parser.close()
    return parser.text()


def extract_notice_times(html: str) -> dict[str, Any]:
    """
    用途：从公告 HTML 的可见正文提取投标截止时间与开标时间（北京时间）。
    对接：详情页读取后的结构化命中字段；fixture 与后续 MockTransport 同步。
    规则：先去标签再匹配中文条款；缺完整截止时间或冲突则 needs_review。
    """
    base: dict[str, Any] = {
        "deadline_at_local": None,
        "opening_at_local": None,
        "extraction_status": "needs_review",
        "source_timezone": "Asia/Shanghai",
    }
    text = _html_to_visible_text(html)
    if not text:
        return base

    deadline: str | None = None
    opening: str | None = None
    deadline_candidates: list[str] = []

    for combined in _COMBINED_CLAUSE_RE.finditer(text):
        times = _times_in_body(combined.group("body"))
        if not times:
            continue
        deadline_candidates.append(times[0])
        if deadline is None:
            deadline = times[0]
            if len(times) >= 2:
                opening = times[1]
            else:
                # 联合条款仅一个时间：截止与开标相同
                opening = times[0]

    for deadline_only in _DEADLINE_ONLY_RE.finditer(text):
        times = _times_in_body(deadline_only.group("body"))
        if times:
            deadline_candidates.append(times[0])
            if deadline is None:
                deadline = times[0]

    for opening_only in _OPENING_ONLY_RE.finditer(text):
        times = _times_in_body(opening_only.group("body"))
        if times and opening is None:
            opening = times[0]

    # 多个互相冲突的截止时间 → 待复核
    unique_deadlines = list(dict.fromkeys(deadline_candidates))
    if len(unique_deadlines) > 1:
        return base
    if deadline is None:
        return base

    base["deadline_at_local"] = deadline
    base["opening_at_local"] = opening
    base["extraction_status"] = "resolved"
    return base
