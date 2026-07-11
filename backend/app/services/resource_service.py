"""
模块：资源中心服务
用途：维护系统精选与工作空间自建 Markdown 资源，统一可见性、写权限、筛选和服务端浏览量累加。
对接：app.api.resources；frontend/src/features/resources；app.main.lifespan。
二次开发：外部同步必须另建受控服务、来源审计和白名单；不得恢复浏览器任意 URL 拉取或将密钥写入资源记录。
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.entities import ResourceRow

_RESOURCE_TONES = frozenset({"blue", "violet", "cyan", "slate"})

_SYSTEM_RESOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "res_system_scoring_response",
        "title": "技术标评分点响应写法",
        "description": "如何把招标评分表映射到大纲与正文，避免“写了但不得分”。",
        "tags": ["技术标", "评分", "写法"],
        "category": "写作指南",
        "tone": "violet",
        "view_count": 1286,
        "body_markdown": """# 技术标评分点响应写法

## 核心原则
1. **评分表是目录骨架**：一级、二级尽量对齐评分项名称。
2. **一条评分点一段可核验证据**：指标、步骤、交付物，少空话。
3. ★ 号条款单独成节或附件索引。

## 推荐结构
- 理解与目标
- 总体架构（对应“技术路线”分）
- 功能模块（逐条映射功能分）
- 实施与运维（对应实施保障分）

## 自检
- [ ] 每项评分在正文有对应小节标题
- [ ] 关键数字与全局事实一致
- [ ] 无与招标冲突的承诺
""",
    },
    {
        "id": "res_system_rejection_checklist",
        "title": "废标项自查清单（形式评审）",
        "description": "递交前快速过一遍，降低目录、签章、有效期类废标风险。",
        "tags": ["废标", "形式评审", "清单"],
        "category": "合规",
        "tone": "blue",
        "view_count": 980,
        "body_markdown": """# 废标项自查清单

## 目录与装订
- 一级目录是否与招标文件规定一致
- 页码、目录是否与正文同步

## 签章与授权
- 投标函、授权委托书是否签字盖章
- 授权期限是否覆盖开标日

## 商务硬性
- 投标有效期是否覆盖要求
- 保证金、保函形式是否允许

## 技术硬性
- ★ 号条款是否逐条响应
- 资格证明是否齐全、在有效期内
""",
    },
    {
        "id": "res_system_export_template",
        "title": "导出 Word 模板配置说明",
        "description": "六级标题、中文字号、页眉页脚与表格样式的配置要点。",
        "tags": ["导出", "模板", "Word"],
        "category": "工具",
        "tone": "cyan",
        "view_count": 756,
        "body_markdown": """# 导出 Word 模板配置

## 去哪配置
工作台的模板设置页面。

## 建议
1. 从政务投标通用模板复制一份个人模板。
2. 一级标题使用黑体小二居中，正文使用宋体三号或小四。
3. 页眉写项目简称，页脚页码居中。
4. 表格全宽，表头使用浅灰底色。

## 与项目联动
技术方案导出与商务标导出共用同一套版式配置。
""",
    },
    {
        "id": "res_system_knowledge_base",
        "title": "知识库投喂规范",
        "description": "历史方案怎样入库和打标签，才能服务“以标写标”。",
        "tags": ["知识库", "RAG", "素材"],
        "category": "知识库",
        "tone": "slate",
        "view_count": 642,
        "body_markdown": """# 知识库投喂规范

## 建议入库
- 已中标或高质量技术方案（脱敏后）
- 企业标准架构图、部署图
- 运维 SLA、培训大纲、等保模板

## 不建议
- 未脱敏的客户机密合同全文
- 扫描模糊、无法 OCR 的大杂烩 PDF

## 标签约定
行业 + 模块 + 年份，例如：智慧城市 / 架构 / 2025。
""",
    },
    {
        "id": "res_system_bid_division",
        "title": "商务标与技术标分工",
        "description": "两册边界与完整投标文件入口的选择，避免重复劳动。",
        "tags": ["商务标", "技术标", "产品"],
        "category": "产品说明",
        "tone": "violet",
        "view_count": 511,
        "body_markdown": """# 商务标与技术标分工

| 入口 | 写什么 |
| --- | --- |
| 技术标生成 | 方案、架构、实施、运维 |
| 商务标生成 | 资格、报价、授权承诺 |
| 完整投标文件 | 两册统一项目上下文后再分册 |
| 商务资料清单 | 只勾选要交什么，不写正文 |

