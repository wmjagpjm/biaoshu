"""
模块：任务取消与扩展模板导出测试
用途：
  - pending/running 可取消；已结束任务 400
  - exportFormat 含页眉页脚等扩展字段时仍可导出
对接：POST .../tasks/{id}/cancel；export 任务
二次开发：新增异步任务类型时，取消测试必须等待对应后台线程退出后再结束夹具，避免拆表竞态。
"""

import threading
import time


def _wait_for_task_thread(task_id: str, timeout_seconds: float = 2.0) -> None:
    """用途：等待指定后台任务线程退出，避免 pytest 夹具拆表时仍有写库操作。"""
    deadline = time.monotonic() + timeout_seconds
    thread_name = f"task-{task_id}"
    while time.monotonic() < deadline:
        if not any(thread.name == thread_name and thread.is_alive() for thread in threading.enumerate()):
            return
        time.sleep(0.02)
    raise AssertionError(f"后台任务线程未按时退出：{task_id}")


def test_cancel_running_parse_task(client):
    proj = client.post("/api/projects", json={"name": "取消任务"}).json()
    pid = proj["id"]
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("a.md", b"# t\n\nhello", "text/markdown")},
    )
    created = client.post(
        f"/api/projects/{pid}/tasks",
        json={"type": "parse"},
    )
    assert created.status_code == 201
    task = created.json()
    tid = task["id"]

    # 立刻取消（可能仍 pending/running，或已 success——均允许测 API 契约）
    cancel = client.post(f"/api/projects/{pid}/tasks/{tid}/cancel")
    # 若已跑完则 400；否则 200 cancelled
    if cancel.status_code == 200:
        body = cancel.json()
        assert body["status"] == "cancelled"
        assert body["id"] == tid
        # 再查一次仍为 cancelled
        got = client.get(f"/api/projects/{pid}/tasks/{tid}").json()
        assert got["status"] == "cancelled"
    else:
        assert cancel.status_code == 400
        # 已结束时轮询应为终态
        final = None
        for _ in range(40):
            got = client.get(f"/api/projects/{pid}/tasks/{tid}").json()
            if got["status"] in ("success", "failed", "cancelled"):
                final = got
                break
            time.sleep(0.05)
        assert final is not None
        assert final["status"] in ("success", "failed", "cancelled")

    _wait_for_task_thread(tid)


def test_cancel_finished_task_rejected(client):
    proj = client.post("/api/projects", json={"name": "已结束不可取消"}).json()
    pid = proj["id"]
    client.post(
        f"/api/projects/{pid}/files",
        files={"file": ("a.md", b"# done\n", "text/markdown")},
    )
    created = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "parse"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "success"
    tid = created.json()["id"]
    cancel = client.post(f"/api/projects/{pid}/tasks/{tid}/cancel")
    assert cancel.status_code == 400


def test_export_with_extended_template(client):
    """用途：默认导出格式含页眉页脚等字段时仍能成功生成 docx。"""
    client.put(
        "/api/settings",
        json={
            "exportFormat": {
                "template_name": "扩展模板测",
                "heading_level1_page_break_before": True,
                "page": {
                    "paper_size": "a4",
                    "orientation": "portrait",
                    "margin_top_cm": 2.5,
                    "margin_bottom_cm": 2.5,
                    "margin_left_cm": 2.8,
                    "margin_right_cm": 2.8,
                    "header_enabled": True,
                    "header_text": "保密标书",
                    "header_font": "黑体",
                    "header_size": "小五",
                    "header_alignment": "居中",
                    "header_color": "#333333",
                    "footer_enabled": True,
                    "footer_text": "内部资料",
                    "footer_font": "宋体",
                    "footer_size": "小五",
                    "footer_alignment": "居中",
                    "page_number_enabled": True,
                    "page_number_format": "第{page}页",
                    "page_number_start": 1,
                },
                "body_text": {
                    "font": "宋体",
                    "size": "小四",
                    "alignment": "两端对齐",
                    "first_line_indent_chars": 2,
                    "line_spacing_multiple": 1.5,
                    "spacing_before_pt": 0,
                    "spacing_after_pt": 0,
                },
                "headings": [
                    {
                        "font": "黑体",
                        "size": "三号",
                        "alignment": "左对齐",
                        "bold": True,
                        "text_color": "#1e3a5f",
                        "spacing_before_pt": 12,
                        "spacing_after_pt": 6,
                        "line_spacing": 1.2,
                    }
                ],
            }
        },
    )
    proj = client.post("/api/projects", json={"name": "模板导出"}).json()
    pid = proj["id"]
    client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "analysisOverview": "概述",
            "outline": [{"id": "n1", "title": "第一章", "children": []}],
            "chapters": [
                {
                    "id": "n1",
                    "title": "第一章",
                    "body": "正文甲",
                    "preview": "正文",
                    "wordCount": 3,
                    "status": "done",
                },
                {
                    "id": "n2",
                    "title": "第二章",
                    "body": "正文乙",
                    "preview": "正文",
                    "wordCount": 3,
                    "status": "done",
                },
            ],
        },
    )
    exp = client.post(
        f"/api/projects/{pid}/tasks?sync=true",
        json={"type": "export"},
    )
    assert exp.status_code == 201
    assert exp.json()["status"] == "success"
    stored = exp.json()["result"]["storedName"]
    dl = client.get(f"/api/projects/{pid}/export/download/{stored}")
    assert dl.status_code == 200
    assert len(dl.content) > 200
