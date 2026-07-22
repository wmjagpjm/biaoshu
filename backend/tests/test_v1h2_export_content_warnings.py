"""
模块：V1-H2 技术标导出正文完整性提醒 failure-first 专项
用途：经真实 POST /tasks?sync=true 与 DOCX 下载，锁定 contentWarnings 契约；
  生产未实现时必须真实失败（缺字段或错误语义），禁止 helper 单测或源码扫描假绿。
对接：task_service._run_export；export_service.build_docx_bytes；
  POST /api/projects/{id}/tasks?sync=true；GET .../export/download/{stored}。
二次开发：禁止复制生产扫描逻辑、skip/xfail、真实 biaoshu.db/uploads 或宽泛 or 假绿。
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from app.core.config import get_settings

# 契约固定文案（实现后须逐字相等）
_WARN_EMPTY_N = "正文存在 {n} 个空章节，导出的 Word 已保留空章占位，请补充后再定稿。"
_WARN_NO_BODY = (
    "当前没有可导出的正文章节，导出的 Word 不包含正文部分，请补充后再定稿。"
)
_PLACEHOLDER = "（本章暂无正文）"
_SECTION_BODY = "四、正文"

# 合成锚点：全部为测试专用，禁止真实业务数据
_ANCHOR_FILLED = "V1H2合成有效正文锚点-勿写入告警。"
_ANCHOR_SHORT = "无。"
_ANCHOR_PENDING_BODY = "V1H2-pending非空正文锚点应正常导出。"
_ANCHOR_BIZ = "V1H2商务承诺正文锚点-应保留。"
_TITLE_EMPTY = "V1H2空章敏感标题-不得入告警"
_TITLE_FILLED = "V1H2实章标题"
_TITLE_WS = "V1H2空白章标题-不得入告警"
_ID_EMPTY = "v1h2_ch_empty_id"
_ID_FILLED = "v1h2_ch_filled_id"
_ID_WS = "v1h2_ch_ws_id"
_PROJ_EMPTY_PAIR = "V1H2两章一空项目名-不得入告警"
_PROJ_TWO_EMPTY = "V1H2双空章项目名-不得入告警"
_PROJ_NO_CH = "V1H2无章节项目名-不得入告警"
_PROJ_NON_DICT = "V1H2非dict章节列表项目-不得入告警"
_PROJ_LEGAL = "V1H2合法短章与pending项目"
_PROJ_BIZ = "V1H2商务导出隔离项目"
# 非 dict 元素中的合成字符串锚点（须出现在 chapters 内，且不得入告警/DOCX 正文区语义）
_ANCHOR_NON_DICT_ELEM = "V1H2非dict章节元素字符串锚点-不得入告警与Word"

_STORED_RE = re.compile(r"^export_[0-9a-f]{8}\.docx$", re.IGNORECASE)

# 告警 JSON 隐私禁词（路径/库/密钥类）
_SENSITIVE_FRAGMENTS = (
    "biaoshu.db",
    "uploads",
    "cookie",
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "c:\\",
    "c:/",
    "/users/",
    "\\users\\",
)


def _use_temp_export_root(tmp_path: Path, monkeypatch) -> Path:
    """用途：将 upload_dir 指到 TEMP，避免写入仓库 uploads。"""
    root = tmp_path / "v1h2_export_root"
    root.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", str(root))
    return root


def _create_project(client: TestClient, name: str, *, kind: str = "technical") -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind, "industry": "政务"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _put_state(client: TestClient, pid: str, body: dict) -> dict:
    res = client.put(f"/api/projects/{pid}/editor-state", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _chapter(
    cid: str,
    title: str,
    *,
    body: str = "",
    status: str = "done",
    word_count: int | None = None,
) -> dict:
    text = body if body is not None else ""
    wc = word_count if word_count is not None else len(text.replace(" ", ""))
    return {
        "id": cid,
        "title": title,
        "body": text,
        "preview": (text[:96].replace("\n", " ") if text.strip() else ""),
        "wordCount": wc,
        "status": status,
        "targetWords": 500,
    }


def _export_sync(client: TestClient, pid: str, *, mode: str | None = None) -> dict:
    """用途：真实同步 export，返回完整任务 JSON。"""
    payload: dict = {"type": "export"}
    if mode is not None:
        payload["payload"] = {"mode": mode}
    res = client.post(f"/api/projects/{pid}/tasks?sync=true", json=payload)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "success", body
    assert body["type"] == "export"
    assert isinstance(body.get("result"), dict), body
    return body


def _download_docx(client: TestClient, pid: str, stored: str) -> Document:
    assert _STORED_RE.match(stored), f"storedName 非法: {stored!r}"
    dl = client.get(f"/api/projects/{pid}/export/download/{stored}")
    assert dl.status_code == 200, dl.text[:200]
    assert len(dl.content) > 50
    return Document(BytesIO(dl.content))


def _docx_text(doc: Document) -> str:
    return "\n".join(p.text for p in doc.paragraphs)


def _assert_content_warnings_is_list(result: dict) -> list:
    """用途：result.contentWarnings 必须始终为数组（生产缺口：字段尚不存在）。"""
    assert "contentWarnings" in result, (
        "export result 必须始终包含 contentWarnings 数组字段（V1-H2 契约）"
    )
    warnings = result["contentWarnings"]
    assert isinstance(warnings, list), (
        f"contentWarnings 必须是 list，实际 type={type(warnings).__name__}"
    )
    for item in warnings:
        assert isinstance(item, str), f"contentWarnings 项必须是 str，实际={item!r}"
    return warnings


def _assert_warning_privacy(
    warnings: list[str],
    *,
    forbidden_substrings: list[str],
    stored: str | None = None,
) -> None:
    """用途：告警 JSON 不得回显标题/ID/正文/项目名/路径/storedName 或敏感禁词。"""
    blob = "\n".join(warnings)
    lower = blob.lower()
    for frag in forbidden_substrings:
        if not frag:
            continue
        assert frag not in blob, f"告警不得含敏感片段 {frag!r}，实际={blob!r}"
        assert frag.lower() not in lower, (
            f"告警不得含敏感片段(大小写不敏感) {frag!r}，实际={blob!r}"
        )
    for frag in _SENSITIVE_FRAGMENTS:
        assert frag not in lower, f"告警不得含禁词 {frag!r}，实际={blob!r}"
    if stored:
        assert stored not in blob, f"告警不得含 storedName={stored!r}"
    # 路径分隔痕迹（告警应为固定中文句，无路径）
    assert ":\\" not in blob
    assert "\\\\" not in blob


# ---------------------------------------------------------------------------
# 1) 技术标两章一实一空
# ---------------------------------------------------------------------------


def test_technical_one_filled_one_empty_content_warning_and_docx(
    client, tmp_path, monkeypatch
):
    """
    用途：一实一空 → export success；contentWarnings 精确 N=1；
      Word 同时含有效合成锚点与空章占位。
    """
    _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_EMPTY_PAIR)
    _put_state(
        client,
        pid,
        {
            "analysisOverview": "V1H2一实一空概述",
            "outline": [
                {"id": _ID_FILLED, "title": _TITLE_FILLED, "children": []},
                {"id": _ID_EMPTY, "title": _TITLE_EMPTY, "children": []},
            ],
            "chapters": [
                _chapter(_ID_FILLED, _TITLE_FILLED, body=_ANCHOR_FILLED, status="done"),
                _chapter(_ID_EMPTY, _TITLE_EMPTY, body="", status="done"),
            ],
        },
    )

    task = _export_sync(client, pid)
    result = task["result"]
    warnings = _assert_content_warnings_is_list(result)
    expected = _WARN_EMPTY_N.format(n=1)
    assert warnings == [expected], f"期望精确 N=1 固定句，实际={warnings!r}"

    stored = result["storedName"]
    _assert_warning_privacy(
        warnings,
        forbidden_substrings=[
            _PROJ_EMPTY_PAIR,
            _TITLE_EMPTY,
            _TITLE_FILLED,
            _ID_EMPTY,
            _ID_FILLED,
            _ANCHOR_FILLED,
            pid,
            str(tmp_path),
        ],
        stored=stored,
    )

    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _SECTION_BODY in text
    assert _ANCHOR_FILLED in text
    assert _PLACEHOLDER in text


# ---------------------------------------------------------------------------
# 2) 技术标空串 + 混合空白两章 → N=2，告警脱敏
# ---------------------------------------------------------------------------


def test_technical_empty_and_whitespace_two_empty_privacy(
    client, tmp_path, monkeypatch
):
    """
    用途：body 空串与混合空白均算空章 → 精确 N=2；
      告警不得含标题、ID、正文锚点、项目名、路径、storedName 或敏感禁词。
    """
    export_root = _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_TWO_EMPTY)
    body_ws = " \n\t 　"
    _put_state(
        client,
        pid,
        {
            "outline": [
                {"id": _ID_EMPTY, "title": _TITLE_EMPTY, "children": []},
                {"id": _ID_WS, "title": _TITLE_WS, "children": []},
            ],
            "chapters": [
                _chapter(_ID_EMPTY, _TITLE_EMPTY, body="", status="done"),
                _chapter(_ID_WS, _TITLE_WS, body=body_ws, status="done"),
            ],
        },
    )

    task = _export_sync(client, pid)
    result = task["result"]
    warnings = _assert_content_warnings_is_list(result)
    expected = _WARN_EMPTY_N.format(n=2)
    assert warnings == [expected], f"期望精确 N=2 固定句，实际={warnings!r}"

    stored = result["storedName"]
    _assert_warning_privacy(
        warnings,
        forbidden_substrings=[
            _PROJ_TWO_EMPTY,
            _TITLE_EMPTY,
            _TITLE_WS,
            _ID_EMPTY,
            _ID_WS,
            "\t",
            pid,
            str(export_root),
            str(tmp_path),
            "export_",
        ],
        stored=stored,
    )

    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _SECTION_BODY in text
    assert text.count(_PLACEHOLDER) >= 2


# ---------------------------------------------------------------------------
# 3) chapters=[] 或缺失 → 无正文区固定提醒
# ---------------------------------------------------------------------------


def test_technical_empty_chapters_list_no_body_section_warning(
    client, tmp_path, monkeypatch
):
    """
    用途：chapters=[] 为真实可构造代表态；
      contentWarnings 精确为「没有可导出正文章节」句；Word 不含「四、正文」。
    说明：新建项目不写 chapters 时 GET 亦为 null/缺失，与 [] 在导出侧
      均走「无可导出正文章节」分支（见 export_service 对 list 空/非 list 判断）。
      API 层 exclude_unset 使「省略键」与「显式 []」均可构造；本测取 chapters=[]，
      不伪造 DB 腐败；两态语义等价故不重复第二测。
    """
    _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_NO_CH)
    # 仅写概述，chapters 显式空列表（真实 API 可构造）
    state = _put_state(
        client,
        pid,
        {
            "analysisOverview": "V1H2无章节概述-仅用于导出封面",
            "outline": [],
            "chapters": [],
        },
    )
    assert state.get("chapters") in ([], None)

    task = _export_sync(client, pid)
    result = task["result"]
    warnings = _assert_content_warnings_is_list(result)
    assert warnings == [_WARN_NO_BODY], f"期望无正文章节固定句，实际={warnings!r}"

    stored = result["storedName"]
    _assert_warning_privacy(
        warnings,
        forbidden_substrings=[
            _PROJ_NO_CH,
            pid,
            "V1H2无章节概述",
        ],
        stored=stored,
    )

    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _SECTION_BODY not in text
    assert _PLACEHOLDER not in text


# ---------------------------------------------------------------------------
# 3b) chapters 非空但无有效 dict 元素 → 与空列表同语义（无正文区）
# ---------------------------------------------------------------------------


def test_technical_non_dict_chapter_elements_no_body_section_warning(
    client, tmp_path, monkeypatch
):
    """
    用途：chapters 为非空 list 且元素全部为非 dict（null/字符串/数字）；
      EditorStateUpdate.chapters 未约束 list 元素类型，API 可合法 PUT；
      语义等同「没有有效章节字典」：contentWarnings 精确为 _WARN_NO_BODY；
      Word 不得含「四、正文」、空章占位或该字符串锚点。
    """
    _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_NON_DICT)
    # 非空 list，元素均为非 dict：null、合成字符串锚点、数字
    chapters_payload = [None, _ANCHOR_NON_DICT_ELEM, 42]
    state = _put_state(
        client,
        pid,
        {
            "analysisOverview": "V1H2非dict章节概述-仅封面",
            "outline": [],
            "chapters": chapters_payload,
        },
    )
    # 证明 API 接受该态：PUT 200 且回读 chapters 为非空 list、无有效 dict
    put_chapters = state.get("chapters")
    assert put_chapters == chapters_payload, put_chapters

    task = _export_sync(client, pid)
    result = task["result"]
    warnings = _assert_content_warnings_is_list(result)
    assert warnings == [_WARN_NO_BODY], (
        f"无有效章节 dict 时期望精确无正文固定句，实际={warnings!r}"
    )

    stored = result["storedName"]
    _assert_warning_privacy(
        warnings,
        forbidden_substrings=[
            _PROJ_NON_DICT,
            pid,
            _ANCHOR_NON_DICT_ELEM,
            "V1H2非dict章节概述",
        ],
        stored=stored,
    )
    # 显式再证：告警不得含合成字符串锚点
    assert _ANCHOR_NON_DICT_ELEM not in "\n".join(warnings)

    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _SECTION_BODY not in text, (
        "无有效章节 dict 时 Word 不得含「四、正文」（与 _WARN_NO_BODY 语义一致）"
    )
    assert _PLACEHOLDER not in text
    assert _ANCHOR_NON_DICT_ELEM not in text


# ---------------------------------------------------------------------------
# 4) 合法短章「无。」与 status=pending 但 body 非空 → 零告警
# ---------------------------------------------------------------------------


def test_technical_short_and_pending_nonempty_zero_warnings(
    client, tmp_path, monkeypatch
):
    """
    用途：合法短章「无。」与 pending 非空 body 均不得误报；
      contentWarnings=[]；Word 正常含两段正文锚点。
    """
    _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_LEGAL)
    id_short = "v1h2_short"
    id_pending = "v1h2_pending_body"
    _put_state(
        client,
        pid,
        {
            "outline": [
                {"id": id_short, "title": "短章", "children": []},
                {"id": id_pending, "title": "待处理非空", "children": []},
            ],
            "chapters": [
                _chapter(
                    id_short,
                    "短章",
                    body=_ANCHOR_SHORT,
                    status="done",
                    word_count=1,
                ),
                _chapter(
                    id_pending,
                    "待处理非空",
                    body=_ANCHOR_PENDING_BODY,
                    status="pending",
                    word_count=20,
                ),
            ],
        },
    )

    task = _export_sync(client, pid)
    result = task["result"]
    warnings = _assert_content_warnings_is_list(result)
    assert warnings == [], f"合法短章与 pending 非空不得告警，实际={warnings!r}"

    stored = result["storedName"]
    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _SECTION_BODY in text
    assert _ANCHOR_SHORT in text
    assert _ANCHOR_PENDING_BODY in text
    assert _PLACEHOLDER not in text


# ---------------------------------------------------------------------------
# 5) 商务标 export：contentWarnings=[]，不扫描技术 chapters
# ---------------------------------------------------------------------------


def test_business_export_content_warnings_empty_and_keeps_body(
    client, tmp_path, monkeypatch
):
    """
    用途：商务标固定 contentWarnings=[]；既有商务正文/下载保持；
      即使写入空技术 chapters 也不得扫描产生正文告警。
    """
    _use_temp_export_root(tmp_path, monkeypatch)
    pid = _create_project(client, _PROJ_BIZ, kind="business")
    # 故意写入空技术 chapters，证明商务模式不得扫描
    _put_state(
        client,
        pid,
        {
            "parsedMarkdown": "V1H2商务条款摘要",
            "chapters": [
                _chapter("biz_tech_empty", _TITLE_EMPTY, body="", status="done"),
                _chapter("biz_tech_ws", _TITLE_WS, body=" \n\t ", status="pending"),
            ],
            "businessQualify": [
                {
                    "id": "q1",
                    "requirement": "法人资格",
                    "response": "具备",
                    "evidence": "",
                    "status": "matched",
                }
            ],
            "businessCommit": [
                {
                    "id": "c1",
                    "title": "履约承诺",
                    "body": _ANCHOR_BIZ,
                    "needsStamp": True,
                }
            ],
        },
    )

    task = _export_sync(client, pid, mode="business")
    result = task["result"]
    assert result.get("mode") == "business"
    warnings = _assert_content_warnings_is_list(result)
    assert warnings == [], (
        f"商务标 contentWarnings 必须固定空数组，不得扫描技术空章，实际={warnings!r}"
    )

    # 不得出现技术空章固定句
    blob = "\n".join(str(x) for x in warnings)
    assert "空章节" not in blob
    assert "没有可导出的正文章节" not in blob

    stored = result["storedName"]
    doc = _download_docx(client, pid, stored)
    text = _docx_text(doc)
    assert _ANCHOR_BIZ in text
    # 商务路径不组装「四、正文」技术区
    assert _SECTION_BODY not in text
    assert result.get("size", 0) > 100
