"""
模块：项目任务服务（本机日用，默认异步线程执行）
用途：
  - 创建/执行 parse|analyze|outline|chapter|chapters|export
  - 默认后台线程 + 前端轮询；sync=1 同步跑完（测试）
  - 协作式取消（cancel_task + 检查点 TaskCancelled）
  - 大纲/正文生成时注入知识库检索（_kb_search_block，读 guidance.kb*）
对接：
  - POST/GET /api/projects/{id}/tasks；POST .../tasks/{id}/cancel
  - knowledge_service、export_service、llm_service、editor_state_service
  - 前端 useProjectPipeline
二次开发：可换 Redis/Celery/SSE；勿跨线程共享 Session；analyze 禁止注入知识库。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import ProjectEditorStateRow, ProjectTaskRow
from app.services import editor_state_service, file_service, llm_service, parse_service
from app.services.export_service import build_docx_bytes
from app.services.llm_service import LlmCallError, LlmConfigError
from app.services.project_service import get_project, update_project

ALLOWED_TYPES = frozenset(
    {"parse", "analyze", "outline", "chapter", "chapters", "export"}
)

# 进行中状态：取消与防重入共用
ACTIVE_STATUSES = frozenset({"pending", "running"})


class TaskCancelled(Exception):
    """用途：协作式取消；worker 在检查点抛出，不覆盖 cancelled 状态。"""


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
    force: bool = False,
) -> None:
    """
    用途：更新任务行并 commit。
    force=False 时若库中已是 cancelled，拒绝再改写为其它终态（除保留 cancelled）。
    """
    if not force:
        db.refresh(task)
        if task.status == "cancelled" and status not in (None, "cancelled"):
            raise TaskCancelled()
        if task.status == "cancelled" and status is None:
            # 进度心跳也不覆盖已取消
            raise TaskCancelled()
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


def _assert_not_cancelled(db: Session, task: ProjectTaskRow) -> None:
    """用途：检查点协作式取消。"""
    db.refresh(task)
    if task.status == "cancelled":
        raise TaskCancelled()


def _has_running_same_type(db: Session, project_id: str, task_type: str) -> bool:
    stmt = (
        select(ProjectTaskRow)
        .where(
            ProjectTaskRow.project_id == project_id,
            ProjectTaskRow.type == task_type,
            ProjectTaskRow.status.in_(tuple(ACTIVE_STATUSES)),
        )
        .limit(1)
    )
    return db.scalars(stmt).first() is not None


def cancel_task(
    db: Session, workspace_id: str, project_id: str, task_id: str
) -> ProjectTaskRow:
    """
    用途：将 pending/running 任务标为 cancelled；worker 在检查点退出。
    对接：POST /api/projects/{id}/tasks/{taskId}/cancel
    """
    task = get_task(db, workspace_id, project_id, task_id)
    if task.status not in ACTIVE_STATUSES:
        raise ValueError(f"任务已结束（{task.status}），无法取消")
    task.status = "cancelled"
    task.message = "已取消"
    task.error = "用户取消"
    # 进度保留当前值，便于 UI 展示中断点
    task.updated_at = _now()
    db.commit()
    db.refresh(task)
    return task


def fail_interrupted_tasks(db: Session) -> int:
    """
    用途：进程重启时把遗留 pending/running 标为 failed。
    对接：main.lifespan 启动时调用。
    """
    stmt = select(ProjectTaskRow).where(
        ProjectTaskRow.status.in_(tuple(ACTIVE_STATUSES))
    )
    rows = list(db.scalars(stmt).all())
    for t in rows:
        t.status = "failed"
        t.progress = 100
        t.message = "进程中断"
        t.error = "服务重启，任务未完成，请重试"
        t.updated_at = _now()
    if rows:
        db.commit()
    return len(rows)


def create_task_record(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    task_type: str,
    payload: dict | None = None,
) -> ProjectTaskRow:
    """用途：仅创建 pending 任务行，不执行。"""
    if task_type not in ALLOWED_TYPES:
        raise ValueError(f"不支持的任务类型: {task_type}")
    get_project(db, workspace_id, project_id)
    if _has_running_same_type(db, project_id, task_type):
        raise ValueError(f"已有进行中的「{task_type}」任务，请等待完成或稍后重试")
    task = ProjectTaskRow(
        id=f"task_{secrets.token_hex(8)}",
        project_id=project_id,
        type=task_type,
        status="pending",
        progress=0,
        message="排队中…",
        payload_json=_dumps(payload or {}),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _execute_task(db: Session, workspace_id: str, task: ProjectTaskRow) -> None:
    """用途：在给定 Session 上执行任务体；支持协作式取消。"""
    payload: dict = {}
    if task.payload_json:
        try:
            payload = json.loads(task.payload_json) or {}
        except json.JSONDecodeError:
            payload = {}
    project_id = task.project_id
    try:
        _assert_not_cancelled(db, task)
        _set_task(db, task, status="running", progress=5, message="任务开始…")
        if task.type == "parse":
            _run_parse(db, workspace_id, project_id, task)
        elif task.type == "analyze":
            _run_analyze(db, workspace_id, project_id, task)
        elif task.type == "outline":
            _run_outline(db, workspace_id, project_id, task)
        elif task.type == "chapter":
            _run_chapter(db, workspace_id, project_id, task, payload)
        elif task.type == "chapters":
            _run_chapters(db, workspace_id, project_id, task, payload)
        elif task.type == "export":
            _run_export(db, workspace_id, project_id, task)
        else:
            raise ValueError(f"未知任务类型: {task.type}")
    except TaskCancelled:
        # 保持 cancel_task 写入的状态，仅补全 message
        db.refresh(task)
        if task.status == "cancelled":
            if not task.message:
                task.message = "已取消"
            task.updated_at = _now()
            db.commit()
        return
    except (LlmConfigError, LlmCallError, ValueError, RuntimeError, KeyError) as exc:
        try:
            _set_task(
                db,
                task,
                status="failed",
                progress=100,
                message="任务失败",
                error=str(exc),
            )
        except TaskCancelled:
            return
    except Exception as exc:  # noqa: BLE001
        try:
            _set_task(
                db,
                task,
                status="failed",
                progress=100,
                message="任务异常",
                error=f"{type(exc).__name__}: {exc}",
            )
        except TaskCancelled:
            return


def create_and_run_task(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    task_type: str,
    payload: dict | None = None,
) -> ProjectTaskRow:
    """用途：同步创建并执行（测试 / sync=1）。"""
    task = create_task_record(
        db, workspace_id, project_id, task_type=task_type, payload=payload
    )
    _execute_task(db, workspace_id, task)
    db.refresh(task)
    return task


def _bg_worker(task_id: str, workspace_id: str) -> None:
    """用途：后台线程：独立 Session 执行任务。"""
    db = SessionLocal()
    try:
        task = db.get(ProjectTaskRow, task_id)
        if task is None:
            return
        _execute_task(db, workspace_id, task)
    finally:
        db.close()


def enqueue_task(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    task_type: str,
    payload: dict | None = None,
) -> ProjectTaskRow:
    """
    用途：创建任务并后台线程执行，立即返回 pending/running 快照。
    """
    task = create_task_record(
        db, workspace_id, project_id, task_type=task_type, payload=payload
    )
    thread = threading.Thread(
        target=_bg_worker,
        args=(task.id, workspace_id),
        name=f"task-{task.id}",
        daemon=True,
    )
    thread.start()
    db.refresh(task)
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
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=20, message=f"解析 {files[0].filename}…")
    path = file_service.resolve_path(settings, project_id, files[0].stored_name)
    if not path.exists():
        raise RuntimeError("文件已丢失，请重新上传")
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=50, message="提取文本…")
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
        result={
            "parsedMarkdown": md[:2000],
            "chars": len(md),
            "filename": files[0].filename,
        },
    )


def _run_analyze(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    from app.services.editor_state_service import normalize_analysis

    _set_task(db, task, progress=15, message="读取解析文本…")
    state = _ensure_state(db, project_id)
    source = (state.parsed_markdown or state.analysis_overview or "").strip()
    if not source:
        raise ValueError("尚无解析文本，请先上传并解析招标文件")
    clip = source[:18000]
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=40, message="调用模型生成结构化招标分析…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标技术标分析助手。只输出一个 JSON 对象，不要 Markdown 围栏。\n"
                    "结构：\n"
                    '{"overview":"200-400字项目概述",'
                    '"techRequirements":["技术要求1","…"],'
                    '"rejectionRisks":["废标/风险1","…"],'
                    '"scoringPoints":[{"name":"评分项","weight":"20%"}]}\n'
                    "不要编造原文没有的硬性数字；列表各 3～8 条。"
                ),
            },
            {"role": "user", "content": f"招标文件摘录：\n\n{clip}"},
        ],
        temperature=0.3,
        timeout_sec=180.0,
    )
    parsed = _try_parse_analysis_json(result.content)
    if parsed is None:
        analysis = normalize_analysis(
            {
                "overview": result.content,
                "techRequirements": [],
                "rejectionRisks": [],
                "scoringPoints": [],
            }
        )
        msg = "招标分析完成（结构解析失败，已保存原文概述）"
    else:
        analysis = normalize_analysis(parsed)
        msg = "招标分析完成（结构化）"
    editor_state_service.upsert_editor_state(
        db,
        workspace_id,
        project_id,
        analysis=analysis,
        analysis_overview=analysis.get("overview") or "",
    )
    update_project(
        db, workspace_id, project_id, status="analyzing", technical_plan_step=2
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message=msg,
        result={"analysis": analysis, "model": result.model},
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
    from app.services.editor_state_service import normalize_analysis

    raw_a = None
    if state.analysis_json:
        try:
            raw_a = json.loads(state.analysis_json)
        except json.JSONDecodeError:
            raw_a = None
    analysis = normalize_analysis(
        raw_a,
        fallback_overview=state.analysis_overview or "",
    )
    ctx = (analysis.get("overview") or state.parsed_markdown or "")[:12000]
    if not ctx.strip():
        raise ValueError("请先完成解析或招标分析")
    struct_hint = _analysis_prompt_block(analysis)
    focus = guidance.get("chapterFocus") or guidance.get("chapter_focus") or ""
    kb_query = f"{(analysis.get('overview') or '')[:800]}\n{focus}".strip()
    kb_block, kb_citations = _kb_search_block(
        db, workspace_id, kb_query, project_id=project_id
    )
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=35, message="生成三级大纲…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是技术标大纲专家。输出严格 JSON 数组，不要 Markdown 围栏。\n"
                    '元素结构：{"id":"n1","title":"章节名","targetWords":3000,'
                    '"description":"要点","children":[...]}\n'
                    "生成 6～12 个一级章节，二级 2～5 个；id 全局唯一。"
                    "大纲应覆盖评分点与关键技术要求。"
                    "若提供知识库参考，可借鉴目录组织方式，但不得引入招标文件未要求的硬性指标。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"目标字数：{guidance.get('targetWordCount') or guidance.get('target_word_count') or 80000}\n"
                    f"侧重：{focus or '无'}\n"
                    f"{struct_hint}\n"
                    f"{kb_block}\n"
                    f"材料：\n{ctx}"
                ),
            },
        ],
        temperature=0.35,
        timeout_sec=180.0,
    )
    outline = _parse_json_array(result.content)
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
    _set_task(db, task, progress=80, message="写入大纲…")
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
        result={
            "outlineCount": len(outline),
            "chapterCount": len(chapters),
            "kbCitations": kb_citations[:8],
        },
    )


def _load_guidance_dict(db: Session, project_id: str) -> dict:
    """用途：从 editor-state 读取 guidance JSON。"""
    state = db.get(ProjectEditorStateRow, project_id)
    if state is None or not state.guidance_json:
        return {}
    try:
        data = json.loads(state.guidance_json) or {}
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _guidance_kb_opts(guidance: dict) -> tuple[bool, list[str] | None]:
    """
    用途：解析 guidance 中的知识库开关与文件夹范围。
    返回：(enabled, folder_ids|None)
    - kbEnabled/kb_enabled 为 false 时不检索
    - kbFolderIds 非空则限定文件夹；空/缺省 = 全库
    """
    enabled = guidance.get("kbEnabled")
    if enabled is None:
        enabled = guidance.get("kb_enabled")
    if enabled is False or enabled == 0 or enabled == "false":
        return False, None
    raw = guidance.get("kbFolderIds")
    if raw is None:
        raw = guidance.get("kb_folder_ids")
    folder_ids: list[str] | None = None
    if isinstance(raw, list) and raw:
        folder_ids = [str(x) for x in raw if x]
    return True, folder_ids


def _kb_search_block(
    db: Session,
    workspace_id: str,
    query: str,
    *,
    project_id: str | None = None,
) -> tuple[str, list]:
    """
    用途：检索知识库并返回 prompt 块与 citations；失败/空库静默跳过。
    对接：knowledge_service.search_prompt_block；可读项目 guidance 过滤文件夹
    """
    try:
        from app.services import knowledge_service

        folder_ids: list[str] | None = None
        if project_id:
            g = _load_guidance_dict(db, project_id)
            ok, folder_ids = _guidance_kb_opts(g)
            if not ok:
                return "", []
        return knowledge_service.search_prompt_block(
            db,
            workspace_id,
            query,
            top_k=5,
            folder_ids=folder_ids,
        )
    except Exception:  # noqa: BLE001
        return "", []


def _analysis_prompt_block(analysis: dict) -> str:
    """用途：把结构化分析压成短摘要注入生成 prompt。"""
    lines = ["【招标分析结构】"]
    tr = analysis.get("techRequirements") or []
    if tr:
        lines.append("技术要求：" + "；".join(str(x) for x in tr[:8]))
    sp = analysis.get("scoringPoints") or []
    if sp:
        parts = []
        for p in sp[:8]:
            if isinstance(p, dict):
                parts.append(f"{p.get('name','')}({p.get('weight','')})")
        if parts:
            lines.append("评分点：" + "；".join(parts))
    rr = analysis.get("rejectionRisks") or []
    if rr:
        lines.append("废标风险：" + "；".join(str(x) for x in rr[:6]))
    return "\n".join(lines) if len(lines) > 1 else ""


def _try_parse_analysis_json(text: str) -> dict | None:
    """用途：从模型输出抠 analysis JSON。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and (
            "overview" in data or "techRequirements" in data or "scoringPoints" in data
        ):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def _generate_one_chapter_body(
    db: Session,
    workspace_id: str,
    *,
    title: str,
    target_words: int,
    overview: str,
    facts_txt: str,
    analysis_block: str = "",
    project_id: str | None = None,
) -> tuple[str, list]:
    """
    用途：单章 LLM 生成，供 chapter / chapters 复用。
    返回：(正文, kbCitations)
    """
    kb_query = f"{title}\n{str(overview)[:500]}"
    kb_block, kb_citations = _kb_search_block(
        db, workspace_id, kb_query, project_id=project_id
    )
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是技术标正文写手。用正式中文 Markdown 撰写指定章节，"
                    "结构清晰、少空话，可含小标题与条目。不要输出与本章无关的全书目录。"
                    "应呼应评分点与技术要求。"
                    "若提供知识库参考，可借鉴写法与要点；禁止编造招标中不存在的硬性指标；"
                    "与招标文件或全局事实冲突时，以招标与事实为准。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"章节标题：{title}\n"
                    f"目标字数约：{target_words}\n"
                    f"项目概述：\n{str(overview)[:4000]}\n"
                    f"{analysis_block}\n"
                    f"全局事实：\n{facts_txt[:3000] or '无'}\n"
                    f"{kb_block}\n"
                    "请直接输出本章正文。"
                ),
            },
        ],
        temperature=0.4,
        timeout_sec=240.0,
    )
    return result.content, kb_citations


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
            (
                c
                for c in chapters
                if isinstance(c, dict) and not (c.get("body") or "").strip()
            ),
            chapters[0] if isinstance(chapters[0], dict) else None,
        )
    if not isinstance(target, dict):
        raise ValueError("未找到可生成的章节")
    title = str(target.get("title") or "章节")
    overview = state_data.get("analysisOverview") or ""
    analysis = state_data.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    facts = state_data.get("facts") or []
    facts_txt = ""
    if isinstance(facts, list):
        facts_txt = "\n".join(
            f"- {f.get('content')}"
            for f in facts
            if isinstance(f, dict) and f.get("content")
        )
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=30, message=f"生成章节：{title}")
    body, kb_citations = _generate_one_chapter_body(
        db,
        workspace_id,
        title=title,
        target_words=int(target.get("targetWords") or 3000),
        overview=str(overview),
        facts_txt=facts_txt,
        analysis_block=_analysis_prompt_block(analysis),
        project_id=project_id,
    )
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
        result={
            "chapterId": target.get("id"),
            "title": title,
            "chars": len(body),
            "kbCitations": kb_citations[:8],
        },
    )


