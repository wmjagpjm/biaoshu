"""
模块：领域实体（ORM）
用途：持久化 Workspace / Project，字段语义对齐前端 shared/types/workspace.ts。
对接：
  - 读写：app.services.project_service
  - 建表：Base.metadata.create_all
  - API 序列化：app.api.schemas.ProjectOut（snake_case 属性 → JSON camelCase）
二次开发：
  - 加字段：同步改 entities + schemas + 前端 Project 类型 + 迁移策略
  - 大纲/事实/章节等产物建议新表（artifact），勿把大 JSON 无限塞进 Project
"""

from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utc_now() -> datetime:
    """用途：ORM 默认 updated_at 时间源（UTC）。"""
    return datetime.now(timezone.utc)


class Workspace(Base):
    """
    用途：工作空间。个人版与账号 1:1，所有项目挂在其下。
    字段：
      - id：主键，默认 ws_local
      - name：展示名
      - owner_user_id：所属用户（鉴权完善后对接真实 user）
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
    )


class Project(Base):
    """
    用途：技术标 / 商务标共用项目列表实体，工作区入口。
    字段（Python snake_case / API camelCase）：
      - id → id
      - workspace_id → workspaceId
      - name → name
      - industry → industry
      - status → status（draft|analyzing|writing|reviewing|exported）
      - updated_at → updatedAt
      - technical_plan_step → technicalPlanStep（1–6，商务标复用为六步序号）
      - word_count → wordCount
      - kind → kind（technical|business，默认 technical）
      - linked_project_id → linkedProjectId（可选关联另一册项目）
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False, default="通用")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    technical_plan_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # technical | business
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="technical")
    linked_project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 标讯删除时清空关联；项目本身及其文件、任务不受影响。
    source_opportunity_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("bid_opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="projects")


class BidOpportunityRow(Base):
    """
    用途：工作空间内本地标讯线索；截止状态由服务端按 deadline 实时计算，不持久化。
    对接：/api/opportunities；标讯页；从标讯创建技术标项目。
    二次开发：外部抓取、RSS 或导入必须保留 workspace 归属；source_key 仅是本地不透明去重键，不得写入外部 URL、密钥或抓取状态。
    """

    __tablename__ = "bid_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_key",
            name="uq_bid_opportunities_workspace_source_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    buyer: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="其他")
    budget_label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    deadline: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_label: Mapped[str] = mapped_column(
        String(200), nullable=False, default="本地录入"
    )
    # 离线导入的可选不透明来源键；SQLite 对 NULL 唯一约束允许多行，手工录入不受影响
    source_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class BidTemplateRow(Base):
    """
    模块：技术标中标内容模板实体
    用途：保存 workspace 内独立的大纲/章节快照，供检索与从模板新建项目；非导出版式模板。
    对接：template_service；/api/templates；frontend/src/features/bid-templates。
    二次开发：
      - source_project_id 仅弱追溯，源项目删除须 SET NULL，不得级联删模板；
      - 禁止与 export_format 混用；多模板融合/商务 kind 另立项。
    """

    __tablename__ = "bid_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active | archived
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # 阶段 1 仅 technical；预留 business 字段值
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="technical")
    # 源项目删除后置空，快照与名称仍保留
    source_project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_project_name: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    # 深拷贝 JSON：至少 outline + chapters；可含 mode/facts/guidance 写作上下文
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResourceRow(Base):
    """
    模块：资源中心持久化实体
    用途：存储系统精选 Markdown 资源与 workspace 自建资源；浏览量由服务端维护。
    对接：resource_service；/api/resources；frontend/src/features/resources。
    二次开发：外部同步应另建来源与审计模型，勿把 URL、密钥或抓取状态写入本表。
    """

    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(
            "(source = 'system' AND workspace_id IS NULL) "
            "OR (source = 'user' AND workspace_id IS NOT NULL)",
            name="ck_resources_source_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # system 资源全局可见且不归属 workspace；user 资源必须归属一个 workspace
    workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # system | user；写权限由 resource_service 校验，不能信任客户端字段
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="资源")
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tone: Mapped[str] = mapped_column(String(16), nullable=False, default="blue")
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResourceSyncSourceRow(Base):
    """
    模块：受控资源同步来源实体
    用途：记录服务端配置的签名清单来源、公共密钥指纹和最近同步摘要，不向 ResourceRow 混入连接信息。
    对接：resource_sync_service；backend/scripts/sync_resources.py；GET /api/resources/sync-sources。
    二次开发：来源 URL 只能由服务端配置驱动；不得从浏览器请求、资源正文或工作空间记录写入。
    """

    __tablename__ = "resource_sync_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    last_manifest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_status: Mapped[str] = mapped_column(String(32), nullable=False, default="never")
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResourceSyncRunRow(Base):
    """
    模块：受控资源同步运行审计实体
    用途：保存每次同步的有限状态、数量与脱敏错误摘要，便于本机管理员追踪但不泄露远端正文或密钥。
    对接：resource_sync_service；GET /api/resources/sync-sources；管理员同步命令。
    二次开发：错误消息只能使用服务端错误码对应的安全文案；不得写入响应体、完整 URL、请求头或 Token。
    """

    __tablename__ = "resource_sync_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("resource_sync_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ResourceSyncItemRow(Base):
    """
    模块：受控资源同步条目映射实体
    用途：将发布方稳定条目键映射到本地只读资源，并保存正文摘要与最近出现时间以支持幂等更新。
    对接：resource_sync_service；ResourceSyncSourceRow；ResourceRow。
    二次开发：外部键只能是协议内不透明标识；不得存 URL、附件路径、HTML 或任何发布方密钥。
    """

    __tablename__ = "resource_sync_items"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_key",
            name="uq_resource_sync_items_source_external_key",
        ),
        UniqueConstraint("resource_id", name="uq_resource_sync_items_resource_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("resource_sync_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_key: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("resources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkspaceSettingsRow(Base):
    """
    用途：工作空间级 LLM/解析配置（一 workspace 一行）。
    字段对齐前端 features/settings/types.ts → WorkspaceSettings。
    安全说明：本机保密环境按产品决策「明文存储与回显」；勿把 DB 提交到公开仓库。
    对接：settings_service、GET|PUT /api/settings、llm_service 读 Key 发请求。
    """

    __tablename__ = "workspace_settings"

    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, default="openai-compatible"
    )
    # OpenAI 兼容接口根，如 https://api.deepseek.com/v1
    api_base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 用户自备 Key，明文存（产品决策：保密机，正常显示输入输出）
    api_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="deepseek-chat")
    # light | local | ask
    parse_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="light")
    # 可选 embedding 模型名；空=仅用本地哈希向量
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 默认导出模板 JSON（对齐前端 ExportFormatConfig，明文）
    export_format_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ProjectEditorStateRow(Base):
    """
    用途：技术标 + 商务标工作区最小持久化。
    对接：GET|PUT /api/projects/{id}/editor-state；
      技术标 useTechnicalPlanEditors / guidance；
      商务标 useBusinessBidWorkspace（business_json → businessQualify 等）。
    二次开发：正式产物版本库可拆 artifact 表，本表可作缓存或草稿。
    """

    __tablename__ = "project_editor_states"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    outline_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ALIGNED")
    analysis_overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 结构化招标分析：overview + techRequirements + rejectionRisks + scoringPoints
    analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 响应矩阵：评分点/技术要求到大纲与章节的手工映射
    response_matrix_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    guidance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 轻量解析后的招标文件 Markdown（document 步）
    parsed_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 商务标整包：qualify / toc / quote / commit
    business_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ProjectFileRow(Base):
    """
    用途：项目上传文件元数据；实体文件在 uploads/{project_id}/ 目录。
    对接：POST /api/projects/{id}/files、POST /api/projects/{id}/images。
    二次开发：role=source|image；不得以文件名、路径或客户端 MIME 代替该角色和项目归属校验。
    """

    __tablename__ = "project_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # source=招标/解析源文件；image=正文受控图片，旧数据默认 source
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="source", server_default="source", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class KbFolderRow(Base):
    """
    用途：知识库文件夹（workspace 级）。
    对接：/api/knowledge/folders；前端 KbFolder
    """

    __tablename__ = "kb_folders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class KbDocumentRow(Base):
    """
    用途：知识库文档元数据；正文在磁盘，分块在 kb_chunks。
    status: pending|parsing|indexing|ready|failed
    对接：/api/knowledge/docs；前端 KnowledgeDoc
    """

    __tablename__ = "kb_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("kb_folders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class KbChunkRow(Base):
    """
    用途：知识库文本分块，供关键词/向量混合检索与生成注入。
    对接：knowledge_service.search_chunks；task_service RAG 注入
    embedding_json：本地哈希或 API embedding 的 float 数组 JSON
    """

    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class KnowledgeCardRow(Base):
    """
    模块：卡片化知识与素材库实体
    用途：在 workspace 内独立沉淀文档片段、图片、资质与业绩快照，供编辑安全引用与复用。
    对接：card_service；/api/cards；/api/projects/{id}/insert-card；frontend knowledge-base。
    二次开发：
      - 禁止污染 kb_documents/kb_chunks、resources、project_files、bid_templates 语义；
      - source_id 仅为弱追溯，源删除不得级联删卡片；
      - 图片字节存卡片目录；插入项目时必须复制为项目 role=image。
    """

    __tablename__ = "knowledge_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # document | image | qualification | performance
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active | archived
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # manual | chunk | project_image | upload 等来源类型快照
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    # 弱引用：源删除后可置空，卡片正文/图片快照仍保留
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_label: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 文本类卡片正文快照；图片卡可为空
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 类型扩展 JSON（资质字段、业绩字段等）
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 图片卡服务端存储元数据（相对卡片目录）
    stored_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ProjectTaskRow(Base):
    """
    用途：本机日用任务（解析/分析/大纲/正文/导出）状态。
    对接：POST/GET /api/projects/{id}/tasks
    status: pending|running|success|failed|cancelled
    """

    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    # 创建时入参（后台线程读取）
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
