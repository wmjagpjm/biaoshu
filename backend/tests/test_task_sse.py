"""
模块：任务 SSE 状态流测试
用途：验收单任务快照、终态关闭、取消状态和资源不存在时的 HTTP 契约。
对接：GET /api/projects/{project_id}/tasks/{task_id}/events；task_service 任务状态。
二次开发：若引入事件游标或多任务总线，须保留当前 snapshot 与终态关闭语义。
"""

import json
import threading
import time

from app.api import tasks as tasks_api
from app.core.database import SessionLocal
from app.services import task_service


def _read_sse_events(raw: str) -> list[tuple[str, dict]]:
    """用途：将测试响应中的 SSE 文本解析为事件名和 JSON 载荷。"""
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def _create_finished_parse_task(client) -> tuple[str, str]:
    """用途：创建可立即读取终态 SSE 的同步 parse 任务。"""
    project = client.post("/api/projects", json={"name": "SSE 完成任务"}).json()
    project_id = project["id"]
    client.post(
        f"/api/projects/{project_id}/files",
        files={"file": ("task.md", b"# SSE\n\nready", "text/markdown")},
    )
    task = client.post(
        f"/api/projects/{project_id}/tasks?sync=true",
        json={"type": "parse"},
    ).json()
    assert task["status"] == "success"
    return project_id, task["id"]


def test_terminal_task_events_starts_with_snapshot_and_finishes(client):
    project_id, task_id = _create_finished_parse_task(client)

    response = client.get(f"/api/projects/{project_id}/tasks/{task_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _read_sse_events(response.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task_id
    assert events[0][1]["status"] == "success"
    assert events[0][1]["progress"] == 100
    assert len(events) == 1


def test_cancelled_task_events_returns_cancelled_snapshot(client):
    project = client.post("/api/projects", json={"name": "SSE 取消任务"}).json()
    project_id = project["id"]
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            "ws_local",
            project_id,
            task_type="parse",
        )
        task_id = task.id
    finally:
        db.close()

    cancelled = client.post(f"/api/projects/{project_id}/tasks/{task_id}/cancel")
    assert cancelled.status_code == 200

    response = client.get(f"/api/projects/{project_id}/tasks/{task_id}/events")

    assert response.status_code == 200
    events = _read_sse_events(response.text)
    assert len(events) == 1
    assert events[0][0] == "snapshot"
    assert events[0][1]["id"] == task_id
    assert events[0][1]["status"] == "cancelled"


def test_running_task_events_emit_heartbeat_and_terminal_change(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "SSE 动态任务"}).json()
    project_id = project["id"]
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            "ws_local",
            project_id,
            task_type="parse",
        )
        task_id = task.id
    finally:
        db.close()

    monkeypatch.setattr(tasks_api, "_SSE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tasks_api, "_SSE_HEARTBEAT_SECONDS", 0.01)

    def finish_task() -> None:
        time.sleep(0.05)
        worker_db = SessionLocal()
        try:
            worker_task = worker_db.get(task_service.ProjectTaskRow, task_id)
            assert worker_task is not None
            task_service._set_task(
                worker_db,
                worker_task,
                status="success",
                progress=100,
                message="测试完成",
            )
        finally:
            worker_db.close()

    worker = threading.Thread(target=finish_task)
    worker.start()
    response = client.get(f"/api/projects/{project_id}/tasks/{task_id}/events")
    worker.join(timeout=1)

    assert response.status_code == 200
    events = _read_sse_events(response.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["status"] == "pending"
    assert any(name == "heartbeat" for name, _data in events)
    assert any(
        name == "task" and data["status"] == "success"
        for name, data in events
    )


def test_running_task_events_emit_cancelled_change(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "SSE 连接中取消"}).json()
    project_id = project["id"]
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            "ws_local",
            project_id,
            task_type="parse",
        )
        task_id = task.id
    finally:
        db.close()

    monkeypatch.setattr(tasks_api, "_SSE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(tasks_api, "_SSE_HEARTBEAT_SECONDS", 0.01)

    def cancel_task() -> None:
        time.sleep(0.05)
        worker_db = SessionLocal()
        try:
            task_service.cancel_task(worker_db, "ws_local", project_id, task_id)
        finally:
            worker_db.close()

    worker = threading.Thread(target=cancel_task)
    worker.start()
    response = client.get(f"/api/projects/{project_id}/tasks/{task_id}/events")
    worker.join(timeout=1)

    assert response.status_code == 200
    events = _read_sse_events(response.text)
    assert events[0][0] == "snapshot"
    assert events[0][1]["status"] == "pending"
    terminal_index = next(
        index
        for index, (name, data) in enumerate(events)
        if name == "task" and data["status"] == "cancelled"
    )
    assert all(
        not (name == "task" and data["status"] in {"pending", "running"})
        for name, data in events[terminal_index + 1 :]
    )


def test_task_events_rejects_unknown_project_or_task(client):
    project_missing = client.get("/api/projects/no_project/tasks/no_task/events")
    assert project_missing.status_code == 404

    project = client.post("/api/projects", json={"name": "SSE 不存在任务"}).json()
    task_missing = client.get(f"/api/projects/{project['id']}/tasks/no_task/events")
    assert task_missing.status_code == 404
