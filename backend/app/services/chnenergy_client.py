"""
模块：国能 e 招受控客户端（解析、地址安全与低频同步）
用途：从 jump 字段安全重建固定 HTTPS 公告详情地址；从公告 HTML 可见正文提取投标截止/开标时间；
      以固定主机/路径/JSON 完成门户 Cookie、检索与详情的受控读取（支持 MockTransport）。
对接：opportunity_watch_service 同步任务；test_opportunity_watch 与 HTML fixture。
二次开发：主机与路径规则固定在国能 e 招；禁止把本模块泛化为任意 URL 抓取；不得落库/记录 Cookie、原文 HTML/JSON。
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from uuid import UUID

import httpx

# 唯一允许的公告主机（不含协议与路径）
_ALLOWED_HOST = "www.chnenergybidding.com.cn"
_PORTAL_URL = f"https://{_ALLOWED_HOST}/bidweb/"
_SEARCH_URL = (
    f"https://{_ALLOWED_HOST}/bidfulltextsearch/rest/"
    "inteligentSearch/getFullTextData"
)
# 检索 JSON 仅允许 wd（计划名）与 rn（固定 5）变化；其余字段冻结。
_SEARCH_RN = 5
_SEARCH_FIELDS = "title;content"
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 15.0
_DEFAULT_MIN_INTERVAL = 1.0
_DEFAULT_SEARCH_RETRY = 1
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


class ChnenergySyncStopError(Exception):
    """
    模块：受控同步安全停止
    用途：门户无 Cookie、限流、结构异常或连续网络失败时终止后续请求。
    对接：ChnenergyControlledClient；opportunity_watch_service.execute_sync_run。
    二次开发：error_code 只能是固定字典，禁止携带远端正文或异常原文。
    """

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code


class ChnenergyNetworkError(Exception):
    """用途：单次网络失败的内部信号；不暴露连接/TLS 原文。"""


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


def parse_jump_fields(jump: str) -> dict[str, str]:
    """
    用途：从 linkurl/jump 安全解析 infoid、categorynum、infodate，不采信为可访问 URL。
    对接：同步执行器写入命中字段前；非法外部主机/协议抛出 ChnenergyClientError。
    """
    raw = (jump or "").strip()
    if not raw:
        raise ChnenergyClientError("jump 链接为空")

    if "://" not in raw:
        parsed = urlparse("https://placeholder.local/" + raw.lstrip("/"))
    else:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host != _ALLOWED_HOST:
            raise ChnenergyClientError("禁止外部主机")
        if (parsed.scheme or "").lower() != "https":
            raise ChnenergyClientError("仅允许 HTTPS")

    query = parse_qs(parsed.query, keep_blank_values=False)
    infoid = _validate_infoid((query.get("infoid") or [""])[0])
    categorynum = _validate_categorynum((query.get("categorynum") or [""])[0])
    infodate = _validate_infodate((query.get("infodate") or [""])[0])
    return {
        "infoid": infoid,
        "categorynum": categorynum,
        "infodate": infodate,
    }


def build_fixed_search_body(plan_title: str) -> dict[str, Any]:
    """
    用途：构造已核验的国能 e 招冻结检索 JSON；仅 wd 随计划名变化，rn 固定为 5。
    对接：ChnenergyControlledClient.search_candidates。
    二次开发：字段名与取值必须与线上已确认模板一致；禁止 isBusiness 或其它漂移字段。
    """
    title = (plan_title or "").strip()
    return {
        "token": "",
        "pn": 0,
        "rn": _SEARCH_RN,
        "sdt": "",
        "edt": "",
        "wd": quote(title, safe=""),
        "inc_wd": "",
        "exc_wd": "",
        "fields": _SEARCH_FIELDS,
        "cnum": "",
        "sort": '{"infodate":0}',
        "ssort": "title",
        "cl": 500,
        "terminal": "",
        "condition": None,
        "time": None,
        "highlights": "title;content",
        "statistics": None,
        "unionCondition": None,
        "accuracy": "",
        "noParticiple": "1",
        "searchRange": None,
    }


class ChnenergyControlledClient:
    """
    模块：国能 e 招固定来源低频客户端
    用途：在单一 httpx.Client 内存中完成门户 uid Cookie、计划名检索与详情读取；支持注入 MockTransport。
    对接：opportunity_watch_service.execute_sync_run；pytest httpx.MockTransport。
    二次开发：禁止 follow_redirects=True、任意 URL 入参、Cookie/HTML 落库或日志；生产默认才用真实网络。
    """

    portal_url = _PORTAL_URL
    search_url = _SEARCH_URL
    follow_redirects = False

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        min_interval_seconds: float = _DEFAULT_MIN_INTERVAL,
        connect_timeout_seconds: float = _CONNECT_TIMEOUT,
        read_timeout_seconds: float = _READ_TIMEOUT,
        search_retry_count: int = _DEFAULT_SEARCH_RETRY,
    ) -> None:
        self._transport = transport
        self._sleep_fn = sleep_fn or time.sleep
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._timeout = httpx.Timeout(
            connect=float(connect_timeout_seconds),
            read=float(read_timeout_seconds),
            write=float(read_timeout_seconds),
            pool=float(connect_timeout_seconds),
        )
        self._search_retry_count = max(0, int(search_retry_count))
        self._client: httpx.Client | None = None
        self._last_request_at: float | None = None
        self._consecutive_network_failures = 0
        self._session_ready = False

    def __enter__(self) -> "ChnenergyControlledClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        """用途：创建禁用重定向的 httpx 客户端；Cookie 仅存于此实例内存。"""
        if self._client is not None:
            return
        kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": False,
            "headers": {
                "User-Agent": "BiaoshuOpportunityWatch/1.0",
                "Accept": "application/json,text/html,*/*",
            },
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        """用途：关闭客户端并丢弃内存 Cookie。"""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._session_ready = False
        self._last_request_at = None
        self._consecutive_network_failures = 0

    def ensure_session(self) -> None:
        """
        用途：GET 固定门户并确认匿名 uid Cookie 存在；缺失则 source_unavailable。
        对接：同步运行开始时调用一次。
        """
        self.open()
        assert self._client is not None
        response = self._request("GET", _PORTAL_URL)
        uid = self._client.cookies.get("uid")
        if not uid:
            # 部分 Mock/站点可能只在 Set-Cookie 头；再扫 jar
            for cookie in self._client.cookies.jar:
                if cookie.name == "uid" and cookie.value:
                    uid = cookie.value
                    break
        if not uid:
            raise ChnenergySyncStopError("source_unavailable")
        self._session_ready = True

    def search_candidates(self, plan_title: str) -> list[dict[str, str]]:
        """
        用途：POST 固定检索接口，仅返回 result.records 前 5 条的 title/infodate/linkurl。
        对接：按已保存计划名检索；结构异常为 malformed_response。
        """
        if not self._session_ready:
            self.ensure_session()
        body = build_fixed_search_body(plan_title)
        attempts = 1 + self._search_retry_count
        last_network: ChnenergyNetworkError | None = None
        response: httpx.Response | None = None
        for attempt in range(attempts):
            try:
                response = self._request(
                    "POST",
                    _SEARCH_URL,
                    json_body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Referer": _PORTAL_URL,
                    },
                )
                last_network = None
                break
            except ChnenergyNetworkError as exc:
                last_network = exc
                if attempt + 1 >= attempts:
                    break
        if last_network is not None or response is None:
            # 检索重试耗尽仍失败：记为一次网络失败路径，由连续失败逻辑处理
            raise ChnenergyNetworkError()

        try:
            payload = response.json()
        except Exception as exc:
            raise ChnenergySyncStopError("malformed_response") from exc

        records = self._extract_records(payload)
        slim: list[dict[str, str]] = []
        for item in records[:_SEARCH_RN]:
            if not isinstance(item, dict):
                continue
            slim.append(
                {
                    "title": str(item.get("title") or "").strip()[:1000],
                    "infodate": str(item.get("infodate") or "").strip()[:100],
                    "linkurl": str(item.get("linkurl") or "").strip(),
                }
            )
        return slim

    def fetch_detail_html(self, detail_url: str) -> str:
        """
        用途：GET 已由服务端重建的固定 HTTPS 详情地址，返回 HTML 文本（仅内存使用）。
        对接：详情不重试；地址必须已是本站固定静态路径。
        """
        if not self._session_ready:
            self.ensure_session()
        parsed = urlparse(detail_url)
        if (parsed.scheme or "").lower() != "https":
            raise ChnenergyClientError("仅允许 HTTPS")
        if (parsed.hostname or "").lower() != _ALLOWED_HOST:
            raise ChnenergyClientError("禁止外部主机")
        if not (parsed.path or "").startswith("/bidweb/"):
            raise ChnenergyClientError("详情路径非法")
        response = self._request(
            "GET",
            detail_url,
            headers={"Referer": _PORTAL_URL},
        )
        return response.text or ""

    def _extract_records(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            raise ChnenergySyncStopError("malformed_response")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ChnenergySyncStopError("malformed_response")
        records = result.get("records")
        if not isinstance(records, list):
            raise ChnenergySyncStopError("malformed_response")
        return records

    def _throttle(self) -> None:
        if self._last_request_at is None or self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remain = self._min_interval - elapsed
        if remain > 0:
            self._sleep_fn(remain)

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.open()
        assert self._client is not None
        self._throttle()
        try:
            response = self._client.request(
                method,
                url,
                json=json_body,
                headers=headers,
            )
        except ChnenergySyncStopError:
            raise
        except Exception as exc:
            self._note_network_failure()
            if self._consecutive_network_failures >= 2:
                raise ChnenergySyncStopError("source_unavailable") from exc
            raise ChnenergyNetworkError() from exc
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code in (403, 429):
            raise ChnenergySyncStopError("rate_limited")
        if response.status_code >= 500:
            # 5xx 视作网络/源站不可用类失败信号
            self._note_network_failure()
            if self._consecutive_network_failures >= 2:
                raise ChnenergySyncStopError("source_unavailable")
            raise ChnenergyNetworkError()
        if response.status_code >= 400:
            raise ChnenergySyncStopError("source_unavailable")
        self._consecutive_network_failures = 0
        return response

    def _note_network_failure(self) -> None:
        """用途：累计连续网络失败次数，供双次失败安全停止。"""
        self._consecutive_network_failures += 1
