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
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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


class BidWatchPlanRow(Base):
    """
    模块：国能 e 招计划追踪计划实体
    用途：保存工作空间内的本机招标计划字段，供受控同步任务按完整计划名检索。
    对接：opportunity_watch_service；后续 /api/opportunity-watch 计划导入接口。
    二次开发：fingerprint 仅用于工作空间内幂等，禁止写入外部 URL、Cookie、原始 Excel 或用户路径。
    """

    __tablename__ = "bid_watch_plans"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="uq_bid_watch_plans_workspace_fingerprint",
        ),
        # 供命中表复合外键引用，强制 plan 与 hit 同属一个 workspace。
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_bid_watch_plans_id_workspace",
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
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    expected_publish_text: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class BidSourceSyncRunRow(Base):
    """
    模块：国能 e 招同步运行实体
    用途：保存单次受控同步的脱敏状态、时间和数量统计，支持启动期恢复中断任务。
    对接：opportunity_watch_service.mark_interrupted_watch_runs；后续同步 API 轮询。
    二次开发：error_code 只能是服务端固定码；禁止保存 URL、Cookie、请求/响应正文或远端异常原文。
    """

    __tablename__ = "bid_source_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "source_name = 'chnenergy'",
            name="ck_bid_source_sync_runs_source_name",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_bid_source_sync_runs_status",
        ),
        # error_code 仅允许 NULL 或服务端固定字典，禁止落库远端异常原文。
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'source_unavailable', 'rate_limited', 'malformed_response', 'interrupted'"
            ")",
            name="ck_bid_source_sync_runs_error_code",
        ),
        # 供命中表复合外键引用，强制 run 与 hit 同属一个 workspace。
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_bid_source_sync_runs_id_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chnenergy"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class BidSourceHitRow(Base):
    """
    模块：国能 e 招公告命中实体
    用途：保存计划与公告的结构化匹配结果及解析时间，供人工确认后创建本地标讯。
    对接：opportunity_watch_service；后续命中列表和人工接受接口。
    二次开发：公告链接必须由 source_info_id/category_num/source_publish_text 动态生成；不得落库正文、HTML、JSON、附件或 Cookie。
    复合外键保证 hit.workspace_id 与其 watch_plan / sync_run 同属一个工作空间，禁止跨空间关系。
    """

    __tablename__ = "bid_source_hits"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "watch_plan_id",
            "source_info_id",
            name="uq_bid_source_hits_workspace_plan_info",
        ),
        CheckConstraint(
            "source_name = 'chnenergy'",
            name="ck_bid_source_hits_source_name",
        ),
        CheckConstraint(
            "source_timezone = 'Asia/Shanghai'",
            name="ck_bid_source_hits_source_timezone",
        ),
        CheckConstraint(
            "extraction_status IN ('resolved', 'needs_review')",
            name="ck_bid_source_hits_extraction_status",
        ),
        # 复合外键：命中的 workspace 必须与计划、运行一致，阻止跨空间关系写入。
        ForeignKeyConstraint(
            ["workspace_id", "watch_plan_id"],
            ["bid_watch_plans.workspace_id", "bid_watch_plans.id"],
            ondelete="CASCADE",
            name="fk_bid_source_hits_plan_same_workspace",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "sync_run_id"],
            ["bid_source_sync_runs.workspace_id", "bid_source_sync_runs.id"],
            ondelete="CASCADE",
            name="fk_bid_source_hits_run_same_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 计划/运行归属由上方复合外键强制与 workspace_id 一致；保留单列索引便于查询。
    watch_plan_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    sync_run_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chnenergy"
    )
    source_info_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category_num: Mapped[str] = mapped_column(String(32), nullable=False)
    source_publish_text: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    deadline_at_local: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opening_at_local: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Shanghai"
    )
    extraction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="needs_review"
    )
    accepted_opportunity_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("bid_opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
    embedding_json：历史本地哈希或旧 API 向量 JSON；P9C 语义检索改读 semantic_chunk_embeddings
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


class SemanticEmbeddingIndexRow(Base):
    """
    模块：P9C 语义索引运行实体
    用途：按工作空间记录离线模型版本化索引状态；版本并存，成功后才切 active。
    对接：knowledge_service 重建/查询；/api/knowledge/semantic-index*。
    二次开发：error_code 仅允许服务端固定码；禁止存 URL、Token、正文、远端错误原文或用户路径。
    """

    __tablename__ = "semantic_embedding_indexes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'active', 'failed', 'superseded')",
            name="ck_semantic_embedding_indexes_status",
        ),
        CheckConstraint(
            "provider = 'offline_bge'",
            name="ck_semantic_embedding_indexes_provider",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'model_unavailable', 'model_storage_insufficient', "
            "'index_interrupted', 'index_failed', 'index_not_built', 'index_building'"
            ")",
            name="ck_semantic_embedding_indexes_error_code",
        ),
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_semantic_embedding_indexes_id_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="offline_bge"
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_fingerprint: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    # total_chunks：收集有效分块后写入；embedded_chunks：成功写入向量数
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedded_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # chunk_count 兼容字段：语义等价于 embedded_chunks（已成功嵌入分块数）
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SemanticChunkEmbeddingRow(Base):
    """
    模块：P9C 语义分块向量实体
    用途：保存某次索引运行下每个分块的离线 512 维向量；与历史 embedding_json 并存。
    对接：knowledge_service 重建执行与 hybrid 检索；SemanticEmbeddingIndexRow。
    二次开发：所有读写必须带 workspace_id 过滤；跨空间 index/chunk 一律视为不存在。
    """

    __tablename__ = "semantic_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "index_id",
            "chunk_id",
            name="uq_semantic_chunk_embeddings_index_chunk",
        ),
        ForeignKeyConstraint(
            ["index_id", "workspace_id"],
            [
                "semantic_embedding_indexes.id",
                "semantic_embedding_indexes.workspace_id",
            ],
            ondelete="CASCADE",
            name="fk_semantic_chunk_embeddings_index_workspace",
        ),
        ForeignKeyConstraint(
            ["chunk_id"],
            ["kb_chunks.id"],
            ondelete="CASCADE",
            name="fk_semantic_chunk_embeddings_chunk",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    index_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
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


class LocalUserRow(Base):
    """
    模块：P10A 本机用户实体
    用途：保存用户名与 scrypt 口令派生值；不含邮箱/手机/第三方身份。
    对接：auth_service；/api/auth/*；bootstrap_local_admin。
    二次开发：禁止回显 password_* 字段；username_normalized 为唯一键。
    """

    __tablename__ = "local_users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkspaceMemberRow(Base):
    """
    模块：P10A 工作空间成员实体
    用途：用户与工作空间的角色关系；is_owner 为所有者标记，不新增第五业务角色。
    对接：auth_service；deps.get_workspace_id；后续成员管理 API。
    二次开发：role 仅允许 bid_writer|finance|hr|bidder；最后所有者保护在服务层。
    """

    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_user",
        ),
        CheckConstraint(
            "role IN ('bid_writer', 'finance', 'hr', 'bidder')",
            name="ck_workspace_members_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("local_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="bid_writer")
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuthSessionRow(Base):
    """
    模块：P10A 会话实体
    用途：不透明 Cookie 会话；库内仅存 token/CSRF 的 SHA-256 摘要与过期/撤销时间。
    对接：auth_service；auth_middleware；/api/auth/login|logout|me。
    二次开发：禁止落库原始 Cookie/CSRF；禁止 JWT。
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("local_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    active_workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuthAuditEventRow(Base):
    """
    模块：P10A 最小审计事件
    用途：记录登录/退出/拒绝等固定动作与结果；不得写入口令、Cookie、摘要或 Token。
    对接：auth_service.record_audit；测试断言脱敏。
    """

    __tablename__ = "auth_audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class FinanceCostEntryRow(Base):
    """
    模块：P10C 财务成本草案条目
    用途：由 strict finance 人工维护的项目成本分项；金额以人民币分整数持久化。
    对接：finance_cost_service；/api/finance/business-bids/*/cost-*。
    二次开发：禁止写回 business_json；禁止客户端指定 id/workspace/project/user/时间戳。
    """

    __tablename__ = "finance_cost_entries"
    __table_args__ = (
        CheckConstraint(
            "category IN ('labor', 'material', 'service', 'other')",
            name="ck_finance_cost_entries_category",
        ),
        CheckConstraint(
            "amount_fen >= 1 AND amount_fen <= 999999999999",
            name="ck_finance_cost_entries_amount_fen",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_fen: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remark: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class FinanceProjectCostChangeEventRow(Base):
    """
    模块：P10K 财务项目成本变更最小不可变事件
    用途：在 P10C 成功写操作同一事务内记录 workspace/project/entry/action/actor/time。
    对接：finance_project_cost_change_event_service；finance_cost_service；
      GET /api/finance/business-bids/{projectId}/cost-change-events。
    二次开发：
      - 禁止金额/名称/备注/快照/失败尝试等业务正文列
      - entry_id 故意无外键，删除条目后事件仍保留
      - 禁止客户端指定 id/时间；无 update/delete API
    """

    __tablename__ = "finance_project_cost_change_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('create', 'update', 'delete')",
            name="ck_finance_project_cost_change_events_action",
        ),
        Index(
            "ix_fpce_workspace_project_created",
            "workspace_id",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 故意无外键：删除成本条目后仍保留 entry_id
    entry_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class HrCredentialCardRow(Base):
    """
    模块：P10D 人员资质素材卡
    用途：由 strict hr 在当前工作空间登记最小人员资质显示信息；不做物理删除。
    对接：hr_credential_service；/api/hr/credential-cards*。
    二次开发：禁止身份证号/手机/住址/照片/附件/URL/证件号码字段；客户端不得写 id/workspace/user/时间戳。
    """

    __tablename__ = "hr_credential_cards"
    __table_args__ = (
        CheckConstraint(
            "category IN ('professional', 'safety', 'performance', 'other')",
            name="ck_hr_credential_cards_category",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class HrTeamRecommendationRow(Base):
    """
    模块：P10F 人力项目团队推荐主表
    用途：每个工作空间技术标项目至多一份人工维护的团队推荐快照（不含成员明细）。
    对接：hr_team_recommendation_service；/api/hr/team-recommendations*。
    二次开发：禁止客户端写入 id/workspace/project/操作者/时间戳；空成员不清物理删除本行。
    """

    __tablename__ = "hr_team_recommendations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "project_id",
            name="uq_hr_team_recommendations_ws_project",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    updated_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class HrTeamRecommendationMemberRow(Base):
    """
    模块：P10F 团队推荐成员快照行
    用途：保存推荐时的 P10D 摘要字段与顺序；不含 remark。
    对接：hr_team_recommendation_service；HR 详情与 bid_writer 投影。
    二次开发：source_card_id 仅供 HR 预选；标书制作者投影禁止返回该字段。
    """

    __tablename__ = "hr_team_recommendation_members"
    __table_args__ = (
        CheckConstraint(
            "category IN ('professional', 'safety', 'performance', 'other')",
            name="ck_hr_team_recommendation_members_category",
        ),
        CheckConstraint(
            "display_order >= 1 AND display_order <= 30",
            name="ck_hr_team_recommendation_members_display_order",
        ),
        UniqueConstraint(
            "recommendation_id",
            "display_order",
            name="uq_hr_team_recommendation_members_rec_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("hr_team_recommendations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_card_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    person_name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class HrPerformanceCardRow(Base):
    """
    模块：P10H 人员业绩素材卡
    用途：由 strict hr 在当前工作空间手工登记最小人员项目业绩；不做物理删除。
    对接：hr_performance_service；/api/hr/performance-cards*。
    二次开发：禁止证件号/联系方式/附件/金额/简历全文/外链字段；客户端不得写 id/workspace/user/时间戳。
    """

    __tablename__ = "hr_performance_cards"
    __table_args__ = (
        CheckConstraint(
            "completed_year IS NULL OR (completed_year >= 1900 AND completed_year <= 2100)",
            name="ck_hr_performance_cards_completed_year",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_name: Mapped[str] = mapped_column(String(80), nullable=False)
    project_name: Mapped[str] = mapped_column(String(120), nullable=False)
    project_role: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    completed_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performance_summary: Mapped[str] = mapped_column(Text, nullable=False)
    remark: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LocalParserCallbackTicketRow(Base):
    """
    模块：P8C 本地解析一次性回传票据
    用途：仅存 SHA-256 摘要与绑定元数据，支撑 required 模式下短期单项目单次回调。
    对接：local_parser_ticket_service；POST 签发与 /api/local-parser/callback。
    二次开发：禁止 raw_ticket/markdown/filename/IP/User-Agent 等字段；摘要唯一；外键级联删除。
    """

    __tablename__ = "local_parser_callback_tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issued_by_user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("local_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # P12B-C2：签发时服务端权威全状态版本；旧行可 NULL（不得写 editor-state）
    expected_state_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
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


class ContentFuseApplicationBatchRow(Base):
    """
    模块：M3-D 融合写入持久恢复批次
    用途：记录 content_fuse 原子确认后的有限恢复快照（每项目最近 20 批）。
    对接：content_fuse_application_service；/api/projects/{id}/content-fuse-applications*。
    二次开发：
      - 字段集合固定；snapshot_json 仅服务端生成，禁止客户端投稿
      - state 仅 active|consumed；禁止扩为通用版本库或正文浏览 API
      - task_id 仅服务端追溯，列表接口不得返回
    """

    __tablename__ = "content_fuse_application_batches"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'consumed')",
            name="ck_content_fuse_application_batches_state",
        ),
        Index(
            "ix_cfab_workspace_project_created",
            "workspace_id",
            "project_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 同项目成功 content_fuse 任务 ID；仅追溯，不向列表返回
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EditorStateCheckpointRow(Base):
    """
    模块：P12A editor-state 手动检查点只读库
    用途：保存用户显式创建的完整编辑态规范快照（每项目最近 20 条）。
    对接：editor_state_checkpoint_service；
      /api/projects/{id}/editor-state-checkpoints*。
    二次开发：
      - 快照仅服务端从 get_editor_state 抽取 13 键生成，禁止客户端投稿
      - 列表不得投影 snapshot_json；不提供恢复/删除/下载/自动历史
      - 禁止修改旧表；仅 create_all 建新表
    """

    __tablename__ = "editor_state_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "snapshot_bytes >= 1 AND snapshot_bytes <= 2097152",
            name="ck_editor_state_checkpoints_snapshot_bytes",
        ),
        CheckConstraint(
            "outline_node_count >= 0",
            name="ck_editor_state_checkpoints_outline_node_count",
        ),
        CheckConstraint(
            "chapter_count >= 0",
            name="ck_editor_state_checkpoints_chapter_count",
        ),
        Index(
            "ix_escp_workspace_project_created_id",
            "workspace_id",
            "project_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 服务端规范快照 JSON（紧凑 sort_keys UTF-8）
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    # esv_ + SHA-256 前 32 hex
    state_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )


class EditorStateRevisionRow(Base):
    """
    模块：P12C-A editor-state 有限自动修订账本
    用途：与手动/安全检查点独立的最近自动修订快照（每项目最近 10 条）。
    对接：editor_state_revision_service.record_editor_state_transition。
    二次开发：
      - 禁止客户端投稿 snapshot/source；A 包无生产写入者、无公开 API
      - 不得复用 editor_state_checkpoints 的 20 条裁剪域
      - 列表/最新/裁剪不得投影 snapshot_json；不提供删除/浏览/恢复端点
    """

    __tablename__ = "editor_state_revisions"
    __table_args__ = (
        CheckConstraint(
            "snapshot_bytes >= 1 AND snapshot_bytes <= 2097152",
            name="ck_editor_state_revisions_snapshot_bytes",
        ),
        CheckConstraint(
            "source_kind IN ("
            "'browser_put','task','revise','callback',"
            "'local_parser','content_fuse_apply',"
            "'content_fuse_consume','checkpoint_restore',"
            "'revision_restore'"
            ")",
            name="ck_editor_state_revisions_source_kind",
        ),
        Index(
            "ix_esr_workspace_project_created_id",
            "workspace_id",
            "project_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 服务端规范快照 JSON（紧凑 sort_keys UTF-8，委托 editor_state_service）
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    # esv_ + SHA-256 前 32 hex
    state_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # 固定内部来源枚举；禁止任意字符串
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        index=True,
    )
