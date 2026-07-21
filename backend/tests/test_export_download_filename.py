"""
模块：V1-F 导出下载人读文件名 failure-first
用途：锁定 Content-Disposition 安全人读名；URL/磁盘仍为 export_*.docx 随机 basename。
对接：GET /api/projects/{id}/export/download/{stored}；export 任务 result.storedName。
二次开发：禁止读真实业务库/uploads；断言不得放宽为接受 filename=storedName。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

# Content-Disposition 中 export_ 随机名即当前生产缺口（filename=storedName）
_STORED_RE = re.compile(r"^export_[0-9a-f]{8}\.docx$", re.IGNORECASE)
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def _parse_content_disposition_filename(header: str | None) -> str:
    """用途：解析 attachment 的 filename / filename*，供人读名断言。"""
    if not header:
        return ""
    star = re.search(r"filename\*\s*=\s*([^;]+)", header, flags=re.IGNORECASE)
    if star:
        raw = star.group(1).strip().strip('"')
        if raw.lower().startswith("utf-8''"):
            return unquote(raw[7:])
        if "''" in raw:
            return unquote(raw.split("''", 1)[1])
        return unquote(raw)
    plain = re.search(r'filename\s*=\s*"([^"]+)"', header, flags=re.IGNORECASE)
    if plain:
        return plain.group(1)
    plain2 = re.search(r"filename\s*=\s*([^;]+)", header, flags=re.IGNORECASE)
    if plain2:
        return plain2.group(1).strip().strip('"')
    return ""


def _seed_exportable(client, name: str, *, kind: str = "technical") -> str:
    """用途：合成项目 + 最小可导出正文，返回 project_id。"""
    proj = client.post(
        "/api/projects",
        json={"name": name, "kind": kind, "industry": "政务"},
    )
    assert proj.status_code == 201, proj.text
    pid = proj.json()["id"]
    if kind == "business":
        put = client.put(
            f"/api/projects/{pid}/editor-state",
            json={
                "parsedMarkdown": "V1F 商务条款\n",
                "businessQualify": [
                    {
                        "id": "q1",
                        "requirement": "法人",
                        "response": "有",
                        "evidence": "",
                        "status": "matched",
                    }
                ],
                "businessCommit": [
                    {
                        "id": "c1",
                        "title": "承诺",
                        "body": "正式承诺正文。",
                        "needsStamp": True,
                    }
                ],
            },
        )
    else:
        put = client.put(
            f"/api/projects/{pid}/editor-state",
            json={
                "outline": [{"id": "n1", "title": "第一章", "children": []}],
                "chapters": [
                    {
                        "id": "n1",
                        "title": "第一章",
                        "body": "V1F 正文内容。\n",
                        "preview": "正文",
                        "wordCount": 6,
                        "status": "done",
                    }
                ],
                "mode": "ALIGNED",
            },
        )
    assert put.status_code == 200, put.text
    return pid


def _export_and_download(client, pid: str):
    """用途：同步 export 后按 storedName 下载，返回 (stored, response, result)。"""
    exp = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exp.status_code == 201, exp.text
    body = exp.json()
    assert body["status"] == "success"
    result = body["result"]
    stored = result["storedName"]
    assert isinstance(stored, str)
    assert _STORED_RE.match(stored), f"磁盘/结果 storedName 必须为随机 export_*.docx，实际={stored!r}"
    # URL 仅 basename，禁止把人读名或绝对路径塞进路径段
    assert "/" not in stored and "\\" not in stored
    dl_path = f"/api/projects/{pid}/export/download/{stored}"
    dl = client.get(dl_path)
    return stored, dl, result


def _assert_no_path_or_body_leak(dl, stored: str, pid: str) -> None:
    """用途：响应头/正文不得泄漏绝对路径或无关敏感串。"""
    cd = dl.headers.get("content-disposition") or ""
    ct = dl.headers.get("content-type") or ""
    joined = f"{cd}\n{ct}"
    assert ":\\" not in joined
    assert "biaoshu.db" not in joined.lower()
    assert pid not in cd
    # 人读头不得再暴露随机磁盘名作为用户可见名（生产缺口：当前恰好相反）
    assert stored not in (cd if "filename" in cd.lower() else "")
    # 二进制体非 JSON 错误页
    assert len(dl.content) > 50
    assert not dl.content.lstrip().startswith(b"{")
    assert not dl.content.lstrip().startswith(b"[")


@pytest.mark.parametrize(
    "project_name,expected_filename",
    [
        ("智慧城市 示范项目", "智慧城市 示范项目.docx"),
        ("含 空格 与中文", "含 空格 与中文.docx"),
        # 去掉 Windows 非法字符 <>:"/\|?* 后为 abcdefghi
        ('a<b>:c"d/e\\f|g?h*i', "abcdefghi.docx"),
        ("报告.docx.docx", "报告.docx"),
        ("尾点.", "尾点.docx"),
        ("尾空格 ", "尾空格.docx"),
        ("***", "标书.docx"),
        # 纯空格：创建层权威默认名为「未命名技术标项目」，非 sanitize 层「标书」
        ("   ", "未命名技术标项目.docx"),
        ("CON", "CON_.docx"),
        ("com1", "com1_.docx"),
        ("LPT9", "LPT9_.docx"),
        ("NUL.docx", "NUL_.docx"),
        # C0 控制字符：TAB U+0009 与 DEL U+007F 必须从人读名中移除（禁止 NUL）
        ("智慧\t城市示范", "智慧城市示范.docx"),
        ("商务\u007f标书名", "商务标书名.docx"),
        # C1 控制字符：U+0085（NEXT LINE）属 C1/Unicode 类别 Cc，人读名必须移除
        ("智慧\u0085城市", "智慧城市.docx"),
    ],
)
def test_download_content_disposition_safe_human_name(
    client, project_name: str, expected_filename: str
):
    """
    用途：人读 Content-Disposition 必须按契约收敛；URL/storedName 仍为 export_*.docx。
    当前生产：FileResponse(filename=storedName) → 真红。
    """
    pid = _seed_exportable(client, project_name)
    stored, dl, result = _export_and_download(client, pid)
    assert dl.status_code == 200, dl.text[:200]
    cd = dl.headers.get("content-disposition")
    assert cd, "必须返回 Content-Disposition"
    assert "attachment" in cd.lower()

    human = _parse_content_disposition_filename(cd)
    assert human == expected_filename, (
        f"人读文件名不符合契约：project={project_name!r} "
        f"got={human!r} expected={expected_filename!r} raw_cd={cd!r}"
    )
    # 不得把随机磁盘名当作用户保存名
    assert not _STORED_RE.match(human)
    assert human != stored
    # 扩展名仅一次
    assert human.lower().endswith(".docx")
    assert not human.lower().endswith(".docx.docx")
    base = human[: -len(".docx")]
    assert base.upper() not in _RESERVED

    # 任务结果 filename 与下载头同规则（契约：唯一收敛函数）
    task_filename = result.get("filename")
    assert task_filename == expected_filename

    _assert_no_path_or_body_leak(dl, stored, pid)
    # downloadPath 仍指向随机 basename
    assert result["downloadPath"].endswith(f"/export/download/{stored}")
    assert expected_filename not in result["downloadPath"]


def test_download_url_and_disk_remain_random_stored_name(client):
    """用途：路径段与磁盘文件名保持 export_<8hex>.docx，绝不按人读名访问。"""
    pid = _seed_exportable(client, "人读名不得进路径")
    stored, dl, result = _export_and_download(client, pid)
    assert dl.status_code == 200
    assert _STORED_RE.match(stored)
    assert result["downloadPath"] == f"/projects/{pid}/export/download/{stored}"
    # 用人读名请求必须失败（路由只认 export_*.docx）
    bad = client.get(f"/api/projects/{pid}/export/download/人读名不得进路径.docx")
    assert bad.status_code in (400, 404)
    # 非 export_ 前缀
    bad2 = client.get(f"/api/projects/{pid}/export/download/not_export_name.docx")
    assert bad2.status_code == 400


def test_download_uses_current_project_name_after_rename(client):
    """
    用途：任务生成后人读名以下载时权威 project.name 为准。
    当前生产忽略项目名 → 真红。
    """
    pid = _seed_exportable(client, "导出前旧名")
    exp = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exp.status_code == 201
    stored = exp.json()["result"]["storedName"]
    assert _STORED_RE.match(stored)

    renamed = client.patch(
        f"/api/projects/{pid}",
        json={"name": "下载时新名"},
    )
    assert renamed.status_code == 200, renamed.text

    dl = client.get(f"/api/projects/{pid}/export/download/{stored}")
    assert dl.status_code == 200
    human = _parse_content_disposition_filename(dl.headers.get("content-disposition"))
    assert human == "下载时新名.docx"
    assert human != stored


def test_long_base_name_truncated_to_100_codepoints(client):
    """用途：基础名限制 100 个 Unicode 码点后再追加 .docx。"""
    base = "测" * 120
    pid = _seed_exportable(client, base)
    stored, dl, _ = _export_and_download(client, pid)
    assert dl.status_code == 200
    human = _parse_content_disposition_filename(dl.headers.get("content-disposition"))
    assert human.endswith(".docx")
    stem = human[: -len(".docx")]
    assert len(stem) == 100
    assert stem == "测" * 100
    assert not _STORED_RE.match(human)
    assert stored != human


def test_business_export_download_same_filename_rules(client):
    """用途：商务标下载头与技术标共用同一人读收敛规则。"""
    pid = _seed_exportable(client, "商务 CON", kind="business")
    # 名称含保留名片段但整名不是保留名；再测纯保留名商务
    stored, dl, result = _export_and_download(client, pid)
    assert dl.status_code == 200
    human = _parse_content_disposition_filename(dl.headers.get("content-disposition"))
    assert human == "商务 CON.docx"
    assert result.get("mode") == "business"
    assert _STORED_RE.match(stored)


def test_business_reserved_name_only(client):
    """用途：商务标项目名 COM1 必须改写。"""
    pid = _seed_exportable(client, "COM1", kind="business")
    stored, dl, _ = _export_and_download(client, pid)
    assert dl.status_code == 200
    human = _parse_content_disposition_filename(dl.headers.get("content-disposition"))
    assert human == "COM1_.docx"
    assert not _STORED_RE.match(human)
    assert stored != human
