"""
模块：API 请求/响应 Schema（Pydantic）
用途：校验入参、序列化出参；JSON 使用 camelCase，与前端 TypeScript 类型对齐。
对接：
  - 前端类型：frontend/src/shared/types/workspace.ts → Project / ProjectStatus
  - 路由：app.api.projects、app.api.health
二次开发：
  - 新增响应字段：Field(serialization_alias="xxxYyy") 保持前端 camelCase
  - 请求体同时支持 snake_case 与 camelCase：populate_by_name=True + alias
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# 与前端 ProjectStatus 字面量一致
ProjectStatus = Literal[
    "draft",
    "analyzing",
    "writing",
    "reviewing",
    "exported",
]


ProjectKind = Literal["technical", "business"]


class ProjectOut(BaseModel):
    """
    用途：项目响应体，一一对应前端 Project。
    来源：ORM Project（from_attributes）。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    name: str
    industry: str
    status: str
    updated_at: datetime = Field(serialization_alias="updatedAt")
    technical_plan_step: int = Field(serialization_alias="technicalPlanStep")
    word_count: int = Field(serialization_alias="wordCount")
    kind: str = "technical"
    linked_project_id: str | None = Field(
        default=None, serialization_alias="linkedProjectId"
    )
    source_opportunity_id: str | None = Field(
        default=None, serialization_alias="sourceOpportunityId"
    )


class ProjectCreate(BaseModel):
    """
    用途：创建项目请求体。
    必填：name；其余可选，服务端有默认值。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    industry: str | None = None
    status: ProjectStatus | None = None
    technical_plan_step: int | None = Field(default=None, alias="technicalPlanStep")
    kind: ProjectKind | None = None
    linked_project_id: str | None = Field(default=None, alias="linkedProjectId")


class ProjectUpdate(BaseModel):
    """
    用途：PATCH 部分更新；全字段可选，仅传需要改的键。
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    industry: str | None = None
    status: ProjectStatus | None = None
    technical_plan_step: int | None = Field(default=None, alias="technicalPlanStep")
    word_count: int | None = Field(default=None, alias="wordCount")
    kind: ProjectKind | None = None
    linked_project_id: str | None = Field(default=None, alias="linkedProjectId")


# ---------- 本地标讯库 ----------


OpportunityStatus = Literal["open", "closing_soon", "closed"]


class OpportunityOut(BaseModel):
    """用途：本地标讯响应；status 始终由服务端 deadline 计算。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    workspace_id: str = Field(serialization_alias="workspaceId")
    title: str
    buyer: str
    region: str
    budget_label: str = Field(serialization_alias="budgetLabel")
    deadline: date
    status: OpportunityStatus
    tags: list[str] = Field(default_factory=list)
    summary: str
    source_label: str = Field(serialization_alias="sourceLabel")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class OpportunityCreate(BaseModel):
    """用途：手工录入一条本地标讯；截止日格式由 date 校验。"""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    buyer: str = ""
    region: str = "其他"
    budget_label: str = Field(default="", alias="budgetLabel")
    deadline: date
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    source_label: str = Field(default="本地录入", alias="sourceLabel")


class OpportunityUpdate(BaseModel):
    """用途：本地标讯部分更新；未传字段不覆盖。"""

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    buyer: str | None = None
    region: str | None = None
    budget_label: str | None = Field(default=None, alias="budgetLabel")
    deadline: date | None = None
    tags: list[str] | None = None
    summary: str | None = None
    source_label: str | None = Field(default=None, alias="sourceLabel")


class OpportunityProjectCreate(BaseModel):
    """用途：从标讯创建技术标时可选覆盖项目名和行业。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    industry: str | None = None


class OpportunityImportOut(BaseModel):
    """
    用途：离线标讯导入统计；非法整批不返回成功统计且不会写入数据库。
    对接：POST /api/opportunities/import；标讯页导入弹层。
    """

    inserted: int
    skipped: int
    total: int


# ---------- 资源中心 ----------


