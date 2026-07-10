"""
模块：废标项检查服务
用途：结合结构化分析 rejectionRisks + 解析文关键词规则 + 正文响应情况。
对接：POST /api/projects/{id}/rejection-check；editor_state
二次开发：规则表可外置 JSON；可接 LLM 复核。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services import editor_state_service
from app.services.project_service import get_project
from app.services.text_similarity import keyword_hits

# 招标废标/否决常见触发词（解析文扫描）
_RULE_KEYWORDS: list[tuple[str, str, str]] = [
    # keyword, title, level
    ("废标", "出现「废标」相关条款", "high"),
    ("否决", "出现「否决投标」相关表述", "high"),
    ("无效投标", "无效投标条件", "high"),
    ("★", "星号/实质性条款标记", "high"),
    ("必须", "强制性「必须」要求", "medium"),
    ("不得低于", "量化门槛「不得低于」", "medium"),
    ("保证金", "投标/履约保证金要求", "medium"),
    ("有效期", "投标有效期要求", "medium"),
    ("资格", "资格条件", "medium"),
    ("营业执照", "主体资格证明", "low"),
    ("社保", "人员社保要求", "medium"),
    ("业绩", "类似业绩要求", "medium"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _body_corpus(state: dict) -> str:
    """用途：大纲标题 + 章节正文拼接，供「是否响应」检索。"""
    parts: list[str] = []
    outline = state.get("outline")
    if isinstance(outline, list):

        def walk(nodes: list) -> None:
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                if n.get("title"):
                    parts.append(str(n["title"]))
                ch = n.get("children")
                if isinstance(ch, list):
                    walk(ch)

        walk(outline)
    chapters = state.get("chapters")
    if isinstance(chapters, list):
        for ch in chapters:
            if isinstance(ch, dict):
                parts.append(str(ch.get("title") or ""))
                parts.append(str(ch.get("body") or ""))
    facts = state.get("facts")
    if isinstance(facts, list):
        for f in facts:
            if isinstance(f, dict):
                parts.append(str(f.get("content") or ""))
    return "\n".join(parts)


def _level_from_text(text: str, default: str = "medium") -> str:
    t = text or ""
    if any(k in t for k in ("★", "废标", "否决", "必须响应", "实质性")):
        return "high"
    if any(k in t for k in ("应当", "建议", "宜")):
        return "low"
    return default


def run_rejection_check(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    include_rules: bool = True,
) -> dict[str, Any]:
    """
    用途：产出废标风险条目列表（camelCase 字段对齐前端 RejectionItem）。
    """
    get_project(db, workspace_id, project_id)
    state = editor_state_service.get_editor_state(db, workspace_id, project_id)
    parsed = str(state.get("parsedMarkdown") or "").strip()
    analysis = state.get("analysis") if isinstance(state.get("analysis"), dict) else {}
    risks = analysis.get("rejectionRisks") or analysis.get("rejection_risks") or []
    corpus = _body_corpus(state)
    items: list[dict[str, Any]] = []

    if not parsed and not risks:
        items.append(
            {
                "id": f"rej_{secrets.token_hex(4)}",
                "level": "high",
                "title": "缺少招标解析文本",
                "tenderClause": "（无 parsedMarkdown）",
                "currentStatus": "尚未完成文档解析或分析",
                "suggestion": "请先在技术标「文档解析」步上传并解析招标文件，再运行废标检查。",
                "relatedLabel": "前往解析",
                "relatedTo": f"/technical-plan/{project_id}/document",
            }
        )
        return {
            "projectId": project_id,
            "items": items,
            "ranAt": _now_iso(),
            "stats": {"fromAnalysis": 0, "fromRules": 0, "total": 1},
        }

    from_analysis = 0
    if isinstance(risks, list):
        for raw in risks:
            text = str(raw).strip() if not isinstance(raw, dict) else str(
                raw.get("text") or raw.get("title") or raw.get("content") or ""
            ).strip()
            if not text:
                continue
            from_analysis += 1
            level = _level_from_text(text, "high")
            # 正文是否提及风险关键词的一部分
            tokens = [t for t in text if "\u4e00" <= t <= "\u9fff"]
            # 用文本前 12 字粗检
            key = text[:12]
            covered = key in corpus if key else False
            status = (
                "正文/大纲中已出现相关表述，请人工确认是否充分响应"
                if covered
                else "正文/大纲中未明显覆盖该风险点，建议补充响应"
            )
            items.append(
                {
                    "id": f"rej_{secrets.token_hex(4)}",
                    "level": level,
                    "title": text[:80] + ("…" if len(text) > 80 else ""),
                    "tenderClause": text[:500],
                    "currentStatus": status,
                    "suggestion": "对照招标废标/否决条款逐条写明响应措施与证明材料索引。",
                    "relatedLabel": "招标分析",
                    "relatedTo": f"/technical-plan/{project_id}/analysis",
                }
            )

    from_rules = 0
    if include_rules and parsed:
        for kw, title, level in _RULE_KEYWORDS:
            if kw not in parsed:
                continue
            from_rules += 1
            # 摘录上下文
            idx = parsed.find(kw)
            start = max(0, idx - 40)
            end = min(len(parsed), idx + 80)
            clause = parsed[start:end].replace("\n", " ").strip()
            covered = kw in corpus or any(
                k in corpus for k in (kw,)
            )
            # 对「★」「必须」等：正文未提则 medium/high
            if not covered and level == "high":
                status = f"解析文含「{kw}」，正文侧覆盖不足"
                sug = f"请在资格/商务或技术响应中明确处理「{kw}」相关要求。"
            elif not covered:
                status = f"解析文含「{kw}」，建议在正文中显式响应"
                sug = f"检索并响应与「{kw}」相关的条款，避免遗漏。"
            else:
                status = f"正文已出现「{kw}」相关表述，请核对是否完整"
                sug = "人工复核条款编号与证明材料是否齐全。"
            items.append(
                {
                    "id": f"rej_{secrets.token_hex(4)}",
                    "level": level,
                    "title": title,
                    "tenderClause": clause or kw,
                    "currentStatus": status,
                    "suggestion": sug,
                    "relatedLabel": "查看解析/正文",
                    "relatedTo": f"/technical-plan/{project_id}/document",
                }
            )

    # 按风险排序 high > medium > low
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: order.get(str(x.get("level")), 9))

    # 去重标题
    seen_t: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for it in items:
        t = str(it.get("title") or "")
        if t in seen_t:
            continue
        seen_t.add(t)
        uniq.append(it)

    return {
        "projectId": project_id,
        "items": uniq,
        "ranAt": _now_iso(),
        "stats": {
            "fromAnalysis": from_analysis,
            "fromRules": from_rules,
            "total": len(uniq),
        },
    }
