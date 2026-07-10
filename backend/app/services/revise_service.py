"""
模块：产物定向修订服务
用途：按用户反馈 +（可选）原文 + 项目 guidance，调用 LLM 生成修订结果/摘要。
对接：
  - POST /api/projects/{projectId}/artifacts/{artifactId}/revise
  - 前端 useProjectGuidance.submitRevise / useBusinessBidWorkspace
  - 商务结构化阶段写回 editor-state（businessQualify 等）
二次开发：
  - 产物版本库就绪后，base_content 可改为服务端按 artifactId 读取
  - 长文可改异步 task + SSE
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services import editor_state_service, llm_service
from app.services.llm_service import LlmCallError, LlmConfigError
from app.services.project_service import get_project

# 阶段中文名（提示词用）
STAGE_LABELS: dict[str, str] = {
    "document_parse": "文档解析",
    "bid_analysis": "招标分析",
    "outline": "目录大纲",
    "global_facts": "全局事实",
    "chapter_content": "正文内容",
    "export_format": "导出格式",
    "project_guidance": "项目生成要求",
    "business_parse": "商务标·条款解析",
    "business_qualify": "商务标·资格响应",
    "business_toc": "商务标·目录清单",
    "business_quote": "商务标·报价说明",
    "business_commit": "商务标·授权承诺",
}

# 结构化 JSON 写回 editor-state 的商务阶段
BUSINESS_STRUCT_STAGES = frozenset(
    {
        "business_qualify",
        "business_toc",
        "business_quote",
        "business_commit",
    }
)

_STRUCT_JSON_HINTS: dict[str, str] = {
    "business_qualify": (
        "JSON 数组，每项：id, requirement, response, evidence, "
        "status(pending|matched|partial|missing)"
    ),
    "business_toc": (
        "JSON 数组，每项：id, title, category, status(required|optional|done), "
        "checked(bool), note"
    ),
    "business_quote": (
        'JSON 对象：{"rows":[{"id","name","unit","quantity","unitPrice","amount","remark"}],'
        '"notes":"..."}'
    ),
    "business_commit": (
        "JSON 数组，每项：id, title, body, needsStamp(bool)"
    ),
}


def _build_messages(
    *,
    stage: str,
    message: str,
    preserve_structure: bool,
    base_content: str | None,
    guidance: dict | None,
    target_label: str | None,
) -> list[dict[str, str]]:
    """用途：组装 revise 用 system + user 消息。"""
    stage_label = STAGE_LABELS.get(stage, stage)
    structure_rule = (
        "尽量保留原有层级与标题结构，只做定向修改。"
        if preserve_structure
        else "允许较大幅度调整结构以更好满足用户意见。"
    )
    guidance_text = ""
    if guidance:
        parts = []
        if guidance.get("targetWordCount") or guidance.get("target_word_count"):
            tw = guidance.get("targetWordCount") or guidance.get("target_word_count")
            parts.append(f"- 目标字数：{tw}")
        if guidance.get("chapterFocus") or guidance.get("chapter_focus"):
            parts.append(
                f"- 章节侧重：{guidance.get('chapterFocus') or guidance.get('chapter_focus')}"
            )
        if guidance.get("formatRequirements") or guidance.get("format_requirements"):
            parts.append(
                f"- 格式要求：{guidance.get('formatRequirements') or guidance.get('format_requirements')}"
            )
        if guidance.get("extraRequirements") or guidance.get("extra_requirements"):
            parts.append(
                f"- 其它：{guidance.get('extraRequirements') or guidance.get('extra_requirements')}"
            )
        if parts:
            guidance_text = "项目级生成要求：\n" + "\n".join(parts)

    if stage in BUSINESS_STRUCT_STAGES:
        hint = _STRUCT_JSON_HINTS.get(stage, "合法 JSON")
        system = (
            "你是招投标商务标助手，负责「基于原文的定向修订」。\n"
            f"当前阶段：{stage_label}。\n"
            f"结构策略：{structure_rule}\n"
            "输出要求：\n"
            "1) 先用 1～3 句中文说明你改了什么（摘要）。\n"
            "2) 摘要后空一行，再给出完整修订结果，且正文必须是合法 JSON"
            f"（不要 Markdown 围栏外的杂文）。JSON 契约：{hint}\n"
            "3) 不要编造招标文件中不存在的硬性资质编号或业绩。\n"
            "4) 在用户意见基础上改原文 JSON，保留可复用字段。"
        )
    elif stage == "business_parse":
        system = (
            "你是招投标商务标助手，负责修订「商务与资格条款解析」Markdown。\n"
            f"结构策略：{structure_rule}\n"
            "输出要求：\n"
            "1) 先用 1～3 句中文摘要。\n"
            "2) 摘要后空一行给出完整修订后的 Markdown 正文。\n"
            "3) 不要编造招标文件中不存在的硬性指标。"
        )
    else:
        system = (
            "你是招投标标书写作助手，负责「基于原文的定向修订」。\n"
            f"当前阶段：{stage_label}。\n"
            f"结构策略：{structure_rule}\n"
            "输出要求：\n"
            "1) 先用 1～3 句中文说明你改了什么（摘要）。\n"
            "2) 若用户提供了原文，摘要后空一行再给出完整修订正文（Markdown）。\n"
            "3) 若没有原文，只给可执行的修订建议摘要即可。\n"
            "4) 不要编造招标文件中不存在的硬性指标。"
        )
    if guidance_text:
        system += "\n\n" + guidance_text

    user_parts = [f"用户修改意见：{message.strip()}"]
    if target_label:
        user_parts.append(f"作用目标：{target_label}")
    if base_content and base_content.strip():
        body = base_content.strip()
        if len(body) > 24000:
            body = body[:24000] + "\n\n…（原文过长，已截断）"
        user_parts.append("当前原文如下：\n```\n" + body + "\n```")
    else:
        user_parts.append("（本次未附带原文，请只输出修订方向摘要。）")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _split_summary_and_body(text: str, has_base: bool) -> tuple[str, str | None]:
    """
    用途：从模型输出拆摘要与正文（启发式：首段为摘要）。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return "模型未返回内容", None
    if not has_base:
        return cleaned[:500], None
    # 双换行分段
    parts = cleaned.split("\n\n", 1)
    if len(parts) == 1:
        return cleaned[:300], cleaned
    summary, body = parts[0].strip(), parts[1].strip()
    return (summary[:500] or cleaned[:300]), (body or cleaned)


