"""
模块：项目任务服务（本机日用同步执行）
用途：创建并执行 parse / analyze / outline / chapter / export 任务，落库进度与结果。
对接：POST/GET /api/projects/{id}/tasks
二次开发：可改为后台线程或 Redis 队列；前端轮询接口形状保持不变。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import ProjectEditorStateRow, ProjectFileRow, ProjectTaskRow
from app.services import editor_state_service, file_service, llm_service, parse_service
from app.services.export_service import build_docx_bytes
from app.services.llm_service import LlmCallError, LlmConfigError
from app.services.project_service import get_project, update_project

ALLOWED_TYPES = frozenset(
    {"parse", "analyze", "outline", "chapter", "export"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def get_task(db: Session, workspace_id: str, project_id: str, task_id: str) -> ProjectTaskRow:
    get_project(db, workspace_id, project_id)
    task = db.get(ProjectTaskRow, task_id)
    if task is None or task.project_id != project_id:
        raise KeyError(task_id)
    return task


def list_tasks(db: Session, workspace_id: str, project_id: str) -> list[ProjectTaskRow]:
    get_project(db, workspace_id, project_id)
    stmt = (
        select(ProjectTaskRow)
        .where(ProjectTaskRow.project_id == project_id)
        .order_by(ProjectTaskRow.created_at.desc())
        .limit(50)
    )
    return list(db.scalars(stmt).all())


def task_to_dict(task: ProjectTaskRow) -> dict:
    result = None
    if task.result_json:
        try:
            result = json.loads(task.result_json)
        except json.JSONDecodeError:
            result = {"raw": task.result_json}
    return {
        "id": task.id,
        "projectId": task.project_id,
        "type": task.type,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "result": result,
        "error": task.error,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
    }


def _set_task(
    db: Session,
    task: ProjectTaskRow,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    if status is not None:
        task.status = status
    if progress is not None:
        task.progress = max(0, min(100, progress))
    if message is not None:
        task.message = message[:1000]
    if result is not None:
        task.result_json = _dumps(result)
    if error is not None:
        task.error = error[:4000]
    task.updated_at = _now()
    db.commit()
    db.refresh(task)


def create_and_run_task(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    task_type: str,
    payload: dict | None = None,
) -> ProjectTaskRow:
    """
    用途：创建任务并同步执行（个人版简单可靠）。
    """
    if task_type not in ALLOWED_TYPES:
        raise ValueError(f"不支持的任务类型: {task_type}")
    get_project(db, workspace_id, project_id)
    payload = payload or {}
    task = ProjectTaskRow(
        id=f"task_{secrets.token_hex(8)}",
        project_id=project_id,
        type=task_type,
        status="running",
        progress=5,
        message="任务开始…",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        if task_type == "parse":
            _run_parse(db, workspace_id, project_id, task)
        elif task_type == "analyze":
            _run_analyze(db, workspace_id, project_id, task)
        elif task_type == "outline":
            _run_outline(db, workspace_id, project_id, task)
        elif task_type == "chapter":
            _run_chapter(db, workspace_id, project_id, task, payload)
        elif task_type == "export":
            _run_export(db, workspace_id, project_id, task)
    except (LlmConfigError, LlmCallError, ValueError, RuntimeError, KeyError) as exc:
        _set_task(
            db,
            task,
            status="failed",
            progress=100,
            message="任务失败",
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — 个人版兜底
        _set_task(
            db,
            task,
            status="failed",
            progress=100,
            message="任务异常",
            error=f"{type(exc).__name__}: {exc}",
        )
    return task


def _ensure_state(db: Session, project_id: str) -> ProjectEditorStateRow:
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None:
        row = ProjectEditorStateRow(project_id=project_id, mode="ALIGNED")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _run_parse(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    settings = get_settings()
    files = file_service.list_files(db, workspace_id, project_id)
    if not files:
        raise ValueError("请先上传招标文件")
    _set_task(db, task, progress=20, message=f"解析 {files[0].filename}…")
    path = file_service.resolve_path(settings, project_id, files[0].stored_name)
    if not path.exists():
        raise RuntimeError("文件已丢失，请重新上传")
    md = parse_service.parse_file_to_markdown(path, files[0].filename)
    state = _ensure_state(db, project_id)
    state.parsed_markdown = md
    state.updated_at = _now()
    db.commit()
    update_project(
        db, workspace_id, project_id, status="analyzing", technical_plan_step=1
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message="解析完成",
        result={"parsedMarkdown": md[:2000], "chars": len(md), "filename": files[0].filename},
    )


def _run_analyze(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    _set_task(db, task, progress=15, message="读取解析文本…")
    state = _ensure_state(db, project_id)
    source = (state.parsed_markdown or state.analysis_overview or "").strip()
    if not source:
        raise ValueError("尚无解析文本，请先上传并解析招标文件")
    clip = source[:18000]
    _set_task(db, task, progress=40, message="调用模型生成招标分析…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标技术标分析助手。根据招标文件摘录，用中文输出：\n"
                    "1) 项目概述（200～400字）\n"
                    "2) 关键技术要求（条目）\n"
                    "3) 评分关注点\n"
                    "4) 潜在废标/风险点\n"
                    "不要编造原文没有的硬性数字。"
                ),
            },
            {"role": "user", "content": f"招标文件摘录：\n\n{clip}"},
        ],
        temperature=0.3,
        timeout_sec=180.0,
    )
    overview = result.content
    state.analysis_overview = overview
    state.updated_at = _now()
    db.commit()
    update_project(
        db, workspace_id, project_id, status="analyzing", technical_plan_step=2
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message="招标分析完成",
        result={"analysisOverview": overview, "model": result.model},
    )


def _run_outline(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    state = _ensure_state(db, project_id)
    guidance = {}
    if state.guidance_json:
        try:
            guidance = json.loads(state.guidance_json) or {}
        except json.JSONDecodeError:
            guidance = {}
    ctx = (state.analysis_overview or state.parsed_markdown or "")[:12000]
    if not ctx.strip():
        raise ValueError("请先完成解析或招标分析")
    _set_task(db, task, progress=35, message="生成三级大纲…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是技术标大纲专家。输出严格 JSON 数组，不要 Markdown 围栏。\n"
                    "元素结构：{\"id\":\"n1\",\"title\":\"章节名\",\"targetWords\":3000,"
                    "\"description\":\"要点\",\"children\":[...]}\n"
                    "生成 6～12 个一级章节，二级 2～5 个；id 全局唯一。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"目标字数：{guidance.get('targetWordCount') or guidance.get('target_word_count') or 80000}\n"
                    f"侧重：{guidance.get('chapterFocus') or guidance.get('chapter_focus') or '无'}\n"
                    f"材料：\n{ctx}"
                ),
            },
        ],
        temperature=0.35,
        timeout_sec=180.0,
    )
    outline = _parse_json_array(result.content)
    # 同步简易章节列表
    chapters = []
    for i, node in enumerate(outline):
        if not isinstance(node, dict):
            continue
        nid = str(node.get("id") or f"ch_{i+1}")
        title = str(node.get("title") or f"第{i+1}章")
        chapters.append(
            {
                "id": nid,
                "title": title,
                "body": "",
                "preview": "（待生成）",
                "wordCount": 0,
                "status": "pending",
                "targetWords": int(node.get("targetWords") or 3000),
            }
        )
    editor_state_service.upsert_editor_state(
        db,
        workspace_id,
        project_id,
        outline=outline,
        chapters=chapters,
        mode="ALIGNED",
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=3
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"大纲已生成（{len(outline)} 章）",
        result={"outlineCount": len(outline), "chapterCount": len(chapters)},
    )


def _run_chapter(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    payload: dict,
) -> None:
    chapter_id = payload.get("chapterId") or payload.get("chapter_id")
    state_data = editor_state_service.get_editor_state(db, workspace_id, project_id)
    chapters = state_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("尚无章节列表，请先生成大纲")
    target = None
    if chapter_id:
        target = next(
            (c for c in chapters if isinstance(c, dict) and c.get("id") == chapter_id),
            None,
        )
    if target is None:
        target = next(
            (c for c in chapters if isinstance(c, dict) and not (c.get("body") or "").strip()),
            chapters[0] if isinstance(chapters[0], dict) else None,
        )
    if not isinstance(target, dict):
        raise ValueError("未找到可生成的章节")
    title = str(target.get("title") or "章节")
    overview = state_data.get("analysisOverview") or ""
    facts = state_data.get("facts") or []
    facts_txt = ""
    if isinstance(facts, list):
        facts_txt = "\n".join(
            f"- {f.get('content')}" for f in facts if isinstance(f, dict) and f.get("content")
        )
    _set_task(db, task, progress=30, message=f"生成章节：{title}")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是技术标正文写手。用正式中文 Markdown 撰写指定章节，"
                    "结构清晰、少空话，可含小标题与条目。不要输出与本章无关的全书目录。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"章节标题：{title}\n"
                    f"目标字数约：{target.get('targetWords') or 3000}\n"
                    f"项目概述：\n{str(overview)[:4000]}\n"
                    f"全局事实：\n{facts_txt[:3000] or '无'}\n"
                    "请直接输出本章正文。"
                ),
            },
        ],
        temperature=0.4,
        timeout_sec=240.0,
    )
    body = result.content
    new_chapters = []
    for c in chapters:
        if not isinstance(c, dict):
            continue
        if c.get("id") == target.get("id"):
            plain = body.replace(" ", "")
            new_chapters.append(
                {
                    **c,
                    "body": body,
                    "preview": body[:96].replace("\n", " "),
                    "wordCount": len(plain),
                    "status": "needs_review",
                }
            )
        else:
            new_chapters.append(c)
    editor_state_service.upsert_editor_state(
        db, workspace_id, project_id, chapters=new_chapters
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=5
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"章节「{title}」已生成",
        result={"chapterId": target.get("id"), "title": title, "chars": len(body)},
    )


def _run_export(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    settings = get_settings()
    _set_task(db, task, progress=40, message="组装 Word…")
    data, filename = build_docx_bytes(db, workspace_id, project_id)
    # 落盘便于下载
    out_dir = file_service._upload_root(settings) / project_id / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stored = f"export_{secrets.token_hex(4)}.docx"
    path = out_dir / stored
    path.write_bytes(data)
    update_project(
        db, workspace_id, project_id, status="exported", technical_plan_step=6
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message="导出完成",
        result={
            "filename": filename,
            "storedName": stored,
            "downloadPath": f"/projects/{project_id}/export/download/{stored}",
            "size": len(data),
        },
    )


def _parse_json_array(text: str) -> list:
    """用途：从模型输出中抠出 JSON 数组。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("outline"), list):
            return data["outline"]
    except json.JSONDecodeError:
        pass
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, list):
            return data
    raise ValueError("模型未返回合法大纲 JSON，请重试或检查模型")