def _run_chapters(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    payload: dict,
) -> None:
    """
    用途：串行生成全部（或仅空）章节；progress 按章递增。
    payload.onlyEmpty 默认 true。
    """
    only_empty = payload.get("onlyEmpty", payload.get("only_empty", True))
    if isinstance(only_empty, str):
        only_empty = only_empty.lower() not in ("0", "false", "no")
    state_data = editor_state_service.get_editor_state(db, workspace_id, project_id)
    chapters = state_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("尚无章节列表，请先生成大纲")
    targets = [
        c
        for c in chapters
        if isinstance(c, dict)
        and (not only_empty or not (c.get("body") or "").strip())
    ]
    if not targets:
        _set_task(
            db,
            task,
            status="success",
            progress=100,
            message="没有需要生成的空章节",
            result={"generated": 0},
        )
        return

    overview = state_data.get("analysisOverview") or ""
    analysis = state_data.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    analysis_block = _analysis_prompt_block(analysis)
    facts = state_data.get("facts") or []
    facts_txt = ""
    if isinstance(facts, list):
        facts_txt = "\n".join(
            f"- {f.get('content')}"
            for f in facts
            if isinstance(f, dict) and f.get("content")
        )

    # 工作用可变副本
    working = [dict(c) if isinstance(c, dict) else c for c in chapters]
    done = 0
    total = len(targets)
    all_cites: list = []
    for tgt in targets:
        _assert_not_cancelled(db, task)
        tid = tgt.get("id")
        title = str(tgt.get("title") or "章节")
        pct = int(10 + (done / total) * 85)
        _set_task(
            db,
            task,
            progress=pct,
            message=f"生成章节 {done + 1}/{total}：{title}",
        )
        body, kb_cites = _generate_one_chapter_body(
            db,
            workspace_id,
            title=title,
            target_words=int(tgt.get("targetWords") or 3000),
            overview=str(overview),
            facts_txt=facts_txt,
            analysis_block=analysis_block,
            project_id=project_id,
        )
        all_cites.extend(kb_cites[:3])
        plain = body.replace(" ", "")
        for i, c in enumerate(working):
            if isinstance(c, dict) and c.get("id") == tid:
                working[i] = {
                    **c,
                    "body": body,
                    "preview": body[:96].replace("\n", " "),
                    "wordCount": len(plain),
                    "status": "needs_review",
                }
                break
        editor_state_service.upsert_editor_state(
            db, workspace_id, project_id, chapters=working
        )
        done += 1
        time.sleep(0.6)  # 轻微限流，避免打爆上游

    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=5
    )
    _set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"已生成 {done} 章",
        result={
            "generated": done,
            "onlyEmpty": bool(only_empty),
            "kbCitations": all_cites[:12],
        },
    )


def _run_export(
    db: Session, workspace_id: str, project_id: str, task: ProjectTaskRow
) -> None:
    settings = get_settings()
    _assert_not_cancelled(db, task)
    _set_task(db, task, progress=40, message="组装 Word…")
    data, filename = build_docx_bytes(db, workspace_id, project_id)
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
