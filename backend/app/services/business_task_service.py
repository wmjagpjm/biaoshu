"""
模块：商务标任务执行器
用途：biz_qualify / biz_toc / biz_quote / biz_commit 的 LLM 生成与写回 editor-state。
对接：
  - task_service._execute_task 分发
  - editor_state_service（businessQualify 等）
  - 可选 knowledge_service（与大纲/章节同口径，读 guidance.kb*）
二次开发：
  - 勿编造招标未出现的硬性资质编号
  - JSON 契约变更时同步 tests/test_business_bid_mvp.py
  - 四类 writer 写回必须走 _upsert_editor_state_for_task（固定 source=task + 异常脱敏）
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ProjectTaskRow
from app.services import editor_state_service, llm_service
from app.services.project_service import update_project

# 商务 writer upsert 非版本冲突失败：固定中文脱敏（禁止回显 SQL/路径/表名/异常类型）
MSG_TASK_EDITOR_UPSERT_FAILED = "编辑内容写入失败，请重试"


def _upsert_editor_state_for_task(
    db: Session,
    workspace_id: str,
    project_id: str,
    **kwargs: Any,
) -> dict:
    """
    用途：商务四类任务 writer 专用 upsert 包装；固定 revision_source_kind=task。
    对接：biz_qualify / biz_toc / biz_quote / biz_commit 真实写点。
    二次开发：
      - EditorStateVersionConflict 原样上抛，保持 stale 固定语义；
      - 其他 upsert 异常脱敏为固定 RuntimeError，from 保留原链，禁止进 REST/SSE。
    """
    kwargs.pop("revision_source_kind", None)
    try:
        return editor_state_service.upsert_editor_state(
            db,
            workspace_id,
            project_id,
            revision_source_kind="task",
            **kwargs,
        )
    except editor_state_service.EditorStateVersionConflict:
        raise
    except Exception as exc:  # noqa: BLE001 — 仅 upsert 窄范围脱敏
        raise RuntimeError(MSG_TASK_EDITOR_UPSERT_FAILED) from exc


def _parse_json_value(text: str) -> Any:
    """用途：从模型输出中抠出 JSON（数组或对象）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 尝试数组
    start_a, end_a = raw.find("["), raw.rfind("]")
    if start_a >= 0 and end_a > start_a:
        try:
            return json.loads(raw[start_a : end_a + 1])
        except json.JSONDecodeError:
            pass
    start_o, end_o = raw.find("{"), raw.rfind("}")
    if start_o >= 0 and end_o > start_o:
        return json.loads(raw[start_o : end_o + 1])
    raise ValueError("模型未返回合法 JSON，请重试或检查模型")


def _source_markdown(db: Session, workspace_id: str, project_id: str) -> str:
    state = editor_state_service.get_editor_state(db, workspace_id, project_id)
    md = (state.get("parsedMarkdown") or "").strip()
    if not md:
        raise ValueError("尚无解析文本，请先上传并解析招标文件")
    return md[:18000]


def _kb_block(db: Session, workspace_id: str, project_id: str, query: str) -> str:
    """用途：可选知识库参考块；失败则空串。"""
    try:
        from app.services.task_service import _kb_search_block

        block, _cites = _kb_search_block(
            db, workspace_id, query, project_id=project_id
        )
        return block or ""
    except Exception:
        return ""


def _normalize_qualify(items: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "pending")
        if status not in ("pending", "matched", "partial", "missing"):
            status = "pending"
        out.append(
            {
                "id": str(raw.get("id") or f"q{i + 1}"),
                "requirement": str(raw.get("requirement") or "").strip(),
                "response": str(raw.get("response") or "").strip(),
                "evidence": str(raw.get("evidence") or "").strip(),
                "status": status,
            }
        )
    return [x for x in out if x["requirement"]]