本 Web 自托管版的算力由用户自备 API Key。
""",
    },
    {
        "id": "res_system_feedback_revise",
        "title": "人工反馈到 AI 调整用法",
        "description": "保留结构、只改明确问题的迭代方式。",
        "tags": ["反馈", "AI", "交互"],
        "category": "产品说明",
        "tone": "blue",
        "view_count": 890,
        "body_markdown": """# 人工反馈到 AI 调整

## 三种干预
1. **手动编辑**：直接改大纲或正文。
2. **按反馈调整**：写清意见，尽量保留结构。
3. **整段重生成**：不带本次意见的全量重试。

## 好反馈的例子
- “把运维提升为一级，实施下增加里程碑。”
- “第四章压缩套话，补双机房切换步骤。”
- “社保人数按 15 人改写资格响应。”
""",
    },
)


class ResourceNotFoundError(Exception):
    """
    用途：资源不存在或不属于当前工作空间时中断服务流程。
    对接：app.api.resources 统一映射为 HTTP 404。
    """


class ResourceReadOnlyError(Exception):
    """
    用途：阻止修改或删除系统只读资源。
    对接：app.api.resources 统一映射为 HTTP 403。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_resource_id() -> str:
    """用途：生成用户资源的服务端主键，避免客户端指定持久化标识。"""
    return f"res_{secrets.token_hex(8)}"


def _clean_text(value: Any, *, default: str = "", limit: int = 20000) -> str:
    """用途：清洗资源文本并限制长度，防止空标题或异常长内容进入数据库。"""
    return str(value or "").strip()[:limit] or default


def _clean_tags(value: Any) -> list[str]:
    """用途：归一化资源标签，去空、去重并限制数量，保持 API 返回稳定数组。"""
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        tag = _clean_text(item, limit=60)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 20:
            break
    return tags


def _loads_tags(raw: str | None) -> list[str]:
    """用途：容错解析标签 JSON；历史损坏值降级为空数组而不影响资源读取。"""
    if not raw:
        return []
    try:
        return _clean_tags(json.loads(raw))
    except json.JSONDecodeError:
        return []


def _clean_tone(value: Any) -> str:
    """用途：限制资源封面色调为前端已声明的安全枚举。"""
    tone = _clean_text(value, default="blue", limit=16)
    return tone if tone in _RESOURCE_TONES else "blue"


def resource_to_data(row: ResourceRow) -> dict[str, Any]:
    """
    用途：ORM 资源行转 API 读模型，统一解析标签和 camelCase 源字段的值。
    对接：app.api.resources 的 ResourceOut 序列化；前端 useResources。
    """
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "source": row.source,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "tags": _loads_tags(row.tags_json),
        "body_markdown": row.body_markdown,
        "tone": _clean_tone(row.tone),
        "view_count": row.view_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_visible_resource(db: Session, workspace_id: str, resource_id: str) -> ResourceRow:
    """
    用途：读取系统资源或当前工作空间用户资源，隐藏其它工作空间记录。
    对接：资源详情、更新、删除和浏览接口；失败抛出 ResourceNotFoundError。
    """
    row = db.get(ResourceRow, resource_id)
    if row is None or (row.source != "system" and row.workspace_id != workspace_id):
        raise ResourceNotFoundError(resource_id)
    return row