def apply_business_struct_revise(
    stage: str, body: str
) -> tuple[dict[str, Any], str] | None:
    """
    用途：将修订正文解析为规范化商务结构，供写库与单测。
    返回：(upsert_kwargs, revised_json_str)；解析失败返回 None。
    """
    from app.services import business_task_service as biz

    if stage not in BUSINESS_STRUCT_STAGES or not (body or "").strip():
        return None
    try:
        raw = biz._parse_json_value(body)
    except (ValueError, json.JSONDecodeError):
        return None

    if stage == "business_qualify":
        data = biz._normalize_qualify(raw)
        if not data:
            return None
        return {"business_qualify": data}, json.dumps(data, ensure_ascii=False)
    if stage == "business_toc":
        data = biz._normalize_toc(raw)
        if not data:
            return None
        return {"business_toc": data}, json.dumps(data, ensure_ascii=False)
    if stage == "business_quote":
        data = biz._normalize_quote(raw)
        if not data.get("rows") and not data.get("notes"):
            return None
        return {"business_quote": data}, json.dumps(data, ensure_ascii=False)
    if stage == "business_commit":
        data = biz._normalize_commit(raw)
        if not data:
            return None
        return {"business_commit": data}, json.dumps(data, ensure_ascii=False)
    return None


def revise_artifact(
    db: Session,
    workspace_id: str,
    project_id: str,
    artifact_id: str,
    *,
    stage: str,
    message: str,
    preserve_structure: bool = True,
    base_content: str | None = None,
    guidance: dict | None = None,
    target_id: str | None = None,
    target_label: str | None = None,
) -> dict:
    """
    用途：执行一次定向修订（同步）。
    返回：前端 AiFeedbackRecord 兼容字段 + revisedContent / model。
    商务结构化阶段成功时写回 editor-state。
    异常：ProjectNotFoundError / LlmConfigError / LlmCallError / ValueError
    """
    get_project(db, workspace_id, project_id)
    if not (message or "").strip():
        raise ValueError("反馈意见不能为空")

    fb_id = f"fb_{secrets.token_hex(6)}"
    created_at = datetime.now(timezone.utc).isoformat()

    messages = _build_messages(
        stage=stage,
        message=message,
        preserve_structure=preserve_structure,
        base_content=base_content,
        guidance=guidance,
        target_label=target_label,
    )

    try:
        result = llm_service.chat_completion(
            db, workspace_id, messages=messages, temperature=0.35
        )
    except (LlmConfigError, LlmCallError):
        raise

    has_base = bool(base_content and base_content.strip())
    summary, revised = _split_summary_and_body(result.content, has_base)

    # —— 商务结构化写回 ——
    if stage in BUSINESS_STRUCT_STAGES and revised:
        applied = apply_business_struct_revise(stage, revised)
        if applied:
            kwargs, revised_json = applied
            editor_state_service.upsert_editor_state(
                db, workspace_id, project_id, **kwargs
            )
            revised = revised_json
        else:
            summary = (
                (summary or "")
                + "（摘要已生成，但未能解析为表格 JSON，未写回工作区，请重试或改意见。）"
            ).strip()

    # —— 商务解析 Markdown 写回 ——
    if stage == "business_parse" and revised and revised.strip():
        editor_state_service.upsert_editor_state(
            db,
            workspace_id,
            project_id,
            parsed_markdown=revised.strip(),
        )

    return {
        "id": fb_id,
        "stage": stage,
        "message": message.strip(),
        "target_id": target_id,
        "target_label": target_label,
        "created_at": created_at,
        "status": "applied",
        "result_summary": summary,
        "revised_content": revised,
        "model": result.model,
        "artifact_id": artifact_id,
        "preserve_structure": preserve_structure,
        "project_id": project_id,
    }