def _normalize_toc(items: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        st = str(raw.get("status") or "required")
        if st not in ("required", "optional", "done"):
            st = "required"
        out.append(
            {
                "id": str(raw.get("id") or f"t{i + 1}"),
                "title": str(raw.get("title") or "").strip(),
                "category": str(raw.get("category") or "其它").strip() or "其它",
                "status": st,
                "checked": bool(raw.get("checked", False)),
                "note": str(raw.get("note") or ""),
            }
        )
    return [x for x in out if x["title"]]


def _normalize_quote(raw: Any) -> dict:
    rows_in: list = []
    notes = ""
    if isinstance(raw, dict):
        rows_in = raw.get("rows") if isinstance(raw.get("rows"), list) else []
        notes = str(raw.get("notes") or "")
    elif isinstance(raw, list):
        rows_in = raw
    rows: list[dict] = []
    for i, r in enumerate(rows_in):
        if not isinstance(r, dict):
            continue
        rows.append(
            {
                "id": str(r.get("id") or f"r{i + 1}"),
                "name": str(r.get("name") or "").strip(),
                "unit": str(r.get("unit") or ""),
                "quantity": str(r.get("quantity") or ""),
                "unitPrice": str(r.get("unitPrice") or r.get("unit_price") or ""),
                "amount": str(r.get("amount") or ""),
                "remark": str(r.get("remark") or ""),
            }
        )
    return {"rows": [x for x in rows if x["name"]], "notes": notes}


def _normalize_commit(items: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "id": str(raw.get("id") or f"c{i + 1}"),
                "title": str(raw.get("title") or "").strip(),
                "body": str(raw.get("body") or "").strip(),
                "needsStamp": bool(
                    raw.get("needsStamp")
                    if "needsStamp" in raw
                    else raw.get("needs_stamp", False)
                ),
            }
        )
    return [x for x in out if x["title"] or x["body"]]


def run_biz_qualify(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    *,
    set_task,
    assert_not_cancelled,
    expected_state_version: str,
) -> None:
    """
    用途：从解析文生成资格响应条目列表。
    对接：任务类型 biz_qualify → businessQualify；最终写 CAS。
    二次开发：expected 必填合法版本，禁止默认 None 静默兼容写。
    """
    assert_not_cancelled(db, task)
    set_task(db, task, progress=20, message="读取解析文本…")
    source = _source_markdown(db, workspace_id, project_id)
    kb = _kb_block(db, workspace_id, project_id, source[:800])
    assert_not_cancelled(db, task)
    set_task(db, task, progress=45, message="生成资格响应草稿…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标商务标助手。根据招标文件解析文本，抽取资格条件并起草响应。\n"
                    "只输出 JSON 数组，每项字段："
                    "id, requirement, response, evidence, status"
                    "（status 取 pending|matched|partial|missing）。\n"
                    "禁止编造招标未出现的硬性资质编号或业绩；不确定的 status 用 pending 或 partial。\n"
                    "evidence 可为空字符串或附件命名建议。"
                ),
            },
            {
                "role": "user",
                "content": f"{kb}\n\n# 招标解析\n{source}\n\n请输出资格响应 JSON 数组。",
            },
        ],
        temperature=0.2,
    )
    items = _normalize_qualify(_parse_json_value(result.content))
    if not items:
        raise ValueError("模型未返回有效资格条目")
    assert_not_cancelled(db, task)
    _upsert_editor_state_for_task(
        db,
        workspace_id,
        project_id,
        business_qualify=items,
        expected_state_version=expected_state_version,
        actor_user_id=getattr(task, "actor_user_id", None),
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=2
    )
    set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"已生成 {len(items)} 条资格响应",
        result={"count": len(items)},
    )