ResourceSource = Literal["system", "user"]
ResourceTone = Literal["blue", "violet", "cyan", "slate"]


class ResourceOut(BaseModel):
    """
    用途：资源中心统一读模型；系统资源的 workspaceId 为 null。
    对接：/api/resources；frontend/src/features/resources/types.ts。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    workspace_id: str | None = Field(serialization_alias="workspaceId")
    source: ResourceSource
    title: str
    description: str
    category: str
    tags: list[str] = Field(default_factory=list)
    body_markdown: str = Field(serialization_alias="bodyMarkdown")
    tone: ResourceTone
    view_count: int = Field(serialization_alias="viewCount")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class ResourceCreate(BaseModel):
    """
    用途：创建当前工作空间资源；来源固定由服务端写为 user。
    对接：POST /api/resources；frontend 资源编辑弹层。
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str = ""
    category: str = "资源"
    tags: list[str] = Field(default_factory=list)
    body_markdown: str = Field(alias="bodyMarkdown")
    tone: ResourceTone = "blue"


class ResourceUpdate(BaseModel):
    """
    用途：部分更新用户资源；系统资源由路由返回写保护错误。
    对接：PATCH /api/resources/{id}；resource_service.update_resource。
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    body_markdown: str | None = Field(default=None, alias="bodyMarkdown")
    tone: ResourceTone | None = None


class ResourceSyncRunOut(BaseModel):
    """
    用途：同步来源最近一次成功运行的脱敏统计，不包含远端地址、密钥或错误原文。
    对接：ResourceSyncSourceOut；GET /api/resources/sync-sources。
    """

    created: int
    updated: int
    skipped: int


class ResourceSyncSourceOut(BaseModel):
    """
    用途：资源同步来源的只读健康状态，供资源中心或未来管理员界面安全展示。
    对接：GET /api/resources/sync-sources；resource_sync_service.list_sync_source_statuses。
    二次开发：不得将 manifestUrl、publicKey、请求头或远端错误正文加入本响应。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    last_status: Literal["never", "running", "success", "failed"] = Field(
        serialization_alias="lastStatus"
    )
    last_success_at: datetime | None = Field(serialization_alias="lastSuccessAt")
    last_attempted_at: datetime | None = Field(
        serialization_alias="lastAttemptedAt"
    )
    last_run: ResourceSyncRunOut | None = Field(serialization_alias="lastRun")


class HealthOut(BaseModel):
    """
    用途：探活响应；前端状态条展示 online。
    字段：status=ok；service；defaultWorkspaceId；dbOk 表示能连上 SQLite。
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str
    service: str
    default_workspace_id: str | None = Field(
        default=None, serialization_alias="defaultWorkspaceId"
    )
    db_ok: bool | None = Field(default=None, serialization_alias="dbOk")


# ---------- 工作空间设置（对齐前端 WorkspaceSettings）----------


class WorkspaceSettingsOut(BaseModel):
    """
    用途：设置读写响应；apiKey 明文，便于设置页正常显示。
    对接：features/settings/types.ts
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    provider: str
    api_base_url: str = Field(serialization_alias="apiBaseUrl")
    api_key: str = Field(serialization_alias="apiKey")
    model: str
    parse_strategy: str = Field(serialization_alias="parseStrategy")
    embedding_model: str = Field(default="", serialization_alias="embeddingModel")
    export_format: dict | None = Field(
        default=None, serialization_alias="exportFormat"
    )
    updated_at: datetime | None = Field(default=None, serialization_alias="updatedAt")