def list_resources(
    db: Session,
    workspace_id: str,
    *,
    q: str | None = None,
    tag: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """
    用途：列出系统精选和当前工作空间资源，并在服务端筛选关键词、标签和分类。
    对接：GET /api/resources；frontend 资源搜索与后续分类筛选。
    """
    stmt = (
        select(ResourceRow)
        .where(
            or_(
                ResourceRow.source == "system",
                ResourceRow.workspace_id == workspace_id,
            )
        )
        .order_by(ResourceRow.updated_at.desc(), ResourceRow.title.asc())
    )
    query = (q or "").strip().casefold()
    selected_tag = (tag or "").strip().casefold()
    selected_category = (category or "").strip().casefold()
    items: list[dict[str, Any]] = []
    for row in db.scalars(stmt).all():
        data = resource_to_data(row)
        if selected_category and data["category"].casefold() != selected_category:
            continue
        tags = data["tags"]
        if selected_tag and not any(item.casefold() == selected_tag for item in tags):
            continue
        if query:
            searchable = "\n".join(
                [data["title"], data["description"], data["category"], " ".join(tags)]
            ).casefold()
            if query not in searchable:
                continue
        items.append(data)
    return items


def create_resource(
    db: Session, workspace_id: str, payload: dict[str, Any]
) -> ResourceRow:
    """
    用途：在当前工作空间创建用户资源，来源和归属由服务端固定。
    对接：POST /api/resources；ResourceCreate；前端资源编辑弹层。
    """
    title = _clean_text(payload.get("title"), limit=500)
    body_markdown = _clean_text(payload.get("body_markdown"), limit=100000)
    if not title:
        raise ValueError("资源标题不能为空")
    if not body_markdown:
        raise ValueError("资源正文不能为空")
    row = ResourceRow(
        id=_new_resource_id(),
        workspace_id=workspace_id,
        source="user",
        title=title,
        description=_clean_text(payload.get("description"), limit=2000),
        category=_clean_text(payload.get("category"), default="资源", limit=100),
        tags_json=json.dumps(_clean_tags(payload.get("tags")), ensure_ascii=False),
        body_markdown=body_markdown,
        tone=_clean_tone(payload.get("tone")),
        view_count=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_resource(
    db: Session,
    workspace_id: str,
    resource_id: str,
    payload: dict[str, Any],
) -> ResourceRow:
    """
    用途：部分更新当前工作空间用户资源；系统资源始终拒绝写入。
    对接：PATCH /api/resources/{id}；ResourceUpdate；get_visible_resource。
    """
    row = get_visible_resource(db, workspace_id, resource_id)
    if row.source != "user":
        raise ResourceReadOnlyError(resource_id)
    if "title" in payload:
        title = _clean_text(payload["title"], limit=500)
        if not title:
            raise ValueError("资源标题不能为空")
        row.title = title
    if "description" in payload:
        row.description = _clean_text(payload["description"], limit=2000)
    if "category" in payload:
        row.category = _clean_text(payload["category"], default="资源", limit=100)
    if "tags" in payload:
        row.tags_json = json.dumps(_clean_tags(payload["tags"]), ensure_ascii=False)
    if "body_markdown" in payload:
        body_markdown = _clean_text(payload["body_markdown"], limit=100000)
        if not body_markdown:
            raise ValueError("资源正文不能为空")
        row.body_markdown = body_markdown
    if "tone" in payload:
        row.tone = _clean_tone(payload["tone"])
    row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def delete_resource(db: Session, workspace_id: str, resource_id: str) -> None:
    """
    用途：删除当前工作空间用户资源；系统资源只读且不可删除。
    对接：DELETE /api/resources/{id}；get_visible_resource。
    """
    row = get_visible_resource(db, workspace_id, resource_id)
    if row.source != "user":
        raise ResourceReadOnlyError(resource_id)
    db.delete(row)
    db.commit()


def record_resource_view(
    db: Session, workspace_id: str, resource_id: str
) -> ResourceRow:
    """
    用途：对可见资源在服务端原子累加浏览量并返回最新记录。
    对接：POST /api/resources/{id}/view；ResourceRow.view_count。
    二次开发：高并发或跨进程部署仍应保留数据库表达式累加，不得改成前端读改写。
    """
    row = get_visible_resource(db, workspace_id, resource_id)
    db.execute(
        update(ResourceRow)
        .where(ResourceRow.id == row.id)
        .values(view_count=ResourceRow.view_count + 1)
    )
    db.commit()
    db.refresh(row)
    return row


def ensure_system_resources(db: Session) -> None:
    """
    用途：幂等写入全局系统精选资源，不向任何用户 workspace 写入示例记录。
    对接：app.main.lifespan；资源中心首页。
    二次开发：新增系统内容使用稳定 id；已有系统条目不得静默覆盖用户本地数据。
    """
    now = _now()
    created = False
    for seed in _SYSTEM_RESOURCES:
        if db.get(ResourceRow, seed["id"]) is not None:
            continue
        db.add(
            ResourceRow(
                id=seed["id"],
                workspace_id=None,
                source="system",
                title=seed["title"],
                description=seed["description"],
                category=seed["category"],
                tags_json=json.dumps(seed["tags"], ensure_ascii=False),
                body_markdown=seed["body_markdown"],
                tone=seed["tone"],
                view_count=seed["view_count"],
                created_at=now,
                updated_at=now,
            )
        )
        created = True
    if created:
        db.commit()