def run_biz_toc(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    *,
    set_task,
    assert_not_cancelled,
    expected_state_version: str,
) -> None:
    """
    用途：生成商务递交材料目录清单。对接：biz_toc → businessToc；最终写 CAS。
    二次开发：expected 必填合法版本，禁止默认 None 静默兼容写。
    """
    assert_not_cancelled(db, task)
    set_task(db, task, progress=20, message="读取解析文本…")
    source = _source_markdown(db, workspace_id, project_id)
    kb = _kb_block(db, workspace_id, project_id, "商务递交材料清单 投标文件组成")
    assert_not_cancelled(db, task)
    set_task(db, task, progress=45, message="生成目录清单…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标商务标助手。根据招标文件，列出拟递交的商务材料目录。\n"
                    "只输出 JSON 数组，每项：id, title, category, status"
                    "（required|optional|done）, checked(bool), note。\n"
                    "勿编造招标未要求的强制材料。"
                ),
            },
            {
                "role": "user",
                "content": f"{kb}\n\n# 招标解析\n{source}\n\n请输出材料清单 JSON 数组。",
            },
        ],
        temperature=0.2,
    )
    items = _normalize_toc(_parse_json_value(result.content))
    if not items:
        raise ValueError("模型未返回有效目录条目")
    assert_not_cancelled(db, task)
    _upsert_editor_state_for_task(
        db,
        workspace_id,
        project_id,
        business_toc=items,
        expected_state_version=expected_state_version,
        actor_user_id=getattr(task, "actor_user_id", None),
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=3
    )
    set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"已生成 {len(items)} 项材料",
        result={"count": len(items)},
    )


def run_biz_quote(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    *,
    set_task,
    assert_not_cancelled,
    expected_state_version: str,
) -> None:
    """
    用途：生成报价分项表骨架。对接：biz_quote → businessQuote；最终写 CAS。
    二次开发：expected 必填合法版本，禁止默认 None 静默兼容写。
    """
    assert_not_cancelled(db, task)
    set_task(db, task, progress=20, message="读取解析文本…")
    source = _source_markdown(db, workspace_id, project_id)
    assert_not_cancelled(db, task)
    set_task(db, task, progress=45, message="生成报价表骨架…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标商务标助手。根据招标文件起草分项报价表骨架。\n"
                    "只输出 JSON 对象："
                    '{"rows":[{"id","name","unit","quantity","unitPrice","amount","remark"}],'
                    '"notes":"报价说明"}。\n'
                    "金额可留空字符串；勿编造招标未列明的采购项。"
                ),
            },
            {
                "role": "user",
                "content": f"# 招标解析\n{source}\n\n请输出报价 JSON 对象。",
            },
        ],
        temperature=0.2,
    )
    quote = _normalize_quote(_parse_json_value(result.content))
    if not quote["rows"] and not quote["notes"]:
        raise ValueError("模型未返回有效报价内容")
    assert_not_cancelled(db, task)
    _upsert_editor_state_for_task(
        db,
        workspace_id,
        project_id,
        business_quote=quote,
        expected_state_version=expected_state_version,
        actor_user_id=getattr(task, "actor_user_id", None),
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=4
    )
    set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"已生成 {len(quote['rows'])} 行报价",
        result={"count": len(quote["rows"])},
    )