class WorkspaceSettingsUpdate(BaseModel):
    """
    用途：PUT 设置请求体；字段均可选（部分更新）。
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: str | None = None
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")
    api_key: str | None = Field(default=None, alias="apiKey")
    model: str | None = None
    parse_strategy: str | None = Field(default=None, alias="parseStrategy")
    embedding_model: str | None = Field(default=None, alias="embeddingModel")
    export_format: dict | None = Field(default=None, alias="exportFormat")


# ---------- LLM / revise ----------


class LlmTestOut(BaseModel):
    """用途：连通性测试结果。"""

    ok: bool
    model: str
    reply: str


class ReviseIn(BaseModel):
    """
    用途：定向修订请求。
    对接：docs/ai-feedback-loop.md；前端 submitRevise
    """

    model_config = ConfigDict(populate_by_name=True)

    stage: str
    message: str
    preserve_structure: bool = Field(default=True, alias="preserveStructure")
    base_content: str | None = Field(default=None, alias="baseContent")
    guidance: dict | None = None
    target_id: str | None = Field(default=None, alias="targetId")
    target_label: str | None = Field(default=None, alias="targetLabel")


class ReviseOut(BaseModel):
    """
    用途：修订结果；兼容前端 AiFeedbackRecord 字段 + 可选修订正文。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    stage: str
    message: str
    target_id: str | None = Field(default=None, serialization_alias="targetId")
    target_label: str | None = Field(default=None, serialization_alias="targetLabel")
    created_at: str = Field(serialization_alias="createdAt")
    status: str
    result_summary: str = Field(serialization_alias="resultSummary")
    revised_content: str | None = Field(default=None, serialization_alias="revisedContent")
    model: str | None = None
    artifact_id: str | None = Field(default=None, serialization_alias="artifactId")
    preserve_structure: bool | None = Field(
        default=None, serialization_alias="preserveStructure"
    )
    project_id: str | None = Field(default=None, serialization_alias="projectId")


class EditorStateOut(BaseModel):
    """
    用途：编辑器整包状态（技术标 outline/chapters/responseMatrix + 商务标 business*）。
    对接：useTechnicalPlanEditors / useProjectGuidance / useBusinessBidWorkspace
    二次开发：responseMatrixVersion 仅表示收敛后的矩阵内容版本，与 updatedAt 无关。
    """

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(serialization_alias="projectId")
    outline: list | dict | None = None
    chapters: list | dict | None = None
    facts: list | dict | None = None
    mode: str = "ALIGNED"
    analysis_overview: str | None = Field(default=None, serialization_alias="analysisOverview")
    analysis: dict | None = None
    response_matrix: list | None = Field(
        default=None, serialization_alias="responseMatrix"
    )
    response_matrix_version: str = Field(
        default="", serialization_alias="responseMatrixVersion"
    )
    guidance: dict | None = None
    parsed_markdown: str | None = Field(default=None, serialization_alias="parsedMarkdown")
    business_qualify: list | None = Field(
        default=None, serialization_alias="businessQualify"
    )
    business_toc: list | None = Field(default=None, serialization_alias="businessToc")
    business_quote: dict | None = Field(
        default=None, serialization_alias="businessQuote"
    )
    business_commit: list | None = Field(
        default=None, serialization_alias="businessCommit"
    )
    updated_at: str | None = Field(default=None, serialization_alias="updatedAt")


class EditorStateUpdate(BaseModel):
    """
    用途：PUT 部分字段；未传的键不覆盖。
    二次开发：同时传 responseMatrix + responseMatrixVersion 时启用乐观锁；仅矩阵 null 不更新。
    """

    model_config = ConfigDict(populate_by_name=True)

    outline: list | dict | None = None
    chapters: list | dict | None = None
    facts: list | dict | None = None
    mode: str | None = None
    analysis_overview: str | None = Field(default=None, alias="analysisOverview")
    analysis: dict | None = None
    response_matrix: list | None = Field(default=None, alias="responseMatrix")
    response_matrix_version: str | None = Field(
        default=None, alias="responseMatrixVersion"
    )
    guidance: dict | None = None
    parsed_markdown: str | None = Field(default=None, alias="parsedMarkdown")
    business_qualify: list | None = Field(default=None, alias="businessQualify")
    business_toc: list | None = Field(default=None, alias="businessToc")
    business_quote: dict | None = Field(default=None, alias="businessQuote")
    business_commit: list | None = Field(default=None, alias="businessCommit")