def run_biz_commit(
    db: Session,
    workspace_id: str,
    project_id: str,
    task: ProjectTaskRow,
    *,
    set_task,
    assert_not_cancelled,
    expected_state_version: str,
) -> None:
    """
    用途：生成授权与承诺正文块。对接：biz_commit → businessCommit；最终写 CAS。
    二次开发：expected 必填合法版本，禁止默认 None 静默兼容写。
    """
    assert_not_cancelled(db, task)
    set_task(db, task, progress=20, message="读取解析文本…")
    source = _source_markdown(db, workspace_id, project_id)
    assert_not_cancelled(db, task)
    set_task(db, task, progress=45, message="生成授权承诺…")
    result = llm_service.chat_completion(
        db,
        workspace_id,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是招投标商务标助手。起草授权委托、投标承诺等固定格式正文。\n"
                    "只输出 JSON 数组，每项：id, title, body, needsStamp(bool)。\n"
                    "语气正式；占位用【】标明待填项；勿编造虚假证书编号。"
                ),
            },
            {
                "role": "user",
                "content": f"# 招标解析\n{source}\n\n请输出授权承诺 JSON 数组。",
            },
        ],
        temperature=0.3,
    )
    blocks = _normalize_commit(_parse_json_value(result.content))
    if not blocks:
        raise ValueError("模型未返回有效承诺正文")
    assert_not_cancelled(db, task)
    _upsert_editor_state_for_task(
        db,
        workspace_id,
        project_id,
        business_commit=blocks,
        expected_state_version=expected_state_version,
        actor_user_id=getattr(task, "actor_user_id", None),
    )
    update_project(
        db, workspace_id, project_id, status="writing", technical_plan_step=5
    )
    set_task(
        db,
        task,
        status="success",
        progress=100,
        message=f"已生成 {len(blocks)} 块承诺",
        result={"count": len(blocks)},
    )


def build_business_markdown(state: dict, project_name: str) -> str:
    """
    用途：将商务 editor-state 组装为 Markdown，供 Word 导出。
    对接：export_service / task export mode=business
    """
    lines: list[str] = [f"# {project_name or '商务标'}", ""]
    md = (state.get("parsedMarkdown") or "").strip()
    if md:
        lines += ["## 一、商务与资格条款摘录", "", md[:12000], ""]

    qualify = state.get("businessQualify") or []
    if qualify:
        lines += ["## 二、资格响应", ""]
        lines.append("| 要求 | 响应说明 | 证明材料 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        for q in qualify:
            if not isinstance(q, dict):
                continue
            lines.append(
                f"| {_cell(q.get('requirement'))} | {_cell(q.get('response'))} | "
                f"{_cell(q.get('evidence'))} | {_cell(q.get('status'))} |"
            )
        lines.append("")

    toc = state.get("businessToc") or []
    if toc:
        lines += ["## 三、递交材料清单", ""]
        for t in toc:
            if not isinstance(t, dict):
                continue
            mark = "☑" if t.get("checked") else "☐"
            req = t.get("status") or ""
            lines.append(
                f"- {mark} **{t.get('title') or ''}**（{t.get('category') or ''}·{req}）"
            )
            if t.get("note"):
                lines.append(f"  - 备注：{t.get('note')}")
        lines.append("")

    quote = state.get("businessQuote") or {}
    if isinstance(quote, dict):
        rows = quote.get("rows") or []
        if rows:
            lines += ["## 四、报价说明", ""]
            lines.append("| 名称 | 单位 | 数量 | 单价 | 金额 | 备注 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                lines.append(
                    f"| {_cell(r.get('name'))} | {_cell(r.get('unit'))} | "
                    f"{_cell(r.get('quantity'))} | {_cell(r.get('unitPrice'))} | "
                    f"{_cell(r.get('amount'))} | {_cell(r.get('remark'))} |"
                )
            lines.append("")
        notes = (quote.get("notes") or "").strip()
        if notes:
            lines += ["### 报价备注", "", notes, ""]

    commits = state.get("businessCommit") or []
    if commits:
        lines += ["## 五、授权与承诺", ""]
        for c in commits:
            if not isinstance(c, dict):
                continue
            title = c.get("title") or "承诺"
            lines.append(f"### {title}")
            if c.get("needsStamp"):
                lines.append("*（需盖章/签字）*")
            lines.append("")
            lines.append(str(c.get("body") or ""))
            lines.append("")

    if len(lines) <= 2:
        lines += ["（暂无商务标内容，请先完成解析与各步生成。）", ""]
    return "\n".join(lines)


def _cell(v: Any) -> str:
    s = str(v or "").replace("|", "\\|").replace("\n", " ")
    return s


def new_temp_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(3)}"
